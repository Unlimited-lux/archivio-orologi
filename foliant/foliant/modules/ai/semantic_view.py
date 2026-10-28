"""Finestra della ricerca per significato (AI-21)."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme


class SemanticSearchDialog(QDialog):
    """`run(query) -> (results, note)`; `on_open(result)`, `on_ask(result)`, `on_highlight(result)`."""

    def __init__(self, run, on_open, on_ask=None, on_highlight=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Ricerca per significato"))
        self.resize(560, 480)
        self._run, self._open, self._ask, self._hl = run, on_open, on_ask, on_highlight
        self.results = []
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText(_("Cerca un concetto, anche con parole diverse…"))
        self.query.returnPressed.connect(self.search)
        go = QPushButton(_("Cerca"))
        bind_icon(go, "search")
        go.clicked.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(self.note)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemActivated.connect(lambda _i: self.open_selected())
        self.list.itemClicked.connect(lambda _i: self.open_selected())
        lay.addWidget(self.list, 1)
        acts = QHBoxLayout()
        for text, icon_name, fn in ((_("Copia"), "copy", self.copy_selected),
                                    (_("Chiedi all'IA"), "message-circle", self.ask_selected),
                                    (_("Evidenzia"), "highlighter", self.highlight_selected)):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch(1)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        acts.addWidget(close)
        lay.addLayout(acts)

    def search(self, query: str | None = None) -> None:
        if query is not None:
            self.query.setText(query)
        q = self.query.text().strip()
        if not q:
            return
        out = self._run(q)
        if out is None:
            return
        self.results, note = out
        self.note.setText(note)
        self.list.clear()
        for r in self.results:
            excerpt = r.excerpt if len(r.excerpt) < 240 else r.excerpt[:237] + "…"
            item = QListWidgetItem(_("Pagina {n}").format(n=r.page + 1) + f" · {excerpt}")
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(item)

    def _current(self):
        item = self.list.currentItem() or (self.list.item(0) if self.list.count() else None)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def open_selected(self) -> None:
        r = self._current()
        if r is not None:
            self._open(r)

    def copy_selected(self) -> None:
        r = self._current()
        if r is not None:
            QGuiApplication.clipboard().setText(r.excerpt)

    def ask_selected(self) -> None:
        r = self._current()
        if r is not None and self._ask:
            self._ask(r)
            self.accept()

    def highlight_selected(self) -> None:
        r = self._current()
        if r is not None and self._hl:
            self._hl(r)


def result_quads(result) -> list[pymupdf.Quad]:
    return [pymupdf.Rect(r).quad for r in result.rects]
