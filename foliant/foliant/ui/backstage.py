"""Scheda «File»: pagina a tutto schermo con i comandi sul file, come la scheda File di Excel.

A sinistra una colonna con le pagine (Nuovo, Apri, Informazioni, Condividi, Esporta, Gestisci file,
Opzioni, Guida) e i comandi immediati (Salva, Salva con nome, Salva una copia, Stampa, Chiudi, Esci);
a destra la pagina scelta, con le voci divise in gruppi. Le voci dei moduli arrivano con
`ctx.add_file_action(azione, pagina, gruppo, descrizione)`; le pagine si ricostruiscono a ogni
apertura, così testo, stato e spunta seguono le azioni.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy,
    QStyle, QStyleOption, QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import _
from .icons import bind_icon, icon

if TYPE_CHECKING:
    from .main_window import MainWindow

# Pagine della colonna di sinistra, nell'ordine. "cmd" = comando immediato (l'azione è collegata
# dalla finestra o da un modulo, es. Stampa); "page" = pagina con le voci.
PAGES: tuple[tuple[str, str, str, str], ...] = (
    ("nuovo", "Nuovo", "file-plus", "page"),
    ("apri", "Apri", "folder-open", "page"),
    ("informazioni", "Informazioni", "info", "page"),
    ("salva", "Salva", "save", "cmd"),
    ("salva_con_nome", "Salva con nome", "save-all", "cmd"),
    ("salva_copia", "Salva una copia", "copy", "cmd"),
    ("stampa", "Stampa", "printer", "cmd"),
    ("condividi", "Condividi", "share-2", "page"),
    ("esporta", "Esporta", "file-output", "page"),
    ("gestisci", "Gestisci file", "folder-cog", "page"),
    ("chiudi", "Chiudi", "x", "cmd"),
    ("altro", "Altri comandi", "ellipsis", "page"),     # voci di add_menu_action (superata)
    ("opzioni", "Opzioni", "settings", "page"),
    ("guida", "Guida", "circle-help", "page"),
    ("esci", "Esci", "log-out", "cmd"),
)
BOTTOM = ("opzioni", "guida", "esci")          # in fondo alla colonna, come Opzioni in Excel
PAGE_IDS = tuple(p[0] for p in PAGES)


def page_title(page_id: str) -> str:
    for pid, title, _icon, _kind in PAGES:
        if pid == page_id:
            return _(title)
    return page_id


@dataclass
class Entry:
    action: QAction
    group: str
    description: str
    order: int
    seq: int


@dataclass
class Page:
    id: str
    kind: str
    entries: list[Entry] = field(default_factory=list)
    command: QAction | None = None


class Tile(QAbstractButton):
    """Voce di una pagina: icona, nome, descrizione e scorciatoia; segue lo stato dell'azione."""

    def __init__(self, action: QAction, description: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.action = action
        self.setObjectName("backstageTile")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(12)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("backstageTileIcon")
        self.icon_label.setFixedSize(36, 36)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("backstageTileTitle")
        self.title_label.setWordWrap(True)
        texts.addWidget(self.title_label)
        self.desc_label = QLabel(self)
        self.desc_label.setProperty("muted", True)
        self.desc_label.setWordWrap(True)
        texts.addWidget(self.desc_label)
        self.key_label = QLabel(self)
        self.key_label.setProperty("muted", True)
        self.key_label.setObjectName("backstageTileKey")
        texts.addWidget(self.key_label)
        lay.addLayout(texts, 1)
        self.state_label = QLabel(self)         # spunta delle voci attivabili, freccia dei sottomenu
        self.state_label.setFixedSize(20, 20)
        lay.addWidget(self.state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        for child in (self.icon_label, self.title_label, self.desc_label, self.key_label, self.state_label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._description = description
        self.sync()

    def sync(self) -> None:
        act = self.action
        text = act.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        self.title_label.setText(text)
        self.desc_label.setText(self._description)
        self.desc_label.setVisible(bool(self._description))
        from .dialogs import key_text
        keys = key_text(act.shortcut()) if not act.shortcut().isEmpty() else ""
        self.key_label.setText(keys)
        self.key_label.setVisible(bool(keys))
        ic = act.icon()
        mode = QIcon.Mode.Normal if act.isEnabled() else QIcon.Mode.Disabled
        if ic.isNull():                          # azione senza icona: un segno generico, per allineare
            ic = icon("square-check" if act.isCheckable() else "chevron-right")
        self.icon_label.setPixmap(ic.pixmap(QSize(22, 22), mode))
        self.setEnabled(act.isEnabled())
        self.setCheckable(act.isCheckable())
        if act.isCheckable():
            self.setChecked(act.isChecked())
            # interruttore: acceso (colore dell'accento) o spento
            from . import theme
            on = act.isChecked()
            switch = icon("toggle-right" if on else "toggle-left",
                          color=theme.hex_color("accent" if on else "text_muted"))
            self.state_label.setPixmap(switch.pixmap(QSize(20, 20)))

        elif act.menu() is not None:
            self.state_label.setPixmap(icon("chevron-down").pixmap(QSize(16, 16)))
        else:
            self.state_label.clear()
        tip = act.toolTip()
        self.setToolTip(tip if tip and tip.replace("&", "") != text else "")
        self.setAccessibleName(text)
        state = (_("Attiva") if act.isChecked() else _("Disattivata")) if act.isCheckable() else ""
        self.setAccessibleDescription(" ".join(t for t in (state, self._description, keys) if t))

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint().expandedTo(QSize(220, 56))

    def minimumSizeHint(self) -> QSize:
        return self.layout().minimumSize()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(56, self.layout().heightForWidth(width))

    def paintEvent(self, _event) -> None:
        opt = QStyleOption()
        opt.initFrom(self)
        if self.isChecked():
            opt.state |= QStyle.StateFlag.State_On
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)


class Backstage(QWidget):
    """Pagina della scheda File. `closeRequested` chiede alla finestra di tornare al documento."""

    aboutToShow = Signal()
    closeRequested = Signal()
    pageChanged = Signal(str)

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.setObjectName("backstage")
        self._window = window
        self._seq = itertools.count()
        self.pages: dict[str, Page] = {pid: Page(pid, kind) for pid, _t, _i, kind in PAGES}
        self.headers: dict[str, Callable[[QVBoxLayout], None]] = {}   # contenuto fisso in cima a una pagina
        self.notes: dict[str, str] = {}
        self.current = "informazioni"
        self.tiles: list[Tile] = []

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.nav = QFrame(self)
        self.nav.setObjectName("backstageNav")
        self.nav.setFixedWidth(220)
        nav_lay = QVBoxLayout(self.nav)
        nav_lay.setContentsMargins(0, 8, 0, 8)
        nav_lay.setSpacing(0)
        self.back_button = self._nav_button(_("Indietro"), "arrow-left")
        self.back_button.setToolTip(_("Torna al documento (Esc)"))
        self.back_button.clicked.connect(self.closeRequested.emit)
        nav_lay.addWidget(self.back_button)
        nav_lay.addSpacing(6)
        self.nav_buttons: dict[str, QToolButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for pid, title, icon_name, kind in PAGES:
            if pid == BOTTOM[0]:
                nav_lay.addStretch(1)
                line = QFrame(self.nav)
                line.setObjectName("backstageNavLine")
                line.setFixedHeight(1)
                nav_lay.addWidget(line)
                nav_lay.addSpacing(6)
            btn = self._nav_button(_(title), icon_name)
            btn.setProperty("pageId", pid)
            if kind == "page":
                btn.setCheckable(True)
                self._group.addButton(btn)
                btn.clicked.connect(lambda _c=False, p=pid: self.show_page(p))
            else:
                btn.clicked.connect(lambda _c=False, p=pid: self._run_command(p))
            nav_lay.addWidget(btn)
            self.nav_buttons[pid] = btn
        outer.addWidget(self.nav)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("backstagePage")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)
        self.body: QWidget | None = None

    # ------------------------------------------------------------------ API
    def add_action(self, action: QAction, page: str, group: str | None = None, description: str = "",
                   order: int = 100) -> None:
        if page not in self.pages:
            page = "altro"
        target = self.pages[page]
        if target.kind == "cmd":
            target.command = action
            return
        if any(e.action is action for e in target.entries):
            return
        target.entries.append(Entry(action, group or "", description, order, next(self._seq)))

    def set_command(self, page: str, action: QAction) -> None:
        self.pages[page].command = action

    def set_header(self, page: str, builder: Callable[[QVBoxLayout], None]) -> None:
        """Contenuto in cima alla pagina (es. il riepilogo del documento in Informazioni)."""
        self.headers[page] = builder

    def set_note(self, page: str, text: str) -> None:
        self.notes[page] = text

    def entries(self, page: str) -> list[Entry]:
        return sorted(self.pages[page].entries, key=lambda e: (e.order, e.seq))

    def actions_of(self, page: str) -> list[QAction]:
        p = self.pages[page]
        return [p.command] if p.kind == "cmd" and p.command is not None else [e.action for e in self.entries(page)]

    def all_actions(self) -> list[QAction]:
        return [a for pid in PAGE_IDS for a in self.actions_of(pid)]

    def page_of(self, action: QAction) -> str | None:
        for pid in PAGE_IDS:
            if action in self.actions_of(pid):
                return pid
        return None

    # ------------------------------------------------------------------ apertura
    def prepare(self, page: str | None = None) -> None:
        """Aggiorna le azioni (aboutToShow), la colonna e la pagina; chiamata dalla finestra prima di mostrarla."""
        self.aboutToShow.emit()
        self._sync_nav()
        if page is not None and page in self.pages and self.pages[page].kind == "page":
            self.current = page
        if not self.nav_buttons[self.current].isVisible() and not self.nav_buttons[self.current].isVisibleTo(self):
            self.current = "informazioni"
        self.show_page(self.current, focus=False)

    def _sync_nav(self) -> None:
        for pid, page in self.pages.items():
            btn = self.nav_buttons[pid]
            if page.kind == "cmd":
                act = page.command
                btn.setVisible(act is not None and act.isVisible())
                btn.setEnabled(act is not None and act.isEnabled())
                if act is not None:
                    from .dialogs import key_text
                    keys = key_text(act.shortcut()) if not act.shortcut().isEmpty() else ""
                    btn.setToolTip(f"{btn.text()} ({keys})" if keys else btn.text())
            elif pid == "altro":
                btn.setVisible(bool(page.entries))

    def show_page(self, page: str, focus: bool = True) -> None:
        if page not in self.pages or self.pages[page].kind != "page":
            return
        self.current = page
        self.nav_buttons[page].setChecked(True)
        self._build_page(page)
        self.pageChanged.emit(page)
        if focus and self.tiles:
            self.tiles[0].setFocus()

    def focus_nav(self) -> None:
        self.nav_buttons[self.current].setFocus()

    def _run_command(self, page: str) -> None:
        act = self.pages[page].command
        if act is None or not act.isEnabled():
            return
        self.closeRequested.emit()          # prima di eseguire: i dialoghi compaiono sul documento
        act.trigger()

    def _build_page(self, page_id: str) -> None:
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.tiles = []
        body = QWidget()
        body.setObjectName("backstageBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.setSpacing(18)
        title = QLabel(page_title(page_id), body)
        title.setObjectName("backstageTitle")
        lay.addWidget(title)
        header = self.headers.get(page_id)
        if header is not None:
            header(lay)
        groups: dict[str, list[Entry]] = {}
        for e in self.entries(page_id):
            if e.action.isVisible():
                groups.setdefault(e.group, []).append(e)
        for name, entries in groups.items():
            box = QVBoxLayout()
            box.setSpacing(8)
            if name:
                label = QLabel(name.upper(), body)
                label.setObjectName("backstageGroup")
                box.addWidget(label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            for i, e in enumerate(entries):
                tile = Tile(e.action, e.description, body)
                tile.clicked.connect(lambda _c=False, t=tile: self._activate(t))
                grid.addWidget(tile, i // 2, i % 2)
                self.tiles.append(tile)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            box.addLayout(grid)
            lay.addLayout(box)
        note = self.notes.get(page_id)
        if note:
            label = QLabel(note, body)
            label.setObjectName("backstageNote")
            label.setWordWrap(True)
            lay.addWidget(label)
        if not groups and header is None and not note:
            empty = QLabel(_("Nessun comando in questa pagina."), body)
            empty.setProperty("muted", True)
            lay.addWidget(empty)
        lay.addStretch(1)
        body.setMaximumWidth(1100)
        self.body = body
        self.scroll.setWidget(body)

    def _activate(self, tile: Tile) -> None:
        act = tile.action
        if not act.isEnabled():
            return
        menu = act.menu()
        if menu is not None:
            # copia del menu: la voce scelta parte dopo la chiusura della scheda File (salvo le opzioni
            # attivabili, che cambiano solo la spunta), così i dialoghi compaiono sul documento
            links: dict[QAction, QAction] = {}
            proxy = self._proxy_menu(menu, links, tile)
            chosen = self._exec_menu(proxy, tile.mapToGlobal(QPoint(0, tile.height())))
            source = links.get(chosen)
            proxy.deleteLater()
            if source is not None and source.isEnabled():
                if not source.isCheckable():
                    self.closeRequested.emit()
                source.trigger()
            if self.isVisible():
                tile.sync()
            return
        if act.isCheckable():
            act.trigger()
            tile.sync()
            return
        self.closeRequested.emit()
        act.trigger()

    def _exec_menu(self, menu: QMenu, pos: QPoint) -> QAction | None:
        return menu.exec(pos)

    def _proxy_menu(self, menu: QMenu, links: dict, parent: QWidget) -> QMenu:
        """Copia di `menu` (sottomenu compresi) con azioni che non eseguono nulla: `links` collega
        ogni copia all'azione originale."""
        menu.aboutToShow.emit()                 # i menu che si riempiono all'apertura (es. le policy)
        proxy = QMenu(menu.title(), parent)
        for src in menu.actions():
            if src.isSeparator():
                proxy.addSeparator()
            elif src.menu() is not None:
                sub = self._proxy_menu(src.menu(), links, proxy)
                sub.setTitle(src.text())
                sub.setIcon(src.icon())
                sub.setEnabled(src.isEnabled())
                proxy.addMenu(sub)
            elif src.isVisible():
                copy = proxy.addAction(src.icon(), src.text())
                copy.setEnabled(src.isEnabled())
                copy.setCheckable(src.isCheckable())
                copy.setChecked(src.isChecked())
                copy.setShortcut(src.shortcut())
                copy.setToolTip(src.toolTip())
                links[copy] = src
        return proxy

    def refresh(self) -> None:
        for tile in self.tiles:
            try:
                tile.sync()
            except RuntimeError:          # azione distrutta
                pass

    # ------------------------------------------------------------------ tastiera
    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)

    def _nav_button(self, text: str, icon_name: str) -> QToolButton:
        btn = QToolButton(self.nav)
        btn.setObjectName("backstageNavButton")
        btn.setText("  " + text)               # distacco tra icona e testo (lo stile non lo regola)
        btn.setAccessibleName(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setIconSize(QSize(18, 18))
        btn.setAutoRaise(True)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind_icon(btn, icon_name, kind="onaccent")
        btn.installEventFilter(self)
        return btn

    def eventFilter(self, obj, event) -> bool:
        # frecce su e giù nella colonna, come in un elenco
        if event.type() == QEvent.Type.KeyPress and isinstance(obj, QToolButton) and obj.parent() is self.nav:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                buttons = [self.back_button] + [b for b in self.nav_buttons.values() if b.isVisible() and b.isEnabled()]
                if obj in buttons:
                    i = buttons.index(obj) + (1 if key == Qt.Key.Key_Down else -1)
                    buttons[i % len(buttons)].setFocus()
                    return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                obj.click()
                return True
            if key == Qt.Key.Key_Right and obj.isCheckable() and self.tiles:
                self.tiles[0].setFocus()
                return True
        return super().eventFilter(obj, event)
