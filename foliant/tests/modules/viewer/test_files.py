"""VIS-03 — Gestione dei file locali."""

from __future__ import annotations

import errno
import os

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from foliant.modules.viewer import files_engine as F

from ...conftest import make_pdf


# ------------------------------------------------------------------ fixture
@pytest.fixture
def fwin(request, monkeypatch):
    monkeypatch.setenv("FOLIANT_VIEWER_PARTS", "navigation,files")
    return request.getfixturevalue("window")


@pytest.fixture
def ctl(fwin):
    from foliant.modules.viewer import files
    parts = getattr(fwin.ctx, "viewer_parts", {})
    c = parts.get("files") or files.install(fwin.ctx)
    c.errors = []
    c.show_error = c.errors.append
    return c


@pytest.fixture
def three_pdfs(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    return [make_pdf(folder / f"{n}.pdf", pages=i + 1) for i, n in enumerate(("uno", "due", "tre"))]


def _modify(view):
    with view.document.change("Prova") as pdf:
        pdf[0].insert_text((72, 300), "Modificato", fontsize=14)


def _tab_text(window, view):
    return window.tabs.tabText(window.tabs.indexOf(view))


# ------------------------------------------------------------------ motore
def test_validate_name_errors():
    assert F.validate_name("  buono.pdf ") == "buono.pdf"
    for bad in ("", "   ", "a/b.pdf", "a\0b.pdf", ".", ".."):
        with pytest.raises(F.FileOpError):
            F.validate_name(bad)
    with pytest.raises(F.FileOpError, match="troppo lungo"):
        F.validate_name("à" * 128 + ".pdf")  # 256 + 4 byte
    assert F.validate_name("a" * 251 + ".pdf")  # 255 byte esatti


def test_names_copy_and_auto_rename(tmp_path):
    p = tmp_path / "nota.pdf"
    p.write_bytes(b"x")
    assert F.with_suffix_of("nuovo", str(p)) == "nuovo.pdf"
    assert F.with_suffix_of("nuovo.txt", str(p)) == "nuovo.txt"
    assert os.path.basename(F.copy_name(str(p))) == "nota (copia).pdf"
    (tmp_path / "nota (copia).pdf").write_bytes(b"x")
    assert os.path.basename(F.copy_name(str(p))) == "nota (copia 2).pdf"
    assert os.path.basename(F.auto_rename_target(str(p))) == "nota (2).pdf"


def test_move_conflict_and_overwrite(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"AAA")
    b.write_bytes(b"BBB")
    with pytest.raises(F.NameConflict):
        F.move(str(a), str(b))
    assert a.read_bytes() == b"AAA" and b.read_bytes() == b"BBB"
    F.move(str(a), str(b), overwrite=True)
    assert not a.exists() and b.read_bytes() == b"AAA"


def test_replace_in_list_keeps_position():
    assert F.replace_in_list(["/x", "/a", "/y"], "/a", "/b") == ["/x", "/b", "/y"]


# ------------------------------------------------- criteri di accettazione
def test_rename_open_tab_follows_title_and_save(fwin, ctl, sample_pdf, tmp_path):
    view = fwin.open_path(sample_pdf)
    new = ctl.rename_path(sample_pdf, "rinominato")
    expected = str(tmp_path / "rinominato.pdf")
    assert new == expected
    assert os.path.exists(expected) and not os.path.exists(sample_pdf)
    assert _tab_text(fwin, view) == "rinominato.pdf"
    assert str(view.document.path) == expected
    # Il Salva successivo scrive sul nuovo percorso.
    _modify(view)
    assert fwin.save_view(view)
    assert not os.path.exists(sample_pdf)
    with pymupdf.open(expected) as pdf:
        assert "Modificato" in pdf[0].get_text()


def test_rename_from_current_document_uses_dialog(fwin, ctl, sample_pdf, tmp_path, monkeypatch):
    fwin.open_path(sample_pdf)
    monkeypatch.setattr(ctl, "ask_new_name", lambda path: "dal menu.pdf")
    ctl.act_rename.trigger()
    assert (tmp_path / "dal menu.pdf").exists()
    assert fwin.ctx.current_document().display_name == "dal menu.pdf"


def test_rename_read_only_folder_reports_permission(fwin, ctl, tmp_path):
    folder = tmp_path / "solalettura"
    folder.mkdir()
    pdf = make_pdf(folder / "fisso.pdf")
    before = open(pdf, "rb").read()
    os.chmod(folder, 0o555)
    try:
        probe = folder / "prova"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            pytest.skip("eseguito come root: la cartella in sola lettura resta scrivibile")
        except PermissionError:
            pass
        assert ctl.rename_path(pdf, "nuovo.pdf") is None
        assert ctl.errors and "Permesso di scrittura" in ctl.errors[-1]
        assert os.listdir(folder) == ["fisso.pdf"]
        assert open(pdf, "rb").read() == before
    finally:
        os.chmod(folder, 0o755)


def test_rename_permission_error_keeps_file(fwin, ctl, sample_pdf, tmp_path, monkeypatch):
    """Anche quando i controlli preventivi passano (es. root, ACL), l'errore del sistema
    viene tradotto in un messaggio sul permesso mancante e il file resta intatto."""
    before = open(sample_pdf, "rb").read()

    def deny(src, dst):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(F.os, "replace", deny)
    assert ctl.rename_path(sample_pdf, "altro.pdf") is None
    assert "Permesso di scrittura mancante" in ctl.errors[-1]
    assert open(sample_pdf, "rb").read() == before and not (tmp_path / "altro.pdf").exists()


def test_trash_moves_file_to_system_trash_and_closes_tab(fwin, ctl, sample_pdf, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    content = open(sample_pdf, "rb").read()
    view = fwin.open_path(sample_pdf)
    asked = []
    monkeypatch.setattr(ctl, "ask_trash", lambda paths: asked.append(paths) or True)
    guarded = []                              # file già nel cestino: la scheda si chiude senza controlli
    guard = lambda v, info: guarded.append(v) or False
    fwin.ctx.add_guard("close_document", guard)
    try:
        done = ctl.trash_paths([sample_pdf])
    finally:
        fwin.ctx.remove_guard("close_document", guard)
    assert done == [sample_pdf] and asked and guarded == []
    assert not os.path.exists(sample_pdf)
    trashed = data / "Trash" / "files" / os.path.basename(sample_pdf)
    assert trashed.read_bytes() == content
    assert view not in fwin.views()


def test_trash_cancelled_keeps_everything(fwin, ctl, sample_pdf, monkeypatch):
    fwin.open_path(sample_pdf)
    monkeypatch.setattr(ctl, "ask_trash", lambda paths: False)
    monkeypatch.setattr(ctl, "move_to_trash", lambda p: pytest.fail("non deve spostare"))
    assert ctl.trash_paths([sample_pdf]) == []
    assert os.path.exists(sample_pdf) and len(fwin.views()) == 1


def test_trash_unavailable_asks_permanent_delete(fwin, ctl, sample_pdf, monkeypatch):
    monkeypatch.setattr(ctl, "ask_trash", lambda paths: True)
    monkeypatch.setattr(ctl, "move_to_trash", lambda p: False)
    monkeypatch.setattr(ctl, "ask_delete_permanently", lambda p: False)
    assert ctl.trash_paths([sample_pdf]) == [] and os.path.exists(sample_pdf)
    monkeypatch.setattr(ctl, "ask_delete_permanently", lambda p: True)
    assert ctl.trash_paths([sample_pdf]) == [sample_pdf] and not os.path.exists(sample_pdf)


def test_trash_unsaved_changes_warns_first(fwin, ctl, sample_pdf, monkeypatch):
    view = fwin.open_path(sample_pdf)
    _modify(view)
    warned = []
    monkeypatch.setattr(ctl, "ask_trash", lambda paths: True)
    monkeypatch.setattr(ctl, "ask_unsaved", lambda p, losing: warned.append(losing) or False)
    monkeypatch.setattr(ctl, "move_to_trash", lambda p: pytest.fail("non deve spostare"))
    assert ctl.trash_paths([sample_pdf]) == []
    assert warned == [True] and os.path.exists(sample_pdf) and view in fwin.views()
    # Confermando, il file va nel cestino e la scheda si chiude senza altre domande.
    monkeypatch.setattr(ctl, "ask_unsaved", lambda p, losing: True)
    trashed = []
    monkeypatch.setattr(ctl, "move_to_trash", lambda p: trashed.append(p) or os.unlink(p) is None)
    assert ctl.trash_paths([sample_pdf]) == [sample_pdf]
    assert trashed and view not in fwin.views()


def test_duplicate_three_files_byte_identical(fwin, ctl, three_pdfs, monkeypatch):
    monkeypatch.setattr(ctl, "ask_paths", lambda title: list(three_pdfs))
    created = ctl.duplicate_dialog()
    assert len(created) == 3
    folder = os.path.dirname(three_pdfs[0])
    for orig in three_pdfs:
        stem = os.path.splitext(os.path.basename(orig))[0]
        copy = os.path.join(folder, f"{stem} (copia).pdf")
        assert open(copy, "rb").read() == open(orig, "rb").read()
    # Una seconda duplicazione numera le copie.
    again = ctl.duplicate_paths(three_pdfs[:1])
    assert os.path.basename(again[0]) == "uno (copia 2).pdf"


def test_duplicate_current_document(fwin, ctl, sample_pdf, tmp_path):
    fwin.open_path(sample_pdf)
    ctl.act_duplicate.trigger()
    copy = tmp_path / "esempio (copia).pdf"
    assert copy.read_bytes() == open(sample_pdf, "rb").read()


@pytest.mark.parametrize("choice,expected", [("auto", "due (2).pdf"), ("cancel", None), ("replace", "due.pdf")])
def test_rename_conflict_choices(fwin, ctl, three_pdfs, monkeypatch, choice, expected):
    uno, due, _tre = three_pdfs
    data_uno, data_due = open(uno, "rb").read(), open(due, "rb").read()
    monkeypatch.setattr(ctl, "ask_conflict", lambda target: choice)
    result = ctl.rename_path(uno, "due.pdf")
    folder = os.path.dirname(uno)
    if expected is None:
        assert result is None
        assert open(uno, "rb").read() == data_uno and open(due, "rb").read() == data_due
    else:
        assert os.path.basename(result) == expected and not os.path.exists(uno)
        assert open(os.path.join(folder, expected), "rb").read() == data_uno
        if choice == "auto":
            assert open(due, "rb").read() == data_due


def test_replace_refused_when_target_is_open(fwin, ctl, three_pdfs, monkeypatch):
    uno, due, _tre = three_pdfs
    fwin.open_path(due)
    monkeypatch.setattr(ctl, "ask_conflict", lambda target: "replace")
    assert ctl.rename_path(uno, "due.pdf") is None
    assert os.path.exists(uno) and "aperto" in ctl.errors[-1]


@pytest.mark.parametrize("name", ["a/b.pdf", "x\0.pdf", "n" * 300 + ".pdf"])
def test_rename_invalid_name_explicit_error(fwin, ctl, sample_pdf, name):
    assert ctl.rename_path(sample_pdf, name) is None
    assert ctl.errors and os.path.exists(sample_pdf)


def test_move_to_folder_tab_follows(fwin, ctl, sample_pdf, tmp_path):
    dest = tmp_path / "archivio"
    dest.mkdir()
    view = fwin.open_path(sample_pdf)
    moved = ctl.move_paths([sample_pdf], str(dest))
    target = str(dest / "esempio.pdf")
    assert moved == [target] and os.path.exists(target) and not os.path.exists(sample_pdf)
    assert str(view.document.path) == target
    assert fwin.tabs.tabToolTip(fwin.tabs.indexOf(view)) == target


def test_move_unsaved_warns(fwin, ctl, sample_pdf, tmp_path, monkeypatch):
    dest = tmp_path / "altrove"
    dest.mkdir()
    view = fwin.open_path(sample_pdf)
    _modify(view)
    monkeypatch.setattr(ctl, "ask_folder", lambda: str(dest))
    monkeypatch.setattr(ctl, "ask_unsaved", lambda p, losing: False)
    assert ctl.move_current() == []
    assert os.path.exists(sample_pdf)
    monkeypatch.setattr(ctl, "ask_unsaved", lambda p, losing: True)
    assert ctl.move_current() == [str(dest / "esempio.pdf")]
    assert view.document.is_modified  # le modifiche restano da salvare sul nuovo percorso


def test_commands_disabled_for_unsaved_document(fwin, ctl, sample_pdf):
    assert not ctl.act_rename.isEnabled()
    fwin.new_document()
    assert all(not a.isEnabled() for a in ctl.current_actions)
    fwin.open_path(sample_pdf)
    assert all(a.isEnabled() for a in ctl.current_actions)
    assert ctl.act_duplicate_many.isEnabled()


def test_reveal_and_copy_path(fwin, ctl, sample_pdf, monkeypatch):
    fwin.open_path(sample_pdf)
    urls = []
    monkeypatch.setattr(ctl, "open_folder_url", lambda url: urls.append(url) or True)
    ctl.act_reveal.trigger()
    assert urls and urls[0].toLocalFile() == os.path.dirname(sample_pdf)
    ctl.act_copy_path.trigger()
    assert QApplication.clipboard().text() == sample_pdf


def test_menu_section(fwin, ctl, sample_pdf):
    fwin.main_menu.aboutToShow.emit()
    texts = [a.text() for a in fwin.main_menu.actions()]
    assert "File" in texts


def test_files_does_not_use_recent_list(fwin, ctl, sample_pdf, tmp_path, monkeypatch):
    """Riservatezza: il modulo non alimenta né consulta l'elenco dei recenti (nemmeno dopo
    rinomina, spostamento o cestino) e non aggiunge voci al menu dei recenti della Home."""
    calls = []
    for name in ("recent_files", "replace_recent", "remove_recent", "add_recent", "pin_recent",
                 "add_recent_menu_provider"):
        if hasattr(fwin.ctx, name):
            monkeypatch.setattr(fwin.ctx, name, lambda *a, _n=name, **k: calls.append(_n) or [])
    from foliant.modules.viewer import files
    files.install(fwin.ctx)                     # nessun fornitore del menu dei recenti
    fwin.open_path(sample_pdf)
    renamed = ctl.rename_path(sample_pdf, "zq7k-rinominato")
    dest = tmp_path / "altrove"
    dest.mkdir()
    [moved] = ctl.move_paths([renamed], str(dest))
    monkeypatch.setattr(ctl, "ask_trash", lambda paths: True)
    monkeypatch.setattr(ctl, "move_to_trash", lambda p: True)
    ctl.trash_paths([moved])
    assert calls == []
    assert not hasattr(ctl, "fill_recent_menu")
    for name in ("act_pin", "act_unpin", "ask_missing_recent", "remove_missing_recent"):
        assert not hasattr(ctl, name), name
    # nulla viene salvato: nelle impostazioni non restano nomi o cartelle dei file
    s = fwin.ctx.settings()
    s.sync()
    for key in s.allKeys():
        text = f"{key}={s.value(key)}"
        for trace in ("zq7k", "esempio.pdf", str(tmp_path)):
            assert trace not in text, key
