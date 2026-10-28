"""Ricerca avanzata e su più documenti (VIS-25): pannello non modale (Ctrl+Maiusc+F)
con ambito (documento corrente, documenti aperti, cartella con sottocartelle),
modalità (frase, tutte le parole, almeno una, espressione regolare), opzioni,
inclusione di commenti, segnalibri, campi modulo e metadati, risultati per file e
pagina con estratto, esportazione CSV.
"""

from __future__ import annotations

import html
import os

import pymupdf
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, Context, OperationCancelled, _, add_translations, atomic_write, bind_icon, theme

from . import advsearch_engine as eng
from . import fulltext
from .advsearch_engine import AdvHit, AdvOptions, AdvSummary

SCOPE_CURRENT = "current"
SCOPE_OPEN = "open"
SCOPE_FOLDER = "folder"
SCOPE_INDEX = "index"

TRANSLATIONS = {
    "cambiato dopo l'indicizzazione": "changed after indexing",
    "Indice": "Index",
    "Crea un indice da una cartella…": "Create an index from a folder…",
    "Aggiorna l'indice (solo i file cambiati)": "Update the index (changed files only)",
    "Elimina l'indice": "Delete the index",
    "Crea un indice da una cartella per cercare in modo immediato.":
        "Create an index from a folder for instant searches.",
    "Gli indici restano solo finché Foliant è aperto.": "Indexes are kept only while Foliant is open.",
    "Ricerca nell'indice…": "Searching the index…",
    "Creazione dell'indice…": "Creating the index…",
    "Aggiornamento dell'indice…": "Updating the index…",
    "Eliminare l'indice «{name}»? I file indicizzati non vengono toccati.":
        "Delete the index “{name}”? The indexed files are not touched.",
    "Indice: {a} file aggiunti, {u} aggiornati, {r} tolti, {k} invariati.":
        "Index: {a} files added, {u} updated, {r} removed, {k} unchanged.",
    "Non indicizzati: {l}": "Not indexed: {l}",
    "{n} file cambiati o eliminati dopo l'indicizzazione: aggiorna l'indice.":
        "{n} files changed or deleted after indexing: update the index.",
    "Allegati PDF": "PDF attachments",
    "Allegato «{name}»": "Attachment “{name}”",
    "ricerca.csv": "search.csv",
    "Ricerca avanzata…": "Advanced search…",
    "Ricerca avanzata": "Advanced search",
    "Vista": "View",
    "Cerca": "Search",
    "Testo da cercare…": "Text to search for…",
    "Ambito": "Scope",
    "Documento corrente": "Current document",
    "Documenti aperti": "Open documents",
    "Cartella": "Folder",
    "Scegli la cartella…": "Choose folder…",
    "Nessuna cartella scelta": "No folder chosen",
    "Includi le sottocartelle": "Include subfolders",
    "Modalità": "Mode",
    "Frase esatta": "Exact phrase",
    "Tutte le parole (stessa pagina)": "All words (same page)",
    "Almeno una parola": "Any word",
    "Espressione regolare": "Regular expression",
    "Opzioni": "Options",
    "Maiuscole/minuscole": "Match case",
    "Parola intera": "Whole word",
    "Ignora accenti": "Ignore accents",
    "Includi": "Include",
    "Commenti": "Comments",
    "Segnalibri": "Bookmarks",
    "Campi modulo": "Form fields",
    "Metadati": "Metadata",
    "Testo": "Text",
    "Commento": "Comment",
    "Segnalibro": "Bookmark",
    "Campo": "Field",
    "Esporta CSV…": "Export CSV…",
    "Esporta risultati": "Export results",
    "Risultati esportati: {p}": "Results exported: {p}",
    "Chiudi": "Close",
    "Espressione regolare non valida: {e}": "Invalid regular expression: {e}",
    "Scegli una cartella in cui cercare.": "Choose a folder to search in.",
    "Nessun documento aperto.": "No document open.",
    "Ricerca in corso…": "Searching…",
    "Ricerca annullata.": "Search cancelled.",
    "Nessun risultato.": "No results.",
    "{n} risultati in {f} file": "{n} results in {f} files",
    "1 risultato in 1 file": "1 result in 1 file",
    "{n} risultati in 1 file": "{n} results in 1 file",
    "{n} file esaminati": "{n} files searched",
    "1 file saltato: {l}": "1 file skipped: {l}",
    "{n} file saltati: {l}": "{n} files skipped: {l}",
    "cifrato": "encrypted",
    "danneggiato": "damaged",
    "{n} pagine interrotte per tempo scaduto": "{n} pages stopped (time limit)",
    "Risultati limitati ai primi {n}.": "Results limited to the first {n}.",
    "Pagina {n}": "Page {n}",
    "Documento": "Document",
    "file": "file",
    "pagina": "page",
    "tipo": "type",
    "estratto": "excerpt",
    "Impossibile aprire il file": "Cannot open the file",
}


def _kind_names() -> dict[str, str]:
    return {eng.KIND_TEXT: _("Testo"), eng.KIND_COMMENT: _("Commento"), eng.KIND_BOOKMARK: _("Segnalibro"),
            eng.KIND_FIELD: _("Campo"), eng.KIND_METADATA: _("Metadati")}


class AdvancedSearchPanel(QDialog):
    """Finestra non modale (mai exec())."""

    def __init__(self, controller: "AdvancedSearchController", parent: QWidget):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("advsearch")
        self.setWindowTitle(_("Ricerca avanzata"))
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(560, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        row = QHBoxLayout()
        self.field = QLineEdit(self)
        self.field.setPlaceholderText(_("Testo da cercare…"))
        self.field.setClearButtonEnabled(True)
        self.field.returnPressed.connect(controller.run)
        self.field.textChanged.connect(lambda *_a: controller.validate())
        row.addWidget(self.field, 1)
        self.search_button = QPushButton(_("Cerca"), self)
        bind_icon(self.search_button, "search")
        self.search_button.setDefault(True)
        self.search_button.clicked.connect(controller.run)
        row.addWidget(self.search_button)
        root.addLayout(row)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.hide()
        root.addWidget(self.error)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.scope = QComboBox(self)
        self.scope.addItem(_("Documento corrente"), SCOPE_CURRENT)
        self.scope.addItem(_("Documenti aperti"), SCOPE_OPEN)
        self.scope.addItem(_("Cartella"), SCOPE_FOLDER)
        self.scope.addItem(_("Indice"), SCOPE_INDEX)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        form.addRow(_("Ambito"), self.scope)
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(_("Nessuna cartella scelta"))
        self.folder_label.setProperty("muted", True)
        self.folder_label.setMinimumWidth(10)
        folder_row.addWidget(self.folder_label, 1)
        self.folder_button = QToolButton(self)
        bind_icon(self.folder_button, "folder-search")
        self.folder_button.setToolTip(_("Scegli la cartella…"))
        self.folder_button.setAutoRaise(True)
        self.folder_button.clicked.connect(lambda: controller.choose_folder())
        folder_row.addWidget(self.folder_button)
        self.recursive = QCheckBox(_("Includi le sottocartelle"), self)
        self.recursive.setChecked(True)
        self.folder_box = QWidget(self)
        fb = QVBoxLayout(self.folder_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(4)
        fb.addLayout(folder_row)
        fb.addWidget(self.recursive)
        form.addRow("", self.folder_box)
        # indici full-text (VIS-25)
        self.index_box = QWidget(self)
        ib = QHBoxLayout(self.index_box)
        ib.setContentsMargins(0, 0, 0, 0)
        ib.setSpacing(4)
        self.index_combo = QComboBox(self.index_box)
        self.index_combo.setMinimumWidth(160)
        ib.addWidget(self.index_combo, 1)
        for attr, icon_name, tip, slot in (
                ("index_new", "plus", _("Crea un indice da una cartella…"), lambda: controller.create_index_dialog()),
                ("index_update", "refresh-cw", _("Aggiorna l'indice (solo i file cambiati)"),
                 lambda: controller.update_index()),
                ("index_delete", "trash-2", _("Elimina l'indice"), lambda: controller.delete_index())):
            b = QToolButton(self.index_box)
            bind_icon(b, icon_name)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            setattr(self, attr, b)
            ib.addWidget(b)
        form.addRow("", self.index_box)
        # riservatezza: gli indici non vengono mai salvati su disco
        self.index_note = QLabel(_("Gli indici restano solo finché Foliant è aperto."), self)
        self.index_note.setProperty("muted", True)
        self.index_note.setWordWrap(True)
        form.addRow("", self.index_note)
        self.mode = QComboBox(self)
        self.mode.addItem(_("Frase esatta"), eng.PHRASE)
        self.mode.addItem(_("Tutte le parole (stessa pagina)"), eng.ALL_WORDS)
        self.mode.addItem(_("Almeno una parola"), eng.ANY_WORD)
        self.mode.addItem(_("Espressione regolare"), eng.REGEX)
        self.mode.currentIndexChanged.connect(lambda *_a: controller.validate())
        form.addRow(_("Modalità"), self.mode)
        opts = QHBoxLayout()
        opts.setSpacing(16)
        self.opts_layout = opts
        self.opt_case = QCheckBox(_("Maiuscole/minuscole"), self)
        self.opt_word = QCheckBox(_("Parola intera"), self)
        self.opt_accents = QCheckBox(_("Ignora accenti"), self)
        for cb in (self.opt_case, self.opt_word, self.opt_accents):
            opts.addWidget(cb)
            cb.toggled.connect(lambda *_a: controller.validate())
        opts.addStretch(1)
        form.addRow(_("Opzioni"), opts)
        inc = QHBoxLayout()
        inc.setSpacing(16)
        self.inc_layout = inc
        self.inc_comments = QCheckBox(_("Commenti"), self)
        self.inc_bookmarks = QCheckBox(_("Segnalibri"), self)
        self.inc_fields = QCheckBox(_("Campi modulo"), self)
        self.inc_metadata = QCheckBox(_("Metadati"), self)
        self.inc_attachments = QCheckBox(_("Allegati PDF"), self)
        for cb in (self.inc_comments, self.inc_bookmarks, self.inc_fields, self.inc_metadata, self.inc_attachments):
            inc.addWidget(cb)
        inc.addStretch(1)
        form.addRow(_("Includi"), inc)
        root.addLayout(form)

        head = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setProperty("muted", True)
        self.summary.setWordWrap(True)
        head.addWidget(self.summary, 1)
        self.export_button = QToolButton(self)
        bind_icon(self.export_button, "download")
        self.export_button.setToolTip(_("Esporta CSV…"))
        self.export_button.setAccessibleName(_("Esporta CSV…"))
        self.export_button.setAutoRaise(True)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(lambda: controller.export_csv())
        head.addWidget(self.export_button)
        root.addLayout(head)

        from .search import HtmlDelegate
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setItemDelegate(HtmlDelegate(self.tree))
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.itemClicked.connect(self._on_item)
        self.tree.itemActivated.connect(self._on_item)
        root.addWidget(self.tree, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.close_button = QPushButton(_("Chiudi"), self)
        self.close_button.clicked.connect(self.hide)
        foot.addWidget(self.close_button)
        root.addLayout(foot)
        self._scope_changed()

    def _scope_changed(self, *_a) -> None:
        self.folder_box.setVisible(self.scope.currentData() == SCOPE_FOLDER)
        self.index_box.setVisible(self.scope.currentData() == SCOPE_INDEX)
        self.index_note.setVisible(self.scope.currentData() == SCOPE_INDEX)
        if self.scope.currentData() == SCOPE_INDEX:
            self.controller.refresh_indexes()

    def set_error(self, message: str | None) -> None:
        if message:
            self.error.setText(message)
            self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
            self.error.show()
            self.field.setStyleSheet(f"QLineEdit {{ border-color: {theme.hex_color('danger')}; }}")
        else:
            self.error.hide()
            self.error.setText("")
            self.field.setStyleSheet("")

    def _on_item(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(idx, int):
            self.controller.go_to(idx)

    def show_results(self, hits: list[AdvHit]) -> None:
        self.tree.clear()
        muted = theme.hex_color("text_muted")
        kinds = _kind_names()
        for file, pages in eng.group_hits(hits):
            total = sum(len(ix) for _p, ix in pages)
            fitem = QTreeWidgetItem(self.tree)
            fitem.setText(0, f"<b>{html.escape(os.path.basename(file) or file)}</b> &nbsp;"
                             f"<span style='color:{muted}'>{total} · {html.escape(os.path.dirname(file))}</span>")
            fitem.setToolTip(0, file)
            fitem.setData(0, Qt.ItemDataRole.UserRole, pages[0][1][0])
            for page, indices in pages:
                pitem = QTreeWidgetItem(fitem)
                title = _("Pagina {n}").format(n=page + 1) if page >= 0 else _("Documento")
                pitem.setText(0, f"<b>{html.escape(title)}</b> &nbsp;<span style='color:{muted}'>{len(indices)}</span>")
                pitem.setData(0, Qt.ItemDataRole.UserRole, indices[0])
                for i in indices:
                    h = hits[i]
                    tag = kinds.get(h.kind, h.kind)
                    if h.label:
                        tag += f" · {h.label}"
                    if h.attachment:
                        tag = _("Allegato «{name}»").format(name=h.attachment) + " · " + tag
                    if h.stale:
                        tag = _("cambiato dopo l'indicizzazione") + " · " + tag
                    child = QTreeWidgetItem(pitem)
                    prefix = "" if h.kind == eng.KIND_TEXT and not h.attachment and not h.stale else \
                        f"<span style='color:{muted}'>[{html.escape(tag)}]</span> "
                    child.setText(0, prefix + "…" + html.escape(h.before) + "<b>" + html.escape(h.found) + "</b>"
                                  + html.escape(h.after) + "…")
                    child.setData(0, Qt.ItemDataRole.UserRole, i)
                pitem.setExpanded(True)
            fitem.setExpanded(True)
        self.export_button.setEnabled(bool(hits))


class AdvancedSearchController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.panel: AdvancedSearchPanel | None = None
        self.folder: str | None = None
        self.hits: list[AdvHit] = []
        self.summary: AdvSummary | None = None
        self.summary_text = ""
        self.page_timeout: float | None = eng.DEFAULT_PAGE_TIMEOUT
        self._docs: dict[int, object] = {}   # chiave -> Document Foliant
        self.act_open = ctx.action(_("Ricerca avanzata…"), "search-code", self.open, shortcut="Ctrl+Shift+F",
                                   requires_document=False)
        ctx.add_menu_action(self.act_open, _("Vista"))
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_action(self.act_open)

    # ------------------------------------------------------------------ pannello
    def ensure_panel(self) -> AdvancedSearchPanel:
        if self.panel is None:
            self.panel = AdvancedSearchPanel(self, self.ctx.window)
        return self.panel

    def open(self) -> AdvancedSearchPanel:
        panel = self.ensure_panel()
        view = self.ctx.current_view()
        if view is not None and not panel.field.text():
            sel = view.selected_text().strip() if hasattr(view, "selected_text") else ""
            if sel and "\n" not in sel and len(sel) < 100:
                panel.field.setText(sel)
        if view is None and panel.scope.currentData() == SCOPE_CURRENT:
            panel.scope.setCurrentIndex(panel.scope.findData(SCOPE_FOLDER))
        panel.show()
        panel.raise_()
        panel.activateWindow()
        panel.field.setFocus()
        panel.field.selectAll()
        return panel

    def options(self) -> AdvOptions:
        p = self.ensure_panel()
        return AdvOptions(query=p.field.text(), mode=p.mode.currentData(), case_sensitive=p.opt_case.isChecked(),
                          whole_word=p.opt_word.isChecked(), ignore_accents=p.opt_accents.isChecked(),
                          comments=p.inc_comments.isChecked(), bookmarks=p.inc_bookmarks.isChecked(),
                          fields=p.inc_fields.isChecked(), metadata=p.inc_metadata.isChecked(),
                          attachments=p.inc_attachments.isChecked())

    def configure(self, query: str | None = None, *, scope: str | None = None, mode: str | None = None,
                  case_sensitive: bool | None = None, whole_word: bool | None = None,
                  ignore_accents: bool | None = None, comments: bool | None = None, bookmarks: bool | None = None,
                  fields: bool | None = None, metadata: bool | None = None, folder: str | None = None,
                  recursive: bool | None = None, attachments: bool | None = None) -> None:
        p = self.ensure_panel()
        if query is not None:
            p.field.setText(query)
        if scope is not None:
            p.scope.setCurrentIndex(p.scope.findData(scope))
        if mode is not None:
            p.mode.setCurrentIndex(p.mode.findData(mode))
        for cb, value in ((p.opt_case, case_sensitive), (p.opt_word, whole_word), (p.opt_accents, ignore_accents),
                          (p.inc_comments, comments), (p.inc_bookmarks, bookmarks), (p.inc_fields, fields),
                          (p.inc_metadata, metadata), (p.recursive, recursive), (p.inc_attachments, attachments)):
            if value is not None:
                cb.setChecked(value)
        if folder is not None:
            self.set_folder(folder)

    def set_folder(self, folder: str | None) -> None:
        self.folder = folder
        p = self.ensure_panel()
        p.folder_label.setText(folder or _("Nessuna cartella scelta"))
        p.folder_label.setToolTip(folder or "")

    # ------------------------------------------------------------- indici (VIS-25)
    def refresh_indexes(self, select: str | None = None) -> list[str]:
        p = self.ensure_panel()
        names = fulltext.list_indexes()
        current = select or p.index_combo.currentData()
        p.index_combo.blockSignals(True)
        p.index_combo.clear()
        for name in names:
            folder = fulltext.FullTextIndex(name).settings().get("folder", "")
            p.index_combo.addItem(name, name)
            p.index_combo.setItemData(p.index_combo.count() - 1, folder, Qt.ItemDataRole.ToolTipRole)
        if current in names:
            p.index_combo.setCurrentIndex(names.index(current))
        p.index_combo.blockSignals(False)
        has = bool(names)
        p.index_update.setEnabled(has)
        p.index_delete.setEnabled(has)
        return names

    def current_index(self) -> fulltext.FullTextIndex | None:
        name = self.ensure_panel().index_combo.currentData()
        return fulltext.FullTextIndex(name) if name else None

    def create_index(self, folder: str, name: str | None = None, recursive: bool = True) -> fulltext.IndexStats | None:
        index = fulltext.FullTextIndex(name or os.path.basename(os.path.normpath(folder)))
        try:
            stats = self.ctx.run_task(_("Creazione dell'indice…"), index.build, folder, recursive)
        except OperationCancelled:
            index.delete()
            return None
        self.refresh_indexes(index.name)
        p = self.ensure_panel()
        p.scope.setCurrentIndex(p.scope.findData(SCOPE_INDEX))
        self._set_summary(self._describe_stats(stats))
        return stats

    def create_index_dialog(self) -> fulltext.IndexStats | None:
        folder = self.ctx.ask_directory(_("Crea un indice da una cartella…"))
        if not folder:
            return None
        return self.create_index(folder, recursive=self.ensure_panel().recursive.isChecked())

    def update_index(self) -> fulltext.IndexStats | None:
        index = self.current_index()
        if index is None:
            return None
        try:
            stats = self.ctx.run_task(_("Aggiornamento dell'indice…"), index.update)
        except OperationCancelled:
            return None
        self._set_summary(self._describe_stats(stats))
        return stats

    def delete_index(self, confirm: bool = True) -> bool:
        index = self.current_index()
        if index is None:
            return False
        if confirm and not self.ctx.confirm(_("Elimina l'indice"), _("Eliminare l'indice «{name}»? I file indicizzati "
                                                                     "non vengono toccati.").format(name=index.name)):
            return False
        index.delete()
        self.refresh_indexes()
        return True

    def _describe_stats(self, s: fulltext.IndexStats) -> str:
        text = _("Indice: {a} file aggiunti, {u} aggiornati, {r} tolti, {k} invariati.").format(
            a=s.added, u=s.updated, r=s.removed, k=s.unchanged)
        if s.skipped:
            reasons = {"encrypted": _("cifrato"), "damaged": _("danneggiato")}
            listing = ", ".join(f"{os.path.basename(p)} ({reasons.get(r, r)})" for p, r in s.skipped)
            text += "\n" + _("Non indicizzati: {l}").format(l=listing)
        return text

    def choose_folder(self) -> str | None:
        folder = self.ctx.ask_directory(_("Scegli la cartella…"))
        if folder:
            self.set_folder(folder)
            p = self.ensure_panel()
            p.scope.setCurrentIndex(p.scope.findData(SCOPE_FOLDER))
        return folder

    def validate(self) -> str | None:
        """Controllo immediato: errore nel campo, mai un dialogo."""
        err = eng.validate(self.options())
        msg = _("Espressione regolare non valida: {e}").format(e=err) if err else None
        self.ensure_panel().set_error(msg)
        self.ensure_panel().search_button.setEnabled(err is None)
        return msg

    # ------------------------------------------------------------------ ricerca
    def search(self, query: str | None = None, **kwargs) -> int:
        """Per i test e l'automazione: configura ed esegue; restituisce il numero di risultati."""
        if query is not None or kwargs:
            self.configure(query, **kwargs)
        self.run()
        return len(self.hits)

    def run(self) -> None:
        p = self.ensure_panel()
        opts = self.options()
        if self.validate() is not None or not opts.terms():
            return
        scope = p.scope.currentData()
        self.hits = []
        self.summary = None
        self._docs = {}
        try:
            if scope == SCOPE_INDEX:
                index = self.current_index()
                if index is None:
                    self._set_summary(_("Crea un indice da una cartella per cercare in modo immediato.") + "\n"
                                      + _("Gli indici restano solo finché Foliant è aperto."))
                    return
                hits, summary = self.ctx.run_task(_("Ricerca nell'indice…"), index.search, opts,
                                                  page_timeout=self.page_timeout)
            elif scope == SCOPE_FOLDER:
                if not self.folder and not self.choose_folder():
                    self._set_summary(_("Scegli una cartella in cui cercare."))
                    return
                paths = eng.list_pdfs(self.folder, p.recursive.isChecked())
                hits, summary = self.ctx.run_task(_("Ricerca in corso…"), eng.search_files, paths, opts, ENGINE_LOCK,
                                                  page_timeout=self.page_timeout, encrypted_reason=_("cifrato"),
                                                  damaged_reason=_("danneggiato"))
            else:
                views = self.ctx.views() if scope == SCOPE_OPEN else \
                    ([self.ctx.current_view()] if self.ctx.current_view() is not None else [])
                if not views:
                    self._set_summary(_("Nessun documento aperto."))
                    p.show_results([])
                    return
                docs = []
                for v in views:
                    d = v.document
                    key = id(d)
                    self._docs[key] = d
                    name = str(d.path) if d.path is not None else d.display_name
                    docs.append((key, name, d))
                hits, summary = self.ctx.run_task(_("Ricerca in corso…"), eng.search_documents, docs, opts,
                                                  ENGINE_LOCK, page_timeout=self.page_timeout)
        except OperationCancelled:
            self._set_summary(_("Ricerca annullata."))
            return
        except Exception as e:
            self.ctx.error(_("Ricerca avanzata"), str(e))
            return
        self.hits = hits
        self.summary = summary
        p.show_results(hits)
        self._set_summary(self.describe(hits, summary))

    def describe(self, hits: list[AdvHit], s: AdvSummary) -> str:
        files = len({h.file for h in hits})
        n = len(hits)
        if not n:
            parts = [_("Nessun risultato.")]
        elif n == 1:
            parts = [_("1 risultato in 1 file")]
        elif files == 1:
            parts = [_("{n} risultati in 1 file").format(n=n)]
        else:
            parts = [_("{n} risultati in {f} file").format(n=n, f=files)]
        parts.append(_("{n} file esaminati").format(n=s.files))
        text = " · ".join(parts)
        if s.skipped:
            listing = ", ".join(f"{os.path.basename(path)} ({reason})" for path, reason in s.skipped)
            text += "\n" + (_("1 file saltato: {l}").format(l=listing) if len(s.skipped) == 1 else
                            _("{n} file saltati: {l}").format(n=len(s.skipped), l=listing))
        if s.timed_out:
            text += "\n" + _("{n} pagine interrotte per tempo scaduto").format(n=len(s.timed_out))
        if s.stale:
            text += "\n" + _("{n} file cambiati o eliminati dopo l'indicizzazione: aggiorna l'indice.").format(
                n=len(s.stale))
        if s.truncated:
            text += "\n" + _("Risultati limitati ai primi {n}.").format(n=eng.MAX_RESULTS)
        return text

    def _set_summary(self, text: str) -> None:
        self.summary_text = text
        self.ensure_panel().summary.setText(text)

    # ------------------------------------------------------------- navigazione
    def _view_for(self, hit: AdvHit):
        views = self.ctx.views()
        if hit.doc_key is not None:
            doc = self._docs.get(hit.doc_key)
            for v in views:
                if v.document is doc:
                    return v
        target = os.path.abspath(hit.file)
        for v in views:
            if v.document.path is not None and os.path.abspath(str(v.document.path)) == target:
                return v
        return None

    def _activate(self, view) -> None:
        if view is self.ctx.current_view():
            return
        if view.document.path is not None:
            self.ctx.open_path(str(view.document.path))   # porta in primo piano la scheda esistente
            return
        self.ctx.activate_view(view)

    def go_to(self, index: int):
        if not (0 <= index < len(self.hits)):
            return None
        hit = self.hits[index]
        view = self._view_for(hit)
        if view is None:
            if hit.doc_key is not None and not os.path.exists(hit.file):
                return None   # documento aperto chiuso nel frattempo
            view = self.ctx.open_path(hit.file)
            if view is None:
                return None
        else:
            self._activate(view)
        if hit.attachment:                     # occorrenza in un PDF allegato: si apre in una scheda
            view = self._open_attachment(view, hit.attachment)
            if view is None:
                return None
        if hit.page < 0:
            return view
        page = min(hit.page, view.document.page_count - 1)
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        if nav is not None and hasattr(nav, "record_jump"):
            try:
                nav.record_jump(view)
            except Exception:
                pass
        rects_found = hit.rects
        if not rects_found and hit.kind == eng.KIND_TEXT and hit.found:   # indice: posizioni al clic
            from .engine import SearchOptions, extract_page_text, find_in_page_text
            with ENGINE_LOCK:
                pt = extract_page_text(view.document.pdf[page])
            found = find_in_page_text(pt, hit.found, SearchOptions(case_sensitive=True))
            rects_found = [tuple(r) for r in found[0]] if found else []
        if rects_found:
            rects = [pymupdf.Rect(r) for r in rects_found]
            view.set_search_hits({page: rects}, (page, 0))
            area = pymupdf.Rect(rects[0])
            for r in rects[1:]:
                area |= r
            view.ensure_visible(page, area)
        else:
            view.set_search_hits({}, None)
            view.go_to_page(page)
        return view

    def _open_attachment(self, view, name: str):
        panel = getattr(self.ctx, "viewer_parts", {}).get("attachments")
        if panel is None:
            return None
        panel.set_view(view)
        att = next((a for a in panel.items() if a.key == name), None)
        if att is None:
            return None
        opened = panel.open_item(att)
        return opened if hasattr(opened, "document") else None

    # ------------------------------------------------------------- esportazione
    def export_csv(self, path: str | None = None) -> str | None:
        if not self.hits:
            return None
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta risultati"), _("ricerca.csv"), "CSV (*.csv)", ".csv")
        if not path:
            return None
        data = eng.export_csv(self.hits, _kind_names(), [_("file"), _("pagina"), _("tipo"), _("estratto")])
        try:
            atomic_write(path, data)
        except Exception as e:
            self.ctx.error(_("Esporta risultati"), str(e))
            return None
        self.ctx.status(_("Risultati esportati: {p}").format(p=path))
        return path


def install(ctx: Context) -> AdvancedSearchController:
    add_translations("en", TRANSLATIONS)
    return AdvancedSearchController(ctx)
