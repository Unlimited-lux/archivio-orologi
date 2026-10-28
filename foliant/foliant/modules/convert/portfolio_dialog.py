"""Finestra «Crea portfolio» (CNV-34): file, cartelle, descrizioni, colonna personalizzata e impostazioni."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from foliant.api import _, bind_icon

COL_NAME, COL_FOLDER, COL_DESC, COL_CUSTOM = range(4)


class PortfolioDialog(QDialog):
    def __init__(self, parent, paths: list[str], ask_files=None, ask_cover=None):
        super().__init__(parent)
        self.setWindowTitle(_("Crea portfolio"))
        self.resize(760, 520)
        self._ask_files = ask_files
        self._ask_cover = ask_cover
        self._paths: list[str] = []
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(_("Portfolio"))
        form.addRow(_("Titolo"), self.title)
        lay.addLayout(form)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("Nome"), _("Cartella"), _("Descrizione"), _("Colonna personalizzata")])
        self.table.horizontalHeader().setSectionResizeMode(COL_DESC, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, icon_name, slot in ((_("Aggiungi file…"), "file-plus", self._add),
                                      (_("Rimuovi"), "trash-2", self._remove)):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        opts = QFormLayout()
        custom = QHBoxLayout()
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText(_("es. Cliente (vuoto = nessuna)"))
        self.custom_type = QComboBox()
        for label, data in ((_("Testo"), "text"), (_("Numero"), "number"), (_("Data"), "date")):
            self.custom_type.addItem(label, data)
        custom.addWidget(self.custom_name, 1)
        custom.addWidget(self.custom_type)
        opts.addRow(_("Colonna personalizzata"), custom)
        self.view = QComboBox()
        self.view.addItem(_("Elenco"), "list")
        self.view.addItem(_("Miniature"), "thumbnails")
        opts.addRow(_("Vista all'apertura"), self.view)
        sort_row = QHBoxLayout()
        self.sort = QComboBox()
        self.sort.addItem(_("Nessuno"), None)
        for label, key in ((_("Nome"), "FileName"), (_("Descrizione"), "Description"), (_("Dimensione"), "Size"),
                           (_("Data di modifica"), "ModDate"), (_("Colonna personalizzata"), "custom")):
            self.sort.addItem(label, key)
        self.sort_dir = QComboBox()
        self.sort_dir.addItem(_("Crescente"), True)
        self.sort_dir.addItem(_("Decrescente"), False)
        sort_row.addWidget(self.sort, 1)
        sort_row.addWidget(self.sort_dir)
        opts.addRow(_("Ordinamento"), sort_row)
        self.initial = QComboBox()
        opts.addRow(_("File mostrato all'apertura"), self.initial)
        cover_row = QHBoxLayout()
        self.cover = QLineEdit()
        self.cover.setPlaceholderText(_("Generata automaticamente"))
        browse = QPushButton(_("Sfoglia…"))
        browse.setAutoDefault(False)
        browse.clicked.connect(self._pick_cover)
        cover_row.addWidget(self.cover, 1)
        cover_row.addWidget(browse)
        opts.addRow(_("Copertina (PDF)"), cover_row)
        lay.addLayout(opts)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Crea portfolio"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        parents = {str(Path(p).parent) for p in self._paths + list(paths)}
        for p in paths:
            if p in self._paths:
                continue
            self._paths.append(p)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, COL_NAME, QTableWidgetItem(Path(p).name))
            self.table.setItem(r, COL_FOLDER, QTableWidgetItem(Path(p).parent.name if len(parents) > 1 else ""))
            self.table.setItem(r, COL_DESC, QTableWidgetItem(""))
            self.table.setItem(r, COL_CUSTOM, QTableWidgetItem(""))
            self.table.item(r, COL_NAME).setToolTip(p)
        self._refresh_initial()

    def _refresh_initial(self) -> None:
        current = self.initial.currentData()
        self.initial.clear()
        self.initial.addItem(_("(nessuno)"), None)
        for r in range(self.table.rowCount()):
            name = self.table.item(r, COL_NAME).text()
            self.initial.addItem(name, r)
        i = self.initial.findData(current)
        if i >= 0:
            self.initial.setCurrentIndex(i)
        self.ok_button.setEnabled(self.table.rowCount() > 0)

    def _add(self) -> None:
        if self._ask_files is not None:
            self.add_paths(self._ask_files())

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
            del self._paths[r]
        self._refresh_initial()

    def _pick_cover(self) -> None:
        if self._ask_cover is not None:
            paths = self._ask_cover()
            if paths:
                self.cover.setText(paths[0])

    def values(self) -> tuple[list[dict], dict]:
        key = "".join(ch for ch in self.custom_name.text().strip().title() if ch.isalnum())
        entries = []
        for r, path in enumerate(self._paths):
            e = {"path": path, "name": self.table.item(r, COL_NAME).text().strip() or Path(path).name,
                 "folder": self.table.item(r, COL_FOLDER).text().strip(),
                 "description": self.table.item(r, COL_DESC).text().strip()}
            if key:
                e["values"] = {key: self.table.item(r, COL_CUSTOM).text().strip()}
            entries.append(e)
        sort_key = self.sort.currentData()
        if sort_key == "custom":
            sort_key = key or None
        initial_row = self.initial.currentData()
        options = {"title": self.title.text().strip() or _("Portfolio"), "view": self.view.currentData(),
                   "columns": [(key, self.custom_name.text().strip(), self.custom_type.currentData())] if key else [],
                   "sort": (sort_key, self.sort_dir.currentData()) if sort_key else None,
                   "initial": initial_row, "cover_pdf": self.cover.text().strip() or None}
        return entries, options


class EditPortfolioDialog(QDialog):
    """Modifica di un portfolio aperto: aggiunta, rimozione e spostamento di file, rinomina delle
    cartelle (applicati insieme come un solo passo annullabile)."""

    def __init__(self, parent, files: list[tuple[str, int, str]], folders: list[str], ask_files=None,
                 file_folders: dict[str, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Modifica portfolio"))
        self.resize(700, 460)
        self._ask_files = ask_files
        self.removed: list[str] = []
        self.added: list[dict] = []
        self.moves: dict[str, str] = {}          # chiave → cartella di destinazione
        self.renames: list[tuple[str, str]] = []  # (vecchio nome, nuovo nome) delle cartelle
        self.folders = list(folders)
        file_folders = file_folders or {}
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("Nome"), _("Cartella"), _("Dimensione"), _("Descrizione")])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        for name, size, desc in files:
            shown = name[name.index(">") + 1:] if name.startswith("<") and ">" in name else name
            self._row(shown, f"{size:,}".replace(",", ".") + " B", desc, folder=file_folders.get(name, ""),
                      ref=("old", name))
        form = QFormLayout()
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItem("")
        for f in folders:
            self.folder.addItem(f)
        form.addRow(_("Cartella per i nuovi file"), self.folder)
        lay.addLayout(form)
        row = QHBoxLayout()
        for text, icon_name, slot in ((_("Aggiungi file…"), "file-plus", self._add),
                                      (_("Rimuovi"), "trash-2", self._remove),
                                      (_("Sposta in cartella…"), "folder-input", self._move),
                                      (_("Rinomina cartella…"), "folder-pen", self._rename_folder)):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Applica"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _row(self, name: str, size: str, desc: str, *, folder: str = "", ref=None) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(name)
        item.setData(256, ref)
        self.table.setItem(r, 0, item)
        self.table.setItem(r, 1, QTableWidgetItem(folder))
        self.table.setItem(r, 2, QTableWidgetItem(size))
        self.table.setItem(r, 3, QTableWidgetItem(desc))

    def add_paths(self, paths: list[str]) -> None:
        folder = self.folder.currentText().strip()
        for p in paths:
            self.added.append({"path": p, "folder": folder})
            self._row(Path(p).name + "  " + _("(nuovo)"), "", "", folder=folder, ref=("new", len(self.added) - 1))

    def _add(self) -> None:
        if self._ask_files is not None:
            self.add_paths(self._ask_files())

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _remove(self) -> None:
        for r in reversed(self._selected_rows()):
            kind, ref = self.table.item(r, 0).data(256)
            if kind == "old":
                self.removed.append(ref)
                self.moves.pop(ref, None)
            else:
                self.added[ref] = None
            self.table.removeRow(r)

    def _ask_folder(self, title: str, label: str, current: str = "") -> str | None:
        from PySide6.QtWidgets import QInputDialog
        items = [""] + self.folders
        name, ok = QInputDialog.getItem(self, title, label, items, items.index(current) if current in items else 0,
                                        True)
        return name.strip() if ok else None

    def move_rows(self, rows: list[int], folder: str) -> None:
        for r in rows:
            kind, ref = self.table.item(r, 0).data(256)
            if kind == "old":
                self.moves[ref] = folder
            elif self.added[ref] is not None:
                self.added[ref]["folder"] = folder
            self.table.item(r, 1).setText(folder)
        if folder and folder not in self.folders:
            self.folders.append(folder)
            self.folder.addItem(folder)

    def _move(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        folder = self._ask_folder(_("Sposta in cartella"), _("Cartella (vuota = radice del portfolio):"))
        if folder is not None:
            self.move_rows(rows, folder)

    def rename(self, old: str, new: str) -> bool:
        new = new.strip()
        if not new or "/" in new or new in self.folders or old not in self.folders:
            return False
        self.renames.append((old, new))
        self.folders[self.folders.index(old)] = new
        i = self.folder.findText(old)
        if i >= 0:
            self.folder.setItemText(i, new)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == old:
                self.table.item(r, 1).setText(new)
        for k, f in list(self.moves.items()):
            if f == old:
                self.moves[k] = new
        for a in self.added:
            if a is not None and a["folder"] == old:
                a["folder"] = new
        return True

    def _rename_folder(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        if not self.folders:
            QMessageBox.information(self, _("Rinomina cartella"), _("Il portfolio non ha cartelle."))
            return
        old, ok = QInputDialog.getItem(self, _("Rinomina cartella"), _("Cartella:"), self.folders, 0, False)
        if not ok:
            return
        new, ok = QInputDialog.getText(self, _("Rinomina cartella"), _("Nuovo nome:"), text=old)
        if ok and new.strip() != old and not self.rename(old, new):
            QMessageBox.warning(self, _("Rinomina cartella"), _("Nome di cartella non valido o già usato."))

    def changes(self) -> tuple[list[dict], list[str]]:
        return [a for a in self.added if a], list(self.removed)
