"""Adattamenti dello zoom in più rispetto al nucleo (VIS-10): adatta all'altezza e adatta al
contenuto (ingombro degli oggetti visibili della pagina). Come gli adattamenti del nucleo,
restano attivi quando la finestra cambia dimensione, finché l'utente non cambia lo zoom."""

from __future__ import annotations

import weakref

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPointF, QTimer

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

TRANSLATIONS = {
    "Adatta all'altezza": "Fit height",
    "Adatta al contenuto": "Fit content",
    "La pagina intera in altezza": "The whole page height in view",
    "L'ingombro del contenuto visibile della pagina riempie la vista":
        "The visible content of the page fills the view",
    "Pagina senza contenuto visibile.": "Page without visible content.",
}

FIT_HEIGHT = "height"
FIT_CONTENT = "content"


def content_bbox(page: pymupdf.Page) -> pymupdf.Rect | None:
    """Ingombro degli oggetti disegnati sulla pagina (testo, immagini, grafica), ritagliato alla
    pagina; None se la pagina non ha contenuto visibile (chiamare con ENGINE_LOCK)."""
    area = page.rect
    box = pymupdf.Rect()
    try:
        items = page.get_bboxlog()
    except Exception:
        return None
    for kind, rect in items:
        if kind.startswith("ignore"):
            continue
        r = pymupdf.Rect(rect) & area
        if r.is_empty or r.is_infinite:
            continue
        box |= r
    return None if box.is_empty else box


class ZoomFitController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.fits: "weakref.WeakKeyDictionary[DocumentView, str]" = weakref.WeakKeyDictionary()
        self._applying = False
        self.act_height = ctx.action(_("Adatta all'altezza"), "move-vertical", lambda: self.set_fit(FIT_HEIGHT),
                                     tooltip=_("La pagina intera in altezza"))
        self.act_content = ctx.action(_("Adatta al contenuto"), "scan", lambda: self.set_fit(FIT_CONTENT),
                                      tooltip=_("L'ingombro del contenuto visibile della pagina riempie la vista"))
        for a in (self.act_height, self.act_content):
            ctx.add_menu_action(a, _("Vista"))
        ctx.documentOpened.connect(self._watch)
        for v in ctx.views():
            self._watch(v)

    def _watch(self, view: DocumentView) -> None:
        view.viewport().installEventFilter(self)
        view.zoomChanged.connect(lambda _z, v=view: self._on_zoom(v))
        view.currentPageChanged.connect(lambda _p, v=view: self._on_page(v))

    # ------------------------------------------------------------------ comandi
    def set_fit(self, fit: str, view: DocumentView | None = None) -> bool:
        view = view or self.ctx.current_view()
        if view is None:
            return False
        if not self.apply(view, fit):
            return False
        self.fits[view] = fit
        return True

    def fit_of(self, view: DocumentView) -> str | None:
        return self.fits.get(view)

    def apply(self, view: DocumentView, fit: str) -> bool:
        page = view.current_page
        vw, vh = view.viewport().width(), view.viewport().height()
        if fit == FIT_HEIGHT:
            rect = view.page_rect(page)
            if rect.height() <= 0:
                return False
            target = vh - 2 * view.MARGIN
            self._set_zoom(view, view.zoom * target / rect.height())
            view.go_to_page(page)
            return True
        doc = view.document
        with ENGINE_LOCK:
            box = content_bbox(doc.pdf[page])
        if box is None:
            self.ctx.status(_("Pagina senza contenuto visibile."))
            return False
        vr = view.page_to_view(page, box)
        if vr.width() <= 0 or vr.height() <= 0:
            return False
        margin = 2 * view.MARGIN
        factor = min((vw - margin) / vr.width(), (vh - margin) / vr.height())
        self._set_zoom(view, view.zoom * factor)
        # centro del contenuto al centro della vista (dimensioni rilette: dopo lo zoom può
        # comparire la barra di scorrimento orizzontale)
        vw, vh = view.viewport().width(), view.viewport().height()
        c = view.page_to_view(page, box).center()
        view.horizontalScrollBar().setValue(int(round(view.horizontalScrollBar().value() + c.x() - vw / 2)))
        view.verticalScrollBar().setValue(int(round(view.verticalScrollBar().value() + c.y() - vh / 2)))
        return True

    def _set_zoom(self, view: DocumentView, zoom: float) -> None:
        self._applying = True
        try:
            view.set_zoom(zoom, anchor=QPointF(0, 0))
        finally:
            self._applying = False

    # ------------------------------------------------------------------ eventi
    def _on_zoom(self, view: DocumentView) -> None:
        if not self._applying:
            self.fits.pop(view, None)      # zoom cambiato dall'utente: l'adattamento finisce

    def _on_page(self, view: DocumentView) -> None:
        if self.fits.get(view) == FIT_CONTENT:
            self._later(view)              # l'ingombro cambia da pagina a pagina

    def _later(self, view: DocumentView) -> None:
        def run(v=weakref.ref(view)):
            target = v()
            try:
                if target is not None and target in self.fits and not target.document.closed:
                    self.apply(target, self.fits[target])
            except RuntimeError:
                pass
        QTimer.singleShot(0, run)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            for view in list(self.fits.keys()):
                if view.viewport() is obj:
                    self._later(view)
        return False


def install(ctx: Context) -> ZoomFitController:
    add_translations("en", TRANSLATIONS)
    return ZoomFitController(ctx)
