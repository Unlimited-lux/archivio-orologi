"""SEC-32 Blocco dell'applicazione."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from foliant.modules import security
from foliant.modules.security import applock as L

CODE = "4471-vetrina"


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def lock(window, monkeypatch):
    c = security.CONTROLLER
    store = _Settings()
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    lk = c.applock
    lk.configure(True, CODE, 0, False)
    yield lk
    if lk.locked:
        lk.unlock()


def test_code_hash_is_salted_and_verifiable():
    a, b = L.hash_code(CODE), L.hash_code(CODE)
    assert a != b and CODE not in a
    assert L.verify_code(CODE, a) and not L.verify_code("0000", a)
    assert not L.verify_code(CODE, "rotto")
    assert L.check_code("12") and not L.check_code("1234")
    assert [L.delay_after(n) for n in (4, 5, 6, 7)] == [0, 2, 4, 8]


def test_lock_hides_titles_and_content(window, lock, sample_pdf):
    """Dato il blocco, tutte le finestre mostrano la schermata di blocco e nessun nome di
    documento compare nei titoli."""
    view = window.open_path(sample_pdf)
    name = view.document.display_name
    assert name in window.windowTitle()
    assert lock.lock()
    screen = lock.screens[window]
    assert screen.isVisible() and screen.geometry() == window.rect()
    assert window.windowTitle() == "Foliant"
    # un cambio di titolo durante il blocco (es. documento modificato) resta mascherato
    with view.document.change("Prova") as pdf:
        pdf[0].insert_text((72, 300), "modifica non salvata")
    QApplication.processEvents()
    assert name not in window.windowTitle()
    window.resize(window.width() + 40, window.height())
    QApplication.processEvents()
    assert screen.geometry() == window.rect()


def test_wrong_then_right_code_keeps_unsaved_changes(window, lock, sample_pdf):
    """Con un codice errato resta bloccato; con quello corretto torna com'era, senza perdere
    le modifiche non salvate."""
    view = window.open_path(sample_pdf)
    with view.document.change("Prova") as pdf:
        pdf[0].insert_text((72, 300), "modifica non salvata")
    lock.lock()
    screen = lock.screens[window]
    screen.code.setText("sbagliato")
    screen.try_unlock()
    assert lock.locked and "errato" in screen.message.text()
    screen.code.setText(CODE)
    screen.try_unlock()
    assert not lock.locked and not lock.screens
    assert view.document.is_modified and view.document.display_name in window.windowTitle()
    with view.document.lock:
        assert "modifica non salvata" in view.document.pdf[0].get_text()


def test_attempts_slow_down_after_five(window, lock):
    lock.lock()
    for _i in range(5):
        assert not lock.attempt("no")
    screen = lock.screens[window]
    assert not screen.code.isEnabled() and "secondi" in screen.message.text()
    assert not lock.attempt(CODE)            # in attesa: anche il codice giusto aspetta
    lock.retry_at = 0
    assert lock.attempt(CODE)


def test_input_blocked_while_locked(window, lock, sample_pdf):
    view = window.open_path(sample_pdf)
    fired = []
    from PySide6.QtGui import QAction, QKeySequence
    act = QAction("prova", window)
    act.setShortcut(QKeySequence("Ctrl+Shift+F9"))
    act.triggered.connect(lambda: fired.append(1))
    window.addAction(act)
    lock.lock()
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a")
    assert lock.eventFilter(view, ev) is True
    assert lock.eventFilter(lock.screens[window].code, ev) is False
    shortcut = QEvent(QEvent.Type.Shortcut)
    assert lock.eventFilter(act, shortcut) is True


def test_other_windows_hidden_and_restored(window, lock, qtbot, monkeypatch):
    panel = QWidget(None, Qt.WindowType.Tool)
    qtbot.addWidget(panel)
    panel.show()
    lock.lock()
    assert not panel.isVisible()
    lock.attempt(CODE)
    assert panel.isVisible()


def test_idle_timeout_locks(window, lock, qtbot):
    """Dato un blocco per inattività, quando passa il tempo senza input, allora si blocca."""
    lock.reload(timeout_ms=50)
    assert lock._filtering
    qtbot.waitUntil(lambda: lock.locked, timeout=2000)
    assert window.windowTitle() == "Foliant"


def test_no_event_filter_when_disabled(window, lock):
    lock.configure(False, None, 5, False)
    assert not lock._filtering and not lock.lock()


def test_startup_lock_before_files(window, lock, sample_pdf):
    """Con il blocco all'avvio, il file passato all'avvio si vede solo dopo lo sblocco."""
    lock.configure(True, None, 0, True)
    lock.start()
    view = window.open_path(sample_pdf)
    QApplication.processEvents()
    assert lock.locked and view.document.display_name not in window.windowTitle()
    assert lock.screens[window].isVisible()
    lock.attempt(CODE)
    assert view.document.display_name in window.windowTitle()


def test_preferences_page(window, lock, qtbot):
    page = L.LockPreferences(lock)
    qtbot.addWidget(page)
    assert page.enabled.isChecked()
    page.code.setText("abcd1")
    page.code2.setText("abcd2")
    assert not page.apply() and "coincidono" in page.error.text()
    page.code2.setText("abcd1")
    page.minutes.setValue(10)
    page.startup.setChecked(True)
    assert page.apply()
    assert lock.minutes() == 10 and lock.at_startup() and lock._filtering
    lock.lock()
    assert lock.attempt("abcd1")


def test_lock_action_without_code_asks_settings(window, monkeypatch):
    from foliant.modules.security import controller as C
    c = security.CONTROLLER
    store = _Settings()
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    seen = []

    def fake_run(dlg):
        seen.append(dlg)
        dlg.page.code.setText(CODE)
        dlg.page.code2.setText(CODE)
        dlg.accept()
        return dlg.result() == QDialog.DialogCode.Accepted

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert c.lock_app()
    assert len(seen) == 1 and c.applock.locked
    c.applock.attempt(CODE)
    assert not c.applock.locked
