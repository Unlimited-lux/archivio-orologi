"""Sincronizzazione della libreria firme con una cartella scelta dall'utente (SIG-07).

La cartella può essere locale, di rete o gestita da un qualsiasi strumento di sincronizzazione: Foliant
non usa servizi propri. Contenuto della cartella:

- `foliant-sync.json` (in chiaro): parametri della derivazione della chiave (scrypt) e un blocco di
  controllo cifrato per riconoscere una passphrase errata;
- `index.enc`: indice cifrato con, per ogni elemento, la versione logica `v`, l'impronta `h` del contenuto
  e dei metadati, la data di cancellazione logica `deleted` e il dispositivo che l'ha scritto;
- `items/<id>.enc`: un file cifrato per elemento (metadati e contenuto).

Lo stato locale (`sync-state.json` nella cartella della libreria) ricorda, per ogni elemento, la versione
e l'impronta dell'ultima sincronizzazione, così si distinguono le modifiche locali da quelle remote.
Stesso elemento modificato su due dispositivi → si conservano entrambe le versioni, la locale con il
suffisso «(conflitto)». Una cancellazione è logica e recuperabile per 30 giorni.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from foliant.i18n import _

from .graphic import SignatureItem, style_by_id, svg_to_art, text_to_art
from .library import (
    LibraryError, LibraryLocked, SignatureLibrary, _atomic_write, _decrypt, _derive_key, _encrypt,
)

FORMAT = 1
HEADER = "foliant-sync.json"
INDEX = "index.enc"
ITEMS = "items"
STATE = "sync-state.json"
RETENTION_DAYS = 30
INTERVAL_S = 60          # ciclo periodico: le modifiche degli altri dispositivi arrivano entro 60 s
_CHECK = b"foliant-signature-sync"


class SyncError(LibraryError):
    pass


class SyncSuspended(SyncError):
    """Cartella non raggiungibile: la libreria locale continua a funzionare."""


class SyncPassphraseError(SyncError):
    pass


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    deleted: int = 0
    conflicts: int = 0
    purged: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def changed_local(self) -> bool:
        return bool(self.pulled or self.deleted or self.conflicts)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _rev_hash(meta: dict, sha256: str) -> str:
    """Impronta di contenuto e metadati sincronizzati (nome, tipo, colore, predefinito)."""
    key = [sha256, meta.get("name", ""), meta.get("kind", ""), meta.get("color", ""), bool(meta.get("default"))]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()


_KEYS: dict[tuple, bytes] = {}    # chiavi già derivate (solo in memoria): il ciclo periodico non ripete scrypt


class SyncFolder:
    """Accesso alla cartella condivisa con la chiave derivata dalla passphrase."""

    def __init__(self, folder, passphrase: str, *, create: bool = False):
        self.path = Path(folder)
        if not self.path.is_dir():
            raise SyncSuspended(_("Cartella di sincronizzazione non raggiungibile: {p}").format(p=self.path))
        head = self.path / HEADER
        try:
            if head.exists():
                header = json.loads(head.read_text("utf-8"))
                if int(header.get("format", 1)) > FORMAT:
                    raise SyncError(_("La cartella è stata creata da una versione successiva di Foliant."))
                salt = base64.b64decode(header["salt"])
                cache = (str(self.path.resolve()), header["salt"], hashlib.sha256(passphrase.encode()).hexdigest())
                self.key = _KEYS.get(cache) or _derive_key(passphrase, salt, header.get("n", 2 ** 15),
                                                           header.get("r", 8), header.get("p", 1))
                try:
                    ok = _decrypt(self.key, base64.b64decode(header["check"])) == _CHECK
                except (LibraryError, LibraryLocked):
                    ok = False
                if not ok:
                    _KEYS.pop(cache, None)
                    raise SyncPassphraseError(_("Passphrase errata per questa cartella di sincronizzazione."))
                _KEYS[cache] = self.key
            elif create:
                salt = os.urandom(16)
                self.key = _derive_key(passphrase, salt)
                header = {"format": FORMAT, "kdf": "scrypt", "salt": base64.b64encode(salt).decode("ascii"),
                          "n": 2 ** 15, "r": 8, "p": 1,
                          "check": base64.b64encode(_encrypt(self.key, _CHECK)).decode("ascii")}
                _atomic_write(head, json.dumps(header).encode("utf-8"))
                (self.path / ITEMS).mkdir(exist_ok=True)
                self.write_index({})
            else:
                raise SyncError(_("La cartella non contiene una libreria sincronizzata."))
        except OSError as e:
            raise SyncSuspended(_("Cartella di sincronizzazione non raggiungibile: {e}").format(
                e=e.strerror or e)) from e
        except (KeyError, ValueError) as e:
            raise SyncError(_("File di sincronizzazione danneggiato.")) from e

    # ----------------------------------------------------------- indice
    def read_index(self) -> dict:
        p = self.path / INDEX
        try:
            if not p.exists():
                return {}
            return json.loads(_decrypt(self.key, p.read_bytes()).decode("utf-8")).get("items", {})
        except OSError as e:
            raise SyncSuspended(_("Cartella di sincronizzazione non raggiungibile: {e}").format(
                e=e.strerror or e)) from e
        except (LibraryError, LibraryLocked, ValueError) as e:
            raise SyncError(_("Indice di sincronizzazione danneggiato.")) from e

    def write_index(self, items: dict) -> None:
        data = json.dumps({"format": FORMAT, "items": items}, ensure_ascii=False).encode("utf-8")
        self._write(self.path / INDEX, _encrypt(self.key, data))

    # ---------------------------------------------------------- elementi
    def item_path(self, eid: str) -> Path:
        return self.path / ITEMS / f"{eid}.enc"

    def read_item(self, eid: str) -> tuple[dict, bytes]:
        try:
            payload = json.loads(_decrypt(self.key, self.item_path(eid).read_bytes()).decode("utf-8"))
        except OSError as e:
            raise SyncSuspended(_("Cartella di sincronizzazione non raggiungibile: {e}").format(
                e=e.strerror or e)) from e
        except (LibraryError, LibraryLocked, ValueError) as e:
            raise SyncError(_("Elemento sincronizzato danneggiato.")) from e
        return payload["meta"], base64.b64decode(payload["data"])

    def write_item(self, eid: str, meta: dict, data: bytes) -> None:
        payload = {"meta": meta, "data": base64.b64encode(data).decode("ascii")}
        (self.path / ITEMS).mkdir(exist_ok=True)
        self._write(self.item_path(eid), _encrypt(self.key, json.dumps(payload, ensure_ascii=False).encode()))

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        try:
            _atomic_write(path, data)
        except LibraryError as e:
            raise SyncSuspended(str(e)) from e


# ============================================================== stato locale
def _state_path(library: SignatureLibrary) -> Path:
    return library.root / STATE


def load_state(library: SignatureLibrary) -> dict:
    try:
        return json.loads(_state_path(library).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(library: SignatureLibrary, state: dict) -> None:
    _atomic_write(_state_path(library), json.dumps(state, ensure_ascii=False).encode("utf-8"))


def configured_folder(library: SignatureLibrary) -> str | None:
    return load_state(library).get("folder") or None


def connect(library: SignatureLibrary, folder, passphrase: str) -> SyncResult:
    """Collega la libreria alla cartella (creandovi la struttura se è vuota) e sincronizza subito."""
    SyncFolder(folder, passphrase, create=True)
    state = load_state(library)
    if state.get("folder") != str(Path(folder).resolve()):
        state = {"folder": str(Path(folder).resolve()), "device": state.get("device") or uuid.uuid4().hex,
                 "items": {}}
        _save_state(library, state)
    return sync(library, passphrase)


def disconnect(library: SignatureLibrary) -> None:
    try:
        _state_path(library).unlink()
    except OSError:
        pass


# ============================================================ conversioni
def _local_payload(library: SignatureLibrary, eid: str) -> tuple[dict, bytes]:
    e = library.entry(eid)
    meta = e.to_json()
    for k in ("file", "sha256"):
        meta.pop(k, None)
    return meta, library._read_file(e)


def _item_from(meta: dict, data: bytes) -> SignatureItem:
    origin = meta.get("origin", "disegnata")
    item = SignatureItem(kind=meta.get("kind", "firma"), origin=origin, name=meta.get("name", ""),
                         color=meta.get("color", "#000000"))
    if origin == "immagine":
        item.png = data
    elif origin == "digitata":
        d = json.loads(data.decode("utf-8"))
        item.text, item.style_id = d.get("text", ""), d.get("style", "")
        item.slant, item.color = float(d.get("slant", 0)), d.get("color", item.color)
        item.art = text_to_art(item.text, style_by_id(item.style_id), item.slant)
    else:
        item.art = svg_to_art(data.decode("utf-8"))
    return item


def _install(library: SignatureLibrary, eid: str, meta: dict, data: bytes) -> None:
    """Porta nella libreria locale la versione remota di un elemento (stesso id)."""
    if library.entry(eid) is not None:
        library.remove(eid)
    e = library.add_raw(_item_from(meta, data), meta.get("name") or None, data, entry_id=eid,
                        created=meta.get("created"))
    if meta.get("default") and not e.default:
        library.set_default(eid)


# =================================================================== ciclo
def sync(library: SignatureLibrary, passphrase: str) -> SyncResult:
    """Un ciclo di sincronizzazione bidirezionale. Solleva SyncSuspended se la cartella non è
    raggiungibile (la libreria locale resta utilizzabile)."""
    state = load_state(library)
    if not state.get("folder"):
        raise SyncError(_("La libreria non è collegata a una cartella di sincronizzazione."))
    if library.locked:
        raise LibraryLocked(_("La libreria è protetta: sbloccala per sincronizzarla."))
    folder = SyncFolder(state["folder"], passphrase)
    device = state.setdefault("device", uuid.uuid4().hex)
    known: dict = state.setdefault("items", {})
    remote = folder.read_index()
    res = SyncResult()
    now = _now()
    local = {e.id: e for e in library.entries()}

    def push(eid: str, version: int) -> None:
        meta, data = _local_payload(library, eid)
        h = _rev_hash(meta, hashlib.sha256(data).hexdigest())
        folder.write_item(eid, meta, data)
        remote[eid] = {"v": version, "h": h, "deleted": None, "device": device}
        known[eid] = {"v": version, "h": h}
        res.pushed += 1

    def pull(eid: str) -> None:
        meta, data = folder.read_item(eid)
        _install(library, eid, meta, data)
        known[eid] = {"v": remote[eid]["v"], "h": remote[eid]["h"]}
        res.pulled += 1

    for eid, e in local.items():
        meta, data = _local_payload(library, eid)
        h = _rev_hash(meta, hashlib.sha256(data).hexdigest())
        k, r = known.get(eid), remote.get(eid)
        changed_here = k is None or k.get("h") != h
        if r is None:
            push(eid, 1)
            continue
        changed_there = k is None or r["v"] > k.get("v", 0)
        if r.get("deleted"):
            if changed_here and r.get("h") != h:
                push(eid, r["v"] + 1)            # modificato qui dopo la cancellazione altrove: resta
            else:
                library.remove(eid)
                known.pop(eid, None)
                res.deleted += 1
            continue
        if not changed_there:
            if changed_here:
                push(eid, r["v"] + 1)
            continue
        if not changed_here or r.get("h") == h:
            if r.get("h") != h:
                pull(eid)
            else:
                known[eid] = {"v": r["v"], "h": h}
            continue
        # conflitto: la versione remota prende il posto dell'id, la locale diventa una copia «(conflitto)»
        item = library.load_item(eid)
        lname = local[eid].name
        pull(eid)
        copy = library.add_raw(item, _("{name} (conflitto)").format(name=lname)[:80], data)
        push(copy.id, 1)
        res.conflicts += 1
    for eid, r in list(remote.items()):
        if eid in local or library.entry(eid) is not None:     # anche le copie «(conflitto)» appena create
            continue
        k = known.get(eid)
        if r.get("deleted"):
            known.pop(eid, None)
            try:
                when = _dt.datetime.fromisoformat(r["deleted"])
            except (TypeError, ValueError):
                when = now
            if now - when > _dt.timedelta(days=RETENTION_DAYS):
                try:
                    folder.item_path(eid).unlink()
                except OSError:
                    pass
                del remote[eid]
                res.purged += 1
            continue
        if k is not None and r["v"] <= k.get("v", 0):
            # eliminato qui: cancellazione logica, recuperabile per 30 giorni
            remote[eid] = {**r, "v": r["v"] + 1, "deleted": now.isoformat(timespec="seconds"), "device": device}
            known.pop(eid, None)
            res.deleted += 1
            continue
        try:
            pull(eid)
        except LibraryError as e:
            res.warnings.append(str(e))
    folder.write_index(remote)
    _save_state(library, state)
    return res


def deleted_items(library: SignatureLibrary, passphrase: str) -> list[tuple[str, str, str]]:
    """Elementi eliminati logicamente e ancora recuperabili: (id, nome, data di cancellazione)."""
    folder = SyncFolder(load_state(library)["folder"], passphrase)
    out = []
    for eid, r in folder.read_index().items():
        if r.get("deleted"):
            try:
                meta, _data = folder.read_item(eid)
                out.append((eid, meta.get("name", "?"), r["deleted"]))
            except SyncError:
                continue
    return sorted(out, key=lambda t: t[2], reverse=True)


def restore(library: SignatureLibrary, passphrase: str, eid: str) -> SyncResult:
    """Annulla la cancellazione logica di un elemento e lo riporta nella libreria."""
    folder = SyncFolder(load_state(library)["folder"], passphrase)
    remote = folder.read_index()
    r = remote.get(eid)
    if r is None or not r.get("deleted"):
        raise SyncError(_("Elemento non recuperabile."))
    remote[eid] = {**r, "v": r["v"] + 1, "deleted": None, "device": load_state(library).get("device", "")}
    folder.write_index(remote)
    return sync(library, passphrase)


TRANSLATIONS_EN = {
    "Cartella di sincronizzazione non raggiungibile: {p}": "Sync folder not reachable: {p}",
    "Cartella di sincronizzazione non raggiungibile: {e}": "Sync folder not reachable: {e}",
    "La cartella è stata creata da una versione successiva di Foliant.":
        "The folder was created by a later version of Foliant.",
    "Passphrase errata per questa cartella di sincronizzazione.": "Wrong passphrase for this sync folder.",
    "La cartella non contiene una libreria sincronizzata.": "The folder does not contain a synced library.",
    "File di sincronizzazione danneggiato.": "Sync file damaged.",
    "Indice di sincronizzazione danneggiato.": "Sync index damaged.",
    "Elemento sincronizzato danneggiato.": "Synced item damaged.",
    "La libreria non è collegata a una cartella di sincronizzazione.": "The library is not linked to a sync folder.",
    "La libreria è protetta: sbloccala per sincronizzarla.": "The library is protected: unlock it to sync it.",
    "{name} (conflitto)": "{name} (conflict)",
    "Elemento non recuperabile.": "Item cannot be recovered.",
}
