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

from .test_ui import block_with, ctrl_of, editor_lines, open_in_edit_mode, quiet  # noqa: F401 - fixture

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


_PADS = ["", " vigente", " come convenuto", " per ciascun esemplare", " secondo le condizioni generali",
         " e nel relativo elenco", " di cui sopra", " firmato in originale", " nonché nella scheda tecnica",
         " in duplice copia", " a ogni effetto di legge", " sopra indicato", " con ogni accessorio"]


def contract_articles() -> list[str]:
    """15 articoli di un contratto di compravendita: 30 «Bianchi & Figli», 30 «€ 24.800,00» e 30 «prezzo
    pattuito», in punti diversi delle righe (così alcune occorrenze vanno a capo)."""
    out = []
    for n in range(1, 16):
        a, b, c = (_PADS[(n * 3 + 1) % 13], _PADS[(n * 5 + 3) % 13], _PADS[(n * 7 + 5) % 13])
        out.append(
            f"Art. {n}. Con il presente atto la società Bianchi & Figli, con sede in Milano, via della Spiga 12, "
            f"cede all'acquirente{a}, che accetta, gli orologi descritti nell'allegato A, rappresentata dalla "
            f"sig.ra Laura Bianchi, al prezzo pattuito di € 24.800,00, comprensivo di revisione completa del "
            f"movimento, sostituzione delle guarnizioni e verifica dell'impermeabilità{b}; il pagamento, che deve "
            f"andare a buon fine entro trenta giorni dalla consegna, avverrà mediante bonifico sul conto "
            f"intestato a Bianchi & Figli presso la filiale indicata in fattura. Le parti convengono che il "
            f"prezzo pattuito{c} di € 24.800,00 resta fermo anche in caso di variazione delle quotazioni di "
            f"mercato degli orologi da collezione, e che ogni intervento successivo alla consegna sarà regolato "
            f"da separato accordo scritto tra le parti, con l'indicazione dei pezzi sostituiti e delle relative "
            f"garanzie del costruttore.")
    return out


@pytest.fixture(scope="module")
def contratto(tmp_path_factory) -> Path:
    """Contratto di compravendita di più pagine scritto con Writer (python-docx con il modello predefinito:
    Letter, righe di 432 pt) e paragrafi giustificati, esportato in PDF da LibreOffice."""
    soffice = find_tool("soffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    docx = pytest.importorskip("docx")
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    out = tmp_path_factory.mktemp("contratto")
    doc = docx.Document()
    doc.styles["Normal"].font.name = "DejaVu Serif"
    doc.styles["Normal"].font.size = Pt(12)
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("CONTRATTO DI COMPRAVENDITA DI OROLOGI DA COLLEZIONE").bold = True
    for text in contract_articles():
        doc.add_paragraph(text).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    src = out / "contratto.docx"
    doc.save(str(src))
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{out}/lo", "--convert-to",
                    "pdf:writer_pdf_Export", "--outdir", str(out), str(src)], capture_output=True, timeout=180)
    pdf = out / "contratto.pdf"
    if not pdf.exists():
        pytest.skip("conversione LibreOffice non riuscita")
    return pdf


def test_contract_second_replace_all_finds_every_occurrence(contratto, tmp_path):
    """E-1: dopo «Bianchi & Figli» → «Ferretti Orologi d'Epoca» (30/30) il secondo «Sostituisci tutto»
    trova e sostituisce tutti i 30 «€ 24.800,00», anche quelli che nei paragrafi riscritti vanno a capo;
    lo stesso per Trova (Ctrl+F), che legge il testo nell'ordine del contenuto della pagina."""
    from foliant.modules.viewer.engine import SearchOptions as FindOptions
    from foliant.modules.viewer.engine import extract_page_text, find_in_page_text

    d = Document.open(str(contratto))
    opts = search.SearchOptions(case_sensitive=True)
    assert len(search.find_in_document(d, "€ 24.800,00", opts)) == 30
    first = search.replace_all(d, "Bianchi & Figli", "Ferretti Orologi d'Epoca", opts)
    assert first.replaced == 30
    for phrase in ("€ 24.800,00", "prezzo pattuito"):
        assert len(search.find_in_document(d, phrase, opts)) == 30, phrase
        with d.lock:
            pages = [extract_page_text(p) for p in d.pdf]
        assert sum(len(find_in_page_text(pt, phrase, FindOptions(case_sensitive=True))) for pt in pages) == 30
    second = search.replace_all(d, "€ 24.800,00", "€ 25.900,00", opts)
    assert second.replaced == 30 and second.not_replaceable == []
    out = tmp_path / "contratto.pdf"
    d.save(out)
    with pymupdf.open(str(out)) as pdf:
        text = " ".join(" ".join(p.get_text() for p in pdf).split())
    assert text.count("24.800,00") == 0 and text.count("25.900,00") == 30
    assert text.count("Ferretti Orologi d'Epoca") == 30 and "Bianchi & Figli" not in text


@pytest.mark.parametrize("zoom", [1.0, 1.5, 2.0])
def test_contract_editor_wraps_where_the_pdf_will(contratto, window, quiet, qtbot, tmp_path, zoom):  # noqa: F811
    """E-4: durante la scrittura il riquadro va a capo esattamente dove andrà a capo il PDF salvato
    (paragrafo giustificato di 432 pt, a zoom 1,0, 1,5 e 2,0)."""
    from PySide6.QtGui import QTextCursor

    path = tmp_path / "contratto.pdf"
    path.write_bytes(contratto.read_bytes())
    view = open_in_edit_mode(window, str(path))
    view.set_zoom(zoom)
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "Art. 1.")
    assert block.align == "justify" and abs(block.width - 432) < 3
    ed = ctrl.open_editor(view, 0, block)
    qtbot.wait(20)
    # senza modifiche: le righe del riquadro sono quelle della pagina
    assert editor_lines(ed) == [" ".join(ln.text.split()) for ln in block.lines]
    cur = ed.textCursor()
    cur.setPosition(len("Art. 1. "))
    ed.setTextCursor(cur)
    qtbot.keyClicks(ed, "Il venditore dichiara che ogni orologio che deve andare all'acquirente sia autentico e ")
    cur = ed.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    ed.setTextCursor(cur)
    qtbot.keyClicks(ed, " Le spese di spedizione assicurata sono a carico del venditore.")
    qtbot.wait(20)
    typed = editor_lines(ed)
    ctrl.commit_editor(view)
    with view.document.lock:
        page = view.document.pdf[0]
        new = [b for b in analysis.analyze_page(page, 0) if b.text.startswith("Art. 1.")][0]
    assert typed == [" ".join(ln.text.split()) for ln in new.lines]
    assert new.align == "justify" and abs(new.x0 - block.x0) < 0.3
    assert abs(new.lines[0].baseline - block.lines[0].baseline) < 0.1
    # Trova e sostituisci dopo la modifica con l'editor: le frasi a cavallo di due righe si trovano
    opts = search.SearchOptions(case_sensitive=True)
    found = search.find_in_document(view.document, "€ 24.800,00", opts, pages=[0])
    assert len(found) == " ".join(view.document.page_text(0).split()).count("€ 24.800,00") >= 2


def words(page) -> dict:
    """(parola, riga, n-esima uguale da sinistra) -> riquadro: non dipende dall'ordine del contenuto (le righe
    riscritte finiscono in fondo, dopo quelle dell'altra colonna)."""
    out, seen = {}, {}
    for w in sorted(page.get_text("words"), key=lambda w: w[0]):
        base = (w[4], round(w[3]))
        seen[base] = seen.get(base, -1) + 1
        out[base + (seen[base],)] = w[:4]
    return out


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
