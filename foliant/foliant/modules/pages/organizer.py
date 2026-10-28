"""Organizzatore pagine a griglia (PAG-01, PAG-04, PAG-14).

Miniature con rendering pigro: si disegnano solo le pagine visibili più un
margine; le altre restano segnaposto e la memoria resta limitata anche con
migliaia di pagine.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import TYPE_CHECKING, Callable

import pymupdf
from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QMimeData, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListView, QMenu, QSlider, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, icon, theme, try_engine

if TYPE_CHECKING:
    from foliant.api import DocumentView

PAGES_MIME = "application/x-foliant-pages"
MIN_THUMB = 90
MAX_THUMB = 420
DEFAULT_THUMB = 170
LABEL_HEIGHT = 42
CELL_PAD = 14


class PageInfo:
    __slots__ = ("width", "height", "rotation", "label", "annots", "odd_size")

    def __init__(self, width: float, height: float, rotation: int, label: str, annots: bool):
        self.width = width
        self.height = height
        self.rotation = rotation
        self.label = label
        self.annots = annots
        self.odd_size = False


def read_page_infos(pdf: pymupdf.Document) -> list[PageInfo]:
    """Dimensioni visualizzate, rotazione, etichetta e presenza di annotazioni."""
    infos: list[PageInfo] = []
    try:
        has_labels = bool(pdf.get_page_labels())
    except Exception:
        has_labels = False
    for i in range(pdf.page_count):
        try:
            page = pdf[i]
            r = page.rect
            label = ""
            if has_labels:
                try:
                    label = page.get_label() or ""
                except Exception:
                    label = ""
            annots = bool(page.first_annot)
            infos.append(PageInfo(r.width, r.height, page.rotation, label, annots))
        except Exception:
            infos.append(PageInfo(595, 842, 0, "", False))
    sizes: dict[tuple[int, int], int] = {}
    for info in infos:
        key = (round(min(info.width, info.height)), round(max(info.width, info.height)))
        sizes[key] = sizes.get(key, 0) + 1
    if sizes:
        common = max(sizes.items(), key=lambda kv: kv[1])[0]
        for info in infos:
            key = (round(min(info.width, info.height)), round(max(info.width, info.height)))
            info.odd_size = abs(key[0] - common[0]) > 2 or abs(key[1] - common[1]) > 2
    return infos


class PagesModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.infos: list[PageInfo] = []

    def set_infos(self, infos: list[PageInfo]) -> None:
        self.beginResetModel()
        self.infos = infos
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.infos)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.infos):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(index.row() + 1)
        if role == Qt.ItemDataRole.ToolTipRole:
            info = self.infos[index.row()]
            parts = [_("Pagina {n}").format(n=index.row() + 1)]
            if info.label and info.label != str(index.row() + 1):
                parts.append(_("Etichetta: {l}").format(l=info.label))
            parts.append(f"{info.width / 72 * 25.4:.0f} × {info.height / 72 * 25.4:.0f} mm")
            if info.rotation:
                parts.append(_("Ruotata di {d}°").format(d=info.rotation))
            if info.annots:
                parts.append(_("Contiene annotazioni"))
            return "\n".join(parts)
        return None

    def flags(self, index: QModelIndex):
        base = super().flags(index)
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        return base | Qt.ItemFlag.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, grid: "PageGrid"):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index) -> QSize:
        s = self.grid.thumb_size
        return QSize(s + CELL_PAD * 2, s + CELL_PAD + LABEL_HEIGHT)

    def page_box(self, cell: QRect, info: PageInfo) -> QRectF:
        s = self.grid.thumb_size
        scale = s / max(info.width, info.height, 1)
        pw, ph = max(8, info.width * scale), max(8, info.height * scale)
        return QRectF(cell.x() + (cell.width() - pw) / 2, cell.y() + CELL_PAD + (s - ph) / 2, pw, ph)

    def quick_buttons(self, cell: QRect, info: PageInfo) -> list[tuple[QRectF, int, str]]:
        """Pulsanti rapidi (rettangolo, gradi, icona) mostrati al passaggio del puntatore."""
        box = self.page_box(cell, info)
        size = 26.0
        y = box.bottom() - size - 6
        cx = box.center().x()
        return [(QRectF(cx - size - 3, y, size, size), -90, "rotate-ccw"),
                (QRectF(cx + 3, y, size, size), 90, "rotate-cw")]

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p.save()
        try:
            self._paint(p, option, index)
        except Exception:   # pagina danneggiata: segnaposto, mai un'eccezione dal disegno
            p.restore()
            p.save()
            self._paint_unreadable(p, option, index)
        finally:
            p.restore()

    def _paint_unreadable(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        cell = option.rect
        s = self.grid.thumb_size
        box = QRectF(cell.x() + (cell.width() - s * 0.7) / 2, cell.y() + CELL_PAD, s * 0.7, s)
        p.setPen(QPen(theme.color("border"), 1))
        p.setBrush(theme.color("surface"))
        p.drawRect(box)
        p.setPen(theme.color("danger"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, _("Pagina non leggibile"))
        p.setPen(theme.color("text"))
        p.drawText(QRectF(cell.x(), cell.y() + CELL_PAD + s + 2, cell.width(), LABEL_HEIGHT - 6),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, str(index.row() + 1))

    def _paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        grid = self.grid
        row = index.row()
        info = grid.model().infos[row]
        cell = option.rect
        s = grid.thumb_size
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # area della pagina in proporzione reale
        w, h = info.width, info.height
        scale = s / max(w, h, 1)
        pw, ph = max(8, w * scale), max(8, h * scale)
        box = QRectF(cell.x() + (cell.width() - pw) / 2, cell.y() + CELL_PAD + (s - ph) / 2, pw, ph)
        if selected or hover:
            bg = QColor(theme.color("accent_soft"))
            if not selected:
                bg.setAlpha(110)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(cell).adjusted(3, 3, -3, -3), 8, 8)
        shadow = theme.color("page_shadow")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawRoundedRect(box.translated(0, 2).adjusted(-1, -1, 1, 1), 2, 2)
        img = grid.thumbnail(row)
        if img is not None:
            p.drawImage(box, img)
        elif grid.failed(row):
            p.setBrush(theme.color("surface"))
            p.drawRect(box)
            ic = icon("file-warning", theme.tokens()["danger"]).pixmap(28, 28)
            p.drawPixmap(int(box.center().x() - 14), int(box.center().y() - 26), ic)
            p.setPen(theme.color("danger"))
            p.drawText(box.adjusted(4, box.height() / 2, -4, 0), Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                       _("Pagina non leggibile"))
        else:
            p.setBrush(QColor("#FFFFFF") if not theme.is_dark() else QColor("#E9ECEF"))
            p.drawRect(box)
        pen = QPen(theme.color("accent") if selected else theme.color("border"), 2.5 if selected else 1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        # indicatori
        badges = []
        if info.rotation:
            badges.append(("rotate-cw", f"{info.rotation}°"))
        if info.odd_size:
            badges.append(("ruler", ""))
        if info.annots:
            badges.append(("message-square", ""))
        bx = box.right() - 4
        for name, text in badges:
            bw = 20 + (p.fontMetrics().horizontalAdvance(text) + 4 if text else 0)
            r = QRectF(bx - bw, box.top() + 4, bw, 20)
            p.setPen(Qt.PenStyle.NoPen)
            col = QColor(theme.color("surface"))
            col.setAlpha(235)
            p.setBrush(col)
            p.drawRoundedRect(r, 5, 5)
            p.drawPixmap(int(r.x() + 2), int(r.y() + 2), icon(name, theme.tokens()["accent"]).pixmap(16, 16))
            if text:
                p.setPen(theme.color("text"))
                f = QFont(p.font())
                f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
                p.setFont(f)
                p.drawText(r.adjusted(20, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, text)
            bx = r.left() - 3
        if hover and grid.quick_buttons_enabled:
            for rect, _deg, name in self.quick_buttons(cell, info):
                p.setPen(QPen(theme.color("border"), 1))
                p.setBrush(theme.color("surface"))
                p.drawEllipse(rect)
                p.drawPixmap(int(rect.x() + 5), int(rect.y() + 5), icon(name, theme.tokens()["accent"]).pixmap(16, 16))
        # numero ed etichetta
        text_rect = QRectF(cell.x() + 4, cell.y() + CELL_PAD + s + 2, cell.width() - 8, LABEL_HEIGHT - 6)
        num = str(row + 1)
        f = QFont(option.font)
        f.setBold(selected)
        p.setFont(f)
        if selected:
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(num) + 16
            pill = QRectF(text_rect.center().x() - tw / 2, text_rect.top() + 1, tw, fm.height() + 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            p.setPen(theme.color("accent_text"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, num)
        else:
            p.setPen(theme.color("text"))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, num)
        if info.label and info.label != num:
            f2 = QFont(option.font)
            f2.setPointSizeF(max(7.0, f2.pointSizeF() - 1.5))
            p.setFont(f2)
            p.setPen(theme.color("text_muted"))
            fm2 = p.fontMetrics()
            label_rect = QRectF(text_rect.x(), text_rect.bottom() - fm2.height(), text_rect.width(), fm2.height())
            p.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       fm2.elidedText(f"({info.label})", Qt.TextElideMode.ElideRight, int(text_rect.width())))


class PageGrid(QListView):
    """Griglia di miniature con selezione multipla e trascinamento."""

    # (indici, posizione) spostamento interno; (mime, posizione, copia) rilascio esterno
    moveRequested = Signal(list, int)
    dropRequested = Signal(object, int, bool)
    selectionCountChanged = Signal(int)
    contextRequested = Signal(object)   # QPoint globale
    zoomStep = Signal(int)
    rotateRequested = Signal(int, int)   # pagina, gradi (pulsanti rapidi)
    insertRequested = Signal(int)        # pulsante «+» tra due miniature: posizione

    RENDER_MARGIN_ROWS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document = None
        self.thumb_size = DEFAULT_THUMB
        self._model = PagesModel(self)
        self.setModel(self._model)
        self.setItemDelegate(ThumbDelegate(self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.contextRequested.emit(self.viewport().mapToGlobal(pos)))
        self._cache: OrderedDict[int, QImage] = OrderedDict()
        self._failed: set[int] = set()
        self._queue: list[int] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_step)
        self._drop_index: int | None = None
        self.drag_payload: Callable[[list[int]], QMimeData | None] | None = None
        self.quick_buttons_enabled = True
        self._plus: tuple[int, QPoint] | None = None
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.schedule_render())
        self._apply_grid()

    # -------------------------------------------------------------- dati
    def set_document(self, document) -> None:
        self.document = document
        self.reload()

    def reload(self, keep_selection: list[int] | None = None) -> None:
        if self.document is None or self.document.closed:
            self._model.set_infos([])
            self._clear_cache()
            return
        with ENGINE_LOCK:
            infos = read_page_infos(self.document.pdf)
        top = self.verticalScrollBar().value()
        current = self.currentIndex().row()
        self._model.set_infos(infos)
        self._clear_cache()
        self.verticalScrollBar().setValue(top)
        if keep_selection is not None:
            self.select_pages(keep_selection)
        elif 0 <= current < len(infos):
            self.setCurrentIndex(self._model.index(current))
        self.schedule_render()
        self.selectionCountChanged.emit(len(self.selected_pages()))

    def page_count(self) -> int:
        return self._model.rowCount()

    # -------------------------------------------------------------- selezione
    def selected_pages(self) -> list[int]:
        return sorted(i.row() for i in self.selectionModel().selectedIndexes())

    def select_pages(self, pages: list[int], current: int | None = None) -> None:
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        sel = QItemSelection()
        n = self.page_count()
        for p in sorted(set(pages)):
            if 0 <= p < n:
                idx = self._model.index(p)
                sel.select(idx, idx)
        sm = self.selectionModel()
        sm.select(sel, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        valid = [p for p in pages if 0 <= p < n]
        if valid:
            cur = current if current is not None else valid[0]
            sm.setCurrentIndex(self._model.index(cur), QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollTo(self._model.index(cur))
        self.selectionCountChanged.emit(len(self.selected_pages()))

    def selectionChanged(self, selected, deselected) -> None:
        super().selectionChanged(selected, deselected)
        self.selectionCountChanged.emit(len(self.selectionModel().selectedIndexes()))

    # -------------------------------------------------------------- miniature
    def set_thumb_size(self, size: int) -> None:
        size = max(MIN_THUMB, min(MAX_THUMB, int(size)))
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self._apply_grid()
        self._clear_cache()
        self.schedule_render()

    def _apply_grid(self) -> None:
        s = self.thumb_size
        self.setGridSize(QSize(s + CELL_PAD * 2 + 6, s + CELL_PAD + LABEL_HEIGHT + 6))
        self.doItemsLayout()

    def thumbnail(self, row: int) -> QImage | None:
        img = self._cache.get(row)
        if img is None and row not in self._failed:
            if row not in self._queue:
                self._queue.append(row)
            if not self._timer.isActive():
                self._timer.start(0)
        return img

    def failed(self, row: int) -> bool:
        return row in self._failed

    def rendered_count(self) -> int:
        return len(self._cache)

    def _clear_cache(self) -> None:
        self._cache.clear()
        self._failed.clear()
        self._queue.clear()

    def visible_rows(self, margin_rows: int = 0) -> list[int]:
        n = self.page_count()
        if n == 0:
            return []
        vp = self.viewport().rect()
        grid = self.gridSize()
        extra = margin_rows * grid.height()
        area = vp.adjusted(0, -extra, 0, extra)
        first = self.indexAt(QPoint(grid.width() // 2, max(0, area.top()) + 2))
        rows = []
        start = first.row() if first.isValid() else 0
        # scorre dalla prima candidata finché le celle restano nell'area
        if not first.isValid():
            # cerca la prima riga visibile
            for r in range(n):
                if self.visualRect(self._model.index(r)).bottom() >= area.top():
                    start = r
                    break
        cols = max(1, vp.width() // max(1, grid.width()))
        start = max(0, start - cols)
        for r in range(start, n):
            rect = self.visualRect(self._model.index(r))
            if rect.top() > area.bottom():
                break
            if rect.bottom() >= area.top():
                rows.append(r)
        return rows

    def schedule_render(self) -> None:
        keep = set(self.visible_rows(self.RENDER_MARGIN_ROWS))
        # memoria limitata: solo visibili + margine
        for r in [r for r in self._cache if r not in keep]:
            del self._cache[r]
        self._queue = [r for r in self.visible_rows(self.RENDER_MARGIN_ROWS) if r not in self._cache and r not in self._failed]
        if self._queue and not self._timer.isActive():
            self._timer.start(0)

    def _render_one(self, row: int) -> None:
        doc = self.document
        if doc is None or doc.closed or row >= self.page_count():
            return
        info = self._model.infos[row]
        dpr = self.devicePixelRatioF() or 1.0
        zoom = self.thumb_size * dpr / max(info.width, info.height, 1)
        try:
            page = doc.pdf[row]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            self._cache[row] = img
        except Exception:
            self._failed.add(row)

    def _render_step(self) -> None:
        if not self._queue:
            return
        keep = set(self.visible_rows(self.RENDER_MARGIN_ROWS))
        with try_engine() as ok:
            if not ok:
                self._timer.start(40)
                return
            done = 0
            while self._queue and done < 4:
                row = self._queue.pop(0)
                if row in keep and row not in self._cache:
                    self._render_one(row)
                    done += 1
        self.viewport().update()
        if self._queue:
            self._timer.start(0)

    def flush_rendering(self) -> None:
        """Disegna subito le miniature visibili (test e catture)."""
        self.schedule_render()
        with ENGINE_LOCK:
            for row in list(self._queue):
                if row not in self._cache:
                    self._render_one(row)
        self._queue.clear()
        self._timer.stop()
        self.viewport().update()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.schedule_render()

    def quick_button_at(self, pos: QPoint) -> tuple[int, int] | None:
        """(pagina, gradi) se `pos` cade su un pulsante rapido di rotazione."""
        if not self.quick_buttons_enabled:
            return None
        idx = self.indexAt(pos)
        if not idx.isValid():
            return None
        info = self._model.infos[idx.row()]
        for rect, deg, _n in self.itemDelegate().quick_buttons(self.visualRect(idx), info):
            if rect.contains(pos.x(), pos.y()):
                return idx.row(), deg
        return None

    PLUS_RADIUS = 11

    def plus_points(self) -> list[tuple[int, QPoint]]:
        """Punti dei pulsanti «+» (posizione di inserimento, centro) tra miniature vicine."""
        out = []
        rows = self.visible_rows()
        delegate = self.itemDelegate()
        for r in rows:
            rect = self.visualRect(self._model.index(r))
            box = delegate.page_box(rect, self._model.infos[r])
            if r + 1 < self.page_count():
                nrect = self.visualRect(self._model.index(r + 1))
                if nrect.top() == rect.top():
                    nbox = delegate.page_box(nrect, self._model.infos[r + 1])
                    out.append((r + 1, QPoint(int((box.right() + nbox.left()) / 2), int(box.center().y()))))
                    continue
            out.append((r + 1, QPoint(int(rect.right() + 3), int(box.center().y()))))
        return out

    def plus_at(self, pos: QPoint) -> int | None:
        for index, pt in self.plus_points():
            if (pt - pos).manhattanLength() <= self.PLUS_RADIUS + 3:
                return index
        return None

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position().toPoint()
        near = None
        if not ev.buttons():
            for index, pt in self.plus_points():
                if (pt - pos).manhattanLength() <= 40:
                    near = (index, pt)
                    break
        if near != self._plus:
            self._plus = near
            self.viewport().update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._plus = None
        self.viewport().update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.quick_buttons_enabled:
            at = self.plus_at(ev.position().toPoint())
            if at is not None:
                ev.accept()
                self.insertRequested.emit(at)
                return
        if ev.button() == Qt.MouseButton.LeftButton:
            hit = self.quick_button_at(ev.position().toPoint())
            if hit is not None:
                ev.accept()
                self.rotateRequested.emit(*hit)
                return
        super().mousePressEvent(ev)

    def wheelEvent(self, ev) -> None:
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomStep.emit(20 if ev.angleDelta().y() > 0 else -20)
            ev.accept()
            return
        super().wheelEvent(ev)

    # -------------------------------------------------------------- tastiera
    keyCommands: dict = {}

    def keyPressEvent(self, ev) -> None:
        if ev.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            return
        for key, action in self.keyCommands.items():
            if ev.matches(key):
                if action.isEnabled():
                    action.trigger()
                ev.accept()
                return
        super().keyPressEvent(ev)

    # -------------------------------------------------------------- trascinamento
    def drop_index_at(self, pos: QPoint) -> int:
        """Posizione di inserimento (prima della pagina restituita) per un punto del viewport."""
        n = self.page_count()
        idx = self.indexAt(pos)
        if idx.isValid():
            rect = self.visualRect(idx)
            return idx.row() + (1 if pos.x() > rect.center().x() else 0)
        best, best_d = n, None
        for r in self.visible_rows():
            rect = self.visualRect(self._model.index(r))
            if rect.top() <= pos.y() <= rect.bottom() or best_d is None:
                d = abs(rect.center().y() - pos.y()) * 4 + abs(rect.center().x() - pos.x())
                if best_d is None or d < best_d:
                    best_d = d
                    best = r + (1 if pos.x() > rect.center().x() else 0)
        if n and pos.y() > self.visualRect(self._model.index(n - 1)).bottom():
            return n
        return best

    def startDrag(self, actions) -> None:
        pages = self.selected_pages()
        if not pages:
            return
        mime = self.drag_payload(pages) if self.drag_payload else None
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        img = self.thumbnail(pages[0])
        if img is not None:
            pm = QPixmap.fromImage(img).scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            if len(pages) > 1:
                p = QPainter(pm)
                p.setBrush(theme.color("accent"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRect(pm.width() - 24, 0, 24, 24))
                p.setPen(theme.color("accent_text"))
                p.drawText(QRect(pm.width() - 24, 0, 24, 24), Qt.AlignmentFlag.AlignCenter, str(len(pages)))
                p.end()
            drag.setPixmap(pm)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        self._drop_index = None
        self.viewport().update()

    def _accepts(self, mime: QMimeData) -> bool:
        return mime.hasFormat(PAGES_MIME) or mime.hasUrls()

    def dragEnterEvent(self, ev) -> None:
        if self._accepts(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:
        if not self._accepts(ev.mimeData()):
            ev.ignore()
            return
        super().dragMoveEvent(ev)   # scorrimento automatico vicino ai bordi
        self._drop_index = self.drop_index_at(ev.position().toPoint())
        internal = ev.source() is self
        copy = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier) or (not internal and not (
            ev.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        ev.setDropAction(Qt.DropAction.CopyAction if copy else Qt.DropAction.MoveAction)
        ev.accept()
        self.viewport().update()

    def dragLeaveEvent(self, ev) -> None:
        self._drop_index = None
        self.viewport().update()
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev) -> None:
        index = self.drop_index_at(ev.position().toPoint())
        self._drop_index = None
        self.viewport().update()
        mime = ev.mimeData()
        if ev.source() is self and mime.hasFormat(PAGES_MIME) and not (
                ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            pages = self.selected_pages()
            ev.setDropAction(Qt.DropAction.CopyAction)   # niente cancellazioni automatiche di Qt
            ev.accept()
            self.moveRequested.emit(pages, index)
            return
        copy = not bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ev.setDropAction(Qt.DropAction.CopyAction)
        ev.accept()
        self.dropRequested.emit(mime, index, copy)

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        if self._plus is not None and self._drop_index is None:
            _i, pt = self._plus
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            r = self.PLUS_RADIUS
            p.drawEllipse(pt, r, r)
            p.setPen(QPen(theme.color("accent_text"), 2))
            p.drawLine(pt.x() - 5, pt.y(), pt.x() + 5, pt.y())
            p.drawLine(pt.x(), pt.y() - 5, pt.x(), pt.y() + 5)
            p.end()
        if self._drop_index is None:
            return
        n = self.page_count()
        p = QPainter(self.viewport())
        pen = QPen(theme.color("accent"), 3)
        p.setPen(pen)
        if n == 0:
            p.end()
            return
        if self._drop_index < n:
            rect = self.visualRect(self._model.index(self._drop_index))
            x = rect.left() - 2
        else:
            rect = self.visualRect(self._model.index(n - 1))
            x = rect.right() + 2
        p.drawLine(x, rect.top() + 8, x, rect.bottom() - 8)
        p.setBrush(theme.color("accent"))
        p.drawEllipse(QPoint(x, rect.top() + 8), 4, 4)
        p.end()


class Organizer(QWidget):
    """Widget centrale della modalità Pagine: griglia + barra inferiore."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pagesOrganizer")
        self.view: "DocumentView | None" = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.grid = PageGrid(self)
        lay.addWidget(self.grid, 1)
        bar = QWidget(self)
        bar.setObjectName("pagesFooter")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(12, 4, 12, 4)
        self.info_label = QLabel(bar)
        hl.addWidget(self.info_label)
        hl.addStretch(1)
        self.zoom_out = QToolButton(bar)
        self.zoom_out.setAutoRaise(True)
        self.zoom_out.setIcon(icon("zoom-out"))
        self.zoom_out.setToolTip(_("Miniature più piccole"))
        hl.addWidget(self.zoom_out)
        self.slider = QSlider(Qt.Orientation.Horizontal, bar)
        self.slider.setRange(MIN_THUMB, MAX_THUMB)
        self.slider.setValue(DEFAULT_THUMB)
        self.slider.setFixedWidth(160)
        self.slider.setToolTip(_("Dimensione delle miniature"))
        hl.addWidget(self.slider)
        self.zoom_in = QToolButton(bar)
        self.zoom_in.setAutoRaise(True)
        self.zoom_in.setIcon(icon("zoom-in"))
        self.zoom_in.setToolTip(_("Miniature più grandi"))
        hl.addWidget(self.zoom_in)
        lay.addWidget(bar)
        self.slider.valueChanged.connect(self.grid.set_thumb_size)
        self.zoom_in.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 40))
        self.zoom_out.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 40))
        self.grid.selectionCountChanged.connect(self._update_info)
        self._apply_style()
        theme.on_change(self._on_theme)
        self.grid.zoomStep.connect(lambda step: self.slider.setValue(self.slider.value() + step))

    def _on_theme(self) -> None:
        try:
            self._apply_style()
            self.zoom_in.setIcon(icon("zoom-in"))
            self.zoom_out.setIcon(icon("zoom-out"))
        except RuntimeError:   # widget già distrutto
            pass

    def _apply_style(self) -> None:
        t = theme.tokens()
        self.grid.setStyleSheet(f"QListView {{ background: {t['canvas']}; border: none; }}")
        self.findChild(QWidget, "pagesFooter").setStyleSheet(
            f"#pagesFooter {{ background: {t['surface']}; border-top: 1px solid {t['border']}; }}")
        self.grid.viewport().update()

    def set_view(self, view) -> None:
        self.view = view
        self.grid.set_document(view.document if view is not None else None)
        if view is not None and self.grid.page_count():
            self.grid.select_pages([min(view.current_page, self.grid.page_count() - 1)])
        self._update_info()

    def selected_pages(self) -> list[int]:
        return self.grid.selected_pages()

    def status_text(self) -> str:
        n = len(self.grid.selected_pages())
        total = self.grid.page_count()
        if n == 0:
            return _("{t} pagine").format(t=total)
        if n == 1:
            return _("1 pagina selezionata su {t}").format(t=total)
        return _("{n} pagine selezionate").format(n=n)

    def _update_info(self, *_a) -> None:
        self.info_label.setText(self.status_text())


