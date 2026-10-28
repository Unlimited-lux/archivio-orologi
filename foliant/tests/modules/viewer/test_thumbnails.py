"""VIS-14 — Pannello miniature."""

from __future__ import annotations

import os
import tempfile

import pymupdf
from PySide6.QtCore import QPointF, Qt


def _panel(window):
    return window.ctx.viewer_parts["thumbnails"].panel


def _open(window, path):
    view = window.open_path(path)
    window.ctx.show_sidebar_panel("thumbnails")
    panel = _panel(window)
    panel.flush()
    return view, panel


def test_only_visible_thumbnails_are_rendered(viewer_window, long_pdf):
    view, panel = _open(viewer_window, long_pdf)
    assert panel.model.rowCount() == 30
    assert 0 < len(panel._cache) < 30
    assert panel.image(0) is not None


def test_click_goes_to_page_and_highlights(viewer_window, long_pdf, qtbot):
    view, panel = _open(viewer_window, long_pdf)
    index = panel.model.index(24)
    panel.list.scrollTo(index)
    panel.flush()
    rect = panel.list.visualRect(index)
    qtbot.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert view.current_page == 24
    assert panel.current_page == 24
    assert panel.list.currentIndex().row() == 24
    # salto registrato in cronologia
    nav = viewer_window.ctx.viewer_parts["navigation"]
    assert nav.state(view).history.can_back


def test_current_page_follows_view(viewer_window, long_pdf):
    view, panel = _open(viewer_window, long_pdf)
    view.go_to_page(7)
    assert panel.current_page == 7
    assert panel.list.currentIndex().row() == 7


def test_drag_visible_area_moves_view(viewer_window, long_pdf, qtbot):
    view, panel = _open(viewer_window, long_pdf)
    view.set_zoom(3.0)
    view.go_to_page(0)
    panel.flush()
    row = 0
    cell = panel.list.visualRect(panel.model.index(row))
    r = panel.delegate.image_rect(cell, row)
    vis = panel.visible_area(row, r)
    assert vis is not None and vis.width() < r.width()
    start = vis.center()
    end = start + QPointF(0, r.height() * 0.4)
    v0 = view.verticalScrollBar().value()
    qtbot.mousePress(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=start.toPoint())
    qtbot.mouseMove(panel.list.viewport(), pos=end.toPoint())
    qtbot.mouseRelease(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=end.toPoint())
    moved_pt = view.page_size(0)[1] * 0.4 * view.zoom * 96 / 72
    assert abs((view.verticalScrollBar().value() - v0) - moved_pt) < moved_pt * 0.1
    assert view.current_page == 0


def test_updates_after_modification_and_undo(viewer_window, long_pdf):
    view, panel = _open(viewer_window, long_pdf)
    with view.document.change("Elimina") as pdf:
        pdf.delete_page(0)
    assert panel.model.rowCount() == 29
    view.document.undo()
    assert panel.model.rowCount() == 30
    panel.flush()
    assert panel.image(0) is not None


def test_size_changes(viewer_window, long_pdf):
    view, panel = _open(viewer_window, long_pdf)
    old = panel.delegate.sizeHint(None, panel.model.index(0))
    panel.set_thumb_size(200)
    assert panel.delegate.sizeHint(None, panel.model.index(0)).width() > old.width()
    assert int(viewer_window.settings.value("viewer/thumb_size")) == 200


def test_mixed_sizes_keep_proportions(viewer_window, tmp_path):
    path = tmp_path / "misto.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=1191, height=842)
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    r0 = panel.delegate.image_rect(panel.list.visualRect(panel.model.index(0)), 0)
    r1 = panel.delegate.image_rect(panel.list.visualRect(panel.model.index(1)), 1)
    assert r0.height() > r0.width()
    assert r1.width() > r1.height()


def test_encrypted_thumbnails_stay_in_memory(viewer_window, encrypted_pdf, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("segreta", True))
    before = set(os.listdir(tempfile.gettempdir()))
    view, panel = _open(viewer_window, encrypted_pdf)
    assert panel.image(0) is not None
    after = set(os.listdir(tempfile.gettempdir()))
    assert not [n for n in after - before if "thumb" in n.lower() or n.endswith((".png", ".jpg"))]


def test_unreadable_page_placeholder(viewer_window, long_pdf, monkeypatch):
    view, panel = _open(viewer_window, long_pdf)
    panel._cache.clear()
    real = pymupdf.Page.get_pixmap

    def broken(self, *a, **k):
        if self.number == 1:
            raise RuntimeError("pagina danneggiata")
        return real(self, *a, **k)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", broken)
    panel.flush()
    assert 1 in panel.failed
    assert panel.image(0) is not None
