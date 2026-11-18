"""VarianceAI – Kommandozeile für den Monatsabschluss.

Beispiele::

    python run_agent.py daten                                 # synthetische Daten → data/varianceai.db
    python run_agent.py pruefen                               # Prüfprotokoll mit Ampel
    python run_agent.py analyse --top 8                       # Top-Treiber
    python run_agent.py analyse --objekt KST-4200             # Abweichung mit Drill-down
    python run_agent.py forecast --kennzahl Umsatz            # Jahresendprognose
    python run_agent.py kommentare                            # Entwürfe erzeugen und speichern
    python run_agent.py kommentare --freigeben "Max Mustermann" --objekte EBIT KST-4100
    python run_agent.py frage "Warum liegt die Kostenstelle 4200 über Plan?"
    python run_agent.py bericht                               # PowerPoint und PDF (nur freigegebene Kommentare)
    python run_agent.py export --pruefen                      # Excel (mit Gegenprüfung) und Power-BI-Paket

Ohne API-Key läuft alles im Offline-Modus (``--anbieter mock``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.agent import (
    Ampel,
    Freigabestatus,
    erstelle_llm_client,
    format_prozent,
    format_wert,
    lade_umgebung,
)
from src.config import DB_PFAD, Einstellungen
from src.pipeline import (
    Abschlussergebnis,
    AbschlussGesperrt,
    fuehre_monatsabschluss_durch,
    lade_kommentare,
    speichere_kommentare,
)
from src.seed_data import erzeuge_daten, lade_datenbank, schreibe_datenbank

AUSGABE = Path("output")


def _abschluss(args: argparse.Namespace) -> Abschlussergebnis:
    einstellungen = replace(Einstellungen(), periode=args.periode, seed=args.seed)
    db = Path(args.db)
    if db.exists() and not args.neu:
        daten = lade_datenbank(db)
        einstellungen = replace(einstellungen, periode=daten.periode, seed=daten.meta["seed"])
    else:
        daten = erzeuge_daten(seed=args.seed, periode=args.periode)
    return fuehre_monatsabschluss_durch(daten, einstellungen, "YTD" if args.ytd else "Monat", trotz_rot=args.trotz_rot)


def _kommentardatei(args: argparse.Namespace) -> Path:
    return Path(args.ausgabe) / "kommentare.json"


# --- Befehle ------------------------------------------------------------------


def befehl_daten(args: argparse.Namespace) -> int:
    daten = erzeuge_daten(seed=args.seed, periode=args.periode)
    pfad = schreibe_datenbank(daten, args.db)
    zeilen = f"{len(daten.acdoca):,}".replace(",", ".")
    print(f"{zeilen} Buchungszeilen nach {pfad} geschrieben (Seed {args.seed}, Periode {args.periode}).")
    return 0


def befehl_pruefen(args: argparse.Namespace) -> int:
    args.trotz_rot = True
    abschluss = _abschluss(args)
    print(abschluss.protokoll.als_markdown())
    return 2 if abschluss.protokoll.ampel is Ampel.ROT else 0


def befehl_analyse(args: argparse.Namespace) -> int:
    analyse = _abschluss(args).analyse
    if args.objekt:
        a = analyse.abweichung(args.objekt)
        print(f"{a.bezeichnung} ({a.vergleich.value}, {analyse.periodenlabel})")
        print(f"  Ist {format_wert(a.ist, a.einheit)} · Vergleich {format_wert(a.vergleichswert, a.einheit)} · "
              f"Δ {format_wert(a.delta_abs, a.einheit, vorzeichen=True)}"  # type: ignore[arg-type]
              + (f" ({format_prozent(a.delta_pct)})" if a.delta_pct is not None else "")
              + f" · {a.bewertung.value if a.bewertung else ''}{' · wesentlich' if a.wesentlich else ''}")
        for t in sorted(a.treiber, key=lambda t: -abs(t.betrag)):
            print(f"    {t.effektart.value:<28} {format_wert(t.betrag, a.einheit, vorzeichen=True):>14}")
        for k in analyse.drilldown(a.id):
            print(f"  ↳ {k.id:<26} {k.bezeichnung:<50} {format_wert(k.delta_abs, k.einheit, vorzeichen=True):>14}")  # type: ignore[arg-type]
        return 0
    print(f"Top-Treiber {analyse.periodenlabel}")
    for e in analyse.top_treiber(args.top).eintraege:
        print(f"  {e.rang:>2}. {e.objekt_bezeichnung:<52} {e.effektart.value:<26} "
              f"{format_wert(e.betrag, e.einheit, vorzeichen=True):>14}{'  (Einmaleffekt)' if e.einmaleffekt else ''}")
    return 0


def befehl_forecast(args: argparse.Namespace) -> int:
    from src.agent import regelbasierter_forecast_text  # noqa: PLC0415

    abschluss = _abschluss(args)
    f = abschluss.forecasts[args.kennzahl]
    print(regelbasierter_forecast_text(f.info))
    for s in f.info.szenarien:
        print(f"  {s.name:<5} {format_wert(s.wert, f.info.einheit):>14}  {s.annahme}")
    for g in f.guete:
        perioden = ", ".join(str(p) for p in g.perioden)
        print(f"  Güte {g.version}: WAPE {format_prozent(Decimal(str(g.wape_pct)), vorzeichen=False)}, "
              f"Bias {format_prozent(Decimal(str(g.bias_pct)))} (Perioden {perioden})")
    return 0


def befehl_kommentare(args: argparse.Namespace) -> int:
    abschluss = _abschluss(args)
    agent = abschluss.agent(erstelle_llm_client(args.anbieter))
    datei = _kommentardatei(args)
    vorhanden = {k.objekt_id: k for k in lade_kommentare(datei)}
    objekte = args.objekte or [a.id for a in abschluss.faktenblatt.wesentliche()
                               if a.ebene in ("Gesamt", "Region", "Produktgruppe", "Kostenstelle")]
    for objekt in objekte:
        k = vorhanden.get(objekt)
        if k is None or k.status is Freigabestatus.ENTWURF:
            k = agent.kommentiere(objekt)
        if args.freigeben and k.status is Freigabestatus.ENTWURF:
            k = k.freigeben(args.freigeben)
        vorhanden[objekt] = k
        print(f"[{k.status.value} | {k.quellenangabe}] {objekt}: {k.text}")
    speichere_kommentare(list(vorhanden.values()), datei)
    print(f"\n{len(vorhanden)} Kommentare gespeichert in {datei}.")
    return 0


def befehl_frage(args: argparse.Namespace) -> int:
    antwort = _abschluss(args).agent(erstelle_llm_client(args.anbieter)).beantworte(args.frage)
    print(antwort.text)
    print(f"\nQuelle: {antwort.quellenangabe} · Faktenprüfung "
          f"{'bestanden' if antwort.pruefung.bestanden else 'nicht bestanden'} · {antwort.modell}")
    print("Tools:", ", ".join(f"{t.name}({', '.join(f'{k}={v}' for k, v in t.argumente.items())})"
                              + ("" if t.erlaubt else " [abgelehnt]") for t in antwort.tool_aufrufe) or "–")
    print("Quellzahlen:", "; ".join(f"{f.text} ← {f.beleg}" for f in antwort.pruefung.funde) or "–")
    return 0


def befehl_diagnose(args: argparse.Namespace) -> int:  # noqa: ARG001
    from src.diagnose import bericht, pruefe_umgebung  # noqa: PLC0415

    print(bericht())
    return 0 if all(s.ok for s in pruefe_umgebung()) else 1


def befehl_bericht(args: argparse.Namespace) -> int:
    from src.reporting_ibcs import erstelle_monatsbericht  # noqa: PLC0415

    abschluss = _abschluss(args)
    kommentare = lade_kommentare(_kommentardatei(args))
    dateien = erstelle_monatsbericht(abschluss, kommentare, Path(args.ausgabe) / "bericht")
    freigegeben = sum(k.status is Freigabestatus.FREIGEGEBEN for k in kommentare)
    print(f"Monatsbericht erstellt ({freigegeben} freigegebene von {len(kommentare)} Kommentaren):")
    print(f"  {dateien['pptx']}\n  {dateien['pdf']}")
    return 0


def befehl_export(args: argparse.Namespace) -> int:
    from src.exporter import (  # noqa: PLC0415
        exportiere_excel,
        exportiere_powerbi,
        pruefe_excel,
    )

    abschluss = _abschluss(args)
    ziel = Path(args.ausgabe) / "export"
    excel = exportiere_excel(abschluss, ziel / f"VarianceAI_{abschluss.einstellungen.geschaeftsjahr}-"
                                              f"{abschluss.einstellungen.periode:02d}.xlsx")
    paket = exportiere_powerbi(abschluss, ziel / "powerbi")
    print(f"Excel: {excel}\nPower BI: {paket}")
    if args.pruefen:
        ergebnis = pruefe_excel(excel)
        if not ergebnis["berechnet"]:
            print("Gegenprüfung übersprungen: LibreOffice nicht installiert (Excel berechnet beim Öffnen).")
            return 0
        print(f"Formel-Gegenprüfung: {ergebnis['gesamtstatus']} · Formelfehler: {len(ergebnis['formelfehler'])}")
        for name, status in ergebnis["pruefungen"].items():
            print(f"  {status:<6} {name}")
        return 0 if ergebnis["gesamtstatus"] == "OK" and not ergebnis["formelfehler"] else 1
    return 0


# --- Parser -------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_agent.py", description="VarianceAI – Monatsabschluss per Kommandozeile")
    p.add_argument("--periode", type=int, default=9, help="Berichtsperiode 1–12 (Standard: 9 = September 2026)")
    p.add_argument("--seed", type=int, default=42, help="Seed der synthetischen Daten")
    p.add_argument("--ytd", action="store_true", help="kumulierte Betrachtung Januar bis Berichtsperiode")
    p.add_argument("--db", default=str(DB_PFAD), help="SQLite-Datenbank (wird genutzt, falls vorhanden)")
    p.add_argument("--neu", action="store_true", help="Daten neu erzeugen statt die Datenbank zu laden")
    p.add_argument("--anbieter", default=None, choices=["mock", "claude", "gemini"], help="LLM-Anbieter")
    p.add_argument("--ausgabe", default=str(AUSGABE), help="Ausgabeordner")
    p.add_argument("--trotz-rot", action="store_true", dest="trotz_rot", help="Analyse trotz roter Ampel (nicht empfohlen)")
    p.add_argument("-v", "--verbose", action="store_true")
    befehle = p.add_subparsers(dest="befehl", required=True)
    befehle.add_parser("daten", help="synthetische Daten erzeugen und speichern").set_defaults(fn=befehl_daten)
    befehle.add_parser("pruefen", help="Monatsabschluss-Prüfung").set_defaults(fn=befehl_pruefen)
    a = befehle.add_parser("analyse", help="Abweichungsanalyse")
    a.add_argument("--objekt", help="Objekt-ID, z. B. UMS-SUED-ANT oder KST-4200")
    a.add_argument("--top", type=int, default=8)
    a.set_defaults(fn=befehl_analyse)
    f = befehle.add_parser("forecast", help="Jahresendprognose")
    f.add_argument("--kennzahl", default="Umsatz", choices=["Umsatz", "Deckungsbeitrag", "Kosten"])
    f.set_defaults(fn=befehl_forecast)
    k = befehle.add_parser("kommentare", help="KI-Kommentare erzeugen und (namentlich) freigeben")
    k.add_argument("--objekte", nargs="*", help="Objekt-IDs (Standard: wesentliche Übersichtsobjekte)")
    k.add_argument("--freigeben", metavar="NAME", help="Entwürfe namentlich freigeben")
    k.set_defaults(fn=befehl_kommentare)
    q = befehle.add_parser("frage", help="Frage an den Controller-Agenten")
    q.add_argument("frage")
    q.set_defaults(fn=befehl_frage)
    befehle.add_parser("bericht", help="Monatsbericht PowerPoint und PDF").set_defaults(fn=befehl_bericht)
    e = befehle.add_parser("export", help="Excel und Power-BI-Paket")
    e.add_argument("--pruefen", action="store_true", help="Formel-Gegenprüfung mit LibreOffice")
    e.set_defaults(fn=befehl_export)
    befehle.add_parser("diagnose", help="Python-Umgebung prüfen (Pakete, Versionen, Importfehler)").set_defaults(
        fn=befehl_diagnose)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    lade_umgebung()
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except AbschlussGesperrt as fehler:
        print(fehler, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
