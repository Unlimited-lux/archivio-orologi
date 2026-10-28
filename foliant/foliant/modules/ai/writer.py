"""Scrittura assistita di nuovi testi (AI-19): generazione, casella di testo, nuovo PDF.

Nessun widget. `write_text` può girare in un thread di lavoro; le funzioni che
modificano il documento usano un solo `document.change()` (un «Annulla»).
"""

from __future__ import annotations

import html as _html
import io
import re

import pymupdf

from foliant.api import ENGINE_LOCK, Document, _

from . import compose, engine, prompts
from .content import usable_text
from .providers import ChatRequest, Message
from .render import plain_text

KINDS = {
    "lettera": "letter", "e-mail": "e-mail", "paragrafo": "paragraph", "articolo": "article",
    "introduzione": "introduction", "conclusioni": "conclusions",
}

TONES = dict(prompts.TONES, neutro="neutral")

A4 = pymupdf.paper_rect("a4")
MARGIN = 56
BOX_FONTSIZE = 11


# ------------------------------------------------------------------ generazione
def write_text(job: engine.Job, kind: str, instructions: str, tone: str = "formale", length_words: int = 200,
               language: str = "it", source_scope: engine.Scope | None = None) -> engine.Answer:
    """Testo nuovo richiesto dall'utente; con `source_scope` il documento è la fonte."""
    if not (instructions or "").strip():
        raise ValueError(_("Scrivi le istruzioni per il testo da generare."))
    profile = job.profile()
    system = engine._system(prompts.BASE, language) + " " + prompts.WRITE.format(
        kind=KINDS.get(kind, kind), tone=TONES.get(tone, tone), length=int(length_words))
    flags: list[str] = []
    doc_text = engine.scope_text(source_scope) if source_scope is not None else ""
    if source_scope is not None:
        system += " Base the text on the provided document content; do not invent facts that are not in it."
        if not usable_text(doc_text):
            flags.append(_("il documento non contiene testo utilizzabile: il testo non si basa sul documento"))
            doc_text = ""
    budget, out_tokens = engine.budget_for(profile, system, instructions)
    out_tokens = max(out_tokens, min(16000, int(length_words) * 3))
    if len(doc_text) > budget:
        doc_text = doc_text[:budget]
        flags.append(_("parziale: il documento è stato troncato"))
    user = instructions.strip() if not doc_text else (
        f"<document>\n{doc_text}\n</document>\n\nRequest: {instructions.strip()}")
    request = ChatRequest(system, [Message("user", user)], out_tokens)
    pages = source_scope.page_indices if source_scope is not None and doc_text else None
    fp = source_scope.fingerprint if source_scope is not None else ""
    r = job.gateway.send(request, function="write", pages=pages, fingerprint=fp, on_text=job.on_text,
                         cancel=job.cancel, on_wait=job.on_wait, profile=profile)
    text = engine.clean_proposal(r.text)
    if source_scope is not None:
        flags += engine._partial_flags(source_scope)
    return engine.Answer(text, engine._status(r), flags=flags)


# ------------------------------------------------------------------ Markdown leggero
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_BULLET = re.compile(r"^\s*[-*•+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _inline(text: str) -> str:
    s = _html.escape(text)
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", s)
    return s


def markdown_to_html(text: str) -> str:
    """Titoli, elenchi, grassetto e corsivo; le altre righe diventano paragrafi."""
    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None

    def flush_para():
        if para:
            out.append("<p>" + "<br/>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in re.sub(r"```[a-zA-Z]*\n?", "", text or "").splitlines():
        line = raw.rstrip()
        h = _HEADING.match(line)
        b = _BULLET.match(line)
        n = _NUMBERED.match(line)
        if not line.strip():
            flush_para()
            close_list()
        elif h:
            flush_para()
            close_list()
            level = min(len(h.group(1)), 3)
            out.append(f"<h{level}>{_inline(h.group(2))}</h{level}>")
        elif b or n:
            flush_para()
            tag = "ul" if b else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline((b or n).group(1))}</li>")
        else:
            close_list()
            para.append(line.strip())
    flush_para()
    close_list()
    return "".join(out)


CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { font-size: 18pt; font-weight: bold; margin: 0 0 8pt 0; }
h2 { font-size: 15pt; font-weight: bold; margin: 10pt 0 6pt 0; }
h3 { font-size: 13pt; font-weight: bold; margin: 8pt 0 4pt 0; }
p { margin: 0 0 7pt 0; }
ul, ol { margin: 0 0 7pt 0; }
"""


def text_to_new_pdf(text: str) -> bytes:
    """PDF A4 con il testo (caratteri incorporati, testo selezionabile), su più pagine se serve."""
    body = markdown_to_html(text) or "<p></p>"
    buf = io.BytesIO()
    with ENGINE_LOCK:
        story = pymupdf.Story(html=f"<body>{body}</body>", user_css=CSS)
        writer = pymupdf.DocumentWriter(buf)
        where = A4 + (MARGIN, MARGIN, -MARGIN, -MARGIN)
        more = 1
        pages = 0
        while more and pages < 2000:
            dev = writer.begin_page(A4)
            more, _filled = story.place(where)
            story.draw(dev)
            writer.end_page()
            pages += 1
        writer.close()
    return buf.getvalue()


# ------------------------------------------------------------------ casella di testo
def box_text(text: str) -> str:
    """Formattazione leggera resa nel testo semplice della casella (elenchi con il punto)."""
    lines = []
    for line in plain_text(text).splitlines():
        b = _BULLET.match(line)
        lines.append("• " + b.group(1) if b else line)
    return "\n".join(lines).strip()


def box_rect(pdf: pymupdf.Document, page: int, point, text: str,
             fontsize: float = BOX_FONTSIZE) -> tuple[pymupdf.Rect, bool]:
    """Riquadro della casella a partire dal punto e se il testo entra nello spazio sotto di esso."""
    with ENGINE_LOCK:
        pr = pdf[page].rect
    x0 = min(max(float(point[0]), 0), pr.width - 60)
    y0 = min(max(float(point[1]), 0), pr.height - 30)
    x1 = max(x0 + 60, min(pr.width - 24, x0 + 320))
    width = x1 - x0
    lines = sum(max(1, int(len(line) * fontsize * 0.5 / max(width - 8, 20)) + 1) for line in text.split("\n"))
    need = lines * fontsize * 1.3 + 10
    return pymupdf.Rect(x0, y0, x1, y0 + max(need, 24)), y0 + need <= pr.height - 5


def insert_text_box_at(document: Document, page: int, point, text: str, *,
                       fontsize: float = BOX_FONTSIZE) -> pymupdf.Rect:
    """Casella di testo con lo stile predefinito nel punto indicato: un solo «Annulla» la rimuove."""
    body = box_text(text)
    rect, _fits = box_rect(document.pdf, page, point, body, fontsize)
    return compose.add_text_box(document, page, tuple(rect), body, fontsize=fontsize,
                                label=_("Inserisci testo generato"))
