"""Modifica modulo: creazione con gli strumenti, selezione, spostamento, disposizione, proprietà."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from foliant.modules import forms
from foliant.modules.forms import engine as E
from foliant.modules.forms.design import FieldCreateTool, FieldSelectTool

from .test_fill import click_field, filler_of, open_form, values


@pytest.fixture
def blank_view(window, tmp_path):
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page()
    path = tmp_path / "vuoto.pdf"
    pdf.save(str(path))
    return open_form(window, path)


def module():
    return forms.instance()


def to_view(view, page, x, y):
    r = view.page_to_view(page, pymupdf.Rect(x, y, x + 1, y + 1))
    return r.topLeft().toPoint()


def drag(qtbot, view, page, p0, p1):
    vp = view.viewport()
    a, b = to_view(view, page, *p0), to_view(view, page, *p1)
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(b), vp.mapToGlobal(QPointF(b)), Qt.MouseButton.NoButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, ev)
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    QApplication.processEvents()


def fields(view):
    with view.document.lock:
        return E.list_fields(view.document.pdf)


def test_enter_mode_disables_filling(qtbot, window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    assert isinstance(view.tool, FieldSelectTool)
    click_field(qtbot, view, "nome")
    assert filler_of(view).editor is None
    assert module().selection
    window.ctx.set_mode("leggi")
    assert not filler_of(view).design


def test_create_text_by_drag_and_undo(qtbot, window, blank_view):
    view = blank_view
    window.ctx.set_mode("moduli")
    module().start_create(E.TEXT)
    assert isinstance(view.tool, FieldCreateTool)
    drag(qtbot, view, 0, (100, 100), (300, 125))
    fs = fields(view)
    assert len(fs) == 1 and fs[0].name == "Testo1" and fs[0].kind == E.TEXT
    assert abs(fs[0].rect.x0 - 100) < 2 and abs(fs[0].rect.x1 - 300) < 2
    assert module().selection == [(0, fs[0].xref)]
    assert isinstance(view.tool, FieldSelectTool)  # uso singolo
    view.document.undo()
    assert fields(view) == []
    with view.document.lock:
        assert view.document.pdf.xref_get_key(view.document.pdf.pdf_catalog(), "AcroForm")[0] == "null"


def test_click_creates_default_size(qtbot, window, blank_view):
    view = blank_view
    window.ctx.set_mode("moduli")
    module().start_create(E.CHECKBOX)
    vp = view.viewport()
    qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, 100, 100))
    fs = fields(view)
    assert fs[0].kind == E.CHECKBOX and abs(fs[0].rect.width - 14) < 1


@pytest.mark.parametrize("kind,ft,flag", [
    (E.TEXT, "/Tx", 0), (E.CHECKBOX, "/Btn", 0), (E.RADIO, "/Btn", E.FF_RADIO), (E.COMBO, "/Ch", 1 << 17),
    (E.LIST, "/Ch", 0), (E.BUTTON, "/Btn", E.FF_PUSHBUTTON), ("image", "/Btn", E.FF_PUSHBUTTON),
    ("date", "/Tx", 0), (E.SIGNATURE, "/Sig", 0),
])
def test_nine_tools(window, blank_view, kind, ft, flag):
    view = blank_view
    window.ctx.set_mode("moduli")
    x = module().create_field(view, 0, kind, pymupdf.Rect(50, 50, 200, 90))
    with view.document.lock:
        pdf = view.document.pdf
        fx = E._field_xref(pdf, x)
        assert pdf.xref_get_key(fx, "FT")[1] == ft
        ff = int(pdf.xref_get_key(fx, "Ff")[1]) if pdf.xref_get_key(fx, "Ff")[0] == "int" else 0
        if flag:
            assert ff & flag
        if kind == "image":
            assert E.is_image_field(pdf, x)
        if kind == "date":
            assert E.field_info(pdf, 0, x).fmt.type == "date"


def test_radio_sticky_same_group(window, blank_view):
    view = blank_view
    window.ctx.set_mode("moduli")
    m = module()
    for i in range(3):
        m.create_field(view, 0, E.RADIO, pymupdf.Rect(50 + 30 * i, 50, 64 + 30 * i, 64), sticky=i > 0)
    fs = fields(view)
    assert len({f.field_xref for f in fs}) == 1 and len(fs) == 3


def test_select_move_and_single_undo(qtbot, window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    before = {f.name: pymupdf.Rect(f.rect) for f in fields(view) if f.name in ("nome", "cognome")}
    m = module()
    nome = [f for f in fields(view) if f.name == "nome"][0]
    cogn = [f for f in fields(view) if f.name == "cognome"][0]
    m.set_selection([(0, nome.xref), (0, cogn.xref)])
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    after = {f.name: f.rect for f in fields(view) if f.name in before}
    assert all(abs(after[n].x0 - before[n].x0 - 10) < 0.01 for n in before)
    view.document.undo()
    after = {f.name: f.rect for f in fields(view) if f.name in before}
    assert all(abs(after[n].x0 - before[n].x0) < 0.01 for n in before)


def test_drag_moves_field(qtbot, window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    nome = [f for f in fields(view) if f.name == "nome"][0]
    c = ((nome.rect.x0 + nome.rect.x1) / 2, (nome.rect.y0 + nome.rect.y1) / 2)
    drag(qtbot, view, 0, c, (c[0] + 40, c[1] + 300))
    moved = [f for f in fields(view) if f.name == "nome"][0]
    assert abs(moved.rect.x0 - nome.rect.x0 - 40) < 2 and abs(moved.rect.y0 - nome.rect.y0 - 300) < 2


def test_align_distribute_same_size(window, blank_view):
    view = blank_view
    window.ctx.set_mode("moduli")
    m = module()
    xs = [m.create_field(view, 0, E.TEXT, pymupdf.Rect(x0, y, x0 + 100, y + 20))
          for x0, y in ((50, 50), (60, 100), (72, 300))]
    m.set_selection([(0, x) for x in xs])
    m.arrange("left")
    assert all(abs(f.rect.x0 - 50) < 0.01 for f in fields(view))
    m.arrange("dist_v")
    fs = sorted(fields(view), key=lambda f: f.rect.y0)
    gaps = [fs[i + 1].rect.y0 - fs[i].rect.y1 for i in range(2)]
    assert abs(gaps[0] - gaps[1]) < 0.01
    assert abs(fs[0].rect.y0 - 50) < 0.01 and abs(fs[2].rect.y1 - 320) < 0.01


def test_duplicate_ctrl_d_and_delete(qtbot, window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    nome = [f for f in fields(view) if f.name == "nome"][0]
    m.set_selection([(0, nome.xref)])
    view.setFocus()
    m.duplicate_selected()  # dal menu contestuale, senza scorciatoia (FRM-23, UX-18)
    fs = fields(view)
    copy = [f for f in fs if f.name not in ("nome", "cognome", "privacy", "scelta", "paese", "colori")]
    assert len(copy) == 1 and copy[0].name.startswith("nome")
    assert abs(copy[0].rect.x0 - nome.rect.x0 - 10) < 0.01
    assert m.selection == [(copy[0].page, copy[0].xref)]
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    assert len(fields(view)) == len(fs) - 1


def test_copy_to_pages_shared_and_independent(window, tmp_path):
    pdf = pymupdf.open()
    for _ in range(10):
        pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "Nome")
    pdf.save(str(tmp_path / "p.pdf"))
    view = open_form(window, tmp_path / "p.pdf")
    window.ctx.set_mode("moduli")
    m = module()
    x = fields(view)[0].xref
    m.set_selection([(0, x)])
    m.copy_to_pages(list(range(10)), shared=True)
    fs = fields(view)
    assert len(fs) == 10 and {f.name for f in fs} == {"Nome"}
    with view.document.lock:
        E.set_value(view.document.pdf, 4, fs[4].xref, "x")
        assert all(f.value == "x" for f in E.list_fields(view.document.pdf))
    view.document.undo()
    m.set_selection([(0, x)])
    m.copy_to_pages([1, 3, 5, 7, 9], shared=False)
    names = {f.name for f in fields(view)}
    assert len(names) == 6


def test_properties_panel_apply_and_rename_merge(window, tmp_path, no_modal_boxes):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "A")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 100, 200, 120), "B")
    pdf.save(str(tmp_path / "r.pdf"))
    view = open_form(window, tmp_path / "r.pdf")
    window.ctx.set_mode("moduli")
    m = module()
    a, b = fields(view)
    assert m.apply_properties([(0, a.xref), (0, b.xref)], {"required": True}, "x")
    assert all(f.required for f in fields(view))
    assert m.apply_properties([(0, b.xref)], {"name": "A"}, "Rinomina")
    assert any(c[0] == "question" for c in no_modal_boxes)
    assert E.form_values(view.document.pdf) == {"A": ""}


def test_calc_cycle_rejected(window, tmp_path, no_modal_boxes):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "A")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 100, 200, 120), "B", calc=E.Calculation("SUM", ["A"]))
    pdf.save(str(tmp_path / "c.pdf"))
    view = open_form(window, tmp_path / "c.pdf")
    a = fields(view)[0]
    assert not module().apply_properties([(0, a.xref)], {"calc": E.Calculation("SUM", ["B"])}, "x")
    assert any("A → B → A" in c[2] for c in no_modal_boxes)


def test_tab_order_selection(window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    xs = [E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50 + 40 * i, 200, 70 + 40 * i), n)
          for i, n in enumerate("ABC")]
    pdf.save(str(tmp_path / "t.pdf"))
    view = open_form(window, tmp_path / "t.pdf")
    window.ctx.set_mode("moduli")
    m = module()
    fs = fields(view)
    m.set_selection([(0, fs[2].xref), (0, fs[0].xref), (0, fs[1].xref)])
    m.tab_order("selection")
    out = tmp_path / "t2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert [f.name for f in E.navigation_order(again, 0)] == ["C", "A", "B"]


def test_preview_fill_and_back(qtbot, window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    m.toggle_preview(True)
    click_field(qtbot, view, "nome")
    f = filler_of(view)
    assert f.editor is not None
    qtbot.keyClicks(f.editor, "Prova")
    m.toggle_preview(False)
    assert values(view)["nome"] == "Prova"
    assert filler_of(view).design


def test_design_screenshot(qtbot, window, form_pdf, tmp_path):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    nome = [f for f in fields(view) if f.name == "nome"][0]
    m.set_selection([(0, nome.xref)])
    view.flush_rendering()
    QApplication.processEvents()
    assert not window.grab().isNull()


def test_preview_discard(qtbot, window, form_pdf, monkeypatch):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    m.toggle_preview(True)
    m.import_values({"nome": "a", "cognome": "b", "paese": "IT"}, view)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    m.toggle_preview(False)
    assert values(view)["nome"] == "" and values(view)["paese"] == ""


def test_tab_numbers_overlay(window, form_pdf):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    m.toggle_tab_numbers(True)
    view.flush_rendering()
    img = view.viewport().grab().toImage()
    nome = [f for f in fields(view) if f.name == "nome"][0]
    r = view.page_to_view(0, pymupdf.Rect(nome.rect.x0 - 5, nome.rect.y0 - 5, nome.rect.x0 + 5, nome.rect.y0 + 5))
    rr = r.toAlignedRect()
    orange = sum(1 for x in range(rr.left(), rr.right()) for y in range(rr.top(), rr.bottom())
                 if (lambda c: c.red() > c.blue() + 80 and c.red() > 150)(img.pixelColor(x, y)))
    assert orange > rr.width() * rr.height() * 0.2  # cerchio nel colore secondario (arancio)
    m.toggle_tab_numbers(False)


def test_image_field_fit_and_size(window, blank_view, tmp_path, monkeypatch, no_modal_boxes):
    from PIL import Image
    view = blank_view
    window.ctx.set_mode("moduli")
    m = module()
    m.create_field(view, 0, "image", pymupdf.Rect(50, 50, 250, 150))
    img = Image.new("RGB", (4000, 3000), (200, 30, 30))
    p = tmp_path / "foto.jpg"
    img.save(p, quality=95)
    before = len(view.document.to_bytes())
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(p)])
    window.ctx.set_mode("leggi")
    f = filler_of(view)
    f.state.refresh(force=True)
    info = [i for i in f.state.fields if i.kind == E.BUTTON][0]
    f.click_field(info)
    data = view.document.to_bytes()
    assert not no_modal_boxes, no_modal_boxes
    assert len(data) - before < 2_000_000
    again = pymupdf.open("pdf", data)
    pix = again[0].get_pixmap(dpi=72)
    # al centro del campo il colore dell'immagine; ai lati (fuori dai 133 pt centrali) lo sfondo
    cx = pix.pixel(150, 100)
    side = pix.pixel(55, 100)
    assert cx[0] > 150 and cx[1] < 80
    assert side[1] > 200


def test_button_actions_reset_and_hide(window, tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    for i, n in enumerate("ABC"):
        x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50 + 30 * i, 200, 70 + 30 * i), n)
        E.set_value(pdf, 0, x, "v" + n)
    b1 = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(300, 50, 380, 70), "reset")
    E.set_button_action(pdf, 0, b1, {"type": "reset", "fields": ["A", "B"]})
    b2 = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(300, 90, 380, 110), "hide")
    E.set_button_action(pdf, 0, b2, {"type": "hide", "fields": ["C"]})
    pdf.save(str(tmp_path / "b.pdf"))
    view = open_form(window, tmp_path / "b.pdf")
    f = filler_of(view)
    f.click_field([i for i in f.state.fields if i.name == "reset"][0])
    v = values(view)
    assert v["A"] == "" and v["B"] == "" and v["C"] == "vC"
    f.state.refresh()
    f.click_field([i for i in f.state.fields if i.name == "hide"][0])
    c = [i for i in fields(view) if i.name == "C"][0]
    assert c.hidden


def test_calc_order_dialog_auto(window, tmp_path, monkeypatch):
    from foliant.modules.forms import dialogs
    pdf = pymupdf.open()
    pdf.new_page()
    xs = {n: E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 40 + 30 * i, 200, 58 + 30 * i), n)
          for i, n in enumerate(("a", "b", "k", "S", "T"))}
    E.update_properties(pdf, 0, xs["T"], calc=E.Calculation("PRD", ["S", "k"]))
    E.update_properties(pdf, 0, xs["S"], calc=E.Calculation("SUM", ["a", "b"]))
    pdf.save(str(tmp_path / "co.pdf"))
    view = open_form(window, tmp_path / "co.pdf")
    seen = {}

    def fake(parent, order, problems, auto):
        seen["problems"] = problems(order)
        return auto()

    monkeypatch.setattr(dialogs, "ask_calc_order", fake)
    module().calc_order_dialog()
    assert seen["problems"] == [("T", "S")]
    with view.document.lock:
        assert E.calc_order(view.document.pdf) == ["S", "T"]


def test_detect_offers_ocr_on_scans(window, tmp_path, monkeypatch, no_modal_boxes):
    src = pymupdf.open()
    pg = src.new_page()
    pg.draw_line((100, 200), (400, 200))
    pix = pg.get_pixmap(dpi=100)
    pdf = pymupdf.open()
    p2 = pdf.new_page()
    p2.insert_image(p2.rect, stream=pix.tobytes("png"))
    pdf.save(str(tmp_path / "scan.pdf"))
    view = open_form(window, tmp_path / "scan.pdf")
    if window.ctx.mode("ocr") is None:
        window.ctx.add_mode("ocr", "OCR", "scan-text", 60)
    module().detect_fields()
    assert any(c[0] == "question" for c in no_modal_boxes)
    assert window.ctx.current_mode() == "ocr"


def test_kind_defaults_applied(window, blank_view):
    view = blank_view
    window.ctx.set_mode("moduli")
    m = module()
    m.set_kind_defaults(E.TEXT, {"font": "Courier", "fontsize": 14, "text_color": (0, 0, 1), "name": "ignorato"})
    x = m.create_field(view, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 80))
    with view.document.lock:
        f = E.field_info(view.document.pdf, 0, x)
    assert f.font == "Courier" and f.fontsize == 14 and f.name == "Testo1"


def test_signed_field_cannot_move(window, tmp_path, no_modal_boxes):
    pdf = pymupdf.open()
    pdf.new_page()
    s = E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(50, 50, 200, 90), "firma")
    pdf.xref_set_key(E._field_xref(pdf, s), "V", "<<>>")
    pdf.save(str(tmp_path / "s.pdf"))
    view = open_form(window, tmp_path / "s.pdf")
    window.ctx.set_mode("moduli")
    f = fields(view)[0]
    module().set_selection([(0, f.xref)])
    module().nudge_selected(10, 0)
    assert fields(view)[0].rect == f.rect
    assert any("firmati" in c[2] for c in no_modal_boxes)


def test_save_warns_required_empty(window, form_pdf, tmp_path):
    view = open_form(window, form_pdf)
    module().import_values({"cognome": "x"}, view)
    view.document.save(tmp_path / "bozza.pdf")
    assert (tmp_path / "bozza.pdf").exists()
    assert filler_of(view).last_warning == "nome"


def _submit_doc(tmp_path, required=False):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "A", required=required)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 200, 110), "B")
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(300, 50, 380, 70), "invia")
    E.set_button_action(pdf, 0, b, {"type": "submit", "url": "https://esempio.test/ricevi", "format": "xfdf"})
    pdf.save(str(tmp_path / "sub.pdf"))
    return tmp_path / "sub.pdf"


def test_submit_xfdf(window, tmp_path, monkeypatch, no_modal_boxes):
    view = open_form(window, _submit_doc(tmp_path))
    m = module()
    m.import_values({"A": "uno", "B": "due"}, view)
    sent = {}
    monkeypatch.setattr(m, "post", lambda url, data, mime: sent.update(url=url, data=data, mime=mime) or 200)
    f = filler_of(view)
    f.state.refresh(force=True)
    f.click_field([i for i in f.state.fields if i.name == "invia"][0])
    assert sent["url"] == "https://esempio.test/ricevi"
    from foliant.modules.forms import interchange as X
    assert X.parse_xfdf(sent["data"]) == {"A": "uno", "B": "due"}


def test_submit_blocked_and_preview(window, tmp_path, monkeypatch, no_modal_boxes):
    view = open_form(window, _submit_doc(tmp_path, required=True))
    m = module()
    calls = []
    monkeypatch.setattr(m, "post", lambda *a: calls.append(a) or 200)
    f = filler_of(view)
    action = {"type": "submit", "url": "https://esempio.test/ricevi", "format": "xfdf"}
    assert not m.submit(f, action) and not calls
    m.import_values({"A": "x"}, view)
    window.ctx.set_mode("moduli")
    m.toggle_preview(True)
    assert not m.submit(filler_of(view), action) and not calls
    assert any("Anteprima" in c[2] for c in no_modal_boxes)


def test_copy_paste_between_documents_renames(qtbot, window, form_pdf, tmp_path):
    view = open_form(window, form_pdf)
    window.ctx.set_mode("moduli")
    m = module()
    nome = [f for f in fields(view) if f.name == "nome"][0]
    m.set_selection([(0, nome.xref)])
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    other = tmp_path / "altro.pdf"
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 300, 400, 320), "nome")
    pdf.save(str(other))
    view2 = open_form(window, other)
    window.ctx.set_mode("moduli")
    view2.setFocus()
    qtbot.keyClick(view2, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    names = sorted(f.name for f in fields(view2))
    assert names == ["nome", "nome_2"]
    pasted = [f for f in fields(view2) if f.name == "nome_2"][0]
    assert pasted.required and abs(pasted.rect.x0 - nome.rect.x0) < 0.01
