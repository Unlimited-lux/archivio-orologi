"""Attività lunghe in background con finestra di avanzamento e annullamento.

Uso::

    def lavoro(progress, percorso):
        for i in range(n):
            progress.update(i / n, f"Pagina {i + 1} di {n}")   # solleva OperationCancelled se annullato
            with ENGINE_LOCK:
                ...  # ogni accesso al motore PDF tenendo il lock
        return risultato

    risultato = run_task(finestra, "Conversione in corso…", lavoro, percorso)

`run_task` restituisce il risultato o rilancia l'eccezione della funzione.
Il lavoro gira in un thread separato: non deve toccare widget Qt.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

from ..core.errors import OperationCancelled
from ..i18n import _


class Progress:
    """Comunicazione tra il lavoro in background e la finestra di avanzamento."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self.fraction = 0.0
        self.text = ""

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise OperationCancelled()

    def update(self, fraction: float | None = None, text: str | None = None) -> None:
        if fraction is not None:
            self.fraction = max(0.0, min(1.0, float(fraction)))
        if text is not None:
            self.text = text
        self.check()


def run_task(parent: QWidget | None, title: str, fn: Callable[..., Any], *args,
             cancellable: bool = True, **kwargs) -> Any:
    progress = Progress()
    app = QApplication.instance()
    if app is None or os.environ.get("FOLIANT_SYNC_TASKS") == "1":
        return fn(progress, *args, **kwargs)

    result: dict[str, Any] = {}

    def work() -> None:
        try:
            result["value"] = fn(progress, *args, **kwargs)
        except BaseException as e:  # riportata al chiamante
            result["error"] = e

    thread = threading.Thread(target=work, name="foliant-task", daemon=True)
    dialog = QProgressDialog(title, _("Annulla") if cancellable else "", 0, 1000, parent)
    if not cancellable:
        dialog.setCancelButton(None)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(300)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setValue(0)
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)

    def tick() -> None:
        dialog.setValue(int(progress.fraction * 1000))
        if progress.text:
            dialog.setLabelText(progress.text)
        if dialog.wasCanceled() and not progress.cancelled:
            progress.cancel()
            dialog.setLabelText(_("Annullamento in corso…"))
        if not thread.is_alive():
            timer.stop()
            loop.quit()

    timer.timeout.connect(tick)
    thread.start()
    timer.start()
    loop.exec()
    dialog.close()
    dialog.deleteLater()
    if "error" in result:
        raise result["error"]
    return result.get("value")
