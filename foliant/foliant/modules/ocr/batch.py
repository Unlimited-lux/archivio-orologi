"""OCR in blocco su più file (OCR-15): logica senza interfaccia."""

from __future__ import annotations

import csv
import io
import os
import shutil
from dataclasses import dataclass

import pymupdf

from foliant.core.document import atomic_write
from foliant.core.engine import ENGINE_LOCK
from foliant.core.errors import OperationCancelled
from foliant.i18n import _

from . import detect, engine
from .tess import OcrError

PDF_EXT = {".pdf"}
IMAGE_EXT = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
OK, SKIPPED, ERROR = "ok", "skipped", "error"


@dataclass
class BatchItem:
    path: str
    status: str = ""
    message: str = ""
    output: str = ""
    pages: int = 0
    uncertain: int = 0


def collect(paths: list[str], recursive: bool = True, exclude_dir: str | None = None) -> list[str]:
    """File da elaborare (PDF e immagini) da file e cartelle, senza doppioni."""
    out: list[str] = []
    seen: set[str] = set()
    exclude = os.path.realpath(exclude_dir) if exclude_dir else None

    def add(p: str) -> None:
        real = os.path.realpath(p)
        if real in seen:
            return
        if os.path.splitext(p)[1].lower() in PDF_EXT | IMAGE_EXT:
            seen.add(real)
            out.append(p)
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                if exclude and os.path.realpath(root).startswith(exclude):
                    continue
                for f in sorted(files):
                    add(os.path.join(root, f))
                if not recursive:
                    break
                dirs.sort()
        else:
            add(p)
    return out


def output_path(src: str, out_dir: str, suffix: str, ext: str = ".pdf") -> str:
    """Percorso di uscita senza sovrascrivere: aggiunge (2), (3)… se il file esiste."""
    base = os.path.splitext(os.path.basename(src))[0] + (suffix or "")
    folder = out_dir or os.path.dirname(os.path.abspath(src))
    path = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


def _load(path: str) -> bytes:
    """Bytes PDF del file (le immagini, anche TIFF multipagina, diventano PDF)."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        with ENGINE_LOCK:
            try:
                img = pymupdf.open(stream=data, filetype=ext.lstrip("."))
                pdf_bytes = img.convert_to_pdf()
                img.close()
            except Exception as e:
                raise OcrError(_("Immagine non leggibile o danneggiata.")) from e
        return pdf_bytes
    return data


def _already_searchable(data: bytes) -> bool:
    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return False
        try:
            if doc.needs_pass:
                return False
            cats = [detect.classify_page(p, check_mixed=False).category for p in doc]
        finally:
            doc.close()
    return bool(cats) and all(c in (detect.TEXT, detect.OCR_LAYER, detect.EMPTY) for c in cats)


def process_file(item: BatchItem, opts: engine.OcrOptions, out_dir: str = "", suffix: str = "_ocr",
                 fmt: str = "pdf", overwrite: bool = False, backup: bool = True,
                 skip_searchable: bool = True, progress=None) -> BatchItem:
    """Elabora un file. Gli errori finiscono in `item` (status "error"), mai eccezioni,
    tranne l'annullamento che si propaga senza lasciare file parziali."""
    try:
        data = _load(item.path)
        if skip_searchable and _already_searchable(data):
            item.status, item.message = SKIPPED, _("già ricercabile")
            return item
        with ENGINE_LOCK:
            try:
                pdf = pymupdf.open(stream=data, filetype="pdf")
            except Exception:
                raise OcrError(_("Il file non è un PDF valido oppure è danneggiato.")) from None
            if pdf.needs_pass:
                pdf.close()
                raise OcrError(_("File protetto da password: saltato."))
        try:
            result = engine.recognize_document(pdf, opts, None, progress)
            if fmt == "txt":
                from .textexport import result_text
                out = result_text(result, separator="formfeed").encode("utf-8")
                ext = ".txt"
            else:
                with ENGINE_LOCK:
                    engine.apply_result(pdf, result)
                    out = pdf.tobytes(garbage=3, deflate=True)
                ext = ".pdf"
        finally:
            with ENGINE_LOCK:
                pdf.close()
        item.pages = len(result.recognized)
        item.uncertain = sum(len(p.low_confidence_words()) for p in result.recognized)
        if overwrite and fmt == "pdf" and os.path.splitext(item.path)[1].lower() in PDF_EXT:
            if backup:
                shutil.copy2(item.path, output_path(item.path, "", ".bak", ".pdf"))
            dest = item.path
        else:
            dest = output_path(item.path, out_dir, suffix, ext)
        atomic_write(dest, out)
        item.output = dest
        item.status = OK
        item.message = _("Salvato come {d}").format(d=os.path.basename(dest))
        if result.failed:
            item.message += " · " + _("pagine non riconosciute: {p}").format(
                p=engine.format_pages([p.index for p in result.failed]))
    except OperationCancelled:
        raise
    except Exception as e:
        item.status, item.message = ERROR, str(e) or type(e).__name__
    return item


def report_csv(items: list[BatchItem]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_("File"), _("Esito"), _("Messaggio"), _("Pagine elaborate"), _("Parole incerte"), _("Uscita")])
    labels = {OK: _("completato"), SKIPPED: _("saltato"), ERROR: _("errore")}
    for it in items:
        w.writerow([it.path, labels.get(it.status, it.status), it.message, it.pages, it.uncertain, it.output])
    return buf.getvalue()
