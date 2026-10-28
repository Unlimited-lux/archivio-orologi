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
