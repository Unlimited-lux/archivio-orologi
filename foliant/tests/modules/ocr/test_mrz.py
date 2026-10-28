"""Righe MRZ di passaporti e carte d'identità: correzione per tipo di campo e cifre di controllo."""

from __future__ import annotations

from foliant.modules.ocr.mrz import check_digit, fix_mrz
from foliant.modules.ocr.tess import LOW_CONFIDENCE, Line, Word


def _lines(*texts):
    return [Line(bbox=(0, i * 30, 400, i * 30 + 20), words=[Word(t, (0, i * 30, 400, i * 30 + 20), 95.0)])
            for i, t in enumerate(texts)]


def test_cifra_di_controllo_icao():
    assert check_digit("L898902C3") == 6          # esempio della norma ICAO 9303
    assert check_digit("740812") == 2
    assert check_digit("120415") == 9


def test_passaporto_lettere_e_cifre_scambiate():
    lines = _lines("Cognome / Surname: ROSSI",
                   "P<ITAROSSI<<MARI0<<<<<<<<<<<<<<<<<<<<<<<<<",          # 0 al posto di O, riempitivo corto
                   "YA12345676ITA75O3120M3103120<<<<<<<<<<<<<<04")         # O al posto di 0 nella data
    assert fix_mrz(lines) == 2
    assert lines[0].words[0].text == "Cognome / Surname: ROSSI"
    assert lines[1].words[0].text == "P<ITAROSSI<<MARIO" + "<" * 27
    assert lines[2].words[0].text == "YA12345676ITA7503120M3103120<<<<<<<<<<<<<<04"
    assert all(ln.words[0].conf == 95.0 for ln in lines[1:])


def test_cifre_di_controllo_sbagliate_segnalano_la_riga():
    lines = _lines("P<ITAROSSI<<MARIO" + "<" * 27, "YA12345679ITA7503120M3103120<<<<<<<<<<<<<<04")
    fix_mrz(lines)
    assert all(ln.words[0].conf < LOW_CONFIDENCE for ln in lines)


def test_carta_identita_td1():
    l1 = "I<ITACA12345XY" + str(check_digit("CA12345XY")) + "<" * 15
    l2 = "750312" + str(check_digit("750312")) + "M" + "310312" + str(check_digit("310312")) + "ITA" + "<" * 11
    composite = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    l2 += str(check_digit(composite))
    l3 = "ROSSI<<MARIO" + "<" * 18
    lines = _lines(l1, l2.replace("0", "O", 1), l3.replace("O", "0"))
    assert fix_mrz(lines) == 3
    assert [ln.words[0].text for ln in lines] == [l1, l2, l3]
    assert all(ln.words[0].conf == 95.0 for ln in lines)


def test_testo_normale_non_toccato():
    lines = _lines("Totale 1.175,02 €", "IBAN IT60X0542811101000000123456")
    assert fix_mrz(lines) == 0
    assert lines[1].words[0].text == "IBAN IT60X0542811101000000123456"
