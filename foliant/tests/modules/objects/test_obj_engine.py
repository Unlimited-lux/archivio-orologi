"""Operazioni sugli oggetti (foliant.modules.objects.engine) — criteri OBJ-04…OBJ-24, OBJ-43."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from foliant.modules.objects import content, engine, links
from foliant.modules.objects.content import IMAGE, PATH, matrix_close

from .conftest import (
    add_image, approx_rect, doc_from, image_objects, jpeg_bytes, new_pdf, page_image_xrefs, pixel, png_bytes,
    render, sos_data, split_png,
)

RED, GREEN, BLUE, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)


def _set_content(pdf, page_index: int, data: bytes) -> None:
    engine.write_content(pdf, pdf[page_index], data)


def _close(a, b, tol=0.5):
    return abs(a - b) <= tol


def _dominant(px) -> str:
    r, g, b = px[:3]
    if r > 200 and g < 80 and b < 80:
        return "red"
    if g > 200 and r < 80 and b < 80:
        return "green"
    if b > 200 and r < 80 and g < 80:
        return "blue"
    if r > 200 and g > 200 and b < 80:
        return "yellow"
    if r > 240 and g > 240 and b > 240:
        return "white"
    return f"other{tuple(px)}"


# ================================================================ inserimento (OBJ-04/05/06)
def test_insert_png_natural_size_from_dpi_centered_on_click(blank):
    data = png_bytes(300, 150, dpi=(150, 150))
    idx = engine.insert_images(blank, 0, [data], center=pymupdf.Point(200, 300))
    assert idx == [0]
    o = engine.objects(blank, 0).objects[0]
    assert o.kind == IMAGE and (o.width, o.height) == (300, 150)
    assert _close(o.rect.width, 144) and _close(o.rect.height, 72)
    c = content.rcenter(o.rect)
    assert _close(c.x, 200) and _close(c.y, 300)


def test_insert_without_dpi_uses_96_and_page_center(blank):
    engine.insert_images(blank, 0, [png_bytes(96, 48)])
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (595 / 2 - 36, 842 / 2 - 18, 595 / 2 + 36, 842 / 2 + 18))


def test_insert_multiple_images_cascade(blank):
    imgs = [png_bytes(96, 96, c) for c in (RED, GREEN, BLUE)]
    idx = engine.insert_images(blank, 0, imgs, center=pymupdf.Point(200, 200))
    assert idx == [0, 1, 2]
    objs = engine.objects(blank, 0).objects
    centers = [content.rcenter(o.rect) for o in objs]
    for k, c in enumerate(centers):
        assert _close(c.x, 200 + 20 * k) and _close(c.y, 200 + 20 * k)


def test_insert_large_image_is_scaled_to_fit_page(blank):
    engine.insert_images(blank, 0, [png_bytes(2000, 1000, dpi=(72, 72))])
    o = engine.objects(blank, 0).objects[0]
    assert o.rect.width <= 0.9 * 595 + 0.5
    assert _close(o.rect.width / o.rect.height, 2, 0.01)


def test_insert_into_rect_keeps_aspect(blank):
    engine.insert_images(blank, 0, [png_bytes(200, 100)], rect=pymupdf.Rect(100, 100, 300, 400))
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 200, 300, 300))


def test_insert_rgba_png_has_smask():
    pdf = new_pdf()
    engine.insert_images(pdf, 0, [png_bytes(400, 200, (0, 0, 255), mode="RGBA")])
    o = engine.objects(pdf, 0).objects[0]
    assert (o.width, o.height) == (400, 200)
    assert pdf.xref_get_key(o.xref, "SMask")[0] == "xref"
    # lo sfondo traspare: blu al 50 % su bianco
    c = content.rcenter(o.rect)
    r, g, b = pixel(pdf, 0, c.x, c.y)
    assert 120 <= r <= 135 and 120 <= g <= 135 and b > 250


def test_insert_jpeg_exif_stripped_and_compressed_data_identical():
    src = jpeg_bytes(64, 32, exif=True)
    assert b"Exif" in src
    pdf = new_pdf()
    engine.insert_images(pdf, 0, [src])
    saved = pymupdf.open(stream=pdf.tobytes(garbage=3, deflate=True), filetype="pdf")
    (xref,) = page_image_xrefs(saved.tobytes())
    assert "DCTDecode" in saved.xref_get_key(xref, "Filter")[1]
    raw = saved.xref_stream_raw(xref)
    assert raw.startswith(b"\xff\xd8")
    assert b"Exif" not in raw and b"FotocameraDiProva" not in raw
    assert sos_data(raw) == sos_data(src)


def test_strip_jpeg_metadata_keeps_jfif():
    src = jpeg_bytes(16, 16, exif=True)
    out, orient = engine.strip_jpeg_metadata(src)
    assert orient == 1
    assert b"JFIF" in out and b"Exif" not in out
    assert sos_data(out) == sos_data(src)
    assert engine.strip_jpeg_metadata(b"not a jpeg") == (b"not a jpeg", 1)


def test_insert_jpeg_exif_orientation_6_appears_upright():
    # immagine memorizzata 96×48: metà sinistra rossa, destra blu; orientamento 6 = ruota 90° orario
    im = Image.new("RGB", (96, 48), (0, 0, 255))
    im.paste(Image.new("RGB", (48, 48), (255, 0, 0)), (0, 0))
    ex = Image.Exif()
    ex[0x0112] = 6
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95, exif=ex.tobytes(), dpi=(72, 72))
    src = buf.getvalue()
    pdf = new_pdf()
    engine.insert_images(pdf, 0, [src], center=pymupdf.Point(200, 200))
    o = engine.objects(pdf, 0).objects[0]
    assert _close(o.rect.width, 48) and _close(o.rect.height, 96)
    # la colonna sinistra memorizzata diventa la parte alta
    assert _dominant(pixel(pdf, 0, 200, 200 - 30)) == "red"
    assert _dominant(pixel(pdf, 0, 200, 200 + 30)) == "blue"
    # nessuna ricodifica
    assert sos_data(pdf.xref_stream_raw(o.xref)) == sos_data(src)


def test_insert_corrupted_png_raises_and_document_unchanged():
    pdf = new_pdf()
    pdf[0].insert_text((72, 72), "Contenuto")
    doc = doc_from(pdf)
    before = render(doc.pdf)
    bad = png_bytes(50, 50)[:60] + b"\x00garbage" * 10
    with pytest.raises(engine.ObjectError):
        with doc.change("Inserisci immagine") as p:
            engine.insert_images(p, 0, [bad])
    assert not doc.is_modified and not doc.can_undo
    assert render(doc.pdf) == before
    assert image_objects(doc.pdf) == []


def test_insert_empty_data_raises(blank):
    with pytest.raises(engine.ObjectError):
        engine.insert_images(blank, 0, [b""])


def test_insert_gif_converted(blank):
    im = Image.new("P", (20, 10))
    buf = io.BytesIO()
    im.save(buf, "GIF")
    engine.insert_images(blank, 0, [buf.getvalue()])
    o = engine.objects(blank, 0).objects[0]
    assert (o.width, o.height) == (20, 10)


def test_three_inserted_images_removed_with_one_undo():
    doc = doc_from(new_pdf())
    before = render(doc.pdf)
    with doc.change("Inserisci immagini") as p:
        engine.insert_images(p, 0, [png_bytes(30, 30, c) for c in (RED, GREEN, BLUE)])
    assert len(image_objects(doc.pdf)) == 3
    doc.undo()
    assert image_objects(doc.pdf) == []
    assert render(doc.pdf) == before and not doc.is_modified


# ================================================================ proprietà (OBJ-03)
def test_image_info_ppi(blank):
    add_image(blank, 0, (100, 100, 300, 200), png_bytes(600, 300))
    o = engine.objects(blank, 0).objects[0]
    info = engine.image_info(blank, o)
    assert info["px"] == (600, 300)
    assert info["ppi"][0] == pytest.approx(216, abs=0.01) and info["ppi"][1] == pytest.approx(216, abs=0.01)
    assert info["width_pt"] == pytest.approx(200, abs=0.01) and info["height_pt"] == pytest.approx(100, abs=0.01)
    assert "colorspace" in info and info["alpha"] is False


def test_image_info_jpeg_filter(blank):
    add_image(blank, 0, (0, 0, 100, 50), jpeg_bytes())
    info = engine.image_info(blank, engine.objects(blank, 0).objects[0])
    assert info["filter"] in ("JPEG", "DCTDecode")


def test_set_width_keeps_x_y_and_ratio(blank):
    add_image(blank, 0, (100, 120, 200, 170))
    engine.resize(blank, 0, [0], pymupdf.Rect(100, 120, 300, 220))
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 120, 300, 220))


def test_mixed_opacity_set_both_to_50(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    add_image(blank, 0, (100, 0, 150, 50))
    engine.set_opacity(blank, 0, [0], 0.4)
    engine.set_opacity(blank, 0, [1], 0.7)
    ops = [o.opacity for o in engine.objects(blank, 0).objects]
    assert ops == [pytest.approx(0.4), pytest.approx(0.7)]
    engine.set_opacity(blank, 0, [0, 1], 0.5)
    assert [o.opacity for o in engine.objects(blank, 0).objects] == [pytest.approx(0.5)] * 2


# ================================================================ spostamento (OBJ-08)
def test_move_with_arrows_x50_to_63(blank):
    add_image(blank, 0, (50, 100, 150, 150))
    for _ in range(3):
        engine.translate(blank, 0, [0], 1, 0)
    engine.translate(blank, 0, [0], 10, 0)
    o = engine.objects(blank, 0).objects[0]
    assert o.rect.x0 == pytest.approx(63)
    assert o.rect.y0 == pytest.approx(100)
    # le traslazioni ripetute non annidano q/Q all'infinito
    assert o.wrapper is not None and matrix_close(o.wrapper.outer_ctm, engine.IDENTITY, 1e-5)


def test_move_rotated_page_follows_view(blank):
    add_image(blank, 0, (100, 100, 200, 150), png_bytes(100, 50, BLUE))
    page = blank[0]
    page.set_rotation(90)
    before = engine.objects(blank, 0).objects[0].rect * page.rotation_matrix
    dx, dy = engine.view_vector_to_page(page, 100, 0)
    engine.translate(blank, 0, [0], dx, dy)
    after = engine.objects(blank, 0).objects[0].rect * blank[0].rotation_matrix
    before.normalize()
    after.normalize()
    assert _close(after.x0 - before.x0, 100) and _close(after.y0, before.y0)
    # nella resa (ruotata) l'immagine è 100 pt più a destra
    c = content.rcenter(after)
    assert _dominant(pixel(blank, 0, c.x, c.y)) == "blue"
    assert _dominant(pixel(blank, 0, c.x - 100, c.y)) == "white"


def test_move_object_outside_others_pixels_unchanged(blank):
    page = blank[0]
    page.insert_text((300, 600), "Testo che non deve cambiare", fontsize=14)
    add_image(blank, 0, (50, 50, 150, 100), png_bytes(100, 50, RED))
    clip = pymupdf.Rect(250, 550, 595, 650)
    before = render(blank, clip=clip)
    img = [o.index for o in engine.objects(blank, 0).objects if o.kind == IMAGE][0]
    engine.translate(blank, 0, [img], 0, 100)
    assert render(blank, clip=clip) == before
    assert _dominant(pixel(blank, 0, 100, 75)) == "white"
    assert _dominant(pixel(blank, 0, 100, 175)) == "red"


def test_move_one_of_two_images_under_shared_clip_other_unchanged(blank):
    xref = add_image(blank, 0, (0, 0, 10, 10), split_png(100, 50))
    name = engine.objects(blank, 0).objects[0].name.encode()
    H = blank[0].rect.height
    # ritaglio condiviso: x da 50 a 350 (spazio pdf)
    data = (b"q 50 0 300 600 re W n\n"
            b"q 100 0 0 50 0 100 cm /" + name + b" Do Q\n"
            b"q 100 0 0 50 300 100 cm /" + name + b" Do Q\n"
            b"Q")
    _set_content(blank, 0, data)
    a, b = engine.objects(blank, 0).objects
    assert a.xref == b.xref == xref
    assert approx_rect(a.rect, (50, H - 150, 100, H - 100))
    clip_b = pymupdf.Rect(290, H - 160, 410, H - 90)
    before_b = render(blank, clip=clip_b)
    engine.translate(blank, 0, [1], 0, 0)  # nessun effetto visivo
    engine.translate(blank, 0, [0], 0, -200)
    assert render(blank, clip=clip_b) == before_b


def test_move_under_shared_clip_shows_same_portion_translated(blank):
    add_image(blank, 0, (0, 0, 10, 10), split_png(100, 50))
    name = engine.objects(blank, 0).objects[0].name.encode()
    data = (b"q 50 0 300 600 re W n\n"
            b"q 100 0 0 50 0 100 cm /" + name + b" Do Q\n"
            b"q 100 0 0 50 300 100 cm /" + name + b" Do Q\n"
            b"Q")
    _set_content(blank, 0, data)
    w0 = engine.objects(blank, 0).objects[0].rect.width
    engine.translate(blank, 0, [0], 100, 0)
    moved = [o for o in engine.objects(blank, 0).objects if o.rect.x0 > 120][0]
    assert moved.rect.width == pytest.approx(w0)  # resta visibile solo la metà destra (50 pt)


def test_clamp_translation_keeps_10pt_visible():
    page = pymupdf.Rect(0, 0, 595, 842)
    r = pymupdf.Rect(100, 100, 200, 200)
    assert engine.clamp_translation(r, page, -1000, 0) == (-190, 0)
    assert engine.clamp_translation(r, page, 0, 5000) == (0, 832 - 100)
    small = pymupdf.Rect(100, 100, 104, 104)
    dx, _ = engine.clamp_translation(small, page, -1000, 0)
    assert 100 + dx + 4 == pytest.approx(4)  # tutto l'oggetto, se più piccolo di 10 pt
    assert engine.clamp_translation(r, page, 5, 5) == (5, 5)


def test_move_40_paths_by_50(blank):
    page = blank[0]
    shape = page.new_shape()
    for k in range(40):
        y = 100 + 10 * k
        shape.draw_line((50, y), (150, y))
        shape.finish(color=(0, 0, 0), width=2)
    shape.commit()
    page.insert_text((400, 800), "fisso")
    parsed = engine.objects(blank, 0)
    assert [o.kind for o in parsed.objects if o.kind != "testo"] == [PATH] * 40
    rects = [pymupdf.Rect(o.rect) for o in parsed.objects if o.kind == PATH]
    sel = engine.objects_in_rect(parsed, pymupdf.Rect(40, 90, 160, 600))
    assert len(sel) == 40
    fixed = render(blank, clip=pymupdf.Rect(350, 700, 595, 842))
    region = render(blank, clip=pymupdf.Rect(40, 90, 160, 500))
    engine.translate(blank, 0, sel, 50, 0)
    after = [o for o in engine.objects(blank, 0).objects if o.kind == PATH]
    for r0, o in zip(rects, after):
        assert approx_rect(o.rect, (r0.x0 + 50, r0.y0, r0.x1 + 50, r0.y1), 0.01)
    assert render(blank, clip=pymupdf.Rect(90, 90, 210, 500)) == region
    assert render(blank, clip=pymupdf.Rect(350, 700, 595, 842)) == fixed


# ================================================================ ridimensionamento (OBJ-09)
def test_resize_proportional_200x100_to_300x150_opposite_corner_fixed(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    engine.resize(blank, 0, [0], pymupdf.Rect(100, 100, 400, 250))
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 100, 400, 250), 0.01)


def test_resize_free_300x120(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    engine.resize(blank, 0, [0], pymupdf.Rect(100, 100, 400, 220))
    w, h = engine.object_size(engine.objects(blank, 0).objects[0])
    assert w == pytest.approx(300) and h == pytest.approx(120)


def test_resize_keeps_image_data(blank):
    xref = add_image(blank, 0, (100, 100, 300, 200), png_bytes(60, 30))
    raw = blank.xref_stream_raw(xref)
    engine.resize(blank, 0, [0], pymupdf.Rect(100, 100, 400, 250))
    o = engine.objects(blank, 0).objects[0]
    assert o.xref == xref and (o.width, o.height) == (60, 30)
    assert blank.xref_stream_raw(xref) == raw


def test_resize_rotated_proportional_keeps_angle(blank):
    add_image(blank, 0, (200, 200, 400, 300))
    engine.rotate(blank, 0, [0], 30)
    o = engine.objects(blank, 0).objects[0]
    assert engine.object_angle(o) == pytest.approx(30, abs=1e-4)
    r = o.quad.rect
    engine.resize(blank, 0, [0], pymupdf.Rect(r.x0, r.y0, r.x0 + r.width * 1.5, r.y0 + r.height * 1.5))
    o2 = engine.objects(blank, 0).objects[0]
    assert engine.object_angle(o2) == pytest.approx(30, abs=1e-4)
    w, h = engine.object_size(o2)
    assert w == pytest.approx(300, abs=1e-3) and h == pytest.approx(150, abs=1e-3)


def test_resize_below_minimum_rejected(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    with pytest.raises(engine.ObjectError):
        engine.resize(blank, 0, [0], pymupdf.Rect(100, 100, 100.5, 200))


# ================================================================ rotazione (OBJ-10)
def test_rotate_90_clockwise_box_and_center(blank):
    add_image(blank, 0, (100, 100, 300, 200), split_png(200, 100))
    engine.rotate(blank, 0, [0], -90)
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (150, 50, 250, 250), 1e-6)
    # orario: la metà sinistra (rossa) va in alto
    assert _dominant(pixel(blank, 0, 200, 80)) == "red"
    assert _dominant(pixel(blank, 0, 200, 220)) == "blue"
    assert engine.object_angle(o) == pytest.approx(-90)


def test_rotate_four_times_identity(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    m0 = engine.objects(blank, 0).objects[0].ctm
    for _ in range(4):
        engine.rotate(blank, 0, [0], -90)
    m1 = engine.objects(blank, 0).objects[0].ctm
    assert all(abs(a - b) < 1e-3 for a, b in zip(m0, m1))


def test_rotate_four_times_exact_identity(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    m0 = engine.objects(blank, 0).objects[0].ctm
    for _ in range(4):
        engine.rotate(blank, 0, [0], -90)
    m1 = engine.objects(blank, 0).objects[0].ctm
    assert all(abs(a - b) < 1e-6 for a, b in zip(m0, m1))


def test_rotate_12_5_ccw(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    c0 = content.rcenter(engine.objects(blank, 0).objects[0].quad.rect)
    engine.rotate(blank, 0, [0], 12.5)
    o = engine.objects(blank, 0).objects[0]
    assert engine.object_angle(o) == pytest.approx(12.5, abs=1e-3)
    c1 = content.rcenter(o.quad.rect)
    assert _close(c0.x, c1.x, 1e-3) and _close(c0.y, c1.y, 1e-3)


def test_rotate_selection_about_common_center(blank):
    add_image(blank, 0, (100, 100, 200, 200))
    add_image(blank, 0, (300, 100, 400, 200))
    engine.rotate(blank, 0, [0, 1], 180)
    a, b = engine.objects(blank, 0).objects
    assert approx_rect(a.rect, (300, 100, 400, 200), 1e-6)
    assert approx_rect(b.rect, (100, 100, 200, 200), 1e-6)


# ================================================================ capovolgimento (OBJ-11)
def test_flip_horizontal_mirrors_and_keeps_box(blank):
    add_image(blank, 0, (100, 100, 300, 200), split_png(200, 100))
    engine.flip(blank, 0, [0], horizontal=True)
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 100, 300, 200), 1e-6)
    assert _dominant(pixel(blank, 0, 150, 150)) == "blue"
    assert _dominant(pixel(blank, 0, 250, 150)) == "red"


def test_flip_twice_identity(blank):
    add_image(blank, 0, (100, 100, 300, 200))
    m0 = engine.objects(blank, 0).objects[0].ctm
    for horizontal in (True, False):
        engine.flip(blank, 0, [0], horizontal)
        engine.flip(blank, 0, [0], horizontal)
        m1 = engine.objects(blank, 0).objects[0].ctm
        assert all(abs(a - b) < 1e-3 for a, b in zip(m0, m1))


def test_flip_two_images_A_B_becomes_B_A(blank):
    add_image(blank, 0, (100, 100, 200, 200), split_png(100, 100, RED, GREEN))   # A
    add_image(blank, 0, (200, 100, 300, 200), split_png(100, 100, BLUE, (255, 255, 0)))  # B
    engine.flip(blank, 0, [0, 1], horizontal=True)
    seq = [_dominant(pixel(blank, 0, x, 150)) for x in (125, 175, 225, 275)]
    assert seq == ["yellow", "blue", "green", "red"]


def test_flip_vertical_on_rotated_page_follows_view(blank):
    add_image(blank, 0, (100, 100, 300, 200), split_png(200, 100))
    blank[0].set_rotation(90)
    m0 = engine.objects(blank, 0).objects[0].ctm
    engine.flip(blank, 0, [0], horizontal=True)
    m1 = engine.objects(blank, 0).objects[0].ctm
    # sulla pagina ruotata il capovolgimento "orizzontale" nella vista è verticale sulla pagina
    assert m1.a == pytest.approx(m0.a) and m1.d == pytest.approx(-m0.d)


# ================================================================ opacità (OBJ-16)
def test_opacity_half_renders_gray_and_extgstate(blank):
    add_image(blank, 0, (100, 100, 200, 200), png_bytes(10, 10, BLACK))
    engine.set_opacity(blank, 0, [0], 0.5)
    r, g, b = pixel(blank, 0, 150, 150)
    assert 126 <= r <= 129 and 126 <= g <= 129 and 126 <= b <= 129
    o = engine.objects(blank, 0).objects[0]
    assert o.opacity == pytest.approx(0.5)
    gs = o.wrapper.gs_name
    val = content.resource_names(blank, blank[0], "ExtGState")[gs]
    text = blank.xref_object(int(val.split()[0]), compressed=True)
    import re
    ca = re.search(r"/ca\s*([0-9.]+)", text)
    CA = re.search(r"/CA\s*([0-9.]+)", text)
    assert ca and float(ca.group(1)) == 0.5 and CA and float(CA.group(1)) == 0.5
    # salvato e riaperto
    re_ = pymupdf.open(stream=blank.tobytes(garbage=3), filetype="pdf")
    assert engine.objects(re_, 0).objects[0].opacity == pytest.approx(0.5)


def test_opacity_reuses_extgstate(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    add_image(blank, 0, (100, 0, 150, 50))
    engine.set_opacity(blank, 0, [0], 0.3)
    engine.set_opacity(blank, 0, [1], 0.3)
    a, b = engine.objects(blank, 0).objects
    assert a.wrapper.gs_name == b.wrapper.gs_name
    assert len(content.resource_names(blank, blank[0], "ExtGState")) == 1


def test_opacity_out_of_range(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    with pytest.raises(engine.ObjectError):
        engine.set_opacity(blank, 0, [0], 1.5)


def test_opacity_transparent_png_keeps_transparent_zones(blank):
    im = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (10, 10), (0, 0, 0, 255)), (0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    add_image(blank, 0, (100, 100, 300, 200), buf.getvalue())
    engine.set_opacity(blank, 0, [0], 0.5)
    assert 126 <= pixel(blank, 0, 150, 150)[0] <= 129
    assert pixel(blank, 0, 250, 150) == (255, 255, 255)


# ================================================================ eliminazione (OBJ-15)
def test_delete_one_of_three_images_full_save(blank):
    imgs = [png_bytes(30, 30, c) for c in (RED, GREEN, BLUE)]
    xrefs = [add_image(blank, 0, (100 * k, 100, 100 * k + 50, 150), d) for k, d in enumerate(imgs)]
    green_raw = blank.xref_stream_raw(xrefs[1])
    engine.delete_objects(blank, 0, [1])
    assert [o.xref for o in engine.objects(blank, 0).objects] == [xrefs[0], xrefs[2]]
    data = blank.tobytes(garbage=3, deflate=True)
    saved = pymupdf.open(stream=data, filetype="pdf")
    images = [x for x in range(1, saved.xref_length()) if saved.xref_get_key(x, "Subtype")[1] == "/Image"]
    assert len(images) == 2
    assert all(saved.xref_stream_raw(x) != green_raw for x in images)
    # la risorsa non usata è stata tolta
    live = {k: v for k, v in content.resource_names(blank, blank[0], "XObject").items() if v != "null"}
    assert len(live) == 2


def test_shared_logo_deleted_on_one_page_still_on_others():
    pdf = new_pdf(5)
    xref = add_image(pdf, 0, (100, 100, 200, 200), png_bytes(20, 20, RED))
    for i in range(1, 5):
        add_image(pdf, i, (100, 100, 200, 200), xref=xref)
    engine.delete_objects(pdf, 1, [0])
    assert engine.objects(pdf, 1).objects == []
    assert _dominant(pixel(pdf, 1, 150, 150)) == "white"
    for i in (0, 2, 3, 4):
        assert _dominant(pixel(pdf, i, 150, 150)) == "red"
    saved = pymupdf.open(stream=pdf.tobytes(garbage=3), filetype="pdf")
    for i in (0, 2, 3, 4):
        assert _dominant(pixel(saved, i, 150, 150)) == "red"


def test_delete_path_removes_paint_operator(blank):
    page = blank[0]
    page.draw_rect(pymupdf.Rect(50, 50, 100, 100), color=(1, 0, 0), width=2)
    page.draw_rect(pymupdf.Rect(200, 50, 250, 100), fill=(0, 0, 1), color=None)
    keep_clip = pymupdf.Rect(190, 40, 260, 110)
    before = render(blank, clip=keep_clip)
    engine.delete_objects(blank, 0, [0])
    parsed = engine.objects(blank, 0)
    assert [o.paint for o in parsed.objects] == ["f"]
    assert not any(op.name == "S" for op in parsed.ops)
    assert render(blank, clip=keep_clip) == before


def test_delete_all_objects_leaves_empty_page(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    blank[0].draw_rect(pymupdf.Rect(60, 60, 80, 80), color=(0, 0, 0))
    assert engine.delete_objects(blank, 0, [0, 1]) == 2
    assert engine.objects(blank, 0).objects == []
    assert blank.page_count == 1


def test_invalid_index_raises(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    with pytest.raises(engine.ObjectError):
        engine.translate(blank, 0, [5], 1, 1)
    with pytest.raises(engine.ObjectError):
        engine.delete_objects(blank, 0, [])


# ================================================================ ordine (OBJ-20)
def _abc(pdf, overlap_b=True):
    xa = add_image(pdf, 0, (100, 100, 200, 200), png_bytes(10, 10, RED))
    rb = (150, 150, 250, 250) if overlap_b else (400, 400, 500, 500)
    xb = add_image(pdf, 0, rb, png_bytes(10, 10, GREEN))
    xc = add_image(pdf, 0, (120, 120, 220, 220), png_bytes(10, 10, BLUE))
    return xa, xb, xc


def test_bring_to_front(blank):
    xa, xb, xc = _abc(blank)
    assert _dominant(pixel(blank, 0, 180, 180)) == "blue"
    new = engine.reorder(blank, 0, [0], engine.FRONT)
    assert new == [2]
    assert [o.xref for o in engine.objects(blank, 0).objects] == [xb, xc, xa]
    assert _dominant(pixel(blank, 0, 180, 180)) == "red"
    assert engine.reorder(blank, 0, [2], engine.FRONT) is None


def test_bring_forward_past_next_overlapping(blank):
    xa, xb, xc = _abc(blank, overlap_b=False)
    new = engine.reorder(blank, 0, [0], engine.FORWARD)
    order = [o.xref for o in engine.objects(blank, 0).objects]
    assert order.index(xa) > order.index(xc)
    assert order == [xb, xc, xa] and new == [2]


def test_send_to_back_and_backward(blank):
    xa, xb, xc = _abc(blank)
    assert engine.reorder(blank, 0, [2], engine.BACK) == [0]
    assert [o.xref for o in engine.objects(blank, 0).objects] == [xc, xa, xb]
    assert engine.reorder(blank, 0, [0], engine.BACK) is None
    engine.reorder(blank, 0, [2], engine.BACKWARD)
    assert [o.xref for o in engine.objects(blank, 0).objects] == [xc, xb, xa]


def test_reorder_keeps_geometry_inside_transformed_context(blank):
    xref = add_image(blank, 0, (0, 0, 10, 10), png_bytes(10, 10, RED))
    name = engine.objects(blank, 0).objects[0].name.encode()
    data = (b"q 1 0 0 1 50 0 cm q 100 0 0 100 0 0 cm /" + name + b" Do Q Q\n"
            b"q 100 0 0 100 100 0 cm /" + name + b" Do Q")
    _set_content(blank, 0, data)
    rects = [pymupdf.Rect(o.rect) for o in engine.objects(blank, 0).objects]
    engine.reorder(blank, 0, [0], engine.FRONT)
    after = [o.rect for o in engine.objects(blank, 0).objects]
    assert approx_rect(after[1], rects[0], 1e-6) and approx_rect(after[0], rects[1], 1e-6)
    assert xref


# ================================================================ allinea / distribuisci (OBJ-21/22)
def test_align_left_selection(blank):
    for k, x in enumerate((50, 80, 120)):
        add_image(blank, 0, (x, 100 + 60 * k, x + 40, 140 + 60 * k))
    objs = engine.objects(blank, 0).objects
    rects = engine.view_rects(blank[0], objs)
    deltas = engine.align_deltas(rects, engine.LEFT, engine.REF_SELECTION, blank[0].rect)
    engine.translate_each(blank, 0, [0, 1, 2], deltas)
    after = engine.objects(blank, 0).objects
    assert [o.rect.x0 for o in after] == [pytest.approx(50)] * 3
    assert [o.rect.y0 for o in after] == [pytest.approx(100 + 60 * k) for k in range(3)]


def test_align_hcenter_page_single_object(blank):
    add_image(blank, 0, (10, 10, 70, 40))
    rects = engine.view_rects(blank[0], engine.objects(blank, 0).objects)
    deltas = engine.align_deltas(rects, engine.HCENTER, engine.REF_PAGE, blank[0].rect)
    engine.translate_each(blank, 0, [0], deltas)
    c = content.rcenter(engine.objects(blank, 0).objects[0].rect)
    assert abs(c.x - 595 / 2) <= 0.1


def test_align_bottom_key_object_stays(blank):
    add_image(blank, 0, (10, 10, 50, 60))      # chiave
    add_image(blank, 0, (100, 200, 150, 220))
    add_image(blank, 0, (200, 0, 250, 30))
    rects = engine.view_rects(blank[0], engine.objects(blank, 0).objects)
    deltas = engine.align_deltas(rects, engine.BOTTOM, engine.REF_KEY, blank[0].rect, key=0)
    assert deltas[0] == (0.0, 0.0)
    engine.translate_each(blank, 0, [0, 1, 2], deltas)
    after = engine.objects(blank, 0).objects
    assert approx_rect(after[0].rect, (10, 10, 50, 60), 1e-6)
    assert [o.rect.y1 for o in after] == [pytest.approx(60)] * 3


def test_distribute_equal_spacing(blank):
    for x in (0, 30, 100):
        add_image(blank, 0, (x, 100, x + 20, 120))
    rects = engine.view_rects(blank[0], engine.objects(blank, 0).objects)
    deltas = engine.distribute_deltas(rects, horizontal=True)
    engine.translate_each(blank, 0, [0, 1, 2], deltas)
    xs = [o.rect.x0 for o in engine.objects(blank, 0).objects]
    assert xs == [pytest.approx(0), pytest.approx(50), pytest.approx(100)]


def test_distribute_centers_four_objects():
    rects = [pymupdf.Rect(0, 0, 10, 10), pymupdf.Rect(17, 0, 57, 10), pymupdf.Rect(70, 0, 75, 10),
             pymupdf.Rect(200, 0, 260, 10)]
    deltas = engine.distribute_deltas(rects, horizontal=True, method=engine.CENTERS)
    centers = sorted((r.x0 + r.x1) / 2 + d[0] for r, d in zip(rects, deltas))
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    assert max(gaps) - min(gaps) <= 0.1
    assert deltas[0] == (0.0, 0.0) and deltas[3] == (0.0, 0.0)


def test_distribute_vertical_and_fewer_than_three():
    rects = [pymupdf.Rect(0, 0, 10, 10), pymupdf.Rect(0, 15, 10, 25), pymupdf.Rect(0, 90, 10, 100)]
    deltas = engine.distribute_deltas(rects, horizontal=False)
    assert deltas[1] == (0.0, pytest.approx(30))
    assert engine.distribute_deltas(rects[:2], True) == [(0.0, 0.0)] * 2


# ================================================================ ritaglio (OBJ-13)
def _crop_page():
    pdf = new_pdf()
    add_image(pdf, 0, (100, 100, 300, 300), split_png(400, 400))
    return pdf


def test_crop_left_half_visible_rect():
    pdf = _crop_page()
    engine.set_crop(pdf, 0, 0, (0, 0, 0.5, 1))
    o = engine.objects(pdf, 0).objects[0]
    assert approx_rect(o.rect, (100, 100, 200, 300), 1e-6)
    assert o.wrapper.clip_unit == (0, 0, 0.5, 1)
    assert _dominant(pixel(pdf, 0, 150, 200)) == "red"
    assert _dominant(pixel(pdf, 0, 250, 200)) == "white"


def test_crop_reset_identical_render():
    pdf = _crop_page()
    before = render(pdf)
    engine.set_crop(pdf, 0, 0, (0, 0, 0.5, 1))
    assert render(pdf) != before
    engine.set_crop(pdf, 0, 0, None)
    assert render(pdf) == before
    assert engine.objects(pdf, 0).objects[0].wrapper.clip_unit is None


def test_crop_full_area_is_no_crop_and_invalid_rejected():
    pdf = _crop_page()
    engine.set_crop(pdf, 0, 0, (-1, -1, 3, 3))
    assert engine.objects(pdf, 0).objects[0].wrapper.clip_unit is None
    with pytest.raises(engine.ObjectError):
        engine.set_crop(pdf, 0, 0, (0.2, 0.2, 0, 0.5))
    with pytest.raises(engine.ObjectError):
        engine.set_crop(pdf, 0, 0, (0, 0, 0.001, 1))  # meno di 1 pixel


def test_crop_moves_with_object():
    pdf = _crop_page()
    engine.set_crop(pdf, 0, 0, (0, 0, 0.5, 1))
    engine.translate(pdf, 0, [0], 50, 0)
    engine.rotate(pdf, 0, [0], -90)
    o = engine.objects(pdf, 0).objects[0]
    assert o.wrapper.clip_unit == (0, 0, 0.5, 1)
    assert o.rect.width == pytest.approx(200) and o.rect.height == pytest.approx(100)


def test_crop_unit_from_rect():
    pdf = _crop_page()
    parsed = engine.objects(pdf, 0)
    o = parsed.objects[0]
    cu = engine.crop_unit_from_rect(o, pymupdf.Rect(100, 100, 200, 300), parsed.pdf_to_page)
    assert all(abs(a - b) < 1e-9 for a, b in zip(cu, (0, 0, 0.5, 1)))


def test_apply_crop_creates_200x400_image_and_drops_original():
    pdf = _crop_page()
    old_xref = engine.objects(pdf, 0).objects[0].xref
    engine.set_crop(pdf, 0, 0, (0, 0, 0.5, 1))
    shown = render(pdf)
    engine.apply_crop(pdf, 0, 0)
    o = engine.objects(pdf, 0).objects[0]
    assert (o.width, o.height) == (200, 400)
    assert o.xref != old_xref
    assert o.wrapper.clip_unit is None
    assert approx_rect(o.rect, (100, 100, 200, 300), 1e-6)
    assert _dominant(pixel(pdf, 0, 150, 200)) == "red"
    assert _dominant(pixel(pdf, 0, 250, 200)) == "white"
    assert shown
    saved = pymupdf.open(stream=pdf.tobytes(garbage=3, deflate=True), filetype="pdf")
    sizes = [(saved.xref_get_key(x, "Width")[1], saved.xref_get_key(x, "Height")[1])
             for x in range(1, saved.xref_length()) if saved.xref_get_key(x, "Subtype")[1] == "/Image"]
    assert ("200", "400") in sizes and ("400", "400") not in sizes


def test_apply_crop_requires_crop():
    pdf = _crop_page()
    with pytest.raises(engine.ObjectError):
        engine.apply_crop(pdf, 0, 0)


def test_crop_path_rejected(blank):
    blank[0].draw_rect(pymupdf.Rect(10, 10, 50, 50), color=(0, 0, 0))
    with pytest.raises(engine.ObjectError):
        engine.set_crop(blank, 0, 0, (0, 0, 0.5, 0.5))


# ================================================================ sostituzione (OBJ-14)
def test_replace_fit_square_into_rotated_box(blank):
    add_image(blank, 0, (200, 200, 400, 300), png_bytes(200, 100, RED))
    engine.rotate(blank, 0, [0], 30)
    o = engine.objects(blank, 0).objects[0]
    c0 = content.rcenter(o.quad)
    engine.replace_image(blank, 0, 0, png_bytes(50, 50, BLUE), mode=engine.FIT)
    n = engine.objects(blank, 0).objects[0]
    w, h = engine.object_size(n)
    assert w == pytest.approx(100, abs=1e-3) and h == pytest.approx(100, abs=1e-3)
    c1 = content.rcenter(n.quad)
    assert _close(c0.x, c1.x, 1e-3) and _close(c0.y, c1.y, 1e-3)
    assert engine.object_angle(n) == pytest.approx(30, abs=1e-4)
    assert (n.width, n.height) == (50, 50)


def test_replace_fill_and_stretch(blank):
    add_image(blank, 0, (100, 100, 300, 200), png_bytes(200, 100, RED))
    engine.replace_image(blank, 0, 0, png_bytes(50, 50, BLUE), mode=engine.FILL)
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 100, 300, 200), 1e-3)  # ritagliata al riquadro
    w, h = engine.object_size(o)
    assert w == pytest.approx(200) and h == pytest.approx(200)
    assert _dominant(pixel(blank, 0, 200, 150)) == "blue"
    assert _dominant(pixel(blank, 0, 200, 90)) == "white"


def test_replace_stretch(blank):
    add_image(blank, 0, (100, 100, 300, 200), png_bytes(200, 100, RED))
    engine.replace_image(blank, 0, 0, png_bytes(50, 50, GREEN), mode=engine.STRETCH)
    o = engine.objects(blank, 0).objects[0]
    assert engine.object_size(o) == (pytest.approx(200), pytest.approx(100))
    assert approx_rect(o.rect, (100, 100, 300, 200), 1e-3)


def test_replace_cropped_image_keeps_visible_box(blank):
    add_image(blank, 0, (100, 100, 300, 300), png_bytes(40, 40, RED))
    engine.set_crop(blank, 0, 0, (0, 0, 0.5, 1))
    engine.replace_image(blank, 0, 0, png_bytes(20, 40, BLUE), mode=engine.STRETCH)
    o = engine.objects(blank, 0).objects[0]
    assert approx_rect(o.rect, (100, 100, 200, 300), 0.5)


def test_replace_keeps_opacity(blank):
    add_image(blank, 0, (100, 100, 300, 200), png_bytes(20, 10, RED))
    engine.set_opacity(blank, 0, [0], 0.6)
    engine.replace_image(blank, 0, 0, png_bytes(20, 10, BLUE))
    o = engine.objects(blank, 0).objects[0]
    assert o.opacity == pytest.approx(0.6)


def test_replace_all_occurrences_across_pages():
    pdf = new_pdf(10)
    xref = add_image(pdf, 0, (100, 100, 200, 200), png_bytes(20, 20, RED))
    for i in range(1, 10):
        add_image(pdf, i, (100, 100, 200, 200), xref=xref)
    assert engine.image_occurrences(pdf, xref) == list(range(10))
    engine.replace_image(pdf, 3, 0, png_bytes(20, 20, BLUE), all_occurrences=True)
    for i in range(10):
        assert _dominant(pixel(pdf, i, 150, 150)) == "blue"
    saved = pymupdf.open(stream=pdf.tobytes(garbage=3, deflate=True), filetype="pdf")
    for i in range(10):
        assert _dominant(pixel(saved, i, 150, 150)) == "blue"


def test_replace_all_occurrences_single_xobject():
    pdf = new_pdf(10)
    xref = add_image(pdf, 0, (100, 100, 200, 200), png_bytes(20, 20, RED))
    for i in range(1, 10):
        add_image(pdf, i, (100, 100, 200, 200), xref=xref)
    engine.replace_image(pdf, 3, 0, png_bytes(20, 20, BLUE), all_occurrences=True)
    data = pdf.tobytes(garbage=3, deflate=True)
    assert len(page_image_xrefs(data)) == 1


def test_replace_single_occurrence_others_unchanged():
    pdf = new_pdf(10)
    xref = add_image(pdf, 0, (100, 100, 200, 200), png_bytes(20, 20, RED))
    for i in range(1, 10):
        add_image(pdf, i, (100, 100, 200, 200), xref=xref)
    before = [render(pdf, i) for i in range(10)]
    engine.replace_image(pdf, 3, 0, png_bytes(20, 20, BLUE))
    for i in range(10):
        if i == 3:
            assert _dominant(pixel(pdf, i, 150, 150)) == "blue"
        else:
            assert render(pdf, i) == before[i]


def test_replace_rejects_non_image_and_bad_data(blank):
    blank[0].draw_rect(pymupdf.Rect(10, 10, 50, 50), color=(0, 0, 0))
    add_image(blank, 0, (100, 100, 200, 200))
    with pytest.raises(engine.ObjectError):
        engine.replace_image(blank, 0, 0, png_bytes())
    with pytest.raises(engine.ObjectError):
        engine.replace_image(blank, 0, 1, b"\x89PNG\r\n\x1a\nrotto")


# ================================================================ estrazione (OBJ-27)
def test_extract_dct_original_identical(blank):
    src = jpeg_bytes(64, 32)
    add_image(blank, 0, (0, 0, 64, 32), src)
    o = engine.objects(blank, 0).objects[0]
    ex = engine.extract_image(blank, o, "original")
    assert ex.ext == "jpg"
    assert ex.data == blank.xref_stream_raw(o.xref)


def test_extract_png_with_smask_alpha_preserved(blank):
    im = Image.new("RGBA", (16, 8))
    im.putdata([(255, 0, 0, (x * 16) % 256) for x in range(16 * 8)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    add_image(blank, 0, (0, 0, 160, 80), buf.getvalue())
    o = engine.objects(blank, 0).objects[0]
    mask_xref = int(blank.xref_get_key(o.xref, "SMask")[1].split()[0])
    mask = pymupdf.Pixmap(blank, mask_xref)
    for fmt in ("original", "png"):
        ex = engine.extract_image(blank, o, fmt)
        assert ex.ext == "png"
        out = Image.open(io.BytesIO(ex.data))
        assert out.mode == "RGBA" and out.size == (16, 8)
        assert out.getchannel("A").tobytes() == mask.samples


def test_extract_as_jpeg_and_inline_rejected(blank):
    add_image(blank, 0, (0, 0, 64, 32), png_bytes(16, 8))
    o = engine.objects(blank, 0).objects[0]
    ex = engine.extract_image(blank, o, "jpeg")
    assert ex.ext == "jpg" and ex.data.startswith(b"\xff\xd8")
    _set_content(blank, 0, b"q 10 0 0 10 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q")
    inline = engine.objects(blank, 0).objects[0]
    assert inline.inline
    with pytest.raises(engine.ObjectError):
        engine.extract_image(blank, inline)


def test_render_as_shown_png(blank):
    add_image(blank, 0, (100, 100, 200, 150), png_bytes(200, 100))
    o = engine.objects(blank, 0).objects[0]
    data = engine.render_as_shown(blank, 0, o)
    im = Image.open(io.BytesIO(data))
    assert im.size == (200, 100)


# ================================================================ selezione e hit test (OBJ-02/24)
def test_hit_test_topmost_first(blank):
    add_image(blank, 0, (100, 100, 200, 200))
    add_image(blank, 0, (150, 150, 250, 250))
    parsed = engine.objects(blank, 0)
    assert engine.hit_test(parsed, pymupdf.Point(175, 175)) == [1, 0]
    assert engine.hit_test(parsed, pymupdf.Point(120, 120)) == [0]
    assert engine.hit_test(parsed, pymupdf.Point(400, 400)) == []


def test_hit_test_rotated_image_and_path_stroke(blank):
    add_image(blank, 0, (200, 200, 400, 220))
    engine.rotate(blank, 0, [0], 45)
    blank[0].draw_line((50, 50), (150, 50), color=(0, 0, 0), width=1)
    parsed = engine.objects(blank, 0)
    # angolo del riquadro ma fuori dal quadrilatero ruotato
    r = parsed.objects[0].rect
    assert 0 not in engine.hit_test(parsed, pymupdf.Point(r.x0 + 2, r.y0 + 2), tolerance=0)
    assert 0 in engine.hit_test(parsed, content.rcenter(r))
    assert engine.hit_test(parsed, pymupdf.Point(100, 52)) == [1]
    assert engine.hit_test(parsed, pymupdf.Point(100, 60)) == []


def test_hidden_objects_skipped(blank):
    add_image(blank, 0, (100, 100, 200, 200))
    engine.set_opacity(blank, 0, [0], 0)
    parsed = engine.objects(blank, 0)
    assert parsed.objects[0].hidden
    assert engine.hit_test(parsed, pymupdf.Point(150, 150)) == []
    assert engine.hit_test(parsed, pymupdf.Point(150, 150), include_hidden=True) == [0]


def test_objects_in_rect_contained_and_intersecting(blank):
    add_image(blank, 0, (10, 10, 50, 50))
    add_image(blank, 0, (60, 10, 100, 50))
    add_image(blank, 0, (90, 60, 200, 100))
    parsed = engine.objects(blank, 0)
    sel = pymupdf.Rect(0, 0, 120, 120)
    assert engine.objects_in_rect(parsed, sel) == [0, 1]
    assert engine.objects_in_rect(parsed, sel, intersect=True) == [0, 1, 2]


def test_filter_images_only(blank):
    for k in range(4):
        add_image(blank, 0, (10 + 60 * k, 10, 50 + 60 * k, 50))
    for k in range(12):
        blank[0].draw_line((10, 100 + 5 * k), (100, 100 + 5 * k))
    parsed = engine.objects(blank, 0)
    assert len([o for o in parsed.objects if o.kind == IMAGE]) == 4
    assert len([o for o in parsed.objects if o.kind == PATH]) == 12


def test_select_similar_paths(blank):
    page = blank[0]
    for k in range(4):
        page.draw_rect(pymupdf.Rect(10 + 60 * k, 10, 50 + 60 * k, 50), color=(1, 0, 0), width=2)
    page.draw_rect(pymupdf.Rect(10, 100, 50, 150), color=(0, 0, 1), width=2)
    page.draw_rect(pymupdf.Rect(100, 100, 150, 150), color=(1, 0, 0), width=1)
    page.draw_rect(pymupdf.Rect(200, 100, 250, 150), color=(1, 0, 0), fill=(1, 0, 0), width=2)
    parsed = engine.objects(blank, 0)
    assert engine.similar_paths(parsed, 0) == [0, 1, 2, 3]
    add_image(blank, 0, (300, 300, 350, 350))
    parsed = engine.objects(blank, 0)
    assert engine.similar_paths(parsed, len(parsed.objects) - 1) == [len(parsed.objects) - 1]


def test_apply_edits_rejects_overlaps():
    data, starts = engine.apply_edits(b"abcdef", [(4, 5, b"XY"), (0, 1, b"Z")])
    assert data == b"ZbcdXYf" and starts == [4, 0]
    with pytest.raises(engine.ObjectError):
        engine.apply_edits(b"abcdef", [(0, 3, b""), (2, 4, b"")])


def test_kind_label():
    assert engine.kind_label(IMAGE) == "Immagine"
    assert engine.kind_label(PATH) == "Tracciato"


# ================================================================ annulla / ripeti (OBJ-01, OBJ-43)
def test_parse_does_not_modify_document():
    pdf = new_pdf()
    add_image(pdf, 0, (100, 100, 200, 200))
    doc = doc_from(pdf)
    engine.objects(doc.pdf, 0)
    engine.hit_test(engine.objects(doc.pdf, 0), pymupdf.Point(150, 150))
    assert not doc.is_modified and not doc.can_undo


def test_five_operations_undo_redo_pixel_identical():
    pdf = new_pdf(2)
    pdf[0].insert_text((72, 72), "Pagina con oggetti", fontsize=16)
    add_image(pdf, 0, (300, 300, 400, 400), split_png(100, 100))
    doc = doc_from(pdf)
    original = [render(doc.pdf, i) for i in range(2)]
    with doc.change("Inserisci immagine") as p:
        engine.insert_images(p, 0, [png_bytes(100, 50, BLUE)], center=pymupdf.Point(150, 200))
    with doc.change("Sposta") as p:
        engine.translate(p, 0, [2], 20, 30)      # 0 = testo, 1 = immagine, 2 = immagine inserita
    with doc.change("Ruota") as p:
        engine.rotate(p, 0, [1], -90)
    with doc.change("Ritaglia") as p:
        engine.set_crop(p, 0, 1, (0, 0, 0.5, 1))
    with doc.change("Collegamento") as p:
        links.create_link(p, 0, pymupdf.Rect(50, 500, 150, 520), links.LinkTarget(uri="https://esempio.it"))
    final = [render(doc.pdf, i) for i in range(2)]
    final_objects = len(engine.objects(doc.pdf, 0).objects)
    final_annots = len(links.link_xrefs(doc.pdf, 0))
    assert final_annots == 1 and final_objects == 3
    for _ in range(5):
        assert doc.undo()
    assert not doc.is_modified
    assert [render(doc.pdf, i) for i in range(2)] == original
    assert links.link_xrefs(doc.pdf, 0) == []
    for _ in range(5):
        assert doc.redo()
    assert [render(doc.pdf, i) for i in range(2)] == final
    assert len(engine.objects(doc.pdf, 0).objects) == final_objects
    assert len(links.link_xrefs(doc.pdf, 0)) == final_annots


def test_undo_after_save_marks_modified(tmp_path):
    pdf = new_pdf()
    add_image(pdf, 0, (100, 100, 200, 200))
    doc = doc_from(pdf)
    for dx in (1, 2, 3):
        with doc.change("Sposta") as p:
            engine.translate(p, 0, [0], dx, 0)
    doc.save(tmp_path / "salvato.pdf")
    assert not doc.is_modified
    doc.undo()
    assert doc.is_modified
    assert engine.objects(doc.pdf, 0).objects[0].rect.x0 == pytest.approx(103)


def test_resource_names_no_spurious_keys(blank):
    add_image(blank, 0, (0, 0, 50, 50))
    names = content.resource_names(blank, blank[0], "XObject")
    assert list(names) == [engine.objects(blank, 0).objects[0].name]


def test_inner_state_ops_still_apply_to_following_objects():
    import pymupdf as _p
    from foliant.modules.objects import engine as _E
    doc = _p.open()
    page = doc.new_page()
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"100 600 50 50 re 1 0 0 rg f\n300 600 50 50 re f", new=True)
    doc.xref_set_key(page.xref, "Contents", f"{x} 0 R")
    _E.translate(doc, 0, [0], 0, -100)
    pix = doc[0].get_pixmap(dpi=72)
    r, g, b = pix.pixel(325, 842 - 625)[:3]
    assert r > 200 and g < 50 and b < 50


def test_clip_of_single_object_moves_with_it(blank):
    add_image(blank, 0, (0, 0, 10, 10), split_png(100, 50))
    name = engine.objects(blank, 0).objects[0].name.encode()
    _set_content(blank, 0, b"q 0 0 50 842 re W n q 100 0 0 50 0 100 cm /" + name + b" Do Q Q")
    w0 = engine.objects(blank, 0).objects[0].rect.width
    assert w0 == pytest.approx(50)
    engine.translate(blank, 0, [0], 200, 0)
    o = engine.objects(blank, 0).objects[0]
    assert o.rect.width == pytest.approx(50) and o.rect.x0 == pytest.approx(200)


def test_page_wide_clip_keeps_drawing_order(blank):
    add_image(blank, 0, (0, 0, 10, 10), split_png(100, 50))
    name = engine.objects(blank, 0).objects[0].name.encode()
    _set_content(blank, 0, b"q 0 0 595 842 re W n\n"
                           b"q 100 0 0 50 0 100 cm /" + name + b" Do Q\n"
                           b"q 100 0 0 50 300 100 cm /" + name + b" Do Q\nQ")
    engine.translate(blank, 0, [0], 10, 0)
    objs = engine.objects(blank, 0).objects
    assert objs[0].rect.x0 == pytest.approx(10) and objs[1].rect.x0 == pytest.approx(300)
