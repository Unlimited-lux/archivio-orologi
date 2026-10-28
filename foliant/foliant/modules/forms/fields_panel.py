"""Elenco campi (FRM-24): pannello laterale con l'albero dei campi del modulo.

I nomi con punti formano la gerarchia («cliente.nome» → nodo «cliente», figlio «nome»).
Ogni voce mostra tipo e pagina; le anomalie (nomi uguali con tipi diversi, calcoli che
citano campi inesistenti) sono marcate. Un clic porta al campo e lo seleziona (modifica
modulo) o gli dà il focus (compilazione); il doppio clic in modifica apre le proprietà.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from foliant.api import _, icon

from . import engine as E

TRANSLATIONS: dict[str, str] = {
    "Cerca campi": "Search fields",
    "Nome": "Name",
    "Tipo": "Type",
    "Pag.": "Page",
    "p. {n}": "p. {n}",
    "Rendi univoci i nomi": "Make names unique",
    "Assegna un nome distinto ai campi omonimi di tipo diverso": "Give a distinct name to same-named fields of different type",
    "Nome duplicato con tipo diverso": "Duplicate name with different type",
    "Il calcolo cita campi inesistenti: {names}": "The calculation refers to missing fields: {names}",
    "Obbligatorio": "Required",
    "Sola lettura": "Read-only",
    "Calcolato": "Calculated",
    "{n} campi": "{n} fields",
    "1 campo": "1 field",
    "{n} di {t} campi": "{n} of {t} fields",
    "Nessun campo": "No fields",
}

try:  # le traduzioni sono registrate anche da qui, così il pannello è autosufficiente
    from foliant.api import add_translations
    add_translations("en", TRANSLATIONS)
except Exception:  # pragma: no cover
    pass

KIND_ICONS = {
    E.TEXT: "text-cursor-input", E.CHECKBOX: "square-check", E.RADIO: "circle-dot",
    E.COMBO: "square-chevron-down", E.LIST: "list", E.BUTTON: "square-mouse-pointer",
    E.SIGNATURE: "signature",
}

KEY_ROLE = Qt.ItemDataRole.UserRole          # (page, xref) del widget, o None per i nodi di gruppo
PATH_ROLE = Qt.ItemDataRole.UserRole + 1     # percorso completo del nodo (per espansione e ricerca)

COL_NAME, COL_KIND, COL_PAGE = 0, 1, 2


def _icon(name: str):
    try:
        return icon(name)
    except Exception:  # pragma: no cover - icona mancante: nessuna icona
        from PySide6.QtGui import QIcon
        return QIcon()


def duplicate_names(fields: list[E.FieldInfo]) -> set[str]:
    """Nomi completi usati da campi di tipo diverso."""
    kinds: dict[str, set[str]] = {}
    for f in fields:
        kinds.setdefault(f.name, set()).add(f.kind)
    return {n for n, k in kinds.items() if len(k) > 1}


def missing_references(fields: list[E.FieldInfo]) -> dict[str, list[str]]:
    """Per ogni campo calcolato (nome) i campi citati dal calcolo che non esistono."""
    names = [f.name for f in fields]
    out: dict[str, list[str]] = {}
    for f in fields:
        if f.calc is None or f.name in out:
            continue
        missing = [n for n in f.calc.fields
                   if not any(a == n or a.startswith(n + ".") for a in names)]
        if missing:
            out[f.name] = missing
    return out


def make_names_unique(pdf) -> int:
    """Rinomina i campi omonimi di tipo diverso (tutti tranne quelli del primo tipo trovato).

    Il nuovo nome resta sotto lo stesso genitore («a.b» → «a.b_2»). Restituisce il numero di
    campi rinominati."""
    fields = E.list_fields(pdf)
    dups = duplicate_names(fields)
    renamed = 0
    for name in sorted(dups):
        current = [f for f in E.list_fields(pdf) if f.name == name]
        if not current:
            continue
        first_kind = current[0].kind
        done: set[int] = set()
        for f in current:
            if f.kind == first_kind or f.field_xref in done:
                continue
            done.add(f.field_xref)
            prefix, _sep, last = name.rpartition(".")
            prefix = prefix + "." if prefix else ""
            taken = {n[len(prefix):] for n in E.field_names(pdf) if n.startswith(prefix)}
            new = prefix + E.unique_name(pdf, last, taken)
            E.update_properties(pdf, f.page, f.xref, name=new)
            renamed += 1
    return renamed


class FieldsPanel(QWidget):
    """Pannello «Campi»: costruito come `FieldsPanel(module)` (il FormsModule)."""

    def __init__(self, module: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.module = module
        self._view = None
        self._fields: list[E.FieldInfo] = []
        self._signature: tuple | None = None
        self._known_paths: set[str] = set()
        self._expanded: set[str] = set()
        self._items_by_key: dict[tuple[int, int], QTreeWidgetItem] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Cerca campi"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(_icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([_("Nome"), _("Tipo"), _("Pag.")])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        # il tipo è già indicato dall'icona: colonna stretta (testo abbreviato), il nome ha la precedenza
        hdr.setSectionResizeMode(COL_KIND, QHeaderView.ResizeMode.Interactive)
        hdr.resizeSection(COL_KIND, 78)
        hdr.setSectionResizeMode(COL_PAGE, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemActivated.connect(self._on_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.itemExpanded.connect(lambda it: self._expanded.add(it.data(0, PATH_ROLE)))
        self.tree.itemCollapsed.connect(lambda it: self._expanded.discard(it.data(0, PATH_ROLE)))
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.summary = QLabel(_("Nessun campo"))
        row.addWidget(self.summary, 1)
        self.btn_unique = QPushButton(_icon("triangle-alert"), _("Rendi univoci i nomi"))
        self.btn_unique.setToolTip(_("Assegna un nome distinto ai campi omonimi di tipo diverso"))
        self.btn_unique.clicked.connect(self.make_unique)
        self.btn_unique.setEnabled(False)
        self.btn_unique.setVisible(False)
        row.addWidget(self.btn_unique)
        lay.addLayout(row)

    # ------------------------------------------------------------ API
    def set_view(self, view) -> None:
        self._view = view
        self._signature = None
        self.refresh()

    def view(self):
        return self._view

    def fields(self) -> list[E.FieldInfo]:
        return list(self._fields)

    def refresh(self) -> None:
        """Ricostruisce l'albero dalla vista corrente (solo se i campi sono cambiati)."""
        fields = self._read_fields()
        sig = (id(self._view), tuple((f.page, f.xref, f.name, f.kind, f.required, f.readonly,
                                      tuple(f.calc.fields) if f.calc else None) for f in fields))
        if sig != self._signature:
            self._signature = sig
            self._fields = fields
            self._rebuild()
        self._sync_selection()

    # ------------------------------------------------------------ dati
    def _read_fields(self) -> list[E.FieldInfo]:
        view = self._view
        if view is None:
            return []
        try:
            state = self.module.filler(view).state
            state.refresh()
            return list(state.fields)
        except Exception:
            return []

    # ------------------------------------------------------------ costruzione
    def _rebuild(self) -> None:
        tree = self.tree
        if tree.topLevelItemCount():
            self._remember_expansion()
        tree.setUpdatesEnabled(False)
        tree.clear()
        self._items_by_key = {}
        fields = self._fields
        dups = duplicate_names(fields)
        missing = missing_references(fields)
        nodes: dict[str, QTreeWidgetItem] = {}
        by_name: dict[str, list[E.FieldInfo]] = {}
        for f in fields:
            by_name.setdefault(f.name, []).append(f)

        def node(path: str) -> QTreeWidgetItem:
            it = nodes.get(path)
            if it is not None:
                return it
            parent_path, _sep, last = path.rpartition(".")
            it = QTreeWidgetItem([last or path, "", ""])
            it.setData(0, PATH_ROLE, path)
            it.setData(0, KEY_ROLE, None)
            if parent_path:
                node(parent_path).addChild(it)
            else:
                tree.addTopLevelItem(it)
            nodes[path] = it
            return it

        for name, group in by_name.items():
            target = node(name)
            first = group[0]
            self._decorate(target, first, name in dups, missing.get(name))
            if len(group) == 1:
                target.setData(0, KEY_ROLE, first.key)
                self._items_by_key[first.key] = target
                continue
            # più widget con lo stesso nome: una voce per widget
            target.setData(0, KEY_ROLE, first.key)
            pages = sorted({f.page + 1 for f in group})
            target.setText(COL_PAGE, ", ".join(str(p) for p in pages))
            for i, f in enumerate(group):
                label = (f.export or str(i + 1)) if f.kind in (E.RADIO, E.CHECKBOX) else str(i + 1)
                child = QTreeWidgetItem([label, "", _("p. {n}").format(n=f.page + 1)])
                child.setData(0, PATH_ROLE, f"{name}#{i}")
                child.setData(0, KEY_ROLE, f.key)
                self._decorate(child, f, name in dups, None)
                if name not in dups:
                    child.setText(COL_KIND, "")
                target.addChild(child)
                self._items_by_key[f.key] = child

        for path, it in nodes.items():
            if it.childCount():
                if path not in self._known_paths or path in self._expanded:
                    it.setExpanded(True)
                    self._expanded.add(path)
        self._known_paths |= set(nodes)
        has_dups = bool(dups)
        self.btn_unique.setVisible(has_dups)
        self.btn_unique.setEnabled(has_dups and self._view is not None)
        tree.setUpdatesEnabled(True)
        self._apply_filter(self.search.text())

    def _decorate(self, it: QTreeWidgetItem, f: E.FieldInfo, duplicate: bool,
                  missing: list[str] | None, show_page: bool = True) -> None:
        text = it.text(COL_NAME)
        if f.required and not text.endswith(" *"):
            it.setText(COL_NAME, text + " *")
        it.setText(COL_KIND, E.kind_label(f.kind))
        if show_page:
            it.setText(COL_PAGE, _("p. {n}").format(n=f.page + 1))
        tips = [f.name, E.kind_label(f.kind)]
        if f.required:
            tips.append(_("Obbligatorio"))
        if f.readonly:
            tips.append(_("Sola lettura"))
        if f.calc is not None:
            tips.append(_("Calcolato"))
        warn = []
        if duplicate:
            warn.append(_("Nome duplicato con tipo diverso"))
        if missing:
            warn.append(_("Il calcolo cita campi inesistenti: {names}").format(names=", ".join(missing)))
        if warn:
            it.setIcon(COL_NAME, _icon("triangle-alert"))
            tips = warn + tips
        else:
            it.setIcon(COL_NAME, _icon(KIND_ICONS.get(f.kind, "text-cursor-input")))
        it.setData(0, Qt.ItemDataRole.UserRole + 2, bool(warn))
        tip = "\n".join(tips)
        for c in range(3):
            it.setToolTip(c, tip)

    def _remember_expansion(self) -> None:
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            path = item.data(0, PATH_ROLE)
            if item.childCount():
                if item.isExpanded():
                    self._expanded.add(path)
                else:
                    self._expanded.discard(path)
            it += 1

    # ------------------------------------------------------------ filtro e riepilogo
    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        shown = 0

        def visit(item: QTreeWidgetItem, parent_match: bool) -> bool:
            nonlocal shown
            path = item.data(0, PATH_ROLE) or ""
            full = path.split("#", 1)[0]
            own = not needle or needle in full.casefold()
            any_child = False
            for i in range(item.childCount()):
                if visit(item.child(i), own):
                    any_child = True
            visible = own or any_child
            item.setHidden(not visible)
            if needle and any_child:
                item.setExpanded(True)
            if visible and item.data(0, KEY_ROLE) is not None and "#" not in path:
                shown += 1
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i), False)
        self._update_summary(shown if needle else None)

    def _update_summary(self, shown: int | None) -> None:
        total = len({f.name for f in self._fields})
        if total == 0:
            self.summary.setText(_("Nessun campo"))
        elif shown is not None and shown != total:
            self.summary.setText(_("{n} di {t} campi").format(n=shown, t=total))
        elif total == 1:
            self.summary.setText(_("1 campo"))
        else:
            self.summary.setText(_("{n} campi").format(n=total))

    def visible_names(self) -> list[str]:
        """Nomi completi dei campi visibili con il filtro corrente (per test e automazioni)."""
        out = []
        for f in self._fields:
            it = self._items_by_key.get(f.key)
            if it is not None and not it.isHidden() and f.name not in out:
                out.append(f.name)
        return out

    def item_for(self, name: str) -> QTreeWidgetItem | None:
        for f in self._fields:
            if f.name == name:
                it = self._items_by_key.get(f.key)
                return it.parent() if it is not None and "#" in (it.data(0, PATH_ROLE) or "") else it
        return None

    # ------------------------------------------------------------ selezione
    def _sync_selection(self) -> None:
        sel = list(getattr(self.module, "selection", []) or []) if getattr(self.module, "design_active", False) else []
        self.tree.blockSignals(True)
        try:
            self.tree.clearSelection()
            first = None
            for key in sel:
                it = self._items_by_key.get(tuple(key))
                if it is not None:
                    it.setSelected(True)
                    first = first or it
            if first is not None:
                self.tree.scrollToItem(first)
        finally:
            self.tree.blockSignals(False)

    def _field_of(self, item: QTreeWidgetItem) -> E.FieldInfo | None:
        key = item.data(0, KEY_ROLE) if item is not None else None
        if key is None:
            return None
        key = tuple(key)
        for f in self._fields:
            if f.key == key:
                return f
        return None

    def go_to(self, f: E.FieldInfo) -> None:
        view = self._view
        if view is None:
            return
        view.go_to_page(f.page)
        view.ensure_visible(f.page, f.rect)
        if self.module.design_active:
            self.module.set_selection([f.key])
        elif f.fillable:
            try:
                self.module.filler(view).activate(f)
            except Exception:
                pass

    def _on_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        f = self._field_of(item)
        if f is not None:
            self.go_to(f)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        f = self._field_of(item)
        if f is None or not self.module.design_active:
            return
        if f.key not in [tuple(k) for k in (getattr(self.module, "selection", []) or [])]:
            self.module.set_selection([f.key])
        self.module.show_properties()

    # ------------------------------------------------------------ anomalie
    def make_unique(self) -> None:
        if self._view is None or not duplicate_names(self._fields):
            return
        self.module._change(_("Rendi univoci i nomi"), make_names_unique, self._view)
        self._signature = None
        self.refresh()
