"""Pannello Livelli (VIS-20; «salva visibilità come predefinita» di VIS-21).

La visibilità cambia solo in memoria (il documento non risulta modificato) e
vale per la sessione, per ciascuna vista. Dopo un annulla/ripeti il documento
interno viene ricaricato con la configurazione predefinita: lo stato salvato
viene riapplicato a ogni cambio di contenuto. La logica pura è in
`layers_engine.py`.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QRadioButton, QStackedWidget, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, add_translations, bind_icon

from . import layers_engine as engine
from .layers_engine import Layer

ROLE_LAYER = Qt.ItemDataRole.UserRole

EN = {
    "Livelli": "Layers",
    "Filtra per nome": "Filter by name",
    "Ripristina visibilità iniziale": "Restore initial visibility",
    "Mostra tutti": "Show all",
    "Salva visibilità come predefinita": "Save visibility as default",
    "Nessun documento aperto.": "No document open.",
    "Questo documento non contiene livelli.": "This document has no layers.",
    "Nessun livello corrisponde al filtro.": "No layer matches the filter.",
    "Livello bloccato: la visibilità non si può cambiare.": "Locked layer: its visibility cannot be changed.",
    "Gruppo esclusivo: attivando questo livello si spengono gli altri del gruppo.":
        "Exclusive group: turning this layer on turns the others in the group off.",
    "{n} livelli": "{n} layers",
    "1 livello": "1 layer",
    "Salva visibilità dei livelli": "Save layer visibility",
    "Impossibile salvare la visibilità dei livelli": "Unable to save layer visibility",
    "La visibilità cambia solo la vista: il documento non viene modificato.":
        "Visibility changes only the view: the document is not modified.",
}


class LayersPanel(QWidget):
    """Pannello laterale e controller della parte `layers`."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.view = None
        # Per vista: stato iniziale (all'apertura) e stato della sessione.
        self._initial: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._session: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        self._widgets: dict[int, QWidget] = {}
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(_("Livelli"))
        title.setProperty("heading", True)
        self.count_label = QLabel("")
        self.count_label.setProperty("muted", True)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count_label)
        layout.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(_("Filtra per nome"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_edit, 1)
        self.btn_reset = self._button("rotate-ccw", _("Ripristina visibilità iniziale"), self.reset_initial)
        self.btn_show_all = self._button("eye", _("Mostra tutti"), self.show_all)
        self.btn_more = QToolButton(self)
        bind_icon(self.btn_more, "ellipsis-vertical")
        self.btn_more.setAutoRaise(True)
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.btn_more)
        self._fill_menu(more)
        self.btn_more.setMenu(more)
        for b in (self.btn_reset, self.btn_show_all, self.btn_more):
            bar.addWidget(b)
        layout.addLayout(bar)

        self.stack = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setProperty("muted", True)
        self.empty.setContentsMargins(12, 24, 12, 24)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)

        self.hint = QLabel(_("La visibilità cambia solo la vista: il documento non viene modificato."))
        self.hint.setWordWrap(True)
        self.hint.setProperty("muted", True)
        layout.addWidget(self.hint)

        ctx.documentChanged.connect(self._on_document_changed)
        ctx.documentOpened.connect(self._on_document_opened)
        self.set_view(None)

    def _button(self, icon_name: str, text: str, slot) -> QToolButton:
        b = QToolButton(self)
        bind_icon(b, icon_name)
        b.setToolTip(text)
        b.setAccessibleName(text)
        b.setAutoRaise(True)
        b.clicked.connect(lambda _c=False: slot())
        return b

    def _fill_menu(self, menu: QMenu) -> None:
        a = menu.addAction(_("Ripristina visibilità iniziale"), self.reset_initial)
        bind_icon(a, "rotate-ccw")
        a = menu.addAction(_("Mostra tutti"), self.show_all)
        bind_icon(a, "eye")
        menu.addSeparator()
        a = menu.addAction(_("Salva visibilità come predefinita"), self.save_as_default)
        bind_icon(a, "save")

    def _context_menu(self, pos) -> None:
        if self.view is None:
            return
        menu = QMenu(self)
        self._fill_menu(menu)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------- documento
    @property
    def document(self):
        return self.view.document if self.view is not None else None

    def set_view(self, view) -> None:
        self.view = view
        if view is not None and view not in self._initial:
            st = self._read(engine.state)
            self._initial[view] = st or {}
            self._session[view] = dict(st or {})
        self.rebuild()

    def _on_document_opened(self, view) -> None:
        try:
            with ENGINE_LOCK:
                pdf = view.document.pdf
                auto = pdf.pagemode == "UseOC" and engine.has_layers(pdf)
        except Exception:
            auto = False
        if auto:
            self.ctx.show_sidebar_panel("layers")

    def _on_document_changed(self, view) -> None:
        if view not in self._session:
            return
        # Dopo annulla/ripeti il documento interno è nuovo: si riapplica lo stato della sessione.
        doc = view.document
        try:
            with ENGINE_LOCK:
                changed = engine.apply_state(doc.pdf, self._session[view])
                self._session[view] = engine.state(doc.pdf)
        except Exception:
            changed = False
        if changed:
            self._refresh_view(view)
        if view is self.view:
            self.rebuild()

    def _read(self, fn, default=None):
        doc = self.document
        if doc is None or doc.closed:
            return default
        try:
            with ENGINE_LOCK:
                return fn(doc.pdf)
        except Exception:
            return default

    def layers(self) -> list[Layer]:
        return self._read(engine.layers, []) or []

    def layer(self, text: str) -> Layer | None:
        for layer in self.layers():
            if layer.text == text:
                return layer
        return None

    # ------------------------------------------------------------------ albero
    def rebuild(self) -> None:
        self._syncing = True
        self.tree.clear()
        self._widgets = {}
        items = self.layers()
        stack: list[tuple[int, QTreeWidgetItem]] = []   # (profondità, riga) degli antenati
        for layer in items:
            depth = max(0, layer.depth)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else None
            row = QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(self.tree)
            row.setData(0, ROLE_LAYER, layer)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if layer.is_label:
                w = QLabel(layer.text)
                w.setStyleSheet("font-weight: 600;")
            else:
                w = QRadioButton(layer.text) if layer.type == engine.RADIOBOX else QCheckBox(layer.text)
                if isinstance(w, QRadioButton):
                    w.setAutoExclusive(False)   # i gruppi li gestisce il motore
                    w.setToolTip(_("Gruppo esclusivo: attivando questo livello si spengono gli altri del gruppo."))
                w.setChecked(layer.on)
                w.setEnabled(not layer.locked)
                if layer.locked:
                    w.setToolTip(_("Livello bloccato: la visibilità non si può cambiare."))
                    bind_icon(w, "lock")
                w.toggled.connect(lambda checked, n=layer.number: self._on_toggled(n, checked))
                self._widgets[layer.number] = w
            w.setAccessibleName(layer.text)
            self.tree.setItemWidget(row, 0, w)
            # le righe successive con profondità maggiore sono figlie di questa
            stack.append((depth, row))
        self.tree.expandAll()
        self._syncing = False
        real = [layer for layer in items if not layer.is_label]
        if self.view is None:
            self.empty.setText(_("Nessun documento aperto."))
            self.stack.setCurrentWidget(self.empty)
            self.count_label.setText("")
        elif not real:
            self.empty.setText(_("Questo documento non contiene livelli."))
            self.stack.setCurrentWidget(self.empty)
            self.count_label.setText("")
        else:
            self.stack.setCurrentWidget(self.tree)
            n = len(real)
            self.count_label.setText(_("1 livello") if n == 1 else _("{n} livelli").format(n=n))
        has = self.view is not None and bool(real)
        for w in (self.btn_reset, self.btn_show_all, self.btn_more, self.filter_edit):
            w.setEnabled(has)
        self.hint.setVisible(has)
        self._apply_filter()

    @property
    def is_empty(self) -> bool:
        return self.stack.currentWidget() is self.empty

    def _sync_widgets(self) -> None:
        self._syncing = True
        for layer in self.layers():
            w = self._widgets.get(layer.number)
            if w is not None and w.isChecked() != layer.on:
                w.setChecked(layer.on)
        self._syncing = False

    def _apply_filter(self, _text: str = "") -> None:
        text = self.filter_edit.text()

        def visit(row: QTreeWidgetItem) -> bool:
            layer = row.data(0, ROLE_LAYER)
            child_visible = False
            for i in range(row.childCount()):
                child_visible = visit(row.child(i)) or child_visible
            show = child_visible or engine.matches(layer, text)
            row.setHidden(not show)
            return show

        any_visible = False
        for i in range(self.tree.topLevelItemCount()):
            any_visible = visit(self.tree.topLevelItem(i)) or any_visible
        if self.view is not None and self.stack.currentWidget() is self.tree and not any_visible and text:
            self.tree.setToolTip(_("Nessun livello corrisponde al filtro."))
        else:
            self.tree.setToolTip("")

    def visible_rows(self) -> list[str]:
        out = []

        def visit(row):
            if not row.isHidden():
                out.append(row.data(0, ROLE_LAYER).text)
            for i in range(row.childCount()):
                visit(row.child(i))

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
        return out

    # ------------------------------------------------------------------ azioni
    def _on_toggled(self, number: int, checked: bool) -> None:
        if self._syncing:
            return
        self.set_visible(number, checked)

    def set_visible(self, number: int, visible: bool) -> bool:
        """Mostra o nasconde un livello (solo in memoria). True se è cambiato qualcosa."""
        changed = self._read(lambda pdf: engine.set_visible(pdf, number, visible), False)
        self._after_change(changed)
        return bool(changed)

    def set_visible_by_name(self, text: str, visible: bool) -> bool:
        layer = self.layer(text)
        return self.set_visible(layer.number, visible) if layer is not None else False

    def reset_initial(self) -> bool:
        if self.view is None:
            return False
        changed = self._read(lambda pdf: engine.apply_state(pdf, self._initial.get(self.view, {})), False)
        self._after_change(changed)
        return bool(changed)

    def show_all(self) -> bool:
        changed = self._read(engine.show_all, False)
        self._after_change(changed)
        return bool(changed)

    def save_as_default(self) -> bool:
        """Scrive la visibilità corrente come configurazione predefinita (modifica annullabile)."""
        doc = self.document
        if doc is None:
            return False
        try:
            with doc.change(_("Salva visibilità dei livelli")) as pdf:
                ok = engine.save_as_default(pdf)
        except Exception as e:
            self.ctx.error(_("Impossibile salvare la visibilità dei livelli"), str(e))
            return False
        if ok and self.view is not None:
            st = self._read(engine.state, {})
            self._initial[self.view] = dict(st)
            self._session[self.view] = dict(st)
        return ok

    def _after_change(self, changed) -> None:
        if self.view is None:
            return
        self._session[self.view] = self._read(engine.state, {}) or {}
        if changed:
            self._refresh_view(self.view)
            thumbs = getattr(self.ctx, "viewer_parts", {}).get("thumbnails")
            if thumbs is not None and thumbs.panel.view is self.view:
                thumbs.panel.refresh()
        self._sync_widgets()

    @staticmethod
    def _refresh_view(view) -> None:
        # il testo visibile cambia con i livelli: le parole in cache (selezione, ricerca)
        # vanno rilette; la resa si rifà con l'API del nucleo
        words = getattr(view, "_words", None)
        if isinstance(words, dict):
            words.clear()
        view.refresh_rendering()


def install(ctx):
    add_translations("en", EN)
    panel = LayersPanel(ctx)
    ctx.add_sidebar_panel("layers", _("Livelli"), "layers", panel, order=50)
    return panel
