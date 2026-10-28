"""Rapporti in PDF (revisione del contratto, classifica): tabelle impaginate su più pagine.

Il PDF è creato in locale con il motore PDF, senza risorse esterne; il testo delle
risposte è sempre trattato come testo (mai come HTML).
"""

from __future__ import annotations

import datetime as _dt
import html
import io
from dataclasses import dataclass, field

import pymupdf

from foliant.api import ENGINE_LOCK, _

CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #222; }
h1 { font-size: 15pt; margin: 0 0 4pt 0; }
h2 { font-size: 11.5pt; margin: 12pt 0 4pt 0; }
p.meta { color: #666; font-size: 8pt; margin: 0 0 8pt 0; }
p.note { color: #555; font-size: 8pt; margin: 10pt 0 0 0; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #e8e8e8; text-align: left; }
th, td { border: 0.5pt solid #999; padding: 2pt 3pt; vertical-align: top; }
td.hi { background-color: #f6d4d4; }
td.mid { background-color: #f8ead0; }
"""


@dataclass
class Table:
    heading: str
    headers: list[str]
    rows: list[list[str]]
    classes: list[list[str]] = field(default_factory=list)   # classe CSS per cella (facoltativa)
    text: str = ""                                            # paragrafo dopo il titolo della sezione


def _esc(value) -> str:
    return html.escape("" if value is None else str(value)).replace("\n", "<br/>")


def build_html(title: str, tables: list[Table], notes: list[str] = ()) -> str:
    date = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    parts = [f"<h1>{_esc(title)}</h1>",
             f"<p class='meta'>{_esc(_('Creato con l’Assistente IA di Foliant il {d}').format(d=date))}</p>"]
    for t in tables:
        parts.append(f"<h2>{_esc(t.heading)}</h2>")
        if t.text:
            parts.append(f"<p>{_esc(t.text)}</p>")
        if not t.rows:
            parts.append(f"<p>{_esc(_('Nessun elemento.'))}</p>")
            continue
        parts.append("<table><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in t.headers) + "</tr>")
        for i, row in enumerate(t.rows):
            cls = t.classes[i] if i < len(t.classes) else []
            cells = []
            for j, value in enumerate(row):
                c = cls[j] if j < len(cls) and cls[j] else ""
                cells.append(f"<td class='{c}'>{_esc(value)}</td>" if c else f"<td>{_esc(value)}</td>")
            parts.append("<tr>" + "".join(cells) + "</tr>")
        parts.append("</table>")
    for n in notes:
        parts.append(f"<p class='note'>{_esc(n)}</p>")
    return "<body>" + "".join(parts) + "</body>"


def render(title: str, tables: list[Table], notes: list[str] = (), *, landscape: bool = False) -> bytes:
    """PDF A4 (orizzontale per le tabelle larghe) con titolo, sezioni e note finali."""
    story = pymupdf.Story(build_html(title, tables, notes), user_css=CSS)
    page = pymupdf.paper_rect("a4-l" if landscape else "a4")
    where = page + (40, 40, -40, -48)
    buf = io.BytesIO()
    with ENGINE_LOCK:
        writer = pymupdf.DocumentWriter(buf)
        more = 1
        pages = 0
        while more:
            dev = writer.begin_page(page)
            more, _filled = story.place(where)
            story.draw(dev)
            writer.end_page()
            pages += 1
            if pages > 500:         # protezione: una cella più alta di una pagina non deve bloccare
                break
        writer.close()
        doc = pymupdf.open("pdf", buf.getvalue())
        doc.set_metadata({"title": title, "creator": "Foliant", "producer": "Foliant"})
        n = doc.page_count
        for i, p in enumerate(doc):
            p.insert_text((page.width - 110, page.height - 24), _("Pagina {i} di {n}").format(i=i + 1, n=n),
                          fontsize=7.5, color=(0.45, 0.45, 0.45))
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return data
