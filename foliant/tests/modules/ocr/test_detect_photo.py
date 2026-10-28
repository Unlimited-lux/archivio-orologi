"""Pagina con la sola foto di un orologio: niente proposta di OCR; un documento fotografato sì."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

from foliant.modules.ocr import detect

from .conftest import image_pdf
from .realistic import id_card_photo, invoice_phone_photo


def watch_photo():
    """Foto di un orologio su un tavolo: sfumature, cerchi, indici, riflessi; nessuna scritta."""
    rng = np.random.default_rng(3)
    h, w = 1600, 1200
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.dstack([90 + 60 * xx / w, 70 + 40 * yy / h, 50 + 30 * (xx + yy) / (w + h)]).astype(np.uint8)
    cx, cy = w // 2, h // 2
    cv2.rectangle(img, (cx - 120, 0), (cx + 120, h), (60, 45, 35), -1)          # cinturino
    cv2.circle(img, (cx, cy), 380, (190, 190, 200), -1)                         # cassa
    cv2.circle(img, (cx, cy), 330, (25, 30, 60), -1)                            # quadrante blu
    for k in range(12):
        a = k * np.pi / 6
        p0 = (int(cx + 280 * np.cos(a)), int(cy + 280 * np.sin(a)))
        p1 = (int(cx + 315 * np.cos(a)), int(cy + 315 * np.sin(a)))
        cv2.line(img, p0, p1, (230, 230, 235), 10)
    cv2.line(img, (cx, cy), (cx + 150, cy - 170), (230, 230, 235), 12)
    cv2.line(img, (cx, cy), (cx - 40, cy + 250), (230, 230, 235), 8)
    cv2.ellipse(img, (cx - 120, cy - 150), (120, 50), -30, 0, 360, (120, 120, 160), -1)   # riflesso
    img = np.clip(img.astype(np.int16) + rng.normal(0, 6, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
    return img


def _category(tmp_path, img, dpi, **kw):
    with pymupdf.open(image_pdf(str(tmp_path / "p.pdf"), [img], dpi=dpi)) as pdf:
        return detect.classify_page(pdf[0], **kw).category


def test_foto_di_orologio_senza_testo(tmp_path):
    assert _category(tmp_path, watch_photo(), 150, check_photo=True) == detect.PHOTO
    assert not detect.PageInfo(0, detect.PHOTO).needs_ocr
    # senza il controllo (motore, OCR in blocco) resta una pagina di sole immagini da riconoscere
    assert _category(tmp_path, watch_photo(), 150) == detect.IMAGE_ONLY


@pytest.mark.parametrize("gen", [id_card_photo, invoice_phone_photo])
def test_documento_fotografato_resta_da_riconoscere(tmp_path, gen):
    img, dpi, _expected = gen()
    assert _category(tmp_path, img, dpi, check_photo=True) == detect.IMAGE_ONLY


def test_pagina_con_poco_testo_ha_caratteri(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Pagina 1", fontsize=24)
    page.insert_text((72, 140), "Orologeria Verdi - contratto di compravendita", fontsize=12)
    assert detect.has_characters(detect.page_gray(page))
