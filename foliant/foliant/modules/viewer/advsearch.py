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
from .advsearch_engine import AdvHit, AdvOptions, AdvSummary

SCOPE_CURRENT = "current"
SCOPE_OPEN = "open"
SCOPE_FOLDER = "folder"

TRANSLATIONS = {
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
        self.mode = QComboBox(self)
        self.mode.addItem(_("Frase esatta"), eng.PHRASE)
        self.mode.addItem(_("Tutte le parole (stessa pagina)"), eng.ALL_WORDS)
        self.mode.addItem(_("Almeno una parola"), eng.ANY_WORD)
        self.mode.addItem(_("Espressione regolare"), eng.REGEX)
        self.mode.currentIndexChanged.connect(lambda *_a: controller.validate())
        form.addRow(_("Modalità"), self.mode)
        opts = QHBoxLayout()
        self.opt_case = QCheckBox(_("Maiuscole/minuscole"), self)
        self.opt_word = QCheckBox(_("Parola intera"), self)
        self.opt_accents = QCheckBox(_("Ignora accenti"), self)
        for cb in (self.opt_case, self.opt_word, self.opt_accents):
            opts.addWidget(cb)
            cb.toggled.connect(lambda *_a: controller.validate())
        opts.addStretch(1)
        form.addRow(_("Opzioni"), opts)
        inc = QHBoxLayout()
        self.inc_comments = QCheckBox(_("Commenti"), self)
        self.inc_bookmarks = QCheckBox(_("Segnalibri"), self)
        self.inc_fields = QCheckBox(_("Campi modulo"), self)
        self.inc_metadata = QCheckBox(_("Metadati"), self)
        for cb in (self.inc_comments, self.inc_bookmarks, self.inc_fields, self.inc_metadata):
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
                    child = QTreeWidgetItem(pitem)
                    prefix = "" if h.kind == eng.KIND_TEXT else \
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
                          fields=p.inc_fields.isChecked(), metadata=p.inc_metadata.isChecked())

    def configure(self, query: str | None = None, *, scope: str | None = None, mode: str | None = None,
                  case_sensitive: bool | None = None, whole_word: bool | None = None,
                  ignore_accents: bool | None = None, comments: bool | None = None, bookmarks: bool | None = None,
                  fields: bool | None = None, metadata: bool | None = None, folder: str | None = None,
                  recursive: bool | None = None) -> None:
        p = self.ensure_panel()
        if query is not None:
            p.field.setText(query)
        if scope is not None:
            p.scope.setCurrentIndex(p.scope.findData(scope))
        if mode is not None:
            p.mode.setCurrentIndex(p.mode.findData(mode))
        for cb, value in ((p.opt_case, case_sensitive), (p.opt_word, whole_word), (p.opt_accents, ignore_accents),
                          (p.inc_comments, comments), (p.inc_bookmarks, bookmarks), (p.inc_fields, fields),
                          (p.inc_metadata, metadata), (p.recursive, recursive)):
            if value is not None:
                cb.setChecked(value)
        if folder is not None:
            self.set_folder(folder)

    def set_folder(self, folder: str | None) -> None:
        self.folder = folder
        p = self.ensure_panel()
        p.folder_label.setText(folder or _("Nessuna cartella scelta"))
        p.folder_label.setToolTip(folder or "")

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
            if scope == SCOPE_FOLDER:
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
        w = self.ctx.window
        tabs = getattr(w, "tabs", None)
        stack = getattr(w, "docs_stack", None)
        if stack is not None and tabs is not None:
            stack.setCurrentWidget(tabs)
        if tabs is not None:
            tabs.setCurrentWidget(view)

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
        if hit.page < 0:
            return view
        page = min(hit.page, view.document.page_count - 1)
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        if nav is not None and hasattr(nav, "record_jump"):
            try:
                nav.record_jump(view)
            except Exception:
                pass
        if hit.rects:
            rects = [pymupdf.Rect(r) for r in hit.rects]
            view.set_search_hits({page: rects}, (page, 0))
            area = pymupdf.Rect(rects[0])
            for r in rects[1:]:
                area |= r
            view.ensure_visible(page, area)
        else:
            view.set_search_hits({}, None)
            view.go_to_page(page)
        return view

    # ------------------------------------------------------------- esportazione
    def export_csv(self, path: str | None = None) -> str | None:
        if not self.hits:
            return None
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta risultati"), "ricerca.csv", "CSV (*.csv)", ".csv")
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
