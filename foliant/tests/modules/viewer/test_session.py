"""Test di posizione per file (VIS-02), vista iniziale (VIS-05), schede e sessione (VIS-30)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from foliant.api import FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE
from foliant.modules.viewer import session_engine as engine
from foliant.modules.viewer.session_engine import ViewState
from foliant.ui.main_window import MainWindow

from .conftest import part


def _session(window):
    return part(window, "session")


def _make(tmp_path, name, pages=30, catalog=None, open_action=None):
    """PDF con chiavi di catalogo arbitrarie; `open_action(page_xrefs)` restituisce il valore."""
    path = tmp_path / name
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=24)
    cat = doc.pdf_catalog()
    for key, value in (catalog or {}).items():
        doc.xref_set_key(cat, key, value)
    if open_action is not None:
        doc.xref_set_key(cat, "OpenAction", open_action([doc[i].xref for i in range(pages)]))
    doc.save(str(path))
    doc.close()
    return str(path)


def _new_window(qtbot, settings):
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    return win


def _cleanup(win):
    for view in list(win.views()):
        view.document._saved_state = view.document._state
        win.close_view(view)


# ====================================================================== motore
def test_parse_dest_variants():
    assert engine.parse_dest("[12 0 R /FitH 792]") == (("xref", 12), FIT_WIDTH, None)
    assert engine.parse_dest("[3 /Fit]") == (("index", 3), FIT_PAGE, None)
    assert engine.parse_dest("[7 0 R /XYZ 0 800 1.5]") == (("xref", 7), None, 1.5)
    assert engine.parse_dest("[7 0 R /XYZ null null 0]") == (("xref", 7), None, None)
    assert engine.parse_dest("garbage") == (None, None, None)


def test_positions_limit_and_roundtrip():
    positions = {}
    for i in range(engine.MAX_POSITIONS + 20):
        positions = engine.remember_position(positions, f"/f{i}.pdf", ViewState(i, 1.0, None, None), now=i)
    assert len(positions) == engine.MAX_POSITIONS
    assert "/f0.pdf" not in positions and f"/f{engine.MAX_POSITIONS + 19}.pdf" in positions
    loaded = engine.load_positions(engine.dump_positions(positions))
    assert ViewState.from_dict(loaded["/f30.pdf"]).page == 30
    assert engine.load_positions("{non json") == {}


def test_session_roundtrip_and_corrupt():
    raw = engine.dump_session([("/a.pdf", ViewState(4, None, FIT_WIDTH, VIEW_CONTINUOUS))], 0)
    tabs, current = engine.load_session(raw)
    assert tabs[0][0] == "/a.pdf" and tabs[0][1].page == 4 and tabs[0][1].fit == FIT_WIDTH
    assert engine.load_session("[[[") == ([], 0)


def test_read_initial_view_from_catalog(tmp_path):
    path = _make(tmp_path, "cat.pdf", pages=10, catalog={"PageLayout": "/TwoPageRight", "PageMode": "/UseThumbs"},
                 open_action=lambda x: f"[{x[3]} 0 R /XYZ 0 0 2]")
    doc = pymupdf.open(path)
    iv = engine.read_initial_view(doc)
    doc.close()
    assert (iv.page, iv.zoom, iv.mode, iv.panel) == (3, 2.0, VIEW_COVER, "thumbnails")


def test_open_action_goto_dictionary_and_javascript(tmp_path):
    path = _make(tmp_path, "goto.pdf", pages=8, open_action=lambda x: f"<</S/GoTo/D[{x[6]} 0 R/Fit]>>")
    doc = pymupdf.open(path)
    iv = engine.read_initial_view(doc)
    doc.close()
    assert (iv.page, iv.fit) == (6, FIT_PAGE)
    path = _make(tmp_path, "js.pdf", pages=3, open_action=lambda x: "<</S/JavaScript/JS(app.alert(1))>>")
    doc = pymupdf.open(path)
    assert engine.read_initial_view(doc).page is None
    doc.close()


# ====================================================================== VIS-02 posizione per file
def test_reopen_restores_page_37_and_zoom_150(viewer_window, tmp_path, qtbot):
    path = _make(tmp_path, "lungo.pdf", pages=60)
    view = viewer_window.open_path(path)
    view.set_zoom(1.5)
    view.go_to_page(36)
    assert view.current_page == 36
    viewer_window.close_view(view)
    view = viewer_window.open_path(path)
    assert view.current_page == 36
    qtbot.wait(100)   # impaginazione definitiva della scheda
    assert view.current_page == 36
    assert view.zoom == pytest.approx(1.5)
    assert view.fit_mode is None
    assert viewer_window.page_spin.text() == "37"
    raw = viewer_window.settings.value("viewer/positions")
    assert "password" not in raw


def test_fit_mode_and_layout_are_remembered(viewer_window, tmp_path):
    path = _make(tmp_path, "fit.pdf", pages=12)
    view = viewer_window.open_path(path)
    view.set_view_mode(VIEW_FACING)
    view.set_fit(FIT_PAGE)
    view.go_to_page(6)
    viewer_window.close_view(view)
    view = viewer_window.open_path(path)
    assert view.view_mode == VIEW_FACING
    assert view.fit_mode == FIT_PAGE
    assert view.current_page in (6, 7)


def test_no_history_preference_records_nothing(viewer_window, tmp_path):
    ctl = _session(viewer_window)
    ctl.set_pref("viewer/no_history", True)
    paths = [_make(tmp_path, f"f{i}.pdf", pages=20) for i in range(3)]
    for p in paths:
        view = viewer_window.open_path(p)
        view.go_to_page(9)
        viewer_window.close_view(view)
    assert not viewer_window.settings.value("viewer/positions")
    view = viewer_window.open_path(paths[0])
    assert view.current_page == 0


# ====================================================================== VIS-05 vista iniziale
def test_open_action_page5_fith(viewer_window, tmp_path, qtbot):
    path = _make(tmp_path, "oa.pdf", pages=12, open_action=lambda x: f"[{x[4]} 0 R /FitH 792]")
    view = viewer_window.open_path(path)
    assert view.current_page == 4
    assert view.fit_mode == FIT_WIDTH
    viewer_window.resize(1000, 700)
    qtbot.wait(100)
    assert view.current_page == 4


def test_page_layout_two_column_left(viewer_window, tmp_path):
    path = _make(tmp_path, "tcl.pdf", pages=6, catalog={"PageLayout": "/TwoColumnLeft"})
    view = viewer_window.open_path(path)
    assert view.view_mode == VIEW_FACING
    assert view._row_of(0) == [0, 1]   # nessuna copertina separata
    assert viewer_window.view_mode_actions[VIEW_FACING].isChecked()


@pytest.mark.parametrize("layout,mode", [("SinglePage", VIEW_SINGLE), ("OneColumn", VIEW_CONTINUOUS),
                                         ("TwoColumnRight", VIEW_COVER), ("TwoPageLeft", VIEW_FACING)])
def test_page_layout_mapping(viewer_window, tmp_path, layout, mode):
    view = viewer_window.open_path(_make(tmp_path, f"{layout}.pdf", pages=4, catalog={"PageLayout": f"/{layout}"}))
    assert view.view_mode == mode


def test_page_mode_use_outlines_shows_bookmarks_panel(viewer_window, tmp_path):
    if "bookmarks" not in viewer_window._sidebar:
        viewer_window.ctx.add_sidebar_panel("bookmarks", "Segnalibri", "bookmark", QLabel("finto"), order=20)
    if "thumbnails" not in viewer_window._sidebar:
        viewer_window.ctx.add_sidebar_panel("thumbnails", "Miniature", "layout-grid", QLabel("finto"), order=10)
    viewer_window.ctx.show_sidebar_panel("thumbnails")
    widget = viewer_window._sidebar["bookmarks"][2]
    viewer_window.open_path(_make(tmp_path, "ol.pdf", pages=3, catalog={"PageMode": "/UseOutlines"}))
    assert viewer_window.sidebar_stack.currentWidget() is widget
    assert viewer_window.sidebar.isVisible()


def test_page_mode_fullscreen_is_only_offered(viewer_window, tmp_path, monkeypatch, qtbot):
    ctl = _session(viewer_window)
    reading = viewer_window.ctx.viewer_parts.get("reading")
    if reading is not None and hasattr(reading, "ask_fullscreen"):
        monkeypatch.setattr(reading, "ask_fullscreen", lambda: False)
    else:
        monkeypatch.setattr(ctl, "_fullscreen_handled_elsewhere", lambda: False)
    viewer_window.open_path(_make(tmp_path, "fs.pdf", pages=3, catalog={"PageMode": "/FullScreen"}))
    qtbot.wait(20)
    assert not viewer_window.isFullScreen()
    assert ctl.last_initial_view.fullscreen


def test_fullscreen_offer_button(viewer_window, tmp_path, monkeypatch):
    ctl = _session(viewer_window)
    monkeypatch.setattr(ctl, "_fullscreen_handled_elsewhere", lambda: False)
    reading = viewer_window.ctx.viewer_parts.get("reading")
    if reading is not None and hasattr(reading, "ask_fullscreen"):
        monkeypatch.setattr(reading, "ask_fullscreen", lambda: False)
    viewer_window.open_path(_make(tmp_path, "fs2.pdf", pages=3, catalog={"PageMode": "/FullScreen"}))
    assert not viewer_window.isFullScreen()
    assert ctl.fullscreen_button.isVisible()
    assert viewer_window.statusBar().currentMessage()
    ctl.fullscreen_button.click()
    assert not ctl.fullscreen_button.isVisible()


def test_ignore_document_view_preference(viewer_window, tmp_path):
    _session(viewer_window).set_pref("viewer/ignore_document_view", True)
    path = _make(tmp_path, "ign.pdf", pages=12, catalog={"PageLayout": "/SinglePage"},
                 open_action=lambda x: f"[{x[4]} 0 R /FitH 792]")
    view = viewer_window.open_path(path)
    assert view.current_page == 0
    assert view.view_mode == VIEW_CONTINUOUS


def test_remembered_position_wins_over_document_view(viewer_window, tmp_path):
    path = _make(tmp_path, "rem.pdf", pages=12, open_action=lambda x: f"[{x[4]} 0 R /FitH 792]")
    view = viewer_window.open_path(path)
    view.go_to_page(9)
    viewer_window.close_view(view)
    assert viewer_window.open_path(path).current_page == 9


# ====================================================================== VIS-30 schede
def _three_tabs(window, tmp_path, pages=(5, 10, 15), prefix="tab"):
    views = []
    for i, p in enumerate(pages):
        view = window.open_path(_make(tmp_path, f"{prefix}{i}.pdf", pages=20))
        view.go_to_page(p - 1)
        views.append(view)
    return views


def test_ctrl_tab_and_alt_numbers(viewer_window, tmp_path, qtbot):
    views = _three_tabs(viewer_window, tmp_path)
    tabs = viewer_window.tabs
    assert tabs.currentIndex() == 2
    _session(viewer_window).act_next_tab.trigger()
    assert tabs.currentIndex() == 0
    _session(viewer_window).act_prev_tab.trigger()
    assert tabs.currentIndex() == 2
    # le scorciatoie Alt+N e Ctrl+Tab sono del nucleo (UX-18): qui si verificano i comandi del modulo
    _session(viewer_window).jump_to_tab(2)
    assert viewer_window.current_view() is views[1]
    _session(viewer_window).jump_to_tab(9)
    assert viewer_window.current_view() is views[2]
    _session(viewer_window).next_tab()
    assert viewer_window.current_view() is views[0]


def test_ctrl_shift_t_reopens_with_same_page_and_zoom(viewer_window, tmp_path, qtbot):
    views = _three_tabs(viewer_window, tmp_path)
    path = str(views[1].document.path)
    views[1].set_zoom(2.0)
    views[1].go_to_page(11)
    viewer_window.close_view(views[1])
    # posizione memorizzata cancellata: il ripristino viene dalla pila delle chiusure
    viewer_window.settings.remove("viewer/positions")
    assert len(viewer_window.views()) == 2
    _session(viewer_window).act_reopen.trigger()   # Ctrl+Maiusc+T è del nucleo
    view = viewer_window.current_view()
    assert str(view.document.path) == path
    assert view.current_page == 11
    assert view.zoom == pytest.approx(2.0)
    assert viewer_window.tabs.indexOf(view) == 1   # stessa posizione nella barra


def test_close_others_right_all(viewer_window, tmp_path):
    ctl = _session(viewer_window)
    views = _three_tabs(viewer_window, tmp_path)
    ctl.activate_tab(1)
    ctl.act_close_others.trigger()
    assert viewer_window.views() == [views[1]]
    more = _three_tabs(viewer_window, tmp_path, prefix="altra")
    ctl.activate_tab(0)
    ctl.close_tabs_to_right(views[1])
    assert viewer_window.views() == [views[1]]
    assert more  # le tre schede aperte a destra sono state chiuse
    ctl.close_all_tabs()
    assert viewer_window.views() == []
    # la pila delle chiusure permette di riaprire l'ultima
    assert ctl.reopen_closed_tab() is not None


def test_close_others_stops_when_user_cancels(viewer_window, tmp_path, monkeypatch):
    ctl = _session(viewer_window)
    views = _three_tabs(viewer_window, tmp_path)
    with monkeypatch.context() as m:      # ripristinato prima della chiusura della finestra
        m.setattr(viewer_window, "close_view", lambda v, **_k: False)
        assert ctl.close_other_tabs(views[0]) is False
        assert len(viewer_window.views()) == 3


def test_tab_context_menu(viewer_window, tmp_path):
    ctl = _session(viewer_window)
    views = _three_tabs(viewer_window, tmp_path)
    menu = ctl.build_tab_menu(2)            # menu del nucleo con le voci del viewer
    assert "Chiudi scheda" in [a.text() for a in menu.actions()]
    texts = {a.text(): a for a in menu.actions() if a.text()}
    assert not texts["Chiudi le schede a destra"].isEnabled()   # ultima scheda
    menu = ctl.build_tab_menu(0)
    texts = {a.text(): a for a in menu.actions() if a.text()}
    texts["Chiudi le schede a destra"].trigger()
    assert viewer_window.views() == [views[0]]


def test_menu_contains_tab_actions(viewer_window):
    viewer_window._rebuild_menu()
    names = []

    def walk(menu):
        for a in menu.actions():
            names.append(a.text())
            if a.menu():
                walk(a.menu())
    walk(viewer_window.main_menu)
    for text in ("Riapri scheda chiusa", "Chiudi le altre schede", "Chiudi le schede a destra",
                 "Chiudi tutte le schede", "Ripristina la sessione all'avvio", "Non registrare la cronologia"):
        assert text in names


# ====================================================================== VIS-30 sessione
def test_session_restored_in_new_window(viewer_window, tmp_path, qtbot, settings):
    ctl = _session(viewer_window)
    ctl.act_restore_session.setChecked(True)
    assert settings.value("viewer/restore_session") in (True, "true")
    views = _three_tabs(viewer_window, tmp_path)
    paths = [str(v.document.path) for v in views]
    settings.remove("viewer/positions")   # il ripristino deve venire dalla sessione
    viewer_window.close()
    assert viewer_window.views() == []

    win = _new_window(qtbot, settings)
    try:
        qtbot.waitUntil(lambda: len(win.views()) == 3, timeout=3000)
        assert [str(v.document.path) for v in win.views()] == paths
        qtbot.wait(100)
        assert [v.current_page for v in win.views()] == [4, 9, 14]
        assert win.current_view() is win.views()[2]
    finally:
        _cleanup(win)


def test_session_skips_missing_files_and_off_by_default(viewer_window, tmp_path, qtbot, settings):
    ctl = _session(viewer_window)
    views = _three_tabs(viewer_window, tmp_path)
    ctl.save_session()   # preferenza disattiva → nessuna sessione salvata
    assert not settings.value("viewer/session")
    ctl.set_pref("viewer/restore_session", True)
    ctl.save_session()
    (tmp_path / "tab1.pdf").unlink()
    _cleanup(viewer_window)
    opened = ctl.restore_session()
    assert [v.document.path.name for v in opened] == ["tab0.pdf", "tab2.pdf"]
    assert "tab1.pdf" in viewer_window.statusBar().currentMessage()
    assert views


def test_session_not_restored_if_documents_already_open(viewer_window, tmp_path, qtbot, settings):
    settings.setValue("viewer/restore_session", True)
    settings.setValue("viewer/session", engine.dump_session(
        [(_make(tmp_path, "s.pdf", pages=3), ViewState(1, None, FIT_WIDTH, VIEW_CONTINUOUS))], 0))
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.show()
    other = win.open_path(_make(tmp_path, "cmd.pdf", pages=2))   # come da riga di comando
    try:
        QApplication.processEvents()
        assert win.views() == [other]
    finally:
        _cleanup(win)
