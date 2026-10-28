"""Collegamenti: normalizzazione delle destinazioni, creazione, modifica,
eliminazione e rilevamento degli URL nel testo (logica pura)."""

from __future__ import annotations

import os
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit, urlunsplit

import pymupdf

from .content import fmt_num, rcenter

ALLOWED_SCHEMES = ("http", "https", "mailto", "ftp", "tel")
MAX_URI = 2000

# tipi di destinazione
URI, PAGE, NAMED, FILE, LAUNCH, OTHER = "uri", "page", "named", "file", "launch", "other"
FITS = ("XYZ", "Fit", "FitH", "FitV", "FitR", "FitB", "FitBH", "FitBV")
HIGHLIGHT = ("N", "I", "O", "P")
STYLES = ("S", "D", "U")


class LinkError(Exception):
    pass


# ================================================================ URI
_DOMAIN_RE = re.compile(r"^(?:[\w\-]+\.)+[^\W\d_]{2,}(?::\d+)?(?:[/?#].*)?$", re.UNICODE)
_EMAIL_RE = re.compile(r"^[\w.!#$%&'*+/=?^`{|}~-]+@(?:[\w-]+\.)+[^\W\d_]{2,}$", re.UNICODE)


@dataclass
class UriCheck:
    uri: str | None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    suggest_file: bool = False


def _idna_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        parts = []
        for label in host.split("."):
            try:
                parts.append(label.encode("idna").decode("ascii") if label else label)
            except UnicodeError:
                parts.append(label)
        return ".".join(parts)


def _pct(s: str, safe: str) -> str:
    return quote(s, safe=safe + "%")


def normalize_uri(text: str) -> UriCheck:
    """Normalizza un indirizzo web/e-mail. Restituisce l'URI in ASCII o un errore."""
    t = (text or "").strip()
    if not t:
        return UriCheck(None, "Inserisci un indirizzo.")
    low = t.lower()
    scheme = low.split(":", 1)[0] if re.match(r"^[a-z][a-z0-9+.\-]*:", low) else ""
    if scheme in ("javascript", "data", "vbscript"):
        return UriCheck(None, "Schema non ammesso.")
    if scheme == "file":
        return UriCheck(None, "Per i file usa il tipo «File».", suggest_file=True)
    if not scheme:
        if _EMAIL_RE.match(t):
            t = "mailto:" + t
            scheme = "mailto"
        elif _DOMAIN_RE.match(t) or low.startswith("www."):
            t = "https://" + t
            scheme = "https"
        else:
            return UriCheck(None, "Indirizzo non valido.")
    if scheme not in ALLOWED_SCHEMES:
        return UriCheck(None, "Schema non ammesso.")
    warnings: list[str] = []
    if scheme == "mailto":
        addr, _q, query = t[7:].partition("?")
        if not _EMAIL_RE.match(addr):
            return UriCheck(None, "Indirizzo e-mail non valido.")
        local, _a, dom = addr.partition("@")
        uri = "mailto:" + _pct(local, "!#$&'*+/=?^_`{|}~.-") + "@" + _idna_host(dom)
        if query:
            uri += "?" + _pct(query, "=&")
        return UriCheck(uri, None, warnings)
    if scheme == "tel":
        num = re.sub(r"[\s().-]", "", t[4:])
        if not re.match(r"^\+?\d{3,}$", num):
            return UriCheck(None, "Numero di telefono non valido.")
        return UriCheck("tel:" + num)
    parts = urlsplit(t)
    host = parts.hostname or ""
    if not host:
        return UriCheck(None, "Indirizzo incompleto.")
    if parts.username or parts.password:
        warnings.append("L'indirizzo contiene credenziali (utente o password).")
    netloc = _idna_host(host)
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username:
        user = _pct(parts.username, "")
        if parts.password:
            user += ":" + _pct(parts.password, "")
        netloc = user + "@" + netloc
    path = _pct(parts.path, "/:@!$&'()*+,;=-._~")
    query = _pct(parts.query, "/:@!$&'()*+,;=-._~?")
    frag = _pct(parts.fragment, "/:@!$&'()*+,;=-._~?")
    uri = urlunsplit((parts.scheme.lower(), netloc, path, query, frag))
    if len(uri) > MAX_URI:
        warnings.append("Indirizzo molto lungo: alcuni lettori potrebbero non gestirlo.")
    return UriCheck(uri, None, warnings)


def uri_host(uri: str) -> str:
    try:
        return (urlsplit(uri).hostname or "").lower()
    except ValueError:
        return ""


# ================================================================ destinazioni
@dataclass
class LinkTarget:
    kind: str = URI
    uri: str = ""
    page: int = 0                  # indice 0-based
    fit: str = "XYZ"
    left: float | None = None
    top: float | None = None
    zoom: float | None = None
    name: str = ""                 # destinazione con nome
    file: str = ""
    new_window: bool = False
    raw: str = ""                  # azione non gestita (sola lettura)
    new_dest: tuple | None = None  # (pagina, alto, zoom): crea la destinazione con nome prima del collegamento

    def describe(self) -> str:
        if self.kind == URI:
            return self.uri
        if self.kind == PAGE:
            return f"Pagina {self.page + 1}"
        if self.kind == NAMED:
            return f"Destinazione «{self.name}»"
        if self.kind == FILE:
            return f"{self.file}" + (f" (pagina {self.page + 1})" if self.page >= 0 else "")
        if self.kind == LAUNCH:
            return f"{self.file}"
        return "Azione non modificabile"


@dataclass
class LinkStyle:
    visible: bool = False
    color: tuple = (0.0, 0.0, 1.0)
    width: float = 1.0
    style: str = "S"               # S continuo, D tratteggiato, U sottolineato
    highlight: str = "I"


@dataclass
class LinkInfo:
    xref: int
    page: int
    rect: pymupdf.Rect
    target: LinkTarget
    style: LinkStyle
    description: str = ""
    quads: list = field(default_factory=list)
    locked: bool = False


def _pdf_str(s: str) -> str:
    """Stringa PDF: ASCII tra parentesi, altrimenti UTF-16BE esadecimale."""
    try:
        s.encode("ascii")
        return "(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", "\\r").replace("\n", "\\n") + ")"
    except UnicodeEncodeError:
        return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def _pdf_date() -> str:
    return time.strftime("D:%Y%m%d%H%M%SZ", time.gmtime())


def _dest_array(pdf: pymupdf.Document, t: LinkTarget, remote: bool = False) -> str:
    if remote:
        head = str(max(0, t.page))
    else:
        if not 0 <= t.page < pdf.page_count:
            raise LinkError("La pagina indicata non esiste.")
        head = f"{pdf.page_xref(t.page)} 0 R"
    page_h = 0.0
    P = None
    if not remote:
        page = pdf[t.page]
        from .content import pdf_to_page_matrix
        P = ~pdf_to_page_matrix(page)
        page_h = page.rect.height

    def pdf_pt(x, y):
        if P is None:
            return x, y
        p = pymupdf.Point(x or 0, y or 0) * P
        return p.x, p.y

    def n(v):
        return "null" if v is None else fmt_num(v)
    fit = t.fit if t.fit in FITS else "XYZ"
    if fit == "XYZ":
        x, y = pdf_pt(t.left, t.top)
        return f"[{head} /XYZ {n(x if t.left is not None else None)} {n(y if t.top is not None else None)} {n(t.zoom)}]"
    if fit in ("FitH", "FitBH"):
        _x, y = pdf_pt(0, t.top)
        return f"[{head} /{fit} {n(y if t.top is not None else None)}]"
    if fit in ("FitV", "FitBV"):
        x, _y = pdf_pt(t.left, 0)
        return f"[{head} /{fit} {n(x if t.left is not None else None)}]"
    del page_h
    return f"[{head} /{fit}]"


def named_destinations(pdf: pymupdf.Document) -> dict:
    try:
        if hasattr(pdf, "_resolved_names"):
            del pdf._resolved_names      # cache del motore: i nomi possono essere cambiati
    except Exception:
        pass
    try:
        return pdf.resolve_names() or {}
    except Exception:
        return {}


def add_named_destination(pdf: pymupdf.Document, name: str, page: int, top: float | None = None,
                          zoom: float | None = None) -> None:
    """Crea una destinazione con nome (OBJ-36) che punta alla pagina e alla posizione indicate."""
    name = (name or "").strip()
    if not name:
        raise LinkError("Indica il nome della destinazione.")
    if name in named_destinations(pdf):
        raise LinkError("Il nome della destinazione è già usato: scegline un altro.")
    dest = _dest_array(pdf, LinkTarget(kind=PAGE, page=page, fit="XYZ", left=0.0 if top is not None else None,
                                       top=top, zoom=zoom))
    cat = pdf.pdf_catalog()
    typ, val = pdf.xref_get_key(cat, "Names/Dests/Names")
    if typ == "array":
        # albero dei nomi piatto: si aggiunge mantenendo l'ordine
        inner = val.strip()[1:-1]
        pdf.xref_set_key(cat, "Names/Dests/Names", "[" + inner + " " + _pdf_str(name) + " " + dest + "]")
    else:
        typ, val = pdf.xref_get_key(cat, "Dests")
        if typ == "xref":
            x = int(val.split()[0])
        else:
            x = pdf.get_new_xref()
            pdf.update_object(x, val if typ == "dict" else "<<>>")
            pdf.xref_set_key(cat, "Dests", f"{x} 0 R")
        from .content import fmt_name
        pdf.xref_set_key(x, fmt_name(name).decode()[1:], dest)
    named_destinations(pdf)


def relative_path(target: str, doc_path: str | None) -> str:
    if not doc_path:
        return target
    try:
        rel = os.path.relpath(target, os.path.dirname(os.path.abspath(doc_path)))
    except ValueError:
        return target
    return rel.replace(os.sep, "/")


def action_string(pdf: pymupdf.Document, t: LinkTarget) -> tuple[str | None, str | None]:
    """(/A, /Dest) da scrivere nell'annotazione."""
    if t.kind == URI:
        chk = normalize_uri(t.uri)
        if chk.uri is None:
            raise LinkError(chk.error or "Indirizzo non valido.")
        return f"<</S/URI/URI{_pdf_str(chk.uri)}>>", None
    if t.kind == PAGE:
        return None, _dest_array(pdf, t)
    if t.kind == NAMED:
        if not t.name:
            raise LinkError("Indica il nome della destinazione.")
        return f"<</S/GoTo/D{_pdf_str(t.name)}>>", None
    if t.kind == FILE:
        if not t.file:
            raise LinkError("Scegli il file.")
        spec = f"<</Type/Filespec/F{_pdf_str(_ascii_path(t.file))}/UF{_pdf_str(t.file)}>>"
        if t.file.lower().endswith(".pdf"):
            d = f"[{max(0, t.page)} /{t.fit if t.fit in ('Fit', 'FitH', 'FitV', 'XYZ') else 'Fit'}]" if not t.name else _pdf_str(t.name)
            nw = "/NewWindow true" if t.new_window else ""
            return f"<</S/GoToR/F{spec}/D{d}{nw}>>", None
        return f"<</S/Launch/F{spec}>>", None
    if t.kind == LAUNCH:
        spec = f"<</Type/Filespec/F{_pdf_str(_ascii_path(t.file))}/UF{_pdf_str(t.file)}>>"
        return f"<</S/Launch/F{spec}>>", None
    raise LinkError("Tipo di destinazione non modificabile.")


def _ascii_path(p: str) -> str:
    try:
        p.encode("ascii")
        return p
    except UnicodeEncodeError:
        return unicodedata.normalize("NFKD", p).encode("ascii", "ignore").decode() or "file"


def _appearance(pdf: pymupdf.Document, rect_pdf: pymupdf.Rect, st: LinkStyle) -> int:
    w = st.width if st.visible else 0
    bw = rect_pdf.width
    bh = rect_pdf.height
    body = ""
    if w > 0:
        r, g, b = (st.color + (0, 0, 0))[:3]
        if st.style == "U":
            body = f"{fmt_num(r)} {fmt_num(g)} {fmt_num(b)} RG {fmt_num(w)} w 0 {fmt_num(w / 2)} m {fmt_num(bw)} {fmt_num(w / 2)} l S"
        else:
            dash = f"[{fmt_num(3 * w)} {fmt_num(2 * w)}] 0 d " if st.style == "D" else ""
            body = (f"{fmt_num(r)} {fmt_num(g)} {fmt_num(b)} RG {fmt_num(w)} w {dash}"
                    f"{fmt_num(w / 2)} {fmt_num(w / 2)} {fmt_num(max(0, bw - w))} {fmt_num(max(0, bh - w))} re S")
    xref = pdf.get_new_xref()
    pdf.update_object(xref, f"<</Type/XObject/Subtype/Form/BBox[0 0 {fmt_num(bw)} {fmt_num(bh)}]/Resources<<>>>>")
    pdf.update_stream(xref, body.encode(), new=True)
    return xref


def _quads_pdf(quads, P_inv: pymupdf.Matrix) -> str:
    vals = []
    for q in quads:
        q = pymupdf.Quad(q)
        for p in (q.ul, q.ur, q.ll, q.lr):
            pp = p * P_inv
            vals += [fmt_num(round(pp.x, 3)), fmt_num(round(pp.y, 3))]
    return "[" + " ".join(vals) + "]"


def write_link(pdf: pymupdf.Document, page_index: int, xref: int, rect: pymupdf.Rect, target: LinkTarget | None,
               style: LinkStyle, description: str = "", quads=None) -> None:
    """Scrive rettangolo, destinazione, aspetto e descrizione di un'annotazione /Link."""
    from .content import pdf_to_page_matrix
    page = pdf[page_index]
    P_inv = ~pdf_to_page_matrix(page)
    r = pymupdf.Rect(rect)
    r.normalize()
    rp = r * P_inv
    rp.normalize()
    pdf.xref_set_key(xref, "Rect", f"[{fmt_num(round(rp.x0, 3))} {fmt_num(round(rp.y0, 3))} {fmt_num(round(rp.x1, 3))} {fmt_num(round(rp.y1, 3))}]")
    if target is not None and target.kind == NAMED and target.new_dest is not None:
        page, top, zoom = target.new_dest
        add_named_destination(pdf, target.name, page, top, zoom)
        target.new_dest = None
    if target is not None and target.kind != OTHER:
        a, d = action_string(pdf, target)
        pdf.xref_set_key(xref, "A", a or "null")
        pdf.xref_set_key(xref, "Dest", d or "null")
    w = style.width if style.visible else 0
    pdf.xref_set_key(xref, "Border", f"[0 0 {fmt_num(w)}]")
    pdf.xref_set_key(xref, "BS", f"<</W {fmt_num(w)}/S/{style.style if style.style in STYLES else 'S'}>>")
    c = style.color or (0, 0, 1)
    pdf.xref_set_key(xref, "C", "[" + " ".join(fmt_num(v) for v in c[:3]) + "]")
    pdf.xref_set_key(xref, "H", f"/{style.highlight if style.highlight in HIGHLIGHT else 'I'}")
    pdf.xref_set_key(xref, "Contents", _pdf_str(description) if description else "null")
    pdf.xref_set_key(xref, "M", _pdf_str(_pdf_date()))
    flags = pdf.xref_get_key(xref, "F")
    try:
        f = int(flags[1]) if flags[0] == "int" else 0
    except ValueError:
        f = 0
    pdf.xref_set_key(xref, "F", str(f | 4))
    if pdf.xref_get_key(xref, "NM")[0] == "null":
        pdf.xref_set_key(xref, "NM", _pdf_str("fo-" + secrets.token_hex(8)))
    if quads is not None:
        pdf.xref_set_key(xref, "QuadPoints", _quads_pdf(quads, P_inv) if quads else "null")
    ap = _appearance(pdf, rp, style)
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
    _refresh(pdf, page_index)


def _refresh(pdf: pymupdf.Document, page_index: int) -> None:
    """Ricarica la pagina perché l'elenco dei collegamenti del motore si aggiorni."""
    try:
        pdf.reload_page(pdf[page_index])
    except Exception:
        pass


def create_link(pdf: pymupdf.Document, page_index: int, rect: pymupdf.Rect, target: LinkTarget,
                style: LinkStyle | None = None, description: str = "", quads=None) -> int:
    """Crea un collegamento; restituisce l'xref dell'annotazione."""
    r = pymupdf.Rect(rect)
    r.normalize()
    if r.width < 4 or r.height < 4:
        raise LinkError("Il rettangolo del collegamento è troppo piccolo.")
    action_string(pdf, target)  # valida prima di modificare
    page = pdf[page_index]
    before = set(_annot_xrefs(pdf, page))
    page.insert_link({"kind": pymupdf.LINK_URI, "uri": "https://localhost/", "from": r})
    page = pdf[page_index]
    new = [x for x in _annot_xrefs(pdf, page) if x not in before]
    if not new:
        raise LinkError("Impossibile creare il collegamento.")
    xref = new[-1]
    write_link(pdf, page_index, xref, r, target, style or LinkStyle(), description, quads)
    return xref


def link_xrefs(pdf: pymupdf.Document, page_index: int) -> list[int]:
    try:
        page = pdf[page_index]
    except Exception:
        return []
    out = []
    n = pdf.xref_length()
    for annot_xref in _annot_xrefs(pdf, page):
        if not 0 < annot_xref < n:
            continue   # PDF danneggiato: riferimento fuori dalla tabella
        try:
            if pdf.xref_get_key(annot_xref, "Subtype")[1] == "/Link":
                out.append(annot_xref)
        except Exception:
            continue
    return out


def _annot_xrefs(pdf: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    """xref delle annotazioni della pagina; [] se la pagina è danneggiata."""
    try:
        xref = page.xref
        if not 0 < xref < pdf.xref_length():
            return []
        typ, val = pdf.xref_get_key(xref, "Annots")
        if typ == "xref":
            val = pdf.xref_object(int(val.split()[0]), compressed=True)
        elif typ != "array":
            return []
    except Exception:
        return []
    return [int(x) for x in re.findall(r"(\d+)\s+0\s+R", val)]


def _nums(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", text)]


def _parse_dest(pdf: pymupdf.Document, dest: str, t: LinkTarget) -> None:
    m = re.match(r"\[\s*(\d+)\s+0\s+R\s*/(\w+)(.*)\]", dest.strip(), re.S)
    if m:
        pxref = int(m.group(1))
        for i in range(pdf.page_count):
            if pdf.page_xref(i) == pxref:
                t.page = i
                break
        else:
            t.page = -1
        t.kind = PAGE
        t.fit = m.group(2)
        rest = m.group(3).split()
        vals = [None if v == "null" else float(v) for v in rest if re.match(r"^(null|[-+]?[\d.]+)$", v)]
        if t.page >= 0:
            from .content import pdf_to_page_matrix
            P = pdf_to_page_matrix(pdf[t.page])
        else:
            P = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
        if t.fit == "XYZ":
            x = vals[0] if len(vals) > 0 else None
            y = vals[1] if len(vals) > 1 else None
            t.zoom = vals[2] if len(vals) > 2 and vals[2] else None
            p = pymupdf.Point(x or 0, y or 0) * P
            t.left = p.x if x is not None else None
            t.top = p.y if y is not None else None
        elif t.fit in ("FitH", "FitBH") and vals:
            t.top = (pymupdf.Point(0, vals[0] or 0) * P).y if vals[0] is not None else None
        elif t.fit in ("FitV", "FitBV") and vals:
            t.left = (pymupdf.Point(vals[0] or 0, 0) * P).x if vals[0] is not None else None
        return
    m = re.match(r"^\((.*)\)$|^/(\S+)$", dest.strip(), re.S)
    if m:
        t.kind = NAMED
        t.name = m.group(1) if m.group(1) is not None else m.group(2)
        return
    t.kind = OTHER
    t.raw = dest


def _decode_pdf_string(s: str) -> str:
    s = s.strip()
    if s.startswith("<") and not s.startswith("<<"):
        h = re.sub(r"[^0-9A-Fa-f]", "", s)
        b = bytes.fromhex(h + ("0" if len(h) % 2 else ""))
        if b.startswith(b"\xfe\xff"):
            return b[2:].decode("utf-16-be", "replace")
        return b.decode("latin-1")
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r", "t": "\t"}.get(m.group(1), m.group(1)), inner)
    return s


def read_link(pdf: pymupdf.Document, page_index: int, xref: int) -> LinkInfo:
    from .content import pdf_to_page_matrix
    page = pdf[page_index]
    P = pdf_to_page_matrix(page)
    nums = _nums(pdf.xref_get_key(xref, "Rect")[1])
    rect = pymupdf.Rect(nums[:4]) * P if len(nums) >= 4 else pymupdf.Rect()
    rect.normalize()
    t = LinkTarget()
    typ, a = pdf.xref_get_key(xref, "A")
    if typ == "xref":
        a = pdf.xref_object(int(a.split()[0]), compressed=True)
        typ = "dict"
    if typ == "dict":
        s = re.search(r"/S\s*/(\w+)", a)
        kind = s.group(1) if s else ""
        if "/Next" in a:
            t.kind, t.raw = OTHER, a
        elif kind == "URI":
            m = re.search(r"/URI\s*(\((?:\\.|[^\\)])*\)|<[^>]*>)", a, re.S)
            t.kind = URI
            t.uri = _decode_pdf_string(m.group(1)) if m else ""
        elif kind == "GoTo":
            m = re.search(r"/D\s*(\[.*?\]|\((?:\\.|[^\\)])*\)|/\S+)", a, re.S)
            _parse_dest(pdf, m.group(1) if m else "", t)
        elif kind in ("GoToR", "Launch"):
            m = re.search(r"/UF\s*(\((?:\\.|[^\\)])*\)|<[^>]*>)", a) or re.search(r"/F\s*(\((?:\\.|[^\\)])*\)|<[^>]*>)", a)
            t.file = _decode_pdf_string(m.group(1)) if m else ""
            t.kind = FILE if kind == "GoToR" or t.file.lower().endswith(".pdf") else LAUNCH
            if kind == "Launch":
                t.kind = LAUNCH if not t.file.lower().endswith(".pdf") else FILE
            m = re.search(r"/D\s*\[\s*(\d+)\s*/(\w+)", a)
            if m:
                t.page = int(m.group(1))
                t.fit = m.group(2)
            t.new_window = "/NewWindow true" in a
        else:
            t.kind, t.raw = OTHER, a
    else:
        typ, d = pdf.xref_get_key(xref, "Dest")
        if typ == "string":
            t.kind = NAMED
            t.name = d
        elif typ == "name":
            t.kind = NAMED
            t.name = d.lstrip("/")
        elif typ != "null":
            _parse_dest(pdf, d, t)
        else:
            t.kind = OTHER
    st = LinkStyle()
    bs_w = pdf.xref_get_key(xref, "BS/W")
    border = pdf.xref_get_key(xref, "Border")
    if bs_w[0] != "null":
        try:
            st.width = float(bs_w[1])
        except ValueError:
            st.width = 1.0
    elif border[0] == "array":
        b = _nums(border[1])
        st.width = b[2] if len(b) >= 3 else 1.0
    else:
        st.width = 1.0  # predefinito del formato
    st.visible = st.width > 0
    if not st.visible:
        st.width = 1.0
    s = pdf.xref_get_key(xref, "BS/S")
    if s[0] == "name" and s[1].lstrip("/") in STYLES:
        st.style = s[1].lstrip("/")
    c = _nums(pdf.xref_get_key(xref, "C")[1]) if pdf.xref_get_key(xref, "C")[0] == "array" else []
    if len(c) >= 3:
        st.color = tuple(c[:3])
    elif len(c) == 1:
        st.color = (c[0], c[0], c[0])
    h = pdf.xref_get_key(xref, "H")
    if h[0] == "name" and h[1].lstrip("/") in HIGHLIGHT:
        st.highlight = h[1].lstrip("/")
    desc = pdf.xref_get_key(xref, "Contents")
    description = _decode_pdf_string(desc[1]) if desc[0] == "string" else ""
    if desc[0] == "string":
        description = desc[1]
    quads = []
    qp = pdf.xref_get_key(xref, "QuadPoints")
    if qp[0] == "array":
        v = _nums(qp[1])
        for k in range(0, len(v) - 7, 8):
            pts = [pymupdf.Point(v[k + 2 * j], v[k + 2 * j + 1]) * P for j in range(4)]
            quads.append(pymupdf.Quad(pts[0], pts[1], pts[2], pts[3]))
    f = pdf.xref_get_key(xref, "F")
    locked = False
    try:
        locked = f[0] == "int" and bool(int(f[1]) & 128)
    except ValueError:
        pass
    return LinkInfo(xref, page_index, rect, t, st, description, quads, locked)


def page_links(pdf: pymupdf.Document, page_index: int) -> list[LinkInfo]:
    """Collegamenti della pagina (lista vuota se la pagina è danneggiata)."""
    out = []
    for x in link_xrefs(pdf, page_index):
        try:
            out.append(read_link(pdf, page_index, x))
        except Exception:
            continue
    return out


def update_link(pdf: pymupdf.Document, page_index: int, xref: int, *, rect: pymupdf.Rect | None = None,
                target: LinkTarget | None = None, style: LinkStyle | None = None,
                description: str | None = None, move: tuple[float, float] | None = None) -> None:
    """Modifica un collegamento esistente (solo i campi indicati)."""
    info = read_link(pdf, page_index, xref)
    quads = None
    new_rect = info.rect if rect is None else pymupdf.Rect(rect)
    if move is not None:
        dx, dy = move
        new_rect = pymupdf.Rect(info.rect.x0 + dx, info.rect.y0 + dy, info.rect.x1 + dx, info.rect.y1 + dy)
        if info.quads:
            quads = [pymupdf.Quad(q.ul + (dx, dy), q.ur + (dx, dy), q.ll + (dx, dy), q.lr + (dx, dy)) for q in info.quads]
    elif rect is not None and info.quads:
        quads = []  # ridimensionato: diventa rettangolare
    tgt = target
    if tgt is not None and info.target.kind == PAGE and tgt == info.target:
        tgt = None  # destinazione invariata: /Dest resta com'è
    if tgt is None:
        # conserva l'azione originale byte per byte
        write_link(pdf, page_index, xref, new_rect, None, style or info.style,
                   info.description if description is None else description, quads)
    else:
        write_link(pdf, page_index, xref, new_rect, tgt, style or info.style,
                   info.description if description is None else description, quads)


def delete_links(pdf: pymupdf.Document, page_index: int, xrefs) -> int:
    page = pdf[page_index]
    count = 0
    wanted = set(xrefs)
    for lk in page.get_links():
        if lk.get("xref") in wanted:
            page.delete_link(lk)
            page = pdf[page_index]
            count += 1
    _refresh(pdf, page_index)
    return count


def link_category(t: LinkTarget) -> str:
    if t.kind == URI:
        return "web"
    if t.kind in (PAGE, NAMED):
        return "internal"
    if t.kind in (FILE, LAUNCH):
        return "file"
    return "other"


def delete_links_in(pdf: pymupdf.Document, pages, categories: set[str], include_locked: bool = False) -> int:
    total = 0
    for i in pages:
        xs = [lk.xref for lk in page_links(pdf, i)
              if link_category(lk.target) in categories and (include_locked or not lk.locked)]
        if xs:
            total += delete_links(pdf, i, xs)
    return total


def count_links_in(pdf: pymupdf.Document, pages, categories: set[str]) -> int:
    return sum(1 for i in pages for lk in page_links(pdf, i) if link_category(lk.target) in categories)


# ================================================================ rilevamento URL
_URL_RE = re.compile(
    r"(?P<url>(?:(?:https?|ftp)://|www\.)[^\s<>\"'«»]+)"
    r"|(?P<mail>[\w.!#$%&'*+/=?^`{|}~-]+@(?:[\w-]+\.)+[^\W\d_]{2,})",
    re.UNICODE,
)
_TRAIL = ".,;:!?"


def clean_url_match(s: str) -> str:
    """Toglie punteggiatura finale e parentesi di chiusura non bilanciate."""
    changed = True
    while s and changed:
        changed = False
        if s[-1] in _TRAIL:
            s = s[:-1]
            changed = True
        for o, c in ("()", "[]", "{}"):
            if s.endswith(c) and s.count(c) > s.count(o):
                s = s[:-1]
                changed = True
        if s and s[-1] in "\"'»”’":
            s = s[:-1]
            changed = True
    return s


@dataclass
class FoundUrl:
    page: int
    text: str
    uri: str
    quads: list
    rect: pymupdf.Rect


def find_urls_in_text(text: str, web: bool = True, email: bool = True) -> list[tuple[int, str, str]]:
    """(posizione, testo, destinazione) degli URL in una stringa."""
    out = []
    for m in _URL_RE.finditer(text):
        if m.group("url") and web:
            raw = clean_url_match(m.group("url"))
            if len(raw) < 5:
                continue
            chk = normalize_uri(raw)
            if chk.uri:
                out.append((m.start(), raw, chk.uri))
        elif m.group("mail") and email:
            raw = clean_url_match(m.group("mail"))
            chk = normalize_uri(raw)
            if chk.uri:
                out.append((m.start(), raw, chk.uri))
    return out


def detect_urls(pdf: pymupdf.Document, page_index: int, web: bool = True, email: bool = True) -> list[FoundUrl]:
    """URL ed e-mail scritti nel testo della pagina, esclusi quelli già collegati."""
    page = pdf[page_index]
    existing = [lk.rect for lk in page_links(pdf, page_index)]
    words = page.get_text("words")
    out: list[FoundUrl] = []
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    keys = sorted(lines)
    # ricongiunge le righe spezzate: riga che termina con un URL senza spazio + riga successiva
    for li, key in enumerate(keys):
        ws = lines[key]
        for k, w in enumerate(ws):
            word = w[4]
            for _pos, raw, uri in find_urls_in_text(word, web, email):
                quads = []
                full = raw
                full_uri = uri
                r = pymupdf.Rect(w[:4])
                # URL che prosegue alla riga dopo (ultima parola della riga, senza punteggiatura finale)
                if (k == len(ws) - 1 and word.endswith(raw) and raw[-1] in "/-_=&?" and li + 1 < len(keys)):
                    nxt = lines[keys[li + 1]][0]
                    cont = clean_url_match(nxt[4])
                    if cont and not cont[0].isupper():
                        full = raw + cont
                        chk = normalize_uri(full)
                        if chk.uri:
                            full_uri = chk.uri
                            r2 = pymupdf.Rect(nxt[:4])
                            quads.append(r2.quad)
                hits = page.search_for(raw, clip=pymupdf.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1), quads=True)
                q0 = hits[0] if hits else r.quad
                quads.insert(0, q0)
                rect = pymupdf.Rect(q0.rect)
                for q in quads[1:]:
                    rect |= q.rect
                center = rcenter(q0.rect)
                if any(e.contains(center) for e in existing):
                    continue
                out.append(FoundUrl(page_index, full, full_uri, quads, rect))
    return out


def create_links_for(pdf: pymupdf.Document, found: list[FoundUrl], style: LinkStyle | None = None) -> int:
    n = 0
    for f in found:
        r = pymupdf.Rect(f.rect)
        if r.width < 4:
            r.x1 = r.x0 + 4
        if r.height < 4:
            r.y1 = r.y0 + 4
        create_link(pdf, f.page, r, LinkTarget(kind=URI, uri=f.uri), style or LinkStyle(visible=False),
                    description=f.text, quads=f.quads)
        n += 1
    return n


def suggest_from_text(text: str) -> LinkTarget | None:
    t = text.strip()
    if not t or " " in t:
        return None
    found = find_urls_in_text(t)
    if found and found[0][1] == clean_url_match(t):
        return LinkTarget(kind=URI, uri=found[0][2])
    return None


def page_from_label(pdf: pymupdf.Document, text: str) -> int | None:
    """Numero fisico (0-based) da un numero o un'etichetta di pagina."""
    t = text.strip()
    if not t:
        return None
    if t.isdigit():
        n = int(t) - 1
        return n if 0 <= n < pdf.page_count else None
    try:
        hits = pdf.get_page_numbers(t)
        if hits:
            return hits[0]
    except Exception:
        pass
    return None
