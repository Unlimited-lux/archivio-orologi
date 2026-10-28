"""Criteri di accettazione della sezione 7 (OCR-04, 05, 06, 08, 13, 14)."""

from __future__ import annotations

import pymupdf
import pytest

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
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
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
    assert per_page < 0.02          # 25 ms per passo nell'interfaccia: almeno una pagina per passo
