"""VIS-10 (Zoom area) e VIS-29 (Istantanea)."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage


def _ctl(window):
    return window.ctx.viewer_parts["tools"]


def _drag(qtbot, view, page, a, b):
    view.flush_rendering()
    t = view.page_transform(page)
    pa = t.map(QPointF(*a)).toPoint()
    pb = t.map(QPointF(*b)).toPoint()
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=pa)
    qtbot.mouseMove(view.viewport(), pos=pb)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=pb)


def test_zoom_area_fills_view(viewer_window, sample_pdf, qtbot):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    ctl.act_zoom.trigger()
    assert view.tool is ctl.zoom_tool
    _drag(qtbot, view, 0, (100, 200), (300, 300))
    r = view.page_to_view(0, pymupdf.Rect(100, 200, 300, 300))
    vw, vh = view.viewport().width(), view.viewport().height()
    assert abs(r.width() - vw) <= 4 or abs(r.height() - vh) <= 4
    assert r.width() <= vw + 4 and r.height() <= vh + 4
    assert abs(r.center().x() - vw / 2) < 6 and abs(r.center().y() - vh / 2) < 6
    nav = viewer_window.ctx.viewer_parts["navigation"]
    assert nav.state(view).history.can_back


def test_zoom_area_click_zooms_one_step(viewer_window, sample_pdf, qtbot):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    view.set_zoom(1.0)
    ctl.act_zoom.trigger()
    _drag(qtbot, view, 0, (100, 200), (100, 200))
    assert view.zoom > 1.0


def test_snapshot_image_resolution(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    img = ctl.copy_image(view, 0, pymupdf.Rect(72, 72, 72 + 144, 72 + 72), dpi=300)  # 2 × 1 pollici
    assert (img.width(), img.height()) == (600, 300)
    clip = QGuiApplication.clipboard().image()
    assert (clip.width(), clip.height()) == (600, 300)


def test_snapshot_text_and_menu(viewer_window, sample_pdf, qtbot, monkeypatch):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    chosen = []

    def fake_exec(menu, pos):
        acts = [a for a in menu.actions() if a.text() == "Copia testo"]
        chosen.append([a.text() for a in menu.actions()])
        acts[0].trigger()

    monkeypatch.setattr(view, "exec_menu", fake_exec)
    ctl.act_snapshot.trigger()
    assert view.tool is ctl.snapshot_tool
    _drag(qtbot, view, 0, (60, 120), (500, 150))
    assert chosen and "Copia immagine" in chosen[0]
    assert "Orologeria Verdi" in QGuiApplication.clipboard().text()
    assert "Pagina 1" not in QGuiApplication.clipboard().text()


def test_snapshot_save(viewer_window, sample_pdf, tmp_path):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    out = ctl.save_image(view, 0, pymupdf.Rect(0, 0, 72, 72), path=str(tmp_path / "a.png"), dpi=144)
    img = QImage(out)
    assert (img.width(), img.height()) == (144, 144)


def test_snapshot_disabled_without_copy_permission(viewer_window, tmp_path):
    path = tmp_path / "nocopia.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    ctl.act_snapshot.trigger()
    assert view.tool is not ctl.snapshot_tool
    assert "non consente" in ctl.act_snapshot.toolTip()


def test_escape_returns_to_selection(viewer_window, sample_pdf, qtbot):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    ctl.act_zoom.trigger()
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert view.tool is view._default_tool
    assert not ctl.act_zoom.isChecked()


def test_copy_permission_survives_edit(viewer_window, tmp_path):
    path = tmp_path / "nocopia2.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_MODIFY)
    view = viewer_window.open_path(str(path))
    with view.document.change("Modifica") as pdf:
        pdf[0].insert_text((72, 200), "altro")
    from foliant.modules.viewer.tools import copy_allowed
    assert not copy_allowed(view)
