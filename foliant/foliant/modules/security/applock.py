"""Blocco dell'applicazione (SEC-32).

Con il blocco attivo ogni finestra mostra una schermata senza contenuti né nomi dei
documenti; il titolo delle finestre diventa «Foliant» e l'input (tastiera, mouse,
scorciatoie) arriva solo alla schermata di blocco. I documenti restano aperti con le loro
modifiche e le elaborazioni in corso proseguono. Il codice è conservato solo come
impronta con sale (scrypt). Il blocco non cifra i file: per questo c'è SEC-31.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from foliant.api import _, icon, theme

KEY_HASH = "security/lock/hash"
KEY_MINUTES = "security/lock/minutes"
KEY_STARTUP = "security/lock/at_startup"
KEY_ENABLED = "security/lock/enabled"

APP_TITLE = "Foliant"
FREE_ATTEMPTS = 5

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_INPUT = {QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride, QEvent.Type.Shortcut,
          QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
          QEvent.Type.Wheel, QEvent.Type.ContextMenu, QEvent.Type.Drop, QEvent.Type.DragEnter}
_ACTIVITY = {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.Wheel}


# ------------------------------------------------------------------ codice

def hash_code(code: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(code.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode("ascii")    # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_code(code: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(code.encode("utf-8"), salt=base64.b64decode(salt), dklen=32,
                             n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_code(code: str) -> str:
    if len(code) < 4:
        return _("Il codice deve avere almeno 4 caratteri.")
    return ""


def delay_after(failures: int) -> float:
    """Attesa in secondi prima del prossimo tentativo: nulla per i primi 5 errori, poi
    raddoppia (2, 4, 8 … fino a 5 minuti)."""
    if failures < FREE_ATTEMPTS:
        return 0.0
    return float(min(300, 2 ** (failures - FREE_ATTEMPTS + 1)))


# ---------------------------------------------------------- schermata di blocco

class LockScreen(QWidget):
    """Copre l'intera finestra: nessun contenuto dei documenti è visibile."""

    def __init__(self, lock: AppLock, window: QWidget):
        super().__init__(window)
        self.lock = lock
        self.setObjectName("foliantLockScreen")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#foliantLockScreen {{ background: {theme.hex_color('surface')}; }}")
        lay = QVBoxLayout(self)
        lay.addStretch(2)
        box = QWidget()
        box.setFixedWidth(300)
        bl = QVBoxLayout(box)
        badge = QLabel()
        badge.setPixmap(icon("lock").pixmap(48, 48))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(badge)
        title = QLabel(_("Foliant è bloccato"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        title.setFont(f)
        bl.addWidget(title)
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setPlaceholderText(_("Codice di sblocco"))
        self.code.returnPressed.connect(self.try_unlock)
        bl.addWidget(self.code)
        self.button = QPushButton(_("Sblocca"))
        self.button.setDefault(True)
        self.button.clicked.connect(self.try_unlock)
        bl.addWidget(self.button)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet(f"color: {theme.hex_color('danger')};")
        bl.addWidget(self.message)
        row = QVBoxLayout()
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addLayout(row)
        lay.addStretch(3)
        self.setGeometry(window.rect())
        window.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.Resize and obj is self.parent():
            self.setGeometry(obj.rect())
        return False

    def try_unlock(self) -> None:
        self.lock.attempt(self.code.text())
        self.code.clear()

    def show_message(self, text: str) -> None:
        self.message.setText(text)

    def set_waiting(self, seconds: float) -> None:
        on = seconds <= 0
        self.code.setEnabled(on)
        self.button.setEnabled(on)
        if on:
            self.code.setFocus()


# ------------------------------------------------------------------ blocco

class AppLock(QObject):
    def __init__(self, settings_getter, parent=None):
        super().__init__(parent)
        self._settings = settings_getter
        self.locked = False
        self.failures = 0
        self.screens: dict[QWidget, LockScreen] = {}
        self.titles: dict[QWidget, str] = {}
        self.hidden: list[QWidget] = []
        self._masking = False
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.timeout.connect(self.lock)
        self.wait = QTimer(self)
        self.wait.setSingleShot(True)
        self.wait.timeout.connect(self._end_wait)
        self.retry_at = 0.0
        self._filtering = False
        app = QGuiApplication.instance()
        app.applicationStateChanged.connect(self._state_changed)
        self.reload()

    # ------------------------------------------------------------ impostazioni
    def _value(self, key, default=None):
        s = self._settings()
        return s.value(key, default) if s is not None else default

    @property
    def configured(self) -> bool:
        return bool(self._value(KEY_HASH, "")) and str(self._value(KEY_ENABLED, "false")).lower() in ("true", "1")

    def minutes(self) -> int:
        try:
            return int(self._value(KEY_MINUTES, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def at_startup(self) -> bool:
        return str(self._value(KEY_STARTUP, "false")).lower() in ("true", "1")

    def configure(self, enabled: bool, code: str | None, minutes: int, at_startup: bool) -> None:
        s = self._settings()
        if s is None:
            return
        if code:
            s.setValue(KEY_HASH, hash_code(code))
        s.setValue(KEY_ENABLED, bool(enabled))
        s.setValue(KEY_MINUTES, int(minutes))
        s.setValue(KEY_STARTUP, bool(at_startup))
        self.reload()

    def _filter(self, on: bool) -> None:
        """Il filtro sugli eventi dell'applicazione costa a ogni evento: è installato solo
        se serve (blocco attivo o conteggio dell'inattività)."""
        if on != self._filtering:
            app = QApplication.instance()
            (app.installEventFilter if on else app.removeEventFilter)(self)
            self._filtering = on

    def reload(self, timeout_ms: int | None = None) -> None:
        """Riprogramma il blocco per inattività (`timeout_ms` per i test)."""
        self.idle.stop()
        ms = timeout_ms if timeout_ms is not None else self.minutes() * 60_000
        if self.configured and ms > 0:
            self.idle.setInterval(ms)
            if not self.locked:
                self.idle.start()
        else:
            self.idle.setInterval(0)
        self._filter(self.locked or self.idle.interval() > 0)

    def start(self) -> None:
        """All'avvio: blocca subito se richiesto (prima che i file passati si vedano)."""
        if self.configured and self.at_startup():
            self.lock()

    # ------------------------------------------------------------------ stato
    def _windows(self) -> list[QWidget]:
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, QMainWindow)]

    def lock(self) -> bool:
        if not self.configured:
            return False
        if self.locked:
            return True
        self.locked = True
        self.idle.stop()
        self._filter(True)
        for w in QApplication.topLevelWidgets():
            if w.isVisible() and not isinstance(w, QMainWindow) and w.isWindow():
                self.hidden.append(w)
                w.hide()
        for win in self._windows():
            self.titles[win] = win.windowTitle()
            self._mask(win)
            win.windowTitleChanged.connect(self._title_changed)
            screen = LockScreen(self, win)
            self.screens[win] = screen
            screen.show()
            screen.raise_()
            screen.code.setFocus()
        return True

    def attempt(self, code: str) -> bool:
        if not self.locked:
            return True
        if time.monotonic() < self.retry_at:
            return False
        if verify_code(code, self._value(KEY_HASH, "")):
            self.unlock()
            return True
        self.failures += 1
        delay = delay_after(self.failures)
        for screen in self.screens.values():
            if delay:
                screen.show_message(_("Codice errato. Riprova tra {s} secondi.").format(s=int(delay)))
                screen.set_waiting(delay)
            else:
                screen.show_message(_("Codice errato."))
        if delay:
            self.retry_at = time.monotonic() + delay
            self.wait.start(int(delay * 1000))
        return False

    def _end_wait(self) -> None:
        for screen in self.screens.values():
            screen.set_waiting(0)
            screen.show_message("")

    def unlock(self) -> None:
        self.locked = False
        self.failures = 0
        self.retry_at = 0.0
        for win, screen in list(self.screens.items()):
            try:
                win.windowTitleChanged.disconnect(self._title_changed)
            except (RuntimeError, TypeError):
                pass
            win.removeEventFilter(screen)
            screen.hide()
            screen.deleteLater()
            self._masking = True
            win.setWindowTitle(self.titles.get(win, win.windowTitle()))
            self._masking = False
        self.screens.clear()
        self.titles.clear()
        for w in self.hidden:
            try:
                w.show()
            except RuntimeError:
                pass
        self.hidden.clear()
        self.reload()

    def _mask(self, win: QWidget) -> None:
        self._masking = True
        win.setWindowTitle(APP_TITLE)
        self._masking = False

    def _title_changed(self, title: str) -> None:
        if self._masking or not self.locked:
            return
        win = self.sender()
        if win is None:
            return
        self.titles[win] = title           # il titolo vero torna allo sblocco
        self._mask(win)

    def _state_changed(self, state) -> None:
        if state == Qt.ApplicationState.ApplicationSuspended:
            self.lock()

    # -------------------------------------------------------------- input
    def _inside_lock(self, obj) -> bool:
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if isinstance(w, LockScreen):
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if self.locked:
            if t in _INPUT and not self._inside_lock(obj):
                return True                        # nessun input ai documenti, scorciatoie comprese
            if t == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow() \
                    and not isinstance(obj, QMainWindow) and not self._inside_lock(obj):
                QTimer.singleShot(0, lambda o=obj: self._hide_late(o))
        elif t in _ACTIVITY and self.idle.interval() > 0:
            self.idle.start()                      # attività: ricomincia il conto
        return False

    def _hide_late(self, w: QWidget) -> None:
        if self.locked and w.isVisible():
            self.hidden.append(w)
            w.hide()


# ---------------------------------------------------------------- preferenze

class LockPreferences(QWidget):
    """Preferenze › Sicurezza e riservatezza: blocco di Foliant."""

    def __init__(self, lock: AppLock, parent=None):
        super().__init__(parent)
        self.lock = lock
        lay = QVBoxLayout(self)
        self.enabled = QCheckBox(_("Blocca Foliant con un codice"))
        self.enabled.setChecked(lock.configured)
        lay.addWidget(self.enabled)
        form = QFormLayout()
        has = bool(lock._value(KEY_HASH, ""))
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setPlaceholderText(_("Lascia vuoto per mantenere quello attuale") if has else "")
        self.code2 = QLineEdit()
        self.code2.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.textChanged.connect(lambda t: t and self.enabled.setChecked(True))
        form.addRow(_("Codice:"), self.code)
        form.addRow(_("Conferma:"), self.code2)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 240)
        self.minutes.setSuffix(_(" min"))
        self.minutes.setSpecialValueText(_("mai"))
        self.minutes.setValue(lock.minutes())
        form.addRow(_("Blocca dopo un'inattività di:"), self.minutes)
        lay.addLayout(form)
        self.startup = QCheckBox(_("Blocca all'avvio"))
        self.startup.setChecked(lock.at_startup())
        lay.addWidget(self.startup)
        note = QLabel(_("Il blocco nasconde i documenti aperti ma non cifra i file, che restano leggibili da altri "
                        "programmi. L'autenticazione del sistema non è disponibile: si usa il codice di Foliant."))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(note)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        lay.addWidget(self.error)
        lay.addStretch(1)

    def problem(self) -> str:
        if not self.enabled.isChecked():
            return ""
        code = self.code.text()
        if not code and not self.lock._value(KEY_HASH, ""):
            return _("Scegli il codice di sblocco.")
        if code:
            if code != self.code2.text():
                return _("I codici non coincidono.")
            return check_code(code)
        return ""

    def apply(self) -> bool:
        problem = self.problem()
        self.error.setText(problem)
        if problem:
            return False
        self.lock.configure(self.enabled.isChecked(), self.code.text() or None, self.minutes.value(),
                            self.startup.isChecked())
        return True


class LockDialog(QDialog):
    """Le stesse impostazioni in una finestra (Proteggi › Blocco dell'applicazione…)."""

    def __init__(self, lock: AppLock, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Sicurezza e riservatezza"))
        lay = QVBoxLayout(self)
        self.page = LockPreferences(lock, self)
        lay.addWidget(self.page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Salva"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def accept(self) -> None:
        if self.page.apply():
            super().accept()
