"""VIS-42 — Opzioni di apertura scritte nel documento."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.api import FIT_WIDTH, VIEW_FACING
from foliant.modules.viewer import session_engine as E


def _ctl(window):
    return window.ctx.viewer_parts["openoptions"]


def _pdf(path, pages=5, toc=True, open_action=None):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=16)
    if toc:
        doc.set_toc([[1, f"Capitolo {i + 1}", i + 1] for i in range(pages)])
    if open_action:
        x = doc.get_new_xref()
        doc.update_object(x, open_action)
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{x} 0 R")
    doc.save(str(path))
    return str(path)


def test_write_and_read_back():
    doc = pymupdf.open()
    for _i in range(4):
        doc.new_page()
    o = E.OpenOptions(page=2, fit=FIT_WIDTH, layout=VIEW_FACING, panel="bookmarks", display_title=True,
                      center_window=True, direction_rtl=True, print_scaling_none=True,
                      duplex="DuplexFlipLongEdge", num_copies=3)
    E.write_open_options(doc, o)
    doc = pymupdf.open("pdf", doc.tobytes())
    back = E.read_open_options(doc)
    assert (back.page, back.fit, back.layout, back.panel) == (2, FIT_WIDTH, VIEW_FACING, "bookmarks")
    assert back.display_title and back.center_window and back.direction_rtl
    assert back.print_scaling_none and back.duplex == "DuplexFlipLongEdge" and back.num_copies == 3
    assert not back.fullscreen and not back.hide_toolbar
    with pytest.raises(ValueError):
        E.write_open_options(doc, E.OpenOptions(page=9))
    # tutto disattivato: niente /ViewerPreferences
    E.write_open_options(doc, E.OpenOptions())
    assert doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")[0] == "null"


def test_saved_options_apply_when_reopened(viewer_window, tmp_path):
    """Dati pagina iniziale 3, zoom «adatta larghezza» e pannello segnalibri salvati, il file
    riaperto con «rispetta le impostazioni del documento» si apre a pagina 3 adattato in
    larghezza con i segnalibri visibili."""
    path = _pdf(tmp_path / "manuale.pdf")
    view = viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    assert ctl.apply(view, E.OpenOptions(page=2, fit=FIT_WIDTH, panel="bookmarks"))
    assert view.document.undo_label == "Opzioni di apertura"
    out = tmp_path / "manuale_opzioni.pdf"
    view.document.save_copy(str(out))
    view.document._saved_state = view.document._state
    viewer_window.close_view(view)
    viewer_window.sidebar.hide()                     # nessun pannello prima di riaprire
    again = viewer_window.open_path(str(out))
    assert again.current_page == 2
    assert again.fit_mode == FIT_WIDTH
    assert viewer_window.sidebar.isVisible()
    bookmarks = viewer_window.ctx.viewer_parts["bookmarks"].panel
    assert bookmarks.isVisible()


def test_print_preferences_preselected(viewer_window, tmp_path):
    """Date la scala di stampa «nessuna» e la stampa fronte/retro sul lato lungo salvate nel
    documento, il dialogo Stampa le preseleziona."""
    path = _pdf(tmp_path / "stampa.pdf")
    view = viewer_window.open_path(path)
    assert _ctl(viewer_window).apply(view, E.OpenOptions(print_scaling_none=True, duplex="DuplexFlipLongEdge",
                                                         num_copies=2))
    dlg = viewer_window.ctx.viewer_parts["printing"].options_dialog(view)
    assert dlg.rb_actual.isChecked() and dlg.copies.value() == 2
    opts = dlg.options()
    assert opts.duplex == "DuplexFlipLongEdge" and opts.scaling != "fit"
    assert "lato lungo" in dlg.duplex_note.text()
    view.document._saved_state = view.document._state


def test_script_open_action_asks_before_replacing(viewer_window, tmp_path, monkeypatch):
    path = _pdf(tmp_path / "script.pdf", open_action="<< /S /JavaScript /JS (app.alert(1)) >>")
    view = viewer_window.open_path(path)
    asked = []
    monkeypatch.setattr(viewer_window.ctx, "confirm", lambda t, m: (asked.append(m), False)[1])
    assert not _ctl(viewer_window).apply(view, E.OpenOptions(page=1))
    assert asked and "script" in asked[0] and not view.document.is_modified


def test_dialog_summary_and_options(viewer_window, tmp_path):
    view = viewer_window.open_path(_pdf(tmp_path / "dlg.pdf"))
    dlg = _ctl(viewer_window).dialog_for(view)
    dlg.page.setValue(3)
    dlg._select(dlg.zoom, FIT_WIDTH)
    dlg._select(dlg.panel, "bookmarks")
    dlg.display_title.setChecked(True)
    assert dlg.summary.text() == ("Si apre a pagina 3, adattata in larghezza, con i segnalibri visibili, "
                                  "mostrando il titolo del documento.")
    o = dlg.options()
    assert (o.page, o.fit, o.panel, o.display_title) == (2, FIT_WIDTH, "bookmarks", True)


def test_properties_dialog_offers_opening_options(viewer_window, tmp_path, monkeypatch):
    view = viewer_window.open_path(_pdf(tmp_path / "prop.pdf"))
    dlg = viewer_window.ctx.viewer_parts["properties"].show_dialog()
    assert dlg.opening_button is not None
    called = []
    monkeypatch.setattr(_ctl(viewer_window), "open_dialog", lambda v=None: called.append(v))
    dlg.opening_button.click()
    assert called == [view]
    dlg.hide()
