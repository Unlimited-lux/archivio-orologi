"""Strumenti interattivi della vista documento.

Uno strumento riceve eventi già tradotti in coordinate della pagina PDF
(punti, pagina NON ruotata: le stesse coordinate usate dal motore per testo,
annotazioni e disegno). Per disegnare un'anteprima, `paint()` riceve un
QPainter già trasformato in coordinate pagina: si disegna direttamente in punti.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter
from PySide6.QtWidgets import QMessageBox

from ..i18n import _

if TYPE_CHECKING:
    from .document_view import DocumentView


@dataclass
class PageEvent:
    """Evento del mouse sulla vista, con la posizione in coordinate pagina."""

    page: int | None             # indice pagina sotto il cursore (None = fuori dalle pagine)
    point: pymupdf.Point | None  # punto in coordinate pagina non ruotata
    pos: QPointF                 # posizione nel viewport (pixel)
    button: Qt.MouseButton
    buttons: Qt.MouseButtons
    modifiers: Qt.KeyboardModifiers
    event: object = None         # evento Qt originale


@dataclass
class TextSelection:
    page: int
    rects: list[pymupdf.Rect] = field(default_factory=list)
    text: str = ""
    # parole selezionate: tuple del motore (x0, y0, x1, y1, parola, blocco, riga, n)
    words: list[tuple] = field(default_factory=list)

    @property
    def quads(self) -> list[pymupdf.Quad]:
        return [r.quad for r in self.rects]


class Tool:
    """Base per gli strumenti. I metodi restituiscono True se l'evento è gestito."""

    id = "tool"
    cursor = Qt.CursorShape.ArrowCursor

    def activate(self, view: "DocumentView") -> None:
        pass

    def deactivate(self, view: "DocumentView") -> None:
        pass

    def press(self, view: "DocumentView", ev: PageEvent) -> bool:
        return False

    def move(self, view: "DocumentView", ev: PageEvent) -> bool:
        return False

    def release(self, view: "DocumentView", ev: PageEvent) -> bool:
        return False

    def double_click(self, view: "DocumentView", ev: PageEvent) -> bool:
        return False

    def key(self, view: "DocumentView", event) -> bool:
        return False

    def paint(self, view: "DocumentView", painter: QPainter, page: int) -> None:
        pass


# Distanza massima (pt) entro cui un estremo del trascinamento «aggancia» la parola più vicina.
SNAP_DISTANCE = 36


def _nearest_word(words: list[tuple], pt: pymupdf.Point, max_dist: float | None = None) -> int | None:
    best, best_d = None, None
    limit = None if max_dist is None else max_dist * max_dist
    for idx, w in enumerate(words):
        r = pymupdf.Rect(w[:4])
        if r.contains(pt):
            return idx
        dx = max(r.x0 - pt.x, 0, pt.x - r.x1)
        dy = max(r.y0 - pt.y, 0, pt.y - r.y1)
        d = dx * dx + dy * dy * 4  # privilegia la stessa riga
        if limit is not None and dx * dx + dy * dy > limit:
            continue
        if best_d is None or d < best_d:
            best, best_d = idx, d
    return best


def words_to_text(words: list[tuple]) -> str:
    parts: list[str] = []
    prev = None
    for w in words:
        key = (w[5], w[6])
        if prev is not None:
            parts.append(" " if key == prev else "\n")
        parts.append(w[4])
        prev = key
    return "".join(parts)


def selection_from_words(page: int, words: list[tuple]) -> TextSelection:
    rects = [pymupdf.Rect(w[:4]) for w in words]
    return TextSelection(page=page, rects=rects, text=words_to_text(words), words=list(words))


# Gestori registrati dai moduli (ctx.add_link_handler): callable(view, link) -> bool.
# Interrogati per priorità decrescente (a pari priorità, in ordine di registrazione); il primo
# che restituisce True ha gestito il collegamento, altrimenti vale il comportamento base.
LINK_HANDLERS: list = []          # elementi: (priorità, progressivo, gestore)
_link_seq = 0


def add_link_handler(handler, priority: int = 0) -> None:
    global _link_seq
    if any(h is handler for _p, _s, h in LINK_HANDLERS):
        return
    _link_seq += 1
    LINK_HANDLERS.append((priority, _link_seq, handler))
    LINK_HANDLERS.sort(key=lambda t: (-t[0], t[1]))


def remove_link_handler(handler) -> None:
    LINK_HANDLERS[:] = [t for t in LINK_HANDLERS if t[2] is not handler]


def follow_link(view: "DocumentView", link: dict) -> None:
    for _prio, _seq, handler in list(LINK_HANDLERS):
        try:
            if handler(view, link):
                return
        except Exception as e:
            view.status(f"Errore nell'apertura del collegamento: {e}")
            return
    kind = link.get("kind")
    if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and link.get("page", -1) >= 0:
        to = link.get("to")
        view.go_to_page(link["page"], to if isinstance(to, pymupdf.Point) else None)
    elif kind == pymupdf.LINK_URI and link.get("uri"):
        uri = link["uri"]
        answer = QMessageBox.question(
            view, _("Collegamento esterno"),
            _("Il documento vuole aprire questo indirizzo:\n\n{uri}\n\nAprirlo nel browser?").format(uri=uri),
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(uri))
    elif kind == pymupdf.LINK_GOTOR and link.get("file"):
        view.status(_("Il collegamento rimanda a un altro file: {f}").format(f=link["file"]))


class SelectTextTool(Tool):
    """Strumento predefinito: selezione del testo e clic sui collegamenti."""

    id = "select-text"
    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self) -> None:
        self._anchor: tuple[int, pymupdf.Point] | None = None
        self._moved = False

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        view.clear_text_selection()
        if ev.page is None:
            self._anchor = None
            return True
        self._anchor = (ev.page, ev.point)
        self._moved = False
        return True

    def move(self, view, ev):
        if self._anchor is None or not (ev.buttons & Qt.MouseButton.LeftButton):
            self._update_cursor(view, ev)
            return False
        page, start = self._anchor
        if ev.point is None:
            return True
        hit = view.view_to_page(ev.pos, clamp=True)
        if hit is None or hit[0] != page:
            # selezione limitata alla pagina iniziale
            hit = (page, view.clamp_to_page(page, ev.pos))
        end = hit[1]
        if not self._moved and abs(end.x - start.x) + abs(end.y - start.y) < 2:
            return True
        self._moved = True
        words = view.words(page)
        a = _nearest_word(words, start, SNAP_DISTANCE)
        b = _nearest_word(words, end, SNAP_DISTANCE)
        if a is None and b is None:
            # trascinamento lontano dal testo: solo le parole dentro il riquadro tracciato
            box = pymupdf.Rect(start, end).normalize()
            inside = [i for i, w in enumerate(words) if box.intersects(pymupdf.Rect(w[:4]))]
            if not inside:
                view.clear_text_selection()
                return True
            a, b = inside[0], inside[-1]
        if a is None:
            a = _nearest_word(words, start)
        if b is None:
            b = _nearest_word(words, end)
        if a is None or b is None:
            return True
        lo, hi = min(a, b), max(a, b)
        view.set_text_selection(selection_from_words(page, words[lo:hi + 1]))
        return True

    def release(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        anchor, self._anchor = self._anchor, None
        if anchor is not None and not self._moved and ev.page is not None:
            link = view.link_at(ev.page, ev.point)
            if link is not None:
                follow_link(view, link)
        return True

    def double_click(self, view, ev):
        if ev.page is None:
            return False
        words = view.words(ev.page)
        idx = _nearest_word(words, ev.point)
        if idx is not None and pymupdf.Rect(words[idx][:4]).contains(ev.point):
            view.set_text_selection(selection_from_words(ev.page, [words[idx]]))
            self._anchor = None
            return True
        return False

    def _update_cursor(self, view, ev):
        shape = Qt.CursorShape.ArrowCursor
        if ev.page is not None:
            if view.link_at(ev.page, ev.point) is not None:
                shape = Qt.CursorShape.PointingHandCursor
            elif view.word_at(ev.page, ev.point) is not None:
                shape = Qt.CursorShape.IBeamCursor
        view.viewport().setCursor(shape)


class HandTool(Tool):
    """Trascina per scorrere il documento."""

    id = "hand"
    cursor = Qt.CursorShape.OpenHandCursor

    def __init__(self) -> None:
        self._start: tuple[QPointF, int, int] | None = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton:
            return False
        self._start = (ev.pos, view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def move(self, view, ev):
        if self._start is None:
            return False
        pos, h, v = self._start
        view.horizontalScrollBar().setValue(int(h - (ev.pos.x() - pos.x())))
        view.verticalScrollBar().setValue(int(v - (ev.pos.y() - pos.y())))
        return True

    def release(self, view, ev):
        if self._start is None:
            return False
        self._start = None
        view.viewport().setCursor(self.cursor)
        return True


def copy_text_to_clipboard(text: str) -> None:
    if text:
        QGuiApplication.clipboard().setText(text)


def selection_color(alpha: int = 80) -> QColor:
    c = QColor(37, 99, 235)
    c.setAlpha(alpha)
    return c


def rect_to_qrectf(r: pymupdf.Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)
