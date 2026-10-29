"""Motore OCR di Foliant: logica pura, senza interfaccia.

API stabile (riusata da altri moduli)::

    ocr_pdf_bytes(data, languages, mode, pages=None, progress=None) -> bytes
    ocr_page_text(source, page=0, languages=None, clip=None, ...) -> str
    available_languages() -> list[str]

Flusso: per ogni pagina da riconoscere si rende un'immagine alla risoluzione di
lavoro (tenendo ENGINE_LOCK), la si pre-elabora (rotazione, raddrizzamento,
rumore, contrasto), si esegue Tesseract (processo locale, in parallelo su più
pagine) e si ottiene un `PageResult` con righe e parole. Il risultato si
applica poi al documento in una delle modalità:

- ``searchable``: testo invisibile (Tr 3) sopra l'immagine, aspetto identico;
- ``editable``: testo vero e visibile, posizionato e dimensionato come
  nell'originale, sopra lo sfondo della scansione ripulito dove c'era il testo;
- ``text``: solo testo (copia o esportazione).

Le coordinate interne sono in pixel dell'immagine elaborata; la conversione
verso la pagina usa una trasformazione rigida (rotazione + traslazione).
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np
import pymupdf

from foliant.core import placement, platform
from foliant.core.engine import ENGINE_LOCK
from foliant.core.errors import OperationCancelled
from foliant.i18n import _

from . import detect
from .mrz import fix_mrz
from .preprocess import (
    PreprocessOptions, apply_point, clean_background, ink_color, invert, prepare, rotation_affine, serif_ratio, slant, stroke_width,
    warp,
)
from .tess import (
    LOW_CONFIDENCE, Line, OcrCancelled, OcrError, Word, logical_lines, orientation, recognize,
)

SEARCHABLE = "searchable"
EDITABLE = "editable"
TEXT_ONLY = "text"
MODES = (SEARCHABLE, EDITABLE, TEXT_ONLY)

SKIP = "skip"        # pagine con testo: saltate
REPLACE = "replace"  # livello OCR esistente (testo invisibile) rimosso e rifatto; testo visibile intatto
FORCE = "force"      # pagine con testo: rasterizzate e riconosciute di nuovo

OCR_TAG = "FoliantOCR"   # sequenza marcata che identifica il livello di testo prodotto dall'OCR

MAX_PIXELS = 70_000_000     # oltre: risoluzione ridotta (pagine enormi)

FONT_FILES = {
    "sans": ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    "sans-bold": ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "serif": ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
              "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
    "serif-bold": ("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
                   "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    "mono": ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    "mono-bold": ("/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
}
for _fam, _file in (("sans", "LiberationSans"), ("serif", "LiberationSerif"), ("mono", "LiberationMono")):
    for _suffix, _style in (("-italic", "Italic"), ("-bold-italic", "BoldItalic")):
        FONT_FILES[_fam + _suffix] = tuple(f"/usr/share/fonts/truetype/{d}/{_file}-{_style}.ttf"
                                           for d in ("liberation", "liberation2"))
for _key in list(FONT_FILES):
    FONT_FILES[_key] += platform.windows_fonts(_key)      # Windows: Arial, Times New Roman, Courier New
_FALLBACK_BASE14 = {"sans": "helv", "sans-bold": "hebo", "serif": "tiro", "serif-bold": "tibo",
                    "mono": "cour", "mono-bold": "cobo", "sans-italic": "heit", "sans-bold-italic": "hebi",
                    "serif-italic": "tiit", "serif-bold-italic": "tibi", "mono-italic": "coit",
                    "mono-bold-italic": "cobi"}


def font_key(family: str, bold: bool = False, italic: bool = False) -> str:
    return family + ("-bold" if bold else "") + ("-italic" if italic else "")
_fonts: dict[str, pymupdf.Font] = {}
_fonts_lock = threading.Lock()


def get_font(kind: str = "sans") -> pymupdf.Font:
    with _fonts_lock:
        font = _fonts.get(kind)
        if font is None:
            for path in FONT_FILES.get(kind, ()):
                if os.path.exists(path):
                    try:
                        font = pymupdf.Font(fontfile=path)
                        break
                    except Exception:
                        continue
            if font is None:
                font = pymupdf.Font(_FALLBACK_BASE14.get(kind, "helv"))
            _fonts[kind] = font
        return font


# ====================================================================== dati
@dataclass
class OcrOptions:
    languages: list[str] = field(default_factory=lambda: ["ita", "eng"])
    mode: str = SEARCHABLE
    dpi: int = 300
    preprocess: PreprocessOptions = field(default_factory=PreprocessOptions)
    existing_text: str = SKIP
    workers: int = 0            # 0 = core disponibili meno uno
    font: str = "auto"          # famiglia del testo modificabile: auto (stimata), sans, serif, mono
    timeout: float = 120.0
    psm: int = 3
    apply_to_page: bool = False  # ricercabile: applica rotazione e raddrizzamento anche alla pagina visibile
    keep_original: bool = False  # modificabile: conserva la scansione come livello nascosto (OCG)
    clean_page: bool = False     # ricercabile: la pulizia sostituisce anche l'immagine visibile (OCR-09)
    zones: list = field(default_factory=list)   # zone manuali (layout.Zone), uguali per tutte le pagine
    auto_tables: bool = True     # riconoscimento automatico delle tabelle con bordi (OCR-10)
    compression: str = "exact"   # ricercabile: immagine della pagina exact | balanced | maximum (OCR-19)

    def worker_count(self) -> int:
        if self.workers > 0:
            return self.workers
        n = max(1, (os.cpu_count() or 2) - 1)
        # poca memoria: ogni lavoro tiene un'immagine di pagina e un processo del motore
        free = available_memory()
        if free:
            per_job = 350 * 1024 * 1024 * (self.dpi / 300) ** 2
            n = max(1, min(n, int(free * 0.5 // per_job)))
        return n


def available_memory() -> int:
    """Memoria disponibile in byte (0 se non determinabile)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


@dataclass
class PageResult:
    index: int
    status: str = "ok"                    # ok | failed | skipped
    message: str = ""
    category: str = ""
    lines: list[Line] = field(default_factory=list)
    zoom: float = 300 / 72                # pixel per punto
    affine: Any = None                    # np 2x3, pixel originali → pixel elaborati
    size: tuple[int, int] = (0, 0)        # dimensioni immagine elaborata
    rotation: int = 0                     # rotazione OSD applicata (oraria)
    skew: float = 0.0
    page_rotation: int = 0                # /Rotate della pagina all'OCR
    rasterize: bool = False               # la pagina va sostituita con la sua immagine
    mixed: bool = False                   # pagina mista: solo aree immagine
    background: bytes | None = None       # immagine di fondo (codificata)
    patches: list[tuple] = field(default_factory=list)   # pagine miste: (riquadro px, colore)
    notes: list[str] = field(default_factory=list)
    placed: list[tuple] = field(default_factory=list)    # dopo l'applicazione: (Rect pagina, Word)
    existing_text: str = ""               # pagine saltate perché già con testo: testo estraibile
    tables: list = field(default_factory=list)   # layout.Table rilevate (pixel elaborati)
    remove_layer: bool = False            # togliere il livello OCR invisibile esistente
    foreground: bytes | None = None       # OCR-19: maschera del testo (PNG con trasparenza) sopra lo sfondo
    source_bytes: int = 0                 # dimensione degli stream delle immagini originali della pagina
    original: bytes | None = None         # scansione originale (per il livello nascosto)
    offset: tuple[float, float] = (0.0, 0.0)   # OCR di un'area: origine dell'area sulla pagina (punti)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def words(self) -> list[Word]:
        return [w for ln in self.lines for w in ln.words]

    def low_confidence_words(self, threshold: float = LOW_CONFIDENCE) -> list[Word]:
        return [w for w in self.words if w.conf < threshold]

    @property
    def text(self) -> str:
        parts: list[str] = []
        prev_par = None
        for ln, text in logical_lines(self.lines):
            key = (ln.block, ln.par)
            if prev_par is not None and key != prev_par:
                parts.append("")
            parts.append(text)
            prev_par = key
        return unicodedata.normalize("NFC", "\n".join(parts))

    @property
    def mean_confidence(self) -> float:
        ws = self.words
        return sum(w.conf for w in ws) / len(ws) if ws else 0.0

    # ---------------------------------------------------------- coordinate
    def processed_to_display(self, x: float, y: float) -> tuple[float, float]:
        """Pixel dell'immagine elaborata → punti della pagina come visualizzata all'OCR."""
        dx, dy = self.offset
        if self.affine is None:
            return (x / self.zoom + dx, y / self.zoom + dy)
        ox, oy = apply_point(invert(np.asarray(self.affine)), x, y)
        return (ox / self.zoom + dx, oy / self.zoom + dy)

    def word_rect_display(self, word: Word) -> pymupdf.Rect:
        x0, y0, x1, y1 = word.bbox
        pts = [self.processed_to_display(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


@dataclass
class OcrResult:
    pages: list[PageResult] = field(default_factory=list)
    options: OcrOptions = field(default_factory=OcrOptions)

    @property
    def recognized(self) -> list[PageResult]:
        return [p for p in self.pages if p.ok]

    @property
    def failed(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "failed"]

    @property
    def skipped(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "skipped"]

    def text(self, page_separator: str = "\n\n") -> str:
        return page_separator.join(p.text for p in self.recognized).strip() + "\n"

    def notes(self) -> list[str]:
        """Avvisi per pagina (risoluzione bassa, pagina ridotta…), es. «Pagina 3: Risoluzione bassa…»."""
        return [_("Pagina {n}: {m}").format(n=p.index + 1, m=note) for p in self.pages for note in p.notes]

    def page(self, index: int) -> PageResult | None:
        for p in self.pages:
            if p.index == index:
                return p
        return None


class OcrInterrupted(OperationCancelled):
    """Riconoscimento annullato: `partial` contiene le pagine già completate."""

    def __init__(self, partial: "OcrResult"):
        super().__init__()
        self.partial = partial


# ===================================================================== riconoscimento
def _pixmap_to_array(pix: pymupdf.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
    arr = arr[:, :pix.width * pix.n].reshape(pix.height, pix.width, pix.n)
    return arr.copy()


def render_page(page: pymupdf.Page, dpi: int, clip: pymupdf.Rect | None = None) -> tuple[np.ndarray, float, list[str]]:
    """Rende la pagina (orientamento visualizzato) in RGB. Da chiamare con ENGINE_LOCK."""
    notes: list[str] = []
    rect = clip if clip is not None else page.rect
    zoom = dpi / 72.0
    pixels = rect.width * zoom * rect.height * zoom
    if pixels > MAX_PIXELS:
        zoom = math.sqrt(MAX_PIXELS / (rect.width * rect.height))
        notes.append(_("Pagina molto grande: riconosciuta a {d} dpi.").format(d=int(zoom * 72)))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False,
                          annots=False, clip=clip)
    return _pixmap_to_array(pix), zoom, notes


def _encode_background(img: np.ndarray) -> bytes:
    """Codifica l'immagine di fondo scegliendo il formato più compatto."""
    if img.ndim == 3:
        spread = np.abs(img.astype(np.int16) - img.mean(axis=2, keepdims=True).astype(np.int16)).max()
        if spread <= 10:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if img.ndim == 3 else img
    ok_p, png = cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    ok_j, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok_j and (not ok_p or len(jpg) * 1.6 < len(png)):
        return jpg.tobytes()
    return png.tobytes()


def _style_lines(lines: list[Line], color_img: np.ndarray | None, gray: np.ndarray, zoom: float) -> None:
    """Stima colore, famiglia (con/senza grazie, monospaziata), grassetto e corsivo di ogni riga."""
    if not lines:
        return
    sans = get_font("sans")
    ratios = []
    for ln in lines:
        fs_px = _font_size_for_line(ln, zoom, sans) * zoom
        ratios.append(stroke_width(gray, ln.bbox) / fs_px if fs_px > 0 else 0.0)
    # grassetto relativo al testo corrente della pagina (mediana), con un minimo assoluto
    ref = float(np.median(ratios)) if ratios else 0.0
    for ln, ratio in zip(lines, ratios):
        if color_img is not None:
            ln.color = ink_color(color_img, ln.bbox)
        ln.bold = ratio > max(0.12, 1.3 * ref) if len(lines) >= 3 else ratio > 0.145
        ln.italic = slant(gray, ln.bbox) >= 0.12
        measured = (ln.words[-1].bbox[2] - ln.words[0].bbox[0]) / zoom
        natural = sans.text_length(unicodedata.normalize("NFC", ln.text), fontsize=fs_px / zoom) or 1.0
        width_ratio = measured / natural
        x_height = max(3.0, ln.x_size - ln.x_ascenders) if ln.x_size else (ln.bbox[3] - ln.bbox[1]) * 0.45
        sr = serif_ratio(gray, ln.bbox, ln.baseline_y(ln.bbox[0]), x_height)
        if width_ratio > 1.45 and len(ln.text) >= 8:
            ln.family = "mono"
        elif not ln.italic and sr > 1.8:
            # nel dubbio senza grazie: le curve di a/e/o/s alzano il rapporto anche nei caratteri bastoni
            ln.family = "serif"
        else:
            ln.family = "sans"
    # voto di maggioranza per blocco: le righe di un paragrafo condividono il carattere
    blocks: dict[int, list[Line]] = {}
    for ln in lines:
        blocks.setdefault(ln.block, []).append(ln)
    for group in blocks.values():
        if len(group) < 2:
            continue
        fams = [ln.family for ln in group]
        top = max(set(fams), key=fams.count)
        if fams.count(top) / len(fams) >= 0.6:
            for ln in group:
                ln.family = top


def separate_words(lines: list[Line], gray: np.ndarray) -> None:
    """Tesseract allarga i riquadri delle parole brevi («i», «e») fin sopra la parola precedente:
    il riquadro si riduce al primo inchiostro dopo la colonna vuota che separa le due parole.
    Senza, nel testo invisibile le parole si sovrappongono e i lettori le uniscono («coprei»)."""
    h, w = gray.shape[:2]
    for ln in lines:
        for prev, word in zip(ln.words, ln.words[1:]):
            px1 = int(prev.bbox[2])
            x0, y0, x1, y1 = (int(v) for v in word.bbox)
            if x0 >= px1 or px1 >= x1:
                continue
            band = gray[max(0, y0):min(h, y1), max(0, px1):min(w, x1)]
            if band.size == 0:
                continue
            ink = (band < 128).any(axis=0)
            cols = np.nonzero(ink)[0]
            if cols.size:
                word.bbox = (float(px1 + cols[0]), word.bbox[1], word.bbox[2], word.bbox[3])


def _reading_score(gray: np.ndarray, languages: list[str], dpi: float, cancelled) -> float:
    lines = recognize(gray, languages, dpi=int(round(dpi)), psm=3, cancelled=cancelled, timeout=60)
    return sum(w.conf * len(w.text) for ln in lines for w in ln.words if w.conf > 50 and len(w.text) > 1)


def verify_rotation(gray: np.ndarray, rotation: int, languages: list[str], dpi: float = 300,
                    cancelled: Callable[[], bool] | None = None, confident: bool = True) -> int:
    """Conferma la rotazione proposta dall'OSD leggendo la pagina nei due versi.

    L'OSD sbaglia su pagine con poche righe di testo e molti segni ripetuti (zona MRZ dei
    passaporti, «<<<<»): si scarta la rotazione se il testo si legge nettamente meglio senza.
    Una proposta poco affidabile (`confident` falso) si accetta solo se ruotato si legge meglio."""
    h, w = gray.shape[:2]
    scale = min(1.0, 1800.0 / max(h, w))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    try:
        straight = _reading_score(gray, languages, dpi * scale, cancelled)
        m, size = rotation_affine(gray.shape[1], gray.shape[0], (rotation // 90) % 4, 0.0)
        turned = _reading_score(warp(gray, m, size, (255,)), languages, dpi * scale, cancelled)
    except OcrError:
        return rotation if confident else 0
    if not confident:
        return rotation if turned > straight * 1.5 else 0
    # Tesseract legge anche le righe verticali: a 90/270° i due punteggi possono coincidere, e nel
    # dubbio vale l'OSD. Si scarta la rotazione solo se il verso originale si legge nettamente meglio.
    return 0 if straight > turned * 1.5 else rotation


def _osd_rotation(img: np.ndarray, gray0: np.ndarray, popts: PreprocessOptions, languages: list[str],
                  dpi: float, cancelled) -> int:
    rot, conf = orientation(gray0, cancelled)
    if rot and conf >= 1.5:
        return verify_rotation(gray0, rot, languages, dpi, cancelled)
    if rot and conf >= 0.5:
        found = verify_rotation(gray0, rot, languages, dpi, cancelled, confident=False)
        if found:
            return found
    if conf < 1.5 and popts.perspective:
        # foto di un documento: sulla foto intera l'OSD trova troppo pochi caratteri; si riprova sul
        # documento già ritagliato e raddrizzato in prospettiva
        flat = prepare(img, PreprocessOptions(deskew=False, auto_rotate=False, denoise=popts.denoise,
                                              contrast=popts.contrast, borders=popts.borders,
                                              perspective=True), 0)
        if "perspective" in flat.notes:
            # sui documenti piccoli e radi l'OSD è poco sicuro anche quando ha ragione: decide la lettura
            rot, conf = orientation(flat.gray, cancelled)
            if rot:
                return verify_rotation(flat.gray, rot, languages, dpi, cancelled, confident=conf >= 1.5)
    return 0


def recognize_array(img: np.ndarray, languages: list[str], dpi: float, popts: PreprocessOptions | None = None,
                    psm: int = 3, cancelled: Callable[[], bool] | None = None, timeout: float = 120.0,
                    keep_color: bool = False) -> tuple[list[Line], Any, np.ndarray | None]:
    """Pre-elabora e riconosce un'immagine RGB o grigia.

    Restituisce (righe, Prepared, immagine a colori elaborata o None).
    """
    popts = popts or PreprocessOptions()
    gray0 = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    osd = _osd_rotation(img, gray0, popts, languages, dpi, cancelled) if popts.auto_rotate else 0
    prep = prepare(img, popts, osd)
    if cancelled is not None and cancelled():
        raise OcrCancelled()
    lines = recognize(prep.gray, languages, dpi=int(round(dpi)), psm=psm, cancelled=cancelled, timeout=timeout)
    separate_words(lines, prep.gray)
    color = None
    if keep_color:
        color = warp(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2RGB), prep.affine, prep.size)
    return lines, prep, color


def _render_job(pdf: pymupdf.Document, index: int, opts: OcrOptions,
                renderer=None) -> tuple[PageResult, np.ndarray | None]:
    """Classifica la pagina e la rende. Con `renderer` (processo separato) la resa avviene senza
    tenere ENGINE_LOCK: l'interfaccia non si blocca mentre l'OCR lavora in background."""
    res, page_needed = _classify_job(pdf, index, opts)
    if not page_needed:
        return res, None
    if renderer is not None:
        try:
            img, zoom, notes = renderer(index, opts.dpi)
            res.zoom = zoom
            res.notes[:0] = notes
            return res, img
        except _RenderLocally:
            pass
    with ENGINE_LOCK:
        img, zoom, notes = render_page(pdf[index], opts.dpi)
    res.zoom = zoom
    res.notes[:0] = notes
    return res, img


# ------------------------------------------------------------------ copie temporanee (riservatezza)
TEMP_PREFIX = "foliant-ocr-"
_LIVE_TEMP: set[str] = set()      # copie del documento ancora in uso da questo processo
_LIVE_LOCK = threading.Lock()
_atexit_registered = False


def _track_temp(path: str) -> None:
    global _atexit_registered
    with _LIVE_LOCK:
        _LIVE_TEMP.add(path)
        if not _atexit_registered:
            import atexit
            atexit.register(remove_live_temp)
            _atexit_registered = True


def _untrack_temp(path: str) -> None:
    with _LIVE_LOCK:
        _LIVE_TEMP.discard(path)


def remove_live_temp() -> None:
    """Cancella le copie temporanee del documento ancora presenti (uscita dall'app con un OCR in corso)."""
    with _LIVE_LOCK:
        paths = list(_LIVE_TEMP)
        _LIVE_TEMP.clear()
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    """True se esiste un processo con questo pid (su Windows senza os.kill, che lo terminerebbe)."""
    return platform.pid_alive(pid)


def cleanup_leftovers(directory: str | None = None, min_age: float = 300.0) -> int:
    """Cancella le copie temporanee dei documenti lasciate da un'esecuzione interrotta (crash).

    Solo file `foliant-ocr-*.pdf` dell'utente; si salta la copia di un processo Foliant ancora vivo
    (il PID è nel nome) e, per i nomi senza PID delle versioni precedenti, quelle recenti."""
    import glob
    import stat as _stat
    import tempfile
    directory = directory or tempfile.gettempdir()
    uid = os.getuid() if hasattr(os, "getuid") else None
    removed = 0
    for path in glob.glob(os.path.join(glob.escape(directory), TEMP_PREFIX + "*.pdf")):
        try:
            st = os.lstat(path)
            if not _stat.S_ISREG(st.st_mode) or (uid is not None and st.st_uid != uid):
                continue
            m = re.match(re.escape(TEMP_PREFIX) + r"(\d+)-", os.path.basename(path))
            if m:
                if _pid_alive(int(m.group(1))):
                    continue
            elif time.time() - st.st_mtime < min_age:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


class ProcessRenderer:
    """Resa delle pagine in un processo separato (`render_worker`), per non tenere ENGINE_LOCK nel
    processo dell'interfaccia durante l'OCR in background. Se il processo non parte o si guasta,
    la resa torna locale (con il lock)."""

    def __init__(self, data: bytes):
        import subprocess
        import sys
        import tempfile
        self.proc = None
        self.path = None
        try:
            fd, self.path = tempfile.mkstemp(prefix=f"{TEMP_PREFIX}{os.getpid()}-", suffix=".pdf")
            _track_temp(self.path)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            env = dict(os.environ)
            env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            self.proc = subprocess.Popen([sys.executable, "-m", "foliant.modules.ocr.render_worker", self.path],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, env=env, cwd=root)
        except Exception:
            self.close()

    def __call__(self, index: int, dpi: int):
        import json
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.write(f"{index} {int(dpi)}\n".encode())
                self.proc.stdin.flush()
                header = json.loads(self.proc.stdout.readline().decode("utf-8"))
                if "error" not in header:
                    n = header["h"] * header["w"] * header["c"]
                    buf = self.proc.stdout.read(n)
                    if len(buf) == n:
                        shape = (header["h"], header["w"]) if header["c"] == 1 else \
                            (header["h"], header["w"], header["c"])
                        img = np.frombuffer(buf, np.uint8).reshape(shape).copy()
                        return img, float(header["zoom"]), list(header.get("notes", []))
            except Exception:
                self.close()
        raise _RenderLocally()

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            _untrack_temp(self.path)
            self.path = None


class _RenderLocally(Exception):
    pass


def _classify_job(pdf: pymupdf.Document, index: int, opts: OcrOptions) -> tuple[PageResult, bool]:
    with ENGINE_LOCK:
        page = pdf[index]
        info = detect.classify_page(page)
        res = PageResult(index=index, category=info.category, page_rotation=page.rotation)
        if info.category == detect.EMPTY:
            res.status, res.message = "skipped", _("pagina vuota")
            return res, False
        # in modalità modificabile un livello OCR invisibile viene sempre convertito (OCR-06);
        # con «sostituisci» il livello invisibile viene rifatto (OCR-14)
        redo_layer = info.category == detect.OCR_LAYER and (opts.mode == EDITABLE or opts.existing_text == REPLACE)
        if info.category in (detect.TEXT, detect.OCR_LAYER) and opts.existing_text != FORCE and not redo_layer:
            res.status = "skipped"
            res.message = _("livello OCR presente") if info.category == detect.OCR_LAYER else _("testo presente")
            if opts.mode == TEXT_ONLY:
                res.existing_text = page.get_text("text", sort=True)
            return res, False
        if info.category == detect.UNEXTRACTABLE and opts.existing_text != FORCE and opts.mode != TEXT_ONLY:
            # convertire il testo non estraibile è distruttivo (la pagina diventa immagine): solo con «Forza OCR»
            res.status = "skipped"
            res.message = _("testo non estraibile: scegli «Forza OCR» per riconoscerlo")
            return res, False
        res.remove_layer = redo_layer
        res.rasterize = info.category in (detect.TEXT, detect.UNEXTRACTABLE) or (
            info.category == detect.OCR_LAYER and not redo_layer)
        res.mixed = info.category == detect.MIXED and opts.existing_text != FORCE
        if info.category == detect.MIXED and opts.existing_text == FORCE:
            res.rasterize = True
        if opts.compression != "exact":
            try:
                res.source_bytes = sum(len(pdf.xref_stream_raw(x[0]) or b"") for x in page.get_images(full=True))
            except Exception:
                res.source_bytes = 0
        if info.low_resolution:
            res.notes.append(_("Risoluzione bassa ({d} dpi): accuratezza ridotta.").format(d=int(info.dpi)))
        if res.mixed:
            # area del testo vettoriale esistente in coordinate visualizzate
            m = placement.page_to_display_matrix(page)
            res.patches = [("text", tuple(pymupdf.Rect(r) * m)) for r in info.text_rects]
        return res, True


def _box_original(res: PageResult, bbox) -> tuple[float, float, float, float]:
    """Riquadro di una parola (pixel elaborati) nei pixel dell'immagine resa della pagina."""
    pts = [res.processed_to_display(x, y) for x, y in ((bbox[0], bbox[1]), (bbox[2], bbox[1]),
                                                       (bbox[0], bbox[3]), (bbox[2], bbox[3]))]
    z = res.zoom
    xs = [p[0] * z for p in pts]
    ys = [p[1] * z for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _image_bytes_estimate(img: np.ndarray) -> int:
    """Dimensione di riferimento della scansione non compressa con perdita (PNG)."""
    ok, png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if img.ndim == 3 else img)
    return len(png) if ok else img.size


def _process(res: PageResult, img: np.ndarray, opts: OcrOptions, cancelled: Callable[[], bool]) -> PageResult:
    editable = opts.mode == EDITABLE
    lines, prep, color = recognize_array(img, opts.languages, res.zoom * 72, opts.preprocess, opts.psm, cancelled,
                                         opts.timeout, keep_color=editable)
    fix_mrz(lines)
    res.affine = prep.affine
    res.size = prep.size
    res.rotation = prep.rotation
    res.skew = prep.skew
    if opts.zones or opts.auto_tables:
        from .layout import apply_layout

        def zone_ocr(crop, langs):
            return recognize(crop, langs, dpi=int(round(res.zoom * 72)), psm=6, cancelled=cancelled,
                             timeout=opts.timeout)
        lines, res.tables = apply_layout(lines, prep.gray, (img.shape[1], img.shape[0]), prep.affine,
                                         list(opts.zones), zone_ocr, opts.auto_tables)
    if res.mixed:
        existing = [pymupdf.Rect(r) for kind, r in res.patches if kind == "text"]
        res.patches = []
        kept = []
        for ln in lines:
            ln.words = [w for w in ln.words if not any(res.word_rect_display(w).intersects(e) for e in existing)]
            if ln.words:
                xs0 = min(w.bbox[0] for w in ln.words)
                xs1 = max(w.bbox[2] for w in ln.words)
                ln.bbox = (xs0, ln.bbox[1], xs1, ln.bbox[3])
                kept.append(ln)
        lines = kept
    res.lines = lines
    if editable:
        _style_lines(lines, color, prep.gray, res.zoom)
        # si ripulisce l'intera riga (parole e spazi tra loro): niente residui di glifi
        boxes = [w.bbox for ln in lines for w in ln.words] + [ln.bbox for ln in lines]
        if res.mixed:
            from .preprocess import paper_color
            for ln in lines:
                x0, y0, x1, y1 = ln.bbox
                pad = 2
                box = (max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad)
                ring = color[max(0, int(y0) - 6):int(y1) + 6, max(0, int(x0) - 6):int(x1) + 6]
                res.patches.append((box, tuple(c / 255.0 for c in paper_color(ring)) if ring.size else (1, 1, 1)))
        else:
            bg = cv2.medianBlur(color, 3) if opts.preprocess.denoise else color
            res.background = _encode_background(clean_background(bg, boxes))
            if opts.keep_original:
                res.original = _encode_background(img)
    elif opts.mode == SEARCHABLE and res.rasterize:
        res.background = _encode_background(img)
    elif opts.mode == SEARCHABLE and opts.compression != "exact" and not res.mixed and \
            res.category == detect.IMAGE_ONLY:
        from .compress import compress_page
        boxes = [_box_original(res, w.bbox) for w in res.words]
        bg, fg = compress_page(img, boxes, opts.compression)
        reference = res.source_bytes or _image_bytes_estimate(img)
        if len(bg) + len(fg or b"") < 0.9 * reference:     # guadagno sotto il 10%: immagine invariata
            res.background, res.foreground = bg, fg
            res.rasterize = True
    elif opts.mode == SEARCHABLE and opts.clean_page and not res.mixed:
        # immagine della pagina pulita (rumore, contrasto, bordi) con la stessa geometria
        from .preprocess import enhance, to_gray
        res.background = _encode_background(enhance(to_gray(img), opts.preprocess))
        res.rasterize = True
    return res


def recognize_document(pdf: pymupdf.Document, opts: OcrOptions, pages: list[int] | None = None,
                       progress=None, render_data: bytes | None = None) -> OcrResult:
    """Riconosce le pagine indicate (tutte se None). Non modifica il documento.

    Le pagine vengono rese una alla volta tenendo ENGINE_LOCK e riconosciute in
    parallelo fuori dal lock. Un errore su una pagina la marca «non
    riconosciuta» senza interrompere le altre.
    """
    if opts.mode not in MODES:
        raise ValueError(f"modalità OCR sconosciuta: {opts.mode}")
    from .languages import check_languages
    check_languages(list(opts.languages) + [c for z in opts.zones for c in z.languages if c not in opts.languages])
    with ENGINE_LOCK:
        count = pdf.page_count
    indices = list(range(count)) if pages is None else [i for i in pages if 0 <= i < count]
    result = OcrResult(options=opts)
    if not indices:
        return result
    cancel_event = threading.Event()

    def cancelled() -> bool:
        if cancel_event.is_set():
            return True
        if progress is not None and getattr(progress, "cancelled", False):
            cancel_event.set()
            return True
        return False

    def paused() -> bool:
        fn = getattr(progress, "is_paused", None)
        return bool(fn()) if callable(fn) else False

    def report(done: int, text: str) -> None:
        if progress is not None:
            progress.update(done / len(indices), text)

    workers = min(opts.worker_count(), len(indices))
    renderer = ProcessRenderer(render_data) if render_data is not None else None
    results: dict[int, PageResult] = {}
    done = 0
    report(0, _("Riconoscimento della pagina {n} di {t}…").format(n=1, t=len(indices)))

    def job(res: PageResult, img: np.ndarray) -> PageResult:
        try:
            return _process(res, img, opts, cancelled)
        except OcrCancelled:
            raise
        except OcrError as e:
            res.status, res.message = "failed", str(e)
        except Exception as e:  # una pagina difettosa non ferma le altre
            res.status, res.message = "failed", _("Errore imprevisto: {e}").format(e=e)
        res.lines = []
        return res

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="foliant-ocr") as pool:
            pending: list = []
            it = iter(indices)
            exhausted = False
            while pending or not exhausted:
                while not exhausted and len(pending) < workers * 2:
                    # pausa (OCR-13): nessuna nuova pagina finché dura; le pagine in corso terminano
                    while paused() and not cancelled():
                        time.sleep(0.1)
                        if not pending:
                            report(done, _("In pausa: {d} di {t} pagine riconosciute.").format(d=done, t=len(indices)))
                    try:
                        index = next(it)
                    except StopIteration:
                        exhausted = True
                        break
                    if cancelled():
                        raise OcrCancelled()
                    try:
                        res, img = _render_job(pdf, index, opts, renderer)
                    except Exception as e:
                        res, img = PageResult(index=index, status="failed",
                                              message=_("Impossibile leggere la pagina: {e}").format(e=e)), None
                    if img is None:
                        results[index] = res
                        done += 1
                        continue
                    pending.append(pool.submit(job, res, img))
                if not pending:
                    continue
                fut = pending.pop(0)
                while True:
                    try:
                        res = fut.result(timeout=0.1)
                        break
                    except TimeoutError:
                        pass
                    except OcrCancelled:
                        raise
                    try:
                        report(done / 1.0, _("Riconoscimento: {d} di {t} pagine…").format(d=done, t=len(indices)))
                    except BaseException:
                        cancel_event.set()
                        raise
                results[res.index] = res
                done += 1
                report(done, _("Riconoscimento: {d} di {t} pagine…").format(d=done, t=len(indices)))
    except (OcrCancelled, OperationCancelled):
        cancel_event.set()
        result.pages = [results[i] for i in indices if i in results]
        raise OcrInterrupted(result) from None
    except BaseException:
        cancel_event.set()
        raise
    finally:
        if renderer is not None:
            renderer.close()
    result.pages = [results[i] for i in indices if i in results]
    return result


def page_fingerprints(pdf: pymupdf.Document) -> list[str]:
    """Impronta di ogni pagina (contenuto, risorse, rotazione, riquadri): serve a capire quali pagine
    sono cambiate mentre l'OCR lavorava in background. Da chiamare con ENGINE_LOCK."""
    import hashlib
    out = []
    for page in pdf:
        h = hashlib.sha1()
        for xref in page.get_contents():
            h.update(pdf.xref_stream(xref) or b"")
        h.update(f"{page.rotation}|{tuple(page.rect)}|{tuple(page.mediabox)}".encode())
        try:
            h.update(pdf.xref_object(page.xref, compressed=True).encode())
        except Exception:
            pass
        out.append(h.hexdigest())
    return out


REVIEW_KEY = "PieceInfo/FoliantOCR"


def store_review_data(page: pymupdf.Page, placed: list) -> None:
    """Salva le parole sospette della pagina come dati privati (/PieceInfo, ISO 32000 §14.5).

    `placed` = [(Rect, Word)]; si conservano solo le parole sotto la soglia di affidabilità."""
    import datetime
    import json
    doc = page.parent
    items = [[round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2), w.text, round(w.conf, 1)]
             for r, w in placed if w.low]
    if not items:
        clear_review_data(page)
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    data = pymupdf.get_pdf_str(json.dumps(items, ensure_ascii=False))
    doc.xref_set_key(page.xref, "PieceInfo", f"<< /FoliantOCR << /LastModified ({stamp}) /Private {data} >> >>")


def load_review_data(page: pymupdf.Page) -> list:
    """Parole sospette salvate nella pagina: [(Rect, Word)] (vuoto se assenti o illeggibili)."""
    import json
    doc = page.parent
    try:
        kind, val = doc.xref_get_key(page.xref, REVIEW_KEY + "/Private")
    except Exception:
        return []
    if kind != "string":
        return []
    try:
        items = json.loads(val)
        return [(pymupdf.Rect(x0, y0, x1, y1), Word(text, (0, 0, 0, 0), conf))
                for x0, y0, x1, y1, text, conf in items]
    except (ValueError, TypeError):
        return []


def clear_review_data(page: pymupdf.Page) -> None:
    doc = page.parent
    try:
        if doc.xref_get_key(page.xref, REVIEW_KEY)[0] != "null":
            doc.xref_set_key(page.xref, REVIEW_KEY, "null")
    except Exception:
        pass


def document_copy_bytes(pdf: pymupdf.Document) -> bytes:
    """Copia non cifrata del documento aperto, senza alterarlo (da chiamare con ENGINE_LOCK).

    Su un documento cifrato `tobytes()` altera lo stato di cifratura in memoria e il
    salvataggio successivo perde immagini e protezione: lì si copiano le pagine in un
    nuovo documento, lasciando intatto l'originale."""
    encrypted = bool(getattr(pdf, "is_encrypted", False)) or bool((pdf.metadata or {}).get("encryption"))
    if not encrypted:
        return pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
    copy = pymupdf.open()
    try:
        copy.insert_pdf(pdf)
        return copy.tobytes(garbage=0, deflate=False)
    finally:
        copy.close()


def preview_page(pdf: pymupdf.Document, index: int, popts: PreprocessOptions, dpi: int = 150,
                 languages: list[str] | None = None) -> dict:
    """Anteprima della pre-elaborazione di una pagina (OCR-04): immagine originale, immagine di lavoro
    (quella che vedrà il riconoscimento), orientamento e inclinazione rilevati, risoluzione effettiva."""
    with ENGINE_LOCK:
        page = pdf[index]
        info = detect.classify_page(page, check_mixed=False)
        img, zoom, _notes = render_page(page, dpi)
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    osd = _osd_rotation(img, gray, popts, languages or default_languages(), dpi, None) if popts.auto_rotate else 0
    prep = prepare(img, popts, osd)
    return {"original": img, "working": prep.gray, "rotation": prep.rotation, "skew": prep.skew,
            "perspective": "perspective" in prep.notes, "dpi": info.dpi, "category": info.category}


def recognize_area(pdf: pymupdf.Document, index: int, clip: pymupdf.Rect, opts: OcrOptions,
                   cancelled: Callable[[], bool] | None = None) -> PageResult:
    """Riconosce un'area (punti della pagina visualizzata) e restituisce un risultato
    applicabile alla pagina come le pagine miste: testo solo dentro l'area (OCR-11)."""
    from .languages import check_languages
    check_languages(opts.languages)
    with ENGINE_LOCK:
        page = pdf[index]
        clip = pymupdf.Rect(clip) & page.rect
        img, zoom, notes = render_page(page, max(opts.dpi, 300), clip)
        rotation = page.rotation
    res = PageResult(index=index, category=detect.MIXED, page_rotation=rotation, zoom=zoom, mixed=True,
                     offset=(clip.x0, clip.y0), notes=notes)
    popts = PreprocessOptions(deskew=opts.preprocess.deskew, auto_rotate=False, denoise=opts.preprocess.denoise,
                              contrast=opts.preprocess.contrast)
    sub = OcrOptions(languages=opts.languages, mode=opts.mode, dpi=opts.dpi, preprocess=popts, psm=6,
                     timeout=opts.timeout)
    return _process(res, img, sub, cancelled or (lambda: False))


def rasterize_pages(pdf: pymupdf.Document, pages: list[int] | None = None, dpi: int = 200) -> int:
    """Converte le pagine in sole immagini (OCR-18): niente testo estraibile, aspetto conservato.

    Il contenuto della pagina è sostituito dalla sua resa; annotazioni e campi modulo restano.
    Da chiamare dentro `document.change()`."""
    n = 0
    for i in (range(pdf.page_count) if pages is None else pages):
        page = pdf[i]
        pix = page.get_pixmap(dpi=dpi, annots=False, alpha=False)
        _clear_page_content(page)
        placement.insert_image(page, placement.page_rect(page), pixmap=pix, keep_proportion=False, upright=True)
        _drop_unused_images(page)
        _drop_unused_fonts(page)
        n += 1
    return n


def _drop_unused_fonts(page: pymupdf.Page) -> None:
    doc = page.parent
    try:
        kind, val = doc.xref_get_key(page.xref, "Resources")
        target, prefix = (int(val.split()[0]), "") if kind == "xref" else (page.xref, "Resources/")
        doc.xref_set_key(target, prefix + "Font", "null")
    except Exception:
        pass


def remove_ocr_layer(pdf: pymupdf.Document, pages: list[int] | None = None) -> int:
    """Rimuove il livello di testo invisibile dalle pagine (OCR-18). Restituisce le pagine modificate."""
    n = 0
    for i in (range(pdf.page_count) if pages is None else pages):
        if remove_invisible_text(pdf[i]):
            n += 1
    return n


# ===================================================================== applicazione
def _clear_page_content(page: pymupdf.Page) -> None:
    doc = page.parent
    for xref in page.get_contents():
        doc.update_stream(xref, b" ")


def _rigid(page: pymupdf.Page, mapper: Callable[[float, float], tuple[float, float]]):
    """Matrice lineare e traslazione della trasformazione rigida pixel → pagina non ruotata."""
    der = placement.display_to_page_matrix(page)
    def to_page(x, y):
        dx, dy = mapper(x, y)
        p = pymupdf.Point(dx, dy) * der
        return p.x, p.y
    o = to_page(0, 0)
    ex = to_page(1000, 0)
    ey = to_page(0, 1000)
    lin = ((ex[0] - o[0]) / 1000, (ey[0] - o[0]) / 1000, (ex[1] - o[1]) / 1000, (ey[1] - o[1]) / 1000)
    return lin, o, to_page


def _write(page: pymupdf.Page, writer: pymupdf.TextWriter, lin, t, render_mode: int, color, opacity=None) -> None:
    """Scrive il TextWriter (posizioni in punti dello spazio elaborato) applicando la
    trasformazione rigida tramite «morph» attorno al suo punto fisso."""
    a, b, c, d = lin   # x' = a x + b y + tx ; y' = c x + d y + ty
    if abs(a - 1) < 1e-6 and abs(d - 1) < 1e-6 and abs(b) < 1e-6 and abs(c) < 1e-6:
        morph = None
        if abs(t[0]) > 1e-6 or abs(t[1]) > 1e-6:
            raise RuntimeError("translation-only")  # gestita dal chiamante con le posizioni
    else:
        # punto fisso p: (I - L) p = t
        m = np.array([[1 - a, -b], [-c, 1 - d]], np.float64)
        px, py = np.linalg.solve(m, np.array(t, np.float64))
        # il morph del motore lavora con l'asse y verso l'alto: matrice coniugata
        morph = (pymupdf.Point(px, py), pymupdf.Matrix(a, -c, -b, d, 0, 0))
    kwargs = {"render_mode": render_mode, "color": color}
    if morph is not None:
        kwargs["morph"] = morph
    placement.write_text(page, writer, **kwargs)


class _Placer:
    """Raccoglie le parole/righe da scrivere sulla pagina con una trasformazione rigida."""

    def __init__(self, page: pymupdf.Page, mapper, zoom: float):
        self.page = page
        self.zoom = zoom
        # posizioni in punti dello spazio elaborato (pixel / zoom)
        self.lin, self.o, self.to_page = _rigid(page, lambda x, y: mapper(x * zoom, y * zoom))
        a, b, c, d = self.lin
        self.translation_only = abs(a - 1) < 1e-6 and abs(d - 1) < 1e-6 and abs(b) < 1e-6 and abs(c) < 1e-6
        # traslazione in punti (le posizioni sono in punti dello spazio elaborato)
        self.t = (self.o[0], self.o[1])
        # sequenza ordinata (chiave, writer): l'ordine di scrittura resta quello di lettura
        self.writers: list[tuple[tuple, pymupdf.TextWriter]] = []

    def pos(self, x_px: float, y_px: float) -> pymupdf.Point:
        x, y = x_px / self.zoom, y_px / self.zoom
        if self.translation_only:
            return pymupdf.Point(x + self.t[0], y + self.t[1])
        return pymupdf.Point(x, y)

    def writer(self, key) -> pymupdf.TextWriter:
        if self.writers and self.writers[-1][0] == key:
            return self.writers[-1][1]
        w = pymupdf.TextWriter(self.page.rect)
        self.writers.append((key, w))
        return w

    def flush(self, render_mode: int) -> None:
        for (color, _k), w in self.writers:
            if self.translation_only:
                placement.write_text(self.page, w, render_mode=render_mode, color=color)
            else:
                # posizioni nello spazio elaborato con origine 0: la traslazione
                # va espressa in punti dello stesso spazio
                _write(self.page, w, self.lin, self.t, render_mode, color)


def _font_size_for_line(ln: Line, zoom: float, font: pymupdf.Font) -> float:
    """Corpo stimato dall'altezza della riga (ascendenti + discendenti)."""
    h = ln.x_size if ln.x_size > 0 else (ln.bbox[3] - ln.bbox[1])
    asc = font.ascender if font.ascender > 0 else 0.9
    desc = -font.descender if font.descender < 0 else 0.2
    return max(2.0, (h / zoom) / max(0.8, asc + desc) * 1.08)


STRAIGHT_WORDS_DEG = 3.0   # sotto questa inclinazione il testo invisibile si scrive diritto


def _insert_words_invisible(page: pymupdf.Page, res: PageResult, mapper, zoom: float) -> int:
    """Livello di testo invisibile: una parola per oggetto di testo, con corpo della riga,
    scala orizzontale (Tz) che fa coincidere la larghezza con la parola nell'immagine e
    direzione (Tm) che segue l'inclinazione della riga.

    Il font viene incorporato (con /ToUnicode) tramite TextWriter; il contenuto è poi
    riscritto con gli operatori per parola."""
    font = get_font("sans")
    to_page = lambda x, y: (pymupdf.Point(*mapper(x, y)) * placement.display_to_page_matrix(page))  # noqa: E731
    items = []
    tw = pymupdf.TextWriter(page.rect)
    for ln in res.lines:
        fs_line = _font_size_for_line(ln, zoom, font)
        asc = font.ascender if font.ascender > 0 else 0.9
        desc = -font.descender if font.descender < 0 else 0.2
        for word in ln.words:
            text = unicodedata.normalize("NFC", word.text)
            if not text:
                continue
            x0, y0, x1, y1 = word.bbox
            # riquadro di selezione = riquadro della parola nell'immagine: corpo dall'altezza
            # della parola (entro limiti ragionevoli rispetto alla riga), base sul fondo della parola
            base = ln.baseline_y(x0)
            fs_px = (y1 - y0) / (asc + desc)
            fs = round(min(max(fs_px / zoom, fs_line * 0.6), fs_line * 1.15), 2)
            shift = min(max(y1 - desc * fs * zoom, base - 0.25 * fs * zoom), base + 0.25 * fs * zoom) - base
            p0 = to_page(x0, ln.baseline_y(x0) + shift)
            p1 = to_page(x1, ln.baseline_y(x1) + shift)
            length = math.hypot(p1.x - p0.x, p1.y - p0.y)
            natural = font.text_length(text, fontsize=fs)
            if length <= 0 or natural <= 0:
                continue
            gids = [font.has_glyph(ord(c)) for c in text]
            if not all(gids):
                text = "".join(c for c, g in zip(text, gids) if g)
                gids = [g for g in gids if g]
                if not text:
                    continue
                natural = font.text_length(text, fontsize=fs) or 1.0
            try:
                tw.append(page.rect.tl, text + " ", font=font, fontsize=fs)
            except Exception:
                continue
            # spazio esplicito dopo la parola (tranne l'ultima della riga): copia e ricerca
            # restituiscono parole separate anche quando sono molto vicine
            # (scritto a parte e largo al più metà dello spazio vero: scalato con la parola invadeva la
            # successiva e poppler le univa, «copre i» → «coprei»)
            space = font.has_glyph(32) if word is not ln.words[-1] else 0
            space_tz = 0.0
            if space:
                nxt = ln.words[ln.words.index(word) + 1]
                gap = max(0.0, (nxt.bbox[0] - x1) / zoom)
                space_tz = 100.0 * min(1.0, 0.5 * gap / max(0.01, font.text_length(" ", fontsize=fs)))
            items.append((p0, p1, length, natural, fs, gids, space, space_tz))
    if not items:
        return 0
    before = set(page.get_contents())
    tw.write_text(page, render_mode=3)
    doc = page.parent
    new = [x for x in page.get_contents() if x not in before]
    m = re.search(rb"/(\S+)\s+[\d.]+\s+Tf", b"".join(doc.xref_stream(x) or b"" for x in new))
    if not m:
        return len(items)
    fname = m.group(1).decode()
    inv = placement.page_to_pdf_matrix(page)     # coordinate standard (y in basso) → spazio PDF
    out = ["q", "BT", "3 Tr"]
    for p0, p1, length, natural, fs, gids, space, space_tz in items:
        o = p0 * inv
        e = p1 * inv
        ux, uy = (e.x - o.x) / length, (e.y - o.y) / length
        # inclinazione lieve: parola diritta (allineata all'asse più vicino) centrata sulla sua base
        # media, come hocrtransform. Poppler (Evince, Okular, pdftotext) spezza e riordina le parole
        # appena ruotate; la differenza di posizione è sotto il punto.
        angle = math.atan2(uy, ux)
        axis = round(angle / (math.pi / 2)) * (math.pi / 2)
        if abs(angle - axis) < math.radians(STRAIGHT_WORDS_DEG):
            mx, my = (o.x + e.x) / 2, (o.y + e.y) / 2
            ux, uy = round(math.cos(axis)), round(math.sin(axis))
            o = pymupdf.Point(mx - ux * length / 2, my - uy * length / 2)
        tz = 100.0 * length / natural
        hexs = "".join(f"{g:04x}" for g in gids)
        out.append(f"/{fname} {fs:g} Tf {tz:.2f} Tz {ux:.5f} {uy:.5f} {-uy:.5f} {ux:.5f} {o.x:.3f} {o.y:.3f} Tm "
                   f"<{hexs}> Tj")
        if space:
            out.append(f"{max(space_tz, 1.0):.2f} Tz <{space:04x}> Tj")
    out += ["ET", "Q"]
    doc.update_stream(new[0], ("\n".join(out) + "\n").encode())
    for x in new[1:]:
        doc.update_stream(x, b" ")
    return len(items)


def line_font_sizes(lines: list[Line], zoom: float, family: str) -> list[float]:
    """Corpo di ogni riga: adattato alla larghezza, limitato dall'altezza e
    uniformato sulla pagina (le righe del testo corrente hanno lo stesso corpo)."""
    sizes = []
    for ln in lines:
        font = get_font(font_key(ln.family if family == "auto" else family, ln.bold, ln.italic))
        fs_h = _font_size_for_line(ln, zoom, font)
        text = unicodedata.normalize("NFC", ln.text)
        width_pt = (ln.words[-1].bbox[2] - ln.words[0].bbox[0]) / zoom if ln.words else 0
        natural = font.text_length(text, fontsize=fs_h) or 1.0
        fs = fs_h * width_pt / natural if width_pt > 0 else fs_h
        sizes.append(min(max(fs, fs_h * 0.6), fs_h * 1.4))
    long_lines = [fs for fs, ln in zip(sizes, lines) if len(ln.words) >= 3] or sizes
    if long_lines:
        med = float(np.median(long_lines))
        sizes = [med if abs(fs - med) / med < 0.15 else fs for fs in sizes]
    return [round(fs * 2) / 2 for fs in sizes]


def _insert_lines_visible(page: pymupdf.Page, res: PageResult, mapper, zoom: float, family: str) -> int:
    placer = _Placer(page, mapper, zoom)
    n = 0
    sizes = line_font_sizes(res.lines, zoom, family)
    for ln, fs in zip(res.lines, sizes):
        text = unicodedata.normalize("NFC", ln.text)
        if not text:
            continue
        key = font_key(ln.family if family == "auto" else family, ln.bold, ln.italic)
        font = get_font(key)
        color = tuple(round(c, 2) for c in ln.color)
        w = placer.writer((color, key))
        # gruppi di parole separati da più di 2 em (celle di tabella, tabulazioni): ognuno alla sua x
        segments: list[list] = [[ln.words[0]]]
        for a, b in zip(ln.words, ln.words[1:]):
            if b.bbox[0] - a.bbox[2] > 2 * fs * zoom:
                segments.append([b])
            else:
                segments[-1].append(b)
        for seg in segments:
            x0 = seg[0].bbox[0]
            seg_text = unicodedata.normalize("NFC", " ".join(wd.text for wd in seg))
            try:
                w.append(placer.pos(x0, ln.baseline_y(x0)), seg_text, font=font, fontsize=fs)
            except Exception:
                continue
        n += 1
    placer.flush(render_mode=0)
    return n


def apply_page(pdf: pymupdf.Document, res: PageResult, mode: str, family: str = "sans",
               apply_to_page: bool = False, original_ocg: int = 0) -> None:
    """Applica il risultato di una pagina al documento (dentro `document.change()`)."""
    if not res.ok or mode == TEXT_ONLY:
        return
    page = pdf[res.index]
    if res.remove_layer:
        remove_invisible_text(page)
    if mode == SEARCHABLE:
        if res.rasterize and res.background:
            _clear_page_content(page)
            placement.insert_image(page, placement.page_rect(page), stream=res.background, keep_proportion=False,
                                   upright=True)
            if res.foreground:
                placement.insert_image(page, placement.page_rect(page), stream=res.foreground,
                                       keep_proportion=False, upright=True)
        if apply_to_page and (res.rotation or res.skew) and not res.mixed:
            _straighten_page(page, res)
            sx = page.rect.width / max(1.0, res.size[0])
            sy = page.rect.height / max(1.0, res.size[1])
            with _marked(page):
                _insert_words_invisible(page, res, lambda x, y: (x * sx, y * sy), 2.0 / (sx + sy))
            der = placement.display_to_page_matrix(page)
            res.placed = [(pymupdf.Rect(w.bbox[0] * sx, w.bbox[1] * sy, w.bbox[2] * sx, w.bbox[3] * sy) * der, w)
                          for w in res.words]
        else:
            with _marked(page):
                _insert_words_invisible(page, res, res.processed_to_display, res.zoom)
            der = placement.display_to_page_matrix(page)
            res.placed = [(res.word_rect_display(w) * der, w) for w in res.words]
        if res.rasterize:
            _drop_unused_images(page)
    elif mode == EDITABLE:
        if res.mixed:
            for box, color in res.patches:
                r = _box_display(res, box) * placement.display_to_page_matrix(page)
                placement.draw_rect(page, r, color=None, fill=color, overlay=True)
            with _marked(page):
                _insert_lines_visible(page, res, res.processed_to_display, res.zoom, family)
            der = placement.display_to_page_matrix(page)
            res.placed = [(res.word_rect_display(w) * der, w) for w in res.words]
        else:
            _clear_page_content(page)
            page.set_rotation((res.page_rotation + res.rotation) % 360)
            if res.original and original_ocg:
                # la scansione originale resta nel file, nascosta in visualizzazione e stampa
                placement.insert_image(page, placement.page_rect(page), stream=res.original, keep_proportion=False,
                                       rotate=(-res.rotation) % 360, upright=True, oc=original_ocg)
            placement.insert_image(page, placement.page_rect(page), stream=res.background, keep_proportion=False,
                                   upright=True)
            # l'immagine elaborata (raddrizzata) riempie la pagina visualizzata
            sx = page.rect.width / max(1.0, res.size[0])
            sy = page.rect.height / max(1.0, res.size[1])
            with _marked(page):
                _insert_lines_visible(page, res, lambda x, y: (x * sx, y * sy), 2.0 / (sx + sy), family)
            _drop_unused_images(page)
            der = placement.display_to_page_matrix(page)
            res.placed = [(pymupdf.Rect(w.bbox[0] * sx, w.bbox[1] * sy, w.bbox[2] * sx, w.bbox[3] * sy) * der, w)
                          for w in res.words]


class _marked:
    """Racchiude il contenuto aggiunto alla pagina in una sequenza marcata /FoliantOCR (ISO 32000 §14.6),
    così il livello prodotto dall'OCR è riconoscibile (per sostituirlo o rimuoverlo)."""

    def __init__(self, page: pymupdf.Page):
        self.page = page

    def __enter__(self):
        self.before = set(self.page.get_contents())
        return self

    def __exit__(self, exc_type, *_a):
        if exc_type is not None:
            return False
        doc = self.page.parent
        for xref in self.page.get_contents():
            if xref in self.before:
                continue
            data = doc.xref_stream(xref) or b""
            doc.update_stream(xref, b"/" + OCR_TAG.encode() + b" BMC\n" + data + b"\nEMC\n")
        return False


_BT_ET = re.compile(rb"BT\b.*?\bET\b", re.S)
_OCR_MC = re.compile(rb"/" + OCR_TAG.encode() + rb"\s+BMC\b.*?\bEMC\b", re.S)


def remove_invisible_text(page: pymupdf.Page) -> int:
    """Rimuove il livello di testo invisibile (OCR) lasciando intatti immagini e testo visibile.

    Toglie le sequenze marcate /FoliantOCR e, per i livelli prodotti da altri
    programmi, gli oggetti di testo BT…ET con modalità di resa 3.
    """
    doc = page.parent
    removed = 0
    page.clean_contents(sanitize=False)
    for xref in page.get_contents():
        data = doc.xref_stream(xref) or b""
        new, n1 = _OCR_MC.subn(b"", data)

        def drop(m):
            return b"" if re.search(rb"\b3\s+Tr\b", m.group(0)) else m.group(0)
        new2 = _BT_ET.sub(drop, new)
        if new2 != data:
            removed += 1
            doc.update_stream(xref, new2)
    return removed


def _straighten_page(page: pymupdf.Page, res: PageResult) -> None:
    """Applica alla pagina visibile la rotazione (/Rotate) e il raddrizzamento
    (matrice «cm» attorno al centro) senza ricodificare l'immagine (OCR-08)."""
    if res.rotation:
        page.set_rotation((page.rotation + res.rotation) % 360)
    if res.skew:
        # rotazione attorno al centro della pagina, in coordinate PDF (y verso l'alto)
        r = page.mediabox
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        # skew = rotazione antioraria (a vista) che raddrizza: nello spazio PDF (y in alto) è positiva
        a = math.radians(res.skew)
        ca, sa = math.cos(a), math.sin(a)
        e = cx - ca * cx + sa * cy
        f = cy - sa * cx - ca * cy
        doc = page.parent
        xrefs = page.get_contents()
        if xrefs:
            first, last = xrefs[0], xrefs[-1]
            pre = f"q {ca:.6f} {sa:.6f} {-sa:.6f} {ca:.6f} {e:.4f} {f:.4f} cm\n".encode()
            doc.update_stream(first, pre + (doc.xref_stream(first) or b""))
            doc.update_stream(last, (doc.xref_stream(last) or b"") + b"\nQ\n")


def _drop_unused_images(page: pymupdf.Page) -> None:
    """Toglie dalle risorse le immagini non più usate dal contenuto (la scansione originale)."""
    doc = page.parent
    try:
        content = b"".join(doc.xref_stream(x) or b"" for x in page.get_contents())
        res_xref = None
        kind, val = doc.xref_get_key(page.xref, "Resources")
        if kind == "xref":
            res_xref = int(val.split()[0])
        target = res_xref or page.xref
        prefix = "" if res_xref else "Resources/"
        kind, xobjs = doc.xref_get_key(target, prefix + "XObject")
        if kind != "dict":
            return
        import re
        for name in re.findall(r"/([^\s/<>\[\]()]+)\s+\d+\s+0\s+R", xobjs):
            if ("/" + name).encode() not in content:
                doc.xref_set_key(target, f"{prefix}XObject/{name}", "null")
    except Exception:
        pass


def _box_display(res: PageResult, box) -> pymupdf.Rect:
    x0, y0, x1, y1 = box
    pts = [res.processed_to_display(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    return pymupdf.Rect(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


LANG_TAGS = {"ita": "it-IT", "eng": "en-US", "deu": "de-DE", "fra": "fr-FR", "spa": "es-ES", "por": "pt-PT",
             "nld": "nl-NL", "pol": "pl-PL", "rus": "ru-RU", "ell": "el-GR", "ara": "ar", "heb": "he-IL",
             "chi_sim": "zh-CN", "chi_tra": "zh-TW", "jpn": "ja-JP", "kor": "ko-KR", "hin": "hi-IN"}


def _record_ocr(pdf: pymupdf.Document, result: OcrResult) -> None:
    """Registra l'OCR nei metadati (strumento, lingue, data) e imposta /Lang se assente."""
    import datetime
    try:
        meta = dict(pdf.metadata or {})
        note = "Foliant OCR ({l}, {d})".format(l="+".join(result.options.languages),
                                               d=datetime.date.today().isoformat())
        kw = meta.get("keywords") or ""
        if "Foliant OCR" not in kw:
            meta["keywords"] = (kw + "; " if kw else "") + note
        if not meta.get("creator"):
            meta["creator"] = "Foliant"
        clean = {k: v for k, v in meta.items() if k in ("author", "producer", "creator", "title", "format",
                                                          "encryption", "creationDate", "modDate", "subject",
                                                          "keywords", "trapped")}
        clean.pop("format", None)
        clean.pop("encryption", None)
        pdf.set_metadata(clean)
    except Exception:
        pass
    try:
        cat = pdf.pdf_catalog()
        kind, _val = pdf.xref_get_key(cat, "Lang")
        main = result.options.languages[0] if result.options.languages else ""
        if kind == "null" and main in LANG_TAGS:
            pdf.xref_set_key(cat, "Lang", pymupdf.get_pdf_str(LANG_TAGS[main]))
    except Exception:
        pass


def apply_result(pdf: pymupdf.Document, result: OcrResult, mode: str | None = None) -> int:
    """Applica tutte le pagine riconosciute. Restituisce il numero di pagine modificate."""
    mode = mode or result.options.mode
    n = 0
    ocg = 0
    if mode == EDITABLE and any(r.original for r in result.recognized):
        ocg = pdf.add_ocg(_("Scansione originale"), on=False)
    for res in result.recognized:
        if not res.lines and not res.rasterize and not res.remove_layer:
            continue
        apply_page(pdf, res, mode, result.options.font, result.options.apply_to_page, ocg)
        n += 1
    if n:
        _record_ocr(pdf, result)
    if n and mode == SEARCHABLE:
        try:
            pdf.subset_fonts()
        except Exception:
            pass
    return n


# ===================================================================== API stabile
def available_languages() -> list[str]:
    """Codici delle lingue di riconoscimento installate (es. ["ita", "eng"])."""
    from .languages import available_languages as _avail
    return _avail()


def default_languages() -> list[str]:
    langs = available_languages()
    preferred = [c for c in ("ita", "eng") if c in langs]
    return preferred or langs[:1]


def ocr_pdf_bytes(data: bytes, languages: list[str], mode: str, pages: list[int] | None = None,
                  progress=None, options: OcrOptions | None = None) -> bytes:
    """Esegue l'OCR su un PDF in memoria e restituisce il nuovo PDF.

    `mode` è "searchable" (testo invisibile, aspetto identico) o "editable"
    (testo vero e visibile su sfondo ripulito). `pages` = indici da 0 (None =
    tutte). `progress` (facoltativo) è un oggetto con `update(frazione, testo)`
    e `cancelled`. Le pagine che hanno già testo vengono saltate (vedi
    `OcrOptions.existing_text`).
    """
    if mode not in (SEARCHABLE, EDITABLE):
        raise ValueError(_("Modalità OCR non valida: {m}").format(m=mode))
    opts = options or OcrOptions()
    opts.languages = list(languages or default_languages())
    opts.mode = mode
    from .languages import check_languages
    check_languages(opts.languages)
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise OcrError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
        if pdf.needs_pass:
            pdf.close()
            raise OcrError(_("Il PDF è protetto da password: aprilo in Foliant per riconoscerlo."))
    try:
        result = recognize_document(pdf, opts, pages, progress)
        with ENGINE_LOCK:
            apply_result(pdf, result, mode)
            return pdf.tobytes(garbage=3, deflate=True)
    finally:
        with ENGINE_LOCK:
            pdf.close()


def ocr_page_text(source, page: int = 0, languages: list[str] | None = None, clip=None, dpi: int = 300,
                  preprocess: PreprocessOptions | None = None, progress=None) -> str:
    """Testo riconosciuto di una pagina (o di un'area `clip`, in punti della pagina visualizzata).

    `source` può essere: bytes di un PDF, `pymupdf.Document`, `pymupdf.Page`,
    `foliant.api.Document`, oppure un'immagine numpy (RGB o grigi; `dpi` ne
    indica la risoluzione).
    """
    languages = list(languages or default_languages())
    from .languages import check_languages
    check_languages(languages)
    cancelled = (lambda: bool(getattr(progress, "cancelled", False))) if progress is not None else None
    if isinstance(source, np.ndarray):
        img, eff_dpi = source, dpi
    else:
        close = None
        with ENGINE_LOCK:
            if isinstance(source, (bytes, bytearray)):
                doc = close = pymupdf.open(stream=bytes(source), filetype="pdf")
                pg = doc[page]
            elif isinstance(source, pymupdf.Page):
                pg = source
            elif isinstance(source, pymupdf.Document):
                pg = source[page]
            elif hasattr(source, "pdf"):
                pg = source.pdf[page]
            else:
                raise TypeError("sorgente non supportata")
            r = pymupdf.Rect(clip) if clip is not None else None
            img, zoom, _n = render_page(pg, dpi, r)
            eff_dpi = zoom * 72
            if close is not None:
                close.close()
    popts = preprocess or PreprocessOptions()
    if clip is not None and preprocess is None:
        # area piccola: la rotazione automatica non è affidabile
        popts = PreprocessOptions(auto_rotate=False)
    if img.shape[0] < 40 or img.shape[1] < 40:
        pad = 40
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    lines, _prep, _c = recognize_array(img, languages, eff_dpi, popts, psm=3 if clip is None else 6,
                                       cancelled=cancelled)
    res = PageResult(index=page, lines=lines)
    if progress is not None:
        progress.update(1.0)
    return res.text


def replace_word(page: pymupdf.Page, rect: pymupdf.Rect, new_text: str, family: str = "sans") -> bool:
    """Corregge una parola già inserita dall'OCR (visibile o invisibile) nell'area `rect`.

    Il testo nell'area viene rimosso con la redazione (immagini e grafica
    restano intatte) e reinserito con lo stesso corpo, colore, modalità di
    resa e direzione. Restituisce False se nell'area non c'è testo.
    """
    rect = pymupdf.Rect(rect)
    target = None
    for span in page.get_texttrace():
        if pymupdf.Rect(span["bbox"]).intersects(rect):
            inter = pymupdf.Rect(span["bbox"]) & rect
            if target is None or abs(inter) > abs(pymupdf.Rect(target["bbox"]) & rect):
                target = span
    if target is None:
        return False
    chars = [c for c in target["chars"] if pymupdf.Rect(c[3]).intersects(rect) and
             abs(pymupdf.Rect(c[3]) & rect) > 0.3 * abs(pymupdf.Rect(c[3]))]
    origin = pymupdf.Point(chars[0][2]) if chars else pymupdf.Point(rect.x0, rect.y1)
    size = float(target["size"])
    invisible = target["type"] == 3
    color = tuple(target.get("color") or (0, 0, 0))
    dx, dy = target.get("dir", (1, 0))
    shrink = pymupdf.Rect(rect.x0 + 0.3, rect.y0 + 0.3, rect.x1 - 0.3, rect.y1 - 0.3)
    page.add_redact_annot(shrink)
    kwargs = {"images": pymupdf.PDF_REDACT_IMAGE_NONE}
    if hasattr(pymupdf, "PDF_REDACT_LINE_ART_NONE"):
        kwargs["graphics"] = pymupdf.PDF_REDACT_LINE_ART_NONE
    page.apply_redactions(**kwargs)
    text = unicodedata.normalize("NFC", new_text.strip())
    if not text:
        return True
    if family == "auto":
        name = str(target.get("font", ""))
        family = "serif" if "Serif" in name else "mono" if "Mono" in name else "sans"
        bold = "Bold" in name
        italic = "Italic" in name
        family = font_key(family, bold, italic)
    font = get_font("sans" if invisible else family)
    if invisible and chars:
        natural = font.text_length(text, fontsize=size) or 1.0
        size = min(max(size * rect.width / natural, size * 0.6), size * 1.4)
    tw = pymupdf.TextWriter(page.rect)
    tw.append(origin, text, font=font, fontsize=size)
    angle = -math.degrees(math.atan2(dy, dx))
    kw = {"render_mode": 3 if invisible else 0, "color": color}
    if abs(angle) > 0.01:
        kw["morph"] = (origin, pymupdf.Matrix(angle))
    placement.write_text(page, tw, **kw)
    return True


def parse_pages(text: str, count: int) -> list[int]:
    """Intervallo di pagine scritto dall'utente → indici da 0.

    Sintassi: «1-3, 7, 10-fine», «pari», «dispari», «tutte». Solleva
    ValueError con un messaggio chiaro se l'espressione non è valida.
    """
    t = (text or "").strip().lower()
    if t in ("", "tutte", "all"):
        return list(range(count))
    if t in ("pari", "even"):
        return list(range(1, count, 2))
    if t in ("dispari", "odd"):
        return list(range(0, count, 2))
    out: list[int] = []
    for part in t.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split("-")]
        if len(bits) == 2 and bits[1] == "":
            bits[1] = "fine"          # «10-» = fino all'ultima pagina
        try:
            ends = [count if b in ("fine", "end", "ultima") else int(b) for b in bits]
        except ValueError:
            raise ValueError(_("Intervallo non valido: «{p}».").format(p=part)) from None
        if len(ends) == 1:
            ends = ends * 2
        if len(ends) != 2 or min(ends) < 1 or max(ends) > count or ends[0] > ends[1]:
            raise ValueError(_("Pagine fuori intervallo: «{p}» (il documento ha {n} pagine).").format(p=part, n=count))
        a, b = ends
        for n in range(a, b + 1):
            if n - 1 not in out:
                out.append(n - 1)
    if not out:
        raise ValueError(_("Nessuna pagina indicata."))
    return out


def format_pages(indices: list[int]) -> str:
    """Indici da 0 → «3, 7, 9-12»."""
    nums = sorted({i + 1 for i in indices})
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def char_accuracy(got: str, expected: str) -> float:
    """1 − distanza di edit / lunghezza del testo atteso (spazi e righe normalizzati, NFC)."""
    def norm(s: str) -> str:
        return " ".join(unicodedata.normalize("NFC", s).split())
    a, b = norm(got), norm(expected)
    if not b:
        return 1.0 if not a else 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return max(0.0, 1.0 - prev[-1] / len(b))
