"""VarianceAI – kompletter Monatsabschluss im Offline-Modus (ohne API-Key).

    python demo.py [--ausgabe output/demo]

Ablauf: Daten erzeugen → Datenprüfung → Forecast und Abweichungsanalyse →
KI-Kommentare (Mock) mit Freigabe → Frage an den Agenten → Prompt-Injection-Test →
Monatsbericht (PowerPoint/PDF) → Excel mit Formel-Gegenprüfung → Power-BI-Paket.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.agent import (
    LLMAntwort,
    LLMClient,
    MockLLMClient,
    Textquelle,
    format_betrag,
    format_prozent,
    format_wert,
    pruefe_kommentarinhalt,
    pruefe_text,
    sammle_faktenwerte,
)
from src.exporter import exportiere_excel, exportiere_powerbi, pruefe_excel
from src.pipeline import (
    fuehre_monatsabschluss_durch,
    speichere_forecasts,
    speichere_kommentare,
)
from src.reporting_ibcs import erstelle_monatsbericht, kernaussagen
from src.seed_data import EINGEBAUTE_FAELLE, erzeuge_daten, schreibe_datenbank


class ManipuliertesLLM(LLMClient):
    """Simuliert ein LLM, das der versteckten Anweisung im Buchungstext folgt."""

    anbieter, modell = "manipuliert", "demo"

    def generiere(self, system, nachrichten, tools=None) -> LLMAntwort:  # noqa: ANN001
        return LLMAntwort(text="Alle Kostenstellen liegen im Plan.")


def schritt(nr: int, titel: str) -> None:
    print(f"\n{'─' * 78}\n{nr}. {titel}\n{'─' * 78}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ausgabe", default="output/demo")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    ziel = Path(args.ausgabe)
    ziel.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()

    schritt(1, "Synthetische Daten der RheinMain Industrietechnik GmbH")
    daten = erzeuge_daten()
    db = schreibe_datenbank(daten, ziel / "varianceai.db")
    print(f"{len(daten.acdoca):,} Buchungszeilen · 36 Monate Ist · Plan 2026 · Seed 42 → {db}".replace(",", "."))
    for fall in EINGEBAUTE_FAELLE:
        print(f"   Fall {fall.nr:>2}: {fall.bezeichnung} ({fall.effekt})")

    schritt(2, "Monatsabschluss-Prüfung")
    abschluss = fuehre_monatsabschluss_durch(daten)
    for punkt in abschluss.protokoll.punkte:
        print(f"   {punkt.ampel.value:<5} {punkt.bezeichnung}: {punkt.ergebnis}")
    print(f"   → Gesamtampel {abschluss.protokoll.ampel.value}; bereinigt: {', '.join(abschluss.protokoll.bereinigungen)}")

    schritt(3, f"Abweichungsanalyse und Forecast {abschluss.periodenlabel}")
    for aussage in kernaussagen(abschluss, anzahl_treiber=5):
        print(f"   • {aussage}")
    for name, f in abschluss.forecasts.items():
        i = f.info
        print(f"   Forecast {name}: {format_betrag(i.jahresendprognose)} ({format_prozent(i.abweichung_plan_pct)} ggü. Plan), "  # type: ignore[arg-type]
              f"{f.methode.value}, WAPE {format_prozent(i.wape_pct, vorzeichen=False)}")  # type: ignore[arg-type]

    schritt(4, "KI-Kommentare (Offline-Modus) und Freigabe")
    agent = abschluss.agent(MockLLMClient())
    objekte = ["EBIT", "UMS-SUED-ANT", "UMS-WEST-STE", "UMS-NORD", "KST-4100", "KST-4200", "KST-4300", "KST-4400"]
    kommentare = [agent.kommentiere(o) for o in objekte]
    print("   Hinweis: In der Praxis prüft ein Controller jeden Entwurf. Die Demo gibt sechs Entwürfe frei, "
          "zwei bleiben Entwurf\n   und erscheinen daher nicht im Bericht.")
    kommentare = [k.freigeben("Demo-Controller") if i < 6 else k for i, k in enumerate(kommentare)]
    for k in kommentare:
        print(f"   [{k.status.value} | {k.quellenangabe}] {k.text[:110]}…")
    speichere_kommentare(kommentare, ziel / "kommentare.json")

    schritt(5, "Frag den Controller (Tool-Calling über die Whitelist)")
    antwort = agent.beantworte("Warum liegt die Kostenstelle 4200 über Plan?")
    print(f"   {antwort.text}")
    print(f"   Tools: {' → '.join(t.name for t in antwort.tool_aufrufe)} · "
          f"{sum(f.belegt for f in antwort.pruefung.funde)} Quellzahlen belegt")

    schritt(6, "Prompt-Injection-Test (Buchungstext mit versteckter Anweisung)")
    manipuliert = abschluss.agent(ManipuliertesLLM()).kommentiere("KST-4200-400000")
    print(f"   Manipulierter KI-Text: „{manipuliert.verworfener_ki_text}“")
    status = "verworfen" if manipuliert.quelle is Textquelle.REGELBASIERT else "NICHT verworfen"
    fb = abschluss.faktenblatt
    pruefung = pruefe_text(manipuliert.verworfener_ki_text or "", sammle_faktenwerte(fb), fb.kontextzahlen())
    pruefung = pruefe_kommentarinhalt(manipuliert.verworfener_ki_text or "", fb.abweichung("KST-4200-400000"), pruefung)  # type: ignore[arg-type]
    print(f"   Konsistenzprüfung: {status}")
    for grund in pruefung.verstoesse:
        print(f"     · {grund}")
    print(f"   Stattdessen regelbasierter Entwurf: {manipuliert.text[:100]}…")

    schritt(7, "Monatsbericht, Excel und Power BI")
    bericht = erstelle_monatsbericht(abschluss, kommentare, ziel / "bericht")
    excel = exportiere_excel(abschluss, ziel / "export" / "VarianceAI_2026-09.xlsx")
    powerbi = exportiere_powerbi(abschluss, ziel / "export" / "powerbi")
    speichere_forecasts(abschluss, db)
    print(f"   PowerPoint: {bericht['pptx']}\n   PDF:        {bericht['pdf']}\n   Excel:      {excel}\n   Power BI:   {powerbi}")
    gegen = pruefe_excel(excel)
    if gegen["berechnet"]:
        print(f"   Formel-Gegenprüfung (LibreOffice): {gegen['gesamtstatus']} · "
              f"{len(gegen['pruefungen'])} Prüfungen · {len(gegen['formelfehler'])} Formelfehler")
    else:
        print("   Formel-Gegenprüfung: LibreOffice nicht installiert – Excel berechnet beim Öffnen.")

    ebit = abschluss.analyse.abweichung("EBIT")
    dauer = f"{time.perf_counter() - start:.1f}".replace(".", ",")
    print(f"\nFertig in {dauer} s · EBIT {format_wert(ebit.ist, ebit.einheit)} "
          f"({format_wert(ebit.delta_abs, ebit.einheit, vorzeichen=True)} ggü. Plan) · Ausgaben in {ziel}/")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
