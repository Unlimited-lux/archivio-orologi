"""OCR-19: immagine ricercabile compressa (sfondo ridotto + maschera del testo senza perdita)."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

from foliant.modules.ocr import compress, engine

from .conftest import FRASI, scan_image

EXPECTED = "\n".join(FRASI)


def _jpeg_pdf(images, quality=90):
    doc = pymupdf.open()
    for img in images:
        page = doc.new_page(width=595, height=842)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
        page.insert_image(page.rect, stream=buf.tobytes())
    return doc.tobytes(deflate=True)


def _render(page, dpi=300):
    pix = page.get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()


@pytest.fixture(scope="module")
def yellowed():
    img = scan_image(dpi=300, background=235)
    img[..., 2] = np.minimum(img[..., 2], 200)
    return _jpeg_pdf([img] * 3)


def test_bilanciata_riduce_almeno_60_per_cento(yellowed):
    exact = engine.ocr_pdf_bytes(yellowed, ["ita"], "searchable")
    out = engine.ocr_pdf_bytes(yellowed, ["ita"], "searchable", options=engine.OcrOptions(compression="balanced"))
    assert len(out) <= 0.4 * len(exact), (len(out), len(exact))
    with pymupdf.open(stream=yellowed) as a, pymupdf.open(stream=out) as b:
        acc_a = engine.char_accuracy(engine.ocr_page_text(_render(a[0]), dpi=300, languages=["ita"]), EXPECTED)
        acc_b = engine.char_accuracy(engine.ocr_page_text(_render(b[0]), dpi=300, languages=["ita"]), EXPECTED)
        assert abs(acc_a - acc_b) <= 0.01
        assert "compravendita" in b[0].get_text()
        g = _render(b[0], 72)
        assert g[400, 50, 2] < g[400, 50, 0] - 15            # la carta resta ingiallita


def test_massima_non_scambia_cifre():
    doc = pymupdf.open()
    page = doc.new_page()
    rows = ["0368 8630 3086 6803", "3608 0863 8036 6380", "8806 3360 0683 6608"]
    for i, r in enumerate(rows):
        page.insert_text((72, 120 + i * 30), r, fontsize=14)
    img = _render(page)
    img[..., 2] = np.minimum(img[..., 2], 215)
    data = _jpeg_pdf([img])
    out = engine.ocr_pdf_bytes(data, ["eng"], "searchable", options=engine.OcrOptions(compression="maximum"))
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=out) as b:
        ta = engine.ocr_page_text(_render(a[0]), dpi=300, languages=["eng"])
        tb = engine.ocr_page_text(_render(b[0]), dpi=300, languages=["eng"])
    digits = lambda t: "".join(c for c in t if c.isdigit())  # noqa: E731
    assert digits(ta) == digits(tb) == "".join(rows).replace(" ", "")


def test_fotografia_resta_a_colori():
    rng = np.random.default_rng(3)
    img = scan_image(lines=FRASI[:2], dpi=300)
    photo = cv2.GaussianBlur(rng.integers(0, 255, (600, 900, 3), dtype=np.uint8), (31, 31), 0)
    photo[..., 0] = np.clip(photo[..., 0].astype(int) + 60, 0, 255)
    img[1200:1800, 400:1300] = photo
    data = _jpeg_pdf([img])
    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable", options=engine.OcrOptions(compression="balanced"))
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=out) as b:
        pa, pb = _render(a[0])[1200:1800, 400:1300], _render(b[0])[1200:1800, 400:1300]
    assert pb[..., 0].mean() > pb[..., 2].mean() + 20                     # ancora a colori
    s = compress.ssim(cv2.cvtColor(pa, cv2.COLOR_RGB2GRAY), cv2.cvtColor(pb, cv2.COLOR_RGB2GRAY))
    assert s >= 0.9, s


def test_bianco_e_nero_bitonale_e_esatta_invariata(tmp_path):
    img = scan_image(lines=FRASI[:2], dpi=300)
    assert compress.is_bitonal(img)
    yellow = img.copy()
    yellow[..., 2] = np.minimum(yellow[..., 2], 200)
    assert not compress.is_bitonal(yellow)
    data = _jpeg_pdf([img])
    exact = engine.ocr_pdf_bytes(data, ["ita"], "searchable")
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=exact) as b:
        assert a.xref_stream_raw(a[0].get_images()[0][0]) == b.xref_stream_raw(b[0].get_images()[0][0])
