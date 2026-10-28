"""Immagine ricercabile compressa (OCR-19): separazione a contenuto misto.

La scansione di una pagina diventa:
- per le pagine in bianco e nero, una sola immagine bitonale compressa senza perdita;
- altrimenti uno *sfondo* (testo cancellato, risoluzione ridotta, JPEG) più una *maschera del
  testo* a piena risoluzione, senza perdita, disegnata nel colore del testo.

Nessuna compressione sostituisce simboli simili (la maschera è PNG/Flate): le cifre non cambiano.
"""

from __future__ import annotations

import cv2
import numpy as np

from .preprocess import clean_background, to_gray

EXACT, BALANCED, MAXIMUM = "exact", "balanced", "maximum"
LEVELS = (EXACT, BALANCED, MAXIMUM)
_SETTINGS = {BALANCED: (2, 70), MAXIMUM: (3, 50)}   # (riduzione dello sfondo, qualità JPEG)


def is_bitonal(img: np.ndarray) -> bool:
    """Bianco e nero puro: quasi tutti i pixel vicini al nero o al bianco e nessuna tinta (carta colorata o
    ingiallita resta a colori)."""
    gray = to_gray(img)
    mid = np.count_nonzero((gray > 50) & (gray < 205))
    if mid / max(1, gray.size) >= 0.03:
        return False
    if img.ndim == 3:
        sample = img[::4, ::4].astype(np.int16)
        spread = sample.max(axis=2) - sample.min(axis=2)
        if np.count_nonzero(spread > 20) / max(1, spread.size) > 0.01:
            return False
        if np.median(gray) < 225:      # fondo non bianco: mantenuto come sfondo
            return False
    return True


def text_mask(gray: np.ndarray, boxes: list[tuple]) -> np.ndarray:
    """Maschera dell'inchiostro dentro i riquadri delle parole (soglia di Otsu locale)."""
    h, w = gray.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for (x0, y0, x1, y1) in boxes:
        x0, y0 = max(0, int(x0) - 2), max(0, int(y0) - 2)
        x1, y1 = min(w, int(x1) + 2), min(h, int(y1) + 2)
        region = gray[y0:y1, x0:x1]
        if region.size < 4:
            continue
        t, _bw = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask[y0:y1, x0:x1] |= (region <= t).astype(np.uint8) * 255
    return mask


def compress_page(img: np.ndarray, boxes: list[tuple], level: str = BALANCED) -> tuple[bytes, bytes | None]:
    """Restituisce (sfondo, primo piano o None). `boxes` = riquadri delle parole in pixel di `img`."""
    gray = to_gray(img)
    if is_bitonal(img):
        _t, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ok, png = cv2.imencode(".png", bw, [cv2.IMWRITE_PNG_COMPRESSION, 9, cv2.IMWRITE_PNG_BILEVEL, 1])
        return png.tobytes(), None
    factor, quality = _SETTINGS.get(level, _SETTINGS[BALANCED])
    mask = text_mask(gray, boxes)
    ink = img[mask > 0] if img.ndim == 3 else None
    color = np.median(ink, axis=0).astype(np.uint8) if ink is not None and len(ink) else np.array([0, 0, 0], np.uint8)
    if int(color.max()) < 90:
        color = np.array([0, 0, 0], np.uint8)
    bg = clean_background(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB), boxes)
    h, w = gray.shape[:2]
    small = cv2.resize(bg, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    fg = np.zeros((h, w, 4), np.uint8)
    fg[..., 0], fg[..., 1], fg[..., 2] = int(color[2]), int(color[1]), int(color[0])   # BGR
    fg[..., 3] = mask
    ok2, png = cv2.imencode(".png", fg, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    return jpg.tobytes(), png.tobytes()


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Similarità strutturale (SSIM) media, su immagini in grigi della stessa dimensione."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    va = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a ** 2
    vb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b ** 2
    cov = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    s = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(s.mean())
