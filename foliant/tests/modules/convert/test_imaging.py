"""Raddrizzamento e ritaglio automatici."""

from __future__ import annotations

from PIL import Image, ImageDraw

from foliant.modules.convert.imaging import autocrop, deskew, estimate_skew


def _text_page(angle: float = 0.0):
    img = Image.new("L", (850, 1100), 255)
    d = ImageDraw.Draw(img)
    for k in range(25):
        y = 100 + k * 36
        for x in range(80, 760, 60):
            d.rectangle((x, y, x + 45, y + 12), fill=0)
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255) if angle else img


def test_estimate_skew():
    for angle in (-3.0, 0.0, 2.0, 4.5):
        assert abs(estimate_skew(_text_page(angle)) + angle) <= 0.3


def test_deskew_straightens():
    fixed, applied = deskew(_text_page(2.5))
    assert abs(applied + 2.5) <= 0.3
    assert abs(estimate_skew(fixed)) <= 0.3
    blank = Image.new("L", (300, 300), 255)
    assert deskew(blank)[1] == 0.0


def test_autocrop_dark_borders():
    page = Image.new("L", (1000, 1300), 0)
    page.paste(Image.new("L", (800, 1100), 255), (100, 120))
    out = autocrop(page)
    assert 780 <= out.width <= 800 and 1080 <= out.height <= 1100
    clean = Image.new("L", (500, 500), 255)
    assert autocrop(clean) is clean
