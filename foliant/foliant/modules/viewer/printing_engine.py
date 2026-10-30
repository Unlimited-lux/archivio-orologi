"""Logica di stampa senza widget.

- `parse_page_ranges`: "1-3, 5, 8-" → indici di pagina.
- `print_capability`: stampa consentita? a quale risoluzione massima (VIS-06)?
- `placement`: scala, centratura e rotazione automatica della pagina sul foglio.
- `sheets_for`: i fogli come escono dalla stampante (intervallo, pari/dispari, più pagine per
  foglio, opuscolo, poster, fronte/retro, ordine inverso); funzione pura, la usano la stampa
  e l'anteprima del dialogo.
- `Imposition`: geometria di ogni facciata (pagine nelle celle, riquadri del poster), comune
  all'imposizione con il motore e al disegno dell'anteprima.
- `print_document`: disegna le pagine su un `QPagedPaintDevice` (QPrinter,
  QPdfWriter). Non tocca widget: può girare in `run_task`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

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

# pagine dell'intervallo da stampare (numero di pagina del documento, da 1)
PAGES_ALL = "all"
PAGES_ODD = "odd"
PAGES_EVEN = "even"

# cosa stampare
CONTENT_ALL = "all"             # documento e commenti
CONTENT_DOCUMENT = "document"   # solo il documento (i valori dei campi modulo sono contenuto)
CONTENT_FIELDS = "fields"       # solo campi e timbri, senza il contenuto delle pagine (moduli prestampati)

# fronte/retro (valori di /ViewerPreferences /Duplex; "" = come la stampante)
SIMPLEX = "Simplex"
DUPLEX_LONG = "DuplexFlipLongEdge"
DUPLEX_SHORT = "DuplexFlipShortEdge"


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
    annotations: bool = True           # commenti sì/no con «Documento e commenti» (vedi `print_content`)
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
    page_set: str = PAGES_ALL          # solo dispari o solo pari, dopo l'intervallo
    reverse: bool = False              # fogli in ordine inverso (l'ultimo per primo)
    content: str = CONTENT_ALL         # cosa stampare: CONTENT_ALL, CONTENT_DOCUMENT o CONTENT_FIELDS

    def sequence(self) -> list[int]:
        n = max(1, int(self.copies))
        if self.collate:
            return list(self.pages) * n
        return [p for p in self.pages for _i in range(n)]


def content_mode(annotations) -> str:
    """Cosa stampare da un valore booleano (annotazioni sì/no) o da una costante CONTENT_*."""
    if annotations is True:
        return CONTENT_ALL
    if annotations is False or annotations is None:
        return CONTENT_DOCUMENT
    return annotations


def print_content(options: PrintOptions) -> str:
    """Cosa stampare: «Documento e commenti» senza `annotations` equivale a «Solo il documento»."""
    if options.content == CONTENT_ALL and not options.annotations:
        return CONTENT_DOCUMENT
    return options.content


# ------------------------------------------------------------------ rendering
# ------------------------------------------------------------- opuscolo e poster (VIS-44)
LAYOUT_NORMAL = "normal"
LAYOUT_BOOKLET = "booklet"
LAYOUT_POSTER = "poster"
MM = 72 / 25.4


def bake_for_imposition(pdf: pymupdf.Document, annotations) -> None:
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
    options = PrintOptions(pages=list(pages), layout=LAYOUT_BOOKLET, booklet_rtl=rtl)
    imposition = Imposition(options, page_sizes(pdf), 0, 0)
    return impose_sides(pdf, imposition, layout_sides(options, pdf.page_count))


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
    options = PrintOptions(pages=list(pages), layout=LAYOUT_POSTER, poster_overlap_mm=overlap_mm)
    imposition = Imposition(options, page_sizes(pdf), paper_w, paper_h)
    return impose_sides(pdf, imposition, layout_sides(options, pdf.page_count, imposition.tile_count))


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
    options = PrintOptions(pages=list(pages), pages_per_sheet=n, nup_by_columns=by_columns, nup_border=border)
    imposition = Imposition(options, page_sizes(pdf), paper_w, paper_h)
    return impose_sides(pdf, imposition, layout_sides(options, pdf.page_count))


def sheet_landscape(options: PrintOptions, page_w: float, page_h: float) -> bool | None:
    """Orientamento della carta per il lavoro dalla prima pagina stampata (`page_w` × `page_h`):
    True = orizzontale, None = quello della stampante (poster)."""
    if options.layout == LAYOUT_BOOKLET:      # due pagine affiancate per foglio
        return page_h >= page_w
    if options.layout == LAYOUT_POSTER:
        return None
    if options.pages_per_sheet > 1:
        return nup_sheet_orientation(options.pages_per_sheet, page_w, page_h)
    if options.auto_rotate:
        return page_w > page_h
    return None


# ------------------------------------------------- pagine, facciate e fogli (VIS-43)
@dataclass(frozen=True)
class Side:
    """Facciata di un foglio. `index`: posizione nell'impaginazione (facciate in ordine naturale,
    prima dell'ordine inverso), None per la facciata bianca aggiunta dal fronte/retro; `pages`:
    pagine 0-based nelle celle, None = cella vuota (opuscolo); `tile`: riquadro del poster."""
    index: int | None
    pages: tuple[int | None, ...] = ()
    tile: int = 0

    @property
    def blank(self) -> bool:
        return self.index is None


@dataclass(frozen=True)
class Sheet:
    """Foglio come esce dalla stampante: il fronte e, in fronte/retro, il retro."""
    front: Side
    back: Side | None = None

    @property
    def sides(self) -> tuple[Side, ...]:
        return (self.front,) if self.back is None else (self.front, self.back)


def selected_pages(options: PrintOptions, page_count: int | None = None) -> list[int]:
    """Pagine da stampare nell'ordine dell'intervallo, poi solo le dispari o solo le pari:
    conta il numero di pagina del documento (1, 3, 5… sono dispari), non la posizione."""
    pages = [p for p in options.pages if page_count is None or 0 <= p < page_count]
    if options.page_set == PAGES_ODD:
        return [p for p in pages if p % 2 == 0]
    if options.page_set == PAGES_EVEN:
        return [p for p in pages if p % 2 == 1]
    return pages


def require_pages(options: PrintOptions, page_count: int | None = None) -> list[int]:
    """Come `selected_pages`, ma un elenco vuoto è un errore per l'utente."""
    pages = selected_pages(options, page_count)
    if not pages:
        if options.pages and options.page_set == PAGES_EVEN:
            raise RangeError(_("L'intervallo non contiene pagine pari."))
        if options.pages and options.page_set == PAGES_ODD:
            raise RangeError(_("L'intervallo non contiene pagine dispari."))
        raise RangeError(_("Nessuna pagina da stampare."))
    return pages


def two_sided(options: PrintOptions) -> bool:
    """Fogli stampati sui due lati: fronte/retro scelto o opuscolo (facciate a coppie)."""
    return options.layout == LAYOUT_BOOKLET or options.duplex in (DUPLEX_LONG, DUPLEX_SHORT)


def imposed(options: PrintOptions) -> bool:
    """Le facciate vanno composte (opuscolo, poster, più pagine per foglio)."""
    return options.layout in (LAYOUT_BOOKLET, LAYOUT_POSTER) or options.pages_per_sheet > 1


def layout_sides(options: PrintOptions, page_count: int,
                 tiles: Callable[[int], int] | None = None) -> list[Side]:
    """Facciate nell'ordine naturale. `tiles(pagina)`: fogli del poster per la pagina."""
    pages = selected_pages(options, page_count)
    if options.layout == LAYOUT_BOOKLET:
        return [Side(k, tuple(None if s is None else pages[s] for s in pair))
                for k, pair in enumerate(booklet_sides(len(pages), options.booklet_rtl))]
    if options.layout == LAYOUT_POSTER:
        out: list[Side] = []
        for p in pages:
            for t in range(max(1, tiles(p)) if tiles is not None else 1):
                out.append(Side(len(out), (p,), t))
        return out
    n = max(1, int(options.pages_per_sheet))
    return [Side(k, tuple(pages[i:i + n])) for k, i in enumerate(range(0, len(pages), n))]


def sheets_for(options: PrintOptions, page_count: int,
               tiles: Callable[[int], int] | None = None) -> list[Sheet]:
    """Fogli di una copia come escono dalla stampante. In fronte/retro le facciate vanno a
    coppie e l'ultimo retro, se manca, resta bianco; l'ordine inverso inverte i fogli, non le
    facciate, così il fronte/retro e l'opuscolo restano giusti."""
    sides = layout_sides(options, page_count, tiles)
    if two_sided(options):
        sheets = [Sheet(sides[i], sides[i + 1] if i + 1 < len(sides) else Side(None))
                  for i in range(0, len(sides), 2)]
    else:
        sheets = [Sheet(s) for s in sides]
    if options.reverse:
        sheets.reverse()
    return sheets


def print_sides(options: PrintOptions, sheets: list[Sheet]) -> list[Side]:
    """Facciate da inviare alla stampante con le copie: fascicolate (1 2 3, 1 2 3) o no
    (1 1, 2 2, 3 3); in fronte/retro si ripete il foglio intero."""
    n = max(1, int(options.copies))
    order = sheets * n if options.collate else [s for s in sheets for _i in range(n)]
    return [side for sheet in order for side in sheet.sides]


def side_scaling(options: PrintOptions) -> tuple[str, bool]:
    """Scala e rotazione automatica della facciata sulla carta: le facciate composte sono già
    della misura della carta (più pagine per foglio, poster) o vanno adattate (opuscolo)."""
    if options.layout == LAYOUT_BOOKLET:
        return FIT_PAGE, True
    if options.layout == LAYOUT_POSTER:
        return ACTUAL_SIZE, False
    if options.pages_per_sheet > 1:
        return FIT_PAGE, False
    return options.scaling, options.auto_rotate


def page_sizes(pdf: pymupdf.Document) -> Callable[[int], tuple[float, float]]:
    """Misure (larghezza, altezza) in punti delle pagine, con /Rotate, lette una volta sola
    (ENGINE_LOCK richiesto alla chiamata)."""
    cache: dict[int, tuple[float, float]] = {}

    def size(index: int) -> tuple[float, float]:
        if index not in cache:
            rect = pdf[index].rect
            cache[index] = (rect.width, rect.height)
        return cache[index]
    return size


@dataclass
class SideLayout:
    """Facciata nelle sue coordinate (punti): le pagine con il loro riquadro di destinazione
    e, per il poster, la parte di pagina (`clip`); bordi e segni di taglio da disegnare."""
    width: float
    height: float
    items: list[tuple[int, pymupdf.Rect, pymupdf.Rect | None]] = field(default_factory=list)
    frames: list[pymupdf.Rect] = field(default_factory=list)
    marks: list[tuple[pymupdf.Point, pymupdf.Point]] = field(default_factory=list)


def _fit(w: float, h: float, box: pymupdf.Rect) -> pymupdf.Rect:
    """Riquadro di `w` × `h` adattato e centrato in `box`."""
    scale = min(box.width / w, box.height / h) if w > 0 and h > 0 else 1.0
    fw, fh = w * scale, h * scale
    return pymupdf.Rect(box.x0 + (box.width - fw) / 2, box.y0 + (box.height - fh) / 2,
                        box.x0 + (box.width + fw) / 2, box.y0 + (box.height + fh) / 2)


class Imposition:
    """Geometria delle facciate di un lavoro. `sizes(pagina)` → (larghezza, altezza) in punti;
    `paper_w` × `paper_h`: area stampabile. Celle dell'opuscolo e della griglia dalla pagina
    più grande del lavoro, come nella stampa."""

    def __init__(self, options: PrintOptions, sizes: Callable[[int], tuple[float, float]],
                 paper_w: float, paper_h: float, page_count: int | None = None):
        self.options = options
        self.sizes = sizes
        self.paper = (paper_w, paper_h)
        self.layout = options.layout
        self.n = max(1, int(options.pages_per_sheet)) if options.layout == LAYOUT_NORMAL else 1
        self.box = (0.0, 0.0)
        if self.layout == LAYOUT_BOOKLET or self.n > 1:
            pages = selected_pages(options, page_count)
            self.box = (max((sizes(p)[0] for p in pages), default=0.0),
                        max((sizes(p)[1] for p in pages), default=0.0))
        self.grid = nup_grid(self.n, *self.box, paper_w, paper_h) if self.n > 1 else (1, 1)
        self._tiles: dict[int, list[pymupdf.Rect]] = {}

    def tiles(self, page: int) -> list[pymupdf.Rect]:
        if page not in self._tiles:
            self._tiles[page] = poster_tiles(*self.sizes(page), *self.paper, self.options.poster_overlap_mm * MM)
        return self._tiles[page]

    def tile_count(self, page: int) -> int:
        return len(self.tiles(page)) if self.layout == LAYOUT_POSTER else 1

    def side(self, side: Side) -> SideLayout:
        if self.layout == LAYOUT_BOOKLET:
            width, height = self.box
            out = SideLayout(2 * width, height)
            for pos, index in enumerate(side.pages):
                if index is not None:
                    half = pymupdf.Rect(pos * width, 0, (pos + 1) * width, height)
                    out.items.append((index, _fit(*self.sizes(index), half), None))
            return out
        if self.layout == LAYOUT_POSTER:
            paper_w, paper_h = self.paper
            out = SideLayout(paper_w, paper_h)
            if not side.pages:
                return out
            index = side.pages[0]
            tiles = self.tiles(index)
            tile = tiles[min(side.tile, len(tiles) - 1)]
            target = pymupdf.Rect(0, 0, tile.width, tile.height)
            out.items.append((index, target, tile))
            if len(tiles) > 1:
                overlap, mark = self.options.poster_overlap_mm * MM, 4 * MM
                if tile.x0 > 0:                          # fascia condivisa con il foglio a sinistra
                    out.marks += [(pymupdf.Point(overlap, y), pymupdf.Point(overlap, y + mark))
                                  for y in (0, target.height - mark)]
                if tile.y0 > 0:
                    out.marks += [(pymupdf.Point(x, overlap), pymupdf.Point(x + mark, overlap))
                                  for x in (0, target.width - mark)]
            return out
        if self.n > 1:
            paper_w, paper_h = self.paper
            cols, rows = self.grid
            cell_w, cell_h = paper_w / cols, paper_h / rows
            gap = 2 * MM
            out = SideLayout(paper_w, paper_h)
            for k, index in enumerate(side.pages):
                if index is None:
                    continue
                col, row = (k // rows, k % rows) if self.options.nup_by_columns else (k % cols, k // cols)
                cell = pymupdf.Rect(col * cell_w + gap, row * cell_h + gap,
                                    (col + 1) * cell_w - gap, (row + 1) * cell_h - gap)
                target = _fit(*self.sizes(index), cell)
                out.items.append((index, target, None))
                if self.options.nup_border:
                    out.frames.append(target)
            return out
        if not side.pages or side.pages[0] is None:
            return SideLayout(*self.paper)
        w, h = self.sizes(side.pages[0])
        return SideLayout(w, h, [(side.pages[0], pymupdf.Rect(0, 0, w, h), None)])


def impose_sides(pdf: pymupdf.Document, imposition: Imposition, sides: list[Side]) -> pymupdf.Document:
    """Documento con una pagina per facciata, nell'ordine di `sides`, composta con il motore
    (contenuto vettoriale; le annotazioni vanno prima cotte con `bake_for_imposition`)."""
    out = pymupdf.open()
    for side in sides:
        lay = imposition.side(side)
        sheet = out.new_page(width=lay.width, height=lay.height)
        for index, target, clip in lay.items:
            sheet.show_pdf_page(target, pdf, index, clip=clip, keep_proportion=True)
        for lines, width in ((lay.frames, 0.5), (lay.marks, 0.3)):
            if not lines:
                continue
            shape = sheet.new_shape()
            for item in lines:
                if isinstance(item, pymupdf.Rect):
                    shape.draw_rect(item)
                else:
                    shape.draw_line(*item)
            shape.finish(color=(0, 0, 0), width=width)
            shape.commit()
    return out


def describe_side(side: Side) -> str:
    """Testo della facciata per l'anteprima e le tecnologie assistive: «pagine 1, 2»."""
    numbers = [str(p + 1) for p in side.pages if p is not None]
    if side.blank or not numbers:
        return _("facciata bianca")
    if len(numbers) == 1:
        return _("pagina {n}").format(n=numbers[0])
    return _("pagine {n}").format(n=", ".join(numbers))


def describe_sheet(sheet: Sheet) -> str:
    if sheet.back is None:
        return describe_side(sheet.front)
    return _("fronte: {a}; retro: {b}").format(a=describe_side(sheet.front), b=describe_side(sheet.back))


def print_copy(document, layers_mode: str) -> pymupdf.Document:
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


def not_printed(pdf: pymupdf.Document, index: int, annotations) -> list[int]:
    """Annotazioni della pagina che non vanno su carta (VIS-43): quelle senza il flag /Print e,
    con «Solo il documento» (`annotations` False), tutte tranne i campi modulo, i cui valori
    sono contenuto del documento; con «Solo campi e timbri» (CONTENT_FIELDS) restano campi
    modulo e timbri (ENGINE_LOCK richiesto)."""
    keep = {CONTENT_DOCUMENT: (pymupdf.PDF_ANNOT_WIDGET,),
            CONTENT_FIELDS: (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_STAMP)}.get(content_mode(annotations))
    hide = []
    for xref, kind, _id in pdf[index].annot_xrefs():
        try:
            k, value = pdf.xref_get_key(xref, "F")
            flags = int(value) if k == "int" else 0
        except Exception:
            flags = 0
        if not flags & pymupdf.PDF_ANNOT_IS_PRINT or (keep is not None and kind not in keep):
            hide.append(xref)
    return hide


def strip_page_contents(pdf: pymupdf.Document, indexes) -> None:
    """Toglie il contenuto delle pagine (testo, immagini, disegni) e lascia annotazioni e campi:
    «Solo campi e timbri» stampa su moduli prestampati. Solo sulle copie (ENGINE_LOCK richiesto)."""
    for index in set(indexes):
        pdf.xref_set_key(pdf.page_xref(index), "Contents", "[]")


def render_page(pdf: pymupdf.Document, index: int, dpi: float, annotations) -> QImage:
    """Immagine della pagina (con /Rotate del documento, mai la rotazione della vista).
    `annotations`: True/False (commenti sì/no) o una costante CONTENT_*; con CONTENT_FIELDS il
    contenuto va tolto prima dalla copia (`strip_page_contents`)."""
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


def render_preview_page(pdf: pymupdf.Document, index: int, dpi: float, content: str) -> QImage:
    """Miniatura per l'anteprima, come uscirà in stampa ma senza copiare il documento: con
    «Solo campi e timbri» si copia la sola pagina e se ne toglie il contenuto."""
    with ENGINE_LOCK:
        if content != CONTENT_FIELDS:
            return render_page(pdf, index, dpi, content)
        single = pymupdf.open()
        try:
            single.insert_pdf(pdf, from_page=index, to_page=index)
            strip_page_contents(single, [0])
            return render_page(single, 0, dpi, content)
        finally:
            single.close()


def print_document(document, device: QPagedPaintDevice, options: PrintOptions, progress=None) -> int:
    """Disegna i fogli scelti su `device`. Restituisce il numero di facciate inviate
    (comprese le copie e le facciate bianche del fronte/retro).

    Non usa widget: si può chiamare da `run_task`. `progress.update()` solleva
    `OperationCancelled` se l'utente annulla.
    """
    with ENGINE_LOCK:
        page_count = document.pdf.page_count
    pages = require_pages(options, page_count)
    content = print_content(options)
    work = print_copy(document, options.layers)
    painter = QPainter()
    paint0 = device.pageLayout().paintRectPoints()
    composed = imposed(options)
    try:
        with ENGINE_LOCK:
            if content == CONTENT_FIELDS:      # moduli prestampati: niente contenuto delle pagine
                strip_page_contents(work, pages)
            imposition = Imposition(options, page_sizes(work), paint0.width(), paint0.height(), page_count)
            sequence = print_sides(options, sheets_for(options, page_count, imposition.tile_count))
            if composed:
                bake_for_imposition(work, content)
                sides = impose_sides(work, imposition, layout_sides(options, page_count, imposition.tile_count))
                work.close()
                work = sides
        # le facciate composte sono pagine della copia, nell'ordine naturale
        scaling, auto_rotate = side_scaling(options)
        if not painter.begin(device):
            raise RuntimeError(_("Impossibile avviare la stampa sul dispositivo scelto."))
        resolution = device.resolution() or 72
        dpi = effective_dpi(resolution, options.max_dpi)
        paint = device.pageLayout().paintRectPixels(resolution)
        px_per_pt = resolution / 72.0
        paper_w, paper_h = paint.width() / px_per_pt, paint.height() / px_per_pt
        total = len(sequence)
        for n, side in enumerate(sequence):
            if progress is not None:
                progress.update(n / total, _("Pagina {n} di {t}").format(n=n + 1, t=total))
            if n:
                device.newPage()
            index = side.index if composed else (side.pages[0] if side.pages else None)
            if side.blank or index is None:
                continue                          # retro bianco del fronte/retro
            with ENGINE_LOCK:
                rect = work[index].rect
            place = placement(rect.width, rect.height, paper_w, paper_h, scaling, auto_rotate)
            image = render_page(work, index, dpi, content)
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
