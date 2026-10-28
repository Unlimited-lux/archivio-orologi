"""Lettura veloce dei campi: una sola passata sugli oggetti PDF.

`page.widgets()` del motore ricarica ogni widget scorrendo l'elenco delle
annotazioni della pagina: con migliaia di campi su una pagina il costo diventa
quadratico. Qui si legge l'array /Annots e ogni oggetto una volta sola
(`xref_object` + interpretazione), con una cache dei nodi genitori; il
risultato coincide con `engine._info` (verificato dai test).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import pymupdf

from . import formats as fm
from . import formula as FX
from . import pdfsyntax as ps

_DA_FONT = re.compile(r"/([^\s/]+)\s+([-\d.]+)\s+Tf")
_DA_RGB = re.compile(r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+rg")
_DA_GRAY = re.compile(r"([-\d.]+)\s+g\b")
_DA_CMYK = re.compile(r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+k\b")


class _Reader:
    """Oggetti interpretati con cache (un documento, una passata)."""

    def __init__(self, pdf: pymupdf.Document):
        self.pdf = pdf
        self.cache: dict[int, Any] = {}

    def obj(self, xref: int) -> Any:
        if xref not in self.cache:
            try:
                self.cache[xref] = ps.parse(self.pdf.xref_object(xref, compressed=True))
            except Exception:
                self.cache[xref] = {}
        return self.cache[xref]

    def deref(self, value: Any, depth: int = 0) -> Any:
        while isinstance(value, ps.Ref) and depth < 16:
            value = self.obj(value[0])
            depth += 1
        return value

    def stream_text(self, value: Any) -> str | None:
        if isinstance(value, ps.Ref):
            target = self.obj(value[0])
            if isinstance(target, dict) and not target:
                pass
            try:
                data = self.pdf.xref_stream(value[0])
            except Exception:
                data = None
            if data is not None:
                return data.decode("latin-1")
            return ps.text_of(target) if isinstance(target, (bytes, str)) else None
        return ps.text_of(value) if isinstance(value, (bytes, str)) else None


def _page_box(r: _Reader, page_xref: int) -> list[float]:
    """CropBox (o MediaBox) della pagina, ereditabile, in spazio PDF (senza leggere /Annots)."""
    pdf = r.pdf
    x = page_xref
    seen = set()
    while x and x not in seen:
        seen.add(x)
        for key in ("CropBox", "MediaBox"):
            t, v = pdf.xref_get_key(x, key)
            if t == "xref":
                v, t = pdf.xref_object(int(v.split()[0]), compressed=True), "array"
            if t == "array":
                try:
                    box = [float(n) for n in ps.parse(v)]
                except (ps.PdfSyntaxError, TypeError, ValueError):
                    box = []
                if len(box) == 4:
                    return box
        t, v = pdf.xref_get_key(x, "Parent")
        x = int(v.split()[0]) if t == "xref" else 0
    return [0.0, 0.0, 612.0, 792.0]


def _number(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _text(v: Any) -> str:
    return ps.text_of(v) or "" if v is not None else ""


def scan_fields(pdf: pymupdf.Document, pages: Iterable[int] | None = None,
                names: set[str] | None = None) -> list:
    """Campi delle pagine (tutte se None) nell'ordine delle annotazioni; con `names`
    solo i campi con quei nomi completi."""
    from . import engine as E

    r = _Reader(pdf)
    out = []
    for page in (range(pdf.page_count) if pages is None else pages):
        pxref = pdf.page_xref(page)
        pdict = r.obj(pxref)
        annots = r.deref(pdict.get("Annots")) if isinstance(pdict, dict) else None
        if not isinstance(annots, list):
            continue
        box = None
        for ref in annots:
            if not isinstance(ref, ps.Ref):
                continue
            w = r.obj(ref[0])
            if not isinstance(w, dict) or str(w.get("Subtype", "")) != "Widget":
                continue
            info = _field_info(r, E, page, ref[0], w, names)
            if info is None:
                continue
            if box is None:
                box = _page_box(r, pxref)
            rect = r.deref(w.get("Rect"))
            if isinstance(rect, list) and len(rect) == 4:
                x0, y0, x1, y1 = (_number(v) for v in rect)
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                info.rect = pymupdf.Rect(x0 - box[0], box[3] - y1, x1 - box[0], box[3] - y0)
            out.append(info)
    return out


def _field_info(r: _Reader, E, page: int, xref: int, w: dict, names: set[str] | None):
    # catena dei nodi: widget, genitori… (ciclo interrotto)
    chain: list[tuple[int, dict]] = [(xref, w)]
    seen = {xref}
    node = w
    while isinstance(node.get("Parent"), ps.Ref):
        px = node["Parent"][0]
        if px in seen:
            break
        seen.add(px)
        node = r.obj(px)
        if not isinstance(node, dict):
            break
        chain.append((px, node))
    parts = [_text(d.get("T")) for _x, d in reversed(chain) if d.get("T") is not None]
    name = ".".join(p for p in parts)
    if names is not None and name not in names:
        return None
    fx = xref if w.get("T") is not None else (chain[1][0] if len(chain) > 1 else xref)

    def inh(key):
        for _x, d in chain:
            if key in d:
                return r.deref(d[key])
        return None

    ft = str(inh("FT") or "")
    flags = int(_number(inh("Ff"), 0))
    if ft == "Btn":
        kind = E.BUTTON if flags & E.FF_PUSHBUTTON else (E.RADIO if flags & E.FF_RADIO else E.CHECKBOX)
    elif ft == "Ch":
        kind = E.COMBO if flags & pymupdf.PDF_CH_FIELD_IS_COMBO else E.LIST
    elif ft == "Sig":
        kind = E.SIGNATURE
    else:
        kind = E.TEXT
    info = E.FieldInfo(page=page, xref=xref, field_xref=fx, name=name, kind=kind, rect=pymupdf.Rect())
    info.readonly = bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY)
    info.required = bool(flags & pymupdf.PDF_FIELD_IS_REQUIRED)
    info.no_export = bool(flags & E.FF_NO_EXPORT)
    info.no_toggle_off = kind == E.RADIO and bool(flags & E.FF_NO_TOGGLE_OFF)
    info.in_unison = kind == E.RADIO and bool(flags & E.FF_IN_UNISON)
    info.do_not_scroll = kind == E.TEXT and bool(flags & E.FF_DO_NOT_SCROLL)
    info.file_select = kind == E.TEXT and bool(flags & E.FF_FILE_SELECT)
    info.no_spell = kind in (E.TEXT, E.COMBO) and bool(flags & E.FF_NO_SPELL)
    af = int(_number(w.get("F"), 0))
    info.hidden = bool(af & E.AF_HIDDEN)
    info.locked = bool(af & E.AF_LOCKED)
    if af & E.AF_HIDDEN:
        info.visibility = "hidden"
    elif af & E.AF_NOVIEW:
        info.visibility = "print"
    elif not af & E.AF_PRINT:
        info.visibility = "screen"
    # aspetto: /DA, /MK, /BS
    da = _text(inh("DA"))
    m = _DA_FONT.search(da)
    if m:
        info.font = E._FONT_NAMES.get(m.group(1), m.group(1))
        info.fontsize = _number(m.group(2))
    else:
        info.font = "Helvetica"
    sys_font = _text(w.get("FoliantFont")) if w.get("FoliantFont") is not None else ""
    if sys_font:
        info.font = sys_font
    m = _DA_RGB.search(da)
    if m:
        info.text_color = tuple(_number(v) for v in m.groups())
    else:
        m = _DA_GRAY.search(da)
        if m:
            g = _number(m.group(1))
            info.text_color = (g, g, g)
        else:
            m = _DA_CMYK.search(da)
            info.text_color = E._color([_number(v) for v in m.groups()]) if m else (0, 0, 0)
    mk = r.deref(w.get("MK"))
    mk = mk if isinstance(mk, dict) else {}
    bg, bc = r.deref(mk.get("BG")), r.deref(mk.get("BC"))
    info.fill_color = E._color([_number(v) for v in bg]) if isinstance(bg, list) and bg else None
    info.border_color = E._color([_number(v) for v in bc]) if isinstance(bc, list) and bc else None
    bs = r.deref(w.get("BS"))
    if isinstance(bs, dict):
        info.border_width = _number(bs.get("W"), 1.0)
        info.border_style = str(bs.get("S", "S"))[:1].upper() or "S"
    else:
        border = r.deref(w.get("Border"))
        # senza /BS vale /Border; in mancanza di entrambi lo spessore predefinito è 1
        info.border_width = _number(border[2], 1.0) if isinstance(border, list) and len(border) >= 3 else 1.0
    info.tooltip = _text(inh("TU"))
    info.export_name = _text(inh("TM"))
    q = inh("Q")
    info.align = int(_number(q, 0)) if q is not None else E.ALIGN_LEFT
    try:
        info.rotation = int(_number(mk.get("R"), 0)) % 360
    except (TypeError, ValueError):
        info.rotation = 0
    raw_v = _plain(r, inh("V"))
    raw_dv = _plain(r, inh("DV"))
    if kind == E.TEXT:
        info.value = raw_v if isinstance(raw_v, str) else ""
        info.default = raw_dv if isinstance(raw_dv, str) else ""
        info.multiline = bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
        info.password = bool(flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD)
        info.comb = bool(flags & pymupdf.PDF_TX_FIELD_IS_COMB)
        info.maxlen = int(_number(inh("MaxLen"), 0))
        aa_w = r.deref(w.get("AA"))
        aa_f = r.deref(chain[1][1].get("AA")) if fx != xref and len(chain) > 1 else None
        if isinstance(aa_w, dict) or isinstance(aa_f, dict):
            def script(trigger):
                for aa in (aa_w, aa_f):
                    if isinstance(aa, dict) and trigger in aa:
                        act = r.deref(aa[trigger])
                        if isinstance(act, dict) and "JS" in act:
                            return r.stream_text(act["JS"])
                return None
            info.fmt = fm.Format.from_script(script("F"))
            c_js = script("C")
            info.calc = E.Calculation.from_script(c_js)
            info.formula = FX.from_script(c_js) if info.calc is None else None
            info.validation = E.Validation.from_script(script("V"))
    elif kind in (E.CHECKBOX, E.RADIO):
        ap = r.deref(w.get("AP"))
        normal = r.deref(ap.get("N")) if isinstance(ap, dict) else None
        states = [str(k) for k in normal.keys()] if isinstance(normal, dict) else []
        info.export = next((s for s in states if s != E.OFF), None) or "Yes"
        state = w.get("AS")
        state = str(state) if isinstance(state, ps.Name) else None
        if kind == E.CHECKBOX:
            info.value = bool(state) and state != E.OFF
        else:
            info.value = raw_v if isinstance(raw_v, str) and raw_v else E.OFF
            if info.value == E.OFF and state and state != E.OFF:
                info.value = state
        info.default = raw_dv if isinstance(raw_dv, str) else E.OFF
        ca = mk.get("CA")
        ca = _text(ca) if ca is not None else None
        info.check_style = next((k for k, c in E.CHECK_STYLES.items() if c == ca),
                                "check" if kind == E.CHECKBOX else "circle")
    elif kind in (E.COMBO, E.LIST):
        opt = r.deref(inh("Opt"))
        pairs = []
        for item in opt if isinstance(opt, list) else []:
            item = r.deref(item)
            if isinstance(item, list):
                e = _text(r.deref(item[0])) if item else ""
                pairs.append((e, _text(r.deref(item[1])) if len(item) > 1 else e))
            else:
                t = _text(item)
                pairs.append((t, t))
        info.exports = [e for e, _d in pairs]
        info.options = [d for _e, d in pairs]
        info.sort = bool(flags & pymupdf.PDF_CH_FIELD_IS_SORT)
        info.commit_on_change = bool(flags & pymupdf.PDF_CH_FIELD_IS_COMMIT_ON_SEL_CHANGE)
        info.multiselect = bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT)
        info.editable = bool(flags & pymupdf.PDF_CH_FIELD_IS_EDIT)
        if kind == E.LIST and info.multiselect:
            info.value = raw_v if isinstance(raw_v, list) else ([raw_v] if raw_v else [])
            info.default = raw_dv if isinstance(raw_dv, list) else ([raw_dv] if raw_dv else [])
        else:
            if isinstance(raw_v, list):
                raw_v = raw_v[0] if raw_v else ""
            info.value = raw_v or ""
            info.default = raw_dv if isinstance(raw_dv, str) else ""
    elif kind == E.BUTTON:
        info.caption = _text(mk.get("CA")) if mk.get("CA") is not None else ""
        info.caption_down = _text(mk.get("AC")) if mk.get("AC") is not None else ""
        info.value = info.caption
        info.action = E.button_action(r.pdf, xref) if ("A" in w or "AA" in w) else {}
        tp = int(_number(mk.get("TP"), 0))
        info.image = tp == 1 and E.is_image_field(r.pdf, xref)
        info.caption_rollover = _text(mk.get("RC")) if mk.get("RC") is not None else ""
        info.layout = E.BUTTON_LAYOUTS[tp] if 0 <= tp < len(E.BUTTON_LAYOUTS) else "text"
        info.icons = tuple(state for state, key in E.ICON_KEYS.items() if mk.get(key) is not None)
        h = str(w.get("H") or "I").lstrip("/")
        info.highlight = {v: k for k, v in E.HIGHLIGHTS.items()}.get(h, "invert")
    elif kind == E.SIGNATURE:
        info.value = "firmato" if inh("V") is not None else ""
        info.lock = E._read_lock(r.pdf, xref) if inh("Lock") is not None else {}
        info.sv = E.read_sv(r.pdf, xref) if inh("SV") is not None else {}
    return info


def _plain(r: _Reader, value: Any) -> Any:
    value = r.deref(value)
    if value is None:
        return None
    if isinstance(value, ps.Name):
        return str(value)
    if isinstance(value, list):
        return [_text(r.deref(v)) for v in value]
    if isinstance(value, dict):
        return None
    return ps.text_of(value)


def widget_xrefs(pdf: pymupdf.Document, page: int) -> list[int]:
    """Xref dei widget della pagina, nell'ordine di /Annots (senza caricarli)."""
    r = _Reader(pdf)
    pdict = r.obj(pdf.page_xref(page))
    annots = r.deref(pdict.get("Annots")) if isinstance(pdict, dict) else None
    out = []
    for ref in annots if isinstance(annots, list) else []:
        if isinstance(ref, ps.Ref):
            d = r.obj(ref[0])
            if isinstance(d, dict) and str(d.get("Subtype", "")) == "Widget":
                out.append(ref[0])
    return out


def widget_info(pdf: pymupdf.Document, page: int, xref: int):
    """Informazioni di un solo widget (senza scorrere la pagina); None se non è un widget."""
    from . import engine as E

    r = _Reader(pdf)
    w = r.obj(xref)
    if not isinstance(w, dict) or str(w.get("Subtype", "")) != "Widget":
        return None
    info = _field_info(r, E, page, xref, w, None)
    rect = r.deref(w.get("Rect"))
    if info is not None and isinstance(rect, list) and len(rect) == 4:
        box = _page_box(r, pdf.page_xref(page))
        x0, y0, x1, y1 = (_number(v) for v in rect)
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        info.rect = pymupdf.Rect(x0 - box[0], box[3] - y1, x1 - box[0], box[3] - y0)
    return info
