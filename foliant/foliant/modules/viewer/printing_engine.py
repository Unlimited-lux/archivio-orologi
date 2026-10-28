"""Logica di stampa senza widget.

- `parse_page_ranges`: "1-3, 5, 8-" → indici di pagina.
- `print_capability`: stampa consentita? a quale risoluzione massima (VIS-06)?
- `placement`: scala, centratura e rotazione automatica della pagina sul foglio.
- `print_document`: disegna le pagine su un `QPagedPaintDevice` (QPrinter,
  QPdfWriter). Non tocca widget: può girare in `run_task`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import pymupdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPagedPaintDevice, QPainter

from foliant.api import ENGINE_LOCK, _


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
    return print_capability(document.permissions)   # permessi effettivi del nucleo


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
    duplex: str = ""        # preferenza del documento (/ViewerPreferences /Duplex), "" = stampante
    layout: str = "normal"  # LAYOUT_NORMAL, LAYOUT_BOOKLET o LAYOUT_POSTER (VIS-44)
    booklet_rtl: bool = False          # opuscolo rilegato a destra
    poster_overlap_mm: float = 10.0    # sovrapposizione tra i fogli del poster
    pages_per_sheet: int = 1           # più pagine per foglio (VIS-43): 1, 2, 4, 6, 9 o 16
    nup_by_columns: bool = False       # ordine per colonne invece che per righe
    nup_border: bool = False           # bordo attorno a ogni pagina
    grayscale: bool = False            # scala di grigi invece del colore

    def sequence(self) -> list[int]:
        n = max(1, int(self.copies))
        if self.collate:
            return list(self.pages) * n
        return [p for p in self.pages for _i in range(n)]


# ------------------------------------------------------------------ rendering
# ------------------------------------------------------------- opuscolo e poster (VIS-44)
LAYOUT_NORMAL = "normal"
LAYOUT_BOOKLET = "booklet"
LAYOUT_POSTER = "poster"
MM = 72 / 25.4


def bake_for_imposition(pdf: pymupdf.Document, annotations: bool) -> None:
    """L'imposizione copia il contenuto delle pagine, non le annotazioni: sulla copia di stampa
    si tolgono quelle che non vanno su carta (`not_printed`) e le altre diventano contenuto."""
    for i in range(pdf.page_count):
        page = pdf[i]
        for xref in not_printed(pdf, i, annotations):
            try:
                annot = page.load_annot(xref)
            except Exception:
                annot = None
            if annot is not None:
                page.delete_annot(annot)
    try:
        pdf.bake(annots=True, widgets=True)
    except Exception:
        pass


def booklet_sides(count: int, rtl: bool = False) -> list[tuple[int | None, int | None]]:
    """Facciate di un opuscolo per `count` pagine: coppie (sinistra, destra) di indici 0-based
    nell'ordine di stampa fronte/retro; None = pagina bianca aggiunta (multipli di 4).
    Con 8 pagine: (8,1), (2,7), (6,3), (4,5) in numeri da 1."""
    if count <= 0:
        return []
    total = -(-count // 4) * 4
    sides = []
    for k in range(total // 4):
        front = (total - 1 - 2 * k, 2 * k)
        back = (2 * k + 1, total - 2 - 2 * k)
        for left, right in (front, back):
            if rtl:
                left, right = right, left
            sides.append((left if left < count else None, right if right < count else None))
    return sides


def impose_booklet(pdf: pymupdf.Document, pages: list[int], rtl: bool = False) -> pymupdf.Document:
    """Documento con le facciate dell'opuscolo: ogni facciata è un foglio orizzontale con due
    pagine affiancate, ciascuna adattata alla propria metà (ENGINE_LOCK richiesto)."""
    width = max(pdf[i].rect.width for i in pages)
    height = max(pdf[i].rect.height for i in pages)
    out = pymupdf.open()
    for left, right in booklet_sides(len(pages), rtl):
        sheet = out.new_page(width=2 * width, height=height)
        for slot, pos in ((left, 0), (right, 1)):
            if slot is None:
                continue
            sheet.show_pdf_page(pymupdf.Rect(pos * width, 0, (pos + 1) * width, height), pdf, pages[slot],
                                keep_proportion=True)
    return out


def poster_tiles(page_w: float, page_h: float, paper_w: float, paper_h: float,
                 overlap: float) -> list[pymupdf.Rect]:
    """Riquadri della pagina (in punti) stampati su ciascun foglio, a scala 1: fogli adiacenti
    condividono una fascia larga `overlap`. Pagina già più piccola del foglio → un solo foglio."""
    if page_w <= paper_w and page_h <= paper_h:
        return [pymupdf.Rect(0, 0, page_w, page_h)]
    overlap = max(0.0, min(overlap, paper_w / 2, paper_h / 2))

    def starts(total: float, size: float) -> list[float]:
        if total <= size:
            return [0.0]
        step = size - overlap
        n = -(-(total - overlap) // step)
        return [min(i * step, total - size) for i in range(int(n))]
    return [pymupdf.Rect(x, y, min(x + paper_w, page_w), min(y + paper_h, page_h))
            for y in starts(page_h, paper_h) for x in starts(page_w, paper_w)]


def impose_poster(pdf: pymupdf.Document, pages: list[int], paper_w: float, paper_h: float,
                  overlap_mm: float = 10.0) -> pymupdf.Document:
    """Documento con i fogli del poster (scala 100%), con i segni di taglio sul bordo della
    fascia di sovrapposizione (ENGINE_LOCK richiesto)."""
    overlap = overlap_mm * MM
    out = pymupdf.open()
    for index in pages:
        rect = pdf[index].rect
        tiles = poster_tiles(rect.width, rect.height, paper_w, paper_h, overlap)
        for tile in tiles:
            sheet = out.new_page(width=paper_w, height=paper_h)
            target = pymupdf.Rect(0, 0, tile.width, tile.height)
            sheet.show_pdf_page(target, pdf, index, clip=tile)
            if len(tiles) > 1:
                shape = sheet.new_shape()
                mark = 4 * MM
                if tile.x0 > 0:                          # fascia condivisa con il foglio a sinistra
                    x = overlap
                    for y in (0, target.height - mark):
                        shape.draw_line((x, y), (x, y + mark))
                if tile.y0 > 0:
                    y = overlap
                    for x in (0, target.width - mark):
                        shape.draw_line((x, y), (x + mark, y))
                shape.finish(color=(0, 0, 0), width=0.3)
                shape.commit()
    return out


# ---------------------------------------------------- più pagine per foglio (VIS-43)
PAGES_PER_SHEET = (1, 2, 4, 6, 9, 16)


def nup_grid(n: int, page_w: float, page_h: float, paper_w: float, paper_h: float) -> tuple[int, int]:
    """(colonne, righe) per `n` pagine sul foglio: la disposizione che le stampa più grandi."""
    best, best_scale = (n, 1), -1.0
    for cols in range(1, n + 1):
        if n % cols:
            continue
        rows = n // cols
        scale = min(paper_w / cols / page_w, paper_h / rows / page_h)
        if scale > best_scale + 1e-9:
            best, best_scale = (cols, rows), scale
    return best


def nup_sheet_orientation(n: int, page_w: float, page_h: float) -> bool:
    """True se conviene il foglio orizzontale: con 2 e 6 pagine per foglio le pagine
    verticali stanno meglio affiancate su un foglio orizzontale (e viceversa)."""
    portrait = page_h >= page_w
    return portrait if n in (2, 6) else not portrait


def impose_nup(pdf: pymupdf.Document, pages: list[int], n: int, paper_w: float, paper_h: float,
               by_columns: bool = False, border: bool = False) -> pymupdf.Document:
    """Documento con `n` pagine per foglio, ciascuna adattata e centrata nella sua cella,
    in ordine per righe o per colonne (ENGINE_LOCK richiesto)."""
    width = max(pdf[i].rect.width for i in pages)
    height = max(pdf[i].rect.height for i in pages)
    cols, rows = nup_grid(n, width, height, paper_w, paper_h)
    cell_w, cell_h = paper_w / cols, paper_h / rows
    gap = 2 * MM if n > 1 else 0
    out = pymupdf.open()
    for start in range(0, len(pages), n):
        sheet = out.new_page(width=paper_w, height=paper_h)
        shape = sheet.new_shape() if border else None
        for k, index in enumerate(pages[start:start + n]):
            col, row = (k // rows, k % rows) if by_columns else (k % cols, k // cols)
            cell = pymupdf.Rect(col * cell_w + gap, row * cell_h + gap,
                                (col + 1) * cell_w - gap, (row + 1) * cell_h - gap)
            rect = pdf[index].rect
            scale = min(cell.width / rect.width, cell.height / rect.height)
            w, h = rect.width * scale, rect.height * scale
            target = pymupdf.Rect(cell.x0 + (cell.width - w) / 2, cell.y0 + (cell.height - h) / 2,
                                  cell.x0 + (cell.width + w) / 2, cell.y0 + (cell.height + h) / 2)
            sheet.show_pdf_page(target, pdf, index, keep_proportion=True)
            if shape is not None:
                shape.draw_rect(target)
        if shape is not None:
            shape.finish(color=(0, 0, 0), width=0.5)
            shape.commit()
    return out


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


def not_printed(pdf: pymupdf.Document, index: int, annotations: bool) -> list[int]:
    """Annotazioni della pagina che non vanno su carta (VIS-43): quelle senza il flag /Print e,
    senza «annotazioni e commenti», tutte tranne i campi modulo, i cui valori sono contenuto
    del documento (ENGINE_LOCK richiesto)."""
    hide = []
    for xref, kind, _id in pdf[index].annot_xrefs():
        try:
            k, value = pdf.xref_get_key(xref, "F")
            flags = int(value) if k == "int" else 0
        except Exception:
            flags = 0
        if not flags & pymupdf.PDF_ANNOT_IS_PRINT or (not annotations and kind != pymupdf.PDF_ANNOT_WIDGET):
            hide.append(xref)
    return hide


def render_page(pdf: pymupdf.Document, index: int, dpi: int, annotations: bool) -> QImage:
    """Immagine della pagina (con /Rotate del documento, mai la rotazione della vista)."""
    from foliant.api import annotations_hidden, page_pixmap
    with ENGINE_LOCK:
        zoom = dpi / 72.0
        # la resa a schermo ignora /Print: le annotazioni da non stampare si nascondono per la
        # durata della resa (sulla copia di stampa); i campi modulo restano anche senza commenti
        with annotations_hidden(pdf, not_printed(pdf, index, annotations)):
            # senza il segnaposto dei campi firma vuoti: non esiste nel file e non va su carta
            pix = page_pixmap(pdf, index, hide_unsigned_signatures=True,
                              matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
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
    nup = options.layout == LAYOUT_NORMAL and options.pages_per_sheet > 1
    if options.layout in (LAYOUT_BOOKLET, LAYOUT_POSTER) or nup:
        paint0 = device.pageLayout().paintRectPoints()
        with ENGINE_LOCK:
            bake_for_imposition(work, options.annotations)
            if nup:
                imposed = impose_nup(work, list(options.pages), options.pages_per_sheet, paint0.width(),
                                     paint0.height(), options.nup_by_columns, options.nup_border)
            elif options.layout == LAYOUT_BOOKLET:
                imposed = impose_booklet(work, list(options.pages), options.booklet_rtl)
            else:
                imposed = impose_poster(work, list(options.pages), paint0.width(), paint0.height(),
                                        options.poster_overlap_mm)
            work.close()
        work = imposed
        sheets = list(range(work.page_count))
        n_copies = max(1, int(options.copies))
        sequence = sheets * n_copies if options.collate else [s for s in sheets for _i in range(n_copies)]
        options = replace(options, scaling=FIT_PAGE if options.layout == LAYOUT_BOOKLET else ACTUAL_SIZE,
                          auto_rotate=options.layout == LAYOUT_BOOKLET)
        if nup:            # il foglio è già della misura della carta
            options = replace(options, scaling=FIT_PAGE, auto_rotate=False)
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
            if options.grayscale:
                image = image.convertToFormat(QImage.Format.Format_Grayscale8)
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
