"""SEC-09 Cifratura con certificato: logica."""
from __future__ import annotations

import datetime

import pymupdf
import pytest

from foliant.modules.security import certenc as CE
from foliant.modules.security import protection as P

PIN = "pin-2026"


def _make(tmp_path, cn, *, days=365, encipher=True):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=2)).not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=encipher, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    pem = tmp_path / f"{cn}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    der = tmp_path / f"{cn}.cer"
    der.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    p12 = tmp_path / f"{cn}.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(cn.encode(), key, cert, None,
                                                          serialization.BestAvailableEncryption(PIN.encode())))
    return {"pem": str(pem), "der": str(der), "p12": str(p12)}


@pytest.fixture(scope="module")
def people(tmp_path_factory):
    d = tmp_path_factory.mktemp("cert")
    return {n: _make(d, n) for n in ("Anna", "Bruno", "Carla")} | {
        "Scaduto": _make(d, "Scaduto", days=-1), "Firma": _make(d, "Firma", encipher=False)}


def _doc() -> bytes:
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Listino riservato rivenditori 2026", fontsize=12)
    return d.tobytes()


def test_load_certificates(people):
    cert, has_key = CE.load_certificate(people["Anna"]["pem"])
    assert CE.cert_name(cert) == "Anna" and not has_key
    assert CE.cert_name(CE.load_certificate(people["Anna"]["der"])[0]) == "Anna"
    cert, has_key = CE.load_certificate(people["Anna"]["p12"], PIN)
    assert has_key
    with pytest.raises(CE.CertError):
        CE.load_certificate(people["Anna"]["p12"], "sbagliato")
    assert CE.cert_problems(cert) == []
    assert any("scaduto" in p for p in CE.cert_problems(CE.load_certificate(people["Scaduto"]["pem"])[0]))
    assert any("cifratura" in p for p in CE.cert_problems(CE.load_certificate(people["Firma"]["pem"])[0]))


def test_open_with_a_or_b_not_c(people):
    """Cifrato per A e B: si apre con la chiave di A o di B; con quella di C fallisce con un
    messaggio esplicativo."""
    a = CE.Recipient(CE.load_certificate(people["Anna"]["pem"])[0])
    b = CE.Recipient(CE.load_certificate(people["Bruno"]["pem"])[0], allowed=set(), print_level=P.PRINT_NONE)
    out = CE.encrypt_for(_doc(), [a, b])
    assert CE.is_pubkey(out) and b"Listino" not in out
    with pytest.raises(Exception):
        pymupdf.open("pdf", out)                 # il motore non apre /Adobe.PubSec
    for who in ("Anna", "Bruno"):
        res = CE.decrypt_with_pkcs12(out, people[who]["p12"], PIN)
        pdf = pymupdf.open("pdf", res.data)
        assert "Listino riservato rivenditori 2026" in pdf[0].get_text()
    with pytest.raises(CE.CertError) as err:
        CE.decrypt_with_pkcs12(out, people["Carla"]["p12"], PIN)
    assert "Destinatari previsti" in str(err.value) and "Anna" in str(err.value) and "Bruno" in str(err.value)


def test_per_recipient_print_permission(people):
    """A con stampa consentita, B con stampa negata: la copia aperta lo rispetta."""
    a = CE.Recipient(CE.load_certificate(people["Anna"]["pem"])[0])
    b = CE.Recipient(CE.load_certificate(people["Bruno"]["pem"])[0], allowed={"copy"}, print_level=P.PRINT_NONE)
    out = CE.encrypt_for(_doc(), [a, b])
    ra = CE.decrypt_with_pkcs12(out, people["Anna"]["p12"], PIN)
    rb = CE.decrypt_with_pkcs12(out, people["Bruno"]["p12"], PIN)
    assert ra.full_access and ra.print_level == P.PRINT_HIGH
    assert not rb.full_access and rb.print_level == P.PRINT_NONE and "copy" in rb.allowed
    pa, pb = pymupdf.open("pdf", ra.data), pymupdf.open("pdf", rb.data)
    assert not pa.is_encrypted
    assert pb.metadata["encryption"] and not pb.needs_pass  # cifrato, si apre senza password…
    assert not pb.permissions & pymupdf.PDF_PERM_PRINT       # …ma senza stampa
    assert pb.permissions & pymupdf.PDF_PERM_COPY and not pb.permissions & pymupdf.PDF_PERM_MODIFY


def test_encrypt_metadata_option(people):
    a = CE.Recipient(CE.load_certificate(people["Anna"]["pem"])[0])
    out = CE.encrypt_for(_doc(), [a], encrypt_metadata=False)
    assert b"/EncryptMetadata false" in out
    with pytest.raises(CE.CertError):
        CE.encrypt_for(_doc(), [])
