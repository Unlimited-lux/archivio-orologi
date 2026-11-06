"""Difetti del collaudo dell'area OCR (B-4 … B-21): un test per difetto, con scansioni
realistiche di documenti di un commerciante di orologi (dati inventati)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.ocr import detect, engine
from tests.modules.ocr.conftest import image_pdf, scan_image

CERTIFICATO = [
    "Certificato di garanzia internazionale",
    "Orologio Omega Speedmaster Professional",
    "Numero di serie 81234567 venduto il 12 marzo 2026",
    "Rivenditore autorizzato Orologeria Verdi di Milano",
]


# ------------------------------------------------------------------ B-5
@pytest.mark.parametrize("rot", [90, 180, 270])
def test_b5_scansione_ruotata_resa_modificabile_non_chiede_ocr(tmp_path, rot):
    """Scanner che salva la pagina coricata con /Rotate: dopo «Rendi modificabile», salvataggio
    e riapertura la pagina è testo, non «scannerizzata senza testo»."""
    img = scan_image(CERTIFICATO, dpi=200, fontsize=14, rotate90=(360 - rot) % 360)
    src = image_pdf(tmp_path / "certificato.pdf", [img], dpi=200)
    with pymupdf.open(src) as doc:
        doc[0].set_rotation(rot)
        data = doc.tobytes()
    with pymupdf.open(stream=data) as doc:
        assert detect.classify_page(doc[0]).category == detect.IMAGE_ONLY
    out = tmp_path / "certificato_modificabile.pdf"
    out.write_bytes(engine.ocr_pdf_bytes(data, ["ita"], engine.EDITABLE))
    with pymupdf.open(str(out)) as doc:
        assert "Speedmaster" in doc[0].get_text()
        info = detect.classify_page(doc[0])
        assert info.category == detect.TEXT
        assert info.category not in detect.NEEDS_OCR


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_b5_pagina_mista_ruotata_resta_mista(tmp_path, rot):
    """Pagina con testo vero e la foto di una garanzia, salvata coricata con /Rotate e mostrata
    diritta: resta «mista» (l'immagine contiene testo da riconoscere)."""
    import cv2
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842) if rot in (0, 180) else doc.new_page(width=842, height=595)
    page.set_rotation(rot)
    d = page.derotation_matrix        # coordinate della pagina mostrata → non ruotate
    page.insert_text(pymupdf.Point(60, 60) * d, "Scheda di valutazione: allegata la copia della garanzia",
                     fontsize=11, rotate=rot)
    img = scan_image(CERTIFICATO, dpi=150, fontsize=16, size=(500, 300))
    ok, buf = cv2.imencode(".png", img)
    page.insert_image(pymupdf.Rect(40, 200, 555, 509) * d, stream=buf.tobytes(), rotate=-rot)
    path = tmp_path / "scheda.pdf"
    doc.save(str(path))
    doc.close()
    with pymupdf.open(str(path)) as d:
        assert d[0].get_text().startswith("Scheda di valutazione")
        assert detect.classify_page(d[0]).category == detect.MIXED


# ------------------------------------------------------------------ B-4
def _ocr_controller():
    from foliant.modules import ocr
    return ocr.controller


@pytest.fixture
def certificato(tmp_path):
    return image_pdf(tmp_path / "certificato.pdf", [scan_image(CERTIFICATO, dpi=300, fontsize=14)], dpi=300)


def _lines(page, sort=True):
    return [" ".join(ln.split()) for ln in page.get_text(sort=sort).splitlines() if ln.strip()]


@pytest.mark.parametrize("mode", [engine.EDITABLE, engine.SEARCHABLE])
def test_b4_correggi_parola_non_lascia_pezzi(window, certificato, mode):
    view = window.open_path(certificato)
    ctl = _ocr_controller()
    ctl.run(view, engine.OcrOptions(languages=["ita"], mode=mode), [0], preview=False)
    rect, word = next((r, w) for r, w in ctl.state(view).placed[0] if w.text == "Speedmaster")
    assert ctl.correct_word(view, 0, rect, word, "Calibro")
    with pymupdf.open(stream=view.document.to_bytes()) as doc:
        page = doc[0]
        lines = _lines(page)
        # ordine di lettura conservato, anche per MuPDF con sort e per l'estrazione semplice
        assert "Orologio Omega Calibro Professional" in lines, lines
        assert lines.index("Orologio Omega Calibro Professional") == 1
        import shutil
        import subprocess
        if shutil.which("pdftotext"):        # poppler (Evince, Okular)
            doc.save(str(certificato) + ".corretto.pdf")
            out = subprocess.run(["pdftotext", str(certificato) + ".corretto.pdf", "-"],
                                 capture_output=True, text=True).stdout
            assert "Orologio Omega Calibro Professional" in [" ".join(ln.split()) for ln in out.splitlines()]
        words = [w[4] for w in page.get_text("words")]
        assert "Speedmaster" not in words and words.count("Calibro") == 1
        row = [w[4] for w in page.get_text("words", sort=True) if w[1] < rect.y1 and w[3] > rect.y0]
        assert row == ["Orologio", "Omega", "Calibro", "Professional"], row
