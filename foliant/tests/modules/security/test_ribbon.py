"""Posizione dei comandi del modulo Proteggi: gruppi della barra e scheda File (stile Excel)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton

from foliant.modules import security

MODULE = Path(security.__file__).resolve().parent
ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly


@pytest.fixture
def sec(window):
    c = security.controller_for(window.ctx)
    assert c is not None
    return c


def _entry(window, page, action):
    found = [e for e in window.backstage.entries(page) if e.action is action]
    assert found, (page, action.text())
    return found[0]


def _widget_action(bar, widget):
    return next(a for a in bar.actions() if bar.widgetForAction(a) is widget)


def test_ribbon_has_three_named_groups(window, sec):
    bar = window.modes["proteggi"].toolbar
    groups = [(title, acts) for title, acts in bar.groups() if acts]
    assert [t for t, _a in groups] == ["Protezione", "Oscuramento", "Pulizia"]
    a = sec.actions
    cipher = _widget_action(bar, sec.encryption_button)
    assert groups[0][1] == [a["protect"], a["remove"], cipher, a["policies"], a["properties"]]
    assert groups[1][1] == [a[k] for k in ("area", "text", "find", "patterns", "pages", "highlights", "style",
                                           "panel", "apply")]
    assert groups[2][1] == [a[k] for k in ("inspect", "sanitize", "metadata", "flatten")]
    for key in ("remove", "policies", "properties", "text", "pages", "highlights", "style", "panel",
                "inspect", "metadata", "flatten"):
        assert bar.widgetForAction(a[key]).toolButtonStyle() == ICON_ONLY, key
    for key in ("protect", "area", "find", "patterns", "apply", "sanitize"):
        assert bar.widgetForAction(a[key]).toolButtonStyle() != ICON_ONLY, key
    # nella barra un'etichetta più corta; menu, scheda File e ricerca dei comandi usano il nome intero
    assert a["protect"].iconText() == "Password" and a["protect"].text() == "Password e permessi"
    assert a["apply"].iconText() == "Applica" and a["apply"].text() == "Applica redazioni"
    window.set_mode("proteggi")                     # scorciatoia invariata, attiva nella scheda Proteggi
    assert a["text"].shortcut().toString() == "Ctrl+Shift+R"


def test_encryption_menu_button(window, sec):
    a = sec.actions
    button = sec.encryption_button
    assert button.text() == "Cifratura"
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    items = [x for x in button.menu().actions() if not x.isSeparator()]
    assert items == [a["cert_encrypt"], a["cert_open"], a["envelope"], a["new_envelope"], a["vault"]]
    assert button.isEnabled()                       # «Apri con certificato…» e altri non chiedono un documento
    assert not a["cert_encrypt"].isEnabled() and a["cert_open"].isEnabled() and a["vault"].isEnabled()


def test_policy_button_carries_the_policy_menu(window, sec):
    """Una sola azione «Policy di protezione»: clic = gestione, freccia (e File › Opzioni) = le policy."""
    act = sec.actions["policies"]
    assert act.menu() is sec.policy_menu
    button = window.modes["proteggi"].toolbar.widgetForAction(act)
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
    texts = [a.text() for a in window.findChildren(type(act)) if a.text() == "Policy di protezione"]
    assert len(texts) == 1                           # niente azione doppia creata solo per il vecchio menu
    for _n in range(2):                              # il menu si ricostruisce a ogni apertura
        sec.fill_policy_menu(sec.policy_menu)
        assert sec.policy_menu.actions()[-1] is sec.actions["manage_policies"]
    assert sec.actions["manage_policies"].text() == "Gestisci policy…"
    # «Cerca un comando» salta le azioni con un menu: la gestione delle policy resta cercabile
    palette = getattr(window.ctx, "viewer_parts", {}).get("commands")
    if palette is not None:
        found = palette.search("policy")
        assert sec.actions["manage_policies"] in found and act not in found


def test_file_tab_positions(window, sec):
    a = sec.actions
    b = window.backstage
    expected = [
        ("protect", "informazioni", "Proteggi il documento", "Password di apertura, stampa e modifica"),
        ("remove", "informazioni", "Proteggi il documento", "Toglie password e permessi"),
        ("new_envelope", "informazioni", "Proteggi il documento", "PDF cifrato da inviare con allegati"),
        ("sanitize", "informazioni", "Controlla il documento", "Metadati, livelli, script e testo nascosto"),
        ("metadata", "informazioni", "Controlla il documento", "Vedi e cancella i metadati del documento"),
        ("cert_open", "apri", "Apri", "PDF cifrati con un certificato digitale"),
        ("policies", "opzioni", "Sicurezza", None),
        ("revoke", "opzioni", "Sicurezza", None),
        ("lock", "opzioni", "Sicurezza", None),
        ("lock_settings", "opzioni", "Sicurezza", None),
    ]
    for key, page, group, description in expected:
        assert b.page_of(a[key]) == page, key
        e = _entry(window, page, a[key])
        assert e.group == group, key
        if description is not None:
            assert e.description == description, key
        else:
            assert e.description, key
    # «Proteggi il documento» prima di «Controlla il documento», come in Excel
    groups = [e.group for e in b.entries("informazioni")]
    assert groups.index("Proteggi il documento") < groups.index("Controlla il documento")
    # «Apri con certificato…» dopo Apri… e Riapri scheda chiusa
    assert [e.action for e in b.entries("apri")].index(a["cert_open"]) > \
        [e.action for e in b.entries("apri")].index(window.act_reopen)
    # nessuna voce del modulo in «Altri comandi»
    others = {e.action for e in b.entries("altro")}
    assert not others & set(a.values())


def test_file_tab_tiles(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    a = sec.actions
    window.show_backstage("informazioni")
    tiles = {t.action: t for t in window.backstage.tiles}
    assert {a["protect"], a["remove"], a["new_envelope"], a["sanitize"], a["metadata"]} <= set(tiles)
    assert tiles[a["protect"]].isEnabled() and not tiles[a["remove"]].isEnabled()   # documento non protetto
    window.show_backstage("opzioni")
    tiles = {t.action: t for t in window.backstage.tiles}
    assert {a["policies"], a["revoke"], a["lock"], a["lock_settings"]} <= set(tiles)
    assert not tiles[a["policies"]].state_label.pixmap().isNull()         # freccia del menu delle policy
    sec._session_trust = {"files": {"x": "y"}, "folders": ["z"]}
    tiles[a["revoke"]].click()
    assert not window.backstage_visible()
    assert sec._session_trust == {"files": {}, "folders": []}
    window.close_view(view, ask=False)


def test_module_no_longer_uses_the_old_menu():
    for f in MODULE.glob("*.py"):
        assert "add_menu_action" not in f.read_text("utf-8"), f.name


def test_ribbon_fits_at_1366_px(window, sec, sample_pdf):
    """A 1366 px la barra Proteggi entra tutta: niente pulsante «»» dei comandi in eccesso."""
    view = window.open_path(sample_pdf)
    window.resize(1366, 800)
    window.set_mode("proteggi")
    QApplication.processEvents()
    bar = window.modes["proteggi"].toolbar
    assert bar.sizeHint().width() <= bar.width()
    ext = bar.findChild(QToolButton, "qt_toolbar_ext_button")
    assert ext is None or not ext.isVisible()
    window.close_view(view, ask=False)
