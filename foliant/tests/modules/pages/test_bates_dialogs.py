"""Test del dialogo Bates multi-file (PAG-35)."""

from __future__ import annotations

import csv
import os

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from foliant.api import ENGINE_LOCK
from foliant.modules.pages import bates
from foliant.modules.pages import bates_dialogs as BD

SHOTS = "/tmp/claude-0/shots"


def _pdf(path, pages, label="Doc"):
    doc = pymupdf.open()
    for k in range(pages):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"{label} pagina {k + 1}", fontsize=20)
    doc.save(str(path))
    doc.close()
    return str(path)


def _codes_doc(doc, prefix="ABC"):
    out = []
    for page in doc:
        words = [w for w in page.get_text().split() if w.startswith(prefix)]
        out.append(words[0] if words else None)
    return out


def _codes(path, prefix="ABC"):
    doc = pymupdf.open(path)
    try:
        return _codes_doc(doc, prefix)
    finally:
        doc.close()


def _accept_with(monkeypatch, configure=None):
    def fake_exec(self):
        # altri dialoghi (es. «modifiche non salvate» alla chiusura della finestra): annulla
        if not isinstance(self, BD.BatesFilesDialog):
            return QDialog.DialogCode.Rejected
        if configure:
            configure(self)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)


def _silence(monkeypatch, ctx, confirm=True):
    msgs = {"error": [], "info": [], "confirm": []}
    monkeypatch.setattr(ctx, "error", lambda t, m: msgs["error"].append(m))
    monkeypatch.setattr(ctx, "info", lambda t, m: msgs["info"].append(m))

    def conf(t, m):
        msgs["confirm"].append(m)
        return confirm
    monkeypatch.setattr(ctx, "confirm", conf)
    return msgs


@pytest.fixture
def ab(tmp_path):
    return _pdf(tmp_path / "A.pdf", 3, "A"), _pdf(tmp_path / "B.pdf", 2, "B")


# ------------------------------------------------------------------ dialogo da solo
def test_preview_first_last_per_file(qtbot, ab):
    a, b = ab
    d = BD.BatesFilesDialog(paths=[a, b])
    qtbot.addWidget(d)
    d.prefix.setText("ABC")
    rows = [[d.tree.topLevelItem(i).text(c) for c in range(4)] for i in range(2)]
    assert rows == [["A.pdf", "3", "ABC000001", "ABC000003"], ["B.pdf", "2", "ABC000004", "ABC000005"]]
    d.mode.setCurrentIndex(d.mode.findData(False))
    assert d.tree.topLevelItem(1).text(2) == "ABC000001"
    # riordino: B prima di A
    d.mode.setCurrentIndex(d.mode.findData(True))
    d.tree.topLevelItem(1).setSelected(True)
    d.move_selected(-1)
    assert d.tree.topLevelItem(0).text(0) == "B.pdf" and d.tree.topLevelItem(0).text(3) == "ABC000002"
    assert [os.path.basename(e.path) for e in d.options()["entries"]] == ["B.pdf", "A.pdf"]


def test_digits_overflow_warning_and_fix(qtbot, ab):
    a, b = ab
    d = BD.BatesFilesDialog(paths=[a, b], folder="/tmp")
    qtbot.addWidget(d)
    d.digits.setValue(3)
    d.start.setValue(996)
    assert not d.ok_button.isEnabled()
    assert d.warning.text() and not d.fix_digits_btn.isHidden()
    assert "4" in d.fix_digits_btn.text()
    d.fix_digits_btn.click()
    assert d.digits.value() == 4 and d.ok_button.isEnabled()


def test_existing_bates_requires_replace_and_encrypted_marked(qtbot, ab, tmp_path):
    a, b = ab
    bates.bates_files([a], prefix="ABC")
    enc = tmp_path / "E.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    doc.close()
    d = BD.BatesFilesDialog(paths=[a, str(enc), b], folder=str(tmp_path / "out"), ask_password=lambda p: None)
    qtbot.addWidget(d)
    assert not d.ok_button.isEnabled() and "A.pdf" in d.warning.text()
    d.replace.setChecked(True)
    assert d.ok_button.isEnabled()
    assert d.tree.topLevelItem(1).text(2) == BD._("protetto")
    assert d.tree.topLevelItem(2).text(2) == "000004"         # il file protetto non consuma numeri
    # con password: entra nella numerazione
    d2 = BD.BatesFilesDialog(paths=[str(enc)], folder="/tmp", ask_password=lambda p: "x")
    qtbot.addWidget(d2)
    assert d2.tree.topLevelItem(0).text(1) == "1" and d2.options()["entries"][0].password == "x"


# ------------------------------------------------------------------ esecuzione
def test_current_document_and_copies_with_log(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    b = _pdf(tmp_path / "B.pdf", 2, "B")
    out = tmp_path / "out"
    log = tmp_path / "registro.csv"
    calls = []
    real = ctx.run_task

    def spy(title, fn, *args, **kw):
        calls.append(ENGINE_LOCK._is_owned())
        return real(title, fn, *args, **kw)
    monkeypatch.setattr(ctx, "run_task", spy)
    monkeypatch.setattr(ctx, "ask_open_paths", lambda *a, **k: [b, sample_pdf])   # il documento aperto non si duplica
    monkeypatch.setattr(ctx, "ask_directory", lambda *a, **k: str(out))
    monkeypatch.setattr(ctx, "ask_save_path", lambda *a, **k: str(log))

    def configure(d):
        assert d.keys()[0] == BD.CURRENT_KEY
        d._add()
        assert len(d.keys()) == 2
        d.prefix.setText("ABC")
        d._browse_folder()
        d._browse_log()
        assert d.ok_button.isEnabled(), d.warning.text()
    _accept_with(monkeypatch, configure)
    assert BD.open_bates_files_dialog(ctx, view, [0])
    assert calls == [False]
    assert _codes_doc(view.document.pdf) == ["ABC000001", "ABC000002", "ABC000003"]
    assert _codes(sample_pdf) == [None, None, None]               # file del documento aperto non scritto
    copies = sorted(p.name for p in out.iterdir())
    assert copies == ["B_ABC000004-ABC000005.pdf"]
    assert _codes(str(out / copies[0])) == ["ABC000004", "ABC000005"]
    with open(log, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["file"], r["pagina"], r["numero"]) for r in rows] == [
        ("esempio.pdf", "1", "ABC000001"), ("esempio.pdf", "2", "ABC000002"), ("esempio.pdf", "3", "ABC000003"),
        ("B.pdf", "1", "ABC000004"), ("B.pdf", "2", "ABC000005")]
    assert msgs["info"] and "ABC000005" in msgs["info"][0]
    view.document.undo()
    assert _codes_doc(view.document.pdf) == [None, None, None]


def test_overwrite_in_place_restart_per_document(window, ab, monkeypatch):
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    a, b = ab

    def configure(d):
        d.add_paths([a, b])
        d.prefix.setText("ABC")
        d.mode.setCurrentIndex(d.mode.findData(False))
        d.overwrite_radio.setChecked(True)
    _accept_with(monkeypatch, configure)
    assert BD.open_bates_files_dialog(ctx, None, None)
    assert msgs["confirm"]                                        # conferma prima di sovrascrivere
    assert _codes(a) == ["ABC000001", "ABC000002", "ABC000003"]
    assert _codes(b) == ["ABC000001", "ABC000002"]


def test_overwrite_refused_changes_nothing(window, ab, monkeypatch):
    ctx = window.ctx
    _silence(monkeypatch, ctx, confirm=False)
    a, _b = ab
    _accept_with(monkeypatch, lambda d: (d.add_paths([a]), d.overwrite_radio.setChecked(True)))
    assert not BD.open_bates_files_dialog(ctx, None, None)
    assert _codes(a, "0") == [None, None, None]


def test_cancel_dialog(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not BD.open_bates_files_dialog(window.ctx, view, [])
    assert not view.document.can_undo


def test_cancelled_run_reports_resume_number(window, ab, tmp_path, monkeypatch):
    from foliant.api import OperationCancelled
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    a, b = ab

    def fake_run(title, fn, *args, **kw):
        state = {"n": 0}

        class P:
            def update(self, f, t):
                state["n"] += 1
                if state["n"] == 2:
                    raise OperationCancelled()
        return fn(P(), *args, **kw)
    monkeypatch.setattr(ctx, "run_task", fake_run)
    _accept_with(monkeypatch, lambda d: (d.add_paths([a, b]), d.prefix.setText("ABC"),
                                         d.folder_edit.setText(str(tmp_path / "out"))))
    assert BD.open_bates_files_dialog(ctx, None, None)
    text = msgs["info"][0]
    assert "B.pdf" in text and "4" in text


# ------------------------------------------------------------------ screenshot
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_dialog_screenshot(qtbot, ab, mode):
    from foliant.ui import theme
    theme.apply(QApplication.instance(), mode)
    try:
        os.makedirs(SHOTS, exist_ok=True)
        a, b = ab
        cur = BD._FileInfo("/home/utente/contratto.pdf", "contratto.pdf", 12)
        d = BD.BatesFilesDialog(current=cur, paths=[a, b], folder="/home/utente/Bates")
        qtbot.addWidget(d)
        d.prefix.setText("ABC")
        d.digits.setValue(3)
        d.start.setValue(990)
        d.show()
        QApplication.processEvents()
        assert d.grab().save(f"{SHOTS}/bates_files_{mode}.png")
    finally:
        theme.apply(QApplication.instance(), "light")
