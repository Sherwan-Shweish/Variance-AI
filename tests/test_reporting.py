"""IBCS-Reporting: Notation der Diagramme und Monatsbericht nur mit freigegebenen Kommentaren."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from matplotlib.colors import to_hex
from pptx import Presentation

from src.agent import Freigabestatus
from src.reporting_ibcs import (
    AC,
    GUENSTIG,
    PY,
    UNGUENSTIG,
    diagramm_abweichungen,
    diagramm_bruecke,
    diagramm_forecast,
    diagramm_kpi,
    erstelle_monatsbericht,
    kernaussagen,
    ohne_titel,
    tabelle_ergebnisrechnung,
)


@pytest.fixture(scope="module")
def kommentare(abschluss):
    agent = abschluss.agent()
    entwuerfe = [agent.kommentiere(o) for o in ("EBIT", "UMS-SUED-ANT", "KST-4300")]
    return [entwuerfe[0].freigeben("Erika Controlling"), entwuerfe[1].freigeben("Erika Controlling"), entwuerfe[2]]


@pytest.fixture(scope="module")
def bericht(abschluss, kommentare, tmp_path_factory):
    return erstelle_monatsbericht(abschluss, kommentare, tmp_path_factory.mktemp("bericht"))


def _balken(fig):
    return [p for ax in fig.axes for p in ax.patches]


def test_ibcs_notation_kpi(abschluss):
    fig = diagramm_kpi(abschluss)
    balken = _balken(fig)
    py, pl, ac = balken[0], balken[1], balken[2]
    assert to_hex(py.get_facecolor()) == PY.lower()                      # PY grau
    assert to_hex(pl.get_facecolor()) == "#ffffff" and to_hex(pl.get_edgecolor()) == AC.lower()  # PL umrandet
    assert to_hex(ac.get_facecolor()) == AC.lower()                      # AC dunkel gefüllt
    plt.close(fig)


def test_forecast_schraffiert(abschluss):
    fig = diagramm_forecast(abschluss)
    schraffiert = [p for p in _balken(fig) if p.get_hatch()]
    assert len(schraffiert) == len(abschluss.forecasts["Umsatz"].monate) == 3
    plt.close(fig)


@pytest.mark.invariante
def test_bruecke_endet_beim_ist(abschluss):
    ebit = abschluss.analyse.abweichung("EBIT")
    fig = diagramm_bruecke(ebit)
    balken = _balken(fig)
    schritte = balken[1:-1]
    assert len(schritte) == len([t for t in ebit.treiber if t.betrag])
    stand = balken[0].get_height()
    for b in schritte:
        stand += b.get_height()
    assert stand == pytest.approx(balken[-1].get_height(), abs=1e-6)
    assert balken[-1].get_height() == pytest.approx(float(ebit.ist) / 1000)
    farben = {to_hex(b.get_facecolor()) for b in schritte}
    assert farben <= {GUENSTIG.lower(), UNGUENSTIG.lower()}
    plt.close(fig)


def test_abweichungsfarben_folgen_bewertung(abschluss):
    kst = [a for a in abschluss.analyse.knoten.values() if a.ebene == "Kostenstelle"]
    fig = diagramm_abweichungen(kst, "Test", "Test")
    balken = sorted(_balken(fig), key=lambda b: b.get_width())
    for b, a in zip(balken, sorted(kst, key=lambda a: a.delta_abs), strict=True):
        erwartet = UNGUENSTIG if a.delta_abs > 0 else GUENSTIG  # Kosten über Plan = ungünstig
        assert to_hex(b.get_facecolor()) == erwartet.lower()
    beschriftungen = [t.get_text() for ax in fig.axes for t in ax.texts]
    assert all(t[0] in "+−" for t in beschriftungen if "(" in t)  # Vorzeichen immer sichtbar
    plt.close(fig)


def test_titel_nur_ausserhalb_der_folien(abschluss):
    fig = diagramm_kpi(abschluss)
    assert any("EBIT" in t.get_text() for t in fig.texts)
    plt.close(fig)
    with ohne_titel():
        fig = diagramm_kpi(abschluss)
    assert fig.texts == []
    plt.close(fig)


def test_kernaussagen_deterministisch(abschluss):
    aussagen = kernaussagen(abschluss)
    assert aussagen == kernaussagen(abschluss)
    assert aussagen[0].startswith("EBIT 569,3 T€: −29,2 % (−235,1 T€) ggü. Plan")
    assert aussagen[1] == "Umsatz Süd – Antriebe: Mengeneffekt −289,4 T€."


def test_tabelle_ergebnisrechnung(abschluss):
    tabelle = tabelle_ergebnisrechnung(abschluss)
    assert tabelle[0][0] == "in T€"
    assert [z[0] for z in tabelle[1:]] == ["Umsatz", "Herstellkosten (Standard)", "Deckungsbeitrag",
                                           "Kosten Kostenstellen", "EBIT"]
    assert tabelle[-1][1:4] == ["569,3", "804,4", "−235,1"]


def _folientexte(pfad: Path) -> list[str]:
    texte = []
    for folie in Presentation(str(pfad)).slides:
        teile = []
        for form in folie.shapes:
            if form.has_text_frame:
                teile.append(form.text_frame.text)
            if form.has_table:
                teile += [z.text for zeile in form.table.rows for z in zeile.cells]
        texte.append("\n".join(teile))
    return texte


def test_powerpoint_struktur(bericht):
    texte = _folientexte(bericht["pptx"])
    assert len(texte) == 8
    assert texte[0].startswith("Monatsbericht September 2026")
    assert "Management Summary" in texte[1]
    assert texte[2].startswith("EBIT −235,1 T€ ggü. Plan")
    assert "Datenprüfung: Gesamtampel gelb" in texte[7]
    bilder = sum(1 for f in Presentation(str(bericht["pptx"])).slides for s in f.shapes if s.shape_type == 13)
    assert bilder == 4


def test_bericht_enthaelt_nur_freigegebene_kommentare(bericht, kommentare):
    texte = "\n".join(_folientexte(bericht["pptx"]))
    for k in kommentare:
        if k.status is Freigabestatus.FREIGEGEBEN:
            assert k.text in texte and "freigegeben von Erika Controlling" in texte
        else:
            assert k.text not in texte


def test_bericht_ohne_freigaben(abschluss, kommentare, tmp_path):
    entwuerfe = [k for k in kommentare if k.status is Freigabestatus.ENTWURF]
    dateien = erstelle_monatsbericht(abschluss, entwuerfe, tmp_path)
    texte = _folientexte(dateien["pptx"])
    assert "noch keine Kommentare freigegeben" in texte[6]
    assert entwuerfe[0].text not in "\n".join(texte)


def test_pdf_erzeugt(bericht):
    inhalt = bericht["pdf"].read_bytes()
    assert inhalt.startswith(b"%PDF") and len(inhalt) > 50_000
    for name in ("kpi", "ebit_bruecke", "umsatz_abweichungen", "kostenstellen", "forecast"):
        assert bericht[f"diagramm_{name}"].exists()
        assert bericht[f"diagramm_{name}_folie"].exists()
