"""Salvataggio incrementale dei documenti firmati e rilevamento delle modifiche al contenuto."""

from __future__ import annotations


import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.sign import digital
from foliant.modules.sign.incremental import changes_content, incremental_update


def _signed(id_a, objstm: bool, encrypt: bool = False) -> bytes:
    d = pymupdf.open()
    for i in range(2):
        p = d.new_page()
        p.insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
        p.insert_image(pymupdf.Rect(72, 200, 172, 300), pixmap=pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20), 0))
    kw = {"use_objstms": 1} if objstm else {}
    if encrypt:
        kw.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="own", user_pw="")
    data = d.tobytes(**kw)
    if encrypt:
        return data
    return digital.sign_pdf(data, id_a[2], digital.SignOptions())


@pytest.mark.parametrize("objstm", [False, True])
@pytest.mark.parametrize("undo_redo", [False, True])
def test_annotation_keeps_signature(id_a, objstm, undo_redo):
    signed = _signed(id_a, objstm)
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Nota") as pdf:
        pdf[1].add_text_annot((100, 100), "ciao")
    if undo_redo:
        doc.undo()
        doc.redo()
    with doc.lock:
        out = incremental_update(signed, doc.pdf)
        assert not changes_content(signed, doc.pdf)
    assert out.startswith(signed)
    r = pymupdf.open(stream=out)
    assert r.page_count == 2 and len(list(r[1].annots())) == 1
    st = digital.verify_pdf(out, trusted=[id_a[1]])
    assert st[0].status == "valida" and "consentite" in st[0].modifications


def test_metadata_change_is_saved_and_detected(id_a):
    signed = _signed(id_a, True)
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Titolo") as pdf:
        pdf.set_metadata({"title": "Nuovo titolo"})
    with doc.lock:
        out = incremental_update(signed, doc.pdf)
        assert changes_content(signed, doc.pdf)
    assert pymupdf.open(stream=out).metadata["title"] == "Nuovo titolo"


def test_image_replaced_in_place_is_content_change(id_a):
    signed = _signed(id_a, False)
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Immagine") as pdf:
        xref = pdf[0].get_images()[0][0]
        pdf[0].replace_image(xref, pixmap=pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20), 1))
    with doc.lock:
        assert changes_content(signed, doc.pdf)


def test_encrypted_undo_redo_does_not_rewrite_streams(id_a):
    data = _signed(id_a, True, encrypt=True)
    doc = Document.from_bytes(data, modified=False)
    with doc.change("Nota") as pdf:
        pdf[0].add_text_annot((100, 100), "ciao")
    doc.undo()
    doc.redo()
    with doc.lock:
        assert not changes_content(data, doc.pdf)
        out = incremental_update(data, doc.pdf)
    r = pymupdf.open(stream=out)
    assert r.needs_pass is False or r.authenticate("")
    assert "Pagina 1" in r[0].get_text() and len(list(r[0].annots())) == 1


def test_no_changes_returns_original(id_a):
    signed = _signed(id_a, False)
    doc = Document.from_bytes(signed, modified=False)
    with doc.lock:
        assert incremental_update(signed, doc.pdf) == signed
