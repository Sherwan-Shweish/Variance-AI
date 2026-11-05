"""Null-Halluzinations-Prüfung und deutsche Zahlenformate."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.agent import (
    Einheit,
    extrahiere_zahlen,
    format_betrag,
    format_prozent,
    pruefe_text,
    regelbasierter_forecast_text,
    regelbasierter_kommentar,
)

# --- Formatierung -----------------------------------------------------------

@pytest.mark.parametrize(
    ("wert", "skala", "erwartet"),
    [
        ("1234500", "auto", "1,2 Mio. €"),
        ("1234567", "T€", "1.234,6 T€"),
        ("-1234.5", "auto", "−1,2 T€"),
        ("-190000", "auto", "−190,0 T€"),
        ("512", "auto", "512 €"),
        ("-0.04", "T€", "0,0 T€"),  # keine „−0,0“
    ],
)
def test_format_betrag(wert, skala, erwartet):
    assert format_betrag(D(wert), skala) == erwartet


def test_format_prozent_und_kaufmaennische_rundung():
    assert format_prozent(D("-4.2")) == "−4,2 %"
    assert format_prozent(D("12.25")) == "+12,3 %"  # ROUND_HALF_UP
    assert format_prozent(D("85"), vorzeichen=False) == "85,0 %"


# --- Zahlenextraktion -------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "betrag", "vorzeichen", "einheit", "skala"),
    [
        ("Umsatz 1,2 Mio. €", D("1.2"), None, Einheit.EUR, 10**6),
        ("Abweichung −4,2 %", D("4.2"), "-", Einheit.PROZENT, 1),
        ("1.234,5 T€ Kosten", D("1234.5"), None, Einheit.EUR, 1000),
        ("Marge +3,1 PP", D("3.1"), "+", Einheit.PROZENTPUNKTE, 1),
        ("Delta – 190 TEUR", D("190"), "-", Einheit.EUR, 1000),
        ("570.000 €", D("570000"), None, Einheit.EUR, 1),
        ("12,5 Prozent", D("12.5"), None, Einheit.PROZENT, 1),
        ("2,1 Mrd. Euro", D("2.1"), None, Einheit.EUR, 10**9),
        ("4.200 Stück", D("4200"), None, Einheit.STUECK, 1),
    ],
)
def test_extraktion_deutscher_formate(text, betrag, vorzeichen, einheit, skala):
    (fund,) = extrahiere_zahlen(text)
    assert (fund.betrag, fund.vorzeichen, fund.einheit, fund.skala) == (betrag, vorzeichen, einheit, skala)


def test_bindestrich_in_woertern_ist_kein_vorzeichen():
    (fund,) = extrahiere_zahlen("Plan-Ist-Abweichung 4,2 %")
    assert fund.vorzeichen is None


# --- Prüfung gegen das Faktenblatt ------------------------------------------

def _pruefe(text, faktenwerte, kontext):
    return pruefe_text(text, faktenwerte, kontext)


def test_korrekter_kommentar_besteht(faktenwerte, kontext):
    text = ("Umsatz Region Süd −4,2 % (−190,0 T€) ggü. Plan, überwiegend Mengeneffekt (−160,0 T€) "
            "in der Produktgruppe Antriebe.")
    ergebnis = _pruefe(text, faktenwerte, kontext)
    assert ergebnis.bestanden, ergebnis.verstoesse
    assert all(f.belegt for f in ergebnis.funde)


@pytest.mark.parametrize(
    "text",
    [
        "Umsatz Region Süd −5,1 % ggü. Plan.",              # erfundener Prozentwert
        "Umsatz Region Süd +4,2 % ggü. Plan.",              # falsches Vorzeichen
        "Umsatz Region Süd −4,2 Mio. € ggü. Plan.",         # falsche Einheit
        "Süd und Nord zusammen −340,0 T€.",                 # selbst gerechnete Summe
        "Umsatz Gesamt rund −1 Mio. €.",                    # zu grob gerundet (570 T€)
        "Die Abweichung betrifft 99 Kunden.",               # unbelegte Zahl ohne Einheit
        "Seit 2019 rückläufig.",                            # unbelegtes Jahr
    ],
)
def test_halluzinationen_werden_erkannt(text, faktenwerte, kontext):
    ergebnis = _pruefe(text, faktenwerte, kontext)
    assert not ergebnis.bestanden
    assert ergebnis.unbelegte


@pytest.mark.parametrize(
    "text",
    [
        "Umsatz Region Süd −4 % ggü. Plan.",                # gröber gerundet, aber korrekt
        "Umsatz Gesamt −0,6 Mio. € ggü. Plan.",             # 570 T€ in Mio. €
        "Umsatz Gesamt −570.000,00 € ggü. Plan.",           # centgenau
        "Kostenstelle 4100 im März 2026, Periode 3.",       # Bezeichner, Jahr, Periode
        "Jahresendprognose 153,5 Mio. € (WAPE 3,8 %), Stand 31.03.2026.",
        "Die drei größten Treiber im Q1.",                  # Bezeichner „Q1“
    ],
)
def test_zulaessige_darstellungen_bestehen(text, faktenwerte, kontext):
    ergebnis = _pruefe(text, faktenwerte, kontext)
    assert ergebnis.bestanden, ergebnis.verstoesse


@pytest.mark.parametrize(
    ("text", "meldung"),
    [
        ("Details unter https://example.com −4,2 %", "Links"),
        ("Rückfragen an controlling@example.com", "E-Mail"),
        ("<script>alert(1)</script>", "Markup"),
        ("Umsatz Region Süd −4.2 %", "englischen Format"),
        ("", "Leerer Text"),
    ],
)
def test_formverbote(text, meldung, faktenwerte, kontext):
    ergebnis = _pruefe(text, faktenwerte, kontext)
    assert not ergebnis.bestanden
    assert any(meldung in v for v in ergebnis.verstoesse)


def test_zahlen_aus_datentexten_gelten_nicht_als_beleg(faktenblatt, kontext):
    from src.agent import sammle_faktenwerte

    faktenblatt.abweichung("UMS-WEST").datentexte.append("Korrektur 777.777 €")
    werte = sammle_faktenwerte(faktenblatt)
    assert not pruefe_text("Korrektur 777.777 €", werte, kontext).bestanden


@pytest.mark.invariante
def test_regelbasierte_texte_bestehen_immer_die_pruefung(faktenblatt, faktenwerte, kontext):
    for abw in faktenblatt.abweichungen:
        ergebnis = pruefe_text(regelbasierter_kommentar(abw), faktenwerte, kontext)
        assert ergebnis.bestanden, (abw.id, ergebnis.verstoesse)
    for fc in faktenblatt.forecasts:
        ergebnis = pruefe_text(regelbasierter_forecast_text(fc), faktenwerte, kontext)
        assert ergebnis.bestanden, ergebnis.verstoesse


def test_regelbasierter_kommentar_inhalt(faktenblatt):
    text = regelbasierter_kommentar(faktenblatt.abweichung("KST-4100"))
    assert text.startswith("Kostenstelle 4100 Fertigung: +12,2 % (+110,0 T€) ggü. verrechneten Plankosten, ungünstig.")
    assert "Verbrauchsabweichung +45,0 T€" in text
    assert "Maßnahme:" in text
