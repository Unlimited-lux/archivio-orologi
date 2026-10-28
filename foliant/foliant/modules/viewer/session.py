"""Vista iniziale (VIS-05) e schede (VIS-30).

- Regola di riservatezza: Foliant non conserva su disco, tra un avvio e l'altro,
  nomi, percorsi o stato dei documenti aperti. Niente posizione per file,
  niente ripristino della sessione: ogni documento si apre con i valori
  predefiniti dell'utente e la vista iniziale chiesta dal documento.
- Vista iniziale chiesta dal documento (`/OpenAction`, `/PageLayout`,
  `/PageMode`), salvo la preferenza «Ignora le impostazioni di apertura dei
  documenti». `/FullScreen` non viene applicato da solo: si propone un
  pulsante nella barra di stato.
- Schede: Ctrl+Tab / Ctrl+Maiusc+Tab, Alt+1…9, Riapri scheda chiusa
  (Ctrl+Maiusc+T, elenco solo in memoria finché l'app è aperta), Chiudi le
  altre / a destra / tutte, menu contestuale.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QMenu, QPushButton

from foliant.api import Context, DocumentView, _, add_translations, bind_icon

from . import session_engine as engine
from .session_engine import ViewState

KEY_IGNORE_DOC_VIEW = "viewer/ignore_document_view"


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class SessionController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.window = ctx.window
        self.closed_tabs: list[tuple[str, ViewState, int]] = []
        self._closing_window = False
        self.last_initial_view: engine.InitialView | None = None
        ctx.documentOpened.connect(self._on_opened)
        ctx.documentClosed.connect(self._on_closed)
        self.window.installEventFilter(self)
        self._build_actions()
        self._build_tab_menu()
        self._build_fullscreen_prompt()

    # ================================================================ preferenze
    def settings(self):
        return self.ctx.settings()

    def pref(self, key: str, default: bool = False) -> bool:
        return _as_bool(self.settings().value(key, default), default)

    def set_pref(self, key: str, value: bool) -> None:
        self.settings().setValue(key, bool(value))
        act = self.pref_actions.get(key)
        if act is not None and act.isChecked() != bool(value):
            act.setChecked(bool(value))

    # ================================================================ azioni
    def _build_actions(self) -> None:
        ctx = self.ctx
        # Ctrl+Tab, Ctrl+Maiusc+Tab, Ctrl+Maiusc+T e Alt+1…9 sono scorciatoie globali del nucleo (UX-18):
        # qui solo le voci di menu, senza scorciatoia (scorciatoie duplicate si annullano in Qt).
        self.act_next_tab = ctx.action(_("Scheda successiva"), "chevron-right", self.next_tab)
        self.act_prev_tab = ctx.action(_("Scheda precedente"), "chevron-left", self.previous_tab)
        self.act_reopen = ctx.action(_("Riapri scheda chiusa"), "history", self.reopen_closed_tab,
                                     requires_document=False)
        self.act_close_others = ctx.action(_("Chiudi le altre schede"), "copy-x", lambda: self.close_other_tabs())
        self.act_close_right = ctx.action(_("Chiudi le schede a destra"), "arrow-right-to-line",
                                          lambda: self.close_tabs_to_right())
        self.act_close_all = ctx.action(_("Chiudi tutte le schede"), "square-x", self.close_all_tabs)

        self.pref_actions: dict[str, QAction] = {}
        for key, text in (
            (KEY_IGNORE_DOC_VIEW, _("Ignora le impostazioni di apertura dei documenti")),
        ):
            act = ctx.action(text, None, None, checkable=True, requires_document=False)
            act.setChecked(self.pref(key))
            act.toggled.connect(lambda on, k=key: self.settings().setValue(k, bool(on)))
            self.pref_actions[key] = act

        section = _("Schede")
        for act in (self.act_next_tab, self.act_prev_tab, self.act_reopen, self.act_close_others,
                    self.act_close_right, self.act_close_all):
            ctx.add_menu_action(act, section)
        ctx.add_menu_action(self.pref_actions[KEY_IGNORE_DOC_VIEW], _("Apertura dei documenti"))
        self.window.main_menu.aboutToShow.connect(self._update_actions)

    def _update_actions(self) -> None:
        self.act_reopen.setEnabled(bool(self.closed_tabs))
        for key, act in self.pref_actions.items():
            act.setChecked(self.pref(key))

    # ================================================================ schede
    @property
    def tabs(self):
        return self.window.tabs

    def activate_tab(self, index: int) -> DocumentView | None:
        if not 0 <= index < self.tabs.count():
            return None
        view = self.tabs.widget(index)
        self.ctx.activate_view(view)
        view.setFocus()
        return view

    def next_tab(self) -> None:
        n = self.tabs.count()
        if n:
            self.activate_tab((self.tabs.currentIndex() + 1) % n)

    def previous_tab(self) -> None:
        n = self.tabs.count()
        if n:
            self.activate_tab((self.tabs.currentIndex() - 1) % n)

    def jump_to_tab(self, number: int) -> None:
        """Alt+1…8 vanno alla scheda indicata, Alt+9 all'ultima."""
        n = self.tabs.count()
        if not n:
            return
        self.activate_tab(n - 1 if number == 9 else number - 1)

    def _tab_views(self) -> list[DocumentView]:
        return [w for w in (self.tabs.widget(i) for i in range(self.tabs.count())) if isinstance(w, DocumentView)]

    def _close(self, views: list[DocumentView]) -> bool:
        for view in views:
            if not self.window.close_view(view):
                return False   # l'utente ha annullato: ci si ferma
        return True

    def close_other_tabs(self, keep: DocumentView | None = None) -> bool:
        keep = keep or self.ctx.current_view()
        return self._close([v for v in self._tab_views() if v is not keep])

    def close_tabs_to_right(self, of: DocumentView | None = None) -> bool:
        of = of or self.ctx.current_view()
        index = self.tabs.indexOf(of) if of is not None else -1
        if index < 0:
            return True
        return self._close([v for v in self._tab_views() if self.tabs.indexOf(v) > index])

    def close_all_tabs(self) -> bool:
        return self._close(self._tab_views())

    def reopen_closed_tab(self) -> DocumentView | None:
        """Riapre l'ultimo file chiuso alla stessa pagina e zoom (Ctrl+Maiusc+T)."""
        while self.closed_tabs:
            path, state, index = self.closed_tabs.pop()
            if not os.path.isfile(path):
                self.ctx.status(_("Il file «{name}» non esiste più.").format(name=Path(path).name), 5000)
                continue
            view = self.ctx.open_path(path)
            if view is None:
                return None
            self.apply_state(view, state)
            current = self.tabs.indexOf(view)
            if 0 <= index < self.tabs.count() and current != index:
                self.tabs.tabBar().moveTab(current, index)
            return view
        self.ctx.status(_("Nessuna scheda chiusa da riaprire."), 3000)
        return None

    # ---------------------------------------------------------------- menu contestuale
    def _build_tab_menu(self) -> None:
        # voci aggiunte al menu della scheda del nucleo (insieme a quelle degli altri moduli)
        self.ctx.add_tab_menu_provider(self._tab_menu_provider)

    def _tab_menu_provider(self, view: DocumentView, menu: QMenu) -> None:
        index = self.tabs.indexOf(view)
        count = self.tabs.count()

        def add(text, icon_name, slot, enabled=True):
            act = menu.addAction(text)
            if icon_name:
                bind_icon(act, icon_name)
            act.triggered.connect(slot)
            act.setEnabled(enabled)
            return act

        add(_("Chiudi le altre schede"), "copy-x", lambda: self.close_other_tabs(view), count > 1)
        add(_("Chiudi le schede a destra"), "arrow-right-to-line", lambda: self.close_tabs_to_right(view),
            0 <= index < count - 1)
        add(_("Chiudi tutte le schede"), "square-x", self.close_all_tabs, count > 0)
        menu.addSeparator()
        add(_("Riapri scheda chiusa"), "history", self.reopen_closed_tab, bool(self.closed_tabs))
        path = view.document.path
        menu.addSeparator()
        add(_("Copia percorso"), "copy",
            lambda: QGuiApplication.clipboard().setText(str(path)), path is not None)

    def build_tab_menu(self, index: int) -> QMenu | None:
        """Menu completo della scheda (nucleo + moduli), per i test."""
        return self.window.tab_menu(index)

    # ================================================================ stato della vista e vista iniziale
    def apply_state(self, view: DocumentView, state: ViewState) -> None:
        if state.mode:
            view.set_view_mode(state.mode)
        if state.fit:
            view.set_fit(state.fit)
        elif state.zoom:
            view.set_zoom(state.zoom)
        if view.page_count:
            view.go_to_page(min(max(0, state.page), view.page_count - 1))
        self._refresh_window(view)

    def _refresh_window(self, view: DocumentView) -> None:
        if view is self.ctx.current_view():
            self.ctx.refresh_state()

    @staticmethod
    def _key(path) -> str:
        return os.path.abspath(str(path))

    def _on_opened(self, view: DocumentView) -> None:
        # nessuna posizione ricordata per file (riservatezza): valori predefiniti e vista del documento
        prefs = getattr(self.ctx, "viewer_parts", {}).get("prefs")
        if prefs is not None:
            prefs.apply_defaults(view)   # valori predefiniti dell'utente (VIS-51)
        if not self.pref(KEY_IGNORE_DOC_VIEW):
            self.apply_initial_view(view)

    def apply_initial_view(self, view: DocumentView) -> engine.InitialView:
        doc = view.document
        with doc.lock:
            initial = engine.read_initial_view(doc.pdf)
        self.last_initial_view = initial
        if initial.mode:
            view.set_view_mode(initial.mode)
        if initial.fit:
            view.set_fit(initial.fit)
        elif initial.zoom:
            view.set_zoom(initial.zoom)
        if initial.page is not None and initial.page != view.current_page:
            view.go_to_page(initial.page)
        if initial.panel:
            self.ctx.show_sidebar_panel(initial.panel)
        if initial.fullscreen and not self.window.isFullScreen() and not self._fullscreen_handled_elsewhere():
            self.offer_fullscreen()
        if not initial.empty:
            self._refresh_window(view)
        return initial

    def _on_closed(self, view: DocumentView) -> None:
        doc = view.document
        if doc.path is None:
            return
        # solo in memoria, per «Riapri scheda chiusa»: nulla su disco
        if not self._closing_window:
            self.closed_tabs.append((self._key(doc.path), engine.view_state(view), self.tabs.indexOf(view)))
            del self.closed_tabs[:-engine.MAX_CLOSED]

    # ---------------------------------------------------------------- schermo intero proposto
    def _build_fullscreen_prompt(self) -> None:
        self.fullscreen_button = QPushButton(_("Schermo intero"), self.window)
        self.fullscreen_button.setObjectName("fullscreenOffer")
        bind_icon(self.fullscreen_button, "expand")
        self.fullscreen_button.setFlat(True)
        self.fullscreen_button.hide()
        self.fullscreen_button.clicked.connect(self._enter_fullscreen)
        self.window.statusBar().insertPermanentWidget(0, self.fullscreen_button)
        self._fs_timer = QTimer(self)
        self._fs_timer.setSingleShot(True)
        self._fs_timer.timeout.connect(self.fullscreen_button.hide)

    def _fullscreen_handled_elsewhere(self) -> bool:
        """La parte «reading» può gestire da sé `/PageMode /FullScreen`: niente doppia proposta."""
        reading = getattr(self.ctx, "viewer_parts", {}).get("reading")
        return reading is not None and hasattr(reading, "ask_fullscreen")

    def offer_fullscreen(self) -> None:
        self.ctx.status(_("Il documento chiede di essere mostrato a schermo intero."), 15000)
        self.fullscreen_button.show()
        self._fs_timer.start(15000)

    def _enter_fullscreen(self) -> None:
        self.fullscreen_button.hide()
        act = getattr(self.window, "act_fullscreen", None)
        if act is not None and not self.window.isFullScreen():
            act.trigger()

    # ================================================================ chiusura della finestra
    def eventFilter(self, obj, event) -> bool:
        # durante la chiusura della finestra le schede non finiscono tra quelle da riaprire
        if obj is self.window and event.type() == QEvent.Type.Close:
            self._closing_window = True
            QTimer.singleShot(0, self, self._reset_closing)
        return False

    def _reset_closing(self) -> None:
        self._closing_window = False


def install(ctx: Context) -> SessionController:
    add_translations("en", {
        "Scheda successiva": "Next tab",
        "Scheda precedente": "Previous tab",
        "Riapri scheda chiusa": "Reopen closed tab",
        "Chiudi scheda": "Close tab",
        "Chiudi le altre schede": "Close other tabs",
        "Chiudi le schede a destra": "Close tabs to the right",
        "Chiudi tutte le schede": "Close all tabs",
        "Copia percorso": "Copy path",
        "Ignora le impostazioni di apertura dei documenti": "Ignore documents' opening settings",
        "Schede": "Tabs",
        "Apertura dei documenti": "Opening documents",
        "Il file «{name}» non esiste più.": "The file “{name}” no longer exists.",
        "Nessuna scheda chiusa da riaprire.": "No closed tab to reopen.",
        "Schermo intero": "Full screen",
        "Il documento chiede di essere mostrato a schermo intero.": "The document asks to be shown full screen.",
    })
    return SessionController(ctx)
