"""Forecast-Engine: Rolling Forecast und Jahresendprognose.

* **Methoden:** saisonal-naiv (Baseline) und Holt-Winters als ETS-Modell mit
  additivem Fehler, gedämpftem additivem Trend und additiver Saison
  (``statsmodels`` ETS(A,Ad,A)).
* **Methodenwahl je Zeitreihe** per Backtesting mit Rolling Origin nach WAPE.
  Bei Gleichstand gewinnt die einfachere Methode (saisonal-naiv).
* **Jahresendprognose** = Ist YTD + Prognose der Restmonate.
* **Prognoseintervalle** per Simulation (fester Seed → reproduzierbar). Das
  Intervall der Jahresendprognose wird über simulierte Pfade gebildet und
  berücksichtigt damit die Korrelation der Fehler über die Restmonate.
* **Szenarien** Best/Base/Worst mit transparenten, deterministischen Annahmen.
* **Forecast-Güte** über die Zeit: Forecast-Versionen nach Quartalsende
  (FC03, FC06, …) werden mit dem später eingetretenen Ist verglichen.

Alle Werte werden in Cent geführt; Rundung erfolgt erst bei der Übergabe an
das Faktenblatt.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import numpy as np
import pandas as pd

from src.agent import ForecastInfo, ForecastMethode, Szenario
from src.config import Einstellungen, ForecastEinstellungen, Szenarioannahmen
from src.seed_data import Datenbestand

logger = logging.getLogger("varianceai.forecast_engine")

Kennzahlname = Literal["Umsatz", "Deckungsbeitrag", "Kosten"]
KENNZAHLEN: tuple[Kennzahlname, ...] = ("Umsatz", "Deckungsbeitrag", "Kosten")
SAISON = 12


# =============================================================================
# Zeitreihen aus dem Datenbestand
# =============================================================================


def monatsreihe(daten: Datenbestand, kennzahl: Kennzahlname, version: str = "IST") -> pd.Series:
    """Monatswerte einer Kennzahl in Cent, Index = ``pd.Period`` (Monat).

    Umsatz und Deckungsbeitrag positiv; Kosten = Summe der Kostenstellenkosten.
    """
    a = daten.acdoca[daten.acdoca["version"] == version]
    typ = a["racct"].map(daten.kostenarttyp())
    if kennzahl == "Umsatz":
        z, faktor = a[typ == "ERLOES"], -1
    elif kennzahl == "Deckungsbeitrag":
        z, faktor = a[typ.isin(["ERLOES", "HK"])], -1
    else:
        z, faktor = a[(typ == "KOSTEN") & a["rcntr"].notna()], 1
    summen = z.groupby(["gjahr", "poper"])["hsl_cent"].sum() * faktor
    index = pd.PeriodIndex([pd.Period(year=int(j), month=int(p), freq="M") for j, p in summen.index], freq="M")
    return pd.Series(summen.to_numpy(dtype="int64"), index=index, name=kennzahl).sort_index()


# =============================================================================
# Methoden
# =============================================================================


def wape(ist: np.ndarray, prognose: np.ndarray) -> float:
    """Weighted Absolute Percentage Error in %: Σ|Ist − Prognose| / Σ|Ist| · 100."""
    ist, prognose = np.asarray(ist, dtype=float), np.asarray(prognose, dtype=float)
    nenner = np.abs(ist).sum()
    if nenner == 0:
        raise ValueError("WAPE nicht definiert: Summe der Istwerte ist 0.")
    return float(np.abs(ist - prognose).sum() / nenner * 100)


def saisonal_naiv(y: np.ndarray, horizont: int, saison: int = SAISON) -> np.ndarray:
    """ŷ(T+h) = y(T+h−k·12) – Wert desselben Monats im letzten verfügbaren Jahr."""
    y = np.asarray(y, dtype=float)
    if len(y) < saison:
        raise ValueError("Saisonal-naiv benötigt mindestens einen vollständigen Saisonzyklus.")
    return np.array([y[len(y) - saison + (h % saison)] for h in range(horizont)])


def _ets(y: np.ndarray, saison: int = SAISON):  # noqa: ANN202 – statsmodels-Ergebnisobjekt
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel  # noqa: PLC0415

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        modell = ETSModel(y, error="add", trend="add", damped_trend=True, seasonal="add",
                          seasonal_periods=saison, initialization_method="heuristic")
        return modell.fit(disp=False)


def holt_winters(y: np.ndarray, horizont: int, saison: int = SAISON) -> np.ndarray:
    """Holt-Winters (ETS A,Ad,A); Reihe wird zur numerischen Stabilität skaliert."""
    y = np.asarray(y, dtype=float)
    if len(y) < 2 * saison:
        raise ValueError("Holt-Winters benötigt mindestens zwei vollständige Saisonzyklen.")
    skala = float(np.abs(y).mean()) or 1.0
    return np.asarray(_ets(y / skala, saison).forecast(horizont)) * skala


METHODEN: dict[ForecastMethode, Callable[[np.ndarray, int], np.ndarray]] = {
    ForecastMethode.SAISONAL_NAIV: saisonal_naiv,
    ForecastMethode.HOLT_WINTERS: holt_winters,
}


@dataclass(frozen=True)
class Backtest:
    methode: ForecastMethode
    wape_pct: float
    anzahl_prognosen: int
    ursprungspunkte: int


def backtest(y: np.ndarray, methode: ForecastMethode, min_train: int = 24, horizont: int = 3) -> Backtest:
    """Rolling-Origin-Backtest: für jeden Ursprung t ≥ min_train die nächsten ``horizont`` Monate."""
    y = np.asarray(y, dtype=float)
    ist, prognose = [], []
    ursprung = 0
    for t in range(min_train, len(y)):
        h = min(horizont, len(y) - t)
        ist.extend(y[t:t + h])
        prognose.extend(METHODEN[methode](y[:t], h))
        ursprung += 1
    if not ist:
        raise ValueError(f"Zu wenige Beobachtungen für das Backtesting (mindestens {min_train + 1}).")
    return Backtest(methode, wape(np.array(ist), np.array(prognose)), len(ist), ursprung)


def waehle_methode(y: np.ndarray, einstellungen: ForecastEinstellungen) -> tuple[ForecastMethode, dict[ForecastMethode, Backtest]]:
    """Methode mit geringstem WAPE; bei Gleichstand die einfachere (saisonal-naiv)."""
    ergebnisse: dict[ForecastMethode, Backtest] = {}
    for methode in METHODEN:
        try:
            ergebnisse[methode] = backtest(y, methode, einstellungen.min_trainingslaenge, einstellungen.backtest_horizont)
        except (ValueError, np.linalg.LinAlgError) as fehler:
            logger.warning("Backtest %s nicht möglich: %s", methode.value, fehler)
    if not ergebnisse:
        raise ValueError("Keine Forecast-Methode anwendbar.")
    beste = min(ergebnisse.values(), key=lambda b: (round(b.wape_pct, 6), list(METHODEN).index(b.methode)))
    return beste.methode, ergebnisse


def _ets_simulieren(ergebnis, horizont: int, anzahl: int, rng: np.random.Generator) -> np.ndarray:  # noqa: ANN001
    """Simulation des ETS-Modells, unabhängig von der statsmodels-Version.

    statsmodels 0.14 akzeptiert nur ``random_state`` (ohne ``np.random.Generator``),
    0.15 nur ``rng``. Die normalverteilten Fehler werden deshalb selbst gezogen –
    genau wie statsmodels es intern tut (Standardnormal × √scale) – und als
    ``random_errors`` übergeben. Ergebnis: identische Pfade in beiden Versionen.
    """
    fehler = rng.standard_normal((horizont, anzahl)) * np.sqrt(ergebnis.scale)
    return ergebnis.simulate(nsimulations=horizont, anchor="end", repetitions=anzahl, random_errors=fehler)


def simuliere_pfade(y: np.ndarray, methode: ForecastMethode, horizont: int, anzahl: int, seed: int) -> np.ndarray:
    """Simulierte Zukunftspfade (Form: anzahl × horizont) für Prognoseintervalle."""
    y = np.asarray(y, dtype=float)
    rng = np.random.default_rng(seed)
    if methode is ForecastMethode.HOLT_WINTERS:
        skala = float(np.abs(y).mean()) or 1.0
        pfade = _ets_simulieren(_ets(y / skala), horizont, anzahl, rng)
        return np.asarray(pfade, dtype=float).reshape(horizont, anzahl).T * skala
    # Saisonal-naiv: Bootstrap der saisonalen Differenzen (inkl. Drift der Historie)
    residuen = y[SAISON:] - y[:-SAISON]
    pfade = np.empty((anzahl, horizont))
    erweitert = np.tile(y, (anzahl, 1))
    for h in range(horizont):
        basis = erweitert[:, -SAISON]
        wert = basis + rng.choice(residuen, size=anzahl)
        pfade[:, h] = wert
        erweitert = np.column_stack([erweitert, wert])
    return pfade


# =============================================================================
# Ergebnisobjekte
# =============================================================================


@dataclass(frozen=True)
class Monatsprognose:
    gjahr: int
    periode: int
    wert_cent: int
    unten_cent: int
    oben_cent: int


@dataclass(frozen=True)
class ForecastGuete:
    """Treffsicherheit einer früheren Forecast-Version gegenüber dem eingetretenen Ist."""

    version: str
    ursprung_periode: int
    methode: ForecastMethode
    perioden: tuple[int, ...]
    wape_pct: float
    bias_pct: float  # (Σ Forecast − Σ Ist) / Σ Ist · 100; positiv = zu optimistisch (Erlöse)


@dataclass
class Forecastergebnis:
    kennzahl: Kennzahlname
    info: ForecastInfo
    methode: ForecastMethode
    backtests: dict[ForecastMethode, Backtest]
    monate: list[Monatsprognose]
    guete: list[ForecastGuete] = field(default_factory=list)
    versionen: dict[str, dict[int, int]] = field(default_factory=dict)  # Version → {Periode: Cent}


def _cent(wert: float) -> int:
    return int(Decimal(wert).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _eur(cent: int) -> Decimal:
    return Decimal(cent).scaleb(-2)


def _pct(wert: float) -> Decimal:
    return Decimal(str(wert)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _bis(serie: pd.Series, gjahr: int, periode: int) -> pd.Series:
    return serie[serie.index <= pd.Period(year=gjahr, month=periode, freq="M")]


def _pruefe_lueckenlos(serie: pd.Series) -> None:
    erwartet = pd.period_range(serie.index.min(), serie.index.max(), freq="M")
    fehlend = erwartet.difference(serie.index)
    if len(fehlend):
        raise ValueError(f"Zeitreihe {serie.name} hat Lücken: {', '.join(str(p) for p in fehlend)}")


def _version_prognose(
    serie: pd.Series, gjahr: int, ursprung: int, einstellungen: ForecastEinstellungen
) -> tuple[ForecastMethode, dict[int, int]]:
    """Forecast-Version mit Datenstand bis ``ursprung`` für die Restmonate des Jahres."""
    y = _bis(serie, gjahr, ursprung).to_numpy(dtype=float)
    methode, _ = waehle_methode(y, einstellungen)
    werte = METHODEN[methode](y, 12 - ursprung)
    return methode, {ursprung + 1 + i: _cent(w) for i, w in enumerate(werte)}


def prognostiziere(
    daten: Datenbestand,
    kennzahl: Kennzahlname,
    einstellungen: Einstellungen | None = None,
    reihe: pd.Series | None = None,
) -> Forecastergebnis:
    """Jahresendprognose einer Kennzahl inkl. Methodenwahl, Intervall, Szenarien und Güte."""
    e = einstellungen or Einstellungen()
    fe, gj, periode = e.forecast, e.geschaeftsjahr, e.periode
    serie = _bis(reihe if reihe is not None else monatsreihe(daten, kennzahl), gj, periode)
    _pruefe_lueckenlos(serie)
    y = serie.to_numpy(dtype=float)
    ist_ytd = int(serie[serie.index.year == gj].sum())
    rest = 12 - periode
    plan = monatsreihe(daten, kennzahl, "PLAN")
    plan_gesamtjahr = int(plan[plan.index.year == gj].sum())

    methode, backtests = waehle_methode(y, fe)
    if rest:
        punkt = METHODEN[methode](y, rest)
        pfade = simuliere_pfade(y, methode, rest, fe.simulationen, e.seed)
        alpha = (100 - float(fe.konfidenzniveau_pct)) / 200
        monat_unten = np.quantile(pfade, alpha, axis=0)
        monat_oben = np.quantile(pfade, 1 - alpha, axis=0)
        summen = pfade.sum(axis=1)
        rest_cent = sum(_cent(w) for w in punkt)
        unten = ist_ytd + min(_cent(float(np.quantile(summen, alpha))), rest_cent)
        oben = ist_ytd + max(_cent(float(np.quantile(summen, 1 - alpha))), rest_cent)
        monate = [
            Monatsprognose(gj, periode + 1 + i, _cent(punkt[i]),
                           min(_cent(monat_unten[i]), _cent(punkt[i])), max(_cent(monat_oben[i]), _cent(punkt[i])))
            for i in range(rest)
        ]
    else:
        rest_cent, unten, oben, monate = 0, ist_ytd, ist_ytd, []

    info = ForecastInfo(
        kennzahl=kennzahl,
        ist_ytd=_eur(ist_ytd),
        prognose_restmonate=_eur(rest_cent),
        plan_gesamtjahr=_eur(plan_gesamtjahr),
        methode=methode,
        wape_pct=_pct(backtests[methode].wape_pct),
        konfidenzniveau_pct=fe.konfidenzniveau_pct,
        intervall_unten=_eur(unten),
        intervall_oben=_eur(oben),
        szenarien=_szenarien(kennzahl, ist_ytd, rest_cent, e.szenarien),
    )
    ergebnis = Forecastergebnis(kennzahl, info, methode, backtests, monate)
    ergebnis.versionen, ergebnis.guete = _versionen_und_guete(reihe if reihe is not None else monatsreihe(daten, kennzahl), gj, periode, fe)
    logger.info("Forecast %s: %s, WAPE %.1f %%, Jahresendprognose %s €.", kennzahl, methode.value,
                backtests[methode].wape_pct, info.jahresendprognose)
    return ergebnis


def _szenarien(kennzahl: Kennzahlname, ist_ytd: int, rest_cent: int, annahmen: Szenarioannahmen) -> list[Szenario]:
    """Best/Base/Worst: Restmonate ± Prozentsatz ggü. Base (Kosten mit umgekehrter Richtung)."""
    kosten = kennzahl == "Kosten"
    best_pct = -annahmen.chance_pct if kosten else annahmen.chance_pct
    worst_pct = annahmen.risiko_pct if kosten else -annahmen.risiko_pct

    def wert(pct: Decimal) -> Decimal:
        rest = (Decimal(rest_cent) * (1 + pct / 100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return _eur(ist_ytd + int(rest))

    richtung = "niedrigere Kosten" if kosten else "höhere Erlöse"
    gegen = "höhere Kosten" if kosten else "niedrigere Erlöse"
    return [
        Szenario(name="Best", wert=wert(best_pct), anpassung_pct=best_pct,
                 annahme=f"Restmonate mit Chance ({richtung}) ggü. Base, Annahme gemäß Konfiguration."),
        Szenario(name="Base", wert=wert(Decimal(0)), anpassung_pct=Decimal(0),
                 annahme="Statistische Prognose der Restmonate ohne Anpassung."),
        Szenario(name="Worst", wert=wert(worst_pct), anpassung_pct=worst_pct,
                 annahme=f"Restmonate mit Risiko ({gegen}) ggü. Base, Annahme gemäß Konfiguration."),
    ]


def _versionen_und_guete(
    serie: pd.Series, gj: int, periode: int, fe: ForecastEinstellungen
) -> tuple[dict[str, dict[int, int]], list[ForecastGuete]]:
    versionen: dict[str, dict[int, int]] = {}
    guete: list[ForecastGuete] = []
    for ursprung in fe.versionen:
        if ursprung >= periode or ursprung >= 12:
            continue
        name = f"FC{ursprung:02d}"
        try:
            methode, werte = _version_prognose(serie, gj, ursprung, fe)
        except ValueError as fehler:
            logger.warning("Forecast-Version %s nicht möglich: %s", name, fehler)
            continue
        versionen[name] = werte
        eingetreten = tuple(p for p in range(ursprung + 1, periode + 1))
        ist = np.array([serie[pd.Period(year=gj, month=p, freq="M")] for p in eingetreten], dtype=float)
        fc = np.array([werte[p] for p in eingetreten], dtype=float)
        guete.append(ForecastGuete(
            version=name, ursprung_periode=ursprung, methode=methode, perioden=eingetreten,
            wape_pct=round(wape(ist, fc), 1), bias_pct=round(float((fc.sum() - ist.sum()) / abs(ist.sum()) * 100), 1),
        ))
    return versionen, guete


def prognostiziere_alle(daten: Datenbestand, einstellungen: Einstellungen | None = None) -> dict[str, Forecastergebnis]:
    """Forecast für Umsatz, Deckungsbeitrag und Kosten.

    Lückenhafte Zeitreihen (z. B. fehlende Periode einer Kostenstelle) werden
    nicht still aufgefüllt, sondern führen zu einer Warnung und werden
    übersprungen – die Datenprüfung weist die Lücke aus.
    """
    ergebnisse: dict[str, Forecastergebnis] = {}
    for kennzahl in KENNZAHLEN:
        try:
            ergebnisse[kennzahl] = prognostiziere(daten, kennzahl, einstellungen)
        except ValueError as fehler:
            logger.warning("Forecast %s übersprungen: %s", kennzahl, fehler)
    return ergebnisse


def forecast_basis(ergebnis: Forecastergebnis, perioden: tuple[int, ...], ist_monate: pd.Series,
                   gjahr: int, periode: int) -> tuple[str, int] | None:
    """Basiswert für den Ist-Forecast-Vergleich im Analysezeitraum.

    Verwendet die jüngste Forecast-Version vor der Berichtsperiode; Monate bis
    zum Ursprung der Version gehen mit Ist ein (rollierende Logik).
    """
    kandidaten = [(int(v[2:]), v) for v in ergebnis.versionen if int(v[2:]) < periode]
    if not kandidaten:
        return None
    ursprung, version = max(kandidaten)
    werte = ergebnis.versionen[version]
    basis = 0
    for p in perioden:
        basis += werte[p] if p > ursprung else int(ist_monate[pd.Period(year=gjahr, month=p, freq="M")])
    return version, basis
