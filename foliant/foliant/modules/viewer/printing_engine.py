"""Logica di stampa senza widget.

- `parse_page_ranges`: "1-3, 5, 8-" → indici di pagina.
- `print_capability`: stampa consentita? a quale risoluzione massima (VIS-06)?
- `placement`: scala, centratura e rotazione automatica della pagina sul foglio.
- `print_document`: disegna le pagine su un `QPagedPaintDevice` (QPrinter,
  QPdfWriter). Non tocca widget: può girare in `run_task`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPagedPaintDevice, QPainter

from foliant.api import ENGINE_LOCK, _

from .properties_engine import document_security

DEFAULT_MAX_DPI = 300
LOW_QUALITY_DPI = 150

FIT_PAGE = "fit"
ACTUAL_SIZE = "actual"

LAYERS_PRINT = "print"     # /Usage /Print del documento (predefinito)
LAYERS_SCREEN = "screen"   # come a schermo


class RangeError(ValueError):
    """Intervallo di pagine non valido (messaggio per l'utente)."""


# ---------------------------------------------------------------- intervalli
_PART_RE = re.compile(r"^(\d*)-(\d*)$")


def parse_page_ranges(text: str, page_count: int) -> list[int]:
    """Converte "1-3, 5, 8-" in indici 0-based nell'ordine indicato.

    Forme ammesse: "N", "N-M", "N-" (fino alla fine), "-M" (dall'inizio),
    "M-N" con M>N (ordine inverso). Separatori: virgola, punto e virgola, spazio.
    """
    text = (text or "").strip()
    if not text:
        raise RangeError(_("Indica le pagine da stampare."))
    pages: list[int] = []
    normalized = re.sub(r"\s*[-–]\s*", "-", text)
    tokens = [t for t in re.split(r"[,;\s]+", normalized) if t]
    for raw in tokens:
        token = raw.strip()
        if token.isdigit():
            start = end = int(token)
        else:
            m = _PART_RE.match(token)
            if not m or (not m.group(1) and not m.group(2)):
                raise RangeError(_("Intervallo non valido: «{x}».").format(x=token))
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else page_count
        for n in (start, end):
            if n < 1 or n > page_count:
                raise RangeError(_("La pagina {n} è fuori intervallo: il documento ha {t} pagine.")
                                 .format(n=n, t=page_count))
        step = 1 if end >= start else -1
        pages.extend(i - 1 for i in range(start, end + step, step))
    return pages


# ------------------------------------------------------------------ permessi
@dataclass
class PrintCapability:
    allowed: bool
    max_dpi: int
    reason: str = ""     # motivo per il suggerimento (vuoto se stampa piena)


def print_capability(permissions: int) -> PrintCapability:
    """Bit 3 (/P) = stampa; bit 12 = alta qualità (altrimenti ≤150 dpi)."""
    if not permissions & pymupdf.PDF_PERM_PRINT:
        return PrintCapability(False, 0, _("Le impostazioni di protezione del documento non consentono la stampa."))
    if not permissions & pymupdf.PDF_PERM_PRINT_HQ:
        return PrintCapability(True, LOW_QUALITY_DPI,
                               _("Il documento consente solo la stampa a bassa qualità ({dpi} dpi).")
                               .format(dpi=LOW_QUALITY_DPI))
    return PrintCapability(True, DEFAULT_MAX_DPI)


def document_print_capability(document) -> PrintCapability:
    with ENGINE_LOCK:
        perms = document_security(document).permissions
    return print_capability(perms)


def effective_dpi(device_dpi: int, max_dpi: int) -> int:
    return max(36, min(int(device_dpi or max_dpi), int(max_dpi)))


# ------------------------------------------------------------- impaginazione
@dataclass
class Placement:
    rotate: bool       # pagina ruotata di 90° per adattarsi al foglio
    scale: float       # punti pagina → punti foglio
    x: float           # rettangolo occupato sul foglio (punti, prima della rotazione)
    y: float
    width: float
    height: float


def placement(page_w: float, page_h: float, paper_w: float, paper_h: float,
              scaling: str = FIT_PAGE, auto_rotate: bool = True) -> Placement:
    """Posizione della pagina nell'area stampabile (tutto in punti).

    Con `auto_rotate` una pagina orizzontale su foglio verticale (o viceversa)
    viene ruotata di 90°. `FIT_PAGE` adatta all'area; `ACTUAL_SIZE` mantiene la
    scala 1:1 (riducendo solo se non entra). Il risultato è centrato.
    """
    rotate = bool(auto_rotate and abs(page_w - page_h) > 0.5 and abs(paper_w - paper_h) > 0.5
                  and (page_w > page_h) != (paper_w > paper_h))
    w, h = (page_h, page_w) if rotate else (page_w, page_h)
    fit = min(paper_w / w, paper_h / h) if w > 0 and h > 0 else 1.0
    scale = fit if scaling == FIT_PAGE else min(1.0, fit)
    out_w, out_h = w * scale, h * scale
    return Placement(rotate, scale, (paper_w - out_w) / 2, (paper_h - out_h) / 2, out_w, out_h)


# ------------------------------------------------------------------- opzioni
@dataclass
class PrintOptions:
    pages: list[int] = field(default_factory=list)   # indici 0-based, nell'ordine
    copies: int = 1
    collate: bool = True
    scaling: str = FIT_PAGE
    annotations: bool = True
    auto_rotate: bool = True
    layers: str = LAYERS_PRINT
    max_dpi: int = DEFAULT_MAX_DPI

    def sequence(self) -> list[int]:
        n = max(1, int(self.copies))
        if self.collate:
            return list(self.pages) * n
        return [p for p in self.pages for _i in range(n)]


# ------------------------------------------------------------------ rendering
def _print_copy(document, layers_mode: str) -> pymupdf.Document:
    """Copia di lavoro del documento, con lo stato dei livelli da stampare.

    La visibilità a schermo (VIS-20) vive solo in memoria: la si riporta sulla
    copia, poi con `LAYERS_PRINT` si applica /Usage /Print /PrintState.
    L'originale non viene toccato."""
    with ENGINE_LOCK:
        pdf = document.pdf
        try:
            screen = {x: bool(v.get("on")) for x, v in (pdf.get_ocgs() or {}).items()}
        except Exception:
            screen = {}
        data = pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
        work = pymupdf.open(stream=data, filetype="pdf")
        if not screen:
            return work
        states = dict(screen)
        if layers_mode == LAYERS_PRINT:
            states.update(print_layer_states(work))
        try:
            work.set_layer(-1, on=sorted(x for x, on in states.items() if on),
                           off=sorted(x for x, on in states.items() if not on))
            # il motore legge la configurazione dei livelli all'apertura
            changed = work.tobytes(garbage=0, deflate=False)
        except Exception:
            return work
        work.close()
        return pymupdf.open(stream=changed, filetype="pdf")


def print_layer_states(pdf: pymupdf.Document) -> dict[int, bool]:
    """{xref livello: visibile} secondo /Usage /Print /PrintState (ENGINE_LOCK richiesto)."""
    result: dict[int, bool] = {}
    try:
        ocgs = pdf.get_ocgs() or {}
    except Exception:
        return result
    for xref in ocgs:
        try:
            kind, value = pdf.xref_get_key(xref, "Usage/Print/PrintState")
        except Exception:
            continue
        if kind == "name" and value.strip("/").upper() in ("ON", "OFF"):
            result[xref] = value.strip("/").upper() == "ON"
    return result


def render_page(pdf: pymupdf.Document, index: int, dpi: int, annotations: bool) -> QImage:
    """Immagine della pagina (con /Rotate del documento, mai la rotazione della vista)."""
    from foliant.api import page_pixmap
    with ENGINE_LOCK:
        zoom = dpi / 72.0
        # senza il segnaposto dei campi firma vuoti: non esiste nel file e non va su carta
        pix = page_pixmap(pdf, index, hide_unsigned_signatures=True,
                          matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=annotations)
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


def print_document(document, device: QPagedPaintDevice, options: PrintOptions, progress=None) -> int:
    """Disegna le pagine scelte su `device`. Restituisce il numero di fogli.

    Non usa widget: si può chiamare da `run_task`. `progress.update()` solleva
    `OperationCancelled` se l'utente annulla.
    """
    sequence = options.sequence()
    if not sequence:
        raise RangeError(_("Nessuna pagina da stampare."))
    work = _print_copy(document, options.layers)
    painter = QPainter()
    try:
        if not painter.begin(device):
            raise RuntimeError(_("Impossibile avviare la stampa sul dispositivo scelto."))
        resolution = device.resolution() or 72
        dpi = effective_dpi(resolution, options.max_dpi)
        paint = device.pageLayout().paintRectPixels(resolution)
        px_per_pt = resolution / 72.0
        paper_w, paper_h = paint.width() / px_per_pt, paint.height() / px_per_pt
        total = len(sequence)
        for n, index in enumerate(sequence):
            if progress is not None:
                progress.update(n / total, _("Pagina {n} di {t}").format(n=n + 1, t=total))
            if n:
                device.newPage()
            with ENGINE_LOCK:
                rect = work[index].rect
            place = placement(rect.width, rect.height, paper_w, paper_h, options.scaling, options.auto_rotate)
            image = render_page(work, index, dpi, options.annotations)
            target = QRectF(place.x * px_per_pt, place.y * px_per_pt,
                            place.width * px_per_pt, place.height * px_per_pt)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setClipRect(QRectF(0, 0, paint.width(), paint.height()))
            if place.rotate:
                # ruota di 90° in senso orario attorno al centro del riquadro
                painter.translate(target.center())
                painter.rotate(90)
                painter.drawImage(QRectF(-target.height() / 2, -target.width() / 2,
                                         target.height(), target.width()), image)
            else:
                painter.drawImage(target, image)
            painter.restore()
        if progress is not None:
            progress.update(1.0, _("Invio alla stampante…"))
        return total
    except BaseException:
        abort = getattr(device, "abort", None)   # QPrinter: niente lavoro parziale
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        raise
    finally:
        if painter.isActive():
            painter.end()
        with ENGINE_LOCK:
            work.close()
