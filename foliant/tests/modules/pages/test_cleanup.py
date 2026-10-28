"""Test della pulizia guidata (PAG-41)."""

from __future__ import annotations

import socket

import numpy as np
import pymupdf
import pytest

from foliant.modules.pages import cleanup
from foliant.modules.pages.engine import PAPER_SIZES

A4 = PAPER_SIZES["A4"]
LETTER = PAPER_SIZES["Letter"]


def body(page, rotate=0, n=1):
    for k in range(8):
        y = 120 + 22 * k
        if rotate == 180:
            page.insert_text((page.rect.width - 72, page.rect.height - y),
                             f"Contratto di vendita orologio numero {n}, riga {k}.", fontsize=11, rotate=180)
        elif rotate == 90:
            page.insert_text((y, page.rect.height - 72), f"Fattura orologio riga {k} pagina {n}.", fontsize=11,
                             rotate=90)
        else:
            page.insert_text((72, y), f"Contratto di vendita orologio numero {n}, riga {k}.", fontsize=11)


def scan_png(w=600, h=850):
    rng = np.random.default_rng(1)
    arr = np.full((h, w), 250, dtype=np.uint8)
    for k in range(25):                     # «righe» scure come in un documento scansionato
        y = 80 + 28 * k
        arr[y:y + 9, 60:540] = rng.integers(0, 90, size=(9, 480), dtype=np.uint8)
    return pymupdf.Pixmap(pymupdf.csGRAY, w, h, arr.tobytes(), False).tobytes("png")


def cleanup_doc():
    """Pagina vuota, pagina capovolta, una Letter tra A4, una scansione senza testo."""
    doc = pymupdf.open()
    body(doc.new_page(width=A4[0], height=A4[1]), n=1)
    doc.new_page(width=A4[0], height=A4[1])                            # 2: vuota
    body(doc.new_page(width=A4[0], height=A4[1]), rotate=180, n=3)      # 3: capovolta
    body(doc.new_page(width=LETTER[0], height=LETTER[1]), n=4)          # 4: Letter
    p = doc.new_page(width=A4[0], height=A4[1])                        # 5: scansione senza testo
    p.insert_image(p.rect, stream=scan_png())
    body(doc.new_page(width=A4[0], height=A4[1]), n=6)
    return doc


def test_four_proposals_in_their_categories():
    res = cleanup.analyze(cleanup_doc(), use_tesseract=False)
    assert [p.page for p in res["blank"]] == [1]
    assert [(p.page, p.params["degrees"]) for p in res["orientation"]] == [(2, 180)]
    assert [p.page for p in res["sizes"]] == [3] and res["sizes"][0].params["ref"] == 0
    assert [p.page for p in res["ocr"]] == [4]
    assert res["annotations"] == []


def test_apply_all_is_one_undo_step(tmp_path):
    from foliant.api import Document
    doc = cleanup_doc()
    path = tmp_path / "pulizia.pdf"
    doc.save(str(path))
    document = Document.open(str(path))
    before = document.to_bytes()
    with document.change("Pulizia guidata") as pdf:
        res = cleanup.analyze(pdf, use_tesseract=False)
        done = cleanup.apply_proposals(pdf, [p for ps in res.values() for p in ps])
    assert done["deleted"] == 1 and done["rotated"] == 1 and done["resized"] == 1
    pdf = document.pdf
    assert pdf.page_count == 5
    mb = pdf.xref_get_key(pdf[0].xref, "MediaBox")[1]
    assert all(pdf.xref_get_key(pdf[i].xref, "MediaBox")[1] == mb for i in range(5))
    assert pdf[1].rotation == 180
    assert document.undo()
    assert document.to_bytes() == before or document.pdf.page_count == 6
    assert document.pdf.page_count == 6 and document.pdf[2].rotation == 0
    assert not document.can_undo


def test_rotation_90_and_low_confidence_and_existing_rotate():
    doc = pymupdf.open()
    p = doc.new_page()
    body(p, rotate=90)
    res = cleanup.analyze(doc, checks=["orientation"], use_tesseract=False)
    assert [pr.params["degrees"] for pr in res["orientation"]] == [90]
    cleanup.apply_proposals(doc, res["orientation"])
    d = doc[0].get_text("dict")["blocks"][0]["lines"][0]["dir"]
    shown = pymupdf.Point(d) * doc[0].rotation_matrix - pymupdf.Point(0, 0) * doc[0].rotation_matrix
    assert round(shown.x) == 1 and round(shown.y) == 0          # ora il testo appare orizzontale
    # testo misto (tabella ruotata): confidenza bassa → nessuna proposta
    doc = pymupdf.open()
    p = doc.new_page()
    body(p)
    body(p, rotate=90)
    assert cleanup.analyze(doc, checks=["orientation"], use_tesseract=False)["orientation"] == []
    # pagina già raddrizzata con /Rotate: nessuna proposta
    doc = pymupdf.open()
    p = doc.new_page()
    body(p, rotate=180)
    p.set_rotation(180)
    assert cleanup.analyze(doc, checks=["orientation"], use_tesseract=False)["orientation"] == []


@pytest.mark.skipif(not cleanup.tesseract_available(), reason="tesseract non installato")
def test_scan_upside_down_with_tesseract():
    src = pymupdf.open()
    p = src.new_page()
    for k in range(25):
        p.insert_text((60, 80 + 26 * k), "The watch dealer signed the purchase contract today.", fontsize=14)
    png = p.get_pixmap(dpi=150).tobytes("png")
    doc = pymupdf.open()
    q = doc.new_page()
    q.insert_image(q.rect, stream=png, rotate=180)
    res = cleanup.analyze(doc, checks=["orientation"], use_tesseract=True)
    assert [pr.params["degrees"] for pr in res["orientation"]] == [180]


def test_annotations_list_export_remove(tmp_path):
    import json
    doc = pymupdf.open()
    p = doc.new_page()
    body(p)
    p.add_text_annot((100, 100), "verificare la referenza")
    p.add_highlight_annot(p.search_for("Contratto")[0])
    res = cleanup.analyze(doc, checks=["annotations"])
    assert len(res["annotations"]) == 2 and not any(pr.accepted for pr in res["annotations"])
    out = tmp_path / "annotazioni.json"
    assert cleanup.export_annotations_json(doc, str(out)) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["annotations"][0]["content"] == "verificare la referenza"
    res["annotations"][0].accepted = True
    assert cleanup.apply_proposals(doc, res["annotations"])["annotations"] == 1
    assert len(list(doc[0].annots())) == 1


def test_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("connessione di rete tentata")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    res = cleanup.analyze(cleanup_doc())
    assert sum(len(v) for v in res.values()) >= 4


def test_cannot_delete_everything_and_permissions():
    doc = pymupdf.open()
    doc.new_page()
    res = cleanup.analyze(doc)
    with pytest.raises(cleanup.PageOpError):
        cleanup.apply_proposals(doc, res["blank"])
    assert cleanup.can_modify(doc)
