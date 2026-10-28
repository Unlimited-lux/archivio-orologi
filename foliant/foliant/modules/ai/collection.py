"""Chat su più documenti (AI-13): raccolta, preparazione, contesto con marcatori documento › pagina."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from foliant.api import Document, DocumentError, FoliantError, _

from . import chunking, content, engine, prompts
from .config import data_dir, write_private
from .engine import Answer, Conversation
from .providers import ChatRequest, Message
from .render import PageSpace, find_refs

MAX_DOCUMENTS = 20


@dataclass
class Member:
    path: str
    fingerprint: str = ""
    title: str = ""
    excluded: str = ""          # motivo dell'esclusione ("" = utilizzabile)
    changed: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "fingerprint": self.fingerprint, "title": self.title}


@dataclass
class Collection:
    name: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    members: list[Member] = field(default_factory=list)
    conversations: list[Conversation] = field(default_factory=list)

    def add(self, path: str, document: Document | None = None) -> Member:
        """Aggiunge un documento (aperto o da disco). Solleva FoliantError oltre il limite."""
        if len([m for m in self.members if not m.excluded]) >= MAX_DOCUMENTS:
            raise FoliantError(_("Una raccolta può contenere al massimo {n} documenti.").format(n=MAX_DOCUMENTS))
        m = Member(str(path), title=Path(path).name if path else (document.display_name if document else "?"))
        try:
            doc = document or Document.open(path)
        except DocumentError as e:
            m.excluded = str(e)
            self.members.append(m)
            return m
        try:
            if not content.extraction_allowed(doc):
                m.excluded = _("permessi: l'estrazione del contenuto non è consentita")
            m.fingerprint = content.content_fingerprint(doc.pdf)
            if document is not None:
                m.title = document.display_name
        finally:
            if document is None:
                doc.close()
        for other in self.members:
            if other.fingerprint and other.fingerprint == m.fingerprint:
                return other   # stesso documento: una sola voce
        self.members.append(m)
        return m

    def remove(self, index: int) -> None:
        del self.members[index]

    def usable(self) -> list[Member]:
        return [m for m in self.members if not m.excluded]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "documents": [m.to_dict() for m in self.members],
                "conversations": [c.to_dict() for c in self.conversations]}

    @classmethod
    def from_dict(cls, d: dict) -> "Collection":
        c = cls(name=d.get("name", ""), id=d.get("id") or uuid.uuid4().hex[:12])
        c.members = [Member(m["path"], m.get("fingerprint", ""), m.get("title", "")) for m in d.get("documents", [])]
        c.conversations = [Conversation.from_dict(x) for x in d.get("conversations", [])]
        return c

    def check(self, open_documents: dict[str, Document] | None = None) -> list[str]:
        """Segnala documenti spostati o modificati dopo il salvataggio."""
        notes = []
        for m in self.members:
            doc = (open_documents or {}).get(m.path)
            try:
                d = doc or Document.open(m.path)
            except DocumentError as e:
                m.excluded = str(e)
                notes.append(_("«{t}» non è disponibile: {e}").format(t=m.title, e=e))
                continue
            try:
                fp = content.content_fingerprint(d.pdf)
            finally:
                if doc is None:
                    d.close()
            if m.fingerprint and fp != m.fingerprint:
                m.changed = True
                m.fingerprint = fp
                notes.append(_("«{t}» è cambiato: l'indice verrà aggiornato.").format(t=m.title))
        return notes


class CollectionStore:
    def __init__(self, directory: Path | None = None):
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir or data_dir() / "raccolte"

    def save(self, coll: Collection) -> Path:
        path = self.directory / f"{coll.id}.json"
        write_private(path, json.dumps(coll.to_dict(), ensure_ascii=False, indent=1))
        return path

    def load_all(self) -> list[Collection]:
        out = []
        try:
            files = sorted(self.directory.glob("*.json"))
        except OSError:
            return out
        for f in files:
            try:
                out.append(Collection.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return out

    def get(self, coll_id: str) -> Collection | None:
        return next((c for c in self.load_all() if c.id == coll_id), None)


def short_names(members: list[Member]) -> dict[str, str]:
    """Nomi brevi e distinti per i marcatori: A, B, … seguiti dal titolo."""
    out = {}
    for n, m in enumerate(members):
        letter = chr(ord("A") + n) if n < 26 else f"D{n + 1}"
        out[m.path] = f"{letter}"
    return out


def prepare(members: list[Member], open_documents: dict[str, Document] | None = None, cache=None,
            step=None) -> list[tuple[Member, list[content.PageText], PageSpace]]:
    """Testo di ogni documento utilizzabile (con le stesse esclusioni del singolo documento)."""
    out = []
    names = short_names(members)
    for n, m in enumerate(members):
        if m.excluded:
            continue
        if step:
            step(n / max(len(members), 1), _("Preparazione di «{t}»").format(t=m.title))
        doc = (open_documents or {}).get(m.path)
        try:
            d = doc or Document.open(m.path)
        except DocumentError as e:
            m.excluded = str(e)
            continue
        try:
            if not content.extraction_allowed(d):
                m.excluded = _("permessi: l'estrazione del contenuto non è consentita")
                continue
            fp = content.content_fingerprint(d.pdf)
            scope = engine.build_scope(d.pdf, engine.DOCUMENT, cache=cache, fingerprint=fp)
            space = scope.space
            space.doc_key = m.path
            space.name = names[m.path]
            out.append((m, scope.pages, space))
        finally:
            if doc is None:
                d.close()
    return out


SYSTEM = (" The content comes from several documents. Each page starts with a marker like `=== B › [p. 3] ===` "
          "where the letter identifies the document (see the list). Cite as [B › p. 3] (document letter, page "
          "label). Compare and combine information across documents when asked.")


def chat(job: engine.Job, conv: Conversation, prepared, question: str) -> Answer:
    profile = job.profile()
    # solo le lettere dei documenti: titoli e nomi dei file restano sul computer
    docs_list = ", ".join(space.name for _m, _p, space in prepared)
    system = engine._system(prompts.BASE, job.language) + SYSTEM + " Documents: " + docs_list
    history = conv.history()
    budget, out_tokens = engine.budget_for(profile, system, question)
    budget -= sum(len(h.content) for h in history)
    per_doc = max(400, budget // max(len(prepared), 1))
    parts = []
    for m, pages, space in prepared:
        text = chunking.format_pages(pages, space.name)
        if len(text) <= per_doc:
            parts.append(text)
        else:
            chunks = chunking.make_chunks(pages, max(400, per_doc // 4), doc=space.name)
            parts.append("\n\n".join(c.text for c in chunking.select_chunks(chunks, question, per_doc)))
    body = "<documents>\n" + "\n\n".join(parts) + "\n</documents>\n\nQuestion: " + question
    req = ChatRequest(system, history + [Message("user", body)], out_tokens)
    pages = []
    r = job.gateway.send(req, function="chat-collection", pages=pages, cancel=job.cancel, on_text=job.on_text,
                         on_wait=job.on_wait, profile=profile)
    spaces = [space for _m, _p, space in prepared]
    refs = [x.to_dict() for x in find_refs(r.text, spaces)]
    return Answer(r.text, engine._status(r), refs, [] if refs else [_("non basata su pagine specifiche")])
