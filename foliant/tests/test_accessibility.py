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


# ------------------------------------------------------------------ contrasto (NFR-23, WCAG 2.x)
def _luminance(value) -> float:
    from PySide6.QtGui import QColor
    c = QColor(value) if isinstance(value, str) else value

    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c.redF()) + 0.7152 * channel(c.greenF()) + 0.0722 * channel(c.blueF())


def contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# testo / sfondo su cui il nucleo e i moduli lo scrivono (≥ 4,5:1)
TEXT_PAIRS = [(fg, bg) for fg in ("text", "text_muted", "accent", "danger", "success", "warning")
              for bg in ("window", "surface")] + [
    ("text", "surface_alt"), ("text_muted", "surface_alt"),     # passaggio del mouse, menu
    ("text", "accent_soft"),                                    # elementi selezionati negli elenchi
    ("accent_text", "accent"), ("accent_text", "accent_hover"),  # modalità attiva, pulsanti principali
]


def _themes():
    from foliant.ui import theme
    return (("chiaro", theme.LIGHT), ("scuro", theme.DARK))


def test_token_text_contrast():
    low = [(name, fg, bg, round(contrast(t[fg], t[bg]), 2)) for name, t in _themes()
           for fg, bg in TEXT_PAIRS if contrast(t[fg], t[bg]) < 4.5]
    assert not low, low


def test_checked_tool_button_text_contrast():
    """Il testo di un QToolButton attivo (es. «Seleziona testo») resta leggibile sul suo fondo."""
    import re

    from foliant.ui import theme
    for name, t in _themes():
        rule = re.search(r"^QToolButton:checked \{ background: (#\w+); color: (#\w+); \}",
                         theme.stylesheet(t), re.M)
        assert rule, name
        assert contrast(rule.group(2), rule.group(1)) >= 4.5, (name, rule.groups())


# contorno o indicatore / sfondo adiacente (≥ 3:1, WCAG 1.4.11)
OUTLINE_PAIRS = [("control_border", "window"), ("control_border", "surface")]


def test_token_outline_contrast():
    low = [(name, fg, bg, round(contrast(t[fg], t[bg]), 2)) for name, t in _themes()
           for fg, bg in OUTLINE_PAIRS if contrast(t[fg], t[bg]) < 3]
    assert not low, low


def test_input_outline_is_visible_without_focus(qapp, qtbot):
    """NFR-23: il contorno di un campo senza fuoco si distingue dal dialogo e dal riempimento."""
    from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton, QVBoxLayout

    from foliant.ui import theme
    try:
        for mode in ("light", "dark"):
            theme.apply(qapp, mode)
            dlg = QDialog()
            qtbot.addWidget(dlg)
            button = QPushButton("OK", dlg)
            edit = QLineEdit(dlg)
            layout = QVBoxLayout(dlg)
            layout.addWidget(button)
            layout.addWidget(edit)
            dlg.resize(240, 120)
            dlg.show()
            qtbot.waitExposed(dlg)
            button.setFocus()
            assert not edit.hasFocus()
            image = dlg.grab().toImage()
            y = edit.y() + edit.height() // 2
            edge = [image.pixelColor(edit.x() + dx, y) for dx in (0, 1)]
            for bg in ("window", "surface"):
                best = max(contrast(c, theme.color(bg)) for c in edge)
                assert best >= 3, (mode, bg, [c.name() for c in edge], round(best, 2))
            dlg.close()
    finally:
        theme.apply(qapp, "light")
