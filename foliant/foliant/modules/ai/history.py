"""Cronologia delle conversazioni della sessione (AI-18).

Cronologia della sessione: sparisce alla chiusura di Foliant. Le conversazioni restano
solo in memoria (regola di riservatezza: nessun nome, percorso o impronta di documento
viene scritto su disco); si possono esportare esplicitamente in Markdown o JSON in un
file scelto dall'utente. Con la cronologia disattivata non si conserva nulla.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import threading
from pathlib import Path

from foliant.api import _

from .engine import Conversation


class HistoryStore:
    """Archivio in memoria delle conversazioni della sessione corrente.

    `directory`, `retention_days`, `encrypt` e `secrets` sono accettati per compatibilità e
    ignorati: nulla viene letto o scritto su disco.
    """

    def __init__(self, directory: Path | None = None, enabled: bool = True, retention_days: int = 0,
                 encrypt: bool = False, secrets=None):
        self.enabled = enabled
        self._items: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        self.damaged: list = []     # compatibilità: niente file, niente danni
        self.locked = False

    def save(self, conv: Conversation) -> bool:
        """Conserva una copia della conversazione per la sessione. False se la cronologia è disattivata."""
        if not self.enabled:
            return False
        conv.updated = _dt.datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._items[conv.id] = copy.deepcopy(conv)
        return True

    def load_all(self) -> list[Conversation]:
        with self._lock:
            out = [copy.deepcopy(c) for c in self._items.values()]
        out.sort(key=lambda c: c.updated, reverse=True)
        return out

    def for_document(self, fingerprint: str, path: str = "") -> list[Conversation]:
        """Conversazioni della sessione sullo stesso contenuto (`path` è ignorato)."""
        if not fingerprint:
            return []
        return [c for c in self.load_all() if c.fingerprint == fingerprint]

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
        with self._lock:
            c = self._items.get(conv_id)
            return copy.deepcopy(c) if c is not None else None

    def rename(self, conv_id: str, title: str) -> None:
        with self._lock:
            c = self._items.get(conv_id)
            if c is not None:
                c.title = title

    def delete(self, conv_id: str) -> None:
        with self._lock:
            self._items.pop(conv_id, None)

    def delete_document(self, fingerprint: str) -> None:
        for c in self.for_document(fingerprint):
            self.delete(c.id)

    def delete_all(self) -> None:
        with self._lock:
            self._items.clear()

    def expire(self, now: _dt.datetime | None = None) -> int:
        """Compatibilità: la cronologia non sopravvive alla sessione, non c'è nulla da far scadere."""
        return 0


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
