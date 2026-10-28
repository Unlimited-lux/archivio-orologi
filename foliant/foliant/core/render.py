"""Resa delle pagine per la stampa e l'esportazione in immagini.

Il motore, rendendo le annotazioni, disegna un segnaposto («SIGN») sopra ogni campo firma
ancora vuoto, anche se il file contiene un aspetto vuoto. A schermo fa da indicatore; in
stampa e nelle immagini esportate non deve comparire (FRM-20: il campo vuoto si vede solo a
schermo). `page_pixmap(..., hide_unsigned_signatures=True)` nasconde quei campi solo per la
durata della resa, senza modificare il documento.
"""

from __future__ import annotations

import pymupdf

from .engine import ENGINE_LOCK

_HIDDEN = 2   # bit /F «Hidden» delle annotazioni (ISO 32000, 12.5.3)


def unsigned_signature_widgets(pdf: pymupdf.Document, page_index: int) -> list[int]:
    """xref dei campi firma senza firma (/FT /Sig senza /V) della pagina."""
    page = pdf[page_index]
    out = []
    for w in page.widgets():
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and pdf.xref_get_key(w.xref, "V")[0] == "null":
            out.append(w.xref)
    return out


def page_pixmap(pdf: pymupdf.Document, page_index: int, *, hide_unsigned_signatures: bool = False,
                **kwargs) -> pymupdf.Pixmap:
    """Come `pdf[page_index].get_pixmap(**kwargs)`; con `hide_unsigned_signatures` senza segnaposti."""
    with ENGINE_LOCK:
        if not hide_unsigned_signatures or not kwargs.get("annots", True):
            return pdf[page_index].get_pixmap(**kwargs)
        xrefs = unsigned_signature_widgets(pdf, page_index)
        if not xrefs:
            return pdf[page_index].get_pixmap(**kwargs)
        saved = {x: pdf.xref_get_key(x, "F") for x in xrefs}
        try:
            for x, (kind, value) in saved.items():
                flags = int(value) if kind == "int" else 0
                pdf.xref_set_key(x, "F", str(flags | _HIDDEN))
            return pdf[page_index].get_pixmap(**kwargs)
        finally:
            for x, (kind, value) in saved.items():
                pdf.xref_set_key(x, "F", value if kind != "null" else "null")
