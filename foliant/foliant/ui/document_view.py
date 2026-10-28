"""Vista del documento: impaginazione, zoom, rendering progressivo, strumenti.

Sistemi di coordinate:
- *pagina*: punti PDF della pagina non ruotata, origine in alto a sinistra
  (le coordinate del motore per testo, annotazioni, disegno);
- *viewport*: pixel logici del widget visibile.
`page_transform(i)` converte pagina -> viewport; `view_to_page()` l'inverso.

Resa: sotto `TILE_THRESHOLD` pixel ogni pagina visibile è un'unica immagine; oltre
(zoom elevati, pagine enormi) si disegnano solo le tasselle visibili, `TILE_SIZE` pixel
di lato, con la stessa matrice della resa intera e un ritaglio (`clip`).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QFrame, QMenu

from ..core import placement
from ..core.document import CLOSED, CONTENT, Document
from ..core.engine import ENGINE_LOCK
from . import theme
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


@dataclass
class PageGeometry:
    width: float             # punti, come visualizzata (rotazioni incluse)
    height: float
    matrix: pymupdf.Matrix   # pagina non ruotata -> punti visualizzati


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
    return PageGeometry(w2, h2, m * extra)


class DocumentView(QAbstractScrollArea):
    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)
    selectionChanged = Signal()
    toolChanged = Signal(object)
    contentChanged = Signal()
    layoutChanged = Signal()
    statusMessage = Signal(str)

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
        # Disegni aggiuntivi dei moduli: callable(view, painter, page_index), painter in coordinate pagina.
        self.overlays: list = []
        # Voci del menu contestuale (tasto destro): callable(view, page_event, menu).
        # La finestra principale condivide qui la lista registrata dai moduli.
        self.menu_providers: list = []
        self.text_selection: TextSelection | None = None
        self.search_hits: dict[int, list] = {}
        self.current_hit: tuple[int, int] | None = None
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
        self._bridge.detach()
        self._cache.clear()
        self._stale.clear()
        self._tiles.clear()
        self._tiles_bytes = 0

    # ================================================================ strumenti
    def set_tool(self, tool: Tool | None) -> None:
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

    def _available_size(self) -> tuple[float, float]:
        vbar = self.verticalScrollBar().sizeHint().width()
        w = self.viewport().width()
        if self.verticalScrollBar().isHidden():
            w -= vbar
        return max(80.0, w - 2 * self.MARGIN), max(80.0, self.viewport().height() - 2 * self.MARGIN)

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
        y = self.MARGIN
        max_w = 0.0
        laid: list[tuple[list[int], float, float]] = []
        for row in rows:
            row_w = sum(self._geoms[i].width * s for i in row) + self.GAP * (len(row) - 1)
            row_h = max(self._geoms[i].height * s for i in row)
            laid.append((row, row_w, row_h))
            max_w = max(max_w, row_w)
        content_w = max(float(self.viewport().width()), max_w + 2 * self.MARGIN)
        for row, row_w, row_h in laid:
            x = (content_w - row_w) / 2
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
        self._schedule_visible()

    def page_rect(self, index: int) -> QRectF:
        """Rettangolo della pagina nel viewport (pixel)."""
        return self._rects[index].translated(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())

    def page_transform(self, index: int) -> QTransform:
        """Trasformazione coordinate pagina -> viewport."""
        g = self._geoms[index]
        r = self._rects[index]
        s = self._scale()
        m = g.matrix
        return (QTransform(m.a, m.b, m.c, m.d, m.e, m.f)
                * QTransform.fromScale(s, s)
                * QTransform.fromTranslate(r.x() - self.horizontalScrollBar().value(),
                                           r.y() - self.verticalScrollBar().value()))

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
        y = r.top() - self.MARGIN / 2
        if point is not None:
            p = self.page_transform(index).map(QPointF(point.x, point.y))
            y = p.y() + self.verticalScrollBar().value() - self.MARGIN
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
        if self.text_selection is not None:
            self.text_selection = None
            self.selectionChanged.emit()
        top = self.verticalScrollBar().value()
        self._reload_geometry()
        self.verticalScrollBar().setValue(top)
        self._update_current_from_scroll()
        self.contentChanged.emit()

    def _invalidate_render(self, keep_stale: bool = False) -> None:
        self._failed.clear()
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
        self.text_selection = selection
        self.viewport().update()
        self.selectionChanged.emit()

    def clear_text_selection(self) -> None:
        if self.text_selection is not None:
            self.set_text_selection(None)

    def selected_text(self) -> str:
        return self.text_selection.text if self.text_selection else ""

    def set_search_hits(self, hits: dict[int, list], current: tuple[int, int] | None = None) -> None:
        self.search_hits = hits
        self.current_hit = current
        self.viewport().update()

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
        except Exception as e:  # una pagina danneggiata non deve bloccare la vista
            self._failed.add(key)
            self.status(f"Errore nel disegno di una pagina: {e}")
        finally:
            ENGINE_LOCK.release()
        self.viewport().update()
        if self._queue:
            self._render_timer.start(0)

    def flush_rendering(self) -> None:
        """Disegna subito le pagine (o le tasselle) visibili in attesa (test, stampa, catture)."""
        self._schedule_visible()
        while self._queue:
            with ENGINE_LOCK:
                self._render_key(self._queue.pop(0))
        self._render_timer.stop()
        self.viewport().update()

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
        if missing and not self._render_timer.isActive():
            self._schedule_visible()

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
        sel = self.text_selection
        if sel is not None and sel.page == index:
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

    # ================================================================ eventi
    def resizeEvent(self, event) -> None:
        page = self._current
        super().resizeEvent(event)
        self._relayout()
        if self._fit:
            self.go_to_page(page)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()
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
        if self.tool.press(self, self._page_event(ev)):
            ev.accept()
            self.viewport().update()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if getattr(self, "_middle_pan", False):
            self.hand_tool.move(self, PageEvent(None, None, ev.position(), Qt.MouseButton.NoButton,
                                                ev.buttons(), ev.modifiers(), ev))
            return
        if self.tool.move(self, self._page_event(ev)):
            ev.accept()
            self.viewport().update()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
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
