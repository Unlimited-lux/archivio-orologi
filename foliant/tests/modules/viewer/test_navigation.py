"""VIS-12 (vai a pagina, etichette, comandi), VIS-13 (cronologia), VIS-19 (collegamenti)."""

from __future__ import annotations

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
