"""Scheda Moduli in stile Excel: gruppi con nome nella barra (Campi, Disposizione, Dati); le voci
del vecchio menu ☰ sono solo in «Dati e modulo ▾»."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QToolButton

import foliant.modules.forms as pkg
from foliant.modules import forms
from foliant.modules.forms import engine as E


def _button_with(bar, action) -> tuple[QToolButton, object]:
    """Il pulsante ▾ della barra il cui menu contiene `action`, con la sua azione nella barra."""
    for act in bar.actions():
        w = bar.widgetForAction(act)
        if isinstance(w, QToolButton) and w.menu() is not None and action in w.menu().actions():
            return w, act
    raise AssertionError(f"nessun pulsante con {action.text()}")


def test_gruppi_con_nome_nella_barra(window):
    m = forms.instance(window.ctx)
    bar = window.ctx.mode("moduli").toolbar
    assert [title for title, _acts in bar.groups()] == ["Campi", "Disposizione", "Dati"]
    assert bar.group_title(m.act_select) == "Campi"
    for kind in (E.TEXT, E.CHECKBOX, E.RADIO, E.COMBO, E.LIST, "date"):
        assert bar.group_title(m.create_actions[kind]) == "Campi"
    _more, more_act = _button_with(bar, m.create_actions[E.SIGNATURE])          # «Altri campi ▾»
    assert bar.group_title(more_act) == "Campi"
    assert bar.group_title(m.act_detect) == "Campi"
    _field, field_act = _button_with(bar, m.act_delete)                         # «Campo ▾»
    assert bar.group_title(field_act) == "Campi"
    _align, align_act = _button_with(bar, m.arrange_actions["left"])
    assert bar.group_title(align_act) == "Disposizione"
    _tab, tab_act = _button_with(bar, m.act_tab_numbers)
    assert bar.group_title(tab_act) == "Disposizione"
    assert bar.group_title(m.act_preview) == "Dati"
    assert m.act_preview.iconText() == "Anteprima" and m.act_preview.text() == "Anteprima compilazione"
    _data, data_act = _button_with(bar, m.act_import)
    assert bar.group_title(data_act) == "Dati"


def test_voci_del_vecchio_menu_solo_in_dati_e_modulo(window):
    m = forms.instance(window.ctx)
    bar = window.ctx.mode("moduli").toolbar
    old_menu = (m.act_import, m.act_export, m.act_reset, m.act_flatten, m.act_collect, m.act_series)
    data, _act = _button_with(bar, m.act_import)
    assert data.text() == "Dati e modulo"
    for act in old_menu:
        assert act in data.menu().actions(), act.text()
        assert window.backstage.page_of(act) is None, act.text()          # né Altri comandi né altre pagine
    # «Raccogli dati da più PDF…» non richiede un documento: il pulsante resta usabile anche senza
    assert window.current_view() is None
    assert data.isEnabled() and m.act_collect.isEnabled()
    for path in Path(pkg.__file__).parent.glob("*.py"):
        assert "add_menu_action(" not in path.read_text(encoding="utf-8"), path.name


@pytest.fixture
def english_window(qtbot, settings):
    from PySide6.QtWidgets import QApplication

    from foliant.i18n import language, set_language
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow
    previous = language()
    set_language("en")
    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    yield win
    set_language(previous)


def test_gruppi_tradotti(english_window):
    win = english_window
    bar = win.ctx.mode("moduli").toolbar
    assert [title for title, _acts in bar.groups()] == ["Fields", "Layout", "Data"]
    assert forms.instance(win.ctx).act_preview.iconText() == "Preview"
