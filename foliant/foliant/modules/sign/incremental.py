"""Salvataggio incrementale dei documenti firmati (soluzione locale, vedi CORE_REQUESTS.md).

Il documento in memoria è aperto dagli stessi byte del file firmato e le
istantanee di annulla/ripeti conservano la numerazione degli oggetti; quindi
basta confrontare oggetto per oggetto il documento attuale con il file originale
e scrivere in coda (aggiornamento incrementale) solo gli oggetti cambiati o nuovi.
I byte firmati restano un prefisso esatto del file salvato.
"""

from __future__ import annotations

import os
import tempfile

import pymupdf

from foliant.i18n import _


class IncrementalSaveError(Exception):
    pass


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


def incremental_update(original: bytes, current: pymupdf.Document, password: str | None = None) -> bytes:
    """Restituisce `original` + un aggiornamento incrementale con le differenze di `current`.

    Va chiamata tenendo ENGINE_LOCK. Solleva IncrementalSaveError se non è possibile.
    """
    with tempfile.TemporaryDirectory(prefix="foliant-incr-") as tmp:
        path = os.path.join(tmp, "documento.pdf")
        with open(path, "wb") as f:
            f.write(original)
        try:
            orig = pymupdf.open(path)
        except Exception as e:
            raise IncrementalSaveError(_("Il file originale non è leggibile.")) from e
        try:
            if orig.needs_pass and not (password and orig.authenticate(password)):
                raise IncrementalSaveError(_("Il file originale è protetto da password."))
            if orig.is_repaired:
                raise IncrementalSaveError(_("Il file originale è danneggiato: un salvataggio incrementale non "
                                             "garantirebbe le firme esistenti."))
            changed = 0
            n = current.xref_length()
            for x in range(1, n):
                cur = _obj(current, x)
                if cur is None:
                    continue
                while orig.xref_length() <= x:
                    orig.get_new_xref()
                old = _obj(orig, x) if x < orig.xref_length() else None
                cur_stream = _stream(current, x)
                old_stream = _stream(orig, x) if old is not None else None
                if cur == old and cur_stream == old_stream:
                    continue
                if old is not None and cur_stream is None and old_stream is None and cur == old:
                    continue
                orig.update_object(x, cur)
                if cur_stream is not None:
                    orig.update_stream(x, cur_stream, compress=True)
                changed += 1
            if changed == 0:
                return original
            orig.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        finally:
            orig.close()
        with open(path, "rb") as f:
            data = f.read()
    if not data.startswith(original):
        raise IncrementalSaveError(_("Il salvataggio incrementale non ha conservato i byte firmati."))
    return data


TRANSLATIONS_EN = {
    "Il file originale non è leggibile.": "The original file cannot be read.",
    "Il file originale è protetto da password.": "The original file is password protected.",
    "Il file originale è danneggiato: un salvataggio incrementale non garantirebbe le firme esistenti.":
        "The original file is damaged: an incremental save would not preserve the existing signatures.",
    "Il salvataggio incrementale non ha conservato i byte firmati.":
        "The incremental save did not preserve the signed bytes.",
}


def changes_content(original: bytes, current: pymupdf.Document) -> bool:
    """True se `current` modifica pagine o contenuto rispetto a `original`
    (False se cambiano solo annotazioni, campi o dati di firma). Tenere ENGINE_LOCK."""
    try:
        orig = pymupdf.open(stream=original, filetype="pdf")
    except Exception:
        return True
    try:
        if orig.needs_pass:
            return True
        if orig.page_count != current.page_count:
            return True
        for i in range(orig.page_count):
            a, b = orig[i], current[i]
            if a.rotation != b.rotation or a.mediabox != b.mediabox or a.cropbox != b.cropbox:
                return True
            if a.read_contents() != b.read_contents():
                return True
        keys = ("title", "author", "subject", "keywords")
        return any((orig.metadata or {}).get(k) != (current.metadata or {}).get(k) for k in keys)
    finally:
        orig.close()
