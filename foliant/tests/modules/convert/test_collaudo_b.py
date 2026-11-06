"""Difetti del collaudo dell'area Converti (B-7 … B-22): un test per difetto, con documenti
realistici di un commerciante di orologi (dati inventati)."""

from __future__ import annotations

import datetime as dt
import re
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


# ------------------------------------------------------------------ azioni (Ripara, Comprimi, Portfolio)
@pytest.fixture
def ctrl(window):
    return window.ctx.convert_controller


@pytest.fixture
def messages(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    log = {"error": [], "info": [], "confirm": [], "notify": []}
    monkeypatch.setattr(window.ctx, "error", lambda t, m: log["error"].append((t, m)))
    monkeypatch.setattr(window.ctx, "info", lambda t, m: log["info"].append((t, m)))
    monkeypatch.setattr(window.ctx, "notify", lambda text, actions=(), duration_ms=None:
                        log["notify"].append((text, [a[0] for a in actions])))

    def confirm(t, m):
        log["confirm"].append((t, m))
        return True
    monkeypatch.setattr(window.ctx, "confirm", confirm)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return log


# ------------------------------------------------------------------ B-11
def _libreoffice_pdf(tmp_path) -> bytes:
    """PDF come lo scrive LibreOffice Writer: albero /Pages e catalogo in fondo al file."""
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page()
        page.insert_text((72, 100), f"Perizia orologio numero {i + 1}: Rolex Submariner 16610, valore "
                                    f"stimato {8000 + i * 100} euro", fontsize=11)
    raw = doc.tobytes(garbage=0, no_new_id=True)
    doc.close()
    # riscrittura in stile Writer: pagine, risorse e contenuti prima; /Pages e /Catalog per ultimi
    src = pymupdf.open(stream=raw)
    objs = {x: src.xref_object(x, compressed=True) for x in range(1, src.xref_length())}
    streams = {x: src.xref_stream(x) for x in objs if src.xref_is_stream(x)}
    cat = src.pdf_catalog()
    tree = int(src.xref_get_key(cat, "Pages")[1].split()[0])
    order = [x for x in objs if x not in (cat, tree)] + [tree, cat]
    out = bytearray(b"%PDF-1.7\n")
    for x in order:
        body = objs[x]
        if x in streams:
            data = streams[x]
            body = re.sub(r"/Filter\s*/\w+|/Length\s+\d+", "", body).rstrip().rstrip(">") + f"/Length {len(data)}>>"
            out += f"{x} 0 obj\n{body}\nstream\n".encode("latin-1") + data + b"\nendstream\nendobj\n"
        else:
            out += f"{x} 0 obj\n{body}\nendobj\n".encode("latin-1")
    out += f"trailer\n<</Size {src.xref_length()} /Root {cat} 0 R>>\nstartxref\n0\n%%EOF\n".encode()
    src.close()
    with pymupdf.open(stream=bytes(out)) as check_doc:
        assert check_doc.page_count == 6
    return bytes(out)


def test_b11_ripara_pdf_troncato_senza_albero_delle_pagine(tmp_path):
    from foliant.modules.convert import optimize
    data = _libreoffice_pdf(tmp_path)
    truncated = data[: int(len(data) * 0.85)]
    assert truncated.rfind(b"/Type/Pages") < 0 and truncated.rfind(b"/Type /Pages") < 0
    path = tmp_path / "perizie.pdf"
    path.write_bytes(truncated)
    out, report = optimize.repair_file(path)
    assert Path(out).name == "perizie_riparato.pdf"
    with pymupdf.open(out) as d:
        texts = [d[i].get_text() for i in range(d.page_count)]
    assert report["pages"] == len(texts) >= 5
    assert "Perizia orologio numero 1" in texts[0] and "numero 2" in texts[1]
    assert any("troncato" in w for w in report["warnings"])


# ------------------------------------------------------------------ B-12 / B-18
def test_b12_ripara_file_di_testo_rinominato_crea_pdf_col_nome_originale(window, ctrl, messages, tmp_path,
                                                                          monkeypatch):
    path = tmp_path / "Nota consegna Rolex.pdf"
    path.write_text("Consegna del Rolex Datejust 126300 al cliente Bianchi il 3 aprile 2026.\n", encoding="utf-8")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(path)])
    ctrl.repair()
    assert not messages["error"], messages["error"]
    assert messages["confirm"] and "file di testo" in messages["confirm"][0][1]
    view = window.ctx.current_view()
    assert view is not None and view.document.display_name == "Nota consegna Rolex.pdf"
    assert "Datejust 126300" in view.document.page_text(0)
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert "tmp" not in (d.metadata.get("title") or "")
    assert list(tmp_path.iterdir()) == [path]          # nessuna copia lasciata accanto


def test_b18_ripara_jpeg_rinominato_crea_pdf_col_nome_originale(window, ctrl, messages, tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from foliant.modules.convert.create_dialogs import ImagesDialog
    from PySide6.QtWidgets import QDialog
    img = np.full((300, 400, 3), 230, np.uint8)
    cv2.putText(img, "Omega 3570.50", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".jpg", img)
    path = tmp_path / "Foto quadrante.pdf"
    path.write_bytes(buf.tobytes())
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(path)])
    monkeypatch.setattr(ImagesDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    ctrl.repair()
    assert not messages["error"], messages["error"]
    view = window.ctx.current_view()
    assert view is not None and view.document.display_name == "Foto quadrante.pdf"


# ------------------------------------------------------------------ B-14
def test_b14_comprimi_piu_file_non_sovrascrive(window, ctrl, messages, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from PySide6.QtWidgets import QInputDialog
    rng = np.random.default_rng(3)
    doc = pymupdf.open()
    page = doc.new_page()
    img = (rng.random((900, 900, 3)) * 40 + 180).astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    page.insert_image(page.rect, stream=buf.tobytes())
    page.insert_text((72, 72), "Catalogo autunno 2026: Rolex, Omega, Longines", fontsize=12)
    src = _save(doc, tmp_path / "catalogo.pdf")
    old = tmp_path / "catalogo_compresso.pdf"
    old.write_bytes(b"versione precedente da conservare")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [src])
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Bassa", True)))
    rows = ctrl.compress_files()
    assert rows and rows[0][3] == str(tmp_path / "catalogo_compresso (1).pdf")
    assert old.read_bytes() == b"versione precedente da conservare"
    assert (tmp_path / "catalogo_compresso (1).pdf").exists()


def test_b14_comprimi_piu_file_chiede_la_password(window, ctrl, messages, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Listino riservato rivenditori " * 20, fontsize=9)
    path = tmp_path / "listino.pdf"
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="orologi", owner_pw="orologi")
    doc.close()
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(path)])
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Senza perdita", True)))
    asked = []
    monkeypatch.setattr(ctrl, "_ask_password", lambda name: asked.append(name) or "orologi")
    rows = ctrl.compress_files()
    assert asked == ["listino.pdf"]
    assert rows and "errore" not in rows[0][3]


# ------------------------------------------------------------------ B-19
def test_b19_modifica_portfolio_nomi_accentati(window, ctrl, messages, tmp_path, monkeypatch):
    from foliant.modules.convert.portfolio_dialog import EditPortfolioDialog
    from PySide6.QtWidgets import QDialog
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Portfolio perizie", fontsize=12)
    doc.embfile_add("perizia.pdf", b"%PDF-1.4 finto", filename="perizia Rolex è.pdf",
                    ufilename="perizia Rolex è.pdf")
    # chiave scritta in UTF-8 come fanno altri programmi (letta «perizia Rolex Ã¨.pdf»)
    cat = doc.pdf_catalog()
    _t, v = doc.xref_get_key(cat, "Names/EmbeddedFiles/Names")
    spec = v.split("(perizia.pdf)", 1)[1][:-1]
    doc.xref_set_key(cat, "Names/EmbeddedFiles/Names", f"[<{'perizia Rolex è.pdf'.encode().hex()}>{spec}]")
    path = _save(doc, tmp_path / "portfolio.pdf")
    window.open_path(path)
    shown = []

    def fake_exec(dlg):
        shown.extend(dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount()))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(EditPortfolioDialog, "exec", fake_exec)
    ctrl.edit_portfolio()
    assert shown == ["perizia Rolex è.pdf"]
