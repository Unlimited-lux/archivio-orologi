"""Contenuto del documento da inviare all'assistente (AI-02, AI-15).

Regole di sicurezza:
- con il permesso di copia/estrazione negato non si estrae nulla;
- il testo sotto marcature di oscuramento non ancora applicate è escluso
  (anche dalle immagini, dove la marcatura viene resa coprente);
- il testo fuori dalla pagina, su livelli nascosti, trasparente o invisibile è
  escluso, tranne lo strato invisibile dell'OCR sopra le scansioni;
- nome del file, percorso, metadati e allegati non vengono mai letti qui.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pymupdf

from foliant.api import ENGINE_LOCK, FoliantError, _


class ContentBlocked(FoliantError):
    pass


def extraction_allowed(pdf) -> bool:
    """Estrazione consentita? Accetta un `Document` (preferito) o un documento del motore.

    Con un `Document` si usano i permessi del file all'apertura: dopo un annulla il
    documento interno è una copia non cifrata e non porta più le restrizioni.
    """
    if hasattr(pdf, "original_protection"):
        document = pdf
        if getattr(document, "access", None) == "owner":     # aperto o sbloccato dal proprietario
            return True
        prot = document.original_protection or {}
        perms = prot.get("permissions")
        if perms is not None:
            owner = bool(document.password) and prot.get("owner_pw") == document.password
            if not (perms & pymupdf.PDF_PERM_COPY) and not owner:
                return False
        pdf = document.pdf
    with ENGINE_LOCK:
        if not pdf.is_encrypted and not (pdf.metadata or {}).get("encryption"):
            return True
        perms = pdf.permissions
    return bool(perms & pymupdf.PDF_PERM_COPY)


def blocked_reason(pdf: pymupdf.Document) -> str:
    return _("I permessi di questo documento vietano la copia e l'estrazione del contenuto: "
             "le funzioni dell'assistente sono disattivate. Si riattivano aprendo il documento "
             "con la password del proprietario.")


@dataclass
class PageText:
    index: int
    label: str
    text: str
    excluded: list[str] = field(default_factory=list)   # motivi delle esclusioni
    ocr: bool = False
    confidence: float | None = None

    @property
    def has_text(self) -> bool:
        return usable_text(self.text)

    @property
    def number(self) -> int:
        return self.index + 1


def page_label(pdf: pymupdf.Document, index: int) -> str:
    with ENGINE_LOCK:
        try:
            label = pdf[index].get_label()
        except Exception:  # noqa: BLE001
            label = ""
    return label or str(index + 1)


def usable_text(text: str) -> bool:
    """Falso per pagine senza testo o con troppi caratteri non decodificabili."""
    stripped = "".join(text.split())
    if len(stripped) < 3:
        return False
    bad = sum(1 for c in stripped if c == "�" or (ord(c) < 32) or 0xE000 <= ord(c) <= 0xF8FF)
    return bad / len(stripped) < 0.3


def _redaction_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    out = []
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]) or []:
        vertices = annot.vertices
        if vertices and len(vertices) >= 4:
            for i in range(0, len(vertices) - 3, 4):
                out.append(pymupdf.Quad(vertices[i:i + 4]).rect)
        else:
            out.append(annot.rect)
    return out


def _hidden_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Testo invisibile (non OCR) o completamente trasparente.

    Il testo invisibile è considerato strato OCR solo se si trova sopra un'immagine
    che occupa una parte consistente della pagina (una scansione).
    """
    parea = abs(page.rect) or 1.0
    scans = [pymupdf.Rect(im["bbox"]) for im in page.get_image_info()
             if abs(pymupdf.Rect(im["bbox"]) & page.rect) > 0.25 * parea]
    out = []
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001
        return out
    for span in trace:
        box = pymupdf.Rect(span["bbox"])
        center = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        invisible = span.get("type") == 3 and not any(r.contains(center) for r in scans)
        transparent = (span.get("opacity") is not None and span.get("opacity") <= 0.01)
        if invisible or transparent:
            out.append(pymupdf.Rect(span["bbox"]))
    return out


def _inside(word_rect: pymupdf.Rect, rects: list[pymupdf.Rect]) -> bool:
    c = pymupdf.Point((word_rect.x0 + word_rect.x1) / 2, (word_rect.y0 + word_rect.y1) / 2)
    for r in rects:
        if r.contains(c) or (word_rect & r).get_area() > 0.5 * max(word_rect.get_area(), 1e-6):
            return True
    return False


def _words_text(words: list[tuple]) -> str:
    lines: list[str] = []
    prev = None
    current: list[str] = []
    for w in words:
        key = (w[5], w[6])
        if prev is not None and key != prev:
            lines.append(" ".join(current))
            current = []
            if key[0] != prev[0]:
                lines.append("")
        current.append(w[4])
        prev = key
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines).strip()


def safe_words(pdf: pymupdf.Document, index: int, clip: pymupdf.Rect | None = None) -> tuple[list[tuple], list[str]]:
    """Parole utilizzabili della pagina e motivi delle esclusioni."""
    with ENGINE_LOCK:
        page = pdf[index]
        area = page.rect if clip is None else (pymupdf.Rect(clip) & page.rect)
        words = page.get_text("words", clip=area, sort=True)
        redact = _redaction_rects(page)
        hidden = _hidden_rects(page)
    excluded: list[str] = []
    kept = []
    n_red = n_hid = 0
    for w in words:
        r = pymupdf.Rect(w[:4])
        if redact and _inside(r, redact):
            n_red += 1
            continue
        if hidden and _inside(r, hidden):
            n_hid += 1
            continue
        kept.append(w)
    if n_red:
        excluded.append(_("testo sotto marcature di oscuramento escluso"))
    if n_hid:
        excluded.append(_("testo nascosto escluso"))
    return kept, excluded


def extract_page(pdf: pymupdf.Document, index: int, *, include_comments: bool = False,
                 include_form_values: bool = False) -> PageText:
    words, excluded = safe_words(pdf, index)
    text = _words_text(words)
    extra: list[str] = []
    with ENGINE_LOCK:
        page = pdf[index]
        if include_comments:
            for a in page.annots() or []:
                if a.type[0] in (pymupdf.PDF_ANNOT_REDACT, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET):
                    continue
                content = (a.info or {}).get("content", "").strip()
                if content:
                    extra.append(_("[Commento] {t}").format(t=content))
        if include_form_values:
            for wdg in page.widgets() or []:
                if wdg.field_value not in (None, "", "Off"):
                    extra.append(_("[Campo {n}] {v}").format(n=wdg.field_name or "?", v=wdg.field_value))
    if extra:
        text = (text + "\n\n" + "\n".join(extra)).strip()
    return PageText(index, page_label(pdf, index), text, excluded)


def view_selections(view) -> list:
    """Selezione di testo della vista, una per pagina (`view.text_selections`)."""
    if view is None:
        return []
    sels = getattr(view, "text_selections", None)
    if sels is None:
        sels = [view.text_selection] if view.text_selection is not None else []
    return [s for s in sels if s is not None]


def selection_text(pdf: pymupdf.Document, page: int, rects: list[pymupdf.Rect]) -> str:
    """Testo di una selezione, applicando le stesse esclusioni delle pagine."""
    words, _excluded = safe_words(pdf, page)
    chosen = [w for w in words if any(_inside(pymupdf.Rect(w[:4]), [pymupdf.Rect(r)]) for r in rects)]
    return _words_text(chosen)


def surrounding_paragraph(pdf: pymupdf.Document, page: int, rects: list[pymupdf.Rect], limit: int = 1500) -> str:
    """Paragrafo (blocco) che contiene la selezione, entro `limit` caratteri."""
    words, _excluded = safe_words(pdf, page)
    if not rects:
        return ""
    target = pymupdf.Rect(rects[0])
    blocks: dict[int, list[tuple]] = {}
    for w in words:
        blocks.setdefault(w[5], []).append(w)
    hit = None
    for b, ws in blocks.items():
        if any(_inside(pymupdf.Rect(w[:4]), [target]) for w in ws):
            hit = b
            break
    if hit is None:
        return ""
    text = _words_text(blocks[hit])
    if len(text) > limit:
        sel = selection_text(pdf, page, rects)
        pos = max(text.find(sel), 0)
        start = max(0, pos - limit // 2)
        text = text[start:start + limit]
    return text


def textless_pages(pdf: pymupdf.Document, pages: list[int]) -> list[int]:
    """Pagine dell'ambito senza testo utilizzabile (AI-15)."""
    out = []
    for i in pages:
        words, _e = safe_words(pdf, i)
        text = _words_text(words)
        if not usable_text(text):
            out.append(i)
            continue
        with ENGINE_LOCK:
            page = pdf[i]
            imgs = page.get_image_info()
            parea = abs(page.rect)
        img_area = sum(abs(pymupdf.Rect(im["bbox"]) & page.rect) for im in imgs) if imgs else 0
        text_area = sum(abs(pymupdf.Rect(w[:4])) for w in words)
        if parea and img_area > 0.6 * parea and text_area < 0.002 * parea:
            out.append(i)
    return out


def ocr_page(pdf: pymupdf.Document, index: int, language: str = "ita+eng", dpi: int = 200,
             words_out: dict | None = None) -> tuple[str, float | None]:
    """OCR locale della pagina: (testo, affidabilità media 0-1 o None).

    Con `words_out` vi registra le parole riconosciute (x0, y0, x1, y1, parola) per
    l'eventuale salvataggio del livello di testo nel documento.
    """
    with ENGINE_LOCK:
        has_redactions = bool(_redaction_rects(pdf[index]))
    try:  # modulo OCR dell'applicazione, se presente (non con oscuramenti in sospeso)
        from foliant.modules.ocr import engine as ocr_engine  # type: ignore
        fn = getattr(ocr_engine, "recognize_page_text", None)
        if fn is not None and not has_redactions and words_out is None:
            res = fn(pdf, index, language=language)
            if isinstance(res, tuple):
                return res[0], res[1]
            return str(res), None
    except Exception:  # noqa: BLE001 - si ripiega sul motore
        pass
    with ENGINE_LOCK:
        page = pdf[index]
        try:
            tp = page.get_textpage_ocr(language=language, dpi=dpi, full=True)
        except Exception as e:  # noqa: BLE001
            raise FoliantError(_("OCR non riuscito: {e}. Controlla che Tesseract e la lingua «{l}» siano installati.")
                               .format(e=e, l=language)) from e
        words = page.get_text("words", textpage=tp, sort=True)
        redact = _redaction_rects(page)
    # il testo riconosciuto sotto le marcature di oscuramento non viene mai usato
    words = [w for w in words if not (redact and _inside(pymupdf.Rect(w[:4]), redact))]
    if words_out is not None:
        words_out[index] = [tuple(w[:5]) for w in words]
    return _words_text(words), None


def ocr_area(pdf: pymupdf.Document, index: int, rect, language: str = "ita+eng") -> str:
    """OCR locale di un'area, escludendo il testo sotto le marcature di oscuramento."""
    with ENGINE_LOCK:
        page = pdf[index]
        try:
            tp = page.get_textpage_ocr(language=language, full=True)
        except Exception as e:  # noqa: BLE001
            raise FoliantError(_("OCR non riuscito: {e}").format(e=e)) from e
        words = page.get_text("words", textpage=tp, clip=pymupdf.Rect(rect), sort=True)
        redact = _redaction_rects(page)
    words = [w for w in words if not (redact and _inside(pymupdf.Rect(w[:4]), redact))]
    return _words_text(words)


def sanitized_copy(document) -> bytes:
    """Copia di lavoro senza testo nascosto e con le marcature di oscuramento applicate.

    Usata per le elaborazioni che leggono il PDF intero (traduzione in un nuovo PDF):
    il contenuto escluso dall'invio non è più presente nella copia.
    """
    with ENGINE_LOCK:
        data = document.pdf.tobytes(garbage=0, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    copy = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page in copy:
            hidden = _hidden_rects(page)
            for r in hidden:
                page.add_redact_annot(r)
            if hidden or _redaction_rects(page):
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        return copy.tobytes(garbage=1, deflate=True)
    finally:
        copy.close()


def ocr_languages_available() -> list[str]:
    import shutil
    import subprocess
    exe = shutil.which("tesseract")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.strip() for l in out.splitlines()[1:] if l.strip()]


def render_area(pdf: pymupdf.Document, index: int, rect: pymupdf.Rect | None = None, *,
                max_side: int = 2048, base_dpi: int = 150, low_res: bool = False) -> tuple[bytes, int, int]:
    """Immagine PNG di un'area (o della pagina), con le marcature di oscuramento rese coprenti."""
    with ENGINE_LOCK:
        page = pdf[index]
        clip = page.rect if rect is None else (pymupdf.Rect(rect) & page.rect)
        if clip.is_empty:
            raise FoliantError(_("L'area scelta è vuota."))
        zoom = (72 if low_res else base_dpi) / 72.0
        w, h = clip.width * zoom, clip.height * zoom
        if max(w, h) > max_side:
            zoom *= max_side / max(w, h)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False, annots=False)
        redact = [r & clip for r in _redaction_rects(page)]
    if any(not r.is_empty for r in redact):
        for r in redact:
            if r.is_empty:
                continue
            ir = pymupdf.IRect(int((r.x0 - clip.x0) * zoom) - 1, int((r.y0 - clip.y0) * zoom) - 1,
                               int((r.x1 - clip.x0) * zoom) + 2, int((r.y1 - clip.y0) * zoom) + 2)
            ir = pymupdf.IRect(ir.x0 + pix.x, ir.y0 + pix.y, ir.x1 + pix.x, ir.y1 + pix.y)
            pix.set_rect(ir & pix.irect, (0, 0, 0))
    return pix.tobytes("png"), pix.width, pix.height


def scale_image_bytes(data: bytes, max_side: int = 2048) -> tuple[bytes, int, int, str]:
    """Normalizza un'immagine esterna in PNG con lato massimo `max_side`."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    img = QImage.fromData(data)
    if img.isNull():
        raise FoliantError(_("Immagine non supportata o danneggiata."))
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data()), img.width(), img.height(), "image/png"


def content_fingerprint(pdf: pymupdf.Document) -> str:
    """Impronta del contenuto (testo e grafica delle pagine), stabile tra rinomini e salvataggi."""
    h = hashlib.sha256()
    with ENGINE_LOCK:
        h.update(str(pdf.page_count).encode())
        for page in pdf:
            h.update(repr(tuple(page.rect)).encode())
            try:
                h.update(page.read_contents())
            except Exception:  # noqa: BLE001
                pass
            for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT]) or []:
                h.update(repr(tuple(a.rect)).encode())
    return h.hexdigest()


def parse_page_range(text: str, page_count: int) -> list[int]:
    """«1-3, 7» -> [0, 1, 2, 6]. Solleva ValueError con un messaggio chiaro."""
    text = (text or "").strip()
    if not text:
        raise ValueError(_("Indica un intervallo di pagine, ad esempio «1-3, 7»."))
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part or "–" in part:
            a, _sep, b = part.replace("–", "-").partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                raise ValueError(_("Intervallo non valido: «{p}».").format(p=part)) from None
        else:
            try:
                lo = hi = int(part)
            except ValueError:
                raise ValueError(_("Intervallo non valido: «{p}».").format(p=part)) from None
        if lo < 1 or hi < lo or hi > page_count:
            raise ValueError(_("Le pagine {p} non esistono: il documento ha {n} pagine.").format(p=part, n=page_count))
        for i in range(lo - 1, hi):
            if i not in out:
                out.append(i)
    if not out:
        raise ValueError(_("Indica un intervallo di pagine, ad esempio «1-3, 7»."))
    return out


_STOPWORDS = {
    "it": {"il", "lo", "la", "gli", "le", "di", "che", "e", "è", "per", "con", "non", "una", "del", "della", "sono"},
    "en": {"the", "and", "of", "to", "is", "in", "that", "for", "with", "are", "this", "not", "be", "on"},
    "fr": {"le", "la", "les", "de", "des", "et", "est", "pour", "dans", "une", "que", "pas", "sur", "du"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "mit", "ein", "eine", "für", "von", "zu", "den", "auf"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "es", "para", "con", "una", "por", "del", "no"},
    "pt": {"o", "a", "os", "as", "de", "que", "e", "é", "para", "com", "uma", "não", "do", "da"},
}


def guess_language(text: str) -> str | None:
    """Lingua probabile (euristica locale sulle parole frequenti), None se incerta."""
    import re as _re
    words = _re.findall(r"[^\W\d_]+", (text or "").lower())
    if len(words) < 4:
        return None
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] < 2 or ranked[0] < ranked[1] * 1.5:
        return None
    return best
