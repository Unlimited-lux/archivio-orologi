"""Strumenti sulla pagina: posizionamento e modifica della firma grafica (SIG-05),
area della firma digitale e campo firma vuoto (SIG-08/19)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import pymupdf
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from foliant.api import ENGINE_LOCK, Tool, _, theme

from . import graphic as g

if TYPE_CHECKING:
    from foliant.api import DocumentView

HANDLE_PX = 7


def _page_rotation(view, page: int) -> int:
    with ENGINE_LOCK:
        return view.document.pdf[page].rotation % 360


def _pixels_per_point(view, page: int) -> float:
    t = view.page_transform(page)
    return math.hypot(t.m11(), t.m12()) or 1.0


def _qrect(r: pymupdf.Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


class SignatureTool(Tool):
    """Posiziona una firma (anteprima che segue il puntatore, clic o trascinamento)
    e modifica quelle già inserite: spostamento, maniglie con proporzioni bloccate
    (Maiusc le sblocca), frecce 1 pt (10 pt con Maiusc), Canc per eliminare."""

    id = "sign-graphic"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, controller) -> None:
        self.c = controller
        self.pending: g.SignatureItem | None = None
        self.hover: tuple[int, pymupdf.Point] | None = None
        self.selected: tuple[int, int] | None = None     # (pagina, xref)
        self._drag: dict | None = None
        self._pixmap_cache: dict = {}
        self._guides_cache: dict = {}
        self.active_guides: list[tuple[str, float]] = []
        self.snap = True

    # ------------------------------------------------------------ stato
    def set_pending(self, item: g.SignatureItem | None) -> None:
        self.pending = item
        self._pixmap_cache.clear()

    def selected_rect(self, view) -> pymupdf.Rect | None:
        if self.selected is None:
            return None
        page, xref = self.selected
        with ENGINE_LOCK:
            if page >= view.document.pdf.page_count:
                return None
            s = g.find_signature(view.document.pdf, page, xref)
        return s.rect if s else None

    def _hit(self, view, page: int, pt: pymupdf.Point):
        with ENGINE_LOCK:
            sigs = g.placed_signatures(view.document.pdf, page)
        for s in reversed(sigs):
            if s.rect.contains(pt):
                return s
        return None

    def _handle_at(self, view, page: int, pt: pymupdf.Point) -> str | None:
        r = self.selected_rect(view)
        if r is None or self.selected[0] != page:
            return None
        tol = HANDLE_PX / _pixels_per_point(view, page)
        for name, (hx, hy) in {"tl": (r.x0, r.y0), "tr": (r.x1, r.y0), "bl": (r.x0, r.y1), "br": (r.x1, r.y1)}.items():
            if abs(pt.x - hx) <= tol and abs(pt.y - hy) <= tol:
                return name
        return None

    def placement_rect(self, view, page: int, center: pymupdf.Point, drag_to: pymupdf.Point | None = None) -> pymupdf.Rect:
        item = self.pending
        rot = _page_rotation(view, page)
        vw, vh = item.default_size()
        aspect_unrot = item.aspect if rot % 180 == 0 else 1 / item.aspect
        if drag_to is not None and (abs(drag_to.x - center.x) > 4 or abs(drag_to.y - center.y) > 4):
            w = abs(drag_to.x - center.x)
            h = w / aspect_unrot
            x0 = min(center.x, drag_to.x)
            y0 = center.y if drag_to.y >= center.y else center.y - h
            return pymupdf.Rect(x0, y0, x0 + w, y0 + h)
        w, h = (vw, vh) if rot % 180 == 0 else (vh, vw)
        return pymupdf.Rect(center.x - w / 2, center.y - h / 2, center.x + w / 2, center.y + h / 2)

    # ----------------------------------------------------------- eventi
    def deactivate(self, view) -> None:
        self.hover = None
        self._drag = None
        self.pending = None

    def press(self, view, ev) -> bool:
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if ev.page is None:
            self.selected = None
            return True
        if self.pending is not None:
            self._drag = {"kind": "place", "page": ev.page, "start": ev.point, "now": ev.point}
            return True
        handle = self._handle_at(view, ev.page, ev.point)
        if handle:
            self._drag = {"kind": "resize", "handle": handle, "page": ev.page, "start": ev.point,
                          "rect": self.selected_rect(view), "now": ev.point}
            return True
        hit = self._hit(view, ev.page, ev.point)
        if hit is not None:
            self.selected = (ev.page, hit.xref)
            self._drag = {"kind": "move", "page": ev.page, "start": ev.point, "rect": hit.rect, "now": ev.point}
            self.c.selection_changed(view, self)
            return True
        self.selected = None
        self.c.selection_changed(view, self)
        return True

    def move(self, view, ev) -> bool:
        if ev.page is not None:
            self.hover = (ev.page, ev.point)
        if self._drag is not None and ev.point is not None:
            self._drag["now"] = view.clamp_to_page(self._drag["page"], ev.pos)
            self._drag["shift"] = bool(ev.modifiers & Qt.KeyboardModifier.ShiftModifier)
            self._drag["nosnap"] = bool(ev.modifiers & Qt.KeyboardModifier.AltModifier)
            return True
        if self.pending is not None:
            return True
        if ev.page is not None:
            cur = Qt.CursorShape.ArrowCursor
            if self._handle_at(view, ev.page, ev.point):
                cur = Qt.CursorShape.SizeFDiagCursor
            elif self._hit(view, ev.page, ev.point):
                cur = Qt.CursorShape.SizeAllCursor
            view.viewport().setCursor(cur)
        return False

    def guides(self, view, page: int):
        key = (page, view.document.version)
        if key not in self._guides_cache:
            with ENGINE_LOCK:
                self._guides_cache = {key: g.page_guides(view.document.pdf[page])}
        return self._guides_cache[key]

    def _snapped(self, view, page: int, rect: pymupdf.Rect) -> pymupdf.Rect:
        self.active_guides = []
        if not self.snap or (self._drag or {}).get("nosnap"):
            return rect
        xs, ys, lines = self.guides(view, page)
        r, self.active_guides = g.snap_rect(rect, xs, ys, lines, 6.0 / _pixels_per_point(view, page))
        return r

    def _drag_rect(self, view) -> pymupdf.Rect | None:
        d = self._drag
        if d is None:
            return None
        start, now = d["start"], d["now"]
        if d["kind"] == "place":
            r = self.placement_rect(view, d["page"], start, now)
            return self._snapped(view, d["page"], r)
        r = pymupdf.Rect(d["rect"])
        dx, dy = now.x - start.x, now.y - start.y
        if d["kind"] == "move":
            return self._snapped(view, d["page"], r + (dx, dy, dx, dy))
        h = d["handle"]
        fixed = pymupdf.Point(r.x1 if "l" in h else r.x0, r.y1 if "t" in h else r.y0)
        moving = pymupdf.Point((r.x0 if "l" in h else r.x1) + dx, (r.y0 if "t" in h else r.y1) + dy)
        w = max(g.MIN_SIDE, abs(moving.x - fixed.x))
        hh = max(g.MIN_SIDE, abs(moving.y - fixed.y))
        if not d.get("shift"):
            aspect = r.width / r.height if r.height else 1
            hh = w / aspect
        x0 = fixed.x - w if "l" in h else fixed.x
        y0 = fixed.y - hh if "t" in h else fixed.y
        return pymupdf.Rect(x0, y0, x0 + w, y0 + hh)

    def release(self, view, ev) -> bool:
        d = self._drag
        if d is None:
            return False
        rect = self._drag_rect(view)
        self._drag = None
        self.active_guides = []
        if d["kind"] == "place":
            item = self.pending
            xref = self.c.place(view, d["page"], rect, item)
            if xref:
                self.pending = None
                self.selected = (d["page"], xref)
                self.c.selection_changed(view, self)
            return True
        moved = abs(d["now"].x - d["start"].x) > 0.5 or abs(d["now"].y - d["start"].y) > 0.5
        if moved and self.selected is not None:
            self.c.set_rect(view, self.selected[0], self.selected[1], rect,
                            _("Sposta firma") if d["kind"] == "move" else _("Ridimensiona firma"))
        return True

    def place_with_keyboard(self, view) -> int | None:
        """Alternativa da tastiera al clic: posiziona la firma in attesa al centro
        della parte visibile della pagina corrente (poi si sposta con le frecce)."""
        if self.pending is None:
            return None
        page = view.current_page
        vp = view.viewport().rect()
        hit = view.view_to_page(QPointF(vp.center()), clamp=True)
        if hit is not None and hit[0] == page:
            center = hit[1]
        else:
            with ENGINE_LOCK:
                crop = pymupdf.Rect(view.document.pdf[page].cropbox)   # coordinate non ruotate
            center = pymupdf.Point((crop.x0 + crop.x1) / 2, (crop.y0 + crop.y1) / 2)
        item = self.pending
        rect = self.placement_rect(view, page, center)
        xref = self.c.place(view, page, rect, item)
        if xref:
            self.pending = None
            self.selected = (page, xref)
            self.c.selection_changed(view, self)
        return xref

    def key(self, view, event) -> bool:
        k = event.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.pending is not None:
            self.place_with_keyboard(view)
            view.viewport().update()
            return True
        if k == Qt.Key.Key_Escape:
            if self.pending is not None:
                self.pending = None
            else:
                self.selected = None
            view.viewport().update()
            return True
        if self.selected is None:
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            page, xref = self.selected
            self.selected = None
            self.c.delete(view, page, xref)
            return True
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                 Qt.Key.Key_Down: (0, step)}
        if k in moves:
            r = self.selected_rect(view)
            if r is None:
                return False
            # le frecce si riferiscono alla vista: convertiamo lo spostamento nelle coordinate della pagina
            dx, dy = moves[k]
            rot = _page_rotation(view, self.selected[0])
            vec = pymupdf.Point(dx, dy) * pymupdf.Matrix(-rot)
            self.c.set_rect(view, self.selected[0], self.selected[1], r + (vec.x, vec.y, vec.x, vec.y),
                            _("Sposta firma"))
            return True
        return False

    # ----------------------------------------------------------- disegno
    def _draw_item(self, view, painter: QPainter, page: int, rect: pymupdf.Rect, item: g.SignatureItem,
                   alpha: float = 0.6) -> None:
        from .ui_create import item_pixmap
        rot = _page_rotation(view, page)
        vw, vh = g.visual_size(rect, rot)
        key = (id(item), int(vw), int(vh))
        pm = self._pixmap_cache.get(key)
        if pm is None:
            ppp = _pixels_per_point(view, page)
            pm = item_pixmap(item, QSize(max(4, int(vw * ppp)), max(4, int(vh * ppp))))
            self._pixmap_cache = {key: pm}
        painter.save()
        c = QPointF((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        painter.translate(c)
        painter.rotate(-rot)
        painter.setOpacity(alpha)
        painter.drawPixmap(QRectF(-vw / 2, -vh / 2, vw, vh), pm, QRectF(pm.rect()))
        painter.restore()

    def _paint_guides(self, view, painter: QPainter, page: int) -> None:
        if not self.active_guides:
            return
        ppp = _pixels_per_point(view, page)
        with ENGINE_LOCK:
            crop = pymupdf.Rect(view.document.pdf[page].cropbox)
        painter.setPen(QPen(theme.color("secondary"), 1 / ppp, Qt.PenStyle.DashDotLine))
        for axis, v in self.active_guides:
            if axis == "x":
                painter.drawLine(QPointF(v, crop.y0), QPointF(v, crop.y1))
            else:
                painter.drawLine(QPointF(crop.x0, v), QPointF(crop.x1, v))

    def paint(self, view, painter: QPainter, page: int) -> None:
        accent = theme.color("accent")
        ppp = _pixels_per_point(view, page)
        if self._drag is not None and self._drag["page"] == page:
            r = self._drag_rect(view)
            if r is not None:
                if self.pending is not None:
                    self._draw_item(view, painter, page, r, self.pending)
                pen = QPen(accent, 1.2 / ppp, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(_qrect(r))
                self._paint_guides(view, painter, page)
            return
        if self.pending is not None and self.hover is not None and self.hover[0] == page:
            r = self.placement_rect(view, page, self.hover[1])
            self._draw_item(view, painter, page, r, self.pending)
            painter.setPen(QPen(accent, 1 / ppp, Qt.PenStyle.DashLine))
            painter.drawRect(_qrect(r))
        if self.selected is not None and self.selected[0] == page:
            r = self.selected_rect(view)
            if r is None:
                return
            painter.setPen(QPen(accent, 1.4 / ppp))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(_qrect(r))
            hs = HANDLE_PX / ppp
            painter.setBrush(theme.color("surface"))
            for x, y in ((r.x0, r.y0), (r.x1, r.y0), (r.x0, r.y1), (r.x1, r.y1)):
                painter.drawRect(QRectF(x - hs / 2, y - hs / 2, hs, hs))


class RectTool(Tool):
    """Traccia un rettangolo e chiama `on_rect(view, page, rect)`. Invio senza
    rettangolo chiama `on_enter(view)` (es. firma invisibile); un clic su un
    campo firma vuoto chiama `on_field(view, field)`."""

    id = "sign-rect"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, on_rect: Callable, on_enter: Callable | None = None, fields: Callable | None = None,
                 on_field: Callable | None = None, min_size: tuple[float, float] = (20, 10)) -> None:
        self.on_rect = on_rect
        self.on_enter = on_enter
        self.fields = fields
        self.on_field = on_field
        self.min_size = min_size
        self._start = None
        self._now = None
        self._page = None

    def press(self, view, ev) -> bool:
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        if self.fields and self.on_field:
            for f in self.fields(view):
                if f.page == ev.page and f.rect is not None and pymupdf.Rect(f.rect).contains(ev.point) \
                        and not f.signed:
                    self.on_field(view, f)
                    return True
        self._page, self._start, self._now = ev.page, ev.point, ev.point
        return True

    def move(self, view, ev) -> bool:
        if self._start is None:
            return False
        self._now = view.clamp_to_page(self._page, ev.pos)
        return True

    def release(self, view, ev) -> bool:
        if self._start is None:
            return False
        r = pymupdf.Rect(self._start, self._now)
        r.normalize()
        page = self._page
        self._start = self._now = self._page = None
        if r.width < 3 and r.height < 3:
            return True
        if r.width < self.min_size[0] or r.height < self.min_size[1]:
            r = pymupdf.Rect(r.x0, r.y0, r.x0 + max(r.width, self.min_size[0]), r.y0 + max(r.height, self.min_size[1]))
        self.on_rect(view, page, r)
        return True

    def key(self, view, event) -> bool:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.on_enter:
            self.on_enter(view)
            return True
        if event.key() == Qt.Key.Key_Escape:
            view.set_tool(None)
            return True
        return False

    def paint(self, view, painter: QPainter, page: int) -> None:
        ppp = _pixels_per_point(view, page)
        if self.fields:
            painter.setPen(QPen(theme.color("warning"), 1.2 / ppp, Qt.PenStyle.DashLine))
            fill = QColor(theme.color("warning"))
            fill.setAlpha(40)
            painter.setBrush(fill)
            for f in self.fields(view):
                if f.page == page and f.rect is not None and not f.signed and not f.hidden:
                    painter.drawRect(_qrect(pymupdf.Rect(f.rect)))
        if self._start is not None and self._page == page:
            r = pymupdf.Rect(self._start, self._now)
            r.normalize()
            painter.setPen(QPen(theme.color("accent"), 1.4 / ppp, Qt.PenStyle.DashLine))
            fill = QColor(theme.color("accent"))
            fill.setAlpha(30)
            painter.setBrush(fill)
            painter.drawRect(_qrect(r))


TRANSLATIONS_EN = {
    "Sposta firma": "Move signature",
    "Ridimensiona firma": "Resize signature",
}
