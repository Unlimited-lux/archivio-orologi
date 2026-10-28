"""Compressione, ottimizzazione avanzata, analisi dello spazio, linearizzazione e
riparazione dei PDF (CNV-29, CNV-30, CNV-31, CNV-32). Solo logica, niente Qt.

Tutte le funzioni lavorano su byte in ingresso e producono byte nuovi: il file
sorgente non viene mai modificato. Ogni accesso al motore avviene tenendo
`ENGINE_LOCK`; la codifica delle immagini con Pillow avviene invece fuori dal
lock, così l'interfaccia può continuare a disegnare le pagine.
"""

from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from foliant.api import ENGINE_LOCK, PasswordRequired, WrongPassword, _
from foliant.core.document import protection_options

from .common import ConversionError, OperationCancelled, OutputFiles, check

# ------------------------------------------------------------------ livelli
LEVELS = ("lossless", "high", "medium", "low")


@dataclass(frozen=True)
class ImageParams:
    ppi: float | None            # risoluzione di destinazione (colore e grigi); None = nessun ricampionamento
    threshold: float | None      # ricampiona solo sopra questa risoluzione
    quality: int | None          # qualità JPEG; None = nessuna ricompressione con perdita
    mono_ppi: float | None       # destinazione delle immagini monocromatiche
    mono_threshold: float | None
    grayscale: bool = False


def level_params(level: str) -> ImageParams:
    """Parametri delle immagini per un livello di CNV-29 (soglia = 1,5 × destinazione)."""
    table = {
        "lossless": (None, None, None),
        "high": (225, 85, 600),
        "medium": (150, 70, 300),
        "low": (96, 50, 200),
    }
    if level not in table:
        raise ConversionError(_("Livello di compressione sconosciuto: «{l}».").format(l=level))
    ppi, quality, mono = table[level]
    if ppi is None:
        return ImageParams(None, None, None, None, None)
    return ImageParams(ppi, ppi * 1.5, quality, mono, mono * 1.5)


LEVEL_LABELS = {"lossless": "Senza perdita", "high": "Alta", "medium": "Media", "low": "Bassa"}


# ------------------------------------------------------------------ apertura
def _open(data: bytes, password: str | None) -> tuple[pymupdf.Document, dict]:
    """Apre i byte del PDF (tenendo ENGINE_LOCK) e restituisce anche le opzioni di cifratura."""
    if not data:
        raise ConversionError(_("Il file è vuoto."))
    kind = detect_file_type(data)
    if kind not in ("pdf", "unknown"):
        err = ConversionError(_not_pdf_message(kind))
        err.detected_type = kind
        raise err
    try:
        pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
    except Exception as e:
        raise ConversionError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
    needed = bool(pdf.needs_pass)
    code = 0
    if needed:
        if not password:
            pdf.close()
            raise PasswordRequired()
        code = pdf.authenticate(password)
        if not code:
            pdf.close()
            raise WrongPassword()
    if pdf.page_count == 0:
        pdf.close()
        raise ConversionError(_("Il documento non contiene pagine."))
    try:
        prot = protection_options(pdf, password, code, needed)
    except Exception:
        prot = {}
    return pdf, prot


def _pdfa_part(pdf: pymupdf.Document) -> int | None:
    try:
        xmp = pdf.get_xml_metadata() or ""
    except Exception:
        return None
    m = re.search(r"pdfaid:part\s*(?:=\s*[\"']|>)\s*(\d)", xmp)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ immagini
_DEVICE_SPACES = {"/DeviceRGB", "/DeviceGray", "/DeviceCMYK", "/CalRGB", "/CalGray"}


def _key(pdf, xref, key) -> tuple[str, str]:
    try:
        return pdf.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _resolve(pdf, value: tuple[str, str]) -> tuple[str, str]:
    """Risolve un riferimento indiretto restituito da xref_get_key."""
    kind, text = value
    if kind == "xref":
        m = re.match(r"(\d+)\s+\d+\s+R", text)
        if m:
            try:
                obj = pdf.xref_object(int(m.group(1)), compressed=True)
            except Exception:
                return ("null", "null")
            return ("object", obj)
    return value


def _image_display_ppi(pdf: pymupdf.Document, progress, start: float, span: float) -> dict[int, float]:
    """Risoluzione effettiva minima (la visualizzazione più grande) di ogni immagine."""
    result: dict[int, float] = {}
    n = pdf.page_count
    for pno in range(n):
        if pno % 5 == 0:
            check(progress, start + span * pno / max(1, n), _("Analisi delle immagini…"))
        try:
            infos = pdf[pno].get_image_info(xrefs=True)
        except Exception:
            continue
        for info in infos:
            xref = info.get("xref") or 0
            if xref <= 0:
                continue
            w, h = info.get("width") or 0, info.get("height") or 0
            a, b, c, d = (info.get("transform") or (0, 0, 0, 0, 0, 0))[:4]
            dw, dh = math.hypot(a, b) / 72.0, math.hypot(c, d) / 72.0
            if w <= 0 or h <= 0 or dw <= 1e-6 or dh <= 1e-6:
                continue
            ppi = min(w / dw, h / dh)
            result[xref] = min(ppi, result.get(xref, ppi))
    return result


def _smask_xrefs(pdf: pymupdf.Document) -> set[int]:
    masks: set[int] = set()
    for xref in range(1, pdf.xref_length()):
        kind, text = _key(pdf, xref, "SMask")
        if kind == "xref":
            m = re.match(r"(\d+)", text)
            if m:
                masks.add(int(m.group(1)))
    return masks


@dataclass
class _Job:
    xref: int
    mode: str              # "1", "L", "RGB"
    size: tuple[int, int]
    samples: bytes
    old_len: int
    new_size: tuple[int, int] | None
    kind: str              # "mono" | "lossless" | "jpeg"
    keep_cs: str | None    # spazio colore originale da mantenere (ICCBased compatibile)


def _prepare_image(pdf, xref, ppi_map, params: ImageParams, smasks: set[int]) -> _Job | None:
    """Decide se e come ricomprimere l'immagine `xref`; legge i pixel (con il lock)."""
    if _key(pdf, xref, "Subtype")[1] != "/Image":
        return None
    if _key(pdf, xref, "ImageMask")[1] == "true" or xref in smasks:
        return None   # maschere: lasciate intatte (trasparenza invariata)
    if _key(pdf, xref, "Mask")[0] == "array":
        return None   # maschera a chiave di colore: dipende dai valori originali
    ppi = ppi_map.get(xref)
    filt = _key(pdf, xref, "Filter")[1]
    bpc = _key(pdf, xref, "BitsPerComponent")[1]
    cs_kind, cs_text = _resolve(pdf, _key(pdf, xref, "ColorSpace"))
    keep_cs = None
    if cs_kind == "name":
        if cs_text not in _DEVICE_SPACES:
            return None
    elif cs_kind in ("array", "object"):
        head = cs_text.lstrip("[ ").split()[0] if cs_text.strip("[ ") else ""
        if head.startswith("/ICCBased"):
            keep_cs = _key(pdf, xref, "ColorSpace")[1]
        elif not head.startswith("/Indexed") and not head.startswith("/CalRGB") and not head.startswith("/CalGray"):
            return None   # Separation, DeviceN, Lab…: non toccati
    elif "JPX" not in filt:
        return None
    mono = bpc == "1"
    if mono:
        target, threshold = params.mono_ppi, params.mono_threshold
    else:
        target, threshold = params.ppi, params.threshold
    resample = bool(target and threshold and ppi and ppi > threshold)
    lossy_ok = params.quality is not None
    if mono and not resample:
        return None
    if not mono and not resample and not lossy_ok and not params.grayscale:
        return None
    try:
        old_len = len(pdf.xref_stream_raw(xref) or b"")
    except Exception:
        return None
    if old_len < 2048 and not resample and not params.grayscale:
        return None
    try:
        pix = pymupdf.Pixmap(pdf, xref)
    except Exception:
        return None
    try:
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        if pix.n == 4 or (pix.colorspace is not None and pix.colorspace.n == 4):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            keep_cs = None
        if pix.n not in (1, 3):
            return None
        if keep_cs and pix.n != (3 if "RGB" in (pix.colorspace.name if pix.colorspace else "") else pix.n):
            keep_cs = None
        mode = "L" if pix.n == 1 else "RGB"
        if mono:
            mode = "1"
        samples = bytes(pix.samples)
        size = (pix.width, pix.height)
    except Exception:
        return None
    new_size = None
    if resample:
        scale = target / ppi
        new_size = (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))
    kind = "mono" if mono else ("jpeg" if lossy_ok or params.grayscale else "lossless")
    if params.grayscale and mode == "RGB":
        keep_cs = None
    return _Job(xref, mode, size, samples, old_len, new_size, kind, keep_cs)


def _encode(job: _Job, params: ImageParams, already_jpeg: bool) -> tuple[bytes, dict] | None:
    """Ricampiona e codifica l'immagine (senza lock). Restituisce stream e chiavi da impostare."""
    img = Image.frombytes("L" if job.mode == "1" else job.mode, job.size, job.samples)
    if job.new_size and job.new_size != job.size:
        img = img.resize(job.new_size, Image.Resampling.LANCZOS if job.mode != "1" else Image.Resampling.BOX)
    if job.mode == "1":
        img = img.point(lambda v: 255 if v >= 128 else 0).convert("1")
        raw = zlib.compress(img.tobytes(), 9)
        keys = {"Filter": "/FlateDecode", "BitsPerComponent": "1", "ColorSpace": "/DeviceGray"}
        return raw, {**keys, "Width": str(img.width), "Height": str(img.height)}
    if params.grayscale and img.mode == "RGB":
        img = img.convert("L")
    quality = params.quality or 85
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=int(quality), optimize=True)
    raw = buf.getvalue()
    if not job.new_size and not params.grayscale:
        # nessun ricampionamento: si accetta la ricompressione solo se conviene davvero
        limit = 0.75 if already_jpeg else 0.5
        if len(raw) > job.old_len * limit:
            return None
    cs = job.keep_cs or ("/DeviceGray" if img.mode == "L" else "/DeviceRGB")
    keys = {"Filter": "/DCTDecode", "BitsPerComponent": "8", "ColorSpace": cs,
            "Width": str(img.width), "Height": str(img.height)}
    return raw, keys


def _recompress_images(pdf: pymupdf.Document, params: ImageParams, progress, start=0.05, span=0.75) -> int:
    """Ricomprime le immagini di `pdf` secondo `params`. Restituisce quante ne ha cambiate."""
    if params.ppi is None and params.quality is None and params.mono_ppi is None and not params.grayscale:
        return 0
    with ENGINE_LOCK:
        ppi_map = _image_display_ppi(pdf, progress, start, span * 0.2)
        smasks = _smask_xrefs(pdf)
        xrefs = [x for x in range(1, pdf.xref_length()) if _key(pdf, x, "Subtype")[1] == "/Image"]
    changed = 0
    total = max(1, len(xrefs))
    for i, xref in enumerate(xrefs):
        check(progress, start + span * (0.2 + 0.8 * i / total),
              _("Compressione delle immagini ({i} di {n})…").format(i=i + 1, n=len(xrefs)))
        try:
            with ENGINE_LOCK:
                job = _prepare_image(pdf, xref, ppi_map, params, smasks)
                already_jpeg = "DCT" in _key(pdf, xref, "Filter")[1]
            if job is None:
                continue
            encoded = _encode(job, params, already_jpeg)
            if encoded is None:
                continue
            raw, keys = encoded
            if len(raw) >= job.old_len:
                continue   # sarebbe più grande: immagine lasciata com'è
            with ENGINE_LOCK:
                pdf.update_stream(xref, raw, compress=0)
                for k in ("DecodeParms", "Decode", "SMaskInData", "Alternates"):
                    pdf.xref_set_key(xref, k, "null")
                for k, v in keys.items():
                    pdf.xref_set_key(xref, k, v)
            changed += 1
        except OperationCancelled:
            raise
        except Exception:
            continue   # immagine illeggibile: lasciata intatta
    return changed


# ------------------------------------------------------------------ pulizie
_FORBIDDEN_JS = re.compile(r"/S\s*/JavaScript\b")


def _remove_javascript(pdf: pymupdf.Document) -> int:
    """Elimina azioni JavaScript, nomi JavaScript del catalogo e azioni aggiuntive /AA."""
    removed = 0
    cat = pdf.pdf_catalog()
    if _key(pdf, cat, "Names/JavaScript")[0] != "null":
        pdf.xref_set_key(cat, "Names/JavaScript", "null")
        removed += 1
    for xref in range(1, pdf.xref_length()):
        try:
            keys = pdf.xref_get_keys(xref)
        except Exception:
            continue
        for k in ("A", "OpenAction", "AA"):
            if k not in keys:
                continue
            kind, text = _resolve(pdf, _key(pdf, xref, k))
            if k == "AA" or _FORBIDDEN_JS.search(text or "") or "/JS" in (text or ""):
                pdf.xref_set_key(xref, k, "null")
                removed += 1
    return removed


def _remove_thumbnails(pdf) -> None:
    for pno in range(pdf.page_count):
        x = pdf.page_xref(pno)
        if _key(pdf, x, "Thumb")[0] != "null":
            pdf.xref_set_key(x, "Thumb", "null")


def _remove_metadata(pdf) -> None:
    try:
        pdf.del_xml_metadata()
    except Exception:
        pass
    try:
        pdf.set_metadata({})
    except Exception:
        pass
    for xref in range(1, pdf.xref_length()):
        try:
            if "PieceInfo" in pdf.xref_get_keys(xref):
                pdf.xref_set_key(xref, "PieceInfo", "null")
        except Exception:
            continue


def _remove_attachments(pdf) -> None:
    try:
        for name in list(pdf.embfile_names()):
            pdf.embfile_del(name)
    except Exception:
        pass
    cat = pdf.pdf_catalog()
    pdf.xref_set_key(cat, "Names/EmbeddedFiles", "null")
    pdf.xref_set_key(cat, "AF", "null")
    for page in pdf:
        for annot in list(page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]) or []):
            page.delete_annot(annot)


def _remove_annotations(pdf) -> None:
    keep = {pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK}
    for page in pdf:
        annots = [a for a in (page.annots() or []) if a.type[0] not in keep]
        for a in annots:
            page.delete_annot(a)


def _remove_structure(pdf) -> None:
    cat = pdf.pdf_catalog()
    for k in ("StructTreeRoot", "MarkInfo"):
        pdf.xref_set_key(cat, k, "null")
    for pno in range(pdf.page_count):
        pdf.xref_set_key(pdf.page_xref(pno), "StructParents", "null")


def _save(pdf: pymupdf.Document, prot: dict, *, garbage: int = 4, objstms: bool = True) -> bytes:
    opts = dict(garbage=garbage, deflate=True, deflate_images=True, deflate_fonts=True,
                use_objstms=1 if objstms else 0)
    if prot:
        opts.update(prot)
    else:
        opts["encryption"] = pymupdf.PDF_ENCRYPT_NONE
    return pdf.tobytes(**opts)


# ------------------------------------------------------------------ API
def _optimize(data: bytes, params: ImageParams, *, password=None, progress=None, garbage=4,
              actions: dict | None = None, linearize=False, subset_fonts=False) -> bytes:
    actions = actions or {}
    check(progress, 0.0, _("Apertura del documento…"))
    with ENGINE_LOCK:
        pdf, prot = _open(data, password)
    try:
        with ENGINE_LOCK:
            pdfa = _pdfa_part(pdf)
            if actions.get("javascript"):
                _remove_javascript(pdf)
            if actions.get("thumbnails"):
                _remove_thumbnails(pdf)
            if actions.get("metadata"):
                _remove_metadata(pdf)
            if actions.get("attachments"):
                _remove_attachments(pdf)
            if actions.get("annotations"):
                _remove_annotations(pdf)
            if actions.get("bookmarks"):
                pdf.set_toc([])
            if actions.get("structure"):
                _remove_structure(pdf)
        _recompress_images(pdf, params, progress)
        if subset_fonts:
            check(progress, 0.82, _("Riduzione dei font…"))
            with ENGINE_LOCK:
                try:
                    pdf.subset_fonts()
                except Exception:
                    pass
        check(progress, 0.85, _("Scrittura del file ottimizzato…"))
        with ENGINE_LOCK:
            out = _save(pdf, prot, garbage=garbage, objstms=pdfa != 1)
    finally:
        with ENGINE_LOCK:
            pdf.close()
    if linearize:
        check(progress, 0.95, _("Linearizzazione…"))
        out = linearize_bytes(out, password=password if prot else None)
    check(progress, 1.0, _("Fatto"))
    return out


def compress_pdf_bytes(data: bytes, level: str = "medium", password: str | None = None, progress=None,
                       **advanced) -> bytes:
    """Comprime un PDF al livello indicato (CNV-29) e restituisce i nuovi byte.

    Testo, vettori, moduli, segnalibri e link sono preservati; la cifratura del
    file originale è riapplicata al risultato. `advanced` accetta le stesse
    opzioni di `optimize_advanced` (sovrascrivono i parametri del livello).
    """
    p = level_params(level)
    if advanced:
        return optimize_advanced(
            data,
            image_ppi=advanced.pop("image_ppi", p.ppi),
            image_threshold_ppi=advanced.pop("image_threshold_ppi", p.threshold),
            jpeg_quality=advanced.pop("jpeg_quality", p.quality),
            password=password, progress=progress, **advanced)
    return _optimize(data, p, password=password, progress=progress)


def optimize_advanced(data: bytes, *, image_ppi=None, image_threshold_ppi=None, jpeg_quality=None,
                      mono_ppi=None, mono_threshold_ppi=None, grayscale=False, remove_thumbnails=False,
                      remove_metadata=False, remove_javascript=False, remove_attachments=False,
                      remove_annotations=False, remove_bookmarks=False, remove_structure=False, dedup=True,
                      remove_unused=True, subset_fonts=False, linearize=False, password=None,
                      progress=None) -> bytes:
    """Ottimizzazione con controllo dettagliato (CNV-30).

    `image_ppi`: destinazione per immagini a colori e grigi; `image_threshold_ppi`
    (predefinita 1,5 × destinazione): si ricampiona solo sopra questa soglia;
    `jpeg_quality`: qualità JPEG (None = nessuna ricompressione con perdita
    delle immagini non ricampionate, quelle ricampionate usano 85).
    """
    if image_ppi and not image_threshold_ppi:
        image_threshold_ppi = image_ppi * 1.5
    if image_ppi and image_threshold_ppi and image_threshold_ppi < image_ppi:
        raise ConversionError(_("La soglia di ricampionamento deve essere maggiore della risoluzione di "
                                "destinazione."))
    if mono_ppi and not mono_threshold_ppi:
        mono_threshold_ppi = mono_ppi * 1.5
    params = ImageParams(image_ppi or None, image_threshold_ppi if image_ppi else None, jpeg_quality,
                         mono_ppi or None, mono_threshold_ppi if mono_ppi else None, bool(grayscale))
    # livelli di raccolta di MuPDF: 2 = oggetti inutilizzati, 4 = anche stream duplicati;
    # la deduplica implica la raccolta degli inutilizzati, quindi senza di essa non c'è deduplica
    garbage = (4 if dedup else 2) if remove_unused else 0
    actions = {"javascript": remove_javascript, "thumbnails": remove_thumbnails, "metadata": remove_metadata,
               "attachments": remove_attachments, "annotations": remove_annotations,
               "bookmarks": remove_bookmarks, "structure": remove_structure}
    return _optimize(data, params, password=password, progress=progress, garbage=garbage, actions=actions,
                     linearize=linearize, subset_fonts=subset_fonts)


def estimate_sizes(data: bytes, levels=LEVELS, password=None, progress=None,
                   sample_pages: int | None = None) -> dict[str, int]:
    """Dimensione stimata del risultato per ogni livello.

    Per documenti piccoli comprime davvero; per quelli grandi (o se
    `sample_pages` è indicato) comprime un campione di pagine distribuite
    uniformemente ed estrapola in proporzione.
    """
    with ENGINE_LOCK:
        pdf, _prot = _open(data, password)
        n = pdf.page_count
    try:
        if sample_pages is None:
            sample_pages = 0 if (n <= 12 or len(data) <= 8 * 1024 * 1024) else 8
        if sample_pages and sample_pages < n:
            step = n / sample_pages
            pages = sorted({min(n - 1, int(i * step)) for i in range(sample_pages)})
            with ENGINE_LOCK:
                sub = pymupdf.open()
                for p in pages:
                    sub.insert_pdf(pdf, from_page=p, to_page=p)
                sample = sub.tobytes(garbage=1)
                sub.close()
            base = len(sample)
            factor = len(data) / max(1, base)
        else:
            sample, factor = None, 1.0
    finally:
        with ENGINE_LOCK:
            pdf.close()
    result: dict[str, int] = {}
    levels = list(levels)
    for i, level in enumerate(levels):
        sub_progress = _SubProgress(progress, i / len(levels), 1 / len(levels))
        if sample is None:
            result[level] = len(compress_pdf_bytes(data, level, password=password, progress=sub_progress))
        else:
            result[level] = int(len(_optimize(sample, level_params(level), progress=sub_progress)) * factor)
    return result


class _SubProgress:
    """Rimappa l'avanzamento di una sotto-operazione in un intervallo del totale."""

    def __init__(self, progress, start: float, span: float, text: str | None = None):
        self.progress, self.start, self.span, self.text = progress, start, span, text

    def update(self, fraction=None, text=None):
        if self.progress is None:
            return
        f = None if fraction is None else self.start + self.span * max(0.0, min(1.0, fraction))
        self.progress.update(f, self.text or text)


def compress_file(input_path, output_path, level, password=None, progress=None, *,
                  policy: str = "overwrite") -> dict:
    """Comprime un file. Scrive `output_path` solo se il risultato è più piccolo.

    Restituisce `{"before", "after", "reduced", "output"}`; se non c'è riduzione
    `output` è None e nessun file è scritto (l'originale resta intatto).
    """
    try:
        data = Path(input_path).read_bytes()
    except OSError as e:
        raise ConversionError(_("Impossibile leggere «{n}»: {e}").format(
            n=Path(input_path).name, e=e.strerror or e)) from e
    out = compress_pdf_bytes(data, level, password=password, progress=progress)
    before, after = len(data), len(out)
    if after >= before:
        return {"before": before, "after": before, "reduced": False, "output": None}
    with OutputFiles(policy) as files:
        tmp = files.new(output_path)
        Path(tmp).write_bytes(out)
        check(progress, 1.0, None)
        final = files.commit()[0]
    return {"before": before, "after": after, "reduced": True, "output": final}


# ------------------------------------------------------------------ analisi spazio
SPACE_CATEGORIES = ("Immagini", "Font", "Contenuti di pagina", "Annotazioni", "Moduli", "Struttura logica",
                    "Segnalibri", "Metadati", "Allegati", "Miniature", "Altro")

_REF = re.compile(rb"(\d+)\s+0\s+R")


def analyze_space(data: bytes, password=None) -> list[tuple[str, int]]:
    """Byte occupati da ogni categoria di contenuto (CNV-30).

    Le dimensioni degli oggetti sono stimate dalla loro forma serializzata e
    riportate in scala sulla dimensione reale del file; la parte non attribuibile
    (xref, intestazioni, oggetti di servizio) finisce in «Altro». La somma è
    sempre uguale alla dimensione del file.
    """
    with ENGINE_LOCK:
        pdf, _prot = _open(data, password)
        try:
            sizes, cats, total_overhead = _object_sizes_and_categories(pdf)
        finally:
            pdf.close()
    totals = {c: 0 for c in SPACE_CATEGORIES}
    for xref, size in sizes.items():
        totals[cats.get(xref, "Altro")] += size
    file_len = len(data)
    available = max(0, file_len - total_overhead)
    used = sum(totals.values())
    if used > available and used > 0:
        scale = available / used
        totals = {k: int(v * scale) for k, v in totals.items()}
    totals["Altro"] += file_len - sum(totals.values())
    return [(_(c), totals[c]) for c in SPACE_CATEGORIES]


def _object_sizes_and_categories(pdf: pymupdf.Document):
    n = pdf.xref_length()
    sizes: dict[int, int] = {}
    refs: dict[int, list[int]] = {}
    types: dict[int, str] = {}
    overhead = 0
    for xref in range(1, n):
        try:
            obj = pdf.xref_object(xref, compressed=True)
        except Exception:
            continue
        t = _key(pdf, xref, "Type")[1]
        types[xref] = t
        size = len(obj) + 20
        if pdf.xref_is_stream(xref):
            length = _key(pdf, xref, "Length")
            try:
                size += int(_resolve(pdf, length)[1].strip()) if length[0] in ("int", "xref") else 0
            except ValueError:
                try:
                    size += len(pdf.xref_stream_raw(xref) or b"")
                except Exception:
                    pass
        if t in ("/ObjStm", "/XRef"):
            overhead += size if t == "/XRef" else 0
            continue
        sizes[xref] = size
        refs[xref] = [int(m) for m in _REF.findall(obj.encode("latin-1", "replace"))]
    cats: dict[int, str] = {}
    stop = {"/Page", "/Pages", "/Catalog"}

    def claim(roots, cat):
        stack = list(roots)
        while stack:
            x = stack.pop()
            if x in cats or x not in sizes or types.get(x) in stop:
                continue
            cats[x] = cat
            stack.extend(refs.get(x, ()))

    cat_x = pdf.pdf_catalog()
    page_x = [pdf.page_xref(i) for i in range(pdf.page_count)]

    def key_refs(xref, key):
        kind, text = _key(pdf, xref, key)
        return [int(m) for m in _REF.findall(text.encode("latin-1", "replace"))] if kind != "null" else []

    thumbs = [r for p in page_x for r in key_refs(p, "Thumb")]
    claim(thumbs, "Miniature")
    images = [x for x in sizes if _key(pdf, x, "Subtype")[1] == "/Image"]
    claim(images, "Immagini")
    fonts = [x for x in sizes if types.get(x) in ("/Font", "/FontDescriptor")]
    claim(fonts, "Font")
    contents = [r for p in page_x for r in key_refs(p, "Contents")]
    forms = [x for x in sizes if _key(pdf, x, "Subtype")[1] == "/Form" and x not in cats]
    widgets, others = [], []
    for p in page_x:
        for r in key_refs(p, "Annots"):
            (widgets if _key(pdf, r, "Subtype")[1] == "/Widget" else others).append(r)
    claim(widgets + key_refs(cat_x, "AcroForm"), "Moduli")
    claim(others, "Annotazioni")
    claim(contents + forms, "Contenuti di pagina")
    claim(key_refs(cat_x, "Outlines"), "Segnalibri")
    claim(key_refs(cat_x, "StructTreeRoot"), "Struttura logica")
    meta = key_refs(cat_x, "Metadata") + key_refs(-1, "Info")
    meta += [x for x in sizes if types.get(x) == "/Metadata"]
    claim(meta, "Metadati")
    att = [x for x in sizes if types.get(x) in ("/EmbeddedFile", "/Filespec")]
    claim(key_refs(cat_x, "Names/EmbeddedFiles") + att, "Allegati")
    return sizes, cats, overhead


# ------------------------------------------------------------------ linearizzazione
def is_linearized(data: bytes) -> bool:
    """True se il file inizia con un dizionario di linearizzazione valido per la sua lunghezza."""
    head = bytes(data[:2048])
    m = re.search(rb"/Linearized\s+[\d.]+", head)
    if not m:
        return False
    end = head.find(b">>", m.end())
    dict_text = head[m.start(): end if end > 0 else len(head)]
    lm = re.search(rb"/L\s+(\d+)", dict_text)
    return bool(lm and int(lm.group(1)) == len(data))


def linearize_bytes(data: bytes, password: str | None = None, progress=None) -> bytes:
    """Riscrive il PDF in forma linearizzata (visualizzazione web veloce, CNV-31) con qpdf.

    La cifratura eventuale è mantenuta (serve la password se il file la richiede).
    """
    exe = shutil.which("qpdf")
    if not exe:
        raise ConversionError(_("Per ottimizzare il PDF per la visualizzazione web veloce serve il programma "
                                "qpdf, che non è installato. Installalo (ad esempio con «sudo apt install "
                                "qpdf») e riprova."))
    with tempfile.TemporaryDirectory(prefix="foliant-lin-") as tmp:
        src, dst = os.path.join(tmp, "in.pdf"), os.path.join(tmp, "out.pdf")
        Path(src).write_bytes(data)
        cmd = [exe, "--linearize", "--warning-exit-0"]
        if password:
            cmd.append(f"--password={password}")
        cmd += [src, dst]
        rc, err = _run(cmd, progress, timeout=600)
        if rc != 0 or not os.path.exists(dst):
            raise ConversionError(_("La linearizzazione non è riuscita: {e}").format(
                e=(err.strip().splitlines() or [_("errore sconosciuto")])[-1]))
        return Path(dst).read_bytes()


def _run(cmd: list[str], progress, timeout: float, fraction: float | None = None) -> tuple[int, str]:
    """Esegue un programma esterno controllando l'annullamento; termina il processo se annullato."""
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                check(progress, fraction, None)
                if time.monotonic() - start > timeout:
                    raise ConversionError(_("L'operazione ha impiegato troppo tempo ed è stata interrotta."))
                time.sleep(0.05)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        errf.seek(0)
        return proc.returncode, errf.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ riparazione
_TYPE_LABELS = {
    "jpeg": "un'immagine JPEG", "png": "un'immagine PNG", "gif": "un'immagine GIF", "tiff": "un'immagine TIFF",
    "bmp": "un'immagine BMP", "webp": "un'immagine WebP", "zip/docx": "un documento Word (DOCX)",
    "zip/xlsx": "un foglio di calcolo Excel (XLSX)", "zip/pptx": "una presentazione PowerPoint (PPTX)",
    "zip/odt": "un documento OpenDocument", "zip": "un archivio ZIP", "doc": "un documento Office (formato "
    "precedente al 2007)", "rtf": "un documento RTF", "html": "una pagina HTML", "xps": "un documento XPS",
    "ps": "un file PostScript", "text": "un file di testo",
}


def detect_file_type(data: bytes) -> str | None:
    """Riconosce il tipo reale di un file dai primi byte.

    Restituisce "pdf", "jpeg", "png", "gif", "tiff", "bmp", "webp", "zip/docx",
    "zip/xlsx", "zip/pptx", "zip/odt", "zip", "doc", "rtf", "html", "ps",
    "text", "unknown"; None per dati vuoti.
    """
    if not data:
        return None
    head = bytes(data[:1024])
    if b"%PDF-" in head:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"BM" and len(head) > 14:
        return "bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"PK\x03\x04":
        blob = bytes(data[:65536])
        if b"word/" in blob:
            return "zip/docx"
        if b"xl/" in blob:
            return "zip/xlsx"
        if b"ppt/" in blob:
            return "zip/pptx"
        if b"opendocument" in blob:
            return "zip/odt"
        if b"[Content_Types].xml" in blob and b"Documents/" in blob:
            return "xps"
        return "zip"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "doc"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"%!PS"):
        return "ps"
    low = head.lstrip().lower()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html"):
        return "html"
    if b"obj" in head and b"endobj" in bytes(data[:65536]):
        return "pdf"   # PDF senza intestazione: si tenta comunque la ricostruzione
    try:
        head.decode("utf-8")
        if all(b >= 9 for b in head[:256]):
            return "text"
    except UnicodeDecodeError:
        pass
    return "unknown"


def _not_pdf_message(kind: str | None) -> str:
    label = _TYPE_LABELS.get(kind or "", None)
    if label is None:
        return _("Il file non è un PDF e il suo formato non è stato riconosciuto: non può essere riparato.")
    return _("Il file non è un PDF ma {t} con estensione sbagliata. Puoi creare un PDF a partire da questo "
             "file con «Crea › Da file».").format(t=_(label))


def _expected_pages(data: bytes) -> int:
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)]
    counts += [int(m) for m in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", data)]
    leaves = len(re.findall(rb"/Type\s*/Page\b(?!s)", data))
    return max(counts + [leaves, 0])


def _page_ok(pdf: pymupdf.Document, pno: int) -> bool:
    """True se gli oggetti della pagina sono leggibili (oggetto pagina e flussi di contenuto)."""
    try:
        xref = pdf.page_xref(pno)
        if xref <= 0 or pdf.xref_object(xref, compressed=True).strip() in ("null", ""):
            return False
        page = pdf[pno]
        for c in page.get_contents():
            if not pdf.xref_is_stream(c):
                return False
            pdf.xref_stream(c)
        page.get_text("text")
        return True
    except Exception:
        return False


def repair_pdf_bytes(data: bytes, password=None, progress=None) -> tuple[bytes, dict]:
    """Ricostruisce un PDF danneggiato (CNV-32) e restituisce (byte riparati, resoconto).

    Il resoconto ha le chiavi `repaired` (il motore ha dovuto ricostruire la
    struttura), `pages` (pagine recuperate), `lost_pages` (numeri, base 1, delle
    pagine perse) e `warnings` (messaggi del motore).
    """
    if not data:
        raise ConversionError(_("Il file è vuoto: non c'è nulla da riparare."))
    kind = detect_file_type(data)
    if kind != "pdf":
        err = ConversionError(_not_pdf_message(kind))
        err.detected_type = kind
        raise err
    check(progress, 0.05, _("Analisi della struttura del file…"))
    warnings: list[str] = []
    start = bytes(data).find(b"%PDF-")
    with ENGINE_LOCK:
        pymupdf.TOOLS.mupdf_warnings(reset=True)
        try:
            pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
        except Exception as e:
            raise ConversionError(_("Il file è danneggiato in modo irreparabile: non è stato possibile "
                                    "ricostruirne la struttura.")) from e
        try:
            if pdf.needs_pass:
                if not password:
                    raise PasswordRequired()
                code = pdf.authenticate(password)
                if not code:
                    raise WrongPassword()
                prot = protection_options(pdf, password, code, True)
            else:
                prot = {}
            repaired = bool(getattr(pdf, "is_repaired", False)) or start > 0
            n = pdf.page_count
            expected = _expected_pages(bytes(data))
            good, lost = [], []
            for pno in range(n):
                if pno % 10 == 0:
                    check(progress, 0.1 + 0.6 * pno / max(1, n), _("Verifica delle pagine…"))
                (good if _page_ok(pdf, pno) else lost).append(pno)
            if not good:
                raise ConversionError(_("Nessuna pagina è recuperabile: il file è danneggiato in modo "
                                        "irreparabile."))
            if lost:
                pdf.select(good)
                repaired = True
            lost_numbers = [p + 1 for p in lost]
            if expected > n:
                repaired = True
                lost_numbers += list(range(n + 1, expected + 1))
            check(progress, 0.8, _("Scrittura della copia riparata…"))
            opts = dict(garbage=3, clean=False, deflate=True)
            opts.update(prot or {"encryption": pymupdf.PDF_ENCRYPT_NONE})
            out = pdf.tobytes(**opts)
            text = pymupdf.TOOLS.mupdf_warnings(reset=True) or ""
            warnings = [w for w in dict.fromkeys(text.splitlines()) if w.strip()]
            pages = pdf.page_count
        finally:
            pdf.close()
    if repaired:
        warnings.insert(0, _("La struttura del file (tabella dei riferimenti incrociati) era danneggiata ed è "
                             "stata ricostruita."))
    if start > 0:
        warnings.insert(0, _("Byte estranei prima dell'intestazione PDF rimossi."))
    if lost_numbers:
        warnings.append(_("Pagine perse: {p}.").format(p=", ".join(map(str, lost_numbers))))
    check(progress, 1.0, _("Fatto"))
    return out, {"repaired": repaired or bool(warnings), "pages": pages, "lost_pages": lost_numbers,
                 "warnings": warnings}


def repair_file(input_path, output_path=None, password=None, progress=None, *,
                policy: str = "rename") -> tuple[str, dict]:
    """Ripara un file e scrive la copia (predefinito «<nome>_riparato.pdf» accanto all'originale)."""
    src = Path(input_path)
    try:
        data = src.read_bytes()
    except OSError as e:
        raise ConversionError(_("Impossibile leggere «{n}»: {e}").format(n=src.name, e=e.strerror or e)) from e
    out, report = repair_pdf_bytes(data, password=password, progress=progress)
    target = Path(output_path) if output_path else src.with_name(f"{src.stem}_riparato.pdf")
    with OutputFiles(policy) as files:
        tmp = files.new(target)
        Path(tmp).write_bytes(out)
        final = files.commit()[0]
    return final, report


def previous_revisions(data: bytes) -> list[bytes]:
    """Revisioni incrementali precedenti integre, dalla più vecchia alla più recente.

    Il file è tagliato a ogni marcatore `%%EOF`; ogni prefisso che il motore apre
    senza riparazioni e con almeno una pagina è una revisione valida. L'ultima
    revisione completa del file (se coincide con i dati) non è inclusa.
    """
    data = bytes(data)
    result: list[bytes] = []
    for m in re.finditer(rb"%%EOF[ \t]*(\r\n|\r|\n)?", data):
        cut = data[: m.end()]
        if len(data[m.end():].strip()) == 0:
            continue   # è il file intero
        with ENGINE_LOCK:
            try:
                doc = pymupdf.open(stream=cut, filetype="pdf")
            except Exception:
                continue
            try:
                ok = doc.page_count > 0 and not getattr(doc, "is_repaired", False)
            except Exception:
                ok = False
            finally:
                doc.close()
        if ok:
            result.append(cut)
    return result


__all__ = ["LEVELS", "LEVEL_LABELS", "SPACE_CATEGORIES", "compress_pdf_bytes", "estimate_sizes", "compress_file",
           "analyze_space", "optimize_advanced", "linearize_bytes", "is_linearized", "detect_file_type",
           "repair_pdf_bytes", "repair_file", "previous_revisions", "level_params"]
