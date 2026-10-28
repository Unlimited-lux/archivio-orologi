"""Pannello «Collegamenti» (OBJ-42): elenco e verifica dei collegamenti."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, add_translations, icon, theme

from . import linkcheck as LC

add_translations("en", {
    "Collegamenti": "Links",
    "Pagina": "Page",
    "Tipo": "Type",
    "Destinazione": "Destination",
    "Descrizione": "Description",
    "Stato": "Status",
    "Aggiorna": "Refresh",
    "Verifica online…": "Check online…",
    "Esporta CSV…": "Export CSV…",
    "Nessun collegamento nel documento": "No links in the document",
    "Nessun documento aperto": "No document open",
    "Tutti": "All",
    "Solo problemi": "Problems only",
    "Verifica online dei collegamenti": "Online link check",
    "La verifica online contatta i siti collegati nel documento: i loro gestori potranno "
    "vedere il tuo indirizzo IP. Continuare?":
        "The online check contacts the sites linked in the document: their operators will be able "
        "to see your IP address. Continue?",
    "Verifica degli indirizzi in corso…": "Checking addresses…",
    "Esporta rapporto": "Export report",
    "File CSV (*.csv)": "CSV files (*.csv)",
    "Impossibile scrivere il file.": "Unable to write the file.",
    "web": "web", "interno": "internal", "file": "file", "altro": "other",
    "OK": "OK", "rotto": "broken", "sospetto": "suspicious", "non verificato": "not checked",
    "non verificabile": "not checkable", "reindirizzato": "redirected", "non trovato": "not found",
    "errore": "error", "tempo scaduto": "timed out",
    "{n} collegamenti, {p} con problemi": "{n} links, {p} with problems",
})

COLUMNS = ("Pagina", "Tipo", "Destinazione", "Descrizione", "Stato")
ROLE = Qt.ItemDataRole.UserRole


def ask_online_consent(parent) -> bool:
    """Chiede il consenso esplicito alla verifica online (sostituibile nei test)."""
    r = QMessageBox.question(
        parent, _("Verifica online dei collegamenti"),
        _("La verifica online contatta i siti collegati nel documento: i loro gestori potranno "
          "vedere il tuo indirizzo IP. Continuare?"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    return r == QMessageBox.StandardButton.Yes


def ask_csv_path(parent) -> str | None:
    """Percorso del rapporto CSV (sostituibile nei test)."""
    path, _f = QFileDialog.getSaveFileName(parent, _("Esporta rapporto"), "collegamenti.csv", _("File CSV (*.csv)"))
    if not path:
        return None
    if not path.lower().endswith(".csv"):
        path += ".csv"
    return path


def _status_color(status: str):
    if status in (LC.BROKEN, LC.NOT_FOUND, LC.ERROR):
        return theme.color("danger")
    if status in (LC.SUSPECT, LC.TIMEOUT, LC.REDIRECTED):
        return theme.color("warning")
    if status == LC.OK:
        return theme.color("success")
    return theme.color("text_muted")


class _NumItem(QTableWidgetItem):
    def __lt__(self, other):
        try:
            return int(self.data(ROLE + 1)) < int(other.data(ROLE + 1))
        except (TypeError, ValueError):
            return super().__lt__(other)


class LinkCheckPanel(QWidget):
    """Elenco dei collegamenti con stato; doppio clic → pagina del collegamento."""

    def __init__(self, ctx=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.view = None
        self.rows: list[LC.LinkRow] = []
        self.on_activate = None          # callback opzionale (page, xref)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QHBoxLayout()
        self.refresh_btn = QPushButton(icon("refresh-cw"), _("Aggiorna"))
        self.online_btn = QPushButton(icon("globe"), _("Verifica online…"))
        self.export_btn = QPushButton(icon("file-down"), _("Esporta CSV…"))
        self.filter = QComboBox()
        self.filter.addItem(_("Tutti"), "all")
        self.filter.addItem(_("Solo problemi"), "problems")
        for w in (self.refresh_btn, self.online_btn, self.export_btn):
            # solo icona: la barra laterale può essere stretta
            w.setToolTip(w.text())
            w.setAccessibleName(w.text())
            w.setText("")
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.filter)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([_(c) for c in COLUMNS])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.empty = QLabel(_("Nessun collegamento nel documento"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(self.summary)

        self.refresh_btn.clicked.connect(self.refresh)
        self.online_btn.clicked.connect(self.check_online)
        self.export_btn.clicked.connect(self.export_csv)
        self.filter.currentIndexChanged.connect(lambda _i: self._fill())
        self.table.cellDoubleClicked.connect(self._activated)
        self._fill()

    # ------------------------------------------------------------ dati
    def set_view(self, view) -> None:
        self.view = view
        self.refresh()

    def _doc_path(self):
        doc = getattr(self.view, "document", None)
        return getattr(doc, "path", None)

    def refresh(self) -> None:
        """Verifica locale (nessun accesso alla rete)."""
        doc = getattr(self.view, "document", None) if self.view is not None else None
        pdf = getattr(doc, "pdf", None)
        if pdf is None:
            self.rows = []
        else:
            with ENGINE_LOCK:
                self.rows = LC.check_document(pdf, self._doc_path())
        self._fill()

    def _visible_rows(self) -> list[LC.LinkRow]:
        if self.filter.currentData() == "problems":
            return [r for r in self.rows if r.status in LC.PROBLEMS]
        return list(self.rows)

    def _fill(self) -> None:
        has_doc = self.view is not None
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        rows = self._visible_rows()
        for r in rows:
            k = self.table.rowCount()
            self.table.insertRow(k)
            page = _NumItem(str(r.page + 1))
            page.setData(ROLE, (r.page, r.xref))
            page.setData(ROLE + 1, r.page)
            items = [page, QTableWidgetItem(_(r.kind)), QTableWidgetItem(r.destination),
                     QTableWidgetItem(r.description), QTableWidgetItem(_(r.status))]
            items[2].setToolTip(r.destination)
            if r.detail:
                items[4].setToolTip(r.detail)
            items[4].setForeground(_status_color(r.status))
            for c, it in enumerate(items):
                if c != 0:
                    it.setData(ROLE, (r.page, r.xref))
                self.table.setItem(k, c, it)
        self.table.setSortingEnabled(True)
        if not self.rows:
            self.empty.setText(_("Nessun collegamento nel documento") if has_doc else _("Nessun documento aperto"))
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.table)
        problems = sum(1 for r in self.rows if r.status in LC.PROBLEMS)
        self.summary.setText(_("{n} collegamenti, {p} con problemi").format(n=len(self.rows), p=problems)
                             if self.rows else "")
        web = any(r.kind == LC.WEB for r in self.rows)
        self.online_btn.setEnabled(web)
        self.export_btn.setEnabled(bool(self.rows))
        self.refresh_btn.setEnabled(has_doc)

    # ------------------------------------------------------------ azioni
    def _activated(self, row: int, _col: int) -> None:
        it = self.table.item(row, 0)
        if it is None:
            return
        page, xref = it.data(ROLE)
        if self.view is not None:
            self.view.go_to_page(page)
        if self.on_activate is not None:
            self.on_activate(page, xref)

    def check_online(self) -> bool:
        """Verifica online degli URL, solo dopo il consenso esplicito."""
        if not self.rows or not ask_online_consent(self):
            return False
        rows = self.rows

        def work(progress=None):
            def prog(done, total):
                if progress is not None:
                    progress.fraction = done / total if total else 1.0
            cancel = (lambda: progress.cancelled) if progress is not None else None
            return LC.check_urls_online(rows, cancel=cancel, on_progress=prog)

        if self.ctx is not None:
            self.ctx.run_task(_("Verifica degli indirizzi in corso…"), work)
        else:
            work()
        self._fill()
        return True

    def export_csv(self) -> str | None:
        if not self.rows:
            return None
        path = ask_csv_path(self)
        if not path:
            return None
        try:
            LC.write_csv(self.rows, path)
        except OSError as e:
            if self.ctx is not None:
                self.ctx.error(_("Esporta rapporto"), _("Impossibile scrivere il file.") + f"\n{e.strerror or e}")
            return None
        return path
