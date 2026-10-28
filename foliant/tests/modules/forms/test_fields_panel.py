"""Elenco campi (FRM-24): albero, ricerca, navigazione, anomalie, riepilogo."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from foliant.modules.forms import engine as E
from foliant.modules.forms import fields_panel as FP
from foliant.modules.forms.fields_panel import FieldsPanel


# ---------------------------------------------------------------- finti oggetti
class FakeState:
    def __init__(self, view):
        self.view = view
        self.fields = []

    def refresh(self, force=False):
        self.fields = E.list_fields(self.view.pdf)


class FakeFiller:
    def __init__(self, view):
        self.state = FakeState(view)
        self.activated = []

    def activate(self, f):
        self.activated.append(f.name)


class FakeView:
    def __init__(self, pdf):
        self.pdf = pdf
        self.calls = []

    def go_to_page(self, i):
        self.calls.append(("go", i))

    def ensure_visible(self, i, rect):
        self.calls.append(("visible", i))


class FakeModule:
    def __init__(self):
        self.design_active = False
        self.selection = []
        self.fillers = {}
        self.changes = []
        self.props_shown = 0

    def filler(self, view):
        if id(view) not in self.fillers:
            self.fillers[id(view)] = FakeFiller(view)
        return self.fillers[id(view)]

    def set_selection(self, keys):
        self.selection = list(keys)

    def show_properties(self):
        self.props_shown += 1

    def _change(self, label, fn, view=None):
        self.changes.append(label)
        return fn(view.pdf)


def make_pdf(pages=1):
    pdf = pymupdf.open()
    for _ in range(pages):
        pdf.new_page()
    return pdf


def R(y):
    return pymupdf.Rect(72, y, 272, y + 20)


@pytest.fixture
def setup(qtbot):
    pdf = make_pdf(8)
    module = FakeModule()
    panel = FieldsPanel(module)
    qtbot.addWidget(panel)
    view = FakeView(pdf)
    yield pdf, module, panel, view
    pdf.close()


def children(item):
    return [item.child(i).text(0) for i in range(item.childCount())]


def top_names(panel):
    return [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]


# ---------------------------------------------------------------- test
def test_tree_hierarchy(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "a.b")
    E.create_field(pdf, 0, E.TEXT, R(110), "a.c")
    E.create_field(pdf, 1, E.CHECKBOX, pymupdf.Rect(72, 140, 86, 154), "d")
    panel.set_view(view)
    assert top_names(panel) == ["a", "d"]
    a = panel.tree.topLevelItem(0)
    assert children(a) == ["b", "c"]
    d = panel.tree.topLevelItem(1)
    assert d.text(1) == E.kind_label(E.CHECKBOX)
    assert d.text(2) == "p. 2"
    assert panel.summary.text() == "3 campi"


def test_empty_without_view(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "x")
    panel.set_view(view)
    assert panel.tree.topLevelItemCount() == 1
    assert panel.summary.text() == "1 campo"
    panel.set_view(None)
    assert panel.tree.topLevelItemCount() == 0
    assert panel.summary.text() == "Nessun campo"


def test_required_marker(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "nome", required=True)
    panel.set_view(view)
    assert panel.tree.topLevelItem(0).text(0) == "nome *"


def test_search_filter(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "Totale")
    E.create_field(pdf, 0, E.TEXT, R(110), "righe.subtotale")
    E.create_field(pdf, 0, E.TEXT, R(140), "righe.prezzo")
    E.create_field(pdf, 0, E.TEXT, R(170), "nome")
    panel.set_view(view)
    panel.search.setText("tot")
    assert sorted(panel.visible_names()) == ["Totale", "righe.subtotale"]
    righe = panel.item_for("righe.subtotale").parent()
    assert not righe.isHidden()  # il genitore della corrispondenza resta visibile
    assert panel.item_for("righe.prezzo").isHidden()
    assert panel.summary.text() == "2 di 4 campi"
    # il filtro sopravvive al refresh
    E.create_field(pdf, 0, E.TEXT, R(200), "totale2")
    panel.refresh()
    assert "totale2" in panel.visible_names() and "nome" not in panel.visible_names()
    panel.search.clear()
    assert len(panel.visible_names()) == 5


def test_expansion_preserved(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "a.b")
    panel.set_view(view)
    a = panel.tree.topLevelItem(0)
    assert a.isExpanded()
    a.setExpanded(False)
    E.create_field(pdf, 0, E.TEXT, R(110), "z")
    panel.refresh()
    assert not panel.tree.topLevelItem(0).isExpanded()


def test_click_design_selects(setup):
    pdf, module, panel, view = setup
    module.design_active = True
    xref = E.create_field(pdf, 6, E.TEXT, R(80), "campo7")
    panel.set_view(view)
    item = panel.item_for("campo7")
    assert item.text(2) == "p. 7"
    panel.tree.itemClicked.emit(item, 0)
    assert view.calls[:2] == [("go", 6), ("visible", 6)]
    assert module.selection == [(6, xref)]
    panel.tree.itemDoubleClicked.emit(item, 0)
    assert module.props_shown == 1
    panel.refresh()
    assert panel.item_for("campo7").isSelected()


def test_click_fill_activates(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 2, E.TEXT, R(80), "libero")
    E.create_field(pdf, 2, E.TEXT, R(110), "bloccato", readonly=True)
    panel.set_view(view)
    panel.tree.itemClicked.emit(panel.item_for("libero"), 0)
    panel.tree.itemClicked.emit(panel.item_for("bloccato"), 0)
    assert module.filler(view).activated == ["libero"]
    assert ("go", 2) in view.calls
    panel.tree.itemDoubleClicked.emit(panel.item_for("libero"), 0)
    assert module.props_shown == 0 and module.selection == []


def test_radio_group_one_leaf_per_widget(setup):
    pdf, module, panel, view = setup
    for i, v in enumerate("AB"):
        E.create_field(pdf, i, E.RADIO, pymupdf.Rect(72 + 40 * i, 150, 86 + 40 * i, 164), "scelta", export=v)
    panel.set_view(view)
    group = panel.tree.topLevelItem(0)
    assert group.text(0) == "scelta"
    assert group.childCount() == 2
    assert [group.child(i).text(2) for i in range(2)] == ["p. 1", "p. 2"]
    assert panel.summary.text() == "1 campo"


def _make_duplicate(pdf):
    E.create_field(pdf, 0, E.TEXT, R(80), "dati.x")
    E.create_field(pdf, 1, E.CHECKBOX, pymupdf.Rect(72, 140, 86, 154), "dati.y")
    cb = [f for f in E.list_fields(pdf) if f.name == "dati.y"][0]
    E._rename_field(pdf, cb.field_xref, "dati.y", "dati.x")  # file malformato: stesso nome, altro tipo
    assert sorted(f.kind for f in E.list_fields(pdf) if f.name == "dati.x") == [E.CHECKBOX, E.TEXT]


def test_duplicates_flagged_and_made_unique(setup):
    pdf, module, panel, view = setup
    _make_duplicate(pdf)
    E.create_field(pdf, 0, E.TEXT, R(200), "altro")
    panel.set_view(view)
    assert not panel.btn_unique.isHidden() and panel.btn_unique.isEnabled()
    group = panel.item_for("dati.x")
    flagged = [group.child(i) for i in range(group.childCount())] or [group]
    assert len(flagged) == 2
    for it in flagged:
        assert "Nome duplicato con tipo diverso" in it.toolTip(0)
    assert "Nome duplicato" not in panel.item_for("altro").toolTip(0)
    panel.btn_unique.click()
    assert module.changes == ["Rendi univoci i nomi"]
    names = [f.name for f in E.list_fields(pdf)]
    assert len(set(names)) == len(names) == 3
    assert "dati.x" in names and "dati.x_2" in names
    assert panel.btn_unique.isHidden()
    assert children(panel.tree.topLevelItem(0)) == ["x", "x_2"]


def test_missing_calc_reference(setup):
    pdf, module, panel, view = setup
    E.create_field(pdf, 0, E.TEXT, R(80), "a")
    xref = E.create_field(pdf, 0, E.TEXT, R(110), "tot")
    E.update_properties(pdf, 0, xref, calc=E.Calculation("SUM", ["a", "manca"]))
    panel.set_view(view)
    tip = panel.item_for("tot").toolTip(0)
    assert "manca" in tip and "inesistenti" in tip
    assert "inesistenti" not in panel.item_for("a").toolTip(0)


def test_translations_cover_strings():
    import re
    from pathlib import Path
    src = Path(FP.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'_\("((?:[^"\\]|\\.)*)"\)', src))
    assert used <= set(FP.TRANSLATIONS)


# ---------------------------------------------------------------- integrazione
def test_integration_window(window, tmp_path):
    from foliant.modules import forms
    mod = forms.instance()
    if mod is None or getattr(mod, "fields_panel", None) is None:
        pytest.skip("pannello Campi non registrato da module.py")
    pdf = make_pdf(7)
    E.create_field(pdf, 0, E.TEXT, R(80), "a.b")
    E.create_field(pdf, 6, E.TEXT, R(80), "d")
    path = tmp_path / "campi.pdf"
    pdf.save(str(path))
    pdf.close()
    view = window.ctx.open_path(str(path))
    view.flush_rendering()
    QApplication.processEvents()
    panel = mod.fields_panel
    panel.set_view(view)
    assert top_names(panel) == ["a", "d"]
    panel.tree.itemClicked.emit(panel.item_for("d"), 0)
    QApplication.processEvents()
    assert view.current_page == 6
