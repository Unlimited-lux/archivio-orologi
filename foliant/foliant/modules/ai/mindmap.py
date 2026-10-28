"""Mappa concettuale e scaletta (AI-12): logica senza interfaccia.

Contiene il modello dei nodi, la lettura della risposta del modello (JSON o
scaletta Markdown a rientri), le esportazioni (JSON, Markdown, SVG, PNG),
le modifiche (rinomina, aggiungi, elimina, sposta), l'impaginazione ad albero
da sinistra a destra e il disegno vettoriale della mappa in una pagina PDF.

Le pagine nel modello sono indici a base 0 (`Node.page`); nel JSON esportato e
nel testo per il modello sono numeri di pagina a base 1.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterator
from xml.sax.saxutils import escape

from foliant.i18n import _

# --------------------------------------------------------------------------- modello


@dataclass
class Node:
    id: str
    title: str
    note: str = ""
    page: int | None = None  # indice di pagina a base 0
    children: list["Node"] = field(default_factory=list)

    def walk(self) -> Iterator["Node"]:
        """Visita in profondità (preordine), radice compresa."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def count(self) -> int:
        return sum(1 for _n in self.walk())

    def find(self, node_id: str) -> "Node | None":
        for node in self.walk():
            if node.id == node_id:
                return node
        return None

    def parent_of(self, node_id: str) -> "Node | None":
        for node in self.walk():
            for child in node.children:
                if child.id == node_id:
                    return node
        return None

    def depth_of(self, node_id: str) -> int:
        """Profondità del nodo (radice = 0), -1 se assente."""
        stack = [(self, 0)]
        while stack:
            node, d = stack.pop()
            if node.id == node_id:
                return d
            stack.extend((c, d + 1) for c in node.children)
        return -1


class MindMapError(Exception):
    """Risposta del modello non utilizzabile o modifica non valida."""


# --------------------------------------------------------------------------- lettura

_TITLE_KEYS = ("title", "titolo", "label", "etichetta", "name", "nome", "text", "testo")
_PAGE_KEYS = ("page", "pagina", "pag", "p")
_NOTE_KEYS = ("note", "nota", "description", "descrizione")
_CHILD_KEYS = ("children", "figli", "nodes", "nodi", "items", "sottosezioni")
_PAGE_RE = re.compile(r"\(?\[?\s*(?:p|pp|pag|pagina|page)\.?\s*(\d+)\s*\]?\)?\s*$", re.IGNORECASE)


def _get(d: dict, keys: tuple[str, ...]):
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k in lower:
            return lower[k]
    return None


def _page_number(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (list, tuple)) and value:
        return _page_number(value[0])
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _from_dict(d, raw_pages: dict[int, int | None], counter: list[int]) -> Node | None:
    """Converte un dizionario in Node; `raw_pages` registra il numero di pagina grezzo."""
    if isinstance(d, str):
        d = {"title": d}
    if not isinstance(d, dict):
        return None
    title = _get(d, _TITLE_KEYS)
    title = " ".join(str(title).split()) if title is not None else ""
    note = _get(d, _NOTE_KEYS)
    note = str(note).strip() if note is not None else ""
    kids = _get(d, _CHILD_KEYS) or []
    if isinstance(kids, dict):
        kids = [kids]
    children = []
    if isinstance(kids, list):
        for k in kids:
            child = _from_dict(k, raw_pages, counter)
            if child is not None:
                children.append(child)
    if not title and not children:
        return None
    node = Node(id="", title=title or _("(senza titolo)"), note=note, children=children)
    raw_pages[id(node)] = _page_number(_get(d, _PAGE_KEYS))
    counter[0] += 1
    return node


def _json_candidates(text: str) -> Iterator[object]:
    for m in re.finditer(r"```(?:json|JSON)?\s*\n?(.*?)```", text, re.DOTALL):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\{\[]", text):
        try:
            obj, _end = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        yield obj


def _tree_from_json(text: str, raw_pages: dict) -> Node | None:
    for obj in _json_candidates(text):
        if isinstance(obj, dict):
            for key in ("root", "radice", "map", "mappa", "tree", "albero"):
                inner = _get(obj, (key,))
                if isinstance(inner, dict):
                    obj = inner
                    break
            node = _from_dict(obj, raw_pages, [0])
        elif isinstance(obj, list) and obj and all(isinstance(x, (dict, str)) for x in obj):
            nodes = [n for n in (_from_dict(x, raw_pages, [0]) for x in obj) if n is not None]
            if len(nodes) == 1:
                node = nodes[0]
            elif nodes:
                node = Node(id="", title=_("Documento"), children=nodes)
                raw_pages[id(node)] = None
            else:
                node = None
        else:
            continue
        if node is not None:
            return node
    return None


_BULLET_RE = re.compile(r"^(\s*)(?:[-*+•]|\d+[.)])\s+(.+?)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _split_page(label: str) -> tuple[str, int | None]:
    label = label.strip().strip("*_").strip()
    m = _PAGE_RE.search(label)
    if m and m.start() > 0:
        return label[: m.start()].rstrip(" -–—:,").strip("*_ ").strip(), int(m.group(1))
    return label, None


def _tree_from_markdown(text: str, raw_pages: dict) -> Node | None:
    entries: list[tuple[int, str, int | None]] = []  # (livello, titolo, pagina)
    heading_title: tuple[str, int | None] | None = None
    for line in text.replace("\t", "    ").splitlines():
        if not line.strip():
            continue
        h = _HEADING_RE.match(line)
        if h:
            title, page = _split_page(h.group(2))
            if not entries and heading_title is None and title:
                heading_title = (title, page)
            elif title:
                entries.append((-10 + len(h.group(1)), title, page))
            continue
        m = _BULLET_RE.match(line)
        if m:
            title, page = _split_page(m.group(2))
            if title:
                entries.append((len(m.group(1)), title, page))
    if not entries:
        return None
    # livelli di rientro -> profondità
    indents = sorted({e[0] for e in entries})
    depth_of = {ind: i for i, ind in enumerate(indents)}
    top: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for ind, title, page in entries:
        d = depth_of[ind]
        node = Node(id="", title=title)
        raw_pages[id(node)] = page
        while stack and stack[-1][0] >= d:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            top.append(node)
        stack.append((d, node))
    if heading_title is not None:
        root = Node(id="", title=heading_title[0], children=top)
        raw_pages[id(root)] = heading_title[1]
    elif len(top) == 1:
        root = top[0]
    else:
        root = Node(id="", title=_("Documento"), children=top)
        raw_pages[id(root)] = None
    return root


def assign_ids(root: Node) -> None:
    """Assegna identificativi n0, n1, … in preordine."""
    for i, node in enumerate(root.walk()):
        node.id = f"n{i}"


def _limit_depth(root: Node, max_depth: int) -> int:
    """Accorpa i livelli oltre `max_depth` (radice = 0); restituisce i nodi accorpati."""
    merged = 0
    stack = [(root, 0)]
    while stack:
        node, d = stack.pop()
        if d >= max_depth and node.children:
            titles = [n.title for c in node.children for n in c.walk()]
            merged += len(titles)
            extra = "; ".join(titles)
            node.note = f"{node.note}\n{extra}".strip() if node.note else extra
            if node.page is None:
                node.page = next((n.page for c in node.children for n in c.walk() if n.page is not None), None)
            node.children = []
        else:
            stack.extend((c, d + 1) for c in node.children)
    return merged


def _limit_nodes(root: Node, max_nodes: int) -> int:
    """Tiene i primi `max_nodes` nodi in ampiezza; restituisce quanti ne elimina."""
    total = root.count()
    if total <= max_nodes:
        return 0
    keep: set[int] = set()
    queue = deque([root])
    while queue and len(keep) < max_nodes:
        node = queue.popleft()
        keep.add(id(node))
        queue.extend(node.children)
    for node in list(root.walk()):
        node.children = [c for c in node.children if id(c) in keep]
    return total - root.count()


def parse_tree(text: str, page_count: int, *, max_depth: int = 4, max_nodes: int = 60,
               page_base: int = 1) -> tuple[Node, list[str]]:
    """Legge la risposta del modello e restituisce (radice, avvisi).

    Accetta JSON (anche tra recinti ``` o con testo attorno) con chiavi
    title/titolo, page/pagina, note/nota, children/figli; in mancanza, una
    scaletta Markdown a elenchi puntati rientrati ("- Titolo [p. 3]").
    `max_depth` è il numero di livelli sotto la radice.
    """
    if not isinstance(text, str) or not text.strip():
        raise MindMapError(_("La risposta è vuota."))
    raw_pages: dict[int, int | None] = {}
    root = _tree_from_json(text, raw_pages)
    if root is None:
        raw_pages.clear()
        root = _tree_from_markdown(text, raw_pages)
    if root is None:
        raise MindMapError(_("La risposta non contiene una struttura di mappa riconoscibile."))

    warnings: list[str] = []
    bad: list[int] = []
    for node in root.walk():
        raw = raw_pages.get(id(node))
        if raw is None:
            node.page = None
            continue
        index = raw - page_base
        if 0 <= index < page_count:
            node.page = index
        else:
            node.page = None
            bad.append(raw)
    if bad:
        warnings.append(_("Riferimenti di pagina non validi ignorati: {pagine}.").format(
            pagine=", ".join(str(p) for p in bad)))

    merged = _limit_depth(root, max(1, max_depth))
    if merged:
        warnings.append(_("{n} nodi oltre il livello {livello} sono stati accorpati nel nodo superiore.")
                        .format(n=merged, livello=max(1, max_depth)))
    dropped = _limit_nodes(root, max(1, max_nodes))
    if dropped:
        warnings.append(_("{n} nodi oltre il limite di {massimo} sono stati omessi.")
                        .format(n=dropped, massimo=max(1, max_nodes)))
    assign_ids(root)
    return root, warnings


# --------------------------------------------------------------------------- esportazioni


def _to_dict(node: Node) -> dict:
    return {
        "id": node.id,
        "title": node.title,
        "note": node.note,
        "page": None if node.page is None else node.page + 1,
        "children": [_to_dict(c) for c in node.children],
    }


def to_json(root: Node) -> str:
    """JSON {id, title, note, page (a base 1 o null), children}."""
    return json.dumps(_to_dict(root), ensure_ascii=False, indent=2)


def from_json(text: str) -> Node:
    """Ricostruisce l'albero da `to_json` (pagine a base 1); conserva gli id."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise MindMapError(_("File della mappa non valido.")) from exc

    def build(d) -> Node:
        if not isinstance(d, dict):
            raise MindMapError(_("File della mappa non valido."))
        title = _get(d, _TITLE_KEYS)
        page = _page_number(_get(d, _PAGE_KEYS))
        kids = _get(d, _CHILD_KEYS) or []
        return Node(
            id=str(d.get("id") or ""),
            title=str(title) if title is not None else "",
            note=str(_get(d, _NOTE_KEYS) or ""),
            page=None if page is None or page < 1 else page - 1,
            children=[build(c) for c in kids],
        )

    root = build(data)
    ids = [n.id for n in root.walk()]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        assign_ids(root)
    return root


def to_markdown_outline(root: Node) -> str:
    """Scaletta Markdown a elenchi annidati, con " (p. N)" a base 1."""
    lines: list[str] = []

    def emit(node: Node, depth: int) -> None:
        suffix = f" (p. {node.page + 1})" if node.page is not None else ""
        lines.append(f"{'  ' * depth}- {node.title}{suffix}")
        for c in node.children:
            emit(c, depth + 1)

    emit(root, 0)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- modifiche


def _require(root: Node, node_id: str) -> Node:
    node = root.find(node_id)
    if node is None:
        raise MindMapError(_("Nodo non trovato."))
    return node


def new_id(root: Node) -> str:
    used = {n.id for n in root.walk()}
    nums = [int(i[1:]) for i in used if re.fullmatch(r"n\d+", i)]
    k = max(nums, default=-1) + 1
    while f"n{k}" in used:
        k += 1
    return f"n{k}"


def rename(root: Node, node_id: str, title: str) -> None:
    title = " ".join(title.split())
    if not title:
        raise MindMapError(_("Il titolo non può essere vuoto."))
    _require(root, node_id).title = title


def add_child(root: Node, parent_id: str, title: str) -> Node:
    parent = _require(root, parent_id)
    node = Node(id=new_id(root), title=" ".join(title.split()) or _("Nuovo nodo"))
    parent.children.append(node)
    return node


def delete(root: Node, node_id: str) -> None:
    if node_id == root.id:
        raise MindMapError(_("La radice non si può eliminare."))
    parent = root.parent_of(node_id)
    if parent is None:
        raise MindMapError(_("Nodo non trovato."))
    parent.children = [c for c in parent.children if c.id != node_id]


def move(root: Node, node_id: str, new_parent_id: str, index: int) -> None:
    if node_id == root.id:
        raise MindMapError(_("La radice non si può spostare."))
    node = _require(root, node_id)
    target = _require(root, new_parent_id)
    if node.find(new_parent_id) is not None:
        raise MindMapError(_("Un nodo non si può spostare dentro sé stesso."))
    parent = root.parent_of(node_id)
    old = parent.children.index(node)
    parent.children.pop(old)
    if parent is target and old < index:
        index -= 1
    index = max(0, min(index, len(target.children)))
    target.children.insert(index, node)


# --------------------------------------------------------------------------- impaginazione

MAX_LABEL = 40
CHAR_WIDTH = 6.2   # larghezza media di un carattere a corpo 11 (approssimazione)
NODE_HEIGHT = 26.0
PAD_X = 10.0
H_GAP = 36.0
V_GAP = 10.0


def short_label(title: str, limit: int = MAX_LABEL) -> str:
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


def layout(root: Node, *, collapsed: set[str] | frozenset = frozenset(),
           char_width: float = CHAR_WIDTH, node_height: float = NODE_HEIGHT,
           h_gap: float = H_GAP, v_gap: float = V_GAP,
           measure: Callable[[Node, str], float] | None = None) -> dict[str, tuple[float, float, float, float]]:
    """Albero da sinistra a destra: {id: (x, y, w, h)} con origine (0, 0) in alto a sinistra.

    La larghezza dei riquadri è stimata con `char_width` per carattere, oppure
    misurata con `measure(nodo, etichetta)` se indicata. I figli dei nodi in
    `collapsed` non vengono disposti.
    """
    def width(node: Node) -> float:
        label = short_label(node.title)
        text_w = measure(node, label) if measure is not None else len(label) * char_width
        return max(40.0, text_w + 2 * PAD_X)

    # larghezza massima per livello -> colonne
    col_w: dict[int, float] = {}
    stack = [(root, 0)]
    while stack:
        node, d = stack.pop()
        col_w[d] = max(col_w.get(d, 0.0), width(node))
        if node.id not in collapsed:
            stack.extend((c, d + 1) for c in node.children)
    col_x: dict[int, float] = {}
    x = 0.0
    for d in range(len(col_w)):
        col_x[d] = x
        x += col_w[d] + h_gap

    boxes: dict[str, tuple[float, float, float, float]] = {}
    next_y = [0.0]

    def place(node: Node, d: int) -> float:
        kids = node.children if node.id not in collapsed else []
        if kids:
            centers = [place(c, d + 1) for c in kids]
            cy = (centers[0] + centers[-1]) / 2
            y = cy - node_height / 2
        else:
            y = next_y[0]
            next_y[0] += node_height + v_gap
            cy = y + node_height / 2
        boxes[node.id] = (col_x[d], y, width(node), node_height)
        return cy

    place(root, 0)
    min_y = min(b[1] for b in boxes.values())
    if min_y:
        boxes = {k: (x, y - min_y, w, h) for k, (x, y, w, h) in boxes.items()}
    return boxes


def bounds(boxes: dict[str, tuple[float, float, float, float]]) -> tuple[float, float]:
    return (max(x + w for x, y, w, h in boxes.values()), max(y + h for x, y, w, h in boxes.values()))


def edges(root: Node, collapsed: set[str] | frozenset = frozenset()) -> list[tuple[str, str]]:
    out = []
    for node in root.walk():
        if node.id in collapsed:
            continue
        out.extend((node.id, c.id) for c in node.children)
    # escludi i rami sotto nodi compressi
    visible = set()
    stack = [root]
    while stack:
        n = stack.pop()
        visible.add(n.id)
        if n.id not in collapsed:
            stack.extend(n.children)
    return [(a, b) for a, b in out if a in visible and b in visible]


def _export_colors() -> dict[str, str]:
    """Colori del tema chiaro per le uscite (stampa)."""
    from foliant.ui.theme import LIGHT

    def hexa(name: str) -> str:
        v = LIGHT.get(name)
        if isinstance(v, tuple):
            return "#%02X%02X%02X" % v[:3]
        return str(v)

    return {k: hexa(k) for k in ("surface", "accent", "accent_soft", "text", "border", "text_muted")}


def _hex_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_svg(root: Node, *, margin: float = 16.0) -> str:
    """SVG autonomo (nessuna risorsa esterna) della mappa."""
    col = _export_colors()
    boxes = layout(root)
    bw, bh = bounds(boxes)
    W, H = bw + 2 * margin, bh + 2 * margin
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
        f'viewBox="0 0 {W:.1f} {H:.1f}" font-family="sans-serif" font-size="11">',
        f'<rect x="0" y="0" width="{W:.1f}" height="{H:.1f}" fill="{col["surface"]}"/>',
    ]
    for a, b in edges(root):
        ax, ay, aw, ah = boxes[a]
        bx, by, _bw, bhh = boxes[b]
        x1, y1 = ax + aw + margin, ay + ah / 2 + margin
        x2, y2 = bx + margin, by + bhh / 2 + margin
        mx = (x1 + x2) / 2
        parts.append(f'<path d="M{x1:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" '
                     f'fill="none" stroke="{col["border"]}" stroke-width="1.2"/>')
    for node in root.walk():
        x, y, w, h = boxes[node.id]
        x += margin
        y += margin
        is_root = node is root
        fill = col["accent"] if is_root else col["accent_soft"]
        fg = col["surface"] if is_root else col["text"]
        tip = escape(node.title + (f" (p. {node.page + 1})" if node.page is not None else ""))
        parts.append(
            f'<g><title>{tip}</title>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6" '
            f'fill="{fill}" stroke="{col["accent"]}" stroke-width="1"/>'
            f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + 4:.1f}" text-anchor="middle" fill="{fg}">'
            f'{escape(short_label(node.title))}</text></g>')
    parts.append("</svg>")
    return "\n".join(parts)


MIN_FONT = 4.0
_PDF_FONT = "helv"
_PDF_BOLD = "hebo"
BASE_FONT = 11.0


def insert_page(pdf, root: Node, at: int = 0, size: tuple | None = None, *, margin: float = 36.0) -> int:
    """Inserisce in `pdf` (pymupdf.Document) una pagina con la mappa vettoriale.

    `at` è la posizione della nuova pagina (-1 o oltre la fine = in coda). Le
    dimensioni sono `size` o quelle della pagina in posizione `at` (o della
    prima). Il testo dei nodi è selezionabile; il corpo si riduce per far stare
    la mappa nella pagina, fino a un minimo di 4 pt. Restituisce l'indice della
    pagina inserita. Da chiamare dentro `document.change()`.
    """
    n = pdf.page_count
    index = n if at < 0 or at > n else at
    if size is None:
        if n:
            ref = pdf[min(index, n - 1)].rect
            size = (ref.width, ref.height)
        else:
            size = (595.0, 842.0)
    pw, ph = float(size[0]), float(size[1])
    page = pdf.new_page(pno=index if index < n else -1, width=pw, height=ph)
    page_no = page.number
    _draw_map(page, root, pw, ph, margin)
    return page_no


def _draw_map(page, root: Node, pw: float, ph: float, margin: float) -> None:
    import pymupdf

    col = {k: _hex_rgb(v) for k, v in _export_colors().items()}
    boxes = _pdf_layout(root)
    bw, bh = bounds(boxes)
    margin = min(margin, pw / 8, ph / 8)
    avail_w, avail_h = pw - 2 * margin, ph - 2 * margin
    scale = min(avail_w / bw, avail_h / bh, 1.6)
    if BASE_FONT * scale < MIN_FONT:
        scale = MIN_FONT / BASE_FONT
    ox = margin + max(0.0, (avail_w - bw * scale) / 2)
    oy = margin + max(0.0, (avail_h - bh * scale) / 2)

    def rect_of(nid: str):
        x, y, w, h = boxes[nid]
        return pymupdf.Rect(ox + x * scale, oy + y * scale, ox + (x + w) * scale, oy + (y + h) * scale)

    shape = page.new_shape()
    for a, b in edges(root):
        ra, rb = rect_of(a), rect_of(b)
        p1 = pymupdf.Point(ra.x1, (ra.y0 + ra.y1) / 2)
        p2 = pymupdf.Point(rb.x0, (rb.y0 + rb.y1) / 2)
        mx = (p1.x + p2.x) / 2
        shape.draw_bezier(p1, pymupdf.Point(mx, p1.y), pymupdf.Point(mx, p2.y), p2)
    shape.finish(color=col["border"], width=max(0.4, 1.1 * scale), closePath=False)
    for node in root.walk():
        r = rect_of(node.id)
        is_root = node is root
        shape.draw_rect(r, radius=min(0.25, 5 * scale / max(1.0, min(r.width, r.height))))
        shape.finish(color=col["accent"], fill=col["accent"] if is_root else col["accent_soft"],
                     width=max(0.3, 0.8 * scale))
    shape.commit()

    for node in root.walk():
        r = rect_of(node.id)
        label = short_label(node.title)
        fg = col["surface"] if node is root else col["text"]
        font = _PDF_BOLD if node is root else _PDF_FONT
        fs = BASE_FONT * scale
        room = r.width - 2 * PAD_X * scale
        length = pymupdf.get_text_length(label, fontname=font, fontsize=fs)
        if length > room > 0:
            fs *= room / length
        fs = max(MIN_FONT, fs)
        length = pymupdf.get_text_length(label, fontname=font, fontsize=fs)
        x = (r.x0 + r.x1) / 2 - length / 2
        y = (r.y0 + r.y1) / 2 + fs * 0.35
        page.insert_text(pymupdf.Point(x, y), label, fontsize=fs, fontname=font, color=fg)


def _pdf_layout(root: Node) -> dict[str, tuple[float, float, float, float]]:
    import pymupdf

    return layout(root, measure=lambda n, label: pymupdf.get_text_length(
        label, fontname=_PDF_BOLD if n is root else _PDF_FONT, fontsize=BASE_FONT))


def render_png(root: Node, path=None, *, dpi: int = 144) -> bytes:
    """Immagine PNG della mappa; se `path` è dato la scrive anche su disco."""
    import pymupdf

    bw, bh = bounds(_pdf_layout(root))
    doc = pymupdf.open()
    try:
        insert_page(doc, root, 0, (bw + 48, bh + 48), margin=24)
        data = doc[0].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
    if path is not None:
        with open(path, "wb") as fh:
            fh.write(data)
    return data
