"""SIG-10: marca temporale da un server che richiede il certificato client (TLS reciproco)."""

from __future__ import annotations

import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from asn1crypto import tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from foliant.modules.sign import digital

PW = "Client-TSA-2026!"


def _cert(cn, key, issuer=None, issuer_key=None, *, ca=False, san=None):
    now = datetime.now(timezone.utc)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
         .issuer_name(issuer.subject if issuer else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
             (issuer_key or key).public_key()), critical=False))
    if ca:
        b = b.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                          crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address(san))]),
                            critical=False)
    return b.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture
def mtls_tsa(tmp_path, tsa, monkeypatch):
    """TSA HTTPS su 127.0.0.1 che accetta solo client con certificato emesso dalla CA di prova."""
    stamper = tsa[0]
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("CA TLS di prova", ca_key, ca=True)
    srv_key = ec.generate_private_key(ec.SECP256R1())
    srv = _cert("127.0.0.1", srv_key, ca, ca_key, san="127.0.0.1")
    cli_key = ec.generate_private_key(ec.SECP256R1())
    cli = _cert("Orologeria Verdi", cli_key, ca, ca_key)
    pem = serialization.Encoding.PEM
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp_path / "srv.pem").write_bytes(srv.public_bytes(pem) + srv_key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "client.p12").write_bytes(pkcs12.serialize_key_and_certificates(
        b"client", cli_key, cli, None, serialization.BestAvailableEncryption(PW.encode())))
    (tmp_path / "client.pem").write_bytes(cli.public_bytes(pem) + cli_key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(PW.encode())))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.connection.getpeercert()["subject"])
            req = tsp.TimeStampReq.load(self.rfile.read(int(self.headers["Content-Length"])))
            out = stamper.request_tsa_response(req).dump()
            self.send_response(200)
            self.send_header("Content-Type", "application/timestamp-reply")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(tmp_path / "srv.pem"))
    sctx.verify_mode = ssl.CERT_REQUIRED
    sctx.load_verify_locations(str(tmp_path / "ca.pem"))
    httpd.socket = sctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(digital, "TSA_CAFILE", str(tmp_path / "ca.pem"))
    yield f"https://127.0.0.1:{httpd.server_port}/", tmp_path, seen
    httpd.shutdown()


@pytest.mark.parametrize("name", ["client.p12", "client.pem"])
def test_timestamp_with_client_certificate(mtls_tsa, pdf_bytes, id_a, name):
    url, folder, seen = mtls_tsa
    out = digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(
        timestamp_url=url, timestamp_client_cert=(str(folder / name), PW)))
    assert seen, "il server non ha ricevuto il certificato client"
    st = digital.verify_pdf(out, trusted=[id_a[1]])[0]
    assert st.pades_level == "B-T" and st.time_source == "marca temporale"


def test_without_client_certificate_no_file_written(mtls_tsa, pdf_bytes, id_a):
    url, _folder, _seen = mtls_tsa
    with pytest.raises(digital.TimestampError):
        digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(timestamp_url=url))


def test_client_certificate_wrong_password(mtls_tsa, pdf_bytes, id_a):
    url, folder, _seen = mtls_tsa
    with pytest.raises(digital.SignError, match="password errata"):
        digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(
            timestamp_url=url, timestamp_client_cert=(str(folder / "client.p12"), "sbagliata")))


def test_ids_dialog_adds_server_with_client_certificate(qtbot, window, mtls_tsa, monkeypatch, pdf_bytes, id_a):
    """Dialogo ID digitali › Server marca temporale: certificato client scelto, password solo nel portachiavi,
    poi il dialogo di firma passa certificato e password alla firma."""
    import keyring
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    from foliant.modules.sign import store
    from foliant.modules.sign.ui_digital import DigitalSignDialog, IDsDialog
    url, folder, seen = mtls_tsa
    saved = {}
    monkeypatch.setattr(keyring, "set_password", lambda svc, u, p: saved.__setitem__((svc, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda svc, u: saved.get((svc, u)))
    answers = iter([(url, True), ("Aziendale", True), ("", True), (PW, True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(folder / "client.p12"), ""))
    c = window.ctx.sign_controller
    dlg = IDsDialog(window, c)
    qtbot.addWidget(dlg)
    dlg._add_tsa()
    [srv] = store.TSAConfig().list()
    assert srv.client_cert.endswith("client.p12") and saved == {("foliant-tsa", "client:" + url): PW}
    assert PW not in (store.TSAConfig().path.read_text())
    assert "🔑" in dlg.tsas.item(0).text()
    sd = DigitalSignDialog(window, c)
    qtbot.addWidget(sd)
    assert sd.tsa_client_cert(sd.tsa.currentData()) == (srv.client_cert, PW)
    out = digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(
        timestamp_url=url, timestamp_client_cert=sd.tsa_client_cert(sd.tsa.currentData())))
    assert seen and digital.verify_pdf(out, trusted=[id_a[1]])[0].pades_level == "B-T"


def test_client_certificate_requires_https(tmp_path):
    from foliant.modules.sign import store
    (tmp_path / "c.p12").write_bytes(b"x")
    with pytest.raises(store.StoreError, match="https"):
        store.TSAConfig().add("TSA", "http://tsa.example.org", client_cert=str(tmp_path / "c.p12"))
