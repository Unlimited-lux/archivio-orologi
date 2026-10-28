"""Strumenti interattivi della modalità Commenta.

Gli eventi arrivano già in coordinate pagina (punti, pagina non ruotata) e
`paint()` riceve un QPainter già trasformato: si disegna direttamente in punti.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip

from foliant.api import ENGINE_LOCK, SelectTextTool, Tool, _, selection_from_words, theme

from . import dialogs, engine

if TYPE_CHECKING:
    from .controller import Controller

HANDLE = 6.0   # lato delle maniglie in pixel
VERTEX_BASE = 100   # maniglie dei vertici: VERTEX_BASE + indice
VERTEX_KINDS = (engine.LINE, engine.ARROW, engine.POLYGON, engine.POLYLINE, engine.CLOUD, engine.DISTANCE,
                engine.PERIMETER, engine.AREA)


def qcolor(c, alpha: float = 1.0) -> QColor:
    if not c:
        return QColor(0, 0, 0, 0)
    q = QColor.fromRgbF(*engine._rgb(c))
    q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def _snap45(a: pymupdf.Point, b: pymupdf.Point) -> pymupdf.Point:
    d = b - a
    length = math.hypot(d.x, d.y)
    if length == 0:
        return b
    ang = round(math.atan2(d.y, d.x) / (math.pi / 4)) * (math.pi / 4)
    return pymupdf.Point(a.x + length * math.cos(ang), a.y + length * math.sin(ang))


def _shape_rect(a: pymupdf.Point, b: pymupdf.Point, square: bool, center: bool) -> pymupdf.Rect:
    dx, dy = b.x - a.x, b.y - a.y
    if square:
        s = max(abs(dx), abs(dy))
        dx = math.copysign(s, dx or 1)
        dy = math.copysign(s, dy or 1)
    if center:
        return pymupdf.Rect(a.x - abs(dx), a.y - abs(dy), a.x + abs(dx), a.y + abs(dy))
    return pymupdf.Rect(a, pymupdf.Point(a.x + dx, a.y + dy)).normalize()


def _pen(view, color: QColor, width_pt: float, dashed: bool = False) -> QPen:
    pen = QPen(color)
    pen.setWidthF(max(width_pt, 0.01))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    return pen


def _cosmetic(color: QColor, px: float = 1.0, dashed: bool = False) -> QPen:
    pen = QPen(color)
    pen.setCosmetic(True)
    pen.setWidthF(px)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    return pen


def _qrect(r: pymupdf.Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


class AnnotTool(Tool):
    """Base degli strumenti che creano annotazioni."""

    kind = ""
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, ctrl: "Controller"):
        self.ctrl = ctrl

    @property
    def id(self):  # type: ignore[override]
        return f"annot-{self.kind}"

    def style(self) -> engine.Style:
        return self.ctrl.style_for(self.kind)

    def activate(self, view):
        self.ctrl.set_tool_kind(self.kind)
        self.ctrl.ctx.show_properties("annotations")

    def deactivate(self, view):
        self.reset()

    def reset(self) -> None:
        pass

    def created(self, view, page: int, xref: int | None) -> None:
        if xref is None:
            return
        self.ctrl.reveal_after_create(view)
        self.ctrl.select([(page, xref)], view)
        if not self.ctrl.keep_tool:
            self.ctrl.ctx.set_tool(None)

    def key(self, view, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reset()
            view.viewport().update()
            self.ctrl.ctx.set_tool(SelectAnnotTool.shared(self.ctrl))
            return True
        return False


# ======================================================================== selezione
class SelectAnnotTool(SelectTextTool):
    """Seleziona, sposta, ridimensiona ed elimina annotazioni; altrove seleziona il testo."""

    id = "annot-select"
    _instance: "SelectAnnotTool | None" = None

    def __init__(self, ctrl: "Controller"):
        super().__init__()
        self.ctrl = ctrl
        self._drag: dict | None = None
        self._band: tuple[int, pymupdf.Point, pymupdf.Point] | None = None
        self._last_tip: tuple | None = None

    @classmethod
    def shared(cls, ctrl) -> "SelectAnnotTool":
        if cls._instance is None or cls._instance.ctrl is not ctrl:
            cls._instance = cls(ctrl)
        return cls._instance

    def activate(self, view):
        self.ctrl.set_tool_kind("")

    def _hit(self, view, ev) -> int | None:
        if ev.page is None:
            return None
        tol = 4.0 / max(view.zoom, 0.1)
        with ENGINE_LOCK:
            return engine.annot_at(view.document.pdf, ev.page, ev.point, tol)

    def _handle_at(self, view, ev) -> int | None:
        """Indice della maniglia (0..7) sotto il cursore per una selezione singola."""
        if len(self.ctrl.selection) != 1 or ev.page is None:
            return None
        page, xref = self.ctrl.selection[0]
        if page != ev.page:
            return None
        info = self.ctrl.info(page, xref, view=view)
        if info is None or info.locked or self.ctrl.is_text_anchored(info):
            return None
        tol = HANDLE / max(view.zoom * 96 / 72, 0.1)
        for i, v in enumerate(self.ctrl.vertices(page, xref, view=view)):
            if abs(v.x - ev.point.x) <= tol and abs(v.y - ev.point.y) <= tol:
                return VERTEX_BASE + i
        if info.kind in VERTEX_KINDS:
            return None
        for i, h in enumerate(_handles(info.rect)):
            if abs(h.x - ev.point.x) <= tol and abs(h.y - ev.point.y) <= tol:
                return i
        return None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if ev.page is None:
            self.ctrl.clear_selection()
            return super().press(view, ev)
        handle = self._handle_at(view, ev)
        if handle is not None:
            page, xref = self.ctrl.selection[0]
            info = self.ctrl.info(page, xref, view=view)
            self._drag = {"page": page, "start": ev.point, "handle": handle, "rect": info.rect,
                          "items": [(page, xref, info.rect)], "moved": False,
                          "ratio": info.kind == engine.STAMP}
            return True
        xref = self._hit(view, ev)
        multi = bool(ev.modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if xref is not None:
            item = (ev.page, xref)
            if multi:
                self.ctrl.toggle(item, view)
            elif item not in self.ctrl.selection:
                self.ctrl.select([item], view)
            view.clear_text_selection()
            items = []
            for page, x in self.ctrl.selection:
                if page != ev.page:
                    continue
                info = self.ctrl.info(page, x, view=view)
                if info is not None:
                    items.append((page, x, info.rect))
            self._drag = {"page": ev.page, "start": ev.point, "handle": None, "items": items, "moved": False}
            return True
        if not multi:
            self.ctrl.clear_selection()
        if view.word_at(ev.page, ev.point) is None and not multi:
            # zona senza testo: rettangolo di selezione
            self._band = (ev.page, ev.point, ev.point)
            view.clear_text_selection()
            return True
        if multi:
            self._band = (ev.page, ev.point, ev.point)
            return True
        return super().press(view, ev)

    def move(self, view, ev):
        if self._drag is not None and ev.buttons & Qt.MouseButton.LeftButton:
            hit = view.view_to_page(ev.pos, clamp=True)
            if hit is None:
                return True
            p = hit[1] if hit[0] == self._drag["page"] else view.clamp_to_page(self._drag["page"], ev.pos)
            d = p - self._drag["start"]
            if abs(d.x) + abs(d.y) > 1.0 / max(view.zoom, 0.1):
                self._drag["moved"] = True
            self._drag["point"] = p
            self._drag["shift"] = bool(ev.modifiers & Qt.KeyboardModifier.ShiftModifier)
            view.viewport().update()
            return True
        if self._band is not None and ev.buttons & Qt.MouseButton.LeftButton:
            page = self._band[0]
            p = view.clamp_to_page(page, ev.pos)
            self._band = (page, self._band[1], p)
            view.viewport().update()
            return True
        if not ev.buttons & Qt.MouseButton.LeftButton and ev.page is not None:
            self._hover(view, ev)
        return super().move(view, ev)

    def _hover(self, view, ev) -> None:
        if self._handle_at(view, ev) is not None:
            view.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
            return
        xref = self._hit(view, ev)
        if xref is None:
            if self._last_tip is not None:
                QToolTip.hideText()
                self._last_tip = None
            return
        view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        key = (ev.page, xref)
        if key != self._last_tip:
            self._last_tip = key
            info = self.ctrl.info(ev.page, xref, view=view)
            if info is not None:
                text = info.contents.strip()
                tip = f"<b>{_(info.label)}</b> — {info.author or _('(autore sconosciuto)')}"
                if text:
                    from html import escape
                    tip += "<br>" + escape(text[:400]).replace("\n", "<br>")
                ge = getattr(ev.event, "globalPosition", None)
                if ge is not None:
                    QToolTip.showText(ge().toPoint(), tip, view)

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if self._drag is not None:
            drag, self._drag = self._drag, None
            if drag["moved"] and "point" in drag:
                d = drag["point"] - drag["start"]
                if drag["handle"] is not None and drag["handle"] >= VERTEX_BASE:
                    page, xref, _r = drag["items"][0]
                    idx = drag["handle"] - VERTEX_BASE
                    verts = self.ctrl.vertices(page, xref, view=view)
                    if idx < len(verts):
                        self.ctrl.move_vertex(page, xref, idx, verts[idx] + d)
                elif drag["handle"] is None:
                    self.ctrl.move(d.x, d.y, [(p, x) for p, x, _r in drag["items"]])
                else:
                    page, xref, rect = drag["items"][0]
                    new = _resized(rect, drag["handle"], d, keep_ratio=drag.get("ratio") or drag.get("shift"))
                    if new.width >= 2 and new.height >= 2:
                        self.ctrl.resize(page, xref, new)
            view.viewport().update()
            return True
        if self._band is not None:
            page, a, b = self._band
            self._band = None
            r = pymupdf.Rect(a, b).normalize()
            if r.width > 2 or r.height > 2:
                with ENGINE_LOCK:
                    infos = engine.page_annotations(view.document.pdf, page)
                found = [(page, i.xref) for i in infos if not i.hidden and not i.irt and not i.is_state
                         and r.intersects(i.rect)]
                keep = list(self.ctrl.selection) if ev.modifiers & (
                    Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier) else []
                self.ctrl.select(keep + found, view)
            view.viewport().update()
            return True
        return super().release(view, ev)

    def double_click(self, view, ev):
        if ev.page is not None:
            xref = self._hit(view, ev)
            if xref is not None:
                self.ctrl.select([(ev.page, xref)], view)
                info = self.ctrl.info(ev.page, xref, view=view)
                if info is not None and info.kind == engine.ATTACHMENT:
                    from . import ui_actions
                    ui_actions.save_attachment(self.ctrl, ev.page, xref)
                    return True
                self.ctrl.edit_text(ev.page, xref, view=view)
                return True
        return super().double_click(view, ev)

    def key(self, view, event):
        key = event.key()
        ctrl_mod = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl_mod and key == Qt.Key.Key_V:
            if self.ctrl.clipboard:
                self.ctrl.paste()
            else:
                self.ctrl.paste_system()
            return True
        if not self.ctrl.selection:
            return False
        if ctrl_mod and key in (Qt.Key.Key_C, Qt.Key.Key_X):
            return self.ctrl.copy(cut=key == Qt.Key.Key_X)
        if ctrl_mod and key == Qt.Key.Key_D:
            self.ctrl.duplicate()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.ctrl.delete()
            return True
        if key == Qt.Key.Key_Escape:
            self.ctrl.clear_selection()
            return True
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if key in moves:
            self.ctrl.move(*moves[key])
            return True
        return False

    def paint(self, view, painter, page):
        accent = theme.color("accent")
        if self._band is not None and self._band[0] == page:
            r = pymupdf.Rect(self._band[1], self._band[2]).normalize()
            fill = QColor(accent)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.setPen(_cosmetic(accent, 1, dashed=True))
            painter.drawRect(_qrect(r))
        if self._drag is not None and self._drag.get("moved") and self._drag["page"] == page and "point" in self._drag:
            d = self._drag["point"] - self._drag["start"]
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_cosmetic(accent, 1.5, dashed=True))
            if self._drag["handle"] is not None and self._drag["handle"] >= VERTEX_BASE:
                page_, xref_, _r = self._drag["items"][0]
                verts = self.ctrl.vertices(page_, xref_, view=view)
                idx = self._drag["handle"] - VERTEX_BASE
                if idx < len(verts):
                    verts[idx] = verts[idx] + d
                    painter.drawPolyline(QPolygonF([QPointF(v.x, v.y) for v in verts]))
                return
            for _p, _x, rect in self._drag["items"]:
                if self._drag["handle"] is None:
                    r = rect + (d.x, d.y, d.x, d.y)
                else:
                    r = _resized(rect, self._drag["handle"], d,
                                 keep_ratio=self._drag.get("ratio") or self._drag.get("shift"))
                painter.drawRect(_qrect(r))


def _handles(r: pymupdf.Rect) -> list[pymupdf.Point]:
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    return [pymupdf.Point(r.x0, r.y0), pymupdf.Point(cx, r.y0), pymupdf.Point(r.x1, r.y0),
            pymupdf.Point(r.x1, cy), pymupdf.Point(r.x1, r.y1), pymupdf.Point(cx, r.y1),
            pymupdf.Point(r.x0, r.y1), pymupdf.Point(r.x0, cy)]


def _resized(r: pymupdf.Rect, handle: int, d: pymupdf.Point, keep_ratio: bool = False) -> pymupdf.Rect:
    x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
    if handle in (0, 6, 7):
        x0 += d.x
    if handle in (2, 3, 4):
        x1 += d.x
    if handle in (0, 1, 2):
        y0 += d.y
    if handle in (4, 5, 6):
        y1 += d.y
    new = pymupdf.Rect(x0, y0, x1, y1).normalize()
    if keep_ratio and r.height > 0 and new.height > 0:
        ratio = r.width / r.height
        if handle in (1, 5):
            w = new.height * ratio
            new = pymupdf.Rect(new.x0, new.y0, new.x0 + w, new.y1)
        else:
            h = new.width / ratio
            if handle in (0, 1, 2):
                new = pymupdf.Rect(new.x0, new.y1 - h, new.x1, new.y1)
            else:
                new = pymupdf.Rect(new.x0, new.y0, new.x1, new.y0 + h)
    return new


def paint_popups(ctrl: "Controller", view, painter: QPainter, page: int) -> None:
    """Livello di disegno: finestre popup aperte (ANN-05), solo nella vista."""
    if ctrl.annotations_hidden(view):
        return
    try:
        popups = ctrl.popups(view, page)
    except Exception:
        return
    if not popups:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for rect, author, date, text in popups:
        r = _qrect(rect)
        painter.setPen(_cosmetic(QColor(160, 130, 20), 1))
        painter.setBrush(QColor(255, 248, 196))
        painter.drawRect(r)
        f = painter.font()
        f.setPointSizeF(7.5)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(60, 50, 10))
        head = QRectF(r.x() + 4, r.y() + 2, r.width() - 8, 12)
        painter.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{author} · {date}" if date else author)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(30, 30, 30))
        body = QRectF(r.x() + 4, r.y() + 16, r.width() - 8, r.height() - 18)
        painter.drawText(body, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap),
                         text)


def paint_selection(ctrl: "Controller", view, painter: QPainter, page: int) -> None:
    """Livello di disegno: riquadro e maniglie delle annotazioni selezionate."""
    if ctrl.selection_view() is not view or not ctrl.selection:
        return
    accent = theme.color("accent")
    scale = view.zoom * 96 / 72
    hs = HANDLE / max(scale, 0.01)
    single = len(ctrl.selection) == 1
    for p, xref in ctrl.selection:
        if p != page:
            continue
        info = ctrl.info(p, xref, view=view)
        if info is None:
            continue
        r = info.rect
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_cosmetic(accent, 1.5, dashed=info.locked))
        painter.drawRect(_qrect(r + (-1, -1, 1, 1)))
        if single and not ctrl.is_text_anchored(info):
            grey = theme.color("text_muted")
            painter.setPen(_cosmetic(grey if info.locked else accent, 1))
            painter.setBrush(theme.color("surface"))
            for v in ctrl.vertices(p, xref, view=view):
                painter.drawEllipse(QPointF(v.x, v.y), hs * 0.6, hs * 0.6)
            if info.kind not in VERTEX_KINDS:
                for h in _handles(r):
                    painter.drawRect(QRectF(h.x - hs / 2, h.y - hs / 2, hs, hs))


# ======================================================================== markup su testo
class TextMarkupTool(SelectTextTool):
    """Evidenzia, sottolinea, barra o ondula il testo trascinando su di esso.

    Su pagine senza testo (scansioni) l'evidenziatore traccia un'area (ANN-11)."""

    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, ctrl: "Controller", kind: str):
        super().__init__()
        self.ctrl = ctrl
        self.kind = kind
        self._area: tuple[int, pymupdf.Point, pymupdf.Point] | None = None
        self.area_mode = False

    @property
    def id(self):  # type: ignore[override]
        return f"annot-{self.kind}" + ("-area" if self.area_mode else "")

    def activate(self, view):
        self.ctrl.set_tool_kind(self.kind)
        self.ctrl.ctx.show_properties("annotations")
        sel = view.text_selection
        if sel is not None and sel.rects and not self.area_mode:
            self.apply_selection(view)

    def apply_selection(self, view) -> int | None:
        sel = view.text_selection
        if sel is None or not sel.rects:
            return None
        quads = engine.quads_from_words(sel.words) if sel.words else sel.quads
        label = {engine.HIGHLIGHT: _("Evidenzia"), engine.UNDERLINE: _("Sottolinea"),
                 engine.STRIKEOUT: _("Barra"), engine.SQUIGGLY: _("Ondulato")}[self.kind]
        xref = self.ctrl.apply(label, engine.create_markup, sel.page, self.kind, quads,
                               self.ctrl.style_for(self.kind), self.ctrl.author())
        view.clear_text_selection()
        if xref is not None:
            self.ctrl.reveal_after_create(view)
            self.ctrl.select([(sel.page, xref)], view)
        return xref

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if ev.page is not None and (self.area_mode or ev.modifiers & Qt.KeyboardModifier.AltModifier
                                    or not view.words(ev.page)):
            if self.kind != engine.HIGHLIGHT:
                self.ctrl.ctx.status(_("La pagina non contiene testo: esegui l'OCR per poterlo marcare."))
                return True
            self._area = (ev.page, ev.point, ev.point)
            return True
        return super().press(view, ev)

    def move(self, view, ev):
        if self._area is not None:
            if ev.buttons & Qt.MouseButton.LeftButton:
                page = self._area[0]
                self._area = (page, self._area[1], view.clamp_to_page(page, ev.pos))
                view.viewport().update()
            return True
        return super().move(view, ev)

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if self._area is not None:
            page, a, b = self._area
            self._area = None
            r = pymupdf.Rect(a, b).normalize()
            if r.width < 2 or r.height < 2:
                view.viewport().update()
                return True
            if not view.words(page) and not self.area_mode:
                self.ctrl.ctx.status(_("Pagina senza testo: creata un'evidenziazione ad area. "
                                       "Per evidenziare il testo esegui prima l'OCR."))
            xref = self.ctrl.apply(_("Evidenzia area"), engine.create_markup, page, engine.HIGHLIGHT, [r.quad],
                                   self.ctrl.style_for(engine.HIGHLIGHT), self.ctrl.author(), "", True)
            if xref is not None:
                self.ctrl.select([(page, xref)], view)
            return True
        anchor_moved = self._moved
        handled = super().release(view, ev)
        if anchor_moved and view.text_selection is not None:
            self.apply_selection(view)
        return handled

    def double_click(self, view, ev):
        if super().double_click(view, ev):
            self.apply_selection(view)
            return True
        return False

    def paint(self, view, painter, page):
        if self._area is not None and self._area[0] == page:
            r = pymupdf.Rect(self._area[1], self._area[2]).normalize()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(self.ctrl.style_for(engine.HIGHLIGHT).color, 0.4))
            painter.drawRect(_qrect(r))

    def key(self, view, event):
        if event.key() == Qt.Key.Key_Escape:
            self._area = None
            self.ctrl.ctx.set_tool(SelectAnnotTool.shared(self.ctrl))
            return True
        return False


# ======================================================================== note e testo
class NoteTool(AnnotTool):
    kind = engine.NOTE

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton and ev.page is not None

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        res = dialogs.edit_comment(view, _("Nota"), self.ctrl.author(), "")
        if res is None:
            return True
        text, rich = res
        page, point = ev.page, ev.point

        def run(pdf):
            xref = engine.create_note(pdf, page, point, text, self.style(), self.ctrl.author())
            if rich:
                engine.set_contents(pdf, page, xref, text, rich)
            return xref

        self.created(view, page, self.ctrl.apply(_("Aggiungi nota"), run))
        return True


class _DragTool(AnnotTool):
    """Strumento con trascinamento da un punto a un altro sulla stessa pagina."""

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self._start: tuple[int, pymupdf.Point] | None = None
        self._end: pymupdf.Point | None = None
        self._mods = Qt.KeyboardModifier.NoModifier

    def reset(self):
        self._start = None
        self._end = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self._start = (ev.page, ev.point)
        self._end = ev.point
        self._mods = ev.modifiers
        return True

    def move(self, view, ev):
        if self._start is None:
            return False
        self._end = view.clamp_to_page(self._start[0], ev.pos)
        self._mods = ev.modifiers
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._start is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        page, a = self._start
        b = view.clamp_to_page(page, ev.pos)
        mods = ev.modifiers
        self.reset()
        view.viewport().update()
        self.finish(view, page, a, b, mods)
        return True

    def finish(self, view, page, a, b, mods) -> None:
        raise NotImplementedError


class TextBoxTool(_DragTool):
    kind = engine.FREETEXT

    def finish(self, view, page, a, b, mods):
        r = pymupdf.Rect(a, b).normalize()
        st = self.style()
        if r.width < 4 or r.height < 4:
            r = pymupdf.Rect(a.x, a.y, a.x + 200, a.y + max(30, st.fontsize * 2.4))
        res = dialogs.edit_comment(view, _("Casella di testo"), self.ctrl.author(), "")
        if res is None or not res[0].strip():
            return
        text, rich = res

        def run(pdf):
            xref = engine.create_freetext(pdf, page, r, text, st, self.ctrl.author())
            if rich:
                engine.set_contents(pdf, page, xref, text, rich)
            return xref

        self.created(view, page, self.ctrl.apply(_("Aggiungi casella di testo"), run))

    def paint(self, view, painter, page):
        if self._start and self._start[0] == page and self._end is not None:
            st = self.style()
            painter.setPen(_pen(view, qcolor(st.color or (0, 0, 0)), max(st.width, 0.5)))
            painter.setBrush(qcolor(st.fill, st.opacity) if st.fill else Qt.BrushStyle.NoBrush)
            painter.drawRect(_qrect(pymupdf.Rect(self._start[1], self._end).normalize()))


class CalloutTool(_DragTool):
    """Clic sul punto da indicare, trascinamento fino alla posizione della casella."""

    kind = engine.CALLOUT

    def finish(self, view, page, a, b, mods):
        st = self.style()
        if abs(b - a) < 8:
            b = a + (60, -60)
        w, h = 180.0, max(30.0, st.fontsize * 2.4)
        box = pymupdf.Rect(b.x, b.y - h / 2, b.x + w, b.y + h / 2)
        if b.x < a.x:
            box = pymupdf.Rect(b.x - w, b.y - h / 2, b.x, b.y + h / 2)
        res = dialogs.edit_comment(view, _("Richiamo"), self.ctrl.author(), "")
        if res is None or not res[0].strip():
            return
        knee = pymupdf.Point((a.x + b.x) / 2, b.y)
        attach = pymupdf.Point(box.x0 if b.x >= a.x else box.x1, (box.y0 + box.y1) / 2)
        self.created(view, page, self.ctrl.apply(_("Aggiungi richiamo"), engine.create_freetext, page, box, res[0],
                                                 st, self.ctrl.author(), callout=[a, knee, attach]))

    def paint(self, view, painter, page):
        if self._start and self._start[0] == page and self._end is not None:
            st = self.style()
            painter.setPen(_pen(view, qcolor(st.color or (0, 0, 0)), max(st.width, 0.5)))
            painter.drawLine(QPointF(self._start[1].x, self._start[1].y), QPointF(self._end.x, self._end.y))


# ======================================================================== forme
class ShapeTool(_DragTool):
    """Rettangolo, ellisse, linea e freccia (trascinamento); Maiusc vincola, Alt dal centro."""

    def __init__(self, ctrl, kind: str):
        super().__init__(ctrl)
        self.kind = kind

    def _geometry(self, a, b, mods):
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if self.kind in (engine.LINE, engine.ARROW, engine.DISTANCE):
            return [a, _snap45(a, b) if shift else b]
        r = _shape_rect(a, b, shift, bool(mods & Qt.KeyboardModifier.AltModifier))
        return [r.tl, r.br]

    def finish(self, view, page, a, b, mods):
        pts = self._geometry(a, b, mods)
        if abs(pts[1] - pts[0]) < 2 or (self.kind in (engine.RECT, engine.ELLIPSE) and
                                         (abs(pts[1].x - pts[0].x) < 2 or abs(pts[1].y - pts[0].y) < 2)):
            return   # clic senza trascinamento: nessuna forma
        if self.kind == engine.DISTANCE:
            xref = self.ctrl.apply(_("Misura distanza"), engine.create_measure, page, self.kind, pts,
                                   self.style(), self.ctrl.scale_for(page), self.ctrl.author())
        else:
            label = {engine.RECT: _("Aggiungi rettangolo"), engine.ELLIPSE: _("Aggiungi ellisse"),
                     engine.LINE: _("Aggiungi linea"), engine.ARROW: _("Aggiungi freccia")}[self.kind]
            xref = self.ctrl.apply(label, engine.create_shape, page, self.kind, pts, self.style(), self.ctrl.author())
        self.created(view, page, xref)

    def paint(self, view, painter, page):
        if not (self._start and self._start[0] == page and self._end is not None):
            return
        st = self.style()
        pts = self._geometry(self._start[1], self._end, self._mods)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(_pen(view, qcolor(st.color, st.opacity), st.width, st.dash != "solid"))
        painter.setBrush(qcolor(st.fill, st.opacity) if st.fill else Qt.BrushStyle.NoBrush)
        a, b = pts
        if self.kind in (engine.LINE, engine.ARROW, engine.DISTANCE):
            painter.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))
            if self.kind == engine.DISTANCE:
                _paint_label(view, painter, b, engine.measure_value(engine.DISTANCE, [a, b],
                                                                    self.ctrl.scale_for(page)))
        elif self.kind == engine.RECT:
            painter.drawRect(_qrect(pymupdf.Rect(a, b)))
        else:
            painter.drawEllipse(_qrect(pymupdf.Rect(a, b)))


def _paint_label(view, painter: QPainter, at: pymupdf.Point, text: str) -> None:
    painter.save()
    f = painter.font()
    f.setPointSizeF(9 / max(view.zoom, 0.1) * 0.75)
    painter.setFont(f)
    fm = painter.fontMetrics()
    w = fm.horizontalAdvance(text) + 6 / max(view.zoom, 0.1)
    h = fm.height()
    r = QRectF(at.x + 6, at.y + 6, w, h)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.color("surface"))
    painter.drawRect(r)
    painter.setPen(theme.color("text"))
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


class PolyTool(AnnotTool):
    """Poligono, polilinea, nuvola, perimetro e area: clic per i vertici; doppio clic,
    Invio o clic sul primo vertice per terminare; Backspace toglie l'ultimo; Esc annulla."""

    def __init__(self, ctrl, kind: str):
        super().__init__(ctrl)
        self.kind = kind
        self.points: list[pymupdf.Point] = []
        self.page: int | None = None
        self._hover: pymupdf.Point | None = None

    def reset(self):
        self.points = []
        self.page = None
        self._hover = None

    @property
    def closed(self) -> bool:
        return self.kind in (engine.POLYGON, engine.CLOUD, engine.AREA)

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        if self.page is not None and ev.page != self.page:
            return True
        p = ev.point
        if self.points and ev.modifiers & Qt.KeyboardModifier.ShiftModifier:
            p = _snap45(self.points[-1], p)
        if len(self.points) >= 3 and self.closed:
            tol = 6 / max(view.zoom, 0.1)
            if abs(p - self.points[0]) <= tol:
                self.finish(view)
                return True
        if self.points and abs(p - self.points[-1]) < 0.5:
            return True   # vertici coincidenti: fusi
        self.page = ev.page
        self.points.append(p)
        view.viewport().update()
        return True

    def move(self, view, ev):
        if self.page is not None:
            p = view.clamp_to_page(self.page, ev.pos)
            if self.points and ev.modifiers & Qt.KeyboardModifier.ShiftModifier:
                p = _snap45(self.points[-1], p)
            self._hover = p
            view.viewport().update()
            return True
        return False

    def double_click(self, view, ev):
        if self.page is not None:
            self.finish(view)
            return True
        return False

    def key(self, view, event):
        k = event.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.page is not None:
            self.finish(view)
            return True
        if k == Qt.Key.Key_Backspace and self.points:
            self.points.pop()
            if not self.points:
                self.reset()
            view.viewport().update()
            return True
        if k == Qt.Key.Key_Escape and self.page is not None:
            self.reset()
            view.viewport().update()
            return True
        return super().key(view, event)

    def finish(self, view) -> None:
        pts, page = list(self.points), self.page
        self.reset()
        view.viewport().update()
        need = 3 if self.closed else 2
        if page is None or len(pts) < need:
            self.ctrl.ctx.status(_("Servono almeno {n} punti.").format(n=need))
            return
        if self.kind in engine.MEASURE_KINDS:
            xref = self.ctrl.apply(_("Aggiungi misura"), engine.create_measure, page, self.kind, pts, self.style(),
                                   self.ctrl.scale_for(page), self.ctrl.author())
        else:
            label = {engine.POLYGON: _("Aggiungi poligono"), engine.POLYLINE: _("Aggiungi polilinea"),
                     engine.CLOUD: _("Aggiungi nuvola")}[self.kind]
            xref = self.ctrl.apply(label, engine.create_shape, page, self.kind, pts, self.style(), self.ctrl.author())
        self.created(view, page, xref)

    def paint(self, view, painter, page):
        if self.page != page or not self.points:
            return
        st = self.style()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(_pen(view, qcolor(st.color, st.opacity), st.width))
        pts = self.points + ([self._hover] if self._hover is not None else [])
        poly = QPolygonF([QPointF(p.x, p.y) for p in pts])
        if self.closed:
            painter.setBrush(qcolor(st.fill, st.opacity) if st.fill else Qt.BrushStyle.NoBrush)
            painter.drawPolygon(poly)
        else:
            painter.drawPolyline(poly)
        hs = 4 / max(view.zoom, 0.1)
        painter.setPen(_cosmetic(theme.color("accent")))
        painter.setBrush(theme.color("surface"))
        for p in self.points:
            painter.drawRect(QRectF(p.x - hs / 2, p.y - hs / 2, hs, hs))
        if self.kind in engine.MEASURE_KINDS and len(pts) >= 2:
            try:
                _paint_label(view, painter, pts[-1], engine.measure_value(self.kind, pts, self.ctrl.scale_for(page)))
            except engine.AnnotationError:
                pass


# ======================================================================== disegno
class InkTool(AnnotTool):
    """Matita (e pennarello evidenziatore). I tratti consecutivi entro `merge_seconds`
    sulla stessa pagina formano un'unica annotazione."""

    merge_seconds = 2.0

    def __init__(self, ctrl, marker: bool = False):
        super().__init__(ctrl)
        self.marker = marker
        self.kind = "marker" if marker else engine.INK
        self._stroke: list[pymupdf.Point] = []
        self._page: int | None = None
        self._last: tuple[int, int, float] | None = None   # (pagina, xref, istante)

    def reset(self):
        self._stroke = []
        self._page = None

    def deactivate(self, view):
        super().deactivate(view)
        self._last = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self._page = ev.page
        self._stroke = [ev.point]
        return True

    def move(self, view, ev):
        if self._page is None or not ev.buttons & Qt.MouseButton.LeftButton:
            return False
        self._stroke.append(view.clamp_to_page(self._page, ev.pos))
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._page is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        page, stroke = self._page, list(self._stroke)
        self.reset()
        self.add_stroke(view, page, stroke)
        view.viewport().update()
        return True

    def add_stroke(self, view, page: int, stroke: list) -> int | None:
        now = time.monotonic()
        last = self._last
        if last is not None and last[0] == page and now - last[2] <= self.merge_seconds and \
                self.ctrl.info(page, last[1], view=view) is not None:
            ok = self.ctrl.apply(_("Disegno a mano libera"), engine.add_ink_strokes, page, last[1], [stroke])
            xref = last[1] if ok is not False else None
        else:
            xref = self.ctrl.apply(_("Disegno a mano libera"), engine.create_ink, page, [stroke], self.style(),
                                   self.ctrl.author(), self.marker)
        if xref is not None:
            self._last = (page, xref, time.monotonic())
        return xref

    def paint(self, view, painter, page):
        if self._page != page or len(self._stroke) < 1:
            return
        st = self.style()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(_pen(view, qcolor(st.color, st.opacity), st.width))
        painter.drawPolyline(QPolygonF([QPointF(p.x, p.y) for p in self._stroke]))


class EraserTool(AnnotTool):
    """Gomma per i disegni a mano libera: parziale o a tratto intero; ogni passata è un passo."""

    kind = "eraser"

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.radius = 6.0
        self.whole = False
        self._path: list[pymupdf.Point] = []
        self._page: int | None = None
        self._hover: tuple[int, pymupdf.Point] | None = None

    def reset(self):
        self._path = []
        self._page = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self._page = ev.page
        self._path = [ev.point]
        return True

    def move(self, view, ev):
        if ev.page is not None:
            self._hover = (ev.page, ev.point)
        if self._page is not None and ev.buttons & Qt.MouseButton.LeftButton:
            self._path.append(view.clamp_to_page(self._page, ev.pos))
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._page is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        page, path = self._page, engine._densify(list(self._path), max(0.5, self.radius / 2))
        self.reset()
        self.erase(view, page, [p for p, _o in path])
        view.viewport().update()
        return True

    def erase(self, view, page: int, path: list) -> int:
        def run(pdf):
            n = 0
            for p in path:
                n += engine.erase_ink(pdf, page, p, self.radius, self.whole)
            if not n:
                raise _NothingToDo()
            return n

        with ENGINE_LOCK:
            pg = view.document.pdf[page]
            has_ink = any(True for _a in pg.annots(types=[pymupdf.PDF_ANNOT_INK]))
        if not has_ink:
            return 0
        try:
            with view.document.change(_("Gomma")) as pdf:
                return run(pdf)
        except _NothingToDo:
            return 0     # nessun tratto toccato: nessun passo di annullamento

    def paint(self, view, painter, page):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(_cosmetic(theme.color("text_muted"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._page == page and self._path:
            path = QPainterPath()
            for p in self._path:
                path.addEllipse(QPointF(p.x, p.y), self.radius, self.radius)
            fill = theme.color("accent")
            fill.setAlpha(60)
            painter.fillPath(path, fill)
        if self._hover is not None and self._hover[0] == page:
            p = self._hover[1]
            painter.drawEllipse(QPointF(p.x, p.y), self.radius, self.radius)


class _NothingToDo(Exception):
    pass


# ======================================================================== timbri, allegati
class StampTool(AnnotTool):
    """Appone un timbro (testo con stile proprio o immagine) con un clic."""

    kind = engine.STAMP

    def __init__(self, ctrl, spec: engine.StampSpec | None = None, image: bytes | None = None,
                 template: str = "", name: str = ""):
        super().__init__(ctrl)
        self.spec = spec
        self.image = image
        self.template = template
        self.name = name
        self.sticker = ""

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton and ev.page is not None

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        page, point = ev.page, ev.point
        if getattr(self, "sticker", None):
            xref = self.ctrl.apply(_("Aggiungi adesivo"), engine.create_sticker, page, point, self.sticker,
                                   self.style(), self.ctrl.author())
        elif self.image is not None:
            xref = self.ctrl.apply(_("Aggiungi timbro"), engine.create_image_stamp, page, point, self.image,
                                   self.style(), self.ctrl.author(), 160.0, self.name)
        else:
            spec = self.spec or engine.StampSpec(_("Approvato"), name="Approved")
            if self.template:
                filename = view.document.display_name
                counter = self.ctrl.next_counter(self.template) if "{contatore}" in self.template else None
                text = engine.fill_template(self.template, author=self.ctrl.author(), filename=filename, page=page,
                                            counter=counter)
                spec = engine.StampSpec(text, spec.color, "", spec.name)
            xref = self.ctrl.apply(_("Aggiungi timbro"), engine.create_stamp, page, point, spec, self.style(),
                                   self.ctrl.author())
        self.created(view, page, xref)
        return True


class AttachmentTool(AnnotTool):
    kind = engine.ATTACHMENT

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton and ev.page is not None

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        path = dialogs.ask_open_file(view, _("Allega file"))
        if not path:
            return True
        import os
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.ctrl.ctx.error(_("Allega file"), _("Impossibile leggere il file: {e}").format(e=e.strerror))
            return True
        if len(data) > 100 * 1024 * 1024 and not self.ctrl.ctx.confirm(
                _("Allega file"), _("Il file supera 100 MB e aumenterà molto la dimensione del documento. Continuare?")):
            return True
        name = os.path.basename(path)
        xref = self.ctrl.apply(_("Allega file"), engine.create_attachment, ev.page, ev.point, data, name,
                               self.style(), self.ctrl.author())
        self.created(view, ev.page, xref)
        return True


def text_selection_on(view, page: int, words) -> None:
    view.set_text_selection(selection_from_words(page, words))


# ======================================================================== testo e correzioni
class TypewriterTool(AnnotTool):
    """Commento di testo sulla pagina, senza bordo né sfondo (ANN-15)."""

    kind = engine.TYPEWRITER

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton and ev.page is not None

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        text = dialogs.ask_text(view, _("Commento di testo"), _("Testo:"), "", multiline=True)
        if text is None or not text.strip():
            return True   # testo vuoto: nessuna annotazione
        st = self.ctrl.style_for(engine.FREETEXT)
        st = engine.Style(fontsize=st.fontsize, text_color=st.text_color or (0, 0, 0), opacity=st.opacity)
        self.created(view, ev.page, self.ctrl.apply(_("Aggiungi commento di testo"), engine.create_typewriter,
                                                    ev.page, ev.point, text, st, self.ctrl.author()))
        return True


class CaretTool(AnnotTool):
    """Inserisci testo: clic tra due caratteri e testo da aggiungere (ANN-13)."""

    kind = engine.CARET

    def __init__(self, ctrl, paragraph: bool = False):
        super().__init__(ctrl)
        self.paragraph = paragraph

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton and ev.page is not None

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        point, height = ev.point, 12.0
        word = view.word_at(ev.page, ev.point)
        if word is not None:
            r = pymupdf.Rect(word[:4])
            height = r.height
            point = pymupdf.Point(ev.point.x, (r.y0 + r.y1) / 2)
        text = dialogs.ask_text(view, _("Inserisci testo"), _("Testo da inserire:"), "", multiline=True)
        if text is None:
            return True
        self.created(view, ev.page, self.ctrl.apply(_("Inserisci testo"), engine.create_caret, ev.page, point,
                                                    text, self.style(), self.ctrl.author(), self.paragraph, height))
        return True


class ReplaceTool(TextMarkupTool):
    """Sostituisci testo: selezione del testo + testo sostitutivo (ANN-13)."""

    def __init__(self, ctrl):
        super().__init__(ctrl, engine.STRIKEOUT)
        self.kind = engine.REPLACE

    def activate(self, view):
        self.ctrl.set_tool_kind(engine.REPLACE)
        sel = view.text_selection
        if sel is not None and sel.rects:
            self.apply_selection(view)

    def apply_selection(self, view):
        sel = view.text_selection
        if sel is None or not sel.rects:
            return None
        text = dialogs.ask_text(view, _("Sostituisci testo"), _("Testo sostitutivo:"), "", multiline=True)
        if text is None:
            view.clear_text_selection()
            return None
        quads = engine.quads_from_words(sel.words) if sel.words else sel.quads
        xref = self.ctrl.apply(_("Sostituisci testo"), engine.create_replacement, sel.page, quads, text,
                               self.ctrl.style_for(engine.STRIKEOUT), self.ctrl.author())
        view.clear_text_selection()
        if xref is not None:
            self.ctrl.select([(sel.page, xref)], view)
        return xref


class CalibrateTool(_DragTool):
    """Calibrazione della scala: segmento di lunghezza nota (ANN-30)."""

    kind = "calibrate"

    def finish(self, view, page, a, b, mods):
        if mods & Qt.KeyboardModifier.ShiftModifier:
            b = _snap45(a, b)
        length = abs(b - a)
        if length < 1:
            return
        scale = dialogs.ask_scale(view, self.ctrl.scale, calibrate_points=length)
        if scale is None:
            return
        self.ctrl.set_scale(scale, page)
        self.ctrl.ctx.set_tool(SelectAnnotTool.shared(self.ctrl))

    def paint(self, view, painter, page):
        if self._start and self._start[0] == page and self._end is not None:
            painter.setPen(_cosmetic(theme.color("accent"), 2, dashed=True))
            painter.drawLine(QPointF(self._start[1].x, self._start[1].y), QPointF(self._end.x, self._end.y))
