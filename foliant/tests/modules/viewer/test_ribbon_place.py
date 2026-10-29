"""Posizione delle voci di lettura e vista nelle schede Leggi e Visualizza e nella scheda File
(il menu ☰ non c'è più): gruppi, ordine, pulsanti con la sola icona, etichette corte e menu ▾."""

from __future__ import annotations

import ast
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolButton

from foliant.i18n import _
from foliant.modules.viewer.ribbon_place import toolbar_action

from .conftest import part

VIEWER = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "viewer"
MY_PARTS = ("navigation", "search", "advsearch", "bookmarks", "thumbnails", "zoomfit", "reading", "appearance",
            "measure", "speech", "presentation", "reflow", "session", "tools")


def _bar(window, mode_id):
    return window.ctx.mode(mode_id).toolbar


def _button(window, mode_id, action) -> QToolButton:
    button = _bar(window, mode_id).widgetForAction(action)
    assert isinstance(button, QToolButton), action.text()
    return button


def _compact(window, mode_id, action) -> bool:
    return _button(window, mode_id, action).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly


def _group(window, mode_id, title) -> list[QAction]:
    for name, acts in _bar(window, mode_id).groups():
        if name == title:
            return acts
    raise AssertionError(f"gruppo «{title}» assente in {mode_id}")


def _in_order(acts: list[QAction], *expected: QAction) -> bool:
    positions = [acts.index(a) for a in expected]
    return positions == sorted(positions)


def _part_actions(window) -> list[QAction]:
    out = []
    for name in MY_PARTS:
        ctl = part(window, name)
        for value in vars(ctl).values():
            if isinstance(value, QAction):
                out.append(value)
            elif isinstance(value, dict):
                out.extend(v for v in value.values() if isinstance(v, QAction))
    return out


# ------------------------------------------------------------------ Leggi
def test_leggi_has_only_tools_zoom_go_and_find(viewer_window):
    titles = [t for t, acts in _bar(viewer_window, "leggi").groups() if t]
    assert titles == [_("Strumenti"), _("Zoom"), _("Vai"), _("Trova")]
    leggi = _bar(viewer_window, "leggi").actions()
    for name, attr in (("presentation", "act_start"), ("reading", "act_reading"), ("reading", "act_autoscroll"),
                       ("reflow", "act")):
        assert getattr(part(viewer_window, name), attr) not in leggi, (name, attr)


def test_leggi_strumenti_and_zoom(viewer_window):
    w = viewer_window
    bar = _bar(w, "leggi")
    tools, fit = part(w, "tools"), part(w, "zoomfit")
    assert bar.group_title(tools.act_snapshot) == _("Strumenti")
    assert not _compact(w, "leggi", tools.act_snapshot)
    for act in (fit.act_height, fit.act_content, tools.act_zoom, tools.act_lens):
        assert bar.group_title(act) == _("Zoom"), act.text()
    assert _compact(w, "leggi", fit.act_height) and _compact(w, "leggi", fit.act_content)
    assert _compact(w, "leggi", tools.act_lens) and not _compact(w, "leggi", tools.act_zoom)
    # dopo gli adattamenti del nucleo: Adatta all'altezza, al contenuto, Zoom area, Lente
    assert _in_order(_group(w, "leggi", _("Zoom")), w.act_fit_page, fit.act_height, fit.act_content,
                     tools.act_zoom, tools.act_lens)


def test_leggi_vai_and_trova(viewer_window):
    w = viewer_window
    bar = _bar(w, "leggi")
    nav, bm = part(w, "navigation"), part(w, "bookmarks")
    for act in (nav.act_back, nav.act_forward, nav.act_goto):
        assert bar.group_title(act) == _("Vai") and _compact(w, "leggi", act), act.text()
    assert bar.group_title(bm.act_add) == _("Vai")
    assert bm.act_add.iconText() == _("Segnalibro") and bm.act_add.text() == _("Aggiungi segnalibro")
    assert _button(w, "leggi", bm.act_add).text() == _("Segnalibro")
    assert _in_order(_group(w, "leggi", _("Vai")), nav.act_back, nav.act_forward, nav.act_goto, bm.act_add)
    search, adv = part(w, "search"), part(w, "advsearch")
    assert bar.group_title(search.act_find) == _("Trova") and bar.group_title(adv.act_open) == _("Trova")
    assert search.act_find.iconText() == _("Cerca") and adv.act_open.iconText() == _("Avanzata")
    assert search.act_find.text() == _("Cerca nel documento")      # menu e ricerca dei comandi
    assert _in_order(_group(w, "leggi", _("Trova")), search.act_find, adv.act_open)


# ------------------------------------------------------------------ Visualizza
def test_visualizza_layout_ruota_and_mostra(viewer_window):
    w = viewer_window
    bar = _bar(w, "visualizza")
    assert bar.group_title(part(w, "reflow").act) == _("Layout pagina")
    nav = part(w, "navigation")
    assert bar.group_title(nav.act_rotate_ccw) == _("Ruota") and _compact(w, "visualizza", nav.act_rotate_ccw)
    acts = bar.actions()
    assert acts.index(nav.act_rotate_ccw) == acts.index(w.act_rotate_view) + 1
    thumbs, measure = part(w, "thumbnails"), part(w, "measure")
    for act in (thumbs.act_toggle, measure.menu_action, nav.act_detect_urls):
        assert bar.group_title(act) == _("Mostra"), act.text()
    assert thumbs.act_toggle.iconText() == _("Miniature")
    assert measure.menu_action.iconText() == _("Griglia")
    assert _compact(w, "visualizza", nav.act_detect_urls)
    assert _in_order(_group(w, "visualizza", _("Mostra")), w.act_rulers, thumbs.act_toggle, measure.menu_action,
                     nav.act_detect_urls)
    grid = _button(w, "visualizza", measure.menu_action)
    assert grid.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert grid.defaultAction().menu() is measure.menu


def test_visualizza_lettura(viewer_window):
    w = viewer_window
    bar = _bar(w, "visualizza")
    reading, look, speech = part(w, "reading"), part(w, "appearance"), part(w, "speech")
    assert bar.group_title(reading.act_reading) == _("Lettura") and not _compact(w, "visualizza", reading.act_reading)
    assert bar.group_title(look.menu_action) == _("Lettura") and look.menu_action.iconText() == _("Tema")
    theme_button = _button(w, "visualizza", look.menu_action)
    assert theme_button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert theme_button.defaultAction().menu() is look.menu
    scroll = reading.autoscroll_button
    scroll_act = toolbar_action(w.ctx.mode("visualizza"), scroll)
    assert bar.group_title(scroll_act) == _("Lettura")
    assert scroll.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and scroll.toolTip()
    items = [a for a in scroll.menu().actions() if not a.isSeparator()]
    assert items == [reading.act_autoscroll, reading.act_pages, reading.act_loop]
    assert bar.group_title(speech.act_menu) == _("Lettura") and _compact(w, "visualizza", speech.act_menu)
    assert _button(w, "visualizza", speech.act_menu).popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert _in_order(_group(w, "visualizza", _("Lettura")), reading.act_reading, look.menu_action, scroll_act,
                     speech.act_menu)


def test_autoscroll_options_without_document(viewer_window):
    """Le opzioni dello scorrimento automatico si scelgono anche senza documenti aperti."""
    reading = part(viewer_window, "reading")
    assert viewer_window.current_view() is None
    assert reading.autoscroll_button.isEnabled()
    assert not reading.act_autoscroll.isEnabled() and reading.act_pages.isEnabled() and reading.act_loop.isEnabled()


def test_visualizza_presentazione_and_finestra(viewer_window, sample_pdf):
    w = viewer_window
    bar = _bar(w, "visualizza")
    pres = part(w, "presentation")
    for act in (pres.act_start, pres.act_current, pres.act_options):
        assert bar.group_title(act) == _("Presentazione"), act.text()
    assert pres.act_start.iconText() == _("Presenta") and not _compact(w, "visualizza", pres.act_start)
    assert _compact(w, "visualizza", pres.act_current) and _compact(w, "visualizza", pres.act_options)
    assert _in_order(_group(w, "visualizza", _("Presentazione")), pres.act_start, pres.act_current, pres.act_options)
    session = part(w, "session")
    tabs = session.tabs_button
    assert bar.group_title(toolbar_action(w.ctx.mode("visualizza"), tabs)) == _("Finestra")
    assert tabs.text() == _("Schede") and tabs.isEnabled()          # Riapri scheda chiusa anche senza documenti
    items = [a for a in tabs.menu().actions() if not a.isSeparator()]
    assert items == [session.act_next_tab, session.act_prev_tab, session.act_reopen, session.act_close_others,
                     session.act_close_right, session.act_close_all]
    view = w.open_path(sample_pdf)
    w.close_view(view)
    session.act_reopen.setEnabled(False)
    tabs.menu().aboutToShow.emit()          # lo stato si aggiorna all'apertura del menu
    assert session.act_reopen.isEnabled()


# ------------------------------------------------------------------ scheda File
def test_ignore_document_view_in_file_options(viewer_window):
    w = viewer_window
    act = part(w, "session").pref_actions["viewer/ignore_document_view"]
    assert w.backstage.page_of(act) == "opzioni"
    entry = next(e for e in w.backstage.entries("opzioni") if e.action is act)
    assert entry.group == _("Apertura dei documenti") and entry.description
    groups = [e.group for e in w.backstage.entries("opzioni")]
    assert groups.index(_("Impostazioni")) < groups.index(_("Apertura dei documenti"))   # dopo Preferenze…
    w.show_backstage("opzioni")
    tile = next(t for t in w.backstage.tiles if t.action is act)
    assert not act.isChecked()
    tile.click()                                         # voce attivabile: cambia la spunta
    assert act.isChecked() and w.settings.value("viewer/ignore_document_view") in (True, "true")
    w.hide_backstage()


def test_no_viewer_entry_left_in_other_commands(viewer_window):
    """Nessuna voce di questi moduli finisce in File › Altri comandi (ex menu ☰)."""
    w = viewer_window
    leftovers = [a.text() for a in _part_actions(w) if w.backstage.page_of(a) == "altro"]
    assert not leftovers, leftovers


def test_modules_do_not_use_the_old_menu():
    for name in MY_PARTS + ("ribbon_place",):
        tree = ast.parse((VIEWER / f"{name}.py").read_text("utf-8"))
        calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                 and n.attr in ("add_menu_action", "add_separator")]
        assert not calls, f"{name}.py: {calls}"


# ------------------------------------------------------------------ scorciatoie
def test_shortcuts_unchanged(viewer_window):
    w = viewer_window
    parts = {n: part(w, n) for n in MY_PARTS}
    expected = (
        (parts["navigation"].act_back, "Alt+Left"), (parts["navigation"].act_forward, "Alt+Right"),
        (parts["search"].act_find, QKeySequence(QKeySequence.StandardKey.Find).toString()),
        (parts["advsearch"].act_open, "Ctrl+Shift+F"), (parts["bookmarks"].act_add, "Ctrl+B"),
        (parts["thumbnails"].act_toggle, "F4"), (parts["reading"].act_reading, "Ctrl+Shift+L"),
        (parts["presentation"].act_start, "F5"), (parts["presentation"].act_current, "Shift+F5"),
    )
    for act, keys in expected:
        assert act.shortcut().toString() == keys, act.text()


def test_rotate_shortcuts_work_where_the_buttons_are(viewer_window, sample_pdf):
    w = viewer_window
    nav = part(w, "navigation")
    view = w.open_path(sample_pdf)
    w.set_mode("visualizza")
    assert nav.act_rotate_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")
    assert w.act_rotate_view.shortcut() == QKeySequence("Ctrl+Shift++")
    nav.act_rotate_ccw.trigger()
    assert view.view_rotation == 270
    others = [m for m in w.modes if m not in ("leggi", "visualizza")]
    if others:
        w.set_mode(others[0])
        assert nav.act_rotate_ccw.shortcut().isEmpty() and w.act_rotate_view.shortcut().isEmpty()
    w.set_mode("leggi")
    assert nav.act_rotate_ccw.shortcut() == QKeySequence("Ctrl+Shift+-")


def test_menu_buttons_open_their_menu_with_one_click(viewer_window, qtbot):
    """I pulsanti ▾ (Tema, Griglia, Leggi ad alta voce) aprono il menu al primo clic, senza la
    metà del pulsante che non farebbe nulla."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu
    w = viewer_window
    w.set_mode("visualizza")
    look = part(w, "appearance")
    button = _button(w, "visualizza", look.menu_action)
    seen = []

    def grab():
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            seen.append(popup)
            popup.close()
    timer = QTimer()
    timer.timeout.connect(grab)
    timer.start(20)
    try:
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: bool(seen))
    finally:
        timer.stop()
    assert seen[0] is look.menu
