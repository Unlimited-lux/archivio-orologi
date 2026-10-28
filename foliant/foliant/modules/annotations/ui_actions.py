"""Comandi del modulo commenti che usano finestre di dialogo (file, conferme)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pymupdf

from foliant.api import ENGINE_LOCK, _, atomic_write

from . import dialogs, engine

if TYPE_CHECKING:
    from .controller import Controller

XFDF_FILTER = "XFDF (*.xfdf)"
FDF_FILTER = "FDF (*.fdf)"
IMPORT_FILTER = "XFDF, FDF, PDF (*.xfdf *.fdf *.pdf)"

# Estensioni che non si aprono mai direttamente (solo salvataggio).
UNSAFE_EXT = {".sh", ".bash", ".exe", ".bat", ".cmd", ".com", ".msi", ".js", ".vbs", ".ps1", ".py", ".pl",
              ".jar", ".app", ".desktop", ".run", ".bin", ".appimage", ".scr", ".deb", ".rpm"}


def _stem(ctrl: "Controller") -> str:
    doc = ctrl.ctx.current_document()
    return os.path.splitext(doc.display_name)[0] if doc else "commenti"


def save_attachment(ctrl: "Controller", page: int, xref: int) -> str | None:
    try:
        name, data = ctrl.read(engine.attachment_data, page, xref)
    except Exception as e:
        ctrl.ctx.error(_("Allegato"), _("Impossibile leggere l'allegato: {e}").format(e=e))
        return None
    path = ctrl.ctx.ask_save_path(_("Salva allegato con nome"), name, _("Tutti i file (*)"), default_suffix="")
    if not path:
        return None
    try:
        atomic_write(path, data)
    except Exception as e:
        ctrl.ctx.error(_("Allegato"), str(e))
        return None
    ctrl.ctx.status(_("Allegato salvato in {p}").format(p=path))
    return path


def export_comments(ctrl: "Controller", fmt: str = "xfdf", items: list[tuple[int, int]] | None = None) -> str | None:
    view = ctrl.view()
    if view is None:
        return None
    infos = ctrl.annotations()
    if not infos:
        ctrl.ctx.info(_("Esporta commenti"), _("Il documento non contiene commenti da esportare."))
        return None
    suggested = _stem(ctrl) + "." + fmt
    path = ctrl.ctx.ask_save_path(_("Esporta commenti"), suggested, XFDF_FILTER if fmt == "xfdf" else FDF_FILTER,
                                  default_suffix="." + fmt)
    if not path:
        return None
    name = view.document.display_name
    with ENGINE_LOCK:
        pdf = view.document.pdf
        if fmt == "xfdf":
            data = engine.export_xfdf(pdf, name).encode("utf-8")
        else:
            data = engine.export_fdf(pdf, name)
    try:
        atomic_write(path, data)
    except Exception as e:
        ctrl.ctx.error(_("Esporta commenti"), str(e))
        return None
    ctrl.ctx.status(_("Esportati {n} commenti in {p}").format(n=len(infos), p=os.path.basename(path)))
    return path


def import_comments(ctrl: "Controller", paths: list[str] | None = None) -> engine.ImportResult | None:
    view = ctrl.view()
    if view is None:
        return None
    if paths is None:
        paths = ctrl.ctx.ask_open_paths(_("Importa commenti"), IMPORT_FILTER, multiple=True)
    if not paths:
        return None
    sources: list[tuple[str, str]] = []
    for path in paths:
        try:
            sources.append((path, read_comment_source(path)))
        except engine.AnnotationError as e:
            ctrl.ctx.error(_("Importa commenti"), f"{os.path.basename(path)}: {_(str(e))}")
            return None
        except OSError as e:
            ctrl.ctx.error(_("Importa commenti"), f"{os.path.basename(path)}: {e.strerror}")
            return None

    def run(pdf):
        total = engine.ImportResult()
        for _path, xfdf in sources:
            res = engine.import_xfdf(pdf, xfdf)
            total.imported += res.imported
            total.skipped += res.skipped
            total.messages.extend(res.messages)
        return total

    res = ctrl.apply(_("Importa commenti"), run)
    if res is not None:
        msg = _("Importati: {i}. Saltati: {s}.").format(i=res.imported, s=res.skipped)
        if res.messages:
            msg += "\n\n" + "\n".join(res.messages[:20])
        ctrl.ctx.info(_("Importa commenti"), msg)
    return res


def read_comment_source(path: str) -> str:
    """Restituisce XFDF dai file XFDF, FDF o PDF."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xfdf" or data.lstrip()[:5] == b"<?xml" or b"<xfdf" in data[:500]:
        return data.decode("utf-8", errors="replace")
    if ext == ".fdf" or data.startswith(b"%FDF"):
        return fdf_to_xfdf(data)
    try:
        src = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise engine.AnnotationError("Il file non è un PDF valido oppure è danneggiato.") from e
    try:
        if src.needs_pass:
            raise engine.AnnotationError("Il PDF di origine è protetto da password.")
        return engine.export_xfdf(src, os.path.basename(path))
    finally:
        src.close()


def fdf_to_xfdf(data: bytes) -> str:
    """Converte un FDF in XFDF ricostruendo un PDF temporaneo con le stesse annotazioni."""
    import re

    text = data.decode("latin-1", errors="replace")
    objs = dict((int(n), body) for n, body in re.findall(r"(\d+)\s+0\s+obj\s*(.*?)\s*endobj", text, re.S))
    m = re.search(r"/Annots\s*\[([^\]]*)\]", text)
    if not m:
        raise engine.AnnotationError("Il file non è un FDF valido.")
    refs = [int(x) for x in re.findall(r"(\d+)\s+0\s+R", m.group(1))]
    pages: dict[int, list[str]] = {}
    for r in refs:
        body = objs.get(r)
        if not body:
            continue
        pm = re.search(r"/Page\s+(\d+)", body)
        page = int(pm.group(1)) if pm else 0
        pages.setdefault(page, []).append(re.sub(r"/Page\s+\d+", "", body))
    if not pages:
        return '<?xml version="1.0" encoding="UTF-8"?><xfdf xmlns="http://ns.adobe.com/xfdf/"><annots/></xfdf>'
    tmp = pymupdf.open()
    try:
        for i in range(max(pages) + 1):
            tmp.new_page()
        for page_no, bodies in pages.items():
            page = tmp[page_no]
            xrefs = []
            for body in bodies:
                x = tmp.get_new_xref()
                tmp.update_object(x, body)
                tmp.xref_set_key(x, "P", f"{page.xref} 0 R")
                xrefs.append(x)
            tmp.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")
        tmp2 = pymupdf.open(stream=tmp.tobytes(), filetype="pdf")
        try:
            return engine.export_xfdf(tmp2)
        finally:
            tmp2.close()
    finally:
        tmp.close()


SUMMARY_FORMATS = [("pdf", "PDF (*.pdf)"), ("csv", "CSV (*.csv)"), ("txt", "Testo (*.txt)"),
                   ("html", "HTML (*.html)")]


def export_summary(ctrl: "Controller", fmt: str | None = None) -> str | None:
    view = ctrl.view()
    if view is None:
        return None
    if not ctrl.annotations():
        ctrl.ctx.info(_("Riepilogo commenti"), _("Il documento non contiene commenti."))
        return None
    if fmt is None:
        labels = [_("PDF"), _("CSV (foglio di calcolo)"), _("Testo semplice"), _("Pagina HTML")]
        idx = dialogs.ask_item(view, _("Riepilogo commenti"), _("Formato:"), labels, 0)
        if idx is None:
            return None
        fmt = SUMMARY_FORMATS[idx][0]
    file_filter = _(dict(SUMMARY_FORMATS)[fmt])
    path = ctrl.ctx.ask_save_path(_("Riepilogo commenti"), _stem(ctrl) + " - " + _("commenti") + "." + fmt,
                                  file_filter, default_suffix="." + fmt)
    if not path:
        return None
    title = view.document.display_name
    with ENGINE_LOCK:
        pdf = view.document.pdf
        if fmt == "pdf":
            data = engine.summary_pdf(pdf, title)
        elif fmt == "csv":
            data = engine.summary_csv(pdf).encode("utf-8-sig")
        elif fmt == "html":
            data = engine.summary_html(pdf, title).encode("utf-8")
        else:
            data = engine.summary_text(pdf, title).encode("utf-8")
    try:
        atomic_write(path, data)
    except Exception as e:
        ctrl.ctx.error(_("Riepilogo commenti"), str(e))
        return None
    ctrl.ctx.status(_("Riepilogo salvato in {p}").format(p=os.path.basename(path)))
    return path


def flatten(ctrl: "Controller", current_page_only: bool = False) -> int:
    view = ctrl.view()
    if view is None:
        return 0
    if not ctrl.annotations():
        ctrl.ctx.info(_("Appiattisci annotazioni"), _("Il documento non contiene annotazioni da appiattire."))
        return 0
    if not ctrl.ctx.confirm(_("Appiattisci annotazioni"),
                            _("Le annotazioni diventeranno parte fissa delle pagine e, dopo il salvataggio, "
                              "non saranno più modificabili. Continuare?")):
        return 0
    pages = [view.current_page] if current_page_only else None
    n = ctrl.apply(_("Appiattisci annotazioni"), engine.flatten, pages) or 0
    ctrl.clear_selection()
    ctrl.ctx.status(_("Annotazioni appiattite: {n}").format(n=n))
    return n


def delete_all(ctrl: "Controller") -> int:
    infos = ctrl.annotations()
    if not infos:
        return 0
    if not ctrl.ctx.confirm(_("Elimina tutti i commenti"),
                            _("Eliminare tutti i {n} commenti del documento?").format(n=len(infos))):
        return 0
    n = ctrl.apply(_("Elimina tutti i commenti"), engine.delete_all) or 0
    ctrl.clear_selection()
    return n


def choose_image_stamp(ctrl: "Controller") -> tuple[bytes, str] | None:
    path = dialogs.ask_open_file(ctrl.view(), _("Timbro da immagine"),
                                 _("Immagini (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)"))
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
        pymupdf.Pixmap(data)
    except Exception:
        ctrl.ctx.error(_("Timbro da immagine"), _("Il file non è un'immagine leggibile."))
        return None
    return data, os.path.splitext(os.path.basename(path))[0]


def set_scale(ctrl: "Controller") -> engine.Scale | None:
    scale = dialogs.ask_scale(ctrl.view(), ctrl.scale)
    if scale is not None:
        ctrl.scale = scale
        ctrl.page_scales.clear()
        ctrl.ctx.status(_("Scala: {s}").format(s=scale.text()))
    return scale
