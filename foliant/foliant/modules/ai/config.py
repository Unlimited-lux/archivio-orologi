"""Profili dei fornitori, chiavi nel portachiavi, consenso, registro degli invii (AI-01, AI-02).

La chiave API non viene mai scritta su disco da Foliant: sta nel portachiavi di
sistema (`keyring`) oppure, se il portachiavi manca, solo in memoria per la
sessione corrente.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from foliant.api import _

from . import providers

KEYRING_SERVICE = "foliant-ai"
SETTINGS_KEY = "ai/profiles"
ACTIVE_KEY = "ai/active_profile"


def data_dir() -> Path:
    """Cartella dati dell'assistente.

    Contiene solo dati senza riferimenti ai documenti: libreria bibliografica, attività
    personalizzate, libreria grafica e contatori del limite di utilizzo. Cronologia delle
    conversazioni, registro degli invii, raccolte e indici dei documenti restano in memoria
    per la sessione (regola di riservatezza: nessun nome, percorso o impronta di documento su disco).
    """
    env = os.environ.get("FOLIANT_AI_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "foliant" / "ai"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_private(path: Path, text: str) -> None:
    """Scrittura atomica leggibile solo dall'utente."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- catalogo
CATALOG = {
    "anthropic": {
        "name": "Anthropic",
        "protocol": "anthropic",
        "base_url": providers.ANTHROPIC_URL,
        "models": list(providers.ANTHROPIC_MODELS),
        "context_window": 1_000_000,
        "images": True,
        "privacy_url": "https://www.anthropic.com/legal/privacy",
    },
    "custom": {
        "name": "Endpoint personalizzato",
        "protocol": "openai",
        "base_url": "http://localhost:11434/v1",
        "models": [],
        "context_window": 8192,
        "images": False,
        "privacy_url": "",
    },
}

DEFAULT_CONTEXT_WINDOW = 8192   # valore prudente se sconosciuto


@dataclass
class Profile:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    provider: str = "anthropic"          # chiave del catalogo
    base_url: str = providers.ANTHROPIC_URL
    protocol: str = "anthropic"
    model: str = "claude-opus-5-5"
    image_model: str = ""
    embed_model: str = ""                # modello per vettori semantici (facoltativo)
    context_window: int = 1_000_000      # token
    max_output: int = 4096
    connect_timeout: float = 15.0
    idle_timeout: float = 60.0
    privacy_url: str = ""
    images: bool = True                  # il modello accetta immagini
    force_capabilities: bool = False
    usage_limit: int = 0                 # richieste; 0 = nessun limite
    usage_period: str = "day"            # day | month
    requires_key: bool = True
    verified: bool = False
    consent: bool = False
    insecure_confirmed: bool = False
    models: list[str] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, key: str, **kw) -> "Profile":
        c = CATALOG.get(key, CATALOG["custom"])
        p = cls(name=_(c["name"]), provider=key, base_url=c["base_url"], protocol=c["protocol"],
                model=(c["models"][0] if c["models"] else ""), context_window=c["context_window"],
                images=c["images"], privacy_url=c["privacy_url"], models=list(c["models"]),
                requires_key=key != "custom")
        for k, v in kw.items():
            setattr(p, k, v)
        return p

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def host(self) -> str:
        import urllib.parse
        return urllib.parse.urlparse(self.base_url).hostname or ""

    @property
    def is_local(self) -> bool:
        return providers.is_local_url(self.base_url)

    @property
    def insecure(self) -> bool:
        return providers.is_insecure_remote(self.base_url)

    def can_activate(self) -> tuple[bool, str]:
        if not self.verified:
            return False, _("Verifica la connessione prima di attivare il profilo.")
        if self.insecure and not self.insecure_confirmed:
            return False, _("Conferma l'avviso sulla connessione non cifrata.")
        return True, ""


# ---------------------------------------------------------------- segreti
class KeyringUnavailable(Exception):
    pass


class SecretStore:
    """Chiavi nel portachiavi di sistema; in alternativa solo in memoria."""

    def __init__(self) -> None:
        self._session: dict[str, str] = {}
        self._cache: dict[str, str | None] = {}   # il portachiavi si interroga una volta per voce

    @staticmethod
    def _backend():
        try:
            import keyring
            from keyring.backends import fail
        except Exception as e:  # noqa: BLE001
            raise KeyringUnavailable(str(e)) from e
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__:
            raise KeyringUnavailable(_("Nessun portachiavi di sistema disponibile."))
        return keyring

    def available(self) -> bool:
        try:
            self._backend()
            return True
        except KeyringUnavailable:
            return False

    def set(self, profile_id: str, key: str, *, session_only: bool = False) -> None:
        key = (key or "").strip()
        if session_only:
            self._session[profile_id] = key
            return
        kr = self._backend()
        try:
            kr.set_password(KEYRING_SERVICE, profile_id, key)
        except Exception as e:  # noqa: BLE001
            raise KeyringUnavailable(_("Impossibile salvare la chiave nel portachiavi: {e}").format(e=e)) from e
        self._session.pop(profile_id, None)
        self._cache[profile_id] = key

    def get(self, profile_id: str) -> str | None:
        if profile_id in self._session:
            return self._session[profile_id]
        if self._cache.get(profile_id):
            return self._cache[profile_id]
        try:
            kr = self._backend()
            value = kr.get_password(KEYRING_SERVICE, profile_id)
        except KeyringUnavailable:
            return None
        except Exception:  # noqa: BLE001 - portachiavi bloccato o rifiutato
            return None
        if value:
            self._cache[profile_id] = value
        return value

    def delete(self, profile_id: str) -> None:
        self._session.pop(profile_id, None)
        self._cache.pop(profile_id, None)
        try:
            kr = self._backend()
            kr.delete_password(KEYRING_SERVICE, profile_id)
        except Exception:  # noqa: BLE001 - voce già assente o portachiavi mancante
            pass

    def is_session_only(self, profile_id: str) -> bool:
        return profile_id in self._session


# ---------------------------------------------------------------- profili
class ProfileStore:
    """Profili nelle impostazioni (senza chiavi)."""

    def __init__(self, settings, secrets: SecretStore | None = None):
        self.settings = settings
        self.secrets = secrets or SecretStore()

    def _raw(self) -> list[dict]:
        value = self.settings.value(SETTINGS_KEY, "")
        if not value:
            return []
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def profiles(self) -> list[Profile]:
        return [Profile.from_dict(d) for d in self._raw() if isinstance(d, dict)]

    def get(self, profile_id: str | None) -> Profile | None:
        for p in self.profiles():
            if p.id == profile_id:
                return p
        return None

    def save(self, profile: Profile) -> None:
        items = [p for p in self.profiles() if p.id != profile.id]
        existing = self.get(profile.id)
        if existing is not None:
            idx = [p.id for p in self.profiles()].index(profile.id)
            items.insert(idx, profile)
        else:
            items.append(profile)
        self.settings.setValue(SETTINGS_KEY, json.dumps([p.to_dict() for p in items]))
        self.settings.sync()

    def duplicate(self, profile_id: str) -> Profile | None:
        p = self.get(profile_id)
        if p is None:
            return None
        q = copy.deepcopy(p)
        q.id = uuid.uuid4().hex[:12]
        q.name = _("{name} (copia)").format(name=p.name)
        q.verified = False
        q.consent = False
        key = self.secrets.get(p.id)
        if key:
            try:
                self.secrets.set(q.id, key, session_only=self.secrets.is_session_only(p.id))
            except KeyringUnavailable:
                self.secrets.set(q.id, key, session_only=True)
        self.save(q)
        return q

    def delete(self, profile_id: str) -> None:
        items = [p for p in self.profiles() if p.id != profile_id]
        self.settings.setValue(SETTINGS_KEY, json.dumps([p.to_dict() for p in items]))
        self.secrets.delete(profile_id)
        if self.active_id() == profile_id:
            self.settings.remove(ACTIVE_KEY)
        self.settings.sync()

    def active_id(self) -> str | None:
        v = self.settings.value(ACTIVE_KEY, "")
        return str(v) if v else None

    def set_active(self, profile_id: str | None) -> None:
        if profile_id is None:
            self.settings.remove(ACTIVE_KEY)
        else:
            p = self.get(profile_id)
            if p is None:
                raise ValueError(profile_id)
            ok, why = p.can_activate()
            if not ok:
                raise ValueError(why)
            self.settings.setValue(ACTIVE_KEY, profile_id)
        self.settings.sync()

    def enabled(self) -> bool:
        v = self.settings.value("ai/enabled", True)
        return v not in (False, "false", "0", 0)

    def set_enabled(self, value: bool) -> None:
        self.settings.setValue("ai/enabled", bool(value))

    def active(self, check_key: bool = True) -> Profile | None:
        """Profilo utilizzabile: attivo, verificato e con la chiave (se richiesta).

        Con `check_key=False` il portachiavi non viene interrogato (all'avvio non si chiede
        lo sblocco: avviene al primo uso dell'assistente).
        """
        if not self.enabled():
            return None
        p = self.get(self.active_id())
        if p is None or not p.verified:
            return None
        if check_key and p.requires_key and not self.secrets.get(p.id):
            return None
        return p

    def provider_for(self, profile: Profile, model: str | None = None) -> providers.Provider:
        key = self.secrets.get(profile.id) if profile.requires_key or self.secrets.get(profile.id) else None
        return providers.make_provider(profile.protocol, profile.base_url, key, model or profile.model,
                                       profile.connect_timeout, profile.idle_timeout)

    def option(self, name: str, default=None):
        v = self.settings.value(f"ai/{name}", default)
        if isinstance(default, bool):
            return v not in (False, "false", "0", 0, None)
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        return v

    def set_option(self, name: str, value) -> None:
        self.settings.setValue(f"ai/{name}", value)


# ---------------------------------------------------------------- registro
class SendLog:
    """Registro degli invii della sessione: nessun contenuto, solo metadati (AI-02).

    Le voci restano in memoria e spariscono alla chiusura di Foliant: nessun riferimento ai
    documenti (né nome, né percorso, né impronta) viene scritto su disco. Per il limite di
    utilizzo giornaliero/mensile si conservano solo contatori aggregati per profilo
    (`contatori-uso.json` nella cartella dati), senza alcun dato sui documenti.
    `path` è accettato per compatibilità e ignorato.
    """

    COUNTERS_FILE = "contatori-uso.json"

    def __init__(self, path: Path | None = None, *, counters_path: Path | None = None):
        self._entries: list[dict] = []
        self._counters_path = counters_path
        self._counters: dict | None = None
        self._lock = threading.Lock()

    @property
    def counters_path(self) -> Path:
        return self._counters_path or data_dir() / self.COUNTERS_FILE

    def _load_counters(self) -> dict:
        if self._counters is None:
            try:
                data = json.loads(self.counters_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            self._counters = data if isinstance(data, dict) else {}
        return self._counters

    def _count(self, profile_id: str, now: _dt.datetime) -> None:
        counters = self._load_counters()
        day, month = now.date().isoformat(), now.strftime("%Y-%m")
        c = counters.get(profile_id)
        c = dict(c) if isinstance(c, dict) else {}
        if c.get("day") != day:
            c["day"], c["day_count"] = day, 0
        if c.get("month") != month:
            c["month"], c["month_count"] = month, 0
        try:
            c["day_count"] = int(c.get("day_count") or 0) + 1
            c["month_count"] = int(c.get("month_count") or 0) + 1
        except (TypeError, ValueError):
            c["day_count"] = c["month_count"] = 1
        counters[profile_id] = c
        try:
            write_private(self.counters_path, json.dumps(counters))
        except OSError:
            pass            # disco pieno o in sola lettura: il conteggio resta in memoria

    def add(self, *, profile: Profile, function: str, pages: list[int] | None, chars: int, images: int,
            outcome: str, fingerprint: str = "", input_tokens: int | None = None,
            output_tokens: int | None = None) -> dict:
        """Aggiunge una voce. `fingerprint` è accettato per compatibilità e non viene registrato."""
        now = _dt.datetime.now()
        entry = {
            "time": now.isoformat(timespec="seconds"),
            "profile": profile.name, "profile_id": profile.id, "domain": profile.host, "model": profile.model,
            "function": function,
            "pages": [p + 1 for p in (pages or [])], "chars": chars, "images": images, "outcome": outcome,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        }
        with self._lock:
            self._entries.append(entry)
            self._count(profile.id, now)
        return entry

    def entries(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._entries]

    def clear(self) -> None:
        """Svuota l'elenco della sessione (i contatori del limite di utilizzo restano)."""
        with self._lock:
            self._entries.clear()

    def export(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.entries(), ensure_ascii=False, indent=2), encoding="utf-8")

    def count_since(self, profile_id: str, period: str) -> int:
        now = _dt.datetime.now()
        with self._lock:
            c = self._load_counters().get(profile_id)
        if not isinstance(c, dict):
            return 0
        try:
            if period == "day":
                return int(c.get("day_count") or 0) if c.get("day") == now.date().isoformat() else 0
            return int(c.get("month_count") or 0) if c.get("month") == now.strftime("%Y-%m") else 0
        except (TypeError, ValueError):
            return 0

    def limit_reached(self, profile: Profile) -> bool:
        return bool(profile.usage_limit) and self.count_since(profile.id, profile.usage_period) >= profile.usage_limit


# ---------------------------------------------------------------- dati delle versioni precedenti
PRIVACY_CLEANUP_KEY = "ai/privacy_cleanup_v1"
# cartelle della cartella dati in cui le versioni precedenti salvavano dati sui documenti
LEGACY_DIRS = {
    "cronologia": ("*.json", "*.danneggiato", ".*.tmp"),   # conversazioni con percorso e impronta
    "cache": ("*.json", ".*.tmp"),                         # testo delle pagine per impronta del documento
    "raccolte": ("*.json", ".*.tmp"),                      # raccolte con i percorsi dei documenti
}
# registro degli invii con l'impronta del documento (e suoi temporanei)
LEGACY_FILES = ("registro-invii.jsonl", ".registro-invii.jsonl.*.tmp")
LEGACY_SETTINGS = ("ai/history_encrypt", "ai/retention_days")
_LEGACY_ENC_PREFIX = "FOLIANT-ENC1\n"


def _unlink_quietly(f: Path) -> None:
    try:
        if f.is_symlink() or f.is_file():
            f.unlink()
    except OSError:
        pass


def purge_legacy_data(settings, *, secrets: SecretStore | None = None) -> bool:
    """Cancella i dati sui documenti salvati dalle versioni precedenti (una volta sola).

    Solo nomi noti dentro `data_dir()` (mai l'intera cartella, mai file sconosciuti) e le
    preferenze tolte; silenziosa: gli errori del disco vengono ignorati. Il portachiavi viene
    toccato solo se la vecchia cronologia era cifrata (per cancellare la chiave `history-key`).
    Il segno `ai/privacy_cleanup_v1` si scrive solo se c'era qualcosa da cancellare: su un profilo
    nuovo l'avvio non scrive nulla. Restituisce True se sono stati trovati dati da cancellare.
    """
    if settings is not None:
        done = settings.value(PRIVACY_CLEANUP_KEY, False)
        if done not in (False, "false", "0", 0, None, ""):
            return False
    base = data_dir()
    found = False
    encrypted = False
    for name, patterns in LEGACY_DIRS.items():
        folder = base / name
        try:
            if folder.is_symlink() or not folder.is_dir():
                continue
        except OSError:
            continue
        found = True
        for pattern in patterns:
            try:
                files = list(folder.glob(pattern))
            except OSError:
                continue
            for f in files:
                if name == "cronologia" and not encrypted and f.suffix == ".json" and not f.is_symlink():
                    try:
                        with open(f, encoding="utf-8", errors="ignore") as fh:
                            encrypted = fh.read(len(_LEGACY_ENC_PREFIX)) == _LEGACY_ENC_PREFIX
                    except OSError:
                        pass
                _unlink_quietly(f)
        try:
            folder.rmdir()          # solo se vuota
        except OSError:
            pass
    for pattern in LEGACY_FILES:
        try:
            files = list(base.glob(pattern))
        except OSError:
            continue
        for f in files:
            found = True
            _unlink_quietly(f)
    if settings is not None:
        for key in LEGACY_SETTINGS:
            if settings.contains(key):
                found = True
                settings.remove(key)
        if found:
            settings.setValue(PRIVACY_CLEANUP_KEY, True)
    if encrypted:
        try:
            (secrets or SecretStore()).delete("history-key")
        except Exception:  # noqa: BLE001 - portachiavi assente o bloccato: niente da fare
            pass
    return found


def text_fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()
