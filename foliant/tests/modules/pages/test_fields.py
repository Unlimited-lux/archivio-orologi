"""Campi dinamici e modelli di nome (PAG-32)."""

from __future__ import annotations

import datetime as dt

import pymupdf

from foliant.modules.pages import engine as E
from foliant.modules.pages import fields as F

from .conftest import numbered_pdf


def test_page_of_total():
    assert F.render_template("Pagina {pagina} di {totale}", {"pagina": 3, "totale": 12}) == "Pagina 3 di 12"


def test_zero_padding_in_file_names(tmp_path):
    doc = numbered_pdf(8)
    files = E.write_parts(doc, [E.SplitPart([i]) for i in range(8)], tmp_path, "{nome}_{pagina:3}", "report")
    assert files[6].name == "report_007.pdf"


def test_date_format():
    when = dt.datetime(2026, 10, 28, 9, 5)
    assert F.render_template("{data:aaaa-MM-gg}", {}, now=when) == "2026-10-28"
    assert F.render_template("{ora:HH:mm}", {}, now=when) == "09:05"
    assert F.render_template("{data}", {}, now=when) == "28/10/2026"


def test_unknown_placeholder_blocked(tmp_path):
    err = F.validate_template("{sconosciuto}")
    assert err and "sconosciuto" in err
    import pytest
    with pytest.raises(E.PageOpError):
        E.write_parts(numbered_pdf(1), [E.SplitPart([0])], tmp_path, "{sconosciuto}", "x")


def test_fallback_literals_and_empty():
    assert F.render_template("{titolo|nome}", {"titolo": "", "nome": "doc"}) == "doc"
    assert F.render_template("{{x}} {nome}", {"nome": "a"}) == "{x} a"
    assert F.validate_template("{{x}}") is None
    assert F.validate_template("{pagina") is not None
    assert E.render_name("{titolo}", titolo="") == "senza_nome"   # in italiano; in inglese «untitled»


def test_document_and_page_values():
    doc = numbered_pdf(3)
    doc.set_metadata({"title": "Relazione", "author": "Verdi"})
    doc.set_page_labels([{"startpage": 0, "style": "r"}])
    base = F.document_values(doc, "/tmp/report.pdf")
    vals = F.page_values(doc, 1, base)
    assert F.render_template("{titolo} – {etichetta} – {nome} – {pagina}/{totale}", vals) == \
        "Relazione – ii – report – 2/3"
