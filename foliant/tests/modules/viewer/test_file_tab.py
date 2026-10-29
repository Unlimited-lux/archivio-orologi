"""Voci del viewer nella scheda File (in stile Excel) al posto del vecchio menu ☰: stampa,
informazioni sul documento, metadati, opzioni di apertura, ripristina e ricarica, gestione dei
file, condivisione, archivi ZIP, preferenze, ricerca dei comandi e scorciatoie."""

from __future__ import annotations

import ast
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QKeySequence

from foliant import i18n

from .conftest import part

MODULE = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "viewer"
FILES = ("printing.py", "properties.py", "openoptions.py", "xmp.py", "reload.py", "files.py", "share.py",
         "archives.py", "prefs.py", "commands.py")

# (parte, attributo dell'azione, pagina della scheda File, gruppo)
EXPECTED = (
    ("properties", "action", "informazioni", "Proprietà"),
    ("xmp", "act", "informazioni", "Proprietà"),
    ("openoptions", "act", "informazioni", "Proprietà"),
    ("reload", "act_revert", "informazioni", "Versioni"),
    ("reload", "act_mode", "opzioni", "Apertura dei documenti"),
    ("files", "act_rename", "gestisci", "Questo file"),
    ("files", "act_duplicate", "gestisci", "Questo file"),
    ("files", "act_move", "gestisci", "Questo file"),
    ("files", "act_trash", "gestisci", "Questo file"),
    ("files", "act_reveal", "gestisci", "Questo file"),
    ("files", "act_copy_path", "gestisci", "Questo file"),
    ("files", "act_duplicate_many", "gestisci", "Altri file"),
    ("files", "act_move_many", "gestisci", "Altri file"),
    ("files", "act_trash_many", "gestisci", "Altri file"),
    ("share", "act_email", "condividi", "Invia"),
    ("share", "act_copy", "condividi", "Invia"),
    ("archives", "act_compress", "condividi", "Invia"),
    ("archives", "act_open", "apri", "Archivi ZIP"),
    ("archives", "act_extract", "apri", "Archivi ZIP"),
    ("prefs", "act", "opzioni", "Aspetto"),
    ("prefs", "act_export", "opzioni", "Impostazioni"),
    ("prefs", "act_import", "opzioni", "Impostazioni"),
    ("commands", "act_palette", "guida", "Guida"),
    ("commands", "act_shortcuts", "opzioni", "Impostazioni"),
)


def _entry(window, action):
    page = window.backstage.page_of(action)
    for e in window.backstage.entries(page) if page else ():
        if e.action is action:
            return e
    return None


def _groups(window, page: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in window.backstage.entries(page):
        out.setdefault(e.group, []).append(e.action)
    return out


def test_every_former_menu_entry_has_its_place(viewer_window):
    backstage = viewer_window.backstage
    for name, attr, page, group in EXPECTED:
        act = getattr(part(viewer_window, name), attr)
        assert backstage.page_of(act) == page, (name, attr)
        e = _entry(viewer_window, act)
        assert e is not None and e.group == group, (name, attr, e and e.group)
        assert e.description, (name, attr)                   # riga sotto il nome della voce
    printing = part(viewer_window, "printing").action
    assert backstage.page_of(printing) == "stampa"           # comando della colonna di sinistra
    mine = {getattr(part(viewer_window, n), a) for n, a, _p, _g in EXPECTED} | {printing}
    assert not mine & set(backstage.actions_of("altro"))     # «Altri comandi» resta vuota
    assert not any(e.action.isSeparator() for p in ("informazioni", "apri", "condividi", "gestisci", "opzioni",
                                                    "guida") for e in backstage.entries(p))


def test_requested_descriptions(viewer_window):
    def desc(name, attr):
        return _entry(viewer_window, getattr(part(viewer_window, name), attr)).description
    assert desc("properties", "action") == "Titolo, autore, parole chiave, pagine, versione"
    assert desc("reload", "act_revert") == "Torna all'ultima versione salvata"
    assert desc("commands", "act_palette") == "Trova qualsiasi funzione scrivendone il nome"
    assert desc("commands", "act_shortcuts") == "Cambia i tasti dei comandi"


def test_order_inside_the_pages(viewer_window):
    w = viewer_window
    info = _groups(w, "informazioni")
    assert info["Proprietà"][:3] == [part(w, "properties").action, part(w, "xmp").act, part(w, "openoptions").act]
    assert info["Versioni"] == [part(w, "reload").act_revert]
    apri = list(_groups(w, "apri"))
    assert apri.index("Apri") < apri.index("Archivi ZIP")
    assert _groups(w, "apri")["Archivi ZIP"] == [part(w, "archives").act_open, part(w, "archives").act_extract]
    assert _groups(w, "condividi")["Invia"][:3] == [part(w, "share").act_email, part(w, "share").act_copy,
                                                    part(w, "archives").act_compress]
    opzioni = _groups(w, "opzioni")
    assert list(opzioni)[:2] == ["Impostazioni", "Aspetto"]
    assert opzioni["Impostazioni"][:4] == [w.act_preferences, part(w, "commands").act_shortcuts,
                                           part(w, "prefs").act_export, part(w, "prefs").act_import]
    assert opzioni["Aspetto"][0] is part(w, "prefs").act
    assert part(w, "reload").act_mode in opzioni["Apertura dei documenti"]
    guida = _groups(w, "guida")["Guida"]
    assert guida.index(w.act_help) < guida.index(part(w, "commands").act_palette) < guida.index(w.act_shortcuts)


def test_pages_show_the_entries(viewer_window, sample_pdf):
    w = viewer_window
    w.open_path(sample_pdf)
    for page in ("informazioni", "apri", "condividi", "gestisci", "opzioni", "guida"):
        w.show_backstage(page)
        shown = [t.action for t in w.backstage.tiles]
        expected = [e.action for e in w.backstage.entries(page) if e.action.isVisible()]
        assert shown == expected, page
        for name, attr, p, _g in EXPECTED:
            act = getattr(part(w, name), attr)
            if p == page:
                tile = next(t for t in w.backstage.tiles if t.action is act)
                assert tile.title_label.text() == act.text().replace("&", "")
    w.hide_backstage()


def test_read_bar_without_print_and_properties(viewer_window):
    leggi = viewer_window.modes["leggi"].toolbar.actions()
    assert part(viewer_window, "printing").action not in leggi
    assert part(viewer_window, "properties").action not in leggi


def test_shortcuts_unchanged(viewer_window):
    assert part(viewer_window, "printing").action.shortcut() == QKeySequence("Ctrl+P")
    assert part(viewer_window, "properties").action.shortcut() == QKeySequence("Alt+Return")
    assert part(viewer_window, "commands").act_palette.shortcut() == QKeySequence("Ctrl+K")


def test_reload_mode_entry_opens_its_menu(viewer_window):
    """La voce «Ricarica dei documenti modificati…» apre il menu Automatica/Chiedi/Mai."""
    w = viewer_window
    ctl = part(w, "reload")
    opened = []

    def shown():
        opened.append([a.isChecked() for a in ctl.mode_actions.values()])
        QTimer.singleShot(0, ctl.mode_menu.close)          # il menu aperto con exec() si chiude da sé
    ctl.mode_menu.aboutToShow.connect(shown)
    ctl.set_mode("ask")
    w.show_backstage("opzioni")
    tile = next(t for t in w.backstage.tiles if t.action is ctl.act_mode)
    assert not tile.state_label.pixmap().isNull()          # freccia del sottomenu
    tile.click()
    assert opened == [[False, True, False]] and w.backstage_visible()
    ctl.mode_menu.aboutToShow.disconnect(shown)
    w.hide_backstage()


def test_command_search_button(viewer_window):
    w = viewer_window
    ctl = part(w, "commands")
    button = w.command_button
    assert button.defaultAction() is ctl.act_palette and w._command_slot.isVisible()
    assert button.text() == "Cerca un comando"
    ctl.act_palette.setStatusTip("prova")                 # l'azione cambia: il pulsante tiene il suo testo
    assert button.text() == "Cerca un comando"
    button.click()
    assert ctl.palette is not None and ctl.palette.isVisible()
    ctl.palette.hide()


def test_customize_shortcuts_is_distinct_from_the_core_list(viewer_window):
    w = viewer_window
    ctl = part(w, "commands")
    assert ctl.act_shortcuts.text() == "Personalizza le scorciatoie…"
    assert ctl.act_shortcuts.text() != w.act_shortcuts.text()
    names = [a.text() for a in ctl.search("scorciatoie")]
    assert "Personalizza le scorciatoie…" in names and "Scorciatoie da tastiera" in names
    ctl.open_shortcuts()
    assert ctl.dialog.windowTitle() == "Personalizza le scorciatoie"
    ctl.dialog.hide()


def test_no_deprecated_menu_calls_in_these_parts():
    """Nessuna di queste parti usa più add_menu_action (superata) né la barra Leggi."""
    for name in FILES:
        tree = ast.parse((MODULE / name).read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                assert n.attr not in ("add_menu_action", "main_menu"), f"{name}:{n.lineno}"
            if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "mode" and n.args
                    and isinstance(n.args[0], ast.Constant) and n.args[0].value == "leggi"):
                raise AssertionError(f"{name}:{n.lineno} usa ancora la barra Leggi")


def test_group_names_in_english(qtbot, tmp_path):
    from foliant.ui.main_window import MainWindow
    try:
        i18n.set_language("en")
        win = MainWindow(QSettings(str(tmp_path / "en.ini"), QSettings.Format.IniFormat))
        qtbot.addWidget(win)
        parts = win.ctx.viewer_parts

        def group(name, attr):
            return _entry(win, getattr(parts[name], attr)).group
        assert group("properties", "action") == group("xmp", "act") == group("openoptions", "act") == "Properties"
        assert group("reload", "act_revert") == "Versions"
        assert group("reload", "act_mode") == "Opening documents"
        assert group("files", "act_rename") == "This file" and group("files", "act_trash_many") == "Other files"
        assert group("share", "act_email") == group("archives", "act_compress") == "Send"
        assert group("archives", "act_open") == "ZIP archives"
        assert group("prefs", "act") == "Appearance"
        settings = _groups(win, "opzioni")["Settings"]          # stesso gruppo delle Preferenze del nucleo
        assert win.act_preferences in settings and parts["prefs"].act_export in settings
        assert parts["commands"].act_shortcuts in settings
        assert parts["commands"].act_palette in _groups(win, "guida")["Help"]
        assert _entry(win, parts["commands"].act_palette).description == "Find any feature by typing its name"
        assert parts["commands"].act_shortcuts.text() == "Customize shortcuts…"
    finally:
        i18n.set_language("it")
