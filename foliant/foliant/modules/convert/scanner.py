"""Acquisizione da scanner in PDF (CNV-24), senza interfaccia.

Il dispositivo è astratto da un «backend»: `SaneBackend` usa il programma
`scanimage` (SANE, scanner locali e di rete, anche senza driver dedicati via
eSCL/AirScan); i test usano un backend simulato con la stessa interfaccia:

    list_devices() -> list[tuple[str, str]]          # (id, descrizione)
    scan(device, settings, progress) -> Iterator[PIL.Image]
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Iterator

import pymupdf

from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, _

from .common import check

MM = 72 / 25.4
ID_CARD_MM = (85.6, 54.0)
A4 = (595.28, 841.89)
PAPER_MM = {"A4": (210.0, 297.0), "A5": (148.0, 210.0), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}
# oltre questa memoria le pagine acquisite passano su disco (600 dpi a colori: ~100 MB a pagina)
MEMORY_BUDGET = 256 * 1024 * 1024


class ScannerError(FoliantError):
    """Errore del dispositivo (scollegato, inceppato, alimentatore vuoto…)."""


@dataclass
class ScanSettings:
    source: str = "flatbed"         # flatbed | adf | adf-duplex
    dpi: int = 300
    mode: str = "color"             # color | gray | bw
    brightness: int = 0             # -100..100
    contrast: int = 0               # -100..100
    paper: str | None = None        # "A4", "Letter"… o None = automatico
    jpeg_quality: int = 85
    lossless: bool = False

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class StoredPage:
    """Pagina acquisita conservata su disco (PNG senza perdita) per le acquisizioni grandi.
    La cartella temporanea viene eliminata quando non resta più alcuna pagina che la usa."""

    def __init__(self, folder, path: str, img):
        self._folder = folder           # TemporaryDirectory condivisa
        self.path = path
        self.size = img.size
        self.width, self.height = img.size
        self.mode = img.mode
        self.info = {"dpi": img.info.get("dpi")} if img.info.get("dpi") else {}

    def load(self):
        from PIL import Image
        with Image.open(self.path) as im:
            img = im.copy()
        if self.info.get("dpi"):
            img.info["dpi"] = self.info["dpi"]
        return img


def load_page(page):
    """Immagine Pillow di una pagina acquisita (in memoria o su disco)."""
    return page.load() if isinstance(page, StoredPage) else page


def _raw_size(img) -> int:
    bands = {"1": 0.125, "L": 1, "P": 1, "RGB": 3, "RGBA": 4}.get(img.mode, 4)
    return int(img.width * img.height * bands)


@dataclass
class ScanResult:
    pages: list = field(default_factory=list)          # immagini Pillow
    error: str | None = None
    removed_blank: int = 0


# ------------------------------------------------------------------ backend SANE
class SaneBackend:
    """Scanner tramite `scanimage` (pacchetto sane-utils)."""

    def __init__(self, program: str | None = None):
        self.program = program or shutil.which("scanimage")

    @property
    def available(self) -> bool:
        return self.program is not None

    def list_devices(self) -> list[tuple[str, str]]:
        if not self.program:
            return []
        try:
            out = subprocess.run([self.program, "-f", "%d\t%v %m (%t)%n"], capture_output=True, text=True,
                                 timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        devices = []
        for line in out.splitlines():
            if "\t" in line:
                dev, label = line.split("\t", 1)
                devices.append((dev.strip(), label.strip()))
        return devices

    def scan(self, device: str, settings: ScanSettings, progress=None) -> Iterator:
        from PIL import Image
        if not self.program:
            raise ScannerError(_("Il programma «scanimage» non è installato (sudo apt install sane-utils)."))
        mode = {"color": "Color", "gray": "Gray", "bw": "Lineart"}.get(settings.mode, "Color")
        args = [self.program, "-d", device, "--format=png", f"--resolution={settings.dpi}", f"--mode={mode}"]
        if settings.paper in PAPER_MM:
            w, h = PAPER_MM[settings.paper]
            args += ["-x", f"{w:g}", "-y", f"{h:g}"]
        if settings.source in ("adf", "adf-duplex"):
            args.append("--source=" + ("ADF Duplex" if settings.source == "adf-duplex" else "ADF"))
        with tempfile.TemporaryDirectory(prefix="foliant-scan-") as tmp:
            if settings.source == "flatbed":
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                data, err = self._wait(proc, progress)
                if proc.returncode != 0 or not data:
                    raise ScannerError(_("Acquisizione non riuscita: {e}").format(
                        e=err.decode(errors="replace").strip() or _("nessuna risposta dallo scanner")))
                yield Image.open(io.BytesIO(data))
                return
            pattern = os.path.join(tmp, "p%04d.png")
            proc = subprocess.Popen(args + [f"--batch={pattern}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _out, err = self._wait(proc, progress)
            files = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
            for f in files:
                with Image.open(os.path.join(tmp, f)) as im:
                    yield im.copy()
            message = err.decode(errors="replace").strip()
            if not files:
                if "empty" in message.lower() or "no docs" in message.lower():
                    raise ScannerError(_("L'alimentatore è vuoto: inserisci i fogli e riprova."))
                raise ScannerError(_("Acquisizione non riuscita: {e}").format(e=message or "?"))
            if proc.returncode not in (0, 7) and "no docs" not in message.lower():
                raise ScannerError(_("Lo scanner si è interrotto: {e}").format(e=message or "?"))

    @staticmethod
    def _wait(proc, progress):
        import time
        start = time.time()
        while True:
            try:
                return proc.communicate(timeout=0.3)
            except subprocess.TimeoutExpired:
                try:
                    check(progress, min(0.9, (time.time() - start) / 120), _("Acquisizione in corso…"))
                except OperationCancelled:
                    proc.kill()
                    proc.communicate()
                    raise


# ------------------------------------------------------------------ acquisizione
def acquire(backend, device: str, settings: ScanSettings, progress=None, *, remove_blank: bool = False,
            blank_threshold: float = 0.005, straighten: bool = False, crop: bool = False,
            memory_budget: int | None = None) -> ScanResult:
    """Acquisisce le pagine; in caso di errore del dispositivo conserva quelle già acquisite.

    Oltre `memory_budget` byte di immagini in memoria le pagine successive vengono salvate su
    disco (`StoredPage`): centinaia di pagine a 600 dpi non esauriscono la memoria."""
    result = ScanResult()
    budget = MEMORY_BUDGET if memory_budget is None else memory_budget
    in_memory = 0
    folder = None
    try:
        for k, img in enumerate(backend.scan(device, settings, progress)):
            check(progress, None, _("Pagina {n} acquisita").format(n=k + 1))
            if crop:
                from .imaging import autocrop
                img = autocrop(img)
            if straighten:
                from .imaging import deskew
                img, _angle = deskew(img)
            img = adjust(img, settings)
            if remove_blank and is_blank(img, blank_threshold):
                result.removed_blank += 1
                continue
            size = _raw_size(img)
            if in_memory + size > budget:
                if folder is None:
                    folder = tempfile.TemporaryDirectory(prefix="foliant-scan-")
                path = os.path.join(folder.name, f"p{len(result.pages):05d}.png")
                img.save(path, format="PNG", compress_level=1)
                result.pages.append(StoredPage(folder, path, img))
                continue
            in_memory += size
            result.pages.append(img)
    except OperationCancelled:
        raise
    except ScannerError as e:
        result.error = str(e)
    except Exception as e:   # dispositivo scollegato o risposta non valida
        result.error = _("Errore dello scanner: {e}").format(e=e)
    return result


def preview(backend, device: str, settings: ScanSettings, progress=None):
    """Anteprima rapida del piano (75 dpi) con le regolazioni scelte; None se non riesce."""
    import copy
    quick = copy.copy(settings)
    quick.dpi = 75
    quick.source = "flatbed"
    try:
        for img in backend.scan(device, quick, progress):
            return adjust(img, quick)
    except OperationCancelled:
        raise
    except Exception as e:
        raise ScannerError(_("Anteprima non riuscita: {e}").format(e=e)) from e
    return None


def adjust(img, settings: ScanSettings):
    """Luminosità, contrasto e modalità colore."""
    from PIL import ImageEnhance
    if settings.brightness:
        img = ImageEnhance.Brightness(img.convert("RGB") if img.mode not in ("L", "RGB") else img).enhance(
            1 + settings.brightness / 100)
    if settings.contrast:
        img = ImageEnhance.Contrast(img.convert("RGB") if img.mode not in ("L", "RGB") else img).enhance(
            1 + settings.contrast / 100)
    if settings.mode == "bw" and img.mode != "1":
        img = img.convert("L").point(lambda v: 255 if v > 140 else 0).convert("1")
    elif settings.mode == "gray" and img.mode not in ("L", "1"):
        img = img.convert("L")
    elif settings.mode == "color" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.info["dpi"] = (settings.dpi, settings.dpi)
    return img


def is_blank(img, threshold: float = 0.005) -> bool:
    """Pagina bianca: quota di pixel scuri sotto la soglia (bordi esclusi)."""
    g = img.convert("L")
    w, h = g.size
    m = int(min(w, h) * 0.05)
    g = g.crop((m, m, w - m, h - m)).resize((max(1, (w - 2 * m) // 4), max(1, (h - 2 * m) // 4)))
    hist = g.histogram()
    dark = sum(hist[:160])
    return dark / max(1, g.width * g.height) < threshold


def merge_duplex(fronts: list, backs_reversed: list) -> list:
    """Fronte/retro manuale: la pila dei retro è acquisita in ordine inverso."""
    backs = list(reversed(backs_reversed))
    out = []
    for k in range(max(len(fronts), len(backs))):
        if k < len(fronts):
            out.append(fronts[k])
        if k < len(backs):
            out.append(backs[k])
    return out


# ------------------------------------------------------------------ PDF
def _g4_stream(img) -> tuple[bytes, int, int]:
    """Dati CCITT G4 (senza perdita) di un'immagine a 1 bit."""
    from PIL import Image, TiffImagePlugin
    buf = io.BytesIO()
    TiffImagePlugin.WRITE_LIBTIFF = True
    try:
        img.convert("1").save(buf, format="TIFF", compression="group4", tiffinfo={278: img.height})
    finally:
        TiffImagePlugin.WRITE_LIBTIFF = False
    buf.seek(0)
    tif = Image.open(buf)
    offsets = tif.tag_v2[273]
    counts = tif.tag_v2[279]
    raw = buf.getvalue()
    data = b"".join(raw[o:o + c] for o, c in zip(offsets, counts))
    return data, img.width, img.height


def _insert_bilevel(doc: pymupdf.Document, page: pymupdf.Page, rect: pymupdf.Rect, img) -> None:
    data, w, h = _g4_stream(img)
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /BitsPerComponent 1 "
                            f"/ColorSpace /DeviceGray /Filter /CCITTFaxDecode "
                            f"/DecodeParms << /K -1 /Columns {w} /Rows {h} /BlackIs1 false >> >>")
    doc.update_stream(xref, data, new=True, compress=False)
    doc.xref_set_key(xref, "Filter", "/CCITTFaxDecode")
    doc.xref_set_key(xref, "DecodeParms", f"<< /K -1 /Columns {w} /Rows {h} /BlackIs1 false >>")
    page.insert_image(rect, xref=xref)


def _insert_image(doc, page, rect, img, settings: ScanSettings) -> None:
    if img.mode == "1" or settings.mode == "bw":
        _insert_bilevel(doc, page, rect, img)
        return
    buf = io.BytesIO()
    if settings.lossless:
        img.save(buf, format="PNG")
    else:
        img.convert("L" if img.mode == "L" else "RGB").save(buf, format="JPEG", quality=settings.jpeg_quality,
                                                             dpi=(settings.dpi, settings.dpi))
    page.insert_image(rect, stream=buf.getvalue())


def pages_to_pdf(images: list, settings: ScanSettings, progress=None, *, into: pymupdf.Document | None = None,
                 at: int | None = None) -> bytes:
    """PDF con una pagina per immagine, dimensione coerente con la risoluzione."""
    if not images:
        raise FoliantError(_("Nessuna pagina acquisita."))
    with ENGINE_LOCK:
        doc = pymupdf.open()
        for k, img in enumerate(images):
            check(progress, 0.9 + 0.1 * k / len(images), _("Creazione del PDF…"))
            img = load_page(img)
            dpi = (img.info.get("dpi") or (settings.dpi, settings.dpi))[0] or settings.dpi
            w, h = img.width * 72 / dpi, img.height * 72 / dpi
            page = doc.new_page(width=w, height=h)
            _insert_image(doc, page, page.rect, img, settings)
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return data


def id_card_pdf(front, back, settings: ScanSettings) -> bytes:
    """Documento d'identità: fronte sopra e retro sotto, in scala reale, su una pagina A4."""
    from PIL import Image
    with ENGINE_LOCK:
        doc = pymupdf.open()
        page = doc.new_page(width=A4[0], height=A4[1])
        cw, ch = ID_CARD_MM[0] * MM, ID_CARD_MM[1] * MM
        x = (A4[0] - cw) / 2
        for k, img in enumerate((front, back)):
            img = load_page(img)
            dpi = settings.dpi
            # ritaglio alla dimensione reale della tessera (in alto a sinistra del piano)
            wpx, hpx = int(round(ID_CARD_MM[0] / 25.4 * dpi)), int(round(ID_CARD_MM[1] / 25.4 * dpi))
            if img.width > wpx * 1.05 or img.height > hpx * 1.05:
                img = img.crop((0, 0, min(img.width, wpx), min(img.height, hpx)))
            if img.size != (wpx, hpx):
                img = img.resize((wpx, hpx), Image.Resampling.LANCZOS)
            y = 100 + k * (ch + 60)
            _insert_image(doc, page, pymupdf.Rect(x, y, x + cw, y + ch), img, settings)
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return data
