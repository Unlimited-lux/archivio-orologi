"""NFR-06: chiusa una scheda, nessun oggetto del modulo trattiene la DocumentView."""

from __future__ import annotations

import gc
import weakref

import pytest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from foliant.modules import objects as O

from .test_obj_ui import make_doc, ui  # noqa: F401


def _module_referrers(obj) -> list:
    out = []
    for r in gc.get_referrers(obj):
        mod = getattr(type(r), "__module__", "") or ""
        if mod.startswith("foliant.modules.objects"):
            out.append(type(r).__name__)
        elif isinstance(r, dict):
            for rr in gc.get_referrers(r):
                m2 = getattr(type(rr), "__module__", "") or ""
                if m2.startswith("foliant.modules.objects") and getattr(rr, "__dict__", None) is r:
                    out.append(type(rr).__name__ + ".__dict__")
    return out


def test_closed_view_not_retained_by_module(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    c = ui.c
    c.set_selection(view, 0, [0])
    c.parsed(view, 0)
    c.links(view, 0)
    O._state["panel"].set_view(view)
    O._state["link_panel"].set_view(view)
    ref = weakref.ref(view)
    view.document._saved_state = view.document._state
    ui.window.close_view(view)
    del view
    for _k in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
    gc.collect()
    v = ref()
    if v is not None:
        assert not _module_referrers(v), _module_referrers(v)


@pytest.mark.qt_no_exception_capture   # i difetti di altri moduli (OCR) non sono di objects
def test_two_windows_close_second_first_still_works(ui, tmp_path, settings, qtbot):
    """Ctrl+N: una seconda finestra nello stesso processo non sovrascrive lo stato della prima."""
    import traceback

    from foliant.ui.main_window import MainWindow
    first_acts = O._st(ui.window.ctx)["actions"]
    with qtbot.capture_exceptions() as errors:   # altri moduli possono avere difetti propri
        win2 = MainWindow(settings)
        win2.show()
        assert O._state["actions"] is not first_acts
        win2.close()
        win2.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        gc.collect()
        assert O._state["actions"] is first_acts
        view = ui.open(make_doc(tmp_path / "b.pdf", images=[(50, 70, 250, 170)]))
        first_acts["objects"].trigger()
        ui.c.set_selection(view, 0, [0])
        assert first_acts["rotate_cw"].isEnabled()
        first_acts["rotate_cw"].trigger()
        QApplication.processEvents()
        view.document._saved_state = view.document._state
    ours = ["".join(traceback.format_exception(*e)) for e in errors]
    ours = [t for t in ours if "modules/objects" in t]
    assert not ours, ours[0]
