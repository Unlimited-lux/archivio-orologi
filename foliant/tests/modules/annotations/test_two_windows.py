"""Ctrl+N: due finestre nello stesso processo; chiudere la seconda non rompe il modulo nella prima."""

import pymupdf
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from foliant.modules.annotations import dialogs, engine

from .conftest import click


def test_second_window_closed_first_still_works(comments, settings, text_pdf, qtbot, monkeypatch):
    import sys
    from foliant.ui.main_window import MainWindow
    window, view, module = comments
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    second = MainWindow(settings)

    second.show()
    v2 = second.open_path(text_pdf)
    m2 = second.ctx.annotations
    assert m2 is not module and m2.ctrl is not module.ctrl
    second.set_mode("commenta")
    m2.ctrl.apply("n", engine.create_note, 0, (100, 100), "x", engine.Style(), "A", view=v2)
    v2.document._saved_state = v2.document._state
    monkeypatch.setattr(second, "_ask_unsaved", lambda docs: "discard", raising=False)
    second.close()
    second.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    # la prima finestra continua a funzionare: nota con il clic, pannello, editor con controllo ortografico
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: ("Prima finestra", None))
    module.activate(engine.NOTE)
    click(view, 0, 300, 400)
    infos = module.ctrl.annotations()
    assert [i.contents for i in infos] == ["Prima finestra"]
    module.comments.reload()
    module.ctrl.select([(0, infos[0].xref)], view)
    dlg = dialogs.TextDialog(view, "T", "L", "testo")
    qtbot.addWidget(dlg)
    import traceback
    mine = ["".join(traceback.format_exception(*e)) for e in errors]
    mine = [t for t in mine if "/modules/annotations/" in t]      # eccezioni di altri moduli: non qui
    assert not mine, "".join(mine)
