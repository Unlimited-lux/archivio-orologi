"""Qualità OCR su scansioni realistiche dell'attività (documenti d'identità fotografati, fatture,
certificati di garanzia con timbri e firme, scansioni a 150 dpi in scala di grigi).

Misura la percentuale di parole attese ritrovate nel testo riconosciuto."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.ocr import engine
from foliant.modules.ocr.preprocess import PreprocessOptions

from .conftest import image_pdf
from .realistic import (gray_150, id_card_far, id_card_photo, invoice, invoice_phone_photo, passport_mrz,
                        red_stamp_over_text, skewed_gray_scan, small_gray_150, thermal_receipt, warranty,
                        word_accuracy)

CASES = {
    "carta_identita_foto": (id_card_photo, 0.95),
    "fattura": (invoice, 0.95),
    "garanzia_timbro_firma": (warranty, 0.95),
    "ricevuta_grigi_150": (gray_150, 0.95),
    "carta_identita_lontana": (id_card_far, 0.95),
    "passaporto_mrz": (passport_mrz, 0.95),
    "testo_piccolo_150": (small_gray_150, 0.95),
    "timbro_rosso": (red_stamp_over_text, 0.9),
    "scontrino_termico": (thermal_receipt, 0.95),
    "fattura_fotografata": (invoice_phone_photo, 0.95),
    "fattura_storta_150": (skewed_gray_scan, 0.95),
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


def test_timbro_colorato_sopra_il_testo_si_attenua_titolo_colorato_resta():
    import cv2
    import numpy as np

    from foliant.modules.ocr.preprocess import ocr_gray

    img = np.full((300, 900, 3), 255, np.uint8)
    cv2.putText(img, "Titolo blu", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 40, 200), 4)
    cv2.putText(img, "IBAN 0000 0123", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    stamp = np.full_like(img, 255)
    cv2.rectangle(stamp, (300, 140), (700, 280), (200, 30, 30), 8)
    cv2.putText(stamp, "PAGATO", (330, 250), cv2.FONT_HERSHEY_DUPLEX, 2, (200, 30, 30), 6)
    scan = (img.astype(np.uint16) * stamp // 255).astype(np.uint8)
    g = ocr_gray(scan)
    assert g[140:146, 600:700].min() >= 190          # cornice del timbro attenuata (era ~80)
    assert g[170:205, 20:290].min() < 60            # testo nero intatto
    assert g[20:70, 20:300].min() < 120             # titolo colorato lontano dal nero: intatto


@pytest.mark.parametrize("gen", [invoice, invoice_phone_photo, skewed_gray_scan])
def test_tabella_fattura_righe_con_colonne_separate_da_tabulazioni(tmp_path, gen):
    """Il testo di una tabella esce riga per riga, con le celle separate da tabulazioni (incollabile
    in un foglio di calcolo), anche da una foto o da una scansione storta."""
    from foliant.modules.ocr.textexport import page_text

    img, dpi, _expected = gen()
    page = _ocr(tmp_path, img, dpi)
    assert "Cinturino in pelle marrone\t2\t85,50 €\t171,00 €" in page.text
    assert "Descrizione\tQ.tà\tPrezzo\tImporto" in page_text(page, paragraphs=True, dehyphenate=True)
