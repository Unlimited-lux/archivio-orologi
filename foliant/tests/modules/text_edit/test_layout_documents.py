"""Allineamento e celle nei documenti d'ufficio di un'azienda di orologi (generati da
`documenti_orologi.py`):

- righe sole centrate o a destra fuori dalle tabelle (fattura e certificato di garanzia di Writer, contratto
  di 5 pagine con paragrafi giustificati): il testo modificato resta centrato o sul margine destro;
- PDF stampato da Chromium: due celle vicine scritte nella stessa riga del motore diventano due blocchi;
- celle di Writer su due righe: un solo paragrafo che va a capo dentro la cella.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtGui import QTextCursor

from foliant.modules.text_edit.analysis import EXTRACT_FLAGS, analyze_page, page_borders
from foliant.modules.text_edit.engine import replace_block
from foliant.modules.text_edit.model import ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, Run

from .conftest import SANS
from .test_ui import block_with, ctrl_of, open_in_edit_mode, quiet  # noqa: F401 - fixture

HERE = Path(__file__).parent
FATTURA = HERE / "fattura_writer.pdf"
CONTRATTO = HERE / "contratto_writer.pdf"
CERTIFICATO = HERE / "certificato_garanzia.pdf"
LISTINO = HERE / "listino_browser.pdf"
MARGIN_RIGHT = 522.0          # pagina Letter con i margini predefiniti di Word (90 pt)
PAGE_CENTER = 306.0


def _block(doc, text, page=0):
    return next(b for b in analyze_page(doc[page], page) if text in b.text)


def _chars(page, text):
    """(riquadro, linea di base) dei caratteri che formano `text` in una riga del PDF."""
    raw = page.get_text("rawdict", flags=EXTRACT_FLAGS)
    for b in raw["blocks"]:
        for line in b.get("lines", ()):
            chars = [c for s in line["spans"] for c in s["chars"]]
            joined = "".join(c["c"] for c in chars)
            k = joined.find(text)
            if k >= 0:
                hit = chars[k:k + len(text)]
                rect = pymupdf.Rect(hit[0]["bbox"])
                for c in hit[1:]:
                    rect |= pymupdf.Rect(c["bbox"])
                return rect, hit[0]["origin"][1]
    raise AssertionError(text)


def _edit(path, tmp_path, text, new_text, page=0, *, append=False):
    """Sostituisce il testo del blocco che contiene `text` e rilegge il PDF salvato."""
    doc = pymupdf.open(str(path))
    block = _block(doc, text, page)
    style = block.paragraphs[0][0].style
    paragraphs = [[Run(block.text + new_text if append else new_text, style)]]
    replace_block(doc, page, block, paragraphs)
    out = tmp_path / "out.pdf"
    doc.save(str(out))
    return block, pymupdf.open(str(out))


# ---------------------------------------------------------------- righe sole centrate o a destra (E-2)
def test_invoice_number_right_aligned_and_edit_stays_on_margin(tmp_path):
    doc = pymupdf.open(str(FATTURA))
    assert _block(doc, "Fattura n. 2026/0147").align == ALIGN_RIGHT
    for text in ("Totale imponibile", "Totale fattura"):
        assert _block(doc, text).align == ALIGN_RIGHT, text
    # le righe del mittente iniziano sul margine sinistro: restano a sinistra
    for text in ("Orologeria Bellini", "Via Montenapoleone"):
        assert _block(doc, text).align == ALIGN_LEFT, text
    _rect, baseline = _chars(doc[0], "Fattura n. 2026/0147 del 03/11/2026")
    _b, saved = _edit(FATTURA, tmp_path, "Fattura n. 2026/0147", " – saldata", append=True)
    rect, new_baseline = _chars(saved[0], "Fattura n. 2026/0147 del 03/11/2026 – saldata")
    assert abs(rect.x1 - MARGIN_RIGHT) <= 0.6          # cresce verso sinistra, non oltre il margine destro
    assert abs(new_baseline - baseline) < 0.3


def test_contract_title_centered_and_edit_keeps_center(tmp_path):
    doc = pymupdf.open(str(CONTRATTO))
    assert doc.page_count == 5
    title = _block(doc, "CONTRATTO DI COMPRAVENDITA")
    assert title.align == ALIGN_CENTER
    for text in ("tra", "e"):
        assert next(b for b in analyze_page(doc[0], 0) if b.text == text).align == ALIGN_CENTER, text
    # paragrafi giustificati e titoli degli articoli invariati, su tutte le pagine
    for p in range(doc.page_count):
        for b in analyze_page(doc[p], p):
            if b.text.startswith("Art. ") or b.text.startswith("Milano,"):
                assert b.align == ALIGN_LEFT, b.text
            elif len(b.lines) >= 3:
                assert b.align == ALIGN_JUSTIFY, b.text[:40]
    last = doc.page_count - 1
    for text in ("Il Venditore", "L'Acquirente"):
        assert next(b for b in analyze_page(doc[last], last) if b.text == text).align == ALIGN_RIGHT, text
    _rect, baseline = _chars(doc[0], "CONTRATTO DI COMPRAVENDITA DI OROLOGIO")
    _b, saved = _edit(CONTRATTO, tmp_path, "CONTRATTO DI COMPRAVENDITA", " – COPIA", append=True)
    rect, new_baseline = _chars(saved[0], "CONTRATTO DI COMPRAVENDITA DI OROLOGIO – COPIA")
    assert abs((rect.x0 + rect.x1) / 2 - PAGE_CENTER) <= 0.6
    assert abs(new_baseline - baseline) < 0.3


def test_warranty_certificate_alignments(tmp_path):
    doc = pymupdf.open(str(CERTIFICATO))
    expected = {
        "CERTIFICATO DI GARANZIA": ALIGN_CENTER,
        "Laboratorio di assistenza tecnica": ALIGN_CENTER,
        "Data: 03/11/2026": ALIGN_RIGHT,
        "Il responsabile tecnico": ALIGN_RIGHT,
        "Pagina 1 di 1": ALIGN_CENTER,
        "Marca: Omega": ALIGN_LEFT,
        "Modello: Speedmaster": ALIGN_LEFT,
        "Cliente: Marco Ferrari": ALIGN_LEFT,
    }
    for text, align in expected.items():
        assert _block(doc, text).align == align, text
    _rect, baseline = _chars(doc[0], "Data: 03/11/2026")
    _b, saved = _edit(CERTIFICATO, tmp_path, "Data: 03/11/2026", "Data: 3 novembre 2026")
    rect, new_baseline = _chars(saved[0], "Data: 3 novembre 2026")
    assert abs(rect.x1 - MARGIN_RIGHT) <= 0.6 and abs(new_baseline - baseline) < 0.3


def _fit(page, x, y, text, width):
    """Scrive `text` con un corpo tale da occupare esattamente `width` punti."""
    size = width / pymupdf.Font(fontfile=SANS).text_length(text, fontsize=1)
    page.insert_text((x, y), text, fontname="F1", fontsize=size)


def test_single_lines_stay_left_when_in_doubt(tmp_path):
    """Falsi positivi da evitare: elenchi, righe a un rientro comune (tabulazioni) anche se per caso
    centrate o finite sul margine destro, numeri di pagina a sinistra, righe con altro testo accanto."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F1", fontfile=SANS)
    left, right = 72.0, 523.0
    y = 90
    for _k in range(5):                                   # corpo del testo: margini 72 e 523
        _fit(page, left, y, "Testo del documento con una riga lunga che va da un margine all'altro", right - left)
        y += 15
    y += 10
    for item in ("• Cassa in acciaio", "• Vetro zaffiro", "1. Revisione", "a) Lucidatura"):
        page.insert_text((150, y), item, fontname="F1", fontsize=11)
        y += 15
    # due righe a un rientro comune: una per caso centrata sulla pagina, l'altra per caso sul margine destro
    _fit(page, 250, y, "Firma del cliente", 2 * (297.5 - 250))
    _fit(page, 250, y + 15, "Luogo e data della consegna", right - 250)
    y += 45
    page.insert_text((left, y), "Totale", fontname="F1", fontsize=11)        # con altro testo accanto
    _fit(page, 470, y, "€ 100,00", right - 470)
    y += 30
    # paragrafo a sinistra (fine delle righe irregolare): tre righe finiscono quasi nello stesso punto, e
    # lì finisce anche una riga sola rientrata; non è un margine destro
    for k, end in enumerate((470, 400.0, 455, 400.4, 430, 400.8, 480, 445, 420, 460, 410, 475)):
        _fit(page, left, y + 14 * k, f"Riga {k} del paragrafo allineato a sinistra con fine irregolare", end - left)
    _fit(page, 200, y + 14 * 12 + 30, "Riga rientrata sola", 400.5 - 200)
    page.insert_text((left, 800), "3", fontname="F1", fontsize=10)          # numero di pagina a sinistra
    path = tmp_path / "dubbi.pdf"
    doc.save(str(path))
    blocks = analyze_page(pymupdf.open(str(path))[0], 0)
    for text in ("Cassa", "Vetro", "Revisione", "Lucidatura", "Firma del cliente", "Luogo e data", "Totale",
                 "€ 100,00", "Riga rientrata sola", "3"):
        block = next(b for b in blocks if text in b.text)
        assert block.align == ALIGN_LEFT, text


# ---------------------------------------------------------------- celle vicine nella stessa riga (E-3)
def test_browser_price_list_cells_are_separate_blocks():
    doc = pymupdf.open(str(LISTINO))
    blocks = analyze_page(doc[0], 0)
    texts = [b.text for b in blocks]
    for price in ("€ 8.900,00", "€ 9.750,00", "€ 7.400,00", "€ 6.300,00", "€ 3.150,00"):
        assert price in texts, price                     # non «2023 € 8.900,00»
        assert next(b for b in blocks if b.text == price).align == ALIGN_RIGHT, price
    for year in ("2023", "2022", "2018", "2021", "2020"):
        assert year in texts, year
    assert "Referenza" in texts and "Modello" in texts    # intestazioni separate
    assert next(b for b in blocks if b.text == "Prezzo").align == ALIGN_RIGHT


@pytest.mark.parametrize("new_price", ["€ 8.950,00", "€ 890,00"])
def test_browser_price_edit_stays_in_its_cell(tmp_path, new_price):
    doc = pymupdf.open(str(LISTINO))
    year_rect, _ = _chars(doc[0], "2023")
    price_rect, baseline = _chars(doc[0], "€ 8.900,00")
    border = min(x for x, y0, y1 in page_borders(doc[0])[0]
                 if year_rect.x1 < x < price_rect.x0 and y0 <= baseline <= y1)
    _b, saved = _edit(LISTINO, tmp_path, "€ 8.900,00", new_price)
    rect, new_baseline = _chars(saved[0], new_price)
    assert abs(rect.x1 - price_rect.x1) <= 0.6           # allineato a destra nella cella
    assert rect.x0 > border + 0.5                        # il «€» non arriva sul bordo tra le celle
    assert abs(new_baseline - baseline) < 0.3
    after_year, _ = _chars(saved[0], "2023")             # la cella Anno resta com'era
    assert abs(after_year.x0 - year_rect.x0) < 0.2 and abs(after_year.x1 - year_rect.x1) < 0.2


def _table_row_pdf(tmp_path, borders: bool) -> str:
    """Tabella simulata con PyMuPDF: le celle Anno e Prezzo di ogni riga sono scritte con un solo comando di
    testo (come i browser), separate da pochi punti."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F1", fontfile=SANS)
    font = pymupdf.Font(fontfile=SANS)
    x_year, x_border, x_right = 300.0, 326.0, 390.0
    rows = [("2023", "€ 8.900,00"), ("2022", "€ 9.750,00"), ("2018", "€ 7.400,00"), ("2020", "€ 3.150,00")]
    y = 100.0
    for year, price in rows:
        # «2023» e il prezzo nella stessa istruzione di testo, allineato a destra con spazi
        gap = x_right - font.text_length(price, 10) - (x_year + font.text_length(year, 10))
        spaces = max(1, round(gap / font.text_length(" ", 10)))
        page.insert_text((x_year, y), year + " " * spaces + price, fontname="F1", fontsize=10)
        if borders:
            page.draw_rect(pymupdf.Rect(296, y - 11, x_border, y + 4), color=(0, 0, 0), width=0.5)
            page.draw_rect(pymupdf.Rect(x_border, y - 11, x_right + 4, y + 4), color=(0, 0, 0), width=0.5)
        y += 15
    path = tmp_path / ("bordi.pdf" if borders else "senza_bordi.pdf")
    doc.save(str(path))
    return str(path)


@pytest.mark.parametrize("borders", [True, False])
def test_simulated_cells_in_one_text_line_are_split(tmp_path, borders):
    doc = pymupdf.open(_table_row_pdf(tmp_path, borders))
    texts = [b.text for b in analyze_page(doc[0], 0)]
    assert "2023" in texts and "€ 8.900,00" in texts and "€ 3.150,00" in texts, texts


def test_paragraph_with_one_wide_space_not_split(tmp_path):
    """Uno spazio largo in una sola riga di un paragrafo (dopo il punto) non è un corridoio tra colonne."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F1", fontfile=SANS)
    font = pymupdf.Font(fontfile=SANS)
    lines = ["Il Venditore garantisce che l'orologio è originale e funzionante.",
             "La garanzia copre il movimento. Sono esclusi urti e infiltrazioni.",
             "Il cinturino e il vetro non sono coperti dalla presente garanzia.",
             "Per ogni intervento rivolgersi al laboratorio della sede di Milano."]
    y = 100
    for row, text in enumerate(lines):
        x = 72.0
        for w in text.split(" "):
            page.insert_text((x, y), w, fontname="F1", fontsize=11)
            x += font.text_length(w, 11) + (10.0 if row == 1 and w.endswith(".") else 4.0)
        y += 14
    path = tmp_path / "paragrafo.pdf"
    doc.save(str(path))
    blocks = analyze_page(pymupdf.open(str(path))[0], 0)
    assert len(blocks) == 1 and len(blocks[0].lines) == 4


# ---------------------------------------------------------------- celle su due righe (E-5)
def test_writer_cell_on_two_lines_is_one_paragraph():
    doc = pymupdf.open(str(FATTURA))
    cell = _block(doc, "Rolex Submariner")
    assert cell.text == "Rolex Submariner 124060, acciaio, quadrante nero, anno 2021"
    assert len(cell.lines) == 2 and cell.align == ALIGN_LEFT and not cell.auto_width
    left, right = _cell_sides(doc, cell)
    pad = cell.x0 - left
    assert abs(cell.x0 + cell.width - (right - pad)) <= 0.6       # larghezza interna della cella
    # le altre celle della colonna restano blocchi a sé
    blocks = analyze_page(doc[0], 0)
    for text in ("Descrizione", "Cinturino in pelle di alligatore", "Revisione completa del movimento"):
        assert text in [b.text for b in blocks], text


def _cell_sides(doc, block):
    vertical, _h = page_borders(doc[0])
    y = (block.bbox.y0 + block.bbox.y1) / 2
    left = max(x for x, y0, y1 in vertical if x < block.x0 and y0 <= y <= y1)
    right = min(x for x, y0, y1 in vertical if x > block.bbox.x1 and y0 <= y <= y1)
    return left, right


def test_writer_cell_edit_wraps_inside_the_cell(tmp_path):
    doc = pymupdf.open(str(FATTURA))
    cell = _block(doc, "Rolex Submariner")
    left, right = _cell_sides(doc, cell)
    _r, baseline = _chars(doc[0], "Rolex Submariner")
    price_before, _ = _chars(doc[0], "€ 11.800,00")
    _b, saved = _edit(FATTURA, tmp_path, "Rolex Submariner", " – più", append=True)
    page = saved[0]
    _r, new_baseline = _chars(page, "Rolex Submariner")
    assert abs(new_baseline - baseline) < 0.3
    tail, _ = _chars(page, "più")
    assert tail.x1 <= right - 0.5                                   # non scavalca il bordo verticale
    for w in page.get_text("words"):
        if left < w[0] < right and cell.bbox.y0 - 1 < w[1] < cell.bbox.y1 + 12:
            assert w[2] <= right - 0.5, w[4]
    price_after, _ = _chars(page, "€ 11.800,00")
    assert abs(price_after.x0 - price_before.x0) < 0.2 and abs(price_after.y0 - price_before.y0) < 0.2


def test_editor_on_right_aligned_line_grows_left(window, quiet, qtbot, tmp_path):  # noqa: F811
    path = tmp_path / "fattura.pdf"
    path.write_bytes(FATTURA.read_bytes())
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Fattura n. 2026/0147")
    ed = ctrl.open_editor(view, 0, block)
    assert ed.align == ALIGN_RIGHT
    ed.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClicks(ed, " - saldata")
    ctrl.commit_editor(view)
    out = tmp_path / "out.pdf"
    view.document.save(out)
    rect, _baseline = _chars(pymupdf.open(str(out))[0], "03/11/2026 - saldata")
    assert abs(rect.x1 - MARGIN_RIGHT) <= 0.6


if __name__ == "__main__":
    pytest.main([__file__])
