"""Portfolio PDF (CNV-34)."""

from __future__ import annotations

import pymupdf
import pytest
from PIL import Image

from foliant.modules.convert.portfolio import create_portfolio
from tests.conftest import make_pdf


@pytest.fixture
def four_files(tmp_path):
    a = make_pdf(tmp_path / "contratto.pdf", pages=1)
    b = make_pdf(tmp_path / "fattura.pdf", pages=2)
    c = tmp_path / "scheda.docx"
    c.write_bytes(b"PK\x03\x04 finto docx")
    d = tmp_path / "foto.jpg"
    Image.new("RGB", (40, 30), (200, 0, 0)).save(d, "JPEG")
    return [{"path": a, "folder": "Vendite", "values": {"Cliente": "Rossi"}},
            {"path": b, "folder": "Vendite", "description": "Fattura n. 7", "values": {"Cliente": "Bianchi"}},
            {"path": str(c), "folder": "Schede", "values": {"Cliente": "Verdi"}},
            {"path": str(d), "folder": "Schede", "values": {"Cliente": "Aldo"}}]


def test_portfolio_folders_bytes_cover_schema(four_files):
    data, warnings = create_portfolio(four_files, title="Orologio 1234", view="thumbnails",
                                      columns=[("Cliente", "Cliente", "text")], sort=("Cliente", True))
    doc = pymupdf.open(stream=data)
    cat = doc.pdf_catalog()
    kind, coll = doc.xref_get_key(cat, "Collection")
    assert kind == "xref"
    cx = int(coll.split()[0])
    assert doc.xref_get_key(cx, "View")[1] == "/T"
    assert doc.xref_get_key(cx, "Sort/S")[1] == "/Cliente" and doc.xref_get_key(cx, "Sort/A")[1] == "true"
    assert doc.xref_get_key(cx, "Schema/Cliente/Subtype")[1] == "/S"
    root = int(doc.xref_get_key(cx, "Folders")[1].split()[0])
    child = int(doc.xref_get_key(root, "Child")[1].split()[0])
    nxt = int(doc.xref_get_key(child, "Next")[1].split()[0])
    assert {doc.xref_get_key(child, "Name")[1], doc.xref_get_key(nxt, "Name")[1]} == {"Schede", "Vendite"}
    assert doc.embfile_count() == 4
    originals = {f.get("path") if isinstance(f["path"], str) else str(f["path"]): f for f in four_files}
    for name in doc.embfile_names():
        assert name.startswith("<1>") or name.startswith("<2>")
        content = doc.embfile_get(name)
        assert any(open(p, "rb").read() == content for p in originals)
    import re
    tree = doc.xref_get_key(cat, "Names/EmbeddedFiles/Names")[1]
    for fs in re.findall(r"(\d+) 0 R", tree):
        assert doc.xref_get_key(int(fs), "CI/Cliente")[0] == "string"
    cover = doc[0].get_text()
    for f in ("contratto.pdf", "fattura.pdf", "scheda.docx", "foto.jpg"):
        assert f in cover
    assert "Fattura n. 7" in cover


def test_portfolio_duplicates_and_list_view(tmp_path):
    a = make_pdf(tmp_path / "a" / "doc.pdf" if (tmp_path / "a").mkdir() is None else tmp_path / "x.pdf")
    b = make_pdf(tmp_path / "doc.pdf")
    data, _w = create_portfolio([{"path": a, "name": "doc.pdf"}, {"path": b}], view="list", initial="doc.pdf")
    doc = pymupdf.open(stream=data)
    assert sorted(doc.embfile_names()) == ["doc (2).pdf", "doc.pdf"]
    cx = int(doc.xref_get_key(doc.pdf_catalog(), "Collection")[1].split()[0])
    assert doc.xref_get_key(cx, "View")[1] == "/D" and doc.xref_get_key(cx, "D")[1] == "doc.pdf"


def _foreign_portfolio(tmp_path):
    """Portfolio «di un altro programma»: allegati con filespec in linea e /Collection minima."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Copertina esterna", fontsize=14)
    doc.embfile_add("listino.csv", b"marca;prezzo\nA;1\n", desc="Listino")
    doc.embfile_add("foto.bin", bytes(range(256)) * 10)
    cx = doc.get_new_xref()
    doc.update_object(cx, "<</Type/Collection/View/D>>")
    doc.xref_set_key(doc.pdf_catalog(), "Collection", f"{cx} 0 R")
    path = tmp_path / "esterno.pdf"
    doc.save(str(path))
    return str(path)


def test_edit_foreign_portfolio_undo_and_save(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from foliant.modules.convert.portfolio_dialog import EditPortfolioDialog
    path = _foreign_portfolio(tmp_path)
    before = {n: pymupdf.open(path).embfile_get(n) for n in pymupdf.open(path).embfile_names()}
    new = tmp_path / "nuovo.txt"
    new.write_text("aggiunto", encoding="utf-8")
    view = window.open_path(path)
    ctrl = window.ctx.convert_controller

    def fake_exec(d):
        d.folder.setEditText("Documenti")
        d.add_paths([str(new)])
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(EditPortfolioDialog, "exec", fake_exec)
    names = ctrl.edit_portfolio()
    assert names == ["<1>nuovo.txt"]
    with view.document.lock:
        assert "<1>nuovo.txt" in view.document.pdf.embfile_names()
    assert view.document.undo()
    with view.document.lock:
        assert "<1>nuovo.txt" not in view.document.pdf.embfile_names()
    ctrl.edit_portfolio()
    assert window.save_view(view)
    saved = pymupdf.open(path)
    assert saved.embfile_get("<1>nuovo.txt") == b"aggiunto"
    for n, data in before.items():
        assert saved.embfile_get(n) == data
    cx = int(saved.xref_get_key(saved.pdf_catalog(), "Collection")[1].split()[0])
    assert saved.xref_get_key(cx, "Folders")[0] == "xref"


def test_remove_from_portfolio(tmp_path):
    from foliant.modules.convert.portfolio import add_files, remove_files
    doc = pymupdf.open(_foreign_portfolio(tmp_path))
    remove_files(doc, ["foto.bin"])
    assert doc.embfile_names() == ["listino.csv"]
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert add_files(doc, [{"path": str(f)}, {"path": str(f)}]) == ["a.txt", "a (2).txt"]
