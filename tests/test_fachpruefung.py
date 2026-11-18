"""Fachliche Absicherungen aus der Controlling-Prüfung (Version 1.3.1).

* Einmaleffekte gehören ins Ist, dürfen aber nicht in die Restmonate fortgeschrieben werden.
* Texte des Offline-Clients sind Regeltexte – sie werden nirgends als „KI-generiert“ ausgewiesen.
* Beschäftigungsabweichung und Plankostenanpassung ergeben zusammen die rein beschäftigungsbedingte Kostenänderung.
"""

from __future__ import annotations

from fractions import Fraction

from src.agent import DEMO_QUELLE, Effektart, MockLLMClient, Textquelle, quellenangabe
from src.config import Einstellungen
from src.forecast_engine import monatsreihe, prognostiziere
from src.pipeline import fuehre_monatsabschluss_durch
from src.seed_data import SONDERABSCHREIBUNG


def test_einmaleffekt_bleibt_im_ist_wird_aber_nicht_fortgeschrieben(rohdaten):
    abschluss = fuehre_monatsabschluss_durch(rohdaten.kopie())
    mit = abschluss.forecasts["Kosten"]
    ohne = abschluss.daten.kopie()
    ohne.acdoca = ohne.acdoca[ohne.acdoca["racct"] != SONDERABSCHREIBUNG[1]]
    referenz = prognostiziere(ohne, "Kosten", abschluss.einstellungen)

    assert mit.einmaleffekte_ytd_cent == SONDERABSCHREIBUNG[2]
    assert [m.wert_cent for m in mit.monate] == [m.wert_cent for m in referenz.monate]
    # Das Ist YTD enthält die Sonderabschreibung weiterhin vollständig.
    assert mit.info.ist_ytd - referenz.info.ist_ytd == Fraction(SONDERABSCHREIBUNG[2], 100)
    assert mit.info.jahresendprognose - referenz.info.jahresendprognose == Fraction(SONDERABSCHREIBUNG[2], 100)


def test_prognosebasis_ohne_einmaleffekte_unterscheidet_sich_nur_um_den_einmaleffekt(rohdaten):
    abschluss = fuehre_monatsabschluss_durch(rohdaten.kopie())
    voll = monatsreihe(abschluss.daten, "Kosten")
    basis = monatsreihe(abschluss.daten, "Kosten", ohne_einmaleffekte=True)
    differenz = (voll - basis)
    assert int(differenz.sum()) == SONDERABSCHREIBUNG[2]
    assert int((differenz != 0).sum()) == 1
    # Umsatz und Deckungsbeitrag kennen keine Einmaleffekte.
    assert abschluss.forecasts["Umsatz"].einmaleffekte_ytd_cent == 0


def test_demo_texte_werden_nicht_als_ki_generiert_ausgewiesen(rohdaten):
    abschluss = fuehre_monatsabschluss_durch(rohdaten.kopie())
    agent = abschluss.agent(MockLLMClient())
    kommentar = agent.kommentiere("KST-4200")
    antwort = agent.beantworte("Warum liegt die Kostenstelle 4200 über Plan?")
    assert kommentar.quellenangabe == DEMO_QUELLE == antwort.quellenangabe
    assert "KI" not in DEMO_QUELLE.replace("ohne KI", "")
    assert quellenangabe(Textquelle.LLM, "claude-sonnet") == "KI-generiert"
    assert quellenangabe(Textquelle.REGELBASIERT, "mock-controller-v1") == "regelbasiert"


def test_beschaeftigung_und_plankostenanpassung_ergeben_die_beschaeftigungsbedingte_kostenaenderung(rohdaten):
    abschluss = fuehre_monatsabschluss_durch(rohdaten.kopie(), Einstellungen())
    analyse = abschluss.analyse
    effekte = {t.effektart: int(t.betrag * 100) for t in analyse.abweichung("EBIT").treiber}
    erwartet = Fraction(0)
    for rechnung in analyse.kostenstellen.values():
        variabel = sum(w.plan_var for w in rechnung.werte.values())
        erwartet += variabel * (1 - rechnung.beschaeftigungsgrad)  # Wirkung auf das EBIT: eingesparte variable Kosten
    summe = effekte[Effektart.BESCHAEFTIGUNG] + effekte[Effektart.PLANKOSTENANPASSUNG]
    assert abs(summe - erwartet) <= len(analyse.kostenstellen) * 10  # Rundung je Kostenart auf den Cent
