"""Pre-elaborazione delle immagini di pagina prima del riconoscimento (OpenCV).

Tutte le funzioni lavorano su array numpy (scala di grigi `HxW` o colore `HxWx3`
RGB, uint8) e non toccano né il motore PDF né l'interfaccia.

La trasformazione geometrica (rotazione a multipli di 90° + raddrizzamento) è
descritta da una matrice affine 2x3 «originale → elaborata», conservata nel
risultato per riportare le coordinate delle parole sulla pagina originale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class PreprocessOptions:
    deskew: bool = True          # raddrizzamento delle scansioni inclinate
    auto_rotate: bool = True     # rotazione automatica a 90/180/270° (OSD)
    denoise: bool = True         # rimozione del rumore granulare
    contrast: bool = True        # miglioramento del contrasto se basso
    max_skew: float = 15.0       # inclinazione massima cercata (gradi)
    borders: bool = True         # rimozione dei bordi neri di scansione
    perspective: bool = True     # foto di documenti: raddrizzamento della prospettiva

    def as_dict(self) -> dict:
        return {"deskew": self.deskew, "auto_rotate": self.auto_rotate, "denoise": self.denoise,
                "contrast": self.contrast, "max_skew": self.max_skew, "borders": self.borders,
                "perspective": self.perspective}


@dataclass
class Prepared:
    """Immagine pronta per l'OCR e trasformazione dall'originale."""

    gray: np.ndarray                      # immagine per il motore OCR
    affine: np.ndarray                    # 2x3, pixel originali → pixel elaborati
    rotation: int = 0                     # rotazione a 90° applicata (senso orario)
    skew: float = 0.0                     # inclinazione corretta (gradi, antiorario positivo)
    size: tuple[int, int] = (0, 0)        # (larghezza, altezza) elaborata
    notes: list[str] = field(default_factory=list)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def ocr_gray(img: np.ndarray) -> np.ndarray:
    """Grigio per il riconoscimento: i timbri colorati che coprono il testo nero si attenuano.

    Un timbro rosso o blu sopra una riga la rende illeggibile in scala di grigi (il rosso pieno
    diventa grigio scuro come l'inchiostro). Nei pixel colorati di un timbro che si sovrappone a
    testo neutro si usa il canale più chiaro, dove l'inchiostro colorato quasi scompare e il nero
    resta nero. Il testo stampato a colori lontano dal testo nero (titoli, loghi) resta intatto."""
    gray = to_gray(img)
    if img.ndim == 2:
        return gray
    rgb = img[..., :3]
    hi = rgb.max(axis=2)
    chroma = (hi.astype(np.int16) - rgb.min(axis=2)).astype(np.int16)
    colored = (chroma > 60).astype(np.uint8)
    if colored.mean() < 0.0005:
        return gray
    neutral_ink = ((chroma < 40) & (hi < 110)).astype(np.uint8)
    n, labels, stats, _c = cv2.connectedComponentsWithStats(cv2.dilate(colored, np.ones((9, 9), np.uint8)))
    out = gray.copy()
    stamps: list[tuple[int, int, int, int]] = []
    # dal più grande: le parti di un timbro (cornice, lettere) cadono dentro la sagoma già trovata
    for i in sorted(range(1, n), key=lambda k: -int(stats[k][2] * stats[k][3])):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 200:
            continue
        inside = any(x >= sx and y >= sy and x + w <= sx + sw and y + h <= sy + sh for sx, sy, sw, sh in stamps)
        # testo nero nel riquadro del timbro: si attenua il timbro
        if not inside and neutral_ink[y:y + h, x:x + w].mean() < 0.01:
            continue
        stamps.append((x, y, w, h))
        sel = (labels[y:y + h, x:x + w] == i) & (colored[y:y + h, x:x + w] > 0)
        out[y:y + h, x:x + w][sel] = hi[y:y + h, x:x + w][sel]
    return out


def paper_color(img: np.ndarray) -> tuple[int, ...]:
    """Colore dominante dello sfondo (mediana dei pixel chiari)."""
    gray = to_gray(img)
    thr = np.percentile(gray, 60)
    mask = gray >= thr
    if img.ndim == 2:
        return (int(np.median(img[mask])) if mask.any() else 255,)
    if not mask.any():
        return (255, 255, 255)
    return tuple(int(np.median(img[..., c][mask])) for c in range(img.shape[2]))


def binarize(gray: np.ndarray) -> np.ndarray:
    """Inchiostro = 255, carta = 0 (Otsu)."""
    _t, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def estimate_skew(gray: np.ndarray, max_angle: float = 15.0) -> float:
    """Stima dell'inclinazione delle righe di testo in gradi.

    Metodo del profilo di proiezione: l'angolo che rende più «nette» le righe
    (massima varianza delle somme per riga) è quello di raddrizzamento.
    Restituisce l'angolo (antiorario positivo) di cui ruotare l'immagine.
    """
    h, w = gray.shape[:2]
    scale = min(1.0, 1000.0 / max(h, w))
    small = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    bw = binarize(small)
    # rimuove punti isolati (rumore) che appiattiscono il profilo
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    if cv2.countNonZero(bw) < 50:
        return 0.0
    sh, sw = bw.shape
    center = (sw / 2, sh / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(bw, m, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = rot.sum(axis=1, dtype=np.float64)
        return float(np.var(prof))

    best, best_s = 0.0, score(0.0)
    step = 1.0
    a = -max_angle
    while a <= max_angle + 1e-9:
        s = score(a)
        if s > best_s:
            best, best_s = a, s
        a += step
    for step in (0.25, 0.05):
        center_a = best
        for k in range(-4, 5):
            a = center_a + k * step
            if abs(a) > max_angle:
                continue
            s = score(a)
            if s > best_s:
                best, best_s = a, s
    return round(best, 2) if abs(best) >= 0.1 else 0.0


def rotation_affine(w: int, h: int, quarter_turns_cw: int, angle: float) -> tuple[np.ndarray, tuple[int, int]]:
    """Affine «originale → elaborata»: rotazione oraria a 90° poi raddrizzamento.

    Il raddrizzamento ruota attorno al centro mantenendo le dimensioni (le
    piccole perdite agli angoli cadono nei margini).
    """
    q = quarter_turns_cw % 4
    if q == 0:
        m = np.array([[1, 0, 0], [0, 1, 0]], np.float64)
        size = (w, h)
    elif q == 1:   # 90° orario: (x, y) → (h-1-y, x)
        m = np.array([[0, -1, h - 1], [1, 0, 0]], np.float64)
        size = (h, w)
    elif q == 2:
        m = np.array([[-1, 0, w - 1], [0, -1, h - 1]], np.float64)
        size = (w, h)
    else:          # 270° orario: (x, y) → (y, w-1-x)
        m = np.array([[0, 1, 0], [-1, 0, w - 1]], np.float64)
        size = (h, w)
    if angle:
        r = cv2.getRotationMatrix2D((size[0] / 2, size[1] / 2), angle, 1.0)
        m = compose(r, m)
    return m, size


def compose(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Affine equivalente ad applicare prima `b` poi `a`."""
    a3 = np.vstack([a, [0, 0, 1]])
    b3 = np.vstack([b, [0, 0, 1]])
    return (a3 @ b3)[:2]


def invert(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, np.float64)
    if m.shape == (3, 3):          # omografia (correzione di prospettiva)
        return np.linalg.inv(m)
    return cv2.invertAffineTransform(m)


def apply_point(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    m = np.asarray(m, np.float64)
    if m.shape == (3, 3):
        d = m[2, 0] * x + m[2, 1] * y + m[2, 2]
        return ((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / d, (m[1, 0] * x + m[1, 1] * y + m[1, 2]) / d)
    return (m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2])


def warp(img: np.ndarray, m: np.ndarray, size: tuple[int, int], border=None) -> np.ndarray:
    if border is None:
        border = paper_color(img)
    if img.ndim == 3 and len(border) == 1:
        border = border * img.shape[2]
    if np.asarray(m).shape == (3, 3):
        return cv2.warpPerspective(img, np.asarray(m, np.float64), size, flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=tuple(int(c) for c in border))
    if np.allclose(m, [[1, 0, 0], [0, 1, 0]]) and size == (img.shape[1], img.shape[0]):
        return img
    return cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=tuple(int(c) for c in border))


def remove_borders(gray: np.ndarray) -> np.ndarray:
    """Toglie i bordi neri della scansione: zone scure grandi che toccano il margine dell'immagine."""
    h, w = gray.shape[:2]
    dark = (gray < 90).astype(np.uint8)
    if not dark.any():
        return gray
    n, labels, stats, _c = cv2.connectedComponentsWithStats(dark, 4)
    out = gray
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        touches = x == 0 or y == 0 or x + ww >= w or y + hh >= h
        large = ww >= 0.3 * w or hh >= 0.3 * h
        if touches and large and area >= 0.4 * min(ww, hh) * max(ww, hh) * 0.1:
            if out is gray:
                out = gray.copy()
            out[labels == i] = 255
    return out


def enhance(gray: np.ndarray, opts: PreprocessOptions) -> np.ndarray:
    out = remove_borders(gray) if opts.borders else gray
    if opts.denoise:
        # il filtro mediano elimina il rumore «sale e pepe» senza sfumare i bordi
        out = cv2.medianBlur(out, 3)
    if opts.contrast:
        # fondo non uniforme (luce radente, ombre, carta ingiallita a chiazze): si normalizza lo sfondo
        bg = cv2.medianBlur(cv2.resize(out, (max(1, out.shape[1] // 8), max(1, out.shape[0] // 8))), 5)
        bg = cv2.resize(bg, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_LINEAR)
        if float(np.std(bg)) > 12:
            out = cv2.divide(out, np.maximum(bg, 1), scale=255)
        # contrasto basso (testo sbiadito): inchiostro e carta stimati con Otsu, non con i percentili
        # (su una pagina con poco testo il 1° percentile è già un grigio del bordo delle lettere)
        t, _bw = cv2.threshold(out, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ink = out[out <= t]
        paper = out[out > t]
        if ink.size and paper.size:
            ink_level = float(np.median(ink))
            paper_level = float(np.median(paper))
            if paper_level - ink_level < 110:
                lo = ink_level
                hi = paper_level
                scale = 225.0 / max(1.0, hi - lo)
                out = np.clip((out.astype(np.float32) - lo) * scale + 20, 0, 255).astype(np.uint8)
    return out


def prepare(img: np.ndarray, opts: PreprocessOptions, osd_rotation: int = 0) -> Prepared:
    """Ruota (a 90°, valore OSD), raddrizza e migliora l'immagine per l'OCR."""
    gray = ocr_gray(img)
    h, w = gray.shape[:2]
    quarter = (osd_rotation // 90) % 4 if opts.auto_rotate else 0
    m, size = rotation_affine(w, h, quarter, 0.0)
    base = warp(gray, m, size, (255,))
    if opts.perspective:
        found = find_document_quad(base)
        if found is not None:
            hm, out_size = found
            total = hm @ np.vstack([m, [0, 0, 1]])
            out = enhance(warp(gray, total, out_size, (255,)), opts)
            return Prepared(gray=out, affine=total, rotation=quarter * 90, skew=0.0, size=out_size,
                            notes=["perspective"])
    probe = remove_borders(base) if opts.borders else base
    angle = estimate_skew(probe, opts.max_skew) if opts.deskew else 0.0
    if angle:
        m, size = rotation_affine(w, h, quarter, angle)
        base = warp(gray, m, size)
    out = enhance(base, opts)
    return Prepared(gray=out, affine=m, rotation=quarter * 90, skew=angle, size=size)


def clean_background(img: np.ndarray, boxes: list[tuple[int, int, int, int]], pad: int = 2) -> np.ndarray:
    """Cancella dall'immagine le aree delle parole riempiendole col colore di fondo locale.

    Per ogni riquadro si stima il fondo dalla cornice di pixel attorno (mediana
    dei pixel chiari) così da funzionare anche su carta colorata o ingiallita.
    """
    out = img.copy()
    h, w = out.shape[:2]
    gray = to_gray(out)
    for (x0, y0, x1, y1) in boxes:
        x0 = max(0, int(x0) - pad)
        y0 = max(0, int(y0) - pad)
        x1 = min(w, int(x1) + pad)
        y1 = min(h, int(y1) + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        m = max(3, (y1 - y0) // 4)
        X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
        ring_mask = np.ones((Y1 - Y0, X1 - X0), bool)
        ring_mask[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = False
        ring_gray = gray[Y0:Y1, X0:X1][ring_mask]
        if ring_gray.size == 0:
            continue
        light = ring_gray >= np.percentile(ring_gray, 50)
        if out.ndim == 2:
            vals = out[Y0:Y1, X0:X1][ring_mask][light]
            out[y0:y1, x0:x1] = int(np.median(vals)) if vals.size else 255
        else:
            region = out[Y0:Y1, X0:X1][ring_mask][light]
            color = np.median(region, axis=0).astype(np.uint8) if region.size else np.array([255] * out.shape[2], np.uint8)
            out[y0:y1, x0:x1] = color
    return out


def ink_color(img: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """Colore stimato del testo nel riquadro (mediana dei pixel d'inchiostro), 0–1."""
    x0, y0, x1, y1 = (int(v) for v in box)
    region = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if region.size == 0:
        return (0.0, 0.0, 0.0)
    gray = to_gray(region)
    _t, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # solo il «cuore» dei tratti: esclude i bordi sfumati e i punti isolati del rumore
    core = cv2.erode(ink, np.ones((2, 2), np.uint8)).astype(bool)
    mask = core if core.sum() >= 20 else ink.astype(bool)
    if not mask.any():
        return (0.0, 0.0, 0.0)
    if region.ndim == 2:
        v = float(np.median(region[mask])) / 255.0
        rgb = (v, v, v)
    else:
        rgb = tuple(float(np.median(region[..., c][mask])) / 255.0 for c in range(3))
    # testo stampato quasi nero o grigio scuro → nero
    if max(rgb) - min(rgb) < 0.15 and max(rgb) < 0.55:
        return (0.0, 0.0, 0.0)
    return tuple(round(c * 20) / 20 for c in rgb)


def stroke_width(gray: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """Spessore medio dei tratti (pixel) nel riquadro: serve a riconoscere il grassetto."""
    x0, y0, x1, y1 = (int(v) for v in box)
    region = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if region.size == 0:
        return 0.0
    _t, bw = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    if cv2.countNonZero(bw) == 0:
        return 0.0
    dist = cv2.distanceTransform(bw, cv2.DIST_L2, 3)
    # massimi locali della distanza ≈ metà spessore del tratto
    ridge = (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8))) & (dist > 0)
    vals = dist[ridge]
    return float(2 * np.median(vals)) if vals.size else 0.0


def ink_density(gray: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = (int(v) for v in box)
    region = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if region.size == 0:
        return 0.0
    _t, bw = cv2.threshold(region, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return float(bw.mean())


def text_lines_present(gray: np.ndarray) -> bool:
    """Indica se un'immagine sembra contenere righe di testo (euristica veloce)."""
    if gray.size == 0 or min(gray.shape[:2]) < 16:
        return False
    bw = binarize(gray)
    if bw.mean() > 128:  # prevalenza di scuro: probabilmente una foto
        bw = 255 - bw
    k = max(3, gray.shape[1] // 80)
    joined = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((1, k), np.uint8))
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(joined, 8)
    lines = 0
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if 4 <= hh <= 60 and ww >= 4 * hh and area >= 0.25 * ww * hh:
            lines += 1
    return lines >= 3


def angle_of(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


def slant(gray: np.ndarray, box) -> float:
    """Inclinazione dei tratti verticali (tangente): > 0,12 circa indica il corsivo."""
    x0, y0, x1, y1 = (int(v) for v in box)
    reg = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if reg.size == 0 or reg.shape[0] < 6:
        return 0.0
    _t, bw = cv2.threshold(reg, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    best_k, best_v = 0.0, -1.0
    for k in np.arange(-0.5, 0.51, 0.05):
        m = np.float32([[1, k, -k * h / 2], [0, 1, 0]])
        v = float(np.var(cv2.warpAffine(bw, m, (w, h)).sum(axis=0, dtype=np.float64)))
        if v > best_v:
            best_k, best_v = float(k), v
    return round(best_k, 2)


def serif_ratio(gray: np.ndarray, box, baseline: float, x_height: float) -> float:
    """Inchiostro appena sopra la linea di base ÷ inchiostro a metà dell'occhio medio.

    Le «grazie» aggiungono tratti orizzontali al piede delle aste: il rapporto
    è più alto nei caratteri con grazie."""
    x0, y0, x1, y1 = (int(v) for v in box)
    reg = gray[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if reg.size == 0:
        return 1.0
    _t, bw = cv2.threshold(reg, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    b = int(baseline) - max(0, y0)
    xh = max(3, int(x_height * 0.5))
    foot = float(bw[max(0, b - 2):max(0, b)].sum())
    mid = float(bw[max(0, b - xh - 1):max(1, b - xh + 1)].sum())
    return foot / max(1.0, mid)


def _order_quad(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype(np.float64)
    s_ = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s_)], pts[np.argmin(d)], pts[np.argmax(s_)], pts[np.argmax(d)]])


def find_document_quad(gray: np.ndarray):
    """Foto di un documento: il foglio è un quadrilatero chiaro su uno sfondo più scuro.

    Restituisce (omografia 3x3 «immagine → foglio raddrizzato», dimensioni) oppure None (scansione
    normale: il foglio occupa già l'immagine, oppure la forma trovata non è un foglio).
    """
    h, w = gray.shape[:2]
    scale = min(1.0, 800.0 / max(h, w))
    small = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    _t, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _hier = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    total = small.shape[0] * small.shape[1]
    if not 0.2 * total <= area <= 0.95 * total:
        return None
    approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return None
    mask = np.zeros_like(small)
    cv2.drawContours(mask, [approx], -1, 255, -1)
    inside = float(small[mask > 0].mean())
    outside = float(small[mask == 0].mean()) if (mask == 0).any() else 255.0
    if inside - outside < 40:          # sfondo non più scuro del foglio: non è una foto di documento
        return None
    quad = _order_quad(approx) / scale
    tl, tr, br, bl = quad
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if width < 100 or height < 100:
        return None
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float64)
    hm = cv2.getPerspectiveTransform(quad.astype(np.float32), dst.astype(np.float32)).astype(np.float64)
    return hm, (width, height)
