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
    pages: dict[int, list[tuple[int, str]]] = {}
    for r in refs:
        body = objs.get(r)
        if not body:
            continue
        pm = re.search(r"/Page\s+(\d+)", body)
        page = int(pm.group(1)) if pm else 0
        pages.setdefault(page, []).append((r, re.sub(r"/Page\s+\d+", "", body)))
    if not pages:
        return '<?xml version="1.0" encoding="UTF-8"?><xfdf xmlns="http://ns.adobe.com/xfdf/"><annots/></xfdf>'
    tmp = pymupdf.open()
    try:
        for i in range(max(pages) + 1):
            tmp.new_page()
        new_xref = {}                   # oggetto FDF → oggetto del PDF temporaneo
        for bodies in pages.values():
            for r, _body in bodies:
                new_xref[r] = tmp.get_new_xref()

        def irt(mm) -> str:             # risposte e stati: riferimento al padre nel PDF temporaneo
            x = new_xref.get(int(mm.group(1)))
            return f"/IRT {x} 0 R" if x is not None else ""

        for page_no, bodies in pages.items():
            page = tmp[page_no]
            xrefs = []
            for r, body in bodies:
                x = new_xref[r]
                tmp.update_object(x, re.sub(r"/IRT\s*(\d+)\s+\d+\s+R", irt, body))
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
        labels = [_("PDF: solo elenco"), _("PDF: pagine con commenti numerati"), _("CSV (foglio di calcolo)"),
                  _("Testo semplice"), _("Pagina HTML")]
        idx = dialogs.ask_item(view, _("Riepilogo commenti"), _("Formato:"), labels, 0)
        if idx is None:
            return None
        fmt = ["pdf", "pdf-page", "csv", "txt", "html"][idx]
    layout = "page" if fmt == "pdf-page" else "list"
    fmt = "pdf" if fmt == "pdf-page" else fmt
    file_filter = _(dict(SUMMARY_FORMATS)[fmt])
    path = ctrl.ctx.ask_save_path(_("Riepilogo commenti"), _stem(ctrl) + " - " + _("commenti") + "." + fmt,
                                  file_filter, default_suffix="." + fmt)
    if not path:
        return None
    title = view.document.display_name
    with ENGINE_LOCK:
        pdf = view.document.pdf
        if fmt == "pdf":
            data = engine.summary_pdf(pdf, title, layout)
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


def flatten(ctrl: "Controller", current_page_only: bool = False, selection: bool = False) -> int:
    view = ctrl.view()
    if view is None:
        return 0
    if not ctrl.annotations():
        ctrl.ctx.info(_("Appiattisci annotazioni"), _("Il documento non contiene annotazioni da appiattire."))
        return 0
    pages = [view.current_page] if current_page_only else None
    only = list(ctrl.selection) if selection else None
    if selection and not only:
        return 0
    n = ctrl.apply(_("Appiattisci annotazioni"), engine.flatten, pages, only,
                 change_kind="content") or 0
    ctrl.clear_selection()
    ctrl.ctx.status(_("Annotazioni appiattite: {n}").format(n=n) + " — " + _("Annulla per ripristinarle."))
    return n


def delete_all(ctrl: "Controller") -> int:
    infos = ctrl.annotations()
    if not infos:
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


def export_measures(ctrl: "Controller") -> str | None:
    view = ctrl.view()
    if view is None:
        return None
    if not any(i.kind in engine.MEASURE_KINDS for i in ctrl.annotations()):
        ctrl.ctx.info(_("Esporta misure"), _("Il documento non contiene misure."))
        return None
    path = ctrl.ctx.ask_save_path(_("Esporta misure"), _stem(ctrl) + " - " + _("misure") + ".csv", "CSV (*.csv)",
                                  default_suffix=".csv")
    if not path:
        return None
    with ENGINE_LOCK:
        data = engine.measures_csv(view.document.pdf).encode("utf-8")
    try:
        atomic_write(path, data)
    except Exception as e:
        ctrl.ctx.error(_("Esporta misure"), str(e))
        return None
    return path


def set_scale(ctrl: "Controller") -> engine.Scale | None:
    view = ctrl.view()
    scale = dialogs.ask_scale(view, ctrl.scale)
    if scale is not None:
        ctrl.set_scale(scale, view.current_page if view is not None else None)
    return scale


def export_styles(ctrl: "Controller") -> str | None:
    path = ctrl.ctx.ask_save_path(_("Esporta stili"), "stili-commenti.json", "JSON (*.json)", default_suffix=".json")
    if not path:
        return None
    try:
        atomic_write(path, ctrl.export_styles().encode("utf-8"))
    except Exception as e:
        ctrl.ctx.error(_("Esporta stili"), str(e))
        return None
    return path


def import_styles(ctrl: "Controller", path: str | None = None) -> int:
    if path is None:
        paths = ctrl.ctx.ask_open_paths(_("Importa stili"), "JSON (*.json)", multiple=False)
        path = paths[0] if paths else None
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            n = ctrl.import_styles(f.read())
    except (OSError, ValueError, engine.AnnotationError) as e:
        ctrl.ctx.error(_("Importa stili"), _("Impossibile importare gli stili: {e}").format(e=_(str(e))))
        return 0
    ctrl.ctx.status(_("Stili importati."))
    return n


def export_stamp_library(ctrl: "Controller") -> str | None:
    import json
    path = ctrl.ctx.ask_save_path(_("Esporta libreria timbri"), "timbri.json", "JSON (*.json)", default_suffix=".json")
    if not path:
        return None
    data = {"version": 1, "stamps": ctrl.custom_stamps(), "dynamic": ctrl.dynamic_templates()}
    try:
        atomic_write(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        ctrl.ctx.error(_("Esporta libreria timbri"), str(e))
        return None
    return path


def import_stamp_library(ctrl: "Controller", path: str | None = None) -> int:
    import base64
    import json
    if path is None:
        paths = ctrl.ctx.ask_open_paths(_("Importa libreria timbri"), "JSON (*.json)", multiple=False)
        path = paths[0] if paths else None
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or int(data.get("version", 0)) > 1:
            raise ValueError(_("versione non supportata"))
        n = 0
        for d in data.get("stamps", []):
            png = base64.b64decode(d["png"])
            pymupdf.Pixmap(png)              # immagine valida
            ctrl.add_custom_stamp(str(d["name"]), png)
            n += 1
        for d in data.get("dynamic", []):
            ctrl.add_dynamic_template(str(d["template"]), str(d.get("color", "#2650B3")))
            n += 1
    except Exception as e:
        ctrl.ctx.error(_("Importa libreria timbri"), _("File non valido: la libreria non è cambiata ({e}).").format(e=e))
        return 0
    ctrl.ctx.status(_("Timbri importati: {n}").format(n=n))
    return n
