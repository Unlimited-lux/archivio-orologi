"""Elaborazione multipla dei dati dei moduli (FRM-30)."""

from __future__ import annotations

import csv
import io
import os

import pymupdf
import pytest

from foliant.modules.forms import batch as B
from foliant.modules.forms import engine as E
from foliant.modules.forms import interchange as X


def make_template(path) -> str:
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(72, 80, 272, 100), "Nome")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 80, 500, 100), "Cognome")
    E.create_field(pdf, 0, E.COMBO, pymupdf.Rect(72, 120, 222, 140), "Scelta", options=["A", "B"])
    E.create_field(pdf, 0, E.CHECKBOX, pymupdf.Rect(72, 160, 86, 174), "Privacy")
    pdf.save(str(path))
    pdf.close()
    return str(path)


@pytest.fixture
def template(tmp_path):
    return make_template(tmp_path / "modello.pdf")


def values_of(path):
    pdf = pymupdf.open(path)
    try:
        return E.form_values(pdf)
    finally:
        pdf.close()


def test_collect_csv_three_files(template, tmp_path):
    out = tmp_path / "compilati"
    rep = B.fill_series(template, [
        {"Nome": "Anna", "Cognome": "Rossi", "Scelta": "A", "Privacy": "Yes"},
        {"Nome": "Luca", "Cognome": "Bianchi", "Scelta": "B"},
        {"Nome": "Sara", "Cognome": "Verdi"},
    ], str(out), "{n}_{Cognome}.pdf")
    assert rep["errors"] == [] and len(rep["created"]) == 3
    steps = []
    csv_path = tmp_path / "raccolta.csv"
    report = B.collect_csv(rep["created"], str(csv_path), progress=lambda f, t: steps.append(f))
    assert report["rows"] == 3 and report["errors"] == []
    rows = list(csv.reader(io.StringIO(csv_path.read_text("utf-8"))))
    assert len(rows) == 4
    assert rows[0][0] == "File" and set(rows[0][1:]) == {"Nome", "Cognome", "Scelta", "Privacy"}
    assert [r[0] for r in rows[1:]] == ["1_Rossi.pdf", "2_Bianchi.pdf", "3_Verdi.pdf"]
    recs = X.csv_records(csv_path.read_bytes())
    assert recs[0]["Nome"] == "Anna" and recs[0]["Privacy"] == "Yes" and recs[1]["Scelta"] == "B"
    assert steps and steps[-1] == 1.0


def test_fill_series_fifty_rows_duplicates(template, tmp_path):
    surnames = ["Rossi", "Bianchi", "Verdi", "Neri", "Gialli"]
    data = X.export_csv([{"Nome": f"N{i}", "Cognome": surnames[i % 5]} for i in range(50)])
    records = X.csv_records(data)
    out = tmp_path / "serie"
    rep = B.fill_series(template, records, str(out), "{Cognome}.pdf")
    assert rep["errors"] == [] and rep["unknown"] == []
    assert len(rep["created"]) == 50 and len(set(rep["created"])) == 50
    names = sorted(os.listdir(out))
    assert len(names) == 50
    assert "Rossi.pdf" in names and "Rossi (2).pdf" in names and "Rossi (10).pdf" in names
    assert values_of(rep["created"][7])["Nome"] == "N7"


def test_invalid_value_reported_others_generated(template, tmp_path):
    records = [
        {"Nome": "Anna", "Cognome": "Rossi", "Scelta": "A"},
        {"Nome": "Luca", "Cognome": "Bianchi", "Scelta": "Z", "Extra": "?"},
        {"Nome": "Sara", "Cognome": "Verdi", "Scelta": "B"},
    ]
    rep = B.fill_series(template, records, str(tmp_path / "out"), "{Cognome}.pdf")
    assert len(rep["created"]) == 3
    assert len(rep["errors"]) == 1
    row, field, message = rep["errors"][0]
    assert (row, field) == (2, "Scelta") and "Z" in message
    assert rep["unknown"] == ["Extra"]
    second = values_of(rep["created"][1])
    assert second["Nome"] == "Luca" and second["Cognome"] == "Bianchi"


def test_names_sanitized_and_no_overwrite(template, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "a_b.pdf").write_bytes(b"esistente")
    rep = B.fill_series(template, [{"Cognome": "a/b"}, {"Cognome": ' <*> '}, {"Cognome": ""}],
                        str(out), "{Cognome}.pdf")
    names = [os.path.basename(p) for p in rep["created"]]
    assert names == ["a_b (2).pdf", "___.pdf", "3.pdf"]
    assert (out / "a_b.pdf").read_bytes() == b"esistente"
    assert B.file_name("{Cognome}_{Nome}", {"Cognome": "Rossi", "Nome": 'A:"B"'}, 4) == "Rossi_A__B_.pdf"
    assert B.file_name("{n}.pdf", {}, 12) == "12.pdf"


def test_collect_errors(template, encrypted_pdf, tmp_path):
    broken = tmp_path / "rotto.pdf"
    broken.write_bytes(b"%PDF-1.7 non un vero pdf")
    plain = tmp_path / "semplice.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(plain))
    doc.close()
    rows, errors = B.collect([template, encrypted_pdf, str(broken), str(plain), str(tmp_path / "manca.pdf")])
    assert [r["File"] for r in rows] == ["modello.pdf"]
    assert list(rows[0])[0] == "File"
    assert [p for p, _m in errors] == [encrypted_pdf, str(broken), str(plain), str(tmp_path / "manca.pdf")]
    assert "password" in errors[0][1]
    assert all(m for _p, m in errors)


def test_list_values_joined(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.LIST, pymupdf.Rect(72, 80, 222, 140), "colori",
                   options=["rosso", "verde", "blu"], multiselect=True)
    E.apply_values(pdf, {"colori": ["rosso", "blu"]})
    path = tmp_path / "lista.pdf"
    pdf.save(str(path))
    pdf.close()
    rows, errors = B.collect([str(path)])
    assert errors == [] and rows[0]["colori"] == "rosso; blu"
