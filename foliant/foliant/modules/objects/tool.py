"""Strumenti interattivi: selezione e trasformazione degli oggetti, collegamenti."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF

from foliant.api import ENGINE_LOCK, Tool, _, theme

from . import engine as E
from .content import FORM, IMAGE, PATH, SHADING, rcenter

if TYPE_CHECKING:
    from .controller import ObjectsController

HANDLE_PX = 4.5
ROTATE_PX = 22


def view_scale(view, page: int) -> float:
    t = view.page_transform(page)
    return math.sqrt(abs(t.m11() * t.m22() - t.m12() * t.m21())) or 1.0


def kind_color(kind: str) -> QColor:
    name = {IMAGE: "accent", PATH: "secondary", FORM: "success", SHADING: "warning"}.get(kind, "accent")
    return theme.color(name)


def qpoly(q: pymupdf.Quad) -> QPolygonF:
    return QPolygonF([QPointF(q.ul.x, q.ul.y), QPointF(q.ur.x, q.ur.y), QPointF(q.lr.x, q.lr.y), QPointF(q.ll.x, q.ll.y)])


def qrect(r: pymupdf.Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


def handle_points(r: pymupdf.Rect) -> list[pymupdf.Point]:
    """8 maniglie: 0 alto-sx, 1 alto, 2 alto-dx, 3 dx, 4 basso-dx, 5 basso, 6 basso-sx, 7 sx."""
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    return [pymupdf.Point(r.x0, r.y0), pymupdf.Point(cx, r.y0), pymupdf.Point(r.x1, r.y0),
            pymupdf.Point(r.x1, cy), pymupdf.Point(r.x1, r.y1), pymupdf.Point(cx, r.y1),
            pymupdf.Point(r.x0, r.y1), pymupdf.Point(r.x0, cy)]


def resized_rect(start: pymupdf.Rect, handle: int, p: pymupdf.Point, proportional: bool,
                 from_center: bool) -> pymupdf.Rect:
    """Nuovo riquadro trascinando la maniglia `handle` fino a `p`."""
    x0, y0, x1, y1 = start.x0, start.y0, start.x1, start.y1
    w0, h0 = start.width, start.height
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = handle in (0, 6, 7)
    right = handle in (2, 3, 4)
    top = handle in (0, 1, 2)
    bottom = handle in (4, 5, 6)
    nx0, ny0, nx1, ny1 = x0, y0, x1, y1
    if left:
        nx0 = min(p.x, x1 - E.MIN_SIZE) if not from_center else min(p.x, cx - E.MIN_SIZE / 2)
    if right:
        nx1 = max(p.x, x0 + E.MIN_SIZE) if not from_center else max(p.x, cx + E.MIN_SIZE / 2)
    if top:
        ny0 = min(p.y, y1 - E.MIN_SIZE) if not from_center else min(p.y, cy - E.MIN_SIZE / 2)
    if bottom:
        ny1 = max(p.y, y0 + E.MIN_SIZE) if not from_center else max(p.y, cy + E.MIN_SIZE / 2)
    if from_center:
        if left:
            nx1 = cx + (cx - nx0)
        if right:
            nx0 = cx - (nx1 - cx)
        if top:
            ny1 = cy + (cy - ny0)
        if bottom:
            ny0 = cy - (ny1 - cy)
    corner = handle in (0, 2, 4, 6)
    if proportional and corner and w0 > 0 and h0 > 0:
        s = max((nx1 - nx0) / w0, (ny1 - ny0) / h0)
        s = max(s, E.MIN_SIZE / min(w0, h0))
        nw, nh = w0 * s, h0 * s
        if from_center:
            nx0, nx1, ny0, ny1 = cx - nw / 2, cx + nw / 2, cy - nh / 2, cy + nh / 2
        else:
            if left:
                nx0 = x1 - nw
            else:
                nx1 = x0 + nw
            if top:
                ny0 = y1 - nh
            else:
                ny1 = y0 + nh
    return pymupdf.Rect(nx0, ny0, nx1, ny1)


def constrain(dx: float, dy: float) -> tuple[float, float]:
    """Vincola a orizzontale, verticale o 45°."""
    if abs(dx) > 2 * abs(dy):
        return dx, 0.0
    if abs(dy) > 2 * abs(dx):
        return 0.0, dy
    m = (abs(dx) + abs(dy)) / 2
    return math.copysign(m, dx), math.copysign(m, dy)


class ObjectTool(Tool):
    """Selezione, spostamento, ridimensionamento e rotazione degli oggetti."""

    id = "objects"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, controller: "ObjectsController"):
        self.c = controller
        self.hover: int | None = None
        self.drag: dict | None = None
        self.crop: dict | None = None       # modalità ritaglio: {"page", "index", "rect"}

    # --------------------------------------------------------------- ciclo
    def activate(self, view):
        self.drag = None
        self.c.tool_activated(view)

    def deactivate(self, view):
        self.drag = None
        self.crop = None
        self.hover = None
        self.c.tool_deactivated(view)

    # --------------------------------------------------------------- geometria
    def _sel_rect(self, view) -> pymupdf.Rect | None:
        objs = self.c.selected_objects(view)
        if not objs:
            return None
        return E.union_rect(objs)

    def _hit_handle(self, view, page: int, p: pymupdf.Point) -> int | None:
        r = self._sel_rect(view)
        if r is None or page != self.c.sel_page:
            return None
        s = view_scale(view, page)
        tol = (HANDLE_PX + 3) / s
        rp = self._rotate_point(r, s)
        if abs(p - rp) <= tol:
            return 8
        for k, hp in enumerate(handle_points(r)):
            if abs(p.x - hp.x) <= tol and abs(p.y - hp.y) <= tol:
                return k
        return None

    @staticmethod
    def _rotate_point(r: pymupdf.Rect, s: float) -> pymupdf.Point:
        return pymupdf.Point((r.x0 + r.x1) / 2, r.y0 - ROTATE_PX / s)

    # --------------------------------------------------------------- eventi
    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        if self.crop is not None:
            return self._crop_press(view, ev)
        if ev.page is None:
            self.c.clear_selection(view)
            return True
        mods = ev.modifiers
        if mods & Qt.KeyboardModifier.ControlModifier and not self.c.selection:
            if self.c.follow_link_at(view, ev.page, ev.point):
                return True
        handle = self._hit_handle(view, ev.page, ev.point)
        if handle is not None:
            r = self._sel_rect(view)
            self.drag = {"type": "rotate" if handle == 8 else "resize", "handle": handle, "start": ev.point,
                         "rect": pymupdf.Rect(r), "page": ev.page, "cur": ev.point, "mods": mods}
            return True
        parsed = self.c.parsed(view, ev.page)
        tol = 3.0 / view_scale(view, ev.page)
        hits = E.hit_test(parsed, ev.point, tol) if parsed else []
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        toggle = bool(mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if alt and parsed:
            hits_all = E.hit_test(parsed, ev.point, tol, include_hidden=True)
            if hits_all:
                cur = self.c.selection[0] if (self.c.sel_page == ev.page and len(self.c.selection) == 1) else None
                if cur in hits_all:
                    nxt = hits_all[(hits_all.index(cur) + 1) % len(hits_all)]
                else:
                    nxt = hits_all[0]
                self.c.set_selection(view, ev.page, [nxt])
                return True
        if hits:
            top = hits[0]
            deselect = None
            if toggle and self.c.sel_page == ev.page:
                sel = list(self.c.selection)
                if top in sel:
                    deselect = top      # tolto al rilascio se non si trascina
                else:
                    sel.append(top)
                    self.c.set_selection(view, ev.page, sel)
            elif not (self.c.sel_page == ev.page and top in self.c.selection):
                self.c.set_selection(view, ev.page, [top])
            self.drag = {"type": "move", "start": ev.point, "page": ev.page, "cur": ev.point, "mods": mods,
                         "cur_page": ev.page, "copy": bool(mods & Qt.KeyboardModifier.ControlModifier),
                         "deselect": deselect}
            return True
        # area vuota: riquadro di selezione
        if not toggle:
            self.c.clear_selection(view)
        self.drag = {"type": "band", "start": ev.point, "page": ev.page, "cur": ev.point, "mods": mods}
        return True

    def move(self, view, ev):
        if self.crop is not None and self.crop.get("drag"):
            return self._crop_move(view, ev)
        d = self.drag
        if d is None:
            self._update_hover(view, ev)
            return False
        hit = view.view_to_page(ev.pos, clamp=True)
        if hit is None:
            return True
        page, pt = hit
        if page != d["page"]:
            d["cur_page"] = page
            d["cur_other"] = pt
            pt = view.clamp_to_page(d["page"], ev.pos)
        else:
            d["cur_page"] = page
            d.pop("cur_other", None)
        d["cur"] = pt
        d.setdefault("mods0", d["mods"])
        d["mods"] = ev.modifiers | d["mods0"]
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self.crop is not None and self.crop.get("drag"):
            self.crop["drag"] = None
            return True
        d, self.drag = self.drag, None
        if d is None:
            return False
        if ev.button != Qt.MouseButton.LeftButton:
            return True
        kind = d["type"]
        if kind == "band":
            r = pymupdf.Rect(d["start"], d["cur"])
            r.normalize()
            s = view_scale(view, d["page"])
            if r.width * s < 3 and r.height * s < 3:
                return True
            parsed = self.c.parsed(view, d["page"])
            if parsed:
                found = E.objects_in_rect(parsed, r, intersect=bool(d["mods"] & Qt.KeyboardModifier.AltModifier))
                if d["mods"] & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier) and self.c.sel_page == d["page"]:
                    found = list(dict.fromkeys(list(self.c.selection) + found))
                self.c.set_selection(view, d["page"], found)
            return True
        if kind == "move":
            if d.get("cur_page") is not None and d["cur_page"] != d["page"] and d.get("cur_other") is not None:
                self.c.move_to_page_at(view, d["cur_page"], d["cur_other"], d["start"])
                return True
            s = view_scale(view, d["page"])
            raw = (d["cur"].x - d["start"].x, d["cur"].y - d["start"].y)
            dx, dy = self._move_delta(view, d)
            if (abs(raw[0]) * s < 2 and abs(raw[1]) * s < 2) or (abs(dx) * s < 0.5 and abs(dy) * s < 0.5):
                if d.get("deselect") is not None:
                    self.c.set_selection(view, d["page"], [i for i in self.c.selection if i != d["deselect"]])
                return True
            if d.get("copy"):
                self.c.duplicate(view, dx, dy)
            else:
                self.c.translate(view, dx, dy, label=_("Sposta oggetti"))
            return True
        if kind == "resize":
            new = self._resize_rect(d)
            self.c.resize_to(view, new)
            return True
        if kind == "rotate":
            ang = self._rotate_angle(d)
            if abs(ang) > 1e-6:
                self.c.rotate(view, ang)
            return True
        return True

    def double_click(self, view, ev):
        if ev.page is None:
            return False
        objs = self.c.selected_objects(view)
        if len(objs) == 1 and objs[0].kind == IMAGE and objs[0].quad.rect.contains(ev.point):
            self.start_crop(view)
            return True
        return False

    def key(self, view, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if self.crop is not None:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_crop(view, True)
                return True
            if key == Qt.Key.Key_Escape:
                self.finish_crop(view, False)
                return True
            return True
        if key == Qt.Key.Key_Escape:
            if self.drag is not None:
                self.drag = None
                return True
            if self.c.selection:
                self.c.clear_selection(view)
                return True
            return False
        if ctrl and key == Qt.Key.Key_A:
            self.c.select_all(view)
            return True
        if ctrl and key == Qt.Key.Key_V:
            if shift:
                self.c.paste(view, in_place=True)
            else:
                self.c.paste(view)
            return True
        if key == Qt.Key.Key_Tab or key == Qt.Key.Key_Backtab:
            self.c.cycle(view, -1 if (shift or key == Qt.Key.Key_Backtab) else 1)
            return True
        if not self.c.selection:
            return False
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.c.delete(view)
            return True
        if ctrl and key == Qt.Key.Key_C:
            self.c.copy(view)
            return True
        if ctrl and key == Qt.Key.Key_X:
            self.c.cut(view)
            return True
        if ctrl and key == Qt.Key.Key_D:
            self.c.duplicate(view, E.PASTE_OFFSET, E.PASTE_OFFSET)
            return True
        if ctrl and key in (Qt.Key.Key_BracketRight, Qt.Key.Key_BraceRight):
            self.c.reorder(view, E.FRONT if shift else E.FORWARD)
            return True
        if ctrl and key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BraceLeft):
            self.c.reorder(view, E.BACK if shift else E.BACKWARD)
            return True
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            step = 10.0 if shift else 1.0
            vx, vy = arrows[key]
            self.c.nudge(view, vx * step, vy * step)
            return True
        return False

    # --------------------------------------------------------------- calcoli
    def _move_delta(self, view, d) -> tuple[float, float]:
        dx = d["cur"].x - d["start"].x
        dy = d["cur"].y - d["start"].y
        if d["mods"] & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = constrain(dx, dy)
        r = self._sel_rect(view)
        d["guides"] = (None, None)
        if r is not None:
            page_rect = self.c.page_rect(view, d["page"])
            if self.c.smart_guides and not (d["mods"] & Qt.KeyboardModifier.AltModifier):
                parsed = self.c.parsed(view, d["page"], quiet=True)
                if parsed is not None:
                    if "targets" not in d:
                        d["targets"] = E.snap_targets(parsed, set(self.c.selection), page_rect, r)
                    xs, ys = d["targets"]
                    tol = 5.0 / view_scale(view, d["page"])
                    dx, dy, gx, gy = E.snap_delta(r, dx, dy, xs, ys, tol)
                    d["guides"] = (gx, gy)
            dx, dy = E.clamp_translation(r, page_rect, dx, dy)
        return dx, dy

    def _resize_rect(self, d) -> pymupdf.Rect:
        mods = d["mods"]
        proportional = not (mods & Qt.KeyboardModifier.ShiftModifier)
        from_center = bool(mods & Qt.KeyboardModifier.AltModifier)
        return resized_rect(d["rect"], d["handle"], d["cur"], proportional, from_center)

    def _rotate_angle(self, d) -> float:
        c = rcenter(d["rect"])
        a0 = math.atan2(d["start"].y - c.y, d["start"].x - c.x)
        a1 = math.atan2(d["cur"].y - c.y, d["cur"].x - c.x)
        ang = -math.degrees(a1 - a0)   # positivo = antiorario a schermo
        while ang <= -180:
            ang += 360
        while ang > 180:
            ang -= 360
        if d["mods"] & Qt.KeyboardModifier.ShiftModifier:
            ang = round(ang / 15.0) * 15.0
        return ang

    def preview_matrix(self, view) -> pymupdf.Matrix | None:
        d = self.drag
        if d is None:
            return None
        if d["type"] == "move":
            if d["cur"] == d["start"]:
                return None
            dx, dy = self._move_delta(view, d)
            return pymupdf.Matrix(1, 0, 0, 1, dx, dy)
        if d["type"] == "resize":
            try:
                return E.resize_matrix(d["rect"], self._resize_rect(d))
            except E.ObjectError:
                return None
        if d["type"] == "rotate":
            return E.about(rcenter(d["rect"]), E.rotation_matrix(self._rotate_angle(d)))
        return None

    def _update_hover(self, view, ev):
        old = self.hover
        self.hover = None
        shape = Qt.CursorShape.ArrowCursor
        if ev.page is not None:
            h = self._hit_handle(view, ev.page, ev.point)
            if h is not None:
                shape = (Qt.CursorShape.CrossCursor if h == 8 else
                         Qt.CursorShape.SizeFDiagCursor if h in (0, 4) else
                         Qt.CursorShape.SizeBDiagCursor if h in (2, 6) else
                         Qt.CursorShape.SizeVerCursor if h in (1, 5) else Qt.CursorShape.SizeHorCursor)
            else:
                parsed = self.c.parsed(view, ev.page)
                hits = E.hit_test(parsed, ev.point, 3.0 / view_scale(view, ev.page)) if parsed else []
                if hits:
                    self.hover = (ev.page, hits[0])
                    shape = Qt.CursorShape.SizeAllCursor
                elif ev.modifiers & Qt.KeyboardModifier.ControlModifier and view.link_at(ev.page, ev.point):
                    shape = Qt.CursorShape.PointingHandCursor
        view.viewport().setCursor(shape)
        if old != self.hover:
            view.viewport().update()

    # --------------------------------------------------------------- ritaglio
    def start_crop(self, view) -> bool:
        objs = self.c.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            return False
        o = objs[0]
        self.crop = {"page": self.c.sel_page, "index": o.index, "rect": pymupdf.Rect(o.rect), "full": o.quad.rect,
                     "drag": None}
        view.viewport().update()
        self.c.status(_("Ritaglio: trascina le maniglie, Invio per confermare, Esc per annullare."))
        return True

    def _crop_press(self, view, ev):
        cr = self.crop
        if ev.page != cr["page"]:
            return True
        s = view_scale(view, ev.page)
        tol = (HANDLE_PX + 3) / s
        for k, hp in enumerate(handle_points(cr["rect"])):
            if abs(ev.point.x - hp.x) <= tol and abs(ev.point.y - hp.y) <= tol:
                cr["drag"] = {"handle": k, "start": pymupdf.Rect(cr["rect"])}
                return True
        if cr["rect"].contains(ev.point):
            cr["drag"] = {"handle": "move", "start": pymupdf.Rect(cr["rect"]), "p": ev.point}
        return True

    def _crop_move(self, view, ev):
        cr = self.crop
        dd = cr["drag"]
        hit = view.view_to_page(ev.pos, clamp=True)
        if hit is None or hit[0] != cr["page"]:
            return True
        p = hit[1]
        full = cr["full"]
        if dd["handle"] == "move":
            r = dd["start"]
            dx = max(full.x0 - r.x0, min(full.x1 - r.x1, p.x - dd["p"].x))
            dy = max(full.y0 - r.y0, min(full.y1 - r.y1, p.y - dd["p"].y))
            cr["rect"] = pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
        else:
            prop = bool(ev.modifiers & Qt.KeyboardModifier.ShiftModifier)
            r = resized_rect(dd["start"], dd["handle"], p, prop, False)
            cr["rect"] = r & full
        view.viewport().update()
        return True

    def finish_crop(self, view, accept: bool) -> None:
        cr, self.crop = self.crop, None
        if cr is not None and accept:
            self.c.crop_to_rect(view, cr["page"], cr["index"], cr["rect"])
        view.viewport().update()

    # --------------------------------------------------------------- disegno
    def paint(self, view, painter: QPainter, page: int) -> None:
        s = view_scale(view, page)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        parsed = self.c.parsed(view, page, quiet=True)
        if parsed is None:
            return
        if self.hover and self.hover[0] == page and 0 <= self.hover[1] < len(parsed.objects):
            o = parsed.objects[self.hover[1]]
            if not (self.c.sel_page == page and o.index in self.c.selection):
                pen = QPen(kind_color(o.kind), 1.5 / s)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(qpoly(o.quad))
        if self.c.sel_page == page:
            objs = [parsed.objects[i] for i in self.c.selection if 0 <= i < len(parsed.objects)]
            for o in objs:
                pen = QPen(kind_color(o.kind), 1.2 / s)
                if o.hidden:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(qpoly(o.quad))
            if objs and self.crop is None:
                r = E.union_rect(objs)
                M = self.preview_matrix(view)
                if M is not None:
                    pen = QPen(theme.color("accent"), 1.5 / s, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    fill = QColor(theme.color("accent"))
                    fill.setAlpha(40)
                    painter.setBrush(QBrush(fill))
                    for o in objs:
                        painter.drawPolygon(qpoly(o.quad * M))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                self._paint_handles(painter, r, s)
        if self.drag is not None and self.drag["type"] == "move" and self.drag["page"] == page:
            gx, gy = self.drag.get("guides", (None, None))
            pr = self.c.page_rect(view, page)
            painter.setPen(QPen(theme.color("secondary"), 1 / s, Qt.PenStyle.DashLine))
            if gx is not None:
                painter.drawLine(QPointF(gx, pr.y0), QPointF(gx, pr.y1))
            if gy is not None:
                painter.drawLine(QPointF(pr.x0, gy), QPointF(pr.x1, gy))
        if self.drag is not None and self.drag["type"] == "band" and self.drag["page"] == page:
            r = pymupdf.Rect(self.drag["start"], self.drag["cur"])
            r.normalize()
            fill = QColor(theme.color("accent"))
            fill.setAlpha(35)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(theme.color("accent"), 1 / s, Qt.PenStyle.DashLine))
            painter.drawRect(qrect(r))
        if self.drag is not None and self.drag["type"] == "move" and self.drag.get("cur_page") == page \
                and page != self.drag["page"]:
            painter.setPen(QPen(theme.color("accent"), 3 / s))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(qrect(self.c.page_rect(view, page)))
        if self.crop is not None and self.crop["page"] == page:
            full, r = self.crop["full"], self.crop["rect"]
            shade = QColor(0, 0, 0, 110)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            for part in (pymupdf.Rect(full.x0, full.y0, full.x1, r.y0), pymupdf.Rect(full.x0, r.y1, full.x1, full.y1),
                         pymupdf.Rect(full.x0, r.y0, r.x0, r.y1), pymupdf.Rect(r.x1, r.y0, full.x1, r.y1)):
                if part.width > 0 and part.height > 0:
                    painter.drawRect(qrect(part))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color("secondary"), 1.5 / s))
            painter.drawRect(qrect(r))
            self._paint_handles(painter, r, s, rotate=False)

    def _paint_handles(self, painter: QPainter, r: pymupdf.Rect, s: float, rotate: bool = True) -> None:
        accent = theme.color("accent")
        painter.setPen(QPen(accent, 1 / s, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(qrect(r))
        hs = HANDLE_PX / s
        painter.setPen(QPen(accent, 1.2 / s))
        painter.setBrush(QBrush(theme.color("surface")))
        for hp in handle_points(r):
            painter.drawRect(QRectF(hp.x - hs, hp.y - hs, 2 * hs, 2 * hs))
        if rotate:
            rp = self._rotate_point(r, s)
            painter.drawLine(QPointF((r.x0 + r.x1) / 2, r.y0), QPointF(rp.x, rp.y + hs))
            painter.setBrush(QBrush(accent))
            painter.drawEllipse(QPointF(rp.x, rp.y), hs, hs)


class LinkTool(Tool):
    """Crea, seleziona, sposta e ridimensiona i collegamenti."""

    id = "links"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, controller: "ObjectsController"):
        self.c = controller
        self.drag: dict | None = None
        self.selection: list[int] = []      # xref delle annotazioni
        self.page: int | None = None

    def activate(self, view):
        self.drag = None
        self.c.status(_("Collegamento: traccia un rettangolo per crearne uno; clic per selezionare quelli esistenti."))

    def deactivate(self, view):
        self.drag = None
        self.selection = []
        self.page = None

    def _links(self, view, page):
        return self.c.links(view, page)

    def _hit(self, view, page, p):
        for info in reversed(self._links(view, page)):
            if info.rect.contains(p):
                return info
        return None

    def _sel_infos(self, view):
        if self.page is None:
            return []
        return [i for i in self._links(view, self.page) if i.xref in self.selection]

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        s = view_scale(view, ev.page)
        infos = self._sel_infos(view)
        if len(infos) == 1 and self.page == ev.page:
            tol = (HANDLE_PX + 3) / s
            for k, hp in enumerate(handle_points(infos[0].rect)):
                if abs(ev.point.x - hp.x) <= tol and abs(ev.point.y - hp.y) <= tol:
                    self.drag = {"type": "resize", "handle": k, "rect": pymupdf.Rect(infos[0].rect),
                                 "start": ev.point, "cur": ev.point, "page": ev.page, "mods": ev.modifiers}
                    return True
        info = self._hit(view, ev.page, ev.point)
        if info is not None:
            if ev.modifiers & Qt.KeyboardModifier.ControlModifier:
                self.c.follow_link_at(view, ev.page, ev.point)
                return True
            if ev.modifiers & Qt.KeyboardModifier.ShiftModifier and self.page == ev.page:
                if info.xref in self.selection:
                    self.selection.remove(info.xref)
                else:
                    self.selection.append(info.xref)
            elif info.xref not in self.selection or self.page != ev.page:
                self.selection = [info.xref]
            self.page = ev.page
            self.drag = {"type": "move", "start": ev.point, "cur": ev.point, "page": ev.page, "mods": ev.modifiers}
            self.c.links_selection_changed(view)
            return True
        self.selection = []
        self.page = ev.page
        self.drag = {"type": "new", "start": ev.point, "cur": ev.point, "page": ev.page, "mods": ev.modifiers}
        self.c.links_selection_changed(view)
        return True

    def move(self, view, ev):
        if self.drag is None:
            shape = Qt.CursorShape.CrossCursor
            if ev.page is not None and self._hit(view, ev.page, ev.point) is not None:
                shape = Qt.CursorShape.SizeAllCursor
                info = self._hit(view, ev.page, ev.point)
                view.setToolTip(info.target.describe())
            else:
                view.setToolTip("")
            view.viewport().setCursor(shape)
            return False
        page = self.drag["page"]
        self.drag["cur"] = view.clamp_to_page(page, ev.pos)
        self.drag.setdefault("mods0", self.drag["mods"])
        self.drag["mods"] = ev.modifiers | self.drag["mods0"]
        view.viewport().update()
        return True

    def release(self, view, ev):
        d, self.drag = self.drag, None
        if d is None:
            return False
        if d["type"] == "new":
            r = pymupdf.Rect(d["start"], d["cur"])
            r.normalize()
            if r.width < 4 or r.height < 4:
                return True
            xref = self.c.create_link_interactive(view, d["page"], r)
            if xref:
                self.selection = [xref]
                self.page = d["page"]
            return True
        if d["type"] == "move":
            dx = d["cur"].x - d["start"].x
            dy = d["cur"].y - d["start"].y
            if abs(dx) + abs(dy) * 1 >= 0.5 / view_scale(view, d["page"]) and (abs(dx) > 0.01 or abs(dy) > 0.01):
                self.c.move_links(view, d["page"], list(self.selection), dx, dy)
            return True
        if d["type"] == "resize":
            r = resized_rect(d["rect"], d["handle"], d["cur"], False, False)
            if self.selection:
                self.c.resize_link(view, d["page"], self.selection[0], r)
            return True
        return True

    def double_click(self, view, ev):
        if ev.page is None:
            return False
        info = self._hit(view, ev.page, ev.point)
        if info is None:
            return False
        self.selection = [info.xref]
        self.page = ev.page
        self.c.edit_link_interactive(view, ev.page, [info.xref])
        return True

    def key(self, view, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Escape:
            if self.selection:
                self.selection = []
                self.c.links_selection_changed(view)
                return True
            return False
        if not self.selection or self.page is None:
            if ctrl and key == Qt.Key.Key_V:
                self.c.paste_links(view)
                return True
            return False
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.c.delete_links(view, self.page, list(self.selection))
            self.selection = []
            return True
        if ctrl and key == Qt.Key.Key_C:
            self.c.copy_links(view, self.page, list(self.selection))
            return True
        if ctrl and key == Qt.Key.Key_V:
            self.c.paste_links(view)
            return True
        if ctrl and key == Qt.Key.Key_D:
            self.c.copy_links(view, self.page, list(self.selection), clipboard=False)
            self.c.paste_links(view, offset=E.PASTE_OFFSET, use_last=True)
            return True
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows:
            step = 10.0 if mods & Qt.KeyboardModifier.ShiftModifier else 1.0
            with ENGINE_LOCK:
                page = view.document.pdf[self.page]
                dx, dy = E.view_vector_to_page(page, arrows[key][0] * step, arrows[key][1] * step)
            self.c.move_links(view, self.page, list(self.selection), dx, dy)
            return True
        return False

    def paint(self, view, painter, page):
        s = view_scale(view, page)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = theme.color("accent")
        for info in self._links(view, page):
            sel = self.page == page and info.xref in self.selection
            r = info.rect
            if self.drag is not None and sel and self.drag["type"] == "move":
                dx = self.drag["cur"].x - self.drag["start"].x
                dy = self.drag["cur"].y - self.drag["start"].y
                r = pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
            if self.drag is not None and sel and self.drag["type"] == "resize":
                r = resized_rect(self.drag["rect"], self.drag["handle"], self.drag["cur"], False, False)
            pen = QPen(accent if sel else theme.color("secondary"), (1.6 if sel else 1.0) / s,
                       Qt.PenStyle.SolidLine if sel else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            fill = QColor(accent)
            fill.setAlpha(40 if sel else 18)
            painter.setBrush(QBrush(fill))
            painter.drawRect(qrect(r))
            if sel and len(self.selection) == 1:
                hs = HANDLE_PX / s
                painter.setBrush(QBrush(theme.color("surface")))
                painter.setPen(QPen(accent, 1.2 / s))
                for hp in handle_points(r):
                    painter.drawRect(QRectF(hp.x - hs, hp.y - hs, 2 * hs, 2 * hs))
        if self.drag is not None and self.drag["type"] == "new" and self.drag["page"] == page:
            r = pymupdf.Rect(self.drag["start"], self.drag["cur"])
            r.normalize()
            painter.setPen(QPen(accent, 1.2 / s, Qt.PenStyle.DashLine))
            fill = QColor(accent)
            fill.setAlpha(30)
            painter.setBrush(QBrush(fill))
            painter.drawRect(qrect(r))


__all__ = ["ObjectTool", "LinkTool", "resized_rect", "constrain", "handle_points", "PATH"]
