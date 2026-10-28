"""ANN-01: vincoli dei documenti PDF/A e struttura logica dei documenti taggati."""

import re

import pymupdf
import pytest

from foliant.modules.annotations import engine

from .conftest import annots, saved

XMP = ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
       '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
       'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="{part}" pdfaid:conformance="B"/>'
       '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


def _make(tmp_path, name, part=None, tagged=False):
    path = tmp_path / name
    d = pymupdf.open()
    page = d.new_page(width=595, height=842)
    page.insert_text((72, 100), "Contratto di fornitura", fontsize=12)
    if part:
        d.set_xml_metadata(XMP.format(part=part))
    if tagged:
        cat = d.pdf_catalog()
        root, doc_el, tree = d.get_new_xref(), d.get_new_xref(), d.get_new_xref()
        d.update_object(tree, "<</Nums[]>>")
        d.update_object(doc_el, f"<</Type/StructElem/S/Document/P {root} 0 R/K[]>>")
        d.update_object(root, f"<</Type/StructTreeRoot/K {doc_el} 0 R/ParentTree {tree} 0 R/ParentTreeNextKey 3>>")
        d.xref_set_key(cat, "StructTreeRoot", f"{root} 0 R")
        d.xref_set_key(cat, "MarkInfo", "<</Marked true>>")
    d.save(str(path))
    return str(path)


def _open(window, module, path):
    v = window.open_path(path)
    window.set_mode("commenta")
    return v


FORBIDDEN = (pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_INVISIBLE | pymupdf.PDF_ANNOT_IS_NO_VIEW
             | pymupdf.PDF_ANNOT_IS_TOGGLE_NO_VIEW)


def test_pdfa2b_shape_and_reply(comments, tmp_path):
    window, _view, module = comments
    v = _open(window, module, _make(tmp_path, "a2b.pdf", part="2"))
    ctrl = module.ctrl
    rect = ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(100, 200), (250, 300)], engine.Style(), "A", view=v)
    ctrl.select([(0, rect)], v)
    reply = ctrl.apply("r", engine.add_reply, 0, rect, "Visto", "B", view=v)
    doc = saved(window, v, tmp_path)
    for a in annots(doc, 0):
        f = a.flags
        assert f & pymupdf.PDF_ANNOT_IS_PRINT and not f & FORBIDDEN, (a.type, f)
        if a.type[0] != pymupdf.PDF_ANNOT_POPUP:
            ap = doc.xref_get_key(a.xref, "AP")[1]
            assert re.fullmatch(r"<<\s*/N\s+\d+\s+0\s+R\s*>>", ap), ap
    # la risposta non si vede: aspetto vuoto; il clic sul rettangolo seleziona il rettangolo
    assert doc.xref_stream(int(doc.xref_get_key(reply, "AP/N")[1].split()[0])) == b""
    assert engine.annot_at(v.document.pdf, 0, pymupdf.Point(180, 250)) == rect
    info = engine.annotation(v.document.pdf, 0, rect)
    assert [r.contents for r in info.replies] == ["Visto"]


def test_pdfa1_opacity_forced_with_warning(comments, tmp_path, monkeypatch):
    window, _view, module = comments
    v = _open(window, module, _make(tmp_path, "a1.pdf", part="1"))
    notes = []
    monkeypatch.setattr(window.ctx, "status", lambda m, *a: notes.append(m))
    x = module.ctrl.apply("h", engine.create_shape, 0, engine.RECT, [(100, 200), (250, 300)],
                          engine.Style(opacity=0.4, fill=(1, 1, 0)), "A", view=v)
    doc = saved(window, v, tmp_path)
    assert float(doc.xref_get_key(x, "CA")[1]) == 1.0
    assert any("PDF/A-1" in n for n in notes)


def test_tagged_note_in_structure(comments, tmp_path):
    window, _view, module = comments
    v = _open(window, module, _make(tmp_path, "tag.pdf", tagged=True))
    ctrl = module.ctrl
    note = ctrl.apply("n", engine.create_note, 0, (300, 300), "Verificare la clausola", engine.Style(), "A", view=v)
    doc = saved(window, v, tmp_path)
    page = doc[0]
    assert doc.xref_get_key(page.xref, "Tabs")[1] == "/S"
    key = int(doc.xref_get_key(note, "StructParent")[1])
    root = int(doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    tree = int(doc.xref_get_key(root, "ParentTree")[1].split()[0])
    m = re.search(rf"(?<!\d){key}\s+(\d+)\s+0\s+R", doc.xref_get_key(tree, "Nums")[1])
    elem = int(m.group(1))
    assert doc.xref_get_key(elem, "S")[1] == "/Annot"
    assert int(doc.xref_get_key(elem, "K/Obj")[1].split()[0]) == note
    assert "Verificare la clausola" in doc.xref_get_key(elem, "Alt")[1]
    doc_el = int(doc.xref_get_key(root, "K")[1].split()[0])
    assert f"{elem} 0 R" in doc.xref_get_key(doc_el, "K")[1]
    assert int(doc.xref_get_key(root, "ParentTreeNextKey")[1]) == key + 1
    # eliminata la nota, l'elemento esce dalla struttura
    ctrl.select([(0, note)], v)
    ctrl.delete()
    doc = saved(window, v, tmp_path, "dopo.pdf")
    assert f"{elem} 0 R" not in doc.xref_get_key(doc_el, "K")[1]
    assert not re.search(rf"(?<!\d){key}\s+{elem}\s+0\s+R", doc.xref_get_key(tree, "Nums")[1])


def test_plain_document_untouched():
    pdf = pymupdf.open()
    pdf.new_page()
    x = engine.create_shape(pdf, 0, engine.RECT, [(10, 10), (50, 50)], engine.Style(opacity=0.5), "A")
    assert engine.conform(pdf) == engine.ConformResult()
    assert pdf.xref_get_key(x, "StructParent")[0] == "null"
