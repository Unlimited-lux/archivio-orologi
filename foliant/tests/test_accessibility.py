"""Accessibilità del nucleo: fuoco da tastiera (NFR-21) e contrasto dei colori (NFR-23)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _tab_round(qtbot, start, presses: int = 90) -> set:
    """Widget che ricevono il fuoco premendo Tab `presses` volte a partire da `start`."""
    start.setFocus(Qt.FocusReason.TabFocusReason)
    if not start.hasFocus():
        start.focusNextChild()
    reached = set()
    for _ in range(presses):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        reached.add(QApplication.focusWidget())
    return reached


def test_toolbar_buttons_are_reachable_with_tab(window, sample_pdf, qtbot):
    """NFR-21: i pulsanti che le barre del nucleo creano per le azioni si raggiungono con Tab."""
    mode = window.ctx.add_mode("prova_fuoco", "Prova", "file", order=999)
    actions = [window.ctx.action(f"Comando {i}", "file", lambda: None) for i in range(3)]
    for act in actions:
        mode.add_action(act)
    direct = window.ctx.action("Diretto", "file", lambda: None)
    mode.toolbar.addAction(direct)           # anche senza Mode.add_action
    window.open_path(sample_pdf)
    window.set_mode("prova_fuoco")
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    context_buttons = [mode.toolbar.widgetForAction(a) for a in actions + [direct]]
    header_buttons = [window.header.widgetForAction(a) for a in
                      (window.act_open, window.act_save, window.act_undo, window.act_redo)]
    for button in context_buttons + header_buttons:
        assert button.focusPolicy() == Qt.FocusPolicy.TabFocus, button.text()
    reached = _tab_round(qtbot, window.header)
    missing = [b.text() for b in context_buttons if b not in reached]
    assert not missing, missing
    # i pulsanti della barra in alto abilitati (Apri; Salva/Annulla/Ripeti sono disattivati)
    assert header_buttons[0] in reached
