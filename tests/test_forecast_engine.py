"""Forecast-Engine: Methoden, Backtesting, Intervalle, Szenarien, Güte, Reproduzierbarkeit."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.agent import ForecastMethode
from src.config import Einstellungen, ForecastEinstellungen
from src.forecast_engine import (
    backtest,
    forecast_basis,
    holt_winters,
    monatsreihe,
    prognostiziere,
    saisonal_naiv,
    waehle_methode,
    wape,
)
from src.seed_data import erzeuge_daten

from .conftest import cent

SAISON = np.array([0.92, 0.96, 1.08, 1.02, 1.03, 1.00, 0.95, 0.82, 1.04, 1.10, 1.08, 1.00])


@pytest.fixture(scope="module")
def umsatz(pruefung):
    return prognostiziere(pruefung.daten, "Umsatz")


@pytest.fixture(scope="module")
def kosten(pruefung):
    return prognostiziere(pruefung.daten, "Kosten")


# --- Methoden ---------------------------------------------------------------

def test_saisonal_naiv():
    y = np.arange(1, 25, dtype=float)
    assert saisonal_naiv(y, 3).tolist() == [13, 14, 15]
    assert saisonal_naiv(y, 14)[12:].tolist() == [13, 14]


def test_wape():
    assert wape(np.array([100, 200]), np.array([110, 170])) == pytest.approx(40 / 300 * 100)
    with pytest.raises(ValueError):
        wape(np.array([0, 0]), np.array([1, 1]))


def test_holt_winters_trifft_saisonales_muster():
    t = np.arange(48)
    y = 1000 * (1 + 0.002 * t) * SAISON[t % 12]
    prognose = holt_winters(y[:36], 12)
    assert wape(y[36:], prognose) < 2


def test_backtest_rolling_origin_zaehlt_korrekt():
    y = 100 * SAISON[np.arange(36) % 12]
    b = backtest(y, ForecastMethode.SAISONAL_NAIV, min_train=24, horizont=3)
    assert b.ursprungspunkte == 12 and b.anzahl_prognosen == 3 * 10 + 2 + 1
    assert b.wape_pct == pytest.approx(0)


def test_methodenwahl_bei_gleichstand_einfachere_methode():
    y = 100 * SAISON[np.arange(36) % 12]  # rein saisonal, ohne Trend und Rauschen
    methode, ergebnisse = waehle_methode(y, ForecastEinstellungen())
    assert methode is ForecastMethode.SAISONAL_NAIV
    assert set(ergebnisse) == {ForecastMethode.SAISONAL_NAIV, ForecastMethode.HOLT_WINTERS}


def test_methodenwahl_nach_wape(umsatz):
    assert umsatz.methode is min(umsatz.backtests.values(), key=lambda b: b.wape_pct).methode
    assert float(umsatz.info.wape_pct) == pytest.approx(umsatz.backtests[umsatz.methode].wape_pct, abs=0.05)


# --- Jahresendprognose ------------------------------------------------------

@pytest.mark.invariante
def test_jahresendprognose_gleich_ist_ytd_plus_restmonate(umsatz, pruefung):
    reihe = monatsreihe(pruefung.daten, "Umsatz")
    ytd = int(reihe[reihe.index.year == 2026].sum())
    assert cent(umsatz.info.ist_ytd) == ytd
    assert umsatz.info.jahresendprognose == umsatz.info.ist_ytd + umsatz.info.prognose_restmonate
    assert cent(umsatz.info.prognose_restmonate) == sum(m.wert_cent for m in umsatz.monate)
    assert [m.periode for m in umsatz.monate] == [10, 11, 12]
    plan = monatsreihe(pruefung.daten, "Umsatz", "PLAN")
    assert cent(umsatz.info.plan_gesamtjahr) == int(plan.sum())


def test_prognoseintervalle(umsatz):
    i = umsatz.info
    assert i.intervall_unten <= i.jahresendprognose <= i.intervall_oben
    assert i.intervall_unten < i.intervall_oben
    for m in umsatz.monate:
        assert m.unten_cent <= m.wert_cent <= m.oben_cent


def test_szenarien_richtung(umsatz, kosten):
    u = {s.name: s.wert for s in umsatz.info.szenarien}
    k = {s.name: s.wert for s in kosten.info.szenarien}
    assert u["Best"] > u["Base"] > u["Worst"]
    assert k["Best"] < k["Base"] < k["Worst"]  # bei Kosten ist weniger besser
    assert u["Base"] == umsatz.info.jahresendprognose


@pytest.mark.invariante
def test_forecast_reproduzierbar_bei_festem_seed(pruefung, umsatz):
    erneut = prognostiziere(pruefung.daten, "Umsatz")
    assert erneut.info == umsatz.info and erneut.monate == umsatz.monate
    anderer = prognostiziere(pruefung.daten, "Umsatz", replace(Einstellungen(), seed=7))
    assert anderer.info.jahresendprognose == umsatz.info.jahresendprognose  # Punktprognose seedunabhängig
    assert anderer.info.intervall_unten != umsatz.info.intervall_unten     # Simulation seedabhängig


# --- Forecast-Güte und Versionen --------------------------------------------

def test_forecast_versionen_und_guete(umsatz):
    assert set(umsatz.versionen) == {"FC03", "FC06"}  # FC09 hat noch kein eingetretenes Ist
    assert set(umsatz.versionen["FC06"]) == set(range(7, 13))
    g = {x.version: x for x in umsatz.guete}
    assert g["FC03"].perioden == (4, 5, 6, 7, 8, 9) and g["FC06"].perioden == (7, 8, 9)
    assert all(x.wape_pct >= 0 for x in umsatz.guete)
    # FC03 kannte das Neuprodukt (ab April) noch nicht → Umsatz unterschätzt
    assert g["FC03"].bias_pct < 0


def test_forecast_basis_monat_und_ytd(umsatz, pruefung):
    reihe = monatsreihe(pruefung.daten, "Umsatz")
    assert forecast_basis(umsatz, (9,), reihe, 2026, 9) == ("FC06", umsatz.versionen["FC06"][9])
    version, basis = forecast_basis(umsatz, tuple(range(1, 10)), reihe, 2026, 9)
    ist_bis_juni = int(reihe[(reihe.index.year == 2026) & (reihe.index.month <= 6)].sum())
    assert basis == ist_bis_juni + sum(umsatz.versionen["FC06"][p] for p in (7, 8, 9))


def test_jahresende_ohne_restmonate():
    daten = erzeuge_daten(periode=12)
    f = prognostiziere(daten, "Umsatz", replace(Einstellungen(), periode=12))
    assert f.info.prognose_restmonate == 0 and f.monate == []
    assert f.info.jahresendprognose == f.info.ist_ytd == f.info.intervall_unten == f.info.intervall_oben


def test_lueckenhafte_reihe_wird_abgelehnt(pruefung):
    reihe = monatsreihe(pruefung.daten, "Umsatz")
    with pytest.raises(ValueError, match="Lücken"):
        prognostiziere(pruefung.daten, "Umsatz", reihe=reihe.drop(pd.Period("2025-07", freq="M")))


def test_zu_kurze_reihe():
    with pytest.raises(ValueError):
        backtest(np.ones(20), ForecastMethode.SAISONAL_NAIV)


def test_ets_simulation_versionsunabhaengig():
    """Regression: statsmodels 0.14 kennt kein ``rng``, 0.15 kein ``random_state`` – nur ``random_errors`` nutzen."""
    import numpy as np

    from src.forecast_engine import _ets_simulieren

    class AlteApi:  # Signatur wie statsmodels 0.14 (ohne rng)
        scale = 4.0

        def simulate(self, nsimulations, anchor=None, repetitions=1, random_errors=None, random_state=None):
            assert random_state is None and random_errors.shape == (nsimulations, repetitions)
            return random_errors

    a = _ets_simulieren(AlteApi(), 3, 5, np.random.default_rng(7))
    b = _ets_simulieren(AlteApi(), 3, 5, np.random.default_rng(7))
    assert np.array_equal(a, b) and np.allclose(a, np.random.default_rng(7).standard_normal((3, 5)) * 2.0)
