"""Classificazione delle pagine per proporre l'OCR (OCR-01).

Categorie:
- ``text``          testo estraibile (PDF nativo digitale)
- ``ocr_layer``     testo invisibile già sovrapposto a una scansione
- ``image_only``    contenuto visibile di sole immagini e < 10 caratteri estraibili
- ``photo``         immagine a tutta pagina senza righe di testo (foto di un orologio): nessuna proposta
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
PHOTO = "photo"

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
        PHOTO: _("immagine senza testo"),
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


def classify_page(page: pymupdf.Page, check_mixed: bool = True, check_photo: bool = False) -> PageInfo:
    """Classifica la pagina. Con `check_photo` una pagina di sole immagini senza caratteri è
    ``photo`` invece di ``image_only`` (costa una resa della pagina: 30-100 ms; l'interfaccia
    del modulo lo fa in un thread, vedi `has_characters`)."""
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
            if check_photo and not visible and not any(_has_characters(page, r) for r in big_images or all_images):
                info.category = PHOTO
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


def page_gray(page: pymupdf.Page, region: pymupdf.Rect | None = None, dpi: int = 100) -> np.ndarray | None:
    """Pagina (o regione) resa in grigi per `has_characters` (va chiamata tenendo ENGINE_LOCK)."""
    try:
        pix = page.get_pixmap(clip=region or page.rect, dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    except Exception:
        return None
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()


def _has_characters(page: pymupdf.Page, region: pymupdf.Rect) -> bool:
    gray = page_gray(page, region)
    return True if gray is None else has_characters(gray)


def has_characters(gray: np.ndarray, min_chars: int = 12) -> bool:
    """Vero se nell'immagine ci sono caratteri: macchie di dimensione da lettera (o parole fuse),
    in fila con vicine di altezza simile, in orizzontale o in verticale. Distingue la foto di un
    documento (anche piccolo, buio o ruotato) dalla foto di un oggetto, dove bordi, indici e
    riflessi non formano file di lettere. Non tocca il motore PDF: si può chiamare da un thread."""
    import cv2
    if min(gray.shape) < 16:
        return False
    # foto buie o sbiadite: si stira il contrasto prima della soglia locale
    # (percentili estremi: su una pagina con due righe il testo è meno dell'1% dei pixel)
    lo, hi = np.percentile(gray, (0.05, 99.95))
    gray = np.clip((gray.astype(np.float32) - lo) * 255.0 / max(1.0, hi - lo), 0, 255).astype(np.uint8)
    for polarity in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):      # testo scuro o chiaro
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, polarity, 31, 12)
        n, _l, stats, _c = cv2.connectedComponentsWithStats(bw, 8)
        st = stats[1:]
        for horizontal in (True, False):                              # righe orizzontali o verticali
            x, y, w, h = (st[:, 0], st[:, 1], st[:, 2], st[:, 3]) if horizontal else \
                (st[:, 1], st[:, 0], st[:, 3], st[:, 2])
            # lettere o parole intere (le lettere vicine si fondono a bassa risoluzione)
            keep = (h >= 5) & (h <= 60) & (w >= 1) & (w <= 10 * h) & (st[:, 4] >= 0.15 * w * h)
            if int(keep.sum()) < min_chars:
                continue
            if _in_rows(x[keep], y[keep], w[keep], h[keep]) >= min_chars:
                return True
    return False


def _in_rows(x, y, w, h) -> int:
    """Quante macchie hanno, a destra e a breve distanza, una vicina della stessa riga e altezza."""
    order = np.argsort(x)
    x, y, w, h = x[order], y[order], w[order], h[order]
    cy = y + h / 2.0
    count = 0
    for k in range(len(x)):
        end = x[k] + w[k] + 1.5 * h[k]
        for j in range(k + 1, len(x)):
            if x[j] > end:
                break
            if abs(cy[j] - cy[k]) < 0.35 * h[k] and 0.6 < h[j] / h[k] < 1.6:
                count += 1
                break
    return count


def classify_document(pdf: pymupdf.Document, pages: list[int] | None = None) -> list[PageInfo]:
    idx = range(pdf.page_count) if pages is None else pages
    return [classify_page(pdf[i]) for i in idx]
