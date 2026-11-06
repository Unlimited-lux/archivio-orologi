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


def test_f6_visits_every_region(window, sample_pdf, qtbot):
    """C-6: F6 passa per tutte le regioni della finestra (intestazione, barra della scheda, pannello
    laterale, documento, pannello delle proprietà, barra di stato); Maiusc+F6 al contrario."""
    from PySide6.QtWidgets import QLineEdit

    view = window.open_path(sample_pdf)
    if not window._sidebar:
        window.ctx.add_sidebar_panel("prova_f6", "Prova", "file", QLineEdit())
    window.show_sidebar_panel(next(iter(window._sidebar)))
    window.ctx.add_properties_panel("prova_f6", QLineEdit())
    window.show_properties("prova_f6")
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    regions = {"intestazione": window.header, "barra della scheda": window.context_stack,
               "pannello laterale": window.sidebar, "documento": view, "proprietà": window.properties,
               "barra di stato": window.statusBar()}

    def where():
        focus = QApplication.focusWidget()
        for name, region in regions.items():
            if focus is region or (focus is not None and region.isAncestorOf(focus)):
                return name
        return focus

    view.setFocus()
    assert where() == "documento"
    forward = []
    for _ in range(6):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F6)
        forward.append(where())
    assert forward == ["proprietà", "barra di stato", "intestazione", "barra della scheda", "pannello laterale",
                       "documento"]
    backward = []
    for _ in range(6):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        backward.append(where())
    assert backward == ["pannello laterale", "barra della scheda", "intestazione", "barra di stato", "proprietà",
                        "documento"]
    # senza pannelli aperti le regioni nascoste si saltano
    window.hide_properties()
    window.sidebar.hide()
    view.setFocus()
    seen = []
    for _ in range(4):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F6)
        seen.append(where())
    assert seen == ["barra di stato", "intestazione", "barra della scheda", "documento"]
    # scheda File aperta: intestazione e scheda File
    window.show_backstage()
    regions = {"intestazione": window.header, "scheda File": window.backstage}
    assert where() == "scheda File"
    seen = []
    for _ in range(2):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_F6)
        seen.append(where())
    assert seen == ["intestazione", "scheda File"]
    window.hide_backstage()


def _focus_change(qtbot, widget, elsewhere):
    """Immagini di `widget` senza e con il fuoco (dato con Tab)."""
    elsewhere.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(elsewhere.hasFocus)
    before = widget.grab().toImage()
    widget.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(widget.hasFocus)
    return before, widget.grab().toImage()


def test_focus_indicator_is_visible(qapp, qtbot):
    """NFR-21: il controllo con il fuoco ha un contorno con contrasto ≥ 3:1 rispetto allo stato
    senza fuoco, e il contenuto non si sposta."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QCheckBox, QPushButton, QRadioButton, QTabBar, QToolButton, QVBoxLayout, QWidget

    from foliant.ui import theme
    from foliant.ui.context import ToolBar
    try:
        for mode in ("light", "dark"):
            theme.apply(qapp, mode)
            w = QWidget()
            qtbot.addWidget(w)
            layout = QVBoxLayout(w)
            other = QPushButton("Altro")
            bar = ToolBar("Prova")
            bar.setObjectName("contextbar")
            bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            plain, checked = QAction("Ruota", w), QAction("Seleziona", w, checkable=True, checked=True)
            bar.addAction(plain)
            bar.addAction(checked)
            mode_button = QToolButton()
            mode_button.setText("Leggi")
            mode_button.setProperty("modeButton", True)
            mode_button.setCheckable(True)
            mode_button.setChecked(True)
            tabs = QTabBar()
            tabs.addTab("Uno")
            tabs.addTab("Due")
            controls = {"pulsante barra": bar.widgetForAction(plain), "pulsante attivo": bar.widgetForAction(checked),
                        "modalità": mode_button, "pulsante": QPushButton("Pulsante"), "schede": tabs,
                        "casella": QCheckBox("Casella"), "opzione": QRadioButton("Opzione")}
            for widget in (other, bar, *list(controls.values())[2:]):
                layout.addWidget(widget)
            w.show()
            w.activateWindow()
            qtbot.waitUntil(w.isActiveWindow)
            for name, widget in controls.items():
                before, after = _focus_change(qtbot, widget, other)
                assert before.size() == after.size(), (mode, name)
                best, moved = 1.0, 0
                for y in range(before.height()):
                    for x in range(before.width()):
                        a, b = before.pixelColor(x, y), after.pixelColor(x, y)
                        if a != b:
                            best = max(best, contrast(a, b))
                            moved += 3 <= y < before.height() - 3 and 6 <= x < before.width() - 6
                assert best >= 3, (mode, name, round(best, 2))
                if widget is not tabs:              # il bordo di una scheda cade dentro la barra
                    assert moved == 0, (mode, name, moved)
            w.close()
    finally:
        theme.apply(qapp, "light")


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
OUTLINE_PAIRS = [("control_border", "window"), ("control_border", "surface"),
                 # indicatore del fuoco: accent sugli sfondi dei controlli, accent_text su quelli in accent
                 ("accent", "window"), ("accent", "surface"), ("accent", "surface_alt"), ("accent", "accent_soft"),
                 ("accent_text", "accent")]


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


def _ink(image, background):
    """Colore del testo disegnato: il punto più lontano dallo sfondo (lontano dai bordi
    arrotondati, dove si vede il fondo della finestra)."""
    best, ink = 1.0, background
    for y in range(4, image.height() - 4):
        for x in range(4, image.width() - 4):
            c = image.pixelColor(x, y)
            ratio = contrast(c, background)
            if ratio > best:
                best, ink = ratio, c
    return ink


def test_disabled_tool_buttons_look_disabled(qapp, qtbot):
    """C-3: un pulsante disattivato con il nome (barra della scheda, schede, «File», scheda File)
    non ha il colore di uno attivo: il testo si attenua, restando leggibile (≥ 3:1) in chiaro e in scuro."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton, QVBoxLayout, QWidget

    from foliant.ui import theme
    from foliant.ui.context import ToolBar

    def tool_button(parent, text, **props):
        b = QToolButton(parent)
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        for key, value in props.items():
            if key == "name":
                b.setObjectName(value)
            elif key == "checked":
                b.setCheckable(True)
                b.setChecked(value)
            else:
                b.setProperty(key, value)
        return b

    try:
        for mode in ("light", "dark"):
            theme.apply(qapp, mode)
            w = QWidget()
            qtbot.addWidget(w)
            layout = QVBoxLayout(w)
            bar = ToolBar("Prova")
            bar.setObjectName("contextbar")
            bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            pairs = {}
            for name in ("Applica redazioni", "Assistente IA"):
                on, off = QAction(name, w), QAction(name, w)
                off.setEnabled(False)
                bar.addAction(on)
                bar.addAction(off)
                pairs["barra " + name] = (bar.widgetForAction(on), bar.widgetForAction(off), "surface")
            layout.addWidget(bar)
            header = QWidget()
            header.setObjectName("sidebar")             # fondo «surface», come la barra in alto
            row = QHBoxLayout(header)
            for label, props in (("scheda", {"modeButton": True}), ("scheda attiva", {"modeButton": True, "checked": True}),
                                 ("File", {"fileButton": True}), ("pulsante attivo", {"checked": True})):
                on, off = tool_button(header, label, **props), tool_button(header, label, **props)
                off.setEnabled(False)
                row.addWidget(on)
                row.addWidget(off)
                pairs[label] = (on, off, "surface")
            layout.addWidget(header)
            nav = QFrame()
            nav.setObjectName("backstageNav")
            nav_row = QHBoxLayout(nav)
            on, off = tool_button(nav, "Salva", name="backstageNavButton"), tool_button(nav, "Salva", name="backstageNavButton")
            off.setEnabled(False)
            nav_row.addWidget(on)
            nav_row.addWidget(off)
            pairs["scheda File › Salva"] = (on, off, "accent")
            layout.addWidget(nav)
            w.show()
            qtbot.waitExposed(w)
            def shot(button):          # con il fondo della barra o della colonna sotto il pulsante
                return button.parentWidget().grab(button.geometry()).toImage()

            for name, (on, off, bg_name) in pairs.items():
                bg = bg_off = theme.color(bg_name)
                if name in ("scheda attiva", "pulsante attivo"):          # fondo del pulsante attivo
                    bg = shot(on).pixelColor(on.width() // 2, 4)
                    bg_off = shot(off).pixelColor(off.width() // 2, 4)
                ink_on, ink_off = _ink(shot(on), bg), _ink(shot(off), bg_off)
                diff = max(abs(ink_on.red() - ink_off.red()), abs(ink_on.green() - ink_off.green()),
                           abs(ink_on.blue() - ink_off.blue()))
                assert diff >= 40, (mode, name, ink_on.name(), ink_off.name())       # non sembra attivo
                assert contrast(ink_off, bg_off) >= 3, (mode, name, ink_off.name(), round(contrast(ink_off, bg_off), 2))
            w.close()
    finally:
        theme.apply(qapp, "light")


def test_disabled_file_tab_command_looks_disabled(window, qtbot):
    """C-3 nella finestra vera: senza documento «Salva» della scheda File è attenuato rispetto ad «Apri»."""
    from foliant.ui import theme
    try:
        for mode in ("light", "dark"):
            window.set_theme(mode)
            window.show_backstage("informazioni")
            on, off = window.backstage.nav_buttons["apri"], window.backstage.nav_buttons["salva"]
            assert on.isEnabled() and not off.isEnabled()
            bg = theme.color("accent")
            inks = [_ink(b.parentWidget().grab(b.geometry()).toImage(), bg) for b in (on, off)]
            assert max(abs(inks[0].red() - inks[1].red()), abs(inks[0].green() - inks[1].green())) >= 40, \
                (mode, [c.name() for c in inks])
            assert contrast(inks[1], bg) >= 3, (mode, inks[1].name())
            window.hide_backstage()
    finally:
        window.set_theme("light")


def test_menu_button_focus_keeps_arrow_space(qapp, qtbot):
    """NFR-21: nei pulsanti con menu il fuoco non toglie lo spazio della freccia (l'icona resta ferma)."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
    from PySide6.QtWidgets import QLineEdit, QMenu, QToolButton, QVBoxLayout, QWidget

    from foliant.ui import theme

    def red_columns(img):
        xs = [x for y in range(img.height()) for x in range(img.width())
              if (c := img.pixelColor(x, y)).red() > 200 and c.green() < 60 and c.blue() < 60]
        return min(xs), max(xs)

    try:
        theme.apply(qapp, "light")
        w = QWidget()
        qtbot.addWidget(w)
        layout = QVBoxLayout(w)
        other = QLineEdit()
        layout.addWidget(other)
        pm = QPixmap(20, 20)
        pm.fill(QColor(255, 0, 0))
        button = QToolButton()              # come _menu_button del modulo annotazioni
        button.setDefaultAction(QAction(QIcon(pm), "Evidenzia", w))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        menu = QMenu(button)
        menu.addAction("Sottolinea")
        button.setMenu(menu)
        layout.addWidget(button)
        w.show()
        w.activateWindow()
        qtbot.waitUntil(w.isActiveWindow)
        before, after = _focus_change(qtbot, button, other)
        assert abs(red_columns(after)[0] - red_columns(before)[0]) <= 1
        w.close()
    finally:
        theme.apply(qapp, "light")
