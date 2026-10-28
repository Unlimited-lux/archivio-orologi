"""Finestra della conversione in blocco (CNV-17) e report finale."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme

from .batch import ERROR, OK, SKIPPED, WARNINGS, collect_pdfs, common_root, report_csv
from .engine import FORMATS
from .export_dialog import FORMAT_ORDER, build_options


class FileList(QListWidget):
    """Elenco dei file con un segnaposto quando è vuoto; accetta file e cartelle trascinati."""

    def __init__(self, on_drop=None):
        super().__init__()
        self._on_drop = on_drop
        self.setAcceptDrops(True)
        self.placeholder = _("Aggiungi file o cartelle, o trascinali qui")

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        if self.count() == 0:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QPainter
            p = QPainter(self.viewport())
            p.setPen(theme.color("text_muted"))
            p.drawText(self.viewport().rect().adjusted(16, 16, -16, -16),
                       int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self.placeholder)
            p.end()

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths and self._on_drop is not None:
            ev.acceptProposedAction()
            self._on_drop(paths)
        else:
            super().dropEvent(ev)


class BatchDialog(QDialog):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(_("Converti più file"))
        self.resize(640, 600)
        self._roots: list[str] = []
        lay = QVBoxLayout(self)
        self.list = FileList(on_drop=lambda paths: self.add_paths(paths))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, icon_name, slot in ((_("Aggiungi file…"), "file-plus", self._add_files),
                                      (_("Aggiungi cartella…"), "folder-plus", self._add_folder),
                                      (_("Rimuovi"), "trash-2", self._remove)):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        self.recursive = QCheckBox(_("Includi le sottocartelle"))
        self.recursive.setChecked(True)
        row.addWidget(self.recursive)
        row.addStretch(1)
        lay.addLayout(row)
        self.notice = QLabel()
        self.notice.setStyleSheet(f"color: {theme.hex_color('warning')};")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        lay.addWidget(self.notice)

        form = QFormLayout()
        self.format_box = QComboBox()
        for key in FORMAT_ORDER:
            self.format_box.addItem(_(FORMATS[key][1]), key)
        form.addRow(_("Formato"), self.format_box)
        self.dest_box = QComboBox()
        self.dest_box.addItem(_("Accanto all'originale"), "same")
        self.dest_box.addItem(_("In una cartella…"), "folder")
        form.addRow(_("Destinazione"), self.dest_box)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        browse = QPushButton(_("Sfoglia…"))
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow(_("Cartella"), folder_row)
        self.keep = QCheckBox(_("Mantieni la struttura delle sottocartelle"))
        self.keep.setChecked(True)
        form.addRow("", self.keep)
        self.policy_box = QComboBox()
        self.policy_box.addItem(_("Aggiungi un suffisso numerico"), "rename")
        self.policy_box.addItem(_("Sovrascrivi"), "overwrite")
        form.addRow(_("Se il file esiste"), self.policy_box)
        from PySide6.QtWidgets import QSpinBox
        from .batch import default_workers
        self.workers = QSpinBox()
        self.workers.setRange(1, max(1, (__import__("os").cpu_count() or 1)))
        self.workers.setValue(default_workers())
        form.addRow(_("Processi paralleli"), self.workers)
        lay.addLayout(form)
        self.options_holder = QVBoxLayout()
        lay.addLayout(self.options_holder)
        self.panel = None

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Converti"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.format_box.currentIndexChanged.connect(self._format_changed)
        self.dest_box.currentIndexChanged.connect(self._dest_changed)
        self._format_changed()
        self._dest_changed()
        self._update()

    def _format_changed(self) -> None:
        if self.panel is not None:
            self.panel.setParent(None)
            self.panel.deleteLater()
        self.panel = build_options(self.format_box.currentData())
        self.options_holder.addWidget(self.panel)

    def _dest_changed(self) -> None:
        folder = self.dest_box.currentData() == "folder"
        self.folder_edit.setEnabled(folder)
        self.keep.setEnabled(folder)

    def _browse(self) -> None:
        path = self.ctx.ask_directory(_("Cartella di destinazione"))
        if path:
            self.folder_edit.setText(path)
            self.dest_box.setCurrentIndex(1)

    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            if Path(p).is_dir():
                self._roots.append(p)
        found, rejected = collect_pdfs(list(self.files()) + list(paths), self.recursive.isChecked())
        self.list.clear()
        self.list.addItems(found)
        if rejected:
            self.notice.setText(_("File non PDF ignorati: {n} (per crearne dei PDF usa «Crea PDF in blocco»).").format(
                n=len(rejected)))
            self.notice.setVisible(True)
        self._update()

    def _add_files(self) -> None:
        self.add_paths(self.ctx.ask_open_paths(_("Aggiungi file"), "PDF (*.pdf)"))

    def _add_folder(self) -> None:
        path = self.ctx.ask_directory(_("Aggiungi cartella"))
        if path:
            self.add_paths([path])

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._update()

    def _update(self) -> None:
        self.ok_button.setEnabled(self.list.count() > 0)

    def files(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def values(self):
        files = self.files()
        out_dir = self.folder_edit.text().strip() if self.dest_box.currentData() == "folder" else None
        root = self._roots[0] if len(self._roots) == 1 else common_root(files)
        options = dict(self.panel.values()) if self.panel else {}
        options["policy"] = self.policy_box.currentData()
        options["workers"] = self.workers.value()
        return files, self.format_box.currentData(), out_dir or None, self.keep.isChecked(), root, options


def show_batch_report(controller, items) -> None:
    """Riepilogo con conteggi, tabella per file ed esportazione del report."""
    dlg = QDialog(controller.window)
    dlg.setWindowTitle(_("Conversione in blocco completata"))
    dlg.resize(720, 420)
    lay = QVBoxLayout(dlg)
    counts = {s: sum(1 for it in items if it.status == s) for s in (OK, WARNINGS, ERROR, SKIPPED)}
    lay.addWidget(QLabel(_("Completati: {ok} · Con avvisi: {w} · Errori: {e} · Saltati: {s}").format(
        ok=counts[OK], w=counts[WARNINGS], e=counts[ERROR], s=counts[SKIPPED])))
    table = QTableWidget(len(items), 3)
    table.setHorizontalHeaderLabels([_("File"), _("Stato"), _("Note")])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    for r, it in enumerate(items):
        table.setItem(r, 0, QTableWidgetItem(Path(it.path).name))
        table.setItem(r, 1, QTableWidgetItem(_(it.status)))
        table.setItem(r, 2, QTableWidgetItem(it.message or " | ".join(Path(o).name for o in it.outputs)))
    lay.addWidget(table, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    save = buttons.addButton(_("Esporta report…"), QDialogButtonBox.ButtonRole.ActionRole)
    buttons.rejected.connect(dlg.reject)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))

    def export():
        path = controller.ctx.ask_save_path(_("Esporta report"), "report_conversione.csv", "CSV (*.csv)", ".csv")
        if path:
            Path(path).write_text(report_csv(items), encoding="utf-8-sig")

    save.clicked.connect(export)
    lay.addWidget(buttons)
    dlg.exec()
