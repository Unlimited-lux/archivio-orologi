"""Fixture e funzioni di supporto per i test della logica del modulo oggetti."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from foliant.core.document import Document
from foliant.modules.objects import content, engine

A4 = (595, 842)


# ------------------------------------------------------------------ immagini
def png_bytes(w: int = 40, h: int = 20, color=(255, 0, 0), mode: str = "RGB", dpi=None) -> bytes:
    """PNG a tinta unita (o RGBA) di w×h pixel."""
    if mode == "RGBA" and len(color) == 3:
        color = tuple(color) + (128,)
    im = Image.new(mode, (w, h), color)
    buf = io.BytesIO()
    kw = {"dpi": dpi} if dpi else {}
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def split_png(w: int = 40, h: int = 20, left=(255, 0, 0), right=(0, 0, 255)) -> bytes:
    """PNG asimmetrico: metà sinistra `left`, metà destra `right`."""
    im = Image.new("RGB", (w, h), right)
    im.paste(Image.new("RGB", (w // 2, h), left), (0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(w: int = 32, h: int = 16, color=(0, 128, 255), exif: bool = False, orientation: int | None = None) -> bytes:
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    kw = {}
    if exif or orientation:
        ex = Image.Exif()
        ex[0x010F] = "FotocameraDiProva"  # Make
        if orientation:
            ex[0x0112] = orientation
        kw["exif"] = ex.tobytes()
    im.save(buf, "JPEG", quality=85, **kw)
    return buf.getvalue()


def sos_data(jpeg: bytes) -> bytes:
    """Dati compressi dal marcatore SOS (FFDA) in poi."""
    i = jpeg.index(b"\xff\xda")
    return jpeg[i:]


# ------------------------------------------------------------------ PDF
def new_pdf(pages: int = 1, size=A4) -> pymupdf.Document:
    pdf = pymupdf.open()
    for _ in range(pages):
        pdf.new_page(width=size[0], height=size[1])
    return pdf


def add_image(pdf: pymupdf.Document, page_index: int, rect, data: bytes | None = None, xref: int = 0) -> int:
    """Disegna un'immagine con PyMuPDF; restituisce l'xref."""
    page = pdf[page_index]
    if xref:
        return page.insert_image(pymupdf.Rect(rect), xref=xref, keep_proportion=False)
    return page.insert_image(pymupdf.Rect(rect), stream=data if data is not None else png_bytes(), keep_proportion=False)


def image_objects(pdf: pymupdf.Document, page_index: int = 0):
    return [o for o in engine.objects(pdf, page_index).objects if o.kind == content.IMAGE]


def render(pdf: pymupdf.Document, page_index: int = 0, dpi: int = 72, clip=None) -> bytes:
    page = pdf[page_index]
    return page.get_pixmap(dpi=dpi, clip=clip, alpha=False, annots=False).samples


def pixel(pdf: pymupdf.Document, page_index: int, x: float, y: float) -> tuple:
    """Colore del pixel in (x, y) punti, resa a 72 dpi."""
    pix = pdf[page_index].get_pixmap(dpi=72, alpha=False, annots=False)
    return pix.pixel(int(x), int(y))


def count_image_xobjects(data: bytes) -> int:
    d = pymupdf.open(stream=data, filetype="pdf")
    try:
        n = 0
        for x in range(1, d.xref_length()):
            if d.xref_get_key(x, "Subtype")[1] == "/Image" and d.xref_get_key(x, "Type")[1] in ("/XObject", "null"):
                # le SMask sono anch'esse /Image: si contano solo quelle referenziate come XObject di pagina
                n += 1
        return n
    finally:
        d.close()


def page_image_xrefs(data: bytes) -> set[int]:
    """Xref delle immagini usate dalle pagine (escluse le maschere)."""
    d = pymupdf.open(stream=data, filetype="pdf")
    try:
        out = set()
        for p in d:
            for info in p.get_images(full=True):
                out.add(info[0])
        return out
    finally:
        d.close()


def approx_rect(r, expected, tol=0.5) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(tuple(r), tuple(expected)))


def doc_from(pdf: pymupdf.Document) -> Document:
    """Document (con cronologia) dal contenuto di un documento del motore, non modificato."""
    return Document.from_bytes(pdf.tobytes(), modified=False)


@pytest.fixture
def blank():
    pdf = new_pdf()
    yield pdf
    pdf.close()


@pytest.fixture(autouse=True)
def _clear_clipboard():
    """Svuota gli appunti dopo ogni test: un'immagine rimasta negli appunti fa
    terminare male il processo con la piattaforma Qt «offscreen»."""
    yield
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            QGuiApplication.clipboard().clear()
    except Exception:
        pass
