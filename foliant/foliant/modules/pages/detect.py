"""Rilevazione di pagine vuote e duplicate (PAG-40), senza interfaccia.

Le funzioni leggono soltanto il documento: chiamarle tenendo `ENGINE_LOCK`
(ad es. dentro il lavoro passato a `ctx.run_task`, che lo acquisisce da sé).
`progress` (facoltativo) è l'oggetto di `run_task`: `progress.update(frazione,
testo)` solleva `OperationCancelled` se l'utente annulla.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

import pymupdf

from foliant.api import _

TRANSLATIONS = {
    "Analisi della pagina {n} di {tot}…": "Analysing page {n} of {tot}…",
    "Confronto delle pagine…": "Comparing pages…",
}

DARK_LEVEL = 160          # grigio sotto questo valore = pixel "scuro" (inchiostro)
ARTIFACT_BAND = 0.10      # fascia alta/bassa in cui cercare intestazioni e numeri di pagina
ARTIFACT_MAX_CHARS = 60
_DPI = 50


@dataclass
class BlankInfo:
    index: int
    blank: bool
    ink: float                 # frazione di pixel scuri (0…1), artefatti esclusi
    visible_text: int          # caratteri visibili fuori dagli artefatti
    invisible_text: int        # caratteri invisibili (es. strato OCR)
    invalid: bool = False      # pagina non renderizzabile o di dimensione nulla


def _tick(progress, k: int, total: int, text: str | None = None) -> None:
    if progress is not None:
        progress.update(k / max(1, total), text)


def _gray(page: pymupdf.Page, dpi: int = _DPI, annots: bool = True):
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False, annots=annots)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return arr, pix.width / max(page.rect.width, 1e-6)


def _text_chars(page: pymupdf.Page):
    """Caratteri come (bbox nella pagina visualizzata, visibile?) dal tracciato del testo."""
    out = []
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001 - contenuti malformati
        return out
    rot = page.rotation_matrix
    for span in trace:
        invisible = span.get("type") == 3 or span.get("opacity", 1) == 0
        for ch in span.get("chars", []):
            if chr(ch[0]).isspace():
                continue
            out.append((pymupdf.Rect(ch[3]) * rot, not invisible))
    return out


def _artifact_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Righe brevi nella fascia alta o bassa (intestazioni, piè di pagina, numeri)."""
    rects = []
    h = page.rect.height
    try:
        d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    except Exception:  # noqa: BLE001
        return rects
    rot = page.rotation_matrix
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
            if not text or len(text) > ARTIFACT_MAX_CHARS:
                continue
            r = pymupdf.Rect(line["bbox"]) * rot
            if r.y1 <= h * ARTIFACT_BAND or r.y0 >= h * (1 - ARTIFACT_BAND):
                rects.append(r + (-2, -2, 2, 2))
    return rects


def analyze_blank(page: pymupdf.Page, ink_threshold: float = 0.01, ignore_annots: bool = True,
                  ignore_pagination_artifacts: bool = True, ocr_text_counts: bool = False) -> BlankInfo:
    """Una pagina è vuota se non ha testo visibile (fuori dagli artefatti) e la
    frazione di pixel scuri del rendering non supera `ink_threshold`."""
    idx = page.number
    r = page.rect
    if r.width < 1 or r.height < 1:
        return BlankInfo(idx, True, 0.0, 0, 0, invalid=True)
    try:
        arr, z = _gray(page, annots=not ignore_annots)
    except Exception:  # noqa: BLE001 - pagina non renderizzabile
        return BlankInfo(idx, True, 0.0, 0, 0, invalid=True)
    artifacts = _artifact_rects(page) if ignore_pagination_artifacts else []
    arr = arr.copy()
    for a in artifacts:
        arr[max(0, int(a.y0 * z)):max(0, int(a.y1 * z) + 1), max(0, int(a.x0 * z)):max(0, int(a.x1 * z) + 1)] = 255
    ink = float((arr < DARK_LEVEL).mean()) if arr.size else 0.0
    visible = invisible = 0
    for bbox, vis in _text_chars(page):
        c = pymupdf.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2)
        if any(a.contains(c) for a in artifacts):
            continue
        if vis:
            visible += 1
        else:
            invisible += 1
    blank = ink <= ink_threshold and visible == 0 and not (ocr_text_counts and invisible)
    return BlankInfo(idx, blank, ink, visible, invisible)


def find_blank_pages(pdf: pymupdf.Document, pages: Iterable[int] | None = None, ink_threshold: float = 0.01,
                     ignore_annots: bool = True, ignore_pagination_artifacts: bool = True, progress=None,
                     ocr_text_counts: bool = False, details: dict | None = None) -> list[int]:
    """Indici delle pagine vuote (o non valide). `details` riceve {indice: BlankInfo}."""
    idx = list(range(pdf.page_count)) if pages is None else list(pages)
    out = []
    for k, i in enumerate(idx):
        _tick(progress, k, len(idx), _("Analisi della pagina {n} di {tot}…").format(n=k + 1, tot=len(idx)))
        info = analyze_blank(pdf[i], ink_threshold, ignore_annots, ignore_pagination_artifacts, ocr_text_counts)
        if details is not None:
            details[i] = info
        if info.blank:
            out.append(i)
    _tick(progress, 1, 1)
    return out


# ------------------------------------------------------------------ duplicati
def _block_mean(arr, rows: int, cols: int):
    import numpy as np

    out = np.zeros((rows, cols))
    for r, band in enumerate(np.array_split(arr, rows, axis=0)):
        for c, cell in enumerate(np.array_split(band, cols, axis=1)):
            out[r, c] = cell.mean() if cell.size else 255.0
    return out


def page_fingerprint(page: pymupdf.Page) -> dict:
    """Impronta: hash del contenuto, hash del rendering e hash percettivo (dHash 16×16)."""
    try:
        content = page.read_contents()
    except Exception:  # noqa: BLE001
        content = b""
    arr, _z = _gray(page, dpi=40)
    small = _block_mean(arr, 32, 33)
    try:
        text = " ".join(page.get_text().split())
    except Exception:  # noqa: BLE001
        text = ""
    dhash = (small[:, 1:] > small[:, :-1]).flatten()
    r = page.rect
    return {
        "size": (round(r.width), round(r.height)),
        "content": hashlib.sha1(content).hexdigest(),
        "render": hashlib.sha1(arr.tobytes() + str(arr.shape).encode()).hexdigest(),
        "dhash": dhash,
        "ink": float((arr < DARK_LEVEL).mean()),
        "text": text,
    }


def similarity(a: dict, b: dict) -> float:
    """Somiglianza 0…1 tra due impronte: percettiva e, se c'è testo, anche testuale."""
    if a["size"] != b["size"]:
        return 0.0
    visual = 1.0 - float((a["dhash"] != b["dhash"]).mean())
    if a["text"] or b["text"]:
        from difflib import SequenceMatcher
        if a["text"] == b["text"]:
            return visual
        return min(visual, SequenceMatcher(None, a["text"], b["text"], autojunk=False).ratio())
    return visual


def find_duplicate_pages(pdf: pymupdf.Document, pages: Sequence[int] | None = None, mode: str = "identical",
                         scope: str = "all", progress=None, threshold: float = 0.95,
                         skip_blank: bool = True) -> list[tuple[int, int]]:
    """Coppie (duplicato, originale): l'originale è la prima occorrenza.

    `mode="identical"`: stesso contenuto o stesso rendering; `"similar"`:
    somiglianza percettiva ≥ `threshold`. `scope="adjacent"` confronta ogni
    pagina solo con la precedente dell'elenco. Le pagine senza inchiostro sono
    escluse con `skip_blank` (sono già segnalate come vuote).
    """
    idx = list(range(pdf.page_count)) if pages is None else list(pages)
    prints: dict[int, dict] = {}
    for k, i in enumerate(idx):
        _tick(progress, k * 0.9, len(idx), _("Analisi della pagina {n} di {tot}…").format(n=k + 1, tot=len(idx)))
        prints[i] = page_fingerprint(pdf[i])
    _tick(progress, 0.9, 1, _("Confronto delle pagine…"))

    def same(a: dict, b: dict) -> bool:
        if mode == "identical":
            return a["size"] == b["size"] and (a["content"] == b["content"] or a["render"] == b["render"])
        return similarity(a, b) >= threshold

    out: list[tuple[int, int]] = []
    originals: list[int] = []
    prev = None
    for i in idx:
        fp = prints[i]
        if skip_blank and fp["ink"] == 0.0:
            prev = i
            continue
        if scope == "adjacent":
            if prev is not None and not (skip_blank and prints[prev]["ink"] == 0.0) and same(fp, prints[prev]):
                root = dict(out).get(prev, prev)       # catena di copie: si risale all'originale
                out.append((i, root))
            prev = i
            continue
        match = next((o for o in originals if same(fp, prints[o])), None)
        if match is None:
            originals.append(i)
        else:
            out.append((i, match))
    _tick(progress, 1, 1)
    return out
