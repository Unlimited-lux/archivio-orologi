"""Modalità modifica modulo: selezione, spostamento, ridimensionamento e creazione dei campi.

Gli strumenti lavorano in coordinate pagina; ogni operazione (creazione,
spostamento di più campi, eliminazione…) è un solo passo di annulla/ripeti.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from foliant.api import ENGINE_LOCK, Tool, _, theme

from . import engine as E

if TYPE_CHECKING:
    from .module import FormsModule

HANDLE = 4.0  # metà lato delle maniglie, in punti


class FieldSelectTool(Tool):
    """Seleziona, sposta e ridimensiona i campi (modifica modulo)."""

    id = "forms-select"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, module: "FormsModule"):
        self.m = module
        self._drag: dict | None = None

    # ------------------------------------------------------------ eventi
    def press(self, view, ev):
        if ev.page is None or ev.button != Qt.MouseButton.LeftButton:
            if ev.page is None:
                self.m.set_selection([])
            return ev.page is None
        filler = self.m.filler(view)
        filler.state.refresh()
        sel = self.m.selected_fields(view)
        # maniglia di ridimensionamento della selezione singola
        if len(sel) == 1 and sel[0].page == ev.page and not sel[0].locked:
            corner = _corner_at(sel[0].rect, ev.point, view)
            if corner:
                self._drag = {"mode": "resize", "corner": corner, "start": ev.point, "page": ev.page,
                              "orig": {(sel[0].page, sel[0].xref): pymupdf.Rect(sel[0].rect)}, "moved": False}
                return True
        f = filler.state.field_at(ev.page, ev.point)
        add = bool(ev.modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if f is None:
            if not add:
                self.m.set_selection([])
            self._drag = {"mode": "band", "start": ev.point, "end": ev.point, "page": ev.page, "add": add}
            return True
        key = (f.page, f.xref)
        keys = list(self.m.selection)
        if add:
            if key in keys:
                keys.remove(key)
            else:
                keys.append(key)
            self.m.set_selection(keys)
        elif key not in keys:
            self.m.set_selection([key])
        sel = self.m.selected_fields(view)
        self._drag = {"mode": "move", "start": ev.point, "page": ev.page,
                      "orig": {(s.page, s.xref): pymupdf.Rect(s.rect) for s in sel if s.page == ev.page and not s.locked},
                      "moved": False, "delta": (0.0, 0.0)}
        return True

    def move(self, view, ev):
        d = self._drag
        if d is None:
            if ev.page is not None:
                sel = self.m.selected_fields(view)
                if len(sel) == 1 and sel[0].page == ev.page and _corner_at(sel[0].rect, ev.point, view):
                    view.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
                else:
                    view.viewport().setCursor(self.cursor)
            return False
        if ev.page is None:
            pos = view.clamp_to_page(d["page"], ev.pos)
        elif ev.page != d["page"]:
            pos = view.clamp_to_page(d["page"], ev.pos)
        else:
            pos = ev.point
        if d["mode"] == "band":
            d["end"] = pos
            return True
        dx, dy = pos.x - d["start"].x, pos.y - d["start"].y
        if abs(dx) + abs(dy) > 1.0:
            d["moved"] = True
        d["delta"] = (dx, dy)
        d["pos"] = pos
        return True

    def release(self, view, ev):
        d, self._drag = self._drag, None
        if d is None:
            return False
        if d["mode"] == "band":
            band = pymupdf.Rect(d["start"], d["end"]).normalize()
            if band.width > 2 and band.height > 2:
                filler = self.m.filler(view)
                keys = [(f.page, f.xref) for f in filler.state.by_page.get(d["page"], [])
                        if pymupdf.Rect(f.rect).intersects(band)]
                if d["add"]:
                    keys = list(dict.fromkeys(self.m.selection + keys))
                self.m.set_selection(keys)
            return True
        if not d.get("moved"):
            return True
        changes = []
        for key, r in d["orig"].items():
            changes.append((key[0], key[1], _apply_drag(d, r)))
        if changes:
            label = _("Ridimensiona campo") if d["mode"] == "resize" else _("Sposta campi")
            self.m.move_fields(view, changes, label)
        return True

    def double_click(self, view, ev):
        if ev.page is None:
            return False
        f = self.m.filler(view).state.field_at(ev.page, ev.point)
        if f is not None:
            self.m.set_selection([(f.page, f.xref)])
            self.m.show_properties()
            return True
        return False

    def key(self, view, event):
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.m.delete_selected()
            return True
        if key == Qt.Key.Key_Escape:
            self.m.set_selection([])
            return True
        if key == Qt.Key.Key_C and mods & Qt.KeyboardModifier.ControlModifier:
            self.m.copy_selected()
            return True
        if key == Qt.Key.Key_V and mods & Qt.KeyboardModifier.ControlModifier:
            self.m.paste_fields(view)
            return True
        if key == Qt.Key.Key_D and mods & Qt.KeyboardModifier.ControlModifier:
            self.m.duplicate_selected()
            return True
        if key == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            filler = self.m.filler(view)
            self.m.set_selection([(f.page, f.xref) for f in filler.state.by_page.get(view.current_page, [])])
            return True
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            step = 10.0 if mods & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx, dy = arrows[key]
            self.m.nudge_selected(dx * step, dy * step)
            return True
        return False

    # ------------------------------------------------------------ disegno
    def paint(self, view, painter: QPainter, page: int) -> None:
        d = self._drag
        if d is None or d["page"] != page:
            return
        if d["mode"] == "band":
            r = pymupdf.Rect(d["start"], d["end"]).normalize()
            c = QColor(theme.color("accent"))
            c.setAlpha(40)
            painter.fillRect(QRectF(r.x0, r.y0, r.width, r.height), c)
            painter.setPen(QPen(theme.color("accent"), 0.8, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))
            return
        if not d.get("moved"):
            return
        painter.setPen(QPen(theme.color("accent"), 1.0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for r in d["orig"].values():
            n = _apply_drag(d, r)
            painter.drawRect(QRectF(n.x0, n.y0, n.width, n.height))


def _corner_at(rect: pymupdf.Rect, pt: pymupdf.Point, view) -> str | None:
    tol = max(HANDLE, 6.0 / max(0.1, view.zoom))
    if abs(pt.x - rect.x1) <= tol and abs(pt.y - rect.y1) <= tol:
        return "br"
    if abs(pt.x - rect.x0) <= tol and abs(pt.y - rect.y0) <= tol:
        return "tl"
    if abs(pt.x - rect.x1) <= tol and abs(pt.y - rect.y0) <= tol:
        return "tr"
    if abs(pt.x - rect.x0) <= tol and abs(pt.y - rect.y1) <= tol:
        return "bl"
    return None


def _apply_drag(d: dict, r: pymupdf.Rect) -> pymupdf.Rect:
    dx, dy = d.get("delta", (0.0, 0.0))
    if d["mode"] == "move":
        return pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
    c = d["corner"]
    x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
    if "l" in c:
        x0 += dx
    else:
        x1 += dx
    if "t" in c:
        y0 += dy
    else:
        y1 += dy
    n = pymupdf.Rect(x0, y0, x1, y1).normalize()
    if n.width < E.MIN_SIZE:
        n.x1 = n.x0 + E.MIN_SIZE
    if n.height < E.MIN_SIZE:
        n.y1 = n.y0 + E.MIN_SIZE
    return n


class FieldCreateTool(Tool):
    """Disegna un nuovo campo: clic (dimensione predefinita) o trascinamento."""

    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, module: "FormsModule", kind: str, sticky: bool = False):
        self.m = module
        self.kind = kind
        self.sticky = sticky
        self.id = f"forms-create-{kind}"
        self._start: tuple[int, pymupdf.Point] | None = None
        self._end: pymupdf.Point | None = None

    def press(self, view, ev):
        if ev.page is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        self._start = (ev.page, ev.point)
        self._end = ev.point
        return True

    def move(self, view, ev):
        if self._start is None:
            return False
        page = self._start[0]
        self._end = ev.point if ev.page == page else view.clamp_to_page(page, ev.pos)
        return True

    def release(self, view, ev):
        if self._start is None:
            return False
        page, p0 = self._start
        p1 = self._end or p0
        self._start = None
        self._end = None
        r = pymupdf.Rect(p0, p1).normalize()
        if r.width < 3 and r.height < 3:
            w, h = E.DEFAULT_SIZES.get("text" if self.kind == "date" else ("button" if self.kind == "image" else self.kind),
                                       (120, 20))
            if self.kind == "image":
                w, h = 120, 80
            r = pymupdf.Rect(p0.x, p0.y, p0.x + w, p0.y + h)
        self.m.create_field(view, page, self.kind, r, sticky=self.sticky)
        return True

    def key(self, view, event):
        if event.key() == Qt.Key.Key_Escape:
            self._start = None
            self.m.activate_select_tool()
            return True
        return False

    def paint(self, view, painter: QPainter, page: int) -> None:
        if self._start is None or self._start[0] != page or self._end is None:
            return
        r = pymupdf.Rect(self._start[1], self._end).normalize()
        painter.setPen(QPen(theme.color("accent"), 1.0, Qt.PenStyle.DashLine))
        c = QColor(theme.color("accent"))
        c.setAlpha(30)
        painter.setBrush(c)
        painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))


KIND_ICONS = {
    E.TEXT: "text-cursor-input", E.CHECKBOX: "square-check", E.RADIO: "circle-dot", E.COMBO: "square-chevron-down",
    E.LIST: "list", E.BUTTON: "square-mouse-pointer", "image": "image", "date": "calendar", E.SIGNATURE: "signature",
}


def paint_fields(view, p: QPainter, page: int, fields: list[E.FieldInfo], selection: set) -> None:
    """Aspetto dei campi in modifica modulo: contorno, nome e tipo; selezione con maniglie."""
    accent = theme.color("accent")
    danger = theme.color("danger")
    font = QFont()
    font.setPixelSize(7)
    p.setFont(font)
    rects = [pymupdf.Rect(f.rect) for f in fields]
    for i, f in enumerate(fields):
        r = QRectF(f.rect.x0, f.rect.y0, f.rect.width, f.rect.height)
        tint = QColor(accent)
        tint.setAlpha(28)
        p.fillRect(r, tint)
        overlap = any(j != i and (rects[i] & rects[j]).get_area() > 1 for j in range(len(rects)))
        p.setPen(QPen(danger if overlap else accent, 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        label = f"{f.name} · {E.kind_label(f.kind)}" if r.width() > 60 else f.name
        tag = QRectF(r.x(), r.y() - 9, max(r.width(), 20), 9) if r.y() > 9 else QRectF(r.x(), r.y(), r.width(), 9)
        back = QColor(theme.color("surface"))
        back.setAlpha(210)
        p.fillRect(QRectF(tag.x(), tag.y(), min(tag.width(), 6 + len(label) * 4.0), tag.height()), back)
        p.setPen(theme.color("text"))
        p.drawText(tag.adjusted(1.5, 0, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        if (f.page, f.xref) in selection:
            p.setPen(QPen(accent, 1.6))
            p.drawRect(r.adjusted(-1, -1, 1, 1))
            p.setBrush(accent)
            p.setPen(Qt.PenStyle.NoPen)
            for cx, cy in ((r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()), (r.right(), r.bottom())):
                p.drawRect(QRectF(cx - HANDLE / 2, cy - HANDLE / 2, HANDLE, HANDLE))
            p.setBrush(Qt.BrushStyle.NoBrush)


def paint_tab_numbers(p: QPainter, order: list[E.FieldInfo]) -> None:
    """Numero d'ordine di tabulazione in un cerchio sull'angolo di ogni campo."""
    font = QFont()
    font.setPixelSize(7)
    font.setBold(True)
    p.setFont(font)
    for i, f in enumerate(order, 1):
        r = QRectF(f.rect.x0 - 5, f.rect.y0 - 5, 11, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("secondary"))
        p.drawEllipse(r)
        p.setPen(theme.color("text") if not theme.is_dark() else QColor(20, 20, 20))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(i))
