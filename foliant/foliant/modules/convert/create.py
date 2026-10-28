"""Creazione di PDF da altri formati (logica senza interfaccia).

Funzioni principali (tutte restituiscono i byte del PDF, che l'interfaccia apre
come nuovo documento non salvato):

- `images_to_pdf` (CNV-19): immagini → PDF, JPEG senza ricompressione;
- `blank_pdf` (CNV-23): PDF vuoto con stile carta condiviso come Form XObject;
- `text_to_pdf`, `markdown_to_pdf`, `csv_to_pdf`, `rtf_to_pdf` (CNV-20);
- `html_to_pdf`, `url_to_html` (CNV-21);
- `office_to_pdf` (CNV-18) tramite LibreOffice locale;
- `clipboard_to_pdf` (CNV-22, parte logica);
- `file_to_pdf` e `batch_create` (CNV-28).
"""

from __future__ import annotations

import csv
import functools
import html as _html
import io
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, PasswordRequired, WrongPassword, _

from .common import ConversionError, OutputFiles, check

# ====================================================================== costanti
MM = 72.0 / 25.4
MAX_PAGE_PT = 14400.0                 # limite pratico di un lato senza /UserUnit (200 pollici)
MIN_SIDE_PT = 3.0
MAX_SIDE_PT = MAX_PAGE_PT * 1000.0    # 200 000 pollici: oltre è certamente un errore
MAX_PAGES = 100_000

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A0": (2383.94, 3370.39),
    "A1": (1683.78, 2383.94),
    "A2": (1190.55, 1683.78),
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "A6": (297.64, 419.53),
    "B4": (708.66, 1000.63),
    "B5": (498.90, 708.66),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Tabloid": (792.0, 1224.0),
}

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff", ".bmp", ".dib", ".gif",
                    ".webp", ".jp2", ".j2k", ".jpx", ".pnm", ".ppm", ".pgm", ".pbm", ".svg")
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv", ".tsv")
MARKDOWN_EXTENSIONS = (".md", ".markdown")
CSV_EXTENSIONS = (".csv", ".tsv")
HTML_EXTENSIONS = (".html", ".htm", ".xhtml")
OFFICE_EXTENSIONS = (".docx", ".docm", ".doc", ".dotx", ".dot", ".odt", ".ott", ".rtf", ".wpd",
                     ".xlsx", ".xlsm", ".xls", ".ods", ".ots",
                     ".pptx", ".pptm", ".ppt", ".pps", ".ppsx", ".odp", ".otp",
                     ".vsdx", ".vsd", ".odg")
# formati che PyMuPDF apre direttamente come documenti
MUPDF_DOC_EXTENSIONS = (".xps", ".oxps", ".epub", ".fb2", ".cbz", ".mobi")
SUPPORTED_EXTENSIONS = (IMAGE_EXTENSIONS + TEXT_EXTENSIONS + HTML_EXTENSIONS + OFFICE_EXTENSIONS
                        + MUPDF_DOC_EXTENSIONS + (".pdf",))

_ARABIC_RE = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]")


def _ext(path) -> str:
    return Path(str(path)).suffix.lower()


def _save(pdf: pymupdf.Document) -> bytes:
    """Serializza il documento (chiamare con ENGINE_LOCK)."""
    return pdf.tobytes(garbage=3, deflate=True, deflate_fonts=True)


# ===================================================================== ordinamento
def natural_sort_key(s) -> tuple:
    """Chiave per l'ordine naturale: «img2» prima di «img10», senza distinzione di maiuscole."""
    text = str(s)
    parts = re.split(r"(\d+)", text.casefold())
    key = []
    for p in parts:
        if p.isdigit():
            key.append((0, int(p), p))
        elif p:
            key.append((1, 0, p))
    return tuple(key)


def _image_date(path: str) -> float:
    try:
        from PIL import Image
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
            if raw:
                import datetime as _dt
                return _dt.datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def sort_images(paths, mode: str = "name") -> list[str]:
    """Ordina i percorsi: "name" (ordine naturale del nome file), "date" (data di scatto EXIF
    o di modifica), "none" (ordine ricevuto)."""
    paths = [str(p) for p in paths]
    if mode == "name":
        return sorted(paths, key=lambda p: (natural_sort_key(Path(p).name), natural_sort_key(p)))
    if mode == "date":
        return sorted(paths, key=lambda p: (_image_date(p), natural_sort_key(Path(p).name)))
    return paths


# ======================================================================= formati
def _resolve_size(size) -> tuple[float, float]:
    if isinstance(size, str):
        for name, dims in PAGE_SIZES.items():
            if name.lower() == size.strip().lower():
                return dims
        raise ConversionError(_("Formato pagina «{s}» sconosciuto. Formati disponibili: {l}.").format(
            s=size, l=", ".join(PAGE_SIZES)))
    try:
        w, h = float(size[0]), float(size[1])
    except Exception as e:
        raise ConversionError(_("Formato pagina non valido.")) from e
    return w, h


def _orient(w: float, h: float, orientation: str) -> tuple[float, float]:
    if orientation == "landscape":
        return max(w, h), min(w, h)
    if orientation == "portrait":
        return min(w, h), max(w, h)
    return w, h


def size_from_units(w: float, h: float, unit: str = "mm") -> tuple[float, float]:
    """Converte larghezza e altezza da "mm", "cm", "in" o "pt" in punti."""
    factor = {"mm": MM, "cm": 10 * MM, "in": 72.0, "inch": 72.0, "pt": 1.0}.get(unit)
    if factor is None:
        raise ConversionError(_("Unità di misura «{u}» non riconosciuta (usare mm, in o pt).").format(u=unit))
    return float(w) * factor, float(h) * factor


def _check_side(w: float, h: float) -> None:
    for v in (w, h):
        if not math.isfinite(v) or v < MIN_SIDE_PT or v > MAX_SIDE_PT:
            raise ConversionError(_(
                "Dimensioni della pagina non valide ({w:.1f} × {h:.1f} pt): ogni lato deve essere compreso "
                "tra {a:g} pt ({amm:.2f} mm) e {b:g} pt ({bm:g} m).").format(
                w=w, h=h, a=MIN_SIDE_PT, amm=MIN_SIDE_PT / MM, b=MAX_SIDE_PT, bm=round(MAX_SIDE_PT / MM / 1000)))


# ====================================================================== PDF vuoto
def _pattern_ops(w: float, h: float, paper: str, step: float, color) -> str:
    """Operatori PDF del motivo carta in un riquadro w×h (origine in basso a sinistra)."""
    r, g, b = color
    ops = []
    if paper in ("lines", "grid"):
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG 0.4 w")
        y = step
        while y < h - 1e-6:
            ops.append(f"0 {h - y:.4f} m {w:.4f} {h - y:.4f} l")
            y += step
        if paper == "grid":
            x = step
            while x < w - 1e-6:
                ops.append(f"{x:.4f} 0 m {x:.4f} {h:.4f} l")
                x += step
        ops.append("S")
    elif paper == "dots":
        rad = max(0.35, min(1.0, step * 0.06))
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
        y = step
        while y < h - 1e-6:
            x = step
            while x < w - 1e-6:
                ops.append(f"{x - rad:.3f} {h - y - rad:.3f} {2 * rad:.3f} {2 * rad:.3f} re")
                x += step
            y += step
        ops.append("f")
    return "\n".join(ops)


def blank_pdf(*, size="A4", orientation: str = "portrait", pages: int = 1, background=(1, 1, 1),
              paper: str = "blank", step_mm: float = 5.0, line_color=(0.75, 0.8, 0.85)) -> bytes:
    """PDF con pagine vuote o con stile carta (righe, quadretti, punti).

    `size` è un nome di `PAGE_SIZES` o (larghezza, altezza) in punti. Il motivo è un
    unico Form XObject condiviso da tutte le pagine, marcato come artefatto di sfondo.
    Oltre 14 400 pt per lato si usa `/UserUnit`.
    """
    w, h = _resolve_size(size)
    w, h = _orient(w, h, orientation)
    _check_side(w, h)
    try:
        pages = int(pages)
    except Exception as e:
        raise ConversionError(_("Numero di pagine non valido.")) from e
    if pages < 1 or pages > MAX_PAGES:
        raise ConversionError(_("Il numero di pagine deve essere compreso tra 1 e {n}.").format(n=MAX_PAGES))
    if paper not in ("blank", "lines", "grid", "dots"):
        raise ConversionError(_("Stile carta «{p}» sconosciuto.").format(p=paper))
    unit = 1.0
    if max(w, h) > MAX_PAGE_PT:
        unit = max(w, h) / MAX_PAGE_PT
    mw, mh = w / unit, h / unit
    step = float(step_mm) * MM / unit
    if paper != "blank":
        if not math.isfinite(step) or step * unit < 1.0:
            raise ConversionError(_("Il passo del motivo deve essere di almeno 0,4 mm."))
        n = (mw / step) * (mh / step) if paper == "dots" else (mw + mh) / step
        if n > 2_000_000:
            raise ConversionError(_("Il passo è troppo piccolo per queste dimensioni di pagina."))

    parts = []
    if background is not None and tuple(background) != (1, 1, 1):
        r, g, b = background
        parts.append(f"{r:.4f} {g:.4f} {b:.4f} rg 0 0 {mw:.4f} {mh:.4f} re f")
    if paper != "blank":
        parts.append(_pattern_ops(mw, mh, paper, step, line_color))
    with ENGINE_LOCK:
        pdf = pymupdf.open()
        for _i in range(pages):
            pdf.new_page(width=mw, height=mh)
        if parts:
            form = pdf.get_new_xref()
            pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {mw:.4f} {mh:.4f}]"
                                    f"/Resources<<>>>>")
            pdf.update_stream(form, ("q\n" + "\n".join(parts) + "\nQ").encode())
            contents = pdf.get_new_xref()
            pdf.update_object(contents, "<<>>")
            pdf.update_stream(contents, b"/Artifact <</Type /Pagination /Subtype /Background>> BDC\n"
                                        b"q /FolBg Do Q\nEMC")
            for pno in range(pages):
                xref = pdf.page_xref(pno)
                pdf.xref_set_key(xref, "Resources", f"<</XObject<</FolBg {form} 0 R>>>>")
                pdf.xref_set_key(xref, "Contents", f"{contents} 0 R")
        if unit != 1.0:
            for pno in range(pages):
                pdf.xref_set_key(pdf.page_xref(pno), "UserUnit", f"{unit:.6f}")
        return _save(pdf)


def effective_page_size(page: pymupdf.Page) -> tuple[float, float]:
    """Dimensioni reali in punti tenendo conto di `/UserUnit`."""
    with ENGINE_LOCK:
        typ, val = page.parent.xref_get_key(page.xref, "UserUnit")
        try:
            unit = float(val) if typ in ("int", "real", "float") else 1.0
        except ValueError:
            unit = 1.0
        return page.mediabox.width * unit, page.mediabox.height * unit


# ===================================================================== immagini
class _ImgItem:
    """Un'immagine (o un fotogramma) pronta da inserire."""

    def __init__(self, data: bytes | None, w: int, h: int, dpi: tuple[float, float], rotate_cw: int,
                 label: str, svg_doc: bytes | None = None, pil_frame=None, is_jpeg=False):
        self.data, self.w, self.h, self.dpi = data, w, h, dpi
        self.rotate_cw, self.label, self.svg_pdf = rotate_cw % 360, label, svg_doc
        self.pil_frame, self.is_jpeg = pil_frame, is_jpeg

    def natural_pt(self) -> tuple[float, float]:
        w = self.w * 72.0 / self.dpi[0]
        h = self.h * 72.0 / self.dpi[1]
        return (h, w) if self.rotate_cw in (90, 270) else (w, h)


_EXIF_CW = {1: 0, 3: 180, 6: 90, 8: 270}


def _dpi_of(im) -> tuple[float, float]:
    dpi = im.info.get("dpi")
    try:
        x, y = float(dpi[0]), float(dpi[1])
        if 10 <= x <= 20000 and 10 <= y <= 20000:
            return x, y
    except Exception:
        pass
    return 72.0, 72.0


def _frame_to_png(frame) -> bytes:
    mode = frame.mode
    if mode in ("RGBX", "YCbCr", "LAB", "HSV", "CMYK"):
        frame = frame.convert("RGB")
    elif mode in ("I", "F", "I;16B", "I;16L", "I;16N"):
        try:
            frame = frame.convert("I;16") if mode != "F" else frame.convert("L")
        except Exception:
            frame = frame.convert("L")
    elif mode == "PA":
        frame = frame.convert("RGBA")
    buf = io.BytesIO()
    frame.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def _load_images(path: str, rotation_cw: int, all_frames: bool, gif_all_frames: bool = False) -> list[_ImgItem]:
    """Legge un file immagine e restituisce gli elementi da inserire (uno per fotogramma)."""
    from PIL import Image, ImageOps
    ext = _ext(path)
    name = Path(path).name
    if ext == ".svg":
        with ENGINE_LOCK:
            src = pymupdf.open(path)
            data = src.convert_to_pdf()
            r = src[0].rect
            src.close()
        return [_ImgItem(None, int(r.width), int(r.height), (72.0, 72.0), rotation_cw, name, svg_doc=data)]
    raw = Path(path).read_bytes()
    im = Image.open(io.BytesIO(raw))
    fmt = (im.format or "").upper()
    dpi = _dpi_of(im)
    if fmt == "JPEG":
        try:
            im.draft(im.mode, (max(1, im.width // 8), max(1, im.height // 8)))
            im.load()                                  # verifica rapida dell'integrità
        except Exception as e:
            raise ConversionError(_("immagine danneggiata ({e})").format(e=e)) from e
        im = Image.open(io.BytesIO(raw))
        try:
            orient = int(im.getexif().get(0x0112, 1) or 1)
        except Exception:
            orient = 1
        if orient in _EXIF_CW:
            return [_ImgItem(raw, im.width, im.height, dpi, _EXIF_CW[orient] + rotation_cw, name,
                             pil_frame=lambda r=raw: Image.open(io.BytesIO(r)), is_jpeg=True)]
        fixed = ImageOps.exif_transpose(im)            # orientamenti speculari: si ricodifica senza perdita
        data = _frame_to_png(fixed)
        return [_ImgItem(data, fixed.width, fixed.height, dpi, rotation_cw, name,
                         pil_frame=lambda d=data: Image.open(io.BytesIO(d)))]
    im.load()
    if fmt == "PNG":
        return [_ImgItem(raw, im.width, im.height, dpi, rotation_cw, name,
                         pil_frame=lambda r=raw: Image.open(io.BytesIO(r)))]
    n = getattr(im, "n_frames", 1) or 1
    if fmt in ("GIF", "WEBP", "PNG", "MPO"):
        n = n if gif_all_frames else 1     # animazioni: primo fotogramma salvo opzione
    elif not all_frames:
        n = 1
    items = []
    for i in range(n):
        im.seek(i)
        frame = im.copy() if fmt != "GIF" else im.convert("RGBA")
        frame = ImageOps.exif_transpose(frame) if fmt in ("TIFF", "WEBP") else frame
        data = _frame_to_png(frame)
        label = name if n == 1 else f"{name} [{i + 1}/{n}]"
        items.append(_ImgItem(data, frame.width, frame.height, dpi, rotation_cw, label,
                              pil_frame=lambda d=data: Image.open(io.BytesIO(d))))
    return items


def _recompress(item: _ImgItem, target_w_pt: float, target_h_pt: float, max_ppi, jpeg_quality: int):
    """Ricomprime l'immagine (orientata) riducendola a `max_ppi`. Restituisce (byte, w, h)."""
    from PIL import Image, ImageOps
    im = item.pil_frame()
    im = ImageOps.exif_transpose(im) if item.is_jpeg else im
    exif_cw = 0
    if item.is_jpeg:
        try:
            exif_cw = _EXIF_CW.get(int(Image.open(io.BytesIO(item.data)).getexif().get(0x0112, 1) or 1), 0)
        except Exception:
            exif_cw = 0
    extra = (item.rotate_cw - exif_cw) % 360
    if extra:
        im = im.rotate(-extra, expand=True)
    if max_ppi:
        ppi = max(im.width / max(target_w_pt / 72.0, 1e-6), im.height / max(target_h_pt / 72.0, 1e-6))
        if ppi > max_ppi:
            f = max_ppi / ppi
            im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    buf = io.BytesIO()
    if has_alpha:
        im.save(buf, "PNG")
    else:
        if im.mode not in ("RGB", "L", "CMYK"):
            im = im.convert("RGB")
        im.save(buf, "JPEG", quality=int(jpeg_quality), optimize=True)
    return buf.getvalue(), im.width, im.height


def _clip_new_contents(page: pymupdf.Page, before: set, clip: pymupdf.Rect) -> None:
    """Racchiude i nuovi stream di contenuto in un tracciato di ritaglio."""
    pdf = page.parent
    h = page.mediabox.height
    pre = f"q {clip.x0:.4f} {h - clip.y1:.4f} {clip.width:.4f} {clip.height:.4f} re W n\n".encode()
    for xref in page.get_contents():
        if xref not in before:
            pdf.update_stream(xref, pre + pdf.xref_stream(xref) + b"\nQ")


def _place_item(pdf, item: _ImgItem, *, page_size, orientation, fit, margin, quality, max_ppi,
                jpeg_quality, warnings) -> None:
    nat_w, nat_h = item.natural_pt()
    if page_size is None:
        pw, ph = nat_w + 2 * margin, nat_h + 2 * margin
        if max(pw, ph) > MAX_PAGE_PT:
            f = MAX_PAGE_PT / max(pw, ph)
            nat_w, nat_h, margin = nat_w * f, nat_h * f, margin * f
            pw, ph = pw * f, ph * f
            warnings.append(_("«{n}»: pagina oltre 200 pollici, ridotta in scala.").format(n=item.label))
        pw, ph = max(pw, MIN_SIDE_PT), max(ph, MIN_SIDE_PT)
    else:
        bw, bh = _resolve_size(page_size)
        if orientation == "auto":
            pw, ph = (max(bw, bh), min(bw, bh)) if nat_w > nat_h else (min(bw, bh), max(bw, bh))
        else:
            pw, ph = _orient(bw, bh, orientation)
    page = pdf.new_page(width=pw, height=ph)
    inner = pymupdf.Rect(margin, margin, pw - margin, ph - margin)
    if inner.is_empty or inner.width < 1 or inner.height < 1:
        inner = pymupdf.Rect(0, 0, pw, ph)
    if page_size is None:
        scale = min(inner.width / nat_w, inner.height / nat_h)
    elif fit == "fill":
        scale = max(inner.width / nat_w, inner.height / nat_h)
    elif fit == "actual":
        scale = 1.0
    else:
        scale = min(inner.width / nat_w, inner.height / nat_h)
    dw, dh = nat_w * scale, nat_h * scale
    cx, cy = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
    rect = pymupdf.Rect(cx - dw / 2, cy - dh / 2, cx + dw / 2, cy + dh / 2)
    before = set(page.get_contents())
    if item.svg_pdf is not None:
        src = pymupdf.open("pdf", item.svg_pdf)
        page.show_pdf_page(rect, src, 0, rotate=-item.rotate_cw, keep_proportion=False)
        src.close()
    else:
        data, rot = item.data, item.rotate_cw
        if quality == "recompress":
            data, _w, _h = _recompress(item, dw, dh, max_ppi, jpeg_quality)
            rot = 0
        page.insert_image(rect, stream=data, rotate=-rot % 360, keep_proportion=False)
    if not inner.contains(rect + (0.01, 0.01, -0.01, -0.01)):
        _clip_new_contents(page, before, inner & page.rect)


def images_to_pdf(paths: list[str], *, page_size=None, orientation: str = "auto", fit: str = "contain",
                  margin_pt: float = 0, quality: str = "original", max_ppi: int | None = None,
                  jpeg_quality: int = 85, rotations: dict[str, int] | None = None, all_frames: bool = True,
                  gif_all_frames: bool = False, progress=None) -> tuple[bytes, list[str]]:
    """Crea un PDF con una pagina per immagine (o per fotogramma di TIFF multipagina).

    `page_size` None = pagina delle dimensioni dell'immagine (dpi dei metadati, 72 se assenti);
    altrimenti nome di `PAGE_SIZES` o (larghezza, altezza) in punti. `rotations` mappa un
    percorso a una rotazione in gradi orari. `all_frames` vale per i TIFF multipagina (una
    pagina per fotogramma); le GIF animate danno solo il primo fotogramma salvo `gif_all_frames`.
    Restituisce (byte del PDF, avvisi). Le immagini illeggibili sono saltate con avviso;
    se nessuna è utilizzabile solleva `ConversionError`.
    """
    if not paths:
        raise ConversionError(_("Nessuna immagine selezionata."))
    if fit not in ("contain", "fill", "actual"):
        raise ConversionError(_("Modalità di adattamento «{f}» non valida.").format(f=fit))
    if page_size is not None:
        _resolve_size(page_size)
    warnings: list[str] = []
    rotations = rotations or {}
    margin = max(0.0, float(margin_pt or 0))
    with ENGINE_LOCK:
        pdf = pymupdf.open()
        total = len(paths)
        for i, path in enumerate(paths):
            name = Path(str(path)).name
            check(progress, i / total, _("Immagine {i} di {n}: {f}").format(i=i + 1, n=total, f=name))
            rot = int(rotations.get(str(path), 0) or 0)
            if rot % 90:
                warnings.append(_("«{n}»: rotazione {r}° non multipla di 90, ignorata.").format(n=name, r=rot))
                rot = 0
            try:
                items = _load_images(str(path), rot, all_frames, gif_all_frames)
            except FileNotFoundError:
                warnings.append(_("«{n}»: file non trovato, escluso.").format(n=name))
                continue
            except ConversionError as e:
                warnings.append(_("«{n}»: {e}, esclusa.").format(n=name, e=e))
                continue
            except Exception as e:
                warnings.append(_("«{n}»: immagine danneggiata o formato non supportato, esclusa ({e}).").format(
                    n=name, e=type(e).__name__))
                continue
            for item in items:
                start = pdf.page_count
                try:
                    _place_item(pdf, item, page_size=page_size, orientation=orientation, fit=fit, margin=margin,
                                quality=quality, max_ppi=max_ppi, jpeg_quality=jpeg_quality, warnings=warnings)
                except Exception as e:
                    if pdf.page_count > start:
                        pdf.delete_page(pdf.page_count - 1)
                    warnings.append(_("«{n}»: impossibile inserire l'immagine, esclusa ({e}).").format(
                        n=item.label, e=e))
        if pdf.page_count == 0:
            pdf.close()
            raise ConversionError(_("Nessuna delle immagini selezionate è utilizzabile.") + "\n" + "\n".join(warnings))
        check(progress, 1.0, _("Salvataggio…"))
        data = pdf.tobytes(garbage=3, deflate=True)
        pdf.close()
    return data, warnings


# ======================================================================= font
_FONT_DIRS = [
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts", "/System/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"), os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
]

# famiglie con buona copertura (latino, greco, cirillico, arabo, ebraico)
_FONT_CANDIDATES = {
    "mono": [("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-Oblique.ttf",
              "DejaVuSansMono-BoldOblique.ttf"),
             ("NotoSansMono-Regular.ttf", "NotoSansMono-Bold.ttf", None, None),
             ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
             ("FreeMono.ttf", "FreeMonoBold.ttf", "FreeMonoOblique.ttf", "FreeMonoBoldOblique.ttf")],
    "sans": [("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
             ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
             ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf", "NotoSans-BoldItalic.ttf"),
             ("FreeSans.ttf", "FreeSansBold.ttf", "FreeSansOblique.ttf", "FreeSansBoldOblique.ttf")],
}


@functools.lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".ttf", ".otf")):
                    index.setdefault(f.lower(), os.path.join(root, f))
    return index


@functools.lru_cache(maxsize=8)
def _font_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def _find_family(kind: str) -> tuple[str | None, ...] | None:
    index = _font_index()
    for cand in _FONT_CANDIDATES[kind]:
        if cand[0].lower() in index:
            return tuple(index.get(c.lower()) if c else None for c in cand)
    return None


def _font_css(archive: pymupdf.Archive) -> tuple[str, dict[str, str]]:
    """Aggiunge all'archivio i font di sistema trovati e restituisce (CSS @font-face, famiglie)."""
    css, fams = [], {}
    for kind in ("sans", "mono"):
        fam = _find_family(kind)
        if not fam:
            continue
        name = f"Foliant{kind.capitalize()}"
        fams[kind] = name
        styles = [("normal", "normal"), ("bold", "normal"), ("normal", "italic"), ("bold", "italic")]
        for idx, (weight, style) in enumerate(styles):
            path = fam[idx] or fam[0]
            if idx and not fam[idx]:
                # senza varianti: grassetto e corsivo sintetici dal regolare
                path = fam[0]
            fname = f"{name}-{idx}{Path(path).suffix.lower()}"
            try:
                archive.add((_font_bytes(path), fname))
            except Exception:
                continue
            css.append(f"@font-face{{font-family:{name};src:url({fname});font-weight:{weight};font-style:{style}}}")
    return "\n".join(css), fams


@functools.lru_cache(maxsize=4)
def _pymupdf_font(kind: str):
    fam = _find_family(kind)
    try:
        if fam:
            return pymupdf.Font(fontbuffer=_font_bytes(fam[0]))
    except Exception:
        pass
    return pymupdf.Font("cour" if kind == "mono" else "helv")


# ================================================================ motore Story
def _new_archive(base_dir) -> pymupdf.Archive:
    arch = pymupdf.Archive()
    if base_dir and os.path.isdir(str(base_dir)):
        arch.add(str(base_dir))
    return arch


def _story_pages(stories, page_rect: pymupdf.Rect, area: pymupdf.Rect, progress=None,
                 fraction=(0.0, 0.9)) -> tuple[bytes, list]:
    """Impagina una sequenza di Story una dopo l'altra sulle stesse pagine.

    Restituisce (byte del PDF, posizioni degli elementi con `page_num` base 1).
    """
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    positions: list = []
    dev = None
    page_num = 0
    cur = None
    stories = list(stories)
    for si, story in enumerate(stories):
        check(progress, fraction[0] + (fraction[1] - fraction[0]) * si / max(1, len(stories)),
              _("Impaginazione: pagina {n}").format(n=max(1, page_num)))
        guard = 0
        while True:
            if dev is None:
                dev = writer.begin_page(page_rect)
                page_num += 1
                cur = pymupdf.Rect(area)
            more, filled = story.place(cur)
            filled = pymupdf.Rect(filled)
            story.element_positions(_collector(positions, page_num))
            story.draw(dev)
            if more:
                writer.end_page()
                dev = None
                guard = guard + 1 if filled.is_empty else 0
                if guard > 3:
                    break
                if page_num % 20 == 0:
                    check(progress, None, _("Impaginazione: pagina {n}").format(n=page_num))
                continue
            if filled.is_empty:
                break
            cur = pymupdf.Rect(area.x0, filled.y1, area.x1, area.y1)
            if cur.height < 6:
                writer.end_page()
                dev = None
            break
    if dev is None and page_num == 0:
        writer.begin_page(page_rect)
        page_num = 1
        dev = True
    if dev is not None:
        writer.end_page()
    writer.close()
    return buf.getvalue(), positions


def _collector(positions: list, page_num: int):
    def cb(pos):
        pos.page_num = page_num
        positions.append(pos)
    return cb


def _add_links_and_toc(pdf: pymupdf.Document, positions, *, bookmarks: bool, scale: float = 1.0) -> None:
    ids = {}
    for p in positions:
        if (p.open_close & 1) and p.id and p.id not in ids:
            ids[p.id] = p
    # Story riporta un riquadro per parola: si uniscono i pezzi contigui sulla stessa riga
    merged: list[list] = []
    for p in positions:
        if not ((p.open_close & 1) and p.href):
            continue
        r = pymupdf.Rect(p.rect)
        if merged:
            last = merged[-1]
            lr = last[2]
            if (last[0] == p.page_num and last[1] == p.href and abs(lr.y0 - r.y0) < 1 and abs(lr.y1 - r.y1) < 1
                    and -1 <= r.x0 - lr.x1 <= 1):
                last[2] = lr | r
                continue
        merged.append([p.page_num, p.href, r])
    for page_num, href, r in merged:
        href = href.strip()
        r = r * scale
        if r.is_empty:
            continue
        page = pdf[page_num - 1]
        if href.startswith("#"):
            target = ids.get(urllib.parse.unquote(href[1:]))
            if target is None:
                continue
            t = pymupdf.Rect(target.rect) * scale
            page.insert_link({"kind": pymupdf.LINK_GOTO, "from": r, "page": target.page_num - 1,
                              "to": pymupdf.Point(t.x0, t.y0)})
        elif href.lower().startswith("javascript:"):
            continue
        else:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": r, "uri": href})
    if not bookmarks:
        return
    toc = []
    last = 0
    for p in positions:
        if (p.open_close & 1) and p.heading:
            title = re.sub(r"\s+", " ", p.text or "").strip()
            if not title:
                continue
            level = max(1, min(int(p.heading), last + 1))
            last = level
            t = pymupdf.Rect(p.rect) * scale
            toc.append([level, title, p.page_num, {"kind": pymupdf.LINK_GOTO, "to": pymupdf.Point(t.x0, t.y0)}])
    if toc:
        try:
            pdf.set_toc(toc)
        except Exception:
            pdf.set_toc([[1, e[1], e[2]] for e in toc])


def _base_css(fams: dict[str, str], font_size: float = 11) -> str:
    sans = fams.get("sans", "sans-serif")
    mono = fams.get("mono", "monospace")
    return (f"body{{font-family:{sans};font-size:{font_size}pt;line-height:1.35}}"
            f"pre,code,tt,kbd,samp{{font-family:{mono};font-size:0.9em}}"
            "pre{white-space:pre-wrap;background-color:#f4f4f4;padding:4pt}"
            "table{border-collapse:collapse}td,th{border:0.5pt solid #888;padding:2pt 4pt;vertical-align:top}"
            "th{background-color:#e8e8e8;font-weight:bold}"
            "blockquote{margin-left:12pt;padding-left:8pt;border-left:2pt solid #bbb;color:#444}"
            "a{color:#1a5fb4}img{max-width:100%}")


def _render_html(html: str, *, archive, user_css: str, page_size="A4", margin_mm=15, single_page=False,
                 bookmarks=True, orientation="portrait", progress=None) -> bytes:
    pw, ph = _orient(*_resolve_size(page_size), orientation)
    m = max(0.0, float(margin_mm)) * MM
    if 2 * m >= min(pw, ph) - 20:
        raise ConversionError(_("Margini troppo grandi per il formato di pagina scelto."))
    with ENGINE_LOCK:
        try:
            story = pymupdf.Story(html, user_css=user_css or None, archive=archive)
        except Exception as e:
            raise ConversionError(_("Impossibile interpretare il contenuto HTML: {e}").format(e=e)) from e
        check(progress, 0.05, _("Impaginazione…"))
        scale = 1.0
        if single_page:
            tall = pymupdf.Rect(m, m, pw - m, 5_000_000)
            more, filled = story.place(tall)
            filled = pymupdf.Rect(filled)
            height = (filled.y1 if not filled.is_empty else m) + m
            if more:
                raise ConversionError(_("La pagina è troppo lunga per una pagina unica."))
            story.reset()
            height = max(height, 3 * m + 10)
            if max(pw, height) > MAX_PAGE_PT:
                scale = MAX_PAGE_PT / max(pw, height)
            positions: list = []
            buf = io.BytesIO()
            writer = pymupdf.DocumentWriter(buf)
            dev = writer.begin_page(pymupdf.Rect(0, 0, pw * scale, height * scale))
            story.place(pymupdf.Rect(m, m, pw - m, height))
            story.element_positions(_collector(positions, 1))
            story.draw(dev, pymupdf.Matrix(scale, scale))
            writer.end_page()
            writer.close()
            data = buf.getvalue()
        else:
            data, positions = _story_pages([story], pymupdf.Rect(0, 0, pw, ph),
                                           pymupdf.Rect(m, m, pw - m, ph - m), progress)
        check(progress, 0.92, _("Collegamenti e segnalibri…"))
        pdf = pymupdf.open("pdf", data)
        _add_links_and_toc(pdf, positions, bookmarks=bookmarks, scale=scale)
        if scale != 1.0:
            pdf.xref_set_key(pdf[0].xref, "UserUnit", f"{1 / scale:.6f}")
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        meta = {"creator": "Foliant", "producer": "Foliant"}
        if title:
            meta["title"] = _html.unescape(re.sub(r"\s+", " ", title.group(1))).strip()
        try:
            pdf.set_metadata(meta)
        except Exception:
            pass
        try:
            pdf.subset_fonts()
        except Exception:
            pass
        out = _save(pdf)
        pdf.close()
    return out


# ========================================================================== HTML
def html_to_pdf(html: str, *, base_dir=None, page_size="A4", margin_mm=15, single_page=False,
                bookmarks=True, orientation: str = "portrait", progress=None) -> bytes:
    """Converte HTML in PDF con link attivi (URI e interni) e titoli h1–h6 come segnalibri.

    `base_dir` è la cartella da cui risolvere immagini e fogli di stile relativi.
    `single_page` produce un'unica pagina alta quanto il contenuto (con `/UserUnit` se serve).
    """
    if not isinstance(html, str):
        html = _decode_bytes(html)[0]
    html = re.sub(r"<script\b.*?</script\s*>", "", html, flags=re.I | re.S)
    archive = _new_archive(base_dir)
    css = ""
    if _ARABIC_RE.search(html):
        # il font arabo di ripiego incorporato ha mappature Unicode imprecise: meglio un font di sistema
        font_css, fams = _font_css(archive)
        if "sans" in fams:
            css = font_css + f"body{{font-family:{fams['sans']}}}"
    return _render_html(html, archive=archive, user_css=css, page_size=page_size, margin_mm=margin_mm,
                        single_page=single_page, bookmarks=bookmarks, orientation=orientation, progress=progress)


def _decode_bytes(data: bytes, declared: str | None = None) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace").lstrip("\ufeff"), "utf-16"
    if declared:
        try:
            return data.decode(declared), declared
        except (LookupError, UnicodeDecodeError):
            pass
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore")
        try:
            return data.decode(enc), enc
        except (LookupError, UnicodeDecodeError):
            pass
    return _decode_text(data)


def url_to_html(url: str, timeout: float = 20) -> tuple[str, str]:
    """Scarica una pagina web come visitatore anonimo (senza cookie).

    Restituisce (HTML con link resi assoluti e script rimossi, URL finale).
    Errori di rete o HTTP → `ConversionError` con il motivo.
    """
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
            url = "https://" + url
        else:
            raise ConversionError(_("Indirizzo non valido: sono ammessi solo URL http:// o https://."))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Foliant PDF)",
                                               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl()
            data = resp.read(50 * 1024 * 1024)
            charset = resp.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        raise ConversionError(_("Il server ha risposto con l'errore HTTP {c} ({r}): nessun documento creato.").format(
            c=e.code, r=e.reason)) from e
    except urllib.error.URLError as e:
        reason = e.reason
        if "CERTIFICATE" in str(reason).upper():
            raise ConversionError(_("Il certificato di sicurezza del sito non è valido: {r}").format(r=reason)) from e
        raise ConversionError(_("Impossibile raggiungere l'indirizzo: {r}").format(r=reason)) from e
    except (TimeoutError, OSError) as e:
        raise ConversionError(_("Impossibile scaricare la pagina: {e}").format(e=e)) from e
    text, _enc = _decode_bytes(data, charset)
    text = re.sub(r"<script\b.*?</script\s*>", "", text, flags=re.I | re.S)

    def absolutize(m: re.Match) -> str:
        attr, q, val = m.group(1), m.group(2), m.group(3)
        if val.startswith(("#", "data:", "mailto:", "javascript:")):
            return m.group(0)
        return f"{attr}={q}{urllib.parse.urljoin(final, val)}{q}"
    text = re.sub(r"\b(href|src)=([\"'])(.*?)\2", absolutize, text, flags=re.I)
    return text, final


# ========================================================================== testo
def _decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        enc = "utf-16-le" if data.startswith(b"\xff\xfe") else "utf-16-be"
        return data[2:].decode(enc, "replace"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # UTF-16 senza BOM: molti zeri alternati
    if len(data) >= 4 and data[1::2].count(0) > len(data) // 4:
        try:
            return data.decode("utf-16-le"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    # cp1252 contiene i caratteri «tipografici» in 0x80–0x9F; se usati sono plausibili
    ctrl = sum(1 for b in data if 0x80 <= b <= 0x9F)
    if ctrl == 0:
        return data.decode("iso-8859-1"), "iso-8859-1"
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("iso-8859-1"), "iso-8859-1"


def read_text_file(path) -> tuple[str, str]:
    """Legge un file di testo rilevando la codifica (BOM UTF-8/16, UTF-8, cp1252, ISO-8859-1).

    Restituisce (testo, nome della codifica)."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise ConversionError(_("Impossibile leggere «{n}»: {e}").format(n=Path(path).name, e=e.strerror or e)) from e
    text, enc = _decode_text(data)
    return text.replace("\r\n", "\n").replace("\r", "\n"), enc


_ADVANCES: dict[tuple[int, str], float] = {}


def _char_width(font, ch: str, fs: float) -> float:
    key = (id(font), ch)
    adv = _ADVANCES.get(key)
    if adv is None:
        adv = 1.0 if unicodedata.east_asian_width(ch) in "WF" else 0.62
        try:
            if font.has_glyph(ord(ch)):
                adv = font.glyph_advance(ord(ch))
        except Exception:
            pass
        if len(_ADVANCES) < 100_000:
            _ADVANCES[key] = adv
    return adv * fs


def _wrap_line(line: str, width: float, font, fs: float) -> list[str]:
    """A capo automatico: alle spaziature se possibile, altrimenti forzato."""
    if sum(_char_width(font, ch, fs) for ch in line) <= width:
        return [line]
    out = []
    cur, cur_w, last_space = [], 0.0, -1
    for ch in line:
        w = _char_width(font, ch, fs)
        if cur and cur_w + w > width:
            if last_space > 0 and ch != " ":
                head = cur[:last_space + 1]
                tail = cur[last_space + 1:]
                out.append("".join(head).rstrip())
                cur = tail
                cur_w = sum(_char_width(font, c, fs) for c in cur)
            elif ch == " ":
                out.append("".join(cur))
                cur, cur_w, last_space = [], 0.0, -1
                continue
            else:
                out.append("".join(cur))
                cur, cur_w = [], 0.0
            last_space = -1
        cur.append(ch)
        cur_w += w
        if ch == " ":
            last_space = len(cur) - 1
    out.append("".join(cur))
    return out


def _decorate(pdf: pymupdf.Document, title: str, margin: float) -> None:
    """Intestazione (titolo) e piè di pagina (numero) su ogni pagina."""
    font = _pymupdf_font("sans")
    n = pdf.page_count

    def safe(s: str) -> str:
        return "".join(c if font.has_glyph(ord(c)) or c == " " else "?" for c in s)
    title = safe(title)
    for i, page in enumerate(pdf):
        r = page.rect
        tw = pymupdf.TextWriter(r, color=(0.35, 0.35, 0.35))
        fs = 8
        if title:
            t = title
            while t and font.text_length(t, fs) > r.width - 2 * margin:
                t = t[:-2] + "…" if len(t) > 2 else ""
            tw.append((margin, max(fs + 2, margin / 2)), t, font=font, fontsize=fs)
        foot = safe(_("Pagina {i} di {n}").format(i=i + 1, n=n))
        fw = font.text_length(foot, fs)
        tw.append(((r.width - fw) / 2, r.height - max(6, margin / 2 - fs / 2)), foot, font=font, fontsize=fs)
        tw.write_text(page)


def text_to_pdf(text: str, *, page_size="A4", margin_mm: float = 20, font_size: float = 10, monospace: bool = True,
                line_numbers: bool = False, header_footer: bool = True, title: str = "", tab_size: int = 4,
                orientation: str = "portrait", progress=None) -> bytes:
    """Impagina testo semplice in un PDF con testo selezionabile.

    A capo automatico (forzato nelle parole più lunghe della riga), font con ampia
    copertura e ripiego automatico per CJK, arabo, ebraico; numeri di riga e
    intestazione/piè facoltativi.
    """
    pw, ph = _orient(*_resolve_size(page_size), orientation)
    m = max(0.0, float(margin_mm)) * MM
    fs = max(4.0, min(72.0, float(font_size)))
    if 2 * m >= min(pw, ph) - 4 * fs:
        raise ConversionError(_("Margini troppo grandi per il formato di pagina scelto."))
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    kind = "mono" if monospace else "sans"
    with ENGINE_LOCK:
        archive = pymupdf.Archive()
        font_css, fams = _font_css(archive)
        family = fams.get(kind, "monospace" if monospace else "sans-serif")
        mfont = _pymupdf_font(kind)
        width = (pw - 2 * m) * 0.97
        gutter = ""
        if line_numbers:
            digits = len(str(max(1, len(lines))))
            gutter = " " * (digits + 3)
            width -= sum(_char_width(mfont, c, fs) for c in gutter)
        visual: list[str] = []
        tabs = max(1, int(tab_size))
        for n, line in enumerate(lines, 1):
            if n % 5000 == 0:
                check(progress, 0.1 * n / len(lines), _("Preparazione del testo…"))
            parts = _wrap_line(line.expandtabs(tabs), width, mfont, fs)
            if line_numbers:
                parts = [f"{n:>{len(gutter) - 3}} │ " + parts[0]] + [gutter + p for p in parts[1:]]
            visual.extend(parts)
        css = (font_css + f"pre{{font-family:{family};font-size:{fs}pt;line-height:1.25;margin:0;"
               "white-space:pre}body{margin:0}")
        chunk = 300
        stories = []
        for start in range(0, max(1, len(visual)), chunk):
            body = "\n".join(_html.escape(v) for v in visual[start:start + chunk])
            stories.append(pymupdf.Story(f"<pre>{body}\n</pre>", user_css=css, archive=archive))
        data, _pos = _story_pages(stories, pymupdf.Rect(0, 0, pw, ph), pymupdf.Rect(m, m, pw - m, ph - m),
                                  progress, (0.1, 0.9))
        pdf = pymupdf.open("pdf", data)
        if header_footer:
            check(progress, 0.92, _("Intestazioni e numeri di pagina…"))
            _decorate(pdf, title, m)
        meta = {"creator": "Foliant", "producer": "Foliant"}
        if title:
            meta["title"] = title
        pdf.set_metadata(meta)
        try:
            pdf.subset_fonts()
        except Exception:
            pass
        out = _save(pdf)
        pdf.close()
    return out


# ====================================================================== Markdown
def _slug(text: str, used: set) -> str:
    base = re.sub(r"[^\w\- ]", "", unicodedata.normalize("NFKC", text).lower()).strip().replace(" ", "-") or "sez"
    s, i = base, 1
    while s in used:
        i += 1
        s = f"{base}-{i}"
    used.add(s)
    return s


class _MdConverter:
    """Sottoinsieme di CommonMark (+ tabelle, elenchi di attività, barrato) → HTML."""

    def __init__(self, base_dir, archive: pymupdf.Archive):
        self.base_dir = Path(base_dir) if base_dir else None
        self.archive = archive
        self.used_ids: set = set()
        self.images = 0
        self.warnings: list[str] = []

    # ------------------------------------------------------------- inline
    def inline(self, text: str) -> str:
        stash: list[str] = []

        def keep(html: str) -> str:
            stash.append(html)
            return f"\x00{len(stash) - 1}\x00"

        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|~>])", lambda m: keep(_html.escape(m.group(1))), text)
        text = re.sub(r"(`+)(.+?)\1", lambda m: keep(f"<code>{_html.escape(m.group(2).strip())}</code>"), text)
        text = re.sub(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)",
                      lambda m: keep(self.image(m.group(1), m.group(2))), text)
        text = re.sub(r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)",
                      lambda m: keep(f'<a href="{_html.escape(m.group(2), quote=True)}">'
                                     f'{self.inline_simple(m.group(1))}</a>'), text)
        text = re.sub(r"<((?:https?|ftp)://[^>\s]+|mailto:[^>\s]+)>",
                      lambda m: keep(f'<a href="{_html.escape(m.group(1), quote=True)}">'
                                     f'{_html.escape(m.group(1))}</a>'), text)
        text = re.sub(r"<(/?(?:b|i|u|em|strong|sub|sup|br|span|kbd|mark|s|del)\b[^>]*)>",
                      lambda m: keep(f"<{m.group(1)}>"), text)
        return self.emphasis(_html.escape(text, quote=False), stash)

    def inline_simple(self, text: str) -> str:
        return self.emphasis(_html.escape(text, quote=False), [])

    @staticmethod
    def emphasis(text: str, stash: list[str]) -> str:
        text = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<b>\2</b>", text)
        text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?!\*)", r"<i>\1</i>", text)
        text = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<i>\1</i>", text)
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text)
        text = re.sub(r" {2,}\n|\\\n", "<br/>", text)
        for _i in range(3):   # segnaposto annidati
            text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    def image(self, alt: str, src: str) -> str:
        alt_e = _html.escape(alt or "")
        if re.match(r"^(https?|ftp):", src, re.I):
            self.warnings.append(_("Immagine remota non scaricata: {u}").format(u=src))
            return f"<i>[{_('immagine remota')}: {alt_e or _html.escape(src)}]</i>"
        p = Path(urllib.parse.unquote(src.replace("file://", "")))
        if not p.is_absolute() and self.base_dir:
            p = self.base_dir / p
        try:
            data = p.read_bytes()
        except OSError:
            self.warnings.append(_("Immagine non trovata: {u}").format(u=src))
            return f"<i>[{_('immagine mancante')}: {alt_e or _html.escape(src)}]</i>"
        self.images += 1
        name = f"mdimg{self.images}{p.suffix.lower()}"
        self.archive.add((data, name))
        return f'<img src="{name}" alt="{alt_e}"/>'

    # -------------------------------------------------------------- blocchi
    def convert(self, md: str) -> str:
        lines = md.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
        return self.blocks(lines)

    def blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        para: list[str] = []

        def flush():
            if para:
                out.append(f"<p>{self.inline(chr(10).join(para).strip())}</p>")
                para.clear()

        while i < n:
            line = lines[i]
            s = line.strip()
            if not s:
                flush()
                i += 1
                continue
            fence = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)", line)
            if fence:
                flush()
                mark = fence.group(1)
                body = []
                i += 1
                while i < n and not lines[i].strip().startswith(mark[0] * len(mark)):
                    body.append(lines[i])
                    i += 1
                i += 1
                out.append("<pre><code>" + _html.escape("\n".join(body)) + "</code></pre>")
                continue
            h = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
            if h:
                flush()
                out.append(self.heading(len(h.group(1)), h.group(2)))
                i += 1
                continue
            if para and re.match(r"^\s{0,3}(=+|-+)\s*$", line):
                level = 1 if s[0] == "=" else 2
                text = " ".join(para).strip()
                para.clear()
                out.append(self.heading(level, text))
                i += 1
                continue
            if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
                flush()
                out.append("<hr/>")
                i += 1
                continue
            if not para and line.startswith("    "):
                body = []
                while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                    body.append(lines[i][4:])
                    i += 1
                while body and not body[-1].strip():
                    body.pop()
                out.append("<pre><code>" + _html.escape("\n".join(body)) + "</code></pre>")
                continue
            if s.startswith(">"):
                flush()
                body = []
                while i < n and lines[i].strip().startswith(">"):
                    body.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append("<blockquote>" + self.blocks(body) + "</blockquote>")
                continue
            if "|" in s and i + 1 < n and re.match(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$", lines[i + 1]) \
                    and lines[i + 1].count("-") >= 1:
                flush()
                i = self.table(lines, i, out)
                continue
            lm = re.match(r"^(\s*)([-*+]|\d{1,9}[.)])\s+", line)
            if lm:
                flush()
                i = self.list_block(lines, i, out)
                continue
            para.append(line)
            i += 1
        flush()
        return "\n".join(out)

    def heading(self, level: int, text: str) -> str:
        plain = re.sub(r"[*_`~]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text)
        hid = _slug(plain, self.used_ids)
        return f'<h{level} id="{hid}">{self.inline(text)}</h{level}>'

    @staticmethod
    def _cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]

    def table(self, lines, i, out) -> int:
        head = self._cells(lines[i])
        aligns = []
        for c in self._cells(lines[i + 1]):
            c = c.strip()
            aligns.append("center" if c.startswith(":") and c.endswith(":") else
                          "right" if c.endswith(":") else "left")
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            rows.append(self._cells(lines[i]))
            i += 1

        def al(k):
            return aligns[k] if k < len(aligns) else "left"
        html = ["<table><tr>" + "".join(f'<th style="text-align:{al(k)}">{self.inline(c)}</th>'
                                        for k, c in enumerate(head)) + "</tr>"]
        for r in rows:
            r = (r + [""] * len(head))[:len(head)]
            html.append("<tr>" + "".join(f'<td style="text-align:{al(k)}">{self.inline(c)}</td>'
                                         for k, c in enumerate(r)) + "</tr>")
        html.append("</table>")
        out.append("".join(html))
        return i

    def list_block(self, lines, i, out) -> int:
        first = re.match(r"^(\s*)([-*+]|\d{1,9}[.)])\s+", lines[i])
        indent = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        items: list[list[str]] = []
        n = len(lines)
        while i < n:
            line = lines[i]
            m = re.match(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$", line)
            if m and len(m.group(1)) == indent and (m.group(2)[0].isdigit()) == ordered:
                items.append([m.group(3)])
                i += 1
                continue
            if not line.strip():
                # riga vuota: l'elenco continua se segue contenuto rientrato o un'altra voce
                j = i + 1
                if j < n and (lines[j].startswith(" " * (indent + 2)) or
                              re.match(rf"^\s{{{indent}}}([-*+]|\d{{1,9}}[.)])\s+", lines[j])):
                    items[-1].append("")
                    i += 1
                    continue
                break
            lead = len(line) - len(line.lstrip())
            if lead > indent:
                # contenuto della voce (anche elenchi annidati): si toglie il rientro della voce
                items[-1].append(line[min(lead, indent + 2):])
                i += 1
                continue
            if re.match(r"^\s*([-*+]|\d{1,9}[.)])\s+", line) or re.match(r"^\s{0,3}#", line):
                break
            items[-1].append(line.strip())   # continuazione pigra
            i += 1
        tag = "ol" if ordered else "ul"
        html = [f"<{tag}>"]
        for it in items:
            first_line = it[0]
            task = re.match(r"^\[([ xX])\]\s+(.*)$", first_line)
            prefix = ""
            if task:
                done = task.group(1).lower() == "x"
                prefix = f"<code>[{'x' if done else ' '}]</code> "
                it = [task.group(2)] + it[1:]
            sub = [s for s in it]
            if len(sub) == 1 or all(not re.match(r"^\s*([-*+]|\d{1,9}[.)])\s+|^\s*$", s) for s in sub[1:]):
                body = self.inline("\n".join(s.strip() for s in sub))
            else:
                inner = self.blocks(sub)
                inner = re.sub(r"^<p>(.*?)</p>", r"\1", inner, count=1, flags=re.S)
                body = inner
            html.append(f"<li>{prefix}{body}</li>")
        html.append(f"</{tag}>")
        out.append("".join(html))
        return i


def markdown_to_html(md: str, *, base_dir=None, archive: pymupdf.Archive | None = None) -> tuple[str, list[str]]:
    """Converte Markdown in HTML (immagini locali aggiunte all'archivio). Restituisce (html, avvisi)."""
    archive = archive if archive is not None else pymupdf.Archive()
    conv = _MdConverter(base_dir, archive)
    return conv.convert(md or ""), conv.warnings


def markdown_to_pdf(md: str, *, base_dir=None, page_size="A4", margin_mm: float = 18, progress=None) -> bytes:
    """Markdown (CommonMark con tabelle, elenchi di attività e barrato) → PDF con segnalibri dai titoli."""
    with ENGINE_LOCK:
        archive = _new_archive(base_dir)
        body, _warn = markdown_to_html(md, base_dir=base_dir, archive=archive)
        font_css, fams = _font_css(archive)
    title = ""
    m = re.search(r"^\s{0,3}#\s+(.+)$", md or "", re.M)
    if m:
        title = f"<title>{_html.escape(m.group(1).strip())}</title>"
    html = f"<html><head>{title}</head><body>{body}</body></html>"
    return _render_html(html, archive=archive, user_css=font_css + _base_css(fams), page_size=page_size,
                        margin_mm=margin_mm, bookmarks=True, progress=progress)


# =========================================================================== CSV
def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.split("\n")[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (";", ",", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def csv_to_pdf(path_or_text, *, delimiter: str | None = None, page_size="A4", orientation: str = "auto",
               margin_mm: float = 12, font_size: float = 8, header: bool = True, progress=None) -> bytes:
    """Tabella CSV/TSV → PDF con la riga di intestazione ripetuta in cima a ogni pagina.

    `delimiter` None = rilevato automaticamente; `orientation` "auto" passa in orizzontale
    per le tabelle larghe.
    """
    text = path_or_text
    is_path = isinstance(path_or_text, os.PathLike) or (
        isinstance(path_or_text, str) and len(path_or_text) < 4096 and "\n" not in path_or_text
        and os.path.isfile(path_or_text))
    if is_path:
        text, _enc = read_text_file(path_or_text)
        if delimiter is None and _ext(path_or_text) == ".tsv":
            delimiter = "\t"
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if delimiter is None:
        delimiter = _sniff_delimiter(text)
    try:
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    except csv.Error as e:
        raise ConversionError(_("Il file CSV non è leggibile: {e}").format(e=e)) from e
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ConversionError(_("Il file CSV è vuoto."))
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    head, body = (rows[0], rows[1:]) if header else (None, rows)

    bw, bh = _resolve_size(page_size)
    if orientation == "auto":
        est = sum(min(40, max(len(r[c]) for r in rows[:300])) * font_size * 0.55 + 8 for c in range(ncols))
        orientation = "landscape" if est > min(bw, bh) - 2 * margin_mm * MM else "portrait"
    pw, ph = _orient(bw, bh, orientation)
    m = margin_mm * MM
    area = pymupdf.Rect(m, m, pw - m, ph - m)

    with ENGINE_LOCK:
        archive = pymupdf.Archive()
        font_css, fams = _font_css(archive)
        css = (font_css + f"body{{font-family:{fams.get('sans', 'sans-serif')};font-size:{font_size}pt}}"
               "table{border-collapse:collapse;width:100%}"
               "td,th{border:0.4pt solid #999;padding:1pt 3pt;vertical-align:top;text-align:left}"
               "th{background-color:#e6e6e6;font-weight:bold}")
        head_html = ("<tr>" + "".join(f"<th>{_html.escape(c)}</th>" for c in head) + "</tr>") if head else ""

        def build(start: int, count: int):
            parts = [f'<tr id="r{start + k}">' + "".join(f"<td>{_html.escape(c)}</td>" for c in body[start + k])
                     + "</tr>" for k in range(count)]
            return pymupdf.Story(f"<table>{head_html}{''.join(parts)}</table>", user_css=css, archive=archive)

        buf = io.BytesIO()
        writer = pymupdf.DocumentWriter(buf)
        start = 0
        total = len(body)
        while True:
            check(progress, start / max(1, total), _("Tabella: riga {i} di {n}").format(i=start + 1, n=total))
            count = min(total - start, 150)
            story = build(start, count)
            story.place(area)
            fitted = set()

            def cb(p):
                if p.id and p.id.startswith("r") and (p.open_close & 2) and p.rect[3] <= area.y1 + 0.5:
                    fitted.add(int(p.id[1:]))
            story.element_positions(cb)
            k = 0
            while start + k in fitted:
                k += 1
            if 0 < k < count:
                story = build(start, k)
                story.place(area)
            elif k == 0 and count > 0:
                k = 1      # riga più alta di una pagina: viene troncata
                story = build(start, 1)
                story.place(area)
            dev = writer.begin_page(pymupdf.Rect(0, 0, pw, ph))
            story.draw(dev)
            writer.end_page()
            start += k
            if start >= total:
                break
        writer.close()
        pdf = pymupdf.open("pdf", buf.getvalue())
        _decorate(pdf, Path(str(path_or_text)).name if is_path else "", m)
        try:
            pdf.subset_fonts()
        except Exception:
            pass
        out = _save(pdf)
        pdf.close()
    return out


# ======================================================================== Office
def soffice_path() -> str | None:
    """Percorso dell'eseguibile di LibreOffice, se installato."""
    for name in ("soffice", "libreoffice", "soffice.exe"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.isfile(p):
            return p
    return None


_INSTALL_HINT = ("LibreOffice non è installato: serve per convertire i documenti Office in PDF in locale. "
                 "Installarlo, ad esempio con «sudo apt install libreoffice-writer libreoffice-calc "
                 "libreoffice-impress», poi riprovare.")


def _office_encrypted(path: str) -> bool:
    """Riconosce i documenti Office/ODF cifrati con password."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        ext = _ext(path)
        if head.startswith(b"\xd0\xcf\x11\xe0") and ext in (".docx", ".xlsx", ".pptx", ".docm", ".xlsm",
                                                              ".pptm", ".ppsx", ".dotx"):
            return True     # OOXML cifrato: contenitore OLE con EncryptionInfo
        if head.startswith(b"PK"):
            import zipfile
            with zipfile.ZipFile(path) as z:
                if "META-INF/manifest.xml" in z.namelist():
                    return b"encryption-data" in z.read("META-INF/manifest.xml")
        if head.startswith(b"\xd0\xcf\x11\xe0") and ext in (".doc", ".xls", ".ppt"):
            data = Path(path).read_bytes()[:2_000_000]
            # flag fEncrypted di Word / FilePass di Excel / EncryptedSummary di PowerPoint
            if ext == ".ppt":
                return "EncryptedSummary".encode("utf-16-le") in data
            if ext == ".doc":
                idx = data.find(b"\xec\xa5")
                if idx >= 0 and idx + 12 <= len(data):
                    return bool(data[idx + 11] & 0x01)
    except Exception:
        return False
    return False


def _decrypt_office(path: str, password: str, tmpdir: str) -> str:
    try:
        import msoffcrypto  # facoltativo
    except ImportError as e:
        raise ConversionError(_("Il documento è protetto da password: per convertirlo installare il pacchetto "
                                "Python «msoffcrypto-tool» oppure rimuovere la password con l'applicazione "
                                "Office.")) from e
    out = os.path.join(tmpdir, "decifrato" + _ext(path))
    try:
        with open(path, "rb") as f, open(out, "wb") as g:
            office = msoffcrypto.OfficeFile(f)
            office.load_key(password=password)
            office.decrypt(g)
    except Exception as e:
        raise WrongPassword() from e
    return out


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------- opzioni Office (CNV-18)
OFFICE_DEFAULTS = {
    "bookmarks": True,          # segnalibri dai titoli
    "tagged": False,            # PDF taggato (accessibile)
    "comments": False,          # commenti come annotazioni
    "image_quality": 90,        # qualità JPEG delle immagini (1-100)
    "max_image_dpi": 0,         # 0 = risoluzione originale
    "pdfa": "",                 # "", "1b", "2b", "3b"
    "sheets": "all",            # fogli di calcolo: all | active
    "fit_width": False,         # fogli di calcolo: adatta le colonne alla larghezza della pagina
    "notes_pages": False,       # presentazioni: aggiunge le pagine delle note
    "hidden_slides": False,     # presentazioni: include le diapositive nascoste
    "final_state": True,        # revisioni tracciate: stato finale senza marcature
}
_PDFA_VERSION = {"1b": 1, "2b": 2, "3b": 3}
SHEET_PAGE_LIMIT = 500
_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_BEFORE_PAGESETUP = ("sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
                     "sheetProtection", "protectedRanges", "scenarios", "autoFilter", "sortState",
                     "dataConsolidate", "customSheetViews", "mergeCells", "phoneticPr", "conditionalFormatting",
                     "dataValidations", "hyperlinks", "printOptions", "pageMargins")


def _filter_options(options: dict) -> str:
    import json
    o = {**OFFICE_DEFAULTS, **(options or {})}

    def b(v):
        return {"type": "boolean", "value": "true" if v else "false"}
    data = {"ExportBookmarks": b(o["bookmarks"]), "ExportNotes": b(o["comments"]),
            "ExportNotesPages": b(o["notes_pages"]), "ExportHiddenSlides": b(o["hidden_slides"]),
            "UseTaggedPDF": b(o["tagged"]),
            "Quality": {"type": "long", "value": str(max(1, min(100, int(o["image_quality"]))))}}
    if int(o.get("max_image_dpi") or 0) > 0:
        data["ReduceImageResolution"] = b(True)
        data["MaxImageResolution"] = {"type": "long", "value": str(int(o["max_image_dpi"]))}
    if o.get("pdfa") in _PDFA_VERSION:
        data["SelectPdfVersion"] = {"type": "long", "value": str(_PDFA_VERSION[o["pdfa"]])}
    return "pdf:writer_pdf_Export:" + json.dumps(data)


def _rewrite_zip(src: str, dst: str, fixers: dict) -> None:
    """Copia un pacchetto OOXML applicando `fixers[nome](byte) -> byte` alle parti indicate
    (chiavi: nome esatto o prefisso terminato da «*»)."""
    import zipfile
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            for key, fn in fixers.items():
                if item.filename == key or (key.endswith("*") and item.filename.startswith(key[:-1])
                                            and item.filename.endswith(".xml")):
                    data = fn(data)
            zout.writestr(item, data)


def _xml(data: bytes):
    from lxml import etree
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))


def _xml_bytes(root) -> bytes:
    from lxml import etree
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _fit_sheet_width(data: bytes) -> bytes:
    """Foglio adattato a una pagina in larghezza (altezza libera)."""
    from lxml import etree
    root = _xml(data)
    q = lambda n: f"{{{_SS}}}{n}"   # noqa: E731
    sp = root.find(q("sheetPr"))
    if sp is None:
        sp = etree.Element(q("sheetPr"))
        root.insert(0, sp)
    psp = sp.find(q("pageSetUpPr"))
    if psp is None:
        psp = etree.SubElement(sp, q("pageSetUpPr"))
    psp.set("fitToPage", "1")
    ps = root.find(q("pageSetup"))
    if ps is None:
        ps = etree.Element(q("pageSetup"))
        idx = 0
        for i, ch in enumerate(root):
            if isinstance(ch.tag, str) and etree.QName(ch).localname in _BEFORE_PAGESETUP:
                idx = i + 1
        root.insert(idx, ps)
    ps.set("fitToWidth", "1")
    ps.set("fitToHeight", "0")
    return _xml_bytes(root)


def _only_active_sheet(data: bytes) -> bytes:
    """Nasconde tutti i fogli tranne quello attivo (i fogli nascosti non vengono stampati)."""
    root = _xml(data)
    q = lambda n: f"{{{_SS}}}{n}"   # noqa: E731
    view = root.find(f"{q('bookViews')}/{q('workbookView')}")
    active = int(view.get("activeTab", "0")) if view is not None else 0
    sheets = root.findall(f"{q('sheets')}/{q('sheet')}")
    for i, sh in enumerate(sheets):
        if i == active:
            sh.attrib.pop("state", None)
        else:
            sh.set("state", "hidden")
    return _xml_bytes(root)


def _final_revisions(data: bytes) -> bytes:
    """Revisioni tracciate mostrate nello stato finale, senza marcature."""
    from lxml import etree
    root = _xml(data)
    q = lambda n: f"{{{_WML}}}{n}"   # noqa: E731
    rv = root.find(q("revisionView"))
    if rv is None:
        rv = etree.Element(q("revisionView"))
        root.insert(0, rv)
    for attr in ("markup", "insDel", "formatting", "comments"):
        rv.set(q(attr), "0")
    return _xml_bytes(root)


def _prepare_office(src: str, tmp: str, options: dict) -> str:
    """Applica al documento (in una copia) le opzioni che LibreOffice legge dal file stesso."""
    o = {**OFFICE_DEFAULTS, **(options or {})}
    ext = _ext(src)
    fixers: dict = {}
    if ext in (".xlsx", ".xlsm"):
        if o["fit_width"]:
            fixers["xl/worksheets/*"] = _fit_sheet_width
        if o["sheets"] == "active":
            fixers["xl/workbook.xml"] = _only_active_sheet
    elif ext in (".docx", ".docm") and o["final_state"]:
        fixers["word/settings.xml"] = _final_revisions
    if not fixers:
        return src
    out = os.path.join(tmp, "preparato" + ext)
    try:
        _rewrite_zip(src, out, fixers)
    except Exception:
        return src     # pacchetto non standard: si converte così com'è
    return out


def office_fonts(path) -> list[str]:
    """Font citati da un documento OOXML (DOCX, XLSX, PPTX)."""
    import zipfile
    names: set[str] = set()
    try:
        with zipfile.ZipFile(str(path)) as z:
            for part in z.namelist():
                if not part.endswith(".xml"):
                    continue
                if part == "word/fontTable.xml":
                    names.update(re.findall(rb'<w:font w:name="([^"]+)"', z.read(part)))
                elif part.startswith("word/") and part.endswith(("document.xml", "styles.xml")):
                    names.update(re.findall(rb'w:ascii="([^"]+)"', z.read(part)))
                elif part == "xl/styles.xml":
                    names.update(re.findall(rb'<name val="([^"]+)"', z.read(part)))
                elif part.startswith(("ppt/slides/", "ppt/theme/", "ppt/slideMasters/")):
                    names.update(re.findall(rb'<a:latin typeface="([^"+][^"]*)"', z.read(part)))
    except Exception:
        return []
    return sorted({n.decode("utf-8", "replace") for n in names if n.strip()})


@functools.lru_cache(maxsize=1)
def _installed_families() -> frozenset:
    exe = shutil.which("fc-list")
    if not exe:
        return frozenset()
    try:
        out = subprocess.run([exe, ":", "family"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    fams = set()
    for line in out.splitlines():
        for f in line.split(","):
            if f.strip():
                fams.add(f.strip().lower())
    return frozenset(fams)


def missing_fonts(path) -> list[str]:
    """Font del documento non installati (LibreOffice userà sostituti metricamente compatibili)."""
    installed = _installed_families()
    if not installed:
        return []
    return [f for f in office_fonts(path) if f.lower() not in installed]


def estimate_sheet_pages(path) -> int:
    """Stima delle pagine di un foglio di calcolo XLSX (dalle dimensioni dei fogli): circa 50
    righe e 10 colonne per pagina. 0 se non stimabile."""
    import zipfile
    total = 0
    try:
        with zipfile.ZipFile(str(path)) as z:
            for part in z.namelist():
                if part.startswith("xl/worksheets/sheet") and part.endswith(".xml"):
                    with z.open(part) as f:
                        head = f.read(4096)
                    m = re.search(rb'<dimension ref="[A-Z]+\d+:([A-Z]+)(\d+)"', head)
                    if not m:
                        continue
                    col = 0
                    for ch in m.group(1).decode():
                        col = col * 26 + (ord(ch) - 64)
                    rows = int(m.group(2))
                    total += math.ceil(rows / 50) * math.ceil(col / 10)
    except Exception:
        return 0
    return total


def office_to_pdf(path, *, password: str | None = None, timeout: float = 180, progress=None,
                  options: dict | None = None, warnings: list | None = None) -> bytes:
    """Converte un documento Office/ODF/RTF/Visio in PDF con LibreOffice in modalità headless.

    Usa un profilo isolato (funziona anche con un'altra istanza aperta); le macro non vengono
    eseguite. L'annullamento termina il processo. XPS/OXPS sono convertiti direttamente.
    `options` (vedi OFFICE_DEFAULTS): segnalibri, PDF taggato, commenti, qualità e risoluzione
    delle immagini, PDF/A, fogli (tutti o attivo, adatta alla larghezza), note e diapositive
    nascoste, revisioni nello stato finale. Gli avvisi (font sostituiti) vanno in `warnings`.
    """
    path = str(path)
    if not os.path.isfile(path):
        raise ConversionError(_("Il file «{n}» non esiste.").format(n=Path(path).name))
    if _ext(path) in MUPDF_DOC_EXTENSIONS:
        return _mupdf_doc_to_pdf(path)
    exe = soffice_path()
    if not exe:
        raise ConversionError(_(_INSTALL_HINT))
    with tempfile.TemporaryDirectory(prefix="foliant-office-") as tmp:
        src = path
        if _office_encrypted(path):
            if not password:
                raise PasswordRequired()
            src = _decrypt_office(path, password, tmp)
        if warnings is not None:
            gone = missing_fonts(src)
            if gone:
                warnings.append(_("Font non installati, sostituiti con equivalenti (l'impaginazione può "
                                  "cambiare): {f}").format(f=", ".join(gone[:8]) + (" …" if len(gone) > 8 else "")))
        src = _prepare_office(src, tmp, options or {})
        # nome sorgente sicuro (LibreOffice non gradisce alcuni caratteri)
        safe = os.path.join(tmp, "in", "documento" + _ext(src))
        os.makedirs(os.path.dirname(safe))
        shutil.copyfile(src, safe)
        outdir = os.path.join(tmp, "out")
        os.makedirs(outdir)
        profile = Path(tmp, "profile").resolve().as_uri()
        filt = _filter_options(options or {})
        cmd = [exe, f"-env:UserInstallation={profile}", "--headless", "--invisible", "--nologo",
               "--nodefault", "--nofirststartwizard", "--nolockcheck", "--norestore",
               "--convert-to", filt, "--outdir", outdir, safe]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["SAL_USE_VCLPLUGIN"] = "svp"
        check(progress, 0.05, _("Avvio di LibreOffice…"))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    env=env, cwd=tmp, start_new_session=(os.name == "posix"))
        except OSError as e:
            raise ConversionError(_("Impossibile avviare LibreOffice: {e}").format(e=e)) from e
        start = time.monotonic()
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    _kill(proc)
                    raise ConversionError(_("La conversione ha superato il tempo massimo ({s} s).").format(
                        s=int(timeout)))
                check(progress, min(0.9, 0.1 + 0.8 * elapsed / max(timeout / 4, 1)),
                      _("Conversione con LibreOffice…"))
                time.sleep(0.1)
        except BaseException:
            if proc.poll() is None:
                _kill(proc)
            raise
        _out, err = proc.communicate()
        result = os.path.join(outdir, "documento.pdf")
        if not os.path.isfile(result) or os.path.getsize(result) == 0:
            msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
            detail = msg[-1] if msg else ""
            raise ConversionError(_("LibreOffice non è riuscito a convertire «{n}»: file danneggiato, protetto o "
                                    "formato non supportato.{d}").format(
                n=Path(path).name, d=(" (" + detail + ")") if detail else ""))
        data = Path(result).read_bytes()
    check(progress, 0.95, _("Verifica del PDF…"))
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open("pdf", data)
            if pdf.page_count == 0:
                raise ConversionError(_("Il documento convertito non contiene pagine."))
            pdf.close()
        except ConversionError:
            raise
        except Exception as e:
            raise ConversionError(_("LibreOffice ha prodotto un PDF non valido.")) from e
    return data


def rtf_to_pdf(path, *, progress=None, **kw) -> bytes:
    """RTF → PDF tramite LibreOffice (stili, tabelle e immagini conservati)."""
    return office_to_pdf(path, progress=progress, **kw)


def _mupdf_doc_to_pdf(path: str) -> bytes:
    with ENGINE_LOCK:
        try:
            src = pymupdf.open(path)
            data = src.convert_to_pdf()
            toc = src.get_toc()
            src.close()
            pdf = pymupdf.open("pdf", data)
            if toc:
                try:
                    pdf.set_toc(toc)
                except Exception:
                    pass
            out = _save(pdf)
            pdf.close()
            return out
        except Exception as e:
            raise ConversionError(_("Impossibile convertire «{n}»: file danneggiato o non supportato.").format(
                n=Path(path).name)) from e


# ============================================================ dispatch e lotti
def file_to_pdf(path, progress=None, **opts) -> tuple[bytes, list[str]]:
    """Converte un file qualsiasi supportato in PDF scegliendo il convertitore dall'estensione.

    Restituisce (byte del PDF, avvisi). I PDF sono restituiti così come sono.
    """
    path = str(path)
    ext = _ext(path)
    name = Path(path).name
    if not os.path.isfile(path):
        raise ConversionError(_("Il file «{n}» non esiste.").format(n=name))
    warnings: list[str] = []
    if ext == ".pdf":
        data = Path(path).read_bytes()
        with ENGINE_LOCK:
            try:
                pymupdf.open("pdf", data).close()
            except Exception as e:
                raise ConversionError(_("«{n}» non è un PDF valido.").format(n=name)) from e
        return data, warnings
    if ext in IMAGE_EXTENSIONS:
        keys = ("page_size", "orientation", "fit", "margin_pt", "quality", "max_ppi", "jpeg_quality",
                "rotations", "all_frames", "gif_all_frames")
        data, warnings = images_to_pdf([path], progress=progress, **{k: v for k, v in opts.items() if k in keys})
        return data, warnings
    # «paper»: formato carta locale (A4 o Letter) per i documenti che non ne hanno uno proprio
    if opts.get("paper") and "page_size" not in opts and ext not in IMAGE_EXTENSIONS:
        opts = dict(opts, page_size=opts["paper"])
    if ext in CSV_EXTENSIONS:
        keys = ("delimiter", "page_size", "orientation")
        return csv_to_pdf(path, progress=progress, **{k: v for k, v in opts.items() if k in keys}), warnings
    if ext in MARKDOWN_EXTENSIONS:
        text, _enc = read_text_file(path)
        return markdown_to_pdf(text, base_dir=str(Path(path).parent), progress=progress,
                               page_size=opts.get("page_size", "A4")), warnings
    if ext in TEXT_EXTENSIONS:
        text, _enc = read_text_file(path)
        keys = ("page_size", "margin_mm", "font_size", "monospace", "line_numbers", "header_footer", "tab_size")
        return text_to_pdf(text, title=name, progress=progress,
                           **{k: v for k, v in opts.items() if k in keys}), warnings
    if ext in HTML_EXTENSIONS:
        data = Path(path).read_bytes()
        text, _enc = _decode_bytes(data)
        keys = ("page_size", "margin_mm", "single_page", "bookmarks", "orientation")
        return html_to_pdf(text, base_dir=str(Path(path).parent), progress=progress,
                           **{k: v for k, v in opts.items() if k in keys}), warnings
    if ext in MUPDF_DOC_EXTENSIONS:
        return _mupdf_doc_to_pdf(path), warnings
    if ext in OFFICE_EXTENSIONS:
        office = {k: opts[k] for k in OFFICE_DEFAULTS if k in opts}
        return office_to_pdf(path, password=opts.get("password"), progress=progress, options=office,
                             warnings=warnings), warnings
    raise ConversionError(_("Formato «{e}» non supportato.").format(e=ext or name))


class _SubProgress:
    """Mappa l'avanzamento di un sottocompito su un intervallo del compito principale."""

    def __init__(self, progress, a: float, b: float, prefix: str = ""):
        self.progress, self.a, self.b, self.prefix = progress, a, b, prefix

    def update(self, fraction=None, text=None):
        if self.progress is None:
            return
        f = None if fraction is None else self.a + (self.b - self.a) * max(0.0, min(1.0, fraction))
        self.progress.update(f, f"{self.prefix}{text}" if text and self.prefix else (text or self.prefix))


def batch_create(paths, *, mode: str = "separate", output_dir=None, output_path=None,
                 bookmarks_from_names: bool = True, policy: str = "rename", progress=None, **opts) -> dict:
    """Crea PDF da molti file (CNV-28).

    - mode "separate": un PDF per file, stesso nome base, in `output_dir` (o nella cartella del
      sorgente); scrittura atomica.
    - mode "single": un unico PDF nell'ordine dell'elenco, con un segnalibro di primo livello per
      file se `bookmarks_from_names`; salvato in `output_path` se indicato.

    Restituisce {"created": [percorsi], "skipped": [(percorso, motivo)], "output": percorso o None,
    "pdf"/"data": byte del PDF unico (solo "single"), "warnings": [...]}.
    """
    paths = [str(p) for p in paths]
    report: dict = {"created": [], "skipped": [], "output": None, "pdf": None, "data": None, "warnings": []}
    total = max(1, len(paths))
    if mode not in ("separate", "single"):
        raise ConversionError(_("Modalità «{m}» non valida.").format(m=mode))
    if mode == "separate":
        with OutputFiles(policy) as out:
            for i, path in enumerate(paths):
                name = Path(path).name
                check(progress, i / total, _("File {i} di {n}: {f}").format(i=i + 1, n=total, f=name))
                if _ext(path) not in SUPPORTED_EXTENSIONS:
                    report["skipped"].append((path, _("formato non supportato")))
                    continue
                try:
                    data, warns = file_to_pdf(path, progress=_SubProgress(progress, i / total, (i + 1) / total,
                                                                          f"{name}: "), **opts)
                except (ConversionError, PasswordRequired, WrongPassword) as e:
                    report["skipped"].append((path, str(e) or type(e).__name__))
                    continue
                except Exception as e:
                    if type(e).__name__ == "OperationCancelled":
                        raise
                    report["skipped"].append((path, _("errore imprevisto: {e}").format(e=e)))
                    continue
                report["warnings"] += [f"{name}: {w}" for w in warns]
                folder = Path(output_dir) if output_dir else Path(path).parent
                tmp = out.new(folder / (Path(path).stem + ".pdf"))
                Path(tmp).write_bytes(data)
            report["created"] = out.commit()
        check(progress, 1.0, _("Completato"))
        return report

    with ENGINE_LOCK:
        combined = pymupdf.open()
    toc = []
    for i, path in enumerate(paths):
        name = Path(path).name
        check(progress, i / total, _("File {i} di {n}: {f}").format(i=i + 1, n=total, f=name))
        if _ext(path) not in SUPPORTED_EXTENSIONS:
            report["skipped"].append((path, _("formato non supportato")))
            continue
        try:
            data, warns = file_to_pdf(path, progress=_SubProgress(progress, i / total, (i + 1) / total,
                                                                  f"{name}: "), **opts)
            with ENGINE_LOCK:
                src = pymupdf.open("pdf", data)
                if src.needs_pass:
                    src.close()
                    raise ConversionError(_("PDF protetto da password"))
                first = combined.page_count
                combined.insert_pdf(src)
                src.close()
        except (ConversionError, PasswordRequired, WrongPassword) as e:
            report["skipped"].append((path, str(e) or type(e).__name__))
            continue
        except Exception as e:
            if type(e).__name__ == "OperationCancelled":
                raise
            report["skipped"].append((path, _("errore imprevisto: {e}").format(e=e)))
            continue
        report["warnings"] += [f"{name}: {w}" for w in warns]
        report["created"].append(path)
        toc.append([1, Path(path).stem, first + 1])
    with ENGINE_LOCK:
        if combined.page_count == 0:
            combined.close()
            raise ConversionError(_("Nessuno dei file selezionati è stato convertito."))
        if bookmarks_from_names:
            combined.set_toc(toc)
        data = _save(combined)
        combined.close()
    report["pdf"] = report["data"] = data
    if output_path:
        with OutputFiles(policy) as out:
            tmp = out.new(output_path)
            Path(tmp).write_bytes(data)
            report["output"] = out.commit()[0]
    check(progress, 1.0, _("Completato"))
    return report


# ======================================================================= appunti
def clipboard_to_pdf(*, pdf_bytes: bytes | None = None, image_bytes: bytes | None = None, html: str | None = None,
                     text: str | None = None, file_paths=None, progress=None, page_size: str = "A4") -> bytes:
    """Crea un PDF dall'istantanea degli appunti.

    Priorità: file copiati / pagine PDF > immagine > testo formattato (HTML) > testo semplice.
    L'immagine diventa una pagina delle sue dimensioni (1 px = 1 pt), senza perdita.
    """
    if file_paths:
        paths = [str(p) for p in file_paths if os.path.isfile(str(p))]
        if len(paths) == 1:
            return file_to_pdf(paths[0], progress=progress, paper=page_size)[0]
        if paths:
            return batch_create(paths, mode="single", progress=progress, paper=page_size)["pdf"]
    if pdf_bytes:
        with ENGINE_LOCK:
            try:
                pymupdf.open("pdf", pdf_bytes).close()
            except Exception as e:
                raise ConversionError(_("Le pagine negli appunti non sono un PDF valido.")) from e
        return bytes(pdf_bytes)
    if image_bytes:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(image_bytes))
            im.load()
        except Exception as e:
            raise ConversionError(_("L'immagine negli appunti non è leggibile.")) from e
        data = bytes(image_bytes) if im.format in ("PNG", "JPEG") else _frame_to_png(im)
        with ENGINE_LOCK:
            pdf = pymupdf.open()
            page = pdf.new_page(width=max(im.width, MIN_SIDE_PT), height=max(im.height, MIN_SIDE_PT))
            page.insert_image(pymupdf.Rect(0, 0, im.width, im.height), stream=data, keep_proportion=False)
            out = pdf.tobytes(garbage=3, deflate=True)
            pdf.close()
        return out
    if html and html.strip():
        frag = re.search(r"<!--StartFragment-->(.*)<!--EndFragment-->", html, re.S)
        body = frag.group(1) if frag else html
        return html_to_pdf(body, progress=progress, page_size=page_size)
    if text and text.strip():
        return text_to_pdf(text, monospace=False, header_footer=False, progress=progress, page_size=page_size)
    raise ConversionError(_("Gli appunti non contengono dati utilizzabili per creare un PDF."))


__all__ = [
    "PAGE_SIZES", "IMAGE_EXTENSIONS", "TEXT_EXTENSIONS", "HTML_EXTENSIONS", "OFFICE_EXTENSIONS",
    "SUPPORTED_EXTENSIONS", "natural_sort_key", "sort_images", "images_to_pdf", "blank_pdf", "size_from_units",
    "effective_page_size", "text_to_pdf", "read_text_file", "markdown_to_html", "markdown_to_pdf",
    "html_to_pdf", "url_to_html", "csv_to_pdf", "rtf_to_pdf", "soffice_path", "office_to_pdf", "file_to_pdf",
    "clipboard_to_pdf", "batch_create",
]
