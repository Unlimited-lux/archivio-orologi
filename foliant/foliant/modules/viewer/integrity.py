"""File danneggiati e contenuti non supportati (VIS-07): avvisi non bloccanti sopra le pagine,
segnaposto «Pagina non leggibile» sulle pagine che non si possono ricostruire (file troncato).

L'avviso del file danneggiato e riparato in memoria, con «Salva copia riparata», è del
modulo di conversione (CNV-32, chiave «riparato»): il viewer non ne mostra un secondo."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QFont, QPen

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, theme

TRANSLATIONS = {
    "Chiudi l'avviso": "Close notice",
    "Funzioni del documento non supportate: {f}": "Document features not supported: {f}",
    "moduli XFA dinamici": "dynamic XFA forms",
    "contenuti 3D": "3D content",
    "contenuti multimediali": "multimedia content",
    "Pagina non leggibile": "Unreadable page",
    "Pagina {pages} non leggibile: le altre pagine sono visualizzate normalmente.":
        "Page {pages} is unreadable: the other pages are shown normally.",
    "Pagine {pages} non leggibili: le altre pagine sono visualizzate normalmente.":
        "Pages {pages} are unreadable: the other pages are shown normally.",
}


def unreadable_pages(pdf) -> list[int]:
    """Pagine che non si possono ricostruire (es. file troncato): l'oggetto pagina manca, oppure
    mancano tutti i flussi del suo contenuto. Una pagina con parte del contenuto si mostra
    com'è (resa parziale). Da chiamare con ENGINE_LOCK."""
    out = []
    count = pdf.xref_length()
    for i in range(pdf.page_count):
        try:
            xref = pdf.page_xref(i)
            if not 0 < xref < count:
                raise ValueError
            pdf.xref_object(xref)
            contents = pdf[i].get_contents()
        except Exception:
            out.append(i)
            continue
        if not contents:
            continue           # pagina vuota per scelta: non danneggiata
        readable = 0
        for c in contents:
            try:
                if 0 < c < count and pdf.xref_stream_raw(c) is not None:
                    readable += 1
            except Exception:
                pass
        if not readable:
            out.append(i)
    return out


def page_ranges(pages: list[int]) -> str:
    """[6, 7, 8, 9, 12] → «7–10, 13» (numeri da 1)."""
    parts = []
    start = prev = None
    for p in sorted(pages):
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            parts.append((start, prev))
            start = prev = p
    if start is not None:
        parts.append((start, prev))
    return ", ".join(str(a + 1) if a == b else f"{a + 1}–{b + 1}" for a, b in parts)


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
    BANNER_UNREADABLE = "viewer.unreadable"

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.flagged: "weakref.WeakKeyDictionary[DocumentView, list[str]]" = weakref.WeakKeyDictionary()
        self.unreadable: "weakref.WeakKeyDictionary[DocumentView, set[int]]" = weakref.WeakKeyDictionary()
        ctx.documentOpened.connect(self.check)
        ctx.documentChanged.connect(self._on_changed)

    def is_unreadable(self, view: DocumentView, page: int) -> bool:
        return page in self.unreadable.get(view, ())

    def check(self, view: DocumentView) -> None:
        document = view.document
        with ENGINE_LOCK:
            pdf = document.pdf
            features = unsupported_features(pdf)
            broken = unreadable_pages(pdf) if getattr(document, "repaired", False) or pdf.is_repaired else []
        ids = []
        self._set_unreadable(view, broken)
        if features:
            self.ctx.show_banner(view, self.BANNER_UNSUPPORTED,
                                 _("Funzioni del documento non supportate: {f}").format(f=", ".join(features)),
                                 level="info")
            ids.append(self.BANNER_UNSUPPORTED)
        if ids:
            self.flagged[view] = ids

    # --------------------------------------------------------- pagine illeggibili
    def _set_unreadable(self, view: DocumentView, pages: list[int]) -> None:
        if pages:
            self.unreadable[view] = set(pages)
            if self.paint not in view.overlays:
                view.overlays.append(self.paint)
            text = (_("Pagina {pages} non leggibile: le altre pagine sono visualizzate normalmente.") if len(pages) == 1
                    else _("Pagine {pages} non leggibili: le altre pagine sono visualizzate normalmente."))
            self.ctx.show_banner(view, self.BANNER_UNREADABLE, text.format(pages=page_ranges(pages)),
                                 level="warning", priority=5)
        else:
            if self.unreadable.pop(view, None) is not None:
                self.ctx.hide_banner(view, self.BANNER_UNREADABLE)
            if self.paint in view.overlays:
                view.overlays.remove(self.paint)
        view.viewport().update()

    def _on_changed(self, view: DocumentView) -> None:
        """Dopo una modifica (pagine eliminate o riordinate) l'elenco si ricalcola."""
        if view not in self.unreadable or view.document.closed:
            return
        with ENGINE_LOCK:
            pages = unreadable_pages(view.document.pdf)
        self._set_unreadable(view, pages)

    def paint(self, view: DocumentView, p, page: int) -> None:
        """Segnaposto sopra la pagina illeggibile (coordinate della pagina)."""
        if page not in self.unreadable.get(view, ()):
            return
        w, h = view.page_size(page)
        area = QRectF(0, 0, w, h)
        p.save()
        p.fillRect(area, theme.color("surface_alt"))
        pen = QPen(theme.color("border"))
        pen.setCosmetic(True)
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(area.adjusted(12, 12, -12, -12))
        font = QFont(p.font())
        font.setPointSizeF(max(8.0, min(w, h) / 22))
        p.setFont(font)
        p.setPen(theme.color("text_muted"))
        p.drawText(area, Qt.AlignmentFlag.AlignCenter, _("Pagina non leggibile"))
        p.restore()


def install(ctx: Context) -> IntegrityController:
    add_translations("en", TRANSLATIONS)
    return IntegrityController(ctx)
