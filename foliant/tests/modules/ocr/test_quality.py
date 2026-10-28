"""Qualità OCR su scansioni realistiche dell'attività (documenti d'identità fotografati, fatture,
certificati di garanzia con timbri e firme, scansioni a 150 dpi in scala di grigi).

Misura la percentuale di parole attese ritrovate nel testo riconosciuto."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.ocr import engine
from foliant.modules.ocr.preprocess import PreprocessOptions

from .conftest import image_pdf
from .realistic import (gray_150, id_card_far, id_card_photo, invoice, passport_mrz, red_stamp_over_text,
                        small_gray_150, thermal_receipt, warranty, word_accuracy)

CASES = {
    "carta_identita_foto": (id_card_photo, 0.95),
    "fattura": (invoice, 0.95),
    "garanzia_timbro_firma": (warranty, 0.95),
    "ricevuta_grigi_150": (gray_150, 0.95),
    "carta_identita_lontana": (id_card_far, 0.95),
    "passaporto_mrz": (passport_mrz, 0.9),
    "testo_piccolo_150": (small_gray_150, 0.95),
    "timbro_rosso": (red_stamp_over_text, 0.9),
    "scontrino_termico": (thermal_receipt, 0.95),
}


def _ocr(tmp_path, img, dpi, **popts):
    path = image_pdf(str(tmp_path / "scan.pdf"), [img], dpi=dpi)
    opts = engine.OcrOptions(languages=["ita", "eng"])
    if popts:
        opts.preprocess = PreprocessOptions(**popts)
    with pymupdf.open(path) as pdf:
        return engine.recognize_document(pdf, opts).pages[0]


@pytest.mark.parametrize("name", list(CASES))
def test_parole_corrette(tmp_path, name):
    gen, threshold = CASES[name]
    img, dpi, expected = gen()
    page = _ocr(tmp_path, img, dpi)
    assert word_accuracy(page.text, expected) >= threshold, page.text


def test_osd_non_capovolge_il_passaporto(tmp_path):
    """La zona MRZ («<<<<») inganna l'OSD, che propone 180°: la rotazione va verificata leggendo."""
    img, dpi, _expected = passport_mrz()
    page = _ocr(tmp_path, img, dpi)
    assert page.rotation == 0
    assert "ROSSI" in page.text


def test_pagina_davvero_capovolta_si_raddrizza(tmp_path):
    import numpy as np
    img, dpi, expected = invoice()
    page = _ocr(tmp_path, np.ascontiguousarray(img[::-1, ::-1]), dpi)
    assert page.rotation == 180
    assert word_accuracy(page.text, expected) >= 0.95


def test_scansione_grigia_150_non_viene_impastata(tmp_path):
    """La normalizzazione dello sfondo e lo stiramento del contrasto si applicano solo se servono:
    su una scansione grigia pulita a 150 dpi ingrossavano le lettere fino a renderle illeggibili."""
    img, dpi, expected = small_gray_150()
    for flags in ({}, {"denoise": False}):
        assert word_accuracy(_ocr(tmp_path, img, dpi, **flags).text, expected) >= 0.95
