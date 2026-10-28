"""Test del motore immagini/testo/appunti (PAG-08, PAG-09, PAG-13)."""

from __future__ import annotations

import io
import re

import pymupdf
import pytest
from PIL import Image

from foliant.modules.pages import images
from foliant.modules.pages.engine import MM, PAPER_SIZES, PageOpError

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 200, 0)


# ------------------------------------------------------------------ helper
def _close(a, b, tol=1.0):
    return abs(a - b) <= tol


def _near(px, ref, tol=40):
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px, ref))


def _make_jpeg(path, size=(3000, 2000), dpi=(300, 300), color=RED, exif_orientation=None, mode="RGB"):
    im = Image.new(mode, size, color)
    kw = {"quality": 90}
    if dpi:
        kw["dpi"] = dpi
    if exif_orientation:
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        kw["exif"] = ex.tobytes()
    im.save(path, "JPEG", **kw)
    return str(path)


def _asymmetric(size=(400, 200)):
    """Metà sinistra rossa, metà destra blu, banda verde in alto."""
    w, h = size
    im = Image.new("RGB", size, RED)
    im.paste(BLUE, (w // 2, 0, w, h))
    im.paste(GREEN, (0, 0, w, h // 8))
    return im


def _image_xref(pdf, pno=0):
    imgs = pdf[pno].get_images(full=True)
    assert imgs, "nessuna immagine nella pagina"
    return imgs[0][0]


def _pixel_at(page, x_pt, y_pt, zoom=1.0):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    return pix.pixel(int(x_pt * zoom), int(y_pt * zoom))


# ------------------------------------------------------------------ PAG-08
def test_jpeg_as_image_size_and_bytes_identical(tmp_path):
    path = _make_jpeg(tmp_path / "foto.jpg")
    raw = (tmp_path / "foto.jpg").read_bytes()
    pdf = images.images_to_pdf([path])
    assert pdf.page_count == 1
    r = pdf[0].rect
    assert _close(r.width, 720) and _close(r.height, 480)
    xref = _image_xref(pdf)
    assert "DCTDecode" in pdf.xref_get_key(xref, "Filter")[1]
    assert pdf.xref_stream_raw(xref) == raw


def test_jpeg_bytes_identical_after_insert_and_save(tmp_path):
    path = _make_jpeg(tmp_path / "foto.jpg", size=(600, 400))
    raw = (tmp_path / "foto.jpg").read_bytes()
    pdf = pymupdf.open()
    pdf.new_page()
    new, errs = images.insert_images(pdf, [path], 1)
    assert new == [1] and errs == []
    saved = pymupdf.open("pdf", pdf.tobytes(garbage=3, deflate=True))
    assert saved.xref_stream_raw(_image_xref(saved, 1)) == raw


def test_png_alpha_contain_a4_margins_centered(tmp_path):
    # 400×200 opaco blu con una fascia centrale trasparente
    im = Image.new("RGBA", (400, 200), (0, 0, 255, 255))
    im.paste((0, 0, 0, 0), (150, 0, 250, 200))
    path = tmp_path / "alpha.png"
    im.save(path)
    m = 10 * MM
    pdf = images.images_to_pdf([str(path)], page_size=PAPER_SIZES["A4"], fit="contain", margin=m,
                               landscape=False)
    page = pdf[0]
    assert _close(page.rect.width, 595.28, 0.1) and _close(page.rect.height, 841.89, 0.1)
    info = page.get_image_info()[0]
    bbox = pymupdf.Rect(info["bbox"])
    # proporzioni conservate e larghezza = area utile
    assert _close(bbox.width / bbox.height, 2.0, 0.01)
    assert _close(bbox.x0, m, 0.5) and _close(595.28 - bbox.x1, m, 0.5)
    # centrata verticalmente
    assert _close(bbox.y0, 841.89 - bbox.y1, 0.5)
    # SMask presente, aree trasparenti bianche, opache blu
    xref = _image_xref(pdf)
    assert pdf.xref_get_key(xref, "SMask")[0] == "xref"
    cy = (bbox.y0 + bbox.y1) / 2
    assert _near(_pixel_at(page, page.rect.width / 2, cy), (255, 255, 255))
    assert _near(_pixel_at(page, bbox.x0 + 20, cy), BLUE)
    assert _near(_pixel_at(page, page.rect.width / 2, bbox.y0 - 20), (255, 255, 255))


def test_tiff_three_frames_in_order(tmp_path):
    colors = [RED, GREEN, BLUE]
    frames = [Image.new("RGB", (100, 150), c) for c in colors]
    path = tmp_path / "multi.tif"
    frames[0].save(path, save_all=True, append_images=frames[1:], dpi=(72, 72))
    pdf = images.images_to_pdf([str(path)])
    assert pdf.page_count == 3
    for k, c in enumerate(colors):
        assert _close(pdf[k].rect.width, 100) and _close(pdf[k].rect.height, 150)
        assert _near(_pixel_at(pdf[k], 50, 75), c)


@pytest.mark.parametrize("orientation", [6, 8, 3])
def test_exif_orientation_upright(tmp_path, orientation):
    upright = _asymmetric((400, 200))          # come deve apparire
    inverse = {6: Image.Transpose.ROTATE_90, 8: Image.Transpose.ROTATE_270, 3: Image.Transpose.ROTATE_180}
    stored = upright.transpose(inverse[orientation])   # come lo salverebbe la fotocamera
    path = tmp_path / f"o{orientation}.jpg"
    ex = Image.Exif()
    ex[0x0112] = orientation
    stored.save(path, "JPEG", quality=95, dpi=(72, 72), exif=ex.tobytes())
    raw = path.read_bytes()
    pdf = images.images_to_pdf([str(path)])
    page = pdf[0]
    assert _close(page.rect.width, 400) and _close(page.rect.height, 200)
    assert _near(_pixel_at(page, 100, 150), RED)
    assert _near(_pixel_at(page, 300, 150), BLUE)
    assert _near(_pixel_at(page, 200, 8), GREEN)
    # nessuna ricodifica
    assert pdf.xref_stream_raw(_image_xref(pdf)) == raw


def test_exif_mirrored_orientation_upright(tmp_path):
    upright = _asymmetric((400, 200))
    stored = upright.transpose(Image.Transpose.TRANSPOSE)   # orientamento 5 = trasposizione
    path = tmp_path / "o5.jpg"
    ex = Image.Exif()
    ex[0x0112] = 5
    stored.save(path, "JPEG", quality=95, dpi=(72, 72), exif=ex.tobytes())
    page = images.images_to_pdf([str(path)])[0]
    assert _close(page.rect.width, 400) and _close(page.rect.height, 200)
    assert _near(_pixel_at(page, 100, 150), RED)
    assert _near(_pixel_at(page, 300, 150), BLUE)
    assert _near(_pixel_at(page, 200, 8), GREEN)


def test_corrupt_file_skipped_and_reported(tmp_path):
    good = _make_jpeg(tmp_path / "ok.jpg", size=(200, 100), dpi=(72, 72))
    bad = tmp_path / "rotto.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\nspazzatura")
    missing = tmp_path / "manca.jpg"
    errors: list[str] = []
    pdf = images.images_to_pdf([str(bad), good, str(missing)], errors=errors)
    assert pdf.page_count == 1
    assert len(errors) == 2
    assert any("rotto.png" in e for e in errors) and any("manca.jpg" in e for e in errors)


def test_all_corrupt_raises(tmp_path):
    bad = tmp_path / "rotto.jpg"
    bad.write_bytes(b"\xff\xd8\xff garbage")
    with pytest.raises(PageOpError) as ei:
        images.images_to_pdf([str(bad)])
    assert "rotto.jpg" in str(ei.value)


def test_missing_dpi_uses_default(tmp_path):
    path = tmp_path / "nodpi.png"
    Image.new("RGB", (300, 150), RED).save(path)
    pdf = images.images_to_pdf([str(path)], dpi_default=150)
    assert _close(pdf[0].rect.width, 144) and _close(pdf[0].rect.height, 72)
    implausible = tmp_path / "strano.jpg"
    _make_jpeg(implausible, size=(300, 150), dpi=(1, 1))
    pdf = images.images_to_pdf([str(implausible)], dpi_default=150)
    assert _close(pdf[0].rect.width, 144)


def test_animated_gif_first_frame_with_warning(tmp_path):
    frames = [Image.new("P", (60, 40), i) for i in range(3)]
    frames[0].putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * 759)
    path = tmp_path / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    errors: list[str] = []
    pdf = images.images_to_pdf([str(path)], errors=errors)
    assert pdf.page_count == 1
    assert errors and "anim.gif" in errors[0]


def test_cmyk_jpeg_colors(tmp_path):
    path = _make_jpeg(tmp_path / "cmyk.jpg", size=(100, 100), dpi=(72, 72), color=(0, 255, 255, 0), mode="CMYK")
    raw = (tmp_path / "cmyk.jpg").read_bytes()
    pdf = images.images_to_pdf([path])
    assert pdf.xref_stream_raw(_image_xref(pdf)) == raw
    px = _pixel_at(pdf[0], 50, 50)
    assert px[0] > 180 and px[1] < 90 and px[2] < 90       # rosso, non ciano


def test_other_formats_bmp_webp_jp2(tmp_path):
    paths = []
    for ext, fmt in (("bmp", "BMP"), ("webp", "WEBP"), ("jp2", "JPEG2000")):
        p = tmp_path / f"img.{ext}"
        Image.new("RGB", (80, 40), GREEN).save(p, fmt)
        paths.append(str(p))
    errors: list[str] = []
    pdf = images.images_to_pdf(paths, errors=errors)
    assert pdf.page_count == 3, errors
    jp2_xref = _image_xref(pdf, 2)
    assert "JPXDecode" in pdf.xref_get_key(jp2_xref, "Filter")[1]
    for k in range(3):
        assert _near(_pixel_at(pdf[k], 20, 10), GREEN, tol=60)


def test_landscape_auto_and_fit_modes(tmp_path):
    path = tmp_path / "wide.png"
    _asymmetric((400, 200)).save(path, dpi=(72, 72))
    a4 = PAPER_SIZES["A4"]
    pdf = images.images_to_pdf([str(path)], page_size=a4, fit="contain")
    assert pdf[0].rect.width > pdf[0].rect.height       # orizzontale automatico
    pdf = images.images_to_pdf([str(path)], page_size=a4, fit="center", landscape=False)
    bbox = pymupdf.Rect(pdf[0].get_image_info()[0]["bbox"])
    assert _close(bbox.width, 400) and _close(bbox.height, 200)
    pdf = images.images_to_pdf([str(path)], page_size=a4, fit="fill", landscape=False, margin=20)
    page = pdf[0]
    # riempie l'area utile: rosso a sinistra e blu a destra fino ai margini, margini bianchi
    assert _near(_pixel_at(page, 30, 420), RED)
    assert _near(_pixel_at(page, 565, 420), BLUE)
    assert _near(_pixel_at(page, 10, 420), (255, 255, 255))
    assert _near(_pixel_at(page, 300, 10), (255, 255, 255))


def test_recompress_option(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (300, 300), RED).save(path)
    pdf = images.images_to_pdf([str(path)], recompress=True)
    assert "DCTDecode" in pdf.xref_get_key(_image_xref(pdf), "Filter")[1]


def test_insert_images_position_and_adjacent(tmp_path):
    a = _make_jpeg(tmp_path / "a.jpg", size=(200, 100), dpi=(72, 72))
    b = _make_jpeg(tmp_path / "b.jpg", size=(100, 200), dpi=(72, 72), color=BLUE)
    pdf = pymupdf.open()
    for _i in range(3):
        pdf.new_page(width=300, height=500)
    new, errs = images.insert_images(pdf, [a, b], 2, page_size="adjacent")
    assert new == [2, 3] and not errs
    assert pdf.page_count == 5
    # formato della pagina adiacente, orientato secondo l'immagine
    assert _close(pdf[2].rect.width, 500) and _close(pdf[2].rect.height, 300)
    assert _close(pdf[3].rect.width, 300) and _close(pdf[3].rect.height, 500)


def test_huge_image_reduced_with_warning(tmp_path):
    path = tmp_path / "enorme.png"
    Image.new("L", (3000, 100), 0).save(path, dpi=(10, 10))      # 300 pollici
    errors: list[str] = []
    pdf = images.images_to_pdf([str(path)], errors=errors)
    assert pdf[0].rect.width <= 14400.5
    assert errors


def test_invalid_options():
    with pytest.raises(PageOpError):
        images.images_to_pdf([])
    with pytest.raises(PageOpError):
        images.images_to_pdf(["x.png"], page_size=(100, 100), margin=60)


# ------------------------------------------------------------------ PAG-09
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def test_text_200_lines_round_trip():
    lines = [f"Riga {i}: perché più città è già finita, l'orologio d'oro costa {i},50 € " + "parola " * (i % 25)
             for i in range(200)]
    text = "\n".join(lines)
    pdf = images.text_to_pdf(text, page_size=PAPER_SIZES["A4"])
    assert pdf.page_count > 1
    for p in pdf:
        assert _close(p.rect.width, 595.28, 0.1)
    extracted = "".join(p.get_text() for p in pdf)
    assert _norm(extracted) == _norm(text)
    # fonts incorporati
    assert all(f[1] != "n/a" for f in pdf[0].get_fonts())


def test_text_long_word_and_empty():
    pdf = images.text_to_pdf("A" * 500 + "\n\nfine")
    assert _norm("".join(p.get_text() for p in pdf)).replace(" ", "") == "A" * 500 + "fine"
    with pytest.raises(PageOpError):
        images.text_to_pdf("   \n ")


def test_text_uncovered_chars_use_fallback_or_warn():
    warnings: list[str] = []
    pdf = images.text_to_pdf("Orologio 時計 ok", warnings=warnings)
    out = pdf[0].get_text()
    assert "Orologio" in out and "ok" in out
    assert "時計" in out or warnings


def test_clipboard_pdf_preferred(tmp_path):
    src = pymupdf.open()
    for k in range(2):
        src.new_page().insert_text((72, 100), f"Copiata {k + 1}")
    data = {"application/pdf": src.tobytes(), "text": "ignorato", "image": b"x"}
    assert images.clipboard_kind(data) == images.CLIP_PDF
    doc = images.clipboard_payload_to_pdf(data)
    assert doc.page_count == 2 and "Copiata 2" in doc[1].get_text()


def test_clipboard_pdf_pages_inserted_identical(tmp_path):
    # PAG-09 criterio 1: due pagine di A inserite in B dopo la pagina 1 → posizioni 2–3
    a = pymupdf.open()
    for k in range(4):
        a.new_page().insert_text((72, 100), f"Documento A pagina {k + 1}", fontsize=20)
    sub = pymupdf.open()
    sub.insert_pdf(a, from_page=1, to_page=2)
    b = pymupdf.open()
    for k in range(3):
        b.new_page().insert_text((72, 100), f"B {k + 1}")
    clip = images.clipboard_payload_to_pdf({"application/pdf": sub.tobytes()})
    b.insert_pdf(clip, start_at=1)
    assert b.page_count == 5
    for k, src_i in ((1, 1), (2, 2)):
        assert b[k].get_pixmap().samples == a[src_i].get_pixmap().samples


def test_clipboard_screenshot_full_page():
    im = _asymmetric((640, 360))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    doc = images.clipboard_payload_to_pdf({"image": buf.getvalue()})
    assert doc.page_count == 1
    page = doc[0]
    bbox = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    assert _close(bbox.width, page.rect.width) and _close(bbox.height, page.rect.height)


def test_clipboard_text_and_html_and_uris(tmp_path):
    doc = images.clipboard_payload_to_pdf({"text": "ciao mondo"})
    assert "ciao mondo" in doc[0].get_text()
    doc = images.clipboard_payload_to_pdf({"html": "<p>Uno &amp; due</p><ul><li>tre</li></ul>"})
    assert "Uno & due" in doc[0].get_text() and "tre" in doc[0].get_text()
    img = _make_jpeg(tmp_path / "u.jpg", size=(100, 100), dpi=(72, 72))
    pdfp = tmp_path / "u.pdf"
    p = pymupdf.open()
    p.new_page()
    p.new_page()
    p.save(pdfp)
    doc = images.clipboard_payload_to_pdf({"uris": ["file://" + img, str(pdfp)], "text": "x"})
    assert doc.page_count == 3
    assert images.clipboard_payload_to_pdf({}) is None
    assert images.clipboard_payload_to_pdf({"text": "  "}) is None
    assert images.clipboard_payload_to_pdf({"uris": ["file:///non/esiste.png"]}) is None
    with pytest.raises(PageOpError):
        images.clipboard_payload_to_pdf({"application/pdf": b"non un pdf"})


# ------------------------------------------------------------------ PAG-13
def _sample(tmp_path, pages=5, highlight=False):
    doc = pymupdf.open()
    for k in range(pages):
        page = doc.new_page(width=595.28, height=841.89)
        page.insert_text((72, 100), f"Pagina {k + 1}", fontsize=24)
        if highlight:
            page.insert_text((72, 300), "Testo evidenziato", fontsize=30)
            quads = page.search_for("Testo evidenziato")
            annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=(1, 1, 0))
            annot.update()
    return doc


def test_export_png_a4_150dpi(tmp_path):
    doc = _sample(tmp_path, 1)
    files = images.export_page_images(doc, [0], tmp_path / "out", fmt="png", dpi=150, base_name="contratto")
    assert len(files) == 1 and files[0].name == "contratto_p1.png"
    with Image.open(files[0]) as im:
        assert abs(im.width - 1240) <= 1 and abs(im.height - 1754) <= 1
        dpi = im.info["dpi"]
        assert round(dpi[0]) == 150 and round(dpi[1]) == 150


def test_export_multipage_tiff_five_frames(tmp_path):
    doc = _sample(tmp_path, 5)
    files = images.export_page_images(doc, range(5), tmp_path, fmt="tiff", dpi=40, multipage_tiff=True)
    assert len(files) == 1
    with Image.open(files[0]) as im:
        assert im.n_frames == 5
        dark = []
        for k in range(5):
            im.seek(k)
            assert round(im.info["dpi"][0]) == 40
            dark.append(im.convert("L").getextrema()[0])
        assert all(d < 128 for d in dark)
    # ordine: confronto col rendering di ciascuna pagina
    ref = images.export_page_images(doc, range(5), tmp_path / "sep", fmt="png", dpi=40)
    with Image.open(files[0]) as im:
        for k in range(5):
            im.seek(k)
            with Image.open(ref[k]) as r:
                assert im.convert("RGB").tobytes() == r.convert("RGB").tobytes()


def test_export_without_annotations(tmp_path):
    doc = _sample(tmp_path, 1, highlight=True)
    page = doc[0]
    rect = pymupdf.Rect(page.first_annot.rect)
    del page
    with_a = images.export_page_images(doc, [0], tmp_path, fmt="png", dpi=72, template="con")[0]
    without = images.export_page_images(doc, [0], tmp_path, fmt="png", dpi=72, annots=False, template="senza")[0]

    def yellow_pixels(path):
        with Image.open(path) as im:
            crop = im.convert("RGB").crop((int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)))
            data = crop.tobytes()
            return sum(1 for k in range(0, len(data), 3)
                       if data[k] > 200 and data[k + 1] > 200 and data[k + 2] < 120)
    assert yellow_pixels(with_a) > 100
    assert yellow_pixels(without) == 0


def test_export_formats_color_modes(tmp_path):
    doc = _sample(tmp_path, 2)
    for fmt in images.EXPORT_FORMATS:
        files = images.export_page_images(doc, [0, 1], tmp_path / fmt, fmt=fmt, dpi=30)
        assert len(files) == 2 and all(f.exists() and f.stat().st_size > 0 for f in files)
    g = images.export_page_images(doc, [0], tmp_path / "g", fmt="png", dpi=30, colorspace="gray")[0]
    with Image.open(g) as im:
        assert im.mode == "L"
    bw = images.export_page_images(doc, [0], tmp_path / "bw", fmt="tiff", dpi=30, colorspace="bw")[0]
    with Image.open(bw) as im:
        assert im.mode == "1"
    t = images.export_page_images(doc, [0], tmp_path / "t", fmt="png", dpi=30, transparent=True)[0]
    with Image.open(t) as im:
        assert im.mode == "RGBA" and im.getpixel((2, 2))[3] == 0
    j = images.export_page_images(doc, [0], tmp_path / "j", fmt="jpeg", dpi=30, transparent=True)[0]
    with Image.open(j) as im:
        assert im.mode == "RGB" and round(im.info["dpi"][0]) == 30


def test_export_rotation_area_and_names(tmp_path):
    doc = _sample(tmp_path, 2)
    doc[0].set_rotation(90)
    doc[1].set_cropbox(pymupdf.Rect(0, 0, 300, 400))
    files = images.export_page_images(doc, [0, 1], tmp_path, fmt="png", dpi=72, template="{nome}-{pagina}",
                                      base_name="x")
    with Image.open(files[0]) as im:
        assert abs(im.width - 842) <= 1 and abs(im.height - 595) <= 1
    with Image.open(files[1]) as im:
        assert im.size == (300, 400)
    media = images.export_page_images(doc, [1], tmp_path, fmt="png", dpi=72, area="media", template="{nome}-{pagina}",
                                      base_name="x")[0]
    assert media.name == "x-2 (2).png"
    with Image.open(media) as im:
        assert abs(im.width - 595) <= 1 and abs(im.height - 842) <= 1
    assert doc[1].cropbox == pymupdf.Rect(0, 0, 300, 400)    # documento non toccato


def test_export_limits_and_errors(tmp_path):
    doc = _sample(tmp_path, 1)
    with pytest.raises(images.ResolutionTooHigh) as ei:
        images.export_page_images(doc, [0], tmp_path, dpi=600, max_pixels=1_000_000)
    assert 10 <= ei.value.suggested_dpi < 600
    assert images.suggested_dpi(doc, [0], ei.value.suggested_dpi, max_pixels=1_000_000) is None
    with pytest.raises(PageOpError):
        images.export_page_images(doc, [0], tmp_path, fmt="gif")
    with pytest.raises(PageOpError):
        images.export_page_images(doc, [], tmp_path)
    with pytest.raises(PageOpError):
        images.export_page_images(doc, [0], tmp_path, dpi=5)


def test_export_progress_and_cancel(tmp_path):
    from foliant.api import OperationCancelled, Progress
    doc = _sample(tmp_path, 3)
    calls = []
    images.export_page_images(doc, range(3), tmp_path, dpi=20, progress=lambda f, t: calls.append(f))
    assert calls[0] == 0 and calls[-1] == 1.0
    prog = Progress()
    prog.cancel()
    with pytest.raises(OperationCancelled):
        images.export_page_images(doc, range(3), tmp_path / "c", dpi=20, progress=prog)
