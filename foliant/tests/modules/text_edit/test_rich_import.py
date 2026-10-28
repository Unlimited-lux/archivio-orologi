"""TXT-19: inserimento di testo formattato da RTF, HTML e testo semplice."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QMimeData, QPointF, QUrl

from foliant.api import Document
from foliant.modules.text_edit import engine, rich_import
from foliant.modules.text_edit.model import Style

from .conftest import save
from .test_ui import open_in_edit_mode, quiet  # noqa: F401

RTF = (rb"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Arial;}{\f1 Times New Roman;}}"
       rb"{\colortbl;\red255\green0\blue0;}"
       rb"\f0\fs28\b\cf1 Riga in grassetto rossa\b0\cf0\par"
       rb"\f1\fs22\i Riga in corsivo\i0\par"
       rb"\u224?\u232?\u236?\u242?\u249? \u8364? \u223?\par"
       rb"{\pict\pngblip 89504e47}}")


def test_rtf_styles_and_unicode():
    imp = rich_import.from_rtf(RTF, Style())
    texts = ["".join(r.text for r in p) for p in imp.paragraphs]
    assert texts[0] == "Riga in grassetto rossa"
    assert texts[1] == "Riga in corsivo"
    assert texts[2] == "àèìòù € ß"
    first = imp.paragraphs[0][0].style
    assert first.bold and first.size == 14 and first.color == (1.0, 0.0, 0.0) and first.font == "Arial"
    second = imp.paragraphs[1][0].style
    assert second.italic and second.size == 11 and not second.bold
    assert imp.omitted == ["immagine"]


def test_rtf_inserted_in_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    d = Document.open(save(doc, tmp_path / "v.pdf"))
    imp = rich_import.from_rtf(RTF, Style())
    with d.change("Inserisci") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(72, 100), imp.paragraphs, align=imp.align)
    out = tmp_path / "o.pdf"
    d.save(out)
    page = pymupdf.open(str(out))[0]
    text = page.get_text()
    assert "Riga in grassetto rossa" in text and "Riga in corsivo" in text and "àèìòù € ß" in text
    spans = {s["text"]: s for b in page.get_text("dict")["blocks"] for ln in b["lines"] for s in ln["spans"]}
    bold = spans["Riga in grassetto rossa"]
    assert abs(bold["size"] - 14) <= 0.05 and bold["color"] == 0xFF0000 and "bold" in bold["font"].lower()
    ital = spans["Riga in corsivo"]
    assert abs(ital["size"] - 11) <= 0.05
    assert "italic" in ital["font"].lower() or "oblique" in ital["font"].lower()


def test_html_and_plain():
    imp = rich_import.from_html("<p style='text-align:center'><b>Titolo</b> e <span style='color:#00ff00;"
                                "font-size:20pt'>verde</span></p><img src='x.png'>", Style())
    runs = imp.paragraphs[0]
    assert runs[0].text == "Titolo" and runs[0].style.bold
    green = [r for r in runs if r.text == "verde"][0]
    assert green.style.color == (0.0, 1.0, 0.0) and abs(green.style.size - 20) < 0.01
    assert imp.align == "center"
    assert rich_import.decode_text("città".encode("utf-16")) == "città"
    assert rich_import.decode_text("città".encode("cp1252")) == "città"
    assert rich_import.from_plain("a\nb", Style()).text == "a\nb"


def test_drop_rtf_file_on_page(window, sample_pdf, quiet, qtbot, tmp_path):  # noqa: F811
    path = tmp_path / "testo.rtf"
    path.write_bytes(RTF)
    view = open_in_edit_mode(window, sample_pdf)
    ctrl = window.ctx.text_edit
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    ctrl.insert_formatted(view, mime, 0, pymupdf.Point(72, 400))
    text = view.document.page_text(0)
    assert "Riga in grassetto rossa" in text
    assert any("omessi" in m for m in quiet["status"])


def test_paste_text_from_other_app(window, sample_pdf, quiet, qtbot):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    view = open_in_edit_mode(window, sample_pdf)
    QGuiApplication.clipboard().setText("Testo incollato da fuori")
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert "Testo incollato da fuori" in view.document.page_text(0)
