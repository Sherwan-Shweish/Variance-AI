"""Pydantic-Schemata: Faktenblatt, Abweichung, Forecast, Kommentar, Freigabe."""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from src.agent import (
    Abweichung,
    Bewertung,
    Effektart,
    Faktenblatt,
    ForecastInfo,
    ForecastMethode,
    FreigabeFehler,
    Freigabestatus,
    Kennzahlart,
    Kommentar,
    Pruefergebnis,
    Textquelle,
    Treiber,
    berichtsfaehige_kommentare,
)


def _abw(**kwargs) -> Abweichung:
    basis = dict(id="X-1", bezeichnung="Test", kennzahl="Umsatz", art=Kennzahlart.ERLOES,
                 ist=D("110.00"), vergleichswert=D("100.00"))
    basis.update(kwargs)
    return Abweichung(**basis)


# --- Abweichung -------------------------------------------------------------

def test_delta_und_prozent_werden_in_python_berechnet(faktenblatt):
    sued = faktenblatt.abweichung("UMS-SUED")
    assert sued.delta_abs == D("-190000.00")
    assert sued.delta_pct == D("-4.2")
    assert sued.bewertung is Bewertung.UNGUENSTIG


@pytest.mark.parametrize(
    ("art", "ist", "plan", "erwartet"),
    [
        (Kennzahlart.ERLOES, "110", "100", Bewertung.GUENSTIG),
        (Kennzahlart.ERLOES, "90", "100", Bewertung.UNGUENSTIG),
        (Kennzahlart.KOSTEN, "110", "100", Bewertung.UNGUENSTIG),  # Kosten über Plan = ungünstig
        (Kennzahlart.KOSTEN, "90", "100", Bewertung.GUENSTIG),
        (Kennzahlart.ERGEBNIS, "100", "100", Bewertung.NEUTRAL),
    ],
)
def test_vorzeichenlogik(art, ist, plan, erwartet):
    assert _abw(art=art, ist=D(ist), vergleichswert=D(plan)).bewertung is erwartet


def test_widerspruechliche_bewertung_wird_abgelehnt():
    with pytest.raises(ValidationError, match="Vorzeichenlogik"):
        _abw(art=Kennzahlart.KOSTEN, bewertung=Bewertung.GUENSTIG)


def test_falsches_delta_wird_abgelehnt():
    with pytest.raises(ValidationError, match="delta_abs"):
        _abw(delta_abs=D("11.00"))


def test_falscher_prozentwert_wird_abgelehnt():
    with pytest.raises(ValidationError, match="delta_pct"):
        _abw(delta_pct=D("9.9"))


@pytest.mark.invariante
def test_treiber_muessen_exakt_additiv_sein():
    with pytest.raises(ValidationError, match="nicht additiv"):
        _abw(treiber=[Treiber(effektart=Effektart.MENGE, betrag=D("6.00")),
                      Treiber(effektart=Effektart.PREIS, betrag=D("3.99"))])  # Summe 9,99 ≠ 10,00


@pytest.mark.invariante
def test_summe_aller_effekte_gleich_gesamtabweichung(faktenblatt):
    for a in faktenblatt.abweichungen:
        if a.treiber:
            assert sum(t.betrag for t in a.treiber) == a.delta_abs


def test_treiberanteile_in_python_berechnet(faktenblatt):
    ges = faktenblatt.abweichung("UMS-GES")
    menge = next(t for t in ges.treiber if t.effektart is Effektart.MENGE)
    assert menge.anteil_pct == D("73.7")  # 420.000 / 570.000


def test_mehr_als_zwei_nachkommastellen_bei_eur_unzulaessig():
    with pytest.raises(ValidationError, match="Cent"):
        _abw(ist=D("110.005"))


def test_unbekannte_felder_werden_abgelehnt():
    with pytest.raises(ValidationError):
        _abw(geheim="x")


def test_id_muster():
    with pytest.raises(ValidationError):
        _abw(id="X'; DROP TABLE acdoca;--")


# --- Faktenblatt ------------------------------------------------------------

@pytest.mark.invariante
def test_summe_drilldown_gleich_gesamtebene(faktenblatt):
    kinder = faktenblatt.kinder("UMS-GES")
    assert sum(k.delta_abs for k in kinder) == faktenblatt.abweichung("UMS-GES").delta_abs


def test_unvollstaendiger_drilldown_wird_abgelehnt(faktenblatt):
    daten = faktenblatt.model_dump()
    daten["abweichungen"] = [a for a in daten["abweichungen"] if a["id"] != "UMS-NORD"]
    with pytest.raises(ValidationError, match="Summe Drill-down"):
        Faktenblatt.model_validate(daten)


def test_doppelte_ids_und_fehlende_eltern(faktenblatt):
    daten = faktenblatt.model_dump()
    daten["abweichungen"].append(daten["abweichungen"][-1])
    with pytest.raises(ValidationError, match="Doppelte"):
        Faktenblatt.model_validate(daten)
    daten = faktenblatt.model_dump()
    daten["abweichungen"][1]["eltern_id"] = "GIBT-ES-NICHT"
    with pytest.raises(ValidationError, match="existiert nicht"):
        Faktenblatt.model_validate(daten)


def test_json_roundtrip(faktenblatt):
    kopie = Faktenblatt.model_validate_json(faktenblatt.model_dump_json())
    assert kopie == faktenblatt


def test_periodenbezeichnung(faktenblatt):
    assert faktenblatt.periodenbezeichnung == "März 2026"


# --- Forecast ---------------------------------------------------------------

def test_jahresendprognose_ist_ytd_plus_restmonate(faktenblatt):
    fc = faktenblatt.forecast("umsatz")
    assert fc.jahresendprognose == fc.ist_ytd + fc.prognose_restmonate == D("153500000.00")
    assert fc.abweichung_plan_pct == D("-2.8")


def test_inkonsistente_jahresendprognose_wird_abgelehnt():
    with pytest.raises(ValidationError, match="Jahresendprognose"):
        ForecastInfo(kennzahl="Umsatz", ist_ytd=D("10"), prognose_restmonate=D("20"),
                     jahresendprognose=D("31"), plan_gesamtjahr=D("30"), methode=ForecastMethode.SAISONAL_NAIV)


def test_prognose_ausserhalb_intervall_wird_abgelehnt():
    with pytest.raises(ValidationError, match="Prognoseintervall"):
        ForecastInfo(kennzahl="Umsatz", ist_ytd=D("10"), prognose_restmonate=D("20"), plan_gesamtjahr=D("30"),
                     methode=ForecastMethode.HOLT_WINTERS, intervall_unten=D("31"), intervall_oben=D("40"))


# --- Kommentar & Freigabe (Human-in-the-Loop) --------------------------------

def _kommentar(bestanden: bool = True, text: str = "Umsatz Region Süd: −4,2 % ggü. Plan.") -> Kommentar:
    return Kommentar(objekt_id="UMS-SUED", text=text, quelle=Textquelle.LLM,
                     pruefung=Pruefergebnis(bestanden=bestanden), modell="test")


def test_neuer_kommentar_ist_entwurf():
    k = _kommentar()
    assert k.status is Freigabestatus.ENTWURF
    assert not k.ist_berichtsfaehig
    assert k.freigegeben_von is None


def test_namentliche_freigabe():
    k = _kommentar().freigeben("  Sherwan Shweish ")
    assert k.status is Freigabestatus.FREIGEGEBEN
    assert k.freigegeben_von == "Sherwan Shweish"
    assert k.freigegeben_am is not None
    assert k.ist_berichtsfaehig


@pytest.mark.parametrize("name", ["", "  ", "X"])
def test_freigabe_ohne_namen_unzulaessig(name):
    with pytest.raises(FreigabeFehler):
        _kommentar().freigeben(name)


def test_doppelte_freigabe_unzulaessig():
    with pytest.raises(FreigabeFehler, match="bereits"):
        _kommentar().freigeben("A. Controller").freigeben("B. Controller")


def test_ungepruefter_kommentar_nicht_freigebbar():
    with pytest.raises(FreigabeFehler, match="Faktenprüfung"):
        _kommentar(bestanden=False).freigeben("A. Controller")


def test_manuelle_aenderung_wird_erneut_geprueft(faktenblatt):
    k = _kommentar()
    ok = k.freigeben("A. Controller", "Umsatz Region Süd: −4,2 % (−190,0 T€) ggü. Plan.", faktenblatt)
    assert ok.manuell_bearbeitet and ok.ist_berichtsfaehig
    with pytest.raises(FreigabeFehler, match="Faktenprüfung"):
        k.freigeben("A. Controller", "Umsatz Region Süd: −9,9 % ggü. Plan.", faktenblatt)
    with pytest.raises(FreigabeFehler, match="Faktenblatt"):
        k.freigeben("A. Controller", "Anderer Text ohne Faktenblatt.")


def test_freigabestatus_ohne_namen_ungueltig():
    with pytest.raises(ValidationError):
        Kommentar(objekt_id="X", text="t", quelle=Textquelle.LLM, pruefung=Pruefergebnis(bestanden=True),
                  modell="m", status=Freigabestatus.FREIGEGEBEN)


def test_freigabe_gate_fuer_bericht():
    entwurf, freigegeben = _kommentar(), _kommentar().freigeben("A. Controller")
    assert berichtsfaehige_kommentare([entwurf, freigegeben]) == [freigegeben]
