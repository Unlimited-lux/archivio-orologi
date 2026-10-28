"""Collaudo 4: richiamo con /CropBox, FDF con risposte, casella di testo esportata, note su pagine ruotate,
ordinamento per pagina su pagine ruotate."""

import pymupdf
import pytest

from foliant.modules.annotations import engine


def _cl(pdf, xref):
    return engine._array(pdf, xref, "CL")


@pytest.mark.parametrize("rotation", [0, 90])
def test_callout_with_cropbox(rotation):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_cropbox(pymupdf.Rect(40, 50, 520, 780))
    page.set_rotation(rotation)
    xref = engine.create_freetext(pdf, 0, (220, 300, 380, 340), "Vedi qui", engine.Style(), "A",
                                  callout=[(100, 400), (200, 350)])
    page = pdf[0]
    cl = _cl(pdf, xref)
    with engine.unrotated(page):
        tip = engine.to_pdf_point(page, 100, 400)
        knee = engine.to_pdf_point(page, 200, 350)
    assert cl[:2] == pytest.approx(tip, abs=0.05)
    assert cl[2:4] == pytest.approx(knee, abs=0.05)
    if rotation == 0:
        assert cl[:2] == pytest.approx((140, 392), abs=0.05)
    pts = engine.vertex_points(pdf, 0, xref)
    assert (pts[0].x, pts[0].y) == pytest.approx((100, 400), abs=0.05)
    # il /Rect contiene punta e casella
    with engine.unrotated(page):
        r = pymupdf.Rect(engine.from_pdf_rect(page, engine._array(pdf, xref, "Rect")))
    assert r.contains(pymupdf.Point(100, 400)) and r.contains(pymupdf.Rect(220, 300, 380, 340))


def _roundtrip(pdf, fmt):
    """Esporta (XFDF o FDF), elimina tutto e reimporta."""
    from foliant.modules.annotations import ui_actions
    if fmt == "xfdf":
        data = engine.export_xfdf(pdf)
    else:
        data = ui_actions.fdf_to_xfdf(engine.export_fdf(pdf))
    engine.delete_all(pdf)
    assert engine.list_annotations(pdf) == []
    engine.import_xfdf(pdf, data)


@pytest.mark.parametrize("fmt", ["xfdf", "fdf"])
def test_callout_with_cropbox_roundtrip(fmt):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_cropbox(pymupdf.Rect(40, 50, 520, 780))
    engine.create_freetext(pdf, 0, (220, 300, 380, 340), "Vedi qui", engine.Style(), "A",
                           callout=[(100, 400), (200, 350)])
    _roundtrip(pdf, fmt)
    a, = engine.list_annotations(pdf)
    pts = engine.vertex_points(pdf, 0, a.xref)
    assert (pts[0].x, pts[0].y) == pytest.approx((100, 400), abs=0.05)
    assert engine._array(pdf, a.xref, "CL")[:2] == pytest.approx((140, 392), abs=0.05)

