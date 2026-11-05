"""Abweichungsanalyse: Mathematik, eingebaute Fälle und Invarianten auf den Cent."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import numpy as np
import pytest

from src.agent import Bewertung, Effektart, Faktenblatt
from src.seed_data import EINGEBAUTE_FAELLE, KONTO_ERLOES, KONTO_HK
from src.variance_engine import (
    Abweichungsanalyse,
    Kostenartwerte,
    Position,
    runde_additiv,
    runde_cent,
    zerlege_kosten,
    zerlege_umsatz,
)

from .conftest import cent

# =============================================================================
# Mathematischer Kern
# =============================================================================


def test_lehrbuchbeispiel_preis_menge_mix():
    """Plan: A 100 Stk à 10 €, B 100 Stk à 20 €. Ist: A 150 à 11 €, B 70 à 20 €."""
    z = zerlege_umsatz([Position("A", 150, 1650, 100, 1000), Position("B", 70, 1400, 100, 2000)])
    assert (z.menge, z.mix, z.preis, z.sortiment) == (300, -400, 150, 0)
    assert z.summe == (1650 + 1400) - (1000 + 2000)


def test_sortimentseffekt_neuprodukt_und_wegfall():
    z = zerlege_umsatz([
        Position("A", 100, 1000, 100, 1000),
        Position("NEU", 10, 50, 0, 0),
        Position("ALT", 0, 0, 5, 20),
    ])
    assert z.sortiment == 50 - 20
    assert z.menge == z.mix == z.preis == 0


def test_reine_wertbuchung_ist_sonstiger_effekt():
    assert zerlege_umsatz([Position("X", 0, 300, 0, 0)]).sonstige == 300


@pytest.mark.invariante
def test_zerlegung_exakt_additiv_fuer_zufallsdaten():
    rng = np.random.default_rng(1)
    for _ in range(200):
        positionen = [
            Position(str(i), int(rng.integers(0, 50)), int(rng.integers(0, 10_000)),
                     int(rng.integers(0, 50)), int(rng.integers(0, 10_000)))
            for i in range(int(rng.integers(1, 6)))
        ]
        positionen = [p if p.menge_ist else Position(p.schluessel, 0, 0, p.menge_basis, p.wert_basis) for p in positionen]
        positionen = [p if p.menge_basis else Position(p.schluessel, p.menge_ist, p.wert_ist, 0, 0) for p in positionen]
        z = zerlege_umsatz(positionen)
        assert z.summe == sum(p.wert_ist - p.wert_basis for p in positionen)


@pytest.mark.invariante
def test_runde_additiv_trifft_ziel_exakt():
    rng = np.random.default_rng(2)
    for _ in range(500):
        werte = [Fraction(int(rng.integers(-10**7, 10**7)), int(rng.integers(1, 997))) for _ in range(int(rng.integers(1, 6)))]
        ziel = runde_cent(sum(werte, Fraction(0)))
        gerundet = runde_additiv(werte, ziel)
        assert sum(gerundet) == ziel
        assert all(abs(g - w) < 1 + Fraction(1, 2) for g, w in zip(gerundet, werte, strict=True))


def test_runde_cent_kaufmaennisch():
    assert [runde_cent(Fraction(n, 2)) for n in (1, -1, 3, -3)] == [1, -1, 2, -2]
    assert runde_cent(Fraction(149, 100)) == 1


def test_plankostenrechnung_lehrbuchbeispiel():
    """README-Beispiel: Plan 1.000 T€ (fix 400 T€), BG 90 %, Ist 1.010 T€, Ist zu Planpreisen 985 T€."""
    w = Kostenartwerte(plan_fix=40_000_000, plan_var=60_000_000, plan_menge=600_000,
                       ist=101_000_000, ist_mit_menge=61_000_000, ist_menge=585_000, mengenbasiert=True)
    z = zerlege_kosten(w, Fraction(9, 10))
    assert z.verrechnete_plankosten == 90_000_000 and z.sollkosten == 94_000_000
    assert (z.preis, z.verbrauch, z.beschaeftigung) == (2_500_000, 4_500_000, 4_000_000)
    assert z.preis + z.verbrauch + z.beschaeftigung == z.istkosten - z.verrechnete_plankosten


def test_einmalige_kostenart_vollstaendig_einmaleffekt():
    w = Kostenartwerte(0, 0, 0, ist=24_000_000, ist_mit_menge=0, ist_menge=0, mengenbasiert=False, einmalig=True)
    z = zerlege_kosten(w, Fraction(1))
    assert z.einmal == 24_000_000 and z.preis == z.verbrauch == z.beschaeftigung == 0


def test_buchung_ohne_menge_hat_keine_preisabweichung():
    w = Kostenartwerte(0, 1_000, 10, ist=1_500, ist_mit_menge=1_000, ist_menge=10, mengenbasiert=True)
    z = zerlege_kosten(w, Fraction(1))
    assert z.preis == 0 and z.verbrauch == 500


# =============================================================================
# Eingebaute Fälle – jede Abweichung wird erkannt und richtig zugeordnet
# =============================================================================

FAELLE_MIT_EFFEKT = [(f, o) for f in EINGEBAUTE_FAELLE if f.effekt in {e.value for e in Effektart} for o in f.objekte]


@pytest.mark.parametrize(("fall", "objekt"), FAELLE_MIT_EFFEKT, ids=[f"Fall{f.nr}-{o}" for f, o in FAELLE_MIT_EFFEKT])
def test_jeder_eingebaute_fall_wird_dem_richtigen_effekt_zugeordnet(analyse, fall, objekt):
    abw = analyse.abweichung(objekt)
    assert abw.wesentlich, f"{objekt} nicht wesentlich"
    assert abw.haupttreiber.effektart.value == fall.effekt


def _absatz(daten, version, jahr, periode, **filter_):
    a = daten.acdoca
    z = a[(a["version"] == version) & (a["gjahr"] == jahr) & (a["poper"] == periode)]
    for spalte, wert in filter_.items():
        z = z[z[spalte] == wert]
    return z


def test_fall_preiserhoehung_exakt(analyse, pruefung):
    """Preiseffekt Nord-Antriebe = Σ Ist-Menge × (gerundeter Preis +6 % − Listenpreis), centgenau."""
    erl = _absatz(pruefung.daten, "IST", 2026, 9, regio="NORD", racct=KONTO_ERLOES)
    erwartet = 0
    for m in ("ANT100", "ANT200", "ANT300"):
        liste = int(pruefung.daten.material.set_index("matnr").at[m, "listenpreis_cent"])
        neu = int((Decimal(liste) * Decimal("1.06")).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        erwartet += int(erl.loc[erl["matnr"] == m, "msl"].sum()) * (neu - liste)
    treiber = {t.effektart: cent(t.betrag) for t in analyse.abweichung("UMS-NORD-ANT").treiber}
    assert treiber[Effektart.PREIS] == erwartet


def test_fall_mengeneinbruch(analyse):
    abw = analyse.abweichung("UMS-SUED-ANT")
    menge = next(t.betrag for t in abw.treiber if t.effektart is Effektart.MENGE)
    assert -0.27 < float(menge / abw.vergleichswert) < -0.17
    andere = sum(abs(t.betrag) for t in abw.treiber if t.effektart is not Effektart.MENGE)
    assert abs(menge) > 10 * andere
    assert analyse.top_treiber(1).eintraege[0].objekt_id == "UMS-SUED-ANT"


def test_fall_mix_nur_in_west(analyse):
    west = {t.effektart: t.betrag for t in analyse.abweichung("UMS-WEST-STE").treiber}
    assert west[Effektart.MIX] < 0 and abs(west.get(Effektart.MENGE, 0)) < abs(west[Effektart.MIX]) / 5
    for region in ("NORD", "SUED"):
        mix = sum(t.betrag for t in analyse.abweichung(f"UMS-{region}-STE").treiber if t.effektart is Effektart.MIX)
        assert abs(mix) < 20_000


@pytest.mark.parametrize("region", ["NORD", "SUED", "WEST"])
def test_fall_sortiment_gleich_umsatz_neuprodukt(analyse, pruefung, region):
    erl = _absatz(pruefung.daten, "IST", 2026, 9, regio=region, racct=KONTO_ERLOES, matnr="SENX1")
    sortiment = next(t for t in analyse.abweichung(f"UMS-{region}-SEN").treiber if t.effektart is Effektart.SORTIMENT)
    assert cent(sortiment.betrag) == -int(erl["hsl_cent"].sum())
    produkt = analyse.abweichung(f"UMS-{region}-SEN-SENX1")
    assert produkt.vergleichswert == 0 and [t.effektart for t in produkt.treiber] == [Effektart.SORTIMENT]


def test_fall_energiepreis_exakt(analyse, pruefung):
    e = _absatz(pruefung.daten, "IST", 2026, 9, rcntr="4100", racct="410000")
    preis = next(t for t in analyse.abweichung("KST-4100-410000").treiber if t.effektart is Effektart.KOSTEN_PREIS)
    assert cent(preis.betrag) == int(e["msl"].sum()) * (32 - 24)
    assert analyse.abweichung("KST-4100").haupttreiber.effektart is Effektart.KOSTEN_PREIS


def test_fall_materialmehrverbrauch(analyse):
    abw = analyse.abweichung("KST-4200-400000")
    effekte = {t.effektart: t.betrag for t in abw.treiber}
    assert Effektart.KOSTEN_PREIS not in effekte  # Planpreis unverändert
    z = analyse.kostenstellen["4200"].kostenarten["400000"]
    assert 0.12 < float(effekte[Effektart.VERBRAUCH] * 100 / Decimal(float(z.sollkosten))) < 0.25


def test_fall_unterauslastung_exakt(analyse):
    r = analyse.kostenstellen["4300"]
    assert 0.68 < float(r.beschaeftigungsgrad) < 0.72
    exakt = r.fixe_plankosten * (1 - r.beschaeftigungsgrad)
    assert r.beschaeftigungsabweichung == exakt
    besch = next(t for t in analyse.abweichung("KST-4300").treiber if t.effektart is Effektart.BESCHAEFTIGUNG)
    assert abs(cent(besch.betrag) - exakt) <= len(r.kostenarten)  # max. 1 Cent Rundung je Kostenart


def test_fall_einmaleffekt(analyse):
    sonder = analyse.abweichung("KST-4400-481000")
    assert [(t.effektart, t.betrag) for t in sonder.treiber] == [(Effektart.EINMAL, Decimal("240000.00"))]
    assert sonder.einmaleffekt and analyse.abweichung("KST-4400").einmaleffekt and analyse.abweichung("KST").einmaleffekt
    eintrag = next(e for e in analyse.top_treiber(10).eintraege if e.objekt_id == "KST-4400-481000")
    assert eintrag.einmaleffekt


def test_dublette_ist_aus_der_analyse_entfernt(rohdaten, analyse):
    ungeprueft = Abweichungsanalyse(rohdaten)
    differenz = ungeprueft.abweichung("UMS-SUED").ist - analyse.abweichung("UMS-SUED").ist
    assert cent(differenz) == -rohdaten.meta["dublette"]["hsl_cent"]


def test_kontrollobjekte_ohne_eingebauten_fall_sind_unauffaellig(analyse):
    for objekt in ("UMS-SUED-STE", "UMS-NORD-STE", "KST-4500"):
        assert not analyse.abweichung(objekt).wesentlich, objekt


# =============================================================================
# Invarianten auf den Cent
# =============================================================================


@pytest.mark.invariante
@pytest.mark.parametrize("zeitraum", ["Monat", "YTD"])
def test_vollstaendiges_faktenblatt_besteht_alle_invarianten(pruefung, zeitraum):
    a = Abweichungsanalyse(pruefung.daten, zeitraum=zeitraum)
    fb = a.als_faktenblatt(pruefung.protokoll.als_datenqualitaet(), vollstaendig=True)
    assert isinstance(fb, Faktenblatt) and len(fb.abweichungen) == len(a.knoten) > 600


@pytest.mark.invariante
def test_summe_effekte_gleich_gesamtabweichung_ueberall(analyse):
    for abw in analyse.knoten.values():
        if abw.treiber:
            assert sum(t.betrag for t in abw.treiber) == abw.delta_abs, abw.id


@pytest.mark.invariante
def test_summe_drilldown_gleich_gesamtebene_ueberall(analyse):
    for abw in analyse.knoten.values():
        kinder = analyse.drilldown(abw.id)
        if not kinder:
            continue
        for feld in ("ist", "vergleichswert", "delta_abs"):
            assert sum(getattr(k, feld) for k in kinder) == getattr(abw, feld), (abw.id, feld)


@pytest.mark.invariante
def test_effekte_ab_produktgruppe_additiv_ueber_ebenen(analyse):
    for abw in analyse.knoten.values():
        kinder = analyse.drilldown(abw.id)
        if abw.ebene not in ("Gesamt", "Region", "Kostenstelle") or not kinder:
            continue
        summe: dict[Effektart, Decimal] = {}
        for k in kinder:
            for t in k.treiber:
                summe[t.effektart] = summe.get(t.effektart, Decimal(0)) + t.betrag
        assert {e: b for e, b in summe.items() if b} == {t.effektart: t.betrag for t in abw.treiber}, abw.id


@pytest.mark.invariante
def test_gesamtwerte_stimmen_mit_hauptbuch(analyse, pruefung):
    d = pruefung.daten
    erl_ist = -int(_absatz(d, "IST", 2026, 9, racct=KONTO_ERLOES)["hsl_cent"].sum())
    erl_plan = -int(_absatz(d, "PLAN", 2026, 9, racct=KONTO_ERLOES)["hsl_cent"].sum())
    hk_ist = int(_absatz(d, "IST", 2026, 9, racct=KONTO_HK)["hsl_cent"].sum())
    assert (cent(analyse.abweichung("UMS").ist), cent(analyse.abweichung("UMS").vergleichswert)) == (erl_ist, erl_plan)
    assert cent(analyse.abweichung("DB").ist) == erl_ist - hk_ist
    kosten = _absatz(d, "IST", 2026, 9)
    assert cent(analyse.abweichung("KST").ist) == int(kosten.loc[kosten["rcntr"].notna(), "hsl_cent"].sum())
    vj = -int(_absatz(d, "IST", 2025, 9, racct=KONTO_ERLOES)["hsl_cent"].sum())
    assert cent(analyse.abweichung("VJ-UMS").vergleichswert) == vj


@pytest.mark.invariante
def test_ebit_bruecke_exakt(analyse):
    """ΔEBIT = ΔDB − Kostenstellenabweichungen − Plankostenanpassung, auf den Cent."""
    ebit, db, kst = analyse.abweichung("EBIT"), analyse.abweichung("DB"), analyse.abweichung("KST")
    zeilen = {z.kennzahl: z for z in analyse.ergebnisrechnung}
    assert cent(ebit.ist) == cent(db.ist) - cent(kst.ist) == zeilen["EBIT"].ist
    assert cent(ebit.vergleichswert) == zeilen["Deckungsbeitrag"].plan - zeilen["Kosten Kostenstellen"].plan
    effekte = {t.effektart: t.betrag for t in ebit.treiber}
    assert effekte[Effektart.PLANKOSTENANPASSUNG] == -(kst.vergleichswert - Decimal(zeilen["Kosten Kostenstellen"].plan) / 100)
    for t in kst.treiber:
        assert effekte[t.effektart] == -t.betrag
    assert sum(effekte.values()) == ebit.delta_abs


def test_preiseffekt_im_deckungsbeitrag_identisch(analyse):
    """Herstellkosten zu Standard: Der Preiseffekt wirkt 1:1 im Deckungsbeitrag."""
    for gruppe in ("NORD-ANT", "NORD-STE", "NORD-SEN"):
        ums = {t.effektart: t.betrag for t in analyse.abweichung(f"UMS-{gruppe}").treiber}
        db = {t.effektart: t.betrag for t in analyse.abweichung(f"DB-{gruppe}").treiber}
        assert ums[Effektart.PREIS] == db[Effektart.PREIS]


def test_vorzeichen_und_wesentlichkeit(analyse):
    assert analyse.abweichung("KST-4100").bewertung is Bewertung.UNGUENSTIG
    assert analyse.abweichung("UMS-NORD").bewertung is Bewertung.GUENSTIG
    w = analyse.wesentlichkeit
    for abw in analyse.knoten.values():
        assert abw.wesentlich == w.ist_wesentlich(cent(abw.delta_abs), cent(abw.vergleichswert)), abw.id


def test_ytd_kumuliert_monatswerte(pruefung):
    ytd = Abweichungsanalyse(pruefung.daten, zeitraum="YTD")
    monate = [Abweichungsanalyse(pruefung.daten, periode=p).abweichung("UMS").ist for p in range(1, 10)]
    assert ytd.abweichung("UMS").ist == sum(monate)
    assert ytd.periodenlabel == "01–09/2026 (YTD)"


# =============================================================================
# Faktenblatt für den Agenten
# =============================================================================


def test_kompaktes_faktenblatt(analyse, pruefung):
    fb = analyse.als_faktenblatt(pruefung.protokoll.als_datenqualitaet())
    ids = {a.id for a in fb.abweichungen}
    for fall in EINGEBAUTE_FAELLE:
        assert set(fall.objekte) <= ids, fall.bezeichnung
    assert len(fb.abweichungen) < 120
    assert not any(a.ebene == "Kunde" for a in fb.abweichungen)
    assert Faktenblatt.model_validate_json(fb.model_dump_json()) == fb


def test_kennzahlen_im_faktenblatt(analyse):
    k = {x.id: x.wert for x in analyse.kennzahlen()}
    assert k["DBQ-ABW"] == pytest.approx(k["DBQ-IST"] - k["DBQ-PLAN"], abs=Decimal("0.1"))
    assert Decimal("68") < k["BG-4300"] < Decimal("72")


def test_datentexte_nur_aus_manuellen_buchungen(analyse):
    assert analyse.abweichung("KST-4200-400000").datentexte[0].startswith("Umbuchung Kleinmaterial")
    assert analyse.abweichung("KST-4400-481000").datentexte[0].startswith("Sonderabschreibung")
    assert not analyse.abweichung("UMS-SUED-ANT").datentexte
