"""Test dell'interfaccia: strumenti della modalità Modifica, editor in loco, pannello."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QTextCursor

from foliant.modules.text_edit.ui_tool import AddTextTool, EditTextTool

from .conftest import span_with


@pytest.fixture
def quiet(window, monkeypatch):
    """Dialoghi simulati: registra errori e conferme invece di aprire finestre modali."""
    log = {"errors": [], "confirms": [], "answer": True, "status": []}
    ctx = window.ctx
    monkeypatch.setattr(ctx, "error", lambda t, m: log["errors"].append((t, m)))
    monkeypatch.setattr(ctx, "info", lambda t, m: None)

    def confirm(t, m):
        log["confirms"].append((t, m))
        return log["answer"]

    monkeypatch.setattr(ctx, "confirm", confirm)
    orig_status = ctx.status
    monkeypatch.setattr(ctx, "status", lambda m, timeout_ms=5000: (log["status"].append(m), orig_status(m, timeout_ms)))
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)

    original_close = window.close_view

    def close_without_prompt(view, *a, **k):
        view.document._saved_state = view.document._state   # pytest-qt chiude la finestra prima dei fixture
        return original_close(view, *a, **k)

    monkeypatch.setattr(window, "close_view", close_without_prompt)
    yield log
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().clear()   # dati negli appunti all'uscita: crash del plugin offscreen


def open_in_edit_mode(window, path, password=None):
    view = window.open_path(path, password)
    assert view is not None
    window.ctx.set_mode("modifica")
    view.flush_rendering()
    return view


def to_view(view, page, x, y):
    p = view.page_transform(page).map(QPointF(x, y))
    return p.toPoint()


def click(qtbot, view, page, x, y, modifier=Qt.KeyboardModifier.NoModifier):
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, modifier, to_view(view, page, x, y))


def ctrl_of(window):
    return window.ctx.text_edit


def block_with(ctrl, view, text, page=0):
    return [b for b in ctrl.blocks(view, page) if text in b.text][0]


def test_mode_activates_text_tool(window, paragraphs_pdf, quiet):
    view = open_in_edit_mode(window, paragraphs_pdf)
    assert isinstance(view.tool, EditTextTool)
    ctrl = ctrl_of(window)
    assert ctrl.actions["edit"].isChecked()
    assert window.properties.isVisible()


def test_click_opens_editor_with_original_style(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    editor = ctrl.editor_for(view)
    assert editor is not None and editor.isVisible()
    assert editor.plain_text() == "Il contratto scade nel 2024"
    st = editor.current_style()
    assert abs(st.size - 12) < 0.05
    assert all(abs(a - b) < 0.01 for a, b in zip(st.color, block.style.color))
    assert st.font == block.style.font
    assert not view.document.is_modified


def test_type_and_commit_with_escape(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    origin = block.lines[0].origin
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    editor = ctrl.editor_for(view)
    cur = editor.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    for _ in range(4):
        cur.deletePreviousChar()
    editor.setTextCursor(cur)
    qtbot.keyClicks(editor, "2026")
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert ctrl.editor_for(view) is None
    doc = view.document
    assert doc.is_modified
    text = doc.page_text(0)
    assert "scade nel 2026" in text and "2024" not in text
    with doc.lock:
        s = span_with(doc.pdf[0], "2026")
    assert abs(s["origin"][0] - origin.x) <= 0.5 and abs(s["origin"][1] - origin.y) <= 0.5
    # Esc lascia il blocco selezionato come oggetto
    assert view.tool.selected is not None
    # un solo passo annulla tutta la digitazione
    assert doc.undo()
    assert "2024" in doc.page_text(0)


def test_enter_and_leave_without_changes(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Lorem")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    assert ctrl.editor_for(view) is not None
    click(qtbot, view, 0, 500, 780)    # fuori dal blocco: conferma senza cambiamenti
    assert ctrl.editor_for(view) is None
    window.ctx.set_mode("leggi")
    assert not view.document.is_modified


def test_add_text_tool(window, sample_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, sample_pdf)
    ctrl = ctrl_of(window)
    ctrl.actions["add"].trigger()
    assert isinstance(view.tool, AddTextTool)
    click(qtbot, view, 0, 100, 600)
    editor = ctrl.editor_for(view)
    assert editor is not None
    qtbot.keyClicks(editor, "Nuovo testo")
    click(qtbot, view, 0, 400, 780)
    assert "Nuovo testo" in view.document.page_text(0)
    with view.document.lock:
        s = span_with(view.document.pdf[0], "Nuovo")
    assert abs(s["bbox"][0] - 100) <= 1.5


def test_empty_text_box_discarded(window, sample_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, sample_pdf)
    ctrl = ctrl_of(window)
    ctrl.actions["add"].trigger()
    click(qtbot, view, 0, 100, 600)
    assert ctrl.editor_for(view) is not None
    click(qtbot, view, 0, 400, 780)
    assert not view.document.is_modified


def test_select_on_border_move_and_delete(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2, Qt.KeyboardModifier.AltModifier)
    tool = view.tool
    assert tool.selected is not None and ctrl.editor_for(view) is None
    x_before = block.lines[0].origin.x
    qtbot.keyClick(view, Qt.Key.Key_Right)
    moved = block_with(ctrl, view, "contratto")
    assert abs(moved.lines[0].origin.x - (x_before + 1)) <= 0.2
    qtbot.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    moved = block_with(ctrl, view, "contratto")
    assert abs(moved.lines[0].origin.x - (x_before + 11)) <= 0.2
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    assert "contratto" not in view.document.page_text(0)
    view.document.undo()
    assert "contratto" in view.document.page_text(0)


def test_drag_moves_block(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    tool = view.tool
    tool.select(view, 0, block)
    c = block.bbox
    start = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    vp = view.viewport()
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, start.x, start.y))
    qtbot.mouseMove(vp, to_view(view, 0, start.x + 30, start.y + 20))
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, start.x + 60, start.y + 40))
    moved = block_with(ctrl, view, "contratto")
    zoom_px = view.zoom * 96 / 72
    assert abs(moved.lines[0].origin.x - (block.lines[0].origin.x + 60)) <= 1.5 / zoom_px + 0.5
    assert abs(moved.lines[0].origin.y - (block.lines[0].origin.y + 40)) <= 1.5 / zoom_px + 0.5


def test_panel_changes_size_and_color(window, paragraphs_pdf, quiet, qtbot):
    from PySide6.QtGui import QColor
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    editor = ctrl.editor_for(view)
    # seleziona «contratto»
    cur = editor.textCursor()
    cur.setPosition(3)
    cur.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cur)
    panel = ctrl.panel
    panel.size.setValue(18)
    panel._size_chosen()
    panel.set_color(QColor("#C00000"))
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    with view.document.lock:
        word = span_with(view.document.pdf[0], "contratto")
        other = span_with(view.document.pdf[0], "scade")
    assert abs(word["size"] - 18) <= 0.05
    assert ((word["color"] >> 16) & 255) == 0xC0
    assert abs(other["size"] - 12) <= 0.05


def test_panel_restyles_selected_block(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    view.tool.select(view, 0, block)
    ctrl.panel.bold.click()
    with view.document.lock:
        s = span_with(view.document.pdf[0], "contratto")
    assert "bold" in s["font"].lower()
    ctrl.panel.align_buttons["center"].click()
    assert view.document.can_undo


def test_granularity_line(window, paragraphs_pdf, quiet):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    panel = ctrl.panel
    panel.granularity.setCurrentIndex(panel.granularity.findData("line"))
    assert ctrl.granularity == "line"
    assert len([b for b in ctrl.blocks(view, 0) if "contratto" not in b.text]) == 12
    panel.granularity.setCurrentIndex(panel.granularity.findData("paragraph"))


def test_permission_denied(window, tmp_path, quiet, qtbot):
    path = tmp_path / "limitato.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo protetto", fontsize=14)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="proprietario",
             permissions=int(pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY))
    doc.close()
    view = open_in_edit_mode(window, str(path), "utente")
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "protetto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    assert ctrl.editor_for(view) is None
    assert quiet["errors"] and "password del proprietario" in quiet["errors"][0][1]
    assert not view.document.is_modified


def test_signed_document_warns_before_editing(window, tmp_path, quiet, qtbot):
    path = tmp_path / "firmato.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Documento firmato", fontsize=14)
    acro = doc.get_new_xref()
    doc.update_object(acro, "<< /Fields [] /SigFlags 3 >>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{acro} 0 R")
    doc.save(str(path))
    doc.close()
    quiet["answer"] = False
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    # l'avviso compare entrando in Modifica, prima di qualsiasi modifica
    assert quiet["confirms"] and "firme digitali" in quiet["confirms"][0][1]
    assert not isinstance(view.tool, EditTextTool)
    quiet["answer"] = True
    window.ctx.set_mode("leggi")
    window.ctx.set_mode("modifica")
    assert isinstance(view.tool, EditTextTool)
    block = block_with(ctrl, view, "firmato")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    assert ctrl.editor_for(view) is not None
    assert not view.document.is_modified


def test_piece_info_preserved(window, tmp_path, quiet, qtbot):
    path = tmp_path / "pieceinfo.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo con dati privati", fontsize=14)
    doc.xref_set_key(page.xref, "PieceInfo", "<< /AltroProgramma << /Private (dati) >> >>")
    doc.save(str(path))
    doc.close()
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "privati")
    ed = ctrl.open_editor(view, 0, block)
    ed.selectAll()
    qtbot.keyClicks(ed, "Testo cambiato")
    ctrl.commit_editor(view)
    out = tmp_path / "out.pdf"
    view.document.save(out)
    re = pymupdf.open(str(out))
    assert "Testo cambiato" in re[0].get_text()
    assert "AltroProgramma" in re.xref_get_key(re[0].xref, "PieceInfo")[1]


def test_scanned_page_suggests_ocr(window, scanned_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, scanned_pdf)
    click(qtbot, view, 0, 200, 150)
    assert any("OCR" in m for m in quiet["status"])
    assert not view.document.is_modified


def test_ocr_layer_correction_from_ui(window, ocr_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, ocr_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Rossl")
    assert block.invisible
    ed = ctrl.open_editor(view, 0, block)
    assert ed.plain_text() == "Mario Rossl 2019"
    ed.selectAll()
    qtbot.keyClicks(ed, "Mario Rossi 2019")
    ctrl.commit_editor(view)
    text = view.document.page_text(0)
    assert "Rossi" in text and "Rossl" not in text
    nb = block_with(ctrl, view, "Rossi")
    assert nb.invisible


def test_copy_paste_block_shortcuts(window, paragraphs_pdf, quiet, qtbot):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    view.tool.select(view, 0, block)
    qtbot.keyClick(view, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert view.document.page_text(0).count("contratto") == 2
    qtbot.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert view.document.page_text(0).count("contratto") == 3


def test_undo_after_save_marks_modified(window, paragraphs_pdf, quiet, qtbot, tmp_path):
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    for word in ("uno", "due", "tre"):
        block = block_with(ctrl, view, "contratto")
        ed = ctrl.open_editor(view, 0, block)
        ed.moveCursor(QTextCursor.MoveOperation.End)
        qtbot.keyClicks(ed, " " + word)
        ctrl.commit_editor(view)
    view.document.save(tmp_path / "s.pdf")
    assert not view.document.is_modified
    view.document.undo()
    assert view.document.is_modified
    assert "tre" not in view.document.page_text(0) and "due" in view.document.page_text(0)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_screenshot(window, paragraphs_pdf, quiet, qtbot, tmp_path, mode):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    theme.apply(QApplication.instance(), mode)
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    view.tool.hover = (0, block_with(ctrl, view, "Lorem"))
    ed = ctrl.open_editor(view, 0, block)
    ed.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClicks(ed, " (modificato)")
    view.flush_rendering()
    img = window.grab()
    assert not img.isNull()
    img.save(str(tmp_path / f"text_edit_{mode}.png"))
    ctrl.commit_editor(view)
    theme.apply(QApplication.instance(), "light")


def test_editor_keeps_original_line_breaks(window, paragraphs_pdf, quiet, qtbot):
    """bug_02: il riquadro di un paragrafo di 4 righe mostra 4 righe, tutte visibili."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    view.set_zoom(1.0)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Lorem")
    ed = ctrl.open_editor(view, 0, block)
    layout = ed.document().begin().layout()
    assert layout.lineCount() == 4
    assert ed.document().size().height() <= ed.viewport().height() + 1


def test_add_text_editor_wraps_at_page_edge(window, sample_pdf, quiet, qtbot):
    """bug_07: la casella a larghezza automatica va a capo al bordo della pagina anche nell'editor."""
    view = open_in_edit_mode(window, sample_pdf)
    ctrl = ctrl_of(window)
    ed = ctrl.open_new_text(view, 0, pymupdf.Point(545, 300), None)
    ed.insertPlainText("Questa frase e lunga abbastanza da superare il bordo destro della pagina")
    qtbot.wait(30)
    r = ed.content_rect_points()
    assert r.right() <= 595 + 1
    assert ed.document().begin().layout().lineCount() > 1


def test_click_in_selected_block_opens_editor(window, paragraphs_pdf, quiet, qtbot):
    """bug_05: dopo Esc il blocco è selezionato; un clic nel testo riapre l'editor."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    qtbot.keyClick(ctrl.editor_for(view), Qt.Key.Key_Escape)
    assert view.tool.selected is not None
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    assert ctrl.editor_for(view) is not None


def test_second_document_gets_text_tool(window, paragraphs_pdf, sample_pdf, quiet):
    """bug_04: aprendo un altro PDF in modalità Modifica lo strumento è già «Modifica testo»."""
    open_in_edit_mode(window, paragraphs_pdf)
    view2 = window.open_path(sample_pdf)
    assert isinstance(view2.tool, EditTextTool)


def test_protected_document_checked_on_entering_mode(window, tmp_path, quiet):
    """bug_11: con modifica negata il messaggio compare entrando in Modifica, senza attivare lo strumento."""
    path = tmp_path / "limitato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Testo protetto", fontsize=14)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="proprietario",
             permissions=int(pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY))
    doc.close()
    view = window.open_path(str(path), "utente")
    window.ctx.set_mode("modifica")
    assert quiet["errors"]
    assert not isinstance(view.tool, EditTextTool)


def test_add_text_returns_to_edit_tool(window, sample_pdf, quiet, qtbot):
    """bug_12: confermata la nuova casella si torna a «Modifica testo»."""
    view = open_in_edit_mode(window, sample_pdf)
    ctrl = ctrl_of(window)
    ctrl.actions["add"].trigger()
    click(qtbot, view, 0, 100, 600)
    qtbot.keyClicks(ctrl.editor_for(view), "Nuovo")
    qtbot.keyClick(ctrl.editor_for(view), Qt.Key.Key_Escape)
    assert "Nuovo" in view.document.page_text(0)
    assert isinstance(view.tool, EditTextTool)
