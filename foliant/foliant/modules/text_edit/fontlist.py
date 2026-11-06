"""Elenco dei font del documento, sostituzione globale e corrispondenze personali (TXT-11)."""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ...core import platform
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


_STYLE_TOKENS = re.compile(r"(bolditalic|boldoblique|bold|italic|oblique|regular|roman|book|normal|"
                           r"psmt|mt|ps|bd|bi|it)$")
_STYLE_OF = {"bolditalic": {"bold", "italic"}, "boldoblique": {"bold", "italic"}, "bold": {"bold"},
             "bd": {"bold"}, "bi": {"bold", "italic"}, "italic": {"italic"}, "oblique": {"italic"},
             "it": {"italic"}}


def _name_key(name: str) -> tuple[str, frozenset]:
    """(famiglia, stile) di un nome di font: «Caladea-Bold» e «Caladea Bold» coincidono,
    «Caladea-Regular» e «Caladea» pure; «Caladea-Bold» e «Caladea-Italic» no."""
    key = normalize_font_name(name)
    style: set[str] = set()
    while True:
        m = _STYLE_TOKENS.search(key)
        if not m or m.start() == 0:
            break
        style |= _STYLE_OF.get(m.group(1), set())
        key = key[:m.start()]
    return key, frozenset(style)


def _uses_font(style, entry_name: str) -> bool:
    """True se il run usa proprio il font `entry_name` (non un'altra variante della stessa famiglia)."""
    return normalize_font_name(style.font) == normalize_font_name(entry_name) or \
        _name_key(style.font) == _name_key(entry_name)


def _target_paragraphs(block, font_name: str, target: str, xrefs: dict | None = None) -> list | None:
    """Paragrafi del blocco con i run del font scelto passati a `target`; None se il blocco non lo usa.

    Gli spazi in un altro font racchiusi tra testo del font scelto passano anch'essi a `target`,
    così il vecchio font non resta nel blocco per uno spazio. `xrefs`: stile -> (nome, xref) del font
    già incorporato da riusare."""
    if not any(_uses_font(r.style, font_name) for p in block.paragraphs for r in p):
        return None
    out = []
    for p in block.paragraphs:
        hits = [_uses_font(r.style, font_name) for r in p]
        for i, r in enumerate(p):
            if not hits[i] and r.text and not r.text.strip():
                prev = next((hits[j] for j in range(i - 1, -1, -1) if p[j].text.strip()), None)
                nxt = next((hits[j] for j in range(i + 1, len(p)) if p[j].text.strip()), None)
                hits[i] = (prev is not False) and (nxt is not False) and (prev or nxt)
        runs = []
        for r, hit in zip(p, hits):
            if not hit:
                runs.append(r)
                continue
            st = r.style.with_(font=target, font_xref=0)
            if xrefs:
                key = (st.bold, st.italic)
                if key in xrefs:
                    name, xref = xrefs[key]
                    st = st.with_(font=name, font_xref=xref)
            runs.append(Run(r.text, st))
        out.append(runs)
    return out


def _embed_targets(pdf: pymupdf.Document, plans: list, target: str) -> dict:
    """Incorpora una volta sola, per tutto il documento, ogni variante di `target` che servirà
    (sottoinsieme con tutti i caratteri usati). Restituisce (grassetto, corsivo) -> (nome, xref)."""
    resolver = fonts.FontResolver(pdf)
    groups: dict[tuple, dict] = {}
    for pno, _block, paragraphs in plans:
        for p in paragraphs:
            for r in p:
                st = r.style
                if st.font != target or st.font_xref:
                    continue
                key = (st.bold, st.italic)
                g = groups.get(key)
                if g is None:
                    try:
                        choice = resolver.primary(st, sample=r.text)
                    except Exception:
                        choice = None
                    g = groups[key] = {"choice": choice, "chars": set(), "pages": set()}
                g["chars"].update(r.text)
                g["pages"].add(pno)
    out: dict = {}
    for key, g in groups.items():
        choice = g["choice"]
        if choice is None or choice.base14 or choice.simulate_bold or choice.simulate_italic or \
                getattr(choice, "resource", None) is not None:
            continue    # nessun programma da incorporare, o stile simulato: percorso normale
        chars = {c for c in g["chars"] if choice.covers(c)}
        if not chars:
            continue
        try:
            data = choice.data()
            sub = engine._subset_font(data, chars)
            alias = "FTs%06x" % (zlib.crc32(sub) & 0xFFFFFF)
            xref = 0
            for pno in sorted(g["pages"]):
                # stesso programma: PyMuPDF riusa lo stesso oggetto font in tutte le pagine
                xref = pdf[pno].insert_font(fontname=alias, fontbuffer=sub)
            name = strip_subset(pdf.extract_font(xref, info_only=True)[0]) if xref else ""
        except Exception:
            continue
        if xref and name:
            out[key] = (name, xref)
    return out


def replace_font(pdf: pymupdf.Document, font_name: str, target: str, pages=None) -> ReplaceFontSummary:
    """Sostituisce `font_name` (proprio quel font, non tutta la famiglia) con `target` nel testo di
    pagina (dentro `Document.change`). Il nuovo font è incorporato una volta sola per il documento."""
    summary = ReplaceFontSummary()
    indices = list(range(pdf.page_count) if pages is None else pages)
    plans = []
    for pno in indices:
        for block in analyze_page(pdf[pno], pno):
            if not block.editable or block.invisible:
                continue
            paragraphs = _target_paragraphs(block, font_name, target)
            if paragraphs is not None:
                plans.append((pno, block, paragraphs))
    xrefs = _embed_targets(pdf, plans, target)
    for pno in indices:
        blocks = analyze_page(pdf[pno], pno)
        for block in reversed(blocks):
            if not block.editable or block.invisible:
                continue
            paragraphs = _target_paragraphs(block, font_name, target, xrefs)
            if paragraphs is None:
                continue
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
    return platform.app_dir("config") / "corrispondenze_font.json"


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
