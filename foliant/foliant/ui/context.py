"""API dell'applicazione per i moduli (vedi README.md, sezione «Scrivere un modulo»).

Ogni modulo riceve un `Context` in `register(ctx)` e lo usa per aggiungere
modalità, azioni, pannelli e voci di menu, e per accedere al documento attivo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolBar, QToolButton, QWidget

from ..core.document import Document
from ..i18n import _
from .icons import bind_icon
from .tasks import run_task as _run_task
from .tools import Tool

if TYPE_CHECKING:
    from .document_view import DocumentView
    from .main_window import MainWindow

PDF_FILTER = "PDF (*.pdf)"


class Mode(QObject):
    """Una modalità della barra in alto (Leggi, Commenta, Modifica, …)."""

    entered = Signal()
    left = Signal()

    def __init__(self, mode_id: str, title: str, icon_name: str, order: int,
                 toolbar: QToolBar, button: QToolButton):
        super().__init__(toolbar)
        self.id = mode_id
        self.title = title
        self.icon_name = icon_name
        self.order = order
        self.toolbar = toolbar
        self.button = button

    def add_action(self, action: QAction) -> QAction:
        self.toolbar.addAction(action)
        return action

    def scope_shortcuts(self, *actions: QAction) -> None:
        """Le scorciatoie di queste azioni valgono solo mentre questa modalità è attiva.

        Così due modalità possono usare lo stesso tasto (es. Ctrl+D per «Duplica pagina» in
        Pagine e «Duplica campo» in Moduli) senza conflitti.
        """
        window = self.parent().window() if self.parent() is not None else None
        from .main_window import MainWindow  # import locale: evita il ciclo di import
        win = window if isinstance(window, MainWindow) else None
        if win is None:
            return
        for act in actions:
            win.scope_action_to_mode(act, self.id)

    def add_widget(self, widget: QWidget) -> None:
        self.toolbar.addWidget(widget)

    def add_separator(self) -> None:
        self.toolbar.addSeparator()


class Context(QObject):
    """Punto di accesso dei moduli all'applicazione."""

    currentViewChanged = Signal(object)   # DocumentView | None
    documentOpened = Signal(object)       # DocumentView
    documentClosed = Signal(object)       # DocumentView (prima della chiusura)
    documentChanged = Signal(object)      # DocumentView il cui contenuto è cambiato
    modeChanged = Signal(str)

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._w = window
        self.pending_edit_hooks: list = []     # vedi add_pending_edit_hook

    # ---------------------------------------------------------------- finestra
    @property
    def window(self) -> "MainWindow":
        return self._w

    def settings(self) -> QSettings:
        return self._w.settings

    # --------------------------------------------------------------- documenti
    def add_pending_edit_hook(self, hook: Callable[["DocumentView"], Any]) -> None:
        """`hook(view)` conferma un editor in loco aperto: il nucleo lo chiama prima di salvare o chiudere."""
        if hook not in self.pending_edit_hooks:
            self.pending_edit_hooks.append(hook)

    def current_view(self) -> "DocumentView | None":
        try:
            return self._w.current_view()
        except RuntimeError:        # finestra già distrutta (timer dei moduli in ritardo)
            return None

    def current_document(self) -> Document | None:
        view = self._w.current_view()
        return view.document if view else None

    def views(self) -> list["DocumentView"]:
        return self._w.views()

    def open_path(self, path: str, password: str | None = None) -> "DocumentView | None":
        """Apre un file (chiede la password se serve). Restituisce la vista o None."""
        return self._w.open_path(path, password)

    def open_document(self, document: Document) -> "DocumentView":
        """Mostra in una nuova scheda un documento creato da un modulo."""
        return self._w.add_document(document)

    def save_current(self, save_as: bool = False) -> bool:
        return self._w.save_view(self.current_view(), save_as=save_as)

    # ----------------------------------------------------------------- modalità
    def add_mode(self, mode_id: str, title: str, icon_name: str, order: int = 100) -> Mode:
        """Aggiunge una modalità con la sua barra degli strumenti contestuale."""
        return self._w.add_mode(mode_id, title, icon_name, order)

    def mode(self, mode_id: str) -> Mode | None:
        return self._w.modes.get(mode_id)

    def current_mode(self) -> str | None:
        return self._w.current_mode_id

    def set_mode(self, mode_id: str) -> None:
        self._w.set_mode(mode_id)

    # ------------------------------------------------------------------ azioni
    def action(self, text: str, icon_name: str | None = None, slot: Callable | None = None, *,
               shortcut: str | QKeySequence | None = None, tooltip: str | None = None,
               checkable: bool = False, requires_document: bool = True, mode: str | None = None) -> QAction:
        """Crea un'azione. Con `requires_document` è disattivata se nessun documento è aperto.

        Con `mode="pagine"` la scorciatoia vale solo mentre quella modalità è attiva
        (consigliato per tutte le scorciatoie specifiche di un modulo).
        """
        act = QAction(text, self._w)
        if icon_name:
            bind_icon(act, icon_name)
        if slot is not None:
            act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
            self._w.addAction(act)
        if not tooltip and shortcut:
            from .dialogs import key_text
            tooltip = f"{text} ({key_text(QKeySequence(shortcut))})"
        act.setToolTip(tooltip or text)
        act.setCheckable(checkable)
        if requires_document:
            self._w.register_document_action(act)
        if mode is not None:
            self._w.scope_action_to_mode(act, mode)
        return act

    def add_menu_action(self, action: QAction, section: str = "Strumenti") -> None:
        """Aggiunge una voce al menu principale (☰), raggruppata per sezione."""
        self._w.add_menu_action(action, section)

    def add_home_action(self, title: str, icon_name: str, callback: Callable[[], Any],
                        description: str = "", order: int = 100) -> None:
        """Aggiunge un'azione rapida alla schermata Home."""
        self._w.home.add_action(title, icon_name, callback, description, order)

    # ------------------------------------------------------------------ pannelli
    def add_sidebar_panel(self, panel_id: str, title: str, icon_name: str, widget: QWidget,
                          order: int = 100) -> None:
        """Pannello laterale sinistro. Se il widget ha `set_view(view)` viene
        chiamato a ogni cambio di documento attivo (anche con None)."""
        self._w.add_sidebar_panel(panel_id, title, icon_name, widget, order)

    def show_sidebar_panel(self, panel_id: str) -> None:
        self._w.show_sidebar_panel(panel_id)

    def add_properties_panel(self, panel_id: str, widget: QWidget) -> None:
        """Pannello destro contestuale (proprietà). `set_view(view)` come sopra."""
        self._w.add_properties_panel(panel_id, widget)

    def show_properties(self, panel_id: str) -> None:
        self._w.show_properties(panel_id)

    def hide_properties(self) -> None:
        self._w.hide_properties()

    def set_central_widget(self, widget: QWidget | None) -> None:
        """Mostra un widget del modulo al posto della vista documento (es.
        organizzatore pagine, confronto). Con None torna alla vista documento.
        Il documento attivo (`current_view()`) non cambia."""
        self._w.set_central_override(widget)

    def central_widget(self) -> QWidget | None:
        return self._w.central_override()

    def add_link_handler(self, handler: Callable, priority: int = 0) -> None:
        """Gestisce l'apertura dei collegamenti cliccati nel documento.

        `handler(view, link) -> bool`: `link` è il dizionario del motore (`kind`, `uri`,
        `page`, `file`, …). Restituire True se il collegamento è stato gestito (anche
        rifiutato); False per lasciare il comportamento predefinito.
        I gestori sono interrogati per priorità decrescente: usare una priorità negativa
        (es. -100) per un controllo «di ultima istanza» prima del comportamento base.
        Registrare lo stesso gestore più volte non ha effetto.
        """
        from .tools import add_link_handler
        add_link_handler(handler, priority)

    def add_view_menu_provider(self, provider: Callable) -> None:
        """Voci del menu contestuale sulle pagine.

        `provider(view, page_event, menu)` aggiunge azioni a `menu` (QMenu) in base
        alla posizione (`page_event.page`, `page_event.point`) e allo stato del modulo.
        """
        self._w.view_menu_providers.append(provider)

    # ----------------------------------------------------------------- strumenti
    def set_tool(self, tool: Tool | None) -> None:
        """Attiva uno strumento sulla vista corrente (None = selezione testo)."""
        view = self.current_view()
        if view is not None:
            view.set_tool(tool)

    # ----------------------------------------------------------------- dialoghi
    def status(self, message: str, timeout_ms: int = 5000) -> None:
        self._w.statusBar().showMessage(message, timeout_ms)

    def error(self, title: str, message: str) -> None:
        QMessageBox.warning(self._w, title, message)

    def info(self, title: str, message: str) -> None:
        QMessageBox.information(self._w, title, message)

    def confirm(self, title: str, message: str) -> bool:
        return QMessageBox.question(self._w, title, message) == QMessageBox.StandardButton.Yes

    def ask_open_paths(self, title: str = "", file_filter: str = PDF_FILTER, multiple: bool = True) -> list[str]:
        start = self._w.last_directory()
        if multiple:
            paths, _f = QFileDialog.getOpenFileNames(self._w, title or _("Apri"), start, file_filter)
        else:
            path, _f = QFileDialog.getOpenFileName(self._w, title or _("Apri"), start, file_filter)
            paths = [path] if path else []
        if paths:
            self._w.remember_directory(paths[0])
        return paths

    def ask_save_path(self, title: str = "", suggested_name: str = "", file_filter: str = PDF_FILTER,
                      default_suffix: str = ".pdf") -> str | None:
        import os
        start = os.path.join(self._w.last_directory(), suggested_name) if suggested_name else self._w.last_directory()
        path, _f = QFileDialog.getSaveFileName(self._w, title or _("Salva"), start, file_filter)
        if not path:
            return None
        if default_suffix and not os.path.splitext(path)[1]:
            path += default_suffix
            if os.path.exists(path) and not self.confirm(
                    title or _("Salva"), _("«{name}» esiste già. Vuoi sostituirlo?").format(name=os.path.basename(path))):
                return None
        self._w.remember_directory(path)
        return path

    def ask_directory(self, title: str = "") -> str | None:
        path = QFileDialog.getExistingDirectory(self._w, title or _("Scegli cartella"), self._w.last_directory())
        if path:
            self._w.remember_directory(path + "/")
        return path or None

    def run_task(self, title: str, fn: Callable[..., Any], *args, cancellable: bool = True, **kwargs) -> Any:
        """Esegue un'operazione lunga in background con avanzamento (vedi ui/tasks.py)."""
        return _run_task(self._w, title, fn, *args, cancellable=cancellable, **kwargs)
