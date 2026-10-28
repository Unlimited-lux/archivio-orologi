"""Assistente comandi (AI-23): finestra dello strumento con i parametri proposti, da controllare e confermare."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from foliant.api import _

from . import commands


class ParamsDialog(QDialog):
    """Parametri precompilati dall'assistente: l'operazione parte solo con «Esegui»."""

    def __init__(self, title: str, page_count: int, params: dict, *, angle: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.page_count = page_count
        self.accepted_values: dict | None = None
        lay = QVBoxLayout(self)
        note = QLabel(_("Parametri proposti dall'assistente: controllali prima di eseguire. "
                        "L'operazione si può annullare."))
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.pages = QLineEdit(commands.compact_pages(commands.page_indices(params.get("pages", ""))))
        self.pages.setPlaceholderText(_("es. 3–5, 8"))
        form.addRow(_("Pagine"), self.pages)
        self.angle = None
        if angle:
            self.angle = QComboBox()
            for deg, text in ((90, _("90° in senso orario")), (180, _("180°")), (270, _("90° in senso antiorario"))):
                self.angle.addItem(text, deg)
            try:
                wanted = int(params.get("angle", 90)) % 360
            except (TypeError, ValueError):
                wanted = 90
            self.angle.setCurrentIndex(max(0, self.angle.findData(wanted)))
            form.addRow(_("Rotazione"), self.angle)
        lay.addLayout(form)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setProperty("error", True)
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Esegui"))
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict | None:
        text = self.pages.text().replace("–", "-")
        pages = commands.page_indices(text)
        if not pages or any(p >= self.page_count for p in pages) or -1 in commands._pages_of(text):
            self.error.setText(_("Pagine non valide: il documento ha {n} pagine.").format(n=self.page_count))
            return None
        out = {"pages": pages}
        if self.angle is not None:
            out["angle"] = int(self.angle.currentData())
        return out

    def confirm(self) -> bool:
        values = self.values()
        if values is None:
            return False
        self.accepted_values = values
        self.accept()
        return True
