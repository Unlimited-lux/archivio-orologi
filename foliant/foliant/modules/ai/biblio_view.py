"""Dialogo della ricerca bibliografica e revisione della letteratura (AI-26).

Il dialogo non fa rete né chiamate al modello direttamente: tutto passa da
`run(titolo, fn)`, che esegue `fn(job)` in un'attività con avanzamento e
restituisce il risultato (None se annullata o fallita: l'errore lo mostra chi chiama).
Le ricerche nel servizio bibliografico inviano solo le parole chiave (vedi `biblio`).
"""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from foliant.api import FoliantError, _, atomic_write, bind_icon, theme

from . import biblio
from .biblio import Entry, Library

ROLE_ENTRY = Qt.UserRole + 1

# Traduzioni inglesi, da registrare con add_translations("en", ...) in register().
# Traduzioni inglesi in translations.py (catalogo unico del modulo).


class BiblioDialog(QDialog):
    """Domanda → sotto-domande → ricerca → libreria → revisione → esportazioni.

    - `run(titolo, fn)`: esegue `fn(job)` con avanzamento; restituisce il risultato o None.
    - `service`: servizio bibliografico, o None (funzione disattivata con spiegazione).
    - `ask_save_path(titolo, nome, filtro, suffisso) -> str | None` (di norma `ctx.ask_save_path`).
    - `open_url(url)`: apertura di un collegamento (la conferma è di chi chiama).
    - `store`: se presente, mostra la scelta del servizio e la salva con `store.set_option`.
    - `run_search(titolo, fn)`: facoltativo, per le ricerche che non usano il modello: esegue `fn()`.
    """

    def __init__(self, run: Callable, service, ask_save_path: Callable[..., str | None],
                 open_url: Callable[[str], None], parent=None, *, store=None, library: Library | None = None,
                 run_search: Callable | None = None, on_error: Callable[[str, str], None] | None = None,
                 question: str = ""):
        super().__init__(parent)
        self.run = run
        self.run_search = run_search or (lambda title, fn: run(title, lambda job: fn()))
        self.service = service
        self.ask_save_path = ask_save_path
        self.open_url = open_url
        self.store = store
        self.library = library if library is not None else Library()
        self.on_error = on_error
        self.results: list[Entry] = []
        self.summaries: dict[str, str] = {}
        self.review_entries: list[Entry] = []
        self.setWindowTitle(_("Ricerca bibliografica"))
        self.resize(980, 680)

        lay = QVBoxLayout(self)

        # ---------------------------------------------------------- servizio
        srow = QHBoxLayout()
        srow.addWidget(QLabel(_("Servizio:")))
        self.service_combo = QComboBox()
        self.service_combo.addItem(_("Nessuno"), "")
        self.service_combo.addItem("OpenAlex", "openalex")
        self.service_combo.addItem("Crossref", "crossref")
        srow.addWidget(self.service_combo)
        srow.addWidget(QLabel(_("Indirizzo (facoltativo):")))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://")
        srow.addWidget(self.url_edit, 1)
        self.apply_button = QPushButton(_("Applica"))
        bind_icon(self.apply_button, "settings")
        srow.addWidget(self.apply_button)
        self.service_row = QWidget()
        self.service_row.setLayout(srow)
        srow.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.service_row)
        self.service_row.setVisible(store is not None)
        if store is not None:
            kind = str(store.option("biblio_service", "") or "")
            i = self.service_combo.findData(kind)
            self.service_combo.setCurrentIndex(max(0, i))
            self.url_edit.setText(str(store.option("biblio_url", "") or ""))

        self.disabled_label = QLabel(
            "<b>" + html.escape(_("Nessun servizio bibliografico configurato")) + "</b><br>" + html.escape(
                _("Scegli un servizio bibliografico qui sopra per attivare la funzione. Al servizio vengono "
                  "inviate solo le parole chiave delle sotto-domande.")))
        self.disabled_label.setWordWrap(True)
        self.disabled_label.setStyleSheet(f"color: {theme.color('warning').name()};")
        lay.addWidget(self.disabled_label)

        # ---------------------------------------------------------- schede
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # Ricerca
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addWidget(QLabel(_("Domanda di ricerca:")))
        self.question = QPlainTextEdit(question)
        self.question.setMaximumHeight(90)
        pl.addWidget(self.question)
        self.propose_button = QPushButton(_("Proponi sotto-domande"))
        bind_icon(self.propose_button, "sparkles")
        row = QHBoxLayout()
        row.addWidget(self.propose_button)
        row.addStretch(1)
        pl.addLayout(row)
        pl.addWidget(QLabel(_("Sotto-domande (modificabili):")))
        self.subquestions = QListWidget()
        pl.addWidget(self.subquestions, 1)
        row = QHBoxLayout()
        self.add_sq_button = QPushButton(_("Aggiungi"))
        bind_icon(self.add_sq_button, "plus")
        self.remove_sq_button = QPushButton(_("Rimuovi"))
        bind_icon(self.remove_sq_button, "trash-2")
        self.search_button = QPushButton(_("Cerca"))
        bind_icon(self.search_button, "search")
        row.addWidget(self.add_sq_button)
        row.addWidget(self.remove_sq_button)
        row.addStretch(1)
        row.addWidget(self.search_button)
        pl.addLayout(row)
        self.tabs.addTab(page, _("Ricerca"))

        # Risultati
        page = QWidget()
        pl = QVBoxLayout(page)
        self.results_info = QLabel()
        self.results_info.setWordWrap(True)
        pl.addWidget(self.results_info)
        split = QSplitter(Qt.Horizontal)
        self.results_list = QListWidget()
        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.setOpenExternalLinks(False)
        self.detail.anchorClicked.connect(lambda url: self._open(url.toString()))
        split.addWidget(self.results_list)
        split.addWidget(self.detail)
        split.setSizes([380, 560])
        pl.addWidget(split, 1)
        row = QHBoxLayout()
        self.summary_button = QPushButton(_("Riassumi"))
        self.summary_button.setToolTip(_("Riassumi la voce selezionata"))
        bind_icon(self.summary_button, "sparkles")
        self.add_lib_button = QPushButton(_("Aggiungi alla libreria"))
        bind_icon(self.add_lib_button, "book-plus")
        self.open_oa_button = QPushButton(_("Apri accesso aperto"))
        bind_icon(self.open_oa_button, "external-link")
        self.open_page_button = QPushButton(_("Apri pagina"))
        bind_icon(self.open_page_button, "external-link")
        for b in (self.summary_button, self.add_lib_button, self.open_oa_button, self.open_page_button):
            row.addWidget(b)
        row.addStretch(1)
        pl.addLayout(row)
        self.tabs.addTab(page, _("Risultati"))

        # Libreria
        page = QWidget()
        pl = QVBoxLayout(page)
        self.library_list = QListWidget()
        pl.addWidget(self.library_list, 1)
        row = QHBoxLayout()
        self.remove_lib_button = QPushButton(_("Rimuovi"))
        bind_icon(self.remove_lib_button, "trash-2")
        row.addWidget(self.remove_lib_button)
        row.addStretch(1)
        pl.addLayout(row)
        self.tabs.addTab(page, _("Libreria"))

        # Revisione
        page = QWidget()
        pl = QVBoxLayout(page)
        row = QHBoxLayout()
        self.review_button = QPushButton(_("Genera revisione"))
        bind_icon(self.review_button, "notebook-pen")
        row.addWidget(self.review_button)
        row.addStretch(1)
        pl.addLayout(row)
        self.review_text = QPlainTextEdit()
        pl.addWidget(self.review_text, 1)
        row = QHBoxLayout()
        self.pdf_button = QPushButton(_("Esporta PDF…"))
        bind_icon(self.pdf_button, "file-text")
        self.bibtex_button = QPushButton(_("Esporta BibTeX…"))
        bind_icon(self.bibtex_button, "download")
        self.ris_button = QPushButton(_("Esporta RIS…"))
        bind_icon(self.ris_button, "download")
        for b in (self.pdf_button, self.bibtex_button, self.ris_button):
            row.addWidget(b)
        row.addStretch(1)
        pl.addLayout(row)
        self.tabs.addTab(page, _("Revisione"))

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText(_("Chiudi"))
        close.rejected.connect(self.reject)
        lay.addWidget(close)

        # ---------------------------------------------------------- segnali
        self.apply_button.clicked.connect(self.apply_service)
        self.propose_button.clicked.connect(self.propose)
        self.add_sq_button.clicked.connect(lambda: self.add_subquestion(_("Nuova sotto-domanda"), edit=True))
        self.remove_sq_button.clicked.connect(self.remove_subquestion)
        self.search_button.clicked.connect(self.search)
        self.results_list.currentRowChanged.connect(lambda _r: self._show_detail())
        self.summary_button.clicked.connect(self.summarize_current)
        self.add_lib_button.clicked.connect(self.add_current_to_library)
        self.open_oa_button.clicked.connect(lambda: self._open_current("oa_url"))
        self.open_page_button.clicked.connect(lambda: self._open_current("url"))
        self.remove_lib_button.clicked.connect(self.remove_from_library)
        self.review_button.clicked.connect(self.generate_review)
        self.pdf_button.clicked.connect(self.export_pdf)
        self.bibtex_button.clicked.connect(self.export_bibtex)
        self.ris_button.clicked.connect(self.export_ris)
        self.subquestions.itemChanged.connect(lambda _i: self._update_state())
        self.review_text.textChanged.connect(self._update_state)
        self.fill_library()
        self._update_state()

    # ------------------------------------------------------------------ stato
    @property
    def enabled(self) -> bool:
        return self.service is not None

    def _update_state(self) -> None:
        on = self.enabled
        self.disabled_label.setVisible(not on)
        self.tabs.setEnabled(on)
        entry = self.current_entry()
        self.search_button.setEnabled(on and bool(self.subquestion_texts()))
        self.remove_sq_button.setEnabled(self.subquestions.currentRow() >= 0)
        self.summary_button.setEnabled(entry is not None and bool(entry.abstract))
        self.add_lib_button.setEnabled(entry is not None)
        self.open_oa_button.setEnabled(entry is not None and bool(entry.oa_url))
        self.open_page_button.setEnabled(entry is not None and bool(entry.url))
        self.review_button.setEnabled(on and len(self.library) > 0)
        has_review = bool(self.review_text.toPlainText().strip())
        self.pdf_button.setEnabled(has_review)
        exportable = bool(self.review_entries or len(self.library))
        self.bibtex_button.setEnabled(exportable)
        self.ris_button.setEnabled(exportable)
        self.remove_lib_button.setEnabled(self.library_list.currentRow() >= 0)

    def _error(self, title: str, text: str) -> None:
        if self.on_error:
            self.on_error(title, text)
        else:
            QMessageBox.warning(self, title, text)

    def apply_service(self) -> None:
        if self.store is None:
            return
        kind = self.service_combo.currentData() or ""
        url = self.url_edit.text().strip()
        biblio.save_service_settings(self.store, kind, url)
        self.service = biblio.service_from_settings(self.store)
        if kind and self.service is None:
            self._error(_("Ricerca bibliografica non disponibile"),
                        _("Indirizzo del servizio non valido: usa https:// (http:// solo per indirizzi locali)."))
        self._update_state()

    # ------------------------------------------------------------------ sotto-domande
    def subquestion_texts(self) -> list[str]:
        out = []
        for i in range(self.subquestions.count()):
            t = self.subquestions.item(i).text().strip()
            if t:
                out.append(t)
        return out

    def add_subquestion(self, text: str, edit: bool = False) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.subquestions.addItem(item)
        if edit:
            self.subquestions.setCurrentItem(item)
            self.subquestions.editItem(item)
        self._update_state()
        return item

    def set_subquestions(self, items: list[str]) -> None:
        self.subquestions.clear()
        for t in items:
            self.add_subquestion(t)
        self._update_state()

    def remove_subquestion(self) -> None:
        row = self.subquestions.currentRow()
        if row >= 0:
            self.subquestions.takeItem(row)
        self._update_state()

    def propose(self) -> None:
        q = self.question.toPlainText().strip()
        if not q:
            self.question.setFocus()
            return
        res = self.run(_("Proposta delle sotto-domande"), lambda job: biblio.propose_subquestions(job, q))
        if res:
            self.set_subquestions(res)

    # ------------------------------------------------------------------ ricerca
    def search(self) -> None:
        if not self.enabled:
            return
        qs = self.subquestion_texts()
        if not qs:
            q = self.question.toPlainText().strip()
            qs = [q] if q else []
        if not qs:
            return
        service = self.service
        res = self.run_search(_("Ricerca delle pubblicazioni"), lambda: biblio.search_all(service, qs))
        if res is None:
            return
        entries, _counts = res
        self.set_results(entries)
        self.tabs.setCurrentIndex(1)

    def set_results(self, entries: list[Entry]) -> None:
        self.results = list(entries)
        self.results_list.clear()
        for e in self.results:
            label = e.title or _("Senza titolo")
            sub = ", ".join(x for x in (e.authors_text(2), str(e.year or "")) if x)
            item = QListWidgetItem(f"{label}\n{sub}" if sub else label)
            item.setData(ROLE_ENTRY, e)
            item.setToolTip(e.reference())
            self.results_list.addItem(item)
        self.results_info.setText(_("Nessun risultato: prova a riformulare le sotto-domande.") if not entries
                                  else _("{n} risultati.").format(n=len(entries)))
        if entries:
            self.results_list.setCurrentRow(0)
        self._show_detail()

    def current_entry(self) -> Entry | None:
        item = self.results_list.currentItem()
        return item.data(ROLE_ENTRY) if item is not None else None

    def _show_detail(self) -> None:
        e = self.current_entry()
        if e is None:
            self.detail.clear()
            self._update_state()
            return
        esc = html.escape
        muted = theme.color("text_muted").name()
        rows = [f"<h3>{esc(e.title or _('Senza titolo'))}</h3>"]

        def field(label: str, value: str, link: str = "") -> None:
            if not value:
                return
            v = f'<a href="{esc(link, quote=True)}">{esc(value)}</a>' if link else esc(value)
            rows.append(f'<p><span style="color:{muted}">{esc(label)}:</span> {v}</p>')

        field(_("Autori"), e.authors_text())
        field(_("Anno"), str(e.year or ""))
        field(_("Sede"), e.venue)
        field(_("Identificativo"), e.identifier, e.url)
        field(_("Accesso aperto"), e.oa_url, e.oa_url)
        if e.abstract:
            rows.append(f"<h4>{esc(_('Abstract'))}</h4><p>{esc(e.abstract)}</p>")
        summary = self.summaries.get(e.key())
        if summary:
            rows.append(f"<h4>{esc(_('Riassunto'))}</h4><p>{esc(summary)}</p>")
        self.detail.setHtml("".join(rows))
        self._update_state()

    def summarize_current(self) -> None:
        e = self.current_entry()
        if e is None:
            return
        res = self.run(_("Riassunto della pubblicazione"), lambda job: biblio.summarize_entry(job, e))
        if res:
            self.summaries[e.key()] = res
            self._show_detail()

    def _open(self, url: str) -> None:
        if url:
            self.open_url(url)

    def _open_current(self, attr: str) -> None:
        e = self.current_entry()
        if e is not None:
            self._open(getattr(e, attr))

    # ------------------------------------------------------------------ libreria
    def fill_library(self) -> None:
        self.library_list.clear()
        for e in self.library.list():
            item = QListWidgetItem(e.reference())
            item.setData(ROLE_ENTRY, e)
            self.library_list.addItem(item)
        self._update_state()

    def add_current_to_library(self) -> None:
        e = self.current_entry()
        if e is None:
            return
        added = self.library.add(e)
        self.results_info.setText(_("Aggiunta alla libreria.") if added else _("Già presente nella libreria."))
        self.fill_library()

    def remove_from_library(self) -> None:
        item = self.library_list.currentItem()
        if item is None:
            return
        self.library.remove(item.data(ROLE_ENTRY))
        self.fill_library()

    # ------------------------------------------------------------------ revisione
    def generate_review(self) -> None:
        entries = self.library.list()
        if not entries:
            self._error(_("Genera revisione"), _("La libreria è vuota: aggiungi almeno una pubblicazione."))
            return
        q = self.question.toPlainText().strip() or "; ".join(self.subquestion_texts())
        library = self.library
        res = self.run(_("Generazione della revisione"),
                       lambda job: biblio.generate_review(job, q, entries, library))
        if res:
            text, used = res
            self.review_entries = used
            self.review_text.setPlainText(text)
            self.tabs.setCurrentIndex(3)
        self._update_state()

    def export_entries(self) -> list[Entry]:
        return self.review_entries or self.library.list()

    def _save(self, title: str, name: str, suffix: str, file_filter: str, data: bytes) -> str | None:
        path = self.ask_save_path(title, name + suffix, file_filter, suffix)
        if not path:
            return None
        try:
            atomic_write(path, data)
        except (OSError, FoliantError) as e:
            self._error(_("Esportazione non riuscita"), str(e))
            return None
        return path

    def export_pdf(self) -> str | None:
        text = self.review_text.toPlainText()
        if not text.strip():
            return None
        try:
            data = biblio.review_pdf(text, self.export_entries())
        except Exception as e:  # noqa: BLE001
            self._error(_("Esportazione non riuscita"), str(e))
            return None
        return self._save(_("Esporta PDF…"), _("revisione"), ".pdf", _("Documenti PDF (*.pdf)"), data)

    def export_bibtex(self) -> str | None:
        return self._save(_("Esporta BibTeX…"), _("bibliografia"), ".bib", _("File BibTeX (*.bib)"),
                          biblio.to_bibtex(self.export_entries()).encode("utf-8"))

    def export_ris(self) -> str | None:
        return self._save(_("Esporta RIS…"), _("bibliografia"), ".ris", _("File RIS (*.ris)"),
                          biblio.to_ris(self.export_entries()).encode("utf-8"))
