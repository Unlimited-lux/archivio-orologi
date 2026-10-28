"""Cattura di un'area come immagine o testo (OBJ-29), logica pura.

Le aree sono rettangoli in coordinate pagina (quelle di `page.rect`, usate da
`get_pixmap(clip=...)` e `get_text(clip=...)`). La resa comprende le
annotazioni visibili. Oltre 100 megapixel la risoluzione viene ridotta e la
funzione lo segnala.
"""

from __future__ import annotations

import io
import math
import re

import pymupdf
from PIL import Image

MIN_AREA = 2.0                    # punti per lato
MAX_PIXELS = 100_000_000          # 100 megapixel


def _check(rect) -> pymupdf.Rect:
    r = pymupdf.Rect(rect).normalize()
    if r.width < MIN_AREA or r.height < MIN_AREA:
        raise ValueError("Area troppo piccola: almeno 2×2 punti.")
    return r


def _fit_dpi(dpi: int, area_pt2: float) -> tuple[int, bool]:
    """DPI usabile perché l'immagine non superi MAX_PIXELS."""
    dpi = max(1, int(dpi))
    px = area_pt2 * (dpi / 72.0) ** 2
    if px <= MAX_PIXELS:
        return dpi, False
    new = int(math.floor(dpi * math.sqrt(MAX_PIXELS / px)))
    while new > 1 and area_pt2 * (new / 72.0) ** 2 > MAX_PIXELS:
        new -= 1
    return max(1, new), True


def _pixmap(pdf: pymupdf.Document, page_index: int, rect: pymupdf.Rect, dpi: int) -> pymupdf.Pixmap:
    return pdf[page_index].get_pixmap(dpi=dpi, clip=rect, alpha=False, annots=True)


def render_area(pdf: pymupdf.Document, page_index: int, rect, dpi: int) -> tuple[bytes, int, bool]:
    """PNG dell'area: (dati, dpi usati, risoluzione ridotta)."""
    r = _check(rect)
    used, reduced = _fit_dpi(dpi, r.width * r.height)
    return _pixmap(pdf, page_index, r, used).tobytes("png"), used, reduced


def render_area_multi(pdf: pymupdf.Document, parts: list[tuple[int, object]], dpi: int) -> tuple[bytes, int, bool]:
    """Porzioni di più pagine (vista continua) impilate dall'alto in basso in un PNG."""
    rects = []
    for pi, rect in parts:
        r = pymupdf.Rect(rect).normalize()
        if r.width >= MIN_AREA and r.height >= MIN_AREA:   # porzioni minime trascurabili
            rects.append((pi, r))
    if not rects:
        raise ValueError("Area troppo piccola: almeno 2×2 punti.")
    if len(rects) == 1:
        return render_area(pdf, rects[0][0], rects[0][1], dpi)
    width_pt = max(r.width for _, r in rects)
    height_pt = sum(r.height for _, r in rects)
    used, reduced = _fit_dpi(dpi, width_pt * height_pt)
    images = []
    for pi, r in rects:
        pix = _pixmap(pdf, pi, r, used)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    out = Image.new("RGB", (max(im.width for im in images), sum(im.height for im in images)), (255, 255, 255))
    y = 0
    for im in images:
        out.paste(im, (0, y))
        y += im.height
    buf = io.BytesIO()
    out.save(buf, "PNG", dpi=(used, used))
    return buf.getvalue(), used, reduced


_SPACES = re.compile(r"[ \t  -​　]+")


def area_text(pdf: pymupdf.Document, page_index: int, rect) -> str:
    """Testo dell'area in ordine di lettura (spazi normalizzati, a capo conservati)."""
    r = _check(rect)
    text = pdf[page_index].get_text("text", clip=r, sort=True)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()
