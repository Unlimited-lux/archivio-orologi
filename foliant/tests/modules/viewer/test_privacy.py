"""Regola di riservatezza del viewer: nessun nome, percorso o cronologia dei documenti resta
su disco tra un avvio e l'altro, e i dati delle versioni precedenti vengono cancellati."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from foliant.modules.viewer import fulltext, privacy
from foliant.modules.viewer.thumbnailer import THUMBNAILER_ENTRY
from foliant.ui.main_window import MainWindow

from .conftest import part

TOKEN = "zq7k"

# Chiavi del nucleo (recenti, ultima cartella, finestra): le corregge l'area del nucleo, non il viewer.
CORE_PREFIXES = ("recent", "Recent", "pinned", "Pinned", "lastDirectory", "window/", "home/")


def _pdf(path: Path, pages: int = 4) -> str:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1} Cliente Rossi", fontsize=14)
    doc.save(str(path))
    doc.close()
    return str(path)


def _new_window(qtbot, settings):
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1100, 760)
    win.show()
    return win


def _traces(roots: list[Path], token: str) -> list[str]:
    """File o cartelle il cui nome o contenuto contiene `token` (anche in UTF-16, come QSettings)."""
    found = []
    needles = (token.encode(), token.encode("utf-16-le"))
    for root in roots:
        if not root.exists():
            continue
        for p in [root, *root.rglob("*")]:
            if token in p.name:
                found.append(str(p))
            elif p.is_file() and not p.is_symlink():
                try:
                    data = p.read_bytes()
                except OSError:
                    continue
                if any(n in data for n in needles):
                    found.append(str(p))
    return found


def _viewer_settings_traces(settings, token: str) -> list[str]:
    settings.sync()
    return [k for k in settings.allKeys()
            if not k.startswith(CORE_PREFIXES) and (token in k or token in str(settings.value(k)))]


# ====================================================================== prova completa
def test_no_traces_after_a_full_session(qtbot, settings, tmp_path, monkeypatch):
    """Profilo isolato: si apre, si cerca, si indicizza, si modifica (copia di recupero), si
    chiude e si riapre la finestra. Nel profilo non resta il nome del file né della cartella
    (salvo le chiavi del nucleo, che non sono di quest'area)."""
    profile = tmp_path / "profilo"
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(profile / var.lower()))
    monkeypatch.setenv("FOLIANT_RECOVERY_DIR", str(profile / "recupero"))
    folder = tmp_path / f"cartella-{TOKEN}-unica"
    folder.mkdir()
    path = _pdf(folder / f"{TOKEN}-riservato.pdf", pages=12)
    _pdf(folder / f"{TOKEN}-altro.pdf", pages=2)

    win = _new_window(qtbot, settings)
    try:
        view = win.open_path(path)
        view.set_zoom(1.5)
        view.go_to_page(7)
        search = part(win, "search")
        search.search(f"{TOKEN} Rossi")                 # termine con il nome del file (prova a scatola chiusa)
        search.finish()
        search.search("Rossi")
        search.finish()
        assert search.history()[:2] == ["Rossi", f"{TOKEN} Rossi"]
        adv = part(win, "advsearch")
        adv.open()
        adv.create_index(str(folder))
        assert fulltext.list_indexes() == [f"cartella-{TOKEN}-unica"]
        with view.document.change("Prova") as pdf:
            pdf[0].insert_text((72, 300), "Modifica", fontsize=12)
        assert part(win, "recovery").autosave_now() == 1
        assert _traces([profile], TOKEN) == []          # nemmeno durante la sessione
        view.document._saved_state = view.document._state
        win.close_view(view)
        assert part(win, "session").reopen_closed_tab() is not None   # in memoria: funziona
        for v in list(win.views()):
            win.close_view(v)
        win.close()
        QApplication.processEvents()
    finally:
        fulltext.clear_indexes()                        # alla chiusura dell'app gli indici spariscono

    assert _traces([profile], TOKEN) == []
    assert _viewer_settings_traces(settings, TOKEN) == []
    assert _viewer_settings_traces(settings, str(folder)) == []

    again = _new_window(qtbot, settings)                # nuovo avvio: nulla viene ricordato
    try:
        QApplication.processEvents()
        assert again.views() == []
        assert part(again, "search").history() == []
        assert part(again, "session").closed_tabs == []
        assert fulltext.list_indexes() == []
        assert part(again, "recovery").orphans() == []
    finally:
        for v in list(again.views()):
            v.document._saved_state = v.document._state
            again.close_view(v)
    assert _traces([profile], TOKEN) == []


# ====================================================================== pulizia una tantum
def test_run_once_removes_legacy_settings_and_indexes(settings, tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "dati")}
    idx = fulltext.legacy_index_dir(env)
    idx.mkdir(parents=True)
    (idx / f"cartella-{TOKEN}.sqlite").write_bytes(b"SQLite format 3\x00" + TOKEN.encode())
    for key in privacy.LEGACY_KEYS:
        settings.setValue(key, f"/home/utente/{TOKEN}.pdf")
    settings.setValue("viewer/units", "mm")
    assert privacy.run_once(settings, env) is True
    for key in privacy.LEGACY_KEYS:
        assert not settings.contains(key)
    assert settings.value("viewer/units") == "mm"
    assert not idx.exists()
    assert privacy.run_once(settings, env) is False      # una volta sola


def _dead_pid() -> int:
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_orphan_temp_dirs_removed(tmp_path):
    base = tmp_path / "tmp"
    base.mkdir()
    old = time.time() - 2 * privacy.TEMP_MAX_AGE
    dead = _dead_pid()
    names = ("foliant-share-abc", "foliant-allegato-def", "foliant-share-nuova", "altro-programma",
             f"foliant-share-{dead}-x1", f"foliant-allegato-{os.getpid()}-x2")
    made = {}
    for name in names:
        d = base / name
        d.mkdir()
        (d / f"{TOKEN}-riservato.pdf").write_bytes(b"%PDF")
        made[name] = d
    for name in ("foliant-share-abc", "foliant-allegato-def", "altro-programma", f"foliant-allegato-{os.getpid()}-x2"):
        os.utime(made[name], (old, old))
    # vecchie senza pid e quella del processo terminato (anche se recente) vengono tolte;
    # quella di questo processo (vivo) resta anche se vecchia
    assert privacy.remove_orphan_temp_dirs(str(base)) == 3
    assert sorted(p.name for p in base.iterdir()) == sorted(
        ["altro-programma", "foliant-share-nuova", f"foliant-allegato-{os.getpid()}-x2"])


def test_temp_dirs_carry_the_process_id(viewer_window, sample_pdf):
    """Le cartelle temporanee nuove portano il pid: dopo un crash si riconoscono subito."""
    view = viewer_window.open_path(sample_pdf)
    share = part(viewer_window, "share")
    copy = share.private_copy(view)
    try:
        assert Path(copy).parent.name.startswith(f"foliant-share-{os.getpid()}-")
    finally:
        share.cleanup()
    assert not Path(copy).exists()


def test_legacy_thumbnailer_entry_removed_only_if_ours(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "dati")}
    [entry] = privacy.legacy_thumbnailer_paths(env)
    entry.parent.mkdir(parents=True)
    entry.write_text("[Thumbnailer Entry]\nExec=altro %u %o\n", encoding="utf-8")
    assert privacy.remove_legacy_thumbnailer(env) == 0 and entry.exists()   # non è nostro
    entry.write_text(THUMBNAILER_ENTRY, encoding="utf-8")
    assert privacy.remove_legacy_thumbnailer(env) == 1 and not entry.exists()


def test_startup_cleanup_never_raises(settings, monkeypatch):
    def boom(*a, **k):
        raise OSError("disco non disponibile")
    monkeypatch.setattr(privacy, "run_once", boom)
    monkeypatch.setattr(privacy, "remove_orphan_temp_dirs", boom)
    privacy.startup_cleanup(settings)                    # nessuna eccezione


@pytest.mark.parametrize("name", ["add_recent", "recent_files", "pin_recent", "is_pinned", "pinned_recent"])
def test_viewer_sources_do_not_call_recent_api(name):
    """I sorgenti del viewer non usano più le API dei recenti del contesto."""
    root = Path(privacy.__file__).parent
    users = [p.name for p in root.glob("*.py") if f"ctx.{name}(" in p.read_text(encoding="utf-8")]
    assert users == []
