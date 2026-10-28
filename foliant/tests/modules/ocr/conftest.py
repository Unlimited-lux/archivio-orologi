"""Corpus di prova OCR: pagine con frasi italiane note rasterizzate come scansioni."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

FRASI = [
    "Il presente contratto di compravendita riguarda un orologio da polso",
    "in acciaio con cinturino in pelle e quadrante color crema.",
    "Il venditore garantisce che il bene è originale e funzionante,",
    "perché revisionato nel laboratorio di Milano il mese scorso.",
    "Il prezzo concordato è di euro tremila, più IVA, da versare",
    "con bonifico bancario entro trenta giorni dalla firma.",
    "Città, data e firma delle parti: Verdi Rossi Bianchi.",
]


def scan_image(lines=FRASI, dpi=300, fontsize=12, skew=0.0, noise=0.0, rotate90=0, size=(595, 842),
               background=255, fontname="helv", top=90):
    """Immagine RGB (numpy) di una pagina «scansionata» con le righe date."""
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    y = top
    for line in lines:
        page.insert_text((60, y), line, fontsize=fontsize, fontname=fontname)
        y += fontsize * 1.8
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    doc.close()
    if background != 255:
        img = np.minimum(img, background).astype(np.uint8)
    if skew:
        h, w = img.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
        img = cv2.warpAffine(img, m, (w, h), borderValue=(background,) * 3)
    if noise:
        rng = np.random.default_rng(1234)
        mask = rng.random(img.shape[:2]) < noise
        salt = rng.random(img.shape[:2]) < 0.5
        img[mask & salt] = 255
        img[mask & ~salt] = 0
    if rotate90:
        img = np.ascontiguousarray(np.rot90(img, k=-(rotate90 // 90)))
    return img


def image_pdf(path, images, dpi=300):
    """Salva un PDF «scansionato»: una pagina per immagine, a piena pagina."""
    doc = pymupdf.open()
    for img in images:
        h, w = img.shape[:2]
        page = doc.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
        ok, buf = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        page.insert_image(page.rect, stream=buf.tobytes())
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def scan_pdf(tmp_path):
    return image_pdf(tmp_path / "scansione.pdf", [scan_image(dpi=300)])


@pytest.fixture(autouse=True)
def _user_tessdata(tmp_path, monkeypatch):
    """Isola la cartella delle lingue dell'utente."""
    monkeypatch.setenv("FOLIANT_TESSDATA_DIR", str(tmp_path / "tessdata-user"))
    from foliant.modules.ocr import languages
    languages._reset_caches()
    yield
    languages._reset_caches()
