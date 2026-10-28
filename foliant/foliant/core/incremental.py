"""Salvataggio incrementale (NFR-09): byte originali + aggiornamento in coda.

Un documento firmato si salva sempre in modo incrementale: i byte coperti dalle
firme (`/ByteRange`) restano un prefisso esatto del file salvato e in coda si
aggiunge una nuova revisione con i soli oggetti cambiati. Così le modifiche
consentite (commenti, compilazione dei campi) non invalidano le firme.

Come funziona: il documento in memoria è aperto dagli stessi byte del file e le
istantanee di annulla/ripeti (`tobytes(garbage=0)`) conservano la numerazione degli
oggetti. Si apre quindi una copia del motore dal file originale (scritto in un
file temporaneo, perché il motore salva in modo incrementale solo sul file da cui
ha letto), vi si ricopiano gli oggetti nuovi o diversi e si salva con
`incremental=True` mantenendo la cifratura originale.

Accorgimenti:
- gli oggetti strutturali dell'originale (xref stream, object stream, oggetti
  liberi) non si riscrivono mai: le istantanee li sciolgono e altrimenti
  verrebbero sovrascritti con `null`;
- i flussi si confrontano prima sui byte grezzi (decifrati ma ancora codificati) e,
  se diversi, sul contenuto decodificato ignorando /Length, /Filter e /DecodeParms:
  una ricodifica senza cambi di contenuto non è una modifica;
- un flusso cambiato si copia con i suoi byte grezzi e i suoi filtri (immagini
  JPEG/JPX comprese), senza ricodificarlo;
- /Info e /Root del trailer si riportano se cambiano;
- una pagina che torna a riferire annotazioni già presenti in una revisione
  precedente (es. ripeti dopo un salvataggio) le riscrive: altrimenti i validatori
  vedono riferimenti «non giustificati» nella nuova revisione.

Va usato tenendo ENGINE_LOCK.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass

import pymupdf

from ..i18n import _
from .errors import DocumentError


class IncrementalSaveError(DocumentError):
    """Il salvataggio incrementale non è possibile: il messaggio spiega perché."""


_STREAM_KEYS = re.compile(r"/(Length|Filter|DecodeParms)\s*(\d+\s+\d+\s+R|\[[^\]]*\]|<<.*?>>|/[^\s/<>\[\]()]+|\d+)", re.S)


@dataclass
class Change:
    """Oggetto da scrivere nell'aggiornamento: testo dell'oggetto e, per i flussi, byte grezzi."""

    xref: int
    obj: str
    raw: bytes | None          # byte del flusso decifrati ma ancora codificati (None = non è un flusso)
    existed: bool              # l'oggetto esisteva nel file originale
    old: str | None = None     # testo dell'oggetto nel file originale


def _obj(pdf: pymupdf.Document, xref: int) -> str | None:
    try:
        return pdf.xref_object(xref, compressed=True)
    except Exception:
        return None


def _is_stream(pdf: pymupdf.Document, xref: int) -> bool:
    try:
        return bool(pdf.xref_is_stream(xref))
    except Exception:
        return False


def _raw(pdf: pymupdf.Document, xref: int) -> bytes | None:
    try:
        return pdf.xref_stream_raw(xref)
    except Exception:
        return None


def _decoded(pdf: pymupdf.Document, xref: int) -> bytes | None:
    try:
        return pdf.xref_stream(xref)
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


def _same(orig: pymupdf.Document, current: pymupdf.Document, x: int, cur: str, old: str | None) -> tuple[bool, bytes | None]:
    """(uguale, byte grezzi attuali) per l'oggetto `x` presente in entrambi i documenti."""
    cur_stream = _is_stream(current, x)
    old_stream = _is_stream(orig, x)
    if cur_stream != old_stream:
        return False, _raw(current, x) if cur_stream else None
    if not cur_stream:
        return _norm(cur, False) == _norm(old, False), None
    raw = _raw(current, x)
    if raw is not None and raw == _raw(orig, x) and _norm(cur, False) == _norm(old, False):
        return True, raw
    if _norm(cur, True) != _norm(old, True):
        return False, raw
    return _decoded(current, x) == _decoded(orig, x), raw


def diff(orig: pymupdf.Document, current: pymupdf.Document) -> list[Change]:
    """Oggetti di `current` nuovi o diversi rispetto a `orig` (stessa numerazione)."""
    skip = _structural(orig)
    n_orig = orig.xref_length()
    out: list[Change] = []
    for x in range(1, current.xref_length()):
        if x in skip:
            continue
        cur = _obj(current, x)
        if cur is None or cur.strip() == "null":
            continue                                   # mai scrivere null sopra un oggetto
        if x < n_orig:
            old = _obj(orig, x)
            same, raw = _same(orig, current, x, cur, old)
            if same:
                continue
            out.append(Change(x, cur, raw, True, old))
        else:
            out.append(Change(x, cur, _raw(current, x) if _is_stream(current, x) else None, False))
    changed = {c.xref for c in out}
    extra: list[int] = []
    for c in list(out):
        if not c.existed or not re.search(r"/Type\s*/Page\b", c.obj):
            continue
        added = _annot_refs(current, c.xref) - _annot_refs(orig, c.xref)
        for a in sorted(added):
            for ref in (a, *_popup_ref(current, a)):
                if ref not in changed and ref not in skip and ref < current.xref_length():
                    changed.add(ref)
                    extra.append(ref)
    for x in extra:
        obj = _obj(current, x)
        if obj and obj.strip() != "null":
            out.append(Change(x, obj, _raw(current, x) if _is_stream(current, x) else None,
                              x < n_orig, _obj(orig, x) if x < n_orig else None))
    return out


def _annot_refs(pdf: pymupdf.Document, page_xref: int) -> set[int]:
    try:
        kind, val = pdf.xref_get_key(page_xref, "Annots")
        if kind == "xref":
            val = pdf.xref_object(int(val.split()[0]), compressed=True)
        elif kind != "array":
            return set()
        return {int(m) for m in re.findall(r"(\d+)\s+0\s+R", val)}
    except Exception:
        return set()


def _popup_ref(pdf: pymupdf.Document, xref: int) -> tuple[int, ...]:
    try:
        kind, val = pdf.xref_get_key(xref, "Popup")
        return (int(val.split()[0]),) if kind == "xref" else ()
    except Exception:
        return ()


def _trailer_key(pdf: pymupdf.Document, key: str) -> str:
    try:
        kind, val = pdf.xref_get_key(-1, key)
    except Exception:
        return ""
    return val if kind == "xref" else ""


def _put_raw_stream(pdf: pymupdf.Document, xref: int, raw: bytes) -> bool:
    """Imposta i byte grezzi del flusso lasciando /Filter e /DecodeParms dell'oggetto."""
    try:
        m = pymupdf.mupdf
        as_pdf = getattr(pymupdf, "_as_pdf_document", None)
        pdoc = as_pdf(pdf) if as_pdf is not None else m.pdf_specifics(pdf.this)
        ref = m.pdf_new_indirect(pdoc, xref, 0)
        m.pdf_update_stream(pdoc, ref, m.fz_new_buffer_from_copied_data(raw), 1)
        return True
    except Exception:
        return False


def _apply(orig: pymupdf.Document, current: pymupdf.Document, ch: Change) -> None:
    while orig.xref_length() <= ch.xref:
        orig.get_new_xref()
    orig.update_object(ch.xref, ch.obj)
    if ch.raw is None:
        return
    if _put_raw_stream(orig, ch.xref, ch.raw):
        return
    # ripiego: contenuto decodificato, ricompresso; i parametri del vecchio filtro non valgono più
    data = _decoded(current, ch.xref)
    if data is None:
        raise IncrementalSaveError(_("Un flusso del documento non è leggibile."))
    orig.update_stream(ch.xref, data, compress=True)
    orig.xref_set_key(ch.xref, "DecodeParms", "null")


def _open_original(path: str, password: str | None) -> pymupdf.Document:
    try:
        orig = pymupdf.open(path)
    except Exception as e:
        raise IncrementalSaveError(_("Il file originale non è leggibile.")) from e
    if orig.needs_pass and not (password and orig.authenticate(password)):
        orig.close()
        raise IncrementalSaveError(_("Il file originale è protetto da una password diversa."))
    return orig


def incremental_update(original: bytes, current: pymupdf.Document, password: str | None = None) -> bytes:
    """Restituisce `original` + un aggiornamento incrementale con le differenze di `current`.

    `current` deve avere la stessa numerazione degli oggetti di `original` (documento
    aperto da quei byte, eventualmente ripristinato da istantanee `garbage=0`).
    Tenere ENGINE_LOCK. Solleva IncrementalSaveError se non è possibile.
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
                _apply(orig, current, ch)
            for k, v in trailer_changed.items():
                orig.xref_set_key(-1, k, v)
            orig.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        except IncrementalSaveError:
            raise
        except Exception as e:
            raise IncrementalSaveError(_("Salvataggio incrementale non riuscito: {e}").format(
                e=str(e) or e.__class__.__name__)) from e
        finally:
            orig.close()
        with open(path, "rb") as f:
            data = f.read()
    if not data.startswith(original):
        raise IncrementalSaveError(_("Il salvataggio incrementale non ha conservato i byte originali."))
    return data


def signed_fields(pdf: pymupdf.Document, limit: int = 20000) -> int:
    """Numero di campi firma (/FT /Sig, anche ereditato) con un valore /V nel modulo del documento."""
    try:
        kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/Fields")
    except Exception:
        return 0
    if kind == "xref":
        try:
            val = pdf.xref_object(int(val.split()[0]), compressed=True)
        except Exception:
            return 0
    elif kind != "array":
        return 0
    stack = [(int(x), None) for x in re.findall(r"(\d+)\s+\d+\s+R", val)]
    seen: set[int] = set()
    count = 0
    while stack and len(seen) < limit:
        x, inherited = stack.pop()
        if x in seen or x <= 0 or x >= pdf.xref_length():
            continue
        seen.add(x)
        try:
            k, ft = pdf.xref_get_key(x, "FT")
            ft = ft if k == "name" else inherited
            if ft == "/Sig" and pdf.xref_get_key(x, "V")[0] in ("xref", "dict"):
                count += 1
            k, kids = pdf.xref_get_key(x, "Kids")
            if k == "xref":
                kids, k = pdf.xref_object(int(kids.split()[0]), compressed=True), "array"
            if k == "array":
                stack.extend((int(c), ft) for c in re.findall(r"(\d+)\s+\d+\s+R", kids))
        except Exception:
            continue
    return count
