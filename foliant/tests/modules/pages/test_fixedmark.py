"""Test della filigrana a posizione fissa di stampa (PAG-37)."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.modules.pages import fixedmark
from foliant.modules.pages.engine import PAPER_SIZES, PageOpError

A4 = PAPER_SIZES["A4"]


def doc_with(n=2):
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page(width=A4[0], height=A4[1]).insert_text((72, 400), f"Pagina {i + 1}", fontsize=20)
    return doc


def ink_bbox(page, dpi=72, mask_text=True):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=True)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    mask = arr < 235
    if mask_text:
        mask[300:450, :] = False                     # esclude il testo della pagina
    ys, xs = np.nonzero(mask)
    s = 72 / dpi
    return pymupdf.Rect(xs.min() * s, ys.min() * s, (xs.max() + 1) * s, (ys.max() + 1) * s)


def test_fixedprint_h_v_09_and_scaled_print():
    doc = doc_with(1)
    x = fixedmark.add_fixed_watermark(doc, [0], "COPIA", h=0.9, v=0.9, fontsize=20)[0]
    doc = pymupdf.open("pdf", doc.tobytes())
    marks = fixedmark.fixed_watermarks(doc, 0)
    assert marks[0]["h"] == pytest.approx(0.9) and marks[0]["v"] == pytest.approx(0.9)
    # stampa al 50% verso un file: il foglio resta A4, la pagina è ridotta, la filigrana no
    sheet = A4
    px, py = fixedmark.print_position(marks[0]["h"], marks[0]["v"], sheet)
    assert px == pytest.approx(0.9 * sheet[0]) and py == pytest.approx(0.9 * sheet[1])
    half = (A4[0] / 2, A4[1] / 2)
    px, py = fixedmark.print_position(marks[0]["h"], marks[0]["v"], half)
    assert px / half[0] == pytest.approx(0.9) and py / half[1] == pytest.approx(0.9)
    # posizione nominale (lettori che ignorano /FixedPrint): stessa frazione del supporto
    page = doc[0]
    r = page.first_annot.rect
    assert r.x0 == pytest.approx(0.9 * A4[0], abs=0.5)
    assert A4[1] - r.y1 == pytest.approx(0.9 * A4[1], abs=0.5)    # y del motore dall'alto


def test_saved_annotation_has_fixedprint_and_valid_appearance(tmp_path):
    doc = doc_with(2)
    fixedmark.add_fixed_watermark(doc, [0, 1], "COPIA", anchor="top-right", margin=20, opacity=0.6)
    path = tmp_path / "copia.pdf"
    doc.save(str(path), garbage=3)
    doc = pymupdf.open(str(path))
    for page in doc:
        annots = list(page.annots())
        assert len(annots) == 1 and annots[0].type[1] == "Watermark"
        obj = doc.xref_object(annots[0].xref)
        assert "/FixedPrint" in obj and "/H " in obj and "/V " in obj and "/Matrix" in obj
        assert annots[0].flags & pymupdf.PDF_ANNOT_IS_PRINT
        ap = doc.xref_get_key(annots[0].xref, "AP/N")
        assert ap[0] == "xref"
        ax = int(ap[1].split()[0])
        assert doc.xref_get_key(ax, "Subtype")[1] == "/Form" and doc.xref_get_key(ax, "BBox")[0] == "array"
        assert b"COPIA" in doc.xref_stream(ax)
        # l'aspetto è visibile e sta in alto a destra
        bb = ink_bbox(page)
        assert bb.x1 > A4[0] - 60 and bb.y0 < 60
        assert bb.x1 <= A4[0] - 19 and bb.y0 >= 19


def test_rotation_anchor_remove_and_errors():
    doc = pymupdf.open()
    doc.new_page(width=A4[0], height=A4[1])
    fixedmark.add_fixed_watermark(doc, [0], "RISERVATO", rotation=45, anchor="center", fontsize=40)
    bb = ink_bbox(doc[0], mask_text=False)
    assert abs((bb.y0 + bb.y1) / 2 - A4[1] / 2) < 8
    assert abs((bb.x0 + bb.x1) / 2 - A4[0] / 2) < 8 and bb.height > 100       # diagonale, centrata
    assert fixedmark.remove_fixed_watermarks(doc, [0], only_ours=True) == 1
    assert not list(doc[0].annots())
    with pytest.raises(PageOpError):
        fixedmark.add_fixed_watermark(doc, [0], "   ")
    with pytest.raises(PageOpError):
        fixedmark.add_fixed_watermark(doc, [0], "X", h=1.5, v=0.2)
    with pytest.raises(PageOpError):
        fixedmark.add_fixed_watermark(doc, [0], "TESTO MOLTO LUNGO " * 10, fontsize=60)


def test_flatten_turns_into_content():
    doc = doc_with(1)
    fixedmark.add_fixed_watermark(doc, [0], "COPIA", h=0.5, v=0.5)
    doc.bake(annots=True)
    assert not list(doc[0].annots())
    assert "COPIA" in doc[0].get_text()


def test_rotated_page_anchor_is_displayed_top_right():
    doc = doc_with(1)
    doc[0].set_rotation(90)
    fixedmark.add_fixed_watermark(doc, [0], "COPIA", anchor="top-right", margin=20, fontsize=24)
    pix_page = doc[0]
    pix = pix_page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, annots=True)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    ys, xs = np.nonzero(arr < 235)
    # pagina visualizzata orizzontale: la filigrana è nel quarto in alto a destra
    sel = (xs > pix.width * 0.6) & (ys < pix.height * 0.3)
    assert sel.sum() > 30
