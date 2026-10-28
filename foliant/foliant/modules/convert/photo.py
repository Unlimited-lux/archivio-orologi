"""Foto di documenti in PDF (CNV-26), senza interfaccia: bordi del foglio, prospettiva, filtri."""

from __future__ import annotations

import io

import numpy as np
import pymupdf

from foliant.api import ENGINE_LOCK, _

from .common import ConversionError, check

FILTERS = ("original", "color", "gray", "bw", "whiteboard")
OUTPUT_SIZES_MM = {"A4": (210, 297), "Letter": (215.9, 279.4), "card": (85.6, 54.0), "id": (85.6, 54.0)}


def _cv():
    try:
        import cv2
        return cv2
    except Exception as e:  # pragma: no cover - dipende dall'installazione
        raise ConversionError(_("Manca il componente di elaborazione delle immagini (opencv-python-headless).")) from e


def load_image(path: str):
    """Immagine RGB (numpy) con l'orientamento EXIF applicato."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            return np.array(im)
    except Exception as e:
        raise ConversionError(_("Immagine non leggibile: {e}").format(e=e)) from e


def order_corners(pts) -> np.ndarray:
    """Angoli in ordine: alto-sinistra, alto-destra, basso-destra, basso-sinistra."""
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def detect_corners(rgb) -> tuple[np.ndarray, bool]:
    """Rileva i 4 angoli del foglio. Restituisce (angoli, trovati); se non trovati,
    gli angoli dell'intera immagine (da regolare a mano)."""
    cv2 = _cv()
    h, w = rgb.shape[:2]
    scale = 800.0 / max(h, w) if max(h, w) > 800 else 1.0
    small = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale != 1.0 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    full = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    best = None
    best_area = 0.0
    for edges in (cv2.Canny(gray, 50, 150), cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]):
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _h = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            area = cv2.contourArea(approx)
            if len(approx) == 4 and cv2.isContourConvex(approx) and area > best_area:
                best, best_area = approx, area
    total = small.shape[0] * small.shape[1]
    if best is None or best_area < 0.15 * total or best_area > 0.97 * total:
        return full, False
    return order_corners(best.reshape(4, 2) / scale), True


def warp(rgb, corners, size_px: tuple[int, int] | None = None):
    """Correzione prospettica: il quadrilatero diventa un rettangolo."""
    cv2 = _cv()
    tl, tr, br, bl = order_corners(corners)
    if size_px is None:
        width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
        height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
        size_px = (max(1, width), max(1, height))
    w, h = size_px
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype="float32"), dst)
    return cv2.warpPerspective(rgb, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def apply_filter(rgb, name: str):
    cv2 = _cv()
    if name == "original":
        return rgb
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if name == "gray":
        return cv2.cvtColor(cv2.equalizeHist(gray), cv2.COLOR_GRAY2RGB)
    if name == "bw":
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
        return cv2.cvtColor(bw, cv2.COLOR_GRAY2RGB)
    # «documento a colori» e «lavagna»: sfondo uniformato al bianco
    bg = cv2.medianBlur(cv2.dilate(rgb, np.ones((7, 7), np.uint8)), 21)
    norm = cv2.divide(rgb, bg, scale=255)
    if name == "whiteboard":
        hsv = cv2.cvtColor(norm, cv2.COLOR_RGB2HSV)
        hsv[..., 1] = cv2.multiply(hsv[..., 1], 1.6)
        norm = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    return norm


def quality_warnings(rgb) -> list[str]:
    """Avvisi su foto mosse (poco nitide) o con riflessi."""
    cv2 = _cv()
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    out = []
    if cv2.Laplacian(gray, cv2.CV_64F).var() < 15:
        out.append(_("La foto sembra mossa o sfocata: il testo potrebbe risultare poco leggibile."))
    if (gray > 250).mean() > 0.25:
        out.append(_("La foto contiene ampi riflessi: prova a cambiare angolazione o illuminazione."))
    return out


def photos_to_pdf(items: list[dict], *, page_size: str | None = "A4", dpi: int = 200, filter_name: str = "color",
                  jpeg_quality: int = 85, progress=None) -> tuple[bytes, list[str]]:
    """Crea il PDF da foto: ogni voce {"path", "corners" (facoltativi), "filter" (facoltativo)}."""
    from PIL import Image
    if not items:
        raise ConversionError(_("Nessuna foto selezionata."))
    warnings: list[str] = []
    pages = []
    for k, item in enumerate(items):
        check(progress, k / len(items), _("Elaborazione della foto {i} di {n}").format(i=k + 1, n=len(items)))
        rgb = load_image(item["path"])
        corners = item.get("corners")
        if corners is None:
            corners, found = detect_corners(rgb)
            if not found:
                warnings.append(_("Foto {i}: bordi del foglio non rilevati, usata l'intera immagine.").format(i=k + 1))
        size_mm = OUTPUT_SIZES_MM.get(page_size or "", None)
        if size_mm:
            tl, tr, br, bl = order_corners(corners)
            landscape = np.linalg.norm(tr - tl) > np.linalg.norm(bl - tl)
            mw, mh = (max(size_mm), min(size_mm)) if landscape else (min(size_mm), max(size_mm))
            size_px = (int(mw / 25.4 * dpi), int(mh / 25.4 * dpi))
        else:
            size_px, mw, mh = None, None, None
        flat = warp(rgb, corners, size_px)
        flat = apply_filter(flat, item.get("filter") or filter_name)
        warnings.extend(f"{_('Foto {i}').format(i=k + 1)}: {w}" for w in quality_warnings(rgb))
        pages.append((flat, mw, mh))
    with ENGINE_LOCK:
        doc = pymupdf.open()
        for flat, mw, mh in pages:
            h, w = flat.shape[:2]
            if mw:
                pw, ph = mw * 72 / 25.4, mh * 72 / 25.4
            else:
                pw, ph = w * 72 / dpi, h * 72 / dpi
            page = doc.new_page(width=pw, height=ph)
            buf = io.BytesIO()
            Image.fromarray(flat).save(buf, format="JPEG", quality=jpeg_quality, dpi=(dpi, dpi))
            page.insert_image(page.rect, stream=buf.getvalue())
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return data, warnings
