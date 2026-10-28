"""Firma digitale PAdES, verifica e campi firma (logica pura, senza interfaccia).

Tutta la crittografia passa da pyHanko; i certificati autofirmati si creano
con `cryptography`. Le password e le chiavi private restano solo in memoria.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asn1crypto.tsp  # noqa: F401 - registra i tipi RFC 3161 (tst_info)
from asn1crypto import cms as asn1_cms
from asn1crypto import keys as asn1_keys
from asn1crypto import pkcs12 as asn1_pkcs12
from asn1crypto import x509 as asn1_x509
from cryptography import x509 as cx509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12 as c_pkcs12
from cryptography.x509.oid import ExtensionOID, NameOID

from foliant.core.engine import ENGINE_LOCK
from foliant.core.errors import FoliantError
from foliant import i18n
from foliant.i18n import _

if asn1_cms.ContentType._reverse_map is not None and "tst_info" not in asn1_cms.ContentType._reverse_map:
    asn1_cms.ContentType._reverse_map = None  # mappa inversa costruita prima della registrazione

__all__ = [
    "SignError", "WrongCertificatePassword", "CertificateNotUsable", "TimestampError",
    "SigningNotAllowed", "CertInfo", "DigitalID", "load_pkcs12", "check_usable",
    "create_self_signed", "export_certificate", "password_strength", "SignOptions",
    "sign_pdf", "sign_pdf_ex", "appearance_warnings", "FieldInfo", "signature_fields",
    "add_signature_field", "SignatureStatus", "verify_pdf", "signed_revision",
    "has_signatures", "strip_signatures", "certification_level", "summary",
    "VALID", "INVALID", "INDETERMINATE", "cert_info_from_der",
]

# --------------------------------------------------------------------------- #
# Errori
# --------------------------------------------------------------------------- #


class SignError(FoliantError):
    """Errore generico di firma digitale."""


class WrongCertificatePassword(SignError):
    def __init__(self, message: str | None = None):
        super().__init__(message or _("Password errata"))


class CertificateNotUsable(SignError):
    pass


class TimestampError(SignError):
    def __init__(self, message: str, kind: str = "rete"):
        super().__init__(message)
        self.kind = kind


class SigningNotAllowed(SignError):
    pass


# --------------------------------------------------------------------------- #
# Certificati
# --------------------------------------------------------------------------- #

_KU_NAMES = [
    ("digital_signature", "digitalSignature"),
    ("content_commitment", "nonRepudiation"),
    ("key_encipherment", "keyEncipherment"),
    ("data_encipherment", "dataEncipherment"),
    ("key_agreement", "keyAgreement"),
    ("key_cert_sign", "keyCertSign"),
    ("crl_sign", "cRLSign"),
]


def _fmt_date(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%d/%m/%Y") if dt else "—"


def _name_cn(name: cx509.Name) -> str:
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME, NameOID.EMAIL_ADDRESS):
        attrs = name.get_attributes_for_oid(oid)
        if attrs:
            return str(attrs[0].value)
    return name.rfc4514_string()


@dataclass
class CertInfo:
    subject: str
    subject_dn: str
    issuer: str
    issuer_dn: str
    serial: str
    not_before: datetime
    not_after: datetime
    key_usage: list[str]
    sha256: str
    sha1: str
    self_signed: bool
    email: str = ""
    der: bytes = field(default=b"", repr=False)
    policies: list[str] = field(default_factory=list)   # OID delle policy del certificato

    def is_valid_now(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.not_before <= now <= self.not_after

    def to_asn1(self) -> asn1_x509.Certificate:
        return asn1_x509.Certificate.load(self.der)

    def pem(self) -> str:
        cert = cx509.load_der_x509_certificate(self.der)
        return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _cert_info(cert: cx509.Certificate) -> CertInfo:
    der = cert.public_bytes(serialization.Encoding.DER)
    ku: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
        for attr, label in _KU_NAMES:
            if getattr(ext, attr):
                ku.append(label)
    except cx509.ExtensionNotFound:
        pass
    email = ""
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        mails = san.get_values_for_type(cx509.RFC822Name)
        if mails:
            email = mails[0]
    except cx509.ExtensionNotFound:
        pass
    policies: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.CERTIFICATE_POLICIES).value
        policies = [p.policy_identifier.dotted_string for p in ext]
    except cx509.ExtensionNotFound:
        pass
    if not email:
        attrs = cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)
        if attrs:
            email = str(attrs[0].value)
    return CertInfo(
        subject=_name_cn(cert.subject),
        subject_dn=cert.subject.rfc4514_string(),
        issuer=_name_cn(cert.issuer),
        issuer_dn=cert.issuer.rfc4514_string(),
        serial=format(cert.serial_number, "x"),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        key_usage=ku,
        sha256=cert.fingerprint(hashes.SHA256()).hex(),
        sha1=cert.fingerprint(hashes.SHA1()).hex(),  # noqa: S303 - solo impronta
        self_signed=cert.subject == cert.issuer,
        email=email,
        der=der,
        policies=policies,
    )


def cert_info_from_der(der: bytes) -> CertInfo:
    return _cert_info(cx509.load_der_x509_certificate(der))


def _to_cert_info(obj) -> CertInfo:
    if isinstance(obj, CertInfo):
        return obj
    if isinstance(obj, cx509.Certificate):
        return _cert_info(obj)
    if isinstance(obj, asn1_x509.Certificate):
        return cert_info_from_der(obj.dump())
    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
        if data.lstrip().startswith(b"-----BEGIN"):
            return _cert_info(cx509.load_pem_x509_certificate(data))
        return cert_info_from_der(data)
    raise SignError(_("Certificato non riconosciuto"))


class DigitalID:
    """ID digitale caricato in memoria (certificato + chiave). Mai serializzato."""

    def __init__(self, signer, info: CertInfo, chain: list[CertInfo], path: str | None = None):
        self._signer = signer
        self.info = info
        self.chain = chain
        self.path = path

    @property
    def signer(self):
        return self._signer

    def __getstate__(self):  # pragma: no cover - protezione
        raise TypeError("DigitalID non è serializzabile")

    def __repr__(self) -> str:
        return f"<DigitalID {self.info.subject!r} {self.info.sha256[:16]}>"


def load_pkcs12(source, password: str) -> DigitalID:
    path = None
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        path = str(source)
        try:
            data = Path(source).read_bytes()
        except OSError as exc:
            raise SignError(_("Impossibile leggere il file dell'ID digitale: {path}").format(path=path)) from exc
    try:
        pfx = asn1_pkcs12.Pfx.load(data)
        pfx["auth_safe"]["content_type"].native  # noqa: B018 - forza il parsing
        pfx.native  # noqa: B018
    except Exception as exc:
        raise SignError(_("Il file non è un ID digitale PKCS#12 (.p12/.pfx) valido")) from exc
    pw_candidates = [password.encode("utf-8")] if password else [None, b""]
    loaded = None
    for pw in pw_candidates:
        try:
            loaded = c_pkcs12.load_key_and_certificates(data, pw)
            break
        except Exception:  # noqa: BLE001
            continue
    if loaded is None:
        raise WrongCertificatePassword()
    key, cert, extra = loaded
    if cert is None:
        raise CertificateNotUsable(_("Il file non contiene un certificato"))
    if key is None:
        raise CertificateNotUsable(_("Il file non contiene la chiave privata del certificato"))
    from pyhanko.sign.signers import SimpleSigner
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    kinfo = asn1_keys.PrivateKeyInfo.load(
        key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption())
    )
    acert = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    store = SimpleCertificateStore()
    others = [asn1_x509.Certificate.load(c.public_bytes(serialization.Encoding.DER)) for c in (extra or [])]
    store.register_multiple(others)
    signer = SimpleSigner(signing_cert=acert, signing_key=kinfo, cert_registry=store)
    del kinfo
    info = _cert_info(cert)
    chain = [info] + [_cert_info(c) for c in (extra or [])]
    return DigitalID(signer, info, chain, path)


def check_usable(digital_id: DigitalID, *, allow_expired: bool = False) -> list[str]:
    info = digital_id.info
    warnings: list[str] = []
    now = datetime.now(timezone.utc)
    if now > info.not_after:
        msg = _("Il certificato è scaduto il {date} (valido dal {start} al {end})").format(
            date=_fmt_date(info.not_after), start=_fmt_date(info.not_before), end=_fmt_date(info.not_after))
        if not allow_expired:
            raise CertificateNotUsable(msg)
        warnings.append(msg + " — " + _("la firma risulterà non valida"))
    elif now < info.not_before:
        msg = _("Il certificato non è ancora valido: lo sarà dal {start} (validità fino al {end})").format(
            start=_fmt_date(info.not_before), end=_fmt_date(info.not_after))
        if not allow_expired:
            raise CertificateNotUsable(msg)
        warnings.append(msg)
    elif info.not_after - now < timedelta(days=30):
        warnings.append(_("Il certificato scadrà il {date}").format(date=_fmt_date(info.not_after)))
    if info.key_usage and not ({"digitalSignature", "nonRepudiation"} & set(info.key_usage)):
        raise CertificateNotUsable(
            _("Il certificato non è abilitato alla firma (manca l'uso chiave digitalSignature o nonRepudiation)"))
    if info.self_signed:
        warnings.append(_("Certificato autofirmato: gli altri non lo riconosceranno automaticamente"))
    return warnings


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    directory = path.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(directory))
    except OSError as exc:
        raise SignError(_("Impossibile scrivere nella cartella {path}").format(path=str(directory))) from exc
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SignError(_("Impossibile scrivere il file {path}").format(path=str(path))) from exc


PKCS12_KDF_ROUNDS = 600_000


def _add_years(dt: datetime, years: int) -> datetime:
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:  # 29 febbraio
        return dt.replace(year=dt.year + years, day=28)


def create_self_signed(path, password: str, *, common_name: str, organization: str = "", unit: str = "",
                       email: str = "", country: str = "", algorithm: str = "ec-p256", years: int = 3,
                       valid_from: datetime | None = None) -> CertInfo:
    """Crea un ID digitale autofirmato (SIG-13) e lo salva come PKCS#12 cifrato."""
    common_name = (common_name or "").strip()
    if not common_name:
        raise SignError(_("Il nome e cognome è obbligatorio"))
    if not password:
        raise SignError(_("La password del file è obbligatoria"))
    country = (country or "").strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise SignError(_("Il paese deve essere un codice di 2 lettere (es. IT)"))
    if not 1 <= int(years) <= 10:
        raise SignError(_("La validità deve essere compresa fra 1 e 10 anni"))
    if algorithm == "ec-p256":
        key = ec.generate_private_key(ec.SECP256R1())
    elif algorithm == "rsa-3072":
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    elif algorithm == "rsa-4096":
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    else:
        raise SignError(_("Algoritmo non supportato: {alg}").format(alg=algorithm))
    attrs = []
    if country:
        attrs.append(cx509.NameAttribute(NameOID.COUNTRY_NAME, country))
    if organization:
        attrs.append(cx509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    if unit:
        attrs.append(cx509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, unit))
    attrs.append(cx509.NameAttribute(NameOID.COMMON_NAME, common_name))
    name = cx509.Name(attrs)
    start = (valid_from or datetime.now(timezone.utc)).replace(microsecond=0)
    if valid_from is None:
        start -= timedelta(minutes=5)
    end = _add_years(start, int(years))
    serial = int.from_bytes(secrets.token_bytes(16), "big") >> 1 | (1 << 120)
    pub = key.public_key()
    builder = (
        cx509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(pub).serial_number(serial)
        .not_valid_before(start).not_valid_after(end)
        .add_extension(cx509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(cx509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                      data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                      crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(cx509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
        .add_extension(cx509.AuthorityKeyIdentifier.from_issuer_public_key(pub), critical=False)
    )
    if email:
        builder = builder.add_extension(cx509.SubjectAlternativeName([cx509.RFC822Name(email)]), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    enc = (
        serialization.PrivateFormat.PKCS12.encryption_builder()
        .kdf_rounds(PKCS12_KDF_ROUNDS)
        .key_cert_algorithm(c_pkcs12.PBES.PBESv2SHA256AndAES256CBC)
        .hmac_hash(hashes.SHA256())
        .build(password.encode("utf-8"))
    )
    blob = c_pkcs12.serialize_key_and_certificates(common_name.encode("utf-8"), key, cert, None, enc)
    _atomic_write(Path(path), blob)
    return _cert_info(cert)


def export_certificate(cert_info_or_id, path, fmt: str = "pem") -> None:
    info = cert_info_or_id.info if isinstance(cert_info_or_id, DigitalID) else _to_cert_info(cert_info_or_id)
    if fmt == "der":
        data = info.der
    elif fmt == "pem":
        data = info.pem().encode("ascii")
    else:
        raise SignError(_("Formato di esportazione non supportato: {fmt}").format(fmt=fmt))
    _atomic_write(Path(path), data, 0o644)


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456", "1234567", "12345678", "123456789", "1234567890",
    "qwerty", "qwertyuiop", "abc123", "111111", "000000", "letmein", "iloveyou", "admin", "welcome",
    "monkey", "dragon", "football", "baseball", "sunshine", "princess", "passw0rd", "trustno1",
    "ciao", "ciao1234", "ciaociao", "amore", "juventus", "napoli", "inter", "milan", "roma", "forzanapoli",
    "forzainter", "forzajuve", "ilovebe", "segreto", "segreta", "qwerty123", "asdfghjkl", "zxcvbnm",
    "firma", "firma123", "foliant", "changeme", "default",
}

_STRENGTH_LABELS = ["Molto debole", "Debole", "Discreta", "Buona", "Ottima"]


def password_strength(pw: str) -> tuple[int, str]:
    pw = pw or ""
    if len(pw) < 8 or pw.lower() in _COMMON_PASSWORDS or len(set(pw)) <= 2:
        score = 0 if len(pw) < 6 or pw.lower() in _COMMON_PASSWORDS else 1
        return score, _(_STRENGTH_LABELS[score])
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    score = 1
    if len(pw) >= 10 and classes >= 2:
        score = 2
    if len(pw) >= 12 and classes >= 3:
        score = 3
    if len(pw) >= 16 and classes >= 3:
        score = 4
    if len(pw) >= 20 and classes >= 2:
        score = max(score, 3)
    return score, _(_STRENGTH_LABELS[score])


# --------------------------------------------------------------------------- #
# Utilità PDF
# --------------------------------------------------------------------------- #


def _decrypt_with(pdf_reader, password: str | None):
    """Decifra con la password vuota e poi con quella del documento. Restituisce il
    risultato di pyHanko o solleva ValueError."""
    last = None
    for pw in ("", password) if password else ("",):
        try:
            result = pdf_reader.decrypt(pw)
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
        ok = getattr(result, "status", None)
        if ok is not None and getattr(ok, "name", "") == "FAILED":
            continue
        return result, pw
    raise ValueError("password") from last


def _check_sign_permission(auth) -> None:
    """Con accesso da utente (non proprietario) servono i permessi di compilazione dei
    moduli / firma: bit 6 (annotazioni e moduli) o bit 9 (solo compilazione dei moduli)."""
    status = getattr(getattr(auth, "status", None), "name", "")
    if status == "OWNER":
        return
    flags = getattr(auth, "permission_flags", None)
    if flags is None:
        return
    from pyhanko.pdf_utils.crypt.permissions import StandardPermissions as P
    if not (flags & P.ALLOW_ANNOTS_FORM_FILLING or flags & P.ALLOW_FORM_FILLING):
        raise SigningNotAllowed(_(
            "I permessi del documento non consentono di firmarlo né di compilarne i moduli. Riaprilo con la "
            "password del proprietario per firmarlo"))


def _open_writer(data: bytes, password: str | None):
    """IncrementalPdfFileWriter che mantiene la cifratura dell'originale. PyMuPDF scrive
    /Encrypt come dizionario diretto nel trailer: pyHanko lo vuole indiretto."""
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
    if writer.prev.encrypted:
        try:
            _r, pw = _decrypt_with(writer.prev, password)
        except ValueError as exc:
            raise SigningNotAllowed(_("Il documento è cifrato con una password: aprilo con la password per "
                                      "firmarlo")) from exc
        auth = writer.encrypt(pw)
        _check_sign_permission(auth)
        if writer._encrypt is not None and not isinstance(writer._encrypt, generic.IndirectObject):
            writer._encrypt = writer.add_object(writer._encrypt)
    return writer


def _reader(data: bytes, password: str | None = None):
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001
        raise SigningNotAllowed(_("Il file è danneggiato o non è un PDF valido")) from exc
    if reader.encrypted:
        try:
            _decrypt_with(reader, password)
        except ValueError as exc:
            raise SigningNotAllowed(
                _("Il documento è cifrato con una password: aprilo con la password per firmarlo")) from exc
    return reader


def _ref_key(ref) -> tuple[int, int]:
    if hasattr(ref, "reference"):
        ref = ref.reference
    return (int(ref.idnum), int(ref.generation))


def _page_refs(root) -> list:
    """Elenco degli IndirectObject delle pagine, in ordine."""
    out = []
    seen = set()

    def walk(node_ref):
        key = _ref_key(node_ref)
        if key in seen:
            return
        seen.add(key)
        node = node_ref.get_object()
        if node.get("/Type") == "/Pages" or "/Kids" in node:
            for kid in node.raw_get("/Kids") if "/Kids" in node else []:
                walk(kid)
        else:
            out.append(node_ref)

    walk(root.raw_get("/Pages"))
    return out


def _deref(obj):
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _inherited(page, key):
    node = _deref(page)
    for _i in range(64):
        if key in node:
            return node[key]
        if "/Parent" not in node:
            return None
        node = node["/Parent"]
    return None


def _page_box(page) -> tuple[float, float, float, float]:
    box = _inherited(page, "/CropBox") or _inherited(page, "/MediaBox") or [0, 0, 612, 792]
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _page_rotation(page) -> int:
    rot = _inherited(page, "/Rotate")
    try:
        return int(rot or 0) % 360
    except (TypeError, ValueError):
        return 0


def _to_pdf_rect(page, rect) -> tuple[float, float, float, float]:
    bx0, _by0, _bx1, by1 = _page_box(page)
    x0, y0, x1, y1 = rect
    x0, x1 = sorted((float(x0), float(x1)))
    y0, y1 = sorted((float(y0), float(y1)))
    return (bx0 + x0, by1 - y1, bx0 + x1, by1 - y0)


def _from_pdf_rect(page, rect) -> tuple[float, float, float, float]:
    bx0, _by0, _bx1, by1 = _page_box(page)
    x0, y0, x1, y1 = (float(v) for v in rect)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return (x0 - bx0, by1 - y1, x1 - bx0, by1 - y0)


def _field_widget(field_obj):
    if "/Rect" in field_obj or "/Kids" not in field_obj:
        return field_obj
    kids = field_obj["/Kids"]
    return kids[0] if len(kids) else field_obj


def _existing_field_names(reader) -> set[str]:
    names: set[str] = set()

    def walk(fields, prefix=""):
        for f in fields:
            fo = f.get_object()
            t = fo.get("/T")
            name = f"{prefix}.{t}" if prefix and t is not None else (str(t) if t is not None else prefix)
            if t is not None:
                names.add(name)
            if "/Kids" in fo:
                walk(fo["/Kids"], name)

    try:
        walk(reader.root["/AcroForm"]["/Fields"])
    except KeyError:
        pass
    return names


def _unique_name(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _auto_name(existing: set[str]) -> str:
    n = 1
    while f"Firma{n}" in existing:
        n += 1
    return f"Firma{n}"


# --------------------------------------------------------------------------- #
# Aspetto della firma visibile
# --------------------------------------------------------------------------- #

MIN_FONT = 6.0
ELLIPSIS = "…"


def _text_width(text: str, size: float) -> float:
    import pymupdf

    n_ell = text.count(ELLIPSIS)  # in Helvetica "…" è largo 1000/1000
    with ENGINE_LOCK:
        return pymupdf.get_text_length(text.replace(ELLIPSIS, ""), fontname="helv", fontsize=size) + n_ell * size


def _truncate(text: str, width: float, size: float, measure=None) -> str:
    measure = measure or _text_width
    if measure(text, size) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid].rstrip() + ELLIPSIS, size) <= width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ELLIPSIS) if lo > 0 else ""


def _fit_text(lines: list[str], w: float, h: float, start: float = 10.0, leading: float = 1.2, measure=None):
    """Restituisce (corpo, righe, troncato)."""
    measure = measure or _text_width
    lines = [ln for ln in lines if ln]
    if not lines or w <= 0 or h <= 0:
        return start, [], False
    size = float(start)
    while size >= MIN_FONT:
        if len(lines) * size * leading <= h and all(measure(ln, size) <= w for ln in lines):
            return size, lines, False
        size -= 0.5
    size = MIN_FONT
    max_lines = max(int(h // (size * leading)), 0)
    out = [_truncate(ln, w, size, measure) for ln in lines[:max_lines]]
    if max_lines < len(lines) and out:
        out[-1] = _truncate(out[-1] + ELLIPSIS if not out[-1].endswith(ELLIPSIS) else out[-1], w, size, measure)
    return size, [ln for ln in out if ln], True


def appearance_warnings(text_lines, box_w, box_h) -> list[str]:
    warnings = []
    if box_w < 40 or box_h < 15:
        warnings.append(_("Riquadro molto piccolo (meno di 40×15 pt): la firma potrebbe essere poco leggibile"))
    pad = 2.0
    _size, _out, truncated = _fit_text(list(text_lines), box_w - 2 * pad, box_h - 2 * pad)
    if truncated:
        warnings.append(_("Il testo non entra nel riquadro ed è stato troncato"))
    return warnings


def _pdf_hex(text: str) -> bytes:
    return b"<" + text.encode("cp1252", errors="replace").hex().encode("ascii") + b">"


APPEARANCE_LABELS = {
    "it": {"nome": "Firmato digitalmente da ", "data": "Data: ", "motivo": "Motivo: ", "luogo": "Luogo: ",
           "dn": "DN: "},
    "en": {"nome": "Digitally signed by ", "data": "Date: ", "motivo": "Reason: ", "luogo": "Location: ",
           "dn": "DN: "},
}
DATE_FORMATS = {
    "gg/mm/aaaa hh:mm:ss": "%d/%m/%Y %H:%M:%S %z",
    "gg/mm/aaaa hh:mm": "%d/%m/%Y %H:%M %Z",
    "aaaa-mm-gg hh:mm:ss": "%Y-%m-%d %H:%M:%S %z",
    "gg/mm/aaaa": "%d/%m/%Y",
}


def _appearance_lines(info: CertInfo, opts: "SignOptions", now: datetime, show: list[str],
                      app: dict | None = None) -> list[str]:
    """Righe di testo dell'aspetto. `app` può indicare "label_language" ("it"/"en"; se manca
    vale la lingua dell'interfaccia), "date_format" (chiave di DATE_FORMATS o formato strftime)
    e "utc" (bool)."""
    app = app or {}
    labels = "etichette" in show
    lang = app.get("label_language") or i18n.language()
    lab = APPEARANCE_LABELS.get(lang, APPEARANCE_LABELS["en"])
    fmt = app.get("date_format") or "%d/%m/%Y %H:%M:%S %z"
    fmt = DATE_FORMATS.get(fmt, fmt)
    if app.get("utc"):
        now = now.astimezone(timezone.utc)
    lines = []
    if "nome" in show:
        lines.append((lab["nome"] if labels else "") + info.subject)
    if "data" in show:
        lines.append((lab["data"] if labels else "") + now.strftime(fmt).strip())
    if "motivo" in show and opts.reason:
        lines.append((lab["motivo"] if labels else "") + opts.reason)
    if "luogo" in show and opts.location:
        lines.append((lab["luogo"] if labels else "") + opts.location)
    if "dn" in show:
        lines.append((lab["dn"] if labels else "") + info.subject_dn)
    return lines


class _FoliantStampStyle:
    """Stile di aspetto compatibile con pyHanko (duck typing di BaseStampStyle)."""

    def __init__(self, info: CertInfo, opts: "SignOptions", rotation: int):
        self.info = info
        self.opts = opts
        self.rotation = rotation
        self.warnings: list[str] = []

    def create_stamp(self, writer, box, text_params):
        return _FoliantStamp(self, writer, float(box.width), float(box.height))


class _FoliantStamp:
    def __init__(self, style: _FoliantStampStyle, writer, w: float, h: float):
        self.style, self.writer, self.w, self.h = style, writer, w, h

    def _image_xobject(self, png: bytes):
        from PIL import Image
        from pyhanko.pdf_utils import generic

        im = Image.open(io.BytesIO(png)).convert("RGBA")
        if max(im.size) > 1500:
            im.thumbnail((1500, 1500))
        rgb = im.convert("RGB").tobytes()
        alpha = im.getchannel("A").tobytes()

        def img_stream(raw, cs):
            d = generic.DictionaryObject({
                generic.NameObject("/Type"): generic.NameObject("/XObject"),
                generic.NameObject("/Subtype"): generic.NameObject("/Image"),
                generic.NameObject("/Width"): generic.NumberObject(im.width),
                generic.NameObject("/Height"): generic.NumberObject(im.height),
                generic.NameObject("/ColorSpace"): generic.NameObject(cs),
                generic.NameObject("/BitsPerComponent"): generic.NumberObject(8),
                generic.NameObject("/Filter"): generic.NameObject("/FlateDecode"),
            })
            return generic.StreamObject(d, encoded_data=zlib.compress(raw))

        smask = self.writer.add_object(img_stream(alpha, "/DeviceGray"))
        main = img_stream(rgb, "/DeviceRGB")
        main[generic.NameObject("/SMask")] = smask
        return self.writer.add_object(main), im.width / max(im.height, 1)

    def apply_appearance(self, sig_annot):
        from pyhanko.pdf_utils import generic

        st = self.style
        opts = st.opts
        app = dict(opts.appearance or {})
        layout = app.get("layout", "testo")
        show = list(app.get("show") or ["nome", "data", "motivo", "etichette"])
        png = app.get("image_png")
        font_size = float(app.get("font_size") or 10)
        rot = st.rotation
        W, H = (self.h, self.w) if rot in (90, 270) else (self.w, self.h)
        now = datetime.now().astimezone()
        lines = _appearance_lines(st.info, opts, now, show, app)
        pad = 2.0
        cmds: list[bytes] = []
        xobjects = {}
        text_area = (pad, pad, W - 2 * pad, H - 2 * pad)  # x, y, w, h
        if layout in ("grafica", "grafica_testo") and png:
            img_ref, ratio = self._image_xobject(png)
            xobjects["/Im1"] = img_ref
            area_w = W - 2 * pad if layout == "grafica" else (W - 3 * pad) / 2
            area_h = H - 2 * pad
            iw, ih = area_w, area_w / ratio
            if ih > area_h:
                ih, iw = area_h, area_h * ratio
            ix = pad + (area_w - iw) / 2
            iy = pad + (area_h - ih) / 2
            cmds.append(b"q %.3f 0 0 %.3f %.3f %.3f cm /Im1 Do Q" % (iw, ih, ix, iy))
            text_area = None if layout == "grafica" else (2 * pad + area_w, pad, area_w, area_h)
        elif layout == "grafica" and not png:
            lines = [st.info.subject]
        text_blocks = []
        if layout == "nome_grande" and text_area:
            half = (W - 3 * pad) / 2
            text_blocks.append(([st.info.subject], (pad, pad, half, H - 2 * pad), 72.0, True))
            details = [ln for ln in lines if not ln.endswith(st.info.subject)] or lines
            text_blocks.append((details, (2 * pad + half, pad, half, H - 2 * pad), font_size, False))
        elif text_area:
            text_blocks.append((lines, text_area, font_size, False))
        from .fonts import font_for, needs_unicode_font
        all_lines = [ln for blk in text_blocks for ln in blk[0]]
        ufont = font_for(all_lines) if needs_unicode_font(all_lines) else None
        if ufont is None and needs_unicode_font(all_lines):
            st.warnings.append(_("Nessun font disponibile copre tutti i caratteri del testo"))
        measure = (lambda t, sz: ufont.width(t, sz)) if ufont else _text_width
        encode = ufont.encode if ufont else _pdf_hex
        for blk_lines, (tx, ty, tw, th), start, centered in text_blocks:
            size, out, truncated = _fit_text(blk_lines, tw, th, start, measure=measure)
            if truncated:
                st.warnings.append(_("Il testo non entra nel riquadro ed è stato troncato"))
            if not out:
                continue
            lead = size * 1.2
            total = lead * len(out)
            y = ty + th - (th - total) / 2 - size if centered else ty + th - size
            cmds.append(b"BT 0 0 0 rg /F1 %.2f Tf" % size)
            for ln in out:
                cmds.append(b"1 0 0 1 %.3f %.3f Tm %s Tj" % (tx, y + 0.2 * size, encode(ln)))
                y -= lead
            cmds.append(b"ET")
        if W < 40 or H < 15:
            st.warnings.append(
                _("Riquadro molto piccolo (meno di 40×15 pt): la firma potrebbe essere poco leggibile"))
        if rot == 90:
            matrix = [0, 1, -1, 0, self.w, 0]
        elif rot == 180:
            matrix = [-1, 0, 0, -1, self.w, self.h]
        elif rot == 270:
            matrix = [0, -1, 1, 0, 0, self.h]
        else:
            matrix = [1, 0, 0, 1, 0, 0]
        if ufont is not None and ufont.used:
            font = ufont.pdf_font(self.writer)
        else:
            font = generic.DictionaryObject({
                generic.NameObject("/Type"): generic.NameObject("/Font"),
                generic.NameObject("/Subtype"): generic.NameObject("/Type1"),
                generic.NameObject("/BaseFont"): generic.NameObject("/Helvetica"),
                generic.NameObject("/Encoding"): generic.NameObject("/WinAnsiEncoding"),
            })
        resources = generic.DictionaryObject({
            generic.NameObject("/Font"): generic.DictionaryObject({generic.NameObject("/F1"): font}),
        })
        if xobjects:
            resources[generic.NameObject("/XObject")] = generic.DictionaryObject(
                {generic.NameObject(k): v for k, v in xobjects.items()})
        form = generic.StreamObject(generic.DictionaryObject({
            generic.NameObject("/Type"): generic.NameObject("/XObject"),
            generic.NameObject("/Subtype"): generic.NameObject("/Form"),
            generic.NameObject("/BBox"): generic.ArrayObject([generic.FloatObject(v) for v in (0, 0, W, H)]),
            generic.NameObject("/Matrix"): generic.ArrayObject([generic.FloatObject(v) for v in matrix]),
            generic.NameObject("/Resources"): resources,
        }), stream_data=b"\n".join(cmds))
        ref = self.writer.add_object(form)
        sig_annot[generic.NameObject("/AP")] = generic.DictionaryObject({generic.NameObject("/N"): ref})


# --------------------------------------------------------------------------- #
# Firma
# --------------------------------------------------------------------------- #

_DOCMDP_DESC = {
    1: "nessuna modifica",
    2: "compilazione dei moduli e firme",
    3: "compilazione dei moduli, firme e annotazioni",
}


@dataclass
class SignOptions:
    field_name: str | None = None
    page: int = 0
    rect: tuple[float, float, float, float] | None = None
    reason: str = ""
    location: str = ""
    contact: str = ""
    appearance: dict | None = None
    timestamp_url: str | None = None
    timestamp_timeout: float = 15.0
    timestamp_auth: tuple[str, str] | None = None    # (utente, password) per la TSA; mai salvati
    timestamper: object | None = None
    certify: bool = False
    docmdp_level: int = 2
    lock_fields: list[str] | None = None
    md_algorithm: str = "sha256"
    allow_expired: bool = False
    password: str | None = None    # password del documento cifrato (mai salvata)


def _timestamp_error(exc: BaseException) -> TimestampError:
    from pyhanko.sign.timestamps.common_utils import TimestampRequestError

    chain = []
    e: BaseException | None = exc
    while e is not None and len(chain) < 10:
        chain.append(e)
        e = e.__cause__ or e.__context__
    for e in chain:
        if getattr(e, "status", None) in (401, 403) or " 401" in str(e) or "Unauthorized" in str(e):
            return TimestampError(_("Il server di marca temporale ha rifiutato le credenziali"), "credenziali")
    for e in chain:
        if isinstance(e, TimestampRequestError):
            msg = str(e)
            if e.__cause__ is not None and isinstance(e.__cause__, OSError):
                return TimestampError(
                    _("Server di marca temporale irraggiungibile o non ha risposto in tempo"), "rete")
            if "refused" in msg:
                detail = msg.split("statusString", 1)[-1].strip(' :"')
                return TimestampError(
                    _("Il server di marca temporale ha rifiutato la richiesta: {detail}").format(detail=detail),
                    "rifiuto")
            return TimestampError(_("La marca temporale ricevuta non è coerente ed è stata scartata"), "token")
    for e in chain:
        if isinstance(e, OSError):
            return TimestampError(
                _("Server di marca temporale irraggiungibile o non ha risposto in tempo"), "rete")
    return TimestampError(_("La marca temporale ricevuta non è coerente ed è stata scartata"), "token")


def _check_field_constraints(sv, digital_id: "DigitalID", options: "SignOptions") -> bool:
    """Verifica i vincoli /SV del campo su motivo, policy ed emittente. Restituisce True
    se il campo ha un vincolo sull'emittente o sulla policy (che verifichiamo noi)."""
    if sv is None:
        return False
    reasons = [str(r) for r in (_deref(sv.get("/Reasons")) or [])]
    if reasons and (options.reason or "") not in reasons:
        raise SigningNotAllowed(_("Il campo ammette solo questi motivi: {r}").format(r="; ".join(reasons)))
    cert = _deref(sv.get("/Cert"))
    if cert is not None and "/OID" in cert:
        oids = [str(o) for o in _deref(cert.get("/OID"))]
        if oids and not set(oids) & set(digital_id.info.policies):
            raise SigningNotAllowed(_("Il campo richiede un certificato con una di queste policy: {oids}").format(
                oids=", ".join(oids)))
    if cert is None or "/Issuer" not in cert:
        return cert is not None and "/OID" in cert   # policy verificata qui: pyHanko non la gestisce
    allowed = [cert_info_from_der(bytes(_deref(x))) for x in _deref(cert.get("/Issuer"))]
    chain = [digital_id.info, *digital_id.chain]
    ok_dn = {c.subject_dn for c in allowed}
    ok_fp = {c.sha256 for c in allowed}
    if not any(c.issuer_dn in ok_dn or c.sha256 in ok_fp for c in chain):
        raise SigningNotAllowed(_("Il campo non accetta certificati emessi da «{who}»").format(
            who=digital_id.info.issuer))
    return True


def sign_pdf(data: bytes, digital_id: DigitalID, options: SignOptions) -> bytes:
    return sign_pdf_ex(data, digital_id, options)[0]


def sign_pdf_ex(data: bytes, digital_id: DigitalID, options: SignOptions) -> tuple[bytes, list[str]]:
    """Firma `data` e restituisce (byte firmati, avvisi)."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields as pfields
    from pyhanko.sign import signers
    from pyhanko.sign.signers.pdf_byterange import BuildProps
    from pyhanko.sign.timestamps import HTTPTimeStamper
    from pyhanko.sign.validation.pdf_embedded import read_certification_data

    from foliant import __version__

    data = bytes(data)
    warnings = check_usable(digital_id, allow_expired=options.allow_expired)
    reader = _reader(data, options.password)
    try:
        writer = _open_writer(data, options.password)
    except SigningNotAllowed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SigningNotAllowed(_("Il file è danneggiato: non può essere firmato in modo sicuro")) from exc
    if getattr(reader, "xrefs", None) is not None and getattr(reader, "_xref_reconstructed", False):
        raise SigningNotAllowed(_("Il file è danneggiato: non può essere firmato in modo sicuro"))

    existing_sigs = []
    try:
        from pyhanko.sign.fields import enumerate_sig_fields

        existing_sigs = list(enumerate_sig_fields(reader, filled_status=True))
    except Exception:  # noqa: BLE001
        existing_sigs = []
    cert_data = None
    try:
        cert_data = read_certification_data(reader)
    except Exception:  # noqa: BLE001
        cert_data = None
    if cert_data is not None and cert_data.permission.value == 1:
        raise SigningNotAllowed(_(
            "Il documento è certificato con livello «nessuna modifica»: non è possibile aggiungere altre firme"))
    if options.certify and existing_sigs:
        raise SigningNotAllowed(_(
            "La firma di certificazione deve essere la prima firma del documento: il documento è già firmato"))
    if options.docmdp_level not in (1, 2, 3):
        raise SignError(_("Livello di certificazione non valido"))

    own_sv_check = False
    names = _existing_field_names(reader)
    sig_fields = {name: fref for name, _v, fref in pfields.enumerate_sig_fields(reader)}
    new_field_spec = None
    sv_ts_url = None
    pages = _page_refs(reader.root)
    if options.field_name and options.field_name in sig_fields:
        field_obj = sig_fields[options.field_name].get_object()
        if "/V" in field_obj:
            raise SigningNotAllowed(
                _("Il campo «{name}» è già firmato: usa un nuovo campo").format(name=options.field_name))
        if _locked_field_names(reader) & {options.field_name}:
            raise SigningNotAllowed(
                _("Il campo «{name}» è bloccato da una firma precedente").format(name=options.field_name))
        field_name = options.field_name
        widget = _field_widget(field_obj)
        page_obj = _deref(widget.get("/P"))
        rotation = _page_rotation(page_obj) if page_obj is not None else 0
        if page_obj is None:
            for p in pages:
                annots = p.get_object().get("/Annots") or []
                if any(_ref_key(a) == _ref_key(sig_fields[field_name]) for a in annots if hasattr(a, "idnum")):
                    rotation = _page_rotation(p.get_object())
                    break
        if options.lock_fields:
            warnings.append(_("Il blocco dei campi si imposta solo per un nuovo campo firma"))
        sv = _deref(field_obj.get("/SV"))
        ts_spec = _deref(sv.get("/TimeStamp")) if sv is not None else None
        if ts_spec is not None and int(ts_spec.get("/Ff", 0) or 0) & 1 and "/URL" in ts_spec:
            sv_ts_url = str(ts_spec["/URL"])
        own_sv_check = _check_field_constraints(sv, digital_id, options)
        if sv is not None and "/DigestMethod" in sv:
            allowed_md = [str(x).lstrip("/").lower().replace("-", "") for x in _deref(sv.get("/DigestMethod"))]
            if allowed_md and (options.md_algorithm or "sha256").lower() not in allowed_md:
                options.md_algorithm = allowed_md[0]
    else:
        if options.field_name and options.field_name in names:
            raise SigningNotAllowed(
                _("Esiste già un campo «{name}» che non è un campo firma").format(name=options.field_name))
        field_name = options.field_name or _auto_name(names)
        if not 0 <= options.page < len(pages):
            raise SignError(_("Pagina {n} inesistente").format(n=options.page + 1))
        page_obj = pages[options.page].get_object()
        rotation = _page_rotation(page_obj)
        box = _to_pdf_rect(page_obj, options.rect) if options.rect else None
        if box is not None and (box[2] - box[0] <= 0 or box[3] - box[1] <= 0):
            box = None
        fmdp = None
        if options.lock_fields:
            fmdp = pfields.FieldMDPSpec(action=pfields.FieldMDPAction.INCLUDE, fields=list(options.lock_fields))
        new_field_spec = pfields.SigFieldSpec(
            sig_field_name=field_name, on_page=options.page, box=box, field_mdp_spec=fmdp)

    md = (options.md_algorithm or "sha256").lower()
    if md not in ("sha256", "sha384", "sha512"):
        raise SignError(_("Algoritmo di hash non supportato: {alg}").format(alg=md))
    meta = signers.PdfSignatureMetadata(
        field_name=field_name,
        md_algorithm=md,
        reason=options.reason or None,
        location=options.location or None,
        contact_info=options.contact or None,
        name=digital_id.info.subject,
        certify=bool(options.certify),
        docmdp_permissions=pfields.MDPPerm(options.docmdp_level),
        subfilter=pfields.SigSeedSubFilter.PADES,
        app_build_props=BuildProps(name="Foliant", revision=__version__),
    )
    timestamper = options.timestamper
    ts_url = options.timestamp_url or sv_ts_url
    if timestamper is None and ts_url:
        timestamper = HTTPTimeStamper(ts_url, timeout=options.timestamp_timeout,
                                      auth=tuple(options.timestamp_auth) if options.timestamp_auth else None)
    style = _FoliantStampStyle(digital_id.info, options, rotation)
    pdf_signer = signers.PdfSigner(meta, digital_id.signer, timestamper=timestamper, stamp_style=style,
                                   new_field_spec=new_field_spec)
    if own_sv_check:
        # vincoli su emittente e policy già verificati qui (pyHanko richiederebbe un percorso di
        # validazione completo); la marca temporale obbligatoria è gestita sopra con sv_ts_url
        pdf_signer._ignore_sv = True
    out = io.BytesIO()
    try:
        pdf_signer.sign_pdf(writer, output=out)
    except SignError:
        raise
    except Exception as exc:  # noqa: BLE001
        if timestamper is not None and _is_timestamp_failure(exc):
            raise _timestamp_error(exc) from exc
        raise SignError(_("Firma non riuscita: {detail}").format(detail=str(exc) or type(exc).__name__)) from exc
    result = out.getvalue()
    if not result.startswith(data):  # garanzia incrementale
        raise SignError(_("Firma non riuscita: il salvataggio non è incrementale"))
    for w in style.warnings:
        if w not in warnings:
            warnings.append(w)
    return result, warnings


def _is_timestamp_failure(exc: BaseException) -> bool:
    from pyhanko.sign.timestamps.common_utils import TimestampRequestError

    e: BaseException | None = exc
    for _i in range(10):
        if e is None:
            return False
        if isinstance(e, (TimestampRequestError, OSError)):
            return True
        e = e.__cause__ or e.__context__
    return False


# --------------------------------------------------------------------------- #
# Campi firma
# --------------------------------------------------------------------------- #


@dataclass
class FieldInfo:
    name: str
    page: int | None
    rect: tuple[float, float, float, float] | None
    signed: bool
    hidden: bool
    required: bool
    invisible: bool
    locked: bool
    timestamp_required_url: str | None = None
    tooltip: str = ""
    allowed_reasons: list[str] | None = None        # /SV /Reasons
    allowed_issuers: list[CertInfo] | None = None   # /SV /Cert /Issuer
    certify_level: int | None = None                # /SV /MDP /P: il campo richiede una certificazione
    digest_methods: list[str] | None = None         # /SV /DigestMethod
    allowed_policies: list[str] | None = None       # /SV /Cert /OID


def _locked_field_names(reader) -> set[str]:
    """Campi bloccati da riferimenti FieldMDP di firme esistenti."""
    from pyhanko.sign.fields import enumerate_sig_fields

    all_names = _existing_field_names(reader)
    locked: set[str] = set()
    try:
        for _name, sig_ref, _fref in enumerate_sig_fields(reader, filled_status=True):
            sig = sig_ref.get_object() if sig_ref is not None else None
            if sig is None:
                continue
            for ref in (sig["/Reference"] if "/Reference" in sig else []):
                ref = ref.get_object()
                if ref.get("/TransformMethod") != "/FieldMDP":
                    continue
                params = _deref(ref.get("/TransformParams")) or {}
                action = str(params.get("/Action", "/All"))
                fields_ = {str(f) for f in (params.get("/Fields") or [])}
                if action == "/All":
                    locked |= all_names
                elif action == "/Include":
                    locked |= fields_
                elif action == "/Exclude":
                    locked |= all_names - fields_
    except Exception:  # noqa: BLE001
        pass
    return locked


def _annot_page_index(reader, annot_ref, pages) -> int | None:
    key = _ref_key(annot_ref)
    for i, p in enumerate(pages):
        annots = p.get_object().get("/Annots")
        if annots is None:
            continue
        try:
            arr = annots.get_object()
        except AttributeError:
            arr = annots
        for a in arr.raw_get_all() if hasattr(arr, "raw_get_all") else _raw_items(arr):
            if hasattr(a, "idnum") or hasattr(a, "reference"):
                if _ref_key(a) == key:
                    return i
    return None


def _raw_items(arr):
    try:
        return [arr.raw_get(i) for i in range(len(arr))]
    except Exception:  # noqa: BLE001
        return list(arr)


def signature_fields(data: bytes, password: str | None = None) -> list[FieldInfo]:
    from pyhanko.sign.fields import enumerate_sig_fields

    reader = _reader(bytes(data), password)
    pages = _page_refs(reader.root)
    locked = _locked_field_names(reader)
    out = []
    for name, value, fref in enumerate_sig_fields(reader):
        fobj = fref.get_object()
        widget = fobj
        widget_ref = fref
        if "/Rect" not in fobj and "/Kids" in fobj:
            kids = fobj.raw_get("/Kids")
            if len(kids):
                widget_ref = _raw_items(kids)[0]
                widget = widget_ref.get_object()
        page_idx = None
        p = widget.raw_get("/P") if "/P" in widget else None
        if p is not None:
            pk = _ref_key(p)
            for i, pr in enumerate(pages):
                if _ref_key(pr) == pk:
                    page_idx = i
                    break
        if page_idx is None:
            page_idx = _annot_page_index(reader, widget_ref, pages)
        rect = None
        invisible = True
        if "/Rect" in widget:
            r = [float(v) for v in widget["/Rect"]]
            invisible = abs(r[2] - r[0]) < 1e-6 or abs(r[3] - r[1]) < 1e-6
            if page_idx is not None:
                rect = _from_pdf_rect(pages[page_idx].get_object(), r)
            else:
                rect = tuple(r)
        flags = int(widget.get("/F", 0) or 0)
        ff = int(fobj.get("/Ff", 0) or 0)
        ts_url = None
        reasons = issuers = mdp = digests = policies = None
        sv = _deref(fobj.get("/SV"))
        if sv is not None:
            ts = _deref(sv.get("/TimeStamp"))
            if ts is not None and int(ts.get("/Ff", 0) or 0) & 1 and "/URL" in ts:
                ts_url = str(ts["/URL"])
            try:
                if "/Reasons" in sv:
                    reasons = [str(r) for r in _deref(sv.get("/Reasons"))] or None
                if "/DigestMethod" in sv:
                    digests = [str(x).lstrip("/").lower().replace("-", "") for x in _deref(sv.get("/DigestMethod"))]
                mdp_d = _deref(sv.get("/MDP"))
                if mdp_d is not None and int(mdp_d.get("/P", 0) or 0) in (1, 2, 3):
                    mdp = int(mdp_d.get("/P"))
                cert = _deref(sv.get("/Cert"))
                if cert is not None and "/Issuer" in cert:
                    issuers = [cert_info_from_der(bytes(_deref(x))) for x in _deref(cert.get("/Issuer"))] or None
                if cert is not None and "/OID" in cert:
                    policies = [str(o) for o in _deref(cert.get("/OID"))] or None
            except Exception:  # noqa: BLE001 - vincoli illeggibili: ignorati
                pass
        out.append(FieldInfo(
            name=name, page=page_idx, rect=rect, signed=value is not None,
            hidden=bool(flags & 2), required=bool(ff & 2), invisible=invisible,
            locked=name in locked, timestamp_required_url=ts_url, tooltip=str(fobj.get("/TU", "") or ""),
            allowed_reasons=reasons, allowed_issuers=issuers, certify_level=mdp, digest_methods=digests or None,
            allowed_policies=policies,
        ))
    out.sort(key=lambda f: (f.page if f.page is not None else 1 << 30,
                            f.rect[1] if f.rect else 0, f.rect[0] if f.rect else 0))
    return out


def _cert_constraints_class():
    from pyhanko.pdf_utils import generic
    from pyhanko.sign import fields as pfields

    class CertConstraints(pfields.SigCertConstraints):
        """/SV /Cert con anche /OID (le policy ammesse), che pyHanko non scrive."""
        policies: list[str] = []

        def as_pdf_object(self):
            out = super().as_pdf_object()
            if self.policies:
                out[generic.NameObject("/OID")] = generic.ArrayObject(
                    generic.TextStringObject(o) for o in self.policies)
            return out
    return CertConstraints


def _CertConstraints(**kw):  # noqa: N802
    return _cert_constraints_class()(**kw)


def add_signature_field(data, name, page, rect, *, required=False, tooltip="", timestamp_url=None,
                        timestamp_required=False, lock_fields=None, lock_action="Include",
                        hidden=False, allowed_reasons=None, allowed_issuers=None,
                        certify_level=None, digest_methods=None, allowed_policies=None,
                        password=None) -> tuple[bytes, str]:
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields as pfields

    data = bytes(data)
    reader = _reader(data, password)
    lvl = None
    try:
        lvl = certification_level(data)
    except Exception:  # noqa: BLE001
        lvl = None
    if lvl == 1:
        raise SigningNotAllowed(_(
            "Il documento è certificato con livello «nessuna modifica»: non è possibile aggiungere campi"))
    writer = _open_writer(data, password)
    names = _existing_field_names(reader)
    base = (name or "").strip() or _auto_name(names)
    eff = _unique_name(base, names)
    pages = _page_refs(reader.root)
    if not 0 <= page < len(pages):
        raise SignError(_("Pagina {n} inesistente").format(n=page + 1))
    page_obj = pages[page].get_object()
    box = _to_pdf_rect(page_obj, rect) if rect else None
    sv = None
    if digest_methods:
        bad = [m for m in digest_methods if m not in ("sha256", "sha384", "sha512")]
        if bad:
            raise SignError(_("Algoritmo di hash non supportato: {alg}").format(alg=", ".join(bad)))
    policies = [str(o).strip() for o in (allowed_policies or []) if str(o).strip()]
    bad = [o for o in policies if not re.fullmatch(r"[0-2](\.\d+)+", o)]
    if bad:
        raise SignError(_("OID di policy non valido: {oid}").format(oid=", ".join(bad)))
    if timestamp_url or allowed_reasons or allowed_issuers or certify_level or digest_methods or policies:
        flags = pfields.SigSeedValFlags(0)
        cert = None
        if allowed_reasons:
            flags |= pfields.SigSeedValFlags.REASONS
        if digest_methods:
            flags |= pfields.SigSeedValFlags.DIGEST_METHOD
        if allowed_issuers or policies:
            cflags = pfields.SigCertConstraintFlags(0)
            if allowed_issuers:
                cflags |= pfields.SigCertConstraintFlags.ISSUER
            if policies:
                cflags |= pfields.SigCertConstraintFlags.OID
            cert = _CertConstraints(
                flags=cflags, issuers=[_to_cert_info(c).to_asn1() for c in allowed_issuers] if allowed_issuers
                else None)
            cert.policies = policies
        sv = pfields.SigSeedValueSpec(
            flags=flags, reasons=list(allowed_reasons) if allowed_reasons else None,
            timestamp_server_url=timestamp_url or None,
            timestamp_required=bool(timestamp_required and timestamp_url), cert=cert,
            seed_signature_type=pfields.SeedSignatureType(pfields.MDPPerm(int(certify_level)))
            if certify_level else None,
            digest_methods=list(digest_methods) if digest_methods else None)
    fmdp = None
    if lock_fields is not None or lock_action == "All":
        action = {"All": pfields.FieldMDPAction.ALL, "Include": pfields.FieldMDPAction.INCLUDE,
                  "Exclude": pfields.FieldMDPAction.EXCLUDE}[lock_action]
        fmdp = pfields.FieldMDPSpec(action=action, fields=None if action == pfields.FieldMDPAction.ALL
                                    else list(lock_fields or []))
    spec = pfields.SigFieldSpec(
        sig_field_name=eff, on_page=page, box=box, seed_value_dict=sv, field_mdp_spec=fmdp,
        readable_field_name=tooltip or None, empty_field_appearance=True,
        invis_sig_settings=pfields.InvisSigSettings(set_print_flag=not hidden, set_hidden_flag=hidden),
    )
    try:
        pfields.append_signature_field(writer, spec)
    except Exception as exc:  # noqa: BLE001
        raise SignError(_("Impossibile creare il campo firma: {detail}").format(detail=str(exc))) from exc
    if required or (hidden and box is not None):
        for fname, _v, fref in pfields.enumerate_sig_fields(writer):
            if fname != eff:
                continue
            fobj = fref.get_object()
            if required:
                fobj[generic.NameObject("/Ff")] = generic.NumberObject(int(fobj.get("/Ff", 0) or 0) | 2)
            if hidden:
                w = _field_widget(fobj)
                w[generic.NameObject("/F")] = generic.NumberObject((int(w.get("/F", 0) or 0) | 2) & ~4)
            writer.update_container(fobj)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), eff


# --------------------------------------------------------------------------- #
# Verifica
# --------------------------------------------------------------------------- #

VALID = "valida"
INVALID = "non valida"
INDETERMINATE = "indeterminata"
_LABELS = {VALID: "Valida", INVALID: "Non valida", INDETERMINATE: "Indeterminata"}


@dataclass
class SignatureStatus:
    field_name: str
    status: str = INDETERMINATE
    label: str = "Indeterminata"
    reasons: list[str] = field(default_factory=list)
    signer: str = ""
    signer_cert: CertInfo | None = None
    chain: list[CertInfo] = field(default_factory=list)
    signing_time: datetime | None = None
    time_source: str = "dichiarata"
    kind: str = "approvazione"
    docmdp_level: int | None = None
    pades_level: str | None = None
    reason: str = ""
    location: str = ""
    contact: str = ""
    page: int | None = None
    rect: tuple | None = None
    invisible: bool = True
    intact: bool = False
    crypto_ok: bool = False
    trusted: bool = False
    modifications: str = "Nessuna modifica"
    modification_details: list[str] = field(default_factory=list)
    revision_end: int = 0
    timestamp_tsa: str | None = None
    md_algorithm: str = ""


def _set_status(st: SignatureStatus, status: str) -> None:
    st.status = status
    st.label = _(_LABELS[status])


def _plural(n: int, one: str, many: str) -> str:
    return (one if n == 1 else many).format(n=n)


def _analyze_changes(reader, signed_rev: int) -> tuple[list[str], list[str]]:
    """Descrive le modifiche dopo la revisione firmata: (consentite, non consentite)."""
    total = reader.xrefs.total_revisions
    if signed_rev >= total - 1:
        return [], []
    try:
        old = reader.get_historical_resolver(signed_rev)
        old_pages = _page_refs(old.root)
    except Exception:  # noqa: BLE001
        old_pages = []
    pages = _page_refs(reader.root)
    page_index = {_ref_key(p): i for i, p in enumerate(pages)}
    content_refs: dict[tuple, int] = {}
    annot_page: dict[tuple, int] = {}
    for plist in (pages, old_pages):
        for i, p in enumerate(plist):
            po = p.get_object()
            if "/Contents" in po:
                c = po.raw_get("/Contents")
                if hasattr(c, "idnum") or hasattr(c, "reference"):
                    content_refs.setdefault(_ref_key(c), i)
                    cobj = c.get_object()
                    if isinstance(cobj, list):
                        for item in _raw_items(cobj):
                            if hasattr(item, "idnum") or hasattr(item, "reference"):
                                content_refs.setdefault(_ref_key(item), i)
                elif isinstance(c, list):
                    for item in _raw_items(c):
                        content_refs.setdefault(_ref_key(item), i)
            if "/Annots" in po:
                try:
                    arr = po["/Annots"]
                    for a in _raw_items(arr):
                        if hasattr(a, "idnum") or hasattr(a, "reference"):
                            annot_page.setdefault(_ref_key(a), i)
                except Exception:  # noqa: BLE001
                    pass
    changed = set()
    for rev in range(signed_rev + 1, total):
        changed |= {_ref_key(r) for r in reader.xrefs.explicit_refs_in_revision(rev)}
    from pyhanko.pdf_utils import generic

    added_annots: dict[int, int] = {}
    modified_annots: dict[int, int] = {}
    content_pages: set[int] = set()
    page_struct: set[int] = set()
    sigs = 0
    form_fill = 0
    for key in sorted(changed):
        ref = generic.Reference(key[0], key[1], reader)
        try:
            obj = reader.get_object(ref)
        except Exception:  # noqa: BLE001
            continue
        intro = reader.xrefs.get_introducing_revision(ref)
        is_new = intro > signed_rev
        if key in page_index:
            if is_new:
                page_struct.add(page_index[key])
                continue
            try:
                old_page = old.get_object(ref)
            except Exception:  # noqa: BLE001
                continue
            if repr(old_page.raw_get("/Contents") if "/Contents" in old_page else None) != repr(
                    obj.raw_get("/Contents") if "/Contents" in obj else None):
                content_pages.add(page_index[key])
            other_old = {k for k in old_page.keys() if k not in ("/Annots",)}
            other_new = {k for k in obj.keys() if k not in ("/Annots",)}
            if other_old != other_new:
                page_struct.add(page_index[key])
            continue
        if key in content_refs:
            content_pages.add(content_refs[key])
            continue
        if not isinstance(obj, generic.DictionaryObject):
            continue
        typ = obj.get("/Type")
        sub = obj.get("/Subtype")
        if typ in ("/Sig", "/DocTimeStamp"):
            sigs += 1
            continue
        if sub == "/Widget" or "/FT" in obj:
            parent = obj["/Parent"] if "/Parent" in obj else None   # [] risolve il riferimento indiretto
            ft = obj.get("/FT") or (parent.get("/FT") if isinstance(parent, generic.DictionaryObject) else None)
            if ft != "/Sig" and not is_new:
                form_fill += 1
            continue
        if sub == "/Popup":
            continue
        if sub is not None and "/Rect" in obj and sub not in ("/Form", "/Image"):
            pg = annot_page.get(key)
            if pg is None and "/P" in obj:
                pg = page_index.get(_ref_key(obj.raw_get("/P")))
            pg = pg if pg is not None else 0
            target = added_annots if is_new else modified_annots
            target[pg] = target.get(pg, 0) + 1
    allowed: list[str] = []
    forbidden: list[str] = []
    if sigs:
        allowed.append(_plural(sigs, _("{n} firma aggiunta"), _("{n} firme aggiunte")))
    if form_fill:
        allowed.append(_("compilazione di campi modulo"))
    for pg, n in sorted(added_annots.items()):
        allowed.append(_plural(n, _("{n} annotazione aggiunta"), _("{n} annotazioni aggiunte"))
                       + " " + _("a pagina {p}").format(p=pg + 1))
    for pg, n in sorted(modified_annots.items()):
        allowed.append(_plural(n, _("{n} annotazione modificata"), _("{n} annotazioni modificate"))
                       + " " + _("a pagina {p}").format(p=pg + 1))
    for pg in sorted(content_pages):
        forbidden.append(_("contenuto della pagina {p}").format(p=pg + 1))
    for pg in sorted(page_struct - content_pages):
        forbidden.append(_("struttura della pagina {p}").format(p=pg + 1))
    return allowed, forbidden


_DIFF_POLICY = None


def _diff_policy():
    """Politica di pyHanko estesa: annotazioni (non widget) aggiunte/modificate = livello ANNOTATIONS."""
    global _DIFF_POLICY
    if _DIFF_POLICY is not None:
        return _DIFF_POLICY
    from pyhanko.pdf_utils import generic
    from pyhanko.sign.diff_analysis import DEFAULT_DIFF_POLICY, ModificationLevel, StandardDiffPolicy
    from pyhanko.sign.diff_analysis.rules_api import QualifiedWhitelistRule, ReferenceUpdate

    def is_ref(x):
        return isinstance(x, generic.IndirectObject)

    class AnnotationRule(QualifiedWhitelistRule):
        def apply_qualified(self, old, new):
            new_refs = {_ref_key(r): r for r in new.explicit_refs_in_revision()}
            xc = old.reader.xrefs

            def existed(ref):
                return xc.get_historical_ref(ref, old.revision) is not None

            def fresh_closure(obj, acc, depth=0):
                if depth > 20:
                    return
                if is_ref(obj):
                    key = _ref_key(obj)
                    if key in new_refs and key not in acc and not existed(new_refs[key]):
                        acc[key] = new_refs[key]
                        fresh_closure(obj.get_object(), acc, depth + 1)
                    return
                if isinstance(obj, generic.DictionaryObject):
                    for k in obj.keys():
                        if k in ("/P", "/Parent"):
                            continue
                        fresh_closure(obj.raw_get(k), acc, depth + 1)
                elif isinstance(obj, generic.ArrayObject):
                    for item in _raw_items(obj):
                        fresh_closure(item, acc, depth + 1)

            def is_plain_annot(o):
                return (isinstance(o, generic.DictionaryObject) and "/Subtype" in o and "/Rect" in o
                        and o.get("/Subtype") != "/Widget" and "/FT" not in o)

            try:
                pages = _page_refs(new.root)
            except Exception:  # noqa: BLE001
                return
            for pref in pages:
                key = _ref_key(pref)
                page_new = pref.get_object()
                if "/Annots" not in page_new:
                    continue
                annots_new_raw = page_new.raw_get("/Annots")
                arr = annots_new_raw.get_object() if is_ref(annots_new_raw) else annots_new_raw
                updates = []
                for a in _raw_items(arr):
                    if not is_ref(a):
                        continue
                    akey = _ref_key(a)
                    if akey not in new_refs:
                        continue
                    obj = a.get_object()
                    if not is_plain_annot(obj):
                        continue
                    acc: dict = {}
                    updates.append(new_refs[akey])
                    fresh_closure(obj, acc)
                    updates.extend(acc.values())
                if not updates:
                    continue  # nessuna annotazione (non widget) toccata: decide pyHanko
                if key in new_refs and existed(new_refs[key]):
                    page_old = old(new_refs[key])
                    keys_old = set(page_old.keys()) - {"/Annots"}
                    keys_new = set(page_new.keys()) - {"/Annots"}
                    if keys_old != keys_new or any(
                            repr(page_old.raw_get(k)) != repr(page_new.raw_get(k)) for k in keys_new):
                        continue
                    updates.append(new_refs[key])
                if is_ref(annots_new_raw) and _ref_key(annots_new_raw) in new_refs:
                    updates.append(new_refs[_ref_key(annots_new_raw)])
                for r in updates:
                    yield ModificationLevel.ANNOTATIONS, ReferenceUpdate(r)

    class WidgetChildFillRule(QualifiedWhitelistRule):
        """Compilazione di un campo con struttura padre + widget figlio (widget senza /T, con
        /Parent): pyHanko giustifica il nuovo aspetto solo sul dizionario del campo. Qui si
        accetta il widget figlio se, tolte le chiavi d'aspetto, è invariato, insieme alle nuove
        dipendenze del suo aspetto (livello FORM_FILLING, come la compilazione del campo)."""

        def apply_qualified(self, old, new):
            from pyhanko.sign.diff_analysis.rules.form_field_rules import _allow_appearance_update
            xc = old.reader.xrefs
            for ref in new.explicit_refs_in_revision():
                try:
                    if xc.get_historical_ref(ref, old.revision) is None:
                        continue
                    obj_new = new(ref)
                    obj_old = old(ref)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(obj_new, generic.DictionaryObject) or not isinstance(obj_old,
                                                                                       generic.DictionaryObject):
                    continue
                if obj_new.get("/Subtype") != "/Widget" or "/T" in obj_new or "/Parent" not in obj_new:
                    continue
                parent = obj_new["/Parent"]
                if not isinstance(parent, generic.DictionaryObject) or "/T" not in parent:
                    continue
                ft = parent.get("/FT")
                if ft is None or ft == "/Sig" or "/Kids" not in parent:
                    continue
                # solo chiavi d'aspetto: /AP e /AS, più /BS e /MK che il motore riscrive
                # rigenerando l'aspetto (es. /BS /W 0 → 1)
                appearance_keys = {"/AP", "/AS", "/BS", "/MK"}
                keys_old = set(obj_old.keys()) - appearance_keys
                keys_new = set(obj_new.keys()) - appearance_keys
                if keys_old != keys_new or any(repr(obj_old.raw_get(k)) != repr(obj_new.raw_get(k))
                                               for k in keys_new):
                    continue
                try:
                    deps = list(_allow_appearance_update(obj_old, obj_new, old, new))
                except Exception:  # noqa: BLE001 - aspetto sospetto: decide pyHanko
                    continue
                yield ModificationLevel.FORM_FILLING, ReferenceUpdate(ref)
                for dep in deps:
                    yield ModificationLevel.FORM_FILLING, ReferenceUpdate(dep)

    base = DEFAULT_DIFF_POLICY
    _DIFF_POLICY = StandardDiffPolicy(
        global_rules=list(base.global_rules) + [AnnotationRule(), WidgetChildFillRule()],
        form_rule=base.form_rule,
    )
    return _DIFF_POLICY


def _trusted_der(trusted) -> list[bytes]:
    out = []
    for t in trusted or ():
        try:
            out.append(_to_cert_info(t).der)
        except Exception:  # noqa: BLE001
            continue
    return out


def _embedded_signature_class(base):
    """EmbeddedPdfSignature che nasconde a pyHanko il vincolo /SV /Cert /OID (non supportato:
    solleverebbe NotImplementedError); la policy la controlla `_verify_one`."""
    import dataclasses

    from pyhanko.sign.fields import SigCertConstraintFlags

    class Embedded(base):
        @property
        def seed_value_spec(self):
            spec = super().seed_value_spec
            if spec is not None and spec.cert is not None and spec.cert.flags & SigCertConstraintFlags.OID:
                spec = dataclasses.replace(spec, cert=dataclasses.replace(
                    spec.cert, flags=spec.cert.flags & ~SigCertConstraintFlags.OID))
            return spec
    return Embedded


def verify_pdf(data: bytes, *, trusted=(), allow_fetching: bool = False,
               revocation_mode: str = "mai", password: str | None = None) -> list[SignatureStatus]:
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.fields import enumerate_sig_fields
    from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
    from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature
    from pyhanko.sign.validation.settings import KeyUsageConstraints
    from pyhanko.sign.validation.status import SignatureCoverageLevel
    from pyhanko_certvalidator import ValidationContext

    data = bytes(data)
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        if reader.encrypted:
            _decrypt_with(reader, password)
        fields_list = list(enumerate_sig_fields(reader, filled_status=True))
    except Exception:  # noqa: BLE001
        return []
    roots = [asn1_x509.Certificate.load(d) for d in _trusted_der(trusted)]
    online = revocation_mode == "sempre" and allow_fetching
    finfo = {}
    try:
        finfo = {f.name: f for f in signature_fields(data, password)}
    except Exception:  # noqa: BLE001
        pass
    has_dss = "/DSS" in reader.root
    results: list[SignatureStatus] = []
    embedded = []
    for name, sig_ref, fref in fields_list:
        st = SignatureStatus(field_name=name)
        fi = finfo.get(name)
        if fi is not None:
            st.page, st.rect, st.invisible = fi.page, fi.rect, fi.invisible
        try:
            sig = sig_ref.get_object()

            def val(key, default):   # sig[...] decifra; sig.get() restituirebbe un proxy cifrato
                return sig[key] if key in sig else default

            st.reason = str(val("/Reason", "") or "")
            st.location = str(val("/Location", "") or "")
            st.contact = str(val("/ContactInfo", "") or "")
            br = [int(v) for v in val("/ByteRange", [])]
            if len(br) == 4:
                st.revision_end = br[2] + br[3]
            emb = _embedded_signature_class(EmbeddedPdfSignature)(reader, fref, name)
        except Exception as exc:  # noqa: BLE001
            _set_status(st, INVALID)
            st.reasons.append(_("Formato della firma non valido") + f" ({type(exc).__name__})")
            results.append(st)
            continue
        embedded.append((st, emb))
        results.append(st)

    doc_ts_revs = [emb.signed_revision for _s, emb in embedded if emb.sig_object_type == "/DocTimeStamp"]
    for st, emb in embedded:
        try:
            _verify_one(st, emb, reader, roots, online, has_dss, doc_ts_revs,
                        ValidationContext, KeyUsageConstraints, SignatureCoverageLevel,
                        validate_pdf_signature, validate_pdf_timestamp)
        except Exception as exc:  # noqa: BLE001
            _set_status(st, INVALID if not st.intact else INDETERMINATE)
            st.reasons.append(_("Impossibile verificare la firma: {detail}").format(
                detail=str(exc) or type(exc).__name__))
        fi = finfo.get(st.field_name)
        wanted = getattr(fi, "allowed_policies", None)
        if wanted and st.signer_cert is not None and not set(wanted) & set(st.signer_cert.policies):
            # come per gli altri vincoli /SV, la firma che non li rispetta non è valida
            _set_status(st, INVALID)
            st.reasons.append(_("Il certificato non ha la policy richiesta dal campo ({oids})").format(
                oids=", ".join(wanted)))
    results.sort(key=lambda s: s.revision_end)
    return results


def _verify_one(st, emb, reader, roots, online, has_dss, doc_ts_revs, ValidationContext,
                KeyUsageConstraints, SignatureCoverageLevel, validate_pdf_signature, validate_pdf_timestamp):
    from pyhanko.sign.diff_analysis import SuspiciousModification

    def vc():
        return ValidationContext(trust_roots=list(roots), allow_fetching=online,
                                 revocation_mode="hard-fail" if online else "soft-fail")

    is_doc_ts = emb.sig_object_type == "/DocTimeStamp"
    try:
        lvl = emb.docmdp_level
    except Exception:  # noqa: BLE001
        lvl = None
    if is_doc_ts:
        st.kind = "marca temporale del documento"
        status = validate_pdf_timestamp(emb, validation_context=vc())
    else:
        st.kind = "certificazione" if lvl is not None else "approvazione"
        st.docmdp_level = lvl.value if lvl is not None else None
        ku = KeyUsageConstraints(key_usage={"digital_signature", "non_repudiation"}, match_all_key_usages=False)
        status = validate_pdf_signature(emb, signer_validation_context=vc(), key_usage_settings=ku,
                                        diff_policy=_diff_policy())
    cert = status.signing_cert
    st.signer_cert = cert_info_from_der(cert.dump())
    st.signer = st.signer_cert.subject
    path = status.validation_path
    if path is not None:
        try:
            st.chain = [cert_info_from_der(c.dump()) for c in reversed(list(path.iter_certs(include_root=True)))]
        except Exception:  # noqa: BLE001
            st.chain = [st.signer_cert]
    else:
        st.chain = [st.signer_cert]
    st.intact = bool(status.intact)
    st.crypto_ok = bool(status.intact and status.valid)
    st.trusted = bool(status.trusted)
    # SIG-16: con la verifica online «rigida» pyHanko non distingue la revoca non verificabile
    # (responder irraggiungibile) da una catena non attendibile; si riprova senza revoca
    revocation_unknown = False
    if online and not st.trusted and not getattr(status, "revoked", False) and st.crypto_ok:
        try:
            soft = ValidationContext(trust_roots=list(roots), allow_fetching=False, revocation_mode="soft-fail")
            if is_doc_ts:
                retry = validate_pdf_timestamp(emb, validation_context=soft)
            else:
                retry = validate_pdf_signature(emb, signer_validation_context=soft, key_usage_settings=ku,
                                               diff_policy=_diff_policy())
            revocation_unknown = bool(retry.trusted)
        except Exception:  # noqa: BLE001
            revocation_unknown = False
    st.md_algorithm = getattr(emb, "md_algorithm", "") or ""
    reasons: list[str] = []
    # tempo
    tsv = getattr(status, "timestamp_validity", None)
    if is_doc_ts:
        st.signing_time = status.timestamp
        st.time_source = "marca temporale"
        st.timestamp_tsa = st.signer
    elif tsv is not None:
        st.signing_time = tsv.timestamp
        st.time_source = "marca temporale"
        try:
            st.timestamp_tsa = cert_info_from_der(tsv.signing_cert.dump()).subject
        except Exception:  # noqa: BLE001
            st.timestamp_tsa = None
    else:
        st.signing_time = getattr(status, "signer_reported_dt", None) or emb.self_reported_timestamp
        st.time_source = "dichiarata"
    # livello PAdES
    sub = str(emb.sig_object.get("/SubFilter", ""))
    if is_doc_ts:
        st.pades_level = None
    elif sub == "/ETSI.CAdES.detached":
        level = "B-T" if tsv is not None else "B-B"
        if has_dss and level == "B-T":
            level = "B-LT"
            if any(r > emb.signed_revision for r in doc_ts_revs):
                level = "B-LTA"
        st.pades_level = level
    # integrità
    coverage = getattr(status, "coverage", None)
    bad_format = coverage is not None and coverage < SignatureCoverageLevel.ENTIRE_REVISION
    if not status.intact:
        reasons.append(_("Il documento è stato alterato dopo la firma"))
    elif not status.valid:
        reasons.append(_("Il valore della firma non è crittograficamente valido"))
    elif bad_format:
        reasons.append(_("Intervallo di byte firmati non valido (/ByteRange)"))
    if st.md_algorithm in ("sha1", "md5"):
        reasons.append(_("Algoritmo debole ({alg})").format(alg=st.md_algorithm.upper().replace("SHA", "SHA-")))
    # modifiche
    allowed, forbidden = [], []
    try:
        allowed, forbidden = _analyze_changes(reader, emb.signed_revision)
    except Exception:  # noqa: BLE001
        pass
    docmdp_ok = getattr(status, "docmdp_ok", None)
    diff = getattr(status, "diff_result", None)
    mod_bad = docmdp_ok is False or isinstance(diff, SuspiciousModification)
    if mod_bad:
        if not forbidden:
            forbidden = [_("modifiche al documento non ammesse")]
        st.modifications = _("Modifiche non consentite: ") + ", ".join(forbidden)
        st.modification_details = forbidden + allowed
    elif allowed or forbidden or emb.signed_revision < reader.xrefs.total_revisions - 1:
        items = allowed or [_("aggiornamenti successivi")]
        st.modifications = _("Modifiche consentite: ") + ", ".join(items)
        st.modification_details = items
    else:
        st.modifications = _("Nessuna modifica")
    # validità temporale del certificato del firmatario
    expiry_problem = None
    cert = st.signer_cert
    if cert is not None and not is_doc_ts:
        now_utc = datetime.now(timezone.utc)
        ts_proof = (tsv is not None and bool(getattr(tsv, "intact", False)) and bool(getattr(tsv, "valid", False))
                    and st.signing_time is not None)
        if ts_proof:
            t = st.signing_time if st.signing_time.tzinfo else st.signing_time.replace(tzinfo=timezone.utc)
            if t > cert.not_after:
                expiry_problem = ("invalid", _(
                    "Certificato scaduto il {date}, prima dell'ora attestata dalla marca temporale ({ts})").format(
                    date=_fmt_date(cert.not_after), ts=_fmt_date(t)))
            elif t < cert.not_before:
                expiry_problem = ("invalid", _(
                    "Certificato non ancora valido all'ora attestata dalla marca temporale ({ts})").format(
                    ts=_fmt_date(t)))
        elif now_utc > cert.not_after or now_utc < cert.not_before:
            expiry_problem = ("indeterminate", _(
                "Certificato scaduto il {date} senza prova dell'ora della firma").format(
                date=_fmt_date(cert.not_after)) if now_utc > cert.not_after else
                _("Certificato non ancora valido e nessuna prova dell'ora della firma"))
    # esito
    if not st.crypto_ok or bad_format:
        _set_status(st, INVALID)
    elif mod_bad:
        _set_status(st, INVALID)
        reasons.append(st.modifications)
        reasons.append(_("Versione firmata integra"))
    elif getattr(status, "revoked", False):
        _set_status(st, INVALID)
        rd = getattr(status, "revocation_details", None)
        when = _fmt_date(getattr(rd, "revocation_date", None)) if rd else "—"
        reasons.append(_("Certificato revocato il {date}").format(date=when))
    elif expiry_problem is not None:
        # validità temporale del certificato (SIG-15): decide prima della fiducia
        kind, msg = expiry_problem
        _set_status(st, INVALID if kind == "invalid" else INDETERMINATE)
        if kind != "invalid":
            reasons.append(_("Integrità confermata: il documento non è stato alterato"))
        reasons.append(msg)
    elif not st.trusted:
        _set_status(st, INDETERMINATE)
        reasons.append(_("Integrità confermata: il documento non è stato alterato"))
        indic = getattr(status, "trust_problem_indic", None)
        indic_name = getattr(indic, "name", "") or ""
        now = datetime.now(timezone.utc)
        if revocation_unknown or "REVOC" in indic_name or "TRY_LATER" in indic_name:
            reasons.append(_("Stato di revoca non verificabile"))
        elif st.signer_cert and (now > st.signer_cert.not_after or "EXPIRED" in indic_name
                                 or "OUT_OF_BOUNDS" in indic_name):
            reasons.append(_("Certificato scaduto il {date} senza prova dell'ora della firma").format(
                date=_fmt_date(st.signer_cert.not_after)))
        else:
            reasons.append(_("Identità del firmatario non attendibile"))
    else:
        _set_status(st, VALID)
        if st.signer_cert and datetime.now(timezone.utc) > st.signer_cert.not_after and st.time_source == \
                "marca temporale":
            reasons.append(_("Certificato scaduto dopo l'ora attestata dalla marca temporale"))
    if not is_doc_ts and st.time_source == "dichiarata":
        reasons.append(_("Ora dichiarata dal firmatario"))
    st.reasons = reasons + [r for r in st.reasons if r not in reasons]


def signed_revision(data: bytes, status: SignatureStatus) -> bytes:
    end = int(status.revision_end)
    if end <= 0 or end > len(data):
        raise SignError(_("Impossibile ricavare la versione firmata"))
    return bytes(data[:end])


def has_signatures(data) -> bool:
    from pyhanko.sign.fields import enumerate_sig_fields

    try:
        reader = _reader(bytes(data))
        return any(True for _x in enumerate_sig_fields(reader, filled_status=True))
    except Exception:  # noqa: BLE001
        return False


def certification_level(data, password: str | None = None) -> int | None:
    from pyhanko.sign.validation.pdf_embedded import read_certification_data

    try:
        reader = _reader(bytes(data), password)
        info = read_certification_data(reader)
    except Exception:  # noqa: BLE001
        return None
    return info.permission.value if info is not None else None


def strip_signatures(data) -> bytes:
    """SIG-21: copia senza firme (salvataggio completo, campi firma vuoti, senza /Perms e /DSS)."""
    import pymupdf

    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=bytes(data), filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise SignError(_("Il file è danneggiato o non è un PDF valido")) from exc
        try:
            if doc.needs_pass and not doc.authenticate(""):
                raise SignError(_("Il documento è protetto da password."))
            touched: set[int] = set()

            def drop(xref: int, key: str) -> None:
                if doc.xref_get_key(xref, key)[0] != "null":
                    doc.xref_set_key(xref, key, "null")
                    touched.add(xref)

            for xref in range(1, doc.xref_length()):
                try:
                    ft = doc.xref_get_key(xref, "FT")
                except Exception:  # noqa: BLE001
                    continue
                if ft[1] != "/Sig":
                    continue
                drop(xref, "V")
                drop(xref, "AP")
                kids = doc.xref_get_key(xref, "Kids")
                if kids[0] == "array":
                    for k in re.findall(r"(\d+) 0 R", kids[1]):
                        drop(int(k), "AP")
            cat = doc.pdf_catalog()
            drop(cat, "Perms")
            drop(cat, "DSS")
            for xref in touched:  # rimuove del tutto le chiavi azzerate
                src = doc.xref_object(xref, compressed=False)
                cleaned = re.sub(r"/(V|AP|Perms|DSS)\s+null\b", "", src)
                if cleaned != src:
                    doc.update_object(xref, cleaned)
            return doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()


def summary(statuses, empty_fields) -> str:
    statuses = list(statuses or [])
    pending = [f for f in (empty_fields or []) if not getattr(f, "signed", False) and not getattr(f, "hidden", False)]
    req = ""
    if pending:
        n = len(pending)
        req = _("1 firma richiesta") if n == 1 else _("{n} firme richieste").format(n=n)
    if not statuses:
        return req or _("Nessuna firma")
    if any(s.status != VALID for s in statuses):
        text = _("Almeno una firma presenta problemi")
    else:
        cert = next((s for s in statuses if s.kind == "certificazione"), None)
        if cert is not None and cert.docmdp_level:
            text = _("Certificato da {name} — consentito: {what}").format(
                name=cert.signer, what=_(_DOCMDP_DESC.get(cert.docmdp_level, "")))
        else:
            text = _("Firmato: tutte le firme sono valide")
    return f"{text} — {req}" if req else text


TRANSLATIONS_EN = {
    "Il certificato non ha la policy richiesta dal campo ({oids})":
        "The certificate does not have the policy required by the field ({oids})",
    "Il campo richiede un certificato con una di queste policy: {oids}":
        "The field requires a certificate with one of these policies: {oids}",
    "OID di policy non valido: {oid}": "Invalid policy OID: {oid}",
    "I permessi del documento non consentono di firmarlo né di compilarne i moduli. Riaprilo con la password del "
    "proprietario per firmarlo": "The document permissions do not allow signing it or filling its forms. Reopen it "
                                  "with the owner password to sign it",
    "Certificato scaduto il {date}, prima dell'ora attestata dalla marca temporale ({ts})":
        "Certificate expired on {date}, before the time attested by the timestamp ({ts})",
    "Certificato non ancora valido all'ora attestata dalla marca temporale ({ts})":
        "Certificate not yet valid at the time attested by the timestamp ({ts})",
    "Certificato non ancora valido e nessuna prova dell'ora della firma":
        "Certificate not yet valid and no proof of signing time",
    "Il server di marca temporale ha rifiutato le credenziali": "The timestamp server rejected the credentials",
    "Nessun font disponibile copre tutti i caratteri del testo": "No available font covers all the characters of the text",
    "Il campo ammette solo questi motivi: {r}": "The field only allows these reasons: {r}",
    "Il campo non accetta certificati emessi da «{who}»": "The field does not accept certificates issued by “{who}”",
    "Password errata": "Wrong password",
    "Certificato non riconosciuto": "Certificate not recognized",
    "Impossibile leggere il file dell'ID digitale: {path}": "Cannot read the digital ID file: {path}",
    "Il file non è un ID digitale PKCS#12 (.p12/.pfx) valido": "The file is not a valid PKCS#12 digital ID (.p12/.pfx)",
    "Il file non contiene un certificato": "The file does not contain a certificate",
    "Il file non contiene la chiave privata del certificato": "The file does not contain the certificate's private key",
    "Il certificato è scaduto il {date} (valido dal {start} al {end})":
        "The certificate expired on {date} (valid from {start} to {end})",
    "la firma risulterà non valida": "the signature will be invalid",
    "Il certificato non è ancora valido: lo sarà dal {start} (validità fino al {end})":
        "The certificate is not yet valid: it will be from {start} (valid until {end})",
    "Il certificato scadrà il {date}": "The certificate will expire on {date}",
    "Il certificato non è abilitato alla firma (manca l'uso chiave digitalSignature o nonRepudiation)":
        "The certificate cannot be used for signing (digitalSignature or nonRepudiation key usage missing)",
    "Certificato autofirmato: gli altri non lo riconosceranno automaticamente":
        "Self-signed certificate: others will not recognize it automatically",
    "Impossibile scrivere nella cartella {path}": "Cannot write to folder {path}",
    "Impossibile scrivere il file {path}": "Cannot write file {path}",
    "Il nome e cognome è obbligatorio": "Full name is required",
    "La password del file è obbligatoria": "The file password is required",
    "Il paese deve essere un codice di 2 lettere (es. IT)": "Country must be a 2-letter code (e.g. IT)",
    "La validità deve essere compresa fra 1 e 10 anni": "Validity must be between 1 and 10 years",
    "Algoritmo non supportato: {alg}": "Unsupported algorithm: {alg}",
    "Formato di esportazione non supportato: {fmt}": "Unsupported export format: {fmt}",
    "Molto debole": "Very weak", "Debole": "Weak", "Discreta": "Fair", "Buona": "Good", "Ottima": "Excellent",
    "Il file è danneggiato o non è un PDF valido": "The file is damaged or is not a valid PDF",
    "Il documento è cifrato con una password: aprilo con la password per firmarlo":
        "The document is encrypted with a password: remove the protection before signing it",
    "Riquadro molto piccolo (meno di 40×15 pt): la firma potrebbe essere poco leggibile":
        "Very small box (less than 40×15 pt): the signature may be hard to read",
    "Il testo non entra nel riquadro ed è stato troncato": "The text does not fit in the box and was truncated",
    "Firmato digitalmente da ": "Digitally signed by ", "Data: ": "Date: ", "Motivo: ": "Reason: ",
    "Luogo: ": "Location: ", "DN: ": "DN: ",
    "nessuna modifica": "no changes",
    "compilazione dei moduli e firme": "form filling and signing",
    "compilazione dei moduli, firme e annotazioni": "form filling, signing and annotations",
    "Server di marca temporale irraggiungibile o non ha risposto in tempo":
        "Timestamp server unreachable or did not respond in time",
    "Il server di marca temporale ha rifiutato la richiesta: {detail}":
        "The timestamp server refused the request: {detail}",
    "La marca temporale ricevuta non è coerente ed è stata scartata":
        "The received timestamp is inconsistent and was discarded",
    "Il file è danneggiato: non può essere firmato in modo sicuro":
        "The file is damaged: it cannot be signed safely",
    "Il documento è certificato con livello «nessuna modifica»: non è possibile aggiungere altre firme":
        "The document is certified with level “no changes”: no further signatures can be added",
    "La firma di certificazione deve essere la prima firma del documento: il documento è già firmato":
        "A certification signature must be the first signature: the document is already signed",
    "Livello di certificazione non valido": "Invalid certification level",
    "Il campo «{name}» è già firmato: usa un nuovo campo": "Field “{name}” is already signed: use a new field",
    "Il campo «{name}» è bloccato da una firma precedente": "Field “{name}” is locked by a previous signature",
    "Il blocco dei campi si imposta solo per un nuovo campo firma":
        "Field locking can only be set for a new signature field",
    "Esiste già un campo «{name}» che non è un campo firma": "A non-signature field named “{name}” already exists",
    "Pagina {n} inesistente": "Page {n} does not exist",
    "Algoritmo di hash non supportato: {alg}": "Unsupported hash algorithm: {alg}",
    "Firma non riuscita: {detail}": "Signing failed: {detail}",
    "Firma non riuscita: il salvataggio non è incrementale": "Signing failed: the save is not incremental",
    "Il documento è certificato con livello «nessuna modifica»: non è possibile aggiungere campi":
        "The document is certified with level “no changes”: fields cannot be added",
    "Impossibile creare il campo firma: {detail}": "Cannot create the signature field: {detail}",
    "Valida": "Valid", "Non valida": "Invalid", "Indeterminata": "Indeterminate",
    "{n} firma aggiunta": "{n} signature added", "{n} firme aggiunte": "{n} signatures added",
    "compilazione di campi modulo": "form fields filled",
    "{n} annotazione aggiunta": "{n} annotation added", "{n} annotazioni aggiunte": "{n} annotations added",
    "{n} annotazione modificata": "{n} annotation modified",
    "{n} annotazioni modificate": "{n} annotations modified",
    "a pagina {p}": "on page {p}",
    "contenuto della pagina {p}": "content of page {p}",
    "struttura della pagina {p}": "structure of page {p}",
    "Formato della firma non valido": "Invalid signature format",
    "Impossibile verificare la firma: {detail}": "Cannot verify the signature: {detail}",
    "Il documento è stato alterato dopo la firma": "The document has been altered after signing",
    "Il valore della firma non è crittograficamente valido": "The signature value is not cryptographically valid",
    "Intervallo di byte firmati non valido (/ByteRange)": "Invalid signed byte range (/ByteRange)",
    "Algoritmo debole ({alg})": "Weak algorithm ({alg})",
    "modifiche al documento non ammesse": "document changes not permitted",
    "Modifiche non consentite: ": "Changes not permitted: ",
    "aggiornamenti successivi": "later updates",
    "Modifiche consentite: ": "Permitted changes: ",
    "Nessuna modifica": "No changes",
    "Versione firmata integra": "Signed version intact",
    "Certificato revocato il {date}": "Certificate revoked on {date}",
    "Integrità confermata: il documento non è stato alterato": "Integrity confirmed: the document has not been altered",
    "Stato di revoca non verificabile": "Revocation status cannot be checked",
    "Certificato scaduto il {date} senza prova dell'ora della firma":
        "Certificate expired on {date} with no proof of signing time",
    "Identità del firmatario non attendibile": "Signer identity not trusted",
    "Certificato scaduto dopo l'ora attestata dalla marca temporale":
        "Certificate expired after the time attested by the timestamp",
    "Ora dichiarata dal firmatario": "Time declared by the signer",
    "Impossibile ricavare la versione firmata": "Cannot extract the signed version",
    "Il documento è protetto da password.": "The document is password protected.",
    "1 firma richiesta": "1 signature required", "{n} firme richieste": "{n} signatures required",
    "Nessuna firma": "No signatures",
    "Almeno una firma presenta problemi": "At least one signature has problems",
    "Certificato da {name} — consentito: {what}": "Certified by {name} — allowed: {what}",
    "Firmato: tutte le firme sono valide": "Signed: all signatures are valid",
}
