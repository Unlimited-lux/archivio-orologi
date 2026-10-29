"""Test dell'interfaccia: strumenti della modalità Modifica, editor in loco, pannello."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QTextCursor

from foliant.core.platform import find_tool
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


def _signed_pdf(tmp_path) -> str:
    """PDF con una vera firma digitale (ID di prova creato al volo)."""
    from foliant.modules.sign import digital
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Documento firmato 2024", fontsize=14)
    data = doc.tobytes()
    doc.close()
    old = digital.PKCS12_KDF_ROUNDS
    digital.PKCS12_KDF_ROUNDS = 2048
    try:
        digital.create_self_signed(tmp_path / "id.p12", "Prova-Firma-2026!", common_name="Mario Rossi")
    finally:
        digital.PKCS12_KDF_ROUNDS = old
    did = digital.load_pkcs12(tmp_path / "id.p12", "Prova-Firma-2026!")
    signed = digital.sign_pdf(data, did, digital.SignOptions(reason="Approvo"))
    path = tmp_path / "firmato.pdf"
    path.write_bytes(signed)
    return str(path)


def test_signed_document_single_warning(window, tmp_path, quiet, qtbot, monkeypatch):
    """Documento firmato: un solo avviso (quello del modulo Firma); con «Annulla» il documento resta invariato."""
    from PySide6.QtWidgets import QApplication
    sign = getattr(window.ctx, "sign_controller", None)
    if sign is None:
        pytest.skip("modulo Firma non caricato")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dati"))
    path = _signed_pdf(tmp_path)
    asked = []
    monkeypatch.setattr(sign, "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    view = open_in_edit_mode(window, path)
    QApplication.processEvents()
    ctrl = ctrl_of(window)
    assert isinstance(view.tool, EditTextTool)
    block = block_with(ctrl, view, "firmato")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    ed = ctrl.editor_for(view)
    assert ed is not None
    ed.selectAll()
    ed.insertPlainText("Documento firmato 2026")
    ctrl.commit_editor(view)
    QApplication.processEvents()
    assert quiet["confirms"] == [] and quiet["errors"] == []     # nessuna conferma di text_edit
    assert len(asked) == 1                                        # un solo avviso: quello del modulo Firma
    assert not view.document.is_modified and "2024" in view.document.page_text(0)


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


def test_scanned_page_suggests_ocr(window, tmp_path, quiet, qtbot):
    from .conftest import render_text_image
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(
        pymupdf.Rect(0, 0, 595, 842), stream=render_text_image("Contratto 2019", size=(794, 1123), fontsize=60))
    path = tmp_path / "scansione_testo.pdf"
    doc.save(str(path))
    quiet["answer"] = False
    view = open_in_edit_mode(window, str(path))
    click(qtbot, view, 0, 200, 150)
    assert any("OCR" in m for m in quiet["status"])
    assert any("scansione" in m for _t, m in quiet["confirms"])
    assert not view.document.is_modified


def test_small_text_image_suggests_ocr(window, tmp_path, quiet, qtbot):
    """bug_09 (regressione): pagina A4 senza testo nativo con un'immagine di testo 495×140 px che non copre
    la pagina: in Modifica compare comunque la proposta di OCR."""
    from .conftest import render_text_image
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(
        pymupdf.Rect(50, 100, 545, 240), stream=render_text_image("Mario Rossi 2019", size=(495, 140), fontsize=40))
    path = tmp_path / "immagine_testo.pdf"
    doc.save(str(path))
    quiet["answer"] = False
    view = open_in_edit_mode(window, str(path))
    click(qtbot, view, 0, 300, 170)
    assert any("scansione" in m for _t, m in quiet["confirms"])
    assert not view.document.is_modified


def test_accepted_ocr_proposal_makes_page_editable(window, tmp_path, quiet, qtbot):
    """bug_09: accettando la proposta, l'OCR rende la pagina modificabile senza riaprire il documento."""
    if not find_tool("tesseract") or getattr(window.ctx, "ocr", None) is None:
        pytest.skip("OCR non disponibile")
    from .conftest import render_text_image
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842),
                      stream=render_text_image("Contratto 2019", size=(794, 1123), fontsize=60))
    path = tmp_path / "scansione_testo.pdf"
    doc.save(str(path))
    doc.close()
    quiet["answer"] = True
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    blocks = ctrl.blocks(view, 0)
    assert blocks and any("2019" in b.text for b in blocks), (quiet, view.document.page_text(0))


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
    ctrl.duplicate_block(view, 0, block)   # Duplica dal menu contestuale (Ctrl+D non usato, specifica v2)
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


def test_overlap_indicator(window, paragraphs_pdf, quiet, qtbot):
    """bug_08: un paragrafo che cresce sopra il successivo mostra l'indicatore di sovrapposizione."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Lorem")
    ed = ctrl.open_editor(view, 0, block)
    ed.moveCursor(QTextCursor.MoveOperation.End)
    ed.insertPlainText(" e ancora molte altre parole aggiunte per far crescere il paragrafo di due righe intere")
    ctrl.commit_editor(view)
    assert ctrl.overflow_marks(view)
    assert any("sovrappone" in m for m in quiet["status"])


@pytest.mark.qt_no_exception_capture   # altri moduli possono segnalare il file danneggiato all'apertura
def test_page_tree_cycle_does_not_break_edit_mode(window, tmp_path, quiet, qtbot):
    """Albero delle pagine con un ciclo: entrare in Modifica non solleva eccezioni."""
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=14)
    pages = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    kids = doc.xref_get_key(pages, "Kids")[1]
    doc.xref_set_key(pages, "Kids", kids.replace("]", f" {pages} 0 R]"))
    doc.xref_set_key(pages, "Count", "3")
    path = tmp_path / "ciclo.pdf"
    doc.save(str(path), garbage=0)
    doc.close()
    try:
        view = window.open_path(str(path))
    except Exception:
        pytest.skip("il motore non apre il file")
    if view is None:
        pytest.skip("il motore non apre il file")
    ctrl = ctrl_of(window)
    ctrl.check_page_has_text(view, view.page_count - 1)
    ctrl.frame(view.page_count - 1, view)
    window.ctx.set_mode("modifica")
    assert window.ctx.current_mode() == "modifica"


def test_panel_indent_and_space_after(window, paragraphs_pdf, quiet, qtbot):
    """TXT-05: rientro della prima riga e spazio dopo dal pannello."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Lorem")
    x0 = block.x0
    view.tool.select(view, 0, block)
    ctrl.panel.indent.setValue(20)
    ctrl.panel._indent_chosen()
    nb = block_with(ctrl, view, "Lorem")
    assert abs(nb.lines[0].bbox.x0 - (x0 + 20)) <= 0.6 and abs(nb.lines[1].bbox.x0 - x0) <= 0.6
    # nell'editor: due paragrafi con 12 pt di spazio dopo
    block = block_with(ctrl, view, "contratto")
    ed = ctrl.open_editor(view, 0, block)
    ed.moveCursor(QTextCursor.MoveOperation.End)
    ed.insertPlainText("\nSecondo paragrafo")
    ctrl.panel.space_after.setValue(12)
    ctrl.panel._space_after_chosen()
    ctrl.commit_editor(view)
    from foliant.modules.text_edit import analysis
    with view.document.lock:
        lines = [b for b in analysis.analyze_page(view.document.pdf[0], 0, "line")
                 if "contratto" in b.text or "Secondo" in b.text]
    gap = lines[1].first_baseline - lines[0].first_baseline
    assert abs(gap - (block.leading + 12)) <= 0.2


def test_drag_snaps_to_other_block_edge(window, paragraphs_pdf, quiet, qtbot):
    """TXT-08: trascinando vicino al bordo sinistro di un altro blocco il blocco si aggancia; Alt sospende."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    view.tool.select(view, 0, block)
    c = block.bbox
    start = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    vp = view.viewport()
    # spostamento di 31 pt in orizzontale: nessun aggancio atteso; poi ritorno a +1,5 pt dal bordo x=72
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, start.x, start.y))
    qtbot.mouseMove(vp, to_view(view, 0, start.x + 1.5, start.y + 60))
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, start.x + 1.5, start.y + 60))
    moved = block_with(ctrl, view, "contratto")
    assert abs(moved.bbox.x0 - 72) <= 0.05          # agganciato al bordo sinistro dei paragrafi
    view.tool.select(view, 0, moved)
    c = moved.bbox
    start = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    alt = Qt.KeyboardModifier.AltModifier
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, start.x, start.y))
    qtbot.mouseMove(vp, to_view(view, 0, start.x + 2, start.y + 30))
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, alt, to_view(view, 0, start.x + 2, start.y + 30))
    moved2 = block_with(ctrl, view, "contratto")
    assert moved2.bbox.x0 > 72.8                     # con Alt nessun aggancio


def test_copy_paste_with_formatting_between_editors(window, paragraphs_pdf, quiet, qtbot):
    """TXT-18: copiato da un riquadro, incollato in un altro con font, corpo e colore di origine;
    Ctrl+Maiusc+V incolla testo semplice con lo stile del punto d'inserimento."""
    from PySide6.QtGui import QGuiApplication
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    src = block_with(ctrl, view, "contratto")
    ed = ctrl.open_editor(view, 0, src)
    cur = ed.textCursor()
    cur.setPosition(3)
    cur.setPosition(12, QTextCursor.MoveMode.KeepAnchor)     # «contratto»
    ed.setTextCursor(cur)
    ed.copy()
    ctrl.commit_editor(view)
    dst = block_with(ctrl, view, "Lorem")
    ed2 = ctrl.open_editor(view, 0, dst)
    ed2.moveCursor(QTextCursor.MoveOperation.End)
    ed2.paste()
    runs = [r for p in ed2.paragraphs() for r in p]
    pasted = [r for r in runs if "contratto" in r.text][0]
    assert abs(pasted.style.size - 12) < 0.05 and pasted.style.color == src.style.color
    QGuiApplication.clipboard().setText(" semplice")
    qtbot.keyClick(ed2, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    runs = [r for p in ed2.paragraphs() for r in p]
    plain = [r for r in runs if "semplice" in r.text][0]
    assert abs(plain.style.size - 12) < 0.05 and plain.style.color == src.style.color   # stile del punto
    ctrl.cancel_editor(view)


def test_rotation_handle(window, paragraphs_pdf, quiet, qtbot):
    """TXT-08: trascinando la maniglia di rotazione il blocco ruota (Maiusc: passi di 15°)."""
    import math
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    tool = view.tool
    tool.select(view, 0, block)
    rh = tool._rotate_handle(view, block)
    assert tool._handle_at(view, rh) == "rotate"
    c = block.bbox
    center = pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    target = pymupdf.Point(center.x - 80, center.y + 3)        # circa 90° in senso antiorario
    vp = view.viewport()
    shift = Qt.KeyboardModifier.ShiftModifier
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, rh.x, rh.y))
    qtbot.mouseMove(vp, to_view(view, 0, target.x, target.y))
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, shift, to_view(view, 0, target.x, target.y))
    with view.document.lock:
        line = [ln for b in view.document.pdf[0].get_text("dict")["blocks"] for ln in b["lines"]
                if "contratto" in "".join(s["text"] for s in ln["spans"])][0]
    angle = -math.degrees(math.atan2(line["dir"][1], line["dir"][0]))
    assert abs(angle - 90) <= 0.5
    assert "contratto" in view.document.page_text(0, sort=False)


def test_panel_left_indent(window, paragraphs_pdf, quiet, qtbot):
    """TXT-05: rientro sinistro di tutto il paragrafo; il bordo destro non supera quello originale."""
    view = open_in_edit_mode(window, paragraphs_pdf)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Lorem")
    x0, x1, n = block.x0, block.x0 + block.width, len(block.lines)
    view.tool.select(view, 0, block)
    ctrl.panel.left_indent.setValue(30)
    ctrl.panel._left_indent_chosen()
    nb = block_with(ctrl, view, "Lorem")
    for ln in nb.lines:
        assert abs(ln.bbox.x0 - (x0 + 30)) <= 0.6 and ln.bbox.x1 <= x1 + 0.6
    assert len(nb.lines) > n
    assert ctrl.panel.left_indent.value() == 0


@pytest.mark.parametrize("all_pages", [False, True])
def test_shared_content_this_or_all_pages(window, tmp_path, quiet, qtbot, all_pages):
    """TXT-20: testo in un Form XObject usato da più pagine: «solo questa pagina» o «tutte le pagine»."""
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Intestazione 2024", fontsize=14)
    doc = pymupdf.open()
    for _i in range(3):
        p = doc.new_page()
        p.show_pdf_page(p.rect, src, 0)
    path = tmp_path / "condiviso.pdf"
    doc.save(str(path))
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    quiet["answer"] = all_pages
    block = block_with(ctrl, view, "Intestazione", page=1)
    ed = ctrl.open_editor(view, 1, block)
    ed.selectAll()
    ed.insertPlainText("Intestazione 2026")
    ctrl.commit_editor(view)
    assert any("altre pagine" in m for _t, m in quiet["confirms"])
    texts = [view.document.page_text(i) for i in range(3)]
    assert "2026" in texts[1]
    for i in (0, 2):
        assert ("2026" in texts[i]) == all_pages
    view.document.undo()
    assert all("2024" in view.document.page_text(i) for i in range(3))


def test_no_ocr_proposal_for_small_image(window, tmp_path, quiet, qtbot):
    """Pagina «vuota» con una piccola immagine (logo): nessuna proposta di OCR."""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(120)
    page.insert_image(pymupdf.Rect(50, 50, 90, 90), pixmap=pix)
    path = tmp_path / "logo.pdf"
    doc.save(str(path))
    open_in_edit_mode(window, str(path))
    assert not any("scansione" in m for _t, m in quiet["confirms"])


def test_second_window_closed_leaves_first_working(window, paragraphs_pdf, quiet, qtbot, settings):
    """Ctrl+N: il controller è per finestra; chiudere la seconda non rompe la prima."""
    from PySide6.QtWidgets import QApplication

    from foliant.modules.text_edit import controller_for, open_find_dialog, open_fonts_dialog
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow

    second = MainWindow(settings)
    second.show()
    assert controller_for(second.ctx) is not controller_for(window.ctx)
    second.ctx.set_mode("modifica")
    second.close()
    second.deleteLater()
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, 0)   # distrugge davvero i widget della seconda finestra
    theme.apply(QApplication.instance(), "dark")
    theme.apply(QApplication.instance(), "light")

    ctx = window.ctx
    view = window.open_path(paragraphs_pdf)
    ctx.set_mode("modifica")
    ctrl = controller_for(ctx)
    assert ctrl is ctx.text_edit
    block = [b for b in ctrl.blocks(view, 0) if "contratto" in b.text][0]
    view.tool.select(view, 0, block)
    ed = ctrl.open_editor(view, 0, block)
    ed.textCursor().insertText("Nuovo ")
    dlg = open_find_dialog(ctx)   # conferma l'editor di questa finestra, non di quella chiusa
    assert dlg is not None
    dlg.close()
    assert "Nuovo" in view.document.pdf[0].get_text()
    fonts = open_fonts_dialog(ctx)
    fonts.close()


def test_no_document_names_in_profile(window, paragraphs_pdf, quiet, qtbot, tmp_path):
    """Riservatezza: modificare e salvare un documento non lascia nel profilo isolato né nelle
    impostazioni del modulo il nome o la cartella del documento."""
    import os
    import shutil
    folder = tmp_path / "zq7k-cartella"
    folder.mkdir()
    path = folder / "zq7k-documento.pdf"
    shutil.copy(paragraphs_pdf, path)
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "contratto")
    c = block.bbox
    click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2)
    qtbot.keyClicks(ctrl.editor_for(view), "X")
    qtbot.keyClick(ctrl.editor_for(view), Qt.Key.Key_Escape)
    view.document.save(str(folder / "zq7k-documento-modificato.pdf"))
    s = window.ctx.settings()
    s.sync()
    assert all("zq7k" not in str(s.value(k)) for k in s.allKeys() if k.startswith("text_edit"))
    found = []
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        for base, _dirs, names in os.walk(os.environ.get(var) or os.devnull):
            for n in names:
                f = os.path.join(base, n)
                with open(f, "rb") as fh:
                    if "zq7k" in f or b"zq7k" in fh.read():
                        found.append(f)
    assert found == []
