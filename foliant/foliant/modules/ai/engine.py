"""Logica dell'assistente senza interfaccia: invii con ritentativi, ambito, funzioni IA.

Tutto qui gira anche in un thread di lavoro: nessun widget, solo callback.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import pymupdf

from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, _

from . import chunking, content, prompts
from .chunking import IndexCache, estimate_tokens, input_budget_chars, make_chunks, output_tokens_for
from .config import Profile, ProfileStore, SendLog
from .content import PageText
from .providers import CONTEXT, LIMIT, AIError, ChatRequest, ChatResult, ImagePart, Message
from .render import PageSpace, find_refs, plain_text

MAX_RETRIES = 3
MAX_WAIT = 60.0


class NotConfigured(FoliantError):
    def __init__(self) -> None:
        super().__init__(_("Configura un fornitore nelle impostazioni dell'Assistente IA."))


class ConsentRequired(FoliantError):
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        super().__init__(_("Accetta l'informativa del profilo prima di inviare dati."))


# =================================================================== gateway
class Gateway:
    """Unico punto da cui partono le richieste: controlli, ritentativi, registro."""

    def __init__(self, store: ProfileStore, log: SendLog | None = None, *,
                 provider_factory: Callable | None = None, sleep: Callable[[float], None] | None = None):
        self.store = store
        self.log = log or SendLog()
        self.provider_factory = provider_factory
        self._sleep = sleep
        self._lock = threading.Lock()
        self.active_requests = 0
        self.max_parallel = 2
        # profilo risolto nel thread dell'interfaccia (le impostazioni non si leggono dai thread)
        self.fixed_profile: Profile | None = None

    def profile(self) -> Profile:
        p = self.fixed_profile or self.store.active()
        if p is None:
            raise NotConfigured()
        return p

    def provider(self, profile: Profile, model: str | None = None):
        if self.provider_factory is not None:
            return self.provider_factory(profile)
        return self.store.provider_for(profile, model)

    def _wait(self, seconds: float, cancel: threading.Event | None, on_wait) -> None:
        if self._sleep is not None:
            if on_wait:
                on_wait(seconds)
            self._sleep(seconds)
            return
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            if on_wait:
                on_wait(left)
            if cancel is not None and cancel.wait(min(0.25, left)):
                raise OperationCancelled()
            elif cancel is None:
                time.sleep(min(0.25, left))

    def send(self, request: ChatRequest, *, function: str, pages: list[int] | None = None, fingerprint: str = "",
             on_text: Callable[[str], None] | None = None, cancel: threading.Event | None = None,
             on_wait: Callable[[float], None] | None = None, profile: Profile | None = None) -> ChatResult:
        profile = profile or self.profile()
        if not profile.consent:
            raise ConsentRequired(profile)
        provider = self.provider(profile)
        attempt = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise OperationCancelled()
            if self.log.limit_reached(profile):
                raise AIError(LIMIT, "local usage limit", host=profile.host)
            received: list[str] = []

            def relay(t: str) -> None:
                received.append(t)
                if on_text:
                    on_text(t)

            try:
                result = provider.complete(request, relay, cancel)
            except AIError as e:
                key = self.store.secrets.get(profile.id) if self.provider_factory is None else None
                e.masked(key)
                self.log.add(profile=profile, function=function, pages=pages, chars=request.text_size(),
                             images=request.image_count(), outcome=e.kind, fingerprint=fingerprint)
                if e.transient and not received and attempt < MAX_RETRIES:
                    attempt += 1
                    wait = min(e.retry_after if e.retry_after is not None else float(2 ** (attempt - 1)), MAX_WAIT)
                    self._wait(wait, cancel, on_wait)
                    continue
                if received and not e.partial:
                    e.partial = "".join(received)
                raise
            self.log.add(profile=profile, function=function, pages=pages, chars=request.text_size(),
                         images=request.image_count(),
                         outcome="interrotto" if result.interrupted else ("incompleto" if result.truncated else "ok"),
                         fingerprint=fingerprint, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
            return result


# ===================================================================== ambito
DOCUMENT, PAGES, SELECTION, AREA, NONE = "document", "pages", "selection", "area", "none"


@dataclass
class Scope:
    """Contesto fissato al momento dell'invio."""

    kind: str = NONE
    page_indices: list[int] = field(default_factory=list)
    pages: list[PageText] = field(default_factory=list)
    selection: str = ""
    selection_page: int | None = None
    selection_rects: list[tuple] = field(default_factory=list)
    selection_parts: list[tuple[int, str]] = field(default_factory=list)   # selezione su più pagine
    paragraph: str = ""
    images: list[ImagePart] = field(default_factory=list)
    space: PageSpace | None = None
    fingerprint: str = ""
    excluded_pages: list[int] = field(default_factory=list)
    low_confidence_pages: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    doc_name: str = ""

    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.pages) + len(self.selection) + len(self.paragraph)

    def describe(self, profile: Profile | None = None, history_messages: int = 0) -> str:
        """Riga «Verrà inviato» (AI-02)."""
        parts = []
        if self.kind in (DOCUMENT, PAGES) and self.page_indices:
            parts.append(_("pagine {r}").format(r=compact_ranges(self.page_indices)))
        elif self.kind == SELECTION:
            parts.append(_("selezione"))
        elif self.kind == AREA:
            parts.append(_("area della pagina"))
        elif self.kind == NONE:
            parts.append(_("solo la richiesta"))
        if self.chars:
            parts.append(_("~{n} caratteri").format(n=f"{self.chars:,}".replace(",", ".")))
        if self.images:
            parts.append(_("{n} immagini").format(n=len(self.images)) if len(self.images) > 1 else _("1 immagine"))
        if history_messages:
            parts.append(_("{n} messaggi precedenti").format(n=history_messages))
        s = " · ".join(parts)
        if profile is not None:
            dest = _("elaborazione sul computer") if profile.is_local else profile.host
            s += f" → {dest} · {profile.model}"
        return s

    def preview(self) -> str:
        """Anteprima integrale del contenuto che verrà inviato."""
        out = []
        if self.notes:
            out.append(_("Esclusioni: ") + "; ".join(self.notes))
        if self.excluded_pages:
            out.append(_("Pagine escluse: {p}").format(p=", ".join(str(i + 1) for i in self.excluded_pages)))
        if self.pages:
            out.append(chunking.format_pages(self.pages))
        if self.selection:
            out.append(_("Selezione:") + "\n" + self.selection)
        if self.paragraph and self.paragraph != self.selection:
            out.append(_("Paragrafo circostante:") + "\n" + self.paragraph)
        if self.images:
            out.append(_("{n} immagini allegate").format(n=len(self.images)))
        return "\n\n".join(out)


def compact_ranges(indices: list[int]) -> str:
    nums = sorted(set(i + 1 for i in indices))
    out = []
    start = prev = None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}–{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}–{prev}" if prev != start else str(start))
    return ", ".join(out)


def page_space(pdf: pymupdf.Document, included: list[int], doc_key: str = "", name: str = "") -> PageSpace:
    with ENGINE_LOCK:
        n = pdf.page_count
    labels = {}
    for i in range(n):
        lab = content.page_label(pdf, i)
        labels.setdefault(lab, i)
        labels.setdefault(lab.lower(), i)
    return PageSpace(labels, set(included), n, doc_key, name)


def load_pages(pdf: pymupdf.Document, pages: list[int], *, cache: IndexCache | None = None, fingerprint: str = "",
               include_comments: bool = False, include_form_values: bool = False,
               progress=None) -> list[PageText]:
    """Testo delle pagine, riusando l'indice in cache finché il contenuto non cambia (AI-14)."""
    use_cache = cache is not None and fingerprint and not include_comments and not include_form_values
    cached = {}
    if use_cache:
        idx = cache.get(fingerprint)
        if idx is not None:
            cached = {p.index: p for p in idx.pages}
    out, built = [], []
    for n, i in enumerate(pages):
        if i in cached:
            out.append(cached[i])
            continue
        if progress is not None:
            progress.update(n / max(len(pages), 1), _("Lettura pagina {i} di {n}").format(i=n + 1, n=len(pages)))
        pt = content.extract_page(pdf, i, include_comments=include_comments, include_form_values=include_form_values)
        out.append(pt)
        built.append(pt)
    if use_cache and built:
        cache.builds += 1
        cache.update_pages(fingerprint, built)
    return out


def build_scope(pdf: pymupdf.Document, kind: str, *, pages: list[int] | None = None, selection=None,
                cache: IndexCache | None = None, fingerprint: str = "", exclude: list[int] | None = None,
                ocr_texts: dict[int, tuple[str, float | None]] | None = None, include_comments: bool = False,
                include_form_values: bool = False, doc_name: str = "", progress=None) -> Scope:
    """Fissa il contesto: testo delle pagine con le esclusioni di sicurezza."""
    if not content.extraction_allowed(pdf):
        raise content.ContentBlocked(content.blocked_reason(pdf))
    with ENGINE_LOCK:
        count = pdf.page_count
    scope = Scope(kind=kind, fingerprint=fingerprint, doc_name=doc_name)
    if kind == DOCUMENT:
        pages = list(range(count))
    if kind in (DOCUMENT, PAGES):
        pages = [p for p in (pages or []) if 0 <= p < count]
        exclude = set(exclude or [])
        keep = [p for p in pages if p not in exclude]
        scope.page_indices = keep
        scope.excluded_pages = [p for p in pages if p in exclude]
        texts = load_pages(pdf, keep, cache=cache, fingerprint=fingerprint, include_comments=include_comments,
                           include_form_values=include_form_values, progress=progress)
        for pt in texts:
            if ocr_texts and pt.index in ocr_texts:
                t, conf = ocr_texts[pt.index]
                pt = PageText(pt.index, pt.label, t, list(pt.excluded), True, conf)
                if conf is not None and conf < 0.6:
                    scope.low_confidence_pages.append(pt.index)
            scope.pages.append(pt)
            for note in pt.excluded:
                msg = _("pagina {p}: {n}").format(p=pt.label, n=note)
                scope.notes.append(msg)
        scope.space = page_space(pdf, keep, fingerprint, doc_name)
    elif kind == SELECTION and selection:
        # una selezione (`TextSelection`) o l'elenco `view.text_selections`, una per pagina
        sels = [x for x in (selection if isinstance(selection, (list, tuple)) else [selection]) if x is not None]
        first = sels[0]
        rects = [pymupdf.Rect(r) for r in first.rects]
        scope.selection_page = first.page
        scope.selection_rects = [tuple(r) for r in rects]
        if len(sels) == 1:
            scope.selection = content.selection_text(pdf, first.page, rects)
            scope.paragraph = content.surrounding_paragraph(pdf, first.page, rects)
        else:
            scope.selection_parts = [(x.page, content.selection_text(pdf, x.page, [pymupdf.Rect(r) for r in x.rects]))
                                     for x in sels]
            scope.selection = "\n".join(t for _p, t in scope.selection_parts if t)
        scope.page_indices = [x.page for x in sels]
        scope.space = page_space(pdf, scope.page_indices, fingerprint, doc_name)
        raw = sum(len((x.text or "").strip()) for x in sels)
        if len(scope.selection) < raw * 0.9:
            scope.notes.append(_("parte della selezione è nascosta o oscurata ed è stata esclusa"))
    return scope


# ===================================================================== conversazioni
@dataclass
class ChatMessage:
    role: str
    text: str
    time: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    status: str = "complete"          # complete | interrupted | error | incomplete
    function: str = "chat"
    model: str = ""
    scope: str = ""
    refs: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)   # parziale, non basata su pagine, …

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "ChatMessage":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    title: str = ""
    fingerprint: str = ""
    path: str = ""
    function: str = "chat"
    profile: str = ""
    model: str = ""
    created: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    updated: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    messages: list[ChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "messages"}
        d["messages"] = [m.to_dict() for m in self.messages]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        c = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "messages"})
        c.messages = [ChatMessage.from_dict(m) for m in d.get("messages", [])]
        return c

    def history(self) -> list[Message]:
        """Messaggi precedenti da reinviare (solo scambi completi della chat)."""
        out = []
        for m in self.messages:
            if m.role in ("user", "assistant") and m.status != "error" and m.text:
                out.append(Message(m.role, m.text))
        # l'API richiede alternanza a partire da «user»
        clean: list[Message] = []
        for m in out:
            if clean and clean[-1].role == m.role:
                clean[-1] = Message(m.role, clean[-1].content + "\n\n" + m.content)
            elif not clean and m.role != "user":
                continue
            else:
                clean.append(m)
        if clean and clean[-1].role == "user":
            clean.pop()
        return clean


# ===================================================================== funzioni
@dataclass
class Job:
    """Parametri comuni di un'esecuzione."""

    gateway: Gateway
    on_text: Callable[[str], None] | None = None
    cancel: threading.Event | None = None
    on_wait: Callable[[float], None] | None = None
    progress: Callable[[float, str], None] | None = None
    language: str = "it"

    def step(self, fraction: float, text: str) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise OperationCancelled()
        if self.progress:
            self.progress(fraction, text)

    def profile(self) -> Profile:
        return self.gateway.profile()


@dataclass
class Answer:
    text: str
    status: str = "complete"
    refs: list = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    data: object = None
    requests: int = 1


def _status(result: ChatResult) -> str:
    return "interrupted" if result.interrupted else ("incomplete" if result.truncated else "complete")


def _system(base: str, language: str) -> str:
    return base.format(language=prompts.language_name(language))


def _partial_flags(scope: Scope) -> list[str]:
    flags = []
    if scope.excluded_pages:
        flags.append(_("parziale: pagine {p} escluse").format(p=", ".join(str(i + 1) for i in scope.excluded_pages)))
    if scope.low_confidence_pages:
        flags.append(_("testo riconosciuto con bassa affidabilità (pagine {p})")
                     .format(p=", ".join(str(i + 1) for i in scope.low_confidence_pages)))
    return flags


def _ref_flags(text: str, scope: Scope) -> tuple[list, list[str]]:
    refs = find_refs(text, scope.space) if scope.space else []
    flags = []
    if scope.kind in (DOCUMENT, PAGES) and not refs:
        flags.append(_("non basata su pagine specifiche"))
    return [r.to_dict() for r in refs], flags


def budget_for(profile: Profile, system: str = "", extra: str = "") -> tuple[int, int]:
    """(caratteri di contenuto disponibili, token di risposta)."""
    out = output_tokens_for(profile.context_window, profile.max_output)
    chars = input_budget_chars(profile.context_window, out) - len(system) - len(extra)
    return max(400, chars), out


def chat(job: Job, conv: Conversation, scope: Scope, question: str) -> Answer:
    """Chat sul documento o generale (AI-04)."""
    profile = job.profile()
    general = scope.kind == NONE or not (scope.pages or scope.selection or scope.images)
    system = _system(prompts.GENERAL if general else prompts.CHAT, job.language)
    history = conv.history()
    hist_chars = sum(len(m.content) for m in history)
    budget, out_tokens = budget_for(profile, system, question)
    budget = max(400, budget - hist_chars)
    context_text = ""
    if scope.pages:
        full = chunking.format_pages(scope.pages)
        if len(full) <= budget:
            context_text = full
        else:
            chunks = make_chunks(scope.pages, max(400, budget // 6))
            context_text = "\n\n".join(c.text for c in chunking.select_chunks(chunks, question, budget))
    elif scope.selection:
        context_text = scope_text(scope)
    user = question if not context_text else (
        f"<document>\n{context_text}\n</document>\n\nQuestion: {question}")
    request = ChatRequest(system, history + [Message("user", user, list(scope.images))], out_tokens)
    try:
        result = job.gateway.send(request, function="chat", pages=scope.page_indices, fingerprint=scope.fingerprint,
                                  on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    except AIError as e:
        if e.kind != CONTEXT or not scope.pages:
            raise
        # contesto superato nonostante la stima: blocchi ridotti, un solo nuovo tentativo
        chunks = make_chunks(scope.pages, max(400, budget // 12))
        context_text = "\n\n".join(c.text for c in chunking.select_chunks(chunks, question, budget // 2))
        request = ChatRequest(system, history + [Message(
            "user", f"<document>\n{context_text}\n</document>\n\nQuestion: {question}", list(scope.images))],
            out_tokens)
        result = job.gateway.send(request, function="chat", pages=scope.page_indices, fingerprint=scope.fingerprint,
                                  on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    refs, flags = _ref_flags(result.text, scope) if not general else ([], [])
    return Answer(result.text, _status(result), refs, flags + _partial_flags(scope))


def _label(scope: Scope, page: int | None = None) -> str:
    page = scope.selection_page if page is None else page
    if page is None:
        return "?"
    for lab, idx in (scope.space.labels if scope.space else {}).items():
        if idx == page:
            return lab
    return str(page + 1)


def scope_text(scope: Scope) -> str:
    if scope.pages:
        return chunking.format_pages(scope.pages)
    if scope.selection_parts:
        return "\n\n".join(f"=== [p. {_label(scope, p)}] ===\n{t}" for p, t in scope.selection_parts if t)
    if scope.selection:
        return f"=== [p. {_label(scope)}] ===\n{scope.selection}"
    return ""


# ------------------------------------------------------------------ riassunto
@dataclass
class SummaryOptions:
    length: str = "medium"        # short | medium | detailed | custom
    words: int = 300
    form: str = "bullets"         # bullets | paragraphs | both


def plan_requests(scope: Scope, profile: Profile, system_len: int = 600) -> tuple[int, int]:
    """Stima (richieste, caratteri) per un'elaborazione a blocchi."""
    budget, _out = budget_for(profile, "x" * system_len)
    total = len(scope_text(scope))
    if total <= budget:
        return 1, total
    n = len(make_chunks(scope.pages, budget)) if scope.pages else max(1, total // budget + 1)
    combine = 1
    partial = n * 1200
    while partial > budget:
        combine += 1
        partial = partial // max(2, budget // 1200)
    return n + combine, total


def needs_confirmation(requests: int, chars: int) -> bool:
    return requests > chunking.MAX_CONFIRM_REQUESTS or chars > chunking.MAX_CONFIRM_CHARS


def summarize(job: Job, scope: Scope, options: SummaryOptions) -> Answer:
    """Riassunto con lunghezza regolabile, a blocchi per i documenti lunghi (AI-05, AI-14)."""
    profile = job.profile()
    instr = prompts.summary_instructions(options.length, options.words, options.form)
    system = _system(prompts.BASE, job.language) + " " + prompts.CITE + " " + instr
    budget, out_tokens = budget_for(profile, system)
    text = scope_text(scope)
    requests = 0

    def send(sys_text: str, body: str, stream: bool) -> ChatResult:
        nonlocal requests
        requests += 1
        req = ChatRequest(sys_text, [Message("user", f"<document>\n{body}\n</document>")], out_tokens)
        return job.gateway.send(req, function="summary", pages=scope.page_indices, fingerprint=scope.fingerprint,
                                on_text=job.on_text if stream else None, cancel=job.cancel, on_wait=job.on_wait,
                                profile=profile)

    if len(text) <= budget:
        result = send(system, text, True)
        final = result
    else:
        partial_system = _system(prompts.BASE, job.language) + " " + prompts.CITE + " " + prompts.PARTIAL_SUMMARY
        pbudget, _o = budget_for(profile, partial_system)
        chunks = make_chunks(scope.pages, pbudget) if scope.pages else [
            chunking.Chunk([], [], 0, 0, text[i:i + pbudget]) for i in range(0, len(text), pbudget)]
        partials: list[str] = []
        for n, ch in enumerate(chunks):
            job.step(n / (len(chunks) + 1), _("Blocco {i} di {n}").format(i=n + 1, n=len(chunks)))
            r = send(partial_system, ch.text, False)
            partials.append(f"[{_('parte')} {n + 1}, p. {ch.labels[0] if ch.labels else '?'}"
                            f"–{ch.labels[-1] if ch.labels else '?'}]\n{r.text}")
        combine_system = _system(prompts.BASE, job.language) + " " + prompts.COMBINE + " " + instr
        cbudget, _o = budget_for(profile, combine_system)
        level = 0
        while len("\n\n".join(partials)) > cbudget:
            level += 1
            groups: list[list[str]] = [[]]
            for p in partials:
                if sum(len(x) for x in groups[-1]) + len(p) > cbudget and groups[-1]:
                    groups.append([])
                groups[-1].append(p)
            if len(groups) >= len(partials):
                # riassunti parziali troppo lunghi: si accorciano per garantire la convergenza
                limit = max(200, cbudget // max(2 * len(partials), 1))
                partials = [p[:limit] for p in partials]
                continue
            new = []
            for g in groups:
                job.step(0.9, _("Combinazione dei riassunti parziali (livello {n})").format(n=level))
                r = send(partial_system.replace(prompts.PARTIAL_SUMMARY, prompts.COMBINE), "\n\n".join(g), False)
                new.append(r.text)
            partials = new
        job.step(0.95, _("Riassunto finale"))
        final = send(combine_system, "\n\n".join(partials), True)
    refs, flags = _ref_flags(final.text, scope)
    flags = [f for f in flags if scope.kind != SELECTION] + _partial_flags(scope)
    words = len(final.text.split())
    limit = {"short": 100, "medium": 250, "detailed": 600}.get(options.length, options.words)
    if words > limit * 1.3:
        flags.append(_("più lungo del richiesto: {n} parole").format(n=words))
    return Answer(final.text, _status(final), refs, flags, requests=requests)


# ------------------------------------------------------------------ traduzione
def translate_text(job: Job, scope: Scope, target: str, glossary=None) -> Answer:
    """Traduzione della selezione o delle pagine, raggruppata per pagina (AI-06)."""
    profile = job.profile()
    system = _system(prompts.BASE, target) + " " + prompts.translate_instructions(target, glossary)
    budget, out_tokens = budget_for(profile, system)
    if scope.selection and not scope.pages:
        req = ChatRequest(system, [Message("user", scope.selection)], out_tokens)
        r = job.gateway.send(req, function="translate", pages=scope.page_indices, fingerprint=scope.fingerprint,
                             on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
        return Answer(r.text, _status(r))
    sections = []
    status = "complete"
    for n, pt in enumerate(scope.pages):
        job.step(n / max(len(scope.pages), 1), _("Pagina {i} di {n}").format(i=n + 1, n=len(scope.pages)))
        if not pt.text.strip():
            continue
        header = "### " + _("Pagina {n}").format(n=pt.label) + "\n\n"
        if job.on_text:
            job.on_text(("\n\n" if sections else "") + header)
        pieces = chunking._split_long(pt.text, budget) if len(pt.text) > budget else [pt.text]
        out = []
        for piece in pieces:
            req = ChatRequest(system, [Message("user", piece)], out_tokens)
            r = job.gateway.send(req, function="translate", pages=[pt.index], fingerprint=scope.fingerprint,
                                 on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
            out.append(r.text)
            if r.interrupted:
                status = "interrupted"
                break
        sections.append(header + "\n\n".join(out))
        if status == "interrupted":
            break
    return Answer("\n\n".join(sections), status, flags=_partial_flags(scope))


def parse_json_payload(text: str):
    """Estrae il primo JSON valido dalla risposta (anche tra ``` o con testo intorno)."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    pairs = sorted(((t.find(o), o, c) for o, c in (("[", "]"), ("{", "}")) if t.find(o) != -1))
    for start, _opener, closer in pairs:
        end = t.rfind(closer)
        if end > start:
            try:
                return json.loads(t[start:end + 1])
            except ValueError:
                continue
    raise ValueError(_("La risposta non è nel formato atteso."))


def block_translator(job: Job, target: str, glossary=None, fingerprint: str = "") -> Callable[[int, list[str]], list[str]]:
    """Callback per `translate_pdf.translate_document`: una richiesta per pagina (AI-07)."""
    profile = job.profile()
    system = _system(prompts.BASE, target) + " " + prompts.translate_instructions(target, glossary) + \
        prompts.BLOCKS_FORMAT
    budget, out_tokens = budget_for(profile, system)

    def translate(page: int, blocks: list[str]) -> list[str]:
        out: list[str] = []
        group: list[str] = []

        def flush():
            if not group:
                return
            req = ChatRequest(system, [Message("user", json.dumps(group, ensure_ascii=False))],
                              max(out_tokens, min(16000, len(json.dumps(group)) // 2)))
            r = job.gateway.send(req, function="translate-pdf", pages=[page], fingerprint=fingerprint,
                                 cancel=job.cancel, on_wait=job.on_wait, profile=profile)
            if r.interrupted:
                raise OperationCancelled()
            data = parse_json_payload(r.text)
            if not isinstance(data, list) or len(data) != len(group):
                raise ValueError(_("Il numero di blocchi tradotti non corrisponde."))
            out.extend(str(x) for x in data)
            group.clear()

        for b in blocks:
            if sum(len(x) for x in group) + len(b) > budget:
                flush()
            group.append(b)
        flush()
        return out

    return translate


# ------------------------------------------------------------------ spiegazione
def explain(job: Job, scope: Scope, level: str = "standard", text: str | None = None) -> Answer:
    """Spiegazione di termini e passaggi (AI-08): selezione + paragrafo circostante."""
    profile = job.profile()
    system = _system(prompts.BASE, job.language) + " " + prompts.explain_instructions(level)
    sel = text if text is not None else scope.selection
    body = f"Selected text:\n«{sel}»"
    if text is None and scope.paragraph and scope.paragraph.strip() != sel.strip():
        body += f"\n\nSurrounding paragraph:\n{scope.paragraph}"
    body += f"\n\nLevel: {level}"
    _budget, out_tokens = budget_for(profile, system)
    req = ChatRequest(system, [Message("user", body)], out_tokens)
    r = job.gateway.send(req, function="explain", pages=scope.page_indices, fingerprint=scope.fingerprint,
                         on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    return Answer(r.text, _status(r))


# ------------------------------------------------------------------ riscrittura
def clean_proposal(text: str) -> str:
    t = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if len(t) >= 2 and t[0] in "«\"“" and t[-1] in "»\"”":
        t = t[1:-1].strip()
    return t


def rewrite(job: Job, scope: Scope, action: str, tone: str = "formale", instruction: str = "") -> Answer:
    """Proposta di riscrittura/correzione (AI-09); il documento non viene toccato qui."""
    profile = job.profile()
    system = _system(prompts.BASE, job.language) + " " + prompts.rewrite_instructions(action, tone, instruction)
    _budget, out_tokens = budget_for(profile, system)
    req = ChatRequest(system, [Message("user", scope.selection)], out_tokens)
    r = job.gateway.send(req, function="rewrite", pages=scope.page_indices, fingerprint=scope.fingerprint,
                         on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    proposal = clean_proposal(r.text)
    flags = [_("nessuna modifica proposta")] if proposal == scope.selection.strip() else []
    return Answer(proposal, _status(r), flags=flags)


def word_diff(old: str, new: str) -> list[tuple[str, str]]:
    """Differenze parola per parola: lista di (op, testo) con op in = - +."""
    import difflib
    a, b = old.split(), new.split()
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(("=", " ".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(("-", " ".join(a[i1:i2])))
            if j2 > j1:
                out.append(("+", " ".join(b[j1:j2])))
    return out


# ------------------------------------------------------------------ immagini
def ask_image(job: Job, scope: Scope, action: str | None, question: str = "") -> Answer:
    """Domande su immagini o aree (AI-11)."""
    profile = job.profile()
    system = _system(prompts.BASE, job.language)
    task = prompts.IMAGE_ACTIONS.get(action or "", "").format(language=prompts.language_name(job.language))
    body = (task + "\n" + question).strip() or prompts.IMAGE_ACTIONS["describe"]
    if scope.selection:
        body += "\n\nText recognised in the area:\n" + scope.selection
    _budget, out_tokens = budget_for(profile, system)
    req = ChatRequest(system, [Message("user", body, list(scope.images))], out_tokens)
    r = job.gateway.send(req, function="image", pages=scope.page_indices, fingerprint=scope.fingerprint,
                         on_text=job.on_text, cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    return Answer(r.text, _status(r))


# ------------------------------------------------------------------ mappe e scalette
def outline(job: Job, scope: Scope, kind: str = "map", depth: int = 3, max_nodes: int = 40) -> Answer:
    """Mappa concettuale o scaletta (AI-12): JSON validato, un nuovo tentativo se non conforme."""
    from . import mindmap
    profile = job.profile()
    system = _system(prompts.BASE, job.language) + " " + prompts.outline_instructions(kind, depth, max_nodes)
    budget, out_tokens = budget_for(profile, system)
    text = scope_text(scope)
    requests = 0
    if len(text) > budget:
        # documenti lunghi: scalette parziali poi unione
        chunks = make_chunks(scope.pages, budget)
        partials = []
        for n, ch in enumerate(chunks):
            job.step(n / (len(chunks) + 1), _("Blocco {i} di {n}").format(i=n + 1, n=len(chunks)))
            req = ChatRequest(system, [Message("user", f"<document>\n{ch.text}\n</document>")], out_tokens)
            requests += 1
            r = job.gateway.send(req, function="outline", pages=ch.pages, fingerprint=scope.fingerprint,
                                 cancel=job.cancel, on_wait=job.on_wait, profile=profile)
            partials.append(r.text)
        text = "\n\n".join(partials)[:budget]
        system = system + " The content is a list of partial outlines (JSON) of consecutive parts: merge them."
    space_count = scope.space.page_count if scope.space else 0
    last_error = None
    for attempt in range(2):
        req = ChatRequest(system, [Message("user", f"<document>\n{text}\n</document>")], out_tokens)
        requests += 1
        r = job.gateway.send(req, function="outline", pages=scope.page_indices, fingerprint=scope.fingerprint,
                             cancel=job.cancel, on_wait=job.on_wait, profile=profile)
        try:
            root, warnings = mindmap.parse_tree(r.text, space_count, max_depth=depth, max_nodes=max_nodes)
        except mindmap.MindMapError as e:
            last_error = e
            continue
        return Answer(mindmap.to_markdown_outline(root), "complete", flags=warnings + _partial_flags(scope),
                      data=root, requests=requests)
    raise FoliantError(_("La risposta del modello non ha la struttura attesa: {e}").format(e=last_error))


# ------------------------------------------------------------------ OCR prima dell'invio
def run_ocr(pdf: pymupdf.Document, pages: list[int], language: str, step=None,
            words_out: dict | None = None) -> dict[int, tuple[str, float | None]]:
    out = {}
    for n, i in enumerate(pages):
        if step:
            step(n / max(len(pages), 1), _("OCR pagina {i}/{n}").format(i=n + 1, n=len(pages)))
        out[i] = content.ocr_page(pdf, i, language, words_out=words_out)
    return out


def answer_to_plain(text: str) -> str:
    return plain_text(text)
