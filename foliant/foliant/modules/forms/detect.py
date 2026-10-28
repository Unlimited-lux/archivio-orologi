"""Riconoscimento automatico dei campi su PDF piatti o scansionati.

Cerca, nelle pagine senza AcroForm, gli elementi che di solito indicano un
campo da compilare: linee orizzontali, riquadri vuoti, sequenze di trattini
bassi, glifi «casella», sequenze di caselle adiacenti («pettine») e, nelle pagine scansionate, le stesse forme ricavate
dall'immagine. Restituisce dei *candidati*; la creazione dei widget spetta
al chiamante.

Le funzioni vanno chiamate tenendo ENGINE_LOCK; non aprono interfacce.
Tutti i rettangoli sono in punti PDF, nelle coordinate della pagina non
ruotata (le stesse di `page.get_text` e `page.get_drawings`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, NamedTuple

import pymupdf

TEXT, CHECKBOX = "text", "checkbox"

# Priorità per l'eliminazione dei doppioni (valore più basso = vince).
_PRIORITY = {"comb": -1, "box": 0, "glyph": 1, "line": 2, "underscore": 3, "raster": 4}

_MIN_LINE = 36.0          # lunghezza minima di una linea-campo (pt)
_FIELD_H = 18.0           # altezza massima di un campo su linea
_FIELD_MIN_H = 10.0       # altezza minima di un campo
_LABEL_DIST = 150.0       # distanza massima dell'etichetta a sinistra/destra
_LABEL_ABOVE = 14.0       # distanza massima dell'etichetta sopra
_WORD_GAP = 12.0          # spazio massimo tra parole della stessa etichetta
_COMB_MIN = 3             # caselle minime di un pettine
_COMB_CELL = (4.0, 30.0)  # larghezza/altezza ammesse di una casella (pt)
_COMB_GAP = 2.5           # spazio massimo tra caselle adiacenti
_CHECK_GLYPHS = "☐□❑◻"
_UNDERSCORES = re.compile(r"_{4,}")
_LABEL_NOISE = re.compile(r"_{2,}|[" + _CHECK_GLYPHS + "]")


@dataclass
class Candidate:
    """Campo proposto: pagina, rettangolo, tipo e nome suggerito."""

    page: int
    rect: pymupdf.Rect
    kind: str
    name: str
    multiline: bool = False
    source: str = ""
    comb: int = 0          # caselle del pettine (0 = non è un pettine)


class _Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line: tuple            # (blocco, riga) di get_text("words")

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


class _HLine(NamedTuple):
    x0: float
    x1: float
    y: float


class _VLine(NamedTuple):
    x: float
    y0: float
    y1: float


# ---------------------------------------------------------------- API


def detect(pdf: pymupdf.Document, pages: list[int] | None = None,
           progress: Callable[[float, str], object] | None = None
           ) -> list[Candidate]:
    """Riconosce i campi sulle pagine indicate (tutte se None)."""
    todo = list(range(pdf.page_count)) if pages is None else list(pages)
    out: list[Candidate] = []
    for i, pno in enumerate(todo):
        out.extend(detect_page(pdf, pno))
        if progress is not None:
            progress((i + 1) / len(todo), f"{i + 1}/{len(todo)}")
    return out


def detect_page(pdf: pymupdf.Document, page: int, *,
                raster: bool | None = None) -> list[Candidate]:
    """Riconosce i campi di una pagina.

    `raster`: None = analisi dell'immagine solo se la pagina sembra una
    scansione; True = sempre; False = mai.
    """
    pg = pdf[page]
    raw_words = [_Word(w[0], w[1], w[2], w[3], w[4], (w[5], w[6]))
                 for w in pg.get_text("words")]
    label_words = _label_words(raw_words)

    hlines, vlines, rects, hsegs = _vector_shapes(pg)
    cands: list[Candidate] = []
    cands += _from_combs(page, vlines, hsegs, label_words)
    cands += _from_rects(page, rects, hlines, raw_words, label_words, pg.rect)
    cands += _from_glyphs(page, pg, label_words)
    cands += _from_lines(page, hlines, vlines, rects, raw_words, label_words)
    cands += _from_underscores(page, raw_words, label_words)

    if raster is None:
        raster = _looks_scanned(pg, len(raw_words))
    if raster:
        cands += _from_raster(page, pg)

    return _postprocess(pg, cands)


def page_needs_ocr(pdf: pymupdf.Document, page: int) -> bool:
    """Vero se conviene eseguire prima l'OCR (FRM-27): pagina senza testo
    (meno di 10 parole) con un'immagine che copre almeno metà pagina."""
    pg = pdf[page]
    return _looks_scanned(pg, len(pg.get_text("words")))


# ------------------------------------------------------- forme vettoriali


def _vector_shapes(pg: pymupdf.Page
                   ) -> tuple[list[_HLine], list[_VLine], list[pymupdf.Rect],
                              list[_HLine]]:
    """Estrae linee orizzontali, linee verticali e rettangoli dai disegni.

    L'ultimo elenco contiene tutti i tratti orizzontali, anche corti e
    compresi i lati dei rettangoli (serve ai pettini).
    """
    hsegs: list[_HLine] = []
    edges: list[_HLine] = []
    vlines: list[_VLine] = []
    rects: list[pymupdf.Rect] = []
    for path in pg.get_drawings():
        items = path.get("items", [])
        closed = _closed_rect(items, path.get("closePath", False))
        if closed is not None:
            items = [("re", closed)]
        for it in items:
            op = it[0]
            if op == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 1.5:
                    hsegs.append(_HLine(min(p1.x, p2.x), max(p1.x, p2.x),
                                        (p1.y + p2.y) / 2))
                elif abs(p1.x - p2.x) < 1.5:
                    vlines.append(_VLine((p1.x + p2.x) / 2, min(p1.y, p2.y),
                                         max(p1.y, p2.y)))
            elif op in ("re", "qu"):
                if op == "qu":
                    if not it[1].is_rectangular:
                        continue
                    r = pymupdf.Rect(it[1].rect)
                else:
                    r = pymupdf.Rect(it[1]).normalize()
                if r.height <= 2 and r.width > 2:
                    hsegs.append(_HLine(r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width <= 2 and r.height >= 2:
                    vlines.append(_VLine((r.x0 + r.x1) / 2, r.y0, r.y1))
                elif r.width > 2 and r.height > 2:
                    rects.append(r)
                    edges += [_HLine(r.x0, r.x1, r.y0), _HLine(r.x0, r.x1, r.y1)]
                    # i lati verticali servono a riconoscere le tabelle
                    vlines.append(_VLine(r.x0, r.y0, r.y1))
                    vlines.append(_VLine(r.x1, r.y0, r.y1))
    return (_merge_hlines(hsegs), vlines, rects,
            _merge_hlines(hsegs + edges, 0.0))


def _closed_rect(items: list, closed: bool) -> pymupdf.Rect | None:
    """Rettangolo se il tracciato è un quadrilatero chiuso ad assi paralleli."""
    if not items or len(items) not in (3, 4) or any(i[0] != "l" for i in items):
        return None
    pts = [items[0][1]] + [i[2] for i in items]
    if len(items) == 3 and not closed:
        return None
    if len(items) == 4 and abs(pts[0] - pts[-1]) > 1 and not closed:
        return None
    xs = _cluster([p.x for p in pts])
    ys = _cluster([p.y for p in pts])
    if len(xs) != 2 or len(ys) != 2:
        return None
    for a, b in zip(pts, pts[1:]):           # solo lati orizzontali/verticali
        if abs(a.x - b.x) > 1 and abs(a.y - b.y) > 1:
            return None
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _cluster(vals: Iterable[float], tol: float = 1.0) -> list[float]:
    """Valori distinti a meno di `tol`."""
    out: list[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _merge_hlines(segs: list[_HLine], min_len: float = _MIN_LINE
                  ) -> list[_HLine]:
    """Unisce segmenti collineari vicini (linee tratteggiate o doppie)."""
    out: list[_HLine] = []
    for s in sorted(segs, key=lambda s: (round(s.y), s.x0)):
        if out and abs(out[-1].y - s.y) < 1.5 and s.x0 - out[-1].x1 <= 3:
            last = out[-1]
            out[-1] = _HLine(last.x0, max(last.x1, s.x1), last.y)
        else:
            out.append(s)
    return [s for s in out if s.x1 - s.x0 >= min_len]


def _from_combs(page: int, vlines: list[_VLine], hsegs: list[_HLine],
                label_words: list[_Word]) -> list[Candidate]:
    """Regola 5: pettini, cioè sequenze di caselle adiacenti.

    Le caselle si ricavano dai tratti verticali della stessa altezza (lati
    di rettangoli separati, divisori interni o linee sciolte), con lati
    superiore e inferiore presenti.
    """
    lo, hi = _COMB_CELL
    groups: list[list[_VLine]] = []
    for v in sorted(vlines, key=lambda v: (round(v.y0), v.y1, v.x)):
        if not lo <= v.y1 - v.y0 <= hi:
            continue
        for g in groups:
            if abs(g[0].y0 - v.y0) <= 1 and abs(g[0].y1 - v.y1) <= 1:
                g.append(v)
                break
        else:
            groups.append([v])
    out: list[Candidate] = []
    for g in groups:
        y0, y1 = g[0].y0, g[0].y1
        xs: list[list[float]] = []          # x vicine = un solo divisore
        for x in sorted(v.x for v in g):
            if xs and x - xs[-1][-1] <= _COMB_GAP:
                xs[-1].append(x)
            else:
                xs.append([x])
        mid = [sum(a) / len(a) for a in xs]
        for run in _comb_runs(list(zip(mid, mid[1:]))):
            x0, x1 = run[0][0], run[-1][1]
            if not (_covered(hsegs, x0, x1, y0) and _covered(hsegs, x0, x1, y1)):
                continue
            r = pymupdf.Rect(x0, y0, x1, y1)
            out.append(Candidate(page, r, TEXT, _text_name(r, label_words),
                                 False, "comb", len(run)))
    return out


def _comb_runs(cells: list[tuple[float, float]]
               ) -> list[list[tuple[float, float]]]:
    """Sequenze di almeno _COMB_MIN celle consecutive di larghezza simile."""
    lo, hi = _COMB_CELL
    runs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for c in cells:
        w = c[1] - c[0]
        fits = lo <= w <= hi
        if cur:
            ref = sorted(b - a for a, b in cur)[len(cur) // 2]
            if not fits or abs(w - ref) > 0.25 * ref:
                runs.append(cur)
                cur = []
        if fits:
            cur.append(c)
    runs.append(cur)
    return [r for r in runs if len(r) >= _COMB_MIN]


def _covered(hsegs: list[_HLine], x0: float, x1: float, y: float) -> bool:
    """Vero se i tratti orizzontali alla quota `y` coprono [x0, x1]."""
    spans = sorted((max(h.x0, x0), min(h.x1, x1)) for h in hsegs
                   if abs(h.y - y) <= 1 and h.x1 > x0 and h.x0 < x1)
    reach = x0
    for a, b in spans:
        if a > reach + _COMB_GAP:
            return False
        reach = max(reach, b)
    return reach >= x1 - _COMB_GAP


def _is_grid_line(h: _HLine, vlines: list[_VLine]) -> bool:
    """Vero se la linea fa parte di una tabella o è il lato di un riquadro."""
    touch: list[_VLine] = []
    crossing = 0
    for v in vlines:
        if h.x0 - 2 <= v.x <= h.x1 + 2 and v.y0 - 2 <= h.y <= v.y1 + 2:
            touch.append(v)
            if v.y0 < h.y - 2 and v.y1 > h.y + 2:
                crossing += 1
    if len(touch) >= 3 or crossing >= 2:
        return True
    if len(touch) == 2 and crossing == 0:
        a, b = sorted(touch, key=lambda v: v.x)
        at_ends = abs(a.x - h.x0) <= 2 and abs(b.x - h.x1) <= 2
        short = max(a.y1 - a.y0, b.y1 - b.y0) < 50
        if at_ends and short:
            return True
    return False


def _from_lines(page: int, hlines: list[_HLine], vlines: list[_VLine],
                rects: list[pymupdf.Rect], words: list[_Word],
                label_words: list[_Word]) -> list[Candidate]:
    """Regola 1: campo di testo appena sopra una linea orizzontale."""
    out = []
    for h in hlines:
        if _is_grid_line(h, vlines) or _is_underline(h, words):
            continue
        rect = _field_above(h, words, hlines, rects)
        out.append(Candidate(page, rect, TEXT,
                             _text_name(rect, label_words), False, "line"))
    return out


def _is_underline(h: _HLine, words: list[_Word]) -> bool:
    """Vero se la linea sottolinea parole già presenti (> 50% coperto)."""
    covered = 0.0
    for w in words:
        if w.y0 < h.y and h.y - 3 <= w.y1 <= h.y + 6:
            covered += max(0.0, min(w.x1, h.x1) - max(w.x0, h.x0))
    return covered > 0.5 * (h.x1 - h.x0)


def _field_above(h: _HLine, words: list[_Word], hlines: list[_HLine],
                 rects: list[pymupdf.Rect]) -> pymupdf.Rect:
    """Rettangolo del campo sopra la linea, limitato da testo o altre linee."""
    bottom = h.y - 1
    limit = bottom - _FIELD_H

    def overlaps(a0: float, a1: float) -> bool:
        return min(a1, h.x1) - max(a0, h.x0) > 1

    for w in words:
        if w.y1 <= bottom + 0.5 and overlaps(w.x0, w.x1):
            limit = max(limit, w.y1)
    for o in hlines:
        if o is not h and o.y < h.y - 1 and overlaps(o.x0, o.x1):
            limit = max(limit, o.y)
    for r in rects:
        if r.y1 < h.y - 1 and overlaps(r.x0, r.x1):
            limit = max(limit, r.y1)
    height = max(_FIELD_MIN_H, min(_FIELD_H, bottom - limit))
    return pymupdf.Rect(h.x0, bottom - height, h.x1, bottom)


def _from_rects(page: int, rects: list[pymupdf.Rect], hlines: list[_HLine],
                words: list[_Word], label_words: list[_Word],
                page_rect: pymupdf.Rect) -> list[Candidate]:
    """Regola 2: caselle quadrate e riquadri vuoti."""
    out = []
    page_area = abs(page_rect) or 1.0
    for r in rects:
        if abs(r) > 0.6 * page_area:
            continue
        kind = _rect_kind(r)
        if kind is None:
            continue
        if kind == CHECKBOX:
            rect = pymupdf.Rect(r.x0 + 1, r.y0 + 1, r.x1 - 1, r.y1 - 1)
            out.append(Candidate(page, rect, CHECKBOX,
                                 _check_name(rect, label_words), False, "box"))
            continue
        if any(_center_in(w, r) for w in words):
            continue
        if _contains_shapes(r, rects, hlines):
            continue
        out.append(Candidate(page, pymupdf.Rect(r), TEXT,
                             _text_name(r, label_words), kind == "multi",
                             "box"))
    return out


def _rect_kind(r: pymupdf.Rect) -> str | None:
    """Classifica un riquadro: CHECKBOX, TEXT, "multi" oppure None."""
    w, h = r.width, r.height
    if 6 <= w <= 22 and 6 <= h <= 22 and 0.75 <= w / h <= 1.33:
        return CHECKBOX
    if h > 40 and w >= 60:
        return "multi"
    if w >= 30 and 10 <= h <= 40:
        return TEXT
    return None


def _center_in(w: _Word, r: pymupdf.Rect) -> bool:
    cx = (w.x0 + w.x1) / 2
    return r.x0 <= cx <= r.x1 and r.y0 <= w.cy <= r.y1


def _contains_shapes(r: pymupdf.Rect, rects: list[pymupdf.Rect],
                     hlines: list[_HLine]) -> bool:
    """Vero se il riquadro è una cornice che contiene altre forme."""
    inner = r + (2, 2, -2, -2)
    for o in rects:
        if o is not r and o in inner:
            return True
    return any(inner.x0 <= h.x0 and h.x1 <= inner.x1 and
               inner.y0 <= h.y <= inner.y1 for h in hlines)


# ------------------------------------------------------------------ testo


def _from_underscores(page: int, words: list[_Word],
                      label_words: list[_Word]) -> list[Candidate]:
    """Regola 3a: sequenze di almeno 4 trattini bassi."""
    runs: list[tuple[pymupdf.Rect, tuple]] = []
    for w in words:
        n = len(w.text)
        for m in _UNDERSCORES.finditer(w.text):
            cw = (w.x1 - w.x0) / n
            x0, x1 = w.x0 + cw * m.start(), w.x0 + cw * m.end()
            bottom = w.y1 - 0.2 * (w.y1 - w.y0)     # circa la linea di base
            r = pymupdf.Rect(x0, bottom - 16, x1, bottom)
            # unisce sequenze separate da spazi sulla stessa riga
            if runs and runs[-1][1] == w.line and 0 <= x0 - runs[-1][0].x1 <= 6:
                runs[-1][0].x1 = x1
            else:
                runs.append((r, w.line))
    return [Candidate(page, r, TEXT, _text_name(r, label_words), False,
                      "underscore") for r, _ in runs]


def _from_glyphs(page: int, pg: pymupdf.Page,
                 label_words: list[_Word]) -> list[Candidate]:
    """Regola 3b: glifi che rappresentano una casella vuota."""
    out = []
    raw = pg.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    if ch.get("c") in _CHECK_GLYPHS:
                        r = pymupdf.Rect(ch["bbox"])
                        if r.is_empty:
                            continue
                        out.append(Candidate(page, r, CHECKBOX,
                                             _check_name(r, label_words),
                                             False, "glyph"))
    return out


# ----------------------------------------------------------------- raster


def _looks_scanned(pg: pymupdf.Page, n_words: int) -> bool:
    """Pagina con poco testo e un'immagine che copre almeno metà pagina."""
    if n_words >= 10:
        return False
    area = abs(pg.rect) or 1.0
    for info in pg.get_image_info():
        if abs(pymupdf.Rect(info["bbox"]) & pg.cropbox) >= 0.5 * area:
            return True
    return False


def _from_raster(page: int, pg: pymupdf.Page) -> list[Candidate]:
    """Regola 4: linee e riquadri ricavati dall'immagine della pagina."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    zoom = 2.0
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                        colorspace=pymupdf.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8)
    img = img.reshape(pix.height, pix.stride)[:, :pix.width]
    _, ink = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if not ink.any():
        return []
    derot = pg.derotation_matrix

    def to_pt(x0: float, y0: float, x1: float, y1: float) -> pymupdf.Rect:
        # pixel della pagina visualizzata -> punti della pagina non ruotata
        r = pymupdf.Rect(x0 / zoom, y0 / zoom, x1 / zoom, y1 / zoom)
        return (r * derot).normalize()

    def ink_ratio(x0: int, y0: int, x1: int, y1: int) -> float:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(ink.shape[1], x1), min(ink.shape[0], y1)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return float(ink[y0:y1, x0:x1].mean()) / 255.0

    # Riquadri: contorni approssimati con 4 vertici, interno vuoto.
    boxes: list[tuple[int, int, int, int]] = []
    contours, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < 6 * zoom or h < 6 * zoom:
            continue
        approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if cv2.contourArea(c) < 0.85 * w * h:
            continue
        m = max(3, int(min(w, h) * 0.15))
        if ink_ratio(x + m, y + m, x + w - m, y + h - m) > 0.04:
            continue
        if any(_iou_px((x, y, w, h), b) > 0.5 for b in boxes):
            continue
        boxes.append((x, y, w, h))

    out: list[Candidate] = []
    disp_area = pix.width * pix.height
    for x, y, w, h in boxes:
        if w * h > 0.6 * disp_area:
            continue
        kind = _rect_kind(pymupdf.Rect(0, 0, w / zoom, h / zoom))
        if kind is None:
            continue
        if kind == CHECKBOX:
            r = to_pt(x + zoom, y + zoom, x + w - zoom, y + h - zoom)
            out.append(Candidate(page, r, CHECKBOX, "", False, "raster"))
        else:
            out.append(Candidate(page, to_pt(x, y, x + w, y + h), TEXT, "",
                                 kind == "multi", "raster"))

    # Linee orizzontali e verticali per apertura morfologica.
    klen = max(40, int(_MIN_LINE * zoom))
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (klen, 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT,
                                                      (1, int(6 * zoom))))
    vlines: list[_VLine] = []
    n, _, stats, _ = cv2.connectedComponentsWithStats(vert)
    for i in range(1, n):
        x, y, w, h = stats[i][:4]
        vlines.append(_VLine(x + w / 2, float(y), float(y + h)))
    hlines: list[_HLine] = []
    n, _, stats, _ = cv2.connectedComponentsWithStats(horiz)
    for i in range(1, n):
        x, y, w, h = (int(v) for v in stats[i][:4])
        if h > 4 * zoom:                       # barre piene, non linee
            continue
        if any(bx - 3 <= x and x + w <= bx + bw + 3 and
               (abs(y - by) <= 3 or abs(y + h - by - bh) <= 3)
               for bx, by, bw, bh in boxes):   # lato di un riquadro
            continue
        hlines.append(_HLine(float(x), float(x + w), y + h / 2))

    box_rects = [pymupdf.Rect(x, y, x + w, y + h) for x, y, w, h in boxes]
    for hl in hlines:
        if _is_grid_line(hl, vlines):
            continue
        top = int(hl.y - 12 * zoom)
        if ink_ratio(int(hl.x0), top, int(hl.x1), int(hl.y - 2 * zoom)) > 0.08:
            continue                            # testo sottolineato
        # campo calcolato in pixel (scala zoom) con la stessa regola vettoriale
        scaled = _HLine(hl.x0 / zoom, hl.x1 / zoom, hl.y / zoom)
        others = [_HLine(o.x0 / zoom, o.x1 / zoom, o.y / zoom)
                  for o in hlines if o is not hl]
        rr = _field_above(scaled, [], others,
                          [r / zoom for r in box_rects])
        out.append(Candidate(page, (rr * derot).normalize(), TEXT, "",
                             False, "raster"))
    return out


def _iou_px(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    iw = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    ih = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


# ---------------------------------------------------------- post-elaborazione


def _iou(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = abs(a & b)
    union = abs(a) + abs(b) - inter
    return inter / union if union > 0 else 0.0


def _postprocess(pg: pymupdf.Page, cands: list[Candidate]) -> list[Candidate]:
    """Toglie doppioni, campi già esistenti e fuori pagina; ordina."""
    bounds = (pg.rect * pg.derotation_matrix).normalize()
    widgets = [pymupdf.Rect(w.rect) for w in (pg.widgets() or [])]
    combs = [c.rect for c in cands if c.comb]
    cands = [c for c in cands if c.comb or not any(
        abs(c.rect & r) > 0.5 * abs(c.rect) for r in combs)]
    kept: list[Candidate] = []
    for c in sorted(cands, key=lambda c: _PRIORITY.get(c.source, 9)):
        area = abs(c.rect)
        if area <= 0:
            continue
        inside = abs(c.rect & bounds)
        if inside < 0.5 * area:
            continue
        c.rect = c.rect & bounds
        if any(abs(c.rect & w) > 0.3 * abs(c.rect) for w in widgets):
            continue
        if any(_iou(c.rect, k.rect) > 0.5 for k in kept):
            continue
        kept.append(c)
    kept.sort(key=lambda c: (round(c.rect.y0 / 4), c.rect.x0))
    return kept


# ------------------------------------------------------------------- nomi


def _label_words(words: list[_Word]) -> list[_Word]:
    """Parole utili come etichette: senza trattini bassi né glifi casella."""
    out = []
    for w in words:
        n = len(w.text)
        if not n:
            continue
        cw = (w.x1 - w.x0) / n
        cuts = [0]
        for m in _LABEL_NOISE.finditer(w.text):
            cuts += [m.start(), m.end()]
        cuts.append(n)
        for start, end in zip(cuts[::2], cuts[1::2]):
            piece = w.text[start:end]
            if piece.strip(" :.;,-"):
                out.append(_Word(w.x0 + cw * start, w.y0, w.x0 + cw * end,
                                 w.y1, piece, w.line))
    return out


def _same_line(w: _Word, r: pymupdf.Rect) -> bool:
    """La parola sta sulla stessa riga del campo (anche un po' più in basso)."""
    return r.y0 - 2 <= w.cy <= r.y1 + 5


def _group(start: _Word, words: list[_Word], leftwards: bool) -> list[_Word]:
    """Parole contigue della stessa riga a partire da `start`."""
    row = sorted((w for w in words if w.line == start.line),
                 key=lambda w: w.x0)
    i = row.index(start)
    group = [start]
    step = -1 if leftwards else 1
    j = i + step
    while 0 <= j < len(row) and len(group) < 3:
        a, b = (row[j], group[-1]) if leftwards else (group[-1], row[j])
        if b.x0 - a.x1 > _WORD_GAP:
            break
        group.append(row[j])
        j += step
    return sorted(group, key=lambda w: w.x0)


def _text_name(r: pymupdf.Rect, words: list[_Word]) -> str:
    """Nome dall'etichetta a sinistra sulla stessa riga, altrimenti sopra."""
    left = [w for w in words if _same_line(w, r) and w.x1 <= r.x0 + 2
            and r.x0 - w.x1 <= _LABEL_DIST]
    if left:
        near = max(left, key=lambda w: w.x1)
        return _clean(_group(near, left, leftwards=True))
    above = [w for w in words if r.y0 - _LABEL_ABOVE <= w.y1 <= r.y0 + 2
             and min(w.x1, r.x1) - max(w.x0, r.x0) > 0]
    if above:
        near = max(above, key=lambda w: (w.y1, -w.x0))
        row = sorted((w for w in above if w.line == near.line),
                     key=lambda w: w.x0)
        return _clean(row[:3])
    return ""


def _check_name(r: pymupdf.Rect, words: list[_Word]) -> str:
    """Nome di una casella: parole subito a destra sulla stessa riga."""
    right = [w for w in words if r.y0 - 3 <= w.cy <= r.y1 + 3
             and w.x0 >= r.x1 - 2 and w.x0 - r.x1 <= _LABEL_DIST]
    if not right:
        return ""
    near = min(right, key=lambda w: w.x0)
    return _clean(_group(near, right, leftwards=False))


def _clean(words: list[_Word]) -> str:
    """Nome valido: lettere, cifre e «_», al massimo 40 caratteri."""
    text = " ".join(w.text for w in words[:3])
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return "_".join(text.split())[:40]
