"""Firma grafica: tracciati, testo calligrafico, immagini e inserimento nel PDF.

Logica senza interfaccia (a parte la conversione del testo in contorni, che usa
i font di Qt e richiede solo una QGuiApplication). Tutte le funzioni che
toccano il PDF ricevono il documento del motore e vanno chiamate dentro
`document.change()` (che tiene già ENGINE_LOCK).

Rappresentazione comune: `VectorArt`, una lista di elementi in un riquadro
locale `width`×`height` (unità arbitrarie, y verso il basso):
- ("stroke", [(x, y, w), ...])  tratto con spessore per punto
- ("fill", [("m", x, y), ("l", x, y), ("c", x1, y1, x2, y2, x, y), ("h",), ...])
"""

from __future__ import annotations

import io
import math
import uuid
from dataclasses import dataclass, field

import pymupdf

from foliant.i18n import _

ANNOT_PREFIX = "foliant-sig-"
SUBJECT_SIGNATURE = "Firma"
SUBJECT_INITIALS = "Iniziali"
MIN_SIDE = 8.0
MAX_IMAGE_PIXELS = 50_000_000
MAX_PPI = 300
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "BMP", "TIFF", "GIF", "WEBP", "MPO"}


class GraphicSignatureError(Exception):
    """Errore con messaggio per l'utente."""


# ====================================================================== tratti
Point = tuple  # (x, y, pressione) oppure (x, y, spessore)


def _perp_distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p[:2], a[:2], b[:2]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def decimate(points: list[Point], epsilon: float = 0.4, width_tolerance: float = 0.08) -> list[Point]:
    """Riduce i punti (Ramer–Douglas–Peucker) con errore massimo `epsilon`.

    Un punto viene conservato anche se lo spessore cambia più della tolleranza,
    così la variazione di pressione non si perde.
    """
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i0, i1 = stack.pop()
        best, best_d = None, epsilon
        a, b = points[i0], points[i1]
        for k in range(i0 + 1, i1):
            d = _perp_distance(points[k], a, b)
            if len(points[k]) > 2 and len(a) > 2:
                t = (k - i0) / (i1 - i0)
                w_interp = a[2] + (b[2] - a[2]) * t
                if abs(points[k][2] - w_interp) > width_tolerance:
                    d = max(d, epsilon * 1.01)
            if d > best_d:
                best, best_d = k, d
        if best is not None:
            keep[best] = True
            stack.append((i0, best))
            stack.append((best, i1))
    return [p for p, k in zip(points, keep) if k]


def chaikin(points: list[Point], iterations: int = 2) -> list[Point]:
    """Levigatura per tagli d'angolo: mantiene estremi e spessori interpolati."""
    pts = [tuple(p) for p in points]
    for _i in range(iterations):
        if len(pts) < 3:
            return pts
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            q = tuple(0.75 * u + 0.25 * v for u, v in zip(a, b))
            r = tuple(0.25 * u + 0.75 * v for u, v in zip(a, b))
            out.extend((q, r))
        out.append(pts[-1])
        pts = out
    return pts


def pressure_to_width(pressure: float, base_width: float) -> float:
    """Spessore monotono crescente con la pressione (0..1)."""
    p = max(0.0, min(1.0, float(pressure)))
    return base_width * (0.3 + 0.7 * p)


def process_strokes(raw: list[list[Point]], base_width: float = 2.5, epsilon: float = 0.4) -> list[list[Point]]:
    """Da tratti grezzi (x, y, pressione) in pixel a tratti levigati (x, y, spessore)."""
    out = []
    for stroke in raw:
        if not stroke:
            continue
        pts = [(float(p[0]), float(p[1]), pressure_to_width(p[2] if len(p) > 2 else 1.0, base_width))
               for p in stroke]
        # punti coincidenti consecutivi
        dedup = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - dedup[-1][0], p[1] - dedup[-1][1]) > 1e-6:
                dedup.append(p)
        smooth = chaikin(dedup, 2) if len(dedup) > 2 else dedup
        out.append(decimate(smooth, epsilon))
    return out


def strokes_length(strokes: list[list[Point]]) -> float:
    total = 0.0
    for s in strokes:
        for a, b in zip(s, s[1:]):
            total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def strokes_bbox(strokes: list[list[Point]]) -> tuple[float, float, float, float] | None:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for s in strokes:
        for x, y, w in s:
            h = w / 2
            xs0.append(x - h)
            ys0.append(y - h)
            xs1.append(x + h)
            ys1.append(y + h)
    if not xs0:
        return None
    return min(xs0), min(ys0), max(xs1), max(ys1)


@dataclass
class VectorArt:
    width: float
    height: float
    items: list = field(default_factory=list)

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0


def strokes_to_art(strokes: list[list[Point]]) -> VectorArt:
    """Tratti (x, y, spessore) → disegno normalizzato ritagliato al contenuto.

    Coordinate in 0..1 rispetto all'altezza del riquadro (x fino a aspetto).
    """
    box = strokes_bbox(strokes)
    if box is None:
        raise GraphicSignatureError(_("La firma è vuota."))
    x0, y0, x1, y1 = box
    h = max(y1 - y0, 1e-6)
    w = max(x1 - x0, 1e-6)
    items = [("stroke", [((x - x0) / h, (y - y0) / h, wd / h) for x, y, wd in s]) for s in strokes]
    return VectorArt(w / h, 1.0, items)


# =================================================================== SVG libreria
def art_to_svg(art: VectorArt, color: str = "#000000") -> str:
    """SVG dei soli tracciati (nessun dato di tempo o velocità)."""
    scale = 1000.0
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {art.width * scale:.2f} {art.height * scale:.2f}" '
             f'data-foliant="1">']
    for item in art.items:
        if item[0] == "stroke":
            pts = " ".join(f"{x * scale:.2f},{y * scale:.2f}" for x, y, _w in item[1])
            widths = " ".join(f"{w * scale:.2f}" for _x, _y, w in item[1])
            avg = sum(w for _x, _y, w in item[1]) / max(1, len(item[1])) * scale
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round" '
                         f'stroke-width="{avg:.2f}" points="{pts}" data-widths="{widths}"/>')
        elif item[0] == "fill":
            d = []
            for op in item[1]:
                if op[0] == "h":
                    d.append("Z")
                else:
                    letter = {"m": "M", "l": "L", "c": "C"}[op[0]]
                    d.append(letter + " " + " ".join(f"{v * scale:.2f}" for v in op[1:]))
            parts.append(f'<path fill="{color}" fill-rule="nonzero" d="{" ".join(d)}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def svg_to_art(svg: str) -> VectorArt:
    import re
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise GraphicSignatureError(_("Il file della firma è danneggiato.")) from e
    vb = [float(v) for v in root.get("viewBox", "0 0 1000 1000").split()]
    scale = 1000.0
    art = VectorArt(vb[2] / scale, vb[3] / scale)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "polyline":
            coords = [tuple(float(v) for v in p.split(",")) for p in el.get("points", "").split()]
            widths = [float(v) for v in el.get("data-widths", "").split()] or [float(el.get("stroke-width", 1))] * len(coords)
            art.items.append(("stroke", [(x / scale, y / scale, w / scale) for (x, y), w in zip(coords, widths)]))
        elif tag == "path":
            ops = []
            tokens = re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?(?:e-?\d+)?", el.get("d", ""))
            i = 0
            while i < len(tokens):
                t = tokens[i]
                n = {"M": 2, "L": 2, "C": 6, "Z": 0}[t]
                vals = [float(v) / scale for v in tokens[i + 1:i + 1 + n]]
                ops.append(({"M": "m", "L": "l", "C": "c", "Z": "h"}[t], *vals))
                i += 1 + n
            art.items.append(("fill", ops))
    return art


# ============================================================ testo calligrafico
@dataclass(frozen=True)
class TypedStyle:
    id: str
    family: str
    italic: bool = False
    bold: bool = False
    label: str = ""


_SCRIPT_HINTS = ("script", "hand", "chancery", "cursive", "brush", "z003", "vibes", "dancing", "pacifico",
                 "satisfy", "allura", "sacramento", "caveat", "kalam", "comic", "marck", "parisienne",
                 "italianno", "tangerine", "calligra", "signature", "felipa", "yellowtail")


def typed_styles(min_count: int = 8) -> list[TypedStyle]:
    """Stili disponibili: prima i font di sistema calligrafici, poi varianti
    corsive dei font installati fino ad almeno `min_count` stili.
    L'ultimo è sempre lo stile di riserva ad ampia copertura Unicode."""
    from PySide6.QtGui import QFontDatabase
    families = list(QFontDatabase.families())
    lower = {f: f.lower() for f in families}
    scripts = [f for f in families if any(h in lower[f] for h in _SCRIPT_HINTS)]
    styles: list[TypedStyle] = []
    for f in scripts:
        if missing_characters("AaMmRr", TypedStyle(f"{f}|n", f)):
            continue
        styles.append(TypedStyle(f"{f}|n", f, italic=False, label=f))
    fallback = fallback_family()
    others = [f for f in families if f not in scripts]
    others.sort(key=lambda f: (0 if "serif" in lower[f] and "sans" not in lower[f] else 1, f))
    variants = [(True, False), (True, True), (False, False), (False, True)]
    for italic, bold in variants:
        for f in others:
            if len(styles) >= max(min_count - 1, len(scripts)):
                break
            if f == fallback and italic and not bold:
                continue
            sid = f"{f}|{'i' if italic else 'n'}{'b' if bold else ''}"
            if any(s.id == sid for s in styles):
                continue
            if missing_characters("AaMmRr", TypedStyle(sid, f, italic, bold)):
                continue    # alias o font senza alfabeto latino
            label = f + (" " + _("corsivo") if italic else "") + (" " + _("grassetto") if bold else "")
            styles.append(TypedStyle(sid, f, italic, bold, label))
    styles.append(TypedStyle(f"{fallback}|i", fallback, italic=True, label=_("Riserva ({f})").format(f=fallback)))
    return styles


def fallback_family() -> str:
    from PySide6.QtGui import QFontDatabase
    families = QFontDatabase.families()
    for name in ("DejaVu Sans", "Noto Sans", "Liberation Sans", "FreeSans"):
        if name in families:
            return name
    return families[0] if families else "Sans"


def style_by_id(style_id: str) -> TypedStyle:
    family, _sep, flags = style_id.partition("|")
    return TypedStyle(style_id, family, italic="i" in flags, bold="b" in flags, label=family)


def _qfont(style: TypedStyle, size: float = 64.0):
    from PySide6.QtGui import QFont
    font = QFont(style.family)
    font.setPointSizeF(size)
    font.setItalic(style.italic)
    font.setBold(style.bold)
    font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
    return font


def missing_characters(text: str, style: TypedStyle) -> list[str]:
    """Caratteri del testo non presenti nel font dello stile (spazi esclusi)."""
    from PySide6.QtGui import QFontDatabase, QFontInfo, QRawFont
    font = _qfont(style)
    raw = QRawFont.fromFont(font)
    if (not raw.isValid() or style.family not in QFontDatabase.families()
            or QFontInfo(font).family().lower() != style.family.lower()):
        return sorted({c for c in text if not c.isspace()})
    missing = []
    for ch in text:
        if ch.isspace() or ch in missing:
            continue
        if not raw.supportsCharacter(ord(ch)):
            missing.append(ch)
    return missing


def text_to_art(text: str, style: TypedStyle, slant: float = 0.0) -> VectorArt:
    """Converte il testo in contorni vettoriali (nessun font nel PDF)."""
    from PySide6.QtGui import QPainterPath, QTransform
    text = text.strip()
    if not text:
        raise GraphicSignatureError(_("Il testo è vuoto."))
    path = QPainterPath()
    path.setFillRule(path.fillRule().WindingFill)
    path.addText(0, 0, _qfont(style), text)
    if slant:
        path = QTransform(1, 0, -math.tan(math.radians(slant)), 1, 0, 0).map(path)
    br = path.boundingRect()
    if br.isEmpty():
        raise GraphicSignatureError(_("Lo stile scelto non contiene i caratteri del testo."))
    pad = br.height() * 0.06
    x0, y0 = br.left() - pad, br.top() - pad
    h = br.height() + 2 * pad
    w = br.width() + 2 * pad
    ops: list[tuple] = []
    i = 0
    n = path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.isMoveTo():
            if ops:
                ops.append(("h",))
            ops.append(("m", (e.x - x0) / h, (e.y - y0) / h))
            i += 1
        elif e.isLineTo():
            ops.append(("l", (e.x - x0) / h, (e.y - y0) / h))
            i += 1
        else:  # CurveTo + 2 CurveToData
            c1 = path.elementAt(i + 1)
            c2 = path.elementAt(i + 2)
            ops.append(("c", (e.x - x0) / h, (e.y - y0) / h, (c1.x - x0) / h, (c1.y - y0) / h,
                        (c2.x - x0) / h, (c2.y - y0) / h))
            i += 3
    if ops:
        ops.append(("h",))
    return VectorArt(w / h, 1.0, [("fill", ops)])


# ======================================================================= immagini
def detect_format(data: bytes) -> str:
    sigs = [(b"\x89PNG", "PNG"), (b"\xff\xd8\xff", "JPEG"), (b"BM", "BMP"), (b"II*\x00", "TIFF"),
            (b"MM\x00*", "TIFF"), (b"GIF8", "GIF"), (b"%PDF", "PDF"), (b"PK\x03\x04", "ZIP"),
            (b"<svg", "SVG"), (b"<?xml", "XML")]
    for sig, name in sigs:
        if data.startswith(sig):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "HEIF/MP4"
    return _("sconosciuto")


def load_image(data: bytes):
    """Apre un'immagine di firma come PIL RGBA in sRGB."""
    from PIL import Image, ImageCms, UnidentifiedImageError
    fmt = detect_format(data)
    try:
        Image.MAX_IMAGE_PIXELS = None
        img = Image.open(io.BytesIO(data))
        if img.format not in SUPPORTED_IMAGE_FORMATS:
            raise UnidentifiedImageError()
        w, h = img.size
        if w * h > MAX_IMAGE_PIXELS:
            raise GraphicSignatureError(
                _("L'immagine è troppo grande ({w}×{h} pixel, {mp:.1f} megapixel): il massimo è 50 megapixel.")
                .format(w=w, h=h, mp=w * h / 1e6))
        img.load()
    except GraphicSignatureError:
        raise
    except Exception as e:
        raise GraphicSignatureError(
            _("Formato immagine non supportato (rilevato: {fmt}). Usa PNG, JPEG, BMP, TIFF, GIF o WebP.")
            .format(fmt=fmt)) from e
    icc = img.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            img = ImageCms.profileToProfile(img.convert(mode), src, dst, outputMode=mode)
        except Exception:
            pass
    return img.convert("RGBA")


def has_transparency(img) -> bool:
    import numpy as np
    alpha = np.asarray(img.getchannel("A"))
    return bool((alpha < 250).mean() > 0.01)


def auto_threshold(img) -> int:
    """Soglia di Otsu sulla luminanza (valore iniziale del cursore)."""
    import numpy as np
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = hist.sum()
    if total == 0:
        return 200
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma = (mu_t * omega - mu) ** 2 / (omega * (1 - omega))
    sigma = np.nan_to_num(sigma)
    if sigma.max() <= 0:
        return 200
    # con classi ben separate la varianza è massima su tutto l'intervallo vuoto
    # fra inchiostro e carta: si prende il centro del pianoro
    best = np.flatnonzero(sigma >= sigma.max() * (1 - 1e-6))
    t = int(round((best.min() + best.max()) / 2))
    return max(1, min(254, t))


def remove_background(img, threshold: int | None = None, *, ink_color: str | None = None,
                      rotate: int = 0, keep_alpha: bool | None = None, crop: bool = True):
    """Rende trasparente lo sfondo chiaro e ritaglia al contenuto.

    `threshold`: luminanza oltre la quale un pixel è sfondo (None = automatica).
    `ink_color`: "#rrggbb" per ricolorare i tratti con un colore uniforme.
    `keep_alpha`: se l'immagine è già trasparente si conserva il suo canale alfa
    (predefinito: automatico).
    """
    import numpy as np
    from PIL import Image
    if rotate % 360:
        img = img.rotate(-rotate, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255, 0))
    arr = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgb, alpha = arr[..., :3], arr[..., 3]
    if keep_alpha is None:
        keep_alpha = has_transparency(img)
    if keep_alpha:
        new_alpha = alpha
    else:
        t = auto_threshold(img) if threshold is None else int(threshold)
        lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
        if threshold is None and background_is_uneven(lum, t):
            # sfondo non uniforme (foto con ombre): soglia locale rispetto allo sfondo vicino
            local = _local_mean(lum, max(15, min(lum.shape) // 8))
            t_map = local * (t / max(float(np.median(lum[lum > t])) if (lum > t).any() else 255.0, 1.0))
            lo_map = t_map * 0.7
            ramp = np.clip((t_map - lum) / np.maximum(t_map - lo_map, 1.0), 0.0, 1.0)
        else:
            lo = t * 0.7
            ramp = np.clip((t - lum) / max(t - lo, 1.0), 0.0, 1.0)
        new_alpha = ramp * 255.0 * (alpha / 255.0)
    if ink_color:
        c = ink_color.lstrip("#")
        rgb = np.broadcast_to(np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32), rgb.shape)
    out = np.dstack([rgb, new_alpha]).clip(0, 255).astype(np.uint8)
    mask = out[..., 3] > 16
    if not mask.any():
        raise GraphicSignatureError(_("Nessun tratto rilevato: prova a cambiare la soglia dello sfondo."))
    if not crop:
        return Image.fromarray(out, "RGBA")
    ys, xs = np.nonzero(mask)
    pad = 2
    y0, y1 = max(0, ys.min() - pad), min(out.shape[0], ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(out.shape[1], xs.max() + 1 + pad)
    return Image.fromarray(out[y0:y1, x0:x1], "RGBA")


def _local_mean(arr, radius: int):
    """Media su finestra quadrata (immagine integrale), stessa forma dell'ingresso."""
    import numpy as np
    pad = np.pad(arr, radius + 1, mode="edge").astype(np.float64)
    ii = pad.cumsum(0).cumsum(1)
    k = 2 * radius + 1
    h, w = arr.shape
    s = ii[k:k + h, k:k + w] - ii[0:h, k:k + w] - ii[k:k + h, 0:w] + ii[0:h, 0:w]
    return s / (k * k)


def background_is_uneven(lum, threshold: float) -> bool:
    """True se lo sfondo (pixel chiari) varia molto fra le zone dell'immagine."""
    import numpy as np
    h, w = lum.shape
    if h < 8 or w < 8:
        return False
    means = []
    for i in range(4):
        for j in range(4):
            block = lum[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4]
            bg = block[block > threshold * 0.8]
            if bg.size:
                means.append(float(np.percentile(bg, 75)))
    return len(means) >= 4 and (max(means) - min(means)) > 40


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ============================================================ elemento di firma
@dataclass
class SignatureItem:
    """Elemento di firma (anche non salvato in libreria)."""

    kind: str = "firma"              # "firma" | "iniziali"
    origin: str = "disegnata"        # "disegnata" | "digitata" | "immagine"
    name: str = ""
    color: str = "#000000"
    art: VectorArt | None = None     # disegnata o digitata
    png: bytes | None = None         # immagine
    text: str = ""                   # digitata
    style_id: str = ""
    slant: float = 0.0
    id: str = ""

    @property
    def aspect(self) -> float:
        if self.art is not None:
            return self.art.aspect
        if self.png:
            from PIL import Image
            w, h = Image.open(io.BytesIO(self.png)).size
            return w / h if h else 1.0
        return 3.0

    @property
    def subject(self) -> str:
        return SUBJECT_INITIALS if self.kind == "iniziali" else SUBJECT_SIGNATURE

    def default_size(self) -> tuple[float, float]:
        h = 28.0 if self.kind == "iniziali" else 40.0
        w = h * self.aspect
        max_w = 120.0 if self.kind == "iniziali" else 220.0
        if w > max_w:
            w, h = max_w, max_w / self.aspect
        return w, h


# ================================================================ inserimento PDF
def _hex_rgb(color: str) -> tuple[float, float, float]:
    c = (color or "#000000").lstrip("#")
    if len(c) != 6:
        return 0.0, 0.0, 0.0
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def art_content(art: VectorArt, width: float, height: float, color: str) -> bytes:
    """Stream di contenuto (solo tracciati) nel riquadro [0 0 width height]."""
    sx = width / art.width
    sy = height / art.height
    s = min(sx, sy)
    r, g, b = _hex_rgb(color)
    out = [f"{_f(r)} {_f(g)} {_f(b)} RG {_f(r)} {_f(g)} {_f(b)} rg 1 J 1 j"]

    def X(x):
        return _f(x * sx)

    def Y(y):
        return _f(height - y * sy)

    for item in art.items:
        if item[0] == "stroke":
            pts = item[1]
            if not pts:
                continue
            widths = [p[2] for p in pts]
            if len(pts) == 1:
                x, y, w = pts[0]
                out.append(f"{_f(w * s)} w {X(x)} {Y(y)} m {X(x)} {Y(y)} l S")
            elif max(widths) - min(widths) < 1e-3:
                seg = [f"{_f(widths[0] * s)} w {X(pts[0][0])} {Y(pts[0][1])} m"]
                seg += [f"{X(x)} {Y(y)} l" for x, y, _w in pts[1:]]
                out.append(" ".join(seg) + " S")
            else:
                for a, bpt in zip(pts, pts[1:]):
                    w = (a[2] + bpt[2]) / 2
                    out.append(f"{_f(w * s)} w {X(a[0])} {Y(a[1])} m {X(bpt[0])} {Y(bpt[1])} l S")
        elif item[0] == "fill":
            seg = []
            for op in item[1]:
                if op[0] == "m":
                    seg.append(f"{X(op[1])} {Y(op[2])} m")
                elif op[0] == "l":
                    seg.append(f"{X(op[1])} {Y(op[2])} l")
                elif op[0] == "c":
                    seg.append(f"{X(op[1])} {Y(op[2])} {X(op[3])} {Y(op[4])} {X(op[5])} {Y(op[6])} c")
                elif op[0] == "h":
                    seg.append("h")
            if seg:
                out.append(" ".join(seg) + " f")
    return "\n".join(out).encode("latin-1")


def _rotation_matrix(rotation: float, w: float, h: float) -> str:
    """/Matrix che raddrizza l'aspetto rispetto alla vista per /Rotate (o ruota di un angolo libero)."""
    rot = rotation % 360
    if abs(rot - round(rot)) > 1e-6 or round(rot) % 90:
        c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return f"[{_f(c)} {_f(sn)} {_f(-sn)} {_f(c)} 0 0]"
    rot = int(round(rot)) % 360
    if rot == 90:
        return f"[0 1 -1 0 {_f(h)} 0]"
    if rot == 180:
        return f"[-1 0 0 -1 {_f(w)} {_f(h)}]"
    if rot == 270:
        return f"[0 -1 1 0 0 {_f(w)}]"
    return "[1 0 0 1 0 0]"


def _image_xobject(pdf: pymupdf.Document, png: bytes, max_px: tuple[int, int], pdfa1: bool = False) -> int:
    """Image XObject RGB con /SMask (o maschera a 1 bit per PDF/A-1), senza perdita."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    mw, mh = max(1, int(max_px[0])), max(1, int(max_px[1]))
    if w > mw or h > mh:
        f = min(mw / w, mh / h)
        img = img.resize((max(1, int(w * f)), max(1, int(h * f))), Image.Resampling.LANCZOS)
        w, h = img.size
    arr = np.asarray(img)
    rgb = arr[..., :3].tobytes()
    alpha = arr[..., 3]
    img_xref = pdf.get_new_xref()
    if pdfa1:
        bits = np.packbits(alpha < 128, axis=1).tobytes()
        mask_xref = pdf.get_new_xref()
        pdf.update_object(mask_xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ImageMask true"
                                     f"/BitsPerComponent 1>>")
        pdf.update_stream(mask_xref, bits, compress=True)
        pdf.update_object(img_xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace/DeviceRGB"
                                    f"/BitsPerComponent 8/Mask {mask_xref} 0 R>>")
    else:
        smask_xref = pdf.get_new_xref()
        pdf.update_object(smask_xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace/DeviceGray"
                                      f"/BitsPerComponent 8>>")
        pdf.update_stream(smask_xref, alpha.tobytes(), compress=True)
        pdf.update_object(img_xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace/DeviceRGB"
                                    f"/BitsPerComponent 8/SMask {smask_xref} 0 R>>")
    pdf.update_stream(img_xref, rgb, compress=True)
    return img_xref


def is_pdfa1(pdf: pymupdf.Document) -> bool:
    try:
        xml = pdf.get_xml_metadata() or ""
    except Exception:
        return False
    return "pdfaid:part=\"1\"" in xml or "<pdfaid:part>1</pdfaid:part>" in xml


def make_appearance(pdf: pymupdf.Document, item: SignatureItem, width: float, height: float,
                    rotation: int = 0, opacity: float = 1.0, color: str | None = None) -> int:
    """Crea il Form XObject di aspetto (dimensioni visive width×height) e ne restituisce lo xref."""
    rot = rotation % 360
    bw, bh = (width, height)
    resources = ""
    if item.art is not None:
        content = art_content(item.art, bw, bh, color or item.color)
    elif item.png:
        px = (bw / 72 * MAX_PPI, bh / 72 * MAX_PPI)
        im = _image_xobject(pdf, item.png, px, pdfa1=is_pdfa1(pdf))
        content = f"q {_f(bw)} 0 0 {_f(bh)} 0 0 cm /Im0 Do Q".encode()
        resources = f"/XObject<</Im0 {im} 0 R>>"
    else:
        raise GraphicSignatureError(_("L'elemento di firma non ha contenuto."))
    gs = ""
    if opacity < 0.999:
        gs = f"/ExtGState<</G0<</Type/ExtGState/CA {_f(opacity)}/ca {_f(opacity)}>>>>"
        content = b"/G0 gs\n" + content
    xref = pdf.get_new_xref()
    pdf.update_object(xref, f"<</Type/XObject/Subtype/Form/FormType 1/BBox[0 0 {_f(bw)} {_f(bh)}]"
                            f"/Matrix{_rotation_matrix(rot, bw, bh)}/Resources<<{resources}{gs}>>>>")
    pdf.update_stream(xref, content, compress=True)
    return xref


def _inherited_box(page: pymupdf.Page, key: str) -> list[float] | None:
    doc = page.parent
    xref = page.xref
    for _i in range(64):
        kind, val = doc.xref_get_key(xref, key)
        if kind == "array":
            return [float(v) for v in val.strip("[]").split()]
        kind, parent = doc.xref_get_key(xref, "Parent")
        if kind != "xref":
            return None
        xref = int(parent.split()[0])
    return None


def page_to_pdf_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Coordinate non ruotate del motore (origine in alto a sinistra del CropBox)
    → spazio utente PDF. Calcolata da /CropBox e /MediaBox perché la matrice del
    motore è errata sulle pagine ruotate con CropBox spostato."""
    media = _inherited_box(page, "MediaBox") or [0, 0, 612, 792]
    crop = _inherited_box(page, "CropBox") or media
    mx0, my0, mx1, my1 = min(media[0], media[2]), min(media[1], media[3]), max(media[0], media[2]), \
        max(media[1], media[3])
    cx0 = max(min(crop[0], crop[2]), mx0)
    cy1 = min(max(crop[1], crop[3]), my1)
    return pymupdf.Matrix(1, 0, 0, -1, cx0, cy1)


def _pdf_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    r = pymupdf.Rect(rect) * page_to_pdf_matrix(page)
    r.normalize()
    return r


def visual_size(rect: pymupdf.Rect, rotation: int) -> tuple[float, float]:
    """Dimensioni visive (come appaiono nella vista) di un riquadro in coordinate non ruotate."""
    if rotation % 180 == 90:
        return rect.height, rect.width
    return rect.width, rect.height


def unrotated_rect(page: pymupdf.Page, visual_rect: pymupdf.Rect) -> pymupdf.Rect:
    """Riquadro in coordinate della vista (pagina ruotata) → coordinate non ruotate."""
    r = pymupdf.Rect(visual_rect) * page.derotation_matrix
    r.normalize()
    return r


def page_area(page: pymupdf.Page) -> pymupdf.Rect:
    """Parte visibile della pagina (CropBox) nelle coordinate non ruotate usate da
    eventi e annotazioni: l'origine è l'angolo del CropBox, non del MediaBox."""
    r = pymupdf.Rect(page.rect) * page.derotation_matrix
    r.normalize()
    return r


def clamp_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """Vincola il riquadro al CropBox (coordinate non ruotate), lato minimo 8 pt."""
    crop = page_area(page)
    r = pymupdf.Rect(rect)
    w = max(MIN_SIDE, min(r.width, crop.width))
    h = max(MIN_SIDE, min(r.height, crop.height))
    x0 = min(max(r.x0, crop.x0), crop.x1 - w)
    y0 = min(max(r.y0, crop.y0), crop.y1 - h)
    return pymupdf.Rect(x0, y0, x0 + w, y0 + h)


def _configure_annot(pdf, page, xref: int, pr: pymupdf.Rect, ap_xref: int, nm: str, subject: str,
                     contents: str, author: str, opacity: float) -> None:
    """Imposta le chiavi dell'annotazione /Stamp creata dal motore (senza
    rigenerarne l'aspetto: l'aspetto è il Form XObject della firma)."""
    s = pymupdf.get_pdf_str
    pdf.xref_set_key(xref, "Rect", f"[{_f(pr.x0)} {_f(pr.y0)} {_f(pr.x1)} {_f(pr.y1)}]")
    pdf.xref_set_key(xref, "AP", f"<</N {ap_xref} 0 R>>")
    pdf.xref_set_key(xref, "C", "null")
    pdf.xref_set_key(xref, "F", "4")
    pdf.xref_set_key(xref, "Name", "/FoliantSignature")
    pdf.xref_set_key(xref, "NM", s(nm))
    pdf.xref_set_key(xref, "M", s(pymupdf.get_pdf_now()))
    pdf.xref_set_key(xref, "Subj", s(subject))
    pdf.xref_set_key(xref, "Contents", s(contents))
    pdf.xref_set_key(xref, "T", s(author or ""))
    pdf.xref_set_key(xref, "CA", _f(opacity) if opacity < 0.999 else "null")


def _pdf_string(pdf, xref: int, key: str) -> str:
    kind, val = pdf.xref_get_key(xref, key)
    if kind != "string":
        return ""
    return val


def insert_signature(pdf: pymupdf.Document, page_index: int, rect, item: SignatureItem, *,
                     opacity: float = 1.0, color: str | None = None, author: str = "",
                     nm: str | None = None, ap_xref: int | None = None) -> int:
    """Crea un'annotazione /Stamp con aspetto della firma; `rect` in coordinate
    non ruotate (PyMuPDF). Restituisce lo xref dell'annotazione."""
    if not (pdf.permissions & pymupdf.PDF_PERM_ANNOTATE):
        raise GraphicSignatureError(_("I permessi del documento non consentono di aggiungere annotazioni."))
    page = pdf[page_index]
    rot = page.rotation % 360
    rect = clamp_rect(page, pymupdf.Rect(rect))
    vw, vh = visual_size(rect, rot)
    if ap_xref is None:
        ap_xref = make_appearance(pdf, item, vw, vh, rot, opacity, color)
    pr = _pdf_rect(page, rect)
    nm = nm or ANNOT_PREFIX + uuid.uuid4().hex[:12]
    desc = item.name or (_("Iniziali") if item.kind == "iniziali" else _("Firma"))
    contents = _("{what} ({how})").format(what=desc, how=_(item.origin))
    if item.origin == "digitata" and item.text:
        contents = _("Firma di {name}").format(name=item.text) if item.kind == "firma" else \
            _("Iniziali: {name}").format(name=item.text)
    annot = page.add_stamp_annot(rect, stamp=0)
    annot_xref = annot.xref
    _configure_annot(pdf, page, annot_xref, pr, ap_xref, nm, item.subject, contents, author, opacity)
    return annot_xref


@dataclass
class PlacedSignature:
    xref: int
    page: int
    rect: pymupdf.Rect     # coordinate non ruotate (PyMuPDF)
    nm: str
    subject: str
    ap_xref: int | None


def placed_signatures(pdf: pymupdf.Document, page_index: int) -> list[PlacedSignature]:
    """Firme grafiche inserite da Foliant sulla pagina."""
    page = pdf[page_index]
    out = []
    for x in [a.xref for a in page.annots(types=[pymupdf.PDF_ANNOT_STAMP])]:
        try:
            if pdf.xref_get_key(x, "Subtype")[1] != "/Stamp":
                continue
            if pdf.xref_get_key(x, "Name")[1] != "/FoliantSignature":
                continue
            rect_s = pdf.xref_get_key(x, "Rect")[1].strip("[]").split()
            pr = pymupdf.Rect(*[float(v) for v in rect_s])
            r = pr * ~page_to_pdf_matrix(page)
            r.normalize()
            nm_raw = _pdf_string(pdf, x, "NM")
            ap = pdf.xref_get_key(x, "AP/N")
            ap_xref = int(ap[1].split()[0]) if ap[0] == "xref" else None
            subj = _pdf_string(pdf, x, "Subj")
            out.append(PlacedSignature(x, page_index, r, nm_raw, subj, ap_xref))
        except Exception:
            continue
    return out


def find_signature(pdf: pymupdf.Document, page_index: int, xref: int) -> PlacedSignature | None:
    for s in placed_signatures(pdf, page_index):
        if s.xref == xref:
            return s
    return None


def set_signature_rect(pdf: pymupdf.Document, page_index: int, xref: int, rect) -> pymupdf.Rect:
    page = pdf[page_index]
    rect = clamp_rect(page, pymupdf.Rect(rect))
    pr = _pdf_rect(page, rect)
    pdf.xref_set_key(xref, "Rect", f"[{_f(pr.x0)} {_f(pr.y0)} {_f(pr.x1)} {_f(pr.y1)}]")
    pdf.xref_set_key(xref, "M", pymupdf.get_pdf_str(pymupdf.get_pdf_now()))
    return rect


def delete_signature(pdf: pymupdf.Document, page_index: int, xref: int) -> None:
    if find_signature(pdf, page_index, xref) is None:
        raise GraphicSignatureError(_("Firma non trovata."))
    page = pdf[page_index]
    for annot in page.annots():
        if annot.xref == xref:
            page.delete_annot(annot)
            return


def set_signature_opacity(pdf: pymupdf.Document, page_index: int, xref: int, opacity: float) -> None:
    opacity = max(0.05, min(1.0, opacity))
    pdf.xref_set_key(xref, "CA", _f(opacity))
    ap = pdf.xref_get_key(xref, "AP/N")
    if ap[0] == "xref":
        ap_xref = int(ap[1].split()[0])
        res = f"<</Type/ExtGState/CA {_f(opacity)}/ca {_f(opacity)}>>"
        pdf.xref_set_key(ap_xref, "Resources/ExtGState/G0", res)
        stream = pdf.xref_stream(ap_xref) or b""
        if not stream.startswith(b"/G0 gs"):
            pdf.update_stream(ap_xref, b"/G0 gs\n" + stream, compress=True)


def duplicate_signature(pdf: pymupdf.Document, page_index: int, xref: int, target_page: int | None = None,
                        offset: tuple[float, float] = (12, 12), rect=None, nm: str | None = None) -> int:
    """Copia di una firma (stesso aspetto) sulla stessa o su un'altra pagina."""
    src = find_signature(pdf, page_index, xref)
    if src is None:
        raise GraphicSignatureError(_("Firma non trovata."))
    target_page = page_index if target_page is None else target_page
    page = pdf[target_page]
    if rect is None:
        rect = src.rect + (offset[0], offset[1], offset[0], offset[1])
    rect = clamp_rect(page, pymupdf.Rect(rect))
    src_rot = pdf[page_index].rotation % 360
    ap_xref = src.ap_xref
    if page.rotation % 360 != src_rot or ap_xref is None:
        ap_xref = _rotated_copy_of_appearance(pdf, src.ap_xref, src_rot, page.rotation % 360)
    pr = _pdf_rect(page, rect)
    opacity_s = pdf.xref_get_key(xref, "CA")
    opacity = float(opacity_s[1]) if opacity_s[0] in ("real", "int") else 1.0
    new_xref = page.add_stamp_annot(rect, stamp=0).xref
    _configure_annot(pdf, page, new_xref, pr, ap_xref, nm or ANNOT_PREFIX + uuid.uuid4().hex[:12],
                     _pdf_string(pdf, xref, "Subj"), _pdf_string(pdf, xref, "Contents"),
                     _pdf_string(pdf, xref, "T"), opacity)
    return new_xref


def _rotated_copy_of_appearance(pdf: pymupdf.Document, ap_xref: int, old_rot: int, new_rot: int) -> int:
    obj = pdf.xref_object(ap_xref, compressed=True)
    stream = pdf.xref_stream(ap_xref) or b""
    bbox = [float(v) for v in pdf.xref_get_key(ap_xref, "BBox")[1].strip("[]").split()]
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    new = pdf.get_new_xref()
    pdf.update_object(new, obj)
    pdf.xref_set_key(new, "Matrix", _rotation_matrix(new_rot, w, h))
    pdf.update_stream(new, stream, compress=True)
    return new


def _add_xobject_resource(pdf: pymupdf.Document, page_xref: int, name: str, xobj_xref: int) -> None:
    """Aggiunge /XObject/<name> alle risorse della pagina, anche se indirette."""
    holder, prefix = page_xref, "Resources/"
    kind, val = pdf.xref_get_key(page_xref, "Resources")
    if kind == "xref":
        holder, prefix = int(val.split()[0]), ""
    elif kind in ("null", "none"):
        pdf.xref_set_key(page_xref, "Resources", "<<>>")
    kind, val = pdf.xref_get_key(holder, prefix + "XObject")
    if kind == "xref":
        pdf.xref_set_key(int(val.split()[0]), name, f"{xobj_xref} 0 R")
    else:
        if kind in ("null", "none"):
            pdf.xref_set_key(holder, prefix + "XObject", "<<>>")
        pdf.xref_set_key(holder, f"{prefix}XObject/{name}", f"{xobj_xref} 0 R")


def _bbox_size_at(w: float, h: float, angle: float) -> tuple[float, float]:
    c, sn = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    return w * c + h * sn, w * sn + h * c


def rotate_signature(pdf: pymupdf.Document, page_index: int, xref: int, degrees: float = 90) -> pymupdf.Rect:
    """Ruota la firma di `degrees` gradi in senso orario rispetto alla vista, attorno
    al centro: passi di 90° o rotazione libera. Il riquadro diventa l'ingombro
    della firma ruotata, con la stessa scala."""
    sig = find_signature(pdf, page_index, xref)
    if sig is None or sig.ap_xref is None:
        raise GraphicSignatureError(_("Firma non trovata."))
    mat_s = pdf.xref_get_key(sig.ap_xref, "Matrix")
    m = [float(v) for v in mat_s[1].strip("[]").split()] if mat_s[0] == "array" else [1, 0, 0, 1, 0, 0]
    current = math.degrees(math.atan2(m[1], m[0])) % 360
    new = (current - degrees) % 360
    if abs(new - round(new)) < 1e-6:
        new = round(new) % 360
    bbox = [float(v) for v in pdf.xref_get_key(sig.ap_xref, "BBox")[1].strip("[]").split()]
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cur_w, cur_h = _bbox_size_at(bw, bh, current)
    scale = sig.rect.width / cur_w if cur_w else 1.0
    # copia dell'aspetto: le copie ripetute condividono lo stesso Form XObject
    ap = _rotated_copy_of_appearance(pdf, sig.ap_xref, current, new)
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
    nw, nh = _bbox_size_at(bw, bh, new)
    nw, nh = nw * scale, nh * scale
    r = sig.rect
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    return set_signature_rect(pdf, page_index, xref, pymupdf.Rect(cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2))


def embed_signature(pdf: pymupdf.Document, page_index: int, xref: int) -> None:
    """Incorpora l'aspetto nel contenuto della pagina e rimuove l'annotazione."""
    sig = find_signature(pdf, page_index, xref)
    if sig is None or sig.ap_xref is None:
        raise GraphicSignatureError(_("Firma non trovata."))
    page = pdf[page_index]
    pr = _pdf_rect(page, sig.rect)
    bbox = [float(v) for v in pdf.xref_get_key(sig.ap_xref, "BBox")[1].strip("[]").split()]
    mat_s = pdf.xref_get_key(sig.ap_xref, "Matrix")
    m = [float(v) for v in mat_s[1].strip("[]").split()] if mat_s[0] == "array" else [1, 0, 0, 1, 0, 0]
    M = pymupdf.Matrix(*m)
    tb = pymupdf.Rect(*bbox) * M
    tb.normalize()
    sx = pr.width / tb.width if tb.width else 1
    sy = pr.height / tb.height if tb.height else 1
    A = pymupdf.Matrix(sx, 0, 0, sy, pr.x0 - tb.x0 * sx, pr.y0 - tb.y0 * sy)
    name = f"FSig{xref}"
    # risorse della pagina: aggiungi l'XObject
    _add_xobject_resource(pdf, page.xref, name, sig.ap_xref)
    # il cm finale applica la sola mappatura BBox→Rect (la /Matrix del form è applicata da Do)
    content = (f"q {_f(A.a)} {_f(A.b)} {_f(A.c)} {_f(A.d)} {_f(A.e)} {_f(A.f)} cm /{name} Do Q").encode()
    kind, val = pdf.xref_get_key(page.xref, "Contents")
    if kind == "array":
        original = val.strip()[1:-1].strip()
    elif kind == "xref":
        original = val
    else:
        original = ""

    def new_stream(data: bytes) -> int:
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, data, compress=True)
        return x

    # Il contenuto originale è racchiuso in q…Q così il suo stato grafico non
    # influisce sulla firma aggiunta in coda.
    head = new_stream(b"q\n")
    tail = new_stream(b"\nQ\n" + content + b"\n")
    pdf.xref_set_key(page.xref, "Contents", f"[{head} 0 R {original} {tail} 0 R]")
    delete_signature(pdf, page_index, xref)


# ============================================================ guide magnetiche
SNAP_MARGIN = 36.0   # margine tipico (mezzo pollice) usato come guida verticale/orizzontale


def page_guides(page: pymupdf.Page) -> tuple[list[float], list[float], list[tuple[float, float, float]]]:
    """Guide della pagina in coordinate non ruotate: (x, y, linee orizzontali (y, x0, x1)).

    x/y: bordi del CropBox a distanza SNAP_MARGIN; linee: tratti orizzontali
    lunghi almeno 40 pt (tipiche linee di firma), anche fatte di trattini «____».
    """
    crop = page_area(page)
    xs = [crop.x0 + SNAP_MARGIN, crop.x1 - SNAP_MARGIN]
    ys = [crop.y0 + SNAP_MARGIN, crop.y1 - SNAP_MARGIN]
    lines: list[tuple[float, float, float]] = []
    try:
        for d in page.get_drawings():
            for it in d.get("items", []):
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.y - p2.y) < 1 and abs(p2.x - p1.x) >= 40:
                        lines.append((p1.y, min(p1.x, p2.x), max(p1.x, p2.x)))
                elif it[0] == "re":
                    r = it[1]
                    if r.height < 2 and r.width >= 40:
                        lines.append((r.y1, r.x0, r.x1))
        for b in page.get_text("words"):
            if set(b[4]) <= {"_"} and b[2] - b[0] >= 40:
                lines.append((b[3] - 1, b[0], b[2]))
    except Exception:
        pass
    return xs, ys, lines


def snap_rect(rect: pymupdf.Rect, xs: list[float], ys: list[float], lines, tol: float,
              ) -> tuple[pymupdf.Rect, list[tuple[str, float]]]:
    """Avvicina il riquadro alle guide entro `tol` punti, senza cambiarne le
    dimensioni. Restituisce (riquadro, guide attive [("x"|"y", valore)])."""
    r = pymupdf.Rect(rect)
    active: list[tuple[str, float]] = []
    best_dx, gx = None, None
    for x in xs:
        for edge in (r.x0, r.x1):
            d = x - edge
            if abs(d) <= tol and (best_dx is None or abs(d) < abs(best_dx)):
                best_dx, gx = d, x
    best_dy, gy = None, None
    for y in ys:
        for edge in (r.y0, r.y1):
            d = y - edge
            if abs(d) <= tol and (best_dy is None or abs(d) < abs(best_dy)):
                best_dy, gy = d, y
    # la base della firma si appoggia alle linee orizzontali che le stanno sotto
    for y, x0, x1 in lines:
        if x1 < r.x0 or x0 > r.x1:
            continue
        target = y - r.height * 0.15     # leggera sovrapposizione, come una firma vera
        d = target - r.y0
        if abs(r.y1 - y) <= tol * 2 and (best_dy is None or abs(d) < abs(best_dy)):
            best_dy, gy = d, y
    if best_dx is not None:
        r = r + (best_dx, 0, best_dx, 0)
        active.append(("x", gx))
    if best_dy is not None:
        r = r + (0, best_dy, 0, best_dy)
        active.append(("y", gy))
    return r, active


# ======================================================== ripetizione su più pagine
def group_of(nm: str) -> str:
    """Identificativo di gruppo: «foliant-sig-xxxx-3» → «foliant-sig-xxxx»."""
    base, sep, tail = (nm or "").rpartition("-")
    if sep and tail.isdigit() and base.startswith(ANNOT_PREFIX):
        return base
    return nm or ""


def group_size(pdf: pymupdf.Document, group: str) -> int:
    return sum(1 for p in range(pdf.page_count) for s in placed_signatures(pdf, p) if group_of(s.nm) == group)


def ungroup(pdf: pymupdf.Document, group: str) -> int:
    """Scioglie il gruppo: ogni copia riceve un identificativo indipendente."""
    n = 0
    for p in range(pdf.page_count):
        for s in placed_signatures(pdf, p):
            if group_of(s.nm) == group and s.nm != group:
                pdf.xref_set_key(s.xref, "NM", pymupdf.get_pdf_str(ANNOT_PREFIX + uuid.uuid4().hex[:12]))
                n += 1
    return n


def parse_page_range(text: str, count: int, current: int | None = None) -> list[int]:
    """Interpreta un intervallo («1-3,7,10-», «pari», «dispari», «tutte»,
    «tutte tranne la corrente») e restituisce indici 0-based ordinati.
    Solleva ValueError con un messaggio in italiano se non valido."""
    t = (text or "").strip().lower()
    if not t:
        raise ValueError(_("Indica le pagine."))
    if t in ("tutte", "all", "*"):
        return list(range(count))
    if t in ("pari", "even"):
        return [i for i in range(count) if (i + 1) % 2 == 0]
    if t in ("dispari", "odd"):
        return [i for i in range(count) if (i + 1) % 2 == 1]
    if t in ("tutte tranne la corrente", "altre"):
        return [i for i in range(count) if i != current]
    pages: set[int] = set()
    for part in t.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _sep, b = part.partition("-")
            a = a.strip()
            b = b.strip()
            if not a.isdigit() or (b and not b.isdigit()):
                raise ValueError(_("Intervallo non valido: «{p}».").format(p=part))
            start = int(a)
            end = int(b) if b else count
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ValueError(_("Intervallo non valido: «{p}».").format(p=part))
        if start < 1 or end < start:
            raise ValueError(_("Intervallo non valido: «{p}».").format(p=part))
        if end > count:
            raise ValueError(_("La pagina {n} non esiste: il documento ha {c} pagine.").format(n=end, c=count))
        pages.update(range(start - 1, end))
    if not pages:
        raise ValueError(_("Indica le pagine."))
    return sorted(pages)


def nearest_anchor(page_rect: pymupdf.Rect, rect: pymupdf.Rect) -> str:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    v = "t" if cy < (page_rect.y0 + page_rect.y1) / 2 else "b"
    h = "l" if cx < (page_rect.x0 + page_rect.x1) / 2 else "r"
    return v + h


def anchored_rect(src_page: pymupdf.Rect, src: pymupdf.Rect, dst_page: pymupdf.Rect,
                  anchor: str) -> tuple[pymupdf.Rect, bool]:
    """Riquadro (visivo) sulla pagina di destinazione mantenendo le distanze
    dall'angolo di ancoraggio; ridotto se non entra. Restituisce (rect, ridotto)."""
    w, h = src.width, src.height
    shrunk = False
    if anchor == "abs":
        r = pymupdf.Rect(src)
    else:
        mx = src.x0 - src_page.x0 if anchor[1] == "l" else src_page.x1 - src.x1
        my = src.y0 - src_page.y0 if anchor[0] == "t" else src_page.y1 - src.y1
        x0 = dst_page.x0 + mx if anchor[1] == "l" else dst_page.x1 - mx - w
        y0 = dst_page.y0 + my if anchor[0] == "t" else dst_page.y1 - my - h
        r = pymupdf.Rect(x0, y0, x0 + w, y0 + h)
    if not dst_page.contains(r):
        avail_w = dst_page.width
        avail_h = dst_page.height
        f = min(1.0, avail_w / w, avail_h / h)
        if f < 1.0:
            shrunk = True
            nw, nh = w * f, h * f
            r = pymupdf.Rect(r.x0, r.y0, r.x0 + nw, r.y0 + nh)
        dx = max(0, dst_page.x0 - r.x0) - max(0, r.x1 - dst_page.x1)
        dy = max(0, dst_page.y0 - r.y0) - max(0, r.y1 - dst_page.y1)
        if dx or dy:
            r = r + (dx, dy, dx, dy)
    return r, shrunk


def _visual_page_rect(page: pymupdf.Page) -> pymupdf.Rect:
    return pymupdf.Rect(page.rect)


def repeat_preview(pdf: pymupdf.Document, page_index: int, xref: int, pages: list[int], anchor: str = "auto",
                   width: int = 96, color: tuple[int, int, int] = (0, 0, 0)) -> list[tuple[int, pymupdf.Pixmap, bool]]:
    """Miniature delle pagine con il riquadro dove verrà ripetuta la firma."""
    src = find_signature(pdf, page_index, xref)
    if src is None:
        return []
    src_page = pdf[page_index]
    src_vis_page = _visual_page_rect(src_page)
    src_vis = pymupdf.Rect(src.rect) * src_page.rotation_matrix
    src_vis.normalize()
    if anchor == "auto":
        anchor = nearest_anchor(src_vis_page, src_vis)
    out = []
    for p in pages:
        if p < 0 or p >= pdf.page_count:
            continue
        page = pdf[p]
        vis_page = _visual_page_rect(page)
        vis, shrunk = anchored_rect(src_vis_page, src_vis, vis_page, anchor)
        zoom = width / max(vis_page.width, 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), annots=True)
        r = pymupdf.IRect(vis * pymupdf.Matrix(zoom, zoom))
        for x in range(max(0, r.x0), min(pix.width, r.x1)):
            for y in (r.y0, r.y1 - 1):
                if 0 <= y < pix.height:
                    pix.set_pixel(x, y, color)
        for y in range(max(0, r.y0), min(pix.height, r.y1)):
            for x in (r.x0, r.x1 - 1):
                if 0 <= x < pix.width:
                    pix.set_pixel(x, y, color)
        out.append((p, pix, shrunk))
    return out


def repeat_signature(pdf: pymupdf.Document, page_index: int, xref: int, pages: list[int],
                     anchor: str = "auto") -> tuple[list[tuple[int, int]], list[int]]:
    """Copia la firma sulle pagine indicate (escluse quella di origine).
    Restituisce ([(pagina, xref)], pagine in cui è stata ridotta)."""
    src = find_signature(pdf, page_index, xref)
    if src is None:
        raise GraphicSignatureError(_("Firma non trovata."))
    src_page = pdf[page_index]
    src_vis_page = _visual_page_rect(src_page)
    src_vis = pymupdf.Rect(src.rect) * src_page.rotation_matrix
    src_vis.normalize()
    if anchor == "auto":
        anchor = nearest_anchor(src_vis_page, src_vis)
    group = (src.nm or ANNOT_PREFIX + uuid.uuid4().hex[:12])
    created, shrunk = [], []
    n = 0
    for p in pages:
        if p == page_index or p < 0 or p >= pdf.page_count:
            continue
        page = pdf[p]
        dst_vis_page = _visual_page_rect(page)
        vis, was_shrunk = anchored_rect(src_vis_page, src_vis, dst_vis_page, anchor)
        rect = unrotated_rect(page, vis)
        n += 1
        new = duplicate_signature(pdf, page_index, xref, p, rect=rect, nm=f"{group}-{n}")
        created.append((p, new))
        if was_shrunk:
            shrunk.append(p)
    return created, shrunk


TRANSLATIONS_EN = {
    "La firma è vuota.": "The signature is empty.",
    "Il file della firma è danneggiato.": "The signature file is damaged.",
    "corsivo": "italic",
    "grassetto": "bold",
    "Riserva ({f})": "Fallback ({f})",
    "Il testo è vuoto.": "The text is empty.",
    "Lo stile scelto non contiene i caratteri del testo.": "The chosen style lacks the characters of the text.",
    "sconosciuto": "unknown",
    "L'immagine è troppo grande ({w}×{h} pixel, {mp:.1f} megapixel): il massimo è 50 megapixel.":
        "The image is too large ({w}×{h} pixels, {mp:.1f} megapixels): the maximum is 50 megapixels.",
    "Formato immagine non supportato (rilevato: {fmt}). Usa PNG, JPEG, BMP, TIFF, GIF o WebP.":
        "Unsupported image format (detected: {fmt}). Use PNG, JPEG, BMP, TIFF, GIF or WebP.",
    "Nessun tratto rilevato: prova a cambiare la soglia dello sfondo.":
        "No strokes detected: try changing the background threshold.",
    "L'elemento di firma non ha contenuto.": "The signature item has no content.",
    "I permessi del documento non consentono di aggiungere annotazioni.":
        "The document permissions do not allow adding annotations.",
    "Iniziali": "Initials",
    "Firma": "Signature",
    "{what} ({how})": "{what} ({how})",
    "disegnata": "drawn",
    "digitata": "typed",
    "immagine": "image",
    "Firma di {name}": "Signature of {name}",
    "Iniziali: {name}": "Initials: {name}",
    "Firma non trovata.": "Signature not found.",
    "Indica le pagine.": "Specify the pages.",
    "Intervallo non valido: «{p}».": "Invalid range: “{p}”.",
    "La pagina {n} non esiste: il documento ha {c} pagine.": "Page {n} does not exist: the document has {c} pages.",
}
