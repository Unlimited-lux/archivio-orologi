"""Test dei dati locali della firma (SIG-10, SIG-14)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from foliant.modules.sign import digital as d
from foliant.modules.sign import store
from tests.modules.sign.conftest import PASSWORD


def _cert(cn: str) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30)).sign(key, hashes.SHA256()))


def test_data_dir_private(tmp_path):
    p = store.data_dir()
    assert p == tmp_path / "dati" / "foliant"
    assert p.is_dir() and (p.stat().st_mode & 0o777) == 0o700


def test_data_dir_follows_standard_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert store.data_dir() == tmp_path / "xdg" / "foliant"


def test_id_registry_never_stores_secrets(id_a, tmp_path):
    path, info, _did = id_a
    reg = store.IDRegistry()
    entry = reg.add(path, info, alias="Lavoro")
    assert entry.fingerprint == info.sha256 and entry.alias == "Lavoro" and entry.default
    raw = reg.path.read_bytes()
    assert PASSWORD.encode() not in raw
    assert b"PRIVATE" not in raw and b"BEGIN" not in raw
    data = json.loads(raw)
    assert set(data["ids"][0]) <= {"path", "alias", "fingerprint", "subject", "not_after", "issuer", "default"}
    assert (reg.path.stat().st_mode & 0o777) == 0o600
    assert (reg.path.parent.stat().st_mode & 0o777) == 0o700
    [e] = reg.list()
    assert not e.missing and e.subject == "Mario Rossi"
    assert reg.default().fingerprint == info.sha256


def test_id_registry_missing_default_remove(id_a, id_b, tmp_path):
    reg = store.IDRegistry()
    gone = tmp_path / "spostato.p12"
    gone.write_bytes((id_b[0]).read_bytes())
    reg.add(id_a[0], id_a[1])
    reg.add(gone, id_b[1])
    gone.unlink()
    by = {e.fingerprint: e for e in reg.list()}
    assert by[id_b[1].sha256].missing and not by[id_a[1].sha256].missing
    assert reg.set_default(id_b[1].sha256)
    assert reg.default().fingerprint == id_b[1].sha256
    assert reg.relink(id_b[1].sha256, id_b[0])
    assert not {e.fingerprint: e for e in reg.list()}[id_b[1].sha256].missing
    assert reg.remove(id_b[1].sha256)
    assert not reg.remove("inesistente")
    assert [e.fingerprint for e in reg.list()] == [id_a[1].sha256]
    assert reg.default().fingerprint == id_a[1].sha256
    # il file dell'ID non viene cancellato
    assert id_b[0].exists()


def test_trust_store_p7b_with_three_certs(tmp_path):
    certs = [_cert(f"CA di prova {i}") for i in range(3)]
    p7b = tmp_path / "catena.p7b"
    p7b.write_bytes(pkcs7.serialize_certificates(certs, serialization.Encoding.DER))
    ts = store.TrustStore()
    added = ts.import_file(p7b)
    assert len(added) == 3
    expected = {c.fingerprint(hashes.SHA256()).hex() for c in certs}
    assert {t.sha256 for t in ts.list()} == expected
    assert {c.sha256 for c in ts.certificates()} == expected
    # duplicati ignorati
    assert ts.import_file(p7b) == []
    assert ts.add(certs[0].public_bytes(serialization.Encoding.PEM)) is False
    # PEM del p7b
    p7pem = tmp_path / "catena.p7c"
    p7pem.write_bytes(pkcs7.serialize_certificates(certs, serialization.Encoding.PEM))
    assert store.TrustStore(tmp_path / "altro").import_file(p7pem) and \
        len(store.TrustStore(tmp_path / "altro").list()) == 3
    assert ts.remove(certs[1].fingerprint(hashes.SHA256()).hex())
    assert len(ts.list()) == 2


def test_trust_store_formats_scope_and_errors(tmp_path):
    c = _cert("Ancora")
    der = tmp_path / "a.cer"
    der.write_bytes(c.public_bytes(serialization.Encoding.DER))
    ts = store.TrustStore()
    assert ts.add(der, scope=["firme"]) is True
    [t] = ts.list()
    assert t.scope == ["firme"] and t.subject == "Ancora"
    assert ts.certificates("firme") and not ts.certificates("marche temporali")
    assert oct(next(ts.dir.glob("*.pem")).stat().st_mode & 0o777) == "0o600"
    bad = tmp_path / "rotto.crt"
    bad.write_bytes(b"non un certificato")
    with pytest.raises(store.StoreError):
        ts.import_file(bad)


def test_trust_store_drives_verification(pdf_bytes, id_a):
    path, info, did = id_a
    signed = d.sign_pdf(pdf_bytes, did, d.SignOptions())
    exported = path.parent / "pubblico.cer"
    d.export_certificate(info, exported, "der")
    ts = store.TrustStore()
    ts.import_file(exported)
    [st] = d.verify_pdf(signed, trusted=ts.certificates())
    assert st.status == d.VALID
    ts.remove(info.sha256)
    [st] = d.verify_pdf(signed, trusted=ts.certificates())
    assert st.status == d.INDETERMINATE


def test_tsa_config(tmp_path):
    cfg = store.TSAConfig()
    a = cfg.add("Interno", "http://tsa.example.org/tsr")
    assert a.default and a.timeout == 15.0
    cfg.add("Secondo", "https://tsa2.example.org", timeout=10)
    assert cfg.default().url == "http://tsa.example.org/tsr"
    assert cfg.set_default("https://tsa2.example.org")
    assert cfg.default().name == "Secondo"
    with pytest.raises(store.StoreError):
        cfg.add("Errato", "ftp://x")
    with pytest.raises(store.StoreError):
        cfg.add("Credenziali", "https://utente:segreta@tsa.example.org")
    assert b"segreta" not in cfg.path.read_bytes()
    assert cfg.remove("https://tsa2.example.org")
    assert [s.name for s in cfg.list()] == ["Interno"] and cfg.default().name == "Interno"
    assert (os.stat(cfg.path).st_mode & 0o777) == 0o600


def test_system_certificates_optional(tmp_path, id_a):
    bundle = tmp_path / "bundle.crt"
    bundle.write_text(id_a[1].pem() + id_a[1].pem())
    certs = store.system_certificates((str(tmp_path / "manca.crt"), str(bundle)))
    assert [c.sha256 for c in certs] == [id_a[1].sha256] * 2
    assert store.system_certificates((str(tmp_path / "manca.crt"),)) == []
