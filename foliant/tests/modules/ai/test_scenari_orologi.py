"""Scenari d'uso di un'azienda di compravendita di orologi, con il fornitore simulato.

Fatture miste (italiano e inglese, scansioni con livello OCR), revisione di un contratto di
vendita, scheda prodotto da foto e testo, traduzione, documenti lunghi, errori e privacy.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time

import os

import pymupdf
import pytest

from foliant.modules.ai import extract

from .conftest import FakeProvider, make_active_profile

IT_INVOICE = """FATTURA n. {n} del {d}/03/2026
Fornitore: Orologeria Rossi S.r.l. - P.IVA 01234567897
Cliente: Unlimited Lux S.r.l.
Rolex Submariner 124060 - quantità 1 - prezzo € {net_it}
Imponibile € {net_it}
IVA 22% € {tax_it}
Totale € {tot_it}"""

EN_INVOICE = """INVOICE No. {n} - Date: March {d}, 2026
Seller: Geneva Watch Trading SA - VAT CHE-123.456.789
Buyer: Unlimited Lux S.r.l.
Omega Speedmaster 310.30.42 - qty 1 - price EUR {net_en}
Subtotal EUR {net_en}
VAT 0% EUR 0.00
Total EUR {tot_en}"""


def _it(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _en(v: float) -> str:
    return f"{v:,.2f}"


_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_ARGS = {"fontname": "dejavu", "fontfile": _FONT} if os.path.exists(_FONT) else {}   # con «€»


def _scan(text_doc: pymupdf.Document) -> pymupdf.Document:
    """Pagina «scansionata»: immagine della pagina + testo invisibile come dopo l'OCR."""
    out = pymupdf.open()
    src = text_doc[0]
    pix = src.get_pixmap(dpi=100)
    page = out.new_page(width=src.rect.width, height=src.rect.height)
    page.insert_image(page.rect, pixmap=pix)
    for b in src.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for span in line["spans"]:
                page.insert_text(span["origin"], span["text"], fontsize=span["size"], render_mode=3, **_FONT_ARGS)
    return out


def make_invoices(tmp_path, count=20):
    """Metà italiane e metà inglesi; una su quattro scansionata con OCR. Restituisce (percorsi, attesi)."""
    paths, expected = [], []
    for i in range(count):
        n = 100 + i
        net = 1000.0 + 250.5 * i
        lang = "it" if i % 2 == 0 else "en"
        if lang == "it":
            tax = round(net * 0.22, 2)
            text = IT_INVOICE.format(n=n, d=10 + i % 15, net_it=_it(net), tax_it=_it(tax), tot_it=_it(net + tax))
        else:
            tax = 0.0
            text = EN_INVOICE.format(n=n, d=10 + i % 15, net_en=_en(net), tot_en=_en(net))
        doc = pymupdf.open()
        if not _FONT_ARGS:
            text = text.replace("€", "EUR")
        doc.new_page().insert_textbox(pymupdf.Rect(50, 60, 545, 400), text, fontsize=11, **_FONT_ARGS)
        scanned = i % 4 == 3
        if scanned:
            doc = _scan(doc)
        path = tmp_path / f"fattura-{lang}-{n}-{'scan' if scanned else 'pdf'}.pdf"
        doc.save(str(path))
        paths.append(str(path))
        expected.append(dict(numero=str(n), lang=lang, net=net, tax=tax, total=round(net + tax, 2), scanned=scanned))
    return paths, expected


def invoice_reply(req) -> str:
    """Fornitore simulato: legge numero e importi dal testo inviato, come li scriverebbe il modello."""
    text = req.messages[-1].content
    num = re.search(r"(?:FATTURA n\.|INVOICE No\.)\s*(\d+)", text).group(1)
    if "FATTURA" in text:
        net = re.search(r"Imponibile (?:€|EUR) ([\d.,]+)", text).group(1)
        tax = re.search(r"IVA 22% (?:€|EUR) ([\d.,]+)", text).group(1)
        tot = re.search(r"Totale (?:€|EUR) ([\d.,]+)", text).group(1)
        rec = {"numero": num, "data": "2026-03-10", "fornitore": "Orologeria Rossi S.r.l.",
               "partita_iva": "01234567897", "imponibile": net, "imposta": tax, "totale": tot, "valuta": "EUR"}
    else:
        net = re.search(r"Subtotal EUR ([\d.,]+)", text).group(1)
        tot = re.search(r"Total EUR ([\d.,]+)", text).group(1)
        rec = {"numero": num, "data": "2026-03-10", "fornitore": "Geneva Watch Trading SA",
               "imponibile": net, "imposta": "0.00", "totale": tot, "valuta": "EUR"}
    rec["_page"] = 1
    return json.dumps({"records": [rec]})


@pytest.fixture
def services(window):
    fake = FakeProvider()
    s = window.ctx.ai_services
    s.provider_factory = lambda p: fake
    make_active_profile(s.store)
    s.refresh()
    s.fake = fake
    s.controller.exec_dialog = lambda dlg: 0
    return s


# ------------------------------------------------------------------ fatture
def test_twenty_mixed_invoices(window, services, tmp_path, monkeypatch):
    paths, expected = make_invoices(tmp_path)
    fake = services.fake
    fake.default = invoice_reply
    notes = []
    monkeypatch.setattr(window.ctx, "notify", lambda text, actions=(), duration_ms=None: notes.append(
        (text, list(actions))))
    t0 = time.monotonic()
    task = services.controller.extract_invoice_files(paths)
    assert task.wait() and task.error is None
    elapsed = time.monotonic() - t0
    assert elapsed < 20, f"20 fatture in {elapsed:.1f} s"
    assert "20 fatture su 20" in notes[-1][0]
    notes[-1][1][0][1]()
    result = services.controller.last_dialog.result
    assert len(result.records) == 20 and len(fake.requests) == 20
    by_num = {r["numero"].value: r for r in result.records}
    for e in expected:
        rec = by_num[e["numero"]]
        assert rec["totale"].value == pytest.approx(e["total"]), e
        assert rec["imponibile"].value == pytest.approx(e["net"]), e
        assert rec["totale"].verified and rec["imponibile"].verified, e    # trovati nel testo (anche OCR)
        assert rec[extract.FILE_COLUMN].value.endswith(f"{e['numero']}-{'scan' if e['scanned'] else 'pdf'}.pdf")
    assert not [c for c in result.checks if "diverso" in c or "non corrisponde" in c], result.checks
    rows = list(csv.reader(io.StringIO(extract.to_csv_summary(result))))
    assert len(rows) == 21 and rows[0][0] == extract.FILE_COLUMN
    sent = fake.all_text()
    assert "fattura-" not in sent and ".pdf" not in sent                  # nomi dei file mai inviati
    assert all(r.image_count() == 0 for r in fake.requests)               # solo testo, anche per le scansioni


# ------------------------------------------------------------------ contratto di vendita lungo
def _sales_contract(tmp_path, pages=200):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_textbox(
            pymupdf.Rect(50, 60, 545, 780),
            f"Art. {i + 1} - Clausola {i + 1} del contratto di compravendita dell'orologio Patek Philippe 5711. "
            + "Il venditore garantisce l'autenticità e la provenienza lecita del bene. " * 25, fontsize=9)
    path = tmp_path / "contratto-vendita.pdf"
    doc.save(str(path))
    return str(path)


def _small_window_job(services, progress=None, cancel=None):
    from foliant.modules.ai import engine
    gw = services.gateway()
    return engine.Job(gw, progress=progress, cancel=cancel)


def test_long_sales_contract_review_and_risks_chunked(window, services, tmp_path):
    import threading

    from foliant.api import OperationCancelled
    from foliant.modules.ai import contracts, engine
    make_active_profile(services.store, context_window=8000, max_output=1000)
    view = window.open_path(_sales_contract(tmp_path))
    fake = services.fake

    def reply(req):
        body = req.messages[-1].content
        if "risks" in req.system:
            arts = re.findall(r"Art\. (\d+)", body)
            return json.dumps({"risks": [{"descrizione": f"Garanzia art. {arts[0]}", "clausola": arts[0]},
                                         {"descrizione": "Foro competente non indicato"}],
                               "sources": [{"riferimento": "art. 1490 c.c.", "citazione": "garanzia per i vizi"}]})
        arts = re.findall(r"Art\. (\d+)", body)
        return json.dumps({"clauses": [{"numero": a, "titolo": f"Clausola {a}", "pagina": int(a),
                                        "testo": f"Clausola {a} del contratto", "criticita": "media"}
                                       for a in dict.fromkeys(arts)]})
    fake.default = reply
    scope = engine.build_scope(view.document.pdf, engine.DOCUMENT)
    steps = []
    t0 = time.monotonic()
    clauses = contracts.review_contract(_small_window_job(services, lambda f, t: steps.append((f, t))), scope)
    assert time.monotonic() - t0 < 30
    n_chunks = len(fake.requests)
    assert n_chunks > 10 and len(steps) == n_chunks and steps[-1][1].startswith(f"Blocco {n_chunks} di")
    assert sorted(int(c.numero) for c in clauses) == list(range(1, 201))      # nessuna clausola persa o doppia
    assert all(c.verified for c in clauses)
    fake.requests.clear()
    res = contracts.risks_and_research(_small_window_job(services), scope, "acquirente")
    assert len(fake.requests) == n_chunks                  # tutto il contratto, non solo l'inizio
    assert "Garanzia art. 200" in [r.descrizione for r in res.risks] or \
        any("Garanzia art. 19" in r.descrizione for r in res.risks)
    assert [r.descrizione for r in res.risks].count("Foro competente non indicato") == 1
    assert len(res.sources) == 1
    # annullamento a metà: si ferma subito, senza altre richieste
    fake.requests.clear()
    cancel = threading.Event()

    def progress(f, t):
        if f > 0.3:
            cancel.set()
    with pytest.raises(OperationCancelled):
        contracts.review_contract(_small_window_job(services, progress, cancel), scope)
    assert len(fake.requests) < n_chunks // 2 + 2


# ------------------------------------------------------------------ scheda prodotto da foto e testo
def _watch_listing(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300), 0)
    pix.set_rect(pix.irect, (20, 40, 90))              # «foto» dell'orologio (quadrante blu)
    page.insert_image(pymupdf.Rect(50, 50, 450, 350), pixmap=pix)
    page.insert_textbox(pymupdf.Rect(50, 370, 545, 780),
                        "Rolex Submariner Date 126610LV\nCassa: acciaio Oystersteel, diametro 41 mm\n"
                        "Movimento: calibro 3235, automatico\nRiserva di carica: 70 ore\n"
                        "Impermeabilità: 300 m\nCorredo: scatola e garanzia 2023", fontsize=11)
    path = tmp_path / "annuncio-submariner.pdf"
    doc.save(str(path))
    return str(path)


SHEET_REPLY = json.dumps({
    "nome": "Rolex Submariner Date 126610LV", "descrizione_breve": "Subacqueo in acciaio con lunetta verde.",
    "descrizione": "Orologio subacqueo automatico.", "caratteristiche": ["Quadrante blu"],
    "specifiche": [{"nome": "Diametro", "valore": 41, "unita": "mm", "pagina": 1},
                   {"nome": "Riserva di carica", "valore": 70, "unita": "ore", "pagina": 1},
                   {"nome": "Colore del quadrante", "valore": "blu", "unita": ""}],
    "vantaggi": ["Corredo completo"], "parole_chiave": ["rolex", "submariner"]})


def test_product_sheet_from_photo_and_text(window, services, tmp_path):
    from foliant.modules.ai import product_sheet
    make_active_profile(services.store, images=True)
    view = window.open_path(_watch_listing(tmp_path))
    fake = services.fake
    fake.default = SHEET_REPLY
    dlg = services.controller.open_product_sheet()
    assert dlg.use_photos.isEnabled() and not dlg.use_photos.isChecked()     # foto inviate solo se scelte
    dlg.generate()
    assert fake.requests[-1].image_count() == 0
    dlg.use_photos.setChecked(True)
    dlg.generate()
    req = fake.requests[-1]
    assert req.image_count() == 1 and "Photos of the product" in req.system
    assert "annuncio-submariner" not in fake.all_text()
    specs = {s.nome: s for s in dlg.sheet.specifiche}
    assert specs["Diametro"].verified and specs["Diametro"].pagina == 0
    assert not specs["Colore del quadrante"].verified                         # solo dalla foto: da controllare
    assert any("foto" in w for w in dlg.sheet.warnings)
    dlg.insert()                                                              # come casella di testo (AI-19)
    with view.document.lock:
        annots = [(a.type[1], a.info["title"]) for a in view.document.pdf[0].annots()]
    assert annots and annots[0][0] == "FreeText" and annots[0][1] == window.ctx.author_name()
    view.document.undo()
    with view.document.lock:
        assert not list(view.document.pdf[0].annots())
    # profilo senza immagini: casella disattivata con la spiegazione, nessuna immagine inviata
    make_active_profile(services.store, images=False)
    dlg2 = services.controller.open_product_sheet()
    assert not dlg2.use_photos.isEnabled() and "non accetta immagini" in dlg2.use_photos.toolTip()
    assert product_sheet.PHOTOS_RULE


def test_product_sheet_long_catalog_declares_truncation(window, services, tmp_path):
    make_active_profile(services.store, context_window=8000, max_output=1000)
    doc = pymupdf.open()
    for i in range(60):
        doc.new_page().insert_textbox(pymupdf.Rect(50, 60, 545, 780), f"Catalogo pagina {i + 1}. " + "Testo. " * 300,
                                      fontsize=9)
    path = tmp_path / "catalogo.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    services.fake.default = SHEET_REPLY
    dlg = services.controller.open_product_sheet()
    dlg.generate()
    assert any(w.startswith("Parziale") and "pagina" in w for w in dlg.sheet.warnings), dlg.sheet.warnings
