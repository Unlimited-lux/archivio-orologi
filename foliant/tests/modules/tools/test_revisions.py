"""Revisioni di un PDF con aggiornamenti incrementali (CMP-14)."""

from __future__ import annotations

import pymupdf

from foliant.modules.tools import compare_engine as ce
from foliant.modules.tools import revisions as rv

from .conftest import page_lines


def _incremental(tmp_path, pdf_builder):
    path = pdf_builder([page_lines(1), page_lines(2)])
    doc = pymupdf.open(path)
    annot = doc[1].add_text_annot((200, 300), "Verificato dal perito")
    annot.update()
    doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


def test_two_revisions_and_comparison(tmp_path, pdf_builder):
    path = _incremental(tmp_path, pdf_builder)
    data = open(path, "rb").read()
    revs = rv.list_revisions(data)
    assert [r.number for r in revs] == [1, 2]
    first = rv.revision_bytes(data, revs[0])
    assert data.startswith(first) and len(first) < len(data)
    r = ce.run_compare(None, ce.SideInput(first, "Revisione 1"), ce.SideInput(rv.revision_bytes(data, revs[1]),
                                                                              "Revisione 2"),
                       ce.CompareOptions(dpi=72))
    assert [(d.kind, d.subtype) for d in r.differences] == [(ce.ANNOTATION, "added")]
    assert r.differences[0].text_b == "Verificato dal perito"


def test_single_revision(pdf_builder):
    path = pdf_builder([page_lines(1)])
    revs = rv.list_revisions(open(path, "rb").read())
    assert len(revs) == 1
    assert rv.signed_revision(open(path, "rb").read()) is None


def test_signed_revision_by_byte_range(tmp_path, pdf_builder):
    path = _incremental(tmp_path, pdf_builder)
    data = open(path, "rb").read()
    revs = rv.list_revisions(data)
    end = revs[0].end
    # simula una firma che copre la prima revisione
    fake = data + b"\n% /ByteRange [0 10 20 " + str(end - 20).encode() + b"]\n"
    sig = rv.signed_revision(fake)
    assert sig is not None and sig.number == 1


def test_dialog(window, tmp_path, pdf_builder, monkeypatch):
    path = _incremental(tmp_path, pdf_builder)
    view = window.open_path(path)
    dlg = rv.RevisionsDialog(window.ctx, view)
    assert dlg.list.count() == 2 and dlg.compare_button.isEnabled()
    assert not dlg.signed_button.isEnabled()
    widget = dlg.compare()
    assert widget is not None
    assert any(d.kind == ce.ANNOTATION for d in widget.result.differences)
    single = window.open_path(pdf_builder([page_lines(3)]))
    dlg2 = rv.RevisionsDialog(window.ctx, single)
    assert not dlg2.compare_button.isEnabled() and "una sola revisione" in dlg2.message.text()
