"""OBJ-29 — Catturare un'area."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from foliant.modules.objects import capture

from .conftest import new_pdf


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_two_by_one_inch_at_300dpi(blank):
    png, dpi, reduced = capture.render_area(blank, 0, pymupdf.Rect(100, 100, 244, 172), 300)
    w, h = _size(png)
    assert abs(w - 600) <= 1 and abs(h - 300) <= 1
    assert dpi == 300 and not reduced


def test_annotations_included(blank):
    annot = blank[0].add_rect_annot(pymupdf.Rect(110, 110, 150, 150))
    annot.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    annot.update()
    png, _, _ = capture.render_area(blank, 0, pymupdf.Rect(100, 100, 160, 160), 72)
    assert Image.open(io.BytesIO(png)).convert("RGB").getpixel((30, 30)) == (255, 0, 0)


def test_too_small_area(blank):
    with pytest.raises(ValueError):
        capture.render_area(blank, 0, pymupdf.Rect(10, 10, 11, 50), 72)
    with pytest.raises(ValueError):
        capture.area_text(blank, 0, pymupdf.Rect(10, 10, 11.5, 11.5))


def test_huge_capture_reduced(blank, monkeypatch):
    monkeypatch.setattr(capture, "MAX_PIXELS", 1_000_000)
    png, dpi, reduced = capture.render_area(blank, 0, pymupdf.Rect(0, 0, 100, 100), 1000)
    assert reduced and dpi < 1000
    w, h = _size(png)
    assert w * h <= capture.MAX_PIXELS


def test_area_text_exact_phrase(blank):
    blank[0].insert_text((100, 200), "Totale   fattura", fontsize=12)
    blank[0].insert_text((100, 400), "Altro testo", fontsize=12)
    assert capture.area_text(blank, 0, pymupdf.Rect(90, 185, 300, 210)) == "Totale fattura"


def test_area_text_keeps_lines(blank):
    blank[0].insert_text((100, 200), "Riga uno", fontsize=12)
    blank[0].insert_text((100, 220), "Riga due", fontsize=12)
    assert capture.area_text(blank, 0, pymupdf.Rect(90, 185, 300, 230)) == "Riga uno\nRiga due"


def test_multi_page_stacks_parts():
    pdf = new_pdf(pages=2)
    pdf[0].draw_rect(pymupdf.Rect(0, 700, 595, 842), color=None, fill=(1, 0, 0))
    pdf[1].draw_rect(pymupdf.Rect(0, 0, 595, 100), color=None, fill=(0, 0, 1))
    parts = [(0, pymupdf.Rect(100, 742, 300, 842)), (1, pymupdf.Rect(100, 0, 300, 50))]
    png, dpi, reduced = capture.render_area_multi(pdf, parts, 144)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    assert abs(im.width - 400) <= 1 and abs(im.height - 300) <= 2
    assert im.getpixel((10, 10)) == (255, 0, 0)
    assert im.getpixel((10, im.height - 10)) == (0, 0, 255)
