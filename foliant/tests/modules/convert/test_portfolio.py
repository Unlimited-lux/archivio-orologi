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


# ------------------------------------------------------- portfolio come sorgente (CNV-01)
def test_convert_portfolio_member_and_cover(four_files, tmp_path):
    from foliant.modules.convert.common import ConversionError
    from foliant.modules.convert.engine import convert_pdf, portfolio_members, run_conversion
    data, _w = create_portfolio(four_files, title="Orologio 1234")
    assert sorted(label for _k, label in portfolio_members(data)) == ["contratto.pdf", "fattura.pdf"]
    out = convert_pdf(data, tmp_path / "f.txt", "txt", portfolio_member="fattura.pdf")
    text = open(out[0], encoding="utf-8").read()
    assert "Pagina 1" in text and "Pagina 2" in text and "Orologio 1234" not in text
    # il documento di copertina resta la scelta predefinita; gli allegati sono elencati nel report
    res = run_conversion(data, tmp_path / "c.txt", "txt")
    assert "Orologio 1234" in open(res.paths[0], encoding="utf-8").read()
    assert sorted(res.details["attachments"]) == ["contratto.pdf", "fattura.pdf", "foto.jpg", "scheda.docx"]
    assert any("scheda.docx" in w for w in res.warnings)
    with pytest.raises(ConversionError):
        convert_pdf(data, tmp_path / "x.txt", "txt", portfolio_member="scheda.docx")
    with pytest.raises(ConversionError):
        convert_pdf(data, tmp_path / "x.txt", "txt", portfolio_member="manca.pdf")
    assert portfolio_members(make_pdf(tmp_path / "semplice.pdf")) == []


def test_export_ui_asks_portfolio_member(window, four_files, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QInputDialog

    from foliant.modules.convert.export_dialog import ExportDialog
    data, _w = create_portfolio(four_files, title="Orologio 1234")
    path = tmp_path / "pratica.pdf"
    path.write_bytes(data)
    window.open_path(str(path))
    asked = []

    def get_item(parent, title, label, items, current, editable):
        asked.append(items)
        return "fattura.pdf", True
    monkeypatch.setattr(QInputDialog, "getItem", get_item)
    seen = {}

    def fake_exec(dlg):
        dlg.format_box.setCurrentIndex(dlg.format_box.findData("txt"))
        dlg.folder_edit.setText(str(tmp_path / "out"))
        seen["pages"] = dlg.page_count
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportDialog, "exec", fake_exec)
    result = window.ctx.convert_controller.export()
    assert asked and asked[0][0] == "Documento di copertina" and "fattura.pdf" in asked[0]
    assert "scheda.docx" not in asked[0]
    assert seen["pages"] == 2
    assert result.paths == [str(tmp_path / "out" / "fattura.txt")]


def test_portfolio_cover_follows_paper_size(four_files):
    data, _w = create_portfolio(four_files, title="Pratica", page_size="Letter")
    doc = pymupdf.open(stream=data)
    assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (612, 792)


# ------------------------------------------------- cartelle, PDF/A-3 e protezione
def test_move_files_and_rename_folder_keep_bytes(four_files):
    from foliant.modules.convert.portfolio import folder_map, folder_of, move_files, rename_folder
    data, _w = create_portfolio(four_files, title="Pratica", initial=1)
    doc = pymupdf.open(stream=data)
    before = {n.split(">")[-1]: doc.embfile_get(n) for n in doc.embfile_names()}
    key = next(n for n in doc.embfile_names() if n.endswith("fattura.pdf"))
    renamed = move_files(doc, [key], "Schede")
    new_key = renamed[key]
    assert folder_of(doc, new_key) == "Schede"
    rename_folder(doc, "Vendite", "Contratti")
    with pytest.raises(Exception):
        rename_folder(doc, "Contratti", "Schede")
    moved_root = move_files(doc, [next(n for n in doc.embfile_names() if n.endswith("foto.jpg"))], "")
    doc = pymupdf.open(stream=doc.tobytes())
    assert sorted(folder_map(doc)) == ["Contratti", "Schede"]
    where = {n.split(">")[-1]: folder_of(doc, n) for n in doc.embfile_names()}
    assert where == {"contratto.pdf": "Contratti", "fattura.pdf": "Schede", "scheda.docx": "Schede", "foto.jpg": ""}
    assert {n.split(">")[-1]: doc.embfile_get(n) for n in doc.embfile_names()} == before
    assert list(moved_root.values()) == ["foto.jpg"]
    # il file iniziale (/D) segue lo spostamento
    from foliant.modules.convert.portfolio import initial_file
    assert initial_file(doc) == new_key


def test_move_in_foreign_portfolio_with_direct_filespecs(tmp_path):
    from foliant.modules.convert.portfolio import folder_of, move_files
    doc = pymupdf.open(_foreign_portfolio(tmp_path))
    before = doc.embfile_get("listino.csv")
    renamed = move_files(doc, ["listino.csv"], "Listini")
    doc = pymupdf.open(stream=doc.tobytes())
    assert folder_of(doc, renamed["listino.csv"]) == "Listini"
    assert doc.embfile_get(renamed["listino.csv"]) == before
    assert doc.embfile_info(renamed["listino.csv"])["description"] == "Listino"


def test_pdfa3_portfolio_declares_associated_files(tmp_path):
    from foliant.modules.convert.portfolio import add_files, declare_associated_files, pdfa_part
    doc = pymupdf.open(_foreign_portfolio(tmp_path))
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
                         '22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/'
                         'pdfa/ns/id/" pdfaid:part="3" pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>')
    assert pdfa_part(doc) == 3
    extra = tmp_path / "perizia.xml"
    extra.write_text("<perizia/>", encoding="utf-8")
    names = add_files(doc, [{"path": str(extra)}])
    assert declare_associated_files(doc, names) == 1
    doc = pymupdf.open(stream=doc.tobytes())
    kind, af = doc.xref_get_key(doc.pdf_catalog(), "AF")
    assert kind == "array" and af.count(" 0 R") == 1
    fs = int(af.strip("[]").split()[0])
    assert doc.xref_get_key(fs, "AFRelationship")[1] == "/Unspecified"
    ef = int(doc.xref_get_key(fs, "EF/F")[1].split()[0])
    assert doc.xref_get_key(ef, "Subtype")[1].replace("#2F", "/") in ("/application/xml", "/text/xml")
    assert doc.embfile_get(names[0]) == b"<perizia/>"


def test_protected_portfolio_encrypts_contained_files(four_files, tmp_path):
    data, _w = create_portfolio(four_files, title="Pratica riservata")
    doc = pymupdf.open(stream=data)
    out = tmp_path / "protetto.pdf"
    doc.save(str(out), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="segreta")
    raw = out.read_bytes()
    original = open(four_files[0]["path"], "rb").read()
    assert original[:200] not in raw            # il contenuto non è leggibile senza password
    back = pymupdf.open(str(out))
    assert back.needs_pass and back.authenticate("segreta")
    key = next(n for n in back.embfile_names() if n.endswith("contratto.pdf"))
    assert back.embfile_get(key) == original


def test_edit_portfolio_ui_move_rename_undo(window, four_files, tmp_path, monkeypatch, messages_portfolio=None):
    """Sposta un file e rinomina una cartella dalla finestra: un passo annullabile."""
    from PySide6.QtWidgets import QDialog

    from foliant.modules.convert.portfolio import folder_map, folder_of
    from foliant.modules.convert.portfolio_dialog import EditPortfolioDialog
    data, _w = create_portfolio(four_files, title="Pratica")
    path = tmp_path / "pratica.pdf"
    path.write_bytes(data)
    view = window.open_path(str(path))
    ctrl = window.ctx.convert_controller
    seen = {}

    def fake_exec(d):
        names = [d.table.item(r, 0).text() for r in range(d.table.rowCount())]
        seen["folders"] = {d.table.item(r, 0).text(): d.table.item(r, 1).text() for r in range(d.table.rowCount())}
        d.move_rows([names.index("foto.jpg")], "Foto")
        assert d.rename("Vendite", "Contratti") and not d.rename("Schede", "Contratti")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(EditPortfolioDialog, "exec", fake_exec)
    ctrl.edit_portfolio()
    assert seen["folders"]["fattura.pdf"] == "Vendite" and seen["folders"]["foto.jpg"] == "Schede"
    with view.document.lock:
        pdf = view.document.pdf
        where = {n.split(">")[-1]: folder_of(pdf, n) for n in pdf.embfile_names()}
    assert where == {"contratto.pdf": "Contratti", "fattura.pdf": "Contratti", "scheda.docx": "Schede",
                     "foto.jpg": "Foto"}
    assert view.document.undo()
    with view.document.lock:
        assert sorted(folder_map(view.document.pdf)) == ["Schede", "Vendite"]


def test_edit_pdfa2_portfolio_asks_before_adding(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from foliant.modules.convert.portfolio_dialog import EditPortfolioDialog
    doc = pymupdf.open(_foreign_portfolio(tmp_path))
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
                         '22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/'
                         'pdfa/ns/id/"><pdfaid:part>2</pdfaid:part></rdf:Description></rdf:RDF></x:xmpmeta>')
    path = tmp_path / "a2.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    extra = tmp_path / "nota.txt"
    extra.write_text("x", encoding="utf-8")
    monkeypatch.setattr(EditPortfolioDialog, "exec", lambda d: d.add_paths([str(extra)]) or QDialog.DialogCode.Accepted)
    asked = []
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: asked.append(m) or False)
    assert window.ctx.convert_controller.edit_portfolio() is None
    assert asked and "PDF/A-2" in asked[0]
    with view.document.lock:
        assert "nota.txt" not in view.document.pdf.embfile_names()
