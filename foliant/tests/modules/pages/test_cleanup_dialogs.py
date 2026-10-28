"""Test del dialogo di pulizia guidata (PAG-41)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from foliant.modules.pages import cleanup_dialogs as cd

from .test_cleanup import cleanup_doc

SHOTS = "/tmp/claude-0/shots"


@pytest.fixture
def env(window, tmp_path, monkeypatch):
    path = tmp_path / "pulizia.pdf"
    cleanup_doc().save(str(path))
    view = window.open_path(str(path))
    ctx = window.ctx
    messages = []
    monkeypatch.setattr(ctx, "info", lambda t, m: messages.append(("info", t, m)), raising=False)
    monkeypatch.setattr(ctx, "error", lambda t, m: messages.append(("error", t, m)), raising=False)
    monkeypatch.setattr(ctx, "confirm", lambda t, m: False, raising=False)
    return ctx, view, messages


def patch_exec(monkeypatch, conf_main=None, conf_review=None, accept_review=True):
    seen = {}

    def main_exec(self):
        seen["main"] = self
        if conf_main:
            conf_main(self)
        return QDialog.DialogCode.Accepted if self.ok.isEnabled() else QDialog.DialogCode.Rejected

    def review_exec(self):
        seen["review"] = self
        if conf_review:
            conf_review(self)
        return QDialog.DialogCode.Accepted if accept_review else QDialog.DialogCode.Rejected

    monkeypatch.setattr(cd.CleanupDialog, "exec", main_exec, raising=False)
    monkeypatch.setattr(cd.CleanupReviewDialog, "exec", review_exec, raising=False)
    return seen


def test_cleanup_all_accepted_single_undo(env, monkeypatch):
    ctx, view, messages = env

    def main(d):
        d.osd.setChecked(False)
        d.scope.set_scope("all")

    def review(r):
        cats = {p.category for _it, p in r.items}
        assert cats == {"blank", "orientation", "sizes", "ocr"}
        r.set_accepted("ocr", True)

    seen = patch_exec(monkeypatch, main, review)
    assert cd.open_cleanup_dialog(ctx, view, [])
    doc = view.document
    assert doc.page_count == 5 and doc.pdf[1].rotation == 180
    assert "OCR" in messages[-1][2] and "Pagine eliminate: 1" in messages[-1][2]
    assert doc.undo()
    assert doc.page_count == 6 and doc.pdf[2].rotation == 0 and not doc.can_undo


def test_cleanup_individual_rejection(env, monkeypatch):
    ctx, view, messages = env

    def review(r):
        r.set_accepted("blank", False)
        r.set_accepted("sizes", False)

    patch_exec(monkeypatch, lambda d: d.osd.setChecked(False), review)
    assert cd.open_cleanup_dialog(ctx, view, [])
    assert view.document.page_count == 6 and view.document.pdf[2].rotation == 180
    assert abs(view.document.pdf[3].rect.width - 612) < 0.5


def test_cleanup_validation_and_cancel(env, monkeypatch):
    ctx, view, messages = env
    dlg = cd.CleanupDialog(ctx.window, view.document, [], 0)
    for cb in dlg.checks.values():
        cb.setChecked(False)
    assert not dlg.ok.isEnabled() and dlg.error_label.isVisibleTo(dlg)
    dlg.checks["blank"].setChecked(True)
    assert dlg.ok.isEnabled() and dlg.options()["checks"] == ["blank"]
    patch_exec(monkeypatch, lambda d: d.osd.setChecked(False), accept_review=False)
    assert not cd.open_cleanup_dialog(ctx, view, [])
    assert not view.document.can_undo


def test_review_read_only_and_export(env, monkeypatch, tmp_path):
    ctx, view, messages = env
    from foliant.api import ENGINE_LOCK
    from foliant.modules.pages import cleanup
    with view.document.change("nota") as pdf:
        pdf[0].add_text_annot((100, 100), "da controllare")
    with ENGINE_LOCK:
        res = cleanup.analyze(view.document.pdf, use_tesseract=False)
    called = []
    r = cd.CleanupReviewDialog(ctx.window, view.document, res, can_modify=False, export_fn=lambda: called.append(1))
    assert not r.ok.isEnabled() and r.export_btn.isVisibleTo(r)
    r.export_btn.click()
    assert called
    out = tmp_path / "a.json"
    monkeypatch.setattr(ctx, "ask_save_path", lambda *a, **k: str(out), raising=False)


def test_no_proposals_message(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    messages = []
    monkeypatch.setattr(ctx, "info", lambda t, m: messages.append(m), raising=False)

    def main(d):
        for k in ("annotations",):
            d.checks[k].setChecked(False)
        d.osd.setChecked(False)

    patch_exec(monkeypatch, main)
    assert not cd.open_cleanup_dialog(ctx, view, [])
    assert "già in ordine" in messages[-1]


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_cleanup_screenshots(env, mode):
    import os
    from foliant.api import ENGINE_LOCK
    from foliant.modules.pages import cleanup
    from foliant.ui import theme
    ctx, view, messages = env
    theme.apply(QApplication.instance(), mode)
    try:
        os.makedirs(SHOTS, exist_ok=True)
        d = cd.CleanupDialog(ctx.window, view.document, [], 0)
        d.show()
        QApplication.processEvents()
        d.grab().save(f"{SHOTS}/CleanupDialog-{mode}.png")
        with ENGINE_LOCK:
            res = cleanup.analyze(view.document.pdf, use_tesseract=False)
        r = cd.CleanupReviewDialog(ctx.window, view.document, res, True, lambda: None)
        r.show()
        QApplication.processEvents()
        r.grab().save(f"{SHOTS}/CleanupReview-{mode}.png")
    finally:
        theme.apply(QApplication.instance(), "light")
