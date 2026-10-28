"""Interfaccia del controllo ortografico (TXT-14).

- `SpellHighlighter`: sottolineatura ondulata delle parole sconosciute nel riquadro
  di modifica. I formati di un QSyntaxHighlighter sono solo di visualizzazione: non
  cambiano i formati dei caratteri da cui `InlineEditor.paragraphs()` ricava i run,
  quindi non arrivano mai nel PDF.
- `attach_spellcheck(editor, service, lang)`: evidenziatore + menu contestuale con
  suggerimenti, «Ignora», «Ignora tutto», «Aggiungi al dizionario» e le voci standard.
- `SpellCheckDialog`: «Controlla ortografia del documento» (non modale).
- `DictionaryDialog`: lingue installate, installazione/rimozione di dizionari
  Hunspell, dizionario personale (modifica, importa, esporta).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import FoliantError, _, icon, theme

from .spell import (
    TRANSLATIONS, Issue, SpellService, apply_corrections, choose_language, document_language, match_case,
)

__all__ = ["TRANSLATIONS", "SpellHighlighter", "attach_spellcheck", "build_context_menu", "SpellCheckDialog",
           "DictionaryDialog"]

MAX_MENU_SUGGESTIONS = 5


# ====================================================================== evidenziatore
class SpellHighlighter(QSyntaxHighlighter):
    """Sottolinea in rosso ondulato le parole assenti dal dizionario (solo a schermo)."""

    def __init__(self, document, service: SpellService, lang: str, status: Callable[[str], None] | None = None):
        super().__init__(document)
        self.service = service
        self.lang = lang
        self.enabled = True
        self.ignored_here: set[str] = set()      # «Ignora» (solo in questo riquadro)
        self._status = status
        self._format = QTextCharFormat()
        self._format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        self._format.setUnderlineColor(theme.color("danger"))
        self._report_missing()

    def _report_missing(self) -> None:
        if self._status is not None and self.lang and not self.service.has_language(self.lang):
            self._status(_("Gestisci i dizionari: {l} non è installata.").format(
                l=self.service.language_name(self.lang)))

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self._report_missing()
        self.rehighlight()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.rehighlight()

    def errors(self, text: str) -> list[tuple[int, int, str]]:
        if not self.enabled or not self.lang:
            return []
        return [(s, e, w) for s, e, w in self.service.misspelled(text, self.lang)
                if w.lower() not in self.ignored_here]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - API Qt
        for s, e, _w in self.errors(text):
            self.setFormat(s, e - s, self._format)

    def error_at(self, position: int) -> tuple[int, int, str] | None:
        """(inizio, fine, parola) assoluti nel documento dell'errore che contiene `position`."""
        doc = self.document()
        if doc is None:
            return None
        block = doc.findBlock(position)
        if not block.isValid():
            return None
        rel = position - block.position()
        for s, e, w in self.errors(block.text()):
            if s <= rel <= e:
                return block.position() + s, block.position() + e, w
        return None


def replace_range(editor, start: int, end: int, text: str) -> None:
    """Sostituisce [start, end) con `text` mantenendo il formato del primo carattere."""
    doc = editor.document()
    probe = QTextCursor(doc)
    probe.setPosition(min(start + 1, max(0, doc.characterCount() - 1)))
    fmt = probe.charFormat()
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText(text, fmt)
    cursor.endEditBlock()
    editor.setTextCursor(cursor)


def build_context_menu(editor, highlighter: SpellHighlighter, pos: QPoint) -> QMenu:
    """Menu contestuale del riquadro: suggerimenti e azioni ortografiche, poi le voci standard."""
    menu = editor.createStandardContextMenu(pos)
    first = menu.actions()[0] if menu.actions() else None
    service = highlighter.service
    cursor = editor.cursorForPosition(pos)
    hit = highlighter.error_at(cursor.position())
    added = []
    if hit is not None:
        start, end, word = hit
        sugg = service.suggestions(word, highlighter.lang, MAX_MENU_SUGGESTIONS)
        if sugg:
            for s in sugg:
                act = menu.addAction(s)
                f = act.font()
                f.setBold(True)
                act.setFont(f)
                act.triggered.connect(lambda _c=False, a=start, b=end, t=s: replace_range(editor, a, b, t))
                added.append(act)
        else:
            act = menu.addAction(_("Nessun suggerimento"))
            act.setEnabled(False)
            added.append(act)

        def ignore_here(_c=False, w=word):
            highlighter.ignored_here.add(w.lower())
            highlighter.rehighlight()

        def ignore_all(_c=False, w=word):
            service.ignore_all(w)
            highlighter.rehighlight()

        def add_dict(_c=False, w=word):
            service.add_to_personal(w)
            highlighter.rehighlight()

        sep = menu.addSeparator()
        added.append(sep)
        for text, slot in ((_("Ignora"), ignore_here), (_("Ignora tutto"), ignore_all),
                           (_("Aggiungi al dizionario"), add_dict)):
            act = menu.addAction(text)
            act.triggered.connect(slot)
            added.append(act)
    # lingua del controllo per il blocco
    langs = service.available_languages()
    sub = QMenu(_("Lingua del controllo"), menu)
    sub.setIcon(icon("languages"))
    for code in langs:
        act = sub.addAction(service.language_name(code))
        act.setCheckable(True)
        act.setChecked(code == highlighter.lang)
        act.triggered.connect(lambda _c=False, c=code: highlighter.set_language(c))
    added.append(menu.addSeparator())
    added.append(sub.menuAction())
    menu.addMenu(sub)
    # le voci ortografiche in cima, prima di quelle standard
    if first is not None:
        for act in added:
            menu.removeAction(act)
        menu.insertActions(first, added)
        menu.insertSeparator(first)
    return menu


def attach_spellcheck(editor, service: SpellService, lang: str,
                      status: Callable[[str], None] | None = None) -> SpellHighlighter:
    """Attiva il controllo ortografico in un `InlineEditor` (o QTextEdit)."""
    old = getattr(editor, "_spell_highlighter", None)
    if old is not None:
        old.setDocument(None)
    hl = SpellHighlighter(editor.document(), service, lang, status)
    editor._spell_highlighter = hl
    editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show_menu(pos: QPoint) -> None:
        menu = build_context_menu(editor, hl, pos)
        menu.exec(editor.mapToGlobal(pos))
        menu.deleteLater()

    previous = getattr(editor, "_spell_menu_slot", None)
    if previous is not None:
        try:
            editor.customContextMenuRequested.disconnect(previous)
        except (RuntimeError, TypeError):
            pass
    editor._spell_menu_slot = show_menu
    editor.customContextMenuRequested.connect(show_menu)
    hl.rehighlight()      # subito (QSyntaxHighlighter altrimenti attende il ciclo di eventi)
    return hl


# ====================================================================== controllo del documento
class SpellCheckDialog(QDialog):
    """«Controlla ortografia del documento»: elenco delle parole sconosciute e correzioni."""

    COL_WORD, COL_PAGE, COL_CONTEXT = 0, 1, 2

    def __init__(self, ctx, service: SpellService, parent: QWidget | None = None, lang: str | None = None,
                 granularity: str = "paragraph"):
        super().__init__(parent)
        self.ctx = ctx
        self.service = service
        self.granularity = granularity
        self.issues: list[Issue] = []
        self.ignored_once: set[tuple] = set()
        self._hits_view = None
        self._applying = False
        self.setModal(False)
        self.setWindowTitle(_("Controlla ortografia del documento"))
        self.setWindowIcon(icon("spell-check"))
        self.resize(640, 460)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(_("Lingua:")))
        self.lang_combo = QComboBox()
        top.addWidget(self.lang_combo, 1)
        self.recheck_btn = QPushButton(icon("refresh-cw"), _("Controlla di nuovo"))
        self.recheck_btn.clicked.connect(self.recheck)
        top.addWidget(self.recheck_btn)
        root.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([_("Parola"), _("Pagina"), _("Contesto")])
        self.tree.setColumnWidth(self.COL_WORD, 140)
        self.tree.setColumnWidth(self.COL_PAGE, 60)
        self.tree.currentItemChanged.connect(lambda *_a: self._on_current())
        root.addWidget(self.tree, 1)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel(_("Non nel dizionario:")))
        self.word_label = QLabel()
        f = self.word_label.font()
        f.setBold(True)
        self.word_label.setFont(f)
        self.word_label.setStyleSheet(f"color: {theme.color('danger').name()};")
        left.addWidget(self.word_label)
        left.addWidget(QLabel(_("Sostituisci con:")))
        self.replacement = QComboBox()
        self.replacement.setEditable(True)
        self.replacement.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        left.addWidget(self.replacement)
        left.addWidget(QLabel(_("Suggerimenti:")))
        self.suggestions = QListWidget()
        self.suggestions.currentTextChanged.connect(
            lambda t: self.replacement.setEditText(t) if t else None)
        self.suggestions.itemDoubleClicked.connect(lambda _i: self.change())
        left.addWidget(self.suggestions, 1)
        body.addLayout(left, 1)

        buttons = QVBoxLayout()
        self.change_btn = QPushButton(_("Cambia"))
        self.change_all_btn = QPushButton(_("Cambia tutto"))
        self.ignore_btn = QPushButton(_("Ignora"))
        self.ignore_all_btn = QPushButton(_("Ignora tutto"))
        self.add_btn = QPushButton(icon("book-plus"), _("Aggiungi al dizionario"))
        self.dict_btn = QPushButton(icon("book-open"), _("Dizionari…"))
        self.close_btn = QPushButton(_("Chiudi"))
        for b, slot in ((self.change_btn, self.change), (self.change_all_btn, self.change_all),
                        (self.ignore_btn, self.ignore), (self.ignore_all_btn, self.ignore_all),
                        (self.add_btn, self.add_to_dictionary), (self.dict_btn, self.open_dictionaries),
                        (self.close_btn, self.close)):
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.insertStretch(6)
        body.addLayout(buttons)
        root.addLayout(body, 1)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._fill_languages(lang)
        self.lang_combo.currentIndexChanged.connect(lambda _i: self.recheck())
        try:
            ctx.documentChanged.connect(self._on_document_changed)
            ctx.currentViewChanged.connect(lambda _v: self.recheck() if self.isVisible() else None)
        except Exception:
            pass
        self._update_buttons()

    # ------------------------------------------------------------ lingua
    def _fill_languages(self, lang: str | None = None) -> None:
        current = lang or self.language()
        if not current:
            view = self._view()
            pdf = view.document.pdf if view is not None else None
            if view is not None:
                with view.document.lock:
                    current = choose_language(self.service, pdf)
                    wanted = document_language(pdf)
                if wanted and not self.service.has_language(wanted):
                    self._missing(wanted)
            else:
                current = choose_language(self.service)
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        for code in self.service.available_languages():
            self.lang_combo.addItem(self.service.language_name(code), code)
        idx = self.lang_combo.findData(current)
        if idx < 0 and current:
            self._missing(current)
        self.lang_combo.setCurrentIndex(max(0, idx))
        self.lang_combo.blockSignals(False)

    def _missing(self, lang: str) -> None:
        msg = _("Gestisci i dizionari: {l} non è installata.").format(l=self.service.language_name(lang))
        self.ctx.status(msg)
        self.status_label.setText(msg)

    def language(self) -> str | None:
        return self.lang_combo.currentData() if hasattr(self, "lang_combo") else None

    def set_language(self, lang: str) -> None:
        idx = self.lang_combo.findData(lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        else:
            self._missing(lang)

    # ------------------------------------------------------------ verifica
    def _view(self):
        return self.ctx.current_view()

    def recheck(self) -> None:
        """Controlla l'intero documento della vista corrente."""
        view = self._view()
        self.issues = []
        if view is None:
            self.status_label.setText(_("Nessun documento aperto."))
            self._refresh_list()
            return
        lang = self.language()
        if not lang or not self.service.has_language(lang):
            self._missing(lang or "?")
            self._refresh_list()
            return
        self.service.refresh()
        try:
            with view.document.lock:
                found = self.service.check_document(view.document.pdf, lang, granularity=self.granularity,
                                                    suggest=False)
        except Exception as e:  # noqa: BLE001 - nessun errore deve chiudere l'applicazione
            self.ctx.error(_("Controllo ortografico"), str(e))
            found = []
        self.issues = [i for i in found if i.key not in self.ignored_once]
        self._refresh_list()
        if self.issues:
            self.select(0)

    def _refresh_list(self, select: int | None = None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        muted = theme.color("text_muted") if "text_muted" in theme.tokens() else None
        for issue in self.issues:
            item = QTreeWidgetItem([issue.word, str(issue.page + 1), issue.context])
            if not issue.editable:
                item.setToolTip(self.COL_WORD, issue.reason)
                if muted is not None:
                    for c in range(3):
                        item.setForeground(c, muted)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        if not self.issues:
            self.word_label.clear()
            self.replacement.clear()
            self.suggestions.clear()
            self._clear_hits()
            if self._view() is not None and self.language() and self.service.has_language(self.language()):
                self.status_label.setText(_("Nessun errore ortografico trovato."))
        else:
            self.status_label.setText(_("{n} parole da controllare.").format(n=len(self.issues)))
            if select is not None:
                self.select(select)
        self._update_buttons()

    def current_index(self) -> int:
        item = self.tree.currentItem()
        return self.tree.indexOfTopLevelItem(item) if item is not None else -1

    def current_issue(self) -> Issue | None:
        i = self.current_index()
        return self.issues[i] if 0 <= i < len(self.issues) else None

    def select(self, index: int) -> None:
        if not self.issues:
            return
        index = max(0, min(index, len(self.issues) - 1))
        item = self.tree.topLevelItem(index)
        if self.tree.currentItem() is item:
            self._on_current()
        else:
            self.tree.setCurrentItem(item)

    def _on_current(self) -> None:
        issue = self.current_issue()
        self.suggestions.blockSignals(True)
        self.suggestions.clear()
        self.replacement.clear()
        if issue is None:
            self.word_label.clear()
            self.suggestions.blockSignals(False)
            self._update_buttons()
            return
        self.word_label.setText(issue.word)
        if not issue.suggestions:
            issue.suggestions = self.service.suggestions(issue.word, self.language() or "it")
        self.suggestions.addItems(issue.suggestions)
        self.replacement.addItems(issue.suggestions)
        self.suggestions.blockSignals(False)
        if issue.suggestions:
            self.suggestions.setCurrentRow(0)
            self.replacement.setEditText(issue.suggestions[0])
        else:
            self.replacement.setEditText(issue.word)
        if not issue.editable:
            self.status_label.setText(_("Questa parola è in un blocco non modificabile: la correzione non è "
                                        "applicabile ({r}).").format(r=issue.reason))
        else:
            self.status_label.setText(_("{n} parole da controllare.").format(n=len(self.issues)))
        self._show_issue(issue)
        self._update_buttons()

    def _update_buttons(self) -> None:
        issue = self.current_issue()
        has = issue is not None
        self.change_btn.setEnabled(has and issue.editable)
        self.change_all_btn.setEnabled(has and issue.editable)
        for b in (self.ignore_btn, self.ignore_all_btn, self.add_btn):
            b.setEnabled(has)

    # ------------------------------------------------------------ evidenziazione
    def _show_issue(self, issue: Issue) -> None:
        view = self._view()
        if view is None:
            return
        try:
            rects = issue.rects
            if rects:
                rect = rects[0]
                for r in rects[1:]:
                    rect = rect | r
                view.ensure_visible(issue.page, rect)
            else:
                view.go_to_page(issue.page)
            if self._hits_view is not None and self._hits_view is not view:
                self._clear_hits()
            view.set_search_hits({issue.page: list(rects)}, (issue.page, 0) if rects else None)
            self._hits_view = view
        except Exception:  # noqa: BLE001 - la navigazione non deve interrompere il controllo
            pass

    def _clear_hits(self) -> None:
        view, self._hits_view = self._hits_view, None
        if view is not None:
            try:
                view.set_search_hits({}, None)
            except RuntimeError:
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        self._clear_hits()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._clear_hits()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self.issues:
            self.recheck()

    # ------------------------------------------------------------ azioni
    def _replacement_text(self) -> str:
        return self.replacement.currentText().strip()

    def _apply(self, corrections: list[tuple[Issue, str]]) -> None:
        view = self._view()
        if view is None or not corrections:
            return
        try:
            ctrl = getattr(self.ctx, "text_edit", None)
            if ctrl is not None:
                ctrl.commit_editor(view)
                if hasattr(ctrl, "ensure_editable") and not ctrl.ensure_editable(view):
                    return
        except Exception:  # noqa: BLE001
            pass
        anchor = corrections[0][0]
        pos = (anchor.page, anchor.block, anchor.start)
        self._applying = True
        try:
            summary = apply_corrections(view.document, corrections, granularity=self.granularity)
        except FoliantError as e:
            self.ctx.error(_("Correzione non riuscita"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.ctx.error(_("Correzione non riuscita"), str(e))
            return
        finally:
            self._applying = False
        if summary.failed:
            self.ctx.status(_("{n} correzioni non applicabili.").format(n=len(summary.failed)))
            self.ctx.error(_("Correzione non riuscita"), "\n".join(
                f"«{i.word}»: {r}" for i, r in summary.failed[:8]))
        elif summary.applied > 1:
            self.ctx.status(_("{n} correzioni applicate.").format(n=summary.applied))
        self._recheck_from(pos)

    def _recheck_from(self, pos: tuple) -> None:
        self.recheck()
        nxt = next((k for k, i in enumerate(self.issues) if (i.page, i.block, i.start) >= pos), 0)
        if self.issues:
            self.select(nxt)

    def _on_document_changed(self, view) -> None:
        if self._applying or not self.isVisible() or view is not self._view():
            return
        issue = self.current_issue()
        self._recheck_from((issue.page, issue.block, issue.start) if issue else (0, 0, 0))

    def change(self) -> None:
        issue = self.current_issue()
        text = self._replacement_text()
        if issue is None or not issue.editable or not text or text == issue.word:
            return
        self._apply([(issue, match_case(issue.word, text))])

    def change_all(self) -> None:
        issue = self.current_issue()
        text = self._replacement_text()
        if issue is None or not issue.editable or not text:
            return
        low = issue.word.lower()
        todo = [(i, match_case(i.word, text)) for i in self.issues
                if i.word.lower() == low and i.editable and i.word != text]
        self._apply(todo)

    def _drop(self, pred) -> None:
        idx = self.current_index()
        self.issues = [i for i in self.issues if not pred(i)]
        self._refresh_list(select=min(max(idx, 0), len(self.issues) - 1) if self.issues else None)

    def ignore(self) -> None:
        issue = self.current_issue()
        if issue is None:
            return
        self.ignored_once.add(issue.key)
        self._drop(lambda i: i is issue)

    def ignore_all(self) -> None:
        issue = self.current_issue()
        if issue is None:
            return
        self.service.ignore_all(issue.word)
        low = issue.word.lower()
        self._drop(lambda i: i.word.lower() == low)

    def add_to_dictionary(self) -> None:
        issue = self.current_issue()
        if issue is None:
            return
        self.service.add_to_personal(issue.word)
        low = issue.word.lower()
        self._drop(lambda i: i.word.lower() == low)

    def open_dictionaries(self) -> "DictionaryDialog":
        dlg = DictionaryDialog(self.ctx, self.service, self)
        dlg.languagesChanged.connect(lambda: self._fill_languages(self.language()))
        dlg.finished.connect(lambda _r: self.recheck())
        dlg.show()
        return dlg


# ====================================================================== dizionari
class DictionaryDialog(QDialog):
    """Lingue installate, dizionari Hunspell dell'utente e dizionario personale."""

    languagesChanged = Signal()

    def __init__(self, ctx, service: SpellService, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.service = service
        self.setWindowTitle(_("Dizionari"))
        self.setWindowIcon(icon("book-open"))
        self.resize(560, 520)
        root = QVBoxLayout(self)

        root.addWidget(_bold_label(_("Lingue installate")))
        self.languages = QListWidget()
        root.addWidget(self.languages, 1)
        self.folder_label = QLabel(_("Cartella dei dizionari dell'utente: {p}").format(p=service.user_dict_dir))
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.folder_label)
        row = QHBoxLayout()
        self.install_btn = QPushButton(icon("download"), _("Installa dizionario…"))
        self.remove_dict_btn = QPushButton(icon("trash-2"), _("Rimuovi dizionario"))
        self.refresh_btn = QPushButton(icon("refresh-cw"), _("Aggiorna"))
        self.install_btn.clicked.connect(self.install_dictionary)
        self.remove_dict_btn.clicked.connect(self.remove_dictionary)
        self.refresh_btn.clicked.connect(self.refresh)
        for b in (self.install_btn, self.remove_dict_btn, self.refresh_btn):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        root.addWidget(_bold_label(_("Dizionario personale")))
        self.path_label = QLabel(_("File: {p}").format(p=service.personal_path))
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.path_label)
        self.words = QListWidget()
        self.words.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        root.addWidget(self.words, 2)
        row2 = QHBoxLayout()
        self.new_word = QLineEdit()
        self.new_word.setPlaceholderText(_("Nuova parola"))
        self.new_word.returnPressed.connect(self.add_word)
        self.add_btn = QPushButton(icon("plus"), _("Aggiungi"))
        self.remove_btn = QPushButton(icon("minus"), _("Rimuovi"))
        self.import_btn = QPushButton(_("Importa…"))
        self.export_btn = QPushButton(_("Esporta…"))
        self.add_btn.clicked.connect(self.add_word)
        self.remove_btn.clicked.connect(self.remove_words)
        self.import_btn.clicked.connect(self.import_words)
        self.export_btn.clicked.connect(self.export_words)
        row2.addWidget(self.new_word, 1)
        for b in (self.add_btn, self.remove_btn, self.import_btn, self.export_btn):
            row2.addWidget(b)
        root.addLayout(row2)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.accept)
        end = QHBoxLayout()
        end.addStretch(1)
        end.addWidget(close)
        root.addLayout(end)
        self.refresh()

    # ------------------------------------------------------------ lingue
    def refresh(self) -> None:
        self.service.refresh()
        self.languages.clear()
        files = self.service.dictionary_files()
        for code in self.service.available_languages():
            parts = []
            if self.service._builtin_available(code):
                parts.append(_("integrato"))
            for p in files.get(code, []):
                parts.append(_("Hunspell (solo radici): {p}").format(p=p))
            self.languages.addItem(f"{self.service.language_name(code)} ({code}) — " + "; ".join(parts))
            item = self.languages.item(self.languages.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, files.get(code, []))
        self._fill_words()
        self.languagesChanged.emit()

    def install_dictionary(self) -> None:
        paths = self.ctx.ask_open_paths(_("Installa dizionario…"), _("Dizionari Hunspell (*.dic)"), True)
        if not paths:
            return
        dest = Path(self.service.user_dict_dir)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for p in paths:
                src = Path(p)
                shutil.copyfile(src, dest / src.name)
                aff = src.with_suffix(".aff")
                if aff.exists():
                    shutil.copyfile(aff, dest / aff.name)
                self.ctx.status(_("Dizionario installato: {n}").format(n=src.name))
        except OSError as e:
            self.ctx.error(_("Dizionari"), _("Impossibile scrivere il file: {e}").format(e=e))
        self.refresh()

    def remove_dictionary(self) -> None:
        item = self.languages.currentItem()
        if item is None:
            return
        user_dir = os.path.realpath(self.service.user_dict_dir)
        mine = [p for p in (item.data(Qt.ItemDataRole.UserRole) or [])
                if os.path.realpath(os.path.dirname(p)) == user_dir]
        if not mine:
            self.ctx.info(_("Dizionari"), _("Si possono rimuovere solo i dizionari installati nella cartella "
                                            "dell'utente."))
            return
        names = ", ".join(os.path.basename(p) for p in mine)
        if not self.ctx.confirm(_("Dizionari"), _("Rimuovere il dizionario «{n}»?").format(n=names)):
            return
        for p in mine:
            for f in (Path(p), Path(p).with_suffix(".aff")):
                try:
                    f.unlink()
                except OSError:
                    pass
        self.refresh()

    # ------------------------------------------------------------ dizionario personale
    def _fill_words(self) -> None:
        self.words.clear()
        self.words.addItems(self.service.personal_words())

    def add_word(self) -> None:
        w = self.new_word.text().strip()
        if not w:
            return
        self.service.add_to_personal(w)
        self.new_word.clear()
        self._fill_words()

    def remove_words(self) -> None:
        for item in self.words.selectedItems():
            self.service.remove_from_personal(item.text())
        self._fill_words()

    def import_words(self) -> None:
        paths = self.ctx.ask_open_paths(_("Importa dizionario personale"),
                                        _("Elenchi di parole (*.txt *.dic);;Tutti i file (*)"), False)
        if not paths:
            return
        try:
            n = self.service.import_personal(paths[0])
        except FoliantError as e:
            self.ctx.error(_("Dizionario personale"), str(e))
            return
        self._fill_words()
        self.ctx.status(_("{n} parole importate.").format(n=n))

    def export_words(self) -> None:
        path = self.ctx.ask_save_path(_("Esporta dizionario personale"), "dizionario_personale.txt",
                                      _("Elenchi di parole (*.txt)"), ".txt")
        if not path:
            return
        try:
            self.service.export_personal(path)
        except FoliantError as e:
            self.ctx.error(_("Dizionario personale"), str(e))
            return
        self.ctx.status(_("Dizionario personale esportato."))


def _bold_label(text: str) -> QLabel:
    lbl = QLabel(text)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    return lbl
