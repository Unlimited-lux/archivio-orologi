"""Criteri di accettazione della sezione 7 (OCR-04, 05, 06, 08, 13, 14)."""

from __future__ import annotations

import pymupdf
import pytest

from tests.conftest import system_font, perf_limit
from foliant.modules.ocr import detect, engine, preprocess

from .conftest import FRASI, image_pdf, iou, scan_image, scan_words

EXPECTED = "\n".join(FRASI)


def _data(tmp_path, name="p.pdf", **kw):
    dpi = kw.get("dpi", 300)
    return open(image_pdf(tmp_path / name, [scan_image(**kw)], dpi=dpi), "rb").read()


@pytest.mark.parametrize("angle", [0.5, -3, 7, -12])
def test_stima_inclinazione_entro_0_3_gradi(angle):
    img = scan_image(dpi=150, skew=angle)
    est = preprocess.estimate_skew(preprocess.to_gray(img))
    # estimate_skew restituisce la rotazione che raddrizza: opposta all'inclinazione applicata
    assert abs(est + angle) <= 0.3, est


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rotazione_automatica(tmp_path, rot):
    data = _data(tmp_path, dpi=200, rotate90=rot)
    with pymupdf.open(stream=data) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    assert res.rotation == (360 - rot) % 360
    assert engine.char_accuracy(res.text, EXPECTED) >= 0.95


def test_bassa_risoluzione(tmp_path):
    with pymupdf.open(stream=_data(tmp_path, "a.pdf", dpi=150)) as pdf:
        r150 = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    with pymupdf.open(stream=_data(tmp_path, "b.pdf", dpi=100)) as pdf:
        r100 = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    assert engine.char_accuracy(r150.text, EXPECTED) >= 0.95
    assert engine.char_accuracy(r100.text, EXPECTED) >= 0.85
    assert any("bassa" in n for n in r100.notes)


def test_ricercabile_riquadri_parole_iou(tmp_path):
    out = engine.ocr_pdf_bytes(_data(tmp_path), ["ita"], "searchable")
    expected, _l = scan_words()
    with pymupdf.open(stream=out) as d:
        got = d[0].get_text("words")
    ok = 0
    for w in expected:
        best = max((iou(w[:4], g[:4]) for g in got if g[4] == w[4]), default=0)
        ok += best >= 0.7
    assert ok / len(expected) >= 0.95, ok / len(expected)


def test_ricercabile_marcato_metadati_lingua_e_nessun_carattere_privato(tmp_path):
    out = engine.ocr_pdf_bytes(_data(tmp_path, dpi=200), ["ita", "eng"], "searchable")
    with pymupdf.open(stream=out) as d:
        content = b"".join(d.xref_stream(x) for x in d[0].get_contents())
        assert b"/FoliantOCR BMC" in content
        assert "Foliant OCR (ita+eng" in d.metadata["keywords"]
        assert d.xref_get_key(d.pdf_catalog(), "Lang")[1].strip("()") == "it-IT"
        text = d[0].get_text()
        assert not any(0xE000 <= ord(c) <= 0xF8FF or (ord(c) < 32 and c not in "\n\t") for c in text)


def test_intervallo_altre_pagine_invariate(tmp_path):
    imgs = [scan_image(lines=[f"Pagina numero {n} della scansione"], dpi=150) for n in range(1, 11)]
    data = open(image_pdf(tmp_path / "dieci.pdf", imgs, dpi=150), "rb").read()
    pages = engine.parse_pages("2-4, 7", 10)
    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable", pages=pages)
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=out) as b:
        for i in range(10):
            has = bool(b[i].get_text().strip())
            assert has == (i in pages)
            if i not in pages:
                ca = b"".join(a.xref_stream(x) for x in a[i].get_contents())
                cb = b"".join(b.xref_stream(x) for x in b[i].get_contents())
                assert ca == cb


def test_intervalli_non_validi():
    for bad in ("12-15", "5-3", "tre"):
        with pytest.raises(ValueError):
            engine.parse_pages(bad, 10)
    assert engine.parse_pages("8-", 10) == [7, 8, 9]


def test_applica_correzioni_alla_pagina_visibile(tmp_path):
    src = _data(tmp_path, dpi=200, skew=5)
    o = engine.OcrOptions(apply_to_page=True)
    out = engine.ocr_pdf_bytes(src, ["ita"], "searchable", options=o)
    with pymupdf.open(stream=src) as a, pymupdf.open(stream=out) as d:
        p = d[0]
        pix = p.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY)
        import numpy as np
        g = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
        assert abs(preprocess.estimate_skew(g)) <= 0.3          # righe orizzontali nel rendering
        assert a.xref_stream_raw(a[0].get_images()[0][0]) == d.xref_stream_raw(p.get_images()[0][0])
        assert engine.char_accuracy(p.get_text(), EXPECTED) >= 0.95
    # capovolta: /Rotate aumenta di 180
    out = engine.ocr_pdf_bytes(_data(tmp_path, "c.pdf", dpi=200, rotate90=180), ["ita"], "searchable", options=o)
    with pymupdf.open(stream=out) as d:
        assert d[0].rotation == 180
        r = d[0].search_for("compravendita")[0] * d[0].rotation_matrix
        assert r.y0 < 200                                      # in alto nella pagina visualizzata


def test_ricercabile_senza_correzioni_rendering_identico(tmp_path):
    src = _data(tmp_path, dpi=200, skew=5)
    out = engine.ocr_pdf_bytes(src, ["ita"], "searchable")
    with pymupdf.open(stream=src) as a, pymupdf.open(stream=out) as b:
        assert a[0].get_pixmap(dpi=150).samples == b[0].get_pixmap(dpi=150).samples
        # la selezione segue la riga inclinata: le parole di una riga hanno direzione inclinata
        line = [b for b in b[0].get_text("dict")["blocks"] if b["type"] == 0][0]["lines"][0]
        assert abs(line["dir"][1]) > 0.05


def test_modificabile_righe_corpo_e_linea_di_base(tmp_path):
    out = engine.ocr_pdf_bytes(_data(tmp_path), ["ita"], "editable")
    _w, blocks = scan_words()
    expected = [ln for b in blocks for ln in b["lines"]]
    with pymupdf.open(stream=out) as d:
        got = [ln for b in d[0].get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"]]
    good = 0
    for e in expected:
        g = max(got, key=lambda g: iou(e["bbox"], g["bbox"]))
        size_ok = abs(g["spans"][0]["size"] - 12) / 12 <= 0.10
        base_ok = abs(g["spans"][0]["origin"][1] - e["spans"][0]["origin"][1]) <= 1.0
        good += iou(e["bbox"], g["bbox"]) >= 0.8 and size_ok and base_ok
    assert good / len(expected) >= 0.9, good


def test_modificabile_inclinata_righe_orizzontali(tmp_path):
    out = engine.ocr_pdf_bytes(_data(tmp_path, dpi=200, skew=4), ["ita"], "editable")
    with pymupdf.open(stream=out) as d:
        for b in d[0].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                assert abs(ln["dir"][1]) < 0.0035                  # ≤ 0,2°


def test_modificabile_sostituisce_livello_ocr_esistente_senza_duplicati(tmp_path):
    src = _data(tmp_path, dpi=200)
    searchable = engine.ocr_pdf_bytes(src, ["ita"], "searchable")
    out = engine.ocr_pdf_bytes(searchable, ["ita"], "editable")
    with pymupdf.open(stream=out) as d:
        text = d[0].get_text()
        assert text.count("compravendita") == 1
        assert all(s["type"] == 0 for s in d[0].get_texttrace())


def test_politiche_pagine_con_testo(tmp_path):
    src = _data(tmp_path, dpi=200)
    once = engine.ocr_pdf_bytes(src, ["ita"], "searchable")
    # Salta: pagine invariate
    again = engine.ocr_pdf_bytes(once, ["ita"], "searchable")
    with pymupdf.open(stream=once) as a, pymupdf.open(stream=again) as b:
        assert b"".join(a.xref_stream(x) for x in a[0].get_contents()) == \
            b"".join(b.xref_stream(x) for x in b[0].get_contents())
        assert len(b[0].search_for("contratto")) == 1
    # Sostituisci: un solo livello, immagine invariata
    opts = engine.OcrOptions(existing_text=engine.REPLACE)
    rep = engine.ocr_pdf_bytes(once, ["ita"], "searchable", options=opts)
    with pymupdf.open(stream=once) as a, pymupdf.open(stream=rep) as b:
        assert len(b[0].search_for("contratto")) == 1
        assert len(b[0].get_text("words")) == len(a[0].get_text("words"))
        assert a[0].get_pixmap(dpi=72).samples == b[0].get_pixmap(dpi=72).samples


def test_pagina_mista_testo_vettoriale_invariato(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Intestazione vettoriale del documento", fontsize=14)
    img = scan_image(lines=FRASI[:4], dpi=200, size=(595, 400), top=40)
    import cv2
    ok, buf = cv2.imencode(".png", img)
    page.insert_image(pymupdf.Rect(0, 100, 595, 500), stream=buf.tobytes())
    page.insert_text((72, 700), "Piè di pagina vettoriale con testo", fontsize=11)
    data = doc.tobytes()
    with pymupdf.open(stream=data) as d:
        assert detect.classify_page(d[0]).category == detect.MIXED
    for mode in ("searchable", "editable"):
        out = engine.ocr_pdf_bytes(data, ["ita"], mode)
        with pymupdf.open(stream=out) as d:
            t = d[0].get_text()
            assert t.count("Intestazione vettoriale") == 1 and t.count("Piè di pagina") == 1
            assert "compravendita" in t


def test_annullamento_restituisce_pagine_completate(tmp_path):
    imgs = [scan_image(lines=["Una riga di prova"], dpi=150) for _ in range(5)]
    data = open(image_pdf(tmp_path / "q.pdf", imgs, dpi=150), "rb").read()

    class P:
        cancelled = False
        calls = 0

        def update(self, f=None, t=None):
            if f is not None and f >= 0.4:
                self.cancelled = True
                from foliant.api import OperationCancelled
                raise OperationCancelled()
    with pymupdf.open(stream=data) as pdf, pytest.raises(engine.OcrInterrupted) as e:
        engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], workers=1), progress=P())
    part = e.value.partial
    assert 1 <= len(part.recognized) < 5
    with pymupdf.open(stream=data) as pdf:
        engine.apply_result(pdf, part)
        filled = [bool(pdf[i].get_text().strip()) for i in range(5)]
    assert filled == [p.index in {r.index for r in part.recognized} for p in [type("x", (), {"index": i}) for i in range(5)]]


def test_testo_non_estraibile(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    # font senza mappatura Unicode affidabile: caratteri in area d'uso privato
    font = pymupdf.Font(fontfile=system_font("truetype/dejavu/DejaVuSans.ttf"))
    tw = pymupdf.TextWriter(page.rect)
    tw.append((72, 100), "Testo con codifica illeggibile " * 2, font=font, fontsize=12)
    tw.write_text(page)
    for xref in page.get_contents():
        pass
    fx = [f[0] for f in doc.get_page_fonts(0)][0]
    tu = doc.xref_get_key(fx, "ToUnicode")[1].split()[0]
    cmap = "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /X def " \
           "1 begincodespacerange <0000> <FFFF> endcodespacerange 1 beginbfrange <0000> <FFFF> <E000> " \
           "endbfrange endcmap CMapName currentdict /CMap defineresource pop end end"
    doc.update_stream(int(tu), cmap.encode())
    with pymupdf.open(stream=doc.tobytes()) as d:
        assert detect.classify_page(d[0]).category == detect.UNEXTRACTABLE


def test_documento_lungo_analisi_incrementale(tmp_path):
    import time
    img = scan_image(lines=["Pagina lunga"], dpi=72)
    one = pymupdf.open(image_pdf(tmp_path / "1.pdf", [img], dpi=72))
    doc = pymupdf.open()
    for _ in range(2000):
        doc.insert_pdf(one)
    t = time.monotonic()
    infos = [detect.classify_page(doc[i]) for i in range(50)]
    per_page = (time.monotonic() - t) / 50
    assert all(i.category == detect.IMAGE_ONLY for i in infos)
    assert per_page < perf_limit(0.02)          # 25 ms per passo nell'interfaccia: almeno una pagina per passo


def test_modificabile_stile_e_famiglia(tmp_path):
    """Grassetto, corsivo, grazie e monospaziato stimati dall'immagine (≥ 85% delle parole)."""
    import cv2
    import numpy as np
    # le grazie si riconoscono solo quando la misura è netta: nel dubbio senza grazie
    styles = [("helv", "Liberation Sans", False, False), ("cour", "Liberation Mono", False, False),
              ("hebo", "Liberation Sans", True, False), ("heit", "Liberation Sans", False, True),
              ("helv", "Liberation Sans", False, False), ("hebo", "Liberation Sans", True, False)]
    parts = [scan_image(lines=FRASI[:3], dpi=300, fontname=fn, size=(595, 140), top=40) for fn, *_r in styles]
    img = np.vstack(parts)
    page_pdf = image_pdf(tmp_path / "stili.pdf", [img], dpi=300)
    out = engine.ocr_pdf_bytes(open(page_pdf, "rb").read(), ["ita"], "editable")
    total = good = 0
    with pymupdf.open(stream=out) as d:
        for b in d[0].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for sp in ln["spans"]:
                    y = sp["origin"][1]
                    k = min(int(y // 140), len(styles) - 1)
                    _fn, fam, bold, italic = styles[k]
                    n = len(sp["text"].split())
                    total += n
                    name = sp["font"]
                    ok = fam.replace(" ", "") in name.replace(" ", "") and (("Bold" in name) == bold) and \
                        (("Italic" in name) == italic)
                    good += n if ok else 0
    assert total and good / total >= 0.85, good / total
    del cv2


def test_conserva_originale_come_livello_nascosto(tmp_path):
    src = _data(tmp_path, dpi=200)
    out = engine.ocr_pdf_bytes(src, ["ita"], "editable", options=engine.OcrOptions(keep_original=True))
    with pymupdf.open(stream=out) as d:
        ocgs = d.get_ocgs()
        assert len(ocgs) == 1 and not list(ocgs.values())[0]["on"]
        assert len(d[0].get_images()) == 2
        assert d[0].get_text().count("compravendita") == 1


def test_parallelismo_limitato_dalla_memoria(monkeypatch):
    monkeypatch.setattr(engine, "available_memory", lambda: 400 * 1024 * 1024)
    assert engine.OcrOptions().worker_count() == 1
    monkeypatch.setattr(engine, "available_memory", lambda: 0)
    assert engine.OcrOptions().worker_count() >= 1


def test_parole_degradate_segnalate(tmp_path):
    """5 parole coperte in parte da macchie: almeno 4 sospette, sospette totali ≤ 10% (OCR-12)."""
    import cv2
    img = scan_image(dpi=300)
    words, _b = scan_words()
    z = 300 / 72
    targets = ["compravendita", "cinturino", "garantisce", "laboratorio", "bonifico"]
    rng = __import__("numpy").random.default_rng(7)
    for t in targets:
        x0, y0, x1, y1, _w = next(w for w in words if w[4] == t)
        cx = int((x0 + (x1 - x0) * 0.45) * z)
        for _ in range(3):
            dx, dy = rng.integers(-25, 25), rng.integers(-8, 8)
            cv2.circle(img, (int(cx + dx), int((y0 + y1) / 2 * z + dy)), 13, (0, 0, 0), -1)
    data = open(image_pdf(tmp_path / "m.pdf", [img]), "rb").read()
    with pymupdf.open(stream=data) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    low = res.low_confidence_words()
    flagged = 0
    for t in targets:
        tx0, ty0, tx1, ty1, _w = next(w for w in words if w[4] == t)
        rect = pymupdf.Rect(tx0, ty0, tx1, ty1)
        hit = [w for w in res.words if res.word_rect_display(w).intersects(rect)]
        flagged += any(w.low or w.text != t for w in hit) and any(w.low for w in hit)
    assert flagged >= 4, (flagged, [(w.text, w.conf) for w in low])
    assert len(low) <= 0.1 * len(res.words) + 1


def _acc(img, **kw):
    lines, _p, _c = engine.recognize_array(img, ["ita"], 300, preprocess.PreprocessOptions(**kw))
    return engine.char_accuracy(engine.PageResult(index=0, lines=lines).text, EXPECTED)


def test_pulizia_rumore_e_contrasto():
    """OCR-09: rumore 5% e testo grigio su carta ingiallita (contrasto 25%)."""
    import numpy as np
    noisy = scan_image(dpi=300, noise=0.05)
    auto, none = _acc(noisy), _acc(noisy, denoise=False, contrast=False)
    assert auto >= 0.98 or (auto >= 0.95 and auto - none >= 0.05), (auto, none)
    img = scan_image(dpi=300).astype(np.float32) / 255
    paper = np.array([240, 225, 170], np.float32) / 255
    ink = paper * 0.75
    low = ((ink + (paper - ink) * img) * 255).astype(np.uint8)
    assert _acc(low) >= 0.95
    clean = scan_image(dpi=300)
    assert abs(_acc(clean) - _acc(clean, denoise=False, contrast=False)) <= 0.005


def test_due_colonne_ordine_di_lettura(tmp_path):
    left = ["Colonna sinistra prima riga del testo", "seconda riga a sinistra con parole",
            "terza riga sinistra e ultima"]
    right = ["Colonna destra prima riga del testo", "seconda riga a destra con parole",
             "terza riga destra e ultima"]
    doc = pymupdf.open()
    page = doc.new_page()
    for i, (a, b) in enumerate(zip(left, right)):
        page.insert_text((50, 100 + i * 20), a, fontsize=11)
        page.insert_text((320, 100 + i * 20), b, fontsize=11)
    pix = page.get_pixmap(dpi=300)
    import numpy as np
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()
    data = open(image_pdf(tmp_path / "col.pdf", [img]), "rb").read()
    with pymupdf.open(stream=data) as pdf:
        text = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0].text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert all("sinistra" not in ln or "destra" not in ln for ln in lines)       # niente righe miste
    assert text.index("terza riga sinistra") < text.index("Colonna destra")


@pytest.mark.parametrize("crop", [False, True])
@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rilettura_pagine_ruotate_e_ritagliate(tmp_path, rot, crop):
    """bug_01/bug_02: le parole riconosciute stanno esattamente dove sono nella scansione
    anche con /Rotate e CropBox spostato (scarto = origine del ritaglio, ± 1 pt)."""
    src = pymupdf.open(image_pdf(tmp_path / "s.pdf", [scan_image(lines=FRASI[:2], dpi=150)], dpi=150))
    for mode in ("searchable", "editable"):
        base = pymupdf.open(stream=engine.ocr_pdf_bytes(src.tobytes(), ["ita"], mode))[0].search_for(
            "compravendita")[0]
        d = pymupdf.open()
        d.insert_pdf(src)
        if crop:
            d[0].set_cropbox(pymupdf.Rect(30, 40, 580, 800))
        d[0].set_rotation(rot)
        dx, dy = (30, 40) if crop else (0, 0)
        with pymupdf.open(stream=engine.ocr_pdf_bytes(d.tobytes(), ["ita"], mode)) as out:
            r = out[0].search_for("compravendita")[0]
            assert abs(r.x0 - (base.x0 - dx)) <= 1 and abs(r.y0 - (base.y0 - dy)) <= 1, (mode, r, base)


def test_modificabile_tabella_e_stile_sans(tmp_path):
    """Titolo grassetto 18 pt, paragrafi 11 pt senza grazie e tabella bordata a 3 colonne:
    colonne non fuse, testo nelle celle giuste, famiglia e grassetto corretti."""
    import numpy as np
    sans = system_font("truetype/liberation/LiberationSans-Regular.ttf")
    bold = system_font("truetype/liberation/LiberationSans-Bold.ttf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="S", fontfile=sans)
    page.insert_font(fontname="B", fontfile=bold)
    page.insert_text((72, 80), "Contratto di compravendita", fontname="B", fontsize=18)
    for i, t in enumerate(["Il venditore cede al compratore gli orologi indicati nella tabella seguente.",
                           "Il prezzo comprende la revisione completa e la garanzia di un anno.",
                           "Il pagamento avviene con bonifico entro trenta giorni dalla consegna."]):
        page.insert_text((72, 120 + i * 18), t, fontname="S", fontsize=11)
    rows = [("Articolo", "Quantità", "Prezzo"), ("Orologio da tasca", "4", "€ 1.200,00"),
            ("Cronografo acciaio", "2", "€ 3.450,00")]
    xs = [72, 260, 360, 500]
    y0 = 200
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((xs[c] + 6, y0 + r * 24 + 16), cell, fontname="S", fontsize=11)
    for x in xs:
        page.draw_line((x, y0), (x, y0 + 72), width=0.8)
    for r in range(4):
        page.draw_line((72, y0 + r * 24), (500, y0 + r * 24), width=0.8)
    pix = page.get_pixmap(dpi=300)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()
    out = engine.ocr_pdf_bytes(open(image_pdf(tmp_path / "t.pdf", [img]), "rb").read(), ["ita"], "editable")
    with pymupdf.open(stream=out) as d:
        p = d[0]
        for word, (lo, hi) in (("Quantità", (260, 360)), ("Prezzo", (360, 500)), ("tasca", (72, 260))):
            r = p.search_for(word, clip=pymupdf.Rect(0, 195, 595, 280))[0]
            assert lo <= r.x0 and r.x1 <= hi, (word, r)
        fonts = [sp["font"] for b in p.get_text("dict")["blocks"] if b["type"] == 0
                 for ln in b["lines"] for sp in ln["spans"] for _w in sp["text"].split()]
        sans_share = sum("LiberationSans" in f for f in fonts) / len(fonts)
        assert sans_share >= 0.85, fonts
        title = [sp for b in p.get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"]
                 for sp in ln["spans"] if "Contratto" in sp["text"]][0]
        assert "Bold" in title["font"]
        body = [sp for b in p.get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"]
                for sp in ln["spans"] if "venditore" in sp["text"]][0]
        assert "Bold" not in body["font"]


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_modificabile_contenuto_memorizzato_di_lato(rot):
    """Pagina con /Rotate il cui contenuto è memorizzato ruotato (appare diritto): in modalità modificabile
    il testo ricostruito cade sulle parole della pagina visualizzata."""
    import cv2
    from foliant.modules.ocr.preprocess import PreprocessOptions
    img = scan_image(lines=FRASI[:3], dpi=200)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    d = pymupdf.open()
    w, h = (842, 595) if rot in (90, 270) else (595, 842)
    p = d.new_page(width=w, height=h)
    p.set_rotation(rot)
    p.insert_image(p.rect * p.derotation_matrix, stream=buf.tobytes(), rotate=rot, keep_proportion=False)
    mb = p.mediabox
    p.set_cropbox(pymupdf.Rect(mb.x0 + 20, mb.y0 + 30, mb.x1 - 25, mb.y1 - 15))
    src = d.tobytes()
    with pymupdf.open(stream=engine.ocr_pdf_bytes(src, ["ita"], "editable")) as out, \
            pymupdf.open(stream=src) as ref:
        q = out[0]
        assert q.rotation == rot
        assert engine.char_accuracy(q.get_text(), "\n".join(FRASI[:3])) >= 0.9
        r = q.search_for("compravendita")[0] * q.rotation_matrix
        seen = engine.ocr_page_text(ref[0], clip=r + (-4, -4, 4, 4), languages=["ita"],
                                    preprocess=PreprocessOptions(auto_rotate=False))
        assert "compravendita" in seen


def test_bordi_neri_e_pulizia_applicata_alla_pagina(tmp_path):
    """OCR-09: bordi neri di scansione rimossi per il riconoscimento; con l'opzione l'immagine visibile
    viene sostituita mantenendo posizione e dimensioni."""
    import numpy as np
    img = scan_image(dpi=200, noise=0.03)
    img[:, :60] = 0
    img[:45, :] = 0
    data = open(image_pdf(tmp_path / "b.pdf", [img], dpi=200), "rb").read()
    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable",
                               options=engine.OcrOptions(clean_page=True))
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=out) as b:
        assert engine.char_accuracy(b[0].get_text(), EXPECTED) >= 0.95
        ia, ib = a[0].get_image_info()[0], b[0].get_image_info()[0]
        from .conftest import iou
        assert iou(ia["bbox"], ib["bbox"]) >= 0.99
        g = np.frombuffer(b[0].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8)
        g0 = np.frombuffer(a[0].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8)
        assert (g < 60).sum() < (g0 < 60).sum() * 0.5          # bordi neri e rumore tolti dall'immagine


def _photo(img, dpi=200):
    """Foglio fotografato in prospettiva su un tavolo scuro."""
    import cv2
    import numpy as np
    h, w = img.shape[:2]
    canvas = np.full((int(h * 1.25), int(w * 1.3), 3), (70, 60, 50), np.uint8)
    H, W = canvas.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[W * 0.12, H * 0.08], [W * 0.86, H * 0.12], [W * 0.92, H * 0.93], [W * 0.07, H * 0.88]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, m, (W, H), borderMode=cv2.BORDER_TRANSPARENT, dst=canvas.copy())
    return warped


def test_foto_in_prospettiva(tmp_path):
    """OCR-09: documento fotografato in prospettiva: raddrizzato e riconosciuto (testo e modificabile)."""
    from foliant.modules.ocr import preprocess
    photo = _photo(scan_image(lines=FRASI[:4], dpi=200))
    assert preprocess.find_document_quad(preprocess.to_gray(photo)) is not None
    assert preprocess.find_document_quad(preprocess.to_gray(scan_image(dpi=150))) is None   # scansione normale
    data = open(image_pdf(tmp_path / "foto.pdf", [photo], dpi=200), "rb").read()
    expected = "\n".join(FRASI[:4])
    with pymupdf.open(stream=data) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    assert engine.char_accuracy(res.text, expected) >= 0.9
    with pymupdf.open(stream=engine.ocr_pdf_bytes(data, ["ita"], "searchable")) as d:
        r = d[0].search_for("compravendita")
        assert r                                                   # livello ricercabile sulla foto
    with pymupdf.open(stream=engine.ocr_pdf_bytes(data, ["ita"], "editable")) as d:
        assert engine.char_accuracy(d[0].get_text(), expected) >= 0.9
        for b in d[0].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                assert abs(ln["dir"][1]) < 0.01                    # righe orizzontali


def test_testo_non_estraibile_solo_con_forza_ocr(tmp_path):
    """OCR-14: le pagine con testo non estraibile non si convertono in immagine senza «Forza OCR»;
    con «Forza OCR» il testo diventa estraibile; in «solo testo» vengono riconosciute (non distruttivo)."""
    from .conftest import unextractable_pdf
    data = open(unextractable_pdf(tmp_path / "u.pdf"), "rb").read()
    with pymupdf.open(stream=data) as pdf:
        assert detect.classify_page(pdf[0]).category == detect.UNEXTRACTABLE
        skipped = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
        assert skipped.status == "skipped" and "Forza OCR" in skipped.message
        text_only = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], mode=engine.TEXT_ONLY))
        assert "orologio" in text_only.pages[0].text
    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable", options=engine.OcrOptions(existing_text=engine.FORCE))
    with pymupdf.open(stream=out) as d:
        assert "Contratto di vendita orologio" in d[0].get_text()
    same = engine.ocr_pdf_bytes(data, ["ita"], "searchable")
    with pymupdf.open(stream=data) as a, pymupdf.open(stream=same) as b:
        assert b"".join(a.xref_stream(x) for x in a[0].get_contents()) == \
            b"".join(b.xref_stream(x) for x in b[0].get_contents())


def test_resa_in_processo_separato_senza_lock(tmp_path):
    """L'OCR in background rende le pagine in un altro processo: il lock del motore resta libero."""
    import threading
    import time
    from foliant.core.engine import ENGINE_LOCK
    data = open(image_pdf(tmp_path / "r.pdf", [scan_image(lines=FRASI[:3], dpi=200)] * 2, dpi=200), "rb").read()
    waits = []
    stop = threading.Event()

    def probe():
        while not stop.is_set():
            t = time.monotonic()
            with ENGINE_LOCK:
                pass
            waits.append(time.monotonic() - t)
            time.sleep(0.005)
    th = threading.Thread(target=probe)
    th.start()
    try:
        with pymupdf.open(stream=data) as pdf:
            res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"]), render_data=data)
    finally:
        stop.set()
        th.join()
    assert len(res.recognized) == 2 and "compravendita" in res.pages[1].text
    assert max(waits) < perf_limit(0.06), max(waits)


@pytest.mark.parametrize("skew", [0.0, 1.2])
def test_ricercabile_leggibile_da_poppler(tmp_path, skew):
    """D4: con un'inclinazione lieve il testo invisibile si scrive diritto parola per parola, così
    poppler (Evince, Okular, pdftotext) non spezza né riordina le parole; spazi tra parole emessi."""
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    righe = ["Orologio: Rolex Datejust 41 referenza 126334 in acciaio,",
             "la garanzia copre i difetti di fabbricazione per due anni."]
    data = open(image_pdf(tmp_path / "s.pdf", [scan_image(lines=righe, skew=skew)], dpi=300), "rb").read()
    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable")
    (tmp_path / "o.pdf").write_bytes(out)
    text = subprocess.run(["pdftotext", "-layout", str(tmp_path / "o.pdf"), "-"], capture_output=True,
                          text=True, check=True).stdout
    words = text.split()
    assert "referenza" in words and "126334" in words, text
    assert "copre" in words and "i" in words, text
    with pymupdf.open(stream=out) as d:
        hits = d[0].search_for("referenza")
        assert len(hits) == 1
        if skew == 0:
            expected, _l = scan_words(lines=righe)
            box = next(e[:4] for e in expected if e[4] == "referenza")
            assert iou(hits[0], box) > 0.5
