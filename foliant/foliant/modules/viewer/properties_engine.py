"""Logica pura delle proprietà del documento (senza interfaccia).

Tutte le funzioni che leggono il motore PDF vanno chiamate tenendo
`ENGINE_LOCK` (lo fa `collect_info`), tranne `collect_fonts` che lo prende
pagina per pagina per poter girare in un'attività in background.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pymupdf

from foliant.api import ENGINE_LOCK, _, format_date, format_number, language

# ------------------------------------------------------------------ permessi
ALL_PERMISSIONS = -1

# (bit del motore, etichetta) nell'ordine di /P (ISO 32000, tabella 22)
PERMISSION_BITS = (
    (pymupdf.PDF_PERM_PRINT, "Stampa del documento"),
    (pymupdf.PDF_PERM_PRINT_HQ, "Stampa ad alta qualità"),
    (pymupdf.PDF_PERM_MODIFY, "Modifica del contenuto"),
    (pymupdf.PDF_PERM_COPY, "Copia ed estrazione di testo e immagini"),
    (pymupdf.PDF_PERM_ANNOTATE, "Commenti e campi modulo"),
    (pymupdf.PDF_PERM_FORM, "Compilazione dei moduli"),
    (pymupdf.PDF_PERM_ACCESSIBILITY, "Accessibilità (tecnologie assistive)"),
    (pymupdf.PDF_PERM_ASSEMBLE, "Assemblaggio (inserire, ruotare, eliminare pagine)"),
)

_METHOD_NAMES = {
    pymupdf.PDF_ENCRYPT_AES_256: "AES 256 bit",
    pymupdf.PDF_ENCRYPT_AES_128: "AES 128 bit",
    pymupdf.PDF_ENCRYPT_RC4_128: "RC4 128 bit",
    pymupdf.PDF_ENCRYPT_RC4_40: "RC4 40 bit",
}


@dataclass
class Security:
    encrypted: bool
    method: str            # descrizione leggibile del metodo ("" se non cifrato)
    permissions: int       # bit /P effettivi per la sessione (-1 = tutto consentito)
    owner_access: bool     # tutte le operazioni consentite (non cifrato o password proprietario)


def document_security(document) -> Security:
    """Stato di protezione effettivo di un `Document` (da chiamare con ENGINE_LOCK).

    Dopo una modifica il documento interno è una copia non cifrata (la
    protezione si riapplica al salvataggio): in quel caso i permessi validi per
    la sessione sono quelli rilevati all'apertura.
    """
    pdf = document.pdf
    description = (pdf.metadata or {}).get("encryption") or ""
    original = getattr(document, "original_protection", {}) or {}
    if description:
        perms = int(pdf.permissions)
        return Security(True, _describe_method(description), perms, _all_allowed(perms))
    if original.get("encryption") not in (None, pymupdf.PDF_ENCRYPT_NONE, pymupdf.PDF_ENCRYPT_KEEP):
        perms = int(original.get("permissions", ALL_PERMISSIONS))
        save = getattr(document, "save_options", {}) or {}
        if save.get("encryption") in (None, pymupdf.PDF_ENCRYPT_NONE):
            # protezione rimossa (possibile solo con la password proprietario)
            perms = ALL_PERMISSIONS
        method = _METHOD_NAMES.get(original["encryption"], "")
        return Security(True, method, perms, _all_allowed(perms))
    return Security(False, "", ALL_PERMISSIONS, True)


def _all_allowed(perms: int) -> bool:
    return all(perms & bit for bit, _label in PERMISSION_BITS)


def _describe_method(description: str) -> str:
    """'Standard V5 R6 256-bit AES' → 'AES 256 bit (Standard, revisione 6)'."""
    d = description.upper()
    bits = re.search(r"(\d+)-BIT", d)
    algo = "AES" if "AES" in d else ("RC4" if "RC4" in d else "")
    rev = re.search(r"\bR(\d)\b", d)
    if not algo:
        return description
    text = f"{algo} {bits.group(1)} bit" if bits else algo
    if rev:
        text += " (" + _("Standard, revisione {n}").format(n=rev.group(1)) + ")"
    return text


def permission_rows(perms: int) -> list[tuple[str, bool]]:
    """[(etichetta tradotta, consentito)] per ogni permesso."""
    return [(_(label), bool(perms & bit)) for bit, label in PERMISSION_BITS]


# --------------------------------------------------------------------- date
_DATE_RE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:\s*([Zz+\-])\s*(\d{2})?'?(\d{2})?'?)?\s*$")


def parse_pdf_date(value: str | None) -> datetime | None:
    """Data PDF ("D:YYYYMMDDHHmmSS+HH'mm'", componenti finali facoltative)."""
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, sign, tzh, tzm = m.groups()
    try:
        tz = None
        if sign:
            if sign in "Zz":
                tz = timezone.utc
            else:
                offset = timedelta(hours=int(tzh or 0), minutes=int(tzm or 0))
                tz = timezone(offset if sign == "+" else -offset)
        return datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0),
                        int(second or 0), tzinfo=tz)
    except ValueError:
        return None


def format_pdf_date(value: str | None) -> str:
    """Data leggibile; il testo originale se non interpretabile, "" se assente."""
    dt = parse_pdf_date(value)
    if dt is None:
        return (value or "").strip()
    text = format_date(dt, time=True, seconds=True)
    if dt.tzinfo is not None:
        off = dt.utcoffset() or timedelta(0)
        if off == timedelta(0):
            text += " UTC"
        else:
            total = int(off.total_seconds() // 60)
            sign = "+" if total >= 0 else "-"
            total = abs(total)
            text += f" UTC{sign}{total // 60:02d}:{total % 60:02d}"
    return text


# ------------------------------------------------------------ dimensioni
def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return _("{n} byte").format(n=format_number(num_bytes))
    if num_bytes < 1024 * 1024:
        text, unit = format_number(num_bytes / 1024, 1), "KB"
    else:
        text, unit = format_number(num_bytes / (1024 * 1024), 2), "MB"
    return f"{text} {unit} ({_('{n} byte').format(n=format_number(num_bytes))})"


# formati noti in millimetri (lato corto, lato lungo)
PAPER_SIZES_MM = (
    ("A0", 841, 1189), ("A1", 594, 841), ("A2", 420, 594), ("A3", 297, 420), ("A4", 210, 297),
    ("A5", 148, 210), ("A6", 105, 148), ("B4", 250, 353), ("B5", 176, 250),
    ("Letter", 215.9, 279.4), ("Legal", 215.9, 355.6), ("Tabloid", 279.4, 431.8),
    ("Executive", 184.15, 266.7), ("Statement", 139.7, 215.9),
)
MM_PER_PT = 25.4 / 72


def paper_name(width_pt: float, height_pt: float, tolerance_mm: float = 1.5) -> str | None:
    """Nome del formato (es. "A4") se le misure corrispondono, altrimenti None."""
    a, b = sorted((width_pt * MM_PER_PT, height_pt * MM_PER_PT))
    for name, short, long in PAPER_SIZES_MM:
        if abs(a - short) <= tolerance_mm and abs(b - long) <= tolerance_mm:
            return name
    return None


def describe_page_size(width_pt: float, height_pt: float) -> str:
    mm = f"{format_number(width_pt * MM_PER_PT, 1)} × {format_number(height_pt * MM_PER_PT, 1)} mm"
    pt = f"{format_number(width_pt, 2)} × {format_number(height_pt, 2)} pt"
    text = f"{mm} ({pt})"
    name = paper_name(width_pt, height_pt)
    if name:
        orient = _("verticale") if height_pt >= width_pt else _("orizzontale")
        text = f"{name} {orient} — {text}"
    return text


# ------------------------------------------------------------------- font
@dataclass(frozen=True)
class FontInfo:
    name: str          # nome senza prefisso di sottoinsieme
    font_type: str     # Type1, TrueType, Type0, Type3…
    encoding: str
    embedding: str     # "embedded" | "subset" | "not_embedded"

    def embedding_label(self) -> str:
        return {
            "embedded": _("Incorporato"),
            "subset": _("Incorporato (sottoinsieme)"),
            "not_embedded": _("Non incorporato"),
        }[self.embedding]


_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")


def font_from_entry(entry: tuple) -> FontInfo:
    """Voce di `page.get_fonts(full=True)`: (xref, ext, tipo, basefont, nome, codifica, …)."""
    _xref, ext, ftype, basefont, refname, encoding = entry[:6]
    base = basefont or refname or "?"
    subset = bool(_SUBSET_RE.match(base))
    name = _SUBSET_RE.sub("", base)
    if not ext or ext == "n/a":
        embedding = "not_embedded"
    else:
        embedding = "subset" if subset else "embedded"
    return FontInfo(name, ftype or "", encoding or "", embedding)


def collect_fonts(pdf: pymupdf.Document, progress=None) -> list[FontInfo]:
    """Font usati in tutte le pagine, senza duplicati, ordinati per nome."""
    seen: dict[int | tuple, FontInfo] = {}
    with ENGINE_LOCK:
        count = pdf.page_count
    for i in range(count):
        with ENGINE_LOCK:
            entries = pdf[i].get_fonts(full=True)
        for entry in entries:
            info = font_from_entry(entry)
            key = entry[0] if entry[0] else (info.name, info.font_type, info.encoding)
            seen.setdefault(key, info)
        if progress is not None and (i % 10 == 0 or i == count - 1):
            progress.update((i + 1) / max(1, count), _("Pagina {n} di {t}").format(n=i + 1, t=count))
    unique = sorted(set(seen.values()), key=lambda f: (f.name.lower(), f.font_type, f.encoding, f.embedding))
    return unique


# --------------------------------------------------------------- raccolta
@dataclass
class DocumentInfo:
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    created: str = ""
    modified: str = ""
    path: str = ""
    file_size: int | None = None
    unsaved_changes: bool = False
    page_count: int = 0
    current_page: int = 0
    page_size: str = ""
    pdf_version: str = ""
    fast_web_view: bool = False
    tagged: bool = False
    opening: list = field(default_factory=list)   # righe della sezione «Apertura»
    security: Security = field(default_factory=lambda: Security(False, "", ALL_PERMISSIONS, True))
    objects: int = 0
    attachments: int = 0
    layers: int = 0
    form_fields: int = 0
    bookmarks: int = 0
    fonts: list[FontInfo] = field(default_factory=list)


def _catalog_has(pdf: pymupdf.Document, key: str) -> bool:
    try:
        kind, _value = pdf.xref_get_key(pdf.pdf_catalog(), key)
    except Exception:
        return False
    return kind not in ("null", None)


def collect_info(document, current_page: int = 0, fonts: list[FontInfo] | None = None) -> DocumentInfo:
    """Raccoglie i dati del documento (sola lettura). Prende ENGINE_LOCK."""
    path = str(document.path) if getattr(document, "path", None) else ""
    size = None
    if path:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
    with ENGINE_LOCK:
        pdf = document.pdf
        meta = pdf.metadata or {}
        info = DocumentInfo(
            title=meta.get("title") or "",
            author=meta.get("author") or "",
            subject=meta.get("subject") or "",
            keywords=meta.get("keywords") or "",
            creator=meta.get("creator") or "",
            producer=meta.get("producer") or "",
            created=format_pdf_date(meta.get("creationDate")),
            modified=format_pdf_date(meta.get("modDate")),
            path=path,
            file_size=size,
            unsaved_changes=bool(getattr(document, "is_modified", False)),
            page_count=pdf.page_count,
            pdf_version=(meta.get("format") or "").replace("PDF-", "PDF "),
        )
        index = min(max(0, current_page), pdf.page_count - 1)
        info.current_page = index
        rect = pdf[index].rect          # dimensione come appare (con /Rotate)
        info.page_size = describe_page_size(rect.width, rect.height)
        try:
            info.fast_web_view = bool(pdf.is_fast_webaccess)
        except Exception:
            info.fast_web_view = False
        info.tagged = _catalog_has(pdf, "StructTreeRoot") or _catalog_has(pdf, "MarkInfo")
        info.security = document_security(document)
        info.objects = max(0, pdf.xref_length() - 1)
        try:
            info.attachments = pdf.embfile_count()
        except Exception:
            info.attachments = 0
        try:
            info.layers = len(pdf.get_ocgs() or {})
        except Exception:
            info.layers = 0
        try:
            form = pdf.is_form_pdf
            info.form_fields = int(form) if form else 0
        except Exception:
            info.form_fields = 0
        try:
            info.bookmarks = len(pdf.get_toc(simple=True))
        except Exception:
            info.bookmarks = 0
    if fonts is None:
        fonts = collect_fonts(document.pdf)
    info.fonts = fonts
    return info


# ----------------------------------------------------------------- schede
def _yes_no(value: bool) -> str:
    return _("Sì") if value else _("No")


def file_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    none = "—"
    size = format_size(info.file_size) if info.file_size is not None else _("non salvato")
    if info.unsaved_changes and info.file_size is not None:
        size += " — " + _("modifiche non salvate")
    return [
        (_("Percorso"), info.path or _("non salvato")),
        (_("Dimensione del file"), size),
        (_("Versione PDF"), info.pdf_version or none),
        (_("Creato con"), info.creator or none),
        (_("Convertito in PDF da"), info.producer or none),
        (_("Data di creazione"), info.created or none),
        (_("Ultima modifica"), info.modified or none),
    ]


def content_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    none = "—"
    return [
        (_("Titolo"), info.title or none),
        (_("Autore"), info.author or none),
        (_("Oggetto"), info.subject or none),
        (_("Parole chiave"), info.keywords or none),
        (_("Pagine"), format_number(info.page_count)),
        (_("Formato della pagina {n}").format(n=info.current_page + 1), info.page_size),
        (_("Struttura logica per l'accessibilità"), _("presente") if info.tagged else _("assente")),
        (_("Segnalibri"), format_number(info.bookmarks)),
        (_("Allegati"), format_number(info.attachments)),
        (_("Livelli"), format_number(info.layers)),
        (_("Campi modulo"), format_number(info.form_fields) if info.form_fields else _("Nessuno")),
        (_("Oggetti PDF"), format_number(info.objects)),
    ]


def opening_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    rows = [(_("Caricamento progressivo dalla rete"), _("previsto") if info.fast_web_view else _("non previsto"))]
    rows.extend(info.opening or [(_("Impostazioni del documento"), _("nessuna: si usano le preferenze"))])
    return rows


# compatibilità con il codice precedente
def description_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    return file_rows(info) + content_rows(info)


def security_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    sec = info.security
    rows = [(_("Cifratura"), (sec.method or _("Sì")) if sec.encrypted else _("Nessuna"))]
    if sec.encrypted:
        rows.append((_("Accesso"), _("Tutte le operazioni consentite") if sec.owner_access
                     else _("Con restrizioni")))
    for label, allowed in permission_rows(sec.permissions):
        rows.append((label, _("Consentito") if allowed else _("Non consentito")))
    return rows


def advanced_rows(info: DocumentInfo) -> list[tuple[str, str]]:
    return [
        (_("Oggetti PDF"), format_number(info.objects)),
        (_("Allegati"), format_number(info.attachments)),
        (_("Livelli"), format_number(info.layers)),
        (_("Campi modulo"), format_number(info.form_fields) if info.form_fields else _("Nessuno")),
        (_("Segnalibri"), format_number(info.bookmarks)),
        (_("Font"), format_number(len(info.fonts))),
    ]


def info_to_text(info: DocumentInfo) -> str:
    """Testo semplice per gli appunti."""
    lines: list[str] = []
    for title, rows in ((_("File"), file_rows(info)), (_("Contenuto"), content_rows(info)),
                        (_("Protezione"), security_rows(info)), (_("Apertura"), opening_rows(info))):
        lines.append(f"[{title}]")
        lines.extend(f"{label}: {value}" for label, value in rows)
        lines.append("")
    lines.append(f"[{_('Caratteri')}]")
    for f in info.fonts:
        lines.append("\t".join((f.name, f.font_type, f.encoding or "—", f.embedding_label())))
    return "\n".join(lines).rstrip() + "\n"
