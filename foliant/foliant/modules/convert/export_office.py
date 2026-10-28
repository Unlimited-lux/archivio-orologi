"""Conversioni verso formati Office: DOCX, XLSX, CSV, PPTX (senza interfaccia).

Ogni funzione `write_*` riceve il documento di lavoro (`pymupdf.Document`, che
contiene solo le pagine da convertire, nell'ordine scelto), il percorso
temporaneo su cui scrivere, un oggetto `Job` con opzioni e report, e
restituisce nulla: gli avvisi finiscono in `job.warnings`.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

import pymupdf

from foliant.api import ENGINE_LOCK, _, page_pixmap

from .common import ConversionError, check
from .extract import (
    TEXT_FLAGS, ImageItem, Para, TableItem, analyze_page, assign_headings, find_tables, image_backed,
    normalize_text,
    table_rows,
)


# ------------------------------------------------------------------ immagini
def _image_for_office(item: ImageItem) -> tuple[bytes, str] | None:
    """Immagine in un formato accettato da Word/PowerPoint (PNG/JPEG/GIF/BMP/TIFF)."""
    ext = (item.ext or "").lower()
    if ext in ("png", "jpeg", "jpg", "gif", "bmp", "tiff", "tif"):
        return item.data, ext
    try:
        with ENGINE_LOCK:
            pix = pymupdf.Pixmap(item.data)
            if pix.alpha or pix.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            return pix.tobytes("png"), "png"
    except Exception:
        return None


def _rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 255, (color >> 8) & 255, color & 255


def analyze_document(work: pymupdf.Document, job, *, images: bool = True, tables: bool = True,
                     phase: tuple[float, float] = (0.0, 0.8)) -> list:
    """Analizza tutte le pagine del documento di lavoro (con avanzamento)."""
    pages = []
    n = work.page_count
    a, b = phase
    for i in range(n):
        check(job.progress, a + (b - a) * i / max(1, n), _("Analisi pagina {i} di {n}").format(i=i + 1, n=n))
        with ENGINE_LOCK:
            pc = analyze_page(work[i], ocr=job.ocr, language=job.language, images=images, tables=tables,
                              dehyphenate=job.options.get("dehyphenate", True))
        pc.index = job.source_index(i)
        job.note_page(pc)
        pages.append(pc)
    # i link interni puntano a pagine del documento di lavoro: riportati alla numerazione originale
    exported = set(job.page_map)
    for pc in pages:
        for it in pc.items:
            if isinstance(it, Para):
                for sp in it.spans:
                    if sp.link and sp.link.startswith("#page-"):
                        target = job.source_index(int(sp.link[6:]) - 1)
                        sp.link = f"#page-{target + 1}" if target in exported else None
    assign_headings(pages)
    return pages


# ---------------------------------------------------------------------- DOCX
def _add_hyperlink(paragraph, text: str, target: str, run_props: dict):
    """Aggiunge un collegamento (URL o ancora interna) a un paragrafo python-docx."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    link = OxmlElement("w:hyperlink")
    if target.startswith("#"):
        link.set(qn("w:anchor"), target[1:].replace("-", "_"))
    else:
        r_id = paragraph.part.relate_to(target, RT.HYPERLINK, is_external=True)
        link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), "Hyperlink")
    rpr.append(style)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rpr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rpr.append(u)
    if run_props.get("bold"):
        rpr.append(OxmlElement("w:b"))
    if run_props.get("italic"):
        rpr.append(OxmlElement("w:i"))
    if run_props.get("size"):
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(round(run_props["size"] * 2))))
        rpr.append(sz)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _add_bookmark(paragraph, name: str, bid: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bid))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bid))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def _write_runs(paragraph, para: Para, heading: bool) -> None:
    from docx.shared import Pt, RGBColor
    for s in para.spans:
        if not s.text:
            continue
        if s.link:
            _add_hyperlink(paragraph, s.text, s.link, {"bold": s.bold, "italic": s.italic,
                                                      "size": None if heading else s.size})
            continue
        run = paragraph.add_run(s.text)
        if not heading:
            run.bold = s.bold or None
            run.italic = s.italic or None
            run.font.size = Pt(max(1.0, round(s.size * 2) / 2))
            if s.color:
                run.font.color.rgb = RGBColor(*_rgb(s.color))
            if s.clean_font:
                run.font.name = s.clean_font


def write_docx_flow(work: pymupdf.Document, path: str, job) -> None:
    """DOCX «testo scorrevole»: paragrafi, titoli, elenchi, tabelle, immagini e link."""
    import docx
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt

    pages = analyze_document(work, job)
    document = docx.Document()
    section = document.sections[0]
    if pages:
        w, h = pages[0].width, pages[0].height
        section.page_width, section.page_height = Pt(w), Pt(h)
        section.orientation = WD_ORIENT.LANDSCAPE if w > h else WD_ORIENT.PORTRAIT
        margin = Pt(min(56.7, w * 0.08))
        section.left_margin = section.right_margin = section.top_margin = section.bottom_margin = margin
    usable = section.page_width - section.left_margin - section.right_margin
    skip: set[int] = set()
    if job.options.get("header_footer", True) and len(pages) > 1:
        from .extract import repeated_furniture
        skip = repeated_furniture(pages)
        _furniture_to_sections(document, pages, skip)
    bid = 0
    n = len(pages)
    for pi, pc in enumerate(pages):
        check(job.progress, 0.8 + 0.18 * pi / max(1, n), _("Scrittura del documento…"))
        first = True
        placed: list[tuple[tuple, object]] = []
        for it in pc.items:
            if id(it) in skip:
                continue
            if isinstance(it, Para):
                if it.kind.startswith("h"):
                    p = document.add_heading(level=int(it.kind[1]))
                    _write_runs(p, it, heading=True)
                else:
                    style = "List Bullet" if it.kind == "li" else None
                    p = document.add_paragraph(style=style)
                    if it.kind == "li":
                        it.spans[0].text = re.sub(r"^\s*[•\-–*]\s*", "", it.spans[0].text)
                    _write_runs(p, it, heading=False)
            elif isinstance(it, TableItem):
                cols = max(len(r) for r in it.rows)
                table = document.add_table(rows=len(it.rows), cols=cols)
                table.style = "Table Grid"
                for r, row in enumerate(it.rows):
                    for c in range(cols):
                        table.cell(r, c).text = row[c] if c < len(row) and row[c] else ""
                p = None
            elif isinstance(it, ImageItem):
                img = _image_for_office(it)
                if img is None:
                    job.warn(_("Immagine non convertibile a pagina {p}").format(p=pc.index + 1))
                    continue
                width = min(int(Pt(max(8.0, it.bbox[2] - it.bbox[0]))), int(usable))
                try:
                    document.add_picture(io.BytesIO(img[0]), width=width)
                    p = document.paragraphs[-1]
                except Exception:
                    job.warn(_("Immagine non convertibile a pagina {p}").format(p=pc.index + 1))
                    continue
            else:
                continue
            if p is not None:
                placed.append((it.bbox, p))
            if first and p is not None:
                bid += 1
                _add_bookmark(p, f"page_{pc.index + 1}", bid)
                first = False
        if job.options.get("comments", True):
            _add_comments(document, work, pi, placed, job)
    if not any(pc.items for pc in pages):
        document.add_paragraph("")
    document.save(path)


def _add_field(paragraph, instr: str, placeholder: str = "1") -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = placeholder
    run.append(t)
    fld.append(run)
    paragraph._p.append(fld)


def _furniture_to_sections(document, pages, skip: set[int]) -> None:
    """Intestazioni e piè di pagina ripetuti nelle aree di intestazione/piè (numeri come campi)."""
    section = document.sections[0]
    done = {"header": False, "footer": False}
    for pc in pages:
        for it in pc.items:
            if id(it) not in skip or not isinstance(it, Para):
                continue
            where = "header" if it.bbox[1] < pc.height / 2 else "footer"
            if done[where]:
                continue
            done[where] = True
            area = section.header if where == "header" else section.footer
            par = area.paragraphs[0]
            parts = re.split(r"(\d+)", it.text)
            numbers = 0
            for part in parts:
                if part.isdigit():
                    _add_field(par, "PAGE" if numbers == 0 else "NUMPAGES", part)
                    numbers += 1
                elif part:
                    par.add_run(part)


def _add_comments(document, work, pi: int, placed: list, job) -> None:
    """Commenti del PDF come commenti del documento, ancorati al paragrafo più vicino."""
    with ENGINE_LOCK:
        page = work[pi]
        m = page.rotation_matrix
        notes = [(pymupdf.Rect(a.rect) * m, a.info.get("content", "").strip(), a.info.get("title", "").strip())
                 for a in page.annots() if a.info.get("content", "").strip()]
    if not notes:
        return
    for rect, text, author in notes:
        target = None
        best = None
        for bbox, par in placed:
            r = pymupdf.Rect(bbox)
            dist = 0 if r.intersects(rect) else min(abs(r.y0 - rect.y1), abs(rect.y0 - r.y1))
            if best is None or dist < best:
                best, target = dist, par
        if target is None:
            target = document.add_paragraph()
        runs = target.runs or [target.add_run("")]
        try:
            document.add_comment(runs, text=text, author=author or "PDF",
                                 initials=(author or "PDF")[:2].upper())
        except Exception:
            job.warn(_("Un commento non è stato convertito."))


def write_docx_faithful(work: pymupdf.Document, path: str, job) -> None:
    """DOCX «fedele» con pdf2docx: una sezione per pagina, posizioni preservate."""
    import logging

    from pdf2docx import Converter
    logging.getLogger("pdf2docx").setLevel(logging.ERROR)
    logging.getLogger().setLevel(max(logging.getLogger().level, logging.WARNING))

    with ENGINE_LOCK:
        data = job.ocr_document_bytes(work)
        scanned = any(image_backed(work[i]) for i in range(work.page_count))
    if scanned:
        job.warn(_("Pagine scansionate: il layout «Testo scorrevole» ricostruisce meglio paragrafi e tabelle."))
    cv = Converter(stream=data)
    try:
        settings = cv.default_settings
        settings.update({"ignore_page_error": True, "raw_exceptions": False, "debug": False,
                         "multi_processing": False})
        with ENGINE_LOCK:
            cv.load_pages()
            cv.parse_document(**settings)
        pages = [p for p in cv.pages if not p.skip_parsing]
        n = len(pages)
        for i, page in enumerate(pages):
            check(job.progress, 0.1 + 0.8 * i / max(1, n), _("Conversione pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                try:
                    page.parse(**settings)
                except Exception as e:  # pagina non convertibile: segnalata, le altre proseguono
                    job.warn(_("Pagina {p}: conversione non riuscita ({e})").format(p=job.source_index(i) + 1, e=e))
        check(job.progress, 0.92, _("Scrittura del documento…"))
        with ENGINE_LOCK:
            cv.make_docx(path, **settings)
    except ConversionError:
        raise
    except Exception as e:
        if type(e).__name__ == "OperationCancelled":
            raise
        raise ConversionError(_("Conversione in DOCX non riuscita: {e}").format(e=e)) from e
    finally:
        cv.close()


def write_docx(work, path, job) -> None:
    if job.options.get("layout", "flow") == "faithful":
        write_docx_faithful(work, path, job)
    else:
        write_docx_flow(work, path, job)


# ------------------------------------------------------------ celle tabellari
_DATE_PATTERNS = [
    (re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), ("y", "m", "d")),
]


def detect_decimal(cells: list[str]) -> str:
    """Separatore decimale prevalente nelle celle: «,» (italiano) o «.»."""
    comma = dot = 0
    for c in cells:
        c = c.strip()
        if re.search(r"\d\.\d{3},\d", c) or re.fullmatch(r"[-+(]?[€$£]?\s?\d+,\d{1,2}\)?%?", c):
            comma += 1
        elif re.search(r"\d,\d{3}\.\d", c) or re.fullmatch(r"[-+(]?[€$£]?\s?\d+\.\d{1,2}\)?%?", c):
            dot += 1
    return "." if dot > comma else ","


def parse_cell(text: str, decimal: str = ","):
    """Converte il testo di una cella in valore tipizzato: (valore, formato numerico o None).

    Numeri, importi, percentuali (15% → 0,15), negativi con segno o tra
    parentesi e date diventano valori; i codici con zeri iniziali restano testo.
    """
    raw = (text or "").strip()
    if not raw:
        return None, None
    s = raw.replace(" ", " ").replace("−", "-")
    for pat, order in _DATE_PATTERNS:
        m = pat.match(s)
        if m:
            parts = dict(zip(order, (int(g) for g in m.groups())))
            try:
                return dt.date(parts["y"], parts["m"], parts["d"]), "dd/mm/yyyy"
            except ValueError:
                return raw, None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    percent = s.endswith("%")
    if percent:
        s = s[:-1].strip()
    s = re.sub(r"^[€$£]\s?|\s?[€$£]$|\s?(EUR|USD)$", "", s).strip()
    if s.startswith(("-", "+")):
        neg = neg or s[0] == "-"
        s = s[1:].strip()
    if not s or not s[0].isdigit():
        return raw, None
    thousands = "." if decimal == "," else ","
    if re.fullmatch(r"0\d+", s):
        return raw, None        # codice con zeri iniziali (CAP, articoli)
    pat = rf"\d{{1,3}}(\{thousands}\d{{3}})+(\{decimal}\d+)?|\d+(\{decimal}\d+)?"
    s_compact = s.replace(" ", "") if re.fullmatch(r"\d{1,3}( \d{3})+([.,]\d+)?", s) else s
    if s_compact != s:
        s = s_compact
        pat = rf"\d+(\{decimal}\d+)?"
    if not re.fullmatch(pat, s):
        return raw, None
    number_text = s.replace(thousands, "").replace(decimal, ".")
    try:
        value = float(number_text)
    except ValueError:
        return raw, None
    if neg:
        value = -value
    decimals = len(number_text.split(".")[1]) if "." in number_text else 0
    if percent:
        return value / 100.0, "0" + ("." + "0" * decimals if decimals else "") + "%"
    if decimals == 0 and abs(value) < 2 ** 53:
        value = int(value)
        fmt = "#,##0" if len(s) > 3 and thousands in s else "0"
    else:
        fmt = "#,##0." + "0" * decimals if thousands in s else "0." + "0" * decimals
    return value, fmt


def collect_tables(work: pymupdf.Document, job, strategy: str = "lines") -> list[tuple[int, list[list[str]], int]]:
    """Tabelle del documento: elenco (pagina sorgente, righe, n. tabella nella pagina)."""
    found = []
    n = work.page_count
    for i in range(n):
        check(job.progress, 0.7 * i / max(1, n), _("Ricerca tabelle a pagina {i} di {n}").format(i=i + 1, n=n))
        with ENGINE_LOCK:
            page = work[i]
            try:
                tabs = find_tables(page, strategy if strategy != "auto" else None)
                if not tabs.tables and strategy == "auto":
                    tabs = find_tables(page, "text")
            except Exception:
                tabs = None
            if (not tabs or not tabs.tables) and image_backed(page):
                from .extract import grid_tables
                for t_i, (_bbox, rows, _used) in enumerate(grid_tables(
                        page.get_text("dict", flags=TEXT_FLAGS).get("blocks", []))):
                    found.append((job.source_index(i), rows, t_i + 1))
                continue
            words = page.get_text("words", flags=TEXT_FLAGS) if tabs and tabs.tables else []
            for t_i, t in enumerate(tabs.tables if tabs else []):
                rows = table_rows(page, t, words)
                rows = [r for r in rows if any((c or "").strip() for c in r)]
                if rows and max(len(r) for r in rows) >= 2:
                    found.append((job.source_index(i), rows, t_i + 1))
    return found


def merge_tables(tables: list[tuple[int, list, int]]) -> list[tuple[int, list, int]]:
    """Unisce le tabelle che proseguono sulla pagina seguente con le stesse colonne,
    togliendo l'intestazione ripetuta."""
    out: list[tuple[int, list, int]] = []
    last_page = -10
    for page, rows, n in tables:
        if out:
            prev_page, prev_rows, prev_n = out[-1]
            same_cols = len(prev_rows[0]) == len(rows[0])
            if same_cols and 0 < page - last_page <= 1:
                body = rows[1:] if [c or "" for c in rows[0]] == [c or "" for c in prev_rows[0]] else rows
                out[-1] = (prev_page, prev_rows + body, prev_n)
                last_page = page
                continue
        out.append((page, [list(r) for r in rows], n))
        last_page = page
    return out


def _header_rows(rows: list[list]) -> int:
    """Righe d'intestazione: la prima più quella successiva se la prima ha celle unite (None)."""
    if len(rows) >= 3 and any(c is None for c in rows[0]):
        return 2
    return 1


def _set_text(cell, text: str) -> None:
    """Testo letterale: un valore che inizia con «=» non deve diventare una formula."""
    cell.value = text
    if isinstance(text, str) and text.startswith("="):
        cell.data_type = "s"


def write_xlsx(work: pymupdf.Document, path: str, job) -> None:
    """Tabelle in fogli XLSX con celle tipizzate, intestazioni unite e larghezze adattate."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    strategy = job.options.get("table_strategy", "lines")
    tables = collect_tables(work, job, strategy)
    if job.options.get("merge_tables", True):
        tables = merge_tables(tables)
    sheets_mode = job.options.get("sheets", "single")     # single | page | table
    decimal = job.options.get("decimal") or detect_decimal(
        [c for _p, rows, _n in tables for r in rows for c in r if c])
    include_text = job.options.get("include_text", False)

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    sheets: dict[str, object] = {}
    cursor: dict[str, int] = {}
    widths: dict[str, dict[int, float]] = {}

    def sheet_for(page: int, n: int):
        if sheets_mode == "page":
            name = _("Pagina {p}").format(p=page + 1)
        elif sheets_mode == "table":
            name = _("Tabella {t} p{p}").format(t=n, p=page + 1)
        else:
            name = _("Tabelle")
        name = name[:31]
        if name not in sheets:
            sheets[name] = wb.create_sheet(name)
            cursor[name] = 1
            widths[name] = {}
        return name, sheets[name]

    for ti, (page, rows, n) in enumerate(tables):
        check(job.progress, 0.7 + 0.25 * ti / max(1, len(tables)), _("Scrittura del foglio…"))
        name, ws = sheet_for(page, n)
        r0 = cursor[name] + (1 if cursor[name] > 1 else 0)
        header = _header_rows(rows)
        if r0 + len(rows) > 1_048_576:
            job.warn(_("Tabella troppo lunga per un foglio: righe in eccesso omesse."))
            rows = rows[: max(0, 1_048_576 - r0)]
        for r, row in enumerate(rows):
            for c, val in enumerate(row[:16384]):
                cell = ws.cell(row=r0 + r, column=c + 1)
                cell.border = border
                if val is None:
                    continue
                if r < header:
                    _set_text(cell, val)
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                else:
                    value, fmt = parse_cell(val, decimal)
                    if isinstance(value, str):
                        _set_text(cell, value)
                    else:
                        cell.value = value
                    if fmt:
                        cell.number_format = fmt
                    elif isinstance(value, str):
                        cell.number_format = "@"
                w = widths[name]
                w[c] = max(w.get(c, 0), min(60, max(len(line) for line in str(val).split("\n")) + 2))
            # celle unite nell'intestazione (le celle None seguono quella unita)
            if r < header:
                c = 0
                while c < len(row):
                    end = c
                    while end + 1 < len(row) and row[end + 1] is None:
                        end += 1
                    if end > c and row[c] is not None:
                        ws.merge_cells(start_row=r0 + r, start_column=c + 1, end_row=r0 + r, end_column=end + 1)
                    c = end + 1
        cursor[name] = r0 + len(rows)
    if include_text:
        pages_with_tables = {p for p, _r, _n in tables}
        for i in range(work.page_count):
            src = job.source_index(i)
            if src in pages_with_tables:
                continue
            with ENGINE_LOCK:
                text = work[i].get_text("text", sort=True)
            if not text.strip():
                continue
            name, ws = sheet_for(src, 0) if sheets_mode != "single" else sheet_for(src, 0)
            r0 = cursor[name] + (1 if cursor[name] > 1 else 0)
            for k, line in enumerate(l for l in text.splitlines() if l.strip()):
                _set_text(ws.cell(row=r0 + k, column=1), normalize_text(line))
                cursor[name] = r0 + k
    if not wb.sheetnames:
        if not tables:
            raise ConversionError(_("Non è stata trovata alcuna tabella nelle pagine scelte."))
    for name, ws in sheets.items():
        for c, w in widths[name].items():
            ws.column_dimensions[get_column_letter(c + 1)].width = max(8, w)
    wb.save(path)


def table_matrix(rows: list[list]) -> list[list[str]]:
    """Righe con celle unite appiattite (valore nella prima, le altre vuote)."""
    width = max(len(r) for r in rows)
    return [[(r[c] or "") if c < len(r) else "" for c in range(width)] for r in rows]


def write_csv_files(work: pymupdf.Document, base_path: str, job, out) -> list[str]:
    """CSV conforme a RFC 4180: un file per tabella o un file unico."""
    strategy = job.options.get("table_strategy", "lines")
    tables = collect_tables(work, job, strategy)
    if job.options.get("merge_tables", False):
        tables = merge_tables(tables)
    if not tables:
        raise ConversionError(_("Non è stata trovata alcuna tabella: nessun file CSV creato."))
    delimiter = job.options.get("delimiter", ";")
    if delimiter in ("tab", "\\t"):
        delimiter = "\t"
    encoding = "utf-8-sig" if job.options.get("bom", False) else "utf-8"
    newline = "\r\n" if job.options.get("line_ending", "crlf").lower() == "crlf" else "\n"
    normalize = job.options.get("numbers", "displayed") == "normalized"
    decimal = detect_decimal([c for _p, rows, _n in tables for r in rows for c in r if c])

    def fmt(val: str) -> str:
        if not normalize or not val:
            return val
        value, _f = parse_cell(val, decimal)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value) if isinstance(value, float) else str(value)
        if isinstance(value, dt.date):
            return value.isoformat()
        return val

    from pathlib import Path
    base = Path(base_path)
    created = []
    if job.options.get("single_file", False):
        tmp = out.new(base)
        with open(tmp, "w", encoding=encoding, newline="") as f:
            writer = csv.writer(f, delimiter=delimiter, lineterminator=newline, quoting=csv.QUOTE_MINIMAL)
            for k, (_page, rows, _n) in enumerate(tables):
                if k:
                    f.write(newline)
                for row in table_matrix(rows):
                    writer.writerow([fmt(c) for c in row])
        created.append(tmp)
    else:
        for page, rows, n in tables:
            tmp = out.new(base.with_name(f"{base.stem}_p{page + 1}_t{n}{base.suffix or '.csv'}"))
            with open(tmp, "w", encoding=encoding, newline="") as f:
                writer = csv.writer(f, delimiter=delimiter, lineterminator=newline, quoting=csv.QUOTE_MINIMAL)
                for row in table_matrix(rows):
                    writer.writerow([fmt(c) for c in row])
            created.append(tmp)
    return created


# ---------------------------------------------------------------------- PPTX
_EMU = 12700  # EMU per punto
_MAX_SLIDE = 51206400  # limite di PowerPoint (56 pollici)


def write_pptx(work: pymupdf.Document, path: str, job) -> None:
    """Una diapositiva per pagina: caselle di testo, immagini e forme semplici modificabili."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.util import Emu, Pt

    pages = analyze_document(work, job, tables=False, phase=(0.0, 0.6))
    if not pages:
        raise ConversionError(_("Nessuna pagina da convertire."))
    size_opt = job.options.get("slide_size", "page")
    w0, h0 = pages[0].width, pages[0].height
    if size_opt == "16:9":
        sw, sh = 960.0, 540.0
    elif size_opt == "4:3":
        sw, sh = 720.0, 540.0
    else:
        sw, sh = w0, h0
    k = min(1.0, _MAX_SLIDE / (max(sw, sh) * _EMU))
    sw, sh = sw * k, sh * k
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(int(sw * _EMU)), Emu(int(sh * _EMU))
    blank = prs.slide_layouts[6]
    max_shapes = int(job.options.get("max_vector_shapes", 400))

    for pi, pc in enumerate(pages):
        check(job.progress, 0.6 + 0.38 * pi / len(pages), _("Diapositiva {i} di {n}").format(i=pi + 1, n=len(pages)))
        slide = prs.slides.add_slide(blank)
        if job.options.get("comments_as_notes", True):
            with ENGINE_LOCK:
                notes = [a.info.get("content", "").strip() for a in work[pi].annots()
                         if a.info.get("content", "").strip()]
            if notes:
                slide.notes_slide.notes_text_frame.text = "\n".join(notes)
        scale = min(sw / pc.width, sh / pc.height)
        ox, oy = (sw - pc.width * scale) / 2, (sh - pc.height * scale) / 2

        def box(b):
            return (Emu(int((ox + b[0] * scale) * _EMU)), Emu(int((oy + b[1] * scale) * _EMU)),
                    Emu(max(1, int((b[2] - b[0]) * scale * _EMU))), Emu(max(1, int((b[3] - b[1]) * scale * _EMU))))

        if pc.ocr or "scansione-senza-ocr" in pc.warnings or "ocr-non-disponibile" in pc.warnings:
            with ENGINE_LOCK:
                png = page_pixmap(work, pi, hide_unsigned_signatures=True, dpi=job.dpi).tobytes("png")
            slide.shapes.add_picture(io.BytesIO(png), *box((0, 0, pc.width, pc.height)))
            if not pc.ocr:
                job.warn(_("Pagina {p} scansionata: inserita come immagine.").format(p=pc.index + 1))
        # forme vettoriali semplici
        if job.options.get("vector", "shapes") == "shapes":
            with ENGINE_LOCK:
                try:
                    drawings = work[pi].get_drawings()
                except Exception:
                    drawings = []
            if len(drawings) > max_shapes:
                job.warn(_("Pagina {p}: grafica complessa non convertita in forme.").format(p=pc.index + 1))
                drawings = []
            with ENGINE_LOCK:
                rot_m = work[pi].rotation_matrix
            for d in drawings:
                for item in d.get("items", []):
                    try:
                        if item[0] == "re":
                            r = pymupdf.Rect(item[1]) * rot_m
                            shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, *box((r.x0, r.y0, r.x1, r.y1)))
                        elif item[0] == "l":
                            p1, p2 = item[1] * rot_m, item[2] * rot_m
                            shp = slide.shapes.add_connector(
                                MSO_CONNECTOR.STRAIGHT, Emu(int((ox + p1.x * scale) * _EMU)),
                                Emu(int((oy + p1.y * scale) * _EMU)), Emu(int((ox + p2.x * scale) * _EMU)),
                                Emu(int((oy + p2.y * scale) * _EMU)))
                        else:
                            continue
                    except Exception:
                        continue
                    fill = d.get("fill")
                    if item[0] == "re":
                        if fill:
                            shp.fill.solid()
                            shp.fill.fore_color.rgb = RGBColor(*(int(c * 255) for c in fill[:3]))
                        else:
                            shp.fill.background()
                    stroke = d.get("color")
                    if stroke:
                        shp.line.color.rgb = RGBColor(*(int(c * 255) for c in stroke[:3]))
                        shp.line.width = Pt(max(0.25, (d.get("width") or 1) * scale))
                    elif item[0] == "re":
                        shp.line.fill.background()
                    if item[0] == "re" and shp.has_text_frame:
                        shp.text_frame.text = ""
        for it in pc.items:
            if isinstance(it, ImageItem):
                img = _image_for_office(it)
                if img is None:
                    continue
                try:
                    slide.shapes.add_picture(io.BytesIO(img[0]), *box(it.bbox))
                except Exception:
                    job.warn(_("Immagine non convertibile a pagina {p}").format(p=pc.index + 1))
            elif isinstance(it, Para) and not pc.ocr:
                b = it.bbox
                b = (b[0], b[1], b[2] + max(4.0, (b[2] - b[0]) * 0.04), b[3])  # margine contro a capo
                tb = slide.shapes.add_textbox(*box(b))
                tf = tb.text_frame
                tf.word_wrap = True
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                p = tf.paragraphs[0]
                for s in it.spans:
                    run = p.add_run()
                    run.text = s.text
                    run.font.size = Pt(max(1.0, s.size * scale))
                    run.font.bold = s.bold or None
                    run.font.italic = s.italic or None
                    if s.clean_font:
                        run.font.name = s.clean_font
                    if s.color:
                        run.font.color.rgb = RGBColor(*_rgb(s.color))
                    if s.link and not s.link.startswith("#"):
                        run.hyperlink.address = s.link
            elif isinstance(it, Para) and pc.ocr:
                # testo OCR: caselle trasparenti sopra l'immagine, modificabili
                tb = slide.shapes.add_textbox(*box(it.bbox))
                tb.text_frame.word_wrap = True
                run = tb.text_frame.paragraphs[0].add_run()
                run.text = it.text
                run.font.size = Pt(max(1.0, it.size * scale))
    prs.save(path)
