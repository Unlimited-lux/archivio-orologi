"""VIS-39 — Coordinate, griglia e riquadri di pagina."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF


def _ctl(w):
    return w.ctx.viewer_parts["measure"]


def test_a4_corner_coordinates_mm(viewer_window, tmp_path):
    path = tmp_path / "a4.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595.28, height=841.89)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    view.set_zoom(0.5)
    ctl = _ctl(viewer_window)
    ctl.set_unit("mm")
    r = view.page_rect(0)
    c = ctl.coordinates(view, QPointF(r.right() - 0.01, r.bottom() - 0.01))
    assert c is not None
    _p, x, y, w, h = c
    assert abs(x - 210.0) <= 0.5 and abs(y - 297.0) <= 0.5
    assert abs(w - 210.0) <= 0.5 and abs(h - 297.0) <= 0.5
    assert "210,0" in ctl.describe(view, QPointF(r.right() - 0.01, r.bottom() - 0.01))


def test_user_unit_scales(viewer_window, tmp_path):
    path = tmp_path / "uu.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=72, height=72)
    doc.xref_set_key(page.xref, "UserUnit", "2")
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    ctl.set_unit("in")
    _p, _x, _y, w, _h = ctl.coordinates(view, view.page_rect(0).center())
    assert abs(w - 2.0) < 0.01


def test_page_boxes_and_grid_not_in_document(viewer_window, tmp_path):
    path = tmp_path / "box.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.set_trimbox(pymupdf.Rect(36, 36, 559, 806))
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    boxes = ctl.page_boxes(view, 0)
    names = [b[0] for b in boxes]
    assert "MediaBox" in names and "TrimBox" in names
    colors = {b[0]: b[2].name() for b in boxes}
    assert colors["MediaBox"] != colors["TrimBox"]
    ctl.set_boxes(True)
    ctl.set_grid(True)
    view.flush_rendering()
    view.viewport().grab()
    assert not view.document.is_modified
    ctl.set_boxes(False)
    ctl.set_grid(False)


# ------------------------------------------------------------- geospaziale
def _geo_pdf(path, five_points=False):
    """Pagina A4 con una mappa nel riquadro [100 200 500 700] (spazio PDF) georeferenziata:
    angoli e, a scelta, un punto di controllo interno."""
    doc = pymupdf.open()
    page = doc.new_page(width=595.28, height=841.89)
    page.draw_rect(pymupdf.Rect(100, 841.89 - 700, 500, 841.89 - 200), color=(0, 0, 1))
    lpts = [0, 0, 0, 1, 1, 1, 1, 0]
    gpts = [45.400000, 9.100000, 45.500000, 9.100000, 45.500000, 9.250000, 45.400000, 9.250000]
    if five_points:
        lpts += [0.25, 0.75]
        gpts += [45.475000, 9.137500]
    meas = doc.get_new_xref()
    doc.update_object(meas, "<< /Type /Measure /Subtype /GEO /GCS << /Type /GEOGCS /EPSG 4326 >> "
                      f"/GPTS [{' '.join(f'{x:.6f}' for x in gpts)}] /LPTS [{' '.join(str(x) for x in lpts)}] >>")
    doc.xref_set_key(page.xref, "VP", f"[<< /Type /Viewport /BBox [100 200 500 700] /Name (Mappa) "
                                       f"/Measure {meas} 0 R >>]")
    doc.save(str(path))
    return str(path)


def test_geospatial_coordinates_at_control_points(viewer_window, tmp_path):
    """Dato il PDF geospaziale di prova, quando il cursore è su un punto di controllo noto,
    le coordinate mostrate coincidono con quelle attese entro 0,00001°."""
    from foliant.modules.viewer.geo import geo_viewports, latlon_at
    view = viewer_window.open_path(_geo_pdf(tmp_path / "mappa.pdf", five_points=True))
    view.set_zoom(1.0)
    ctl = _ctl(viewer_window)
    pdf = view.document.pdf
    [vp] = geo_viewports(pdf, 0)
    tm = pdf[0].transformation_matrix
    for (u, v), (lat, lon) in (((0, 0), (45.4, 9.1)), ((1, 1), (45.5, 9.25)), ((0.25, 0.75), (45.475, 9.1375))):
        pdf_pt = pymupdf.Point(100 + 400 * u, 200 + 500 * v)
        page_pt = pdf_pt * tm
        got = latlon_at(pdf, 0, page_pt)
        assert got is not None
        assert abs(got[0] - lat) < 1e-5 and abs(got[1] - lon) < 1e-5
        # dal cursore sullo schermo alla barra delle coordinate
        pos = view.page_transform(0).map(QPointF(page_pt.x, page_pt.y))
        lat_c, lon_c = ctl.latlon(view, pos)
        assert abs(lat_c - lat) < 1e-3 and abs(lon_c - lon) < 1e-3     # ±1 px a zoom 100%
    text = ctl.describe(view, view.page_transform(0).map(QPointF(*(pymupdf.Point(300, 450) * tm))))
    assert "° N" in text and "° E" in text
    # fuori dalla mappa nessuna coordinata geografica
    outside = view.page_transform(0).map(QPointF(*(pymupdf.Point(50, 50) * tm)))
    assert ctl.latlon(view, outside) is None and "°" not in ctl.describe(view, outside)


def test_geo_transform_exact_with_four_corners():
    from foliant.modules.viewer.geo import _apply, fit_transform
    local = [(0, 0), (0, 1), (1, 1), (1, 0)]
    geo = [(10.0, 20.0), (10.5, 20.1), (10.6, 20.9), (10.1, 20.8)]      # non affine: proiettiva
    c = fit_transform(local, geo)
    for (u, v), (lat, lon) in zip(local, geo):
        a, b = _apply(c, u, v)
        assert abs(a - lat) < 1e-9 and abs(b - lon) < 1e-9
    assert fit_transform(local[:2], geo[:2]) is None
