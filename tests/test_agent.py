"""ControllerAgent und LLM-Clients (Mock, Claude, Gemini) – vollständig offline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agent import (
    ClaudeClient,
    ControllerAgent,
    Freigabestatus,
    MockLLMClient,
    Nachricht,
    ObjektNichtGefunden,
    Textquelle,
    ToolAufrufAnfrage,
    ToolErgebnis,
    erstelle_llm_client,
    tool_definitionen,
)

from .conftest import DefekterClient, SkriptClient


@pytest.fixture
def agent(faktenblatt) -> ControllerAgent:
    return ControllerAgent(faktenblatt, MockLLMClient())


# --- Kommentierung ----------------------------------------------------------

def test_mock_kommentare_bestehen_pruefung_und_sind_entwuerfe(agent, faktenblatt):
    kommentare = agent.kommentiere_wesentliche()
    assert [k.objekt_id for k in kommentare] == [a.id for a in faktenblatt.wesentliche()]
    for k in kommentare:
        assert k.quelle is Textquelle.LLM
        assert k.pruefung.bestanden, k.pruefung.verstoesse
        assert k.status is Freigabestatus.ENTWURF


def test_beispielkommentar_im_controlling_stil(agent):
    text = agent.kommentiere("UMS-GES").text
    assert text.startswith("Umsatz Gesamt: −4,4 % (−570,0 T€) ggü. Plan, ungünstig; überwiegend Mengeneffekt")
    assert "Maßnahme:" in text


def test_halluzinierter_kommentar_faellt_auf_regelbasiert_zurueck(faktenblatt):
    llm = SkriptClient(["Umsatz Region Süd −7,9 % ggü. Plan wegen Preisverfall."])
    k = ControllerAgent(faktenblatt, llm).kommentiere("UMS-SUED")
    assert k.quelle is Textquelle.REGELBASIERT
    assert k.verworfener_ki_text is not None
    assert k.text.startswith("Umsatz Region Süd: −4,2 % (−190,0 T€) ggü. Plan, ungünstig.")


def test_ausfall_des_llm_fuehrt_zu_regelbasiertem_kommentar(faktenblatt):
    k = ControllerAgent(faktenblatt, DefekterClient()).kommentiere("KST-4100")
    assert k.quelle is Textquelle.REGELBASIERT and k.pruefung.bestanden


def test_unbekanntes_objekt(agent):
    with pytest.raises(ObjektNichtGefunden):
        agent.kommentiere("GIBT-ES-NICHT")


# --- Fragen -----------------------------------------------------------------

def test_frage_zum_deckungsbeitrag_nutzt_tools(agent):
    antwort = agent.beantworte("Warum ist der Deckungsbeitrag im März gesunken?")
    assert antwort.quelle is Textquelle.LLM, antwort.pruefung.verstoesse
    assert [a.name for a in antwort.tool_aufrufe] == ["abweichungen_auflisten", "abweichung_holen", "drilldown"]
    assert antwort.tool_aufrufe[1].argumente == {"objekt_id": "DB-GES"}
    assert "−360,0 T€" in antwort.text


def test_frage_zur_jahresendprognose(agent):
    antwort = agent.beantworte("Wo landen wir beim Umsatz am Jahresende?")
    assert "forecast_holen" in [a.name for a in antwort.tool_aufrufe]
    assert "153,5 Mio. €" in antwort.text
    assert antwort.pruefung.bestanden


def test_frage_ohne_treffer_liefert_top_treiber(agent):
    antwort = agent.beantworte("Was sind die größten Belastungen?")
    assert "top_treiber" in [a.name for a in antwort.tool_aufrufe]
    assert antwort.pruefung.bestanden


def test_ausfall_bei_frage_liefert_regelbasierte_antwort(faktenblatt):
    antwort = ControllerAgent(faktenblatt, DefekterClient()).beantworte("Wie lief der März?")
    assert antwort.quelle is Textquelle.REGELBASIERT
    assert antwort.text.startswith("Hinweis:")
    assert antwort.pruefung.bestanden


@pytest.mark.parametrize("frage", ["", "   ", "x" * 1001])
def test_ungueltige_fragen(agent, frage):
    with pytest.raises(ValueError):
        agent.beantworte(frage)


def test_mock_ist_deterministisch(faktenblatt):
    a = ControllerAgent(faktenblatt, MockLLMClient()).kommentiere("UMS-WEST").text
    b = ControllerAgent(faktenblatt, MockLLMClient()).kommentiere("UMS-WEST").text
    assert a == b


# --- Client-Fabrik und Anbieter ---------------------------------------------

def test_fabrik_faellt_ohne_schluessel_auf_mock_zurueck(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert isinstance(erstelle_llm_client("claude"), MockLLMClient)
    assert isinstance(erstelle_llm_client("gemini"), MockLLMClient)
    monkeypatch.setenv("LLM_ANBIETER", "mock")
    assert isinstance(erstelle_llm_client(), MockLLMClient)


def test_fabrik_unbekannter_anbieter():
    with pytest.raises(ValueError):
        erstelle_llm_client("openai-xyz")


def test_claude_nachrichtenformat():
    nachrichten = [
        Nachricht("user", "Frage"),
        Nachricht("assistant", tool_aufrufe=[ToolAufrufAnfrage("tu_1", "top_treiber", {"anzahl": 3})]),
        Nachricht("tool", tool_ergebnisse=[ToolErgebnis("tu_1", "top_treiber", "<tool_ergebnis>\n{}\n</tool_ergebnis>")]),
    ]
    umgewandelt = ClaudeClient.nachrichten_umwandeln(nachrichten)
    assert umgewandelt[0] == {"role": "user", "content": "Frage"}
    assert umgewandelt[1]["content"][0] == {"type": "tool_use", "id": "tu_1", "name": "top_treiber", "input": {"anzahl": 3}}
    assert umgewandelt[2]["content"][0]["type"] == "tool_result"
    assert umgewandelt[2]["content"][0]["tool_use_id"] == "tu_1"


def test_claude_client_mit_simuliertem_sdk(faktenblatt):
    """Prüft den vollständigen Tool-Use-Ablauf gegen ein simuliertes SDK-Objekt."""
    antworten = [
        SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="tu_1", name="abweichung_holen",
                                                 input={"objekt_id": "UMS-SUED"})]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="Umsatz Region Süd −4,2 % (−190,0 T€) ggü. Plan.")]),
    ]
    gesendet: list[dict] = []

    def create(**kwargs):
        gesendet.append(kwargs)
        return antworten.pop(0)

    sdk = SimpleNamespace(messages=SimpleNamespace(create=create))
    client = ClaudeClient(modell="test-modell", client=sdk, temperatur=0)
    antwort = ControllerAgent(faktenblatt, client).beantworte("Wie steht der Süden?")
    assert antwort.quelle is Textquelle.LLM
    assert gesendet[0]["model"] == "test-modell" and gesendet[0]["temperature"] == 0
    assert {t["name"] for t in gesendet[0]["tools"]} == {d.name for d in tool_definitionen()}
    assert gesendet[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_gemini_nachrichtenformat():
    pytest.importorskip("google.genai")
    from src.agent import GeminiClient

    client = GeminiClient(api_key="test", client=object())
    inhalte = client.nachrichten_umwandeln([
        Nachricht("user", "Frage"),
        Nachricht("assistant", tool_aufrufe=[ToolAufrufAnfrage("gemini-0", "top_treiber", {"anzahl": 3})]),
        Nachricht("tool", tool_ergebnisse=[ToolErgebnis("gemini-0", "top_treiber", "{}")]),
    ])
    assert [c.role for c in inhalte] == ["user", "model", "user"]
    assert inhalte[1].parts[0].function_call.name == "top_treiber"
    assert inhalte[2].parts[0].function_response.name == "top_treiber"
