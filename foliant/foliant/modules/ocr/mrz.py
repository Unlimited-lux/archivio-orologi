"""Zona a lettura ottica (MRZ) di passaporti e carte d'identità (ICAO 9303).

Le righe MRZ hanno un alfabeto ridotto (A-Z, 0-9, «<») e campi a tipo fisso: dove il campo vuole
lettere, 0/1/5/8 riconosciuti sono O/I/S/B, e viceversa nei campi numerici. Le cifre di controllo
che non tornano abbassano l'affidabilità delle parole, così la revisione le segnala."""

from __future__ import annotations

import re

from .tess import LOW_CONFIDENCE, Line

_MRZ = re.compile(r"^[A-Z0-9<«]{28,46}$")
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}
_TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}

# campi per formato: (inizio, fine, tipo) con tipo A lettere, N cifre, X qualsiasi
_TD3 = ([(0, 2, "A"), (2, 5, "A"), (5, 44, "A")],
        [(0, 9, "X"), (9, 10, "N"), (10, 13, "A"), (13, 19, "N"), (19, 20, "N"), (20, 21, "A"),
         (21, 27, "N"), (27, 28, "N"), (28, 42, "X"), (42, 44, "N")])
_TD1 = ([(0, 2, "A"), (2, 5, "A"), (5, 14, "X"), (14, 15, "N"), (15, 30, "X")],
        [(0, 6, "N"), (6, 7, "N"), (7, 8, "A"), (8, 14, "N"), (14, 15, "N"), (15, 18, "A"), (18, 29, "X"),
         (29, 30, "N")],
        [(0, 30, "A")])


def check_digit(field: str) -> int:
    weights = (7, 3, 1)
    total = 0
    for i, ch in enumerate(field):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch) - 55
        else:
            v = 0
        total += v * weights[i % 3]
    return total % 10


def _fix(text: str, fields) -> str:
    chars = list(text)
    for a, b, kind in fields:
        for i in range(a, min(b, len(chars))):
            ch = chars[i]
            if kind == "A" and ch in _TO_LETTER:
                chars[i] = _TO_LETTER[ch]
            elif kind == "N" and ch in _TO_DIGIT:
                chars[i] = _TO_DIGIT[ch]
    return "".join(chars)


def _checks_td3(l2: str) -> bool:
    if len(l2) != 44:
        return False
    pairs = [(l2[0:9], l2[9]), (l2[13:19], l2[19]), (l2[21:27], l2[27])]
    ok = all(d.isdigit() and check_digit(f) == int(d) for f, d in pairs)
    composite = l2[0:10] + l2[13:20] + l2[21:43]
    return ok and l2[43].isdigit() and check_digit(composite) == int(l2[43])


def _checks_td1(l1: str, l2: str) -> bool:
    if len(l1) != 30 or len(l2) != 30:
        return False
    pairs = [(l1[5:14], l1[14]), (l2[0:6], l2[6]), (l2[8:14], l2[14])]
    ok = all(d.isdigit() and check_digit(f) == int(d) for f, d in pairs)
    composite = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    return ok and l2[29].isdigit() and check_digit(composite) == int(l2[29])


def _mrz_word(ln: Line):
    if len(ln.words) != 1:
        return None
    w = ln.words[0]
    t = w.text.replace("«", "<").replace(" ", "")
    if not _MRZ.match(t) or t.count("<") < 2:
        return None
    return w


def fix_mrz(lines: list[Line]) -> int:
    """Corregge sul posto le righe MRZ riconosciute; restituisce quante righe ha trovato."""
    cand = [(k, _mrz_word(ln)) for k, ln in enumerate(lines)]
    cand = [(k, w) for k, w in cand if w is not None]
    found = 0
    i = 0
    while i < len(cand):
        k, w = cand[i]
        nxt = cand[i + 1] if i + 1 < len(cand) and cand[i + 1][0] == k + 1 else None
        t1 = w.text.replace("«", "<")
        if nxt is not None and len(nxt[1].text) == 44 and 40 <= len(t1) <= 48 and t1.endswith("<"):
            # il riempitivo «<<<<» in fondo alla prima riga si conta male: si riporta a 44 caratteri
            t1 = (t1.rstrip("<") + "<" * 44)[:44] if len(t1.rstrip("<")) < 44 else t1
            w.text = _fix(t1, _TD3[0])
            nxt[1].text = _fix(nxt[1].text.replace("«", "<"), _TD3[1])
            if not _checks_td3(nxt[1].text):
                for word in (w, nxt[1]):
                    word.conf = min(word.conf, LOW_CONFIDENCE - 1)
            found += 2
            i += 2
            continue
        third = cand[i + 2] if i + 2 < len(cand) and cand[i + 2][0] == k + 2 else None
        if nxt is not None and third is not None and all(len(x[1].text) == 30 for x in (cand[i], nxt, third)):
            words = [w, nxt[1], third[1]]
            for word, fields in zip(words, _TD1):
                word.text = _fix(word.text.replace("«", "<"), fields)
            if not _checks_td1(words[0].text, words[1].text):
                for word in words:
                    word.conf = min(word.conf, LOW_CONFIDENCE - 1)
            found += 3
            i += 3
            continue
        i += 1
    return found
