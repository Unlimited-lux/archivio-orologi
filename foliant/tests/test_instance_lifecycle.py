"""Istanza singola e ciclo di vita delle finestre: richieste arrivate durante l'avvio, file aperti
da fuori mentre una finestra si chiude, memoria restituita alla chiusura di una finestra con dei
documenti (NFR-06, NFR-31)."""
from __future__ import annotations


def _close_all_windows():
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from foliant.ui.main_window import app_windows
    for w in app_windows():
        for view in w.views():
            w.close_view(view, ask=False)
        w.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_server_handles_requests_as_soon_as_it_listens(qtbot, settings, monkeypatch):
    """Una richiesta di un secondo avvio arrivata subito dopo listen(), mentre main() apre ancora
    i suoi file (domanda della password, avviso di errore: cicli di eventi annidati), non va persa."""
    import os
    import uuid

    from PySide6.QtWidgets import QApplication

    from foliant import app as fapp
    from foliant.ui import instance

    class Stop(Exception):
        pass

    class App(QApplication):            # l'applicazione dei test, senza crearne un'altra
        def __new__(cls, argv):
            return QApplication.instance()
    for static in ("setApplicationName", "setOrganizationName", "setDesktopFileName"):
        monkeypatch.setattr(App, static, staticmethod(lambda *a: None))
    monkeypatch.setattr(fapp, "QApplication", App)
    monkeypatch.setattr(fapp, "QSettings", lambda: settings)
    monkeypatch.setattr(fapp, "set_language", lambda *a: None)
    monkeypatch.setattr(fapp, "install_qt_translations", lambda *a: None)
    name = f"foliant-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(instance, "instance_name", lambda: name)
    handled = []
    monkeypatch.setattr(instance, "open_request", lambda request, *a: handled.append(request))
    servers = []

    def listen(self):                   # appena in ascolto arriva un secondo avvio
        servers.append(self)
        self.requestReceived.emit({"files": [["b.pdf", None]], "new_window": False, "command": None})
        raise Stop
    monkeypatch.setattr(instance.InstanceServer, "listen", listen)
    try:
        try:
            fapp.main(["foliant"])
        except Stop:
            pass
        assert handled == [{"files": [["b.pdf", None]], "new_window": False, "command": None}]
    finally:
        for server in servers:
            server.deleteLater()


def test_files_from_outside_do_not_go_to_a_closing_window(qtbot, settings, sample_pdf, monkeypatch):
    """Mentre una finestra chiede se chiudersi (ciclo di eventi annidato) un file aperto da fuori
    va in un'altra finestra: in quella che si chiude sparirebbe con lei."""
    from foliant.ui.instance import open_request
    from foliant.ui.main_window import MainWindow, app_windows, register_window

    first = register_window(MainWindow(settings, load_modules=False))
    closing = register_window(MainWindow(settings, load_modules=False))
    first.show()
    closing.show()
    targets = []

    def guards(*_args):                 # durante le domande di chiusura arriva un file da fuori
        targets.append(open_request({"files": [(sample_pdf, None)], "new_window": False,
                                     "command": None}, settings))
        return True
    monkeypatch.setattr(closing.ctx, "run_guards", guards)
    try:
        assert closing.close()
        assert targets == [first] and first.tabs.count() == 1
    finally:
        _close_all_windows()
    assert app_windows() == []


def test_closing_a_window_with_documents_returns_memory(qtbot, settings, sample_pdf, monkeypatch):
    """NFR-06: chiudendo una finestra con dei documenti le schede muoiono con lei, ma la memoria
    liberata torna comunque al sistema (il rilascio non è legato alla finestra)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from foliant.ui import main_window
    calls = []
    monkeypatch.setattr(main_window, "release_memory", lambda: calls.append(1) or True)
    win = main_window.register_window(main_window.MainWindow(settings, load_modules=False))
    win.show()
    win.open_path(sample_pdf)
    assert win.close()
    for _ in range(2):
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qtbot.wait(20)
    assert calls == [1]


def test_instance_channel_is_always_in_a_private_folder(tmp_path, monkeypatch):
    """Senza XDG_RUNTIME_DIR il canale non finisce nella /tmp condivisa (un altro utente potrebbe
    occuparlo prima e ricevere i percorsi dei file): va nella cartella di runtime privata di Qt."""
    import os
    import stat

    from foliant.ui import instance

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    name = instance.instance_name()
    assert name and os.path.isabs(name)
    folder = os.path.dirname(name)
    assert os.stat(folder).st_uid == os.getuid() and not stat.S_IMODE(os.stat(folder).st_mode) & 0o077

    shared = tmp_path / "condivisa"                 # cartella leggibile da tutti: non va usata
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
    assert not instance.instance_name().startswith(str(shared))

    private = tmp_path / "privata"
    private.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(private))
    assert instance.instance_name() == str(private / "foliant-instance")
