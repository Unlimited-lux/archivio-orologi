"""Libreria delle attività (AI-20): elenco per categoria, ricerca, parametri, gestione."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from foliant.api import FoliantError, _, atomic_write, theme

from .tasks_library import CATEGORIES, TaskStore, TaskTemplate


class TasksDialog(QDialog):
    """Sceglie un'attività e i suoi parametri. Dopo `exec()`: `chosen`, `values()`, `scope()`."""

    def __init__(self, store: TaskStore, parent=None, *, has_document: bool = True, has_selection: bool = False,
                 ask_save_path=None, ask_open_path=None, on_error=None):
        super().__init__(parent)
        self.store = store
        self.chosen: TaskTemplate | None = None
        self.ask_save_path = ask_save_path
        self.ask_open_path = ask_open_path
        self.on_error = on_error or (lambda m: None)
        self.setWindowTitle(_("Attività"))
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItem(_("Tutte le categorie"), "")
        for c in CATEGORIES:
            self.category.addItem(_(c), c)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Cerca un'attività…"))
        row.addWidget(self.category)
        row.addWidget(self.search, 1)
        lay.addLayout(row)
        body = QHBoxLayout()
        self.list = QListWidget()
        body.addWidget(self.list, 2)
        right = QVBoxLayout()
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.description.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        right.addWidget(self.description)
        self.form_box = QWidget()
        self.form = QFormLayout(self.form_box)
        right.addWidget(self.form_box)
        self.scope_combo = QComboBox()
        if has_document:
            self.scope_combo.addItem(_("Documento"), "document")
        if has_selection:
            self.scope_combo.addItem(_("Selezione"), "selection")
        self.scope_combo.addItem(_("Nessun documento"), "none")
        right.addWidget(QLabel(_("Ambito")))
        right.addWidget(self.scope_combo)
        right.addStretch(1)
        body.addLayout(right, 3)
        lay.addLayout(body, 1)
        btns = QHBoxLayout()
        self.b_dup = QPushButton(_("Duplica"))
        self.b_del = QPushButton(_("Elimina"))
        self.b_exp = QPushButton(_("Esporta…"))
        self.b_imp = QPushButton(_("Importa…"))
        self.b_run = QPushButton(_("Esegui"))
        self.b_run.setDefault(True)
        cancel = QPushButton(_("Annulla"))
        for b in (self.b_dup, self.b_del, self.b_exp, self.b_imp):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.b_run)
        lay.addLayout(btns)
        self.b_dup.clicked.connect(self.duplicate)
        self.b_del.clicked.connect(self.delete)
        self.b_exp.clicked.connect(self.export)
        self.b_imp.clicked.connect(self.import_file)
        self.b_run.clicked.connect(self.run)
        cancel.clicked.connect(self.reject)
        self.category.currentIndexChanged.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.list.currentItemChanged.connect(self._show)
        self._editors: dict[str, QWidget] = {}
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for t in self.store.search(self.search.text(), self.category.currentData() or ""):
            item = QListWidgetItem(f"{_(t.name)}  ·  {_(t.category)}" + ("" if t.builtin else _("  (personale)")))
            item.setData(Qt.ItemDataRole.UserRole, t)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show()

    def current(self) -> TaskTemplate | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, task_id: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole).id == task_id:
                self.list.setCurrentRow(i)

    def _show(self, *_a) -> None:
        t = self.current()
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors = {}
        self.b_run.setEnabled(t is not None)
        self.b_del.setEnabled(t is not None and not t.builtin)
        self.b_dup.setEnabled(t is not None)
        if t is None:
            self.description.setText(_("Nessuna attività trovata."))
            return
        desc = _(t.description) if t.description else ""
        if t.sensitive:
            desc += "\n" + _("Il risultato non sostituisce il parere di un professionista.")
        self.description.setText(desc)
        for p in t.params:
            if p.type == "number":
                w = QSpinBox()
                w.setRange(1, 100)
                try:
                    w.setValue(int(p.default or 1))
                except ValueError:
                    w.setValue(1)
            elif p.type == "choice":
                w = QComboBox()
                w.addItems(p.choices)
                if p.default in p.choices:
                    w.setCurrentText(p.default)
            else:
                w = QLineEdit(p.default)
            self._editors[p.name] = w
            self.form.addRow(_(p.name), w)
        idx = self.scope_combo.findData("none" if t.scope == "any" and self.scope_combo.count() == 1 else
                                        self.scope_combo.itemData(0))
        self.scope_combo.setCurrentIndex(max(0, idx))

    def values(self) -> dict:
        out = {}
        for name, w in self._editors.items():
            if isinstance(w, QSpinBox):
                out[name] = str(w.value())
            elif isinstance(w, QComboBox):
                out[name] = w.currentText()
            else:
                out[name] = w.text()
        return out

    def scope(self) -> str:
        return self.scope_combo.currentData()

    def run(self) -> None:
        t = self.current()
        if t is None:
            return
        if t.scope == "document" and self.scope() == "none":
            self.on_error(_("Questa attività richiede un documento."))
            return
        self.chosen = t
        self.accept()

    def duplicate(self) -> None:
        t = self.current()
        if t is not None:
            q = self.store.duplicate(t)
            self.reload()
            self.select(q.id)

    def delete(self) -> None:
        t = self.current()
        if t is not None and not t.builtin:
            self.store.delete(t.id)
            self.reload()

    def export(self) -> None:
        t = self.current()
        if t is None or self.ask_save_path is None:
            return
        path = self.ask_save_path(_("Esporta attività"), "attivita.json", "JSON (*.json)", ".json")
        if path:
            atomic_write(path, TaskStore.export([t]).encode("utf-8"))

    def import_file(self, path: str | None = None) -> None:
        path = path or (self.ask_open_path() if self.ask_open_path else None)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                items = self.store.import_text(f.read())
        except (OSError, UnicodeDecodeError) as e:
            self.on_error(_("Impossibile leggere il file: {e}").format(e=e))
            return
        except FoliantError as e:
            self.on_error(str(e))
            return
        self.reload()
        if items:
            self.select(items[-1].id)
