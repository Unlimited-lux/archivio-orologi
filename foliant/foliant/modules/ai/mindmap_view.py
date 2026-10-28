"""Mappa concettuale e scaletta (AI-12): widget interattivo.

`MindMapView` mostra l'albero come mappa (riquadri collegati, zoom, spostamento,
espandi/comprimi) o come scaletta modificabile, e permette di aggiungere,
eliminare, rinominare e spostare i nodi. Il clic su un nodo con pagina emette
`pageRequested(indice a base 0)`; ogni modifica emette `changed`.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from foliant.i18n import _
from foliant.ui import theme
from foliant.ui.icons import bind_icon

from . import mindmap as mm

ID_ROLE = Qt.ItemDataRole.UserRole + 1
TOGGLE = 12.0


class _NodeItem(QGraphicsRectItem):
    def __init__(self, view: "MindMapView", node: mm.Node, rect: QRectF, is_root: bool, selected: bool,
                 has_children: bool, collapsed: bool):
        super().__init__(rect)
        self._view = view
        self.node_id = node.id
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if node.page is not None else Qt.CursorShape.ArrowCursor)
        tip = node.title
        if node.page is not None:
            tip += "\n" + _("Pagina {n}").format(n=node.page + 1)
        if node.note:
            tip += "\n\n" + node.note
        self.setToolTip(tip)
        accent = theme.color("accent")
        self.setPen(QPen(accent, 2.2 if selected else 1.0))
        self.setBrush(QBrush(accent if is_root else theme.color("surface")))
        text = QGraphicsSimpleTextItem(mm.short_label(node.title), self)
        text.setFont(_node_font(is_root))
        text.setBrush(QBrush(theme.color("accent_text") if is_root else theme.color("text")))
        br = text.boundingRect()
        room = rect.width() - (TOGGLE / 2 if has_children else 0)
        text.setPos(rect.x() + (room - br.width()) / 2, rect.y() + (rect.height() - br.height()) / 2)
        self.toggle_rect = None
        if has_children:
            self.toggle_rect = QRectF(rect.right() - TOGGLE / 2, rect.center().y() - TOGGLE / 2, TOGGLE, TOGGLE)
            t = _ToggleItem(self.toggle_rect, collapsed, self)
            t.setToolTip(_("Espandi") if collapsed else _("Comprimi"))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if self.toggle_rect is not None and self.toggle_rect.contains(ev.pos()):
                self._view.toggle_collapsed(self.node_id)
            else:
                self._view.click_node(self.node_id)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self._view.toggle_collapsed(self.node_id)
        ev.accept()


def _node_font(bold: bool) -> QFont:
    font = QFont()
    font.setPointSizeF(9.5)
    font.setBold(bold)
    return font


class _ToggleItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, collapsed: bool, parent):
        super().__init__(rect, parent)
        self._collapsed = collapsed
        self.setPen(QPen(theme.color("accent"), 1.0))
        self.setBrush(QBrush(theme.color("surface")))
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def paint(self, painter: QPainter, option, widget=None):
        r = self.rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawEllipse(r)
        c = r.center()
        d = r.width() * 0.28
        painter.drawLine(QPointF(c.x() - d, c.y()), QPointF(c.x() + d, c.y()))
        if self._collapsed:
            painter.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x(), c.y() + d))


class _MapView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
            ev.accept()
            return
        super().wheelEvent(ev)

    def zoom(self, factor: float) -> None:
        current = self.transform().m11()
        target = max(0.2, min(4.0, current * factor))
        self.scale(target / current, target / current)


class _OutlineTree(QTreeWidget):
    moved = Signal()

    def dropEvent(self, ev):
        super().dropEvent(ev)
        self.moved.emit()


class MindMapView(QWidget):
    pageRequested = Signal(int)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = mm.Node(id="n0", title=_("Documento"))
        self._collapsed: set[str] = set()
        self._selected: str | None = None
        self._filling = False

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 4)
        bar.setSpacing(2)
        self.btn_map = self._button("network", _("Mappa"), checkable=True)
        self.btn_outline = self._button("list-tree", _("Scaletta"), checkable=True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.btn_map)
        group.addButton(self.btn_outline)
        self.btn_map.setChecked(True)
        self.btn_add = self._button("plus", _("Aggiungi nodo figlio"))
        self.btn_rename = self._button("pencil", _("Rinomina nodo"))
        self.btn_delete = self._button("trash-2", _("Elimina nodo"))
        self.btn_zoom_in = self._button("zoom-in", _("Ingrandisci"))
        self.btn_zoom_out = self._button("zoom-out", _("Riduci"))
        for b in (self.btn_map, self.btn_outline):
            bar.addWidget(b)
        bar.addSpacing(8)
        for b in (self.btn_add, self.btn_rename, self.btn_delete):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_zoom_out)
        bar.addWidget(self.btn_zoom_in)

        self.scene = QGraphicsScene(self)
        self.map_view = _MapView()
        self.map_view.setScene(self.scene)

        self.outline = _OutlineTree()
        self.outline.setColumnCount(2)
        self.outline.setHeaderLabels([_("Titolo"), _("Pagina")])
        self.outline.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.outline.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed
                                     | QAbstractItemView.EditTrigger.SelectedClicked)
        self.outline.itemChanged.connect(self._on_item_changed)
        self.outline.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.outline.currentItemChanged.connect(self._on_current_item)
        self.outline.moved.connect(self._on_outline_moved)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.map_view)
        self.stack.addWidget(self.outline)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.stack, 1)

        self.btn_map.toggled.connect(lambda on: on and self.show_map())
        self.btn_outline.toggled.connect(lambda on: on and self.show_outline())
        self.btn_add.clicked.connect(self.add_child_to_selected)
        self.btn_rename.clicked.connect(self.rename_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_zoom_in.clicked.connect(lambda: self.map_view.zoom(1.25))
        self.btn_zoom_out.clicked.connect(lambda: self.map_view.zoom(0.8))
        ref = weakref.ref(self)
        theme.on_change(lambda: (w := ref()) is not None and w._rebuild())
        self._rebuild()

    # ------------------------------------------------------------------ API

    def set_tree(self, root: mm.Node) -> None:
        self._root = root
        self._collapsed.clear()
        self._selected = None
        self._rebuild()

    def tree(self) -> mm.Node:
        return self._root

    def selected_id(self) -> str | None:
        return self._selected

    def select(self, node_id: str | None) -> None:
        self._selected = node_id if node_id and self._root.find(node_id) else None
        self._rebuild()

    def click_node(self, node_id: str) -> None:
        """Seleziona il nodo e, se ha una pagina, ne chiede l'apertura."""
        node = self._root.find(node_id)
        if node is None:
            return
        self.select(node_id)
        if node.page is not None:
            self.pageRequested.emit(node.page)

    def toggle_collapsed(self, node_id: str) -> None:
        node = self._root.find(node_id)
        if node is None or not node.children:
            return
        if node_id in self._collapsed:
            self._collapsed.discard(node_id)
        else:
            self._collapsed.add(node_id)
        self._rebuild()

    def is_collapsed(self, node_id: str) -> bool:
        return node_id in self._collapsed

    def show_map(self) -> None:
        self.btn_map.setChecked(True)
        self.stack.setCurrentWidget(self.map_view)

    def show_outline(self) -> None:
        self.btn_outline.setChecked(True)
        self.stack.setCurrentWidget(self.outline)

    def node_item(self, node_id: str):
        for item in self.scene.items():
            if isinstance(item, _NodeItem) and item.node_id == node_id:
                return item
        return None

    # ------------------------------------------------------------------ modifiche

    def add_child_to_selected(self, title: str | None = None) -> mm.Node:
        parent_id = self._selected or self._root.id
        node = mm.add_child(self._root, parent_id, title if isinstance(title, str) and title else _("Nuovo nodo"))
        self._collapsed.discard(parent_id)
        self._selected = node.id
        self._rebuild()
        self.changed.emit()
        if self.stack.currentWidget() is self.outline:
            item = self._outline_item(node.id)
            if item is not None:
                self.outline.editItem(item, 0)
        return node

    def rename_node(self, node_id: str, title: str) -> bool:
        try:
            mm.rename(self._root, node_id, title)
        except mm.MindMapError:
            return False
        self._rebuild()
        self.changed.emit()
        return True

    def rename_selected(self) -> None:
        node = self._root.find(self._selected or self._root.id)
        if node is None:
            return
        if self.stack.currentWidget() is self.outline:
            item = self._outline_item(node.id)
            if item is not None:
                self.outline.editItem(item, 0)
                return
        text, ok = QInputDialog.getText(self, _("Rinomina nodo"), _("Titolo:"), text=node.title)
        if ok:
            self.rename_node(node.id, text)

    def delete_selected(self) -> None:
        if not self._selected or self._selected == self._root.id:
            return
        self.delete_node(self._selected)

    def delete_node(self, node_id: str) -> None:
        parent = self._root.parent_of(node_id)
        mm.delete(self._root, node_id)
        self._selected = parent.id if parent is not None else None
        self._rebuild()
        self.changed.emit()

    def move_node(self, node_id: str, new_parent_id: str, index: int) -> None:
        mm.move(self._root, node_id, new_parent_id, index)
        self._rebuild()
        self.changed.emit()

    # ------------------------------------------------------------------ disegno

    def _button(self, icon_name: str, tip: str, checkable: bool = False) -> QToolButton:
        b = QToolButton(self)
        bind_icon(b, icon_name)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setAutoRaise(True)
        b.setCheckable(checkable)
        return b

    def _rebuild(self) -> None:
        try:
            self._build_scene()
            self._build_outline()
        except RuntimeError:  # widget già distrutto (cambio tema tardivo)
            return
        can_edit = self._selected is not None and self._selected != self._root.id
        self.btn_delete.setEnabled(can_edit)

    def _build_scene(self) -> None:
        self.scene.clear()
        self.map_view.setBackgroundBrush(QBrush(theme.color("surface")))
        metrics = {False: QFontMetricsF(_node_font(False)), True: QFontMetricsF(_node_font(True))}
        root = self._root

        def measure(node: mm.Node, label: str) -> float:
            extra = TOGGLE / 2 if node.children else 0.0
            return metrics[node is root].horizontalAdvance(label) + extra

        boxes = mm.layout(self._root, collapsed=self._collapsed, measure=measure)
        pen = QPen(theme.color("border"), 1.4)
        for a, b in mm.edges(self._root, self._collapsed):
            ax, ay, aw, ah = boxes[a]
            bx, by, _bw, bh = boxes[b]
            p1 = QPointF(ax + aw + TOGGLE / 2, ay + ah / 2)
            p2 = QPointF(bx, by + bh / 2)
            mx = (p1.x() + p2.x()) / 2
            path = QPainterPath(p1)
            path.cubicTo(QPointF(mx, p1.y()), QPointF(mx, p2.y()), p2)
            edge = QGraphicsPathItem(path)
            edge.setPen(pen)
            edge.setZValue(-1)
            self.scene.addItem(edge)
        visible = set(boxes)
        for node in self._root.walk():
            if node.id not in visible:
                continue
            x, y, w, h = boxes[node.id]
            item = _NodeItem(self, node, QRectF(x, y, w, h), node is self._root, node.id == self._selected,
                             bool(node.children), node.id in self._collapsed)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.scene.addItem(item)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))

    def _build_outline(self) -> None:
        self._filling = True
        try:
            self.outline.clear()

            def add(node: mm.Node, parent) -> QTreeWidgetItem:
                item = QTreeWidgetItem(parent)
                item.setText(0, node.title)
                item.setText(1, "" if node.page is None else str(node.page + 1))
                item.setToolTip(0, node.title + (f"\n\n{node.note}" if node.note else ""))
                item.setData(0, ID_ROLE, node.id)
                flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                         | Qt.ItemFlag.ItemIsDropEnabled)
                if node is not self._root:
                    flags |= Qt.ItemFlag.ItemIsDragEnabled
                item.setFlags(flags)
                for c in node.children:
                    add(c, item)
                item.setExpanded(node.id not in self._collapsed)
                if node.id == self._selected:
                    self.outline.setCurrentItem(item)
                return item

            add(self._root, self.outline)
            self.outline.resizeColumnToContents(1)
        finally:
            self._filling = False

    def _outline_item(self, node_id: str) -> QTreeWidgetItem | None:
        stack = [self.outline.topLevelItem(i) for i in range(self.outline.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, ID_ROLE) == node_id:
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
        return None

    # ------------------------------------------------------------------ scaletta

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        node_id = item.data(0, ID_ROLE)
        node = self._root.find(node_id)
        if node is None or item.text(0) == node.title:
            return
        if not self.rename_node(node_id, item.text(0)):
            self._rebuild()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        node = self._root.find(item.data(0, ID_ROLE))
        if node is not None and node.page is not None:
            self.pageRequested.emit(node.page)

    def _on_current_item(self, current, _previous) -> None:
        if self._filling or current is None:
            return
        self._selected = current.data(0, ID_ROLE)
        self.btn_delete.setEnabled(self._selected != self._root.id)

    def _on_outline_moved(self) -> None:
        """Ricostruisce la gerarchia dopo un trascinamento nella scaletta."""
        if self.outline.topLevelItemCount() != 1 or self.outline.topLevelItem(0).data(0, ID_ROLE) != self._root.id:
            self._rebuild()  # la radice deve restare unica: annulla lo spostamento
            return
        by_id = {n.id: n for n in self._root.walk()}

        def rebuild(item: QTreeWidgetItem) -> None:
            node = by_id[item.data(0, ID_ROLE)]
            node.children = [by_id[item.child(i).data(0, ID_ROLE)] for i in range(item.childCount())]
            for i in range(item.childCount()):
                rebuild(item.child(i))

        rebuild(self.outline.topLevelItem(0))
        self._rebuild()
        self.changed.emit()
