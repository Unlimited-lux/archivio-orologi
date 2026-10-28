"""Inserimento dei risultati nel documento (note, caselle, pagine, sostituzione del testo).

Ogni funzione esegue un solo `document.change()`: un «Annulla» la rimuove.
"""

from __future__ import annotations

import html as _html
import re

import pymupdf

from foliant.api import ENGINE_LOCK, Document, FoliantError, _

from .render import plain_text

SUBJECT = "Assistente IA"

BASE14 = {
    "helvetica": "helv", "helvetica-bold": "hebo", "helvetica-oblique": "heit", "helvetica-boldoblique": "hebi",
    "times-roman": "tiro", "times-bold": "tibo", "times-italic": "tiit", "times-bolditalic": "tibi",
    "courier": "cour", "courier-bold": "cobo", "courier-oblique": "coit", "courier-boldoblique": "cobi",
    "symbol": "symb", "zapfdingbats": "zadb",
}


def _author(author: str | None) -> str:
    return author or _("Assistente IA")


def add_note(document: Document, page: int, point: tuple[float, float], text: str, *, subject: str = SUBJECT,
             author: str | None = None, label: str | None = None) -> None:
    with document.change(label or _("Inserisci nota IA")) as pdf:
        p = pdf[page]
        x = min(max(point[0], 0), p.rect.width - 20)
        y = min(max(point[1], 0), p.rect.height - 20)
        annot = p.add_text_annot((x, y), text, icon="Comment")
        annot.set_info(title=_author(author), subject=subject, content=text)
        annot.update()


def add_text_box(document: Document, page: int, rect: tuple | None, text: str, *, author: str | None = None,
                 fontsize: float = 10, label: str | None = None) -> pymupdf.Rect:
    """Casella di testo (annotazione testo libero) con il testo indicato."""
    with document.change(label or _("Inserisci casella di testo IA")) as pdf:
        p = pdf[page]
        if rect is None:
            r = pymupdf.Rect(p.rect.width * 0.1, p.rect.height * 0.1, p.rect.width * 0.9, p.rect.height * 0.5)
        else:
            r = pymupdf.Rect(rect)
            if r.width < 20 or r.height < 20:
                r = pymupdf.Rect(r.x0, r.y0, min(r.x0 + 260, p.rect.width - 10), r.y0 + 120)
        # altezza sufficiente per il testo
        lines = sum(max(1, int(len(line) * fontsize * 0.5 / max(r.width - 8, 20)) + 1) for line in text.split("\n"))
        need = lines * fontsize * 1.3 + 10
        if r.height < need:
            r.y1 = min(p.rect.height - 5, r.y0 + need)
        annot = p.add_freetext_annot(r, text, fontsize=fontsize, fontname="helv", text_color=(0, 0, 0),
                                     fill_color=(1, 1, 0.85))
        annot.set_info(title=_author(author), subject=SUBJECT)
        annot.update()
        return r


def insert_text_page(document: Document, text: str, *, at: int = 0, like_page: int = 0,
                     label: str | None = None, title: str = "") -> int:
    """Nuova pagina con testo selezionabile e carattere incorporato (riassunto, AI-05)."""
    body = plain_text(text)
    with document.change(label or _("Inserisci riassunto come pagina")) as pdf:
        like = pdf[min(max(like_page, 0), pdf.page_count - 1)].rect
        at = max(0, min(at, pdf.page_count))
        page = pdf.new_page(at, width=like.width, height=like.height)
        rect = pymupdf.Rect(48, 48, like.width - 48, like.height - 48)
        parts = []
        if title:
            parts.append(f"<h2 style='font-family:sans-serif'>{_html.escape(title)}</h2>")
        for para in re.split(r"\n\s*\n", body):
            parts.append("<p style='font-family:sans-serif;font-size:11pt'>" +
                         "<br>".join(_html.escape(line) for line in para.split("\n")) + "</p>")
        spare, scale = page.insert_htmlbox(rect, "".join(parts), scale_low=0)
        if spare < 0:
            raise FoliantError(_("Il testo non entra nella pagina."))
        return at


# ------------------------------------------------------------------ sostituzione testo
def is_signed(pdf: pymupdf.Document) -> bool:
    with ENGINE_LOCK:
        try:
            return pdf.get_sigflags() > 0
        except Exception:  # noqa: BLE001
            return False


def _font_key(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name or "").lower()


def _dominant_span(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> tuple[dict | None, list[dict]]:
    spans = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                r = pymupdf.Rect(span["bbox"])
                if any((r & s).get_area() > 0.5 * max(min(r.get_area(), s.get_area()), 1e-6) for s in rects) \
                        and span["text"].strip():
                    spans.append(span)
    if not spans:
        return None, []
    weights: dict = {}
    for s in spans:
        key = (s["font"], round(s["size"], 1), s["color"], s["flags"])
        weights[key] = weights.get(key, 0) + len(s["text"])
    best = max(weights, key=weights.get)
    dom = next(s for s in spans if (s["font"], round(s["size"], 1), s["color"], s["flags"]) == best)
    return dom, spans


def replace_check(pdf: pymupdf.Document, page: int, rects: list) -> tuple[bool, str]:
    """Si può sostituire il testo nel documento? (motivo se no)."""
    from . import content
    with ENGINE_LOCK:
        if pdf.is_encrypted or (pdf.metadata or {}).get("encryption"):
            if not pdf.permissions & pymupdf.PDF_PERM_MODIFY:
                return False, _("Il documento non consente modifiche.")
        p = pdf[page]
        rr = [pymupdf.Rect(r) for r in rects]
        dom, spans = _dominant_span(p, rr)
        if dom is None:
            return False, _("Nella selezione non c'è testo modificabile.")
        has_images = bool(p.get_images())
        invisible = 0
        for span in p.get_texttrace():
            r = pymupdf.Rect(span["bbox"])
            if span.get("type") == 3 and any((r & s).get_area() > 0 for s in rr):
                invisible += 1
    if invisible and has_images:
        return False, _("La pagina è una scansione: il testo riconosciuto non si può sostituire.")
    if content.textless_pages(pdf, [page]):
        return False, _("La pagina non contiene testo modificabile.")
    return True, ""


def _font_for(pdf: pymupdf.Document, page: pymupdf.Page, span: dict, text: str) -> tuple[str, bytes | None, bool]:
    """(nome font per l'inserimento, buffer o None, stesso font dell'originale)."""
    key = _font_key(span["font"])
    if key in BASE14:
        return BASE14[key], None, True
    for f in page.get_fonts(full=True):
        xref, basefont = f[0], f[3]
        if _font_key(basefont) == key:
            try:
                name, ext, _type, buf = pdf.extract_font(xref)
            except Exception:  # noqa: BLE001
                buf = b""
            if buf:
                try:
                    font = pymupdf.Font(fontbuffer=buf)
                    if all(font.has_glyph(ord(c)) for c in text if not c.isspace()):
                        return "FAI" + str(xref), buf, True
                except Exception:  # noqa: BLE001
                    pass
    bold = span["flags"] & 16
    italic = span["flags"] & 2
    serif = span["flags"] & 4
    if serif:
        return ("tibi" if bold and italic else "tibo" if bold else "tiit" if italic else "tiro"), None, False
    return ("hebi" if bold and italic else "hebo" if bold else "heit" if italic else "helv"), None, False


def replace_text(document: Document, page: int, rects: list, new_text: str, *,
                 label: str | None = None) -> list[str]:
    """Sostituisce il testo selezionato mantenendo carattere, corpo e colore (AI-09).

    Restituisce gli avvisi (stile misto, carattere sostitutivo, corpo ridotto).
    """
    warnings: list[str] = []
    rr = [pymupdf.Rect(r) for r in rects]
    with document.change(label or _("Sostituisci testo (IA)")) as pdf:
        p = pdf[page]
        dom, spans = _dominant_span(p, rr)
        if dom is None:
            raise FoliantError(_("Nella selezione non c'è testo modificabile."))
        if len({(s["font"], round(s["size"], 1), s["color"]) for s in spans}) > 1:
            warnings.append(_("Formattazione mista: è stato mantenuto lo stile prevalente."))
        area = pymupdf.Rect(rr[0])
        for r in rr[1:]:
            area |= r
        all_words = p.get_text("words")
        fontname, buf, same = _font_for(pdf, p, dom, new_text)
        if not same:
            warnings.append(_("Il carattere originale non contiene tutti i glifi: è stato usato un carattere simile."))
        size = dom["size"]
        color = pymupdf.sRGB_to_pdf(dom["color"])
        for r in rr:
            p.add_redact_annot(r)
        p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                           text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if buf:
            p.insert_font(fontname=fontname, fontbuffer=buf)
        # una sola riga d'origine: si riscrive sulla stessa linea di base se c'è spazio a destra
        single = len({round(r.y1) for r in rr}) == 1 and "\n" not in new_text
        if single:
            right = p.rect.width - 36
            for w in all_words:
                wr = pymupdf.Rect(w[:4])
                c = (wr.x0 + wr.x1) / 2
                if c > area.x1 and wr.y1 > area.y0 and wr.y0 < area.y1:
                    right = min(right, wr.x0 - 2)
            try:
                font = pymupdf.Font(fontbuffer=buf) if buf else pymupdf.Font(fontname)
                width = font.text_length(new_text, fontsize=size)
            except Exception:  # noqa: BLE001
                width = None
            baseline = spans[0]["origin"][1]
            if width is not None and area.x0 + width <= max(right, area.x1):
                origin = (area.x0, baseline)
                p.insert_text(origin, new_text, fontname=fontname, fontsize=size, color=color)
                return warnings
        box = pymupdf.Rect(area.x0, area.y0, max(area.x1, area.x0 + 20), area.y1)
        # il riquadro si allarga in basso, poi il corpo si riduce
        fs = size
        rc = -1.0
        for grow in (0, 1, 2, 4, 8):
            b = pymupdf.Rect(box.x0, box.y0, box.x1, min(p.rect.height, box.y1 + grow * size * 1.2) + size * 0.4)
            rc = _try_textbox(p, b, new_text, fontname, fs, color)
            if rc >= 0:
                if grow:
                    warnings.append(_("Il testo proposto è più lungo: il riquadro è stato allargato."))
                break
        if rc < 0:
            b = pymupdf.Rect(box.x0, box.y0, box.x1, min(p.rect.height, box.y1 + 8 * size * 1.2))
            while fs > 4 and rc < 0:
                fs -= 0.5
                rc = _try_textbox(p, b, new_text, fontname, fs, color)
            warnings.append(_("Il testo proposto è più lungo: il corpo è stato ridotto a {s} pt.").format(s=f"{fs:g}"))
            if rc < 0:
                raise FoliantError(_("Il testo proposto non entra nello spazio disponibile."))
        p.insert_textbox(b, new_text, fontname=fontname, fontsize=fs, color=color)
    return warnings


def _try_textbox(page: pymupdf.Page, rect: pymupdf.Rect, text: str, fontname: str, fs: float, color) -> float:
    """Misura senza scrivere: insert_textbox su una pagina temporanea."""
    tmp = pymupdf.open()
    tp = tmp.new_page(width=page.rect.width, height=page.rect.height)
    if fontname.startswith("FAI"):
        # stesso buffer del documento
        doc = page.parent
        xref = int(fontname[3:])
        try:
            _n, _e, _t, buf = doc.extract_font(xref)
            tp.insert_font(fontname=fontname, fontbuffer=buf)
        except Exception:  # noqa: BLE001
            fontname = "helv"
    rc = tp.insert_textbox(rect, text, fontname=fontname, fontsize=fs, color=color)
    tmp.close()
    return rc


def add_replacement_annotation(document: Document, page: int, rects: list, proposed: str, *,
                               author: str | None = None) -> None:
    """Alternativa non distruttiva: barrato con il testo proposto come contenuto."""
    with document.change(_("Annotazione di sostituzione (IA)")) as pdf:
        p = pdf[page]
        quads = [pymupdf.Rect(r).quad for r in rects]
        annot = p.add_strikeout_annot(quads=quads)
        annot.set_info(title=_author(author), subject=_("Sostituzione testo"), content=proposed)
        annot.update()


def add_ocr_layer(document: Document, words_by_page: dict[int, list[tuple]]) -> int:
    """Livello di testo invisibile sopra le scansioni (rende il PDF ricercabile). Un solo «Annulla»."""
    n = 0
    with document.change(_("Salva il testo riconosciuto")) as pdf:
        font = pymupdf.Font("helv")
        for index, words in words_by_page.items():
            page = pdf[index]
            for x0, y0, x1, y1, word in words:
                h = max(1.0, y1 - y0)
                fs = h * 0.85
                width = font.text_length(word, fontsize=fs) or 1.0
                # larghezza adattata al riquadro riconosciuto
                morph = (pymupdf.Point(x0, y1), pymupdf.Matrix((x1 - x0) / width, 1)) if width else None
                page.insert_text((x0, y1 - h * 0.2), word, fontsize=fs, fontname="helv", render_mode=3,
                                 morph=morph)
                n += 1
    return n
