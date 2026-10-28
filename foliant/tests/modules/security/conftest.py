"""Fixture del modulo Proteggi: PDF con dati personali, immagini, script e livelli."""

from __future__ import annotations

import pymupdf
import pytest

PII_LINES = [
    "Cliente: Mario Rossi, email mario.rossi@esempio.it",
    "Telefono +39 347 123 4567 oppure 02 1234 5678",
    "IBAN IT60 X054 2811 1010 0000 0123 456 per il bonifico",
    "Codice fiscale RSSMRA80A01H501U nato il 12/03/1980",
    "Partita IVA 01234567897, carta 4111 1111 1111 1111",
    "Orologio Submariner, prezzo concordato EUR 12.500,00",
    "Sito https://www.esempio.it e server 192.168.1.10",
]


def _png(color=(200, 30, 30), size=(60, 40)) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


@pytest.fixture
def png_bytes():
    return _png()


@pytest.fixture
def pii_pdf(tmp_path):
    path = tmp_path / "dati.pdf"
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page()
        y = 80
        for line in PII_LINES:
            page.insert_text((60, y), line, fontsize=11)
            y += 20
        page.insert_text((60, y + 20), f"Pagina {n + 1} - riservato", fontsize=11)
    doc.set_metadata({"title": "Contratto", "author": "Mario Rossi", "creator": "Prova"})
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def image_pdf(tmp_path):
    path = tmp_path / "immagine.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Documento d'identita allegato", fontsize=12)
    page.insert_image(pymupdf.Rect(100, 200, 300, 333), stream=_png())
    page.draw_rect(pymupdf.Rect(350, 200, 450, 260), color=(0, 0, 1), fill=(0, 0, 1))
    page.insert_text((360, 300), "Firma Rossi", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def active_pdf(tmp_path):
    """PDF con script all'apertura, collegamento esterno, file incorporato e commento."""
    path = tmp_path / "attivo.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Documento con contenuti attivi", fontsize=12)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 120, 200, 140),
                      "uri": "https://esterno.example.com/traccia"})
    annot = page.add_text_annot((300, 100), "Nota interna")
    annot.update()
    doc.embfile_add("segreto.txt", b"dati riservati", filename="segreto.txt")
    js = doc.get_new_xref()
    doc.update_object(js, "<< /Type /Action /S /JavaScript /JS (app.alert\\('ciao'\\);) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    doc.set_metadata({"author": "Ufficio vendite", "title": "Interno"})
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def layered_pdf(tmp_path):
    path = tmp_path / "livelli.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    visible = doc.add_ocg("Visibile", on=True)
    hidden = doc.add_ocg("Nascosto", on=False)
    page.insert_text((72, 100), "Testo sempre presente", fontsize=12)
    page.insert_text((72, 140), "Testo del livello visibile", fontsize=12, oc=visible)
    page.insert_text((72, 180), "Prezzo minimo segreto", fontsize=12, oc=hidden)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def cyclic_pdf(tmp_path):
    """Albero delle pagine ciclico, script all'apertura, /Launch, sola password del proprietario."""
    path = tmp_path / "ciclo.pdf"
    doc = pymupdf.open()
    for n in range(2):
        doc.new_page().insert_text((72, 100), f"Pagina {n + 1}", fontsize=12)
    js = doc.get_new_xref()
    doc.update_object(js, "<< /Type /Action /S /JavaScript /JS (app.alert\\(1\\);) >>")
    launch = doc.get_new_xref()
    doc.update_object(launch, "<< /Type /Action /S /Launch /F (calc.exe) >>")
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "OpenAction", f"{js} 0 R")
    doc.xref_set_key(cat, "AA", f"<< /WC {launch} 0 R >>")
    plain = tmp_path / "ciclo-chiaro.pdf"
    doc.save(str(plain))
    doc.close()
    doc = pymupdf.open(str(plain))
    root = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    kids = doc.xref_get_key(root, "Kids")[1].strip("[] ")
    doc.xref_set_key(root, "Kids", f"[{kids} {root} 0 R]")
    doc.xref_set_key(root, "Count", "3")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    doc.close()
    return str(path)


def build_many_fields(path, count: int = 5000) -> str:
    """PDF di una pagina con `count` campi di testo, costruito a oggetti (veloce)."""
    doc = pymupdf.open()
    page = doc.new_page()
    xrefs = []
    for n in range(count):
        x = doc.get_new_xref()
        x0, y0 = 10 + (n % 50) * 11, 10 + (n // 50) * 8
        doc.update_object(x, f"<< /Type /Annot /Subtype /Widget /FT /Tx /T (c{n}) /V (valore {n}) "
                             f"/Rect [{x0} {y0} {x0 + 10} {y0 + 7}] /P {page.xref} 0 R >>")
        xrefs.append(x)
    refs = " ".join(f"{x} 0 R" for x in xrefs)
    doc.xref_set_key(page.xref, "Annots", f"[{refs}]")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<< /Fields [{refs}] >>")
    doc.save(str(path))
    doc.close()
    return str(path)


def build_many_annots(path, count: int = 2000) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    xrefs = []
    for n in range(count):
        x = doc.get_new_xref()
        x0, y0 = 10 + (n % 50) * 10, 10 + (n // 50) * 10
        doc.update_object(x, f"<< /Type /Annot /Subtype /Text /Contents (n{n}) "
                             f"/Rect [{x0} {y0} {x0 + 8} {y0 + 8}] /P {page.xref} 0 R >>")
        xrefs.append(x)
    doc.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")
    doc.save(str(path))
    doc.close()
    return str(path)
