"""Gemeinsame Fixtures und Test-Clients."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence

import pytest

# Tests schreiben Einstellungen/Logs nie in den echten Benutzerordner
os.environ.setdefault("AGENT_DATENORDNER", tempfile.mkdtemp(prefix="agent_test_"))

from src.agent import (
    Faktenblatt,
    LLMAntwort,
    LLMClient,
    LLMFehler,
    Nachricht,
    ToolAufrufAnfrage,
    ToolDefinition,
    demo_faktenblatt,
    sammle_faktenwerte,
)


@pytest.fixture
def faktenblatt() -> Faktenblatt:
    return demo_faktenblatt()


# --- Schritt 2: echte Daten (session-weit, nur lesend verwenden!) ------------

@pytest.fixture(scope="session")
def rohdaten():
    from src.seed_data import erzeuge_daten

    return erzeuge_daten()


@pytest.fixture(scope="session")
def pruefung(rohdaten):
    from src.datenpruefung import pruefe_monatsabschluss

    return pruefe_monatsabschluss(rohdaten)


@pytest.fixture(scope="session")
def analyse(pruefung):
    from src.variance_engine import Abweichungsanalyse

    return Abweichungsanalyse(pruefung.daten)


@pytest.fixture(scope="session")
def abschluss(rohdaten):
    from src.pipeline import fuehre_monatsabschluss_durch

    return fuehre_monatsabschluss_durch(rohdaten)


def cent(wert) -> int:
    """Decimal-Eurobetrag → Cent (exakt)."""
    return int(wert * 100)


@pytest.fixture
def faktenwerte(faktenblatt):
    return sammle_faktenwerte(faktenblatt)


@pytest.fixture
def kontext(faktenblatt):
    return faktenblatt.kontextzahlen()


class SkriptClient(LLMClient):
    """Liefert vorgegebene Antworten nacheinander und protokolliert alle Prompts."""

    anbieter = "skript"

    def __init__(self, antworten: Sequence[LLMAntwort | str]) -> None:
        self.modell = "skript-v1"
        self._antworten = [a if isinstance(a, LLMAntwort) else LLMAntwort(text=a) for a in antworten]
        self.aufrufe: list[tuple[str, list[Nachricht]]] = []

    def generiere(self, system: str, nachrichten: Sequence[Nachricht], tools: Sequence[ToolDefinition] | None = None) -> LLMAntwort:
        self.aufrufe.append((system, list(nachrichten)))
        if not self._antworten:
            return LLMAntwort(text="")
        return self._antworten.pop(0)

    def alle_texte(self) -> str:
        teile = []
        for system, nachrichten in self.aufrufe:
            teile.append(system)
            for n in nachrichten:
                teile.append(n.text)
                teile += [e.inhalt for e in n.tool_ergebnisse]
        return "\n".join(teile)


class DefekterClient(LLMClient):
    anbieter = "defekt"
    modell = "defekt"

    def generiere(self, system, nachrichten, tools=None) -> LLMAntwort:
        raise LLMFehler("Simulierter Ausfall")


def tool_antwort(*aufrufe: tuple[str, dict]) -> LLMAntwort:
    return LLMAntwort(
        text="",
        tool_aufrufe=[ToolAufrufAnfrage(id=f"t{i}", name=n, argumente=a) for i, (n, a) in enumerate(aufrufe)],
    )
