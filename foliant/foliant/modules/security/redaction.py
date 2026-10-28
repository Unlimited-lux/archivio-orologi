"""Redazione (oscuramento definitivo): segni di anteprima e applicazione.

Logica pura. I segni sono annotazioni di redazione del PDF: si vedono come
riquadri contornati, si possono annullare, salvare e rivedere; l'applicazione
rimuove davvero testo, immagini e grafica sotto i riquadri e li sostituisce con
un riquadro pieno (con testo sovrapposto facoltativo).
"""

from __future__ import annotations

import re
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
    graphics: str = GRAPHICS_TOUCHED   # nessun punto di un tracciato resta nell'area (SEC-20)
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
    from foliant.core import placement
    annot = placement.add_redact_annot(page, rect, **kwargs)
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


def iter_pages(pdf: pymupdf.Document, pages: Iterable[int] | None = None):
    """(indice, pagina) delle pagine caricabili: una pagina danneggiata (es. albero
    delle pagine ciclico) viene saltata invece di interrompere l'operazione."""
    for i in (range(pdf.page_count) if pages is None else pages):
        try:
            page = pdf[i]
        except Exception:
            continue
        yield i, page


def _redact_xrefs(page: pymupdf.Page) -> list[int]:
    # annot_xrefs è lineare; page.annots() costa in modo quadratico con molte annotazioni
    try:
        return [x[0] for x in page.annot_xrefs() if x[1] == pymupdf.PDF_ANNOT_REDACT]
    except Exception:
        return []


def marks(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> list[Mark]:
    """Segni di redazione presenti (anche quelli creati da altri programmi)."""
    out: list[Mark] = []
    index = RedactIndex().pages(pdf)       # niente caricamento delle pagine senza segni
    wanted = sorted(index) if pages is None else [i for i in pages if i in index]
    for i, page in iter_pages(pdf, wanted):
        for xref in index[i]:
            annot = page.load_annot(xref)
            if annot is None:
                continue
            info = annot.info or {}
            out.append(Mark(i, annot.xref, pymupdf.Rect(annot.rect), info.get("content", ""),
                            info.get("subject", "") or "area"))
    return out


def has_marks(pdf: pymupdf.Document) -> bool:
    return RedactIndex().any(pdf)


_RX_REF = re.compile(r"(\d+)\s+0\s+R")


class RedactIndex:
    """Pagine con segni di redazione, senza caricare le pagine né le annotazioni.

    Legge solo l'array /Annots di ogni pagina; il tipo di ogni annotazione (che non
    cambia per uno stesso oggetto) resta in cache, così dopo una modifica si
    controllano solo le annotazioni nuove. `clear()` dopo le modifiche del modulo
    (gli oggetti eliminati possono essere riusati)."""

    def __init__(self) -> None:
        self._redact: dict[int, bool] = {}

    def clear(self) -> None:
        self._redact.clear()

    def _is_redact(self, pdf: pymupdf.Document, xref: int) -> bool:
        known = self._redact.get(xref)
        if known is None:
            try:
                known = pdf.xref_get_key(xref, "Subtype") == ("name", "/Redact")
            except Exception:
                known = False
            self._redact[xref] = known
        return known

    def pages(self, pdf: pymupdf.Document) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(pdf.page_count):
            try:
                kind, value = pdf.xref_get_key(pdf.page_xref(i), "Annots")
                if kind == "xref":
                    value = pdf.xref_object(int(value.split()[0]), compressed=True)
                elif kind != "array":
                    continue
            except Exception:
                continue
            refs = [int(m.group(1)) for m in _RX_REF.finditer(value)]
            hits = [x for x in refs if self._is_redact(pdf, x)]
            if hits:
                out[i] = hits
        return out

    def any(self, pdf: pymupdf.Document) -> bool:
        return bool(self.pages(pdf))


def remove_mark(pdf: pymupdf.Document, page_index: int, xref: int) -> bool:
    page = pdf[page_index]
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]):
        if annot.xref == xref:
            page.delete_annot(annot)
            return True
    return False


def clear_marks(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> int:
    n = 0
    for i, page in iter_pages(pdf, pages):
        for xref in _redact_xrefs(page):
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
        try:
            page = pdf[i]
        except Exception:
            continue
        redacts = [a for a in (page.load_annot(x) for x in _redact_xrefs(page)) if a is not None]
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
                    else _GRAPHICS.get(options.graphics, pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED))
        fills = [(pymupdf.Rect(a.rect), tuple((a.colors or {}).get("fill") or (0, 0, 0))) for a in redacts]
        # Il motore disegna riempimento e testo sovrapposto nel posto sbagliato sulle pagine
        # con /CropBox spostato: li togliamo dai segni e li disegniamo noi (core/placement).
        boxes = [_box_of(pdf, a) for a in redacts]
        for a in redacts:
            pdf.xref_set_key(a.xref, "IC", "null")
            pdf.xref_set_key(a.xref, "OverlayText", "null")
        runs = _removed_runs(page, [b[0] for b in boxes])
        page.apply_redactions(images=images, graphics=graphics, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if runs:
            _fix_text_advance(pdf, page, runs)
        _draw_boxes(page, boxes)
        if images == pymupdf.PDF_REDACT_IMAGE_PIXELS:
            _paint_image_pixels(pdf, page, fills)
        # la miniatura incorporata mostrerebbe ancora il contenuto rimosso
        if pdf.xref_get_key(page.xref, "Thumb")[0] != "null":
            pdf.xref_set_key(page.xref, "Thumb", "null")
    return report


def _removed_runs(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> list[tuple[float, float]]:
    """Sequenze di glifi rimossi seguite da testo che resta sulla stessa riga.

    Per ciascuna: (avanzamento totale, avanzamento del primo glifo) in millesimi di em."""
    runs: list[tuple[float, float]] = []
    try:
        raw = page.get_text("rawdict", flags=0)
    except Exception:
        return runs
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            if tuple(round(v, 3) for v in line.get("dir", (1, 0))) != (1.0, 0.0):
                continue
            chars = [(ch, float(span.get("size") or 0)) for span in line.get("spans", [])
                     for ch in span.get("chars", [])]
            run: list[tuple[float, float]] = []
            for ch, size in chars:
                if size <= 0:
                    run = []
                    continue
                box = pymupdf.Rect(ch["bbox"])
                adv = box.width / size * 1000
                if any(box.intersects(r) for r in rects):
                    run.append((adv, size))
                elif run:
                    runs.append((sum(a for a, _s in run), run[0][0]))
                    run = []
    return runs


_RX_TJ_NUMBER = re.compile(rb"(?<=[)>])\s*(-?\d+(?:\.\d+)?)\s*(?=[(<])")


def _fix_text_advance(pdf: pymupdf.Document, page: pymupdf.Page, runs: list[tuple[float, float]]) -> None:
    """Il motore, togliendo i glifi, compensa l'avanzamento senza il primo glifo rimosso:
    il testo che segue sulla riga si sposta a sinistra (bug_06). Correggiamo il numero
    inserito nell'array TJ quando corrisponde esattamente a questo errore."""
    pending = list(runs)
    for xref in page.get_contents():
        data = pdf.xref_stream(xref)
        out, last, changed = bytearray(), 0, False
        for m in _RX_TJ_NUMBER.finditer(data):
            value = float(m.group(1))
            for k, (total, first) in enumerate(pending):
                if abs(value + (total - first)) < 0.6:
                    out += data[last:m.start(1)] + f"{-total:.3f}".encode()
                    last = m.end(1)
                    pending.pop(k)
                    changed = True
                    break
        if changed:
            out += data[last:]
            pdf.update_stream(xref, bytes(out))
        if not pending:
            break


def _box_of(pdf: pymupdf.Document, annot) -> tuple:
    import re
    rect = pymupdf.Rect(annot.rect)
    fill = tuple((annot.colors or {}).get("fill") or (0, 0, 0))
    text = ""
    kind, val = pdf.xref_get_key(annot.xref, "OverlayText")
    if kind == "string":
        text = val
    size, color = 11.0, contrast_color(fill)
    kind, da = pdf.xref_get_key(annot.xref, "DA")
    if kind == "string":
        m = re.search(r"([\d.]+)\s+Tf", da)
        if m:
            size = float(m.group(1))
        m = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da)
        if m:
            color = tuple(float(x) for x in m.groups())
    return rect, fill, text, size, color


def _draw_boxes(page: pymupdf.Page, boxes) -> None:
    from foliant.core import placement
    for rect, fill, text, size, color in boxes:
        placement.draw_rect(page, rect, color=None, fill=fill, width=0, overlay=True)
        if text and rect.height >= 6:
            fs = size
            while fs >= 4:
                rc = placement.insert_textbox(page, rect, text, fontsize=fs, fontname="helv", color=color,
                                              align=pymupdf.TEXT_ALIGN_CENTER)
                rc = rc[0] if isinstance(rc, tuple) else rc
                if rc >= 0:
                    break
                fs -= 1


def _paint_image_pixels(pdf: pymupdf.Document, page: pymupdf.Page, fills) -> None:
    """Il motore cancella i pixel coperti in bianco: li porta al colore di riempimento,
    così l'immagine estratta dal file mostra il riquadro scelto (SEC-20)."""
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref") or 0
        if not xref:
            continue
        bbox = pymupdf.Rect(info["bbox"])
        hits = [(r & bbox, c) for r, c in fills if r.intersects(bbox)]
        if not hits or bbox.is_empty:
            continue
        try:
            pix = pymupdf.Pixmap(pdf, xref)
            if pix.alpha or pix.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            elif pix.n == 1:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        except Exception:
            continue
        # trasformazione pagina → pixel (immagini non ruotate né inclinate)
        t = pymupdf.Matrix(info.get("transform") or (1, 0, 0, 1, 0, 0))
        if abs(t.b) > 1e-6 or abs(t.c) > 1e-6:
            continue
        sx, sy = pix.width / bbox.width, pix.height / bbox.height
        flip_y = t.d < 0
        for r, color in hits:
            x0 = int((r.x0 - bbox.x0) * sx)
            x1 = int(round((r.x1 - bbox.x0) * sx))
            if flip_y:
                y0 = int((bbox.y1 - r.y1) * sy)
                y1 = int(round((bbox.y1 - r.y0) * sy))
            else:
                y0 = int((r.y0 - bbox.y0) * sy)
                y1 = int(round((r.y1 - bbox.y0) * sy))
            irect = pymupdf.IRect(x0, y0, x1, y1) & pix.irect
            if not irect.is_empty:
                pix.set_rect(irect, tuple(int(round(c * 255)) for c in color[:3]))
        try:
            page.replace_image(xref, pixmap=pix)
        except Exception:
            continue


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


# ------------------------------------------------------------------ XFDF


def export_xfdf(pdf: pymupdf.Document, filename: str = "") -> str:
    """Segni di redazione in XFDF (ISO 19444-1) per la revisione da parte di altri."""
    from xml.sax.saxutils import quoteattr

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">', "<annots>"]
    for m in marks(pdf):
        page = pdf[m.page]
        h = page.cropbox.height
        # XFDF usa coordinate PDF (origine in basso a sinistra)
        rect = f"{m.rect.x0:.2f},{h - m.rect.y1:.2f},{m.rect.x1:.2f},{h - m.rect.y0:.2f}"
        annot = page.load_annot(m.xref)
        fill = (annot.colors or {}).get("fill") or (0, 0, 0)
        color = "#%02X%02X%02X" % tuple(int(round(c * 255)) for c in fill[:3])
        overlay = ""
        kind, val = pdf.xref_get_key(m.xref, "OverlayText")
        if kind == "string":
            overlay = val
        attrs = (f"page={quoteattr(str(m.page))} rect={quoteattr(rect)} interior-color={quoteattr(color)} "
                 f"title={quoteattr(MARK_TITLE)} subject={quoteattr(m.source)} "
                 f"overlay-text={quoteattr(overlay)}")
        lines.append(f"<redact {attrs}><contents>{_esc(m.text)}</contents></redact>")
    lines += ["</annots>", f'<f href={quoteattr(filename)}/>' if filename else "", "</xfdf>"]
    return "\n".join(line for line in lines if line)


def _esc(text: str) -> str:
    from xml.sax.saxutils import escape
    return escape(text or "")


def import_xfdf(pdf: pymupdf.Document, data: str, style: MarkStyle | None = None) -> int:
    """Ricrea i segni di redazione da un file XFDF. Solleva ValueError se non valido."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(_("Il file XFDF non è valido: {e}").format(e=e)) from e
    n = 0
    for el in root.iter():
        if not el.tag.endswith("redact"):
            continue
        try:
            page_index = int(el.get("page", "0"))
            x0, y0, x1, y1 = (float(v) for v in el.get("rect", "").split(","))
        except ValueError:
            continue
        if not 0 <= page_index < pdf.page_count:
            continue
        h = pdf[page_index].cropbox.height
        rect = pymupdf.Rect(x0, h - y1, x1, h - y0)
        st = MarkStyle(**{**(style or MarkStyle()).__dict__})
        color = el.get("interior-color", "")
        if len(color) == 7 and color.startswith("#"):
            st.fill = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
            st.text_color = contrast_color(st.fill)
        st.overlay_text = el.get("overlay-text", st.overlay_text)
        contents = "".join(c.text or "" for c in el if c.tag.endswith("contents"))
        if add_mark(pdf, page_index, rect, st, el.get("subject", "area") or "area", contents) is not None:
            n += 1
    return n
