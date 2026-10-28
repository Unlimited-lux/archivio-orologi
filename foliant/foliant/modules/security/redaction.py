"""Redazione (oscuramento definitivo): segni di anteprima e applicazione.

Logica pura. I segni sono annotazioni di redazione del PDF: si vedono come
riquadri contornati, si possono annullare, salvare e rivedere; l'applicazione
rimuove davvero testo, immagini e grafica sotto i riquadri e li sostituisce con
un riquadro pieno (con testo sovrapposto facoltativo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import pymupdf

from foliant.api import _

MARK_TITLE = "Foliant"
WHOLE_PAGE = "pagina-intera"

IMAGES_PIXELS = "pixels"      # cancella solo i pixel coperti
IMAGES_REMOVE = "remove"      # elimina le immagini toccate
GRAPHICS_COVERED = "covered"  # elimina la grafica interamente coperta
GRAPHICS_TOUCHED = "touched"  # elimina la grafica che tocca l'area
GRAPHICS_NONE = "none"


@dataclass
class MarkStyle:
    fill: tuple[float, float, float] = (0.0, 0.0, 0.0)
    overlay_text: str = ""
    text_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    font_size: float = 0.0           # 0 = automatico (adattato al riquadro)
    repeat_text: bool = False        # ripeti il testo per riempire il riquadro


@dataclass
class ApplyOptions:
    images: str = IMAGES_PIXELS
    graphics: str = GRAPHICS_COVERED
    remove_annotations: bool = True   # commenti e collegamenti che toccano le aree


@dataclass
class Mark:
    page: int
    xref: int
    rect: pymupdf.Rect
    text: str          # testo coperto (per l'elenco)
    source: str        # area, selezione, ricerca, schema:<id>, pagina-intera

    @property
    def whole_page(self) -> bool:
        return self.source == WHOLE_PAGE


def _clean_color(c: Iterable[float]) -> tuple[float, float, float]:
    vals = [min(1.0, max(0.0, float(v))) for v in c][:3]
    while len(vals) < 3:
        vals.append(0.0)
    return tuple(vals)  # type: ignore[return-value]


def contrast_color(fill: tuple[float, float, float]) -> tuple[float, float, float]:
    lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
    return (0.0, 0.0, 0.0) if lum > 0.55 else (1.0, 1.0, 1.0)


def page_box(page: pymupdf.Page) -> pymupdf.Rect:
    """Rettangolo dell'intera pagina in coordinate non ruotate."""
    return pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def covered_text(page: pymupdf.Page, rect: pymupdf.Rect, limit: int = 200) -> str:
    try:
        text = page.get_textbox(rect) or ""
    except Exception:
        text = ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def add_mark(pdf: pymupdf.Document, page_index: int, rect: pymupdf.Rect, style: MarkStyle,
             source: str = "area", text: str | None = None) -> int | None:
    """Aggiunge un segno di redazione. Restituisce lo xref (None se l'area è vuota)."""
    page = pdf[page_index]
    rect = pymupdf.Rect(rect).normalize() & page_box(page)
    if rect.is_empty or rect.width < 0.5 or rect.height < 0.5:
        return None
    fill = _clean_color(style.fill)
    overlay = style.overlay_text.strip() or None
    kwargs: dict = {"fill": fill, "cross_out": False}
    if overlay:
        size = style.font_size or max(4.0, min(11.0, rect.height * 0.6))
        if style.repeat_text:
            overlay = _repeat(overlay, rect, size)
        kwargs.update(text=overlay, fontname="helv", fontsize=size, align=pymupdf.TEXT_ALIGN_CENTER,
                      text_color=_clean_color(style.text_color))
    annot = page.add_redact_annot(rect, **kwargs)
    covered = covered_text(page, rect) if text is None else text
    annot.set_info(title=MARK_TITLE, subject=source, content=covered)
    annot.update(fill_color=fill, cross_out=False)
    return annot.xref


def _repeat(text: str, rect: pymupdf.Rect, size: float) -> str:
    width = pymupdf.get_text_length(text + " ", fontname="helv", fontsize=size) or 1
    n = max(1, int(rect.width // width))
    return " ".join([text] * n)


def add_marks(pdf: pymupdf.Document, items: Iterable[tuple[int, pymupdf.Rect, str]], style: MarkStyle,
              source: str) -> int:
    """Aggiunge più segni: `items` = (pagina, rettangolo, testo coperto)."""
    n = 0
    for page_index, rect, text in items:
        if add_mark(pdf, page_index, rect, style, source, text) is not None:
            n += 1
    return n


def mark_whole_pages(pdf: pymupdf.Document, pages: Iterable[int], style: MarkStyle) -> int:
    n = 0
    for i in pages:
        page = pdf[i]
        if add_mark(pdf, i, page_box(page), style, WHOLE_PAGE, _("Pagina intera")) is not None:
            n += 1
    return n


def marks(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> list[Mark]:
    """Segni di redazione presenti (anche quelli creati da altri programmi)."""
    out: list[Mark] = []
    indices = range(pdf.page_count) if pages is None else pages
    for i in indices:
        page = pdf[i]
        for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]):
            info = annot.info or {}
            out.append(Mark(i, annot.xref, pymupdf.Rect(annot.rect), info.get("content", ""),
                            info.get("subject", "") or "area"))
    return out


def has_marks(pdf: pymupdf.Document) -> bool:
    for page in pdf:
        if page.first_annot is not None and any(True for _a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])):
            return True
    return False


def remove_mark(pdf: pymupdf.Document, page_index: int, xref: int) -> bool:
    page = pdf[page_index]
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]):
        if annot.xref == xref:
            page.delete_annot(annot)
            return True
    return False


def clear_marks(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> int:
    n = 0
    for i in (range(pdf.page_count) if pages is None else pages):
        page = pdf[i]
        for xref in [a.xref for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])]:
            if remove_mark(pdf, i, xref):
                n += 1
    return n


_IMAGES = {IMAGES_PIXELS: pymupdf.PDF_REDACT_IMAGE_PIXELS, IMAGES_REMOVE: pymupdf.PDF_REDACT_IMAGE_REMOVE}
_GRAPHICS = {
    GRAPHICS_COVERED: pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
    GRAPHICS_TOUCHED: pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
    GRAPHICS_NONE: pymupdf.PDF_REDACT_LINE_ART_NONE,
}


@dataclass
class ApplyReport:
    pages: int = 0
    marks: int = 0
    annotations_removed: int = 0


def apply(pdf: pymupdf.Document, options: ApplyOptions | None = None, pages: Iterable[int] | None = None,
          progress: Callable[[float, str], None] | None = None) -> ApplyReport:
    """Applica i segni: rimuove il contenuto sotto i riquadri in modo definitivo."""
    options = options or ApplyOptions()
    report = ApplyReport()
    indices = list(range(pdf.page_count) if pages is None else pages)
    for n, i in enumerate(indices):
        if progress is not None:
            progress(n / max(1, len(indices)), _("Pagina {n} di {t}").format(n=n + 1, t=len(indices)))
        page = pdf[i]
        redacts = list(page.annots(types=[pymupdf.PDF_ANNOT_REDACT]))
        if not redacts:
            continue
        rects = [pymupdf.Rect(a.rect) for a in redacts]
        whole = any((a.info or {}).get("subject") == WHOLE_PAGE for a in redacts)
        report.pages += 1
        report.marks += len(redacts)
        if options.remove_annotations or whole:
            report.annotations_removed += _remove_touching(page, rects, whole)
        images = pymupdf.PDF_REDACT_IMAGE_REMOVE if whole else _IMAGES.get(options.images,
                                                                            pymupdf.PDF_REDACT_IMAGE_PIXELS)
        graphics = (pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED if whole
                    else _GRAPHICS.get(options.graphics, pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED))
        page.apply_redactions(images=images, graphics=graphics, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return report


def _remove_touching(page: pymupdf.Page, rects: list[pymupdf.Rect], whole: bool) -> int:
    """Rimuove annotazioni, campi e collegamenti che toccano le aree (tutti se `whole`)."""
    def touches(r) -> bool:
        r = pymupdf.Rect(r)
        return whole or any(r.intersects(x) for x in rects)

    n = 0
    widget_xrefs = [w.xref for w in page.widgets() if touches(w.rect)]
    for xref in widget_xrefs:
        for w in page.widgets():
            if w.xref == xref:
                page.delete_widget(w)
                n += 1
                break
    annot_xrefs = [a.xref for a in page.annots()
                   if a.type[0] not in (pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_WIDGET,
                                        pymupdf.PDF_ANNOT_LINK) and touches(a.rect)]
    for xref in annot_xrefs:
        annot = page.load_annot(xref)
        if annot is not None:
            page.delete_annot(annot)
            n += 1
    for link in list(page.get_links()):
        if touches(link.get("from", (0, 0, 0, 0))):
            page.delete_link(link)
            n += 1
    return n


def words_under_marks(pdf: pymupdf.Document, page_index: int) -> list[pymupdf.Rect]:
    """Parole che l'applicazione rimuoverà sulla pagina (per l'anteprima)."""
    page = pdf[page_index]
    rects = [pymupdf.Rect(a.rect) for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])]
    if not rects:
        return []
    out = []
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4])
        if any(r.intersects(x) for x in rects):
            out.append(r)
    return out
