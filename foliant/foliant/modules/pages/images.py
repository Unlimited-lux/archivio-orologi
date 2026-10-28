"""Pagine da immagini e testo, pagine verso immagini (PAG-08, PAG-09, PAG-13).

Motore senza interfaccia: nessun widget Qt. Le funzioni che ricevono `pdf`
lo modificano sul posto (chiamarle dentro `document.change()`); le altre
restituiscono un nuovo `pymupdf.Document` o i percorsi dei file creati.

Scelte di incorporamento delle immagini (PAG-08):

* JPEG senza ricompressione: lo stream originale è incorporato byte per byte
  (DCTDecode); l'orientamento EXIF è applicato ruotando l'immagine nella pagina
  (`rotate=`), senza ricodifica. Le orientazioni speculari (2, 4, 5, 7) non sono
  ottenibili con una sola rotazione: l'immagine viene decodificata, raddrizzata e
  incorporata senza perdita (PNG/Flate).
* JPEG 2000: stream originale (JPXDecode).
* PNG senza orientamento: file originale, il motore lo converte in Flate con
  il canale alfa come /SMask.
* Altri formati e fotogrammi (TIFF, GIF, BMP, WebP…): decodificati con Pillow e
  incorporati senza perdita, conservando trasparenza e profilo ICC.
* `recompress=True`: immagini opache ricodificate in JPEG (qualità 85).
"""

from __future__ import annotations

import html as _html
import io
import os
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .engine import (
    IMAGE_SUFFIXES,
    MAX_SIDE,
    PAPER_SIZES,
    PageOpError,
    render_name,
    unique_path,
)

try:  # Pillow è una dipendenza del progetto, ma il motore non deve rompersi senza
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 400_000_000
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None

try:  # HEIC/HEIF solo se il codec è installato
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:
    HEIF_AVAILABLE = False


TRANSLATIONS = {
    "«{n}»: file non trovato o illeggibile.": "“{n}”: file not found or unreadable.",
    "«{n}»: formato non supportato o file danneggiato.": "“{n}”: unsupported format or damaged file.",
    "«{n}»: GIF animata, inserito solo il primo fotogramma.": "“{n}”: animated GIF, only the first frame was inserted.",
    "«{n}»: immagine animata, inserito solo il primo fotogramma.": "“{n}”: animated image, only the first frame was inserted.",
    "«{n}»: fotogramma {f} danneggiato, saltato.": "“{n}”: frame {f} is damaged and was skipped.",
    "«{n}»: immagine oltre 200 pollici, ridotta per stare nel formato massimo.":
        "“{n}”: image larger than 200 inches, reduced to fit the maximum page size.",
    "Nessuna immagine selezionata.": "No image selected.",
    "Nessuna immagine è stata convertita.": "No image could be converted.",
    "Nessuna immagine è stata convertita:\n{e}": "No image could be converted:\n{e}",
    "Il testo è vuoto.": "The text is empty.",
    "Formato pagina non valido.": "Invalid page size.",
    "Il margine è troppo grande per il formato scelto.": "The margin is too large for the chosen page size.",
    "Formato immagine non supportato: {f}.": "Unsupported image format: {f}.",
    "La risoluzione deve essere tra 10 e 2400 dpi.": "Resolution must be between 10 and 2400 dpi.",
    "Nessuna pagina da esportare.": "No pages to export.",
    "Impossibile creare la cartella «{d}».": "Cannot create the folder “{d}”.",
    "Impossibile scrivere «{n}»: {e}.": "Cannot write “{n}”: {e}.",
    "Pagina {p}: rendering non riuscito ({e}).": "Page {p}: rendering failed ({e}).",
    "Pagina {p} di {t}": "Page {p} of {t}",
    "A {d} dpi le immagini supererebbero il limite di {m} megapixel. Risoluzione proposta: {s} dpi.":
        "At {d} dpi the images would exceed the {m} megapixel limit. Suggested resolution: {s} dpi.",
    "Alcuni caratteri non sono disponibili in nessun font e sono stati sostituiti.":
        "Some characters are not available in any font and were replaced.",
    "Gli appunti non contengono dati utilizzabili.": "The clipboard contains no usable data.",
    "I dati PDF negli appunti non sono validi.": "The PDF data in the clipboard is not valid.",
}

FITS = ("contain", "fill", "center")
EXPORT_FORMATS = ("png", "jpeg", "tiff", "bmp", "webp")
EXPORT_EXT = {"png": ".png", "jpeg": ".jpg", "tiff": ".tif", "bmp": ".bmp", "webp": ".webp"}
DPI_MIN_PLAUSIBLE = 10.0
DPI_MAX_PLAUSIBLE = 5000.0
DEFAULT_MAX_PIXELS = 250_000_000     # per immagine esportata (~1 GB in RGBA)

_JPEG_FORMATS = {"JPEG", "MPO"}
_JPX_FORMATS = {"JPEG2000"}
# Orientamento EXIF → rotazione da applicare (gradi, senso orario) se non speculare.
_EXIF_ROTATION = {1: 0, 3: 180, 6: 90, 8: 270}


# =========================================================================
# PAG-08 — immagini → pagine
# =========================================================================

class _Frame:
    """Un'immagine pronta da collocare: stream da incorporare e geometria naturale."""

    __slots__ = ("dpi_x", "dpi_y", "px_h", "px_w", "rotate", "stream")

    def __init__(self, stream: bytes, px_w: int, px_h: int, dpi_x: float, dpi_y: float, rotate: int = 0):
        self.stream = stream
        self.px_w, self.px_h = px_w, px_h          # dimensioni dello stream (prima della rotazione)
        self.dpi_x, self.dpi_y = dpi_x, dpi_y
        self.rotate = rotate                       # gradi orari da applicare in pagina

    @property
    def engine_rotate(self) -> int:
        """Il parametro `rotate` del motore ruota in senso antiorario."""
        return (360 - self.rotate) % 360

    def natural_size(self) -> tuple[float, float]:
        w = self.px_w * 72.0 / self.dpi_x
        h = self.px_h * 72.0 / self.dpi_y
        return (h, w) if self.rotate in (90, 270) else (w, h)


def _plausible_dpi(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not (DPI_MIN_PLAUSIBLE <= v <= DPI_MAX_PLAUSIBLE):
        return default
    return v


def _image_dpi(im, default: float) -> tuple[float, float]:
    dpi = im.info.get("dpi")
    if isinstance(dpi, (tuple, list)) and len(dpi) >= 2:
        dx = _plausible_dpi(dpi[0], default)
        dy = _plausible_dpi(dpi[1], default)
        if dx == default or dy == default:   # un solo valore plausibile: usarlo per entrambi
            good = dx if dx != default else dy
            return good, good
        return dx, dy
    if isinstance(dpi, (int, float)):
        v = _plausible_dpi(dpi, default)
        return v, v
    return default, default


def _exif_orientation(im) -> int:
    try:
        value = int(im.getexif().get(0x0112, 1))
    except Exception:
        return 1
    return value if 1 <= value <= 8 else 1


def _lossless_bytes(im) -> bytes:
    """Fotogramma Pillow → PNG (senza perdita), con alfa e profilo ICC se presenti."""
    icc = im.info.get("icc_profile")
    mode = im.mode
    if mode == "P" or mode == "PA":
        im = im.convert("RGBA" if ("transparency" in im.info or mode == "PA") else "RGB")
    elif mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        pass                                       # PNG a 16 bit in scala di grigi
    elif mode in ("I", "F"):
        im = im.convert("L")
    elif mode == "CMYK":
        im = im.convert("RGB")
        icc = None
    elif mode == "LA":
        pass
    elif mode not in ("1", "L", "RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in mode else "RGB")
    buf = io.BytesIO()
    kwargs = {"compress_level": 6}
    if icc and im.mode in ("RGB", "RGBA", "L", "LA"):
        kwargs["icc_profile"] = icc
    im.save(buf, "PNG", **kwargs)
    return buf.getvalue()


def _jpeg_bytes(im, quality: int = 85) -> bytes:
    if im.mode not in ("RGB", "L", "CMYK"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    kwargs = {"quality": quality, "optimize": True}
    icc = im.info.get("icc_profile")
    if icc:
        kwargs["icc_profile"] = icc
    im.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def _has_alpha(im) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def _encode_frame(im, recompress: bool) -> bytes:
    if recompress and not _has_alpha(im) and im.mode != "1":
        return _jpeg_bytes(im)
    return _lossless_bytes(im)


def _frames_from_file(path: str, dpi_default: float, recompress: bool, errors: list[str]) -> list[_Frame]:
    """Legge un file immagine e restituisce i fotogrammi da inserire (vuoto se illeggibile)."""
    name = Path(path).name
    try:
        data = Path(path).read_bytes()
    except OSError:
        errors.append(_("«{n}»: file non trovato o illeggibile.").format(n=name))
        return []
    if Image is None:
        return _frames_with_engine(data, name, dpi_default, errors)
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        # Ultima possibilità: formati letti dal motore PDF ma non da Pillow.
        return _frames_with_engine(data, name, dpi_default, errors)

    fmt = (im.format or "").upper()
    n_frames = int(getattr(im, "n_frames", 1) or 1)
    frames: list[_Frame] = []

    if fmt in _JPEG_FORMATS:
        dx, dy = _image_dpi(im, dpi_default)
        orient = _exif_orientation(im)
        if n_frames > 1:      # MPO: solo l'immagine principale
            errors.append(_("«{n}»: immagine animata, inserito solo il primo fotogramma.").format(n=name))
        if fmt == "MPO" or n_frames > 1:
            im.seek(0)
            stream = _jpeg_from_first_mpo(data, im)
        else:
            stream = data
        if recompress:
            img = ImageOps.exif_transpose(im) if orient != 1 else im
            if orient in (5, 6, 7, 8):
                dx, dy = dy, dx
            return [_Frame(_jpeg_bytes(img), img.width, img.height, dx, dy)]
        if orient in _EXIF_ROTATION:
            return [_Frame(stream, im.width, im.height, dx, dy, _EXIF_ROTATION[orient])]
        img = ImageOps.exif_transpose(im)
        if orient in (5, 6, 7, 8):
            dx, dy = dy, dx
        return [_Frame(_lossless_bytes(img), img.width, img.height, dx, dy)]

    if fmt in _JPX_FORMATS and not recompress:
        dx, dy = _image_dpi(im, dpi_default)
        return [_Frame(data, im.width, im.height, dx, dy)]

    if fmt == "PNG" and n_frames == 1 and not recompress and _exif_orientation(im) == 1:
        dx, dy = _image_dpi(im, dpi_default)
        return [_Frame(data, im.width, im.height, dx, dy)]

    # TIFF (tutti i fotogrammi), GIF/WebP/APNG animati (solo il primo), altri formati.
    multi = fmt == "TIFF"
    if n_frames > 1 and not multi:
        msg = (_("«{n}»: GIF animata, inserito solo il primo fotogramma.") if fmt == "GIF"
               else _("«{n}»: immagine animata, inserito solo il primo fotogramma."))
        errors.append(msg.format(n=name))
    count = n_frames if multi else 1
    for k in range(count):
        try:
            im.seek(k)
            frame = im.copy()
            frame.info = dict(im.info)
            dx, dy = _image_dpi(im, dpi_default)
            orient = _exif_orientation(im)
            if orient != 1:
                frame = ImageOps.exif_transpose(frame)
                if orient in (5, 6, 7, 8):
                    dx, dy = dy, dx
            frames.append(_Frame(_encode_frame(frame, recompress), frame.width, frame.height, dx, dy))
        except Exception:
            errors.append(_("«{n}»: fotogramma {f} danneggiato, saltato.").format(n=name, f=k + 1))
    if not frames:
        errors.append(_("«{n}»: formato non supportato o file danneggiato.").format(n=name))
    return frames


def _jpeg_from_first_mpo(data: bytes, im) -> bytes:
    """Primo JPEG di un file MPO (sequenza di JPEG concatenati)."""
    try:
        offsets = im._MpoImageFile__mpoffsets  # type: ignore[attr-defined]
        if len(offsets) > 1:
            return data[offsets[0]:offsets[1]]
    except Exception:
        pass
    return data


def _frames_with_engine(data: bytes, name: str, dpi_default: float, errors: list[str]) -> list[_Frame]:
    try:
        with ENGINE_LOCK:
            pix = pymupdf.Pixmap(data)
            dx = _plausible_dpi(pix.xres, dpi_default) if pix.xres not in (0, 72, 96) else dpi_default
            dy = _plausible_dpi(pix.yres, dpi_default) if pix.yres not in (0, 72, 96) else dpi_default
            if pix.colorspace and pix.colorspace.n == 4:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            png = pix.tobytes("png")
            return [_Frame(png, pix.width, pix.height, dx, dy)]
    except Exception:
        errors.append(_("«{n}»: formato non supportato o file danneggiato.").format(n=name))
        return []


def _orient(size: tuple[float, float], landscape: bool) -> tuple[float, float]:
    w, h = size
    return (max(w, h), min(w, h)) if landscape else (min(w, h), max(w, h))


def _place_frame(out: pymupdf.Document, frame: _Frame, page_size, fit: str, margin: float,
                 landscape, name: str, errors: list[str]) -> None:
    nat_w, nat_h = frame.natural_size()
    if page_size is None:
        # «Come l'immagine»: pagina della dimensione naturale (più i margini).
        img_w, img_h = nat_w, nat_h
        limit = MAX_SIDE - 2 * margin
        if img_w > limit or img_h > limit:
            s = min(limit / img_w, limit / img_h)
            img_w, img_h = img_w * s, img_h * s
            errors.append(_("«{n}»: immagine oltre 200 pollici, ridotta per stare nel formato massimo.").format(n=name))
        page_w, page_h = img_w + 2 * margin, img_h + 2 * margin
        page = out.new_page(width=page_w, height=page_h)
        rect = pymupdf.Rect(margin, margin, margin + img_w, margin + img_h)
        page.insert_image(rect, stream=frame.stream, rotate=frame.engine_rotate, keep_proportion=False)
        return

    w, h = page_size
    if landscape == "auto" or landscape is None:
        if abs(nat_w - nat_h) > 1e-6:
            w, h = _orient((w, h), nat_w > nat_h)
    elif landscape is True:
        w, h = _orient((w, h), True)
    elif landscape is False:
        w, h = _orient((w, h), False)
    page = out.new_page(width=w, height=h)
    area = pymupdf.Rect(margin, margin, w - margin, h - margin)
    if area.width < 1 or area.height < 1:
        raise PageOpError(_("Il margine è troppo grande per il formato scelto."))
    if fit == "fill":
        s = max(area.width / nat_w, area.height / nat_h)
        dw, dh = nat_w * s, nat_h * s
    elif fit == "center":
        dw, dh = nat_w, nat_h
    else:  # contain
        s = min(area.width / nat_w, area.height / nat_h)
        dw, dh = nat_w * s, nat_h * s
    cx, cy = (area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2
    rect = pymupdf.Rect(cx - dw / 2, cy - dh / 2, cx + dw / 2, cy + dh / 2)
    tol = 0.01
    if (rect.x0 >= area.x0 - tol and rect.y0 >= area.y0 - tol
            and rect.x1 <= area.x1 + tol and rect.y1 <= area.y1 + tol):
        page.insert_image(rect, stream=frame.stream, rotate=frame.engine_rotate, keep_proportion=False)
        return
    # L'immagine eccede l'area utile: la si colloca in una pagina di appoggio e se
    # ne mostra solo la parte visibile (lo stream dell'immagine resta invariato).
    tmp = pymupdf.open()
    try:
        tp = tmp.new_page(width=dw, height=dh)
        tp.insert_image(tp.rect, stream=frame.stream, rotate=frame.engine_rotate, keep_proportion=False)
        visible = rect & area
        clip = pymupdf.Rect(visible.x0 - rect.x0, visible.y0 - rect.y0, visible.x1 - rect.x0, visible.y1 - rect.y0)
        page.show_pdf_page(visible, tmp, 0, clip=clip, keep_proportion=False)
    finally:
        tmp.close()


def _check_options(page_size, fit: str, margin: float) -> None:
    if fit not in FITS:
        raise PageOpError(_("Formato pagina non valido."))
    if margin < 0:
        raise PageOpError(_("Il margine è troppo grande per il formato scelto."))
    if page_size is not None:
        try:
            w, h = float(page_size[0]), float(page_size[1])
        except Exception as e:
            raise PageOpError(_("Formato pagina non valido.")) from e
        if w < 3 or h < 3 or w > MAX_SIDE or h > MAX_SIDE:
            raise PageOpError(_("Formato pagina non valido."))
        if 2 * margin >= min(w, h) - 1:
            raise PageOpError(_("Il margine è troppo grande per il formato scelto."))


def images_to_pdf(paths: Sequence[str | os.PathLike], *, page_size: tuple[float, float] | None = None,
                  fit: str = "contain", margin: float = 0.0, landscape="auto", dpi_default: float = 150.0,
                  recompress: bool = False, errors: list[str] | None = None,
                  progress: Callable[[float, str], None] | None = None) -> pymupdf.Document:
    """Crea un nuovo PDF con una pagina per immagine (una per fotogramma nei TIFF).

    `page_size=None` → pagina della dimensione dell'immagine alla risoluzione
    dichiarata (o `dpi_default` se assente/non plausibile); altrimenti
    (larghezza, altezza) in punti con adattamento `fit` ("contain", "fill",
    "center") entro i margini `margin` (punti). `landscape="auto"` orienta la
    pagina secondo le proporzioni dell'immagine.

    I file illeggibili sono saltati e descritti in `errors` (insieme agli
    avvisi, ad es. GIF animate); se nessuna immagine è convertibile solleva
    `PageOpError`."""
    if errors is None:
        errors = []
    paths = [str(p) for p in paths]
    if not paths:
        raise PageOpError(_("Nessuna immagine selezionata."))
    _check_options(page_size, fit, margin)
    if page_size is not None:
        page_size = (float(page_size[0]), float(page_size[1]))
    dpi_default = _plausible_dpi(dpi_default, 150.0)
    first_error = len(errors)
    with ENGINE_LOCK:
        out = pymupdf.open()
        try:
            for j, path in enumerate(paths):
                if progress:
                    progress(j / len(paths), Path(path).name)
                frames = _frames_from_file(path, dpi_default, recompress, errors)
                for fr in frames:
                    try:
                        _place_frame(out, fr, page_size, fit, margin, landscape, Path(path).name, errors)
                    except PageOpError:
                        raise
                    except Exception:
                        errors.append(_("«{n}»: formato non supportato o file danneggiato.").format(
                            n=Path(path).name))
                        # una pagina vuota rimasta a metà va tolta
                        if out.page_count and not out[-1].get_images():
                            out.delete_page(-1)
            if out.page_count == 0:
                msgs = errors[first_error:]
                raise PageOpError(_("Nessuna immagine è stata convertita:\n{e}").format(e="\n".join(msgs))
                                  if msgs else _("Nessuna immagine è stata convertita."))
        except BaseException:
            out.close()
            raise
    return out


def resolve_page_size(pdf: pymupdf.Document | None, page_size, at: int):
    """`page_size` può essere None, (w, h), un nome di PAPER_SIZES o "adjacent"
    (formato della pagina adiacente al punto di inserimento)."""
    if page_size is None:
        return None
    if isinstance(page_size, str):
        if page_size == "adjacent":
            if pdf is None or pdf.page_count == 0:
                return PAPER_SIZES["A4"]
            ref = max(0, min(pdf.page_count - 1, at - 1 if at > 0 else 0))
            r = pdf[ref].rect                       # dimensione visualizzata
            return (r.width, r.height)
        if page_size in PAPER_SIZES:
            return PAPER_SIZES[page_size]
        raise PageOpError(_("Formato pagina non valido."))
    return page_size


def insert_images(pdf: pymupdf.Document, paths: Sequence[str | os.PathLike], at: int,
                  **options) -> tuple[list[int], list[str]]:
    """Inserisce le immagini come nuove pagine prima della posizione `at`.

    Opzioni come `images_to_pdf` (inoltre `page_size` può valere "adjacent" o
    un nome di formato). Restituisce (indici delle nuove pagine, errori/avvisi)."""
    errors: list[str] = list(options.pop("errors", None) or [])
    at = max(0, min(pdf.page_count, int(at)))
    options["page_size"] = resolve_page_size(pdf, options.get("page_size"), at)
    src = images_to_pdf(paths, errors=errors, **options)
    try:
        n = src.page_count
        pdf.insert_pdf(src, start_at=at)
    finally:
        src.close()
    return list(range(at, at + n)), errors


# =========================================================================
# PAG-09 — testo → pagine
# =========================================================================

_FALLBACK_FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def _fonts() -> list[pymupdf.Font]:
    fonts = [pymupdf.Font("helv")]
    for f in _FALLBACK_FONT_FILES:
        if os.path.exists(f):
            try:
                fonts.append(pymupdf.Font(fontfile=f))
                break
            except Exception:
                pass
    try:
        fonts.append(pymupdf.Font("cjk"))
    except Exception:
        pass
    return fonts


def _runs(line: str, fonts: list[pymupdf.Font], missing: list[str]) -> list[tuple[str, pymupdf.Font]]:
    """Divide la riga in tratti con il primo font che copre ogni carattere."""
    runs: list[tuple[str, pymupdf.Font]] = []
    for ch in line:
        font = None
        for f in fonts:
            if ch == " " or f.has_glyph(ord(ch)):
                font = f
                break
        if font is None:
            missing.append(ch)
            ch, font = "?", fonts[0]
        if runs and runs[-1][1] is font:
            runs[-1] = (runs[-1][0] + ch, font)
        else:
            runs.append((ch, font))
    return runs


def _width(text: str, fonts: list[pymupdf.Font], size: float, missing: list[str]) -> float:
    return sum(f.text_length(t, fontsize=size) for t, f in _runs(text, fonts, missing))


def _wrap(line: str, max_w: float, fonts, size: float, missing: list[str]) -> list[str]:
    """A capo automatico per parole; le parole troppo lunghe sono spezzate."""
    if not line:
        return [""]
    tokens = re.findall(r"\S+\s*|\s+", line)
    out: list[str] = []
    cur = ""
    for tok in tokens:
        cand = cur + tok
        if _width(cand.rstrip(), fonts, size, missing) <= max_w or not cur.strip():
            if _width(cand.rstrip(), fonts, size, missing) <= max_w:
                cur = cand
                continue
            # parola più lunga della riga: spezzare a caratteri
            for ch in tok:
                if _width((cur + ch).rstrip(), fonts, size, missing) > max_w and cur.strip():
                    out.append(cur.rstrip())
                    cur = ch.lstrip()
                else:
                    cur += ch
            continue
        out.append(cur.rstrip())
        cur = tok.lstrip() if tok.strip() else ""
    out.append(cur.rstrip())
    return out


def text_to_pdf(text: str, *, page_size: tuple[float, float] = PAPER_SIZES["A4"], margin: float = 56,
                font_size: float = 11, warnings: list[str] | None = None) -> pymupdf.Document:
    """Impagina testo semplice UTF-8 su pagine del formato dato (a capo
    automatico, interruzioni di pagina, font incorporati in sottoinsieme: il
    testo resta ricercabile). I caratteri non coperti da nessun font
    disponibile diventano «?» con un avviso in `warnings`."""
    if text is None or not text.strip():
        raise PageOpError(_("Il testo è vuoto."))
    _check_options(page_size, "contain", margin)
    w, h = float(page_size[0]), float(page_size[1])
    size = max(4.0, min(72.0, float(font_size)))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").replace("\f", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
    with ENGINE_LOCK:
        fonts = _fonts()
        missing: list[str] = []
        max_w = w - 2 * margin
        leading = size * 1.3
        lines: list[str] = []
        for raw in text.split("\n"):
            lines.extend(_wrap(raw, max_w, fonts, size, missing))
        while lines and not lines[-1].strip():
            lines.pop()
        per_page = max(1, int((h - 2 * margin) // leading))
        out = pymupdf.open()
        try:
            for start in range(0, len(lines), per_page):
                page = out.new_page(width=w, height=h)
                tw = pymupdf.TextWriter(page.rect)
                y = margin + size
                for line in lines[start:start + per_page]:
                    x = margin
                    for chunk, font in _runs(line, fonts, missing):
                        if chunk:
                            tw.append((x, y), chunk, font=font, fontsize=size)
                            x += font.text_length(chunk, fontsize=size)
                    y += leading
                tw.write_text(page)
            try:
                out.subset_fonts()
            except Exception:
                pass
        except BaseException:
            out.close()
            raise
    if missing and warnings is not None:
        warnings.append(_("Alcuni caratteri non sono disponibili in nessun font e sono stati sostituiti."))
    return out


# =========================================================================
# PAG-09 — appunti → PDF
# =========================================================================

CLIP_PDF = "application/pdf"
CLIP_IMAGE = "image"
CLIP_URIS = "uris"
CLIP_HTML = "html"
CLIP_TEXT = "text"
CLIPBOARD_ORDER = (CLIP_PDF, CLIP_IMAGE, CLIP_URIS, CLIP_HTML, CLIP_TEXT)


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table|ul|ol|pre|blockquote)>", "\n", markup)
    markup = re.sub(r"(?i)<li\b[^>]*>", "• ", markup)
    markup = re.sub(r"<[^>]+>", "", markup)
    return _html.unescape(markup).strip("\n")


def uri_to_path(uri: str) -> str | None:
    uri = uri.strip()
    if not uri or uri.startswith("#"):
        return None
    if uri.startswith("file:"):
        return unquote(urlparse(uri).path) or None
    if "://" in uri:
        return None
    return uri


def clipboard_kind(data: dict) -> str | None:
    """Tipo che verrà usato tra quelli presenti, nell'ordine di preferenza PAG-09."""
    for key in CLIPBOARD_ORDER:
        val = data.get(key)
        if key == CLIP_PDF and val:
            return key
        if key == CLIP_IMAGE and val:
            return key
        if key == CLIP_URIS and val:
            paths = [p for p in (uri_to_path(u) for u in val) if p]
            if any(Path(p).is_file() for p in paths):
                return key
        if key in (CLIP_HTML, CLIP_TEXT) and isinstance(val, str) and val.strip():
            return key
    return None


def clipboard_payload_to_pdf(data: dict, *, page_size=None, margin: float = 0.0, text_page_size=None,
                             text_margin: float = 56, font_size: float = 11,
                             errors: list[str] | None = None) -> pymupdf.Document | None:
    """Converte un'istantanea degli appunti in un PDF (None se non c'è nulla di utile).

    `data` è un dizionario con alcune di queste chiavi:
    ``"application/pdf"`` (bytes), ``"image"`` (bytes PNG/JPEG…),
    ``"uris"`` (elenco di percorsi o URI file://), ``"html"`` (str),
    ``"text"`` (str). È usato il primo tipo presente nell'ordine di
    preferenza; per le immagini `page_size=None` significa «come l'immagine»
    (un'immagine più grande del formato scelto viene ridotta)."""
    if errors is None:
        errors = []
    kind = clipboard_kind(data or {})
    if kind is None:
        return None
    if kind == CLIP_PDF:
        try:
            with ENGINE_LOCK:
                doc = pymupdf.open(stream=bytes(data[CLIP_PDF]), filetype="pdf")
                if doc.needs_pass or doc.page_count == 0:
                    doc.close()
                    raise ValueError
                return doc
        except Exception as e:
            raise PageOpError(_("I dati PDF negli appunti non sono validi.")) from e
    if kind == CLIP_IMAGE:
        import tempfile
        payload = bytes(data[CLIP_IMAGE])
        with tempfile.TemporaryDirectory(prefix="foliant-clip-") as tmp:
            p = Path(tmp) / "appunti"
            p.write_bytes(payload)
            return images_to_pdf([str(p)], page_size=page_size, margin=margin,
                                 fit="contain", errors=errors)
    if kind == CLIP_URIS:
        paths = [p for p in (uri_to_path(u) for u in data[CLIP_URIS]) if p and Path(p).is_file()]
        return files_to_pdf(paths, page_size=page_size, margin=margin, errors=errors)
    text = _html_to_text(data[CLIP_HTML]) if kind == CLIP_HTML else data[CLIP_TEXT]
    return text_to_pdf(text, page_size=text_page_size or PAPER_SIZES["A4"], margin=text_margin,
                       font_size=font_size, warnings=errors)


def files_to_pdf(paths: Sequence[str], *, page_size=None, margin: float = 0.0,
                 errors: list[str] | None = None) -> pymupdf.Document:
    """Elenco di file (PDF, immagini, altri formati apribili) → un unico PDF."""
    from .engine import open_source
    if errors is None:
        errors = []
    out = pymupdf.open()
    try:
        for p in paths:
            try:
                if Path(p).suffix.lower() in IMAGE_SUFFIXES or Path(p).suffix.lower() in (".heic", ".heif"):
                    src = images_to_pdf([p], page_size=page_size, margin=margin, errors=errors)
                else:
                    src = open_source(p)
            except Exception as e:
                msg = str(e) or _("«{n}»: formato non supportato o file danneggiato.").format(n=Path(p).name)
                if msg not in errors:
                    errors.append(msg)
                continue
            with ENGINE_LOCK:
                try:
                    out.insert_pdf(src)
                finally:
                    src.close()
        if out.page_count == 0:
            raise PageOpError(_("Gli appunti non contengono dati utilizzabili."))
    except BaseException:
        out.close()
        raise
    return out


# =========================================================================
# PAG-13 — pagine → immagini
# =========================================================================

class ResolutionTooHigh(PageOpError):
    """La risoluzione richiesta supera il limite: `suggested_dpi` è la proposta."""

    def __init__(self, message: str, suggested_dpi: int):
        super().__init__(message)
        self.suggested_dpi = suggested_dpi


def _page_rect(page: pymupdf.Page, area: str) -> pymupdf.Rect:
    if area == "media":
        r = page.mediabox * page.rotation_matrix
        return pymupdf.Rect(r).normalize()
    return page.rect


def suggested_dpi(pdf: pymupdf.Document, indices: Iterable[int], dpi: float, *, area: str = "crop",
                  max_pixels: int = DEFAULT_MAX_PIXELS) -> int | None:
    """None se a `dpi` tutte le immagini restano entro `max_pixels`, altrimenti
    la risoluzione massima (intera) che rispetta il limite."""
    worst = 0.0
    for i in indices:
        r = _page_rect(pdf[i], area)
        worst = max(worst, r.width * r.height)
    if worst <= 0:
        return None
    pixels = worst * (dpi / 72.0) ** 2
    if pixels <= max_pixels:
        return None
    return max(10, int(72.0 * (max_pixels / worst) ** 0.5))


def _render(pdf: pymupdf.Document, index: int, *, dpi: float, colorspace: str, transparent: bool,
            annots: bool, area: str):
    """Rendering di una pagina → immagine Pillow."""
    page = pdf[index]
    alpha = transparent
    cs = pymupdf.csGRAY if colorspace in ("gray", "bw") else pymupdf.csRGB
    matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
    tmp = None
    try:
        if area == "media" and page.cropbox != page.mediabox:
            # copia temporanea della pagina con CropBox = MediaBox: il documento non cambia
            tmp = pymupdf.open()
            tmp.insert_pdf(pdf, from_page=index, to_page=index, annots=annots)
            page = tmp[0]
            page.set_cropbox(page.mediabox)
        pix = page.get_pixmap(matrix=matrix, colorspace=cs, alpha=alpha, annots=annots)
        mode = {(1, False): "L", (1, True): "LA", (3, False): "RGB", (3, True): "RGBA"}[(pix.n - pix.alpha, bool(pix.alpha))]
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    finally:
        if tmp is not None:
            tmp.close()
    if colorspace == "bw":
        if img.mode == "LA":
            a = img.getchannel("A")
            img = img.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("1")
            if transparent:
                img = Image.merge("LA", (img.convert("L"), a))
        else:
            img = img.point(lambda v: 255 if v >= 128 else 0).convert("1")
    return img


def _save_kwargs(fmt: str, img, dpi: float, quality: int) -> dict:
    kw: dict = {"dpi": (dpi, dpi)}
    if fmt == "jpeg":
        kw.update(quality=max(1, min(100, int(quality))), optimize=True)
    elif fmt == "png":
        kw.update(optimize=False, compress_level=6)
    elif fmt == "tiff":
        kw["compression"] = "group4" if img.mode == "1" else "tiff_deflate"
    elif fmt == "webp":
        kw.update(quality=max(1, min(100, int(quality))))
        kw.pop("dpi")
    return kw


def _for_format(img, fmt: str):
    if fmt == "jpeg" and img.mode not in ("L", "RGB"):
        return img.convert("RGB") if img.mode != "1" else img.convert("L")
    if fmt == "bmp" and img.mode in ("RGBA", "LA"):
        return img.convert("RGB" if img.mode == "RGBA" else "L")
    if fmt == "webp" and img.mode in ("1", "L", "LA"):
        return img.convert("RGBA" if img.mode == "LA" else "RGB")
    return img


def export_page_images(pdf: pymupdf.Document, indices: Sequence[int], folder: str | os.PathLike, *,
                       fmt: str = "png", dpi: float = 150, colorspace: str = "rgb", quality: int = 90,
                       transparent: bool = False, annots: bool = True, area: str = "crop",
                       multipage_tiff: bool = False, template: str = "{nome}_p{pagina}",
                       base_name: str = "doc", max_pixels: int = DEFAULT_MAX_PIXELS,
                       overwrite: bool = False, progress=None,
                       errors: list[str] | None = None) -> list[Path]:
    """Esporta le pagine `indices` come immagini nella cartella `folder` (PAG-13).

    Rendering con rotazione applicata, entro CropBox (`area="crop"`) o MediaBox
    (`"media"`); risoluzione scritta nei metadati. `transparent` vale solo per
    PNG/TIFF/WebP. Con `multipage_tiff` produce un solo TIFF con un fotogramma
    per pagina. `progress` può essere un oggetto con `update(frazione, testo)`
    o una funzione `(frazione, testo)`. Le pagine non renderizzabili sono
    saltate e descritte in `errors`. Solleva `ResolutionTooHigh` se la
    risoluzione supera `max_pixels` (con la risoluzione proposta).

    Accede al motore tenendo ENGINE_LOCK pagina per pagina (rientrante)."""
    if Image is None:  # pragma: no cover
        raise PageOpError(_("Formato immagine non supportato: {f}.").format(f=fmt))
    fmt = (fmt or "png").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt == "tif":
        fmt = "tiff"
    if fmt not in EXPORT_FORMATS:
        raise PageOpError(_("Formato immagine non supportato: {f}.").format(f=fmt))
    try:
        dpi = float(dpi)
    except (TypeError, ValueError):
        dpi = 0
    if not 10 <= dpi <= 2400:
        raise PageOpError(_("La risoluzione deve essere tra 10 e 2400 dpi."))
    indices = list(indices)
    if not indices:
        raise PageOpError(_("Nessuna pagina da esportare."))
    if fmt in ("jpeg", "bmp"):
        transparent = False
    if errors is None:
        errors = []
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PageOpError(_("Impossibile creare la cartella «{d}».").format(d=str(folder))) from e

    with ENGINE_LOCK:
        n = pdf.page_count
        indices = [i for i in indices if 0 <= i < n]
        if not indices:
            raise PageOpError(_("Nessuna pagina da esportare."))
        sug = suggested_dpi(pdf, indices, dpi, area=area, max_pixels=max_pixels)
    if sug is not None:
        raise ResolutionTooHigh(
            _("A {d} dpi le immagini supererebbero il limite di {m} megapixel. Risoluzione proposta: {s} dpi.").format(
                d=int(dpi), m=max_pixels // 1_000_000, s=sug), sug)

    def report(fraction: float, text: str) -> None:
        if progress is None:
            return
        if hasattr(progress, "update"):
            progress.update(fraction, text)
        else:
            progress(fraction, text)

    ext = EXPORT_EXT[fmt]
    pil_fmt = {"png": "PNG", "jpeg": "JPEG", "tiff": "TIFF", "bmp": "BMP", "webp": "WEBP"}[fmt]
    total = len(indices)
    created: list[Path] = []
    taken: set[str] = set()
    tiff_frames: list = []

    def name_for(i: int, first: int, last: int) -> Path:
        stem = render_name(template or "{nome}_p{pagina}", nome=base_name, pagina=i + 1, n=i + 1,
                           inizio=first + 1, fine=last + 1)
        return unique_path(folder, stem + ext, overwrite=overwrite, taken=taken)

    for k, i in enumerate(indices):
        report(k / total, _("Pagina {p} di {t}").format(p=k + 1, t=total))
        try:
            with ENGINE_LOCK:
                img = _render(pdf, i, dpi=dpi, colorspace=colorspace, transparent=transparent,
                              annots=annots, area=area)
        except Exception as e:  # pagina non renderizzabile
            if type(e).__name__ == "OperationCancelled":
                raise
            errors.append(_("Pagina {p}: rendering non riuscito ({e}).").format(p=i + 1, e=e))
            continue
        img = _for_format(img, fmt)
        if fmt == "tiff" and multipage_tiff:
            tiff_frames.append(img)
            continue
        path = name_for(i, i, i)
        try:
            img.save(path, pil_fmt, **_save_kwargs(fmt, img, dpi, quality))
        except OSError as e:
            raise PageOpError(_("Impossibile scrivere «{n}»: {e}.").format(n=path.name, e=e.strerror or e)) from e
        created.append(path)

    if tiff_frames:
        path = name_for(indices[0], indices[0], indices[-1]) if "{pagina}" not in (template or "") else \
            unique_path(folder, render_name((template or "").replace("{pagina}", "{inizio}-{fine}"),
                                            nome=base_name, inizio=indices[0] + 1, fine=indices[-1] + 1) + ext,
                        overwrite=overwrite, taken=taken)
        first = tiff_frames[0]
        try:
            first.save(path, "TIFF", save_all=True, append_images=tiff_frames[1:],
                       **_save_kwargs("tiff", first, dpi, quality))
        except Exception:
            # modalità miste (es. bianco e nero con trasparenza): compressione generica
            first.save(path, "TIFF", save_all=True, append_images=tiff_frames[1:], dpi=(dpi, dpi),
                       compression="tiff_deflate")
        created.append(path)
    report(1.0, "")
    return created
