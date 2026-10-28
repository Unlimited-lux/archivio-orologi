"""Servizi della finestra per i moduli: avvisi del documento, avvisi temporanei, controlli prima
delle operazioni, attività non modali, schede e pannelli, preferenze, pagine selezionate,
menu contestuali, recenti e strumento predefinito delle modalità."""

from __future__ import annotations

import shutil
import threading

import pymupdf
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from foliant.api import GUARD_EVENTS, BackgroundTask, OperationCancelled, Tool, selection_from_words
from foliant.ui import theme
from foliant.ui.banners import DocumentPage
from foliant.ui.document_view import DocumentView


def _copy(sample_pdf, tmp_path, name):
    target = tmp_path / name
    shutil.copy(sample_pdf, target)
    return str(target)


# ====================================================================== barre di avviso
def test_banner_sits_above_the_page_one_at_a_time(window, sample_pdf, qtbot):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    page = window.tabs.page(view)
    assert isinstance(page, DocumentPage)
    # compatibilità: le schede restituiscono e accettano ancora la vista
    assert window.tabs.widget(0) is view and window.tabs.currentWidget() is view
    assert window.tabs.indexOf(view) == 0 and window.views() == [view]
    bar = page.banners
    assert bar.isHidden()
    done = []
    ctx.show_banner(view, "ocr", "Documento scansionato senza testo", [("Esegui OCR", lambda: done.append("ocr"))])
    ctx.show_banner(view, "firme", "Firmato: tutte le firme sono valide", [("Mostra firme", lambda: None)],
                    level="warning", priority=10)
    qtbot.waitUntil(lambda: bar.isVisible())
    page.layout().activate()
    assert bar.geometry().bottom() < view.geometry().top()           # sopra la pagina, non sovrapposta
    assert ctx.banners(view) == ["firme", "ocr"]                     # priorità più alta per prima
    assert bar.current().id == "firme" and bar.text_label.text().startswith("Firmato")
    assert bar.more_button.isVisible() and bar.more_button.text() == "1 altro avviso"
    bar.more_button.click()                                          # «N altri avvisi» li fa scorrere
    assert bar.current().id == "ocr"
    bar.action_buttons[0].click()
    assert done == ["ocr"]
    # aggiornare lo stesso avviso non lo duplica
    ctx.show_banner(view, "ocr", "Documento scansionato: 3 pagine senza testo")
    assert ctx.banners(view) == ["firme", "ocr"]
    assert ctx.hide_banner(view, "firme") and not ctx.hide_banner(view, "firme")
    assert ctx.banners(view) == ["ocr"] and not bar.more_button.isVisible()
    ctx.hide_banner(view, "ocr")
    assert bar.isHidden()


def test_banner_level_order_and_close(window, sample_pdf):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    closed = []
    ctx.show_banner(view, "info", "Informazione")
    ctx.show_banner(view, "errore", "Errore di integrità", level="error", on_close=lambda: closed.append(1))
    ctx.show_banner(view, "fisso", "Non chiudibile", level="warning", closable=False)
    assert ctx.banners(view) == ["errore", "fisso", "info"]          # a pari priorità conta il livello
    bar = window.tabs.page(view).banners
    assert bar.close_button.isVisibleTo(bar)
    bar.close_button.click()                                         # × sull'avviso «errore»
    assert closed == [1]
    assert ctx.banners(view) == ["fisso", "info"]
    assert not bar.close_button.isVisibleTo(bar)                     # «fisso» non si chiude
    # un avviso chiuso non ricompare finché il documento resta aperto...
    ctx.show_banner(view, "errore", "Errore di integrità", level="error")
    assert "errore" not in ctx.banners(view)
    # ...e resta come icona nella barra di stato, da cui si può rivedere
    assert window.banner_button.isVisibleTo(window)
    window.fill_banner_menu()
    actions = window.banner_menu.actions()
    assert [a.text() for a in actions] == ["Errore di integrità"]
    actions[0].trigger()
    assert ctx.banners(view)[0] == "errore" and not window.banner_button.isVisibleTo(window)
    # hide_banner azzera la chiusura: il modulo può mostrarlo di nuovo
    bar.dismiss("errore")
    ctx.hide_banner(view, "errore")
    ctx.show_banner(view, "errore", "Errore di integrità", level="error")
    assert ctx.banners(view)[0] == "errore"


def test_banners_follow_the_tab_and_the_theme(window, sample_pdf, tmp_path):
    ctx = window.ctx
    v1 = window.open_path(sample_pdf)
    v2 = window.open_path(_copy(sample_pdf, tmp_path, "b.pdf"))
    ctx.show_banner(v1, "a", "Solo nel primo documento")
    assert ctx.banners(v1) == ["a"] and ctx.banners(v2) == []
    assert window.current_view() is v2
    assert not window.tabs.page(v1).banners.isVisible()             # scheda non attiva
    assert window.tabs.page(v2).banners.isHidden()
    window.tabs.setCurrentWidget(v1)
    assert window.current_view() is v1 and window.tabs.page(v1).banners.isVisible()
    bar = window.tabs.page(v1).banners
    app = QApplication.instance()
    light = bar.background_color().name()
    theme.apply(app, "dark")
    try:
        assert bar.background_color().name() != light               # colori ricalcolati dal tema
        bar.grab()                                                   # disegno nel tema scuro senza errori
    finally:
        theme.apply(app, "light")
    assert bar.background_color().name() == light
    # una vista non in una scheda, o None, non dà errori
    assert ctx.show_banner(None, "x", "niente") is None and ctx.banners(None) == []


def test_banner_action_error_does_not_crash(window, sample_pdf):
    view = window.open_path(sample_pdf)

    def broken():
        raise RuntimeError("guasto")
    window.ctx.show_banner(view, "rotto", "Avviso", [("Prova", broken)])
    window.tabs.page(view).banners.action_buttons[0].click()
    assert "guasto" in window.statusBar().currentMessage()


# ====================================================================== avvisi temporanei
def test_notify_is_not_modal(window, recwarn, qtbot):
    ctx = window.ctx
    undone = []
    n = ctx.notify("3 pagine eliminate", [("Annulla", lambda: undone.append(1))], duration_ms=0)
    assert n.is_open and n.text == "3 pagine eliminate"
    assert window.notifications.isVisible()
    area = window.notifications.geometry()
    host = window.centralWidget().rect()
    assert area.bottom() <= host.bottom() and area.center().x() == pytest.approx(host.center().x(), abs=2)
    n.buttons[0].click()                                             # l'azione chiude l'avviso
    assert undone == [1] and not n.is_open
    short = ctx.notify("Salvato", duration_ms=50)
    qtbot.waitUntil(lambda: not short.is_open, timeout=3000)
    for i in range(6):
        ctx.notify(f"Avviso {i}", duration_ms=0)
    assert len(window.notifications.notifications()) == window.notifications.MAX_VISIBLE
    assert not [w for w in recwarn if "finestra modale" in str(w.message)]


def test_stacked_notifications_keep_their_height(window, sample_pdf):
    """Un secondo avviso non schiaccia il primo: la pila cresce e ogni pulsante resta leggibile."""
    from PySide6.QtWidgets import QAbstractButton, QApplication
    window.resize(1280, 800)
    window.open_path(sample_pdf)
    ctx = window.ctx
    first = ctx.notify("Documento salvato in /home/utente/Documenti/Orologi/Listino autunno 2026 con un nome "
                       "di file piuttosto lungo.pdf", [("Apri cartella", lambda: None)], duration_ms=0)
    QApplication.processEvents()
    alone = first.height()
    second = ctx.notify("Seconda notifica", duration_ms=0)
    QApplication.processEvents()
    assert first.height() == alone and second.height() >= second.minimumSizeHint().height()
    for n in (first, second):
        for b in n.findChildren(QAbstractButton):
            assert b.height() >= b.minimumSizeHint().height()
    area = window.notifications
    assert area.height() >= first.height() + second.height()


def test_toolbar_extension_button_follows_theme(qapp):
    """Il pulsante «»» delle barre usa un'icona del tema (non il grigio fisso di Fusion) e
    il suo spazio è quello dell'icona."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QStyle
    from foliant.ui import theme
    for mode in ("dark", "light"):
        theme.apply(qapp, mode)
        style = qapp.style()
        assert style.pixelMetric(QStyle.PixelMetric.PM_ToolBarExtensionExtent) == 20
        img = style.standardIcon(QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton).pixmap(16, 16).toImage()
        ink = [QColor(img.pixel(x, y)) for x in range(img.width()) for y in range(img.height())
               if QColor.fromRgba(img.pixel(x, y)).alpha() > 200]
        assert ink, mode
        text = theme.color("text")
        assert min(abs(c.lightness() - text.lightness()) for c in ink) < 40, mode   # colore del testo del tema
    theme.apply(qapp, "light")


# ====================================================================== controlli
def test_save_guards(window, sample_pdf):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    with view.document.change("x") as pdf:
        pdf.delete_page(0)
    seen = []

    def refuse(v, info):
        seen.append((v, dict(info)))
        return False
    ctx.add_guard("save", refuse)
    ctx.add_guard("save", refuse)                                    # doppia registrazione ignorata
    try:
        assert not window.save_view(view)
        assert len(seen) == 1 and seen[0][0] is view
        assert seen[0][1]["event"] == "save" and seen[0][1]["path"] == str(view.document.path)
        assert pymupdf.open(sample_pdf).page_count == 3              # nulla scritto
    finally:
        ctx.remove_guard("save", refuse)

    def apply_before_saving(v, info):                                # es. «Applica ora» le redazioni
        with v.document.change("Applica") as pdf:
            pdf[0].insert_text((72, 300), "APPLICATO")
        return True
    ctx.add_guard("save", apply_before_saving)
    try:
        assert window.save_view(view)
    finally:
        ctx.remove_guard("save", apply_before_saving)
    saved = pymupdf.open(sample_pdf)
    assert saved.page_count == 2 and "APPLICATO" in saved[0].get_text()


def test_save_as_and_copy_guards(window, sample_pdf, tmp_path, monkeypatch):
    ctx = window.ctx
    events = []
    guard = lambda v, info: (events.append((info["event"], info.get("copy"))), True)[1]
    ctx.add_guard("save_as", guard)
    try:
        target = str(tmp_path / "nuovo.pdf")
        monkeypatch.setattr(ctx, "ask_save_path", lambda *a, **k: target)
        new = window.new_document()                                  # Ctrl+S su un documento nuovo = Salva con nome
        assert window.save_view(new)
        view = window.open_path(sample_pdf)
        target = str(tmp_path / "copia.pdf")
        assert window.save_copy(view)
        assert events == [("save_as", False), ("save_as", True)]
    finally:
        ctx.remove_guard("save_as", guard)
    with pytest.raises(ValueError):
        ctx.add_guard("salva", guard)
    assert set(GUARD_EVENTS) == {"save", "save_as", "print", "close_document", "quit"}


def test_close_and_print_guards(window, sample_pdf, recwarn):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    asked = []

    def busy(v, info):                                               # es. AI-17: operazioni in corso
        asked.append(info["event"])
        return False
    ctx.add_guard("close_document", busy)
    ctx.add_guard("print", busy)
    try:
        assert not window.close_view(view)
        assert window.current_view() is view
        assert not ctx.run_guards("print", view, {"pages": [0]})
        assert asked == ["close_document", "print"]
        with view.document.change("x") as pdf:
            pdf.delete_page(0)
        assert ctx.close_view(view, discard=True)                    # chiusura forzata: niente domande
        assert window.views() == [] and asked == ["close_document", "print"]
    finally:
        ctx.remove_guard("close_document", busy)
        ctx.remove_guard("print", busy)
    assert not [w for w in recwarn if "finestra modale" in str(w.message)]


def test_quit_guard_and_broken_guard(window, sample_pdf):
    from PySide6.QtGui import QCloseEvent
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    seen = []
    stop = lambda v, info: (seen.append((v, list(info["views"]))), False)[1]
    ctx.add_guard("quit", stop)
    try:
        ev = QCloseEvent()
        window.closeEvent(ev)
        assert not ev.isAccepted() and seen == [(None, [view])]
        assert window.views() == [view]
    finally:
        ctx.remove_guard("quit", stop)

    def broken(v, info):
        raise RuntimeError("guasto")
    ctx.add_guard("print", broken)
    try:
        assert ctx.run_guards("print", view)                         # un controllo difettoso non blocca
        assert "guasto" in window.statusBar().currentMessage()
    finally:
        ctx.remove_guard("print", broken)


# ====================================================================== attività non modali
def test_run_task_non_modal_sync_mode(window):
    ctx = window.ctx
    results, errors = [], []
    task = ctx.run_task("Somma", lambda progress, a, b: a + b, 2, 3, modal=False,
                        on_done=results.append, on_error=errors.append)
    assert isinstance(task, BackgroundTask) and task.done and task.result == 5
    assert results == [5] and errors == []

    def fail(progress):
        raise ValueError("rotto")
    task = ctx.run_task("Errore", fail, modal=False, on_error=errors.append)
    assert isinstance(errors[0], ValueError) and task.error is errors[0]
    ctx.run_task("Senza gestore", fail, modal=False)                 # l'errore diventa un avviso
    assert any("rotto" in n.text for n in window.notifications.notifications())
    # comportamento modale predefinito invariato
    assert ctx.run_task("Modale", lambda progress: 42) == 42
    with pytest.raises(ValueError):
        ctx.run_task("Modale", fail)
    got = []
    assert ctx.run_task("Modale", lambda progress: 7, on_done=got.append) == 7 and got == [7]


def test_run_task_non_modal_runs_in_background(window, sample_pdf, monkeypatch, qtbot):
    monkeypatch.setenv("FOLIANT_SYNC_TASKS", "0")
    ctx = window.ctx
    release = threading.Event()
    started = threading.Event()

    def work(progress, n):
        started.set()
        while not release.is_set():
            progress.update(0.5, "Pagina 1 di 2")
            release.wait(0.02)
        progress.update(1.0)
        return n * 2

    done, errors = [], []
    first = ctx.run_task("Confronto", work, 21, modal=False, on_done=done.append, on_error=errors.append)
    second = ctx.run_task("Elaborazione in blocco", work, 1, modal=False, on_done=done.append,
                          on_error=errors.append)
    try:
        assert first.running and second.running and done == []     # restituisce subito
        assert ctx.background_tasks() == [first, second]
        started.wait(5)
        qtbot.waitUntil(lambda: window.task_status.isVisible(), timeout=3000)
        status = window.task_status
        qtbot.waitUntil(lambda: "Confronto" in status.label.toolTip(), timeout=3000)
        assert status.more_button.isVisible() and status.more_button.text() == "+1"
        status.fill_menu()
        assert len(status.menu.actions()) == 2
        # la finestra resta utilizzabile mentre le attività lavorano
        view = window.open_path(sample_pdf)
        assert window.current_view() is view
        status.cancel_current()                                      # Annulla della prima attività
        assert first.wait(5000) and isinstance(errors[0], OperationCancelled)
        release.set()
        assert second.wait(5000)
        assert done == [2] and not window.task_status.isVisible()
    finally:
        release.set()
        first.wait(5000)
        second.wait(5000)


def test_quit_with_running_task_asks(window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    monkeypatch.setenv("FOLIANT_SYNC_TASKS", "0")
    release = threading.Event()
    task = window.ctx.run_task("Esportazione", lambda progress: release.wait(5), modal=False)
    asked = []

    def wait_button(box):
        asked.append(box.text())
        next(b for b in box.buttons() if b.text() == "Attendi").click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", wait_button)
    try:
        ev = QCloseEvent()
        window.closeEvent(ev)
        assert not ev.isAccepted() and "Esportazione" in asked[0]
    finally:
        release.set()
        task.wait(5000)


def test_task_callbacks_survive_nested_event_loops(window, monkeypatch):
    """Un `on_done` che apre un dialogo modale (ciclo di eventi annidato) mentre altre attività
    sono finite nello stesso giro: ognuna conclusa una sola volta, senza errori."""
    monkeypatch.setenv("FOLIANT_SYNC_TASKS", "0")
    ctx, manager = window.ctx, window.task_manager
    gate = threading.Event()
    calls = []

    def on_first(result):
        calls.append(("a", result))
        manager.poll()                                               # come il ciclo di un dialogo modale
    a = ctx.run_task("A", lambda p: gate.wait(5) and 1, modal=False, on_done=on_first)
    b = ctx.run_task("B", lambda p: gate.wait(5) and 2, modal=False, on_done=lambda r: calls.append(("b", r)))
    gate.set()
    a._thread.join(5)
    b._thread.join(5)
    manager.poll()                                                   # entrambe finite nello stesso giro
    assert calls == [("a", 1), ("b", 2)] and a.done and b.done and manager.running() == []


def test_quit_stops_tasks_only_when_quit_is_confirmed(window, sample_pdf, monkeypatch):
    """«Interrompi ed esci» seguito da un annullamento (controllo `quit` o domanda sulle
    modifiche) non deve aver già interrotto le attività; a uscita confermata si attende che
    smettano di lavorare prima di chiudere i documenti."""
    import time
    from PySide6.QtGui import QCloseEvent
    monkeypatch.setenv("FOLIANT_SYNC_TASKS", "0")
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    seen_closed = []

    def work(progress):
        while True:
            progress.update(0.5)                                     # OperationCancelled se annullata
            seen_closed.append(view.document.closed)
            time.sleep(0.01)

    errors = []
    task = ctx.run_task("Esportazione", work, modal=False, on_error=errors.append)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: next(
        b for b in box.buttons() if b.text() == "Interrompi ed esci").click() or 0)
    refuse = lambda v, info: False
    ctx.add_guard("quit", refuse)
    try:
        ev = QCloseEvent()
        window.closeEvent(ev)
        assert not ev.isAccepted() and window.views() == [view]
        assert task.running and not task.cancelled                  # l'uscita è stata annullata
    finally:
        ctx.remove_guard("quit", refuse)
    try:
        ev = QCloseEvent()
        window.closeEvent(ev)
        assert ev.isAccepted() and window.views() == []
        assert task.done and isinstance(errors[0], OperationCancelled)
        assert not any(seen_closed)                                  # mai al lavoro su un documento chiuso
    finally:
        task.cancel()
        task.wait(5000)


# ====================================================================== schede e pannelli
def test_services_ignore_closed_views(window, sample_pdf, qtbot):
    """Callback in ritardo (es. fine di un'attività in background) su una scheda già chiusa."""
    import shiboken6
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    qtbot.wait(50)                                                   # sistemazione della vista appena aperta
    assert ctx.close_view(view, discard=True)
    assert ctx.show_banner(view, "tardi", "Analisi completata") is None   # documento chiuso
    qtbot.waitUntil(lambda: not shiboken6.isValid(view), timeout=3000)  # vista ormai distrutta
    assert ctx.show_banner(view, "tardi", "Analisi completata") is None
    assert ctx.banners(view) == [] and not ctx.hide_banner(view, "tardi")
    assert not ctx.activate_view(view) and window.tabs.indexOf(view) == -1
    n = ctx.notify("Fatto", [("Apri", lambda: None, True)], duration_ms=0)   # azioni come negli avvisi
    assert [b.text() for b in n.buttons] == ["Apri"]
    n.close_notification()


def test_activate_view_close_view_and_refresh_state(window, sample_pdf, tmp_path):
    ctx = window.ctx
    v1 = window.open_path(sample_pdf)
    v2 = window.open_path(_copy(sample_pdf, tmp_path, "b.pdf"))
    window.show_home()
    assert ctx.current_view() is None
    assert ctx.activate_view(v1)
    assert window.docs_stack.currentWidget() is window.tabs and ctx.current_view() is v1
    ctx.set_central_widget(QLabel("organizzatore"))
    assert ctx.activate_view(v2) and ctx.current_view() is v2
    assert window.docs_stack.currentWidget() is window.tabs
    # navigazione: niente «precedente» sulla prima pagina, niente «successiva» sull'ultima
    v2.go_to_page(0)
    ctx.refresh_state()
    assert not window.act_prev.isEnabled() and window.act_next.isEnabled()
    v2.go_to_page(v2.page_count - 1)
    assert window.act_prev.isEnabled() and not window.act_next.isEnabled() and not window.act_last.isEnabled()
    with v2.document.change("x") as pdf:
        pdf.delete_page(0)
    assert ctx.close_view(v2, discard=True)                          # nessuna domanda di salvataggio
    assert window.views() == [v1]
    assert not ctx.activate_view(v2)


def test_toggle_sidebar_panel_and_fullscreen_signal(window, sample_pdf, qtbot):
    ctx = window.ctx
    window.open_path(sample_pdf)
    a, b = QLabel("a"), QLabel("b")
    ctx.add_sidebar_panel("prova_a", "A", "star", a, order=900)
    ctx.add_sidebar_panel("prova_b", "B", "star", b, order=901)
    ctx.toggle_sidebar_panel("prova_b")
    assert window.sidebar.isVisible() and window.sidebar_stack.currentWidget() is b
    ctx.toggle_sidebar_panel("prova_b")
    assert not window.sidebar_stack.isVisible()
    ctx.toggle_sidebar_panel("prova_a")
    assert window.sidebar_stack.isVisible() and window.sidebar_stack.currentWidget() is a
    ctx.toggle_sidebar_panel("inesistente")                          # nessun errore
    states = []
    ctx.fullscreenChanged.connect(states.append)
    window.toggle_fullscreen()
    qtbot.waitUntil(lambda: states == [True], timeout=3000)
    window.toggle_fullscreen()
    qtbot.waitUntil(lambda: states == [True, False], timeout=3000)


# ====================================================================== preferenze
def test_preferences_pages_and_author_name(window):
    from foliant.ui.dialogs import PreferencesDialog
    ctx = window.ctx
    applied = []

    class Page(QWidget):
        def apply(self):
            applied.append(self)

    created = []
    ctx.add_preferences_page("Assistente IA", lambda parent: created.append(Page(parent)) or created[-1])
    ctx.add_preferences_page("Rotta", lambda: 1 / 0, order=200)
    dlg = PreferencesDialog(window.settings, window, pages=window.preference_pages())
    assert [dlg.sections.item(i).text() for i in range(dlg.sections.count())] == ["Generale", "Assistente IA", "Rotta"]
    assert dlg.show_page("Assistente IA") and dlg.stack.currentWidget() is created[0]
    assert isinstance(dlg.module_pages[1], QLabel)                   # pagina difettosa: messaggio, non errore
    dlg.author.setText("Anna Bianchi")
    dlg.apply()
    assert applied == [created[0]]
    assert ctx.author_name() == "Anna Bianchi"
    dlg.close()
    # senza pagine dei moduli la finestra resta quella semplice
    plain = PreferencesDialog(window.settings, window)
    assert plain.sections is None
    plain.close()


# ====================================================================== pagine selezionate
def test_selected_pages_provider(window, sample_pdf):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    view.go_to_page(1)
    try:
        assert ctx.selected_pages(view) == [1]                       # senza provider: la pagina corrente
        ctx.set_selected_pages_provider(lambda v: [2, 0, 2, 9])
        assert ctx.selected_pages() == [0, 2]                        # ordinate, senza doppioni e fuori intervallo
        ctx.set_selected_pages_provider(lambda v: [])
        assert ctx.selected_pages(view) == [1]
        ctx.set_selected_pages_provider(lambda v: 1 / 0)
        assert ctx.selected_pages(view) == [1]
    finally:
        ctx.set_selected_pages_provider(None)
    window.show_home()
    assert ctx.selected_pages() == []


# ====================================================================== menu contestuali
def test_tab_menu_provider(window, sample_pdf, monkeypatch):
    ctx = window.ctx
    view = window.open_path(sample_pdf)
    seen = []

    def provider(v, menu):
        seen.append(v)
        menu.addAction("Voce di prova del test")
    ctx.add_tab_menu_provider(provider)
    ctx.add_tab_menu_provider(provider)                              # una sola volta
    try:
        menu = window.tab_menu(window.tabs.indexOf(view))
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "Chiudi scheda" in texts and texts.count("Voce di prova del test") == 1 and seen == [view]
        shown = []
        monkeypatch.setattr(window, "exec_tab_menu", lambda m, pos: shown.append([a.text() for a in m.actions()]))
        from PySide6.QtGui import QContextMenuEvent
        bar = window.tabs.tabBar()
        pos = bar.tabRect(0).center()
        bar.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, bar.mapToGlobal(pos)))
        assert shown and "Voce di prova del test" in shown[0]
        assert window.tab_menu(5) is None
    finally:
        window.tab_menu_providers.remove(provider)


def test_view_menu_providers_see_the_text_selection(window, sample_pdf, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent
    view = window.open_path(sample_pdf)
    view.flush_rendering()
    words = view.words(0)[:2]
    view.set_text_selection(selection_from_words(0, words))
    seen = []
    provider = lambda v, ev, menu: seen.append((v.text_selection.page if v.text_selection else None,
                                                 v.selected_text()))
    window.ctx.add_view_menu_provider(provider)
    try:
        monkeypatch.setattr(view, "exec_menu", lambda menu, pos: None)
        r = view.page_rect(0)
        p = QPoint(int(r.center().x()), int(r.top() + 20))
        view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, p, view.viewport().mapToGlobal(p)))
    finally:
        window.view_menu_providers.remove(provider)
    assert seen == [(0, " ".join(w[4] for w in words))]


# ====================================================================== recenti
def test_replace_and_remove_recent(window, tmp_path):
    ctx = window.ctx
    a, b, c = (str(tmp_path / n) for n in ("a.pdf", "b.pdf", "c.pdf"))
    window._add_recent(a)
    window._add_recent(b)
    assert ctx.recent_files() == [b, a]
    ctx.replace_recent(a, c)                                         # rinominato: stessa posizione
    assert ctx.recent_files() == [b, c]
    ctx.replace_recent(b, c)                                         # niente doppioni
    assert ctx.recent_files() == [c]
    ctx.replace_recent(str(tmp_path / "x.pdf"), a)                   # assente: in cima
    assert ctx.recent_files() == [a, c]
    ctx.remove_recent(c)
    assert ctx.recent_files() == [a]


# ====================================================================== strumento predefinito
class _EditTool(Tool):
    id = "prova-modifica"


def test_mode_default_tool(window, sample_pdf, tmp_path):
    ctx = window.ctx
    mode = ctx.add_mode("prova_strumento", "Prova", "star", 997)
    made = []

    def factory(view):
        made.append(view)
        return _EditTool()
    mode.set_default_tool(factory)
    seen_on_enter = []
    mode.entered.connect(lambda: seen_on_enter.append(ctx.current_view().tool))
    v1 = window.open_path(sample_pdf)
    assert v1.tool is v1._default_tool                               # altra modalità: nessun effetto
    ctx.set_mode("prova_strumento")
    assert isinstance(v1.tool, _EditTool) and made == [v1]
    assert isinstance(seen_on_enter[0], _EditTool)                   # i moduli lo vedono già entrando
    v2 = window.open_path(_copy(sample_pdf, tmp_path, "b.pdf"))      # aperto mentre la modalità è attiva
    assert isinstance(v2.tool, _EditTool) and made == [v1, v2]
    v2.set_tool(v2.hand_tool)
    mode.set_default_tool(lambda: _EditTool())                       # anche senza argomento
    assert v2.tool is v2.hand_tool                                   # lo strumento scelto resta
    ctx.set_mode("leggi")
    assert v1.tool is v1._default_tool and v2.tool is v2._default_tool
    shared = _EditTool()
    mode.set_default_tool(shared)                                    # un Tool già pronto
    ctx.set_mode("prova_strumento")
    assert v1.tool is shared or v2.tool is shared
    mode.set_default_tool(None)
    ctx.set_mode("leggi")
    ctx.set_mode("prova_strumento")
    assert v1.tool is v1._default_tool


def test_views_stay_document_views_in_tabs(window, sample_pdf):
    view = window.open_path(sample_pdf)
    assert isinstance(window.tabs.widget(0), DocumentView)
    assert isinstance(view.parentWidget(), DocumentPage)
    window.tabs.setCurrentWidget(view)
    assert window.ctx.close_view(view)
    assert window.tabs.count() == 0 and window.current_view() is None
