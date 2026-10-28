"""NFR-06: chiusa la scheda, il modulo Moduli non tiene vivi la vista e il documento.

Si controlla con `gc.get_referrers` che nessun oggetto del modulo (filler, stato del modulo,
chiusure, metodi legati) punti ancora alla vista o al documento chiusi. La liberazione
completa (weakref → None) dipende anche dal nucleo e dagli altri moduli.
"""

from __future__ import annotations

import gc
import types
import weakref

from PySide6.QtCore import QEvent
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
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # deleteLater del nucleo
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


def test_closed_tab_view_and_document_collected(qtbot, window, form_pdf):
    """NFR-06: con i riferimenti rilasciati anche dal nucleo, vista e documento vengono raccolti."""
    view_ref, doc_ref = _close(qtbot, window, form_pdf, lambda view: None)
    assert view_ref() is None and doc_ref() is None


def test_second_window_closed_first_still_works(qtbot, settings, window, form_pdf):
    """Ctrl+N: un modulo per finestra; chiusa la seconda, la prima funziona senza errori."""
    from foliant.ui.main_window import MainWindow
    first = forms.instance(window.ctx)
    second_win = MainWindow(settings)
    qtbot.addWidget(second_win)
    second = forms.instance(second_win.ctx)
    assert second is not None and second is not first
    v2 = open_form(second_win, form_pdf)
    v2.document._saved_state = v2.document._state
    second_win.close_view(v2)
    second_win.close()
    second_win.deleteLater()
    QApplication.processEvents()
    view = open_form(window, form_pdf)
    assert forms.instance(window.ctx) is first
    click_field(qtbot, view, "nome")
    f = first.filler(view)
    qtbot.keyClicks(f.editor, "Anna")
    f.commit_active()
    window.ctx.set_mode("moduli")
    window.ctx.set_mode("leggi")
    QApplication.processEvents()
    from .test_fill import values
    assert values(view)["nome"] == "Anna"


def test_content_change_without_form_is_fast(window, tmp_path):
    """Un documento con 300 commenti e nessun campo: la reazione del modulo alle modifiche è
    immediata (nessuna lettura dei campi)."""
    import time

    import pymupdf
    pdf = pymupdf.open()
    for p in range(30):
        pg = pdf.new_page()
        for i in range(10):
            pg.add_text_annot((50 + 40 * i, 50 + 10 * i), f"nota {p}.{i}")
    pdf.save(str(tmp_path / "contratto.pdf"))
    view = open_form(window, tmp_path / "contratto.pdf")
    f = forms.instance().filler(view)
    with view.document.change("nota") as d:
        d[0].add_text_annot((300, 300), "nuova")
    t = time.perf_counter()
    f._on_content_changed()
    assert time.perf_counter() - t < 0.02
    assert not f.state.fields


def test_signature_click_uses_request_signature(qtbot, window, tmp_path, monkeypatch):
    """FRM-20: clic su un campo firma vuoto → ctx.request_signature con il campo preselezionato."""
    import pymupdf

    from foliant.modules.forms import engine as E
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(300, 80, 500, 130), "FirmaCliente")
    pdf.save(str(tmp_path / "firma.pdf"))
    view = open_form(window, tmp_path / "firma.pdf")
    calls = []
    monkeypatch.setattr(window.ctx, "request_signature", lambda v, name: calls.append((v, name)) or True,
                        raising=False)
    click_field(qtbot, view, "FirmaCliente")
    assert calls == [(view, "FirmaCliente")]
