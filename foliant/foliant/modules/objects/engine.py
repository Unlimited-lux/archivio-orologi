"""Operazioni sugli oggetti della pagina (logica pura, senza interfaccia).

Tutte le funzioni che modificano ricevono il documento del motore (`pdf`) e
vanno chiamate dentro `Document.change()`. Gli oggetti sono identificati dal
loro indice nell'ordine di disegno della pagina (vedi `content.parse_page`).
Le geometrie sono in coordinate pagina del motore (pagina non ruotata).
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass

import pymupdf

from .content import (
    FORM, IMAGE, PATH, SHADING, Name, PageObject, ParsedPage, fmt_matrix, fmt_name, fmt_num, matrix_close,
    page_content, parse_page, rcenter, Pt, resource_names, resources_value, tokenize_ops,
)

MIN_SIZE = 1.0          # punti
CASCADE = 20.0          # scostamento a cascata (punti)
PASTE_OFFSET = 10.0


class ObjectError(Exception):
    """Errore con messaggio per l'utente."""


IDENTITY = pymupdf.Matrix(1, 0, 0, 1, 0, 0)


# =================================================================== utilità
def objects(pdf: pymupdf.Document, page_index: int) -> ParsedPage:
    page = pdf[page_index]
    return parse_page(pdf, page)


def union_rect(objs: list[PageObject]) -> pymupdf.Rect:
    r = None
    for o in objs:
        q = o.quad.rect
        r = q if r is None else r | q
    return r if r is not None else pymupdf.Rect()


def visual_rect(objs: list[PageObject]) -> pymupdf.Rect:
    r = None
    for o in objs:
        q = o.rect if not o.rect.is_empty else o.quad.rect
        r = pymupdf.Rect(q) if r is None else r | q
    return r if r is not None else pymupdf.Rect()


def write_content(pdf: pymupdf.Document, page: pymupdf.Page, data: bytes) -> None:
    """Sostituisce /Contents con un unico stream nuovo (gli stream condivisi restano intatti)."""
    xref = pdf.get_new_xref()
    pdf.update_object(xref, "<<>>")
    pdf.update_stream(xref, data, new=True)
    pdf.xref_set_key(page.xref, "Contents", f"{xref} 0 R")


def apply_edits(data: bytes, edits: list[tuple[int, int, bytes]]) -> tuple[bytes, list[int]]:
    """Applica sostituzioni (start, end, nuovi_byte) non sovrapposte.

    Restituisce i nuovi dati e, per ogni modifica, la posizione iniziale del
    testo inserito nei nuovi dati."""
    order = sorted(range(len(edits)), key=lambda k: (edits[k][0], edits[k][1]))
    out = bytearray()
    pos = 0
    starts = [0] * len(edits)
    for k in order:
        s, e, new = edits[k]
        if s < pos:
            raise ObjectError("Modifiche sovrapposte nel contenuto della pagina.")
        out += data[pos:s]
        starts[k] = len(out)
        out += new
        pos = e
    out += data[pos:]
    return bytes(out), starts


def _sep(new: bytes) -> bytes:
    return b"\n" + new + b"\n"


def unit_bytes(parsed: ParsedPage, obj: PageObject, new_ctm: pymupdf.Matrix, outer: pymupdf.Matrix, *,
               gs_name: str | None | bool = True, clip_unit: tuple | None | bool = True,
               include_state: bool = False, body: bytes | None = None) -> bytes:
    """Ricrea l'oggetto come `q cm [stato] [gs] [ritaglio] OGGETTO Q`.

    `gs_name`/`clip_unit` = True conservano quelli del contenitore esistente."""
    w = obj.wrapper
    if gs_name is True:
        gs_name = w.gs_name if w is not None else None
    if clip_unit is True:
        clip_unit = w.clip_unit if w is not None else None
    m = new_ctm * ~outer
    parts = [b"q", fmt_matrix(m)]
    if include_state:
        sb = obj.state.state_bytes()
        if sb:
            parts.append(sb)
    if gs_name:
        parts.append(fmt_name(gs_name) + b" gs")
    if clip_unit:
        x, y, cw, ch = clip_unit
        parts.append(" ".join(fmt_num(v) for v in (x, y, cw, ch)).encode() + b" re W n")
    original = parsed.data[obj.start:obj.end]
    parts.append(body if body is not None else original)
    parts.append(b"Q")
    if w is None and not include_state:
        # gli operatori di stato interni all'oggetto valevano anche per i successivi
        leak = [original[op.start:op.end] for op in tokenize_ops(original) if op.name in _LEAK_OPS]
        if leak:
            parts.append(b"\n".join(leak))
    return b"\n".join(parts)


_LEAK_OPS = {"w", "J", "j", "M", "d", "ri", "i", "gs", "CS", "cs", "SC", "SCN", "sc", "scn", "G", "g",
             "RG", "rg", "K", "k"}


def page_to_pdf_transform(parsed: ParsedPage, T: pymupdf.Matrix) -> pymupdf.Matrix:
    """Trasformazione in spazio pagina -> equivalente in spazio pdf."""
    P = parsed.pdf_to_page
    return P * T * ~P


def outer_of(obj: PageObject) -> pymupdf.Matrix:
    return obj.wrapper.outer_ctm if obj.wrapper is not None else obj.ctm


def _check_indices(parsed: ParsedPage, indices) -> list[PageObject]:
    out = []
    for i in sorted(set(indices)):
        if not 0 <= i < len(parsed.objects):
            raise ObjectError("L'oggetto selezionato non esiste più nella pagina.")
        out.append(parsed.objects[i])
    if not out:
        raise ObjectError("Nessun oggetto selezionato.")
    return out


# ============================================================ trasformazioni
def _block_end(parsed: ParsedPage, q_index: int) -> int | None:
    """Posizione subito dopo il Q che chiude il q all'indice dato."""
    depth = 0
    for op in parsed.ops[q_index:]:
        if op.name == "q":
            depth += 1
        elif op.name == "Q":
            depth -= 1
            if depth == 0:
                return op.end
    return None


def _transform_edits(parsed: ParsedPage, o: PageObject, Tp: pymupdf.Matrix,
                     page_rect: pymupdf.Rect) -> list[tuple[int, int, bytes]]:
    """Modifiche per trasformare l'oggetto. Se l'oggetto è ritagliato da un tracciato
    definito fuori da sé (anche condiviso con altri oggetti), il ritaglio lo segue:
    l'oggetto esce dal blocco q…Q del ritaglio e riceve una copia trasformata del
    tracciato; gli altri oggetti restano invariati (OBJ-08)."""
    new_ctm = o.ctm * Tp
    s, e = o.span
    in_place = [(s, e, _sep(unit_bytes(parsed, o, new_ctm, outer_of(o))))]
    clips = list(o.state.clips)
    if o.wrapper is not None:
        wq = next((k for k, op in enumerate(parsed.ops) if op.start == o.wrapper.start and op.name == "q"), None)
        clips = [c for c in clips if c[4] != wq]
    grown = pymupdf.Rect(page_rect.x0 - 1, page_rect.y0 - 1, page_rect.x1 + 1, page_rect.y1 + 1)
    local = [c for c in clips if not c[2].contains(page_rect) and not (grown.contains(c[2]) and
                                                                       c[2].contains(page_rect))]
    if not local:
        return in_place
    depth = min(c[3] for c in local)
    if depth < 1:
        return in_place          # ritaglio al livello più esterno: non si può uscirne
    q_index = min(c[4] for c in local if c[3] == depth)
    end = _block_end(parsed, q_index)
    if end is None or end <= e:
        return in_place
    from .content import ctm_at
    cur = ctm_at(parsed, end) if end < len(parsed.data) else parsed.end_ctm
    parts = [b"q"]
    for cbytes, cctm, _r, cdepth, _qi in clips:
        if cdepth < depth:
            continue            # ritaglio esterno al blocco: vale ancora nel punto d'arrivo
        target = cctm * Tp
        parts.append(fmt_matrix(target * ~cur))
        parts.append(cbytes)
        cur = target
    parts.append(unit_bytes(parsed, o, new_ctm, cur, include_state=True))
    parts.append(b"Q")
    return [(s, e, b"\n"), (end, end, _sep(b"\n".join(parts)))]


def _apply_and_locate(pdf, page, parsed: ParsedPage, edits_per_obj: list) -> list[int]:
    """Applica le modifiche e restituisce i nuovi indici degli oggetti modificati."""
    flat = [ed for eds in edits_per_obj for ed in eds]
    data, starts = apply_edits(parsed.data, flat)
    write_content(pdf, page, data)
    anchors = []
    k = 0
    for eds in edits_per_obj:
        ed = eds[-1]
        anchors.append((starts[k + len(eds) - 1], len(ed[2])))
        k += len(eds)
    new = parse_page(pdf, page)
    out = []
    for a, n in anchors:
        for ob in new.objects:
            if a <= ob.span[0] < a + max(n, 1):
                out.append(ob.index)
                break
    return out


def transform_objects(pdf: pymupdf.Document, page_index: int, indices, T: pymupdf.Matrix) -> list[int]:
    """Applica la trasformazione T (spazio pagina) agli oggetti indicati."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    Tp = page_to_pdf_transform(parsed, T)
    pr = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    edits = [_transform_edits(parsed, o, Tp, pr) for o in objs]
    if all(len(ed) == 1 for ed in edits):
        data, _starts = apply_edits(parsed.data, [ed[0] for ed in edits])
        write_content(pdf, page, data)
        return [o.index for o in objs]
    return _apply_and_locate(pdf, pdf[page_index], parsed, edits)


def translate(pdf, page_index: int, indices, dx: float, dy: float) -> list[int]:
    return transform_objects(pdf, page_index, indices, pymupdf.Matrix(1, 0, 0, 1, dx, dy))


def clamp_translation(rect: pymupdf.Rect, page_rect: pymupdf.Rect, dx: float, dy: float,
                      keep: float = 10.0) -> tuple[float, float]:
    """Limita lo spostamento perché restino visibili almeno `keep` punti dell'oggetto."""
    kx = min(keep, rect.width)
    ky = min(keep, rect.height)
    nx0, nx1 = rect.x0 + dx, rect.x1 + dx
    ny0, ny1 = rect.y0 + dy, rect.y1 + dy
    if nx1 < page_rect.x0 + kx:
        dx += page_rect.x0 + kx - nx1
    if nx0 > page_rect.x1 - kx:
        dx -= nx0 - (page_rect.x1 - kx)
    if ny1 < page_rect.y0 + ky:
        dy += page_rect.y0 + ky - ny1
    if ny0 > page_rect.y1 - ky:
        dy -= ny0 - (page_rect.y1 - ky)
    return dx, dy


def about(center: pymupdf.Point, M: pymupdf.Matrix) -> pymupdf.Matrix:
    return pymupdf.Matrix(1, 0, 0, 1, -center.x, -center.y) * M * pymupdf.Matrix(1, 0, 0, 1, center.x, center.y)


def rotation_matrix(angle_ccw: float) -> pymupdf.Matrix:
    """Rotazione visiva antioraria (gradi) nello spazio pagina (y verso il basso)."""
    a = math.radians(angle_ccw)
    c, s = math.cos(a), math.sin(a)
    # valori esatti per multipli di 90°
    if abs(angle_ccw % 90) < 1e-9:
        c, s = round(c), round(s)
    return pymupdf.Matrix(c, -s, s, c, 0, 0)


def rotate(pdf, page_index: int, indices, angle_ccw: float) -> list[int]:
    parsed = objects(pdf, page_index)
    objs = _check_indices(parsed, indices)
    center = rcenter(union_rect(objs)) if len(objs) > 1 else rcenter(objs[0].quad.rect)
    return transform_objects(pdf, page_index, indices, about(center, rotation_matrix(angle_ccw)))


def flip(pdf, page_index: int, indices, horizontal: bool) -> list[int]:
    """Specchia rispetto all'asse verticale (orizzontale=True) o orizzontale della vista."""
    page = pdf[page_index]
    rot = page.rotation % 180
    if rot == 90:
        horizontal = not horizontal
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    center = rcenter(union_rect(objs))
    M = pymupdf.Matrix(-1, 0, 0, 1, 0, 0) if horizontal else pymupdf.Matrix(1, 0, 0, -1, 0, 0)
    return transform_objects(pdf, page_index, indices, about(center, M))


def resize_matrix(old: pymupdf.Rect, new: pymupdf.Rect) -> pymupdf.Matrix:
    if old.width <= 0 or old.height <= 0:
        raise ObjectError("L'oggetto non ha dimensioni ridimensionabili.")
    if new.width < MIN_SIZE or new.height < MIN_SIZE:
        raise ObjectError("La dimensione minima è 1 pt.")
    sx = new.width / old.width
    sy = new.height / old.height
    return (pymupdf.Matrix(1, 0, 0, 1, -old.x0, -old.y0) * pymupdf.Matrix(sx, 0, 0, sy, 0, 0)
            * pymupdf.Matrix(1, 0, 0, 1, new.x0, new.y0))


def resize(pdf, page_index: int, indices, new_rect: pymupdf.Rect) -> list[int]:
    parsed = objects(pdf, page_index)
    objs = _check_indices(parsed, indices)
    old = union_rect(objs)
    return transform_objects(pdf, page_index, indices, resize_matrix(old, pymupdf.Rect(new_rect)))


def object_angle(obj: PageObject) -> float:
    """Angolo visivo (gradi, antiorario) dell'asse x dell'oggetto, in (-180, 180]."""
    q = obj.quad
    v = q.ur - q.ul
    ang = -math.degrees(math.atan2(v.y, v.x))
    if ang <= -180:
        ang += 360
    return round(ang, 6) + 0.0


def object_size(obj: PageObject) -> tuple[float, float]:
    q = obj.quad
    return abs(q.ur - q.ul), abs(q.ll - q.ul)


# ================================================================ opacità
def _find_or_add_extgstate(pdf: pymupdf.Document, page: pymupdf.Page, alpha: float) -> str:
    alpha = round(alpha, 4)
    existing = resource_names(pdf, page, "ExtGState")
    for name, val in existing.items():
        text = val
        if val.strip().endswith(" R"):
            try:
                text = pdf.xref_object(int(val.split()[0]), compressed=True)
            except Exception:
                continue
        keys = set(re.findall(r"/([A-Za-z]+)", text))
        if not keys <= {"Type", "ExtGState", "CA", "ca"}:
            continue
        vals = {k: float(v) for k, v in re.findall(r"/(CA|ca)\s*([-+]?(?:\d+\.?\d*|\.\d+))", text)}
        if abs(vals.get("CA", -1) - alpha) < 1e-4 and abs(vals.get("ca", -1) - alpha) < 1e-4:
            return name
    xref = pdf.get_new_xref()
    a = fmt_num(alpha)
    pdf.update_object(xref, f"<</Type/ExtGState/CA {a}/ca {a}>>")
    name = unique_name(existing.keys(), "FoGS")
    set_resource(pdf, page, "ExtGState", name, f"{xref} 0 R")
    return name


def unique_name(existing, prefix: str) -> str:
    existing = set(existing)
    n = 0
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def set_opacity(pdf, page_index: int, indices, alpha: float) -> list[int]:
    if not 0 <= alpha <= 1:
        raise ObjectError("L'opacità deve essere compresa tra 0 e 100 %.")
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    name = _find_or_add_extgstate(pdf, page, alpha)
    edits = []
    for o in objs:
        s, e = o.span
        edits.append((s, e, _sep(unit_bytes(parsed, o, o.ctm, outer_of(o), gs_name=name))))
    data, _ = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    return [o.index for o in objs]


# ================================================================ risorse
def _resources_xref(pdf: pymupdf.Document, page: pymupdf.Page) -> tuple[int, str]:
    """(xref, percorso) del dizionario /Resources proprio della pagina (lo crea se ereditato)."""
    holder, key = resources_value(pdf, page)
    typ, val = pdf.xref_get_key(holder, key)
    if holder != page.xref:
        # risorse ereditate: copia nella pagina per non toccare le altre pagine
        pdf.xref_set_key(page.xref, "Resources", val if typ != "null" else "<<>>")
        typ, val = pdf.xref_get_key(page.xref, "Resources")
    if typ == "null":
        pdf.xref_set_key(page.xref, "Resources", "<<>>")
        typ, val = pdf.xref_get_key(page.xref, "Resources")
    if typ == "xref":
        return int(val.split()[0]), ""
    return page.xref, "Resources"


def set_resource(pdf: pymupdf.Document, page: pymupdf.Page, category: str, name: str, value: str) -> None:
    rx, path = _resources_xref(pdf, page)
    cat_path = f"{path}/{category}" if path else category
    typ, val = pdf.xref_get_key(rx, cat_path)
    if typ == "xref":
        pdf.xref_set_key(int(val.split()[0]), fmt_name(name).decode()[1:], value)
    else:
        if typ != "dict":
            pdf.xref_set_key(rx, cat_path, "<<>>")
        pdf.xref_set_key(rx, f"{cat_path}/{fmt_name(name).decode()[1:]}", value)


def _resources_shared(pdf: pymupdf.Document, page: pymupdf.Page) -> bool:
    holder, key = resources_value(pdf, page)
    if holder != page.xref:
        return True
    typ, val = pdf.xref_get_key(page.xref, "Resources")
    if typ != "xref":
        return False
    for i in range(pdf.page_count):
        if i == page.number:
            continue
        t, v = pdf.xref_get_key(pdf.page_xref(i), "Resources")
        if t == "xref" and v == val:
            return True
    return False


def _drop_resource_names(pdf: pymupdf.Document, page: pymupdf.Page, category: str, names) -> None:
    """Toglie nomi aggiunti dal motore e non usati dal contenuto."""
    if not names:
        return
    used = parse_page(pdf, page).names_used.get(category, set())
    holder, key = resources_value(pdf, page)
    typ, val = pdf.xref_get_key(holder, key)
    if typ == "xref":
        rx, cat_path = int(val.split()[0]), category
    else:
        rx, cat_path = holder, f"{key}/{category}"
    t, v = pdf.xref_get_key(rx, cat_path)
    for name in names:
        if name in used:
            continue
        k = fmt_name(name).decode()[1:]
        if t == "xref":
            pdf.xref_set_key(int(v.split()[0]), k, "null")
        elif t == "dict":
            pdf.xref_set_key(rx, f"{cat_path}/{k}", "null")


def prune_resources(pdf: pymupdf.Document, page_index: int, category: str, names) -> None:
    """Toglie dalle risorse della pagina i nomi non più usati (solo se non condivise)."""
    page = pdf[page_index]
    if _resources_shared(pdf, page):
        return
    parsed = parse_page(pdf, page)
    used = parsed.names_used.get(category, set())
    rx, path = _resources_xref(pdf, page)
    cat_path = f"{path}/{category}" if path else category
    typ, val = pdf.xref_get_key(rx, cat_path)
    for name in names:
        if not name or name in used:
            continue
        key = fmt_name(name).decode()[1:]
        if typ == "xref":
            pdf.xref_set_key(int(val.split()[0]), key, "null")
        elif typ == "dict":
            pdf.xref_set_key(rx, f"{cat_path}/{key}", "null")


# ================================================================ eliminazione
def delete_objects(pdf, page_index: int, indices) -> int:
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    edits = [(o.span[0], o.span[1], b"\n") for o in objs]
    data, _ = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    prune_resources(pdf, page_index, "XObject", [o.name for o in objs if o.kind in (IMAGE, FORM) and o.name])
    return len(objs)


# ======================================================== ordine di disegno
FRONT, FORWARD, BACKWARD, BACK = "front", "forward", "backward", "back"


def _overlaps(a: PageObject, b: PageObject) -> bool:
    r = a.rect & b.rect
    return not r.is_empty and r.width > 0 and r.height > 0


def reorder(pdf, page_index: int, indices, where: str) -> list[int] | None:
    """Cambia l'ordine di sovrapposizione. None se non cambia nulla."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    sel = _check_indices(parsed, indices)
    sel_idx = {o.index for o in sel}
    others = [o for o in parsed.objects if o.index not in sel_idx]
    if where == FRONT:
        if all(o.index >= len(parsed.objects) - len(sel) for o in sel):
            return None
        pos = len(parsed.data)
    elif where == BACK:
        if all(o.index < len(sel) for o in sel):
            return None
        pos = 0
    elif where == FORWARD:
        top = max(sel_idx)
        cand = [o for o in others if o.index > top and any(_overlaps(o, s) for s in sel)]
        if not cand:
            return None
        pos = cand[0].span[1]
    elif where == BACKWARD:
        bottom = min(sel_idx)
        cand = [o for o in others if o.index < bottom and any(_overlaps(o, s) for s in sel)]
        if not cand:
            return None
        pos = cand[-1].span[0]
    else:
        raise ValueError(where)
    # posizione d'inserimento non dentro un oggetto selezionato
    from .content import ctm_at
    outer = ctm_at(parsed, pos) if pos < len(parsed.data) else parsed.end_ctm
    if pos == 0:
        outer = IDENTITY
    units = b"\n".join(unit_bytes(parsed, o, o.ctm, outer, include_state=True) for o in sel)
    edits = [(o.span[0], o.span[1], b"\n") for o in sel]
    edits.append((pos, pos, _sep(units)))
    data, starts = apply_edits(parsed.data, edits)
    write_content(pdf, page, data)
    new = parse_page(pdf, page)
    ins = starts[-1]
    found = [o.index for o in new.objects if ins <= o.span[0] < ins + len(units) + 2]
    return found or None


# ================================================================ immagini
def natural_size(data: bytes) -> tuple[float, float, int, int]:
    """Dimensione naturale (pt) e pixel di un'immagine, dalla densità dichiarata."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            dpi = im.info.get("dpi")
            orient = 1
            try:
                orient = im.getexif().get(0x0112, 1) or 1
            except Exception:
                pass
    except Exception:
        try:
            pix = pymupdf.Pixmap(data)
        except Exception as e:
            raise ObjectError("Il file non è un'immagine valida o è danneggiato.") from e
        w, h, dpi, orient = pix.width, pix.height, (pix.xres, pix.yres), 1
    dx = dy = 96.0
    if dpi:
        try:
            ddx, ddy = (round(float(v)) if abs(float(v) - round(float(v))) < 0.1 else float(v) for v in dpi[:2])
            if 50 <= ddx <= 2400 and 50 <= ddy <= 2400:
                dx, dy = ddx, ddy
        except (TypeError, ValueError, IndexError):
            pass
    if orient in (5, 6, 7, 8):
        w, h, dx, dy = h, w, dy, dx
    return w * 72.0 / dx, h * 72.0 / dy, w, h


JPEG_KEEP = {0xE0, 0xE2, 0xEE}  # APP0 (JFIF), APP2 (ICC), APP14 (Adobe)


def strip_jpeg_metadata(data: bytes) -> tuple[bytes, int]:
    """Rimuove EXIF/XMP/commenti da un JPEG senza ricodificare. Restituisce (dati, orientamento)."""
    orient = 1
    if not data.startswith(b"\xff\xd8"):
        return data, orient
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            orient = im.getexif().get(0x0112, 1) or 1
    except Exception:
        pass
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return data, orient
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xDA:  # SOS: da qui in poi i dati compressi
            out += data[i:]
            return bytes(out), orient
        if 0xD0 <= marker <= 0xD9 or marker == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + seg_len]
        if (0xE0 <= marker <= 0xEF and marker not in JPEG_KEEP) or marker == 0xFE:
            pass
        else:
            out += seg
        i += 2 + seg_len
    return data, orient


def prepare_image(data: bytes) -> tuple[bytes, int]:
    """Valida un'immagine e la prepara all'inserimento. Restituisce (dati, orientamento EXIF)."""
    if not data:
        raise ObjectError("Il file immagine è vuoto.")
    if data.startswith(b"\xff\xd8"):
        data, orient = strip_jpeg_metadata(data)
    else:
        orient = 1
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            fmt = (im.format or "").upper()
            n_frames = getattr(im, "n_frames", 1)
            if fmt in ("WEBP", "GIF", "TIFF", "BMP", "ICO", "TGA", "PPM") or n_frames > 1 or im.mode in ("P", "LA", "PA") and fmt != "PNG":
                if fmt not in ("PNG", "JPEG"):
                    im.seek(0)
                    buf = io.BytesIO()
                    conv = im.convert("RGBA") if ("A" in im.mode or "transparency" in im.info) else im.convert("RGB")
                    conv.save(buf, "PNG")
                    data = buf.getvalue()
    except ObjectError:
        raise
    except Exception:
        try:
            pymupdf.Pixmap(data)
        except Exception as e:
            raise ObjectError("Il file non è un'immagine valida o è danneggiato.\n"
                              "Formati accettati: PNG, JPEG, TIFF, BMP, GIF, WebP.") from e
    return data, orient


def _orientation_matrix(orient: int) -> pymupdf.Matrix:
    """Matrice (spazio unitario dell'immagine) che raddrizza l'orientamento EXIF."""
    # immagine unitaria: x verso destra, y verso l'alto
    mats = {
        1: (1, 0, 0, 1, 0, 0),
        2: (-1, 0, 0, 1, 1, 0),
        3: (-1, 0, 0, -1, 1, 1),
        4: (1, 0, 0, -1, 0, 1),
        5: (0, -1, -1, 0, 1, 1),
        6: (0, -1, 1, 0, 0, 1),
        7: (0, 1, 1, 0, 0, 0),
        8: (0, 1, -1, 0, 1, 0),
    }
    return pymupdf.Matrix(*mats.get(orient, mats[1]))


def add_image_resource(pdf: pymupdf.Document, page: pymupdf.Page, data: bytes | None = None,
                       xref: int = 0) -> tuple[str, int]:
    """Crea (o riusa) un image XObject nelle risorse della pagina senza toccarne il contenuto."""
    _resources_xref(pdf, page)  # risorse proprie della pagina
    contents = pdf.xref_get_key(page.xref, "Contents")
    before = resource_names(pdf, page, "XObject")
    try:
        if xref:
            new_xref = page.insert_image(page.rect, xref=xref)
        else:
            new_xref = page.insert_image(page.rect, stream=data)
    except Exception as e:
        raise ObjectError("Impossibile leggere l'immagine: il file è danneggiato o in un formato non supportato.") from e
    finally:
        if contents[0] == "null":
            pdf.xref_set_key(page.xref, "Contents", "null")
        else:
            pdf.xref_set_key(page.xref, "Contents", contents[1])
    after = resource_names(pdf, page, "XObject")
    for name, val in after.items():
        if val.strip() == f"{new_xref} 0 R" and (name not in before or before[name] == val):
            return name, new_xref
    for name, val in after.items():
        if val.strip() == f"{new_xref} 0 R":
            return name, new_xref
    raise ObjectError("Impossibile aggiungere l'immagine alla pagina.")


def quad_matrix(tl: pymupdf.Point, tr: pymupdf.Point, bl: pymupdf.Point) -> pymupdf.Matrix:
    """Matrice che porta il quadrato unitario dell'immagine sui tre angoli dati (spazio pdf)."""
    br = tr + (bl - tl)
    return pymupdf.Matrix(br.x - bl.x, br.y - bl.y, tl.x - bl.x, tl.y - bl.y, bl.x, bl.y)


def view_rect_to_ctm(page: pymupdf.Page, P: pymupdf.Matrix, center: pymupdf.Point, w: float, h: float) -> pymupdf.Matrix:
    """CTM di un'immagine w×h (come appare nella vista) centrata in `center` (spazio pagina)."""
    rot = page.rotation_matrix
    derot = page.derotation_matrix
    cv = center * rot
    tl = pymupdf.Point(cv.x - w / 2, cv.y - h / 2) * derot
    tr = pymupdf.Point(cv.x + w / 2, cv.y - h / 2) * derot
    bl = pymupdf.Point(cv.x - w / 2, cv.y + h / 2) * derot
    iP = ~P
    return quad_matrix(tl * iP, tr * iP, bl * iP)


def visible_size(page: pymupdf.Page) -> tuple[float, float]:
    r = page.rect  # già ruotato come nella vista
    return r.width, r.height


def insert_images(pdf: pymupdf.Document, page_index: int, images: list[bytes], center: pymupdf.Point | None = None,
                  rect: pymupdf.Rect | None = None) -> list[int]:
    """Inserisce una o più immagini (a cascata). Restituisce gli indici dei nuovi oggetti."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    prepared = [prepare_image(d) for d in images]
    vw, vh = visible_size(page)
    if center is None:
        center = pymupdf.Point(page.cropbox.width / 2, page.cropbox.height / 2)
    units = []
    for k, (data, orient) in enumerate(prepared):
        w, h, _pw, _ph = natural_size(data)
        if orient in (5, 6, 7, 8):
            w, h = h, w
        if rect is not None and k == 0:
            r = pymupdf.Rect(rect) * page.rotation_matrix
            r.normalize()
            s = min(r.width / w, r.height / h)
            w, h = w * s, h * s
            c = rcenter(pymupdf.Rect(rect))
        else:
            s = min(1.0, 0.9 * vw / w, 0.9 * vh / h)
            w, h = w * s, h * s
            c = pymupdf.Point(center.x, center.y)
            if k:
                # a cascata nella direzione della vista
                vec = pymupdf.Point(CASCADE * k, CASCADE * k)
                c = (c * page.rotation_matrix + vec) * page.derotation_matrix
        name, _x = add_image_resource(pdf, page, data)
        ctm = _orientation_matrix(orient) * view_rect_to_ctm(page, parsed.pdf_to_page, c, w, h)
        units.append(b"q\n" + fmt_matrix(ctm * ~parsed.end_ctm) + b"\n" + fmt_name(name) + b" Do\nQ")
    body = b"\n".join(units)
    data = parsed.data + _sep(body)
    write_content(pdf, page, data)
    n0 = len(parsed.objects)
    return list(range(n0, n0 + len(units)))


def _reoriented_probe(data: bytes, orient: int) -> bytes:
    return data  # natural_size gestisce già lo scambio di larghezza e altezza


# ---------------------------------------------------------------- ritaglio
def set_crop(pdf, page_index: int, index: int, clip_unit: tuple | None) -> list[int]:
    """Ritaglio non distruttivo (x, y, w, h nello spazio unitario dell'immagine) o None per toglierlo."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    (o,) = _check_indices(parsed, [index])
    if o.kind != IMAGE:
        raise ObjectError("Si possono ritagliare solo le immagini.")
    if clip_unit is not None:
        x, y, w, h = clip_unit
        x0, y0 = max(0.0, x), max(0.0, y)
        x1, y1 = min(1.0, x + w), min(1.0, y + h)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            raise ObjectError("L'area da ritagliare è vuota.")
        if o.width and o.height and ((x1 - x0) * o.width < 1 or (y1 - y0) * o.height < 1):
            raise ObjectError("L'area ritagliata è inferiore a 1 pixel.")
        clip_unit = (x0, y0, x1 - x0, y1 - y0)
        if clip_unit == (0.0, 0.0, 1.0, 1.0):
            clip_unit = None
    s, e = o.span
    data, _ = apply_edits(parsed.data, [(s, e, _sep(unit_bytes(parsed, o, o.ctm, outer_of(o), clip_unit=clip_unit)))])
    write_content(pdf, page, data)
    return [o.index]


def crop_unit_from_rect(obj: PageObject, rect: pymupdf.Rect, P: pymupdf.Matrix) -> tuple:
    """Converte un rettangolo (spazio pagina) nel ritaglio unitario dell'immagine."""
    inv = ~(obj.ctm * P)
    pts = [p * inv for p in (rect.tl, rect.tr, rect.bl, rect.br)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def image_pixmap(pdf: pymupdf.Document, xref: int) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pdf, xref)
    smask = 0
    try:
        t, v = pdf.xref_get_key(xref, "SMask")
        if t == "xref":
            smask = int(v.split()[0])
    except Exception:
        smask = 0
    if smask:
        try:
            mask = pymupdf.Pixmap(pdf, smask)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace and pix.colorspace.n > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            pix = pymupdf.Pixmap(pix, mask)
        except Exception:
            pass
    elif pix.colorspace and pix.colorspace.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix


def apply_crop(pdf, page_index: int, index: int) -> list[int]:
    """Ritaglio definitivo: nuova immagine con i soli pixel visibili."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    (o,) = _check_indices(parsed, [index])
    if o.kind != IMAGE or o.inline or not o.xref:
        raise ObjectError("Il ritaglio definitivo è disponibile solo per le immagini.")
    w = o.wrapper
    if w is None or not w.clip_unit:
        raise ObjectError("L'immagine non è ritagliata.")
    x, y, cw, ch = w.clip_unit
    pix = image_pixmap(pdf, o.xref)
    W, H = pix.width, pix.height
    px0 = max(0, int(math.floor(x * W + 1e-6)))
    px1 = min(W, int(math.ceil((x + cw) * W - 1e-6)))
    py0 = max(0, int(math.floor((1 - (y + ch)) * H + 1e-6)))
    py1 = min(H, int(math.ceil((1 - y) * H - 1e-6)))
    if px1 - px0 < 1 or py1 - py0 < 1:
        raise ObjectError("L'area ritagliata è inferiore a 1 pixel.")
    from PIL import Image
    mode = "RGBA" if pix.alpha else ("L" if pix.n == 1 else "RGB")
    if pix.n - pix.alpha not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        mode = "RGBA" if pix.alpha else "RGB"
    im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    im = im.crop((px0, py0, px1, py1))
    buf = io.BytesIO()
    filt = pdf.xref_get_key(o.xref, "Filter")[1]
    if "DCT" in filt and mode != "RGBA":
        im.save(buf, "JPEG", quality=90)
    else:
        im.save(buf, "PNG")
    name, _nx = add_image_resource(pdf, page, buf.getvalue())
    # nuova matrice: il quadrato unitario va sull'area dei pixel tenuti
    ux0, ux1 = px0 / W, px1 / W
    uy0, uy1 = 1 - py1 / H, 1 - py0 / H
    sub = pymupdf.Matrix(ux1 - ux0, 0, 0, uy1 - uy0, ux0, uy0)
    new_ctm = sub * o.ctm
    body = fmt_name(name) + b" Do"
    s, e = o.span
    data, _ = apply_edits(parsed.data, [(s, e, _sep(unit_bytes(parsed, o, new_ctm, outer_of(o), clip_unit=None, body=body)))])
    write_content(pdf, page, data)
    prune_resources(pdf, page_index, "XObject", [o.name])
    return [o.index]


# ---------------------------------------------------------------- sostituzione
FIT, FILL, STRETCH = "fit", "fill", "stretch"


def image_occurrences(pdf: pymupdf.Document, xref: int) -> list[int]:
    """Pagine in cui compare l'image XObject (anche più volte per pagina)."""
    pages = []
    for i in range(pdf.page_count):
        try:
            for o in parse_page(pdf, pdf[i]).objects:
                if o.kind == IMAGE and o.xref == xref:
                    pages.append(i)
        except Exception:
            continue
    return pages


def replace_image(pdf, page_index: int, index: int, data: bytes, mode: str = FIT,
                  all_occurrences: bool = False) -> list[int]:
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    (o,) = _check_indices(parsed, [index])
    if o.kind != IMAGE:
        raise ObjectError("L'oggetto selezionato non è un'immagine.")
    data, orient = prepare_image(data)
    _nw, _nh, pw, ph = natural_size(data)
    if orient in (5, 6, 7, 8):
        pw, ph = ph, pw
    if all_occurrences and o.xref:
        before = set(resource_names(pdf, page, "XObject"))
        try:
            page.replace_image(o.xref, stream=data)
        except Exception as e:
            raise ObjectError("Impossibile sostituire l'immagine.") from e
        page = pdf[page_index]
        added = [n for n in resource_names(pdf, page, "XObject") if n not in before]
        _drop_resource_names(pdf, page, "XObject", added)
        return [o.index]
    name, _x = add_image_resource(pdf, page, data)
    ow, oh = object_size(o)
    if ow <= 0 or oh <= 0 or pw <= 0 or ph <= 0:
        raise ObjectError("Dimensioni dell'immagine non valide.")
    ar_new = pw / ph
    ar_old = ow / oh
    sub = IDENTITY
    clip = True
    if mode == FIT:
        if ar_new > ar_old:
            hn = ar_old / ar_new
            sub = pymupdf.Matrix(1, 0, 0, hn, 0, (1 - hn) / 2)
        else:
            wn = ar_new / ar_old
            sub = pymupdf.Matrix(wn, 0, 0, 1, (1 - wn) / 2, 0)
    elif mode == FILL:
        if ar_new > ar_old:
            wn = ar_new / ar_old
            sub = pymupdf.Matrix(wn, 0, 0, 1, (1 - wn) / 2, 0)
        else:
            hn = ar_old / ar_new
            sub = pymupdf.Matrix(1, 0, 0, hn, 0, (1 - hn) / 2)
        inv = ~sub
        a = pymupdf.Point(0, 0) * inv
        b = pymupdf.Point(1, 1) * inv
        clip = (min(a.x, b.x), min(a.y, b.y), abs(b.x - a.x), abs(b.y - a.y))
    old_clip = o.wrapper.clip_unit if o.wrapper is not None else None
    if old_clip:
        inv = ~sub
        x, y, cw, ch = old_clip
        a = pymupdf.Point(x, y) * inv
        b = pymupdf.Point(x + cw, y + ch) * inv
        oc = (min(a.x, b.x), min(a.y, b.y), abs(b.x - a.x), abs(b.y - a.y))
        if mode == FILL and isinstance(clip, tuple):
            x0, y0 = max(clip[0], oc[0]), max(clip[1], oc[1])
            x1 = min(clip[0] + clip[2], oc[0] + oc[2])
            y1 = min(clip[1] + clip[3], oc[1] + oc[3])
            clip = (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))
        else:
            clip = oc
    elif mode != FILL:
        clip = None
    new_ctm = _orientation_matrix(orient) * sub * o.ctm
    body = fmt_name(name) + b" Do"
    s, e = o.span
    unit = unit_bytes(parsed, o, new_ctm, outer_of(o), clip_unit=clip, body=body)
    new_data, _ = apply_edits(parsed.data, [(s, e, _sep(unit))])
    write_content(pdf, page, new_data)
    if o.name:
        prune_resources(pdf, page_index, "XObject", [o.name])
    return [o.index]


# ---------------------------------------------------------------- estrazione
@dataclass
class ExtractedImage:
    data: bytes
    ext: str


def extract_image(pdf: pymupdf.Document, obj: PageObject, fmt: str = "original") -> ExtractedImage:
    """Dati dell'immagine: "original" (senza conversione quando possibile), "png", "jpeg"."""
    if obj.kind != IMAGE:
        raise ObjectError("L'oggetto selezionato non è un'immagine.")
    if obj.inline or not obj.xref:
        raise ObjectError("Le immagini inline non si possono ancora estrarre: usa «Cattura area».")
    has_smask = pdf.xref_get_key(obj.xref, "SMask")[0] == "xref"
    if fmt == "original" and not has_smask:
        info = pdf.extract_image(obj.xref)
        if info and info.get("image"):
            ext = info.get("ext", "png")
            if ext in ("jpeg", "jpg", "jpx", "jp2", "png", "tif", "tiff"):
                return ExtractedImage(info["image"], "jpg" if ext == "jpeg" else ext)
    pix = image_pixmap(pdf, obj.xref)
    if fmt == "jpeg":
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        return ExtractedImage(pix.tobytes("jpeg", jpg_quality=90), "jpg")
    return ExtractedImage(pix.tobytes("png"), "png")


def render_as_shown(pdf: pymupdf.Document, page_index: int, obj: PageObject) -> bytes:
    """PNG dell'area visibile dell'oggetto alla sua risoluzione nativa (circa)."""
    page = pdf[page_index]
    ow, oh = object_size(obj)
    zoom = 1.0
    if obj.width and ow > 0:
        zoom = max(0.5, min(8.0, obj.width / ow))
    clip = obj.rect if not obj.rect.is_empty else obj.quad.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False, annots=False)
    return pix.tobytes("png")


def image_info(pdf: pymupdf.Document, obj: PageObject) -> dict:
    """Informazioni per il pannello proprietà."""
    w, h = object_size(obj)
    info: dict = {"width_pt": w, "height_pt": h}
    if obj.kind == IMAGE and obj.width and obj.height and w > 0 and h > 0:
        info["px"] = (obj.width, obj.height)
        info["ppi"] = (obj.width / (w / 72.0), obj.height / (h / 72.0))
    if obj.kind == IMAGE and obj.xref:
        filt = pdf.xref_get_key(obj.xref, "Filter")[1].replace("/", "").strip("[] ")
        info["filter"] = {"DCTDecode": "JPEG", "FlateDecode": "Flate", "JPXDecode": "JPEG 2000",
                          "CCITTFaxDecode": "CCITT", "JBIG2Decode": "JBIG2", "null": ""}.get(filt, filt)
        cs = pdf.xref_get_key(obj.xref, "ColorSpace")
        cs_text = cs[1]
        if cs[0] == "xref":
            cs_text = pdf.xref_object(int(cs[1].split()[0]), compressed=True)
        cs_text = cs_text.replace("/", " ").strip()
        if "ICCBased" in cs_text:
            m = re.search(r"(\d+)\s+0\s+R", cs_text)
            n = ""
            if m:
                n = pdf.xref_get_key(int(m.group(1)), "N")[1]
            cs_text = f"ICC ({n})" if n and n != "null" else "ICC"
        cs_text = cs_text.strip("[] ")
        info["colorspace"] = cs_text
        info["bpc"] = pdf.xref_get_key(obj.xref, "BitsPerComponent")[1]
        info["alpha"] = pdf.xref_get_key(obj.xref, "SMask")[0] == "xref"
        try:
            info["bytes"] = len(pdf.xref_stream_raw(obj.xref) or b"")
        except Exception:
            info["bytes"] = 0
    return info


# ================================================================ allinea / distribuisci
LEFT, HCENTER, RIGHT, TOP, VCENTER, BOTTOM = "left", "hcenter", "right", "top", "vcenter", "bottom"
REF_SELECTION, REF_KEY, REF_PAGE = "selection", "key", "page"


def align_deltas(rects: list[pymupdf.Rect], how: str, reference: str, page_rect: pymupdf.Rect,
                 key: int = 0) -> list[tuple[float, float]]:
    """Spostamenti (dx, dy) per allineare i riquadri (coordinate della vista)."""
    if not rects:
        return []
    if reference == REF_PAGE:
        ref = pymupdf.Rect(page_rect)
    elif reference == REF_KEY:
        ref = pymupdf.Rect(rects[key])
    else:
        ref = pymupdf.Rect(rects[0])
        for r in rects[1:]:
            ref |= r
    out = []
    for r in rects:
        dx = dy = 0.0
        if how == LEFT:
            dx = ref.x0 - r.x0
        elif how == RIGHT:
            dx = ref.x1 - r.x1
        elif how == HCENTER:
            dx = (ref.x0 + ref.x1) / 2 - (r.x0 + r.x1) / 2
        elif how == TOP:
            dy = ref.y0 - r.y0
        elif how == BOTTOM:
            dy = ref.y1 - r.y1
        elif how == VCENTER:
            dy = (ref.y0 + ref.y1) / 2 - (r.y0 + r.y1) / 2
        out.append((dx, dy))
    return out


SPACING, CENTERS = "spacing", "centers"


def distribute_deltas(rects: list[pymupdf.Rect], horizontal: bool, method: str = SPACING) -> list[tuple[float, float]]:
    n = len(rects)
    if n < 3:
        return [(0.0, 0.0)] * n

    def lo(r):
        return r.x0 if horizontal else r.y0

    def size(r):
        return r.width if horizontal else r.height

    order = sorted(range(n), key=lambda k: (lo(rects[k]), k))
    first, last = rects[order[0]], rects[order[-1]]
    deltas = [(0.0, 0.0)] * n
    if method == CENTERS:
        c0 = lo(first) + size(first) / 2
        c1 = lo(last) + size(last) / 2
        step = (c1 - c0) / (n - 1)
        for pos, k in enumerate(order[1:-1], start=1):
            target = c0 + step * pos - size(rects[k]) / 2
            d = target - lo(rects[k])
            deltas[k] = (d, 0.0) if horizontal else (0.0, d)
    else:
        total = (lo(last) + size(last)) - lo(first)
        gap = (total - sum(size(rects[k]) for k in order)) / (n - 1)
        x = lo(first) + size(first) + gap
        for k in order[1:-1]:
            d = x - lo(rects[k])
            deltas[k] = (d, 0.0) if horizontal else (0.0, d)
            x += size(rects[k]) + gap
    return deltas


def translate_each(pdf, page_index: int, indices: list[int], deltas_view: list[tuple[float, float]]) -> list[int]:
    """Sposta ogni oggetto del proprio vettore (espresso nella vista)."""
    page = pdf[page_index]
    derot = page.derotation_matrix
    parsed = parse_page(pdf, page)
    edits = []
    for i, (dx, dy) in zip(indices, deltas_view):
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            continue
        o = parsed.objects[i]
        v = pymupdf.Point(dx, dy) * derot - pymupdf.Point(0, 0) * derot
        Tp = page_to_pdf_transform(parsed, pymupdf.Matrix(1, 0, 0, 1, v.x, v.y))
        pr = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        edits.append(_transform_edits(parsed, o, Tp, pr))
    if not edits:
        return []
    if all(len(ed) == 1 for ed in edits):
        data, _ = apply_edits(parsed.data, [ed[0] for ed in edits])
        write_content(pdf, page, data)
        return list(indices)
    return _apply_and_locate(pdf, pdf[page_index], parsed, edits)


def view_rects(page: pymupdf.Page, objs: list[PageObject]) -> list[pymupdf.Rect]:
    rot = page.rotation_matrix
    out = []
    for o in objs:
        r = (o.rect if not o.rect.is_empty else o.quad.rect) * rot
        r.normalize()
        out.append(r)
    return out


def view_vector_to_page(page: pymupdf.Page, dx: float, dy: float) -> tuple[float, float]:
    derot = page.derotation_matrix
    v = pymupdf.Point(dx, dy) * derot - pymupdf.Point(0, 0) * derot
    return v.x, v.y


# ================================================================ guide e aggancio
def snap_targets(parsed: ParsedPage, exclude: set, page_rect: pymupdf.Rect, near: pymupdf.Rect | None = None,
                 limit: int = 400) -> tuple[list[float], list[float]]:
    """Bordi e centri di riferimento (x, y): pagina e oggetti vicini non selezionati."""
    xs = [page_rect.x0, (page_rect.x0 + page_rect.x1) / 2, page_rect.x1]
    ys = [page_rect.y0, (page_rect.y0 + page_rect.y1) / 2, page_rect.y1]
    count = 0
    for o in parsed.objects:
        if o.index in exclude or o.hidden:
            continue
        r = o.rect if not o.rect.is_empty else o.quad.rect
        if r.width >= page_rect.width * 0.98 and r.height >= page_rect.height * 0.98:
            continue  # sfondi a tutta pagina
        if near is not None:
            grown = pymupdf.Rect(near.x0 - 200, near.y0 - 200, near.x1 + 200, near.y1 + 200)
            if not grown.intersects(r):
                continue
        xs += [r.x0, (r.x0 + r.x1) / 2, r.x1]
        ys += [r.y0, (r.y0 + r.y1) / 2, r.y1]
        count += 1
        if count >= limit:
            break
    return xs, ys


def snap_delta(rect: pymupdf.Rect, dx: float, dy: float, xs: list[float], ys: list[float],
               tol: float) -> tuple[float, float, float | None, float | None]:
    """Aggancia bordi/centro del riquadro spostato al riferimento più vicino entro `tol`.

    Restituisce (dx, dy, guida_x, guida_y)."""
    def best(vals, cands):
        found = None
        for v in vals:
            for c in cands:
                d = c - v
                if abs(d) <= tol and (found is None or abs(d) < abs(found[0])):
                    found = (d, c)
        return found
    mx = [rect.x0 + dx, (rect.x0 + rect.x1) / 2 + dx, rect.x1 + dx]
    my = [rect.y0 + dy, (rect.y0 + rect.y1) / 2 + dy, rect.y1 + dy]
    gx = gy = None
    bx = best(mx, xs)
    if bx is not None:
        dx += bx[0]
        gx = bx[1]
    by = best(my, ys)
    if by is not None:
        dy += by[0]
        gy = by[1]
    return dx, dy, gx, gy


MM = 72.0 / 25.4


def snap_to_grid(rect: pymupdf.Rect, dx: float, dy: float, step: float) -> tuple[float, float]:
    """Porta l'angolo superiore sinistro del riquadro spostato sull'intersezione più vicina."""
    if step <= 0:
        return dx, dy
    x = rect.x0 + dx
    y = rect.y0 + dy
    return dx + round(x / step) * step - x, dy + round(y / step) * step - y


# ================================================================ tracciati simili
def similar_paths(parsed: ParsedPage, index: int) -> list[int]:
    ref = parsed.objects[index]
    if ref.kind != PATH:
        return [index]
    out = []
    for o in parsed.objects:
        if o.kind != PATH or o.paint != ref.paint:
            continue
        if o.stroke_color != ref.stroke_color or o.fill_color != ref.fill_color:
            continue
        if abs(o.state.line_width - ref.state.line_width) > 1e-6:
            continue
        out.append(o.index)
    return out


# ================================================================ hit test
def hit_test(parsed: ParsedPage, point: pymupdf.Point, tolerance: float = 3.0,
             include_hidden: bool = False) -> list[int]:
    """Oggetti sotto il punto, dal più in alto al più in basso."""
    hits = []
    for o in reversed(parsed.objects):
        if o.hidden and not include_hidden:
            continue
        if _contains(o, point, tolerance):
            hits.append(o.index)
    return hits


def _contains(o: PageObject, p: pymupdf.Point, tol: float) -> bool:
    if o.rect.is_empty:
        return False
    r = o.rect
    if not pymupdf.Rect(r.x0 - tol, r.y0 - tol, r.x1 + tol, r.y1 + tol).contains(p):
        return False
    if o.kind == PATH:
        return _path_contains(o, p, tol)
    if o.kind in (IMAGE, FORM):
        return _quad_contains(o.quad, p) and (o.state.clip is None or o.state.clip.contains(p))
    return True


def _quad_contains(q: pymupdf.Quad, p: pymupdf.Point) -> bool:
    pts = [q.ul, q.ur, q.lr, q.ll]
    sign = 0
    for k in range(4):
        a, b = pts[k], pts[(k + 1) % 4]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def flatten(segments: list) -> list[list]:
    polys = []
    for seg in segments:
        poly: list[pymupdf.Point] = []
        start = None
        for el in seg:
            if el[0] == "m":
                poly = [el[1]]
                start = el[1]
            elif el[0] == "l":
                poly.append(el[1])
            elif el[0] == "c":
                p0 = poly[-1] if poly else el[1]
                p1, p2, p3 = el[1], el[2], el[3]
                for k in range(1, 9):
                    t = k / 8
                    mt = 1 - t
                    poly.append(Pt(
                        mt ** 3 * p0.x + 3 * mt * mt * t * p1.x + 3 * mt * t * t * p2.x + t ** 3 * p3.x,
                        mt ** 3 * p0.y + 3 * mt * mt * t * p1.y + 3 * mt * t * t * p2.y + t ** 3 * p3.y))
            elif el[0] == "h" and start is not None:
                poly.append(start)
        if poly:
            polys.append(poly)
    return polys


def _dist_seg(p, a, b) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    L = dx * dx + dy * dy
    if L <= 0:
        return math.hypot(p.x - a.x, p.y - a.y)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def _path_contains(o: PageObject, p: pymupdf.Point, tol: float) -> bool:
    polys = flatten(o.segments)
    filled = o.paint not in ("S", "s")
    stroked = o.paint in ("S", "s", "B", "B*", "b", "b*")
    if filled:
        inside = False
        for poly in polys:
            n = len(poly)
            for k in range(n):
                a, b = poly[k], poly[(k + 1) % n]
                if (a.y > p.y) != (b.y > p.y):
                    x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
                    if x > p.x:
                        inside = not inside
        if inside:
            return True
    ctm = o.ctm
    scale = abs(ctm.a * ctm.d - ctm.b * ctm.c) ** 0.5
    limit = max(tol, o.state.line_width * scale / 2)
    if stroked or filled:
        for poly in polys:
            for k in range(len(poly) - 1):
                if _dist_seg(p, poly[k], poly[k + 1]) <= limit:
                    return True
    return False


def objects_in_rect(parsed: ParsedPage, rect: pymupdf.Rect, intersect: bool = False) -> list[int]:
    out = []
    for o in parsed.objects:
        r = o.rect if not o.rect.is_empty else o.quad.rect
        if intersect:
            if not (r & rect).is_empty or rect.contains(r):
                out.append(o.index)
        elif rect.contains(r):
            out.append(o.index)
    return out


def kind_label(kind: str) -> str:
    return {IMAGE: "Immagine", PATH: "Tracciato", FORM: "Oggetto composto", SHADING: "Sfumatura"}.get(kind, kind)


__all__ = [
    "ObjectError", "objects", "translate", "rotate", "flip", "resize", "transform_objects", "set_opacity",
    "delete_objects", "reorder", "insert_images", "set_crop", "apply_crop", "replace_image", "extract_image",
    "align_deltas", "distribute_deltas", "translate_each", "hit_test", "objects_in_rect", "similar_paths",
    "Name", "tokenize_ops", "page_content", "matrix_close",
]
