"""PDF di prova per la modifica del testo."""

from __future__ import annotations

import pymupdf
import pytest

from tests.conftest import system_font

SERIF = system_font("truetype/liberation/LiberationSerif-Regular.ttf")
SANS = system_font("truetype/liberation/LiberationSans-Regular.ttf")
DEJAVU = system_font("truetype/dejavu/DejaVuSans.ttf")

LOREM = [
    "Lorem ipsum dolor sit amet, consectetur",
    "adipiscing elit, sed do eiusmod tempor",
    "incididunt ut labore et dolore magna",
    "aliqua. Ut enim ad minim veniam.",
]


def new_doc_with_font(fontfile: str = SERIF, name: str = "F1"):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname=name, fontfile=fontfile)
    return doc, page


def write_lines(page, lines, x=72, y=100, size=11, leading=14, fontname="F1", color=(0, 0, 0)):
    for line in lines:
        page.insert_text((x, y), line, fontname=fontname, fontsize=size, color=color)
        y += leading
    return y


def save(doc, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def paragraphs_pdf(tmp_path):
    """3 paragrafi di 4 righe in una colonna (font incorporato completo)."""
    doc, page = new_doc_with_font()
    y = 100
    for _k in range(3):
        y = write_lines(page, LOREM, y=y) + 14
    page.insert_text((72, 400), "Il contratto scade nel 2024", fontname="F1", fontsize=12, color=(0.75, 0, 0))
    return save(doc, tmp_path / "paragrafi.pdf")


@pytest.fixture
def subset_pdf(tmp_path):
    """Frase in un font TrueType incorporato come sottoinsieme (contiene le cifre necessarie)."""
    doc, page = new_doc_with_font(SANS)
    page.insert_text((72, 100), "Il contratto scade nel 2024 o 2026", fontname="F1", fontsize=12, color=(0, 0, 0.5))
    doc.subset_fonts()
    return save(doc, tmp_path / "sottoinsieme.pdf")


@pytest.fixture
def helvetica_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Benvenuti nella nostra sede", fontname="helv", fontsize=12)
    return save(doc, tmp_path / "helvetica.pdf")


@pytest.fixture
def two_columns_pdf(tmp_path):
    doc, page = new_doc_with_font()
    write_lines(page, LOREM, x=50, y=100, size=10, leading=12)
    write_lines(page, LOREM, x=320, y=100, size=10, leading=12)
    return save(doc, tmp_path / "colonne.pdf")


@pytest.fixture
def scanned_pdf(tmp_path):
    """Pagina composta da un'unica immagine."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.clear_with(230)
    page.insert_image(pymupdf.Rect(50, 50, 545, 300), pixmap=pix)
    return save(doc, tmp_path / "scansione.pdf")


def render_text_image(text: str, size=(900, 200), fontsize=48):
    """Immagine (PNG) con testo nero su bianco, simile a una scansione."""
    d = pymupdf.open()
    p = d.new_page(width=size[0] * 0.75, height=size[1] * 0.75)
    p.insert_text((20, size[1] * 0.75 * 0.6), text, fontsize=fontsize * 0.75, fontname="helv")
    png = p.get_pixmap(dpi=96).tobytes("png")
    d.close()
    return png


@pytest.fixture
def ocr_pdf(tmp_path):
    """Scansione con livello OCR invisibile che contiene «Rossl» al posto di «Rossi»."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    png = render_text_image("Mario Rossi 2019", size=(900, 200), fontsize=48)
    rect = pymupdf.Rect(50, 50, 50 + 675, 50 + 150)
    page.insert_image(rect, stream=png)
    # parole invisibili allineate (stima simile a quella di un OCR)
    y = 50 + 150 * 0.6
    x = 50 + 15
    for word in ["Mario", "Rossl", "2019"]:
        w = pymupdf.get_text_length(word, fontname="helv", fontsize=36)
        page.insert_text((x, y), word, fontname="helv", fontsize=36, render_mode=3)
        x += w + pymupdf.get_text_length(" ", fontname="helv", fontsize=36)
    return save(doc, tmp_path / "ocr.pdf")


def spans(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["text"].strip():
                    out.append(s)
    return out


def span_with(page, text):
    for s in spans(page):
        if text in s["text"]:
            return s
    return None
