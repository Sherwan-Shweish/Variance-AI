"""Interaktive IBCS-Diagramme für das Cockpit (Altair/Vega-Lite, dunkles Thema).

Die Berichtsdiagramme für PowerPoint/PDF bleiben in :mod:`src.reporting_ibcs`
(Druck, helle Fläche). Hier gilt dieselbe Notation, angepasst an die dunkle
Fläche des Cockpits:

* AC hell gefüllt · PY grau · PL umrandet (ohne Füllung) · FC gestrichelt umrandet
  und halbtransparent (Schraffur ist in Vega-Lite nicht verfügbar).
* Abweichungen günstig/ungünstig (validierte Farben aus :data:`FARBEN`) und immer
  mit Vorzeichen beschriftet.
* Keine Doppelachsen, Säulen immer ab 0 – ist eine Abweichung klein gegenüber der
  Basis, wird automatisch eine Abweichungsbrücke (Basis 0) gezeichnet, statt die
  Achse abzuschneiden.
* Beschriftungen werden in Python deutsch formatiert (``−1.234,5``); Tooltips
  zeigen dieselben Texte.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import altair as alt
import pandas as pd

from src.agent import Abweichung, Kennzahlart, Vergleichsart, format_zahl
from src.forecast_engine import monatsreihe
from src.pipeline import Abschlussergebnis
from src.ui_design import FARBEN, rgba

MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
_KURZ = {
    "Mengeneffekt": "Menge", "Preiseffekt": "Preis", "Mixeffekt": "Mix", "Sortimentseffekt": "Sorti-|ment",
    "Preisabweichung": "Preis-|abw.", "Verbrauchsabweichung": "Ver-|brauch",
    "Beschäftigungsabweichung": "Beschäf-|tigung", "Einmaleffekt": "Einmal-|effekt",
    "Plankostenanpassung": "Plan-|kosten", "Sonstige Abweichung": "Sonstige",
}
_KURZ_KPI = {"Mengeneffekt": "Menge", "Preiseffekt": "Preis", "Mixeffekt": "Mix", "Sortimentseffekt": "Sortiment",
             "Preisabweichung": "Preisabw.", "Verbrauchsabweichung": "Verbrauchsabw.",
             "Beschäftigungsabweichung": "Beschäftigung", "Plankostenanpassung": "Plankosten",
             "Sonstige Abweichung": "Sonstige"}


def kurz_effekt(effektart: str) -> str:
    """Kurzform einer Effektart für schmale Kennzahlenkarten (z. B. „Sortiment“ statt „Sortimentseffekt“)."""
    return _KURZ_KPI.get(effektart, effektart)


BASISKUERZEL = {Vergleichsart.PLAN_IST: "PL", Vergleichsart.IST_VJ: "PY", Vergleichsart.IST_FC: "FC"}
SCHWELLE_ABWEICHUNGSBRUECKE = 0.12


def basis_kuerzel(abw: Abweichung) -> str:
    """IBCS-Kürzel der Vergleichsbasis; Kostenstellen: verrechnete Plankosten (flexible Plankostenrechnung)."""
    if abw.art is Kennzahlart.KOSTEN and abw.vergleich is Vergleichsart.PLAN_IST:
        return "PL verr."
    return BASISKUERZEL[abw.vergleich]


def _t(wert: float, stellen: int = 1, vorzeichen: bool = False) -> str:
    return format_zahl(Decimal(str(round(wert, 6))), stellen, vorzeichen)


def _guenstig(betrag: float, art: Kennzahlart) -> bool:
    return (betrag > 0) != (art is Kennzahlart.KOSTEN)


def _thema(chart: alt.TopLevelMixin, hoehe: int) -> alt.TopLevelMixin:
    f = FARBEN
    return (
        chart.properties(height=hoehe, background="transparent", padding={"left": 4, "right": 8, "top": 12, "bottom": 4})
        .configure_view(stroke=None)
        .configure_axis(labelColor=f["text_leise"], titleColor=f["text_leise"], gridColor=f["raster"],
                        domainColor=f["linie_stark"], tickColor=f["linie_stark"], labelFont="Inter", titleFont="Inter",
                        labelFontSize=11, titleFontSize=11, titleFontWeight=500, labelPadding=6)
        .configure_text(font="Inter", fontSize=11)
        .configure_legend(labelColor=f["text_2"], titleColor=f["text_leise"], labelFont="Inter", titleFont="Inter",
                          orient="top", direction="horizontal", symbolSize=90, labelFontSize=11)
    )


# =============================================================================
# Brücke (Wasserfall) Basis → Ist
# =============================================================================


def brueckendaten(abw: Abweichung, nur_abweichung: bool | None = None) -> tuple[pd.DataFrame, bool]:
    """Daten der Brücke in T€; Summe der Effekte = Ist − Basis (Invariante der Engine)."""
    basis_name = basis_kuerzel(abw)
    basis, ist = float(abw.vergleichswert) / 1000, float(abw.ist) / 1000
    treiber = [t for t in abw.treiber if t.betrag != 0]
    groesster = max((abs(float(t.betrag)) / 1000 for t in treiber), default=0.0)
    if nur_abweichung is None:
        nur_abweichung = groesster < SCHWELLE_ABWEICHUNGSBRUECKE * max(abs(basis), abs(ist), 1e-9)
    zeilen: list[dict] = []
    stand = 0.0 if nur_abweichung else basis
    if not nur_abweichung:
        zeilen.append({"schritt": basis_name, "von": 0.0, "bis": basis, "art": "basis", "label": _t(basis),
                       "tooltip": f"{basis_name}: {_t(basis)} T€"})
    for t in treiber:
        d = float(t.betrag) / 1000
        anteil = f" · {format_zahl(t.anteil_pct, 1, False)} % der Abweichung" if t.anteil_pct is not None else ""
        zeilen.append({"schritt": _KURZ.get(t.effektart.value, t.effektart.value), "von": stand, "bis": stand + d,
                       "art": "gut" if _guenstig(d, abw.art) else "schlecht", "label": _t(d, 1, True),
                       "tooltip": f"{t.effektart.value}: {_t(d, 1, True)} T€{anteil}"})
        stand += d
    if nur_abweichung:
        gesamt = ist - basis
        zeilen.append({"schritt": f"Δ{basis_name}", "von": 0.0, "bis": gesamt,
                       "art": "gut" if _guenstig(gesamt, abw.art) else "schlecht", "label": _t(gesamt, 1, True),
                       "tooltip": f"Abweichung gesamt: {_t(gesamt, 1, True)} T€"})
    else:
        zeilen.append({"schritt": "AC", "von": 0.0, "bis": ist, "art": "ac", "label": _t(ist),
                       "tooltip": f"AC: {_t(ist)} T€"})
    df = pd.DataFrame(zeilen)
    df["oben"] = df[["von", "bis"]].max(axis=1)
    df["unten"] = df[["von", "bis"]].min(axis=1)
    df["steigt"] = (df["bis"] >= df["von"]) & (df["art"] != "basis") | df["art"].isin(["basis", "ac"]) & (df["bis"] >= 0)
    return df, nur_abweichung


def bruecke(abw: Abweichung, hoehe: int = 320, nur_abweichung: bool | None = None) -> alt.TopLevelMixin:
    df, _ = brueckendaten(abw, nur_abweichung)
    reihenfolge = list(df["schritt"])
    f = FARBEN
    x = alt.X("schritt:N", sort=reihenfolge, title=None,
              axis=alt.Axis(labelAngle=0, labelLimit=130, ticks=False, domain=False, labelLineHeight=13,
                            labelExpr="split(datum.label, '|')"))
    farbe = alt.Fill("art:N", legend=None, scale=alt.Scale(
        domain=["basis", "ac", "gut", "schlecht"], range=[f["karte"], f["ac"], f["gut"], f["schlecht"]]))
    rand = alt.Stroke("art:N", legend=None, scale=alt.Scale(
        domain=["basis", "ac", "gut", "schlecht"], range=[f["pl"], f["ac"], f["gut"], f["schlecht"]]))
    spanne = max(float(df["oben"].max()), 0.0) - min(float(df["unten"].min()), 0.0) or 1.0
    y = alt.Y("von:Q", axis=None, scale=alt.Scale(domain=[min(float(df["unten"].min()), 0.0) - spanne * 0.08,
                                                         max(float(df["oben"].max()), 0.0) + spanne * 0.1]))
    balken = alt.Chart(df).mark_bar(size=34, strokeWidth=1.3, cornerRadius=2).encode(
        x=x, y=y, y2="bis:Q", fill=farbe, stroke=rand, tooltip=alt.Tooltip("tooltip:N", title=None))
    # Alle Ebenen ohne y-Achse: gemischte Achsdefinitionen in Layern bricht Vega-Lite ab.
    oben = alt.Chart(df).transform_filter("datum.steigt").mark_text(dy=-9, color=f["text"], fontWeight=600).encode(
        x=x, y=alt.Y("oben:Q", axis=None), text="label:N")
    unten = alt.Chart(df).transform_filter("!datum.steigt").mark_text(dy=11, color=f["text"], fontWeight=600).encode(
        x=x, y=alt.Y("unten:Q", axis=None), text="label:N")
    null = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(color=f["linie_stark"], strokeWidth=1).encode(
        y=alt.Y("y:Q", axis=None))
    return _thema(alt.layer(null, balken, oben, unten), hoehe)


# =============================================================================
# Abweichungen der Unterebene (horizontale Balken)
# =============================================================================


def kurzname(bezeichnung: str) -> str:
    for praefix in ("Kostenstelle ", "Umsatz ", "Deckungsbeitrag "):
        if bezeichnung.startswith(praefix):
            bezeichnung = bezeichnung[len(praefix):]
    return bezeichnung if len(bezeichnung) <= 38 else bezeichnung[:37] + "…"


def abweichungsbalken(objekte: Sequence[Abweichung], max_anzahl: int = 12) -> alt.TopLevelMixin:
    auswahl = sorted(objekte, key=lambda a: -abs(a.delta_abs or 0))[:max_anzahl]
    df = pd.DataFrame([{
        "name": kurzname(a.bezeichnung), "delta": float(a.delta_abs or 0) / 1000,
        "art": "gut" if _guenstig(float(a.delta_abs or 0), a.art) else "schlecht",
        "label": _t(float(a.delta_abs or 0) / 1000, 1, True),
        "tooltip": f"{a.bezeichnung}: {_t(float(a.delta_abs or 0) / 1000, 1, True)} T€"
                   + (f" ({format_zahl(a.delta_pct, 1, True)} %)" if a.delta_pct is not None else ""),
    } for a in auswahl])
    f = FARBEN
    spanne = max(df["delta"].abs().max(), 1e-9)
    links, rechts = min(df["delta"].min(), 0.0), max(df["delta"].max(), 0.0)
    y = alt.Y("name:N", sort=list(df["name"]), title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=240))
    x = alt.X("delta:Q", axis=None, scale=alt.Scale(domain=[links - spanne * 0.28 * (links < 0), rechts + spanne * 0.28]))
    balken = alt.Chart(df).mark_bar(size=14, cornerRadius=2).encode(
        x=x, y=y, color=alt.Color("art:N", legend=None, scale=alt.Scale(domain=["gut", "schlecht"],
                                                                         range=[f["gut"], f["schlecht"]])),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    pos = alt.Chart(df).transform_filter("datum.delta >= 0").mark_text(align="left", dx=6, color=f["text"]).encode(
        x=alt.X("delta:Q", axis=None), y=y, text="label:N")
    neg = alt.Chart(df).transform_filter("datum.delta < 0").mark_text(align="right", dx=-6, color=f["text"]).encode(
        x=alt.X("delta:Q", axis=None), y=y, text="label:N")
    null = alt.Chart(pd.DataFrame({"x": [0.0]})).mark_rule(color=f["linie_stark"]).encode(x=alt.X("x:Q", axis=None))
    return _thema(alt.layer(balken, pos, neg, null), max(140, 30 * len(df) + 30))


# =============================================================================
# Monatsverlauf und Forecast
# =============================================================================


def monatsdaten(abschluss: Abschlussergebnis, kennzahl: str) -> pd.DataFrame:
    """Monatswerte des Geschäftsjahres in T€ (AC bis Berichtsperiode, PL, PY; EBIT = DB − Kosten)."""
    e = abschluss.einstellungen

    def reihe(name: str, version: str = "IST") -> pd.Series:
        if name == "EBIT":
            return reihe("Deckungsbeitrag", version).sub(reihe("Kosten", version), fill_value=0)
        return monatsreihe(abschluss.daten, name, version)  # type: ignore[arg-type]

    ist, plan = reihe(kennzahl), reihe(kennzahl, "PLAN")
    zeilen = []
    for m in range(1, 13):
        periode = pd.Period(year=e.geschaeftsjahr, month=m, freq="M")
        vorjahr = pd.Period(year=e.geschaeftsjahr - 1, month=m, freq="M")
        zeilen.append({
            "monat": MONATE[m - 1], "m": m,
            "ac": float(ist.get(periode, 0)) / 100_000 if m <= e.periode else None,
            "pl": float(plan.get(periode, 0)) / 100_000 if periode in plan.index else None,
            "py": float(ist.get(vorjahr, 0)) / 100_000 if vorjahr in ist.index else None,
        })
    return pd.DataFrame(zeilen)


def _achse_teur() -> alt.Axis:
    """Achse in T€ ohne Nachkommastellen, deutscher Tausenderpunkt."""
    return alt.Axis(title=None, tickCount=5, domain=False, ticks=False,
                    labelExpr="replace(format(datum.value, ',.0f'), ',', '.')")


def monatsverlauf(abschluss: Abschlussergebnis, kennzahl: str, hoehe: int = 260) -> alt.TopLevelMixin:
    """AC gefüllt und PL umrandet je Monat (IBCS-Säulen), PY als graue Marke."""
    df = monatsdaten(abschluss, kennzahl)
    lang = df.melt(id_vars=["monat", "m"], value_vars=["pl", "ac"], var_name="szenario", value_name="wert").dropna()
    lang["szenario"] = lang["szenario"].str.upper()
    lang["tooltip"] = lang.apply(lambda r: f"{r['monat']} · {r['szenario']}: {_t(r['wert'])} T€", axis=1)
    f = FARBEN
    x = alt.X("monat:N", sort=MONATE, title=None, axis=alt.Axis(labelAngle=0, ticks=False, domain=False))
    saeulen = alt.Chart(lang).mark_bar(width=11, strokeWidth=1.2, cornerRadius=1.5).encode(
        x=x, xOffset=alt.XOffset("szenario:N", sort=["PL", "AC"]), y=alt.Y("wert:Q", axis=_achse_teur()),
        fill=alt.Fill("szenario:N", scale=alt.Scale(domain=["AC", "PL"], range=[f["ac"], f["karte"]]),
                      legend=alt.Legend(title=None)),
        stroke=alt.Stroke("szenario:N", scale=alt.Scale(domain=["AC", "PL"], range=[f["ac"], f["pl"]]), legend=None),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    py = df.dropna(subset=["py"]).assign(tooltip=lambda d: d.apply(
        lambda r: f"{r['monat']} · PY: {_t(r['py'])} T€", axis=1))
    marken = alt.Chart(py).mark_tick(color=f["py"], thickness=2, size=26).encode(
        x=x, y="py:Q", tooltip=alt.Tooltip("tooltip:N", title=None))
    return _thema(alt.layer(saeulen, marken), hoehe)


def forecastverlauf(abschluss: Abschlussergebnis, kennzahl: str, hoehe: int = 300) -> alt.TopLevelMixin:
    """AC bis Berichtsmonat, FC (Restmonate) gestrichelt mit Intervall, PL umrandet."""
    df = monatsdaten(abschluss, kennzahl)
    fc = abschluss.forecasts[kennzahl]
    for m in fc.monate:
        df.loc[df["m"] == m.periode, ["fc", "unten", "oben"]] = (m.wert_cent / 100_000, m.unten_cent / 100_000,
                                                                  m.oben_cent / 100_000)
    if "fc" not in df:
        df["fc"], df["unten"], df["oben"] = None, None, None
    df["ist_fc"] = df["ac"].fillna(df["fc"])
    df["szenario"] = df["ac"].notna().map({True: "AC", False: "FC"})
    df["tooltip"] = df.apply(lambda r: f"{r['monat']} · {r['szenario']}: {_t(r['ist_fc'])} T€"
                             + (f" (Intervall {_t(r['unten'])}–{_t(r['oben'])})" if r["szenario"] == "FC" else "")
                             + (f" · PL {_t(r['pl'])}" if pd.notna(r["pl"]) else ""), axis=1)
    f = FARBEN
    x = alt.X("monat:N", sort=MONATE, title=None, axis=alt.Axis(labelAngle=0, ticks=False, domain=False))
    plan = df.dropna(subset=["pl"]).assign(szenario="PL")
    pl = alt.Chart(plan).mark_bar(width=11, fill=f["karte"], stroke=f["pl"], strokeWidth=1.2, xOffset=-7).encode(
        x=x, y=alt.Y("pl:Q", axis=_achse_teur()), tooltip=alt.Tooltip("tooltip:N", title=None))
    werte = df.dropna(subset=["ist_fc"])
    saeulen = alt.Chart(werte).mark_bar(width=11, xOffset=7, strokeWidth=1.2).encode(
        x=x, y="ist_fc:Q",
        fill=alt.Fill("szenario:N", scale=alt.Scale(domain=["AC", "FC", "PL"],
                                                    range=[f["ac"], rgba(f["ac"], 0.22), f["karte"]]),
                      legend=alt.Legend(title=None, values=["AC", "FC", "PL"])),
        stroke=alt.Stroke("szenario:N", scale=alt.Scale(domain=["AC", "FC", "PL"], range=[f["ac"], f["ac"], f["pl"]]),
                          legend=None),
        strokeDash=alt.StrokeDash("szenario:N", scale=alt.Scale(domain=["AC", "FC"], range=[[1, 0], [4, 3]]),
                                  legend=None),
        tooltip=alt.Tooltip("tooltip:N", title=None))
    intervall = alt.Chart(df.dropna(subset=["unten"])).mark_rule(color=f["text_2"], strokeWidth=1.4, xOffset=7).encode(
        x=x, y="unten:Q", y2="oben:Q", tooltip=alt.Tooltip("tooltip:N", title=None))
    return _thema(alt.layer(pl, saeulen, intervall), hoehe)
