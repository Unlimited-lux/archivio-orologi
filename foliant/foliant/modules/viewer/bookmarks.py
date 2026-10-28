"""Pannello Segnalibri: consultazione (VIS-15) e modifica (VIS-16).

Ogni modifica riscrive l'albero con `Document.change()` ed è quindi un passo
di Annulla/Ripeti.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton, QTreeWidget,
    QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, bind_icon, follow_link, theme

from . import engine as E

TRANSLATIONS = {
    "Segnalibri": "Bookmarks",
    "Aggiungi segnalibro": "Add bookmark",
    "Rinomina": "Rename",
    "Elimina": "Delete",
    "Sposta su": "Move up",
    "Sposta giù": "Move down",
    "Aumenta livello": "Indent",
    "Riduci livello": "Outdent",
    "Espandi tutto": "Expand all",
    "Comprimi tutto": "Collapse all",
    "Filtra i segnalibri…": "Filter bookmarks…",
    "Il documento non ha segnalibri.": "The document has no bookmarks.",
    "Usa «Aggiungi segnalibro» (Ctrl+B) per crearne uno sulla vista corrente.":
        "Use “Add bookmark” (Ctrl+B) to create one on the current view.",
    "Nessun documento aperto": "No document open",
    "Pagina {n}": "Page {n}",
    "(senza titolo)": "(untitled)",
    "Destinazione non valida": "Invalid destination",
    "La destinazione di questo segnalibro non esiste più nel documento.":
        "This bookmark's destination no longer exists in the document.",
    "Elimina segnalibro": "Delete bookmark",
    "Eliminare «{t}» e i suoi {n} segnalibri figli?": "Delete “{t}” and its {n} child bookmarks?",
    "Rinomina segnalibro": "Rename bookmark",
    "Sposta segnalibro": "Move bookmark",
    "Modifica segnalibro": "Edit bookmark",
    "Imposta destinazione sulla vista corrente": "Set destination to current view",
    "Apri un indirizzo web…": "Open a web address…",
    "Crea pagina di sommario": "Create table of contents page",
    "Sommario": "Contents",
    "Indirizzo da aprire:": "Address to open:",
    "Grassetto": "Bold",
    "Corsivo": "Italic",
    "Colore…": "Colour…",
    "Colore predefinito": "Default colour",
    "Evidenzia il segnalibro corrente": "Highlight current bookmark",
    "Il documento non consente modifiche: segnalibri in sola lettura.":
        "The document does not allow changes: bookmarks are read-only.",
    "Vai": "Go",
    "Strumenti dei segnalibri": "Bookmark tools",
    "Esporta come testo…": "Export as text…",
    "Importa da testo…": "Import from text…",
    "Genera dai titoli del testo": "Generate from text headings",
    "Genera ogni N pagine…": "Generate every N pages…",
    "Verifica segnalibri": "Check bookmarks",
    "Esporta segnalibri": "Export bookmarks",
    "Importa segnalibri": "Import bookmarks",
    "Genera segnalibri": "Generate bookmarks",
    "Testo (*.txt)": "Text (*.txt)",
    "Segnalibri esportati: {p}": "Bookmarks exported: {p}",
    "Impossibile leggere il file: {e}": "Cannot read the file: {e}",
    "Nessun segnalibro valido nel file.": "No valid bookmarks in the file.",
    "{n} righe scartate (pagina fuori intervallo o formato non valido):": "{n} lines discarded (page out of range or invalid format):",
    "Sostituire i segnalibri esistenti? Scegli No per aggiungerli in fondo.":
        "Replace the existing bookmarks? Choose No to append them.",
    "Nessun titolo riconosciuto: il documento potrebbe essere una scansione (usa l'OCR).":
        "No headings recognised: the document may be a scan (use OCR).",
    "Trovati {n} titoli. Creare i segnalibri?": "Found {n} headings. Create the bookmarks?",
    "Ogni quante pagine?": "Every how many pages?",
    "Genera da un'espressione regolare…": "Generate from a regular expression…",
    "Espressione regolare delle righe da usare come titoli:": "Regular expression of the lines to use as titles:",
    "Espressione regolare non valida: {e}": "Invalid regular expression: {e}",
    "Nessuna riga corrisponde all'espressione.": "No line matches the expression.",
    "Pagine {a}-{b}": "Pages {a}-{b}",
    "Nessun problema trovato nei segnalibri.": "No problems found in the bookmarks.",
    "{n} segnalibri con problemi:": "{n} bookmarks with problems:",
    "destinazione non valida": "invalid destination",
    "senza titolo": "untitled",
    "duplicato": "duplicate",
    "Ricerca dei titoli…": "Looking for headings…",
    "Vista": "View",
}


def _can_modify(view: DocumentView) -> bool:
    """Permesso di modifica effettivo (anche dopo una modifica, quando la copia
    interna non è più cifrata)."""
    return view.document.allows(pymupdf.PDF_PERM_MODIFY)


class BookmarkTree(QTreeWidget):
    def __init__(self, panel: "BookmarksPanel"):
        super().__init__(panel)
        self.panel = panel
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setExpandsOnDoubleClick(False)
        self.setStyleSheet("QTreeWidget::item { padding: 3px 2px; }")

    def dropEvent(self, event) -> None:
        src = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if src is None or target is None or not self.panel.editable:
            event.ignore()
            return
        pos = self.dropIndicatorPosition()
        i, t = src.data(0, Qt.ItemDataRole.UserRole), target.data(0, Qt.ItemDataRole.UserRole)
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            self.panel.move_to(i, t, "child")
        elif pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
            self.panel.move_to(i, t, "before")
        else:
            self.panel.move_to(i, t, "after")


class BookmarksPanel(QWidget):
    def __init__(self, ctx: Context):
        super().__init__()
        self.ctx = ctx
        self.view: DocumentView | None = None
        self.items: list[E.Bookmark] = []
        self._tree_items: list[QTreeWidgetItem] = []
        self._version = -1
        self._current: int | None = None
        self._rebuilding = False
        self.editable = True
        self.highlight_current = ctx.settings().value("viewer/highlight_bookmark", True) not in (False, "false")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 8, 4)
        title = QLabel(_("Segnalibri"))
        title.setProperty("heading", True)
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 0, 8, 4)
        bar.setSpacing(0)
        self.actions: dict[str, QAction] = {}
        for key, text, icon_name, slot, shortcut in (
            ("add", _("Aggiungi segnalibro"), "bookmark-plus", lambda: self.add_bookmark(edit=True), None),
            ("rename", _("Rinomina"), "pencil", self.start_rename, "F2"),
            ("delete", _("Elimina"), "trash-2", self.delete_selected, None),
            ("up", _("Sposta su"), "arrow-up", lambda: self._op("up"), None),
            ("down", _("Sposta giù"), "arrow-down", lambda: self._op("down"), None),
            ("indent", _("Aumenta livello"), "indent-increase", lambda: self._op("indent"), None),
            ("outdent", _("Riduci livello"), "indent-decrease", lambda: self._op("outdent"), None),
            ("expand", _("Espandi tutto"), "chevrons-up-down", lambda: self.tree.expandAll(), None),
            ("collapse", _("Comprimi tutto"), "chevrons-down-up", lambda: self.tree.collapseAll(), None),
        ):
            act = QAction(text, self)
            bind_icon(act, icon_name)
            act.setToolTip(text)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                self.addAction(act)
            self.actions[key] = act
            if key not in ("add", "rename", "delete"):
                continue      # azioni secondarie nel menu «…»
            btn = QToolButton(self)
            btn.setDefaultAction(act)
            btn.setAutoRaise(True)
            btn.setIconSize(QSize(18, 18))
            bar.addWidget(btn)
        bar.addStretch(1)
        self.tools_button = QToolButton(self)
        bind_icon(self.tools_button, "ellipsis")
        self.tools_button.setToolTip(_("Strumenti dei segnalibri"))
        self.tools_button.setAutoRaise(True)
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_button.setIconSize(QSize(18, 18))
        tools = QMenu(self.tools_button)
        for key in ("up", "down", "indent", "outdent"):
            tools.addAction(self.actions[key])
        tools.addSeparator()
        tools.addAction(self.actions["expand"])
        tools.addAction(self.actions["collapse"])
        tools.addSeparator()
        tools.addAction(_("Esporta come testo…"), self.export_text)
        self.act_import = tools.addAction(_("Importa da testo…"), self.import_text)
        tools.addSeparator()
        self.act_gen_headings = tools.addAction(_("Genera dai titoli del testo"), lambda: self.generate("headings"))
        self.act_gen_every = tools.addAction(_("Genera ogni N pagine…"), lambda: self.generate("every"))
        self.act_gen_regex = tools.addAction(_("Genera da un'espressione regolare…"), lambda: self.generate("regex"))
        tools.addSeparator()
        tools.addAction(_("Verifica segnalibri"), self.verify)
        self.act_toc_page = tools.addAction(_("Crea pagina di sommario"), self.create_toc_page)
        self.tools_button.setMenu(tools)
        bar.addWidget(self.tools_button)
        layout.addLayout(bar)

        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText(_("Filtra i segnalibri…"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.apply_filter)
        fl = QHBoxLayout()
        fl.setContentsMargins(8, 0, 8, 6)
        fl.addWidget(self.filter)
        layout.addLayout(fl)

        self.empty = QLabel(self)
        self.empty.setWordWrap(True)
        self.empty.setProperty("muted", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.empty.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.empty, 1)   # senza albero occupa lo spazio: il titolo resta in alto

        self.tree = BookmarkTree(self)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemActivated.connect(self._on_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(lambda it: None)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(lambda *_a: self._update_actions())
        layout.addWidget(self.tree, 1)
        del_act = QAction(self)
        del_act.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        del_act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        del_act.triggered.connect(self.delete_selected)
        self.tree.addAction(del_act)
        theme.on_change(self._restyle)
        ctx.documentAccessChanged.connect(self._on_access)
        self._update_empty()

    def _on_access(self, view) -> None:
        """Permessi cambiati (sblocco con password o blocco): segnalibri modificabili o no."""
        if view is self.view:
            self.reload()

    # ------------------------------------------------------------------ vista
    def set_view(self, view: DocumentView | None) -> None:
        if view is self.view:
            return
        if self.view is not None:
            for sig, slot in ((self.view.currentPageChanged, self._on_page),
                              (self.view.contentChanged, self.reload)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.view = view
        if view is not None:
            view.currentPageChanged.connect(self._on_page)
            view.contentChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        view = self.view
        if view is None or view.document.closed:
            self.items = []
            self.editable = False
        else:
            with ENGINE_LOCK:
                self.items = E.read_outline(view.document.pdf)
            self.editable = _can_modify(view)
            self._version = view.document.version
        self._rebuild()

    def _rebuild(self, select: int | None = None) -> None:
        if select is None:
            cur = self.tree.currentItem()
            select = cur.data(0, Qt.ItemDataRole.UserRole) if cur is not None else None
        self._rebuilding = True
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self._tree_items = []
            stack: list[tuple[int, QTreeWidgetItem | None]] = [(0, None)]
            for i, b in enumerate(self.items):
                while stack and stack[-1][0] >= b.level:
                    stack.pop()
                parent = stack[-1][1] if stack else None
                item = QTreeWidgetItem(parent if parent is not None else self.tree)
                item.setData(0, Qt.ItemDataRole.UserRole, i)
                self._decorate(item, b)
                self._tree_items.append(item)
                stack.append((b.level, item))
            counts = E.children_counts(self.items)
            for i, (item, b) in enumerate(zip(self._tree_items, self.items)):
                if counts[i]:
                    item.setExpanded(not b.collapsed)
            if select is not None and 0 <= select < len(self._tree_items):
                self.tree.setCurrentItem(self._tree_items[select])
        finally:
            self.tree.setUpdatesEnabled(True)
            self._rebuilding = False
        self._current = None
        self._on_page(self.view.current_page if self.view is not None else 0)
        self.apply_filter(self.filter.text())
        self._update_empty()
        self._update_actions()

    def _decorate(self, item: QTreeWidgetItem, b: E.Bookmark) -> None:
        item.setText(0, b.title or _("(senza titolo)"))
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.editable:
            flags |= Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
        item.setFlags(flags)
        font = QFont(self.tree.font())
        font.setBold(b.bold)
        font.setItalic(b.italic or self._broken(b))
        item.setFont(0, font)
        if self._broken(b):
            item.setForeground(0, QBrush(theme.color("text_muted")))
            item.setToolTip(0, _("Destinazione non valida"))
        else:
            if b.color:
                c = QColor.fromRgbF(*[max(0.0, min(1.0, float(x))) for x in b.color[:3]])
                item.setForeground(0, QBrush(c))
            else:
                item.setForeground(0, QBrush(theme.color("text")))
            item.setToolTip(0, f"{b.title}\n{_('Pagina {n}').format(n=b.page + 1)}")

    def _broken(self, b: E.Bookmark) -> bool:
        if b.dest.get("kind", pymupdf.LINK_GOTO) not in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED, pymupdf.LINK_NONE):
            return False
        n = self.view.page_count if self.view is not None else 0
        return not (0 <= b.page < n)

    def _restyle(self) -> None:
        was, self._rebuilding = self._rebuilding, True
        try:
            for item, b in zip(self._tree_items, self.items):
                self._decorate(item, b)
        finally:
            self._rebuilding = was
        self._paint_current()

    def _update_empty(self) -> None:
        if self.view is None:
            self.empty.setText(_("Nessun documento aperto"))
        else:
            self.empty.setText(_("Il documento non ha segnalibri.") + "\n\n"
                               + _("Usa «Aggiungi segnalibro» (Ctrl+B) per crearne uno sulla vista corrente."))
        has = bool(self.items)
        self.empty.setVisible(not has)
        self.tree.setVisible(has)
        self.filter.setVisible(has)

    def _update_actions(self) -> None:
        has_view = self.view is not None
        sel = self.selected_index()
        for key, act in self.actions.items():
            if key == "add":
                act.setEnabled(has_view and self.editable)
            elif key in ("expand", "collapse"):
                act.setEnabled(bool(self.items))
            else:
                act.setEnabled(has_view and self.editable and sel is not None)
        for act in (getattr(self, "act_import", None), getattr(self, "act_gen_headings", None),
                    getattr(self, "act_gen_every", None), getattr(self, "act_gen_regex", None),
                    getattr(self, "act_toc_page", None)):
            if act is not None:
                act.setEnabled(has_view and self.editable)
        tip = None if self.editable or not has_view else _("Il documento non consente modifiche: segnalibri in sola lettura.")
        for key in ("add", "rename", "delete", "up", "down", "indent", "outdent"):
            self.actions[key].setToolTip(tip or self.actions[key].text())

    def selected_index(self) -> int | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        i = item.data(0, Qt.ItemDataRole.UserRole)
        return i if isinstance(i, int) and 0 <= i < len(self.items) else None

    def item(self, index: int) -> QTreeWidgetItem:
        return self._tree_items[index]

    # ------------------------------------------------------------- navigazione
    def _on_clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        i = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(i, int):
            self.follow(i)

    def follow(self, index: int) -> bool:
        view = self.view
        if view is None or not (0 <= index < len(self.items)):
            return False
        b = self.items[index]
        kind = b.dest.get("kind", pymupdf.LINK_GOTO)
        if kind not in (pymupdf.LINK_GOTO, pymupdf.LINK_NONE) and kind != pymupdf.LINK_NAMED:
            follow_link(view, dict(b.dest))
            return True
        if not (0 <= b.page < view.page_count):
            self.ctx.status(_("La destinazione di questo segnalibro non esiste più nel documento."))
            return False
        zoom = float(b.dest.get("zoom") or 0)
        to = b.dest.get("to")
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        st = None
        if nav is not None:
            nav.record_jump(view)
            st = nav.state(view)
            st.suppress += 1
        try:
            if zoom > 0:
                view.set_zoom(zoom)
            view.go_to_page(b.page)
            if isinstance(to, pymupdf.Point) and (to.x or to.y):
                p = view.page_transform(b.page).map(QPointF(to.x, to.y))
                view.verticalScrollBar().setValue(int(round(view.verticalScrollBar().value() + p.y())))
                if zoom > 0:
                    view.horizontalScrollBar().setValue(int(round(view.horizontalScrollBar().value() + p.x())))
        finally:
            if st is not None:
                st.suppress -= 1
        return True

    def _on_page(self, page: int) -> None:
        if not self.highlight_current or not self.items:
            self._current = None
            return
        cur = E.current_bookmark(self.items, page)
        if cur != self._current:
            self._current = cur
            self._paint_current()

    def _paint_current(self) -> None:
        accent = QBrush(theme.color("accent_soft"))
        was, self._rebuilding = self._rebuilding, True
        try:
            for i, item in enumerate(self._tree_items):
                item.setBackground(0, accent if i == self._current else QBrush())
        finally:
            self._rebuilding = was
        if self._current is not None and self._current < len(self._tree_items):
            self.tree.scrollToItem(self._tree_items[self._current])

    def current_index(self) -> int | None:
        return self._current

    def apply_filter(self, text: str) -> None:
        text = (text or "").strip().casefold()
        if not text:
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                it.value().setHidden(False)
                it += 1
            return
        for item in self._tree_items:
            item.setHidden(True)
        for item in self._tree_items:
            if text in item.text(0).casefold():
                node = item
                while node is not None:
                    node.setHidden(False)
                    node.setExpanded(True)
                    node = node.parent()

    # ---------------------------------------------------------------- modifica
    def _apply(self, label: str, new_items: list[E.Bookmark], select: int | None = None) -> bool:
        view = self.view
        if view is None or not self.editable:
            return False
        try:
            with view.document.change(label) as pdf:
                E.write_outline(pdf, new_items)
        except Exception as e:
            self.ctx.error(_("Segnalibri"), str(e))
            return False
        # contentChanged ricarica l'albero; riallinea la selezione
        if self.view is view:
            with ENGINE_LOCK:
                self.items = E.read_outline(view.document.pdf)
            self._rebuild(select)
        return True

    def add_bookmark(self, title: str | None = None, edit: bool = False) -> int | None:
        view = self.view
        if view is None or not self.editable:
            return None
        page = view.current_page
        if title is None:
            sel = view.selected_text().strip() if hasattr(view, "selected_text") else ""
            title = " ".join(sel.split())[:120] if sel else _("Pagina {n}").format(n=page + 1)
        inv, _ok = view.page_transform(page).inverted()
        tl = inv.map(QPointF(0, 0))
        dest = {"kind": pymupdf.LINK_GOTO, "page": page, "to": pymupdf.Point(max(0.0, tl.x()), max(0.0, tl.y()))}
        if self.ctx.settings().value("viewer/bookmark_inherit_zoom", False) in (True, "true"):
            dest["zoom"] = view.zoom
        new_items, pos = E.bookmark_add(self.items, self.selected_index(), title, page, dest)
        if not self._apply(_("Aggiungi segnalibro"), new_items, pos):
            return None
        if edit and pos < len(self._tree_items):
            self.tree.editItem(self._tree_items[pos])
        return pos

    def start_rename(self) -> None:
        i = self.selected_index()
        if i is not None and self.editable:
            self.tree.editItem(self._tree_items[i])

    def rename(self, index: int, title: str) -> bool:
        title = title.strip()
        if not title or self.items[index].title == title:
            return False
        return self._apply(_("Rinomina segnalibro"), E.bookmark_rename(self.items, index, title), index)

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._rebuilding:
            return
        i = item.data(0, Qt.ItemDataRole.UserRole)
        if not (isinstance(i, int) and 0 <= i < len(self.items)):
            return
        text = item.text(0).strip()
        shown = self.items[i].title or _("(senza titolo)")
        if text == shown:
            return  # cambio di aspetto, non di testo
        if not self.rename(i, text):
            QTimer.singleShot(0, lambda: self._rebuild(i))

    def delete(self, index: int, confirm: bool = True) -> bool:
        children = E.children_counts(self.items)[index]
        if confirm and children:
            if not self.ctx.confirm(_("Elimina segnalibro"), _("Eliminare «{t}» e i suoi {n} segnalibri figli?")
                                    .format(t=self.items[index].title, n=children)):
                return False
        new_items = E.bookmark_delete(self.items, index)
        select = min(index, len(new_items) - 1) if new_items else None
        return self._apply(_("Elimina segnalibro"), new_items, select)

    def delete_selected(self) -> None:
        i = self.selected_index()
        if i is not None:
            self.delete(i)

    def _op(self, op: str) -> bool:
        i = self.selected_index()
        if i is None:
            return False
        if op == "up":
            new_items, j = E.bookmark_move_up(self.items, i)
        elif op == "down":
            new_items, j = E.bookmark_move_down(self.items, i)
        elif op == "indent":
            new_items, j = E.bookmark_indent(self.items, i), i
        else:
            new_items, j = E.bookmark_outdent(self.items, i)
        if new_items is self.items or [(b.level, b.title) for b in new_items] == [(b.level, b.title) for b in self.items]:
            return False
        return self._apply(_("Sposta segnalibro"), new_items, j)

    def move_to(self, index: int, target: int, where: str) -> bool:
        """Trascinamento: `where` = "child", "before" o "after" rispetto a `target`."""
        if index == target:
            return False
        if where == "after":
            nxt = E.subtree_end(self.items, target)
            if nxt < len(self.items) and self.items[nxt].level == self.items[target].level:
                target, where = nxt, "before"
            else:
                # ultima voce del suo livello: diventa l'ultimo figlio del genitore (o in fondo)
                parent = target - 1
                while parent >= 0 and self.items[parent].level >= self.items[target].level:
                    parent -= 1
                if parent < 0:
                    end = E.subtree_end(self.items, index)
                    if index <= target < end:
                        return False
                    block = [E.Bookmark(b.level - self.items[index].level + 1, b.title, b.page, dict(b.dest))
                             for b in self.items[index:end]]
                    rest = self.items[:index] + self.items[end:]
                    return self._apply(_("Sposta segnalibro"), rest + block, len(rest))
                target, where = parent, "child"
        new_items, pos = E.bookmark_move_into(self.items, index, target, as_child=(where == "child"))
        if new_items is self.items:
            return False
        return self._apply(_("Sposta segnalibro"), new_items, pos)

    def set_destination_here(self, index: int) -> bool:
        view = self.view
        if view is None:
            return False
        page = view.current_page
        inv, _ok = view.page_transform(page).inverted()
        tl = inv.map(QPointF(0, 0))
        new_items = [E.Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in self.items]
        b = new_items[index]
        b.page = page
        b.dest.update({"kind": pymupdf.LINK_GOTO, "page": page,
                       "to": pymupdf.Point(max(0.0, tl.x()), max(0.0, tl.y()))})
        b.dest.pop("uri", None)
        return self._apply(_("Modifica segnalibro"), new_items, index)

    def create_toc_page(self) -> int:
        """Pagine di sommario cliccabili in testa al documento (annullabile)."""
        view = self.view
        if view is None or not self.editable or not self.items:
            return 0
        items = [E.Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in self.items]
        try:
            with view.document.change(_("Crea pagina di sommario")) as pdf:
                n = E.insert_toc_pages(pdf, items, _("Sommario"))
        except Exception as e:
            self.ctx.error(_("Crea pagina di sommario"), str(e))
            return 0
        if n:
            view.go_to_page(0)
        return n

    def set_uri(self, index: int, uri: str) -> bool:
        """Il segnalibro apre un indirizzo web (con la conferma dei collegamenti del nucleo)."""
        uri = uri.strip()
        if not uri:
            return False
        if "://" not in uri and not uri.startswith("mailto:"):
            uri = "https://" + uri
        new_items = [E.Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in self.items]
        b = new_items[index]
        b.dest = {k: v for k, v in b.dest.items() if k in ("bold", "italic", "color", "collapse")}
        b.dest.update({"kind": pymupdf.LINK_URI, "uri": uri})
        b.page = -1
        return self._apply(_("Modifica segnalibro"), new_items, index)

    def ask_uri(self, index: int) -> None:
        from PySide6.QtWidgets import QInputDialog
        current = self.items[index].dest.get("uri", "")
        uri, ok = QInputDialog.getText(self, _("Apri un indirizzo web…"), _("Indirizzo da aprire:"), text=current)
        if ok and uri.strip():
            self.set_uri(index, uri)

    def set_style(self, index: int, bold: bool | None = None, italic: bool | None = None,
                  color: tuple | None | bool = False) -> bool:
        new_items = [E.Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in self.items]
        d = new_items[index].dest
        if bold is not None:
            d["bold"] = bold
        if italic is not None:
            d["italic"] = italic
        if color is not False:
            if color is None:
                d.pop("color", None)
            else:
                d["color"] = tuple(color)
        return self._apply(_("Modifica segnalibro"), new_items, index)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            i = item.data(0, Qt.ItemDataRole.UserRole)
            self.tree.setCurrentItem(item)
            menu.addAction(_("Vai"), lambda: self.follow(i))
            if self.editable:
                menu.addSeparator()
                menu.addAction(self.actions["rename"])
                menu.addAction(_("Imposta destinazione sulla vista corrente"), lambda: self.set_destination_here(i))
                menu.addAction(_("Apri un indirizzo web…"), lambda: self.ask_uri(i))
                b = self.items[i]
                a = menu.addAction(_("Grassetto"), lambda: self.set_style(i, bold=not b.bold))
                a.setCheckable(True)
                a.setChecked(b.bold)
                a = menu.addAction(_("Corsivo"), lambda: self.set_style(i, italic=not b.italic))
                a.setCheckable(True)
                a.setChecked(b.italic)
                menu.addAction(_("Colore…"), lambda: self._pick_color(i))
                if b.color:
                    menu.addAction(_("Colore predefinito"), lambda: self.set_style(i, color=None))
                menu.addSeparator()
                for key in ("up", "down", "indent", "outdent"):
                    menu.addAction(self.actions[key])
                menu.addSeparator()
                menu.addAction(self.actions["delete"])
        if self.editable and self.view is not None:
            menu.addSeparator()
            menu.addAction(self.actions["add"])
        menu.addSeparator()
        a = menu.addAction(_("Evidenzia il segnalibro corrente"))
        a.setCheckable(True)
        a.setChecked(self.highlight_current)
        a.toggled.connect(self.set_highlight_current)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _pick_color(self, index: int) -> None:
        current = self.items[index].color
        initial = QColor.fromRgbF(*current[:3]) if current else theme.color("text")
        c = QColorDialog.getColor(initial, self, _("Colore…"))
        if c.isValid():
            self.set_style(index, color=(c.redF(), c.greenF(), c.blueF()))

    def set_highlight_current(self, on: bool) -> None:
        self.highlight_current = bool(on)
        self.ctx.settings().setValue("viewer/highlight_bookmark", self.highlight_current)
        self._current = None
        if on and self.view is not None:
            self._on_page(self.view.current_page)
        else:
            self._paint_current()


def _bookmark_tools(panel_cls):
    """Strumenti avanzati (VIS-17) aggiunti al pannello."""

    def export_text(self, path: str | None = None) -> str | None:
        if self.view is None:
            return None
        if path is None:
            stem = self.view.document.display_name.rsplit(".", 1)[0]
            path = self.ctx.ask_save_path(_("Esporta segnalibri"), f"{stem} - segnalibri.txt",
                                          _("Testo (*.txt)"), ".txt")
        if not path:
            return None
        from foliant.api import atomic_write
        try:
            atomic_write(path, E.outline_to_text(self.items).encode("utf-8"))
        except Exception as e:
            self.ctx.error(_("Esporta segnalibri"), str(e))
            return None
        self.ctx.status(_("Segnalibri esportati: {p}").format(p=path))
        return path

    def _merge(self, label: str, new: list, replace: bool | None) -> bool:
        if replace is None:
            replace = not self.items or self.ctx.confirm(
                label, _("Sostituire i segnalibri esistenti? Scegli No per aggiungerli in fondo."))
        items = new if replace else [E.Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in self.items] + new
        return self._apply(label, items, 0)

    def import_text(self, path: str | None = None, replace: bool | None = None) -> bool:
        if self.view is None or not self.editable:
            return False
        if path is None:
            paths = self.ctx.ask_open_paths(_("Importa segnalibri"), _("Testo (*.txt)"), multiple=False)
            path = paths[0] if paths else None
        if not path:
            return False
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            self.ctx.error(_("Importa segnalibri"), _("Impossibile leggere il file: {e}").format(e=e))
            return False
        items, errors = E.outline_from_text(text, self.view.page_count)
        if errors:
            self.ctx.status(_("{n} righe scartate (pagina fuori intervallo o formato non valido):").format(n=len(errors))
                            + " " + "; ".join(errors[:5]), 10000)
        if not items:
            self.ctx.error(_("Importa segnalibri"), _("Nessun segnalibro valido nel file."))
            return False
        return self._merge(_("Importa segnalibri"), items, replace)

    def generate(self, method: str, n: int | None = None, replace: bool | None = None,
                 pattern: str | None = None) -> bool:
        view = self.view
        if view is None or not self.editable:
            return False
        if method == "every":
            if n is None:
                from PySide6.QtWidgets import QInputDialog
                n, ok = QInputDialog.getInt(self, _("Genera segnalibri"), _("Ogni quante pagine?"), 10, 1,
                                            max(1, view.page_count))
                if not ok:
                    return False
            items = E.outline_every_n(view.page_count, n, _("Pagine {a}-{b}"))
        elif method == "regex":
            if pattern is None:
                from PySide6.QtWidgets import QInputDialog
                pattern, ok = QInputDialog.getText(self, _("Genera segnalibri"),
                                                   _("Espressione regolare delle righe da usare come titoli:"),
                                                   text=r"^(?P<level>\d+(\.\d+)*)\s+\S")
                if not ok or not pattern.strip():
                    return False
            try:
                with ENGINE_LOCK:
                    items = E.outline_from_regex(view.document.pdf, pattern)
            except ValueError as e:
                self.ctx.error(_("Genera segnalibri"), _("Espressione regolare non valida: {e}").format(e=e))
                return False
            if not items:
                self.ctx.info(_("Genera segnalibri"), _("Nessuna riga corrisponde all'espressione."))
                return False
        else:
            document = view.document

            def work(progress):
                with ENGINE_LOCK:
                    return E.outline_from_headings(document.pdf, progress=lambda f: progress.update(f))

            try:
                items = self.ctx.run_task(_("Ricerca dei titoli…"), work)
            except Exception as e:
                from foliant.api import OperationCancelled
                if not isinstance(e, OperationCancelled):
                    self.ctx.error(_("Genera segnalibri"), str(e))
                return False
            if not items:
                self.ctx.info(_("Genera segnalibri"),
                              _("Nessun titolo riconosciuto: il documento potrebbe essere una scansione (usa l'OCR)."))
                return False
            if replace is None and not self.ctx.confirm(_("Genera segnalibri"),
                                                        _("Trovati {n} titoli. Creare i segnalibri?").format(n=len(items))):
                return False
        return self._merge(_("Genera segnalibri"), items, replace)

    def verify(self) -> list:
        if self.view is None:
            return []
        problems = E.verify_outline(self.items, self.view.page_count)
        if not problems:
            self.ctx.info(_("Verifica segnalibri"), _("Nessun problema trovato nei segnalibri."))
            return []
        reasons = {"broken": _("destinazione non valida"), "untitled": _("senza titolo"), "duplicate": _("duplicato")}
        lines = [f"• {self.items[i].title or _('(senza titolo)')} — {reasons.get(why, why)}" for i, why in problems[:30]]
        self.tree.setCurrentItem(self._tree_items[problems[0][0]])
        self.ctx.info(_("Verifica segnalibri"), _("{n} segnalibri con problemi:").format(n=len(problems))
                      + "\n\n" + "\n".join(lines))
        return problems

    for fn in (export_text, _merge, import_text, generate, verify):
        setattr(panel_cls, fn.__name__, fn)
    return panel_cls


_bookmark_tools(BookmarksPanel)


class BookmarksController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.panel = BookmarksPanel(ctx)
        ctx.add_sidebar_panel("bookmarks", _("Segnalibri"), "bookmark", self.panel, order=20)
        self.act_add = ctx.action(_("Aggiungi segnalibro"), "bookmark-plus", self.add, shortcut="Ctrl+B")
        ctx.add_menu_action(self.act_add, _("Vista"))
        ctx.add_view_menu_provider(self._menu_provider)

    def add(self) -> None:
        if self.ctx.current_view() is None:
            return
        self.ctx.show_sidebar_panel("bookmarks")
        self.panel.add_bookmark(edit=True)

    def _menu_provider(self, view, page_event, menu) -> None:
        if view.selected_text() and self.panel.editable:
            menu.addAction(_("Aggiungi segnalibro"), self.add)


def install(ctx: Context) -> BookmarksController:
    add_translations("en", TRANSLATIONS)
    return BookmarksController(ctx)
