"""Cifratura standard AES-256 scritta con pyHanko per le opzioni che il motore non ha:
metadati in chiaro (/EncryptMetadata false, SEC-06) e verifica di integrità ISO/TS 32004
(SEC-11), più la verifica all'apertura. Il risultato resta AES-256 R6 con AESV3 in
modalità CBC, quindi il motore lo apre; AES-GCM (ISO/TS 32003) non è usato perché il
motore non saprebbe riaprire il file.
"""
from __future__ import annotations

import io
import re

INTEGRITY_OK = "ok"
INTEGRITY_TAMPERED = "tampered"
INTEGRITY_UNVERIFIABLE = "unverifiable"
INTEGRITY_NONE = "none"

_PERM_MASK = 0b1111_0011_1100          # bit 3–6 e 9–12 di /P (ISO 32000-2, tabella 22)


def pyhanko_permissions(value: int):
    from pyhanko.pdf_utils.crypt.permissions import StandardPermissions as SP
    bits = _PERM_MASK if value == -1 else value & _PERM_MASK
    return SP(bits) | SP.TOLERATE_MISSING_PDF_MAC


def encrypt(plain: bytes, user_pw: str, owner_pw: str, permissions: int, *, encrypt_metadata: bool = True,
            mac: bool = False) -> bytes:
    """`plain` (PDF non cifrato) cifrato con il gestore standard AES-256 R6."""
    from pyhanko.pdf_utils.crypt import StandardSecurityHandler
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    handler = StandardSecurityHandler.build_from_pw(owner_pw, user_pw or "", perms=pyhanko_permissions(permissions),
                                                    encrypt_metadata=encrypt_metadata, pdf_mac=mac)
    writer = copy_into_new_writer(PdfFileReader(io.BytesIO(plain)))
    writer._assign_security_handler(handler)
    if not encrypt_metadata:
        # pyHanko lascia in chiaro solo l'XMP che crea lui: quello del documento va marcato
        # con il crypt filter /Identity (ISO 32000-2 §7.6.5, /EncryptMetadata false)
        from pyhanko.pdf_utils.generic import IDENTITY
        try:
            meta = writer.root["/Metadata"]
        except KeyError:
            meta = None
        if meta is not None and not meta._has_crypt_filter:
            meta.add_crypt_filter(IDENTITY, handler=handler)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def has_integrity(data: bytes) -> bool:
    """Il file dichiara la protezione dell'integrità (sale /KDFSalt nel dizionario /Encrypt)."""
    return re.search(rb"/KDFSalt\s*[<(]", data) is not None


def verify(data: bytes, passwords: list[str]) -> str:
    """Esito della verifica ISO/TS 32004 con le password note (la prima che apre)."""
    import logging

    from pyhanko.pdf_utils.crypt import AuthStatus
    from pyhanko.pdf_utils.reader import PdfFileReader
    if not has_integrity(data):
        return INTEGRITY_NONE
    if b"/AuthCode" not in data:
        return INTEGRITY_UNVERIFIABLE
    log = logging.getLogger("pyhanko")
    level = log.level
    log.setLevel(logging.CRITICAL)            # l'esito si comunica all'utente, non nel registro
    try:
        tried = False
        for pw in dict.fromkeys(passwords):
            reader = PdfFileReader(io.BytesIO(data))
            try:
                if reader.decrypt(pw).status != AuthStatus.FAILED:
                    return INTEGRITY_OK
                tried = True
            except Exception:
                tried = True
        # con nessuna password l'apertura (e quindi la verifica del MAC) è riuscita: se una
        # password apre il file con il motore, il MAC non corrisponde
        return INTEGRITY_TAMPERED if tried else INTEGRITY_UNVERIFIABLE
    finally:
        log.setLevel(level)
