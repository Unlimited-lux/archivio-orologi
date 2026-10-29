"""Scheda Modifica in stile Excel: gruppi del modulo oggetti e voci del vecchio menu ☰ nelle nuove posizioni."""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt

from foliant.i18n import _
from foliant.modules import objects as O


def _state(window) -> dict:
    return O._st(window.ctx)


def test_object_groups_in_the_edit_bar(window):
    st = _state(window)
    acts = st["actions"]
    bar = window.ctx.mode("modifica").toolbar
    assert [t for t, _a in bar.groups()] == [_("Testo"), _("Inserisci"), _("Oggetti")]
    assert all(title and group for title, group in bar.groups())
    assert bar.group_title(acts["insert"]) == _("Inserisci")
    assert bar.group_title(acts["links"]) == _("Inserisci")
    assert acts["insert"].iconText() == _("Immagine") and acts["insert"].text() == _("Aggiungi immagine")
    assert bar.group_title(window.ctx.text_edit.actions["add"]) == _("Inserisci")   # Testo, Immagine, Collegamento
    inserisci = dict(bar.groups())[_("Inserisci")]
    assert inserisci.index(window.ctx.text_edit.actions["add"]) < inserisci.index(acts["insert"])
    assert bar.group_title(acts["objects"]) == _("Oggetti")
    for key in ("rotate_ccw", "rotate_cw", "flip_h", "flip_v", "crop", "replace", "delete"):
        assert bar.group_title(acts[key]) == _("Oggetti"), key
        assert bar.widgetForAction(acts[key]).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    oggetti = [bar.widgetForAction(a) for a in dict(bar.groups())[_("Oggetti")]]
    assert st["arrange_button"] in oggetti and st["more_button"] in oggetti
    assert st["more_button"].toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert st["more_button"].toolTip() and st["more_button"].accessibleName() == _("Altro")
    assert st["controller"]._opacity_slider.parent() in oggetti                     # Opacità nel gruppo


def test_extract_all_images_in_file_export_and_more_menu(window):
    st = _state(window)
    act = st["actions"]["extract_all"]
    assert window.backstage.page_of(act) == "esporta"
    entry = next(e for e in window.backstage.entries("esporta") if e.action is act)
    assert entry.group == _("Esporta") and entry.description == _("Salva le immagini del PDF in una cartella")
    assert act in st["more_button"].menu().actions()                    # resta anche in Modifica › Altro ▾


def test_detect_urls_only_in_the_more_menu(window):
    st = _state(window)
    act = st["actions"]["detect_urls"]
    assert window.backstage.page_of(act) is None
    assert act in st["more_button"].menu().actions()
    folder = pathlib.Path(O.__file__).parent
    assert not [f.name for f in folder.glob("*.py") if "add_menu_action" in f.read_text(encoding="utf-8")]
