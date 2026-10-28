"""Ripristina e ricarica (VIS-49).

- File → «Ripristina»: scarta le modifiche non salvate e riapre l'ultima versione
  salvata nella stessa scheda, alla stessa pagina e con lo stesso zoom.
- Monitoraggio del file su disco (QFileSystemWatcher + controllo periodico di data e
  dimensione come ripiego): quando un'altra applicazione cambia il file, il documento
  si ricarica secondo la preferenza `viewer/reload_mode` ("auto", "ask", "never");
  con modifiche non salvate compare invece una barra non modale con tre scelte.
- File eliminato o spostato → barra con «Salva» per ricrearlo.

Il nucleo non offre ancora un modo per sostituire il documento di una vista: la
ricarica apre un nuovo Document, lo aggiunge in una nuova scheda spostata al posto
della vecchia e chiude la vecchia senza richiesta di salvataggio.
"""

from __future__ import annotations

import logging
import os
import weakref
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QToolButton, QWidget,
)

from foliant.api import (
    SAVED, Context, Document, DocumentError, DocumentView, PasswordRequired, WrongPassword, _,
    add_translations, bind_icon, icon, theme,
)

from . import reload_engine as E

log = logging.getLogger("foliant.modules.viewer.reload")

KEY_MODE = "viewer/reload_mode"

TRANSLATIONS = {
    "File": "File",
    "Ripristina": "Revert",
    "Scarta le modifiche e riapri l'ultima versione salvata":
        "Discard changes and reopen the last saved version",
    "Ripristina il documento": "Revert document",
    "Scartare le modifiche non salvate a «{name}» e riaprire l'ultima versione salvata?":
        "Discard unsaved changes to «{name}» and reopen the last saved version?",
    "Impossibile ricaricare": "Cannot reload",
    "Documento protetto": "Protected document",
    "La password di «{name}» è cambiata. Inserisci la nuova password:":
        "The password of «{name}» has changed. Enter the new password:",
    "Il file «{name}» è stato modificato da un'altra applicazione.":
        "The file «{name}» was changed by another application.",
    "Il file «{name}» è stato modificato da un'altra applicazione, ma qui ci sono modifiche non salvate.":
        "The file «{name}» was changed by another application, but there are unsaved changes here.",
    "Il file «{name}» è stato eliminato o spostato.": "The file «{name}» was deleted or moved.",
    "Ricarica": "Reload",
    "Ricarica e perdi le modifiche": "Reload and lose changes",
    "Ignora": "Ignore",
    "Salva con nome…": "Save as…",
    "Salva": "Save",
    "Chiudi l'avviso": "Close notice",
    "Ricaricato: {name}": "Reloaded: {name}",
    "Ricarica dei documenti modificati da altre applicazioni": "Reload of documents changed by other applications",
    "Automatica": "Automatic",
    "Chiedi": "Ask",
    "Mai": "Never",
}

BAR_CHANGED = "changed"    # file cambiato, documento con modifiche (tre scelte)
BAR_ASK = "ask"            # file cambiato, preferenza «chiedi»
BAR_MISSING = "missing"    # file eliminato o spostato


class ReloadBar(QWidget):
    """Barra informativa non modale in cima alla vista."""

    def __init__(self, kind: str, text: str, buttons: list[tuple[str, object, bool]], on_close, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("viewerReloadBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(6)
        self.icon_label = QLabel()
        lay.addWidget(self.icon_label)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.buttons: dict[str, QPushButton] = {}
        for label, slot, primary in buttons:
            b = QPushButton(label)
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(lambda _c=False, s=slot: s())
            lay.addWidget(b)
            self.buttons[label] = b
        close = QToolButton()
        bind_icon(close, "x")
        close.setToolTip(_("Chiudi l'avviso"))
        close.setAutoRaise(True)
        close.clicked.connect(lambda _c=False: on_close())
        lay.addWidget(close)
        self.close_button = close
        self.restyle()

    def restyle(self) -> None:
        bg = theme.hex_color("accent_soft")
        self.setStyleSheet(
            f"QWidget#viewerReloadBar {{ background: {bg};"
            f" border-bottom: 1px solid {theme.hex_color('border')}; }}"
            f" QWidget#viewerReloadBar QLabel {{ background: transparent; color: {theme.hex_color('text')}; }}")
        name = "triangle-alert" if self.kind == BAR_MISSING else ("circle-alert" if self.kind == BAR_CHANGED
                                                                  else "refresh-cw")
        self.icon_label.setPixmap(icon(name).pixmap(18, 18))


class _Watch:
    """Stato del monitoraggio di un documento aperto."""

    def __init__(self, view: DocumentView, path: str):
        self.view = weakref.ref(view)
        self.path = path
        self.known = E.file_stamp(path)     # ultima versione caricata o salvata da Foliant
        self.last_seen = self.known         # ultima impronta osservata (per il debounce)
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.attempts = 0                   # tentativi con file non valido
        self.bar: ReloadBar | None = None
        self.listener = None


class ReloadController(QObject):
    reloaded = Signal(object)           # nuova DocumentView dopo una ricarica

    MAX_INVALID_ATTEMPTS = 40

    def __init__(self, ctx: Context, *, debounce_ms: int = 500, poll_ms: int = 2000):
        super().__init__(ctx)
        self.ctx = ctx
        self.debounce_ms = debounce_ms
        self.reload_count = 0
        self.watches: dict[DocumentView, _Watch] = {}
        self._bar_heights: "weakref.WeakKeyDictionary[DocumentView, int]" = weakref.WeakKeyDictionary()
        self._reloading = False

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_fs_event)
        self.watcher.directoryChanged.connect(self._on_dir_event)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll)
        self.set_poll_interval(poll_ms)

        self.act_revert = ctx.action(_("Ripristina"), "rotate-ccw", self.revert,
                                     tooltip=_("Scarta le modifiche e riapri l'ultima versione salvata"))
        ctx.add_menu_action(self.act_revert, _("File"))
        self._build_mode_menu()
        ctx.documentOpened.connect(self._on_opened)
        ctx.documentClosed.connect(self._on_closed)
        ctx.currentViewChanged.connect(lambda *_a: self._update_action())
        theme.on_change(self._restyle)
        for view in ctx.views():
            self._on_opened(view)
        self._update_action()

    # ------------------------------------------------------------- preferenze
    @property
    def mode(self) -> str:
        return E.normalize_mode(self.ctx.settings().value(KEY_MODE, E.MODE_AUTO))

    def set_mode(self, mode: str) -> None:
        self.ctx.settings().setValue(KEY_MODE, E.normalize_mode(mode))
        if hasattr(self, "mode_actions"):
            self._sync_mode_menu()

    def _build_mode_menu(self) -> None:
        from PySide6.QtGui import QActionGroup
        from PySide6.QtWidgets import QMenu
        self.mode_menu = QMenu(_("Ricarica dei documenti modificati da altre applicazioni"), self.ctx.window)
        group = QActionGroup(self.mode_menu)
        self.mode_actions = {}
        for key, label in ((E.MODE_AUTO, _("Automatica")), (E.MODE_ASK, _("Chiedi")), (E.MODE_NEVER, _("Mai"))):
            act = self.mode_menu.addAction(label)
            act.setCheckable(True)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_mode(k))
            self.mode_actions[key] = act
        self.mode_menu.aboutToShow.connect(self._sync_mode_menu)
        self._sync_mode_menu()
        self.act_mode = self.mode_menu.menuAction()
        bind_icon(self.act_mode, "refresh-cw")
        self.ctx.add_menu_action(self.act_mode, _("File"))

    def _sync_mode_menu(self) -> None:
        for key, act in self.mode_actions.items():
            act.setChecked(key == self.mode)

    def set_poll_interval(self, ms: int) -> None:
        self.poll.setInterval(max(20, int(ms)))
        self.poll.start()

    # ------------------------------------------------------------- monitoraggio
    def _on_opened(self, view: DocumentView) -> None:
        if view in self.watches:
            return
        doc = view.document
        w = _Watch(view, str(doc.path) if doc.path else "")
        w.timer.timeout.connect(lambda v=view: self._settle(v))

        def listener(_doc, kind, v=weakref.ref(view)):
            if kind == SAVED and v() is not None:
                self._on_saved(v())
        w.listener = listener
        doc.add_listener(listener)
        self.watches[view] = w
        if w.path:
            self._watch_path(w.path)
        self._update_action()

    def _on_closed(self, view: DocumentView) -> None:
        w = self.watches.pop(view, None)
        if w is None:
            return
        w.timer.stop()
        try:
            view.document.remove_listener(w.listener)
        except Exception:
            pass
        self._remove_bar(view)
        if w.path and not any(o.path == w.path for o in self.watches.values()):
            self.watcher.removePath(w.path)
        self._update_action()

    def _watch_path(self, path: str) -> None:
        if os.path.exists(path) and path not in self.watcher.files():
            self.watcher.addPath(path)
        d = os.path.dirname(path)
        if d and os.path.isdir(d) and d not in self.watcher.directories():
            self.watcher.addPath(d)

    def _on_saved(self, view: DocumentView) -> None:
        """Salvataggio di Foliant: il cambiamento sul disco non è esterno."""
        w = self.watches.get(view)
        if w is None:
            return
        path = str(view.document.path) if view.document.path else ""
        if path != w.path:
            w.path = path
        w.known = w.last_seen = E.file_stamp(path) if path else E.MISSING
        w.timer.stop()
        w.attempts = 0
        if path:
            self._watch_path(path)
        self._remove_bar(view)
        self._update_action()

    def _on_fs_event(self, path: str) -> None:
        # Gli editor spesso sostituiscono il file: il watcher lo perde, va riaggiunto.
        if os.path.exists(path) and path not in self.watcher.files():
            self.watcher.addPath(path)
        for view, w in list(self.watches.items()):
            if w.path == path:
                self._observe(view, w)

    def _on_dir_event(self, directory: str) -> None:
        for view, w in list(self.watches.items()):
            if w.path and os.path.dirname(w.path) == directory:
                self._watch_path(w.path)
                self._observe(view, w)

    def _poll(self) -> None:
        """Ripiego per i percorsi senza notifiche (es. di rete)."""
        for view, w in list(self.watches.items()):
            if w.path:
                self._observe(view, w)

    def _observe(self, view: DocumentView, w: _Watch) -> None:
        s = E.file_stamp(w.path)
        if s == w.last_seen and not w.timer.isActive():
            return
        if s != w.last_seen:
            w.last_seen = s
            w.timer.start(self.debounce_ms)   # ricomincia l'attesa a ogni variazione

    def _settle(self, view: DocumentView) -> None:
        """Nessuna variazione da `debounce_ms`: decide cosa fare."""
        w = self.watches.get(view)
        if w is None:
            return
        s = E.file_stamp(w.path)
        if s != w.last_seen:
            w.last_seen = s
            w.timer.start(self.debounce_ms)
            return
        if not s.exists:
            self._show_bar(view, BAR_MISSING)
            return
        if w.bar is not None and w.bar.kind == BAR_MISSING:
            self._remove_bar(view)
        if s == w.known:
            return
        if not E.looks_complete(w.path):
            self._postpone(view, w)
            return
        action = E.decide(self.mode, view.document.is_modified)
        if action == E.RELOAD:
            self.reload_view(view, interactive=False)
        elif action == E.CONFLICT:
            self._show_bar(view, BAR_CHANGED)
        elif action == E.ASK:
            self._show_bar(view, BAR_ASK)
        else:
            w.known = s

    def _postpone(self, view: DocumentView, w: _Watch) -> None:
        """File temporaneamente non valido: si riprova più tardi."""
        w.attempts += 1
        if w.attempts > self.MAX_INVALID_ATTEMPTS:
            w.attempts = 0
            w.known = w.last_seen   # file rimasto non valido: si smette di riprovare
            return
        w.timer.start(self.debounce_ms)

    # ------------------------------------------------------------- ricarica
    def _update_action(self) -> None:
        view = self.ctx.current_view()
        self.act_revert.setEnabled(view is not None and view.document.path is not None)

    def confirm_revert(self, view: DocumentView) -> bool:
        return QMessageBox.question(
            self.ctx.window, _("Ripristina il documento"),
            _("Scartare le modifiche non salvate a «{name}» e riaprire l'ultima versione salvata?").format(
                name=view.document.display_name)) == QMessageBox.StandardButton.Yes

    def ask_password(self, view: DocumentView) -> str | None:
        pw, ok = QInputDialog.getText(
            self.ctx.window, _("Documento protetto"),
            _("La password di «{name}» è cambiata. Inserisci la nuova password:").format(
                name=view.document.display_name), QLineEdit.EchoMode.Password)
        return pw if ok else None

    def revert(self, view: DocumentView | None = None) -> DocumentView | None:
        view = view or self.ctx.current_view()
        if view is None or view.document.path is None:
            return None
        if view.document.is_modified and not self.confirm_revert(view):
            return None
        return self.reload_view(view)

    def _load(self, view: DocumentView, interactive: bool) -> Document | None:
        doc = view.document
        password = doc.password
        for _attempt in range(5):
            try:
                return Document.open(doc.path, password)
            except (PasswordRequired, WrongPassword):
                # la password si richiede di nuovo solo se è cambiata
                password = self.ask_password(view)
                if password is None:
                    return None
            except DocumentError as e:
                if interactive:
                    self.ctx.error(_("Impossibile ricaricare"), str(e))
                return None
        return None

    def reload_view(self, view: DocumentView, interactive: bool = True) -> DocumentView | None:
        """Riapre il file dal disco nella stessa scheda, alla stessa posizione."""
        if self._reloading or view.document.path is None:
            return None
        w = self.watches.get(view)
        new_doc = self._load(view, interactive)
        if new_doc is None:
            if w is not None and not interactive:
                self._postpone(view, w)   # file non valido durante la scrittura
            return None
        state = {
            "page": view.current_page, "zoom": view.zoom, "fit": view.fit_mode, "mode": view.view_mode,
            "rotation": view.view_rotation, "count": view.page_count,
            "h": view.horizontalScrollBar().value(), "v": view.verticalScrollBar().value(),
        }
        window = self.ctx.window
        tabs = window.tabs
        previous = self.ctx.current_view()
        self._reloading = True
        try:
            new_view = window.add_document(new_doc)
            old_index = tabs.indexOf(view)
            new_index = tabs.indexOf(new_view)
            if old_index >= 0 and new_index >= 0 and new_index != old_index:
                tabs.tabBar().moveTab(new_index, old_index)
            # chiusura della vecchia vista senza richiesta di salvataggio
            view.document._saved_state = view.document._state
            window.close_view(view)
            # la scheda attiva resta quella di prima
            tabs.setCurrentWidget(new_view if previous is view or previous is None else previous)
        finally:
            self._reloading = False
        self._apply_state(new_view, state)
        QTimer.singleShot(0, lambda v=new_view, s=state: self._apply_state(v, s) if v in self.watches else None)
        self.reload_count += 1
        self.ctx.status(_("Ricaricato: {name}").format(name=new_doc.display_name))
        self.reloaded.emit(new_view)
        return new_view

    @staticmethod
    def _apply_state(view: DocumentView, s: dict) -> None:
        try:
            if view.view_rotation != s["rotation"]:
                view.rotate_view((s["rotation"] - view.view_rotation) % 360)
            view.set_view_mode(s["mode"])
            if s["fit"]:
                view.set_fit(s["fit"])
            else:
                view.set_zoom(s["zoom"])
            page = E.clamp_page(s["page"], view.page_count)
            view.go_to_page(page)
            if view.page_count == s["count"]:
                view.horizontalScrollBar().setValue(s["h"])
                view.verticalScrollBar().setValue(s["v"])
        except RuntimeError:
            pass   # vista già distrutta

    # ------------------------------------------------------------- barre
    def _show_bar(self, view: DocumentView, kind: str) -> None:
        w = self.watches.get(view)
        if w is None:
            return
        if w.bar is not None and w.bar.kind == kind:
            return
        self._remove_bar(view)
        name = view.document.display_name
        if kind == BAR_CHANGED:
            text = _("Il file «{name}» è stato modificato da un'altra applicazione, ma qui ci sono modifiche "
                     "non salvate.").format(name=name)
            buttons = [(_("Ricarica e perdi le modifiche"), lambda v=view: self.reload_view(v), False),
                       (_("Ignora"), lambda v=view: self.ignore(v), False),
                       (_("Salva con nome…"), lambda v=view: self.save_as(v), True)]
        elif kind == BAR_ASK:
            text = _("Il file «{name}» è stato modificato da un'altra applicazione.").format(name=name)
            buttons = [(_("Ricarica"), lambda v=view: self.reload_view(v), True),
                       (_("Ignora"), lambda v=view: self.ignore(v), False)]
        else:
            text = _("Il file «{name}» è stato eliminato o spostato.").format(name=name)
            buttons = [(_("Salva"), lambda v=view: self.save(v), True)]
        on_close = (lambda v=view: self.ignore(v)) if kind != BAR_MISSING else (lambda v=view: self._remove_bar(v))
        bar = ReloadBar(kind, text, buttons, on_close, view)
        w.bar = bar
        view.installEventFilter(self)
        bar.show()
        bar.raise_()
        self._layout(view)

    def _remove_bar(self, view: DocumentView) -> None:
        w = self.watches.get(view)
        bar = w.bar if w is not None else None
        if bar is None:
            return
        w.bar = None
        bar.hide()
        bar.deleteLater()
        self._layout(view)

    def bar(self, view: DocumentView) -> ReloadBar | None:
        w = self.watches.get(view)
        return w.bar if w is not None else None

    def _layout(self, view: DocumentView) -> None:
        """Posiziona la barra sotto eventuali altre barre (margine superiore già impostato)."""
        try:
            mine = self._bar_heights.get(view, 0)
            base = max(0, view.viewportMargins().top() - mine)
        except RuntimeError:
            return
        w = self.watches.get(view)
        bar = w.bar if w is not None else None
        h = bar.sizeHint().height() if bar is not None else 0
        if bar is not None:
            bar.setGeometry(0, base, view.width(), h)
            bar.raise_()
        self._bar_heights[view] = h
        m = view.viewportMargins()
        view.setViewportMargins(m.left(), base + h, m.right(), m.bottom())

    def _restyle(self) -> None:
        for w in self.watches.values():
            if w.bar is not None:
                w.bar.restyle()

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.Resize and isinstance(obj, DocumentView) and obj in self.watches:
            QTimer.singleShot(0, lambda v=obj: self._layout(v) if v in self.watches else None)
        return False

    def ignore(self, view: DocumentView) -> None:
        """Mantiene il documento aperto così com'è fino al prossimo cambiamento del file."""
        w = self.watches.get(view)
        if w is not None:
            w.known = w.last_seen = E.file_stamp(w.path)
        self._remove_bar(view)

    def save_as(self, view: DocumentView) -> bool:
        return self.ctx.window.save_view(view, save_as=True)

    def save(self, view: DocumentView) -> bool:
        """Ricrea il file eliminato o spostato al suo percorso."""
        path = view.document.path
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return self.ctx.window.save_view(view)


def install(ctx: Context) -> ReloadController:
    add_translations("en", TRANSLATIONS)
    return ReloadController(ctx)
