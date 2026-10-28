"""Dialogo «Trova e sostituisci» / «Trova ed elimina» (TXT-12, TXT-13).

Non modale: resta aperto mentre si naviga nel documento. La logica è in
`search.py`; qui solo interfaccia, evidenziazioni e navigazione.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from foliant.api import FoliantError, _, icon, theme

from .search import (
    Match, ReplaceSummary, SearchError, SearchOptions, check_replacement, compile_pattern, find_in_document,
    match_key, parse_page_ranges, replace_all, replace_one,
)

SCOPE_DOCUMENT, SCOPE_PAGE, SCOPE_RANGE = 0, 1, 2
MODE_REPLACE, MODE_DELETE = 0, 1
MAX_SUMMARY_ITEMS = 8

TRANSLATIONS = {
    # dialogo
    "Trova e sostituisci": "Find and replace",
    "Trova ed elimina": "Find and delete",
    "Trova:": "Find:",
    "Sostituisci con:": "Replace with:",
    "Maiuscole/minuscole": "Match case",
    "Parola intera": "Whole word",
    "Ignora accenti": "Ignore accents",
    "Espressione regolare": "Regular expression",
    "Ambito:": "Scope:",
    "Documento": "Document",
    "Pagina corrente": "Current page",
    "Intervallo di pagine": "Page range",
    "es. 1-3, 5": "e.g. 1-3, 5",
    "Sostituisci": "Replace",
    "Elimina": "Delete",
    "Trova": "Find",
    "Precedente": "Previous",
    "Successivo": "Next",
    "Sostituisci tutto": "Replace all",
    "Elimina tutto": "Delete all",
    "Rimuovi anche uno spazio adiacente": "Also remove an adjacent space",
    "Ricomponi il paragrafo": "Reflow the paragraph",
    "Se disattivata, il testo restante non si sposta e resta un vuoto.":
        "When off, the remaining text does not move and a gap is left.",
    "L'occorrenza non copre parole intere.": "The occurrence does not cover whole words.",
    "Chiudi": "Close",
    "Nell'espressione regolare usa $1, $2… o ${nome} per inserire i gruppi trovati.":
        "In a regular expression use $1, $2… or ${name} to insert the captured groups.",
    "La ricerca riguarda il testo delle pagine: annotazioni, campi modulo e segnalibri sono esclusi.":
        "The search covers page text: annotations, form fields and bookmarks are excluded.",
    "L'eliminazione non agisce su immagini e grafica e, con il salvataggio incrementale, il testo resta "
    "recuperabile dalle revisioni precedenti. Per i dati riservati usa la Redazione e il salvataggio completo.":
        "Deletion does not affect images and graphics and, with an incremental save, the text can still be "
        "recovered from previous revisions. For confidential data use Redaction and a full save.",
    "Nessun documento aperto.": "No document open.",
    "Nessuna occorrenza trovata.": "No occurrences found.",
    "{n} occorrenze trovate.": "{n} occurrences found.",
    "1 occorrenza trovata.": "1 occurrence found.",
    "{n} non sostituibili.": "{n} not replaceable.",
    "p. {p} — {c}": "p. {p} — {c}",
    " (non sostituibile: {r})": " (not replaceable: {r})",
    "Occorrenza {i} di {n}": "Occurrence {i} of {n}",
    "Nessuna occorrenza da sostituire.": "No occurrences to replace.",
    "Nessuna occorrenza selezionata da eliminare.": "No occurrences selected for deletion.",
    "Sostituire {n} occorrenze con «{t}»?": "Replace {n} occurrences with «{t}»?",
    "Sostituire 1 occorrenza con «{t}»?": "Replace 1 occurrence with «{t}»?",
    "Eliminare {n} occorrenze?": "Delete {n} occurrences?",
    "Eliminare 1 occorrenza?": "Delete 1 occurrence?",
    " L'operazione si annulla con un solo Annulla.": " A single Undo reverts the operation.",
    "Occorrenze sostituite: {n}.": "Occurrences replaced: {n}.",
    "Occorrenze eliminate: {n}.": "Occurrences deleted: {n}.",
    "Non sostituibili: {n}.": "Not replaceable: {n}.",
    "p. {p} «{t}»: {r}": "p. {p} «{t}»: {r}",
    "… e altre {n}.": "… and {n} more.",
    "Font sostitutivi usati:": "Substitute fonts used:",
    "{a} → {b} ({n} caratteri)": "{a} → {b} ({n} characters)",
    "Operazione non riuscita: {e}": "Operation failed: {e}",
    # search.py
    "Inserisci il testo da cercare.": "Enter the text to find.",
    "Espressione regolare non valida: {err}.": "Invalid regular expression: {err}.",
    "Espressione regolare troppo complessa.": "Regular expression too complex.",
    "{msg} (posizione {pos})": "{msg} (position {pos})",
    "errore di sintassi": "syntax error",
    "parentesi «(» non chiusa": "unclosed «(»",
    "parentesi «)» senza apertura": "unbalanced «)»",
    "classe di caratteri «[» non chiusa": "unclosed character class «[»",
    "quantificatore senza nulla da ripetere": "nothing to repeat",
    "quantificatori ripetuti": "multiple repeat",
    "«\\» alla fine dell'espressione": "«\\» at end of expression",
    "sequenza di escape non valida": "invalid escape sequence",
    "intervallo di caratteri non valido": "invalid character range",
    "nome di gruppo sconosciuto": "unknown group name",
    "riferimento a gruppo non valido": "invalid group reference",
    "nome di gruppo ripetuto": "duplicate group name",
    "nome di gruppo mancante": "missing group name",
    "nome di gruppo non valido": "invalid group name",
    "il «lookbehind» richiede una lunghezza fissa": "lookbehind requires a fixed width",
    "estensione «(?» sconosciuta": "unknown «(?» extension",
    "nome di gruppo non chiuso": "unterminated group name",
    "«:» mancante": "missing «:»",
    "commento non chiuso": "unterminated comment",
    "Riferimento a gruppo vuoto nel testo sostitutivo: «${}».": "Empty group reference in the replacement: «${}».",
    "Il testo sostitutivo fa riferimento al gruppo «{g}», che non esiste nell'espressione.":
        "The replacement refers to group «{g}», which does not exist in the expression.",
    "blocco non modificabile: {r}": "block not editable: {r}",
    "L'occorrenza non corrisponde più al testo del blocco.": "The occurrence no longer matches the block text.",
    "Trova ed elimina ({n})": "Find and delete ({n})",
    "Sostituisci tutto ({n})": "Replace all ({n})",
    "L'occorrenza non esiste più.": "The occurrence no longer exists.",
    "Questa occorrenza non può essere sostituita: {r}.": "This occurrence cannot be replaced: {r}.",
    "Elimina «{t}»": "Delete «{t}»",
    "Sostituisci «{t}»": "Replace «{t}»",
    "Indica le pagine, ad esempio «1-3, 5».": "Enter the pages, for example «1-3, 5».",
    "Intervallo di pagine non valido: «{p}».": "Invalid page range: «{p}».",
    "Le pagine vanno da 1 a {n}: «{p}» non è valido.": "Pages go from 1 to {n}: «{p}» is not valid.",
}


class FindReplaceDialog(QDialog):
    """Trova e sostituisci / Trova ed elimina nel testo di pagina del documento attivo."""

    def __init__(self, ctx, parent: QWidget | None = None, mode: int = MODE_REPLACE):
        super().__init__(parent)
        self.ctx = ctx
        self.setModal(False)
        self.setWindowTitle(_("Trova e sostituisci"))
        self.setWindowIcon(icon("search"))
        self.matches: list[Match] = []
        self.current = -1
        self._stamp: tuple | None = None        # (documento, versione, ricerca) dei risultati
        self._hits_view = None
        self._hit_offsets: list[int] = []
        self._build()
        self.set_mode(mode)
        self._validate()

    # ------------------------------------------------------------------ interfaccia
    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.tabs = QTabBar(self)
        self.tabs.addTab(icon("replace"), _("Sostituisci"))
        self.tabs.addTab(icon("trash-2"), _("Elimina"))
        root.addWidget(self.tabs)

        form = QFormLayout()
        self.find_edit = QLineEdit(self)
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.returnPressed.connect(self.on_find)
        form.addRow(_("Trova:"), self.find_edit)
        self.replace_label = QLabel(_("Sostituisci con:"), self)
        self.replace_edit = QLineEdit(self)
        self.replace_edit.setClearButtonEnabled(True)
        form.addRow(self.replace_label, self.replace_edit)
        root.addLayout(form)

        opts = QHBoxLayout()
        self.case_box = QCheckBox(_("Maiuscole/minuscole"), self)
        self.word_box = QCheckBox(_("Parola intera"), self)
        self.accent_box = QCheckBox(_("Ignora accenti"), self)
        self.regex_box = QCheckBox(_("Espressione regolare"), self)
        for box in (self.case_box, self.word_box, self.accent_box, self.regex_box):
            opts.addWidget(box)
            box.toggled.connect(self._options_changed)
        opts.addStretch(1)
        root.addLayout(opts)

        scope = QHBoxLayout()
        scope.addWidget(QLabel(_("Ambito:"), self))
        self.scope_combo = QComboBox(self)
        self.scope_combo.addItems([_("Documento"), _("Pagina corrente"), _("Intervallo di pagine")])
        self.scope_combo.currentIndexChanged.connect(self._options_changed)
        scope.addWidget(self.scope_combo)
        self.range_edit = QLineEdit(self)
        self.range_edit.setPlaceholderText(_("es. 1-3, 5"))
        self.range_edit.textChanged.connect(self._options_changed)
        scope.addWidget(self.range_edit, 1)
        root.addLayout(scope)

        self.space_box = QCheckBox(_("Rimuovi anche uno spazio adiacente"), self)
        self.space_box.setChecked(True)
        root.addWidget(self.space_box)
        self.recompose_box = QCheckBox(_("Ricomponi il paragrafo"), self)
        self.recompose_box.setChecked(True)
        self.recompose_box.setToolTip(_("Se disattivata, il testo restante non si sposta e resta un vuoto."))
        root.addWidget(self.recompose_box)

        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error_label.hide()
        root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.find_btn = self._button(_("Trova"), "search", self.on_find, buttons)
        self.find_btn.setDefault(True)
        self.prev_btn = self._button(_("Precedente"), "chevron-up", self.on_previous, buttons)
        self.next_btn = self._button(_("Successivo"), "chevron-down", self.on_next, buttons)
        buttons.addStretch(1)
        self.replace_btn = self._button(_("Sostituisci"), "replace", self.on_replace, buttons)
        self.replace_all_btn = self._button(_("Sostituisci tutto"), "replace-all", self.on_replace_all, buttons)
        self.delete_all_btn = self._button(_("Elimina tutto"), "trash-2", self.on_delete_all, buttons)
        root.addLayout(buttons)

        self.results = QListWidget(self)
        self.results.setMinimumHeight(160)
        self.results.currentRowChanged.connect(self._row_changed)
        self.results.itemActivated.connect(lambda item: self._row_changed(self.results.row(item)))
        root.addWidget(self.results, 1)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_label.hide()
        root.addWidget(self.summary_label)

        self.regex_note = QLabel(_("Nell'espressione regolare usa $1, $2… o ${nome} per inserire i gruppi trovati."),
                                 self)
        self.regex_note.setWordWrap(True)
        root.addWidget(self.regex_note)
        self.delete_note = QLabel(_(
            "L'eliminazione non agisce su immagini e grafica e, con il salvataggio incrementale, il testo resta "
            "recuperabile dalle revisioni precedenti. Per i dati riservati usa la Redazione e il salvataggio "
            "completo."),
            self)
        self.delete_note.setWordWrap(True)
        root.addWidget(self.delete_note)
        note = QLabel(_("La ricerca riguarda il testo delle pagine: annotazioni, campi modulo e segnalibri sono "
                        "esclusi."), self)
        note.setWordWrap(True)
        root.addWidget(note)
        for label in (self.regex_note, self.delete_note, note):
            label.setEnabled(False)     # testo attenuato dal tema

        close = QHBoxLayout()
        close.addStretch(1)
        self.close_btn = QPushButton(_("Chiudi"), self)
        self.close_btn.setAutoDefault(False)
        self.close_btn.clicked.connect(self.close)
        close.addWidget(self.close_btn)
        root.addLayout(close)

        self.find_edit.textChanged.connect(self._options_changed)
        self.replace_edit.textChanged.connect(self._validate)
        self.tabs.currentChanged.connect(self.set_mode)
        self.resize(620, 560)

    def _button(self, text: str, icon_name: str, slot, layout: QHBoxLayout) -> QPushButton:
        b = QPushButton(icon(icon_name), text, self)
        b.setAutoDefault(False)
        b.clicked.connect(lambda _checked=False: self._guard(slot))
        layout.addWidget(b)
        return b

    @property
    def mode(self) -> int:
        return self.tabs.currentIndex()

    def set_mode(self, mode: int) -> None:
        if self.tabs.currentIndex() != mode:
            self.tabs.setCurrentIndex(mode)      # richiama set_mode tramite il segnale
            return
        deleting = mode == MODE_DELETE
        self.setWindowTitle(_("Trova ed elimina") if deleting else _("Trova e sostituisci"))
        for w in (self.replace_label, self.replace_edit, self.replace_btn, self.replace_all_btn):
            w.setVisible(not deleting)
        for w in (self.delete_all_btn, self.space_box, self.recompose_box, self.delete_note):
            w.setVisible(deleting)
        self._fill_results()
        self._validate()

    # ------------------------------------------------------------------ stato
    def options(self) -> SearchOptions:
        return SearchOptions(case_sensitive=self.case_box.isChecked(), whole_word=self.word_box.isChecked(),
                             ignore_accents=self.accent_box.isChecked(), regex=self.regex_box.isChecked())

    def _view(self):
        return self.ctx.current_view()

    def _pages(self, view) -> list[int] | None:
        scope = self.scope_combo.currentIndex()
        if scope == SCOPE_PAGE:
            return [view.current_page]
        if scope == SCOPE_RANGE:
            return parse_page_ranges(self.range_edit.text(), view.document.page_count)
        return None

    def _query(self) -> tuple:
        o = self.options()
        return (self.find_edit.text(), o.case_sensitive, o.whole_word, o.ignore_accents, o.regex,
                self.scope_combo.currentIndex(), self.range_edit.text())

    def _fresh(self, view) -> bool:
        return (view is not None and self._stamp is not None
                and self._stamp == (id(view.document), view.document.version, self._query()))

    def _options_changed(self, *_args) -> None:
        self.range_edit.setEnabled(self.scope_combo.currentIndex() == SCOPE_RANGE)
        if self.matches:
            self._clear_results()
        self._validate()

    def _validate(self, *_args) -> bool:
        """Controlla ricerca, sostitutivo e ambito; mostra l'errore in linea e (dis)attiva i pulsanti."""
        error = ""
        pattern = self.find_edit.text()
        if pattern:
            try:
                compile_pattern(pattern, self.options())
                if self.mode == MODE_REPLACE:
                    check_replacement(pattern, self.replace_edit.text(), self.options())
                view = self._view()
                if view is not None and self.scope_combo.currentIndex() == SCOPE_RANGE:
                    parse_page_ranges(self.range_edit.text(), view.document.page_count)
            except SearchError as e:
                error = str(e)
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))
        ok = bool(pattern) and not error and self._view() is not None
        for b in (self.find_btn, self.prev_btn, self.next_btn, self.replace_btn, self.replace_all_btn,
                  self.delete_all_btn):
            b.setEnabled(ok)
        return ok

    def _guard(self, slot) -> None:
        """Esegue un'azione senza mai far cadere l'applicazione."""
        try:
            slot()
        except SearchError as e:
            self.error_label.setText(str(e))
            self.error_label.show()
        except FoliantError as e:
            self.ctx.error(self.windowTitle(), str(e))
        except Exception as e:  # noqa: BLE001 - un errore del modulo non deve chiudere l'applicazione
            self.ctx.error(self.windowTitle(), _("Operazione non riuscita: {e}").format(e=e))

    # ------------------------------------------------------------------ ricerca
    def search(self) -> list[Match]:
        """Esegue la ricerca nell'ambito scelto e mostra i risultati."""
        view = self._view()
        if view is None:
            raise SearchError(_("Nessun documento aperto."))
        pages = self._pages(view)
        self.matches = find_in_document(view.document, self.find_edit.text(), self.options(), pages)
        self._stamp = (id(view.document), view.document.version, self._query())
        self.current = -1
        self.summary_label.hide()
        self._fill_results()
        n = len(self.matches)
        if n == 0:
            msg = _("Nessuna occorrenza trovata.")
        else:
            msg = _("1 occorrenza trovata.") if n == 1 else _("{n} occorrenze trovate.").format(n=n)
            bad = sum(1 for m in self.matches if not m.replaceable)
            if bad:
                msg += " " + _("{n} non sostituibili.").format(n=bad)
        self.status_label.setText(msg)
        self.ctx.status(msg)
        self._update_hits()
        return self.matches

    def _ensure_results(self) -> bool:
        if not self._fresh(self._view()):
            self.search()
        return bool(self.matches)

    def _fill_results(self) -> None:
        if not hasattr(self, "results"):
            return
        self.results.blockSignals(True)
        self.results.clear()
        deleting = self.mode == MODE_DELETE
        for m in self.matches:
            text = _("p. {p} — {c}").format(p=m.page + 1, c=m.context)
            if not m.replaceable:
                text += _(" (non sostituibile: {r})").format(r=m.reason)
            item = QListWidgetItem(text)
            if deleting and m.replaceable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            elif not m.replaceable:
                item.setForeground(theme.color("danger"))
            self.results.addItem(item)
        if 0 <= self.current < len(self.matches):
            self.results.setCurrentRow(self.current)
        self.results.blockSignals(False)

    def _clear_results(self) -> None:
        self.matches = []
        self.current = -1
        self._stamp = None
        self.results.clear()
        self.status_label.clear()
        self._clear_hits()

    def _row_changed(self, row: int) -> None:
        if 0 <= row < len(self.matches):
            self.select(row)

    def select(self, index: int) -> None:
        """Rende corrente l'occorrenza `index`: la mostra ed evidenzia nella pagina."""
        if not self.matches:
            return
        index %= len(self.matches)
        self.current = index
        if self.results.currentRow() != index:
            self.results.blockSignals(True)
            self.results.setCurrentRow(index)
            self.results.blockSignals(False)
        m = self.matches[index]
        view = self._view()
        if view is not None:
            try:
                if m.rects:
                    rect = m.rects[0]
                    for r in m.rects[1:]:
                        rect = rect | r
                    view.ensure_visible(m.page, rect)
                else:
                    view.go_to_page(m.page)
            except Exception:  # noqa: BLE001 - la navigazione non deve interrompere la ricerca
                pass
        self.status_label.setText(_("Occorrenza {i} di {n}").format(i=index + 1, n=len(self.matches)))
        self._update_hits()

    def _update_hits(self) -> None:
        view = self._view()
        if view is None:
            return
        if self._hits_view is not None and self._hits_view is not view:
            self._clear_hits()
        hits: dict[int, list] = {}
        self._hit_offsets = []
        for m in self.matches:
            lst = hits.setdefault(m.page, [])
            self._hit_offsets.append(len(lst))
            lst.extend(m.rects)
        current = None
        if 0 <= self.current < len(self.matches) and self.matches[self.current].rects:
            current = (self.matches[self.current].page, self._hit_offsets[self.current])
        view.set_search_hits(hits, current)
        self._hits_view = view

    def _clear_hits(self) -> None:
        view = self._hits_view
        self._hits_view = None
        if view is None:
            return
        try:
            view.set_search_hits({}, None)
        except RuntimeError:     # vista già distrutta
            pass

    # ------------------------------------------------------------------ azioni
    def on_find(self) -> None:
        if not self._validate():
            return
        self._guard(self._find)

    def _find(self) -> None:
        if self.search():
            self.select(self._first_from_current_page())

    def _first_from_current_page(self) -> int:
        view = self._view()
        page = view.current_page if view is not None else 0
        for i, m in enumerate(self.matches):
            if m.page >= page:
                return i
        return 0

    def on_next(self) -> None:
        was_fresh = self._fresh(self._view())
        if self._ensure_results():
            self.select(self.current + 1 if was_fresh and self.current >= 0 else self._first_from_current_page())

    def on_previous(self) -> None:
        was_fresh = self._fresh(self._view())
        if self._ensure_results():
            self.select(self.current - 1 if was_fresh and self.current >= 0 else self._first_from_current_page() - 1)

    def on_replace(self) -> None:
        view = self._view()
        if not self._fresh(view) or self.current < 0:
            # prima pressione: mostra l'occorrenza che verrà sostituita
            if self._ensure_results():
                self.select(self.current if self.current >= 0 else self._first_from_current_page())
            return
        m = self.matches[self.current]
        if not m.replaceable:
            self.select(self.current + 1)
            return
        replacement = self.replace_edit.text()
        summary = replace_one(view.document, m, replacement, self.options(), delete=False,
                              remove_adjacent_space=True)
        self._show_summary(summary, deleting=not replacement)
        # passa all'occorrenza successiva (dopo il testo appena inserito)
        old_page, old_block, old_end = m.page, m.block, m.start + len(replacement)
        self.search()
        for i, n in enumerate(self.matches):
            if (n.page, n.block) > (old_page, old_block) or (
                    (n.page, n.block) == (old_page, old_block) and n.start >= old_end):
                self.select(i)
                break
        else:
            if self.matches:
                self.select(0)

    def on_replace_all(self) -> None:
        view = self._view()
        if not self._ensure_results():
            self.ctx.info(self.windowTitle(), _("Nessuna occorrenza trovata."))
            return
        n = sum(1 for m in self.matches if m.replaceable)
        if n == 0:
            self.ctx.info(self.windowTitle(), _("Nessuna occorrenza da sostituire."))
            return
        replacement = self.replace_edit.text()
        # nessuna conferma: l'operazione si annulla con un solo Annulla (UX-13)
        summary = replace_all(view.document, self.find_edit.text(), replacement, self.options(),
                              pages=self._pages(view))
        self._after_bulk(summary, deleting=not replacement)

    def on_delete_all(self) -> None:
        view = self._view()
        if not self._ensure_results():
            self.ctx.info(self.windowTitle(), _("Nessuna occorrenza trovata."))
            return
        excluded = []
        for i, m in enumerate(self.matches):
            item = self.results.item(i)
            if m.replaceable and item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable \
                    and item.checkState() != Qt.CheckState.Checked:
                excluded.append(match_key(m))
        n = sum(1 for m in self.matches if m.replaceable) - len(excluded)
        if n <= 0:
            self.ctx.info(self.windowTitle(), _("Nessuna occorrenza selezionata da eliminare."))
            return
        summary = replace_all(view.document, self.find_edit.text(), "", self.options(), pages=self._pages(view),
                              delete=True, remove_adjacent_space=self.space_box.isChecked(), exclude=excluded,
                              recompose=self.recompose_box.isChecked())
        self._after_bulk(summary, deleting=True)

    def _after_bulk(self, summary: ReplaceSummary, deleting: bool) -> None:
        self.search()
        self._show_summary(summary, deleting)

    def _show_summary(self, summary: ReplaceSummary, deleting: bool) -> None:
        lines = [(_("Occorrenze eliminate: {n}.") if deleting else _("Occorrenze sostituite: {n}.")).format(
            n=summary.replaced)]
        if summary.not_replaceable:
            lines.append(_("Non sostituibili: {n}.").format(n=len(summary.not_replaceable)))
            for m, reason in summary.not_replaceable[:MAX_SUMMARY_ITEMS]:
                lines.append("  " + _("p. {p} «{t}»: {r}").format(p=m.page + 1, t=m.text, r=reason))
            extra = len(summary.not_replaceable) - MAX_SUMMARY_ITEMS
            if extra > 0:
                lines.append("  " + _("… e altre {n}.").format(n=extra))
        if summary.substitutions:
            lines.append(_("Font sostitutivi usati:"))
            for s in summary.substitutions:
                lines.append("  " + _("{a} → {b} ({n} caratteri)").format(a=s.original, b=s.substitute, n=s.chars))
        text = "\n".join(lines)
        self.summary_label.setText(text)
        self.summary_label.show()
        self.ctx.status(lines[0])

    # ------------------------------------------------------------------ chiusura
    def closeEvent(self, event) -> None:
        self._clear_hits()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self._clear_hits()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._validate()
