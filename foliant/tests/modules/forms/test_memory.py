"""NFR-06: chiusa la scheda, il modulo Moduli non tiene vivi la vista e il documento.

Si controlla con `gc.get_referrers` che nessun oggetto del modulo (filler, stato del modulo,
chiusure, metodi legati) punti ancora alla vista o al documento chiusi. La liberazione
completa (weakref → None) dipende anche dal nucleo e dagli altri moduli.
"""

from __future__ import annotations

import gc
import types
import weakref

from PySide6.QtWidgets import QApplication

from foliant.modules import forms

from .test_fill import click_field, open_form

FORMS = "foliant.modules.forms"


def _from_forms(ref) -> bool:
    """True se `ref` (un referente diretto) appartiene al modulo Moduli."""
    if isinstance(ref, types.MethodType):
        return getattr(ref.__func__, "__module__", "").startswith(FORMS)
    if isinstance(ref, types.FunctionType):
        return ref.__module__.startswith(FORMS)
    if isinstance(ref, types.CellType):
        return any(_from_forms(f) for t in gc.get_referrers(ref) if isinstance(t, tuple)
                   for f in gc.get_referrers(t) if isinstance(f, types.FunctionType))
    if isinstance(ref, dict):
        return any(getattr(o, "__dict__", None) is ref and type(o).__module__.startswith(FORMS)
                   for o in gc.get_referrers(ref))
    return type(ref).__module__.startswith(FORMS)


def _close(qtbot, window, form_pdf, interact):
    view = open_form(window, form_pdf)
    forms.instance().filler(view).state.refresh(force=True)
    interact(view)
    refs = weakref.ref(view), weakref.ref(view.document)
    view.document._saved_state = view.document._state
    window.close_view(view)
    del view
    for _ in range(3):
        QApplication.processEvents()
        gc.collect()
    return refs


def _forms_holders(refs) -> list[str]:
    out = []
    for r in refs:
        obj = r()
        if obj is None:
            continue
        out += [type(x).__name__ for x in gc.get_referrers(obj)
                if not isinstance(x, types.FrameType) and _from_forms(x)]
    return out


def test_closed_tab_not_kept_by_forms(qtbot, window, form_pdf):
    refs = _close(qtbot, window, form_pdf, lambda view: None)
    assert not _forms_holders(refs)
    assert not forms.instance().fillers


def test_closed_tab_not_kept_after_editing_and_design(qtbot, window, form_pdf):
    def interact(view):
        click_field(qtbot, view, "nome")
        qtbot.keyClicks(forms.instance().filler(view).editor, "Mario")
        window.ctx.set_mode("moduli")
        QApplication.processEvents()
    refs = _close(qtbot, window, form_pdf, interact)
    assert not _forms_holders(refs)
    assert not forms.instance().fillers
