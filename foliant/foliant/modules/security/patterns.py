"""Ricerca di testo e di schemi (email, telefono, IBAN, …) con le loro aree sulla pagina.

Logica pura, senza interfaccia. Il testo della pagina viene ricostruito
carattere per carattere con il riquadro di ciascun carattere, così una
corrispondenza di un'espressione regolare si traduce in uno o più rettangoli
(uno per riga) da oscurare.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable

import pymupdf

from foliant.api import _

# --------------------------------------------------------------- testo pagina


@dataclass
class PageText:
    """Testo di una pagina con il riquadro (o None) di ogni carattere."""

    text: str
    boxes: list[pymupdf.Rect | None]
    lines: list[int]          # indice di riga di ogni carattere (-1 per i separatori)

    def rects(self, start: int, end: int) -> list[pymupdf.Rect]:
        """Rettangoli (uno per riga) che coprono i caratteri [start, end)."""
        out: dict[int, pymupdf.Rect] = {}
        for i in range(start, end):
            box = self.boxes[i]
            if box is None:
                continue
            line = self.lines[i]
            if line in out:
                out[line] |= box
            else:
                out[line] = pymupdf.Rect(box)
        return [out[k] for k in sorted(out)]


def _tight(box: pymupdf.Rect) -> pymupdf.Rect:
    # Il riquadro del motore copre l'intera altezza del carattere (ascendenti e
    # discendenti) e spesso sconfina nella riga successiva: lo stringiamo così
    # l'oscuramento non porta via caratteri delle righe vicine.
    h = box.height
    return pymupdf.Rect(box.x0, box.y0 + h * 0.2, box.x1, box.y1 - h * 0.2)


def page_text(page: pymupdf.Page) -> PageText:
    """Ricostruisce il testo della pagina (righe separate da «\\n»)."""
    flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP
    raw = page.get_text("rawdict", flags=flags)
    chars: list[str] = []
    boxes: list[pymupdf.Rect | None] = []
    lines: list[int] = []
    line_no = 0
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            prev: pymupdf.Rect | None = None
            prev_size = 0.0
            for span in line.get("spans", []):
                size = float(span.get("size", 10) or 10)
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c:
                        continue
                    box = pymupdf.Rect(ch["bbox"])
                    # spazi «impliciti»: caratteri distanziati senza spazio nel file
                    if (prev is not None and not c.isspace() and chars and not chars[-1].isspace()
                            and box.x0 - prev.x1 > 0.25 * max(size, prev_size)):
                        chars.append(" ")
                        boxes.append(None)
                        lines.append(-1)
                    chars.append(c)
                    boxes.append(_tight(box) if not box.is_empty else None)
                    lines.append(line_no)
                    prev, prev_size = box, size
            chars.append("\n")
            boxes.append(None)
            lines.append(-1)
            line_no += 1
    return PageText("".join(chars), boxes, lines)


# ----------------------------------------------------------------- controlli


def _iban_ok(s: str) -> bool:
    s = re.sub(r"\s", "", s).upper()
    if not (15 <= len(s) <= 34) or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    moved = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in moved)
    return int(num) % 97 == 1


_CF_ODD = {
    **dict(zip("0123456789", (1, 0, 5, 7, 9, 13, 15, 17, 19, 21))),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23))),
}


def _cf_ok(s: str) -> bool:
    s = s.upper()
    if len(s) != 16:
        return False
    total = 0
    for i, c in enumerate(s[:15]):
        if i % 2 == 0:
            total += _CF_ODD[c]
        else:
            total += int(c) if c.isdigit() else ord(c) - 65
    return chr(65 + total % 26) == s[15]


def _piva_ok(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if len(digits) != 11 or digits == "0" * 11:
        return False
    total = 0
    for i, c in enumerate(digits[:10]):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(digits[10])


def _luhn_ok(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_DATE_LIKE = re.compile(r"^\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}$")


def _phone_ok(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if not 6 <= len(digits) <= 15:
        return False
    if _DATE_LIKE.match(s.strip()):
        return False
    # numeri «semplici» come importi con separatori di migliaia (1.234.567)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s.strip()):
        return False
    return True


def _date_ok(s: str) -> bool:
    import datetime
    m = re.fullmatch(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2}|\d{4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
    else:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        if not m:
            return True   # date in lettere
        y, mo, d = (int(x) for x in m.groups())
    try:
        datetime.date(y, mo, d)
    except ValueError:
        return False
    return True


def _ip_ok(s: str) -> bool:
    return all(0 <= int(p) <= 255 for p in s.split("."))


# ------------------------------------------------------------------- schemi

_MONTHS = ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|"
           "gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic|"
           "january|february|march|april|may|june|july|august|september|october|november|december|"
           "jan|jun|jul|aug|sep|sept|oct|dec")


@dataclass(frozen=True)
class Pattern:
    id: str
    label: str
    regex: str
    flags: int = 0
    check: Callable[[str], bool] | None = None
    example: str = ""
    fold: bool = False         # confronto senza accenti

    def compiled(self) -> re.Pattern:
        return re.compile(self.regex, self.flags)


def builtin_patterns() -> list[Pattern]:
    """Schemi predefiniti (etichette tradotte nella lingua attiva)."""
    return [
        Pattern("email", _("Indirizzi email"),
                r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}",
                example="mario.rossi@esempio.it"),
        Pattern("phone", _("Numeri di telefono"),
                r"(?<![\w+])(?:(?:\+|00)\d{1,3}[ .\-]?)?(?:\(\d{1,4}\)[ .\-]?)?\d[\d .\-/]{4,16}\d(?![\w])",
                check=_phone_ok, example="+39 347 123 4567"),
        Pattern("iban", _("IBAN"),
                r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", check=_iban_ok,
                example="IT60 X054 2811 1010 0000 0123 456"),
        Pattern("cf", _("Codici fiscali"),
                r"\b[A-Z]{6}[0-9LMNP-V]{2}[A-EHLMPR-T][0-9LMNP-V]{2}[A-Z][0-9LMNP-V]{3}[A-Z]\b",
                re.IGNORECASE, check=_cf_ok, example="RSSMRA80A01H501U"),
        Pattern("vat", _("Partite IVA"), r"\b(?:IT[ ]?)?\d{11}\b", check=_piva_ok, example="IT01234567897"),
        Pattern("card", _("Carte di pagamento"), r"\b\d(?:[ \-]?\d){12,18}\b", check=_luhn_ok,
                example="4111 1111 1111 1111"),
        Pattern("date", _("Date"),
                r"\b(?:\d{1,2}[./\-]\d{1,2}[./\-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}"
                r"|\d{1,2}(?:°|º)?[ ]+(?:" + _MONTHS + r")\.?[ ]+\d{4}"
                r"|(?:" + _MONTHS + r")\.?[ ]+\d{1,2},?[ ]+\d{4})\b",
                re.IGNORECASE, check=_date_ok, example="12/03/2024"),
        Pattern("amount", _("Importi"),
                r"(?:€|EUR|CHF|USD|\$|£)[ ]?\d[\d.,']*\d|\b\d[\d.,']*[ ]?(?:€|EUR|CHF|USD|euro)\b",
                re.IGNORECASE, example="€ 12.500,00"),
        Pattern("url", _("Indirizzi web"), r"\b(?:https?://|www\.)[^\s<>\"']+[^\s<>\"'.,;:)]",
                re.IGNORECASE, example="https://esempio.it"),
        Pattern("ip", _("Indirizzi IP"), r"\b(?:\d{1,3}\.){3}\d{1,3}\b", check=_ip_ok, example="192.168.1.10"),
    ]


def pattern_by_id(pattern_id: str) -> Pattern | None:
    return next((p for p in builtin_patterns() if p.id == pattern_id), None)


def custom_pattern(regex: str, case_sensitive: bool = False) -> Pattern:
    """Schema personalizzato. Solleva ValueError con un messaggio chiaro se non è valido."""
    try:
        re.compile(regex)
    except re.error as e:
        raise ValueError(_("Espressione non valida: {e}").format(e=e)) from e
    if re.fullmatch(regex, ""):
        raise ValueError(_("L'espressione trova anche il testo vuoto: rendila più specifica."))
    return Pattern("custom", _("Schema personalizzato"), regex, 0 if case_sensitive else re.IGNORECASE)


def fold_accents(text: str) -> str:
    """Toglie gli accenti mantenendo la lunghezza (un carattere per carattere)."""
    out = []
    for c in text:
        base = unicodedata.normalize("NFD", c)[0] if ord(c) > 127 else c
        out.append(base if len(base) == 1 else c)
    return "".join(out)


def term_pattern(term: str, *, case_sensitive: bool = False, whole_word: bool = False,
                 ignore_accents: bool = False) -> Pattern:
    """Schema per una parola o frase esatta. Spazi multipli e a capo valgono come
    uno spazio; una parola sillabata a fine riga («Ros-» a capo «si») è trovata."""
    term = term.strip()
    if not term:
        raise ValueError(_("Scrivi la parola o la frase da cercare."))
    if ignore_accents:
        term = fold_accents(term)
    hyphen = r"(?:-\n)?"
    parts = [hyphen.join(re.escape(c) for c in word) for word in term.split()]
    regex = r"\s+".join(parts)
    if whole_word:
        regex = r"(?<!\w)" + regex + r"(?!\w)"
    return Pattern("term", term, regex, 0 if case_sensitive else re.IGNORECASE, fold=ignore_accents)


# -------------------------------------------------------------------- ricerca


@dataclass
class Match:
    page: int
    text: str
    rects: list[pymupdf.Rect] = field(default_factory=list)
    pattern: str = ""

    @property
    def bounds(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.rects[0])
        for x in self.rects[1:]:
            r |= x
        return r


def _iban_spans(pattern: Pattern, text: str) -> Iterable[tuple[int, int]]:
    # Un IBAN raggruppato a blocchi può essere seguito da altre parole: si
    # accorcia la corrispondenza finché il codice di controllo è valido.
    rx = pattern.compiled()
    pos = 0
    while True:
        m = rx.search(text, pos)
        if m is None:
            return
        start, end = m.span()
        found = None
        while end - start >= 15:
            if _iban_ok(text[start:end]):
                found = end
                break
            end -= 1
            while end > start and text[end - 1] == " ":
                end -= 1
        if found:
            yield start, found
            pos = found
        else:
            pos = m.start() + 1


_GENERIC = {"phone", "amount"}
_SPECIFIC = ("iban", "card", "vat", "cf", "date", "ip", "email", "url")


def _specific_spans(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in builtin_patterns():
        if p.id not in _SPECIFIC:
            continue
        if p.id == "iban":
            out.extend(_iban_spans(p, text))
            continue
        for m in p.compiled().finditer(text):
            if m.end() > m.start() and (p.check is None or _safe_check(p, m.group(0))):
                out.append(m.span())
    return out


def _safe_check(p: Pattern, s: str) -> bool:
    try:
        return bool(p.check(s)) if p.check else True
    except (ValueError, KeyError):
        return False


def find_in_page(page: pymupdf.Page, pattern: Pattern, page_index: int = 0,
                 text: PageText | None = None) -> list[Match]:
    pt = text or page_text(page)
    haystack = fold_accents(pt.text) if pattern.fold else pt.text
    if pattern.id == "iban":
        spans = list(_iban_spans(pattern, haystack))
    else:
        spans = []
        for m in pattern.compiled().finditer(haystack):
            if m.end() == m.start():
                continue
            s = m.group(0)
            if not _safe_check(pattern, s):
                continue
            spans.append(m.span())
    if pattern.id in _GENERIC:
        # numeri che fanno parte di un IBAN, una carta, una data… non sono telefoni o importi
        blocked = _specific_spans(haystack)
        spans = [(a, b) for a, b in spans if not any(a < y and x < b for x, y in blocked)]
    out = []
    for start, end in spans:
        rects = pt.rects(start, end)
        if rects:
            out.append(Match(page_index, pt.text[start:end], rects, pattern.id))
    return out


def find(pdf: pymupdf.Document, patterns: list[Pattern], pages: Iterable[int] | None = None,
         progress: Callable[[float, str], None] | None = None) -> list[Match]:
    """Cerca gli schemi nelle pagine indicate (tutte se None). Le corrispondenze
    sovrapposte di schemi diversi vengono mantenute: l'oscuramento le unisce."""
    indices = list(range(pdf.page_count)) if pages is None else list(pages)
    out: list[Match] = []
    for n, i in enumerate(indices):
        if progress is not None:
            progress(n / max(1, len(indices)), _("Pagina {n} di {t}").format(n=n + 1, t=len(indices)))
        page = pdf[i]
        pt = page_text(page)
        for p in patterns:
            out.extend(find_in_page(page, p, i, pt))
    return out
