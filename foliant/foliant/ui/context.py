"""API dell'applicazione per i moduli (vedi README.md, sezione «Scrivere un modulo»).

Ogni modulo riceve un `Context` in `register(ctx)` e lo usa per aggiungere
modalità, azioni, pannelli e voci di menu, e per accedere al documento attivo.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QEvent, QObject, QRect, QSettings, Qt, Signal
from PySide6.QtGui import QAction, QFontMetrics, QKeySequence, QPainter
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QToolBar, QToolButton, QWidget, QWidgetAction

from ..core.document import Document
from ..i18n import _
from .icons import bind_icon
from .tasks import run_task as _run_task
from .tools import Tool

if TYPE_CHECKING:
    from .document_view import DocumentView
    from .main_window import MainWindow

PDF_FILTER = "PDF (*.pdf)"


def _tab_focusable(toolbar: QToolBar, action: QAction) -> None:
    """Il QToolButton che una QToolBar crea per un'azione nasce con NoFocus: lo rende
    raggiungibile con Tab (NFR-21). Il clic del mouse non sposta comunque il fuoco."""
    if isinstance(action, QWidgetAction):
        return                                   # widget del chiamante: decide lui
    button = toolbar.widgetForAction(action)
    if isinstance(button, QToolButton) and button.focusPolicy() == Qt.FocusPolicy.NoFocus:
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)


class ToolBar(QToolBar):
    """Barra del nucleo con i pulsanti raggiungibili con Tab e Maiusc+Tab (NFR-21), compreso
    quello dei comandi in eccesso («»»)."""

    def __init__(self, *args):
        super().__init__(*args)
        extension = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if extension is not None:
            extension.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def actionEvent(self, event) -> None:
        super().actionEvent(event)
        if event.type() == QEvent.Type.ActionAdded:
            _tab_focusable(self, event.action())


class ContextBar(ToolBar):
    """Barra contestuale di una modalità, divisa in gruppi come la barra multifunzione di Excel.

    I gruppi sono separati dai separatori; quelli aperti con `Mode.add_group(nome)` hanno il nome
    scritto sotto i pulsanti. Il separatore di un gruppo vuoto (o con tutte le voci nascoste) sparisce.
    """

    CAPTION_GAP = 2

    def __init__(self, *args):
        super().__init__(*args)
        self._titles: dict[int, str] = {}        # id(separatore che apre il gruppo), 0 = primo gruppo
        self._captions = False
        self._syncing = False

    def set_group_title(self, opener: QAction | None, title: str) -> None:
        self._titles[id(opener) if opener is not None else 0] = title
        if not self._captions:
            self._captions = True
            m = self.contentsMargins()
            self.setContentsMargins(m.left(), m.top(), m.right(), m.bottom() + self.caption_height())
        self.update()

    def caption_height(self) -> int:
        return QFontMetrics(self._caption_font()).height() + self.CAPTION_GAP

    def _caption_font(self):
        font = self.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        else:
            font.setPixelSize(max(9, int(font.pixelSize() * 0.85)))
        return font

    def groups(self) -> list[tuple[str, list[QAction]]]:
        """(nome, azioni) di ogni gruppo nell'ordine della barra; nome vuoto per i gruppi senza nome."""
        out: list[tuple[str, list[QAction]]] = [(self._titles.get(0, ""), [])]
        for act in self.actions():
            if act.isSeparator():
                out.append((self._titles.get(id(act), ""), []))
            else:
                out[-1][1].append(act)
        return out

    def group_title(self, action: QAction) -> str:
        """Nome del gruppo che contiene `action` ("" se senza nome o assente)."""
        for title, acts in self.groups():
            if action in acts:
                return title
        return ""

    def actionEvent(self, event) -> None:
        super().actionEvent(event)
        if event.type() in (QEvent.Type.ActionAdded, QEvent.Type.ActionRemoved, QEvent.Type.ActionChanged):
            self._sync_separators()

    def _sync_separators(self) -> None:
        """Nasconde i separatori dei gruppi senza voci visibili (niente doppie righe vuote)."""
        if self._syncing:
            return
        self._syncing = True
        try:
            acts = self.actions()
            seen_visible = False
            for i, act in enumerate(acts):
                if not act.isSeparator():
                    seen_visible = seen_visible or act.isVisible()
                    continue
                following = []
                for nxt in acts[i + 1:]:
                    if nxt.isSeparator():
                        break
                    following.append(nxt)
                want = seen_visible and any(a.isVisible() for a in following)
                if act.isVisible() != want:
                    act.setVisible(want)
        finally:
            self._syncing = False

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._captions:
            return
        from . import theme
        p = QPainter(self)
        font = self._caption_font()
        p.setFont(font)
        p.setPen(theme.color("text_muted"))
        fm = QFontMetrics(font)
        h = self.caption_height()
        top = self.height() - h
        for title, acts in self.groups():
            if not title:
                continue
            rects = []
            for a in acts:
                w = self.widgetForAction(a)
                if w is not None and w.isVisible() and a.isVisible():
                    rects.append(w.geometry())
            if not rects:
                continue
            left = min(r.left() for r in rects)
            right = max(r.right() for r in rects)
            box = QRect(left - 4, top, right - left + 8, h)
            p.drawText(box, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                       fm.elidedText(title, Qt.TextElideMode.ElideRight, box.width()))


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
        self._groups: dict[str, QAction | None] = {}     # id del gruppo → separatore che lo apre

    def add_group(self, title: str, group_id: str | None = None) -> None:
        """Apre un gruppo con nome, come i gruppi della barra multifunzione di Excel: le azioni e i
        widget aggiunti dopo finiscono qui e il nome compare sotto i pulsanti. Con `group_id`
        (es. "zoom") anche altri moduli aggiungono voci al gruppo: `add_action(a, group="zoom")`."""
        opener = self.toolbar.addSeparator() if self.toolbar.actions() else None
        if isinstance(self.toolbar, ContextBar):
            self.toolbar.set_group_title(opener, title)
        self._groups[group_id or title] = opener

    def has_group(self, group_id: str) -> bool:
        return group_id in self._groups

    def _insert_before(self, group: str | None) -> QAction | None:
        """L'azione prima della quale si inserisce in fondo al gruppo `group` (None = fine barra)."""
        if group is None or group not in self._groups:
            return None
        acts = self.toolbar.actions()
        opener = self._groups[group]
        try:
            start = acts.index(opener) + 1 if opener is not None else 0
        except ValueError:
            return None
        for act in acts[start:]:
            if act.isSeparator():
                return act
        return None

    def add_action(self, action: QAction, group: str | None = None, compact: bool = False) -> QAction:
        """Aggiunge un pulsante. `group`: id o nome di un gruppo (`add_group`), altrimenti in fondo.
        `compact=True` mostra solo l'icona (il nome resta nel suggerimento), per le voci secondarie;
        per un'etichetta più corta di quella del menu usare `action.setIconText(...)`."""
        before = self._insert_before(group)
        if before is None:
            self.toolbar.addAction(action)
        else:
            self.toolbar.insertAction(before, action)
        _tab_focusable(self.toolbar, action)
        if compact:
            button = self.toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        return action

    def add_menu_button(self, text: str, icon_name: str, actions: list, group: str | None = None, *,
                        compact: bool = False, tooltip: str | None = None,
                        requires_document: bool = True) -> QToolButton:
        """Pulsante con un menu a tendina (la freccia ▾ della barra multifunzione). `actions`: QAction,
        QMenu (sottomenu) o None (separatore). Restituisce il pulsante."""
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip or text)
        button.setAccessibleName(text)
        bind_icon(button, icon_name)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                                  else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        menu = QMenu(button)
        for item in actions:
            if item is None:
                menu.addSeparator()
            elif isinstance(item, QMenu):
                menu.addMenu(item)
            else:
                menu.addAction(item)
        button.setMenu(menu)
        self.add_widget(button, group)
        if requires_document and self._window is not None:
            self._window.register_document_action(button)
        return button

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

    def add_widget(self, widget: QWidget, group: str | None = None) -> QAction:
        before = self._insert_before(group)
        if before is None:
            return self.toolbar.addWidget(widget)
        return self.toolbar.insertWidget(before, widget)

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

    def add_file_action(self, action: QAction, page: str, group: str | None = None, description: str = "",
                        order: int = 100) -> None:
        """Voce della scheda File (la pagina a tutto schermo che si apre con «File», come in Excel).

        `page`: "nuovo", "apri", "informazioni", "condividi", "esporta", "gestisci", "opzioni" o
        "guida"; oppure "stampa", il comando Stampa della colonna di sinistra. `group` è il titolo del
        gruppo nella pagina (es. "Proteggi il documento"), `description` la riga sotto il nome della
        voce, `order` la posizione (prima i numeri più bassi). Le voci seguono testo, stato e spunta
        dell'azione; una voce con `action.menu()` apre il menu, una attivabile cambia la spunta.
        """
        self._w.backstage.add_action(action, page, group, description, order)

    def add_quick_access_action(self, action: QAction) -> None:
        """Pulsante della barra di accesso rapido in alto a sinistra (dopo Apri e Salva, prima di
        Annulla e Ripeti), come in Excel. Per i comandi di uso continuo, es. Stampa."""
        self._w.add_quick_access_action(action)

    def set_command_search(self, action: QAction) -> None:
        """Il pulsante «Cerca un comando» in alto a destra (dove c'era il menu ☰) avvia `action`."""
        self._w.set_command_search(action)

    def show_file_tab(self, page: str | None = None) -> None:
        """Apre la scheda File, eventualmente sulla pagina `page` (es. "informazioni")."""
        self._w.show_backstage(page)

    def add_menu_action(self, action: QAction, section: str = "Strumenti") -> None:
        """Superata: il menu ☰ non c'è più. Usare `add_file_action` per i comandi sul file e
        `Mode.add_action` per quelli di una scheda. Per compatibilità la voce finisce nella scheda
        File, pagina «Altri comandi», nel gruppo `section`."""
        self._w.add_menu_action(action, section)

    def add_command(self, name: str, fn: Callable[[], Any]) -> None:
        """Comando con nome per la riga di comando e le azioni del file .desktop: «create-pdf»
        (`foliant --create`) e «merge» (`foliant --merge`). Senza, il nucleo usa l'azione
        rapida della Home «Crea PDF da file» / «Unisci PDF»."""
        self._w.add_command(name, fn)

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
        """Dialogo «Apri». Parte dall'ultima cartella della sessione, ricordata solo in memoria."""
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

    # ------------------------------------------------------------------ recenti (inerti)
    # Riservatezza (ui/privacy.py): Foliant non conserva nomi né percorsi dei file aperti o
    # salvati. Queste funzioni restano chiamabili per compatibilità, ma non salvano nulla.
    def recent_files(self) -> list[str]:
        """Sempre vuoto: Foliant non conserva i file aperti."""
        return []

    def replace_recent(self, old_path: str, new_path: str) -> None:
        """Non fa nulla (compatibilità)."""

    def remove_recent(self, path: str) -> None:
        """Non fa nulla (compatibilità)."""

    def pin_recent(self, path: str, pinned: bool = True) -> None:
        """Non fa nulla: non esistono documenti fissati (compatibilità)."""

    def is_pinned(self, path: str) -> bool:
        """Sempre False (compatibilità)."""
        return False

    def pinned_recent(self) -> list[str]:
        """Sempre vuoto (compatibilità)."""
        return []

    def clear_recent(self, ask: bool = True) -> bool:
        """Non c'è nulla da svuotare: True, senza domande (compatibilità)."""
        return True

    def add_recent_menu_provider(self, provider: Callable[[str, Any], Any]) -> None:
        """Non registra nulla: la Home non ha più l'elenco dei recenti (compatibilità)."""
