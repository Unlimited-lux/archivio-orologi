"""Finestra «Contratti» (AI-28): bozza, revisione per clausole, rischi e ricerca giuridica."""

from __future__ import annotations

from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from foliant.api import _, atomic_write, bind_icon, theme

from . import contracts, render


class ContractsDialog(QDialog):
    """Le operazioni lunghe passano per `run(title, fn)` (fn(job) → risultato o None)."""

    def __init__(self, run, scope_factory, document, *, ask_save_path, open_document, go_to, prepare_comm,
                 confirm, author: str = "", parent=None):
        super().__init__(parent)
        self.run, self.scope_factory, self.document = run, scope_factory, document
        self.ask_save_path, self.open_document, self.go_to = ask_save_path, open_document, go_to
        self.prepare_comm, self.confirm, self.author = prepare_comm, confirm, author
        self.clauses: list[contracts.Clause] = []
        self.risks: contracts.RisksResult | None = None
        self.setWindowTitle(_("Contratti"))
        self.resize(820, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(contracts.disclaimer()))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # --- bozza
        w = QWidget()
        form = QFormLayout(w)
        self.params = {}
        for key, label in (("tipo", _("Tipo")), ("parti", _("Parti")), ("oggetto", _("Oggetto")),
                           ("durata", _("Durata")), ("corrispettivo", _("Corrispettivo")),
                           ("clausole", _("Clausole richieste"))):
            self.params[key] = QLineEdit()
            form.addRow(label, self.params[key])
        b = QPushButton(_("Genera bozza"))
        bind_icon(b, "sparkles")
        b.clicked.connect(self.draft)
        form.addRow(b)
        self.draft_text = QPlainTextEdit()
        form.addRow(self.draft_text)
        row = QHBoxLayout()
        pdf_btn = QPushButton(_("Crea nuovo PDF"))
        pdf_btn.clicked.connect(lambda: self.draft_text.toPlainText() and self.open_document(
            __import__("foliant.modules.ai.writer", fromlist=["writer"]).text_to_new_pdf(self.draft_text.toPlainText())))
        row.addWidget(pdf_btn)
        form.addRow(row)
        self.tabs.addTab(w, _("Bozza"))
        # --- revisione
        w = QWidget()
        v = QVBoxLayout(w)
        row = QHBoxLayout()
        rev = QPushButton(_("Esamina il contratto"))
        bind_icon(rev, "scan-search")
        rev.clicked.connect(self.review)
        self.comments_btn = QPushButton(_("Aggiungi come commenti"))
        self.comments_btn.clicked.connect(self.add_comments)
        self.comm_btn = QPushButton(_("Prepara comunicazione al cliente"))
        self.comm_btn.clicked.connect(lambda: self.clauses and self.prepare_comm(
            contracts.communication_source(self.clauses)))
        csv_btn = QPushButton(_("Esporta CSV…"))
        csv_btn.clicked.connect(lambda: self.export_csv())
        for x in (rev, self.comments_btn, self.comm_btn, csv_btn):
            row.addWidget(x)
        v.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([_("N."), _("Titolo"), _("Pagina"), _("Criticità"), _("Sintesi"),
                                              _("Lacune"), _("Proposta")])
        self.table.cellClicked.connect(self.on_row)
        v.addWidget(self.table, 1)
        self.tabs.addTab(w, _("Revisione"))
        # --- rischi
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        self.party = QLineEdit()
        self.jurisdiction = QLineEdit(_("italiano"))
        form.addRow(_("Parte da tutelare"), self.party)
        form.addRow(_("Ordinamento"), self.jurisdiction)
        v.addLayout(form)
        b = QPushButton(_("Valuta i rischi"))
        b.clicked.connect(self.assess)
        v.addWidget(b)
        self.risks_view = QTextBrowser()
        self.risks_view.setOpenLinks(False)
        v.addWidget(self.risks_view, 1)
        self.tabs.addTab(w, _("Rischi e ricerca giuridica"))
        self.comments_btn.setEnabled(document is not None)

    # ----------------------------------------------------------------- bozza
    def draft(self) -> None:
        params = {k: e.text().strip() for k, e in self.params.items()}
        ans = self.run(_("Bozza del contratto…"), lambda job: contracts.draft_contract(job, params))
        if ans is not None:
            self.draft_text.setPlainText(ans.text)

    # ----------------------------------------------------------------- revisione
    def review(self) -> None:
        scope = self.scope_factory()
        if scope is None:
            return
        res = self.run(_("Revisione del contratto…"), lambda job: contracts.review_contract(job, scope))
        if res is not None:
            self.set_clauses(res)

    def set_clauses(self, clauses) -> None:
        self.clauses = list(clauses)
        self.table.setRowCount(len(self.clauses))
        warn = QBrush(theme.color("warning"))
        for r, c in enumerate(self.clauses):
            vals = [c.numero, c.titolo, str(c.pagina + 1) if c.pagina is not None else "", c.criticita,
                    c.sintesi + ("" if c.verified else f" ({_('non verificato')})"), c.lacune, c.proposta]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(text)
                if not c.verified:
                    item.setForeground(warn)
                    item.setToolTip(_("non verificato"))
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    def on_row(self, row: int, _col: int = 0) -> None:
        if 0 <= row < len(self.clauses) and self.clauses[row].pagina is not None:
            self.go_to(self.clauses[row].pagina)

    def add_comments(self) -> int:
        n = len([c for c in self.clauses if c.criticita in ("media", "alta")])
        if not n or self.document is None:
            return 0
        if not self.confirm(_("Aggiungi come commenti"),
                            _("Aggiungere {n} note alle clausole di criticità media o alta?").format(n=n)):
            return 0
        return contracts.add_review_comments(self.document, self.clauses, self.author or None)

    def export_csv(self, path: str | None = None) -> None:
        if not self.clauses:
            return
        path = path or self.ask_save_path(_("Esporta revisione"), "revisione.csv", "CSV (*.csv)", ".csv")
        if path:
            atomic_write(path, contracts.review_to_csv(self.clauses).encode("utf-8"))

    # ----------------------------------------------------------------- rischi
    def assess(self) -> None:
        scope = self.scope_factory()
        if scope is None:
            return
        party = self.party.text().strip() or _("la parte indicata")
        res = self.run(_("Rischi e ricerca giuridica…"), lambda job: contracts.risks_and_research(
            job, scope, party, self.jurisdiction.text().strip() or "italiano"))
        if res is not None:
            self.risks = res
            self.risks_view.setHtml(render.render_markdown(contracts.risks_markdown(res)).html)
