"""Analisi del content stream di una pagina in oggetti modificabili.

Logica pura (nessuna interfaccia). Il flusso dei contenuti viene letto come
sequenza di operatori con la loro posizione in byte; da questa si ricavano gli
oggetti della pagina (immagini, tracciati, oggetti composti, sfumature) con la
matrice di trasformazione corrente e lo stato grafico necessario a ricrearli.

Convenzioni: lo spazio "pdf" è lo spazio utente predefinito della pagina
(origine in basso a sinistra); lo spazio "pagina" è quello del motore
(punti, pagina non ruotata, origine in alto a sinistra del riquadro visibile).
Le matrici seguono la convenzione del motore: `a * b` applica prima `a`, poi `b`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

# ----------------------------------------------------------------- tokenizer
_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_TOKEN_RE = re.compile(
    rb"(?P<ws>[\x00\t\n\x0c\r ]+)"
    rb"|(?P<comment>%[^\r\n]*)"
    rb"|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))(?![^\x00\t\n\x0c\r ()<>\[\]{}/%])"
    rb"|(?P<name>/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*)"
    rb"|(?P<dopen><<)|(?P<dclose>>>)"
    rb"|(?P<hex><[0-9A-Fa-f\x00\t\n\x0c\r ]*>)"
    rb"|(?P<aopen>\[)|(?P<aclose>\])"
    rb"|(?P<bopen>\{)|(?P<bclose>\})"
    rb"|(?P<str>\()"
    rb"|(?P<kw>[^\x00\t\n\x0c\r ()<>\[\]{}/%]+)"
)


class Name(str):
    """Nome PDF (senza la barra iniziale)."""

    def __repr__(self) -> str:
        return "/" + str(self)


class ContentError(Exception):
    pass


def _name_decode(raw: bytes) -> str:
    s = raw[1:]
    if b"#" in s:
        s = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), s)
    return s.decode("latin-1")


def _read_string(data: bytes, pos: int) -> tuple[bytes, int]:
    """Legge una stringa letterale che inizia in `pos` (carattere '(')."""
    depth = 0
    i = pos
    n = len(data)
    out = bytearray()
    while i < n:
        c = data[i]
        if c == 0x5C:  # backslash
            i += 1
            if i < n:
                out.append(data[i])
            i += 1
            continue
        if c == 0x28:
            depth += 1
            if depth > 1:
                out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), n


@dataclass
class Op:
    """Operatore con i suoi operandi e l'intervallo di byte [start, end)."""

    name: str
    operands: list
    start: int
    end: int
    data: dict | None = None   # immagini inline: dizionario e dati


def _read_inline_image(data: bytes, pos: int) -> tuple[dict, int, int]:
    """Dopo 'BI': legge il dizionario fino a 'ID', poi i dati fino a 'EI'.

    Restituisce (dizionario, inizio_dati, fine_dopo_EI)."""
    d: dict = {}
    key = None
    i = pos
    n = len(data)
    while i < n:
        m = _TOKEN_RE.match(data, i)
        if m is None:
            raise ContentError("immagine inline non valida")
        i = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        tok = m.group(kind)
        if kind == "kw" and tok == b"ID":
            break
        if kind == "aopen":
            arr, i = _read_array(data, i)
            val = arr
        elif kind == "name":
            val = Name(_name_decode(tok))
        elif kind == "num":
            val = float(tok) if b"." in tok else int(tok)
        elif kind == "dopen":
            val, i = _read_dict(data, i)
        else:
            val = tok
        if key is None:
            key = val
        else:
            d[str(key)] = val
            key = None
    # un solo carattere di spazio dopo ID
    start = i + 1 if i < n and data[i] in _WS else i
    length = d.get("L") or d.get("Length")
    if isinstance(length, int) and start + length <= n:
        j = data.find(b"EI", start + length)
    else:
        j = start
        while True:
            j = data.find(b"EI", j)
            if j < 0:
                break
            before_ok = j > 0 and data[j - 1] in _WS
            after_ok = j + 2 >= n or data[j + 2] in _WS
            if before_ok and after_ok:
                break
            j += 2
    if j < 0:
        raise ContentError("immagine inline senza EI")
    return d, start, j + 2


def _read_array(data: bytes, pos: int) -> tuple[list, int]:
    out: list = []
    i = pos
    n = len(data)
    while i < n:
        val, i, kind = _read_value(data, i)
        if kind == "aclose":
            return out, i
        if kind is None:
            break
        out.append(val)
    return out, i


class Ref(tuple):
    """Riferimento indiretto «n g R»."""


def _fold_refs(vals: list) -> list:
    out: list = []
    for v in vals:
        if v == "R" and len(out) >= 2 and type(out[-1]) is int and type(out[-2]) is int:
            g = out.pop()
            n = out.pop()
            out.append(Ref((n, g)))
        else:
            out.append(v)
    return out


def _read_dict(data: bytes, pos: int) -> tuple[dict, int]:
    vals: list = []
    i = pos
    n = len(data)
    while i < n:
        val, i, kind = _read_value(data, i)
        if kind == "dclose" or kind is None:
            break
        vals.append(val)
    vals = _fold_refs(vals)
    out: dict = {}
    for k in range(0, len(vals) - 1, 2):
        out[str(vals[k])] = vals[k + 1]
    return out, i


def _read_value(data: bytes, i: int):
    n = len(data)
    while i < n:
        m = _TOKEN_RE.match(data, i)
        if m is None:
            return None, i + 1, "junk"
        kind = m.lastgroup
        tok = m.group(kind)
        if kind in ("ws", "comment"):
            i = m.end()
            continue
        if kind == "num":
            return (float(tok) if b"." in tok else int(tok)), m.end(), kind
        if kind == "name":
            return Name(_name_decode(tok)), m.end(), kind
        if kind == "str":
            s, j = _read_string(data, m.start())
            return s, j, kind
        if kind == "hex":
            h = re.sub(rb"[^0-9A-Fa-f]", b"", tok[1:-1])
            if len(h) % 2:
                h += b"0"
            return bytes.fromhex(h.decode()), m.end(), kind
        if kind == "aopen":
            arr, j = _read_array(data, m.end())
            return arr, j, "array"
        if kind == "dopen":
            d, j = _read_dict(data, m.end())
            return d, j, "dict"
        if kind in ("aclose", "dclose", "bopen", "bclose"):
            return None, m.end(), kind
        return tok.decode("latin-1"), m.end(), "kw"
    return None, n, None


def tokenize_ops(data: bytes) -> list[Op]:
    """Scompone un content stream in operatori con operandi e posizione."""
    ops: list[Op] = []
    operands: list = []
    start = None
    i = 0
    n = len(data)
    while i < n:
        m = _TOKEN_RE.match(data, i)
        if m is None:
            i += 1
            continue
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            i = m.end()
            continue
        tok_start = m.start()
        if kind == "kw":
            name = m.group(kind).decode("latin-1")
            if start is None:
                start = tok_start
            i = m.end()
            if name == "BI":
                try:
                    d, dstart, end = _read_inline_image(data, i)
                except ContentError:
                    break
                ops.append(Op("BI", [], start, end, {"dict": d, "data_start": dstart}))
                i = end
            elif name in ("true", "false", "null"):
                operands.append({"true": True, "false": False, "null": None}[name])
                continue
            else:
                ops.append(Op(name, operands, start, i))
            operands = []
            start = None
            continue
        val, j, vk = _read_value(data, i)
        if vk in ("aclose", "dclose", "bopen", "bclose", "junk", None):
            i = j
            continue
        if start is None:
            start = tok_start
        operands.append(val)
        i = j
    return ops


# -------------------------------------------------------------- formattazione
def fmt_num(v: float) -> str:
    # i calcoli del motore sono in precisione singola: si eliminano i residui
    if abs(v - round(v)) < 2e-4 * max(1.0, min(abs(v), 1000.0) / 100.0):
        return str(int(round(v)))
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_matrix(m: pymupdf.Matrix) -> bytes:
    return " ".join(fmt_num(v) for v in (m.a, m.b, m.c, m.d, m.e, m.f)).encode() + b" cm"


def fmt_name(name: str) -> bytes:
    out = []
    for ch in name.encode("latin-1", "replace"):
        if ch < 33 or ch > 126 or ch in b"#()<>[]{}/%":
            out.append(b"#%02X" % ch)
        else:
            out.append(bytes([ch]))
    return b"/" + b"".join(out)


def matrix_close(a: pymupdf.Matrix, b: pymupdf.Matrix, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol * max(1.0, abs(x), abs(y)) for x, y in zip(a, b))


# ---------------------------------------------------------------- oggetti
IMAGE = "immagine"
PATH = "tracciato"
FORM = "composto"
SHADING = "sfumatura"

PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
PATH_OPS = {"m", "l", "c", "v", "y", "h", "re"}
# operatori di stato grafico conservati per ricreare un oggetto altrove
STATE_KEYS = {
    "w": "w", "J": "J", "j": "j", "M": "M", "d": "d", "ri": "ri", "i": "i",
}


@dataclass
class GState:
    ctm: pymupdf.Matrix
    ops: dict = field(default_factory=dict)       # chiave -> bytes dell'operatore
    gs: list = field(default_factory=list)       # bytes degli operatori gs, in ordine
    fill_alpha: float = 1.0
    stroke_alpha: float = 1.0
    clip: pymupdf.Rect | None = None             # riquadro del ritaglio (spazio pagina)
    line_width: float = 1.0

    def copy(self) -> "GState":
        return GState(pymupdf.Matrix(self.ctm), dict(self.ops), list(self.gs), self.fill_alpha,
                      self.stroke_alpha, pymupdf.Rect(self.clip) if self.clip is not None else None,
                      self.line_width)

    def state_bytes(self) -> bytes:
        parts = list(self.gs)
        for key in ("w", "J", "j", "M", "d", "ri", "i", "CS", "SC", "cs", "sc"):
            if key in self.ops:
                parts.append(self.ops[key])
        return b"\n".join(parts)


@dataclass
class Wrapper:
    """Coppia q…Q che contiene solo l'oggetto (più cm, gs e ritaglio rettangolare)."""

    start: int
    end: int
    outer_ctm: pymupdf.Matrix
    gs_name: str | None
    clip_unit: tuple | None        # ritaglio (x, y, w, h) nello spazio dell'oggetto


@dataclass
class PageObject:
    index: int
    kind: str
    start: int
    end: int
    ctm: pymupdf.Matrix              # spazio oggetto -> spazio pdf
    quad: pymupdf.Quad               # contorno nello spazio pagina
    rect: pymupdf.Rect               # riquadro visivo nello spazio pagina (limitato dal ritaglio)
    state: GState
    name: str | None = None          # nome della risorsa XObject
    xref: int = 0
    inline: bool = False
    paint: str = ""
    segments: list = field(default_factory=list)   # tracciati: sottotracciati in spazio pagina
    wrapper: Wrapper | None = None
    hidden: bool = False             # opacità 0 o interamente ritagliato
    width: int = 0                   # immagini: pixel
    height: int = 0
    stroke_color: tuple | None = None
    fill_color: tuple | None = None
    op_range: tuple = (0, 0)         # indici del primo e dell'ultimo operatore

    @property
    def span(self) -> tuple[int, int]:
        """Intervallo di byte da sostituire per ricreare l'oggetto."""
        if self.wrapper is not None:
            return self.wrapper.start, self.wrapper.end
        return self.start, self.end

    @property
    def opacity(self) -> float:
        return self.state.fill_alpha if self.kind != PATH or self.paint not in ("S", "s") else self.state.stroke_alpha


@dataclass
class ParsedPage:
    data: bytes
    ops: list[Op]
    objects: list[PageObject]
    end_ctm: pymupdf.Matrix
    pdf_to_page: pymupdf.Matrix
    names_used: dict


def rcenter(r) -> pymupdf.Point:
    """Centro di un rettangolo o di un quadrilatero."""
    if isinstance(r, pymupdf.Quad):
        r = r.rect
    return pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def pdf_to_page_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Spazio pdf -> spazio pagina del motore (non ruotata)."""
    mb = page.mediabox
    cb = page.cropbox
    return pymupdf.Matrix(1, 0, 0, -1, -cb.x0, mb.y1 - cb.y0)


def _unit_quad(m: pymupdf.Matrix) -> pymupdf.Quad:
    # angoli del quadrato unitario: (0,1) in alto a sinistra dell'immagine
    return pymupdf.Quad(pymupdf.Point(0, 1) * m, pymupdf.Point(1, 1) * m,
                        pymupdf.Point(0, 0) * m, pymupdf.Point(1, 0) * m)


def _color_tuple(operands: list) -> tuple | None:
    nums = [float(v) for v in operands if isinstance(v, (int, float))]
    return tuple(nums) if nums else None


def resources_value(doc: pymupdf.Document, page: pymupdf.Page) -> tuple[int, str]:
    """(xref, chiave) dove si trova il dizionario /Resources della pagina (anche ereditato)."""
    xref = page.xref
    seen = set()
    while xref and xref not in seen:
        seen.add(xref)
        typ, _val = doc.xref_get_key(xref, "Resources")
        if typ != "null":
            return xref, "Resources"
        typ, val = doc.xref_get_key(xref, "Parent")
        if typ != "xref":
            break
        xref = int(val.split()[0])
    return page.xref, "Resources"


def resource_entry(doc: pymupdf.Document, page: pymupdf.Page, category: str, name: str) -> tuple[str, str]:
    holder, key = resources_value(doc, page)
    typ, val = doc.xref_get_key(holder, key)
    if typ == "xref":
        rx = int(val.split()[0])
        return _get_path(doc, rx, [category, name])
    return _get_path(doc, holder, [key, category, name])


def _get_path(doc, xref: int, keys: list[str]) -> tuple[str, str]:
    """Legge una chiave annidata seguendo anche i riferimenti indiretti."""
    cur = xref
    path: list[str] = []
    for k in keys:
        path.append(k)
        typ, val = doc.xref_get_key(cur, "/".join(path))
        if typ == "xref" and k != keys[-1]:
            cur = int(val.split()[0])
            path = []
        elif typ == "null":
            return "null", "null"
    return doc.xref_get_key(cur, "/".join(path))


def resource_names(doc: pymupdf.Document, page: pymupdf.Page, category: str) -> dict[str, str]:
    """Nomi -> valore (testo PDF) di una categoria di risorse della pagina."""
    holder, key = resources_value(doc, page)
    typ, val = doc.xref_get_key(holder, key)
    if typ == "xref":
        cx, cpath = int(val.split()[0]), category
    else:
        cx, cpath = holder, f"{key}/{category}"
    typ, val = doc.xref_get_key(cx, cpath)
    if typ == "xref":
        cx, cpath = int(val.split()[0]), None
    elif typ != "dict":
        return {}
    keys = doc.xref_get_keys(cx) if cpath is None else _dict_keys(val)
    out = {}
    for k in keys:
        t, v = doc.xref_get_key(cx, k if cpath is None else f"{cpath}/{k}")
        out[k] = v
    return out


def _dict_keys(text: str) -> list[str]:
    try:
        d, _j = _read_dict(text.encode("latin-1"), text.find("<<") + 2)
        return list(d.keys())
    except Exception:
        return []


class _Resolver:
    """Risoluzione dei nomi delle risorse di una pagina (con cache)."""

    def __init__(self, doc: pymupdf.Document, page: pymupdf.Page):
        self.doc = doc
        self.page = page
        self._cache: dict = {}

    def xobject(self, name: str) -> tuple[int, str]:
        key = ("XObject", name)
        if key not in self._cache:
            typ, val = resource_entry(self.doc, self.page, "XObject", name)
            xref = int(val.split()[0]) if typ == "xref" else 0
            sub = ""
            if xref:
                sub = self.doc.xref_get_key(xref, "Subtype")[1].lstrip("/")
            self._cache[key] = (xref, sub)
        return self._cache[key]

    def extgstate_alpha(self, name: str) -> tuple[float | None, float | None]:
        key = ("ExtGState", name)
        if key not in self._cache:
            typ, val = resource_entry(self.doc, self.page, "ExtGState", name)
            ca = CA = None
            text = val
            if typ == "xref":
                text = self.doc.xref_object(int(val.split()[0]), compressed=True)
            m = re.search(r"/ca\s+([0-9.+-]+)", text or "")
            if m:
                ca = float(m.group(1))
            m = re.search(r"/CA\s+([0-9.+-]+)", text or "")
            if m:
                CA = float(m.group(1))
            self._cache[key] = (ca, CA)
        return self._cache[key]

    def form_bbox(self, xref: int) -> tuple[pymupdf.Rect, pymupdf.Matrix]:
        key = ("form", xref)
        if key not in self._cache:
            bbox = pymupdf.Rect(0, 0, 1, 1)
            mat = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
            t, v = self.doc.xref_get_key(xref, "BBox")
            nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", v)] if t == "array" else []
            if len(nums) == 4:
                bbox = pymupdf.Rect(nums)
                bbox.normalize()
            t, v = self.doc.xref_get_key(xref, "Matrix")
            nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)] if t == "array" else []
            if len(nums) == 6:
                mat = pymupdf.Matrix(*nums)
            self._cache[key] = (bbox, mat)
        return self._cache[key]

    def image_size(self, xref: int) -> tuple[int, int]:
        key = ("size", xref)
        if key not in self._cache:
            w = self.doc.xref_get_key(xref, "Width")[1]
            h = self.doc.xref_get_key(xref, "Height")[1]
            try:
                self._cache[key] = (int(float(w)), int(float(h)))
            except ValueError:
                self._cache[key] = (0, 0)
        return self._cache[key]


def page_content(doc: pymupdf.Document, page: pymupdf.Page) -> bytes:
    """Contenuto completo della pagina (gli stream di /Contents concatenati)."""
    parts = []
    for x in page.get_contents():
        try:
            parts.append(doc.xref_stream(x) or b"")
        except Exception:
            parts.append(b"")
    return b"\n".join(parts)


def parse_page(doc: pymupdf.Document, page: pymupdf.Page, data: bytes | None = None) -> ParsedPage:
    """Analizza la pagina e ne restituisce gli oggetti in ordine di disegno."""
    if data is None:
        data = page_content(doc, page)
    ops = tokenize_ops(data)
    P = pdf_to_page_matrix(page)
    res = _Resolver(doc, page)
    page_rect = page.cropbox * pymupdf.Matrix(1, 0, 0, 1, -page.cropbox.x0, -page.cropbox.y0)
    st = GState(ctm=pymupdf.Matrix(1, 0, 0, 1, 0, 0))
    stack: list[tuple[GState, int]] = []
    objects: list[PageObject] = []
    names_used: dict[str, set] = {}
    path_start: int | None = None
    path_first_op = -1
    segments: list = []        # sottotracciati correnti (spazio pagina)
    current: list = []
    cur_pt = None
    start_pt = None
    clip_pending = False
    in_text = 0

    def use(cat: str, name) -> None:
        names_used.setdefault(cat, set()).add(str(name))

    def to_page(x: float, y: float) -> pymupdf.Point:
        return pymupdf.Point(x, y) * st.ctm * P

    def num(v) -> float:
        return float(v) if isinstance(v, (int, float)) else 0.0

    for idx, op in enumerate(ops):
        name = op.name
        o = op.operands
        if name == "q":
            stack.append((st.copy(), idx))
        elif name == "Q":
            if stack:
                st, _qi = stack.pop()
        elif name == "cm" and len(o) == 6:
            st.ctm = pymupdf.Matrix(*[num(v) for v in o]) * st.ctm
        elif name in STATE_KEYS:
            st.ops[name] = data[op.start:op.end]
            if name == "w" and o:
                st.line_width = num(o[0])
        elif name in ("CS", "cs"):
            st.ops[name] = data[op.start:op.end]
            st.ops.pop("SC" if name == "CS" else "sc", None)
            if o:
                use("ColorSpace", o[0])
        elif name in ("SC", "SCN", "G", "RG", "K"):
            st.ops["SC"] = data[op.start:op.end]
            if name != "SC" and name != "SCN":
                st.ops.pop("CS", None)
            if name == "SCN" and o and isinstance(o[-1], Name):
                use("Pattern", o[-1])
        elif name in ("sc", "scn", "g", "rg", "k"):
            st.ops["sc"] = data[op.start:op.end]
            if name not in ("sc", "scn"):
                st.ops.pop("cs", None)
            if name == "scn" and o and isinstance(o[-1], Name):
                use("Pattern", o[-1])
        elif name == "gs" and o:
            st.gs.append(data[op.start:op.end])
            use("ExtGState", o[0])
            ca, CA = res.extgstate_alpha(str(o[0]))
            if ca is not None:
                st.fill_alpha = ca
            if CA is not None:
                st.stroke_alpha = CA
        elif name == "BT":
            in_text += 1
        elif name == "ET":
            in_text = max(0, in_text - 1)
        elif name == "Tf" and o:
            use("Font", o[0])
        elif name in ("BDC", "DP") and len(o) >= 2 and isinstance(o[1], Name):
            use("Properties", o[1])
        elif name in PATH_OPS:
            if path_start is None:
                path_start = op.start
                path_first_op = idx
                segments = []
                current = []
            if name == "m" and len(o) >= 2:
                if current:
                    segments.append(current)
                cur_pt = to_page(num(o[0]), num(o[1]))
                start_pt = cur_pt
                current = [("m", cur_pt)]
            elif name == "l" and len(o) >= 2:
                cur_pt = to_page(num(o[0]), num(o[1]))
                current.append(("l", cur_pt))
            elif name in ("c", "v", "y"):
                vals = [num(v) for v in o]
                if name == "c" and len(vals) >= 6:
                    p1, p2, p3 = to_page(vals[0], vals[1]), to_page(vals[2], vals[3]), to_page(vals[4], vals[5])
                elif name == "v" and len(vals) >= 4:
                    p1, p2, p3 = cur_pt or to_page(vals[0], vals[1]), to_page(vals[0], vals[1]), to_page(vals[2], vals[3])
                elif name == "y" and len(vals) >= 4:
                    p1, p2, p3 = to_page(vals[0], vals[1]), to_page(vals[2], vals[3]), to_page(vals[2], vals[3])
                else:
                    continue
                current.append(("c", p1, p2, p3))
                cur_pt = p3
            elif name == "h":
                if current:
                    current.append(("h",))
                cur_pt = start_pt
            elif name == "re" and len(o) >= 4:
                x, y, w, h = (num(v) for v in o[:4])
                if current:
                    segments.append(current)
                a, b, c, d = to_page(x, y), to_page(x + w, y), to_page(x + w, y + h), to_page(x, y + h)
                segments.append([("m", a), ("l", b), ("l", c), ("l", d), ("h",)])
                current = []
                cur_pt = start_pt = a
        elif name in ("W", "W*"):
            clip_pending = True
        elif name in PAINT_OPS:
            if current:
                segments.append(current)
                current = []
            pts = [p for seg in segments for el in seg for p in el[1:]]
            if clip_pending and pts:
                r = pymupdf.Rect(pts[0], pts[0])
                for p in pts[1:]:
                    r |= p
                st.clip = r if st.clip is None else (st.clip & r)
            if name != "n" and not clip_pending and path_start is not None and pts and not in_text:
                r = pymupdf.Rect(pts[0], pts[0])
                for p in pts[1:]:
                    r |= p
                if name in ("S", "s", "B", "B*", "b", "b*"):
                    scale = (abs(st.ctm.a * st.ctm.d - st.ctm.b * st.ctm.c)) ** 0.5
                    lw = st.line_width * scale / 2
                    r = pymupdf.Rect(r.x0 - lw, r.y0 - lw, r.x1 + lw, r.y1 + lw)
                quad = r.quad
                obj = PageObject(len(objects), PATH, path_start, op.end, pymupdf.Matrix(st.ctm), quad,
                                 _clip_rect(r, st.clip), st.copy(), paint=name, segments=segments)
                obj.op_range = (path_first_op, idx)
                _set_colors(obj, st)
                objects.append(obj)
            path_start = None
            clip_pending = False
            segments = []
        elif name == "Do" and o and not in_text:
            use("XObject", o[0])
            xref, sub = res.xobject(str(o[0]))
            if sub == "Image":
                quad = _unit_quad(st.ctm) * P
                obj = PageObject(len(objects), IMAGE, op.start, op.end, pymupdf.Matrix(st.ctm), quad,
                                 _clip_rect(quad.rect, st.clip), st.copy(), name=str(o[0]), xref=xref)
                obj.width, obj.height = res.image_size(xref)
            elif sub == "Form":
                bbox, fm = res.form_bbox(xref)
                q = bbox.quad * fm * st.ctm * P
                obj = PageObject(len(objects), FORM, op.start, op.end, pymupdf.Matrix(st.ctm), q,
                                 _clip_rect(q.rect, st.clip), st.copy(), name=str(o[0]), xref=xref)
            else:
                continue
            obj.op_range = (idx, idx)
            objects.append(obj)
        elif name == "BI" and not in_text:
            d = op.data["dict"] if op.data else {}
            cs = d.get("CS", d.get("ColorSpace"))
            if isinstance(cs, Name) and cs not in ("DeviceRGB", "DeviceGray", "DeviceCMYK", "RGB", "G", "CMYK", "I", "Indexed"):
                use("ColorSpace", cs)
            quad = _unit_quad(st.ctm) * P
            obj = PageObject(len(objects), IMAGE, op.start, op.end, pymupdf.Matrix(st.ctm), quad,
                             _clip_rect(quad.rect, st.clip), st.copy(), inline=True)
            try:
                obj.width = int(d.get("W", d.get("Width", 0)))
                obj.height = int(d.get("H", d.get("Height", 0)))
            except (TypeError, ValueError):
                pass
            obj.op_range = (idx, idx)
            objects.append(obj)
        elif name == "sh" and o and not in_text:
            use("Shading", o[0])
            r = pymupdf.Rect(st.clip) if st.clip is not None else pymupdf.Rect(page_rect)
            obj = PageObject(len(objects), SHADING, op.start, op.end, pymupdf.Matrix(st.ctm), r.quad,
                             r, st.copy(), name=str(o[0]))
            obj.op_range = (idx, idx)
            objects.append(obj)
    for obj in objects:
        obj.wrapper = _find_wrapper(data, ops, obj, P)
        obj.hidden = obj.opacity <= 0 or obj.rect.is_empty
    return ParsedPage(data, ops, objects, pymupdf.Matrix(st.ctm), P, names_used)


def _clip_rect(r: pymupdf.Rect, clip: pymupdf.Rect | None) -> pymupdf.Rect:
    r = pymupdf.Rect(r)
    r.normalize()
    if clip is None:
        return r
    out = r & clip
    return out


def _set_colors(obj: PageObject, st: GState) -> None:
    for key, attr in (("SC", "stroke_color"), ("sc", "fill_color")):
        raw = st.ops.get(key)
        if raw:
            try:
                ops = tokenize_ops(raw)
                if ops:
                    setattr(obj, attr, _color_tuple(ops[0].operands))
            except Exception:
                pass
        else:
            setattr(obj, attr, (0.0,))


def _find_wrapper(data: bytes, ops: list[Op], obj: PageObject, P: pymupdf.Matrix) -> Wrapper | None:
    """Riconosce `q [cm] [gs] [x y w h re W n] OGGETTO Q` attorno all'oggetto."""
    first, last = obj.op_range
    if last + 1 >= len(ops) or ops[last + 1].name != "Q":
        return None
    i = first - 1
    clip_unit = None
    if i >= 2 and ops[i].name == "n" and ops[i - 1].name in ("W",) and ops[i - 2].name == "re":
        re_op = ops[i - 2]
        if len(re_op.operands) == 4 and all(isinstance(v, (int, float)) for v in re_op.operands):
            clip_unit = tuple(float(v) for v in re_op.operands)
            i -= 3
        else:
            return None
    gs_name = None
    if i >= 0 and ops[i].name == "gs" and ops[i].operands:
        gs_name = str(ops[i].operands[0])
        i -= 1
    cm = None
    if i >= 0 and ops[i].name == "cm" and len(ops[i].operands) == 6:
        cm = pymupdf.Matrix(*[float(v) for v in ops[i].operands])
        i -= 1
    if i < 0 or ops[i].name != "q":
        return None
    outer = obj.ctm if cm is None else ~cm * obj.ctm
    if clip_unit is not None and cm is None:
        return None
    return Wrapper(ops[i].start, ops[last + 1].end, outer, gs_name, clip_unit)


def ctm_at(parsed: ParsedPage, offset: int) -> pymupdf.Matrix:
    """Matrice corrente subito prima della posizione `offset` del flusso."""
    ctm = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    stack = []
    for op in parsed.ops:
        if op.start >= offset:
            break
        if op.name == "q":
            stack.append(pymupdf.Matrix(ctm))
        elif op.name == "Q":
            if stack:
                ctm = stack.pop()
        elif op.name == "cm" and len(op.operands) == 6:
            try:
                ctm = pymupdf.Matrix(*[float(v) for v in op.operands]) * ctm
            except (TypeError, ValueError):
                pass
    return ctm


def text_at_depth_zero(parsed: ParsedPage, offset: int) -> bool:
    """True se `offset` è fuori da blocchi di testo (BT…ET)."""
    depth = 0
    for op in parsed.ops:
        if op.start >= offset:
            break
        if op.name == "BT":
            depth += 1
        elif op.name == "ET":
            depth -= 1
    return depth <= 0
