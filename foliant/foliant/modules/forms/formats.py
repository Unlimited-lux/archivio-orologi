"""Formati dei campi di testo: numeri, percentuali, date, ore e formati speciali.

Il formato è memorizzato nel PDF come chiamata alle funzioni predefinite dei
moduli (prefisso «AF», convenzione di scripting pubblica per PDF) nelle azioni
di formato (/AA /F) e battitura (/AA /K): così gli altri lettori con supporto
agli script ottengono lo stesso comportamento, mentre Foliant le esegue in
modo nativo. Il valore memorizzato resta in forma canonica (numeri con punto
decimale e senza separatori delle migliaia); la forma formattata compare solo
nell'aspetto.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from foliant.i18n import _

NONE, NUMBER, PERCENT, DATE, TIME, SPECIAL = "none", "number", "percent", "date", "time", "special"

# Stili dei separatori (indice = argomento delle funzioni predefinite).
SEPARATORS = ["1,234.56", "1234.56", "1.234,56", "1234,56", "1'234.56"]
# Stili dei negativi: 0 segno meno · 1 rosso · 2 parentesi · 3 rosso tra parentesi.
NEGATIVES = 4
# Formati di ora predefiniti (argomento di AFTime_Format).
TIME_PATTERNS = ["HH:MM", "h:MM tt", "HH:MM:ss", "h:MM:ss tt"]
# Formati speciali predefiniti (argomento di AFSpecial_Format) come maschere.
SPECIAL_MASKS = ["99999", "99999-9999", "(999) 999-9999", "999-99-9999"]

DATE_PATTERNS = ["dd/mm/yyyy", "d/m/yyyy", "dd/mm/yy", "dd-mm-yyyy", "dd.mm.yyyy", "yyyy-mm-dd",
                 "mm/yyyy", "d mmmm yyyy", "mm/dd/yyyy"]

_MONTHS_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
              "settembre", "ottobre", "novembre", "dicembre"]
_MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]


class FormatError(ValueError):
    """Valore non interpretabile secondo il formato (messaggio per l'utente)."""


def _js_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class Format:
    type: str = NONE
    decimals: int = 2
    separator: int = 2
    negative: int = 0
    currency: str = ""
    currency_before: bool = False
    pattern: str = "dd/mm/yyyy"     # data o ora
    mask: str = ""                   # formato speciale (9 cifra, A lettera, O alfanumerico, X qualsiasi)

    # ---------------------------------------------------------------- script
    def scripts(self) -> tuple[str | None, str | None]:
        """(azione di formato, azione di battitura)."""
        if self.type == NUMBER:
            args = (f"{self.decimals}, {self.separator}, {self.negative}, 0, {_js_str(self.currency)}, "
                    f"{'true' if self.currency_before else 'false'}")
            return f"AFNumber_Format({args});", f"AFNumber_Keystroke({args});"
        if self.type == PERCENT:
            args = f"{self.decimals}, {self.separator}"
            return f"AFPercent_Format({args});", f"AFPercent_Keystroke({args});"
        if self.type == DATE:
            return f"AFDate_FormatEx({_js_str(self.pattern)});", f"AFDate_KeystrokeEx({_js_str(self.pattern)});"
        if self.type == TIME:
            if self.pattern in TIME_PATTERNS:
                n = TIME_PATTERNS.index(self.pattern)
                return f"AFTime_Format({n});", f"AFTime_Keystroke({n});"
            return f"AFTime_FormatEx({_js_str(self.pattern)});", f"AFTime_KeystrokeEx({_js_str(self.pattern)});"
        if self.type == SPECIAL:
            if self.mask in SPECIAL_MASKS:
                n = SPECIAL_MASKS.index(self.mask)
                return f"AFSpecial_Format({n});", f"AFSpecial_Keystroke({n});"
            return f"AFSpecial_KeystrokeEx({_js_str(self.mask)});", f"AFSpecial_KeystrokeEx({_js_str(self.mask)});"
        return None, None

    @classmethod
    def from_script(cls, script: str | None) -> "Format":
        """Riconosce una sola chiamata predefinita con argomenti letterali; altrimenti nessun formato."""
        s = (script or "").strip()
        lit = r'"((?:[^"\\]|\\.)*)"'
        m = re.fullmatch(r"AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*,\s*" + lit +
                         r"\s*,\s*(true|false)\s*\)\s*;?", s)
        if m:
            return cls(NUMBER, min(10, int(m.group(1))), min(4, int(m.group(2))), min(3, int(m.group(3))),
                       _unjs(m.group(4)), m.group(5) == "true")
        m = re.fullmatch(r"AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*\w+\s*)?\)\s*;?", s)
        if m:
            return cls(PERCENT, min(10, int(m.group(1))), min(4, int(m.group(2))))
        m = re.fullmatch(r"AFDate_Format(?:Ex)?\(\s*" + lit + r"\s*\)\s*;?", s)
        if m:
            return cls(DATE, pattern=_unjs(m.group(1)))
        m = re.fullmatch(r"AFTime_Format\(\s*(\d)\s*\)\s*;?", s)
        if m and int(m.group(1)) < len(TIME_PATTERNS):
            return cls(TIME, pattern=TIME_PATTERNS[int(m.group(1))])
        m = re.fullmatch(r"AFTime_FormatEx\(\s*" + lit + r"\s*\)\s*;?", s)
        if m:
            return cls(TIME, pattern=_unjs(m.group(1)))
        m = re.fullmatch(r"AFSpecial_Format\(\s*(\d)\s*\)\s*;?", s)
        if m and int(m.group(1)) < len(SPECIAL_MASKS):
            return cls(SPECIAL, mask=SPECIAL_MASKS[int(m.group(1))])
        m = re.fullmatch(r"AFSpecial_KeystrokeEx\(\s*" + lit + r"\s*\)\s*;?", s)
        if m:
            return cls(SPECIAL, mask=_unjs(m.group(1)))
        return cls()

    @property
    def numeric(self) -> bool:
        return self.type in (NUMBER, PERCENT)

    def label(self) -> str:
        return {NONE: _("Nessuno"), NUMBER: _("Numero"), PERCENT: _("Percentuale"), DATE: _("Data"),
                TIME: _("Ora"), SPECIAL: _("Speciale")}[self.type]


def _unjs(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def is_custom_script(script: str | None) -> bool:
    """True se l'azione contiene codice che non è una sola chiamata predefinita riconosciuta."""
    s = (script or "").strip()
    if not s:
        return False
    if Format.from_script(s).type != NONE:
        return False
    if re.fullmatch(r"AF\w+_Keystroke(?:Ex)?\([^;]*\)\s*;?", s):
        return False
    return True


# ====================================================================== numeri
def _decimal_comma(separator: int | None) -> bool:
    """Stili 2 («1.234,56») e 3 («1234,56»): la virgola è il separatore decimale."""
    return separator in (2, 3)


def parse_number(text: Any, separator: int | None = None) -> float | None:
    """Interpreta numeri all'italiana o all'inglese («1.234,5», «1,234.5», «1'234.5», «€ 3»).

    `separator`: stile dei separatori del campo (0-4) per l'input dell'utente: il testo si legge
    secondo quello stile («12.500» con lo stile 2 è dodicimilacinquecento). Senza stile si
    indovina, come per i valori memorizzati (canonici, con il punto decimale) e importati."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text) if math.isfinite(text) else None
    s = str(text).strip().replace("−", "-").replace("'", "").replace(" ", "").replace(" ", "")
    if separator is not None:
        if _decimal_comma(separator):
            s = s.replace(".", "")
            if s.count(",") > 1:
                return None
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
            if s.count(".") > 1:
                return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d,.\-+eE]", "", s)
    s = re.sub(r"[eE](?![-+]?\d)", "", s)
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return -abs(v) if negative else v


def canonical_number(value: float, decimals: int | None = None) -> str:
    """Forma canonica memorizzata: punto decimale, niente migliaia, 15 cifre significative."""
    if value == 0:
        return "0"
    v = float(f"{value:.15g}")
    if decimals is not None:
        v = round(v, decimals)
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    if re.fullmatch(r"-?0", s):
        return "0"
    return s


def _group(intpart: str, sep: str) -> str:
    out = []
    while len(intpart) > 3:
        out.insert(0, intpart[-3:])
        intpart = intpart[:-3]
    out.insert(0, intpart)
    return sep.join(out)


def number_text(value: float, decimals: int, separator: int) -> str:
    dec = max(0, min(10, decimals))
    s = f"{abs(value):.{dec}f}"
    intpart, _dot, frac = s.partition(".")
    thousands, point = {0: (",", "."), 1: ("", "."), 2: (".", ","), 3: ("", ","), 4: ("'", ".")}.get(separator, (",", "."))
    text = _group(intpart, thousands) if thousands else intpart
    if frac:
        text += point + frac
    return text


def display(fmt: Format, raw: Any) -> tuple[str, bool]:
    """(testo visualizzato, in rosso) per il valore memorizzato `raw`."""
    text = "" if raw is None else str(raw)
    if not text.strip() or fmt.type == NONE:
        return text, False
    if fmt.type in (NUMBER, PERCENT):
        v = parse_number(text)
        if v is None:
            return text, False
        if fmt.type == PERCENT:
            return ("-" if v < 0 else "") + number_text(v * 100, fmt.decimals, fmt.separator) + "%", False
        body = number_text(v, fmt.decimals, fmt.separator)
        if fmt.currency:
            body = f"{fmt.currency}{body}" if fmt.currency_before else f"{body} {fmt.currency}"
        red = False
        if v < 0 and round(abs(v), fmt.decimals) > 0:
            if fmt.negative in (2, 3):
                body = f"({body})"
            else:
                body = "-" + body
            red = fmt.negative in (1, 3)
        return body, red
    if fmt.type == DATE:
        d = parse_date(text, fmt.pattern)
        return (format_date(d, fmt.pattern) if d else text), False
    if fmt.type == TIME:
        t = parse_time(text, fmt.pattern)
        return (format_time(t, fmt.pattern) if t else text), False
    if fmt.type == SPECIAL:
        return apply_mask(fmt.mask, text) or text, False
    return text, False


def normalize(fmt: Format, text: str) -> str:
    """Valore da memorizzare per l'input dell'utente. Solleva FormatError se non interpretabile."""
    s = (text or "").strip()
    if not s or fmt.type == NONE:
        return text
    if fmt.type in (NUMBER, PERCENT):
        v = parse_number(s, fmt.separator)
        if v is None:
            raise FormatError(_("Va inserito un numero."))
        if fmt.type == PERCENT and s.endswith("%"):
            v = v / 100
        return canonical_number(v)
    if fmt.type == DATE:
        d = parse_date(s, fmt.pattern)
        if d is None:
            raise FormatError(_("Data non valida: usa il formato {p}.").format(p=pattern_label(fmt.pattern)))
        return format_date(d, fmt.pattern)
    if fmt.type == TIME:
        t = parse_time(s, fmt.pattern)
        if t is None:
            raise FormatError(_("Ora non valida: usa il formato {p}.").format(p=pattern_label(fmt.pattern)))
        return format_time(t, fmt.pattern)
    if fmt.type == SPECIAL:
        raw = mask_raw(fmt.mask, s)
        if raw is None:
            raise FormatError(_("Il valore non rispetta il formato {m}.").format(m=fmt.mask))
        return raw
    return text


def edit_text(fmt: Format, value: str) -> str:
    """Testo nell'editor per un valore memorizzato: per i numeri la forma canonica con il
    separatore decimale dello stile del campo, senza migliaia («1234,5» con lo stile 2), così
    che confermarlo senza modifiche dia lo stesso valore."""
    if fmt.type in (NUMBER, PERCENT) and value and _decimal_comma(fmt.separator):
        if parse_number(value) is not None and "," not in value:
            return value.replace(".", ",")
    return value


def filter_input(fmt: Format, text: str) -> str:
    """Filtro di battitura: toglie i caratteri non ammessi dal formato (es. lettere nei numeri)."""
    if fmt.type in (NUMBER, PERCENT):
        allowed = set("0123456789-+.,'() ")
        if fmt.type == PERCENT:
            allowed.add("%")
        return "".join(c for c in text if c in allowed)
    if fmt.type in (DATE, TIME):
        return "".join(c for c in text if c.isalnum() or c in "/-.: ")
    if fmt.type == SPECIAL and fmt.mask:
        out, slots = [], [m for m in fmt.mask if m in "9AOX"]
        for c in text:
            if len(out) >= len(slots):
                break
            if _fits(slots[len(out)], c):
                out.append(c)
        return "".join(out)
    return text


# ====================================================================== maschere
def _fits(slot: str, c: str) -> bool:
    return ((slot == "9" and c.isdigit()) or (slot == "A" and c.isalpha()) or
            (slot == "O" and c.isalnum()) or slot == "X")


def mask_raw(mask: str, text: str) -> str | None:
    """Caratteri utili del valore secondo la maschera; None se non conforme."""
    slots = [m for m in mask if m in "9AOX"]
    chars = [c for c in text if not (c in "()-. /" and "X" not in slots)]
    if any(m == "X" for m in slots):
        chars = list(text)
        literal = [m for m in mask if m not in "9AOX"]
        if len(chars) == len(mask) and all(chars[i] == m for i, m in enumerate(mask) if m not in "9AOX"):
            chars = [chars[i] for i, m in enumerate(mask) if m in "9AOX"]
        del literal
    if len(chars) != len(slots) or not all(_fits(s, c) for s, c in zip(slots, chars)):
        return None
    return "".join(chars)


def apply_mask(mask: str, raw: str) -> str | None:
    chars = mask_raw(mask, raw)
    if chars is None:
        return None
    out, it = [], iter(chars)
    for m in mask:
        out.append(next(it) if m in "9AOX" else m)
    return "".join(out)


# ====================================================================== date e ore
def pattern_label(pattern: str) -> str:
    """Modello mostrato all'utente (gg/mm/aaaa in italiano)."""
    from foliant.i18n import language
    if language() == "it":
        return pattern.replace("yyyy", "aaaa").replace("yy", "aa").replace("dd", "gg").replace("d", "g")
    return pattern


def _tokens(pattern: str) -> list[str]:
    return re.findall(r"yyyy|yy|mmmm|mmm|mm|m|dd|d|HH|H|hh|h|MM|M|ss|s|tt|.", pattern)


def _month_from_name(word: str) -> int | None:
    w = word.lower().strip(".")
    if len(w) < 3:
        return None
    for names in (_MONTHS_IT, _MONTHS_EN):
        for i, n in enumerate(names):
            if n.startswith(w) or (len(w) >= 3 and w == n[:3]):
                return i + 1
    return None


def _year(y: str) -> int:
    v = int(y)
    if len(y) <= 2:
        v += 2000 if v < 50 else 1900
    return v


def parse_date(text: str, pattern: str = "dd/mm/yyyy") -> date | None:
    """Interpreta una data con separatori qualsiasi, anno a 2 cifre e nomi dei mesi (it/en),
    secondo l'ordine giorno/mese/anno del modello."""
    s = (text or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)  # forma ISO sempre accettata
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    parts = re.findall(r"\d+|[^\W\d_]+", s)
    order = [t[0].lower() for t in _tokens(pattern) if t[0] in "dmy" and t != "tt"]
    order = [o for i, o in enumerate(order) if o not in order[:i]]
    if not order:
        order = ["d", "m", "y"]
    values: dict[str, int] = {}
    numbers = []
    for p in parts:
        if p.isdigit():
            numbers.append(p)
        else:
            mon = _month_from_name(p)
            if mon is None:
                return None
            values["m"] = mon
    remaining = [o for o in order if o not in values]
    if len(numbers) == 1 and len(numbers[0]) in (6, 8) and len(remaining) == 3:  # 010326 / 01032026
        n = numbers[0]
        chunks, i = [], 0
        for o in remaining:
            size = (len(n) - 4) if o == "y" else 2
            chunks.append(n[i:i + size])
            i += size
        numbers = chunks
    if len(numbers) != len(remaining):
        if "d" not in order and len(numbers) == len(remaining):
            pass
        else:
            return None
    for o, n in zip(remaining, numbers):
        values[o] = _year(n) if o == "y" else int(n)
    try:
        return date(values.get("y", date.today().year), values.get("m", 1), values.get("d", 1))
    except ValueError:
        return None


def format_date(d: date, pattern: str = "dd/mm/yyyy") -> str:
    from foliant.i18n import language
    months = _MONTHS_IT if language() == "it" else _MONTHS_EN
    out = []
    for t in _tokens(pattern):
        if t == "yyyy":
            out.append(f"{d.year:04d}")
        elif t == "yy":
            out.append(f"{d.year % 100:02d}")
        elif t == "mmmm":
            out.append(months[d.month - 1])
        elif t == "mmm":
            out.append(months[d.month - 1][:3])
        elif t == "mm":
            out.append(f"{d.month:02d}")
        elif t == "m":
            out.append(str(d.month))
        elif t == "dd":
            out.append(f"{d.day:02d}")
        elif t == "d":
            out.append(str(d.day))
        else:
            out.append(t)
    return "".join(out)


def parse_time(text: str, pattern: str = "HH:MM") -> time | None:
    s = (text or "").strip().lower()
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{1,2}))?(?:[:.](\d{1,2}))?\s*(am|pm|a|p)?", s)
    if not m:
        m2 = re.fullmatch(r"(\d{3,4})", s)
        if not m2:
            return None
        n = m2.group(1).zfill(4)
        h, mi, sec, ap = int(n[:2]), int(n[2:]), 0, None
    else:
        h, mi, sec = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
        ap = m.group(4)
    if ap:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ap.startswith("p") else 0)
    try:
        return time(h, mi, sec)
    except ValueError:
        return None


def format_time(t: time, pattern: str = "HH:MM") -> str:
    out = []
    for tok in _tokens(pattern):
        if tok == "HH":
            out.append(f"{t.hour:02d}")
        elif tok == "H":
            out.append(str(t.hour))
        elif tok in ("hh", "h"):
            h = t.hour % 12 or 12
            out.append(f"{h:02d}" if tok == "hh" else str(h))
        elif tok == "MM":
            out.append(f"{t.minute:02d}")
        elif tok == "M":
            out.append(str(t.minute))
        elif tok == "ss":
            out.append(f"{t.second:02d}")
        elif tok == "s":
            out.append(str(t.second))
        elif tok == "tt":
            out.append("AM" if t.hour < 12 else "PM")
        else:
            out.append(tok)
    return "".join(out)


def pattern_has_day(pattern: str) -> bool:
    return any(t in ("d", "dd") for t in _tokens(pattern))


def valid_date_pattern(pattern: str) -> bool:
    toks = _tokens(pattern)
    return any(t in ("yyyy", "yy") for t in toks) or any(t in ("mm", "m", "mmm", "mmmm") for t in toks)


def to_iso(fmt: Format, raw: str) -> str:
    """Forma ISO di date e ore (per i dati XML)."""
    if fmt.type == DATE:
        d = parse_date(raw, fmt.pattern)
        return d.isoformat() if d else raw
    if fmt.type == TIME:
        t = parse_time(raw, fmt.pattern)
        return t.strftime("%H:%M:%S") if t else raw
    return raw


def today_text(fmt: Format) -> str:
    return format_date(datetime.now().date(), fmt.pattern)
