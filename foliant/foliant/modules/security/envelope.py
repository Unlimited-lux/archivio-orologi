"""Busta di sicurezza: cifratura dei soli allegati (SEC-10).

Le pagine restano in chiaro (filtri /Identity per flussi e stringhe); i file incorporati
usano un crypt filter AESV3 con /AuthEvent /EFOpen (ISO 32000-2 §7.6.6), quindi la
password serve solo per aprire o estrarre gli allegati. Il file è scritto con pyHanko,
perché il motore non sa produrre /EFF.

Nota: il motore (MuPDF 1.28) chiede comunque la password per aprire una busta, anche
se le pagine non sono cifrate; gli altri lettori conformi mostrano la copertina.
"""
from __future__ import annotations

import io
import os
import secrets

import pymupdf

from foliant.api import _

EFF_NAME = "/FolEFF"


def has_attachments(pdf: pymupdf.Document) -> bool:
    try:
        return pdf.embfile_count() > 0
    except Exception:
        return False


def encrypt_attachments(data: bytes, password: str, owner_password: str | None = None) -> bytes:
    """Copia di `data` (PDF non cifrato) con i soli allegati cifrati."""
    from pyhanko.pdf_utils.crypt import CryptFilterConfiguration, StandardAESCryptFilter, StandardSecurityHandler
    from pyhanko.pdf_utils.generic import NameObject
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    if not password:
        raise ValueError(_("Scrivi la password per gli allegati."))
    cf = StandardAESCryptFilter(keylen=32)
    cf.set_embedded_only()                     # /AuthEvent /EFOpen
    config = CryptFilterConfiguration({NameObject(EFF_NAME): cf},
                                      default_stream_filter=NameObject("/Identity"),
                                      default_string_filter=NameObject("/Identity"),
                                      default_file_filter=NameObject(EFF_NAME))
    handler = StandardSecurityHandler.build_from_pw(owner_password or secrets.token_urlsafe(24), password,
                                                    pdf_mac=False, crypt_filter_config=config)
    writer = copy_into_new_writer(PdfFileReader(io.BytesIO(data)))
    writer._assign_security_handler(handler)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def is_envelope(pdf: pymupdf.Document) -> bool:
    """Il documento cifra solo gli allegati (/EFF con flussi e stringhe /Identity)."""
    try:
        kind, val = pdf.xref_get_key(-1, "Encrypt")
    except Exception:
        return False
    if kind == "null":
        return False
    get = (lambda k: pdf.xref_get_key(int(val.split()[0]), k)[1]) if kind == "xref" else \
        (lambda k: pdf.xref_get_key(-1, f"Encrypt/{k}")[1])
    return bool(get("EFF")) and get("EFF") != "null" and get("StmF") == "/Identity"


def cover_document(files: list[str]) -> pymupdf.Document:
    """Nuovo PDF con una pagina di copertina che elenca i file e li incorpora."""
    from foliant.core import placement

    pdf = pymupdf.open()
    page = pdf.new_page()
    placement.insert_text(page, (72, 90), _("Busta di sicurezza"), fontsize=20)
    placement.insert_textbox(page, pymupdf.Rect(72, 110, 523, 150),
                             _("Gli allegati di questo documento sono cifrati: per aprirli o estrarli serve la "
                               "password ricevuta dal mittente."), fontsize=10)
    y = 175
    for path in files:
        name = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()
        pdf.embfile_add(name if name not in pdf.embfile_names() else f"{len(pdf.embfile_names())}_{name}",
                        content, filename=name)
        if y < 780:
            placement.insert_text(page, (90, y), f"• {name}  ({_size(len(content))})", fontsize=11)
            y += 18
    return pdf


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
