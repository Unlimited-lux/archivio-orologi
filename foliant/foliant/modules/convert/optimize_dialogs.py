"""Finestre di compressione (CNV-29) e ottimizzazione avanzata (CNV-30)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme

LEVEL_LABELS = {
    "lossless": ("Senza perdita", "Nessun ricampionamento: pulizia e compressione ottimale"),
    "high": ("Alta qualità", "Immagini a 225 ppi, JPEG qualità 85"),
    "medium": ("Media", "Immagini a 150 ppi, JPEG qualità 70"),
    "low": ("Bassa", "Immagini a 96 ppi, JPEG qualità 50: file più piccolo"),
}


def human_size(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} GB"


class CompressDialog(QDialog):
    """Scelta del livello con dimensioni stimate, anteprima e destinazione."""

    def __init__(self, parent, *, before: int, estimates: dict, has_document: bool = True, preview=None,
                 levels=("lossless", "high", "medium", "low")):
        super().__init__(parent)
        self.setWindowTitle(_("Comprimi PDF"))
        self.setMinimumWidth(560)
        self._preview = preview
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Dimensione attuale: {s}").format(s=human_size(before))))
        self.table = QTableWidget(len(levels), 3)
        self.table.setHorizontalHeaderLabels([_("Livello"), _("Dimensione stimata"), _("Riduzione")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.levels = list(levels)
        for r, level in enumerate(levels):
            title, desc = LEVEL_LABELS.get(level, (level, ""))
            item = QTableWidgetItem(_(title))
            item.setToolTip(_(desc))
            item.setData(Qt.ItemDataRole.UserRole, level)
            self.table.setItem(r, 0, item)
            est = estimates.get(level)
            self.table.setItem(r, 1, QTableWidgetItem(human_size(est)))
            red = "—" if not est or not before else f"{max(0.0, (1 - est / before) * 100):.0f}%"
            self.table.setItem(r, 2, QTableWidgetItem(red))
        self.table.selectRow(min(2, len(levels) - 1))
        self.table.setFixedHeight(self.table.verticalHeader().length() + self.table.horizontalHeader().sizeHint().height() + 4)
        lay.addWidget(self.table)

        prev = QGroupBox(_("Anteprima di un dettaglio della pagina"))
        outer = QVBoxLayout(prev)
        pl = QHBoxLayout()
        outer.addLayout(pl)
        self.before_label = QLabel(_("Originale"))
        self.after_label = QLabel(_("Compresso"))
        for lab in (self.before_label, self.after_label):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setFixedSize(250, 180)
            lab.setStyleSheet(f"border: 1px solid {theme.hex_color('border')};")
            pl.addWidget(lab, 1)
        self.preview_button = QPushButton(_("Anteprima"))
        bind_icon(self.preview_button, "eye")
        self.preview_button.setAutoDefault(False)
        self.preview_button.clicked.connect(self.update_preview)
        self.preview_button.setEnabled(preview is not None)
        outer.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignRight)
        lay.addWidget(prev)

        form = QFormLayout()
        self.target = QComboBox()
        if has_document:
            self.target.addItem(_("Applica al documento aperto (annullabile)"), "document")
        self.target.addItem(_("Salva come nuovo file"), "file")
        form.addRow(_("Risultato"), self.target)
        self.linearize = QCheckBox(_("Ottimizza per la visualizzazione web veloce"))
        form.addRow("", self.linearize)
        lay.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Comprimi"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def level(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        r = rows[0].row() if rows else 2
        return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)

    def update_preview(self) -> None:
        if self._preview is None:
            return
        result = self._preview(self.level())
        if not result:
            return
        before, after = result
        for lab, img in ((self.before_label, before), (self.after_label, after)):
            pm = QPixmap.fromImage(img).scaled(lab.width() - 4, lab.height() - 4, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            lab.setPixmap(pm)

    def values(self) -> dict:
        return {"level": self.level(), "target": self.target.currentData(), "linearize": self.linearize.isChecked()}


class AdvancedDialog(QDialog):
    """Analisi dello spazio e impostazioni dettagliate di ottimizzazione."""

    def __init__(self, parent, *, analysis: list[tuple[str, int]], has_document: bool = True):
        super().__init__(parent)
        self.setWindowTitle(_("Compressione personalizzata"))
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        box = QGroupBox(_("Analisi dello spazio"))
        bl = QVBoxLayout(box)
        total = sum(n for _c, n in analysis) or 1
        table = QTableWidget(len(analysis), 3)
        table.setHorizontalHeaderLabels([_("Contenuto"), _("Dimensione"), "%"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for r, (cat, n) in enumerate(analysis):
            table.setItem(r, 0, QTableWidgetItem(_(cat)))
            table.setItem(r, 1, QTableWidgetItem(human_size(n)))
            table.setItem(r, 2, QTableWidgetItem(f"{n * 100 / total:.1f}%".replace(".", ",")))
        table.setMaximumHeight(240)
        bl.addWidget(table)
        lay.addWidget(box)

        img = QGroupBox(_("Immagini"))
        f = QFormLayout(img)
        self.image_ppi = QSpinBox()
        self.image_ppi.setRange(0, 1200)
        self.image_ppi.setValue(150)
        self.image_ppi.setSpecialValueText(_("Non ricampionare"))
        self.image_ppi.setSuffix(" ppi")
        f.addRow(_("Risoluzione di destinazione"), self.image_ppi)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 2400)
        self.threshold.setValue(225)
        self.threshold.setSuffix(" ppi")
        f.addRow(_("Solo immagini sopra"), self.threshold)
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(75)
        f.addRow(_("Qualità JPEG"), self.quality)
        self.grayscale = QCheckBox(_("Converti in scala di grigi"))
        f.addRow("", self.grayscale)
        lay.addWidget(img)

        rm = QGroupBox(_("Elimina e pulisci"))
        g = QVBoxLayout(rm)
        self.checks: dict[str, QCheckBox] = {}
        for key, label, default in (
                ("dedup", _("Unisci gli oggetti duplicati"), True),
                ("remove_unused", _("Rimuovi gli oggetti inutilizzati"), True),
                ("remove_thumbnails", _("Rimuovi le miniature incorporate"), True),
                ("remove_metadata", _("Rimuovi i metadati"), False),
                ("remove_javascript", _("Rimuovi JavaScript"), False),
                ("remove_attachments", _("Rimuovi gli allegati"), False),
                ("remove_annotations", _("Rimuovi i commenti"), False),
                ("remove_bookmarks", _("Rimuovi i segnalibri"), False),
                ("remove_structure", _("Rimuovi la struttura logica (riduce l'accessibilità)"), False),
                ("linearize", _("Ottimizza per la visualizzazione web veloce"), False)):
            cb = QCheckBox(label)
            cb.setChecked(default)
            self.checks[key] = cb
            g.addWidget(cb)
        lay.addWidget(rm)

        form = QFormLayout()
        self.target = QComboBox()
        if has_document:
            self.target.addItem(_("Applica al documento aperto (annullabile)"), "document")
        self.target.addItem(_("Salva come nuovo file"), "file")
        form.addRow(_("Risultato"), self.target)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Ottimizza"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict:
        out = {k: cb.isChecked() for k, cb in self.checks.items()}
        out.update(image_ppi=self.image_ppi.value() or None, image_threshold_ppi=self.threshold.value() or None,
                   jpeg_quality=self.quality.value(), grayscale=self.grayscale.isChecked())
        return out


class FlattenDialog(QDialog):
    """Scelta degli elementi da appiattire (CNV-33)."""

    def __init__(self, parent, page_count: int):
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit
        self.setWindowTitle(_("Appiattisci"))
        self.setMinimumWidth(460)
        self.page_count = page_count
        lay = QVBoxLayout(self)
        box = QGroupBox(_("Elementi da rendere parte fissa della pagina"))
        g = QVBoxLayout(box)
        self.checks: dict[str, QCheckBox] = {}
        for key, label, default in (("annotations", _("Annotazioni e commenti (i collegamenti restano attivi)"), True),
                                    ("forms", _("Campi modulo compilati e firme"), True),
                                    ("layers", _("Livelli (nello stato visibile corrente)"), True),
                                    ("crop", _("Ritaglio delle pagine (reso definitivo)"), False),
                                    ("rasterize", _("Rasterizza le pagine (testo non selezionabile senza OCR)"), False)):
            cb = QCheckBox(label)
            cb.setChecked(default)
            self.checks[key] = cb
            g.addWidget(cb)
        lay.addWidget(box)
        form = QFormLayout()
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(_("Tutte (es. 1-3, 7)"))
        form.addRow(_("Pagine"), self.pages)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(200)
        self.dpi.setSuffix(" dpi")
        form.addRow(_("Risoluzione"), self.dpi)
        self.ocr = QCheckBox(_("Mantieni il testo ricercabile con l'OCR"))
        self.ocr.setChecked(True)
        form.addRow("", self.ocr)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.setVisible(False)
        lay.addWidget(self.error)
        self.checks["rasterize"].toggled.connect(self._update)
        self._update()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Appiattisci"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _update(self) -> None:
        on = self.checks["rasterize"].isChecked()
        self.dpi.setEnabled(on)
        self.ocr.setEnabled(on)

    def accept(self) -> None:
        from .common import RangeError, parse_page_range
        try:
            parse_page_range(self.pages.text(), self.page_count)
        except RangeError as e:
            self.error.setText(str(e))
            self.error.setVisible(True)
            return
        super().accept()

    def values(self) -> dict:
        out = {k: cb.isChecked() for k, cb in self.checks.items()}
        out.update(pages=self.pages.text(), dpi=self.dpi.value(), ocr=self.ocr.isChecked())
        return out
