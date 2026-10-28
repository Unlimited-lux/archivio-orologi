"""Avvisi non bloccanti della finestra (`ctx.notify`).

Compaiono in basso al centro dell'area di lavoro, sopra la barra di stato, e spariscono
da soli: sostituiscono le finestre modali per i messaggi informativi (es. «3 pagine
eliminate · Annulla», UX-13). Il clic su un'azione chiude l'avviso ed esegue la funzione.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from ..i18n import _
from . import theme
from .icons import bind_icon

DEFAULT_MS = 6000               # durata senza azioni
DEFAULT_ACTION_MS = 10000       # con azioni (UX-13: «Annulla» resta visibile almeno 5 s)


class Notification(QFrame):
    """Un avviso temporaneo. `close_notification()` lo chiude subito."""

    closed = Signal()

    def __init__(self, text: str, actions, duration_ms: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("notification")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._closed = False
        self._duration = max(0, int(duration_ms))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 6, 6)
        lay.setSpacing(8)
        self.label = QLabel(str(text), self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(520)
        lay.addWidget(self.label, 1)
        self.buttons: list[QPushButton] = []
        for action in actions or ():
            label, callback = action[0], action[1]     # come gli avvisi: un terzo elemento è ignorato
            btn = QPushButton(str(label), self)
            btn.clicked.connect(lambda _c=False, f=callback: self._activate(f))
            lay.addWidget(btn)
            self.buttons.append(btn)
        close = QToolButton(self)
        close.setAutoRaise(True)
        bind_icon(close, "x")
        close.setToolTip(_("Chiudi"))
        close.clicked.connect(self.close_notification)
        lay.addWidget(close)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close_notification)
        if self._duration:
            self.timer.start(self._duration)

    @property
    def text(self) -> str:
        return self.label.text()

    @property
    def is_open(self) -> bool:
        return not self._closed

    def close_notification(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.hide()
        self.closed.emit()
        self.deleteLater()

    def _activate(self, callback: Callable) -> None:
        self.close_notification()
        try:
            callback()
        except Exception as e:          # un modulo difettoso non deve chiudere l'applicazione
            win = self.window()
            status = getattr(win, "statusBar", None)
            if callable(status):
                status().showMessage(_("Errore in un avviso: {e}").format(e=e), 8000)

    def enterEvent(self, ev) -> None:          # il puntatore sopra l'avviso lo trattiene
        self.timer.stop()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        if self._duration and not self._closed:
            self.timer.start(max(2000, self._duration // 2))
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(theme.color("border"), 1))
        p.setBrush(theme.color("surface"))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("accent"))
        p.drawRoundedRect(QRectF(r.x(), r.y() + 6, 3, max(0.0, r.height() - 12)), 1.5, 1.5)
        p.end()


class NotificationArea(QWidget):
    """Pila degli avvisi temporanei, sovrapposta in basso al centro di `host`."""

    MAX_VISIBLE = 3

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setObjectName("notificationArea")
        self._items: list[Notification] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        host.installEventFilter(self)
        self.hide()

    def notify(self, text: str, actions=(), duration_ms: int | None = None) -> Notification:
        actions = list(actions or ())
        if duration_ms is None:
            duration_ms = DEFAULT_ACTION_MS if actions else DEFAULT_MS
        item = Notification(text, actions, duration_ms, self)
        item.closed.connect(lambda n=item: self._remove(n))
        self._items.append(item)
        self.layout().addWidget(item)
        item.show()          # subito visibile: _place() deve contarlo nell'altezza della pila
        while len(self._items) > self.MAX_VISIBLE:
            self._items[0].close_notification()        # i più vecchi lasciano il posto
        self._place()
        self.show()
        self.raise_()
        return item

    def notifications(self) -> list[Notification]:
        return [n for n in self._items if n.is_open]

    def _remove(self, item: Notification) -> None:
        if item in self._items:
            self._items.remove(item)
        self.layout().removeWidget(item)
        if self._items:
            self._place()
        else:
            self.hide()

    def _place(self) -> None:
        host = self.host
        self.layout().activate()
        hint = self.sizeHint()
        width = max(1, min(hint.width(), host.width() - 32))
        height = self.layout().heightForWidth(width) if self.layout().hasHeightForWidth() else hint.height()
        height = max(height, hint.height())
        self.setGeometry((host.width() - width) // 2, max(0, host.height() - height - 16), width, height)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.host and ev.type() == QEvent.Type.Resize and self._items:
            self._place()
        return False

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.LayoutRequest and self._items:
            self._place()            # un avviso ha cambiato dimensioni: la pila si adegua
        return super().event(ev)
