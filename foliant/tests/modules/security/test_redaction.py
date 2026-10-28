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


def test_xfdf_roundtrip(sample_pdf):
    src = Document.open(sample_pdf)
    with src.change("Segna") as pdf:
        R.add_mark(pdf, 1, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle(fill=(0.2, 0.4, 0.6), overlay_text="OMISSIS"))
        data = R.export_xfdf(pdf, "esempio.pdf")
    assert "<redact" in data and 'overlay-text="OMISSIS"' in data
    dst = Document.open(sample_pdf)
    with dst.change("Importa") as pdf:
        assert R.import_xfdf(pdf, data) == 1
        m = R.marks(pdf)[0]
    assert m.page == 1 and abs(m.rect.x0 - 60) < 0.01 and abs(m.rect.y1 - 110) < 0.01
    with pytest.raises(ValueError):
        with dst.change("x") as pdf:
            R.import_xfdf(pdf, "<xfdf")
    src.close()
    dst.close()


def test_thumbnail_removed_on_apply(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        x = pdf.get_new_xref()
        pdf.update_object(x, "<< /Width 1 /Height 1 >>")
        pdf.xref_set_key(pdf[0].xref, "Thumb", f"{x} 0 R")
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
        R.apply(pdf)
        assert pdf.xref_get_key(pdf[0].xref, "Thumb")[0] == "null"
    doc.close()


def test_include_invalid_check_digit(tmp_path):
    from foliant.modules.security import patterns as P2
    from dataclasses import replace
    pdf = _text_pdf(tmp_path, ["RSSMRA85T10A562X"])
    p = P2.pattern_by_id("cf")
    assert P2.find(pdf, [p]) == []
    assert len(P2.find(pdf, [replace(p, check=None)])) == 1


def test_bug07_line_crossing_mark_removed(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("Segna") as pdf:
        pdf[0].draw_line((300, 420), (500, 620), color=(0, 0, 0), width=2)
        R.add_mark(pdf, 0, pymupdf.Rect(350, 480, 450, 560), R.MarkStyle())
        R.apply(pdf)
    out = tmp_path / "linea.pdf"
    doc.save(out)
    area = pymupdf.Rect(350, 480, 450, 560)
    for d in pymupdf.open(out)[0].get_drawings():
        for item in d["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                for k in range(21):
                    pt = p + (q - p) * (k / 20)
                    assert not (area.x0 < pt.x < area.x1 and area.y0 < pt.y < area.y1)
    doc.close()


def test_bug08_image_pixels_take_fill_color(tmp_path):
    path = tmp_path / "foto.pdf"
    src = pymupdf.open()
    page = src.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pix.set_rect(pix.irect, (40, 160, 90))
    page.insert_image(pymupdf.Rect(72, 420, 272, 620), pixmap=pix)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(172, 470, 272, 570), R.MarkStyle(fill=(0, 0, 0)))
        R.apply(pdf)
    out = tmp_path / "foto-redatta.pdf"
    doc.save(out)
    res = pymupdf.open(out)
    xref = res[0].get_images()[0][0]
    img = pymupdf.Pixmap(res, xref)
    if img.n > 3:
        img = pymupdf.Pixmap(pymupdf.csRGB, img)
    sx = img.width / 200
    assert img.pixel(int(150 * sx), int(100 * sx)) == (0, 0, 0)       # dentro l'area
    assert img.pixel(int(30 * sx), int(100 * sx)) == (40, 160, 90)    # fuori
    doc.close()


def test_bug05_rotated_cropbox_fill_position(tmp_path):
    path = tmp_path / "ritaglio.pdf"
    src = pymupdf.open()
    page = src.new_page(width=600, height=800)
    page.insert_text((200, 300), "BERSAGLIO", fontsize=14)
    page.set_cropbox(pymupdf.Rect(100, 150, 500, 700))
    page.set_rotation(90)
    src.save(str(path))
    doc = Document.open(path)
    with doc.lock:
        hit = doc.pdf[0].search_for("BERSAGLIO")[0]
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, hit + (-2, -2, 2, 2), R.MarkStyle(fill=(0, 0, 0)))
        R.apply(pdf)
        p = pdf[0]
        assert "BERSAGLIO" not in p.get_text()
        pix = p.get_pixmap(matrix=pymupdf.Matrix(1, 1))
        c = hit.tl + (hit.br - hit.tl) * 0.5
        pt = c * p.rotation_matrix
        assert pix.pixel(int(pt.x), int(pt.y)) == (0, 0, 0)
    doc.close()


@pytest.mark.parametrize("font", [{"fontname": "helv"},
                                  {"fontname": "F0", "fontfile": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}])
@pytest.mark.parametrize("margin", [0, 1])
def test_bug06_following_text_keeps_position(tmp_path, font, margin):
    import os
    if "fontfile" in font and not os.path.exists(font["fontfile"]):
        pytest.skip("font non disponibile")
    path = tmp_path / "riga.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Prima parola CONFIDENZIALE-4711 ultima parola", fontsize=12, **font)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Redigi") as pdf:
        words = pdf[0].get_text("words")
        before = {w[4]: w[0] for w in words if w[4] in ("ultima", "Prima")}
        R.add_mark(pdf, 0, pymupdf.Rect(words[2][:4]) + (-margin, -1, margin, 1), R.MarkStyle())
        R.apply(pdf)
        after = {w[4]: w[0] for w in pdf[0].get_text("words") if w[4] in ("ultima", "Prima")}
        assert "CONFIDENZIALE" not in pdf[0].get_text()
    assert abs(after["ultima"] - before["ultima"]) < 0.01
    assert abs(after["Prima"] - before["Prima"]) < 0.01
    doc.close()


def test_ocr_scan_redaction_covers_full_glyph_height(tmp_path):
    """Scansione con testo OCR invisibile: il riquadro copre tutta l'altezza della parola
    nell'immagine (nessuna riga di pixel dei glifi resta visibile)."""
    path = tmp_path / "scansione.pdf"
    src = pymupdf.open()
    page = src.new_page(width=612, height=396)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1700, 1100), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    # «glifi» scuri nell'immagine dove si trova la parola (y 255,6–271,1 pt)
    s = 1700 / 612
    pix.set_rect(pymupdf.IRect(int(130 * s), int(255.6 * s), int(248 * s), int(271.1 * s)), (20, 20, 20))
    page.insert_image(page.rect, pixmap=pix)
    # testo OCR invisibile con il riquadro dei glifi uguale alla parola nell'immagine
    tw = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("helv")
    size = 15.5 / (font.ascender - font.descender)
    tw.append((130, 255.6 + font.ascender * size), "CA12345XY", font=font, fontsize=size)
    tw.write_text(page, render_mode=3)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Redigi", kind="redaction") as pdf:
        matches = PT.find(pdf, [PT.term_pattern("CA12345XY")])
        assert len(matches) == 1
        r = matches[0].rects[0]
        assert r.y0 <= 255.7 and r.y1 >= 271.0          # niente riduzione per il testo OCR
        R.add_marks(pdf, ((m.page, x, m.text) for m in matches for x in m.rects), R.MarkStyle(), "ricerca")
        R.apply(pdf)
        # tutta l'altezza dei glifi (a meno di un bordo di antialiasing di 0,2 pt)
        out = pdf[0].get_pixmap(dpi=200, clip=pymupdf.Rect(r.x0 + 0.2, 255.8, r.x1 - 0.2, 270.9))
    light = [(x, y) for x in range(0, out.width, 7) for y in range(out.height) if out.pixel(x, y)[0] >= 40]
    assert not light, (len(light), light[:5], out.width, out.height, r)      # tutta l'area è coperta dal riempimento nero (nessun pixel chiaro di sfondo)
    assert "CA12345XY" not in doc.page_text(0)
    doc.close()


def test_identity_document_numbers(tmp_path):
    """Dati dei clienti nei documenti d'identità allegati ai contratti di compravendita."""
    pdf = _text_pdf(tmp_path, ["Carta d'identità n. CA12345XY rilasciata dal Comune",
                               "Passaporto YA1234567, patente MI1234567A",
                               "Orologio Submariner ref. 116610LN, prezzo EUR 9.500"])
    found = [m.text for m in PT.find(pdf, [PT.pattern_by_id("iddoc")])]
    assert found == ["CA12345XY", "YA1234567", "MI1234567A"]
