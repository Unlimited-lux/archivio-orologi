"""API dell'applicazione per i moduli (vedi README.md, sezione «Scrivere un modulo»).

Ogni modulo riceve un `Context` in `register(ctx)` e lo usa per aggiungere
modalità, azioni, pannelli e voci di menu, e per accedere al documento attivo.
"""

from __future__ import annotations

import inspect
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
                 toolbar: QToolBar, button: QToolButton, window: "MainWindow | None" = None):
        super().__init__(toolbar)
        self.id = mode_id
        self.title = title
        self.icon_name = icon_name
        self.order = order
        self.toolbar = toolbar
        self.button = button
        self._window = window
        self.default_tool: Tool | Callable | None = None

    def add_action(self, action: QAction) -> QAction:
        self.toolbar.addAction(action)
        return action

    def set_default_tool(self, tool_or_factory: "Tool | Callable[[DocumentView], Tool | None] | None") -> None:
        """Strumento attivato dal nucleo entrando nella modalità e sui documenti aperti mentre è attiva.

        Accetta un `Tool` (stesso oggetto per tutte le viste) o una funzione `factory(view)`
        che restituisce lo strumento per quella vista (None = nessuno, es. permessi negati).
        Vale solo per le viste con lo strumento predefinito «Seleziona testo»: uno strumento
        scelto dall'utente resta. Con più moduli nella stessa modalità vince l'ultima chiamata.
        None toglie lo strumento predefinito.
        """
        self.default_tool = tool_or_factory
        if self._window is not None and self._window.current_mode_id == self.id:
            self._window.apply_default_tool(self)

    def make_default_tool(self, view: "DocumentView") -> Tool | None:
        spec = self.default_tool
        if spec is None or isinstance(spec, Tool):
            return spec
        try:
            params = [p for p in inspect.signature(spec).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)]
        except (TypeError, ValueError):
            params = [None]
        tool = spec(view) if params else spec()
        return tool if isinstance(tool, Tool) else None

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


# Operazioni che i moduli possono controllare (ed eventualmente annullare) con `add_guard`.
GUARD_EVENTS = ("save", "save_as", "print", "close_document", "quit")


class Context(QObject):
    """Punto di accesso dei moduli all'applicazione."""

    currentViewChanged = Signal(object)   # DocumentView | None
    documentOpened = Signal(object)       # DocumentView
    documentClosed = Signal(object)       # DocumentView (prima della chiusura)
    documentChanged = Signal(object)      # DocumentView il cui contenuto è cambiato
    documentAccessChanged = Signal(object)  # DocumentView: accesso/permessi (unlock) o blocco (set_locked) cambiati
    modeChanged = Signal(str)
    fullscreenChanged = Signal(bool)      # la finestra entra (True) o esce (False) dallo schermo intero

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self._w = window
        self.pending_edit_hooks: list = []     # vedi add_pending_edit_hook
        self._change_guards: list = []         # vedi add_change_guard
        self.documentOpened.connect(self._attach_change_guards)   # collegato prima dei moduli: le guardie valgono subito
        self._guards: dict[str, list[Callable]] = {event: [] for event in GUARD_EVENTS}

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

    def add_change_guard(self, guard: Callable[[Document, str, str], Any]) -> None:
        """`guard(document, etichetta, kind) -> bool` interrogata prima di ogni modifica di ogni
        documento aperto (ora e in futuro). False annulla la modifica (`change()` solleva
        OperationCancelled); vedi `Document.add_change_guard`."""
        if guard in self._change_guards:
            return
        self._change_guards.append(guard)
        for view in self.views():
            view.document.add_change_guard(guard)

    def remove_change_guard(self, guard: Callable[[Document, str, str], Any]) -> None:
        if guard in self._change_guards:
            self._change_guards.remove(guard)
        for view in self.views():
            view.document.remove_change_guard(guard)

    def _attach_change_guards(self, view: "DocumentView") -> None:
        for guard in self._change_guards:
            view.document.add_change_guard(guard)

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

    def activate_view(self, view: "DocumentView") -> bool:
        """Porta in primo piano la scheda di `view`, anche se è visibile la Home o un widget
        centrale di un modulo. False se la vista non è (più) aperta."""
        return self._w.activate_view(view)

    def close_view(self, view: "DocumentView | None", discard: bool = False) -> bool:
        """Chiude la scheda. Con `discard=False` come Ctrl+W (controlli `close_document` e
        domanda sulle modifiche non salvate); con `discard=True` chiude subito scartando le
        modifiche (es. file spostato nel cestino). True se la scheda è stata chiusa."""
        return self._w.close_view(view, ask=not discard)

    def refresh_state(self) -> None:
        """Ricalcola titolo, azioni (annulla/ripeti, navigazione) e barra di stato della finestra."""
        self._w.refresh_state()

    # ------------------------------------------------------------- pagine selezionate
    def set_selected_pages_provider(self, provider: Callable[["DocumentView"], Any] | None) -> None:
        """Registra chi conosce le pagine selezionate (il pannello miniature): `provider(view)`
        restituisce gli indici (base 0) o None/[] se nessuna pagina è selezionata."""
        self._w.selected_pages_provider = provider

    def selected_pages(self, view: "DocumentView | None" = None) -> list[int]:
        """Pagine selezionate di `view` (predefinita: la vista corrente), ordinate e senza
        doppioni; senza selezione (o senza provider) la sola pagina corrente."""
        view = view if view is not None else self.current_view()
        if view is None:
            return []
        pages: list[int] = []
        provider = self._w.selected_pages_provider
        if provider is not None:
            try:
                pages = sorted({int(i) for i in (provider(view) or ()) if 0 <= int(i) < view.page_count})
            except Exception as e:
                self.status(_("Errore nella selezione delle pagine: {e}").format(e=e), 8000)
                pages = []
        return pages or ([view.current_page] if view.page_count else [])

    # ------------------------------------------------------------------ avvisi
    def show_banner(self, view: "DocumentView | None", banner_id: str, text: str,
                    actions: list[tuple] | tuple = (), level: str = "info", priority: int = 0,
                    closable: bool = True, on_close: Callable[[], Any] | None = None):
        """Barra di avviso sopra le pagine di `view` (UX-14).

        `actions`: elenco di `(etichetta, callback)` (o `(etichetta, callback, principale)`;
        la prima è principale); `level`: "info", "warning" o "error"; `priority`: più alta =
        mostrata prima (a parità vale il livello). Si vede un avviso alla volta con «N altri
        avvisi» per scorrerli. Richiamarla con lo stesso `banner_id` aggiorna l'avviso; se
        l'utente lo ha chiuso (×, `on_close()` chiamato) resta chiuso finché il documento è
        aperto o finché il modulo non chiama `hide_banner`. Restituisce il `Banner` o None se
        la vista non è in una scheda.
        """
        return self._w.show_banner(view, banner_id, text, actions, level, priority, closable, on_close)

    def hide_banner(self, view: "DocumentView | None", banner_id: str) -> bool:
        """Toglie l'avviso (anche se chiuso dall'utente). False se non c'era."""
        return self._w.hide_banner(view, banner_id)

    def banners(self, view: "DocumentView | None") -> list[str]:
        """Identificativi degli avvisi visibili di `view`, dal più importante (esclusi i chiusi)."""
        return self._w.banner_ids(view)

    def notify(self, text: str, actions: list[tuple] | tuple = (), duration_ms: int | None = None):
        """Avviso non bloccante in basso nella finestra, al posto di una finestra modale.

        `actions`: `(etichetta, callback)`; il clic chiude l'avviso ed esegue la funzione.
        `duration_ms`: None = 6 s (10 s con azioni), 0 = finché l'utente non lo chiude.
        Restituisce l'avviso (`close_notification()` per toglierlo prima).
        """
        return self._w.notifications.notify(text, actions, duration_ms)

    # ------------------------------------------------------------- controlli
    def add_guard(self, event: str, fn: Callable[["DocumentView | None", dict], Any]) -> None:
        """Controllo prima di un'operazione: `fn(view, info) -> bool` (False = annulla).

        `event`: "save" (Salva sul file del documento), "save_as" (Salva con nome e Salva una
        copia, `info["copy"]`), "print" (chiamato dal modulo che stampa con `run_guards`),
        "close_document" (chiusura di una scheda da parte dell'utente), "quit" (uscita: una
        sola chiamata con `view=None` e `info["views"]`). `info["event"]` è sempre presente.
        Il controllo può chiedere conferma o modificare il documento (es. applicare le
        redazioni) prima che l'operazione prosegua. Registrare due volte non ha effetto.
        """
        if event not in self._guards:
            raise ValueError(f"evento sconosciuto: {event!r} (validi: {', '.join(GUARD_EVENTS)})")
        if fn not in self._guards[event]:
            self._guards[event].append(fn)

    def remove_guard(self, event: str, fn: Callable) -> None:
        if fn in self._guards.get(event, []):
            self._guards[event].remove(fn)

    def run_guards(self, event: str, view: "DocumentView | None", info: dict | None = None) -> bool:
        """Interroga i controlli di `event` nell'ordine di registrazione. False se uno annulla.

        Un controllo che solleva un'eccezione non blocca l'operazione (errore nella barra di stato).
        """
        if event not in self._guards:
            raise ValueError(f"evento sconosciuto: {event!r} (validi: {', '.join(GUARD_EVENTS)})")
        data = dict(info or {})
        data.setdefault("event", event)
        for fn in list(self._guards[event]):
            try:
                ok = fn(view, data)
            except Exception as e:      # un modulo difettoso non deve impedire di salvare o uscire
                self.status(_("Errore in un controllo prima dell'operazione: {e}").format(e=e), 8000)
                continue
            if ok is not None and not ok:
                return False
        return True

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

    def toggle_sidebar_panel(self, panel_id: str) -> None:
        """Mostra il pannello laterale `panel_id`, o lo nasconde se è già quello visibile."""
        self._w.toggle_sidebar_panel(panel_id)

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
        La selezione di testo corrente è `view.text_selection` (pagina, rettangoli, parole;
        None se non c'è) e il suo testo `view.selected_text()`: il clic destro non la cancella.
        """
        self._w.view_menu_providers.append(provider)

    def add_tab_menu_provider(self, provider: Callable[["DocumentView", Any], Any]) -> None:
        """Voci del menu contestuale di una scheda: `provider(view, menu)` aggiunge azioni a
        `menu` (QMenu) per il documento di quella scheda (es. «Confronta con…»)."""
        if provider not in self._w.tab_menu_providers:
            self._w.tab_menu_providers.append(provider)

    # ----------------------------------------------------------------- strumenti
    def set_tool(self, tool: Tool | None) -> None:
        """Attiva uno strumento sulla vista corrente (None = selezione testo)."""
        view = self.current_view()
        if view is not None:
            view.set_tool(tool)

    # ------------------------------------------------------------------ righelli
    def set_rulers_visible(self, visible: bool) -> None:
        """Mostra o nasconde i righelli (VIS-39) su tutte le viste, anche future, come la voce
        «Righelli» del menu Vista; la scelta resta tra una sessione e l'altra."""
        self._w.set_rulers_visible(visible)

    def rulers_visible(self) -> bool:
        return self._w.rulers_visible()

    def set_ruler_unit(self, unit: str) -> None:
        """Unità dei righelli per tutte le viste: "mm", "pt" o "in" (preferenza salvata)."""
        self._w.set_ruler_unit(unit)

    def ruler_unit(self) -> str:
        return self._w.ruler_unit()

    # ------------------------------------------------------- formati locali
    def paper_size(self) -> str:
        """Formato carta predefinito per i nuovi documenti: "A4" o "Letter" (preferenza
        «Formato carta», altrimenti dalle impostazioni locali, NFR-26)."""
        from ..core.locale_defaults import paper_size
        return paper_size(self._w.settings)

    def measure_unit(self) -> str:
        """Unità di misura dei dialoghi: "mm" o "in" (preferenza «Unità di misura»,
        altrimenti dalle impostazioni locali, NFR-26)."""
        from ..core.locale_defaults import measure_unit
        return measure_unit(self._w.settings)

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

    def run_task(self, title: str, fn: Callable[..., Any], *args, cancellable: bool = True, modal: bool = True,
                 on_done: Callable[[Any], Any] | None = None, on_error: Callable[[BaseException], Any] | None = None,
                 **kwargs) -> Any:
        """Esegue un'operazione lunga in background con avanzamento (vedi ui/tasks.py).

        Predefinito (`modal=True`): finestra modale, restituisce il risultato. Con `modal=False`
        la finestra resta utilizzabile (avanzamento e Annulla nella barra di stato), il metodo
        restituisce subito un `BackgroundTask` e alla fine chiama `on_done(risultato)` oppure
        `on_error(eccezione)` nel thread dell'interfaccia.
        """
        return _run_task(self._w, title, fn, *args, cancellable=cancellable, modal=modal,
                         on_done=on_done, on_error=on_error, **kwargs)

    def background_tasks(self) -> list:
        """Attività non modali in corso (`BackgroundTask`)."""
        return self._w.task_manager.running()

    # ------------------------------------------------------------- preferenze
    def add_preferences_page(self, title: str, widget_factory: Callable[..., QWidget], order: int = 100) -> None:
        """Pagina di un modulo nella finestra Preferenze (es. «Assistente IA»).

        `widget_factory(parent)` crea il widget a ogni apertura della finestra. Se il widget
        ha `apply()`, il nucleo lo chiama quando l'utente preme «Salva».
        """
        self._w.add_preferences_page(title, widget_factory, order)

    def show_preferences(self, page: str | None = None) -> None:
        """Apre la finestra Preferenze, eventualmente sulla pagina con titolo `page`."""
        self._w.show_preferences(page)

    def author_name(self) -> str:
        """Nome dell'autore per commenti, note e firme (preferenza «Nome autore», altrimenti
        il nome dell'utente di sistema)."""
        from .dialogs import author_name
        return author_name(self._w.settings)

    # ------------------------------------------------------------------ recenti
    def recent_files(self) -> list[str]:
        return self._w.recent_files()

    def replace_recent(self, old_path: str, new_path: str) -> None:
        """Sostituisce una voce dei recenti nella stessa posizione (file rinominato o spostato)."""
        self._w.replace_recent(str(old_path), str(new_path))

    def remove_recent(self, path: str) -> None:
        """Toglie un file dall'elenco dei recenti."""
        self._w.remove_recent(str(path))

    def pin_recent(self, path: str, pinned: bool = True) -> None:
        """Fissa (True) o sblocca (False) un documento in cima ai recenti della Home."""
        self._w.pin_recent(str(path), pinned)

    def is_pinned(self, path: str) -> bool:
        return self._w.is_pinned(str(path))

    def pinned_recent(self) -> list[str]:
        """Documenti fissati, nell'ordine in cui compaiono in cima ai recenti."""
        return self._w.pinned_recent()

    def clear_recent(self, ask: bool = True) -> bool:
        """«Svuota elenco»: toglie i recenti non fissati (con `ask`, dopo la conferma UX-13).
        Restituisce False se l'utente annulla."""
        return self._w.clear_recent(ask)

    def add_recent_menu_provider(self, provider: Callable[[str, Any], Any]) -> None:
        """Voci del menu contestuale di un recente della Home: `provider(percorso, menu)`
        aggiunge azioni a `menu` (QMenu), tra quelle del nucleo (Apri, Fissa/Sblocca) e
        Rimuovi dall'elenco / Svuota elenco."""
        if provider not in self._w.recent_menu_providers:
            self._w.recent_menu_providers.append(provider)
