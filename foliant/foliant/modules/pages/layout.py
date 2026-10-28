"""Geometria delle pagine senza interfaccia (PAG-25…PAG-30).

Ritaglio (CropBox / MediaBox), rimozione dei margini bianchi, riquadri di
stampa, ridimensionamento, divisione di una pagina in parti e pagine
affiancate (n-up).

Convenzioni:

* le funzioni che ricevono `pdf` lo modificano sul posto (chiamarle dentro
  `with document.change(...) as pdf:`); `nup()` restituisce i `bytes` di un
  nuovo documento;
* i riquadri (`get_boxes`, `set_box`) sono espressi nello *spazio utente PDF*
  (i valori grezzi scritti nel file, origine in basso a sinistra);
* margini, rettangoli di ritaglio, griglie e formati di destinazione si
  riferiscono alla pagina *come visualizzata* (rotazione `/Rotate` inclusa,
  origine in alto a sinistra del ritaglio attuale);
* `content_bbox()` restituisce coordinate di pagina del motore (pagina non
  ruotata, origine in alto a sinistra del ritaglio), come il resto dell'app.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

import pymupdf

from foliant.api import _

from .engine import INCH, MIN_SIDE, MM, PAPER_SIZES, PageOpError, check_page_size

TRANSLATIONS = {
    "I margini non possono essere negativi.": "Margins cannot be negative.",
    "Unità di misura non valida: {u}.": "Invalid unit: {u}.",
    "L'area risultante è troppo piccola (minimo 3×3 pt) o i margini si sovrappongono.":
        "The resulting area is too small (minimum 3×3 pt) or the margins overlap.",
    "Il rettangolo di ritaglio è troppo piccolo (minimo 3×3 pt).": "The crop rectangle is too small (minimum 3×3 pt).",
    "Riquadro sconosciuto: {n}.": "Unknown box: {n}.",
    "Il riquadro è fuori dal supporto (MediaBox).": "The box lies outside the media box.",
    "Il supporto (MediaBox) non può essere rimosso.": "The media box cannot be removed.",
    "Il riquadro è troppo piccolo (minimo 3×3 pt).": "The box is too small (minimum 3×3 pt).",
    "L'abbondanza non può essere negativa.": "Bleed cannot be negative.",
    "Il formato finito è più grande del supporto della pagina {n}.":
        "The trim size is larger than the media box of page {n}.",
    "Modalità sconosciuta: {m}.": "Unknown mode: {m}.",
    "Ancoraggio sconosciuto: {a}.": "Unknown anchor: {a}.",
    "Orientamento sconosciuto: {o}.": "Unknown orientation: {o}.",
    "Indica il formato di destinazione.": "Specify the target size.",
    "I margini lasciano un'area troppo piccola per il contenuto.": "The margins leave too little room for the content.",
    "La pagina di riferimento {n} non esiste.": "Reference page {n} does not exist.",
    "Righe e colonne devono essere almeno 1.": "Rows and columns must be at least 1.",
    "Le parti risultanti sono troppo piccole (minimo 3×3 pt).": "The resulting parts are too small (minimum 3×3 pt).",
    "Nessuna linea di taglio valida: la pagina resterebbe intera.": "No valid cut line: the page would stay whole.",
    "Nessuna pagina sorgente.": "No source pages.",
    "Le celle sono troppo piccole: riduci righe, colonne, margini o spaziatura.":
        "Cells are too small: reduce rows, columns, margins or spacing.",
    "Ordine sconosciuto: {o}.": "Unknown order: {o}.",
    "Rilegatura sconosciuta: {b}.": "Unknown binding: {b}.",
}

UNITS = {"pt": 1.0, "mm": MM, "in": INCH}
BOX_NAMES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")
ANCHORS: dict[str, tuple[float, float]] = {
    "top-left": (0.0, 0.0), "top": (0.5, 0.0), "top-right": (1.0, 0.0),
    "left": (0.0, 0.5), "center": (0.5, 0.5), "right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0), "bottom": (0.5, 1.0), "bottom-right": (1.0, 1.0),
}
MIN_CELL = 18.0      # lato minimo di una cella n-up (pt)
MAX_CELLS_WARN = 100

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_REF = re.compile(r"(\d+)\s+\d+\s+R")


# =========================================================================
# Riquadri grezzi (spazio utente PDF)
# =========================================================================

def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _rect_str(r: pymupdf.Rect) -> str:
    return "[" + " ".join(_fmt(v) for v in (r.x0, r.y0, r.x1, r.y1)) + "]"


def _value(pdf: pymupdf.Document, xref: int, key: str) -> str | None:
    """Valore di una chiave, risolvendo un eventuale riferimento indiretto."""
    typ, val = pdf.xref_get_key(xref, key)
    if typ == "null":
        return None
    if typ == "xref":
        m = _REF.match(val)
        if m:
            return pdf.xref_object(int(m.group(1)), compressed=True)
    return val


def _numbers(text: str | None) -> list[float]:
    return [float(x) for x in _NUM.findall(text or "")]


def _norm(r: pymupdf.Rect) -> pymupdf.Rect:
    return pymupdf.Rect(min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1), max(r.y0, r.y1))


def _raw_box(pdf: pymupdf.Document, xref: int, name: str, inherit: bool) -> pymupdf.Rect | None:
    seen = 0
    while xref and seen < 64:
        seen += 1
        nums = _numbers(_value(pdf, xref, name))
        if len(nums) == 4:
            return _norm(pymupdf.Rect(*nums))
        if not inherit:
            return None
        typ, val = pdf.xref_get_key(xref, "Parent")
        m = _REF.match(val) if typ == "xref" else None
        xref = int(m.group(1)) if m else 0
    return None


def _media(pdf: pymupdf.Document, page: pymupdf.Page) -> pymupdf.Rect:
    r = _raw_box(pdf, page.xref, "MediaBox", True)
    return r if r is not None and not r.is_empty else pymupdf.Rect(0, 0, 612, 792)


def _crop(pdf: pymupdf.Document, page: pymupdf.Page) -> pymupdf.Rect:
    media = _media(pdf, page)
    r = _raw_box(pdf, page.xref, "CropBox", True)
    if r is None:
        return media
    r = r & media
    return media if r.is_empty else r


def _set_raw(pdf: pymupdf.Document, page: pymupdf.Page, name: str, rect: pymupdf.Rect | None) -> None:
    pdf.xref_set_key(page.xref, name, "null" if rect is None else _rect_str(rect))


def _clip_print_boxes(pdf: pymupdf.Document, page: pymupdf.Page, media: pymupdf.Rect,
                      matrix: pymupdf.Matrix | None = None) -> None:
    """Trasforma (facoltativo) e limita Bleed/Trim/Art al nuovo supporto."""
    for name in ("BleedBox", "TrimBox", "ArtBox"):
        r = _raw_box(pdf, page.xref, name, False)
        if r is None:
            continue
        if matrix is not None:
            r = _norm(r * matrix)
        r = r & media
        _set_raw(pdf, page, name, None if r.is_empty or r.width < MIN_SIDE or r.height < MIN_SIDE else r)


# =========================================================================
# Pagina visualizzata ↔ spazio utente
# =========================================================================

def _rot(page: pymupdf.Page) -> int:
    return page.rotation % 360


def _disp_size(base: pymupdf.Rect, rot: int) -> tuple[float, float]:
    return (base.width, base.height) if rot in (0, 180) else (base.height, base.width)


def _disp_point_to_user(base: pymupdf.Rect, rot: int, dx: float, dy: float) -> tuple[float, float]:
    if rot == 90:
        return base.x0 + dy, base.y0 + dx
    if rot == 180:
        return base.x1 - dx, base.y0 + dy
    if rot == 270:
        return base.x1 - dy, base.y1 - dx
    return base.x0 + dx, base.y1 - dy


def _user_point_to_disp(base: pymupdf.Rect, rot: int, x: float, y: float) -> tuple[float, float]:
    if rot == 90:
        return y - base.y0, x - base.x0
    if rot == 180:
        return base.x1 - x, y - base.y0
    if rot == 270:
        return base.y1 - y, base.x1 - x
    return x - base.x0, base.y1 - y


def disp_to_user(base: pymupdf.Rect, rot: int, rect: pymupdf.Rect) -> pymupdf.Rect:
    """Rettangolo nella pagina visualizzata (relativa a `base`) → spazio utente."""
    a = _disp_point_to_user(base, rot, rect.x0, rect.y0)
    b = _disp_point_to_user(base, rot, rect.x1, rect.y1)
    return _norm(pymupdf.Rect(a[0], a[1], b[0], b[1]))


def user_to_disp(base: pymupdf.Rect, rot: int, rect: pymupdf.Rect) -> pymupdf.Rect:
    a = _user_point_to_disp(base, rot, rect.x0, rect.y0)
    b = _user_point_to_disp(base, rot, rect.x1, rect.y1)
    return _norm(pymupdf.Rect(a[0], a[1], b[0], b[1]))


def _disp_frac_to_user(rot: int, fx: float, fy: float) -> tuple[float, float]:
    """Frazioni (0..1) nella pagina visualizzata (y in giù) → frazioni utente (y in su)."""
    if rot == 90:
        return fy, fx
    if rot == 180:
        return 1 - fx, fy
    if rot == 270:
        return 1 - fy, 1 - fx
    return fx, 1 - fy


def _engine_to_user(crop: pymupdf.Rect, r: pymupdf.Rect) -> pymupdf.Rect:
    """Coordinate del motore (non ruotate, origine in alto a sinistra del ritaglio) → utente."""
    return _norm(pymupdf.Rect(crop.x0 + r.x0, crop.y1 - r.y1, crop.x0 + r.x1, crop.y1 - r.y0))


def _user_to_engine(crop: pymupdf.Rect, r: pymupdf.Rect) -> pymupdf.Rect:
    return _norm(pymupdf.Rect(r.x0 - crop.x0, crop.y1 - r.y1, r.x1 - crop.x0, crop.y1 - r.y0))


def displayed_crop_size(pdf: pymupdf.Document, index: int) -> tuple[float, float]:
    page = pdf[index]
    return _disp_size(_crop(pdf, page), _rot(page))


def displayed_media_size(pdf: pymupdf.Document, index: int) -> tuple[float, float]:
    page = pdf[index]
    return _disp_size(_media(pdf, page), _rot(page))


def crop_in_media_disp(pdf: pymupdf.Document, index: int) -> pymupdf.Rect:
    """Il ritaglio attuale nelle coordinate della pagina intera visualizzata (anteprime)."""
    page = pdf[index]
    return user_to_disp(_media(pdf, page), _rot(page), _crop(pdf, page))


# =========================================================================
# PAG-25 — ritaglio
# =========================================================================

def _unit_factor(unit: str) -> float | None:
    if unit == "%":
        return None
    if unit not in UNITS:
        raise PageOpError(_("Unità di misura non valida: {u}.").format(u=unit))
    return UNITS[unit]


def _apply_crop(pdf: pymupdf.Document, page: pymupdf.Page, rect: pymupdf.Rect, hard: bool) -> None:
    if hard:
        _set_raw(pdf, page, "MediaBox", rect)
        _set_raw(pdf, page, "CropBox", None)
        _clip_print_boxes(pdf, page, rect)
    else:
        _set_raw(pdf, page, "CropBox", rect)


def crop_margins_rect(pdf: pymupdf.Document, index: int, margins: Sequence[float], unit: str = "pt",
                      relative_to_current: bool = True, mirror_even: bool = False) -> pymupdf.Rect | None:
    """Nuovo ritaglio (spazio utente) per una pagina, o None se l'area è troppo piccola."""
    t, b, l, r = (float(m) for m in margins)
    if min(t, b, l, r) < 0:
        raise PageOpError(_("I margini non possono essere negativi."))
    factor = _unit_factor(unit)
    page = pdf[index]
    rot = _rot(page)
    media = _media(pdf, page)
    base = _crop(pdf, page) if relative_to_current else media
    dw, dh = _disp_size(base, rot)
    if mirror_even and (index + 1) % 2 == 0:
        l, r = r, l
    if factor is None:
        t, b, l, r = t * dh / 100, b * dh / 100, l * dw / 100, r * dw / 100
    else:
        t, b, l, r = t * factor, b * factor, l * factor, r * factor
    if dw - l - r < MIN_SIDE or dh - t - b < MIN_SIDE:
        return None
    new = disp_to_user(base, rot, pymupdf.Rect(l, t, dw - r, dh - b)) & media
    if new.is_empty or new.width < MIN_SIDE or new.height < MIN_SIDE:
        return None
    return new


def crop_pages(pdf: pymupdf.Document, pages: Iterable[int], margins: Sequence[float] = (0, 0, 0, 0),
               unit: str = "pt", relative_to_current: bool = True, mirror_even: bool = False,
               hard: bool = False) -> list[int]:
    """Ritaglia con margini (alto, basso, sinistra, destra) riferiti alla pagina visualizzata.

    Con `unit="%"` alto/basso sono percentuali dell'altezza visualizzata e
    sinistra/destra della larghezza. `mirror_even` scambia sinistra e destra
    sulle pagine pari (fronte/retro). `hard=True` scrive il `/MediaBox`.
    Restituisce le pagine ritagliate; quelle troppo piccole sono saltate
    (errore se nessuna pagina può essere ritagliata).
    """
    done: list[int] = []
    for i in pages:
        new = crop_margins_rect(pdf, i, margins, unit, relative_to_current, mirror_even)
        if new is None:
            continue
        _apply_crop(pdf, pdf[i], new, hard)
        done.append(i)
    if not done:
        raise PageOpError(_("L'area risultante è troppo piccola (minimo 3×3 pt) o i margini si sovrappongono."))
    return done


def crop_to_rect(pdf: pymupdf.Document, pages: Iterable[int], rect_displayed, hard: bool = False) -> list[int]:
    """Ritaglia al rettangolo indicato nella pagina visualizzata (relativo al ritaglio attuale).

    Il rettangolo è limitato ai bordi di ciascuna pagina.
    """
    rect = _norm(pymupdf.Rect(rect_displayed))
    done: list[int] = []
    for i in pages:
        page = pdf[i]
        rot = _rot(page)
        base = _crop(pdf, page)
        dw, dh = _disp_size(base, rot)
        r = rect & pymupdf.Rect(0, 0, dw, dh)
        if r.is_empty or r.width < MIN_SIDE or r.height < MIN_SIDE:
            continue
        new = disp_to_user(base, rot, r) & _media(pdf, page)
        _apply_crop(pdf, page, new, hard)
        done.append(i)
    if not done:
        raise PageOpError(_("Il rettangolo di ritaglio è troppo piccolo (minimo 3×3 pt)."))
    return done


def reset_crop(pdf: pymupdf.Document, pages: Iterable[int]) -> list[int]:
    """Ripristina l'area originale: rimuove `/CropBox` (o lo rende uguale al `/MediaBox`)."""
    done = []
    for i in pages:
        page = pdf[i]
        _set_raw(pdf, page, "CropBox", None)
        page = pdf[i]
        media = _media(pdf, page)
        if _crop(pdf, page) != media:          # ritaglio ereditato dall'albero delle pagine
            _set_raw(pdf, page, "CropBox", media)
        done.append(i)
    return done


def is_cropped(pdf: pymupdf.Document, index: int) -> bool:
    page = pdf[index]
    c, m = _crop(pdf, page), _media(pdf, page)
    return any(abs(a - b) > 0.01 for a, b in zip(c, m))


_TEXT_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_MEDIABOX_CLIP


def hidden_content_outside_crop(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> bool:
    """True se c'è testo fuori dall'area di ritaglio (resta nel file ma non si vede)."""
    for i in (range(pdf.page_count) if pages is None else pages):
        if not is_cropped(pdf, i):
            continue
        page = pdf[i]
        crop = _crop(pdf, page)
        area = pymupdf.Rect(0, 0, crop.width, crop.height) + (-0.5, -0.5, 0.5, 0.5)
        for w in page.get_text("words", flags=_TEXT_FLAGS, clip=pymupdf.INFINITE_RECT()):
            if not area.contains(pymupdf.Rect(w[:4])):
                return True
    return False


def annots_outside_crop(pdf: pymupdf.Document, pages: Iterable[int]) -> int:
    """Numero di annotazioni non interamente visibili nel ritaglio attuale."""
    n = 0
    for i in pages:
        page = pdf[i]
        crop = _crop(pdf, page)
        area = pymupdf.Rect(0, 0, crop.width, crop.height) + (-0.5, -0.5, 0.5, 0.5)
        for a in page.annots() or []:
            if not area.contains(a.rect):
                n += 1
    return n


# =========================================================================
# PAG-26 — margini bianchi
# =========================================================================

def _visible_color(c) -> bool:
    return bool(c) and any(v < 0.98 for v in c)


def content_bbox(page: pymupdf.Page, tolerance: float = 0.1, ignore_border: float = 0.0,
                 include_annots: bool = False, max_pixels: int = 2_500_000) -> pymupdf.Rect | None:
    """Riquadro del contenuto visibile (coordinate del motore) o None se la pagina è vuota.

    Analizza un rendering a bassa risoluzione (pixel più scuri del bianco oltre
    `tolerance`, 0…1) e affina i bordi con i limiti esatti degli oggetti
    grafici. Il testo invisibile e gli oggetti bianchi su bianco non contano.
    `ignore_border` (0…0,45) esclude una fascia di bordo (frazione del lato).
    """
    import numpy as np

    disp = page.rect
    if disp.is_empty:
        return None
    zoom = min(1.0, (max_pixels / max(1.0, disp.width * disp.height)) ** 0.5)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False,
                          annots=include_annots)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    tol = min(max(float(tolerance), 0.0), 1.0)
    mask = arr < 255.0 * (1.0 - tol) - 0.5
    border = min(max(float(ignore_border), 0.0), 0.45)
    if border:
        bx, by = int(round(pix.width * border)), int(round(pix.height * border))
        mask[:by, :] = False
        mask[pix.height - by:, :] = False
        mask[:, :bx] = False
        mask[:, pix.width - bx:] = False
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    sx, sy = disp.width / pix.width, disp.height / pix.height
    dbox = pymupdf.Rect(cols[0] * sx, rows[0] * sy, (cols[-1] + 1) * sx, (rows[-1] + 1) * sy)
    box = _norm(dbox * page.derotation_matrix)
    # affinamento con i limiti esatti degli oggetti vettoriali vicini ai bordi
    snap = 1.5 / zoom + 0.5
    edges: dict[str, list[float]] = {"x0": [], "y0": [], "x1": [], "y1": []}
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - contenuti malformati: si usa solo il rendering
        drawings = []
    grown = box + (-snap, -snap, snap, snap)
    for d in drawings:
        if not (_visible_color(d.get("fill")) or _visible_color(d.get("color"))):
            continue
        r = pymupdf.Rect(d["rect"])
        if r.is_empty and r.width == 0 and r.height == 0:
            continue
        if not grown.intersects(r + (-0.1, -0.1, 0.1, 0.1)):
            continue
        for k in edges:
            edges[k].append(getattr(r, k))
    vals = [box.x0, box.y0, box.x1, box.y1]
    for n, k in enumerate(("x0", "y0", "x1", "y1")):
        near = [v for v in edges[k] if abs(v - vals[n]) <= snap]
        if near:
            vals[n] = min(near, key=lambda v: abs(v - vals[n]))
    out = pymupdf.Rect(*vals) & pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    return None if out.is_empty else out


def remove_white_margins(pdf: pymupdf.Document, pages: Iterable[int], extra: float = 0.0,
                         tolerance: float = 0.1, uniform: bool = False, ignore_border: float = 0.0,
                         include_annots: bool = False, rects: dict[int, pymupdf.Rect] | None = None) -> dict:
    """Imposta il ritaglio attorno al contenuto visibile (+ `extra` punti).

    `uniform=True`: stesso `/CropBox` per tutte le pagine (unione dei riquadri).
    `rects`: riquadri già calcolati/modificati dall'utente (coordinate del motore).
    Restituisce {"cropped": [...], "blank": [...], "full": [...]}; "full" sono le
    pagine in cui il contenuto occupa tutta la pagina (nessun margine trovato).
    """
    pages = list(pages)
    report: dict[str, list[int]] = {"cropped": [], "blank": [], "full": []}
    found: dict[int, pymupdf.Rect] = {}
    for i in pages:
        page = pdf[i]
        if rects is not None and i in rects:
            bb = rects[i]
        else:
            bb = content_bbox(page, tolerance, ignore_border, include_annots)
        if bb is None:
            report["blank"].append(i)
            continue
        crop = _crop(pdf, page)
        if bb.x0 <= 1 and bb.y0 <= 1 and bb.x1 >= crop.width - 1 and bb.y1 >= crop.height - 1:
            report["full"].append(i)
            if not uniform:
                continue
        found[i] = _engine_to_user(crop, bb) + (-extra, -extra, extra, extra)
    if uniform and found:
        union = pymupdf.Rect(next(iter(found.values())))
        for r in found.values():
            union |= r
        found = {i: union for i in found}
    for i, r in found.items():
        page = pdf[i]
        new = r & _media(pdf, page)
        if new.is_empty or new.width < MIN_SIDE or new.height < MIN_SIDE:
            continue
        _set_raw(pdf, page, "CropBox", new)
        report["cropped"].append(i)
    return report


# =========================================================================
# PAG-27 — riquadri di stampa
# =========================================================================

def get_boxes(page: pymupdf.Page) -> dict[str, dict]:
    """Tutti i riquadri (spazio utente) con il flag "default" se assenti nel file."""
    pdf = page.parent
    media_raw = _raw_box(pdf, page.xref, "MediaBox", True)
    media = _media(pdf, page)
    crop_raw = _raw_box(pdf, page.xref, "CropBox", True)
    crop = _crop(pdf, page)
    out = {
        "MediaBox": {"rect": media, "default": media_raw is None},
        "CropBox": {"rect": crop, "default": crop_raw is None},
    }
    for name in ("BleedBox", "TrimBox", "ArtBox"):
        r = _raw_box(pdf, page.xref, name, False)
        out[name] = {"rect": crop if r is None else (r & media), "default": r is None}
    return out


def set_box(pdf: pymupdf.Document, pages: Iterable[int], name: str, rect) -> list[int]:
    """Imposta (o rimuove con `rect=None`) un riquadro in spazio utente.

    Ogni riquadro è limitato al `/MediaBox`; restituisce le pagine in cui il
    valore è stato limitato (per l'avviso). Cambiando il `/MediaBox` gli altri
    riquadri vengono limitati al nuovo supporto.
    """
    if name not in BOX_NAMES:
        raise PageOpError(_("Riquadro sconosciuto: {n}.").format(n=name))
    clipped: list[int] = []
    for i in pages:
        page = pdf[i]
        if rect is None:
            if name == "MediaBox":
                raise PageOpError(_("Il supporto (MediaBox) non può essere rimosso."))
            _set_raw(pdf, page, name, None)
            continue
        r = _norm(pymupdf.Rect(rect))
        if r.width < MIN_SIDE or r.height < MIN_SIDE:
            raise PageOpError(_("Il riquadro è troppo piccolo (minimo 3×3 pt)."))
        if name == "MediaBox":
            check_page_size(r.width, r.height)
            _set_raw(pdf, page, "MediaBox", r)
            page = pdf[i]
            c = _raw_box(pdf, page.xref, "CropBox", False)
            if c is not None:
                c = c & r
                _set_raw(pdf, page, "CropBox", None if c.is_empty or c.width < MIN_SIDE or c.height < MIN_SIDE else c)
            _clip_print_boxes(pdf, page, r)
            continue
        media = _media(pdf, page)
        inside = r & media
        if inside.is_empty or inside.width < MIN_SIDE or inside.height < MIN_SIDE:
            raise PageOpError(_("Il riquadro è fuori dal supporto (MediaBox)."))
        if any(abs(a - b) > 0.01 for a, b in zip(inside, r)):
            clipped.append(i)
        _set_raw(pdf, page, name, inside)
    return clipped


def set_trim_with_bleed(pdf: pymupdf.Document, pages: Iterable[int], trim_size: tuple[float, float],
                        bleed: float = 3 * MM) -> list[int]:
    """Formato finito centrato nel supporto (`/TrimBox`) e abbondanza (`/BleedBox`).

    `trim_size` (larghezza, altezza in punti) si riferisce alla pagina
    visualizzata. Restituisce le pagine in cui l'abbondanza è stata limitata al
    supporto.
    """
    if bleed < 0:
        raise PageOpError(_("L'abbondanza non può essere negativa."))
    tw, th = float(trim_size[0]), float(trim_size[1])
    if tw < MIN_SIDE or th < MIN_SIDE:
        raise PageOpError(_("Il riquadro è troppo piccolo (minimo 3×3 pt)."))
    clipped: list[int] = []
    for i in pages:
        page = pdf[i]
        media = _media(pdf, page)
        w, h = (tw, th) if _rot(page) in (0, 180) else (th, tw)
        if w > media.width + 0.5 or h > media.height + 0.5:     # tolleranza per i formati arrotondati
            raise PageOpError(_("Il formato finito è più grande del supporto della pagina {n}.").format(n=i + 1))
        cx, cy = (media.x0 + media.x1) / 2, (media.y0 + media.y1) / 2
        trim = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2) & media
        full = trim + (-bleed, -bleed, bleed, bleed)
        bl = full & media
        if any(abs(a - b) > 0.01 for a, b in zip(bl, full)):
            clipped.append(i)
        _set_raw(pdf, page, "TrimBox", trim)
        _set_raw(pdf, page, "BleedBox", bl)
    return clipped


# =========================================================================
# PAG-28 — ridimensiona / cambia formato
# =========================================================================

def _parse_struct(text: str):
    """Array PDF (anche annidati) di numeri → liste Python."""
    stack: list[list] = [[]]
    for tok in re.findall(r"\[|\]|" + _NUM.pattern, text):
        if tok == "[":
            stack.append([])
        elif tok == "]":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        else:
            stack[-1].append(float(tok))
    res = stack[0]
    return res[0] if len(res) == 1 and isinstance(res[0], list) else res


def _struct_str(obj) -> str:
    if isinstance(obj, list):
        return "[" + " ".join(_struct_str(o) for o in obj) + "]"
    return _fmt(obj)


def _map_pairs(values: list, m: pymupdf.Matrix) -> list:
    if values and isinstance(values[0], list):
        return [_map_pairs(v, m) for v in values]
    out = []
    for k in range(0, len(values) - 1, 2):
        p = pymupdf.Point(values[k], values[k + 1]) * m
        out += [p.x, p.y]
    return out


def _annot_xrefs(pdf: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    return [int(x) for x in _REF.findall(_value(pdf, page.xref, "Annots") or "")]


def _transform_annots(pdf: pymupdf.Document, page: pymupdf.Page, m: pymupdf.Matrix) -> None:
    """Trasforma coerentemente le annotazioni (spazio utente) con la matrice `m`."""
    scale = (abs(m.a) + abs(m.d)) / 2 or 1.0
    for ax in _annot_xrefs(pdf, page):
        rect = _numbers(_value(pdf, ax, "Rect"))
        if len(rect) == 4:
            r = _norm(pymupdf.Rect(*rect) * m)
            pdf.xref_set_key(ax, "Rect", _rect_str(r))
        for key in ("QuadPoints", "Vertices", "L", "CL", "InkList"):
            raw = _value(pdf, ax, key)
            if not raw or "[" not in raw:
                continue
            vals = _parse_struct(raw)
            if vals:
                pdf.xref_set_key(ax, key, _struct_str(_map_pairs(vals, m)))
        rd = _numbers(_value(pdf, ax, "RD"))
        if len(rd) == 4:
            pdf.xref_set_key(ax, "RD", _struct_str([v * scale for v in rd]))


def _wrap_content(pdf: pymupdf.Document, page: pymupdf.Page, m: pymupdf.Matrix, clip: pymupdf.Rect) -> None:
    """Racchiude i contenuti esistenti in `q <m> cm <clip> W n … Q` (nessuna riscrittura)."""
    page.wrap_contents()
    page = pdf[page.number]
    olds = page.get_contents()
    pre = (f"q {_fmt(m.a)} {_fmt(m.b)} {_fmt(m.c)} {_fmt(m.d)} {_fmt(m.e)} {_fmt(m.f)} cm "
           f"{_fmt(clip.x0)} {_fmt(clip.y0)} {_fmt(clip.width)} {_fmt(clip.height)} re W n\n")
    refs = []
    for data in (pre.encode(), b"\nQ\n"):
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, data)
        refs.append(x)
    arr = [refs[0], *olds, refs[1]]
    pdf.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in arr) + "]")


def _scale_into(pdf: pymupdf.Document, index: int, new_media: pymupdf.Rect, area: pymupdf.Rect,
                proportional: bool, ufrac: tuple[float, float], new_crop: pymupdf.Rect | None = None,
                fill: bool = False) -> None:
    page = pdf[index]
    crop = _crop(pdf, page)
    sx, sy = area.width / crop.width, area.height / crop.height
    if fill:
        sx = sy = max(sx, sy)
    elif proportional:
        sx = sy = min(sx, sy)
    dx = area.x0 + (area.width - crop.width * sx) * ufrac[0]
    dy = area.y0 + (area.height - crop.height * sy) * ufrac[1]
    m = pymupdf.Matrix(sx, 0, 0, sy, dx - crop.x0 * sx, dy - crop.y0 * sy)
    _wrap_content(pdf, page, m, crop)
    page = pdf[index]
    _set_raw(pdf, page, "MediaBox", new_media)
    _set_raw(pdf, page, "CropBox", new_crop)
    _clip_print_boxes(pdf, page, new_media, m)
    _transform_annots(pdf, page, m)


def _target_size(size, orientation: str, dw: float, dh: float) -> tuple[float, float]:
    w, h = float(size[0]), float(size[1])
    lo, hi = min(w, h), max(w, h)
    if orientation == "portrait":
        return lo, hi
    if orientation == "landscape":
        return hi, lo
    if orientation == "auto":
        return (hi, lo) if dw > dh else (lo, hi)
    if orientation == "fixed":
        return w, h
    raise PageOpError(_("Orientamento sconosciuto: {o}.").format(o=orientation))


MIN_READABLE = 4.0


def min_font_size(page: pymupdf.Page) -> float | None:
    """Corpo minimo del testo visibile della pagina (punti), o None se non c'è testo."""
    sizes = []
    try:
        for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip() and span.get("size", 0) > 0:
                        sizes.append(span["size"])
    except Exception:  # noqa: BLE001 - testo illeggibile: nessun avviso
        return None
    return min(sizes) if sizes else None


def text_too_small(page: pymupdf.Page, scale: float, limit: float = MIN_READABLE) -> bool:
    """True se con la scala indicata il testo scende sotto `limit` pt effettivi."""
    if scale >= 1:
        return False
    m = min_font_size(page)
    return m is not None and m * scale < limit - 1e-6


def resize_pages(pdf: pymupdf.Document, pages: Iterable[int], size: tuple[float, float] | None = None,
                 mode: str = "scale", proportional: bool = True, anchor: str = "center",
                 margins: Sequence[float] | None = None, orientation: str = "auto",
                 report: dict | None = None) -> list[int]:
    """Cambia il formato delle pagine (riferito alla pagina visualizzata).

    * `mode="scale"`: il contenuto è racchiuso in una trasformazione (`cm`) e
      adattato al formato meno gli eventuali `margins` (alto, basso, sinistra,
      destra in punti), allineato secondo `anchor`; le annotazioni seguono.
    * `mode="canvas"`: cambia solo la tela; il contenuto non è scalato ed è
      posizionato secondo `anchor`; con `margins` la tela è il contenuto più i
      margini indicati (negativi per togliere spazio) e `size` è ignorato.
    * `mode="fill"`: come "scale" ma proporzionale riempiendo l'area; le parti
      eccedenti sono tagliate (segnalate in `report["overflow"]`).
    `orientation`: "auto" (come la pagina), "portrait", "landscape", "fixed".
    `report` (dict facoltativo) riceve "small_text" (pagine il cui testo scende
    sotto 4 pt effettivi) e "overflow" (pagine con contenuto tagliato).
    Restituisce le pagine modificate (formato uguale → pagina invariata).
    """
    if report is not None:
        report.setdefault("small_text", [])
        report.setdefault("overflow", [])
    if mode not in ("scale", "canvas", "fill"):
        raise PageOpError(_("Modalità sconosciuta: {m}.").format(m=mode))
    if anchor not in ANCHORS:
        raise PageOpError(_("Ancoraggio sconosciuto: {a}.").format(a=anchor))
    if size is None and not (mode == "canvas" and margins):
        raise PageOpError(_("Indica il formato di destinazione."))
    t = b = l = r = 0.0
    if margins:
        t, b, l, r = (float(v) for v in margins)
    fx, fy = ANCHORS[anchor]
    changed: list[int] = []
    for i in pages:
        page = pdf[i]
        rot = _rot(page)
        crop = _crop(pdf, page)
        media = _media(pdf, page)
        dw, dh = _disp_size(crop, rot)
        if mode == "canvas" and margins:
            tw, th = dw + l + r, dh + t + b
            ox, oy = l, t
        else:
            tw, th = _target_size(size, orientation, dw, dh)
            ox, oy = (tw - dw) * fx, (th - dh) * fy
        check_page_size(tw, th)
        same = abs(tw - dw) < 0.01 and abs(th - dh) < 0.01
        if mode == "canvas":
            if same and abs(ox) < 0.01 and abs(oy) < 0.01 and crop == media:
                continue
            new_media = disp_to_user(crop, rot, pymupdf.Rect(-ox, -oy, tw - ox, th - oy))
            _set_raw(pdf, page, "MediaBox", new_media)
            _set_raw(pdf, page, "CropBox", None)
            _clip_print_boxes(pdf, pdf[i], new_media)
            changed.append(i)
            continue
        if same and not margins and crop == media and abs(media.x0) < 0.01 and abs(media.y0) < 0.01:
            continue
        uw, uh = (tw, th) if rot in (0, 180) else (th, tw)
        new_media = pymupdf.Rect(0, 0, uw, uh)
        if tw - l - r < MIN_SIDE or th - t - b < MIN_SIDE:
            raise PageOpError(_("I margini lasciano un'area troppo piccola per il contenuto."))
        area = disp_to_user(new_media, rot, pymupdf.Rect(l, t, tw - r, th - b))
        if report is not None:
            sx, sy = area.width / crop.width, area.height / crop.height
            if mode == "fill":
                scale = max(sx, sy)
                if abs(sx - sy) > 1e-3:
                    report["overflow"].append(i)
            else:
                scale = min(sx, sy)
            if text_too_small(page, scale):
                report["small_text"].append(i)
        _scale_into(pdf, i, new_media, area, proportional, _disp_frac_to_user(rot, fx, fy), fill=mode == "fill")
        changed.append(i)
    return changed


def match_page_size(pdf: pymupdf.Document, pages: Iterable[int], ref_index: int, mode: str = "scale",
                    proportional: bool = True, anchor: str = "center") -> list[int]:
    """Porta le pagine al formato della pagina di riferimento: stesso `/MediaBox` (e ritaglio)."""
    if not 0 <= ref_index < pdf.page_count:
        raise PageOpError(_("La pagina di riferimento {n} non esiste.").format(n=ref_index + 1))
    ref = pdf[ref_index]
    ref_media = _media(pdf, ref)
    ref_crop_raw = _raw_box(pdf, ref.xref, "CropBox", True)
    ref_crop = _crop(pdf, ref)
    ref_rot = _rot(ref)
    fx, fy = ANCHORS.get(anchor, (0.5, 0.5))
    changed = []
    for i in pages:
        if i == ref_index:
            continue
        page = pdf[i]
        media, crop = _media(pdf, page), _crop(pdf, page)
        if media == ref_media and crop == ref_crop:
            continue
        rot = _rot(page)
        if mode == "canvas":
            dw, dh = _disp_size(crop, rot)
            tw, th = _disp_size(ref_crop, ref_rot)
            if (rot - ref_rot) % 180:
                tw, th = th, tw
            ox, oy = (tw - dw) * fx, (th - dh) * fy
            new_media = disp_to_user(crop, rot, pymupdf.Rect(-ox, -oy, tw - ox, th - oy))
            # traslazione per far coincidere il nuovo supporto con quello di riferimento
            shift = pymupdf.Matrix(1, 0, 0, 1, ref_crop.x0 - new_media.x0, ref_crop.y0 - new_media.y0)
            _wrap_content(pdf, page, shift, crop)
            page = pdf[i]
            _transform_annots(pdf, page, shift)
            _set_raw(pdf, page, "MediaBox", ref_media)
            _set_raw(pdf, page, "CropBox", ref_crop if ref_crop_raw is not None else None)
            _clip_print_boxes(pdf, pdf[i], ref_media, shift)
        else:
            _scale_into(pdf, i, ref_media, ref_crop, proportional, _disp_frac_to_user(rot, fx, fy),
                        new_crop=ref_crop if ref_crop_raw is not None else None)
        changed.append(i)
    return changed


# =========================================================================
# PAG-29 — dividi una pagina in parti
# =========================================================================

def _cuts(total: float, n: int, cuts: Sequence[float] | None) -> list[float]:
    if cuts:
        vals = sorted({round(float(c), 3) for c in cuts if 0.5 < float(c) < total - 0.5})
        return [0.0, *vals, total]
    return [total * k / n for k in range(n + 1)]


def split_parts_disp(width: float, height: float, rows: int = 1, cols: int = 2, rtl: bool = False,
                     cuts_x: Sequence[float] | None = None, cuts_y: Sequence[float] | None = None,
                     overlap: float = 0.0) -> list[pymupdf.Rect]:
    """Parti (coordinate visualizzate) nell'ordine di lettura scelto."""
    if rows < 1 or cols < 1:
        raise PageOpError(_("Righe e colonne devono essere almeno 1."))
    xs = _cuts(width, cols, cuts_x)
    ys = _cuts(height, rows, cuts_y)
    if len(xs) == 2 and len(ys) == 2:
        raise PageOpError(_("Nessuna linea di taglio valida: la pagina resterebbe intera."))
    ov = max(0.0, float(overlap)) / 2
    parts = []
    for ry in range(len(ys) - 1):
        row = []
        for cx in range(len(xs) - 1):
            r = pymupdf.Rect(xs[cx] - (ov if cx else 0), ys[ry] - (ov if ry else 0),
                             xs[cx + 1] + (ov if cx < len(xs) - 2 else 0),
                             ys[ry + 1] + (ov if ry < len(ys) - 2 else 0))
            r &= pymupdf.Rect(0, 0, width, height)
            if r.width < MIN_SIDE or r.height < MIN_SIDE:
                raise PageOpError(_("Le parti risultanti sono troppo piccole (minimo 3×3 pt)."))
            row.append(r)
        parts.extend(reversed(row) if rtl else row)
    return parts


def detect_fold(page: pymupdf.Page, band: float = 0.3, min_contrast: float = 6.0) -> float | None:
    """Ascissa (pagina visualizzata) della piega centrale di una scansione di libro.

    Cerca, nel 30% centrale della larghezza, la fascia verticale più scura (o
    più chiara) rispetto alle colonne vicine in un rendering in grigi a bassa
    risoluzione; None se non c'è una fascia abbastanza evidente.
    """
    import numpy as np

    disp = page.rect
    if disp.width < 10 or disp.height < 10:
        return None
    zoom = min(1.0, 500.0 / disp.width)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False, annots=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    h = arr.shape[0]
    cols = arr[int(h * 0.05):max(int(h * 0.95), int(h * 0.05) + 1), :].astype(float).mean(axis=0)
    if cols.size >= 3:
        cols = np.convolve(cols, np.ones(3) / 3, mode="same")
    lo = int(pix.width * (0.5 - band / 2))
    hi = max(int(pix.width * (0.5 + band / 2)), lo + 1)
    mid = cols[lo:hi]
    if mid.size == 0:
        return None
    base = float(np.median(mid))
    dark, bright = base - float(mid.min()), float(mid.max()) - base
    if max(dark, bright) < min_contrast:
        return None
    target = mid.min() if dark >= bright else mid.max()
    idx = np.flatnonzero(mid == target)
    x = (lo + (idx[0] + idx[-1]) / 2 + 0.5) / zoom
    return float(min(max(x, 0.0), disp.width))


_REDACT_TEXT_ONLY = dict(images=getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0),
                         graphics=getattr(pymupdf, "PDF_REDACT_LINE_ART_NONE", 0),
                         text=getattr(pymupdf, "PDF_REDACT_TEXT_REMOVE", 0))


def _remove_text_outside(pdf: pymupdf.Document, index: int, keep_user: pymupdf.Rect) -> None:
    """Elimina dal contenuto il testo fuori da `keep_user` (immagini e grafica restano)."""
    page = pdf[index]
    crop = _crop(pdf, page)
    keep = _user_to_engine(crop, keep_user)
    outer = _user_to_engine(crop, _media(pdf, page)) | keep
    outer = outer & pymupdf.Rect(0, 0, crop.width, crop.height)
    pieces = [pymupdf.Rect(outer.x0, outer.y0, outer.x1, keep.y0), pymupdf.Rect(outer.x0, keep.y1, outer.x1, outer.y1),
              pymupdf.Rect(outer.x0, keep.y0, keep.x0, keep.y1), pymupdf.Rect(keep.x1, keep.y0, outer.x1, keep.y1)]
    added = False
    for r in pieces:
        if r.width > 0.01 and r.height > 0.01:
            page.add_redact_annot(r)
            added = True
    if added:
        page.apply_redactions(**_REDACT_TEXT_ONLY)


def split_page_grid(pdf: pymupdf.Document, pages: Iterable[int], rows: int = 1, cols: int = 2, rtl: bool = False,
                    cuts_x: Sequence[float] | None = None, cuts_y: Sequence[float] | None = None,
                    overlap: float = 0.0, remove_outside: bool = False, fold: bool = False) -> list[int]:
    """Sostituisce ogni pagina con le sue parti (griglia sulla pagina visualizzata).

    Le parti sono copie della pagina che condividono le risorse (immagini,
    font) con `/MediaBox` pari alla porzione; ogni annotazione (campi modulo
    inclusi, che restano registrati nell'AcroForm) viene spostata nella parte
    con cui si sovrappone di più. `remove_outside=True` elimina dal contenuto
    di ogni parte il testo esterno (non compare più in ricerca e copia).
    `fold=True`: taglio verticale sulla piega rilevata (`detect_fold`), al
    centro se non rilevata. Restituisce gli indici delle nuove pagine.
    """
    pages = sorted(set(pages))
    counts: dict[int, int] = {}
    for i in sorted(pages, reverse=True):
        page = pdf[i]
        rot = _rot(page)
        crop = _crop(pdf, page)
        media = _media(pdf, page)
        dw, dh = _disp_size(crop, rot)
        cx = cuts_x
        if fold:
            x = detect_fold(page)
            cx = [x if x is not None else dw / 2]
        parts = [disp_to_user(crop, rot, r) & media
                 for r in split_parts_disp(dw, dh, rows, 2 if fold else cols, rtl, cx, cuts_y, overlap)]
        # assegnazione delle annotazioni alla parte con la sovrapposizione maggiore
        annots = _annot_xrefs(pdf, page)
        owner: list[int] = []
        for ax in annots:
            nums = _numbers(_value(pdf, ax, "Rect"))
            best = 0
            if len(nums) == 4:
                ar = _norm(pymupdf.Rect(*nums))
                areas = [((ar & p).get_area() if ar.intersects(p) else 0.0) for p in parts]
                if max(areas) <= 0:   # rettangolo degenere: si usa il centro
                    c = pymupdf.Point((ar.x0 + ar.x1) / 2, (ar.y0 + ar.y1) / 2)
                    areas = [1.0 if p.contains(c) else 0.0 for p in parts]
                best = max(range(len(parts)), key=lambda k: areas[k])
            owner.append(best)
        # le finestre popup seguono l'annotazione madre
        pos = {ax: n for n, ax in enumerate(annots)}
        for n, ax in enumerate(annots):
            m = _REF.match(pdf.xref_get_key(ax, "Parent")[1] or "")
            if m and int(m.group(1)) in pos:
                owner[n] = owner[pos[int(m.group(1))]]
        # le copie nascono senza annotazioni: gli oggetti originali vengono poi
        # spostati nella parte giusta (niente duplicati di campi o commenti)
        if annots:
            pdf.xref_set_key(page.xref, "Annots", "null")
        for _k in range(len(parts) - 1):
            pdf.fullcopy_page(i, i + 1 if i + 1 < pdf.page_count else -1)
        for k, part in enumerate(parts):
            if remove_outside:
                _remove_text_outside(pdf, i + k, part)
            pg = pdf[i + k]
            _set_raw(pdf, pg, "MediaBox", part)
            _set_raw(pdf, pg, "CropBox", None)
            for name in ("BleedBox", "TrimBox", "ArtBox"):
                _set_raw(pdf, pg, name, None)
            keep = [ax for n, ax in enumerate(annots) if owner[n] == k]
            pdf.xref_set_key(pg.xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in keep) + "]" if keep else "null")
            for ax in keep:
                pdf.xref_set_key(ax, "P", f"{pg.xref} 0 R")
        counts[i] = len(parts)
    out: list[int] = []
    offset = 0
    for i in pages:
        start = i + offset
        out.extend(range(start, start + counts[i]))
        offset += counts[i] - 1
    return out


# =========================================================================
# PAG-30 — pagine affiancate (n-up)
# =========================================================================

def nup_grid(sheet: tuple[float, float], cols: int, rows: int, margin: float, spacing: float) -> list[pymupdf.Rect]:
    """Celle del foglio (per righe, sinistra→destra)."""
    sw, sh = sheet
    cw = (sw - 2 * margin - (cols - 1) * spacing) / cols
    ch = (sh - 2 * margin - (rows - 1) * spacing) / rows
    if cw < MIN_CELL or ch < MIN_CELL:
        raise PageOpError(_("Le celle sono troppo piccole: riduci righe, colonne, margini o spaziatura."))
    return [pymupdf.Rect(margin + c * (cw + spacing), margin + r * (ch + spacing),
                         margin + c * (cw + spacing) + cw, margin + r * (ch + spacing) + ch)
            for r in range(rows) for c in range(cols)]


def _fit(cell: pymupdf.Rect, w: float, h: float) -> float:
    return min(cell.width / w, cell.height / h)


def nup_layout(page_sizes: Sequence[tuple[float, float]], *, cols: int = 2, rows: int = 1,
               sheet_size: tuple[float, float] = PAPER_SIZES["A4"], landscape: bool | None = None,
               order: str = "rows", rtl: bool = False, margin: float = 18.0, spacing: float = 6.0,
               scale: str = "fit", auto_rotate: bool = True, repeat: bool = False) -> dict:
    """Piano dell'impaginazione: {"sheet": (w,h), "sheets": [[(src, rect, extra_rot), …], …]}.

    `page_sizes` sono le dimensioni visualizzate delle pagine sorgente (in ordine).
    """
    if cols < 1 or rows < 1:
        raise PageOpError(_("Righe e colonne devono essere almeno 1."))
    if order not in ("rows", "columns"):
        raise PageOpError(_("Ordine sconosciuto: {o}.").format(o=order))
    if not page_sizes:
        raise PageOpError(_("Nessuna pagina sorgente."))
    lo, hi = sorted((float(sheet_size[0]), float(sheet_size[1])))
    check_page_size(lo, hi)
    if landscape is None:
        pw, ph = page_sizes[0]
        best = None
        for land in (False, True):
            sheet = (hi, lo) if land else (lo, hi)
            try:
                cell = nup_grid(sheet, cols, rows, margin, spacing)[0]
            except PageOpError:
                continue
            s = max(_fit(cell, pw, ph), _fit(cell, ph, pw) if auto_rotate else 0)
            if best is None or s > best[0] + 1e-6:
                best = (s, land)
        landscape = best[1] if best else False
    sheet = (hi, lo) if landscape else (lo, hi)
    cells = nup_grid(sheet, cols, rows, margin, spacing)
    n = cols * rows

    def cell_at(k: int) -> pymupdf.Rect:
        if order == "rows":
            r, c = divmod(k, cols)
        else:
            c, r = divmod(k, rows)
        if rtl:
            c = cols - 1 - c
        return cells[r * cols + c]

    if repeat:
        groups = [[s] * n for s in range(len(page_sizes))]
    else:
        groups = [list(range(k, min(k + n, len(page_sizes)))) for k in range(0, len(page_sizes), n)]
    sheets = []
    for g in groups:
        items = []
        for k, src in enumerate(g):
            cell = cell_at(k)
            pw, ph = page_sizes[src]
            extra = 0
            s = _fit(cell, pw, ph)
            if auto_rotate and _fit(cell, ph, pw) > s * 1.01:
                extra, s, pw, ph = 90, _fit(cell, ph, pw), ph, pw
            if scale == "actual":
                s = min(1.0, s)
            w, h = pw * s, ph * s
            cx, cy = (cell.x0 + cell.x1) / 2, (cell.y0 + cell.y1) / 2
            items.append((src, pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), extra))
        sheets.append(items)
    return {"sheet": sheet, "sheets": sheets, "cells": cells}


def nup(pdf: pymupdf.Document, pages: Sequence[int] | None = None, *, cols: int = 2, rows: int = 1,
        sheet_size: tuple[float, float] = PAPER_SIZES["A4"], landscape: bool | None = None,
        order: str = "rows", rtl: bool = False, margin: float = 18.0, spacing: float = 6.0,
        border: bool = False, scale: str = "fit", auto_rotate: bool = True, repeat: bool = False,
        annotations: str = "flatten") -> bytes:
    """Nuovo PDF con più pagine sorgente per foglio; il documento originale non cambia.

    Ogni pagina sorgente è incorporata una sola volta come Form XObject
    (le risorse condivise, ad es. un'immagine ripetuta, restano uniche) e il
    testo resta ricercabile. `annotations`: "flatten" (rese nell'aspetto) o
    "discard". Le pagine con `/Rotate` sono considerate come visualizzate.
    """
    idx = list(range(pdf.page_count)) if pages is None else list(pages)
    if not idx:
        raise PageOpError(_("Nessuna pagina sorgente."))
    sizes = [displayed_crop_size(pdf, i) for i in idx]
    plan = nup_layout(sizes, cols=cols, rows=rows, sheet_size=sheet_size, landscape=landscape, order=order,
                      rtl=rtl, margin=margin, spacing=spacing, scale=scale, auto_rotate=auto_rotate,
                      repeat=repeat)
    src = pymupdf.open("pdf", pdf.tobytes())
    try:
        if annotations == "flatten":
            try:
                src.bake(annots=True, widgets=True)
            except Exception:  # noqa: BLE001 - annotazioni malformate: si scartano
                annotations = "discard"
        rots = {}
        for i in sorted(set(idx)):
            page = src[i]
            rots[i] = _rot(page)
            if annotations != "flatten":
                src.xref_set_key(page.xref, "Annots", "null")
            if rots[i]:
                page.set_rotation(0)
        out = pymupdf.open()
        sw, sh = plan["sheet"]
        for items in plan["sheets"]:
            sheet = out.new_page(width=sw, height=sh)
            for k, rect, extra in items:
                i = idx[k]
                sheet.show_pdf_page(rect, src, i, keep_proportion=True, rotate=(extra - rots[i]) % 360)
                if border:
                    sheet.draw_rect(rect, color=(0, 0, 0), width=0.5)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
    finally:
        src.close()


# =========================================================================
# PAG-31 — opuscolo (booklet)
# =========================================================================

def booklet_order(n: int, binding: str = "left", signature_sheets: int | None = None,
                  blank_position: str = "end") -> list[tuple[int | None, int | None]]:
    """Facciate dell'opuscolo come coppie (sinistra, destra) di indici sorgente (None = vuota).

    Le pagine sono completate con vuote fino a un multiplo di 4 (in fondo o
    prima dell'ultima). Per ogni fascicolo di m pagine, foglio k = 1…m/4:
    fronte [m−2k+2, 2k−1], retro [2k, m−2k+1]; rilegatura a destra = coppie
    specchiate. `signature_sheets` = fogli per segnatura (None = punto metallico).
    """
    if n < 1:
        raise PageOpError(_("Nessuna pagina sorgente."))
    if binding not in ("left", "right"):
        raise PageOpError(_("Rilegatura sconosciuta: {b}.").format(b=binding))
    blanks = (-n) % 4
    seq: list[int | None] = list(range(n))
    if blanks:
        if blank_position == "before_last" and n > 1:
            seq = seq[:-1] + [None] * blanks + seq[-1:]
        else:
            seq = seq + [None] * blanks
    per = len(seq) if not signature_sheets else max(1, int(signature_sheets)) * 4
    sides: list[tuple[int | None, int | None]] = []
    for start in range(0, len(seq), per):
        chunk = seq[start:start + per]
        m = len(chunk)
        for k in range(1, m // 4 + 1):
            front = (chunk[m - 2 * k + 1], chunk[2 * k - 2])
            back = (chunk[2 * k - 1], chunk[m - 2 * k])
            for left, right in (front, back):
                sides.append((right, left) if binding == "right" else (left, right))
    return sides


def booklet(pdf: pymupdf.Document, pages: Sequence[int] | None = None, *,
            sheet_size: tuple[float, float] = PAPER_SIZES["A4"], binding: str = "left",
            signature_sheets: int | None = None, blank_position: str = "end", gutter: float = 0.0,
            creep: float = 0.0, annotations: str = "flatten") -> bytes:
    """Nuovo PDF imposto a libretto: due pagine per facciata su fogli orizzontali.

    `gutter`: spazio totale alla piega (pt); `creep`: spostamento massimo verso
    la piega dei fogli più interni (pt), crescente dal foglio esterno a quello
    centrale di ogni fascicolo. Pagine di formato diverso sono adattate alla
    mezza facciata mantenendo le proporzioni.
    """
    idx = list(range(pdf.page_count)) if pages is None else list(pages)
    sides = booklet_order(len(idx), binding, signature_sheets, blank_position)
    lo, hi = sorted((float(sheet_size[0]), float(sheet_size[1])))
    check_page_size(lo, hi)
    sw, sh = hi, lo
    half = (sw - max(0.0, gutter)) / 2
    if half < MIN_CELL:
        raise PageOpError(_("Le celle sono troppo piccole: riduci righe, colonne, margini o spaziatura."))
    per_sig = (signature_sheets * 4) if signature_sheets else None
    total = len(sides) * 2
    src = pymupdf.open("pdf", pdf.tobytes())
    try:
        if annotations == "flatten":
            try:
                src.bake(annots=True, widgets=True)
            except Exception:  # noqa: BLE001
                annotations = "discard"
        rots = {}
        for i in sorted(set(idx)):
            page = src[i]
            rots[i] = _rot(page)
            if annotations != "flatten":
                src.xref_set_key(page.xref, "Annots", "null")
            if rots[i]:
                page.set_rotation(0)
        out = pymupdf.open()
        for s, (left, right) in enumerate(sides):
            sheet = out.new_page(width=sw, height=sh)
            # posizione del foglio nel suo fascicolo (0 = esterno) per il creep
            pos_pages = s * 2
            sig_len = per_sig or total
            sig_start = (pos_pages // sig_len) * sig_len
            sig_pages = min(sig_len, total - sig_start)
            sheets_in_sig = max(1, sig_pages // 4)
            k = ((pos_pages - sig_start) // 4)
            shift = creep * k / max(1, sheets_in_sig - 1) if creep and sheets_in_sig > 1 else 0.0
            for slot, k_src in ((0, left), (1, right)):
                if k_src is None:
                    continue
                i = idx[k_src]
                pw, ph = displayed_crop_size(pdf, i)
                x0 = 0.0 if slot == 0 else sw - half
                cell = pymupdf.Rect(x0, 0, x0 + half, sh)
                sc = _fit(cell, pw, ph)
                w, h = pw * sc, ph * sc
                cx = (cell.x0 + cell.x1) / 2 + (shift if slot == 0 else -shift)
                cy = sh / 2
                rect = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                sheet.show_pdf_page(rect, src, i, keep_proportion=True, rotate=(-rots[i]) % 360)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
    finally:
        src.close()
