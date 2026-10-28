"""Analisi del content stream (foliant.modules.objects.content)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.objects import content, engine
from foliant.modules.objects.content import (
    FORM, IMAGE, PATH, SHADING, Name, ctm_at, fmt_matrix, fmt_name, fmt_num, matrix_close, parse_page,
    text_at_depth_zero, tokenize_ops,
)

from .conftest import add_image, new_pdf, png_bytes


def _set_content(pdf, page_index: int, data: bytes) -> None:
    page = pdf[page_index]
    xref = pdf.get_new_xref()
    pdf.update_object(xref, "<<>>")
    pdf.update_stream(xref, data, new=True)
    pdf.xref_set_key(page.xref, "Contents", f"{xref} 0 R")


# ------------------------------------------------------------------ tokenizer
def test_tokenize_operands_and_byte_ranges():
    data = b"q 1 0 0 1 10 20 cm /Im0 Do Q"
    ops = tokenize_ops(data)
    assert [o.name for o in ops] == ["q", "cm", "Do", "Q"]
    cm = ops[1]
    assert cm.operands == [1, 0, 0, 1, 10, 20]
    assert data[cm.start:cm.end] == b"1 0 0 1 10 20 cm"
    do = ops[2]
    assert do.operands == [Name("Im0")] and isinstance(do.operands[0], Name)
    assert data[do.start:do.end] == b"/Im0 Do"


def test_tokenize_strings_arrays_dicts_hex_and_comments():
    data = (b"% commento\nBT /F1 12 Tf (a\\)b(c)) Tj [(x) -20 <4142>] TJ ET "
            b"/OC <</MCID 3>> BDC EMC 0.5 -.25 +3. w")
    ops = tokenize_ops(data)
    names = [o.name for o in ops]
    assert names == ["BT", "Tf", "Tj", "TJ", "ET", "BDC", "EMC", "w"]
    assert ops[2].operands == [b"a)b(c)"]
    assert ops[3].operands == [[b"x", -20, b"AB"]]
    assert ops[5].operands[1] == {"MCID": 3}
    assert ops[7].operands == [0.5, -0.25, 3.0]


def test_tokenize_name_hex_escape_and_booleans():
    ops = tokenize_ops(b"/A#20B gs true false null d0")
    assert ops[0].operands == ["A B"]
    assert ops[1].name == "d0" and ops[1].operands == [True, False, None]


def test_tokenize_inline_image_binary_data_with_EI_inside():
    raw = bytes([0x45, 0x49, 0x20, 0x00, 0xFF, 0x51])  # contiene "EI " nei dati
    data = b"q 10 0 0 10 5 5 cm BI /W 2 /H 1 /CS /RGB /BPC 8 /L 6 ID " + raw + b" EI Q 1 w"
    ops = tokenize_ops(data)
    assert [o.name for o in ops] == ["q", "cm", "BI", "Q", "w"]
    bi = ops[2]
    assert bi.data["dict"]["W"] == 2 and bi.data["dict"]["CS"] == "RGB"
    start = bi.data["data_start"]
    assert data[start:start + 6] == raw
    assert data[bi.end - 2:bi.end] == b"EI"


def test_tokenize_inline_image_without_length():
    data = b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x80 EI 2 w"
    ops = tokenize_ops(data)
    assert [o.name for o in ops] == ["BI", "w"]


def test_fmt_helpers():
    assert fmt_num(2.0) == "2"
    assert fmt_num(0.5) == "0.5"
    assert fmt_num(-1e-12) == "0"
    assert fmt_num(1 / 3) == "0.333333"
    assert fmt_matrix(pymupdf.Matrix(1, 0, 0, 1, 10.5, -2)) == b"1 0 0 1 10.5 -2 cm"
    assert fmt_name("Im 0") == b"/Im#200"
    assert fmt_name("a/b") == b"/a#2Fb"
    assert matrix_close(pymupdf.Matrix(1, 0, 0, 1, 0, 0), pymupdf.Matrix(1 + 1e-9, 0, 0, 1, 0, 0))
    assert not matrix_close(pymupdf.Matrix(1, 0, 0, 1, 0, 0), pymupdf.Matrix(1, 0, 0, 1, 0.01, 0))


# ------------------------------------------------------------------ parser su contenuto PyMuPDF
def test_parse_insert_image_draw_rect_and_text():
    pdf = new_pdf()
    xref = add_image(pdf, 0, (50, 100, 250, 200))
    pdf[0].draw_rect(pymupdf.Rect(300, 300, 400, 350), color=(1, 0, 0), width=2)
    pdf[0].insert_text((72, 500), "Testo di prova")
    parsed = parse_page(pdf, pdf[0])
    kinds = [o.kind for o in parsed.objects]
    assert kinds == [IMAGE, PATH]  # il testo non è un oggetto
    img, path = parsed.objects
    assert img.rect == pymupdf.Rect(50, 100, 250, 200)
    assert img.xref == xref and img.width == 40 and img.height == 20
    assert img.name and not img.inline and img.wrapper is not None
    assert img.wrapper.outer_ctm == pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    assert pymupdf.Point(img.quad.ul) == pymupdf.Point(50, 100)  # in alto a sinistra dell'immagine
    assert img.opacity == 1.0 and not img.hidden
    # tracciato: il riquadro include metà dello spessore del tratto
    assert path.paint == "S"
    assert path.rect == pymupdf.Rect(299, 299, 401, 351)
    assert path.stroke_color == (1.0, 0.0, 0.0)
    assert path.state.line_width == 2
    assert "XObject" in parsed.names_used and img.name in parsed.names_used["XObject"]
    assert "Font" in parsed.names_used


def test_parse_nested_q_Q_and_cm():
    pdf = new_pdf()
    xref = add_image(pdf, 0, (0, 0, 10, 10))
    name = parse_page(pdf, pdf[0]).objects[0].name
    data = (b"q 1 0 0 1 100 0 cm\n"
            b"  q 2 0 0 2 0 0 cm\n"
            b"    q 50 0 0 25 0 0 cm /" + name.encode() + b" Do Q\n"
            b"  Q\n"
            b"  q 50 0 0 25 0 0 cm /" + name.encode() + b" Do Q\n"
            b"Q\n"
            b"q 50 0 0 25 0 0 cm /" + name.encode() + b" Do Q\n")
    _set_content(pdf, 0, data)
    parsed = parse_page(pdf, pdf[0])
    assert [o.kind for o in parsed.objects] == [IMAGE] * 3
    a, b, c = parsed.objects
    H = pdf[0].rect.height
    assert a.ctm == pymupdf.Matrix(100, 0, 0, 50, 100, 0)
    assert a.rect == pymupdf.Rect(100, H - 50, 200, H)
    assert b.rect == pymupdf.Rect(100, H - 25, 150, H)
    assert c.rect == pymupdf.Rect(0, H - 25, 50, H)
    assert all(o.xref == xref for o in parsed.objects)
    # contenitore q cm OGGETTO Q: outer = ctm esterna
    assert a.wrapper.outer_ctm == pymupdf.Matrix(2, 0, 0, 2, 100, 0)
    assert parsed.end_ctm == pymupdf.Matrix(1, 0, 0, 1, 0, 0)


def test_parse_inline_image_object():
    pdf = new_pdf()
    data = b"q 100 0 0 50 10 20 cm BI /W 2 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00\x00\x00\xff EI Q"
    _set_content(pdf, 0, data)
    parsed = parse_page(pdf, pdf[0])
    assert len(parsed.objects) == 1
    o = parsed.objects[0]
    assert o.kind == IMAGE and o.inline and o.xref == 0
    assert (o.width, o.height) == (2, 1)
    H = pdf[0].rect.height
    assert o.rect == pymupdf.Rect(10, H - 70, 110, H - 20)
    assert o.wrapper is not None


def test_parse_clip_restricts_visual_rect_and_wrapper_clip_unit():
    pdf = new_pdf()
    add_image(pdf, 0, (0, 0, 10, 10))
    name = parse_page(pdf, pdf[0]).objects[0].name.encode()
    H = pdf[0].rect.height
    data = b"q 200 0 0 200 0 0 cm 0 0 0.5 1 re W n /" + name + b" Do Q"
    _set_content(pdf, 0, data)
    o = parse_page(pdf, pdf[0]).objects[0]
    assert o.quad.rect == pymupdf.Rect(0, H - 200, 200, H)
    assert o.rect == pymupdf.Rect(0, H - 200, 100, H)
    assert o.wrapper.clip_unit == (0, 0, 0.5, 1)


def test_clip_only_path_is_not_object_and_text_paths_ignored():
    pdf = new_pdf()
    data = b"q 0 0 100 100 re W n 10 10 20 20 re f Q BT /F1 12 Tf 0 0 Td (x) Tj ET"
    _set_content(pdf, 0, data)
    parsed = parse_page(pdf, pdf[0])
    assert [o.kind for o in parsed.objects] == [PATH]
    assert parsed.objects[0].paint == "f"


def test_curves_lines_and_fill_paint_ops():
    pdf = new_pdf()
    page = pdf[0]
    page.draw_circle((200, 200), 50, color=(0, 0, 1), fill=(0, 1, 0))
    page.draw_line((10, 10), (100, 10), color=(0, 0, 0), width=1)
    parsed = parse_page(pdf, page)
    assert [o.kind for o in parsed.objects] == [PATH, PATH]
    circle, line = parsed.objects
    assert circle.paint in ("b", "B")
    assert abs(circle.rect.x0 - 149.5) < 1 and abs(circle.rect.x1 - 250.5) < 1
    assert circle.fill_color == (0.0, 1.0, 0.0)
    assert line.paint == "S"
    assert any(el[0] == "c" for seg in circle.segments for el in seg)


def test_form_xobject_and_shading_objects():
    src = new_pdf()
    src[0].draw_rect(pymupdf.Rect(0, 0, 50, 50), fill=(1, 0, 0))
    pdf = new_pdf()
    pdf[0].show_pdf_page(pymupdf.Rect(100, 100, 200, 200), src, 0, keep_proportion=False)
    o = [o for o in parse_page(pdf, pdf[0]).objects if o.kind == FORM]
    assert len(o) == 1
    assert abs(o[0].rect.x0 - 100) < 0.5 and abs(o[0].rect.y1 - 200) < 0.5
    # sfumatura: senza ritaglio occupa la pagina
    pdf2 = new_pdf()
    sx = pdf2.get_new_xref()
    pdf2.update_object(sx, "<</ShadingType 2/ColorSpace/DeviceRGB/Coords[0 0 100 0]"
                           "/Function<</FunctionType 2/Domain[0 1]/C0[1 0 0]/C1[0 0 1]/N 1>>>>")
    pdf2.xref_set_key(pdf2[0].xref, "Resources", f"<</Shading<</Sh0 {sx} 0 R>>>>")
    _set_content(pdf2, 0, b"q 0 0 100 100 re W n /Sh0 sh Q")
    sh = parse_page(pdf2, pdf2[0]).objects
    assert [o.kind for o in sh] == [SHADING]
    H = pdf2[0].rect.height
    assert sh[0].rect == pymupdf.Rect(0, H - 100, 100, H)


def test_opacity_from_extgstate_and_hidden():
    pdf = new_pdf()
    add_image(pdf, 0, (0, 0, 10, 10))
    name = parse_page(pdf, pdf[0]).objects[0].name
    gx = pdf.get_new_xref()
    pdf.update_object(gx, "<</Type/ExtGState/ca 0.4/CA 0.4>>")
    g0 = pdf.get_new_xref()
    pdf.update_object(g0, "<</ca 0/CA 0>>")
    engine.set_resource(pdf, pdf[0], "ExtGState", "G1", f"{gx} 0 R")
    engine.set_resource(pdf, pdf[0], "ExtGState", "G0", f"{g0} 0 R")
    data = (f"q 50 0 0 50 0 0 cm /G1 gs /{name} Do Q\n"
            f"q 50 0 0 50 100 0 cm /G0 gs /{name} Do Q").encode()
    _set_content(pdf, 0, data)
    a, b = parse_page(pdf, pdf[0]).objects
    assert a.opacity == pytest.approx(0.4) and not a.hidden
    assert a.wrapper.gs_name == "G1"
    assert b.opacity == 0 and b.hidden


def test_page_with_cropbox_offset_maps_to_page_space():
    pdf = new_pdf()
    add_image(pdf, 0, (100, 100, 200, 150))
    pdf[0].set_cropbox(pymupdf.Rect(50, 50, 400, 500))
    o = parse_page(pdf, pdf[0]).objects[0]
    # coordinate relative all'angolo in alto a sinistra del riquadro visibile
    assert o.rect == pymupdf.Rect(50, 50, 150, 100)


def test_ctm_at_and_text_depth():
    pdf = new_pdf()
    data = b"q 2 0 0 2 0 0 cm BT ET q 1 0 0 1 5 5 cm Q Q 1 w"
    _set_content(pdf, 0, data)
    parsed = parse_page(pdf, pdf[0])
    bt = next(o for o in parsed.ops if o.name == "BT")
    assert ctm_at(parsed, bt.start) == pymupdf.Matrix(2, 0, 0, 2, 0, 0)
    assert ctm_at(parsed, len(data)) == pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    et = next(o for o in parsed.ops if o.name == "ET")
    assert not text_at_depth_zero(parsed, et.start)
    assert text_at_depth_zero(parsed, et.end)


def test_inherited_resources_are_resolved():
    pdf = new_pdf(2)
    add_image(pdf, 0, (0, 0, 50, 50), png_bytes())
    o = parse_page(pdf, pdf[0]).objects[0]
    # sposta le risorse della pagina sul nodo /Pages (ereditate)
    res = pdf.xref_get_key(pdf[0].xref, "Resources")
    parent = int(pdf.xref_get_key(pdf[0].xref, "Parent")[1].split()[0])
    if res[0] == "xref":
        pdf.xref_set_key(parent, "Resources", res[1])
    else:
        pdf.xref_set_key(parent, "Resources", res[1])
    pdf.xref_set_key(pdf[0].xref, "Resources", "null")
    o2 = parse_page(pdf, pdf[0]).objects[0]
    assert o2.xref == o.xref and o2.width == 40
    assert content.resource_names(pdf, pdf[0], "XObject")
