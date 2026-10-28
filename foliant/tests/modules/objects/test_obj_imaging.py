"""Test di OBJ-17 (regolazioni dell'immagine) e OBJ-18 (ottimizzazione)."""

from __future__ import annotations

import io
import zlib

import numpy as np
import pymupdf
import pytest
from PIL import Image

from foliant.modules.objects import engine, imaging
from foliant.modules.objects.engine import ObjectError

from .conftest import add_image, approx_rect, doc_from, image_objects, jpeg_bytes, new_pdf, png_bytes, render, split_png


def draw_xobject(pdf, dict_text: str, raw: bytes, filt: str = "", page_index: int = 0,
                 cm: bytes = b"80 0 0 40 0 0") -> int:
    """Crea un image XObject a mano e lo disegna sulla pagina."""
    xref = pdf.get_new_xref()
    pdf.update_object(xref, dict_text)
    pdf.update_stream(xref, raw, new=True, compress=False)
    if filt:
        pdf.xref_set_key(xref, "Filter", filt)
    page = pdf[page_index]
    engine.set_resource(pdf, page, "XObject", "X0", f"{xref} 0 R")
    engine.write_content(pdf, page, b"q " + cm + b" cm /X0 Do Q")
    return xref


def add_palette_image(pdf) -> int:
    data = bytes((x + y) % 3 for y in range(4) for x in range(8))
    return draw_xobject(pdf, "<</Type/XObject/Subtype/Image/Width 8/Height 4/BitsPerComponent 8"
                             "/ColorSpace[/Indexed/DeviceRGB 2 <ff000000ff000000ff>]>>",
                        zlib.compress(data), "/FlateDecode")


def gradient_jpeg(w: int, h: int) -> bytes:
    x = np.linspace(0, 255, w, dtype=np.float32)
    y = np.linspace(0, 255, h, dtype=np.float32)
    arr = np.stack([np.add.outer(y, x) / 2, np.tile(x, (h, 1)), np.tile(y[:, None], (1, w))], axis=-1)
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


def only_image(pdf, page_index=0):
    (o,) = image_objects(pdf, page_index)
    return o


# =================================================================== OBJ-17
def test_grayscale_one_component_same_size():
    pdf = new_pdf()
    add_image(pdf, 0, (50, 50, 250, 150), split_png(40, 20))
    imaging.adjust_image(pdf, 0, 0, grayscale=True)
    o = only_image(pdf)
    assert pdf.xref_get_key(o.xref, "ColorSpace")[1] == "/DeviceGray"
    pix = pymupdf.Pixmap(pdf, o.xref)
    assert (pix.n, pix.width, pix.height) == (1, 40, 20)
    assert approx_rect(o.rect, (50, 50, 250, 150))
    assert "Flate" in pdf.xref_get_key(o.xref, "Filter")[1]


def test_alpha_mask_kept_and_jpeg_reencoded():
    pdf = new_pdf()
    add_image(pdf, 0, (0, 0, 100, 100), png_bytes(10, 10, (200, 100, 50), mode="RGBA"))
    smask = int(pdf.xref_get_key(only_image(pdf).xref, "SMask")[1].split()[0])
    mask_data = pdf.xref_stream_raw(smask)
    imaging.adjust_image(pdf, 0, 0, brightness=30, contrast=20)
    o = only_image(pdf)
    assert pdf.xref_get_key(o.xref, "SMask")[1] == f"{smask} 0 R"
    assert pdf.xref_stream_raw(smask) == mask_data

    pdf2 = new_pdf()
    add_image(pdf2, 0, (0, 0, 100, 100), jpeg_bytes(32, 16))
    imaging.adjust_image(pdf2, 0, 0, saturation=-50, jpeg_quality=70)
    o2 = only_image(pdf2)
    assert pdf2.xref_get_key(o2.xref, "Filter")[1] == "/DCTDecode"
    assert (o2.width, o2.height) == (32, 16)


def test_palette_invert_changes_only_palette():
    pdf = new_pdf()
    old = add_palette_image(pdf)
    indices = pdf.xref_stream(old)
    before = pymupdf.Pixmap(pdf, old).samples
    imaging.adjust_image(pdf, 0, 0, invert=True)
    new = only_image(pdf).xref
    assert new != old
    assert pdf.xref_stream(new) == indices
    assert "Indexed" in pdf.xref_object(new, compressed=True)
    after = pymupdf.Pixmap(pdf, new).samples
    assert bytes(255 - b for b in before) == after


def test_undo_restores_rendering():
    pdf = new_pdf()
    add_image(pdf, 0, (50, 50, 250, 150), split_png(40, 20))
    doc = doc_from(pdf)
    original = render(doc.pdf)
    with doc.change("Regola immagine") as p:
        imaging.adjust_image(p, 0, 0, brightness=40, contrast=-30, saturation=60, invert=True)
    assert render(doc.pdf) != original
    assert doc.undo()
    assert render(doc.pdf) == original


def test_only_this_occurrence_leaves_other_page():
    pdf = new_pdf(2)
    x = add_image(pdf, 0, (10, 10, 110, 60), split_png(40, 20))
    add_image(pdf, 1, (10, 10, 110, 60), xref=x)
    other = render(pdf, 1)
    first = render(pdf, 0)
    imaging.adjust_image(pdf, 0, 0, invert=True)
    assert render(pdf, 1) == other
    assert render(pdf, 0) != first
    assert only_image(pdf, 1).xref == x
    assert only_image(pdf, 0).xref != x


def test_all_occurrences_changes_shared_xobject():
    pdf = new_pdf(2)
    x = add_image(pdf, 0, (10, 10, 110, 60), split_png(40, 20))
    add_image(pdf, 1, (10, 10, 110, 60), xref=x)
    other = render(pdf, 1)
    names = set(engine.resource_names(pdf, pdf[0], "XObject"))
    imaging.adjust_image(pdf, 0, 0, grayscale=True, all_occurrences=True)
    assert only_image(pdf, 0).xref == x and only_image(pdf, 1).xref == x
    assert render(pdf, 1) != other
    assert set(engine.resource_names(pdf, pdf[0], "XObject")) == names


def test_stencil_mask_only_invert_and_errors_leave_doc_unchanged():
    pdf = new_pdf()
    draw_xobject(pdf, "<</Type/XObject/Subtype/Image/Width 8/Height 1/ImageMask true/BitsPerComponent 1>>",
                 b"\xf0", cm=b"0 0 1 rg 80 0 0 10 0 0")
    snapshot = pdf.tobytes(no_new_id=True)
    with pytest.raises(ObjectError):
        imaging.adjust_image(pdf, 0, 0, brightness=10)
    with pytest.raises(ObjectError):
        imaging.adjust_image(pdf, 0, 0)
    assert pdf.tobytes(no_new_id=True) == snapshot
    imaging.adjust_image(pdf, 0, 0, invert=True)
    new = only_image(pdf).xref
    assert pdf.xref_get_key(new, "Decode")[1].replace(" ", "") == "[10]"
    assert pdf.xref_stream_raw(new) == b"\xf0"


# =================================================================== OBJ-18
def test_downsample_to_max_ppi_keeps_drawn_rect():
    pdf = new_pdf()
    rect = (50, 50, 410, 410)  # 5 × 5 in
    add_image(pdf, 0, rect, gradient_jpeg(3000, 3000))
    est = imaging.estimate_optimization(pdf, 0, [0], max_ppi=150)
    res = imaging.optimize_images(pdf, 0, [0], max_ppi=150, mode="jpeg", quality=80)
    o = only_image(pdf)
    assert (o.width, o.height) == (750, 750)
    assert approx_rect(o.rect, rect)
    assert res["changed"] == 1 and res["kept"] == []
    assert res["after"] < res["before"]
    assert (est["before"], est["after"]) == (res["before"], res["after"])


def test_heavier_result_keeps_original_data():
    pdf = new_pdf()
    add_image(pdf, 0, (0, 0, 100, 100), png_bytes(4, 4))
    x = only_image(pdf).xref
    raw = pdf.xref_stream_raw(x)
    obj = pdf.xref_object(x)
    res = imaging.optimize_images(pdf, 0, [0], max_ppi=150, mode="jpeg", quality=95)
    assert res["changed"] == 0 and res["kept"] == [0]
    assert res["before"] == res["after"]
    assert pdf.xref_stream_raw(x) == raw and pdf.xref_object(x) == obj


def test_smask_resampled_and_estimate_does_not_modify():
    pdf = new_pdf()
    im = Image.new("RGBA", (600, 300))
    arr = np.zeros((300, 600, 4), np.uint8)
    arr[..., 0] = np.linspace(0, 255, 600, dtype=np.uint8)
    arr[..., 3] = np.linspace(0, 255, 300, dtype=np.uint8)[:, None]
    im = Image.fromarray(arr, "RGBA")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    add_image(pdf, 0, (0, 0, 144, 72), buf.getvalue())  # 2 × 1 in → 300 ppi
    snapshot = pdf.tobytes(no_new_id=True)
    est = imaging.estimate_optimization(pdf, 0, [0], max_ppi=150, mode="lossless")
    assert pdf.tobytes(no_new_id=True) == snapshot
    imaging.optimize_images(pdf, 0, [0], max_ppi=150, mode="lossless")
    o = only_image(pdf)
    assert (o.width, o.height) == (300, 150)
    smask = int(pdf.xref_get_key(o.xref, "SMask")[1].split()[0])
    m = pymupdf.Pixmap(pdf, smask)
    assert (m.width, m.height) == (300, 150)
    assert est["after"] < est["before"]


def test_unsupported_colorspace_raises_and_color_key_becomes_smask():
    pdf = new_pdf()
    draw_xobject(pdf, "<</Type/XObject/Subtype/Image/Width 2/Height 1/BitsPerComponent 8"
                      "/ColorSpace[/Lab<</WhitePoint[0.95 1 1.09]>>]>>", b"\x80\x00\x00\x80\x00\x00")
    snapshot = pdf.tobytes(no_new_id=True)
    with pytest.raises(ObjectError):
        imaging.adjust_image(pdf, 0, 0, invert=True)
    assert pdf.tobytes(no_new_id=True) == snapshot

    pdf2 = new_pdf()
    draw_xobject(pdf2, "<</Type/XObject/Subtype/Image/Width 2/Height 1/BitsPerComponent 8"
                       "/ColorSpace/DeviceGray/Mask[0 0]>>", b"\x00\xff")
    imaging.adjust_image(pdf2, 0, 0, invert=True)
    o = only_image(pdf2)
    smask = int(pdf2.xref_get_key(o.xref, "SMask")[1].split()[0])
    assert pymupdf.Pixmap(pdf2, smask).samples == b"\x00\xff"
    assert pymupdf.Pixmap(pdf2, o.xref).samples == b"\xff\x00"
