"""Zentrale Konfiguration von VarianceAI.

Alle fachlichen Stellschrauben (Wesentlichkeit, Szenarien, Forecast-Parameter)
sind hier gebündelt und unveränderlich (``frozen``), damit Analysen
reproduzierbar bleiben.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

PROJEKT_WURZEL = Path(__file__).resolve().parent.parent
DATEN_VERZEICHNIS = PROJEKT_WURZEL / "data"
DB_PFAD = DATEN_VERZEICHNIS / "varianceai.db"

UNTERNEHMEN = "RheinMain Industrietechnik GmbH"
BUCHUNGSKREIS = "1000"
SEED = 42
GESCHAEFTSJAHR = 2026
BERICHTSPERIODE = 9  # Monatsabschluss September 2026


def prozent_gerundet(zaehler: int, nenner: int) -> Decimal | None:
    """|zaehler| / |nenner| in % mit einer Nachkommastelle (kaufmännisch gerundet)."""
    if nenner == 0:
        return None
    wert = Decimal(abs(zaehler)) * 100 / Decimal(abs(nenner))
    return wert.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Wesentlichkeit:
    """Wesentlichkeitsschwellen für Abweichungen.

    Standard: |Δ| ≥ 50.000 € **und** |Δ %| ≥ 5 %. Ohne Vergleichswert
    (z. B. Neuprodukt, ungeplanter Einmaleffekt) entscheidet nur der Betrag.
    """

    betrag_eur: Decimal = Decimal("50000")
    prozent: Decimal = Decimal("5")
    verknuepfung: Literal["und", "oder"] = "und"

    def ist_wesentlich(self, delta_cent: int, vergleich_cent: int) -> bool:
        betrag_ok = abs(delta_cent) >= self.betrag_eur * 100
        pct = prozent_gerundet(delta_cent, vergleich_cent)
        if pct is None:
            return betrag_ok
        pct_ok = pct >= self.prozent
        return (betrag_ok and pct_ok) if self.verknuepfung == "und" else (betrag_ok or pct_ok)


@dataclass(frozen=True)
class Szenarioannahmen:
    """Deterministische Szenarioannahmen für die Restmonate (in % ggü. Base).

    Für Erlöse/Ergebnis gilt Best = +Chance, Worst = −Risiko; für Kosten
    entsprechend umgekehrt.
    """

    chance_pct: Decimal = Decimal("3")
    risiko_pct: Decimal = Decimal("5")


@dataclass(frozen=True)
class ForecastEinstellungen:
    min_trainingslaenge: int = 24  # zwei vollständige Saisonzyklen
    backtest_horizont: int = 3
    konfidenzniveau_pct: Decimal = Decimal("80")
    simulationen: int = 2000
    versionen: tuple[int, ...] = (3, 6, 9)  # Forecast-Versionen nach Quartalsende
    saisonlaenge: int = 12


@dataclass(frozen=True)
class Einstellungen:
    geschaeftsjahr: int = GESCHAEFTSJAHR
    periode: int = BERICHTSPERIODE
    seed: int = SEED
    unternehmen: str = UNTERNEHMEN
    wesentlichkeit: Wesentlichkeit = field(default_factory=Wesentlichkeit)
    szenarien: Szenarioannahmen = field(default_factory=Szenarioannahmen)
    forecast: ForecastEinstellungen = field(default_factory=ForecastEinstellungen)
