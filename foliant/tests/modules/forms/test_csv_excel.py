"""Moduli › CSV leggibile da Excel: UTF-8 con BOM, «;» in italiano, «,» in inglese."""

from __future__ import annotations

import pymupdf
import pytest

from foliant import i18n
from foliant.modules.forms import batch as B
from foliant.modules.forms import interchange as X

BOM = b"\xef\xbb\xbf"


def _scheda(path, cliente: str, modello: str, prezzo: str) -> str:
    """Scheda di valutazione di un orologio usato (dati inventati)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Scheda di valutazione", fontsize=16)
    for i, (name, value) in enumerate((("Cliente", cliente), ("Modello", modello), ("Prezzo", prezzo))):
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.field_value = value
        w.rect = pymupdf.Rect(72, 100 + 30 * i, 400, 120 + 30 * i)
        page.add_widget(w)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def lang():
    previous = i18n.language()
    yield i18n.set_language
    i18n.set_language(previous)


@pytest.fixture
def schede(tmp_path):
    return [_scheda(tmp_path / "valutazione_1.pdf", "Niccolò Àlberti", "Omega Speedmaster «Moonwatch»", "4.250,00 €"),
            _scheda(tmp_path / "valutazione_2.pdf", "Café Rossi", "Tudor Black Bay; 41 mm", "3.100,50 €")]


def test_collect_csv_italian_excel(tmp_path, schede, lang, monkeypatch):
    lang("it")
    out = tmp_path / "raccolta.csv"
    B.collect_csv(schede, str(out))
    data = out.read_bytes()
    assert data.startswith(BOM)
    header = data[3:].decode("utf-8").splitlines()[0]
    assert header == "File;Cliente;Modello;Prezzo"
    assert "4.250,00 €" in data.decode("utf-8-sig")
    recs = X.csv_records(data)                      # Foliant rilegge ciò che scrive
    assert recs[0]["Cliente"] == "Niccolò Àlberti" and recs[0]["Prezzo"] == "4.250,00 €"
    assert recs[1]["Modello"] == "Tudor Black Bay; 41 mm"


def test_collect_csv_english(tmp_path, schede, lang, monkeypatch):
    lang("en")
    monkeypatch.setattr(X, "_system_decimal_comma", lambda: False)
    out = tmp_path / "raccolta.csv"
    B.collect_csv(schede, str(out))
    data = out.read_bytes()
    assert data.startswith(BOM)
    assert data[3:].decode("utf-8").splitlines()[0] == "File,Cliente,Modello,Prezzo"
    recs = X.csv_records(data)
    assert recs[0]["Prezzo"] == "4.250,00 €" and recs[1]["Cliente"] == "Café Rossi"


def test_english_ui_with_decimal_comma_uses_semicolon(lang, monkeypatch):
    lang("en")
    monkeypatch.setattr(X, "_system_decimal_comma", lambda: True)
    assert X.csv_delimiter() == ";"


def test_export_data_csv_and_txt(lang):
    lang("it")
    values = {"Cliente": "Niccolò", "Prezzo": "8.500 €"}
    csv_data = X.export_data("csv", values)
    assert csv_data.startswith(BOM) and b"Cliente;Prezzo" in csv_data
    assert X.parse_data("csv", csv_data) == values
    txt = X.export_data("txt", values)
    assert txt.startswith(b"Cliente\tPrezzo")
    assert X.parse_data("txt", txt) == values


def test_collect_csv_no_rows_has_bom(tmp_path):
    out = tmp_path / "vuota.csv"
    B.collect_csv([], str(out))
    assert out.read_bytes() == BOM + b"File\n"
