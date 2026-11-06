"""Pagine › Inserisci da immagini: foto senza risoluzione reale nel formato del documento."""

from __future__ import annotations

import pymupdf
from PIL import Image

from foliant.modules.pages import images
from foliant.modules.pages.engine import PAPER_SIZES


def _foto_telefono(path, size=(3000, 4000), exif_dpi=72.0):
    """Foto di un orologio scattata col telefono: nessun JFIF, EXIF con 72 dpi segnaposto."""
    im = Image.new("RGB", size, (40, 60, 90))
    ex = Image.Exif()
    ex[0x010F] = "Telefono"
    if exif_dpi:
        ex[0x011A] = exif_dpi
        ex[0x011B] = exif_dpi
        ex[0x0128] = 2
    im.save(path, "JPEG", quality=80, exif=ex.tobytes())
    return str(path)


def _listino(page_size=(595, 842), pages=2) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=page_size[0], height=page_size[1]).insert_text((72, 72), f"Listino orologi p. {i + 1}")
    return doc


def _image_rect(page: pymupdf.Page) -> pymupdf.Rect:
    return page.get_image_rects(page.get_images()[0][0])[0]


def _inside(inner: pymupdf.Rect, outer: pymupdf.Rect, tol=0.5) -> bool:
    return (inner.x0 >= outer.x0 - tol and inner.y0 >= outer.y0 - tol
            and inner.x1 <= outer.x1 + tol and inner.y1 <= outer.y1 + tol)


def test_phone_photo_takes_document_page_size(tmp_path):
    doc = _listino()
    new, errors = images.insert_images(doc, [_foto_telefono(tmp_path / "rolex.jpg")], 1)
    page = doc[new[0]]
    assert (round(page.rect.width), round(page.rect.height)) == (595, 842)
    r = _image_rect(page)
    assert _inside(r, page.rect)
    assert abs(r.width / r.height - 3000 / 4000) < 0.01
    assert abs(r.width - 595) < 1                    # adattata (contenuta) alla pagina


def test_png_without_dpi_landscape_in_letter_document(tmp_path):
    path = tmp_path / "cinturino.png"
    Image.new("RGB", (4000, 3000), (200, 180, 150)).save(path)
    doc = _listino(PAPER_SIZES["Letter"])
    new, _e = images.insert_images(doc, [str(path)], 2)
    page = doc[new[0]]
    w, h = PAPER_SIZES["Letter"]
    assert {round(page.rect.width), round(page.rect.height)} == {round(w), round(h)}
    r = _image_rect(page)
    assert _inside(r, page.rect) and abs(r.width / r.height - 4 / 3) < 0.01


def test_empty_document_uses_a4(tmp_path):
    doc = pymupdf.open()
    new, _e = images.insert_images(doc, [_foto_telefono(tmp_path / "omega.jpg", exif_dpi=None)], 0)
    page = doc[new[0]]
    w, h = PAPER_SIZES["A4"]
    assert (round(page.rect.width), round(page.rect.height)) == (round(w), round(h))
    assert _inside(_image_rect(page), page.rect)


def test_real_dpi_keeps_natural_size(tmp_path):
    path = tmp_path / "garanzia_scansione.jpg"
    Image.new("RGB", (2480, 3508), (250, 250, 250)).save(path, "JPEG", dpi=(300, 300))
    doc = _listino()
    new, _e = images.insert_images(doc, [str(path)], 0)
    page = doc[new[0]]
    assert abs(page.rect.width - 2480 * 72 / 300) < 1 and abs(page.rect.height - 3508 * 72 / 300) < 1
