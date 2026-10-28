"""File danneggiati (VIS-07): avviso non bloccante quando il motore ha dovuto
ricostruire il file, con il comando «Ripara e salva con nome»."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

TRANSLATIONS = {
    "Il file era danneggiato ed è stato riparato in memoria.": "The file was damaged and has been repaired in memory.",
    "Ripara e salva con nome…": "Repair and save as…",
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
    """Avvisi del documento (barre del nucleo, `ctx.show_banner`): file ricostruito con
    «Ripara e salva con nome»; funzioni del documento non supportate."""

    # Stessa chiave dell'avviso del modulo di conversione (CNV-32): un solo avviso per il
    # file riparato; se l'ha già mostrato un altro modulo non lo si duplica.
    BANNER_REPAIRED = "riparato"
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
            repaired = bool(getattr(document, "repaired", False) or getattr(pdf, "is_repaired", False))
            features = unsupported_features(pdf)
        ids = []
        if repaired and self.BANNER_REPAIRED not in self.ctx.banners(view):
            self.ctx.show_banner(view, self.BANNER_REPAIRED, _("Il file era danneggiato ed è stato riparato in memoria."),
                                 [(_("Ripara e salva con nome…"), lambda v=view: self.repair(v))],
                                 level="warning", priority=10)
            ids.append(self.BANNER_REPAIRED)
        if features:
            self.ctx.show_banner(view, self.BANNER_UNSUPPORTED,
                                 _("Funzioni del documento non supportate: {f}").format(f=", ".join(features)),
                                 level="info")
            ids.append(self.BANNER_UNSUPPORTED)
        if ids:
            self.flagged[view] = ids

    def repair(self, view: DocumentView) -> bool:
        ok = self.ctx.window.save_view(view, save_as=True)
        if ok:
            self.ctx.hide_banner(view, self.BANNER_REPAIRED)
            ids = self.flagged.get(view, [])
            if self.BANNER_REPAIRED in ids:
                ids.remove(self.BANNER_REPAIRED)
        return ok


def install(ctx: Context) -> IntegrityController:
    add_translations("en", TRANSLATIONS)
    return IntegrityController(ctx)
