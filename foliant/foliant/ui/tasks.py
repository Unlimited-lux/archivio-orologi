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

Con `modal=False` la finestra resta utilizzabile: avanzamento e Annulla compaiono nella
barra di stato (con l'elenco delle altre attività in corso), `run_task` restituisce subito
un `BackgroundTask` e alla fine, nel thread dell'interfaccia, chiama `on_done(risultato)`
oppure `on_error(eccezione)` (anche `OperationCancelled` se annullata)::

    ctx.run_task("Confronto in corso…", confronta, a, b, modal=False,
                 on_done=lambda esito: mostra(esito), on_error=lambda e: ...)

Con `FOLIANT_SYNC_TASKS=1` (test) il lavoro e i callback girano subito, prima del ritorno.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QProgressBar, QProgressDialog, QToolButton, QWidget,
)

from ..core.engine import ENGINE_LOCK
from ..core.errors import OperationCancelled
from ..i18n import _, pgettext
from .icons import bind_icon


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
             cancellable: bool = True, modal: bool = True, on_done: Callable[[Any], Any] | None = None,
             on_error: Callable[[BaseException], Any] | None = None, **kwargs) -> Any:
    """Esegue `fn(progress, *args, **kwargs)` in un thread.

    `modal=True` (predefinito): finestra di avanzamento modale; restituisce il risultato o
    rilancia l'eccezione. Se indicati, `on_done(risultato)` è chiamato prima del ritorno e
    `on_error(eccezione)` riceve l'errore al posto del rilancio (restituisce None).
    `modal=False`: restituisce subito un `BackgroundTask`; vedi la docstring del modulo.
    """
    if ENGINE_LOCK._is_owned():
        raise RuntimeError("run_task chiamato tenendo ENGINE_LOCK (es. dentro document.change()): "
                           "il lavoro non potrebbe mai acquisire il lock")
    if not modal:
        return _run_background(parent, title, fn, args, kwargs, cancellable, on_done, on_error)
    if on_done is not None or on_error is not None:
        try:
            value = run_task(parent, title, fn, *args, cancellable=cancellable, **kwargs)
        except Exception as e:
            if on_error is None:
                raise
            on_error(e)
            return None
        if on_done is not None:
            on_done(value)
        return value
    progress = Progress()
    app = QApplication.instance()
    if app is None or _sync_tasks():
        return fn(progress, *args, **kwargs)

    result: dict[str, Any] = {}

    def work() -> None:
        try:
            result["value"] = fn(progress, *args, **kwargs)
        except BaseException as e:  # riportata al chiamante
            result["error"] = e

    thread = threading.Thread(target=work, name="foliant-task", daemon=True)
    dialog = QProgressDialog(title, pgettext("pulsante", "Annulla") if cancellable else "", 0, 1000, parent)
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
            if cancellable:
                progress.cancel()
                dialog.setLabelText(_("Annullamento in corso…"))
            else:
                dialog.reset(); dialog.show()      # non annullabile: la finestra resta
        if not thread.is_alive():
            timer.stop()
            loop.quit()

    timer.timeout.connect(tick)
    thread.start()
    timer.start()
    loop.exec()
    if thread.is_alive():          # ciclo eventi interrotto (uscita dall'app): non è un successo
        progress.cancel()
        dialog.close()
        dialog.deleteLater()
        raise OperationCancelled()
    dialog.close()
    dialog.deleteLater()
    if "error" in result:
        raise result["error"]
    return result.get("value")


# ============================================================== attività non modali
def _sync_tasks() -> bool:
    return os.environ.get("FOLIANT_SYNC_TASKS") == "1"


class BackgroundTask(QObject):
    """Attività non modale avviata con `run_task(..., modal=False)`."""

    finished = Signal(object)       # il task stesso, dopo on_done/on_error

    def __init__(self, title: str, cancellable: bool = True, on_done: Callable | None = None,
                 on_error: Callable | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.title = title
        self.cancellable = cancellable
        self.on_done = on_done
        self.on_error = on_error
        self.progress = Progress()
        self.result: Any = None
        self.error: BaseException | None = None
        self.done = False
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self.progress.cancelled

    @property
    def running(self) -> bool:
        return not self.done

    def cancel(self) -> None:
        if self.cancellable and not self.done:
            self.progress.cancel()

    def wait(self, timeout_ms: int = 30000) -> bool:
        """Attende la fine elaborando gli eventi (callback compresi). True se è finita."""
        deadline = time.monotonic() + timeout_ms / 1000
        app = QCoreApplication.instance()
        while not self.done and time.monotonic() < deadline:
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(0.005)
        return self.done


class TaskManager(QObject):
    """Attività non modali di una finestra: avvio, avanzamento, fine nel thread dell'interfaccia."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None, report: Callable[[str], Any] | None = None,
                 status: Callable[[str], Any] | None = None):
        super().__init__(parent)
        self.tasks: list[BackgroundTask] = []
        # report: errori senza on_error (la finestra usa ctx.notify); status: esiti brevi
        self.report = report or (lambda text: logging.getLogger("foliant").warning(text))
        self.status = status or self.report
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.poll)

    def running(self) -> list[BackgroundTask]:
        return [t for t in self.tasks if not t.done]

    def start(self, task: BackgroundTask, fn: Callable, args: tuple, kwargs: dict,
              sync: bool = False) -> BackgroundTask:
        def work() -> None:
            try:
                task.result = fn(task.progress, *args, **kwargs)
            except BaseException as e:      # riportata a on_error
                task.error = e

        if sync:
            work()
            self._finish(task)
            return task
        task._thread = threading.Thread(target=work, name="foliant-task-bg", daemon=True)
        self.tasks.append(task)
        task._thread.start()
        self._timer.start()
        self.changed.emit()
        return task

    def poll(self) -> None:
        ended = [t for t in self.tasks if t._thread is not None and not t._thread.is_alive()]
        for task in ended:
            # un callback con un dialogo modale fa girare una poll annidata che può aver già
            # concluso le altre attività di questo giro
            if task not in self.tasks:
                continue
            self.tasks.remove(task)
            self._finish(task)
        if not self.tasks:
            self._timer.stop()
        self.changed.emit()

    def cancel_all(self) -> None:
        for task in self.running():
            task.progress.cancel()

    def wait_all(self, timeout_ms: int = 30000) -> bool:
        """Attende la fine di tutte le attività (callback compresi) senza accettare clic e tasti
        nel frattempo (es. all'uscita). True se sono finite tutte entro `timeout_ms`."""
        deadline = time.monotonic() + timeout_ms / 1000
        app = QCoreApplication.instance()
        while self.running() and time.monotonic() < deadline:
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20)
            self.poll()
            time.sleep(0.005)
        return not self.running()

    def _finish(self, task: BackgroundTask) -> None:
        task.done = True
        try:
            if task.error is None:
                if task.on_done is not None:
                    task.on_done(task.result)
                else:
                    self.status(_("Completato: {t}").format(t=task.title))
            elif task.on_error is not None:
                task.on_error(task.error)
            elif isinstance(task.error, OperationCancelled):
                self.status(_("Operazione annullata: {t}").format(t=task.title))
            else:
                self.report(_("«{t}» non è riuscita: {e}").format(t=task.title, e=task.error))
        except Exception as e:          # errore nel callback del modulo: mai fatale
            self.report(_("Errore al termine di «{t}»: {e}").format(t=task.title, e=e))
        task.finished.emit(task)


_fallback_manager: TaskManager | None = None


def task_manager_for(widget: QWidget | None) -> TaskManager:
    """Il gestore delle attività della finestra di `widget` (o uno senza interfaccia)."""
    global _fallback_manager
    w = widget
    while w is not None:
        manager = getattr(w, "task_manager", None)
        if isinstance(manager, TaskManager):
            return manager
        w = w.parentWidget() if isinstance(w, QWidget) else None
    if _fallback_manager is None:
        _fallback_manager = TaskManager()
    return _fallback_manager


def _run_background(parent, title, fn, args, kwargs, cancellable, on_done, on_error) -> BackgroundTask:
    manager = task_manager_for(parent)
    task = BackgroundTask(title, cancellable, on_done, on_error)    # senza genitore: non si accumulano
    sync = QApplication.instance() is None or _sync_tasks()
    return manager.start(task, fn, args, kwargs, sync=sync)


class TaskStatusWidget(QWidget):
    """Attività non modali nella barra di stato: la prima con avanzamento e Annulla,
    le altre nel menu «+N»."""

    def __init__(self, manager: TaskManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.manager = manager
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(4)
        self.label = QLabel(self)
        self.label.setProperty("muted", True)
        lay.addWidget(self.label)
        self.bar = QProgressBar(self)
        self.bar.setFixedSize(110, 12)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        self.cancel_button = QToolButton(self)
        self.cancel_button.setAutoRaise(True)
        bind_icon(self.cancel_button, "x")
        self.cancel_button.setToolTip(pgettext("pulsante", "Annulla"))
        self.cancel_button.clicked.connect(self.cancel_current)
        lay.addWidget(self.cancel_button)
        self.more_button = QToolButton(self)
        self.more_button.setAutoRaise(True)
        self.more_button.setToolTip(_("Altre attività in corso"))
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.more_button)
        self.menu.aboutToShow.connect(self.fill_menu)
        self.more_button.setMenu(self.menu)
        lay.addWidget(self.more_button)
        manager.changed.connect(self.refresh)
        self.hide()

    def current(self) -> BackgroundTask | None:
        tasks = self.manager.running()
        return tasks[0] if tasks else None

    def cancel_current(self) -> None:
        task = self.current()
        if task is not None:
            task.cancel()
            self.refresh()

    def refresh(self) -> None:
        tasks = self.manager.running()
        if not tasks:
            self.hide()
            return
        task = tasks[0]
        if task.cancelled:
            text = _("Annullamento in corso…")
        else:
            text = task.title + (f" — {task.progress.text}" if task.progress.text else "")
        self.label.setToolTip(text)
        self.label.setText(QFontMetrics(self.label.font()).elidedText(text, Qt.TextElideMode.ElideRight, 260))
        if task.progress.fraction > 0:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(task.progress.fraction * 1000))
        else:
            self.bar.setRange(0, 0)          # durata non stimabile: indicatore indeterminato
        self.cancel_button.setVisible(task.cancellable)
        self.cancel_button.setEnabled(not task.cancelled)
        others = len(tasks) - 1
        self.more_button.setVisible(others > 0)
        self.more_button.setText(f"+{others}")
        self.show()

    def fill_menu(self) -> None:
        """Elenco di tutte le attività in corso, ciascuna con il proprio Annulla."""
        self.menu.clear()
        for task in self.manager.running():
            pct = f"{round(task.progress.fraction * 100)}%"
            act = self.menu.addAction(_("Annulla «{t}» ({p})").format(t=task.title, p=pct))
            act.setEnabled(task.cancellable and not task.cancelled)
            act.triggered.connect(lambda _c=False, t=task: t.cancel())
