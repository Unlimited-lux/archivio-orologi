"""Contenuti non supportati (VIS-07): avviso non bloccante sopra le pagine.

L'avviso del file danneggiato e riparato in memoria, con «Salva copia riparata», è del
modulo di conversione (CNV-32, chiave «riparato»): il viewer non ne mostra un secondo."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

TRANSLATIONS = {
    "Chiudi l'avviso": "Close notice",
    "Funzioni del documento non supportate: {f}": "Document features not supported: {f}",
    "moduli XFA dinamici": "dynamic XFA forms",
    "contenuti 3D": "3D content",
    "contenuti multimediali": "multimedia content",
}


def unsupported_features(pdf) -> list[str]:
    """Funzioni presenti nel documento che l'applicazione non riproduce."""
    found = []
    try:
        cat = pdf.pdf_catalog()
        kind, _v = pdf.xref_get_key(cat, "AcroForm/XFA")
        if kind not in ("null", ""):
            found.append(_("moduli XFA dinamici"))
        subtypes = set()
        for i in range(min(pdf.page_count, 200)):
            for annot_xref in [a[0] for a in pdf[i].annot_xrefs()]:
                k, st = pdf.xref_get_key(annot_xref, "Subtype")
                subtypes.add(st)
        if "/3D" in subtypes:
            found.append(_("contenuti 3D"))
        if subtypes & {"/Movie", "/Sound", "/Screen", "/RichMedia"}:
            found.append(_("contenuti multimediali"))
    except Exception:
        pass
    return found


class IntegrityController(QObject):
    """Avviso del documento (barra del nucleo, `ctx.show_banner`) per le funzioni non supportate."""

    BANNER_UNSUPPORTED = "viewer.unsupported"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.flagged: "weakref.WeakKeyDictionary[DocumentView, list[str]]" = weakref.WeakKeyDictionary()
        ctx.documentOpened.connect(self.check)

    def check(self, view: DocumentView) -> None:
        document = view.document
        with ENGINE_LOCK:
            pdf = document.pdf
            features = unsupported_features(pdf)
        ids = []
        if features:
            self.ctx.show_banner(view, self.BANNER_UNSUPPORTED,
                                 _("Funzioni del documento non supportate: {f}").format(f=", ".join(features)),
                                 level="info")
            ids.append(self.BANNER_UNSUPPORTED)
        if ids:
            self.flagged[view] = ids


def install(ctx: Context) -> IntegrityController:
    add_translations("en", TRANSLATIONS)
    return IntegrityController(ctx)
