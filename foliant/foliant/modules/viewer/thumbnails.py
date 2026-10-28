"""Pannello Miniature (VIS-14).

Le miniature sono generate solo per le voci che la lista disegna (cioè quelle
visibili), un po' alla volta con un timer e senza mai bloccare l'interfaccia
mentre un'attività in background usa il motore. Restano solo in memoria
(anche per i documenti cifrati nulla viene scritto su disco).
"""

from __future__ import annotations

from collections import OrderedDict

import pymupdf
from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListView, QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from foliant.api import (
    ENGINE_LOCK, Context, DocumentView, _, add_translations, page_geometry, theme, try_engine,
)

from .engine import page_labels

TRANSLATIONS = {
    "Miniature": "Thumbnails",
    "Dimensione delle miniature": "Thumbnail size",
    "Mostra/nascondi miniature": "Show/hide thumbnails",
    "Nessun documento aperto": "No document open",
    "Pagina non leggibile": "Unreadable page",
    "Vista": "View",
}

CACHE_ITEMS = 600
MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 72, 280, 132
LABEL_H = 20
PAD = 10


class ThumbnailModel(QAbstractListModel):
    def __init__(self, panel: "ThumbnailPanel"):
        super().__init__(panel)
        self.panel = panel
        self.count = 0

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.count

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.panel.label(index.row())
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.panel.label(index.row())
        return None

    def reset(self, count: int) -> None:
        self.beginResetModel()
        self.count = count
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, panel: "ThumbnailPanel"):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index) -> QSize:
        s = self.panel.thumb_size
        return QSize(s + 2 * PAD, s + LABEL_H + 2 * PAD)

    def image_rect(self, cell: QRect, row: int) -> QRectF:
        """Riquadro dell'immagine della pagina dentro la cella."""
        s = self.panel.thumb_size
        w, h = self.panel.page_aspect(row)
        scale = min(s / w, s / h)
        iw, ih = w * scale, h * scale
        x = cell.x() + (cell.width() - iw) / 2
        y = cell.y() + PAD + (s - ih) / 2
        return QRectF(x, y, iw, ih)

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.row()
        panel = self.panel
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        current = row == panel.current_page
        cell = option.rect.adjusted(2, 2, -2, -2)
        if selected or hover:
            bg = theme.color("accent_soft") if selected else theme.color("surface_alt")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(cell), 6, 6)
        r = self.image_rect(option.rect, row)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("page_shadow"))
        painter.drawRect(r.adjusted(1, 2, 2, 3))
        painter.fillRect(r, QColor(255, 255, 255))
        img = panel.image(row)
        if img is not None:
            painter.drawImage(r, img, QRectF(0, 0, img.width(), img.height()))
        elif row in panel.failed:
            painter.setPen(theme.color("danger"))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, _("Pagina non leggibile"))
        if current:
            vis = panel.visible_area(row, r)
            if vis is not None:
                fill = theme.color("accent")
                fill.setAlpha(40)
                painter.setBrush(fill)
                painter.setPen(QPen(theme.color("accent"), 1.5))
                painter.drawRect(vis)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color("accent"), 2.5))
            painter.drawRect(r.adjusted(-2, -2, 2, 2))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color("border"), 1))
            painter.drawRect(r)
        text_rect = QRectF(option.rect.x(), option.rect.bottom() - LABEL_H - PAD / 2, option.rect.width(), LABEL_H)
        painter.setPen(theme.color("accent") if current else theme.color("text_muted"))
        f = painter.font()
        f.setBold(current)
        painter.setFont(f)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, panel.label(row))
        painter.restore()
        if img is None and row not in panel.failed:
            panel.request(row)


class ThumbnailPanel(QWidget):
    pageClicked = Signal(int)

    def __init__(self, ctx: Context):
        super().__init__()
        self.ctx = ctx
        self.view: DocumentView | None = None
        self.thumb_size = int(ctx.settings().value("viewer/thumb_size", DEFAULT_SIZE) or DEFAULT_SIZE)
        self.current_page = 0
        self._cache: "OrderedDict[tuple, QImage]" = OrderedDict()
        self._geom: dict[int, tuple[float, float, pymupdf.Matrix]] = {}
        self._labels: list[str] | None = None
        self._labels_version = -1
        self._queue: list[int] = []
        self.failed: set[int] = set()
        self._drag: tuple[int, QPointF, pymupdf.Point] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 12, 6)
        title = QLabel(_("Miniature"))
        title.setProperty("heading", True)
        head.addWidget(title)
        head.addStretch(1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_SIZE, MAX_SIZE)
        self.slider.setValue(self.thumb_size)
        self.slider.setFixedWidth(90)
        self.slider.setToolTip(_("Dimensione delle miniature"))
        self.slider.valueChanged.connect(self.set_thumb_size)
        head.addWidget(self.slider)
        layout.addLayout(head)
        self.empty = QLabel(_("Nessun documento aperto"))
        self.empty.setProperty("muted", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        self.list = QListView(self)
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.setSpacing(0)
        self.model = ThumbnailModel(self)
        self.delegate = ThumbnailDelegate(self)
        self.list.setModel(self.model)
        self.list.setItemDelegate(self.delegate)
        self.list.clicked.connect(self._on_clicked)
        self.list.activated.connect(self._on_clicked)
        self.list.viewport().installEventFilter(self)
        layout.addWidget(self.list, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_step)
        theme.on_change(self.list.viewport().update)
        self._update_empty()

    # ------------------------------------------------------------------ vista
    def set_view(self, view: DocumentView | None) -> None:
        if view is self.view:
            return
        if self.view is not None:
            for sig, slot in ((self.view.currentPageChanged, self._on_page),
                              (self.view.contentChanged, self._on_content),
                              (self.view.layoutChanged, self._on_layout)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            try:
                self.view.verticalScrollBar().valueChanged.disconnect(self._on_scroll)
                self.view.horizontalScrollBar().valueChanged.disconnect(self._on_scroll)
            except (RuntimeError, TypeError):
                pass
        self.view = view
        self._clear()
        if view is not None:
            view.currentPageChanged.connect(self._on_page)
            view.contentChanged.connect(self._on_content)
            view.layoutChanged.connect(self._on_layout)
            view.verticalScrollBar().valueChanged.connect(self._on_scroll)
            view.horizontalScrollBar().valueChanged.connect(self._on_scroll)
            self.current_page = view.current_page
            self.model.reset(view.page_count)
            self._select_current()
        else:
            self.model.reset(0)
        self._update_empty()

    def _update_empty(self) -> None:
        has = self.view is not None
        self.empty.setVisible(not has)
        self.list.setVisible(has)
        self.slider.setEnabled(has)

    def _clear(self) -> None:
        self._cache.clear()
        self._geom.clear()
        self._queue.clear()
        self.failed.clear()
        self._labels = None

    def _on_content(self) -> None:
        if self.view is None:
            return
        self._clear()
        self.current_page = self.view.current_page
        self.model.reset(self.view.page_count)
        self._select_current()

    def refresh(self) -> None:
        """Rigenera le miniature (es. dopo un cambio di visibilità dei livelli)."""
        self._cache.clear()
        self.failed.clear()
        self.list.viewport().update()

    def _on_page(self, page: int) -> None:
        old = self.current_page
        self.current_page = page
        self._update_row(old)
        self._select_current()

    def _on_layout(self) -> None:
        self._update_row(self.current_page)

    def _on_scroll(self, *_a) -> None:
        if self._drag is None:
            self._update_row(self.current_page)

    def _update_row(self, row: int) -> None:
        if 0 <= row < self.model.count:
            self.list.update(self.model.index(row))

    def _select_current(self) -> None:
        if not (0 <= self.current_page < self.model.count):
            return
        index = self.model.index(self.current_page)
        sel = self.list.selectionModel()
        if len(sel.selectedIndexes()) <= 1:
            self.list.setCurrentIndex(index)
        self.list.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        self.list.viewport().update()

    def _on_clicked(self, index: QModelIndex) -> None:
        from PySide6.QtWidgets import QApplication
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return  # selezione multipla per le operazioni sulle pagine
        self.go_to(index.row())

    def go_to(self, row: int) -> None:
        if self.view is not None and 0 <= row < self.view.page_count:
            self.view.go_to_page(row)
            self.pageClicked.emit(row)

    def selected_pages(self) -> list[int]:
        return sorted(i.row() for i in self.list.selectionModel().selectedIndexes())

    # --------------------------------------------------------------- dimensioni
    def set_thumb_size(self, size: int) -> None:
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self.ctx.settings().setValue("viewer/thumb_size", size)
        if self.slider.value() != size:
            self.slider.setValue(size)
        self._cache.clear()
        self.list.doItemsLayout()
        self.list.viewport().update()

    def label(self, row: int) -> str:
        if self.view is None:
            return str(row + 1)
        if self._labels is None or self._labels_version != self.view.document.version:
            with try_engine() as ok:
                if not ok:
                    return str(row + 1)
                self._labels = page_labels(self.view.document.pdf)
                self._labels_version = self.view.document.version
        label = self._labels[row] if row < len(self._labels) else ""
        return label if label else str(row + 1)

    def page_aspect(self, row: int) -> tuple[float, float]:
        g = self._geometry(row)
        return (g[0], g[1]) if g else (595.0, 842.0)

    def _geometry(self, row: int):
        g = self._geom.get(row)
        if g is None and self.view is not None and not self.view.document.closed:
            with try_engine() as ok:
                if ok and row < self.view.document.pdf.page_count:
                    pg = page_geometry(self.view.document.pdf[row], 0)
                    g = (pg.width, pg.height, pg.matrix)
                    self._geom[row] = g
        return g

    # ----------------------------------------------------------------- resa
    def image(self, row: int) -> QImage | None:
        if self.view is None:
            return None
        key = (row, self.view.document.version, self.thumb_size)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
        return img

    def request(self, row: int) -> None:
        if row not in self._queue:
            self._queue.append(row)
        if not self._timer.isActive():
            self._timer.start(0)

    def _render_one(self, row: int) -> None:
        view = self.view
        if view is None or view.document.closed or row >= view.page_count:
            return
        key = (row, view.document.version, self.thumb_size)
        if key in self._cache:
            return
        dpr = self.devicePixelRatioF() or 1.0
        try:
            page = view.document.pdf[row]
            g = page_geometry(page, 0)
            self._geom[row] = (g.width, g.height, g.matrix)
            scale = min(self.thumb_size / g.width, self.thumb_size / g.height) * dpr
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        except Exception:
            self.failed.add(row)
            return
        self._cache[key] = img
        while len(self._cache) > CACHE_ITEMS:
            self._cache.popitem(last=False)

    def _render_step(self) -> None:
        if not self._queue or self.view is None:
            return
        if not ENGINE_LOCK.acquire(blocking=False):
            self._timer.start(40)
            return
        try:
            # le richieste più recenti sono quelle visibili ora
            row = self._queue.pop()
            self._render_one(row)
        finally:
            ENGINE_LOCK.release()
        self._update_row(row)
        if self._queue:
            self._timer.start(0)

    def flush(self) -> None:
        """Genera subito le miniature in attesa (test e catture)."""
        self.list.viewport().repaint()
        while self._queue:
            row = self._queue.pop()
            with ENGINE_LOCK:
                self._render_one(row)
        self._timer.stop()
        self.list.viewport().update()

    # --------------------------------------------------------- area visibile
    def _page_to_thumb(self, row: int, image_rect: QRectF):
        g = self._geometry(row)
        if g is None:
            return None
        w, h, m = g
        sx, sy = image_rect.width() / w, image_rect.height() / h

        def f(pt: pymupdf.Point) -> QPointF:
            p = pt * m
            return QPointF(image_rect.x() + p.x * sx, image_rect.y() + p.y * sy)

        return f

    def visible_area(self, row: int, image_rect: QRectF) -> QRectF | None:
        """Parte della pagina visibile nella vista principale, in coordinate della miniatura."""
        view = self.view
        if view is None or row >= view.page_count:
            return None
        inv, ok = view.page_transform(row).inverted()
        if not ok:
            return None
        vp = view.viewport().rect()
        corners = [inv.map(QPointF(vp.left(), vp.top())), inv.map(QPointF(vp.right(), vp.bottom())),
                   inv.map(QPointF(vp.left(), vp.bottom())), inv.map(QPointF(vp.right(), vp.top()))]
        f = self._page_to_thumb(row, image_rect)
        if f is None:
            return None
        pts = [f(pymupdf.Point(c.x(), c.y())) for c in corners]
        xs, ys = [p.x() for p in pts], [p.y() for p in pts]
        r = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).intersected(image_rect)
        if r.isEmpty() or (r.width() >= image_rect.width() - 0.5 and r.height() >= image_rect.height() - 0.5):
            return None if r.isEmpty() else r
        return r

    def thumb_to_page(self, row: int, pos: QPointF) -> pymupdf.Point | None:
        """Punto della pagina (coordinate non ruotate) sotto un punto della miniatura."""
        cell = self.list.visualRect(self.model.index(row))
        r = self.delegate.image_rect(cell, row)
        g = self._geometry(row)
        if g is None or r.width() <= 0:
            return None
        w, h, m = g
        p = pymupdf.Point((pos.x() - r.x()) * w / r.width(), (pos.y() - r.y()) * h / r.height())
        return p * ~m

    def move_view_to(self, row: int, top_left: pymupdf.Point) -> None:
        """Sposta la vista principale perché `top_left` (punto pagina) sia nell'angolo in alto a sinistra."""
        view = self.view
        if view is None:
            return
        if row != view.current_page:
            view.go_to_page(row)
        p = view.page_transform(row).map(QPointF(top_left.x, top_left.y))
        view.verticalScrollBar().setValue(int(round(view.verticalScrollBar().value() + p.y())))
        view.horizontalScrollBar().setValue(int(round(view.horizontalScrollBar().value() + p.x())))
        self._update_row(row)

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QEvent.Type.Wheel and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 12 if ev.angleDelta().y() > 0 else -12
            self.set_thumb_size(self.thumb_size + step)
            return True
        if self.view is None:
            return False
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            row = self.current_page
            cell = self.list.visualRect(self.model.index(row))
            r = self.delegate.image_rect(cell, row)
            vis = self.visible_area(row, r)
            if vis is not None and vis.contains(ev.position()):
                inv, _ok = self.view.page_transform(row).inverted()
                tl = inv.map(QPointF(0, 0))
                self._drag = (row, ev.position(), pymupdf.Point(tl.x(), tl.y()))
                return True
        elif t == QEvent.Type.MouseMove and self._drag is not None:
            self._drag_to(ev.position())
            return True
        elif t == QEvent.Type.MouseButtonRelease and self._drag is not None:
            self._drag_to(ev.position())
            self._drag = None
            self._update_row(self.current_page)
            return True
        return False

    def _drag_to(self, pos: QPointF) -> None:
        row, start, start_tl = self._drag
        a = self.thumb_to_page(row, start)
        b = self.thumb_to_page(row, pos)
        if a is None or b is None:
            return
        self.move_view_to(row, pymupdf.Point(start_tl.x + b.x - a.x, start_tl.y + b.y - a.y))


class ThumbnailsController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.panel = ThumbnailPanel(ctx)
        ctx.add_sidebar_panel("thumbnails", _("Miniature"), "gallery-vertical-end", self.panel, order=10)
        self.act_toggle = ctx.action(_("Mostra/nascondi miniature"), "gallery-vertical-end", self.toggle,
                                     shortcut="F4")
        ctx.add_menu_action(self.act_toggle, _("Vista"))

    def toggle(self) -> None:
        w = self.ctx.window
        toggle = getattr(w, "_toggle_sidebar_panel", None)
        if toggle is not None:
            toggle("thumbnails")
        else:
            self.ctx.show_sidebar_panel("thumbnails")


def install(ctx: Context) -> ThumbnailsController:
    add_translations("en", TRANSLATIONS)
    return ThumbnailsController(ctx)
