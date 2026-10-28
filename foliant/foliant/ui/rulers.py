"""Righelli della vista documento (VIS-39, OBJ-23).

Due righelli (orizzontale in alto, verticale a sinistra) e l'angolo con l'unità stanno sopra
il bordo del viewport della `DocumentView`; la vista sposta l'impaginazione di `RULER_SIZE`
pixel per non coprire le pagine. Lo zero è l'angolo superiore sinistro della pagina come la
si vede (riquadro /CropBox con /Rotate e la rotazione della vista applicati) sotto il
puntatore, o della pagina corrente; la scala segue lo zoom.

Trascinando da un righello si crea una guida (la logica delle guide è nella vista).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..i18n import _, language
from . import theme

if TYPE_CHECKING:
    from .document_view import DocumentView

RULER_SIZE = 20                  # spessore dei righelli, pixel logici
RULER_UNITS = ("mm", "pt", "in")
PT_PER_UNIT = {"mm": 72.0 / 25.4, "pt": 1.0, "in": 72.0}
MIN_LABEL_PX = 50                # distanza minima tra due numeri del righello
MIN_TICK_PX = 4                  # distanza minima tra due tacche

# Passi delle tacche principali (con numero), dal più fitto; oltre l'ultimo si moltiplica per 10.
_STEPS = {
    "mm": (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000),
    "pt": (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000),
    "in": (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 5, 10, 20, 50, 100),
}
# Suddivisioni di una tacca principale, dalla più fitta.
_SUBDIVISIONS = {"mm": (10, 5, 2), "pt": (10, 5, 2), "in": (8, 4, 2)}


def unit_names() -> list[tuple[str, str]]:
    """(chiave, nome per l'utente) delle unità dei righelli."""
    return [("mm", _("Millimetri")), ("pt", _("Punti")), ("in", _("Pollici"))]


def major_step(unit: str, px_per_unit: float) -> float:
    """Passo delle tacche con numero, nell'unità: almeno MIN_LABEL_PX pixel tra due numeri."""
    steps = _STEPS[unit]
    for s in steps:
        if s * px_per_unit >= MIN_LABEL_PX:
            return s
    s = steps[-1]
    while s * px_per_unit < MIN_LABEL_PX:
        s *= 10
    return s


def ruler_ticks(origin: float, px_per_pt: float, unit: str, start: float, end: float) -> list[tuple[float, float, int]]:
    """Tacche del righello tra i pixel `start` ed `end`: (pixel, valore nell'unità, livello).

    `origin` è il pixel dello zero (bordo della pagina), `px_per_pt` i pixel per punto PDF
    (zoom). Livello 0 = tacca principale con numero, 1 = metà, 2 = suddivisione."""
    ppu = px_per_pt * PT_PER_UNIT[unit]
    if ppu <= 0 or end <= start:
        return []
    major = major_step(unit, ppu)
    sub = next((n for n in _SUBDIVISIONS[unit] if major * ppu / n >= MIN_TICK_PX), 1)
    minor = major / sub
    step_px = minor * ppu
    first = math.floor((start - origin) / step_px)
    last = math.ceil((end - origin) / step_px)
    ticks = []
    for k in range(first, last + 1):
        if k % sub == 0:
            level = 0
        elif sub % 2 == 0 and k % (sub // 2) == 0:
            level = 1
        else:
            level = 2
        ticks.append((origin + k * step_px, k * minor, level))
    return ticks


def format_value(value: float) -> str:
    """Numero del righello: senza decimali inutili, con la virgola in italiano."""
    if abs(value) < 1e-9:
        value = 0.0
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if language() == "it" else text


class Ruler(QWidget):
    """Righello orizzontale (in alto) o verticale (a sinistra), figlio del viewport della vista."""

    def __init__(self, view: "DocumentView", horizontal: bool):
        super().__init__(view.viewport())
        self.view = view
        self.horizontal = horizontal
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)      # i movimenti sul righello non arrivano alle pagine sotto
        # trascinando si crea una guida parallela al righello
        self.setCursor(Qt.CursorShape.SplitVCursor if horizontal else Qt.CursorShape.SplitHCursor)
        self.setToolTip(_("Trascina sulla pagina per creare una guida (con Ctrl: su tutte le pagine)"))

    def _to_viewport(self, pos: QPointF) -> QPointF:
        return QPointF(self.mapTo(self.view.viewport(), pos))

    # Gli eventi del mouse si accettano sempre: non devono arrivare allo strumento attivo
    # attraverso il viewport (la rotella invece sì, per scorrere).
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.view._begin_new_guide(self.horizontal, self._to_viewport(ev.position()))
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        pos = self._to_viewport(ev.position())
        if self.view._guide_drag is not None:
            self.view._guide_drag_move(pos)
        else:
            self.view._track_pointer(pos)
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.view._guide_drag is not None:
            self.view._guide_drag_end(self._to_viewport(ev.position()), ev.modifiers())
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        ev.accept()

    def contextMenuEvent(self, ev) -> None:
        self.view._ruler_menu(ev.globalPos())
        ev.accept()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        view = self.view
        size = float(RULER_SIZE)
        p.fillRect(self.rect(), theme.color("surface_alt"))
        length = float(self.width() if self.horizontal else self.height())
        page = view.ruler_page()
        if page is not None:
            r = view.page_rect(page)
            lo, hi = (r.left(), r.right()) if self.horizontal else (r.top(), r.bottom())
            band = QRectF(lo, 0, hi - lo, size) if self.horizontal else QRectF(0, lo, size, hi - lo)
            p.fillRect(band, theme.color("surface"))
            font = p.font()
            font.setPointSizeF(max(6.0, font.pointSizeF() * 0.75))
            p.setFont(font)
            p.setPen(QPen(theme.color("text_muted"), 0))
            for pos, value, level in ruler_ticks(lo, view._scale(), view.ruler_unit, 0.0, length):
                tick = size * (0.5 if level == 0 else 0.32 if level == 1 else 0.2)
                if self.horizontal:
                    p.drawLine(QPointF(pos, size), QPointF(pos, size - tick))
                    if level == 0:
                        p.drawText(QPointF(pos + 2, size * 0.5), format_value(value))
                else:
                    p.drawLine(QPointF(size, pos), QPointF(size - tick, pos))
                    if level == 0:
                        p.save()
                        p.translate(3, pos + 2)
                        p.rotate(90)
                        p.drawText(QPointF(0, 0), format_value(value))
                        p.restore()
        pointer = view._pointer
        if pointer is not None:
            p.setPen(QPen(theme.color("accent"), 0))
            if self.horizontal:
                p.drawLine(QPointF(pointer.x(), 0), QPointF(pointer.x(), size))
            else:
                p.drawLine(QPointF(0, pointer.y()), QPointF(size, pointer.y()))
        p.setPen(QPen(theme.color("border"), 0))
        if self.horizontal:
            p.drawLine(QPointF(0, size - 0.5), QPointF(length, size - 0.5))
        else:
            p.drawLine(QPointF(size - 0.5, 0), QPointF(size - 0.5, length))


class RulerCorner(QWidget):
    """Angolo tra i due righelli: mostra l'unità; il menu (anche col clic) la cambia."""

    def __init__(self, view: "DocumentView"):
        super().__init__(view.viewport())
        self.view = view
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setToolTip(_("Unità dei righelli"))

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.view._ruler_menu(ev.globalPosition().toPoint())
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:
        ev.accept()

    def contextMenuEvent(self, ev) -> None:
        self.view._ruler_menu(ev.globalPos())
        ev.accept()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), theme.color("surface_alt"))
            font = p.font()
            font.setPointSizeF(max(6.0, font.pointSizeF() * 0.7))
            p.setFont(font)
            p.setPen(theme.color("text_muted"))
            p.drawText(QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.view.ruler_unit)
            p.setPen(QPen(theme.color("border"), 0))
            s = float(RULER_SIZE) - 0.5
            p.drawLine(QPointF(0, s), QPointF(s, s))
            p.drawLine(QPointF(s, 0), QPointF(s, s))
        finally:
            p.end()
