"""Un file aperto una volta sola (ui/open_files.py, core/openlock.py): nella stessa finestra, in
un'altra finestra dello stesso processo (Ctrl+N), in un altro processo Foliant (anche un secondo
avvio mentre il primo è occupato). Il blocco segue il documento (Salva con nome, ricarica), si libera
alla chiusura e dopo un crash, e su disco non lascia né nomi né percorsi dei file."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

import foliant
from tests.conftest import has_mode, make_pdf

ROOT = Path(foliant.__file__).resolve().parent.parent      # cartella del progetto (contiene foliant/)


def _close_all_windows():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from foliant.ui.main_window import app_windows
    for w in app_windows():
        for view in w.views():
            w.close_view(view, ask=False)
        w.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _window(settings):
    from foliant.ui.main_window import MainWindow, register_window
    w = register_window(MainWindow(settings, load_modules=False))
    w.show()
    return w


def _locks() -> list[str]:
    """Blocchi su disco nella cartella del test (isolata: XDG_RUNTIME_DIR o %LOCALAPPDATA%)."""
    from foliant.core import openlock
    folder = openlock.folder()
    return sorted(p.name for p in folder.glob("*" + openlock.LOCK_SUFFIX)) if folder else []


def _modify(view):
    with view.document.change("Prova") as pdf:
        pdf[0].insert_text((72, 300), "Modificato", fontsize=14)


# ------------------------------------------------------------------ stesso processo
def test_same_file_in_the_same_window_opens_once(window, tmp_path):
    """Lo stesso file scritto in modi diversi (relativo, «..», collegamento simbolico) è sempre la
    stessa scheda; anche `add_document` con un documento dello stesso file non ne crea un'altra."""
    from foliant.core.document import Document
    a = make_pdf(tmp_path / "contratto.pdf")
    first = window.open_path(a)
    window.show_home()
    variants = [a, str(tmp_path / "altra" / ".." / "contratto.pdf")]
    if sys.platform != "win32":                     # su Windows i collegamenti richiedono privilegi
        os.symlink(a, tmp_path / "collegamento.pdf")
        variants.append(str(tmp_path / "collegamento.pdf"))
    for variant in variants:
        assert window.open_path(variant) is first
        assert window.current_view() is first
    assert window.tabs.count() == 1
    doc = Document.open(a)
    assert window.add_document(doc) is first and doc.closed
    assert window.tabs.count() == 1 and len(_locks()) == 1


def test_names_differing_only_in_case_are_the_same_file_on_windows(window, tmp_path, monkeypatch):
    """Su Windows i nomi non distinguono maiuscole e minuscole: `normalize_path` usa `normcase`
    (su Linux lo si simula come su Windows)."""
    from foliant.core import platform
    if not platform.IS_WINDOWS:
        monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())
    a = make_pdf(tmp_path / "Contratto.pdf")
    assert platform.normalize_path(a) == platform.normalize_path(str(tmp_path / "CONTRATTO.PDF"))
    view = window.open_path(a)
    assert window.open_path(str(tmp_path / "CONTRATTO.PDF")) is view
    assert window.tabs.count() == 1


def test_second_window_shows_the_tab_of_the_first(settings, tmp_path):
    """Ctrl+N: il file già aperto nella prima finestra non si riapre nella seconda; va in primo piano
    la prima con la sua scheda, anche per un file consegnato da un altro avvio (istanza singola)."""
    from foliant.ui.instance import open_request
    a = make_pdf(tmp_path / "a.pdf", pages=4)
    first = _window(settings)
    try:
        view = first.open_path(a)
        first.show_home()
        second = first.new_window()
        assert second.open_path(a) is view
        assert second.tabs.count() == 0 and first.tabs.count() == 1 and first.current_view() is view
        assert open_request({"files": [(a, 3)], "new_window": False, "command": None}, settings, second) is first
        assert second.tabs.count() == 0 and view.current_page == 2
        # «Riapri scheda chiusa» nella seconda finestra di un file aperto nella prima: nessuna copia
        b = make_pdf(tmp_path / "b.pdf")
        other = second.open_path(b)
        second.close_view(other)
        assert first.open_path(b) is not None
        second.reopen_closed()
        assert second.tabs.count() == 0 and first.tabs.count() == 2
    finally:
        _close_all_windows()


def test_save_as_and_save_copy_refuse_a_file_open_elsewhere(window, settings, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    a, b = make_pdf(tmp_path / "a.pdf"), make_pdf(tmp_path / "b.pdf")
    va = window.open_path(a)
    other = _window(settings)
    try:
        other.open_path(b)                          # in un'altra finestra dello stesso processo
        before = Path(b).read_bytes()
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(
            lambda *args, **k: warnings.append(args[2]) or QMessageBox.StandardButton.Ok))
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *args, **k: b)
        _modify(va)
        assert not window.save_view(va, save_as=True)
        assert not window.save_copy(va)
        assert Path(b).read_bytes() == before and str(va.document.path) == a
        assert len(warnings) == 2 and all("b.pdf" in w for w in warnings)
        # sul file stesso del documento Salva con nome resta possibile
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *args, **k: a)
        assert window.save_view(va, save_as=True) and not va.document.is_modified
    finally:
        _close_all_windows()


def test_save_as_moves_the_lock_to_the_new_file(window, settings, tmp_path, monkeypatch):
    """Dopo Salva con nome il blocco vale per il nuovo percorso e libera il vecchio; un documento
    nuovo salvato la prima volta prende il blocco del suo file."""
    from foliant.ui import open_files
    a = make_pdf(tmp_path / "a.pdf")
    target = tmp_path / "nuovo.pdf"
    view = window.open_path(a)
    before = _locks()
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *args, **k: str(target))
    assert window.save_view(view, save_as=True)
    assert open_files.view_for(target) is view and open_files.view_for(a) is None
    after = _locks()
    assert len(after) == 1 and after != before
    other = _window(settings)
    try:
        old = other.open_path(a)                    # il vecchio file è libero
        assert old is not None and old is not view and other.tabs.count() == 1
        assert other.open_path(str(target)) is view and other.tabs.count() == 1
        new = window.new_document()
        third = tmp_path / "terzo.pdf"
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *args, **k: str(third))
        assert window.save_view(new)
        assert open_files.view_for(third) is new and len(_locks()) == 3
    finally:
        _close_all_windows()


def test_closing_tab_window_or_reloading_keeps_one_lock(window, settings, tmp_path):
    """La chiusura della scheda o della finestra cancella il blocco; la ricarica dal disco (viewer)
    lo passa alla nuova scheda."""
    from foliant.modules.viewer import reload as R
    from foliant.ui import open_files
    a = make_pdf(tmp_path / "a.pdf")
    view = window.open_path(a)
    ctl = window.ctx.viewer_parts.get("reload") or R.install(window.ctx)
    new = ctl.reload_view(view)
    assert new is not None and new is not view and window.tabs.count() == 1
    assert open_files.view_for(a) is new and len(_locks()) == 1
    window.close_view(new)
    assert _locks() == [] and open_files.view_for(a) is None
    other = _window(settings)
    try:
        assert other.open_path(a) is not None and len(_locks()) == 1
        assert other.close()
        assert _locks() == [] and open_files.view_for(a) is None
        assert window.open_path(a) is not None
    finally:
        _close_all_windows()


def test_no_name_or_path_on_disk_while_the_file_is_open(window, tmp_path):
    """Riservatezza: mentre il file è aperto i blocchi (e ogni altro file scritto da Foliant nelle
    cartelle dell'utente) non contengono il nome, la cartella o un'impronta semplice del percorso;
    la cartella dei blocchi è privata e chiudendo il file il blocco sparisce."""
    from foliant.core import openlock
    from tests.test_no_file_traces import _needles, _scan
    token = uuid.uuid4().hex
    folder = tmp_path / f"cartella riservata {token}"
    folder.mkdir()
    path = folder / f"contratto riservato {token}.pdf"
    make_pdf(path)
    view = window.open_path(str(path))
    locks = openlock.folder()
    assert locks is not None and len(_locks()) == 1
    assert has_mode(locks, 0o700) and has_mode(locks / openlock.KEY_NAME, 0o600)
    needles = _needles({"documento": path, "cartella": folder}, {"segreto": token}, {})
    roots = {Path(os.environ[v]) for v in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                                          "APPDATA", "LOCALAPPDATA", "FOLIANT_RECOVERY_DIR") if os.environ.get(v)}
    roots.add(Path(window.settings.fileName()).parent)
    traces = []                                 # contenuti e nomi dei file (anche le impronte)
    for root in roots:
        if root.is_dir():
            traces += _scan(root, {"segreto": token}, needles)
    assert not traces, traces
    window.close_view(view)
    assert _locks() == []


# ------------------------------------------------------------------ altri processi
def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class _Child:
    """Processo Python con le righe dello stdout lette in un thread (anche su Windows)."""

    def __init__(self, code: str, *args: str, log: Path):
        self.log = open(log, "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-c", code, *args], cwd=ROOT, env=_env(), text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def wait_for(self, prefix: str, timeout: float = 90) -> str:
        end = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.05, end - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"«{prefix}» non arrivato:\n" + Path(self.log.name).read_text("utf-8")[-3000:])
            if line.startswith(prefix):
                return line[len(prefix):].strip()

    def finish(self, timeout: float = 60) -> int:
        """Attende la fine del processo e ne restituisce il codice."""
        try:
            return self.proc.wait(timeout)
        finally:
            self.kill()

    def kill(self) -> None:
        """Termina il processo se è ancora vivo (fine del test, anche dopo un errore)."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.log.close()


# tiene il blocco di uno o più file come un altro processo Foliant, finché stdin resta aperto
LOCKER = textwrap.dedent('''
    import sys
    from foliant.core import openlock
    print("BLOCCO", " ".join(type(openlock.acquire(p)).__name__ for p in sys.argv[1:]), flush=True)
    sys.stdin.read()
''')

# un altro processo Foliant con una finestra che ha aperto il file e ascolta sul canale del processo
HOLDER = textwrap.dedent(r'''
    import json, os, sys
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(["foliant"])
    from foliant.ui import instance
    from foliant.ui.main_window import MainWindow, register_window
    path, settings_file, stop = sys.argv[1:4]
    settings = QSettings(settings_file, QSettings.Format.IniFormat)
    window = register_window(MainWindow(settings, load_modules=False))
    window.show()

    def handle(request):
        instance.handle_request(request, settings)
        views = window.views()
        print("RICHIESTA " + json.dumps({"activate": bool(request.get("activate")), "tabs": len(views),
                                         "shown": bool(views) and window.current_view() is views[0]}), flush=True)

    server = instance.InstanceServer(instance.process_channel(os.getpid()), app)
    server.requestReceived.connect(handle)
    assert server.listen()
    window.open_path(path)
    window.show_home()                  # la richiesta dell'altro processo deve riportare la scheda
    print("PRONTO %d" % len(window.views()), flush=True)

    def poll():
        if os.path.exists(stop):
            window.close()
            app.quit()
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(50)
    app.exec()
    print("FINE", flush=True)
''')

# secondo avvio vero (foliant.app.main) con le finestre modali e il ciclo degli eventi simulati
SECOND = textwrap.dedent(r'''
    import json, sys
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    QDialog.exec = lambda self, *a, **k: 0                       # nessuna finestra modale blocca il test
    shown = []
    QMessageBox.exec = lambda self, *a, **k: shown.append(self.text()) or 0
    QApplication.exec = lambda *a, **k: 0
    from foliant.app import main
    code = main(["foliant", *sys.argv[1:]])
    from foliant.ui.main_window import app_windows
    print("ESITO " + json.dumps({"code": code, "messages": shown,
                                  "tabs": sum(len(w.views()) for w in app_windows())}), flush=True)
''')

# primo avvio vero (istanza singola) che apre il file e poi resta occupato senza gestire eventi
BUSY = textwrap.dedent(r'''
    import os, sys, time
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    QDialog.exec = lambda self, *a, **k: 0                       # nessuna finestra modale blocca il test
    QMessageBox.exec = lambda self, *a, **k: 0
    path, release = sys.argv[1:3]
    _orig_exec = QApplication.exec

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def scenario():
        from foliant.ui.main_window import app_windows
        window = app_windows()[-1]
        spin(0.3)
        print("PRONTO %d" % len(window.views()), flush=True)
        while not os.path.exists(release):           # occupato: nessun evento
            time.sleep(0.05)
        spin(3)                                      # ora riceve le richieste rimaste in attesa
        print("SCHEDE %d" % sum(len(w.views()) for w in app_windows()), flush=True)
        for w in app_windows():
            w.close()
        QApplication.instance().quit()

    def _exec(self=None):
        QTimer.singleShot(0, scenario)
        try:
            return _orig_exec()
        except TypeError:
            return _orig_exec(QApplication.instance())

    QApplication.exec = _exec
    from foliant.app import main
    sys.exit(main(["foliant", path]))
''')


def test_lock_of_a_killed_process_does_not_block(window, tmp_path):
    """Un altro processo ha il file: non lo si apre (avviso), finché quel processo non termina,
    anche male (kill): il blocco rimasto si riconosce dal processo morto e si toglie."""
    from foliant.core import openlock
    a = make_pdf(tmp_path / "a.pdf")
    locker = _Child(LOCKER, a, log=tmp_path / "blocco.log")
    try:
        assert locker.wait_for("BLOCCO") == "FileLock"
        holder = openlock.holder(a)
        assert holder is not None and holder.pid == locker.proc.pid
        assert window.open_path(a) is None and window.tabs.count() == 0
        texts = [n.text for n in window.notifications.notifications()]
        assert any("a.pdf" in t for t in texts), texts
    finally:
        locker.kill()                               # come un crash: nessuna pulizia
    assert len(_locks()) == 1                       # il blocco del processo morto è rimasto…
    assert openlock.holder(a) is None               # …ma non tiene più il file (e si toglie)
    view = window.open_path(a)
    assert view is not None and len(_locks()) == 1
    window.close_view(view)
    assert _locks() == []


def test_request_for_a_file_open_in_another_process(settings, tmp_path, monkeypatch):
    """Richiesta dell'istanza singola per un file che un terzo processo ha aperto: nessuna scheda,
    un avviso, e la finestra non va in primo piano (ci va quella dell'altro processo)."""
    from foliant.ui import open_files
    from foliant.ui.instance import open_request
    a = make_pdf(tmp_path / "terzo.pdf")
    b = make_pdf(tmp_path / "libero.pdf")
    window = _window(settings)
    raised = []
    monkeypatch.setattr(open_files, "raise_window", lambda w: raised.append(w))
    locker = _Child(LOCKER, a, log=tmp_path / "blocco.log")
    try:
        assert locker.wait_for("BLOCCO") == "FileLock"
        assert open_request({"files": [(a, None)], "new_window": False, "command": None}, settings, window) is window
        assert window.tabs.count() == 0 and raised == []
        assert any("terzo.pdf" in n.text for n in window.notifications.notifications())
        # con un file libero insieme, si apre quello e la finestra va davanti
        assert open_request({"files": [(a, None), (b, None)], "new_window": False, "command": None},
                            settings, window) is window
        assert [v.document.display_name for v in window.views()] == ["libero.pdf"] and raised == [window]
    finally:
        locker.kill()
        _close_all_windows()


def test_second_process_does_not_open_a_file_open_in_another(tmp_path):
    """Due processi veri: il primo tiene il file in una finestra; il secondo avvio (foliant.app.main)
    non lo apre, chiede al primo di mostrarne la scheda, lo dice con un messaggio ed esce."""
    token = uuid.uuid4().hex[:8]
    a = make_pdf(tmp_path / f"condiviso {token}.pdf")
    stop = tmp_path / "ferma"
    holder = _Child(HOLDER, a, str(tmp_path / "primo.ini"), str(stop), log=tmp_path / "primo.log")
    try:
        assert holder.wait_for("PRONTO") == "1"
        r = subprocess.run([sys.executable, "-c", SECOND, a], cwd=ROOT, env=_env(), capture_output=True,
                           text=True, timeout=120)
        out = [line for line in r.stdout.splitlines() if line.startswith("ESITO ")]
        assert out, r.stdout[-2000:] + r.stderr[-3000:]
        result = json.loads(out[-1][len("ESITO "):])
        assert result["code"] == 0 and result["tabs"] == 0, result
        assert len(result["messages"]) == 1 and f"condiviso {token}.pdf" in result["messages"][0], result
        shown = json.loads(holder.wait_for("RICHIESTA"))
        assert shown == {"activate": True, "tabs": 1, "shown": True}
        assert token not in r.stderr                # nessun nome di file nei messaggi del processo
        stop.write_text("")
        holder.wait_for("FINE")
        assert holder.finish() == 0
    finally:
        holder.kill()
    assert _locks() == []                           # uscito il primo processo, nessun blocco resta


def test_second_start_while_the_first_is_busy_does_not_open_twice(tmp_path):
    """Il problema noto del secondo avvio: il primo Foliant (istanza singola) ha aperto il file ed è
    occupato per più di 6 s; il secondo avvio non riceve risposta, ma trova il file bloccato: non lo
    apre, lo dice ed esce. Tornato libero, il primo riceve le richieste rimaste e non apre copie."""
    a = make_pdf(tmp_path / "occupato.pdf")
    release = tmp_path / "libero"
    first = _Child(BUSY, a, str(release), log=tmp_path / "primo.log")
    try:
        assert first.wait_for("PRONTO") == "1"
        started = time.monotonic()
        r = subprocess.run([sys.executable, "-c", SECOND, a], cwd=ROOT, env=_env(), capture_output=True,
                           text=True, timeout=120)
        out = [line for line in r.stdout.splitlines() if line.startswith("ESITO ")]
        assert out, r.stdout[-2000:] + r.stderr[-3000:]
        result = json.loads(out[-1][len("ESITO "):])
        assert result["code"] == 0 and result["tabs"] == 0, result
        assert len(result["messages"]) == 1 and "occupato.pdf" in result["messages"][0], result
        assert time.monotonic() - started >= 6      # ha davvero atteso l'istanza occupata
        release.write_text("")
        assert first.wait_for("SCHEDE") == "1"
        assert first.finish() == 0
    finally:
        first.kill()
    assert _locks() == []


@pytest.mark.skipif(sys.platform == "win32", reason="su Windows la cartella è in %LOCALAPPDATA%, privata per gli ACL")
def test_runtime_dir_is_always_private(tmp_path, monkeypatch):
    """I blocchi stanno solo in una cartella privata (0700): mai in una cartella condivisa, dove un
    altro utente potrebbe leggerne i nomi o occuparli prima."""
    from foliant.core import platform
    private = tmp_path / "privata"
    private.mkdir(mode=0o700)
    shared = tmp_path / "condivisa"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(private))
    assert platform.runtime_dir() == private
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
    assert platform.runtime_dir() is None and platform.runtime_dir([str(private)]) == private
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert platform.runtime_dir([str(shared)]) is None


def test_activation_request_carries_only_the_lock_name():
    """La richiesta a un altro processo di mostrare un file porta il nome del blocco (64 cifre
    esadecimali), mai un percorso; qualunque altra cosa è scartata."""
    from foliant.ui.instance import clean_request
    name = "ab" * 32
    assert clean_request({"activate": name})["activate"] == name
    assert clean_request({"files": []})["activate"] is None
    for bad in ("/home/utente/contratto.pdf", "AB" * 32, "ab" * 31, 7):
        with pytest.raises(ValueError):
            clean_request({"activate": bad})


def test_lock_folder_and_key(tmp_path, monkeypatch):
    """Cartella privata, chiave casuale 0600 che si rinnova quando nessun file è aperto; senza
    una cartella privata (es. XDG_RUNTIME_DIR condivisa) nessun blocco su disco."""
    from foliant.core import openlock, platform
    a = make_pdf(tmp_path / "a.pdf")
    first = openlock.acquire(a)
    folder = openlock.folder()
    key = (folder / openlock.KEY_NAME).read_bytes()
    assert isinstance(first, openlock.FileLock) and len(key) == openlock.KEY_BYTES
    assert isinstance(openlock.acquire(a), openlock.Holder)        # già tenuto da questo processo
    assert openlock.holder(a) is None                              # «altro processo»: nessuno
    name = first.name
    first.release()
    assert not (folder / (name + openlock.LOCK_SUFFIX)).exists()
    second = openlock.acquire(a)
    assert second.name != name and (folder / openlock.KEY_NAME).read_bytes() != key   # chiave nuova
    second.release()
    if not platform.IS_WINDOWS:
        shared = tmp_path / "condivisa"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
        monkeypatch.setattr(platform, "runtime_dir", lambda fallbacks=(): None)
        assert openlock.folder() is None and openlock.acquire(a) is None
        assert not list(shared.iterdir())
