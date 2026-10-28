"""Pulizia guidata del documento (PAG-41), senza interfaccia.

`analyze()` legge il documento (tenere `ENGINE_LOCK`) e restituisce le
proposte per categoria; `apply_proposals()` applica quelle accettate sul
documento ricevuto (chiamarla dentro un unico `document.change()`).
L'analisi è solo locale: nessun accesso alla rete. Per le scansioni senza
testo l'orientamento è stimato, se disponibile, con il programma locale
`tesseract` (rilevamento dell'orientamento, `--psm 0`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import pymupdf

from foliant.i18n import format_number
from foliant.api import _

from . import detect, layout
from .engine import PageOpError, delete_pages

TRANSLATIONS = {
    "Pagine vuote o non valide": "Blank or invalid pages",
    "Orientamento del testo": "Text orientation",
    "Formati di pagina non uniformi": "Non-uniform page sizes",
    "Pagine da riconoscere (OCR)": "Pages to recognise (OCR)",
    "Annotazioni": "Annotations",
    "Pagina {n}: vuota (inchiostro {p}%)": "Page {n}: blank (ink {p}%)",
    "Pagina {n}: non valida (non renderizzabile o di dimensione nulla)":
        "Page {n}: invalid (cannot be rendered or zero size)",
    "Pagina {n}: testo ruotato, ruota di {g}°": "Page {n}: rotated text, rotate by {g}°",
    "Pagina {n}: scansione ruotata, ruota di {g}°": "Page {n}: rotated scan, rotate by {g}°",
    "Pagina {n}: {w} × {h} mm, diverso da {rw} × {rh} mm (pagina {r})":
        "Page {n}: {w} × {h} mm, differs from {rw} × {rh} mm (page {r})",
    "Pagina {n}: solo immagini, nessun testo": "Page {n}: images only, no text",
    "Pagina {n}: {tipo} — {testo}": "Page {n}: {tipo} — {testo}",
    "Analisi: pagine vuote…": "Analysis: blank pages…",
    "Analisi: orientamento…": "Analysis: orientation…",
    "Analisi: formati…": "Analysis: sizes…",
    "Analisi: pagine da riconoscere…": "Analysis: pages to recognise…",
    "Analisi: annotazioni…": "Analysis: annotations…",
}

CATEGORIES = ("blank", "orientation", "sizes", "ocr", "annotations")
CATEGORY_LABELS = {
    "blank": "Pagine vuote o non valide",
    "orientation": "Orientamento del testo",
    "sizes": "Formati di pagina non uniformi",
    "ocr": "Pagine da riconoscere (OCR)",
    "annotations": "Annotazioni",
}
MIN_CHARS = 20            # testo minimo per stimare l'orientamento
MIN_CONFIDENCE = 0.8      # quota del testo nella direzione prevalente
OSD_MIN_CONFIDENCE = 2.0  # confidenza minima del rilevamento di tesseract
SIZE_TOLERANCE = 2.0      # pt


@dataclass
class Proposal:
    category: str
    page: int
    action: str                      # delete | rotate | match | ocr | remove_annot
    reason: str
    params: dict = field(default_factory=dict)
    accepted: bool = True


def _mm(v: float) -> str:
    return f"{v / layout.MM:.0f}"


def _progress(progress, f: float, text: str) -> None:
    if progress is not None:
        progress.update(f, text)


# ------------------------------------------------------------------ orientamento
_DIR_ANGLE = {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}


def text_orientation(page: pymupdf.Page) -> tuple[int, float, int] | None:
    """Direzione prevalente del testo nella pagina non ruotata.

    Restituisce (angolo 0/90/180/270 in senso orario, confidenza 0…1, caratteri)
    oppure None se il testo è troppo poco.
    """
    weights: Counter = Counter()
    try:
        d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    except Exception:  # noqa: BLE001
        return None
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            n = sum(len(s.get("text", "").strip()) for s in line.get("spans", []))
            if not n:
                continue
            dx, dy = line.get("dir", (1, 0))
            key = (round(dx), round(dy))
            if key in _DIR_ANGLE and abs(abs(dx) + abs(dy) - 1) < 0.2:
                weights[_DIR_ANGLE[key]] += n
            else:
                weights["other"] += n
    total = sum(weights.values())
    if total < MIN_CHARS:
        return None
    angle, w = max(((a, v) for a, v in weights.items() if a != "other"), key=lambda t: t[1], default=(0, 0))
    return int(angle), w / total, total


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def scan_orientation(page: pymupdf.Page, timeout: float = 30.0) -> tuple[int, float] | None:
    """Rotazione oraria che raddrizza una scansione (tesseract locale), o None."""
    exe = shutil.which("tesseract")
    if exe is None:
        return None
    try:
        png = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY, alpha=False, annots=False).tobytes("png")
        res = subprocess.run([exe, "stdin", "stdout", "--psm", "0"], input=png, capture_output=True,
                             timeout=timeout, check=False)
    except Exception:  # noqa: BLE001 - tesseract assente o in errore: nessuna proposta
        return None
    out = res.stdout.decode("utf-8", "replace") + res.stderr.decode("utf-8", "replace")
    rot = re.search(r"Rotate:\s*(\d+)", out)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not rot or not conf:
        return None
    return int(rot.group(1)) % 360, float(conf.group(1))


def _rotation_fix(page: pymupdf.Page, text_angle: int) -> int:
    """Rotazione da aggiungere a /Rotate perché il testo appaia orizzontale."""
    target = (-text_angle) % 360
    return (target - page.rotation) % 360


# ------------------------------------------------------------------ immagini
def is_image_only(page: pymupdf.Page, coverage: float = 0.5) -> bool:
    """Pagina composta da immagini (≥ `coverage` dell'area) e senza testo."""
    try:
        if page.get_text("text").strip():
            return False
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001
        return False
    area = abs(page.rect)
    if not infos or area <= 0:
        return False
    covered = sum(abs(pymupdf.Rect(i["bbox"]) & page.rect) for i in infos)
    return covered >= coverage * area


# ------------------------------------------------------------------ annotazioni
def list_annotations(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> list[dict]:
    """Annotazioni (escluse quelle di collegamento e i campi modulo)."""
    out = []
    for i in (range(pdf.page_count) if pages is None else pages):
        for a in pdf[i].annots() or []:
            info = a.info or {}
            out.append({
                "page": i + 1, "xref": a.xref, "type": a.type[1], "author": info.get("title", ""),
                "content": info.get("content", ""), "subject": info.get("subject", ""),
                "modified": info.get("modDate", ""), "rect": [round(v, 2) for v in a.rect],
            })
    return out


def export_annotations_json(pdf: pymupdf.Document, path: str, pages: Iterable[int] | None = None) -> int:
    items = list_annotations(pdf, pages)
    for it in items:
        it.pop("xref", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"annotations": items}, fh, ensure_ascii=False, indent=2)
    return len(items)


# ------------------------------------------------------------------ analisi
def analyze(pdf: pymupdf.Document, checks: Iterable[str] = CATEGORIES, pages: Iterable[int] | None = None,
            progress=None, ink_threshold: float = 0.01, use_tesseract: bool = True) -> dict[str, list[Proposal]]:
    """Analizza il documento e restituisce le proposte per categoria."""
    checks = set(checks)
    idx = list(range(pdf.page_count)) if pages is None else sorted(set(pages))
    result: dict[str, list[Proposal]] = {c: [] for c in CATEGORIES}
    excluded: set[int] = set()
    if "blank" in checks:
        _progress(progress, 0.0, _("Analisi: pagine vuote…"))
        details: dict = {}
        blanks = detect.find_blank_pages(pdf, idx, ink_threshold=ink_threshold, details=details,
                                         progress=_Sub(progress, 0.0, 0.35))
        for i in blanks:
            info = details[i]
            reason = (_("Pagina {n}: non valida (non renderizzabile o di dimensione nulla)").format(n=i + 1)
                      if info.invalid else
                      _("Pagina {n}: vuota (inchiostro {p}%)").format(n=i + 1, p=format_number(info.ink * 100, 2)))
            result["blank"].append(Proposal("blank", i, "delete", reason, {"invalid": info.invalid}))
        excluded = set(blanks)
    if "orientation" in checks:
        _progress(progress, 0.35, _("Analisi: orientamento…"))
        osd = use_tesseract and tesseract_available()
        for k, i in enumerate(idx):
            _progress(progress, 0.35 + 0.35 * k / max(1, len(idx)), _("Analisi: orientamento…"))
            if i in excluded:
                continue
            page = pdf[i]
            est = text_orientation(page)
            if est is not None:
                angle, conf, _n = est
                if conf >= MIN_CONFIDENCE:
                    fix = _rotation_fix(page, angle)
                    if fix:
                        result["orientation"].append(Proposal(
                            "orientation", i, "rotate",
                            _("Pagina {n}: testo ruotato, ruota di {g}°").format(n=i + 1, g=fix), {"degrees": fix}))
                continue
            if osd and is_image_only(page):
                found = scan_orientation(page)
                if found and found[0] and found[1] >= OSD_MIN_CONFIDENCE:
                    result["orientation"].append(Proposal(
                        "orientation", i, "rotate",
                        _("Pagina {n}: scansione ruotata, ruota di {g}°").format(n=i + 1, g=found[0]),
                        {"degrees": found[0], "confidence": found[1]}))
    if "sizes" in checks:
        _progress(progress, 0.7, _("Analisi: formati…"))
        sizes = {}
        for i in idx:
            if i in excluded:
                continue
            w, h = layout.displayed_crop_size(pdf, i)
            sizes[i] = (min(w, h), max(w, h))
        if sizes:
            counts = Counter((round(a), round(b)) for a, b in sizes.values())
            common = counts.most_common(1)[0][0]
            ref = next(i for i in idx if i in sizes and (round(sizes[i][0]), round(sizes[i][1])) == common)
            rw, rh = layout.displayed_crop_size(pdf, ref)
            for i, (a, b) in sizes.items():
                if abs(a - common[0]) > SIZE_TOLERANCE or abs(b - common[1]) > SIZE_TOLERANCE:
                    w, h = layout.displayed_crop_size(pdf, i)
                    result["sizes"].append(Proposal(
                        "sizes", i, "match",
                        _("Pagina {n}: {w} × {h} mm, diverso da {rw} × {rh} mm (pagina {r})").format(
                            n=i + 1, w=_mm(w), h=_mm(h), rw=_mm(rw), rh=_mm(rh), r=ref + 1),
                        {"ref": ref}))
    if "ocr" in checks:
        _progress(progress, 0.8, _("Analisi: pagine da riconoscere…"))
        for i in idx:
            if i not in excluded and is_image_only(pdf[i]):
                result["ocr"].append(Proposal("ocr", i, "ocr", _("Pagina {n}: solo immagini, nessun testo").format(n=i + 1)))
    if "annotations" in checks:
        _progress(progress, 0.9, _("Analisi: annotazioni…"))
        for a in list_annotations(pdf, idx):
            text = (a["content"] or a["subject"] or "").strip().replace("\n", " ")[:60]
            result["annotations"].append(Proposal(
                "annotations", a["page"] - 1, "remove_annot",
                _("Pagina {n}: {tipo} — {testo}").format(n=a["page"], tipo=a["type"], testo=text or "—"),
                {"xref": a["xref"]}, accepted=False))
    _progress(progress, 1.0, "")
    return result


class _Sub:
    """Avanzamento di una sotto-fase mappato in [start, end]."""

    def __init__(self, progress, start: float, end: float):
        self.p, self.a, self.b = progress, start, end

    def update(self, fraction=None, text=None):
        if self.p is not None:
            self.p.update(None if fraction is None else self.a + (self.b - self.a) * fraction, text)


# ------------------------------------------------------------------ applicazione
def apply_proposals(pdf: pymupdf.Document, proposals: Iterable[Proposal]) -> dict[str, int]:
    """Applica le proposte accettate (dentro un unico `document.change()`).

    Ordine: rimozione delle annotazioni, rotazioni, uniformazione dei formati,
    eliminazione delle pagine (per ultima, perché sposta gli indici). Le
    proposte "ocr" non modificano il documento.
    """
    props = [p for p in proposals if p.accepted]
    done = {"annotations": 0, "rotated": 0, "resized": 0, "deleted": 0}
    for p in props:
        if p.action == "remove_annot":
            page = pdf[p.page]
            for a in page.annots() or []:
                if a.xref == p.params.get("xref"):
                    page.delete_annot(a)
                    done["annotations"] += 1
                    break
    for p in props:
        if p.action == "rotate":
            page = pdf[p.page]
            page.set_rotation((page.rotation + int(p.params["degrees"])) % 360)
            done["rotated"] += 1
    for p in props:
        if p.action == "match":
            done["resized"] += len(layout.match_page_size(pdf, [p.page], int(p.params["ref"])))
    to_delete = sorted({p.page for p in props if p.action == "delete"})
    if to_delete:
        if len(to_delete) >= pdf.page_count:
            raise PageOpError(_("Impossibile eliminare tutte le pagine: il documento deve conservarne almeno una."))
        done["deleted"] = delete_pages(pdf, to_delete)
    return done


def can_modify(pdf: pymupdf.Document) -> bool:
    """False se i permessi del documento negano la modifica (analisi comunque consentita)."""
    try:
        perms = pdf.permissions
    except Exception:  # noqa: BLE001
        return True
    return perms == -1 or bool(perms & pymupdf.PDF_PERM_MODIFY)
