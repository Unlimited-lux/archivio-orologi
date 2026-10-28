"""VIS-34 — Temi di lettura e protezione degli occhi."""

from __future__ import annotations

import hashlib

import pymupdf
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from foliant.modules.viewer.appearance import PRESETS, contrast_ratio


def _photo_pdf(tmp_path):
    path = tmp_path / "foto.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo nero di prova", fontsize=28)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (200, 40, 40))
    page.insert_image(pymupdf.Rect(300, 300, 500, 500), pixmap=pix)
    doc.save(str(path))
    return str(path)


def _pixel(view, page, pt):
    view.flush_rendering()
    img = view.viewport().grab().toImage()
    p = view.page_transform(page).map(QPointF(*pt)).toPoint()
    return img.pixelColor(p)


def test_dark_inversion_keeps_photos(viewer_window, tmp_path):
    view = viewer_window.open_path(_photo_pdf(tmp_path))
    ctl = viewer_window.ctx.viewer_parts["appearance"]
    view.set_zoom(1.0)
    view.go_to_page(0)
    photo_before = _pixel(view, 0, (400, 400))
    bg_before = _pixel(view, 0, (500, 150))
    assert bg_before.lightness() > 240
    ctl.set_theme("dark")
    bg_after = _pixel(view, 0, (500, 150))
    assert bg_after.lightness() < 40                 # fondo scuro
    photo_after = _pixel(view, 0, (400, 400))
    assert photo_after.getRgb() == photo_before.getRgb()   # fotografia invariata
    ctl.set_option("keep_images", False)
    assert _pixel(view, 0, (400, 400)).getRgb() != photo_before.getRgb()
    ctl.set_theme("white")


def test_theme_never_touches_file_or_document(viewer_window, tmp_path):
    path = _photo_pdf(tmp_path)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    view = viewer_window.open_path(path)
    ctl = viewer_window.ctx.viewer_parts["appearance"]
    ctl.set_theme("night")
    view.flush_rendering()
    assert not view.document.is_modified
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
    # le esportazioni (istantanea) usano i colori originali
    tools = viewer_window.ctx.viewer_parts["tools"]
    from foliant.modules.viewer.tools import capture_image
    img, _dpi = capture_image(view, 0, pymupdf.Rect(450, 120, 550, 180), 72)
    assert img.pixelColor(10, 10).lightness() > 240
    ctl.set_theme("white")


def test_presets_contrast():
    for key, (_label, text, bg) in PRESETS.items():
        if text is not None:
            assert contrast_ratio(text, bg) >= 4.5, key


def test_custom_low_contrast_warns(viewer_window, sample_pdf, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = viewer_window.ctx.viewer_parts["appearance"]
    msgs = []
    monkeypatch.setattr(viewer_window.ctx, "status", lambda m, t=5000: msgs.append(m))
    assert ctl.set_custom((120, 120, 120), (140, 140, 140)) < 4.5
    assert msgs and "Contrasto" in msgs[0]
    assert ctl.theme == "custom"
    ctl.set_theme("white")


def test_setting_persisted(viewer_window):
    ctl = viewer_window.ctx.viewer_parts["appearance"]
    ctl.set_theme("paper")
    assert viewer_window.settings.value("viewer/reading_theme") == "paper"
    ctl.set_theme("white")
