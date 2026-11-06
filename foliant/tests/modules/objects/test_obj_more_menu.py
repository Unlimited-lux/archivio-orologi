"""Scheda Modifica › Altro: «Incolla», «Incolla in posizione» e «Termina modifica esterna»
sono attive solo quando servono."""

from __future__ import annotations

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from foliant.modules import objects as O

from .test_obj_ui import make_doc, png_bytes, ui  # noqa: F401


def _acts():
    return O._state["actions"]


def test_paste_needs_something_to_paste(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "scheda_orologio.pdf", images=[(50, 70, 250, 170)]))
    import sys
    CT = sys.modules["foliant.modules.objects.controller"]
    monkeypatch.setattr(CT, "_last_payload", None)
    QGuiApplication.clipboard().clear()
    QApplication.processEvents()
    assert not _acts()["paste"].isEnabled()
    assert not _acts()["paste_in_place"].isEnabled()
    # solo testo negli appunti: niente da incollare come oggetto
    QGuiApplication.clipboard().setText("Rolex Submariner 16610")
    QApplication.processEvents()
    assert not _acts()["paste"].isEnabled()
    # un'immagine negli appunti
    mime = QMimeData()
    mime.setData("image/png", png_bytes(30, 20))
    QGuiApplication.clipboard().setMimeData(mime)
    QApplication.processEvents()
    assert _acts()["paste"].isEnabled() and _acts()["paste_in_place"].isEnabled()
    QGuiApplication.clipboard().clear()
    QApplication.processEvents()
    assert not _acts()["paste"].isEnabled()
    # oggetti copiati da Foliant
    ui.c.set_selection(view, 0, [0])
    ui.c.copy(view, quiet=True)
    QApplication.processEvents()
    assert _acts()["paste"].isEnabled() and _acts()["paste_in_place"].isEnabled()


def test_stop_external_only_during_external_edit(ui, tmp_path, monkeypatch):
    from foliant.modules.objects import external as XT
    monkeypatch.setattr(XT, "launch", lambda p: True)
    view = ui.open(make_doc(tmp_path / "foto_quadrante.pdf", images=[(50, 70, 250, 170)]))
    stop = _acts()["external_stop"]
    assert not stop.isEnabled()
    ui.c.set_selection(view, 0, [0])
    sess = ui.c.edit_external(view)
    assert sess is not None
    assert stop.isEnabled()
    ui.c.stop_external()
    assert not stop.isEnabled()
    # la sessione può terminare da sola (file temporaneo eliminato)
    ui.c.set_selection(view, 0, [0])
    sess = ui.c.edit_external(view)
    assert stop.isEnabled()
    monkeypatch.setattr(ui.ctx, "error", lambda *a, **k: None)
    import os
    os.remove(sess.path)
    sess.reimport()
    assert not stop.isEnabled()
