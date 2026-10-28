"""Test della traduzione di un documento in un nuovo PDF (AI-07)."""

from __future__ import annotations

import hashlib

import pymupdf
import pytest

from foliant.core.errors import OperationCancelled
from foliant.modules.ai.translate_pdf import (
    MODE_BILINGUAL,
    MODE_LAYOUT,
    MODE_TEXT,
    TranslationReport,
    extract_blocks,
    translate_document,
)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _png(color) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def _make_source(pages: int = 3, images: int = 0) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Titolo pagina {i + 1}", fontsize=20)
        page.insert_textbox(
            pymupdf.Rect(72, 150, 520, 260),
            f"Il contratto numero {i + 1} riguarda la vendita di un orologio da tasca.",
            fontsize=12,
        )
        if i < images:
            page.insert_image(pymupdf.Rect(72, 400, 200, 528), stream=_png((200, 30 * i, 40)))
    data = doc.tobytes()
    doc.close()
    return data


def _upper(page_index, texts):
    return [t.upper() for t in texts]


def _page_texts(data: bytes) -> list[str]:
    doc = pymupdf.open("pdf", data)
    try:
        return [_norm(page.get_text()) for page in doc]
    finally:
        doc.close()


def _expected_texts(data: bytes) -> list[str]:
    doc = pymupdf.open("pdf", data)
    try:
        return [_norm(" ".join(b.text.upper() for b in extract_blocks(doc, i))) for i in range(doc.page_count)]
    finally:
        doc.close()


def _image_count(data: bytes) -> int:
    doc = pymupdf.open("pdf", data)
    try:
        return sum(len(page.get_images(full=True)) for page in doc)
    finally:
        doc.close()


class _Progress:
    def __init__(self, cancel_at: int | None = None):
        self.calls = []
        self.cancel_at = cancel_at

    def update(self, fraction=None, text=None):
        self.calls.append((fraction, text))
        if self.cancel_at is not None and len(self.calls) > self.cancel_at:
            raise OperationCancelled()


def test_extract_blocks_style():
    src = _make_source(1)
    doc = pymupdf.open("pdf", src)
    blocks = extract_blocks(doc, 0)
    doc.close()
    assert [b.index for b in blocks] == [0, 1]
    assert blocks[0].text == "Titolo pagina 1"
    assert blocks[0].fontsize == pytest.approx(20, abs=0.1)
    assert "orologio da tasca" in blocks[1].text
    assert blocks[1].color == (0.0, 0.0, 0.0)


def test_layout_keeps_pages_images_and_source():
    src = _make_source(3, images=2)
    digest = hashlib.sha256(src).hexdigest()
    progress = _Progress()
    out, report = translate_document(src, None, MODE_LAYOUT, _upper, progress, target_language="en")
    assert hashlib.sha256(src).hexdigest() == digest
    assert _page_texts(out) == _expected_texts(src)
    assert _image_count(out) == 2
    doc = pymupdf.open("pdf", out)
    assert doc.page_count == 3
    assert doc.language in ("en", None) or doc.language.startswith("en")
    for page in doc:
        assert "contratto" not in page.get_text()
        assert "CONTRATTO" in page.get_text()
    doc.close()
    assert report.translated_pages == [0, 1, 2]
    assert report.failed_pages == {}
    assert any(text == "Pagina 2 di 3" for _f, text in progress.calls)


def test_layout_page_range():
    src = _make_source(3)
    out, report = translate_document(src, [2], MODE_LAYOUT, _upper)
    texts = _page_texts(out)
    assert len(texts) == 1 and "TITOLO PAGINA 3" in texts[0]
    assert report.translated_pages == [2]


def test_bilingual_order():
    src = _make_source(2)
    out, _report = translate_document(src, None, MODE_BILINGUAL, _upper)
    texts = _page_texts(out)
    assert len(texts) == 4
    assert "Titolo pagina 1" in texts[0]
    assert "TITOLO PAGINA 1" in texts[1] and "Titolo" not in texts[1]
    assert "Titolo pagina 2" in texts[2]
    assert "TITOLO PAGINA 2" in texts[3] and "Titolo" not in texts[3]


def test_failed_page_and_retry():
    src = _make_source(3)

    def failing(page_index, texts):
        if page_index == 1:
            raise RuntimeError("servizio non disponibile")
        return _upper(page_index, texts)

    out, report = translate_document(src, None, MODE_LAYOUT, failing)
    assert list(report.failed_pages) == [1]
    assert "servizio non disponibile" in report.failed_pages[1]
    assert report.translated_pages == [0, 2]
    texts = _page_texts(out)
    assert "Titolo pagina 2" in texts[1]
    text_report = report.to_text()
    assert "non riuscite" in text_report and "2" in text_report

    calls = []

    def recording(page_index, texts):
        calls.append(page_index)
        return _upper(page_index, texts)

    out2, report2 = translate_document(
        src, None, MODE_LAYOUT, recording, retry_pages=[1], previous=out
    )
    assert calls == [1]
    assert report2.failed_pages == {}
    assert report2.translated_pages == [0, 1, 2]
    assert _page_texts(out2) == _expected_texts(src)


@pytest.mark.parametrize("mode", [MODE_BILINGUAL, MODE_TEXT])
def test_retry_other_modes(mode):
    src = _make_source(3)

    def failing(page_index, texts):
        if page_index == 1:
            raise RuntimeError("errore")
        return _upper(page_index, texts)

    out, report = translate_document(src, None, mode, failing)
    assert list(report.failed_pages) == [1]
    calls = []

    def recording(page_index, texts):
        calls.append(page_index)
        return _upper(page_index, texts)

    out2, report2 = translate_document(src, None, mode, recording, retry_pages=[1], previous=out)
    assert calls == [1]
    assert report2.failed_pages == {}
    full, _r = translate_document(src, None, mode, _upper)
    assert _page_texts(out2) == _page_texts(full)


def test_cancel_propagates():
    src = _make_source(3)
    with pytest.raises(OperationCancelled):
        translate_document(src, None, MODE_LAYOUT, _upper, _Progress(cancel_at=1))

    def cancelling(page_index, texts):
        raise OperationCancelled()

    with pytest.raises(OperationCancelled):
        translate_document(src, None, MODE_LAYOUT, cancelling)


def test_long_translation_is_complete_and_reported():
    src = _make_source(1)

    def doubled(page_index, texts):
        return [f"{t.upper()} {t.upper()}" for t in texts]

    out, report = translate_document(src, None, MODE_LAYOUT, doubled)
    doc = pymupdf.open("pdf", src)
    blocks = extract_blocks(doc, 0)
    doc.close()
    text = _page_texts(out)[0]
    for block in blocks:
        assert _norm(f"{block.text.upper()} {block.text.upper()}") in text
    flagged = set(report.shrunk_blocks) | set(report.minimum_size_blocks)
    assert (0, 1) in flagged or (0, 0) in flagged


def test_very_long_translation_minimum_size():
    src = _make_source(1)
    long_text = " ".join(["PAROLA"] * 400)

    def huge(page_index, texts):
        return [texts[0].upper(), long_text]

    out, report = translate_document(src, None, MODE_LAYOUT, huge, min_fontsize=8)
    assert (0, 1) in report.minimum_size_blocks
    assert _norm(long_text) in _page_texts(out)[0]
    assert "corpo minimo" in report.to_text()


def test_text_mode():
    src = _make_source(2)
    out, report = translate_document(src, None, MODE_TEXT, _upper)
    doc = pymupdf.open("pdf", out)
    assert doc.page_count == 2
    assert doc[0].rect.width == pytest.approx(595, abs=1)
    first = _norm(doc[0].get_text())
    doc.close()
    assert first.startswith("Pagina 1")
    assert "TITOLO PAGINA 1" in first and "OROLOGIO DA TASCA" in first
    assert report.translated_pages == [0, 1]


def test_page_without_text_untranslated():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 150, 150), stream=_png((0, 0, 255)))
    src = doc.tobytes()
    doc.close()
    calls = []

    def recording(page_index, texts):
        calls.append(page_index)
        return texts

    out, report = translate_document(src, None, MODE_LAYOUT, recording)
    assert calls == []
    assert report.untranslated_pages == [0]
    assert _image_count(out) == 1


def test_wrong_block_count_is_page_failure():
    src = _make_source(2)
    out, report = translate_document(src, None, MODE_LAYOUT, lambda p, t: ["x"] if p == 0 else _upper(p, t))
    assert list(report.failed_pages) == [0]
    assert report.translated_pages == [1]


def test_report_text():
    report = TranslationReport(
        translated_pages=[0, 2],
        failed_pages={1: "timeout"},
        shrunk_blocks=[(0, 3)],
        minimum_size_blocks=[],
        untranslated_pages=[3],
    )
    text = report.to_text()
    assert "Pagine tradotte: 2" in text
    assert "Pagina 2: timeout" in text
    assert "pagina 1, blocco 4" in text
    assert "4" in text


def test_layout_keeps_links_and_annotations():
    doc = pymupdf.open("pdf", _make_source(1))
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 80, 250, 105), "uri": "https://example.org"})
    page.add_text_annot((300, 90), "nota")
    page.draw_rect(pymupdf.Rect(60, 140, 530, 280), color=(1, 0, 0))
    src = doc.tobytes()
    doc.close()
    out, _report = translate_document(src, None, MODE_LAYOUT, _upper)
    result = pymupdf.open("pdf", out)
    page = result[0]
    assert [link["uri"] for link in page.get_links()] == ["https://example.org"]
    assert [a.type[1] for a in page.annots()] == ["Text"]
    assert len(page.get_drawings()) >= 1
    result.close()
