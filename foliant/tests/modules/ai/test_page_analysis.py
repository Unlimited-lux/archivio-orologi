"""AI-24 — Analisi e pulizia guidata delle pagine."""

from __future__ import annotations

import json

import pymupdf
import pytest

from foliant.core.document import Document
from foliant.modules.ai import page_analysis as pa

BODY = "Contenuto della pagina {n}: paragrafo di prova con testo sufficiente per l'analisi. " * 4


def _normal(page, n):
    page.insert_textbox(pymupdf.Rect(72, 72, 520, 760), BODY.format(n=n), fontsize=11)


@pytest.fixture
def six_pages(tmp_path):
    """6 pagine: la 4 bianca, la 5 con il testo ruotato di 90°."""
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page()
        if i == 3:
            continue
        if i == 4:
            page.insert_text((300, 700), "Testo ruotato di novanta gradi nella pagina cinque", fontsize=12, rotate=90)
            page.insert_text((330, 700), "Seconda riga ruotata della stessa pagina", fontsize=12, rotate=90)
            continue
        _normal(page, i + 1)
    path = tmp_path / "sei.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_local_analysis_finds_blank_and_rotation_without_ai(six_pages, fake_provider):
    document = Document.open(six_pages)
    issues = pa.analyze(document.pdf)
    blank = [it for it in issues if it.problem == pa.BLANK]
    rotated = [it for it in issues if it.problem == pa.ROTATION]
    assert [it.page for it in blank] == [3]
    assert blank[0].action == pa.DELETE and blank[0].auto_select and blank[0].origin == pa.LOCAL
    assert "%" in blank[0].evidence
    assert [it.page for it in rotated] == [4]
    assert rotated[0].action == pa.ROTATE and rotated[0].angle == 90
    assert fake_provider.requests == []


def test_rotated_page_attribute_detected(tmp_path):
    doc = pymupdf.open()
    for i in range(4):
        _normal(doc.new_page(), i + 1)
    doc[2].set_rotation(90)
    path = tmp_path / "r.pdf"
    doc.save(str(path))
    document = Document.open(str(path))
    issues = pa.analyze(document.pdf)
    rot = [it for it in issues if it.problem == pa.ROTATION]
    assert [it.page for it in rot] == [2]
    assert not [it for it in issues if it.problem == pa.SIZE]
    pa.apply(document, rot)
    assert document.pdf[2].rotation == 0


def test_delete_blank_page_single_undo(six_pages):
    document = Document.open(six_pages)
    issues = pa.analyze(document.pdf)
    blank = [it for it in issues if it.problem == pa.BLANK]
    result = pa.apply(document, blank)
    assert result.deleted == [3]
    assert document.page_count == 5
    assert document.can_undo
    document.undo()
    assert document.page_count == 6
    assert not document.can_undo


def test_combined_actions_one_undo_step(six_pages):
    document = Document.open(six_pages)
    issues = pa.analyze(document.pdf)
    pa.apply(document, [it for it in issues if it.problem in (pa.BLANK, pa.ROTATION)])
    assert document.page_count == 5
    assert document.pdf[3].rotation == 90           # ex pagina 5, ora ruotata
    document.undo()
    assert document.page_count == 6
    assert document.pdf[4].rotation == 0
    assert not document.can_undo


def test_near_blank_page_number_not_auto_selected(tmp_path):
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page()
        if i == 2:
            page.insert_text((297, 800), "3", fontsize=10)
        else:
            _normal(page, i + 1)
    path = tmp_path / "n.pdf"
    doc.save(str(path))
    issues = pa.analyze(Document.open(str(path)).pdf)
    near = [it for it in issues if it.page == 2]
    assert len(near) == 1 and near[0].problem == pa.NEAR_BLANK
    assert near[0].auto_select is False
    assert "«3»" in near[0].evidence
    assert not [it for it in issues if it.problem == pa.BLANK]


def test_size_annotations_and_ocr(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        _normal(doc.new_page(), i + 1)
    small = doc.new_page(width=400, height=400)
    small.insert_text((40, 60), "Pagina di formato diverso con testo", fontsize=11)
    doc[0].add_text_annot((100, 100), "Nota da conservare")
    scan = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
    pix.set_rect(pix.irect, (120, 120, 120))
    scan.insert_image(scan.rect, pixmap=pix)
    path = tmp_path / "misti.pdf"
    doc.save(str(path))
    document = Document.open(str(path))
    issues = pa.analyze(document.pdf)
    by = {(it.problem, it.page) for it in issues}
    assert (pa.SIZE, 3) in by
    assert (pa.ANNOTATIONS, 0) in by
    assert (pa.NEEDS_OCR, 4) in by
    chosen = [it for it in issues if it.problem in (pa.SIZE, pa.ANNOTATIONS, pa.NEEDS_OCR)]
    result = pa.apply(document, chosen)
    assert "Nota da conservare" in result.annotations_text
    assert result.ocr_pages == [4]
    assert result.normalized == [3]
    assert round(document.pdf[3].rect.width) == 595
    assert "formato diverso" in document.pdf[3].get_text()
    document.undo()
    assert round(document.pdf[3].rect.width) == 400


def test_ai_classification_sends_only_candidates(text_pdf, job, fake_provider):
    path = text_pdf(5, texts=[f"Testo esclusivo numero{i + 1} della pagina" for i in range(5)])
    document = Document.open(path)
    fake_provider.responses = [json.dumps([
        {"page": 2, "category": "separator", "reason": "foglio separatore"},
        {"page": 4, "category": "other", "reason": "pagina normale"},
        {"page": 5, "category": "duplicate", "reason": "non richiesta"},
    ])]
    issues = pa.classify_with_ai(job, document.pdf, [1, 3])
    assert len(fake_provider.requests) == 1
    sent = fake_provider.all_text()
    assert "numero2" in sent and "numero4" in sent
    for n in (1, 3, 5):
        assert f"numero{n} " not in sent
    assert [(it.page, it.problem, it.origin, it.auto_select) for it in issues] == [(1, "ai_separator", "IA", False)]
    assert issues[0].evidence == "foglio separatore"
    line = pa.describe_ai_send(document.pdf, [1, 3], job.profile())
    assert line.startswith("Verrà inviato") and "pagine 2, 4" in line


def test_ai_sends_low_res_image_for_textless_page(tmp_path, job, fake_provider):
    doc = pymupdf.open()
    _normal(doc.new_page(), 1)
    scan = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 140), False)
    pix.set_rect(pix.irect, (90, 90, 90))
    scan.insert_image(scan.rect, pixmap=pix)
    path = tmp_path / "img.pdf"
    doc.save(str(path))
    fake_provider.responses = ["[]"]
    assert pa.classify_with_ai(job, Document.open(str(path)).pdf, [1]) == []
    assert fake_provider.requests[0].image_count() == 1


def test_dialog_smoke(qtbot, six_pages, job, fake_provider):
    from PySide6.QtCore import Qt

    from foliant.modules.ai.page_analysis_view import PageAnalysisDialog

    document = Document.open(six_pages)
    issues = pa.analyze(document.pdf)
    dlg = PageAnalysisDialog(document, issues, job_factory=lambda: job)
    qtbot.addWidget(dlg)
    assert dlg.classify_button.isEnabled()
    assert dlg.send_label.text().startswith("Verrà inviato")
    selected = dlg.selected_issues()
    assert {(it.problem, it.page) for it in selected} >= {(pa.BLANK, 3), (pa.ROTATION, 4)}
    # lascia selezionata solo l'eliminazione della pagina bianca
    for row in range(dlg.list.count()):
        item = dlg.list.item(row)
        it = item.data(Qt.ItemDataRole.UserRole + 1)
        if it is not None and it.problem != pa.BLANK:
            item.setCheckState(Qt.CheckState.Unchecked)
    fake_provider.responses = ["[]"]
    dlg.classify()
    assert len(fake_provider.requests) == 1
    dlg.apply_selected()
    assert document.page_count == 5
    document.undo()
    assert document.page_count == 6


def test_dialog_empty_and_without_profile(qtbot, text_pdf):
    from foliant.modules.ai.page_analysis_view import PageAnalysisDialog

    document = Document.open(text_pdf(3))
    issues = pa.analyze(document.pdf)
    assert issues == []
    dlg = PageAnalysisDialog(document, issues, job_factory=None)
    qtbot.addWidget(dlg)
    assert not dlg.classify_button.isEnabled()
    assert not dlg.empty.isHidden() and dlg.empty.text() == "Nessun problema trovato"
    assert not dlg.apply_button.isEnabled()


def test_percent_and_amount_texts_follow_language():
    from foliant import i18n
    from foliant.modules.ai import extract, page_analysis
    assert page_analysis._percent(0.0013) == "0,13" and extract._fmt(1234.5) == "1.234,50"
    i18n.set_language("en")
    try:
        assert page_analysis._percent(0.0013) == "0.13" and page_analysis._percent(0.25) == "25.0"
        assert extract._fmt(1234.5) == "1,234.50"
    finally:
        i18n.set_language("it")
