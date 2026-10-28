"""Estrazione in blocco delle immagini del documento (OBJ-28), logica pura.

`plan` raccoglie le occorrenze di immagine nell'ordine delle pagine e segna i
duplicati (stesso oggetto o stesso contenuto: vale la prima occorrenza);
`run` scrive i file in una cartella, senza mai sovrascrivere, e restituisce
un riepilogo. La cartella viene creata solo quando si scrive il primo file.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable

import pymupdf

from . import engine
from .content import IMAGE, PageObject, parse_page

FORMATS = ("original", "png", "jpeg")
DEFAULT_PATTERN = "{name}_p{page}_img{n}"


class ExtractError(Exception):
    """Errore di scrittura (es. disco pieno); `report` contiene quanto già fatto."""

    def __init__(self, message: str, report: "ExtractReport"):
        super().__init__(message)
        self.report = report


@dataclass
class ExtractOptions:
    pages: list[int] | None = None          # indici 0-based; None = tutte
    fmt: str = "original"                   # "original", "png", "jpeg"
    min_width: int = 0                      # pixel
    min_height: int = 0
    include_inline: bool = True
    dedup: bool = True
    pattern: str = DEFAULT_PATTERN          # campi: {name}, {page} (1-based), {n}


@dataclass
class ExtractReport:
    written: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (descrizione, motivo)
    duplicates: int = 0
    not_processed: int = 0
    cancelled: bool = False


@dataclass
class ImageJob:
    page_index: int
    n: int                       # posizione dell'immagine nella pagina (1-based)
    obj: PageObject
    key: str                     # impronta del contenuto
    duplicate: bool = False

    @property
    def description(self) -> str:
        kind = "inline " if self.obj.inline else ""
        return f"Pagina {self.page_index + 1}, immagine {kind}{self.n} ({self.obj.width}×{self.obj.height} px)"


def _content_key(pdf: pymupdf.Document, data: bytes, obj: PageObject) -> str:
    h = hashlib.sha1()
    if obj.inline or not obj.xref:
        h.update(b"inline:")
        h.update(data[obj.start:obj.end])
    else:
        try:
            raw = pdf.xref_stream_raw(obj.xref) or b""
        except Exception:
            raw = f"xref:{obj.xref}".encode()
        h.update(raw)
        # anche la maschera distingue due immagini con gli stessi pixel
        h.update(pdf.xref_get_key(obj.xref, "SMask")[1].encode())
    h.update(f"|{obj.width}x{obj.height}".encode())
    return h.hexdigest()


def plan(pdf: pymupdf.Document, options: ExtractOptions) -> list[ImageJob]:
    """Occorrenze di immagine in ordine di pagina, con i duplicati segnati."""
    pages = range(pdf.page_count) if options.pages is None else options.pages
    jobs: list[ImageJob] = []
    seen_xrefs: set[int] = set()
    seen_keys: set[str] = set()
    for pi in pages:
        if not 0 <= pi < pdf.page_count:
            continue
        try:
            parsed = parse_page(pdf, pdf[pi])
        except Exception:
            continue   # pagina danneggiata
        n = 0
        for obj in parsed.objects:
            if obj.kind != IMAGE:
                continue
            n += 1
            key = _content_key(pdf, parsed.data, obj)
            dup = False
            if options.dedup:
                if (obj.xref and obj.xref in seen_xrefs) or key in seen_keys:
                    dup = True
                seen_keys.add(key)
                if obj.xref:
                    seen_xrefs.add(obj.xref)
            jobs.append(ImageJob(pi, n, obj, key, dup))
    return jobs


def _render_inline(pdf: pymupdf.Document, job: ImageJob, fmt: str) -> engine.ExtractedImage:
    """Immagine inline: resa dell'area alla risoluzione nativa (circa)."""
    obj = job.obj
    page = pdf[job.page_index]
    clip = obj.rect if not obj.rect.is_empty else obj.quad.rect
    zoom = 1.0
    if obj.width and clip.width > 0:
        zoom = max(0.01, min(8.0, obj.width / clip.width))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False, annots=False)
    if fmt == "jpeg":
        return engine.ExtractedImage(pix.tobytes("jpeg", jpg_quality=90), "jpg")
    return engine.ExtractedImage(pix.tobytes("png"), "png")


def _write_unique(folder: str, stem: str, ext: str, data: bytes) -> str:
    """Scrive senza mai sovrascrivere: `stem.ext`, `stem_2.ext`, `stem_3.ext`…"""
    i = 1
    while True:
        name = f"{stem}.{ext}" if i == 1 else f"{stem}_{i}.{ext}"
        path = os.path.join(folder, name)
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            i += 1


def run(pdf: pymupdf.Document, options: ExtractOptions, folder: str, base_name: str,
        progress: Callable[[float, str], None] | None = None, lock=None) -> ExtractReport:
    """Estrae le immagini in `folder`.

    `progress(frazione, testo)` può sollevare un'eccezione per interrompere:
    i file già scritti restano e il riepilogo indica quelli non elaborati.
    Un errore di scrittura solleva `ExtractError` con il riepilogo parziale.
    """
    guard = lock if lock is not None else contextlib.nullcontext()
    fmt = options.fmt if options.fmt in FORMATS else "original"
    report = ExtractReport()
    with guard:
        jobs = plan(pdf, options)
    work = [j for j in jobs if not j.duplicate]
    report.duplicates = len(jobs) - len(work)
    total = len(work)
    folder_ready = os.path.isdir(folder)
    for i, job in enumerate(work):
        if progress is not None:
            try:
                progress(i / total, f"Immagine {i + 1} di {total} (pagina {job.page_index + 1})")
            except Exception:
                report.cancelled = True
                report.not_processed = total - i
                return report
        obj = job.obj
        descr = job.description
        if obj.inline and not options.include_inline:
            report.skipped.append((descr, "immagine inline esclusa"))
            continue
        if obj.width < options.min_width or obj.height < options.min_height:
            report.skipped.append((descr, f"più piccola di {options.min_width}×{options.min_height} px"))
            continue
        try:
            with guard:
                if obj.inline or not obj.xref:
                    img = _render_inline(pdf, job, fmt)
                else:
                    img = engine.extract_image(pdf, obj, fmt)
        except Exception as e:  # dati dell'immagine illeggibili
            report.skipped.append((descr, f"dati non leggibili ({e})"))
            continue
        stem = options.pattern.format(name=base_name, page=job.page_index + 1, n=job.n)
        try:
            if not folder_ready:
                os.makedirs(folder, exist_ok=True)
                folder_ready = True
            path = _write_unique(folder, stem, img.ext, img.data)
        except OSError as e:
            report.not_processed = total - i
            raise ExtractError(f"Impossibile scrivere nella cartella «{folder}»: {e.strerror or e}", report) from e
        report.written.append(path)
    if progress is not None and total:
        try:
            progress(1.0, "Completato")
        except Exception:
            pass
    return report
