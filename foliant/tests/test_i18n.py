"""NFR-25: con l'interfaccia in inglese nessun testo del nucleo resta in italiano.

Il controllo statico delle chiamate `_()` (test_window) non vede i testi costruiti fuori da `_()`
né le voci mancanti nei cataloghi usate da codice dinamico: qui si costruiscono davvero finestra,
Home, preferenze, scorciatoie, guida e avvisi in inglese e se ne leggono i testi.
"""

from __future__ import annotations

import re

import pytest

# Parole che esistono solo in italiano (non ambigue in inglese): se compaiono, il testo non è tradotto.
ITALIAN = re.compile(
    r"\b(il|lo|gli|della|delle|dei|degli|nella|nel|con|una|non|pagina|pagine|salva|apri|chiudi|"
    r"annulla|ripeti|documento|documenti|modifica|cerca|nuovo|esci|stampa|preferenze|scorciatoie|"
    r"guida|tutte|tutti|nessun|nessuna|aggiungi|elimina|rimuovi|colore|tema|lingua|autore|cartella|"
    r"modalità|avvisi|altri|recenti|scheda|schede|finestra|chiaro|scuro|sistema|vista|adatta|"
    r"larghezza|continua|singola|affiancate|copertina|righelli|indietro|avanti|successiva|precedente|"
    r"prima|ultima|aumenta|riduci|ruota|apertura|salvataggio|sola|lettura|file recenti)\b", re.I)
ALLOWED = {"Italiano"}


def _texts(root) -> list[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit,
                                   QTabWidget, QTextBrowser, QWidget)
    out = []
    for w in [root] + root.findChildren(QWidget):
        if isinstance(w, (QLabel, QAbstractButton)):
            out.append(w.text())
        if isinstance(w, QLineEdit):
            out.append(w.placeholderText())
        if isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        if isinstance(w, QTextBrowser):
            out.append(w.toPlainText())
        if isinstance(w, QGroupBox):
            out.append(w.title())
        if isinstance(w, QTabWidget):
            out += [w.tabText(i) for i in range(w.count())]
        out += [w.toolTip(), w.accessibleName(), w.windowTitle() if w.isWindow() else ""]
    return out


def _italian(texts) -> list[str]:
    return sorted({t for t in texts if t and t not in ALLOWED and ITALIAN.search(t.replace("&", ""))})


@pytest.fixture
def english():
    from foliant.i18n import language, set_language
    previous = language()
    set_language("en")
    yield
    set_language(previous)


def test_core_interface_has_no_italian_text_in_english(english, qtbot, settings, sample_pdf):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    from foliant.ui.dialogs import HelpDialog, PreferencesDialog, ShortcutsDialog
    from foliant.ui.main_window import MainWindow

    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    texts = []
    for name in dir(win):
        act = getattr(win, name, None) if name.startswith("act_") else None
        if isinstance(act, QAction):
            texts += [act.text(), act.toolTip(), act.statusTip()]
    for act in getattr(win, "view_mode_actions", {}).values():
        texts += [act.text(), act.toolTip()]
    texts += _texts(win.home)
    texts += [win.zoom_combo.itemText(i) for i in range(win.zoom_combo.count())]
    view = win.open_path(sample_pdf)
    texts += [win.windowTitle(), win.zoom_combo.toolTip(), win.page_box.toolTip()]
    win.ctx.show_banner(view, "a", "Test", [("OK", lambda: None)])
    win.ctx.show_banner(view, "b", "Test 2")
    texts += _texts(win.tabs.page(view).banners)
    prefs = PreferencesDialog(win.settings, win)
    texts += _texts(prefs)
    shortcuts = ShortcutsDialog(win)
    texts += [shortcuts.windowTitle()] + [r[2] for r in shortcuts.rows if r[2]]
    header = getattr(shortcuts, "table", None)
    if header is not None:
        texts += [header.horizontalHeaderItem(i).text() for i in range(header.columnCount())]
    texts += _texts(HelpDialog(win))
    found = _italian(texts)
    view.document._saved_state = view.document._state
    assert not found, found


def test_each_module_keeps_its_own_translations(english):
    """La stessa parola italiana tradotta in modo diverso da due moduli: ognuno vede la propria;
    il nucleo vede la sua; l'ordine di registrazione non conta."""
    from foliant import i18n

    def run(module_name: str, code: str):
        scope = {"__name__": module_name, "add_translations": i18n.add_translations,
                 "_": i18n._, "pgettext": i18n.pgettext}
        exec(code, scope)
        return scope

    run("foliant.modules.prova_a.ui", 'add_translations("en", {"Valuta di prova": "Currency"})')
    run("foliant.modules.prova_b.ui", 'add_translations("en", {"Valuta di prova": "Evaluate", "Voce solo del modulo B di prova": "Only B"})')
    a = run("foliant.modules.prova_a.dialogs", 'r = _("Valuta di prova"); s = _("Voce solo del modulo B di prova")')
    b = run("foliant.modules.prova_b.panel", 'r = _("Valuta di prova")')
    assert a["r"] == "Currency" and b["r"] == "Evaluate"
    assert a["s"] == "Only B"                       # voci degli altri moduli come ripiego
    assert i18n._("Valuta di prova") == "Currency"  # fuori dai moduli: il primo registrato
    run("foliant.modules.prova_a.ui", 'add_translations("en", {"Annulla": "Cancel-A"})')
    core = run("foliant.ui.prova", 'r = _("Annulla")')
    assert core["r"] == "Undo"                      # il nucleo non viene sovrascritto dai moduli
    assert run("foliant.modules.prova_a.x", 'r = _("Annulla")')["r"] == "Cancel-A"


def test_core_english_catalog_has_no_duplicate_keys():
    """Una chiave ripetuta nel dizionario EN fa vincere in silenzio l'ultima traduzione."""
    import ast
    from collections import Counter
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "foliant" / "ui" / "i18n_en.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    catalogs = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "EN" for t in node.targets)]
    assert len(catalogs) == 1 and isinstance(catalogs[0], ast.Dict)
    keys = Counter(ast.literal_eval(k) for k in catalogs[0].keys)
    assert not [k for k, n in keys.items() if n > 1]
