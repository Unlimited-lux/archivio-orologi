"""Strumenti della modalità Modifica per il testo (TXT-01, 02, 06, 07, 15)."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QToolTip

from ...ui import theme
from ...ui.tools import PageEvent, Tool
from .analysis import block_at
from .model import TextBlock


def frame_rect_to_page(ctrl, page: int, r: pymupdf.Rect) -> QRectF:
    """Rettangolo del riquadro di lavoro -> coordinate pagina non ruotata (QRectF)."""
    rr = pymupdf.Rect(r) * ctrl.unframe(page)
    return QRectF(rr.x0, rr.y0, rr.width, rr.height)


@dataclass
class _Drag:
    kind: str                 # "move" | "resize-l" | "resize-r"
    page: int
    block: TextBlock
    start: pymupdf.Point      # punto iniziale (riquadro di lavoro)
    current: pymupdf.Point
    target_page: int


HANDLE_PX = 7


class EditTextTool(Tool):
    """Clic su un blocco: modifica in loco. Clic sul bordo: selezione come oggetto."""

    id = "text-edit"
    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, controller):
        self.ctrl = controller
        self.hover: tuple[int, TextBlock] | None = None
        self.selected: tuple[int, TextBlock] | None = None
        self.drag: _Drag | None = None

    # ------------------------------------------------------------ ciclo di vita
    def activate(self, view) -> None:
        self.ctrl.tool_activated(view, self)
        page = view.current_page
        self.ctrl.check_page_has_text(view, page)

    def deactivate(self, view) -> None:
        self.ctrl.commit_editor(view)
        self.selected = None
        self.hover = None
        self.drag = None
        self.ctrl.selection_changed(view, None)

    # ------------------------------------------------------------ utilità
    def _hit(self, view, ev: PageEvent) -> tuple[int, pymupdf.Point, TextBlock | None] | None:
        if ev.page is None:
            return None
        fpt = ev.point * self.ctrl.frame(ev.page)
        blocks = self.ctrl.blocks(view, ev.page)
        return ev.page, fpt, block_at(blocks, fpt, margin=self._margin(view))

    def _margin(self, view) -> float:
        return max(1.5, HANDLE_PX / max(view.zoom * 1.333, 0.1) * 0.6)

    def _on_border(self, view, block: TextBlock, fpt: pymupdf.Point) -> bool:
        m = self._margin(view)
        r = block.bbox
        inside_inner = (r.x0 + m < fpt.x < r.x1 - m) and (r.y0 + m < fpt.y < r.y1 - m)
        return not inside_inner

    def _handle_at(self, view, fpt: pymupdf.Point) -> str | None:
        if self.selected is None:
            return None
        _p, block = self.selected
        r = block.bbox
        m = self._margin(view) * 1.5
        cy = (r.y0 + r.y1) / 2
        if abs(fpt.x - r.x0) <= m and abs(fpt.y - cy) <= max(m, r.height / 2):
            return "resize-l"
        if abs(fpt.x - r.x1) <= m and abs(fpt.y - cy) <= max(m, r.height / 2):
            return "resize-r"
        return None

    def select(self, view, page: int, block: TextBlock | None) -> None:
        self.selected = (page, block) if block is not None else None
        self.ctrl.selection_changed(view, self.selected)
        view.viewport().update()

    # ------------------------------------------------------------ mouse
    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if self.ctrl.editor_for(view) is not None:
            self.ctrl.commit_editor(view)
        hit = self._hit(view, ev)
        if hit is None:
            self.select(view, 0, None)
            return True
        page, fpt, block = hit
        # maniglie e trascinamento del blocco selezionato
        if self.selected is not None and self.selected[0] == page:
            handle = self._handle_at(view, fpt)
            sel_block = self.selected[1]
            if handle is not None:
                self.drag = _Drag(handle, page, sel_block, fpt, fpt, page)
                return True
            if sel_block.bbox.contains(fpt) and (block is None or block is sel_block or
                                                 self._on_border(view, sel_block, fpt)):
                self.drag = _Drag("move", page, sel_block, fpt, fpt, page)
                return True
        if block is None:
            self.select(view, page, None)
            self.ctrl.check_page_has_text(view, page)
            return True
        if not block.editable:
            self.select(view, page, None)
            self.ctrl.explain_block(view, block)
            return True
        if self._on_border(view, block, fpt) or ev.modifiers & Qt.KeyboardModifier.AltModifier:
            self.select(view, page, block)
            self.drag = _Drag("move", page, block, fpt, fpt, page)
            return True
        self.select(view, page, None)
        self.ctrl.open_editor(view, page, block, click_pos=ev.pos)
        return True

    def move(self, view, ev):
        if self.drag is not None:
            if ev.page is not None:
                if self.drag.kind == "move" and ev.page != self.drag.page:
                    # trascinamento su un'altra pagina (vista continua)
                    self.drag.target_page = ev.page
                    self.drag.current = self._cross_page_point(view, ev)
                else:
                    self.drag.target_page = self.drag.page
                    self.drag.current = ev.point * self.ctrl.frame(self.drag.page)
            view.viewport().update()
            return True
        hit = self._hit(view, ev)
        old = self.hover
        if hit is None or hit[2] is None:
            self.hover = None
            view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.hideText()
        else:
            page, fpt, block = hit
            self.hover = (page, block)
            if self._handle_at(view, fpt):
                view.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            elif not block.editable:
                view.viewport().setCursor(Qt.CursorShape.ForbiddenCursor)
            elif self._on_border(view, block, fpt) or (self.selected and self.selected[1] is block):
                view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                view.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            tip = self.ctrl.block_tooltip(block)
            if tip:
                ge = getattr(ev.event, "globalPosition", None)
                if ge is not None:
                    QToolTip.showText(ge().toPoint(), tip, view.viewport())
        if old != self.hover:
            view.viewport().update()
        return False

    def _cross_page_point(self, view, ev) -> pymupdf.Point:
        """Punto nella pagina di destinazione (stesse coordinate relative all'angolo della pagina)."""
        return ev.point * self.ctrl.frame(ev.page)

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        drag, self.drag = self.drag, None
        if drag is None:
            return True
        if ev.page is not None:
            if drag.kind == "move" and ev.page != drag.page:
                drag.target_page = ev.page
            drag.current = ev.point * self.ctrl.frame(drag.target_page)
        dx = drag.current.x - drag.start.x
        dy = drag.current.y - drag.start.y
        if drag.kind == "move":
            if drag.target_page != drag.page:
                self.ctrl.move_block(view, drag.page, drag.block, dx, dy, target_page=drag.target_page)
            elif abs(dx) + abs(dy) >= 0.5:
                self.ctrl.move_block(view, drag.page, drag.block, dx, dy)
        else:
            r = drag.block.bbox
            if drag.kind == "resize-r":
                x0, x1 = drag.block.x0, max(drag.block.x0 + 2, r.x1 + dx)
            else:
                x0, x1 = min(r.x1 - 2, drag.block.x0 + dx), drag.block.x0 + drag.block.width
            if abs(dx) >= 0.5:
                self.ctrl.resize_block(view, drag.page, drag.block, x0, x1 - x0)
        return True

    def double_click(self, view, ev):
        return False

    # ------------------------------------------------------------ tastiera
    def key(self, view, event) -> bool:
        if self.selected is None:
            if event.key() == Qt.Key.Key_Escape and self.ctrl.editor_for(view) is None:
                return False
            if event.matches(QKeySequence.StandardKey.Paste):
                return self.ctrl.paste_block(view)
            return False
        page, block = self.selected
        key = event.key()
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                 Qt.Key.Key_Down: (0, step)}
        if key in moves:
            self.ctrl.move_block(view, page, block, *moves[key])
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.ctrl.delete_block(view, page, block)
            return True
        if key == Qt.Key.Key_Escape:
            self.select(view, page, None)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.select(view, page, None)
            self.ctrl.open_editor(view, page, block)
            return True
        if event.matches(QKeySequence.StandardKey.Copy):
            self.ctrl.copy_block(view, page, block)
            return True
        if event.matches(QKeySequence.StandardKey.Cut):
            self.ctrl.copy_block(view, page, block)
            self.ctrl.delete_block(view, page, block)
            return True
        if event.matches(QKeySequence.StandardKey.Paste):
            return self.ctrl.paste_block(view)
        if key == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.ctrl.duplicate_block(view, page, block)
            return True
        return False

    # ------------------------------------------------------------ disegno
    def paint(self, view, painter: QPainter, page: int) -> None:
        accent = theme.color("accent")
        danger = theme.color("danger")
        warn = theme.color("warning")
        z = max(view.zoom * 1.333, 0.05)
        blocks = self.ctrl.blocks(view, page)
        if blocks and self.ctrl.show_ocr_text:
            self.ctrl.paint_ocr_text(view, painter, page, blocks)
        if blocks:
            for b in blocks:
                if not b.editable:
                    pen = QPen(danger, 1.0 / z, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(frame_rect_to_page(self.ctrl, page, b.bbox))
                elif b.invisible:
                    pen = QPen(warn, 0.8 / z, Qt.PenStyle.DotLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(frame_rect_to_page(self.ctrl, page, b.bbox))
                else:
                    faint = QColor(accent)
                    faint.setAlpha(70)
                    painter.setPen(QPen(faint, 0.8 / z, Qt.PenStyle.DotLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(frame_rect_to_page(self.ctrl, page, b.bbox))
        if self.hover and self.hover[0] == page and not self.ctrl.editor_for(view):
            b = self.hover[1]
            painter.setPen(QPen(accent if b.editable else danger, 1.5 / z))
            fill = QColor(accent)
            fill.setAlpha(18)
            painter.setBrush(fill)
            painter.drawRect(frame_rect_to_page(self.ctrl, page, b.bbox))
        if self.selected and self.selected[0] == page:
            b = self.selected[1]
            r = frame_rect_to_page(self.ctrl, page, b.bbox)
            painter.setPen(QPen(accent, 1.5 / z))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)
            hs = HANDLE_PX / z
            painter.setBrush(theme.color("surface"))
            for cx in (b.bbox.x0, b.bbox.x1):
                c = pymupdf.Point(cx, (b.bbox.y0 + b.bbox.y1) / 2) * self.ctrl.unframe(page)
                painter.drawRect(QRectF(c.x - hs / 2, c.y - hs / 2, hs, hs))
        d = self.drag
        if d is not None and d.target_page == page:
            r = pymupdf.Rect(d.block.bbox)
            dx, dy = d.current.x - d.start.x, d.current.y - d.start.y
            if d.kind == "move":
                r = pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
            elif d.kind == "resize-r":
                r.x1 = max(r.x0 + 2, r.x1 + dx)
            else:
                r.x0 = min(r.x1 - 2, r.x0 + dx)
            painter.setPen(QPen(accent, 1.2 / z, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(frame_rect_to_page(self.ctrl, page, r))
        for p_index, rect in self.ctrl.overflow_marks(view):
            if p_index == page:
                painter.setPen(QPen(warn, 1.5 / z, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(frame_rect_to_page(self.ctrl, page, rect))


class AddTextTool(Tool):
    """Clic: casella a larghezza automatica; trascinamento: casella a larghezza fissa (TXT-06)."""

    id = "text-add"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, controller):
        self.ctrl = controller
        self._start: tuple[int, pymupdf.Point, QPointF] | None = None
        self._current: pymupdf.Point | None = None
        self.sticky = False

    def deactivate(self, view) -> None:
        self.ctrl.commit_editor(view)
        self._start = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if self.ctrl.editor_for(view) is not None:
            self.ctrl.commit_editor(view)
            if not self.sticky:
                return True
        if ev.page is None:
            return True
        fpt = ev.point * self.ctrl.frame(ev.page)
        self._start = (ev.page, fpt, ev.pos)
        self._current = fpt
        return True

    def move(self, view, ev):
        if self._start is None or ev.page != self._start[0]:
            return False
        self._current = ev.point * self.ctrl.frame(ev.page)
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._start is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        page, start, pos = self._start
        self._start = None
        end = self._current or start
        dragged = abs(ev.pos.x() - pos.x()) > 6
        if dragged:
            x0, x1 = sorted((start.x, end.x))
            top = min(start.y, end.y)
            self.ctrl.open_new_text(view, page, pymupdf.Point(x0, top), width=max(x1 - x0, 4.0))
        else:
            self.ctrl.open_new_text(view, page, start, width=None)
        return True

    def paint(self, view, painter, page):
        if self._start is None or self._start[0] != page or self._current is None:
            return
        z = max(view.zoom * 1.333, 0.05)
        r = pymupdf.Rect(self._start[1], self._current)
        r.normalize()
        painter.setPen(QPen(theme.color("accent"), 1.2 / z, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame_rect_to_page(self.ctrl, page, r))
