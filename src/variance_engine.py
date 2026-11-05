"""Abweichungsanalyse-Engine: Preis-/Mengen-/Mix-/Sortimentseffekt und
flexible Plankostenrechnung auf Vollkostenbasis.

Rechengrundsätze:

* Alle Beträge liegen in **Cent** (ganze Zahlen) vor. Effekte werden mit
  :class:`fractions.Fraction` **exakt** berechnet – ihre Summe entspricht der
  Gesamtabweichung ohne jeden Rundungsfehler.
* Erst für die Ausgabe wird auf den Cent gerundet, und zwar mit dem
  **Largest-Remainder-Verfahren** (:func:`runde_additiv`): Die gerundeten Effekte
  ergeben weiterhin exakt die Gesamtabweichung.
* Der Mixeffekt wird nur **innerhalb einer Produktgruppe** (je Region) berechnet.
  Region und Gesamt sind die Summe ihrer Produktgruppen – dadurch ist die
  Zerlegung über alle Ebenen ab Produktgruppe additiv.
* Produkte ohne Basis (Neuprodukt) bzw. ohne Ist (Wegfall) bilden den
  **Sortimentseffekt**, damit der Planpreis nie undefiniert ist.

Drill-down: Gesamt → Region → Produktgruppe → Produkt → Kunde bzw.
Kosten gesamt → Kostenstelle → Kostenart.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Literal

import pandas as pd

from src.agent import (
    Abweichung,
    AbweichungKurz,
    Datenqualitaet,
    Effektart,
    Einheit,
    Faktenblatt,
    ForecastInfo,
    Kennzahl,
    Kennzahlart,
    ObjektNichtGefunden,
    Treiber,
    TreiberRanking,
    Vergleichsart,
    rangliste_treiber,
)
from src.config import UNTERNEHMEN, Wesentlichkeit
from src.seed_data import Datenbestand

logger = logging.getLogger("varianceai.variance_engine")

Zeitraum = Literal["Monat", "YTD"]

# =============================================================================
# 1. Mathematischer Kern
# =============================================================================


@dataclass(frozen=True)
class Position:
    """Eine Produktposition im Vergleich (Mengen in Stück, Werte in Cent)."""

    schluessel: str
    menge_ist: int
    wert_ist: int
    menge_basis: int
    wert_basis: int


@dataclass(frozen=True)
class Umsatzzerlegung:
    """Exakte (ungerundete) Effekte in Cent."""

    menge: Fraction = Fraction(0)
    mix: Fraction = Fraction(0)
    preis: Fraction = Fraction(0)
    sortiment: Fraction = Fraction(0)
    sonstige: Fraction = Fraction(0)

    @property
    def summe(self) -> Fraction:
        return self.menge + self.mix + self.preis + self.sortiment + self.sonstige

    def effekte(self) -> dict[Effektart, Fraction]:
        return {
            Effektart.MENGE: self.menge, Effektart.MIX: self.mix, Effektart.PREIS: self.preis,
            Effektart.SORTIMENT: self.sortiment, Effektart.SONSTIGE: self.sonstige,
        }


def zerlege_umsatz(positionen: Iterable[Position]) -> Umsatzzerlegung:
    """Preis-/Mengen-/Mix-/Sortimentszerlegung einer Produktgruppe.

    Für das Vergleichssortiment (Ist- und Basismenge > 0) gilt mit
    ``M`` = Gesamtmenge, ``s_i = m_i / M`` und ``p_i = Wert_i / m_i``:

    * Mengeneffekt = (M_ist − M_basis) · Σ s_i,basis · p_i,basis
    * Mixeffekt    = M_ist · Σ (s_i,ist − s_i,basis) · p_i,basis
    * Preiseffekt  = Σ m_i,ist · (p_i,ist − p_i,basis)

    Positionen nur mit Ist oder nur mit Basis bilden den Sortimentseffekt;
    reine Wertbuchungen ohne Mengen den sonstigen Effekt.
    """
    positionen = list(positionen)
    vergleich = [p for p in positionen if p.menge_ist > 0 and p.menge_basis > 0]
    sortiment = sum(
        (Fraction(p.wert_ist - p.wert_basis) for p in positionen
         if (p.menge_ist > 0) != (p.menge_basis > 0)),
        Fraction(0),
    )
    sonstige = sum(
        (Fraction(p.wert_ist - p.wert_basis) for p in positionen if p.menge_ist <= 0 and p.menge_basis <= 0),
        Fraction(0),
    )
    if not vergleich:
        return Umsatzzerlegung(sortiment=sortiment, sonstige=sonstige)
    menge_ist = sum(p.menge_ist for p in vergleich)
    menge_basis = sum(p.menge_basis for p in vergleich)
    wert_ist = sum(p.wert_ist for p in vergleich)
    wert_basis = sum(p.wert_basis for p in vergleich)
    durchschnittspreis_basis = Fraction(wert_basis, menge_basis)  # = Σ s_i,basis · p_i,basis
    ist_zu_basispreisen = sum((Fraction(p.menge_ist * p.wert_basis, p.menge_basis) for p in vergleich), Fraction(0))
    return Umsatzzerlegung(
        menge=(menge_ist - menge_basis) * durchschnittspreis_basis,
        mix=ist_zu_basispreisen - menge_ist * durchschnittspreis_basis,
        preis=wert_ist - ist_zu_basispreisen,
        sortiment=sortiment,
        sonstige=sonstige,
    )


@dataclass(frozen=True)
class Kostenartwerte:
    """Plan- und Istwerte einer Kostenart in einer Kostenstelle (Cent bzw. Mengeneinheiten)."""

    plan_fix: int
    plan_var: int
    plan_menge: int
    ist: int
    ist_mit_menge: int
    ist_menge: int
    mengenbasiert: bool
    einmalig: bool = False


@dataclass(frozen=True)
class Kostenzerlegung:
    """Flexible Plankostenrechnung auf Vollkostenbasis (exakt, in Cent)."""

    plankosten: Fraction
    verrechnete_plankosten: Fraction
    sollkosten: Fraction
    ist_zu_planpreisen: Fraction
    istkosten: Fraction
    preis: Fraction
    verbrauch: Fraction
    beschaeftigung: Fraction
    einmal: Fraction

    def effekte(self) -> dict[Effektart, Fraction]:
        return {
            Effektart.KOSTEN_PREIS: self.preis, Effektart.VERBRAUCH: self.verbrauch,
            Effektart.BESCHAEFTIGUNG: self.beschaeftigung, Effektart.EINMAL: self.einmal,
        }


def zerlege_kosten(w: Kostenartwerte, beschaeftigungsgrad: Fraction) -> Kostenzerlegung:
    """Preis-, Verbrauchs- und Beschäftigungsabweichung einer Kostenart.

    * verrechnete Plankosten = Plankosten × BG
    * Sollkosten = fixe Plankosten + variable Plankosten × BG
    * Preisabweichung = Istkosten zu Istpreisen − Istkosten zu Planpreisen
    * Verbrauchsabweichung = Istkosten zu Planpreisen − Sollkosten
    * Beschäftigungsabweichung = Sollkosten − verrechnete Plankosten

    Istkosten zu Planpreisen = Ist-Menge × Planpreis (Planpreis = variable
    Plankosten / Planmenge); Buchungen ohne Menge gehen zu Istpreisen ein.
    Ungeplante, einmalige Kostenarten (z. B. Sonderabschreibung) werden
    vollständig als Einmaleffekt ausgewiesen.
    """
    plan = Fraction(w.plan_fix + w.plan_var)
    verrechnet = plan * beschaeftigungsgrad
    soll = w.plan_fix + w.plan_var * beschaeftigungsgrad
    ist = Fraction(w.ist)
    if w.einmalig:
        return Kostenzerlegung(plan, verrechnet, soll, ist, ist, Fraction(0), Fraction(0), Fraction(0), ist - verrechnet)
    if w.mengenbasiert and w.plan_menge > 0:
        planpreis = Fraction(w.plan_var, w.plan_menge)
        ist_pp = w.ist_menge * planpreis + (w.ist - w.ist_mit_menge)
    else:
        ist_pp = ist
    return Kostenzerlegung(
        plankosten=plan, verrechnete_plankosten=verrechnet, sollkosten=soll, ist_zu_planpreisen=ist_pp,
        istkosten=ist, preis=ist - ist_pp, verbrauch=ist_pp - soll, beschaeftigung=soll - verrechnet,
        einmal=Fraction(0),
    )


def runde_cent(wert: Fraction) -> int:
    """Kaufmännische Rundung eines exakten Centbetrags (halbe Cent vom Nullpunkt weg)."""
    vorzeichen = -1 if wert < 0 else 1
    return vorzeichen * math.floor(abs(wert) + Fraction(1, 2))


def runde_additiv(werte: Sequence[Fraction], ziel: int) -> list[int]:
    """Rundet exakte Centwerte so, dass ihre Summe exakt ``ziel`` ergibt.

    Largest-Remainder-Verfahren (Hare-Niemeyer): Abrunden, dann die
    verbleibenden Cent an die Werte mit den größten Nachkommaresten verteilen.
    Deterministisch bei Gleichstand (Reihenfolge der Eingabe).
    """
    if not werte:
        if ziel != 0:
            raise ValueError("Keine Werte, aber Ziel ≠ 0.")
        return []
    untergrenzen = [math.floor(w) for w in werte]
    rest = ziel - sum(untergrenzen)
    ganz, uebrig = divmod(rest, len(werte))
    reihenfolge = sorted(range(len(werte)), key=lambda i: (-(werte[i] - untergrenzen[i]), i))
    ergebnis = [u + ganz for u in untergrenzen]
    for i in reihenfolge[:uebrig]:
        ergebnis[i] += 1
    return ergebnis


def _decimal(cent: int) -> Decimal:
    return Decimal(cent).scaleb(-2)


def _prozent_dezimal(wert: Fraction) -> Decimal:
    return (Decimal(wert.numerator) / Decimal(wert.denominator)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


# =============================================================================
# 2. Analysebaum
# =============================================================================

_KURZ = {"Umsatz": "UMS", "Deckungsbeitrag": "DB", "Kosten": "KST", "EBIT": "EBIT"}
_KENNZAHLART = {
    "Umsatz": Kennzahlart.ERLOES, "Deckungsbeitrag": Kennzahlart.ERGEBNIS,
    "Kosten": Kennzahlart.KOSTEN, "EBIT": Kennzahlart.ERGEBNIS,
}


@dataclass(frozen=True)
class Ergebniszeile:
    """Eine Zeile der vereinfachten Ergebnisrechnung (Cent)."""

    kennzahl: str
    ist: int
    plan: int
    vorjahr: int
    art: Kennzahlart

    @property
    def delta_plan(self) -> int:
        return self.ist - self.plan

    @property
    def delta_vorjahr(self) -> int:
        return self.ist - self.vorjahr
_ABSATZSCHLUESSEL = ["regio", "prodh", "matnr", "kunnr"]


@dataclass(frozen=True)
class ForecastVergleich:
    """Basis für einen Ist-Forecast-Vergleich (Wert der Forecast-Version im Analysezeitraum)."""

    kennzahl: Literal["Umsatz", "Deckungsbeitrag", "Kosten"]
    version: str
    basis_cent: int


@dataclass(frozen=True)
class Kostenstellenrechnung:
    """Exakte Zwischenergebnisse je Kostenstelle (für Tests, Bericht und Nachvollziehbarkeit)."""

    rcntr: str
    stunden_ist: int
    stunden_plan: int
    beschaeftigungsgrad: Fraction
    kostenarten: dict[str, Kostenzerlegung] = field(default_factory=dict)
    werte: dict[str, Kostenartwerte] = field(default_factory=dict)

    @property
    def fixe_plankosten(self) -> int:
        return sum(w.plan_fix for w in self.werte.values())

    @property
    def beschaeftigungsabweichung(self) -> Fraction:
        """Exakt: fixe Plankosten × (1 − Beschäftigungsgrad)."""
        return sum((z.beschaeftigung for z in self.kostenarten.values()), Fraction(0))


class Abweichungsanalyse:
    """Vollständiger Analysebaum für einen Zeitraum (Monat oder YTD).

    Enthält Plan-Ist (Umsatz, Deckungsbeitrag, Kostenstellen), Ist-Vorjahr
    (Umsatz, Deckungsbeitrag) und optional Ist-Forecast-Vergleiche.
    """

    def __init__(
        self,
        daten: Datenbestand,
        geschaeftsjahr: int | None = None,
        periode: int | None = None,
        zeitraum: Zeitraum = "Monat",
        wesentlichkeit: Wesentlichkeit | None = None,
        forecast_vergleiche: Sequence[ForecastVergleich] = (),
    ) -> None:
        self.daten = daten
        self.geschaeftsjahr = geschaeftsjahr or daten.geschaeftsjahr
        self.periode = periode or daten.periode
        self.zeitraum: Zeitraum = zeitraum
        self.perioden = tuple(range(1, self.periode + 1)) if zeitraum == "YTD" else (self.periode,)
        self.wesentlichkeit = wesentlichkeit or Wesentlichkeit()
        self._typ = daten.kostenarttyp()
        self._knoten: dict[str, Abweichung] = {}
        self._kinder: dict[str, list[str]] = {}
        self._wurzeln: list[str] = []
        self.kostenstellen: dict[str, Kostenstellenrechnung] = {}
        self._manuell = self._manuelle_buchungen()

        ist = self._absatz("IST", self.geschaeftsjahr)
        plan = self._absatz("PLAN", self.geschaeftsjahr)
        vorjahr = self._absatz("IST", self.geschaeftsjahr - 1)
        for kennzahl in ("Umsatz", "Deckungsbeitrag"):
            self._absatzbaum(kennzahl, Vergleichsart.PLAN_IST, ist, plan, treiberebene=kennzahl == "Umsatz")
        self._kostenbaum()
        for kennzahl in ("Umsatz", "Deckungsbeitrag"):
            self._absatzbaum(kennzahl, Vergleichsart.IST_VJ, ist, vorjahr, treiberebene=False)
        self._ebit()
        for fv in forecast_vergleiche:
            self._forecastknoten(fv)
        self.knoten = self._sortiert()
        logger.info("Analysebaum %s: %d Objekte, davon %d wesentlich.", self.periodenlabel,
                    len(self.knoten), sum(a.wesentlich for a in self.knoten.values()))

    # --- Datenaufbereitung -------------------------------------------------
    @property
    def periodenlabel(self) -> str:
        if self.zeitraum == "YTD":
            return f"01–{self.periode:02d}/{self.geschaeftsjahr} (YTD)"
        return f"{self.periode:02d}/{self.geschaeftsjahr}"

    def _zeilen(self, version: str, jahr: int) -> pd.DataFrame:
        a = self.daten.acdoca
        return a[(a["version"] == version) & (a["gjahr"] == jahr) & a["poper"].isin(self.perioden)]

    def _absatz(self, version: str, jahr: int) -> pd.DataFrame:
        """Menge, Umsatz und Herstellkosten je Region/Produktgruppe/Material/Kunde (Cent, positiv)."""
        z = self._zeilen(version, jahr)
        typ = z["racct"].map(self._typ)
        prodh = dict(zip(self.daten.material["matnr"], self.daten.material["prodh"], strict=True))
        erl = z[typ == "ERLOES"].assign(prodh=lambda d: d["matnr"].map(prodh))
        hk = z[typ == "HK"].assign(prodh=lambda d: d["matnr"].map(prodh))
        e = erl.groupby(_ABSATZSCHLUESSEL, as_index=False).agg(menge=("msl", "sum"), umsatz=("hsl_cent", "sum"))
        e["umsatz"] = -e["umsatz"]
        h = hk.groupby(_ABSATZSCHLUESSEL, as_index=False).agg(hk=("hsl_cent", "sum"))
        df = e.merge(h, on=_ABSATZSCHLUESSEL, how="outer").fillna({"menge": 0, "umsatz": 0, "hk": 0})
        return df.astype({"menge": "int64", "umsatz": "int64", "hk": "int64"})

    def _manuelle_buchungen(self) -> pd.DataFrame:
        z = self._zeilen("IST", self.geschaeftsjahr)
        prodh = dict(zip(self.daten.material["matnr"], self.daten.material["prodh"], strict=True))
        m = z[(z["erfassung"] == "MANUELL") & z["sgtxt"].notna()].assign(prodh=lambda d: d["matnr"].map(prodh))
        return m.assign(betrag=m["hsl_cent"].abs()).sort_values(["betrag", "belnr"], ascending=[False, True])

    def _datentexte(self, **filter_: str) -> list[str]:
        m = self._manuell
        for spalte, wert in filter_.items():
            m = m[m[spalte] == wert]
        return list(dict.fromkeys(m["sgtxt"].astype(str)))[:3]

    # --- Knotenverwaltung --------------------------------------------------
    def _registriere(
        self, *, id: str, bezeichnung: str, kennzahl: str, vergleich: Vergleichsart, ebene: str,
        eltern: str | None, ist: int, basis: int, effekte: dict[Effektart, int], einmal: bool = False,
        treiberebene: bool = False, datentexte: Sequence[str] = (), kinder_vollstaendig: bool = False,
    ) -> Abweichung:
        treiber = [Treiber(effektart=art, betrag=_decimal(b)) for art, b in effekte.items() if b != 0]
        abw = Abweichung(
            id=id, bezeichnung=bezeichnung, kennzahl=kennzahl, art=_KENNZAHLART[kennzahl], vergleich=vergleich,
            einheit=Einheit.EUR, ebene=ebene, eltern_id=eltern, kinder_vollstaendig=kinder_vollstaendig,
            ist=_decimal(ist), vergleichswert=_decimal(basis),
            wesentlich=self.wesentlichkeit.ist_wesentlich(ist - basis, basis),
            einmaleffekt=einmal, treiberebene=treiberebene, treiber=treiber, datentexte=list(datentexte),
        )
        self._knoten[id] = abw
        if eltern is None:
            self._wurzeln.append(id)
        else:
            self._kinder.setdefault(eltern, []).append(id)
        return abw

    def _sortiert(self) -> dict[str, Abweichung]:
        reihenfolge: dict[str, Abweichung] = {}

        def besuche(knoten_id: str) -> None:
            reihenfolge[knoten_id] = self._knoten[knoten_id]
            for kind in self._kinder.get(knoten_id, []):
                besuche(kind)

        for wurzel in self._wurzeln:
            besuche(wurzel)
        return reihenfolge

    @staticmethod
    def _summiere(knoten: Iterable[Abweichung]) -> tuple[int, int, dict[Effektart, int], bool]:
        ist = basis = 0
        effekte: Counter[Effektart] = Counter()
        einmal = False
        for k in knoten:
            ist += int(k.ist * 100)
            basis += int(k.vergleichswert * 100)
            for t in k.treiber:
                effekte[t.effektart] += int(t.betrag * 100)
            einmal = einmal or k.einmaleffekt
        return ist, basis, dict(effekte), einmal

    @staticmethod
    def _gerundet(effekte: dict[Effektart, Fraction], ziel: int) -> dict[Effektart, int]:
        arten = list(effekte)
        return dict(zip(arten, runde_additiv([effekte[a] for a in arten], ziel), strict=True))

    # --- Umsatz und Deckungsbeitrag ----------------------------------------
    def _absatzbaum(
        self, kennzahl: str, vergleich: Vergleichsart, ist: pd.DataFrame, basis: pd.DataFrame, treiberebene: bool
    ) -> None:
        prefix = _KURZ[kennzahl] if vergleich is Vergleichsart.PLAN_IST else f"VJ-{_KURZ[kennzahl]}"
        df = ist.merge(basis, on=_ABSATZSCHLUESSEL, how="outer", suffixes=("_ist", "_basis"))
        df = df.fillna(0)
        for spalte in ("menge", "umsatz", "hk"):
            df[f"{spalte}_ist"] = df[f"{spalte}_ist"].astype("int64")
            df[f"{spalte}_basis"] = df[f"{spalte}_basis"].astype("int64")
        if kennzahl == "Umsatz":
            df["wert_ist"], df["wert_basis"] = df["umsatz_ist"], df["umsatz_basis"]
        else:
            df["wert_ist"] = df["umsatz_ist"] - df["hk_ist"]
            df["wert_basis"] = df["umsatz_basis"] - df["hk_basis"]
        zeilen: dict[tuple[str, str, str, str], tuple[int, int, int, int]] = {
            (z.regio, z.prodh, z.matnr, z.kunnr): (int(z.menge_ist), int(z.wert_ist), int(z.menge_basis), int(z.wert_basis))
            for z in df.itertuples()
        }
        d = self.daten
        regionen = list(zip(d.regionen["regio"], d.regionen["bezeichnung"], strict=True))
        gruppen = list(zip(d.produktgruppen["prodh"], d.produktgruppen["bezeichnung"], strict=True))
        materialien = list(zip(d.material["matnr"], d.material["bezeichnung"], d.material["prodh"], strict=True))
        kunden = list(zip(d.kunden["kunnr"], d.kunden["name"], d.kunden["regio"], strict=True))
        plan_ist = vergleich is Vergleichsart.PLAN_IST

        def texte(**f: str) -> list[str]:
            return self._datentexte(**f) if plan_ist else []

        gesamt_id = prefix
        regionsknoten: list[Abweichung] = []
        for regio, rname in regionen:
            region_id = f"{prefix}-{regio}"
            gruppenknoten: list[Abweichung] = []
            for prodh, gname in gruppen:
                gruppe_id = f"{region_id}-{prodh}"
                mats = [(m, mb) for m, mb, g in materialien if g == prodh]
                positionen: list[Position] = []
                produktknoten: list[Abweichung] = []
                for matnr, mbez in mats:
                    kd = [(k, kn) for k, kn, r in kunden if r == regio and (regio, prodh, matnr, k) in zeilen]
                    if not kd:
                        continue
                    produkt_id = f"{gruppe_id}-{matnr}"
                    werte = [zeilen[(regio, prodh, matnr, k)] for k, _ in kd]
                    pos = Position(matnr, *(sum(w[i] for w in werte) for i in range(4)))
                    positionen.append(pos)
                    kundenknoten = []
                    for (kunnr, kname), w in zip(kd, werte, strict=True):
                        kz = zerlege_umsatz([Position(kunnr, *w)])
                        kundenknoten.append(self._registriere(
                            id=f"{produkt_id}-{kunnr}", bezeichnung=f"{kennzahl} {rname} – {mbez} – {kname}",
                            kennzahl=kennzahl, vergleich=vergleich, ebene="Kunde", eltern=produkt_id,
                            ist=w[1], basis=w[3], effekte=self._gerundet(kz.effekte(), w[1] - w[3]),
                            datentexte=texte(regio=regio, matnr=matnr, kunnr=kunnr),
                        ))
                    pz = zerlege_umsatz([pos])
                    produktknoten.append(self._registriere(
                        id=produkt_id, bezeichnung=f"{kennzahl} {rname} – {mbez}", kennzahl=kennzahl,
                        vergleich=vergleich, ebene="Produkt", eltern=gruppe_id, ist=pos.wert_ist,
                        basis=pos.wert_basis, effekte=self._gerundet(pz.effekte(), pos.wert_ist - pos.wert_basis),
                        datentexte=texte(regio=regio, matnr=matnr), kinder_vollstaendig=True,
                    ))
                if not positionen:
                    continue
                gz = zerlege_umsatz(positionen)
                ist_g = sum(p.wert_ist for p in positionen)
                basis_g = sum(p.wert_basis for p in positionen)
                gruppenknoten.append(self._registriere(
                    id=gruppe_id, bezeichnung=f"{kennzahl} {rname} – {gname}", kennzahl=kennzahl, vergleich=vergleich,
                    ebene="Produktgruppe", eltern=region_id, ist=ist_g, basis=basis_g,
                    effekte=self._gerundet(gz.effekte(), ist_g - basis_g), treiberebene=treiberebene and plan_ist,
                    datentexte=texte(regio=regio, prodh=prodh), kinder_vollstaendig=True,
                ))
            ist_r, basis_r, eff_r, _ = self._summiere(gruppenknoten)
            regionsknoten.append(self._registriere(
                id=region_id, bezeichnung=f"{kennzahl} Region {rname}", kennzahl=kennzahl, vergleich=vergleich,
                ebene="Region", eltern=gesamt_id, ist=ist_r, basis=basis_r, effekte=eff_r,
                datentexte=texte(regio=regio), kinder_vollstaendig=True,
            ))
        ist_t, basis_t, eff_t, _ = self._summiere(regionsknoten)
        self._registriere(id=gesamt_id, bezeichnung=f"{kennzahl} Gesamt", kennzahl=kennzahl, vergleich=vergleich,
                          ebene="Gesamt", eltern=None, ist=ist_t, basis=basis_t, effekte=eff_t, kinder_vollstaendig=True)

    # --- Kostenstellen -----------------------------------------------------
    def _kostenbaum(self) -> None:
        d = self.daten
        ist = self._zeilen("IST", self.geschaeftsjahr)
        plan = self._zeilen("PLAN", self.geschaeftsjahr)
        ist, plan = ist[ist["rcntr"].notna()], plan[plan["rcntr"].notna()]
        lst = d.kst_leistung
        lst = lst[(lst["gjahr"] == self.geschaeftsjahr) & lst["poper"].isin(self.perioden)]
        stunden = lst.groupby(["version", "rcntr"])["menge_h"].sum().to_dict()
        ka = d.kostenarten.set_index("racct")
        kostenstellen = list(zip(d.kostenstellen["rcntr"], d.kostenstellen["bezeichnung"], strict=True))

        kst_knoten: list[Abweichung] = []
        for rcntr, kbez in kostenstellen:
            kst_id = f"KST-{rcntr}"
            h_ist = int(stunden.get(("IST", rcntr), 0))
            h_plan = int(stunden.get(("PLAN", rcntr), 0))
            if h_plan == 0:
                logger.warning("Kostenstelle %s ohne Planbeschäftigung – Beschäftigungsgrad 1 angenommen.", rcntr)
            bg = Fraction(h_ist, h_plan) if h_plan else Fraction(1)
            rechnung = Kostenstellenrechnung(rcntr, h_ist, h_plan, bg)
            i_k, p_k = ist[ist["rcntr"] == rcntr], plan[plan["rcntr"] == rcntr]
            kostenarten = [r for r in d.kostenarten["racct"] if r in set(i_k["racct"]) | set(p_k["racct"])]
            ka_knoten: list[Abweichung] = []
            for racct in kostenarten:
                i_a, p_a = i_k[i_k["racct"] == racct], p_k[p_k["racct"] == racct]
                mit_menge = i_a[i_a["msl"].notna()]
                werte = Kostenartwerte(
                    plan_fix=int(p_a.loc[p_a["kostenverhalten"] == "F", "hsl_cent"].sum()),
                    plan_var=int(p_a.loc[p_a["kostenverhalten"] == "V", "hsl_cent"].sum()),
                    plan_menge=int(p_a.loc[p_a["kostenverhalten"] == "V", "msl"].sum()),
                    ist=int(i_a["hsl_cent"].sum()),
                    ist_mit_menge=int(mit_menge["hsl_cent"].sum()),
                    ist_menge=int(mit_menge["msl"].sum()),
                    mengenbasiert=bool(ka.at[racct, "mengenbasiert"]),
                    einmalig=bool(ka.at[racct, "einmalig"]),
                )
                z = zerlege_kosten(werte, bg)
                rechnung.kostenarten[racct] = z
                rechnung.werte[racct] = werte
                verrechnet = runde_cent(z.verrechnete_plankosten)
                ka_knoten.append(self._registriere(
                    id=f"{kst_id}-{racct}", bezeichnung=f"Kostenstelle {rcntr} – {ka.at[racct, 'bezeichnung']}",
                    kennzahl="Kosten", vergleich=Vergleichsart.PLAN_IST, ebene="Kostenart", eltern=kst_id,
                    ist=werte.ist, basis=verrechnet, effekte=self._gerundet(z.effekte(), werte.ist - verrechnet),
                    einmal=werte.einmalig and werte.ist != 0, treiberebene=True,
                    datentexte=self._datentexte(rcntr=rcntr, racct=racct),
                ))
            self.kostenstellen[rcntr] = rechnung
            ist_s, basis_s, eff_s, einmal_s = self._summiere(ka_knoten)
            kst_knoten.append(self._registriere(
                id=kst_id, bezeichnung=f"Kostenstelle {rcntr} {kbez}", kennzahl="Kosten",
                vergleich=Vergleichsart.PLAN_IST, ebene="Kostenstelle", eltern="KST", ist=ist_s, basis=basis_s,
                effekte=eff_s, einmal=einmal_s, datentexte=self._datentexte(rcntr=rcntr),
                kinder_vollstaendig=bool(ka_knoten),
            ))
        ist_t, basis_t, eff_t, einmal_t = self._summiere(kst_knoten)
        self._registriere(id="KST", bezeichnung="Kosten Kostenstellen gesamt", kennzahl="Kosten",
                          vergleich=Vergleichsart.PLAN_IST, ebene="Gesamt", eltern=None, ist=ist_t, basis=basis_t,
                          effekte=eff_t, einmal=einmal_t, kinder_vollstaendig=True)

    # --- EBIT-Brücke ------------------------------------------------------
    def _kostenstellenkosten(self, version: str, jahr: int) -> int:
        z = self._zeilen(version, jahr)
        return int(z.loc[z["rcntr"].notna(), "hsl_cent"].sum())

    def _ebit(self) -> None:
        """EBIT (vereinfacht) = Deckungsbeitrag − Kosten der Kostenstellen.

        Brücke Plan → Ist, exakt additiv auf den Cent:

        ΔEBIT = ΔDB (Menge, Mix, Preis, Sortiment)
              − Kostenstellenabweichungen (Preis, Verbrauch, Beschäftigung, Einmal)
              − Plankostenanpassung (verrechnete Plankosten − Plankosten)
        """
        db, db_vj, kst = self._knoten["DB"], self._knoten["VJ-DB"], self._knoten["KST"]
        kosten_ist = int(kst.ist * 100)
        kosten_plan = self._kostenstellenkosten("PLAN", self.geschaeftsjahr)
        kosten_vj = self._kostenstellenkosten("IST", self.geschaeftsjahr - 1)
        verrechnet = int(kst.vergleichswert * 100)
        effekte: Counter[Effektart] = Counter()
        for t in db.treiber:
            effekte[t.effektart] += int(t.betrag * 100)
        for t in kst.treiber:
            effekte[t.effektart] -= int(t.betrag * 100)
        effekte[Effektart.PLANKOSTENANPASSUNG] -= verrechnet - kosten_plan
        ist = int(db.ist * 100) - kosten_ist
        self._registriere(
            id="EBIT", bezeichnung="EBIT Gesamt (vereinfacht)", kennzahl="EBIT", vergleich=Vergleichsart.PLAN_IST,
            ebene="Gesamt", eltern=None, ist=ist, basis=int(db.vergleichswert * 100) - kosten_plan,
            effekte=dict(effekte), einmal=kst.einmaleffekt,
        )
        self._registriere(
            id="VJ-EBIT", bezeichnung="EBIT Gesamt (vereinfacht)", kennzahl="EBIT", vergleich=Vergleichsart.IST_VJ,
            ebene="Gesamt", eltern=None, ist=ist, basis=int(db_vj.vergleichswert * 100) - kosten_vj, effekte={},
        )
        self.ergebnisrechnung = [
            Ergebniszeile("Umsatz", int(self._knoten["UMS"].ist * 100), int(self._knoten["UMS"].vergleichswert * 100),
                          int(self._knoten["VJ-UMS"].vergleichswert * 100), Kennzahlart.ERLOES),
            Ergebniszeile("Herstellkosten (Standard)",
                          int((self._knoten["UMS"].ist - db.ist) * 100),
                          int((self._knoten["UMS"].vergleichswert - db.vergleichswert) * 100),
                          int((self._knoten["VJ-UMS"].vergleichswert - db_vj.vergleichswert) * 100), Kennzahlart.KOSTEN),
            Ergebniszeile("Deckungsbeitrag", int(db.ist * 100), int(db.vergleichswert * 100),
                          int(db_vj.vergleichswert * 100), Kennzahlart.ERGEBNIS),
            Ergebniszeile("Kosten Kostenstellen", kosten_ist, kosten_plan, kosten_vj, Kennzahlart.KOSTEN),
            Ergebniszeile("EBIT", ist, int(db.vergleichswert * 100) - kosten_plan,
                          int(db_vj.vergleichswert * 100) - kosten_vj, Kennzahlart.ERGEBNIS),
        ]

    def _forecastknoten(self, fv: ForecastVergleich) -> None:
        gesamt = self._knoten[_KURZ[fv.kennzahl]]
        ist = int(gesamt.ist * 100)
        self._registriere(
            id=f"FC-{_KURZ[fv.kennzahl]}", bezeichnung=f"{fv.kennzahl} Gesamt (Forecast-Version {fv.version})",
            kennzahl=fv.kennzahl, vergleich=Vergleichsart.IST_FC, ebene="Gesamt", eltern=None,
            ist=ist, basis=fv.basis_cent, effekte={},
        )

    # =========================================================================
    # Abfragen (werden über das EngineBackend als Tools bereitgestellt)
    # =========================================================================
    def abweichung(self, objekt_id: str) -> Abweichung:
        try:
            return self.knoten[objekt_id]
        except KeyError:
            raise ObjektNichtGefunden(f"Analyseobjekt „{objekt_id}“ existiert nicht.") from None

    def drilldown(self, objekt_id: str) -> list[Abweichung]:
        """Nächsttiefere Ebene, sortiert nach |Δ| absteigend."""
        self.abweichung(objekt_id)
        kinder = [self.knoten[k] for k in self._kinder.get(objekt_id, [])]
        return sorted(kinder, key=lambda a: (-abs(a.delta_abs or 0), a.id))

    def wesentliche(self) -> list[Abweichung]:
        return [a for a in self.knoten.values() if a.wesentlich]

    def top_treiber(self, anzahl: int = 5, objekt_id: str | None = None) -> TreiberRanking:
        """Ranking der Treiber auf der Treiberebene (Region × Produktgruppe, Kostenart).

        Auf dieser Ebene addieren sich alle Effekte exakt zur Gesamtabweichung –
        es gibt keine Doppelzählung über Hierarchieebenen.
        """
        if objekt_id is not None:
            return rangliste_treiber([self.abweichung(objekt_id)], anzahl, objekt_id)
        return rangliste_treiber((a for a in self.knoten.values() if a.treiberebene), anzahl)

    def uebersicht(self, nur_wesentliche: bool = True, maximal: int = 40) -> list[AbweichungKurz]:
        if nur_wesentliche:
            auswahl = sorted(self.wesentliche(), key=lambda a: (-abs(a.delta_abs or 0), a.id))
        else:
            auswahl = [a for a in self.knoten.values()
                       if a.ebene in ("Gesamt", "Region", "Produktgruppe", "Kostenstelle")]
        return [AbweichungKurz.aus(a) for a in auswahl[:maximal]]

    # --- Faktenblatt -------------------------------------------------------
    def kennzahlen(self) -> list[Kennzahl]:
        """Abgeleitete Kennzahlen: DB-Quote Ist/Plan/Abweichung und Beschäftigungsgrade."""
        werte: list[Kennzahl] = []
        ums, db = self.knoten.get("UMS"), self.knoten.get("DB")
        if ums is not None and db is not None and ums.ist and ums.vergleichswert:
            q_ist = Fraction(int(db.ist * 100), int(ums.ist * 100)) * 100
            q_plan = Fraction(int(db.vergleichswert * 100), int(ums.vergleichswert * 100)) * 100
            werte += [
                Kennzahl(id="DBQ-IST", bezeichnung="DB-Quote Ist", einheit=Einheit.PROZENT, wert=_prozent_dezimal(q_ist)),
                Kennzahl(id="DBQ-PLAN", bezeichnung="DB-Quote Plan", einheit=Einheit.PROZENT, wert=_prozent_dezimal(q_plan)),
                Kennzahl(id="DBQ-ABW", bezeichnung="DB-Quote Abweichung", einheit=Einheit.PROZENTPUNKTE,
                         wert=_prozent_dezimal(q_ist - q_plan)),
            ]
        namen = dict(zip(self.daten.kostenstellen["rcntr"], self.daten.kostenstellen["bezeichnung"], strict=True))
        for rcntr, r in self.kostenstellen.items():
            werte.append(Kennzahl(id=f"BG-{rcntr}", bezeichnung=f"Beschäftigungsgrad Kostenstelle {rcntr} {namen[rcntr]}",
                                  einheit=Einheit.PROZENT, wert=_prozent_dezimal(r.beschaeftigungsgrad * 100)))
        return werte

    def als_faktenblatt(
        self,
        datenqualitaet: Datenqualitaet,
        forecasts: Sequence[ForecastInfo] = (),
        unternehmen: str = UNTERNEHMEN,
        vollstaendig: bool = False,
    ) -> Faktenblatt:
        """Erzeugt das Faktenblatt für den Agenten.

        ``vollstaendig=False`` (Standard) liefert die kompakte Fassung für den
        Prompt: Übersichtsebenen, alle Kostenarten, wesentliche Produkte sowie
        Vorjahres- und Forecast-Vergleich auf Gesamt-/Regionsebene. Tiefere
        Ebenen (z. B. Kunde) ruft der Agent bei Bedarf per Tool ab.
        """
        if vollstaendig:
            auswahl = set(self.knoten)
        else:
            auswahl = {
                i for i, a in self.knoten.items()
                if (a.vergleich is Vergleichsart.PLAN_IST and a.ebene in ("Gesamt", "Region", "Produktgruppe", "Kostenstelle", "Kostenart"))
                or (a.vergleich is Vergleichsart.PLAN_IST and a.ebene == "Produkt" and a.wesentlich)
                or (a.vergleich is not Vergleichsart.PLAN_IST and a.ebene in ("Gesamt", "Region"))
            }
        abweichungen = []
        for i, a in self.knoten.items():
            if i not in auswahl:
                continue
            kinder = self._kinder.get(i, [])
            vollstaendig_enthalten = a.kinder_vollstaendig and bool(kinder) and all(k in auswahl for k in kinder)
            abweichungen.append(a if vollstaendig_enthalten == a.kinder_vollstaendig
                                else a.model_copy(update={"kinder_vollstaendig": vollstaendig_enthalten}))
        return Faktenblatt(
            unternehmen=unternehmen, geschaeftsjahr=self.geschaeftsjahr, periode=self.periode, zeitraum=self.zeitraum,
            datenqualitaet=datenqualitaet, abweichungen=abweichungen, forecasts=list(forecasts),
            kennzahlen=self.kennzahlen(),
        )


# =============================================================================
# 3. Backend für die Tool-Whitelist des Agenten
# =============================================================================


class EngineBackend:
    """Verbindet die Tool-Whitelist aus ``agent.py`` mit den echten Engine-Funktionen.

    Anders als das Faktenblatt (kompakt, für den Prompt) liefert dieses Backend
    den vollständigen Analysebaum – Drill-down bis Kunde bzw. Kostenart.
    """

    def __init__(self, analyse: Abweichungsanalyse, forecasts: Sequence[ForecastInfo] = ()) -> None:
        self.analyse = analyse
        self.forecasts = {f.kennzahl.lower(): f for f in forecasts}

    def abweichungen_auflisten(self, nur_wesentliche: bool = True) -> list[AbweichungKurz]:
        return self.analyse.uebersicht(nur_wesentliche)

    def abweichung_holen(self, objekt_id: str) -> Abweichung:
        return self.analyse.abweichung(objekt_id)

    def drilldown(self, objekt_id: str) -> list[Abweichung]:
        return self.analyse.drilldown(objekt_id)

    def top_treiber(self, anzahl: int = 5, objekt_id: str | None = None) -> TreiberRanking:
        return self.analyse.top_treiber(anzahl, objekt_id)

    def forecast_holen(self, kennzahl: str) -> ForecastInfo:
        fc = self.forecasts.get(kennzahl.strip().lower())
        if fc is None:
            verfuegbar = ", ".join(f.kennzahl for f in self.forecasts.values()) or "keine"
            raise ObjektNichtGefunden(f"Kein Forecast für „{kennzahl}“. Verfügbar: {verfuegbar}.")
        return fc
