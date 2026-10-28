"""SEC-31 Spazio protetto: logica del contenitore."""
from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.security import vault as V

FAST = {"iterations": 1, "memory": 8 * 1024, "lanes": 1}
PASS = "vetrina-Daytona-1963"
SECRET_TEXT = "Passaporto YA1234567 di Giulia Ferri"


_CACHE: dict[str, bytes] = {}


def _pdf_bytes(text=SECRET_TEXT) -> bytes:
    """PDF in cui il testo riservato compare in chiaro (titolo nei metadati)."""
    if text not in _CACHE:
        d = pymupdf.open()
        d.new_page().insert_text((72, 100), text, fontsize=12)
        d.set_metadata({"title": text})
        _CACHE[text] = d.tobytes()
    return _CACHE[text]


def _all_bytes(folder) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())


def test_names_and_text_not_in_container(tmp_path):
    """Dato un PDF aggiunto, cercando nei file del contenitore il nome e frammenti del testo,
    allora non si trovano."""
    v = V.Vault(tmp_path / "spazio", FAST)
    recovery = v.create(PASS)
    assert len(recovery.replace("-", "")) == 32
    data = _pdf_bytes()
    assert SECRET_TEXT.encode() in data
    oid = v.add("Ferri Giulia - passaporto.pdf", data)
    raw = _all_bytes(tmp_path / "spazio")
    for needle in (b"Ferri", b"Giulia", b"passaporto", b"Passaporto", b"YA1234567", PASS.encode()):
        assert needle not in raw
    names = [p.name for p in (tmp_path / "spazio").rglob("*") if p.is_file()]
    assert not any("Ferri" in n or "pdf" in n for n in names)
    assert v.read(oid) == data
    assert [e.name for e in v.entries()] == ["Ferri Giulia - passaporto.pdf"]


def test_locked_requires_passphrase(tmp_path):
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    oid = v.add("a.pdf", _pdf_bytes())
    v.lock()
    assert not v.unlocked
    with pytest.raises(V.VaultLocked):
        v.read(oid)
    with pytest.raises(V.WrongPassphrase):
        v.unlock("sbagliata")
    assert not v.unlocked
    again = V.Vault(tmp_path, FAST)              # una nuova istanza parte bloccata
    with pytest.raises(V.VaultLocked):
        again.entries()
    again.unlock(PASS)
    assert again.read(oid) == _pdf_bytes()


def test_tampered_byte_detected_others_intact(tmp_path):
    """Dato un byte alterato, aprendo il file corrispondente si segnala l'alterazione; gli
    altri restano leggibili."""
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    a = v.add("a.pdf", _pdf_bytes("Contratto A"))
    b = v.add("b.pdf", _pdf_bytes("Contratto B"))
    path = tmp_path / "objects" / f"{a}.bin"
    blob = bytearray(path.read_bytes())
    blob[len(blob) // 2] ^= 0x01
    path.write_bytes(bytes(blob))
    with pytest.raises(V.VaultTampered):
        v.read(a)
    assert v.read(b) == _pdf_bytes("Contratto B")


def test_recovery_key_and_new_passphrase(tmp_path):
    v = V.Vault(tmp_path, FAST)
    recovery = v.create(PASS)
    oid = v.add("a.pdf", b"%PDF-1.7 dati")
    v.lock()
    with pytest.raises(V.WrongPassphrase):
        v.unlock_with_recovery("AAAA-BBBB")
    v.unlock_with_recovery(recovery.lower().replace("-", " "))
    v.set_passphrase("nuova-passphrase-2026")
    v.lock()
    with pytest.raises(V.WrongPassphrase):
        v.unlock(PASS)
    v.unlock("nuova-passphrase-2026")
    assert v.read(oid) == b"%PDF-1.7 dati"


def test_damaged_index_rebuilt(tmp_path):
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    v.add("uno.pdf", b"1")
    v.add("due.pdf", b"22")
    v.lock()
    (tmp_path / "index.bin").write_bytes(b"rovinato")
    v.unlock(PASS)
    assert sorted(e.name for e in v.entries()) == ["due.pdf", "uno.pdf"] and v.notices


def test_write_and_remove(tmp_path):
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    oid = v.add("a.pdf", b"vecchio")
    v.write(oid, b"nuovo contenuto")
    assert v.read(oid) == b"nuovo contenuto" and v.entries()[0].size == len(b"nuovo contenuto")
    v.remove(oid)
    assert v.entries() == [] and not list((tmp_path / "objects").iterdir())


def test_lock_wipes_key(tmp_path):
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    key = v._key
    v.lock()
    assert key == bytearray(32) and v._key is None


def test_create_twice_and_not_a_vault(tmp_path):
    v = V.Vault(tmp_path, FAST)
    v.create(PASS)
    with pytest.raises(V.VaultError):
        V.Vault(tmp_path, FAST).create(PASS)
    (tmp_path / "altro").mkdir()
    (tmp_path / "altro" / "vault.json").write_text("{}", "utf-8")
    with pytest.raises(V.VaultError):
        V.Vault(tmp_path / "altro", FAST).unlock(PASS)


def test_default_kdf_is_memory_hard():
    assert V.KDF["memory"] >= 64 * 1024 and V.KDF["iterations"] >= 3
