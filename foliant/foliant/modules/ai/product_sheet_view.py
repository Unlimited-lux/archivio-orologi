"""Finestra della scheda prodotto (AI-30)."""

from __future__ import annotations

from PySide6.QtGui import QBrush, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout,
)

from foliant.api import _, atomic_write, bind_icon, theme

from . import extract, product_sheet, render
from .dialogs import language_combo


class ProductSheetDialog(QDialog):
    """`generate(language, tone, length) -> ProductSheet | None`; `open_document(bytes)`."""

    def __init__(self, generate, ask_save_path, open_document, parent=None):
        super().__init__(parent)
        self.generate_fn, self.ask_save_path, self.open_document = generate, ask_save_path, open_document
        self.sheet: product_sheet.ProductSheet | None = None
        self.setWindowTitle(_("Scheda prodotto"))
        self.resize(720, 640)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.language = language_combo("it")
        self.tone = QComboBox()
        self.tone.addItem(_("Tecnico"), "tecnico")
        self.tone.addItem(_("Commerciale"), "commerciale")
        self.length = QSpinBox()
        self.length.setRange(50, 1000)
        self.length.setValue(150)
        self.length.setSuffix(_(" parole"))
        go = QPushButton(_("Genera"))
        bind_icon(go, "sparkles")
        go.clicked.connect(self.generate)
        for w in (self.language, self.tone, self.length, go):
            row.addWidget(w)
        lay.addLayout(row)
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"color: {theme.hex_color('warning')};")
        lay.addWidget(self.warnings)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        lay.addWidget(self.view, 2)
        lay.addWidget(QLabel(_("Specifiche tecniche")))
        self.specs = QTableWidget(0, 4)
        self.specs.setHorizontalHeaderLabels([_("Specifica"), _("Valore"), _("Unità"), _("Pagina")])
        self.specs.horizontalHeader().setStretchLastSection(True)
        self.specs.itemChanged.connect(self._edited)
        lay.addWidget(self.specs, 1)
        row = QHBoxLayout()
        for text, icon_name, fn in ((_("Copia"), "copy", self.copy), (_("Esporta specifiche CSV…"), "table",
                                                                      lambda: self.export_csv()),
                                    (_("Crea nuovo PDF"), "file-plus", self.new_pdf)):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def generate(self) -> None:
        sheet = self.generate_fn(self.language.currentData(), self.tone.currentData(), self.length.value())
        if sheet is not None:
            self.set_sheet(sheet)

    def set_sheet(self, sheet: product_sheet.ProductSheet) -> None:
        self.sheet = sheet
        self.warnings.setText(" ".join(sheet.warnings))
        self.view.setHtml(render.render_markdown(product_sheet.to_markdown(sheet)).html)
        self.specs.blockSignals(True)
        self.specs.setRowCount(len(sheet.specifiche))
        warn = QBrush(theme.color("warning"))
        for r, s in enumerate(sheet.specifiche):
            vals = [s.nome, extract.format_value(s.valore), s.unita, str(s.pagina + 1) if s.pagina is not None else ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if not s.verified and c == 1:
                    item.setForeground(warn)
                    item.setToolTip(_("non verificato"))
                self.specs.setItem(r, c, item)
        self.specs.blockSignals(False)

    def _edited(self, item) -> None:
        if self.sheet is None or item.row() >= len(self.sheet.specifiche):
            return
        s = self.sheet.specifiche[item.row()]
        text = item.text()
        if item.column() == 0:
            s.nome = text
        elif item.column() == 1:
            n = extract.parse_number(text)
            s.valore, s.verified = (n if n is not None else text), True
        elif item.column() == 2:
            s.unita = text

    def copy(self) -> None:
        if self.sheet:
            QGuiApplication.clipboard().setText(render.plain_text(product_sheet.to_markdown(self.sheet)))

    def export_csv(self, path: str | None = None) -> None:
        if self.sheet is None:
            return
        path = path or self.ask_save_path(_("Esporta specifiche"), "specifiche.csv", "CSV (*.csv)", ".csv")
        if path:
            atomic_write(path, product_sheet.specs_to_csv(self.sheet).encode("utf-8"))

    def new_pdf(self) -> None:
        if self.sheet is None:
            return
        from . import writer
        self.open_document(writer.text_to_new_pdf(product_sheet.to_markdown(self.sheet)))
