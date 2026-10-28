"""Appiattimento (SEC-24): commenti, campi, elementi aggiunti da Foliant, livelli,
ritaglio e, a scelta, rasterizzazione delle pagine.

Tutte le funzioni lavorano su un `pymupdf.Document` già dentro `document.change()`.
Il `bake()` del motore non si usa: elimina in silenzio i segni di redazione non applicati
(il testo sotto resta, la redazione da fare si perde) e le annotazioni nascoste, che
ANN-40 chiede di conservare. Ogni annotazione viene quindi fusa singolarmente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from foliant.api import _

HIDDEN_KEEP = "keep"
HIDDEN_DELETE = "delete"

_HIDDEN_FLAGS = pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW
_STAMP_RE = re.compile(rb"/FolStamp_([a-z]+)_(\d+)(\s+Do)")


@dataclass
class FlattenOptions:
    annots: bool = True
    widgets: bool = True
    hidden: str = HIDDEN_KEEP          # annotazioni nascoste o non stampabili: mai fuse
    keep_links: bool = True
    signatures: bool = False           # firme già apposte: escluse salvo scelta esplicita
    stamps: bool = True                # filigrane, sfondi, numeri, intestazioni di Foliant
    layers: bool = False
    crop: bool = False
    pages: list[int] | None = None     # None = tutte
    rasterize: bool = False
    dpi: int = 150


@dataclass
class FlattenReport:
    annots: int = 0
    widgets: int = 0
    stamps: int = 0
    hidden_removed: int = 0
    layer_sections: int = 0
    cropped: int = 0
    rasterized: int = 0
    notes_lost: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------- utilità

def _ref(xref: int) -> str:
    return f"{xref} 0 R"


def _refs(value: str) -> list[int]:
    return [int(m) for m in re.findall(r"(\d+)\s+0\s+R", value or "")]


def _is_signed(pdf: pymupdf.Document, xref: int) -> bool:
    for x in (xref, *_refs(pdf.xref_get_key(xref, "Parent")[1])):
        if pdf.xref_get_key(x, "FT")[1] == "/Sig" and pdf.xref_get_key(x, "V")[0] != "null":
            return True
    return False


def _field_root(pdf: pymupdf.Document, xref: int) -> int:
    seen = {xref}
    while True:
        kind, val = pdf.xref_get_key(xref, "Parent")
        parents = _refs(val) if kind == "xref" else []
        if not parents or parents[0] in seen:
            return xref
        xref = parents[0]
        seen.add(xref)


# ---------------------------------------------- commenti, campi e firme

def _flags(pdf: pymupdf.Document, xref: int) -> int:
    kind, val = pdf.xref_get_key(xref, "F")
    try:
        return int(val) if kind == "int" else 0
    except ValueError:
        return 0


def _matrix(pdf: pymupdf.Document, xref: int) -> list[float]:
    kind, val = pdf.xref_get_key(xref, "Matrix")
    try:
        nums = [float(v) for v in val.strip("[]").split()] if kind == "array" else []
    except ValueError:
        nums = []
    return nums if len(nums) == 6 else [1, 0, 0, 1, 0, 0]


def _appearance(pdf: pymupdf.Document, xref: int) -> int:
    kind, val = pdf.xref_get_key(xref, "AP/N")
    if kind == "xref":
        return _refs(val)[0]
    if kind == "dict":                          # caselle e pulsanti: aspetto dello stato /AS
        state = pdf.xref_get_key(xref, "AS")[1].lstrip("/")
        k2, v2 = pdf.xref_get_key(xref, f"AP/N/{state}") if state else ("null", "")
        if k2 == "xref":
            return _refs(v2)[0]
    return 0


def _fuse_ops(pdf: pymupdf.Document, xref: int, ap: int, name: str) -> bytes:
    """«q cm /nome Do Q» che disegna l'aspetto dove l'annotazione appare (ISO 32000-2
    §12.5.5: BBox trasformato con /Matrix e adattato a /Rect)."""
    rect = _box(pdf, xref, "Rect")
    bbox = _box(pdf, ap, "BBox") or rect
    if not rect or not bbox:
        return b""
    m = pymupdf.Matrix(*_matrix(pdf, ap))
    tb = pymupdf.Rect(bbox) * m
    x0, y0, x1, y1 = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
    if tb.width <= 0 or tb.height <= 0:
        return b""
    sx, sy = (x1 - x0) / tb.width, (y1 - y0) / tb.height
    e, f = x0 - tb.x0 * sx, y0 - tb.y0 * sy
    return f"q {sx:g} 0 0 {sy:g} {e:g} {f:g} cm /{name} Do Q\n".encode()


def _append_content(pdf: pymupdf.Document, page: pymupdf.Page, ops: bytes) -> None:
    """Aggiunge `ops` al contenuto della pagina isolando quello esistente in q … Q."""
    current = page.get_contents()
    new = []
    for data in (b"q\n", b"Q\n" + ops):
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, data)
        new.append(x)
    refs = [new[0], *current, new[1]]
    pdf.xref_set_key(page.xref, "Contents", "[" + " ".join(_ref(x) for x in refs) + "]")


def _xobject_dict(pdf: pymupdf.Document, page_xref: int) -> tuple[int, str]:
    """(oggetto, percorso) del dizionario /XObject delle risorse della pagina, adatto a
    `xref_set_key` (che non attraversa i riferimenti indiretti). Le risorse ereditate
    vengono copiate nella pagina."""
    kind, val = pdf.xref_get_key(page_xref, "Resources")
    if kind == "null":
        node, inherited = page_xref, ("null", "")
        for _i in range(64):
            parent = _refs(pdf.xref_get_key(node, "Parent")[1])
            if not parent:
                break
            node = parent[0]
            inherited = pdf.xref_get_key(node, "Resources")
            if inherited[0] != "null":
                break
        pdf.xref_set_key(page_xref, "Resources", inherited[1] if inherited[0] != "null" else "<<>>")
        kind, val = pdf.xref_get_key(page_xref, "Resources")
    owner, prefix = (_refs(val)[0], "") if kind == "xref" else (page_xref, "Resources/")
    k2, v2 = pdf.xref_get_key(owner, prefix + "XObject")
    if k2 == "xref":
        return _refs(v2)[0], ""
    if k2 == "null":
        pdf.xref_set_key(owner, prefix + "XObject", "<<>>")
    return owner, prefix + "XObject/"


def _set_xobject(pdf: pymupdf.Document, page_xref: int, name: str, value: str) -> None:
    owner, path = _xobject_dict(pdf, page_xref)
    pdf.xref_set_key(owner, path + name, value)


def _delete(page: pymupdf.Page, xref: int) -> None:
    subtype = page.parent.xref_get_key(xref, "Subtype")[1]
    if subtype == "/Link":
        for link in page.get_links():
            if link.get("xref") == xref:
                page.delete_link(link)
                return
    elif subtype == "/Widget":
        for w in page.widgets():
            if w.xref == xref:
                page.delete_widget(w)
                return
    else:
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)


def flatten_annotations(pdf: pymupdf.Document, opts: FlattenOptions, rep: FlattenReport) -> None:
    """Regole di ANN-40 e FRM-32: ogni aspetto /AP /N diventa un Form XObject nel
    contenuto della pagina; le annotazioni nascoste o non stampabili non vengono mai fuse
    (restano o si eliminano); popup e risposte delle annotazioni fuse spariscono; i segni
    di redazione non applicati e le firme apposte (salvo scelta esplicita) restano."""
    for pno in range(pdf.page_count) if opts.pages is None else opts.pages:
        page = pdf[pno]
        entries = page.annot_xrefs()
        fuse, drop = [], []
        for xref, atype, _name in entries:
            if atype in (pymupdf.PDF_ANNOT_POPUP, pymupdf.PDF_ANNOT_REDACT):
                continue
            if pdf.xref_get_key(xref, "IRT")[0] != "null":
                continue                                    # risposta: segue l'originale
            if atype == pymupdf.PDF_ANNOT_LINK:
                if not opts.keep_links:
                    drop.append(xref)
            elif atype == pymupdf.PDF_ANNOT_WIDGET:
                if opts.widgets and (opts.signatures or not _is_signed(pdf, xref)):
                    fuse.append(xref)
            elif not opts.annots:
                continue
            elif _flags(pdf, xref) & _HIDDEN_FLAGS or not _flags(pdf, xref) & pymupdf.PDF_ANNOT_IS_PRINT:
                if opts.hidden == HIDDEN_DELETE:
                    drop.append(xref)
                    rep.hidden_removed += 1
            else:
                fuse.append(xref)
        gone = set(fuse) | set(drop)
        replies = [x for x, _t, _n in entries
                   if set(_refs(pdf.xref_get_key(x, "IRT")[1])) & gone]
        # aspetto mancante: generato prima della fusione
        for xref in fuse:
            if not _appearance(pdf, xref):
                try:
                    if pdf.xref_get_key(xref, "Subtype")[1] == "/Widget":
                        next(w for w in page.widgets() if w.xref == xref).update()
                    else:
                        page.load_annot(xref).update()
                except Exception:
                    pass
                page = pdf[pno]
        ops = []
        for xref in fuse:
            ap = _appearance(pdf, xref)
            if not ap:
                continue
            name = f"FolFlat{xref}"
            piece = _fuse_ops(pdf, xref, ap, name)
            if not piece:
                continue
            pdf.xref_set_key(ap, "Type", "/XObject")
            pdf.xref_set_key(ap, "Subtype", "/Form")
            _set_xobject(pdf, page.xref, name, _ref(ap))
            ops.append(piece)
            if pdf.xref_get_key(xref, "Subtype")[1] == "/Widget":
                rep.widgets += 1
            else:
                rep.annots += 1
                if pdf.xref_get_key(xref, "Subtype")[1] == "/Text" and pdf.xref_get_key(xref, "Contents")[1]:
                    rep.notes_lost += 1
        if ops:
            _append_content(pdf, page, b"".join(ops))
        for xref in replies + fuse + drop:
            page = pdf[pno]
            if xref in {x for x, _t, _n in page.annot_xrefs()}:
                _delete(page, xref)
    _tidy_form(pdf)


def _tidy_form(pdf: pymupdf.Document) -> None:
    """Toglie da /AcroForm i campi senza più widget e /AcroForm stesso se resta vuoto."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm")[0] == "null":
        return
    alive = {w.xref for page in pdf for w in page.widgets()}

    def has_widget(x, seen):
        if x in alive:
            return True
        if x in seen:
            return False
        seen.add(x)
        return any(has_widget(k, seen) for k in _refs(pdf.xref_get_key(x, "Kids")[1]))

    fields = [x for x in _refs(pdf.xref_get_key(cat, "AcroForm/Fields")[1]) if has_widget(x, set())]
    if fields:
        pdf.xref_set_key(cat, "AcroForm/Fields", "[" + " ".join(_ref(x) for x in fields) + "]")
    else:
        pdf.xref_set_key(cat, "AcroForm", "null")


# ------------------------------------------------------------------ timbri

def flatten_stamps(pdf: pymupdf.Document, pages: list[int] | None, rep: FlattenReport) -> None:
    """Filigrane, sfondi, numeri e intestazioni di Foliant restano visibili ma diventano
    contenuto normale: il nome che li rende riconoscibili (e rimovibili) cambia e il
    livello «Foliant: …» non li condiziona più."""
    layer_xrefs: set[int] = set()
    for pno in range(pdf.page_count) if pages is None else pages:
        page = pdf[pno]
        names: set[bytes] = set()
        for x in page.get_contents():
            data = pdf.xref_stream(x) or b""
            found = _STAMP_RE.findall(data)
            if not found:
                continue
            names.update(b"FolStamp_%s_%s" % (k, n) for k, n, _d in found)
            rep.stamps += len(found)
            pdf.update_stream(x, _STAMP_RE.sub(rb"/FlatStamp_\1_\2\3", data))
        for name in names:
            old = name.decode()
            kind, val = pdf.xref_get_key(page.xref, f"Resources/XObject/{old}")
            if kind == "null":
                continue
            for x in _refs(val):
                oc = pdf.xref_get_key(x, "OC")
                layer_xrefs.update(_refs(oc[1]))
                pdf.xref_set_key(x, "OC", "null")
            _set_xobject(pdf, page.xref, f"Flat{old[3:]}", val)
            _set_xobject(pdf, page.xref, old, "null")
    still_used: set[int] = set()
    for page in pdf:
        if any(_STAMP_RE.search(pdf.xref_stream(x) or b"") for x in page.get_contents()):
            still_used.update(layer_xrefs)
            break
    _drop_ocgs(pdf, layer_xrefs - still_used)


def _drop_ocgs(pdf: pymupdf.Document, xrefs: set[int]) -> None:
    if not xrefs:
        return
    cat = pdf.pdf_catalog()
    for key in ("OCProperties/OCGs", "OCProperties/D/ON", "OCProperties/D/OFF", "OCProperties/D/Order"):
        kind, val = pdf.xref_get_key(cat, key)
        if kind == "array":
            kept = re.sub(r"(\d+)\s+0\s+R", lambda m: "" if int(m.group(1)) in xrefs else m.group(0), val)
            pdf.xref_set_key(cat, key, re.sub(r"\s+", " ", kept))
    if not _refs(pdf.xref_get_key(cat, "OCProperties/OCGs")[1]):
        pdf.xref_set_key(cat, "OCProperties", "null")


# ----------------------------------------------------------------- livelli

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_INLINE_END = re.compile(rb"[\s]EI(?=[\s/\[<(]|$)")


def _lex(data: bytes):
    """Token di un flusso di contenuto: (tipo, inizio, fine, valore). Le immagini in
    linea (BI … ID dati EI) sono un unico operatore."""
    i, n = 0, len(data)
    stop = _WS + _DELIM
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
        elif c == 0x25:                                       # %
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
        elif c == 0x28:                                       # (
            start, depth, i = i, 1, i + 1
            while i < n and depth:
                ch = data[i]
                if ch == 0x5C:
                    i += 2
                    continue
                depth += (ch == 0x28) - (ch == 0x29)
                i += 1
            yield "str", start, i, None
        elif c == 0x3C:                                       # <
            if data[i + 1:i + 2] == b"<":
                yield "<<", i, i + 2, None
                i += 2
            else:
                j = data.find(b">", i)
                j = n - 1 if j < 0 else j
                yield "str", i, j + 1, None
                i = j + 1
        elif c == 0x3E:                                       # >
            w = 2 if data[i + 1:i + 2] == b">" else 1
            yield ">>", i, i + w, None
            i += w
        elif c in b"[]{}":
            yield chr(c), i, i + 1, None
            i += 1
        elif c == 0x2F:                                       # /
            j = i + 1
            while j < n and data[j] not in stop:
                j += 1
            name = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), data[i + 1:j])
            yield "name", i, j, name
            i = j
        else:
            j = i
            while j < n and data[j] not in stop:
                j += 1
            if j == i:
                i += 1
                continue
            word = data[i:j]
            if word == b"BI":
                k = data.find(b"ID", j)
                m = _INLINE_END.search(data, k + 3) if k >= 0 else None
                end = m.end() if m else n
                yield "op", i, end, b"EI"
                i = end
                continue
            kind = "num" if re.fullmatch(rb"[+\-]?(\d+\.?\d*|\.\d+)", word) else "op"
            yield kind, i, j, word
            i = j


def strip_hidden_sections(data: bytes, hidden_prop, hidden_xobj) -> tuple[bytes, int]:
    """Toglie le sezioni «/OC /nome BDC … EMC» dei gruppi nascosti e i «/nome Do»
    degli oggetti con /OC nascosto. Restituisce (dati, sezioni tolte)."""
    cuts: list[tuple[int, int]] = []
    operands: list[tuple] = []
    stack: list[bool] = []
    cut_start = None
    for tok in _lex(data):
        kind, start, end, value = tok
        if kind != "op":
            operands.append(tok)
            continue
        first = operands[0][1] if operands else start
        if value in (b"BDC", b"BMC"):
            hidden = False
            if (value == b"BDC" and cut_start is None and len(operands) >= 2 and operands[0][3] == b"OC"
                    and operands[1][0] == "name"):
                hidden = bool(hidden_prop(operands[1][3].decode("latin-1")))
            if hidden:
                cut_start = first
            stack.append(hidden)
        elif value == b"EMC":
            if stack and stack.pop():
                cuts.append((cut_start, end))
                cut_start = None
        elif value == b"Do" and cut_start is None and operands and operands[-1][0] == "name":
            if hidden_xobj(operands[-1][3].decode("latin-1")):
                cuts.append((first, end))
        operands = []
    if cut_start is not None:
        cuts.append((cut_start, len(data)))
    if not cuts:
        return data, 0
    out, pos = [], 0
    for a, b in cuts:
        out.append(data[pos:a])
        out.append(b"\n")
        pos = b
    out.append(data[pos:])
    return b"".join(out), len(cuts)


def _ocg_states(pdf: pymupdf.Document) -> dict[int, bool]:
    try:
        return {x: bool(info.get("on", True)) for x, info in (pdf.get_ocgs() or {}).items()}
    except Exception:
        return {}


def _oc_hidden(pdf: pymupdf.Document, xref: int, states: dict[int, bool]) -> bool:
    if xref in states:
        return not states[xref]
    if pdf.xref_get_key(xref, "Type")[1] != "/OCMD":
        return False
    groups = [states.get(x, True) for x in _refs(pdf.xref_get_key(xref, "OCGs")[1])]
    if not groups:
        return False
    policy = pdf.xref_get_key(xref, "P")[1] or "/AnyOn"
    visible = {"/AllOn": all(groups), "/AnyOff": not all(groups),
               "/AllOff": not any(groups)}.get(policy, any(groups))
    return not visible


def flatten_layers(pdf: pymupdf.Document, rep: FlattenReport) -> None:
    """Contenuto dei livelli nascosti eliminato, quello dei visibili reso incondizionato,
    /OCProperties rimosso."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "OCProperties")[0] == "null":
        return
    states = _ocg_states(pdf)
    done: set[int] = set()

    def lookup(owners, path):
        for owner in owners:
            kind, val = pdf.xref_get_key(owner, path)
            if kind == "xref":
                return _refs(val)[0]
        return 0

    def process(xref_stream: int, owners: list[int]) -> None:
        data = pdf.xref_stream(xref_stream) or b""

        def hidden_prop(name):
            x = lookup(owners, f"Resources/Properties/{name}")
            return x and _oc_hidden(pdf, x, states)

        removed: set[str] = set()

        def hidden_xobj(name):
            x = lookup(owners, f"Resources/XObject/{name}")
            if not x:
                return False
            oc = _refs(pdf.xref_get_key(x, "OC")[1])
            if oc and _oc_hidden(pdf, oc[0], states):
                removed.add(name)
                return True
            return False

        new, n = strip_hidden_sections(data, hidden_prop, hidden_xobj)
        if n:
            pdf.update_stream(xref_stream, new)
            rep.layer_sections += n
        # oggetti nascosti non più disegnati: via anche dalle risorse
        for name in removed:
            if not re.search(rb"/" + re.escape(name.encode("latin-1")) + rb"\s+Do", new) and \
                    pdf.xref_get_key(owners[0], f"Resources/XObject/{name}")[0] != "null":
                _set_xobject(pdf, owners[0], name, "null")
        # moduli annidati (form XObject) con le proprie risorse
        for m in re.finditer(rb"/([^\s/\[\]<>()]+)\s+Do", new):
            x = lookup(owners, f"Resources/XObject/{m.group(1).decode('latin-1')}")
            if x and x not in done and pdf.xref_get_key(x, "Subtype")[1] == "/Form":
                done.add(x)
                pdf.xref_set_key(x, "OC", "null")
                process(x, [x] + owners)

    for page in pdf:
        for x in page.get_contents():
            process(x, [page.xref])
        for xref, _t, _n in page.annot_xrefs():
            oc = _refs(pdf.xref_get_key(xref, "OC")[1])
            if oc and _oc_hidden(pdf, oc[0], states):
                _delete(page, xref)
                rep.layer_sections += 1
            elif oc:
                pdf.xref_set_key(xref, "OC", "null")
    for page in pdf:
        kind, val = pdf.xref_get_key(page.xref, "Resources/XObject")
        if kind == "dict":
            for x in _refs(val):
                if pdf.xref_get_key(x, "OC")[0] != "null":
                    pdf.xref_set_key(x, "OC", "null")
    pdf.xref_set_key(cat, "OCProperties", "null")


# ----------------------------------------------------------------- ritaglio

def _box(pdf: pymupdf.Document, page_xref: int, key: str) -> list[float] | None:
    kind, val = pdf.xref_get_key(page_xref, key)
    if kind != "array":
        return None
    try:
        nums = [float(v) for v in val.strip("[]").split()]
    except ValueError:
        return None
    return nums if len(nums) == 4 else None


class PendingMarks(ValueError):
    """Ritaglio e rasterizzazione richiedono che i segni di redazione siano applicati."""


def pages_with_marks(pdf: pymupdf.Document, pages: list[int] | None) -> list[int]:
    return [p for p in (range(pdf.page_count) if pages is None else pages)
            if any(t == pymupdf.PDF_ANNOT_REDACT for _x, t, _n in pdf[p].annot_xrefs())]


def flatten_crop(pdf: pymupdf.Document, pages: list[int] | None, rep: FlattenReport) -> None:
    """Contenuto interamente fuori dal ritaglio eliminato (testo, immagini e grafica), le
    immagini a cavallo ripulite fuori dal bordo e /MediaBox posto uguale al /CropBox."""
    for pno in range(pdf.page_count) if pages is None else pages:
        page = pdf[pno]
        media = _box(pdf, page.xref, "MediaBox") or list(page.mediabox)
        crop = _box(pdf, page.xref, "CropBox")
        if not crop:
            continue
        # area visibile V = CropBox ∩ MediaBox, in coordinate PDF
        v = [max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3])]
        if v[2] <= v[0] or v[3] <= v[1] or v == media:
            continue
        # strisce esterne in coordinate standard (origine in alto a sinistra di V)
        mx0, my0, mx1, my1 = media[0] - v[0], v[3] - media[3], media[2] - v[0], v[3] - media[1]
        vw, vh = v[2] - v[0], v[3] - v[1]
        strips = [pymupdf.Rect(mx0, my0, mx1, 0), pymupdf.Rect(mx0, vh, mx1, my1),
                  pymupdf.Rect(mx0, 0, 0, vh), pymupdf.Rect(vw, 0, mx1, vh)]
        for xref, _t, _n in page.annot_xrefs():
            r = _box(pdf, xref, "Rect")
            if r and (r[2] <= v[0] or r[0] >= v[2] or r[3] <= v[1] or r[1] >= v[3]):
                _delete(page, xref)
                page = pdf[pno]
        added = False
        for r in strips:
            if r.width > 0 and r.height > 0:
                page.add_redact_annot(r, fill=False)
                added = True
        if added:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        page = pdf[pno]
        box = "[" + " ".join(f"{x:g}" for x in v) + "]"
        pdf.xref_set_key(page.xref, "MediaBox", box)
        pdf.xref_set_key(page.xref, "CropBox", "null")
        for key in ("TrimBox", "BleedBox", "ArtBox"):
            b = _box(pdf, page.xref, key)
            if b:
                x0, y0, x1, y1 = max(b[0], v[0]), max(b[1], v[1]), min(b[2], v[2]), min(b[3], v[3])
                pdf.xref_set_key(page.xref, key, f"[{x0:g} {y0:g} {x1:g} {y1:g}]" if x1 > x0 and y1 > y0
                                 else "null")
        rep.cropped += 1


# ----------------------------------------------------------- rasterizzazione

def rasterize(pdf: pymupdf.Document, pages: list[int] | None, dpi: int, rep: FlattenReport) -> None:
    """Ogni pagina diventa un'immagine (annotazioni già fuse). Collegamenti e segni di
    redazione restano annotazioni."""
    for pno in range(pdf.page_count) if pages is None else pages:
        page = pdf[pno]
        pix = page.get_pixmap(dpi=dpi, annots=False)
        rotation = page.rotation
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, b" ")
        pdf.xref_set_key(page.xref, "Contents", _ref(x))
        pdf.xref_set_key(page.xref, "Resources", "<<>>")
        pdf.xref_set_key(page.xref, "Group", "null")
        page = pdf[pno]
        page.set_rotation(0)
        page = pdf[pno]
        if rotation:
            pix = pymupdf.Pixmap(pix, 0) if pix.alpha else pix
            img = pymupdf.Pixmap(pix)
            page.insert_image(page.rect, pixmap=img, rotate=(360 - rotation) % 360, keep_proportion=False)
        else:
            page.insert_image(page.rect, pixmap=pix, keep_proportion=False)
        page.set_rotation(rotation)
        rep.rasterized += 1
    if rep.rasterized:
        rep.warnings.append(_("Le pagine rasterizzate non hanno testo selezionabile o ricercabile: "
                              "esegui il riconoscimento del testo (OCR) se serve."))


# ------------------------------------------------------------------- insieme

def flatten(pdf: pymupdf.Document, opts: FlattenOptions | None = None) -> FlattenReport:
    opts = opts or FlattenOptions()
    rep = FlattenReport()
    if (opts.crop or opts.rasterize) and pages_with_marks(pdf, opts.pages):
        raise PendingMarks(_("Applica prima i segni di redazione: ritaglio e rasterizzazione "
                             "cambiano il contenuto sotto i segni."))
    if opts.layers:
        flatten_layers(pdf, rep)
    if opts.stamps:
        flatten_stamps(pdf, opts.pages, rep)
    if opts.crop:
        flatten_crop(pdf, opts.pages, rep)
    if opts.annots or opts.widgets or opts.rasterize:
        flatten_annotations(pdf, opts, rep)
    if opts.rasterize:
        rasterize(pdf, opts.pages, opts.dpi, rep)
    if rep.notes_lost:
        rep.warnings.append(_("{n} note fuse come icona: il loro testo non è più leggibile.")
                            .format(n=rep.notes_lost))
    return rep


def preview(pdf: pymupdf.Document, opts: FlattenOptions, pno: int, width: int = 360) -> pymupdf.Pixmap:
    """Anteprima della pagina `pno` appiattita, calcolata su una copia (che conserva
    livelli e moduli del documento)."""
    tmp = pymupdf.open("pdf", pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE, garbage=0))
    try:
        tmp.select([pno])
        flatten(tmp, FlattenOptions(**{**opts.__dict__, "pages": None}))
        page = tmp[0]
        zoom = width / max(1.0, page.rect.width)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), annots=True)
    finally:
        tmp.close()
