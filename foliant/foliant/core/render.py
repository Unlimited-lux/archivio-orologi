"""Resa delle pagine per la stampa e l'esportazione in immagini.

Il motore, rendendo le annotazioni, disegna un segnaposto («SIGN») sopra ogni campo firma
ancora vuoto, anche se il file contiene un aspetto vuoto. A schermo fa da indicatore; in
stampa e nelle immagini esportate non deve comparire (FRM-20: il campo vuoto si vede solo a
schermo). `page_pixmap(..., hide_unsigned_signatures=True)` nasconde quei campi solo per la
durata della resa, senza modificare il documento.

`annotations_hidden(pdf, xrefs)` è il meccanismo comune: imposta il flag «Hidden» sulle
annotazioni indicate e lo ripristina subito dopo (lo usa anche il filtro delle annotazioni
della vista, `DocumentView.set_annotation_filter`).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Iterator

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


@contextmanager
def annotations_hidden(pdf: pymupdf.Document, xrefs: Iterable[int]) -> Iterator[None]:
    """Nasconde (flag /F «Hidden») le annotazioni `xrefs` per la durata del blocco.

    All'uscita, anche dopo un errore, /F torna al valore originale: gli oggetti tornano
    identici e il documento non risulta modificato (nemmeno per il salvataggio incrementale).
    Tiene ENGINE_LOCK per tutto il blocco: nessun altro thread vede lo stato provvisorio."""
    with ENGINE_LOCK:
        saved: dict[int, tuple[str, str]] = {}
        try:
            for x in xrefs:
                if x in saved:
                    continue
                kind, value = pdf.xref_get_key(x, "F")
                saved[x] = (kind, value)
                flags = int(value) if kind == "int" else 0
                pdf.xref_set_key(x, "F", str(flags | _HIDDEN))
            yield
        finally:
            for x, (kind, value) in saved.items():
                pdf.xref_set_key(x, "F", value if kind != "null" else "null")


def page_pixmap(pdf: pymupdf.Document, page_index: int, *, hide_unsigned_signatures: bool = False,
                **kwargs) -> pymupdf.Pixmap:
    """Come `pdf[page_index].get_pixmap(**kwargs)`; con `hide_unsigned_signatures` senza segnaposti."""
    with ENGINE_LOCK:
        if not hide_unsigned_signatures or not kwargs.get("annots", True):
            return pdf[page_index].get_pixmap(**kwargs)
        xrefs = unsigned_signature_widgets(pdf, page_index)
        if not xrefs:
            return pdf[page_index].get_pixmap(**kwargs)
        with annotations_hidden(pdf, xrefs):
            return pdf[page_index].get_pixmap(**kwargs)
