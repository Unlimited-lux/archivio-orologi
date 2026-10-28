"""Conversione in PDF/A-1b, 2b e 3b e verifica dei requisiti principali (CNV-14),
rimozione della dichiarazione di conformità (CNV-15). Solo logica, niente Qt.

La riscrittura vera e propria è affidata a Ghostscript (`pdfwrite` in modalità
PDF/A), che incorpora tutti i font (anche i 14 standard), aggiunge l'OutputIntent
con il profilo sRGB, scrive i metadati XMP e, per il livello 1, appiattisce le
trasparenze. Prima di Ghostscript il documento è decifrato e ripulito con il
motore (JavaScript, azioni vietate, XFA, flag delle annotazioni); dopo, il
risultato è controllato e, se serve, completato (identificazione `pdfaid`,
allegati del livello 3b).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, PasswordRequired, WrongPassword, _

from .common import ConversionError, OutputFiles, check

PDFA_LEVELS = ("1b", "2b", "3b", "2u", "3u")

FORBIDDEN_ACTIONS = ("Launch", "Sound", "Movie", "ResetForm", "ImportData", "JavaScript", "Hide",
                     "SetOCGState", "Rendition", "Trans", "GoTo3DView")
_FORBIDDEN_RE = re.compile(r"/S\s*/(" + "|".join(FORBIDDEN_ACTIONS) + r")\b")
_REF_RE = re.compile(r"(\d+)\s+0\s+R")


# ------------------------------------------------------------------ utilità
def _level(level: str) -> tuple[int, str]:
    lv = (level or "").lower().replace("pdf/a-", "").replace("pdfa", "").strip("- ")
    if lv not in PDFA_LEVELS:
        raise ConversionError(_("Livello PDF/A non supportato: «{l}». Scegli tra 1b, 2b, 3b, 2u e 3u.").format(l=level))
    return int(lv[0]), lv[1].upper()


def _key(pdf, xref, key) -> tuple[str, str]:
    try:
        return pdf.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _refs(text: str) -> list[int]:
    return [int(m) for m in _REF_RE.findall(text or "")]


def _obj(pdf, xref) -> str:
    try:
        return pdf.xref_object(xref, compressed=True)
    except Exception:
        return ""


def _value_text(pdf, value: tuple[str, str]) -> str:
    kind, text = value
    if kind == "xref":
        r = _refs(text)
        return _obj(pdf, r[0]) if r else ""
    return text if kind != "null" else ""


def _open(data: bytes, password: str | None) -> pymupdf.Document:
    """Apre i byte (chiamare con ENGINE_LOCK) autenticando con la password se serve."""
    if not data:
        raise ConversionError(_("Il file è vuoto."))
    try:
        pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
    except Exception as e:
        raise ConversionError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
    if pdf.needs_pass:
        if not password:
            pdf.close()
            raise PasswordRequired()
        if not pdf.authenticate(password):
            pdf.close()
            raise WrongPassword()
    if pdf.page_count == 0:
        pdf.close()
        raise ConversionError(_("Il documento non contiene pagine."))
    return pdf


def _font_embedded(pdf, xref) -> bool:
    sub = _key(pdf, xref, "Subtype")[1]
    if sub == "/Type3":
        return True
    target = xref
    if sub == "/Type0":
        kind, text = _key(pdf, xref, "DescendantFonts")
        if kind == "xref":
            text = _obj(pdf, _refs(text)[0]) if _refs(text) else ""
        refs = _refs(text)
        if not refs:
            return False
        target = refs[0]
    for ff in ("FontFile", "FontFile2", "FontFile3"):
        if _key(pdf, target, f"FontDescriptor/{ff}")[0] != "null":
            return True
    return False


def _font_name(pdf, xref) -> str:
    return _key(pdf, xref, "BaseFont")[1].lstrip("/") or f"#{xref}"


def _xmp(pdf) -> str:
    try:
        return pdf.get_xml_metadata() or ""
    except Exception:
        return ""


def _pdfaid(xmp: str) -> tuple[str | None, str | None]:
    part = re.search(r"pdfaid:part\s*(?:=\s*[\"']\s*|>\s*)(\d+)", xmp)
    conf = re.search(r"pdfaid:conformance\s*(?:=\s*[\"']\s*|>\s*)([A-Za-z])", xmp)
    return (part.group(1) if part else None, conf.group(1).upper() if conf else None)


def _scan(pdf: pymupdf.Document, data: bytes) -> dict:
    """Raccoglie i fatti rilevanti per PDF/A in un solo passaggio sugli oggetti."""
    facts = {"fonts_missing": [], "javascript": False, "actions": set(), "transparency": False,
             "lzw": False, "jpx": False, "xfa": False, "attachments": 0, "objstm": False,
             "annot_flags": 0, "reference_xobj": False}
    cat = pdf.pdf_catalog()
    if _key(pdf, cat, "Names/JavaScript")[0] != "null":
        facts["javascript"] = True
    if _key(pdf, cat, "AcroForm/XFA")[0] != "null":
        facts["xfa"] = True
    try:
        facts["attachments"] = pdf.embfile_count()
    except Exception:
        pass
    seen_fonts: set[str] = set()
    for xref in range(1, pdf.xref_length()):
        text = _obj(pdf, xref)
        if not text:
            continue
        t = _key(pdf, xref, "Type")[1]
        sub = _key(pdf, xref, "Subtype")[1]
        if t == "/Font" and sub not in ("/CIDFontType0", "/CIDFontType2"):
            if not _font_embedded(pdf, xref):
                name = _font_name(pdf, xref)
                if name not in seen_fonts:
                    seen_fonts.add(name)
                    facts["fonts_missing"].append(name)
        if t in ("/ObjStm", "/XRef"):
            facts["objstm"] = True
        if re.search(r"/JS(?![A-Za-z0-9])", text) or re.search(r"/S\s*/JavaScript\b", text):
            facts["javascript"] = True
        for m in _FORBIDDEN_RE.finditer(text):
            facts["actions"].add(m.group(1))
        if "/AA" in text and re.search(r"/AA\s*(<<|\d+\s+0\s+R)", text):
            facts["actions"].add("AA")
        if re.search(r"/SMask\s*(\d+\s+0\s+R|<<)", text) or re.search(r"/S\s*/Transparency\b", text):
            facts["transparency"] = True
        for m in re.finditer(r"/(?:CA|ca)\s+([0-9.]+)", text):
            try:
                if float(m.group(1)) < 1.0:
                    facts["transparency"] = True
            except ValueError:
                pass
        if re.search(r"/BM\s*/(?!Normal\b|Compatible\b)\w+", text):
            facts["transparency"] = True
        filt = _key(pdf, xref, "Filter")[1]
        if "LZW" in filt:
            facts["lzw"] = True
        if "JPX" in filt:
            facts["jpx"] = True
        if sub == "/Form" and _key(pdf, xref, "Ref")[0] != "null":
            facts["reference_xobj"] = True
        if t == "/Annot" or _key(pdf, xref, "Rect")[0] == "array" and sub in (
                "/Link", "/Widget", "/Text", "/Highlight", "/FreeText", "/Square", "/Circle", "/Ink", "/Stamp",
                "/Line", "/Polygon", "/PolyLine", "/Underline", "/StrikeOut", "/Squiggly", "/Caret",
                "/FileAttachment"):
            if sub != "/Popup":
                try:
                    flags = int(_key(pdf, xref, "F")[1])
                except ValueError:
                    flags = 0
                if not flags & 4 or flags & (1 | 2 | 32):
                    facts["annot_flags"] += 1
    facts["encrypted"] = _key(pdf, -1, "Encrypt")[0] != "null" or bool((pdf.metadata or {}).get("encryption"))
    facts["id"] = _key(pdf, -1, "ID")[0] != "null"
    oi = _value_text(pdf, _key(pdf, cat, "OutputIntents"))
    profile = False
    for r in _refs(oi):
        if _key(pdf, r, "DestOutputProfile")[0] != "null":
            profile = True
    if "/DestOutputProfile" in oi:
        profile = True
    facts["output_intent"] = profile
    facts["xmp"] = _xmp(pdf)
    meta = _key(pdf, cat, "Metadata")
    facts["xmp_filtered"] = bool(meta[0] == "xref" and _refs(meta[1]) and
                                 _key(pdf, _refs(meta[1])[0], "Filter")[0] != "null")
    m = re.match(rb"\s*%PDF-(\d\.\d)", bytes(data[:1024]).lstrip(b"\x00"))
    facts["version"] = m.group(1).decode() if m else None
    return facts


# ------------------------------------------------------------------ analisi e verifica
def analyze_pdfa(data: bytes, password=None, level: str = "2b") -> list[str]:
    """Problemi da correggere per la conversione in PDF/A, ciascuno con la correzione prevista."""
    part, _conf = _level(level)
    with ENGINE_LOCK:
        pdf = _open(data, password)
        try:
            f = _scan(pdf, data)
        finally:
            pdf.close()
    problems: list[str] = []
    if f["encrypted"]:
        problems.append(_("Il documento è cifrato: la cifratura sarà rimossa."))
    if f["fonts_missing"]:
        problems.append(_("Font non incorporati ({f}): saranno incorporati (con un sostituto metricamente "
                          "compatibile se il font non è disponibile).").format(f=", ".join(f["fonts_missing"][:10])))
    if f["javascript"]:
        problems.append(_("Il documento contiene JavaScript: sarà rimosso."))
    actions = sorted(f["actions"] - {"JavaScript"})
    if actions:
        problems.append(_("Azioni non ammesse ({a}): saranno rimosse.").format(a=", ".join(actions)))
    if f["xfa"]:
        problems.append(_("Modulo XFA: sarà rimosso (resta il modulo standard, se presente)."))
    if part == 1 and f["transparency"]:
        problems.append(_("Trasparenze non ammesse in PDF/A-1: saranno appiattite."))
    if part == 1 and f["jpx"]:
        problems.append(_("Immagini JPEG 2000 non ammesse in PDF/A-1: saranno ricodificate."))
    if f["lzw"]:
        problems.append(_("Compressione LZW non ammessa: sarà sostituita con Flate."))
    if f["reference_xobj"]:
        problems.append(_("Contenuti esterni (Reference XObject): saranno rimossi."))
    if f["attachments"]:
        if part == 1:
            problems.append(_("Allegati non ammessi in PDF/A-1: saranno rimossi."))
        elif part == 2:
            problems.append(_("In PDF/A-2 sono ammessi solo allegati PDF/A: gli altri saranno rimossi."))
        else:
            problems.append(_("Allegati: saranno dichiarati come file associati (/AF, /AFRelationship)."))
    if f["annot_flags"]:
        problems.append(_("{n} annotazioni non stampabili o nascoste: i flag saranno corretti.").format(
            n=f["annot_flags"]))
    if not f["output_intent"]:
        problems.append(_("Manca l'OutputIntent con profilo colore: sarà aggiunto il profilo sRGB."))
    part_found, _c = _pdfaid(f["xmp"])
    if not f["xmp"]:
        problems.append(_("Mancano i metadati XMP: saranno creati con l'identificazione PDF/A."))
    elif part_found is None:
        problems.append(_("I metadati XMP non contengono l'identificazione PDF/A: sarà aggiunta."))
    if not f["id"]:
        problems.append(_("Manca l'identificativo /ID nel trailer: sarà aggiunto."))
    return problems


def verify_pdfa(data: bytes, level: str) -> list[str]:
    """Controllo dei requisiti principali di PDF/A (non sostituisce un validatore completo).

    Restituisce l'elenco degli errori; lista vuota = conforme per i controlli eseguiti.
    """
    part, conf = _level(level)
    errors: list[str] = []
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
        except Exception:
            return [_("Il file non è un PDF valido.")]
        try:
            if pdf.needs_pass or pdf.is_encrypted:
                return [_("Il documento è cifrato (/Encrypt non ammesso).")]
            f = _scan(pdf, data)
            repaired = bool(getattr(pdf, "is_repaired", False))
        finally:
            pdf.close()
    if repaired:
        errors.append(_("La struttura del file (xref) non è valida."))
    if f["encrypted"]:
        errors.append(_("Il documento è cifrato (/Encrypt non ammesso)."))
    if f["fonts_missing"]:
        errors.append(_("Font non incorporati: {f}.").format(f=", ".join(f["fonts_missing"][:10])))
    if f["javascript"]:
        errors.append(_("Il documento contiene JavaScript."))
    actions = sorted(f["actions"] - {"JavaScript"})
    if actions:
        errors.append(_("Azioni non ammesse: {a}.").format(a=", ".join(actions)))
    if f["xfa"]:
        errors.append(_("Il documento contiene un modulo XFA."))
    if f["lzw"]:
        errors.append(_("Compressione LZW non ammessa."))
    if f["reference_xobj"]:
        errors.append(_("Contenuti esterni (Reference XObject) non ammessi."))
    if f["annot_flags"]:
        errors.append(_("{n} annotazioni senza flag di stampa o nascoste.").format(n=f["annot_flags"]))
    if not f["output_intent"]:
        errors.append(_("Manca /OutputIntents con un profilo ICC."))
    if not f["id"]:
        errors.append(_("Manca /ID nel trailer."))
    if not f["xmp"]:
        errors.append(_("Mancano i metadati XMP."))
    else:
        p, c = _pdfaid(f["xmp"])
        if p != str(part) or c != conf:
            errors.append(_("I metadati XMP non dichiarano PDF/A-{l} (trovato: {f}).").format(
                l=f"{part}{conf.lower()}", f=f"{p or '-'}{(c or '-').lower()}"))
    if conf == "U":
        errors.extend(_unicode_problems(data))
    if part == 1:
        if f["transparency"]:
            errors.append(_("Trasparenze non ammesse in PDF/A-1."))
        if f["jpx"]:
            errors.append(_("Immagini JPEG 2000 non ammesse in PDF/A-1."))
        if f["objstm"]:
            errors.append(_("Flussi di oggetti o xref compresse non ammessi in PDF/A-1."))
        if f["attachments"]:
            errors.append(_("Allegati non ammessi in PDF/A-1."))
        if f["xmp_filtered"]:
            errors.append(_("Il flusso dei metadati XMP non deve essere compresso in PDF/A-1."))
        if f["version"] and f["version"] > "1.4":
            errors.append(_("La versione PDF {v} non è ammessa in PDF/A-1 (massimo 1.4).").format(v=f["version"]))
    return errors


_SIMPLE_ENCODINGS = ("WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding", "PDFDocEncoding")


def _unicode_problems(data: bytes) -> list[str]:
    """Livello «u»: ogni font deve avere una mappatura Unicode (/ToUnicode o codifica standard)."""
    problems: list[str] = []
    with ENGINE_LOCK:
        pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
        try:
            seen: set[int] = set()
            missing: list[str] = []
            bad_chars = 0
            for page in pdf:
                for font in page.get_fonts(full=True):
                    xref, ftype, basefont, enc = font[0], font[2], font[3], font[5]
                    if xref in seen or ftype == "Type3":
                        continue
                    seen.add(xref)
                    has_map = _key(pdf, xref, "ToUnicode")[0] != "null"
                    simple = ftype in ("Type1", "TrueType", "MMType1") and enc in _SIMPLE_ENCODINGS
                    if not has_map and not simple:
                        missing.append(basefont.split("+")[-1])
                bad_chars += page.get_text("text").count("\ufffd")
        finally:
            pdf.close()
    if missing:
        problems.append(_("Font senza mappatura Unicode: {f}.").format(f=", ".join(missing[:10])))
    if bad_chars:
        problems.append(_("{n} caratteri senza corrispondenza Unicode.").format(n=bad_chars))
    return problems


# ------------------------------------------------------------------ preparazione
def _clean_for_pdfa(pdf: pymupdf.Document, part: int) -> list[str]:
    """Rimuove dal documento ciò che PDF/A vieta; restituisce le correzioni applicate."""
    fixes: list[str] = []
    cat = pdf.pdf_catalog()
    js = actions = 0
    if _key(pdf, cat, "Names/JavaScript")[0] != "null":
        pdf.xref_set_key(cat, "Names/JavaScript", "null")
        js += 1
    if _key(pdf, cat, "AcroForm/XFA")[0] != "null":
        pdf.xref_set_key(cat, "AcroForm/XFA", "null")
        fixes.append(_("Modulo XFA rimosso."))
    if _key(pdf, cat, "AcroForm/NeedAppearances")[0] != "null":
        pdf.xref_set_key(cat, "AcroForm/NeedAppearances", "null")
    for xref in range(1, pdf.xref_length()):
        try:
            keys = pdf.xref_get_keys(xref)
        except Exception:
            continue
        for k in ("A", "OpenAction", "AA", "Next"):
            if k not in keys:
                continue
            if k == "AA":
                pdf.xref_set_key(xref, k, "null")
                actions += 1
                continue
            text = _value_text(pdf, _key(pdf, xref, k))
            if re.search(r"/S\s*/JavaScript\b", text) or re.search(r"/JS(?![A-Za-z0-9])", text):
                pdf.xref_set_key(xref, k, "null")
                js += 1
            elif _FORBIDDEN_RE.search(text):
                pdf.xref_set_key(xref, k, "null")
                actions += 1
        if "JS" in keys and "S" in keys:
            pdf.xref_set_key(xref, "JS", "null")
        if _key(pdf, xref, "Subtype")[1] == "/Form" and "Ref" in keys:
            pdf.xref_set_key(xref, "Ref", "null")
    # flag delle annotazioni: Print attivo, Hidden/Invisible/NoView disattivati
    fixed_annots = 0
    for pno in range(pdf.page_count):
        page_x = pdf.page_xref(pno)
        kind, text = _key(pdf, page_x, "Annots")
        if kind == "xref":
            text = _value_text(pdf, (kind, text))
        for r in _refs(text):
            if _key(pdf, r, "Subtype")[1] == "/Popup":
                continue
            try:
                flags = int(_key(pdf, r, "F")[1])
            except ValueError:
                flags = 0
            new = (flags | 4) & ~(1 | 2 | 32)
            if new != flags:
                pdf.xref_set_key(r, "F", str(new))
                fixed_annots += 1
    if js:
        fixes.append(_("JavaScript rimosso."))
    if actions:
        fixes.append(_("Azioni non ammesse rimosse ({n}).").format(n=actions))
    if fixed_annots:
        fixes.append(_("Flag di stampa delle annotazioni corretti ({n}).").format(n=fixed_annots))
    if part == 1:
        if pdf.embfile_count():
            for name in list(pdf.embfile_names()):
                pdf.embfile_del(name)
            fixes.append(_("Allegati rimossi (non ammessi in PDF/A-1)."))
    elif part == 2:
        removed = 0
        for name in list(pdf.embfile_names()):
            try:
                content = pdf.embfile_get(name)
            except Exception:
                content = b""
            if not content.lstrip().startswith(b"%PDF"):
                pdf.embfile_del(name)
                removed += 1
        if removed:
            fixes.append(_("Allegati non PDF rimossi ({n}): PDF/A-2 ammette solo allegati PDF/A.").format(
                n=removed))
    return fixes


# ------------------------------------------------------------------ Ghostscript
def ghostscript() -> str | None:
    for name in ("gs", "gswin64c", "gswin32c"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def find_srgb_profile() -> str | None:
    """Cerca un profilo ICC sRGB installato nel sistema."""
    candidates = [
        "/usr/share/color/icc/ghostscript/srgb.icc",
        *sorted(glob.glob("/usr/share/ghostscript/*/iccprofiles/srgb.icc")),
        "/usr/share/color/icc/sRGB.icc", "/usr/share/color/icc/colord/sRGB.icc",
        *sorted(glob.glob("/usr/share/color/icc/**/sRGB*.ic[cm]", recursive=True)),
        *sorted(glob.glob("/usr/share/ghostscript/*/iccprofiles/default_rgb.icc")),
        "/usr/local/share/ghostscript/iccprofiles/srgb.icc",
        "C:/Windows/System32/spool/drivers/color/sRGB Color Space Profile.icm",
        "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _ps_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _pdfa_def(icc: str, part: int) -> str:
    """File di definizione PostScript per l'OutputIntent (pdfmark, documentati da Ghostscript)."""
    return f"""%!
% Definizione dell'OutputIntent per PDF/A generata da Foliant
/ICCProfile {_ps_string(icc)} def
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} << /N 3 >> /PUT pdfmark
[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent
  /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB IEC61966-2.1)
  /Info (sRGB IEC61966-2.1)
  /RegistryName (http://www.color.org)
>> /PUT pdfmark
[{{Catalog}} << /OutputIntents [ {{OutputIntent_PDFA}} ] >> /PUT pdfmark
"""


def _run_gs(cmd: list[str], progress, pages: int, log_path: str, timeout: float) -> int:
    """Esegue Ghostscript controllando l'annullamento e stimando l'avanzamento dal log."""
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                done = 0
                try:
                    with open(log_path, "rb") as fh:
                        done = len(re.findall(rb"^Page \d+", fh.read(), re.M))
                except OSError:
                    pass
                check(progress, 0.15 + 0.65 * min(1.0, done / max(1, pages)),
                      _("Conversione in PDF/A: pagina {i} di {n}…").format(i=min(done + 1, pages), n=pages))
                if time.monotonic() - start > timeout:
                    raise ConversionError(_("Ghostscript ha impiegato troppo tempo ed è stato interrotto."))
                time.sleep(0.1)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    return proc.returncode


# ------------------------------------------------------------------ post-elaborazione
_PDFAID_NS = "http://www.aiim.org/pdfa/ns/id/"


def _set_xmp_stream(pdf: pymupdf.Document, xmp: str) -> None:
    """Scrive il pacchetto XMP non compresso (PDF/A-1 vieta i filtri sui metadati)."""
    cat = pdf.pdf_catalog()
    kind, text = _key(pdf, cat, "Metadata")
    refs = _refs(text) if kind == "xref" else []
    payload = xmp.encode("utf-8")
    if refs:
        x = refs[0]
    else:
        x = pdf.get_new_xref()
        pdf.update_object(x, "<< /Type /Metadata /Subtype /XML >>")
        pdf.xref_set_key(cat, "Metadata", f"{x} 0 R")
    pdf.update_stream(x, payload, compress=0)
    pdf.xref_set_key(x, "Type", "/Metadata")
    pdf.xref_set_key(x, "Subtype", "/XML")
    pdf.xref_set_key(x, "Filter", "null")


def _xmp_with_pdfaid(xmp: str, part: int, conf: str) -> str:
    desc = (f'<rdf:Description rdf:about="" xmlns:pdfaid="{_PDFAID_NS}">'
            f"<pdfaid:part>{part}</pdfaid:part><pdfaid:conformance>{conf}</pdfaid:conformance>"
            "</rdf:Description>")
    xmp = _strip_pdfaid_xml(xmp)
    if "</rdf:RDF>" in xmp:
        return xmp.replace("</rdf:RDF>", desc + "</rdf:RDF>", 1)
    return ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            + desc + '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


def _strip_pdfaid_xml(xmp: str) -> str:
    """Toglie le proprietà pdfaid (in forma di elemento o di attributo) dal pacchetto XMP."""
    if not xmp:
        return xmp
    xmp = re.sub(r"<pdfaid:(\w+)\b[^>]*/>", "", xmp)
    xmp = re.sub(r"<pdfaid:(\w+)\b[^>]*>.*?</pdfaid:\1\s*>", "", xmp, flags=re.S)
    xmp = re.sub(r"\s+pdfaid:\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", xmp)
    # descrizioni rimaste vuote
    xmp = re.sub(r"<rdf:Description\b[^>]*xmlns:pdfaid=[^>]*?/>", "", xmp)
    xmp = re.sub(r"<rdf:Description\b[^>]*xmlns:pdfaid=[^>]*>\s*</rdf:Description>", "", xmp)
    xmp = re.sub(r"\s+xmlns:pdfaid\s*=\s*(\"[^\"]*\"|'[^']*')", "", xmp)
    return xmp


def _embed_files(pdf: pymupdf.Document, files: list[tuple[str, bytes, dict]], associated: bool) -> list[str]:
    """Reinserisce gli allegati dopo Ghostscript con oggetti indiretti puliti.

    Con `associated` (PDF/A-3) ogni allegato è un file associato: `/AF` nel
    catalogo, `/AFRelationship` nella specifica, tipo MIME e data di modifica.
    """
    if not files:
        return []
    cat = pdf.pdf_catalog()
    specs = []
    for name, content, info in files:
        filename = info.get("ufilename") or info.get("filename") or name
        ef = pdf.get_new_xref()
        pdf.update_object(ef, "<< /Type /EmbeddedFile >>")
        pdf.update_stream(ef, content, compress=1)
        pdf.xref_set_key(ef, "Type", "/EmbeddedFile")
        mime = _guess_mime(filename).replace("/", "#2F")
        pdf.xref_set_key(ef, "Subtype", "/" + mime)
        pdf.xref_set_key(ef, "Params", f"<< /Size {len(content)} /ModDate {pymupdf.get_pdf_str(pymupdf.get_pdf_now())} >>")
        spec = pdf.get_new_xref()
        fname = pymupdf.get_pdf_str(filename)
        desc = pymupdf.get_pdf_str(info.get("description") or info.get("desc") or "")
        rel = "/AFRelationship /Unspecified" if associated else ""
        pdf.update_object(spec, f"<< /Type /Filespec /F {fname} /UF {fname} /Desc {desc} "
                                f"/EF << /F {ef} 0 R /UF {ef} 0 R >> {rel} >>")
        specs.append((name, spec))
    specs.sort(key=lambda t: t[0])
    names = " ".join(f"{pymupdf.get_pdf_str(n)} {x} 0 R" for n, x in specs)
    pdf.xref_set_key(cat, "Names/EmbeddedFiles", f"<< /Names [ {names} ] >>")
    fixes = []
    if associated:
        pdf.xref_set_key(cat, "AF", "[" + " ".join(f"{x} 0 R" for _n, x in specs) + "]")
        fixes.append(_("Allegati dichiarati come file associati (/AF, /AFRelationship): {n}.").format(n=len(specs)))
    return fixes


def _guess_mime(name: str) -> str:
    import mimetypes
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# ------------------------------------------------------------------ conversione
def convert_to_pdfa(data: bytes, level: str = "2b", password=None, progress=None) -> tuple[bytes, dict]:
    """Converte in PDF/A (CNV-14) e restituisce (byte, resoconto).

    Resoconto: `{"level", "fixes", "errors", "compliant"}`; `errors` sono gli
    errori residui trovati da `verify_pdfa` sul risultato.
    """
    part, conf = _level(level)
    gs = ghostscript()
    if not gs:
        raise ConversionError(_("La conversione in PDF/A richiede Ghostscript, che non è installato. "
                                "Installalo (ad esempio con «sudo apt install ghostscript») e riprova."))
    icc = find_srgb_profile()
    if not icc:
        raise ConversionError(_("Non è stato trovato un profilo colore sRGB nel sistema: impossibile creare "
                                "l'OutputIntent richiesto da PDF/A. Installa i profili ICC (ad esempio il "
                                "pacchetto «icc-profiles-free») e riprova."))
    check(progress, 0.0, _("Preparazione del documento…"))
    fixes: list[str] = []
    with ENGINE_LOCK:
        pdf = _open(data, password)
        try:
            if (pdf.metadata or {}).get("encryption"):
                fixes.append(_("Cifratura rimossa."))
            fixes += _clean_for_pdfa(pdf, part)
            # gli allegati ammessi sono tolti prima di Ghostscript e reinseriti dopo
            originals = []
            for name in list(pdf.embfile_names()):
                try:
                    originals.append((name, pdf.embfile_get(name), pdf.embfile_info(name)))
                except Exception:
                    pass
                pdf.embfile_del(name)
            pdf.xref_set_key(pdf.pdf_catalog(), "Names/EmbeddedFiles", "null")
            pages = pdf.page_count
            before = _scan(pdf, data)
            prepared = pdf.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            pdf.close()
    if before["fonts_missing"]:
        fixes.append(_("Font incorporati: {f}.").format(f=", ".join(before["fonts_missing"][:10])))
    if part == 1 and before["transparency"]:
        fixes.append(_("Trasparenze appiattite."))
    if not before["output_intent"]:
        fixes.append(_("Aggiunto l'OutputIntent sRGB."))
    if not before["id"]:
        fixes.append(_("Aggiunto /ID nel trailer."))
    if before["lzw"]:
        fixes.append(_("Compressione LZW sostituita con Flate."))
    check(progress, 0.1, _("Conversione con Ghostscript…"))
    tmpdir = tempfile.mkdtemp(prefix="foliant-pdfa-")
    try:
        src = os.path.join(tmpdir, "in.pdf")
        dst = os.path.join(tmpdir, "out.pdf")
        defs = os.path.join(tmpdir, "pdfa_def.ps")
        icc_copy = os.path.join(tmpdir, "srgb.icc")
        shutil.copyfile(icc, icc_copy)
        Path(src).write_bytes(prepared)
        Path(defs).write_text(_pdfa_def(icc_copy.replace("\\", "/"), part), encoding="latin-1")
        cmd = [gs, f"-dPDFA={part}", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-dPDFACompatibilityPolicy=1",
               "-sColorConversionStrategy=RGB", "-sProcessColorModel=DeviceRGB", "-sDEVICE=pdfwrite",
               "-dEmbedAllFonts=true", "-dSubsetFonts=true", "-dAutoRotatePages=/None",
               f"-dCompatibilityLevel={'1.4' if part == 1 else '1.7'}",
               f"--permit-file-read={tmpdir}{os.sep}", f"-sOutputFile={dst}", defs, src]
        rc = _run_gs(cmd, progress, pages, os.path.join(tmpdir, "gs.log"), timeout=max(300, pages * 30))
        if rc != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
            try:
                log = Path(tmpdir, "gs.log").read_text("utf-8", "replace").strip().splitlines()
            except OSError:
                log = []
            detail = next((ln for ln in reversed(log) if "rror" in ln), log[-1] if log else "")
            raise ConversionError(_("Ghostscript non è riuscito a convertire il documento in PDF/A. {d}").format(
                d=detail).strip())
        out = Path(dst).read_bytes()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    check(progress, 0.85, _("Verifica e completamento…"))
    out, more = _post_fix(out, part, conf, originals, prepared)
    fixes += more
    check(progress, 0.95, _("Verifica della conformità…"))
    errors = verify_pdfa(out, level)
    check(progress, 1.0, _("Fatto"))
    return out, {"level": f"{part}{conf.lower()}", "fixes": fixes, "errors": errors, "compliant": not errors}


def _restore_text(pdf: pymupdf.Document, source: bytes) -> list[int]:
    """Pagine rasterizzate da Ghostscript (appiattimento delle trasparenze in PDF/A-1):
    il testo originale è aggiunto come livello invisibile, così resta ricercabile e copiabile."""
    restored: list[int] = []
    try:
        src = pymupdf.open(stream=source, filetype="pdf")
    except Exception:
        return restored
    try:
        if src.page_count != pdf.page_count:
            return restored
        font = pymupdf.Font("helv")
        for pno in range(pdf.page_count):
            page = pdf[pno]
            if page.get_text("text").strip():
                continue
            words = src[pno].get_text("words")
            if not words:
                continue
            tw = pymupdf.TextWriter(page.rect)
            for x0, y0, x1, y1, word, *_rest in words:
                size = max(1.0, (y1 - y0) * 0.8)
                width = font.text_length(word, fontsize=size) or 1.0
                size = max(1.0, min(size, size * (x1 - x0) / width))
                try:
                    tw.append((x0, y1 - (y1 - y0) * 0.2), word, font=font, fontsize=size)
                except Exception:
                    continue
            tw.write_text(page, render_mode=3)
            restored.append(pno + 1)
    finally:
        src.close()
    return restored


def _post_fix(out: bytes, part: int, conf: str, originals, source: bytes) -> tuple[bytes, list[str]]:
    fixes: list[str] = []
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=out, filetype="pdf")
        except Exception as e:
            raise ConversionError(_("Il file prodotto da Ghostscript non è leggibile.")) from e
        try:
            changed = False
            if part == 1:
                restored = _restore_text(pdf, source)
                if restored:
                    fixes.append(_("Pagine rasterizzate per appiattire le trasparenze: {p}; il testo è stato "
                                   "conservato come livello invisibile ricercabile.").format(
                                       p=", ".join(map(str, restored))))
                    changed = True
            xmp = _xmp(pdf)
            p, c = _pdfaid(xmp)
            if p != str(part) or c != conf:
                _set_xmp_stream(pdf, _xmp_with_pdfaid(xmp, part, conf))
                fixes.append(_("Identificazione PDF/A aggiunta ai metadati XMP."))
                changed = True
            if originals:
                fixes += _embed_files(pdf, originals, associated=part == 3)
                changed = True
            remaining_js = _scan(pdf, out)
            if remaining_js["javascript"] or remaining_js["actions"]:
                _clean_for_pdfa(pdf, part)
                changed = True
            if _key(pdf, -1, "ID")[0] == "null":
                changed = True   # il salvataggio del motore scrive sempre /ID
            if changed:
                out = pdf.tobytes(garbage=1, deflate=False, use_objstms=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            pdf.close()
    return out, fixes


# ------------------------------------------------------------------ CNV-15
def strip_pdfa_identification(data: bytes) -> bytes:
    """Rimuove la dichiarazione di conformità (pdfaid) dai metadati XMP.

    Il resto del file è riscritto senza toccare gli stream: i contenuti delle
    pagine restano byte per byte identici.
    """
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=bytes(data), filetype="pdf")
        except Exception as e:
            raise ConversionError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
        try:
            if pdf.needs_pass:
                raise PasswordRequired()
            xmp = _xmp(pdf)
            if not xmp or "pdfaid" not in xmp:
                return bytes(data)
            _set_xmp_stream(pdf, _strip_pdfaid_xml(xmp))
            return pdf.tobytes(garbage=0, deflate=False, clean=False, expand=0)
        finally:
            pdf.close()


def convert_file_to_pdfa(input_path, output_path, level: str = "2b", password=None, progress=None,
                         save_anyway: bool = False, *, policy: str = "overwrite") -> tuple[str | None, dict]:
    """Converte un file in PDF/A e lo scrive in `output_path`.

    Se il risultato non è conforme: con `save_anyway` scrive un PDF ordinario
    senza identificazione `pdfaid`, altrimenti non scrive nulla (percorso None).
    """
    try:
        data = Path(input_path).read_bytes()
    except OSError as e:
        raise ConversionError(_("Impossibile leggere «{n}»: {e}").format(
            n=Path(input_path).name, e=e.strerror or e)) from e
    out, report = convert_to_pdfa(data, level, password=password, progress=progress)
    if not report["compliant"]:
        if not save_anyway:
            return None, report
        out = strip_pdfa_identification(out)
        report = {**report, "fixes": report["fixes"] + [_("Salvato come PDF ordinario, senza dichiarazione di "
                                                          "conformità PDF/A.")]}
    with OutputFiles(policy) as files:
        tmp = files.new(output_path)
        Path(tmp).write_bytes(out)
        final = files.commit()[0]
    return final, report


__all__ = ["PDFA_LEVELS", "analyze_pdfa", "convert_to_pdfa", "verify_pdfa", "strip_pdfa_identification",
           "convert_file_to_pdfa", "ghostscript", "find_srgb_profile"]
