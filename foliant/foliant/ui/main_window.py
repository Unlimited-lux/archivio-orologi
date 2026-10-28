"""Finestra principale: barra modalità, schede documento, pannelli, barra di stato."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIntValidator, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.document import CONTENT, Document
from ..core.errors import DocumentError, PasswordRequired, WrongPassword
from ..i18n import _, pgettext, set_language
from . import theme
from .context import PDF_FILTER, Context, Mode
from .document_view import FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE, DocumentView
from .home import HomeScreen
from .icons import app_icon, bind_icon

MAX_RECENT = 15


class PageField(QLineEdit):
    """Campo numero pagina: Invio per saltare alla pagina digitata."""

    pageRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max = 1
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(64)
        self.setValidator(QIntValidator(1, 999999, self))
        self.returnPressed.connect(self._submit)

    def set_page(self, page: int, total: int) -> None:
        self._max = max(1, total)
        if not self.hasFocus():
            self.setText(str(page))

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return 0

    def _submit(self) -> None:
        value = self.value()
        if 1 <= value <= self._max:
            self.pageRequested.emit(value)
        self.clearFocus()
OPEN_FILTER = "Documenti (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.xps *.epub *.svg *.txt);;PDF (*.pdf);;Tutti i file (*)"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, *, load_modules: bool = True):
        super().__init__()
        self.settings = settings or QSettings()
        self.ctx = Context(self)
        self.modes: dict[str, Mode] = {}
        self.current_mode_id: str | None = None
        self._document_actions: list[QAction] = []
        self._menu_sections: "OrderedDict[str, list[QAction]]" = OrderedDict()
        self._sidebar: dict[str, tuple[str, str, QWidget, int, QAction]] = {}
        self._props: dict[str, QWidget] = {}
        self._connected_view: DocumentView | None = None
        self.view_menu_providers: list = []
        self.module_results: list[tuple[str, Exception | None]] = []
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1320, 860)
        self._build_ui()
        self._build_core_actions()
        self._build_read_mode()
        if load_modules:
            from ..modules import load_modules as _load
            self.module_results = _load(self.ctx)
            failed = [name for name, err in self.module_results if err is not None]
            if failed:
                QTimer.singleShot(0, lambda: self.statusBar().showMessage(
                    _("Alcuni moduli non sono stati caricati: {m}").format(m=", ".join(failed)), 10000))
        self.set_mode("leggi")
        self._refresh_recent()
        self._update_state()
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # ================================================================== UI base
    def _build_ui(self) -> None:
        # Barra in alto
        self.header = QToolBar(_("Barra principale"), self)
        self.header.setObjectName("header")
        self.header.setMovable(False)
        self.header.setFloatable(False)
        self.header.setIconSize(QSize(20, 20))
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.header.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.header)

        self.home_button = QToolButton(self)
        self.home_button.setToolTip(_("Home"))
        self.home_button.setIcon(app_icon())
        self.home_button.setIconSize(QSize(26, 26))
        self.home_button.setAutoRaise(True)
        self.home_button.clicked.connect(self.show_home)
        self.header.addWidget(self.home_button)

        self._mode_bar = QWidget(self)
        self._mode_layout = QHBoxLayout(self._mode_bar)
        self._mode_layout.setContentsMargins(12, 0, 12, 0)
        self._mode_layout.setSpacing(2)

        # Centro: barra contestuale + pannelli
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.context_stack = QStackedWidget(central)
        self.context_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.context_stack)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        layout.addWidget(self.splitter, 1)

        self.sidebar = QWidget(self.splitter)
        self.sidebar.setObjectName("sidebar")
        side_layout = QHBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        self.sidebar_strip = QToolBar(self.sidebar)
        self.sidebar_strip.setOrientation(Qt.Orientation.Vertical)
        self.sidebar_strip.setIconSize(QSize(20, 20))
        self.sidebar_group = QActionGroup(self)
        self.sidebar_group.setExclusive(True)
        side_layout.addWidget(self.sidebar_strip)
        self.sidebar_stack = QStackedWidget(self.sidebar)
        self.sidebar_stack.setMinimumWidth(200)
        side_layout.addWidget(self.sidebar_stack, 1)
        self.sidebar.hide()

        self.docs_stack = QStackedWidget(self.splitter)
        self.home = HomeScreen(self)
        self.home.openRequested.connect(self.open_dialog)
        self.home.newRequested.connect(self.new_document)
        self.home.pathRequested.connect(self.open_path)
        self.docs_stack.addWidget(self.home)
        self.tabs = QTabWidget(self.docs_stack)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.close_view(self.tabs.widget(i)))
        self.tabs.currentChanged.connect(self._on_current_changed)
        self.docs_stack.addWidget(self.tabs)

        self.properties = QStackedWidget(self.splitter)
        self.properties.setObjectName("properties")
        self.properties.setMinimumWidth(260)
        self.properties.hide()

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.setCentralWidget(central)
        self._build_statusbar()

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.page_box = QWidget(self)
        self.page_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        hl = QHBoxLayout(self.page_box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        hl.addWidget(QLabel(_("Pagina")))
        self.page_spin = PageField(self.page_box)
        self.page_spin.pageRequested.connect(self._on_page_spin)
        hl.addWidget(self.page_spin)
        self.page_total = QLabel("/ 0")
        hl.addWidget(self.page_total)
        sb.addPermanentWidget(self.page_box)

        self.zoom_combo = QComboBox(self)
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_combo.setFixedWidth(150)
        self.zoom_combo.addItem(_("Adatta larghezza"), FIT_WIDTH)
        self.zoom_combo.addItem(_("Adatta pagina"), FIT_PAGE)
        for z in (50, 75, 100, 125, 150, 200, 300, 400):
            self.zoom_combo.addItem(f"{z}%", z / 100)
        self.zoom_combo.activated.connect(self._on_zoom_combo)
        self.zoom_combo.lineEdit().returnPressed.connect(self._on_zoom_text)
        sb.addPermanentWidget(self.zoom_combo)

    # ================================================================== azioni
    def _act(self, text, icon_name=None, slot=None, shortcut=None, *, requires_document=False, checkable=False):
        return self.ctx.action(text, icon_name, slot, shortcut=shortcut, checkable=checkable,
                               requires_document=requires_document)

    def _build_core_actions(self) -> None:
        a = self._act
        self.act_new = a(_("Nuovo documento"), "file-plus", self.new_document, "Ctrl+N")
        self.act_open = a(_("Apri…"), "folder-open", self.open_dialog, QKeySequence.StandardKey.Open)
        self.act_save = a(_("Salva"), "save", lambda: self.save_view(self.current_view()),
                          QKeySequence.StandardKey.Save, requires_document=True)
        self.act_save_as = a(_("Salva con nome…"), "save-all",
                             lambda: self.save_view(self.current_view(), save_as=True),
                             "Ctrl+Shift+S", requires_document=True)
        self.act_save_copy = a(_("Salva una copia…"), "copy", self.save_copy, requires_document=True)
        self.act_close = a(_("Chiudi documento"), "x", lambda: self.close_view(self.current_view()),
                           QKeySequence.StandardKey.Close, requires_document=True)
        self.act_quit = a(_("Esci"), "log-out", self.close, QKeySequence.StandardKey.Quit)
        self.act_undo = a(_("Annulla"), "undo-2", self.undo, QKeySequence.StandardKey.Undo, requires_document=True)
        self.act_redo = a(_("Ripeti"), "redo-2", self.redo, QKeySequence.StandardKey.Redo, requires_document=True)
        self.act_redo.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        self.act_zoom_in = a(_("Aumenta zoom"), "zoom-in", lambda: self._with_view(lambda v: v.zoom_in()),
                             QKeySequence.StandardKey.ZoomIn, requires_document=True)
        self.act_zoom_in.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        self.act_zoom_out = a(_("Riduci zoom"), "zoom-out", lambda: self._with_view(lambda v: v.zoom_out()),
                              QKeySequence.StandardKey.ZoomOut, requires_document=True)
        self.act_zoom_100 = a(_("Dimensione reale"), "scan", lambda: self._with_view(lambda v: v.set_zoom(1.0)),
                              "Ctrl+1", requires_document=True)
        self.act_fit_width = a(_("Adatta alla larghezza"), "move-horizontal",
                               lambda: self._with_view(lambda v: v.set_fit(FIT_WIDTH)), "Ctrl+2", requires_document=True)
        self.act_fit_page = a(_("Adatta alla pagina"), "maximize",
                              lambda: self._with_view(lambda v: v.set_fit(FIT_PAGE)), "Ctrl+0", requires_document=True)
        self.act_rotate_view = a(_("Ruota vista"), "rotate-cw", lambda: self._with_view(lambda v: v.rotate_view(90)),
                                 "Ctrl+R", requires_document=True)
        self.act_first = a(_("Prima pagina"), "chevrons-up", lambda: self._with_view(lambda v: v.go_to_page(0)),
                           requires_document=True)
        self.act_prev = a(_("Pagina precedente"), "chevron-up", lambda: self._with_view(lambda v: v.previous_page()),
                          requires_document=True)
        self.act_next = a(_("Pagina successiva"), "chevron-down", lambda: self._with_view(lambda v: v.next_page()),
                          requires_document=True)
        self.act_last = a(_("Ultima pagina"), "chevrons-down",
                          lambda: self._with_view(lambda v: v.go_to_page(v.page_count - 1)), requires_document=True)
        self.act_fullscreen = a(_("Schermo intero"), "expand", self.toggle_fullscreen, "F11", checkable=True)
        self.act_hand = a(_("Mano (trascina)"), "hand", self._toggle_hand, "H", checkable=True, requires_document=True)
        self.act_select = a(_("Seleziona testo"), "text-cursor", lambda: self.ctx.set_tool(None), "V",
                            checkable=True, requires_document=True)
        tool_group = QActionGroup(self)
        tool_group.addAction(self.act_select)
        tool_group.addAction(self.act_hand)
        self.act_select.setChecked(True)

        self.view_mode_group = QActionGroup(self)
        self.view_mode_actions: dict[str, QAction] = {}
        for mode, text, icon_name in (
            (VIEW_CONTINUOUS, _("Scorrimento continuo"), "rows-3"),
            (VIEW_SINGLE, _("Pagina singola"), "file"),
            (VIEW_FACING, _("Due pagine"), "book-open"),
            (VIEW_COVER, _("Due pagine con copertina"), "book"),
        ):
            act = a(text, icon_name, lambda _c=False, m=mode: self._with_view(lambda v: v.set_view_mode(m)),
                    checkable=True, requires_document=True)
            self.view_mode_group.addAction(act)
            self.view_mode_actions[mode] = act
        self.view_mode_actions[VIEW_CONTINUOUS].setChecked(True)

        self.act_preferences = a(_("Preferenze…"), "settings", self.show_preferences, "Ctrl+,")
        self.act_shortcuts = a(_("Scorciatoie da tastiera"), "keyboard", self.show_shortcuts, "Ctrl+?")
        self.act_help = a(_("Guida rapida"), "circle-help", self.show_help, "F1")
        self.act_reopen = a(_("Riapri scheda chiusa"), "history", self.reopen_closed, "Ctrl+Shift+T")
        self._build_navigation_shortcuts()

        for act in (self.act_open, self.act_save, self.act_undo, self.act_redo):
            self.header.addAction(act)
        self.header.addWidget(self._mode_bar)
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.header.addWidget(spacer)
        self.menu_button = QToolButton(self)
        bind_icon(self.menu_button, "menu")
        self.menu_button.setToolTip(_("Menu principale"))
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.main_menu = QMenu(self)
        self.main_menu.aboutToShow.connect(self._rebuild_menu)
        self.menu_button.setMenu(self.main_menu)
        self.header.addWidget(self.menu_button)

        # Barra di stato: pulsanti pagina
        sb = self.statusBar()
        for act in (self.act_prev, self.act_next):
            btn = QToolButton(self)
            btn.setDefaultAction(act)
            btn.setAutoRaise(True)
            sb.insertPermanentWidget(0, btn) if act is self.act_prev else sb.insertPermanentWidget(2, btn)
        for act in (self.act_zoom_out, self.act_zoom_in):
            btn = QToolButton(self)
            btn.setDefaultAction(act)
            btn.setAutoRaise(True)
            sb.addPermanentWidget(btn)

    def _shortcut(self, seq: str, slot) -> QAction:
        act = QAction(self)
        act.setShortcut(QKeySequence(seq))
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _build_navigation_shortcuts(self) -> None:
        """Scorciatoie globali della tabella UX-18 senza voce di menu propria."""
        self._closed_paths: list[str] = []
        self._shortcut("Ctrl+T", self.show_home)
        self._shortcut("Ctrl+Shift+?", self.show_shortcuts)
        self._shortcut("Ctrl+L", self._focus_page_field)
        for seq in ("Ctrl+Tab", "Ctrl+PgDown"):
            self._shortcut(seq, lambda: self._cycle_tab(1))
        for seq in ("Ctrl+Shift+Tab", "Ctrl+PgUp"):
            self._shortcut(seq, lambda: self._cycle_tab(-1))
        for n in range(1, 10):
            self._shortcut(f"Alt+{n}", lambda _c=False, n=n: self._go_tab(n - 1))
        for n in range(10):
            self._shortcut(f"Ctrl+Alt+{n}", lambda _c=False, n=n: self._mode_by_index(9 if n == 0 else n - 1))
        self._shortcut("F9", self.toggle_sidebar)
        self._shortcut("Shift+F9", self.toggle_properties)
        self._shortcut("F10", lambda: self.menu_button.showMenu())
        self._shortcut("F6", lambda: self._cycle_focus(1))
        self._shortcut("Shift+F6", lambda: self._cycle_focus(-1))

    def _focus_page_field(self) -> None:
        if self.current_view() is not None:
            self.page_spin.setFocus()
            self.page_spin.selectAll()

    def _show_tabs(self) -> None:
        if self.docs_stack.currentWidget() is not self.tabs:
            self.docs_stack.setCurrentWidget(self.tabs)
            self._on_current_changed()        # stato (azioni, titolo, barre) della scheda mostrata

    def _cycle_tab(self, step: int) -> None:
        n = self.tabs.count()
        if n:
            self._show_tabs()
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def _go_tab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self._show_tabs()
            self.tabs.setCurrentIndex(index)

    def _mode_by_index(self, index: int) -> None:
        ordered = sorted(self.modes.values(), key=lambda m: (m.order, m.title))
        if 0 <= index < len(ordered):
            self.set_mode(ordered[index].id)

    def toggle_sidebar(self) -> None:
        if self.sidebar.isVisible():
            self.sidebar.hide()
        elif self._sidebar and self.current_view() is not None:
            self.sidebar.show()
            self.sidebar_stack.show()
            self._sidebar_open = True

    def toggle_properties(self) -> None:
        if self.properties.isVisible():
            self.properties.hide()
        elif self.properties.count() and self.current_view() is not None:
            self.properties.show()

    def _cycle_focus(self, step: int) -> None:
        regions = [w for w in (self.header, self.sidebar_stack.currentWidget() if self.sidebar.isVisible() else None,
                               self.current_view() or self.docs_stack.currentWidget(),
                               self.properties.currentWidget() if self.properties.isVisible() else None)
                   if w is not None and w.isVisible()]
        if not regions:
            return
        focus = QApplication.focusWidget()
        current = 0
        for i, w in enumerate(regions):
            if focus is not None and (focus is w or w.isAncestorOf(focus)):
                current = i
                break
        target = regions[(current + step) % len(regions)]
        target.setFocus(Qt.FocusReason.TabFocusReason)
        if not target.hasFocus():
            target.focusNextChild()

    def reopen_closed(self) -> None:
        while self._closed_paths:
            path = self._closed_paths.pop()
            if os.path.exists(path):
                self.open_path(path)
                return

    def show_preferences(self) -> None:
        from .dialogs import PreferencesDialog
        dlg = PreferencesDialog(self.settings, self)
        if dlg.exec():
            old_lang = self.settings.value("language", "")
            dlg.apply()
            self.set_theme(self.settings.value("theme", "system"))
            if dlg.language.currentData() != old_lang:
                self.statusBar().showMessage(_("La lingua verrà applicata al prossimo avvio."), 6000)

    def show_shortcuts(self) -> None:
        from .dialogs import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def show_help(self) -> None:
        from .dialogs import HelpDialog
        HelpDialog(self).exec()

    def _build_read_mode(self) -> None:
        mode = self.add_mode("leggi", _("Leggi"), "book-open-text", 10)
        for act in (self.act_select, self.act_hand):
            mode.add_action(act)
        mode.add_separator()
        for act in (self.act_zoom_out, self.act_zoom_in, self.act_fit_width, self.act_fit_page):
            mode.add_action(act)
        mode.add_separator()
        for act in self.view_mode_actions.values():
            mode.add_action(act)
        mode.add_separator()
        mode.add_action(self.act_rotate_view)
        mode.add_action(self.act_fullscreen)

    def register_document_action(self, act: QAction) -> None:
        self._document_actions.append(act)
        act.setEnabled(self.current_view() is not None)

    def add_menu_action(self, action: QAction, section: str) -> None:
        self._menu_sections.setdefault(section, []).append(action)

    def _rebuild_menu(self) -> None:
        m = self.main_menu
        m.clear()
        for act in (self.act_new, self.act_open):
            m.addAction(act)
        recent = m.addMenu(_("Apri recenti"))
        paths = self.recent_files()
        for p in paths:
            act = recent.addAction(Path(p).name)
            act.setToolTip(p)
            act.triggered.connect(lambda _c=False, p=p: self.open_path(p))
        if not paths:
            empty = recent.addAction(_("Nessun file recente"))
            empty.setEnabled(False)
        m.addSeparator()
        for act in (self.act_save, self.act_save_as, self.act_save_copy, self.act_close, self.act_reopen):
            m.addAction(act)
        for section, actions in self._menu_sections.items():
            m.addSeparator()
            sub = m.addMenu(section) if len(actions) > 4 else m
            for act in actions:
                sub.addAction(act)
        m.addSeparator()
        theme_menu = m.addMenu(_("Tema"))
        group = QActionGroup(theme_menu)
        for key, label in (("system", _("Come il sistema")), ("light", _("Chiaro")), ("dark", _("Scuro"))):
            act = theme_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(theme.mode() == key)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_theme(k))
        lang_menu = m.addMenu(_("Lingua"))
        current_lang = self.settings.value("language", "")
        for key, label in (("it", "Italiano"), ("en", "English")):
            act = lang_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current_lang == key)
            act.triggered.connect(lambda _c=False, k=key: self.set_language(k))
        m.addAction(self.act_preferences)
        m.addAction(self.act_shortcuts)
        m.addAction(self.act_help)
        m.addAction(_("Informazioni su Foliant"), self.about)
        m.addSeparator()
        m.addAction(self.act_quit)

    # ================================================================== modalità
    def add_mode(self, mode_id: str, title: str, icon_name: str, order: int = 100) -> Mode:
        if mode_id in self.modes:
            return self.modes[mode_id]
        toolbar = QToolBar(title, self)
        toolbar.setObjectName("contextbar")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setMovable(False)
        self.context_stack.addWidget(toolbar)
        button = QToolButton(self._mode_bar)
        button.setText(title)
        button.setProperty("modeButton", True)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(18, 18))
        bind_icon(button, icon_name, kind="mode")
        button.clicked.connect(lambda _c=False, m=mode_id: self.set_mode(m))
        mode = Mode(mode_id, title, icon_name, order, toolbar, button)
        self.modes[mode_id] = mode
        ordered = sorted(self.modes.values(), key=lambda m: (m.order, m.title))
        self._mode_layout.insertWidget(ordered.index(mode), button)
        return mode

    def set_mode(self, mode_id: str) -> None:
        mode = self.modes.get(mode_id)
        if mode is None:
            return
        if self.docs_stack.currentWidget() is self.home and self.tabs.count():
            self._show_tabs()                                  # dalla Home si torna ai documenti aperti
        previous = self.modes.get(self.current_mode_id) if self.current_mode_id else None
        if previous is mode:
            mode.button.setChecked(True)
            return
        if previous is not None:
            previous.button.setChecked(False)
            previous.left.emit()
        self.current_mode_id = mode_id
        mode.button.setChecked(True)
        self.context_stack.setCurrentWidget(mode.toolbar)
        if previous is not None:
            for v in self.views():
                if v.tool is not v._default_tool:
                    v.set_tool(None)
            self.act_select.setChecked(True)
        mode.entered.emit()
        self.ctx.modeChanged.emit(mode_id)

    # ================================================================== pannelli
    def add_sidebar_panel(self, panel_id: str, title: str, icon_name: str, widget: QWidget, order: int = 100) -> None:
        act = QAction(title, self)
        act.setCheckable(True)
        act.setToolTip(title)
        bind_icon(act, icon_name)
        act.triggered.connect(lambda _c=False, p=panel_id: self._toggle_sidebar_panel(p))
        self.sidebar_group.addAction(act)
        self._sidebar[panel_id] = (title, icon_name, widget, order, act)
        self.sidebar_stack.addWidget(widget)
        ordered = sorted(self._sidebar.items(), key=lambda kv: (kv[1][3], kv[1][0]))
        self.sidebar_strip.clear()
        for _pid, (_t, _i, _w, _o, a) in ordered:
            self.sidebar_strip.addAction(a)
        if hasattr(widget, "set_view"):
            widget.set_view(self.current_view())
        if self.current_view() is not None and len(self._sidebar) == 1:
            self.sidebar.show()
            act.setChecked(True)
            self.sidebar_stack.setCurrentWidget(widget)

    def _toggle_sidebar_panel(self, panel_id: str) -> None:
        _t, _i, widget, _o, act = self._sidebar[panel_id]
        if self.sidebar_stack.isVisible() and self.sidebar_stack.currentWidget() is widget and self._sidebar_open:
            self._sidebar_open = False
            self.sidebar_stack.hide()
            self.sidebar_group.setExclusive(False)
            act.setChecked(False)
            self.sidebar_group.setExclusive(True)
            return
        self.show_sidebar_panel(panel_id)

    _sidebar_open = True

    def show_sidebar_panel(self, panel_id: str) -> None:
        if panel_id not in self._sidebar:
            return
        _t, _i, widget, _o, act = self._sidebar[panel_id]
        self.sidebar.show()
        self.sidebar_stack.show()
        self._sidebar_open = True
        self.sidebar_stack.setCurrentWidget(widget)
        act.setChecked(True)

    def add_properties_panel(self, panel_id: str, widget: QWidget) -> None:
        self._props[panel_id] = widget
        self.properties.addWidget(widget)
        if hasattr(widget, "set_view"):
            widget.set_view(self.current_view())

    def show_properties(self, panel_id: str) -> None:
        widget = self._props.get(panel_id)
        if widget is None:
            return
        self.properties.setCurrentWidget(widget)
        self.properties.show()

    def hide_properties(self) -> None:
        self.properties.hide()

    # ================================================================== documenti
    def current_view(self) -> DocumentView | None:
        if self.docs_stack.currentWidget() is self.home:
            return None
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocumentView) else None

    def set_central_override(self, widget: QWidget | None) -> None:
        """Mostra `widget` al posto delle schede documento (None = ripristina).

        Il documento attivo resta quello della scheda corrente."""
        if widget is None:
            if self.docs_stack.currentWidget() not in (self.home, self.tabs):
                self.docs_stack.setCurrentWidget(self.tabs if self.tabs.count() else self.home)
            return
        if self.docs_stack.indexOf(widget) < 0:
            self.docs_stack.addWidget(widget)
        self.docs_stack.setCurrentWidget(widget)

    def central_override(self) -> QWidget | None:
        w = self.docs_stack.currentWidget()
        return None if w in (self.home, self.tabs) else w

    def views(self) -> list[DocumentView]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if isinstance(self.tabs.widget(i), DocumentView)]

    def _with_view(self, fn) -> None:
        view = self.current_view()
        if view is not None:
            fn(view)

    def show_home(self) -> None:
        self._refresh_recent()
        self.set_central_override(None)
        self.docs_stack.setCurrentWidget(self.home)
        self._on_current_changed()

    def new_document(self) -> DocumentView:
        return self.add_document(Document.new())

    def open_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Apri documenti"), OPEN_FILTER)
        for p in paths:
            self.open_path(p)

    def open_path(self, path: str, password: str | None = None) -> DocumentView | None:
        path = os.path.abspath(os.path.expanduser(str(path)))
        for view in self.views():
            if view.document.path is not None and os.path.realpath(view.document.path) == os.path.realpath(path):
                self._show_tabs()
                self.tabs.setCurrentWidget(view)
                return view
        attempts = 0
        while True:
            try:
                doc = Document.open(path, password)
                break
            except PasswordRequired:
                pass
            except WrongPassword:
                attempts += 1
                if attempts >= 5:
                    QMessageBox.warning(self, _("Password errata"), _("Troppi tentativi non riusciti."))
                    return None
                QMessageBox.warning(self, _("Password errata"), _("La password non è corretta. Riprova."))
            except DocumentError as e:
                QMessageBox.warning(self, _("Impossibile aprire il file"), f"{Path(path).name}\n\n{e}")
                self._forget_recent(path)
                return None
            password, ok = QInputDialog.getText(
                self, _("Documento protetto"),
                _("«{name}» è protetto. Inserisci la password per aprirlo:").format(name=Path(path).name),
                QLineEdit.EchoMode.Password)
            if not ok:
                return None
        if doc.path is not None:
            self._add_recent(str(doc.path))
        self.remember_directory(path)
        return self.add_document(doc)

    def add_document(self, document: Document) -> DocumentView:
        view = DocumentView(document, self.tabs)
        view.menu_providers = self.view_menu_providers
        mode = self.settings.value("view/mode", "continuous")
        if mode in (VIEW_SINGLE, VIEW_FACING, VIEW_COVER):
            view.set_view_mode(mode)
        zoom = self.settings.value("view/zoom", "width")
        if zoom == "page":
            view.set_fit(FIT_PAGE)
        elif zoom == "100":
            view.set_zoom(1.0)
        view.currentPageChanged.connect(self._update_page_box)
        view.zoomChanged.connect(self._update_zoom_box)
        view.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 6000))
        view.contentChanged.connect(lambda v=view: self.ctx.documentChanged.emit(v))
        view._bridge.changed.connect(lambda _k, v=view: self._update_tab_title(v))
        self.set_central_override(None)
        index = self.tabs.addTab(view, document.display_name)
        self.tabs.setTabToolTip(index, str(document.path or document.display_name))
        self.docs_stack.setCurrentWidget(self.tabs)
        self.tabs.setCurrentIndex(index)
        self.ctx.documentOpened.emit(view)
        self._on_current_changed()
        view.setFocus()
        return view

    def close_view(self, view: DocumentView | None) -> bool:
        if view is None:
            return True
        doc = view.document
        if doc.is_modified:
            self.tabs.setCurrentWidget(view)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(_("Modifiche non salvate"))
            box.setText(_("Salvare le modifiche a «{name}» prima di chiudere?").format(name=doc.display_name))
            box.setInformativeText(_("Se non salvi, le modifiche andranno perse."))
            save = box.addButton(_("Salva"), QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton(_("Non salvare"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(pgettext("pulsante", "Annulla"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(save)
            box.exec()
            clicked = box.clickedButton()
            if clicked is save:
                if not self.save_view(view):
                    return False
            elif clicked is not discard:
                return False
        self.ctx.documentClosed.emit(view)
        if doc.path is not None:
            self._closed_paths.append(str(doc.path))
        index = self.tabs.indexOf(view)
        view.dispose()
        doc.close()
        if index >= 0:
            self.tabs.removeTab(index)
        view.deleteLater()
        if self.tabs.count() == 0:
            self.show_home()
        self._on_current_changed()
        return True

    def save_view(self, view: DocumentView | None, save_as: bool = False) -> bool:
        if view is None:
            return False
        doc = view.document
        path = None
        if save_as or doc.needs_save_as:
            path = self.ctx.ask_save_path(_("Salva con nome"), doc.display_name, PDF_FILTER)
            if not path:
                return False
        try:
            doc.save(path)
        except DocumentError as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), str(e))
            return False
        except Exception as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), _("Errore imprevisto: {e}").format(e=e))
            return False
        if doc.path is not None:
            self._add_recent(str(doc.path))
        self.statusBar().showMessage(_("Salvato: {name}").format(name=doc.display_name), 4000)
        self._update_state()
        return True

    def save_copy(self) -> None:
        view = self.current_view()
        if view is None:
            return
        path = self.ctx.ask_save_path(_("Salva una copia"), view.document.display_name, PDF_FILTER)
        if not path:
            return
        try:
            view.document.save_copy(path)
            self.statusBar().showMessage(_("Copia salvata: {p}").format(p=path), 4000)
        except DocumentError as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), str(e))

    def undo(self) -> None:
        view = self.current_view()
        if view is not None and view.document.undo():
            self.statusBar().showMessage(_("Annullato"), 2000)

    def redo(self) -> None:
        view = self.current_view()
        if view is not None and view.document.redo():
            self.statusBar().showMessage(_("Ripetuto"), 2000)

    def _toggle_hand(self) -> None:
        view = self.current_view()
        if view is not None:
            view.set_tool(view.hand_tool if self.act_hand.isChecked() else None)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.act_fullscreen.setChecked(self.isFullScreen())

    # ================================================================== stato
    def _on_current_changed(self, *_args) -> None:
        view = self.current_view()
        if self._connected_view is not None and self._connected_view is not view:
            try:
                self._connected_view.toolChanged.disconnect(self._on_tool_changed)
            except (RuntimeError, TypeError):
                pass
        if view is not None and self._connected_view is not view:
            view.toolChanged.connect(self._on_tool_changed)
        self._connected_view = view
        if view is not None:
            self._on_tool_changed(view.tool)
        panels = [w for (_t, _i, w, _o, _a) in self._sidebar.values()] + list(self._props.values())
        for widget in panels:
            if hasattr(widget, "set_view"):
                try:
                    widget.set_view(view)
                except Exception as e:
                    self.statusBar().showMessage(_("Errore in un pannello: {e}").format(e=e), 8000)
        if view is not None and self._sidebar:
            self.sidebar.show()
        elif view is None:
            self.sidebar.hide()
            self.properties.hide()
        self.ctx.currentViewChanged.emit(view)
        self._update_state()

    def _on_tool_changed(self, tool) -> None:
        view = self.current_view()
        if view is None:
            return
        self.act_hand.setChecked(tool is view.hand_tool)
        self.act_select.setChecked(tool is view._default_tool)

    def _update_tab_title(self, view: DocumentView) -> None:
        index = self.tabs.indexOf(view)
        if index < 0:
            return
        doc = view.document
        if doc.closed:
            return
        self.tabs.setTabText(index, ("• " if doc.is_modified else "") + doc.display_name)
        self.tabs.setTabToolTip(index, str(doc.path or doc.display_name))
        if view is self.current_view():
            self._update_state()

    def _update_state(self) -> None:
        view = self.current_view()
        has = view is not None
        for act in self._document_actions:
            act.setEnabled(has)
        self.context_stack.setVisible(has)
        self.page_box.setVisible(has)
        self.zoom_combo.setVisible(has)
        if not has:
            self.setWindowTitle(APP_NAME)
            return
        doc = view.document
        self.setWindowTitle(f"{'• ' if doc.is_modified else ''}{doc.display_name} — {APP_NAME}")
        self.act_undo.setEnabled(doc.can_undo)
        self.act_redo.setEnabled(doc.can_redo)
        self.act_undo.setToolTip(_("Annulla: {x}").format(x=doc.undo_label) if doc.undo_label else _("Annulla"))
        self.act_redo.setToolTip(_("Ripeti: {x}").format(x=doc.redo_label) if doc.redo_label else _("Ripeti"))
        self.view_mode_actions[view.view_mode].setChecked(True)
        self._update_page_box()
        self._update_zoom_box()

    def _update_page_box(self, *_a) -> None:
        view = self.current_view()
        if view is None:
            return
        self.page_spin.set_page(view.current_page + 1, view.page_count)
        self.page_total.setText(f"/ {view.page_count}")

    def _update_zoom_box(self, *_a) -> None:
        view = self.current_view()
        if view is None:
            return
        if view.fit_mode == FIT_WIDTH:
            self.zoom_combo.setCurrentIndex(0)
        elif view.fit_mode == FIT_PAGE:
            self.zoom_combo.setCurrentIndex(1)
        else:
            self.zoom_combo.setCurrentIndex(-1)
            self.zoom_combo.setEditText(f"{round(view.zoom * 100)}%")

    def _on_page_spin(self, value: int) -> None:
        self._with_view(lambda v: v.go_to_page(value - 1))

    def _on_zoom_combo(self, index: int) -> None:
        data = self.zoom_combo.itemData(index)
        view = self.current_view()
        if view is None or data is None:
            return
        if isinstance(data, str):
            view.set_fit(data)
        else:
            view.set_zoom(float(data))
        view.setFocus()

    def _on_zoom_text(self) -> None:
        text = self.zoom_combo.currentText().replace("%", "").replace(",", ".").strip()
        view = self.current_view()
        try:
            value = float(text) / 100
        except ValueError:
            self._update_zoom_box()
            return
        if view is not None:
            view.set_zoom(value)
            view.setFocus()

    # ================================================================== recenti e cartelle
    def recent_files(self) -> list[str]:
        value = self.settings.value("recent", []) or []
        if isinstance(value, str):
            value = [value]
        return [p for p in value if isinstance(p, str)]

    def _add_recent(self, path: str) -> None:
        items = [p for p in self.recent_files() if p != path]
        items.insert(0, path)
        self.settings.setValue("recent", items[:MAX_RECENT])
        self._refresh_recent()

    def _forget_recent(self, path: str) -> None:
        self.settings.setValue("recent", [p for p in self.recent_files() if p != path])
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        self.home.set_recent([p for p in self.recent_files() if os.path.exists(p)])

    def last_directory(self) -> str:
        d = self.settings.value("lastDirectory", "")
        return d if d and os.path.isdir(d) else str(Path.home())

    def remember_directory(self, path: str) -> None:
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d:
            self.settings.setValue("lastDirectory", d)

    # ================================================================== impostazioni
    def set_theme(self, key: str) -> None:
        self.settings.setValue("theme", key)
        theme.apply(QApplication.instance(), key)
        for view in self.views():
            view.viewport().update()

    def set_language(self, key: str) -> None:
        self.settings.setValue("language", key)
        set_language(key)
        QMessageBox.information(self, APP_NAME, _("La lingua verrà applicata al prossimo avvio."))

    def about(self) -> None:
        QMessageBox.about(
            self, _("Informazioni su Foliant"),
            f"<h3>{APP_NAME} {__version__}</h3>"
            + _("<p>Editor PDF per Linux.</p><p>Icone: set open source con licenza ISC.</p>"))

    # ================================================================== eventi finestra
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        for view in list(self.views()):
            if not self.close_view(view):
                event.ignore()
                return
        self.settings.setValue("window/geometry", self.saveGeometry())
        event.accept()
