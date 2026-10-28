"""Dati locali della firma digitale: ID registrati, certificati attendibili, server TSA.

Nessuna password o chiave privata viene mai scritta su disco. Le scritture
sono atomiche (file temporaneo + rinomina) con permessi 0600, cartelle 0700.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from foliant.i18n import _

from .digital import CertInfo, SignError, cert_info_from_der

__all__ = ["data_dir", "IDRegistry", "IDEntry", "TrustStore", "TrustedCert", "TSAConfig", "TSAServer",
           "StoreError", "SCOPES"]


class StoreError(SignError):
    pass


def data_dir() -> Path:
    env = os.environ.get("FOLIANT_DATA_DIR")
    if env:
        base = Path(env)
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "foliant"
    _ensure_dir(base)
    return base


def _ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise StoreError(_("Impossibile creare la cartella {path}").format(path=str(path))) from exc
    return path


def _sign_dir() -> Path:
    return _ensure_dir(data_dir() / "sign")


def _atomic_write(path: Path, data: bytes) -> None:
    _ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise StoreError(_("Impossibile scrivere il file {path}").format(path=str(path))) from exc


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


# --------------------------------------------------------------------------- #
# Registro degli ID digitali
# --------------------------------------------------------------------------- #


@dataclass
class IDEntry:
    path: str
    alias: str
    fingerprint: str
    subject: str
    not_after: str  # ISO 8601
    issuer: str = ""
    default: bool = False
    missing: bool = False

    @property
    def expires(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.not_after)
        except ValueError:
            return None


class IDRegistry:
    """Elenco degli ID digitali dell'utente (solo riferimenti: percorso e impronta)."""

    _ALLOWED = ("path", "alias", "fingerprint", "subject", "not_after", "issuer", "default")

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else _sign_dir() / "ids.json"

    def _load(self) -> list[dict]:
        raw = _read_json(self.path, {"ids": []})
        items = raw.get("ids", []) if isinstance(raw, dict) else []
        return [{k: v for k, v in it.items() if k in self._ALLOWED} for it in items if isinstance(it, dict)]

    def _save(self, items: list[dict]) -> None:
        clean = [{k: it.get(k) for k in self._ALLOWED} for it in items]
        _write_json(self.path, {"version": 1, "ids": clean})

    def add(self, path, info: CertInfo, alias: str | None = None) -> IDEntry:
        items = self._load()
        fp = info.sha256
        entry = {
            "path": str(Path(path).resolve()),
            "alias": alias or info.subject,
            "fingerprint": fp,
            "subject": info.subject,
            "issuer": info.issuer,
            "not_after": info.not_after.isoformat(),
            "default": False,
        }
        replaced = False
        for i, it in enumerate(items):
            if it.get("fingerprint") == fp:
                entry["default"] = bool(it.get("default"))
                items[i] = entry
                replaced = True
        if not replaced:
            if not items:
                entry["default"] = True
            items.append(entry)
        self._save(items)
        return self._entry(entry)

    def remove(self, fingerprint: str) -> bool:
        items = self._load()
        keep = [it for it in items if it.get("fingerprint") != fingerprint]
        if len(keep) == len(items):
            return False
        if keep and not any(it.get("default") for it in keep):
            keep[0]["default"] = True
        self._save(keep)
        return True

    def relink(self, fingerprint: str, new_path) -> bool:
        items = self._load()
        for it in items:
            if it.get("fingerprint") == fingerprint:
                it["path"] = str(Path(new_path).resolve())
                self._save(items)
                return True
        return False

    def set_default(self, fingerprint: str) -> bool:
        items = self._load()
        found = False
        for it in items:
            it["default"] = it.get("fingerprint") == fingerprint
            found = found or it["default"]
        if found:
            self._save(items)
        return found

    def default(self) -> IDEntry | None:
        for e in self.list():
            if e.default:
                return e
        return None

    def list(self) -> list[IDEntry]:
        return [self._entry(it) for it in self._load()]

    @staticmethod
    def _entry(it: dict) -> IDEntry:
        e = IDEntry(
            path=str(it.get("path", "")), alias=str(it.get("alias", "")),
            fingerprint=str(it.get("fingerprint", "")), subject=str(it.get("subject", "")),
            not_after=str(it.get("not_after", "")), issuer=str(it.get("issuer", "")),
            default=bool(it.get("default")),
        )
        e.missing = not Path(e.path).is_file()
        return e


# --------------------------------------------------------------------------- #
# Certificati attendibili
# --------------------------------------------------------------------------- #

SCOPES = ("firme", "certificazioni", "marche temporali")


@dataclass
class TrustedCert:
    info: CertInfo
    scope: list[str] = field(default_factory=lambda: list(SCOPES))

    def __getattr__(self, name):  # accesso diretto ai campi di CertInfo
        if name == "info":
            raise AttributeError(name)
        return getattr(self.info, name)


def _parse_certificates(data: bytes) -> list[bytes]:
    """Restituisce i DER dei certificati in PEM, DER o PKCS#7 (.p7b/.p7c)."""
    from asn1crypto import pem
    from asn1crypto import x509 as ax509

    blobs: list[bytes] = []
    if pem.detect(data):
        for type_name, _headers, der in pem.unarmor(data, multiple=True):
            if type_name in ("CERTIFICATE", "X509 CERTIFICATE", "TRUSTED CERTIFICATE"):
                blobs.append(der)
            elif type_name in ("PKCS7", "CMS"):
                blobs.extend(_parse_p7(der))
        if not blobs:
            raise StoreError(_("Il file PEM non contiene certificati"))
        return blobs
    try:
        cert = ax509.Certificate.load(data)
        cert.native  # noqa: B018
        return [cert.dump()]
    except Exception:  # noqa: BLE001
        pass
    try:
        return _parse_p7(data)
    except StoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StoreError(_("File non riconosciuto: non è un certificato (.cer, .crt, .pem, .der, .p7b)")) from exc


def _parse_p7(der: bytes) -> list[bytes]:
    from asn1crypto import cms

    ci = cms.ContentInfo.load(der)
    if ci["content_type"].native != "signed_data":
        raise StoreError(_("Il file PKCS#7 non contiene certificati"))
    certs = ci["content"]["certificates"]
    out = [c.chosen.dump() for c in certs if c.name == "certificate"]
    if not out:
        raise StoreError(_("Il file PKCS#7 non contiene certificati"))
    return out


class TrustStore:
    """Archivio locale dei certificati attendibili (ancore di fiducia)."""

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else _sign_dir() / "trusted"

    @property
    def _meta_path(self) -> Path:
        return self.dir / "trusted.json"

    def _meta(self) -> dict:
        _ensure_dir(self.dir)
        raw = _read_json(self._meta_path, {})
        return raw if isinstance(raw, dict) else {}

    def add(self, cert, scope=None) -> bool:
        """Aggiunge un certificato (DER/PEM/percorso/CertInfo). False se già presente."""
        if isinstance(cert, CertInfo):
            der = cert.der
        elif isinstance(cert, (str, Path)):
            ders = _parse_certificates(self._read(cert))
            der = ders[0]
        else:
            ders = _parse_certificates(bytes(cert))
            der = ders[0]
        return self._add_der(der, scope)

    @staticmethod
    def _read(path) -> bytes:
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            raise StoreError(_("Impossibile leggere il file {path}").format(path=str(path))) from exc

    def _add_der(self, der: bytes, scope=None) -> bool:
        info = cert_info_from_der(der)
        meta = self._meta()
        if info.sha256 in meta:
            return False
        pem_path = self.dir / f"{info.sha256}.pem"
        _atomic_write(pem_path, info.pem().encode("ascii"))
        meta[info.sha256] = {
            "subject": info.subject,
            "scope": [s for s in (scope or SCOPES) if s in SCOPES] or list(SCOPES),
            "added": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        _write_json(self._meta_path, meta)
        return True

    def import_file(self, path, scope=None) -> list[CertInfo]:
        """Importa tutti i certificati di un file; restituisce quelli nuovi."""
        ders = _parse_certificates(self._read(path))
        added = []
        for der in ders:
            if self._add_der(der, scope):
                added.append(cert_info_from_der(der))
        return added

    def remove(self, sha256: str) -> bool:
        meta = self._meta()
        if sha256 not in meta:
            return False
        del meta[sha256]
        _write_json(self._meta_path, meta)
        try:
            (self.dir / f"{sha256}.pem").unlink()
        except OSError:
            pass
        return True

    def set_scope(self, sha256: str, scope) -> bool:
        meta = self._meta()
        if sha256 not in meta:
            return False
        meta[sha256]["scope"] = [s for s in scope if s in SCOPES]
        _write_json(self._meta_path, meta)
        return True

    def list(self) -> list[TrustedCert]:
        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import serialization

        out = []
        for fp, m in self._meta().items():
            p = self.dir / f"{fp}.pem"
            try:
                cert = cx509.load_pem_x509_certificate(p.read_bytes())
            except (OSError, ValueError):
                continue
            info = cert_info_from_der(cert.public_bytes(serialization.Encoding.DER))
            out.append(TrustedCert(info=info, scope=list(m.get("scope") or SCOPES)))
        out.sort(key=lambda t: t.info.subject.lower())
        return out

    def certificates(self, scope: str | None = None) -> list[CertInfo]:
        return [t.info for t in self.list() if scope is None or scope in t.scope]


# --------------------------------------------------------------------------- #
# Server di marca temporale
# --------------------------------------------------------------------------- #


@dataclass
class TSAServer:
    name: str
    url: str
    timeout: float = 15.0
    hash_algorithm: str = "sha256"
    policy_oid: str = ""
    default: bool = False


class TSAConfig:
    """Elenco dei server TSA (mai credenziali su disco)."""

    _FIELDS = ("name", "url", "timeout", "hash_algorithm", "policy_oid", "default")

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else _sign_dir() / "tsa.json"

    def list(self) -> list[TSAServer]:
        raw = _read_json(self.path, {"servers": []})
        items = raw.get("servers", []) if isinstance(raw, dict) else []
        out = []
        for it in items:
            if not isinstance(it, dict) or not it.get("url"):
                continue
            out.append(TSAServer(**{k: it[k] for k in self._FIELDS if k in it}))
        return out

    def _save(self, servers: list[TSAServer]) -> None:
        _write_json(self.path, {"version": 1, "servers": [
            {k: v for k, v in asdict(s).items() if k in self._FIELDS} for s in servers]})

    @staticmethod
    def _validate(url: str, timeout: float) -> None:
        if not (url.startswith("http://") or url.startswith("https://")):
            raise StoreError(_("L'indirizzo del server deve iniziare con http:// o https://"))
        if "@" in url.split("//", 1)[1].split("/", 1)[0]:
            raise StoreError(_("Non inserire credenziali nell'indirizzo del server"))
        if not 1 <= float(timeout) <= 300:
            raise StoreError(_("Il timeout deve essere compreso fra 1 e 300 secondi"))

    def add(self, name: str, url: str, timeout: float = 15.0, *, hash_algorithm: str = "sha256",
            policy_oid: str = "", default: bool = False) -> TSAServer:
        url = url.strip()
        self._validate(url, timeout)
        servers = [s for s in self.list() if s.url != url]
        srv = TSAServer(name=name.strip() or url, url=url, timeout=float(timeout),
                        hash_algorithm=hash_algorithm, policy_oid=policy_oid, default=default)
        if default or not any(s.default for s in servers):
            for s in servers:
                s.default = False
            srv.default = True
        servers.append(srv)
        self._save(servers)
        return srv

    def remove(self, url: str) -> bool:
        servers = self.list()
        keep = [s for s in servers if s.url != url]
        if len(keep) == len(servers):
            return False
        if keep and not any(s.default for s in keep):
            keep[0].default = True
        self._save(keep)
        return True

    def set_default(self, url: str) -> bool:
        servers = self.list()
        if not any(s.url == url for s in servers):
            return False
        for s in servers:
            s.default = s.url == url
        self._save(servers)
        return True

    def default(self) -> TSAServer | None:
        return next((s for s in self.list() if s.default), None)


TRANSLATIONS_EN = {
    "Impossibile creare la cartella {path}": "Cannot create folder {path}",
    "Impossibile scrivere il file {path}": "Cannot write file {path}",
    "Impossibile leggere il file {path}": "Cannot read file {path}",
    "Il file PEM non contiene certificati": "The PEM file does not contain certificates",
    "File non riconosciuto: non è un certificato (.cer, .crt, .pem, .der, .p7b)":
        "Unrecognized file: it is not a certificate (.cer, .crt, .pem, .der, .p7b)",
    "Il file PKCS#7 non contiene certificati": "The PKCS#7 file does not contain certificates",
    "L'indirizzo del server deve iniziare con http:// o https://": "The server address must start with http:// or https://",
    "Non inserire credenziali nell'indirizzo del server": "Do not put credentials in the server address",
    "Il timeout deve essere compreso fra 1 e 300 secondi": "The timeout must be between 1 and 300 seconds",
}
