"""Schermata Home: apertura file, azioni rapide e documenti recenti."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import _
from . import theme
from .icons import RESOURCES, _pixmap, bind_icon, icon


PATH_ROLE = Qt.ItemDataRole.UserRole
MISSING_ROLE = Qt.ItemDataRole.UserRole + 1      # il file non esiste più
PINNED_ROLE = Qt.ItemDataRole.UserRole + 2       # documento fissato in cima


def order_recent(paths: list[str], pinned: list[str]) -> list[str]:
    """Ordine dei recenti: prima i fissati (nell'ordine in cui sono stati fissati), poi gli altri."""
    top = [p for p in pinned if p in paths]
    return top + [p for p in paths if p not in top]


class RecentList(QListWidget):
    """Elenco dei recenti. Il clic destro su una voce emette `menuRequested(percorso, posizione)`.

    Se un modulo mostra da sé il menu (CustomContextMenu) creando un QMenu figlio dell'elenco,
    `menu_extender(percorso, menu)` lo completa con le voci del nucleo prima che compaia."""

    menuRequested = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.menu_extender: Callable[[str, QMenu], None] | None = None
        self._menu_path: str | None = None

    def _item_for(self, ev: QContextMenuEvent, from_viewport: bool) -> QListWidgetItem | None:
        if ev.reason() == QContextMenuEvent.Reason.Keyboard or not from_viewport:
            return self.currentItem()
        return self.itemAt(ev.pos())

    def _with_menu_path(self, ev, from_viewport: bool, handler) -> bool:
        item = self._item_for(ev, from_viewport)
        self._menu_path = item.data(PATH_ROLE) if item is not None else None
        try:
            return handler(ev)
        finally:
            self._menu_path = None

    def viewportEvent(self, ev) -> bool:
        if ev.type() == QEvent.Type.ContextMenu:
            return self._with_menu_path(ev, True, super().viewportEvent)
        return super().viewportEvent(ev)

    def event(self, ev) -> bool:
        if ev.type() == QEvent.Type.ContextMenu and self._menu_path is None:     # da tastiera
            return self._with_menu_path(ev, False, super().event)
        return super().event(ev)

    def contextMenuEvent(self, ev: QContextMenuEvent) -> None:
        path = self._menu_path
        if path:
            pos = ev.globalPos()
            if ev.reason() == QContextMenuEvent.Reason.Keyboard and self.currentItem() is not None:
                pos = self.viewport().mapToGlobal(self.visualItemRect(self.currentItem()).center())
            self.menuRequested.emit(path, pos)
        ev.accept()

    def childEvent(self, ev) -> None:
        super().childEvent(ev)
        if ev.type() != QEvent.Type.ChildPolished or not self._menu_path or self.menu_extender is None:
            return
        menu = ev.child()
        if isinstance(menu, QMenu) and not menu.property("foliantRecentMenu"):
            menu.setProperty("foliantRecentMenu", True)
            self.menu_extender(self._menu_path, menu)


class HomeScreen(QScrollArea):
    openRequested = Signal()
    newRequested = Signal()
    pathRequested = Signal(str)
    missingRequested = Signal(str)          # clic su un recente il cui file non esiste più
    menuRequested = Signal(str, QPoint)     # clic destro su un recente (posizione globale)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._actions: list[tuple[int, str, str, Callable, str]] = []
        body = QWidget(self)
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        column = QWidget(body)
        column.setMaximumWidth(900)
        column.setMinimumWidth(520)
        outer.addWidget(column, 4)
        outer.addStretch(1)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(24, 40, 24, 24)
        lay.setSpacing(18)

        head = QHBoxLayout()
        logo = QLabel(column)
        logo.setPixmap(_pixmap((RESOURCES / "foliant.svg").read_bytes(), 56))
        head.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel("Foliant")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel(_("Leggi, modifica, converti e firma i tuoi PDF."))
        subtitle.setProperty("muted", True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        lay.addLayout(head)

        buttons = QHBoxLayout()
        self.open_btn = QPushButton(_("Apri file…"))
        self.open_btn.setProperty("primary", True)
        bind_icon(self.open_btn, "folder-open", kind="mode")
        self.open_btn.setIconSize(QSize(18, 18))
        self.open_btn.setMinimumHeight(40)
        self.open_btn.clicked.connect(self.openRequested.emit)
        self.new_btn = QPushButton(_("Nuovo PDF vuoto"))
        bind_icon(self.new_btn, "file-plus")
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setMinimumHeight(40)
        self.new_btn.clicked.connect(self.newRequested.emit)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.new_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        self.drop_hint = QLabel(_("Puoi anche trascinare qui i file PDF o le immagini da aprire."))
        self.drop_hint.setProperty("muted", True)
        lay.addWidget(self.drop_hint)

        self.actions_label = QLabel(_("Azioni rapide"))
        self.actions_label.setProperty("heading", True)
        lay.addWidget(self.actions_label)
        self.actions_grid = QGridLayout()
        self.actions_grid.setSpacing(10)
        lay.addLayout(self.actions_grid)
        self.actions_label.hide()

        recent_head = QHBoxLayout()
        recent_label = QLabel(_("Documenti recenti"))
        recent_label.setProperty("heading", True)
        recent_head.addWidget(recent_label)
        recent_head.addStretch(1)
        self.filter_edit = QLineEdit(column)
        self.filter_edit.setPlaceholderText(_("Filtra per nome"))
        self.filter_edit.setAccessibleName(_("Filtra i documenti recenti per nome"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMaximumWidth(280)
        bind_icon(self.filter_edit.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition), "search")
        self.filter_edit.textChanged.connect(self._apply_filter)
        recent_head.addWidget(self.filter_edit)
        lay.addLayout(recent_head)
        self.recent = RecentList(column)
        self.recent.setIconSize(QSize(20, 20))
        self.recent.setMinimumHeight(220)
        self.recent.itemActivated.connect(self._activate)
        self.recent.itemClicked.connect(self._activate)
        self.recent.menuRequested.connect(self.menuRequested)
        lay.addWidget(self.recent, 1)
        self.no_match = QLabel(_("Nessun documento corrisponde al filtro."))
        self.no_match.setProperty("muted", True)
        self.no_match.hide()
        lay.addWidget(self.no_match)
        self.empty_recent = QLabel(_("Nessun documento recente."))
        self.empty_recent.setProperty("muted", True)
        lay.addWidget(self.empty_recent)
        self._paths: list[str] = []
        self._pinned: list[str] = []

    def set_recent(self, paths: list[str], pinned: list[str] | None = None) -> None:
        """Mostra i recenti: i fissati in cima, i file che non esistono più attenuati con
        «File non trovato»; il filtro per nome resta applicato."""
        self._paths = list(paths)
        self._pinned = list(pinned or [])
        self._render()

    def _render(self) -> None:
        self.recent.clear()
        for p in order_recent(self._paths, self._pinned):
            path = Path(p)
            exists = os.path.exists(p)
            pinned = p in self._pinned
            if exists:
                try:
                    when = dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m/%Y %H:%M")
                except OSError:
                    when = ""
            else:
                when = _("File non trovato")
            item = QListWidgetItem(f"{path.name}\n{path.parent}   {when}")
            item.setData(PATH_ROLE, p)
            item.setData(MISSING_ROLE, not exists)
            item.setData(PINNED_ROLE, pinned)
            states = ([_("Fissato")] if pinned else []) + ([] if exists else [_("File non trovato")])
            item.setToolTip("\n".join([p] + states))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, ", ".join([path.name] + states))
            if exists:
                item.setIcon(icon("pin" if pinned else "file-text"))
            else:
                item.setIcon(icon("file-x", theme.hex_color("text_muted")))
                item.setForeground(theme.color("text_muted"))
            item.setSizeHint(QSize(0, 48))
            self.recent.addItem(item)
        has = bool(self._paths)
        self.recent.setVisible(has)
        self.filter_edit.setVisible(has)
        self.empty_recent.setVisible(not has)
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        """Nasconde i recenti il cui nome non contiene il testo del filtro (a ogni carattere)."""
        text = self.filter_edit.text().strip().casefold()
        shown = 0
        for row in range(self.recent.count()):
            item = self.recent.item(row)
            hidden = bool(text) and text not in Path(item.data(PATH_ROLE)).name.casefold()
            item.setHidden(hidden)
            shown += not hidden
        self.no_match.setVisible(bool(self._paths) and shown == 0)

    def visible_recent(self) -> list[str]:
        """Percorsi mostrati, nell'ordine dell'elenco (fissati in cima, filtro applicato)."""
        return [self.recent.item(r).data(PATH_ROLE) for r in range(self.recent.count())
                if not self.recent.item(r).isHidden()]

    def _activate(self, item: QListWidgetItem) -> None:
        path = item.data(PATH_ROLE)
        if not path:
            return
        if os.path.exists(path):
            self.pathRequested.emit(path)
        else:
            self.missingRequested.emit(path)       # Individua o Rimuovi dall'elenco, non «Apri»

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.PaletteChange and self._paths:
            self._render()                          # colori dei file mancanti e icone col tema

    def add_action(self, title: str, icon_name: str, callback: Callable, description: str = "",
                   order: int = 100) -> None:
        self._actions.append((order, title, icon_name, callback, description))
        self._actions.sort(key=lambda a: (a[0], a[1]))
        while self.actions_grid.count():
            item = self.actions_grid.takeAt(0)
            if item.widget():
                item.widget().hide()            # altrimenti resta visibile finché non viene distrutto
                item.widget().deleteLater()
        for n, (_o, t, ic, cb, desc) in enumerate(self._actions):
            btn = QToolButton(self)
            btn.setText(t)
            btn.setToolTip(desc or t)
            bind_icon(btn, ic)
            btn.setIconSize(QSize(26, 26))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setMinimumSize(QSize(150, 86))
            btn.setProperty("homeAction", True)          # stile nel foglio globale (segue il tema)
            btn.clicked.connect(lambda _c=False, f=cb: f())
            self.actions_grid.addWidget(btn, n // 4, n % 4)
        self.actions_label.setVisible(bool(self._actions))
