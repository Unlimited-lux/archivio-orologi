"""Conversioni verso formati testuali: TXT, RTF, HTML, XML, EPUB (senza interfaccia)."""

from __future__ import annotations

import base64
import html
import re
import uuid
import zipfile
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .common import check
from .extract import (
    ImageItem, PageContent, Para, TableItem, classify_page, clean_xml_chars, layout_text, normalize_text,
    ocr_textpage, textpage_get, SCANNED,
)
from .export_office import _image_for_office, analyze_document


# ----------------------------------------------------------------------- TXT
def write_txt(work: pymupdf.Document, path: str, job) -> None:
    """Testo semplice in ordine di lettura o con impaginazione, scritto pagina per pagina."""
    from .extract import analyze_page

    mode = job.options.get("mode", "reading")
    encoding = {"utf-8": "utf-8", "utf-8-bom": "utf-8-sig", "utf-8-sig": "utf-8-sig",
                "utf-16": "utf-16"}.get(job.options.get("encoding", "utf-8"), "utf-8")
    newline = "\r\n" if job.options.get("line_ending", "lf").lower() == "crlf" else "\n"
    sep_opt = job.options.get("page_separator", "ff")
    separator = {"ff": "\f", "none": ""}.get(sep_opt, sep_opt)
    annotations = job.options.get("annotations", False)
    n = work.page_count
    struct: dict = {}
    if mode != "layout" and job.options.get("use_structure", True):
        from .structure import structure_order
        with ENGINE_LOCK:
            try:
                struct = structure_order(work)
            except Exception:
                struct = {}
    with open(path, "w", encoding=encoding, newline="") as f:
        for i in range(n):
            check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                page = work[i]
                if mode == "layout":
                    tp = None
                    if classify_page(page) == SCANNED and job.ocr != "never":
                        tp = ocr_textpage(page, job.language)
                    text = layout_text(page, tp)
                    pc = PageContent(index=job.source_index(i), width=0, height=0, ocr=tp is not None)
                else:
                    tagged = None
                    if struct.get(page.xref):
                        from .structure import tagged_page_text
                        tagged = tagged_page_text(work, i, struct[page.xref])
                    if tagged is not None:
                        pc = PageContent(index=job.source_index(i), width=0, height=0)
                        text = tagged
                    else:
                        pc = analyze_page(page, ocr=job.ocr, language=job.language, images=False,
                                          dehyphenate=job.options.get("dehyphenate", True))
                        pc.index = job.source_index(i)
                        text = pc.text()
                if annotations:
                    notes = [a.info.get("content", "") for a in page.annots() if a.info.get("content")]
                    if notes:
                        text += "\n" + "\n".join(normalize_text(t) for t in notes)
            job.note_page(pc)
            if not text.strip():
                job.warn(_("Pagina {p} senza testo.").format(p=job.source_index(i) + 1))
            if "\ufffd" in text:
                job.warn(_("Pagina {p}: {k} caratteri non decodificabili.").format(
                    p=job.source_index(i) + 1, k=text.count("\ufffd")))
            if i:
                f.write(separator if separator in ("\f", "") else newline + separator + newline)
            f.write(text.replace("\r\n", "\n").replace("\n", newline))
            if separator != "\f":
                f.write(newline)


# ---------------------------------------------------------------------- HTML
_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;max-width:52em;margin:2em auto;padding:0 1em;color:#1b212a}
table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #999;padding:.25em .5em;vertical-align:top}
figure{margin:1em 0}img{max-width:100%;height:auto}section.page{margin-bottom:2em}
"""


def _spans_html(para: Para) -> str:
    out = []
    for s in para.spans:
        t = html.escape(s.text, quote=False)
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.link:
            t = f'<a href="{html.escape(s.link)}">{t}</a>'
        out.append(t)
    return "".join(out)


def _table_html(t: TableItem) -> str:
    rows = []
    for r, row in enumerate(t.rows):
        tag = "th" if r < t.header_rows else "td"
        cells = []
        c = 0
        while c < len(row):
            span = 1
            while c + span < len(row) and row[c + span] is None:
                span += 1
            attr = f' colspan="{span}"' if span > 1 else ""
            cells.append(f"<{tag}{attr}>{html.escape(row[c] or '')}</{tag}>")
            c += span
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def _image_src(item: ImageItem, job, assets: dict | None, name: str) -> str | None:
    img = _image_for_office(item)
    if img is None:
        return None
    data, ext = img
    ext = "jpg" if ext == "jpeg" else ext
    if assets is None:
        mime = {"jpg": "jpeg", "tif": "tiff"}.get(ext, ext)
        return f"data:image/{mime};base64," + base64.b64encode(data).decode("ascii")
    fname = f"{name}.{ext}"
    assets[fname] = data
    return fname


def items_html(pages: list[PageContent], job, assets: dict | None = None, prefix: str = "",
               skip: set | None = None) -> str:
    """Corpo HTML semantico (h1–h6, p, ul/li, table, figure) per le pagine date."""
    out: list[str] = []
    img_n = 0
    in_list = False
    for pc in pages:
        out.append(f'<section class="page" id="page-{pc.index + 1}">')
        for it in pc.items:
            if skip and id(it) in skip:
                continue
            if isinstance(it, Para) and it.kind == "li":
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                text = _spans_html(it)
                text = re.sub(r"^\s*[•\-–*]\s*", "", text)
                out.append(f"<li>{text}</li>")
                continue
            if in_list:
                out.append("</ul>")
                in_list = False
            if isinstance(it, Para):
                tag = it.kind if it.kind.startswith("h") else "p"
                out.append(f"<{tag}>{_spans_html(it)}</{tag}>")
            elif isinstance(it, TableItem):
                out.append(_table_html(it))
            elif isinstance(it, ImageItem):
                img_n += 1
                src = _image_src(it, job, assets, f"{prefix}img{pc.index + 1:03}_{img_n}")
                if src:
                    alt = html.escape(it.alt or _("Immagine a pagina {p}").format(p=pc.index + 1))
                    out.append(f'<figure><img src="{html.escape(src)}" alt="{alt}" '
                               f'width="{int(it.bbox[2] - it.bbox[0])}"/></figure>')
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append("</section>")
    return "\n".join(out)


def _doc_meta(work: pymupdf.Document, job) -> dict:
    with ENGINE_LOCK:
        meta = dict(work.metadata or {})
        lang = None
        try:
            cat = work.pdf_catalog()
            kind, val = work.xref_get_key(cat, "Lang")
            if kind == "string":
                lang = val
        except Exception:
            pass
    return {"title": meta.get("title") or job.name, "author": meta.get("author") or "",
            "lang": lang or job.options.get("lang") or "it"}


def write_html(work: pymupdf.Document, path: str, job, out) -> list[str]:
    """HTML5: «scorrevole» (struttura semantica) o «fedele» (pagine posizionate)."""
    meta = _doc_meta(work, job)
    single = job.options.get("single_file", True)
    head = (f'<!DOCTYPE html>\n<html lang="{html.escape(meta["lang"])}">\n<head>\n<meta charset="utf-8"/>\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
            f'<title>{html.escape(meta["title"])}</title>\n'
            + (f'<meta name="author" content="{html.escape(meta["author"])}"/>\n' if meta["author"] else "")
            + f"<style>{_CSS}</style>\n</head>\n<body>\n")
    created: list[str] = []
    if job.options.get("layout", "flow") == "faithful":
        parts = []
        n = work.page_count
        for i in range(n):
            check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                page_html = work[i].get_text("html", flags=pymupdf.TEXT_PRESERVE_IMAGES
                                             | pymupdf.TEXT_PRESERVE_WHITESPACE)
            page_html = re.sub(r'id="page\d+"', f'id="page-{job.source_index(i) + 1}"', page_html, count=1)
            parts.append(normalize_text(page_html))
        body = "\n".join(parts)
        style = "<style>body{background:#888;margin:0;padding:1em}div[id^=page-]{background:#fff;margin:0 auto 1em auto}</style>"
        doc = head.replace("</head>", style + "\n</head>") + body + "\n</body>\n</html>\n"
        tmp = out.new(path)
        Path(tmp).write_text(doc, encoding="utf-8")
        return [tmp]

    pages = analyze_document(work, job, phase=(0.0, 0.85))
    assets: dict | None = None if single else {}
    toc_html = ""
    if job.options.get("toc", True):
        with ENGINE_LOCK:
            toc = work.get_toc(simple=True)
        if toc:
            items = "".join(f'<li style="margin-left:{(lv - 1) * 1.2}em"><a href="#page-{job.source_index(max(0, p - 1)) + 1}">'
                            f"{html.escape(t)}</a></li>" for lv, t, p in toc if p >= 1)
            toc_html = f'<nav><h2>{html.escape(_("Sommario"))}</h2><ul>{items}</ul></nav>\n'
    check(job.progress, 0.9, _("Scrittura del file HTML…"))
    body = items_html(pages, job, assets)
    doc = head + toc_html + body + "\n</body>\n</html>\n"
    from .common import resolve_target, unique_path
    final = resolve_target(path, out.policy)       # nome definitivo deciso prima delle risorse
    tmp = out.new(final)
    if assets is not None and assets:
        folder = Path(final).with_name(Path(final).stem + "_files")
        if folder.exists() and out.policy != "overwrite":
            folder = Path(unique_path(folder))
        doc = re.sub(r'src="(img[^"]+)"', lambda m: f'src="{folder.name}/{m.group(1)}"', doc)
        for fname, data in assets.items():
            tmp_a = out.new(folder / fname)
            Path(tmp_a).write_bytes(data)
            created.append(tmp_a)
    Path(tmp).write_text(doc, encoding="utf-8")
    created.insert(0, tmp)
    return created


# ----------------------------------------------------------------------- XML
XML_NS = "urn:foliant:export:1"


def write_xml(work: pymupdf.Document, path: str, job) -> None:
    """XML documentato (schema `foliant_export.xsd`): pagine, blocchi, righe, parole."""
    from xml.sax.saxutils import quoteattr

    detail = job.options.get("detail", "words")     # blocks | lines | words
    removed = 0

    def esc(text: str) -> str:
        nonlocal removed
        text, k = clean_xml_chars(text)
        removed += k
        return html.escape(text, quote=False)

    def attr(text) -> str:
        nonlocal removed
        text, k = clean_xml_chars(str(text))
        removed += k
        return quoteattr(text)

    def bbox(b) -> str:
        return " ".join(f"{v:.2f}" for v in b)

    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<document xmlns="{XML_NS}" version="1" pages="{work.page_count}">\n')
        with ENGINE_LOCK:
            meta = dict(work.metadata or {})
            labels = [work[i].get_label() if hasattr(work[i], "get_label") else "" for i in range(work.page_count)]
            has_struct = False
            try:
                has_struct = work.xref_get_key(work.pdf_catalog(), "StructTreeRoot")[0] != "null"
            except Exception:
                pass
        f.write("  <metadata>\n")
        for k, v in meta.items():
            if v:
                f.write(f"    <item key={attr(k)} value={attr(v)}/>\n")
        f.write("  </metadata>\n")
        n = work.page_count
        for i in range(n):
            check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                page = work[i]
                tp = None
                if classify_page(page) == SCANNED and job.ocr != "never":
                    tp = ocr_textpage(page, job.language)
                raw = textpage_get(page, "rawdict", tp,
                                   pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
                annots = [(a.type[1], a.info.get("title", ""), a.info.get("modDate", ""), a.info.get("content", ""),
                           tuple(a.rect)) for a in page.annots()] if job.options.get("annotations", True) else []
                rect, rot = page.rect, page.rotation
            src = job.source_index(i)
            f.write(f'  <page number="{src + 1}" label={attr(labels[i] or src + 1)} width="{rect.width:.2f}" '
                    f'height="{rect.height:.2f}" rotation="{rot}" structure="{"tagged" if has_struct else "deduced"}"'
                    f'{" ocr=\"true\"" if tp else ""}>\n')
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    if block.get("type") == 1:
                        f.write(f'    <image bbox="{bbox(block["bbox"])}" width="{block.get("width", 0)}" '
                                f'height="{block.get("height", 0)}"/>\n')
                    continue
                f.write(f'    <block bbox="{bbox(block["bbox"])}">\n')
                for line in block.get("lines", []):
                    line_text = "".join(c["c"] for s in line["spans"] for c in s["chars"])
                    if detail == "blocks":
                        f.write(f"      {esc(normalize_text(line_text))}\n")
                        continue
                    f.write(f'      <line bbox="{bbox(line["bbox"])}"')
                    if detail == "lines":
                        f.write(f">{esc(normalize_text(line_text))}</line>\n")
                        continue
                    f.write(">\n")
                    for span in line["spans"]:
                        word, wb = "", None
                        chars = span["chars"] + [{"c": " ", "bbox": None}]
                        for ch in chars:
                            if ch["c"].isspace():
                                if word:
                                    f.write(f'        <word bbox="{bbox(wb)}" font={attr(span["font"])} '
                                            f'size="{span["size"]:.1f}" color="#{span["color"]:06x}">'
                                            f"{esc(normalize_text(word))}</word>\n")
                                word, wb = "", None
                                continue
                            word += ch["c"]
                            b = ch["bbox"]
                            wb = list(b) if wb is None else [min(wb[0], b[0]), min(wb[1], b[1]),
                                                             max(wb[2], b[2]), max(wb[3], b[3])]
                    f.write("      </line>\n")
                f.write("    </block>\n")
            for typ, author, date, content, r in annots:
                f.write(f'    <annotation type={attr(typ)} author={attr(author)} date={attr(date)} '
                        f'bbox="{bbox(r)}">{esc(content)}</annotation>\n')
            f.write("  </page>\n")
        f.write("</document>\n")
    if removed:
        job.warn(_("{k} caratteri di controllo non ammessi in XML sono stati rimossi.").format(k=removed))


XSD_PATH = Path(__file__).with_name("foliant_export.xsd")


# ----------------------------------------------------------------------- RTF
def _rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif o < 128:
            out.append(ch)
        else:
            for unit in (ch.encode("utf-16-le")[k:k + 2] for k in range(0, len(ch.encode("utf-16-le")), 2)):
                v = int.from_bytes(unit, "little")
                out.append(f"\\u{v - 65536 if v > 32767 else v}?")
    return "".join(out)


def write_rtf(work: pymupdf.Document, path: str, job) -> None:
    """RTF 1.9 con tabelle dei font e dei colori, stili, tabelle, immagini e link."""
    pages = analyze_document(work, job, phase=(0.0, 0.85))
    fonts: list[str] = ["Arial"]
    colors: list[tuple] = [(0, 0, 0)]

    def font_index(name: str) -> int:
        name = name or "Arial"
        if name not in fonts:
            fonts.append(name)
        return fonts.index(name)

    def color_index(c: int) -> int:
        rgb = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
        if rgb not in colors:
            colors.append(rgb)
        return colors.index(rgb) + 1

    body: list[str] = []
    for pc in pages:
        for it in pc.items:
            if isinstance(it, Para):
                heading = it.kind.startswith("h")
                parts = ["\\pard\\sa120"]
                if heading:
                    parts.append(f"\\outlinelevel{int(it.kind[1]) - 1}")
                for s in it.spans:
                    style = f"\\f{font_index(s.clean_font)}\\fs{int(round(s.size * 2))}\\cf{color_index(s.color)}"
                    style += "\\b" if (s.bold or heading) else "\\b0"
                    style += "\\i" if s.italic else "\\i0"
                    run = "{" + style + " " + _rtf_escape(s.text) + "}"
                    if s.link and not s.link.startswith("#"):
                        run = ('{\\field{\\*\\fldinst HYPERLINK "' + _rtf_escape(s.link) + '"}{\\fldrslt '
                               + run + "}}")
                    parts.append(run)
                parts.append("\\par\n")
                body.append("".join(parts))
            elif isinstance(it, TableItem):
                cols = max(len(r) for r in it.rows)
                width = 9000 // max(1, cols)
                for row in it.rows:
                    cells = "".join(f"\\clbrdrt\\brdrs\\clbrdrl\\brdrs\\clbrdrb\\brdrs\\clbrdrr\\brdrs\\cellx{width * (c + 1)}"
                                    for c in range(cols))
                    content = "".join("\\pard\\intbl " + _rtf_escape((row[c] if c < len(row) else "") or "") + "\\cell "
                                      for c in range(cols))
                    body.append(f"\\trowd\\trgaph108{cells}\n{content}\\row\n")
                body.append("\\pard\\par\n")
            elif isinstance(it, ImageItem):
                img = _image_for_office(it)
                if img is None:
                    continue
                data, ext = img
                if ext not in ("png", "jpeg", "jpg"):
                    try:
                        with ENGINE_LOCK:
                            data, ext = pymupdf.Pixmap(data).tobytes("png"), "png"
                    except Exception:
                        continue
                blip = "\\pngblip" if ext == "png" else "\\jpegblip"
                wg = int((it.bbox[2] - it.bbox[0]) * 20)
                hg = int((it.bbox[3] - it.bbox[1]) * 20)
                hexdata = data.hex()
                lines = "\n".join(hexdata[k:k + 128] for k in range(0, len(hexdata), 128))
                body.append(f"\\pard{{\\pict{blip}\\picw{it.width}\\pich{it.height}\\picwgoal{wg}\\pichgoal{hg}\n"
                            f"{lines}}}\\par\n")
        body.append("\\page\n" if job.options.get("page_breaks", False) else "")
    fonttbl = "{\\fonttbl" + "".join(f"{{\\f{i}\\fnil\\fcharset0 {_rtf_escape(n)};}}" for i, n in enumerate(fonts)) + "}"
    colortbl = "{\\colortbl;" + "".join(f"\\red{r}\\green{g}\\blue{b};" for r, g, b in colors) + "}"
    w = int(pages[0].width * 20) if pages else 11906
    h = int(pages[0].height * 20) if pages else 16838
    doc = ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1\n" + fonttbl + "\n" + colortbl + "\n"
           f"\\paperw{w}\\paperh{h}\\margl1134\\margr1134\\margt1134\\margb1134\n" + "".join(body) + "}\n")
    Path(path).write_text(doc, encoding="ascii", errors="replace")


# ---------------------------------------------------------------------- EPUB
def _repeated_furniture(pages: list[PageContent]) -> set[int]:
    """Paragrafi ripetuti in testa o a piè di pagina (con i numeri resi generici)."""
    if len(pages) < 2:
        return set()
    counts: dict[str, int] = {}
    where: dict[str, list] = {}
    for pc in pages:
        seen = set()
        for it in pc.items:
            if not isinstance(it, Para):
                continue
            top, bottom = it.bbox[1], it.bbox[3]
            if top > pc.height * 0.12 and bottom < pc.height * 0.88:
                continue
            key = re.sub(r"\d+", "#", it.text.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
            where.setdefault(key, []).append(it)
    limit = max(2, int(len(pages) * 0.5))
    return {id(it) for key, n in counts.items() if n >= limit for it in where[key]}


def write_epub(work: pymupdf.Document, path: str, job) -> None:
    """EPUB 3: scorrevole (capitoli XHTML) o a layout fisso (una pagina XHTML per pagina).

    Il contenitore è scritto man mano (le immagini di pagina non restano in memoria)."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        _write_epub(work, job, z)


def _write_epub(work: pymupdf.Document, job, z: zipfile.ZipFile) -> None:
    meta = _doc_meta(work, job)
    fixed = job.options.get("layout", "flow") == "fixed" or job.options.get("layout") == "faithful"
    every = int(job.options.get("chapter_every", 10)) or 10
    book_id = "urn:uuid:" + str(uuid.uuid4())
    manifest: list[tuple[str, str, str, str]] = []   # id, href, media-type, properties
    spine: list[str] = []
    nav_items: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}

    def xhtml(title: str, body: str, extra_head: str = "") -> bytes:
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
                f'lang="{html.escape(meta["lang"])}" xml:lang="{html.escape(meta["lang"])}">\n<head>\n'
                f'<meta charset="utf-8"/>\n<title>{html.escape(title)}</title>\n'
                f'<link rel="stylesheet" type="text/css" href="style.css"/>\n{extra_head}</head>\n<body>\n'
                f"{body}\n</body>\n</html>\n").encode("utf-8")

    files["OEBPS/style.css"] = (_CSS.replace("max-width:52em;margin:2em auto;", "")
                                + "\nbody.fixed{margin:0;padding:0}img.page{width:100%;height:100%}").encode()
    manifest.append(("css", "style.css", "text/css", ""))
    # copertina: pagina 1 resa come immagine
    with ENGINE_LOCK:
        cover = work[0].get_pixmap(dpi=96).tobytes("png")
    files["OEBPS/cover.png"] = cover
    manifest.append(("cover-image", "cover.png", "image/png", "cover-image"))

    if fixed:
        n = work.page_count
        with ENGINE_LOCK:
            w, h = int(work[0].rect.width), int(work[0].rect.height)
        for i in range(n):
            check(job.progress, i / max(1, n), _("Pagina {i} di {n}").format(i=i + 1, n=n))
            with ENGINE_LOCK:
                page = work[i]
                pw, ph = int(page.rect.width), int(page.rect.height)
                png = page.get_pixmap(dpi=job.options.get("dpi", 150)).tobytes("png")
                text = normalize_text(page.get_text("text"))
            img = f"page{i + 1:04}.png"
            z.writestr(f"OEBPS/{img}", png, compress_type=zipfile.ZIP_STORED)   # subito su disco
            manifest.append((f"img{i + 1}", img, "image/png", ""))
            doc_name = f"page{i + 1:04}.xhtml"
            alt = html.escape(clean_xml_chars(text)[0][:500].replace("\n", " "), quote=True)
            files[f"OEBPS/{doc_name}"] = xhtml(
                _("Pagina {p}").format(p=i + 1),
                f'<div class="fixed"><img class="page" src="{img}" alt="{alt}"/></div>',
                f'<meta name="viewport" content="width={pw}, height={ph}"/>\n')
            manifest.append((f"p{i + 1}", doc_name, "application/xhtml+xml", ""))
            spine.append(f"p{i + 1}")
            if i == 0:
                nav_items.append((meta["title"], doc_name))
        layout_meta = ('<meta property="rendition:layout">pre-paginated</meta>\n'
                       '<meta property="rendition:spread">auto</meta>\n')
        _w = (w, h)
    else:
        pages = analyze_document(work, job, phase=(0.0, 0.8))
        skip = _repeated_furniture(pages) if job.options.get("remove_furniture", True) else set()
        with ENGINE_LOCK:
            toc = [(lv, t, p) for lv, t, p in work.get_toc(simple=True) if lv == 1 and p >= 1]
        # inizio dei capitoli (indice nella lista delle pagine)
        starts: list[tuple[int, str]] = []
        if toc:
            for _lv, title, p in toc:
                starts.append((p - 1, title))
        else:
            heads = [(k, it.text) for k, pc in enumerate(pages) for it in pc.items
                     if isinstance(it, Para) and it.kind == "h1"]
            seen_pages = set()
            for k, title in heads:
                if k not in seen_pages:
                    starts.append((k, title))
                    seen_pages.add(k)
            if not starts:
                job.warn(_("Nessun segnalibro né titolo: capitoli ogni {n} pagine.").format(n=every))
                starts = [(k, _("Pagine {a}-{b}").format(a=k + 1, b=min(len(pages), k + every)))
                          for k in range(0, len(pages), every)]
        starts = sorted({k: t for k, t in starts}.items())
        if not starts or starts[0][0] != 0:
            starts.insert(0, (0, meta["title"]))
        assets: dict = {}
        for c, (start, title) in enumerate(starts):
            end = starts[c + 1][0] if c + 1 < len(starts) else len(pages)
            chunk = pages[start:end] if end > start else []
            body = items_html(chunk, job, assets, prefix=f"c{c + 1}_", skip=skip)
            body = re.sub(r'<section class="page" id="page-(\d+)">', r'<section class="page" id="page-\1">', body)
            doc_name = f"chapter{c + 1:03}.xhtml"
            files[f"OEBPS/{doc_name}"] = xhtml(title, body)
            manifest.append((f"c{c + 1}", doc_name, "application/xhtml+xml", ""))
            spine.append(f"c{c + 1}")
            nav_items.append((title, doc_name))
        for k, (fname, data) in enumerate(assets.items()):
            ext = fname.rsplit(".", 1)[-1]
            mime = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "bmp": "image/bmp",
                    "tif": "image/tiff", "tiff": "image/tiff"}.get(ext, "image/png")
            files[f"OEBPS/{fname}"] = data
            manifest.append((f"a{k + 1}", fname, mime, ""))
        # i link interni puntano a «#page-N»: vanno riferiti al capitolo che la contiene
        page_doc = {}
        for c, (start, _t) in enumerate(starts):
            end = starts[c + 1][0] if c + 1 < len(starts) else len(pages)
            for pc in pages[start:end]:
                page_doc[pc.index + 1] = f"chapter{c + 1:03}.xhtml"
        for key in [k for k in files if k.startswith("OEBPS/chapter")]:
            text = files[key].decode("utf-8")
            text = re.sub(r'href="#page-(\d+)"', lambda m: f'href="{page_doc.get(int(m.group(1)), "")}#page-{m.group(1)}"', text)
            files[key] = text.encode("utf-8")
        layout_meta = ""
    check(job.progress, 0.95, _("Scrittura dell'EPUB…"))
    nav = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
           f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(meta["lang"])}" xml:lang="{html.escape(meta["lang"])}">\n'
           f'<head><meta charset="utf-8"/><title>{html.escape(meta["title"])}</title></head>\n<body>\n'
           f'<nav epub:type="toc" id="toc"><h1>{html.escape(_("Sommario"))}</h1>\n<ol>\n'
           + "".join(f'<li><a href="{href}">{html.escape(t)}</a></li>\n' for t, href in nav_items)
           + "</ol>\n</nav>\n</body>\n</html>\n")
    files["OEBPS/nav.xhtml"] = nav.encode("utf-8")
    manifest.append(("nav", "nav.xhtml", "application/xhtml+xml", "nav"))
    import datetime as _dt
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" '
           'prefix="rendition: http://www.idpf.org/vocab/rendition/#">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n'
           f"<dc:title>{html.escape(meta['title'])}</dc:title>\n"
           f"<dc:language>{html.escape(meta['lang'])}</dc:language>\n"
           + (f"<dc:creator>{html.escape(meta['author'])}</dc:creator>\n" if meta["author"] else "")
           + f'<meta property="dcterms:modified">{modified}</meta>\n<meta name="cover" content="cover-image"/>\n'
           + layout_meta + "</metadata>\n<manifest>\n"
           + "".join(f'<item id="{i}" href="{h}" media-type="{m}"' + (f' properties="{p}"' if p else "") + "/>\n"
                     for i, h, m, p in manifest)
           + "</manifest>\n<spine>\n" + "".join(f'<itemref idref="{s}"/>\n' for s in spine) + "</spine>\n</package>\n")
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n<rootfiles>\n'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                 "</rootfiles>\n</container>\n")
    z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
    for name, data in files.items():
        z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
