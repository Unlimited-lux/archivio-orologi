"""Lettura e scrittura minima della sintassi degli oggetti PDF.

Serve per i valori dei campi (array, nomi, stringhe) letti con
`xref_get_key` e per il formato FDF, che usa la stessa sintassi del PDF.
Non dipende dal motore PDF.
"""

from __future__ import annotations

import codecs
import re

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


class Name(str):
    """Nome PDF (/Nome)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Name({str(self)!r})"


class Ref(tuple):
    """Riferimento indiretto «n g R»."""

    def __new__(cls, num: int, gen: int = 0):
        return super().__new__(cls, (num, gen))


class PdfSyntaxError(ValueError):
    pass


def decode_text(raw: bytes) -> str:
    """Decodifica una stringa di testo PDF (UTF-16 con BOM, UTF-8 con BOM o PDFDocEncoding≈Latin-1)."""
    if raw.startswith(codecs.BOM_UTF16_BE):
        return raw[2:].decode("utf-16-be", errors="replace")
    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(codecs.BOM_UTF8):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("latin-1")


class _Parser:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def ws(self) -> None:
        d, n = self.d, len(self.d)
        while self.i < n:
            c = d[self.i]
            if c in _WS:
                self.i += 1
            elif c == 0x25:  # % commento
                while self.i < n and d[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def eof(self) -> bool:
        self.ws()
        return self.i >= len(self.d)

    def peek_keyword(self, word: bytes) -> bool:
        self.ws()
        return self.d.startswith(word, self.i)

    def regular(self) -> bytes:
        start = self.i
        d, n = self.d, len(self.d)
        while self.i < n and d[self.i] not in _WS and d[self.i] not in _DELIM:
            self.i += 1
        return d[start:self.i]

    def value(self):
        self.ws()
        if self.i >= len(self.d):
            raise PdfSyntaxError("fine inattesa")
        c = self.d[self.i:self.i + 1]
        if c == b"/":
            self.i += 1
            raw = self.regular()
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("utf-8", "replace"))
        if c == b"(":
            return self.literal()
        if self.d.startswith(b"<<", self.i):
            self.i += 2
            out: dict = {}
            while True:
                self.ws()
                if self.d.startswith(b">>", self.i):
                    self.i += 2
                    return out
                key = self.value()
                if not isinstance(key, Name):
                    raise PdfSyntaxError("chiave di dizionario non valida")
                out[str(key)] = self.value()
        if c == b"<":
            self.i += 1
            end = self.d.find(b">", self.i)
            if end < 0:
                raise PdfSyntaxError("stringa esadecimale non chiusa")
            hexs = re.sub(rb"\s", b"", self.d[self.i:end])
            self.i = end + 1
            if len(hexs) % 2:
                hexs += b"0"
            return bytes.fromhex(hexs.decode("ascii"))
        if c == b"[":
            self.i += 1
            arr = []
            while True:
                self.ws()
                if self.d.startswith(b"]", self.i):
                    self.i += 1
                    return arr
                arr.append(self.value())
        tok = self.regular()
        if not tok:
            raise PdfSyntaxError(f"carattere inatteso {c!r}")
        if tok == b"true":
            return True
        if tok == b"false":
            return False
        if tok == b"null":
            return None
        try:
            if re.fullmatch(rb"[+-]?\d+", tok):
                num = int(tok)
                # riferimento «n g R»?
                save = self.i
                self.ws()
                gen = self.regular()
                if re.fullmatch(rb"\d+", gen or b"-"):
                    self.ws()
                    if self.d.startswith(b"R", self.i) and (self.i + 1 >= len(self.d) or self.d[self.i + 1] in _WS + _DELIM):
                        self.i += 1
                        return Ref(num, int(gen))
                self.i = save
                return num
            return float(tok)
        except ValueError:
            return Name(tok.decode("latin-1"))  # parola chiave sconosciuta (es. obj, trailer)

    def literal(self) -> bytes:
        self.i += 1
        out = bytearray()
        depth = 1
        d, n = self.d, len(self.d)
        while self.i < n:
            c = d[self.i]
            self.i += 1
            if c == 0x5C:  # backslash
                if self.i >= n:
                    break
                e = d[self.i]
                self.i += 1
                simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12, 0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
                if e in simple:
                    out.append(simple[e])
                elif 0x30 <= e <= 0x37:
                    digits = bytes([e])
                    while len(digits) < 3 and self.i < n and 0x30 <= d[self.i] <= 0x37:
                        digits += bytes([d[self.i]])
                        self.i += 1
                    out.append(int(digits, 8) & 0xFF)
                elif e == 0x0D:
                    if self.i < n and d[self.i] == 0x0A:
                        self.i += 1
                elif e == 0x0A:
                    pass
                else:
                    out.append(e)
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    return bytes(out)
                out.append(c)
            else:
                out.append(c)
        raise PdfSyntaxError("stringa non chiusa")


def parse(data: bytes | str):
    """Interpreta un singolo oggetto PDF."""
    if isinstance(data, str):
        data = data.encode("latin-1", errors="replace")
    return _Parser(data).value()


def parser(data: bytes) -> _Parser:
    return _Parser(data)


def text_of(value) -> str | None:
    """Testo di un valore PDF (stringa, nome o numero); None se assente."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return decode_text(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ------------------------------------------------------------------ scrittura
def encode_string(text: str) -> str:
    """Stringa di testo PDF: letterale Latin-1 se possibile, altrimenti UTF-16BE esadecimale."""
    try:
        raw = text.encode("latin-1")
        if any(0x80 <= b <= 0x9F for b in raw):
            raise UnicodeEncodeError("latin-1", text, 0, 1, "intervallo di controllo")
    except UnicodeEncodeError:
        return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"
    out = []
    for b in raw:
        ch = chr(b)
        if ch in "()\\":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif b < 32 or b > 126:
            out.append("\\%03o" % b)
        else:
            out.append(ch)
    return "(" + "".join(out) + ")"


def encode_name(name: str) -> str:
    out = []
    for b in name.encode("utf-8"):
        c = chr(b)
        if b < 33 or b > 126 or c in "#()<>[]{}/%":
            out.append("#%02X" % b)
        else:
            out.append(c)
    return "/" + "".join(out)


def serialize(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Name):
        return encode_name(value)
    if isinstance(value, Ref):
        return f"{value[0]} {value[1]} R"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, int) else ("%f" % value).rstrip("0").rstrip(".")
    if isinstance(value, bytes):
        return "<" + value.hex().upper() + ">"
    if isinstance(value, str):
        return encode_string(value)
    if isinstance(value, dict):
        return "<<" + "".join(f"{encode_name(k)} {serialize(v)}" for k, v in value.items()) + ">>"
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(serialize(v) for v in value) + "]"
    raise TypeError(f"tipo non serializzabile: {type(value).__name__}")
