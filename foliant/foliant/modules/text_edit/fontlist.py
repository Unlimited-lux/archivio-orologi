"""Elenco dei font del documento, sostituzione globale e corrispondenze personali (TXT-11)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ...i18n import _
from . import engine, fonts
from .analysis import analyze_page, normalize_font_name, strip_subset
from .model import Run

STATE_FULL = "completo"
STATE_SUBSET = "sottoinsieme"
STATE_NOT_EMBEDDED = "non incorporato"

_SUBSET = re.compile(r"^[A-Z]{6}\+")


@dataclass
class FontEntry:
    name: str                       # nome senza prefisso di sottoinsieme
    basefont: str
    type: str
    encoding: str
    state: str
    pages: list[int] = field(default_factory=list)
    xrefs: list[int] = field(default_factory=list)
    has_tounicode: bool = True
    display_font: str = ""          # font usato per la visualizzazione (se non incorporato)

    @property
    def problem(self) -> bool:
        return self.state == STATE_NOT_EMBEDDED or not self.has_tounicode or bool(self.display_font)


def document_fonts(pdf: pymupdf.Document) -> list[FontEntry]:
    """Font usati dalle pagine (raggruppati per nome). Da chiamare tenendo ENGINE_LOCK."""
    entries: dict[str, FontEntry] = {}
    for pno in range(pdf.page_count):
        try:
            page_fonts = pdf.get_page_fonts(pno, full=True)
        except Exception:
            continue
        for f in page_fonts:
            xref, ext, ftype, basefont, _name, encoding = f[:6]
            name = strip_subset(basefont) or _("(senza nome)")
            entry = entries.get(name)
            if entry is None:
                if ext in ("n/a", ""):
                    state = STATE_NOT_EMBEDDED
                elif _SUBSET.match(basefont or ""):
                    state = STATE_SUBSET
                else:
                    state = STATE_FULL
                try:
                    tu = pdf.xref_get_key(xref, "ToUnicode")[0] != "null"
                except Exception:
                    tu = False
                display = ""
                if state == STATE_NOT_EMBEDDED:
                    sf = fonts.find_system_font(name)
                    display = "" if sf is not None else _("sostituito in visualizzazione")
                entry = FontEntry(name, basefont, ftype, encoding or "", state, has_tounicode=tu,
                                  display_font=display)
                if ftype == "Type1" and state == STATE_NOT_EMBEDDED and normalize_font_name(name).startswith(
                        ("helvetica", "times", "courier", "symbol", "zapfdingbats")):
                    entry.display_font = ""     # i 14 font standard non richiedono incorporamento
                    entry.has_tounicode = True
                entries[name] = entry
            if pno + 1 not in entry.pages:
                entry.pages.append(pno + 1)
            if xref not in entry.xrefs:
                entry.xrefs.append(xref)
    return sorted(entries.values(), key=lambda e: e.name.lower())


@dataclass
class ReplaceFontSummary:
    blocks: int = 0
    width_changed: list[tuple[int, str]] = field(default_factory=list)   # (pagina, inizio del testo)
    substitutions: list = field(default_factory=list)


def _uses_font(style, entry_name: str) -> bool:
    return fonts.family_of(style.font) == fonts.family_of(entry_name) or \
        normalize_font_name(style.font) == normalize_font_name(entry_name)


def replace_font(pdf: pymupdf.Document, font_name: str, target: str, pages=None) -> ReplaceFontSummary:
    """Sostituisce `font_name` con `target` nel testo di pagina (dentro `Document.change`)."""
    summary = ReplaceFontSummary()
    indices = range(pdf.page_count) if pages is None else pages
    for pno in indices:
        blocks = analyze_page(pdf[pno], pno)
        for block in reversed(blocks):
            if not block.editable or block.invisible:
                continue
            if not any(_uses_font(r.style, font_name) for p in block.paragraphs for r in p):
                continue
            paragraphs = [[Run(r.text, r.style.with_(font=target, font_xref=0)) if _uses_font(r.style, font_name)
                           else r for r in p] for p in block.paragraphs]
            old_w = max(ln.bbox.width for ln in block.lines)
            res = engine.replace_block(pdf, pno, block, paragraphs, keep_unchanged=False)
            summary.blocks += 1
            summary.substitutions.extend(res.substitutions)
            new_w = res.bbox.width if res.bbox and not res.bbox.is_empty else old_w
            if old_w > 0 and abs(new_w - old_w) / old_w > 0.05:
                summary.width_changed.append((pno + 1, block.text[:40]))
    return summary


def embed_font(pdf: pymupdf.Document, font_name: str, pages=None) -> ReplaceFontSummary:
    """Incorpora un font non incorporato usando lo stesso font installato nel sistema."""
    sf = fonts.find_system_font(font_name)
    if sf is None:
        raise engine.TextEditError(_("Il font «{f}» non è installato nel sistema con lo stesso nome: non può "
                                     "essere incorporato senza cambiare l'aspetto. Usa «Sostituisci».")
                                   .format(f=font_name))
    return replace_font(pdf, font_name, f"{sf.family} {sf.style}".strip(), pages)


# ---------------------------------------------------------------- corrispondenze
def mappings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "foliant" / "corrispondenze_font.json"


def _clean_mapping(mapping: dict) -> dict[str, str]:
    """Solo nomi di famiglia: senza il prefisso del sottoinsieme («ABCDEF+Calibri» -> «Calibri»), che
    viene dal documento e lo renderebbe riconoscibile (riservatezza)."""
    out: dict[str, str] = {}
    for k, v in (mapping or {}).items():
        k, v = strip_subset(str(k)).strip(), strip_subset(str(v)).strip()
        if k and v:
            out[k] = v
    return out


def load_mappings(path: Path | None = None) -> dict[str, str]:
    path = path or mappings_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return _clean_mapping(data) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_mappings(mapping: dict[str, str], path: Path | None = None) -> None:
    path = Path(path or mappings_path())
    mapping = _clean_mapping(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    fonts.set_user_mappings(mapping)


def forget_subset_prefixes(path: Path | None = None) -> bool:
    """Pulizia dei dati delle versioni precedenti: riscrive le corrispondenze salvate con il prefisso
    del sottoinsieme preso dai documenti. True se il file è stato riscritto."""
    path = Path(path or mappings_path())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if not any(_SUBSET.match(str(k)) or _SUBSET.match(str(v)) for k, v in data.items()):
        return False
    try:
        save_mappings(data, path)
    except OSError:
        return False
    return True
