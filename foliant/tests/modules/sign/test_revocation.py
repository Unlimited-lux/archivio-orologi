"""SIG-16: controllo della revoca con un responder OCSP di prova su 127.0.0.1."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509 import ocsp
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

from foliant.modules.sign import digital

PASSWORD = "Prova-Revoca-2026!"


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


class _Responder:
    """Responder OCSP minimo: risponde «revocato» o «valido» firmando con la chiave della CA."""

    def __init__(self, ca_cert, ca_key):
        self.ca_cert, self.ca_key = ca_cert, ca_key
        self.revoked: dict[int, datetime] = {}
        self.requests = 0
        responder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                responder.requests += 1
                out = responder.answer(ocsp.load_der_ocsp_request(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/ocsp-response")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *_a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.leaf = None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, req) -> bytes:
        now = datetime.now(timezone.utc)
        when = self.revoked.get(req.serial_number)
        status = ocsp.OCSPCertStatus.REVOKED if when else ocsp.OCSPCertStatus.GOOD
        builder = ocsp.OCSPResponseBuilder().add_response(
            cert=self.leaf, issuer=self.ca_cert, algorithm=hashes.SHA256(), cert_status=status,
            this_update=now - timedelta(minutes=1), next_update=now + timedelta(hours=1),
            revocation_time=when, revocation_reason=x509.ReasonFlags.key_compromise if when else None,
        ).responder_id(ocsp.OCSPResponderEncoding.HASH, self.ca_cert)
        if req.extensions:
            for ext in req.extensions:
                builder = builder.add_extension(ext.value, ext.critical)
        return builder.sign(self.ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


@pytest.fixture
def pki(tmp_path):
    """CA attendibile, responder OCSP e ID del firmatario con AIA verso il responder."""
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name("CA di prova")).issuer_name(_name("CA di prova"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=30)).not_valid_after(now + timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    resp = _Responder(ca, ca_key)
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(_name("Mario Rossi")).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=10)).not_valid_after(now + timedelta(days=365))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
                AuthorityInformationAccessOID.OCSP, x509.UniformResourceIdentifier(resp.url))]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    resp.leaf = leaf
    p12 = pkcs12.serialize_key_and_certificates(
        b"mario", key, leaf, [ca], serialization.BestAvailableEncryption(PASSWORD.encode()))
    path = tmp_path / "mario-ca.p12"
    path.write_bytes(p12)
    yield resp, digital.load_pkcs12(path, PASSWORD), digital.cert_info_from_der(
        ca.public_bytes(serialization.Encoding.DER))
    resp.server.shutdown()


def _signed(pdf_bytes, did):
    return digital.sign_pdf(pdf_bytes, did, digital.SignOptions(reason="Vendita"))


def test_revoked_before_signing_is_invalid_with_date(pki, pdf_bytes):
    resp, did, ca = pki
    revoked_at = datetime.now(timezone.utc) - timedelta(days=2)
    resp.revoked[resp.leaf.serial_number] = revoked_at
    data = _signed(pdf_bytes, did)
    [st] = digital.verify_pdf(data, trusted=[ca], allow_fetching=True, revocation_mode="sempre")
    assert resp.requests >= 1
    assert st.status == digital.INVALID
    assert any(r.startswith("Certificato revocato il ") and str(revoked_at.year) in r for r in st.reasons), \
        st.reasons


def test_good_certificate_online_is_valid(pki, pdf_bytes):
    resp, did, ca = pki
    data = _signed(pdf_bytes, did)
    [st] = digital.verify_pdf(data, trusted=[ca], allow_fetching=True, revocation_mode="sempre")
    assert resp.requests >= 1
    assert st.status == digital.VALID, st.reasons


def test_unreachable_responder_is_indeterminate(pki, pdf_bytes):
    resp, did, ca = pki
    data = _signed(pdf_bytes, did)
    resp.server.shutdown()
    resp.server.server_close()
    [st] = digital.verify_pdf(data, trusted=[ca], allow_fetching=True, revocation_mode="sempre")
    assert st.status == digital.INDETERMINATE
    assert "Stato di revoca non verificabile" in st.reasons


def test_offline_mode_makes_no_requests(pki, pdf_bytes):
    resp, did, ca = pki
    resp.revoked[resp.leaf.serial_number] = datetime.now(timezone.utc) - timedelta(days=2)
    data = _signed(pdf_bytes, did)
    [st] = digital.verify_pdf(data, trusted=[ca], allow_fetching=False, revocation_mode="mai")
    assert resp.requests == 0
    assert st.status != digital.INVALID
