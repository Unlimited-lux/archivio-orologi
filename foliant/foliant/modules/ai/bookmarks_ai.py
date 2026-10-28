"""Segnalibri generati e riassunto per sezione (AI-22).

Nessun widget: le funzioni girano anche in un thread di lavoro. Al modello si
inviano solo i titoli candidati (testo, pagina, corpo, grassetto, posizione),
mai il testo completo del documento.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pymupdf

from foliant.api import ENGINE_LOCK, Document, _

from . import engine, prompts
from .providers import ChatRequest, Message

MAX_CANDIDATES = 400
MAX_TITLE_CHARS = 160

BOOKMARKS_PROMPT = (
    "You receive a JSON list of heading candidates extracted from a PDF: each has the text, the page number "
    "(1-based), the vertical position `y` in points from the top of the page, the font size and whether it "
    "is bold; `body_size` is the size of the normal text. Build the table of contents of the document by "
    "choosing the real headings (chapters, sections, subsections) and arranging them into a hierarchy of at "
    "most {depth} levels, in reading order. Discard running headers, footers, page numbers and captions. "
    "Use the heading text exactly as written (you may fix spacing). Reply ONLY with JSON of the form "
    '[{{"title": "...", "page": N, "children": [ ... same structure ... ]}}] where `page` is the 1-based page '
    "number of the candidate. If the document has no recognisable structure reply with []."
)


@dataclass
class BNode:
    """Voce proposta: pagina 0-based, `y` = posizione verticale del titolo (None = inizio pagina)."""

    title: str
    page: int
    y: float | None = None
    children: list["BNode"] = field(default_factory=list)

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)


# ------------------------------------------------------------------ candidati
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _page_lines(pdf: pymupdf.Document, index: int) -> list[dict]:
    """Righe della pagina con corpo massimo, grassetto e posizione (tenendo ENGINE_LOCK)."""
    out = []
    with ENGINE_LOCK:
        page = pdf[index]
        height = page.rect.height
        data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = _norm("".join(s["text"] for s in line["spans"]))
            chars = sum(len(s["text"].strip()) for s in spans)
            size = max(s["size"] for s in spans)
            bold_chars = sum(len(s["text"].strip()) for s in spans
                             if s["flags"] & 16 or "bold" in s.get("font", "").lower())
            bbox = line["bbox"]
            out.append({"text": text, "size": round(size, 1), "bold": bold_chars >= chars * 0.6,
                        "chars": chars, "x": round(bbox[0], 1), "y": round(bbox[1], 1), "height": height})
    return out


def heading_candidates(pdf: pymupdf.Document, pages: list[int] | None = None) -> list[dict]:
    """Righe che sembrano titoli: corpo maggiore del testo normale o grassetto brevi.

    Ogni candidato: {text, page (0-based), y, x, size, bold, body_size}.
    """
    with ENGINE_LOCK:
        count = pdf.page_count
    pages = [p for p in (range(count) if pages is None else pages) if 0 <= p < count]
    lines_by_page = {p: _page_lines(pdf, p) for p in pages}
    weights: dict[float, int] = {}
    for lines in lines_by_page.values():
        for ln in lines:
            weights[ln["size"]] = weights.get(ln["size"], 0) + ln["chars"]
    if not weights:
        return []
    body = max(weights, key=weights.get)
    # testo ripetuto su molte pagine (intestazioni e piè di pagina) non è un titolo
    repeats: dict[str, int] = {}
    for lines in lines_by_page.values():
        for t in {ln["text"].lower() for ln in lines}:
            repeats[t] = repeats.get(t, 0) + 1
    limit_repeat = max(3, len(pages) // 2)
    out: list[dict] = []
    for p in pages:
        for ln in lines_by_page[p]:
            text = ln["text"]
            if not text or len(text) > MAX_TITLE_CHARS or not re.search(r"[^\W\d_]", text):
                continue
            if repeats.get(text.lower(), 0) >= limit_repeat and len(pages) > 2:
                continue
            bigger = ln["size"] >= body * 1.15
            bold_short = ln["bold"] and ln["size"] >= body * 0.95 and len(text) <= 100 and not text.endswith((".", ","))
            if bigger or bold_short:
                out.append({"text": text, "page": p, "y": ln["y"], "x": ln["x"], "size": ln["size"],
                            "bold": ln["bold"], "body_size": body})
                if len(out) >= MAX_CANDIDATES:
                    return out
    return out


# ------------------------------------------------------------------ proposta
def _title_y(pdf: pymupdf.Document, page: int, title: str, candidates: list[dict]) -> float | None:
    """Posizione verticale del titolo sulla pagina, None se non trovato."""
    key = _norm(title).lower()
    for c in candidates:
        if c["page"] == page and _norm(c["text"]).lower() == key:
            return float(c["y"])
    if not key:
        return None
    with ENGINE_LOCK:
        hits = pdf[page].search_for(title.strip()[:80])
    if hits:
        return float(min(h.y0 for h in hits))
    for c in candidates:
        if c["page"] == page and (key in c["text"].lower() or c["text"].lower() in key) and len(c["text"]) > 3:
            return float(c["y"])
    return None


def _items(data) -> list:
    if isinstance(data, dict):
        for k in ("bookmarks", "toc", "items", "children"):
            if isinstance(data.get(k), list):
                return data[k]
        if "title" in data:
            return [data]
        return []
    return data if isinstance(data, list) else []


def _to_nodes(pdf, items: list, page_count: int, depth: int, max_depth: int, candidates: list[dict],
              warnings: list[str]) -> list[BNode]:
    out: list[BNode] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _norm(str(it.get("title") or ""))
        children = _items(it.get("children") or [])
        try:
            page1 = int(it.get("page"))
        except (TypeError, ValueError):
            page1 = None
        kids = _to_nodes(pdf, children, page_count, depth + 1, max_depth, candidates, warnings) \
            if depth < max_depth else []
        if not title or page1 is None or not 1 <= page1 <= page_count:
            warnings.append(_("Voce «{t}» scartata: la pagina {p} non esiste.").format(
                t=title or "?", p=it.get("page")))
            out.extend(kids)    # i figli validi salgono di un livello
            continue
        node = BNode(title, page1 - 1, _title_y(pdf, page1 - 1, title, candidates), kids)
        out.append(node)
    return out


def _no_structure_warning(page_count: int) -> str:
    n = suggested_step(page_count)
    return _("Il documento non ha una struttura riconoscibile: puoi creare un segnalibro ogni {n} pagine.").format(n=n)


def suggested_step(page_count: int) -> int:
    return 1 if page_count <= 10 else 5 if page_count <= 60 else 10


def propose_bookmarks(job: engine.Job, pdf: pymupdf.Document, max_depth: int = 3,
                      pages: list[int] | None = None) -> tuple[list[BNode], list[str]]:
    """Albero di segnalibri proposto dal modello e avvisi (voci scartate, nessuna struttura)."""
    with ENGINE_LOCK:
        page_count = pdf.page_count
    candidates = heading_candidates(pdf, pages)
    if not candidates:
        return [], [_no_structure_warning(page_count)]
    profile = job.profile()
    system = engine._system(prompts.BASE, job.language) + " " + BOOKMARKS_PROMPT.format(depth=max_depth)
    budget, out_tokens = engine.budget_for(profile, system)
    payload = [{"text": c["text"], "page": c["page"] + 1, "y": c["y"], "size": c["size"], "bold": c["bold"]}
               for c in candidates]
    body = json.dumps({"body_size": candidates[0]["body_size"], "page_count": page_count,
                       "candidates": payload}, ensure_ascii=False)
    while len(body) > budget and len(payload) > 10:
        payload = payload[: int(len(payload) * 0.8)]
        body = json.dumps({"body_size": candidates[0]["body_size"], "page_count": page_count,
                           "candidates": payload}, ensure_ascii=False)
    req = ChatRequest(system, [Message("user", body)], out_tokens)
    used_pages = sorted({c["page"] for c in candidates})
    r = job.gateway.send(req, function="bookmarks", pages=used_pages, cancel=job.cancel, on_wait=job.on_wait,
                         profile=profile)
    data = engine.parse_json_payload(r.text)
    warnings: list[str] = []
    nodes = _to_nodes(pdf, _items(data), page_count, 1, max(1, max_depth), candidates, warnings)
    if not nodes:
        warnings.append(_no_structure_warning(page_count))
    return nodes, warnings


def fallback_every_n(page_count: int, n: int) -> list[BNode]:
    """Segnalibri semplici ogni N pagine."""
    n = max(1, int(n))
    out = []
    for start in range(0, page_count, n):
        end = min(page_count, start + n)
        title = _("Pagina {a}").format(a=start + 1) if end - start == 1 else \
            _("Pagine {a}–{b}").format(a=start + 1, b=end)
        out.append(BNode(title, start, None))
    return out


# ------------------------------------------------------------------ applicazione
def _dest(page: int, y: float | None) -> dict:
    return {"kind": pymupdf.LINK_GOTO, "page": page, "to": pymupdf.Point(0, max(0.0, y or 0.0)), "zoom": 0}


def nodes_to_toc(nodes: list[BNode], level: int = 1) -> list[list]:
    out: list[list] = []
    for n in nodes:
        out.append([level, n.title, n.page + 1, _dest(n.page, n.y)])
        out.extend(nodes_to_toc(n.children, level + 1))
    return out


def _groups(toc: list[list]) -> list[list[list]]:
    groups: list[list[list]] = []
    for e in toc:
        if e[0] == 1 or not groups:
            groups.append([])
        groups[-1].append(e)
    return groups


def _clean_entry(e: list) -> list:
    """Voce esistente pronta per set_toc (senza riferimenti interni)."""
    out = [e[0], e[1], e[2]]
    if len(e) > 3 and isinstance(e[3], dict):
        d = {k: v for k, v in e[3].items() if k not in ("xref",)}
        out.append(d)
    return out


def _sort_key(group: list[list]) -> tuple:
    head = group[0]
    y = 0.0
    if len(head) > 3 and isinstance(head[3], dict):
        to = head[3].get("to")
        if to is not None:
            try:
                y = float(to.y)
            except AttributeError:
                y = float(to[1])
    return (head[2] if head[2] > 0 else 10 ** 9, y)


def apply_bookmarks(document: Document, nodes: list[BNode], mode: str = "replace") -> None:
    """Scrive i segnalibri con destinazione (pagina e posizione): un solo passo annullabile."""
    new = nodes_to_toc(nodes)
    label = _("Sostituisci segnalibri (IA)") if mode == "replace" else _("Unisci segnalibri (IA)")
    with document.change(label) as pdf:
        if mode == "merge":
            old = [_clean_entry(e) for e in pdf.get_toc(simple=False)]
            existing = {(_norm(e[1]).lower(), e[2]) for e in old}
            groups = _groups(old)
            for g in _groups(new):
                if (_norm(g[0][1]).lower(), g[0][2]) not in existing:
                    groups.append(g)
            groups.sort(key=_sort_key)
            toc = [e for g in groups for e in g]
        else:
            toc = new
        pdf.set_toc(toc)


# ------------------------------------------------------------------ sezioni
def section_pages(toc: list, index: int, page_count: int | None = None) -> list[int]:
    """Pagine (0-based) dal segnalibro `index` al successivo dello stesso livello o superiore.

    `toc` nel formato di `get_toc(simple=False)`. L'ultima sezione arriva a fine documento
    (`page_count`; se assente, all'ultima pagina citata dai segnalibri).
    """
    if not 0 <= index < len(toc):
        return []
    level, start = toc[index][0], toc[index][2] - 1
    if page_count is None:
        page_count = max((e[2] for e in toc), default=start + 1)
    if start < 0:
        return []
    end = page_count - 1
    for e in toc[index + 1:]:
        if e[0] <= level and e[2] > 0:
            end = e[2] - 2
            break
    end = min(max(end, start), page_count - 1)
    return list(range(start, end + 1))


def summarize_section(job: engine.Job, pdf: pymupdf.Document, toc_index: int,
                      options: engine.SummaryOptions | None = None, *, toc: list | None = None,
                      **scope_kw) -> engine.Answer:
    """Riassunto della sezione del segnalibro (AI-22 → AI-05)."""
    with ENGINE_LOCK:
        if toc is None:
            toc = pdf.get_toc(simple=False)
        count = pdf.page_count
    pages = section_pages(toc, toc_index, count)
    scope = engine.build_scope(pdf, engine.PAGES, pages=pages, **scope_kw)
    return engine.summarize(job, scope, options or engine.SummaryOptions())
