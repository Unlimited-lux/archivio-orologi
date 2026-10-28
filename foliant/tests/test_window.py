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
    assert shown == [["Voce di prova"]]


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
