"""Spazio protetto per documenti riservati (SEC-31), senza interfaccia.

Struttura della cartella dello spazio:

- ``vault.json``: parametri di Argon2id (RFC 9106) con il sale e la chiave principale
  avvolta (AES-256-GCM) due volte, con la passphrase e con la chiave di recupero;
- ``index.bin``: indice cifrato (nomi, dimensioni, date);
- ``objects/<id>.bin``: un file per documento, cifrato con AES-256-GCM; l'id è casuale e
  il nome originale è cifrato anche dentro l'oggetto, così l'indice si ricostruisce se
  si danneggia.

Nessun nome né contenuto compare in chiaro. Ogni oggetto è autenticato: un byte alterato
fa fallire la lettura di quel solo documento (`VaultTampered`), gli altri restano
leggibili. La chiave principale resta in memoria solo finché lo spazio è sbloccato.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from foliant.api import _
from foliant.core import platform

FORMAT = "foliant-vault"
VERSION = 1
# Argon2id: 64 MiB, 3 passaggi, 4 corsie (profilo consigliato di RFC 9106 §4, ridotto per
# un'applicazione desktop). I test usano parametri più leggeri.
KDF = {"iterations": 3, "memory": 64 * 1024, "lanes": 4}


class VaultError(Exception):
    pass


class VaultLocked(VaultError):
    pass


class WrongPassphrase(VaultError):
    pass


class VaultTampered(VaultError):
    """Dati alterati o danneggiati (verifica di autenticità non superata)."""


@dataclass
class Entry:
    id: str
    name: str
    size: int
    added: str
    modified: str


# --------------------------------------------------------------- primitive

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _derive(secret: bytes, salt: bytes, kdf: dict) -> bytes:
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    return Argon2id(salt=salt, length=32, iterations=int(kdf["iterations"]), lanes=int(kdf["lanes"]),
                    memory_cost=int(kdf["memory"])).derive(secret)


def _seal(key: bytes, data: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, data, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 28:
        raise VaultTampered(_("Dati dello spazio protetto incompleti."))
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], aad)
    except InvalidTag:
        raise VaultTampered(_("I dati non superano la verifica di autenticità: sono stati alterati o sono "
                              "danneggiati.")) from None


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        platform.replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def format_recovery_key(raw: bytes) -> str:
    text = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def parse_recovery_key(text: str) -> bytes:
    clean = "".join(c for c in text.upper() if c.isalnum())
    try:
        return base64.b32decode(clean + "=" * (-len(clean) % 8))
    except (ValueError, base64.binascii.Error):
        raise WrongPassphrase(_("La chiave di recupero non è valida.")) from None


# ------------------------------------------------------------------ spazio

class Vault:
    def __init__(self, folder: str | os.PathLike, kdf: dict | None = None):
        self.folder = Path(folder)
        self.kdf = dict(kdf or KDF)
        self._key: bytearray | None = None
        self._index: dict[str, dict] = {}
        self.notices: list[str] = []

    # ------------------------------------------------------------ stato
    @property
    def header_path(self) -> Path:
        return self.folder / "vault.json"

    @property
    def exists(self) -> bool:
        return self.header_path.is_file()

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def _header(self) -> dict:
        try:
            data = json.loads(self.header_path.read_text("utf-8"))
        except (OSError, ValueError) as e:
            raise VaultError(_("Lo spazio protetto non è leggibile: {e}").format(e=e)) from e
        if data.get("format") != FORMAT:
            raise VaultError(_("La cartella non contiene uno spazio protetto di Foliant."))
        return data

    # --------------------------------------------------------- creazione
    def create(self, passphrase: str) -> str:
        """Crea lo spazio e restituisce la chiave di recupero (da mostrare una volta)."""
        if self.exists:
            raise VaultError(_("Lo spazio protetto esiste già."))
        if not passphrase:
            raise VaultError(_("Scegli una passphrase."))
        (self.folder / "objects").mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        recovery = secrets.token_bytes(20)
        salt, rsalt = secrets.token_bytes(16), secrets.token_bytes(16)
        header = {
            "format": FORMAT, "version": VERSION,
            "kdf": {"alg": "argon2id", "salt": _b64(salt), **self.kdf},
            "recovery_kdf": {"alg": "argon2id", "salt": _b64(rsalt), **self.kdf},
            "wrapped": {"passphrase": _b64(_seal(_derive(passphrase.encode("utf-8"), salt, self.kdf), key,
                                                 b"wrap:passphrase")),
                        "recovery": _b64(_seal(_derive(recovery, rsalt, self.kdf), key, b"wrap:recovery"))},
        }
        _atomic_write(self.header_path, json.dumps(header, indent=1).encode("utf-8"))
        self._key = bytearray(key)
        self._index = {}
        self._save_index()
        return format_recovery_key(recovery)

    # ----------------------------------------------------- sblocco/blocco
    def unlock(self, passphrase: str) -> None:
        self._unwrap(passphrase.encode("utf-8"), "kdf", "passphrase")

    def unlock_with_recovery(self, recovery_key: str) -> None:
        self._unwrap(parse_recovery_key(recovery_key), "recovery_kdf", "recovery")

    def _unwrap(self, secret: bytes, kdf_key: str, slot: str) -> None:
        header = self._header()
        kdf = header[kdf_key]
        wrap = _derive(secret, _unb64(kdf["salt"]), kdf)
        try:
            key = _open(wrap, _unb64(header["wrapped"][slot]), f"wrap:{slot}".encode())
        except VaultTampered:
            raise WrongPassphrase(_("Passphrase errata.") if slot == "passphrase"
                                  else _("Chiave di recupero errata.")) from None
        self._key = bytearray(key)
        self.notices = []
        self._load_index()

    def lock(self) -> None:
        if self._key is not None:
            for i in range(len(self._key)):
                self._key[i] = 0
        self._key = None
        self._index = {}

    def set_passphrase(self, new: str) -> None:
        """Nuova passphrase (con lo spazio sbloccato, es. dopo la chiave di recupero)."""
        key = self._require()
        if not new:
            raise VaultError(_("Scegli una passphrase."))
        header = self._header()
        salt = secrets.token_bytes(16)
        header["kdf"] = {"alg": "argon2id", "salt": _b64(salt), **self.kdf}
        header["wrapped"]["passphrase"] = _b64(_seal(_derive(new.encode("utf-8"), salt, self.kdf), key,
                                                     b"wrap:passphrase"))
        _atomic_write(self.header_path, json.dumps(header, indent=1).encode("utf-8"))

    def _require(self) -> bytes:
        if self._key is None:
            raise VaultLocked(_("Lo spazio protetto è bloccato."))
        return bytes(self._key)

    # ------------------------------------------------------------- indice
    def _save_index(self) -> None:
        blob = _seal(self._require(), json.dumps(self._index).encode("utf-8"), b"index")
        _atomic_write(self.folder / "index.bin", blob)

    def _load_index(self) -> None:
        try:
            self._index = json.loads(_open(self._require(), (self.folder / "index.bin").read_bytes(), b"index"))
        except (OSError, VaultTampered, ValueError):
            self._index = self._rebuild_index()
            self.notices.append(_("L'indice dello spazio protetto era danneggiato ed è stato ricostruito dai "
                                  "documenti integri."))
            self._save_index()

    def _rebuild_index(self) -> dict:
        index = {}
        for f in sorted((self.folder / "objects").glob("*.bin")):
            try:
                header, data = self._read_object(f.stem)
            except VaultError:
                continue
            index[f.stem] = {"name": header.get("name", f.stem), "size": len(data),
                             "added": header.get("added", ""), "modified": header.get("modified", "")}
        return index

    # ---------------------------------------------------------- documenti
    def entries(self) -> list[Entry]:
        self._require()
        return sorted((Entry(i, d["name"], d["size"], d.get("added", ""), d.get("modified", ""))
                       for i, d in self._index.items()), key=lambda e: e.name.casefold())

    def _object_path(self, oid: str) -> Path:
        if not oid.isalnum():
            raise VaultError(_("Documento non valido."))
        return self.folder / "objects" / f"{oid}.bin"

    def _write_object(self, oid: str, header: dict, data: bytes) -> None:
        head = json.dumps(header).encode("utf-8")
        payload = len(head).to_bytes(4, "big") + head + data
        _atomic_write(self._object_path(oid), _seal(self._require(), payload, oid.encode()))

    def _read_object(self, oid: str) -> tuple[dict, bytes]:
        try:
            blob = self._object_path(oid).read_bytes()
        except OSError as e:
            raise VaultTampered(_("Il documento non è più presente nello spazio protetto.")) from e
        payload = _open(self._require(), blob, oid.encode())
        n = int.from_bytes(payload[:4], "big")
        try:
            header = json.loads(payload[4:4 + n])
        except ValueError:
            raise VaultTampered(_("Dati dello spazio protetto incompleti.")) from None
        return header, payload[4 + n:]

    def add(self, name: str, data: bytes) -> str:
        self._require()
        oid = secrets.token_hex(16)
        now = _now()
        header = {"name": name, "added": now, "modified": now}
        self._write_object(oid, header, data)
        self._index[oid] = {"name": name, "size": len(data), "added": now, "modified": now}
        self._save_index()
        return oid

    def read(self, oid: str) -> bytes:
        return self._read_object(oid)[1]

    def name(self, oid: str) -> str:
        return self._index.get(oid, {}).get("name", "")

    def write(self, oid: str, data: bytes) -> None:
        entry = self._index.get(oid)
        if entry is None:
            raise VaultError(_("Il documento non è più presente nello spazio protetto."))
        now = _now()
        self._write_object(oid, {"name": entry["name"], "added": entry.get("added", now), "modified": now}, data)
        entry.update(size=len(data), modified=now)
        self._save_index()

    def remove(self, oid: str) -> None:
        self._require()
        try:
            self._object_path(oid).unlink()
        except FileNotFoundError:
            pass
        self._index.pop(oid, None)
        self._save_index()
