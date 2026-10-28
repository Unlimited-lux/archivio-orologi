"""Test dell'acquisizione da scanner (CNV-24) con un dispositivo simulato."""

from __future__ import annotations

import pymupdf
import pytest
from PIL import Image, ImageDraw

from foliant.modules.convert.scanner import (
    ScannerError, ScanSettings, acquire, id_card_pdf, is_blank, merge_duplex, pages_to_pdf,
)


def _page(dpi: int, text: str, blank: bool = False) -> Image.Image:
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("RGB", (w, h), "white")
    if not blank:
        d = ImageDraw.Draw(img)
        for k in range(12):
            d.rectangle((w // 10, h // 10 + k * h // 16, w * 8 // 10, h // 10 + k * h // 16 + h // 60), fill="black")
        d.text((w // 10, h // 20), text, fill="black")
    return img


class FakeScanner:
    """Dispositivo simulato: produce `pages` immagini, eventualmente si scollega dopo `fail_after`."""

    def __init__(self, pages, fail_after=None):
        self.pages = pages
        self.fail_after = fail_after

    def list_devices(self):
        return [("sim:0", "Scanner simulato")]

    def scan(self, device, settings, progress=None):
        for k, img in enumerate(self.pages):
            if self.fail_after is not None and k >= self.fail_after:
                raise ScannerError("Scanner scollegato")
            yield img


def test_adf_bw_300dpi_g4():
    settings = ScanSettings(source="adf", dpi=300, mode="bw")
    res = acquire(FakeScanner([_page(300, f"p{k}") for k in range(5)]), "sim:0", settings)
    data = pages_to_pdf(res.pages, settings)
    doc = pymupdf.open(stream=data)
    assert doc.page_count == 5
    for page in doc:
        xref, _s, w, h, bpc, cs = page.get_images(full=True)[0][:6]
        assert bpc == 1
        assert doc.xref_get_key(xref, "Filter")[1] == "/CCITTFaxDecode"
        bbox = page.get_image_bbox(page.get_images(full=True)[0])
        assert abs(w / (bbox.width / 72) - 300) <= 1
        # immagine leggibile dal motore (dati G4 validi)
        pix = pymupdf.Pixmap(doc, xref)
        assert pix.width == w


def test_duplex_remove_blank():
    sheets = [_page(100, "f1"), _page(100, "r1"), _page(100, "f2"), _page(100, "", blank=True),
              _page(100, "f3"), _page(100, "r3")]
    settings = ScanSettings(source="adf-duplex", dpi=100, mode="gray")
    res = acquire(FakeScanner(sheets), "sim:0", settings, remove_blank=True)
    assert len(res.pages) == 5 and res.removed_blank == 1
    assert pymupdf.open(stream=pages_to_pdf(res.pages, settings)).page_count == 5


def test_disconnect_keeps_pages():
    settings = ScanSettings(source="adf", dpi=100)
    res = acquire(FakeScanner([_page(100, str(k)) for k in range(6)], fail_after=3), "sim:0", settings)
    assert len(res.pages) == 3 and "scollegato" in res.error
    assert pymupdf.open(stream=pages_to_pdf(res.pages, settings)).page_count == 3


def test_id_card():
    settings = ScanSettings(dpi=300, mode="color")
    front = Image.new("RGB", (int(85.6 / 25.4 * 300), int(54 / 25.4 * 300)), (200, 50, 50))
    back = Image.new("RGB", front.size, (50, 50, 200))
    doc = pymupdf.open(stream=id_card_pdf(front, back, settings))
    assert doc.page_count == 1
    page = doc[0]
    assert abs(page.rect.width - 595.28) < 1 and abs(page.rect.height - 841.89) < 1
    boxes = [page.get_image_bbox(img) for img in page.get_images(full=True)]
    assert len(boxes) == 2
    for b in boxes:
        assert abs(b.width / 72 * 25.4 - 85.6) <= 0.5 and abs(b.height / 72 * 25.4 - 54) <= 0.5
    assert boxes[0].y0 < boxes[1].y0   # fronte sopra


def test_merge_duplex_and_blank():
    assert merge_duplex(["f1", "f2", "f3"], ["r3", "r2", "r1"]) == ["f1", "r1", "f2", "r2", "f3", "r3"]
    assert is_blank(_page(50, "", blank=True)) and not is_blank(_page(50, "x"))


def test_sane_backend_without_program():
    from foliant.modules.convert.scanner import SaneBackend
    b = SaneBackend(program=None)
    b.program = None
    assert b.list_devices() == []
    with pytest.raises(ScannerError):
        list(b.scan("x", ScanSettings()))


def test_acquire_straightens_and_crops():
    from foliant.modules.convert.imaging import estimate_skew
    page = _page(100, "x").rotate(3, fillcolor="white", expand=False)
    framed = Image.new("RGB", (page.width + 80, page.height + 80), "black")
    framed.paste(page, (40, 40))
    res = acquire(FakeScanner([framed]), "sim:0", ScanSettings(dpi=100, mode="gray"), straighten=True, crop=True)
    out = res.pages[0]
    assert out.width <= page.width and abs(estimate_skew(out)) <= 0.4
