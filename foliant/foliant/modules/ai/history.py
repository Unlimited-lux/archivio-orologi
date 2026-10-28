"""Cronologia locale delle conversazioni (AI-18).

Un file JSON per conversazione nella cartella dati dell'utente (permessi 0600).
Nessuna sincronizzazione. Con la cronologia disattivata non si scrive nulla.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from foliant.api import _

from .config import data_dir, write_private
from .engine import Conversation

RETENTION_CHOICES = (0, 30, 90, 365)   # giorni; 0 = mai


ENC_PREFIX = "FOLIANT-ENC1\n"


class HistoryStore:
    """Archivio della cronologia. Con `encrypt` i file sono cifrati (chiave nel portachiavi)."""

    def __init__(self, directory: Path | None = None, enabled: bool = True, retention_days: int = 0,
                 encrypt: bool = False, secrets=None):
        self._dir = directory
        self.enabled = enabled
        self.retention_days = retention_days
        self.encrypt = encrypt
        self.secrets = secrets
        self.damaged: list[Path] = []

    # ----------------------------------------------------------------- cifratura
    def _fernet(self, create: bool):
        from cryptography.fernet import Fernet
        from .config import SecretStore
        secrets = self.secrets or SecretStore()
        key = secrets.get("history-key")
        if not key and create:
            key = Fernet.generate_key().decode("ascii")
            secrets.set("history-key", key)   # KeyringUnavailable se manca il portachiavi
        return Fernet(key.encode("ascii")) if key else None

    def _dump(self, conv: Conversation) -> str:
        text = json.dumps(conv.to_dict(), ensure_ascii=False, indent=1)
        if self.encrypt:
            return ENC_PREFIX + self._fernet(True).encrypt(text.encode("utf-8")).decode("ascii")
        return text

    def _read(self, f: Path) -> Conversation:
        raw = f.read_text(encoding="utf-8")
        if raw.startswith(ENC_PREFIX):
            fernet = self._fernet(False)
            if fernet is None:
                raise ValueError("chiave della cronologia non disponibile")
            raw = fernet.decrypt(raw[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
        return Conversation.from_dict(json.loads(raw))

    @property
    def directory(self) -> Path:
        return self._dir or data_dir() / "cronologia"

    def _path(self, conv_id: str) -> Path:
        safe = "".join(c for c in conv_id if c.isalnum() or c in "-_")[:64]
        return self.directory / f"{safe}.json"

    def save(self, conv: Conversation) -> bool:
        """Salva la conversazione. Restituisce False se disattivata o se il disco non lo consente."""
        if not self.enabled:
            return False
        conv.updated = _dt.datetime.now().isoformat(timespec="seconds")
        try:
            write_private(self._path(conv.id), self._dump(conv))
        except Exception:  # noqa: BLE001 - disco pieno o portachiavi mancante: resta in memoria
            return False
        return True

    def load_all(self) -> list[Conversation]:
        out = []
        self.damaged = []
        try:
            files = sorted(self.directory.glob("*.json"))
        except OSError:
            return out
        for f in files:
            try:
                conv = self._read(f)
            except Exception:  # noqa: BLE001 - file danneggiato o non decifrabile
                self._isolate(f)
                continue
            out.append(conv)
        out.sort(key=lambda c: c.updated, reverse=True)
        return out

    def _isolate(self, f: Path) -> None:
        target = f.with_suffix(".danneggiato")
        try:
            os.replace(f, target)
            self.damaged.append(target)
        except OSError:
            self.damaged.append(f)

    def for_document(self, fingerprint: str, path: str = "") -> list[Conversation]:
        return [c for c in self.load_all() if c.fingerprint == fingerprint or (path and c.path == path)]

    def search(self, text: str = "", function: str = "", since: str = "") -> list[Conversation]:
        text = text.lower().strip()
        out = []
        for c in self.load_all():
            if function and c.function != function:
                continue
            if since and c.updated < since:
                continue
            if text and text not in c.title.lower() and not any(text in m.text.lower() for m in c.messages):
                continue
            out.append(c)
        return out

    def get(self, conv_id: str) -> Conversation | None:
        try:
            return self._read(self._path(conv_id))
        except Exception:  # noqa: BLE001
            return None

    def rename(self, conv_id: str, title: str) -> None:
        c = self.get(conv_id)
        if c is not None:
            c.title = title
            self.save(c)

    def delete(self, conv_id: str) -> None:
        try:
            self._path(conv_id).unlink()
        except OSError:
            pass

    def delete_document(self, fingerprint: str) -> None:
        for c in self.for_document(fingerprint):
            self.delete(c.id)

    def delete_all(self) -> None:
        try:
            for f in list(self.directory.glob("*.json")) + list(self.directory.glob("*.danneggiato")):
                f.unlink()
        except OSError:
            pass

    def expire(self, now: _dt.datetime | None = None) -> int:
        """Elimina le conversazioni più vecchie del periodo di conservazione."""
        if not self.retention_days:
            return 0
        now = now or _dt.datetime.now()
        limit = now - _dt.timedelta(days=self.retention_days)
        n = 0
        for c in self.load_all():
            try:
                updated = _dt.datetime.fromisoformat(c.updated)
            except ValueError:
                continue
            if updated < limit:
                self.delete(c.id)
                n += 1
        return n


def export_markdown(conv: Conversation) -> str:
    lines = [f"# {conv.title or _('Conversazione')}", "",
             f"*{conv.created} · {conv.model}*", ""]
    for m in conv.messages:
        who = _("Utente") if m.role == "user" else _("Assistente")
        lines.append(f"## {who} — {m.time}")
        if m.flags:
            lines.append("_" + " · ".join(m.flags) + "_")
        lines.append("")
        lines.append(m.text)
        lines.append("")
    return "\n".join(lines)


def export_json(conv: Conversation) -> str:
    return json.dumps(conv.to_dict(), ensure_ascii=False, indent=2)
