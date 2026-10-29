"""Scheda File (come in Excel) e gruppi con nome delle barre contestuali."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from foliant.i18n import _


def test_file_button_opens_and_closes_the_file_tab(window, sample_pdf, qtbot):
    view = window.open_path(sample_pdf)
    assert window.file_button.text() == _("File")
    assert not hasattr(window, "main_menu")                 # niente più menu ☰
    window.file_button.click()
    assert window.backstage_visible() and window.file_button.isChecked()
    assert not window.statusBar().isVisible()
    assert not any(m.button.isChecked() for m in window.modes.values())
    qtbot.keyClick(QApplication.focusWidget() or window.backstage, Qt.Key.Key_Escape)
    assert not window.backstage_visible() and not window.file_button.isChecked()
    assert window.statusBar().isVisible()
    assert window.modes[window.current_mode_id].button.isChecked()
    window.show_backstage()
    window.set_mode("commenta")                             # un clic su una scheda chiude la scheda File
    assert not window.backstage_visible() and window.current_mode_id == "commenta"
    window.close_view(view, ask=False)


def test_f10_toggles_the_file_tab(window, qtbot):
    window.activateWindow()
    qtbot.keyClick(window, Qt.Key.Key_F10)
    assert window.backstage_visible()
    qtbot.keyClick(window.backstage, Qt.Key.Key_F10)
    assert not window.backstage_visible()


def test_commands_in_the_left_column_close_the_file_tab_first(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    seen = []
    window.act_save.triggered.connect(lambda: seen.append(window.backstage_visible()))
    window.show_backstage()
    window.backstage.nav_buttons["salva"].click()
    assert seen == [False]                                  # il comando parte con la scheda File già chiusa
    assert not window.backstage_visible()
    window.show_backstage()
    assert not window.backstage.nav_buttons["altro"].isVisibleTo(window.backstage) or \
        window.backstage.pages["altro"].entries
    window.hide_backstage()
    window.close_view(view, ask=False)


def test_module_entries_follow_action_state(window):
    ctx = window.ctx
    plain = ctx.action("Voce di prova", "star", lambda: None, requires_document=False)
    toggle = ctx.action("Opzione di prova", None, None, checkable=True, requires_document=False)
    needs_doc = ctx.action("Con documento", "star", lambda: None)
    for act, order in ((plain, 1), (toggle, 2), (needs_doc, 3)):
        ctx.add_file_action(act, "esporta", "Prova", "Descrizione", order=order)
    window.show_backstage("esporta")
    tiles = {t.action: t for t in window.backstage.tiles}
    assert tiles[plain].isEnabled() and tiles[plain].desc_label.text() == "Descrizione"
    assert not tiles[needs_doc].isEnabled()                 # nessun documento aperto
    tiles[toggle].click()                                   # una voce attivabile non chiude la scheda
    assert toggle.isChecked() and window.backstage_visible() and tiles[toggle].isChecked()
    fired = []
    plain.triggered.connect(lambda: fired.append(window.backstage_visible()))
    tiles[plain].click()
    assert fired == [False] and not window.backstage_visible()


def test_unknown_page_and_print_command(window):
    ctx = window.ctx
    act = ctx.action("Stampa di prova", "printer", lambda: None, requires_document=False)
    ctx.add_file_action(act, "stampa")
    other = ctx.action("Altrove", None, lambda: None, requires_document=False)
    ctx.add_file_action(other, "pagina-che-non-esiste")
    assert window.backstage.pages["stampa"].command is act
    assert window.backstage.page_of(other) == "altro"
    window.show_backstage()
    assert window.backstage.nav_buttons["stampa"].isVisibleTo(window.backstage)
    window.hide_backstage()


def test_info_page_summarises_the_document(window, sample_pdf):
    view = window.open_path(sample_pdf)
    window.show_backstage("informazioni")
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in window.backstage.body.findChildren(QLabel)]
    assert view.document.display_name in texts
    assert _("Pagine") in texts
    window.hide_backstage()
    window.close_view(view, ask=False)


def test_options_page_switches_theme(window):
    from PySide6.QtWidgets import QToolButton
    from foliant.ui import theme
    window.show_backstage("opzioni")
    buttons = {b.text(): b for b in window.backstage.body.findChildren(QToolButton) if b.property("segment")}
    assert {_("Come il sistema"), _("Chiaro"), _("Scuro"), "Italiano", "English"} <= set(buttons)
    buttons[_("Scuro")].click()
    assert theme.mode() == "dark"
    window.set_theme("light")
    window.hide_backstage()


def test_nav_is_keyboard_operable(window, qtbot):
    window.show_backstage("nuovo")
    nav = window.backstage.nav_buttons
    nav["nuovo"].setFocus()
    qtbot.keyClick(nav["nuovo"], Qt.Key.Key_Down)
    assert nav["apri"].hasFocus() or QApplication.focusWidget() is nav["apri"]
    qtbot.keyClick(nav["apri"], Qt.Key.Key_Return)
    assert window.backstage.current == "apri"
    window.hide_backstage()


def test_ribbon_groups_have_captions_and_accept_actions_from_modules(window):
    ctx = window.ctx
    view_mode = ctx.mode("visualizza")
    bar = view_mode.toolbar
    titles = [t for t, _a in bar.groups() if t]
    assert titles[:3] == [_("Layout pagina"), _("Ruota"), _("Mostra")]
    extra = QAction("Prova di gruppo", window)
    view_mode.add_action(extra, group="ruota", compact=True)
    assert bar.group_title(extra) == _("Ruota")
    acts = bar.actions()
    assert acts.index(extra) == acts.index(window.act_rotate_view) + 1
    from PySide6.QtWidgets import QToolButton
    assert bar.widgetForAction(extra).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    button = view_mode.add_menu_button("Menu di prova", "star", [QAction("Uno", window), None,
                                                                 QAction("Due", window)], group="mostra")
    assert isinstance(button, QToolButton) and len(button.menu().actions()) == 3
    assert bar.contentsMargins().bottom() >= bar.caption_height()
    # i gruppi vuoti non lasciano separatori doppi
    leggi = ctx.mode("leggi").toolbar
    seps = [a for a in leggi.actions() if a.isSeparator() and a.isVisible()]
    visible_groups = [g for g in leggi.groups() if any(a.isVisible() for a in g[1])]
    assert len(seps) == len(visible_groups) - 1


def test_quick_access_toolbar(window):
    act = QAction("Rapida", window)
    window.ctx.add_quick_access_action(act)
    acts = window.header.actions()
    assert acts.index(act) == acts.index(window.act_undo) - 1
    window.ctx.add_quick_access_action(act)                 # una volta sola
    assert window.header.actions().count(act) == 1


def test_command_search_button(window):
    act = window.ctx.action("Cerca un comando di prova", "command", lambda: None, requires_document=False)
    window.ctx.set_command_search(act)
    assert window.command_button.defaultAction() is act and window._command_slot.isVisible()
    window.ctx.show_file_tab("guida")
    assert window.backstage_visible() and window.backstage.current == "guida"
    window.hide_backstage()
