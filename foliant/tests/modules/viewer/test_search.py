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
