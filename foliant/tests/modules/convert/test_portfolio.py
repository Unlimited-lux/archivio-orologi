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
