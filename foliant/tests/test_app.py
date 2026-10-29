"""Riga di comando e istanza singola (NFR-31), senza avviare processi veri."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.conftest import make_pdf


def test_command_line_pages_files_and_unknown_options(tmp_path):
    from foliant.app import parse_args

    a = make_pdf(tmp_path / "a.pdf", pages=5)
    b = make_pdf(tmp_path / "b.pdf", pages=5)
    opts = parse_args(["--page", "3", a, b])
    assert opts.files == [(a, 3), (b, None)] and not opts.unknown
    assert parse_args([a, "-p", "2", b]).files == [(a, 2), (b, None)]         # opzioni tra i file
    assert parse_args(["--page=4", a]).files == [(a, 4)]
    assert parse_args([a + "#page=5", b + "#PAGE=2"]).files == [(a, 5), (b, 2)]
    assert parse_args([Path(a).as_uri() + "#page=2"]).files == [(a, 2)]   # file:///… anche con «C:\\»
    assert parse_args(["a.pdf", "../b.pdf"], cwd=str(tmp_path / "x")).files == [
        (str(tmp_path / "x" / "a.pdf"), None), (str(tmp_path / "b.pdf"), None)]
    hashed = tmp_path / "scheda#page=1.pdf"                                   # un nome vero con «#page=»
    hashed.write_bytes(b"")
    assert parse_args([str(hashed)]).files == [(str(hashed), None)]
    # le opzioni sconosciute sono segnalate e non diventano file da aprire
    opts = parse_args(["--sconosciuta", "-z", a])
    assert opts.unknown == ["--sconosciuta", "-z"] and opts.files == [(a, None)]
    assert parse_args(["--new-window"]).new_window
    req = parse_args(["-p", "3", a]).request()
    assert req == {"files": [[a, 3]], "new_window": False, "command": None}
    for bad in (["--page", "x", a], ["--page", "0", a]):
        with pytest.raises(SystemExit):
            parse_args(bad)


def test_second_start_hands_files_to_the_running_instance(window, qtbot, tmp_path):
    """Primo e secondo avvio nello stesso processo: il server dell'istanza attiva e il client del
    secondo avvio (send_request), che consegna percorsi e pagina e si ferma alla risposta."""
    from foliant.app import parse_args
    from foliant.ui.instance import InstanceServer, open_request, send_request

    a = make_pdf(tmp_path / "a.pdf", pages=5)
    b = make_pdf(tmp_path / "b.pdf", pages=5)
    name = f"foliant-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    assert not send_request(name, parse_args([a]).request(), 300)     # nessuna istanza: si parte da soli
    first = InstanceServer(name)
    assert first.listen()
    second = InstanceServer(name)
    assert not second.listen()                                        # nome già in uso da un'istanza viva
    received = []
    first.requestReceived.connect(lambda r: (received.append(r), open_request(r, window.settings, window)))
    try:
        assert send_request(name, parse_args(["--page", "3", a, b]).request())
        qtbot.waitUntil(lambda: window.tabs.count() == 2)
        assert [(v.document.display_name, v.current_page) for v in window.views()] == [("a.pdf", 2), ("b.pdf", 0)]
        # file già aperto: si attiva la sua scheda (e si va alla pagina chiesta), niente doppioni
        assert send_request(name, parse_args([a + "#page=5"]).request())
        qtbot.waitUntil(lambda: len(received) == 2)
        assert window.tabs.count() == 2
        assert window.current_view().document.display_name == "a.pdf" and window.current_view().current_page == 4
        # una richiesta malformata è scartata senza conseguenze
        assert not send_request(name, {"files": "nonsense"})
        assert len(received) == 2
    finally:
        first.close()
        second.close()
    # chiusa l'istanza, il socket rimasto non blocca il prossimo avvio
    third = InstanceServer(name)
    assert third.listen()
    third.close()


def test_request_goes_to_active_window_or_to_a_new_one(settings, sample_pdf, qtbot):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    from foliant.ui.instance import open_request
    from foliant.ui.main_window import MainWindow, app_windows, register_window

    first = register_window(MainWindow(settings, load_modules=False))
    first.show()
    try:
        assert open_request({"files": [(sample_pdf, None)], "new_window": False, "command": None}, settings) is first
        assert first.tabs.count() == 1
        other = open_request({"files": [], "new_window": True, "command": None}, settings)   # --new-window
        assert other is not first and other in app_windows() and other.current_view() is None
        assert other.settings is settings
    finally:
        for view in first.views():
            first.close_view(view, ask=False)
        for w in app_windows():
            w.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert app_windows() == []
