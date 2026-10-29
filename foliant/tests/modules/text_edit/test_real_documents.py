"""Verifica su documenti reali generati con LibreOffice (listino con tabella e testo su due colonne)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest

from foliant.core.platform import find_tool
from foliant.api import Document
from foliant.modules.text_edit import analysis, engine, search
from foliant.modules.text_edit.model import Run

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def listino(tmp_path_factory) -> Path:
    soffice = find_tool("soffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    out = tmp_path_factory.mktemp("listino")
    src = out / "listino.html"
    src.write_text((HERE / "listino.html").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{out}/lo", "--convert-to",
                    "pdf:writer_pdf_Export", "--outdir", str(out), str(src)], capture_output=True, timeout=180)
    pdf = out / "listino.pdf"
    if not pdf.exists():
        pytest.skip("conversione LibreOffice non riuscita")
    return pdf


def words(page) -> dict:
    return {(w[4], round(w[3])): w[:4] for w in page.get_text("words")}


def fonts(page) -> set:
    return {f[0] for f in page.get_fonts(full=True)}


def test_listino_analysis(listino):
    blocks = analysis.analyze_page(pymupdf.open(str(listino))[0], 0)
    texts = [b.text for b in blocks]
    assert "€ 9.500,00" in texts and "Rolex Submariner Date" in texts and "2005" in texts
    cols = [b for b in blocks if b.text.startswith(("Tutti gli orologi", "Spedizione assicurata"))]
    assert len(cols) == 2
    for b in cols:
        assert b.align == "justify"
        assert len({round(ln.bbox.x0) for ln in b.lines}) == 1      # nessuna riga attraverso la canaletta


def test_listino_price_change_touches_only_the_cell(listino):
    d = Document.open(str(listino))
    with d.lock:
        before = words(d.pdf[0])
        f0 = fonts(d.pdf[0])
    s = search.replace_all(d, "9.500,00", "9.900,00", search.SearchOptions(case_sensitive=True))
    assert s.replaced == 1
    with d.lock:
        page = d.pdf[0]
        after = words(page)
        assert fonts(page) == f0                                       # nessun font aggiunto
        cell = page.search_for("9.900,00")[0]
    assert abs(cell.x1 - 564.8) <= 1.0                                 # prezzo ancora allineato a destra
    for key, r in before.items():
        if key[0] in ("9.500,00",):
            continue
        assert key in after and abs(after[key][0] - r[0]) < 0.2, key


def test_listino_justified_column_edit(listino):
    d = Document.open(str(listino))
    with d.lock:
        page = d.pdf[0]
        right = [b for b in analysis.analyze_page(page, 0) if b.text.startswith("Spedizione")][0]
        left = [b for b in analysis.analyze_page(page, 0) if b.text.startswith("Tutti")][0]
        right_words = {k: v for k, v in words(page).items() if v[0] >= right.x0 - 1 and v[1] > 240}
    new = [[Run(r.text.replace("ventiquattro", "trentasei"), r.style) for r in p] for p in left.paragraphs]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, left, new)
    with d.lock:
        page = d.pdf[0]
        after = words(page)
        nb = [b for b in analysis.analyze_page(page, 0) if b.text.startswith("Tutti")][0]
    assert "trentasei" in nb.text and nb.align == "justify"
    for ln in nb.lines:
        assert ln.bbox.x1 <= left.x1 + 0.6                            # resta nella sua colonna
    for key, r in right_words.items():
        assert key in after and abs(after[key][0] - r[0]) < 0.2, key  # l'altra colonna non si muove


def test_listino_add_row(listino):
    d = Document.open(str(listino))
    with d.lock:
        page = d.pdf[0]
        model_cell = [b for b in analysis.analyze_page(page, 0) if b.text == "Omega Speedmaster Professional"][0]
    st = model_cell.style
    with d.change("Nuova riga") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(model_cell.x0, model_cell.bbox.y1 + 4),
                        [[Run("Tudor Black Bay 58", st)]])
    with d.lock:
        page = d.pdf[0]
        s = [sp for b in page.get_text("dict")["blocks"] for ln in b["lines"] for sp in ln["spans"]
             if "Tudor" in sp["text"]][0]
    assert abs(s["size"] - st.size) < 0.05 and s["font"].startswith("Carlito")
    assert abs(s["bbox"][0] - model_cell.x0) <= 1


@pytest.fixture(scope="module")
def chrome_invoice(tmp_path_factory) -> Path:
    """Fattura stampata in PDF da Chromium (font Type0 Identity-H in sottoinsieme, come browser e gestionali)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node non disponibile")
    out = tmp_path_factory.mktemp("chrome")
    html = out / "fattura.html"
    html.write_text((HERE / "fattura.html").read_text(encoding="utf-8"), encoding="utf-8")
    pdf = out / "fattura.pdf"
    script = out / "stampa.js"
    script.write_text(
        "const { chromium } = require('playwright');(async () => {const b = await chromium.launch();"
        f"const p = await b.newPage(); await p.goto('file://{html}');"
        f"await p.pdf({{ path: '{pdf}', format: 'A4' }}); await b.close();}})();", encoding="utf-8")
    import os
    env = dict(os.environ)
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
        env["NODE_PATH"] = root
    except Exception:
        pass
    subprocess.run([node, str(script)], capture_output=True, timeout=120, env=env)
    if not pdf.exists():
        pytest.skip("stampa con Chromium non riuscita")
    return pdf


def test_chrome_invoice_cells_and_edits(chrome_invoice):
    d = Document.open(str(chrome_invoice))
    with d.lock:
        page = d.pdf[0]
        blocks = analysis.analyze_page(page, 0)
        f0 = fonts(page)
        before = words(page)
    # ogni cella è un blocco a sé (le celle di una colonna non diventano un paragrafo)
    assert all(len(b.lines) == 1 for b in blocks if "Revisione" in b.text or "Rolex" in b.text)
    s = search.replace_all(d, "9.500,00", "9.900,00", search.SearchOptions(case_sensitive=True))
    assert s.replaced == 1
    with d.lock:
        page = d.pdf[0]
        assert fonts(page) == f0
        cell = [b for b in analysis.analyze_page(page, 0) if b.text.startswith("Revisione")][0]
    new = [[Run("Revisione completa e lucidatura della cassa", r.style) for r in cell.paragraphs[0][:1]]]
    with d.change("Modifica cella") as pdf:
        engine.replace_block(pdf, 0, cell, new)
    with d.lock:
        after = words(d.pdf[0])
    # le altre righe e colonne non si spostano
    for key, r in before.items():
        if key[0] in ("Revisione", "e", "lucidatura", "9.500,00"):
            continue
        assert key in after and abs(after[key][0] - r[0]) < 0.2, key
