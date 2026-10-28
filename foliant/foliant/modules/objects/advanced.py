"""Funzioni avanzate sugli oggetti (logica pura): replica su più pagine (OBJ-32),
trasformazioni numeriche e reimpostazione (OBJ-12), scomporre e raggruppare
oggetti composti (OBJ-26)."""

from __future__ import annotations

import math
import re

import pymupdf

from . import clipboard as C
from .content import FORM, IMAGE, _dict_keys, fmt_matrix, fmt_name, fmt_num, parse_page, rcenter
from .engine import (
    IDENTITY, ObjectError, _check_indices, _sep, about, apply_edits, outer_of, page_to_pdf_transform,
    prune_resources, rotation_matrix, set_resource, union_rect, unit_bytes, write_content,
)

CATEGORIES = C.CATEGORIES


# ================================================================ OBJ-32
def replicate(pdf: pymupdf.Document, page_index: int, indices, pages, skip_different: bool = True) -> list[int]:
    """Copia gli oggetti nella stessa posizione sulle pagine indicate, con un solo
    oggetto composto condiviso. Restituisce le pagine modificate."""
    payload = C.make_payload(pdf, page_index, indices)
    src = pymupdf.open(stream=payload.pdf, filetype="pdf")
    try:
        sp = src[0]
        sw, sh = sp.rect.width, sp.rect.height
        done = []
        targets = [i for i in sorted(set(pages)) if i != page_index and 0 <= i < pdf.page_count]
        if targets:
            # anche l'originale diventa l'oggetto condiviso: i dati restano una sola volta
            from .engine import delete_objects
            delete_objects(pdf, page_index, indices)
            pg = pdf[page_index]
            pg.show_pdf_page(pg.rect, src, 0, keep_proportion=True, overlay=True)
        for i in targets:
            page = pdf[i]
            same = abs(page.rect.width - sw) < 0.5 and abs(page.rect.height - sh) < 0.5
            if not same and skip_different:
                continue
            page.show_pdf_page(page.rect, src, 0, keep_proportion=True, overlay=True)
            done.append(i)
        return done
    finally:
        src.close()


# ================================================================ OBJ-12
def set_ctms(pdf: pymupdf.Document, page_index: int, new: dict) -> list[int]:
    """Imposta direttamente la matrice (spazio pdf) di alcuni oggetti."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    edits = []
    for i, ctm in new.items():
        o = parsed.objects[i]
        if abs(ctm.a * ctm.d - ctm.b * ctm.c) < 1e-9:
            raise ObjectError("La trasformazione non è valida (matrice degenere).")
        s, e = o.span
        edits.append((s, e, _sep(unit_bytes(parsed, o, ctm, outer_of(o)))))
    data, _ = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    return sorted(new)


def transform_matrix(scale_x: float = 100, scale_y: float = 100, rotate: float = 0, skew_x: float = 0,
                     skew_y: float = 0, dx: float = 0, dy: float = 0) -> pymupdf.Matrix:
    """Trasformazione combinata (spazio pagina, attorno all'origine): scala %, rotazione
    antioraria, inclinazioni in gradi (-80…80), spostamento in punti."""
    for v in (skew_x, skew_y):
        if not -80 <= v <= 80:
            raise ObjectError("L'inclinazione deve essere compresa tra -80° e 80°.")
    if scale_x == 0 or scale_y == 0:
        raise ObjectError("La scala non può essere zero.")
    S = pymupdf.Matrix(scale_x / 100.0, 0, 0, scale_y / 100.0, 0, 0)
    K = pymupdf.Matrix(1, math.tan(math.radians(skew_y)), -math.tan(math.radians(skew_x)), 1, 0, 0)
    M = S * K * rotation_matrix(rotate)
    if abs(M.a * M.d - M.b * M.c) < 1e-9:
        raise ObjectError("La trasformazione non è valida (matrice degenere).")
    return M * pymupdf.Matrix(1, 0, 0, 1, dx, dy)


def apply_transform(pdf: pymupdf.Document, page_index: int, indices, each: bool = False, **kw) -> list[int]:
    """Applica `transform_matrix(**kw)` all'insieme (attorno al centro comune) o a ogni oggetto."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    M = transform_matrix(**kw)
    lin = pymupdf.Matrix(M.a, M.b, M.c, M.d, 0, 0)
    move = pymupdf.Matrix(1, 0, 0, 1, M.e, M.f)
    new = {}
    common = rcenter(union_rect(objs))
    for o in objs:
        c = rcenter(o.quad.rect) if each else common
        T = about(c, lin) * move
        new[o.index] = o.ctm * page_to_pdf_transform(parsed, T)
    return set_ctms(pdf, page_index, new)


def reset_transform(pdf: pymupdf.Document, page_index: int, indices) -> tuple[list[int], bool]:
    """Toglie rotazione, inclinazione e capovolgimento mantenendo il centro; per le
    immagini ripristina le proporzioni native a parità di area. Restituisce
    (indici, era_inclinato)."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    P = parsed.pdf_to_page
    new = {}
    skewed = False
    for o in objs:
        C0 = o.ctm
        ux = math.hypot(C0.a, C0.b)
        uy = math.hypot(C0.c, C0.d)
        if ux > 0 and uy > 0 and abs((C0.a * C0.c + C0.b * C0.d) / (ux * uy)) > 1e-3:
            skewed = True
        if o.kind == IMAGE:
            p0 = pymupdf.Point(0.5, 0.5)
            area = abs(C0.a * C0.d - C0.b * C0.c)
            r = (o.width / o.height) if o.width and o.height else (ux / uy if uy else 1)
            w = math.sqrt(area * r)
            h = math.sqrt(area / r)
            L = pymupdf.Matrix(w, 0, 0, h, 0, 0)
        else:
            p0 = rcenter(o.quad.rect) * ~P * ~C0
            L = pymupdf.Matrix(ux, 0, 0, uy, 0, 0)
        target = p0 * C0
        lp = p0 * L
        new[o.index] = pymupdf.Matrix(L.a, L.b, L.c, L.d, target.x - lp.x, target.y - lp.y)
    return set_ctms(pdf, page_index, new), skewed


# ================================================================ OBJ-26
def _entries(pdf: pymupdf.Document, xref: int, path: str) -> dict[str, str]:
    """Voci (nome -> valore) di un dizionario annidato, seguendo i riferimenti."""
    typ, val = pdf.xref_get_key(xref, path)
    if typ == "xref":
        x = int(val.split()[0])
        return {k: pdf.xref_get_key(x, k)[1] for k in pdf.xref_get_keys(x)}
    if typ == "dict":
        return {k: pdf.xref_get_key(xref, f"{path}/{k}")[1] for k in _dict_keys(val)}
    return {}


def form_is_transparency_group(pdf: pymupdf.Document, xref: int) -> bool:
    typ, val = pdf.xref_get_key(xref, "Group")
    if typ == "xref":
        val = pdf.xref_object(int(val.split()[0]), compressed=True)
    return "/Transparency" in (val or "")


def decompose(pdf: pymupdf.Document, page_index: int, index: int, force: bool = False) -> list[int]:
    """Porta gli oggetti di un oggetto composto nel contenuto della pagina (stessa resa)."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    (o,) = _check_indices(parsed, [index])
    if o.kind != FORM or not o.xref:
        raise ObjectError("L'oggetto selezionato non è un oggetto composto.")
    if form_is_transparency_group(pdf, o.xref) and not force:
        raise TransparencyGroup("L'oggetto è un gruppo di trasparenza: scomporlo può cambiarne la resa.")
    body = pdf.xref_stream(o.xref) or b""
    t, v = pdf.xref_get_key(o.xref, "Matrix")
    nums = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", v)] if t == "array" else []
    fm = pymupdf.Matrix(*nums) if len(nums) == 6 else IDENTITY
    t, v = pdf.xref_get_key(o.xref, "BBox")
    bb = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", v)] if t == "array" else []
    # risorse dell'oggetto composto -> risorse della pagina (rinominate se in conflitto)
    from .content import resource_names
    renames: dict = {}
    for cat in CATEGORIES:
        entries = _entries(pdf, o.xref, f"Resources/{cat}")
        if not entries:
            continue
        existing = resource_names(pdf, page, cat)
        for name, val in entries.items():
            if name in existing and existing[name].strip() == val.strip():
                continue
            new_name = name
            k = 1
            while new_name in existing:
                new_name = f"{name}_{k}"
                k += 1
            if new_name != name:
                renames[(cat, name)] = new_name
            existing[new_name] = val
            set_resource(pdf, page, cat, new_name, val)
    page = pdf[page_index]
    body = C._rename_ops(body, renames)
    clip = b""
    if len(bb) == 4:
        x0, y0, x1, y1 = bb
        clip = " ".join(fmt_num(z) for z in (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))).encode() + b" re W n\n"
    outer = outer_of(o)
    m = fm * o.ctm * ~outer
    parts = [b"q", fmt_matrix(m)]
    if o.wrapper is not None and o.wrapper.gs_name:
        parts.append(fmt_name(o.wrapper.gs_name) + b" gs")
    unit = b"\n".join(parts) + b"\n" + clip + body + b"\nQ"
    s, e = o.span
    before = len(parsed.objects)
    data, starts = apply_edits(parsed.data, [(s, e, _sep(unit))])
    write_content(pdf, page, data)
    prune_resources(pdf, page_index, "XObject", [o.name])
    new = parse_page(pdf, pdf[page_index])
    added = len(new.objects) - before + 1
    return list(range(o.index, o.index + max(0, added)))


class TransparencyGroup(ObjectError):
    pass


def group(pdf: pymupdf.Document, page_index: int, indices) -> list[int]:
    """Raggruppa 2 o più oggetti in un oggetto composto (Form XObject)."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    if len(objs) < 2:
        raise ObjectError("Seleziona almeno 2 oggetti da raggruppare.")
    snippet = b"\n".join(unit_bytes(parsed, o, o.ctm, IDENTITY, include_state=True) for o in objs)
    used = C._names_in(snippet)
    from .content import resource_entry
    res = []
    for cat in CATEGORIES:
        names = used.get(cat)
        if not names:
            continue
        inner = []
        for n in sorted(names):
            typ, val = resource_entry(pdf, page, cat, n)
            if typ != "null":
                inner.append(fmt_name(n).decode() + " " + val)
        if inner:
            res.append(f"/{cat}<<" + " ".join(inner) + ">>")
    mb = page.mediabox
    xref = pdf.get_new_xref()
    pdf.update_object(xref, f"<</Type/XObject/Subtype/Form/BBox[{fmt_num(mb.x0)} {fmt_num(mb.y0)} "
                            f"{fmt_num(mb.x1)} {fmt_num(mb.y1)}]/Resources<<{''.join(res)}>>>>")
    pdf.update_stream(xref, snippet, new=True)
    from .content import resource_names
    existing = resource_names(pdf, page, "XObject")
    k = 0
    while f"FoGrp{k}" in existing:
        k += 1
    name = f"FoGrp{k}"
    set_resource(pdf, page, "XObject", name, f"{xref} 0 R")
    page = pdf[page_index]
    top = objs[-1]
    from .content import ctm_at
    pos = top.span[1]
    outer = ctm_at(parsed, pos) if pos < len(parsed.data) else parsed.end_ctm
    call = b"q\n" + fmt_matrix(~outer) + b"\n" + fmt_name(name) + b" Do\nQ"
    edits = [(o.span[0], o.span[1], b"\n") for o in objs[:-1]]
    edits.append((top.span[0], top.span[1], _sep(call)))
    data, _ = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    new = parse_page(pdf, pdf[page_index])
    for ob in new.objects:
        if ob.kind == FORM and ob.name == name:
            return [ob.index]
    return []


# ================================================================ OBJ-25
NONE = "none"


def _color_op(color, stroke: bool) -> bytes:
    vals = " ".join(fmt_num(max(0.0, min(1.0, float(v)))) for v in color).encode()
    op = {1: b"G", 3: b"RG", 4: b"K"}.get(len(color))
    if op is None:
        raise ObjectError("Colore non valido.")
    return vals + b" " + (op if stroke else op.lower())


def restyle_paths(pdf: pymupdf.Document, page_index: int, indices, stroke=None, fill=None,
                  width: float | None = None, dash: list | None = None) -> list[int]:
    """Cambia colore di tratto/riempimento, spessore e tratteggio dei tracciati.

    `stroke`/`fill`: None = invariato, NONE = nessuno, tupla di 1 (grigio), 3 (RGB)
    o 4 (CMYK) componenti 0–1."""
    from .content import PATH
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    if any(o.kind != PATH for o in objs):
        raise ObjectError("Lo stile si può cambiare solo sui tracciati.")
    if width is not None and width < 0:
        raise ObjectError("Lo spessore non può essere negativo.")
    edits = []
    for o in objs:
        paint = o.paint
        has_fill = paint in ("f", "F", "f*", "B", "B*", "b", "b*")
        has_stroke = paint in ("S", "s", "B", "B*", "b", "b*")
        evenodd = paint.endswith("*")
        close = paint in ("s", "b", "b*")
        if stroke == NONE:
            has_stroke = False
        elif stroke is not None:
            has_stroke = True
        if fill == NONE:
            has_fill = False
        elif fill is not None:
            has_fill = True
        if has_fill and has_stroke:
            new_paint = "B*" if evenodd else "B"
        elif has_fill:
            new_paint = "f*" if evenodd else "f"
        elif has_stroke:
            new_paint = "S"
        else:
            new_paint = "n"
        body = parsed.data[o.start:o.end]
        drop = set()
        if width is not None:
            drop |= {"w"}
        if dash is not None:
            drop |= {"d"}
        if stroke not in (None, NONE):
            drop |= {"RG", "G", "K", "SC", "SCN", "CS"}
        if fill not in (None, NONE):
            drop |= {"rg", "g", "k", "sc", "scn", "cs"}
        if drop:
            from .content import tokenize_ops
            cut = [(op.start, op.end, b"") for op in tokenize_ops(body) if op.name in drop]
            body, _ = apply_edits(body, cut)
        if not body.rstrip().endswith(paint.encode()):
            raise ObjectError("Tracciato non modificabile.")
        body = body.rstrip()[: -len(paint)] + (b"h " if close else b"") + new_paint.encode()
        state = []
        if stroke not in (None, NONE):
            state.append(_color_op(stroke, True))
        if fill not in (None, NONE):
            state.append(_color_op(fill, False))
        if width is not None:
            scale = abs(o.ctm.a * o.ctm.d - o.ctm.b * o.ctm.c) ** 0.5 or 1.0
            state.append(fmt_num(width / scale).encode() + b" w")
        if dash is not None:
            state.append(b"[" + b" ".join(fmt_num(v).encode() for v in dash) + b"] 0 d")
        full = b"\n".join(state + [body]) if state else body
        s, e = o.span
        edits.append((s, e, _sep(unit_bytes(parsed, o, o.ctm, outer_of(o), body=full))))
    data, _ = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    return [o.index for o in objs]
