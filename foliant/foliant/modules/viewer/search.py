"""Ricerca nel documento (VIS-24): barra Ctrl+F, ricerca incrementale che non
blocca l'interfaccia (poche pagine per volta con un timer, a partire dalla
pagina corrente), evidenziazione, F3/Maiusc+F3, pannello Risultati per pagina.
"""

from __future__ import annotations

import bisect
import html
import time
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAbstractTextDocumentLayout, QAction, QKeySequence, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolBar,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, bind_icon, theme
from foliant.i18n import pgettext

from .engine import (
    PageText, SearchOptions, compile_query, context_snippet, extract_page_text, find_across_pages, find_hits,
    find_in_page_text, page_labels,
)

TRANSLATIONS = {
    "risultati.csv": "results.csv",
    "barra di ricerca\x04Cerca": "Find",
    "Cerca nel documento": "Find in document",
    "Cerca nel documento…": "Find in document…",
    "Risultato successivo": "Next result",
    "Risultato precedente": "Previous result",
    "Maiuscole/minuscole": "Match case",
    "Parola intera": "Whole word",
    "Ignora accenti": "Ignore accents",
    "Chiudi la ricerca": "Close search",
    "Risultati": "Results",
    "{i} di {n}": "{i} of {n}",
    "Nessun risultato": "No results",
    "{n} risultati": "{n} results",
    "1 risultato": "1 result",
    "Ricerca… {p}%": "Searching… {p}%",
    "Pagina {n}": "Page {n}",
    "Pagina {label} ({n})": "Page {label} ({n})",
    "{n} pagine senza testo: esegui l'OCR per cercarvi.": "{n} pages without text: run OCR to search them.",
    "1 pagina senza testo: esegui l'OCR per cercarvi.": "1 page without text: run OCR to search it.",
    "Digita nella barra di ricerca (Ctrl+F).": "Type in the search bar (Ctrl+F).",
    "Nessun documento aperto": "No document open",
    "Vista": "View",
    "Espressione regolare": "Regular expression",
    "Cerca anche nei commenti": "Also search comments",
    "commento": "comment",
    "Espressione non valida: {e}": "Invalid expression: {e}",
    "Esporta i risultati (CSV)…": "Export results (CSV)…",
    "Esporta risultati": "Export results",
    "Risultati esportati: {p}": "Results exported: {p}",
    "pagina": "page",
    "etichetta": "label",
    "prima": "before",
    "trovato": "match",
    "dopo": "after",
}

MAX_HISTORY = 15
TICK_SECONDS = 0.03


@dataclass
class Hit:
    page: int
    rects: list          # pymupdf.Rect, uno per riga
    before: str = ""
    found: str = ""
    after: str = ""
    cont: tuple | None = None     # (pagina successiva, rettangoli) se la frase prosegue
    comment: bool = False         # occorrenza nel testo di un commento


class SearchJob:
    """Ricerca su tutte le pagine, a partire da `start`, con ripresa dall'inizio."""

    def __init__(self, query: str, opts: SearchOptions, page_count: int, start: int, get_comments=None):
        self.get_comments = get_comments   # pagina -> [(testo, riquadro)] se si cerca anche nei commenti
        self.page_count = page_count
        self.query = query
        self.opts = opts
        self.order = list(range(start, page_count)) + list(range(0, start))
        self.pos = 0
        self.results: dict[int, list[Hit]] = {}
        self.no_text = 0

    @property
    def done(self) -> bool:
        return self.pos >= len(self.order)

    @property
    def fraction(self) -> float:
        return self.pos / max(1, len(self.order))

    def step(self, get_text) -> int:
        page = self.order[self.pos]
        self.pos += 1
        pt: PageText = get_text(page)
        comment_hits = []
        if self.get_comments is not None:
            for content, rect in self.get_comments(page):
                cpt = PageText(content, [pymupdf.Rect(rect)] * len(content), [0] * len(content))
                for _rects in find_in_page_text(cpt, self.query, self.opts):
                    comment_hits.append(Hit(page, [pymupdf.Rect(rect)], "", content[:80], "", comment=True))
        if not pt.has_text:
            self.no_text += 1
            if comment_hits:
                self.results[page] = comment_hits
            return page
        hits = []
        for rects, (b, f, a) in find_hits(pt, self.query, self.opts):
            hits.append(Hit(page, rects, b, f, a))
        if page + 1 < self.page_count and not self.opts.regex:
            nxt = get_text(page + 1)
            for ra, rb in find_across_pages(pt, nxt, self.query, self.opts):
                b, f, _a = context_snippet(pt, ra, width=28)
                _b, f2, a2 = context_snippet(nxt, rb, width=28)
                hits.append(Hit(page, ra, b, f + " " + f2, a2, cont=(page + 1, rb)))
        hits.extend(comment_hits)
        if hits:
            self.results[page] = hits
        return page


class HtmlDelegate(QStyledItemDelegate):
    """Voci dei risultati con il termine trovato in grassetto."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(2)
        doc.setHtml(text)
        doc.setTextWidth(max(10, opt.rect.width()))
        painter.save()
        painter.translate(opt.rect.topLeft())
        painter.setClipRect(QRectF(0, 0, opt.rect.width(), opt.rect.height()))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, theme.color("text"))
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(2)
        doc.setHtml(opt.text)
        tree = self.parent()
        width = opt.rect.width()
        if tree is not None and hasattr(tree, "viewport"):
            depth = 1 if index.parent().isValid() else 0
            width = tree.viewport().width() - tree.indentation() * (depth + 1) - 4
        doc.setTextWidth(max(60, width))
        return QSize(int(doc.idealWidth()), int(doc.size().height()) + 4)


class ResultsPanel(QWidget):
    def __init__(self, controller: "SearchController"):
        super().__init__()
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 12, 6)
        title = QLabel(_("Risultati"))
        title.setProperty("heading", True)
        head.addWidget(title)
        head.addStretch(1)
        self.export_button = QToolButton(self)
        bind_icon(self.export_button, "download")
        self.export_button.setToolTip(_("Esporta i risultati (CSV)…"))
        self.export_button.setAutoRaise(True)
        self.export_button.clicked.connect(lambda: self.controller.export_csv())
        head.addWidget(self.export_button)
        layout.addLayout(head)
        self.summary = QLabel(_("Digita nella barra di ricerca (Ctrl+F)."))
        self.summary.setProperty("muted", True)
        self.summary.setWordWrap(True)
        self.summary.setContentsMargins(12, 0, 12, 6)
        layout.addWidget(self.summary)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setItemDelegate(HtmlDelegate(self.tree))
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.itemClicked.connect(self._on_item)
        self.tree.itemActivated.connect(self._on_item)
        layout.addWidget(self.tree, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setProperty("muted", True)
        self.note.setContentsMargins(12, 6, 12, 10)
        self.note.hide()
        layout.addWidget(self.note)
        self._page_items: dict[int, QTreeWidgetItem] = {}
        self._pages: list[int] = []          # pagine con risultati, in ordine

    def set_view(self, view) -> None:
        self.controller.set_view(view)

    def clear(self) -> None:
        self.tree.clear()
        self._page_items.clear()
        self._pages.clear()
        self.note.hide()

    def page_title(self, page: int, count: int) -> str:
        labels = self.controller.labels()
        label = labels[page] if page < len(labels) else ""
        head = (_("Pagina {label} ({n})").format(label=html.escape(label), n=page + 1)
                if label and label != str(page + 1) else _("Pagina {n}").format(n=page + 1))
        return f"<b>{head}</b> &nbsp;<span style='color:{theme.hex_color('text_muted')}'>{count}</span>"

    def add_page(self, page: int, hits: list[Hit], flat_start: int) -> None:
        item = QTreeWidgetItem()
        item.setText(0, self.page_title(page, len(hits)))
        item.setData(0, Qt.ItemDataRole.UserRole, flat_start)
        for n, h in enumerate(hits):
            child = QTreeWidgetItem(item)
            if h.comment:
                child.setText(0, f"<i>[{html.escape(_('commento'))}]</i> " + html.escape(h.found))
            else:
                child.setText(0, "…" + html.escape(h.before) + "<b>" + html.escape(h.found) + "</b>"
                              + html.escape(h.after) + "…")
            child.setData(0, Qt.ItemDataRole.UserRole, flat_start + n)
        # inserimento in ordine di pagina
        pos = bisect.bisect_left(self._pages, page)
        self._pages.insert(pos, page)
        self.tree.insertTopLevelItem(pos, item)
        item.setExpanded(True)
        self._page_items[page] = item

    def renumber(self) -> None:
        """Dopo l'arrivo di nuove pagine gli indici globali cambiano."""
        flat = 0
        for p in self._pages:
            item = self._page_items[p]
            item.setData(0, Qt.ItemDataRole.UserRole, flat)
            for n in range(item.childCount()):
                item.child(n).setData(0, Qt.ItemDataRole.UserRole, flat + n)
            flat += item.childCount()

    def select(self, flat: int) -> None:
        for p in self._pages:
            item = self._page_items[p]
            for n in range(item.childCount()):
                child = item.child(n)
                if child.data(0, Qt.ItemDataRole.UserRole) == flat:
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    return

    def _on_item(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        flat = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(flat, int):
            self.controller.go_to(flat, record=True)


class SearchBar(QToolBar):
    def __init__(self, controller: "SearchController"):
        super().__init__(pgettext("barra di ricerca", "Cerca"))
        self.controller = controller
        self.setObjectName("searchbar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(18, 18))
        icon_label = QLabel()
        icon_label.setContentsMargins(6, 0, 4, 0)
        self.field = QLineEdit(self)
        self.field.setPlaceholderText(_("Cerca nel documento…"))
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(260)
        self.field.setMaximumWidth(420)
        self.field.installEventFilter(controller)
        self.completer = QCompleter([], self.field)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.field.setCompleter(self.completer)
        self.addWidget(self.field)
        self.counter = QLabel("")
        self.counter.setProperty("muted", True)
        self.counter.setMinimumWidth(110)
        self.counter.setContentsMargins(8, 0, 8, 0)
        self.addWidget(self.counter)
        self.act_prev = QAction(_("Risultato precedente"), self)
        bind_icon(self.act_prev, "chevron-up")
        self.act_prev.triggered.connect(controller.previous)
        self.act_next = QAction(_("Risultato successivo"), self)
        bind_icon(self.act_next, "chevron-down")
        self.act_next.triggered.connect(controller.next)
        self.addAction(self.act_prev)
        self.addAction(self.act_next)
        self.addSeparator()
        self.opt_case = self._toggle(_("Maiuscole/minuscole"), "case-sensitive", "viewer/search_case")
        self.opt_word = self._toggle(_("Parola intera"), "whole-word", "viewer/search_word")
        self.opt_accents = self._toggle(_("Ignora accenti"), "a-large-small", "viewer/search_accents")
        self.opt_regex = self._toggle(_("Espressione regolare"), "regex", "viewer/search_regex")
        self.opt_comments = self._toggle(_("Cerca anche nei commenti"), "message-square-text", "viewer/search_comments")
        spacer = QWidget(self)
        from PySide6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        self.act_close = QAction(_("Chiudi la ricerca"), self)
        bind_icon(self.act_close, "x")
        self.act_close.triggered.connect(controller.close)
        self.addAction(self.act_close)
        self.field.textChanged.connect(controller.schedule)

    def _toggle(self, text: str, icon_name: str, key: str) -> QAction:
        act = QAction(text, self)
        bind_icon(act, icon_name)
        act.setCheckable(True)
        act.setToolTip(text)
        act.setChecked(self.controller.ctx.settings().value(key, False) in (True, "true"))
        act.toggled.connect(lambda on, k=key: (self.controller.ctx.settings().setValue(k, bool(on)),
                                               self.controller.restart()))
        self.addAction(act)
        return act

    def set_error(self, error: bool) -> None:
        self.field.setStyleSheet(f"QLineEdit {{ border-color: {theme.hex_color('danger')}; }}" if error else "")


class SearchController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.view: DocumentView | None = None
        self.job: SearchJob | None = None
        self.hits: list[Hit] = []
        self._hit_pages: list[int] = []      # pagina di ogni risultato (ricerca binaria)
        self._renumber_pending = False
        self.current: int = -1
        self._texts: dict[int, PageText] = {}
        self._texts_version = -1
        self._labels: list[str] | None = None
        self._pending_select = True
        self._restart_pending = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._work)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.restart)

        self.bar = SearchBar(self)
        w = ctx.window
        w.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self.bar)
        self.bar.hide()
        self.panel = ResultsPanel(self)
        ctx.add_sidebar_panel("search", pgettext("barra di ricerca", "Cerca"), "search", self.panel, order=30)

        ctx.add_view_menu_provider(self._menu_provider)
        self.act_find = ctx.action(_("Cerca nel documento"), "search", self.open, shortcut=QKeySequence.StandardKey.Find)
        # UX-18: F3 / Ctrl+G successivo, Maiusc+F3 / Ctrl+Maiusc+G precedente
        self.act_next = ctx.action(_("Risultato successivo"), "chevron-down", self.next, shortcut="F3")
        self.act_prev = ctx.action(_("Risultato precedente"), "chevron-up", self.previous, shortcut="Shift+F3")
        self.act_next.setShortcuts([QKeySequence("F3"), QKeySequence("Ctrl+G")])
        self.act_prev.setShortcuts([QKeySequence("Shift+F3"), QKeySequence("Ctrl+Shift+G")])
        from foliant.ui.dialogs import key_text
        for act in (self.act_next, self.act_prev):
            keys = ", ".join(key_text(s) for s in act.shortcuts())
            act.setToolTip(f"{act.text()} ({keys})")
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_separator()
            mode.add_action(self.act_find)
        ctx.add_menu_action(self.act_find, _("Vista"))
        ctx.currentViewChanged.connect(self.set_view)
        ctx.documentChanged.connect(self._on_changed)
        self._refresh_completer()

    # ------------------------------------------------------------------ vista
    def set_view(self, view: DocumentView | None) -> None:
        if view is self.view:
            return
        if self.view is not None and not self.view.document.closed:
            self.view.set_search_hits({})
        self.view = view
        self._texts.clear()
        self._labels = None
        if self.bar.isVisible() and view is not None and self.bar.field.text().strip():
            self.restart()
        else:
            self._reset_results()

    def _on_changed(self, view: DocumentView) -> None:
        if view is not self.view:
            return
        self._texts.clear()
        self._labels = None
        if (self.hits or self.job is not None) and not self._restart_pending:
            # una sola nuova ricerca per giro di eventi, anche dopo molte modifiche di fila
            self._restart_pending = True
            QTimer.singleShot(0, self._restart_after_change)

    def _restart_after_change(self) -> None:
        if not self._restart_pending:
            return
        self._restart_pending = False
        try:
            self._pending_select = False
            self.restart()
        except RuntimeError:
            pass     # finestra già distrutta

    def labels(self) -> list[str]:
        if self.view is None:
            return []
        if self._labels is None:
            with ENGINE_LOCK:
                self._labels = page_labels(self.view.document.pdf)
        return self._labels

    def page_comments(self, page: int) -> list[tuple[str, pymupdf.Rect]]:
        """Testo dei commenti della pagina con il loro riquadro (chiamata con ENGINE_LOCK)."""
        out = []
        try:
            for annot in self.view.document.pdf[page].annots() or []:
                content = (annot.info or {}).get("content", "") or ""
                if content.strip():
                    out.append((content, pymupdf.Rect(annot.rect)))
        except Exception:
            pass
        return out

    def page_text(self, page: int) -> PageText:
        view = self.view
        if self._texts_version != view.document.version:
            self._texts.clear()
            self._texts_version = view.document.version
        pt = self._texts.get(page)
        if pt is None:
            with ENGINE_LOCK:
                try:
                    pt = extract_page_text(view.document.pdf[page])
                except Exception:
                    pt = PageText()
            self._texts[page] = pt
        return pt

    # ------------------------------------------------------------------ comandi
    def _menu_provider(self, view, _page_event, menu) -> None:
        """UX-07: «Cerca» nel menu contestuale del testo selezionato (precompila la barra)."""
        if not view.selected_text().strip():
            return
        act = menu.addAction(pgettext("barra di ricerca", "Cerca"), self.open)
        bind_icon(act, "search")

    def open(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        self.set_view(view)
        sel = " ".join(view.selected_text().split()) if view.selected_text() else ""
        self.bar.show()
        if sel and len(sel) < 200:
            self.bar.field.setText(sel)
        self.bar.field.setFocus()
        self.bar.field.selectAll()
        if self.bar.field.text().strip() and not self.hits and self.job is None:
            self.restart()

    def close(self) -> None:
        self._timer.stop()
        self._debounce.stop()
        self.job = None
        self.bar.hide()
        if self.view is not None and not self.view.document.closed:
            self.view.set_search_hits({})
            self.view.setFocus()

    def schedule(self, *_a) -> None:
        self._debounce.start()

    def options(self) -> SearchOptions:
        return SearchOptions(case_sensitive=self.bar.opt_case.isChecked(),
                             whole_word=self.bar.opt_word.isChecked(),
                             ignore_accents=self.bar.opt_accents.isChecked(),
                             regex=self.bar.opt_regex.isChecked())

    def search(self, text: str, *, case_sensitive: bool | None = None, whole_word: bool | None = None,
               ignore_accents: bool | None = None, regex: bool | None = None, comments: bool | None = None,
               wait: bool = True) -> int:
        """Avvia una ricerca (usata anche dai test); con `wait` la completa subito."""
        for act, value in ((self.bar.opt_case, case_sensitive), (self.bar.opt_word, whole_word),
                           (self.bar.opt_accents, ignore_accents), (self.bar.opt_regex, regex),
                           (self.bar.opt_comments, comments)):
            if value is not None:
                act.blockSignals(True)
                act.setChecked(value)
                act.blockSignals(False)
        self.bar.field.blockSignals(True)
        self.bar.field.setText(text)
        self.bar.field.blockSignals(False)
        if self.ctx.current_view() is not None:
            self.set_view(self.ctx.current_view())
        self.bar.show()
        self.restart()
        if wait:
            self.finish()
        return len(self.hits)

    def _reset_results(self) -> None:
        self._timer.stop()
        self.job = None
        self.hits = []
        self._hit_pages = []
        self._renumber_pending = False
        self.current = -1
        self.panel.clear()
        self.bar.set_error(False)
        self.bar.counter.setText("")
        self.panel.summary.setText(_("Digita nella barra di ricerca (Ctrl+F).") if self.view is not None
                                   else _("Nessun documento aperto"))
        if self.view is not None and not self.view.document.closed:
            self.view.set_search_hits({})

    def restart(self) -> None:
        self._debounce.stop()
        text = self.bar.field.text().strip()
        keep_page = self.current_hit_page()
        self._reset_results()
        if self.view is None or not text:
            return
        self._remember(text)
        start = keep_page if keep_page is not None else self.view.current_page
        try:
            compile_query(text, self.options())
        except ValueError as e:
            self.bar.set_error(True)
            self.bar.counter.setText(_("Espressione non valida: {e}").format(e=e)[:60])
            self.bar.counter.setToolTip(str(e))
            return
        self.job = SearchJob(text, self.options(), self.view.page_count, start,
                             self.page_comments if self.bar.opt_comments.isChecked() else None)
        self._pending_select = True
        self._timer.start(0)

    def current_hit_page(self) -> int | None:
        return None

    def _work(self) -> None:
        job = self.job
        if job is None or self.view is None or self.view.document.closed:
            return
        deadline = time.perf_counter() + TICK_SECONDS
        changed = False
        while not job.done and time.perf_counter() < deadline:
            if not ENGINE_LOCK.acquire(blocking=False):
                self._timer.start(40)
                break
            try:
                page = job.step(self.page_text)
            finally:
                ENGINE_LOCK.release()
            if page in job.results:
                self._add_page(page, job.results[page])
                changed = True
        if changed:
            self._renumber()
            self._update_highlights()
        self._update_counter()
        if not job.done:
            if not self._timer.isActive():
                self._timer.start(0)
        else:
            self._finished()

    def finish(self) -> None:
        """Completa subito la ricerca in corso (test)."""
        self._restart_after_change()
        job = self.job
        while job is not None and not job.done:
            with ENGINE_LOCK:
                page = job.step(self.page_text)
            if page in job.results:
                self._add_page(page, job.results[page])
        if job is not None:
            self._renumber()
            self._update_highlights()
            self._finished()

    def _add_page(self, page: int, hits: list[Hit]) -> None:
        """Aggiunge i risultati di una pagina; la numerazione dell'elenco si aggiorna una volta
        per giro di lavoro (`_renumber`), non a ogni pagina."""
        pos = bisect.bisect_left(self._hit_pages, page)
        self.hits[pos:pos] = hits
        self._hit_pages[pos:pos] = [page] * len(hits)
        if 0 <= self.current and pos <= self.current:
            self.current += len(hits)
        self.panel.add_page(page, hits, pos)
        self._renumber_pending = True
        if self._pending_select:
            self._pending_select = False
            self.current = pos
            self.go_to(pos, record=False)

    def _renumber(self) -> None:
        if self._renumber_pending:
            self._renumber_pending = False
            self.panel.renumber()

    def _finished(self) -> None:
        job = self.job
        self._timer.stop()
        self._update_counter(final=True)
        if job is not None and job.no_text:
            n = job.no_text
            self.panel.note.setText(_("1 pagina senza testo: esegui l'OCR per cercarvi.") if n == 1 else
                                    _("{n} pagine senza testo: esegui l'OCR per cercarvi.").format(n=n))
            self.panel.note.show()
        self.job = None

    def _update_counter(self, final: bool = False) -> None:
        n = len(self.hits)
        job = self.job
        if not final and job is not None and not job.done:
            self.bar.counter.setText(_("Ricerca… {p}%").format(p=int(job.fraction * 100)))
        elif n == 0:
            self.bar.counter.setText(_("Nessun risultato"))
        else:
            self.bar.counter.setText(_("{i} di {n}").format(i=self.current + 1, n=n))
        self.bar.set_error(final and n == 0 and bool(self.bar.field.text().strip()))
        if n == 0:
            self.panel.summary.setText(_("Nessun risultato") if final else "")
        else:
            self.panel.summary.setText(_("1 risultato") if n == 1 else _("{n} risultati").format(n=n))

    def _update_highlights(self) -> None:
        view = self.view
        if view is None:
            return
        hits: dict[int, list] = {}
        current = None
        for i, h in enumerate(self.hits):
            lst = hits.setdefault(h.page, [])
            if i == self.current:
                current = (h.page, len(lst))
            lst.extend(h.rects)
        for h in self.hits:          # parti delle frasi che proseguono sulla pagina successiva
            if h.cont is not None:
                hits.setdefault(h.cont[0], []).extend(h.cont[1])
        view.set_search_hits(hits, current)

    def go_to(self, index: int, record: bool = False) -> None:
        if not self.hits or self.view is None:
            return
        index %= len(self.hits)
        view = self.view
        hit = self.hits[index]
        if record:
            nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
            if nav is not None:
                nav.record_jump(view)
        self.current = index
        self._update_highlights()
        rect = pymupdf.Rect(hit.rects[0])
        for r in hit.rects[1:]:
            rect |= r
        view.ensure_visible(hit.page, rect)
        self.panel.select(index)
        self._update_counter(final=self.job is None)

    def next(self) -> None:
        if not self.bar.isVisible():
            self.open()
            return
        if self.hits:
            self.go_to(self.current + 1)

    def previous(self) -> None:
        if not self.bar.isVisible():
            self.open()
            return
        if self.hits:
            self.go_to(self.current - 1)

    def export_csv(self, path: str | None = None) -> str | None:
        if not self.hits or self.view is None:
            return None
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta risultati"), _("risultati.csv"), "CSV (*.csv)", ".csv")
        if not path:
            return None
        import csv
        import io
        from foliant.api import atomic_write
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([_("pagina"), _("etichetta"), _("prima"), _("trovato"), _("dopo")])
        labels = self.labels()
        for h in self.hits:
            writer.writerow([h.page + 1, labels[h.page] if h.page < len(labels) else "", h.before, h.found, h.after])
        try:
            atomic_write(path, buf.getvalue().encode("utf-8-sig"))
        except Exception as e:
            self.ctx.error(_("Esporta risultati"), str(e))
            return None
        self.ctx.status(_("Risultati esportati: {p}").format(p=path))
        return path

    # --------------------------------------------------------------- cronologia
    def history(self) -> list[str]:
        value = self.ctx.settings().value("viewer/search_history", []) or []
        if isinstance(value, str):
            value = [value]
        return [v for v in value if isinstance(v, str)]

    def _remember(self, text: str) -> None:
        items = [t for t in self.history() if t != text]
        items.insert(0, text)
        self.ctx.settings().setValue("viewer/search_history", items[:MAX_HISTORY])
        self._refresh_completer()

    def _refresh_completer(self) -> None:
        from PySide6.QtCore import QStringListModel
        self.bar.completer.setModel(QStringListModel(self.history(), self.bar.completer))

    # ------------------------------------------------------------------ tasti
    def eventFilter(self, obj, ev) -> bool:
        bar = getattr(self, "bar", None)
        if bar is not None and obj is bar.field and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._debounce.isActive() or (not self.hits and self.job is None):
                    self.restart()
                elif ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.previous()
                else:
                    self.next()
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return False


def install(ctx: Context) -> SearchController:
    add_translations("en", TRANSLATIONS)
    return SearchController(ctx)
