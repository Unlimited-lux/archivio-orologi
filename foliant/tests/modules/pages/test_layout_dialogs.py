"""Test dei dialoghi di geometria pagine (PAG-25…PAG-30)."""

from __future__ import annotations

import os

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from foliant.modules.pages import layout_dialogs as ld
from foliant.modules.pages.engine import MM

SHOTS = "/tmp/claude-0/shots"


# ------------------------------------------------------------------ helpers
@pytest.fixture
def env(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    messages = []
    monkeypatch.setattr(ctx, "info", lambda t, m: messages.append(("info", t, m)), raising=False)
    monkeypatch.setattr(ctx, "error", lambda t, m: messages.append(("error", t, m)), raising=False)
    return ctx, view, messages


def run_dialog(monkeypatch, configure, accept=True):
    seen = {}

    def fake_exec(self):
        seen["dlg"] = self
        configure(self)
        self.refresh()
        return QDialog.DialogCode.Accepted if accept and self.ok.isEnabled() else QDialog.DialogCode.Rejected

    monkeypatch.setattr(ld._LayoutDialog, "exec", fake_exec, raising=False)
    return seen


def raw_box(view, i, key):
    pdf = view.document.pdf
    t, v = pdf.xref_get_key(pdf[i].xref, key)
    return None if t == "null" else [float(x) for x in v.strip("[]").split()]


# ======================================================================== crop
def test_crop_dialog_applies_and_undo(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.unit, "mm")
        d.same.setChecked(True)
        d.top.setValue(10)
        d.scope.set_scope("all")

    run_dialog(monkeypatch, conf)
    assert ld.open_crop_dialog(ctx, view, [])
    for i in range(view.document.page_count):
        box = raw_box(view, i, "CropBox")
        assert box is not None and abs(box[0] - 10 * MM) < 0.1 and abs(box[3] - (842 - 10 * MM)) < 0.1
    assert view.document.undo()
    assert raw_box(view, 0, "CropBox") is None


def test_crop_dialog_even_pages_and_reset(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.unit, "pt")
        d.left.setValue(50)
        d.scope.set_scope("even")

    run_dialog(monkeypatch, conf)
    assert ld.open_crop_dialog(ctx, view, [])
    assert raw_box(view, 0, "CropBox") is None and raw_box(view, 1, "CropBox")[0] == 50

    def conf_reset(d):
        d.scope.set_scope("all")
        d._reset()

    run_dialog(monkeypatch, conf_reset)
    assert ld.open_crop_dialog(ctx, view, [])
    assert view.document.pdf[1].rect.width == 595


def test_crop_dialog_hidden_text_warning(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.unit, "pt")
        d.top.setValue(200)
        d.scope.set_scope("current")

    run_dialog(monkeypatch, conf)
    assert ld.open_crop_dialog(ctx, view, [])
    assert messages and "resta nel file" in messages[-1][2]


def test_crop_dialog_range_validation(window, sample_pdf):
    view = window.open_path(sample_pdf)
    dlg = ld.CropDialog(window, view.document, [0, 2], 0)
    assert dlg.scope.scope() == "selected" and dlg.options()["pages"] == [0, 2]
    dlg.scope.set_scope("range", "2-x")
    dlg.refresh()
    assert not dlg.ok.isEnabled() and dlg.error_label.isVisibleTo(dlg)
    dlg.scope.set_scope("range", "2-3")
    dlg.refresh()
    assert dlg.ok.isEnabled() and dlg.options()["pages"] == [1, 2]
    dlg.top.setValue(5000)
    dlg.refresh()
    assert not dlg.ok.isEnabled()
    dlg.top.setValue(0)
    # rettangolo disegnato sull'anteprima → margini
    from PySide6.QtCore import QRectF
    ld._set_combo(dlg.unit, "pt")
    dlg._rect_drawn(QRectF(10, 20, 300, 400))
    o = dlg.options()
    assert o["margins"] == pytest.approx((20, 842 - 420, 10, 595 - 310), abs=0.01)


# ======================================================================== margins
def test_remove_margins_dialog(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        d.extra.setValue(2)
        d.scope.set_scope("all")

    run_dialog(monkeypatch, conf)
    assert ld.open_remove_margins_dialog(ctx, view, [])
    box = raw_box(view, 0, "CropBox")
    assert box is not None and box[0] > 60 and box[3] < 842 - 60
    assert view.document.undo()
    assert raw_box(view, 0, "CropBox") is None


# ======================================================================== boxes
def test_boxes_dialog_trim_bleed(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        assert "predefinito" in d.value_labels["TrimBox"].text()
        d.mode_trim.setChecked(True)
        ld._set_combo(d.paper, "A5")
        d.bleed.setValue(3)

    run_dialog(monkeypatch, conf)
    assert ld.open_boxes_dialog(ctx, view, [])
    trim = pymupdf.Rect(raw_box(view, 0, "TrimBox"))
    assert abs(trim.width / MM - 148) < 0.2 and abs(trim.height / MM - 210) < 0.2
    assert view.document.undo()
    assert raw_box(view, 0, "TrimBox") is None


def test_boxes_dialog_set_art_with_margins(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        d.mode_box.setChecked(True)
        ld._set_combo(d.box_name, "ArtBox")
        d.m_left.setValue(10)
        d.scope.set_scope("current")

    run_dialog(monkeypatch, conf)
    assert ld.open_boxes_dialog(ctx, view, [])
    art = raw_box(view, 0, "ArtBox")
    assert abs(art[0] - 10 * MM) < 0.1 and raw_box(view, 1, "ArtBox") is None


# ======================================================================== resize
def test_resize_dialog_scale_and_undo(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.paper, "A3")
        d.mode_scale.setChecked(True)
        d.scope.set_scope("all")

    run_dialog(monkeypatch, conf)
    assert ld.open_resize_dialog(ctx, view, [])
    assert abs(view.document.pdf[0].rect.width - 841.89) < 0.5
    assert "Pagina 1" in view.document.pdf[0].get_text()
    assert view.document.undo()
    assert abs(view.document.pdf[0].rect.width - 595) < 0.5


def test_resize_dialog_canvas_anchor(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.paper, "A3")
        d.mode_canvas.setChecked(True)
        d.anchor_buttons["top-left"].setChecked(True)
        d.scope.set_scope("current")

    run_dialog(monkeypatch, conf)
    assert ld.open_resize_dialog(ctx, view, [])
    page = view.document.pdf[0]
    assert abs(page.rect.height - 1190.55) < 0.5
    w = page.search_for("Pagina")[0]
    assert w.x0 < 80 and w.y0 < 100        # ancorato in alto a sinistra: non spostato


# ======================================================================== split
def test_split_dialog(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        d.rows.setValue(2)
        d.cols.setValue(1)
        d.scope.set_scope("range", "1")

    run_dialog(monkeypatch, conf)
    assert ld.open_split_page_dialog(ctx, view, [])
    assert view.document.page_count == 4
    assert abs(view.document.pdf[0].rect.height - 421) < 0.5
    assert view.document.undo()
    assert view.document.page_count == 3


def test_split_dialog_invalid_cuts_disable_ok(window, sample_pdf):
    view = window.open_path(sample_pdf)
    dlg = ld.SplitPageDialog(window, view.document, [], 0)
    dlg.cuts_x.setText("10, abc")
    dlg.refresh()
    assert not dlg.ok.isEnabled()
    dlg.cuts_x.setText("100")
    dlg.refresh()
    assert dlg.ok.isEnabled() and dlg.options()["cuts_x"] == pytest.approx([100 * MM])


# ======================================================================== n-up
def test_nup_dialog_opens_new_document(env, monkeypatch):
    ctx, view, messages = env
    before = len(list(ctx.window.views()))

    def conf(d):
        ld._set_combo(d.preset, "2x1")
        d.scope.set_scope("all")
        assert "2" in d.info.text()

    run_dialog(monkeypatch, conf)
    assert ld.open_nup_dialog(ctx, view, [])
    views = list(ctx.window.views())
    assert len(views) == before + 1
    new = [v for v in views if v is not view][-1]
    assert new.document.page_count == 2
    assert "affiancate" in new.document.display_name
    assert view.document.page_count == 3 and not view.document.can_undo


def test_dialog_cancel_changes_nothing(env, monkeypatch):
    ctx, view, messages = env
    run_dialog(monkeypatch, lambda d: None, accept=False)
    assert not ld.open_crop_dialog(ctx, view, [])
    assert not view.document.can_undo


# ======================================================================== screenshots
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_dialog_screenshots(window, sample_pdf, mode):
    from foliant.ui import theme
    theme.apply(QApplication.instance(), mode)
    try:
        view = window.open_path(sample_pdf)
        os.makedirs(SHOTS, exist_ok=True)
        for cls in (ld.CropDialog, ld.RemoveMarginsDialog, ld.BoxesDialog, ld.ResizeDialog, ld.SplitPageDialog,
                    ld.NupDialog):
            dlg = cls(window, view.document, [0, 1], 0)
            if cls is ld.CropDialog:
                dlg.same.setChecked(True)
                dlg.top.setValue(15)
            if cls is ld.NupDialog:
                ld._set_combo(dlg.preset, "2x2")
            dlg.refresh()
            dlg.show()
            QApplication.processEvents()
            assert dlg.grab().save(f"{SHOTS}/{cls.__name__}-{mode}.png")
            dlg.close()
    finally:
        theme.apply(QApplication.instance(), "light")


# ======================================================================== follow-up
def test_remove_margins_dialog_custom_rect(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        d.scope.set_scope("current")
        d.set_custom_rect(0, (100, 200, 300, 500))      # coordinate visualizzate
        assert d.options()["rects"] is not None

    run_dialog(monkeypatch, conf)
    assert ld.open_remove_margins_dialog(ctx, view, [])
    box = raw_box(view, 0, "CropBox")
    assert box == pytest.approx([100, 842 - 500, 300, 842 - 200], abs=0.01)
    assert view.document.undo()


def test_remove_margins_dialog_restore_detection(window, sample_pdf):
    view = window.open_path(sample_pdf)
    dlg = ld.RemoveMarginsDialog(window, view.document, [], 0)
    from PySide6.QtCore import QRectF
    dlg._rect_drawn(QRectF(10, 10, 100, 100))
    assert 0 in dlg.custom
    dlg.refresh()
    assert dlg.restore_btn.isEnabled()
    dlg._restore_detection()
    dlg.refresh()
    assert dlg.options()["rects"] is None and not dlg.restore_btn.isEnabled()


def test_resize_dialog_fill_and_small_text_warning(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        ld._set_combo(d.paper, "custom")
        d.width_mm.setValue(100)
        d.height_mm.setValue(100)
        ld._set_combo(d.orientation, "portrait")
        d.mode_fill.setChecked(True)
        d.scope.set_scope("current")
        d.refresh()
        assert "tagliate" in d.info.text()

    run_dialog(monkeypatch, conf)
    assert ld.open_resize_dialog(ctx, view, [])
    assert abs(view.document.pdf[0].rect.width - 100 * MM) < 0.5
    assert messages and "tagliato" in messages[-1][2]

    def conf_small(d):
        ld._set_combo(d.paper, "custom")
        d.width_mm.setValue(30)
        d.height_mm.setValue(42)
        d.mode_scale.setChecked(True)
        d.scope.set_scope("range", "2")
        d.refresh()
        assert "4 pt" in d.info.text()

    run_dialog(monkeypatch, conf_small)
    assert ld.open_resize_dialog(ctx, view, [])
    assert "4 pt" in messages[-1][2]


def test_split_dialog_fold_and_remove_outside(window, tmp_path, monkeypatch):
    doc = pymupdf.open()
    p = doc.new_page(width=842, height=595)
    p.draw_rect(p.rect, color=None, fill=(0.85, 0.85, 0.85))
    p.draw_rect(pymupdf.Rect(446, 0, 454, 595), color=None, fill=(0.2, 0.2, 0.2))
    p.insert_text((60, 100), "SINISTRA", fontsize=20)
    p.insert_text((600, 100), "DESTRA", fontsize=20)
    path = tmp_path / "libro.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    ctx = window.ctx
    monkeypatch.setattr(ctx, "info", lambda *a: None, raising=False)
    monkeypatch.setattr(ctx, "error", lambda *a: None, raising=False)

    def conf(d):
        ld._set_combo(d.method, "fold")
        d.remove_outside.setChecked(True)
        d.scope.set_scope("all")
        d.refresh()
        assert "Piega rilevata" in d.info.text()

    run_dialog(monkeypatch, conf)
    assert ld.open_split_page_dialog(ctx, view, [])
    pdf = view.document.pdf
    assert pdf.page_count == 2 and abs(pdf[0].rect.width - 450) <= 3
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_MEDIABOX_CLIP
    assert "DESTRA" not in pdf[0].get_text("text", flags=flags, clip=pymupdf.INFINITE_RECT())
    assert view.document.undo() and view.document.page_count == 1


def test_boxes_overlay_install_toggle(env, monkeypatch):
    ctx, view, messages = env
    from PySide6.QtGui import QImage, QPainter
    with view.document.change("trim") as pdf:
        from foliant.modules.pages import layout
        layout.set_trim_with_bleed(pdf, [0], (500, 700), bleed=10)
    dlg = ld.BoxesDialog(ctx.window, view.document, [], 0, view=view)
    assert dlg.show_in_view.isEnabled() and not dlg.show_in_view.isChecked()
    dlg.show_in_view.setChecked(True)
    assert ld.boxes_overlay_installed(view)
    ld.install_boxes_overlay(view)                       # idempotente
    assert sum(getattr(f, "foliant_boxes", False) for f in view.overlays) == 1
    img = QImage(600, 850, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    for fn in view.overlays:
        fn(view, p, 0)
        fn(view, p, 1)
    p.end()
    assert img.pixel(int((595 - 500) / 2), 300) != 0xFFFFFFFF      # bordo del formato finito disegnato
    view.grab()
    dlg2 = ld.BoxesDialog(ctx.window, view.document, [], 0, view=view)
    assert dlg2.show_in_view.isChecked()
    dlg2.show_in_view.setChecked(False)
    assert not ld.boxes_overlay_installed(view)
    ld.remove_boxes_overlay(view)
    assert ld.BoxesDialog(ctx.window, view.document, [], 0).show_in_view.isEnabled() is False
