"""Monatsabschluss-Prüfung: Ampel, Befunde und Bereinigung."""

from __future__ import annotations

import pandas as pd
import pytest

from src.agent import Ampel
from src.datenpruefung import _abstimmungsdifferenzen, pruefe_monatsabschluss
from src.seed_data import KONTO_ERLOES, erzeuge_daten


def test_gesamtampel_gelb_und_analyse_freigegeben(pruefung):
    p = pruefung.protokoll
    assert p.ampel is Ampel.GELB
    assert p.analyse_freigegeben
    assert {pt.id for pt in p.punkte if pt.ampel is Ampel.ROT} == set()


def test_dublette_gefunden_und_bereinigt(rohdaten, pruefung):
    dub = rohdaten.meta["dublette"]
    punkt = pruefung.protokoll.punkt("dubletten")
    assert punkt.ampel is Ampel.GELB and punkt.anzahl_befunde == 1
    assert dub["xblnr"] in punkt.befunde[0]
    assert dub["original_belnr"] in punkt.befunde[0] and dub["belnr"] in punkt.befunde[0]
    assert len(rohdaten.acdoca) - len(pruefung.daten.acdoca) == 1
    assert dub["belnr"] not in set(pruefung.daten.acdoca["belnr"])
    assert dub["belnr"] in set(rohdaten.acdoca["belnr"])  # Original unverändert


def test_fehlende_periode_gefunden(pruefung):
    punkt = pruefung.protokoll.punkt("vollstaendigkeit_ist")
    assert punkt.ampel is Ampel.GELB
    assert punkt.befunde == ["Kostenstelle 4500 (Qualitätssicherung): keine Ist-Kosten und Leistungsmengen 07/2025"]


@pytest.mark.invariante
def test_summenabstimmung_erklaert_differenzen_auf_den_cent(rohdaten, pruefung):
    er = rohdaten.ergebnisrechnung
    roh = _abstimmungsdifferenzen(rohdaten.acdoca, er)
    nachher = _abstimmungsdifferenzen(pruefung.daten.acdoca, er)
    # Dublette erklärt genau eine Differenz im Berichtsmonat
    dublette = roh[(roh["gjahr"] == 2026) & (roh["poper"] == 9)]
    assert dublette["racct"].tolist() == [KONTO_ERLOES]
    assert int(dublette["differenz"].iloc[0]) == rohdaten.meta["dublette"]["hsl_cent"]
    # Nach Bereinigung bleiben nur Differenzen der fehlenden Vorjahresperiode
    assert set(zip(nachher["gjahr"], nachher["poper"], strict=True)) == {(2025, 7)}
    punkt = pruefung.protokoll.punkt("summenabstimmung")
    assert punkt.ampel is Ampel.GELB
    assert "durch entfernte Dubletten erklärt" in punkt.befunde[0]


def test_injektionstext_wird_erkannt(pruefung):
    punkt = pruefung.protokoll.punkt("buchungstexte")
    assert punkt.ampel is Ampel.GELB and punkt.anzahl_befunde == 1
    assert "nur bereinigt" in punkt.befunde[0]


def test_buchung_ohne_menge_wird_gemeldet(pruefung):
    assert pruefung.protokoll.punkt("mengen").anzahl_befunde == 1


def test_fehlerfreie_daten_sind_in_datenfehler_pruefpunkten_gruen():
    p = pruefe_monatsabschluss(erzeuge_daten(datenfehler_einbauen=False)).protokoll
    for punkt_id in ("stammdaten", "vollstaendigkeit_ist", "vollstaendigkeit_plan", "dubletten",
                     "summenabstimmung", "vorzeichen", "periodenzuordnung"):
        assert p.punkt(punkt_id).ampel is Ampel.GRUEN, punkt_id


def _mit(rohdaten, aenderung) -> pd.DataFrame:
    d = rohdaten.kopie()
    d.acdoca = aenderung(d.acdoca.copy())
    return d


def test_fehlende_berichtsperiode_ist_rot(rohdaten):
    def ohne_west(a):
        return a[~((a["version"] == "IST") & (a["gjahr"] == 2026) & (a["poper"] == 9) & (a["regio"] == "WEST"))]

    p = pruefe_monatsabschluss(_mit(rohdaten, ohne_west)).protokoll
    assert p.punkt("vollstaendigkeit_ist").ampel is Ampel.ROT
    assert p.ampel is Ampel.ROT and not p.analyse_freigegeben


def test_vorzeichenfehler_in_berichtsperiode_ist_rot(rohdaten):
    def vorzeichen(a):
        i = a[(a["version"] == "IST") & (a["gjahr"] == 2026) & (a["poper"] == 9) & (a["racct"] == "435000")].index[0]
        a.loc[i, "hsl_cent"] = -a.loc[i, "hsl_cent"]
        return a

    assert pruefe_monatsabschluss(_mit(rohdaten, vorzeichen)).protokoll.punkt("vorzeichen").ampel is Ampel.ROT


def test_falsche_periodenzuordnung(rohdaten):
    def datum(a):
        i = a[(a["version"] == "IST") & (a["gjahr"] == 2026) & (a["poper"] == 9)].index[0]
        a.loc[i, "budat"] = "2026-10-02"
        return a

    punkt = pruefe_monatsabschluss(_mit(rohdaten, datum)).protokoll.punkt("periodenzuordnung")
    assert punkt.ampel is Ampel.ROT and "2026-10-02" in punkt.befunde[0]


def test_unbekannte_stammdaten_sind_rot(rohdaten):
    def kunde(a):
        a.loc[a.index[0], "kunnr"] = "K999"
        return a

    assert pruefe_monatsabschluss(_mit(rohdaten, kunde)).protokoll.punkt("stammdaten").ampel is Ampel.ROT


def test_ohne_bereinigung_sperrt_dublette_die_analyse(rohdaten):
    ergebnis = pruefe_monatsabschluss(rohdaten, bereinigen=False)
    assert ergebnis.protokoll.punkt("dubletten").ampel is Ampel.ROT
    assert len(ergebnis.daten.acdoca) == len(rohdaten.acdoca)


def test_protokoll_ausgaben(pruefung):
    md = pruefung.protokoll.als_markdown()
    assert "Gesamtampel" in md and "Automatische Bereinigungen" in md
    dq = pruefung.protokoll.als_datenqualitaet()
    assert dq.ampel is Ampel.GELB and any(h.startswith("Dubletten") for h in dq.hinweise)
    assert pruefung.protokoll.model_dump_json()  # revisionssicher serialisierbar
