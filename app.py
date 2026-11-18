"""VarianceAI – Controlling-Cockpit (Streamlit).

Start: ``streamlit run app.py``

Läuft ohne API-Key vollständig im Offline-Modus (deterministischer Mock-Agent).
Alle Zahlen stammen aus den Python-Engines; der KI-Agent formuliert nur.
Gestaltung und Farbregeln: ``src/ui_design.py``, Diagramme: ``src/ui_diagramme.py``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit import config as st_config

from demo import ManipuliertesLLM
from desktop import praeferenzen
from src import ui_design as ui
from src import ui_diagramme as dia
from src.agent import (
    Abweichung,
    Ampel,
    FreigabeFehler,
    Freigabestatus,
    Kennzahlart,
    Kommentar,
    MockLLMClient,
    Textquelle,
    berichtsfaehige_kommentare,
    erkenne_injektionsmuster,
    erstelle_llm_client,
    format_betrag,
    format_prozent,
    format_wert,
    lade_umgebung,
    pruefe_kommentarinhalt,
    pruefe_text,
    sammle_faktenwerte,
    vergleichstext,
)
from src.config import Einstellungen, Wesentlichkeit
from src.pipeline import Abschlussergebnis, fuehre_monatsabschluss_durch
from src.reporting_ibcs import erstelle_monatsbericht, kernaussagen
from src.seed_data import erzeuge_daten

st.set_page_config(page_title="VarianceAI – Controlling-Cockpit", page_icon=str(Path(__file__).parent / "static" / "favicon.png"), layout="wide",
                   initial_sidebar_state="expanded")


def wende_streamlit_thema_an(darstellung: str) -> bool:
    """Setzt das Streamlit-Thema (hell/dunkel) zur Laufzeit; ``True``, wenn sich etwas geändert hat.

    Hinweis: Die Optionen gelten serverweit – passend für die lokale Desktop-App mit einem Benutzer.
    Im Mehrbenutzerbetrieb würde das Thema stattdessen fest in ``.streamlit/config.toml`` gesetzt.
    """
    geaendert = False
    for schluessel, wert in ui.STREAMLIT_THEMA[darstellung].items():
        try:
            if st_config.get_option(schluessel) != wert:
                st_config.set_option(schluessel, wert)
                geaendert = True
        except Exception:  # noqa: BLE001 – unbekannte Option in älterer Streamlit-Version: überspringen
            continue
    return geaendert


# Laufzeit: "desktop" (vom Launcher gesetzt) und "lokal" erlauben den Umschalter hell/dunkel. Im Webbetrieb
# (z. B. Streamlit Community Cloud) teilen sich alle Besucher einen Server – dort bleibt das Thema fest
# (.streamlit/config.toml) und es werden keine Einstellungen gespeichert.
LAUFZEIT = os.getenv("AGENT_LAUFZEIT", "web").strip().lower()
UMSCHALTBAR = LAUFZEIT in ("desktop", "lokal")

if not UMSCHALTBAR:
    darstellung = "dunkel"
    ui.setze_modus(darstellung)
else:
    if "darstellung" not in st.session_state:
        st.session_state["darstellung"] = praeferenzen.lade()["darstellung"]
    darstellung = st.session_state["darstellung"]
    ui.setze_modus(darstellung)
    if wende_streamlit_thema_an(darstellung):
        st.rerun()
st.html(ui.stylesheet())

try:  # Vorabprüfung: ohne funktionierendes pyarrow kann Streamlit weder Tabellen noch Diagramme übertragen
    import pyarrow  # noqa: F401
except Exception as _fehler:  # noqa: BLE001
    import sys

    st.error(f"Die Python-Umgebung ist nicht konsistent: pyarrow lässt sich nicht laden ({_fehler}).")
    st.markdown(
        f"Aktiver Interpreter: `{sys.executable}`\n\n"
        "Typische Ursache: ein altes **pyarrow** (gegen NumPy 1.x gebaut) neben **NumPy 2**. Lösung:\n\n"
        "```bash\npip install -U \"pyarrow>=16\"\n```\n"
        "oder – sauberer – eine eigene virtuelle Umgebung:\n\n"
        "```bash\npython3 -m venv .venv && source .venv/bin/activate\npip install -r requirements.txt\n"
        "python -m streamlit run app.py\n```"
    )
    st.stop()
lade_umgebung()
logger = logging.getLogger("varianceai.app")

MONATSNAMEN = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember"]
AMPEL_TON: dict[Ampel, ui.Ton] = {Ampel.GRUEN: "gut", Ampel.GELB: "warnung", Ampel.ROT: "schlecht"}
WURZELN = {"Umsatz": "UMS", "Deckungsbeitrag": "DB", "Kosten Kostenstellen": "KST", "EBIT": "EBIT"}
KOMMENTIERBARE_EBENEN = ("Gesamt", "Region", "Produktgruppe", "Kostenstelle")
BEISPIELFRAGEN = (
    "Warum liegt die Kostenstelle 4200 über Plan?",
    "Warum ist der Deckungsbeitrag gesunken?",
    "Wo landen wir beim Umsatz am Jahresende?",
    "Was sind die größten Belastungen?",
)


# =============================================================================
# Daten, Zustand und Hilfsfunktionen
# =============================================================================


@st.cache_resource(show_spinner="Monatsabschluss wird berechnet …")
def abschluss_laden(periode: int, seed: int, zeitraum: str, schwelle_eur: int, schwelle_pct: float) -> Abschlussergebnis:
    einstellungen = Einstellungen(
        periode=periode, seed=seed,
        wesentlichkeit=Wesentlichkeit(betrag_eur=Decimal(schwelle_eur), prozent=Decimal(str(schwelle_pct))),
    )
    daten = erzeuge_daten(seed=seed, geschaeftsjahr=einstellungen.geschaeftsjahr, periode=periode)
    return fuehre_monatsabschluss_durch(daten, einstellungen, zeitraum)  # type: ignore[arg-type]


def kommentarspeicher(schluessel: str) -> dict[str, Kommentar]:
    return st.session_state.setdefault("kommentare", {}).setdefault(schluessel, {})


def teur(wert: Decimal | int | float) -> str:
    return format_betrag(Decimal(str(wert)), "T€")


def guenstig(betrag: Decimal | float, art: Kennzahlart) -> bool | None:
    return None if betrag == 0 else (betrag > 0) != (art is Kennzahlart.KOSTEN)


def zeige(html: str) -> None:
    st.html(html)


def diagramm(chart, beschreibung: str) -> None:  # noqa: ANN001
    st.altair_chart(chart, theme=None, width="stretch", alt=beschreibung)


def abweichungstabelle(objekte: list[Abweichung]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Objekt": a.bezeichnung, "Ebene": a.ebene,
            "Δ": format_wert(a.delta_abs, a.einheit, vorzeichen=True),  # type: ignore[arg-type]
            "Δ %": format_prozent(a.delta_pct) if a.delta_pct is not None else "–",
            "Bewertung": a.bewertung.value if a.bewertung else "",
            "Haupttreiber": a.haupttreiber.effektart.value if a.haupttreiber else "–",
        }
        for a in objekte
    ])


def audit(kontext: str) -> list[ui.Logeintrag]:
    """Append-only-Protokoll der Sitzung (Einträge werden nie verändert oder gelöscht)."""
    return st.session_state.setdefault("audit", {}).setdefault(kontext, [])


def protokolliere(kontext: str, art: str, ton: ui.Ton, objekt: str, grund: str, wert: str = "",
                  zeit: datetime | None = None) -> None:
    audit(kontext).append(ui.Logeintrag((zeit or datetime.now()).strftime("%H:%M:%S"), art, ton, objekt, grund, wert))


def ablehnungsgruende(text: str, objekt_id: str) -> list[str]:
    """Tatsächliche Gründe, aus denen Zahlen- und Konsistenzprüfung einen KI-Text verwerfen."""
    fb = abschluss.faktenblatt
    abw = fb.abweichung(objekt_id)
    pruefung = pruefe_text(text, sammle_faktenwerte(fb), fb.kontextzahlen())
    if abw is not None:
        pruefung = pruefe_kommentarinhalt(text, abw, pruefung)
    return pruefung.verstoesse


# =============================================================================
# Seitenleiste
# =============================================================================

with st.sidebar:
    zeige('<div class="va va-marke" style="margin:4px 0 14px"><div class="va-logo">Δ</div><div><b>VarianceAI</b>'
          '<small>Plan-Ist · Forecast · Reporting<br>Variance Analysis &amp; Forecasting</small></div></div>')
    periode = st.selectbox("Berichtsperiode 2026", list(range(1, 13)), index=8, format_func=lambda p: MONATSNAMEN[p - 1])
    zeitraum = st.radio("Zeitraum", ["Monat", "YTD"], horizontal=True)
    with st.expander("Einstellungen"):
        schwelle_eur = st.number_input("Wesentlich ab |Δ| in €", min_value=0, value=50_000, step=10_000)
        schwelle_pct = st.number_input("Wesentlich ab |Δ %|", min_value=0.0, value=5.0, step=0.5)
        anbieter = st.selectbox("KI-Anbieter", ["mock", "claude", "gemini"],
                                format_func={"mock": "Demo ohne KI-Schlüssel", "claude": "Anthropic Claude",
                                             "gemini": "Google Gemini"}.get)
        seed = st.number_input("Seed der Demodaten", min_value=0, value=42, step=1)
    llm = erstelle_llm_client(anbieter)
    if anbieter != "mock" and isinstance(llm, MockLLMClient):
        st.warning("Kein API-Key bzw. SDK gefunden – Demo-Modus aktiv.")
    st.caption("Alle Daten sind synthetisch. Die RheinMain Industrietechnik GmbH ist fiktiv.")
    with st.expander("Systemprüfung"):
        from src.diagnose import bericht  # noqa: PLC0415

        st.caption(f"Modell: `{llm.anbieter}:{llm.modell}`")
        st.code(bericht(), language=None)

try:
    abschluss = abschluss_laden(periode, int(seed), zeitraum, int(schwelle_eur), float(schwelle_pct))
except Exception as fehler:  # noqa: BLE001 – Sperre durch rote Ampel o. Ä. sichtbar machen
    st.error(f"Monatsabschluss konnte nicht berechnet werden: {fehler}")
    st.stop()

kontext = f"{periode}-{seed}-{zeitraum}-{schwelle_eur}-{schwelle_pct}"
agent = abschluss.agent(llm)
analyse = abschluss.analyse
protokoll = abschluss.protokoll
speicher = kommentarspeicher(kontext)
offline = isinstance(llm, MockLLMClient)

# Startereignisse des Audit-Protokolls (einmal je Kontext)
if not audit(kontext):
    zeit = protokoll.erstellt_am.replace(tzinfo=None)
    for p in protokoll.punkte:
        if p.ampel is not Ampel.GRUEN:
            protokolliere(kontext, "PRÜFUNG", AMPEL_TON[p.ampel], p.bezeichnung, p.ergebnis, p.ampel.value, zeit)
    for b in protokoll.bereinigungen:
        protokolliere(kontext, "BEREINIGT", "neutral", "Datenbereinigung", b, "", zeit)
    for a in analyse.knoten.values():
        for t in a.datentexte:
            if muster := erkenne_injektionsmuster(t):
                protokolliere(kontext, "GESCHÜTZT", "akzent", a.id,
                              f"Buchungstext mit Steuerungsmuster ({', '.join(muster)}) – nur bereinigt an das LLM",
                              "", zeit)
    protokolliere(kontext, "ANALYSE", "neutral", "Abweichungsanalyse",
                  f"{len(analyse.knoten)} Analyseobjekte, Effekte centgenau additiv",
                  f"{sum(a.wesentlich for a in analyse.knoten.values())} wesentlich", zeit)
    for name, fc in abschluss.forecasts.items():
        protokolliere(kontext, "FORECAST", "neutral", name,
                      f"Methode {fc.methode.value} per Backtesting gewählt",
                      f"WAPE {format_prozent(fc.info.wape_pct, vorzeichen=False)}", zeit)  # type: ignore[arg-type]

# =============================================================================
# Kopfzeile und Kennzahlen-Leiste
# =============================================================================

zeilen = {z.kennzahl: z for z in analyse.ergebnisrechnung}
anzahl_ok = sum(p.ampel is Ampel.GRUEN for p in protokoll.punkte)
modus_pill = ui.pill("Demo-Modus" if offline else f"KI: {llm.modell}", "akzent", punkt=True)
if UMSCHALTBAR:
    kopf_links, kopf_rechts = st.columns([4.4, 1.5], vertical_alignment="center", gap="small")
    with kopf_rechts:
        wahl = st.segmented_control(
            "Darstellung", list(ui.MODI), default=darstellung, key="darstellung_wahl", width="stretch",
            help="Hell oder dunkel – die Wahl wird gespeichert und gilt auch beim nächsten Start.",
            format_func={"dunkel": ":material/dark_mode: Dunkel", "hell": ":material/light_mode: Hell"}.get,
        )
        if wahl and wahl != darstellung:  # Auswahl merken (auch für den nächsten App-Start) und neu zeichnen
            st.session_state["darstellung"] = wahl
            praeferenzen.speichere({"darstellung": wahl})
            st.rerun()
else:
    kopf_links = st.container()
with kopf_links:
    zeige(ui.kopfzeile(
        "VarianceAI", "Plan-Ist-Analyse und Forecast · Variance Analysis & Forecasting",
        f"Monatsabschluss {abschluss.periodenlabel}",
        f"{'Monatswerte' if zeitraum == 'Monat' else 'kumuliert Jan–' + MONATSNAMEN[periode - 1][:3]} · "
        "RheinMain Industrietechnik GmbH (fiktiv)",
        [modus_pill, ui.pill(f"Datenprüfung {protokoll.ampel.value}", AMPEL_TON[protokoll.ampel], punkt=True)],
    ))


def kpi_aus_zeile(name: str, label: str, hervorheben: bool = False, en: str = "") -> ui.Kpi:
    z = zeilen[name]
    art = Kennzahlart.KOSTEN if name.startswith("Kosten") else Kennzahlart.ERGEBNIS
    pct_pl = Decimal(z.delta_plan) * 100 / abs(Decimal(z.plan)) if z.plan else None
    pct_py = Decimal(z.delta_vorjahr) * 100 / abs(Decimal(z.vorjahr)) if z.vorjahr else None
    reihe = {"Kosten Kostenstellen": "Kosten"}.get(name, name)
    monate = dia.monatsdaten(abschluss, reihe)
    bis = monate[monate["m"] <= periode]
    return ui.Kpi(
        label=label, wert=teur(Decimal(z.ist) / 100),
        delta=f"{format_prozent(pct_pl)} ggü. PL" if pct_pl is not None else None,
        ton=ui.ton_fuer(guenstig(Decimal(z.delta_plan), art)),
        fuss=f"PY {format_prozent(pct_py)}" if pct_py is not None else "",
        verlauf=list(bis["ac"].fillna(0)), vergleich=list(bis["pl"].fillna(0)), hervorheben=hervorheben, en=en,
    )


kpis = [kpi_aus_zeile("Umsatz", "Umsatz", en="Revenue"),
        kpi_aus_zeile("Deckungsbeitrag", "Deckungsbeitrag", en="Contribution Margin"),
        kpi_aus_zeile("Kosten Kostenstellen", "Kosten", en="Cost Center Costs"), kpi_aus_zeile("EBIT", "EBIT", True)]
if "Umsatz" in abschluss.forecasts:
    fi = abschluss.forecasts["Umsatz"].info
    jahr = dia.monatsdaten(abschluss, "Umsatz")
    fc_werte = {m.periode: m.wert_cent / 100_000 for m in abschluss.forecasts["Umsatz"].monate}
    kpis.append(ui.Kpi(
        label="Jahresumsatz (FC)", wert=format_betrag(fi.jahresendprognose, "Mio. €"),  # type: ignore[arg-type]
        delta=f"{format_prozent(fi.abweichung_plan_pct)} ggü. PL",  # type: ignore[arg-type]
        ton=ui.ton_fuer(guenstig(fi.abweichung_plan_abs, Kennzahlart.ERLOES)),  # type: ignore[arg-type]
        fuss=f"PL {format_betrag(fi.plan_gesamtjahr, 'Mio. €')}",  # type: ignore[arg-type]
        verlauf=[v if pd.notna(v) else fc_werte.get(m, 0) for m, v in zip(jahr["m"], jahr["ac"], strict=True)],
        vergleich=list(jahr["pl"].fillna(0)), en="Full-Year Revenue",
    ))
zeige(ui.kpi_leiste(kpis))

_REIHENFOLGE = {"Plan-Ist": 0, "Ist-Forecast": 1, "Ist-Vorjahr": 2}
wesentliche = sorted(  # kommentierbare Objekte: EBIT zuerst, dann Plan-Ist vor Forecast und Vorjahr, je nach Betrag
    (a for a in abschluss.faktenblatt.wesentliche() if a.ebene in KOMMENTIERBARE_EBENEN),
    key=lambda a: (a.id != "EBIT", _REIHENFOLGE.get(a.vergleich.value, 3), -abs(a.delta_abs or 0)),
)


def sicher(seite, name: str) -> None:  # noqa: ANN001
    """Fehlergrenze je Tab: ein Fehler (z. B. eine defekte Bibliothek) legt nicht das ganze Cockpit lahm."""
    try:
        seite()
    except Exception as fehler:  # noqa: BLE001 – Rerun/Stop von Streamlit sind BaseException und bleiben unberührt
        logger.exception("Tab %s konnte nicht dargestellt werden", name)
        st.error(f"**{name}** konnte nicht dargestellt werden – {type(fehler).__name__}: {fehler}")
        st.info("Meist ist ein Paket der Python-Umgebung veraltet oder beschädigt. Prüfen mit "
                "`python run_agent.py diagnose`, beheben mit `pip install -U -r requirements.txt` "
                "(am besten in einer frischen virtuellen Umgebung).")
        with st.expander("Technische Details"):
            st.exception(fehler)


tabs = st.tabs(["Monatsübersicht", "Abweichungsanalyse", "Forecast", "KI-Kommentare", "Frag den Controller",
                "Agent & Audit"])
tab_uebersicht, tab_abweichung, tab_forecast, tab_kommentare, tab_frage, tab_audit = tabs

# =============================================================================
# 1 · Monatsübersicht
# =============================================================================

def _seite_uebersicht() -> None:
    zeige(ui.leitfrage("Wie steht das Ergebnis im Berichtsmonat – und was treibt die Abweichung zum Plan?", "Monthly Overview"))
    links, rechts = st.columns([1.75, 1], gap="medium")
    ebit = analyse.abweichung("EBIT")
    z_ebit = zeilen["EBIT"]
    with links:
        with st.container(border=True):
            pct_py = Decimal(z_ebit.delta_vorjahr) * 100 / abs(Decimal(z_ebit.vorjahr)) if z_ebit.vorjahr else None
            zeige(ui.held(
                f"EBIT · AC · {abschluss.periodenlabel}", format_betrag(ebit.ist, "T€").removesuffix(" T€"), "T€",
                [ui.pill(f"{format_betrag(ebit.delta_abs, 'T€', vorzeichen=True)} ggü. PL",  # type: ignore[arg-type]
                         ui.ton_fuer(guenstig(ebit.delta_abs or 0, ebit.art))),
                 ui.pill(f"{format_prozent(ebit.delta_pct)} ggü. PL", ui.ton_fuer(guenstig(ebit.delta_abs or 0, ebit.art)))
                 if ebit.delta_pct is not None else "",
                 ui.pill(f"{format_prozent(pct_py)} ggü. PY", "leise") if pct_py is not None else ""],
                f"EBIT-Brücke Plan → Ist: {format_betrag(ebit.delta_abs, 'T€', vorzeichen=True)}",  # type: ignore[arg-type]
                "in T€ · günstig grün, ungünstig rot", en="EBIT Bridge",
            ))
            diagramm(dia.bruecke(ebit, hoehe=330), "Wasserfall der EBIT-Abweichung vom Plan zum Ist nach Treibern")
        with st.container(border=True):
            k1, k2 = st.columns([1, 3])
            k1.html('<div class="va va-ueberschrift" style="margin-top:8px"><span>Monatsverlauf</span></div>')
            verlauf_kz = k2.segmented_control("Kennzahl im Monatsverlauf", ["Umsatz", "Deckungsbeitrag", "EBIT", "Kosten"],
                                              default="EBIT", key="verlauf_kz", label_visibility="collapsed")
            diagramm(dia.monatsverlauf(abschluss, verlauf_kz or "EBIT"),
                     f"Monatswerte {verlauf_kz} {abschluss.einstellungen.geschaeftsjahr}: AC, PL und PY")
            st.caption("in T€ · AC gefüllt · PL umrandet · PY als graue Marke")
        tabellenzeilen = []
        for z in analyse.ergebnisrechnung:
            art = Kennzahlart.KOSTEN if z.kennzahl.startswith(("Kosten", "Herstell")) else Kennzahlart.ERGEBNIS
            t_pl = ui.ton_fuer(guenstig(Decimal(z.delta_plan), art))
            t_py = ui.ton_fuer(guenstig(Decimal(z.delta_vorjahr), art))
            tabellenzeilen.append([
                (z.kennzahl, None), (teur(Decimal(z.ist) / 100).removesuffix(" T€"), None),
                (teur(Decimal(z.plan) / 100).removesuffix(" T€"), None),
                (format_betrag(Decimal(z.delta_plan) / 100, "T€", vorzeichen=True).removesuffix(" T€"), t_pl),
                (format_prozent(Decimal(z.delta_plan) * 100 / abs(Decimal(z.plan))) if z.plan else "–", t_pl),
                (teur(Decimal(z.vorjahr) / 100).removesuffix(" T€"), None),
                (format_betrag(Decimal(z.delta_vorjahr) / 100, "T€", vorzeichen=True).removesuffix(" T€"), t_py),
            ])
        summen = [i for i, z in enumerate(analyse.ergebnisrechnung) if z.kennzahl in ("Deckungsbeitrag", "EBIT")]
        zeige(ui.karte(ui.tabelle(["in T€", "AC", "PL", "ΔPL", "ΔPL %", "PY", "ΔPY"], tabellenzeilen, summen),
                       "Vereinfachte Ergebnisrechnung", "Simplified P&amp;L"))

    with rechts:
        wesentlich = sum(a.wesentlich for a in analyse.knoten.values())
        scan = [
            ("Datenprüfung", f"{protokoll.ampel.value} · {anzahl_ok} von {len(protokoll.punkte)} ohne Befund",
             AMPEL_TON[protokoll.ampel]),
            ("Abweichungen", f"{wesentlich} wesentlich", "gut"),
        ]
        if "Umsatz" in abschluss.forecasts:
            fc_u = abschluss.forecasts["Umsatz"]
            scan.append(("Forecast", f"{fc_u.methode.value} · WAPE "
                                     f"{format_prozent(fc_u.info.wape_pct, vorzeichen=False)}", "gut"))  # type: ignore[arg-type]
        aussagen = "".join(f'<p class="va-absatz">{ui.e(a)}</p>' for a in kernaussagen(abschluss)[:-1])
        copilot = (
            f'<div class="va-copilot-kopf"><div class="va-logo">Δ</div><div><b>Controller-Copilot</b>'
            f'<small class="va-leise" style="display:block;font-size:.78rem">Das Wichtigste im Monat</small></div></div>'
            f'<div class="va-ueberschrift"><span>Kernaussagen</span><span>Key Messages</span></div>{aussagen}'
            f'<div class="va-ueberschrift" style="margin-top:18px"><span>Abschluss-Status</span><span>Closing Status</span></div>'
            f'{ui.checkliste(scan)}'
        )
        zeige(ui.karte(copilot))
        with st.expander(f"Prüfprotokoll der Datenprüfung ({protokoll.ampel.value})",
                         expanded=protokoll.ampel is Ampel.ROT):
            st.markdown(protokoll.als_markdown())

# =============================================================================
# 2 · Abweichungsanalyse
# =============================================================================

def _seite_abweichung() -> None:
    zeige(ui.leitfrage("Welche Treiber erklären die Abweichung – bis auf Kunde und Kostenart?", "Variance Analysis"))
    kopf1, kopf2, _ = st.columns([1.3, 1.3, 2])
    kennzahl = kopf1.selectbox("Kennzahl", list(WURZELN), key="abw_kennzahl")
    vergleich = kopf2.radio("Vergleich", ["Plan", "Vorjahr"], horizontal=True, key="abw_vergleich",
                            disabled=kennzahl == "Kosten Kostenstellen")
    wurzel = WURZELN[kennzahl]
    if vergleich == "Vorjahr" and kennzahl != "Kosten Kostenstellen":
        wurzel = f"VJ-{wurzel}"

    aktuell = analyse.abweichung(wurzel)
    pfad = [aktuell]
    drill_spalten = st.columns(4)
    tiefe = 0
    while tiefe < 4:
        kinder = analyse.drilldown(aktuell.id)
        if not kinder:
            break
        auswahl = drill_spalten[tiefe].selectbox(
            f"Drill-down: {kinder[0].ebene}", ["(alle)"] + [k.id for k in kinder], key=f"drill_{wurzel}_{tiefe}",
            format_func=lambda i: "(alle)" if i == "(alle)" else analyse.abweichung(i).bezeichnung,
        )
        if auswahl == "(alle)":
            break
        aktuell = analyse.abweichung(auswahl)
        pfad.append(aktuell)
        tiefe += 1
    zeige(ui.pfad([a.bezeichnung for a in pfad]))

    ton = ui.ton_fuer(guenstig(aktuell.delta_abs or 0, aktuell.art))
    zeige(ui.kpi_leiste([
        ui.Kpi("Ist (AC)", teur(aktuell.ist), en="Actual"),
        ui.Kpi(f"Vergleichswert ({dia.basis_kuerzel(aktuell)})", teur(aktuell.vergleichswert),
               fuss=vergleichstext(aktuell).removeprefix("ggü. "), en="Comparison"),
        ui.Kpi("Abweichung", format_wert(aktuell.delta_abs, aktuell.einheit, vorzeichen=True),  # type: ignore[arg-type]
               format_prozent(aktuell.delta_pct) if aktuell.delta_pct is not None else None, ton,
               fuss=" · ".join(t for t in (aktuell.bewertung.value if aktuell.bewertung else "",
                                           "wesentlich" if aktuell.wesentlich else "nicht wesentlich") if t),
               hervorheben=True, en="Variance"),
        ui.Kpi("Haupttreiber", dia.kurz_effekt(aktuell.haupttreiber.effektart.value) if aktuell.haupttreiber else "–",
               format_wert(aktuell.haupttreiber.betrag, aktuell.einheit, vorzeichen=True)  # type: ignore[arg-type]
               if aktuell.haupttreiber else None,
               ui.ton_fuer(guenstig(aktuell.haupttreiber.betrag, aktuell.art)) if aktuell.haupttreiber else "neutral",
               fuss="Einmaleffekt" if aktuell.einmaleffekt else "", en="Main Driver"),
    ]))

    kinder = analyse.drilldown(aktuell.id)
    b1, b2 = st.columns([1.15, 1], gap="medium") if (aktuell.treiber and kinder) else (st.container(), st.container())
    if aktuell.treiber:
        with b1, st.container(border=True):
            _, nur_abw = dia.brueckendaten(aktuell)
            basis = dia.basis_kuerzel(aktuell)
            zeige(f'<div class="va"><p class="va-botschaft">{ui.e(aktuell.bezeichnung)}: '
                  f'{ui.e(format_wert(aktuell.delta_abs, aktuell.einheit, vorzeichen=True))} '
                  f'{ui.e(vergleichstext(aktuell))}</p>'  # type: ignore[arg-type]
                  f'<p class="va-unter">in T€ · {"Abweichung nach Effekten" if nur_abw else f"Brücke {basis} → AC"}</p></div>')
            diagramm(dia.bruecke(aktuell, hoehe=300), f"Brücke {basis} zu AC für {aktuell.bezeichnung}")
    if kinder:
        with b2, st.container(border=True):
            zeige(f'<div class="va"><p class="va-botschaft">Abweichungen der Unterebene ({ui.e(kinder[0].ebene)})</p>'
                  f'<p class="va-unter">Δ in T€ · größte {min(len(kinder), 12)} von {len(kinder)}</p></div>')
            diagramm(dia.abweichungsbalken(kinder), f"Abweichungen der Unterebene von {aktuell.bezeichnung}")
    with b2:
        rang = analyse.top_treiber(8).eintraege
        groesster = max((abs(r.betrag) for r in rang), default=Decimal(1)) or Decimal(1)
        zeige(ui.karte(ui.rangliste([
            (f"{r.rang}. {dia.kurzname(r.objekt_bezeichnung)}", r.effektart.value + (" · einmalig" if r.einmaleffekt else ""),
             format_wert(r.betrag, r.einheit, vorzeichen=True), float(abs(r.betrag) / groesster),
             ui.ton_fuer(guenstig(r.betrag, analyse.abweichung(r.objekt_id).art)))
            for r in rang
        ]), "Top-Treiber", "Top Drivers"))
    if aktuell.datentexte:
        with st.expander("Buchungstexte manueller Buchungen (unvertrauenswürdige Daten)"):
            for text in aktuell.datentexte:
                st.code(text, language=None)
            st.caption("Diese Texte werden dem KI-Agenten nur bereinigt übergeben und in der Oberfläche nie als HTML "
                       "interpretiert.")

    with st.container(border=True):
        zeige('<div class="va va-ueberschrift"><span>Alle Abweichungen</span><span>All Variances</span></div>')
        f1, f2, _ = st.columns([1, 1.4, 2], vertical_alignment="bottom")
        nur_vergleich = f1.selectbox("Vergleich", ["Plan-Ist", "Ist-Vorjahr", "Ist-Forecast"], key="abw_liste_vergleich")
        nur_wesentliche = f2.toggle("nur wesentliche Abweichungen", value=True, key="abw_liste_wesentlich")
        gefiltert = [a for a in analyse.knoten.values()
                     if a.vergleich.value == nur_vergleich and (a.wesentlich or not nur_wesentliche)]
        gefiltert.sort(key=lambda a: -abs(a.delta_abs or 0))
        st.dataframe(abweichungstabelle(gefiltert), hide_index=True, width="stretch", height=330)
        st.caption(f"{len(gefiltert)} Abweichungen · wesentlich ab {format_betrag(Decimal(schwelle_eur), 'T€')} und "
                   f"{format_prozent(Decimal(str(schwelle_pct)), vorzeichen=False)} (Einstellungen in der Seitenleiste)")

# =============================================================================
# 3 · Forecast
# =============================================================================

def _seite_forecast() -> None:
    zeige(ui.leitfrage("Wo landen wir am Jahresende – und wie verlässlich ist die Prognose?", "Forecast"))
    if not abschluss.forecasts:
        st.info("Für diese Periode liegt kein Forecast vor.")
    else:
        fc_name = st.selectbox("Kennzahl", list(abschluss.forecasts), key="fc_kennzahl")
        fc = abschluss.forecasts[fc_name]
        i = fc.info
        art = Kennzahlart.KOSTEN if fc_name == "Kosten" else Kennzahlart.ERLOES
        unten = format_betrag(i.intervall_unten, "Mio. €").removesuffix(" Mio. €")  # type: ignore[arg-type]
        zeige(ui.kpi_leiste([
            ui.Kpi("Jahresendprognose", format_betrag(i.jahresendprognose, "Mio. €"),  # type: ignore[arg-type]
                   f"{format_prozent(i.abweichung_plan_pct)} ggü. PL",  # type: ignore[arg-type]
                   ui.ton_fuer(guenstig(i.abweichung_plan_abs, art)),  # type: ignore[arg-type]
                   fuss=f"PL {format_betrag(i.plan_gesamtjahr, 'Mio. €')}", hervorheben=True, en="Year-End Forecast"),  # type: ignore[arg-type]
            ui.Kpi("Ist YTD", format_betrag(i.ist_ytd, "Mio. €"), fuss=f"Jan–{MONATSNAMEN[periode - 1][:3]}", en="Actual YTD"),
            ui.Kpi("Prognose Restmonate", format_betrag(i.prognose_restmonate, "Mio. €"),  # type: ignore[arg-type]
                   fuss=f"{12 - periode} Monate", en="Remaining"),
            ui.Kpi(f"Bandbreite {format_prozent(i.konfidenzniveau_pct, 0, vorzeichen=False)} · Mio. €",  # type: ignore[arg-type]
                   f"{unten}–{format_betrag(i.intervall_oben, 'Mio. €').removesuffix(' Mio. €')}",  # type: ignore[arg-type]
                   fuss=f"Methode {fc.methode.value}", en="Range"),
        ]))
        with st.container(border=True):
            zeige(f'<div class="va"><p class="va-botschaft">Jahresendprognose {ui.e(fc_name)} '
                  f'{ui.e(format_betrag(i.jahresendprognose))} ({ui.e(format_prozent(i.abweichung_plan_pct))} ggü. Plan)</p>'  # type: ignore[arg-type]
                  f'<p class="va-unter">in T€ je Monat · AC gefüllt · FC gestrichelt mit '
                  f'{ui.e(format_prozent(i.konfidenzniveau_pct, 0, vorzeichen=False))}-Intervall · PL umrandet</p></div>')  # type: ignore[arg-type]
            diagramm(dia.forecastverlauf(abschluss, fc_name), f"Monatsverlauf {fc_name}: Ist, Forecast mit Intervall, Plan")

        zeige(ui.karte(ui.szenarien([
            (s.name, format_betrag(s.wert, "Mio. €"),
             f"Restmonate {format_prozent(s.anpassung_pct)}" if s.anpassung_pct is not None else "", s.annahme)
            for s in i.szenarien
        ]), "Szenarien zum Jahresende", "Scenarios"))
        s1, s2 = st.columns(2, gap="medium")
        with s1:
            schlechteste = max(b.wape_pct for b in fc.backtests.values()) or 1.0
            zeige(ui.karte(ui.rangliste([
                (b.methode.value, "gewählt" if b.methode is fc.methode else "",
                 f"WAPE {format_prozent(Decimal(str(round(b.wape_pct, 1))), vorzeichen=False)}",
                 b.wape_pct / schlechteste, "gut" if b.methode is fc.methode else "leise")
                for b in sorted(fc.backtests.values(), key=lambda b: b.wape_pct)
            ]) + '<p class="va-unter" style="margin-top:4px">Prognosefehler im Rückblick (Backtesting) – die Methode mit '
                 'dem kleineren Fehler wird verwendet.</p>', "Methodenwahl", "Method Selection"))
        with s2:
            if fc.guete:
                guete = ui.tabelle(["Forecast", "erstellt", "WAPE", "Bias"], [
                    [(g.version, None), (f"nach Periode {g.ursprung_periode}", None),
                     (format_prozent(Decimal(str(g.wape_pct)), vorzeichen=False), None),
                     (format_prozent(Decimal(str(g.bias_pct))), None)] for g in fc.guete])
                zeige(ui.karte(guete + '<p class="va-unter" style="margin-top:8px">Bias &gt; 0: Forecast lag über dem '
                                       'eingetretenen Ist.</p>', "Trefferquote früherer Forecasts", "Forecast Accuracy"))
            else:
                st.info("Noch keine frühere Forecast-Version mit eingetretenem Ist vorhanden.")

# =============================================================================
# 4 · KI-Kommentare (Human-in-the-Loop)
# =============================================================================

def _seite_kommentare() -> None:
    zeige(ui.leitfrage("Welche Erklärungen gehen – geprüft und namentlich freigegeben – in den Bericht?", "AI Commentary"))

    def objektname(objekt_id: str) -> str:
        a = abschluss.faktenblatt.abweichung(objekt_id)
        return objekt_id if a is None else f"{a.bezeichnung} · {vergleichstext(a)}"

    steuer_links, steuer_rechts = st.columns([2.2, 1], gap="medium")
    with steuer_links:
        auswahl = st.multiselect("Objekte für Kommentar-Entwürfe", [a.id for a in wesentliche],
                                 default=[a.id for a in wesentliche[:6]],
                                 format_func=objektname)
        if st.button("Entwürfe erzeugen", type="primary"):
            with st.spinner("Kommentare werden erzeugt und geprüft …"):
                for objekt in auswahl:
                    if objekt not in speicher or speicher[objekt].status is Freigabestatus.ENTWURF:
                        k = agent.kommentiere(objekt)
                        speicher[objekt] = k
                        if k.verworfener_ki_text:
                            protokolliere(kontext, "VERWORFEN", "schlecht", objekt,
                                          "; ".join(ablehnungsgruende(k.verworfener_ki_text, objekt)) or "Faktenprüfung",
                                          "regelbasierter Ersatz")
                        protokolliere(kontext, "ENTWURF", "neutral", objekt,
                                      f"{k.quelle.value} · {sum(f.belegt for f in k.pruefung.funde)} Zahlen belegt",
                                      k.modell)
    with steuer_rechts:
        controller = st.text_input("Name für die Freigabe", placeholder="z. B. Max Mustermann")
        zeige(ui.statistik([
            (str(sum(k.status is Freigabestatus.ENTWURF for k in speicher.values())), "Entwürfe", "neutral"),
            (str(len(berichtsfaehige_kommentare(speicher.values()))), "freigegeben", "gut"),
            (str(sum(1 for k in speicher.values() if k.verworfener_ki_text)), "verworfen", "leise"),
        ]))

    for objekt, k in list(speicher.items()):
        abw = abschluss.faktenblatt.abweichung(objekt)
        with st.container(border=True):
            frei = k.status is Freigabestatus.FREIGEGEBEN
            belege = sum(f.belegt for f in k.pruefung.funde)
            pills = [
                ui.pill("Freigegeben" if frei else "Entwurf", "gut" if frei else "akzent", punkt=True),
                ui.pill(k.quelle.value, "leise"),
                ui.pill(f"Faktenprüfung {'bestanden' if k.pruefung.bestanden else 'nicht bestanden'} · {belege} belegt",
                        "gut" if k.pruefung.bestanden else "schlecht"),
            ]
            if abw is not None and abw.delta_abs is not None:
                pills.insert(0, ui.pill(format_wert(abw.delta_abs, abw.einheit, vorzeichen=True),
                                        ui.ton_fuer(guenstig(abw.delta_abs, abw.art))))
            zeige(f'<div class="va" style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;'
                  f'align-items:center"><div><b>{ui.e(abw.bezeichnung if abw else objekt)}</b> '
                  f'<span class="va-leise" style="font-size:.85rem">{ui.e(vergleichstext(abw) if abw else "")}</span> '
                  f'<span class="va-mono va-leise">{ui.e(objekt)}</span></div>'
                  f'<div style="display:flex;gap:6px;flex-wrap:wrap">{"".join(pills)}</div></div>')
            if frei:
                st.write(k.text)
                st.caption(f"✅ Freigegeben von {k.freigegeben_von} am {k.freigegeben_am:%d.%m.%Y %H:%M}"  # type: ignore[union-attr]
                           + (" · manuell bearbeitet" if k.manuell_bearbeitet else ""))
                continue
            text = st.text_area("Kommentar", k.text, key=f"text_{kontext}_{objekt}", height=105,
                                label_visibility="collapsed")
            p1, p2 = st.columns([1, 4])
            if p1.button("Freigeben", key=f"freigabe_{kontext}_{objekt}"):
                try:
                    speicher[objekt] = k.freigeben(controller, text, abschluss.faktenblatt)
                    protokolliere(kontext, "FREIGABE", "gut", objekt, f"freigegeben von {controller.strip()}"
                                  + (" · Text manuell geändert und erneut geprüft" if speicher[objekt].manuell_bearbeitet else ""),
                                  "berichtsfähig")
                    st.rerun()
                except FreigabeFehler as fehler:
                    protokolliere(kontext, "ABGELEHNT", "warnung", objekt, str(fehler))
                    p2.error(str(fehler))
            if k.verworfener_ki_text:
                with st.expander("Verworfener KI-Text (Prüfung nicht bestanden)"):
                    st.write(k.verworfener_ki_text)

    with st.container(border=True):
        freigegebene = berichtsfaehige_kommentare(speicher.values())
        zeige(f'<div class="va"><div class="va-ueberschrift"><span>Monatsbericht und Export</span><span>Monthly Report &amp; Export</span></div>'
              f'<p class="va-unter">{len(freigegebene)} freigegebene Kommentare gehen in den Bericht ein – Entwürfe nie.</p></div>')
        if st.button("Monatsbericht, Excel und Power-BI-Paket erstellen"):
            from src.exporter import (  # noqa: PLC0415
                exportiere_excel,
                exportiere_powerbi,
            )

            with st.spinner("Bericht und Exporte werden erstellt …"):
                ordner = Path(tempfile.mkdtemp(prefix="varianceai_"))
                bericht = erstelle_monatsbericht(abschluss, list(speicher.values()), ordner)
                st.session_state["downloads"] = {
                    "PowerPoint (.pptx)": bericht["pptx"],
                    "PDF": bericht["pdf"],
                    "Excel mit Gegenprüfung (.xlsx)": exportiere_excel(abschluss, ordner / "VarianceAI_Export.xlsx"),
                    "Power-BI-Paket (.zip)": exportiere_powerbi(abschluss, ordner / "powerbi"),
                }
                protokolliere(kontext, "BERICHT", "akzent", "Monatsbericht",
                              f"PowerPoint, PDF, Excel, Power BI · {len(freigegebene)} freigegebene Kommentare")
        downloads = st.session_state.get("downloads", {})
        if downloads:
            spalten = st.columns(len(downloads))
            for spalte, (label, datei) in zip(spalten, downloads.items(), strict=True):
                spalte.download_button(f"⬇ {label}", data=io.BytesIO(Path(datei).read_bytes()),
                                       file_name=Path(datei).name, key=f"dl_{label}", width="stretch")

# =============================================================================
# 5 · Frag den Controller
# =============================================================================

def _seite_frage() -> None:
    zeige(ui.leitfrage("Was möchten Sie zum Monatsabschluss wissen? Jede Zahl der Antwort ist belegt.", "Ask the Controller"))
    verlauf = st.session_state.setdefault("verlauf", {}).setdefault(kontext, [])
    knoepfe = st.columns(2)
    vorschlag = None
    for nr, frage in enumerate(BEISPIELFRAGEN):
        if knoepfe[nr % 2].button(frage, key=f"bsp_{frage}", width="stretch"):
            vorschlag = frage
    eingabe = st.chat_input("Frage an den Controller, z. B. „Warum ist der Deckungsbeitrag gesunken?“")
    frage = eingabe or vorschlag
    if frage:
        with st.spinner("Analysefunktionen werden abgefragt …"):
            antwort = agent.beantworte(frage)
            verlauf.append(antwort)
            protokolliere(kontext, "FRAGE", "neutral", frage[:80],
                          " → ".join(t.name for t in antwort.tool_aufrufe) or "ohne Tools",
                          f"{sum(f.belegt for f in antwort.pruefung.funde)} Zahlen belegt")

    if not verlauf:
        st.caption("Wählen Sie eine Beispielfrage oder stellen Sie unten eine eigene.")
    for antwort in reversed(verlauf):
        with st.chat_message("user", avatar=":material/person:"):
            st.write(antwort.frage)
        with st.chat_message("assistant", avatar=":material/query_stats:"):
            st.write(antwort.text)
            quelle = "KI-generiert, geprüft" if antwort.quelle is Textquelle.LLM else "regelbasiert (Fallback)"
            belegt = sum(f.belegt for f in antwort.pruefung.funde)
            bestanden = antwort.pruefung.bestanden
            glieder = [(t.name + (f"({', '.join(f'{k}={v}' for k, v in t.argumente.items())})" if t.argumente else ""),
                        t.erlaubt) for t in antwort.tool_aufrufe]
            zeige('<div class="va" style="display:flex;flex-direction:column;gap:8px">'
                  '<div style="display:flex;gap:6px;flex-wrap:wrap">'
                  + ui.pill(quelle, "akzent")
                  + ui.pill("Faktenprüfung " + ("bestanden" if bestanden else "nicht bestanden"),
                            "gut" if bestanden else "schlecht")
                  + ui.pill(f"{belegt}/{len(antwort.pruefung.funde)} Zahlen belegt", "leise")
                  + "</div>" + ui.kette(glieder) + "</div>")
            with st.expander(f"Verwendete Tools ({len(antwort.tool_aufrufe)})"):
                st.dataframe(pd.DataFrame([
                    {"Tool": t.name, "Argumente": ", ".join(f"{k}={v}" for k, v in t.argumente.items()) or "–",
                     "erlaubt": "✔" if t.erlaubt else "✘ (nicht auf der Whitelist)", "Fehler": t.fehler or ""}
                    for t in antwort.tool_aufrufe
                ]), hide_index=True, width="stretch")
            with st.expander(f"Quellzahlen ({len(antwort.pruefung.funde)})"):
                st.dataframe(pd.DataFrame([
                    {"Zahl im Text": f.text, "belegt": "✔" if f.belegt else "✘", "Quelle": f.beleg or "–"}
                    for f in antwort.pruefung.funde
                ]), hide_index=True, width="stretch")
            if antwort.verworfener_ki_text:
                st.warning(f"Verworfener KI-Text: {antwort.verworfener_ki_text}")

# =============================================================================
# 6 · Agent & Audit
# =============================================================================

def _seite_audit() -> None:
    zeige(ui.leitfrage("Wer entscheidet was – und warum? Jede Zahl hat eine Quelle, jede Freigabe einen Namen.", "Agent & Audit Trail"))
    a_links, a_rechts = st.columns([1.3, 1], gap="medium")
    frei_n = len(berichtsfaehige_kommentare(speicher.values()))
    verworfen_n = sum(1 for k in speicher.values() if k.verworfener_ki_text)
    with a_links:
        zeige(ui.karte(ui.agentenkarten([
            ui.Agentenkarte("datenbank", "Datenprüfung", protokoll.ampel.value,
                            "Neun Prüfpunkte vor jeder Analyse.", "Keine Analyse vor bestandener Prüfung.", "",
                            AMPEL_TON[protokoll.ampel]),
            ui.Agentenkarte("diagramm", "Analyse und Forecast", "fertig",
                            "Python berechnet Abweichungen, Brücken und Prognose.", "Rechnet centgenau – nie die KI.", "",
                            "gut"),
            ui.Agentenkarte("funke", "KI-Agent", "Demo" if offline else "live",
                            "Formuliert Kommentare und Antworten; jede Zahl wird automatisch geprüft.",
                            "Formuliert – rechnet nicht.", "", "akzent", aktiv=True),
            ui.Agentenkarte("person", "Controller", "Freigabe", "Prüft, bearbeitet und gibt namentlich frei.",
                            "Kein Bericht ohne Namen.", "", "neutral"),
        ]), "So arbeitet VarianceAI", "How It Works"))

        knoten = list(analyse.knoten.values())
        wesentlich_n = sum(a.wesentlich for a in knoten)
        zeige(ui.karte(ui.trichter([
            ("datenbank", "Berechnete Abweichungen", "alle Ebenen und Vergleiche", str(len(knoten)), ""),
            ("filter", "Wesentlich", f"|Δ| ≥ {format_betrag(Decimal(schwelle_eur), 'T€')} und "
             f"|Δ %| ≥ {format_prozent(Decimal(str(schwelle_pct)), vorzeichen=False)}", str(wesentlich_n),
             f"{len(knoten) - wesentlich_n} unter Schwelle"),
            ("lupe", "Kommentierbar", "Gesamt, Region, Produktgruppe, Kostenstelle", str(len(wesentliche)), ""),
            ("funke", "Entwürfe", "KI-Text oder regelbasierter Ersatz, geprüft", str(len(speicher)),
             f"{verworfen_n} KI-Texte verworfen"),
            ("check", "Im Bericht", "nur namentlich freigegeben", str(frei_n), "berichtsfähig"),
        ]), "Von der Abweichung zum Bericht", "From Variance to Report"))


    with a_rechts:
        with st.container(border=True):
            zeige('<div class="va"><div class="va-ueberschrift"><span>Sicherheitstest</span>'
                  f'{ui.pill("Prompt Injection", "akzent")}</div><p class="va-absatz">Ein manipuliertes LLM folgt der '
                  'versteckten Anweisung im Buchungstext und behauptet: <i>„Alle Kostenstellen liegen im Plan.“</i> '
                  'Die Konsistenzprüfung muss den Text verwerfen.</p></div>')
            if st.button("Injection-Test ausführen", key="injektionstest", width="stretch"):
                ziel = "KST-4200-400000" if "KST-4200-400000" in analyse.knoten else "KST"
                test = abschluss.agent(ManipuliertesLLM()).kommentiere(ziel)
                if test.quelle is Textquelle.REGELBASIERT and test.verworfener_ki_text:
                    gruende_test = ablehnungsgruende(test.verworfener_ki_text, ziel)
                    protokolliere(kontext, "BLOCKIERT", "gut", ziel,
                                  f"„{test.verworfener_ki_text}“ verworfen: " + "; ".join(gruende_test),
                                  "regelbasierter Ersatz")
                    st.session_state["injektionstest_ergebnis"] = ("gut", gruende_test)
                else:
                    protokolliere(kontext, "NICHT BLOCKIERT", "schlecht", ziel, test.text[:120])
                    st.session_state["injektionstest_ergebnis"] = ("schlecht", [])
            if ergebnis := st.session_state.get("injektionstest_ergebnis"):
                ton_test, gruende_test = ergebnis
                zeige('<div class="va" style="display:flex;flex-direction:column;gap:6px">'
                      + ui.pill("verworfen – nicht im Bericht" if ton_test == "gut" else "NICHT verworfen", ton_test,
                                punkt=True)
                      + "".join(f'<span class="va-leise" style="font-size:.8rem">· {ui.e(g)}</span>' for g in gruende_test)
                      + "</div>")

        eintraege = audit(kontext)
        zeige(ui.karte(ui.protokoll(list(reversed(eintraege))), "Audit-Protokoll", "Audit Log"))
        st.download_button(
            "⬇ Audit-Protokoll als JSON", width="stretch", key="audit_json", file_name=f"audit_{kontext}.json",
            data=json.dumps([e.__dict__ for e in eintraege], ensure_ascii=False, indent=2).encode("utf-8"),
        )


# Tabs mit Fehlergrenze aufbauen
with tab_uebersicht:
    sicher(_seite_uebersicht, "Monatsübersicht")
with tab_abweichung:
    sicher(_seite_abweichung, "Abweichungsanalyse")
with tab_forecast:
    sicher(_seite_forecast, "Forecast")
with tab_kommentare:
    sicher(_seite_kommentare, "KI-Kommentare")
with tab_frage:
    sicher(_seite_frage, "Frag den Controller")
with tab_audit:
    sicher(_seite_audit, "Agent & Audit")
