"""Orchestrierung des Monatsabschlusses.

Ablauf: Datenprüfung (Ampel) → Bereinigung → Forecast → Abweichungsanalyse
(inkl. Ist-Forecast-Vergleich) → Faktenblatt → Agent mit EngineBackend.
Bei roter Ampel wird die Analyse gesperrt (:class:`AbschlussGesperrt`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.agent import (
    Ampel,
    ControllerAgent,
    Faktenblatt,
    Kommentar,
    LLMClient,
    VarianceAIFehler,
    format_wert,
)
from src.config import DB_PFAD, Einstellungen
from src.datenpruefung import Pruefprotokoll, pruefe_monatsabschluss
from src.forecast_engine import (
    Forecastergebnis,
    forecast_basis,
    monatsreihe,
    prognostiziere_alle,
)
from src.seed_data import Datenbestand, erzeuge_daten
from src.variance_engine import (
    Abweichungsanalyse,
    EngineBackend,
    ForecastVergleich,
    Zeitraum,
)

logger = logging.getLogger("varianceai.pipeline")


class AbschlussGesperrt(VarianceAIFehler):
    """Die Datenprüfung hat ROT ergeben – keine Analyse auf ungeprüften Daten."""


@dataclass
class Abschlussergebnis:
    einstellungen: Einstellungen
    daten_roh: Datenbestand
    daten: Datenbestand  # bereinigt
    protokoll: Pruefprotokoll
    forecasts: dict[str, Forecastergebnis]
    analyse: Abweichungsanalyse
    faktenblatt: Faktenblatt
    backend: EngineBackend

    def agent(self, llm: LLMClient | None = None) -> ControllerAgent:
        """Agent mit kompaktem Faktenblatt (Prompt) und vollständigem Engine-Backend (Tools)."""
        return ControllerAgent(self.faktenblatt, llm, backend=self.backend)

    @property
    def periodenlabel(self) -> str:
        return self.faktenblatt.periodenbezeichnung


def speichere_kommentare(kommentare: list[Kommentar], pfad: Path | str) -> Path:
    """Speichert Kommentare (Entwürfe und Freigaben) revisionssicher als JSON."""
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps([k.model_dump(mode="json") for k in kommentare], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return pfad


def lade_kommentare(pfad: Path | str) -> list[Kommentar]:
    pfad = Path(pfad)
    if not pfad.exists():
        return []
    return [Kommentar.model_validate(k) for k in json.loads(pfad.read_text(encoding="utf-8"))]


def fuehre_monatsabschluss_durch(
    daten: Datenbestand | None = None,
    einstellungen: Einstellungen | None = None,
    zeitraum: Zeitraum = "Monat",
    trotz_rot: bool = False,
) -> Abschlussergebnis:
    """Führt den kompletten Abschlusszyklus deterministisch aus."""
    e = einstellungen or Einstellungen()
    roh = daten or erzeuge_daten(seed=e.seed, geschaeftsjahr=e.geschaeftsjahr, periode=e.periode)
    pruefung = pruefe_monatsabschluss(roh, e.geschaeftsjahr, e.periode, unternehmen=e.unternehmen)
    if pruefung.protokoll.ampel is Ampel.ROT and not trotz_rot:
        raise AbschlussGesperrt("Datenprüfung ROT – Analyse gesperrt.\n" + pruefung.protokoll.als_markdown())

    forecasts = prognostiziere_alle(pruefung.daten, e)
    perioden = tuple(range(1, e.periode + 1)) if zeitraum == "YTD" else (e.periode,)
    vergleiche = []
    for kennzahl, ergebnis in forecasts.items():
        basis = forecast_basis(ergebnis, perioden, monatsreihe(pruefung.daten, kennzahl), e.geschaeftsjahr, e.periode)
        if basis is not None:
            vergleiche.append(ForecastVergleich(kennzahl=kennzahl, version=basis[0], basis_cent=basis[1]))

    analyse = Abweichungsanalyse(pruefung.daten, e.geschaeftsjahr, e.periode, zeitraum, e.wesentlichkeit, vergleiche)
    infos = [f.info for f in forecasts.values()]
    faktenblatt = analyse.als_faktenblatt(pruefung.protokoll.als_datenqualitaet(), infos, e.unternehmen)
    return Abschlussergebnis(e, roh, pruefung.daten, pruefung.protokoll, forecasts, analyse, faktenblatt,
                             EngineBackend(analyse, infos))


def speichere_forecasts(ergebnis: Abschlussergebnis, pfad: Path | str = DB_PFAD) -> None:
    """Schreibt Forecast-Versionen und aktuelle Monatsprognose in die Tabelle ``forecast``."""
    zeilen = []
    gj = ergebnis.einstellungen.geschaeftsjahr
    for kennzahl, f in ergebnis.forecasts.items():
        for version, werte in f.versionen.items():
            zeilen += [dict(version=version, kennzahl=kennzahl, gjahr=gj, poper=p, wert_cent=w, unten_cent=None,
                            oben_cent=None, methode=None) for p, w in werte.items()]
        version = f"FC{ergebnis.einstellungen.periode:02d}"
        zeilen += [dict(version=version, kennzahl=kennzahl, gjahr=m.gjahr, poper=m.periode, wert_cent=m.wert_cent,
                        unten_cent=m.unten_cent, oben_cent=m.oben_cent, methode=f.methode.value) for m in f.monate]
    with sqlite3.connect(pfad) as con:
        pd.DataFrame(zeilen).to_sql("forecast", con, if_exists="replace", index=False)


if __name__ == "__main__":  # python -m src.pipeline
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ergebnis = fuehre_monatsabschluss_durch()
    print(ergebnis.protokoll.als_markdown())
    print("\n## Top-Treiber")
    for t in ergebnis.analyse.top_treiber(8).eintraege:
        print(f"{t.rang}. {t.objekt_bezeichnung}: {t.effektart.value} {format_wert(t.betrag, t.einheit, vorzeichen=True)}")
    agent = ergebnis.agent()
    print("\n## Kommentar-Entwürfe (Offline-Modus)")
    for objekt in ("UMS-SUED-ANT", "UMS-WEST-STE", "KST-4100", "KST-4200", "KST-4300", "KST-4400"):
        k = agent.kommentiere(objekt)
        print(f"[{k.status.value} | {k.quelle.value}] {k.text}")
    for frage in ("Warum liegt die Kostenstelle 4200 über Plan?", "Wo landen wir beim Umsatz am Jahresende?"):
        a = agent.beantworte(frage)
        print(f"\nFrage: {frage}\n[{a.quelle.value}] {a.text}")
