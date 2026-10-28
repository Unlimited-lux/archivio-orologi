"""Rilettura su pagina ruotata con /CropBox più grande del /MediaBox (coordinate standard del nucleo)."""

import pymupdf
import pytest

from foliant.api import placement
from foliant.modules.annotations import engine


def _raw_rect(doc, page, xref):
    vals = [float(v) for v in doc.xref_get_key(xref, "Rect")[1].strip("[]").split()]
    a = pymupdf.Point(vals[0], vals[1]) * placement.pdf_to_page_matrix(page)
    b = pymupdf.Point(vals[2], vals[3]) * placement.pdf_to_page_matrix(page)
    return pymupdf.Rect(a, b).normalize()


@pytest.mark.parametrize("rot", [0, 90, 270])
def test_reread_rotated_crop_larger_than_media(rot):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    doc.xref_set_key(page.xref, "CropBox", "[-30 -40 650 900]")
    page = doc.reload_page(page)
    page.set_rotation(rot)
    st = engine.Style(width=1)
    # Rect scritti dal modulo (adesivo, evidenziazione ad area) e punti convertiti (XFDF)
    sticker = engine.create_sticker(doc, 0, (200, 300), "stella", st, "A")
    area = engine.create_markup(doc, 0, engine.HIGHLIGHT, [pymupdf.Rect(100, 400, 300, 450)], st, "A", "", True)
    data = doc.tobytes()
    re = pymupdf.open(stream=data, filetype="pdf")
    p = re[0]
    r = _raw_rect(re, p, sticker)
    assert abs((r.x0 + r.x1) / 2 - 200) < 0.6 and abs((r.y0 + r.y1) / 2 - 300) < 0.6
    a = _raw_rect(re, p, area)
    assert abs(a.x0 - 100) < 0.6 and abs(a.y1 - 450) < 0.6
    # conversione andata e ritorno
    for pt in ((0, 0), (123.4, 567.8)):
        back = engine.from_pdf_point(p, *engine.to_pdf_point(p, *pt))
        assert abs(back[0] - pt[0]) < 1e-3 and abs(back[1] - pt[1]) < 1e-3
