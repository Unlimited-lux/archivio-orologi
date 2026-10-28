"""Cifratura con certificato (SEC-09), senza interfaccia.

Gestore di sicurezza a chiave pubblica di ISO 32000-2 §7.6.5 (/Filter /Adobe.PubSec,
/SubFilter /adbe.pkcs7.s5, crypt filter AESV3 con /Recipients come CMS EnvelopedData),
scritto e letto con pyHanko: il motore non sa né produrlo né aprirlo.

All'apertura il documento viene decifrato in memoria. Se il destinatario ha permessi
limitati, la copia in memoria viene ricifrata con il gestore standard (senza password
di apertura e con una password dei permessi casuale): così il nucleo applica gli stessi
permessi (SEC-02) senza che esista una copia in chiaro.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import secrets
from dataclasses import dataclass, field

import pymupdf

from foliant.api import _

from . import protection as P


class CertError(Exception):
    pass


@dataclass
class Recipient:
    cert: object                                # asn1crypto.x509.Certificate
    allowed: set[str] = field(default_factory=lambda: set(P.PERM_BITS))
    print_level: str = P.PRINT_HIGH
    mine: bool = False                          # certificato dell'utente (con chiave privata)

    @property
    def name(self) -> str:
        return cert_name(self.cert)


# ------------------------------------------------------------ certificati

def _to_asn1(cert_crypto):
    from asn1crypto import x509 as ax509
    from cryptography.hazmat.primitives import serialization
    return ax509.Certificate.load(cert_crypto.public_bytes(serialization.Encoding.DER))


def load_certificate(path: str, password: str | None = None) -> tuple[object, bool]:
    """Certificato da un file PEM/DER/CER/CRT o PKCS#12. Restituisce (certificato,
    ha_chiave_privata)."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        data = open(path, "rb").read()
    except OSError as e:
        raise CertError(_("Impossibile leggere il file: {e}").format(e=e.strerror)) from e
    if path.lower().endswith((".p12", ".pfx")):
        try:
            key, cert, _extra = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
        except ValueError as e:
            raise CertError(_("Password del file PKCS#12 errata o file non valido.")) from e
        if cert is None:
            raise CertError(_("Il file PKCS#12 non contiene un certificato."))
        return _to_asn1(cert), key is not None
    for loader in (x509.load_pem_x509_certificate, x509.load_der_x509_certificate):
        try:
            return _to_asn1(loader(data)), False
        except ValueError:
            continue
    raise CertError(_("Il file non contiene un certificato X.509 riconosciuto."))


def cert_name(cert) -> str:
    subject = cert.subject.native
    return subject.get("common_name") or subject.get("organization_name") or cert.subject.human_friendly


def cert_issuer(cert) -> str:
    issuer = cert.issuer.native
    return issuer.get("common_name") or issuer.get("organization_name") or cert.issuer.human_friendly


def cert_problems(cert, now: _dt.datetime | None = None) -> list[str]:
    """Motivi per cui il certificato non è adatto alla cifratura (scaduto, uso della chiave)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    out = []
    validity = cert["tbs_certificate"]["validity"]
    if validity["not_after"].native < now:
        out.append(_("Il certificato è scaduto il {d}.").format(d=validity["not_after"].native.strftime("%d/%m/%Y")))
    elif validity["not_before"].native > now:
        out.append(_("Il certificato non è ancora valido."))
    usage = cert.key_usage_value
    if usage is not None:
        names = set(usage.native)
        if not names & {"key_encipherment", "key_agreement"}:
            out.append(_("L'uso della chiave del certificato non prevede la cifratura."))
    return out


def _perms(allowed: set[str], print_level: str, full: bool):
    from pyhanko.pdf_utils.crypt.permissions import PubKeyPermissions as PP
    flags = PP.TOLERATE_MISSING_PDF_MAC           # il file non porta il MAC di ISO/TS 32004
    mapping = {"modify": PP.ALLOW_MODIFICATION_GENERIC, "copy": PP.ALLOW_CONTENT_EXTRACTION,
               "annotate": PP.ALLOW_ANNOTS_FORM_FILLING, "form": PP.ALLOW_FORM_FILLING,
               "accessibility": PP.ALLOW_ASSISTIVE_TECHNOLOGY, "assemble": PP.ALLOW_REASSEMBLY}
    allowed = set(allowed) | {"accessibility"}
    if "annotate" in allowed:
        allowed.add("form")
    for key, flag in mapping.items():
        if key in allowed:
            flags |= flag
    if print_level in (P.PRINT_LOW, P.PRINT_HIGH):
        flags |= PP.ALLOW_PRINTING
    if print_level == P.PRINT_HIGH:
        flags |= PP.ALLOW_HIGH_QUALITY_PRINTING
    if full:
        flags |= PP.ALLOW_ENCRYPTION_CHANGE
    return flags


def is_full(r: Recipient) -> bool:
    return r.print_level == P.PRINT_HIGH and set(P.PERM_BITS) <= (set(r.allowed) | {"accessibility"})


# -------------------------------------------------------------- cifratura

def encrypt_for(data: bytes, recipients: list[Recipient], encrypt_metadata: bool = True) -> bytes:
    """Copia di `data` (PDF non cifrato) cifrata per i destinatari, AES-256."""
    from pyhanko.pdf_utils.crypt import PubKeySecurityHandler
    from pyhanko.pdf_utils.crypt.pubkey import RecipientEncryptionPolicy
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    if not recipients:
        raise CertError(_("Aggiungi almeno un destinatario."))
    policy = RecipientEncryptionPolicy(ignore_key_usage=True)    # l'avviso è già stato dato
    first, *others = recipients
    handler = PubKeySecurityHandler.build_from_certs(
        [first.cert], keylen_bytes=32, perms=_perms(first.allowed, first.print_level, is_full(first)),
        encrypt_metadata=encrypt_metadata, policy=policy, pdf_mac=False)
    for r in others:
        handler.add_recipients([r.cert], perms=_perms(r.allowed, r.print_level, is_full(r)), policy=policy)
    writer = copy_into_new_writer(PdfFileReader(io.BytesIO(data)))
    writer._assign_security_handler(handler)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def is_pubkey(data: bytes) -> bool:
    head = data[:2_000_000]
    return re.search(rb"/Filter\s*/Adobe\.PubSec", head) is not None or \
        re.search(rb"/Filter\s*/Adobe\.PubSec", data[-2_000_000:]) is not None


def expected_recipients(data: bytes) -> list[str]:
    """«Emittente — numero di serie» dei destinatari previsti, per il messaggio d'errore.
    Le stringhe /Recipients del dizionario /Encrypt non sono cifrate: si leggono dal file."""
    from asn1crypto import cms
    out = []
    for block in re.finditer(rb"/Recipients\s*\[(.*?)\]", data, re.S):
        for hexa in re.findall(rb"<([0-9A-Fa-f\s]+)>", block.group(1)):
            try:
                info = cms.ContentInfo.load(bytes.fromhex(re.sub(rb"\s", b"", hexa).decode()))
                for ri in info["content"]["recipient_infos"]:
                    rid = ri.chosen["rid"].chosen
                    if isinstance(rid, cms.IssuerAndSerialNumber):
                        out.append(f"{rid['issuer'].human_friendly} — {rid['serial_number'].native:X}")
            except Exception:
                continue
    return list(dict.fromkeys(out))


@dataclass
class Decrypted:
    data: bytes                     # PDF da aprire (in chiaro o ricifrato con i permessi)
    full_access: bool
    allowed: set[str]
    print_level: str


def decrypt_with_pkcs12(data: bytes, pkcs12_path: str, password: str) -> Decrypted:
    from pyhanko.pdf_utils.crypt import AuthStatus, SimpleEnvelopeKeyDecrypter
    from pyhanko.pdf_utils.crypt.permissions import PubKeyPermissions as PP
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    credential = SimpleEnvelopeKeyDecrypter.load_pkcs12(pkcs12_path, password.encode() if password else None)
    if credential is None:
        raise CertError(_("Password del file PKCS#12 errata o file non valido."))
    reader = PdfFileReader(io.BytesIO(data))
    result = reader.decrypt_pubkey(credential)
    if result.status == AuthStatus.FAILED:
        expected = expected_recipients(data)
        msg = _("Nessuna chiave privata corrisponde ai destinatari del documento.")
        if expected:
            msg += "\n\n" + _("Destinatari previsti (emittente — numero di serie):") + "\n" + "\n".join(
                "• " + e for e in expected)
        raise CertError(msg)
    flags = result.permission_flags or PP(0)
    mapping = {"modify": PP.ALLOW_MODIFICATION_GENERIC, "copy": PP.ALLOW_CONTENT_EXTRACTION,
               "annotate": PP.ALLOW_ANNOTS_FORM_FILLING, "form": PP.ALLOW_FORM_FILLING,
               "accessibility": PP.ALLOW_ASSISTIVE_TECHNOLOGY, "assemble": PP.ALLOW_REASSEMBLY}
    allowed = {k for k, f in mapping.items() if flags & f}
    level = P.PRINT_HIGH if flags & PP.ALLOW_HIGH_QUALITY_PRINTING and flags & PP.ALLOW_PRINTING else \
        P.PRINT_LOW if flags & PP.ALLOW_PRINTING else P.PRINT_NONE
    writer = copy_into_new_writer(reader)
    out = io.BytesIO()
    writer.write(out)
    plain = out.getvalue()
    full = bool(flags & PP.ALLOW_ENCRYPTION_CHANGE) or (level == P.PRINT_HIGH and set(P.PERM_BITS) <= allowed)
    if full:
        return Decrypted(plain, True, allowed, level)
    # permessi limitati: la copia in memoria li porta con sé (gestore standard)
    settings = P.ProtectionSettings(permissions_password=secrets.token_urlsafe(24), restrict=True,
                                    allowed=allowed, print_level=level, method=P.AES256)
    pdf = pymupdf.open("pdf", plain)
    try:
        restricted = pdf.tobytes(**settings.save_options())
    finally:
        pdf.close()
    return Decrypted(restricted, False, allowed, level)
