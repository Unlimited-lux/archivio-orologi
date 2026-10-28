"""Test del riconoscimento automatico dei campi (forms.detect)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.forms.detect import Candidate, detect, detect_page


def _doc() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    return doc, doc.new_page(width=595, height=842)


def _draw_form(page: pymupdf.Page) -> None:
    """Modulo di prova: «Nome:» con linea e casella «Accetto»."""
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100), width=1)
    page.draw_rect(pymupdf.Rect(72, 150, 84, 162), width=1)
    page.insert_text((90, 160), "Accetto", fontsize=11)


def test_label_and_line():
    doc, page = _doc()
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100))
    cands = detect_page(doc, 0)
    assert len(cands) == 1
    c = cands[0]
    assert (c.kind, c.name, c.source, c.page) == ("text", "Nome", "line", 0)
    assert c.rect.y1 <= 100 and c.rect.y1 >= 98
    assert 10 <= c.rect.height <= 18
    assert c.rect.x0 == pytest.approx(110) and c.rect.x1 == pytest.approx(300)


def test_checkbox_with_label_on_right():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 150, 84, 162))
    page.insert_text((90, 160), "Accetto", fontsize=11)
    cands = detect_page(doc, 0)
    assert len(cands) == 1
    c = cands[0]
    assert (c.kind, c.name, c.source) == ("checkbox", "Accetto", "box")
    assert c.rect == pymupdf.Rect(73, 151, 83, 161)


def test_big_empty_rect_is_multiline():
    doc, page = _doc()
    page.insert_text((72, 290), "Note", fontsize=11)
    page.draw_rect(pymupdf.Rect(72, 300, 400, 400))
    (c,) = detect_page(doc, 0)
    assert c.kind == "text" and c.multiline and c.name == "Note"


def test_small_empty_rect_is_single_line():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 300, 250, 320))
    (c,) = detect_page(doc, 0)
    assert c.kind == "text" and not c.multiline and c.name == ""


def test_rect_with_text_is_ignored():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 300, 400, 340), fill=(0.9, 0.9, 0.9))
    page.insert_text((80, 325), "Intestazione del modulo", fontsize=11)
    assert detect_page(doc, 0) == []


def test_underscores():
    doc, page = _doc()
    page.insert_text((72, 200), "Cognome: __________", fontsize=11)
    page.insert_text((72, 240), "Citta:__________", fontsize=11)
    cands = detect_page(doc, 0)
    assert [c.name for c in cands] == ["Cognome", "Citta"]
    first = cands[0]
    assert first.kind == "text" and first.source == "underscore"
    label = page.search_for("Cognome:")[0]
    assert first.rect.x0 > label.x1 - 1
    assert first.rect.y1 == pytest.approx(200, abs=2)


def test_underlined_text_not_detected():
    doc, page = _doc()
    page.insert_text((72, 200), "Testo sottolineato importante", fontsize=11)
    width = pymupdf.get_text_length("Testo sottolineato importante",
                                    fontsize=11)
    page.draw_line((72, 201.5), (72 + width, 201.5))
    assert detect_page(doc, 0) == []


def test_table_grid_not_detected():
    doc, page = _doc()
    for y in (100, 120, 140):
        page.draw_line((72, y), (372, y))
    for x in (72, 172, 272, 372):
        page.draw_line((x, 100), (x, 140))
    assert all(c.source != "line" for c in detect_page(doc, 0))


def test_existing_widget_suppresses_candidate():
    doc, page = _doc()
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100))
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "nome"
    w.rect = pymupdf.Rect(110, 82, 300, 99)
    page.add_widget(w)
    assert detect_page(doc, 0) == []


def test_scanned_page():
    src, page = _doc()
    _draw_form(page)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
    doc = pymupdf.open()
    scan = doc.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    cands = detect_page(doc, 0)
    assert {c.source for c in cands} == {"raster"}
    texts = [c for c in cands if c.kind == "text"]
    checks = [c for c in cands if c.kind == "checkbox"]
    assert len(texts) == 1 and len(checks) == 1
    t, k = texts[0].rect, checks[0].rect
    assert t.x0 == pytest.approx(110, abs=4) and t.x1 == pytest.approx(300, abs=4)
    assert t.y1 == pytest.approx(99, abs=4)
    for a, b in zip(k, (73, 151, 83, 161)):
        assert a == pytest.approx(b, abs=4)
    # senza analisi raster non si trova nulla
    assert detect_page(doc, 0, raster=False) == []


def test_scanned_rotated_page():
    # scansione orizzontale raddrizzata con /Rotate 90: si vede diritta
    src, page = _doc()
    _draw_form(page)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    doc = pymupdf.open()
    scan = doc.new_page(width=842, height=595)
    scan.set_rotation(90)
    scan.insert_image(scan.rect * scan.derotation_matrix, pixmap=pix,
                      rotate=90)
    assert scan.rect.width == 595
    derot = scan.derotation_matrix
    cands = detect_page(doc, 0)
    checks = [c for c in cands if c.kind == "checkbox"]
    texts = [c for c in cands if c.kind == "text"]
    assert len(checks) == 1 and len(texts) == 1
    assert checks[0].rect.contains(pymupdf.Point(78, 156) * derot)
    assert texts[0].rect.contains(pymupdf.Point(200, 95) * derot)
    # in coordinate non ruotate il campo di testo è «verticale»
    assert texts[0].rect.height > texts[0].rect.width


def test_rotated_vector_page():
    doc, page = _doc()
    _draw_form(page)
    page.set_rotation(90)
    cands = detect_page(doc, 0)
    by_kind = {c.kind: c for c in cands}
    assert set(by_kind) == {"text", "checkbox"}
    assert by_kind["text"].rect.contains(pymupdf.Point(200, 95))
    assert by_kind["text"].name == "Nome"
    assert by_kind["checkbox"].rect.contains(pymupdf.Point(78, 156))
    assert by_kind["checkbox"].name == "Accetto"


def test_empty_page():
    doc, _ = _doc()
    assert detect_page(doc, 0) == []
    assert detect_page(doc, 0, raster=True) == []


def test_sorted_and_named_in_order():
    doc, page = _doc()
    _draw_form(page)
    page.insert_text((320, 100), "Cognome:", fontsize=11)
    page.draw_line((370, 100), (520, 100))
    names = [c.name for c in detect_page(doc, 0)]
    assert names == ["Nome", "Cognome", "Accetto"]


def test_detect_progress():
    doc = pymupdf.open()
    for _ in range(3):
        _draw_form(doc.new_page())
    calls: list[tuple[float, str]] = []
    cands = detect(doc, progress=lambda f, t: calls.append((f, t)))
    assert len(calls) == 3 and calls[-1][0] == pytest.approx(1.0)
    assert [c.page for c in cands] == [0, 0, 1, 1, 2, 2]
    assert all(isinstance(c, Candidate) for c in cands)
    assert [c.page for c in detect(doc, pages=[2])] == [2, 2]
