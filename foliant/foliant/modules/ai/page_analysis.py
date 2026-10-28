"""Analisi e pulizia guidata delle pagine (AI-24). Nessun widget.

Controlli locali (pagine bianche o quasi bianche, orientamento del testo, formato,
annotazioni, pagine da sottoporre a OCR), classificazione facoltativa con l'IA
delle sole pagine candidate e applicazione selettiva delle correzioni in un
unico passo annullabile.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from foliant.api import FoliantError, OperationCancelled, _
from foliant.core.engine import ENGINE_LOCK

from . import content
from .engine import Job, _system, budget_for, compact_ranges, parse_json_payload
from .providers import ChatRequest, ImagePart, Message

# problemi
BLANK = "blank"
NEAR_BLANK = "near_blank"
ROTATION = "rotation"
SIZE = "size"
ANNOTATIONS = "annotations"
NEEDS_OCR = "needs_ocr"
AI_PREFIX = "ai_"

# azioni
DELETE = "delete"
ROTATE = "rotate"
NORMALIZE_SIZE = "normalize_size"
EXTRACT_ANNOTATIONS = "extract_annotations"
OCR = "ocr"
NONE = "none"

LOCAL = "locale"
AI = "IA"

AI_CATEGORIES = ("transmittal", "separator", "duplicate", "other")

BLANK_COVERAGE = 0.0005       # frazione di pixel con inchiostro sotto cui la pagina è bianca
NEAR_BLANK_COVERAGE = 0.02    # pagina quasi bianca: poco inchiostro...
NEAR_BLANK_CHARS = 12         # ...e al massimo un numero di pagina o un timbro
SIZE_TOLERANCE = 2.0          # punti
AI_PAGE_CHARS = 1500          # testo massimo per pagina candidata


@dataclass
class Issue:
    page: int                 # indice 0-based
    problem: str
    evidence: str
    action: str
    origin: str = LOCAL       # "locale" | "IA"
    auto_select: bool = True
    angle: int = 0            # rotazione oraria da aggiungere (azione "rotate")


@dataclass
class ApplyResult:
    deleted: list[int] = field(default_factory=list)
    rotated: list[int] = field(default_factory=list)
    normalized: list[int] = field(default_factory=list)
    annotations_text: str = ""
    ocr_pages: list[int] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.deleted or self.rotated or self.normalized)


def problem_title(problem: str) -> str:
    titles = {
        BLANK: _("Pagine bianche"),
        NEAR_BLANK: _("Pagine quasi bianche"),
        ROTATION: _("Orientamento diverso"),
        SIZE: _("Formato diverso"),
        ANNOTATIONS: _("Pagine con annotazioni"),
        NEEDS_OCR: _("Pagine senza testo (OCR)"),
        AI_PREFIX + "transmittal": _("Pagine di trasmissione (IA)"),
        AI_PREFIX + "separator": _("Pagine separatrici (IA)"),
        AI_PREFIX + "duplicate": _("Pagine duplicate (IA)"),
        AI_PREFIX + "other": _("Altre pagine non pertinenti (IA)"),
    }
    return titles.get(problem, problem)


def action_title(action: str) -> str:
    return {
        DELETE: _("Elimina"),
        ROTATE: _("Ruota"),
        NORMALIZE_SIZE: _("Uniforma il formato"),
        EXTRACT_ANNOTATIONS: _("Estrai le annotazioni"),
        OCR: _("Esegui l'OCR"),
        NONE: _("Nessuna azione"),
    }.get(action, action)


def _percent(fraction: float) -> str:
    value = fraction * 100
    return (f"{value:.2f}" if value < 1 else f"{value:.1f}").replace(".", ",")


def _step(progress, fraction: float, text: str) -> None:
    if progress is None:
        return
    if hasattr(progress, "update"):
        progress.update(fraction, text)
    else:
        progress(fraction, text)


# ------------------------------------------------------------------ misure per pagina
@dataclass
class _PageInfo:
    index: int
    coverage: float
    text: str
    chars: int
    images: bool
    image_fraction: float
    rotation: int
    size: tuple[float, float]           # pagina non ruotata (cropbox)
    text_angle: int | None              # direzione dominante del testo (antioraria, 0/90/180/270)
    annotations: list[str]


def _ink_coverage(page: pymupdf.Page) -> float:
    """Frazione di pixel non bianchi in un rendering a bassa risoluzione (senza annotazioni)."""
    zoom = 36 / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False, annots=False)
    samples = pix.samples
    total = len(samples)
    if not total:
        return 0.0
    ink = len(bytes(samples).translate(None, bytes(range(200, 256))))   # pixel più scuri di 200
    return ink / total


def _text_angle(page: pymupdf.Page) -> int | None:
    weights: Counter = Counter()
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            n = sum(len(s.get("text", "").strip()) for s in line.get("spans", []))
            if not n:
                continue
            angle = int(round(math.degrees(math.atan2(-dy, dx)) / 90.0)) * 90 % 360
            weights[angle] += n
    if not weights:
        return None
    return weights.most_common(1)[0][0]


def _annotation_texts(page: pymupdf.Page) -> list[str]:
    out = []
    for a in page.annots() or []:
        if a.type[0] in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_POPUP):
            continue
        info = a.info or {}
        out.append((info.get("content") or "").strip() or a.type[1])
    return out


def _page_info(pdf: pymupdf.Document, index: int) -> _PageInfo:
    words, _excluded = content.safe_words(pdf, index)
    text = " ".join(w[4] for w in words)
    with ENGINE_LOCK:
        page = pdf[index]
        coverage = _ink_coverage(page)
        imgs = page.get_image_info()
        parea = abs(page.rect) or 1.0
        img_area = sum(abs(pymupdf.Rect(im["bbox"]) & page.rect) for im in imgs) if imgs else 0.0
        rotation = page.rotation % 360
        box = page.cropbox
        angle = _text_angle(page)
        annots = _annotation_texts(page)
    return _PageInfo(index, coverage, text, len("".join(text.split())), bool(imgs), img_area / parea,
                     rotation, (round(box.width, 1), round(box.height, 1)), angle, annots)


def _same_size(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= SIZE_TOLERANCE and abs(a[1] - b[1]) <= SIZE_TOLERANCE


def _majority_size(infos: list[_PageInfo]) -> tuple[float, float] | None:
    groups: list[tuple[tuple[float, float], int]] = []
    for info in infos:
        for k, (s, n) in enumerate(groups):
            if _same_size(s, info.size):
                groups[k] = (s, n + 1)
                break
        else:
            groups.append((info.size, 1))
    if not groups:
        return None
    best = max(groups, key=lambda g: g[1])
    if best[1] * 2 <= len(infos) and len(groups) > 1:
        # nessuna maggioranza chiara: si usa comunque il formato più frequente se unico
        counts = sorted((n for _s, n in groups), reverse=True)
        if counts[0] == counts[1]:
            return None
    return best[0]


def _size_name(size: tuple[float, float]) -> str:
    mm = lambda pt: round(pt * 25.4 / 72)  # noqa: E731
    return f"{mm(size[0])}×{mm(size[1])} mm"


# ------------------------------------------------------------------ analisi locale
def analyze(pdf: pymupdf.Document, progress=None) -> list[Issue]:
    """Controlli locali su tutte le pagine; nessun invio."""
    with ENGINE_LOCK:
        count = pdf.page_count
    infos: list[_PageInfo] = []
    for i in range(count):
        _step(progress, i / max(count, 1) * 0.9, _("Analisi della pagina {n} di {t}").format(n=i + 1, t=count))
        infos.append(_page_info(pdf, i))

    issues: list[Issue] = []
    blank_pages: set[int] = set()
    for info in infos:
        if info.chars == 0 and not info.images and info.coverage < BLANK_COVERAGE:
            blank_pages.add(info.index)
            issues.append(Issue(info.index, BLANK,
                                _("pagina coperta allo {p}%, senza testo né immagini").format(p=_percent(info.coverage)),
                                DELETE, LOCAL, True))
        elif (info.coverage < NEAR_BLANK_COVERAGE and info.chars <= NEAR_BLANK_CHARS
              and info.image_fraction < 0.05):
            if info.text.strip():
                ev = _("pagina coperta allo {p}%, contiene solo «{t}»").format(
                    p=_percent(info.coverage), t=info.text.strip()[:20])
            else:
                ev = _("pagina coperta allo {p}%, forse un timbro o un segno").format(p=_percent(info.coverage))
            blank_pages.add(info.index)
            issues.append(Issue(info.index, NEAR_BLANK, ev, DELETE, LOCAL, False))

    # orientamento: direzione del testo vista a schermo rispetto alla maggioranza
    visual = {}
    for info in infos:
        if info.text_angle is not None and info.chars > NEAR_BLANK_CHARS:
            visual[info.index] = (info.text_angle - info.rotation) % 360
    if visual:
        majority = Counter(visual.values()).most_common(1)[0][0]
        for i, v in visual.items():
            if v != majority:
                delta = (v - majority) % 360
                issues.append(Issue(i, ROTATION,
                                    _("testo ruotato di {g}° rispetto alla maggior parte delle pagine").format(g=delta),
                                    ROTATE, LOCAL, True, angle=delta))
    rotations = [info.rotation for info in infos if info.index not in blank_pages]
    if rotations:
        maj_rot = Counter(rotations).most_common(1)[0][0]
        for info in infos:
            if info.index in visual or info.index in blank_pages or info.rotation == maj_rot:
                continue
            delta = (maj_rot - info.rotation) % 360
            issues.append(Issue(info.index, ROTATION,
                                _("pagina ruotata di {r}°, la maggior parte è a {m}°").format(r=info.rotation, m=maj_rot),
                                ROTATE, LOCAL, True, angle=delta))

    # formato
    target = _majority_size(infos)
    if target is not None:
        for info in infos:
            if not _same_size(info.size, target):
                issues.append(Issue(info.index, SIZE,
                                    _("formato {a}, la maggior parte è {b}").format(
                                        a=_size_name(info.size), b=_size_name(target)),
                                    NORMALIZE_SIZE, LOCAL, False))

    # annotazioni
    for info in infos:
        if info.annotations:
            n = len(info.annotations)
            ev = _("1 annotazione") if n == 1 else _("{n} annotazioni").format(n=n)
            issues.append(Issue(info.index, ANNOTATIONS, ev, EXTRACT_ANNOTATIONS, LOCAL, False))

    # OCR: niente testo utilizzabile ma immagini presenti
    for info in infos:
        if info.index in blank_pages and info.image_fraction < 0.05:
            continue
        if info.images and not content.usable_text(info.text):
            issues.append(Issue(info.index, NEEDS_OCR,
                                _("immagini sul {p}% della pagina, nessun testo utilizzabile").format(
                                    p=_percent(info.image_fraction)),
                                OCR, LOCAL, True))
    _step(progress, 1.0, _("Analisi completata"))
    order = {p: k for k, p in enumerate((BLANK, NEAR_BLANK, ROTATION, SIZE, ANNOTATIONS, NEEDS_OCR))}
    issues.sort(key=lambda it: (order.get(it.problem, 99), it.page))
    return issues


def ai_candidates(pdf: pymupdf.Document, issues: list[Issue], max_chars: int = 400) -> list[int]:
    """Pagine da proporre alla classificazione IA: quasi bianche, senza testo o con poco testo."""
    blank = {it.page for it in issues if it.problem == BLANK}
    out = {it.page for it in issues if it.problem in (NEAR_BLANK, NEEDS_OCR)}
    with ENGINE_LOCK:
        count = pdf.page_count
    for i in range(count):
        if i in blank or i in out:
            continue
        words, _e = content.safe_words(pdf, i)
        if sum(len(w[4]) for w in words) < max_chars:
            out.add(i)
    return sorted(out - blank)


# ------------------------------------------------------------------ classificazione IA
SYSTEM = (
    "You are a document assistant that reviews pages of a PDF to help clean it up. "
    "For each page you receive, classify it as one of: transmittal (cover/fax/transmittal sheet), "
    "separator (separator or slip sheet with no real content), duplicate (same content as another page "
    "you received), other (normal or relevant page). Reply ONLY with a JSON array of objects "
    '{{"page": <page number>, "category": "transmittal|separator|duplicate|other", '
    '"reason": "<short reason in {language}>"}}, one per page. Never invent page numbers.'
)


def _payload(pdf: pymupdf.Document, pages: list[int], images_ok: bool, per_page: int):
    parts: list[str] = []
    images: list[ImagePart] = []
    image_pages: list[int] = []
    for i in pages:
        words, _e = content.safe_words(pdf, i)
        text = " ".join(w[4] for w in words)
        if content.usable_text(text) or not images_ok:
            parts.append(f'<page number="{i + 1}">\n{text[:per_page]}\n</page>')
        else:
            data, w, h = content.render_area(pdf, i, low_res=True, max_side=800)
            images.append(ImagePart(data, "image/png", w, h))
            image_pages.append(i)
            parts.append(f'<page number="{i + 1}">[image {len(images)}]</page>')
    return "\n".join(parts), images, image_pages


def describe_ai_send(pdf: pymupdf.Document, pages: list[int], profile=None) -> str:
    """Riga «Verrà inviato» per la classificazione IA."""
    if not pages:
        return _("Verrà inviato: nessuna pagina candidata")
    images_ok = bool(getattr(profile, "images", False))
    text, images, _ip = _payload(pdf, pages, images_ok, AI_PAGE_CHARS)
    chars = len(text)
    parts = [_("pagine {r}").format(r=compact_ranges(pages)),
             _("~{n} caratteri").format(n=f"{chars:,}".replace(",", "."))]
    if images:
        parts.append(_("1 immagine a bassa risoluzione") if len(images) == 1
                     else _("{n} immagini a bassa risoluzione").format(n=len(images)))
    s = _("Verrà inviato: ") + " · ".join(parts)
    if profile is not None:
        dest = _("elaborazione sul computer") if profile.is_local else profile.host
        s += f" → {dest} · {profile.model}"
    return s


def classify_with_ai(job: Job, pdf: pymupdf.Document, candidate_pages: list[int]) -> list[Issue]:
    """Classifica con l'IA solo le pagine candidate; restituisce voci di origine «IA»."""
    pages = sorted(set(candidate_pages))
    if not pages:
        return []
    if not content.extraction_allowed(pdf):
        raise FoliantError(content.blocked_reason(pdf))
    profile = job.profile()
    system = _system(SYSTEM, job.language)
    budget, out_tokens = budget_for(profile, system)
    per_page = max(200, min(AI_PAGE_CHARS, budget // len(pages)))
    job.step(0.1, _("Preparazione delle pagine"))
    body, images, _image_pages = _payload(pdf, pages, bool(profile.images), per_page)
    req = ChatRequest(system, [Message("user", body, images)], out_tokens)
    job.step(0.3, _("Classificazione con l'IA"))
    result = job.gateway.send(req, function="page-analysis", pages=pages, cancel=job.cancel,
                              on_wait=job.on_wait, profile=profile)
    if result.interrupted:
        raise OperationCancelled()
    data = parse_json_payload(result.text)
    if isinstance(data, dict):
        data = data.get("pages") or data.get("items") or [data]
    if not isinstance(data, list):
        raise FoliantError(_("La risposta non è nel formato atteso."))
    allowed = set(pages)
    issues: list[Issue] = []
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("page")) - 1
        except (TypeError, ValueError):
            continue
        category = str(item.get("category", "")).strip().lower()
        if index not in allowed or index in seen or category not in AI_CATEGORIES or category == "other":
            continue
        seen.add(index)
        reason = str(item.get("reason") or "").strip()
        evidence = reason or problem_title(AI_PREFIX + category)
        issues.append(Issue(index, AI_PREFIX + category, evidence, DELETE, AI, False))
    job.step(1.0, _("Classificazione completata"))
    return issues


# ------------------------------------------------------------------ applicazione
def _normalize_page(pdf: pymupdf.Document, index: int, size: tuple[float, float]) -> None:
    src = pymupdf.open()
    try:
        src.insert_pdf(pdf, from_page=index, to_page=index)
        rotation = src[0].rotation
        src[0].set_rotation(0)
        new = pdf.new_page(pno=index, width=size[0], height=size[1])
        new.show_pdf_page(new.rect, src, 0, keep_proportion=True)
        if rotation:
            new.set_rotation(rotation)
        pdf.delete_page(index + 1)
    finally:
        src.close()


def apply(document, issues: list[Issue]) -> ApplyResult:
    """Applica le azioni delle voci indicate in un solo passo annullabile.

    Ordine: rotazioni, formati, estrazione delle annotazioni, poi le eliminazioni
    in ordine decrescente. L'OCR non viene eseguito qui: le pagine sono restituite
    in `ocr_pages` per lo strumento OCR.
    """
    result = ApplyResult()
    deletes = sorted({it.page for it in issues if it.action == DELETE}, reverse=True)
    gone = set(deletes)
    rotations: dict[int, int] = {}
    for it in issues:
        if it.action == ROTATE and it.page not in gone and it.page not in rotations:
            rotations[it.page] = it.angle % 360
    normalize = sorted({it.page for it in issues if it.action == NORMALIZE_SIZE and it.page not in gone})
    extract = sorted({it.page for it in issues if it.action == EXTRACT_ANNOTATIONS})
    result.ocr_pages = sorted({it.page for it in issues if it.action == OCR and it.page not in gone})

    if extract:
        lines = []
        with ENGINE_LOCK:
            pdf = document.pdf
            for i in extract:
                if 0 <= i < pdf.page_count:
                    for t in _annotation_texts(pdf[i]):
                        lines.append(_("Pagina {n}: {t}").format(n=i + 1, t=t))
        result.annotations_text = "\n".join(lines)

    if not (deletes or rotations or normalize):
        return result
    with document.change(_("Pulizia pagine"), kind="pages") as pdf:
        count = pdf.page_count
        for i, angle in sorted(rotations.items()):
            if 0 <= i < count and angle:
                page = pdf[i]
                page.set_rotation((page.rotation + angle) % 360)
                result.rotated.append(i)
        if normalize:
            infos = []
            for i in range(count):
                box = pdf[i].cropbox
                infos.append(_PageInfo(i, 0, "", 0, False, 0, 0, (round(box.width, 1), round(box.height, 1)),
                                       None, []))
            target = _majority_size(infos)
            if target is not None:
                for i in normalize:
                    if 0 <= i < count and not _same_size(infos[i].size, target):
                        _normalize_page(pdf, i, target)
                        result.normalized.append(i)
        valid = [i for i in deletes if 0 <= i < count]
        if len(valid) >= count:
            raise FoliantError(_("Non è possibile eliminare tutte le pagine del documento."))
        for i in valid:
            pdf.delete_page(i)
            result.deleted.append(i)
    return result
