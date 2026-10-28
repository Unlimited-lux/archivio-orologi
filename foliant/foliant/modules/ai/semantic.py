"""Ricerca per significato nel documento (AI-21).

Con un modello per vettori semantici (endpoint compatibile OpenAI, `/embeddings`)
i passaggi sono ordinati per similarità; senza, l'app sceglie localmente i
candidati e chiede al modello di chat di ordinarli (più richieste: lo si dichiara).
I vettori restano nella cache locale, indicizzati per impronta del contenuto.
"""

from __future__ import annotations

import json
import math
import re
import urllib.request
from dataclasses import dataclass, field

import pymupdf

from foliant.api import ENGINE_LOCK, _

from . import content, engine, prompts
from .chunking import select_chunks
from .providers import AIError, ChatRequest, Message, OpenAICompatProvider


@dataclass
class Passage:
    page: int
    text: str
    rects: list[tuple] = field(default_factory=list)


@dataclass
class Result:
    page: int
    excerpt: str
    score: float
    rects: list[tuple] = field(default_factory=list)


def passages(pdf: pymupdf.Document, max_chars: int = 600) -> list[Passage]:
    """Passaggi = blocchi di testo (paragrafi), con i rettangoli delle parole."""
    out: list[Passage] = []
    with ENGINE_LOCK:
        n = pdf.page_count
    for i in range(n):
        words, _e = content.safe_words(pdf, i)
        blocks: dict[int, list[tuple]] = {}
        for w in words:
            blocks.setdefault(w[5], []).append(w)
        for ws in blocks.values():
            text = content._words_text(ws)
            if not text.strip():
                continue
            # paragrafi lunghi divisi senza perdere i rettangoli
            cur: list[tuple] = []
            for w in ws:
                cur.append(w)
                if sum(len(x[4]) + 1 for x in cur) >= max_chars:
                    out.append(Passage(i, content._words_text(cur), [tuple(x[:4]) for x in cur]))
                    cur = []
            if cur:
                out.append(Passage(i, content._words_text(cur), [tuple(x[:4]) for x in cur]))
    return out


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)


def embed_openai(provider: OpenAICompatProvider, model: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(provider._url("embeddings"), data=body, headers=provider._headers(), method="POST")
    resp = provider._open(req)
    try:
        with resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (ValueError, OSError) as e:
        raise AIError("interrupted", str(e), host=provider.host) from e
    items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
    return [list(map(float, d["embedding"])) for d in items]


class VectorCache:
    def __init__(self) -> None:
        self._mem: dict[tuple[str, str], list[list[float]]] = {}

    def get(self, fingerprint: str, model: str):
        return self._mem.get((fingerprint, model))

    def put(self, fingerprint: str, model: str, vectors) -> None:
        self._mem[(fingerprint, model)] = vectors


def search(job: engine.Job, pdf: pymupdf.Document, query: str, *, embed=None, embed_model: str = "",
           fingerprint: str = "", cache: VectorCache | None = None, limit: int = 10) -> tuple[list[Result], str]:
    """Restituisce (risultati ordinati per pertinenza, nota per l'utente)."""
    if not content.extraction_allowed(pdf):
        raise content.ContentBlocked(content.blocked_reason(pdf))
    items = passages(pdf)
    if not items or not query.strip():
        return [], _("Nessun risultato pertinente: prova una ricerca esatta.")
    if embed is not None:
        vecs = cache.get(fingerprint, embed_model) if cache and fingerprint else None
        if vecs is None or len(vecs) != len(items):
            vecs = []
            for start in range(0, len(items), 64):
                job.step(start / len(items), _("Indicizzazione per significato…"))
                vecs.extend(embed([p.text for p in items[start:start + 64]]))
            if cache and fingerprint:
                cache.put(fingerprint, embed_model, vecs)
        qv = embed([query])[0]
        scored = sorted(((cosine(qv, v), n) for n, v in enumerate(vecs)), reverse=True)
        results = [Result(items[n].page, items[n].text, s, items[n].rects) for s, n in scored[:limit] if s > 0.2]
        note = "" if results else _("Nessun risultato pertinente: prova una ricerca esatta.")
        return results, note
    # modalità alternativa: candidati locali + ordinamento del modello di chat
    profile = job.profile()
    from .chunking import Chunk
    chunks = [Chunk([p.page], [str(p.page + 1)], 0, len(p.text), f"[{n}] (p. {p.page + 1}) {p.text}")
              for n, p in enumerate(items)]
    budget, out_tokens = engine.budget_for(profile, "x" * 800)
    expanded = query
    cand = select_chunks(chunks, expanded, budget)
    if len(cand) > 60:
        cand = cand[:60]
    system = engine._system(prompts.BASE, job.language) + (
        " You receive numbered passages. Return ONLY a JSON array of the numbers of the passages relevant to the "
        "query by meaning (synonyms, related terms, paraphrases), most relevant first; [] if none.")
    body = "Query: " + query + "\n\n" + "\n".join(c.text for c in cand)
    req = ChatRequest(system, [Message("user", body)], min(out_tokens, 512))
    r = job.gateway.send(req, function="semantic-search", pages=sorted({c.pages[0] for c in cand}),
                         fingerprint=fingerprint, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    try:
        order = engine.parse_json_payload(r.text)
    except ValueError:
        order = [int(x) for x in re.findall(r"\d+", r.text)]
    results = []
    for rank, n in enumerate(order if isinstance(order, list) else []):
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(items):
            p = items[n]
            results.append(Result(p.page, p.text, 1.0 / (rank + 1), p.rects))
    note = _("Senza un modello per vettori la ricerca usa il modello di chat e richiede più richieste.")
    if not results:
        note = _("Nessun risultato pertinente: prova una ricerca esatta.")
    return results[:limit], note
