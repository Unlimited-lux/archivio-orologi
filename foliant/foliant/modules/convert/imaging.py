"""Elaborazioni delle immagini di pagina (senza interfaccia): raddrizzamento e ritaglio automatici."""

from __future__ import annotations

import numpy as np


def estimate_skew(img, max_angle: float = 6.0, step: float = 0.2) -> float:
    """Inclinazione del testo in gradi (positivo = antiorario), dal profilo di proiezione delle righe.

    Si cerca l'angolo che rende più «nette» le righe di testo (massima varianza
    delle somme per riga) su un'immagine ridotta.
    """
    from PIL import Image
    g = img.convert("L")
    scale = 900 / max(g.size) if max(g.size) > 900 else 1.0
    if scale != 1.0:
        g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))))
    arr = np.asarray(g, dtype=np.uint8)
    if (arr < 128).mean() < 0.002:      # pagina quasi vuota
        return 0.0
    ink = Image.fromarray(((arr < 128) * 255).astype(np.uint8))

    def score(angle: float) -> float:
        rot = np.asarray(ink.rotate(angle, resample=Image.Resampling.NEAREST, expand=False), dtype=np.float32)
        rows = rot.sum(axis=1)
        return float(np.var(rows))

    coarse = np.arange(-max_angle, max_angle + 1e-9, 1.0)
    best = max(coarse, key=score)
    fine = np.arange(best - 1.0, best + 1.0 + 1e-9, step)
    best = max(fine, key=score)
    return float(round(best, 2))


def deskew(img, min_angle: float = 0.15):
    """Immagine raddrizzata (sfondo bianco) e angolo applicato."""
    from PIL import Image
    angle = estimate_skew(img)
    if abs(angle) < min_angle:
        return img, 0.0
    fill = 255 if img.mode in ("L", "1") else (255, 255, 255)
    src = img.convert("L") if img.mode == "1" else img
    out = src.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=fill)
    if img.mode == "1":
        out = out.point(lambda v: 255 if v > 127 else 0).convert("1")
    out.info.update(img.info)
    return out, angle


def autocrop(img, margin_px: int = 8, threshold: int = 200):
    """Ritaglia i bordi scuri/vuoti dello scanner attorno al foglio (o al contenuto)."""
    arr = np.asarray(img.convert("L"))
    h, w = arr.shape
    # bordi scuri esterni (coperchio aperto): righe/colonne prevalentemente scure
    rows_dark = (arr < 60).mean(axis=1) > 0.8
    cols_dark = (arr < 60).mean(axis=0) > 0.8
    top = int(np.argmax(~rows_dark)) if rows_dark.any() else 0
    bottom = h - int(np.argmax(~rows_dark[::-1])) if rows_dark.any() else h
    left = int(np.argmax(~cols_dark)) if cols_dark.any() else 0
    right = w - int(np.argmax(~cols_dark[::-1])) if cols_dark.any() else w
    if (top, bottom, left, right) == (0, h, 0, w):
        return img
    box = (max(0, left + margin_px), max(0, top + margin_px), min(w, right - margin_px), min(h, bottom - margin_px))
    if box[2] - box[0] < w * 0.3 or box[3] - box[1] < h * 0.3:
        return img
    out = img.crop(box)
    out.info.update(img.info)
    return out
