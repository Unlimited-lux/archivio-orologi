"""Timbri su pagine che condividono lo stesso dizionario /Resources (PDF di LibreOffice e Office, e
ogni PDF salvato con l'unione degli oggetti uguali): ogni pagina deve avere il suo timbro."""

from __future__ import annotations

import re

import pymupdf

from foliant.modules.pages import stamp


def _shared(n=3):
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 100), f"Contratto pagina {i + 1}", fontsize=14)
    doc = pymupdf.open(stream=doc.tobytes(garbage=3))         # risorse uguali → un solo oggetto
    assert len({doc.xref_get_key(doc[i].xref, "Resources") for i in range(n)}) == 1
    return doc


def _reopen(doc):
    return pymupdf.open(stream=doc.tobytes(garbage=3))


def test_page_numbers_differ_on_shared_resources():
    doc = _shared()
    stamp.add_page_numbers(doc, None, fmt="{pagina}/{totale}")
    doc = _reopen(doc)
    assert [doc[i].get_text().split()[-1] for i in range(3)] == ["1/3", "2/3", "3/3"]


def test_bates_numbers_differ_on_shared_resources():
    doc = _shared()
    stamp.add_bates(doc, None, prefix="ORO-", digits=6)
    doc = _reopen(doc)
    assert [f"ORO-00000{i + 1}" in doc[i].get_text() for i in range(3)] == [True, True, True]


def test_removing_a_watermark_from_one_page_keeps_the_others():
    doc = _shared()
    stamp.add_watermark(doc, None, text="BOZZA")
    doc = _reopen(doc)
    stamp.remove_watermarks(doc, [0])
    doc = _reopen(doc)
    assert ["BOZZA" in doc[i].get_text() for i in range(3)] == [False, True, True]
    for i in range(3):                                         # nessun riferimento orfano
        for x in doc[i].get_contents():
            for name in re.findall(rb"/(FolStamp_\w+) Do", doc.xref_stream(x) or b""):
                assert doc.xref_get_key(doc[i].xref, f"Resources/XObject/{name.decode()}")[0] != "null"


def test_inherited_resources_are_copied_not_shared():
    doc = _shared(2)
    pages_xref = int(doc.xref_get_key(doc[0].xref, "Parent")[1].split()[0])
    res = doc.xref_get_key(doc[0].xref, "Resources")[1]
    doc.xref_set_key(pages_xref, "Resources", res)
    for i in range(2):
        doc.xref_set_key(doc[i].xref, "Resources", "null")
    doc = _reopen(doc)
    stamp.add_page_numbers(doc, None, fmt="{pagina}")
    doc = _reopen(doc)
    assert [doc[i].get_text().split()[-1] for i in range(2)] == ["1", "2"]
    assert "Contratto pagina 2" in doc[1].get_text()
