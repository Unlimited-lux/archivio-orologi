"""VIS-24 — Ricerca nel documento."""

from __future__ import annotations

import time

import pymupdf
from PySide6.QtCore import Qt


def _ctl(window):
    return window.ctx.viewer_parts["search"]


def _pdf(tmp_path, pages, name="cerca.pdf"):
    path = tmp_path / name
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 100
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 16
    doc.save(str(path))
    return str(path)


def test_case_and_count(viewer_window, tmp_path):
    path = _pdf(tmp_path, [["Contratto A", "contratto b"], ["Contratto C Contratto D"], ["altro contratto"]])
    viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    assert ctl.search("contratto") == 5
    assert ctl.bar.counter.text() == "1 di 5"
    assert ctl.search("contratto", case_sensitive=True) == 2
    assert ctl.search("contratto", case_sensitive=False) == 5


def test_whole_word(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["Bisogna ricontrattare"]]))
    ctl = _ctl(viewer_window)
    assert ctl.search("contratt", whole_word=True) == 0
    assert ctl.search("contratt", whole_word=False) == 1


def test_hyphenated_word_highlighted_on_both_lines(viewer_window, tmp_path):
    view = viewer_window.open_path(_pdf(tmp_path, [["una infor-", "mazione utile"]]))
    ctl = _ctl(viewer_window)
    assert ctl.search("informazione") == 1
    assert len(view.search_hits[0]) == 2


def test_navigation_next_previous_wraps(viewer_window, tmp_path, qtbot):
    path = _pdf(tmp_path, [["Rolex"], ["niente"], ["Rolex e Rolex"]])
    view = viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.search("rolex")
    assert ctl.current == 0 and view.current_hit == (0, 0)
    ctl.act_next.trigger()      # F3
    assert ctl.current == 1 and view.current_page == 2
    ctl.next()
    assert ctl.current == 2 and view.current_hit == (2, 1)
    ctl.next()
    assert ctl.current == 0     # ripresa dall'inizio
    ctl.act_prev.trigger()      # Maiusc+F3
    assert ctl.current == 2
    assert ctl.bar.counter.text() == "3 di 3"
    assert ctl.act_next.shortcut().toString() == "F3"
    assert ctl.act_prev.shortcut().toString() == "Shift+F3"


def test_starts_from_current_page(viewer_window, long_pdf):
    view = viewer_window.open_path(long_pdf)
    view.go_to_page(20)
    ctl = _ctl(viewer_window)
    ctl.search("Contenuto numero")
    assert len(ctl.hits) == 30
    assert ctl.hits[ctl.current].page == 20


def test_enter_and_escape_in_field(viewer_window, tmp_path, qtbot):
    view = viewer_window.open_path(_pdf(tmp_path, [["uno due uno"]]))
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.bar.isVisible()
    qtbot.keyClicks(ctl.bar.field, "uno")
    qtbot.keyClick(ctl.bar.field, Qt.Key.Key_Return)
    ctl.finish()
    assert len(ctl.hits) == 2
    qtbot.keyClick(ctl.bar.field, Qt.Key.Key_Return)
    assert ctl.current == 1
    qtbot.keyClick(ctl.bar.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert ctl.current == 0
    qtbot.keyClick(ctl.bar.field, Qt.Key.Key_Escape)
    assert not ctl.bar.isVisible()
    assert view.search_hits == {}
    assert "uno" in ctl.history()
    # cronologia solo in memoria (riservatezza): nulla nelle impostazioni
    viewer_window.settings.sync()
    assert not viewer_window.settings.contains("viewer/search_history")
    assert not any("uno" in str(viewer_window.settings.value(k)) for k in viewer_window.settings.allKeys())


def test_prefill_with_selection(viewer_window, tmp_path):
    from foliant.api import selection_from_words
    view = viewer_window.open_path(_pdf(tmp_path, [["parola speciale"]]))
    words = view.words(0)
    view.set_text_selection(selection_from_words(0, [w for w in words if w[4] == "speciale"]))
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.bar.field.text() == "speciale"


def test_results_panel_grouped_and_click(viewer_window, tmp_path, qtbot):
    path = _pdf(tmp_path, [["il Rolex Daytona"], ["vuota"], ["un Rolex Submariner", "e un Rolex GMT"]])
    view = viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.search("rolex")
    tree = ctl.panel.tree
    assert tree.topLevelItemCount() == 2
    assert tree.topLevelItem(1).childCount() == 2
    assert "<b>Rolex</b>" in tree.topLevelItem(0).child(0).text(0)
    child = tree.topLevelItem(1).child(1)
    viewer_window.ctx.show_sidebar_panel("search")
    rect = tree.visualItemRect(child)
    view.go_to_page(0)
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert ctl.current == 2
    assert view.current_page == 2
    assert view.current_hit == (2, 1)
    nav = viewer_window.ctx.viewer_parts["navigation"]
    assert nav.state(view).history.can_back


def test_no_results_no_modal_and_error_state(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["testo"]]))
    ctl = _ctl(viewer_window)
    assert ctl.search("inesistente") == 0
    assert ctl.bar.counter.text() == "Nessun risultato"
    assert "border-color" in ctl.bar.field.styleSheet()


def test_pages_without_text_reported(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["testo"], [], []]))
    ctl = _ctl(viewer_window)
    ctl.search("testo")
    assert not ctl.panel.note.isHidden()
    assert "2 pagine senza testo" in ctl.panel.note.text()


def test_search_refreshes_after_modification(viewer_window, tmp_path):
    view = viewer_window.open_path(_pdf(tmp_path, [["Rolex"], ["Rolex"]]))
    ctl = _ctl(viewer_window)
    ctl.search("rolex")
    with view.document.change("Elimina") as pdf:
        pdf.delete_page(1)
    ctl.finish()
    assert len(ctl.hits) == 1


def test_many_changes_restart_search_once(viewer_window, tmp_path, monkeypatch):
    """Prestazioni: più modifiche nello stesso giro di eventi → una sola nuova ricerca."""
    from PySide6.QtWidgets import QApplication
    view = viewer_window.open_path(_pdf(tmp_path, [["Rolex"], ["Rolex"]]))
    ctl = _ctl(viewer_window)
    ctl.search("rolex")
    ctl.finish()
    calls = []
    real = ctl.restart
    monkeypatch.setattr(ctl, "restart", lambda: calls.append(1) or real())
    for i in range(3):
        with view.document.change("Testo") as pdf:
            pdf[0].insert_text((72, 300 + 10 * i), "x")
    assert calls == []
    QApplication.processEvents()
    assert len(calls) == 1
    ctl.finish()
    assert len(ctl.hits) == 2
    view.document._saved_state = view.document._state


def test_incremental_search_keeps_ui_responsive(viewer_window, tmp_path, qtbot):
    path = tmp_path / "grande.pdf"
    doc = pymupdf.open()
    for i in range(400):
        doc.new_page().insert_text((72, 100), f"Pagina {i} testo di riempimento", fontsize=12)
    doc[-1].insert_text((72, 200), "Patek", fontsize=12)
    doc.save(str(path))
    viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    t = time.perf_counter()
    ctl.search("patek", wait=False)
    assert time.perf_counter() - t < 0.5        # l'avvio non blocca
    qtbot.waitUntil(lambda: ctl.job is None, timeout=15000)
    assert len(ctl.hits) == 1 and ctl.hits[0].page == 399


def test_regex_and_invalid_expression(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["CAP 20121 e 00184", "tel 0212345678"], ["10100"]]))
    ctl = _ctl(viewer_window)
    assert ctl.search(r"\b\d{5}\b", regex=True) == 3
    assert ctl.search("(", regex=True) == 0
    assert "non valida" in ctl.bar.counter.text()
    assert ctl.search("cap", regex=False) == 1


def test_export_csv(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["Rolex uno"], ["Rolex due"]]))
    ctl = _ctl(viewer_window)
    ctl.search("rolex", regex=False)
    out = ctl.export_csv(str(tmp_path / "r.csv"))
    import csv
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    assert rows[0][0] == "pagina" and len(rows) == 3
    assert rows[2][0] == "2" and rows[2][3] == "Rolex"


def test_phrase_continuing_on_next_page(viewer_window, tmp_path):
    view = viewer_window.open_path(_pdf(tmp_path, [["inizio", "il prezzo del"], ["Rolex Daytona", "fine"]]))
    ctl = _ctl(viewer_window)
    assert ctl.search("del rolex") == 1
    hit = ctl.hits[0]
    assert hit.page == 0 and hit.cont is not None and hit.cont[0] == 1
    assert 0 in view.search_hits and 1 in view.search_hits     # evidenziata su entrambe le pagine
    assert "Rolex" in hit.found


def test_hyphenated_word_across_pages(viewer_window, tmp_path):
    viewer_window.open_path(_pdf(tmp_path, [["una infor-"], ["mazione utile"]]))
    assert _ctl(viewer_window).search("informazione") == 1


def test_search_also_in_comments(viewer_window, tmp_path):
    path = tmp_path / "commenti.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo della pagina", fontsize=12)
    page.add_text_annot((300, 300), "Da verificare con il cliente")
    doc.new_page()
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    assert ctl.search("verificare", comments=False) == 0
    assert ctl.search("verificare", comments=True) == 1
    hit = ctl.hits[0]
    assert hit.comment and hit.page == 0
    assert "commento" in ctl.panel.tree.topLevelItem(0).child(0).text(0)
    assert 0 in view.search_hits


def test_large_document_full_count_is_fast(viewer_window, tmp_path):
    """NFR-07: 500 pagine con 18.000 occorrenze, conteggio completo in tempi ragionevoli
    (prima ~50 s: contesto calcolato riscorrendo la pagina e rinumerazione a ogni pagina)."""
    path = tmp_path / "grande.pdf"
    doc = pymupdf.open()
    line = "Il nostro orologio preferito è un orologio automatico, orologio da polso."
    for _i in range(500):
        doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), "\n".join([line] * 12), fontsize=10)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    t = time.perf_counter()
    ctl.search("orologio")
    ctl.finish()
    elapsed = time.perf_counter() - t
    assert len(ctl.hits) == 18000
    assert elapsed < 10, f"{elapsed:.1f} s"
    assert [h.page for h in ctl.hits] == sorted(h.page for h in ctl.hits)
    h = ctl.hits[3]
    assert h.found == "orologio" and h.before and h.after
    flat = [c.data(0, Qt.ItemDataRole.UserRole) for p in ctl.panel._pages
            for c in (ctl.panel._page_items[p].child(n) for n in range(ctl.panel._page_items[p].childCount()))]
    assert flat == list(range(18000))           # numerazione dell'elenco completa e ordinata
    view.document._saved_state = view.document._state


def test_snippet_from_indices_matches_and_is_linear():
    from foliant.modules.viewer.engine import (
        context_snippet, extract_page_text, find_hits, find_spans, rects_of, span_snippet,
    )
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), "\n".join(["Un orologio e un altro orologio."] * 30),
                        fontsize=10)
    pt = extract_page_text(page)
    spans = find_spans(pt, "orologio")
    assert len(spans) == 60
    for src in spans[:5]:
        assert span_snippet(pt, src, 28) == context_snippet(pt, rects_of(pt, src), 28)
    assert [s for _r, s in find_hits(pt, "orologio")][:2] == [span_snippet(pt, s, 28) for s in spans[:2]]

    def cost(n):
        """Tempo per occorrenza su una pagina con 80·n occorrenze."""
        big = pymupdf.open().new_page(width=595, height=842 * n)
        big.insert_textbox(pymupdf.Rect(40, 40, 560, 842 * n - 40),
                           "\n".join(["Un orologio e un altro orologio."] * 40 * n), fontsize=10)
        text = extract_page_text(big)
        t = time.perf_counter()
        for _r in range(3):
            hits = find_hits(text, "orologio")
        assert len(hits) == 80 * n
        return (time.perf_counter() - t) / len(hits)
    small, large = min(cost(1) for _i in range(3)), min(cost(8) for _i in range(3))
    assert large < small * 4          # lineare: il costo per occorrenza non cresce con la pagina (prima ×8)


def test_ux18_ctrl_g_moves_between_results(viewer_window, tmp_path, qtbot):
    """UX-18: Ctrl+G / Ctrl+Maiusc+G = occorrenza successiva / precedente (come F3 / Maiusc+F3);
    «Vai a pagina» non usa Ctrl+G (Ctrl+L è il campo pagina del nucleo)."""
    from PySide6.QtGui import QAction, QKeySequence
    view = viewer_window.open_path(_pdf(tmp_path, [["Rolex Rolex"], ["Rolex"]]))
    ctl = _ctl(viewer_window)
    owners = [a for a in viewer_window.findChildren(QAction)
              if any(s == QKeySequence("Ctrl+G") for s in a.shortcuts())]
    assert owners == [ctl.act_next]
    assert QKeySequence("Ctrl+Shift+G") in ctl.act_prev.shortcuts()
    assert viewer_window.ctx.viewer_parts["navigation"].act_goto.shortcut().isEmpty()
    assert "Ctrl+G" in ctl.act_next.toolTip() and "F3" in ctl.act_next.toolTip()
    ctl.search("rolex")
    ctl.finish()
    assert ctl.current == 0
    viewer_window.activateWindow()
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    assert ctl.current == 1
    qtbot.keyClick(view, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert ctl.current == 0


def test_context_menu_search_selected_text(viewer_window, tmp_path):
    """UX-07: «Cerca» nel menu contestuale del testo selezionato."""
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QMenu

    from foliant.api import PageEvent, selection_from_words
    view = viewer_window.open_path(_pdf(tmp_path, [["Rolex Daytona Rolex"]]))
    ctl = _ctl(viewer_window)
    pe = PageEvent(0, pymupdf.Point(100, 100), QPointF(10, 10), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    menu = QMenu()
    ctl._menu_provider(view, pe, menu)
    assert menu.isEmpty()                      # senza selezione nessuna voce
    view.set_text_selection(selection_from_words(0, view.words(0)[:1]))
    assert ctl._menu_provider in viewer_window.view_menu_providers
    ctl._menu_provider(view, pe, menu)
    [act] = [a for a in menu.actions() if not a.isSeparator()]
    assert act.text() == "Cerca"
    act.trigger()
    ctl.finish()
    assert ctl.bar.isVisible() and ctl.bar.field.text() == "Rolex"
    assert len(ctl.hits) == 2
