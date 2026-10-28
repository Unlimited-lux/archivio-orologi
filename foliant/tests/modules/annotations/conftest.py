"""Fixture del modulo commenti."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent


@pytest.fixture
def text_pdf(tmp_path):
    """PDF di 4 pagine: una riga «Lorem ipsum», un paragrafo di tre righe, testo su ogni pagina."""
    path = tmp_path / "testo.pdf"
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "Lorem ipsum", fontsize=12)
        page.insert_text((72, 200), "Prima riga del paragrafo", fontsize=12)
        page.insert_text((72, 216), "Seconda riga del paragrafo", fontsize=12)
        page.insert_text((72, 232), "Terza riga del paragrafo", fontsize=12)
        page.insert_text((72, 300), f"Pagina {i + 1} termine di consegna", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def image_pdf(tmp_path):
    """PDF con una sola pagina composta da un'immagine (nessun testo)."""
    path = tmp_path / "scansione.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), 0)
    pix.clear_with(230)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def comments(window, text_pdf, monkeypatch):
    """Finestra con il documento di prova aperto in modalità Commenta.

    Restituisce (window, view, module). Gli errori e i messaggi vanno in `module.messages`."""
    view = window.open_path(text_pdf)
    module = window.ctx.annotations
    messages = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: messages.append(("error", t, m)))
    monkeypatch.setattr(window.ctx, "info", lambda t, m: messages.append(("info", t, m)))
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: True)
    module.messages = messages
    original_close = window.close_view

    def close_view(v):   # nessuna richiesta di salvataggio alla chiusura dei test
        if v is not None:
            v.document._saved_state = v.document._state
        return original_close(v)

    monkeypatch.setattr(window, "close_view", close_view)
    if hasattr(window, "_ask_unsaved"):   # chiusura della finestra a fine test senza domande
        monkeypatch.setattr(window, "_ask_unsaved", lambda docs: "discard")
    window.ctx.settings().setValue("annotations/author", "Maria Rossi")
    window.ctx.settings().setValue("annotations/author_confirmed", True)
    window.set_mode("commenta")
    view.set_zoom(1.0)
    view.flush_rendering()
    return window, view, module


def view_pos(view, page: int, x: float, y: float) -> QPointF:
    return view.page_transform(page).map(QPointF(x, y))


def _event(kind, pos: QPointF, button, buttons, mods):
    return QMouseEvent(kind, pos, pos, button, buttons, mods)


def press(view, page, x, y, mods=Qt.KeyboardModifier.NoModifier):
    view.mousePressEvent(_event(QEvent.Type.MouseButtonPress, view_pos(view, page, x, y), Qt.MouseButton.LeftButton,
                                Qt.MouseButton.LeftButton, mods))


def move(view, page, x, y, mods=Qt.KeyboardModifier.NoModifier, pressed=True):
    view.mouseMoveEvent(_event(QEvent.Type.MouseMove, view_pos(view, page, x, y), Qt.MouseButton.NoButton,
                               Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton, mods))


def release(view, page, x, y, mods=Qt.KeyboardModifier.NoModifier):
    view.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, view_pos(view, page, x, y),
                                  Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods))


def double_click(view, page, x, y):
    view.mouseDoubleClickEvent(_event(QEvent.Type.MouseButtonDblClick, view_pos(view, page, x, y),
                                      Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                      Qt.KeyboardModifier.NoModifier))


def click(view, page, x, y, mods=Qt.KeyboardModifier.NoModifier):
    press(view, page, x, y, mods)
    release(view, page, x, y, mods)


def drag(view, page, a, b, mods=Qt.KeyboardModifier.NoModifier, steps: int = 6):
    press(view, page, *a, mods)
    for i in range(1, steps + 1):
        t = i / steps
        move(view, page, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, mods)
    release(view, page, *b, mods)


def key(view, k, mods=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtGui import QKeyEvent
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods))


def saved(window, view, tmp_path, name="salvato.pdf") -> pymupdf.Document:
    """Salva il documento su disco e lo riapre con il motore."""
    path = tmp_path / name
    view.document.save(str(path))
    return pymupdf.open(str(path))


_PAGES: list = []   # le annotazioni del motore richiedono che la pagina resti viva


def annots(doc: pymupdf.Document, page: int = 0, subtype: str | None = None) -> list:
    p = doc[page]
    _PAGES.append(p)
    out = [a for a in p.annots() if subtype is None or a.type[1] == subtype]
    return out


def key_of(doc, xref, key):
    return doc.xref_get_key(xref, key)
