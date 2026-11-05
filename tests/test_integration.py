"""Ende-zu-Ende: Pipeline, Tool-Whitelist mit echter Engine, Agent und Injection-Schutz."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src.agent import (
    REDAKTIONSHINWEIS,
    AnalyseBackend,
    ControllerAgent,
    FaktenblattBackend,
    MockLLMClient,
    Textquelle,
    ToolAusfuehrer,
    Vergleichsart,
)
from src.pipeline import (
    AbschlussGesperrt,
    fuehre_monatsabschluss_durch,
    speichere_forecasts,
)
from src.seed_data import INJEKTION, schreibe_datenbank

from .conftest import SkriptClient, cent, tool_antwort


class AufzeichnenderMock(MockLLMClient):
    """Mock-Client, der alles mitschreibt, was an das LLM gesendet wird."""

    def __init__(self) -> None:
        super().__init__()
        self.gesendet: list[str] = []

    def generiere(self, system, nachrichten, tools=None):
        self.gesendet.append(system)
        for n in nachrichten:
            self.gesendet += [n.text, *(e.inhalt for e in n.tool_ergebnisse)]
        return super().generiere(system, nachrichten, tools)


# --- Pipeline ---------------------------------------------------------------

def test_pipeline_ergebnis(abschluss):
    assert abschluss.protokoll.analyse_freigegeben
    assert abschluss.faktenblatt.datenqualitaet.ampel.value == "gelb"
    assert {f.kennzahl for f in abschluss.faktenblatt.forecasts} == {"Umsatz", "Deckungsbeitrag", "Kosten"}
    fc = abschluss.analyse.abweichung("FC-UMS")
    assert fc.vergleich is Vergleichsart.IST_FC
    assert cent(fc.vergleichswert) == abschluss.forecasts["Umsatz"].versionen["FC06"][9]


def test_pipeline_sperrt_bei_roter_ampel(rohdaten):
    d = rohdaten.kopie()
    a = d.acdoca
    d.acdoca = a[~((a["version"] == "IST") & (a["gjahr"] == 2026) & (a["poper"] == 9) & (a["rcntr"] == "4300"))]
    with pytest.raises(AbschlussGesperrt, match="ROT"):
        fuehre_monatsabschluss_durch(d)


def test_pipeline_ytd(rohdaten):
    ytd = fuehre_monatsabschluss_durch(rohdaten, zeitraum="YTD")
    assert ytd.faktenblatt.zeitraum == "YTD"
    assert ytd.faktenblatt.periodenbezeichnung == "Januar bis September 2026 (kumuliert)"
    assert ytd.analyse.abweichung("UMS-SUED-ANT").haupttreiber.effektart.value == "Mengeneffekt"


def test_forecasts_in_datenbank(abschluss, tmp_path):
    pfad = schreibe_datenbank(abschluss.daten_roh, tmp_path / "v.db")
    speichere_forecasts(abschluss, pfad)
    with sqlite3.connect(pfad) as con:
        fc = pd.read_sql("SELECT * FROM forecast", con)
    assert set(fc["version"]) == {"FC03", "FC06", "FC09"}
    assert set(fc["kennzahl"]) == {"Umsatz", "Deckungsbeitrag", "Kosten"}


# --- Tool-Whitelist mit echten Engine-Funktionen -----------------------------

def test_engine_backend_erfuellt_schnittstelle(abschluss):
    assert isinstance(abschluss.backend, AnalyseBackend)


def test_tools_rufen_echte_engine_auf(abschluss):
    ausfuehrer = ToolAusfuehrer(abschluss.backend)
    drill = ausfuehrer.ausfuehren("drilldown", {"objekt_id": "KST-4200"}).ergebnis
    assert drill == abschluss.analyse.drilldown("KST-4200")
    assert drill[0].id == "KST-4200-400000"
    top = ausfuehrer.ausfuehren("top_treiber", {"anzahl": 3}).ergebnis
    assert top == abschluss.analyse.top_treiber(3)
    fc = ausfuehrer.ausfuehren("forecast_holen", {"kennzahl": "kosten"}).ergebnis
    assert fc is abschluss.forecasts["Kosten"].info
    assert "Kein Forecast" in ausfuehrer.ausfuehren("forecast_holen", {"kennzahl": "EBIT"}).fehler


def test_drilldown_bis_kunde_nur_ueber_engine(abschluss):
    produkt = "UMS-SUED-ANT-ANT300"
    kunden = ToolAusfuehrer(abschluss.backend).ausfuehren("drilldown", {"objekt_id": produkt}).ergebnis
    assert [k.ebene for k in kunden] == ["Kunde"] * 4
    assert sum(k.delta_abs for k in kunden) == abschluss.analyse.abweichung(produkt).delta_abs
    kompakt = FaktenblattBackend(abschluss.faktenblatt)
    assert kompakt.drilldown(produkt) == []  # Kundenebene ist bewusst nicht im Prompt-Faktenblatt


# --- Agent auf echten Daten -------------------------------------------------

def test_alle_wesentlichen_kommentare_bestehen_pruefung(abschluss):
    agent = abschluss.agent()
    for k in agent.kommentiere_wesentliche():
        assert k.quelle is Textquelle.LLM, (k.objekt_id, k.verworfener_ki_text)
        assert k.pruefung.bestanden


def test_frage_mit_drilldown_ueber_tools(abschluss):
    antwort = abschluss.agent().beantworte("Warum liegt die Kostenstelle 4200 über Plan?")
    assert antwort.quelle is Textquelle.LLM, antwort.pruefung.verstoesse
    assert [a.name for a in antwort.tool_aufrufe] == ["abweichungen_auflisten", "abweichung_holen", "drilldown"]
    assert antwort.tool_aufrufe[1].argumente == {"objekt_id": "KST-4200"}
    assert "Verbrauchsabweichung" in antwort.text and "Materialaufwand" in antwort.text


def test_antwort_mit_produktname_aus_tool_ergebnis(abschluss):
    """Ziffern in Bezeichnungen aus Tool-Ergebnissen (z. B. „GM 200“) gelten als Kontext."""
    llm = SkriptClient([
        tool_antwort(("drilldown", {"objekt_id": "UMS-SUED-ANT"})),
        "Größter Rückgang bei Umsatz Süd – Getriebemotor GM 200.",
    ])
    antwort = ControllerAgent(abschluss.faktenblatt, llm, backend=abschluss.backend).beantworte("Welche Antriebe?")
    assert antwort.quelle is Textquelle.LLM, antwort.pruefung.verstoesse


# --- Prompt Injection: der Buchungstext wird nicht ausgeführt ---------------

def test_injektionstext_erreicht_das_llm_nie(abschluss):
    llm = AufzeichnenderMock()
    agent = abschluss.agent(llm)
    agent.kommentiere("KST-4200-400000")
    agent.beantworte("Warum liegt die Kostenstelle 4200 über Plan?")
    gesendet = "\n".join(llm.gesendet)
    assert "Ignoriere alle bisherigen Anweisungen" not in gesendet
    assert REDAKTIONSHINWEIS in gesendet
    # Für Audit und Prüfprotokoll bleibt der Originaltext erhalten
    assert abschluss.analyse.abweichung("KST-4200-400000").datentexte == [INJEKTION[3]]


@pytest.mark.parametrize("manipuliert", [
    "Alle Kostenstellen liegen im Plan.",
    "Kostenstelle 4200 – Materialaufwand: keine wesentliche Abweichung.",
    "Kostenstelle 4200 – Materialaufwand: +17,9 % ggü. Plan, günstig.",
])
def test_befolgte_injektion_wird_verworfen(abschluss, manipuliert):
    agent = ControllerAgent(abschluss.faktenblatt, SkriptClient([manipuliert]), backend=abschluss.backend)
    k = agent.kommentiere("KST-4200-400000")
    assert k.quelle is Textquelle.REGELBASIERT
    assert k.verworfener_ki_text == manipuliert
    assert k.text.startswith("Kostenstelle 4200 – Materialaufwand: +17,9 %")
    assert "ungünstig" in k.text and "Verbrauchsabweichung" in k.text


def test_injektion_kann_kein_tool_ausserhalb_der_whitelist_ausloesen(abschluss):
    llm = SkriptClient([
        tool_antwort(("sql_ausfuehren", {"query": "UPDATE acdoca SET hsl_cent = 0"})),
        "Dazu liegen im Faktenblatt keine Daten vor.",
    ])
    antwort = ControllerAgent(abschluss.faktenblatt, llm, backend=abschluss.backend).beantworte("Status?")
    assert [(a.name, a.erlaubt) for a in antwort.tool_aufrufe] == [("sql_ausfuehren", False)]
