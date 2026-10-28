"""Estrazione di dati strutturati (AI-10) con il fornitore simulato: nessuna rete."""

from __future__ import annotations

import csv
import io
import json

import pymupdf
import pytest

from foliant.modules.ai import engine, extract
from foliant.modules.ai.engine import DOCUMENT
from foliant.modules.ai.extract import Cell, ExtractError, ExtractResult, NoData

INVOICE_TEXT = (
    "FATTURA N. 2024/17 del 15/03/2024\n"
    "Fornitore: Officina Rossi srl  P.IVA 01234567897\n"
    "Cliente: Studio Bianchi\n"
    "Revisione movimento  1  100,00  22  100,00\n"
    "Sostituzione vetro  2  100,00  22  200,00\n"
    "Cinturino in pelle  1  1.234,56  22  1.234,56\n"
    "Imponibile 1.534,56  IVA 337,60  Totale 1.872,16 EUR"
)


def invoice_json(total=1872.16, lines=None):
    lines = lines if lines is not None else [
        {"descrizione": "Revisione movimento", "quantita": 1, "prezzo_unitario": 100.0, "aliquota": 22,
         "importo": 100.0, "_page": 1},
        {"descrizione": "Sostituzione vetro", "quantita": 2, "prezzo_unitario": "100,00", "aliquota": 22,
         "importo": 200.0, "_page": 1},
        {"descrizione": "Cinturino in pelle", "quantita": 1, "prezzo_unitario": "1.234,56", "aliquota": 22,
         "importo": 1234.56, "_page": 1},
    ]
    return json.dumps({"records": [{
        "numero": "2024/17", "data": "15/03/2024", "fornitore": "Officina Rossi srl", "partita_iva": "01234567897",
        "cliente": "Studio Bianchi", "righe": lines, "imponibile": 1534.56, "imposta": "337,60",
        "totale": total, "valuta": "EUR", "_page": 1}]})


@pytest.fixture
def invoice_scope(text_pdf):
    path = text_pdf(pages=1, texts=[INVOICE_TEXT], name="fattura.pdf")
    pdf = pymupdf.open(path)
    scope = engine.build_scope(pdf, DOCUMENT, fingerprint="abcdef0123456789", doc_name="fattura.pdf")
    yield scope
    pdf.close()


def test_invoice_json_export_has_three_lines_and_pages(job, fake_provider, invoice_scope, tmp_path):
    fake_provider.responses = [invoice_json()]
    result = extract.extract_data(job, invoice_scope, "invoice")
    assert len(fake_provider.requests) == 1
    assert "invoice" in fake_provider.requests[0].system.lower()
    rec = result.records[0]
    assert len(rec["righe"]) == 3
    assert rec["data"].value == "2024-03-15"
    assert rec["imposta"].value == pytest.approx(337.60)
    assert rec["righe"][2]["prezzo_unitario"].value == pytest.approx(1234.56)
    assert result.checks == []
    assert all(c.verified for c in rec.values() if isinstance(c, Cell))

    data = json.loads(extract.to_json(result))
    assert data["tipo"] == "invoice"
    assert data["origine"] == {"documento": "fattura.pdf", "pagine": [1]}
    r = data["record"][0]
    assert len(r["righe"]) == 3
    for line in r["righe"]:
        assert set(extract.INVOICE_LINE_FIELDS) <= set(line)
        assert all(v["pagina"] == 1 for v in line.values())
    for k in ("numero", "data", "fornitore", "partita_iva", "cliente", "imponibile", "imposta", "totale", "valuta"):
        assert r[k]["pagina"] == 1
    assert r["totale"]["valore"] == pytest.approx(1872.16)


def test_total_absent_from_text_is_unverified(job, fake_provider, invoice_scope):
    fake_provider.responses = [invoice_json(total="999,00")]
    result = extract.extract_data(job, invoice_scope, "invoice")
    total = result.records[0]["totale"]
    assert total.value == pytest.approx(999.0)
    assert total.verified is False
    assert result.records[0]["imponibile"].verified is True
    assert any("totale" in c for c in result.checks)


def test_line_sum_mismatch_is_reported(job, fake_provider, invoice_scope):
    lines = [{"descrizione": "Revisione movimento", "importo": 100.0, "_page": 1}]
    fake_provider.responses = [invoice_json(lines=lines)]
    result = extract.extract_data(job, invoice_scope, "invoice")
    assert any("somma delle righe" in c for c in result.checks)


def test_non_conforming_then_conforming_two_requests(job, fake_provider, invoice_scope):
    fake_provider.responses = ["Ecco i dati della fattura: numero 2024/17, totale 1.872,16.", invoice_json()]
    result = extract.extract_data(job, invoice_scope, "invoice")
    assert len(fake_provider.requests) == 2
    assert result.requests == 2
    assert "only valid json" in fake_provider.requests[1].all_text().lower()
    assert len(result.records[0]["righe"]) == 3


def test_non_conforming_twice_raises_with_raw(job, fake_provider, invoice_scope):
    fake_provider.responses = ["non è JSON", '{"items": []}']
    with pytest.raises(ExtractError) as e:
        extract.extract_data(job, invoice_scope, "invoice")
    assert len(fake_provider.requests) == 2
    assert e.value.raw == '{"items": []}'


def test_no_data_raises_and_creates_no_file(job, fake_provider, invoice_scope, tmp_path):
    fake_provider.responses = ['{"records": []}']
    with pytest.raises(NoData):
        extract.extract_data(job, invoice_scope, "invoice")
    assert not list(tmp_path.glob("*.csv")) and not list(tmp_path.glob("*.json"))


def test_csv_semicolon_and_quotes_roundtrip():
    value = 'Vite "M3"; acciaio, inox'
    result = ExtractResult("tables", [{"Articolo": Cell(value, 0, True), "Prezzo": Cell(12.5, 0, True)}],
                           ["Articolo", "Prezzo"], pages=[0])
    text = extract.to_csv(result, ";")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    assert rows[0] == ["Articolo", "Prezzo", extract.PAGE_COLUMN]
    assert rows[1] == [value, "12.5", "1"]
    assert "\r\n" in text


def test_invoice_csv_one_row_per_line(job, fake_provider, invoice_scope):
    fake_provider.responses = [invoice_json()]
    result = extract.extract_data(job, invoice_scope, "invoice")
    rows = list(csv.DictReader(io.StringIO(extract.to_csv(result), newline="")))
    assert len(rows) == 3
    assert {r["numero"] for r in rows} == {"2024/17"}
    assert [r["righe.importo"] for r in rows] == ["100", "200", "1234.56"]


@pytest.mark.parametrize("text, expected", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("€ 12,50", 12.5), ("12.50", 12.5), ("1.234.567", 1234567.0),
    ("1 234,5", 1234.5), ("-3,25", -3.25), ("22%", 22.0), ("1'234.50", 1234.5), ("abc", None), ("", None),
])
def test_local_number_normalisation(text, expected):
    got = extract.parse_number(text)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_dates_and_untyped_values():
    assert extract.parse_date("15/03/2024") == "2024-03-15"
    assert extract.parse_date("2024-03-15") == "2024-03-15"
    assert extract.parse_date("03/04/2024", assume_day_first=False) is None     # ambigua
    assert extract.parse_date("31/02/2024") is None
    norm = extract._norm_value
    assert norm("1.234,56", None) == pytest.approx(1234.56)
    assert norm("00123", None) == "00123"                                        # codice: invariato
    assert norm("25/12/2023", None) == "2023-12-25"


def test_verification_compares_normalised_forms():
    src = extract._source("Totale documento 999,00 € del 15.03.2024")
    assert extract.value_in_text(999.0, src)
    assert extract.value_in_text("2024-03-15", src)
    assert not extract.value_in_text(998.0, src)
    assert not extract.value_in_text("Officina Verdi", src)


def test_tables_across_chunks_drop_repeated_headers(job, fake_provider, text_pdf, monkeypatch):
    filler = "Listino ricambi per orologi meccanici, prezzi in euro IVA esclusa. " * 6
    texts = [filler + "\nCodice Descrizione Prezzo\n" +
             "\n".join(f"A{p}{i} Articolo {p}{i} {i},50" for i in range(3)) for p in range(2)]
    path = text_pdf(pages=2, texts=texts)
    pdf = pymupdf.open(path)
    scope = engine.build_scope(pdf, DOCUMENT)
    page_len = max(len(p.text) for p in scope.pages)
    monkeypatch.setattr(extract, "budget_for", lambda profile, system="", extra="": (page_len + 40, 256))

    def reply(request):
        page = 1 if "[p. 1]" in request.messages[0].content else 2
        rows = [{"Codice": "Codice", "Descrizione": "Descrizione", "Prezzo": "Prezzo", "_page": page}]
        rows += [{"Codice": f"A{page - 1}{i}", "Descrizione": f"Articolo {page - 1}{i}", "Prezzo": f"{i},50",
                  "_page": page} for i in range(3)]
        return json.dumps({"records": rows})

    fake_provider.responses = [reply, reply]
    result = extract.extract_data(job, scope, "tables")
    assert len(fake_provider.requests) == 2
    assert len(result.records) == 6
    assert result.columns == ["Codice", "Descrizione", "Prezzo"]
    assert result.records[3]["Codice"].page == 1
    assert result.records[0]["Prezzo"].value == pytest.approx(0.5)
    assert all(c.verified for r in result.records for c in r.values())
    pdf.close()


def test_page_label_maps_to_physical_index(job, fake_provider, text_pdf):
    path = text_pdf(pages=3, texts=["Copertina", "Prefazione", "Importo 45,00"],
                    labels=[{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
    pdf = pymupdf.open(path)
    scope = engine.build_scope(pdf, DOCUMENT)
    fake_provider.responses = [json.dumps({"records": [{"importo": 45, "_page": "iii"}]})]
    result = extract.extract_data(job, scope, "custom", [{"name": "importo", "type": "number"}])
    cell = result.records[0]["importo"]
    assert cell.page == 2 and cell.verified and cell.value == 45.0
    pdf.close()


# ------------------------------------------------------------------ dialogo
def test_dialog_edit_and_export(qtbot, job, fake_provider, invoice_scope, tmp_path):
    from PySide6.QtCore import Qt

    from foliant.modules.ai.extract_view import ExtractDialog

    fake_provider.responses = [invoice_json(total="999,00")]
    result = extract.extract_data(job, invoice_scope, "invoice")
    asked = []

    def ask_save_path(title, suggested, file_filter, suffix):
        asked.append(suggested)
        return str(tmp_path / ("out" + suffix))

    dlg = ExtractDialog(result, ask_save_path)
    qtbot.addWidget(dlg)
    cols = dlg.columns
    assert dlg.table.rowCount() == 3
    tc = cols.index("totale")
    total_item = dlg.table.item(0, tc)
    assert total_item.toolTip() == "non verificato"
    assert total_item.background().color().alpha() == 60
    assert not dlg.checks.isHidden()

    # modifica del totale: aggiorna risultato, tutte le righe della fattura e i controlli
    total_item.setText("1.872,16")
    assert result.records[0]["totale"].value == pytest.approx(1872.16)
    assert result.records[0]["totale"].verified
    assert dlg.table.item(2, tc).text() == "1872.16"
    assert result.checks == []
    assert dlg.checks.isHidden()

    # modifica di una riga annidata
    dc = cols.index("righe.descrizione")
    dlg.table.item(1, dc).setText('Vetro "zaffiro"; bombato')
    assert result.records[0]["righe"][1]["descrizione"].value == 'Vetro "zaffiro"; bombato'

    dlg.separator.setCurrentIndex(dlg.separator.findData(";"))
    path = dlg.export_csv()
    assert path and asked[-1].endswith(".csv")
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    assert rows[1]["righe.descrizione"] == 'Vetro "zaffiro"; bombato'
    assert rows[0]["totale"] == "1872.16"

    path = dlg.export_json()
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["record"][0]["totale"]["valore"] == pytest.approx(1872.16)

    dlg.copy()
    from PySide6.QtWidgets import QApplication
    assert "Vetro" in QApplication.clipboard().text()

    dlg.raw_button.click()
    assert "999,00" in dlg.raw_dialog.text.toPlainText()
    assert not dlg.table.item(0, len(cols)).flags() & Qt.ItemIsEditable      # colonna pagina
    assert dlg.table.item(0, len(cols)).text() == "1"


def test_dialog_export_cancelled_writes_nothing(qtbot, tmp_path):
    from foliant.modules.ai.extract_view import ExtractDialog

    result = ExtractResult("tables", [{"A": Cell("x", 0, True)}], ["A"], pages=[0])
    dlg = ExtractDialog(result, lambda *a: None)
    qtbot.addWidget(dlg)
    assert dlg.export_csv() is None
    assert not list(tmp_path.glob("*.csv"))
