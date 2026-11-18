"""IBCS-konforme Diagramme und automatischer Monatsbericht (PowerPoint und PDF).

Notation nach den International Business Communication Standards (IBCS):

* **AC** (Ist) dunkel gefüllt · **PY** (Vorjahr) grau · **PL** (Plan) umrandet ·
  **FC** (Forecast) schraffiert
* Abweichungen: günstig grün, ungünstig rot – immer zusätzlich mit Vorzeichen
  beschriftet (Farbe ist nie der einzige Träger der Information; das Farbpaar ist
  auf Farbsehschwächen geprüft)
* Botschaft im Titel („SAY“), Einheit und Zeitraum im Untertitel
* Brücke (Wasserfall) Plan → Ist mit additiven Treibern

Der Monatsbericht enthält **ausschließlich freigegebene Kommentare**
(Human-in-the-Loop); Entwürfe erscheinen nie im Bericht.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from src.agent import (  # noqa: E402
    Abweichung,
    Ampel,
    Bewertung,
    Kennzahlart,
    Kommentar,
    Vergleichsart,
    berichtsfaehige_kommentare,
    format_betrag,
    format_prozent,
    format_wert,
    format_zahl,
)
from src.forecast_engine import monatsreihe  # noqa: E402

if TYPE_CHECKING:
    from src.pipeline import Abschlussergebnis

logger = logging.getLogger("varianceai.reporting")

# =============================================================================
# Notation und Farben
# =============================================================================

AC = "#333333"          # Ist: dunkel gefüllt
PY = "#A6A6A6"          # Vorjahr: grau
PL_RAND = "#333333"     # Plan: umrandet
GUENSTIG = "#2E6B30"    # validiert (CVD ΔE ≥ 10) – immer mit Vorzeichen beschriftet
UNGUENSTIG = "#F06A4F"
TEXT = "#1A1A1A"
TEXT_LEISE = "#595959"
LINIE = "#BFBFBF"
AMPELFARBE = {Ampel.GRUEN: "#2E6B30", Ampel.GELB: "#C9A227", Ampel.ROT: "#C62828"}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.edgecolor": LINIE,
    "axes.labelcolor": TEXT_LEISE,
    "xtick.color": TEXT_LEISE,
    "ytick.color": TEXT_LEISE,
    "hatch.linewidth": 0.8,
    "hatch.color": AC,
    "figure.dpi": 100,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
})


def _teur(wert: Decimal | float | int) -> float:
    return float(wert) / 1000


def _t(wert: float, stellen: int = 1, vorzeichen: bool = False) -> str:
    return format_zahl(Decimal(str(round(wert, 6))), stellen, vorzeichen)


def _farbe(betrag: float, art: Kennzahlart) -> str:
    if betrag == 0:
        return PY
    guenstig = (betrag > 0) != (art is Kennzahlart.KOSTEN)
    return GUENSTIG if guenstig else UNGUENSTIG


def _achse_ruhig(ax, nur_x: bool = False) -> None:
    for seite in ("top", "right", "left"):
        ax.spines[seite].set_visible(False)
    if nur_x:
        ax.spines["bottom"].set_visible(False)
    ax.tick_params(length=0)
    ax.grid(False)


_TITEL = {"aktiv": True}


@contextmanager
def ohne_titel() -> Iterator[None]:
    """Diagramme ohne eigenen Titel rendern (auf Folien trägt der Folientitel die Botschaft)."""
    vorher = _TITEL["aktiv"]
    _TITEL["aktiv"] = False
    try:
        yield
    finally:
        _TITEL["aktiv"] = vorher


def _kopf(fig: Figure, botschaft: str, untertitel: str) -> None:
    """IBCS-Titelbereich: Botschaft (fett) und Untertitel mit Einheit/Zeitraum."""
    if not _TITEL["aktiv"]:
        return
    fig.text(0.0, 1.06, botschaft, fontsize=13, fontweight="bold", color=TEXT, ha="left", va="bottom",
             transform=fig.transFigure)
    fig.text(0.0, 1.01, untertitel, fontsize=9.5, color=TEXT_LEISE, ha="left", va="bottom", transform=fig.transFigure)


def _kurz(bezeichnung: str) -> str:
    for praefix in ("Umsatz ", "Deckungsbeitrag ", "Kostenstelle "):
        if bezeichnung.startswith(praefix):
            return bezeichnung[len(praefix):]
    return bezeichnung


# =============================================================================
# Diagramme
# =============================================================================


def diagramm_kpi(abschluss: Abschlussergebnis) -> Figure:
    """Small Multiples: Umsatz, Deckungsbeitrag, EBIT als PY | PL | AC (T€)."""
    zeilen = {z.kennzahl: z for z in abschluss.analyse.ergebnisrechnung}
    kpis = [("Umsatz", "Umsatz"), ("Deckungsbeitrag", "Deckungsbeitrag"), ("EBIT", "EBIT")]
    fig, achsen = plt.subplots(1, 3, figsize=(10, 3.4))
    for ax, (schluessel, titel) in zip(achsen, kpis, strict=True):
        z = zeilen[schluessel]
        werte = [_teur(z.vorjahr / 100), _teur(z.plan / 100), _teur(z.ist / 100)]
        ax.bar(0, werte[0], width=0.62, color=PY)
        ax.bar(1, werte[1], width=0.62, facecolor="white", edgecolor=PL_RAND, linewidth=1.2)
        ax.bar(2, werte[2], width=0.62, color=AC)
        for x, w in enumerate(werte):
            ax.text(x, w, _t(w, 0), ha="center", va="bottom", fontsize=9, color=TEXT)
        ax.set_xticks([0, 1, 2], ["PY", "PL", "AC"])
        ax.set_yticks([])
        ax.set_ylim(0, max(werte) * 1.22)
        _achse_ruhig(ax)
        d_pl = (z.ist - z.plan) / max(abs(z.plan), 1) * 100
        d_py = (z.ist - z.vorjahr) / max(abs(z.vorjahr), 1) * 100
        ax.set_title(f"{titel}\nΔPL {_t(d_pl, 1, True)} %  ·  ΔPY {_t(d_py, 1, True)} %", fontsize=10, color=TEXT, loc="left")
    _kopf(fig, f"EBIT {_t((zeilen['EBIT'].ist - zeilen['EBIT'].plan) / 100_000, 1, True)} T€ ggü. Plan",
          f"{abschluss.faktenblatt.unternehmen} · in T€ · {abschluss.periodenlabel} · PY, PL, AC")
    fig.tight_layout()
    return fig


_BASIS = {
    Vergleichsart.PLAN_IST: ("PL", "ggü. Plan"),
    Vergleichsart.IST_VJ: ("PY", "ggü. Vorjahr"),
    Vergleichsart.IST_FC: ("FC", "ggü. Forecast"),
}


def diagramm_bruecke(abw: Abweichung, botschaft: str | None = None, untertitel: str | None = None,
                     nur_abweichung: bool | None = None) -> Figure:
    """Wasserfall Plan → Ist mit den additiven Treibern einer Abweichung.

    Sind die Treiber im Verhältnis zur Basis klein (< 12 %), wird automatisch
    eine **Abweichungsbrücke** (Start bei 0, Endbalken ΔPL) gezeichnet – statt
    die Achse abzuschneiden, was IBCS für Säulen nicht zulässt.
    """
    plan, ist = _teur(abw.vergleichswert), _teur(abw.ist)
    basis, vergleichstext = _BASIS[abw.vergleich]
    treiber = [t for t in abw.treiber if t.betrag != 0]
    groesster = max((abs(_teur(t.betrag)) for t in treiber), default=0.0)
    if nur_abweichung is None:
        nur_abweichung = groesster < 0.12 * max(abs(plan), abs(ist))
    fig, ax = plt.subplots(figsize=(10, 4.2))
    effektlabels = [t.effektart.value.replace("abweichung", "-\nabweichung").replace("effekt", "-\neffekt")
                    .replace("anpassung", "-\nanpassung") for t in treiber]
    if nur_abweichung:
        labels = ["0"] + effektlabels + [f"Δ{basis}"]
        ausschlag = max(groesster, abs(ist - plan)) or 1
        stand = 0.0
        ax.plot([-0.3, 0.3], [0, 0], color=AC, linewidth=1.2)
        plan_anzeige = 0.0
    else:
        labels = [basis] + effektlabels + ["AC"]
        ax.bar(0, plan, width=0.6, facecolor="white", edgecolor=PL_RAND, linewidth=1.2)
        ax.text(0, max(plan, 0), _t(plan, 1), ha="center", va="bottom", fontsize=9, color=TEXT)
        stand = plan_anzeige = plan
        ausschlag = max(abs(plan), abs(ist), groesster) or 1
    for i, t in enumerate(treiber, start=1):
        delta = _teur(t.betrag)
        ax.plot([i - 1 + 0.3, i - 0.3], [stand, stand], color=LINIE, linewidth=0.8)
        ax.bar(i, delta, bottom=stand, width=0.6, color=_farbe(delta, abw.art))
        oben = max(stand, stand + delta)
        unten = min(stand, stand + delta)
        if delta >= 0:
            ax.text(i, oben + ausschlag * 0.01, _t(delta, 1, True), ha="center", va="bottom", fontsize=9, color=TEXT)
        else:
            ax.text(i, unten - ausschlag * 0.01, _t(delta, 1, True), ha="center", va="top", fontsize=9, color=TEXT)
        stand += delta
    n = len(treiber) + 1
    ax.plot([n - 1 + 0.3, n - 0.3], [stand, stand], color=LINIE, linewidth=0.8)
    if nur_abweichung:
        gesamt = ist - plan
        ax.bar(n, gesamt, width=0.6, color=_farbe(gesamt, abw.art))
        ax.text(n, gesamt + (ausschlag * 0.01 if gesamt >= 0 else -ausschlag * 0.01), _t(gesamt, 1, True),
                ha="center", va="bottom" if gesamt >= 0 else "top", fontsize=9, color=TEXT, fontweight="bold")
    else:
        ax.bar(n, ist, width=0.6, color=AC)
        ax.text(n, max(ist, 0), _t(ist, 1), ha="center", va="bottom", fontsize=9, color=TEXT)
    ax.axhline(0, color=AC, linewidth=0.8)
    ax.set_xticks(range(n + 1), labels, fontsize=8.5)
    ax.set_yticks([])
    tiefster = min(0.0, *(p.get_y() for p in ax.patches), *(p.get_y() + p.get_height() for p in ax.patches))
    hoechster = max(0.0, *(p.get_y() + p.get_height() for p in ax.patches), *(p.get_y() for p in ax.patches))
    ax.set_ylim(tiefster - ausschlag * 0.15, hoechster + ausschlag * 0.12)
    _achse_ruhig(ax)
    delta = ist - plan_anzeige if not nur_abweichung else ist - plan
    art = f"Abweichungsbrücke Δ{basis} (Basis 0)" if nur_abweichung else f"Brücke {basis} → AC"
    _kopf(fig, botschaft or f"{abw.bezeichnung}: {_t(delta, 1, True)} T€ {vergleichstext}",
          untertitel or f"in T€ · {art} · günstig grün, ungünstig rot")
    fig.tight_layout()
    return fig


def diagramm_abweichungen(objekte: Sequence[Abweichung], botschaft: str, untertitel: str) -> Figure:
    """Horizontale Abweichungsbalken ΔPL (T€) mit Vorzeichen- und Prozentbeschriftung."""
    objekte = sorted(objekte, key=lambda a: a.delta_abs)  # type: ignore[arg-type, return-value]
    hoehe = max(2.6, 0.42 * len(objekte) + 1.0)
    fig, ax = plt.subplots(figsize=(10, hoehe))
    werte = [_teur(a.delta_abs) for a in objekte]  # type: ignore[arg-type]
    farben = [GUENSTIG if a.bewertung is Bewertung.GUENSTIG else UNGUENSTIG if a.bewertung is Bewertung.UNGUENSTIG
              else PY for a in objekte]
    y = list(range(len(objekte)))
    ax.barh(y, werte, height=0.62, color=farben)
    spanne = max((abs(w) for w in werte), default=1) or 1
    for yi, w, a in zip(y, werte, objekte, strict=True):
        pct = f" ({format_prozent(a.delta_pct)})" if a.delta_pct is not None else ""
        text = f"{_t(w, 1, True)}{pct}"
        ax.text(w + (spanne * 0.015 if w >= 0 else -spanne * 0.015), yi, text, va="center",
                ha="left" if w >= 0 else "right", fontsize=10, color=TEXT)
    ax.axvline(0, color=AC, linewidth=1.0)
    ax.set_yticks(y, [_kurz(a.bezeichnung) for a in objekte], fontsize=10.5)
    ax.set_xticks([])
    # Platz für die Beschriftung nur auf den Seiten, auf denen Balken liegen
    links = min([0.0, *werte])
    rechts = max([0.0, *werte])
    ax.set_xlim(links - spanne * (0.6 if links < 0 else 0.05), rechts + spanne * (0.6 if rechts > 0 else 0.05))
    _achse_ruhig(ax, nur_x=True)
    _kopf(fig, botschaft, untertitel)
    fig.tight_layout()
    return fig


def diagramm_forecast(abschluss: Abschlussergebnis, kennzahl: str = "Umsatz") -> Figure:
    """Monatsverlauf des Geschäftsjahres: AC gefüllt, FC schraffiert, PL umrandet, Intervall als Linie."""
    e = abschluss.einstellungen
    fc = abschluss.forecasts[kennzahl]
    ist = monatsreihe(abschluss.daten, kennzahl)
    plan = monatsreihe(abschluss.daten, kennzahl, "PLAN")
    ist_jahr = {p.month: v for p, v in ist.items() if p.year == e.geschaeftsjahr and p.month <= e.periode}
    plan_jahr = {p.month: v for p, v in plan.items() if p.year == e.geschaeftsjahr}
    fig, ax = plt.subplots(figsize=(10, 3.8))
    monate = list(range(1, 13))
    ax.bar([m - 0.14 for m in monate], [_teur(plan_jahr.get(m, 0) / 100) for m in monate], width=0.5,
           facecolor="white", edgecolor=PL_RAND, linewidth=1.0, zorder=1)
    ax.bar([m + 0.14 for m in ist_jahr], [_teur(v / 100) for v in ist_jahr.values()], width=0.5, color=AC, zorder=2)
    for m in fc.monate:
        ax.bar(m.periode + 0.14, _teur(m.wert_cent / 100), width=0.5, facecolor="white", edgecolor=AC, hatch="////",
               linewidth=1.0, zorder=2)
        ax.plot([m.periode + 0.14] * 2, [_teur(m.unten_cent / 100), _teur(m.oben_cent / 100)], color=TEXT, linewidth=1.2,
                zorder=3)
    namen = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    ax.set_xticks(monate, namen)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _t(v, 0)))
    ax.grid(axis="y", color="#EDEDED", linewidth=0.6)
    ax.set_axisbelow(True)
    for seite in ("top", "right", "left"):
        ax.spines[seite].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(handles=[Patch(color=AC, label="AC"), Patch(facecolor="white", edgecolor=AC, hatch="////", label="FC"),
                       Patch(facecolor="white", edgecolor=PL_RAND, label="PL")],
              loc="upper left", ncol=3, frameon=False, fontsize=8.5)
    i = fc.info
    _kopf(fig, f"Jahresendprognose {kennzahl} {format_betrag(i.jahresendprognose)} "  # type: ignore[arg-type]
               f"({format_prozent(i.abweichung_plan_pct)} ggü. Plan)",  # type: ignore[arg-type]
          f"in T€ je Monat · {e.geschaeftsjahr} · AC, FC ({fc.methode.value}, Intervall "
          f"{format_prozent(i.konfidenzniveau_pct, 0, vorzeichen=False)}), PL")
    fig.tight_layout()
    return fig


# =============================================================================
# Kernaussagen (deterministisch, keine KI)
# =============================================================================


def kernaussagen(abschluss: Abschlussergebnis, anzahl_treiber: int = 3) -> list[str]:
    """Regelbasierte Kernaussagen für Management Summary und Folientitel."""
    a = abschluss.analyse
    ebit = a.abweichung("EBIT")
    aussagen = [
        f"EBIT {format_wert(ebit.ist, ebit.einheit)}: {format_prozent(ebit.delta_pct)} "  # type: ignore[arg-type]
        f"({format_wert(ebit.delta_abs, ebit.einheit, vorzeichen=True)}) ggü. Plan."  # type: ignore[arg-type]
    ]
    for e in a.top_treiber(anzahl_treiber).eintraege:
        aussagen.append(f"{e.objekt_bezeichnung}: {e.effektart.value} {format_wert(e.betrag, e.einheit, vorzeichen=True)}.")
    if "Umsatz" in abschluss.forecasts:
        i = abschluss.forecasts["Umsatz"].info
        aussagen.append(f"Jahresendprognose Umsatz {format_betrag(i.jahresendprognose)} "  # type: ignore[arg-type]
                        f"({format_prozent(i.abweichung_plan_pct)} ggü. Plan).")  # type: ignore[arg-type]
    p = abschluss.protokoll
    nicht_gruen = sum(pt.ampel is not Ampel.GRUEN for pt in p.punkte)
    aussagen.append(f"Datenqualität {p.ampel.value}: {nicht_gruen} von {len(p.punkte)} Prüfpunkten mit Hinweis.")
    return aussagen


def erzeuge_diagramme(abschluss: Abschlussergebnis, ordner: Path | str) -> dict[str, Path]:
    """Rendert alle Berichtsdiagramme als PNG – mit Titel (PDF, Cockpit) und ohne (``*_folie``, PowerPoint)."""
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    pfade: dict[str, Path] = {}
    for suffix in ("", "_folie"):
        if suffix:
            with ohne_titel():
                figuren = berichtsdiagramme(abschluss)
        else:
            figuren = berichtsdiagramme(abschluss)
        for name, fig in figuren.items():
            pfad = ordner / f"{name}{suffix}.png"
            fig.savefig(pfad)
            plt.close(fig)
            pfade[f"{name}{suffix}"] = pfad
    return pfade


def berichtsdiagramme(abschluss: Abschlussergebnis) -> dict[str, Figure]:
    """Alle Diagramme des Monatsberichts als matplotlib-Figuren (auch für das Cockpit)."""
    a = abschluss.analyse
    gruppen = [x for x in a.knoten.values() if x.treiberebene and x.kennzahl == "Umsatz"]
    kst = [x for x in a.knoten.values() if x.ebene == "Kostenstelle"]
    ebit = a.abweichung("EBIT")
    groesster = max(ebit.treiber, key=lambda t: abs(t.betrag))
    return {
        "kpi": diagramm_kpi(abschluss),
        "ebit_bruecke": diagramm_bruecke(
            ebit, f"EBIT {_t(_teur(ebit.delta_abs), 1, True)} T€ ggü. Plan – größter Einzeltreiber: {groesster.effektart.value}",  # type: ignore[arg-type]
            f"{abschluss.faktenblatt.unternehmen} · EBIT in T€ · {abschluss.periodenlabel} · PL → AC"),
        "umsatz_abweichungen": diagramm_abweichungen(
            gruppen,
            f"Umsatz {_t(_teur(a.abweichung('UMS').delta_abs), 1, True)} T€ ggü. Plan – Abweichungen je Region und Produktgruppe",  # type: ignore[arg-type]
            f"ΔPL in T€ · {abschluss.periodenlabel}"),
        "kostenstellen": diagramm_abweichungen(
            kst, f"Kostenstellen {_t(_teur(a.abweichung('KST').delta_abs), 1, True)} T€ über verrechneten Plankosten",  # type: ignore[arg-type]
            f"ΔPL (Ist − verrechnete Plankosten) in T€ · {abschluss.periodenlabel}"),
        "forecast": diagramm_forecast(abschluss, "Umsatz"),
    }


# =============================================================================
# Tabellen (gemeinsam für PowerPoint und PDF)
# =============================================================================


def tabelle_ergebnisrechnung(abschluss: Abschlussergebnis) -> list[list[str]]:
    """Vereinfachte Ergebnisrechnung in T€ (Einheit im Tabellenkopf, nicht in jeder Zelle)."""
    kopf = ["in T€", "AC", "PL", "ΔPL", "ΔPL %", "PY", "ΔPY", "ΔPY %"]
    zeilen = [kopf]

    def teur(cent: int, vorzeichen: bool = False) -> str:
        return format_zahl(Decimal(cent) / 100_000, 1, vorzeichen)

    def pct(delta: int, basis: int) -> str:
        return format_prozent(Decimal(delta) * 100 / abs(Decimal(basis))) if basis else "–"

    for z in abschluss.analyse.ergebnisrechnung:
        zeilen.append([
            z.kennzahl, teur(z.ist), teur(z.plan), teur(z.delta_plan, True), pct(z.delta_plan, z.plan),
            teur(z.vorjahr), teur(z.delta_vorjahr, True), pct(z.delta_vorjahr, z.vorjahr),
        ])
    return zeilen


def tabelle_forecast(abschluss: Abschlussergebnis) -> list[list[str]]:
    zeilen = [["Kennzahl", "Ist YTD", "Prognose", "Jahresende", "PL", "ΔPL %", "Intervall", "Methode", "WAPE"]]
    for name, f in abschluss.forecasts.items():
        i = f.info
        zeilen.append([
            name, format_betrag(i.ist_ytd, "Mio. €"), format_betrag(i.prognose_restmonate, "Mio. €"),
            format_betrag(i.jahresendprognose, "Mio. €"), format_betrag(i.plan_gesamtjahr, "Mio. €"),  # type: ignore[arg-type]
            format_prozent(i.abweichung_plan_pct),  # type: ignore[arg-type]
            f"{format_zahl(i.intervall_unten / 1_000_000, 1)}–{format_betrag(i.intervall_oben, 'Mio. €')}",  # type: ignore[operator, arg-type]
            f.methode.value, format_prozent(i.wape_pct, vorzeichen=False),  # type: ignore[arg-type]
        ])
    return zeilen


def tabelle_pruefprotokoll(abschluss: Abschlussergebnis) -> list[list[str]]:
    return [["Prüfpunkt", "Ampel", "Ergebnis"]] + [
        [p.bezeichnung, p.ampel.value, p.ergebnis] for p in abschluss.protokoll.punkte
    ]


# =============================================================================
# PowerPoint
# =============================================================================


@dataclass(frozen=True)
class _Folienformat:
    breite: float = 13.333
    hoehe: float = 7.5
    rand: float = 0.55
    schrift: str = "Calibri"


def erstelle_pptx(abschluss: Abschlussergebnis, kommentare: Sequence[Kommentar], pfad: Path | str,
                  diagramme: dict[str, Path] | None = None) -> Path:
    """Monatsbericht als PowerPoint (16:9). Enthält nur freigegebene Kommentare."""
    from pptx import Presentation  # noqa: PLC0415
    from pptx.dml.color import RGBColor  # noqa: PLC0415
    from pptx.enum.text import PP_ALIGN  # noqa: PLC0415
    from pptx.util import Inches, Pt  # noqa: PLC0415

    pfad = Path(pfad)
    diagramme = diagramme or erzeuge_diagramme(abschluss, pfad.parent / "diagramme")
    f = _Folienformat()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(f.breite), Inches(f.hoehe)
    leer = prs.slide_layouts[6]
    dunkel, ink, leise = RGBColor(0x26, 0x26, 0x26), RGBColor(0x1A, 0x1A, 0x1A), RGBColor(0x59, 0x59, 0x59)
    periode = abschluss.periodenlabel
    firma = abschluss.faktenblatt.unternehmen
    freigegeben = berichtsfaehige_kommentare(kommentare)
    nummer = [0]

    def text(folie, x, y, w, h, inhalt, groesse=14, fett=False, farbe=ink, ausrichtung=PP_ALIGN.LEFT, name="Text"):
        box = folie.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        box.name = name
        rahmen = box.text_frame
        rahmen.word_wrap = True
        absaetze = inhalt if isinstance(inhalt, list) else [inhalt]
        for i, zeile in enumerate(absaetze):
            p = rahmen.paragraphs[0] if i == 0 else rahmen.add_paragraph()
            p.alignment = ausrichtung
            p.space_after = Pt(6)
            lauf = p.add_run()
            lauf.text = zeile
            lauf.font.size, lauf.font.bold, lauf.font.name = Pt(groesse), fett, f.schrift
            lauf.font.color.rgb = farbe
        return box

    def inhaltsfolie(titel: str, untertitel: str):
        nummer[0] += 1
        folie = prs.slides.add_slide(leer)
        text(folie, f.rand, 0.35, f.breite - 2 * f.rand, 0.7, titel, 24, True, name="Titel")
        text(folie, f.rand, 0.98, f.breite - 2 * f.rand, 0.4, untertitel, 13, farbe=leise, name="Untertitel")
        text(folie, f.rand, f.hoehe - 0.45, 9, 0.3, f"{firma} · Monatsbericht {periode} · synthetische Daten",
             10, farbe=leise, name="Fußzeile")
        text(folie, f.breite - f.rand - 1, f.hoehe - 0.45, 1, 0.3, str(nummer[0] + 1), 10, farbe=leise,
             ausrichtung=PP_ALIGN.RIGHT, name="Foliennummer")
        return folie

    def bild(folie, schluessel: str, x: float, y: float, w: float, h: float):
        """Diagramm (Folienvariante ohne eigenen Titel) maßstabsgetreu in den Rahmen w × h einpassen."""
        from PIL import Image as PILImage  # noqa: PLC0415

        datei = diagramme.get(f"{schluessel}_folie", diagramme[schluessel])
        with PILImage.open(datei) as img:
            verhaeltnis = img.width / img.height
        breite, hoehe = (w, w / verhaeltnis) if w / verhaeltnis <= h else (h * verhaeltnis, h)
        bildobjekt = folie.shapes.add_picture(str(datei), Inches(x), Inches(y), width=Inches(breite),
                                              height=Inches(hoehe))
        bildobjekt.name = f"Diagramm {schluessel}"
        return bildobjekt

    def tabelle(folie, daten: list[list[str]], x, y, w, h, groesse=12, erste_spalte=2.4, links: tuple[int, ...] = (0,),
                spalten: Sequence[float] | None = None):
        form = folie.shapes.add_table(len(daten), len(daten[0]), Inches(x), Inches(y), Inches(w), Inches(h))
        form.name = "Tabelle"
        t = form.table
        rest = (w - erste_spalte) / (len(daten[0]) - 1)
        breiten = list(spalten) if spalten else [erste_spalte] + [rest] * (len(daten[0]) - 1)
        for j, b in enumerate(breiten):
            t.columns[j].width = Inches(b)
        for i, zeile in enumerate(daten):
            for j, wert in enumerate(zeile):
                zelle = t.cell(i, j)
                zelle.fill.background()
                zelle.text = wert
                for p in zelle.text_frame.paragraphs:
                    p.alignment = PP_ALIGN.LEFT if j in links else PP_ALIGN.RIGHT
                    for lauf in p.runs:
                        lauf.font.size, lauf.font.name = Pt(groesse), f.schrift
                        lauf.font.bold = i == 0 or zeile[0] == "EBIT"
                        lauf.font.color.rgb = ink
        return form

    # 1 · Titel
    folie = prs.slides.add_slide(leer)
    folie.background.fill.solid()
    folie.background.fill.fore_color.rgb = dunkel
    weiss = RGBColor(0xFF, 0xFF, 0xFF)
    text(folie, 0.9, 2.3, 11, 1.1, f"Monatsbericht {periode}", 44, True, weiss, name="Titel")
    text(folie, 0.9, 3.45, 11, 0.6, firma, 22, farbe=RGBColor(0xD9, 0xD9, 0xD9), name="Untertitel")
    text(folie, 0.9, 4.3, 11, 1.2, [
        f"Operatives Controlling · erstellt am {datetime.now():%d.%m.%Y}",
        f"Datenqualität: {abschluss.protokoll.ampel.value} · freigegebene Kommentare: {len(freigegeben)}",
    ], 14, farbe=RGBColor(0xBF, 0xBF, 0xBF), name="Hinweise")

    # 2 · Management Summary
    aussagen = kernaussagen(abschluss)
    folie = inhaltsfolie(aussagen[0].rstrip("."), f"Management Summary · {periode} · in T€")
    zeilen = {z.kennzahl: z for z in abschluss.analyse.ergebnisrechnung}
    for i, name in enumerate(("Umsatz", "Deckungsbeitrag", "EBIT")):
        z = zeilen[name]
        x = f.rand + i * 2.55
        text(folie, x, 1.65, 2.4, 0.4, name, 14, farbe=leise, name=f"KPI {name}")
        text(folie, x, 2.0, 2.4, 0.7, format_betrag(Decimal(z.ist) / 100, "T€", stellen=0), 26, True,
             name=f"KPI-Wert {name}")
        pct_pl = Decimal(z.delta_plan) * 100 / abs(Decimal(z.plan))
        pct_py = Decimal(z.delta_vorjahr) * 100 / abs(Decimal(z.vorjahr))
        text(folie, x, 2.7, 2.4, 0.7, [f"ΔPL {format_prozent(pct_pl)}", f"ΔPY {format_prozent(pct_py)}"], 13,
             farbe=leise, name=f"KPI-Abweichung {name}")
    text(folie, 8.4, 1.65, 4.4, 4.9, ["Kernaussagen", *aussagen[1:]], 14, name="Kernaussagen")
    tabelle(folie, tabelle_ergebnisrechnung(abschluss), f.rand, 3.75, 7.6, 2.6, 11, 2.2)

    # 3 · EBIT-Brücke
    ebit = abschluss.analyse.abweichung("EBIT")
    belastend = [t.effektart.value for t in sorted(ebit.treiber, key=lambda t: t.betrag) if t.betrag < 0][:2]
    zusatz = f" – größte Belastungen: {' und '.join(belastend)}" if belastend else ""
    folie = inhaltsfolie(f"EBIT {format_wert(ebit.delta_abs, ebit.einheit, vorzeichen=True)} ggü. Plan{zusatz}",  # type: ignore[arg-type]
                         f"Brücke Plan → Ist · {periode} · in T€")
    bild(folie, "ebit_bruecke", f.rand, 1.55, 12.2, 5.25)

    # 4 · Umsatz
    ums = abschluss.analyse.abweichung("UMS")
    folie = inhaltsfolie(f"Umsatz {format_wert(ums.delta_abs, ums.einheit, vorzeichen=True)} ggü. Plan – "  # type: ignore[arg-type]
                         "gegenläufige Effekte je Region und Produktgruppe", f"ΔPL je Region × Produktgruppe · {periode}")
    bild(folie, "umsatz_abweichungen", f.rand, 1.55, 8.2, 5.25)
    text(folie, 9.0, 1.6, 3.8, 4.8, ["Zerlegung Umsatz Gesamt"] + [
        f"{t.effektart.value}: {format_wert(t.betrag, ums.einheit, vorzeichen=True)}" for t in ums.treiber
    ], 14, name="Zerlegung")

    # 5 · Kostenstellen
    kst = abschluss.analyse.abweichung("KST")
    folie = inhaltsfolie(f"Kostenstellen {format_wert(kst.delta_abs, kst.einheit, vorzeichen=True)} über "  # type: ignore[arg-type]
                         "verrechneten Plankosten", f"Flexible Plankostenrechnung · {periode}")
    bild(folie, "kostenstellen", f.rand, 1.55, 8.2, 5.25)
    text(folie, 9.0, 1.6, 3.8, 4.8, ["Abweichungsarten"] + [
        f"{t.effektart.value}: {format_wert(t.betrag, kst.einheit, vorzeichen=True)}" for t in kst.treiber
    ], 14, name="Abweichungsarten")

    # 6 · Forecast
    fu = abschluss.forecasts["Umsatz"].info
    folie = inhaltsfolie(f"Jahresendprognose Umsatz {format_betrag(fu.jahresendprognose)} "  # type: ignore[arg-type]
                         f"({format_prozent(fu.abweichung_plan_pct)} ggü. Plan)",  # type: ignore[arg-type]
                         f"Rolling Forecast {abschluss.einstellungen.geschaeftsjahr}")
    bild(folie, "forecast", f.rand, 1.45, 12.2, 3.55)
    tabelle(folie, tabelle_forecast(abschluss), f.rand, 5.15, 12.2, 1.5, 11, 1.8)

    # 7 · Kommentare (nur freigegebene)
    folie = inhaltsfolie("Freigegebene Management-Kommentare",
                         "Nur namentlich freigegebene Kommentare (Human-in-the-Loop) · KI-Entwürfe sind ausgeschlossen")
    if freigegeben:
        y = 1.6
        for k in freigegeben[:5]:
            text(folie, f.rand, y, 12.2, 0.95, [
                k.text,
                f"{k.objekt_id} · {k.quellenangabe} · freigegeben von {k.freigegeben_von} am "
                f"{k.freigegeben_am:%d.%m.%Y %H:%M}",  # type: ignore[union-attr]
            ], 13, name=f"Kommentar {k.objekt_id}")
            y += 1.0
        if len(freigegeben) > 5:
            text(folie, f.rand, y, 12.2, 0.4, f"… sowie {len(freigegeben) - 5} weitere im PDF-Bericht.", 12,
                 farbe=leise)
    else:
        text(folie, f.rand, 1.8, 12.2, 1, "Für diese Periode wurden noch keine Kommentare freigegeben.", 16,
             farbe=leise, name="Hinweis")

    # 8 · Anhang Datenprüfung
    folie = inhaltsfolie(f"Datenprüfung: Gesamtampel {abschluss.protokoll.ampel.value}",
                         "Anhang · Prüfprotokoll des Monatsabschlusses")
    tabelle(folie, tabelle_pruefprotokoll(abschluss), f.rand, 1.55, 12.2, 4.6, 11, links=(0, 1, 2), spalten=(3.8, 1.2, 7.2))

    pfad.parent.mkdir(parents=True, exist_ok=True)
    prs.save(pfad)
    logger.info("PowerPoint-Bericht erstellt: %s (%d freigegebene Kommentare).", pfad, len(freigegeben))
    return pfad


# =============================================================================
# PDF
# =============================================================================


def _schriften() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415

    ordner = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(ordner / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(ordner / "DejaVuSans-Bold.ttf")))
    return "DejaVu", "DejaVu-Bold"


def erstelle_pdf(abschluss: Abschlussergebnis, kommentare: Sequence[Kommentar], pfad: Path | str,
                 diagramme: dict[str, Path] | None = None) -> Path:
    """Monatsbericht als PDF (A4 quer). Enthält nur freigegebene Kommentare."""
    from reportlab.lib import colors  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4, landscape  # noqa: PLC0415
    from reportlab.lib.styles import ParagraphStyle  # noqa: PLC0415
    from reportlab.lib.units import cm  # noqa: PLC0415
    from reportlab.platypus import (  # noqa: PLC0415
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    diagramme = diagramme or erzeuge_diagramme(abschluss, pfad.parent / "diagramme")
    normal, fett = _schriften()
    stil_titel = ParagraphStyle("Titel", fontName=fett, fontSize=20, leading=24, textColor=colors.HexColor(TEXT))
    stil_h = ParagraphStyle("H", fontName=fett, fontSize=13, leading=17, spaceBefore=6, spaceAfter=4,
                            textColor=colors.HexColor(TEXT))
    stil = ParagraphStyle("Text", fontName=normal, fontSize=9.5, leading=13, textColor=colors.HexColor(TEXT))
    stil_leise = ParagraphStyle("Leise", parent=stil, textColor=colors.HexColor(TEXT_LEISE), fontSize=8.5)
    periode = abschluss.periodenlabel
    firma = abschluss.faktenblatt.unternehmen
    freigegeben = berichtsfaehige_kommentare(kommentare)
    breite = landscape(A4)[0] - 3 * cm

    def tab(daten: list[list[str]], spalten: list[float] | None = None, textspalten: bool = False) -> Table:
        t = Table(daten, colWidths=spalten, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), normal, 8.5),
            ("FONT", (0, 0), (-1, 0), fett, 8.5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(AC)),
            ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor(LINIE)),
            ("ALIGN", (1, 0), (-1, -1), "LEFT" if textspalten else "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return t

    def bild(schluessel: str, max_hoehe: float) -> Image:
        img = Image(str(diagramme[schluessel]))
        faktor = min(breite / img.imageWidth, max_hoehe / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * faktor, img.imageHeight * faktor
        return img

    def fusszeile(canvas, doc) -> None:  # noqa: ANN001
        canvas.saveState()
        canvas.setFont(normal, 7.5)
        canvas.setFillColor(colors.HexColor(TEXT_LEISE))
        canvas.drawString(1.5 * cm, 0.9 * cm, f"{firma} · Monatsbericht {periode} · synthetische Daten · VarianceAI")
        canvas.drawRightString(landscape(A4)[0] - 1.5 * cm, 0.9 * cm, f"Seite {doc.page}")
        canvas.restoreState()

    inhalt = [
        Paragraph(f"Monatsbericht {periode}", stil_titel), Spacer(1, 4),
        Paragraph(f"{firma} · Operatives Controlling · erstellt am {datetime.now():%d.%m.%Y} · "
                  f"Datenqualität: {abschluss.protokoll.ampel.value}", stil_leise),
        Spacer(1, 10), Paragraph("Management Summary", stil_h),
        *[Paragraph(f"• {a}", stil) for a in kernaussagen(abschluss)],
        Spacer(1, 8), tab(tabelle_ergebnisrechnung(abschluss)), Spacer(1, 10),
        bild("kpi", 6.8 * cm),
        PageBreak(), bild("ebit_bruecke", 11 * cm),
        PageBreak(), bild("umsatz_abweichungen", 15 * cm),
        PageBreak(), bild("kostenstellen", 7 * cm), Spacer(1, 10), bild("forecast", 7.5 * cm),
        Spacer(1, 6), tab(tabelle_forecast(abschluss)),
        PageBreak(), Paragraph("Freigegebene Management-Kommentare", stil_h),
        Paragraph("Nur namentlich freigegebene Kommentare (Human-in-the-Loop). KI-Entwürfe sind ausgeschlossen.",
                  stil_leise), Spacer(1, 6),
    ]
    if freigegeben:
        for k in freigegeben:
            inhalt += [Paragraph(k.text, stil),
                       Paragraph(f"{k.objekt_id} · {k.quellenangabe} · freigegeben von {k.freigegeben_von} am "
                                 f"{k.freigegeben_am:%d.%m.%Y %H:%M}", stil_leise), Spacer(1, 6)]  # type: ignore[union-attr]
    else:
        inhalt.append(Paragraph("Für diese Periode wurden noch keine Kommentare freigegeben.", stil))
    inhalt += [Spacer(1, 10), Paragraph("Anhang: Prüfprotokoll", stil_h),
               tab([[Paragraph(c, stil) if i and j == 2 else c for j, c in enumerate(z)]
                    for i, z in enumerate(tabelle_pruefprotokoll(abschluss))], [7.8 * cm, 1.6 * cm, breite - 9.4 * cm], textspalten=True)]
    doc = SimpleDocTemplate(str(pfad), pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.3 * cm, bottomMargin=1.5 * cm, title=f"Monatsbericht {periode}",
                            author="VarianceAI", subject=firma)
    doc.build(inhalt, onFirstPage=fusszeile, onLaterPages=fusszeile)
    logger.info("PDF-Bericht erstellt: %s (%d freigegebene Kommentare).", pfad, len(freigegeben))
    return pfad


def erstelle_monatsbericht(abschluss: Abschlussergebnis, kommentare: Sequence[Kommentar],
                           ordner: Path | str) -> dict[str, Path]:
    """Erzeugt Diagramme, PowerPoint und PDF in einem Ordner."""
    ordner = Path(ordner)
    diagramme = erzeuge_diagramme(abschluss, ordner / "diagramme")
    kuerzel = f"{abschluss.einstellungen.geschaeftsjahr}-{abschluss.einstellungen.periode:02d}"
    return {
        "pptx": erstelle_pptx(abschluss, kommentare, ordner / f"Monatsbericht_{kuerzel}.pptx", diagramme),
        "pdf": erstelle_pdf(abschluss, kommentare, ordner / f"Monatsbericht_{kuerzel}.pdf", diagramme),
        **{f"diagramm_{k}": v for k, v in diagramme.items()},
    }
