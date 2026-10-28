"""Schermata Home: apertura file, azioni rapide e documenti recenti."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import _
from . import theme
from .icons import RESOURCES, _pixmap, bind_icon, icon


class HomeScreen(QScrollArea):
    openRequested = Signal()
    newRequested = Signal()
    pathRequested = Signal(str)

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

        recent_label = QLabel(_("Documenti recenti"))
        recent_label.setProperty("heading", True)
        lay.addWidget(recent_label)
        self.recent = QListWidget(column)
        self.recent.setIconSize(QSize(20, 20))
        self.recent.setMinimumHeight(220)
        self.recent.itemActivated.connect(lambda item: self.pathRequested.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.recent.itemClicked.connect(lambda item: self.pathRequested.emit(item.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.recent, 1)
        self.empty_recent = QLabel(_("Nessun documento recente."))
        self.empty_recent.setProperty("muted", True)
        lay.addWidget(self.empty_recent)

    def set_recent(self, paths: list[str]) -> None:
        self.recent.clear()
        for p in paths:
            path = Path(p)
            try:
                mtime = dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m/%Y %H:%M")
            except OSError:
                mtime = ""
            item = QListWidgetItem(f"{path.name}\n{path.parent}   {mtime}")
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            item.setIcon(icon("file-text"))
            item.setSizeHint(QSize(0, 48))
            self.recent.addItem(item)
        self.recent.setVisible(bool(paths))
        self.empty_recent.setVisible(not paths)

    def action_callback(self, *titles: str) -> Callable | None:
        """Funzione dell'azione rapida con uno di questi titoli (None se non c'è)."""
        for _o, title, _i, callback, _d in self._actions:
            if title in titles:
                return callback
        return None

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
