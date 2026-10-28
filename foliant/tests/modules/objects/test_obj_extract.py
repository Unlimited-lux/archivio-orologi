"""OBJ-28 — Estrai tutte le immagini."""

from __future__ import annotations

import os
import threading

import pymupdf
import pytest
from PIL import Image

from foliant.core.errors import OperationCancelled
from foliant.modules.objects import extract

from .conftest import add_image, jpeg_bytes, new_pdf, png_bytes


def _five_images_one_on_three_pages():
    pdf = new_pdf(pages=3)
    shared = add_image(pdf, 0, (10, 10, 60, 60), png_bytes(40, 40, (1, 2, 3)))
    add_image(pdf, 1, (10, 10, 60, 60), xref=shared)
    add_image(pdf, 2, (10, 10, 60, 60), xref=shared)
    add_image(pdf, 0, (100, 10, 150, 60), png_bytes(40, 40, (200, 0, 0)))
    add_image(pdf, 1, (100, 10, 150, 60), png_bytes(40, 40, (0, 200, 0)))
    add_image(pdf, 2, (100, 10, 150, 60), png_bytes(40, 40, (0, 0, 200)))
    add_image(pdf, 2, (200, 10, 250, 60), jpeg_bytes(40, 40))
    return pdf


def test_dedup_five_files_without_seven(tmp_path):
    pdf = _five_images_one_on_three_pages()
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path / "a"), "doc")
    assert len(rep.written) == 5 and rep.duplicates == 2
    assert len(os.listdir(tmp_path / "a")) == 5
    rep = extract.run(pdf, extract.ExtractOptions(dedup=False), str(tmp_path / "b"), "doc")
    assert len(rep.written) == 7 and rep.duplicates == 0
    assert len(os.listdir(tmp_path / "b")) == 7
    names = sorted(os.listdir(tmp_path / "b"))
    assert "doc_p1_img1.png" in names and "doc_p3_img3.jpg" in names


def test_dedup_by_content_distinct_xrefs(tmp_path):
    pdf = new_pdf(pages=2)
    data = png_bytes(30, 30, (9, 9, 9))
    a = add_image(pdf, 0, (10, 10, 50, 50), data)
    b = pdf.get_new_xref()
    pdf.update_object(b, "<<>>")
    pdf.xref_copy(a, b)
    add_image(pdf, 1, (10, 10, 50, 50), xref=b)
    assert extract.plan(pdf, extract.ExtractOptions())[1].obj.xref == b
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path), "x")
    assert len(rep.written) == 1 and rep.duplicates == 1


def test_min_size_skips_small(tmp_path):
    pdf = new_pdf()
    add_image(pdf, 0, (10, 10, 26, 26), png_bytes(16, 16))
    add_image(pdf, 0, (50, 10, 100, 60), png_bytes(64, 64, (0, 0, 255)))
    rep = extract.run(pdf, extract.ExtractOptions(min_width=32, min_height=32), str(tmp_path), "d")
    assert len(rep.written) == 1
    assert len(rep.skipped) == 1 and "32×32" in rep.skipped[0][1]
    assert Image.open(rep.written[0]).size == (64, 64)


def test_cancel_after_ten_of_fifty(tmp_path):
    pdf = new_pdf(pages=5)
    for i in range(50):
        add_image(pdf, i // 10, (10 + (i % 10) * 50, 10, 50 + (i % 10) * 50, 50), png_bytes(8, 8, (i * 5, 0, 0)))
    calls = []

    def progress(frac, text):
        calls.append(frac)
        if len(calls) > 10:
            raise OperationCancelled()

    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path), "d", progress=progress, lock=threading.RLock())
    assert rep.cancelled
    assert len(rep.written) == 10 and rep.not_processed == 40
    assert len(os.listdir(tmp_path)) == 10


def test_homonymous_files_get_suffix(tmp_path):
    pdf = new_pdf()
    add_image(pdf, 0, (10, 10, 50, 50), png_bytes(20, 20))
    (tmp_path / "d_p1_img1.png").write_bytes(b"old")
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path), "d")
    assert rep.written == [str(tmp_path / "d_p1_img1_2.png")]
    assert (tmp_path / "d_p1_img1.png").read_bytes() == b"old"
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path), "d")
    assert rep.written == [str(tmp_path / "d_p1_img1_3.png")]


def test_no_images_no_folder(tmp_path):
    pdf = new_pdf()
    pdf[0].insert_text((50, 50), "solo testo")
    target = tmp_path / "out"
    rep = extract.run(pdf, extract.ExtractOptions(), str(target), "d")
    assert rep.written == [] and not target.exists()


def test_page_range_and_formats(tmp_path):
    pdf = _five_images_one_on_three_pages()
    rep = extract.run(pdf, extract.ExtractOptions(pages=[2], fmt="png", dedup=False), str(tmp_path), "d")
    assert len(rep.written) == 3 and all(p.endswith(".png") and "_p3_" in p for p in rep.written)
    rep = extract.run(pdf, extract.ExtractOptions(pages=[0], fmt="jpeg"), str(tmp_path / "j"), "d")
    assert all(p.endswith(".jpg") for p in rep.written)


def test_original_jpeg_kept(tmp_path):
    pdf = new_pdf()
    data = jpeg_bytes(40, 20)
    add_image(pdf, 0, (10, 10, 90, 50), data)
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path), "d")
    assert rep.written[0].endswith(".jpg")


def _inline_pdf():
    pdf = new_pdf()
    page = pdf[0]
    page.insert_text((10, 10), " ")
    xref = page.get_contents()[0]
    stream = b"q 40 0 0 40 100 700 cm BI /W 4 /H 4 /CS /RGB /BPC 8 ID " + b"\xff\x00\x00" * 16 + b"\nEI Q"
    pdf.update_stream(xref, stream)
    return pdf


def test_inline_images(tmp_path):
    pdf = _inline_pdf()
    jobs = extract.plan(pdf, extract.ExtractOptions())
    assert len(jobs) == 1 and jobs[0].obj.inline
    rep = extract.run(pdf, extract.ExtractOptions(include_inline=False), str(tmp_path / "no"), "d")
    assert rep.written == [] and len(rep.skipped) == 1
    rep = extract.run(pdf, extract.ExtractOptions(), str(tmp_path / "yes"), "d")
    assert len(rep.written) == 1
    im = Image.open(rep.written[0])
    assert abs(im.width - 4) <= 1 and im.convert("RGB").getpixel((1, 1))[0] > 200


def test_disk_error_raises(tmp_path):
    pdf = new_pdf()
    add_image(pdf, 0, (10, 10, 50, 50), png_bytes(20, 20))
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    with pytest.raises(extract.ExtractError) as ei:
        extract.run(pdf, extract.ExtractOptions(), str(blocker / "sub"), "d")
    assert ei.value.report.not_processed == 1
