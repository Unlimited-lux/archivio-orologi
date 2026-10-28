"""Classificazione delle pagine per proporre l'OCR (OCR-01).

Categorie:
- ``text``          testo estraibile (PDF nativo digitale)
- ``ocr_layer``     testo invisibile già sovrapposto a una scansione
- ``image_only``    contenuto visibile di sole immagini e < 10 caratteri estraibili
- ``mixed``         testo estraibile più un'immagine estesa che contiene righe di testo
- ``unextractable`` testo presente ma senza mappatura Unicode affidabile
- ``empty``         pagina vuota

Le funzioni vanno chiamate tenendo ENGINE_LOCK (accedono al motore PDF).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf

from foliant.i18n import _

TEXT = "text"
OCR_LAYER = "ocr_layer"
IMAGE_ONLY = "image_only"
MIXED = "mixed"
UNEXTRACTABLE = "unextractable"
EMPTY = "empty"

NEEDS_OCR = (IMAGE_ONLY, MIXED, UNEXTRACTABLE)

MIN_CHARS = 10
IMAGE_ONLY_COVERAGE = 0.5
MIXED_COVERAGE = 0.2
BAD_RATIO = 0.3
LOW_DPI = 150


def category_label(cat: str) -> str:
    return {
        TEXT: _("testo presente"),
        OCR_LAYER: _("livello OCR presente"),
        IMAGE_ONLY: _("solo immagine"),
        MIXED: _("mista (testo e immagini con testo)"),
        UNEXTRACTABLE: _("testo non estraibile"),
        EMPTY: _("vuota"),
    }.get(cat, cat)


@dataclass
class PageInfo:
    index: int
    category: str
    chars: int = 0             # caratteri visibili estraibili
    invisible: int = 0         # caratteri invisibili (Tr 3)
    bad: int = 0               # caratteri senza Unicode affidabile
    coverage: float = 0.0      # frazione dell'area coperta da immagini
    dpi: float = 0.0           # risoluzione effettiva stimata delle immagini (minima)
    text_rects: tuple = ()     # riquadri del testo esistente (per le pagine miste)

    @property
    def needs_ocr(self) -> bool:
        return self.category in NEEDS_OCR

    @property
    def low_resolution(self) -> bool:
        return 0 < self.dpi < LOW_DPI


def _bad_char(c: int) -> bool:
    return c == 0xFFFD or 0xE000 <= c <= 0xF8FF or (c < 0x20 and c not in (9, 10, 13))


def classify_page(page: pymupdf.Page, check_mixed: bool = True) -> PageInfo:
    index = page.number
    area = abs(page.rect) or 1.0
    visible = invisible = bad = 0
    rects: list[pymupdf.Rect] = []
    try:
        traces = page.get_texttrace()
    except Exception:
        traces = []
    for span in traces:
        chars = span.get("chars", ())
        n = sum(1 for ch in chars if ch[0] not in (32, 9, 10, 13))
        bad += sum(1 for ch in chars if _bad_char(ch[0]))
        if span.get("type") == 3 or span.get("opacity", 1) == 0:
            invisible += n
        else:
            visible += n
            bbox = span.get("bbox")
            if bbox:
                rects.append(pymupdf.Rect(bbox))
    coverage = 0.0
    dpi = 0.0
    big_images: list[pymupdf.Rect] = []
    all_images: list[pymupdf.Rect] = []
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    mask_rect = page.rect
    for info in infos:
        r = pymupdf.Rect(info.get("bbox")) & mask_rect
        if r.is_empty:
            continue
        frac = abs(r) / area
        coverage += frac
        all_images.append(r)
        w_px = info.get("width") or 0
        if r.width > 0 and w_px:
            d = w_px / (pymupdf.Rect(info["bbox"]).width / 72.0)
            dpi = d if dpi == 0 else min(dpi, d)
        if frac >= MIXED_COVERAGE:
            big_images.append(r)
    coverage = min(1.0, coverage)
    total = visible + invisible
    info = PageInfo(index, TEXT, visible, invisible, bad, coverage, dpi, tuple(tuple(r) for r in rects))
    if total and bad / max(1, total + bad) > BAD_RATIO and bad >= MIN_CHARS:
        info.category = UNEXTRACTABLE
    elif visible < MIN_CHARS and invisible >= MIN_CHARS:
        info.category = OCR_LAYER
    elif visible < MIN_CHARS:
        if coverage >= IMAGE_ONLY_COVERAGE:
            info.category = IMAGE_ONLY
        elif coverage > 0 and check_mixed and _images_have_text(page, all_images, rects):
            info.category = IMAGE_ONLY
        else:
            info.category = TEXT if visible else EMPTY
    elif big_images and check_mixed and _images_have_text(page, big_images, rects):
        info.category = MIXED
    return info


def _images_have_text(page: pymupdf.Page, regions: list[pymupdf.Rect], text_rects: list[pymupdf.Rect]) -> bool:
    for r in regions:
        try:
            pix = page.get_pixmap(clip=r, dpi=100, colorspace=pymupdf.csGRAY, alpha=False)
        except Exception:
            continue
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()
        # nasconde il testo vettoriale sovrapposto: conta solo il testo dentro l'immagine
        sx = pix.width / max(1e-6, r.width)
        sy = pix.height / max(1e-6, r.height)
        for t in text_rects:
            t = t & r
            if t.is_empty:
                continue
            x0, y0 = int((t.x0 - r.x0) * sx), int((t.y0 - r.y0) * sy)
            x1, y1 = int((t.x1 - r.x0) * sx) + 1, int((t.y1 - r.y0) * sy) + 1
            gray[max(0, y0):y1, max(0, x0):x1] = 255
        from .preprocess import text_lines_present
        if text_lines_present(gray):
            return True
    return False


def classify_document(pdf: pymupdf.Document, pages: list[int] | None = None) -> list[PageInfo]:
    idx = range(pdf.page_count) if pages is None else pages
    return [classify_page(pdf[i]) for i in idx]
