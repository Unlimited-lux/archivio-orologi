"""Test del pannello Allegati (VIS-22)."""

from __future__ import annotations

import os
import stat

import pymupdf
from PySide6.QtWidgets import QApplication
import pytest

from foliant.modules.viewer import attachments_engine as engine

from .conftest import part


def _inner_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Documento allegato interno", fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


INNER = _inner_pdf()
TEXT = "Riga 1\nRiga 2 con àccenti\n".encode("utf-8") * 50
SCRIPT = b"#!/bin/sh\necho ciao\n"


@pytest.fixture
def attach_pdf(tmp_path):
    path = tmp_path / "con_allegati.pdf"
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
    doc.embfile_add("contratto.pdf", INNER, filename="contratto.pdf", desc="Contratto firmato")
    doc.embfile_add("note.txt", TEXT, filename="note.txt", desc="Note di lavoro")
    doc.embfile_add("installa.sh", SCRIPT, filename="installa.sh", desc="Script")
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def page_attach_pdf(tmp_path):
    path = tmp_path / "allegato_pagina.pdf"
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
    doc[2].add_file_annot((100, 200), b"dati della pagina", "misure.csv", desc="Misure")
    doc.embfile_add("../../x.pdf", INNER, filename="../../x.pdf", desc="Nome pericoloso")
    doc.save(str(path))
    doc.close()
    return str(path)


def _open(window, path):
    view = window.ctx.open_path(path)
    panel = part(window, "attachments")
    window.ctx.show_sidebar_panel("attachments")
    return view, panel


def _by_name(panel, name):
    return next(a for a in panel.items() if a.filename == name)


# ----------------------------------------------------------------- elenco
def test_three_attachments_listed(viewer_window, attach_pdf):
    view, panel = _open(viewer_window, attach_pdf)
    assert panel.view is view
    assert panel.tree.topLevelItemCount() == 3
    rows = {panel.tree.topLevelItem(i).text(0): panel.tree.topLevelItem(i) for i in range(3)}
    assert set(rows) == {"contratto.pdf", "note.txt", "installa.sh"}
    assert rows["note.txt"].text(1) == "Note di lavoro"
    assert rows["contratto.pdf"].text(1) == "Contratto firmato"
    assert _by_name(panel, "note.txt").size == len(TEXT)
    assert rows["installa.sh"].text(2) == f"{len(SCRIPT)} B"
    assert rows["note.txt"].text(2) == engine.format_size(len(TEXT))
    assert rows["contratto.pdf"].text(4)  # data di creazione mostrata
    assert not panel.is_empty


def test_sort_by_size_is_numeric(viewer_window, attach_pdf):
    _view, panel = _open(viewer_window, attach_pdf)
    panel.tree.sortByColumn(2, pymupdf_order())
    names = [panel.tree.topLevelItem(i).text(0) for i in range(3)]
    sizes = [_by_name(panel, n).size for n in names]
    assert sizes == sorted(sizes)


def pymupdf_order():
    from PySide6.QtCore import Qt
    return Qt.SortOrder.AscendingOrder


def test_empty_state(viewer_window, sample_pdf):
    panel = part(viewer_window, "attachments")
    assert panel.is_empty and "Nessun documento" in panel.empty.text()
    _open(viewer_window, sample_pdf)
    assert panel.is_empty
    assert "non contiene allegati" in panel.empty.text()
    assert not panel.btn_save_all.isEnabled()
    assert panel.btn_add.isEnabled()


def test_page_attachment_with_page_and_jump(viewer_window, page_attach_pdf):
    view, panel = _open(viewer_window, page_attach_pdf)
    att = _by_name(panel, "misure.csv")
    assert att.page == 2 and att.kind == engine.PAGE and att.description == "Misure"
    row = next(panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())
               if panel.tree.topLevelItem(i).text(0) == "misure.csv")
    assert row.text(3) == "3"
    panel._double_clicked(row, 0)
    assert view.current_page == 2
    assert panel.data_of(att) == b"dati della pagina"


# ------------------------------------------------------------------- apri
def test_open_pdf_attachment_in_new_tab(viewer_window, attach_pdf, monkeypatch):
    view, panel = _open(viewer_window, attach_pdf)
    opened_external = []
    monkeypatch.setattr(panel, "open_external", lambda p: opened_external.append(p) or True)
    before = len(viewer_window.views())
    new_view = panel.open_item(_by_name(panel, "contratto.pdf"))
    assert new_view is not None
    assert len(viewer_window.views()) == before + 1
    assert viewer_window.current_view() is new_view
    assert new_view.document.path is None          # nulla scritto su disco
    assert new_view.document.display_name == "contratto.pdf"
    assert "Documento allegato interno" in new_view.document.page_text(0)
    assert not opened_external


def test_open_other_type_asks_and_uses_private_temp(viewer_window, attach_pdf, monkeypatch):
    _view, panel = _open(viewer_window, attach_pdf)
    opened = []
    monkeypatch.setattr(panel, "open_external", lambda p: opened.append(p) or True)
    asked = []
    monkeypatch.setattr(panel, "confirm", lambda t, m: asked.append(m) or False)
    assert panel.open_item(_by_name(panel, "note.txt")) is None   # avviso rifiutato
    assert asked and not opened
    monkeypatch.setattr(panel, "confirm", lambda t, m: True)
    path = panel.open_item(_by_name(panel, "note.txt"))
    try:
        assert opened == [path]
        assert open(path, "rb").read() == TEXT
        assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    finally:
        panel.cleanup_temp()
    assert not os.path.exists(path)


def test_executable_open_blocked(viewer_window, attach_pdf, monkeypatch, tmp_path):
    _view, panel = _open(viewer_window, attach_pdf)
    errors, opened = [], []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append((t, m)))
    monkeypatch.setattr(panel, "open_external", lambda p: opened.append(p) or True)
    monkeypatch.setattr(panel, "confirm", lambda t, m: pytest.fail("nessun avviso: apertura bloccata"))
    att = _by_name(panel, "installa.sh")
    assert panel.open_item(att) is None
    assert errors and "Apertura bloccata" in errors[0][0] and not opened
    # resta disponibile «Salva come»
    target = panel.save_item(att, str(tmp_path / "copia.sh"))
    assert open(target, "rb").read() == SCRIPT
    # nel menu contestuale «Apri» è disattivato
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    panel.build_menu(menu, att)
    acts = {a.text(): a for a in menu.actions() if a.text()}
    assert not acts["Apri"].isEnabled() and acts["Salva come…"].isEnabled()


def test_engine_executable_detection():
    for name in ("a.exe", "b.sh", "c.BAT", "d.cmd", "e.js", "f.py", "g.jar", "h.msi", "i.bin", "j.run",
                 "k.AppImage", "l.desktop", "fattura.pdf.exe"):
        assert engine.is_executable(name), name
    for name in ("a.pdf", "b.txt", "c.xml", "d.png", "senza_estensione"):
        assert not engine.is_executable(name), name
    assert engine.is_executable("senza_estensione", b"\x7fELF\x02\x01")
    assert engine.is_executable("leggimi.txt", b"MZ\x90\x00")


# ------------------------------------------------------------------- salva
def test_save_as_byte_identical(viewer_window, attach_pdf, monkeypatch, tmp_path):
    view, panel = _open(viewer_window, attach_pdf)
    for name, data in (("contratto.pdf", INNER), ("note.txt", TEXT)):
        target = tmp_path / f"out_{name}"
        monkeypatch.setattr(panel, "ask_save_path", lambda suggested, t=target: str(t))
        assert panel.save_item(_by_name(panel, name)) == str(target)
        assert target.read_bytes() == data
    with pymupdf.open(attach_pdf) as ref:
        assert (tmp_path / "out_contratto.pdf").read_bytes() == ref.embfile_get("contratto.pdf")
    assert not view.document.is_modified


def test_sanitized_name_and_save_all(viewer_window, page_attach_pdf, monkeypatch, tmp_path):
    assert engine.safe_filename("../../x.pdf") == "x.pdf"
    assert engine.safe_filename("..\\..\\win\\y.txt") == "y.txt"
    assert engine.safe_filename("/etc/passwd") == "passwd"
    assert engine.safe_filename("..") == "allegato"
    _view, panel = _open(viewer_window, page_attach_pdf)
    att = _by_name(panel, "../../x.pdf")
    suggested = []
    monkeypatch.setattr(panel, "ask_save_path", lambda s: suggested.append(s) or str(tmp_path / s))
    panel.save_item(att)
    assert suggested == ["x.pdf"]
    folder = tmp_path / "tutti"
    folder.mkdir()
    monkeypatch.setattr(panel, "ask_directory", lambda: str(folder))
    written = panel.save_all()
    assert sorted(os.listdir(folder)) == ["misure.csv", "x.pdf"]
    assert len(written) == 2
    assert (folder / "x.pdf").read_bytes() == INNER
    assert not (tmp_path.parent / "x.pdf").exists()


# ------------------------------------------------------- aggiungi / elimina
def test_add_and_delete_are_undoable(viewer_window, attach_pdf, monkeypatch, tmp_path):
    view, panel = _open(viewer_window, attach_pdf)
    doc = view.document
    new_file = tmp_path / "foto.png"
    new_file.write_bytes(b"\x89PNG finta")
    monkeypatch.setattr(panel, "ask_open_paths", lambda: [str(new_file)])
    assert panel.add_files() == ["foto.png"]
    assert panel.tree.topLevelItemCount() == 4
    assert doc.is_modified and doc.undo_label == "Aggiungi allegato"
    assert panel.data_of(_by_name(panel, "foto.png")) == b"\x89PNG finta"
    doc.undo()
    QApplication.processEvents()          # l'elenco si rilegge una volta, al giro di eventi
    assert panel.tree.topLevelItemCount() == 3
    doc.redo()
    QApplication.processEvents()
    assert panel.tree.topLevelItemCount() == 4

    monkeypatch.setattr(panel, "confirm", lambda t, m: False)
    assert not panel.delete_item(_by_name(panel, "note.txt"))
    assert panel.tree.topLevelItemCount() == 4
    monkeypatch.setattr(panel, "confirm", lambda t, m: True)
    assert panel.delete_item(_by_name(panel, "note.txt"))
    assert {a.filename for a in panel.items()} == {"contratto.pdf", "installa.sh", "foto.png"}
    assert doc.undo_label == "Elimina allegato"
    doc.undo()
    assert {a.filename for a in panel.items()} == {"contratto.pdf", "note.txt", "installa.sh", "foto.png"}
    doc.redo()
    assert "note.txt" not in {a.filename for a in panel.items()}


def test_hidden_panel_does_not_read_attachments_on_change(viewer_window, attach_pdf, monkeypatch):
    """Prestazioni: a pannello nascosto una modifica non rilegge gli allegati; si rileggono
    una sola volta quando il pannello viene mostrato."""
    view, panel = _open(viewer_window, attach_pdf)
    viewer_window.ctx.toggle_sidebar_panel("attachments")
    QApplication.processEvents()
    assert not panel.isVisible()
    calls = []
    real = engine.list_attachments
    monkeypatch.setattr(engine, "list_attachments", lambda pdf: calls.append(1) or real(pdf))
    for i in range(3):
        with view.document.change("Testo") as pdf:
            pdf[0].insert_text((72, 300 + 10 * i), "x")
    QApplication.processEvents()
    assert calls == []
    viewer_window.ctx.show_sidebar_panel("attachments")
    QApplication.processEvents()
    assert len(calls) == 1 and panel.tree.topLevelItemCount() == 3
    view.document._saved_state = view.document._state


def test_visible_panel_groups_changes(viewer_window, attach_pdf, monkeypatch):
    view, panel = _open(viewer_window, attach_pdf)
    assert panel.isVisible()
    calls = []
    real = engine.list_attachments
    monkeypatch.setattr(engine, "list_attachments", lambda pdf: calls.append(1) or real(pdf))
    for i in range(3):
        with view.document.change("Testo") as pdf:
            pdf[0].insert_text((72, 300 + 10 * i), "x")
    assert calls == []
    QApplication.processEvents()
    assert len(calls) == 1
    view.document._saved_state = view.document._state


def test_engine_delete_middle_item_keeps_others():
    doc = pymupdf.open()
    doc.new_page()
    for n in ("a.txt", "b.txt", "c.txt", "d.txt"):
        doc.embfile_add(n, n.encode() * 3, filename=n)
    doc = pymupdf.open("pdf", doc.tobytes())
    item = next(a for a in engine.list_attachments(doc) if a.key == "c.txt")
    engine.delete(doc, item)
    assert {n: doc.embfile_get(n) for n in doc.embfile_names()} == {
        "a.txt": b"a.txta.txta.txt", "b.txt": b"b.txtb.txtb.txt", "d.txt": b"d.txtd.txtd.txt"}
    engine._rebuild_without(doc, "a.txt")
    assert [a.filename for a in engine.list_attachments(doc)] == ["b.txt", "d.txt"]


def test_delete_page_attachment_undoable(viewer_window, page_attach_pdf, monkeypatch):
    view, panel = _open(viewer_window, page_attach_pdf)
    monkeypatch.setattr(panel, "confirm", lambda t, m: True)
    assert panel.delete_item(_by_name(panel, "misure.csv"))
    assert "misure.csv" not in {a.filename for a in panel.items()}
    view.document.undo()
    assert "misure.csv" in {a.filename for a in panel.items()}


def test_add_duplicate_name_gets_unique_key(viewer_window, attach_pdf, tmp_path):
    view, panel = _open(viewer_window, attach_pdf)
    f = tmp_path / "note.txt"
    f.write_bytes(b"altro")
    assert panel.add_files([str(f)]) == ["note (2).txt"]


def test_switching_views_updates_panel(viewer_window, attach_pdf, sample_pdf):
    _v1, panel = _open(viewer_window, attach_pdf)
    assert panel.tree.topLevelItemCount() == 3
    viewer_window.ctx.open_path(sample_pdf)
    assert panel.is_empty


def test_translations_registered(viewer_window):
    from foliant.modules.viewer.attachments import EN
    assert EN["Allegati"] == "Attachments"


# ------------------------------------------------------------------ portfolio
def _portfolio(tmp_path):
    """Portfolio con 2 cartelle e 5 file; documento iniziale «<1>contratto.pdf»."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Pagina di copertina del portfolio")
    small = pymupdf.open()
    small.new_page().insert_text((72, 100), "Contratto")
    pdf_bytes = small.tobytes()
    files = [("<1>contratto.pdf", "contratto.pdf", pdf_bytes), ("<1>allegato A.txt", "allegato A.txt", b"A"),
             ("<2>foto rolex.png", "foto rolex.png", b"\x89PNG"), ("<2>foto omega.png", "foto omega.png", b"\x89PNG"),
             ("listino.txt", "listino.txt", b"prezzi")]
    for key, name, data in files:
        doc.embfile_add(key, data, filename=name, ufilename=name, desc=f"File {name}")
    root = doc.get_new_xref()
    f1 = doc.get_new_xref()
    f2 = doc.get_new_xref()
    doc.update_object(root, f"<< /Type /Folder /ID 0 /Name (Portfolio) /Child {f1} 0 R >>")
    doc.update_object(f1, f"<< /Type /Folder /ID 1 /Name (Contratti) /Parent {root} 0 R /Next {f2} 0 R >>")
    doc.update_object(f2, f"<< /Type /Folder /ID 2 /Name (Fotografie) /Parent {root} 0 R >>")
    col = doc.get_new_xref()
    doc.update_object(col, f"<< /Type /Collection /Folders {root} 0 R /D (<1>contratto.pdf) /View /D >>")
    doc.xref_set_key(doc.pdf_catalog(), "Collection", f"{col} 0 R")
    path = tmp_path / "portfolio.pdf"
    doc.save(str(path))
    return str(path)


def test_portfolio_folders_and_initial_document(viewer_window, tmp_path):
    """Dato un portfolio con 2 cartelle e 5 file, la vista mostra cartelle e file navigabili ed
    evidenzia il documento iniziale indicato da /D."""
    view, panel = _open(viewer_window, _portfolio(tmp_path))
    assert panel.collection is not None and panel.collection.initial == "<1>contratto.pdf"
    top = {panel.tree.topLevelItem(i).text(0): panel.tree.topLevelItem(i)
           for i in range(panel.tree.topLevelItemCount())}
    assert {"Contratti", "Fotografie", "listino.txt"} == set(top)
    contracts = top["Contratti"]
    assert sorted(contracts.child(i).text(0) for i in range(contracts.childCount())) == ["allegato A.txt",
                                                                                       "contratto.pdf"]
    photos = top["Fotografie"]
    assert photos.childCount() == 2 and photos.isExpanded()
    current = panel.current_item()
    assert current is not None and current.filename == "contratto.pdf"
    assert panel.tree.currentItem().font(0).bold()
    assert len(panel.items()) == 5
    # i PDF contenuti si aprono in schede
    new_view = panel.open_item(current)
    assert new_view is not None and new_view is not view
    assert "Contratto" in new_view.document.pdf[0].get_text()


def test_portfolio_search_by_name_inside_folders(viewer_window, tmp_path):
    """Dato un portfolio con 5 file, cercando una parte del nome l'elenco mostra solo i file
    corrispondenti, anche dentro le cartelle."""
    view, panel = _open(viewer_window, _portfolio(tmp_path))
    panel.filter_edit.setText("foto")
    assert sorted(panel.visible_names()) == ["foto omega.png", "foto rolex.png"]
    panel.filter_edit.setText("ROLEX")
    assert panel.visible_names() == ["foto rolex.png"]
    panel.filter_edit.setText("")
    assert len(panel.visible_names()) == 5


def test_plain_document_search_filter(viewer_window, attach_pdf):
    view, panel = _open(viewer_window, attach_pdf)
    assert panel.collection is None and not panel.tree.rootIsDecorated()
    panel.filter_edit.setText("note")
    assert panel.visible_names() == ["note.txt"]
