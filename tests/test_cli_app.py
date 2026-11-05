"""Kommandozeile, Demo-Durchlauf und Streamlit-Cockpit (Ende-zu-Ende, offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import demo
import run_agent
from src.pipeline import fuehre_monatsabschluss_durch
from src.seed_data import erzeuge_daten

APP = str(Path(__file__).resolve().parent.parent / "app.py")


# --- CLI ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    ordner = tmp_path_factory.mktemp("cli")
    basis = ["--db", str(ordner / "v.db"), "--ausgabe", str(ordner)]
    assert run_agent.main([*basis, "daten"]) == 0
    return ordner, basis


def test_cli_pruefen_und_analyse(cli, capsys):
    _, basis = cli
    assert run_agent.main([*basis, "pruefen"]) == 0
    assert "Gesamtampel" in capsys.readouterr().out
    assert run_agent.main([*basis, "analyse", "--top", "3"]) == 0
    ausgabe = capsys.readouterr().out
    assert "Umsatz Süd – Antriebe" in ausgabe and "Mengeneffekt" in ausgabe
    assert run_agent.main([*basis, "analyse", "--objekt", "KST-4300"]) == 0
    assert "Beschäftigungsabweichung" in capsys.readouterr().out


def test_cli_kommentare_freigabe_und_bericht(cli, capsys):
    ordner, basis = cli
    assert run_agent.main([*basis, "kommentare", "--objekte", "EBIT", "KST-4100"]) == 0
    assert run_agent.main([*basis, "kommentare", "--objekte", "EBIT", "--freigeben", "Max Mustermann"]) == 0
    gespeichert = json.loads((ordner / "kommentare.json").read_text(encoding="utf-8"))
    status = {k["objekt_id"]: k["status"] for k in gespeichert}
    assert status == {"EBIT": "Freigegeben", "KST-4100": "Entwurf"}
    assert run_agent.main([*basis, "bericht"]) == 0
    assert "1 freigegebene von 2 Kommentaren" in capsys.readouterr().out
    assert (ordner / "bericht" / "Monatsbericht_2026-09.pptx").exists()


def test_cli_frage_zeigt_tools_und_quellzahlen(cli, capsys):
    _, basis = cli
    assert run_agent.main([*basis, "frage", "Warum liegt die Kostenstelle 4200 über Plan?"]) == 0
    ausgabe = capsys.readouterr().out
    assert "Tools: abweichungen_auflisten" in ausgabe and "drilldown(objekt_id=KST-4200)" in ausgabe
    assert "+9,0 % ← tool[" in ausgabe  # Quellzahl aus einem Tool-Ergebnis belegt


def test_cli_export(cli, capsys):
    ordner, basis = cli
    assert run_agent.main([*basis, "export"]) == 0
    assert (ordner / "export" / "VarianceAI_2026-09.xlsx").exists()
    assert (ordner / "export" / "powerbi.zip").exists()


def test_cli_rote_ampel_liefert_exitcode(tmp_path, capsys):
    from src.seed_data import schreibe_datenbank

    daten = erzeuge_daten()
    a = daten.acdoca
    daten.acdoca = a[~((a["version"] == "IST") & (a["gjahr"] == 2026) & (a["poper"] == 9) & (a["regio"] == "NORD"))]
    schreibe_datenbank(daten, tmp_path / "rot.db")
    basis = ["--db", str(tmp_path / "rot.db"), "--ausgabe", str(tmp_path)]
    assert run_agent.main([*basis, "pruefen"]) == 2
    assert run_agent.main([*basis, "analyse"]) == 2
    assert "Analyse gesperrt" in capsys.readouterr().err


def test_demo_durchlauf(tmp_path, capsys):
    assert demo.main(["--ausgabe", str(tmp_path)]) == 0
    ausgabe = capsys.readouterr().out
    assert "Konsistenzprüfung: verworfen" in ausgabe
    for datei in ("varianceai.db", "kommentare.json", "bericht/Monatsbericht_2026-09.pptx",
                  "bericht/Monatsbericht_2026-09.pdf", "export/VarianceAI_2026-09.xlsx", "export/powerbi.zip"):
        assert (tmp_path / datei).exists(), datei


@pytest.mark.parametrize("periode", [1, 3, 12])
def test_pipeline_fuer_andere_berichtsperioden(periode):
    """Das Cockpit erlaubt jede Periode – auch ohne Forecast-Version (Q1) oder ohne Restmonate (Dezember)."""
    from dataclasses import replace

    from src.config import Einstellungen

    e = replace(Einstellungen(), periode=periode)
    abschluss = fuehre_monatsabschluss_durch(erzeuge_daten(periode=periode), e)
    assert abschluss.faktenblatt.periode == periode
    assert ("FC-UMS" in abschluss.analyse.knoten) == (periode > 3)
    assert abschluss.forecasts["Umsatz"].info.jahresendprognose >= abschluss.forecasts["Umsatz"].info.ist_ytd


# --- Streamlit-Cockpit -------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _html(app) -> str:
    return "\n".join(h.proto.body for h in app.get("html"))


def test_app_tabs_und_kpis(app):
    assert [t.label for t in app.tabs] == ["Monatsübersicht", "Abweichungsanalyse", "Forecast", "KI-Kommentare",
                                           "Frag den Controller", "Agent & Audit"]
    html = _html(app)
    assert '<div class="va-kpi-label">EBIT</div>' in html and "569,3 T€" in html
    assert 'va-pill--schlecht">−29,2 % ggü. PL' in html      # ungünstig: rot und mit Vorzeichen
    assert "Datenprüfung gelb" in html and "Controller-Copilot" in html
    assert "<svg" not in html                                  # Grafiken nur als bereinigte Data-URI-Bilder


def test_app_drilldown(app):
    app.selectbox(key="abw_kennzahl").set_value("Kosten Kostenstellen").run()
    app.selectbox(key="drill_KST_0").set_value("KST-4300").run()
    assert not app.exception
    html = _html(app)
    assert "+60,2 T€" in html and "ungünstig" in html
    assert "Vergleichswert (PL verr.)" in html                  # Basis der flexiblen Plankostenrechnung


def test_app_frage_mit_tools_und_quellzahlen(app):
    app.button(key="bsp_Warum liegt die Kostenstelle 4200 über Plan?").click().run()
    assert not app.exception
    beschriftungen = [e.label for e in app.expander]
    assert any(b.startswith("Verwendete Tools (3)") for b in beschriftungen)
    assert any(b.startswith("Quellzahlen (") for b in beschriftungen)
    assert "drilldown(objekt_id=KST-4200)" in _html(app)       # Tool-Kette sichtbar


def test_app_kommentar_freigabe(app):
    next(b for b in app.button if b.label == "Entwürfe erzeugen").click().run()
    assert len(app.text_area) >= 1
    next(b for b in app.button if b.label == "Freigeben").click().run()
    assert any("Freigabe nur mit Namen" in e.value for e in app.error)  # ohne Namen keine Freigabe
    app.text_input[0].set_value("Erika Controlling").run()
    next(b for b in app.button if b.label == "Freigeben").click().run()
    assert not app.exception
    assert any("Freigegeben von Erika Controlling" in c.value for c in app.caption)


def test_app_injektionstest_und_audit(app):
    app.button(key="injektionstest").click().run()
    assert not app.exception
    html = _html(app)
    assert "verworfen – nicht im Bericht" in html and "Entwarnung widerspricht" in html
    assert "BLOCKIERT" in html and "FREIGABE" in html            # Audit-Protokoll ist append-only fortgeschrieben


def test_cli_diagnose(capsys):
    assert run_agent.main(["diagnose"]) == 0
    ausgabe = capsys.readouterr().out
    assert "OK streamlit" in ausgabe and "OK pyarrow" in ausgabe


def test_fehler_in_einem_tab_legt_cockpit_nicht_lahm(monkeypatch):
    """Ein defektes Paket (hier simuliert) darf nur den betroffenen Tab treffen."""
    from streamlit.testing.v1 import AppTest

    import src.ui_diagramme as dia

    def kaputt(*_a, **_k):
        raise ImportError("simulierter Importfehler")

    monkeypatch.setattr(dia, "abweichungsbalken", kaputt)
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert [e.message for e in at.exception] == ["simulierter Importfehler"]  # nur unter „Technische Details“
    assert any("Abweichungsanalyse" in e.value and "simulierter Importfehler" in e.value for e in at.error)
    assert [t.label for t in at.tabs][-1] == "Agent & Audit" and "Audit-Protokoll" in _html(at)  # spätere Tabs laufen
