"""Compilazione nella finestra: editor sovrapposti, Tab, caselle, barra, evidenziazione."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from foliant.modules import forms
from foliant.modules.forms import engine as E
from foliant.modules.forms import formats as fm

from .conftest import build_form


def open_form(window, path):
    view = window.ctx.open_path(str(path))
    assert view is not None
    view.flush_rendering()
    QApplication.processEvents()
    return view


def filler_of(view):
    return forms.instance().filler(view)


def click_field(qtbot, view, name, index=0):
    f = filler_of(view)
    f.state.refresh(force=True)
    info = [x for x in f.state.fields if x.name == name][index]
    r = view.page_to_view(info.page, info.rect)
    view.ensure_visible(info.page, info.rect)
    QApplication.processEvents()
    r = view.page_to_view(info.page, info.rect)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=r.center().toPoint())
    QApplication.processEvents()
    return info


def values(view):
    with view.document.lock:
        return E.form_values(view.document.pdf)


@pytest.fixture
def form_view(window, form_pdf):
    return open_form(window, form_pdf)


def test_mode_registered(window):
    assert window.ctx.mode("moduli") is not None


def test_bar_counts(form_view):
    f = filler_of(form_view)
    assert f.bar.isVisible()
    assert "6 campi" in f.text_of_bar() and "1 obbligatorio da compilare" in f.text_of_bar()


def test_no_bar_without_form(window, sample_pdf):
    view = open_form(window, sample_pdf)
    assert not filler_of(view).bar.isVisible()


def test_click_text_field_and_type(qtbot, form_view):
    click_field(qtbot, form_view, "nome")
    f = filler_of(form_view)
    assert isinstance(f.editor, QLineEdit) and f.editor.hasFocus()
    qtbot.keyClicks(f.editor, "Mario")
    qtbot.keyClick(f.editor, Qt.Key.Key_Return)
    assert values(form_view)["nome"] == "Mario"
    assert form_view.document.is_modified
    form_view.document.undo()
    assert values(form_view)["nome"] == ""


def test_escape_restores(qtbot, form_view):
    click_field(qtbot, form_view, "nome")
    f = filler_of(form_view)
    qtbot.keyClicks(f.editor, "Bozza")
    qtbot.keyClick(f.editor, Qt.Key.Key_Escape)
    assert f.editor is None
    assert values(form_view)["nome"] == ""


def test_tab_navigation_commits_and_wraps(qtbot, form_view):
    click_field(qtbot, form_view, "nome")
    f = filler_of(form_view)
    qtbot.keyClicks(f.editor, "Anna")
    qtbot.keyClick(f.editor, Qt.Key.Key_Tab)
    assert f.active.name == "cognome"
    assert values(form_view)["nome"] == "Anna"
    order = []
    for _ in range(8):
        order.append(f.active.name)
        f.focus_next()
    assert order[:8] == ["cognome", "privacy", "scelta", "scelta", "scelta", "paese", "colori", "nome"]
    assert f.active.name == "cognome"
    f.focus_next(back=True)
    assert f.active.name == "nome"
    f.focus_next(back=True)
    assert f.active.name == "colori"  # all'indietro dal primo si torna all'ultimo


def test_checkbox_click_and_space(qtbot, form_view):
    click_field(qtbot, form_view, "privacy")
    assert values(form_view)["privacy"] == "Yes"
    f = filler_of(form_view)
    qtbot.keyClick(f.editor, Qt.Key.Key_Space)
    assert values(form_view)["privacy"] == "Off"
    qtbot.keyClick(f.editor, Qt.Key.Key_Space)
    assert values(form_view)["privacy"] == "Yes"


def test_radio_click_and_arrows(qtbot, form_view):
    click_field(qtbot, form_view, "scelta", 0)
    assert values(form_view)["scelta"] == "A"
    f = filler_of(form_view)
    qtbot.keyClick(f.editor, Qt.Key.Key_Down)
    assert values(form_view)["scelta"] == "B"
    click_field(qtbot, form_view, "scelta", 1)  # clic sull'opzione attiva: si disattiva
    assert values(form_view)["scelta"] == "Off"


def test_combo_choose_display_text(qtbot, form_view):
    click_field(qtbot, form_view, "paese")
    f = filler_of(form_view)
    f.editor.setCurrentIndex(f.editor.findText("Francia"))
    f.commit_active()
    assert values(form_view)["paese"] == "FR"


def test_list_multiselect(qtbot, form_view):
    click_field(qtbot, form_view, "colori")
    f = filler_of(form_view)
    f.editor.item(0).setSelected(True)
    f.editor.item(2).setSelected(True)
    f.commit_active()
    assert values(form_view)["colori"] == ["rosso", "blu"]


def test_maxlen_paste(qtbot, window, tmp_path):
    pdf = build_form()
    x = [f for f in E.list_fields(pdf) if f.name == "cognome"][0]
    E.update_properties(pdf, 0, x.xref, maxlen=5)
    pdf.save(str(tmp_path / "m.pdf"))
    view = open_form(window, tmp_path / "m.pdf")
    click_field(qtbot, view, "cognome")
    f = filler_of(view)
    QApplication.clipboard().setText("ABCDEFGH")
    f.editor.paste()
    f.commit_active()
    assert values(view)["cognome"] == "ABCDE"


def test_multiline_enter_inserts_newline(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 300, 150), "note", multiline=True)
    pdf.save(str(tmp_path / "n.pdf"))
    view = open_form(window, tmp_path / "n.pdf")
    click_field(qtbot, view, "note")
    f = filler_of(view)
    assert isinstance(f.editor, QPlainTextEdit)
    qtbot.keyClicks(f.editor, "riga1")
    qtbot.keyClick(f.editor, Qt.Key.Key_Return)
    qtbot.keyClicks(f.editor, "riga2")
    f.commit_active()
    assert values(view)["note"] == "riga1\rriga2"


def _number_doc(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "A",
                   fmt=fm.Format(fm.NUMBER, 2, 2, 0, "€", False), validation=E.Validation(0, 100))
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 250, 110), "B")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 130, 250, 150), "T", calc=E.Calculation("SUM", ["A", "B"]))
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 170, 250, 190), "D", fmt=fm.Format(fm.DATE, pattern="dd/mm/yyyy"))
    E.apply_values(pdf, {"A": "2", "B": "3", "T": "5"})
    pdf.save(str(tmp_path / "num.pdf"))
    return tmp_path / "num.pdf"


def test_number_filter_validation_and_calc(qtbot, window, tmp_path):
    view = open_form(window, _number_doc(tmp_path))
    click_field(qtbot, view, "A")
    f = filler_of(view)
    f.editor.selectAll()
    qtbot.keyClicks(f.editor, "1a5")
    assert f.editor.text() == "15"
    qtbot.keyClick(f.editor, Qt.Key.Key_Tab)
    assert values(view)["A"] == "15" and values(view)["T"] == "18"
    click_field(qtbot, view, "A")
    f.editor.selectAll()
    qtbot.keyClicks(f.editor, "150")
    qtbot.keyClick(f.editor, Qt.Key.Key_Return)
    assert f.last_error and "100" in f.last_error
    assert values(view)["A"] == "15" and values(view)["T"] == "18"
    assert f.active is not None and f.active.name == "A"  # il focus resta sul campo


def test_date_normalized_and_rejected(qtbot, window, tmp_path):
    view = open_form(window, _number_doc(tmp_path))
    click_field(qtbot, view, "D")
    f = filler_of(view)
    qtbot.keyClicks(f.editor, "1-3-26")
    qtbot.keyClick(f.editor, Qt.Key.Key_Tab)
    assert values(view)["D"] == "01/03/2026"
    click_field(qtbot, view, "D")
    f.editor.selectAll()
    qtbot.keyClicks(f.editor, "31/02/2026")
    qtbot.keyClick(f.editor, Qt.Key.Key_Return)
    assert "gg/mm/aaaa" in (f.last_error or "") or "dd/mm/yyyy" in (f.last_error or "")
    assert values(view)["D"] == "01/03/2026"


def test_calendar_pick(qtbot, window, tmp_path, monkeypatch):
    view = open_form(window, _number_doc(tmp_path))
    module = forms.instance()
    monkeypatch.setattr(module, "exec_popup", lambda menu, pos: None)
    click_field(qtbot, view, "D")
    f = filler_of(view)
    f.open_calendar()
    cal = f.calendar
    from PySide6.QtCore import QDate
    d = QDate(cal.yearShown(), cal.monthShown(), 15)
    cal.clicked.emit(d)
    assert values(view)["D"] == f"15/{d.month():02d}/{d.year()}"


def test_password_not_saved(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "pin", password=True)
    pdf.save(str(tmp_path / "pw.pdf"))
    view = open_form(window, tmp_path / "pw.pdf")
    click_field(qtbot, view, "pin")
    f = filler_of(view)
    assert f.editor.echoMode() == QLineEdit.EchoMode.Password
    qtbot.keyClicks(f.editor, "segreto99")
    f.commit_active()
    out = tmp_path / "salvato.pdf"
    view.document.save(out)
    assert b"segreto99" not in out.read_bytes()
    assert E.form_values(pymupdf.open(str(out)))["pin"] == ""


def test_readonly_and_hidden_skipped(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "A")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 250, 110), "N", visibility="hidden")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 130, 250, 150), "R", readonly=True)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 170, 250, 190), "C")
    pdf.save(str(tmp_path / "h.pdf"))
    view = open_form(window, tmp_path / "h.pdf")
    click_field(qtbot, view, "A")
    f = filler_of(view)
    qtbot.keyClick(f.editor, Qt.Key.Key_Tab)
    assert f.active.name == "C"


def test_tab_skips_pages_without_fields(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "p1")
    E.create_field(pdf, 2, E.TEXT, pymupdf.Rect(50, 700, 250, 720), "p3")
    pdf.save(str(tmp_path / "t.pdf"))
    view = open_form(window, tmp_path / "t.pdf")
    click_field(qtbot, view, "p1")
    f = filler_of(view)
    qtbot.keyClick(f.editor, Qt.Key.Key_Tab)
    assert f.active.name == "p3" and f.active.page == 2
    QApplication.processEvents()
    r = view.page_to_view(2, f.active.rect)
    assert view.viewport().rect().contains(r.toAlignedRect())


def test_highlight_pixels_and_not_saved(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    for i in range(5):
        E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50 + 40 * i, 250, 70 + 40 * i), f"c{i}",
                       required=i < 2, fill_color=None, border_width=0)
    path = tmp_path / "hl.pdf"
    pdf.save(str(path))
    view = open_form(window, path)
    f = filler_of(view)
    f.toggle_highlight(True)
    view.flush_rendering()
    img = view.viewport().grab().toImage()
    for i in range(5):
        r = view.page_to_view(0, pymupdf.Rect(50, 50 + 40 * i, 250, 70 + 40 * i))
        c = img.pixelColor(r.center().toPoint())
        assert c != Qt.GlobalColor.white and c.blue() > c.red(), (i, c.name())
    on = view.document.to_bytes(no_new_id=True)
    f.toggle_highlight(False)
    assert view.document.to_bytes(no_new_id=True) == on
    # preferenza persistente
    f.toggle_highlight(True)
    view2 = open_form(window, _number_doc(tmp_path))
    assert filler_of(view2).highlight


def test_next_required_cycles(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    for i in range(3):
        E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50 + 40 * i, 250, 70 + 40 * i), f"c{i}", required=i != 1)
    pdf.save(str(tmp_path / "r.pdf"))
    view = open_form(window, tmp_path / "r.pdf")
    f = filler_of(view)
    assert [f.next_required().name for _ in range(3)] == ["c0", "c2", "c0"]


def test_blocked_by_permissions(qtbot, window, tmp_path):
    pdf = build_form()
    path = tmp_path / "p.pdf"
    pdf.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = open_form(window, path)
    f = filler_of(view)
    assert "permessi" in f.text_of_bar()
    click_field(qtbot, view, "nome")
    assert f.editor is None


def test_orphan_widgets_relinked_on_save(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "A")
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/Fields", "[]")
    path = tmp_path / "o.pdf"
    pdf.save(str(path))
    view = open_form(window, path)
    click_field(qtbot, view, "A")
    f = filler_of(view)
    qtbot.keyClicks(f.editor, "x")
    f.commit_active()
    out = tmp_path / "o2.pdf"
    view.document.save(out)
    assert E.orphan_count(pymupdf.open(str(out))) == 0


def test_shared_widgets_same_value(qtbot, window, tmp_path):
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "Nome")
    for p in (1, 2):
        E.copy_field_to_page(pdf, 0, x, p, shared=True)
    pdf.save(str(tmp_path / "s.pdf"))
    view = open_form(window, tmp_path / "s.pdf")
    click_field(qtbot, view, "Nome", 1)
    f = filler_of(view)
    qtbot.keyClicks(f.editor, "Lia")
    f.commit_active()
    out = tmp_path / "s2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert E.form_values(again) == {"Nome": "Lia"}
    assert all("Lia" in again[i].get_text() for i in range(3))


def test_need_appearances_fixed_on_open_and_save(window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "A")
    E.set_value(pdf, 0, x, "valore")
    pdf.xref_set_key(x, "AP", "null")
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/NeedAppearances", "true")
    path = tmp_path / "na.pdf"
    pdf.save(str(path))
    view = open_form(window, path)
    QApplication.processEvents()
    out = tmp_path / "na2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    w = E.list_fields(again)[0]
    assert again.xref_get_key(w.xref, "AP/N")[0] == "xref"
    assert not E.needs_appearances(again)


def test_reset_skips_signature_locked():
    pdf = build_form()
    s = E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(300, 300, 450, 340), "firma")
    fx = E._field_xref(pdf, s)
    pdf.xref_set_key(fx, "V", "<<>>")
    pdf.xref_set_key(fx, "Lock", "<</Type/SigFieldLock/Action/Include/Fields[(nome)]>>")
    E.apply_values(pdf, {"nome": "Bloccato", "cognome": "Libero"})
    E.reset_form(pdf)
    vals = E.form_values(pdf)
    assert vals["nome"] == "Bloccato" and vals["cognome"] == ""
