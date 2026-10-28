"""Dialogo «Font del documento» (TXT-11)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ...core.engine import ENGINE_LOCK
from ...core.errors import FoliantError
from ...i18n import _
from . import fontlist, fonts


class FontsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(_("Font del documento"))
        self.resize(760, 420)
        lay = QVBoxLayout(self)
        info = QLabel(_("Font usati dal testo di pagina. La sostituzione agisce solo sul testo di pagina, non su "
                        "annotazioni e campi modulo."))
        info.setWordWrap(True)
        info.setProperty("muted", True)
        lay.addWidget(info)
        self.only_problems = QCheckBox(_("Solo problemi (non incorporati, sostituiti o senza /ToUnicode)"))
        self.only_problems.toggled.connect(self.refresh)
        lay.addWidget(self.only_problems)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([_("Font"), _("Tipo"), _("Codifica"), _("Stato"), _("Pagine"),
                                              _("Visualizzazione")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_replace = QPushButton(_("Sostituisci…"))
        self.btn_replace.clicked.connect(self.replace_selected)
        self.btn_embed = QPushButton(_("Incorpora"))
        self.btn_embed.clicked.connect(self.embed_selected)
        self.btn_map = QPushButton(_("Corrispondenze…"))
        self.btn_map.clicked.connect(self.edit_mappings)
        for b in (self.btn_replace, self.btn_embed, self.btn_map):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)
        self.entries: list[fontlist.FontEntry] = []
        self.refresh()

    def refresh(self) -> None:
        view = self.ctx.current_view()
        self.entries = []
        if view is not None:
            with ENGINE_LOCK:
                self.entries = fontlist.document_fonts(view.document.pdf)
        shown = [e for e in self.entries if e.problem or not self.only_problems.isChecked()]
        self.table.setRowCount(len(shown))
        states = {fontlist.STATE_FULL: _("completo"), fontlist.STATE_SUBSET: _("sottoinsieme"),
                  fontlist.STATE_NOT_EMBEDDED: _("non incorporato")}
        for r, e in enumerate(shown):
            pages = ", ".join(str(p) for p in e.pages[:12]) + ("…" if len(e.pages) > 12 else "")
            values = [e.name, e.type, e.encoding, states.get(e.state, e.state) +
                      ("" if e.has_tounicode else _(", senza /ToUnicode")), pages, e.display_font]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, e.name)
                self.table.setItem(r, c, item)
        if shown:
            self.table.selectRow(0)

    def selected(self) -> fontlist.FontEntry | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((e for e in self.entries if e.name == name), None)

    def ask_family(self, title: str) -> str | None:
        families = fonts.system_families()
        if not families:
            self.ctx.error(title, _("Nessun font installato nel sistema."))
            return None
        name, ok = QInputDialog.getItem(self, title, _("Font di destinazione:"), families, 0, False)
        return name if ok and name else None

    def _run(self, label: str, fn) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        try:
            with view.document.change(label) as pdf:
                summary = fn(pdf)
        except FoliantError as e:
            self.ctx.error(_("Font del documento"), str(e))
            return
        except Exception as e:
            self.ctx.error(_("Font del documento"), _("Operazione non riuscita: {e}").format(e=e))
            return
        text = _("Blocchi ricomposti: {n}.").format(n=summary.blocks)
        if summary.width_changed:
            text += " " + _("Larghezza cambiata oltre il 5% in {n} blocchi: {l}").format(
                n=len(summary.width_changed), l="; ".join(f"p. {p} «{t}»" for p, t in summary.width_changed[:5]))
        self.summary.setText(text)
        self.refresh()

    def replace_selected(self) -> None:
        entry = self.selected()
        if entry is None:
            return
        target = self.ask_family(_("Sostituisci «{f}»").format(f=entry.name))
        if target:
            self._run(_("Sostituisci font"), lambda pdf: fontlist.replace_font(pdf, entry.name, target))

    def embed_selected(self) -> None:
        entry = self.selected()
        if entry is None:
            return
        if entry.state != fontlist.STATE_NOT_EMBEDDED:
            self.summary.setText(_("Il font «{f}» è già incorporato.").format(f=entry.name))
            return
        self._run(_("Incorpora font"), lambda pdf: fontlist.embed_font(pdf, entry.name))

    def edit_mappings(self) -> None:
        dlg = MappingsDialog(self)
        dlg.exec()


class MappingsDialog(QDialog):
    """Tabella personale «font originale → font da usare»."""

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(_("Corrispondenze dei font"))
        self.resize(520, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Quando un font originale non è disponibile viene usato il font indicato qui, "
                               "in tutti i documenti.")))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([_("Font originale"), _("Font da usare")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton(_("Aggiungi"))
        add.clicked.connect(lambda: self.add_row("", ""))
        rem = QPushButton(_("Rimuovi"))
        rem.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for k, v in fontlist.load_mappings(self.path).items():
            self.add_row(k, v)

    def add_row(self, original: str, target: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(original))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(fonts.system_families())
        combo.setCurrentText(target)
        self.table.setCellWidget(r, 1, combo)

    def mapping(self) -> dict[str, str]:
        out = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            combo = self.table.cellWidget(r, 1)
            k = item.text().strip() if item else ""
            v = combo.currentText().strip() if combo else ""
            if k and v:
                out[k] = v
        return out

    def save(self) -> None:
        fontlist.save_mappings(self.mapping(), self.path)
        self.accept()
