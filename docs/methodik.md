# Methodik und Rechenwege

Dieses Dokument beschreibt die fachlichen Rechenwege von VarianceAI und die
bewusst in den synthetischen Daten eingebauten Testfälle. Alle Zahlen stammen
aus dem Standardlauf (Seed 42, Monatsabschluss September 2026).

---

## 1. Datenmodell (angelehnt an SAP S/4HANA)

| Tabelle | Inhalt | Vorbild |
|---|---|---|
| `acdoca` | Einzelposten Ist und Plan (Spalte `version`), Soll positiv / Haben negativ, Beträge in Cent | Universal Journal ACDOCA, Plan wie ACDOCP |
| `kst_leistung` | Fertigungsstunden je Kostenstelle und Periode (Ist und Plan) | Leistungsarten / statistische Kennzahlen |
| `ergebnisrechnung` | Summen je Version, Periode und Kostenart | Abstimmbasis aus dem Hauptbuch |
| Stammdaten | Regionen, Produktgruppen, Material, Kunden, Kostenstellen, Kostenarten | Material-, Kunden-, CO-Stammdaten |

Umfang: 36 Monate Ist (10/2023–09/2026), Plan für alle 12 Perioden 2026,
Saisonfaktoren je Monat, ca. 11.600 Buchungszeilen, fester Seed.

---

## 2. Abschlussprüfung

| Prüfpunkt | ROT, wenn … | GELB, wenn … |
|---|---|---|
| Stammdaten-Referenzen | unbekannte Kostenart, Kostenstelle, Material, Kunde oder Region | – |
| Vollständigkeit Ist | Lücke im Berichtsjahr bis zur Berichtsperiode | Lücke nur in der Historie |
| Vollständigkeit Plan | Planlücke bis zur Berichtsperiode | Planlücke in Folgeperioden |
| Dubletten | gleiche Belegnummer/Position oder gleiche Rechnungsreferenz, nicht bereinigt | automatisch bereinigt (spätere Belegnummer entfernt) |
| Summenabstimmung | Differenz Detail vs. Ergebnisrechnung im Berichtszeitraum | Differenz nur in Vorjahresperioden oder durch Dublette erklärt |
| Vorzeichen | Erlöse im Soll bzw. Kosten im Haben im Berichtszeitraum | in der Historie |
| Periodenzuordnung | Buchungsdatum passt nicht zur Periode (Berichtszeitraum) | in der Historie |
| Mengen | – | Buchung ohne Menge auf mengenbasierter Kostenart |
| Buchungstexte | – | Text mit Mustern einer versteckten Anweisung |

Bei Gesamtampel ROT sperrt die Pipeline die Analyse (`AbschlussGesperrt`).

---

## 3. Umsatz und Deckungsbeitrag: Preis, Menge, Mix, Sortiment

Für die Produkte einer Produktgruppe, die in Ist **und** Basis (Plan bzw.
Vorjahr) Menge haben, gilt mit $M$ = Gesamtmenge, $s_i = m_i / M$ und
$p_i = W_i / m_i$:

```math
\text{Menge} = (M_{ist} - M_{b}) \cdot \frac{W_b}{M_b}
\qquad
\text{Mix} = \sum_i m_{i,ist}\, p_{i,b} - M_{ist} \cdot \frac{W_b}{M_b}
\qquad
\text{Preis} = W_{ist} - \sum_i m_{i,ist}\, p_{i,b}
```

Dabei ist $\frac{W_b}{M_b} = \sum_i s_{i,b}\, p_{i,b}$ der durchschnittliche
Basispreis. Die Summe der drei Effekte ist algebraisch exakt $W_{ist} - W_b$.

* **Sortimentseffekt:** Produkte nur im Ist (Neuprodukt) oder nur in der Basis
  (Wegfall) gehen mit ihrer vollen Wertdifferenz ein.
* **Mix nur innerhalb der Produktgruppe:** Region und Gesamt sind die Summe
  ihrer Produktgruppen. Dadurch sind alle Effekte ab Produktgruppe über die
  Hierarchie additiv; auf Produkt- und Kundenebene gibt es definitionsgemäß
  keinen Mix.
* **Deckungsbeitrag:** gleiche Zerlegung mit dem Stück-DB
  (Umsatz − Herstellkosten zu Standard). Weil die Standardkosten in Plan und Ist
  identisch sind, ist der Preiseffekt im DB exakt gleich dem im Umsatz.

### Lehrbuchbeispiel (Testfall)

| Produkt | Plan | Ist |
|---|---|---|
| A | 100 Stk × 10 € = 1.000 € | 150 Stk × 11 € = 1.650 € |
| B | 100 Stk × 20 € = 2.000 € | 70 Stk × 20 € = 1.400 € |

Mengeneffekt +300 €, Mixeffekt −400 €, Preiseffekt +150 € = Gesamtabweichung +50 €.

---

## 4. Kostenstellen: flexible Plankostenrechnung auf Vollkostenbasis

Mit Beschäftigungsgrad $BG = \text{Ist-Stunden} / \text{Plan-Stunden}$ je Kostenart:

| Größe | Formel |
|---|---|
| verrechnete Plankosten | (fix + variabel) × BG |
| Sollkosten | fix + variabel × BG |
| Istkosten zu Planpreisen | Ist-Menge × Planpreis (+ Buchungen ohne Menge zu Istwerten) |
| Preisabweichung | Istkosten − Istkosten zu Planpreisen |
| Verbrauchsabweichung | Istkosten zu Planpreisen − Sollkosten |
| Beschäftigungsabweichung | Sollkosten − verrechnete Plankosten = fixe Plankosten × (1 − BG) |

Ungeplante, als einmalig gekennzeichnete Kostenarten (Sonderabschreibung)
werden vollständig als **Einmaleffekt** ausgewiesen, damit sie die
Verbrauchsabweichung nicht verfälschen.

### Vereinfachte Ergebnisrechnung und EBIT-Brücke

| Zeile | Rechnung |
|---|---|
| Umsatz | Erlöse (Haben, mit umgekehrtem Vorzeichen) |
| Herstellkosten (Standard) | Absatzmenge × variable Standardherstellkosten je Material (Material, Zukaufteile) |
| **Deckungsbeitrag** | Umsatz − variable Standardherstellkosten |
| Kosten Kostenstellen | Summe der Kostenstellenkosten (beschäftigungsvariabel und fix) |
| **EBIT (vereinfacht)** | Deckungsbeitrag − Kosten Kostenstellen; ohne Vertriebs- und Verwaltungskosten |

Die EBIT-Abweichung zum Plan wird ohne Restgröße in Treiber zerlegt:

```math
\Delta \text{EBIT} = \underbrace{\text{Preis} + \text{Menge} + \text{Mix} + \text{Sortiment}}_{\Delta\,\text{DB}}
- \underbrace{(\text{Preis-} + \text{Verbrauchs-} + \text{Beschäftigungsabw.} + \text{Einmaleffekt})}_{\text{Ist} - \text{verrechnete Plankosten}}
- \underbrace{(\text{verrechnete Plankosten} - \text{Plankosten})}_{\text{Plankostenanpassung}}
```

September 2026: ΔDB +55,1 T€ − Kostenstellenabweichungen 503,6 T€ +
Plankostenanpassung 213,5 T€ = **ΔEBIT −235,1 T€** (EBIT 569,3 T€ statt 804,4 T€,
−29,2 %). Der Test `test_ebit_bruecke_exakt` prüft diese Identität auf den Cent.

---

## 5. Rundung auf den Cent

Alle Effekte werden mit `fractions.Fraction` exakt berechnet. Für die Ausgabe
wird mit dem **Largest-Remainder-Verfahren** gerundet: zuerst abrunden, dann
die fehlenden Cent an die Effekte mit den größten Nachkommaresten verteilen.
So gilt auch nach der Rundung exakt: *Summe der Effekte = Gesamtabweichung*.
Übergeordnete Ebenen entstehen als Summe der gerundeten Unterebenen, daher
stimmt auch *Summe Drill-down = Gesamtebene* auf den Cent.

---

## 6. Forecast

* **Jahresendprognose** = Ist YTD + Prognose der Restmonate.
* **Einmaleffekte:** Als einmalig gekennzeichnete Kostenarten (Sonderabschreibung 240,0 T€) bleiben im Ist YTD,
  werden aber aus der Prognosebasis herausgenommen und nicht fortgeschrieben. Ohne diese Bereinigung würde das
  Modell den Sprung als neues Kostenniveau deuten und die Restmonate um rund 0,56 Mio. € zu hoch ansetzen.
* **Methoden:** saisonal-naiv und Holt-Winters (ETS mit gedämpftem Trend).
* **Auswahl** je Zeitreihe per Rolling-Origin-Backtest (Mindesthistorie 24
  Monate, Horizont 3 Monate) nach WAPE; bei Gleichstand gewinnt saisonal-naiv.
* **Intervalle** (80 %) aus 2.000 simulierten Pfaden mit festem Seed.
* **Szenarien:** Best/Worst passen die Restmonate um konfigurierte Prozentsätze an
  (Standard: Chance 3 %, Risiko 5 %; bei Kosten mit umgekehrtem Vorzeichen).
* **Forecast-Güte:** Versionen nach Quartalsende (FC03, FC06) werden mit dem
  später eingetretenen Ist verglichen (WAPE und Bias).

| Kennzahl | Backtest-WAPE saisonal-naiv | Backtest-WAPE Holt-Winters | Jahresendprognose | Plan | Abweichung |
|---|---|---|---|---|---|
| Umsatz | 4,8 % | 2,3 % | 88,1 Mio. € | 88,5 Mio. € | −0,5 % |
| Deckungsbeitrag | 5,4 % | 1,9 % | 49,2 Mio. € | 49,0 Mio. € | +0,3 % |
| Kosten Kostenstellen | 7,4 % | 1,8 % | 41,0 Mio. € | 40,1 Mio. € | +2,3 % |

Forecast-Güte Umsatz: FC03 WAPE 4,5 % (Bias −4,5 % – das Neuprodukt ab April war
noch nicht bekannt), FC06 WAPE 1,1 %. Die niedrigen Fehlerwerte folgen aus dem geringen Rauschen der
synthetischen Daten und sind keine Aussage über echte Unternehmensdaten.

---

## 7. Eingebaute Testfälle und Ergebnis (September 2026)

| Nr. | Fall | Objekt | Abweichung | Erkannter Haupttreiber |
|---|---|---|---|---|
| 1 | Preiserhöhung Region Nord | `UMS-NORD` | +242,1 T€ (+9,8 %) | Preiseffekt +148,0 T€ |
| 2 | Mengeneinbruch Antriebe Süd | `UMS-SUED-ANT` | −288,7 T€ (−22,2 %) | Mengeneffekt −289,4 T€ |
| 3 | Mix zu margenschwachen Steuerungen West | `UMS-WEST-STE` | −120,8 T€ (−17,4 %) | Mixeffekt −136,6 T€ |
| 4 | Neuprodukt Smart Sensor X1 ohne Plan | `UMS-NORD-SEN` (u. a.) | +144,1 T€ (+23,4 %) | Sortimentseffekt +104,3 T€ |
| 5 | Energiepreisanstieg Gießerei | `KST-4100-410000` | +104,3 T€ (+35,5 %) | Preisabweichung +99,5 T€ |
| 6 | Materialmehrverbrauch Montage | `KST-4200-400000` | +88,2 T€ (+17,9 %) | Verbrauchsabweichung +88,2 T€ |
| 7 | Unterauslastung Lackiererei | `KST-4300` | +60,2 T€ (+15,9 %) | Beschäftigungsabweichung +61,1 T€ |
| 8 | Sonderabschreibung Instandhaltung | `KST-4400-481000` | +240,0 T€ (ungeplant) | Einmaleffekt +240,0 T€ |
| 9 | Dublette Rechnung | – | 134.900,00 € doppelt | Datenprüfung: GELB, bereinigt |
| 10 | Fehlende Periode | – | KST 4500, 07/2025 | Datenprüfung: GELB (Historie) |
| 11 | Buchungstext mit versteckter Anweisung | `KST-4200-400000` | – | Datenprüfung: GELB; Text wird dem LLM nur bereinigt übergeben |

**Gegenläufige Effekte auf Gesamtebene:** Der Umsatz liegt insgesamt nur
+39,5 T€ (+0,5 %) über Plan – dahinter stehen Sortimentseffekt +325,3 T€,
Preiseffekt +148,0 T€, Mengeneffekt −301,0 T€ und Mixeffekt −132,8 T€. Genau
diese Zerlegung macht die Analyse auf Gesamtebene erst aussagekräftig.

---

## 8. Sicherheit des KI-Agenten

1. **Datenisolierung:** Faktenblatt und Tool-Ergebnisse werden als JSON in
   Datenblöcke eingebettet; spitze Klammern werden maskiert.
2. **Bereinigung:** Auffällige Datentexte werden vor dem LLM-Aufruf ersetzt –
   im Faktenblatt und in jedem Tool-Ergebnis.
3. **Tool-Whitelist:** fünf feste, streng typisierte Analysefunktionen; kein SQL.
4. **Zahlenprüfung:** jede Zahl mit Einheit, Vorzeichen und Rundung belegt.
5. **Konsistenzprüfung für Kommentare:** Pflichtangabe der Abweichung, keine
   Entwarnung bei wesentlichen Abweichungen, Bewertung passend zur Vorzeichenlogik.
   Damit wird auch ein manipulierter Text ohne Zahlen („Alle Kostenstellen liegen
   im Plan.“) verworfen.
6. **Human-in-the-Loop:** Nur namentlich freigegebene Kommentare sind berichtsfähig.

---

## 9. Berichtswesen und Export

* **IBCS-Notation:** Ist (AC) dunkelgrau gefüllt, Vorjahr (PY) hellgrau, Plan (PL)
  als Umriss, Forecast (FC) schraffiert; Abweichungen grün (günstig) bzw. rot
  (ungünstig) und immer mit Vorzeichen beschriftet. Ist eine Abweichung im
  Verhältnis zur Basis klein (< 12 %), zeigt die Brücke nur die Abweichung
  (Start bei 0), damit die Effekte lesbar bleiben.
* **Monatsbericht** (PowerPoint, 8 Folien, und PDF): enthält ausschließlich
  namentlich freigegebene Kommentare; Entwürfe erscheinen nicht.
* **Excel-Bericht:** Eingaben blau, Formeln schwarz. Das Blatt *Gegenprüfung*
  vergleicht jede Excel-Formel (Treibersummen, Δ, Plankostenrechnung,
  Ergebnisrechnung, Drill-down, Forecast-Szenarien) mit dem Python-Ergebnis.
  `pruefe_excel()` rechnet die Datei mit LibreOffice neu und liefert den
  Gesamtstatus und alle Formelfehler.
* **Power-BI-Paket:** Sternschema (Fakten Buchungen, Leistung, Forecast;
  Dimensionen Kalender, Kostenart, Kostenstelle, Material, Kunde, Region),
  deutsches CSV-Format (Semikolon, Dezimalkomma, `TT.MM.JJJJ`, UTF-8 mit BOM),
  DAX-Measures und Power-Query-Skript.
