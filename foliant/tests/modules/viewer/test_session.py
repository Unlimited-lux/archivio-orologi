"""Test di vista iniziale (VIS-05) e schede (VIS-30), e della regola di riservatezza:
nessuna posizione per file e nessuna sessione salvate su disco."""

from __future__ import annotations

import json

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QLabel

from tests.conftest import perf_limit
from foliant.api import FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE
from foliant.modules.viewer import session_engine as engine
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


# ====================================================================== riservatezza
LEGACY_KEYS = ("viewer/positions", "viewer/session", "viewer/restore_session", "viewer/no_history",
               "viewer/restore_position", "viewer/search_history")


def _viewer_values(settings) -> str:
    """Tutte le chiavi e i valori del viewer, come testo (per cercarvi nomi di file)."""
    settings.sync()
    out = []
    for key in settings.allKeys():
        if key.startswith("viewer/"):
            out.append(f"{key}={settings.value(key)!r}")
    return "\n".join(out)


def test_reopen_does_not_remember_position(viewer_window, tmp_path, qtbot):
    """Riservatezza: alla riapertura il documento parte dalla vista predefinita, e nelle
    impostazioni non resta né la posizione né il nome del file."""
    path = _make(tmp_path, "zq7k-lungo.pdf", pages=60)
    view = viewer_window.open_path(path)
    view.set_zoom(1.5)
    view.go_to_page(36)
    viewer_window.close_view(view)
    view = viewer_window.open_path(path)
    qtbot.wait(50)
    assert view.current_page == 0
    assert viewer_window.settings.value("viewer/positions") is None
    assert "zq7k" not in _viewer_values(viewer_window.settings)


def test_document_view_applies_on_every_open(viewer_window, tmp_path):
    path = _make(tmp_path, "rem.pdf", pages=12, open_action=lambda x: f"[{x[4]} 0 R /FitH 792]")
    view = viewer_window.open_path(path)
    view.go_to_page(9)
    viewer_window.close_view(view)
    assert viewer_window.open_path(path).current_page == 4


def test_no_history_actions_and_preferences(viewer_window):
    viewer_window.show_backstage()
    names = []

    def walk(actions):
        for a in actions:
            names.append(a.text())
            if a.menu():
                walk(a.menu().actions())
    walk(viewer_window.backstage.all_actions())
    walk(a for m in viewer_window.modes.values() for a in m.toolbar.actions())
    viewer_window.hide_backstage()
    for text in ("Ripristina la sessione all'avvio", "Non registrare la cronologia"):
        assert text not in names
    ctl = _session(viewer_window)
    for name in ("save_session", "restore_session", "remember", "positions", "act_restore_session"):
        assert not hasattr(ctl, name)


def test_legacy_keys_removed_at_startup(qtbot, settings, tmp_path):
    """Chi aggiorna da una versione precedente: posizioni, sessione e cronologie salvate
    vengono cancellate all'avvio (senza riaprire nulla e senza scrivere marcatori)."""
    old = _make(tmp_path, "zq7k-vecchio.pdf", pages=3)
    settings.setValue("viewer/positions", json.dumps({old: {"page": 2, "zoom": 1.0, "t": 1}}))
    settings.setValue("viewer/session", json.dumps({"tabs": [{"path": old, "page": 1}], "current": 0}))
    settings.setValue("viewer/restore_session", True)
    settings.setValue("viewer/no_history", False)
    settings.setValue("viewer/restore_position", True)
    settings.setValue("viewer/search_history", ["zq7k"])
    settings.setValue("viewer/reading_theme", "sepia")         # preferenza generale: resta
    win = _new_window(qtbot, settings)
    try:
        QApplication.processEvents()
        qtbot.wait(50)
        assert win.views() == []                                # la sessione non viene ripristinata
        for key in LEGACY_KEYS:
            assert not settings.contains(key), key
        assert settings.value("viewer/reading_theme") == "sepia"
        assert not settings.contains("viewer/privacy_cleanup")  # nessun marcatore
        assert "zq7k" not in _viewer_values(settings)
        # idempotente: ricontrolla a ogni avvio, ma senza nulla da togliere non fa nulla
        from foliant.modules.viewer import privacy
        assert privacy.run_once(settings) is False
        settings.setValue("viewer/no_history", True)
        assert privacy.run_once(settings) is True
        assert not settings.contains("viewer/no_history")
    finally:
        _cleanup(win)


def test_closing_window_saves_no_session(viewer_window, tmp_path, qtbot, settings):
    views = _three_tabs(viewer_window, tmp_path, prefix="zq7k-scheda")
    assert len(views) == 3
    viewer_window.close()
    assert viewer_window.views() == []
    for key in LEGACY_KEYS:
        assert not settings.contains(key), key
    assert "zq7k" not in _viewer_values(settings)
    win = _new_window(qtbot, settings)
    try:
        QApplication.processEvents()
        qtbot.wait(50)
        assert win.views() == []
        assert _session(win).closed_tabs == []                 # la pila delle chiusure è solo in memoria
    finally:
        _cleanup(win)


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
    qtbot.waitUntil(lambda: ctl.last_initial_view is not None and ctl.last_initial_view.fullscreen, timeout=3000)
    assert not viewer_window.isFullScreen()


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
    # il ripristino viene dalla pila delle chiusure, solo in memoria
    assert not viewer_window.settings.contains("viewer/positions")
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
    viewer_window.show_backstage()
    names = []

    def walk(actions):
        for a in actions:
            names.append(a.text())
            if a.menu():
                walk(a.menu().actions())
    walk(viewer_window.backstage.all_actions())
    walk(a for m in viewer_window.modes.values() for a in m.toolbar.actions())
    viewer_window.hide_backstage()
    for text in ("Riapri scheda chiusa", "Chiudi le altre schede", "Chiudi le schede a destra",
                 "Chiudi tutte le schede", "Ignora le impostazioni di apertura dei documenti"):
        assert text in names


def test_switching_among_50_tabs_shows_page_quickly(viewer_window, tmp_path, qtbot):
    """VIS-30/NFR-07: con 50 documenti aperti, la pagina della scheda attivata è visibile entro
    200 ms (mediana; gli ascoltatori del viewer non devono rallentare il cambio di scheda)."""
    import statistics
    import time

    from PySide6.QtWidgets import QApplication
    views = []
    for i in range(50):
        path = tmp_path / f"doc{i}.pdf"
        doc = pymupdf.open()
        for p in range(5):
            doc.new_page().insert_text((72, 100), f"Documento {i} pagina {p + 1}", fontsize=14)
        doc.save(str(path))
        views.append(viewer_window.open_path(str(path)))
    QApplication.processEvents()
    times = []
    for i in (3, 17, 42, 8, 29, 49, 0, 33, 12, 21):
        view = views[i]
        t = time.perf_counter()
        viewer_window.ctx.activate_view(view)
        qtbot.waitUntil(lambda v=view: any(k[0] == v.current_page for k in v._cache), timeout=2000)
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < perf_limit(0.2), [round(x * 1000) for x in times]


def test_closing_with_two_modified_documents_asks_once(viewer_window, tmp_path, monkeypatch):
    """Dati 2 documenti modificati, alla chiusura dell'applicazione compare un solo dialogo che li
    elenca entrambi (Salva tutti / Non salvare / Annulla)."""
    from PySide6.QtWidgets import QMessageBox
    a = viewer_window.open_path(_make(tmp_path, "uno.pdf", pages=2))
    b = viewer_window.open_path(_make(tmp_path, "due.pdf", pages=2))
    for v in (a, b):
        with v.document.change("Testo") as pdf:
            pdf[0].insert_text((72, 300), "x")
    shown = []

    def fake_exec(box):
        shown.append((box.text(), [btn.text() for btn in box.buttons()]))
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert not viewer_window.close()                 # Annulla (nessun pulsante scelto)
    assert len(shown) == 1
    text, buttons = shown[0]
    assert "uno.pdf" in text and "due.pdf" in text
    assert "Salva tutti" in buttons
    for v in (a, b):
        v.document._saved_state = v.document._state
