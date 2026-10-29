"""Fixture per i test della firma digitale: ID di prova generati al volo, PDF, TSA fittizia."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import make_pdf, set_data_home

PASSWORD = "Prova-Firma-2026!"


@pytest.fixture(autouse=True)
def _foliant_data_dir(tmp_path, monkeypatch):
    """Ogni test usa una cartella dati isolata."""
    set_data_home(monkeypatch, tmp_path / "dati")


@pytest.fixture(scope="session")
def id_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("ids")


def _fast_create(path, **kw):
    from foliant.modules.sign import digital

    old = digital.PKCS12_KDF_ROUNDS
    digital.PKCS12_KDF_ROUNDS = 2048  # i test non hanno bisogno di un KDF lento
    try:
        return digital.create_self_signed(path, PASSWORD, **kw)
    finally:
        digital.PKCS12_KDF_ROUNDS = old


@pytest.fixture(scope="session")
def id_a(id_dir):
    """(percorso, CertInfo, DigitalID) del firmatario A."""
    from foliant.modules.sign import digital

    path = id_dir / "mario.p12"
    info = _fast_create(path, common_name="Mario Rossi", organization="Orologeria Verdi",
                        email="mario.rossi@example.org", country="IT")
    return path, info, digital.load_pkcs12(path, PASSWORD)


@pytest.fixture(scope="session")
def id_b(id_dir):
    from foliant.modules.sign import digital

    path = id_dir / "lucia.p12"
    info = _fast_create(path, common_name="Lucia Bianchi", email="lucia@example.org", country="IT")
    return path, info, digital.load_pkcs12(path, PASSWORD)


@pytest.fixture(scope="session")
def expired_id(id_dir):
    path = id_dir / "scaduto.p12"
    info = _fast_create(path, common_name="Vecchio Certificato", years=1,
                        valid_from=datetime(2020, 1, 10, tzinfo=timezone.utc))
    return path, info


@pytest.fixture(scope="session")
def tsa(id_dir):
    """(DummyTimeStamper, CertInfo della TSA) con un certificato timeStamping di prova."""
    from asn1crypto import keys as ak
    from asn1crypto import x509 as ax
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper

    from foliant.modules.sign.digital import cert_info_from_der

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # DummyTimeStamper: solo RSA
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TSA di prova")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    tsa_key = ak.PrivateKeyInfo.load(key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    stamper = DummyTimeStamper(tsa_cert=ax.Certificate.load(der), tsa_key=tsa_key)
    return stamper, cert_info_from_der(der)


@pytest.fixture
def pdf_bytes(tmp_path) -> bytes:
    return Path(make_pdf(tmp_path / "contratto.pdf", pages=2)).read_bytes()


@pytest.fixture
def rotated_pdf_bytes(tmp_path) -> bytes:
    def rotate(page, _i):
        page.set_rotation(90)

    return Path(make_pdf(tmp_path / "ruotato.pdf", pages=1, extra=rotate)).read_bytes()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Al termine di ogni test segna come salvati i documenti aperti nella finestra,
    così la chiusura della finestra non apre la richiesta di salvataggio."""
    try:
        return (yield)
    finally:
        win = getattr(item, "funcargs", {}).get("window")
        if win is not None:
            for view in list(win.views()):
                view.document._saved_state = view.document._state


POLICY_OID = "1.3.76.16.6"


@pytest.fixture(scope="session")
def policy_id(id_dir):
    """ID con l'estensione Certificate Policies (OID POLICY_OID), per i vincoli /SV /Cert /OID."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    from foliant.modules.sign import digital

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Giulia Verdi")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.CertificatePolicies([x509.PolicyInformation(
                x509.ObjectIdentifier(POLICY_OID), None)]), critical=False)
            .sign(key, hashes.SHA256()))
    path = id_dir / "giulia.p12"
    path.write_bytes(pkcs12.serialize_key_and_certificates(
        b"giulia", key, cert, None, serialization.BestAvailableEncryption(PASSWORD.encode())))
    did = digital.load_pkcs12(path, PASSWORD)
    return path, did.info, did
