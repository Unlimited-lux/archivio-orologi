"""Difetti del collaudo dell'area Converti (B-7 … B-22): un test per difetto, con documenti
realistici di un commerciante di orologi (dati inventati)."""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path

import pymupdf
import pytest

from tests.conftest import system_font
from foliant.api import OperationCancelled
from foliant.modules.convert.common import RangeError, parse_page_range
from foliant.modules.convert.engine import convert_pdf, run_conversion


def _save(doc, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


# ------------------------------------------------------------------ B-7
def _perizie(path, pages=150):
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page()
        for k in range(25):
            p.insert_text((50, 60 + k * 26), f"Perizia {i + 1}.{k + 1}: Omega Speedmaster ref. 3570.50, "
                                             f"cassa 42 mm, valore stimato {1800 + k * 10} euro", fontsize=10)
    return _save(doc, path)


def test_b7_word_fedele_annulla_durante_analisi(tmp_path):
    """Annulla durante l'analisi di pdf2docx ferma la conversione e non crea il file."""
    from foliant.ui.tasks import Progress
    src = _perizie(tmp_path / "perizie.pdf")
    out = tmp_path / "perizie.docx"
    progress = Progress()
    seen = []
    orig = progress.update

    def update(fraction=None, text=None):
        seen.append(text)
        if text and text.startswith("Analisi della pagina 5 "):
            progress.cancel()
        orig(fraction, text)
    progress.update = update
    with pytest.raises(OperationCancelled):
        run_conversion(src, out, "docx", progress, layout="faithful")
    assert not out.exists()
    assert not any(t and t.startswith("Conversione pagina") for t in seen)


def test_b7_word_fedele_annulla_alla_fine_non_crea_file(tmp_path, monkeypatch):
    """Annulla richiesto mentre pdf2docx scrive il documento: nessun file rinominato."""
    from pdf2docx import Converter

    from foliant.ui.tasks import Progress
    src = _perizie(tmp_path / "perizie.pdf", pages=3)
    out = tmp_path / "perizie.docx"
    progress = Progress()
    orig = Converter.make_docx

    def make_docx(self, *a, **k):
        orig(self, *a, **k)
        progress.cancel()
    monkeypatch.setattr(Converter, "make_docx", make_docx)
    with pytest.raises(OperationCancelled):
        run_conversion(src, out, "docx", progress, layout="faithful")
    assert not out.exists() and not list(tmp_path.glob("*.docx"))


def test_b7_word_fedele_senza_annullo_funziona(tmp_path):
    from docx import Document
    src = _perizie(tmp_path / "perizie.pdf", pages=2)
    out = convert_pdf(src, tmp_path / "p.docx", "docx", layout="faithful")[0]
    assert "Speedmaster" in "\n".join(p.text for p in Document(out).paragraphs)


# ------------------------------------------------------------------ B-8
CLAUSOLA = ("Il venditore dichiara che l'orologio Rolex Datejust ref. 126300 è di sua piena proprietà, "
            "libero da vincoli e privo di difetti occulti, e ne garantisce l'autenticità.")


@pytest.fixture
def contratto(tmp_path):
    bold = pymupdf.Font(fontfile=system_font("truetype/liberation/LiberationSans-Bold.ttf"))
    regular = pymupdf.Font(fontfile=system_font("truetype/liberation/LiberationSans-Regular.ttf"))
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for n, titolo in ((1, "Premesse"), (2, "Oggetto della vendita")):
        # titolo vicino alla clausola: MuPDF li mette nello stesso blocco
        tw = pymupdf.TextWriter(page.rect)
        tw.append((72, y), f"Art. {n} – {titolo}", font=bold, fontsize=14)
        tw.append((72, y + 14), CLAUSOLA[:CLAUSOLA.index(" ", 65)], font=regular, fontsize=10)
        tw.append((72, y + 26), CLAUSOLA[CLAUSOLA.index(" ", 65) + 1:], font=regular, fontsize=10)
        tw.write_text(page)
        y += 90
    return _save(doc, tmp_path / "contratto.pdf")


def test_b8_titolo_separato_in_word(contratto, tmp_path):
    from docx import Document
    blocks = pymupdf.open(contratto)[0].get_text("dict")["blocks"]
    assert any("Premesse" in "".join(s["text"] for ln in b["lines"] for s in ln["spans"]) and
               "venditore" in "".join(s["text"] for ln in b["lines"] for s in ln["spans"]) for b in blocks), \
        "il documento di prova deve avere titolo e clausola nello stesso blocco"
    out = convert_pdf(contratto, tmp_path / "c.docx", "docx")[0]
    paras = [p for p in Document(out).paragraphs if p.text.strip()]
    head = next(p for p in paras if "Premesse" in p.text)
    assert head.text.strip() == "Art. 1 – Premesse"
    assert head.style.name.startswith("Heading")
    body = next(p for p in paras if p.text.startswith("Il venditore"))
    assert not body.style.name.startswith("Heading")


def test_b8_titolo_separato_in_html(contratto, tmp_path):
    out = convert_pdf(contratto, tmp_path / "c.html", "html")[0]
    html = Path(out).read_text(encoding="utf-8")
    import re
    assert re.search(r"<h\d>(<strong>)?Art\. 1 – Premesse(</strong>)?</h\d>", html)
    assert re.search(r"<p>Il venditore dichiara", html)


# ------------------------------------------------------------------ B-9 / B-10
@pytest.fixture
def fattura(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 70), "Fattura n. 2026/118", fontsize=14)
    page.insert_text((72, 95), "Cliente: Orologeria Bianchi S.r.l.", fontsize=11)
    rows = [["Articolo", "Ritiro", "Importo"], ["Rolex Submariner 16610", "09:00", "8.500,00"],
            ["Omega Seamaster 300", "14:30:15", "3.200,00"]]
    x0, y0, cw, rh = 72, 120, 150, 24
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), val, fontsize=10)
    page.insert_text((72, 230), "Totale fattura: 11.700,00 euro", fontsize=12)
    return _save(doc, tmp_path / "fattura.pdf")


def test_b9_excel_testo_fuori_tabella_nelle_pagine_con_tabella(fattura, tmp_path):
    from openpyxl import load_workbook
    ws = load_workbook(convert_pdf(fattura, tmp_path / "f.xlsx", "xlsx", include_text=True)[0]).active
    col_a = [str(c.value) for c in ws["A"] if c.value is not None]
    i_num = next(i for i, v in enumerate(col_a) if "Fattura n. 2026/118" in v)
    i_cli = next(i for i, v in enumerate(col_a) if "Orologeria Bianchi" in v)
    i_tab = col_a.index("Articolo")
    i_tot = next(i for i, v in enumerate(col_a) if "Totale fattura" in v)
    assert i_num < i_cli < i_tab < i_tot
    # il testo dentro la tabella non è ripetuto come riga di testo
    assert sum("Submariner" in v for v in col_a) == 1


def test_b9_excel_senza_testo_resta_solo_tabella(fattura, tmp_path):
    from openpyxl import load_workbook
    ws = load_workbook(convert_pdf(fattura, tmp_path / "f.xlsx", "xlsx")[0]).active
    assert ws["A1"].value == "Articolo"
    assert not any("Totale fattura" in str(c.value) for c in ws["A"])


def test_b10_excel_orari(fattura, tmp_path):
    from openpyxl import load_workbook

    from foliant.modules.convert.export_office import parse_cell
    assert parse_cell("09:00") == (dt.time(9, 0), "hh:mm")
    assert parse_cell("14:30:15") == (dt.time(14, 30, 15), "hh:mm:ss")
    assert parse_cell("25:10")[0] == "25:10"          # non è un orario
    assert parse_cell("12.50", ".")[0] == 12.5             # il punto resta separatore decimale
    ws = load_workbook(convert_pdf(fattura, tmp_path / "f.xlsx", "xlsx")[0]).active
    cell = next(c for row in ws.iter_rows() for c in row if c.value == dt.time(9, 0))
    assert cell.data_type in ("n", "d") and cell.number_format == "hh:mm"
    assert any(c.value == dt.time(14, 30, 15) and c.number_format == "hh:mm:ss"
               for row in ws.iter_rows() for c in row)


# ------------------------------------------------------------------ B-13
def test_b13_pdf_vuoto_personalizzato_mantiene_larghezza_e_altezza(window):
    from foliant.modules.convert.create import MM, blank_pdf
    from foliant.modules.convert.create_dialogs import BlankDialog
    dlg = BlankDialog(window)
    dlg.size_box.setCurrentIndex(dlg.size_box.findData("custom"))
    dlg.unit.setCurrentIndex(dlg.unit.findData("mm"))
    dlg.w.setValue(200)
    dlg.h.setValue(100)
    with pymupdf.open(stream=blank_pdf(**dlg.options())) as d:
        assert abs(d[0].rect.width - 200 * MM) < 1 and abs(d[0].rect.height - 100 * MM) < 1
    # i formati predefiniti seguono ancora l'orientamento
    dlg.size_box.setCurrentIndex(dlg.size_box.findData("A4"))
    dlg.orient_box.setCurrentIndex(dlg.orient_box.findData("landscape"))
    with pymupdf.open(stream=blank_pdf(**dlg.options())) as d:
        assert d[0].rect.width > d[0].rect.height


# ------------------------------------------------------------------ B-17
def test_b17_nome_con_barra_non_crea_sottocartelle(window, tmp_path):
    from foliant.modules.convert.export_dialog import ExportDialog
    dlg = ExportDialog(window, page_count=3, name="Perizia 12/2026", folder=str(tmp_path), fmt="txt")
    assert dlg.name_edit.text() == "Perizia 12-2026"
    dlg.name_edit.setText("Stima Rolex 3\\2026")
    assert Path(dlg.output_path()) == tmp_path / "Stima Rolex 3-2026.txt"
    dlg.name_edit.textEdited.emit("Perizia 7/2026")
    assert dlg.name_edit.text() == "Perizia 7-2026"


# ------------------------------------------------------------------ B-22
def test_b22_messaggi_intervallo_chiari():
    def msg(expr, n=3):
        with pytest.raises(RangeError) as e:
            parse_page_range(expr, n)
        return str(e.value)
    assert msg("99") == "La pagina 99 non esiste (il documento ha 3 pagine)."
    assert msg("5-2") == "Le pagine 4-5 non esistono (il documento ha 3 pagine)."
    assert msg("2-4") == "La pagina 4 non esiste (il documento ha 3 pagine)."
    assert msg("2", 1) == "La pagina 2 non esiste (il documento ha 1 pagina)."
    assert "parte da 1" in msg("0")


def test_b22_messaggi_intervallo_in_inglese():
    from foliant.modules.convert.i18n_en import EN
    for key in ("La pagina {p} non esiste ({has}).", "Le pagine {a}-{b} non esistono ({has}).",
                "il documento ha 1 pagina", "il documento ha {n} pagine",
                "La pagina 0 non esiste: la numerazione parte da 1.", "Analisi della pagina {i} di {n}"):
        assert key in EN
