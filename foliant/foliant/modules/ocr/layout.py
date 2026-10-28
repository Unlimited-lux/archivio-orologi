"""Analisi del layout (OCR-10): tabelle con bordi e zone manuali.

- Tabelle con bordi: le linee orizzontali e verticali dell'immagine elaborata
  formano una griglia; le parole vengono assegnate alle celle e ogni cella
  diventa una riga (blocco) a sé, nell'ordine riga per riga.
- Zone manuali: rettangoli in frazioni della pagina visualizzata (0–1), di tipo
  ``text`` (ordine e lingua propri), ``table`` (struttura forzata), ``image`` e
  ``ignore`` (nessun testo prodotto). Si possono salvare come modello e
  applicare a più pagine o a un lotto.

Nessuna interfaccia: funzioni su array numpy e sui dati di `tess.Line`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from .preprocess import apply_point
from .tess import Line, Word

TEXT, TABLE, IMAGE, IGNORE = "text", "table", "image", "ignore"
ZONE_KINDS = (TEXT, TABLE, IMAGE, IGNORE)


@dataclass
class Zone:
    rect: tuple[float, float, float, float]      # frazioni della pagina visualizzata (x0, y0, x1, y1)
    kind: str = TEXT
    languages: list[str] = field(default_factory=list)   # vuoto = lingue del documento
    order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Zone":
        r = tuple(float(v) for v in d["rect"])
        kind = d.get("kind", TEXT)
        if len(r) != 4 or kind not in ZONE_KINDS:
            raise ValueError("zona non valida")
        r = (max(0.0, min(r[0], r[2])), max(0.0, min(r[1], r[3])), min(1.0, max(r[0], r[2])),
             min(1.0, max(r[1], r[3])))
        return Zone(r, kind, [str(c) for c in d.get("languages", [])], int(d.get("order", 0)))


@dataclass
class Table:
    xs: list[float]          # ascisse delle linee verticali (pixel elaborati)
    ys: list[float]          # ordinate delle linee orizzontali
    cells: list[list[str]] = field(default_factory=list)   # testo per riga e colonna

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.ys) - 1, len(self.xs) - 1)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.xs[0], self.ys[0], self.xs[-1], self.ys[-1])


# ---------------------------------------------------------------- coordinate
def zone_box(zone: Zone, img_size: tuple[int, int], affine) -> tuple[float, float, float, float]:
    """Riquadro della zona in pixel dell'immagine elaborata (rotazione/raddrizzamento compresi)."""
    w, h = img_size
    x0, y0, x1, y1 = zone.rect
    pts = [(x0 * w, y0 * h), (x1 * w, y0 * h), (x0 * w, y1 * h), (x1 * w, y1 * h)]
    if affine is not None:
        pts = [apply_point(np.asarray(affine), x, y) for x, y in pts]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _center(b) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _inside(b, box) -> bool:
    cx, cy = _center(b)
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


# ---------------------------------------------------------------- tabelle
def _positions(profile: np.ndarray, min_len: int) -> list[int]:
    """Centri dei tratti consecutivi in cui il profilo supera min_len."""
    on = profile >= min_len
    out, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start + i - 1) // 2)
            start = None
    if start is not None:
        out.append((start + len(on) - 1) // 2)
    return out


def detect_tables(gray: np.ndarray, box: tuple | None = None, min_cells: int = 2) -> list[Table]:
    """Tabelle con bordi: griglie di linee orizzontali e verticali (pixel elaborati)."""
    h, w = gray.shape[:2]
    x0, y0, x1, y1 = (0, 0, w, h) if box is None else (int(max(0, box[0])), int(max(0, box[1])),
                                                       int(min(w, box[2])), int(min(h, box[3])))
    region = gray[y0:y1, x0:x1]
    if region.size == 0:
        return []
    _t, bw = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rh, rw = bw.shape
    klen_h = max(20, rw // 25)
    klen_v = max(20, rh // 40)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (klen_h, 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, klen_v)))
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(grid, 8)
    tables: list[Table] = []
    for i in range(1, n):
        gx, gy, gw, gh, _area = stats[i]
        if gw < rw * 0.15 or gh < 20:
            continue
        sub_h = horiz[gy:gy + gh, gx:gx + gw]
        sub_v = vert[gy:gy + gh, gx:gx + gw]
        ys = _positions((sub_h > 0).sum(axis=1), int(gw * 0.5))
        xs = _positions((sub_v > 0).sum(axis=0), int(gh * 0.5))
        if len(xs) < min_cells or len(ys) < min_cells:
            continue
        tables.append(Table([float(x + gx + x0) for x in xs], [float(y + gy + y0) for y in ys]))
    return tables


def table_from_words(lines: list[Line], box) -> Table | None:
    """Tabella senza bordi in una zona «tabella»: colonne dagli allineamenti, righe dalle righe di testo."""
    rows = [ln for ln in lines if _inside(ln.bbox, box)]
    if len(rows) < 2:
        return None
    words = [w for ln in rows for w in ln.words]
    if not words:
        return None
    # colonne: intervalli x coperti da parole, separati da canali vuoti
    xs = sorted((w.bbox[0], w.bbox[2]) for w in words)
    cols: list[list[float]] = []
    for a, b in xs:
        if cols and a <= cols[-1][1] + 8:
            cols[-1][1] = max(cols[-1][1], b)
        else:
            cols.append([a, b])
    if len(cols) < 2:
        return None
    cuts = [box[0]] + [(cols[i][1] + cols[i + 1][0]) / 2 for i in range(len(cols) - 1)] + [box[2]]
    ys = [box[1]] + [(rows[i].bbox[3] + rows[i + 1].bbox[1]) / 2 for i in range(len(rows) - 1)] + [box[3]]
    return Table(cuts, ys)


def fill_table(table: Table, lines: list[Line]) -> list[Line]:
    """Assegna le parole alle celle; restituisce le righe fuori tabella più una riga per cella
    (in ordine riga per riga), e compila `table.cells`."""
    rows_n, cols_n = table.shape
    cells: dict[tuple[int, int], list[Word]] = {}
    template: dict[tuple[int, int], Line] = {}
    outside: list[Line] = []
    for ln in lines:
        keep = []
        for w in ln.words:
            cx, cy = _center(w.bbox)
            r = next((i for i in range(rows_n) if table.ys[i] <= cy <= table.ys[i + 1]), None)
            c = next((j for j in range(cols_n) if table.xs[j] <= cx <= table.xs[j + 1]), None)
            if r is None or c is None:
                keep.append(w)
            else:
                cells.setdefault((r, c), []).append(w)
                template.setdefault((r, c), ln)
        if keep:
            ln.words = keep
            ln.bbox = (min(w.bbox[0] for w in keep), ln.bbox[1], max(w.bbox[2] for w in keep), ln.bbox[3])
            outside.append(ln)
    table.cells = [["" for _c in range(cols_n)] for _r in range(rows_n)]
    cell_lines: list[Line] = []
    base_block = 10_000 + len(outside)
    for (r, c) in sorted(cells):
        ws = sorted(cells[(r, c)], key=lambda w: (round(_center(w.bbox)[1] / 10), w.bbox[0]))
        table.cells[r][c] = " ".join(w.text for w in ws)
        src = template[(r, c)]
        bbox = (min(w.bbox[0] for w in ws), min(w.bbox[1] for w in ws), max(w.bbox[2] for w in ws),
                max(w.bbox[3] for w in ws))
        offset = src.baseline_y(bbox[0]) - bbox[3]
        cell_lines.append(Line(bbox=bbox, words=ws, baseline=(src.baseline[0], offset), x_size=src.x_size,
                               x_ascenders=src.x_ascenders, x_descenders=src.x_descenders,
                               block=base_block + r * 100 + c, par=base_block + r * 100 + c))
    # le celle prendono il posto della tabella nell'ordine di lettura (prima delle righe sotto la tabella)
    top = table.ys[0]
    before = [ln for ln in outside if ln.bbox[3] <= top]
    after = [ln for ln in outside if ln.bbox[3] > top]
    return before + cell_lines + after


# ---------------------------------------------------------------- zone
def apply_layout(lines: list[Line], gray: np.ndarray, img_size: tuple[int, int], affine, zones: list[Zone],
                 recognize_zone=None, auto_tables: bool = True) -> tuple[list[Line], list[Table]]:
    """Applica zone manuali e tabelle alle righe riconosciute.

    `recognize_zone(gray_crop, languages)` riconosce una zona con lingue proprie e restituisce
    righe con coordinate relative al ritaglio.
    """
    boxes = [(z, zone_box(z, img_size, affine)) for z in sorted(zones, key=lambda z: z.order)]
    # 1) zone da ignorare o immagini: nessun testo
    for z, box in boxes:
        if z.kind in (IGNORE, IMAGE):
            for ln in lines:
                ln.words = [w for w in ln.words if not _inside(w.bbox, box)]
    lines = [ln for ln in lines if ln.words]
    # 2) zone di testo con lingua propria: riconosciute di nuovo sul ritaglio
    for z, box in boxes:
        if z.kind == TEXT and z.languages and recognize_zone is not None:
            x0, y0, x1, y1 = (int(max(0, box[0])), int(max(0, box[1])), int(box[2]), int(box[3]))
            crop = gray[y0:y1, x0:x1]
            if crop.size == 0:
                continue
            new = recognize_zone(crop, z.languages)
            for ln in lines:
                ln.words = [w for w in ln.words if not _inside(w.bbox, box)]
            lines = [ln for ln in lines if ln.words]
            for ln in new:
                ln.bbox = (ln.bbox[0] + x0, ln.bbox[1] + y0, ln.bbox[2] + x0, ln.bbox[3] + y0)
                for w in ln.words:
                    w.bbox = (w.bbox[0] + x0, w.bbox[1] + y0, w.bbox[2] + x0, w.bbox[3] + y0)
            lines.extend(new)
    # 3) tabelle: zone «tabella» (griglia dalle linee o dagli allineamenti) e tabelle con bordi automatiche
    tables: list[Table] = []
    for z, box in boxes:
        if z.kind == TABLE:
            found = detect_tables(gray, box)
            t = found[0] if found else table_from_words(lines, box)
            if t is not None:
                tables.append(t)
    if auto_tables and not any(z.kind == TABLE for z in zones):
        for t in detect_tables(gray):
            if t.shape[0] >= 2 and t.shape[1] >= 2:
                tables.append(t)
    for t in tables:
        lines = fill_table(t, lines)
    # 4) ordine di lettura: righe nelle zone di testo nell'ordine delle zone, poi il resto
    text_zones = [(z, b) for z, b in boxes if z.kind == TEXT]
    if text_zones:
        ordered: list[Line] = []
        rest = list(lines)
        for z, box in text_zones:
            inside = [ln for ln in rest if _inside(ln.bbox, box)]
            inside.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
            for k, ln in enumerate(inside):
                ln.block = 20_000 + z.order * 1000
                ln.par = ln.block
            ordered.extend(inside)
            rest = [ln for ln in rest if ln not in inside]
        lines = ordered + rest
    return lines, tables


def table_text(table: Table) -> str:
    return "\n".join("\t".join(row) for row in table.cells)
