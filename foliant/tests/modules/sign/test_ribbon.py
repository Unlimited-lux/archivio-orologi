"""Posizione dei comandi del modulo Firma: gruppi con nome della barra (stile Excel)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton

from foliant.modules import sign

MODULE = Path(sign.__file__).resolve().parent
ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly


def test_ribbon_has_three_named_groups(window):
    c = window.ctx.sign_controller
    a = c.actions
    bar = window.modes["firma"].toolbar
    groups = [(title, acts) for title, acts in bar.groups() if acts]
    assert [t for t, _a in groups] == ["Firme", "Firma digitale", "Identità"]
    library = next(x for x in bar.actions() if bar.widgetForAction(x) is c.library_button)
    assert groups[0][1] == [a["create"], a["initials"], library, a["edit"], a["embed"]]
    assert groups[1][1] == [a["digital"], a["invisible"], a["certify"], a["field"]]
    assert groups[2][1] == [a["verify"], a["ids"], a["self_signed"]]
    # «Incorpora le firme» e «Crea ID autofirmato…» erano solo nel menu ☰: ora sono nella barra
    assert bar.group_title(a["embed"]) == "Firme"
    assert bar.group_title(a["self_signed"]) == "Identità"
    assert a["embed"].text() == "Incorpora le firme" and a["embed"].iconText() == "Incorpora"
    assert a["self_signed"].text() == "Crea ID autofirmato…" and a["self_signed"].iconText() == "Nuovo ID"
    assert a["ids"].text() == "Gestisci firme e identità digitali…" and a["ids"].iconText() == "ID digitali"
    for key in ("edit", "invisible"):
        assert bar.widgetForAction(a[key]).toolButtonStyle() == ICON_ONLY, key
    for key in ("create", "initials", "embed", "digital", "certify", "field", "verify", "ids", "self_signed"):
        assert bar.widgetForAction(a[key]).toolButtonStyle() != ICON_ONLY, key


def test_saved_signatures_menu_button(window):
    c = window.ctx.sign_controller
    button = c.library_button
    assert button.text() == "Firme salvate" and button.isEnabled()      # anche senza documento
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    button.menu().aboutToShow.emit()
    texts = [x.text() for x in button.menu().actions()]
    assert texts[-1] == "Gestisci libreria…"


def test_module_no_longer_uses_the_old_menu(window):
    for f in MODULE.glob("*.py"):
        assert "add_menu_action" not in f.read_text("utf-8"), f.name
    actions = set(window.ctx.sign_controller.actions.values())
    assert not actions & {e.action for e in window.backstage.entries("altro")}


def test_ribbon_fits_at_1366_px(window, sample_pdf):
    """A 1366 px la barra Firma entra tutta: niente pulsante «»» dei comandi in eccesso."""
    view = window.open_path(sample_pdf)
    window.resize(1366, 800)
    window.set_mode("firma")
    QApplication.processEvents()
    bar = window.modes["firma"].toolbar
    assert bar.sizeHint().width() <= bar.width()
    ext = bar.findChild(QToolButton, "qt_toolbar_ext_button")
    assert ext is None or not ext.isVisible()
    window.close_view(view, ask=False)


def test_group_titles_follow_the_language(qtbot, settings):
    from foliant import i18n
    from foliant.ui.main_window import MainWindow
    i18n.set_language("en")
    try:
        win = MainWindow(settings)
        qtbot.addWidget(win)
        bar = win.modes["firma"].toolbar
        assert [t for t, acts in bar.groups() if acts] == ["Signatures", "Digital signature", "Identity"]
        a = win.ctx.sign_controller.actions
        assert (a["embed"].iconText(), a["self_signed"].iconText(), a["ids"].iconText()) == \
            ("Embed", "New ID", "Digital IDs")
    finally:
        i18n.set_language("it")
