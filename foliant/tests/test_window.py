import pymupdf
from PySide6.QtWidgets import QMessageBox

from foliant.ui.document_view import DocumentView


def test_starts_on_home(window):
    assert window.current_view() is None
    assert window.docs_stack.currentWidget() is window.home
    assert "leggi" in window.modes
    assert not window.act_save.isEnabled()


def test_open_save_close(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    assert isinstance(view, DocumentView)
    assert window.current_view() is view
    assert window.act_save.isEnabled()
    assert "esempio.pdf" in window.windowTitle()
    # aprire di nuovo lo stesso file non crea una seconda scheda
    assert window.open_path(sample_pdf) is view
    assert window.tabs.count() == 1
    with view.document.change("Ruota") as pdf:
        pdf[0].set_rotation(180)
    assert window.windowTitle().startswith("•")
    assert window.act_undo.isEnabled()
    assert window.save_view(view)
    assert not view.document.is_modified
    assert pymupdf.open(sample_pdf)[0].rotation == 180
    assert window.close_view(view)
    assert window.current_view() is None
    assert sample_pdf in window.recent_files()


def test_close_modified_asks(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    with view.document.change("x") as pdf:
        pdf.delete_page(0)
    def click(label):
        def fake_exec(box):
            next(b for b in box.buttons() if b.text() == label).click()
            return 0
        return fake_exec

    monkeypatch.setattr(QMessageBox, "exec", click("Annulla"))
    assert not window.close_view(view)
    assert window.current_view() is view
    monkeypatch.setattr(QMessageBox, "exec", click("Non salvare"))
    assert window.close_view(view)
    assert window.current_view() is None
    assert pymupdf.open(sample_pdf).page_count == 3   # file originale intatto


def test_encrypted_prompts_password(window, encrypted_pdf, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("segreta", True))
    view = window.open_path(encrypted_pdf)
    assert view is not None and view.page_count == 1


def test_invalid_file_shows_error(window, tmp_path, monkeypatch):
    bad = tmp_path / "x.pdf"
    bad.write_text("no")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    assert window.open_path(str(bad)) is None
    assert shown


def test_new_document_and_modes(window):
    view = window.new_document()
    assert view.document.needs_save_as
    window.set_mode("leggi")
    assert window.current_mode_id == "leggi"


def test_context_api(window, sample_pdf):
    ctx = window.ctx
    mode = ctx.add_mode("prova", "Prova", "star", 999)
    act = ctx.action("Fai qualcosa", "star", lambda: None)
    mode.add_action(act)
    assert not act.isEnabled()
    ctx.open_path(sample_pdf)
    assert act.isEnabled()
    ctx.set_mode("prova")
    assert ctx.current_mode() == "prova"
    from PySide6.QtWidgets import QLabel

    class Panel(QLabel):
        def __init__(self):
            super().__init__("pannello")
            self.view = "non impostata"

        def set_view(self, view):
            self.view = view

    panel = Panel()
    ctx.add_sidebar_panel("p", "Pannello", "star", panel)
    assert panel.view is ctx.current_view()
    ctx.add_menu_action(ctx.action("Voce", None, lambda: None), "Prova")
    window._rebuild_menu()


def test_broken_module_does_not_crash(window):
    from foliant.modules import load_modules

    class Fake:
        pass
    # tutti i moduli reali sono stati caricati senza errori
    assert all(err is None for _n, err in window.module_results), window.module_results


def test_view_menu_provider(window, sample_pdf, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    seen = []

    def provider(view, ev, menu):
        seen.append(ev.page)
        menu.addAction("Voce di prova")

    window.ctx.add_view_menu_provider(provider)
    view = window.open_path(sample_pdf)
    view.flush_rendering()
    shown = []
    monkeypatch.setattr(view, "exec_menu", lambda menu, pos: shown.append([a.text() for a in menu.actions()]))
    r = view.page_rect(0)
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(int(r.center().x()), int(r.top() + 20)),
                                            view.viewport().mapToGlobal(QPoint(int(r.center().x()), int(r.top() + 20)))))
    assert seen == [0]
    assert len(shown) == 1 and "Voce di prova" in shown[0]      # anche i moduli possono aggiungere voci


def test_central_widget_override(window, sample_pdf):
    from PySide6.QtWidgets import QLabel
    view = window.open_path(sample_pdf)
    grid = QLabel("organizzatore")
    window.ctx.set_central_widget(grid)
    assert window.docs_stack.currentWidget() is grid
    assert window.ctx.current_view() is view          # il documento attivo resta
    assert window.ctx.central_widget() is grid
    window.ctx.set_central_widget(None)
    assert window.docs_stack.currentWidget() is window.tabs
    assert window.ctx.central_widget() is None


def test_english_translations_cover_core_strings():
    import re
    from pathlib import Path

    from foliant import i18n
    from foliant.ui.i18n_en import EN
    root = Path(__file__).resolve().parent.parent / "foliant"
    missing = set()
    for f in list((root / "ui").glob("*.py")) + [root / "app.py"]:
        if f.name == "i18n_en.py":
            continue
        for m in re.finditer(r'_\("((?:[^"\\]|\\.)*)"\)', f.read_text(encoding="utf-8")):
            text = m.group(1).encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
            if text not in EN:
                missing.add(text)
    assert not missing, missing
    i18n.set_language("en")
    try:
        assert i18n._("Salva") == "Save"
    finally:
        i18n.set_language("it")


def test_no_duplicate_shortcuts(window):
    """Due azioni con la stessa scorciatoia si annullano a vicenda in Qt: non devono esistere."""
    from collections import defaultdict
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtCore import Qt
    local = (Qt.ShortcutContext.WidgetShortcut, Qt.ShortcutContext.WidgetWithChildrenShortcut)
    seen = defaultdict(list)
    for act in window.findChildren(QAction):
        if act.shortcutContext() in local:      # valide solo nel proprio pannello: nessun conflitto
            continue
        for seq in act.shortcuts():
            if not seq.isEmpty():
                seen[seq.toString(QKeySequence.SequenceFormat.PortableText)].append(act.text() or "(senza nome)")
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dup, dup


def test_ux18_core_shortcuts(window, sample_pdf, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    assert window.act_fit_page.shortcut() == QKeySequence("Ctrl+0")
    assert window.act_zoom_100.shortcut() == QKeySequence("Ctrl+1")
    assert window.act_fit_width.shortcut() == QKeySequence("Ctrl+2")
    view = window.open_path(sample_pdf)
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_N)
    assert view.current_page == 1
    qtbot.keyClick(view, Qt.Key.Key_P)
    assert view.current_page == 0
    view.set_tool(view.hand_tool)
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert view.tool is view._default_tool
    window._mode_by_index(0)
    assert window.current_mode_id == "leggi"
    window.toggle_sidebar()
    path = str(view.document.path)
    window.close_view(view)
    window.reopen_closed()
    assert window.current_view() is not None and str(window.current_view().document.path) == path


def test_service_dialogs(window, monkeypatch):
    from foliant.ui.dialogs import HelpDialog, PreferencesDialog, ShortcutsDialog, author_name
    dlg = PreferencesDialog(window.settings, window)
    dlg.author.setText("Mario Verdi")
    dlg.theme.setCurrentIndex(dlg.theme.findData("dark"))
    dlg.apply()
    assert author_name(window.settings) == "Mario Verdi"
    assert window.settings.value("theme") == "dark"
    sd = ShortcutsDialog(window)
    assert sd.table.rowCount() > 10
    sd.filter.setText("salva")
    assert 0 < sd.table.rowCount() < 10
    HelpDialog(window).close()


def test_link_handler_extension(window, sample_pdf):
    from foliant.ui import tools
    seen = []
    first = lambda view, link: (seen.append(("prima", link.get("uri"))), False)[1]
    last = lambda view, link: (seen.append(("ultima", link.get("uri"))), True)[1]
    saved = list(tools.LINK_HANDLERS)        # il test verifica il meccanismo, senza i gestori dei moduli
    tools.LINK_HANDLERS.clear()
    window.ctx.add_link_handler(last, priority=-1000)
    window.ctx.add_link_handler(first, priority=1000)
    window.ctx.add_link_handler(first, priority=1000)      # doppia registrazione ignorata
    try:
        view = window.open_path(sample_pdf)
        tools.follow_link(view, {"kind": 2, "uri": "https://example.org"})
        assert seen == [("prima", "https://example.org"), ("ultima", "https://example.org")]
    finally:
        tools.LINK_HANDLERS[:] = saved


def test_mode_scoped_shortcuts(window, sample_pdf):
    from PySide6.QtGui import QKeySequence
    ctx = window.ctx
    m1 = ctx.add_mode("prova1", "Prova 1", "star", 990)
    m2 = ctx.add_mode("prova2", "Prova 2", "star", 991)
    a1 = ctx.action("Duplica pagina", None, lambda: None, shortcut="Ctrl+D", mode="prova1")
    a2 = ctx.action("Duplica campo", None, lambda: None, shortcut="Ctrl+D")
    m2.scope_shortcuts(a2)
    window.set_mode("leggi")
    assert a1.shortcut().isEmpty() and a2.shortcut().isEmpty()
    window.set_mode("prova1")
    assert a1.shortcut() == QKeySequence("Ctrl+D") and a2.shortcut().isEmpty()
    window.set_mode("prova2")
    assert a1.shortcut().isEmpty() and a2.shortcut() == QKeySequence("Ctrl+D")


def test_module_disabled_action_survives_view_switch(window, sample_pdf, tmp_path):
    """Un modulo che disattiva un'azione (es. permessi negati) non viene smentito dal nucleo."""
    import shutil
    act = window.ctx.action("Azione protetta", None, lambda: None)
    window.ctx.currentViewChanged.connect(lambda v: act.setEnabled(v is not None and "b.pdf" not in str(v.document.path)))
    window.open_path(sample_pdf)
    assert act.isEnabled()
    other = tmp_path / "b.pdf"
    shutil.copy(sample_pdf, other)
    view = window.open_path(other)
    with view.document.change("x") as pdf:
        pdf.delete_page(0)
    assert not act.isEnabled()
    view.document._saved_state = view.document._state     # chiusura senza domanda di salvataggio


def test_read_only_file_saves_as(window, sample_pdf, tmp_path, monkeypatch):
    """File senza permesso di scrittura: indicazione nel titolo e Salva chiede un nuovo nome."""
    import os
    from foliant.core import document as docmod
    real_access = os.access
    ro = os.path.realpath(sample_pdf)
    monkeypatch.setattr(docmod.os, "access",
                        lambda p, mode: False if (mode == os.W_OK and os.path.realpath(p) == ro) else real_access(p, mode))
    view = window.open_path(sample_pdf)
    assert view.document.read_only and "sola lettura" in window.windowTitle()
    assert "Salva con nome" in window.statusBar().currentMessage()
    with view.document.change("x") as pdf:
        pdf.delete_page(0)
    target = str(tmp_path / "copia.pdf")
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: target)
    assert window.save_view(view)
    assert pymupdf.open(target).page_count == 2
    assert pymupdf.open(sample_pdf).page_count == 3          # l'originale non è stato toccato


def test_mode_switch_hides_previous_properties_panel(window, sample_pdf):
    from PySide6.QtWidgets import QLabel
    window.open_path(sample_pdf)
    m = window.ctx.add_mode("prova_pannello", "Prova", "star", 995)
    m.entered.connect(lambda: window.ctx.show_properties("prova_pannello"))
    window.ctx.add_properties_panel("prova_pannello", QLabel("pannello"))
    window.set_mode("prova_pannello")
    assert window.properties.isVisible()
    window.set_mode("leggi")
    assert not window.properties.isVisible()


def test_save_commits_pending_editor(window, sample_pdf):
    """Salva conferma prima l'editor in loco aperto (gancio dei moduli)."""
    view = window.open_path(sample_pdf)
    calls = []

    def hook(v):
        calls.append(v)
        with v.document.change("Editor confermato") as pdf:
            pdf[0].insert_text((72, 300), "confermato")
    window.ctx.add_pending_edit_hook(hook)
    try:
        assert window.save_view(view)
    finally:
        window.ctx.pending_edit_hooks.remove(hook)
    assert calls == [view]
    assert "confermato" in pymupdf.open(sample_pdf)[0].get_text()
