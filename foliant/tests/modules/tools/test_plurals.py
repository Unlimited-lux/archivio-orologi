"""Singolare e plurale nei conteggi (collaudo C-10): «1 aggiunta · 0 eliminazioni · 2 modifiche», non
«1 aggiunte»; «20 completati», non «20 completato». Anche in inglese («1 addition», «2 changes»)."""

from __future__ import annotations

from datetime import datetime

import pymupdf
import pytest

from foliant import i18n
from foliant.modules.tools import compare_engine as ce
from foliant.modules.tools import engine as be
from foliant.modules.tools import report
from foliant.modules.tools import text_compare as tc

from .conftest import read

EXPECTED = {"it": "1 aggiunta · 0 eliminazioni · 2 modifiche", "en": "1 addition · 0 deletions · 2 changes"}


def _result(kinds, statuses=()):
    side = ce.SideInfo("a.pdf", None, "0" * 64, "", 1, [0])
    diffs = [ce.Difference(k, rects_a=[(0, 10, 10, 20, 20)], rects_b=[(0, 10, 10, 20, 20)]) for k in kinds]
    for d, st in zip(diffs, statuses):
        d.status = st
    return ce.CompareResult(side, side, ce.CompareOptions(), diffs)


@pytest.fixture(params=["it", "en"])
def lang(request, window):
    """Lingua dell'interfaccia (la finestra registra i cataloghi dei moduli)."""
    i18n.set_language(request.param)
    yield request.param
    i18n.set_language("it")


def test_count_labels(lang):
    assert ce.count_label(ce.ADDED, 1) == {"it": "1 aggiunta", "en": "1 addition"}[lang]
    assert ce.count_label(ce.ADDED, 0) == {"it": "0 aggiunte", "en": "0 additions"}[lang]
    assert ce.count_label(ce.IMAGE, 1) == {"it": "1 immagine", "en": "1 image"}[lang]
    assert ce.count_label(ce.FIELD, 3) == {"it": "3 campi modulo", "en": "3 form fields"}[lang]
    assert ce.status_count_label("accepted", 1) == {"it": "1 accettata", "en": "1 accepted"}[lang]
    assert ce.status_count_label("rejected", 2) == {"it": "2 rifiutate", "en": "2 rejected"}[lang]


def test_compare_documents_summary(lang):
    r = _result([ce.ADDED, ce.MODIFIED, ce.MODIFIED])
    assert r.summary() == EXPECTED[lang]


def test_compare_panel_status_counts(lang, window):
    from foliant.modules.tools import compare_ui as cu
    panel = cu.DiffPanel()
    panel.set_result(_result([ce.ADDED, ce.MODIFIED, ce.MODIFIED], ["accepted", "rejected", "rejected"]))
    assert panel.status_counts.text() == {"it": "1 accettata · 2 rifiutate · 0 da verificare",
                                          "en": "1 accepted · 2 rejected · 0 to check"}[lang]
    panel.deleteLater()


def test_text_compare_summary(lang, window):
    dlg = tc.TextCompareDialog(window.ctx, window)
    dlg.side_a.edit.setPlainText("uno\ndue\ntre\nquattro\ncinque")
    dlg.side_b.edit.setPlainText("uno\nDUE\ntre\nQUATTRO\ncinque\nsei")
    dlg.granularity.setCurrentIndex(dlg.granularity.findData("line"))
    dlg.run()
    assert dlg.summary.text() == EXPECTED[lang]
    dlg.close()


def test_pdf_report_counts(lang, pdf_builder):
    from .conftest import page_lines
    a = pdf_builder([page_lines(1), ["Il canone è di 100 euro", "fine pagina"]])
    b = pdf_builder([page_lines(1), ["Il canone è di 120 euro", "fine pagina", "riga nuova aggiunta qui"]])
    r = ce.run_compare(None, ce.SideInput(read(a), "a.pdf", a), ce.SideInput(read(b), "b.pdf", b),
                       ce.CompareOptions(dpi=72))
    counts = r.counts()
    assert counts[ce.MODIFIED] == 1 and counts[ce.ADDED] == 1
    data = report.build_report(None, r, read(a), read(b))
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = " ".join(doc[i].get_text() for i in range(doc.page_count)).replace("\n", " ")
    one = {"it": ("1 aggiunta", "1 modifica", "0 eliminazioni"), "en": ("1 addition", "1 change", "0 deletions")}
    for label in one[lang]:
        assert label in text, (label, text[:400])
    wrong = {"it": ("1 aggiunte", "1 modifiche"), "en": ("1 additions", "1 changes")}
    assert not any(w in text for w in wrong[lang])


def test_batch_summary(lang):
    def report_of(statuses):
        results = [be.FileResult(f"/tmp/f{i}.pdf", status=st) for i, st in enumerate(statuses)]
        return be.JobReport(results, [], datetime(2026, 11, 6, 10, 0))
    many = report_of(["ok"] * 20 + ["warning"] * 2 + ["error"])
    one = report_of(["ok", "warning", "error", "error"])
    if lang == "it":
        assert many.summary() == "20 completati · 2 con avvisi · 1 errore"
        assert one.summary() == "1 completato · 1 con avviso · 2 errori"
    else:
        assert many.summary() == "20 completed · 2 with warnings · 1 error"
        assert one.summary() == "1 completed · 1 with warning · 2 errors"
