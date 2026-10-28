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


class HistoryStore:
    def __init__(self, directory: Path | None = None, enabled: bool = True, retention_days: int = 0):
        self._dir = directory
        self.enabled = enabled
        self.retention_days = retention_days
        self.damaged: list[Path] = []

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
            write_private(self._path(conv.id), json.dumps(conv.to_dict(), ensure_ascii=False, indent=1))
        except OSError:
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
                conv = Conversation.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
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
            return Conversation.from_dict(json.loads(self._path(conv_id).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
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
