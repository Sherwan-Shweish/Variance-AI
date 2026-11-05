"""Excel mit Formel-Gegenprüfung und Power-BI-Paket."""

from __future__ import annotations

import csv
import shutil
import zipfile

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.exporter import (
    DAX_MEASURES,
    dax_spaltenreferenzen,
    exportiere_excel,
    exportiere_powerbi,
    pruefe_excel,
)

from .conftest import cent

LIBREOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


@pytest.fixture(scope="module")
def excel(abschluss, tmp_path_factory):
    return exportiere_excel(abschluss, tmp_path_factory.mktemp("excel") / "export.xlsx")


@pytest.fixture(scope="module")
def powerbi(abschluss, tmp_path_factory):
    return exportiere_powerbi(abschluss, tmp_path_factory.mktemp("pbi") / "powerbi", als_zip=False)


# --- Excel --------------------------------------------------------------------

def test_excel_blaetter(excel):
    wb = load_workbook(excel)
    assert wb.sheetnames == ["Hinweise", "Gegenprüfung", "Ergebnisrechnung", "Abweichungen", "Kostenstellen", "Forecast"]


def test_ableitungen_sind_formeln_nicht_werte(excel):
    wb = load_workbook(excel)
    er = wb["Ergebnisrechnung"]
    assert er["B6"].value == "=B4-B5"          # Deckungsbeitrag
    assert er["B8"].value == "=B6-B7"          # EBIT
    kst = wb["Kostenstellen"]
    assert kst["P4"].value == "=(F4+G4)*L4"    # verrechnete Plankosten
    assert kst["Q4"].value == "=F4+G4*L4"      # Sollkosten
    assert kst["U4"].value == '=IF(E4="ja",0,Q4-P4)'  # Beschäftigungsabweichung
    assert er["B4"].font.color.rgb.endswith("0000FF")  # Eingabewerte blau


@pytest.mark.skipif(LIBREOFFICE is None, reason="LibreOffice nicht installiert")
def test_formel_gegenpruefung_ok(excel):
    ergebnis = pruefe_excel(excel)
    assert ergebnis["formelfehler"] == []
    assert ergebnis["gesamtstatus"] == "OK"
    assert len(ergebnis["pruefungen"]) == 9 and set(ergebnis["pruefungen"].values()) == {"OK"}
    werte = load_workbook(ergebnis["datei"], data_only=True)
    assert werte["Ergebnisrechnung"]["B8"].value == pytest.approx(569_316.53, abs=500)


@pytest.mark.skipif(LIBREOFFICE is None, reason="LibreOffice nicht installiert")
def test_excel_enthaelt_formeln_und_ergebniswerte(excel):
    """Vorschau-Programme und pandas lesen nur gespeicherte Werte – Formelzellen dürfen nicht leer sein."""
    werte = pd.read_excel(excel, sheet_name="Ergebnisrechnung", header=2)
    ebit = werte.set_index("Kennzahl").loc["EBIT"]
    assert ebit["AC"] == pytest.approx(569_310.92, abs=0.01) and ebit["Prüfung"] == "OK"
    assert pd.read_excel(excel, sheet_name="Gegenprüfung", header=None).iloc[-1, 2] == "OK"
    assert load_workbook(excel)["Ergebnisrechnung"]["B8"].value == "=B6-B7"  # Formel bleibt erhalten


@pytest.mark.skipif(LIBREOFFICE is None, reason="LibreOffice nicht installiert")
def test_gegenpruefung_erkennt_manipulation(excel, tmp_path):
    """Wird ein Eingabewert verändert, muss die Gegenprüfung FEHLER melden."""
    wb = load_workbook(excel)
    abw = wb["Abweichungen"]
    zeile = next(r for r in range(4, abw.max_row + 1) if abw.cell(r, 1).value == "UMS-SUED-ANT")
    spalte = next(c for c in range(1, abw.max_column + 1) if abw.cell(3, c).value == "Mengeneffekt")
    abw.cell(zeile, spalte).value += 1000
    kst = wb["Kostenstellen"]
    kst["M4"].value += 500  # Istkosten verändert → Python-Abweichungen passen nicht mehr
    manipuliert = tmp_path / "manipuliert.xlsx"
    wb.save(manipuliert)
    ergebnis = pruefe_excel(manipuliert)
    assert ergebnis["gesamtstatus"] == "FEHLER"
    fehler = {name for name, status in ergebnis["pruefungen"].items() if status == "FEHLER"}
    assert any(name.startswith("Abweichungen: Summe der Treiber") for name in fehler)
    assert any(name.startswith("Kostenstellen") for name in fehler)


# --- Power BI ------------------------------------------------------------------

def _lesen(ordner, name):
    return pd.read_csv(ordner / name, sep=";", decimal=",", encoding="utf-8-sig", dtype={"Kostenart_ID": str,
                       "Kostenstelle_ID": str})


def test_deutsches_csv_format(powerbi):
    roh = (powerbi / "fakt_buchungen.csv").read_bytes()
    assert roh.startswith(b"\xef\xbb\xbf")  # UTF-8 mit BOM
    kopf, zeile = roh.decode("utf-8-sig").splitlines()[:2]
    assert kopf.startswith("Datum;Version;Kostenart_ID")
    felder = next(csv.reader([zeile], delimiter=";"))
    assert felder[0].count(".") == 2 and len(felder[0]) == 10  # TT.MM.JJJJ
    assert "," in felder[-1] and "." not in felder[-1]          # Dezimalkomma, kein Tausenderpunkt


@pytest.mark.invariante
def test_powerbi_summen_stimmen_mit_analyse(powerbi, abschluss):
    fakt = _lesen(powerbi, "fakt_buchungen.csv")
    kostenart = _lesen(powerbi, "dim_kostenart.csv")
    fakt = fakt.merge(kostenart[["Kostenart_ID", "Typ"]], on="Kostenart_ID")
    sept = fakt[(fakt["Datum"] == "01.09.2026") & (fakt["Version"] == "IST")]
    umsatz = round(sept.loc[sept["Typ"] == "ERLOES", "Wert_EUR"].sum() * 100)
    kosten = round(sept.loc[sept["Typ"] == "KOSTEN", "Wert_EUR"].sum() * 100)
    assert umsatz == cent(abschluss.analyse.abweichung("UMS").ist)
    assert kosten == cent(abschluss.analyse.abweichung("KST").ist)


def test_sternschema_referentielle_integritaet(powerbi):
    fakt = _lesen(powerbi, "fakt_buchungen.csv")
    for spalte, datei in (("Kostenart_ID", "dim_kostenart.csv"), ("Kostenstelle_ID", "dim_kostenstelle.csv"),
                          ("Material_ID", "dim_material.csv"), ("Kunde_ID", "dim_kunde.csv"),
                          ("Region_ID", "dim_region.csv")):
        schluessel = set(_lesen(powerbi, datei)[spalte].astype(str))
        werte = set(fakt[spalte].dropna().astype(str))
        assert werte <= schluessel, spalte
        assert _lesen(powerbi, datei)[spalte].is_unique, datei
    kalender = set(_lesen(powerbi, "dim_kalender.csv")["Datum"])
    for datei in ("fakt_buchungen.csv", "fakt_leistung.csv", "fakt_forecast.csv"):
        assert set(_lesen(powerbi, datei)["Datum"]) <= kalender, datei


def test_kalenderdimension(powerbi):
    kal = _lesen(powerbi, "dim_kalender.csv")
    assert kal["Datum"].iloc[0] == "01.01.2023" and kal["Datum"].iloc[-1] == "31.12.2026"
    assert {"Jahr", "Quartal", "Monat", "Monatsname", "Jahr_Monat", "Ist_abgeschlossen"} <= set(kal.columns)
    assert len(kal) == 1461


def test_dax_referenziert_nur_vorhandene_spalten(powerbi):
    dax = (powerbi / "measures.dax").read_text(encoding="utf-8")
    assert "FC09" in dax and "{aktuelle_version}" not in dax
    for tabelle, spalte in dax_spaltenreferenzen(DAX_MEASURES):
        assert spalte in _lesen(powerbi, f"{tabelle}.csv").columns, f"{tabelle}[{spalte}]"
    for measure in ("Umsatz AC", "Umsatz PY", "DB-Quote AC", "EBIT ΔPL %", "Beschäftigungsgrad", "Umsatz Jahresendprognose"):
        assert f"\n{measure} = " in dax


def test_powerbi_zip(abschluss, tmp_path):
    archiv = exportiere_powerbi(abschluss, tmp_path / "powerbi")
    with zipfile.ZipFile(archiv) as z:
        namen = {n.split("/")[-1] for n in z.namelist()}
    assert {"fakt_buchungen.csv", "dim_kalender.csv", "measures.dax", "power_query.m", "README.md"} <= namen
