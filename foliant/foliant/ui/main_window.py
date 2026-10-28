"""Finestra principale: barra modalità, schede documento, pannelli, barra di stato."""

from __future__ import annotations

import ctypes
import itertools
import logging
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QContextMenuEvent, QIntValidator, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QTabBar, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..core.document import ACCESS, CONTENT, LOCK, Document, write_error
from ..core.errors import DocumentError, DocumentWriteError, PasswordRequired, WrongPassword
from ..i18n import _, pgettext, set_language
from . import privacy, theme
from .banners import Banner, BannerBar, DocumentTabs
from .context import PDF_FILTER, Context, Mode, ToolBar
from .document_view import FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE, DocumentView
from .home import HomeScreen
from .icons import app_icon, bind_icon
from .notify import NotificationArea
from .tasks import BackgroundTask, TaskManager, TaskStatusWidget

READ_ONLY_BANNER = "nucleo.sola_lettura"     # avviso dei file senza permesso di scrittura
# Comandi con nome della riga di comando e delle azioni del file .desktop (NFR-32): se nessun modulo
# li registra con ctx.add_command, si usa l'azione rapida della Home con questo titolo.
HOME_COMMANDS = {"create-pdf": "Crea PDF da file", "merge": "Unisci PDF"}


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

# Finestre aperte dell'applicazione («Nuova finestra», istanza singola), dalla meno alla più
# recentemente attiva: l'elenco le tiene vive finché non vengono chiuse (WA_DeleteOnClose).
_windows: list["MainWindow"] = []

# Riservatezza (vedi ui/privacy.py): l'ultima cartella dei dialoghi e le guide dei documenti si
# ricordano solo in memoria, per la sessione e per tutte le finestre; mai nelle impostazioni.
_session_directory: str | None = None
_session_guides: dict[str, list] = {}


def app_windows() -> list["MainWindow"]:
    """Finestre di Foliant ancora aperte, l'ultima è quella attiva più di recente."""
    import shiboken6
    alive = [w for w in _windows if shiboken6.isValid(w)]
    _windows[:] = alive
    return [w for w in alive if not w._closed]


def active_window() -> "MainWindow | None":
    """La finestra a cui vanno i file aperti da fuori (gestore file, riga di comando)."""
    current = QApplication.activeWindow()
    # non quella che si sta chiudendo: un file aperto lì sparirebbe con lei
    windows = [w for w in app_windows() if not w._closing]
    if current in windows:
        return current
    return windows[-1] if windows else None


def register_window(window: "MainWindow") -> "MainWindow":
    """Aggiunge `window` alle finestre dell'applicazione; chiusa, viene distrutta e tolta."""
    if window not in _windows:
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        _windows.append(window)
    return window


def open_window(settings: QSettings, near: "MainWindow | None" = None) -> "MainWindow":
    """Nuova finestra con le stesse impostazioni, sulla Home (UX-18: Ctrl+N)."""
    window = register_window(MainWindow(settings))
    if near is not None and not near.isMaximized() and not near.isFullScreen():
        window.move(near.pos() + QPoint(32, 32))      # non esattamente sopra l'altra
    window.show()
    window.show_home()
    return window


_memory_timer: QTimer | None = None


def schedule_memory_release(*_args) -> None:
    """Dopo la chiusura di una o più schede: un solo `release_memory()` al prossimo giro del ciclo
    degli eventi. Il timer è dell'applicazione, non della finestra: chiusa una finestra con dei
    documenti, le sue schede vengono distrutte con lei e il rilascio deve avvenire lo stesso."""
    global _memory_timer
    import shiboken6
    if _memory_timer is None or not shiboken6.isValid(_memory_timer):
        _memory_timer = QTimer(QApplication.instance())
        _memory_timer.setSingleShot(True)
        _memory_timer.setInterval(0)
        _memory_timer.timeout.connect(lambda: release_memory())
    _memory_timer.start()


def release_memory() -> bool:
    """Restituisce al sistema la memoria già liberata (NFR-06). Con glibc i blocchi delle
    immagini di pagina di una scheda chiusa restano all'allocatore e la memoria residente
    resterebbe al picco: `malloc_trim(0)` li rende al sistema. Solo su Linux con glibc."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        if not os.confstr("CS_GNU_LIBC_VERSION").startswith("glibc"):
            return False
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:           # altra libc o libreria non disponibile: nulla da fare
        return False
    return True


class MainWindow(QMainWindow):
    _exit_confirmed = False     # uscita già confermata: close_view non chiede più nulla
    _closed = False             # chiusura confermata (closeEvent accettato)
    _closing = False            # closeEvent in corso (domande aperte): non riceve file da fuori

    def __init__(self, settings: QSettings | None = None, *, load_modules: bool = True):
        super().__init__()
        self.settings = settings or QSettings()
        # dati con nomi o percorsi di file lasciati dalle versioni precedenti; dialoghi di file
        # che non lasciano cronologia (idempotenti: main() li ha già chiamati)
        privacy.cleanup_legacy(self.settings)
        privacy.install(QApplication.instance())
        self.ctx = Context(self)
        self.modes: dict[str, Mode] = {}
        self.current_mode_id: str | None = None
        self._document_actions: list[QAction] = []
        self._actions_have_document = False
        self._menu_sections: "OrderedDict[str, list[QAction]]" = OrderedDict()
        self._sidebar: dict[str, tuple[str, str, QWidget, int, QAction]] = {}
        self._props: dict[str, QWidget] = {}
        self._connected_view: DocumentView | None = None
        self.view_menu_providers: list = []
        self.tab_menu_providers: list = []           # ctx.add_tab_menu_provider: fn(view, menu)
        self.recent_menu_providers: list = []        # compatibilità: i recenti non esistono più
        self.selected_pages_provider: Callable | None = None   # ctx.set_selected_pages_provider
        self._preference_pages: list[tuple[int, int, str, Callable]] = []   # (ordine, n, titolo, factory)
        self._preference_seq = itertools.count()
        # Scorciatoie valide solo in una modalità: (azione, scorciatoie, id modalità)
        self._mode_shortcuts: list[tuple[QAction, list, str]] = []
        self.module_results: list[tuple[str, Exception | None]] = []
        self._commands: dict[str, Callable] = {}          # ctx.add_command
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1320, 860)
        self._build_ui()
        self._build_core_actions()
        self._build_read_mode()
        self._build_services()
        if load_modules:
            from ..modules import load_modules as _load
            self.module_results = _load(self.ctx)
            failed = [name for name, err in self.module_results if err is not None]
            if failed:
                QTimer.singleShot(0, lambda: self.statusBar().showMessage(
                    _("Alcuni moduli non sono stati caricati: {m}").format(m=", ".join(failed)), 10000))
        self.set_mode("leggi")
        self._update_state()
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    # ================================================================== UI base
    def _build_ui(self) -> None:
        # Barra in alto
        self.header = ToolBar(_("Barra principale"), self)
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
        self.sidebar_strip = ToolBar(self.sidebar)
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
        # ogni scheda: barre di avviso (UX-14) sopra la vista; widget(i)/indexOf lavorano con le viste
        self.tabs = DocumentTabs(self.docs_stack)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(lambda i: self.close_view(self.tabs.widget(i)))
        self.tabs.currentChanged.connect(self._on_current_changed)
        self.tabs.tabBar().menuRequested.connect(self._on_tab_menu)
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
        # stesse parole delle azioni del menu Vista; larghezza presa dalla voce più lunga
        self.zoom_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.zoom_combo.addItem(_("Adatta alla larghezza"), FIT_WIDTH)
        self.zoom_combo.addItem(_("Adatta alla pagina"), FIT_PAGE)
        for z in (10, 25, 50, 75, 100, 125, 150, 200, 300, 400, 800, 1600, 3200, 6400):
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
        # UX-18: Ctrl+N è «Nuova finestra»; il PDF vuoto resta senza scorciatoia (Home e menu ☰)
        self.act_new_window = a(_("Nuova finestra"), "app-window", self.new_window, "Ctrl+N")
        self.act_new = a(_("Nuovo documento"), "file-plus", self.new_document)
        self.act_open = a(_("Apri…"), "folder-open", self.open_dialog, QKeySequence.StandardKey.Open)
        self.act_save = a(_("Salva"), "save", lambda: self.save_view(self.current_view()),
                          QKeySequence.StandardKey.Save, requires_document=True)
        self.act_save_as = a(_("Salva con nome…"), "save-all",
                             lambda: self.save_view(self.current_view(), save_as=True),
                             "Ctrl+Shift+S", requires_document=True)
        self.act_save_copy = a(_("Salva una copia…"), "copy", lambda: self.save_copy(), requires_document=True)
        self.act_close = a(_("Chiudi documento"), "x", lambda: self.close_view(self.current_view()),
                           QKeySequence.StandardKey.Close, requires_document=True)
        self.act_quit = a(_("Esci"), "log-out", self.quit_application, "Ctrl+Q")
        # UX-18: tasti espliciti, non quelli della piattaforma (fuori da GNOME/KDE Close è solo
        # Ctrl+F4 e Quit non ha tasti)
        self.act_close.setShortcuts([QKeySequence("Ctrl+W"), QKeySequence("Ctrl+F4")])
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
        # Righelli (VIS-39): niente scorciatoia, Ctrl+R è già di «Ruota vista».
        self.act_rulers = a(_("Righelli"), "ruler", self.set_rulers_visible, checkable=True)
        self.act_rulers.setChecked(self.rulers_visible())
        self.add_menu_action(self.act_rulers, _("Vista"))
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

        self.act_preferences = a(_("Preferenze…"), "settings", lambda: self.show_preferences(), "Ctrl+,")
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

    def _build_services(self) -> None:
        """Servizi della finestra per i moduli: attività non modali e avvisi chiusi nella barra
        di stato, avvisi temporanei in basso (ctx.notify)."""
        sb = self.statusBar()
        self.banner_button = QToolButton(self)
        self.banner_button.setAutoRaise(True)
        bind_icon(self.banner_button, "bell")
        self.banner_button.setToolTip(_("Avvisi chiusi del documento"))
        self.banner_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.banner_menu = QMenu(self.banner_button)
        self.banner_menu.aboutToShow.connect(self.fill_banner_menu)
        self.banner_button.setMenu(self.banner_menu)
        self.banner_button.hide()
        sb.insertPermanentWidget(0, self.banner_button)
        self.task_manager = TaskManager(self, report=lambda text: self.ctx.notify(text),
                                        status=lambda text: self.statusBar().showMessage(text, 5000))
        self.task_status = TaskStatusWidget(self.task_manager, self)
        sb.insertPermanentWidget(0, self.task_status)
        self.notifications = NotificationArea(self.centralWidget())

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
        for seq in ("Shift+F10", "Menu"):      # su Linux Qt non li trasforma da sé in menu contestuale
            self._shortcut(seq, self.open_context_menu)
        self._shortcut("F6", lambda: self._cycle_focus(1))
        self._shortcut("Shift+F6", lambda: self._cycle_focus(-1))

    def open_context_menu(self) -> bool:
        """Maiusc+F10 / tasto Menu (UX-18, UX-07): menu contestuale del widget con il fuoco, come il
        clic destro; per l'area documento al centro della selezione o della vista."""
        widget = QApplication.focusWidget()
        if widget is None or widget.window() is not self:
            widget = self.current_view()
        if widget is None:
            return False
        target, pos = widget, widget.rect().center()
        if isinstance(widget, DocumentView):
            target, pos = widget.viewport(), widget.keyboard_menu_pos()
        elif isinstance(widget, QAbstractScrollArea):
            target = widget.viewport()
            pos = target.rect().center()
            if isinstance(widget, QAbstractItemView) and widget.currentIndex().isValid():
                rect = widget.visualRect(widget.currentIndex())
                if target.rect().contains(rect.center()):
                    pos = rect.center()
        elif isinstance(widget, QTabBar) and widget.currentIndex() >= 0:
            pos = widget.tabRect(widget.currentIndex()).center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, pos, target.mapToGlobal(pos),
                                  QApplication.keyboardModifiers())
        QApplication.sendEvent(target, event)
        return True

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

    def _apply_initial_splitter_sizes(self) -> None:
        """Larghezze iniziali: pannello laterale e proprietà compatti, documento al centro."""
        if getattr(self, "_splitter_ready", False):
            return
        state = self.settings.value("window/splitter")
        if state is not None and self.splitter.restoreState(state):
            self._splitter_ready = True
            return
        total = max(800, self.splitter.width())
        side = 280 if self.sidebar.isVisible() else 0
        props = 300 if self.properties.isVisible() else 0
        self.splitter.setSizes([side, max(400, total - side - props), props])
        self._splitter_ready = True

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

    def add_preferences_page(self, title: str, factory: Callable, order: int = 100) -> None:
        """Pagina di un modulo nelle Preferenze; lo stesso titolo sostituisce la pagina precedente."""
        self._preference_pages = [p for p in self._preference_pages if p[2] != title]
        self._preference_pages.append((order, next(self._preference_seq), title, factory))
        self._preference_pages.sort(key=lambda p: (p[0], p[1]))

    def preference_pages(self) -> list[tuple[str, Callable]]:
        return [(title, factory) for _o, _n, title, factory in self._preference_pages]

    def show_preferences(self, page: str | None = None) -> None:
        from .dialogs import PreferencesDialog
        dlg = PreferencesDialog(self.settings, self, pages=self.preference_pages())
        if page:
            dlg.show_page(page)
        if dlg.exec():
            old_lang = self.settings.value("language", "")
            dlg.apply()
            self.set_theme(self.settings.value("theme", "system"))
            self.set_ruler_unit(self.ruler_unit())
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

    def scope_action_to_mode(self, act: QAction, mode_id: str) -> None:
        """La scorciatoia di `act` resta attiva solo nella modalità `mode_id`."""
        for i, (a, seqs, m) in enumerate(self._mode_shortcuts):
            if a is act:
                self._mode_shortcuts[i] = (a, seqs, mode_id)
                break
        else:
            self._mode_shortcuts.append((act, list(act.shortcuts()), mode_id))
        self._apply_mode_shortcuts()

    def _apply_mode_shortcuts(self) -> None:
        alive = []
        for act, seqs, mode_id in self._mode_shortcuts:
            try:
                act.setShortcuts(seqs if mode_id == self.current_mode_id else [])
            except RuntimeError:      # azione già distrutta
                continue
            alive.append((act, seqs, mode_id))
        self._mode_shortcuts = alive

    def register_document_action(self, act: QAction) -> None:
        self._document_actions.append(act)
        act.setEnabled(self.current_view() is not None)

    def add_menu_action(self, action: QAction, section: str) -> None:
        self._menu_sections.setdefault(section, []).append(action)

    def _rebuild_menu(self) -> None:
        m = self.main_menu
        m.clear()
        for act in (self.act_new_window, self.act_new, self.act_open):
            m.addAction(act)
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
        toolbar = ToolBar(title, self)
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
        button.setToolButtonStyle(self._mode_button_style())
        button.setToolTip(title)
        button.setIconSize(QSize(18, 18))
        bind_icon(button, icon_name, kind="mode")
        button.clicked.connect(lambda _c=False, m=mode_id: self.set_mode(m))
        mode = Mode(mode_id, title, icon_name, order, toolbar, button, window=self)
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
        self._apply_mode_shortcuts()
        mode.button.setChecked(True)
        self.context_stack.setCurrentWidget(mode.toolbar)
        if previous is not None:
            for v in self.views():
                if v.tool is not v._default_tool:
                    v.set_tool(None)
            self.act_select.setChecked(True)
            self.properties.hide()      # il pannello della modalità precedente non resta aperto
        self.apply_default_tool(mode)   # prima di `entered`: i moduli vedono già lo strumento scelto
        mode.entered.emit()
        self.ctx.modeChanged.emit(mode_id)

    def apply_default_tool(self, mode: Mode | None = None, views: list[DocumentView] | None = None) -> None:
        """Attiva lo strumento predefinito della modalità attiva (`mode.set_default_tool`) sulle
        viste che hanno «Seleziona testo»: uno strumento scelto dall'utente resta."""
        mode = mode if mode is not None else self.modes.get(self.current_mode_id or "")
        if mode is None or mode.default_tool is None or mode.id != self.current_mode_id:
            return
        for view in (views if views is not None else self.views()):
            if view.document.closed or view.tool is not view._default_tool:
                continue
            try:
                tool = mode.make_default_tool(view)
            except Exception as e:      # un modulo difettoso non blocca il cambio di modalità
                self.statusBar().showMessage(_("Errore nello strumento della modalità: {e}").format(e=e), 8000)
                return
            if tool is not None:
                view.set_tool(tool)

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

    def toggle_sidebar_panel(self, panel_id: str) -> None:
        """Mostra il pannello `panel_id` o lo nasconde se è già quello visibile (es. F4 miniature)."""
        if panel_id in self._sidebar:
            if not self.sidebar.isVisible():
                self.show_sidebar_panel(panel_id)
            else:
                self._toggle_sidebar_panel(panel_id)

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
        if not self.properties.isVisible():
            self.properties.show()
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] < 240:
                take = 300 - sizes[2]
                self.splitter.setSizes([sizes[0], max(400, sizes[1] - take), 300])

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
        self.set_central_override(None)
        self.docs_stack.setCurrentWidget(self.home)
        self._on_current_changed()

    def activate_view(self, view: DocumentView | None) -> bool:
        """Porta in primo piano la scheda di `view` (anche dalla Home o da un widget centrale)."""
        if view is None or self.tabs.indexOf(view) < 0:
            return False
        self._show_tabs()
        if self.tabs.currentWidget() is not view:
            self.tabs.setCurrentWidget(view)        # currentChanged aggiorna lo stato
        view.setFocus()
        return True

    # ================================================================== avvisi del documento
    def _banner_bar(self, view: DocumentView | None) -> BannerBar | None:
        page = self.tabs.page(view)
        if page is None or view.document.closed:      # scheda chiusa (anche in attesa di eliminazione)
            return None
        return page.banners

    def show_banner(self, view: DocumentView | None, banner_id: str, text: str, actions=(), level: str = "info",
                    priority: int = 0, closable: bool = True, on_close: Callable | None = None) -> Banner | None:
        bar = self._banner_bar(view)
        if bar is None:
            return None
        return bar.show_banner(banner_id, text, actions, level, priority, closable, on_close)

    def hide_banner(self, view: DocumentView | None, banner_id: str) -> bool:
        bar = self._banner_bar(view)
        return bar.hide_banner(banner_id) if bar is not None else False

    def banner_ids(self, view: DocumentView | None) -> list[str]:
        bar = self._banner_bar(view)
        return [b.id for b in bar.active()] if bar is not None else []

    def _update_banner_button(self) -> None:
        """Gli avvisi chiusi restano come icona nella barra di stato (UX-14)."""
        bar = self._banner_bar(self.current_view())
        closed = bar.dismissed() if bar is not None else []
        self.banner_button.setVisible(bool(closed))
        if closed:
            self.banner_button.setToolTip(_("Avvisi chiusi del documento") + "\n"
                                          + "\n".join("• " + b.text for b in closed))

    def fill_banner_menu(self) -> None:
        """Menu dell'icona: un clic mostra di nuovo l'avviso chiuso."""
        self.banner_menu.clear()
        bar = self._banner_bar(self.current_view())
        for b in (bar.dismissed() if bar is not None else []):
            text = b.text if len(b.text) <= 90 else b.text[:89] + "…"
            act = self.banner_menu.addAction(text)
            act.triggered.connect(lambda _c=False, bar=bar, i=b.id: bar.restore(i))

    def new_document(self) -> DocumentView:
        return self.add_document(Document.new())

    def new_window(self) -> "MainWindow":
        """«Nuova finestra» (Ctrl+N): un'altra finestra di Foliant sulla Home."""
        return open_window(self.settings, near=self)

    def quit_application(self) -> None:
        """«Esci»: chiude tutte le finestre; ognuna chiede delle proprie modifiche e un Annulla ferma l'uscita."""
        for window in [w for w in app_windows() if w is not self] + [self]:
            if not window.close():
                return

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
                return None
            except Exception as e:          # errore imprevisto del motore: un file non ferma gli altri
                # nel registro né il percorso né il messaggio (può contenere il nome del file):
                # lo stderr può finire nel giornale di sistema, che resta su disco
                logging.getLogger(__name__).error("apertura non riuscita (%s)", type(e).__name__)
                QMessageBox.warning(self, _("Impossibile aprire il file"),
                                    f"{Path(path).name}\n\n" + _("Il file non è un PDF valido oppure è danneggiato.")
                                    + f"\n({type(e).__name__})")
                return None
            password, ok = QInputDialog.getText(
                self, _("Documento protetto"),
                _("«{name}» è protetto. Inserisci la password per aprirlo:").format(name=Path(path).name),
                QLineEdit.EchoMode.Password)
            if not ok:
                return None
        self.remember_directory(path)
        return self.add_document(doc)

    def add_command(self, name: str, fn: Callable) -> None:
        self._commands[name] = fn

    def run_command(self, name: str) -> bool:
        """Comando con nome (`foliant --create`, `--merge`, azioni del file .desktop): quello
        registrato da un modulo, altrimenti l'azione rapida della Home; se manca, mostra la Home."""
        fn = self._commands.get(name)
        if fn is None and name in HOME_COMMANDS:
            title = HOME_COMMANDS[name]
            fn = self.home.action_callback(title, _(title))
        if fn is None:
            self.show_home()
            self.statusBar().showMessage(_("Comando non disponibile: {name}").format(name=name), 8000)
            return False
        try:
            fn()
        except Exception as e:          # un modulo difettoso non chiude l'applicazione
            # senza il messaggio dell'errore, che può contenere nomi di file (vedi open_path)
            logging.getLogger(__name__).error("comando %s non riuscito (%s)", name, type(e).__name__)
            self.statusBar().showMessage(_("Errore imprevisto: {e}").format(e=e), 8000)
        return True

    def open_files(self, files) -> list[DocumentView]:
        """File della riga di comando o di un altro avvio: `(percorso, pagina da 1 o None)`;
        un file già aperto ne attiva la scheda (NFR-31)."""
        views = []
        for path, page in files:
            view = self.open_path(path)
            if view is None:
                continue
            if page and view.page_count:
                view.go_to_page(min(max(int(page), 1), view.page_count) - 1)
            views.append(view)
        return views

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
        view.set_ruler_unit(self.ruler_unit())
        view.set_rulers_visible(self.rulers_visible())
        self._load_guides(view)
        view.guidesChanged.connect(lambda v=view: self._store_guides(v))
        view.rulerUnitChanged.connect(self.set_ruler_unit)
        view.currentPageChanged.connect(self._update_page_box)
        view.zoomChanged.connect(self._update_zoom_box)
        view.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 6000))
        view.contentChanged.connect(lambda v=view: self.ctx.documentChanged.emit(v))
        view._bridge.changed.connect(lambda _k, v=view: self._update_tab_title(v))
        view._bridge.changed.connect(lambda k, v=view: self._on_document_event(v, k))
        self.set_central_override(None)
        index = self.tabs.addTab(view, document.display_name)
        self.tabs.setTabToolTip(index, str(document.path or document.display_name))
        page = self.tabs.page(view)
        if page is not None:
            page.banners.changed.connect(self._update_banner_button)
        self.docs_stack.setCurrentWidget(self.tabs)
        self.tabs.setCurrentIndex(index)
        self.ctx.documentOpened.emit(view)
        self.apply_default_tool(views=[view])      # documento aperto in una modalità con strumento proprio
        self._on_current_changed()
        if document.read_only:
            self.ctx.show_banner(view, READ_ONLY_BANNER,
                                 _("«{name}» è in sola lettura: puoi modificarlo e salvarlo con «Salva con nome».")
                                 .format(name=document.display_name),
                                 [(_("Salva con nome…"), lambda v=view: self.save_view(v, save_as=True))],
                                 level="info", priority=5)
        if document.locked_reason:
            self.statusBar().showMessage(_("Sola lettura: {reason}").format(reason=document.locked_reason), 15000)
        view.setFocus()
        return view

    def commit_pending_edits(self, view: DocumentView | None) -> None:
        """Conferma l'editor aperto sulla pagina (testo, campo modulo) prima di salvare o chiudere.

        Gli editor in loco dei moduli confermano quando perdono il fuoco: togliendolo qui la
        modifica in corso entra nel documento invece di andare persa.
        """
        if view is None:
            return
        for hook in list(self.ctx.pending_edit_hooks):
            try:
                hook(view)
            except Exception as e:          # un modulo difettoso non deve impedire il salvataggio
                self.statusBar().showMessage(_("Errore in un pannello: {e}").format(e=e), 8000)
        fw = QApplication.focusWidget()
        if fw is not None and fw is not view and view.isAncestorOf(fw):
            fw.clearFocus()
            view.setFocus()
            QApplication.processEvents()

    def _ask_unsaved(self, docs: list) -> str | None:
        """Un solo dialogo per uno o più documenti modificati: "save", "discard" o None (annulla)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Modifiche non salvate"))
        if len(docs) == 1:
            box.setText(_("Salvare le modifiche a «{name}» prima di chiudere?").format(name=docs[0].display_name))
            save = box.addButton(_("Salva"), QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton(_("Non salvare"), QMessageBox.ButtonRole.DestructiveRole)
        else:
            box.setText(_("Questi documenti hanno modifiche non salvate:") + "\n\n"
                        + "\n".join("• " + d.display_name for d in docs))
            save = box.addButton(_("Salva tutti"), QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton(_("Chiudi senza salvare"), QMessageBox.ButtonRole.DestructiveRole)
        box.setInformativeText(_("Se non salvi, le modifiche andranno perse."))
        box.addButton(pgettext("pulsante", "Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is discard else None

    def close_view(self, view: DocumentView | None, ask: bool = True) -> bool:
        """Chiude la scheda. Con `ask` (chiusura dell'utente) interroga i controlli
        `close_document` dei moduli e chiede se salvare le modifiche; senza, chiude subito."""
        if view is None:
            return True
        self.commit_pending_edits(view)
        doc = view.document
        if ask and not self.ctx.run_guards("close_document", view, {"modified": doc.is_modified}):
            return False
        if doc.is_modified and ask and not self._exit_confirmed:
            self.tabs.setCurrentWidget(view)
            choice = self._ask_unsaved([doc])
            if choice == "save":
                if not self.save_view(view):
                    return False
            elif choice != "discard":
                return False
        self._close_view_now(view)
        return True

    def _close_view_now(self, view: DocumentView) -> None:
        """Chiude la scheda senza domande (le decisioni sono già state prese)."""
        doc = view.document
        self.ctx.documentClosed.emit(view)
        if doc.path is not None:
            self._closed_paths.append(str(doc.path))        # «Riapri scheda chiusa»: solo in memoria
        self._store_guides(view)
        index = self.tabs.indexOf(view)
        page = self.tabs.page(view)
        view.dispose()
        doc.close()
        if index >= 0:
            self.tabs.removeTab(index)
        view.deleteLater()
        if page is not None:
            page.release()
            page.deleteLater()                  # contenitore con le barre di avviso
        # distrutte davvero vista e contenitore, la memoria liberata torna al sistema (NFR-06)
        # (funzione del modulo: chiudendo la finestra le schede muoiono con lei, ma il rilascio resta)
        (page if page is not None else view).destroyed.connect(schedule_memory_release)
        if self.tabs.count() == 0:
            self.show_home()
        self._on_current_changed()

    def save_view(self, view: DocumentView | None, save_as: bool = False, recreate: bool = False) -> bool:
        """Salva il documento (con `save_as` chiede dove). Se il file è stato eliminato o spostato
        dopo l'apertura chiede se ricrearlo lì o salvarlo altrove (NFR-12/4); `recreate=True`
        lo ricrea senza chiedere (l'utente l'ha già scelto, es. dalla barra di avviso)."""
        if view is None:
            return False
        self.commit_pending_edits(view)
        doc = view.document
        path = None
        missing = doc.path is not None and not os.path.exists(doc.path)
        if missing and not (save_as or recreate):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(_("File eliminato o spostato"))
            box.setText(_("«{name}» è stato eliminato o spostato dopo l'apertura.").format(name=doc.display_name))
            box.setInformativeText(_("Puoi ricrearlo nella posizione originale o salvarlo in un'altra posizione."))
            here = box.addButton(_("Ricrea nella posizione originale"), QMessageBox.ButtonRole.AcceptRole)
            other = box.addButton(_("Salva con nome…"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(pgettext("pulsante", "Annulla"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(other)
            box.exec()
            clicked = box.clickedButton()
            if clicked is other:
                save_as = True
            elif clicked is here:
                recreate = True
            else:
                return False
        recreate = missing and recreate and not save_as
        if not (save_as or doc.needs_save_as) and doc.disk_changed():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(_("File modificato da un altro programma"))
            box.setText(_("«{name}» è stato modificato da un altro programma dopo l'apertura. "
                          "Salvando lo sostituisci e quelle modifiche vanno perse.").format(name=doc.display_name))
            overwrite = box.addButton(_("Sovrascrivi"), QMessageBox.ButtonRole.DestructiveRole)
            other = box.addButton(_("Salva con nome…"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(pgettext("pulsante", "Annulla"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(other)
            box.exec()
            clicked = box.clickedButton()
            if clicked is other:
                save_as = True
            elif clicked is not overwrite:
                return False
        as_new = save_as or (doc.needs_save_as and not recreate)
        info = {"path": None if as_new or doc.path is None else str(doc.path), "copy": False}
        if not self.ctx.run_guards("save_as" if as_new else "save", view, info):
            return False
        if as_new:
            path = self.ctx.ask_save_path(_("Salva con nome"), doc.display_name, PDF_FILTER)
            if not path:
                return False
        try:
            if recreate:
                try:
                    doc.path.parent.mkdir(parents=True, exist_ok=True)   # anche la cartella era sparita
                except OSError as e:
                    raise write_error(doc.path, e) from e
            doc.save(path)
        except DocumentWriteError as e:         # disco pieno, cartella non scrivibile…: il documento resta modificato
            return self._write_failed(e, _("Salva con nome…"), lambda: self.save_view(view, save_as=True))
        except DocumentError as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), str(e))
            return False
        except Exception as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), _("Errore imprevisto: {e}").format(e=e))
            return False
        self.statusBar().showMessage(_("Salvato: {name}").format(name=doc.display_name), 4000)
        self._update_state()
        return True

    def save_copy(self, view: DocumentView | None = None) -> bool:
        view = view if view is not None else self.current_view()
        if view is None:
            return False
        self.commit_pending_edits(view)
        if not self.ctx.run_guards("save_as", view, {"path": None, "copy": True}):
            return False
        path = self.ctx.ask_save_path(_("Salva una copia"), view.document.display_name, PDF_FILTER)
        if not path:
            return False
        try:
            view.document.save_copy(path)
            self.statusBar().showMessage(_("Copia salvata: {p}").format(p=path), 4000)
        except DocumentWriteError as e:
            return self._write_failed(e, _("Scegli un'altra posizione…"), lambda: self.save_copy(view))
        except DocumentError as e:
            QMessageBox.warning(self, _("Salvataggio non riuscito"), str(e))
            return False
        return True

    def _write_failed(self, error: DocumentWriteError, other_label: str, other: Callable[[], bool]) -> bool:
        """Scrittura non riuscita (NFR-08, NFR-15): il messaggio dice causa e azione e propone
        un'altra posizione. Restituisce l'esito di `other` se l'utente la sceglie."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Salvataggio non riuscito"))
        box.setText(str(error))
        retry = box.addButton(other_label, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Chiudi"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        if box.clickedButton() is retry:
            return other()
        return False

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
            if not self.sidebar.isVisible():
                self.sidebar.show()
                self._apply_initial_splitter_sizes()
        elif view is None:
            self.sidebar.hide()
            self.properties.hide()
        self._update_state()                     # prima del segnale: i moduli hanno l'ultima parola
        self._update_banner_button()
        self.ctx.currentViewChanged.emit(view)

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
        read_only = doc.read_only
        self.tabs.setTabText(index, ("• " if doc.is_modified else "") + doc.display_name
                             + (" " + _("(sola lettura)") if read_only or doc.locked_reason else ""))
        if not read_only:                   # dopo «Salva con nome» in una posizione scrivibile
            self.ctx.hide_banner(view, READ_ONLY_BANNER)
        self.tabs.setTabToolTip(index, str(doc.path or doc.display_name))
        if view is self.current_view():
            self._update_state()

    def _on_document_event(self, view: DocumentView, kind: str) -> None:
        """Avvisi nella barra di stato per sola lettura logica e accesso completo; ai moduli
        `ctx.documentAccessChanged` per ricalcolare i comandi che dipendono dai permessi."""
        doc = view.document
        if doc.closed:
            return
        if kind in (ACCESS, LOCK):
            self.ctx.documentAccessChanged.emit(view)
            if view is self.current_view():
                self._update_state()
        if view is not self.current_view():
            return
        if kind == LOCK and doc.locked_reason:
            self.statusBar().showMessage(_("Sola lettura: {reason}").format(reason=doc.locked_reason), 8000)
        elif kind == LOCK:
            self.statusBar().showMessage(_("Il documento è di nuovo modificabile."), 5000)
        elif kind == ACCESS and doc.access == "owner":
            self.statusBar().showMessage(_("Accesso completo: tutte le azioni sono consentite."), 6000)

    def refresh_state(self) -> None:
        """Ricalcola titolo, azioni e barra di stato (API pubblica: `ctx.refresh_state()`)."""
        self._update_state()

    def _update_state(self) -> None:
        view = self.current_view()
        has = view is not None
        if has != self._actions_have_document:   # solo al passaggio nessun documento ↔ documento:
            self._actions_have_document = has    # gli stati impostati dai moduli (permessi) restano
            for act in self._document_actions:
                act.setEnabled(has)
        self.context_stack.setVisible(has)
        self.page_box.setVisible(has)
        self.zoom_combo.setVisible(has)
        if not has:
            self.setWindowTitle(APP_NAME)
            return
        doc = view.document
        ro = " " + _("(sola lettura)") if doc.read_only or doc.locked_reason else ""
        self.setWindowTitle(f"{'• ' if doc.is_modified else ''}{doc.display_name}{ro} — {APP_NAME}")
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
        # VIS-12: niente «precedente» sulla prima pagina né «successiva» sull'ultima
        at_first = view.current_page <= 0
        at_last = view.current_page >= view.page_count - 1
        for act, enabled in ((self.act_first, not at_first), (self.act_prev, not at_first),
                             (self.act_next, not at_last), (self.act_last, not at_last)):
            act.setEnabled(enabled)

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

    # ================================================================== righelli e guide (VIS-39)
    def rulers_visible(self) -> bool:
        return str(self.settings.value("view/rulers", "false")).lower() == "true"

    def set_rulers_visible(self, visible: bool) -> None:
        """Righelli su tutte le viste, anche quelle aperte dopo (preferenza salvata)."""
        visible = bool(visible)
        self.settings.setValue("view/rulers", "true" if visible else "false")
        if self.act_rulers.isChecked() != visible:
            self.act_rulers.setChecked(visible)
        for view in self.views():
            view.set_rulers_visible(visible)

    def ruler_unit(self) -> str:
        from .rulers import RULER_UNITS
        unit = str(self.settings.value("view/ruler_unit", "mm") or "mm")
        return unit if unit in RULER_UNITS else "mm"

    def set_ruler_unit(self, unit: str) -> None:
        from .rulers import RULER_UNITS
        if unit not in RULER_UNITS:
            raise ValueError(f"unità dei righelli non valida: {unit!r}")
        self.settings.setValue("view/ruler_unit", unit)
        for view in self.views():
            view.set_ruler_unit(unit)

    @staticmethod
    def _guides_key(view: DocumentView) -> str | None:
        """Chiave delle guide del documento nella memoria della sessione (mai su disco)."""
        path = view.document.path
        return os.path.realpath(str(path)) if path else None

    def _load_guides(self, view: DocumentView) -> None:
        """Guide del documento se è già stato aperto in questa sessione (solo in memoria)."""
        key = self._guides_key(view)
        if key is not None and _session_guides.get(key):
            view.set_guides(_session_guides[key])

    def _store_guides(self, view: DocumentView) -> None:
        key = self._guides_key(view)
        if key is None:
            return
        try:
            guides = view.guides()
        except RuntimeError:                     # vista già distrutta
            return
        if guides:
            _session_guides[key] = list(guides)
        else:
            _session_guides.pop(key, None)

    # ================================================================== recenti (inerti) e cartelle
    # Foliant non conserva l'elenco dei file aperti (ui/privacy.py): i metodi restano per i moduli
    # che li chiamano ancora, ma non salvano nulla e restituiscono elenchi vuoti.
    def recent_files(self) -> list[str]:
        """Sempre vuoto: Foliant non conserva i file aperti."""
        return []

    def pinned_recent(self) -> list[str]:
        """Sempre vuoto: non esistono documenti fissati."""
        return []

    def is_pinned(self, path: str) -> bool:
        return False

    def pin_recent(self, path: str, pinned: bool = True) -> None:
        """Non fa nulla (compatibilità)."""

    def clear_recent(self, ask: bool = True) -> bool:
        """Non c'è nulla da svuotare: restituisce True senza domande (compatibilità)."""
        return True

    def remove_recent(self, path: str) -> None:
        """Non fa nulla (compatibilità)."""

    def replace_recent(self, old_path: str, new_path: str) -> None:
        """Non fa nulla (compatibilità)."""

    def open_recent(self, path: str) -> DocumentView | None:
        """Compatibilità: apre `path` se esiste."""
        return self.open_path(path) if os.path.exists(path) else None

    def recent_menu(self, path: str) -> QMenu:
        """Compatibilità: un menu vuoto (la Home non mostra più i recenti)."""
        menu = QMenu(self)
        menu.setObjectName("coreRecentMenu")
        return menu

    def extend_recent_menu(self, path: str, menu: QMenu) -> None:
        """Non aggiunge nulla (compatibilità)."""

    def last_directory(self) -> str:
        """Cartella iniziale dei dialoghi: l'ultima usata in questa sessione (solo in memoria,
        condivisa dalle finestre), altrimenti la cartella personale."""
        d = _session_directory
        return d if d and os.path.isdir(d) else str(Path.home())

    def remember_directory(self, path: str) -> None:
        global _session_directory
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d:
            _session_directory = d

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

    # ================================================================== menu della scheda
    def tab_menu(self, index: int) -> QMenu | None:
        """Menu contestuale della scheda `index`: voci del nucleo e dei moduli (add_tab_menu_provider)."""
        view = self.tabs.widget(index) if 0 <= index < self.tabs.count() else None
        if not isinstance(view, DocumentView):
            return None
        menu = QMenu(self)
        menu.setObjectName("coreTabMenu")
        close = menu.addAction(_("Chiudi scheda"))
        bind_icon(close, "x")
        close.triggered.connect(lambda _c=False, v=view: self.close_view(v))
        if self.tab_menu_providers:
            menu.addSeparator()
        for provider in list(self.tab_menu_providers):
            try:
                provider(view, menu)
            except Exception as e:
                self.statusBar().showMessage(_("Errore nel menu della scheda: {e}").format(e=e), 8000)
        return menu

    def _on_tab_menu(self, index: int, global_pos) -> None:
        menu = self.tab_menu(index)
        if menu is not None:
            self.exec_tab_menu(menu, global_pos)
            menu.deleteLater()

    def exec_tab_menu(self, menu: QMenu, global_pos) -> None:
        """Mostra il menu della scheda (separato per poterlo simulare nei test)."""
        menu.exec(global_pos)

    # ================================================================== eventi finestra
    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self in _windows:
            _windows.remove(self)
            _windows.append(self)           # in fondo: la finestra attiva più di recente
        if event.type() == QEvent.Type.WindowStateChange:
            full = self.isFullScreen()
            if full != self._was_fullscreen:
                self._was_fullscreen = full
                act = getattr(self, "act_fullscreen", None)
                if act is not None:
                    act.setChecked(full)
                self.ctx.fullscreenChanged.emit(full)

    _was_fullscreen = False

    def background_tasks(self) -> list[BackgroundTask]:
        return self.task_manager.running()

    def _confirm_stop_tasks(self, tasks: list[BackgroundTask]) -> bool:
        """UX-12: con attività in corso l'uscita chiede se interromperle o attendere.

        Solo la domanda: le attività si interrompono in `closeEvent` quando l'uscita è
        confermata anche dai controlli `quit` e dalla domanda sulle modifiche non salvate."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Attività in corso"))
        box.setText(_("Ci sono attività in corso:") + "\n\n" + "\n".join("• " + t.title for t in tasks))
        box.setInformativeText(_("Puoi interromperle e uscire, oppure attendere che finiscano."))
        stop = box.addButton(_("Interrompi ed esci"), QMessageBox.ButtonRole.DestructiveRole)
        wait = box.addButton(_("Attendi"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(wait)
        box.exec()
        return box.clickedButton() is stop

    STOP_TASKS_TIMEOUT_MS = 5000      # attesa massima all'uscita per le attività interrotte

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
        event.acceptProposedAction()

    def _mode_button_style(self) -> Qt.ToolButtonStyle:
        # sotto ~1500 px le modalità con il testo non entrano: solo icone (il nome resta nel suggerimento)
        return (Qt.ToolButtonStyle.ToolButtonTextBesideIcon if self.width() >= 1500
                else Qt.ToolButtonStyle.ToolButtonIconOnly)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        style = self._mode_button_style()
        for m in getattr(self, "modes", {}).values():
            if m.button.toolButtonStyle() != style:
                m.button.setToolButtonStyle(style)

    def closeEvent(self, event) -> None:
        # le domande qui sotto fanno girare il ciclo degli eventi: intanto la finestra è «in chiusura»
        self._closing = True
        try:
            self._close_window(event)
        finally:
            self._closing = False

    def _close_window(self, event) -> None:
        running = self.task_manager.running()
        if running and not self._confirm_stop_tasks(running):
            event.ignore()
            return
        views = list(self.views())
        for view in views:
            self.commit_pending_edits(view)
        if not self.ctx.run_guards("quit", None, {"views": views}):
            event.ignore()
            return
        modified = [v for v in views if v.document.is_modified]
        if modified:
            # una sola domanda; nessuna scheda si chiude finché l'uscita non è confermata
            choice = self._ask_unsaved([v.document for v in modified])
            if choice is None:
                event.ignore()
                return
            if choice == "save":
                for view in modified:
                    if not self.save_view(view):
                        event.ignore()
                        return
        if self.task_manager.running():
            # uscita confermata: solo ora si interrompono le attività, e si aspetta che smettano
            # di usare i documenti prima di chiuderli (con un limite, per non bloccare l'uscita)
            self.task_manager.cancel_all()
            self.task_manager.wait_all(self.STOP_TASKS_TIMEOUT_MS)
        for view in views:
            self._close_view_now(view)
        self.settings.setValue("window/geometry", self.saveGeometry())
        if getattr(self, "_splitter_ready", False):
            self.settings.setValue("window/splitter", self.splitter.saveState())
        self._closed = True
        event.accept()
