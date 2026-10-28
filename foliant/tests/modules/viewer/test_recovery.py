"""Test del salvataggio automatico e del recupero dopo crash."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import Qt

from foliant.api import Document
from foliant.modules.viewer import recovery as recovery_mod
from foliant.modules.viewer import recovery_engine as engine
from foliant.ui.main_window import MainWindow

from .conftest import part


@pytest.fixture(autouse=True)
def _no_close_dialog(monkeypatch):
    """Chiusure con modifiche alla fine dei test: nessun dialogo bloccante (equivale ad Annulla)."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)


def _rec(window):
    return part(window, "recovery")


def _edit(doc, text="MODIFICA DI PROVA"):
    with doc.change("Prova") as pdf:
        pdf[0].insert_text((72, 300), text, fontsize=14)


def _text(data: bytes, password: str | None = None) -> str:
    pdf = pymupdf.open(stream=data, filetype="pdf")
    if password:
        pdf.authenticate(password)
    try:
        return pdf[0].get_text()
    finally:
        pdf.close()


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.glob("*") if not p.name.startswith(".")) if directory.exists() else []


def _crash_entry(directory: Path, doc: Document, original: str, page: int = 1, pid: int | None = None) -> str:
    entry_id = engine.new_entry_id()
    meta = engine.make_meta(original_path=original, display_name=Path(original).name, page=page,
                            pid=_dead_pid() if pid is None else pid)
    engine.write_entry(directory, entry_id, doc.to_bytes(), meta)
    return entry_id


def _new_window(qtbot, settings):
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1100, 760)
    win.show()
    return win


def _cleanup(win):
    for view in list(win.views()):
        view.document._saved_state = view.document._state
        win.close_view(view)


# ====================================================================== motore
def test_recovery_dir_resolution(tmp_path):
    assert engine.recovery_dir({"FOLIANT_RECOVERY_DIR": "/x/rec"}) == Path("/x/rec")
    assert engine.recovery_dir({"XDG_STATE_HOME": "/s"}) == Path("/s/foliant/recovery")
    assert engine.recovery_dir({}) == Path.home() / ".local/state/foliant/recovery"


def test_pid_alive():
    assert engine.pid_alive(os.getpid())
    assert not engine.pid_alive(_dead_pid())
    assert not engine.pid_alive(0)
    assert not engine.pid_alive(2 ** 40)


def test_orphans_filtering_and_corrupt_json(tmp_path, sample_pdf):
    d = tmp_path / "rec"
    doc = Document.open(sample_pdf)
    dead = _crash_entry(d, doc, sample_pdf)
    live = _crash_entry(d, doc, sample_pdf, pid=12345)
    corrupt_ok = _crash_entry(d, doc, sample_pdf)
    (d / f"{corrupt_ok}.json").write_text("{non è json")
    corrupt_bad = _crash_entry(d, doc, sample_pdf)
    (d / f"{corrupt_bad}.json").write_text("garbage")
    (d / f"{corrupt_bad}.pdf").write_bytes(b"not a pdf")
    (d / "solo.json").write_text(json.dumps(engine.make_meta(original_path="", display_name="x", page=0,
                                                             pid=_dead_pid())))
    doc.close()
    found = engine.orphan_entries(d, alive=lambda pid: pid == 12345)
    ids = {e.id for e in found}
    assert ids == {dead, corrupt_ok}
    assert not (d / f"{corrupt_bad}.pdf").exists()        # copia illeggibile eliminata
    assert not (d / "solo.json").exists()
    assert (d / f"{live}.pdf").exists()                    # di un'altra istanza: intatta
    corrupt = next(e for e in found if e.id == corrupt_ok)
    assert corrupt.display_name == "Senza titolo.pdf" and corrupt.page == 0


# ====================================================================== salvataggio automatico
def test_autosave_writes_modified_document(viewer_window, sample_pdf):
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = viewer_window.open_path(sample_pdf)
    view.go_to_page(2)
    _edit(view.document)
    assert rec.autosave_now() == 1
    entry_id = rec.entry_id(view.document)
    assert _files(d) == sorted([f"{entry_id}.json", f"{entry_id}.pdf"])
    assert "MODIFICA DI PROVA" in _text((d / f"{entry_id}.pdf").read_bytes())
    meta = json.loads((d / f"{entry_id}.json").read_text())
    assert meta["original_path"] == sample_pdf
    assert meta["display_name"] == "esempio.pdf"
    assert meta["page"] == 2 and meta["pid"] == os.getpid() and meta["format"] == engine.FORMAT_VERSION
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE((d / f"{entry_id}.pdf").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / f"{entry_id}.json").stat().st_mode) == 0o600
    assert "MODIFICA DI PROVA" not in Document.open(sample_pdf).page_text(0)   # originale intatto
    assert "Copia di recupero salvata alle" in viewer_window.statusBar().currentMessage()
    # Nessuna nuova copia se il documento non è cambiato dall'ultima.
    assert rec.autosave_now() == 0
    _edit(view.document, "SECONDA")
    assert rec.autosave_now() == 1
    assert "SECONDA" in _text((d / f"{entry_id}.pdf").read_bytes())


def test_unmodified_document_not_copied(viewer_window, sample_pdf):
    viewer_window.open_path(sample_pdf)
    assert _rec(viewer_window).autosave_now() == 0
    assert _files(Path(os.environ["FOLIANT_RECOVERY_DIR"])) == []


def test_save_removes_copy(viewer_window, sample_pdf):
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = viewer_window.open_path(sample_pdf)
    _edit(view.document)
    rec.autosave_now()
    assert _files(d)
    assert viewer_window.save_view(view)
    assert _files(d) == []


def test_close_removes_copy(viewer_window, sample_pdf):
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = viewer_window.open_path(sample_pdf)
    _edit(view.document)
    rec.autosave_now()
    assert _files(d)
    view.document._saved_state = view.document._state   # "Non salvare" senza dialogo
    viewer_window.close_view(view)
    assert _files(d) == []


def test_regular_exit_leaves_nothing(qtbot, settings, sample_pdf, tmp_path):
    win = _new_window(qtbot, settings)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = win.open_path(sample_pdf)
    _edit(view.document)
    _rec(win).autosave_now()
    assert _files(d)
    view.document._saved_state = view.document._state
    win.close()
    assert _files(d) == []


def test_timer_interval_from_settings(qtbot, settings):
    settings.setValue("viewer/autosave_seconds", 0)
    win = _new_window(qtbot, settings)
    assert not _rec(win).timer.isActive()
    settings.setValue("viewer/autosave_seconds", 5)
    win2 = _new_window(qtbot, settings)
    assert _rec(win2).timer.isActive() and _rec(win2).timer.interval() == 5000


def test_timer_uses_background_thread_for_large_documents(viewer_window, sample_pdf, qtbot, monkeypatch):
    rec = _rec(viewer_window)
    monkeypatch.setattr(recovery_mod, "LARGE_DOCUMENT_PAGES", 0)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = viewer_window.open_path(sample_pdf)
    _edit(view.document)
    rec._on_timer()
    qtbot.waitUntil(lambda: rec.last_saved_at is not None, timeout=5000)
    entry_id = rec.entry_id(view.document)
    assert (d / f"{entry_id}.pdf").exists()
    # salvato mentre la copia era in corso → nessun file rimasto
    _edit(view.document, "ANCORA")
    rec._on_timer()
    viewer_window.save_view(view)
    qtbot.waitUntil(lambda: not rec._inflight, timeout=5000)
    assert _files(d) == []


def test_encrypted_copy_stays_encrypted(viewer_window, encrypted_pdf, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("segreta", True))
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    view = viewer_window.open_path(encrypted_pdf)
    _edit(view.document)
    rec.autosave_now()
    data = (d / f"{rec.entry_id(view.document)}.pdf").read_bytes()
    pdf = pymupdf.open(stream=data, filetype="pdf")
    assert pdf.needs_pass
    pdf.close()
    assert "MODIFICA DI PROVA" in _text(data, "segreta")


# ====================================================================== recupero dopo crash
def _crash_with_modified_copy(tmp_path, sample_pdf, page=1):
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    doc = Document.open(sample_pdf)
    _edit(doc)
    entry_id = _crash_entry(d, doc, sample_pdf, page=page)
    doc.close()
    return d, entry_id


def test_crash_recovery_restore(qtbot, settings, sample_pdf, tmp_path):
    original = Path(sample_pdf).read_bytes()
    d, entry_id = _crash_with_modified_copy(tmp_path, sample_pdf, page=2)
    win = _new_window(qtbot, settings)
    try:
        rec = _rec(win)
        qtbot.waitUntil(lambda: rec.dialog is not None and rec.dialog.isVisible(), timeout=3000)
        dialog = rec.dialog
        assert not dialog.isModal()
        assert dialog.tree.topLevelItemCount() == 1
        item = dialog.tree.topLevelItem(0)
        assert item.text(0) == "esempio.pdf" and item.text(1) == sample_pdf and item.text(2)
        qtbot.mouseClick(dialog.restore_button, Qt.MouseButton.LeftButton)
        assert not dialog.isVisible()
        view = win.current_view()
        doc = view.document
        assert str(doc.path) == sample_pdf
        assert doc.is_modified
        assert "MODIFICA DI PROVA" in doc.page_text(0)
        assert view.current_page == 2
        # Il file originale non è stato toccato; la copia resta finché non è al sicuro.
        assert Path(sample_pdf).read_bytes() == original
        assert (d / f"{entry_id}.pdf").exists()
        assert json.loads((d / f"{entry_id}.json").read_text())["pid"] == os.getpid()
        assert rec.entry_id(doc) == entry_id
        assert win.save_view(view)
        assert "MODIFICA DI PROVA" in Document.open(sample_pdf).page_text(0)
        assert _files(d) == []
    finally:
        _cleanup(win)


def test_crash_recovery_restore_then_close_removes(qtbot, settings, sample_pdf, tmp_path):
    d, entry_id = _crash_with_modified_copy(tmp_path, sample_pdf)
    win = _new_window(qtbot, settings)
    try:
        rec = _rec(win)
        qtbot.waitUntil(lambda: rec.dialog is not None, timeout=3000)
        rec.dialog.restore()
        assert _files(d)
        _cleanup(win)
        assert _files(d) == []
    finally:
        _cleanup(win)


def test_crash_recovery_discard(qtbot, settings, sample_pdf, tmp_path):
    d, _id = _crash_with_modified_copy(tmp_path, sample_pdf)
    win = _new_window(qtbot, settings)
    rec = _rec(win)
    qtbot.waitUntil(lambda: rec.dialog is not None and rec.dialog.isVisible(), timeout=3000)
    qtbot.mouseClick(rec.dialog.discard_button, Qt.MouseButton.LeftButton)
    assert _files(d) == []
    assert win.views() == []


def test_crash_recovery_later_keeps_files(qtbot, settings, sample_pdf, tmp_path):
    d, _id = _crash_with_modified_copy(tmp_path, sample_pdf)
    win = _new_window(qtbot, settings)
    rec = _rec(win)
    qtbot.waitUntil(lambda: rec.dialog is not None and rec.dialog.isVisible(), timeout=3000)
    before = _files(d)
    qtbot.mouseClick(rec.dialog.later_button, Qt.MouseButton.LeftButton)
    assert not rec.dialog.isVisible()
    assert _files(d) == before and win.views() == []


def test_live_pid_entry_ignored(qtbot, settings, sample_pdf, tmp_path):
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        doc = Document.open(sample_pdf)
        _edit(doc)
        _crash_entry(d, doc, sample_pdf, pid=proc.pid)
        doc.close()
        win = _new_window(qtbot, settings)
        qtbot.wait(50)
        assert _rec(win).dialog is None
        assert len(_files(d)) == 2
    finally:
        proc.kill()
        proc.wait()


def test_corrupt_json_still_restorable(qtbot, settings, sample_pdf, tmp_path):
    d, entry_id = _crash_with_modified_copy(tmp_path, sample_pdf)
    (d / f"{entry_id}.json").write_text("{{{")
    win = _new_window(qtbot, settings)
    try:
        rec = _rec(win)
        qtbot.waitUntil(lambda: rec.dialog is not None, timeout=3000)
        assert rec.dialog.tree.topLevelItemCount() == 1
        views = rec.restore_entries(rec.dialog.entries)
        assert len(views) == 1
        assert views[0].document.path is None and views[0].document.is_modified
        assert "MODIFICA DI PROVA" in views[0].document.page_text(0)
    finally:
        _cleanup(win)


def test_restore_encrypted_copy_asks_password(viewer_window, encrypted_pdf, monkeypatch):
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    doc = Document.open(encrypted_pdf, "segreta")
    _edit(doc)
    _crash_entry(d, doc, encrypted_pdf)
    doc.close()
    asked = []
    monkeypatch.setattr(rec, "ask_password", lambda name, retry=False: asked.append(retry) or "segreta")
    entries = rec.orphans()
    views = rec.restore_entries(entries)
    assert asked == [False]
    assert views and views[0].document.is_protected
    assert "MODIFICA DI PROVA" in views[0].document.page_text(0)


def test_restore_missing_folder_leaves_path_empty(viewer_window, sample_pdf, tmp_path):
    rec = _rec(viewer_window)
    d = Path(os.environ["FOLIANT_RECOVERY_DIR"])
    doc = Document.open(sample_pdf)
    _edit(doc)
    _crash_entry(d, doc, str(tmp_path / "sparita" / "doc.pdf"))
    doc.close()
    views = rec.restore_entries(rec.orphans())
    assert views[0].document.path is None
    assert views[0].document.display_name == "doc.pdf"
