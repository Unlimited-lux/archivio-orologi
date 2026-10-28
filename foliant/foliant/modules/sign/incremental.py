"""Classificazione delle modifiche a un documento firmato (SIG-17/SIG-21).

Il salvataggio incrementale è del nucleo (`foliant.core.incremental`, NFR-09): qui
resta solo ciò che il nucleo non fa, cioè stabilire se le modifiche in memoria
toccano qualcosa oltre ad annotazioni, campi del modulo o dati di firma.
"""

from __future__ import annotations

import os
import re
import tempfile

import pymupdf

from foliant.core.incremental import IncrementalSaveError, _open_original, _trailer_key, diff


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
