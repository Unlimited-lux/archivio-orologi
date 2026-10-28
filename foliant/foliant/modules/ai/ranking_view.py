"""Finestra «Confronta e classifica» (AI-32)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from foliant.api import FoliantError, _, atomic_write, bind_icon, theme

from . import collection, ranking
from .collection import Collection
from .ranking import Criterion


class RankingDialog(QDialog):
    """`run_eval(prepared, excluded, criteria, cv) -> (rows, warnings) | None`; `open_ref(path, page)`."""

    def __init__(self, run_eval, open_ref, ask_paths, ask_save_path, open_documents: dict, parent=None, *,
                 cache=None):
        super().__init__(parent)
        self.run_eval, self.open_ref, self.ask_paths, self.ask_save_path = run_eval, open_ref, ask_paths, ask_save_path
        self.open_documents = open_documents
        self.cache = cache
        self.coll = Collection(name=_("Classifica"))
        self.rows: list[ranking.RankRow] = []
        self.setWindowTitle(_("Confronta e classifica"))
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Documenti")))
        self.docs = QListWidget()
        self.docs.setMaximumHeight(110)
        lay.addWidget(self.docs)
        row = QHBoxLayout()
        b_open = QPushButton(_("Aggiungi le schede aperte"))
        b_files = QPushButton(_("Aggiungi file…"))
        b_open.clicked.connect(self.add_open)
        b_files.clicked.connect(lambda: self.add_paths(self.ask_paths()))
        row.addWidget(b_open)
        row.addWidget(b_files)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel(_("Criteri (peso da 1 a 5)")))
        self.criteria = QTableWidget(0, 4)
        self.criteria.setHorizontalHeaderLabels([_("Nome"), _("Descrizione"), _("Peso"), _("Obbligatorio")])
        self.criteria.horizontalHeader().setStretchLastSection(True)
        self.criteria.setMaximumHeight(150)
        lay.addWidget(self.criteria)
        row = QHBoxLayout()
        b_add = QPushButton(_("Aggiungi criterio"))
        bind_icon(b_add, "plus")
        b_add.clicked.connect(lambda: self.add_criterion())
        self.cv = QCheckBox(_("Curriculum (ignora età, stato civile, foto e origine)"))
        b_eval = QPushButton(_("Valuta"))
        bind_icon(b_eval, "sparkles")
        b_eval.clicked.connect(self.evaluate)
        row.addWidget(b_add)
        row.addWidget(self.cv, 1)
        row.addWidget(b_eval)
        lay.addLayout(row)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet(f"color: {theme.hex_color('warning')};")
        lay.addWidget(self.notice)
        self.results = QTableWidget(0, 0)
        self.results.cellClicked.connect(self.on_cell)
        lay.addWidget(self.results, 1)
        row = QHBoxLayout()
        b_csv = QPushButton(_("Esporta CSV…"))
        b_csv.clicked.connect(lambda: self.export_csv())
        b_pdf = QPushButton(_("Esporta PDF…"))
        b_pdf.clicked.connect(lambda: self.export_pdf())
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(b_csv)
        row.addWidget(b_pdf)
        row.addWidget(close)
        lay.addLayout(row)

    # ----------------------------------------------------------------- documenti e criteri
    def _refresh_docs(self) -> None:
        self.docs.clear()
        names = collection.short_names(self.coll.members)
        for m in self.coll.members:
            state = _("escluso: {r}").format(r=m.excluded) if m.excluded else _("pronto")
            self.docs.addItem(f"{names.get(m.path, '?')} · {m.title} — {state}")

    def add_open(self) -> None:
        for path, doc in self.open_documents.items():
            self._add(path, doc)
        self._refresh_docs()

    def add_paths(self, paths) -> None:
        for p in paths or []:
            self._add(p, self.open_documents.get(p))
        self._refresh_docs()

    def _add(self, path, doc=None) -> None:
        try:
            self.coll.add(path, doc)
        except FoliantError as e:
            self.notice.setText(str(e))

    def add_criterion(self, nome: str = "", descrizione: str = "", peso: int = 1, obbligatorio: bool = False) -> None:
        r = self.criteria.rowCount()
        self.criteria.insertRow(r)
        self.criteria.setItem(r, 0, QTableWidgetItem(nome))
        self.criteria.setItem(r, 1, QTableWidgetItem(descrizione))
        spin = QSpinBox()
        spin.setRange(1, 5)
        spin.setValue(peso)
        self.criteria.setCellWidget(r, 2, spin)
        check = QCheckBox()
        check.setChecked(obbligatorio)
        self.criteria.setCellWidget(r, 3, check)

    def criteria_list(self) -> list[Criterion]:
        out = []
        for r in range(self.criteria.rowCount()):
            nome = (self.criteria.item(r, 0).text() if self.criteria.item(r, 0) else "").strip()
            if not nome:
                continue
            desc = self.criteria.item(r, 1).text() if self.criteria.item(r, 1) else ""
            out.append(Criterion(nome, desc, self.criteria.cellWidget(r, 2).value(),
                                 self.criteria.cellWidget(r, 3).isChecked()))
        return out

    # ----------------------------------------------------------------- valutazione
    def evaluate(self) -> None:
        crit = self.criteria_list()
        if not crit:
            self.notice.setText(_("Definisci almeno un criterio."))
            return
        if not self.coll.usable():
            self.notice.setText(_("Aggiungi almeno un documento utilizzabile."))
            return
        prepared = collection.prepare(self.coll.members, self.open_documents, self.cache)
        excluded = [m for m in self.coll.members if m.excluded]
        res = self.run_eval(prepared, excluded, crit, self.cv.isChecked())
        if res is None:
            return
        self.rows, warnings = res
        self.notice.setText(" ".join(warnings))
        self.show_results(crit)

    def show_results(self, crit: list[Criterion]) -> None:
        self._crit = crit
        headers = [_("Posizione"), _("Documento")] + [f"{c.nome} (×{c.peso})" for c in crit] + [_("Totale")]
        self.results.clear()
        self.results.setColumnCount(len(headers))
        self.results.setHorizontalHeaderLabels(headers)
        self.results.setRowCount(len(self.rows))
        muted = QBrush(theme.color("text_muted"))
        for i, r in enumerate(self.rows):
            cells = [str(r.posizione or "—"), f"{r.etichetta} · {r.titolo}"]
            tips = ["", r.motivo]
            for c in crit:
                ev = r.scores.get(c.nome)
                cells.append(f"{ev.punteggio:g}" if ev else "")
                tips.append((ev.motivazione + (" — " + _("pagine {p}").format(
                    p=", ".join(str(p + 1) for p in ev.pagine)) if ev.pagine else "")) if ev else "")
            cells.append(f"{r.totale:.2f}" if r.totale is not None else _("escluso: {m}").format(m=r.motivo))
            tips.append("")
            for j, (text, tip) in enumerate(zip(cells, tips)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if tip:
                    item.setToolTip(tip)
                if r.escluso:
                    item.setForeground(muted)
                self.results.setItem(i, j, item)
        self.results.resizeColumnsToContents()

    def on_cell(self, row: int, col: int) -> None:
        crit = getattr(self, "_crit", [])
        if not (0 <= row < len(self.rows)) or not (2 <= col < 2 + len(crit)):
            return
        ev = self.rows[row].scores.get(crit[col - 2].nome)
        if ev and ev.pagine:
            self.open_ref(self.rows[row].documento, ev.pagine[0])

    def export_csv(self, path: str | None = None) -> None:
        if not self.rows:
            return
        path = path or self.ask_save_path(_("Esporta classifica"), "classifica.csv", "CSV (*.csv)", ".csv")
        if path:
            atomic_write(path, ranking.to_csv(self.rows, getattr(self, "_crit", [])).encode("utf-8"))

    def export_pdf(self, path: str | None = None) -> str | None:
        if not self.rows:
            return None
        path = path or self.ask_save_path(_("Esporta classifica"), "classifica.pdf", "PDF (*.pdf)", ".pdf")
        if path:
            atomic_write(path, ranking.to_pdf(self.rows, getattr(self, "_crit", [])))
        return path
