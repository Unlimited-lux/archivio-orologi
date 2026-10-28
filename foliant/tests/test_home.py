"""UX-10 / VIS-02: recenti della Home con file mancanti, filtro per nome, documenti fissati,
menu del recente e «Svuota elenco» con conferma."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from foliant.ui import theme
from foliant.ui.home import MISSING_ROLE, PATH_ROLE, PINNED_ROLE

from .conftest import make_pdf


def _item(home, path):
    return next(home.recent.item(r) for r in range(home.recent.count())
                if home.recent.item(r).data(PATH_ROLE) == path)


def _click(home, path, qtbot):
    QApplication.processEvents()
    lst = home.recent
    lst.scrollToItem(_item(home, path))
    pos = lst.visualItemRect(_item(home, path)).center()
    qtbot.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def _texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


@pytest.fixture
def bare_window(qtbot, settings):
    """Finestra senza moduli: il menu dei recenti è quello del nucleo."""
    from foliant.ui.main_window import MainWindow
    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings, load_modules=False)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    yield win


def test_missing_recent_stays_dimmed_and_click_offers_remove(window, tmp_path, qtbot, monkeypatch):
    a = make_pdf(tmp_path / "a.pdf")
    b = make_pdf(tmp_path / "b.pdf")
    for p in (a, b):
        window.close_view(window.open_path(p))
    os.remove(b)
    window.show_home()
    home = window.home
    assert home.visible_recent() == [b, a]                     # il mancante non sparisce
    item = _item(home, b)
    assert item.data(MISSING_ROLE) and "File non trovato" in item.text()
    assert item.foreground().color() == theme.color("text_muted")
    assert not _item(home, a).data(MISSING_ROLE)

    asked = []
    monkeypatch.setattr(window, "ask_missing_recent", lambda p: (asked.append(p), "remove")[1])
    _click(home, b, qtbot)
    assert asked == [b]                                        # niente «Impossibile aprire il file»
    assert window.current_view() is None
    assert window.recent_files() == [a] and home.visible_recent() == [a]


def test_missing_recent_can_be_located(window, tmp_path, monkeypatch):
    a = make_pdf(tmp_path / "a.pdf")
    window._add_recent(a)
    window.pin_recent(a)
    moved = str(tmp_path / "spostato.pdf")
    os.rename(a, moved)
    monkeypatch.setattr(window, "ask_missing_recent", lambda p: "locate")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [moved])
    view = window.open_recent(a)
    assert view is not None and str(view.document.path) == moved
    assert window.recent_files() == [moved] and window.is_pinned(moved)   # la voce segue il file


def test_missing_recent_question_has_cancel(window, tmp_path, monkeypatch):
    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        next(b for b in box.buttons() if b.text() == "Annulla").click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    gone = str(tmp_path / "sparito.pdf")
    window._add_recent(gone)
    assert window.ask_missing_recent(gone) is None
    assert {"Individua…", "Rimuovi dall'elenco", "Annulla"} <= set(seen["buttons"])
    assert window.recent_files() == [gone]


def test_filter_reduces_the_list_while_typing(window, tmp_path, qtbot):
    home = window.home
    window.show_home()
    assert not home.filter_edit.isVisible()                    # nessun recente: niente filtro
    paths = [str(tmp_path / n) for n in ("Listino autunno.pdf", "contratto.pdf", "listino primavera.pdf")]
    for p in paths:
        make_pdf(p)
        window._add_recent(p)
    assert home.filter_edit.isVisible() and home.filter_edit.placeholderText() == "Filtra per nome"
    seen = []
    home.filter_edit.textChanged.connect(lambda _t: seen.append(len(home.visible_recent())))
    qtbot.keyClicks(home.filter_edit, "lis")
    assert seen == [2, 2, 2]                                   # a ogni carattere, senza maiuscole
    assert set(home.visible_recent()) == {paths[0], paths[2]}
    qtbot.keyClicks(home.filter_edit, "tino a")
    assert home.visible_recent() == [paths[0]]
    assert not home.no_match.isVisible()
    home.filter_edit.setText("zzz")
    assert home.visible_recent() == [] and home.no_match.isVisible()
    window._add_recent(paths[1])                               # il filtro resta applicato
    assert home.visible_recent() == []
    home.filter_edit.clear()
    assert len(home.visible_recent()) == 3 and not home.no_match.isVisible()


def test_pinned_documents_stay_on_top(window, tmp_path):
    ctx, home = window.ctx, window.home
    a, b, c, d = (make_pdf(tmp_path / f"{n}.pdf") for n in "abcd")
    for p in (a, b, c):
        window._add_recent(p)
    assert home.visible_recent() == [c, b, a]
    ctx.pin_recent(a)
    assert ctx.is_pinned(a) and ctx.pinned_recent() == [a]
    assert home.visible_recent() == [a, c, b] and _item(home, a).data(PINNED_ROLE)
    window._add_recent(d)                                      # più recente, ma sotto il fissato
    assert home.visible_recent() == [a, d, c, b]
    for n in range(20):                                        # il limite dei recenti non lo toglie
        window._add_recent(str(tmp_path / f"altro{n}.pdf"))
    assert a in ctx.recent_files() and home.visible_recent()[0] == a
    assert window.settings.value("recentPinned") in ([a], a)   # salvato nelle impostazioni
    other = str(tmp_path / "rinominato.pdf")
    ctx.replace_recent(a, other)
    assert ctx.pinned_recent() == [other] and home.visible_recent()[0] == other
    ctx.pin_recent(other, False)
    assert not ctx.is_pinned(other) and home.visible_recent()[0] != other
    ctx.pin_recent(b)
    ctx.remove_recent(b)
    assert ctx.pinned_recent() == []


def test_core_recent_menu(bare_window, tmp_path, monkeypatch):
    win, ctx = bare_window, bare_window.ctx
    a = make_pdf(tmp_path / "a.pdf")
    gone = str(tmp_path / "sparito.pdf")
    win._add_recent(gone)
    win._add_recent(a)
    ctx.add_recent_menu_provider(lambda path, menu: menu.addAction("Voce del modulo"))
    menu = win.recent_menu(a)
    assert _texts(menu) == ["Apri", "Fissa", "Voce del modulo", "Rimuovi dall'elenco", "Svuota elenco"]
    next(x for x in menu.actions() if x.text() == "Fissa").trigger()
    assert ctx.is_pinned(a)
    assert "Sblocca" in _texts(win.recent_menu(a))
    assert _texts(win.recent_menu(gone))[0] == "Individua…"    # il file non c'è più: niente «Apri»

    shown = []
    monkeypatch.setattr(win, "exec_recent_menu", lambda m, pos: shown.append(_texts(m)))
    win.show_home()
    QApplication.processEvents()
    lst = win.home.recent
    pos = lst.visualItemRect(_item(win.home, a)).center()
    QApplication.sendEvent(lst.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                             lst.viewport().mapToGlobal(pos)))
    assert shown and shown[-1][:2] == ["Apri", "Sblocca"]
    lst.setCurrentItem(_item(win.home, gone))                  # da tastiera (tasto Menu)
    QApplication.sendEvent(lst, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0),
                                                     lst.mapToGlobal(QPoint(0, 0))))
    assert len(shown) == 2 and shown[-1][0] == "Individua…"


def test_module_recent_menu_gets_pin_and_clear(bare_window, tmp_path, monkeypatch):
    """Un modulo che mostra da sé il menu dei recenti (come viewer/files.py) riceve comunque
    Fissa/Sblocca e Svuota elenco dal nucleo, senza voci doppie."""
    win = bare_window
    a = make_pdf(tmp_path / "a.pdf")
    win._add_recent(a)
    lst = win.home.recent
    shown = []
    monkeypatch.setattr(win, "exec_recent_menu", lambda m, pos: shown.append(["menu del nucleo"]))

    def module_menu(pos):
        menu = QMenu(lst)
        menu.addAction("Rinomina…")
        menu.addAction("Rimuovi dall'elenco")
        menu.popup(lst.viewport().mapToGlobal(pos))     # come exec(), senza bloccare il test
        shown.append(_texts(menu))
        menu.close()
    lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    lst.customContextMenuRequested.connect(module_menu)
    win.show_home()
    QApplication.processEvents()
    pos = lst.visualItemRect(_item(win.home, a)).center()
    QApplication.sendEvent(lst.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                             lst.viewport().mapToGlobal(pos)))
    assert len(shown) == 1                                     # solo il menu del modulo
    assert shown[0] == ["Apri", "Fissa", "Rinomina…", "Rimuovi dall'elenco", "Svuota elenco"]


def test_clear_recent_asks_first(window, tmp_path, monkeypatch):
    ctx = window.ctx
    a, b, c = (make_pdf(tmp_path / f"{n}.pdf") for n in "abc")
    for p in (a, b, c):
        window._add_recent(p)
    ctx.pin_recent(b)
    seen = {}

    def click(label):
        def fake_exec(box):
            seen.update(title=box.windowTitle(), default=box.defaultButton().text(), info=box.informativeText())
            next(x for x in box.buttons() if x.text() == label).click()
            return 0
        return fake_exec

    monkeypatch.setattr(QMessageBox, "exec", click("Annulla"))
    menu = window.recent_menu(a)
    next(x for x in menu.actions() if x.text() == "Svuota elenco").trigger()
    assert ctx.recent_files() == [c, b, a]                     # annullato: nulla cambia
    assert seen["title"].endswith("?") and seen["default"] == "Annulla"   # UX-13
    assert "fissati" in seen["info"]
    monkeypatch.setattr(QMessageBox, "exec", click("Svuota elenco"))
    assert ctx.clear_recent()
    assert ctx.recent_files() == [b] and window.home.visible_recent() == [b]   # il fissato resta
    assert os.path.exists(a)                                   # i file non si toccano
    ctx.pin_recent(b, False)
    assert ctx.clear_recent(ask=False) and ctx.recent_files() == []
    assert window.home.empty_recent.isVisibleTo(window.home)


def test_module_submenu_is_not_extended(bare_window, tmp_path, monkeypatch):
    """Solo il menu del modulo riceve le voci del nucleo, non un suo sottomenu figlio dell'elenco."""
    win = bare_window
    a = make_pdf(tmp_path / "a.pdf")
    win._add_recent(a)
    lst = win.home.recent
    shown = []

    def module_menu(pos):
        menu = QMenu(lst)
        sub = QMenu("Invia a", lst)
        sub.addAction("Posta elettronica")
        menu.addMenu(sub)
        menu.popup(lst.viewport().mapToGlobal(pos))
        sub.popup(lst.viewport().mapToGlobal(pos))             # come al passaggio del mouse
        shown.extend([_texts(menu), _texts(sub)])
        sub.close()
        menu.close()
    lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    lst.customContextMenuRequested.connect(module_menu)
    win.show_home()
    QApplication.processEvents()
    pos = lst.visualItemRect(_item(win.home, a)).center()
    QApplication.sendEvent(lst.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                             lst.viewport().mapToGlobal(pos)))
    assert shown[0] == ["Apri", "Fissa", "Invia a", "Rimuovi dall'elenco", "Svuota elenco"]
    assert shown[1] == ["Posta elettronica"]


def test_other_windows_home_follows_recent_changes(settings, tmp_path, qtbot):
    from PySide6.QtCore import QEvent

    from foliant.ui.main_window import MainWindow, app_windows, register_window
    x, y, z = (make_pdf(tmp_path / f"{n}.pdf") for n in "xyz")
    a = register_window(MainWindow(settings, load_modules=False))
    try:
        for p in (x, y, z):
            a._add_recent(p)
        b = register_window(MainWindow(settings, load_modules=False))
        b.show_home()
        assert b.home.visible_recent() == [z, y, x]
        a.pin_recent(x)
        assert b.home.visible_recent() == [x, z, y] and _item(b.home, x).data(PINNED_ROLE)
        a.clear_recent(ask=False)
        assert b.home.visible_recent() == [x]
    finally:
        for w in app_windows():
            w.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)