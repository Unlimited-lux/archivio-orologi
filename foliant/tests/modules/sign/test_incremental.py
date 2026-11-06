"""Salvataggio incrementale (del nucleo) dei documenti firmati: le firme restano valide."""

from __future__ import annotations


import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.sign import digital
from foliant.core.incremental import incremental_update


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
    assert pymupdf.open(stream=out).metadata["title"] == "Nuovo titolo"


def test_encrypted_undo_redo_does_not_rewrite_streams(id_a):
    data = _signed(id_a, True, encrypt=True)
    doc = Document.from_bytes(data, modified=False)
    with doc.change("Nota") as pdf:
        pdf[0].add_text_annot((100, 100), "ciao")
    doc.undo()
    doc.redo()
    with doc.lock:
        out = incremental_update(data, doc.pdf)
    r = pymupdf.open(stream=out)
    assert r.needs_pass is False or r.authenticate("")
    assert "Pagina 1" in r[0].get_text() and len(list(r[0].annots())) == 1


def test_no_changes_returns_original(id_a):
    signed = _signed(id_a, False)
    doc = Document.from_bytes(signed, modified=False)
    with doc.lock:
        assert incremental_update(signed, doc.pdf) == signed


# --------------------------------------------------------------------------- #
# Testo originale dei valori invariati (F-1: MediaBox di LibreOffice)
# --------------------------------------------------------------------------- #


def test_keep_spans_only_unchanged_imprecise_values():
    from foliant.core.original_text import keep_spans

    raw = ("<</Type/Page/MediaBox[0 0 595.303937007874 841.889763779528]/Rotate 0"
           "/Resources<</Font<</F1 5 0 R>>/X[1.123456789 2]>>/Annots[4 0 R]/UserUnit 1.0>>")
    old = ("<</Type/Page/MediaBox[0 0 595.30398 841.8898]/Rotate 0"
           "/Resources<</Font<</F1 5 0 R>>/X[1.1234568 2]>>/Annots[4 0 R]/UserUnit 1>>")
    cur = ("<</Type/Page/MediaBox[0 0 595.30398 841.8898]/Rotate 0"
           "/Resources<</Font<</F1 5 0 R/F2 9 0 R>>/X[1.1234568 2]>>/Annots[4 0 R 8 0 R]/UserUnit 1>>")
    spans = keep_spans(raw, old, cur)
    got = {cur[a:b]: text for a, b, text in spans}
    # MediaBox e /X (dentro /Resources, cambiato) tornano al testo originale; /UserUnit 1.0 = 1
    assert got == {"[0 0 595.30398 841.8898]": "[0 0 595.303937007874 841.889763779528]",
                   "[1.1234568 2]": "[1.123456789 2]"}
    # un valore cambiato davvero resta quello nuovo
    changed = cur.replace("[0 0 595.30398 841.8898]", "[0 0 600 800]")
    assert "[0 0 600 800]" not in {changed[a:b] for a, b, _t in keep_spans(raw, old, changed)}
    # stringhe nei file cifrati: mai dal testo del file
    raw_s, old_s = "<</A[(x) 0.123456789]/B 1>>", "<</A[(x) .12345679]/B 1>>"
    cur_s = "<</A[(x) .12345679]/B 2>>"
    assert keep_spans(raw_s, old_s, cur_s) and not keep_spans(raw_s, old_s, cur_s, encrypted=True)


def test_keep_text_falls_back_to_engine_text(id_a, monkeypatch):
    """Se i segnaposto non si ritrovano, il salvataggio riesce comunque con il testo del motore."""
    from foliant.core import original_text
    from tests.modules.sign.test_libreoffice import _lo_like_pdf

    signed = digital.sign_pdf(_lo_like_pdf(), id_a[2], digital.SignOptions())
    monkeypatch.setattr(original_text, "fill_placeholders", lambda data, start, repl: None)
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Nota") as pdf:
        pdf[0].add_text_annot((100, 100), "ciao")
    with doc.lock:
        out = incremental_update(signed, doc.pdf)
    assert out.startswith(signed) and b"FoliantKeep" not in out
    assert b"595.30398" in out[len(signed):]
    r = pymupdf.open(stream=out)
    assert len(list(r[0].annots())) == 1
