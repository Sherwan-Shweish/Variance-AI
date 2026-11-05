"""Tool-Whitelist: nur feste Analysefunktionen, strikte Parameter, kein freies SQL."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.agent import (
    TOOL_WHITELIST,
    ControllerAgent,
    FaktenblattBackend,
    Textquelle,
    ToolAusfuehrer,
    ToolNichtErlaubtFehler,
    tool_definitionen,
    tool_holen,
)

from .conftest import SkriptClient, tool_antwort

ERWARTETE_TOOLS = {"abweichungen_auflisten", "abweichung_holen", "drilldown", "top_treiber", "forecast_holen"}


@pytest.fixture
def ausfuehrer(faktenblatt) -> ToolAusfuehrer:
    return ToolAusfuehrer(FaktenblattBackend(faktenblatt), max_aufrufe=8)


def test_whitelist_ist_abschliessend_und_ohne_sql():
    assert set(TOOL_WHITELIST) == ERWARTETE_TOOLS
    assert not any("sql" in name.lower() for name in TOOL_WHITELIST)


def test_whitelist_ist_unveraenderlich():
    with pytest.raises(TypeError):
        TOOL_WHITELIST["sql_ausfuehren"] = None  # type: ignore[index]


def test_tool_schemata_sind_strikt():
    definitionen = tool_definitionen()
    assert {d.name for d in definitionen} == ERWARTETE_TOOLS
    for d in definitionen:
        assert d.parameter_schema["type"] == "object"
        assert d.parameter_schema["additionalProperties"] is False


@pytest.mark.parametrize("name", ["sql_ausfuehren", "datei_lesen", "ABWEICHUNG_HOLEN", ""])
def test_nicht_gelistete_tools_werden_abgelehnt(name, ausfuehrer):
    with pytest.raises(ToolNichtErlaubtFehler):
        tool_holen(name)
    aufruf = ausfuehrer.ausfuehren(name, {"query": "SELECT * FROM acdoca"})
    assert aufruf.erlaubt is False
    assert "Whitelist" in aufruf.fehler


@pytest.mark.parametrize(
    ("name", "argumente"),
    [
        ("top_treiber", {"anzahl": 99}),
        ("top_treiber", {"anzahl": 3, "sql": "DROP TABLE"}),
        ("abweichung_holen", {"objekt_id": "x'; DROP TABLE acdoca;--"}),
        ("abweichung_holen", {}),
    ],
)
def test_ungueltige_parameter_werden_abgelehnt(name, argumente, ausfuehrer):
    aufruf = ausfuehrer.ausfuehren(name, argumente)
    assert aufruf.erlaubt is True
    assert aufruf.ergebnis is None
    assert aufruf.fehler.startswith("Ungültige Parameter")


def test_unbekanntes_objekt_liefert_fehlermeldung(ausfuehrer):
    aufruf = ausfuehrer.ausfuehren("abweichung_holen", {"objekt_id": "GIBT-ES-NICHT"})
    assert "nicht im Faktenblatt" in aufruf.fehler


@pytest.mark.invariante
def test_drilldown_summe_gleich_elternobjekt(ausfuehrer, faktenblatt):
    kinder = ausfuehrer.ausfuehren("drilldown", {"objekt_id": "UMS-GES"}).ergebnis
    assert [k.id for k in kinder] == ["UMS-WEST", "UMS-SUED", "UMS-NORD"]  # nach |Δ| sortiert
    assert sum(k.delta_abs for k in kinder) == faktenblatt.abweichung("UMS-GES").delta_abs


def test_top_treiber_ranking_deterministisch(ausfuehrer):
    erg1 = ausfuehrer.ausfuehren("top_treiber", {"anzahl": 3}).ergebnis
    erg2 = ausfuehrer.ausfuehren("top_treiber", {"anzahl": 3}).ergebnis
    assert erg1 == erg2
    betraege = [abs(e.betrag) for e in erg1.eintraege]
    assert betraege == sorted(betraege, reverse=True)
    assert [e.rang for e in erg1.eintraege] == [1, 2, 3]
    # tiefste wesentliche Objekte – keine Doppelzählung über Hierarchieebenen
    assert {e.objekt_id for e in erg1.eintraege} <= {"UMS-SUED-ANTR", "UMS-WEST", "KST-4100"}
    assert erg1.eintraege[0].betrag == D("-160000.00")


def test_forecast_holen(ausfuehrer):
    assert ausfuehrer.ausfuehren("forecast_holen", {"kennzahl": "Umsatz"}).ergebnis.jahresendprognose == D("153500000.00")
    assert "Kein Forecast" in ausfuehrer.ausfuehren("forecast_holen", {"kennzahl": "EBIT"}).fehler


def test_limit_der_tool_aufrufe(faktenblatt):
    ausfuehrer = ToolAusfuehrer(FaktenblattBackend(faktenblatt), max_aufrufe=2)
    for _ in range(2):
        assert ausfuehrer.ausfuehren("abweichungen_auflisten", {}).fehler is None
    aufruf = ausfuehrer.ausfuehren("abweichungen_auflisten", {})
    assert aufruf.erlaubt is False and "Limit" in aufruf.fehler


def test_agent_lehnt_nicht_gelistetes_tool_ab_und_antwortet_trotzdem(faktenblatt):
    llm = SkriptClient([
        tool_antwort(("sql_ausfuehren", {"query": "DELETE FROM acdoca"})),
        tool_antwort(("abweichung_holen", {"objekt_id": "UMS-SUED"})),
        "Umsatz Region Süd −4,2 % (−190,0 T€) ggü. Plan, überwiegend Mengeneffekt.",
    ])
    antwort = ControllerAgent(faktenblatt, llm).beantworte("Warum liegt der Süden unter Plan?")
    assert antwort.quelle is Textquelle.LLM
    abgelehnt = [a for a in antwort.tool_aufrufe if not a.erlaubt]
    assert [a.name for a in abgelehnt] == ["sql_ausfuehren"]
    assert "Whitelist" in llm.alle_texte()  # Fehlermeldung ging als Tool-Ergebnis an das LLM
