# VarianceAI

**Plan-Ist-Analyse, Forecast und IBCS-Monatsbericht für das operative Controlling**

**Live-Demo:** <https://variance-ai-shweish.streamlit.app> · **Stack:** Python, SQL (SQLite), Streamlit, Excel- und Power-BI-Export

[![Tests](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Offline](https://img.shields.io/badge/Demo-ohne%20API--Key-0A66C2)
![macOS](https://img.shields.io/badge/macOS-Desktop--App-555555?logo=apple)
![Lizenz](https://img.shields.io/badge/Lizenz-MIT-lightgrey)

> *Warum weicht das Ergebnis vom Plan ab – und wo landen wir am Jahresende?*

**Kurzfassung:** VarianceAI bildet den monatlichen Controlling-Zyklus eines fiktiven Industrieunternehmens ab:
Plan-Ist-Vergleich, Zerlegung der Abweichungen in Preis-, Mengen- und Mixeffekte, rollierender Forecast und
Monatsbericht nach IBCS.

Alle Zahlen berechnet Python; die KI formuliert nur Kommentare, deren Zahlen automatisch geprüft werden.
Portfolio-Projekt mit synthetischen Daten.

---

## Inhalt

1. [Einblick: Cockpit und Monatsbericht](#einblick-cockpit-und-monatsbericht)
2. [Business Value für den Monatsabschluss](#business-value-für-den-monatsabschluss)
3. [Funktionsumfang](#funktionsumfang)
4. [Fachliche Methodik](#fachliche-methodik)
5. [Reporting und Export](#reporting-und-export)
6. [Leitprinzip: Determinismus vor KI](#leitprinzip-determinismus-vor-ki)
7. [Architektur](#architektur)
8. [KI-Controller-Agent](#ki-controller-agent)
9. [Cockpit und Designsystem](#cockpit-und-designsystem)
10. [Unternehmenseinsatz: SAP-Anbindung und KI-Schlüssel](#unternehmenseinsatz-sap-anbindung-und-ki-schlüssel)
11. [Einordnung im Portfolio](#einordnung-im-portfolio)
12. [Projektstruktur](#projektstruktur)
13. [Installation & Schnellstart](#installation--schnellstart)
14. [Tests](#tests)
15. [Glossar Deutsch–Englisch](#glossar-deutschenglisch)
16. [Hinweise](#hinweise)

---

## Einblick: Cockpit und Monatsbericht

Monatsabschluss September 2026 im Offline-Modus (synthetische Daten, Seed 42). Alle Bilder entstehen
reproduzierbar aus `python demo.py` bzw. `streamlit run app.py`.

**Monatsübersicht** – Kennzahlenleiste mit Monatsverlauf (Sparklines), EBIT-Brücke Plan → Ist als Hauptgrafik
und der *Controller-Copilot* mit den Kernaussagen des Monats und dem Abschluss-Status.

![Monatsübersicht](docs/screenshots/01_monatsuebersicht.png)

| Abweichungsanalyse | Forecast |
|---|---|
| ![Abweichungsanalyse](docs/screenshots/02_abweichungsanalyse.png) | ![Forecast](docs/screenshots/03_forecast.png) |
| Brücke je Objekt, Drill-down bis Kostenart bzw. Kunde, Wesentlichkeitsfilter, Top-Treiber | Jahresendprognose mit 80-%-Intervall, Szenarien Best/Base/Worst, Backtesting und Forecast-Güte |
| **KI-Kommentare** | **Frag den Controller** |
| ![KI-Kommentare](docs/screenshots/04_ki_kommentare.png) | ![Frag den Controller](docs/screenshots/05_frag_den_controller.png) |
| Entwürfe prüfen, bearbeiten und namentlich freigeben | Antwort mit Tool-Kette und belegten Quellzahlen |

**Agent & Audit** – so arbeitet VarianceAI: Rollen und Regeln, der Weg von der Abweichung zum Bericht,
Prompt-Injection-Test per Knopfdruck und Audit-Protokoll (Export als JSON):

![Agent und Audit](docs/screenshots/06_agent_audit.png)

| Darstellung hell (Umschalter oben rechts) | Als Mac-App im eigenen Fenster |
|---|---|
| ![Hell](docs/screenshots/11_monatsuebersicht_hell.png) | ![Desktop-Fenster](docs/screenshots/12_desktop_fenster.png) |

**Automatischer Monatsbericht** (PowerPoint und PDF, nur freigegebene Kommentare) und **Excel-Gegenprüfung**:

| Management Summary (PowerPoint) | EBIT-Brücke Plan → Ist (PowerPoint) |
|---|---|
| ![Management Summary](docs/screenshots/07_bericht_management_summary.png) | ![EBIT-Brücke](docs/screenshots/08_bericht_ebit_bruecke.png) |
| **Monatsbericht (PDF, Seite 1)** | **Excel: Formel-Gegenprüfung** |
| ![PDF-Bericht](docs/screenshots/09_bericht_pdf.png) | ![Excel-Gegenprüfung](docs/screenshots/10_excel_gegenpruefung.png) |

---

## Business Value für den Monatsabschluss

| Schritt im Abschluss | Typischer Engpass | Beitrag von VarianceAI |
|---|---|---|
| Datenprüfung | Fehlende Perioden, Dubletten oder nicht abgestimmte Summen fallen erst im Bericht auf | Ampelprüfung vor jeder Analyse: Vollständigkeit, Summenabstimmung, Dubletten, Vorzeichen, Periodenzuordnung |
| Abweichungsanalyse | Manuelle Excel-Brücken, Ursachen bleiben auf KPI-Ebene | Exakt additive Zerlegung (Preis/Menge/Mix/Sortiment, Preis-/Verbrauchs-/Beschäftigungsabweichung) mit Drill-down bis Kunde bzw. Kostenart |
| Priorisierung | Alles wird kommentiert, Wesentliches geht unter | Konfigurierbare Wesentlichkeitsschwellen, Ranking der Top-Treiber, Kennzeichnung von Einmaleffekten |
| Forecast | Jahresendprognose als Bauchgefühl | Ist YTD + statistische Prognose der Restmonate, Methodenwahl per Backtesting (WAPE), Szenarien Best/Base/Worst |
| Kommentierung | Hoher Zeitaufwand, uneinheitlicher Stil | KI-Entwürfe im Controlling-Stil (Ursache → Wirkung → Maßnahme), zahlengeprüft und erst nach Freigabe berichtsfähig |
| Berichtserstellung | Copy-Paste in PowerPoint | Monatsbericht nach IBCS als PowerPoint/PDF, Excel mit Formel-Gegenprüfung, Power-BI-Paket |

**Ergebnis:** weniger Routinearbeit im Abschluss, mehr Zeit für die eigentliche Ursachenklärung mit den
Fachbereichen – bei voller Nachvollziehbarkeit jeder Zahl.

---

## Funktionsumfang

| Bereich | Was VarianceAI leistet |
|---|---|
| **Datenprüfung im Monatsabschluss** | Neun Prüfpunkte mit Ampel und Protokoll: Vollständigkeit, Summenabstimmung, Dubletten, Vorzeichen, Periodenzuordnung; bei Rot ist die Analyse gesperrt |
| **Plan-Ist-Analyse Umsatz** | Zerlegung in Preis-, Mengen-, Mix- und Sortimentseffekt, centgenau additiv, Drill-down bis Kunde |
| **Kostenstellen** | Flexible Plankostenrechnung: Preis-, Verbrauchs- und Beschäftigungsabweichung, Einmaleffekte getrennt |
| **Ergebnis** | Vereinfachte Ergebnisrechnung und EBIT-Brücke Plan → Ist ohne Restgröße |
| **Forecast** | Rollierende Jahresendprognose (saisonal-naiv, Holt-Winters), Methodenwahl per Backtesting nach WAPE, Szenarien Best/Base/Worst |
| **KI-Kommentare** | Entwürfe im Controlling-Stil, jede Zahl automatisch geprüft, namentliche Freigabe vor dem Bericht |
| **Frag den Controller** | Fragen in deutscher Sprache, Antwort mit Tool-Kette und belegten Quellzahlen |
| **Reporting und Export** | Monatsbericht nach IBCS als PowerPoint und PDF, Excel mit Formel-Gegenprüfung, Power-BI-Paket (Sternschema, DAX) |
| **Betrieb** | Browser, Mac-App im eigenen Fenster, vollständiger Offline-Modus ohne API-Key, Audit-Protokoll |

Mögliche Erweiterungen: Anbindung an echte SAP-Extrakte (CSV/OData), Kommentarhistorie über mehrere Monate,
Mehrmandantenfähigkeit.

---

## Fachliche Methodik

### Umsatz: Preis-, Mengen-, Mix- und Sortimentseffekt

Notation: $m_i$ Menge, $p_i$ Preis von Produkt $i$, $M = \sum_i m_i$ Gesamtmenge, $s_i = m_i / M$ Mix-Anteil.

```math
\text{Mengeneffekt} = (M_{ist} - M_{plan}) \cdot \sum_i s_{i,plan} \cdot p_{i,plan}
```

```math
\text{Mixeffekt} = M_{ist} \cdot \sum_i (s_{i,ist} - s_{i,plan}) \cdot p_{i,plan}
```

```math
\text{Preiseffekt} = \sum_i m_{i,ist} \cdot (p_{i,ist} - p_{i,plan})
```

Die Zerlegung ist **exakt additiv** – die Summe der Effekte ergibt die Gesamtabweichung auf den Cent:

```math
\text{Mengeneffekt} + \text{Mixeffekt} + \text{Preiseffekt} = \sum_i m_{i,ist}\,p_{i,ist} - \sum_i m_{i,plan}\,p_{i,plan}
```

- Der Mixeffekt wird nur **innerhalb vergleichbarer Produktgruppen** berechnet.
- Produkte ohne Plan (Neuprodukte) bzw. ohne Ist (Wegfall) laufen in einen separaten
  **Sortimentseffekt**, damit $p_{i,plan}$ nie undefiniert ist.

### Kostenstellen: flexible Plankostenrechnung auf Vollkostenbasis

Mit dem Beschäftigungsgrad $BG = \text{Ist-Beschäftigung} / \text{Plan-Beschäftigung}$:

| Größe | Formel |
|---|---|
| Verrechnete Plankosten | Plankosten × BG |
| Sollkosten | fixe Plankosten + variable Plankosten × BG |
| Preisabweichung | Istkosten zu Istpreisen − Istkosten zu Planpreisen |
| Verbrauchsabweichung | Istkosten zu Planpreisen − Sollkosten |
| Beschäftigungsabweichung | Sollkosten − verrechnete Plankosten |

Die drei Teilabweichungen addieren sich zur Gesamtabweichung *Istkosten − verrechnete Plankosten*.

**Rechenbeispiel:** Plan 10.000 h, Ist 9.000 h, Plankosten 1.000,0 T€ (davon fix 400,0 T€) →
verrechnete Plankosten 900,0 T€, Sollkosten 940,0 T€. Preisabweichung +25,0 T€, Verbrauchsabweichung
+45,0 T€, Beschäftigungsabweichung +40,0 T€ = Gesamtabweichung **+110,0 T€ (ungünstig)**.

Ungeplante Einmalkosten (z. B. Sonderabschreibung) werden vollständig als **Einmaleffekt** ausgewiesen.

### Vereinfachte Ergebnisrechnung und EBIT-Brücke

Umsatz − Herstellkosten zu Standard = **Deckungsbeitrag**; Deckungsbeitrag − Kosten der Kostenstellen = **EBIT**.
Die EBIT-Abweichung wird ohne Restgröße in Treiber zerlegt:

```math
\Delta\text{EBIT} = \underbrace{\text{Preis} + \text{Menge} + \text{Mix} + \text{Sortiment}}_{\Delta\text{DB}}
- \underbrace{(\text{Preis-} + \text{Verbrauchs-} + \text{Beschäftigungsabw.} + \text{Einmaleffekt})}_{\text{Kostenstellen}}
- \text{Plankostenanpassung}
```

Die Plankostenanpassung (verrechnete Plankosten − Plankosten) überführt die Kostenstellenrechnung auf
Basis der Istbeschäftigung in den Plan. September 2026: ΔDB +55,1 T€ − Kostenstellenabweichungen
503,6 T€ + Plankostenanpassung 213,5 T€ = **ΔEBIT −235,1 T€ (−29,2 %)**, centgenau durch einen Test abgesichert.

### Bewertung, Wesentlichkeit und Drill-down

- **Vorzeichen- und Farblogik:** Erlöse/Ergebnis über Plan = günstig (grün), Kosten über Plan = ungünstig (rot).
- **Wesentlichkeitsschwellen** (konfigurierbar), z. B. |Δ| ≥ 50.000 € und |Δ %| ≥ 5 %.
- **Drill-down:** Region → Produktgruppe → Produkt → Kunde bzw. Kostenstelle → Kostenart.
  Invariante: Die Summe einer vollständigen Unterebene entspricht exakt der übergeordneten Ebene.
- **Top-Treiber-Ranking** nach Absolutbetrag, ohne Doppelzählung über Hierarchieebenen; Einmaleffekte werden gekennzeichnet.

### Forecast: Rolling Forecast und Jahresendprognose

```math
\text{Jahresendprognose} = \text{Ist}_{YTD} + \text{Prognose der Restmonate}
```

- **Methoden:** saisonal-naiv als Baseline und Exponential Smoothing (Holt-Winters, `statsmodels`).
- **Methodenwahl je Zeitreihe** per Backtesting (Rolling Origin) nach WAPE:

```math
\text{WAPE} = \frac{\sum_t |\text{Ist}_t - \text{Forecast}_t|}{\sum_t |\text{Ist}_t|}
```

- Prognoseintervalle sowie Szenarien **Best / Base / Worst** mit transparenten, deterministischen Annahmen.
- Messung der Forecast-Güte über die Zeit (Forecast vs. später eingetretenes Ist).

### Reporting nach IBCS

Ist (AC) dunkel gefüllt · Vorjahr (PY) grau · Plan (PL) umrandet · Forecast (FC) schraffiert ·
Abweichungsbalken günstig grün / ungünstig rot, immer mit Vorzeichen beschriftet · Wasserfall-Brücke Plan → Ist.
Ist die Abweichung im Verhältnis zur Basis klein, zeigt die Brücke automatisch nur die Abweichung (Start bei 0),
damit die Effekte lesbar bleiben. Botschaft als Diagrammtitel (z. B. *„EBIT −235,1 T€ ggü. Plan“*).
Zahlenformat nach deutschem Standard: `1.234,5 T€`, `1,2 Mio. €`, `−4,2 %`.

---

## Reporting und Export

| Ausgabe | Modul | Inhalt |
|---|---|---|
| **Monatsbericht PowerPoint** | `src/reporting_ibcs.py` | 8 Folien (16:9): Titel, Management Summary, EBIT-Brücke, Umsatzbrücke, Kostenstellen, Forecast, freigegebene Kommentare, Datenprüfung |
| **Monatsbericht PDF** | `src/reporting_ibcs.py` | A4 quer, gleiche Inhalte, deutsches Zahlenformat |
| **Excel-Bericht** | `src/exporter.py` | Blätter *Hinweise, Gegenprüfung, Ergebnisrechnung, Abweichungen, Kostenstellen, Forecast*; Eingaben blau, Formeln schwarz, Ergebniswerte vorberechnet |
| **Power-BI-Paket** | `src/exporter.py` | Sternschema als CSV (3 Fakten, 6 Dimensionen inkl. Kalender), `measures.dax`, `power_query.m`, Modellbeschreibung |

- **Nur Freigegebenes geht in den Bericht:** Kommentare im Status *Entwurf* erscheinen weder in PowerPoint noch im PDF.
- **Formel-Gegenprüfung:** Treibersummen, Abweichungen, Plankostenrechnung, Ergebnisrechnung, Drill-down und
  Szenarien stehen in Excel als echte Formeln. Das Blatt *Gegenprüfung* vergleicht sie mit dem Python-Ergebnis;
  `pruefe_excel()` rechnet die Datei mit LibreOffice neu (9 Prüfungen, 0 Formelfehler). Wird ein Wert in Excel
  manipuliert, springt der Gesamtstatus auf *FEHLER*.
- **Power BI im deutschen Format:** Semikolon, Dezimalkomma, `TT.MM.JJJJ`, UTF-8 mit BOM; Kalender als lückenlose
  Tagesdimension 2023–2026 mit Geschäftsjahr, Quartal und Monatsnamen. Die Summen im Paket stimmen centgenau mit der
  Analyse überein; DAX-Measures referenzieren nur vorhandene Spalten (beides per Test geprüft).

---

## Leitprinzip: Determinismus vor KI

| Grundsatz | Umsetzung |
|---|---|
| **Alle Zahlen kommen aus Python** | Beträge in Cent, Effekte exakt mit `Fraction`, Rundung erst bei der Ausgabe (Largest-Remainder, Summe bleibt centgenau), abgesichert durch mathematische Invarianten in pytest |
| **Das LLM sieht nur ein Faktenblatt** | Strukturiertes JSON (Pydantic-validiert) bzw. Ergebnisse fest definierter Analysefunktionen |
| **Null-Halluzinations-Prüfung** | Jede Zahl im KI-Text wird mit Wert, Einheit, Vorzeichen und Rundung gegen das Faktenblatt abgeglichen; zusätzlich prüft eine Konsistenzprüfung Pflichtangaben und Bewertung. Fehlschlag → Text wird verworfen, regelbasierter Standardkommentar wird erzeugt |
| **Human-in-the-Loop** | KI-Kommentare sind `Entwurf`. Erst die namentliche Freigabe durch den Controller macht sie berichtsfähig – manuelle Änderungen werden erneut geprüft |
| **Offline-Modus** | Ohne API-Key läuft das gesamte Projekt mit einem deterministischen Mock-Client vollständig durch |

---

## Architektur

```mermaid
flowchart LR
    subgraph Daten["1 · Daten & Abschlussprüfung"]
        GEN["seed_data.py<br/>(synthetisch, fester Seed)"] --> DB[("SQLite<br/>ACDOCA-Stil<br/>Ist · Plan · Forecast")]
        DB --> CHK{"datenpruefung.py<br/>Ampel + Protokoll"}
    end

    subgraph Engines["2 · Deterministische Engines (Python)"]
        VAR["variance_engine.py<br/>Preis · Menge · Mix · Sortiment<br/>Preis · Verbrauch · Beschäftigung"]
        FC["forecast_engine.py<br/>Saisonal-naiv · Holt-Winters · WAPE"]
    end

    CHK -->|"grün / gelb (bereinigt)"| VAR & FC
    CHK -.->|rot| STOP["Analyse gesperrt"]
    FC -->|"Forecast-Version"| VAR
    VAR & FC --> FB[["Faktenblatt<br/>(JSON, Pydantic)"]]
    VAR --> EB["EngineBackend<br/>vollständiger Analysebaum"]

    subgraph Agent["3 · KI-Controller-Agent"]
        FB --> LLM["LLM<br/>Claude · Gemini · Mock"]
        LLM <-->|"nur Whitelist-Tools"| TOOLS["abweichung_holen · drilldown<br/>top_treiber · forecast_holen"]
        TOOLS --- EB
        LLM --> PRF{"Zahlen- und<br/>Konsistenzprüfung"}
        PRF -->|bestanden| ENT["Kommentar-Entwurf"]
        PRF -->|"nicht bestanden"| REG["Regelbasierter<br/>Standardkommentar"]
        REG --> ENT
        ENT --> HITL{"Freigabe durch<br/>Controller"}
    end

    subgraph Reporting["4 · Reporting & Export"]
        HITL -->|freigegeben| BER["reporting_ibcs.py<br/>Monatsbericht IBCS<br/>PowerPoint · PDF"]
        FB --> XLS["exporter.py<br/>Excel mit<br/>Formel-Gegenprüfung"]
        FB --> PBI["exporter.py<br/>Power BI<br/>Sternschema · DAX"]
        UI["app.py · run_agent.py · demo.py<br/>Cockpit · CLI · Demo"]
    end
```

---

## KI-Controller-Agent

Der Agent (`src/agent.py`) erzeugt Management-Kommentare auf Deutsch und beantwortet Fragen wie
*„Warum ist der Deckungsbeitrag im März gesunken?“*.

### Tool-Whitelist statt freiem SQL

Bewusst anders als in ControllAI (Text-to-SQL) darf das LLM ausschließlich fünf fest definierte,
streng typisierte Analysefunktionen aufrufen. Unbekannte Tools und unbekannte Parameter werden abgelehnt
und protokolliert.

| Tool | Zweck |
|---|---|
| `abweichungen_auflisten` | Übersicht der Abweichungen (optional nur wesentliche) |
| `abweichung_holen` | Eine Abweichung mit Zerlegung in Treiber |
| `drilldown` | Nächsttiefere Ebene, sortiert nach Betrag |
| `top_treiber` | Ranking der größten Treiber (1–10) |
| `forecast_holen` | Jahresendprognose inkl. Methode, WAPE, Intervall, Szenarien |

### Null-Halluzinations-Prüfung

Jede Zahl im KI-Text wird erkannt – auch in deutschen Formaten wie `1,2 Mio. €`, `1.234,5 T€`,
`−4,2 %`, `+3,1 PP` – und muss im Faktenblatt belegt sein:

- gleiche **Einheit** (€, %, PP, Stück), passendes **Vorzeichen**,
- Abweichung höchstens im Rahmen der **kaufmännischen Rundung** der angezeigten Nachkommastellen,
- skalierte Angaben (T€, Mio. €) nicht gröber als 10 % gerundet,
- keine selbst gerechneten Summen, keine Zahlen aus Buchungstexten,
- Formverbote: Links, E-Mail-Adressen, Code/Markup, englisches Zahlenformat.

### Schutz vor Prompt Injection

- Texte aus den Daten (z. B. Buchungstexte) sind **Daten, keine Anweisungen** – so steht es im System-Prompt.
- Daten werden als JSON in Datenblöcke eingebettet; spitze Klammern werden maskiert, damit kein Text den Block verlassen kann.
- Auffällige Datentexte („Ignoriere alle Anweisungen …“) werden vor dem LLM-Aufruf entfernt; das Original bleibt für das Audit erhalten.
- Die Bereinigung gilt für das Faktenblatt **und** für jedes Tool-Ergebnis (z. B. Drill-down auf eine Kostenart mit manipuliertem Buchungstext).
- Selbst ein manipuliertes LLM kann keine falsche Aussage in den Bericht bringen: Neben der Zahlenprüfung verlangt eine **Konsistenzprüfung** für Kommentare die Angabe der Abweichung, verbietet Entwarnungen („im Plan“) bei wesentlichen Abweichungen und prüft die Bewertung gegen die Vorzeichenlogik. Danach greift die Freigabe.

### Tools mit der echten Engine

Das Faktenblatt im Prompt ist bewusst kompakt (Übersichtsebenen, Kostenarten, wesentliche Produkte).
Die Tools greifen über das `EngineBackend` auf den **vollständigen Analysebaum** zu – Drill-down bis
Kunde bzw. Kostenart. Auch Zahlen aus Tool-Ergebnissen durchlaufen die Faktenprüfung.

### Beispiel (Offline-Modus, Monatsabschluss September 2026)

```text
[Entwurf | KI-generiert] Umsatz Süd – Antriebe: −22,2 % (−288,7 T€) ggü. Plan, ungünstig; überwiegend
Mengeneffekt (−289,4 T€), größter Beitrag aus Umsatz Süd – Getriebemotor GM 200 (−102,9 T€).
Maßnahme: Absatzentwicklung und Auftragseingang mit dem Vertrieb analysieren.

[Entwurf | KI-generiert] Kostenstelle 4300 Lackiererei: +15,9 % (+60,2 T€) ggü. Plan, ungünstig;
überwiegend Beschäftigungsabweichung (+61,1 T€), größter Beitrag aus Kostenstelle 4300 – Planmäßige
Abschreibungen (+41,4 T€). Maßnahme: Kapazitätsauslastung prüfen und Anpassung der Fixkosten erwägen.

Frage: Warum liegt die Kostenstelle 4200 über Plan?
Tools: abweichungen_auflisten → abweichung_holen(KST-4200) → drilldown(KST-4200)
[KI-generiert] Kostenstelle 4200 Montage: +9,0 % (+91,0 T€) ggü. Plan, ungünstig; überwiegend
Verbrauchsabweichung (+89,0 T€), größter Beitrag aus Kostenstelle 4200 – Materialaufwand (+88,2 T€). …
```

Unterstützte Anbieter: **Anthropic Claude**, **Google Gemini** und **Mock/Offline**. Modell-IDs sind über
`.env` konfigurierbar – bitte die aktuellen IDs in der offiziellen Dokumentation prüfen
([Claude](https://docs.claude.com/en/docs/about-claude/models), [Gemini](https://ai.google.dev/gemini-api/docs/models)).

---

## Cockpit und Designsystem

Das Cockpit ist bewusst ruhig gestaltet – **dunkel oder hell**, umschaltbar oben rechts auf der Startseite (die Wahl
wird gespeichert). Fläche und Rahmen treten zurück, Farbe trägt nur Bedeutung.
Die Regeln sind im Code dokumentiert (`src/ui_design.py`, `src/ui_diagramme.py`, `.streamlit/config.toml`).

| Regel | Umsetzung | Begründung |
|---|---|---|
| **Eine Farbe – eine Aufgabe** | Datenfarben nach IBCS (AC hell gefüllt, PY grau, PL umrandet, FC gestrichelt); Akzent Kupfer nur für Interaktion und KI-Elemente | Wer Farbe liest, soll nie raten müssen, ob sie Daten oder Bedienung meint |
| **Günstig/ungünstig barrierearm** | `#3AAB86` / `#C8382F`, auf der dunklen Fläche validiert (Kontrast ≥ 3:1, Farbsehschwäche ΔE 12,6) und immer mit Vorzeichen beschriftet | Rot-Grün allein ist für ca. 8 % der Männer nicht unterscheidbar |
| **Ehrliche Achsen** | Säulen immer ab 0; ist eine Abweichung klein gegenüber der Basis, zeigt die Brücke automatisch nur die Abweichung | Abgeschnittene Achsen übertreiben Unterschiede |
| **Richtige Vergleichsbasis** | Kostenstellen werden gegen **verrechnete Plankosten** gemessen (*PL verr.*), die Differenz zum starren Plan zeigt die EBIT-Brücke als Plankostenanpassung | Flexible Plankostenrechnung; sonst würde Beschäftigung als Verbrauch fehlgedeutet |
| **Leitfrage je Tab** | z. B. *„Welche Treiber erklären die Abweichung – bis auf Kunde und Kostenart?“* | Jede Ansicht beantwortet genau eine Managementfrage |
| **Interaktiv, aber deterministisch** | Altair/Vega-Lite mit Tooltips; alle Beschriftungen deutsch in Python formatiert | Tooltips zeigen dieselben geprüften Zahlen wie Bericht und Excel |
| **Sicher gerendert** | Jeder dynamische Text wird HTML-maskiert; Grafiken als Data-URI-Bilder, die HTML-Bereinigung von Streamlit bleibt aktiv | Buchungstexte sind unvertrauenswürdige Daten – auch für die Oberfläche |
| **Hell und dunkel** | Zwei validierte Paletten (hell: günstig `#2A9D72` / ungünstig `#C8382F`, CVD ΔE 8,8); IBCS-Notation passt sich an (AC auf hell dunkel gefüllt) | Arbeitsplatz und Präsentation: dunkel am Bildschirm, hell für Beamer und Ausdruck |
| **Offline-fähig** | Schriften Inter und JetBrains Mono (SIL OFL) lokal in `static/fonts`, kein CDN | Läuft im Firmennetz und ohne Internet |

Der Monatsbericht (PowerPoint/PDF) bleibt hell und druckoptimiert – dieselbe IBCS-Notation, angepasst an Papier.

---

## Unternehmenseinsatz: SAP-Anbindung und KI-Schlüssel

So würde VarianceAI in einem Unternehmen produktiv betrieben: Die Daten kommen über OData aus freigegebenen
CDS-Views (technischer Benutzer, Feldzuordnung ACDOCA/ACDOCP), der KI-Schlüssel liegt im Secret-Manager bzw. im
macOS-Schlüsselbund, und die Anmeldung läuft über Single Sign-on. Dieses Repository enthält keinen produktiven
SAP-Konnektor; die Tabelle zeigt, welcher Baustein der Demo im Betrieb ersetzt wird.

| Baustein | Demo (dieses Repository) | Produktivbetrieb |
|---|---|---|
| Daten | `src/seed_data.py` (synthetisch) | SAP-Konnektor liefert denselben `Datenbestand` (ACDOCA/ACDOCP) |
| KI-Schlüssel | ohne Schlüssel: Mock-Modus | Secret-Manager → Umgebungsvariable; Mac-App: Schlüsselbund |
| Anmeldung | lokal (127.0.0.1) | Single Sign-on, Rollen, persistentes Audit-Log |

---

## Einordnung im Portfolio

VarianceAI und ControllAI bilden zusammen eine kleine Controlling-Suite: dasselbe fiktive Unternehmen und ein
gemeinsames Zahlenwerk (Buchungskreis 1000, Stichtag 30.09.2026).

| Projekt | Ebene | Kernfrage | Schwerpunkte |
|---|---|---|---|
| **VarianceAI** | Operatives Controlling & Reporting | Warum weicht das Ergebnis vom Plan ab – und wo landen wir am Jahresende? | Preis-/Mengen-/Mix-Analyse, Plankostenrechnung, Forecast, IBCS-Bericht |
| **ControllAI** | Profitabilität, Strategie & BI | Welche Kunden, Produkte und Prozesse tragen den Erfolg? | Deckungsbeitragsrechnung DB I–III, ABC/Pareto, Order-to-Cash mit DSO, Balanced Scorecard, Text-to-SQL |

**Abgrenzung:** VarianceAI erklärt den Monat – Ursachenzerlegung, Forecast und automatisierter Monatsbericht – und
setzt auf eine Tool-Whitelist statt freiem SQL. ControllAI zeigt, wer das Ergebnis trägt. Umsatz, Herstellkosten,
Kostenstellenkosten und EBIT stimmen in beiden Projekten centgenau überein.

---

## Projektstruktur

```text
VarianceAI/
├── README.md
├── LICENSE                       MIT
├── .gitignore
├── .github/workflows/ci.yml     Automatische Prüfung bei jedem Push: ruff und pytest (Python 3.11 und 3.13)
├── .env.example                  LLM-Anbieter, Modell-IDs, Limits (ohne Secrets)
├── CLAUDE.md                     Arbeitsregeln für KI-gestützte Entwicklung (Agenten-Standard v1)
├── pyproject.toml · uv.lock      Abhängigkeiten (uv, gesperrte Versionen), pytest- und ruff-Konfiguration
├── requirements.txt              dieselben Abhängigkeiten für pip
├── app.py                        Streamlit-Cockpit (6 Tabs, hell/dunkel, Offline-Modus)
├── .streamlit/config.toml        Thema-Startwerte, Schriften
├── desktop/                      Mac-App: app.toml, launcher.py, server.py, build_app.py, praeferenzen.py, Symbol
├── run_agent.py                  CLI: daten, pruefen, analyse, forecast, kommentare, frage, bericht, export
├── demo.py                       Kompletter Monatsabschluss im Offline-Modus
├── src/
│   ├── agent.py                  Agent, System-Prompt, Schemata, Zahlen- und Konsistenzprüfung, Whitelist, LLM-Clients
│   ├── config.py                 Wesentlichkeit, Szenarioannahmen, Forecast-Parameter, Pfade, Seed
│   ├── seed_data.py              Synthetische Daten (ACDOCA-Stil), 11 eingebaute Fälle, SQLite
│   ├── datenpruefung.py          Monatsabschluss-Prüfung: 9 Prüfpunkte, Ampel, Protokoll, Bereinigung
│   ├── variance_engine.py        Preis/Menge/Mix/Sortiment, Plankostenrechnung, EBIT-Brücke, Drill-down, Faktenblatt
│   ├── forecast_engine.py        Saisonal-naiv, Holt-Winters, Backtesting (WAPE), Intervalle, Szenarien, Güte
│   ├── pipeline.py               Orchestrierung: Prüfung → Forecast → Analyse → Faktenblatt → Agent
│   ├── ui_design.py              Designsystem: Farbtoken, Stylesheet, HTML-Bausteine (maskiert)
│   ├── ui_diagramme.py           Interaktive IBCS-Diagramme (Altair): Brücke, Abweichungen, Verlauf, Forecast
│   ├── reporting_ibcs.py         IBCS-Diagramme, Monatsbericht PowerPoint und PDF
│   └── exporter.py               Excel mit Formel-Gegenprüfung, Power-BI-Paket
├── static/                      Schriften (SIL OFL) und Favicon, offline ausgeliefert
├── data/README.md                Hinweis: Datenbank wird lokal erzeugt (nicht im Repository)
├── prompts/README.md             Prompt-Versionierung
├── docs/
│   ├── methodik.md               Rechenwege, Rundung, EBIT-Brücke, Testfälle mit Ergebnissen
│   ├── STANDARD.md               Agenten-Standard v1 (Aufbau, Technik, Sicherheitsregeln)
│   └── screenshots/              Cockpit, Monatsbericht, Excel-Gegenprüfung
└── tests/                        289 Tests (15 Module, siehe unten)
```

---

## Installation & Schnellstart

Voraussetzung: [uv](https://docs.astral.sh/uv/) (installiert Python bei Bedarf selbst). Alternativ funktioniert pip
mit `requirements.txt`.

```bash
git clone https://github.com/<benutzername>/VarianceAI.git
cd VarianceAI
uv sync                              # Umgebung mit gesperrten Versionen aus uv.lock
cp .env.example .env                 # optional: API-Key eintragen (oder Schlüsselbund, siehe unten)
```

### Als Mac-App (empfohlen auf dem Mac)

```bash
uv run python -m desktop.build_app   # erzeugt ~/Applications/VarianceAI.app
```

Danach startet VarianceAI per Doppelklick, über Spotlight oder aus dem Dock – in einem eigenen Fenster ohne Browser.
Die App ist ein Starter für diesen Projektordner: Änderungen am Code sind beim nächsten Öffnen aktiv, ohne neu zu bauen.
Nur lokal erreichbar (127.0.0.1), eine Instanz, Protokolle unter `~/Library/Logs/VarianceAI/`.

**API-Key im macOS-Schlüsselbund** statt in einer Datei (wird vom Starter automatisch gelesen):

```bash
security add-generic-password -s "VarianceAI" -a "ANTHROPIC_API_KEY" -w   # fragt den Schlüssel verdeckt ab
```

### Im Browser

```bash
uv run streamlit run app.py                         # Webmodus: festes dunkles Thema
AGENT_LAUFZEIT=lokal uv run streamlit run app.py    # lokal mit Umschalter hell/dunkel
```

### Online-Demo

Ohne Installation: <https://variance-ai-shweish.streamlit.app>. Die Demo läuft im Offline-Modus (Mock-Agent, kein
API-Key) mit festem dunklem Thema; die LibreOffice-gestützte Excel-Gegenprüfung wird dort übersprungen, alle Exporte
funktionieren.

**Kompletter Monatsabschluss im Offline-Modus (ohne API-Key, rund 10 Sekunden):**

```bash
python demo.py              # Daten → Prüfung → Analyse → Forecast → Kommentare → Freigabe → Bericht → Excel → Power BI
```

Ausgaben landen in `output/demo/`: `bericht/Monatsbericht_2026-09.pptx` und `.pdf`, `export/VarianceAI_2026-09.xlsx`,
`export/powerbi.zip`, `kommentare.json`. Die Demo zeigt außerdem, wie ein manipulierter KI-Text
(„Alle Kostenstellen liegen im Plan.“) von der Konsistenzprüfung verworfen wird.

**Streamlit-Cockpit:**

```bash
streamlit run app.py
```

Tabs *Monatsübersicht*, *Abweichungsanalyse*, *Forecast*, *KI-Kommentare*, *Frag den Controller* und
*Agent & Audit*. In der Seitenleiste stehen Berichtsperiode und Monat/YTD; Wesentlichkeitsschwellen, KI-Anbieter und
Seed liegen unter *Einstellungen*.

**Kommandozeile:**

```bash
python run_agent.py daten                                   # synthetische Daten → data/varianceai.db
python run_agent.py pruefen                                 # Ampel und Prüfprotokoll (Exit-Code 2 bei ROT)
python run_agent.py analyse --top 5                         # Top-Treiber
python run_agent.py analyse --objekt KST-4300               # eine Abweichung mit Drill-down
python run_agent.py forecast --kennzahl Umsatz
python run_agent.py kommentare --objekte EBIT KST-4100      # Entwürfe → output/kommentare.json
python run_agent.py kommentare --objekte EBIT --freigeben "Max Mustermann"
python run_agent.py frage "Warum liegt die Kostenstelle 4200 über Plan?"
python run_agent.py bericht                                 # PowerPoint + PDF (nur freigegebene Kommentare)
python run_agent.py export --pruefen                        # Excel + Power BI, Formel-Gegenprüfung per LibreOffice
python run_agent.py --periode 6 --ytd analyse               # andere Periode, kumuliert
python run_agent.py diagnose                                # Umgebung prüfen: Pakete, Versionen, Importfehler
```

Für die automatische Formel-Gegenprüfung (`export --pruefen`, `demo.py`) wird optional
[LibreOffice](https://www.libreoffice.org/) benötigt; ohne LibreOffice wird die Prüfung übersprungen
und das Blatt *Gegenprüfung* rechnet beim Öffnen in Excel.

**Fehlersuche:** Meldet das Cockpit einen Importfehler, ist fast immer die Python-Umgebung gemischt (alte und neue
Pakete nebeneinander). `python run_agent.py diagnose` – auch in der Seitenleiste unter *Systemprüfung* – zeigt das
betroffene Paket; Abhilfe schafft `uv sync` (oder eine frische virtuelle Umgebung mit `pip install -r requirements.txt`). Jeder Tab hat
eine eigene Fehlergrenze: ein Fehler legt nicht das ganze Cockpit lahm.

**Mit Claude oder Gemini:** in `.env` `LLM_ANBIETER=claude` bzw. `gemini` sowie den passenden API-Key
setzen. Fehlt der Key, wechselt der Agent automatisch in den Offline-Modus.

```python
from src.agent import erstelle_llm_client
from src.pipeline import fuehre_monatsabschluss_durch

abschluss = fuehre_monatsabschluss_durch()            # Prüfung → Forecast → Analyse → Faktenblatt
print(abschluss.protokoll.als_markdown())             # Ampel und Prüfprotokoll

agent = abschluss.agent(erstelle_llm_client())
entwurf = agent.kommentiere("UMS-SUED-ANT")
freigegeben = entwurf.freigeben("Max Mustermann")     # Human-in-the-Loop
print(agent.beantworte("Warum liegt die Kostenstelle 4200 über Plan?").text)
```

---

## Tests

```bash
uv run pytest
```

**289 Tests**, Laufzeit gut eine Minute – vollständig offline, ohne API-Key. Bei jedem Push laufen ruff und pytest
automatisch über GitHub Actions auf Python 3.11 und 3.13 (`.github/workflows/ci.yml`). Geprüft werden u. a.:

- **Jeder eingebaute Fall wird erkannt** und dem richtigen Effekt zugeordnet (Preis, Menge, Mix, Sortiment, Preis-, Verbrauchs-, Beschäftigungsabweichung, Einmaleffekt) – teils centgenau gegen das Hauptbuch nachgerechnet
- **Mathematische Invarianten auf den Cent** über alle 687 Analyseobjekte: Summe aller Effekte = Gesamtabweichung; Summe Drill-down = Gesamtebene; Effekte ab Produktgruppe additiv über alle Ebenen; Jahresendprognose = Ist YTD + Restmonate; Forecast reproduzierbar bei festem Seed
- **Datenprüfung:** Dublette und fehlende Periode werden gefunden, Summenabstimmung erklärt jede Differenz; ROT-Fälle (Lücke, Vorzeichen, Periodenzuordnung, Stammdaten) sperren die Analyse
- **Faktenprüfung:** deutsche Zahlenformate, erfundene Werte, falsche Vorzeichen und Einheiten, selbst gerechnete Summen
- **Tool-Whitelist:** abschließend, unveränderlich, strikte Parameter, keine SQL-Ausführung, Anbindung an die echte Engine
- **Prompt Injection:** Der Injection-Buchungstext erreicht das LLM nie; ein manipuliertes LLM wird auch ohne Zahlen („Alle Kostenstellen liegen im Plan.“) verworfen
- **Human-in-the-Loop:** Entwurfsstatus, namentliche Freigabe, Neuprüfung manueller Änderungen
- **EBIT-Brücke:** DB-Effekte − Kostenstellenabweichungen − Plankostenanpassung = ΔEBIT auf den Cent
- **Reporting:** IBCS-Farben und -Notation, Brücken-Invarianten, Bericht enthält nur freigegebene Kommentare, Folienstruktur
- **Export:** Excel enthält echte Formeln **und** vorberechnete Werte (lesbar auch in Vorschau und pandas),
  LibreOffice-Neuberechnung ohne Fehler, eine Manipulation führt zu *FEHLER*;
  Power-BI-Summen = Analyse, referenzielle Integrität, lückenloser Kalender, deutsches CSV-Format
- **CLI, Demo und Cockpit:** alle Kommandos, Exit-Code bei roter Ampel, kompletter Demo-Durchlauf, Streamlit-Tabs
  (Kennzahlen, Drill-down, Fragen mit Tool-Kette und Quellzahlen, Freigabe nur mit Namen, Injection-Test mit
  Audit-Eintrag) über `streamlit.testing`
- **Desktop-App:** Konfiguration, Einstellungen (atomar, nur erlaubte Werte), Einzelinstanz, Schlüssel-Vorrang
  (Umgebung > `.env` > Schlüsselbund), Server nur auf 127.0.0.1, Server endet auch nach hartem Abbruch des Launchers,
  gültiges App-Bundle; Umschalten hell/dunkel per `AppTest`
- **Designsystem:** HTML-Maskierung gegen eingeschleuste Inhalte, keine Inline-SVG, Brücken schließen centgenau,
  Vega-Lite-Spezifikationen gültig, EBIT-Monatswerte = Ergebnisrechnung

Die Rechenwege und alle Testfälle mit Ergebnissen sind in [`docs/methodik.md`](docs/methodik.md) dokumentiert.

---

## Glossar Deutsch–Englisch

Die Oberfläche ist deutsch; die englischen Fachbegriffe stehen als Zweitbeschriftung an Seiten, Kennzahlen und Karten.

| Deutsch | Englisch |
|---|---|
| Monatsübersicht | Monthly Overview |
| Abweichungsanalyse (Plan-Ist) | Variance Analysis (Plan vs. Actual) |
| Umsatz · Deckungsbeitrag · Kostenstellenkosten | Revenue · Contribution Margin · Cost Center Costs |
| Preis-, Mengen-, Mixeffekt | Price, Volume, Mix Effect |
| Verbrauchs-, Beschäftigungsabweichung | Usage Variance, Volume (Capacity) Variance |
| EBIT-Brücke | EBIT Bridge |
| Jahresendprognose · Bandbreite | Year-End Forecast · Range |
| Wesentlichkeit | Materiality |
| KI-Kommentare · Freigabe | AI Commentary · Approval |
| Audit-Protokoll | Audit Log |

---

## Hinweise

- Alle Daten sind **synthetisch**. Die *RheinMain Industrietechnik GmbH* ist ein fiktives Unternehmen; Ähnlichkeiten mit realen Unternehmen sind zufällig.
- VarianceAI ist ein **Portfolio- und Demonstrationsprojekt**. Es ersetzt weder einen echten Monats- oder Jahresabschluss noch die fachliche Prüfung durch qualifizierte Controller.
- KI-generierte Texte sind Entwürfe und müssen vor der Verwendung fachlich geprüft und freigegeben werden.
- Es werden keine Secrets im Repository gespeichert; API-Keys gehören ausschließlich in die lokale `.env`.

---

## Lizenz & Autor

MIT-Lizenz – siehe [LICENSE](LICENSE).

**Sherwan Shweish** · B.Sc. Digital Business Management, Hochschule RheinMain
Fokus: Controlling, Datenanalyse und Reporting · Python, SQL, Excel, Power BI, BPMN/eEPK, SAP S/4HANA
