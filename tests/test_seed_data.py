"""Synthetische Daten: Reproduzierbarkeit, Struktur und eingebaute Fälle."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import pytest

from src.seed_data import (
    EINGEBAUTE_FAELLE,
    INJEKTION,
    KONTO_ERLOES,
    MATERIALIEN,
    SAISON,
    SONDERABSCHREIBUNG,
    erzeuge_daten,
    lade_datenbank,
    schreibe_datenbank,
)


def _ist(daten, jahr, periode=None):
    a = daten.acdoca
    maske = (a["version"] == "IST") & (a["gjahr"] == jahr)
    return a[maske & (a["poper"] == periode)] if periode else a[maske]


def test_reproduzierbar_bei_festem_seed(rohdaten):
    pd.testing.assert_frame_equal(erzeuge_daten(seed=42).acdoca, rohdaten.acdoca)
    assert not erzeuge_daten(seed=7).acdoca["hsl_cent"].equals(rohdaten.acdoca["hsl_cent"])


def test_36_monate_ist_und_12_monate_plan(rohdaten):
    a = rohdaten.acdoca
    ist_monate = a.loc[a["version"] == "IST", ["gjahr", "poper"]].drop_duplicates()
    assert len(ist_monate) == 36
    assert (ist_monate["gjahr"] * 100 + ist_monate["poper"]).agg(["min", "max"]).tolist() == [202310, 202609]
    plan = a[a["version"] == "PLAN"]
    assert set(plan["gjahr"]) == {2026}
    assert sorted(plan["poper"].unique()) == list(range(1, 13))


def test_saisonalitaet_im_plan(rohdaten):
    plan = rohdaten.acdoca[(rohdaten.acdoca["version"] == "PLAN") & (rohdaten.acdoca["racct"] == KONTO_ERLOES)]
    je_monat = -plan.groupby("poper")["hsl_cent"].sum()
    assert je_monat[10] / je_monat[8] == pytest.approx(SAISON[9] / SAISON[7], rel=0.01)


def test_vorzeichenlogik_soll_haben(rohdaten):
    a, typ = rohdaten.acdoca, rohdaten.acdoca["racct"].map(rohdaten.kostenarttyp())
    assert (a.loc[typ == "ERLOES", "hsl_cent"] < 0).all()
    assert (a.loc[typ.isin(["HK", "KOSTEN"]), "hsl_cent"] > 0).all()


def test_neuprodukt_ohne_plan_ab_april(rohdaten):
    a = rohdaten.acdoca
    x1 = a[a["matnr"] == "SENX1"]
    assert x1[x1["version"] == "PLAN"].empty
    assert x1["poper"].min() == 4 and set(x1["gjahr"]) == {2026}


def test_preiserhoehung_region_nord(rohdaten):
    erl = _ist(rohdaten, 2026, 9)
    erl = erl[(erl["racct"] == KONTO_ERLOES) & (erl["regio"] == "NORD") & (erl["matnr"] == "ANT100")]
    stueckpreis = (-erl["hsl_cent"] / erl["msl"].astype(int)).unique()
    erwartet = int((Decimal(120_000) * Decimal("1.06")).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    assert list(stueckpreis) == [erwartet]


def test_energiepreis_giesserei(rohdaten):
    e = _ist(rohdaten, 2026)
    e = e[(e["rcntr"] == "4100") & (e["racct"] == "410000")]
    assert set(e["hsl_cent"] // e["msl"].astype(int)) == {32}


def test_sonderabschreibung_und_injektion_als_manuelle_buchung(rohdaten):
    manuell = rohdaten.acdoca[rohdaten.acdoca["erfassung"] == "MANUELL"]
    assert set(manuell["sgtxt"]) == {SONDERABSCHREIBUNG[3], INJEKTION[3]}
    sonder = manuell[manuell["racct"] == SONDERABSCHREIBUNG[1]]
    assert int(sonder["hsl_cent"].iloc[0]) == 24_000_000 and int(sonder["poper"].iloc[0]) == 9


def test_datenfehler_sind_eingebaut(rohdaten):
    meta = rohdaten.meta
    a = rohdaten.acdoca
    erloes = a[a["racct"] == KONTO_ERLOES]
    assert (erloes["xblnr"] == meta["dublette"]["xblnr"]).sum() == 2  # Original + Dublette
    fp = meta["fehlende_periode"]
    luecke = a[(a["version"] == "IST") & (a["rcntr"] == fp["rcntr"]) & (a["gjahr"] == fp["gjahr"]) & (a["poper"] == fp["poper"])]
    assert luecke.empty


def test_ohne_datenfehler_stimmt_detail_mit_ergebnisrechnung():
    d = erzeuge_daten(datenfehler_einbauen=False)
    detail = d.acdoca.groupby(["version", "gjahr", "poper", "racct"], as_index=False)["hsl_cent"].sum()
    pd.testing.assert_frame_equal(detail.reset_index(drop=True), d.ergebnisrechnung.reset_index(drop=True), check_dtype=False)


def test_eingebaute_faelle_dokumentiert():
    assert [f.nr for f in EINGEBAUTE_FAELLE] == list(range(1, 12))
    assert {m.matnr for m in MATERIALIEN if m.neu_ab_periode} == {"SENX1"}


def test_sqlite_roundtrip(rohdaten, tmp_path):
    pfad = schreibe_datenbank(rohdaten, tmp_path / "test.db")
    geladen = lade_datenbank(pfad)
    for tabelle in ("acdoca", "kst_leistung", "ergebnisrechnung", "kostenarten"):
        pd.testing.assert_frame_equal(getattr(geladen, tabelle), getattr(rohdaten, tabelle), check_dtype=False)
    assert geladen.acdoca["msl"].dtype == "Int64"
    assert geladen.periode == 9 and geladen.geschaeftsjahr == 2026


@pytest.mark.parametrize("kwargs", [{"periode": 13}, {"monate": 20}])
def test_ungueltige_parameter(kwargs):
    with pytest.raises(ValueError):
        erzeuge_daten(**kwargs)
