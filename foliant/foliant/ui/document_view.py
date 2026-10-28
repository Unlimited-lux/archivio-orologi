"""Vista del documento: impaginazione, zoom, rendering progressivo, strumenti.

Sistemi di coordinate:
- *pagina*: punti PDF della pagina non ruotata, origine in alto a sinistra
  (le coordinate del motore per testo, annotazioni, disegno);
- *viewport*: pixel logici del widget visibile.
`page_transform(i)` converte pagina -> viewport; `view_to_page()` l'inverso.

Resa: sotto `TILE_THRESHOLD` pixel ogni pagina visibile è un'unica immagine; oltre
(zoom elevati, pagine enormi) si disegnano solo le tasselle visibili, `TILE_SIZE` pixel
di lato, con la stessa matrice della resa intera e un ritaglio (`clip`).

Guide (VIS-39, OBJ-23): linee orizzontali o verticali nelle coordinate della *pagina
visualizzata* (riquadro /CropBox con /Rotate applicato, origine in alto a sinistra, punti;
senza la rotazione della vista, così restano attaccate al contenuto). `display_transform(i)`
converte queste coordinate -> viewport, `page_guide_lines(i)` le riporta in coordinate pagina.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

import pymupdf
from PySide6.QtCore import QEvent, QLineF, QObject, QPoint, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QFrame, QMenu

from ..core import placement
from ..core.document import CLOSED, CONTENT, Document
from ..core.engine import ENGINE_LOCK
from ..core.render import annotations_hidden
from . import theme
from .rulers import PT_PER_UNIT, RULER_SIZE, RULER_UNITS, Ruler, RulerCorner, unit_names
from .tools import HandTool, PageEvent, SelectTextTool, TextSelection, Tool, copy_text_to_clipboard, selection_color

PX_PER_PT = 96.0 / 72.0
MAX_RENDER_PIXELS = 24_000_000
TILE_THRESHOLD = 16_000_000      # pixel della resa intera oltre i quali la pagina va a tasselle
TILE_SIZE = 1024                 # lato delle tasselle, in pixel del dispositivo
PREVIEW_PIXELS = 4_000_000       # anteprima intera di una pagina a tasselle mai disegnata prima

VIEW_CONTINUOUS = "continuous"   # pagine una sotto l'altra
VIEW_SINGLE = "single"           # una pagina alla volta
VIEW_FACING = "facing"           # due pagine affiancate
VIEW_COVER = "cover"             # affiancate con copertina singola
VIEW_MODES = (VIEW_CONTINUOUS, VIEW_SINGLE, VIEW_FACING, VIEW_COVER)

FIT_WIDTH = "width"
FIT_PAGE = "page"

ZOOM_STEPS = (0.01, 0.02, 0.05, 0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0,
              4.0, 5.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0)

GUIDE_HORIZONTAL = "horizontal"  # guida orizzontale: y costante nella pagina visualizzata
GUIDE_VERTICAL = "vertical"      # guida verticale: x costante
_GUIDE_ALIASES = {"horizontal": GUIDE_HORIZONTAL, "h": GUIDE_HORIZONTAL, "y": GUIDE_HORIZONTAL,
                  "vertical": GUIDE_VERTICAL, "v": GUIDE_VERTICAL, "x": GUIDE_VERTICAL}
GUIDE_HIT_PX = 4.0               # distanza (pixel) entro cui il puntatore «prende» una guida
AUTOSCROLL_EDGE = 12.0           # pixel dal bordo del viewport in cui parte lo scorrimento automatico
AUTOSCROLL_INTERVAL_MS = 30


def guide_orientation(value: str) -> str:
    """Orientamento normalizzato di una guida: GUIDE_HORIZONTAL o GUIDE_VERTICAL.

    Accetta anche "h"/"v" e, come le guide «x»/«y» del modulo oggetti, "y" (orizzontale) e
    "x" (verticale)."""
    try:
        return _GUIDE_ALIASES[str(value).lower()]
    except KeyError:
        raise ValueError(f"orientamento della guida non valido: {value!r}") from None


def _qt_matrix(m: pymupdf.Matrix) -> QTransform:
    return QTransform(m.a, m.b, m.c, m.d, m.e, m.f)


def _distance_to_segment(line: QLineF, p: QPointF) -> float:
    ax, ay, bx, by = line.x1(), line.y1(), line.x2(), line.y2()
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p.x() - ax) * dx + (p.y() - ay) * dy) / length2))
    return math.hypot(p.x() - (ax + t * dx), p.y() - (ay + t * dy))


class DocumentBridge(QObject):
    """Riporta le notifiche del documento nel thread dell'interfaccia."""

    changed = Signal(str)

    def __init__(self, document: Document, parent: QObject | None = None):
        super().__init__(parent)
        self.document = document
        document.add_listener(self._on_change)

    def _on_change(self, _doc: Document, kind: str) -> None:
        self.changed.emit(kind)

    def detach(self) -> None:
        self.document.remove_listener(self._on_change)


def _identity() -> pymupdf.Matrix:
    return pymupdf.Matrix(1, 0, 0, 1, 0, 0)


@dataclass
class PageGeometry:
    width: float             # punti, come visualizzata (rotazioni incluse)
    height: float
    matrix: pymupdf.Matrix   # pagina non ruotata -> punti visualizzati
    # pagina visualizzata senza la rotazione della vista (/CropBox e /Rotate applicati) -> punti visualizzati
    view: pymupdf.Matrix = field(default_factory=_identity)


def _extra_rotation(w: float, h: float, deg: int) -> tuple[pymupdf.Matrix, float, float]:
    if deg == 90:
        return pymupdf.Matrix(0, 1, -1, 0, h, 0), h, w
    if deg == 180:
        return pymupdf.Matrix(-1, 0, 0, -1, w, h), w, h
    if deg == 270:
        return pymupdf.Matrix(0, -1, 1, 0, 0, w), h, w
    return pymupdf.Matrix(1, 0, 0, 1, 0, 0), w, h


def page_geometry(page: pymupdf.Page, view_rotation: int = 0) -> PageGeometry:
    # Coordinate del motore (testo, annotazioni) -> pagina visualizzata. La matrice del motore
    # sbaglia quando il CropBox è più grande del MediaBox: placement la calcola dai riquadri.
    try:
        m = placement.page_to_display_matrix(page)
    except Exception:
        m = page.rotation_matrix
    w, h = page.rect.width, page.rect.height
    extra, w2, h2 = _extra_rotation(w, h, view_rotation % 360)
    return PageGeometry(w2, h2, m * extra, extra)


class DocumentView(QAbstractScrollArea):
    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)
    selectionChanged = Signal()
    toolChanged = Signal(object)
    contentChanged = Signal()
    layoutChanged = Signal()
    statusMessage = Signal(str)
    guidesChanged = Signal()           # guide aggiunte, spostate o rimosse
    rulerUnitChanged = Signal(str)     # unità dei righelli scelta dall'utente ("mm", "pt", "in")

    GAP = 16.0
    MARGIN = 24.0
    MIN_ZOOM = 0.01                  # VIS-10: 1%–6400%
    MAX_ZOOM = 64.0
    CACHE_LIMIT = 320 * 1024 * 1024
    TILE_CACHE_LIMIT = 256 * 1024 * 1024

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self._bridge = DocumentBridge(document, self)
        self._bridge.changed.connect(self._on_document_changed)
        self._zoom = 1.0
        self._fit: str | None = FIT_WIDTH
        self._view_mode = VIEW_CONTINUOUS
        self._view_rotation = 0
        self._geoms: list[PageGeometry] = []
        self._rects: list[QRectF] = []
        self._content = QSizeF(0, 0)
        self._current = 0
        # Pagine intere: (pagina, scala, rotazione, versione) -> immagine.
        self._cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._cache_bytes = 0
        self._stale: dict[int, QImage] = {}
        # Tasselle: (pagina, scala, rotazione, versione, colonna, riga) -> (immagine, dx, dy), con
        # dx, dy = posizione rispetto all'angolo della pagina, in pixel della resa.
        self._tiles: OrderedDict[tuple, tuple[QImage, float, float]] = OrderedDict()
        self._tiles_bytes = 0
        # Coda delle rese: chiavi di `_cache` (4 elementi) o di `_tiles` (6 elementi).
        self._queue: list[tuple] = []
        self._failed: set[tuple] = set()
        self._failed_pages: set[tuple[int, int]] = set()   # (pagina, versione) che il motore non riesce a disegnare
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_step)
        self._words: dict[int, list[tuple]] = {}
        self._links: dict[int, list[dict]] = {}
        self._default_tool: Tool = SelectTextTool()
        self._tool: Tool | None = None
        self.hand_tool = HandTool()
        # False: pagine disegnate senza annotazioni (solo a schermo, il file non cambia; ANN-39).
        self.show_annotations = True
        # Filtro delle annotazioni disegnate: fn(pagina, xref) -> bool (True = visibile); None = tutte.
        self._annot_filter = None
        # Disegni aggiuntivi dei moduli: callable(view, painter, page_index), painter in coordinate pagina.
        self.overlays: list = []
        # Voci del menu contestuale (tasto destro): callable(view, page_event, menu).
        # La finestra principale condivide qui la lista registrata dai moduli.
        self.menu_providers: list = []
        # Selezione del testo: una TextSelection per pagina, in ordine di pagina (VIS-28).
        self._selections: list[TextSelection] = []
        self.search_hits: dict[int, list] = {}
        self.current_hit: tuple[int, int] | None = None
        # Trascinamento di uno strumento col tasto sinistro (per lo scorrimento automatico).
        self._tool_dragging = False
        self._autoscroll_state: tuple | None = None    # (posizione, pulsanti, modificatori)
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(AUTOSCROLL_INTERVAL_MS)
        self._autoscroll_timer.timeout.connect(self._autoscroll_step)
        # Righelli e guide (VIS-39).
        self._pointer: QPointF | None = None           # ultima posizione del puntatore nel viewport
        self._rulers_visible = False
        self._rulers: tuple[Ruler, Ruler, RulerCorner] | None = None
        self._ruler_unit = "mm"
        self._guides: list[tuple[str, float, int | None]] = []
        self._guides_visible = True
        self._guides_locked = False
        self._guide_drag: dict | None = None
        self._guide_hover = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._reload_geometry()
        self.set_tool(None)

    # ================================================================ proprietà
    @property
    def tool(self) -> Tool:
        return self._tool or self._default_tool

    @property
    def page_count(self) -> int:
        return len(self._geoms)

    @property
    def current_page(self) -> int:
        return self._current

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def fit_mode(self) -> str | None:
        return self._fit

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def view_rotation(self) -> int:
        return self._view_rotation

    @property
    def text_selection(self) -> TextSelection | None:
        """Selezione di testo della prima pagina selezionata (None = nessuna selezione).

        Per una selezione su più pagine: `text_selections`."""
        return self._selections[0] if self._selections else None

    @text_selection.setter
    def text_selection(self, selection: TextSelection | None) -> None:
        self._selections = [selection] if selection is not None else []

    @property
    def text_selections(self) -> list[TextSelection]:
        """Selezione di testo: una TextSelection per pagina, in ordine di pagina (copia dell'elenco)."""
        return list(self._selections)

    def status(self, message: str) -> None:
        self.statusMessage.emit(message)

    def copy_selection(self) -> bool:
        """Copia il testo selezionato, se il documento lo consente."""
        text = self.selected_text()
        if not text:
            return False
        if not self.document.can_copy:
            from ..i18n import _
            self.status(_("Il documento non consente la copia del contenuto."))
            return False
        copy_text_to_clipboard(text)
        return True

    def dispose(self) -> None:
        """Da chiamare prima di chiudere il documento."""
        self._render_timer.stop()
        self._stop_autoscroll()
        self._bridge.detach()
        self._cache.clear()
        self._stale.clear()
        self._tiles.clear()
        self._tiles_bytes = 0

    # ================================================================ strumenti
    def set_tool(self, tool: Tool | None) -> None:
        self._tool_dragging = False
        self._stop_autoscroll()
        old = self.tool
        old.deactivate(self)
        self._tool = tool
        new = self.tool
        new.activate(self)
        self.viewport().setCursor(new.cursor)
        self.toolChanged.emit(new)
        self.viewport().update()

    # ================================================================ geometria
    def _reload_geometry(self) -> None:
        with ENGINE_LOCK:
            pdf = self.document.pdf
            geoms = []
            for i in range(pdf.page_count):
                try:
                    geoms.append(page_geometry(pdf[i], self._view_rotation))
                except Exception:      # pagina illeggibile: segnaposto, il resto resta consultabile
                    geoms.append(PageGeometry(595.0, 842.0, pymupdf.Matrix(1, 0, 0, 1, 0, 0)))
            self._geoms = geoms
        if self._current >= len(self._geoms):
            self._current = max(0, len(self._geoms) - 1)
        self._relayout()

    def page_size(self, index: int) -> tuple[float, float]:
        """Dimensione della pagina come visualizzata, in punti."""
        g = self._geoms[index]
        return g.width, g.height

    def _scale(self) -> float:
        return self._zoom * PX_PER_PT

    def _rows(self) -> list[list[int]]:
        n = len(self._geoms)
        if n == 0:
            return []
        if self._view_mode == VIEW_SINGLE:
            return [[self._current]]
        if self._view_mode == VIEW_FACING:
            return [list(range(i, min(i + 2, n))) for i in range(0, n, 2)]
        if self._view_mode == VIEW_COVER:
            rows = [[0]]
            rows += [list(range(i, min(i + 2, n))) for i in range(1, n, 2)]
            return rows
        return [[i] for i in range(n)]

    def _row_of(self, page: int) -> list[int]:
        for row in self._rows():
            if page in row:
                return row
        return [page]

    def _inset(self) -> float:
        """Spazio occupato dai righelli sul bordo superiore e sinistro del viewport."""
        return float(RULER_SIZE) if self._rulers_visible else 0.0

    def _available_size(self) -> tuple[float, float]:
        vbar = self.verticalScrollBar().sizeHint().width()
        w = self.viewport().width()
        if self.verticalScrollBar().isHidden():
            w -= vbar
        inset = self._inset()
        return (max(80.0, w - 2 * self.MARGIN - inset),
                max(80.0, self.viewport().height() - 2 * self.MARGIN - inset))

    def _fit_zoom(self) -> float:
        rows = self._rows()
        if not rows:
            return self._zoom
        aw, ah = self._available_size()
        if self._fit == FIT_WIDTH:
            widest = max(sum(self._geoms[i].width for i in r) for r in rows)
            gaps = max(len(r) for r in rows) - 1
            z = (aw - gaps * self.GAP) / (widest * PX_PER_PT)
        else:
            row = self._row_of(self._current)
            rw = sum(self._geoms[i].width for i in row)
            rh = max(self._geoms[i].height for i in row)
            z = min((aw - (len(row) - 1) * self.GAP) / (rw * PX_PER_PT), ah / (rh * PX_PER_PT))
        return min(self.MAX_ZOOM, max(self.MIN_ZOOM, z))

    def _relayout(self) -> None:
        if self._fit:
            self._zoom = self._fit_zoom()
        s = self._scale()
        n = len(self._geoms)
        rects = [QRectF() for _ in range(n)]
        rows = self._rows()
        inset = self._inset()           # i righelli coprono il bordo: le pagine iniziano dopo
        y = self.MARGIN + inset
        max_w = 0.0
        laid: list[tuple[list[int], float, float]] = []
        for row in rows:
            row_w = sum(self._geoms[i].width * s for i in row) + self.GAP * (len(row) - 1)
            row_h = max(self._geoms[i].height * s for i in row)
            laid.append((row, row_w, row_h))
            max_w = max(max_w, row_w)
        content_w = max(float(self.viewport().width()), max_w + 2 * self.MARGIN + inset)
        for row, row_w, row_h in laid:
            x = inset + (content_w - inset - row_w) / 2
            for i in row:
                g = self._geoms[i]
                w, h = g.width * s, g.height * s
                rects[i] = QRectF(x, y + (row_h - h) / 2, w, h)
                x += w + self.GAP
            y += row_h + self.GAP
        total_h = (y - self.GAP + self.MARGIN) if rows else 0.0
        self._rects = rects
        self._content = QSizeF(content_w, total_h)
        vw, vh = self.viewport().width(), self.viewport().height()
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        # tolleranza di 1 px: con «adatta larghezza» gli arrotondamenti non devono creare una barra inutile
        hbar.setRange(0, max(0, min(2**31 - 1, int(math.ceil(content_w - vw - 1.0)))))
        hbar.setPageStep(vw)
        hbar.setSingleStep(24)
        vbar.setRange(0, max(0, min(2**31 - 1, int(math.ceil(total_h - vh)))))
        vbar.setPageStep(vh)
        vbar.setSingleStep(36)
        self.layoutChanged.emit()
        self.viewport().update()
        self._update_rulers()
        self._schedule_visible()

    def page_rect(self, index: int) -> QRectF:
        """Rettangolo della pagina nel viewport (pixel)."""
        return self._rects[index].translated(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())

    def _shown_to_view(self, index: int) -> QTransform:
        """Punti visualizzati (rotazione della vista inclusa) -> viewport."""
        r = self._rects[index]
        s = self._scale()
        return (QTransform.fromScale(s, s)
                * QTransform.fromTranslate(r.x() - self.horizontalScrollBar().value(),
                                           r.y() - self.verticalScrollBar().value()))

    def page_transform(self, index: int) -> QTransform:
        """Trasformazione coordinate pagina -> viewport."""
        return _qt_matrix(self._geoms[index].matrix) * self._shown_to_view(index)

    def display_transform(self, index: int) -> QTransform:
        """Trasformazione coordinate della pagina visualizzata -> viewport.

        Coordinate della pagina visualizzata: punti dall'angolo superiore sinistro della pagina
        come la mostra il documento (/CropBox con /Rotate applicato), senza la rotazione della
        vista: sono le coordinate delle guide e di `placement.page_to_display_matrix`."""
        return _qt_matrix(self._geoms[index].view) * self._shown_to_view(index)

    def display_size(self, index: int) -> tuple[float, float]:
        """Larghezza e altezza della pagina visualizzata (senza la rotazione della vista), in punti."""
        g = self._geoms[index]
        r = pymupdf.Rect(0, 0, g.width, g.height) * ~g.view
        return r.width, r.height

    def page_to_view(self, index: int, rect: pymupdf.Rect) -> QRectF:
        return self.page_transform(index).mapRect(QRectF(rect.x0, rect.y0, rect.width, rect.height))

    def view_to_page(self, pos: QPointF, clamp: bool = False) -> tuple[int, pymupdf.Point] | None:
        """Pagina e punto (coordinate pagina) sotto una posizione del viewport."""
        hit = None
        best_d = None
        for i in self.visible_pages():
            r = self.page_rect(i)
            if r.contains(pos):
                hit = i
                break
            if clamp:
                dx = max(r.left() - pos.x(), 0.0, pos.x() - r.right())
                dy = max(r.top() - pos.y(), 0.0, pos.y() - r.bottom())
                d = dx * dx + dy * dy
                if best_d is None or d < best_d:
                    best_d, hit_c = d, i
        if hit is None:
            if not clamp or best_d is None:
                return None
            hit = hit_c
            return hit, self.clamp_to_page(hit, pos)
        inv, _ok = self.page_transform(hit).inverted()
        p = inv.map(pos)
        return hit, pymupdf.Point(p.x(), p.y())

    def clamp_to_page(self, index: int, pos: QPointF) -> pymupdf.Point:
        r = self.page_rect(index)
        cx = min(max(pos.x(), r.left()), r.right())
        cy = min(max(pos.y(), r.top()), r.bottom())
        inv, _ok = self.page_transform(index).inverted()
        p = inv.map(QPointF(cx, cy))
        return pymupdf.Point(p.x(), p.y())

    def visible_pages(self, prefetch: float = 0.0) -> list[int]:
        x0 = self.horizontalScrollBar().value()
        y0 = self.verticalScrollBar().value() - prefetch
        area = QRectF(x0, y0, self.viewport().width(), self.viewport().height() + 2 * prefetch)
        return [i for i, r in enumerate(self._rects) if not r.isEmpty() and r.intersects(area)]

    # ================================================================ navigazione
    def go_to_page(self, index: int, point: pymupdf.Point | None = None) -> None:
        if not self._geoms:
            return
        index = max(0, min(index, len(self._geoms) - 1))
        if self._view_mode == VIEW_SINGLE and index != self._current:
            self._current = index
            self._relayout()
            self.currentPageChanged.emit(index)
        r = self._rects[index]
        inset = self._inset()
        y = r.top() - self.MARGIN / 2 - inset
        if point is not None:
            p = self.page_transform(index).map(QPointF(point.x, point.y))
            y = p.y() + self.verticalScrollBar().value() - self.MARGIN - inset
        self.verticalScrollBar().setValue(int(y))
        self._set_current(index)

    def next_page(self) -> None:
        row = self._row_of(self._current)
        self.go_to_page(row[-1] + 1)

    def previous_page(self) -> None:
        row = self._row_of(self._current)
        target = row[0] - 1
        if target >= 0:
            self.go_to_page(self._row_of(target)[0])

    def ensure_visible(self, index: int, rect: pymupdf.Rect, margin: int = 60) -> None:
        if self._view_mode == VIEW_SINGLE and index != self._current:
            self.go_to_page(index)
        vr = self.page_to_view(index, rect)
        vw, vh = self.viewport().width(), self.viewport().height()
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        if vr.top() < margin or vr.bottom() > vh - margin:
            vbar.setValue(int(vbar.value() + vr.center().y() - vh / 2))
        if vr.left() < margin or vr.right() > vw - margin:
            hbar.setValue(int(hbar.value() + vr.center().x() - vw / 2))
        self._set_current(index)

    def _set_current(self, index: int) -> None:
        if index != self._current:
            self._current = index
            self.currentPageChanged.emit(index)

    def _update_current_from_scroll(self) -> None:
        if self._view_mode == VIEW_SINGLE or not self._rects:
            return
        probe = self.verticalScrollBar().value() + self.viewport().height() * 0.3
        current = self._current
        for i, r in enumerate(self._rects):
            if r.isEmpty():
                continue
            if r.top() - self.GAP <= probe <= r.bottom() + self.GAP:
                current = i
                break
        if self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() and self.verticalScrollBar().maximum() > 0:
            current = max(current, self._row_of(len(self._rects) - 1)[0])
        self._set_current(current)

    # ================================================================ zoom e modalità
    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, zoom))
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        hit = self.view_to_page(anchor, clamp=True)
        self._fit = None
        self._zoom = zoom
        self._relayout()
        if hit is not None:
            index, pt = hit
            p = self.page_transform(index).map(QPointF(pt.x, pt.y))
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() + p.x() - anchor.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() + p.y() - anchor.y()))
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        for z in ZOOM_STEPS:
            if z > self._zoom + 1e-3:
                self.set_zoom(z)
                return
        self.set_zoom(self.MAX_ZOOM)

    def zoom_out(self) -> None:
        for z in reversed(ZOOM_STEPS):
            if z < self._zoom - 1e-3:
                self.set_zoom(z)
                return
        self.set_zoom(self.MIN_ZOOM)

    def set_fit(self, fit: str | None) -> None:
        page = self._current
        self._fit = fit
        self._relayout()
        self.go_to_page(page)
        self.zoomChanged.emit(self._zoom)

    def set_view_mode(self, mode: str) -> None:
        if mode not in VIEW_MODES or mode == self._view_mode:
            return
        page = self._current
        self._view_mode = mode
        self._relayout()
        self.go_to_page(page)

    def rotate_view(self, degrees: int = 90) -> None:
        """Ruota solo la visualizzazione (il documento non cambia)."""
        page = self._current
        self._view_rotation = (self._view_rotation + degrees) % 360
        self._invalidate_render()
        self._reload_geometry()
        self.go_to_page(page)

    # ================================================================ contenuto
    def _on_document_changed(self, kind: str) -> None:
        if kind == CLOSED:
            self.dispose()
            return
        if kind != CONTENT:
            return
        self._words.clear()
        self._links.clear()
        self._invalidate_render(keep_stale=True)
        if self._selections:
            self._selections = []
            self.selectionChanged.emit()
        top = self.verticalScrollBar().value()
        self._reload_geometry()
        self.verticalScrollBar().setValue(top)
        self._update_current_from_scroll()
        self.contentChanged.emit()

    def _invalidate_render(self, keep_stale: bool = False) -> None:
        self._failed.clear()
        self._failed_pages.clear()
        self._cache.clear()
        self._cache_bytes = 0
        if not keep_stale:
            self._stale.clear()
            self._tiles.clear()
            self._tiles_bytes = 0
        else:
            keep = set(self.visible_pages(prefetch=self.viewport().height()))
            self._stale = {k: v for k, v in self._stale.items() if k in keep}
            # le tasselle in vista restano come resa provvisoria finché non arrivano le nuove
            key = self._scale_key()
            for k in [k for k in self._tiles if k[0] not in keep or k[1] != key]:
                self._tiles_bytes -= self._tiles.pop(k)[0].sizeInBytes()
        self.viewport().update()

    def words(self, index: int) -> list[tuple]:
        """Parole della pagina (x0, y0, x1, y1, parola, blocco, riga, n), in cache."""
        if index not in self._words:
            with ENGINE_LOCK:
                self._words[index] = self.document.pdf[index].get_text("words")
        return self._words[index]

    def word_at(self, index: int, point: pymupdf.Point) -> tuple | None:
        for w in self.words(index):
            if pymupdf.Rect(w[:4]).contains(point):
                return w
        return None

    def links(self, index: int) -> list[dict]:
        """Collegamenti della pagina con `from`/`to` in coordinate pagina standard."""
        if index not in self._links:
            with ENGINE_LOCK:
                page = self.document.pdf[index]
                try:
                    self._links[index] = placement.get_links(page)
                except Exception:      # geometria non gestibile: valori del motore
                    self._links[index] = page.get_links()
        return self._links[index]

    def link_at(self, index: int, point: pymupdf.Point) -> dict | None:
        for link in self.links(index):
            if pymupdf.Rect(link["from"]).contains(point):
                return link
        return None

    def set_text_selection(self, selection: TextSelection | None) -> None:
        """Selezione di testo su una pagina (None = nessuna selezione)."""
        self.set_text_selections([selection] if selection is not None else [])

    def set_text_selections(self, selections: list[TextSelection]) -> None:
        """Selezione di testo su più pagine: una TextSelection per pagina (riordinate per pagina)."""
        self._selections = sorted((s for s in selections if s is not None), key=lambda s: s.page)
        self.viewport().update()
        self.selectionChanged.emit()

    def clear_text_selection(self) -> None:
        if self._selections:
            self.set_text_selections([])

    def selected_text(self) -> str:
        """Testo selezionato; su più pagine i testi delle pagine sono separati da una riga vuota."""
        return "\n\n".join(s.text for s in self._selections if s.text)

    def set_search_hits(self, hits: dict[int, list], current: tuple[int, int] | None = None) -> None:
        self.search_hits = hits
        self.current_hit = current
        self.viewport().update()

    # ================================================================ filtro delle annotazioni
    @property
    def annotation_filter(self):
        return self._annot_filter

    def set_annotation_filter(self, fn) -> None:
        """Annotazioni da disegnare: `fn(pagina, xref) -> bool` (True = visibile), None = tutte.

        Vale per la resa a pagina intera e per le tasselle degli zoom elevati, solo a schermo
        (il file non cambia; miniature e stampa non lo usano). `fn` riceve ogni voce di /Annots
        (anche campi e collegamenti) e va chiamata di nuovo, anche con la stessa funzione, quando
        cambia ciò che nasconde: la vista ridisegna le pagine. Con `show_annotations = False`
        non si disegna comunque nessuna annotazione."""
        self._annot_filter = fn
        self.refresh_rendering()

    def refresh_rendering(self) -> None:
        """Ridisegna le pagine con lo stesso documento (es. filtro cambiato): finché non arriva la
        resa nuova resta visibile quella precedente."""
        self._invalidate_render(keep_stale=True)
        self._tiles.clear()               # stessa versione del documento: le tasselle non si riusano
        self._tiles_bytes = 0
        self._schedule_visible()
        self.viewport().update()

    @contextmanager
    def _filtered_annotations(self, index: int) -> Iterator[None]:
        """Durante la resa della pagina nasconde le annotazioni escluse dal filtro (flag Hidden,
        ripristinato subito dopo). Va usato tenendo ENGINE_LOCK."""
        fn = self._annot_filter
        if fn is None or not self.show_annotations:
            yield
            return
        pdf = self.document.pdf
        hidden = []
        for xref, *_rest in pdf[index].annot_xrefs():
            try:
                visible = bool(fn(index, xref))
            except Exception:             # un filtro difettoso non deve impedire di vedere la pagina
                visible = True
            if not visible:
                hidden.append(xref)
        if not hidden:
            yield
            return
        with annotations_hidden(pdf, hidden):
            yield

    # ================================================================ rendering
    def _scale_key(self) -> float:
        return round(self._scale() * self.devicePixelRatioF(), 4)

    def _schedule_visible(self) -> None:
        key = self._scale_key()
        ver = self.document.version
        rot = self._view_rotation
        vw, vh = self.viewport().width(), self.viewport().height()
        vis = self.visible_pages()
        extra = [i for i in self.visible_pages(prefetch=vh) if i not in vis]
        if self._tiles and not any(self._tiled(i, key) for i in vis + extra):
            self._tiles.clear()              # tornati a zoom normali: la memoria delle tasselle si libera
            self._tiles_bytes = 0
        view = QRectF(0, 0, vw, vh)
        around = view.adjusted(-vw / 2, -vh, vw / 2, vh)   # anticipo: come per le pagine, più mezza vista di lato
        now: list[tuple] = []                # ciò che si vede, poi ciò che sta per entrare in vista
        later: list[tuple] = []
        later_tiles: list[tuple] = []
        for i in vis + extra:
            target = now if i in vis else later
            if not self._tiled(i, key):
                target.append((i, key, rot, ver))
                continue
            if self._stale.get(i) is None:   # nessuna resa intera da mostrare sotto: prima un'anteprima
                target.append((i, self._preview_scale(i), rot, ver))
            shown = self._tiles_in(i, key, view) if i in vis else []
            now += [(i, key, rot, ver) + t for t in shown]
            later_tiles += [(i, key, rot, ver) + t for t in self._tiles_in(i, key, around) if t not in shown]
        # le tasselle anticipate non devono far uscire dalla cache quelle visibili
        budget = self.TILE_CACHE_LIMIT // (2 * TILE_SIZE * TILE_SIZE * 4) - sum(len(k) == 6 for k in now)
        queue = now + later + later_tiles[:max(0, budget)]
        self._queue = [k for k in queue if k not in self._cache and k not in self._tiles and k not in self._failed]
        if self._queue and not self._render_timer.isActive():
            self._render_timer.start(0)

    def _render_step(self) -> None:
        if not self._queue or self.document.closed:
            return
        if not ENGINE_LOCK.acquire(blocking=False):
            self._render_timer.start(40)
            return
        key = self._queue.pop(0)
        try:
            self._render_key(key)
        except Exception:  # una pagina danneggiata non deve bloccare la vista
            self._mark_failed(key)
        finally:
            ENGINE_LOCK.release()
        self.viewport().update()
        if self._queue:
            self._render_timer.start(0)

    def flush_rendering(self) -> None:
        """Disegna subito le pagine (o le tasselle) visibili in attesa (test, stampa, catture)."""
        self._schedule_visible()
        while self._queue:
            key = self._queue.pop(0)
            with ENGINE_LOCK:
                try:
                    self._render_key(key)
                except Exception:
                    self._mark_failed(key)
        self._render_timer.stop()
        self.viewport().update()

    def _mark_failed(self, key: tuple) -> None:
        from ..i18n import _
        self._failed.add(key)
        page = (key[0], key[3])
        if page not in self._failed_pages:
            self._failed_pages.add(page)
            self.status(_("Impossibile visualizzare la pagina {n}: il file è danneggiato.").format(n=key[0] + 1))

    def _render_key(self, key: tuple) -> None:
        """Esegue una voce della coda, tenendo ENGINE_LOCK: pagina intera (4 elementi) o tassella (6)."""
        index = key[0]
        if (index >= len(self._geoms) or key[2] != self._view_rotation or key[3] != self.document.version
                or key in self._cache or key in self._tiles):
            return
        if len(key) == 4:
            img = self._render_page(index, key[1])
            self._cache_put(key, img)
            self._stale[index] = img
        else:
            self._tile_put(key, self._render_tile(index, key[1], key[4], key[5]))

    def _render_matrix(self, scale: float) -> pymupdf.Matrix:
        mat = pymupdf.Matrix(scale, scale)
        if self._view_rotation:
            mat = mat.prerotate(self._view_rotation)
        return mat

    def _render_page(self, index: int, scale: float) -> QImage:
        g = self._geoms[index]
        pixels = (g.width * scale) * (g.height * scale)
        if pixels > MAX_RENDER_PIXELS:
            scale *= math.sqrt(MAX_RENDER_PIXELS / pixels)
        page = self.document.pdf[index]
        mat = self._render_matrix(scale)
        with self._filtered_annotations(index):
            pix = page.get_pixmap(matrix=mat, alpha=False, annots=self.show_annotations)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        return img

    def _cache_put(self, key: tuple, img: QImage) -> None:
        self._cache[key] = img
        self._cache_bytes += img.sizeInBytes()
        while self._cache_bytes > self.CACHE_LIMIT and len(self._cache) > 1:
            k, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.sizeInBytes()
            if self._stale.get(k[0]) is old:
                del self._stale[k[0]]

    # ---------------------------------------------------------------- tasselle
    # Spazio della resa: i pixel dell'immagine che `get_pixmap(matrix=_render_matrix(scala))`
    # produrrebbe per la pagina intera. Il motore disegna nel riquadro della pagina visualizzata
    # (`page.rect`: /Rotate e /CropBox già applicati, origine sempre in (0, 0)); la matrice
    # aggiunge zoom e rotazione della vista. Le tasselle sono una griglia fissa di questo spazio,
    # quindi coincidono pixel per pixel con la resa intera e si riusano durante lo scorrimento.
    def _tiled(self, index: int, scale: float) -> bool:
        """True se la resa intera della pagina a questa scala supererebbe TILE_THRESHOLD pixel."""
        g = self._geoms[index]
        return g.width * g.height * scale * scale > TILE_THRESHOLD

    def _preview_scale(self, index: int) -> float:
        g = self._geoms[index]
        return round(math.sqrt(PREVIEW_PIXELS / max(1.0, g.width * g.height)), 4)

    def _render_origin(self, index: int, scale: float) -> tuple[float, float]:
        """Angolo superiore sinistro della pagina nello spazio della resa.

        La matrice ruota attorno all'origine: con la rotazione della vista l'immagine non
        parte da (0, 0). Basta la geometria già nota, senza interrogare il motore."""
        g = self._geoms[index]
        w, h = (g.height, g.width) if self._view_rotation in (90, 270) else (g.width, g.height)
        b = pymupdf.Rect(0, 0, w, h) * self._render_matrix(scale)
        return b.x0, b.y0

    def _tiles_in(self, index: int, scale: float, area: QRectF) -> list[tuple[int, int]]:
        """Tasselle (colonna, riga) della pagina che coprono `area` (viewport), dal centro verso i bordi."""
        r = self.page_rect(index)
        a = area & r
        if a.isEmpty():
            return []
        f = scale / self._scale()                 # pixel della resa per pixel logico
        ox, oy = self._render_origin(index, scale)
        gx, gy = math.floor(ox + 1e-3), math.floor(oy + 1e-3)   # origine della griglia, come fz_round_rect
        x0 = ox - gx + (a.left() - r.left()) * f
        x1 = ox - gx + (a.right() - r.left()) * f
        y0 = oy - gy + (a.top() - r.top()) * f
        y1 = oy - gy + (a.bottom() - r.top()) * f
        c0, c1 = max(0, math.floor(x0 / TILE_SIZE + 1e-6)), math.ceil(x1 / TILE_SIZE - 1e-6) - 1
        r0, r1 = max(0, math.floor(y0 / TILE_SIZE + 1e-6)), math.ceil(y1 / TILE_SIZE - 1e-6) - 1
        cc, rc = (c0 + c1) / 2, (r0 + r1) / 2
        tiles = [(c, row) for row in range(r0, r1 + 1) for c in range(c0, c1 + 1)]
        tiles.sort(key=lambda t: (t[0] - cc) ** 2 + (t[1] - rc) ** 2)
        return tiles

    def _render_tile(self, index: int, scale: float, col: int, row: int) -> tuple[QImage, float, float]:
        """Disegna una tassella con la matrice della resa intera e un ritaglio.

        `clip` va espresso nel riquadro della pagina visualizzata (lo spazio in cui il motore
        disegna, non le coordinate pagina non ruotate): è il rettangolo della tassella
        riportato indietro con l'inversa della matrice di resa."""
        mat = self._render_matrix(scale)
        ox, oy = self._render_origin(index, scale)
        x0 = math.floor(ox + 1e-3) + col * TILE_SIZE
        y0 = math.floor(oy + 1e-3) + row * TILE_SIZE
        clip = pymupdf.Rect(x0, y0, x0 + TILE_SIZE, y0 + TILE_SIZE) * ~mat
        page = self.document.pdf[index]
        with self._filtered_annotations(index):
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False, annots=self.show_annotations)
        if pix.width <= 0 or pix.height <= 0:
            return QImage(), 0.0, 0.0
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        return img, pix.x - ox, pix.y - oy

    def _tile_put(self, key: tuple, tile: tuple[QImage, float, float]) -> None:
        # la stessa tassella di una versione precedente (resa provvisoria) non serve più
        for k in [k for k in self._tiles if k[:3] == key[:3] and k[4:] == key[4:]]:
            self._tiles_bytes -= self._tiles.pop(k)[0].sizeInBytes()
        self._tiles[key] = tile
        self._tiles_bytes += tile[0].sizeInBytes()
        while self._tiles_bytes > self.TILE_CACHE_LIMIT and len(self._tiles) > 1:
            _k, old = self._tiles.popitem(last=False)     # la meno usata di recente
            self._tiles_bytes -= old[0].sizeInBytes()

    def render_page_image(self, index: int, zoom: float = 1.0) -> QImage:
        """Immagine della pagina (per miniature, anteprime, stampa)."""
        with ENGINE_LOCK:
            page = self.document.pdf[index]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            del pix, page                   # distrutti tenendo il lock
        return img

    def paintEvent(self, event) -> None:
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), theme.color("canvas"))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        key = self._scale_key()
        ver = self.document.version
        missing = False
        shadow = theme.color("page_shadow")
        try:
            self._paint_pages(p, key, ver)
        finally:
            p.end()

    def _paint_pages(self, p, key, ver) -> None:
        missing = False
        shadow = theme.color("page_shadow")
        for i in self.visible_pages():
            r = self.page_rect(i)
            p.fillRect(r.adjusted(1, 2, 2, 3), shadow)
            p.fillRect(r, QColor(255, 255, 255))
            if (i, ver) in self._failed_pages:      # NFR-14: la pagina danneggiata lo dice, le altre restano visibili
                self._paint_failed(p, r)
                continue
            if self._tiled(i, key):
                missing = self._paint_tiles(p, i, r, key, ver) or missing
            else:
                img = self._cache.get((i, key, self._view_rotation, ver))
                if img is None:
                    missing = True
                    img = self._stale.get(i)
                else:
                    self._cache.move_to_end((i, key, self._view_rotation, ver))
                if img is not None:
                    p.drawImage(r, img, QRectF(0, 0, img.width(), img.height()))
            p.save()
            p.setClipRect(r)
            p.setWorldTransform(self.page_transform(i))
            self._paint_overlays(p, i)
            p.restore()
            if self._guides_visible and self._guides:
                self._paint_guides(p, i)
        self._paint_guide_preview(p)
        if missing and not self._render_timer.isActive():
            self._schedule_visible()

    def _paint_failed(self, p: QPainter, r: QRectF) -> None:
        from ..i18n import _
        p.save()
        p.fillRect(r, QColor(246, 246, 246))
        p.setPen(QColor(150, 150, 150))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.setPen(QColor(90, 90, 90))
        p.drawText(r.adjusted(12, 12, -12, -12), int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
                   _("Impossibile visualizzare questa pagina"))
        p.restore()

    def _paint_tiles(self, p: QPainter, index: int, r: QRectF, key: float, ver: int) -> bool:
        """Pagina a tasselle: solo la parte visibile. Restituisce True se manca qualche tassella.

        Finché una tassella manca, sotto resta la resa precedente: l'ultima immagine intera
        (sfocata) e le tasselle ancora in cache di un altro zoom o della versione precedente."""
        area = QRectF(self.viewport().rect()) & r
        if area.isEmpty():
            return False
        rot = self._view_rotation
        s = self._scale()
        p.save()
        p.setClipRect(area)
        base = self._stale.get(index)
        if base is not None and not base.isNull():
            sx, sy = base.width() / r.width(), base.height() / r.height()
            p.drawImage(area, base, QRectF((area.left() - r.left()) * sx, (area.top() - r.top()) * sy,
                                           area.width() * sx, area.height() * sy))

        def draw(k: tuple, tile: tuple[QImage, float, float]) -> None:
            img, dx, dy = tile
            f = k[1] / s                          # pixel della resa per pixel logico
            target = QRectF(r.left() + dx / f, r.top() + dy / f, img.width() / f, img.height() / f)
            if not img.isNull() and target.intersects(area):
                p.drawImage(target, img, QRectF(img.rect()))

        # rese provvisorie dalla peggiore alla migliore: prima le versioni precedenti, poi lo zoom più vicino
        others = [(k, t) for k, t in self._tiles.items()
                  if k[0] == index and k[2] == rot and (k[1] != key or k[3] != ver)]
        others.sort(key=lambda kt: (kt[0][3] == ver, -abs(math.log(kt[0][1] / key))))
        for k, t in others:
            draw(k, t)
        missing = False
        for t in self._tiles_in(index, key, area):
            k = (index, key, rot, ver) + t
            tile = self._tiles.get(k)
            if tile is None:
                missing = True
                continue
            self._tiles.move_to_end(k)
            draw(k, tile)
        p.restore()
        return missing

    def _paint_overlays(self, p: QPainter, index: int) -> None:
        hits = self.search_hits.get(index)
        if hits:
            p.setPen(Qt.PenStyle.NoPen)
            for n, h in enumerate(hits):
                current = self.current_hit == (index, n)
                p.setBrush(QColor(255, 140, 0, 150) if current else QColor(255, 210, 0, 110))
                if isinstance(h, pymupdf.Quad):
                    p.drawPolygon(QPolygonF([QPointF(h.ul.x, h.ul.y), QPointF(h.ur.x, h.ur.y),
                                             QPointF(h.lr.x, h.lr.y), QPointF(h.ll.x, h.ll.y)]))
                else:
                    r = pymupdf.Rect(h)
                    p.drawRect(QRectF(r.x0, r.y0, r.width, r.height))
        for sel in self._selections:
            if sel.page != index:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(selection_color())
            for r in sel.rects:
                p.drawRect(QRectF(r.x0, r.y0, r.width, r.height))
        for overlay in list(self.overlays):
            p.save()
            try:
                overlay(self, p, index)
            except Exception as e:
                self.status(f"Errore in un livello di disegno: {e}")
            p.restore()
        p.save()
        try:
            self.tool.paint(self, p, index)
        except Exception as e:
            self.status(f"Errore nello strumento attivo: {e}")
        finally:
            p.restore()

    # ================================================================ righelli (VIS-39)
    @property
    def rulers_visible(self) -> bool:
        return self._rulers_visible

    def set_rulers_visible(self, visible: bool) -> None:
        """Mostra o nasconde i righelli; le pagine si spostano per non esserne coperte."""
        visible = bool(visible)
        if visible == self._rulers_visible:
            return
        self._rulers_visible = visible
        if visible and self._rulers is None:
            self._rulers = (Ruler(self, True), Ruler(self, False), RulerCorner(self))
        if not visible:
            self._guide_drag = None
            self._guide_hover = False
        self._place_rulers()
        if self._rulers is not None:
            for w in self._rulers:
                w.setVisible(visible)
        self._relayout()

    def _place_rulers(self) -> None:
        if self._rulers is None:
            return
        h, v, corner = self._rulers
        vw, vh = self.viewport().width(), self.viewport().height()
        h.setGeometry(0, 0, vw, RULER_SIZE)
        v.setGeometry(0, 0, RULER_SIZE, vh)
        corner.setGeometry(0, 0, RULER_SIZE, RULER_SIZE)
        corner.raise_()

    def _update_rulers(self) -> None:
        if self._rulers is not None and self._rulers_visible:
            for w in self._rulers:
                w.update()

    def _track_pointer(self, pos: QPointF) -> None:
        """Posizione del puntatore (viewport) per i righelli: pagina di riferimento e indicatore."""
        self._pointer = QPointF(pos)
        self._update_rulers()

    @property
    def ruler_unit(self) -> str:
        return self._ruler_unit

    def set_ruler_unit(self, unit: str) -> None:
        """Unità dei righelli: "mm", "pt" o "in" (pollici)."""
        if unit not in RULER_UNITS:
            raise ValueError(f"unità dei righelli non valida: {unit!r}")
        if unit == self._ruler_unit:
            return
        self._ruler_unit = unit
        self._update_rulers()
        self.rulerUnitChanged.emit(unit)

    def ruler_page(self) -> int | None:
        """Pagina a cui si riferiscono i righelli: quella sotto il puntatore (o la più vicina),
        altrimenti la pagina corrente."""
        if not self._geoms:
            return None
        if self._pointer is not None:
            hit = self.view_to_page(self._pointer, clamp=True)
            if hit is not None:
                return hit[0]
        visible = self.visible_pages()
        if self._current in visible or not visible:
            return self._current
        return visible[0]

    def ruler_position(self, pos: QPointF, page: int | None = None) -> tuple[float, float] | None:
        """Posizione `pos` (viewport) misurata come sui righelli: dall'angolo superiore sinistro
        della pagina come si vede (CropBox, /Rotate, rotazione della vista), nell'unità dei righelli."""
        page = self.ruler_page() if page is None else page
        if page is None or not (0 <= page < len(self._rects)):
            return None
        r = self.page_rect(page)
        f = self._scale() * PT_PER_UNIT[self._ruler_unit]
        return (pos.x() - r.left()) / f, (pos.y() - r.top()) / f

    def _ruler_menu(self, global_pos) -> None:
        from ..i18n import _
        menu = QMenu(self)
        units = menu.addMenu(_("Unità dei righelli"))
        group = QActionGroup(units)
        for key, label in unit_names():
            act = units.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == self._ruler_unit)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_ruler_unit(k))
        menu.addSeparator()
        lock = menu.addAction(_("Blocca le guide"))
        lock.setCheckable(True)
        lock.setChecked(self._guides_locked)
        lock.toggled.connect(self.set_guides_locked)
        clear = menu.addAction(_("Rimuovi tutte le guide"), self.clear_guides)
        clear.setEnabled(bool(self._guides))
        self.exec_menu(menu, global_pos)
        menu.deleteLater()

    # ================================================================ guide (VIS-39, OBJ-23)
    # Una guida è (orientamento, posizione in punti, pagina | None): GUIDE_HORIZONTAL = y costante,
    # GUIDE_VERTICAL = x costante, nelle coordinate della pagina visualizzata; pagina None = tutte.
    def guides(self) -> list[tuple[str, float, int | None]]:
        """Guide del documento in questa vista: (orientamento, posizione_pt, pagina | None)."""
        return list(self._guides)

    def guides_on_page(self, index: int) -> list[tuple[str, float]]:
        """(orientamento, posizione_pt) delle guide che valgono per la pagina `index`."""
        return [(o, v) for o, v, page in self._guides if page is None or page == index]

    def add_guide(self, orientation: str, position_pt: float, page: int | None = None) -> tuple[str, float, int | None]:
        """Aggiunge una guida (se non c'è già) e la restituisce normalizzata."""
        guide = (guide_orientation(orientation), float(position_pt), None if page is None else int(page))
        if guide not in self._guides:
            self._guides.append(guide)
            self._guides_changed()
        return guide

    def remove_guide(self, orientation: str, position_pt: float, page: int | None = None) -> bool:
        """Rimuove la guida indicata (posizione con tolleranza di 0,01 pt); False se non c'è.

        Accetta una voce di `guides()`: `view.remove_guide(*view.guides()[0])`."""
        o = guide_orientation(orientation)
        for n, (go, gv, gp) in enumerate(self._guides):
            if go == o and gp == page and abs(gv - float(position_pt)) < 0.01:
                del self._guides[n]
                self._guides_changed()
                return True
        return False

    def set_guides(self, guides) -> None:
        """Sostituisce tutte le guide: elementi (orientamento, posizione_pt[, pagina])."""
        out: list[tuple[str, float, int | None]] = []
        for g in guides:
            o, v, *rest = g
            page = rest[0] if rest else None
            item = (guide_orientation(o), float(v), None if page is None else int(page))
            if item not in out:
                out.append(item)
        if out != self._guides:
            self._guides = out
            self._guides_changed()

    def clear_guides(self) -> None:
        if self._guides:
            self._guides = []
            self._guides_changed()

    def page_guide_lines(self, index: int) -> dict[str, list[float]]:
        """Guide della pagina in coordinate pagina (non ruotata, come testo e disegni):
        {"x": [x delle linee verticali], "y": [y delle linee orizzontali]}, per l'aggancio.

        Su una pagina con /Rotate 90 una guida verticale della pagina visualizzata è una linea
        con y costante nelle coordinate pagina: finisce in "y"."""
        g = self._geoms[index]
        to_page = ~(g.matrix * ~g.view)        # pagina visualizzata -> coordinate pagina
        out: dict[str, list[float]] = {"x": [], "y": []}
        for o, v in self.guides_on_page(index):
            if o == GUIDE_VERTICAL:
                a, b = pymupdf.Point(v, 0) * to_page, pymupdf.Point(v, 100) * to_page
            else:
                a, b = pymupdf.Point(0, v) * to_page, pymupdf.Point(100, v) * to_page
            if abs(a.x - b.x) < 1e-6:
                out["x"].append(a.x)
            else:
                out["y"].append(a.y)
        return out

    @property
    def guides_visible(self) -> bool:
        return self._guides_visible

    def set_guides_visible(self, visible: bool) -> None:
        """Mostra o nasconde le guide (restano memorizzate)."""
        self._guides_visible = bool(visible)
        self.viewport().update()

    @property
    def guides_locked(self) -> bool:
        return self._guides_locked

    def set_guides_locked(self, locked: bool) -> None:
        """Con le guide bloccate non si possono spostare né eliminare trascinandole."""
        self._guides_locked = bool(locked)

    def _guides_changed(self) -> None:
        self.viewport().update()
        self._update_rulers()
        self.guidesChanged.emit()

    def _guides_interactive(self) -> bool:
        """Le guide si trascinano con i righelli visibili, se sono visibili e non bloccate."""
        return self._rulers_visible and self._guides_visible and not self._guides_locked and bool(self._guides)

    def _guide_line(self, index: int, orientation: str, value: float) -> QLineF:
        """Segmento della guida sulla pagina `index`, in coordinate del viewport."""
        w, h = self.display_size(index)
        t = self.display_transform(index)
        if orientation == GUIDE_VERTICAL:
            return QLineF(t.map(QPointF(value, 0)), t.map(QPointF(value, h)))
        return QLineF(t.map(QPointF(0, value)), t.map(QPointF(w, value)))

    def _guide_at(self, pos: QPointF) -> tuple[int, int] | None:
        """(indice in guides(), pagina) della guida sotto `pos`, entro GUIDE_HIT_PX pixel."""
        best = None
        m = GUIDE_HIT_PX
        for i in self.visible_pages():
            if not self.page_rect(i).adjusted(-m, -m, m, m).contains(pos):
                continue
            for n, (o, v, page) in enumerate(self._guides):
                if page is not None and page != i:
                    continue
                d = _distance_to_segment(self._guide_line(i, o, v), pos)
                if d <= m and (best is None or d < best[0]):
                    best = (d, n, i)
        return None if best is None else (best[1], best[2])

    def _horizontal_on_screen(self, orientation: str) -> bool:
        return (orientation == GUIDE_HORIZONTAL) != (self._view_rotation in (90, 270))

    def _paint_guides(self, p: QPainter, index: int) -> None:
        moving = self._guide_drag.get("index") if self._guide_drag is not None else None
        p.save()
        p.setClipRect(self.page_rect(index))
        p.setPen(QPen(theme.color("accent"), 0))
        for n, (o, v, page) in enumerate(self._guides):
            if n == moving or (page is not None and page != index):
                continue
            p.drawLine(self._guide_line(index, o, v))
        p.restore()

    def _paint_guide_preview(self, p: QPainter) -> None:
        d = self._guide_drag
        if d is None or d.get("pos") is None:
            return
        pos = d["pos"]
        p.save()
        p.setPen(QPen(theme.color("accent"), 0, Qt.PenStyle.DashLine))
        if d["screen"] == "h":
            p.drawLine(QPointF(0, pos.y()), QPointF(self.viewport().width(), pos.y()))
        else:
            p.drawLine(QPointF(pos.x(), 0), QPointF(pos.x(), self.viewport().height()))
        p.restore()

    def _begin_new_guide(self, from_horizontal_ruler: bool, pos: QPointF) -> None:
        """Inizio del trascinamento da un righello: quello in alto crea guide orizzontali sullo schermo."""
        self._guide_drag = {"index": None, "screen": "h" if from_horizontal_ruler else "v", "pos": QPointF(pos)}
        self.viewport().update()

    def _begin_guide_move(self, index: int, pos: QPointF) -> None:
        horizontal = self._horizontal_on_screen(self._guides[index][0])
        self._guide_drag = {"index": index, "screen": "h" if horizontal else "v", "pos": QPointF(pos)}
        self.viewport().update()

    def _guide_drag_move(self, pos: QPointF) -> None:
        if self._guide_drag is None:
            return
        self._guide_drag["pos"] = QPointF(pos)
        self._pointer = QPointF(pos)
        self.viewport().update()
        self._update_rulers()

    def _guide_drag_end(self, pos: QPointF, modifiers) -> None:
        """Fine del trascinamento: crea o sposta la guida; fuori dalle pagine la elimina."""
        d, self._guide_drag = self._guide_drag, None
        if d is None:
            return
        target = self._guide_target(d, pos, modifiers)
        n = d["index"]
        if n is None:
            if target is not None:
                self.add_guide(*target)
        elif n < len(self._guides):
            if target is None or (target != self._guides[n] and target in self._guides):
                del self._guides[n]
                self._guides_changed()
            elif target != self._guides[n]:
                self._guides[n] = target
                self._guides_changed()
        self._guide_hover = False
        self.viewport().setCursor(self.tool.cursor)
        self.viewport().update()

    def _guide_target(self, d: dict, pos: QPointF, modifiers) -> tuple[str, float, int | None] | None:
        """Guida che risulta rilasciando in `pos` (None = fuori dalle pagine: nessuna guida)."""
        inset = self._inset()
        if not QRectF(self.viewport().rect()).contains(pos) or pos.x() < inset or pos.y() < inset:
            return None                      # fuori dalla vista o di nuovo sopra i righelli
        hit = self.view_to_page(pos)
        if hit is None:
            return None
        page = hit[0]
        inv, _ok = self.display_transform(page).inverted()
        dp = inv.map(pos)
        if (d["screen"] == "h") != (self._view_rotation in (90, 270)):
            orientation, value = GUIDE_HORIZONTAL, dp.y()
        else:
            orientation, value = GUIDE_VERTICAL, dp.x()
        n = d["index"]
        if n is None:                        # nuova: sulla pagina, con Ctrl su tutte
            target_page = None if modifiers & Qt.KeyboardModifier.ControlModifier else page
        else:                                # spostata: una guida di tutte le pagine resta tale
            target_page = None if self._guides[n][2] is None else page
        return orientation, round(value, 2), target_page

    # ================================================================ scorrimento automatico
    def _autoscroll_delta(self, pos: QPointF) -> tuple[int, int]:
        """Passo (pixel) dello scorrimento automatico col puntatore in `pos`; (0, 0) = nessuno."""
        inset = self._inset()
        w, h = self.viewport().width(), self.viewport().height()

        def speed(distance: float) -> int:
            return int(min(80.0, 4.0 + distance * 0.6))

        dx = dy = 0
        edge = AUTOSCROLL_EDGE
        if pos.y() < inset + edge:
            dy = -speed(inset + edge - pos.y())
        elif pos.y() > h - edge:
            dy = speed(pos.y() - (h - edge))
        if pos.x() < inset + edge:
            dx = -speed(inset + edge - pos.x())
        elif pos.x() > w - edge:
            dx = speed(pos.x() - (w - edge))
        return dx, dy

    def _update_autoscroll(self, pos: QPointF, buttons, modifiers) -> None:
        self._autoscroll_state = (QPointF(pos), buttons, modifiers)
        dx, dy = self._autoscroll_delta(pos)
        if dx or dy:
            if not self._autoscroll_timer.isActive():
                self._autoscroll_timer.start()
        else:
            self._autoscroll_timer.stop()

    def _stop_autoscroll(self) -> None:
        self._autoscroll_timer.stop()
        self._autoscroll_state = None

    def _autoscroll_step(self) -> None:
        """Scorre di un passo e ripete `move()` dello strumento con la stessa posizione del puntatore."""
        state = self._autoscroll_state
        if state is None or not self._tool_dragging or not getattr(self.tool, "autoscroll", False):
            self._stop_autoscroll()
            return
        pos, buttons, modifiers = state
        dx, dy = self._autoscroll_delta(pos)
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        before = (hbar.value(), vbar.value())
        if dx:
            hbar.setValue(hbar.value() + dx)
        if dy:
            vbar.setValue(vbar.value() + dy)
        if (hbar.value(), vbar.value()) == before:
            self._autoscroll_timer.stop()     # al limite: riparte al prossimo movimento
            return
        hit = self.view_to_page(pos)
        pe = PageEvent(page=hit[0] if hit else None, point=hit[1] if hit else None, pos=QPointF(pos),
                       button=Qt.MouseButton.NoButton, buttons=buttons, modifiers=modifiers, event=None)
        try:
            if self.tool.move(self, pe):
                self.viewport().update()
        except Exception as e:
            self._stop_autoscroll()
            self.status(f"Errore nello strumento attivo: {e}")

    # ================================================================ eventi
    def event(self, ev) -> bool:
        # Tab e Maiusc+Tab vanno prima allo strumento attivo (es. per scorrere gli oggetti):
        # se li gestisce non spostano il fuoco, altrimenti vale il comportamento normale.
        if (ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                and not ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            try:
                handled = self.tool.key(self, ev)
            except Exception as e:
                handled = False
                self.status(f"Errore nello strumento attivo: {e}")
            if handled:
                ev.accept()
                self.viewport().update()
                return True
        return super().event(ev)

    def resizeEvent(self, event) -> None:
        page = self._current
        super().resizeEvent(event)
        self._place_rulers()
        self._relayout()
        if self._fit:
            self.go_to_page(page)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()
        self._update_rulers()
        self._update_current_from_scroll()
        self._schedule_visible()

    def _page_event(self, ev) -> PageEvent:
        pos = ev.position()
        hit = self.view_to_page(pos)
        return PageEvent(
            page=hit[0] if hit else None,
            point=hit[1] if hit else None,
            pos=pos,
            button=ev.button(),
            buttons=ev.buttons(),
            modifiers=ev.modifiers(),
            event=ev,
        )

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        if ev.button() == Qt.MouseButton.MiddleButton:
            self.hand_tool.press(self, PageEvent(None, None, ev.position(), Qt.MouseButton.LeftButton,
                                                 ev.buttons(), ev.modifiers(), ev))
            self._middle_pan = True
            return
        if ev.button() == Qt.MouseButton.LeftButton and self._guides_interactive():
            hit = self._guide_at(ev.position())
            if hit is not None:              # guida sotto il puntatore: si sposta o si elimina
                self._begin_guide_move(hit[0], ev.position())
                ev.accept()
                return
        if self.tool.press(self, self._page_event(ev)):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._tool_dragging = True
            ev.accept()
            self.viewport().update()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        self._pointer = QPointF(pos)
        if self._guide_drag is not None:
            self._guide_drag_move(pos)
            ev.accept()
            return
        self._update_rulers()
        if getattr(self, "_middle_pan", False):
            self.hand_tool.move(self, PageEvent(None, None, ev.position(), Qt.MouseButton.NoButton,
                                                ev.buttons(), ev.modifiers(), ev))
            return
        if self._guides_interactive() and ev.buttons() == Qt.MouseButton.NoButton:
            hit = self._guide_at(pos)
            if hit is not None:
                horizontal = self._horizontal_on_screen(self._guides[hit[0]][0])
                self.viewport().setCursor(Qt.CursorShape.SplitVCursor if horizontal else Qt.CursorShape.SplitHCursor)
                self._guide_hover = True
                return
        if self._guide_hover:
            self._guide_hover = False
            self.viewport().setCursor(self.tool.cursor)
        if self.tool.move(self, self._page_event(ev)):
            ev.accept()
            self.viewport().update()
        else:
            super().mouseMoveEvent(ev)
        if (self._tool_dragging and ev.buttons() & Qt.MouseButton.LeftButton
                and getattr(self.tool, "autoscroll", False)):
            self._update_autoscroll(pos, ev.buttons(), ev.modifiers())

    def mouseReleaseEvent(self, ev) -> None:
        if self._guide_drag is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._guide_drag_end(ev.position(), ev.modifiers())
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            self._tool_dragging = False
            self._stop_autoscroll()
        if getattr(self, "_middle_pan", False) and ev.button() == Qt.MouseButton.MiddleButton:
            self._middle_pan = False
            self.hand_tool.release(self, PageEvent(None, None, ev.position(), Qt.MouseButton.LeftButton,
                                                   ev.buttons(), ev.modifiers(), ev))
            self.viewport().setCursor(self.tool.cursor)
            return
        if self.tool.release(self, self._page_event(ev)):
            ev.accept()
            self.viewport().update()
        else:
            super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if self.tool.double_click(self, self._page_event(ev)):
            ev.accept()
            self.viewport().update()
        else:
            super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev) -> None:
        from ..i18n import _
        pos = QPointF(ev.pos())
        hit = self.view_to_page(pos)
        pe = PageEvent(page=hit[0] if hit else None, point=hit[1] if hit else None, pos=pos,
                       button=Qt.MouseButton.RightButton, buttons=Qt.MouseButton.RightButton,
                       modifiers=ev.modifiers(), event=ev)
        menu = QMenu(self)
        if self.selected_text():
            act = menu.addAction(_("Copia"), self.copy_selection)
            act.setEnabled(self.document.can_copy)
        for provider in list(self.menu_providers):
            try:
                provider(self, pe, menu)
            except Exception as e:
                self.status(f"Errore nel menu contestuale: {e}")
        if not menu.isEmpty():
            self.exec_menu(menu, ev.globalPos())
        menu.deleteLater()

    def keyboard_menu_pos(self) -> QPoint:
        """Dove aprire il menu contestuale da tastiera: al centro della parte visibile della
        selezione di testo, altrimenti al centro della vista (coordinate del viewport)."""
        area = self.viewport().rect()
        shown = QRectF()
        for sel in self.text_selections:
            if 0 <= sel.page < len(self._geoms):
                for rect in sel.rects:
                    shown = shown.united(self.page_to_view(sel.page, rect))
        shown = shown.intersected(QRectF(area))
        return shown.center().toPoint() if not shown.isEmpty() else area.center()

    def exec_menu(self, menu: QMenu, global_pos) -> None:
        """Mostra il menu contestuale (separato per poterlo simulare nei test)."""
        menu.exec(global_pos)

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = ev.angleDelta().y()
            if delta:
                self.set_zoom(self._zoom * math.pow(1.0015, delta), anchor=ev.position())
            ev.accept()
            return
        if self._view_mode == VIEW_SINGLE:
            vbar = self.verticalScrollBar()
            delta = ev.angleDelta().y()
            if delta < 0 and vbar.value() >= vbar.maximum() and self._current < len(self._geoms) - 1:
                self.go_to_page(self._current + 1)
                ev.accept()
                return
            if delta > 0 and vbar.value() <= vbar.minimum() and self._current > 0:
                self.go_to_page(self._current - 1)
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
                ev.accept()
                return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape and self._guide_drag is not None:
            self._guide_drag = None           # trascinamento di una guida annullato
            self.viewport().update()
            return
        if self.tool.key(self, ev):
            ev.accept()
            self.viewport().update()
            return
        if ev.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        if ev.matches(QKeySequence.StandardKey.SelectAll):
            self._select_all_on_page()
            return
        key = ev.key()
        mods = ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key == Qt.Key.Key_Escape:
            if self._tool is not None:
                self.set_tool(None)       # torna allo strumento Seleziona
            elif self.text_selection is not None:
                self.clear_text_selection()
            return
        if mods == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_N:
            self.next_page()
            return
        if mods == Qt.KeyboardModifier.NoModifier and key == Qt.Key.Key_P:
            self.previous_page()
            return
        if key == Qt.Key.Key_Space and mods in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            action = (QAbstractSlider.SliderAction.SliderPageStepSub
                      if mods == Qt.KeyboardModifier.ShiftModifier else
                      QAbstractSlider.SliderAction.SliderPageStepAdd)
            self.verticalScrollBar().triggerAction(action)
            return
        if (mods == Qt.KeyboardModifier.NoModifier and key in (Qt.Key.Key_Right, Qt.Key.Key_Left)
                and self.horizontalScrollBar().maximum() == 0):
            self.next_page() if key == Qt.Key.Key_Right else self.previous_page()
            return
        if key == Qt.Key.Key_Home:
            self.go_to_page(0)
            return
        if key == Qt.Key.Key_End:
            self.go_to_page(len(self._geoms) - 1)
            return
        if self._view_mode == VIEW_SINGLE and key == Qt.Key.Key_PageDown and (
                self.verticalScrollBar().value() >= self.verticalScrollBar().maximum()):
            self.next_page()
            return
        if self._view_mode == VIEW_SINGLE and key == Qt.Key.Key_PageUp and (
                self.verticalScrollBar().value() <= self.verticalScrollBar().minimum()):
            self.previous_page()
            return
        super().keyPressEvent(ev)

    def _select_all_on_page(self) -> None:
        words = self.words(self._current)
        if words:
            from .tools import selection_from_words
            self.set_text_selection(selection_from_words(self._current, words))
