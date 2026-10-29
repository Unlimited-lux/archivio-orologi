"""Scheda Modifica in stile Excel: gruppi con nome del modulo del testo, nessuna voce nel vecchio menu ☰."""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt

from foliant.i18n import _
from foliant.modules import text_edit


def test_text_groups_in_the_edit_bar(window):
    acts = window.ctx.text_edit.actions
    bar = window.ctx.mode("modifica").toolbar
    titles = [t for t, _a in bar.groups()]
    assert titles[:2] == [_("Testo"), _("Inserisci")]
    assert all(title for title, group in bar.groups() if group)          # ogni gruppo ha un nome
    for key in ("edit", "find", "spell", "fonts"):
        assert bar.group_title(acts[key]) == _("Testo"), key
    assert bar.group_title(acts["add"]) == _("Inserisci")
    # etichette corte nella barra, nome completo nei menu e nella ricerca dei comandi
    assert acts["add"].iconText() == _("Testo") and acts["add"].text() == _("Aggiungi testo")
    assert acts["find"].iconText() == _("Sostituisci") and acts["find"].text() == _("Trova e sostituisci")
    for key in ("spell", "fonts"):                                      # voci secondarie: solo l'icona
        assert bar.widgetForAction(acts[key]).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def test_spell_and_find_are_only_in_the_bar(window):
    acts = window.ctx.text_edit.actions
    for key in ("spell", "find"):
        assert window.backstage.page_of(acts[key]) is None               # non in File › Altri comandi
    folder = pathlib.Path(text_edit.__file__).parent
    assert not [f.name for f in folder.glob("*.py") if "add_menu_action" in f.read_text(encoding="utf-8")]


def test_edit_shortcuts_unchanged(window):
    acts = window.ctx.text_edit.actions
    window.set_mode("modifica")
    assert acts["find"].shortcut().toString() == "Ctrl+H"
    assert acts["spell"].shortcut().toString() == "F7"
