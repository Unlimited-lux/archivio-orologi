"""Test del dialogo della filigrana di stampa (PAG-37)."""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from foliant.modules.pages import fixedmark, fixedmark_dialogs as fd

SHOTS = "/tmp/claude-0/shots"


@pytest.fixture
def env(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    messages = []
    monkeypatch.setattr(ctx, "info", lambda t, m: messages.append(m), raising=False)
    monkeypatch.setattr(ctx, "error", lambda t, m: messages.append(m), raising=False)
    return ctx, view, messages


def run(monkeypatch, conf):
    def fake(self):
        conf(self)
        self.refresh()
        return QDialog.DialogCode.Accepted if self.ok.isEnabled() else QDialog.DialogCode.Rejected
    monkeypatch.setattr(fd.FixedWatermarkDialog, "exec", fake, raising=False)


def test_add_explicit_and_undo(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        d.text.setText("COPIA")
        d.explicit.setChecked(True)
        d.h.setValue(0.9)
        d.v.setValue(0.9)
        d.size.setValue(18)
        d.scope.set_scope("all")

    run(monkeypatch, conf)
    assert fd.open_fixed_watermark_dialog(ctx, view, [])
    for i in range(view.document.page_count):
        marks = fixedmark.fixed_watermarks(view.document.pdf, i)
        assert len(marks) == 1 and marks[0]["h"] == pytest.approx(0.9)
    assert view.document.undo()
    assert fixedmark.fixed_watermarks(view.document.pdf, 0) == []


def test_anchor_remove_and_validation(env, monkeypatch):
    ctx, view, messages = env

    def conf(d):
        from foliant.modules.pages.layout_dialogs import _set_combo
        _set_combo(d.anchor, "bottom-left")
        d.scope.set_scope("current")

    run(monkeypatch, conf)
    assert fd.open_fixed_watermark_dialog(ctx, view, [])
    marks = fixedmark.fixed_watermarks(view.document.pdf, 0)
    assert marks[0]["h"] < 0.1 and marks[0]["v"] < 0.1
    run(monkeypatch, lambda d: (d.remove.setChecked(True), d.scope.set_scope("all")))
    assert fd.open_fixed_watermark_dialog(ctx, view, [])
    assert fixedmark.fixed_watermarks(view.document.pdf, 0) == []
    dlg = fd.FixedWatermarkDialog(ctx.window, view.document, [], 0)
    dlg.text.setText("  ")
    dlg.refresh()
    assert not dlg.ok.isEnabled()
    dlg.text.setText("TESTO LUNGHISSIMO " * 12)
    dlg.size.setValue(80)
    dlg.refresh()
    assert not dlg.ok.isEnabled() and dlg.error_label.text()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_screenshots(env, mode):
    from foliant.ui import theme
    ctx, view, messages = env
    theme.apply(QApplication.instance(), mode)
    try:
        os.makedirs(SHOTS, exist_ok=True)
        d = fd.FixedWatermarkDialog(ctx.window, view.document, [], 0)
        d.rotation.setValue(30)
        d.refresh()
        d.show()
        QApplication.processEvents()
        d.grab().save(f"{SHOTS}/FixedWatermark-{mode}.png")
    finally:
        theme.apply(QApplication.instance(), "light")


def test_new_dialogs_fully_english(pages_window):
    """NFR-25 per i dialoghi di PAG-31/37/40/41."""
    from foliant import i18n
    from foliant.api import ENGINE_LOCK
    from foliant.modules.pages import cleanup, cleanup_dialogs as cd, detect_dialogs as dd, layout_dialogs as ld

    from .test_english import _italian_words, _texts
    window, view = pages_window
    doc = view.document
    i18n.set_language("en")
    try:
        with ENGINE_LOCK:
            res = cleanup.analyze(doc.pdf, use_tesseract=False)
        results = [{"page": 1, "kind": "duplicate", "original": 0, "reason": "Page 2"}]
        bad = {}
        for name, make in (("Booklet", lambda: ld.BookletDialog(window, doc, [0])),
                           ("FixedWatermark", lambda: fd.FixedWatermarkDialog(window, doc, [0])),
                           ("Detect", lambda: dd.DetectDialog(window, doc, [0])),
                           ("DetectResults", lambda: dd.DetectResultsDialog(window, doc, results)),
                           ("Cleanup", lambda: cd.CleanupDialog(window, doc, [0])),
                           ("CleanupReview", lambda: cd.CleanupReviewDialog(window, doc, res, True, lambda: None))):
            dlg = make()
            if hasattr(dlg, "refresh"):
                dlg.refresh()
            words = set()
            for t in _texts(dlg):
                words |= _italian_words(t)
            if words:
                bad[name] = sorted(words)
        assert not bad, bad
    finally:
        i18n.set_language("it")
