"""Rapporto del confronto in PDF (CMP-10)."""

from __future__ import annotations

import hashlib

import pymupdf

from foliant.modules.tools import compare_engine as ce
from foliant.modules.tools import report

from .conftest import page_lines, read


def _result(pdf_builder, **opts):
    a = pdf_builder([page_lines(1), ["Il canone è di 100 euro", "riga da togliere", "fine pagina"], page_lines(3)])
    b = pdf_builder([page_lines(1), ["Il canone è di 120 euro", "fine pagina", "riga nuova aggiunta qui"], page_lines(3)],
                    extra=lambda p, i: i == 2 and p.draw_rect(pymupdf.Rect(300, 400, 340, 440), fill=(0, 0, 0)))
    r = ce.run_compare(None, ce.SideInput(read(a), "a.pdf", a), ce.SideInput(read(b), "b.pdf", b),
                       ce.CompareOptions(dpi=72, compare_format=True, **opts))
    return a, b, r


def test_report_summary_counts_and_links(pdf_builder):
    a, b, r = _result(pdf_builder)
    ha, hb = hashlib.sha256(read(a)).hexdigest(), hashlib.sha256(read(b)).hexdigest()
    data = report.build_report(None, r, read(a), read(b))
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = doc[0].get_text()
    assert "Rapporto di confronto" in text
    counts = r.counts()
    assert f"Differenze: {len(r.differences)}" in text
    assert f"{counts[ce.MODIFIED]} modifiche" in text
    assert ha in text and hb in text
    links = [l for l in doc[0].get_links() if l["kind"] == pymupdf.LINK_GOTO]
    assert len(links) == len(r.differences)
    for link, d in zip(links, r.differences):
        target = doc[link["page"]]
        assert d.page_label("b") in target.get_text()
    # A e B invariati, PDF strutturalmente valido
    assert hashlib.sha256(read(a)).hexdigest() == ha and hashlib.sha256(read(b)).hexdigest() == hb
    assert doc.get_toc()[0][1] == "Riepilogo"
    assert not doc.is_repaired


def test_report_filters_exclude_kind(pdf_builder):
    a, b, r = _result(pdf_builder)
    visible = [d for d in r.differences if d.kind != ce.VISUAL]
    data = report.build_report(None, r, read(a), read(b), visible, filters_note="Filtro: visive nascoste")
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = doc[0].get_text()
    assert "0 visive" in text and "Filtro: visive nascoste" in text


def test_report_no_differences(pdf_builder):
    a = pdf_builder([page_lines(1)])
    r = ce.run_compare(None, ce.SideInput(read(a), "a"), ce.SideInput(read(a), "a"))
    data = report.build_report(None, r, read(a), read(a))
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert doc.page_count == 1
    assert "Nessuna differenza" in doc[0].get_text()


def test_report_layout_b_only_and_all(pdf_builder):
    a, b, r = _result(pdf_builder)
    one = pymupdf.open(stream=report.build_report(None, r, read(a), read(b), layout=report.LAYOUT_B), filetype="pdf")
    assert one[1].rect.width < one[1].rect.height
    every = pymupdf.open(stream=report.build_report(None, r, read(a), read(b), content=report.CONTENT_ALL),
                         filetype="pdf")
    assert every.page_count == 1 + 3


def test_annotated_b(pdf_builder):
    a, b, r = _result(pdf_builder)
    data, n = report.build_annotated_b(None, r, read(b))
    textual = [d for d in r.differences if d.kind in report.TEXT_KINDS and d.subtype == "text"]
    doc = pymupdf.open(stream=data, filetype="pdf")
    annots = [an for page in doc for an in page.annots()]
    assert n == len(textual) == len(annots)
    deleted = [d for d in textual if d.kind == ce.DELETED]
    assert deleted
    contents = [an.info["content"] for an in annots]
    for d in deleted:
        assert any(d.text_a in c for c in contents)


def test_suggested_name(pdf_builder):
    a, b, r = _result(pdf_builder)
    from datetime import datetime
    assert report.suggested_name(r, datetime(2026, 10, 28)) == "Confronto_a_b_20261028.pdf"
