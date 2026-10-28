"""Salvataggio incrementale dei documenti firmati (soluzione locale, vedi CORE_REQUESTS.md).

Il documento in memoria è aperto dagli stessi byte del file firmato e le
istantanee di annulla/ripeti conservano la numerazione degli oggetti; quindi
basta confrontare oggetto per oggetto il documento attuale con il file originale
e scrivere in coda (aggiornamento incrementale) solo gli oggetti cambiati o nuovi.
I byte firmati restano un prefisso esatto del file salvato.

Accorgimenti:
- gli oggetti strutturali dell'originale (xref stream, object stream, oggetti
  liberi) non vengono mai riscritti: le istantanee li sciolgono e altrimenti
  verrebbero sovrascritti con `null`;
- i flussi si confrontano sul contenuto decodificato, ignorando /Length,
  /Filter e /DecodeParms (le istantanee sono decifrate e non compresse);
- /Info e /Root del trailer vengono riportati se cambiano.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass

import pymupdf

from foliant.i18n import _


class IncrementalSaveError(Exception):
    pass


_STREAM_KEYS = re.compile(r"/(Length|Filter|DecodeParms)\s*(\d+\s+\d+\s+R|\[[^\]]*\]|<<.*?>>|/\w+|\d+)", re.S)


def _obj(pdf: pymupdf.Document, xref: int) -> str | None:
    try:
        return pdf.xref_object(xref, compressed=True)
    except Exception:
        return None


def _stream(pdf: pymupdf.Document, xref: int) -> bytes | None:
    try:
        return pdf.xref_stream(xref) if pdf.xref_is_stream(xref) else None
    except Exception:
        return None


def _norm(obj: str | None, is_stream: bool) -> str | None:
    if obj is None:
        return None
    if is_stream:
        obj = _STREAM_KEYS.sub("", obj)
    return re.sub(r"\s+", " ", obj).strip()


def _structural(pdf: pymupdf.Document) -> set[int]:
    """Oggetti dell'originale da non toccare mai: xref stream, object stream, liberi."""
    skip = set()
    for x in range(1, pdf.xref_length()):
        try:
            if pdf.xref_get_key(x, "Type")[1] in ("/XRef", "/ObjStm"):
                skip.add(x)
            elif _obj(pdf, x) in (None, "null"):
                skip.add(x)
        except Exception:
            skip.add(x)
    return skip


@dataclass
class Change:
    xref: int
    obj: str
    stream: bytes | None
    existed: bool
    old: str | None = None


def diff(orig: pymupdf.Document, current: pymupdf.Document) -> list[Change]:
    """Oggetti di `current` nuovi o diversi rispetto a `orig` (stessa numerazione)."""
    skip = _structural(orig)
    n_orig = orig.xref_length()
    out = []
    for x in range(1, current.xref_length()):
        if x in skip:
            continue
        cur = _obj(current, x)
        if cur is None or cur.strip() == "null":
            continue                                   # mai scrivere null sopra un oggetto
        cur_stream = _stream(current, x)
        if x < n_orig:
            old = _obj(orig, x)
            old_stream = _stream(orig, x)
            if cur_stream == old_stream and _norm(cur, cur_stream is not None) == \
                    _norm(old, old_stream is not None):
                continue
            out.append(Change(x, cur, cur_stream, True, old))
        else:
            out.append(Change(x, cur, cur_stream, False))
    return out


def _trailer_key(pdf: pymupdf.Document, key: str) -> str:
    try:
        kind, val = pdf.xref_get_key(-1, key)
    except Exception:
        return ""
    return val if kind == "xref" else ""


def _open_original(path: str, password: str | None) -> pymupdf.Document:
    try:
        orig = pymupdf.open(path)
    except Exception as e:
        raise IncrementalSaveError(_("Il file originale non è leggibile.")) from e
    if orig.needs_pass and not (password and orig.authenticate(password)):
        orig.close()
        raise IncrementalSaveError(_("Il file originale è protetto da password."))
    return orig


def incremental_update(original: bytes, current: pymupdf.Document, password: str | None = None) -> bytes:
    """Restituisce `original` + un aggiornamento incrementale con le differenze di `current`.

    Va chiamata tenendo ENGINE_LOCK. Solleva IncrementalSaveError se non è possibile.
    """
    with tempfile.TemporaryDirectory(prefix="foliant-incr-") as tmp:
        path = os.path.join(tmp, "documento.pdf")
        with open(path, "wb") as f:
            f.write(original)
        orig = _open_original(path, password)
        try:
            if orig.is_repaired:
                raise IncrementalSaveError(_("Il file originale è danneggiato: un salvataggio incrementale non "
                                             "garantirebbe le firme esistenti."))
            changes = diff(orig, current)
            trailer = {k: _trailer_key(current, k) for k in ("Info", "Root")}
            trailer_changed = {k: v for k, v in trailer.items() if v and v != _trailer_key(orig, k)}
            if not changes and not trailer_changed:
                return original
            for ch in changes:
                while orig.xref_length() <= ch.xref:
                    orig.get_new_xref()
                orig.update_object(ch.xref, ch.obj)
                if ch.stream is not None:
                    orig.update_stream(ch.xref, ch.stream, compress=True)
            for k, v in trailer_changed.items():
                orig.xref_set_key(-1, k, v)
            orig.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        except IncrementalSaveError:
            raise
        except Exception as e:
            raise IncrementalSaveError(str(e) or e.__class__.__name__) from e
        finally:
            orig.close()
        with open(path, "rb") as f:
            data = f.read()
    if not data.startswith(original):
        raise IncrementalSaveError(_("Il salvataggio incrementale non ha conservato i byte firmati."))
    return data


def _is_allowed_object(old: str | None, new: str) -> bool:
    """True per oggetti la cui modifica è consentita dopo una firma di approvazione:
    annotazioni, campi del modulo, /AcroForm, e pagine o catalogo cambiati solo in /Annots o /AcroForm."""
    text = new or ""
    if re.search(r"/Type\s*/Annot\b", text) or (re.search(r"/Subtype\s*/\w+", text) and "/Rect" in text
                                                  and not re.search(r"/Type\s*/Page\b", text)):
        return True
    if re.search(r"/FT\s*/\w+", text) or (old and re.search(r"/FT\s*/\w+", old)):
        return True
    if re.search(r"/Fields\s*\[", text) and "/Type" not in text:
        return True                                    # dizionario /AcroForm
    if old is None:
        return False

    def strip(s: str, keys: tuple[str, ...]) -> str:
        for k in keys:
            s = re.sub(rf"/{k}\s*(\[[^\]]*\]|\d+\s+\d+\s+R|<<.*?>>)", "", s, flags=re.S)
        return re.sub(r"\s+", " ", s).strip()

    if re.search(r"/Type\s*/Page\b", text):
        return strip(old, ("Annots",)) == strip(text, ("Annots",))
    if re.search(r"/Type\s*/Catalog\b", text):
        return strip(old, ("AcroForm", "Perms", "DSS")) == strip(text, ("AcroForm", "Perms", "DSS"))
    return False


def changes_content(original: bytes, current: pymupdf.Document, password: str | None = None) -> bool:
    """True se `current` modifica qualcosa oltre ad annotazioni, campi o dati di
    firma rispetto a `original` (pagine, contenuti, immagini, font, metadati).
    Tenere ENGINE_LOCK."""
    with tempfile.TemporaryDirectory(prefix="foliant-diff-") as tmp:
        path = os.path.join(tmp, "documento.pdf")
        with open(path, "wb") as f:
            f.write(original)
        try:
            orig = _open_original(path, password)
        except IncrementalSaveError:
            return True
        try:
            if orig.page_count != current.page_count:
                return True
            if _trailer_key(current, "Info") != _trailer_key(orig, "Info"):
                return True
            for ch in diff(orig, current):
                if ch.existed and not _is_allowed_object(ch.old, ch.obj):
                    return True
            return False
        except Exception:
            return True
        finally:
            orig.close()


TRANSLATIONS_EN = {
    "Il file originale non è leggibile.": "The original file cannot be read.",
    "Il file originale è protetto da password.": "The original file is password protected.",
    "Il file originale è danneggiato: un salvataggio incrementale non garantirebbe le firme esistenti.":
        "The original file is damaged: an incremental save would not preserve the existing signatures.",
    "Il salvataggio incrementale non ha conservato i byte firmati.":
        "The incremental save did not preserve the signed bytes.",
}
