"""OBJ-12, OBJ-26, OBJ-32: trasformazioni numeriche, scomporre/raggruppare, replica su pagine."""

from __future__ import annotations

import io
import math

import pymupdf
import pytest
from PIL import Image

from foliant.core.document import Document
from foliant.modules.objects import advanced as A
from foliant.modules.objects import engine as E
from foliant.modules.objects.content import FORM, PATH


def png(w, h, color=(200, 20, 20)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def render(doc, i=0):
    return doc[i].get_pixmap(dpi=72).samples


def count_images(data: bytes) -> int:
    d = pymupdf.open(stream=data, filetype="pdf")
    return sum(1 for x in range(1, d.xref_length()) if d.xref_get_key(x, "Subtype")[1] == "/Image")


def test_replicate_logo_all_pages_single_data():
    doc = pymupdf.open()
    for _k in range(20):
        doc.new_page()
    doc[0].insert_image(pymupdf.Rect(20, 20, 120, 70), stream=png(300, 150))
    done = A.replicate(doc, 0, [0], range(20))
    assert done == list(range(1, 20))
    data = doc.tobytes(garbage=3, deflate=True)
    assert count_images(data) == 1
    d = pymupdf.open(stream=data, filetype="pdf")
    for i in range(20):
        info = d[i].get_image_info()
        assert info and abs(info[0]["bbox"][0] - 20) < 0.5 and abs(info[0]["bbox"][1] - 20) < 0.5


def test_replicate_skips_different_size():
    doc = pymupdf.open()
    for k in range(4):
        doc.new_page(width=842 if k >= 2 else 595, height=1191 if k >= 2 else 842)
    doc[0].insert_image(pymupdf.Rect(20, 20, 120, 70), stream=png(30, 15))
    before = [render(doc, i) for i in (2, 3)]
    assert A.replicate(doc, 0, [0], range(4), skip_different=True) == [1]
    assert [render(doc, i) for i in (2, 3)] == before


def test_skew_keeps_height():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 200, 200), color=None, fill=(0, 0, 1))
    A.apply_transform(doc, 0, [0], skew_x=20)
    o = E.objects(doc, 0).objects[0]
    assert abs(o.quad.rect.height - 100) < 0.01
    assert abs(o.quad.rect.width - (100 + 100 * math.tan(math.radians(20)))) < 0.05


def test_skew_out_of_range_rejected():
    with pytest.raises(E.ObjectError):
        A.transform_matrix(skew_x=85)


def test_reset_transform_rotated_flipped_image():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(100, 100, 300, 200), stream=png(200, 100), keep_proportion=False)
    E.rotate(doc, 0, [0], 37)
    E.flip(doc, 0, [0], True)
    c0 = E.objects(doc, 0).objects[0].quad.rect
    c0 = ((c0.x0 + c0.x1) / 2, (c0.y0 + c0.y1) / 2)
    A.reset_transform(doc, 0, [0])
    o = E.objects(doc, 0).objects[0]
    m = o.ctm
    assert abs(m.b) < 1e-4 and abs(m.c) < 1e-4 and m.a > 0 and m.d > 0
    r = o.quad.rect
    assert abs((r.x0 + r.x1) / 2 - c0[0]) < 0.01 and abs((r.y0 + r.y1) / 2 - c0[1]) < 0.01
    assert abs(r.width / r.height - 2) < 1e-3


def _logo_form_doc():
    doc = pymupdf.open()
    src = pymupdf.open()
    sp = src.new_page(width=200, height=100)
    for k in range(5):
        sp.draw_rect(pymupdf.Rect(10 + 35 * k, 10, 40 + 35 * k, 60), color=(0, 0, 0), fill=(k / 5, 0.3, 0.6))
    page = doc.new_page()
    page.show_pdf_page(pymupdf.Rect(100, 100, 300, 200), src, 0)
    return doc


def test_decompose_form_same_render():
    doc = _logo_form_doc()
    objs = E.objects(doc, 0).objects
    assert [o.kind for o in objs] == [FORM]
    before = render(doc)
    for _k in range(3):
        objs = E.objects(doc, 0).objects
        forms = [o.index for o in objs if o.kind == FORM]
        if not forms:
            break
        A.decompose(doc, 0, forms[0])
    objs = E.objects(doc, 0).objects
    assert len(objs) == 5 and all(o.kind == PATH for o in objs)
    after = render(doc)
    diff = sum(1 for a, b in zip(before, after) if abs(a - b) > 1)
    assert diff == 0


def test_decompose_transparency_group_needs_confirmation():
    doc = _logo_form_doc()
    xref = E.objects(doc, 0).objects[0].xref
    doc.xref_set_key(xref, "Group", "<</S/Transparency/K true>>")
    from foliant.modules.objects.content import page_content
    data = page_content(doc, doc[0])
    with pytest.raises(A.TransparencyGroup):
        A.decompose(doc, 0, 0)
    assert page_content(doc, doc[0]) == data


def test_group_move_and_ungroup():
    d = Document.new()
    with d.change("prep") as pdf:
        page = pdf[0]
        for k in range(3):
            page.draw_rect(pymupdf.Rect(50 + 60 * k, 50, 100 + 60 * k, 100), color=(1, 0, 0), fill=(0, 1, 0))
    with d.change("Raggruppa") as pdf:
        new = A.group(pdf, 0, [0, 1, 2])
    with d.lock:
        objs = E.objects(d.pdf, 0).objects
    assert len(objs) == 1 and objs[0].kind == FORM and new == [0]
    with d.change("Sposta") as pdf:
        E.translate(pdf, 0, [0], 30, 0)
    with d.change("Separa") as pdf:
        A.decompose(pdf, 0, 0)
    with d.lock:
        objs = E.objects(d.pdf, 0).objects
    assert len(objs) == 3
    assert [round(o.quad.rect.x0) for o in objs] == [80, 140, 200]


def test_restyle_cmyk_fill_red():
    doc = pymupdf.open()
    page = doc.new_page()
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"0 0 1 0 k 100 600 100 100 re f", new=True)
    doc.xref_set_key(page.xref, "Contents", f"{x} 0 R")
    A.restyle_paths(doc, 0, [0], fill=(0, 1, 1, 0))
    from foliant.modules.objects.content import page_content
    assert b"0 1 1 0 k" in page_content(doc, doc[0])
    pix = doc[0].get_pixmap(dpi=72)
    r, g, b = pix.pixel(150, 200)[:3]
    assert r > 200 and g < 60 and b < 60


def test_restyle_width_and_dash_and_no_fill():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(100, 100, 200, 200), color=(0, 0, 0), fill=(1, 1, 0), width=1)
    A.restyle_paths(doc, 0, [0], width=3, dash=[4, 2], fill=A.NONE)
    o = E.objects(doc, 0).objects[0]
    assert o.paint == "S" and abs(o.state.line_width - 3) < 1e-6
    ref = pymupdf.open()
    rp = ref.new_page()
    rp.draw_rect(pymupdf.Rect(100, 100, 200, 200), color=(0, 0, 0), width=3, dashes="[4 2] 0")
    a = doc[0].get_pixmap(dpi=72).samples
    b = rp.get_pixmap(dpi=72).samples
    assert sum(1 for x, y in zip(a, b) if abs(x - y) > 1) < len(a) * 0.002


SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
<rect x="10" y="10" width="80" height="80" fill="red"/><circle cx="150" cy="50" r="40" fill="blue"/></svg>"""


def test_insert_svg_vector_no_image():
    doc = pymupdf.open()
    doc.new_page()
    idx = A.insert_svg(doc, 0, SVG, pymupdf.Point(300, 400))
    objs = E.objects(doc, 0).objects
    assert len(idx) == 1 and objs[idx[0]].kind == FORM
    r = objs[idx[0]].quad.rect
    assert abs((r.x0 + r.x1) / 2 - 300) < 1 and abs((r.y0 + r.y1) / 2 - 400) < 1
    data = doc.tobytes(garbage=3)
    assert count_images(data) == 0
    for _k in range(4):
        forms = [o.index for o in E.objects(doc, 0).objects if o.kind == FORM]
        if not forms:
            break
        A.decompose(doc, 0, forms[0], force=True)
    paths = [o for o in E.objects(doc, 0).objects if o.kind == PATH]
    assert len(paths) == 2


def test_insert_bad_svg():
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(E.ObjectError):
        A.insert_svg(doc, 0, b"<svg nonvalido", None)
