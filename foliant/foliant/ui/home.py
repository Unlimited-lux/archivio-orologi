"""Schermata Home: apertura file e azioni rapide (nessun elenco dei documenti recenti)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import _
from .icons import RESOURCES, _pixmap, bind_icon


# Ruoli dell'elenco dei recenti delle versioni precedenti: restano solo per compatibilità.
PATH_ROLE = Qt.ItemDataRole.UserRole
MISSING_ROLE = Qt.ItemDataRole.UserRole + 1
PINNED_ROLE = Qt.ItemDataRole.UserRole + 2


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
        for col in range(4):                    # colonne uguali anche con l'ultima riga incompleta
            self.actions_grid.setColumnStretch(col, 1)
        lay.addLayout(self.actions_grid)
        self.actions_label.hide()

        lay.addStretch(1)
        # Riservatezza (ui/privacy.py): la Home non mostra i documenti recenti, perché Foliant non
        # conserva i file aperti. `recent` resta, vuoto e nascosto, per i moduli che lo cercano.
        self.recent = QListWidget(column)
        self.recent.setObjectName("homeRecentCompat")
        self.recent.hide()

    def set_recent(self, paths: list[str], pinned: list[str] | None = None) -> None:
        """Non fa nulla (compatibilità): la Home non mostra i recenti."""

    def visible_recent(self) -> list[str]:
        """Sempre vuoto (compatibilità)."""
        return []

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
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setProperty("homeAction", True)          # stile nel foglio globale (segue il tema)
            btn.clicked.connect(lambda _c=False, f=cb: f())
            self.actions_grid.addWidget(btn, n // 4, n % 4)
        self.actions_label.setVisible(bool(self._actions))
