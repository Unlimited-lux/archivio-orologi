"""Appiattimento (CNV-33), senza interfaccia: annotazioni, campi modulo, livelli, ritaglio, rasterizzazione.

    data, report = flatten_pdf_bytes(data, annotations=True, forms=True, layers=True, crop=True)

Le funzioni lavorano su byte e restituiscono byte: l'interfaccia applica il
risultato con `document.replace_with()` (annullabile finché non si salva).
"""

from __future__ import annotations

import re

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .common import ConversionError, check, parse_page_range

# tipi di annotazione «di markup» appiattibili (i link restano attivi per impostazione)
MARKUP_TYPES = {
    pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT, pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_SQUARE,
    pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE, pymupdf.PDF_ANNOT_HIGHLIGHT,
    pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_SQUIGGLY, pymupdf.PDF_ANNOT_STRIKE_OUT, pymupdf.PDF_ANNOT_STAMP,
    pymupdf.PDF_ANNOT_CARET, pymupdf.PDF_ANNOT_INK, pymupdf.PDF_ANNOT_FILE_ATTACHMENT, pymupdf.PDF_ANNOT_REDACT,
}


# ------------------------------------------------------------------ annotazioni
def _save_links(pdf: pymupdf.Document) -> list[list[dict]]:
    return [[{k: v for k, v in link.items() if k != "xref"} for link in page.get_links()] for page in pdf]


def _restore_links(pdf: pymupdf.Document, links: list[list[dict]]) -> None:
    for page, page_links in zip(pdf, links):
        existing = {(tuple(l["from"]), l.get("uri"), l.get("page")) for l in page.get_links()}
        for link in page_links:
            key = (tuple(link["from"]), link.get("uri"), link.get("page"))
            if key in existing:
                continue
            try:
                page.insert_link(link)
            except Exception:
                pass


def _overlay_flatten(pdf: pymupdf.Document, pages: list[int], keep) -> None:
    """Appiattisce solo le annotazioni scelte: la pagina «cotta» (senza le altre
    annotazioni) diventa il nuovo contenuto, le annotazioni restanti rimangono."""
    baked = pymupdf.open(stream=pdf.tobytes(), filetype="pdf")
    for i in pages:
        page = baked[i]
        for annot in list(page.annots()):
            if keep(annot):
                page.delete_annot(annot)
        for link in page.get_links():
            page.delete_link(link)
    baked.bake(annots=True, widgets=False)
    for i in pages:
        page = pdf[i]
        if not any(not keep(a) for a in page.annots()):
            continue
        for annot in list(page.annots()):
            if not keep(annot):
                page.delete_annot(annot)
        rot = page.rotation
        page.set_rotation(0)
        baked[i].set_rotation(0)
        for xref in page.get_contents():
            pdf.update_stream(xref, b"")
        page.show_pdf_page(page.rect, baked, i)
        page.set_rotation(rot)
    baked.close()


def flatten_annotations(pdf: pymupdf.Document, pages: list[int], *, types: set[int] | None = None,
                        include_links: bool = False, forms: bool = True, report: dict) -> None:
    lost_notes = 0
    for i in pages:
        for annot in pdf[i].annots():
            if annot.type[0] == pymupdf.PDF_ANNOT_TEXT and (annot.info.get("content") or "").strip():
                lost_notes += 1
    if lost_notes:
        report["warnings"].append(_("Il testo di {n} note è andato perso: resta solo l'icona.").format(n=lost_notes))
    signed = any(w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.field_value
                 for i in pages for w in (pdf[i].widgets() or []))
    if forms and signed:
        report["warnings"].append(_("Le firme appiattite non sono più verificabili."))
    all_pages = pages == list(range(pdf.page_count))
    if types is None and all_pages:
        links = None if include_links else _save_links(pdf)
        count = sum(len(list(pdf[i].annots())) for i in pages)
        pdf.bake(annots=True, widgets=forms)
        if links is not None:
            _restore_links(pdf, links)
        report["annotations"] = count
    else:
        wanted = MARKUP_TYPES if types is None else types

        def keep(annot) -> bool:
            return annot.type[0] not in wanted

        report["annotations"] = sum(1 for i in pages for a in pdf[i].annots() if not keep(a))
        _overlay_flatten(pdf, pages, keep)
        if forms:
            _flatten_widgets_overlay(pdf, pages)
    if forms:
        _drop_empty_acroform(pdf)


def _flatten_widgets_overlay(pdf: pymupdf.Document, pages: list[int]) -> None:
    if not any(list(pdf[i].widgets()) for i in pages):
        return
    baked = pymupdf.open(stream=pdf.tobytes(), filetype="pdf")
    for i in range(baked.page_count):
        for annot in list(baked[i].annots()):
            baked[i].delete_annot(annot)
        for link in baked[i].get_links():
            baked[i].delete_link(link)
    baked.bake(annots=False, widgets=True)
    for i in pages:
        page = pdf[i]
        widgets = list(page.widgets())
        if not widgets:
            continue
        for w in widgets:
            page.delete_widget(w)
        rot = page.rotation
        page.set_rotation(0)
        baked[i].set_rotation(0)
        for xref in page.get_contents():
            pdf.update_stream(xref, b"")
        page.show_pdf_page(page.rect, baked, i)
        page.set_rotation(rot)
    baked.close()


def _drop_empty_acroform(pdf: pymupdf.Document) -> None:
    cat = pdf.pdf_catalog()
    kind, val = pdf.xref_get_key(cat, "AcroForm")
    if kind == "null":
        return
    has_fields = any(True for p in pdf for _w in p.widgets())
    if not has_fields:
        pdf.xref_set_key(cat, "AcroForm", "null")


# --------------------------------------------------------------------- livelli
_TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|<<|>>|<[0-9A-Fa-f\s]*>|/[^\s/<>\[\]()%{}]*|\[|\]|%[^\r\n]*|[^\s/<>\[\]()%]+")


def _tokens(data: bytes):
    """Tokenizzatore minimo dei flussi di contenuto (stringhe con parentesi annidate gestite a parte)."""
    i, n = 0, len(data)
    while i < n:
        c = data[i:i + 1]
        if c.isspace():
            i += 1
            continue
        if c == b"(":
            depth, j = 0, i
            while j < n:
                ch = data[j:j + 1]
                if ch == b"\\":
                    j += 2
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            yield i, j + 1, data[i:j + 1]
            i = j + 1
            continue
        if data.startswith(b"BI", i) and (i + 2 >= n or data[i + 2:i + 3].isspace()):
            end = data.find(b"EI", i + 2)
            while end != -1 and not (end + 2 >= n or data[end + 2:end + 3].isspace()):
                end = data.find(b"EI", end + 2)
            end = n if end == -1 else end + 2
            yield i, end, data[i:end]
            i = end
            continue
        m = _TOKEN.match(data, i)
        if not m or m.end() == i:
            i += 1
            continue
        yield i, m.end(), m.group(0)
        i = m.end()


def strip_hidden_marked_content(data: bytes, hidden_names: set[bytes]) -> tuple[bytes, int]:
    """Rimuove i blocchi `/OC /Nome BDC … EMC` dei livelli nascosti; toglie i
    marcatori dei livelli visibili lasciandone il contenuto."""
    out = bytearray()
    stack: list[str] = []          # "hidden", "visible", "other"
    pending: list[bytes] = []      # operandi in attesa dell'operatore
    pending_start = 0
    removed = 0
    last = 0

    def hidden_depth() -> bool:
        return "hidden" in stack

    for start, end, tok in _tokens(data):
        is_op = not (tok[:1] in (b"/", b"(", b"<", b"[", b"]") or tok in (b"<<", b">>")
                     or re.fullmatch(rb"[-+]?(\d+\.?\d*|\.\d+)", tok) or tok in (b"true", b"false", b"null"))
        if tok.startswith(b"%"):
            continue
        if not is_op:
            if not pending:
                pending_start = start
            pending.append(tok)
            continue
        chunk = data[pending_start if pending else start:end]
        if tok == b"BDC":
            kind = "other"
            if len(pending) >= 2 and pending[0] == b"/OC" and pending[1].startswith(b"/"):
                kind = "hidden" if pending[1][1:] in hidden_names else "visible"
            if not hidden_depth() and kind == "other":
                out += chunk + b"\n"
            if kind == "hidden" and not hidden_depth():
                removed += 1
            stack.append(kind)
        elif tok == b"BMC":
            if not hidden_depth():
                out += chunk + b"\n"
            stack.append("other")
        elif tok == b"EMC":
            kind = stack.pop() if stack else "other"
            if not hidden_depth() and kind == "other":
                out += chunk + b"\n"
        else:
            if not hidden_depth():
                out += chunk + b"\n"
        pending = []
        last = end
    _ = last
    return bytes(out), removed


def flatten_layers(pdf: pymupdf.Document, pages: list[int], report: dict) -> None:
    ocgs = pdf.get_ocgs() or {}
    if not ocgs:
        return
    hidden_xrefs = {x for x, info in ocgs.items() if not info.get("on", True)}
    for i in pages:
        page = pdf[i]
        names: set[bytes] = set()
        kind, props = pdf.xref_get_key(page.xref, "Resources/Properties")
        if kind == "dict":
            for name, xref in re.findall(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R", props):
                if int(xref) in hidden_xrefs:
                    names.add(name.encode())
        elif kind == "xref":
            obj = pdf.xref_object(int(props.split()[0]))
            for name, xref in re.findall(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R", obj):
                if int(xref) in hidden_xrefs:
                    names.add(name.encode())
        content = page.read_contents()
        new, removed = strip_hidden_marked_content(content, names)
        xrefs = page.get_contents()
        if xrefs:
            pdf.update_stream(xrefs[0], new)
            for x in xrefs[1:]:
                pdf.update_stream(x, b"")
        report["layers_removed"] = report.get("layers_removed", 0) + removed
        # oggetti esterni (XObject) assegnati a un livello nascosto: rimossi dalle risorse
        for img in page.get_xobjects():
            xref = img[0]
            k, oc = pdf.xref_get_key(xref, "OC")
            if k == "xref" and int(oc.split()[0]) in hidden_xrefs:
                pdf.update_stream(xref, b"") if pdf.xref_get_key(xref, "Subtype")[1] == "/Form" else None
            if k != "null":
                pdf.xref_set_key(xref, "OC", "null")
    pdf.xref_set_key(pdf.pdf_catalog(), "OCProperties", "null")


# --------------------------------------------------------------------- ritaglio
def flatten_crop(pdf: pymupdf.Document, pages: list[int], report: dict) -> None:
    """MediaBox = CropBox ed eliminazione del contenuto esterno all'area visibile."""
    remove_art = getattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 0)
    count = 0
    for i in pages:
        page = pdf[i]
        rot = page.rotation
        page.set_rotation(0)
        visible = pymupdf.Rect(page.cropbox)          # coordinate di pagina (origine in alto a sinistra)
        media = pymupdf.Rect(page.mediabox)
        if visible.is_empty or (abs(visible.width - media.width) < 0.01 and abs(visible.height - media.height) < 0.01):
            page.set_rotation(rot)
            continue
        kind, val = pdf.xref_get_key(page.xref, "CropBox")
        if kind == "array":   # valori originali (coordinate PDF, origine in basso a sinistra)
            raw_crop = pymupdf.Rect([float(v) for v in val.strip("[]").split()])
        else:
            raw_crop = pymupdf.Rect(media.x0 + visible.x0, media.y0 + media.height - visible.y1,
                                    media.x0 + visible.x1, media.y0 + media.height - visible.y0)
        raw_crop.normalize()
        page.set_cropbox(page.mediabox)
        full = page.rect
        outside = [pymupdf.Rect(full.x0, full.y0, full.x1, visible.y0), pymupdf.Rect(full.x0, visible.y1, full.x1, full.y1),
                   pymupdf.Rect(full.x0, visible.y0, visible.x0, visible.y1),
                   pymupdf.Rect(visible.x1, visible.y0, full.x1, visible.y1)]
        for r in outside:
            r = r & full
            if not r.is_empty and r.width > 0.01 and r.height > 0.01:
                page.add_redact_annot(r, fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=remove_art, text=0)
        page.set_mediabox(raw_crop)
        page.set_rotation(rot)
        count += 1
    report["cropped_pages"] = count


# ------------------------------------------------------------------ rasterizza
def rasterize_pages(pdf: pymupdf.Document, pages: list[int], dpi: int, ocr: bool, language: str | None,
                    report: dict, progress=None) -> None:
    """Sostituisce il contenuto delle pagine con la loro immagine (sul posto: segnalibri e link restano)."""
    from .extract import ocr_available, ocr_languages
    use_ocr = ocr and ocr_available()
    if ocr and not use_ocr:
        report["warnings"].append(_("OCR non disponibile: il testo delle pagine rasterizzate non sarà ricercabile."))
    for k, i in enumerate(pages):
        check(progress, 0.5 + 0.45 * k / max(1, len(pages)), _("Rasterizzazione pagina {p}").format(p=i + 1))
        page = pdf[i]
        rot = page.rotation
        page.set_rotation(0)
        pix = page.get_pixmap(dpi=dpi, annots=True)
        src = None
        if use_ocr:
            src = pymupdf.open(stream=pix.pdfocr_tobytes(language=ocr_languages(language)), filetype="pdf")
        links = [l for l in page.get_links()]
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for w in list(page.widgets()):
            page.delete_widget(w)
        for xref in page.get_contents():
            pdf.update_stream(xref, b"")
        if src is not None:
            page.show_pdf_page(page.rect, src, 0)
            src.close()
        else:
            page.insert_image(page.rect, pixmap=pix)
        for link in links:
            if not any(tuple(l["from"]) == tuple(link["from"]) for l in page.get_links()):
                try:
                    page.insert_link({k2: v for k2, v in link.items() if k2 != "xref"})
                except Exception:
                    pass
        page.set_rotation(rot)
    report["rasterized_pages"] = len(pages)
    if pages:
        report["warnings"].append(_("Le pagine rasterizzate aumentano la dimensione del file."))


# ------------------------------------------------------------------ ingresso
def flatten_pdf_bytes(data: bytes, *, annotations: bool = True, annotation_types: set[int] | None = None,
                      include_links: bool = False, forms: bool = True, layers: bool = True, crop: bool = False,
                      rasterize: bool = False, dpi: int = 200, ocr: bool = True, language: str | None = None,
                      pages: str | list[int] | None = None, password: str | None = None,
                      progress=None) -> tuple[bytes, dict]:
    """Appiattisce gli elementi scelti e restituisce (byte, report)."""
    report: dict = {"warnings": []}
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise ConversionError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
        try:
            if pdf.needs_pass:
                if not password or not pdf.authenticate(password):
                    from foliant.api import PasswordRequired
                    raise PasswordRequired()
            if (pdf.metadata or {}).get("encryption") and not (pdf.permissions & pymupdf.PDF_PERM_MODIFY):
                code = pdf.authenticate(password) if password else 0
                if not code & 4:
                    raise ConversionError(_("Il documento non consente modifiche: serve la password del proprietario."))
            if pdf.is_form_pdf and pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/XFA")[0] != "null":
                report["warnings"].append(_("Il modulo XFA dinamico non è appiattibile: resta solo il modulo standard."))
            if isinstance(pages, (list, tuple)):
                page_list = list(pages)
            else:
                page_list = parse_page_range(pages, pdf.page_count)
            check(progress, 0.05, _("Appiattimento…"))
            if annotations or forms:
                flatten_annotations(pdf, page_list, types=annotation_types if annotations else set(),
                                    include_links=include_links, forms=forms, report=report)
            check(progress, 0.3)
            if layers:
                # i livelli sono del documento: si appiattiscono sempre su tutte le pagine
                flatten_layers(pdf, list(range(pdf.page_count)), report)
            if crop:
                flatten_crop(pdf, page_list, report)
            if rasterize:
                rasterize_pages(pdf, page_list, dpi, ocr, language, report, progress)
            check(progress, 0.97, _("Salvataggio…"))
            out = pdf.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            pdf.close()
    return out, report
