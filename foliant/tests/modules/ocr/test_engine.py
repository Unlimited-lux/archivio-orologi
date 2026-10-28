"""Motore OCR: accuratezza, modalità ricercabile/modificabile, pagine con testo, errori."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.core.errors import OperationCancelled
from foliant.modules.ocr import detect, engine, tess
from foliant.modules.ocr.languages import MissingLanguagesError

from .conftest import FRASI, image_pdf, scan_image

EXPECTED = "\n".join(FRASI)


def _text(data: bytes, page: int = 0) -> str:
    with pymupdf.open(stream=data, filetype="pdf") as d:
        return d[page].get_text()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus")
    variants = {
        "dritta-300": dict(dpi=300),
        "inclinata-200-rumore": dict(dpi=200, skew=3.5, noise=0.015),
        "inclinata-neg-300": dict(dpi=300, skew=-7, background=225),
        "ruotata-90": dict(dpi=300, rotate90=90),
    }
    out = {}
    for name, kw in variants.items():
        out[name] = open(image_pdf(d / f"{name}.pdf", [scan_image(**kw)], dpi=kw["dpi"]), "rb").read()
    return out


@pytest.fixture(scope="module")
def ocr_out(corpus):
    """Risultati calcolati una volta: {(variante, modalità): bytes}."""
    cache = {}

    def get(name, mode):
        key = (name, mode)
        if key not in cache:
            cache[key] = engine.ocr_pdf_bytes(corpus[name], ["ita", "eng"], mode)
        return cache[key]
    return get


def test_lingue_disponibili():
    langs = engine.available_languages()
    assert langs[:2] == ["ita", "eng"]
    assert engine.default_languages() == ["ita", "eng"]


@pytest.mark.parametrize("variant", ["dritta-300", "inclinata-200-rumore", "inclinata-neg-300", "ruotata-90"])
@pytest.mark.parametrize("mode", ["searchable", "editable"])
def test_accuratezza_almeno_95(ocr_out, corpus, variant, mode):
    assert _text(corpus[variant]).strip() == ""          # scansione: nessun testo prima
    got = _text(ocr_out(variant, mode))
    acc = engine.char_accuracy(got, EXPECTED)
    assert acc >= 0.95, (acc, got)
    assert "perché" in got and "Città" in got            # accenti italiani


def test_ricercabile_aspetto_identico_e_testo_invisibile(ocr_out, corpus):
    out = ocr_out("inclinata-neg-300", "searchable")
    with pymupdf.open(stream=corpus["inclinata-neg-300"]) as a, pymupdf.open(stream=out) as b:
        pa = np.frombuffer(a[0].get_pixmap(dpi=72).samples, np.uint8).astype(int)
        pb = np.frombuffer(b[0].get_pixmap(dpi=72).samples, np.uint8).astype(int)
        assert np.abs(pa - pb).max() == 0
        spans = b[0].get_texttrace()
        assert spans and all(s["type"] == 3 for s in spans)
        # le parole cadono dove sono nell'immagine: «Città» nell'ultima riga, in basso
        hits = b[0].search_for("Bianchi")
        assert hits and hits[0].y0 > b[0].search_for("contratto")[0].y1
        assert detect.classify_page(b[0]).category == detect.OCR_LAYER


def test_modificabile_testo_visibile_e_sfondo_ripulito(ocr_out):
    out = ocr_out("dritta-300", "editable")
    with pymupdf.open(stream=out) as d:
        page = d[0]
        spans = page.get_texttrace()
        assert spans and all(s["type"] == 0 for s in spans)        # testo visibile
        blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 0]
        sizes = [s["size"] for s in blocks[0]["lines"][0]["spans"]]
        assert 10.5 <= sizes[0] <= 13.5                              # originale: 12 pt
        r = page.search_for("Il presente")[0]
        assert abs(r.x0 - 60) < 4 and abs(r.y1 - 92) < 8             # posizione come nell'originale
        assert detect.classify_page(page).category == detect.TEXT
        # sotto il testo l'immagine di fondo non contiene più le lettere
        imgs = page.get_images()
        assert len(imgs) == 1
        pix = pymupdf.Pixmap(d, imgs[0][0])
        bg = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
        assert bg.min() > 200


def test_modificabile_redazione_e_reinserimento(ocr_out):
    """Il testo modificabile si cambia: «tremila» → «mille» anche nella resa."""
    out = ocr_out("inclinata-200-rumore", "editable")
    with pymupdf.open(stream=out) as d:
        page = d[0]
        r = page.search_for("tremila")[0]
        span = next(s for b in page.get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"] for s in ln["spans"]
                    if "tremila" in s["text"])
        page.add_redact_annot(r)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        page.insert_text((r.x0, span["origin"][1]), "mille", fontsize=span["size"], fontname="helv")
        text = page.get_text()
        assert "mille" in text and "tremila" not in text
        seen = engine.ocr_page_text(page, languages=["ita"])
        assert "mille" in seen and "tremila" not in seen


def test_ocr_area_e_sorgenti(corpus):
    data = corpus["dritta-300"]
    with pymupdf.open(stream=data) as d:
        r = pymupdf.Rect(50, 75, 545, 100)
        t1 = engine.ocr_page_text(data, 0, ["ita"], clip=r)
        t2 = engine.ocr_page_text(d, 0, ["ita"], clip=r)
    assert engine.char_accuracy(t1, FRASI[0]) >= 0.95
    assert t1 == t2
    img = scan_image(lines=FRASI[:2], dpi=200)
    assert engine.char_accuracy(engine.ocr_page_text(img, dpi=200, languages=["ita"]), "\n".join(FRASI[:2])) >= 0.95


def test_pagine_con_testo_saltate_o_forzate(tmp_path):
    def mixed(path):
        doc = pymupdf.open(image_pdf(tmp_path / "s.pdf", [scan_image(lines=FRASI[:2], dpi=200)], dpi=200))
        p = doc.new_page()
        p.insert_text((72, 100), "Pagina digitale con testo vettoriale originale", fontsize=14)
        doc.save(str(path))
        return open(path, "rb").read()
    data = mixed(tmp_path / "misto.pdf")
    progress_seen = []

    class P:
        cancelled = False

        def update(self, f=None, t=None):
            progress_seen.append(f)

    out = engine.ocr_pdf_bytes(data, ["ita"], "searchable", progress=P())
    assert progress_seen and progress_seen[-1] == 1.0
    with pymupdf.open(stream=out) as d:
        assert d[1].get_text().strip() == "Pagina digitale con testo vettoriale originale"
        assert "compravendita" in d[0].get_text()
    # intervallo: solo pagina 2 (con testo) → nulla da fare, pagina 1 resta senza testo
    out2 = engine.ocr_pdf_bytes(data, ["ita"], "searchable", pages=[1])
    with pymupdf.open(stream=out2) as d:
        assert d[0].get_text().strip() == ""
    # forzato: la pagina con testo viene rasterizzata e riconosciuta (niente doppioni)
    opts = engine.OcrOptions(existing_text=engine.FORCE)
    out3 = engine.ocr_pdf_bytes(data, ["ita"], "editable", pages=[1], options=opts)
    with pymupdf.open(stream=out3) as d:
        t = d[1].get_text()
        assert t.count("digitale") == 1 and engine.char_accuracy(t, "Pagina digitale con testo vettoriale originale") > 0.95


def test_pagina_che_fallisce_non_ferma_le_altre(tmp_path, monkeypatch):
    imgs = [scan_image(lines=[f"Pagina numero {n} del documento"], dpi=150) for n in range(1, 4)]
    data = open(image_pdf(tmp_path / "tre.pdf", imgs, dpi=150), "rb").read()
    real = tess.recognize
    calls = {"n": 0}

    def flaky(gray, languages, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise tess.OcrError("errore iniettato")
        return real(gray, languages, **kw)
    monkeypatch.setattr(engine, "recognize", flaky)
    with pymupdf.open(stream=data) as pdf:
        result = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], workers=1))
    assert [p.status for p in result.pages].count("failed") == 1
    assert len(result.recognized) == 2
    assert "errore iniettato" in result.failed[0].message


def test_annullamento(tmp_path):
    imgs = [scan_image(lines=["Una riga di prova"], dpi=150) for _ in range(4)]
    data = open(image_pdf(tmp_path / "q.pdf", imgs, dpi=150), "rb").read()

    class P:
        cancelled = False

        def update(self, f=None, t=None):
            if f and f > 0:
                self.cancelled = True
                raise OperationCancelled()
    with pytest.raises(OperationCancelled):
        engine.ocr_pdf_bytes(data, ["ita"], "searchable", progress=P())


def test_deterministico(corpus):
    with pymupdf.open(stream=corpus["inclinata-200-rumore"]) as pdf:
        a = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], workers=1))
        b = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], workers=3))
    wa = [(w.text, w.bbox, w.conf) for w in a.pages[0].words]
    wb = [(w.text, w.bbox, w.conf) for w in b.pages[0].words]
    assert wa == wb and wa


def test_errori_chiari(encrypted_pdf):
    with pytest.raises(MissingLanguagesError) as e:
        engine.ocr_pdf_bytes(b"%PDF", ["ita", "deu"], "searchable")
    assert "tedesco" in str(e.value)
    with pytest.raises(tess.OcrError):
        engine.ocr_pdf_bytes(b"non un pdf", ["ita"], "searchable")
    with pytest.raises(tess.OcrError):
        engine.ocr_pdf_bytes(open(encrypted_pdf, "rb").read(), ["ita"], "searchable")
    with pytest.raises(ValueError):
        engine.ocr_pdf_bytes(b"", ["ita"], "boh")


def test_parole_bassa_affidabilita(corpus):
    with pymupdf.open(stream=corpus["inclinata-200-rumore"]) as pdf:
        res = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"])).pages[0]
    assert res.mean_confidence > 80
    assert all(w.conf < 70 for w in res.low_confidence_words())
    assert res.text.startswith("Il presente contratto")


def test_char_accuracy():
    assert engine.char_accuracy("ciao  mondo\n", "ciao mondo") == 1.0
    assert engine.char_accuracy("ciao", "ciaO") == 0.75
