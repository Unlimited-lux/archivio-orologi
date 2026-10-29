"""Conversione in PDF/X-3 (CNV-15) con Ghostscript, senza interfaccia.

Ghostscript produce PDF/X-3 (colori convertiti in CMYK con il profilo della
condizione di stampa). PDF/X-1a, X-4 e PDF/E non sono disponibili con gli
strumenti installati.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _
from foliant.core import platform

from .common import ConversionError, check
from .pdfa import _open, _run_gs, _scan, ghostscript

PDFX_LEVELS = ("3",)


def find_cmyk_profile() -> str | None:
    from .pdfa import ghostscript_icc_dir
    gs_icc = ghostscript_icc_dir() if platform.IS_WINDOWS else None
    for path in (*([os.path.join(gs_icc, "default_cmyk.icc"), os.path.join(gs_icc, "ps_cmyk.icc")] if gs_icc else []),"/usr/share/color/icc/ghostscript/default_cmyk.icc", "/usr/share/color/icc/ghostscript/ps_cmyk.icc",
                 *sorted(str(p) for p in Path("/usr/share/ghostscript").glob("*/iccprofiles/default_cmyk.icc"))):
        if os.path.isfile(path):
            return path
    return None


def _ps(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _pdfx_def(icc: str, title: str) -> str:
    """Definizione dell'OutputIntent PDF/X (pdfmark documentati da Ghostscript)."""
    return f"""%!
[ /GTS_PDFXVersion (PDF/X-3:2002) /Title {_ps(title)} /Trapped /False /DOCINFO pdfmark
[/_objdef {{icc_PDFX}} /type /stream /OBJ pdfmark
[{{icc_PDFX}} << /N 4 >> /PUT pdfmark
[{{icc_PDFX}} {_ps(icc)} (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFX}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFX}} <<
  /Type /OutputIntent /S /GTS_PDFX
  /OutputCondition (Stampa commerciale) /Info (CMYK) /OutputConditionIdentifier (Custom)
  /RegistryName (http://www.color.org) /DestOutputProfile {{icc_PDFX}}
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFX}} ]>> /PUT pdfmark
"""


def _boxes_and_info(data: bytes) -> tuple[bytes, list[str]]:
    """TrimBox su ogni pagina (uguale al CropBox se mancante) e GTS_PDFXVersion nel dizionario Info."""
    fixes: list[str] = []
    with ENGINE_LOCK:
        pdf = pymupdf.open(stream=data, filetype="pdf")
        try:
            missing = 0
            for page in pdf:
                if pdf.xref_get_key(page.xref, "TrimBox")[0] == "null" and \
                        pdf.xref_get_key(page.xref, "ArtBox")[0] == "null":
                    page.set_trimbox(page.cropbox)
                    missing += 1
            if missing:
                fixes.append(_("TrimBox impostato uguale al CropBox su {n} pagine.").format(n=missing))
            out = pdf.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            pdf.close()
    return out, fixes


def verify_pdfx(data: bytes) -> list[str]:
    """Requisiti principali di PDF/X-3 (non sostituisce un validatore completo)."""
    errors: list[str] = []
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
        except Exception:
            return [_("Il file non è un PDF valido.")]
        try:
            if pdf.needs_pass or (pdf.metadata or {}).get("encryption"):
                return [_("Il documento è cifrato (/Encrypt non ammesso).")]
            f = _scan(pdf, data)
            trailer_info = pdf.xref_get_key(-1, "Info")
            version = ""
            if trailer_info[0] == "xref":
                version = pdf.xref_get_key(int(trailer_info[1].split()[0]), "GTS_PDFXVersion")[1]
            intents = pdf.xref_object(pdf.pdf_catalog())
            oi = pdf.xref_get_key(pdf.pdf_catalog(), "OutputIntents")[1]
            refs = [int(x) for x in re.findall(r"(\d+)\s+0\s+R", oi)]
            gts = any(pdf.xref_get_key(x, "S")[1] == "/GTS_PDFX" and pdf.xref_get_key(x, "DestOutputProfile")[0] != "null"
                      for x in refs) or "/GTS_PDFX" in oi
            no_box = [p.number + 1 for p in pdf if pdf.xref_get_key(p.xref, "TrimBox")[0] == "null"
                      and pdf.xref_get_key(p.xref, "ArtBox")[0] == "null"]
            _ = intents
        finally:
            pdf.close()
    if "PDF/X-3" not in version:
        errors.append(_("Manca GTS_PDFXVersion «PDF/X-3» nel dizionario Info."))
    if not gts:
        errors.append(_("Manca l'OutputIntent /GTS_PDFX con il profilo della condizione di stampa."))
    if f["fonts_missing"]:
        errors.append(_("Font non incorporati: {f}.").format(f=", ".join(f["fonts_missing"][:10])))
    if f["transparency"]:
        errors.append(_("Trasparenze non ammesse in PDF/X-3."))
    if no_box:
        errors.append(_("Pagine senza TrimBox: {p}.").format(p=", ".join(map(str, no_box[:20]))))
    return errors


def convert_to_pdfx(data: bytes, level: str = "3", password=None, progress=None) -> tuple[bytes, dict]:
    """Converte in PDF/X-3 e restituisce (byte, {"level", "fixes", "errors", "compliant"})."""
    if str(level).lower().replace("pdf/x-", "").replace("pdfx-", "") not in PDFX_LEVELS:
        raise ConversionError(_("Profilo PDF/X non supportato: «{l}». È disponibile PDF/X-3.").format(l=level))
    gs = ghostscript()
    if not gs and platform.IS_WINDOWS:
        raise ConversionError(platform.missing_tool_message("gs"))
    if not gs:
        raise ConversionError(_("La conversione in PDF/X richiede Ghostscript, che non è installato. "
                                "Installalo (ad esempio con «sudo apt install ghostscript») e riprova."))
    icc = find_cmyk_profile()
    if not icc:
        raise ConversionError(_("Non è stato trovato un profilo colore CMYK per la condizione di stampa."))
    check(progress, 0.0, _("Preparazione del documento…"))
    with ENGINE_LOCK:
        pdf = _open(data, password)
        try:
            title = (pdf.metadata or {}).get("title") or "Documento"
            pages = pdf.page_count
            prepared = pdf.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            pdf.close()
    prepared, fixes = _boxes_and_info(prepared)
    fixes.append(_("Colori convertiti in CMYK con il profilo della condizione di stampa: i colori possono variare."))
    tmpdir = tempfile.mkdtemp(prefix="foliant-pdfx-")
    try:
        src, dst = os.path.join(tmpdir, "in.pdf"), os.path.join(tmpdir, "out.pdf")
        defs, icc_copy = os.path.join(tmpdir, "pdfx_def.ps"), os.path.join(tmpdir, "cmyk.icc")
        shutil.copyfile(icc, icc_copy)
        Path(src).write_bytes(prepared)
        Path(defs).write_text(_pdfx_def(icc_copy, title), encoding="latin-1", errors="replace")
        cmd = [gs, "-dPDFX", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-sDEVICE=pdfwrite",
               "-sColorConversionStrategy=CMYK", "-sProcessColorModel=DeviceCMYK", "-dEmbedAllFonts=true",
               "-dSubsetFonts=true", "-dAutoRotatePages=/None", "-dCompatibilityLevel=1.4",
               f"--permit-file-read={tmpdir}{os.sep}", f"-sOutputFile={dst}", defs, src]
        rc = _run_gs(cmd, progress, pages, os.path.join(tmpdir, "gs.log"), timeout=max(300, pages * 30))
        if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
            try:
                log = Path(tmpdir, "gs.log").read_text("utf-8", "replace").strip().splitlines()
            except OSError:
                log = []
            detail = next((ln for ln in reversed(log) if "rror" in ln), log[-1] if log else "")
            raise ConversionError(_("Ghostscript non è riuscito a convertire il documento in PDF/X. {d}").format(
                d=detail).strip())
        out = Path(dst).read_bytes()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    out, more = _boxes_and_info(out)
    fixes += more
    check(progress, 0.95, _("Verifica della conformità…"))
    errors = verify_pdfx(out)
    return out, {"level": "X-3", "fixes": fixes, "errors": errors, "compliant": not errors}
