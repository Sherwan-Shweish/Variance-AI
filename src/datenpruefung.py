"""Monatsabschluss-Prüfung mit Ampel und Prüfprotokoll.

Vor jeder Analyse wird der Datenbestand geprüft. Jeder Prüfpunkt erhält eine
Ampel; die Gesamtampel ist der schlechteste Einzelwert.

Ampellogik:

* **ROT** – Befund betrifft das Berichtsjahr bis einschließlich Berichtsperiode
  und kann nicht automatisch bereinigt werden. Die Analyse wird gesperrt.
* **GELB** – Befund nur in der Historie bzw. automatisch bereinigt (mit Protokoll)
  oder Hinweis zur Durchsicht.
* **GRÜN** – keine Befunde.

Prüfpunkte: Stammdaten-Referenzen, Vollständigkeit Ist und Plan, Dubletten,
Summenabstimmung Detail vs. Ergebnisrechnung, Vorzeichen, Periodenzuordnung,
Mengen auf mengenbasierten Kostenarten, Buchungstexte (Sicherheitsprüfung).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from src.agent import Ampel, Datenqualitaet, erkenne_injektionsmuster, format_betrag
from src.seed_data import Datenbestand

logger = logging.getLogger("varianceai.datenpruefung")

_RANG = {Ampel.GRUEN: 0, Ampel.GELB: 1, Ampel.ROT: 2}
MAX_BEFUNDE = 25


def _schlechteste(ampeln: list[Ampel]) -> Ampel:
    return max(ampeln, key=_RANG.__getitem__, default=Ampel.GRUEN)


def _eur(cent: int) -> str:
    return format_betrag(Decimal(cent).scaleb(-2), "€", stellen=2)


def _monat(jahr: int, periode: int) -> str:
    return f"{periode:02d}/{jahr}"


class Pruefpunkt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    bezeichnung: str
    ampel: Ampel
    ergebnis: str
    anzahl_befunde: int = 0
    befunde: list[str] = Field(default_factory=list)


class Pruefprotokoll(BaseModel):
    """Prüfprotokoll des Monatsabschlusses (revisionssicher als JSON speicherbar)."""

    model_config = ConfigDict(extra="forbid")

    unternehmen: str
    geschaeftsjahr: int
    periode: int
    erstellt_am: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    punkte: list[Pruefpunkt]
    bereinigungen: list[str] = Field(default_factory=list)

    @property
    def ampel(self) -> Ampel:
        return _schlechteste([p.ampel for p in self.punkte])

    @property
    def analyse_freigegeben(self) -> bool:
        return self.ampel is not Ampel.ROT

    def punkt(self, punkt_id: str) -> Pruefpunkt:
        return next(p for p in self.punkte if p.id == punkt_id)

    def als_datenqualitaet(self) -> Datenqualitaet:
        hinweise = [f"{p.bezeichnung}: {p.ergebnis}" for p in self.punkte if p.ampel is not Ampel.GRUEN]
        return Datenqualitaet(ampel=self.ampel, hinweise=hinweise or ["Alle Abschlussprüfungen bestanden."])

    def als_markdown(self) -> str:
        symbol = {Ampel.GRUEN: "🟢", Ampel.GELB: "🟡", Ampel.ROT: "🔴"}
        zeilen = [
            f"## Prüfprotokoll Monatsabschluss {_monat(self.geschaeftsjahr, self.periode)}",
            "",
            f"**Gesamtampel:** {symbol[self.ampel]} {self.ampel.value}",
            "",
            "| Prüfpunkt | Ampel | Ergebnis |",
            "|---|---|---|",
        ]
        zeilen += [f"| {p.bezeichnung} | {symbol[p.ampel]} | {p.ergebnis} |" for p in self.punkte]
        for p in self.punkte:
            if p.befunde:
                zeilen += ["", f"**{p.bezeichnung}**", *[f"- {b}" for b in p.befunde]]
        if self.bereinigungen:
            zeilen += ["", "**Automatische Bereinigungen**", *[f"- {b}" for b in self.bereinigungen]]
        return "\n".join(zeilen)


@dataclass
class Abschlusspruefung:
    """Ergebnis: Protokoll und (ggf. bereinigter) Datenbestand für die Analyse."""

    protokoll: Pruefprotokoll
    daten: Datenbestand


class _Kontext:
    def __init__(self, daten: Datenbestand, gj: int, periode: int) -> None:
        self.daten = daten
        self.gj = gj
        self.periode = periode
        self.typ = daten.kostenarttyp()

    def im_berichtszeitraum(self, jahr: int, periode: int) -> bool:
        return jahr == self.gj and periode <= self.periode

    def ampel_fuer(self, betroffen: list[tuple[int, int]]) -> Ampel:
        if not betroffen:
            return Ampel.GRUEN
        return Ampel.ROT if any(self.im_berichtszeitraum(j, p) for j, p in betroffen) else Ampel.GELB


# =============================================================================
# Einzelprüfungen
# =============================================================================


def _pruefe_stammdaten(k: _Kontext) -> Pruefpunkt:
    a, d = k.daten.acdoca, k.daten
    befunde: list[str] = []
    for spalte, gueltig, name in (
        ("racct", set(d.kostenarten["racct"]), "Kostenart"),
        ("rcntr", set(d.kostenstellen["rcntr"]), "Kostenstelle"),
        ("matnr", set(d.material["matnr"]), "Material"),
        ("kunnr", set(d.kunden["kunnr"]), "Kunde"),
        ("regio", set(d.regionen["regio"]), "Region"),
    ):
        werte = a[spalte].dropna()
        unbekannt = sorted(set(werte) - gueltig)
        befunde += [f"Unbekannte {name} „{w}“ ({int((werte == w).sum())} Zeilen)" for w in unbekannt]
    ampel = Ampel.ROT if befunde else Ampel.GRUEN
    return Pruefpunkt(id="stammdaten", bezeichnung="Stammdaten-Referenzen", ampel=ampel,
                      ergebnis="Alle Referenzen gültig." if not befunde else f"{len(befunde)} unbekannte Referenzen.",
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _erwartete_ist_monate(k: _Kontext) -> list[tuple[int, int]]:
    ist = k.daten.acdoca[k.daten.acdoca["version"] == "IST"]
    start = int((ist["gjahr"] * 12 + ist["poper"] - 1).min())
    ende = k.gj * 12 + k.periode - 1
    return [(i // 12, i % 12 + 1) for i in range(start, ende + 1)]


def _pruefe_vollstaendigkeit_ist(k: _Kontext) -> Pruefpunkt:
    a, d = k.daten.acdoca, k.daten
    ist = a[a["version"] == "IST"]
    leistung = d.kst_leistung[d.kst_leistung["version"] == "IST"]
    erloese = ist[ist["racct"].map(k.typ) == "ERLOES"]
    vorhanden_region = set(zip(erloese["regio"], erloese["gjahr"], erloese["poper"], strict=True))
    kosten = ist[ist["rcntr"].notna()]
    vorhanden_kst = set(zip(kosten["rcntr"], kosten["gjahr"], kosten["poper"], strict=True))
    vorhanden_leistung = set(zip(leistung["rcntr"], leistung["gjahr"], leistung["poper"], strict=True))
    kst_namen = dict(zip(d.kostenstellen["rcntr"], d.kostenstellen["bezeichnung"], strict=True))

    befunde: list[str] = []
    betroffen: list[tuple[int, int]] = []
    for jahr, periode in _erwartete_ist_monate(k):
        for regio in d.regionen["regio"]:
            if (regio, jahr, periode) not in vorhanden_region:
                befunde.append(f"Region {regio}: keine Umsatzbuchungen {_monat(jahr, periode)}")
                betroffen.append((jahr, periode))
        for rcntr in d.kostenstellen["rcntr"]:
            fehlt = [t for t, menge in (("Kosten", vorhanden_kst), ("Leistungsmengen", vorhanden_leistung))
                     if (rcntr, jahr, periode) not in menge]
            if fehlt:
                befunde.append(f"Kostenstelle {rcntr} ({kst_namen[rcntr]}): keine Ist-{' und '.join(fehlt)} {_monat(jahr, periode)}")
                betroffen.append((jahr, periode))
    ampel = k.ampel_fuer(betroffen)
    if not befunde:
        ergebnis = "Alle Perioden für alle Regionen und Kostenstellen vorhanden."
    elif ampel is Ampel.GELB:
        ergebnis = f"{len(befunde)} Lücke(n) in der Historie – Berichtszeitraum vollständig; Vorjahresvergleiche und Forecast betroffen."
    else:
        ergebnis = f"{len(befunde)} Lücke(n) im Berichtszeitraum – Analyse gesperrt."
    return Pruefpunkt(id="vollstaendigkeit_ist", bezeichnung="Vollständigkeit Ist", ampel=ampel, ergebnis=ergebnis,
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _pruefe_vollstaendigkeit_plan(k: _Kontext) -> Pruefpunkt:
    a, d = k.daten.acdoca, k.daten
    plan = a[(a["version"] == "PLAN") & (a["gjahr"] == k.gj)]
    leistung = d.kst_leistung[(d.kst_leistung["version"] == "PLAN") & (d.kst_leistung["gjahr"] == k.gj)]
    erloese = plan[plan["racct"].map(k.typ) == "ERLOES"]
    vorhanden_region = set(zip(erloese["regio"], erloese["poper"], strict=True))
    vorhanden_kst = set(zip(plan["rcntr"].dropna(), plan.loc[plan["rcntr"].notna(), "poper"], strict=True))
    vorhanden_leistung = set(zip(leistung["rcntr"], leistung["poper"], strict=True))
    befunde: list[str] = []
    betroffen: list[tuple[int, int]] = []
    for periode in range(1, 13):
        for regio in d.regionen["regio"]:
            if (regio, periode) not in vorhanden_region:
                befunde.append(f"Region {regio}: kein Umsatzplan {_monat(k.gj, periode)}")
                betroffen.append((k.gj, periode))
        for rcntr in d.kostenstellen["rcntr"]:
            if (rcntr, periode) not in vorhanden_kst or (rcntr, periode) not in vorhanden_leistung:
                befunde.append(f"Kostenstelle {rcntr}: Kosten- oder Leistungsplan fehlt {_monat(k.gj, periode)}")
                betroffen.append((k.gj, periode))
    ampel = k.ampel_fuer(betroffen)
    ergebnis = "Plan für alle 12 Perioden vollständig." if not befunde else f"{len(befunde)} Planlücke(n)."
    return Pruefpunkt(id="vollstaendigkeit_plan", bezeichnung="Vollständigkeit Plan", ampel=ampel, ergebnis=ergebnis,
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _finde_dubletten(a: pd.DataFrame) -> tuple[pd.Index, list[str], list[tuple[int, int]]]:
    """Technische (gleiche Beleg-/Positionsnummer) und fachliche Dubletten (gleiche Rechnungsreferenz)."""
    befunde: list[str] = []
    betroffen: list[tuple[int, int]] = []
    technisch = a[a["belnr"].notna()].duplicated(subset=["version", "gjahr", "belnr", "buzei"], keep="first")
    technisch_idx = technisch[technisch].index
    for i in technisch_idx:
        z = a.loc[i]
        befunde.append(f"Beleg {z['belnr']}/{z['buzei']} mehrfach vorhanden ({_monat(z['gjahr'], z['poper'])}, {_eur(int(z['hsl_cent']))})")
        betroffen.append((int(z["gjahr"]), int(z["poper"])))

    schluessel = ["version", "racct", "xblnr", "matnr", "kunnr", "hsl_cent"]
    mit_referenz = a[a["xblnr"].notna() & (a["version"] == "IST")].drop(index=technisch_idx, errors="ignore")
    mit_referenz = mit_referenz.sort_values("belnr", kind="stable")
    fachlich = mit_referenz.duplicated(subset=schluessel, keep="first")
    fachlich_idx = fachlich[fachlich].index
    for i in fachlich_idx:
        z = a.loc[i]
        original = mit_referenz[(mit_referenz[schluessel] == z[schluessel]).all(axis=1)].iloc[0]
        befunde.append(
            f"Rechnung {z['xblnr']} doppelt gebucht: Belege {original['belnr']} und {z['belnr']} "
            f"({_monat(z['gjahr'], z['poper'])}, Kunde {z['kunnr']}, {_eur(-int(z['hsl_cent']))})"
        )
        betroffen.append((int(z["gjahr"]), int(z["poper"])))
    return technisch_idx.union(fachlich_idx), befunde, betroffen


def _pruefe_dubletten(k: _Kontext, bereinigen: bool) -> tuple[Pruefpunkt, pd.Index]:
    idx, befunde, betroffen = _finde_dubletten(k.daten.acdoca)
    if not befunde:
        return Pruefpunkt(id="dubletten", bezeichnung="Dubletten", ampel=Ampel.GRUEN,
                          ergebnis="Keine Dubletten gefunden."), idx
    if bereinigen:
        ampel = Ampel.GELB
        ergebnis = f"{len(befunde)} Dublette(n) gefunden und für die Analyse entfernt (jeweils spätere Belegnummer)."
    else:
        ampel = k.ampel_fuer(betroffen)
        ergebnis = f"{len(befunde)} Dublette(n) gefunden – nicht bereinigt."
    return Pruefpunkt(id="dubletten", bezeichnung="Dubletten", ampel=ampel, ergebnis=ergebnis,
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE]), idx


def _abstimmungsdifferenzen(acdoca: pd.DataFrame, er: pd.DataFrame) -> pd.DataFrame:
    detail = acdoca.groupby(["version", "gjahr", "poper", "racct"], as_index=False)["hsl_cent"].sum()
    vergleich = detail.merge(er, on=["version", "gjahr", "poper", "racct"], how="outer", suffixes=("_detail", "_er"))
    vergleich = vergleich.fillna({"hsl_cent_detail": 0, "hsl_cent_er": 0})
    vergleich["differenz"] = (vergleich["hsl_cent_detail"] - vergleich["hsl_cent_er"]).astype("int64")
    return vergleich[vergleich["differenz"] != 0].sort_values(["gjahr", "poper", "racct"])


def _pruefe_summen(k: _Kontext, roh: pd.DataFrame, bereinigt: pd.DataFrame) -> Pruefpunkt:
    namen = dict(zip(k.daten.kostenarten["racct"], k.daten.kostenarten["bezeichnung"], strict=True))
    diff_roh = _abstimmungsdifferenzen(roh, k.daten.ergebnisrechnung)
    diff = _abstimmungsdifferenzen(bereinigt, k.daten.ergebnisrechnung)
    befunde = [
        f"{_monat(int(z.gjahr), int(z.poper))} {z.version} Kostenart {z.racct} ({namen.get(z.racct, '?')}): "
        f"Detail {_eur(int(z.hsl_cent_detail))} vs. Ergebnisrechnung {_eur(int(z.hsl_cent_er))}, "
        f"Differenz {_eur(int(z.differenz))}"
        for z in diff.itertuples()
    ]
    erklaert = len(diff_roh) - len(diff)
    if erklaert > 0:
        befunde.insert(0, f"{erklaert} Abstimmungsdifferenz(en) vor Bereinigung – vollständig durch entfernte Dubletten erklärt.")
    betroffen = [(int(z.gjahr), int(z.poper)) for z in diff.itertuples()]
    ampel = k.ampel_fuer(betroffen)
    if ampel is Ampel.GRUEN and erklaert > 0:
        ampel = Ampel.GELB
    if diff.empty:
        ergebnis = "Detail und Ergebnisrechnung stimmen überein." + (" (nach Bereinigung)" if erklaert else "")
    elif ampel is Ampel.GELB:
        ergebnis = f"{len(diff)} Differenz(en) nur in Vorjahresperioden – Berichtszeitraum abgestimmt."
    else:
        ergebnis = f"{len(diff)} Differenz(en) im Berichtszeitraum – Analyse gesperrt."
    return Pruefpunkt(id="summenabstimmung", bezeichnung="Summenabstimmung Detail vs. Ergebnisrechnung", ampel=ampel,
                      ergebnis=ergebnis, anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _pruefe_vorzeichen(k: _Kontext) -> Pruefpunkt:
    a = k.daten.acdoca
    typ = a["racct"].map(k.typ)
    falsch = a[((typ == "ERLOES") & (a["hsl_cent"] > 0)) | (typ.isin(["HK", "KOSTEN"]) & (a["hsl_cent"] < 0))]
    befunde = [
        f"Beleg {z.belnr} ({z.version} {_monat(z.gjahr, z.poper)}, Kostenart {z.racct}): unerwartetes Vorzeichen {_eur(int(z.hsl_cent))}"
        for z in falsch.itertuples()
    ]
    ampel = k.ampel_fuer([(int(z.gjahr), int(z.poper)) for z in falsch.itertuples()])
    return Pruefpunkt(id="vorzeichen", bezeichnung="Vorzeichen (Soll/Haben-Logik)", ampel=ampel,
                      ergebnis="Alle Vorzeichen plausibel." if not befunde else f"{len(befunde)} Buchung(en) mit unerwartetem Vorzeichen.",
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _pruefe_periodenzuordnung(k: _Kontext) -> Pruefpunkt:
    ist = k.daten.acdoca[k.daten.acdoca["version"] == "IST"]
    datum = pd.to_datetime(ist["budat"], errors="coerce")
    falsch = ist[datum.isna() | (datum.dt.year != ist["gjahr"]) | (datum.dt.month != ist["poper"])]
    befunde = [f"Beleg {z.belnr}: Buchungsdatum {z.budat} passt nicht zu Periode {_monat(z.gjahr, z.poper)}"
               for z in falsch.itertuples()]
    ampel = k.ampel_fuer([(int(z.gjahr), int(z.poper)) for z in falsch.itertuples()])
    return Pruefpunkt(id="periodenzuordnung", bezeichnung="Periodenzuordnung", ampel=ampel,
                      ergebnis="Alle Buchungsdaten passen zur Periode." if not befunde else f"{len(befunde)} Abweichung(en).",
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _pruefe_mengen(k: _Kontext) -> Pruefpunkt:
    a = k.daten.acdoca
    mengenbasiert = dict(zip(k.daten.kostenarten["racct"], k.daten.kostenarten["mengenbasiert"], strict=True))
    ohne = a[(a["version"] == "IST") & (a["gjahr"] == k.gj) & a["racct"].map(mengenbasiert).astype(bool) & a["msl"].isna()]
    befunde = [
        f"Beleg {z.belnr} ({_monat(z.gjahr, z.poper)}, Kostenstelle {z.rcntr}, Kostenart {z.racct}, {_eur(int(z.hsl_cent))}) "
        "ohne Menge – fließt vollständig in die Verbrauchsabweichung."
        for z in ohne.itertuples()
    ]
    return Pruefpunkt(id="mengen", bezeichnung="Mengen auf mengenbasierten Kostenarten",
                      ampel=Ampel.GELB if befunde else Ampel.GRUEN,
                      ergebnis="Alle Buchungen mit Menge." if not befunde else f"{len(befunde)} Buchung(en) ohne Menge (Durchsicht).",
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


def _pruefe_buchungstexte(k: _Kontext) -> Pruefpunkt:
    a = k.daten.acdoca
    texte = a[a["sgtxt"].notna()]
    befunde: list[str] = []
    for z in texte.itertuples():
        treffer = erkenne_injektionsmuster(str(z.sgtxt))
        if treffer:
            befunde.append(f"Beleg {z.belnr} ({_monat(z.gjahr, z.poper)}): auffälliger Buchungstext ({', '.join(treffer)}) – "
                           "wird dem KI-Agenten nur bereinigt übergeben.")
    manuell = a[(a["erfassung"] == "MANUELL") & (a["gjahr"] == k.gj) & (a["poper"] == k.periode)]
    hinweis = f" {len(manuell)} manuelle Buchung(en) in der Berichtsperiode zur Durchsicht."
    return Pruefpunkt(id="buchungstexte", bezeichnung="Buchungstexte (Sicherheitsprüfung)",
                      ampel=Ampel.GELB if befunde else Ampel.GRUEN,
                      ergebnis=("Keine auffälligen Texte." if not befunde else f"{len(befunde)} auffällige(r) Text(e).") + hinweis,
                      anzahl_befunde=len(befunde), befunde=befunde[:MAX_BEFUNDE])


# =============================================================================
# Einstiegspunkt
# =============================================================================


def pruefe_monatsabschluss(
    daten: Datenbestand,
    geschaeftsjahr: int | None = None,
    periode: int | None = None,
    bereinigen: bool = True,
    unternehmen: str = "RheinMain Industrietechnik GmbH",
) -> Abschlusspruefung:
    """Prüft den Datenbestand und liefert Protokoll sowie den Analysedatenbestand.

    Bei ``bereinigen=True`` werden gefundene Dubletten (jeweils die spätere
    Belegnummer) aus einer Kopie entfernt; das Original bleibt unverändert.
    """
    gj = geschaeftsjahr or daten.geschaeftsjahr
    per = periode or daten.periode
    k = _Kontext(daten, gj, per)
    punkte = [_pruefe_stammdaten(k), _pruefe_vollstaendigkeit_ist(k), _pruefe_vollstaendigkeit_plan(k)]
    dubletten_punkt, dubletten_idx = _pruefe_dubletten(k, bereinigen)
    punkte.append(dubletten_punkt)

    analyse = daten.kopie()
    bereinigungen: list[str] = []
    if bereinigen and len(dubletten_idx):
        entfernt = analyse.acdoca.loc[dubletten_idx]
        analyse.acdoca = analyse.acdoca.drop(index=dubletten_idx).reset_index(drop=True)
        bereinigungen = [f"Beleg {z.belnr} entfernt ({_monat(z.gjahr, z.poper)}, {_eur(int(z.hsl_cent))})"
                         for z in entfernt.itertuples()]
        analyse.meta["bereinigt"] = [str(b) for b in entfernt["belnr"]]

    punkte += [
        _pruefe_summen(k, daten.acdoca, analyse.acdoca),
        _pruefe_vorzeichen(k),
        _pruefe_periodenzuordnung(k),
        _pruefe_mengen(k),
        _pruefe_buchungstexte(k),
    ]
    protokoll = Pruefprotokoll(unternehmen=unternehmen, geschaeftsjahr=gj, periode=per, punkte=punkte,
                               bereinigungen=bereinigungen)
    log = logger.warning if protokoll.ampel is not Ampel.GRUEN else logger.info
    log("Abschlussprüfung %s: Ampel %s.", _monat(gj, per), protokoll.ampel.value)
    return Abschlusspruefung(protokoll=protokoll, daten=analyse)
