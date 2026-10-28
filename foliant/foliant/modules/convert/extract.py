"""Analisi delle pagine per le conversioni «scorrevoli» (senza interfaccia).

Ricava da ogni pagina un elenco ordinato di elementi (paragrafi con stile,
tabelle, immagini) in ordine di lettura: colonne riconosciute con un taglio
ricorsivo X-Y, tabelle con `find_tables()`, titoli dalla gerarchia dei corpi.
Le pagine scansionate senza testo passano dall'OCR (CNV-02) se richiesto.

Tutte le funzioni che toccano il motore vanno chiamate tenendo ENGINE_LOCK.
"""

from __future__ import annotations

import collections
import re
import shutil
import unicodedata
from dataclasses import dataclass, field

import pymupdf

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
}
_LIG_TABLE = str.maketrans(LIGATURES)

# Estrazione: legature espanse, spazi conservati, immagini incluse a richiesta.
TEXT_FLAGS = (pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)


def normalize_text(text: str) -> str:
    """Espande le legature e normalizza in NFC (U+FB01 → «fi»)."""
    return unicodedata.normalize("NFC", text.translate(_LIG_TABLE))


def clean_xml_chars(text: str) -> tuple[str, int]:
    """Rimuove i caratteri di controllo non ammessi in XML 1.0; restituisce (testo, rimossi)."""
    out = []
    removed = 0
    for ch in text:
        o = ord(ch)
        if o in (9, 10, 13) or 0x20 <= o <= 0xD7FF or 0xE000 <= o <= 0xFFFD or 0x10000 <= o <= 0x10FFFF:
            out.append(ch)
        else:
            removed += 1
    return "".join(out), removed


# ------------------------------------------------------------------ modello
@dataclass
class Span:
    text: str
    size: float = 11.0
    font: str = ""
    bold: bool = False
    italic: bool = False
    color: int = 0
    link: str | None = None      # URL esterno o "#page-N" per i link interni

    @property
    def clean_font(self) -> str:
        """Nome del font senza il prefisso di subset («ABCDEF+»)."""
        name = self.font
        if len(name) > 7 and name[6] == "+" and name[:6].isupper():
            name = name[7:]
        return name


@dataclass
class Para:
    spans: list[Span]
    bbox: tuple
    kind: str = "p"              # "p", "h1".."h6", "li"
    align: str = "left"
    angle: int = 0               # rotazione del testo come visualizzato (gradi, antiorari)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        sizes = [(len(s.text), s.size) for s in self.spans if s.text.strip()]
        return max(sizes)[1] if sizes else 11.0


@dataclass
class TableItem:
    rows: list[list[str]]
    bbox: tuple
    header_rows: int = 1


@dataclass
class ImageItem:
    data: bytes
    ext: str
    width: int
    height: int
    bbox: tuple
    alt: str = ""


@dataclass
class PageContent:
    index: int                    # indice nel documento sorgente
    width: float
    height: float
    items: list = field(default_factory=list)
    ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    def text(self, table_sep: str = "\t") -> str:
        parts = []
        for it in self.items:
            if isinstance(it, Para):
                parts.append(it.text)
            elif isinstance(it, TableItem):
                parts.append("\n".join(table_sep.join(c or "" for c in r) for r in it.rows))
        return "\n".join(parts)


# ------------------------------------------------------------ classificazione
NATIVE, SCANNED, EMPTY = "nativa", "scansionata", "vuota"


def classify_page(page: pymupdf.Page) -> str:
    """«nativa» (testo estraibile), «scansionata» (nessun testo e immagine ≥ 80%) o «vuota»."""
    if page.get_text("text").strip():
        return NATIVE
    area = abs(page.rect)
    covered = 0.0
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            covered = max(covered, abs(r))
    except Exception:
        pass
    if area and covered / area >= 0.8:
        return SCANNED
    return EMPTY


def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        return bool(pymupdf.get_tessdata())
    except Exception:
        return False


def ocr_languages(preferred: str | None = None) -> str:
    """Lingue OCR installate, nel formato «ita+eng» (default: italiano e inglese se presenti)."""
    import os
    try:
        tessdata = pymupdf.get_tessdata()
        installed = {f[:-12] for f in os.listdir(tessdata) if f.endswith(".traineddata")}
    except Exception:
        installed = set()
    wanted = (preferred or "ita+eng").split("+")
    langs = [lang for lang in wanted if lang in installed] or [lang for lang in ("eng",) if lang in installed]
    return "+".join(langs) or "eng"


def detect_rotation(png: bytes) -> int:
    """Rotazione (0, 90, 180, 270) che raddrizza la pagina, stimata da Tesseract (OSD)."""
    import re
    import subprocess
    try:
        res = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "0"], input=png, capture_output=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 0
    text = (res.stdout or b"").decode(errors="replace")
    m = re.search(r"Rotate:\s*(\d+)", text)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", text)
    if not m or (conf and float(conf.group(1)) < 1.0):
        return 0
    return int(m.group(1)) % 360


_OCR_HOLD: list = []   # documenti temporanei delle pagine raddrizzate (vivi finché serve la TextPage)


def ocr_textpage(page: pymupdf.Page, language: str | None = None, dpi: int = 300):
    """TextPage riconosciuta con OCR per una pagina scansionata (None se impossibile).

    Se la scansione è ruotata (90/180/270°) l'immagine viene raddrizzata prima
    del riconoscimento; le coordinate restano quelle della pagina raddrizzata.
    """
    if not ocr_available():
        return None
    try:
        lang = ocr_languages(language)
        from PIL import Image

        from .imaging import estimate_skew
        probe = page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY)
        rotation = detect_rotation(probe.tobytes("png"))
        probe_img = Image.frombytes("L", (probe.width, probe.height), probe.samples).rotate(-rotation, expand=True)
        skew = estimate_skew(probe_img)
        if abs(skew) < 0.3:
            skew = 0.0
        if rotation or skew:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples).rotate(-rotation, expand=True)
            if skew:
                img = img.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=255)
            tmp = pymupdf.open()
            w, h = img.width * 72 / dpi, img.height * 72 / dpi
            tp_page = tmp.new_page(width=w, height=h)
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            tp_page.insert_image(tp_page.rect, stream=buf.getvalue())
            _OCR_HOLD[:] = [tmp, tp_page]
            return tp_page.get_textpage_ocr(flags=TEXT_FLAGS, language=lang, dpi=dpi, full=True)
        return page.get_textpage_ocr(flags=TEXT_FLAGS, language=lang, dpi=dpi, full=True)
    except Exception:
        return None


def textpage_get(page: pymupdf.Page, kind: str, textpage=None, flags: int | None = None):
    """Come `page.get_text(kind)`, ma accetta anche la TextPage OCR di una pagina raddrizzata."""
    if textpage is None:
        return page.get_text(kind, flags=flags) if flags is not None else page.get_text(kind)
    return {"dict": textpage.extractDICT, "rawdict": textpage.extractRAWDICT, "words": textpage.extractWORDS,
            "text": textpage.extractText}[kind]()


# ------------------------------------------------------------- ordine lettura
def _xy_cut(boxes: list[tuple[tuple, object]], min_gap: float = 6.0) -> list[object]:
    """Ordina gli elementi (bbox, obj) in ordine di lettura con un taglio X-Y ricorsivo.

    Prima si cerca una colonna (taglio verticale libero da elementi), poi una
    fascia orizzontale in alto; senza tagli possibili si ordina per riga.
    """
    if len(boxes) <= 1:
        return [o for _b, o in boxes]
    # taglio verticale: intervalli x coperti
    spans = sorted((b[0], b[2]) for b, _o in boxes)
    reach = spans[0][1]
    for x0, x1 in spans[1:]:
        if x0 - reach >= min_gap:
            cut = (reach + x0) / 2
            left = [(b, o) for b, o in boxes if (b[0] + b[2]) / 2 < cut]
            right = [(b, o) for b, o in boxes if (b[0] + b[2]) / 2 >= cut]
            if left and right:
                return _xy_cut(left, min_gap) + _xy_cut(right, min_gap)
        reach = max(reach, x1)
    # taglio orizzontale: prima fascia libera dall'alto
    spans = sorted((b[1], b[3]) for b, _o in boxes)
    reach = spans[0][1]
    for y0, y1 in spans[1:]:
        if y0 - reach > 0.5:
            cut = (reach + y0) / 2
            top = [(b, o) for b, o in boxes if (b[1] + b[3]) / 2 < cut]
            rest = [(b, o) for b, o in boxes if (b[1] + b[3]) / 2 >= cut]
            if top and rest:
                return _xy_cut(top, min_gap) + _xy_cut(rest, min_gap)
        reach = max(reach, y1)
    return [o for _b, o in sorted(boxes, key=lambda t: (round(t[0][1]), t[0][0]))]


class _NoTables:
    tables: list = []


def find_tables(page: pymupdf.Page, strategy: str | None = None):
    """`page.find_tables` senza il modello di layout facoltativo (lento: ~0,15 s per pagina).

    Con la strategia a linee le pagine senza grafica vettoriale non possono avere
    tabelle bordate e vengono saltate subito."""
    kwargs = {"strategy": strategy} if strategy and strategy != "lines" else {}
    if not kwargs:
        try:
            if not page.get_cdrawings():
                return _NoTables()
        except Exception:
            pass
    # PyMuPDF cerca pymupdf.layout per suggerirne l'installazione: con l'esclusione del nucleo
    # (NFR-19) quella ricerca solleva ModuleNotFoundError e find_tables fallirebbe
    pymupdf._recommend_layout = False
    try:
        return page.find_tables(use_layout=False, **kwargs)
    except TypeError:
        return page.find_tables(**kwargs)


def image_backed(page: pymupdf.Page) -> bool:
    """Pagina il cui contenuto è un'immagine a tutta pagina (scansione, anche con testo OCR)."""
    try:
        area = abs(page.rect)
        return any(abs(pymupdf.Rect(i["bbox"]) & page.rect) >= 0.8 * area for i in page.get_image_info())
    except Exception:
        return False


def _cell_item(spans: list[dict], block: dict) -> dict:
    x0 = min(sp["bbox"][0] for sp in spans)
    y0 = min(sp["bbox"][1] for sp in spans)
    x1 = max(sp["bbox"][2] for sp in spans)
    y1 = max(sp["bbox"][3] for sp in spans)
    text = " ".join(" ".join(normalize_text(sp["text"]).split()) for sp in spans)
    return {"bbox": (x0, y0, x1, y1), "cy": (y0 + y1) / 2, "text": text, "block": block}


def grid_tables(blocks: list[dict], min_rows: int = 2, min_cols: int = 2, tol: float = 10.0) -> list[tuple]:
    """Tabelle ricostruite dall'allineamento dei blocchi di testo (celle separate, senza linee).

    Restituisce [(bbox, righe, blocchi usati)]: sequenze di righe consecutive con lo
    stesso numero di celle allineate in colonna sugli stessi bordi sinistri."""
    # celle: segmenti di testo di una riga separati da uno spazio ampio (indipendente da come
    # l'OCR raggruppa righe e blocchi)
    items = []
    for b in blocks:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = sorted((sp for sp in ln.get("spans", []) if sp.get("text", "").strip() and sp.get("bbox")),
                           key=lambda sp: sp["bbox"][0])
            cell: list[dict] = []
            for sp in spans:
                if cell:
                    gap = sp["bbox"][0] - cell[-1]["bbox"][2]
                    if gap > max(6.0, 0.9 * float(sp.get("size", 10))):
                        items.append(_cell_item(cell, b))
                        cell = []
                cell.append(sp)
            if cell:
                items.append(_cell_item(cell, b))
    rows: list[list[dict]] = []
    for it in sorted(items, key=lambda i: (i["cy"], i["bbox"][0])):
        h = it["bbox"][3] - it["bbox"][1]
        if rows and abs(rows[-1][0]["cy"] - it["cy"]) <= max(3.0, h * 0.45):
            rows[-1].append(it)
        else:
            rows.append([it])
    for r in rows:
        r.sort(key=lambda i: i["bbox"][0])
    found = []
    k = 0
    while k < len(rows):
        first = rows[k]
        if len(first) < min_cols:
            k += 1
            continue
        # colonne: intervalli orizzontali delle celle della prima riga; una cella appartiene alla
        # colonna se ne condivide il bordo sinistro o se si sovrappone all'intervallo
        cols = [[i["bbox"][0], i["bbox"][2]] for i in first]

        def fits(cell, col) -> bool:
            x0, x1 = cell["bbox"][0], cell["bbox"][2]
            return abs(x0 - col[0]) <= tol or (max(x0, col[0]) < min(x1, col[1]) + tol)

        def separated(row) -> bool:      # celle in colonne distinte e nello stesso ordine
            return all(fits(c, col) for c, col in zip(row, cols)) and \
                all(row[n]["bbox"][0] >= cols[n - 1][1] - tol for n in range(1, len(row)))

        run = [first]
        j = k + 1
        while j < len(rows) and len(rows[j]) == len(cols) and separated(rows[j]):
            for c, col in zip(rows[j], cols):
                col[0], col[1] = min(col[0], c["bbox"][0]), max(col[1], c["bbox"][2])
            run.append(rows[j])
            j += 1
        if len(run) >= min_rows:
            used = [i for r in run for i in r]
            bbox = (min(i["bbox"][0] for i in used), min(i["bbox"][1] for i in used),
                    max(i["bbox"][2] for i in used), max(i["bbox"][3] for i in used))
            found.append((bbox, [[i["text"] for i in r] for r in run], [i["block"] for i in used]))
            k = j
        else:
            k += 1
    return found


def table_rows(page: pymupdf.Page, table, words: list | None = None) -> list[list[str | None]]:
    """Testo delle celle di una tabella ricavato dalle parole della pagina.

    Più robusto dell'estrazione interna (punteggiatura e righe multiple nella
    cella): le parole il cui centro cade nella cella, righe separate da «\\n».
    Le celle coperte da una cella unita valgono None.
    """
    if words is None:
        words = page.get_text("words", flags=TEXT_FLAGS)
    out: list[list[str | None]] = []
    rows = list(table.rows)
    ncols = max((len(r.cells) for r in rows), default=0)
    # colonne e righe degeneri (bordi doppi ravvicinati) non sono celle reali
    thin_cols = {c for c in range(ncols)
                 if all(r.cells[c] is None or r.cells[c][2] - r.cells[c][0] < 3 for r in rows if c < len(r.cells))}
    for row in rows:
        if row.bbox and row.bbox[3] - row.bbox[1] < 3:
            continue
        cells: list[str | None] = []
        for ci, cell in enumerate(row.cells):
            if ci in thin_cols:
                continue
            if cell is None:
                cells.append(None)
                continue
            x0, y0, x1, y1 = cell
            inside = [w for w in words if x0 - 1 <= (w[0] + w[2]) / 2 <= x1 + 1 and y0 - 1 <= (w[1] + w[3]) / 2 <= y1 + 1]
            inside.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
            lines: list[list] = []
            for w in inside:
                cy = (w[1] + w[3]) / 2
                if lines and abs(lines[-1][0] - cy) <= max(2.0, (w[3] - w[1]) * 0.5):
                    lines[-1][1].append(w)
                else:
                    lines.append([cy, [w]])
            text = "\n".join(" ".join(normalize_text(w[4]) for w in sorted(ws, key=lambda w: w[0]))
                             for _cy, ws in lines)
            cells.append(text)
        out.append(cells)
    return out


def _inside(inner: tuple, outer: tuple, tol: float = 2.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - tol <= cx <= outer[2] + tol and outer[1] - tol <= cy <= outer[3] + tol


# ------------------------------------------------------------ analisi pagina
def _page_links(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    try:
        for link in page.get_links():
            rect = pymupdf.Rect(link.get("from"))
            if link.get("kind") == pymupdf.LINK_URI and link.get("uri"):
                out.append((rect, link["uri"]))
            elif link.get("kind") == pymupdf.LINK_GOTO and link.get("page", -1) >= 0:
                out.append((rect, f"#page-{link['page'] + 1}"))
    except Exception:
        pass
    return out


def _span_link(bbox, links) -> str | None:
    r = pymupdf.Rect(bbox)
    for rect, target in links:
        inter = r & rect
        if not inter.is_empty and abs(inter) >= 0.5 * abs(r):
            return target
    return None


def _merge_spans(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if out and (out[-1].bold, out[-1].italic, out[-1].link, round(out[-1].size), out[-1].color) == \
                (s.bold, s.italic, s.link, round(s.size), s.color):
            out[-1].text += s.text
        else:
            out.append(Span(s.text, s.size, s.font, s.bold, s.italic, s.color, s.link))
    return out


def _block_to_para(block: dict, links, dehyphenate: bool = True) -> Para | None:
    spans: list[Span] = []
    lines = block.get("lines", [])
    for li, line in enumerate(lines):
        line_spans = []
        for sp in line.get("spans", []):
            text = normalize_text(sp.get("text", ""))
            if not text:
                continue
            flags = sp.get("flags", 0)
            font = sp.get("font", "")
            low = font.lower()
            line_spans.append(Span(
                text=text, size=float(sp.get("size", 11)), font=font,
                bold=bool(flags & 16) or "bold" in low or "black" in low or "heavy" in low,
                italic=bool(flags & 2) or "italic" in low or "oblique" in low,
                color=int(sp.get("color", 0)), link=_span_link(sp.get("bbox"), links)))
        if not line_spans:
            continue
        if spans:
            prev = spans[-1]
            first = line_spans[0]
            if dehyphenate and prev.text.endswith("-") and first.text[:1].islower():
                prev.text = prev.text[:-1]
            elif not prev.text.endswith((" ", " ")) and not first.text.startswith(" "):
                prev.text += " "
        spans.extend(line_spans)
        _ = li
    spans = _merge_spans(spans)
    if not spans or not "".join(s.text for s in spans).strip():
        return None
    # rimuove spazi finali/iniziali
    spans[0].text = spans[0].text.lstrip()
    spans[-1].text = spans[-1].text.rstrip()
    return Para(spans=spans, bbox=tuple(block["bbox"]), angle=_line_angle(lines))


def _line_angle(lines: list) -> int:
    """Angolo prevalente delle righe del blocco, arrotondato a 90° (0 se il testo non è
    chiaramente ruotato)."""
    import math
    votes: collections.Counter = collections.Counter()
    for line in lines:
        dx, dy = line.get("dir", (1, 0))
        a = math.degrees(math.atan2(-dy, dx)) % 360
        snapped = int(round(a / 90) * 90) % 360
        if abs(((a - snapped) + 180) % 360 - 180) <= 10:
            votes[snapped] += len("".join(sp.get("text", "") for sp in line.get("spans", [])))
    return votes.most_common(1)[0][0] if votes else 0


def analyze_page(page: pymupdf.Page, *, ocr: str = "auto", language: str | None = None,
                 images: bool = True, tables: bool = True, dehyphenate: bool = True) -> PageContent:
    """Contenuto della pagina in ordine di lettura (paragrafi, tabelle, immagini)."""
    content = PageContent(index=page.number, width=page.rect.width, height=page.rect.height)
    kind = classify_page(page)
    textpage = None
    if kind == SCANNED and ocr in ("auto", "always") or (ocr == "always" and kind != NATIVE):
        textpage = ocr_textpage(page, language)
        if textpage is not None:
            content.ocr = True
        else:
            content.warnings.append("ocr-non-disponibile")
    elif kind == SCANNED:
        content.warnings.append("scansione-senza-ocr")
    flags = TEXT_FLAGS | (pymupdf.TEXT_PRESERVE_IMAGES if images else 0)
    if dehyphenate:
        flags |= getattr(pymupdf, "TEXT_DEHYPHENATE", 0) * 0   # gestita a mano (conserva gli stili)
    try:
        data = textpage_get(page, "dict", textpage, flags)
    except Exception:
        data = {"blocks": []}
    links = _page_links(page)

    table_items: list[TableItem] = []
    if tables and textpage is None:
        try:
            found = find_tables(page)
            words = page.get_text("words", flags=TEXT_FLAGS) if found.tables else []
            for t in found.tables:
                rows = [[(c or "").strip() for c in row] for row in table_rows(page, t, words)]
                rows = [r for r in rows if any(c for c in r)]
                if len(rows) >= 1 and max(len(r) for r in rows) >= 2:
                    table_items.append(TableItem(rows=rows, bbox=tuple(t.bbox)))
        except Exception:
            pass

    if tables and not table_items and textpage is None and image_backed(page):
        # scansione con testo riconosciuto: tabelle dalle celle allineate
        for bbox, rows, _used in grid_tables(data.get("blocks", [])):
            table_items.append(TableItem(rows=rows, bbox=bbox))
    boxes: list[tuple[tuple, object]] = [(t.bbox, t) for t in table_items]
    for block in data.get("blocks", []):
        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
        if any(_inside(bbox, t.bbox) for t in table_items):
            continue
        if block.get("type") == 1:
            if not images:
                continue
            img = block.get("image")
            if not img:
                continue
            if textpage is not None:
                continue   # pagina scansionata: l'immagine è la pagina stessa
            boxes.append((bbox, ImageItem(data=img, ext=block.get("ext", "png"), width=block.get("width", 0),
                                          height=block.get("height", 0), bbox=bbox)))
        elif block.get("type") == 0:
            para = _block_to_para(block, links, dehyphenate)
            if para is not None:
                boxes.append((bbox, para))
    if page.rotation and textpage is None:
        # coordinate della pagina come visualizzata (ruotata), coerenti con width/height
        m = page.rotation_matrix
        rotated = []
        for bbox, item in boxes:
            r = tuple(pymupdf.Rect(bbox) * m)
            item.bbox = r
            if isinstance(item, Para):
                item.angle = (item.angle - page.rotation) % 360
            rotated.append((r, item))
        boxes = rotated
    content.items = _xy_cut(_upright(boxes))
    return content


def _upright(boxes: list) -> list:
    """Se il testo della pagina è in prevalenza ruotato, l'ordine di lettura si calcola sulle
    coordinate raddrizzate (le righe di un testo verticale si leggono da destra a sinistra o
    viceversa); i riquadri degli elementi restano quelli della pagina."""
    votes: collections.Counter = collections.Counter()
    for _b, it in boxes:
        if isinstance(it, Para):
            votes[it.angle] += len(it.text)
    angle = votes.most_common(1)[0][0] if votes else 0
    if not angle or votes[angle] < 0.5 * sum(votes.values()):
        return boxes
    m = pymupdf.Matrix(angle)
    return [(tuple(pymupdf.Rect(b) * m), it) for b, it in boxes]


def assign_headings(pages: list[PageContent], max_levels: int = 3) -> None:
    """Titoli dalla gerarchia dei corpi: i corpi chiaramente maggiori del testo
    prevalente diventano h1, h2, h3 (paragrafi brevi)."""
    counter: collections.Counter = collections.Counter()
    for pc in pages:
        for it in pc.items:
            if isinstance(it, Para):
                for s in it.spans:
                    counter[round(s.size)] += len(s.text)
    if not counter:
        return
    body = counter.most_common(1)[0][0]
    big = sorted({round(it.size) for pc in pages for it in pc.items
                  if isinstance(it, Para) and round(it.size) >= body * 1.15 + 0.5 and len(it.text) < 200},
                 reverse=True)[:max_levels]
    level = {size: i + 1 for i, size in enumerate(big)}
    for pc in pages:
        for it in pc.items:
            if isinstance(it, Para):
                lv = level.get(round(it.size))
                if lv and len(it.text) < 200:
                    it.kind = f"h{lv}"
                elif it.text.lstrip()[:2] in ("• ", "- ", "– ", "* ") or it.text.lstrip()[:1] == "•":
                    it.kind = "li"


def page_plain_text(page: pymupdf.Page, *, mode: str = "reading", ocr: str = "auto", language=None,
                    dehyphenate: bool = True) -> tuple[str, bool]:
    """Testo della pagina: «reading» (ordine di lettura, tabelle con tabulazioni) o «layout»."""
    if mode == "layout":
        kind = classify_page(page)
        tp = ocr_textpage(page, language) if (kind == SCANNED and ocr != "never") else None
        return layout_text(page, tp), tp is not None
    pc = analyze_page(page, ocr=ocr, language=language, images=False, dehyphenate=dehyphenate)
    return pc.text(), pc.ocr


def layout_text(page: pymupdf.Page, textpage=None) -> str:
    """Riproduce l'impaginazione con spazi (colonne allineate)."""
    words = textpage_get(page, "words", textpage)
    if not words:
        return ""
    sizes = [(w[3] - w[1]) for w in words]
    line_h = max(4.0, sorted(sizes)[len(sizes) // 2] * 0.6)
    char_w = max(2.0, sorted(sizes)[len(sizes) // 2] * 0.5)
    rows: list[list] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - cy) <= line_h:
            rows[-1][1].append(w)
        else:
            rows.append([cy, [w]])
    out = []
    x_origin = min(w[0] for w in words)
    for _cy, ws in rows:
        line = ""
        prev = None
        for w in sorted(ws, key=lambda w: w[0]):
            col = int(round((w[0] - x_origin) / char_w))
            # parole vicine nella stessa riga: un solo spazio; distacchi ampi: allineamento a colonna
            own_w = (prev[2] - prev[0]) / max(1, len(prev[4])) if prev is not None else char_w
            if prev is not None and w[0] - prev[2] <= 2.5 * own_w:
                line += " "
            elif len(line) < col:
                line += " " * (col - len(line))
            elif line and not line.endswith(" "):
                line += " "
            line += normalize_text(w[4])
            prev = w
        out.append(line.rstrip())
    return "\n".join(out)


def repeated_furniture(pages: list[PageContent]) -> set[int]:
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
            # solo paragrafi contenuti per intero nella fascia alta o bassa: un paragrafo che
            # occupa la pagina (stesso testo su ogni pagina) non è un'intestazione
            if bottom > pc.height * 0.15 and top < pc.height * 0.85:
                continue
            key = re.sub(r"\d+", "#", it.text.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
            where.setdefault(key, []).append(it)
    limit = max(2, int(len(pages) * 0.5))
    return {id(it) for key, n in counts.items() if n >= limit for it in where[key]}


def add_ocr_layer(data: bytes, language: str | None = None, progress=None, dpi: int = 300) -> tuple[bytes, list[int]]:
    """Aggiunge un livello di testo invisibile (OCR) alle pagine senza testo, lasciando le
    immagini originali intatte. Restituisce (byte, pagine riconosciute in base 1)."""
    from foliant.api import ENGINE_LOCK, _

    from .common import check
    if not ocr_available():
        return data, []
    done: list[int] = []
    with ENGINE_LOCK:
        doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        n = doc.page_count
        for i in range(n):
            check(progress, i / max(1, n), _("OCR della pagina {p}").format(p=i + 1))
            with ENGINE_LOCK:
                page = doc[i]
                if page.get_text("text").strip():
                    continue
                rot = page.rotation
                page.set_rotation(0)
                pix = page.get_pixmap(dpi=dpi)
            ocr_bytes = pix.pdfocr_tobytes(language=ocr_languages(language))
            with ENGINE_LOCK:
                ocr = pymupdf.open(stream=ocr_bytes, filetype="pdf")
                try:
                    # resta solo il testo invisibile: via il disegno dell'immagine e la sua risorsa
                    op = ocr[0]
                    content = re.sub(rb"/[^\s/]+\s+Do\b", b"", op.read_contents())
                    xrefs = op.get_contents()
                    ocr.update_stream(xrefs[0], content)
                    for x in xrefs[1:]:
                        ocr.update_stream(x, b"")
                    ocr.xref_set_key(op.xref, "Resources/XObject", "<<>>")
                    page.show_pdf_page(page.rect, ocr, 0, overlay=True)
                finally:
                    ocr.close()
                page.set_rotation(rot)
            done.append(i + 1)
        with ENGINE_LOCK:
            out = doc.tobytes(garbage=3, deflate=True)
    finally:
        with ENGINE_LOCK:
            doc.close()
    return out, done
