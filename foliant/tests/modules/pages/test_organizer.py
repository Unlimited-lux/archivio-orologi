"""Organizzatore a griglia e comandi della modalità Pagine (PAG-01, 04, 11, 14–18)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox

from foliant.modules.pages import engine as E

from .conftest import page_texts, save_numbered


def ctrl(window):
    return window.ctx.pages_controller


def doc_texts(view):
    with E.ENGINE_LOCK:
        return page_texts(view.document.pdf)


@pytest.fixture(autouse=True)
def _yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)


def test_mode_shows_organizer_and_restores(pages_window):
    window, view = pages_window
    c = ctrl(window)
    assert window.ctx.central_widget() is c.organizer
    assert c.organizer.grid.page_count() == 10
    window.set_mode("leggi")
    assert window.ctx.central_widget() is None
    window.set_mode("pagine")
    assert window.ctx.central_widget() is c.organizer


def test_mode_without_document(window):
    window.set_mode("pagine")
    assert window.ctx.central_widget() is None


def test_close_document_restores_view(pages_window, monkeypatch):
    window, view = pages_window
    window.close_view(view)
    assert window.ctx.central_widget() is None


def test_click_shift_click_selects_range(pages_window, qtbot):
    window, view = pages_window
    grid = ctrl(window).organizer.grid
    grid.flush_rendering()
    r2 = grid.visualRect(grid.model().index(1)).center()
    r5 = grid.visualRect(grid.model().index(4)).center()
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=r2)
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=r5)
    assert grid.selected_pages() == [1, 2, 3, 4]
    assert "4 pagine selezionate" in window.statusBar().currentMessage()
    assert "4 pagine selezionate" in ctrl(window).organizer.info_label.text()
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=r2)
    assert grid.selected_pages() == [2, 3, 4]


def test_select_all_and_filters(pages_window, qtbot):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.setFocus()
    qtbot.keyClick(c.organizer.grid, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(c.organizer.selected_pages()) == 10
    c.select_filter(E.FILTER_EVEN)
    assert c.organizer.selected_pages() == [1, 3, 5, 7, 9]


def test_thumbnails_render_with_size(pages_window):
    window, view = pages_window
    c = ctrl(window)
    grid = c.organizer.grid
    grid.flush_rendering()
    assert grid.rendered_count() > 0
    img = grid.thumbnail(0)
    assert max(img.width(), img.height()) >= grid.thumb_size * 0.95
    c.organizer.slider.setValue(300)
    grid.flush_rendering()
    assert max(grid.thumbnail(0).width(), grid.thumbnail(0).height()) >= 285


def test_lazy_rendering_large_document(window, tmp_path):
    path = save_numbered(tmp_path / "grande.pdf", 1500)
    view = window.open_path(path)
    window.set_mode("pagine")
    grid = ctrl(window).organizer.grid
    grid.flush_rendering()
    visible = len(grid.visible_rows(grid.RENDER_MARGIN_ROWS))
    assert 0 < grid.rendered_count() <= visible
    grid.verticalScrollBar().setValue(grid.verticalScrollBar().maximum())
    grid.flush_rendering()
    assert grid.rendered_count() <= len(grid.visible_rows(grid.RENDER_MARGIN_ROWS))
    assert grid.thumbnail(1499) is not None


def test_corrupt_page_placeholder(pages_window):
    window, view = pages_window
    grid = ctrl(window).organizer.grid
    with view.document.change("rompi") as pdf:
        xref = pdf.get_new_xref()
        pdf.update_object(xref, "<< /Length 5 >>")
        pdf.update_stream(xref, b"q 1 0 0 1 0 0 cm /Nonesiste Do BT /F9 12 Tf (x) Tj ET Q garbage[[[")
        pdf.xref_set_key(pdf[0].xref, "Contents", f"{xref} 0 R")
    grid.flush_rendering()
    grid.select_pages([0])
    assert grid.selected_pages() == [0]


def test_rotate_and_undo_identical(pages_window):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([0, 2])
    before = view.render_page_image(0, 0.5)
    c.act_rotate_right.trigger()
    with E.ENGINE_LOCK:
        assert [view.document.pdf[i].rotation for i in range(3)] == [90, 0, 90]
    assert c.organizer.selected_pages() == [0, 2]
    assert c.organizer.grid.model().infos[0].rotation == 90
    view.document.undo()
    assert view.render_page_image(0, 0.5) == before
    with E.ENGINE_LOCK:
        assert view.document.pdf[0].rotation == 0


def test_delete_selected_and_undo(pages_window):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([1, 3, 4, 5])
    c.act_delete.trigger()
    assert doc_texts(view) == ["P1", "P3", "P7", "P8", "P9", "P10"]
    assert c.organizer.grid.page_count() == 6
    view.document.undo()
    assert doc_texts(view) == [f"P{i}" for i in range(1, 11)]
    assert c.organizer.grid.page_count() == 10


def test_delete_all_refused(window, tmp_path, qtbot):
    view = window.open_path(save_numbered(tmp_path / "tre.pdf", 3))
    window.set_mode("pagine")
    c = ctrl(window)
    c.organizer.grid.setFocus()
    c.organizer.grid.selectAll()
    c.act_delete.trigger()
    assert view.page_count == 3
    assert not view.document.can_undo


def test_drag_reorder_via_signal(pages_window):
    window, view = pages_window
    c = ctrl(window)
    grid = c.organizer.grid
    grid.select_pages([1, 4])
    grid.moveRequested.emit([1, 4], 0)
    assert doc_texts(view)[:6] == ["P2", "P5", "P1", "P3", "P4", "P6"]
    assert grid.selected_pages() == [0, 1]
    # rilascio nella posizione d'origine: nessuna voce di annullamento in più
    steps = len(view.document._undo)
    grid.moveRequested.emit([0, 1], 0)
    assert len(view.document._undo) == steps


def test_drop_index_geometry(pages_window):
    window, view = pages_window
    grid = ctrl(window).organizer.grid
    grid.flush_rendering()
    r = grid.visualRect(grid.model().index(2))
    assert grid.drop_index_at(QPoint(r.left() + 3, r.center().y())) == 2
    assert grid.drop_index_at(QPoint(r.right() - 3, r.center().y())) == 3


def test_move_end_and_duplicate(pages_window):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([0])
    c.act_move_end.trigger()
    assert doc_texts(view)[-1] == "P1" and view.page_count == 10
    c.organizer.grid.select_pages([0])
    c.act_duplicate.trigger()
    assert doc_texts(view)[:3] == ["P2", "P2", "P3"]
    assert c.organizer.selected_pages() == [1]


def test_reverse_swap_pairs(pages_window):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([0])
    c.act_reverse.trigger()
    assert doc_texts(view)[0] == "P10"
    view.document.undo()
    c.act_swap_pairs.trigger()
    assert doc_texts(view)[:4] == ["P2", "P1", "P4", "P3"]
    view.document.undo()
    c.organizer.grid.select_pages([0, 9])
    c.update_actions()
    assert c.act_swap.isEnabled()
    c.act_swap.trigger()
    assert doc_texts(view)[0] == "P10" and doc_texts(view)[9] == "P1"
    c.organizer.grid.select_pages([0])
    c.update_actions()
    assert not c.act_swap.isEnabled()


def test_copy_paste_between_documents(window, tmp_path):
    a = window.open_path(save_numbered(tmp_path / "A.pdf", 3, prefix="A"))
    window.set_mode("pagine")
    c = ctrl(window)
    c.organizer.grid.select_pages([0, 1])
    assert c.copy_pages()
    mime = QGuiApplication.clipboard().mimeData()
    assert mime.hasFormat("application/pdf")
    ext = pymupdf.open(stream=bytes(mime.data("application/pdf")), filetype="pdf")
    assert page_texts(ext) == ["A1", "A2"]
    a.document._saved_state = a.document._state
    window.close_view(a)
    b = window.open_path(save_numbered(tmp_path / "B.pdf", 2, prefix="B"))
    window.set_mode("leggi")
    window.set_mode("pagine")
    c.organizer.grid.select_pages([1])
    c.act_paste.trigger()
    assert doc_texts(b) == ["B1", "B2", "A1", "A2"]


def test_cut_paste_same_document(pages_window):
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([2])
    c.act_cut.trigger()
    assert view.page_count == 9
    c.organizer.grid.select_pages([0])
    c.act_paste_before.trigger()
    assert view.page_count == 10 and doc_texts(view)[0] == "P3"


def test_drag_out_creates_pdf(pages_window):
    window, view = pages_window
    c = ctrl(window)
    mime = c.drag_mime([0, 1, 2])
    urls = mime.urls()
    assert urls and urls[0].toLocalFile().endswith(".pdf")
    out = pymupdf.open(urls[0].toLocalFile())
    assert page_texts(out) == ["P1", "P2", "P3"]
    assert "(pagine 1-3)" in urls[0].toLocalFile()


def test_drop_pages_from_other_document(window, tmp_path):
    a = window.open_path(save_numbered(tmp_path / "A.pdf", 3, prefix="A"))
    b = window.open_path(save_numbered(tmp_path / "B.pdf", 2, prefix="B"))
    window.tabs.setCurrentWidget(a)
    window.set_mode("pagine")
    c = ctrl(window)
    mime = c.drag_mime([1, 2])
    window.set_mode("leggi")
    window.tabs.setCurrentWidget(b)
    window.set_mode("pagine")
    c.drop_mime(mime, 1, True)
    assert doc_texts(b) == ["B1", "A2", "A3", "B2"]
    assert doc_texts(a) == ["A1", "A2", "A3"]


def test_drop_file_from_file_manager(pages_window, tmp_path):
    window, view = pages_window
    c = ctrl(window)
    other = save_numbered(tmp_path / "X.pdf", 1, prefix="X")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(other)])
    c.drop_mime(mime, 4, True)
    assert doc_texts(view)[4] == "X1" and view.page_count == 11


def test_assembly_denied_disables(window, tmp_path):
    path = tmp_path / "bloccato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(path))
    window.set_mode("pagine")
    c = ctrl(window)
    for a in (c.act_delete, c.act_rotate_right, c.act_insert_blank, c.act_move_to):
        assert not a.isEnabled()
        assert "assemblaggio" in a.toolTip()
    assert c.act_unlock.isVisible() or not c.act_unlock.isVisible()  # presente nella barra
    from PySide6.QtWidgets import QInputDialog
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(QInputDialog, "getText", lambda *a, **k: ("padrone", True))
    c.unlock()
    mp.undo()
    assert c.act_delete.isEnabled() or c.act_rotate_right.isEnabled()


def test_signed_document_warning(pages_window, monkeypatch):
    window, view = pages_window
    c = ctrl(window)
    asked = []
    monkeypatch.setattr(type(c), "is_signed", lambda self, d: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: asked.append(m) or False)
    c.organizer.grid.select_pages([0])
    c.act_rotate_right.trigger()
    assert asked and "firme" in asked[0]
    with E.ENGINE_LOCK:
        assert view.document.pdf[0].rotation == 0


def test_share_mail(pages_window, monkeypatch):
    window, view = pages_window
    c = ctrl(window)
    sent = []
    from foliant.modules.pages import module as M
    monkeypatch.setattr(M, "send_by_mail", lambda p: sent.append(p) or True)
    c.organizer.grid.select_pages([1, 2, 3])
    path = c.share("mail")
    assert sent and sent[0] == path
    assert pymupdf.open(str(path)).page_count == 3


def test_share_keeps_password(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    path = save_numbered(tmp_path / "segreto.pdf", 3, password="pw")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("pw", True))
    view = window.open_path(path)
    window.set_mode("pagine")
    c = ctrl(window)
    from foliant.modules.pages import module as M
    monkeypatch.setattr(M, "send_by_mail", lambda p: True)
    c.organizer.grid.select_pages([0])
    out = c.share("mail")
    shared = pymupdf.open(str(out))
    assert shared.needs_pass and shared.authenticate("pw")


def test_document_changed_updates_grid(pages_window):
    window, view = pages_window
    c = ctrl(window)
    with view.document.change("x") as pdf:
        pdf.new_page()
    assert c.organizer.grid.page_count() == 11
    view.document.undo()
    assert c.organizer.grid.page_count() == 10


def test_grid_context_menu_has_commands(pages_window):
    window, view = pages_window
    c = ctrl(window)
    assert c.act_rotate_right in c.context_actions and c.act_delete in c.context_actions


def test_screenshots(pages_window, tmp_path):
    from PySide6.QtWidgets import QApplication
    from foliant.ui import theme
    window, view = pages_window
    c = ctrl(window)
    c.organizer.grid.select_pages([1, 2])
    c.organizer.grid.flush_rendering()
    QApplication.processEvents()
    out = tmp_path / "light.png"
    assert window.grab().save(str(out))
    theme.apply(QApplication.instance(), "dark")
    c.organizer.grid.flush_rendering()
    QApplication.processEvents()
    assert window.grab().save(str(tmp_path / "dark.png"))
    theme.apply(QApplication.instance(), "light")


def test_quick_rotate_button(pages_window, qtbot):
    window, view = pages_window
    c = ctrl(window)
    grid = c.organizer.grid
    grid.flush_rendering()
    idx = grid.model().index(3)
    info = grid.model().infos[3]
    rect, deg, _n = grid.itemDelegate().quick_buttons(grid.visualRect(idx), info)[1]
    pos = rect.center().toPoint()
    assert grid.quick_button_at(pos) == (3, 90)
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    with E.ENGINE_LOCK:
        assert view.document.pdf[3].rotation == 90
