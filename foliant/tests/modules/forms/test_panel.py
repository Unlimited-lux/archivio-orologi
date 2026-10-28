"""Pannello delle proprietà dei campi (FRM-11…FRM-16)."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from foliant.modules.forms import engine as E
from foliant.modules.forms import formats as fm
from foliant.modules.forms import panel as P


class Harness:
    """Documento in memoria + funzione apply che registra e applica davvero."""

    def __init__(self, qtbot, result: bool = True):
        self.pdf = pymupdf.open()
        self.pdf.new_page()
        self.calls: list[tuple[list, dict, str]] = []
        self.result = result
        self.panel = P.FieldPropertiesPanel(self.apply)
        qtbot.addWidget(self.panel)
        self.selected: list[str] = []

    def apply(self, keys, props, label):
        self.calls.append((list(keys), dict(props), label))
        if not self.result:
            return False
        for page, xref in keys:
            E.update_properties(self.pdf, page, xref, **props)
        return True

    def add(self, kind, name, y, **props):
        E.create_field(self.pdf, 0, kind, pymupdf.Rect(72, y, 272, y + 20), name, **props)

    def fields(self, *names):
        return [f for f in E.list_fields(self.pdf) if f.name in names]

    def select(self, *names):
        self.selected = list(names)
        self.panel.set_fields(self.fields(*names), [f.name for f in E.list_fields(self.pdf)])

    def refresh(self):
        self.select(*self.selected)

    def field(self, name):
        return self.fields(name)[0]


@pytest.fixture
def h(qtbot):
    hh = Harness(qtbot)
    hh.add(E.TEXT, "a", 50)
    hh.add(E.TEXT, "b", 80, required=True)
    hh.add(E.TEXT, "c", 110)
    hh.add(E.CHECKBOX, "ok", 140)
    hh.add(E.COMBO, "paese", 170, options=[("IT", "Italia"), ("FR", "Francia")])
    yield hh
    hh.pdf.close()


def test_placeholder_and_no_apply_on_load(h):
    h.panel.set_fields([], [])
    assert not h.panel.placeholder.isHidden() and h.panel.tabs.isHidden()
    for names in (("a",), ("a", "b", "c"), ("ok",), ("paese",), ("a", "ok")):
        h.select(*names)
    assert h.calls == []
    assert h.panel.tabs.isHidden() is False


def test_required_on_three_fields_one_call(h):
    h.select("a", "b", "c")
    cb = h.panel.required_check
    assert cb.checkState() == Qt.CheckState.PartiallyChecked      # misto
    cb.click()
    assert len(h.calls) == 1
    keys, props, label = h.calls[0]
    assert len(keys) == 3 and props == {"required": True} and label
    assert all(f.required for f in h.fields("a", "b", "c"))


def test_mixed_values(h):
    E.update_properties(h.pdf, 0, h.field("a").xref, tooltip="uno")
    E.update_properties(h.pdf, 0, h.field("b").xref, tooltip="due", align=2)
    h.select("a", "b")
    p = h.panel
    assert p.tooltip_edit.text() == "" and p.tooltip_edit.placeholderText() == "misto"
    assert not any(b.isChecked() for b in p.align_buttons)
    assert p.name_edit.isHidden()
    p.tooltip_edit.editingFinished.emit()          # nessun testo: nessuna modifica
    assert h.calls == []
    p.tooltip_edit.setText("tre")
    p.tooltip_edit.editingFinished.emit()
    assert h.calls[-1][1] == {"tooltip": "tre"} and len(h.calls[-1][0]) == 2
    # tipi diversi: niente schede di formato
    h.select("a", "ok")
    assert not p.tabs.isTabVisible(p.tabs.indexOf(p._pages["format"]))
    assert not p.tabs.isTabVisible(p.tabs.indexOf(p._pages["options"]))


def test_comb_constraints(h):
    h.select("a")
    p = h.panel
    assert not p.comb_check.isEnabled()            # serve la lunghezza massima
    p.multiline_check.setChecked(True)
    p.maxlen_spin.setValue(5)
    assert p.comb_check.isEnabled()
    h.refresh()
    p.comb_check.setChecked(True)
    assert h.calls[-1][1] == {"comb": True, "maxlen": 5}
    assert not p.multiline_check.isEnabled() and not p.password_check.isEnabled()
    assert not p.multiline_check.isChecked()
    f = h.field("a")
    assert f.comb and f.maxlen == 5 and not f.multiline
    xref = h.field("a").field_xref
    assert h.pdf.xref_get_key(xref, "MaxLen") == ("int", "5")
    # togliere la lunghezza massima toglie anche il pettine
    h.refresh()
    p.maxlen_spin.setValue(0)
    assert h.calls[-1][1] == {"maxlen": 0, "comb": False}
    assert not h.field("a").comb


def test_number_format_preview(h):
    h.select("a")
    p = h.panel
    p.fmt_category.setCurrentIndex(p.fmt_category.findData(fm.NUMBER))
    p.fmt_decimals.setValue(2)
    p.fmt_separator.setCurrentIndex(p.fmt_separator.findData(fm.SEPARATORS.index("1.234,56")))
    p.fmt_negative.setCurrentIndex(3)
    assert p.fmt_preview.text() == "(1.234,50)"
    fmt = h.calls[-1][1]["fmt"]
    assert fmt == fm.Format(fm.NUMBER, decimals=2, separator=2, negative=3)
    assert h.field("a").fmt == fmt
    assert not p.fmt_decimals.isHidden() and p.fmt_date.isHidden()


def test_date_pattern_rejected(h):
    h.select("a")
    p = h.panel
    p.fmt_category.setCurrentIndex(p.fmt_category.findData(fm.DATE))
    n = len(h.calls)
    assert h.calls[-1][1]["fmt"].pattern == "dd/mm/yyyy"
    p.fmt_date.setEditText("dd")
    p.fmt_date.lineEdit().editingFinished.emit()
    assert len(h.calls) == n and not p.fmt_error.isHidden()


def test_validation(h):
    h.select("a")
    p = h.panel
    p.val_min.setText("18")
    p.val_max.setText("99")
    p.val_max.editingFinished.emit()
    assert h.calls[-1][1] == {"validation": E.Validation(18, 99)}
    assert h.field("a").validation == E.Validation(18, 99)


def test_validation_errors(h):
    h.select("a")
    p = h.panel
    p.val_regex.setText("([a-z")
    p.val_regex.editingFinished.emit()
    assert h.calls == [] and not p.val_error.isHidden() and p.val_error.text()
    p.val_regex.setText("")
    p.val_min.setText("100")
    p.val_max.setText("10")
    p.val_max.editingFinished.emit()
    assert h.calls == [] and "minimo" in p.val_error.text().lower()
    p.val_min.setText("abc")
    p.val_min.editingFinished.emit()
    assert h.calls == [] and not p.val_error.isHidden()


def test_items_editor_and_paste(h):
    h.select("paese")
    p = h.panel
    t = p.items_table
    assert t.rowCount() == 2 and t.item(0, 0).text() == "Italia" and t.item(0, 1).text() == "IT"
    t.item(1, 0).setText("Spagna")
    t.item(1, 1).setText("ES")
    assert h.calls[-1][1] == {"options": [("IT", "Italia"), ("ES", "Spagna")]}
    h.refresh()
    p.item_buttons["add"].click()
    assert h.calls[-1][1]["options"][-1] == ("Voce 3", "Voce 3")
    h.refresh()
    t.selectRow(2)
    p.item_buttons["up"].click()
    assert [e for e, _d in h.calls[-1][1]["options"]] == ["IT", "Voce 3", "ES"]
    h.refresh()
    assert p.paste_items("\n".join(f"Voce {i}" for i in range(50)))
    opts = h.calls[-1][1]["options"]
    assert len(opts) == 50 and opts[7] == ("Voce 7", "Voce 7")
    assert len(h.field("paese").options) == 50
    # testo e valore separati da tabulazione
    h.refresh()
    p.paste_items("Uno\t1\nDue\t2\n")
    assert h.calls[-1][1]["options"] == [("1", "Uno"), ("2", "Due")]


def test_calculation(h):
    h.select("c")
    p = h.panel
    names = [p.calc_list.item(i).text() for i in range(p.calc_list.count())]
    assert "c" not in names and {"a", "b"} <= set(names)
    p.calc_op.setCurrentIndex(p.calc_op.findData("SUM"))
    for i in range(p.calc_list.count()):
        if p.calc_list.item(i).text() in ("a", "b"):
            p.calc_list.item(i).setCheckState(Qt.CheckState.Checked)
    assert h.calls[-1][1] == {"calc": E.Calculation("SUM", ["a", "b"])}
    assert h.field("c").calc == E.Calculation("SUM", ["a", "b"])
    p.calc_filter.setText("pae")
    hidden = [p.calc_list.item(i).isHidden() for i in range(p.calc_list.count())]
    assert hidden.count(False) == 1
    h.refresh()
    p.calc_op.setCurrentIndex(0)
    assert h.calls[-1][1] == {"calc": None, "formula": None} and h.field("c").calc is None


def test_rename(h):
    h.select("a")
    p = h.panel
    p.name_edit.setText("eta")
    p.name_edit.editingFinished.emit()
    assert h.calls[-1][1] == {"name": "eta"}
    assert h.fields("eta")
    p.name_edit.editingFinished.emit()      # invariato: nessuna nuova chiamata
    assert len(h.calls) == 1


def test_rename_failure_reverts(qtbot):
    hh = Harness(qtbot, result=False)
    hh.add(E.TEXT, "a", 50)
    hh.select("a")
    hh.panel.name_edit.setText("nuovo")
    hh.panel.name_edit.editingFinished.emit()
    assert hh.calls[-1][1] == {"name": "nuovo"}
    assert hh.panel.name_edit.text() == "a"
    hh.pdf.close()


def test_colors_and_alignment(h, monkeypatch):
    h.select("a")
    p = h.panel
    p.fill_color_btn.set_transparent()
    assert h.calls[-1][1] == {"fill_color": None}
    assert h.field("a").fill_color is None
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(255, 0, 0)))
    p.text_color_btn.click()
    assert h.calls[-1][1] == {"text_color": (1.0, 0.0, 0.0)}
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))   # annullato
    n = len(h.calls)
    p.border_color_btn.choose()
    assert len(h.calls) == n
    p.align_buttons[1].click()
    assert h.calls[-1][1] == {"align": 1} and h.field("a").align == 1
    p.border_width_combo.setCurrentIndex(p.border_width_combo.findData(3.0))
    p.border_style_combo.setCurrentIndex(p.border_style_combo.findData("D"))
    p.size_spin.setValue(12)
    f = h.field("a")
    assert f.border_width == 3 and f.border_style == "D" and f.fontsize == 12
    assert p.size_spin.specialValueText() == "Automatica"


def test_general_and_options_for_other_kinds(h):
    h.select("ok")
    p = h.panel
    assert p.default_edit.isHidden() and not p.default_check.isHidden()
    p.default_check.click()
    assert h.calls[-1][1] == {"default": True}
    p.visibility_combo.setCurrentIndex(p.visibility_combo.findData("print"))
    assert h.field("ok").visibility == "print"
    p.check_style_combo.setCurrentIndex(p.check_style_combo.findData("star"))
    assert h.field("ok").check_style == "star"
    p.export_edit.setText("Si")
    p.export_edit.editingFinished.emit()
    assert h.field("ok").export == "Si"
    h.select("paese")
    p.default_combo.setCurrentIndex(p.default_combo.findData("FR"))
    assert h.calls[-1][1] == {"default": "FR"} and h.field("paese").default == "FR"
    p.editable_check.click()
    assert h.field("paese").editable


def test_failed_apply_reloads(qtbot):
    hh = Harness(qtbot, result=False)
    hh.add(E.TEXT, "a", 50)
    hh.select("a")
    hh.panel.required_check.click()
    assert len(hh.calls) == 1 and not hh.panel.required_check.isChecked()
    hh.pdf.close()


def test_set_view_harmless(h):
    h.select("a")
    h.panel.set_view(object())
    h.panel.set_view(None)
    assert h.calls == []


def test_rotation(h):
    h.select("a")
    p = h.panel
    assert p.rotation_combo.currentData() == 0
    p.rotation_combo.setCurrentIndex(p.rotation_combo.findData(90))
    assert h.calls[-1][1] == {"rotation": 90}
    assert h.field("a").rotation == 90
    h.refresh()
    assert p.rotation_combo.currentData() == 90


def test_set_default_callback(qtbot):
    got = []
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(72, 50, 272, 70), "a", fontsize=12, align=1)
    E.create_field(pdf, 0, E.CHECKBOX, pymupdf.Rect(72, 80, 92, 100), "ok")
    panel = P.FieldPropertiesPanel(lambda *a: True, set_default=lambda kind, props: got.append((kind, props)))
    qtbot.addWidget(panel)
    fields = E.list_fields(pdf)
    panel.set_fields([f for f in fields if f.name == "a"], [f.name for f in fields])
    assert panel.set_default_btn.isEnabled()
    panel.set_default_btn.click()
    kind, props = got[-1]
    assert kind == E.TEXT
    assert set(props) == set(P._APPEARANCE_KEYS)
    assert props["fontsize"] == 12 and props["align"] == 1 and props["font"] == "Helvetica"
    panel.set_fields(fields, [f.name for f in fields])      # tipi diversi
    assert not panel.set_default_btn.isEnabled()
    # senza callback il pulsante è disattivato
    h2 = P.FieldPropertiesPanel(lambda *a: True)
    qtbot.addWidget(h2)
    h2.set_fields([fields[0]], [f.name for f in fields])
    assert not h2.set_default_btn.isEnabled()
    pdf.close()


def test_formula_valid_applies(h):
    h.select("c")
    p = h.panel
    assert not p.calc_mode_formula.isChecked()
    p.calc_mode_formula.click()
    assert h.calls == []
    assert not p.formula_edit.isHidden() and p.calc_op.isHidden()
    p.formula_edit.setText("a + b")
    assert p.formula_error.isHidden() and p.formula_apply_btn.isEnabled()
    p.formula_edit.editingFinished.emit()
    assert h.calls[-1][1] == {"formula": "a + b"}
    assert h.field("c").formula.expression == "a + b"
    p.formula_apply_btn.click()                 # invariata: nessuna nuova chiamata
    assert len(h.calls) == 1
    # tornare al calcolo predefinito sostituisce la formula
    h.refresh()
    p.calc_mode_predefined.click()
    assert h.calls[-1][1] == {"calc": None, "formula": None}
    assert h.field("c").formula is None


def test_formula_syntax_error(h):
    h.select("c")
    p = h.panel
    p.calc_mode_formula.click()
    p.formula_edit.setText("a + * b")
    assert not p.formula_error.isHidden()
    assert re.search(r"posizione \d+:", p.formula_error.text())
    assert "posizione 5" in p.formula_error.text()
    assert not p.formula_apply_btn.isEnabled()
    p.formula_edit.editingFinished.emit()
    p.formula_apply_btn.click()
    assert h.calls == []


def test_formula_unknown_name(h):
    h.select("c")
    p = h.panel
    p.calc_mode_formula.click()
    p.formula_edit.setText("a + zeta * 2")
    assert not p.formula_error.isHidden() and "zeta" in p.formula_error.text()
    p.formula_edit.editingFinished.emit()
    assert h.calls == []
    p.formula_edit.setText("c * 2")             # il campo stesso
    p.formula_edit.editingFinished.emit()
    assert h.calls == [] and not p.formula_error.isHidden()


def test_formula_loaded_and_completer(h):
    E.update_properties(h.pdf, 0, h.field("c").xref, formula="a * 1.22 - b")
    h.select("c")
    p = h.panel
    assert p.calc_mode_formula.isChecked()
    assert p.formula_edit.text() == "a * 1.22 - b"
    assert not p.formula_edit.isHidden() and p.formula_error.isHidden()
    names = p.formula_completer.model().stringList()
    assert "a" in names and "c" not in names
    p.formula_edit.setText("pa")
    p.formula_edit.setCursorPosition(2)
    p._insert_completion("paese")
    assert p.formula_edit.text() == "paese"
    assert h.calls == []


@pytest.fixture
def hb(qtbot):
    hh = Harness(qtbot)
    hh.add(E.TEXT, "A", 50)
    hh.add(E.TEXT, "B", 80)
    hh.add(E.TEXT, "C", 110)
    hh.add(E.BUTTON, "btn", 140)
    hh.add(E.SIGNATURE, "firma", 170)
    yield hh
    hh.pdf.close()


def _check_names(lw, *names):
    for i in range(lw.count()):
        if lw.item(i).text() in names:
            lw.item(i).setCheckState(Qt.CheckState.Checked)


def test_button_caption_and_reset_action(hb):
    hb.select("btn")
    p = hb.panel
    assert hb.panel.tabs.isTabVisible(p.tabs.indexOf(p._pages["button"]))
    assert not hb.panel.tabs.isTabVisible(p.tabs.indexOf(p._pages["signature"]))
    assert not p.caption_edit.isHidden() and p.image_note.isHidden()
    p.caption_edit.setText("Azzera")
    p.caption_edit.editingFinished.emit()
    assert hb.calls[-1][1] == {"caption": "Azzera"} and hb.field("btn").caption == "Azzera"
    hb.refresh()
    p.action_combo.setCurrentIndex(p.action_combo.findData("reset"))
    assert hb.calls[-1][1] == {"action": {"type": "reset", "fields": []}}
    assert not p.action_fields.isHidden()
    names = [p.action_fields.item(i).text() for i in range(p.action_fields.count())]
    assert "btn" not in names
    _check_names(p.action_fields, "A", "B")
    assert hb.calls[-1][1] == {"action": {"type": "reset", "fields": ["A", "B"]}}
    assert hb.field("btn").action == {"type": "reset", "fields": ["A", "B"]}
    n = len(hb.calls)
    hb.refresh()
    assert p.action_combo.currentData() == "reset" and len(hb.calls) == n
    p.action_combo.setCurrentIndex(0)
    assert hb.calls[-1][1] == {"action": None} and not hb.field("btn").action


def test_button_uri_warning_and_goto(hb):
    hb.select("btn")
    p = hb.panel
    p.action_combo.setCurrentIndex(p.action_combo.findData("uri"))
    assert hb.calls == []                       # indirizzo vuoto: nulla da applicare
    p.action_uri.setText("http://example.com")
    assert not p.uri_warning.isHidden() and "HTTPS" in p.uri_warning.text()
    p.action_uri.editingFinished.emit()
    assert hb.calls[-1][1] == {"action": {"type": "uri", "uri": "http://example.com"}}
    p.action_uri.setText("https://example.com")
    assert p.uri_warning.isHidden()
    hb.refresh()
    p.action_combo.setCurrentIndex(p.action_combo.findData("goto"))
    assert hb.calls[-1][1] == {"action": {"type": "goto", "page": 0}}
    assert p.action_page.value() == 1


def test_button_hide_action(hb):
    hb.select("btn")
    p = hb.panel
    p.action_combo.setCurrentIndex(p.action_combo.findData("hide"))
    assert hb.calls == [] and not p.action_error.isHidden()
    p.action_hide.setCurrentIndex(p.action_hide.findData(False))
    _check_names(p.action_fields, "C")
    assert hb.calls[-1][1] == {"action": {"type": "hide", "fields": ["C"], "hide": False}}


def test_image_button_hides_caption(qtbot):
    hh = Harness(qtbot)
    hh.add(E.BUTTON, "foto", 50)
    f = hh.field("foto")
    hh.pdf.xref_set_key(f.xref, "MK/TP", "1")
    hh.select("foto")
    assert hh.field("foto").image
    assert hh.panel.caption_edit.isHidden() and not hh.panel.image_note.isHidden()
    hh.pdf.close()


def test_signature_lock(hb):
    hb.select("firma")
    p = hb.panel
    assert p.tabs.isTabVisible(p.tabs.indexOf(p._pages["signature"]))
    assert p.lock_combo.currentData() is None and not p.lock_fields.isEnabled()
    p.lock_combo.setCurrentIndex(p.lock_combo.findData("Include"))
    assert hb.calls == [] and p.lock_fields.isEnabled()
    _check_names(p.lock_fields, "A", "B")
    assert hb.calls[-1][1] == {"lock": {"action": "Include", "fields": ["A", "B"]}}
    assert hb.field("firma").lock == {"action": "Include", "fields": ["A", "B"]}
    hb.refresh()
    assert p.lock_combo.currentData() == "Include" and P.FieldPropertiesPanel._checked(p.lock_fields) == ["A", "B"]
    p.lock_combo.setCurrentIndex(p.lock_combo.findData("All"))
    assert hb.calls[-1][1] == {"lock": {"action": "All", "fields": []}}
    hb.refresh()
    p.lock_combo.setCurrentIndex(0)
    assert hb.calls[-1][1] == {"lock": None} and not hb.field("firma").lock.get("action")


def test_translations_cover_strings():
    src = Path(P.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'_\("((?:[^"\\]|\\.)*)"\)', src))
    missing = {s for s in used if s.replace("\\'", "'") not in P.TRANSLATIONS}
    assert not missing
    for label in E.OPERATIONS.values():
        assert label in P.TRANSLATIONS


def test_button_caption_down_applied(qtbot):
    import pymupdf

    from foliant.modules.forms import engine as E
    from foliant.modules.forms.panel import FieldPropertiesPanel
    pdf = pymupdf.open()
    pdf.new_page()
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 72), "invia", caption="Invia")
    calls = []

    def apply(keys, props, label):
        calls.append(props)
        for page, xref in keys:
            E.update_properties(pdf, page, xref, **props)
        return True

    panel = FieldPropertiesPanel(apply)
    qtbot.addWidget(panel)
    panel.set_fields(E.list_fields(pdf), ["invia"])
    panel.caption_down_edit.setText("Invio…")
    panel.caption_down_edit.editingFinished.emit()
    assert {"caption_down": "Invio…"} in calls
    assert E.field_info(pdf, 0, b).caption_down == "Invio…"


def test_export_name_applied(qtbot):
    import pymupdf

    from foliant.modules.forms import engine as E
    from foliant.modules.forms.panel import FieldPropertiesPanel
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 150, 72), "cognome")
    calls = []
    panel = FieldPropertiesPanel(lambda keys, props, label: calls.append(props) or True)
    qtbot.addWidget(panel)
    panel.set_fields(E.list_fields(pdf), ["cognome"])
    panel.export_name_edit.setText("surname")
    panel.export_name_edit.editingFinished.emit()
    assert {"export_name": "surname"} in calls


def test_system_fonts_listed(qtbot):
    from foliant.modules.forms.panel import FieldPropertiesPanel
    panel = FieldPropertiesPanel(lambda *a: True)
    qtbot.addWidget(panel)
    items = [panel.font_combo.itemData(i) for i in range(panel.font_combo.count())]
    assert items[:3] == ["Helvetica", "Times", "Courier"]
    from PySide6.QtGui import QFontDatabase
    if QFontDatabase.families():
        assert len([i for i in items if i]) > 3


def test_button_icons_layout_and_rollover_applied(qtbot, tmp_path):
    """FRM-18: disposizione, didascalia al passaggio e icone per stato dalla scheda Pulsante."""
    from PIL import Image

    from foliant.modules.forms.panel import FieldPropertiesPanel
    img = tmp_path / "logo.png"
    Image.new("RGB", (120, 80), (180, 20, 20)).save(img)
    pdf = pymupdf.open()
    pdf.new_page()
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 250, 100), "garanzia", caption="Garanzia")

    def apply(keys, props, label):
        for page, xref in keys:
            E.update_properties(pdf, page, xref, **props)
        panel.set_fields([E.field_info(pdf, p, x) for p, x in keys], ["garanzia"])
        return True

    panel = FieldPropertiesPanel(apply)
    qtbot.addWidget(panel)
    panel.choose_icon_file = lambda: str(img)
    panel.set_fields(E.list_fields(pdf), ["garanzia"])
    assert panel.caption_down_edit.placeholderText() == "uguale alla didascalia"
    panel.icon_rows["normal"][1].click()
    assert E.field_info(pdf, 0, b).icons == ("normal",)
    assert panel.icon_rows["normal"][0].text() == "impostata" and panel.icon_rows["down"][0].text() == "nessuna"
    panel.layout_combo.setCurrentIndex(panel.layout_combo.findData("icon_left"))
    assert E.field_info(pdf, 0, b).layout == "icon_left"
    panel.caption_rollover_edit.setText("Apri")
    panel.caption_rollover_edit.editingFinished.emit()
    assert E.field_info(pdf, 0, b).caption_rollover == "Apri"
    panel.icon_rows["normal"][2].click()
    assert E.field_info(pdf, 0, b).icons == ()


def test_button_event_applied(qtbot):
    from foliant.modules.forms.panel import FieldPropertiesPanel
    pdf = pymupdf.open()
    pdf.new_page()
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 72), "stampa", caption="Stampa")
    E.set_button_action(pdf, 0, b, {"type": "print"})

    def apply(keys, props, label):
        for page, xref in keys:
            E.update_properties(pdf, page, xref, **props)
        return True

    panel = FieldPropertiesPanel(apply)
    qtbot.addWidget(panel)
    panel.set_fields(E.list_fields(pdf), ["stampa"])
    assert panel.event_combo.currentData() == "U"
    panel.event_combo.setCurrentIndex(panel.event_combo.findData("E"))
    assert E.button_action(pdf, b) == {"type": "print", "name": "Print", "event": "E"}
    panel.set_fields(E.list_fields(pdf), ["stampa"])
    assert panel.event_combo.currentData() == "E"


def test_submit_action_applied(qtbot):
    from foliant.modules.forms.panel import FieldPropertiesPanel
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 20, 150, 40), "modello")
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 72), "invia", caption="Invia")

    def apply(keys, props, label):
        for page, xref in keys:
            E.update_properties(pdf, page, xref, **props)
        return True

    panel = FieldPropertiesPanel(apply)
    qtbot.addWidget(panel)
    panel.set_fields([E.field_info(pdf, 0, b)], ["modello", "invia"])
    panel.action_combo.setCurrentIndex(panel.action_combo.findData("submit"))
    assert E.button_action(pdf, b) == {}   # manca ancora l'indirizzo
    panel.action_uri.setText("ftp://x")
    panel.action_uri.editingFinished.emit()
    assert "mailto" in panel.action_error.text()
    panel.submit_format.setCurrentIndex(panel.submit_format.findData("xfdf"))
    panel.action_uri.setText("mailto:acquisti@esempio.test")
    panel.action_uri.editingFinished.emit()
    assert E.button_action(pdf, b) == {"type": "submit", "url": "mailto:acquisti@esempio.test", "format": "xfdf",
                                       "fields": [], "empty": False}
    panel.set_fields([E.field_info(pdf, 0, b)], ["modello", "invia"])
    assert panel.action_combo.currentData() == "submit" and panel.action_uri.text() == "mailto:acquisti@esempio.test"


def test_signature_constraints_applied(qtbot):
    from foliant.modules.forms.panel import FieldPropertiesPanel
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(50, 50, 250, 100), "Firma")

    def apply(keys, props, label):
        for page, xref in keys:
            E.update_properties(pdf, page, xref, **props)
        return True

    panel = FieldPropertiesPanel(apply)
    qtbot.addWidget(panel)
    panel.set_fields([E.field_info(pdf, 0, x)], ["Firma"])
    panel.sv_reasons.setText("Venditore; Acquirente")
    panel.sv_reasons.editingFinished.emit()
    panel.sv_certify.setCurrentIndex(panel.sv_certify.findData(2))
    assert E.field_info(pdf, 0, x).sv == {"reasons": ["Venditore", "Acquirente"], "certify": 2}
    panel.set_fields([E.field_info(pdf, 0, x)], ["Firma"])
    assert panel.sv_reasons.text() == "Venditore; Acquirente" and panel.sv_certify.currentData() == 2
