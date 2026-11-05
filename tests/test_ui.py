"""Designsystem und interaktive Diagramme des Cockpits."""

from __future__ import annotations

import importlib.util
from decimal import Decimal

import pytest

from src import ui_design as ui
from src import ui_diagramme as dia
from src.agent import vergleichstext


def test_html_wird_maskiert():
    boese = '<img src=x onerror="alert(1)">'
    html = ui.kpi_leiste([ui.Kpi(boese, boese, boese)]) + ui.protokoll([ui.Logeintrag("1", boese, "neutral", boese, boese)])
    assert "<img src=x" not in html and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


def test_grafiken_als_data_uri_ohne_inline_svg():
    for html in (ui.symbol("check"), ui.sparkline([1, 2, 3], [1, 1, 2]), ui.ring(0.5, "1/2")):
        assert "<svg" not in html and 'src="data:image/svg+xml;base64,' in html


def test_farben_semantisch_getrennt():
    """Der Akzent ist für Interaktion/KI reserviert und kollidiert nicht mit den Datenfarben."""
    daten = {ui.FARBEN[k] for k in ("ac", "py", "gut", "schlecht")}
    assert ui.FARBEN["akzent"] not in daten
    assert ui.FARBEN["gut"] == "#3AAB86" and ui.FARBEN["schlecht"] == "#C8382F"  # CVD-validiertes Paar


@pytest.mark.invariante
@pytest.mark.parametrize("objekt", ["EBIT", "UMS", "KST", "KST-4300", "UMS-SUED-ANT", "VJ-UMS"])
def test_bruecke_schliesst_exakt(abschluss, objekt):
    abw = abschluss.analyse.abweichung(objekt)
    df, nur_abweichung = dia.brueckendaten(abw)
    effekte = df[df["art"].isin(["gut", "schlecht"])]
    if nur_abweichung:
        summe = effekte.iloc[:-1]["bis"].sub(effekte.iloc[:-1]["von"]).sum()
        assert summe == pytest.approx(float(abw.delta_abs) / 1000, abs=1e-6)
        assert df.iloc[-1]["bis"] == pytest.approx(float(abw.delta_abs) / 1000, abs=1e-6)
    else:
        assert df.iloc[-1]["bis"] == pytest.approx(float(abw.ist) / 1000, abs=1e-6)
        assert effekte.iloc[-1]["bis"] == pytest.approx(float(abw.ist) / 1000, abs=1e-6)  # lückenlos bis AC


def test_kostenstellen_gegen_verrechnete_plankosten(abschluss):
    kst = abschluss.analyse.abweichung("KST-4300")
    assert dia.basis_kuerzel(kst) == "PL verr." and vergleichstext(kst) == "ggü. verrechneten Plankosten"
    assert vergleichstext(abschluss.analyse.abweichung("UMS")) == "ggü. Plan"


def test_ebit_monatswerte_passen_zur_ergebnisrechnung(abschluss):
    monate = dia.monatsdaten(abschluss, "EBIT")
    ebit = next(z for z in abschluss.analyse.ergebnisrechnung if z.kennzahl == "EBIT")
    assert monate.loc[monate["m"] == abschluss.einstellungen.periode, "ac"].item() == pytest.approx(
        float(Decimal(ebit.ist) / 100_000), abs=1e-6)


def _alle_diagramme(abschluss):
    a = abschluss.analyse
    return [dia.bruecke(a.abweichung("EBIT")), dia.bruecke(a.abweichung("UMS")), dia.abweichungsbalken(a.drilldown("KST")),
            dia.monatsverlauf(abschluss, "EBIT"), dia.forecastverlauf(abschluss, "Umsatz")]


def test_diagramme_sind_gueltige_vega_lite_specs(abschluss):
    for chart in _alle_diagramme(abschluss):
        spec = chart.to_dict()  # validiert gegen das Vega-Lite-Schema
        assert spec["background"] == "transparent"


@pytest.mark.skipif(importlib.util.find_spec("vl_convert") is None, reason="vl-convert nicht installiert")
def test_diagramme_rendern_fehlerfrei(abschluss):
    """Schema-gültig reicht nicht: gemischte Achsdefinitionen in Layern brechen erst beim Rendern ab."""
    import vl_convert

    for chart in _alle_diagramme(abschluss):
        assert vl_convert.vegalite_to_svg(chart.to_dict()).startswith("<svg")
