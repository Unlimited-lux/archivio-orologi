"""OCR-10: tabelle con bordi, zone manuali (ignora, ordine, lingua), modelli di zone."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.modules.ocr import engine, layout

from .conftest import image_pdf


def _page(draw, dpi=300):
    doc = pymupdf.open()
    page = doc.new_page()
    draw(page)
    pix = page.get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()


CELLS = [["Articolo", "Quantità", "Prezzo", "Note"],
         ["Orologio", "4", "1200", "acciaio"],
         ["Cronografo", "2", "3450", "oro"],
         ["Cinturino", "10", "80", "pelle"],
         ["Scatola", "3", "45", "legno"]]


def _table(page):
    xs = [60, 200, 300, 400, 540]
    y0 = 120
    for r, row in enumerate(CELLS):
        for c, cell in enumerate(row):
            page.insert_text((xs[c] + 8, y0 + r * 28 + 19), cell, fontsize=12)
    for x in xs:
        page.draw_line((x, y0), (x, y0 + 28 * len(CELLS)), width=1)
    for r in range(len(CELLS) + 1):
        page.draw_line((60, y0 + r * 28), (540, y0 + r * 28), width=1)


def test_tabella_con_bordi_4x5(tmp_path):
    img = _page(_table)
    with pymupdf.open(image_pdf(tmp_path / "t.pdf", [img])) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    assert len(res.tables) == 1
    t = res.tables[0]
    assert t.shape == (5, 4)
    good = sum(engine.char_accuracy(t.cells[r][c], CELLS[r][c]) >= 0.95 for r in range(5) for c in range(4))
    assert good / 20 >= 0.95, t.cells
    # le celle sono blocchi distinti: nessuna riga unisce celle diverse
    assert "Articolo" in res.text and not any("Articolo" in ln and "Quantità" in ln for ln in res.text.splitlines())


def test_zona_da_ignorare_e_ordine(tmp_path):
    def draw(page):
        page.insert_text((60, 100), "Primo paragrafo in alto a sinistra", fontsize=12)
        page.insert_text((60, 400), "Secondo paragrafo in basso", fontsize=12)
        page.insert_text((380, 250), "TIMBRO RICEVUTO", fontsize=16)
    img = _page(draw)
    zones = [layout.Zone((0.6, 0.25, 0.95, 0.35), layout.IGNORE),
             layout.Zone((0.05, 0.42, 0.9, 0.52), layout.TEXT, order=0),
             layout.Zone((0.05, 0.05, 0.9, 0.15), layout.TEXT, order=1)]
    with pymupdf.open(image_pdf(tmp_path / "z.pdf", [img])) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], zones=zones)).pages[0]
    text = res.text
    assert "TIMBRO" not in text and "RICEVUTO" not in text
    assert text.index("Secondo paragrafo") < text.index("Primo paragrafo")


def test_zona_con_lingua_propria(tmp_path):
    def draw(page):
        page.insert_text((60, 100), "Il contratto è firmato a Milano", fontsize=12)
        page.insert_text((60, 200), "The contract is signed in Milan", fontsize=12)
    img = _page(draw)
    zones = [layout.Zone((0.05, 0.2, 0.9, 0.28), layout.TEXT, languages=["eng"])]
    with pymupdf.open(image_pdf(tmp_path / "l.pdf", [img])) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], zones=zones)).pages[0]
    assert "The contract is signed in Milan" in res.text and "Il contratto è firmato" in res.text
    with pytest.raises(Exception) as e:
        engine.recognize_document(pymupdf.open(image_pdf(tmp_path / "l2.pdf", [img])),
                                  engine.OcrOptions(languages=["ita"], zones=[layout.Zone((0, 0, 1, 1), languages=["deu"])]))
    assert "tedesco" in str(e.value)


def test_modello_di_zone_su_piu_pagine(tmp_path):
    imgs = []
    for n in range(3):
        def draw(page, n=n):
            page.insert_text((60, 400), f"Totale fattura numero {n}", fontsize=12)
            page.insert_text((60, 100), f"Cliente numero {n}", fontsize=12)
        imgs.append(_page(draw, dpi=200))
    zones = [layout.Zone.from_dict(z) for z in [
        {"rect": [0.05, 0.42, 0.9, 0.52], "kind": "text", "order": 0},
        {"rect": [0.05, 0.05, 0.9, 0.15], "kind": "text", "order": 1}]]
    with pymupdf.open(image_pdf(tmp_path / "m.pdf", imgs, dpi=200)) as pdf:
        result = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], zones=zones))
    for n, res in enumerate(result.pages):
        assert res.text.index(f"Totale fattura numero {n}") < res.text.index(f"Cliente numero {n}")
    with pytest.raises(ValueError):
        layout.Zone.from_dict({"rect": [0, 0, 1, 1], "kind": "boh"})


def test_ordine_nelle_celle_con_simboli():
    """Il simbolo «€» resta dove è nell'immagine (prima o dopo il numero), anche se il suo centro
    verticale differisce da quello delle cifre."""
    from foliant.modules.ocr.tess import Word
    num = Word("85,50", (100, 200, 160, 230), 95)
    eur = Word("€", (170, 196, 182, 226), 90)          # centro più in alto di 4 px
    assert [w.text for w in layout.reading_order([eur, num])] == ["85,50", "€"]
    eur2 = Word("€", (40, 205, 52, 235), 90)
    assert [w.text for w in layout.reading_order([num, eur2])] == ["€", "85,50"]
    second = Word("riga2", (40, 260, 90, 290), 90)
    assert [w.text for w in layout.reading_order([second, eur, num])] == ["85,50", "€", "riga2"]


@pytest.mark.parametrize("mode", ["editable", "searchable"])
def test_importi_con_euro_in_tabella(tmp_path, mode):
    """«85,50 €» e «€ 9.500,00» in una tabella a 300 dpi: ordine del testo e riquadri come nell'immagine."""
    sans = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    amounts = ["85,50 €", "€ 9.500,00", "1.250,00 €", "€ 45,90", "12,00 €"]

    def draw(page):
        page.insert_font(fontname="S", fontfile=sans)
        xs, y0 = [72, 260, 450], 150
        for r, (a, b) in enumerate([("Articolo", "Prezzo")] + [(f"Voce {i}", v) for i, v in enumerate(amounts)]):
            page.insert_text((xs[0] + 8, y0 + r * 26 + 18), a, fontname="S", fontsize=12)
            page.insert_text((xs[1] + 8, y0 + r * 26 + 18), b, fontname="S", fontsize=12)
        for x in xs:
            page.draw_line((x, y0), (x, y0 + 26 * (len(amounts) + 1)))
        for r in range(len(amounts) + 2):
            page.draw_line((72, y0 + r * 26), (450, y0 + r * 26))
    ref_doc = pymupdf.open()
    draw(ref_doc.new_page())
    ref_words = ref_doc[0].get_text("words")
    img = _page(draw)
    out = engine.ocr_pdf_bytes(open(image_pdf(tmp_path / "e.pdf", [img]), "rb").read(), ["ita"], mode)
    with pymupdf.open(stream=out) as d:
        text = d[0].get_text()
        for a in amounts:
            assert a in text, (a, text)
        got = d[0].get_text("words")
    from .conftest import iou
    for rw in ref_words:
        if rw[4] == "€":
            best = max((iou(rw[:4], g[:4]) for g in got if g[4] == "€"), default=0)
            assert best >= 0.4, rw
