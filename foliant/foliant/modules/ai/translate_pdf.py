"""Traduzione di un documento in un nuovo PDF (AI-07): logica pura, senza Qt né rete.

Il chiamante fornisce `translate_blocks(page_index, [testi]) -> [testi tradotti]`
(che a sua volta interroga il fornitore IA). Qui si estraggono i blocchi di testo,
si compone il nuovo PDF nella modalità scelta e si prepara il rapporto finale.

Modalità:
- ``layout``: il testo tradotto prende il posto dell'originale nello stesso riquadro;
  immagini, grafica, collegamenti e annotazioni restano.
- ``bilingual``: ogni pagina originale è seguita dalla sua traduzione.
- ``text``: nuove pagine A4 con il solo testo tradotto, riformattato.

Il PDF prodotto contiene, nel catalogo, una chiave privata con la mappa
pagina sorgente -> pagine prodotte e lo stato del rapporto: serve a
«Ritenta pagine non riuscite» (`retry_pages` + `previous`).
"""

from __future__ import annotations

import html
import io
import json
from dataclasses import dataclass, field
from typing import Callable

import pymupdf

from foliant.core.engine import ENGINE_LOCK
from foliant.core.errors import OperationCancelled
from foliant.i18n import _

MODE_LAYOUT = "layout"
MODE_BILINGUAL = "bilingual"
MODE_TEXT = "text"
MODES = (MODE_LAYOUT, MODE_BILINGUAL, MODE_TEXT)

_STATE_KEY = "FoliantTranslation"
_A4 = (595.0, 842.0)
_TEXT_MARGIN = 56.0


@dataclass
class TextBlock:
    page: int
    index: int
    rect: tuple[float, float, float, float]
    text: str
    fontsize: float
    color: tuple[float, float, float]
    bold: bool
    font: str = ""
    italic: bool = False


@dataclass
class TranslationReport:
    translated_pages: list[int] = field(default_factory=list)
    failed_pages: dict[int, str] = field(default_factory=dict)
    shrunk_blocks: list[tuple[int, int]] = field(default_factory=list)
    minimum_size_blocks: list[tuple[int, int]] = field(default_factory=list)
    untranslated_pages: list[int] = field(default_factory=list)

    def to_text(self) -> str:
        def pages(items) -> str:
            return ", ".join(str(p + 1) for p in sorted(items))

        def blocks(items) -> str:
            return ", ".join(
                _("pagina {page}, blocco {block}").format(page=p + 1, block=b + 1)
                for p, b in sorted(items)
            )

        lines = [_("Rapporto di traduzione"), ""]
        lines.append(
            _("Pagine tradotte: {n}").format(n=len(self.translated_pages))
            + (f" ({pages(self.translated_pages)})" if self.translated_pages else "")
        )
        if self.failed_pages:
            lines.append("")
            lines.append(
                _("Pagine non riuscite, lasciate in lingua originale: {pages}").format(
                    pages=pages(self.failed_pages)
                )
            )
            for p in sorted(self.failed_pages):
                lines.append(
                    "  " + _("Pagina {n}: {error}").format(n=p + 1, error=self.failed_pages[p])
                )
        if self.untranslated_pages:
            lines.append("")
            lines.append(
                _("Pagine senza testo da tradurre (per esempio scansioni): {pages}").format(
                    pages=pages(self.untranslated_pages)
                )
            )
        if self.shrunk_blocks:
            lines.append("")
            lines.append(
                _("Blocchi ridotti per entrare nel riquadro: {blocks}").format(
                    blocks=blocks(self.shrunk_blocks)
                )
            )
        if self.minimum_size_blocks:
            lines.append("")
            lines.append(
                _(
                    "Blocchi inseriti al corpo minimo, che possono uscire dal riquadro: {blocks}"
                ).format(blocks=blocks(self.minimum_size_blocks))
            )
        if not self.failed_pages:
            lines.append("")
            lines.append(_("Nessun errore."))
        return "\n".join(lines) + "\n"

    # stato serializzabile (per i nuovi tentativi)
    def _to_state(self) -> dict:
        return {
            "translated": sorted(self.translated_pages),
            "failed": {str(k): v for k, v in self.failed_pages.items()},
            "shrunk": [list(x) for x in self.shrunk_blocks],
            "minimum": [list(x) for x in self.minimum_size_blocks],
            "untranslated": sorted(self.untranslated_pages),
        }

    @classmethod
    def _from_state(cls, state: dict) -> "TranslationReport":
        return cls(
            translated_pages=[int(p) for p in state.get("translated", [])],
            failed_pages={int(k): str(v) for k, v in state.get("failed", {}).items()},
            shrunk_blocks=[(int(a), int(b)) for a, b in state.get("shrunk", [])],
            minimum_size_blocks=[(int(a), int(b)) for a, b in state.get("minimum", [])],
            untranslated_pages=[int(p) for p in state.get("untranslated", [])],
        )

    def _forget_page(self, page: int) -> None:
        if page in self.translated_pages:
            self.translated_pages.remove(page)
        if page in self.untranslated_pages:
            self.untranslated_pages.remove(page)
        self.failed_pages.pop(page, None)
        self.shrunk_blocks = [b for b in self.shrunk_blocks if b[0] != page]
        self.minimum_size_blocks = [b for b in self.minimum_size_blocks if b[0] != page]


# ---------------------------------------------------------------- estrazione


def _int_to_rgb(value: int) -> tuple[float, float, float]:
    value = int(value or 0)
    return (((value >> 16) & 255) / 255.0, ((value >> 8) & 255) / 255.0, (value & 255) / 255.0)


def _join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and line[:1].islower():
            out = out[:-1] + line  # parola spezzata a fine riga
        else:
            out += " " + line
    return out


def extract_blocks(pdf: pymupdf.Document, page_index: int) -> list[TextBlock]:
    """Blocchi di testo (a livello di paragrafo) della pagina, con lo stile prevalente."""
    with ENGINE_LOCK:
        page = pdf[page_index]
        data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    result: list[TextBlock] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        lines: list[str] = []
        weight: dict[tuple, int] = {}
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            lines.append(text)
            for span in line.get("spans", []):
                n = len(span.get("text", "").strip())
                if not n:
                    continue
                flags = int(span.get("flags", 0))
                font = str(span.get("font", ""))
                bold = bool(flags & 16) or "bold" in font.lower()
                italic = bool(flags & 2) or "italic" in font.lower() or "oblique" in font.lower()
                key = (round(float(span.get("size", 11.0)), 2), int(span.get("color", 0)), bold, italic, font)
                weight[key] = weight.get(key, 0) + n
        text = _join_lines(lines)
        if not text.strip() or not weight:
            continue
        size, color, bold, italic, font = max(weight.items(), key=lambda kv: kv[1])[0]
        result.append(
            TextBlock(
                page=page_index,
                index=len(result),
                rect=tuple(float(v) for v in block["bbox"]),
                text=text,
                fontsize=float(size) or 11.0,
                color=_int_to_rgb(color),
                bold=bold,
                font=font,
                italic=italic,
            )
        )
    return result


# ---------------------------------------------------------------- composizione


def _family(font: str) -> str:
    name = font.lower()
    if any(k in name for k in ("courier", "mono", "consol", "cour")):
        return "monospace"
    if any(k in name for k in ("times", "serif", "georgia", "garamond", "roman", "tiro", "cambria")) and "sans" not in name:
        return "serif"
    return "sans-serif"


def _css_color(color: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(c * 255))) for c in color)
    return f"#{r:02x}{g:02x}{b:02x}"


def _block_html(block: TextBlock, text: str, fontsize: float | None = None) -> tuple[str, str]:
    size = block.fontsize if fontsize is None else fontsize
    css = (
        "* {margin: 0; padding: 0;} "
        f"p {{font-family: {_family(block.font)}; font-size: {size:.2f}px; "
        f"color: {_css_color(block.color)}; line-height: 1.15; "
        f"font-weight: {'bold' if block.bold else 'normal'}; "
        f"font-style: {'italic' if block.italic else 'normal'};}}"
    )
    body = "<p>" + html.escape(text).replace("\n", "<br/>") + "</p>"
    return body, css


def _place_block(page: pymupdf.Page, block: TextBlock, text: str, min_fontsize: float) -> str:
    """Inserisce un blocco tradotto. Restituisce "ok", "shrunk" o "minimum"."""
    body, css = _block_html(block, text)
    x0, y0, x1, y1 = block.rect
    rect = pymupdf.Rect(x0, y0, x1 + 1.0, y1 + block.fontsize * 0.25)
    scale_low = 1.0 if block.fontsize <= min_fontsize else min_fontsize / block.fontsize
    spare, scale = page.insert_htmlbox(rect, body, css=css, scale_low=scale_low)
    if spare >= 0:
        return "shrunk" if scale < 0.999 else "ok"
    # Non entra nemmeno al corpo minimo: il testo va comunque inserito per intero,
    # al corpo minimo, lasciandolo scendere oltre il riquadro (fino al fondo pagina);
    # se non basta si allarga verso destra e, in ultima istanza, si riduce ancora.
    size = min(block.fontsize, min_fontsize)
    body, css = _block_html(block, text, size)
    bottom = max(rect.y1, page.rect.y1 - 2.0)
    big = pymupdf.Rect(rect.x0, rect.y0, rect.x1, bottom)
    spare, scale = page.insert_htmlbox(big, body, css=css, scale_low=1)
    if spare < 0:
        big.x1 = max(rect.x1, page.rect.x1 - 2.0)
        page.insert_htmlbox(big, body, css=css, scale_low=0)
    return "minimum"


def _translate_page_layout(
    page: pymupdf.Page, blocks: list[TextBlock], texts: list[str], min_fontsize: float,
    report: TranslationReport,
) -> None:
    links = page.get_links()  # la rimozione del testo eliminerebbe i collegamenti sovrapposti
    for block in blocks:
        x0, y0, x1, y1 = block.rect
        page.add_redact_annot(pymupdf.Rect(x0, y0, x1, y1), fill=False)
    kwargs = {"images": pymupdf.PDF_REDACT_IMAGE_NONE}
    if hasattr(pymupdf, "PDF_REDACT_LINE_ART_NONE"):
        kwargs["graphics"] = pymupdf.PDF_REDACT_LINE_ART_NONE
    page.apply_redactions(**kwargs)
    remaining = {(tuple(link["from"]), link.get("uri"), link.get("page")) for link in page.get_links()}
    for link in links:
        if (tuple(link["from"]), link.get("uri"), link.get("page")) not in remaining:
            link = dict(link)
            link.pop("xref", None)
            link.pop("id", None)
            page.insert_link(link)
    for block, text in zip(blocks, texts):
        outcome = _place_block(page, block, text, min_fontsize)
        if outcome == "shrunk":
            report.shrunk_blocks.append((block.page, block.index))
        elif outcome == "minimum":
            report.minimum_size_blocks.append((block.page, block.index))


def _text_pages(page_index: int, texts: list[str]) -> pymupdf.Document:
    """Pagine A4 con il testo riformattato di una pagina sorgente."""
    heading = html.escape(_("Pagina {n}").format(n=page_index + 1))
    paragraphs = "".join(f"<p>{html.escape(t)}</p>" for t in texts if t.strip())
    css = (
        "body {font-family: sans-serif; font-size: 11px; line-height: 1.35;} "
        "h1 {font-size: 15px; margin-bottom: 10px;} p {margin-bottom: 7px;}"
    )
    story = pymupdf.Story(html=f"<h1>{heading}</h1>{paragraphs}", user_css=css)

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.Rect(0, 0, *_A4)
    where = mediabox + (_TEXT_MARGIN, _TEXT_MARGIN, -_TEXT_MARGIN, -_TEXT_MARGIN)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buffer.getvalue())


# ---------------------------------------------------------------- stato nel PDF


def _write_state(doc: pymupdf.Document, state: dict) -> None:
    payload = json.dumps(state, separators=(",", ":"), ensure_ascii=True)
    doc.xref_set_key(doc.pdf_catalog(), _STATE_KEY, pymupdf.get_pdf_str(payload))


def _read_state(doc: pymupdf.Document) -> dict | None:
    kind, value = doc.xref_get_key(doc.pdf_catalog(), _STATE_KEY)
    if kind != "string":
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _set_language(doc: pymupdf.Document, language: str) -> None:
    if not language:
        return
    if hasattr(doc, "set_language"):
        try:
            doc.set_language(language)
            return
        except Exception:
            pass
    doc.xref_set_key(doc.pdf_catalog(), "Lang", pymupdf.get_pdf_str(language))


# ---------------------------------------------------------------- traduzione


def translate_document(
    source_pdf_bytes: bytes,
    pages: list[int] | None,
    mode: str,
    translate_blocks: Callable[[int, list[str]], list[str]],
    progress=None,
    *,
    target_language: str = "en",
    min_fontsize: float = 6.0,
    retry_pages: list[int] | None = None,
    previous: bytes | None = None,
) -> tuple[bytes, TranslationReport]:
    """Crea il PDF tradotto. Restituisce (byte del nuovo PDF, rapporto).

    `pages`: indici delle pagine sorgente da tradurre (None = tutte); il nuovo PDF
    contiene solo quelle pagine. Con `retry_pages` e `previous` (byte prodotti da una
    chiamata precedente) si ritraducono solo quelle pagine sopra il risultato precedente:
    `pages` e `mode` vengono allora ripresi dal risultato precedente.
    `OperationCancelled` (dal callback o da `progress.update`) si propaga.
    """
    if mode not in MODES:
        raise ValueError(f"modalità sconosciuta: {mode!r}")
    retrying = retry_pages is not None
    if retrying and previous is None:
        raise ValueError("retry_pages richiede previous")

    work = original = prev_doc = None
    chunks: dict[int, pymupdf.Document | None] = {}
    try:
        with ENGINE_LOCK:
            work = pymupdf.open("pdf", bytes(source_pdf_bytes))  # copia: la sorgente non cambia
            original = pymupdf.open("pdf", bytes(source_pdf_bytes))
            page_count = work.page_count
            prev_state = None
            if retrying:
                prev_doc = pymupdf.open("pdf", previous)
                prev_state = _read_state(prev_doc)
        if retrying:
            if prev_state is None:
                raise ValueError("il PDF precedente non contiene i dati della traduzione")
            mode = prev_state.get("mode", mode)
            selected = [int(p) for p in prev_state["pages"]]
            todo = [p for p in sorted(set(retry_pages)) if p in selected]
            report = TranslationReport._from_state(prev_state.get("report", {}))
            for p in todo:
                report._forget_page(p)
        else:
            selected = list(range(page_count)) if pages is None else list(pages)
            selected = list(dict.fromkeys(int(p) for p in selected))
            for p in selected:
                if not 0 <= p < page_count:
                    raise ValueError(f"pagina fuori intervallo: {p}")
            todo = list(selected)
            report = TranslationReport()

        # Il lock non viene tenuto durante il callback (richieste al fornitore IA).
        need_chunks = retrying or mode != MODE_LAYOUT
        total = max(1, len(todo))
        for n, p in enumerate(todo):
            if progress is not None:
                progress.update(n / total, _("Pagina {n} di {tot}").format(n=n + 1, tot=len(todo)))
            chunks[p] = _process_page(
                work, original, p, mode, translate_blocks, min_fontsize, report, need_chunks
            )
        if progress is not None:
            progress.update(1.0, _("Composizione del PDF"))

        with ENGINE_LOCK:
            if retrying:
                out = prev_doc
                counts = {int(k): int(v) for k, v in prev_state["counts"].items()}
                starts: dict[int, int] = {}
                pos = 0
                for p in selected:
                    starts[p] = pos
                    pos += counts[p]
                for p in sorted(todo, key=lambda q: starts[q], reverse=True):
                    start, count = starts[p], counts[p]
                    out.delete_pages(from_page=start, to_page=start + count - 1)
                    chunk = chunks[p]
                    out.insert_pdf(chunk, start_at=start)
                    counts[p] = chunk.page_count
            elif mode == MODE_LAYOUT:
                out = work
                if selected != list(range(page_count)):
                    out.select(selected)
                counts = {p: 1 for p in selected}
            else:
                out = prev_doc = pymupdf.open()  # chiuso nel finally
                counts = {}
                for p in selected:
                    chunk = chunks[p]
                    out.insert_pdf(chunk)
                    counts[p] = chunk.page_count

            report.translated_pages.sort()
            report.untranslated_pages.sort()
            _set_language(out, target_language)
            _write_state(
                out,
                {
                    "mode": mode,
                    "pages": selected,
                    "counts": {str(k): v for k, v in counts.items()},
                    "report": report._to_state(),
                },
            )
            data = out.tobytes(garbage=3, deflate=True)
    finally:
        with ENGINE_LOCK:
            for doc in (work, original, prev_doc, *chunks.values()):
                if doc is not None and not doc.is_closed:
                    doc.close()
    return data, report


def _process_page(
    work: pymupdf.Document,
    original: pymupdf.Document,
    p: int,
    mode: str,
    translate_blocks: Callable[[int, list[str]], list[str]],
    min_fontsize: float,
    report: TranslationReport,
    need_chunk: bool = True,
) -> pymupdf.Document | None:
    """Traduce la pagina `p` e restituisce le pagine prodotte per essa.

    In modalità layout la pagina di `work` viene modificata sul posto e il
    risultato è un documento con quella sola pagina.
    """
    blocks = extract_blocks(work, p)
    texts = [b.text for b in blocks]
    translated: list[str] | None = None
    if not blocks:
        report.untranslated_pages.append(p)
    else:
        try:
            result = translate_blocks(p, list(texts))
            result = [str(t) for t in (result or [])]
            if len(result) != len(blocks):
                raise ValueError(
                    _("risposta con {got} blocchi invece di {exp}").format(
                        got=len(result), exp=len(blocks)
                    )
                )
            translated = result
        except OperationCancelled:
            raise
        except Exception as exc:  # pagina lasciata in lingua originale
            report.failed_pages[p] = str(exc) or exc.__class__.__name__

    with ENGINE_LOCK:
        return _compose_page(
            work, original, p, mode, blocks, texts, translated, min_fontsize, report, need_chunk
        )


def _compose_page(
    work: pymupdf.Document,
    original: pymupdf.Document,
    p: int,
    mode: str,
    blocks: list[TextBlock],
    texts: list[str],
    translated: list[str] | None,
    min_fontsize: float,
    report: TranslationReport,
    need_chunk: bool,
) -> pymupdf.Document | None:
    shrunk_before = len(report.shrunk_blocks)
    minimum_before = len(report.minimum_size_blocks)
    if mode in (MODE_LAYOUT, MODE_BILINGUAL) and translated is not None:
        try:
            _translate_page_layout(work[p], blocks, translated, min_fontsize, report)
        except Exception as exc:
            # composizione fallita: la pagina resta com'era (ricaricata dalla sorgente)
            del report.shrunk_blocks[shrunk_before:]
            del report.minimum_size_blocks[minimum_before:]
            report.failed_pages[p] = str(exc) or exc.__class__.__name__
            translated = None
            work.delete_page(p)
            work.insert_pdf(original, from_page=p, to_page=p, start_at=p)
    if translated is not None:
        report.translated_pages.append(p)

    if mode == MODE_TEXT:
        return _text_pages(p, translated if translated is not None else texts)
    if not need_chunk:
        return None
    chunk = pymupdf.open()
    if mode == MODE_BILINGUAL:
        chunk.insert_pdf(original, from_page=p, to_page=p)
    chunk.insert_pdf(work, from_page=p, to_page=p)
    return chunk

