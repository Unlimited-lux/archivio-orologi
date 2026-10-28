"""Dialoghi strutturali: inserisci, estrai, dividi, unisci, duplica, sposta, fascicola, sostituisci."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules.pages import dialogs as D
from foliant.modules.pages import engine as E

from .conftest import page_texts, save_numbered


def texts(view):
    with E.ENGINE_LOCK:
        return page_texts(view.document.pdf)


@pytest.fixture
def accept(monkeypatch):
    """Fa accettare i dialoghi: `accept(fn)` imposta i widget prima della conferma."""
    hooks = []

    def fake_exec(dlg):
        for h in hooks:
            h(dlg)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    return hooks.append


def test_page_scope_validation(window, ten_pdf):
    view = window.open_path(ten_pdf)
    scope = D.PageScope(view.document, [0], 0)
    scope.set_range("1-3, 8-")
    assert scope.is_valid() and scope.pages() == [0, 1, 2, 7, 8, 9]
    assert "6" in scope.status.text()
    scope.set_range("4-12")
    assert not scope.is_valid() and "12" in scope.error()
    scope.set_range("1-6")
    scope.set_filter(E.FILTER_ODD)
    assert scope.pages() == [0, 2, 4]


def test_extract_dialog_disables_ok_on_error(window, ten_pdf):
    view = window.open_path(ten_pdf)
    dlg = D.ExtractDialog(window, view.document, [0])
    dlg.scope.set_range("4-12")
    assert not dlg.ok_button().isEnabled()
    dlg.scope.set_range("1-3")
    assert dlg.ok_button().isEnabled()


def test_insert_blank_dialog(pages_window, accept):
    window, view = pages_window
    accept(lambda d: (d.count.setValue(2), d.position.set_where("after")))
    assert D.open_insert_blank_dialog(window.ctx, view, [0])
    assert view.page_count == 12
    assert texts(view)[:4] == ["P1", "", "", "P2"]
    assert window.ctx.pages_controller.organizer.selected_pages() == [1, 2]
    view.document.undo()
    assert view.page_count == 10


def test_insert_blank_custom(pages_window, accept):
    window, view = pages_window

    def setup(d):
        d.size.setCurrentIndex(d.size.findData("custom"))
        d.width.setValue(100)
        d.height.setValue(200)
        d.orientation.setCurrentIndex(1)
        d.position.set_where("end")
    accept(setup)
    assert D.open_insert_blank_dialog(window.ctx, view, [0])
    with E.ENGINE_LOCK:
        r = view.document.pdf[10].rect
    assert abs(r.width / E.MM - 200) < 0.5 and abs(r.height / E.MM - 100) < 0.5


def test_insert_file_dialog(pages_window, accept, tmp_path):
    window, view = pages_window
    b = save_numbered(tmp_path / "B.pdf", 3, prefix="B")
    accept(lambda d: (d.src_range.setText("2-3"), d.position.set_where("after")))
    assert D.open_insert_file_dialog(window.ctx, view, [0], path=b)
    assert texts(view)[:4] == ["P1", "B2", "B3", "P2"]


def test_insert_file_wrong_password_no_change(pages_window, accept, tmp_path, monkeypatch):
    window, view = pages_window
    from PySide6.QtWidgets import QInputDialog
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B", password="giusta")
    answers = iter([("sbagliata", True), ("", False)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    assert not D.open_insert_file_dialog(window.ctx, view, [0], path=b)
    assert view.page_count == 10 and not view.document.can_undo


def test_insert_file_interleaved(window, accept, tmp_path):
    view = window.open_path(save_numbered(tmp_path / "A.pdf", 3, prefix="A"))
    b = save_numbered(tmp_path / "B.pdf", 3, prefix="B")
    accept(lambda d: (d.interleave.setChecked(True), d.every.setValue(1), d.position.set_where("start")))
    assert D.open_insert_file_dialog(window.ctx, view, [0], path=b)
    assert texts(view) == ["A1", "B1", "A2", "B2", "A3", "B3"]


def test_extract_single_opens_tab(pages_window, accept):
    window, view = pages_window
    accept(lambda d: d.scope.set_range("1-3,7"))
    assert D.open_extract_dialog(window.ctx, view, [0])
    new = window.current_view()
    assert new is not view and texts(new) == ["P1", "P2", "P3", "P7"]
    assert view.page_count == 10


def test_extract_each_page_files_and_delete(pages_window, accept, tmp_path, monkeypatch):
    window, view = pages_window
    monkeypatch.setattr(window.ctx, "ask_directory", lambda *a, **k: str(tmp_path))

    def setup(d):
        d.scope.set_range("2-3")
        d.rb_each.setChecked(True)
        d.delete_after.setChecked(True)
    accept(setup)
    assert D.open_extract_dialog(window.ctx, view, [0])
    assert sorted(p.name for p in tmp_path.glob("doc_p*.pdf")) == ["doc_p2.pdf", "doc_p3.pdf"]
    assert view.page_count == 8
    view.document.undo()
    assert view.page_count == 10


def test_split_dialog_every(pages_window, accept, tmp_path, monkeypatch):
    window, view = pages_window
    monkeypatch.setattr(window.ctx, "ask_directory", lambda *a, **k: str(tmp_path))
    accept(lambda d: (d.rbs["every"].setChecked(True), d.every.setValue(3)))
    assert D.open_split_dialog(window.ctx, view)
    files = sorted(tmp_path.glob("doc_parte*.pdf"))
    assert [pymupdf.open(str(f)).page_count for f in files] == [3, 3, 3, 1]


def test_split_dialog_preview_warning(window, ten_pdf):
    view = window.open_path(ten_pdf)
    dlg = D.SplitDialog(window, view.document)
    dlg.rbs["ranges"].setChecked(True)
    dlg.ranges.setText("1-2; 5-6")
    assert "2 file" in dlg.preview.text()
    assert "3-4, 7-10" in dlg.message.text()
    dlg.ranges.setText("1-20")
    assert not dlg.ok_button().isEnabled()


def test_merge_dialog(window, accept, tmp_path, monkeypatch):
    a = save_numbered(tmp_path / "A.pdf", 3, prefix="A")
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B")

    def setup(d):
        d.add_path(b)
        d.table.item(0, 2).setText("2-3")
        d.blank_odd.setChecked(False)
    accept(setup)
    assert D.open_merge_dialog(window.ctx, paths=[a])
    view = window.current_view()
    assert texts(view) == ["A2", "A3", "B1", "B2"]
    with E.ENGINE_LOCK:
        assert [t[1] for t in view.document.pdf.get_toc()] == ["A", "B"]


def test_merge_dialog_order_buttons(window, tmp_path):
    a = save_numbered(tmp_path / "A.pdf", 1, prefix="A")
    b = save_numbered(tmp_path / "B.pdf", 1, prefix="B")
    dlg = D.MergeDialog(window, window.ctx, [b, a])
    dlg.sort("name")
    assert [Path(r[0]).name for r in dlg.rows()] == ["A.pdf", "B.pdf"]
    dlg.table.setCurrentCell(0, 0)
    dlg.move(1)
    assert [Path(r[0]).name for r in dlg.rows()] == ["B.pdf", "A.pdf"]
    dlg.table.item(0, 2).setText("5")
    assert not dlg.ok_button().isEnabled()


def test_merge_dialog_empty_disabled(window):
    dlg = D.MergeDialog(window, window.ctx, [])
    assert not dlg.ok_button().isEnabled()


def test_duplicate_dialog(pages_window, accept):
    window, view = pages_window
    accept(lambda d: d.copies.setValue(2))
    assert D.open_duplicate_dialog(window.ctx, view, [1])
    assert texts(view)[:5] == ["P1", "P2", "P2", "P2", "P3"]


def test_move_dialog(pages_window, accept):
    window, view = pages_window
    accept(lambda d: d.target.setValue(1))
    assert D.open_move_dialog(window.ctx, view, [1, 4])
    assert texts(view)[:3] == ["P2", "P5", "P1"]


def test_collate_dialog(window, accept, tmp_path):
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for t in ("F1", "F2", "F3", "R3", "R2", "R1"):
        doc.new_page().insert_text((72, 100), t, fontsize=24)
    doc.save(str(path))
    view = window.open_path(str(path))
    accept(lambda d: d.reverse.setChecked(True))
    assert D.open_collate_dialog(window.ctx, view, [0])
    assert texts(view) == ["F1", "R1", "F2", "R2", "F3", "R3"]


def test_replace_dialog(window, accept, tmp_path):
    a = window.open_path(save_numbered(tmp_path / "A.pdf", 5, prefix="A", toc=[[1, "tre", 3]]))
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B")
    accept(lambda d: d.target.setText("3-4"))
    assert D.open_replace_dialog(window.ctx, a, [2], path=b)
    assert texts(a) == ["A1", "A2", "B1", "B2", "A5"]
    with E.ENGINE_LOCK:
        assert a.document.pdf.get_toc()[0][2] == 3
    a.document.undo()
    assert texts(a)[2] == "A3"


def test_scanner_none(pages_window, monkeypatch):
    window, view = pages_window
    errors = []
    monkeypatch.setattr(D, "list_scanners", lambda: [])
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    assert not D.open_scanner_dialog(window.ctx, view, [0])
    assert errors and "scanner" in errors[0].lower()


def test_scanner_fake_device(pages_window, accept, monkeypatch):
    window, view = pages_window
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2480, 3508), False)
    pix.clear_with(255)
    png = pix.tobytes("png")
    monkeypatch.setattr(D, "list_scanners", lambda: [("test:0", "Scanner di prova")])
    monkeypatch.setattr(D, "scan_pages", lambda dev, dpi, mode, **k: [png, png, png])
    accept(lambda d: d.position.set_where("start"))
    assert D.open_scanner_dialog(window.ctx, view, [0])
    assert view.page_count == 13
    with E.ENGINE_LOCK:
        r = view.document.pdf[0].rect
    assert abs(r.width - 595) <= 2 and abs(r.height - 842) <= 2
    view.document.undo()
    assert view.page_count == 10


def test_dialog_screenshots(window, ten_pdf, tmp_path):
    from PySide6.QtWidgets import QApplication
    from foliant.ui import theme
    view = window.open_path(ten_pdf)
    for mode in ("light", "dark"):
        theme.apply(QApplication.instance(), mode)
        for dlg in (D.ExtractDialog(window, view.document, [0, 1]), D.SplitDialog(window, view.document),
                    D.InsertBlankDialog(window, view.document, [0]), D.MergeDialog(window, window.ctx, [])):
            dlg.show()
            assert dlg.grab().save(str(tmp_path / f"{type(dlg).__name__}_{mode}.png"))
            dlg.close()
    theme.apply(QApplication.instance(), "light")
