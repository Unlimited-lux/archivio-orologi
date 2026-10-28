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
