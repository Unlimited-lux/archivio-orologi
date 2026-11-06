"""Testo originale dei valori negli oggetti riscritti dal salvataggio incrementale.

Il motore conserva i numeri reali con precisione ridotta (float a 32 bit) e li
riscrive con meno cifre: il MediaBox A4 scritto da LibreOffice
«595.303937007874 841.889763779528» diventa «595.30398 841.8898». Quando un
aggiornamento incrementale riscrive un oggetto esistente (la pagina che riceve una
nota in /Annots, il widget di un campo compilato) anche i valori che non c'entrano
cambierebbero: per pyHanko, Acrobat e gli altri validatori la pagina firmata
risulterebbe alterata e la firma non valida.

Per ogni oggetto riscritto si individuano i valori rimasti uguali per il motore e
se ne recupera il testo esatto dal file originale (oggetto di primo livello o dentro
un object stream). Il motore non sa scrivere un testo dato, quindi nell'oggetto che
gli si passa ogni valore da conservare diventa un nome segnaposto univoco, lungo
almeno quanto il testo originale; dopo il salvataggio, nei soli byte aggiunti, il
segnaposto si sostituisce con il testo originale riempito di spazi fino alla stessa
lunghezza. Gli offset della xref (tabella o flusso) restano quindi validi.

Si conservano solo valori il cui testo originale descrive la stessa struttura vista
dal motore (stesse chiavi, stessi riferimenti e nomi, numeri uguali entro
l'arrotondamento) e che contengono almeno un numero che il motore riscriverebbe
diverso. Con un file cifrato i valori con stringhe restano quelli del motore (nel
file sono cifrati o, in un object stream, in chiaro).
"""

from __future__ import annotations

import re
import secrets
from decimal import Decimal, InvalidOperation

import pymupdf

_WS = " \t\r\n\f\x00"
_DELIMS = "()<>[]{}/%"
_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_INT = re.compile(r"\d+")
_OBJ_HEAD = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj(?=[\s<\[(/%])")
_HEX = re.compile(r"#([0-9A-Fa-f]{2})")
_STREAM_OWN_KEYS = frozenset({"/Length", "/Filter", "/DecodeParms", "/DL"})
_MAX_VALUE = 1 << 16          # valori più lunghi restano quelli del motore
_REL_TOL = Decimal("1e-5")    # numeri «uguali» fra testo originale e motore (float a 32 bit)


class _Syntax(Exception):
    pass


class _Node:
    """Valore PDF con la sua posizione nel testo: dict (items = [(chiave, nodo)]), array
    (items = [nodo]), ref, num, name, str, hex, kw."""

    __slots__ = ("kind", "start", "end", "items")

    def __init__(self, kind: str, start: int, end: int, items=None):
        self.kind, self.start, self.end, self.items = kind, start, end, items


def _skip(s: str, i: int) -> int:
    n = len(s)
    while i < n:
        c = s[i]
        if c in _WS:
            i += 1
        elif c == "%":
            while i < n and s[i] not in "\r\n":
                i += 1
        else:
            break
    return i


def _token(s: str, i: int) -> tuple[str, int, int]:
    i = _skip(s, i)
    n = len(s)
    if i >= n:
        raise _Syntax
    c = s[i]
    if c == "<":
        if s.startswith("<<", i):
            return "<<", i, i + 2
        j = s.find(">", i)
        if j < 0:
            raise _Syntax
        return "hex", i, j + 1
    if c == ">":
        if s.startswith(">>", i):
            return ">>", i, i + 2
        raise _Syntax
    if c in "[]{}":
        return c, i, i + 1
    if c == "(":
        depth, j = 0, i
        while j < n:
            ch = s[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return "str", i, j + 1
            j += 1
        raise _Syntax
    if c == ")":
        raise _Syntax
    j = i + 1 if c == "/" else i
    while j < n and s[j] not in _WS and s[j] not in _DELIMS:
        j += 1
    if c == "/":
        return "name", i, j
    if j == i:
        raise _Syntax
    return ("num" if _NUM.fullmatch(s, i, j) else "kw"), i, j


def _value(s: str, i: int) -> tuple[_Node, int]:
    kind, a, b = _token(s, i)
    if kind == "<<":
        items: list = []
        j = b
        while True:
            k, ka, kb = _token(s, j)
            if k == ">>":
                return _Node("dict", a, kb, items), kb
            if k != "name":
                raise _Syntax
            v, j = _value(s, kb)
            items.append((_name(s[ka:kb]), v))
    if kind == "[":
        items = []
        j = b
        while True:
            k, _ka, kb = _token(s, j)
            if k == "]":
                return _Node("array", a, kb, items), kb
            v, j = _value(s, j)
            items.append(v)
    if kind == "num" and _INT.fullmatch(s, a, b):
        try:   # riferimento «n g R»
            k2, a2, b2 = _token(s, b)
            if k2 == "num" and _INT.fullmatch(s, a2, b2):
                k3, a3, b3 = _token(s, b2)
                if k3 == "kw" and s[a3:b3] == "R":
                    return _Node("ref", a, b3), b3
        except _Syntax:
            pass
    if kind in (">>", "]", "{", "}"):
        raise _Syntax
    return _Node(kind, a, b), b


def _parse(s: str, i: int = 0) -> _Node:
    try:
        return _value(s, i)[0]
    except RecursionError as e:
        raise _Syntax from e


def _name(text: str) -> str:
    return _HEX.sub(lambda m: chr(int(m.group(1), 16)), text)


def _keys(node: _Node) -> dict[str, _Node] | None:
    out = dict(node.items)
    return out if len(out) == len(node.items) else None   # chiavi doppie: ambiguo


def _num(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------- testo originale


def raw_objects(pdf: pymupdf.Document, original: bytes, xrefs) -> dict[int, str]:
    """Testo (latin-1) degli oggetti `xrefs` com'è scritto nel file `original`, da cui `pdf`
    è aperto e che non è ancora stato modificato. Gli oggetti non trovati mancano."""
    try:
        get = pymupdf.mupdf.ll_pdf_get_xref_entry_no_null
        as_pdf = getattr(pymupdf, "_as_pdf_document", None)
        pdoc = as_pdf(pdf) if as_pdf is not None else pymupdf.mupdf.pdf_specifics(pdf.this)
        handle = pdoc.m_internal
    except Exception:
        return {}
    streams: dict[int, tuple] = {}
    out: dict[int, str] = {}
    for x in xrefs:
        try:
            entry = get(handle, x)
            kind = entry.type if isinstance(entry.type, str) else chr(entry.type)
            if kind == "n":
                text = _top_level(original, int(entry.ofs), x)
            elif kind == "o":
                text = _in_object_stream(pdf, streams, int(entry.ofs), int(entry.gen), x)
            else:
                text = None
        except Exception:
            text = None
        if text is not None:
            out[x] = text
    return out


def _top_level(original: bytes, ofs: int, xref: int) -> str | None:
    if not 0 <= ofs < len(original):
        return None
    for size in (1 << 16, len(original) - ofs):    # una finestra, poi il resto del file
        chunk = original[ofs:ofs + size]
        m = _OBJ_HEAD.match(chunk)
        if not m or int(m.group(1)) != xref:
            return None
        s = chunk.decode("latin-1")
        try:
            node = _parse(s, m.end())
        except _Syntax:
            if ofs + size >= len(original):
                return None
            continue
        return s[node.start:node.end]
    return None


def _in_object_stream(pdf: pymupdf.Document, cache: dict, stm: int, index: int, xref: int) -> str | None:
    if stm not in cache:
        data = pdf.xref_stream(stm)
        first = int(pdf.xref_get_key(stm, "First")[1])
        count = int(pdf.xref_get_key(stm, "N")[1])
        head = data[:first].split()
        cache[stm] = (data, first, [(int(head[2 * k]), int(head[2 * k + 1])) for k in range(count)])
    data, first, pairs = cache[stm]
    if not 0 <= index < len(pairs) or pairs[index][0] != xref:
        return None
    end = first + pairs[index + 1][1] if index + 1 < len(pairs) else len(data)
    s = data[first + pairs[index][1]:end].decode("latin-1")
    node = _parse(s)
    return s[node.start:node.end]


# --------------------------------------------------------------------------- valori da conservare


def keep_spans(raw: str, old: str, cur: str, *, stream: bool = False,
               encrypted: bool = False) -> list[tuple[int, int, str]]:
    """Valori dell'oggetto riscritto da conservare con il testo originale.

    `raw`: testo dell'oggetto nel file originale; `old`: lo stesso oggetto stampato dal
    motore; `cur`: l'oggetto nuovo stampato dal motore. Restituisce (inizio, fine) in
    `cur` e testo originale per i valori uguali in `old` e `cur` che il motore
    riscriverebbe con numeri diversi da quelli del file. Per un flusso /Length, /Filter,
    /DecodeParms e /DL restano quelli del motore."""
    try:
        r, o, c = _parse(raw), _parse(old), _parse(cur)
    except _Syntax:
        return []
    if not r.kind == o.kind == c.kind == "dict":
        return []
    out: list[tuple[int, int, str]] = []
    _walk_dict(raw, r, old, o, cur, c, out, encrypted, _STREAM_OWN_KEYS if stream else frozenset())
    return out


def _walk_dict(rs, r, os_, o, cs, c, out, encrypted, skip=frozenset()) -> None:
    rd, od = _keys(r), _keys(o)
    if rd is None or od is None:
        return
    for key, cv in c.items:
        if key not in skip and key in rd and key in od:
            _walk(rs, rd[key], os_, od[key], cs, cv, out, encrypted)


def _walk(rs, r, os_, o, cs, c, out, encrypted) -> None:
    if " ".join(os_[o.start:o.end].split()) == " ".join(cs[c.start:c.end].split()):
        # valore invariato per il motore: si rimette il testo originale se serve
        if (r.end - r.start <= _MAX_VALUE and _similar(rs, r, os_, o, encrypted)
                and _imprecise(rs, r, os_, o)):
            out.append((c.start, c.end, rs[r.start:r.end]))
        return
    if r.kind == o.kind == c.kind == "dict":
        _walk_dict(rs, r, os_, o, cs, c, out, encrypted)
    elif r.kind == o.kind == c.kind == "array" and len(r.items) == len(o.items) == len(c.items):
        for rv, ov, cv in zip(r.items, o.items, c.items):
            _walk(rs, rv, os_, ov, cs, cv, out, encrypted)


def _similar(rs, r, os_, o, encrypted) -> bool:
    """Il testo originale descrive lo stesso valore letto dal motore."""
    if r.kind != o.kind:
        return False
    if r.kind == "dict":
        rd, od = _keys(r), _keys(o)
        return (rd is not None and od is not None and rd.keys() == od.keys()
                and all(_similar(rs, rd[k], os_, od[k], encrypted) for k in rd))
    if r.kind == "array":
        return len(r.items) == len(o.items) and all(
            _similar(rs, a, os_, b, encrypted) for a, b in zip(r.items, o.items))
    rt, ot = rs[r.start:r.end], os_[o.start:o.end]
    if r.kind == "num":
        a, b = _num(rt), _num(ot)
        return a is not None and b is not None and abs(a - b) <= _REL_TOL * max(Decimal(1), abs(a))
    if r.kind == "ref":
        return [int(t) for t in rt.split()[:2]] == [int(t) for t in ot.split()[:2]]
    if r.kind == "name":
        return _name(rt) == _name(ot)
    if r.kind in ("str", "hex"):
        return not encrypted and rt == ot
    return rt == ot


def _imprecise(rs, r, os_, o) -> bool:
    """Almeno un numero del testo originale ha un valore diverso da quello del motore."""
    if r.kind == "dict":
        od = _keys(o) or {}
        return any(k in od and _imprecise(rs, v, os_, od[k]) for k, v in r.items)
    if r.kind == "array":
        return any(_imprecise(rs, a, os_, b) for a, b in zip(r.items, o.items))
    return r.kind == "num" and _num(rs[r.start:r.end]) != _num(os_[o.start:o.end])


# --------------------------------------------------------------------------- segnaposto


def with_placeholders(cur: str, spans: list[tuple[int, int, str]]) -> tuple[str, dict[bytes, bytes]]:
    """`cur` con i valori da conservare sostituiti da nomi segnaposto, e la tabella
    segnaposto → testo da scrivere (stessa lunghezza in byte)."""
    parts: list[str] = []
    repl: dict[bytes, bytes] = {}
    last = 0
    for a, b, raw in sorted(spans):
        if a < last:
            continue
        text = raw.encode("latin-1")
        tag = "FoliantKeep" + secrets.token_hex(8)
        size = max(len(text) + 1, len(tag) + 1)       # spazio iniziale + testo
        name = "/" + tag + "x" * (size - 1 - len(tag))
        parts += [cur[last:a], name, " "]
        repl[name.encode("ascii")] = (b" " + text).ljust(size, b" ")
        last = b
    parts.append(cur[last:])
    return "".join(parts), repl


_REGULAR_AFTER = re.compile(rb"[^\s()<>\[\]{}/%]")


def fill_placeholders(data: bytes, start: int, repl: dict[bytes, bytes]) -> bytes | None:
    """Sostituisce i segnaposto nei byte da `start` in poi; None se uno non compare esattamente
    una volta come nome intero."""
    tail = data[start:]
    for name, text in repl.items():
        at = tail.find(name)
        if at < 0 or tail.find(name, at + 1) >= 0:
            return None
        end = at + len(name)
        if end < len(tail) and _REGULAR_AFTER.match(tail, end):
            return None
        tail = tail[:at] + text + tail[end:]
    return data[:start] + tail
