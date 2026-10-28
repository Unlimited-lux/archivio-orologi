"""Rapporto dell'analisi delle pagine (AI-24): miniature raggruppate per problema."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout,
)

from foliant.api import FoliantError, _, bind_icon, theme
from foliant.ui.tasks import run_task

from . import content, page_analysis
from .page_analysis import ApplyResult, Issue

THUMB = 64
ISSUE_ROLE = Qt.ItemDataRole.UserRole + 1


class PageAnalysisDialog(QDialog):
    """Mostra le voci del rapporto e applica quelle selezionate.

    `job_factory`: funzione senza argomenti che restituisce un `engine.Job` per la
    classificazione IA, oppure None se non c'è un profilo configurato.
    Dopo «Applica», `result` contiene l'`ApplyResult` (annotazioni estratte, pagine da OCR).
    """

    def __init__(self, document, issues: list[Issue], job_factory: Callable | None = None, parent=None):
        super().__init__(parent)
        self.document = document
        self.issues: list[Issue] = list(issues)
        self.job_factory = job_factory
        self.result: ApplyResult | None = None
        self._thumbs: dict[int, QIcon] = {}
        self._profile = None
        if job_factory is not None:
            try:
                self._profile = job_factory().profile()
            except Exception:  # noqa: BLE001 - profilo assente o non valido: solo controlli locali
                self._profile = None

        self.setWindowTitle(_("Analisi delle pagine"))
        self.resize(640, 560)
        lay = QVBoxLayout(self)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)

        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSpacing(2)
        self.list.itemChanged.connect(lambda _i: self._update_buttons())
        lay.addWidget(self.list, 1)

        self.empty = QLabel(_("Nessun problema trovato"))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color: {theme.hex_color('text_muted')}; font-size: 14px; padding: 24px;")
        lay.addWidget(self.empty, 1)

        ai_row = QHBoxLayout()
        self.classify_button = QPushButton(_("Classifica con IA"))
        bind_icon(self.classify_button, "sparkles")
        self.classify_button.clicked.connect(self.classify)
        ai_row.addWidget(self.classify_button)
        self.send_label = QLabel()
        self.send_label.setWordWrap(True)
        self.send_label.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        ai_row.addWidget(self.send_label, 1)
        lay.addLayout(ai_row)

        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(_("Applica"), QDialogButtonBox.ButtonRole.AcceptRole)
        bind_icon(self.apply_button, "check")
        close = buttons.addButton(_("Chiudi"), QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply_selected)
        lay.addWidget(buttons)

        self._rebuild()

    # ------------------------------------------------------------ contenuto
    def _thumbnail(self, page: int) -> QIcon:
        if page not in self._thumbs:
            pix = QPixmap()
            try:
                data, _w, _h = content.render_area(self.document.pdf, page, low_res=True, max_side=THUMB * 2)
                pix.loadFromData(data, "PNG")
            except Exception:  # noqa: BLE001 - miniatura non indispensabile
                pass
            self._thumbs[page] = QIcon(pix) if not pix.isNull() else QIcon()
        return self._thumbs[page]

    def candidate_pages(self) -> list[int]:
        return page_analysis.ai_candidates(self.document.pdf, self.issues)

    def _rebuild(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        groups: dict[str, list[Issue]] = {}
        for it in self.issues:
            groups.setdefault(it.problem, []).append(it)
        bold = QFont(self.list.font())
        bold.setBold(True)
        for problem, items in groups.items():
            header = QListWidgetItem(f"{page_analysis.problem_title(problem)} ({len(items)})")
            header.setFlags(Qt.ItemFlag.NoItemFlags | Qt.ItemFlag.ItemIsEnabled)
            header.setFont(bold)
            self.list.addItem(header)
            for it in sorted(items, key=lambda x: x.page):
                origin = _("IA") if it.origin == page_analysis.AI else _("locale")
                text = _("Pagina {n} — {e}\nAzione proposta: {a} · origine: {o}").format(
                    n=it.page + 1, e=it.evidence, a=page_analysis.action_title(it.action), o=origin)
                item = QListWidgetItem(self._thumbnail(it.page), text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if it.auto_select else Qt.CheckState.Unchecked)
                item.setData(ISSUE_ROLE, it)
                self.list.addItem(item)
        self.list.blockSignals(False)
        has = bool(self.issues)
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            pages = len({it.page for it in self.issues})
            self.summary.setText(_("{n} segnalazioni su {p} pagine. Seleziona le correzioni da applicare: "
                                   "si annullano tutte insieme con un solo comando.").format(n=len(self.issues), p=pages))
        else:
            self.summary.setText("")
        self._update_send_line()
        self._update_buttons()

    def _update_send_line(self) -> None:
        if self._profile is None:
            self.classify_button.setEnabled(False)
            self.send_label.setText(_("Profilo IA non configurato: sono disponibili solo i controlli locali."))
            return
        pages = self.candidate_pages()
        self.classify_button.setEnabled(bool(pages))
        try:
            self.send_label.setText(page_analysis.describe_ai_send(self.document.pdf, pages, self._profile))
        except Exception as e:  # noqa: BLE001
            self.send_label.setText(str(e))

    def _update_buttons(self) -> None:
        self.apply_button.setEnabled(bool(self.selected_issues()))

    def selected_issues(self) -> list[Issue]:
        out = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            it = item.data(ISSUE_ROLE)
            if it is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(it)
        return out

    # ------------------------------------------------------------ azioni
    def classify(self) -> None:
        if self.job_factory is None:
            return
        pages = self.candidate_pages()
        if not pages:
            return
        pdf = self.document.pdf

        def work(progress):
            job = self.job_factory()
            job.progress = lambda f, t: progress.update(f, t)
            return page_analysis.classify_with_ai(job, pdf, pages)

        try:
            found = run_task(self, _("Classificazione delle pagine"), work)
        except Exception as e:  # noqa: BLE001 - errori del fornitore o del contenuto
            QMessageBox.warning(self, _("Classificazione non riuscita"), str(e))
            return
        self.issues = [it for it in self.issues if it.origin != page_analysis.AI] + list(found or [])
        self._rebuild()
        if not found:
            self.summary.setText(self.summary.text() + " " + _("L'IA non ha segnalato altre pagine."))

    def apply_selected(self) -> None:
        chosen = self.selected_issues()
        if not chosen:
            return
        try:
            self.result = page_analysis.apply(self.document, chosen)
        except FoliantError as e:
            QMessageBox.warning(self, _("Pulizia non riuscita"), str(e))
            return
        self.accept()
