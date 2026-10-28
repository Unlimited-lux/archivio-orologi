"""Widget del modulo: pannello Redazioni, strumento «Segna area», barra di avviso, anteprima."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import pymupdf
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, Tool, _, icon, theme

from . import redaction as R

if TYPE_CHECKING:
    from foliant.api import DocumentView


def source_label(source: str) -> str:
    if source.startswith("schema:"):
        return source.split(":", 1)[1] or _("Schema")
    if source == "schema":
        return _("Schema")
    return {
        "area": _("Area"),
        "selezione": _("Testo selezionato"),
        "ricerca": _("Ricerca"),
        R.WHOLE_PAGE: _("Pagina intera"),
    }.get(source, source or _("Area"))


# ------------------------------------------------------------- pannello


class RedactionPanel(QWidget):
    """Elenco dei segni di redazione per pagina, con revisione e applicazione."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.view: DocumentView | None = None
        self._doc = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        head = QHBoxLayout()
        self.title = QLabel(_("Redazioni"))
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        head.addWidget(self.title, 1)
        self.count = QLabel("0")
        head.addWidget(self.count)
        lay.addLayout(head)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Segno"), _("Origine"), _("Stato")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemClicked.connect(self._go)
        lay.addWidget(self.tree, 1)
        self.empty = QLabel(_("Nessun segno di redazione. Usa «Segna area», «Segna testo», «Trova e segna» "
                              "o «Trova dati personali» per indicare cosa oscurare."))
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.empty, 1)
        row = QHBoxLayout()
        self.remove_button = QPushButton(icon("trash-2"), _("Rimuovi"))
        self.remove_button.clicked.connect(self._remove)
        self.preview_button = QPushButton(icon("eye"), _("Anteprima risultato"))
        self.preview_button.setCheckable(True)
        self.preview_button.toggled.connect(lambda on: self.controller.set_preview(on))
        row.addWidget(self.remove_button)
        row.addWidget(self.preview_button)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.apply_selected_button = QPushButton(_("Applica selezionati"))
        self.apply_selected_button.clicked.connect(lambda: self.controller.apply_redactions(self.selected()))
        self.apply_all_button = QPushButton(icon("check"), _("Applica tutti"))
        self.apply_all_button.clicked.connect(lambda: self.controller.apply_redactions(None))
        row2.addWidget(self.apply_selected_button)
        row2.addWidget(self.apply_all_button)
        lay.addLayout(row2)
        row3 = QHBoxLayout()
        self.export_button = QPushButton(icon("download"), _("Esporta XFDF"))
        self.export_button.clicked.connect(lambda: self.controller.export_marks())
        self.import_button = QPushButton(icon("upload"), _("Importa XFDF"))
        self.import_button.clicked.connect(lambda: self.controller.import_marks())
        row3.addWidget(self.export_button)
        row3.addWidget(self.import_button)
        lay.addLayout(row3)
        from PySide6.QtGui import QKeySequence, QShortcut
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.tree)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._remove)
        self.marks: list[R.Mark] = []
        self.refresh()

    def set_view(self, view) -> None:
        if self._doc is not None:
            self._doc.remove_listener(self._on_doc)
        self.view = view
        self._doc = view.document if view is not None else None
        if self._doc is not None:
            self._doc.add_listener(self._on_doc)
        self.refresh()

    def _on_doc(self, _doc, kind: str) -> None:
        if kind == "content":
            if self.isVisible():
                self.refresh()
            else:
                self._stale = True   # aggiornato quando il pannello viene mostrato

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_stale", False):
            self._stale = False
            self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        self.marks = []
        doc = self._doc
        if doc is not None and not doc.closed:
            with ENGINE_LOCK:
                try:
                    self.marks = R.marks(doc.pdf)
                except Exception:
                    self.marks = []
        pages: dict[int, QTreeWidgetItem] = {}
        for n, m in enumerate(self.marks):
            parent = pages.get(m.page)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [_("Pagina {n}").format(n=m.page + 1), "", ""])
                parent.setData(0, Qt.ItemDataRole.UserRole, -1 - m.page)
                parent.setExpanded(True)
                pages[m.page] = parent
            text = m.text or _("(nessun testo)")
            item = QTreeWidgetItem(parent, [text, source_label(m.source), _("Da applicare")])
            item.setToolTip(0, text)
            item.setData(0, Qt.ItemDataRole.UserRole, n)
        for page, parent in pages.items():
            parent.setText(1, str(parent.childCount()))
        self.count.setText(str(len(self.marks)))
        has = bool(self.marks)
        self.empty.setVisible(not has)
        self.tree.setVisible(has)
        for b in (self.remove_button, self.apply_selected_button, self.apply_all_button, self.export_button):
            b.setEnabled(has)
        self.import_button.setEnabled(doc is not None)
        tip = "" if has else _("Nessuna area contrassegnata per la redazione")
        self.apply_all_button.setToolTip(tip)

    def selected(self) -> list[R.Mark]:
        out: list[R.Mark] = []
        for item in self.tree.selectedItems():
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx is None:
                continue
            if idx < 0:   # pagina: tutti i suoi segni
                page = -1 - idx
                out.extend(m for m in self.marks if m.page == page and m not in out)
            elif self.marks[idx] not in out:
                out.append(self.marks[idx])
        return out

    def _go(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if self.view is None or idx is None:
            return
        if idx < 0:
            self.view.go_to_page(-1 - idx)
        else:
            m = self.marks[idx]
            self.view.ensure_visible(m.page, m.rect)

    def _remove(self) -> None:
        self.controller.remove_marks(self.selected())


# ---------------------------------------------------------- strumento area


class RedactAreaTool(Tool):
    """Trascina per segnare un'area; trascina un segno esistente per spostarlo."""

    id = "security-redact-area"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, on_rect: Callable[[int, pymupdf.Rect], None],
                 on_move: Callable[[int, int, pymupdf.Rect], None] | None = None):
        self.on_rect = on_rect
        self.on_move = on_move
        self._page: int | None = None
        self._start: pymupdf.Point | None = None
        self._end: pymupdf.Point | None = None
        self._moving: tuple[int, pymupdf.Rect] | None = None   # (xref, rettangolo originale)
        self._corner: str | None = None                         # angolo trascinato (ridimensiona)

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self._page, self._start, self._end = ev.page, ev.point, ev.point
        self._moving = None
        self._corner = None
        if self.on_move is not None:
            tol = 6 / max(view.zoom, 0.1)
            with ENGINE_LOCK:
                for m in R.marks(view.document.pdf, [ev.page]):
                    if m.whole_page:
                        continue
                    corner = _corner_at(m.rect, ev.point, tol)
                    if corner is not None or m.rect.contains(ev.point):
                        self._moving = (m.xref, pymupdf.Rect(m.rect))
                        self._corner = corner
                        break
        return True

    def move(self, view, ev):
        if self._page is None:
            return False
        self._end = view.clamp_to_page(self._page, ev.pos)
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._page is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        page, start, end, moving = self._page, self._start, self._end, self._moving
        self._page = self._start = self._end = None
        self._moving = None
        view.viewport().update()
        if start is None or end is None:
            return True
        if moving is not None:
            dx, dy = end.x - start.x, end.y - start.y
            if abs(dx) + abs(dy) >= 1 and self.on_move is not None:
                xref, rect = moving
                new = _resized(rect, self._corner, dx, dy)
                if new.width >= 2 and new.height >= 2:
                    self.on_move(page, xref, new)
            return True
        rect = pymupdf.Rect(start, end).normalize()
        if rect.width < 2 or rect.height < 2:      # clic senza trascinamento: nessun segno
            return True
        self.on_rect(page, rect)
        return True

    def key(self, view, event):
        if event.key() == Qt.Key.Key_Escape and self._page is not None:
            self._page = None
            view.viewport().update()
            return True
        return False

    def paint(self, view, painter: QPainter, page: int) -> None:
        if self._page != page or self._start is None or self._end is None:
            return
        pen = QPen(QColor(220, 38, 38))
        pen.setWidthF(1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        if self._moving is not None:
            _x, r = self._moving
            dx, dy = self._end.x - self._start.x, self._end.y - self._start.y
            n = _resized(r, self._corner, dx, dy)
            painter.setBrush(QColor(220, 38, 38, 50))
            painter.drawRect(QRectF(n.x0, n.y0, n.width, n.height))
            return
        painter.setBrush(QColor(220, 38, 38, 40))
        r = pymupdf.Rect(self._start, self._end).normalize()
        painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))


def _corner_at(rect: pymupdf.Rect, pt: pymupdf.Point, tol: float) -> str | None:
    for name, (x, y) in {"tl": (rect.x0, rect.y0), "tr": (rect.x1, rect.y0),
                         "bl": (rect.x0, rect.y1), "br": (rect.x1, rect.y1)}.items():
        if abs(pt.x - x) <= tol and abs(pt.y - y) <= tol:
            return name
    return None


def _resized(rect: pymupdf.Rect, corner: str | None, dx: float, dy: float) -> pymupdf.Rect:
    """Spostamento (nessun angolo) o ridimensionamento dall'angolo indicato."""
    r = pymupdf.Rect(rect)
    if corner is None:
        return r + (dx, dy, dx, dy)
    if "l" in corner:
        r.x0 += dx
    else:
        r.x1 += dx
    if "t" in corner:
        r.y0 += dy
    else:
        r.y1 += dy
    return r.normalize()


# ------------------------------------------------------------ anteprima


@dataclass
class _PreviewBox:
    rect: pymupdf.Rect
    fill: QColor
    text: str
    text_color: QColor


def _color(rgb, default) -> QColor:
    if not rgb or len(rgb) < 3:
        return default
    return QColor.fromRgbF(*[min(1.0, max(0.0, float(c))) for c in rgb[:3]])


class PreviewOverlay:
    """Livello di disegno: mostra i segni con l'aspetto finale (anteprima risultato)."""

    def __init__(self):
        self.enabled = False
        self._cache: dict[tuple[int, int, int], list[_PreviewBox]] = {}

    def boxes(self, view, page: int) -> list[_PreviewBox]:
        doc = view.document
        key = (id(doc), doc.version, page)
        if key not in self._cache:
            if len(self._cache) > 200:
                self._cache.clear()
            out = []
            with ENGINE_LOCK:
                pdf = doc.pdf
                for annot in pdf[page].annots(types=[pymupdf.PDF_ANNOT_REDACT]):
                    colors = annot.colors or {}
                    fill = _color(colors.get("fill"), QColor(0, 0, 0))
                    text = ""
                    try:
                        kind, val = pdf.xref_get_key(annot.xref, "OverlayText")
                        if kind == "string":
                            text = val
                    except Exception:
                        pass
                    tc = QColor(255, 255, 255) if fill.lightnessF() < 0.55 else QColor(0, 0, 0)
                    out.append(_PreviewBox(pymupdf.Rect(annot.rect), fill, text, tc))
            self._cache[key] = out
        return self._cache[key]

    def __call__(self, view, painter: QPainter, page: int) -> None:
        if not self.enabled:
            return
        for b in self.boxes(view, page):
            r = QRectF(b.rect.x0, b.rect.y0, b.rect.width, b.rect.height)
            painter.fillRect(r, b.fill)
            if b.text and b.rect.height >= 6:
                painter.setPen(b.text_color)
                f = QFont("DejaVu Sans")
                f.setPointSizeF(max(4.0, min(11.0, b.rect.height * 0.6)))
                painter.setFont(f)
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, b.text)


# ---------------------------------------------------------- barra di avviso


@dataclass
class Notice:
    key: str
    priority: int                    # 0 = sicurezza, 1 = integrità, 2 = informazione
    text: str
    actions: list[tuple[str, Callable[[], None]]]
    kind: str = "warning"


class NoticeBoard:
    """Avvisi del modulo per una vista, mostrati con `ctx.show_banner` del nucleo (UX-14)."""

    PREFIX = "security-"
    _LEVEL = {"warning": "warning", "danger": "error", "info": "info"}

    def __init__(self, ctx, view):
        import weakref
        self.ctx = ctx
        self._view = weakref.ref(view)
        self._notices: dict[str, Notice] = {}

    @property
    def view(self):
        return self._view()

    @property
    def notices(self) -> list[Notice]:
        return sorted(self._notices.values(), key=lambda n: n.priority)

    def set_notice(self, notice: Notice) -> None:
        view = self.view
        if view is None:
            return
        self._notices[notice.key] = notice
        self.ctx.show_banner(view, self.PREFIX + notice.key, notice.text, list(notice.actions),
                             level=self._LEVEL.get(notice.kind, "warning"), priority=100 - 40 * notice.priority)

    def remove_notice(self, key: str) -> None:
        self._notices.pop(key, None)
        view = self.view
        if view is not None:
            self.ctx.hide_banner(view, self.PREFIX + key)

    def current(self) -> Notice | None:
        """Il primo avviso del modulo tra quelli visibili (non chiusi dall'utente)."""
        view = self.view
        if view is None:
            return None
        for banner_id in self.ctx.banners(view):
            if banner_id.startswith(self.PREFIX):
                notice = self._notices.get(banner_id[len(self.PREFIX):])
                if notice is not None:
                    return notice
        return None

    def isVisible(self) -> bool:
        return self.current() is not None
