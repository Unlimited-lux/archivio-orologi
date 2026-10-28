"""Confronto e classifica di più documenti (AI-32)."""

from __future__ import annotations

import csv
import io
import json

import pytest

from foliant.modules.ai import ranking
from foliant.modules.ai.ranking import Criterion, Evaluation, RankRow

from .conftest import FakeProvider, make_active_profile


def _rows(scores):
    out = []
    for n, (a, b) in enumerate(scores):
        out.append(RankRow(f"/doc{n}.pdf", "ABC"[n], f"cv{n}.pdf",
                           {"Esperienza": Evaluation("Esperienza", a), "Lingue": Evaluation("Lingue", b)}))
    return out


def test_totals_and_order():
    crit = [Criterion("Esperienza", peso=3), Criterion("Lingue", peso=1)]
    rows = ranking.rank(_rows([(8, 6), (5, 10), (9, 2)]), crit)
    assert [r.etichetta for r in rows] == ["A", "C", "B"]
    assert [r.totale for r in rows] == [7.5, 7.25, 6.25]
    assert [r.posizione for r in rows] == [1, 2, 3]


def test_mandatory_zero_excluded_at_bottom():
    crit = [Criterion("Esperienza", peso=3, obbligatorio=True), Criterion("Lingue", peso=1)]
    rows = ranking.rank(_rows([(0, 10), (5, 5)]), crit)
    assert rows[-1].etichetta == "A" and rows[-1].escluso and "Esperienza" in rows[-1].motivo
    assert rows[0].posizione == 1


def test_csv_one_row_per_document():
    crit = [Criterion("Esperienza", peso=3), Criterion("Lingue", peso=1)]
    rows = ranking.rank(_rows([(8, 6), (5, 10), (9, 2)]), crit)
    data = list(csv.reader(io.StringIO(ranking.to_csv(rows, crit))))
    assert len(data) == 4 and data[0][3:5] == ["Esperienza", "Lingue"]
    assert data[1][3:6] == ["8", "6", "7.50"]


@pytest.fixture
def ready(window):
    fake = FakeProvider()
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    services.refresh()
    services.fake = fake
    return services


def test_dialog_evaluates_cvs_privately(window, ready, text_pdf):
    paths = [text_pdf(1, name=f"cv-rossi-{i}.pdf", texts=[f"Curriculum {i}, nato il 1/1/1980"]) for i in range(3)]
    replies = [(8, 6), (5, 10), (9, 2)]
    fake = ready.fake
    fake.responses = [json.dumps({"evaluations": [{"criterio": "Esperienza", "punteggio": a, "motivazione": "ok",
                                                   "pagine": [1]},
                                                  {"criterio": "Lingue", "punteggio": b, "motivazione": "ok"}]})
                      for a, b in replies]
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.open_ranking()
    dlg.add_paths(paths)
    dlg.add_criterion("Esperienza", "anni di esperienza", 3)
    dlg.add_criterion("Lingue", "lingue straniere", 1)
    dlg.cv.setChecked(True)
    dlg.evaluate()
    assert [r.totale for r in dlg.rows] == [7.5, 7.25, 6.25]
    assert [r.etichetta for r in dlg.rows] == ["A", "C", "B"]
    sent = fake.all_text()
    assert "marital status" in sent and "photos" in sent and "origin" in sent
    assert "cv-rossi" not in sent
    assert all(r.image_count() == 0 for r in fake.requests)
    assert "decisione presa da una persona" in dlg.notice.text() or "decision" in dlg.notice.text()
    opened = []
    dlg.open_ref = lambda path, page: opened.append((path, page))
    dlg.on_cell(0, 2)
    assert opened == [(paths[0], 0)]


def test_ranking_pdf_with_reasons_and_notice(tmp_path):
    import pymupdf
    crit = [Criterion("Esperienza", "anni", peso=3, obbligatorio=True), Criterion("Lingue", peso=1)]
    rows = _rows([(8, 6), (0, 10)])
    rows[0].scores["Esperienza"] = Evaluation("Esperienza", 8, "dieci anni <b>nel settore</b>", [1, 2])
    rows = ranking.rank(rows, crit)
    pdf = pymupdf.open("pdf", ranking.to_pdf(rows, crit))
    text = " ".join(" ".join(p.get_text(flags=0).split()) for p in pdf)   # legature «ﬁ» espanse
    assert "Confronto e classifica" in text and "Esperienza (×3)" in text
    assert "dieci anni <b>nel settore</b>" in text                # testo, mai HTML interpretato
    assert "escluso" in text and "decisione presa da una persona" in text
    assert "Pagina 1 di" in text


def test_ranking_pdf_many_rows_spans_pages():
    import pymupdf
    crit = [Criterion("Esperienza"), Criterion("Lingue")]
    rows = []
    for n in range(60):
        rows.append(RankRow(f"/d{n}.pdf", f"D{n}", f"doc{n}.pdf",
                            {"Esperienza": Evaluation("Esperienza", n % 10, "motivazione " * 12),
                             "Lingue": Evaluation("Lingue", 5, "ok")}))
    pdf = pymupdf.open("pdf", ranking.to_pdf(ranking.rank(rows, crit), crit))
    assert pdf.page_count > 1
    assert "doc59.pdf" in " ".join(p.get_text() for p in pdf)


def test_ranking_pdf_dates_and_numbers_follow_language():
    import datetime as dt

    import pymupdf

    from foliant import i18n
    crit = [Criterion("Esperienza", peso=3), Criterion("Lingue", peso=1)]
    rows = ranking.rank(_rows([(8, 6), (5, 10)]), crit)
    i18n.set_language("en")
    try:
        text = " ".join(" ".join(p.get_text(flags=0).split()) for p in pymupdf.open("pdf", ranking.to_pdf(rows, crit)))
    finally:
        i18n.set_language("it")
    assert "7.50" in text and "7,50" not in text
    today = dt.date.today()
    assert f"{today.month:02d}/{today.day:02d}/{today.year}" in text and ("AM" in text or "PM" in text)
    text_it = " ".join(" ".join(p.get_text(flags=0).split()) for p in pymupdf.open("pdf", ranking.to_pdf(rows, crit)))
    assert "7,50" in text_it and f"{today.day:02d}/{today.month:02d}/{today.year}" in text_it
