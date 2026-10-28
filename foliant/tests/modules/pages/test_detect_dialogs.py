"""Test del dialogo «Trova pagine vuote e duplicate» (PAG-40)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from foliant.modules.pages import detect_dialogs as dd

SHOTS = "/tmp/claude-0/shots"


def make_doc(path):
    doc = pymupdf.open()
    for n in range(1, 7):
        p = doc.new_page(width=595, height=842)
        if n not in (2, 5):
            p.insert_text((72, 100), f"Fattura {n}", fontsize=24)
            p.insert_text((72, 140), f"Orologio cronografo, referenza {n * 1111}", fontsize=12)
    doc.fullcopy_page(0)                      # pagina 7 = copia della 1
    doc.save(str(path))
    return str(path)


@pytest.fixture
def env(window, tmp_path, monkeypatch):
    view = window.open_path(make_doc(tmp_path / "fatture.pdf"))
    ctx = window.ctx
    messages = []
    monkeypatch.setattr(ctx, "info", lambda t, m: messages.append(m), raising=False)
    monkeypatch.setattr(ctx, "error", lambda t, m: messages.append(m), raising=False)
    return ctx, view, messages


def patch(monkeypatch, conf=None, results_conf=None, accept=True):
    seen = {}

    def main_exec(self):
        if conf:
            conf(self)
        return QDialog.DialogCode.Accepted if self.ok.isEnabled() else QDialog.DialogCode.Rejected

    def res_exec(self):
        seen["res"] = self
        if results_conf:
            results_conf(self)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(dd.DetectDialog, "exec", main_exec, raising=False)
    monkeypatch.setattr(dd.DetectResultsDialog, "exec", res_exec, raising=False)
    return seen


def test_detect_delete_in_one_undo_step(env, monkeypatch):
    ctx, view, messages = env

    def res(r):
        assert [x["page"] for x in r.results] == [1, 4, 6]
        assert r.results[2]["original"] == 0 and "duplicato della pagina 1" in r.results[2]["reason"]
        assert "vuota" in r.results[0]["reason"]
        r.action = r.DELETE

    patch(monkeypatch, lambda d: d.scope.set_scope("all"), res)
    assert dd.open_detect_dialog(ctx, view, [])
    assert view.document.page_count == 4
    assert view.document.undo() and view.document.page_count == 7 and not view.document.can_undo


def test_detect_select_in_organizer(env, monkeypatch):
    ctx, view, messages = env
    c = getattr(ctx, "pages_controller", None)

    def res(r):
        r.set_checked([1, 4])
        r.action = r.SELECT

    patch(monkeypatch, None, res)
    assert not dd.open_detect_dialog(ctx, view, [])
    assert view.document.page_count == 7
    if c is not None:
        assert sorted(c.organizer.selected_pages()) == [1, 4]


def test_detect_options_and_no_results(env, monkeypatch, window, sample_pdf):
    ctx, view, messages = env
    dlg = dd.DetectDialog(ctx.window, view.document, [], 0)
    dlg.blank_box.setChecked(False)
    dlg.dup_box.setChecked(False)
    assert not dlg.ok.isEnabled()
    dlg.dup_box.setChecked(True)
    dlg.mode.setCurrentIndex(1)
    dlg.scope_adj.setChecked(True)
    o = dlg.options()
    assert o["mode"] == "similar" and o["scope"] == "adjacent" and not o["blank"] and dlg.similarity.isEnabled()

    def only_blank_high_threshold(d):
        d.dup_box.setChecked(False)
        d.ink.setValue(0)
        d.scope.set_scope("range", "1")

    patch(monkeypatch, only_blank_high_threshold)
    assert not dd.open_detect_dialog(ctx, view, [])
    assert messages[-1] == "Nessuna pagina vuota o duplicata trovata."


def test_results_dialog_buttons_and_thumbs(env):
    ctx, view, messages = env
    results = [{"page": 6, "kind": "duplicate", "original": 0, "reason": "x"}]
    r = dd.DetectResultsDialog(ctx.window, view.document, results)
    assert r.thumb_orig.pixmap() is not None and not r.thumb_orig.pixmap().isNull()
    r.set_checked([])
    assert not r.delete_btn.isEnabled() and not r.select_btn.isEnabled()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_detect_screenshots(env, mode):
    import os
    from foliant.api import ENGINE_LOCK
    from foliant.ui import theme
    ctx, view, messages = env
    theme.apply(QApplication.instance(), mode)
    try:
        os.makedirs(SHOTS, exist_ok=True)
        d = dd.DetectDialog(ctx.window, view.document, [], 0)
        d.show()
        QApplication.processEvents()
        d.grab().save(f"{SHOTS}/DetectDialog-{mode}.png")
        with ENGINE_LOCK:
            res = dd.run_detection(view.document.pdf, d.options())
        r = dd.DetectResultsDialog(ctx.window, view.document, res)
        r.list.setCurrentRow(len(res) - 1)
        r.show()
        QApplication.processEvents()
        r.grab().save(f"{SHOTS}/DetectResults-{mode}.png")
    finally:
        theme.apply(QApplication.instance(), "light")
