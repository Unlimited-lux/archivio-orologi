"""Documenti lunghi: marcatori di pagina, blocchi, scelta dei passaggi, cache (AI-14)."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .content import PageText

CHARS_PER_TOKEN = 3.0          # stima prudente (margine di sicurezza)
MAX_CONFIRM_REQUESTS = 20
MAX_CONFIRM_CHARS = 200_000


def estimate_tokens(text: str | int) -> int:
    n = text if isinstance(text, int) else len(text)
    return int(math.ceil(n / CHARS_PER_TOKEN))


def page_marker(index: int, label: str, doc: str | None = None) -> str:
    head = f"{doc} › " if doc else ""
    if label != str(index + 1):
        return f"=== {head}[p. {label}] (pagina fisica {index + 1}) ==="
    return f"=== {head}[p. {label}] ==="


def format_pages(pages: list[PageText], doc: str | None = None) -> str:
    return "\n\n".join(f"{page_marker(p.index, p.label, doc)}\n{p.text}" for p in pages)


@dataclass
class Chunk:
    pages: list[int]
    labels: list[str]
    start: int          # carattere iniziale nel testo della prima pagina
    end: int            # carattere finale nel testo dell'ultima pagina
    text: str           # con i marcatori di pagina
    doc: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def input_budget_chars(context_window: int, max_output: int, reserve_tokens: int = 1500) -> int:
    """Caratteri di contenuto che stanno nella finestra, lasciando spazio a istruzioni e risposta."""
    tokens = max(500, int(context_window) - int(max_output) - reserve_tokens)
    return int(tokens * CHARS_PER_TOKEN * 0.9)


def output_tokens_for(context_window: int, wanted: int = 4096) -> int:
    return max(256, min(wanted, int(context_window) // 4))


_SENT = re.compile(r"(?<=[.!?;:])\s+")


def _split_long(text: str, size: int) -> list[str]:
    """Divide per paragrafi, poi per frasi, infine a lunghezza fissa."""
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= size:
            parts.append(para)
            continue
        cur = ""
        for sent in _SENT.split(para):
            while len(sent) > size:
                parts.append(sent[:size])
                sent = sent[size:]
            if len(cur) + len(sent) + 1 > size and cur:
                parts.append(cur)
                cur = sent
            else:
                cur = (cur + " " + sent).strip()
        if cur:
            parts.append(cur)
    # ricompone pezzi piccoli fino alla dimensione
    out: list[str] = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) + 2 > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        out.append(cur)
    return out


def make_chunks(pages: list[PageText], size_chars: int, overlap: int = 200, doc: str = "") -> list[Chunk]:
    """Blocchi lungo i confini di pagina e di paragrafo, ciascuno con i propri marcatori."""
    size_chars = max(400, size_chars)
    chunks: list[Chunk] = []
    cur_pages: list[PageText] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur_pages, cur_len
        if cur_pages:
            chunks.append(Chunk([p.index for p in cur_pages], [p.label for p in cur_pages], 0,
                                len(cur_pages[-1].text), format_pages(cur_pages, doc or None), doc))
        cur_pages, cur_len = [], 0

    for p in pages:
        marker = len(page_marker(p.index, p.label, doc or None)) + 2
        need = len(p.text) + marker
        if need <= size_chars:
            if cur_len + need > size_chars:
                flush()
            cur_pages.append(p)
            cur_len += need
            continue
        flush()
        pieces = _split_long(p.text, size_chars - marker - overlap)
        pos = 0
        for n, piece in enumerate(pieces):
            prefix = ""
            if n and overlap:
                prefix = pieces[n - 1][-overlap:]
            body = (prefix + " " + piece).strip() if prefix else piece
            start = p.text.find(piece[:40], pos) if piece else pos
            start = max(start, 0)
            pos = start + len(piece)
            chunks.append(Chunk([p.index], [p.label], start, pos,
                                f"{page_marker(p.index, p.label, doc or None)}\n{body}", doc))
    flush()
    return chunks


_WORD = re.compile(r"\w{3,}", re.UNICODE)


def _terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def select_chunks(chunks: list[Chunk], query: str, budget_chars: int) -> list[Chunk]:
    """Sceglie localmente i blocchi più pertinenti (BM25) entro il budget, in ordine di documento."""
    if sum(len(c.text) for c in chunks) <= budget_chars:
        return list(chunks)
    q = set(_terms(query))
    n = len(chunks)
    docs = [Counter(_terms(c.text)) for c in chunks]
    avg = sum(sum(d.values()) for d in docs) / max(n, 1)
    df = Counter()
    for d in docs:
        for t in q:
            if t in d:
                df[t] += 1
    scores = []
    for i, d in enumerate(docs):
        length = sum(d.values()) or 1
        s = 0.0
        for t in q:
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(avg, 1)))
        scores.append((s, -i, i))
    scores.sort(reverse=True)
    chosen: list[int] = []
    used = 0
    for s, _neg, i in scores:
        size = len(chunks[i].text)
        if used + size > budget_chars:
            continue
        chosen.append(i)
        used += size
    return [chunks[i] for i in sorted(chosen)]


# --------------------------------------------------------------------- cache
@dataclass
class DocumentIndex:
    fingerprint: str
    pages: list[PageText] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"fingerprint": self.fingerprint,
                           "pages": [asdict(p) for p in self.pages]}, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "DocumentIndex":
        data = json.loads(text)
        return cls(data["fingerprint"], [PageText(**p) for p in data["pages"]])


class IndexCache:
    """Testo delle pagine (con OCR temporaneo) per impronta del contenuto.

    In memoria e su disco (cartella dati, leggibile solo dall'utente), con
    dimensione massima; si svuota con `clear()`.
    """

    MAX_FILES = 50

    def __init__(self, directory: Path | None = None, persist: bool = True):
        self._mem: dict[str, DocumentIndex] = {}
        self._dir = directory
        self.persist = persist
        self.builds = 0

    @property
    def directory(self) -> Path:
        from .config import data_dir
        return self._dir or data_dir() / "cache"

    def get(self, fingerprint: str) -> DocumentIndex | None:
        if fingerprint in self._mem:
            return self._mem[fingerprint]
        if not self.persist:
            return None
        path = self.directory / f"{fingerprint[:32]}.json"
        try:
            idx = DocumentIndex.from_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if idx.fingerprint != fingerprint:
            return None
        self._mem[fingerprint] = idx
        return idx

    def put(self, index: DocumentIndex) -> None:
        self._mem[index.fingerprint] = index
        if not self.persist:
            return
        from .config import write_private
        try:
            write_private(self.directory / f"{index.fingerprint[:32]}.json", index.to_json())
            files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for old in files[:-self.MAX_FILES]:
                old.unlink()
        except OSError:
            pass

    def update_pages(self, fingerprint: str, pages: list[PageText]) -> None:
        idx = self.get(fingerprint) or DocumentIndex(fingerprint)
        by = {p.index: p for p in idx.pages}
        for p in pages:
            by[p.index] = p
        idx.pages = [by[k] for k in sorted(by)]
        self.put(idx)

    def clear(self) -> None:
        self._mem.clear()
        try:
            for f in self.directory.glob("*.json"):
                f.unlink()
        except OSError:
            pass
