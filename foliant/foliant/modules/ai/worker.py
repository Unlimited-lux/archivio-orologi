"""Esecuzione in background delle operazioni IA, con flusso di testo e annullamento (AI-17).

A differenza di `run_task` non apre una finestra modale: l'utente continua a
leggere e modificare mentre la risposta arriva nel pannello.
"""

from __future__ import annotations

import datetime as _dt
import threading
import uuid
from typing import Callable

from PySide6.QtCore import QObject, Signal

from foliant.api import OperationCancelled

from .engine import Job


class Worker(QObject):
    text = Signal(str)
    progress = Signal(float, str)
    waiting = Signal(float)
    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, gateway, fn: Callable[[Job], object], *, function: str = "", document=None,
                 language: str = "it", parent=None):
        super().__init__(parent)
        self.id = uuid.uuid4().hex[:8]
        self.function = function
        self.document = document
        self.started = _dt.datetime.now()
        self.state = "in corso"
        self.phase = ""
        self.fraction = 0.0
        self.cancel_event = threading.Event()
        self.job = Job(gateway, on_text=self.text.emit, cancel=self.cancel_event, on_wait=self.waiting.emit,
                       progress=self._progress, language=language)
        self._fn = fn
        self._thread: threading.Thread | None = None

    def _progress(self, fraction: float, text: str) -> None:
        self.fraction, self.phase = fraction, text
        self.progress.emit(fraction, text)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"foliant-ai-{self.id}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            result = self._fn(self.job)
        except OperationCancelled as e:
            self.state = "interrotta"
            self.failed.emit(e)
            return
        except BaseException as e:  # noqa: BLE001 - riportata all'interfaccia
            self.state = "errore"
            self.failed.emit(e)
            return
        self.state = "completata"
        self.finished.emit(result)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()
