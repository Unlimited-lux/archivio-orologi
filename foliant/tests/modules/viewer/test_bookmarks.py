"""VIS-15 (consultazione) e VIS-16 (creazione e modifica) dei segnalibri."""

from __future__ import annotations

import time

import pymupdf
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor

from foliant.api import Document


def _panel(window):
    return window.ctx.viewer_parts["bookmarks"].panel


def _toc_pdf(tmp_path, name="toc.pdf", pages=20):
    path = tmp_path / name
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
    doc.set_toc([
        [1, "Capitolo 1", 1],
        [2, "Sezione 1.1", 2],
        [2, "Sezione 1.2", 3],
        [1, "Capitolo 2", 5],
        [1, "Capitolo 3", 8, {"kind": pymupdf.LINK_GOTO, "page": 7, "to": pymupdf.Point(0, 0),
                              "bold": True, "italic": True, "color": (1, 0, 0)}],
        [2, "Sezione 3.1", 9],
        [2, "Sezione 3.2", 10],
        [2, "Sezione 3.3", 11],
    ], collapse=0)
    doc.save(str(path))
    return str(path)


def _open(window, path):
    view = window.open_path(path)
    window.ctx.show_sidebar_panel("bookmarks")
    return view, _panel(window)


def test_tree_and_click_navigation(viewer_window, tmp_path, qtbot):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    assert panel.tree.topLevelItemCount() == 3
    assert panel.tree.topLevelItem(0).childCount() == 2
    item = panel.item(3)
    rect = panel.tree.visualItemRect(item)
    qtbot.mouseClick(panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert view.current_page == 4
    nav = viewer_window.ctx.viewer_parts["navigation"]
    assert nav.state(view).history.can_back  # salto registrato in cronologia


def test_xyz_destination_with_zoom(viewer_window, tmp_path):
    path = tmp_path / "xyz.pdf"
    doc = pymupdf.open()
    for i in range(12):
        doc.new_page(width=595, height=842)
    doc.set_toc([[1, "Dieci", 10]])
    xref = doc.get_outline_xrefs()[0]
    page_xref = doc[9].xref
    doc.xref_set_key(xref, "A", f"<</S/GoTo/D[{page_xref} 0 R/XYZ 72 720 2]>>")
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    panel.follow(0)
    assert view.current_page == 9
    assert abs(view.zoom - 2.0) < 1e-3
    inv, _ok = view.page_transform(9).inverted()
    tl = inv.map(QPointF(0, 0))
    # (72, 720) in coordinate PDF = (72, 122) in coordinate pagina dall'alto
    assert abs(tl.x() - 72) < 2 and abs(tl.y() - 122) < 2


def test_collapsed_state_and_styles(viewer_window, tmp_path):
    path = tmp_path / "stili.pdf"
    doc = pymupdf.open()
    for i in range(5):
        doc.new_page()
    doc.set_toc([[1, "Aperto", 1], [2, "a", 1], [1, "Chiuso", 2], [2, "b", 2]], collapse=0)
    xrefs = doc.get_outline_xrefs()
    doc.xref_set_key(xrefs[2], "Count", "-1")
    doc.xref_set_key(xrefs[0], "F", "3")
    doc.xref_set_key(xrefs[0], "C", "[1 0 0]")
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    assert panel.item(0).isExpanded()
    assert not panel.item(2).isExpanded()
    font = panel.item(0).font(0)
    assert font.bold() and font.italic()
    assert panel.item(0).foreground(0).color() == QColor(255, 0, 0)


def test_current_bookmark_highlight(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    view.go_to_page(8)   # dentro il capitolo 3 (sezione 3.1)
    assert panel.items[panel.current_index()].title == "Sezione 3.1"
    view.go_to_page(5)
    assert panel.items[panel.current_index()].title == "Capitolo 2"
    view.go_to_page(6)
    assert panel.items[panel.current_index()].title == "Capitolo 2"


def test_add_bookmark_saved_and_reopened(viewer_window, sample_pdf, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path, pages=10))
    view.go_to_page(6)
    pos = panel.add_bookmark()
    assert pos is not None
    panel.rename(pos, "Introduzione")
    out = tmp_path / "salvato.pdf"
    view.document.save(out)
    doc = Document.open(out)
    with doc.lock:
        toc = doc.pdf.get_toc()
    entry = [t for t in toc if t[1] == "Introduzione"]
    assert entry and entry[0][2] == 7


def test_add_bookmark_on_document_without_outline(viewer_window, sample_pdf):
    view, panel = _open(viewer_window, sample_pdf)
    assert not panel.tree.isVisible() and panel.empty.isVisible()
    view.go_to_page(2)
    panel.add_bookmark()
    assert panel.items[0].title == "Pagina 3" and panel.items[0].page == 2
    assert view.document.is_modified
    view.document.undo()
    assert panel.items == []


def test_drag_under_another_keeps_count(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    # "Capitolo 2" (3) diventa figlio di "Capitolo 1" (0)
    assert panel.move_to(3, 0, "child")
    out = tmp_path / "trascinato.pdf"
    view.document.save(out)
    doc = pymupdf.open(str(out))
    toc = doc.get_toc()
    assert [t[:2] for t in toc][:4] == [[1, "Capitolo 1"], [2, "Sezione 1.1"], [2, "Sezione 1.2"], [2, "Capitolo 2"]]
    first = doc.get_outline_xrefs()[0]
    kind, count = doc.xref_get_key(first, "Count")
    assert abs(int(count)) == 3


def test_unicode_title_roundtrip(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    panel.rename(0, "Résumé — 概要")
    out = tmp_path / "unicode.pdf"
    view.document.save(out)
    doc = pymupdf.open(str(out))
    assert doc.get_toc()[0][1] == "Résumé — 概要"


def test_delete_with_children_and_undo(viewer_window, tmp_path, monkeypatch):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    asked = []
    monkeypatch.setattr(viewer_window.ctx, "confirm", lambda t, m: asked.append(m) or True)
    before = [(b.level, b.title, b.page) for b in panel.items]
    assert panel.delete(4)       # Capitolo 3 con 3 figli
    assert asked and "3" in asked[0]
    assert [b.title for b in panel.items] == ["Capitolo 1", "Sezione 1.1", "Sezione 1.2", "Capitolo 2"]
    view.document.undo()
    assert [(b.level, b.title, b.page) for b in panel.items] == before
    view.document.redo()
    assert len(panel.items) == 4


def test_move_and_indent_operations(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    panel.tree.setCurrentItem(panel.item(3))
    assert panel._op("up")
    assert panel.items[0].title == "Capitolo 2"
    panel.tree.setCurrentItem(panel.item(0))
    assert panel._op("down")
    assert panel.items[3].title == "Capitolo 2"
    assert panel._op("indent")
    assert panel.items[3].level == 2
    assert panel._op("outdent")
    assert panel.items[3].level == 1
    assert view.document.can_undo


def test_styles_and_destination(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    assert panel.set_style(0, bold=True, color=(0, 0, 1))
    assert panel.items[0].bold and panel.items[0].color == (0.0, 0.0, 1.0)
    view.go_to_page(15)
    assert panel.set_destination_here(1)
    assert panel.items[1].page == 15


def test_broken_destination(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    with view.document.change("Elimina pagine") as pdf:
        pdf.delete_pages(from_page=4, to_page=19)   # le destinazioni dei capitoli 2 e 3 spariscono
    titles = [b.title for b in panel.items]
    assert "Capitolo 2" in titles  # mai rimosso in silenzio
    i = titles.index("Capitolo 2")
    assert panel.item(i).font(0).italic() or panel.item(i).toolTip(0)
    page = view.current_page
    assert not panel.follow(i) or view.current_page == page


def test_filter(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    panel.filter.setText("3.2")
    visible = [panel.items[i].title for i, it in enumerate(panel._tree_items) if not it.isHidden()]
    assert visible == ["Capitolo 3", "Sezione 3.2"]
    panel.filter.setText("")
    assert not any(it.isHidden() for it in panel._tree_items)


def test_large_outline_loads_fast(viewer_window, tmp_path):
    path = tmp_path / "grande.pdf"
    doc = pymupdf.open()
    for i in range(50):
        doc.new_page()
    toc = []
    for i in range(1000):
        toc.append([1, f"Capitolo {i}", i % 50 + 1])
        for j in range(4):
            toc.append([2, f"Voce {i}.{j}", i % 50 + 1])
    doc.set_toc(toc, collapse=1)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    panel = _panel(viewer_window)
    panel.set_view(None)
    t = time.perf_counter()
    panel.set_view(view)
    assert time.perf_counter() - t < 1.5   # 5.000 voci (margine per la macchina di test)
    assert panel.tree.topLevelItemCount() == 1000


def test_read_only_when_modification_forbidden(viewer_window, tmp_path):
    path = tmp_path / "bloccato.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    view, panel = _open(viewer_window, str(path))
    assert not panel.editable
    assert not panel.actions["add"].isEnabled()
    assert panel.add_bookmark() is None


# ------------------------------------------------------------ VIS-17 (strumenti)

def test_export_import_text(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    out = panel.export_text(str(tmp_path / "s.txt"))
    text = open(out, encoding="utf-8").read()
    assert "\tSezione 1.1\t2" in text
    (tmp_path / "nuovi.txt").write_text("Uno\t1\n\tDue\t2\n\t\tTre\t3\nFuori\t999\n", encoding="utf-8")
    assert panel.import_text(str(tmp_path / "nuovi.txt"), replace=True)
    assert [(b.level, b.title, b.page) for b in panel.items] == [(1, "Uno", 0), (2, "Due", 1), (3, "Tre", 2)]
    view.document.undo()
    assert panel.items[0].title == "Capitolo 1"


def test_generate_every_n_and_from_headings(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "titoli.pdf"
    doc = pymupdf.open()
    for i in range(4):
        p = doc.new_page()
        p.insert_text((72, 80), f"Capitolo {i + 1}", fontsize=22)
        for k in range(8):
            p.insert_text((72, 120 + k * 14), "corpo del testo normale " * 3, fontsize=10)
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    assert panel.generate("every", n=2, replace=True)
    assert [b.page for b in panel.items] == [0, 2]
    monkeypatch.setattr(viewer_window.ctx, "confirm", lambda *a: True)
    assert panel.generate("headings")
    assert [b.title for b in panel.items] == ["Capitolo 1", "Capitolo 2", "Capitolo 3", "Capitolo 4"]


def test_generate_from_scan_reports(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "vuoto.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    msgs = []
    monkeypatch.setattr(viewer_window.ctx, "info", lambda t, m: msgs.append(m))
    assert not panel.generate("headings")
    assert "OCR" in msgs[0]
    assert not view.document.is_modified


def test_verify_lists_broken(viewer_window, tmp_path, monkeypatch):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    with view.document.change("Elimina") as pdf:
        pdf.delete_pages(from_page=10, to_page=19)
    msgs = []
    monkeypatch.setattr(viewer_window.ctx, "info", lambda t, m: msgs.append(m))
    problems = panel.verify()
    assert {panel.items[i].title for i, why in problems if why == "broken"} == {"Sezione 3.3"}
    assert "Sezione 3.3" in msgs[0]


def test_rename_by_editing_item(viewer_window, tmp_path):
    view, panel = _open(viewer_window, _toc_pdf(tmp_path))
    panel.item(0).setText(0, "Nuovo titolo")
    assert panel.items[0].title == "Nuovo titolo"
    assert view.document.undo_label == "Rinomina segnalibro"


def test_read_only_kept_after_document_change(viewer_window, tmp_path):
    path = tmp_path / "bloccato2.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ANNOTATE)
    view, panel = _open(viewer_window, str(path))
    with view.document.change("Annotazione") as pdf:
        pdf[0].add_text_annot((50, 50), "nota")
    panel.reload()
    assert not panel.editable


def test_generate_from_regex_with_levels(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "numerati.pdf"
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 80), f"{i + 1} Capitolo {i + 1}", fontsize=14)
        p.insert_text((72, 120), f"{i + 1}.1 Sezione", fontsize=12)
        p.insert_text((72, 160), "testo normale senza numero", fontsize=10)
    doc.save(str(path))
    view, panel = _open(viewer_window, str(path))
    assert panel.generate("regex", pattern=r"^(?P<level>\d+(\.\d+)*)\s+\S", replace=True)
    assert [(b.level, b.title) for b in panel.items][:2] == [(1, "1 Capitolo 1"), (2, "1.1 Sezione")]
    assert len(panel.items) == 6
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    assert not panel.generate("regex", pattern="(", replace=True)
    assert errors and "non valida" in errors[0]


def test_empty_panel_layout_compact(viewer_window, sample_pdf):
    view, panel = _open(viewer_window, sample_pdf)
    viewer_window.resize(1200, 800)
    from PySide6.QtWidgets import QApplication, QLabel, QToolButton
    QApplication.processEvents()
    title = [w for w in panel.findChildren(QLabel) if w.property("heading")][0]
    assert title.geometry().top() < 40                       # titolo in alto anche senza albero
    buttons = [b for b in panel.findChildren(QToolButton) if b.isVisible()]
    assert len(buttons) <= 4                                  # azioni secondarie nel menu «…»
    assert all(b.width() >= 24 for b in buttons)
    menu_texts = [a.text() for a in panel.tools_button.menu().actions()]
    assert "Sposta su" in menu_texts and "Espandi tutto" in menu_texts
