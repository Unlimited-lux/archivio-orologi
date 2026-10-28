"""Motore di conversione da PDF (senza interfaccia), riusato dalle operazioni in blocco.

    from foliant.modules.convert.engine import convert_pdf
    paths = convert_pdf("in.pdf", "out.docx", "docx", pages="1-3", layout="flow")

Formati: docx, xlsx, csv, pptx, png, jpeg, tiff, bmp, gif, long-image, txt, rtf,
html, xml, epub, pdfa (pdfa-1b, pdfa-2b, pdfa-3b).

Opzioni comuni (`**options`):
- `pages`: espressione d'intervallo («1-3, 7, 10-fine», «pari»…) o elenco di indici (base 0);
- `password`: password di apertura del sorgente;
- `policy`: "rename" (default, suffisso « (1)») o "overwrite" se il file esiste;
- `ocr`: "auto" (default), "always", "never"; `language`: es. "ita+eng";
- `layout`: "flow" (scorrevole) o "faithful" (fedele) per DOCX, HTML, RTF, EPUB, TXT;
- `dpi`: risoluzione di rasterizzazione/immagini (default 150; 300 per la grafica).
Le opzioni specifiche sono documentate nelle funzioni `write_*`.

I file sono scritti come temporanei nella cartella di destinazione e rinominati
solo a lavoro concluso: un annullamento o un errore non lascia file parziali.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pymupdf

from foliant.api import ENGINE_LOCK, OperationCancelled, _

from .common import ConversionError, OutputFiles, RangeError, check, open_source, parse_page_range
from .extract import NATIVE, SCANNED, PageContent, classify_page, ocr_available, ocr_languages

# formato → (estensione, descrizione, gruppo)
FORMATS: dict[str, tuple[str, str, str]] = {
    "docx": (".docx", "Word (DOCX)", "office"),
    "xlsx": (".xlsx", "Excel (XLSX)", "office"),
    "pptx": (".pptx", "PowerPoint (PPTX)", "office"),
    "csv": (".csv", "CSV", "office"),
    "png": (".png", "PNG", "image"),
    "jpeg": (".jpg", "JPEG", "image"),
    "tiff": (".tif", "TIFF", "image"),
    "bmp": (".bmp", "BMP", "image"),
    "gif": (".gif", "GIF", "image"),
    "long-image": (".png", "Immagine lunga", "image"),
    "txt": (".txt", "Testo (TXT)", "text"),
    "rtf": (".rtf", "RTF", "text"),
    "html": (".html", "HTML", "text"),
    "xml": (".xml", "XML", "text"),
    "epub": (".epub", "EPUB", "text"),
    "pdfa-1b": (".pdf", "PDF/A-1b", "pdfa"),
    "pdfa-2b": (".pdf", "PDF/A-2b", "pdfa"),
    "pdfa-3b": (".pdf", "PDF/A-3b", "pdfa"),
    "pdfa-2u": (".pdf", "PDF/A-2u", "pdfa"),
    "pdfa-3u": (".pdf", "PDF/A-3u", "pdfa"),
}
ALIASES = {"jpg": "jpeg", "tif": "tiff", "htm": "html", "pdfa": "pdfa-2b", "pdf/a": "pdfa-2b", "word": "docx",
           "excel": "xlsx", "powerpoint": "pptx", "text": "txt", "long_image": "long-image", "longimage": "long-image"}
TEXT_FORMATS = {"docx", "xlsx", "csv", "pptx", "txt", "rtf", "html", "xml", "epub"}


def normalize_format(fmt: str) -> str:
    key = (fmt or "").strip().lower().lstrip(".")
    key = ALIASES.get(key, key)
    if key not in FORMATS:
        raise ConversionError(_("Formato di uscita non supportato: «{f}».").format(f=fmt))
    return key


def default_output_path(input_path: str | os.PathLike, fmt: str, folder: str | None = None) -> str:
    """Percorso di uscita predefinito: stesso nome, estensione del formato."""
    fmt = normalize_format(fmt)
    src = Path(input_path)
    ext = FORMATS[fmt][0]
    stem = src.stem + ("_pdfa" if fmt.startswith("pdfa") else "")
    return str(Path(folder or src.parent) / (stem + ext))


@dataclass
class ConversionResult:
    paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)        # pagine (base 1) riconosciute con OCR
    scanned_pages: list[int] = field(default_factory=list)    # pagine (base 1) scansionate
    pages: list[int] = field(default_factory=list)            # pagine convertite (base 1)
    details: dict = field(default_factory=dict)

    def report_text(self) -> str:
        lines = [_("Pagine convertite: {n}").format(n=len(self.pages))]
        if self.ocr_pages:
            lines.append(_("Pagine riconosciute con OCR: {p}").format(p=", ".join(map(str, self.ocr_pages))))
        for p in self.paths:
            lines.append(_("File creato: {p}").format(p=p))
        if self.warnings:
            lines.append(_("Avvisi:"))
            lines.extend(f"- {w}" for w in self.warnings)
        return "\n".join(lines)


def foliant_ocr_module():
    """(ocr_pdf_bytes, modalità EDITABLE) del modulo OCR di Foliant, o None se non installato."""
    try:
        from foliant.modules.ocr import engine as ocr_engine  # type: ignore
    except Exception:
        return None
    fn = getattr(ocr_engine, "ocr_pdf_bytes", None)
    if fn is None:
        return None
    return fn, getattr(ocr_engine, "EDITABLE", "editable")


class Job:
    """Stato di una conversione: opzioni, avanzamento e report."""

    def __init__(self, fmt: str, name: str, page_map: list[int], progress, options: dict):
        self.fmt = fmt
        self.name = name
        self.page_map = page_map
        self.progress = progress
        self.options = options
        self.ocr = options.get("ocr", "auto")
        self.language = options.get("language")
        self.dpi = int(options.get("dpi", 150))
        self.result = ConversionResult(pages=[p + 1 for p in page_map])
        self._warned: set[str] = set()

    def source_index(self, i: int) -> int:
        return self.page_map[i] if 0 <= i < len(self.page_map) else i

    def warn(self, text: str) -> None:
        if text not in self._warned:
            self._warned.add(text)
            self.result.warnings.append(text)

    def note_page(self, pc: PageContent) -> None:
        p = pc.index + 1
        if pc.ocr and p not in self.result.ocr_pages:
            self.result.ocr_pages.append(p)
        if "scansione-senza-ocr" in pc.warnings:
            self.warn(_("Pagina {p} scansionata: OCR disattivato, il testo non è stato riconosciuto.").format(p=p))
        if "ocr-non-disponibile" in pc.warnings:
            self.warn(_("Pagina {p} scansionata: OCR non disponibile (installa tesseract-ocr).").format(p=p))

    # ------------------------------------------------------------------ OCR
    def ocr_document_bytes(self, work: pymupdf.Document) -> bytes:
        """Copia del documento di lavoro con un livello di testo OCR sulle pagine scansionate.

        Usa il modulo OCR di Foliant se presente, altrimenti l'OCR del motore
        (Tesseract). Da chiamare tenendo ENGINE_LOCK.
        """
        data = work.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
        if self.ocr == "never":
            return data
        scanned = [i for i in range(work.page_count)
                   if classify_page(work[i]) == SCANNED or (self.ocr == "always" and classify_page(work[i]) != NATIVE)]
        if not scanned:
            return data
        ocr_module = foliant_ocr_module()
        if ocr_module is not None:   # modulo OCR di Foliant: testo vero e modificabile (modalità EDITABLE)
            ocr_pdf_bytes, editable = ocr_module
            langs = [x for x in (self.language or "ita+eng").split("+") if x]
            try:
                ENGINE_LOCK.release()   # il modulo OCR prende il lock da sé, pagina per pagina
                try:
                    out = ocr_pdf_bytes(data, langs, editable, pages=scanned, progress=self.progress)
                finally:
                    ENGINE_LOCK.acquire()
                if isinstance(out, tuple):
                    out = out[0]
                if isinstance(out, (bytes, bytearray)) and out:
                    self.result.ocr_pages.extend(self.source_index(i) + 1 for i in scanned)
                    return bytes(out)
                self.warn(_("Il modulo OCR non ha restituito un documento: uso il riconoscimento di base."))
            except OperationCancelled:
                raise
            except Exception as e:
                self.warn(_("OCR non riuscito ({e}): uso il riconoscimento di base.").format(e=e))
        if not ocr_available():
            for i in scanned:
                self.warn(_("Pagina {p} scansionata: OCR non disponibile (installa tesseract-ocr).").format(
                    p=self.source_index(i) + 1))
            return data
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            return self._ocr_pages(doc, scanned)
        finally:
            doc.close()

    def _ocr_pages(self, doc: pymupdf.Document, scanned: list[int]) -> bytes:
        lang = ocr_languages(self.language)
        for k, i in enumerate(scanned):
            check(self.progress, 0.1 * k / len(scanned), _("OCR della pagina {p}").format(p=self.source_index(i) + 1))
            try:
                page = doc[i]
                pix = page.get_pixmap(dpi=300)
                ocr_pdf = pymupdf.open(stream=pix.pdfocr_tobytes(language=lang), filetype="pdf")
                try:
                    doc.insert_pdf(ocr_pdf, start_at=i + 1)
                    doc.delete_page(i)
                finally:
                    ocr_pdf.close()
                self.result.ocr_pages.append(self.source_index(i) + 1)
            except OperationCancelled:
                raise
            except Exception:
                self.warn(_("OCR non riuscito a pagina {p}.").format(p=self.source_index(i) + 1))
        return doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)


_LANGS = {"it": "ita", "en": "eng", "de": "deu", "fr": "fra", "es": "spa", "pt": "por", "nl": "nld"}


def _lang_to_tesseract(pdf: pymupdf.Document) -> str:
    """Lingue OCR predefinite dalla chiave /Lang del catalogo (più l'inglese)."""
    try:
        kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "Lang")
        code = val.strip("()").lower()[:2] if kind == "string" else ""
    except Exception:
        code = ""
    main = _LANGS.get(code, "ita")
    return main if main == "eng" else f"{main}+eng"


def _make_work_document(src: pymupdf.Document, pages: list[int]) -> pymupdf.Document:
    """Documento con le sole pagine scelte, nell'ordine scelto (link e segnalibri adattati)."""
    if pages == list(range(src.page_count)):
        return pymupdf.open(stream=src.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
    work = pymupdf.open(stream=src.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
    work.select(pages)
    return work


def run_conversion(source, output_path: str | os.PathLike, fmt: str, progress=None, **options) -> ConversionResult:
    """Converte un PDF (percorso o byte) e restituisce file creati e report."""
    from . import export_image, export_office, export_text

    fmt = normalize_format(fmt)
    output_path = str(output_path)
    if not output_path:
        raise ConversionError(_("Indica il file di destinazione."))
    ext = FORMATS[fmt][0]
    if not Path(output_path).suffix and fmt not in ("long-image",):
        output_path += ext
    check(progress, 0.0, _("Apertura del documento…"))
    with ENGINE_LOCK:
        src = open_source(source, options.get("password"), need_extract=not fmt.startswith("pdfa"))
        work = None
        try:
            pages = options.get("pages")
            if isinstance(pages, (list, tuple)):
                bad = [p for p in pages if not 0 <= int(p) < src.page_count]
                if bad or not pages:
                    raise RangeError(_("Intervallo di pagine non valido."))
                page_list = [int(p) for p in pages]
            else:
                page_list = parse_page_range(pages, src.page_count, options.get("current", 0),
                                             options.get("selected"))
            name = Path(str(source)).stem if not isinstance(source, (bytes, bytearray)) else \
                Path(output_path).stem
            name = options.get("name") or name
            if not options.get("language"):
                options = dict(options)
                options["language"] = _lang_to_tesseract(src)
            if fmt.startswith("pdfa"):
                work = None
                src_bytes = src.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE) if page_list == list(range(src.page_count)) else None
                if src_bytes is None:
                    tmpdoc = _make_work_document(src, page_list)
                    src_bytes = tmpdoc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
                    tmpdoc.close()
            else:
                # il sorgente è già una copia indipendente (file o byte): diventa il documento di lavoro
                if page_list != list(range(src.page_count)):
                    src.select(page_list)
                work, src = src, None
                signed = False
                try:
                    signed = any(w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.field_value
                                 for p in work for w in (p.widgets() or []))
                except Exception:
                    pass
                embedded = work.embfile_count() if hasattr(work, "embfile_count") else 0
        except BaseException:
            if work is not None:
                work.close()
            raise
        finally:
            if src is not None:
                src.close()

    job = Job(fmt, name, page_list, progress, dict(options))
    try:
        return _run_job(work, job, fmt, output_path,
                        src_bytes if work is None else None, signed if work is not None else False,
                        embedded if work is not None else 0, progress, options)
    finally:
        with ENGINE_LOCK:
            for doc in (work, getattr(job, "_replaced_work", None)):
                if doc is not None and not doc.is_closed:
                    doc.close()


def _run_job(work, job: Job, fmt: str, output_path: str, src_bytes, signed: bool, embedded: int, progress,
             options: dict) -> ConversionResult:
    from . import export_image, export_office, export_text
    if work is not None and fmt in TEXT_FORMATS and job.ocr != "never" and foliant_ocr_module() is not None:
        # pagine scansionate riconosciute dal modulo OCR prima dell'analisi (tutti i layout)
        with ENGINE_LOCK:
            scanned_any = any(classify_page(work[i]) == SCANNED for i in range(work.page_count))
            if scanned_any:
                data = job.ocr_document_bytes(work)
                work.close()
                work = pymupdf.open(stream=data, filetype="pdf")
                job._replaced_work = work
    if work is not None:
        if signed:
            job.warn(_("Il documento è firmato: le firme non sono trasferibili nel formato di uscita."))
        if embedded:
            job.warn(_("Gli allegati del PDF ({n}) non sono stati convertiti.").format(n=embedded))
        if fmt in TEXT_FORMATS and job.ocr == "never":
            with ENGINE_LOCK:
                scanned = [job.source_index(i) + 1 for i in range(work.page_count)
                           if classify_page(work[i]) == SCANNED]
            job.result.scanned_pages = scanned
    policy = options.get("policy", "rename")
    try:
        with OutputFiles(policy) as out:
            if fmt.startswith("pdfa"):
                from . import pdfa
                level = fmt.split("-", 1)[1]
                data, report = pdfa.convert_to_pdfa(src_bytes, level, password=None, progress=progress)
                job.result.details["pdfa"] = report
                if not report.get("compliant", False) and not options.get("save_anyway", False):
                    errors = "; ".join(report.get("errors", [])[:5])
                    raise ConversionError(_("Conformità PDF/A non raggiunta: {e}").format(e=errors or "?"))
                if not report.get("compliant", False):
                    data = pdfa.strip_pdfa_identification(data)
                    job.warn(_("Il file è stato salvato come PDF ordinario, senza dichiarazione PDF/A."))
                tmp = out.new(output_path)
                Path(tmp).write_bytes(data)
            elif fmt in ("png", "jpeg", "tiff", "bmp", "gif"):
                export_image.write_images(work, output_path, job, out)
            elif fmt == "long-image":
                p = Path(output_path)
                if not p.suffix:
                    output_path += ".jpg" if options.get("image_format") in ("jpeg", "jpg") else ".png"
                elif p.suffix.lower() in (".jpg", ".jpeg"):
                    job.options["image_format"] = "jpeg"
                export_image.write_long_image(work, output_path, job, out)
            elif fmt == "csv":
                export_office.write_csv_files(work, output_path, job, out)
            elif fmt == "html":
                export_text.write_html(work, output_path, job, out)
            else:
                writer: Callable[[Any, str, Job], None] = {
                    "docx": export_office.write_docx, "xlsx": export_office.write_xlsx,
                    "pptx": export_office.write_pptx, "txt": export_text.write_txt,
                    "rtf": export_text.write_rtf, "xml": export_text.write_xml, "epub": export_text.write_epub,
                }[fmt]
                tmp = out.new(output_path)
                writer(work, tmp, job)
            check(progress, 1.0, _("Completamento…"))
            job.result.paths = out.commit()
    except OSError as e:
        if e.errno == 28:
            raise ConversionError(_("Spazio su disco insufficiente: conversione interrotta.")) from e
        raise ConversionError(_("Impossibile scrivere il file: {e}").format(e=e.strerror or e)) from e
    job.result.ocr_pages = sorted(set(job.result.ocr_pages))
    return job.result


def convert_pdf(input_path, output_path, fmt, progress=None, **options) -> list[str]:
    """Converte `input_path` (percorso o byte PDF) nel formato `fmt`; restituisce i file creati.

    Per i formati a più file (immagini per pagina, CSV per tabella, HTML con
    cartella di risorse) `output_path` è il nome base. Vedi la docstring del modulo.
    """
    return run_conversion(input_path, output_path, fmt, progress, **options).paths


def scanned_pages(source, password: str | None = None, limit: int | None = None) -> list[int]:
    """Pagine (base 0) senza testo e coperte da un'immagine: candidate all'OCR.

    Con `limit` si esamina un campione distribuito di al massimo `limit` pagine."""
    with ENGINE_LOCK:
        src = open_source(source, password, need_extract=False)
        try:
            n = src.page_count
            indices = range(n) if not limit or n <= limit else sorted({int(k * n / limit) for k in range(limit)})
            return [i for i in indices if classify_page(src[i]) == SCANNED]
        finally:
            src.close()
