"""Riferimenti di pagina e resa sicura delle risposte (AI-03, AI-04).

La risposta del modello è testo non fidato: viene sempre convertita da un
sottoinsieme di Markdown a HTML partendo dal testo già «escapato». Nessun tag
del modello arriva al visualizzatore, le immagini diventano testo inattivo e i
collegamenti esterni puntano a `ext:N` (aperti solo dopo conferma).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from foliant.api import _

REF_RE = re.compile(
    r"\[(?:(?P<doc>[^\[\]›>\n]{1,80}?)\s*(?:›|>)\s*)?(?P<pp>pp?|pag|pagina|page)\.?\s*"
    r"(?P<label>[0-9A-Za-z]{1,12})(?:\s*[-–]\s*[0-9A-Za-z]{1,12})?"
    r"(?:\s*[:,]\s*[«\"“](?P<quote>[^»\"”\]\n]{1,300})[»\"”])?\s*\]",
    re.IGNORECASE)


@dataclass
class PageRef:
    raw: str
    label: str
    doc: str = ""
    quote: str = ""
    page: int | None = None          # indice fisico se verificato
    doc_key: str = ""                # chiave del documento (AI-13)
    verified: bool = False
    outdated: bool = False

    def to_dict(self) -> dict:
        return dict(raw=self.raw, label=self.label, doc=self.doc, quote=self.quote, page=self.page,
                    doc_key=self.doc_key, verified=self.verified, outdated=self.outdated)

    @classmethod
    def from_dict(cls, d: dict) -> "PageRef":
        return cls(**{k: d.get(k) for k in ("raw", "label", "doc", "quote", "page", "doc_key", "verified",
                                              "outdated") if k in d})


@dataclass
class PageSpace:
    """Pagine valide per i riferimenti: etichetta -> indice, e pagine incluse nel contesto."""

    labels: dict[str, int] = field(default_factory=dict)
    included: set[int] = field(default_factory=set)
    page_count: int = 0
    doc_key: str = ""
    name: str = ""

    def resolve(self, label: str) -> int | None:
        idx = self.labels.get(label)
        if idx is None:
            idx = self.labels.get(label.lower())
        if idx is None and label.isdigit():
            n = int(label) - 1
            if 0 <= n < self.page_count and str(n + 1) not in self.labels.values():
                idx = n
        if idx is None or not (0 <= idx < self.page_count):
            return None
        if self.included and idx not in self.included:
            return None
        return idx


def find_refs(text: str, spaces: list[PageSpace] | PageSpace | None) -> list[PageRef]:
    if isinstance(spaces, PageSpace):
        spaces = [spaces]
    spaces = spaces or []
    refs = []
    for m in REF_RE.finditer(text):
        ref = PageRef(m.group(0), m.group("label"), (m.group("doc") or "").strip(), (m.group("quote") or "").strip())
        space = None
        if ref.doc:
            for s in spaces:
                if s.name and (s.name == ref.doc or s.name.lower().startswith(ref.doc.lower())
                               or ref.doc.lower() in s.name.lower()):
                    space = s
                    break
        elif spaces:
            space = spaces[0]
        if space is not None:
            idx = space.resolve(ref.label)
            if idx is not None:
                ref.page, ref.verified, ref.doc_key = idx, True, space.doc_key
        refs.append(ref)
    return refs


# ------------------------------------------------------------------ Markdown
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)[^)]*\)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)[^)]*\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])")


@dataclass
class Rendered:
    html: str
    refs: list[PageRef]
    links: list[str]
    images: list[str]


class _Renderer:
    def __init__(self, spaces, known=None, prefix=""):
        self.spaces = spaces
        self.known = known
        self.prefix = prefix
        self.refs: list[PageRef] = []
        self.links: list[str] = []
        self.images: list[str] = []
        self.colors = _colors()

    def inline(self, raw: str) -> str:
        """Testo inline: tutto viene escapato; le strutture sono ricostruite con segnaposto."""
        slots: list[str] = []

        def slot(h: str) -> str:
            slots.append(h)
            return f"\x00{len(slots) - 1}\x00"

        def code(m):
            return slot(f"<code>{html.escape(m.group(1))}</code>")

        def image(m):
            alt, url = m.group(1), m.group(2)
            self.images.append(url)
            text = _("[immagine non caricata: {alt}]").format(alt=alt or url)
            return slot(f"<span style='color:{self.colors['muted']};text-decoration:underline' "
                        f"title='{html.escape(url, quote=True)}'>{html.escape(text)}</span>")

        def ref(m):
            if self.known is not None and len(self.refs) < len(self.known):
                r = PageRef.from_dict(self.known[len(self.refs)])
            else:
                r = find_refs(m.group(0), self.spaces)[0]
            self.refs.append(r)
            shown = (f"{r.doc} › " if r.doc else "") + f"p. {r.label}"
            if r.verified and not r.outdated:
                return slot(f"<a href='page:{self.prefix}{len(self.refs) - 1}' style='color:{self.colors['accent']}'>"
                            f"{html.escape(shown)}</a>")
            return slot(f"<span style='color:{self.colors['muted']}' title='{html.escape(_('non verificato'))}'>"
                        f"{html.escape(shown)} <i>({html.escape(_('non verificato'))})</i></span>")

        def link(m):
            text, url = m.group(1), m.group(2)
            self.links.append(url)
            return slot(f"<a href='ext:{self.prefix}{len(self.links) - 1}'>{html.escape(text)}</a>")

        s = _INLINE_CODE.sub(code, raw)
        s = _IMAGE.sub(image, s)
        s = REF_RE.sub(ref, s)
        s = _LINK.sub(link, s)
        s = html.escape(s, quote=False)
        s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
        s = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
        return re.sub("\x00(\\d+)\x00", lambda m: slots[int(m.group(1))], s)

    def render(self, text: str) -> str:
        lines = text.replace("\r\n", "\n").split("\n")
        out: list[str] = []
        i = 0
        list_stack: list[str] = []

        def close_lists():
            while list_stack:
                out.append(f"</{list_stack.pop()}>")

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith("```"):
                close_lists()
                buf = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    buf.append(lines[i])
                    i += 1
                i += 1
                out.append(f"<pre style='background:{self.colors['alt']}'>{html.escape(chr(10).join(buf))}</pre>")
                continue
            if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
                close_lists()
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append(lines[i])
                    i += 1
                cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
                head, body = cells[0], [r for r in cells[2:]]
                h = ["<table border='1' cellspacing='0' cellpadding='4'>",
                     "<tr>" + "".join(f"<th>{self.inline(c)}</th>" for c in head) + "</tr>"]
                for r in body:
                    h.append("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in r) + "</tr>")
                h.append("</table>")
                out.append("".join(h))
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if m:
                close_lists()
                level = min(len(m.group(1)) + 2, 6)
                out.append(f"<h{level}>{self.inline(m.group(2))}</h{level}>")
                i += 1
                continue
            m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
            if m:
                kind = "ol" if m.group(2)[0].isdigit() else "ul"
                depth = len(m.group(1).replace("\t", "  ")) // 2 + 1
                while len(list_stack) < depth:
                    list_stack.append(kind)
                    out.append(f"<{kind}>")
                while len(list_stack) > depth:
                    out.append(f"</{list_stack.pop()}>")
                out.append(f"<li>{self.inline(m.group(3))}</li>")
                i += 1
                continue
            if not stripped:
                close_lists()
                i += 1
                continue
            close_lists()
            para = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*([-*+]|\d+[.)])\s|```|\|)",
                                                                        lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>" + "<br>".join(self.inline(p) for p in para) + "</p>")
        close_lists()
        return "".join(out)


def _colors() -> dict:
    try:
        from foliant.api import theme
        return {"accent": theme.hex_color("accent"), "muted": theme.hex_color("text_muted"),
                "alt": theme.hex_color("surface_alt"), "danger": theme.hex_color("danger"),
                "warning": theme.hex_color("warning")}
    except Exception:  # noqa: BLE001
        return {"accent": "#0E7C7B", "muted": "#5E6776", "alt": "#EDEFF2", "danger": "#C2410C", "warning": "#B45309"}


def render_markdown(text: str, spaces: list[PageSpace] | PageSpace | None = None, *,
                    known_refs: list[dict] | None = None, prefix: str = "") -> Rendered:
    if isinstance(spaces, PageSpace):
        spaces = [spaces]
    r = _Renderer(spaces or [], known_refs, prefix)
    body = r.render(text or "")
    return Rendered(body, r.refs, r.links, r.images)


def plain_text(text: str) -> str:
    """Testo semplice dalla risposta (per note e copia)."""
    s = re.sub(r"```[a-zA-Z]*\n?", "", text or "")
    s = _IMAGE.sub(lambda m: m.group(1), s)
    s = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", s)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.M)
    s = _BOLD.sub(lambda m: m.group(1) or m.group(2), s)
    s = _INLINE_CODE.sub(lambda m: m.group(1), s)
    return s.strip()
