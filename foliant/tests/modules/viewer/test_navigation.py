"""VIS-12 (vai a pagina, etichette, comandi), VIS-13 (cronologia), VIS-19 (collegamenti)."""

from __future__ import annotations

import pytest

import pymupdf
from PySide6.QtCore import QPointF, Qt

from foliant.modules.viewer.navigation import describe_link


def _nav(window):
    return window.ctx.viewer_parts["navigation"]


def _linked_pdf(tmp_path, pages=45):
    path = tmp_path / "link.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
    doc[2].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 300, 300, 330), "page": 39,
                        "to": pymupdf.Point(0, 0)})
    doc.save(str(path))
    return str(path)


def _click(qtbot, view, page, point):
    view.flush_rendering()
    pos = view.page_transform(page).map(QPointF(*point)).toPoint()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def test_back_forward_after_link(viewer_window, tmp_path, qtbot):
    w = viewer_window
    view = w.open_path(_linked_pdf(tmp_path))
    nav = _nav(w)
    view.set_zoom(1.25)
    view.go_to_page(2)
    qtbot.wait(10)
    nav._refresh()
    before_scroll = view.verticalScrollBar().value()
    assert view.current_page == 2
    _click(qtbot, view, 2, (100, 315))
    assert view.current_page == 39
    nav._refresh()
    assert nav.act_back.isEnabled()
    assert nav.back()
    assert view.current_page == 2
    assert abs(view.verticalScrollBar().value() - before_scroll) <= 2
    assert abs(view.zoom - 1.25) < 1e-3
    assert nav.forward()
    assert view.current_page == 39


def test_scrolling_does_not_create_history(viewer_window, long_pdf, qtbot):
    w = viewer_window
    view = w.open_path(long_pdf)
    nav = _nav(w)
    for _ in range(10):
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 400)
        view.next_page()
    nav._refresh()
    assert view.current_page > 5
    assert not nav.act_back.isEnabled()


def test_new_jump_clears_forward(viewer_window, long_pdf):
    w = viewer_window
    view = w.open_path(long_pdf)
    nav = _nav(w)
    view.go_to_page(10)       # A -> B
    assert nav.back()
    view.go_to_page(20)       # salto verso C
    nav._refresh()
    assert not nav.act_forward.isEnabled()
    assert nav.act_back.isEnabled()


def test_shortcut_alt_left(viewer_window, long_pdf, qtbot):
    w = viewer_window
    view = w.open_path(long_pdf)
    nav = _nav(w)
    view.go_to_page(12)
    nav.act_back.trigger()
    assert view.current_page == 0
    assert nav.act_back.shortcut().toString() == "Alt+Left"


def test_go_to_labels(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "etichette.pdf"
    doc = pymupdf.open()
    for _ in range(110):
        doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                         {"startpage": 10, "prefix": "", "style": "D", "firstpagenum": 1}])
    doc.save(str(path))
    w = viewer_window
    view = w.open_path(str(path))
    nav = _nav(w)
    assert nav.go_to_text("iv")
    assert view.current_page == 3
    nav._refresh()
    assert nav.label_box.text() == "iv (4 di 110)"
    assert nav.go_to_text("#15")
    assert view.current_page == 14
    nav._refresh()
    assert nav.label_box.text().startswith("5 (15")
    assert not nav.go_to_text("999")
    assert view.current_page == 14
    # dialogo simulato
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ii", True))
    nav.ask_go_to()
    assert view.current_page == 1


def test_next_disabled_on_last_page(viewer_window, sample_pdf):
    w = viewer_window
    view = w.open_path(sample_pdf)
    nav = _nav(w)
    view.go_to_page(view.page_count - 1)
    nav._refresh()
    assert not w.act_next.isEnabled()
    assert w.act_prev.isEnabled()
    view.go_to_page(0)
    nav._refresh()
    assert w.act_next.isEnabled()
    assert not w.act_prev.isEnabled()


def test_describe_link():
    assert describe_link({"kind": pymupdf.LINK_GOTO, "page": 11}) == "Vai a pagina 12"
    assert describe_link({"kind": pymupdf.LINK_GOTO, "page": 3}, ["i", "ii", "iii", "iv"]) == "Vai a pagina iv (4)"
    assert describe_link({"kind": pymupdf.LINK_URI, "uri": "https://esempio.it/x"}) == "https://esempio.it/x"
    assert "bloccato" in describe_link({"kind": pymupdf.LINK_LAUNCH, "file": "x.sh"})
    assert "altro.pdf" in describe_link({"kind": pymupdf.LINK_GOTOR, "file": "altro.pdf", "page": 1})


def test_gotor_opens_other_file_on_destination(viewer_window, tmp_path, qtbot):
    other = tmp_path / "altro.pdf"
    doc = pymupdf.open()
    for i in range(5):
        doc.new_page().insert_text((72, 100), f"Altro {i + 1}")
    doc.save(str(other))
    src = tmp_path / "sorgente.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_link({"kind": pymupdf.LINK_GOTOR, "from": pymupdf.Rect(72, 200, 300, 230), "file": "altro.pdf",
                      "page": 3, "to": pymupdf.Point(0, 0)})
    doc.save(str(src))
    w = viewer_window
    view = w.open_path(str(src))
    nav = _nav(w)
    with view.document.lock:
        link = view.document.pdf[0].get_links()[0]
    new = nav.open_remote(view, link)
    assert new is not None and new.document.path.name == "altro.pdf"
    assert new.current_page == 3
    assert len(w.views()) == 2


def test_gotor_missing_file(viewer_window, tmp_path, monkeypatch, sample_pdf):
    w = viewer_window
    view = w.open_path(sample_pdf)
    errors = []
    monkeypatch.setattr(w.ctx, "error", lambda t, m: errors.append(m))
    assert _nav(w).open_remote(view, {"kind": pymupdf.LINK_GOTOR, "file": "manca.pdf", "page": 0}) is None
    assert errors and "manca.pdf" in errors[0]


def _links_pdf(tmp_path):
    path = tmp_path / "cinque.pdf"
    doc = pymupdf.open()
    for _ in range(12):
        doc.new_page()
    page = doc[0]
    # inseriti in ordine sparso: l'ordine di lettura è per righe
    positions = [(300, 300), (72, 100), (300, 100), (72, 300), (72, 500)]
    for n, (x, y) in enumerate(positions):
        page.insert_text((x, y + 15), f"link{n}")
        page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(x, y, x + 100, y + 20),
                          "page": n + 5, "to": pymupdf.Point(0, 0)})
    doc.save(str(path))
    return str(path)


def test_tab_visits_links_in_reading_order(viewer_window, tmp_path, qtbot):
    view = viewer_window.open_path(_links_pdf(tmp_path))
    nav = _nav(viewer_window)
    view.setFocus()
    visited = []
    for _ in range(5):
        qtbot.keyClick(view, Qt.Key.Key_Tab)
        visited.append(nav.focused_link(view)["page"])
    assert visited == [6, 7, 8, 5, 9]
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert view.current_page == 9
    assert nav.state(view).history.can_back


def test_link_order_tabs_structure():
    from foliant.modules.viewer.navigation import link_order
    links = [{"from": pymupdf.Rect(300, 100, 400, 120)}, {"from": pymupdf.Rect(72, 100, 170, 120)}]
    assert link_order(links, None) == [1, 0]
    assert link_order(links, "/S") == [0, 1]


def test_detect_url_in_text(viewer_window, tmp_path, qtbot, monkeypatch):
    from foliant.modules.viewer.navigation import detect_url
    assert detect_url("www.esempio.it") == "https://www.esempio.it"
    assert detect_url("info@orologi.it,") == "mailto:info@orologi.it"
    assert detect_url("parola") is None
    path = tmp_path / "url.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Visita www.esempio.it oggi", fontsize=14)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    nav = _nav(viewer_window)
    asked = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    word = [w for w in view.words(0) if w[4] == "www.esempio.it"][0]
    point = ((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
    _click(qtbot, view, 0, point)
    qtbot.wait(20)
    assert asked == []              # preferenza disattiva
    nav.act_detect_urls.trigger()
    _click(qtbot, view, 0, point)
    qtbot.waitUntil(lambda: bool(asked), timeout=1000)
    assert "https://www.esempio.it" in asked[0]


def _gotor_source(tmp_path):
    other = tmp_path / "altro2.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Altro")
    doc.save(str(other))
    src = tmp_path / "sorgente2.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_link({"kind": pymupdf.LINK_GOTOR, "from": pymupdf.Rect(72, 200, 300, 230),
                                "file": "altro2.pdf", "page": 0, "to": pymupdf.Point(0, 0)})
    doc.save(str(src))
    return str(src)


@pytest.fixture
def only_viewer_link_handlers():
    """Nell'app completa Proteggi intercetta i GoToR con priorità alta: qui si prova solo il viewer."""
    from foliant.ui import tools
    saved = list(tools.LINK_HANDLERS)
    tools.LINK_HANDLERS[:] = [t for t in saved if t[0] <= 0]
    yield
    tools.LINK_HANDLERS[:] = saved


def test_gotor_click_goes_through_link_handlers(viewer_window, tmp_path, qtbot, only_viewer_link_handlers):
    view = viewer_window.open_path(_gotor_source(tmp_path))
    _click(qtbot, view, 0, (100, 215))
    qtbot.waitUntil(lambda: len(viewer_window.views()) == 2, timeout=2000)


def test_gotor_blocked_by_higher_priority_handler(viewer_window, tmp_path, qtbot, only_viewer_link_handlers):
    blocked = []

    def guard(view, link):
        if link.get("kind") == pymupdf.LINK_GOTOR:
            blocked.append(link["file"])
            return True
        return False

    viewer_window.ctx.add_link_handler(guard, priority=100)
    try:
        view = viewer_window.open_path(_gotor_source(tmp_path))
        _click(qtbot, view, 0, (100, 215))
        qtbot.waitUntil(lambda: blocked == ["altro2.pdf"], timeout=3000)
        assert len(viewer_window.views()) == 1   # nessuna nuova scheda
    finally:
        from foliant.ui.tools import remove_link_handler
        remove_link_handler(guard)


def test_back_returns_to_origin_document_tab(viewer_window, tmp_path, qtbot):
    src = _gotor_source(tmp_path)
    view = viewer_window.open_path(src)
    nav = _nav(viewer_window)
    with view.document.lock:
        link = view.document.pdf[0].get_links()[0]
    new = nav.open_remote(view, link)
    assert viewer_window.current_view() is new
    nav._refresh()
    assert nav.act_back.isEnabled()
    assert nav.back()
    assert viewer_window.current_view() is view       # di nuovo sulla scheda d'origine
    viewer_window.close_view(view)                    # scheda d'origine chiusa: voce saltata
    viewer_window.tabs.setCurrentWidget(new)
    assert not nav.back()


def test_ux18_rotate_view_and_rulers_shortcuts(viewer_window, sample_pdf, qtbot):
    from PySide6.QtGui import QKeySequence
    w = viewer_window
    nav = _nav(w)
    assert w.act_rotate_view.shortcut() == QKeySequence("Ctrl+Shift++")
    assert nav.act_rotate_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")
    assert w.act_rulers.shortcut() == QKeySequence("Ctrl+R")
    view = w.open_path(sample_pdf)
    nav.act_rotate_ccw.trigger()
    assert view.view_rotation == 270
    w.act_rotate_view.trigger()
    assert view.view_rotation == 0
    # in Visualizza, dove ci sono i pulsanti, valgono come in Leggi
    w.set_mode("visualizza")
    assert nav.act_rotate_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")
    # nelle altre modalità le combinazioni restano libere (Pagine le usa per ruotare le pagine)
    other = [m for m in w.modes if m not in ("leggi", "visualizza")]
    if other:
        w.set_mode(other[0])
        assert nav.act_rotate_ccw.shortcut().isEmpty()
        w.set_mode("leggi")
        assert nav.act_rotate_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")


def test_many_changes_refresh_navigation_once(viewer_window, sample_pdf, monkeypatch):
    """Prestazioni: più modifiche nello stesso giro di eventi → un solo aggiornamento."""
    from PySide6.QtWidgets import QApplication
    view = viewer_window.open_path(sample_pdf)
    nav = viewer_window.ctx.viewer_parts["navigation"]
    QApplication.processEvents()
    calls = []
    real = nav._do_refresh
    monkeypatch.setattr(nav, "_do_refresh", lambda: calls.append(1) or real())
    for i in range(3):
        with view.document.change("Testo") as pdf:
            pdf[0].insert_text((72, 300 + 10 * i), "x")
    QApplication.processEvents()
    assert len(calls) == 1
    view.document._saved_state = view.document._state


def test_ux18_rotate_tooltips_follow_language(settings, qtbot, monkeypatch):
    """NFR-25: in inglese i suggerimenti di «Ruota vista» non mostrano «Maiusc»."""
    from foliant import i18n
    from foliant.ui.main_window import MainWindow
    monkeypatch.setattr(i18n, "_language", "en")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    nav = win.ctx.viewer_parts["navigation"]
    tips = [nav.act_rotate_ccw.toolTip()]
    if getattr(win, "act_rotate_view", None) is not None:
        tips.append(win.act_rotate_view.toolTip())
    assert all("Maiusc" not in t and "Shift" in t for t in tips), tips
    assert nav.act_rotate_ccw.toolTip().startswith("Rotate view")


def test_ux18_rotate_tooltips_italian(viewer_window):
    nav = viewer_window.ctx.viewer_parts["navigation"]
    assert "Maiusc" in nav.act_rotate_ccw.toolTip()


def test_closed_view_not_retained_by_navigation(viewer_window, sample_pdf):
    """NFR-06: dopo la chiusura della scheda la navigazione non trattiene la vista (chiusure con
    il metodo originale di go_to_page, filtri d'evento, disegno del focus, stato)."""
    import gc
    view = viewer_window.open_path(sample_pdf)
    view.go_to_page(2)
    assert "go_to_page" in view.__dict__            # sostituita mentre la scheda è aperta
    nav = _nav(viewer_window)
    viewer_window.close_view(view)
    gc.collect()
    assert "go_to_page" not in view.__dict__ and not view.overlays.count(nav._paint_focus)
    assert view not in nav._states
    from foliant.modules.viewer import navigation as nav_mod
    holders = [r for r in gc.get_referrers(view)
               if (callable(r) and getattr(r, "__module__", "") == nav_mod.__name__)
               or (type(r).__name__ == "cell")]
    for cell in [r for r in holders if type(r).__name__ == "cell"]:
        owners = [o for o in gc.get_referrers(cell)
                  if callable(o) and getattr(o, "__module__", "") == nav_mod.__name__]
        assert not owners, owners
    assert not [r for r in holders if callable(r)], holders
