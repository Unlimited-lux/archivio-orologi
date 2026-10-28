"""Test dell'appiattimento (CNV-33)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.convert.flatten import flatten_pdf_bytes


def _render(doc, i=0):
    from PIL import Image
    pix = doc[i].get_pixmap(dpi=100)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _diff(a, b) -> float:
    from PIL import ImageChops
    d = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 8 else 0)
    return sum(d.histogram()[255:]) / (a.width * a.height) * 100


@pytest.fixture
def annotated():
    doc = pymupdf.open()
    page = doc.new_page()
    for k in range(3):
        page.insert_text((72, 100 + k * 30), f"Riga evidenziata {k}", fontsize=12)
        page.add_highlight_annot(pymupdf.Rect(70, 88 + k * 30, 200, 104 + k * 30))
    page.add_stamp_annot(pymupdf.Rect(300, 300, 450, 350), stamp=0)
    for k in range(5):
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = f"campo{k}"
        w.rect = pymupdf.Rect(72, 400 + k * 30, 272, 420 + k * 30)
        w.field_value = f"valore {k}"
        page.add_widget(w)
    data = doc.tobytes()
    doc.close()
    return data


def test_flatten_all(annotated):
    before = pymupdf.open(stream=annotated)
    ref = _render(before)
    out, report = flatten_pdf_bytes(annotated)
    doc = pymupdf.open(stream=out)
    assert list(doc[0].annots()) == [] and list(doc[0].widgets()) == []
    assert doc.xref_get_key(doc.pdf_catalog(), "AcroForm")[0] == "null"
    assert _diff(ref, _render(doc)) <= 0.5
    assert "valore 3" in doc[0].get_text()


def test_links_kept_comment_flattened():
    doc = pymupdf.open()
    p = doc.new_page()
    for k in range(2):
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 100 + k * 30, 200, 115 + k * 30),
                       "uri": f"https://x{k}.example.com"})
    p.add_text_annot((300, 300), "Commento")
    out, report = flatten_pdf_bytes(doc.tobytes())
    res = pymupdf.open(stream=out)
    assert len(res[0].get_links()) == 2 and list(res[0].annots(types=[pymupdf.PDF_ANNOT_TEXT])) == []
    assert any("note" in w for w in report["warnings"])


def test_flatten_by_type_on_range(annotated):
    out, _r = flatten_pdf_bytes(annotated, annotation_types={pymupdf.PDF_ANNOT_HIGHLIGHT}, forms=False, pages="1")
    doc = pymupdf.open(stream=out)
    types = [a.type[0] for a in doc[0].annots()]
    assert pymupdf.PDF_ANNOT_HIGHLIGHT not in types and pymupdf.PDF_ANNOT_STAMP in types
    assert len(list(doc[0].widgets())) == 5
    assert "Riga evidenziata 1" in doc[0].get_text()


def test_flatten_crop():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((100, 100), "FUORI", fontsize=14)
    p.insert_text((100, 800), "DENTRO", fontsize=14)
    p.set_cropbox(pymupdf.Rect(50, 300, 545, 842))   # coordinate di pagina: esclude la parte alta
    crop = pymupdf.Rect(p.cropbox)
    out, report = flatten_pdf_bytes(doc.tobytes(), annotations=False, forms=False, layers=False, crop=True)
    res = pymupdf.open(stream=out)
    assert pymupdf.Rect(res[0].mediabox) == pymupdf.Rect(0, 0, crop.width, crop.height) or \
        abs(res[0].mediabox.width - crop.width) < 0.01
    text = res[0].get_text(flags=0)
    assert "DENTRO" in text and "FUORI" not in text
    res[0].set_mediabox(pymupdf.Rect(-1000, -1000, 2000, 2000))
    assert "FUORI" not in res[0].get_text(flags=0)


def test_flatten_hidden_layer():
    doc = pymupdf.open()
    p = doc.new_page()
    hidden = doc.add_ocg("Nascosto", on=False)
    visible = doc.add_ocg("Visibile", on=True)
    p.insert_text((72, 100), "TESTO NASCOSTO", fontsize=12, oc=hidden)
    p.insert_text((72, 140), "TESTO VISIBILE", fontsize=12, oc=visible)
    p.insert_text((72, 180), "BASE", fontsize=12)
    out, report = flatten_pdf_bytes(doc.tobytes(), annotations=False, forms=False, layers=True)
    res = pymupdf.open(stream=out)
    raw = b"".join(res.xref_stream(x) or b"" for x in res[0].get_contents())
    text = res[0].get_text()
    assert "TESTO VISIBILE" in text and "BASE" in text
    assert "NASCOSTO" not in text
    assert res.xref_get_key(res.pdf_catalog(), "OCProperties")[0] == "null"
    assert b"BDC" not in raw or b"/OC" not in raw


def test_rasterize_with_ocr(sample_pdf):
    from foliant.modules.convert.extract import ocr_available
    data = open(sample_pdf, "rb").read()
    out, report = flatten_pdf_bytes(data, rasterize=True, dpi=200, pages="1")
    res = pymupdf.open(stream=out)
    info = res[0].get_image_info()
    assert len(info) == 1
    bbox = pymupdf.Rect(info[0]["bbox"])
    ppi = info[0]["width"] / (bbox.width / 72)
    assert abs(ppi - 200) <= 2
    if ocr_available():
        assert "Pagina" in res[0].get_text()
    assert res.page_count == 3 and "Pagina 2" in res[1].get_text()


def test_flatten_crop_top_half_keeps_visible():
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=400)
    p.draw_rect(p.rect, color=None, fill=(0.9, 0.9, 1))
    p.insert_text((50, 80), "Visibile", fontsize=14)
    p.insert_text((50, 350), "Esterno", fontsize=14)
    p.set_cropbox(pymupdf.Rect(0, 0, 400, 200))       # metà superiore (coordinate di pagina)
    out, _r = flatten_pdf_bytes(doc.tobytes(), annotations=False, forms=False, layers=False, crop=True)
    res = pymupdf.open(stream=out)
    assert "Visibile" in res[0].get_text() and "Esterno" not in res[0].get_text()
    assert abs(res[0].rect.height - 200) < 0.5
    assert res[0].get_drawings()   # lo sfondo non viene eliminato


def test_layers_whole_document_and_raster_keeps_toc():
    doc = pymupdf.open()
    hidden = doc.add_ocg("Nascosto", on=False)
    for k in range(2):
        p = doc.new_page()
        p.insert_text((72, 100), f"NASCOSTO {k + 1}", fontsize=12, oc=hidden)
        p.insert_text((72, 140), f"Testo {k + 1}", fontsize=12)
    doc.set_toc([[1, "Cap 1", 1], [1, "Cap 2", 2]])
    out, _r = flatten_pdf_bytes(doc.tobytes(), annotations=False, forms=False, layers=True, pages="1")
    res = pymupdf.open(stream=out)
    assert all("NASCOSTO" not in res[i].get_text() for i in range(2))
    out2, _r = flatten_pdf_bytes(out, annotations=False, forms=False, layers=False, rasterize=True, dpi=72,
                                 ocr=False, pages="1")
    res2 = pymupdf.open(stream=out2)
    assert res2.get_toc() == [[1, "Cap 1", 1], [1, "Cap 2", 2]]
