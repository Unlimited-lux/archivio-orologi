"""Redazione: SEC-13…SEC-20 (logica) e schemi SEC-15/16."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.security import patterns as PT
from foliant.modules.security import redaction as R


def _all_text(path) -> str:
    pdf = pymupdf.open(path)
    text = "\n".join(p.get_text() for p in pdf)
    pdf.close()
    return text


def test_mark_is_preview_only(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    with doc.lock:
        assert "Pagina 1" in doc.pdf[0].get_text()
        marks = R.marks(doc.pdf)
    assert len(marks) == 1 and marks[0].page == 0 and "Pagina 1" in marks[0].text
    doc.close()


def test_apply_removes_text_and_saves(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle(overlay_text="OMISSIS"))
    with doc.change("Applica") as pdf:
        rep = R.apply(pdf)
    assert rep.marks == 1 and rep.pages == 1
    out = tmp_path / "redatto.pdf"
    doc.save(out)
    text = _all_text(out)
    assert "Pagina 1" not in text
    assert "Pagina 2" in text and "Orologeria Verdi" in text
    assert "OMISSIS" in text
    data = out.read_bytes()
    assert b"/Redact" not in data and b"/Prev" not in data
    doc.close()


def test_undo_apply(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    with doc.change("Applica") as pdf:
        R.apply(pdf)
    assert "Pagina 1" not in doc.page_text(0)
    doc.undo()
    assert "Pagina 1" in doc.page_text(0)
    doc.close()


def test_neighbour_line_kept(tmp_path):
    path = tmp_path / "righe.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Mario Rossi nato a", fontsize=12)
    page.insert_text((72, 114), "Roma il giorno", fontsize=12)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Segna") as pdf:
        matches = PT.find(pdf, [PT.term_pattern("Rossi")])
        assert len(matches) == 1
        R.add_marks(pdf, ((m.page, r, m.text) for m in matches for r in m.rects), R.MarkStyle(), "ricerca")
        R.apply(pdf)
    text = doc.page_text(0)
    assert "Rossi" not in text and "Mario" in text and "Roma il giorno" in text
    doc.close()


def test_image_pixels_removed(image_pdf, tmp_path):
    doc = Document.open(image_pdf)
    # metà sinistra dell'immagine (100..300 x 200..333)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(100, 200, 200, 333), R.MarkStyle(fill=(0, 0, 0)))
        R.apply(pdf)
    out = tmp_path / "img.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    imgs = pdf[0].get_images(full=True)
    assert imgs, "l'immagine coperta solo in parte resta"
    pix = pymupdf.Pixmap(pdf, imgs[0][0])
    if pix.n > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    w = pix.width
    left = pix.pixel(int(w * 0.2), pix.height // 2)
    right = pix.pixel(int(w * 0.8), pix.height // 2)
    assert left != (200, 30, 30)
    assert right == (200, 30, 30)
    doc.close()


def test_image_fully_covered_removed(image_pdf, tmp_path):
    doc = Document.open(image_pdf)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(90, 190, 310, 340), R.MarkStyle())
        R.apply(pdf, R.ApplyOptions(images=R.IMAGES_REMOVE))
    out = tmp_path / "img2.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    assert pdf[0].get_images() == []
    doc.close()


def test_vector_graphics_removed(image_pdf):
    doc = Document.open(image_pdf)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(340, 190, 460, 270), R.MarkStyle())
        R.apply(pdf)
        drawings = [d for d in pdf[0].get_drawings() if d.get("fill") == (0.0, 0.0, 1.0)]
    assert drawings == []
    doc.close()


def test_whole_pages_and_overlay(sample_pdf, tmp_path, make_pdf_with_annots):
    path = make_pdf_with_annots(tmp_path / "pagine.pdf", pages=6)
    doc = Document.open(path)
    with doc.change("Pagine") as pdf:
        n = R.mark_whole_pages(pdf, [1, 3], R.MarkStyle(overlay_text="Pagina omessa", fill=(0.2, 0.2, 0.2)))
        assert n == 2
        R.apply(pdf)
    out = tmp_path / "omesse.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    assert pdf.page_count == 6
    for i in (1, 3):
        page = pdf[i]
        assert page.get_text().strip() == "Pagina omessa"
        assert list(page.annots()) == [] and page.get_images() == []
        assert page.rect == pymupdf.Rect(0, 0, 595, 842)
    assert "Pagina 3" in pdf[2].get_text()
    assert len(list(pdf[2].annots())) == 1
    doc.close()


@pytest.fixture
def make_pdf_with_annots():
    def make(path, pages=6):
        src = pymupdf.open()
        for i in range(pages):
            page = src.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
            page.add_text_annot((300, 300), f"nota {i + 1}")
        src.save(str(path))
        return str(path)
    return make


def test_fill_color(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Colore") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(300, 400, 400, 500), R.MarkStyle(fill=(0x1F / 255, 0x3A / 255, 0x93 / 255)))
        R.apply(pdf)
        pix = pdf[0].get_pixmap()
    r, g, b = pix.pixel(350, 450)
    assert abs(r - 0x1F) <= 2 and abs(g - 0x3A) <= 2 and abs(b - 0x93) <= 2
    doc.close()


def test_repeat_overlay(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Ripeti") as pdf:
        rect = pymupdf.Rect(60, 400, 500, 420)
        R.add_mark(pdf, 0, rect, R.MarkStyle(overlay_text="OMISSIS", repeat_text=True))
        R.apply(pdf)
        text = pdf[0].get_textbox(rect)
    words = text.split()
    assert len(words) >= 3 and set(words) == {"OMISSIS"}
    doc.close()


def test_remove_mark_keeps_content(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        a = R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
        R.add_mark(pdf, 0, pymupdf.Rect(60, 125, 400, 150), R.MarkStyle())
        assert R.remove_mark(pdf, 0, a)
        R.apply(pdf)
    text = doc.page_text(0)
    assert "Pagina 1" in text and "Orologeria" not in text
    doc.close()


def test_mark_clipped_and_empty(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        assert R.add_mark(pdf, 0, pymupdf.Rect(10, 10, 10, 10), R.MarkStyle()) is None
        xref = R.add_mark(pdf, 0, pymupdf.Rect(-50, -50, 100, 100), R.MarkStyle())
        m = R.marks(pdf)[0]
    assert xref and m.rect.x0 >= 0 and m.rect.y0 >= 0
    doc.close()


def test_annotations_and_links_under_mark_removed(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Prep") as pdf:
        page = pdf[0]
        page.add_text_annot((100, 90), "commento sotto")
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 80, 200, 105), "uri": "https://x.it"})
        page.add_text_annot((400, 600), "commento lontano")
    with doc.change("Applica") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
        R.apply(pdf)
        page = pdf[0]
        contents = [a.info["content"] for a in page.annots()]
        links = page.get_links()
    assert contents == ["commento lontano"] and links == []
    doc.close()


def test_widget_under_mark_removed(tmp_path):
    path = tmp_path / "modulo.pdf"
    src = pymupdf.open()
    page = src.new_page()
    w = pymupdf.Widget()
    w.field_name = "cliente"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(100, 100, 300, 120)
    w.field_value = "CONFIDENZIALE-4711"
    page.add_widget(w)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Applica") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(90, 95, 310, 125), R.MarkStyle())
        R.apply(pdf)
    out = tmp_path / "modulo-redatto.pdf"
    doc.save(out)
    assert b"4711" not in out.read_bytes()
    pdf = pymupdf.open(out)
    assert list(pdf[0].widgets()) == []
    doc.close()


def test_rotated_page_mark(tmp_path):
    path = tmp_path / "ruotata.pdf"
    src = pymupdf.open()
    page = src.new_page(width=600, height=800)
    page.insert_text((50, 100), "Testo da nascondere", fontsize=12)
    page.insert_text((50, 400), "Testo da tenere", fontsize=12)
    page.set_rotation(90)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(40, 85, 250, 105), R.MarkStyle())
        R.apply(pdf)
    text = doc.page_text(0)
    assert "nascondere" not in text and "tenere" in text
    doc.close()


# ------------------------------------------------------------------ schemi


def test_patterns_find_each(pii_pdf):
    pdf = pymupdf.open(pii_pdf)
    found = {}
    for m in PT.find(pdf, PT.builtin_patterns(), pages=[0]):
        found.setdefault(m.pattern, []).append(m.text)
    assert found["email"] == ["mario.rossi@esempio.it"]
    assert found["iban"] == ["IT60 X054 2811 1010 0000 0123 456"]
    assert found["cf"] == ["RSSMRA80A01H501U"]
    assert found["vat"] == ["01234567897"]
    assert found["card"] == ["4111 1111 1111 1111"]
    assert found["date"] == ["12/03/1980"]
    assert "+39 347 123 4567" in found["phone"] and "02 1234 5678" in found["phone"]
    assert all("2811" not in p for p in found["phone"])
    assert found["amount"] and "12.500,00" in found["amount"][0]


def _text_pdf(tmp_path, lines, name="t.pdf"):
    path = tmp_path / name
    src = pymupdf.open()
    page = src.new_page()
    y = 80
    for line in lines:
        page.insert_text((60, y), line, fontsize=11)
        y += 20
    src.save(str(path))
    return pymupdf.open(path)


def test_cf_check_digit(tmp_path):
    pdf = _text_pdf(tmp_path, ["RSSMRA85T10A562S e RSSMRA85T10A562X"])
    found = [m.text for m in PT.find(pdf, [PT.pattern_by_id("cf")])]
    assert found == ["RSSMRA85T10A562S"]


def test_spec_examples(tmp_path):
    pdf = _text_pdf(tmp_path, ["mario.rossi@example.it", "+39 06 1234 5678", "10 dicembre 1985", "2024-03-15",
                               "importo 1.234.567,00", "data 31/02/2024"])
    found = {}
    for m in PT.find(pdf, [PT.pattern_by_id(k) for k in ("email", "phone", "date")]):
        found.setdefault(m.pattern, []).append(m.text)
    assert found["email"] == ["mario.rossi@example.it"]
    assert found["phone"] == ["+39 06 1234 5678"]
    assert found["date"] == ["10 dicembre 1985", "2024-03-15"]


def test_term_case_and_hyphen(tmp_path):
    pdf = _text_pdf(tmp_path, ["Mario Rossi e MARIO ROSSI", "poi Mario", "Rossi a capo", "e Nicolò"])
    found = PT.find(pdf, [PT.term_pattern("mario rossi")])
    assert len(found) == 3
    assert len(found[2].rects) == 2   # spezzata su due righe
    assert PT.find(pdf, [PT.term_pattern("mario rossi", case_sensitive=True)]) == []
    assert len(PT.find(pdf, [PT.term_pattern("Nicolo", ignore_accents=True)])) == 1
    assert PT.find(pdf, [PT.term_pattern("Nicolo")]) == []


def test_reapply_patterns_after_redaction(pii_pdf, tmp_path):
    doc = Document.open(pii_pdf)
    pats = PT.builtin_patterns()
    with doc.change("Segna") as pdf:
        matches = PT.find(pdf, pats)
        R.add_marks(pdf, ((m.page, r, m.text) for m in matches for r in m.rects), R.MarkStyle(), "schema")
        R.apply(pdf)
    out = tmp_path / "pulito.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    assert PT.find(pdf, pats) == []
    assert "Mario Rossi" in pdf[0].get_text()   # non è uno schema
    doc.close()


def test_custom_pattern_errors():
    with pytest.raises(ValueError):
        PT.custom_pattern("(abc")
    with pytest.raises(ValueError):
        PT.custom_pattern("a*")
    assert PT.custom_pattern(r"SN-\d{4}").regex


def test_invisible_ocr_text_found_and_removed(tmp_path):
    path = tmp_path / "ocr.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Matricola 12345678", fontsize=12, render_mode=3)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Segna") as pdf:
        ms = PT.find(pdf, [PT.term_pattern("12345678")])
        assert len(ms) == 1
        R.add_marks(pdf, ((m.page, r, m.text) for m in ms for r in m.rects), R.MarkStyle(), "ricerca")
        R.apply(pdf)
    assert "12345678" not in doc.page_text(0)
    doc.close()
