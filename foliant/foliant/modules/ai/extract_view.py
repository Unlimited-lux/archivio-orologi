"""Griglia modificabile del risultato dell'estrazione dati (AI-10)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from foliant.api import FoliantError, _, atomic_write, bind_icon, theme

from . import extract
from .extract import ExtractResult

ROLE_KEY = Qt.UserRole + 1          # (indice record, colonna, indice riga annidata)

KIND_TITLES = {"tables": "Tabelle", "invoice": "Fattura/Ricevuta", "custom": "Schema personalizzato"}


class RawDialog(QDialog):
    """Risposta grezza del modello, in sola lettura."""

    def __init__(self, raw: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Risposta grezza del modello"))
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(raw)
        self.text.setReadOnly(True)
        lay.addWidget(self.text)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.Close).setText(_("Chiudi"))
        lay.addWidget(box)


class ExtractDialog(QDialog):
    """Controllo e modifica dei dati estratti, poi esportazione CSV/JSON o copia.

    `ask_save_path(titolo, nome_suggerito, filtro, suffisso) -> str | None` è il dialogo di
    salvataggio (di norma `ctx.ask_save_path`); `on_error(titolo, testo)` mostra gli errori.
    """

    def __init__(self, result: ExtractResult, ask_save_path: Callable[..., str | None], parent=None, *,
                 source_name: str = "", on_error: Callable[[str, str], None] | None = None):
        super().__init__(parent)
        self.result = result
        self.ask_save_path = ask_save_path
        self.source_name = source_name or result.source
        self.on_error = on_error
        self.raw_dialog: RawDialog | None = None
        self._filling = False
        self.setWindowTitle(_("Dati estratti") + " — " + _(KIND_TITLES.get(result.kind, result.kind)))
        self.resize(900, 560)

        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextFormat(Qt.PlainText)
        lay.addWidget(self.checks)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(False)
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(_("Separatore CSV:")))
        self.separator = QComboBox()
        self.separator.addItem(_("virgola (,)"), ",")
        self.separator.addItem(_("punto e virgola (;)"), ";")
        bar.addWidget(self.separator)
        bar.addStretch(1)
        self.csv_button = QPushButton(_("Esporta CSV…"))
        bind_icon(self.csv_button, "file-spreadsheet")
        self.json_button = QPushButton(_("Esporta JSON…"))
        bind_icon(self.json_button, "file-json")
        self.copy_button = QPushButton(_("Copia"))
        bind_icon(self.copy_button, "copy")
        self.raw_button = QPushButton(_("Mostra risposta grezza"))
        bind_icon(self.raw_button, "code")
        for b in (self.csv_button, self.json_button, self.copy_button, self.raw_button):
            bar.addWidget(b)
        lay.addLayout(bar)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText(_("Chiudi"))
        close.rejected.connect(self.reject)
        lay.addWidget(close)

        self.csv_button.clicked.connect(self.export_csv)
        self.json_button.clicked.connect(self.export_json)
        self.copy_button.clicked.connect(self.copy)
        self.raw_button.clicked.connect(self.show_raw)
        self.raw_button.setEnabled(bool(result.raw))
        self.table.itemChanged.connect(self._edited)
        self.fill()

    # ------------------------------------------------------------------ griglia
    def _unverified_brush(self) -> QBrush:
        c = QColor(theme.color("warning"))
        c.setAlpha(60)
        return QBrush(c)

    def fill(self) -> None:
        self._filling = True
        try:
            cols = extract.flat_columns(self.result)
            rows = extract.flat_rows(self.result)
            self.columns = cols
            self.table.clear()
            self.table.setColumnCount(len(cols) + 1)
            self.table.setHorizontalHeaderLabels(cols + [_("Pagina")])
            self.table.setRowCount(len(rows))
            prefixes = tuple(c + "." for c in extract.nested_columns(self.result))
            for r, (ri, list_col, li, row) in enumerate(rows):
                for c, col in enumerate(cols):
                    cell = row.get(col)
                    item = QTableWidgetItem(extract.format_value(cell.value) if cell else "")
                    nested = list_col is not None and col.startswith(list_col + ".")
                    item.setData(ROLE_KEY, (ri, col, li if nested else None))
                    if prefixes and col.startswith(prefixes) and not nested:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self._style(item, cell)
                    self.table.setItem(r, c, item)
                pages = [x.page for k, x in row.items() if x.page is not None
                         and (list_col is None or k.startswith(list_col + "."))] or \
                    [x.page for x in row.values() if x.page is not None]
                pitem = QTableWidgetItem(str(pages[0] + 1) if pages else "")
                pitem.setFlags(pitem.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, len(cols), pitem)
            self.table.resizeColumnsToContents()
        finally:
            self._filling = False
        self._update_labels()

    def _style(self, item: QTableWidgetItem, cell) -> None:
        if cell is not None and not cell.verified:
            item.setBackground(self._unverified_brush())
            item.setToolTip(_("non verificato"))
        else:
            item.setBackground(QBrush())
            item.setToolTip("")

    def _update_labels(self) -> None:
        unverified = sum(1 for _ri, _lc, _li, row in extract.flat_rows(self.result)
                         for c in row.values() if not c.verified)
        parts = [_("{n} righe").format(n=self.table.rowCount())]
        if self.result.model:
            parts.append(_("modello: {m}").format(m=self.result.model))
        if unverified:
            parts.append(_("{n} valori non verificati (evidenziati)").format(n=unverified))
        self.info.setText(" · ".join(parts))
        if self.result.checks:
            self.checks.setText(_("Controlli non superati:") + "\n" + "\n".join(f"• {c}" for c in self.result.checks))
            self.checks.setStyleSheet(f"color: {theme.hex_color('warning')};")
            self.checks.show()
        else:
            self.checks.setText("")
            self.checks.hide()

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        key = item.data(ROLE_KEY)
        if not key:
            return
        ri, col, li = key
        cell = extract.set_value(self.result, ri, col, li, item.text())
        self._filling = True
        try:
            # i campi del record compaiono su più righe: aggiornarle tutte
            c = self.columns.index(col)
            for r in range(self.table.rowCount()):
                other = self.table.item(r, c)
                if other is None:
                    continue
                okey = other.data(ROLE_KEY)
                if okey and okey[0] == ri and okey[1] == col and okey[2] == li:
                    other.setText(extract.format_value(cell.value))
                    self._style(other, cell)
        finally:
            self._filling = False
        self._update_labels()

    # ------------------------------------------------------------------ azioni
    def _error(self, title: str, text: str) -> None:
        if self.on_error:
            self.on_error(title, text)
        else:
            QMessageBox.warning(self, title, text)

    def _base_name(self) -> str:
        import os
        name = os.path.splitext(os.path.basename(self.source_name or ""))[0]
        return (name or _("dati")) + "-" + _("dati")

    def _save(self, title: str, suffix: str, file_filter: str, data: str) -> str | None:
        path = self.ask_save_path(title, self._base_name() + suffix, file_filter, suffix)
        if not path:
            return None
        try:
            atomic_write(path, data.encode("utf-8"))
        except (OSError, FoliantError) as e:
            self._error(_("Esportazione non riuscita"), str(e))
            return None
        return path

    def export_csv(self) -> str | None:
        return self._save(_("Esporta CSV"), ".csv", _("File CSV (*.csv)"),
                          extract.to_csv(self.result, self.separator.currentData()))

    def export_json(self) -> str | None:
        return self._save(_("Esporta JSON"), ".json", _("File JSON (*.json)"),
                          extract.to_json(self.result, self.source_name))

    def copy(self) -> None:
        """Copia come testo separato da tabulazioni (si incolla nei fogli di calcolo)."""
        QApplication.clipboard().setText(extract.to_csv(self.result, "\t"))

    def show_raw(self) -> None:
        self.raw_dialog = RawDialog(self.result.raw, self)
        self.raw_dialog.show()
