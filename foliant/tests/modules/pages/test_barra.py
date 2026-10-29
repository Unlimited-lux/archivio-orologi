"""Scheda Pagine in stile Excel: gruppi con nome nella barra e «Unisci file…» nella scheda File."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

import foliant.modules.pages as pkg


def _widget_action(bar, widget):
    """L'azione della barra che contiene `widget` (i pulsanti ▾ sono widget, non azioni)."""
    return next(a for a in bar.actions() if bar.widgetForAction(a) is widget)


def test_gruppi_con_nome_nella_barra(pages_window):
    window, _view = pages_window
    c = window.ctx.pages_controller
    bar = window.ctx.mode("pagine").toolbar
    assert [title for title, _acts in bar.groups()] == ["Pagine", "Inserisci e dividi", "Formato"]
    for act in (c.act_rotate_left, c.act_rotate_right, c.act_delete, c.act_duplicate):
        assert bar.group_title(act) == "Pagine"
    for button in (c.btn_insert, c.btn_split, c.btn_arrange):
        assert bar.group_title(_widget_action(bar, button)) == "Inserisci e dividi"
    assert bar.group_title(c.act_extract) == "Inserisci e dividi"
    for button in (c.btn_layout, c.btn_stamps, c.btn_more):
        assert bar.group_title(_widget_action(bar, button)) == "Formato"
    assert bar.group_title(c.act_unlock) == "Formato"
    # le voci degli altri moduli per le pagine (es. «Analizza pagine» dell'Assistente IA) finiscono in Formato
    for act in bar.actions():
        if not act.isSeparator() and act.text() == "Analizza pagine":
            assert bar.group_title(act) == "Formato"
    # ruota: solo l'icona, il nome resta nel suggerimento; gli altri pulsanti hanno il testo
    for act in (c.act_rotate_left, c.act_rotate_right):
        assert bar.widgetForAction(act).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert c.btn_split.text() == "Dividi e unisci" and c.btn_split.accessibleName() == "Dividi e unisci"


def test_unisci_in_file_nuovo_e_nella_barra(window):
    c = window.ctx.pages_controller
    backstage = window.backstage
    assert backstage.page_of(c.act_merge) == "nuovo"
    entry = next(e for e in backstage.entries("nuovo") if e.action is c.act_merge)
    assert entry.group == "Crea" and entry.description == "Più PDF o immagini in un solo documento"
    new_doc = next(e for e in backstage.entries("nuovo") if e.action is window.act_new)
    assert new_doc.group == entry.group                       # stesso gruppo di «Nuovo documento», dopo
    assert backstage.entries("nuovo").index(new_doc) < backstage.entries("nuovo").index(entry)
    # resta anche in «Dividi e unisci ▾», insieme a «Dividi documento…» (che è solo lì)
    assert c.act_merge in c.btn_split.menu().actions()
    assert c.act_split in c.btn_split.menu().actions()
    assert backstage.page_of(c.act_split) is None
    # senza documenti la voce della scheda File funziona (il pulsante della barra è spento)
    assert window.current_view() is None and not c.btn_split.isEnabled()
    window.show_backstage("nuovo")
    tile = next(t for t in backstage.tiles if t.action is c.act_merge)
    assert tile.isEnabled() and tile.desc_label.text() == "Più PDF o immagini in un solo documento"
    window.hide_backstage()


def test_nessuna_voce_in_altri_comandi(window):
    c = window.ctx.pages_controller
    stray = [e.action.text() for e in window.backstage.entries("altro") if e.action in c.all_actions]
    assert not stray
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
    bar = win.ctx.mode("pagine").toolbar
    assert [title for title, _acts in bar.groups()] == ["Pages", "Insert and split", "Format"]
    c = win.ctx.pages_controller
    assert c.btn_layout.text() == "Layout"                     # il pulsante «Formato ▾» resta «Layout»
    entry = next(e for e in win.backstage.entries("nuovo") if e.action is c.act_merge)
    assert entry.group == "Create" and entry.description == "Several PDFs or images in a single document"
