"""Excel-Bericht mit Formel-Gegenprüfung und Power-BI-Paket.

**Excel (Formel-Gegenprüfung):** Eingabewerte aus Python stehen blau, alle
Ableitungen sind echte Excel-Formeln (schwarz). Je Zeile vergleicht eine
Prüfspalte das Excel-Ergebnis mit dem Python-Ergebnis; das Blatt
„Gegenprüfung“ fasst alle Prüfungen mit Gesamtstatus zusammen. Ändert jemand
einen Eingabewert, rechnet die Mappe weiter – und die Prüfspalten zeigen, wo
Excel und Python nicht mehr übereinstimmen.

**Power BI:** Sternschema (Faktentabellen Buchungen, Leistung, Forecast;
Dimensionen Kalender, Kostenart, Kostenstelle, Material, Kunde, Region) im
deutschen CSV-Format (Semikolon, Dezimalkomma, Datum TT.MM.JJJJ, UTF-8 mit
BOM), dazu DAX-Measures, ein Power-Query-Skript und eine Modellbeschreibung.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from copy import copy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.agent import Effektart, Vergleichsart
from src.forecast_engine import monatsreihe

if TYPE_CHECKING:
    from src.pipeline import Abschlussergebnis

logger = logging.getLogger("varianceai.exporter")

# =============================================================================
# Excel
# =============================================================================

SCHRIFT = "Arial"
EUR = '#,##0.00 "€";-#,##0.00 "€";"–"'
PROZENT = '0.0%;-0.0%;"–"'
ZAHL = '#,##0;-#,##0;"–"'
TOLERANZ = 0.0101  # ≤ 1 Cent: Python rundet additiv (Largest Remainder), Excel rechnet ungerundet

_EINGABE = Font(name=SCHRIFT, size=10, color="0000FF")
_FORMEL = Font(name=SCHRIFT, size=10, color="000000")
_KOPF = Font(name=SCHRIFT, size=10, bold=True)
_TITEL = Font(name=SCHRIFT, size=14, bold=True)
_LEISE = Font(name=SCHRIFT, size=9, color="595959")
_LINIE = Border(bottom=Side(style="thin", color="333333"))
_OK = PatternFill("solid", fgColor="DCEBDD")
_FEHLER = PatternFill("solid", fgColor="F9D3CC")
EFFEKTSPALTEN: tuple[Effektart, ...] = tuple(Effektart)


def _euro(cent: int | Decimal) -> float:
    return float(Decimal(cent) / 100) if isinstance(cent, int) else float(cent)


def _kopfzeile(ws: Worksheet, zeile: int, titel: list[str], breiten: list[int] | None = None) -> None:
    for spalte, text in enumerate(titel, start=1):
        zelle = ws.cell(zeile, spalte, text)
        zelle.font, zelle.border = _KOPF, _LINIE
        zelle.alignment = Alignment(wrap_text=True, vertical="bottom")
    for spalte, breite in enumerate(breiten or [], start=1):
        ws.column_dimensions[get_column_letter(spalte)].width = breite


def _setze(ws: Worksheet, zeile: int, spalte: int, wert: Any, format_: str | None = None,
           eingabe: bool | None = None) -> None:
    zelle = ws.cell(zeile, spalte, wert)
    ist_formel = isinstance(wert, str) and wert.startswith("=")
    zelle.font = _FORMEL if (ist_formel if eingabe is None else not eingabe) else _EINGABE
    if format_:
        zelle.number_format = format_


def _status_formatierung(ws: Worksheet, bereich: str) -> None:
    ws.conditional_formatting.add(bereich, CellIsRule(operator="equal", formula=['"OK"'], fill=_OK))
    ws.conditional_formatting.add(bereich, CellIsRule(operator="equal", formula=['"FEHLER"'], fill=_FEHLER))


def _blatt_hinweise(wb: Workbook, abschluss: Abschlussergebnis) -> None:
    ws = wb.active
    ws.title = "Hinweise"
    ws["A1"], ws["A1"].font = f"VarianceAI – Monatsabschluss {abschluss.periodenlabel}", _TITEL
    zeilen = [
        f"Unternehmen: {abschluss.faktenblatt.unternehmen} (fiktiv, synthetische Daten)",
        f"Datenqualität laut Abschlussprüfung: {abschluss.protokoll.ampel.value}",
        "",
        "Legende",
        "Blaue Zahlen: Eingabewerte aus der Python-Analyse (Quelle: VarianceAI-Engines, Seed und Periode siehe oben).",
        "Schwarze Zahlen: Excel-Formeln, die die Python-Ergebnisse unabhängig nachrechnen.",
        "Prüfspalten: „OK“, wenn Excel und Python übereinstimmen (Toleranz 1 Cent wegen centgenauer Rundung in Python).",
        "Ergebniswerte sind vorberechnet gespeichert; Excel rechnet beim Öffnen neu.",
        "",
        "Blätter",
        "Ergebnisrechnung – Umsatz, Herstellkosten, Deckungsbeitrag, Kostenstellenkosten, EBIT (AC, PL, PY).",
        "Abweichungen – Abweichungen mit Treiberzerlegung; Summe der Treiber muss der Gesamtabweichung entsprechen.",
        "Kostenstellen – flexible Plankostenrechnung je Kostenart, vollständig als Excel-Formeln.",
        "Forecast – Monatsprognose, Jahresendprognose und Szenarien.",
        "Gegenprüfung – Zusammenfassung aller Prüfungen mit Gesamtstatus.",
    ]
    for i, text in enumerate(zeilen, start=3):
        ws.cell(i, 1, text).font = _KOPF if text in ("Legende", "Blätter") else Font(name=SCHRIFT, size=10)
    ws.column_dimensions["A"].width = 120


def _blatt_ergebnisrechnung(wb: Workbook, abschluss: Abschlussergebnis) -> None:
    ws = wb.create_sheet("Ergebnisrechnung")
    ws["A1"], ws["A1"].font = f"Vereinfachte Ergebnisrechnung {abschluss.periodenlabel} (in €)", _TITEL
    _kopfzeile(ws, 3, ["Kennzahl", "AC", "PL", "PY", "ΔPL", "ΔPL %", "ΔPY", "ΔPY %", "Python AC", "Prüfung"],
               [30, 16, 16, 16, 16, 10, 16, 10, 16, 11])
    z = {e.kennzahl: e for e in abschluss.analyse.ergebnisrechnung}
    zeilen = [
        ("Umsatz", z["Umsatz"], None),
        ("Herstellkosten (Standard)", z["Herstellkosten (Standard)"], None),
        ("Deckungsbeitrag", z["Deckungsbeitrag"], "={c}4-{c}5"),
        ("Kosten Kostenstellen", z["Kosten Kostenstellen"], None),
        ("EBIT", z["EBIT"], "={c}6-{c}7"),
    ]
    for i, (name, e, formel) in enumerate(zeilen, start=4):
        ws.cell(i, 1, name).font = _KOPF if name == "EBIT" else _FORMEL
        for spalte, wert in ((2, e.ist), (3, e.plan), (4, e.vorjahr)):
            buchstabe = get_column_letter(spalte)
            _setze(ws, i, spalte, formel.format(c=buchstabe) if formel else _euro(wert), EUR)
        _setze(ws, i, 5, f"=B{i}-C{i}", EUR)
        _setze(ws, i, 6, f'=IF(C{i}=0,"",E{i}/ABS(C{i}))', PROZENT)
        _setze(ws, i, 7, f"=B{i}-D{i}", EUR)
        _setze(ws, i, 8, f'=IF(D{i}=0,"",G{i}/ABS(D{i}))', PROZENT)
        _setze(ws, i, 9, _euro(e.ist), EUR, eingabe=True)
        _setze(ws, i, 10, f'=IF(ABS(B{i}-I{i})<={TOLERANZ},"OK","FEHLER")')
    _status_formatierung(ws, "J4:J8")
    ws.cell(10, 1, "Deckungsbeitrag und EBIT werden in Excel aus ihren Bestandteilen berechnet und gegen Python geprüft.").font = _LEISE
    ws.freeze_panes = "B4"


def _blatt_abweichungen(wb: Workbook, abschluss: Abschlussergebnis) -> int:
    ws = wb.create_sheet("Abweichungen")
    ws["A1"], ws["A1"].font = f"Abweichungen mit Treiberzerlegung {abschluss.periodenlabel} (in €)", _TITEL
    titel = ["ID", "Bezeichnung", "Ebene", "Vergleich", "AC", "Vergleichswert", "Δ", "Δ %", "Bewertung",
             *[e.value for e in EFFEKTSPALTEN], "Summe Treiber", "Δ Python", "Prüfung Treiber", "Prüfung Δ"]
    breiten = [24, 44, 14, 12, 15, 15, 14, 9, 11, *[14] * len(EFFEKTSPALTEN), 15, 15, 12, 11]
    _kopfzeile(ws, 3, titel, breiten)
    e0 = 10  # erste Effektspalte (J)
    e1 = e0 + len(EFFEKTSPALTEN) - 1
    s_summe, s_python, s_pt, s_pd = e1 + 1, e1 + 2, e1 + 3, e1 + 4
    L = get_column_letter
    abweichungen = abschluss.faktenblatt.abweichungen
    for i, a in enumerate(abweichungen, start=4):
        ws.cell(i, 1, a.id)
        ws.cell(i, 2, a.bezeichnung)
        ws.cell(i, 3, a.ebene)
        ws.cell(i, 4, a.vergleich.value)
        _setze(ws, i, 5, float(a.ist), EUR, eingabe=True)
        _setze(ws, i, 6, float(a.vergleichswert), EUR, eingabe=True)
        _setze(ws, i, 7, f"=E{i}-F{i}", EUR)
        _setze(ws, i, 8, f'=IF(F{i}=0,"",G{i}/ABS(F{i}))', PROZENT)
        ws.cell(i, 9, a.bewertung.value if a.bewertung else "")
        treiber = {t.effektart: t.betrag for t in a.treiber}
        for j, art in enumerate(EFFEKTSPALTEN):
            if art in treiber:
                _setze(ws, i, e0 + j, float(treiber[art]), EUR, eingabe=True)
        _setze(ws, i, s_summe, f"=SUM({L(e0)}{i}:{L(e1)}{i})", EUR)
        _setze(ws, i, s_python, float(a.delta_abs), EUR, eingabe=True)  # type: ignore[arg-type]
        _setze(ws, i, s_pt, f'=IF(COUNT({L(e0)}{i}:{L(e1)}{i})=0,"–",'
                            f'IF(ABS(G{i}-{L(s_summe)}{i})<0.005,"OK","FEHLER"))')
        _setze(ws, i, s_pd, f'=IF(ABS(G{i}-{L(s_python)}{i})<0.005,"OK","FEHLER")')
    letzte = 3 + len(abweichungen)
    _status_formatierung(ws, f"{L(s_pt)}4:{L(s_pd)}{letzte}")
    ws.freeze_panes = "C4"
    ws.auto_filter.ref = f"A3:{L(s_pd)}{letzte}"
    return letzte


def _blatt_kostenstellen(wb: Workbook, abschluss: Abschlussergebnis) -> int:
    ws = wb.create_sheet("Kostenstellen")
    ws["A1"], ws["A1"].font = f"Flexible Plankostenrechnung {abschluss.periodenlabel} (in €)", _TITEL
    titel = ["Kostenstelle", "Kostenart", "Bezeichnung", "mengen-basiert", "einmalig", "Plan fix", "Plan variabel",
             "Planmenge", "Planpreis", "Plan-Std.", "Ist-Std.", "BG", "Istkosten", "Ist-Menge", "Ist mit Menge",
             "verrechnete Plankosten", "Sollkosten", "Ist zu Planpreisen", "Preisabw.", "Verbrauchsabw.",
             "Beschäftigungsabw.", "Einmaleffekt", "Python Preis", "Python Verbrauch", "Python Beschäftigung",
             "Python Einmal", "Prüfung"]
    _kopfzeile(ws, 3, titel, [11, 10, 34, 8, 8, *[14] * 3, 10, 10, 10, 8, *[14] * 3, *[15] * 9, 10])
    ws["L3"].comment = Comment("Beschäftigungsgrad = Ist-Stunden / Plan-Stunden der Kostenstelle", "VarianceAI")
    ka = abschluss.daten.kostenarten.set_index("racct")
    analyse = abschluss.analyse
    i = 4
    for rcntr, rechnung in analyse.kostenstellen.items():
        for racct, w in rechnung.werte.items():
            knoten = analyse.abweichung(f"KST-{rcntr}-{racct}")
            treiber = {t.effektart: t.betrag for t in knoten.treiber}
            ws.cell(i, 1, rcntr)
            ws.cell(i, 2, racct)
            ws.cell(i, 3, str(ka.at[racct, "bezeichnung"]))
            ws.cell(i, 4, "ja" if w.mengenbasiert else "nein")
            ws.cell(i, 5, "ja" if w.einmalig else "nein")
            for spalte, wert in ((6, _euro(w.plan_fix)), (7, _euro(w.plan_var))):
                _setze(ws, i, spalte, wert, EUR, eingabe=True)
            _setze(ws, i, 8, w.plan_menge, ZAHL, eingabe=True)
            _setze(ws, i, 9, f'=IF(H{i}=0,"",G{i}/H{i})', '#,##0.0000 "€"')
            _setze(ws, i, 10, rechnung.stunden_plan, ZAHL, eingabe=True)
            _setze(ws, i, 11, rechnung.stunden_ist, ZAHL, eingabe=True)
            _setze(ws, i, 12, f"=IF(J{i}=0,1,K{i}/J{i})", "0.0%")
            _setze(ws, i, 13, _euro(w.ist), EUR, eingabe=True)
            _setze(ws, i, 14, w.ist_menge, ZAHL, eingabe=True)
            _setze(ws, i, 15, _euro(w.ist_mit_menge), EUR, eingabe=True)
            _setze(ws, i, 16, f"=(F{i}+G{i})*L{i}", EUR)
            _setze(ws, i, 17, f"=F{i}+G{i}*L{i}", EUR)
            _setze(ws, i, 18, f'=IF(E{i}="ja",M{i},IF(AND(D{i}="ja",H{i}>0),N{i}*I{i}+(M{i}-O{i}),M{i}))', EUR)
            _setze(ws, i, 19, f'=IF(E{i}="ja",0,M{i}-R{i})', EUR)
            _setze(ws, i, 20, f'=IF(E{i}="ja",0,R{i}-Q{i})', EUR)
            _setze(ws, i, 21, f'=IF(E{i}="ja",0,Q{i}-P{i})', EUR)
            _setze(ws, i, 22, f'=IF(E{i}="ja",M{i}-P{i},0)', EUR)
            for spalte, art in ((23, Effektart.KOSTEN_PREIS), (24, Effektart.VERBRAUCH),
                                (25, Effektart.BESCHAEFTIGUNG), (26, Effektart.EINMAL)):
                _setze(ws, i, spalte, float(treiber.get(art, 0)), EUR, eingabe=True)
            _setze(ws, i, 27, f'=IF(AND(ABS(S{i}-W{i})<={TOLERANZ},ABS(T{i}-X{i})<={TOLERANZ},'
                              f'ABS(U{i}-Y{i})<={TOLERANZ},ABS(V{i}-Z{i})<={TOLERANZ}),"OK","FEHLER")')
            i += 1
    _status_formatierung(ws, f"AA4:AA{i - 1}")
    ws.freeze_panes = "D4"
    return i - 1


def _blatt_forecast(wb: Workbook, abschluss: Abschlussergebnis) -> dict[str, int]:
    ws = wb.create_sheet("Forecast")
    ws["A1"], ws["A1"].font = f"Rolling Forecast {abschluss.einstellungen.geschaeftsjahr} (in €)", _TITEL
    gj, periode = abschluss.einstellungen.geschaeftsjahr, abschluss.einstellungen.periode
    namen = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
             "November", "Dezember"]
    pruefzeilen: dict[str, int] = {}
    start = 3
    for kennzahl, f in abschluss.forecasts.items():
        ist = monatsreihe(abschluss.daten, kennzahl)
        plan = monatsreihe(abschluss.daten, kennzahl, "PLAN")
        ws.cell(start, 1, kennzahl).font = _KOPF
        _kopfzeile(ws, start + 1, ["Monat", "AC", "FC", "FC unten", "FC oben", "PL"], [26, 16, 16, 16, 16, 16])
        monate = {m.periode: m for m in f.monate}
        for p in range(1, 13):
            r = start + 1 + p
            ws.cell(r, 1, namen[p - 1])
            if p <= periode:
                _setze(ws, r, 2, _euro(int(ist[pd.Period(year=gj, month=p, freq="M")])), EUR, eingabe=True)
            else:
                m = monate[p]
                for spalte, wert in ((3, m.wert_cent), (4, m.unten_cent), (5, m.oben_cent)):
                    _setze(ws, r, spalte, _euro(wert), EUR, eingabe=True)
            _setze(ws, r, 6, _euro(int(plan[pd.Period(year=gj, month=p, freq="M")])), EUR, eingabe=True)
        a, b = start + 2, start + 13
        s = start + 15
        i = f.info
        werte = [
            ("Ist YTD", f"=SUM(B{a}:B{b})", None),
            ("Prognose Restmonate", f"=SUM(C{a}:C{b})", None),
            ("Jahresendprognose", f"=B{s}+B{s + 1}", float(i.jahresendprognose)),  # type: ignore[arg-type]
            ("Plan Gesamtjahr", f"=SUM(F{a}:F{b})", float(i.plan_gesamtjahr)),
            ("Abweichung zum Plan %", f'=IF(B{s + 3}=0,"",(B{s + 2}-B{s + 3})/ABS(B{s + 3}))', None),
        ]
        ws.cell(s - 1, 2, "Excel").font = _KOPF
        ws.cell(s - 1, 3, "Python").font = _KOPF
        ws.cell(s - 1, 4, "Prüfung").font = _KOPF
        for k, (label, formel, python) in enumerate(werte):
            r = s + k
            ws.cell(r, 1, label)
            _setze(ws, r, 2, formel, PROZENT if "%" in label else EUR)
            if python is not None:
                _setze(ws, r, 3, python, EUR, eingabe=True)
                _setze(ws, r, 4, f'=IF(ABS(B{r}-C{r})<={TOLERANZ},"OK","FEHLER")')
        pruefzeilen[kennzahl] = s + 2
        r = s + len(werte) + 1
        _kopfzeile(ws, r, ["Szenario", "Excel", "Python", "Prüfung", "Anpassung Restmonate"])
        for k, sz in enumerate(f.info.szenarien, start=1):
            rr = r + k
            ws.cell(rr, 1, sz.name)
            _setze(ws, rr, 5, float(sz.anpassung_pct or 0) / 100, PROZENT, eingabe=True)
            _setze(ws, rr, 2, f"=B{s}+B{s + 1}*(1+E{rr})", EUR)
            _setze(ws, rr, 3, float(sz.wert), EUR, eingabe=True)
            _setze(ws, rr, 4, f'=IF(ABS(B{rr}-C{rr})<={TOLERANZ},"OK","FEHLER")')
        _status_formatierung(ws, f"D{s}:D{r + 3}")
        ws.cell(r + 4, 1, f"Methode: {f.methode.value} · WAPE (Backtesting): {i.wape_pct} % · "
                          f"Intervall {i.konfidenzniveau_pct} % aus Simulation").font = _LEISE
        start = r + 7
    return pruefzeilen


def _blatt_gegenpruefung(wb: Workbook, letzte_abw: int, letzte_kst: int, forecast_zeilen: dict[str, int]) -> None:
    ws = wb.create_sheet("Gegenprüfung")
    ws["A1"], ws["A1"].font = "Formel-Gegenprüfung: Python-Ergebnis vs. Excel-Formel", _TITEL
    _kopfzeile(ws, 3, ["Prüfung", "Fehler / Differenz", "Status"], [95, 18, 12])
    spalte_pt = get_column_letter(10 + len(EFFEKTSPALTEN) + 2)
    spalte_pd = get_column_letter(10 + len(EFFEKTSPALTEN) + 3)
    pruefungen = [
        ("Abweichungen: Summe der Treiber = Gesamtabweichung (je Objekt)",
         f'=COUNTIF(Abweichungen!{spalte_pt}4:{spalte_pt}{letzte_abw},"FEHLER")'),
        ("Abweichungen: Δ als Excel-Formel = Δ aus Python (je Objekt)",
         f'=COUNTIF(Abweichungen!{spalte_pd}4:{spalte_pd}{letzte_abw},"FEHLER")'),
        ("Kostenstellen: Preis-, Verbrauchs-, Beschäftigungsabweichung, Einmaleffekt (Excel = Python)",
         f'=COUNTIF(Kostenstellen!AA4:AA{letzte_kst},"FEHLER")'),
        ("Ergebnisrechnung: AC je Zeile (Excel = Python)", '=COUNTIF(Ergebnisrechnung!J4:J8,"FEHLER")'),
        ("Drill-down: Summe der Regionen = Umsatz Gesamt (Δ)",
         f'=ABS(SUMIFS(Abweichungen!G4:G{letzte_abw},Abweichungen!C4:C{letzte_abw},"Region",'
         f'Abweichungen!D4:D{letzte_abw},"{Vergleichsart.PLAN_IST.value}",Abweichungen!A4:A{letzte_abw},"UMS-*")'
         f'-SUMIFS(Abweichungen!G4:G{letzte_abw},Abweichungen!A4:A{letzte_abw},"UMS"))'),
        ("Drill-down: Summe der Kostenstellen = Kosten gesamt (Δ)",
         f'=ABS(SUMIFS(Abweichungen!G4:G{letzte_abw},Abweichungen!C4:C{letzte_abw},"Kostenstelle")'
         f'-SUMIFS(Abweichungen!G4:G{letzte_abw},Abweichungen!A4:A{letzte_abw},"KST"))'),
    ]
    pruefungen += [(f"Forecast {k}: Jahresendprognose und Szenarien (Excel = Python)",
                    f'=COUNTIF(Forecast!D{z}:D{z + 8},"FEHLER")') for k, z in forecast_zeilen.items()]
    for i, (text, formel) in enumerate(pruefungen, start=4):
        ws.cell(i, 1, text)
        _setze(ws, i, 2, formel, "#,##0.00")
        _setze(ws, i, 3, f'=IF(ABS(B{i})<0.005,"OK","FEHLER")')
    letzte = 3 + len(pruefungen)
    ws.cell(letzte + 2, 1, "Gesamtstatus").font = _KOPF
    _setze(ws, letzte + 2, 3, f'=IF(COUNTIF(C4:C{letzte},"FEHLER")=0,"OK","FEHLER")')
    ws.cell(letzte + 2, 3).font = _KOPF
    _status_formatierung(ws, f"C4:C{letzte + 2}")


def _schrift_vereinheitlichen(wb: Workbook) -> None:
    """Alle befüllten Zellen in Arial (Farbe und Fettung bleiben erhalten)."""
    for ws in wb.worksheets:
        for zeile in ws.iter_rows():
            for zelle in zeile:
                if zelle.value is not None and zelle.font.name != SCHRIFT:
                    schrift = copy(zelle.font)
                    schrift.name = SCHRIFT
                    zelle.font = schrift


def exportiere_excel(abschluss: Abschlussergebnis, pfad: Path | str, werte_cachen: bool = True) -> Path:
    """Excel-Bericht mit Formeln und Gegenprüfung schreiben.

    openpyxl speichert nur Formeln, keine Ergebnisse. Viele Programme (Vorschau,
    pandas, Power Query, mobile Viewer) zeigen solche Zellen leer an. Deshalb wird
    die Mappe – falls LibreOffice vorhanden ist – einmal berechnet und mit Formeln
    **und** Ergebniswerten gespeichert. Ohne LibreOffice erzwingt ``fullCalcOnLoad``
    die Berechnung beim Öffnen in Excel.
    """
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    _blatt_hinweise(wb, abschluss)
    _blatt_ergebnisrechnung(wb, abschluss)
    letzte_abw = _blatt_abweichungen(wb, abschluss)
    letzte_kst = _blatt_kostenstellen(wb, abschluss)
    forecast_zeilen = _blatt_forecast(wb, abschluss)
    _blatt_gegenpruefung(wb, letzte_abw, letzte_kst, forecast_zeilen)
    wb.move_sheet("Gegenprüfung", offset=-4)
    _schrift_vereinheitlichen(wb)
    wb.calculation.fullCalcOnLoad = True
    wb.save(pfad)
    if werte_cachen and (berechnet := neu_berechnen(pfad)) is not None:
        shutil.move(berechnet, pfad)
        shutil.rmtree(berechnet.parent, ignore_errors=True)
    logger.info("Excel-Bericht geschrieben: %s", pfad)
    return pfad


def neu_berechnen(pfad: Path | str, timeout: int = 120) -> Path | None:
    """Formeln mit LibreOffice berechnen und Ergebnisse cachen (gibt den neuen Pfad zurück).

    Liefert ``None``, wenn LibreOffice nicht installiert ist – die Mappe ist dann
    trotzdem gültig; Excel berechnet die Formeln beim Öffnen.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        logger.warning("LibreOffice nicht gefunden – Formel-Gegenprüfung erfolgt beim Öffnen in Excel.")
        return None
    pfad = Path(pfad)
    ziel = Path(tempfile.mkdtemp(prefix="varianceai_recalc_"))
    with tempfile.TemporaryDirectory(prefix="lo_profil_") as profil:
        umgebung = {**os.environ, "SAL_USE_VCLPLUGIN": "svp"}
        subprocess.run(
            [soffice, f"-env:UserInstallation={Path(profil).as_uri()}", "--headless", "--calc",
             "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(ziel), str(pfad)],
            env=umgebung, capture_output=True, timeout=timeout, check=False,
        )
    ergebnis = ziel / pfad.name
    return ergebnis if ergebnis.exists() else None


def pruefe_excel(pfad: Path | str) -> dict[str, Any]:
    """Berechnet die Mappe und liest Gesamtstatus, Einzelprüfungen und Formelfehler aus."""
    berechnet = neu_berechnen(pfad)
    if berechnet is None:
        return {"berechnet": False}
    wb = load_workbook(berechnet, data_only=True)
    fehlerwerte = ("#VALUE!", "#REF!", "#NAME?", "#DIV/0!", "#N/A", "#NUM!", "Err:")
    formelfehler = [
        f"{ws.title}!{zelle.coordinate}" for ws in wb.worksheets for zeile in ws.iter_rows() for zelle in zeile
        if isinstance(zelle.value, str) and zelle.value.startswith(fehlerwerte)
    ]
    ws = wb["Gegenprüfung"]
    pruefungen = {ws.cell(r, 1).value: ws.cell(r, 3).value for r in range(4, ws.max_row + 1)
                  if ws.cell(r, 1).value and ws.cell(r, 3).value in ("OK", "FEHLER")}
    gesamt = pruefungen.pop("Gesamtstatus", None)
    return {"berechnet": True, "gesamtstatus": gesamt, "pruefungen": pruefungen, "formelfehler": formelfehler,
            "datei": berechnet}


# =============================================================================
# Power BI
# =============================================================================


def _csv(df: pd.DataFrame, pfad: Path) -> None:
    """Deutsches CSV-Format: Semikolon, Dezimalkomma, Datum TT.MM.JJJJ, UTF-8 mit BOM."""
    df.to_csv(pfad, sep=";", decimal=",", index=False, encoding="utf-8-sig", date_format="%d.%m.%Y",
              float_format="%.2f")


def _kalender(von: date, bis: date, gj: int, periode: int) -> pd.DataFrame:
    tage = pd.date_range(von, bis, freq="D")
    monate = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
              "November", "Dezember"]
    return pd.DataFrame({
        "Datum": tage,
        "Jahr": tage.year,
        "Quartal": "Q" + tage.quarter.astype(str),
        "Monat": tage.month,
        "Monatsname": [monate[m - 1] for m in tage.month],
        "Monat_kurz": [monate[m - 1][:3] for m in tage.month],
        "Jahr_Monat": tage.strftime("%Y-%m"),
        "Periode": tage.month,
        "Geschaeftsjahr": tage.year,
        "Ist_abgeschlossen": [(j < gj) or (j == gj and m <= periode) for j, m in zip(tage.year, tage.month, strict=True)],
        "Monatsanfang": [t.day == 1 for t in tage],
    })


DAX_MEASURES = """// ---------------------------------------------------------------------------
// VarianceAI – DAX-Measures (Tabelle „Measures“ anlegen und Ausdrücke einfügen)
// Vorzeichen: fakt_buchungen[Wert_EUR] ist betriebswirtschaftlich positiv
// (Erlöse und Kosten > 0); fakt_buchungen[Betrag_SAP_EUR] folgt Soll/Haben.
// ---------------------------------------------------------------------------

Umsatz AC = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "ERLOES", fakt_buchungen[Version] = "IST" )
Umsatz PL = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "ERLOES", fakt_buchungen[Version] = "PLAN" )
Umsatz PY = CALCULATE ( [Umsatz AC], SAMEPERIODLASTYEAR ( dim_kalender[Datum] ) )
Umsatz ΔPL = [Umsatz AC] - [Umsatz PL]
Umsatz ΔPL % = DIVIDE ( [Umsatz ΔPL], ABS ( [Umsatz PL] ) )
Umsatz ΔPY = [Umsatz AC] - [Umsatz PY]
Umsatz ΔPY % = DIVIDE ( [Umsatz ΔPY], ABS ( [Umsatz PY] ) )

Herstellkosten AC = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "HK", fakt_buchungen[Version] = "IST" )
Herstellkosten PL = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "HK", fakt_buchungen[Version] = "PLAN" )
Deckungsbeitrag AC = [Umsatz AC] - [Herstellkosten AC]
Deckungsbeitrag PL = [Umsatz PL] - [Herstellkosten PL]
Deckungsbeitrag PY = CALCULATE ( [Deckungsbeitrag AC], SAMEPERIODLASTYEAR ( dim_kalender[Datum] ) )
DB-Quote AC = DIVIDE ( [Deckungsbeitrag AC], [Umsatz AC] )
DB-Quote PL = DIVIDE ( [Deckungsbeitrag PL], [Umsatz PL] )
DB-Quote ΔPL (PP) = ( [DB-Quote AC] - [DB-Quote PL] ) * 100

Kosten KST AC = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "KOSTEN", fakt_buchungen[Version] = "IST" )
Kosten KST PL = CALCULATE ( SUM ( fakt_buchungen[Wert_EUR] ), dim_kostenart[Typ] = "KOSTEN", fakt_buchungen[Version] = "PLAN" )
Kosten KST PY = CALCULATE ( [Kosten KST AC], SAMEPERIODLASTYEAR ( dim_kalender[Datum] ) )
Kosten KST ΔPL = [Kosten KST AC] - [Kosten KST PL]

EBIT AC = [Deckungsbeitrag AC] - [Kosten KST AC]
EBIT PL = [Deckungsbeitrag PL] - [Kosten KST PL]
EBIT PY = CALCULATE ( [EBIT AC], SAMEPERIODLASTYEAR ( dim_kalender[Datum] ) )
EBIT ΔPL = [EBIT AC] - [EBIT PL]
EBIT ΔPL % = DIVIDE ( [EBIT ΔPL], ABS ( [EBIT PL] ) )

Ist-Stunden = CALCULATE ( SUM ( fakt_leistung[Stunden] ), fakt_leistung[Version] = "IST" )
Plan-Stunden = CALCULATE ( SUM ( fakt_leistung[Stunden] ), fakt_leistung[Version] = "PLAN" )
Beschäftigungsgrad = DIVIDE ( [Ist-Stunden], [Plan-Stunden] )

Umsatz AC YTD = TOTALYTD ( [Umsatz AC], dim_kalender[Datum] )
Umsatz FC = CALCULATE ( SUM ( fakt_forecast[Wert_EUR] ), fakt_forecast[Kennzahl] = "Umsatz", fakt_forecast[Version] = "{aktuelle_version}" )
Umsatz Jahresendprognose = CALCULATE ( [Umsatz AC], dim_kalender[Ist_abgeschlossen] = TRUE () ) + [Umsatz FC]

// IBCS-Farblogik für bedingte Formatierung (günstig / ungünstig)
Farbe Umsatz ΔPL = IF ( [Umsatz ΔPL] >= 0, "#2E6B30", "#F06A4F" )
Farbe Kosten ΔPL = IF ( [Kosten KST ΔPL] <= 0, "#2E6B30", "#F06A4F" )
Farbe EBIT ΔPL = IF ( [EBIT ΔPL] >= 0, "#2E6B30", "#F06A4F" )
"""

POWER_QUERY = """// Power Query (M) – Import einer VarianceAI-CSV im deutschen Format.
// Für jede Datei eine Abfrage anlegen und den Dateinamen anpassen.
let
    Ordner = "C:\\\\VarianceAI\\\\powerbi\\\\",
    Datei = "fakt_buchungen.csv",
    Quelle = Csv.Document(File.Contents(Ordner & Datei), [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Kopf = Table.PromoteHeaders(Quelle, [PromoteAllScalars = true]),
    // Typen mit deutscher Kultur (Dezimalkomma, Datum TT.MM.JJJJ)
    Typen = Table.TransformColumnTypes(Kopf, {{"Datum", type date}, {"Menge", type number},
        {"Betrag_SAP_EUR", type number}, {"Wert_EUR", type number}}, "de-DE")
in
    Typen
"""

MODELL_MD = """# Power-BI-Paket VarianceAI

Sternschema für den Monatsabschluss der fiktiven {firma} (synthetische Daten).

## Dateien

| Datei | Typ | Inhalt |
|---|---|---|
| `fakt_buchungen.csv` | Fakt | Ist- und Planbuchungen (Monatsebene, Datum = Monatserster) |
| `fakt_leistung.csv` | Fakt | Fertigungsstunden je Kostenstelle |
| `fakt_forecast.csv` | Fakt | Forecast-Versionen und aktuelle Monatsprognose inkl. Intervall |
| `dim_kalender.csv` | Dimension | Tageskalender {von}–{bis} mit Jahr, Quartal, Monat, Periode |
| `dim_kostenart.csv` | Dimension | Kostenarten mit Typ (ERLOES, HK, KOSTEN) |
| `dim_kostenstelle.csv` | Dimension | Kostenstellen mit Profit Center |
| `dim_material.csv` | Dimension | Material mit Produktgruppe |
| `dim_kunde.csv` | Dimension | Kunden mit Region |
| `dim_region.csv` | Dimension | Regionen |
| `measures.dax` | DAX | Measures für AC, PL, PY, Abweichungen, DB-Quote, EBIT, Forecast, IBCS-Farben |
| `power_query.m` | M | Import im deutschen Format (Semikolon, Dezimalkomma, UTF-8) |

## Beziehungen (alle 1:n, einfache Filterrichtung)

```mermaid
erDiagram
    dim_kalender ||--o{{ fakt_buchungen : Datum
    dim_kalender ||--o{{ fakt_leistung : Datum
    dim_kalender ||--o{{ fakt_forecast : Datum
    dim_kostenart ||--o{{ fakt_buchungen : Kostenart_ID
    dim_kostenstelle ||--o{{ fakt_buchungen : Kostenstelle_ID
    dim_kostenstelle ||--o{{ fakt_leistung : Kostenstelle_ID
    dim_material ||--o{{ fakt_buchungen : Material_ID
    dim_kunde ||--o{{ fakt_buchungen : Kunde_ID
    dim_region ||--o{{ fakt_buchungen : Region_ID
```

`dim_kalender` als Datumstabelle markieren (Spalte `Datum`). Leere Fremdschlüssel
(z. B. Kostenstelle bei Erlösbuchungen) sind fachlich korrekt.

## Format

Semikolon als Trennzeichen, Dezimalkomma, Datum `TT.MM.JJJJ`, UTF-8 mit BOM.
Beim Import Gebietsschema **Deutsch (Deutschland)** verwenden.
"""


def exportiere_powerbi(abschluss: Abschlussergebnis, ordner: Path | str, als_zip: bool = True) -> Path:
    """Power-BI-Paket (Sternschema als CSV, DAX, Power Query, Modellbeschreibung)."""
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    d = abschluss.daten
    e = abschluss.einstellungen
    typ = d.kostenarttyp()
    a = d.acdoca.copy()
    a["Datum"] = pd.to_datetime(dict(year=a["gjahr"], month=a["poper"], day=1))
    vorzeichen = a["racct"].map(lambda k: -1 if typ[k] == "ERLOES" else 1)
    gruppen = ["Datum", "version", "racct", "rcntr", "matnr", "kunnr", "regio"]
    fakt = (a.assign(wert=a["hsl_cent"] * vorzeichen)
            .groupby(gruppen, dropna=False, as_index=False)
            .agg(Menge=("msl", "sum"), Betrag_SAP_EUR=("hsl_cent", "sum"), Wert_EUR=("wert", "sum")))
    fakt["Betrag_SAP_EUR"] = fakt["Betrag_SAP_EUR"] / 100
    fakt["Wert_EUR"] = fakt["Wert_EUR"] / 100
    fakt["Menge"] = fakt["Menge"].astype("Int64")
    fakt = fakt.rename(columns={"version": "Version", "racct": "Kostenart_ID", "rcntr": "Kostenstelle_ID",
                                "matnr": "Material_ID", "kunnr": "Kunde_ID", "regio": "Region_ID"})
    _csv(fakt, ordner / "fakt_buchungen.csv")

    lst = d.kst_leistung.copy()
    lst["Datum"] = pd.to_datetime(dict(year=lst["gjahr"], month=lst["poper"], day=1))
    _csv(lst.rename(columns={"version": "Version", "rcntr": "Kostenstelle_ID", "menge_h": "Stunden"})
         [["Datum", "Version", "Kostenstelle_ID", "Stunden"]], ordner / "fakt_leistung.csv")

    zeilen = []
    for kennzahl, f in abschluss.forecasts.items():
        for version, werte in f.versionen.items():
            zeilen += [dict(Datum=date(e.geschaeftsjahr, p, 1), Version=version, Kennzahl=kennzahl, Wert_EUR=w / 100,
                            Unten_EUR=None, Oben_EUR=None) for p, w in werte.items()]
        aktuell = f"FC{e.periode:02d}"
        zeilen += [dict(Datum=date(m.gjahr, m.periode, 1), Version=aktuell, Kennzahl=kennzahl, Wert_EUR=m.wert_cent / 100,
                        Unten_EUR=m.unten_cent / 100, Oben_EUR=m.oben_cent / 100) for m in f.monate]
    fc = pd.DataFrame(zeilen, columns=["Datum", "Version", "Kennzahl", "Wert_EUR", "Unten_EUR", "Oben_EUR"])
    fc["Datum"] = pd.to_datetime(fc["Datum"])
    _csv(fc, ordner / "fakt_forecast.csv")

    von = date(int(a["gjahr"].min()), 1, 1)
    bis = date(e.geschaeftsjahr, 12, 31)
    _csv(_kalender(von, bis, e.geschaeftsjahr, e.periode), ordner / "dim_kalender.csv")
    _csv(d.kostenarten.rename(columns={"racct": "Kostenart_ID", "bezeichnung": "Kostenart", "typ": "Typ",
                                       "mengenbasiert": "Mengenbasiert", "einheit": "Einheit", "einmalig": "Einmalig"}),
         ordner / "dim_kostenart.csv")
    _csv(d.kostenstellen.rename(columns={"rcntr": "Kostenstelle_ID", "bezeichnung": "Kostenstelle",
                                         "prctr": "Profit_Center", "leistungsart": "Leistungsart"}),
         ordner / "dim_kostenstelle.csv")
    pg = dict(zip(d.produktgruppen["prodh"], d.produktgruppen["bezeichnung"], strict=True))
    mat = d.material.assign(Produktgruppe=d.material["prodh"].map(pg))
    mat["Listenpreis_EUR"] = mat["listenpreis_cent"] / 100
    mat["Standardkosten_EUR"] = mat["standardkosten_cent"] / 100
    _csv(mat.rename(columns={"matnr": "Material_ID", "bezeichnung": "Material", "prodh": "Produktgruppe_ID",
                             "neu_ab_periode": "Neu_ab_Periode"})
         [["Material_ID", "Material", "Produktgruppe_ID", "Produktgruppe", "Listenpreis_EUR", "Standardkosten_EUR",
           "Neu_ab_Periode"]], ordner / "dim_material.csv")
    _csv(d.kunden.rename(columns={"kunnr": "Kunde_ID", "name": "Kunde", "regio": "Region_ID"}), ordner / "dim_kunde.csv")
    _csv(d.regionen.rename(columns={"regio": "Region_ID", "bezeichnung": "Region", "prctr": "Profit_Center"}),
         ordner / "dim_region.csv")

    (ordner / "measures.dax").write_text(DAX_MEASURES.replace("{aktuelle_version}", f"FC{e.periode:02d}"), encoding="utf-8")
    (ordner / "power_query.m").write_text(POWER_QUERY, encoding="utf-8")
    (ordner / "README.md").write_text(MODELL_MD.format(firma=abschluss.faktenblatt.unternehmen,
                                                       von=von.strftime("%d.%m.%Y"), bis=bis.strftime("%d.%m.%Y")),
                                      encoding="utf-8")
    logger.info("Power-BI-Paket geschrieben: %s", ordner)
    if not als_zip:
        return ordner
    archiv = ordner.with_suffix(".zip")
    with zipfile.ZipFile(archiv, "w", zipfile.ZIP_DEFLATED) as z:
        for datei in sorted(ordner.iterdir()):
            z.write(datei, f"{ordner.name}/{datei.name}")
    return archiv


def dax_spaltenreferenzen(dax: str) -> set[tuple[str, str]]:
    """Alle ``tabelle[spalte]``-Referenzen in einem DAX-Text (für die Modellprüfung)."""
    return set(re.findall(r"\b(fakt_\w+|dim_\w+)\[([^\]]+)\]", dax))
