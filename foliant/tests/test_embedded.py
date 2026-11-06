"""Allegati: cancellazione sicura (foliant.core.embedded). Collaudo B-2 e B-3: `embfile_del` del
motore su alcuni alberi cancellava l'allegato sbagliato."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.core.embedded import clear_embedded, delete_embedded


def _files(tmp_path):
    def pdf(name, text):
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), text)
        path = tmp_path / name
        d.save(str(path))
        return path
    out = [pdf("perizia.pdf", "Perizia Omega"), pdf("contratto è.pdf", "Contratto")]
    (tmp_path / "preventivo.xlsx").write_bytes(b"PK\x03\x04 preventivo finto")
    (tmp_path / "foto.jpg").write_bytes(b"\xff\xd8\xff foto finta")
    return out + [tmp_path / "preventivo.xlsx", tmp_path / "foto.jpg"]


def test_portfolio_remove_deletes_the_chosen_file(tmp_path):
    from foliant.modules.convert.portfolio import create_portfolio, remove_files
    paths = _files(tmp_path)
    data, _w = create_portfolio([{"path": str(p)} for p in paths])
    doc = pymupdf.open(stream=data)
    before = {n: doc.embfile_get(n) for n in doc.embfile_names()}
    target = next(n for n in before if "preventivo" in n)
    remove_files(doc, [target])
    doc = pymupdf.open(stream=doc.tobytes(garbage=3))
    assert sorted(doc.embfile_names()) == sorted(n for n in before if n != target)
    assert all(doc.embfile_get(n) == before[n] for n in doc.embfile_names())


def test_delete_keeps_others_and_af_and_rejects_unknown(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    for i, name in enumerate(["a.pdf", "b.jpg", "c.pdf", "d.xlsx"]):
        doc.embfile_add(name, f"contenuto {i}".encode(), filename=name)
    delete_embedded(doc, ["b.jpg", "d.xlsx"])
    assert sorted(doc.embfile_names()) == ["a.pdf", "c.pdf"]
    assert doc.embfile_get("c.pdf") == b"contenuto 2"
    with pytest.raises(KeyError):
        delete_embedded(doc, "nessuno.pdf")
    clear_embedded(doc)
    assert doc.embfile_names() == []


def test_pdfa2_keeps_pdf_attachments(tmp_path):
    from foliant.core import platform
    if not platform.find_tool("gs"):
        pytest.skip("Ghostscript non disponibile")
    from foliant.modules.convert.engine import convert_pdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Perizia con allegati")
    for name in ("a_certificato.pdf", "c_garanzia.pdf"):
        att = pymupdf.open()
        att.new_page().insert_text((72, 72), name)
        doc.embfile_add(name, att.tobytes(), filename=name)
    doc.embfile_add("b_foto.jpg", b"\xff\xd8\xff foto", filename="b_foto.jpg")
    doc.embfile_add("d_ricevuta.xlsx", b"PK\x03\x04 ricevuta", filename="d_ricevuta.xlsx")
    src = tmp_path / "perizia.pdf"
    doc.save(str(src))
    out = convert_pdf(str(src), tmp_path / "perizia_a.pdf", "pdfa-2b")[0]
    names = sorted(pymupdf.open(str(out)).embfile_names())
    assert names == ["a_certificato.pdf", "c_garanzia.pdf"], names
