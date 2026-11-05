# Daten

Dieses Verzeichnis enthält ausschließlich **synthetische Daten** der fiktiven
*RheinMain Industrietechnik GmbH*. Die SQLite-Datenbank `varianceai.db` wird mit

```bash
python -m src.seed_data
```

reproduzierbar (Seed 42) erzeugt und ist per `.gitignore` von der Versionierung
ausgeschlossen.

| Tabelle | Inhalt |
|---|---|
| `acdoca` | Einzelposten Ist und Plan im Stil des Universal Journals (Beträge in Cent, Soll +/Haben −) |
| `kst_leistung` | Fertigungsstunden je Kostenstelle und Periode |
| `ergebnisrechnung` | Summen je Version, Periode und Kostenart (Abstimmbasis) |
| `regionen`, `produktgruppen`, `material`, `kunden`, `kostenstellen`, `kostenarten` | Stammdaten |
| `forecast` | Forecast-Versionen (wird von `src.pipeline.speichere_forecasts` geschrieben) |
| `meta` | Seed, Geschäftsjahr, Berichtsperiode, eingebaute Datenfehler |

Die bewusst eingebauten Fälle (Preis-, Mengen-, Mix-, Sortimentseffekt,
Kostenstellenabweichungen, Einmaleffekt, Datenfehler, Prompt-Injection-Testfall)
sind in [`../docs/methodik.md`](../docs/methodik.md) beschrieben.

Es werden keine echten Unternehmens- oder Personendaten verwendet.
