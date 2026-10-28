"""Libreria locale di firme e iniziali (SIG-04).

Struttura su disco (cartella dati dell'utente, permessi 0700/0600):

    signatures/
      manifest.json        manifest versionato (id, nome, tipo, origine, colore, data, SHA-256)
      manifest.json.bak    copia di sicurezza automatica dell'ultimo manifest valido
      <id>.svg|.png|.json  un file per elemento (tracciati, immagini, firme digitate)
      .lock                blocco per le scritture concorrenti

Con la protezione attiva il contenuto del manifest e i file degli elementi
sono cifrati con AES-256-GCM e una chiave derivata dalla password (scrypt):
su disco non resta nulla in chiaro. La chiave resta solo in memoria.
"""

from __future__ import annotations

import base64
import copy
import datetime as _dt
import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from foliant.i18n import _

from .graphic import SignatureItem, art_to_svg, style_by_id, svg_to_art, text_to_art

MANIFEST_VERSION = 1
MAX_PER_KIND = 100


class LibraryError(Exception):
    """Errore della libreria con messaggio per l'utente."""


class LibraryLocked(LibraryError):
    pass


def data_dir() -> Path:
    env = os.environ.get("FOLIANT_DATA_DIR")
    if env:
        base = Path(env)
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        base = Path(xdg) / "foliant"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(e, OSError):
            raise LibraryError(_("Impossibile salvare la libreria delle firme: {e}").format(e=e.strerror)) from e
        raise


@dataclass
class LibraryEntry:
    id: str
    name: str
    kind: str          # "firma" | "iniziali"
    origin: str        # "disegnata" | "digitata" | "immagine"
    color: str
    created: str
    sha256: str
    file: str
    default: bool = False
    available: bool = True

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        d.pop("available", None)
        return d


def _derive_key(password: str, salt: bytes, n: int = 2 ** 15, r: int = 8, p: int = 1) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _encrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return b"FSE1" + nonce + AESGCM(key).encrypt(nonce, data, None)


def _decrypt(key: bytes, blob: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(b"FSE1"):
        raise LibraryError(_("File della libreria non riconosciuto."))
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], None)
    except InvalidTag as e:
        raise LibraryLocked(_("Password errata o libreria danneggiata.")) from e


class SignatureLibrary:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root else data_dir() / "signatures"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self._key: bytes | None = None
        self._entries: list[LibraryEntry] = []
        self._kdf: dict | None = None
        self.notice: str = ""            # notifica per l'utente (ripristino da copia di sicurezza)
        self.unrecoverable: list[str] = []
        self.reload()

    # ------------------------------------------------------------ manifest
    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def backup_path(self) -> Path:
        return self.root / "manifest.json.bak"

    @property
    def protected(self) -> bool:
        return self._kdf is not None

    @property
    def locked(self) -> bool:
        return self.protected and self._key is None

    @contextmanager
    def _lock(self):
        lock_path = self.root / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _parse(self, raw: bytes) -> tuple[list[LibraryEntry], dict | None]:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or "version" not in data:
            raise ValueError("manifest")
        kdf = data.get("kdf")
        if kdf:
            if self._key is None:
                return [], kdf
            payload = _decrypt(self._key, base64.b64decode(data["payload"]))
            items = json.loads(payload.decode("utf-8"))
        else:
            items = data.get("items", [])
        entries = []
        for it in items:
            e = LibraryEntry(**{k: it[k] for k in LibraryEntry.__dataclass_fields__ if k in it and k != "available"})
            e.available = (self.root / e.file).exists()
            entries.append(e)
        return entries, kdf

    def reload(self) -> None:
        self.notice = ""
        self.unrecoverable = []
        if not self.manifest_path.exists():
            self._entries, self._kdf = [], None
            if self.backup_path.exists():
                self._restore_backup()
            return
        try:
            self._entries, self._kdf = self._parse(self.manifest_path.read_bytes())
        except LibraryLocked:
            raise
        except Exception:
            self._restore_backup()

    def _restore_backup(self) -> None:
        try:
            entries, kdf = self._parse(self.backup_path.read_bytes())
        except LibraryLocked:
            raise
        except Exception:
            entries, kdf = [], None
            self.notice = _("La libreria delle firme era danneggiata e non è stato possibile recuperarla.")
        else:
            self.notice = _("La libreria delle firme era danneggiata: è stata ripristinata dalla copia di sicurezza.")
        self._entries, self._kdf = entries, kdf
        self.unrecoverable = [e.name for e in entries if not e.available]
        if self.unrecoverable:
            self.notice += " " + _("Elementi non recuperabili: {names}.").format(names=", ".join(self.unrecoverable))
        if self.backup_path.exists() and (entries or kdf):
            _atomic_write(self.manifest_path, self.backup_path.read_bytes())

    def _write_manifest(self) -> None:
        items = [e.to_json() for e in self._entries]
        if self._kdf:
            if self._key is None:
                raise LibraryLocked(_("La libreria è protetta: sbloccala prima di modificarla."))
            payload = base64.b64encode(_encrypt(self._key, json.dumps(items).encode("utf-8"))).decode("ascii")
            data = {"version": MANIFEST_VERSION, "kdf": self._kdf, "payload": payload}
        else:
            data = {"version": MANIFEST_VERSION, "items": items}
        raw = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        _atomic_write(self.manifest_path, raw)
        _atomic_write(self.backup_path, raw)

    # ----------------------------------------------------------- protezione
    def unlock(self, password: str) -> None:
        if not self._kdf:
            return
        kdf = self._kdf
        key = _derive_key(password, base64.b64decode(kdf["salt"]), kdf.get("n", 2 ** 15), kdf.get("r", 8),
                          kdf.get("p", 1))
        self._key = key
        try:
            self._entries, self._kdf = self._parse(self.manifest_path.read_bytes())
        except LibraryLocked:
            self._key = None
            raise
        except Exception:
            self._restore_backup()

    def lock(self) -> None:
        if self._kdf:
            self._key = None
            self._entries = []

    def protect(self, password: str) -> None:
        """Attiva la protezione: cifra manifest e file con una chiave derivata dalla password."""
        if not password:
            raise LibraryError(_("Indica una password."))
        if self.locked:
            raise LibraryLocked(_("La libreria è protetta: sbloccala prima di modificarla."))
        with self._lock():
            contents = {e.id: self._read_file(e) for e in self._entries if e.available}
            salt = os.urandom(16)
            self._kdf = {"alg": "scrypt", "salt": base64.b64encode(salt).decode("ascii"), "n": 2 ** 15, "r": 8, "p": 1}
            self._key = _derive_key(password, salt)
            self._rewrite_files(contents)
            self._write_manifest()

    def unprotect(self) -> None:
        if not self._kdf:
            return
        if self.locked:
            raise LibraryLocked(_("La libreria è protetta: sbloccala prima di modificarla."))
        with self._lock():
            contents = {e.id: self._read_file(e) for e in self._entries if e.available}
            self._kdf = None
            self._key = None
            self._rewrite_files(contents)
            self._write_manifest()

    def _rewrite_files(self, contents: dict[str, bytes]) -> None:
        for e in self._entries:
            if e.id not in contents:
                continue
            old = self.root / e.file
            base = e.file.split(".")[0]
            ext = {"disegnata": "svg", "digitata": "json", "immagine": "png"}[e.origin]
            e.file = f"{base}.{ext}" + (".enc" if self._kdf else "")
            self._write_file(e, contents[e.id])
            if old != self.root / e.file:
                try:
                    old.unlink()
                except OSError:
                    pass

    def _read_file(self, e: LibraryEntry) -> bytes:
        raw = (self.root / e.file).read_bytes()
        if e.file.endswith(".enc"):
            if self._key is None:
                raise LibraryLocked(_("La libreria è protetta: sbloccala per usarla."))
            return _decrypt(self._key, raw)
        return raw

    def _write_file(self, e: LibraryEntry, data: bytes) -> None:
        if self._kdf:
            data = _encrypt(self._key, data)
        _atomic_write(self.root / e.file, data)

    # --------------------------------------------------------------- elenco
    def entries(self, kind: str | None = None) -> list[LibraryEntry]:
        if self.locked:
            return []
        return [e for e in self._entries if kind is None or e.kind == kind]

    def entry(self, entry_id: str) -> LibraryEntry | None:
        return next((e for e in self._entries if e.id == entry_id), None)

    def default(self, kind: str = "firma") -> LibraryEntry | None:
        items = self.entries(kind)
        return next((e for e in items if e.default), items[0] if items else None)

    # ------------------------------------------------------- serializzazione
    @staticmethod
    def serialize(item: SignatureItem) -> tuple[bytes, str]:
        if item.origin == "immagine":
            if not item.png:
                raise LibraryError(_("L'elemento non ha contenuto."))
            return item.png, "png"
        if item.origin == "digitata":
            data = {"text": item.text, "style": item.style_id, "color": item.color, "slant": item.slant}
            return json.dumps(data, ensure_ascii=False).encode("utf-8"), "json"
        if item.art is None:
            raise LibraryError(_("L'elemento non ha contenuto."))
        return art_to_svg(item.art, item.color).encode("utf-8"), "svg"

    def load_item(self, entry_id: str) -> SignatureItem:
        e = self.entry(entry_id)
        if e is None:
            raise LibraryError(_("Elemento non trovato nella libreria."))
        if not (self.root / e.file).exists():
            raise LibraryError(_("Il file dell'elemento «{name}» non è disponibile.").format(name=e.name))
        raw = self._read_file(e)
        item = SignatureItem(kind=e.kind, origin=e.origin, name=e.name, color=e.color, id=e.id)
        if e.origin == "immagine":
            item.png = raw
        elif e.origin == "digitata":
            data = json.loads(raw.decode("utf-8"))
            item.text = data.get("text", "")
            item.style_id = data.get("style", "")
            item.slant = float(data.get("slant", 0))
            item.color = data.get("color", e.color)
            item.art = text_to_art(item.text, style_by_id(item.style_id), item.slant)
        else:
            item.art = svg_to_art(raw.decode("utf-8"))
        return item

    # ------------------------------------------------------------ modifiche
    def _modify(self):
        if self.locked:
            raise LibraryLocked(_("La libreria è protetta: sbloccala prima di modificarla."))
        return self._lock()

    def _reload_for_write(self) -> None:
        """Ricarica dal disco (altra istanza) prima di modificare."""
        if self.manifest_path.exists():
            try:
                self._entries, self._kdf = self._parse(self.manifest_path.read_bytes())
            except LibraryLocked:
                raise
            except Exception:
                pass

    def add(self, item: SignatureItem, name: str | None = None) -> LibraryEntry:
        with self._modify():
            self._reload_for_write()
            if len(self.entries(item.kind)) >= MAX_PER_KIND:
                raise LibraryError(_("La libreria contiene già {n} elementi di questo tipo.").format(n=MAX_PER_KIND))
            data, ext = self.serialize(item)
            eid = uuid.uuid4().hex
            default_name = _("Iniziali") if item.kind == "iniziali" else _("Firma")
            n = len(self.entries(item.kind)) + 1
            e = LibraryEntry(
                id=eid, name=(name or item.name or f"{default_name} {n}").strip()[:80], kind=item.kind,
                origin=item.origin, color=item.color,
                created=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                sha256=hashlib.sha256(data).hexdigest(), file=f"{eid}.{ext}" + (".enc" if self._kdf else ""),
                default=not self.entries(item.kind))
            self._write_file(e, data)
            self._entries.append(e)
            try:
                self._write_manifest()
            except BaseException:
                self._entries.remove(e)
                try:
                    (self.root / e.file).unlink()
                except OSError:
                    pass
                raise
            item.id = eid
            return e

    def rename(self, entry_id: str, name: str) -> None:
        with self._modify():
            self._reload_for_write()
            e = self.entry(entry_id)
            if e is None:
                return
            if not name.strip():
                raise LibraryError(_("Il nome non può essere vuoto."))
            e.name = name.strip()[:80]
            self._write_manifest()

    def move(self, entry_id: str, delta: int) -> None:
        with self._modify():
            self._reload_for_write()
            e = self.entry(entry_id)
            if e is None:
                return
            same = [x for x in self._entries if x.kind == e.kind]
            i = same.index(e)
            j = max(0, min(len(same) - 1, i + delta))
            if i == j:
                return
            other = same[j]
            a, b = self._entries.index(e), self._entries.index(other)
            self._entries[a], self._entries[b] = self._entries[b], self._entries[a]
            self._write_manifest()

    def set_default(self, entry_id: str) -> None:
        with self._modify():
            self._reload_for_write()
            e = self.entry(entry_id)
            if e is None:
                return
            for x in self._entries:
                if x.kind == e.kind:
                    x.default = x is e
            self._write_manifest()

    def duplicate(self, entry_id: str) -> LibraryEntry:
        item = self.load_item(entry_id)
        e = self.entry(entry_id)
        copy_item = copy.copy(item)
        return self.add(copy_item, _("{name} (copia)").format(name=e.name if e else item.name))

    def remove(self, entry_id: str) -> None:
        with self._modify():
            self._reload_for_write()
            e = self.entry(entry_id)
            if e is None:
                return
            self._entries.remove(e)
            if e.default:
                rest = [x for x in self._entries if x.kind == e.kind]
                if rest:
                    rest[0].default = True
            self._write_manifest()
            try:
                (self.root / e.file).unlink()
            except OSError:
                pass


TRANSLATIONS_EN = {
    "Impossibile salvare la libreria delle firme: {e}": "Cannot save the signature library: {e}",
    "File della libreria non riconosciuto.": "Library file not recognised.",
    "Password errata o libreria danneggiata.": "Wrong password or damaged library.",
    "La libreria delle firme era danneggiata e non è stato possibile recuperarla.":
        "The signature library was damaged and could not be recovered.",
    "La libreria delle firme era danneggiata: è stata ripristinata dalla copia di sicurezza.":
        "The signature library was damaged: it has been restored from the backup copy.",
    "Elementi non recuperabili: {names}.": "Unrecoverable items: {names}.",
    "La libreria è protetta: sbloccala prima di modificarla.": "The library is protected: unlock it before changing it.",
    "Indica una password.": "Enter a password.",
    "La libreria è protetta: sbloccala per usarla.": "The library is protected: unlock it to use it.",
    "L'elemento non ha contenuto.": "The item has no content.",
    "Elemento non trovato nella libreria.": "Item not found in the library.",
    "Il file dell'elemento «{name}» non è disponibile.": "The file of item “{name}” is not available.",
    "La libreria contiene già {n} elementi di questo tipo.": "The library already contains {n} items of this type.",
    "Il nome non può essere vuoto.": "The name cannot be empty.",
    "{name} (copia)": "{name} (copy)",
}
