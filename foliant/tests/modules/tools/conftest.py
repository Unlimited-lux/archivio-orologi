"""Fixture del modulo Strumenti: PDF con differenze note."""

from __future__ import annotations

import pymupdf
import pytest

MM = 72.0 / 25.4


def build_pdf(path, pages: list, size=(595, 842), extra=None) -> str:
    """Ogni elemento di `pages` è una lista di righe (testo) o una funzione(page)."""
    doc = pymupdf.open()
    for i, content in enumerate(pages):
        page = doc.new_page(width=size[0], height=size[1])
        if callable(content):
            content(page)
        else:
            y = 90
            for line in content:
                page.insert_text((72, y), line, fontsize=12)
                y += 20
        if extra:
            extra(page, i)
    doc.save(str(path))
    doc.close()
    return str(path)


def read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def page_lines(n: int, words: int = 8) -> list[str]:
    return [" ".join(f"parola{n}x{k}y{j}" for j in range(words)) for k in range(6)]


@pytest.fixture
def pdf_builder(tmp_path):
    counter = {"n": 0}

    def make(pages, **kw):
        counter["n"] += 1
        return build_pdf(tmp_path / f"doc{counter['n']}.pdf", pages, **kw)
    return make


@pytest.fixture
def fake_engines(monkeypatch):
    """Sostituisce OCR e conversione degli altri moduli con versioni simulate."""
    from foliant.modules.tools import engine

    calls = {"ocr": [], "convert": []}

    def ocr_pdf_bytes(data, languages, mode, pages=None, progress=None):
        calls["ocr"].append((languages, mode))
        doc = pymupdf.open(stream=data, filetype="pdf")
        for page in doc:
            page.insert_text((10, 20), "OCR", fontsize=8)
        out = doc.tobytes()
        doc.close()
        return out

    def convert_pdf(input_path, output_path, fmt, progress=None, **options):
        calls["convert"].append((fmt, options))
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"convertito in {fmt}")
        return output_path

    monkeypatch.setattr(engine, "_ocr_function", lambda: ocr_pdf_bytes)
    monkeypatch.setattr(engine, "_convert_function", lambda: convert_pdf)
    return calls
