"""Regressioni dal collaudo end-to-end dell'app integrata."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules.pages import dialogs as D
from foliant.modules.pages import engine as E

from .conftest import numbered_pdf, page_texts, save_numbered


@pytest.fixture
def accept(monkeypatch):
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


def _form_pdf(n=3, with_image_on=1):
    doc = numbered_pdf(n)
    for i in range(n):
        w = pymupdf.Widget()
        w.field_name = f"Campo{i + 1}"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(50, 600, 200, 630)
        w.field_value = f"v{i + 1}"
        doc[i].add_widget(w)
    import os
    pix = pymupdf.Pixmap(pymupdf.csRGB, 300, 300, os.urandom(300 * 300 * 3), False)
    doc[with_image_on].insert_image(pymupdf.Rect(0, 200, 300, 500), pixmap=pix)
    return pymupdf.open(stream=doc.tobytes())


def test_extract_open_and_delete_acts_on_source(pages_window, accept):
    window, view = pages_window

    def setup(d):
        d.scope.set_range("2-3")
        d.rb_single.setChecked(True)
        d.open_result.setChecked(True)
        d.delete_after.setChecked(True)
    accept(setup)
    assert D.open_extract_dialog(window.ctx, view, [0])
    new = window.current_view()
    assert new is not view
    with E.ENGINE_LOCK:
        assert page_texts(new.document.pdf) == ["P2", "P3"]
        assert view.document.pdf.page_count == 8


def test_delete_page_with_field_and_image_leaves_no_orphans():
    doc = _form_pdf(3, with_image_on=1)
    E.delete_pages(doc, [1])
    out = pymupdf.open(stream=doc.tobytes(garbage=3, deflate=True))
    live = {out[i].xref for i in range(out.page_count)}
    form = out.xref_get_key(out.pdf_catalog(), "AcroForm")
    fx = int(form[1].split()[0]) if form[0] == "xref" else None
    fields = out.xref_get_key(fx, "Fields")[1] if fx else out.xref_get_key(out.pdf_catalog(), "AcroForm/Fields")[1]
    names = sorted(w.field_name for i in range(out.page_count) for w in out[i].widgets())
    assert names == ["Campo1", "Campo3"]
    assert len(E._split_array(fields)) == 2
    pages_objs = [x for x in range(1, out.xref_length()) if out.xref_get_key(x, "Type")[1] == "/Page"]
    assert set(pages_objs) == live
    assert not any(out.xref_get_key(x, "Subtype")[1] == "/Image" for x in range(1, out.xref_length()))


def test_extract_keeps_acroform():
    doc = _form_pdf(3)
    out = pymupdf.open(stream=E.extract_pages(doc, [0, 2]))
    assert out.is_form_pdf
    names = sorted(w.field_name for i in range(out.page_count) for w in out[i].widgets())
    assert names == ["Campo1", "Campo3"]
    form = out.xref_get_key(out.pdf_catalog(), "AcroForm")
    assert form[0] != "null"


def test_locked_second_document_in_pages_mode(window, ten_pdf, tmp_path):
    window.open_path(ten_pdf)
    window.set_mode("pagine")
    path = tmp_path / "bloccato.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "x")
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
           permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
    window.open_path(str(path))
    c = window.ctx.pages_controller
    for a in (c.act_delete, c.act_rotate_right, c.act_insert_blank, c.act_move_to):
        assert not a.isEnabled(), a.text()
        assert "assemblaggio" in a.toolTip()


def test_duplicate_has_no_ctrl_d(pages_window):
    window, view = pages_window
    c = window.ctx.pages_controller
    assert not c.act_duplicate.shortcuts()


def test_delete_without_confirmation(pages_window, monkeypatch):
    window, view = pages_window
    c = window.ctx.pages_controller
    asked = []
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: asked.append(a) or False)
    c.organizer.grid.select_pages([0])
    c.act_delete.trigger()
    assert view.page_count == 9 and not asked
    view.document.undo()
    assert view.page_count == 10


def test_merge_sort_buttons_not_truncated(window):
    from PySide6.QtWidgets import QPushButton
    dlg = D.MergeDialog(window, window.ctx, [])
    dlg.show()
    for b in dlg.findChildren(QPushButton):
        if b.text().startswith("Ordina"):
            assert b.width() >= b.fontMetrics().horizontalAdvance(b.text()) + 40


def test_split_disabled_single_page(window, tmp_path):
    window.open_path(save_numbered(tmp_path / "uno.pdf", 1))
    window.set_mode("pagine")
    c = window.ctx.pages_controller
    c.update_actions()
    assert not c.act_split.isEnabled()


@pytest.mark.parametrize("dialog_name", ["PageNumbersDialog", "HeaderFooterDialog", "BatesDialog"])
def test_restamping_replaces_existing_series(window, tmp_path, dialog_name):
    """Rinumerare dopo aver eliminato una pagina non lascia due serie sovrapposte (PAG-33/34/35)."""
    from foliant.modules.pages import stamp
    from foliant.modules.pages import stamp_dialogs as SD
    view = window.open_path(save_numbered(tmp_path / "cinque.pdf", 5))
    cls = getattr(SD, dialog_name)

    def make():
        dlg = cls(window, view.document, [0])
        dlg.scope.set_all() if hasattr(dlg.scope, "set_all") else None
        if dialog_name == "PageNumbersDialog":
            dlg.fmt.setCurrentText("Pagina {n} di {total}")
        elif dialog_name == "HeaderFooterDialog":
            dlg.fields["footer_center"].setText("Pagina {n} di {total}")
        return dlg
    first = make()
    assert first.replace_box is not None and first.replace_box.isHidden()
    first.apply()
    with view.document.change("elimina") as pdf:
        E.delete_pages(pdf, [4])
    second = make()
    assert not second.replace_box.isHidden() and second.replace_box.isChecked()
    second.apply()
    with E.ENGINE_LOCK:
        pdf = view.document.pdf
        kinds = stamp.list_watermarks(pdf)
        for i in range(pdf.page_count):
            text = pdf[i].get_text()
            assert "di 5" not in text
            assert sum(1 for k in kinds.get(i, []) if k == cls.REPLACE_KIND) == 1


def test_page_numbers_pag33_criteria():
    """PAG-33: dalla pagina 2 con numero 1; posizione speculare; stili romani/lettere."""
    from foliant.modules.pages import stamp
    doc = numbered_pdf(10)
    stamp.add_page_numbers(doc, range(1, 10), fmt="{pagina}", start=1, position="bottom-center")
    assert "1" not in doc[0].get_text().split()[1:]
    assert doc[1].get_text().split()[-1] == "1" and doc[9].get_text().split()[-1] == "9"
    doc = numbered_pdf(4)
    stamp.add_page_numbers(doc, None, fmt="{n}", position="bottom-center", mirror="outer")
    w = doc[0].rect.width
    for i in range(4):
        x = [b for b in doc[i].get_text("words") if b[4] == str(i + 1) and b[1] > 700][0][0]
        assert (x > w / 2) == (i % 2 == 0)      # dispari (pagine 1, 3) a destra
    doc = numbered_pdf(3)
    stamp.add_page_numbers(doc, None, fmt="- {n} -", style="r")
    assert "- iii -" in doc[2].get_text()
    assert stamp.format_number(27, "A") == "AA" and stamp.format_number(4, "R") == "IV"


def _cyclic_pdf(path):
    d = numbered_pdf(2)
    root = int(d.xref_get_key(d.pdf_catalog(), "Pages")[1].split()[0])
    kids = d.xref_get_key(root, "Kids")[1]
    d.xref_set_key(root, "Kids", kids[:-1] + f" {root} 0 R]")
    d.xref_set_key(root, "Count", "3")
    d.save(str(path), garbage=0)
    return str(path)


@pytest.mark.qt_no_exception_capture   # altri moduli (security, viewer) sollevano eccezioni su questo file
def test_cyclic_page_tree_handled(window, tmp_path, monkeypatch):
    """Ciclo nell'albero delle pagine: segnaposto «Pagina non leggibile», messaggi chiari, nessuna eccezione."""
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: errors.append(a[2]))
    view = window.open_path(_cyclic_pdf(tmp_path / "ciclo.pdf"))
    window.sidebar.hide()        # le miniature del viewer non fanno parte di questo modulo
    window.set_mode("pagine")
    c = window.ctx.pages_controller
    grid = c.organizer.grid
    grid.flush_rendering()
    assert grid.failed(2) or grid.thumbnail(2) is None
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem
    img = QImage(400, 400, QImage.Format.Format_RGB32)
    painter = QPainter(img)
    opt = QStyleOptionViewItem()
    opt.rect = grid.visualRect(grid.model().index(2))
    grid.itemDelegate().paint(painter, opt, grid.model().index(2))   # nessuna eccezione, stato ripristinato
    assert painter.end()
    grid.selectAll()
    c.rotate(90)
    grid.select_pages([2])
    c.delete()
    assert errors and all("danneggiato" in e for e in errors)
    grid.select_pages([0, 1])
    assert c.copy_pages()
    assert view.page_count == 3


def test_save_guard_warns_on_hidden_cropped_text(window, tmp_path, monkeypatch):
    """PAG-25: al salvataggio avvisa che il testo fuori dal ritaglio resta nel file.
    «No» annulla e al salvataggio successivo chiede di nuovo; dopo «Sì» non chiede più."""
    import hashlib
    path = tmp_path / "rit.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 780), "nascosto in basso", fontsize=12)
    d[0].set_cropbox(pymupdf.Rect(0, 0, 595, 600))
    d.save(str(path))
    view = window.open_path(str(path))
    with view.document.change("modifica") as pdf:
        pdf[0].insert_text((72, 100), "nuovo")
    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    answers = []
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: answers.append(t) or answers_queue.pop(0))
    answers_queue = [False, False, True]
    window.save_view(view)
    assert answers == ["Contenuto nascosto dal ritaglio"]
    assert hashlib.md5(path.read_bytes()).hexdigest() == md5
    window.save_view(view)
    assert len(answers) == 2 and hashlib.md5(path.read_bytes()).hexdigest() == md5
    assert window.save_view(view)
    assert len(answers) == 3 and hashlib.md5(path.read_bytes()).hexdigest() != md5
    window.save_view(view)          # già confermato per questa versione
    assert len(answers) == 3


def test_unlock_uses_document_api(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    path = tmp_path / "bloccato.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "x")
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
           permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(path))
    window.set_mode("pagine")
    c = window.ctx.pages_controller
    assert not c.can_assemble() and not c.act_rotate_right.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("padrone", True))
    c.unlock()
    assert view.document.allows(pymupdf.PDF_PERM_ASSEMBLE)
    assert c.act_rotate_right.isEnabled()
    c.organizer.grid.select_pages([0])
    c.act_rotate_right.trigger()
    view.document.undo()
    view.document.redo()
    assert c.can_assemble()


def test_selected_pages_provider_only_in_pages_mode(pages_window):
    window, view = pages_window
    c = window.ctx.pages_controller
    c.organizer.grid.select_pages([2, 5])
    assert window.ctx.selected_pages(view) == [2, 5]
    window.set_mode("leggi")
    assert window.selected_pages_provider is c._previous_provider   # torna quello del viewer
    window.set_mode("pagine")
    assert window.selected_pages_provider == c._selection_provider


def test_signed_document_single_warning_with_sign_module(pages_window, monkeypatch):
    """Con il modulo Firma caricato l'avviso di invalidazione è uno solo: quello di Firma."""
    window, view = pages_window
    c = window.ctx.pages_controller
    assert c.sign_handles_warnings()
    monkeypatch.setattr(type(c), "is_signed", lambda self, d: True)
    asked = []
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: asked.append(t) or True)
    assert c.confirm_structural(view.document)
    assert "Documento firmato" not in asked


def test_actions_follow_document_access_changes(window, tmp_path):
    """Sblocco fatto da un altro modulo (document.unlock): i comandi di Pagine si riattivano subito."""
    path = tmp_path / "bloccato.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "x")
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
           permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(path))
    window.set_mode("pagine")
    c = window.ctx.pages_controller
    assert not c.act_rotate_right.isEnabled()
    assert view.document.unlock("padrone")
    assert c.act_rotate_right.isEnabled() and c.act_delete.isEnabled()
