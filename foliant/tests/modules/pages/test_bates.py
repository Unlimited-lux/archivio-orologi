"""Test del motore Bates multi-file (PAG-35)."""

from __future__ import annotations

import csv

import pymupdf
import pytest

from foliant.api import OperationCancelled, Progress
from foliant.modules.pages import bates, stamp
from foliant.modules.pages.engine import PageOpError


def _pdf(path, pages, label="Doc"):
    doc = pymupdf.open()
    for k in range(pages):
        doc.new_page(width=595, height=842).insert_text((72, 100), f"{label} pagina {k + 1}", fontsize=20)
    doc.save(str(path))
    doc.close()
    return str(path)


def _codes(path):
    doc = pymupdf.open(path)
    try:
        out = []
        for page in doc:
            words = [w for w in page.get_text().split() if w.startswith("ABC")]
            out.append(words[0] if words else None)
        return out
    finally:
        doc.close()


@pytest.fixture
def ab(tmp_path):
    return _pdf(tmp_path / "A.pdf", 3, "A"), _pdf(tmp_path / "B.pdf", 2, "B")


def test_continuous_numbering_in_place(ab):
    a, b = ab
    rep = bates.bates_files([(a, None), (b, None)], prefix="ABC", digits=6, start=1, continuous=True)
    assert _codes(a) == ["ABC000001", "ABC000002", "ABC000003"]
    assert _codes(b) == ["ABC000004", "ABC000005"]
    assert [(r.first_code, r.last_code) for r in rep.done] == [("ABC000001", "ABC000003"), ("ABC000004", "ABC000005")]
    assert rep.next_number == 6 and not rep.cancelled and not rep.skipped


def test_restart_per_document(ab):
    a, b = ab
    bates.bates_files([a, b], prefix="ABC", digits=6, continuous=False)
    assert _codes(a) == ["ABC000001", "ABC000002", "ABC000003"]
    assert _codes(b) == ["ABC000001", "ABC000002"]


def test_csv_log(ab, tmp_path):
    a, b = ab
    log = tmp_path / "registro.csv"
    rep = bates.bates_files([a, b], prefix="ABC", digits=6, log_csv=log)
    assert rep.log_path == str(log)
    with open(log, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 5
    assert [(r["file"], r["pagina"], r["numero"]) for r in rows] == [
        ("A.pdf", "1", "ABC000001"), ("A.pdf", "2", "ABC000002"), ("A.pdf", "3", "ABC000003"),
        ("B.pdf", "1", "ABC000004"), ("B.pdf", "2", "ABC000005")]


def test_removal_restores_rendering(ab):
    a, _b = ab
    before = pymupdf.open(a)
    ref = [p.get_pixmap(dpi=50).samples for p in before]
    before.close()
    bates.bates_files([a], prefix="ABC")
    stamped = pymupdf.open(a)
    assert stamped[0].get_pixmap(dpi=50).samples != ref[0]
    stamp.remove_watermarks(stamped, kind="bates")
    assert [p.get_pixmap(dpi=50).samples for p in stamped] == ref
    stamped.close()
    # anche dal file
    assert bates.remove_bates_files([a]) == [a]
    doc = pymupdf.open(a)
    assert [p.get_pixmap(dpi=50).samples for p in doc] == ref


def test_copies_with_name_template(ab, tmp_path):
    a, b = ab
    out = tmp_path / "out"
    rep = bates.bates_files([a, b], prefix="ABC", out_folder=out, name_template=bates.DEFAULT_TEMPLATE)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["A_ABC000001-ABC000003.pdf", "B_ABC000004-ABC000005.pdf"]
    assert rep.done[0].output == str(out / "A_ABC000001-ABC000003.pdf")
    assert _codes(a) == [None, None, None]          # originali intatti
    assert _codes(str(out / "B_ABC000004-ABC000005.pdf")) == ["ABC000004", "ABC000005"]


def test_digits_overflow_checked_before_start(tmp_path):
    a = _pdf(tmp_path / "A.pdf", 3)
    b = _pdf(tmp_path / "B.pdf", 3)
    with pytest.raises(bates.BatesDigitsError) as ei:
        bates.bates_files([a, b], prefix="ABC", start=996, digits=3)
    assert ei.value.required_digits == 4
    assert "4" in str(ei.value)
    assert _codes(a) == [None, None, None]          # nessun file toccato
    with pytest.raises(PageOpError):
        bates.bates_files([a], digits=2)
    with pytest.raises(PageOpError):
        bates.bates_files([a], digits=16)
    with pytest.raises(PageOpError):
        bates.bates_files([])


def test_encrypted_skipped_or_with_password(tmp_path):
    a = _pdf(tmp_path / "A.pdf", 2)
    enc = tmp_path / "E.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato")
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="prop")
    doc.close()
    rep = bates.bates_files([a, str(enc)], prefix="ABC")
    assert len(rep.done) == 1 and len(rep.skipped) == 1 and "E.pdf" in rep.skipped[0][1]
    with pytest.raises(PageOpError):                 # solo file saltati → niente da numerare
        bates.bates_files([(str(enc), "sbagliata")], prefix="ABC")
    rep = bates.bates_files([(str(enc), "segreta")], prefix="ABC", start=10)
    assert rep.done and not rep.skipped
    d = pymupdf.open(str(enc))
    assert d.needs_pass                              # protezione mantenuta
    assert d.authenticate("segreta")
    assert "ABC000010" in d[0].get_text()


def test_existing_bates_replace_or_refuse(ab):
    a, b = ab
    bates.bates_files([a], prefix="ABC")
    with pytest.raises(bates.BatesExistsError) as ei:
        bates.bates_files([a, b], prefix="ABC", start=100)
    assert ei.value.paths == [a]
    assert _codes(b) == [None, None]
    bates.bates_files([a, b], prefix="ABC", start=100, replace=True)
    assert _codes(a) == ["ABC000100", "ABC000101", "ABC000102"]
    doc = pymupdf.open(a)
    assert sum(1 for w in doc[0].get_text().split() if w.startswith("ABC")) == 1   # nessun doppione


def test_cancel_reports_completed_and_next_number(ab):
    a, b = ab
    prog = Progress()
    calls = []

    def update(fraction, text):
        calls.append(fraction)
        if len(calls) == 2:              # annulla all'inizio del secondo file
            prog.cancel()
        prog.update(fraction, text)
    rep = bates.bates_files([a, b], prefix="ABC", progress=update)
    assert rep.cancelled
    assert [r.path for r in rep.done] == [a]
    assert rep.next_number == 4 and rep.remaining == [b]
    assert _codes(b) == [None, None]


def test_external_entry_and_plan_preview(ab):
    a, b = ab
    entries = [bates.BatesEntry(a), bates.BatesEntry("/aperto.pdf", external=True, page_count=4), (b, None)]
    plan = bates.plan_bates(entries, start=1, continuous=True)
    assert [(f.first, f.last) for f in plan.files] == [(1, 3), (4, 7), (8, 9)]
    assert bates.preview_codes(plan, prefix="X", digits=3) == [
        ("A.pdf", "X001", "X003"), ("aperto.pdf", "X004", "X007"), ("B.pdf", "X008", "X009")]
    rep = bates.bates_files(entries, prefix="ABC", plan=plan)
    assert rep.done[1].external and rep.done[1].output is None
    assert _codes(b) == ["ABC000008", "ABC000009"]


def test_apply_bates_in_memory_and_cancel_object(ab):
    a, b = ab
    doc = pymupdf.open(a)
    first, last = bates.apply_bates(doc, 7, prefix="ABC", digits=6)
    assert (first, last) == ("ABC000007", "ABC000009")
    prog = Progress()
    prog.cancel()
    rep = bates.bates_files([b], prefix="ABC", progress=prog)
    assert rep.cancelled and not rep.done
    with pytest.raises(OperationCancelled):
        prog.update(0.5)
