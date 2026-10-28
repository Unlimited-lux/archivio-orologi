"""Operazioni in blocco (BAT): coda, destinazione, nomi, esecuzione e rapporto.

Logica pura, senza interfaccia. Le operazioni semplici (comprimi, proteggi,
filigrana, sfondo, intestazioni, numerazione, Bates, unione, rimozione
password, stampa) sono implementate qui con il motore PDF; OCR e conversione
usano, se presenti, i motori dei moduli `ocr` e `convert`.

Regole: i file sorgente non vengono mai modificati (salvo «sostituisci gli
originali»); ogni scrittura è atomica; le password non compaiono mai in
rapporti, registri o file di sequenza.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pymupdf

from foliant.api import ENGINE_LOCK, DocumentError, OperationCancelled, _, atomic_write

INPUT_EXTENSIONS = (".pdf",)
MAX_CANDIDATES = 20
MARK_KEY = "/FoliantMark"
MARK_KINDS = ("watermark", "background", "header_footer", "bates")


# =============================================================================
# Dipendenze facoltative (moduli OCR e Converti)
# =============================================================================
def _ocr_function() -> Callable | None:
    try:
        from foliant.modules.ocr.engine import ocr_pdf_bytes
    except Exception:
        return None
    return ocr_pdf_bytes


def _convert_function() -> Callable | None:
    try:
        from foliant.modules.convert.engine import convert_pdf
    except Exception:
        return None
    return convert_pdf


def _print_command(path: str, printer: str | None, copies: int = 1, duplex: str = "none",
                   pages: str = "") -> list[str]:
    """Comando di stampa (CUPS). Sostituibile nei test."""
    lp = shutil.which("lp")
    if lp is None:
        raise BatchError("DEP-02", _("Il comando di stampa «lp» (CUPS) non è installato."))
    cmd = [lp, "-n", str(max(1, int(copies)))]
    if printer:
        cmd += ["-d", printer]
    if duplex == "long":
        cmd += ["-o", "sides=two-sided-long-edge"]
    elif duplex == "short":
        cmd += ["-o", "sides=two-sided-short-edge"]
    if pages:
        cmd += ["-P", pages]
    cmd.append(path)
    return cmd


def _run_print(cmd: list[str]) -> None:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise BatchError("PRN-01", _("Stampa non riuscita: {e}").format(e=e)) from e
    if done.returncode != 0:
        raise BatchError("PRN-01", _("Stampa non riuscita: {e}").format(e=(done.stderr or "").strip()))


class BatchError(Exception):
    """Errore di un singolo file con codice stabile."""

    def __init__(self, code: str, message: str, status: str = "error"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# =============================================================================
# Coda dei file di input (BAT-01)
# =============================================================================
@dataclass
class QueueItem:
    path: str
    password: str | None = None
    pages: int = 0
    size: int = 0
    encrypted: bool = False
    needs_password: bool = False
    signed: bool = False
    pdfa: bool = False
    damaged: bool = False
    has_text: bool = False
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def status_text(self) -> str:
        if self.error:
            return self.error
        if self.damaged:
            return _("danneggiato")
        parts = []
        if self.needs_password:
            parts.append(_("richiede password") if not self.password else _("password indicata"))
        elif self.encrypted:
            parts.append(_("permessi limitati"))
        if self.signed:
            parts.append(_("firmato"))
        if self.pdfa:
            parts.append("PDF/A")
        return ", ".join(parts)


def analyze_file(path: str) -> QueueItem:
    """Analisi preliminare di un file della coda."""
    item = QueueItem(path=path)
    try:
        item.size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        item.error = _("non leggibile: {e}").format(e=e.strerror or e)
        return item
    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            item.damaged = True
            return item
        try:
            item.encrypted = bool(doc.needs_pass) or bool((doc.metadata or {}).get("encryption"))
            item.needs_password = bool(doc.needs_pass)
            item.damaged = bool(getattr(doc, "is_repaired", False))
            if not doc.needs_pass:
                item.pages = doc.page_count
                try:
                    item.signed = doc.get_sigflags() > 0
                except Exception:
                    item.signed = False
                try:
                    xml = doc.get_xml_metadata() or ""
                except Exception:
                    xml = ""
                item.pdfa = "pdfaid" in xml.lower()
                item.has_text = any(doc[i].get_text("text").strip() for i in range(min(doc.page_count, 20)))
        finally:
            doc.close()
    return item


def natural_key(path: str):
    """Chiave di ordinamento naturale: doc2 precede doc10."""
    name = os.path.basename(path).casefold()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def collect_inputs(paths: list[str], recursive: bool = True, extensions=INPUT_EXTENSIONS,
                   existing: list[str] = ()) -> tuple[list[str], list[tuple[str, str]]]:
    """File da aggiungere alla coda: percorsi canonici senza duplicati.

    Restituisce (nuovi_percorsi, ignorati[(percorso, motivo)])."""
    exts = tuple(e.lower() for e in extensions)
    seen = {os.path.realpath(p) for p in existing}
    found: list[str] = []
    ignored: list[tuple[str, str]] = []

    def add_file(p: str) -> None:
        real = os.path.realpath(p)
        if not real.lower().endswith(exts):
            ignored.append((p, _("formato non ammesso per l'operazione")))
            return
        if not os.access(real, os.R_OK):
            ignored.append((p, _("file non leggibile")))
            return
        if real in seen:
            return
        seen.add(real)
        found.append(real)

    for p in paths:
        p = os.path.expanduser(str(p))
        if os.path.isdir(p):
            visited: set[str] = set()
            for root, dirs, files in os.walk(p, followlinks=True, onerror=lambda e: ignored.append(
                    (getattr(e, "filename", "") or p, _("cartella non leggibile")))):
                real_root = os.path.realpath(root)
                if real_root in visited:
                    dirs[:] = []
                    continue
                visited.add(real_root)
                dirs.sort(key=natural_key)
                for name in sorted(files, key=natural_key):
                    add_file(os.path.join(root, name))
                if not recursive:
                    dirs[:] = []
        elif os.path.exists(p):
            add_file(p)
        else:
            ignored.append((p, _("file non trovato")))
    return found, ignored


# =============================================================================
# Azioni e parametri
# =============================================================================
@dataclass
class Param:
    key: str
    label: str
    type: str
    default: object = None
    choices: list[tuple[str, str]] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False


@dataclass
class ActionSpec:
    id: str
    label: str
    suffix: str
    params: list[Param]
    kind: str = "pdf"

    def defaults(self) -> dict:
        return {p.key: p.default for p in self.params}

    def secret_keys(self) -> set[str]:
        return {p.key for p in self.params if p.secret}


POSITIONS = [("top-left", "In alto a sinistra"), ("top-center", "In alto al centro"),
             ("top-right", "In alto a destra"), ("bottom-left", "In basso a sinistra"),
             ("bottom-center", "In basso al centro"), ("bottom-right", "In basso a destra")]


def _build_actions() -> dict[str, ActionSpec]:
    pos = [(k, v) for k, v in POSITIONS]
    actions = [
        ActionSpec("compress", "Comprimi", "compresso", [
            Param("level", "Livello", "choice", "medium",
                  [("low", "Leggera (qualità alta)"), ("medium", "Bilanciata"), ("high", "Massima")]),
        ]),
        ActionSpec("protect", "Proteggi con password", "protetto", [
            Param("user_password", "Password di apertura", "password", "", secret=True),
            Param("owner_password", "Password dei permessi", "password", "", secret=True),
            Param("allow_print", "Consenti la stampa", "bool", True),
            Param("allow_copy", "Consenti la copia del contenuto", "bool", False),
            Param("allow_edit", "Consenti le modifiche", "bool", False),
            Param("password_csv", "File CSV con password per file (nome;password)", "path", ""),
        ]),
        ActionSpec("remove_password", "Rimuovi password nota", "sbloccato", [
            Param("passwords", "Password candidate (una per riga, max 20)", "text", "", secret=True),
        ]),
        ActionSpec("watermark", "Filigrana", "filigrana", [
            Param("text", "Testo ({nome} = nome del file)", "str", "RISERVATO"),
            Param("fontsize", "Dimensione (pt)", "int", 60, minimum=4, maximum=400),
            Param("opacity", "Opacità (%)", "int", 30, minimum=1, maximum=100),
            Param("rotation", "Rotazione (gradi)", "int", 45, minimum=-180, maximum=180),
            Param("color", "Colore", "color", "#C00000"),
            Param("pages", "Pagine (vuoto = tutte, «dispari», «pari», 1-3,5)", "pages", ""),
            Param("replace", "Sostituisci filigrane precedenti", "bool", True),
        ]),
        ActionSpec("background", "Sfondo", "sfondo", [
            Param("color", "Colore", "color", "#FFF8E1"),
            Param("file", "PDF di sfondo (carta intestata, facoltativo)", "path", ""),
            Param("first_page_separate", "Pagina 1 sulla prima, pagina 2 sulle successive", "bool", False),
            Param("pages", "Pagine", "pages", ""),
            Param("replace", "Sostituisci sfondi precedenti", "bool", True),
        ]),
        ActionSpec("header_footer", "Intestazioni e piè di pagina", "intestato", [
            Param("header_left", "Intestazione a sinistra", "str", ""),
            Param("header_center", "Intestazione al centro", "str", ""),
            Param("header_right", "Intestazione a destra", "str", ""),
            Param("footer_left", "Piè a sinistra", "str", ""),
            Param("footer_center", "Piè al centro", "str", "Pagina {pagina} di {totale}"),
            Param("footer_right", "Piè a destra", "str", ""),
            Param("fontsize", "Dimensione (pt)", "int", 9, minimum=4, maximum=72),
            Param("margin", "Margine (mm)", "float", 10.0, minimum=0, maximum=100),
            Param("color", "Colore", "color", "#000000"),
            Param("continuous", "Numerazione continua tra i file", "bool", False),
            Param("start", "Numero iniziale", "int", 1, minimum=0),
            Param("mirror", "Speculari sulle pagine pari", "bool", False),
            Param("skip_first", "Escludi la prima pagina", "bool", False),
            Param("count_skipped", "Conta le pagine escluse", "bool", True),
            Param("replace", "Sostituisci intestazioni precedenti", "bool", True),
        ]),
        ActionSpec("page_numbers", "Numeri di pagina", "numerato", [
            Param("text", "Testo", "str", "Pagina {pagina} di {totale}"),
            Param("position", "Posizione", "position", "bottom-center", pos),
            Param("fontsize", "Dimensione (pt)", "int", 10, minimum=4, maximum=72),
            Param("margin", "Margine (mm)", "float", 10.0, minimum=0, maximum=100),
            Param("continuous", "Numerazione continua tra i file", "bool", False),
            Param("start", "Numero iniziale", "int", 1, minimum=0),
            Param("skip_first", "Escludi la prima pagina", "bool", False),
            Param("count_skipped", "Conta le pagine escluse", "bool", True),
        ]),
        ActionSpec("bates", "Numerazione Bates", "bates", [
            Param("prefix", "Prefisso", "str", ""),
            Param("suffix", "Suffisso", "str", ""),
            Param("digits", "Cifre", "int", 6, minimum=1, maximum=12),
            Param("start", "Numero iniziale", "int", 1, minimum=0),
            Param("restart", "Ricomincia per documento", "bool", False),
            Param("position", "Posizione", "position", "bottom-right", pos),
            Param("fontsize", "Dimensione (pt)", "int", 9, minimum=4, maximum=72),
            Param("margin", "Margine (mm)", "float", 8.0, minimum=0, maximum=100),
        ]),
        ActionSpec("remove_marks", "Rimuovi filigrane, sfondi, intestazioni e Bates", "pulito", [
            Param("watermark", "Filigrane", "bool", True),
            Param("background", "Sfondi", "bool", False),
            Param("header_footer", "Intestazioni, piè e numeri di pagina", "bool", False),
            Param("bates", "Numeri Bates", "bool", False),
        ]),
        ActionSpec("insert_pages", "Inserisci pagine", "inserito", [
            Param("file", "PDF da inserire (vuoto = pagina bianca)", "path", ""),
            Param("where", "Dove", "choice", "start",
                  [("start", "All'inizio"), ("end", "Alla fine"), ("after", "Dopo la pagina"),
                   ("every", "Dopo ogni N pagine")]),
            Param("number", "Pagina / N", "int", 1, minimum=1),
        ]),
        ActionSpec("merge", "Unisci in un file", "unito", [
            Param("bookmarks", "Un segnalibro per file", "bool", True),
            Param("blank_after_odd", "Pagina bianca dopo i file con pagine dispari", "bool", False),
            Param("name", "Nome del file unito", "str", "Unione"),
        ], kind="merge"),
        ActionSpec("ocr", "OCR (rendi ricercabile)", "ocr", [
            Param("languages", "Lingue (es. ita+eng)", "str", "ita+eng"),
            Param("mode", "Modalità", "choice", "skip_text",
                  [("skip_text", "Solo pagine senza testo"), ("all", "Tutte le pagine")]),
        ]),
        ActionSpec("convert", "Converti", "convertito", [
            Param("format", "Formato", "choice", "docx",
                  [("docx", "Word (DOCX)"), ("xlsx", "Excel (XLSX)"), ("pptx", "PowerPoint (PPTX)"),
                   ("txt", "Testo (TXT)"), ("html", "HTML"), ("png", "Immagini PNG"),
                   ("jpg", "Immagini JPEG"), ("pdfa", "PDF/A-2b")]),
            Param("dpi", "Risoluzione immagini (dpi)", "int", 150, minimum=36, maximum=1200),
        ], kind="convert"),
        ActionSpec("print", "Stampa", "stampato", [
            Param("printer", "Stampante (vuoto = predefinita)", "str", ""),
            Param("copies", "Copie", "int", 1, minimum=1, maximum=999),
            Param("duplex", "Fronte/retro", "choice", "none",
                  [("none", "Solo fronte"), ("long", "Fronte/retro lato lungo"), ("short", "Fronte/retro lato corto")]),
            Param("pages", "Pagine (es. 1-2)", "pages", ""),
        ], kind="print"),
    ]
    return {a.id: a for a in actions}


ACTIONS: dict[str, ActionSpec] = _build_actions()


def available_actions() -> list[ActionSpec]:
    return list(ACTIONS.values())


def action_label(action_id: str) -> str:
    spec = ACTIONS.get(action_id)
    return _(spec.label) if spec else action_id


def action_available(action_id: str) -> tuple[bool, str]:
    if action_id not in ACTIONS:
        return False, _("Operazione sconosciuta: {a}").format(a=action_id)
    if action_id == "ocr" and _ocr_function() is None:
        return False, _("L'OCR non è disponibile: il modulo OCR non è installato.")
    if action_id == "convert" and _convert_function() is None:
        return False, _("La conversione non è disponibile: il modulo Converti non è installato.")
    return True, ""


@dataclass
class Step:
    action: str
    params: dict = field(default_factory=dict)

    def merged_params(self) -> dict:
        spec = ACTIONS.get(self.action)
        out = spec.defaults() if spec else {}
        out.update(self.params or {})
        return out

    def to_dict(self, keep_secrets: bool = False) -> dict:
        params = dict(self.params or {})
        spec = ACTIONS.get(self.action)
        if spec and not keep_secrets:
            for k in spec.secret_keys():
                if k in params:
                    params[k] = ""
        return {"action": self.action, "params": params}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(str(data.get("action", "")), dict(data.get("params") or {}))


# =============================================================================
# Impostazioni del lavoro, destinazione e nomi (BAT-02, BAT-03)
# =============================================================================
@dataclass
class JobSettings:
    steps: list[Step] = field(default_factory=list)
    destination_mode: str = "folder"
    destination: str = ""
    subfolder: str = "elaborati"
    keep_structure: bool = False
    input_root: str | None = None
    pattern: str = "{nome}"
    counter_start: int = 1
    conflict: str = "rename"
    replace_originals: bool = False
    backup: bool = True
    error_policy: str = "continue"
    passwords: list[str] = field(default_factory=list)
    workers: int = 1

    def to_dict(self) -> dict:
        """Impostazioni senza segreti (per giornale e rapporto)."""
        return {
            "steps": [s.to_dict() for s in self.steps], "destination_mode": self.destination_mode,
            "destination": self.destination, "subfolder": self.subfolder, "keep_structure": self.keep_structure,
            "pattern": self.pattern, "counter_start": self.counter_start, "conflict": self.conflict,
            "replace_originals": self.replace_originals, "backup": self.backup,
            "error_policy": self.error_policy, "workers": self.workers,
        }


PLACEHOLDERS = {"nome", "est", "cartella", "data", "ora", "contatore", "pagine", "operazione", "pagina", "parte",
                "bates_inizio", "bates_fine"}
_PH = re.compile(r"\{([^{}]*)\}")
_INVALID = re.compile(r'[/\\\x00:*?"<>|\x01-\x1f]')


def _main_action(settings: JobSettings) -> ActionSpec | None:
    for s in reversed(settings.steps):
        if s.action in ACTIONS:
            return ACTIONS[s.action]
    return None


def default_pattern(settings: JobSettings) -> str:
    spec = _main_action(settings)
    if settings.destination_mode == "same" and spec is not None:
        return "{nome}_" + _(spec.suffix)
    return "{nome}"


def validate_pattern(pattern: str) -> list[str]:
    errors = []
    depth = 0
    for c in pattern:
        if c == "{":
            depth += 1
            if depth > 1:
                break
        elif c == "}":
            depth -= 1
            if depth < 0:
                break
    if depth != 0:
        errors.append(_("Parentesi graffe non bilanciate nel modello del nome."))
        return errors
    for m in _PH.finditer(pattern):
        name, _sep, arg = m.group(1).partition(":")
        if name not in PLACEHOLDERS:
            errors.append(_("Segnaposto non valido: {{{p}}}").format(p=m.group(1)))
        elif arg and not arg.isdigit():
            errors.append(_("Parametro non numerico nel segnaposto {{{p}}}").format(p=m.group(1)))
    if not pattern.strip():
        errors.append(_("Il modello del nome è vuoto."))
    return errors


def _sanitize(name: str) -> str:
    name = _INVALID.sub("_", name)
    return name.strip(" .")


def _truncate(stem: str, ext: str, limit: int = 255) -> str:
    while len((stem + ext).encode("utf-8")) > limit and stem:
        stem = stem[:-1]
    return stem + ext


def render_name(pattern: str, path: str, index: int, settings: JobSettings, started: datetime, pages: int = 0,
                extra: dict | None = None, ext: str = ".pdf") -> str:
    extra = extra or {}
    p = Path(path)
    spec = _main_action(settings)
    values = {
        "nome": p.stem, "est": p.suffix.lstrip("."), "cartella": p.parent.name,
        "data": started.strftime("%Y-%m-%d"), "ora": started.strftime("%H-%M-%S"),
        "pagine": str(pages), "operazione": _(spec.suffix) if spec else "",
    }

    def repl(m: re.Match) -> str:
        name, _sep, arg = m.group(1).partition(":")
        if name == "contatore":
            n = settings.counter_start + index
            return str(n).zfill(int(arg)) if arg else str(n)
        if name in ("pagina", "parte"):
            n = int(extra.get(name, 1))
            return str(n).zfill(int(arg)) if arg else str(n)
        if name in ("bates_inizio", "bates_fine"):
            return str(extra.get(name, ""))
        return values.get(name, "")

    stem = _sanitize(_PH.sub(repl, pattern))
    if not stem:
        stem = _sanitize(p.stem) or "documento"
    return _truncate(stem, ext)


def preview_names(items: list[QueueItem], settings: JobSettings, started: datetime | None = None,
                  limit: int = 5) -> list[tuple[str, str]]:
    started = started or datetime.now()
    errors = validate_pattern(settings.pattern)
    out = []
    plan = _bates_plan(items, settings)
    for i, item in enumerate(items[:limit]):
        if errors:
            out.append((item.path, errors[0]))
            continue
        extra = plan.get(i, {})
        out.append((item.path, render_name(settings.pattern, item.path, i, settings, started, item.pages, extra,
                                           _output_ext(settings))))
    return out


def _output_ext(settings: JobSettings) -> str:
    for s in settings.steps:
        if s.action == "convert":
            fmt = s.merged_params().get("format", "docx")
            return ".pdf" if fmt == "pdfa" else "." + ("jpg" if fmt == "jpeg" else fmt)
    return ".pdf"


def _candidates(text: str) -> list[str]:
    return [line for line in (text or "").splitlines() if line != ""]


def validate_settings(items: list[QueueItem], settings: JobSettings) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not items:
        errors.append(_("La coda è vuota: aggiungi almeno un file."))
    if not settings.steps:
        errors.append(_("Scegli almeno un'operazione."))
    for i, step in enumerate(settings.steps):
        ok, msg = action_available(step.action)
        if not ok:
            errors.append(msg)
            continue
        spec = ACTIONS[step.action]
        params = step.merged_params()
        if spec.kind in ("convert", "print") and i != len(settings.steps) - 1:
            errors.append(_("«{a}» deve essere l'ultimo passo della sequenza.").format(a=_(spec.label)))
        if step.action == "protect":
            up, op = params.get("user_password") or "", params.get("owner_password") or ""
            if not up and not op and not params.get("password_csv"):
                errors.append(_("Indica almeno una password per la protezione."))
            if up and up == op:
                errors.append(_("La password di apertura e quella dei permessi devono essere diverse."))
        if step.action == "remove_password":
            if len(_candidates(params.get("passwords", ""))) > MAX_CANDIDATES:
                errors.append(_("Puoi indicare al massimo {n} password candidate.").format(n=MAX_CANDIDATES))
        if step.action == "bates":
            total = sum(max(1, it.pages) for it in items)
            start = int(params.get("start", 1))
            digits = int(params.get("digits", 6))
            last = start + (max((it.pages for it in items), default=1) if params.get("restart") else total) - 1
            if len(str(last)) > digits:
                errors.append(_("Cifre insufficienti per la numerazione Bates: l'ultimo numero ({n}) richiede "
                                "{d} cifre.").format(n=last, d=len(str(last))))
        if step.action == "watermark" and not str(params.get("text", "")).strip():
            errors.append(_("Il testo della filigrana è vuoto."))
    errors.extend(validate_pattern(settings.pattern) if not settings.replace_originals else [])
    if settings.replace_originals and any(ACTIONS.get(s.action) and ACTIONS[s.action].kind != "pdf"
                                          for s in settings.steps):
        errors.append(_("«Sostituisci gli originali» è disponibile solo per le operazioni da PDF a PDF."))
    if settings.destination_mode == "folder" and not settings.replace_originals:
        if not settings.destination:
            errors.append(_("Scegli la cartella di destinazione."))
        else:
            dest = settings.destination
            probe = dest
            while probe and not os.path.exists(probe):
                probe = os.path.dirname(probe)
            if probe and not os.access(probe, os.W_OK):
                errors.append(_("La cartella di destinazione non è scrivibile."))
    if len(items) > 1 and not any(k in settings.pattern for k in ("{nome", "{contatore", "{bates")) \
            and not any(s.action == "merge" for s in settings.steps) and not settings.replace_originals:
        warnings.append(_("Il modello produce nomi non univoci: aggiungi {contatore} oppure i conflitti saranno "
                          "risolti secondo la politica scelta."))
    signed = sum(1 for it in items if it.signed)
    if signed:
        warnings.append(_("{n} file firmati: le firme non saranno più valide.").format(n=signed))
    pdfa = sum(1 for it in items if it.pdfa)
    if pdfa and any(s.action in ("protect", "watermark", "background") for s in settings.steps):
        warnings.append(_("{n} file PDF/A: l'elaborazione fa perdere la conformità.").format(n=pdfa))
    locked = sum(1 for it in items if it.needs_password and not it.password)
    if locked and not settings.passwords and not any(s.action == "remove_password" for s in settings.steps):
        warnings.append(_("{n} file richiedono una password non indicata: saranno saltati.").format(n=locked))
    return errors, warnings


# =============================================================================
# Rapporto (BAT-05)
# =============================================================================
STATUS_LABELS = {
    "ok": "Completato", "warning": "Avviso", "error": "Errore", "skipped": "Saltato",
    "cancelled": "Annullato", "not_processed": "Non elaborato",
}


@dataclass
class FileResult:
    input: str
    outputs: list[str] = field(default_factory=list)
    status: str = "not_processed"
    code: str = ""
    message: str = ""
    pages: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    duration_ms: int = 0

    @property
    def status_label(self) -> str:
        return _(STATUS_LABELS.get(self.status, self.status))


@dataclass
class JobReport:
    results: list[FileResult]
    steps: list[Step]
    started: datetime
    finished: datetime | None = None
    cancelled: bool = False
    settings: dict = field(default_factory=dict)

    STATUS_LABELS = STATUS_LABELS

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in STATUS_LABELS}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def failed_items(self) -> list[str]:
        return [r.input for r in self.results if r.status in ("error", "cancelled", "not_processed")]

    def to_csv(self, sep: str = ",") -> str:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=sep, lineterminator="\n")
        w.writerow(["file_ingresso", "file_uscita", "esito", "codice", "messaggio", "pagine", "byte_ingresso",
                    "byte_uscita", "durata_ms"])
        for r in self.results:
            w.writerow([r.input, "|".join(r.outputs), r.status_label, r.code, r.message, r.pages, r.bytes_in,
                        r.bytes_out, r.duration_ms])
        return buf.getvalue()

    def to_json(self) -> str:
        from foliant import __version__ as version  # noqa: F401
        data = {
            "lavoro": {"passi": [s.to_dict() for s in self.steps], "impostazioni": self.settings,
                       "inizio": self.started.isoformat(timespec="seconds"),
                       "fine": self.finished.isoformat(timespec="seconds") if self.finished else None,
                       "annullato": self.cancelled, "versione": version},
            "file": [{"file_ingresso": r.input, "file_uscita": r.outputs, "esito": r.status,
                      "esito_testo": r.status_label, "codice": r.code, "messaggio": r.message, "pagine": r.pages,
                      "byte_ingresso": r.bytes_in, "byte_uscita": r.bytes_out, "durata_ms": r.duration_ms}
                     for r in self.results],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def summary(self) -> str:
        c = self.counts()
        if self.results and not c["ok"] and not c["warning"]:
            causes: dict[str, int] = {}
            for r in self.results:
                if r.message:
                    causes[r.message] = causes.get(r.message, 0) + 1
            main = max(causes, key=causes.get) if causes else ""
            text = _("Nessun file elaborato.")
            return f"{text} {main}".strip()
        parts = [f"{c[k]} {_(STATUS_LABELS[k]).lower()}" for k in STATUS_LABELS if c.get(k)]
        if self.finished:
            secs = (self.finished - self.started).total_seconds()
            parts.append(_("durata {s:.1f} s").format(s=secs))
        return " · ".join(parts)


# =============================================================================
# Operazioni singole (bytes -> bytes)
# =============================================================================
def _open(data: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=data, filetype="pdf")


def _save(doc: pymupdf.Document, **opts) -> bytes:
    kw = {"garbage": 3, "deflate": True}
    kw.update(opts)
    return doc.tobytes(**kw)


def _rgb(color: str | tuple) -> tuple[float, float, float]:
    if isinstance(color, (tuple, list)):
        return tuple(float(c) for c in color[:3])
    c = str(color or "#000000").lstrip("#")
    if len(c) != 6:
        return (0.0, 0.0, 0.0)
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def parse_pages(spec: str, count: int) -> tuple[list[int], bool]:
    """Pagine (0-based) da «1-3,5», «dispari», «pari», vuoto = tutte.
    Restituisce (pagine, fuori_intervallo)."""
    spec = (spec or "").strip().lower()
    if not spec or spec in ("tutte", "all"):
        return list(range(count)), False
    if spec in ("dispari", "odd"):
        return list(range(0, count, 2)), False
    if spec in ("pari", "even"):
        return list(range(1, count, 2)), False
    out: list[int] = []
    out_of_range = False
    for part in re.split(r"[,;\s]+", spec):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else count
        elif part.isdigit():
            a = b = int(part)
        else:
            raise BatchError("PAR-01", _("Intervallo di pagine non valido: «{p}».").format(p=part))
        if a < 1 or b > count or a > b:
            out_of_range = True
        for n in range(max(1, a), min(b, count) + 1):
            if n - 1 not in out:
                out.append(n - 1)
    return out, out_of_range


def _mark_new_contents(doc: pymupdf.Document, page: pymupdf.Page, before: list[int], kind: str) -> None:
    """Racchiude il contenuto appena aggiunto in un artefatto marcato da Foliant."""
    subtype = {"watermark": "/Watermark", "background": "/Background", "header_footer": "/Header",
               "bates": "/Bates"}.get(kind, "/Watermark")
    typ = "/Background" if kind == "background" else "/Pagination"
    for xref in page.get_contents():
        if xref in before:
            continue
        stream = doc.xref_stream(xref) or b""
        head = f"/Artifact <</Type {typ} /Subtype {subtype} {MARK_KEY} ({kind})>> BDC\n".encode()
        doc.update_stream(xref, head + stream + b"\nEMC\n")


def _remove_marks_page(doc: pymupdf.Document, page: pymupdf.Page, kinds: set[str]) -> int:
    removed = 0
    for xref in page.get_contents():
        stream = doc.xref_stream(xref) or b""
        m = re.match(rb"/Artifact <<[^>]*" + MARK_KEY.encode() + rb" \((\w+)\)>> BDC", stream)
        if m and m.group(1).decode() in kinds:
            doc.update_stream(xref, b"")
            removed += 1
    return removed


def remove_marks(data: bytes, kinds: set[str] | list[str], check=None) -> tuple[bytes, int]:
    kinds = set(kinds)
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            n = 0
            for page in doc:
                n += _remove_marks_page(doc, page, kinds)
            return (_save(doc) if n else data), n
        finally:
            doc.close()


def compress_pdf(data: bytes, level: str = "medium") -> bytes:
    targets = {"low": (225, 200, 85), "medium": (180, 150, 75), "high": (110, 96, 55)}
    threshold, target, quality = targets.get(level, targets["medium"])
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            try:
                doc.rewrite_images(dpi_threshold=threshold, dpi_target=target, quality=quality)
            except Exception:
                pass
            try:
                doc.subset_fonts()
            except Exception:
                pass
            return doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True,
                               use_objstms=1)
        finally:
            doc.close()


def _permissions(allow_print: bool, allow_copy: bool, allow_edit: bool) -> int:
    perm = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= pymupdf.PDF_PERM_COPY
    if allow_edit:
        perm |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ASSEMBLE
    return perm


def protect_pdf(data: bytes, user_password: str = "", owner_password: str = "", allow_print: bool = True,
                allow_copy: bool = False, allow_edit: bool = False) -> bytes:
    import secrets
    owner = owner_password or (secrets.token_urlsafe(24) if not user_password else user_password + "#" +
                               secrets.token_urlsafe(12))
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            return _save(doc, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_password or "", owner_pw=owner,
                         permissions=_permissions(allow_print, allow_copy, allow_edit))
        finally:
            doc.close()


def remove_password(data: bytes, password: str) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if doc.needs_pass and not doc.authenticate(password):
                raise BatchError("PWD-01", _("Password errata."))
            return _save(doc, encryption=pymupdf.PDF_ENCRYPT_NONE)
        finally:
            doc.close()


def _expand(text: str, values: dict) -> str:
    def repl(m):
        key = m.group(1)
        return str(values[key]) if key in values else m.group(0)
    return re.sub(r"\{(\w+)\}", repl, text or "")


def add_watermark(data: bytes, text: str, fontsize: int = 60, opacity: int = 30, rotation: int = 45,
                  color: str = "#C00000", pages: str = "", replace: bool = True, name: str = "", check=None) -> bytes:
    text = _expand(text, {"nome": name})
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if replace:
                for page in doc:
                    _remove_marks_page(doc, page, {"watermark"})
            selected, _oor = parse_pages(pages, doc.page_count)
            for pno in selected:
                if check:
                    check()
                page = doc[pno]
                before = page.get_contents()
                r = page.rect
                width = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
                center = pymupdf.Point(r.width / 2, r.height / 2)
                start = pymupdf.Point(center.x - width / 2, center.y + fontsize * 0.35)
                page.insert_text(start, text, fontsize=fontsize, fontname="helv", color=_rgb(color),
                                 fill_opacity=max(0.01, min(1.0, opacity / 100.0)),
                                 morph=(center, pymupdf.Matrix(-float(rotation))), overlay=True)
                _mark_new_contents(doc, page, before, "watermark")
            return _save(doc)
        finally:
            doc.close()


def add_background(data: bytes, color: str = "#FFF8E1", file: str = "", first_page_separate: bool = False,
                   pages: str = "", replace: bool = True, check=None) -> bytes:
    src = None
    if file:
        try:
            with ENGINE_LOCK:
                src = pymupdf.open(file)
        except Exception as e:
            raise BatchError("FILE-02", _("Impossibile aprire lo sfondo «{f}».").format(f=os.path.basename(file))) from e
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if replace:
                for page in doc:
                    _remove_marks_page(doc, page, {"background"})
            selected, _oor = parse_pages(pages, doc.page_count)
            for k, pno in enumerate(selected):
                if check:
                    check()
                page = doc[pno]
                before = page.get_contents()
                if src is not None:
                    spage = 0 if (not first_page_separate or pno == 0 or src.page_count < 2) else 1
                    page.show_pdf_page(page.rect, src, spage, overlay=False)
                else:
                    page.draw_rect(page.rect, color=None, fill=_rgb(color), overlay=False, width=0)
                _mark_new_contents(doc, page, before, "background")
            return _save(doc)
        finally:
            doc.close()
            if src is not None:
                src.close()


def _place(page: pymupdf.Page, text: str, position: str, fontsize: float, margin_pt: float,
           color=(0, 0, 0)) -> None:
    r = page.rect
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
    vert, horiz = position.split("-")
    if horiz == "left":
        x = margin_pt
    elif horiz == "right":
        x = r.width - margin_pt - width
    else:
        x = (r.width - width) / 2
    y = margin_pt + fontsize if vert == "top" else r.height - margin_pt
    # le coordinate tengono conto della rotazione della pagina
    pt = pymupdf.Point(x, y) * page.derotation_matrix
    page.insert_text(pt, text, fontsize=fontsize, fontname="helv", color=color, rotate=page.rotation)


def add_header_footer(data: bytes, texts: dict[str, str], fontsize: float = 9, margin_mm: float = 10.0,
                      color: str = "#000000", first_number: int = 1, total: int | None = None,
                      queue_total: int | None = None, mirror: bool = False, skip_first: bool = False,
                      count_skipped: bool = True, replace: bool = True, values: dict | None = None,
                      kind: str = "header_footer", check=None) -> bytes:
    """`texts`: posizione ("top-left", …) -> testo con {pagina}, {totale}, {totale_coda}, {nome}, {data}."""
    margin = margin_mm * 72 / 25.4
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if replace:
                for page in doc:
                    _remove_marks_page(doc, page, {kind})
            n_pages = doc.page_count
            tot = total if total is not None else (first_number - 1 + (n_pages if count_skipped or not skip_first
                                                                       else n_pages - 1))
            number = first_number
            for pno in range(n_pages):
                if check:
                    check()
                if skip_first and pno == 0:
                    if count_skipped:
                        number += 1
                    continue
                page = doc[pno]
                before = page.get_contents()
                vals = dict(values or {})
                vals.update({"pagina": number, "totale": tot,
                             "totale_coda": queue_total if queue_total is not None else tot})
                for pos, text in texts.items():
                    if not text:
                        continue
                    where = pos
                    if mirror and (pno + 1) % 2 == 0:
                        where = pos.replace("left", "@").replace("right", "left").replace("@", "right")
                    _place(page, _expand(text, vals), where, fontsize, margin, _rgb(color))
                _mark_new_contents(doc, page, before, kind)
                number += 1
            return _save(doc)
        finally:
            doc.close()


def add_page_numbers(data: bytes, text: str = "Pagina {pagina} di {totale}", position: str = "bottom-center",
                     fontsize: float = 10, margin_mm: float = 10.0, first_number: int = 1, **kw) -> bytes:
    return add_header_footer(data, {position: text}, fontsize=fontsize, margin_mm=margin_mm,
                             first_number=first_number, **kw)


def bates_label(n: int, prefix: str = "", digits: int = 6, suffix: str = "") -> str:
    return f"{prefix}{str(n).zfill(digits)}{suffix}"


def add_bates(data: bytes, start: int = 1, prefix: str = "", digits: int = 6, suffix: str = "",
              position: str = "bottom-right", fontsize: float = 9, margin_mm: float = 8.0,
              check=None) -> tuple[bytes, int]:
    """Numera le pagine; restituisce (dati, ultimo_numero)."""
    margin = margin_mm * 72 / 25.4
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            for page in doc:
                _remove_marks_page(doc, page, {"bates"})
            n = start
            for page in doc:
                if check:
                    check()
                before = page.get_contents()
                _place(page, bates_label(n, prefix, digits, suffix), position, fontsize, margin)
                _mark_new_contents(doc, page, before, "bates")
                n += 1
            return _save(doc), n - 1
        finally:
            doc.close()


def insert_pages(data: bytes, file: str = "", where: str = "start", number: int = 1) -> tuple[bytes, list[str]]:
    warnings: list[str] = []
    src = None
    with ENGINE_LOCK:
        if file:
            try:
                src = pymupdf.open(file)
            except Exception as e:
                raise BatchError("FILE-02", _("Impossibile aprire «{f}».").format(f=os.path.basename(file))) from e
        doc = _open(data)
        try:
            def insert_at(pos: int) -> int:
                if src is not None:
                    doc.insert_pdf(src, start_at=pos)
                    return src.page_count
                ref = doc[min(max(pos - 1, 0), doc.page_count - 1)].rect
                doc.new_page(pno=pos, width=ref.width, height=ref.height)
                return 1
            n = doc.page_count
            if where == "start":
                insert_at(0)
            elif where == "end":
                insert_at(n)
            elif where == "after":
                pos = int(number)
                if pos > n:
                    warnings.append(_("La pagina {p} non esiste: inserimento alla fine.").format(p=pos))
                    pos = n
                insert_at(pos)
            else:
                step = max(1, int(number))
                pos = step
                while pos <= doc.page_count:
                    added = insert_at(pos)
                    pos += added + step
                    if pos > doc.page_count + (0 if src is None else 0) and pos - step - added >= doc.page_count:
                        break
            return _save(doc), warnings
        finally:
            doc.close()
            if src is not None:
                src.close()


def merge_pdfs(parts: list[tuple[str, bytes]], bookmarks: bool = True, blank_after_odd: bool = False,
               check=None) -> bytes:
    with ENGINE_LOCK:
        out = pymupdf.open()
        try:
            toc = []
            for title, data in parts:
                if check:
                    check()
                src = _open(data)
                try:
                    start = out.page_count
                    out.insert_pdf(src, widgets=True, join_duplicates=False) if _supports_join() else \
                        out.insert_pdf(src)
                    if bookmarks:
                        toc.append([1, title, start + 1])
                    if blank_after_odd and src.page_count % 2 == 1:
                        ref = out[out.page_count - 1].rect
                        out.new_page(width=ref.width, height=ref.height)
                finally:
                    src.close()
            if toc:
                out.set_toc(toc)
            return _save(out)
        finally:
            out.close()


def _supports_join() -> bool:
    import inspect
    try:
        return "join_duplicates" in inspect.signature(pymupdf.Document.insert_pdf).parameters
    except (TypeError, ValueError):
        return False


# =============================================================================
# Esecuzione del lavoro (BAT-04)
# =============================================================================
class _Ctx:
    """Stato per un file in elaborazione."""

    def __init__(self, progress, index: int, total_files: int):
        self.progress = progress
        self.index = index
        self.total_files = total_files
        self.warnings: list[str] = []

    def check(self) -> None:
        if self.progress is not None:
            self.progress.check() if hasattr(self.progress, "check") else self.progress.update()


def _open_input(data: bytes, item: QueueItem, candidates: list[str]) -> tuple[bytes, dict, int | None]:
    """Apre il file; restituisce (dati in chiaro, opzioni di cifratura originali, n. candidata)."""
    with ENGINE_LOCK:
        try:
            doc = _open(data)
        except Exception as e:
            raise BatchError("PDF-01", _("File danneggiato o non PDF: impossibile aprirlo.")) from e
        try:
            if doc.page_count == 0:
                raise BatchError("PDF-01", _("Il documento non contiene pagine."))
            used = None
            auth = 0
            had_pass = bool(doc.needs_pass)
            if had_pass:
                tries = ([item.password] if item.password else []) + [c for c in candidates if c != item.password]
                if not tries:
                    raise BatchError("PWD-02", _("Password mancante: indica la password di questo file."),
                                     status="skipped")
                for n, pw in enumerate(tries, 1):
                    auth = doc.authenticate(pw)
                    if auth:
                        used = (n, pw)
                        break
                if not auth:
                    raise BatchError("PWD-01", _("Password errata: verifica la password di questo file."))
            enc = (doc.metadata or {}).get("encryption")
            if enc and not had_pass:
                for n, pw in enumerate(([item.password] if item.password else []) + candidates, 1):
                    a = doc.authenticate(pw)
                    if a in (4, 6):
                        auth, used = a, (n, pw)
                        break
            protection: dict = {}
            if enc:
                import secrets
                owner_known = auth in (4, 6)
                user_pw = used[1] if (used and had_pass) else ""
                protection = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "permissions": doc.permissions,
                              "user_pw": user_pw,
                              "owner_pw": used[1] if (used and owner_known) else secrets.token_urlsafe(24),
                              "_auth": auth, "_restricted": not owner_known}
            plain = doc.tobytes(garbage=0, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
            return plain, protection, (used[0] if used else None)
        finally:
            doc.close()


def _reencrypt(data: bytes, protection: dict) -> bytes:
    opts = {k: v for k, v in protection.items() if not k.startswith("_")}
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            return _save(doc, **opts)
        finally:
            doc.close()


def _page_count(data: bytes) -> int:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            return doc.page_count
        finally:
            doc.close()


def _has_text_everywhere(data: bytes) -> bool:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            return all(doc[i].get_text("text").strip() for i in range(doc.page_count))
        finally:
            doc.close()


def _load_password_csv(path: str) -> dict[str, str]:
    out = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            sample = f.read(2048)
            f.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            for row in csv.reader(f, delimiter=delim):
                if len(row) >= 2 and row[0].strip():
                    out[os.path.basename(row[0].strip())] = row[1]
    except OSError as e:
        raise BatchError("FILE-02", _("Impossibile leggere il file CSV delle password.")) from e
    return out


def _bates_plan(items: list[QueueItem], settings: JobSettings) -> dict[int, dict]:
    """Numeri Bates e numerazione continua precalcolati per ogni file."""
    plan: dict[int, dict] = {i: {} for i in range(len(items))}
    for step in settings.steps:
        p = step.merged_params()
        if step.action == "bates":
            n = int(p.get("start", 1))
            for i, it in enumerate(items):
                first = int(p.get("start", 1)) if p.get("restart") else n
                count = max(1, it.pages)
                plan[i]["bates_start"] = first
                plan[i]["bates_inizio"] = bates_label(first, p.get("prefix", ""), int(p.get("digits", 6)),
                                                      p.get("suffix", ""))
                plan[i]["bates_fine"] = bates_label(first + count - 1, p.get("prefix", ""),
                                                    int(p.get("digits", 6)), p.get("suffix", ""))
                n = first + count
        if step.action in ("header_footer", "page_numbers") and p.get("continuous"):
            n = int(p.get("start", 1))
            total = sum(max(1, it.pages) for it in items)
            for i, it in enumerate(items):
                plan[i].setdefault("numbering", {})[step.action] = (n, n - 1 + total, total)
                n += max(1, it.pages)
    return plan


def _apply_step(step: Step, data: bytes, item: QueueItem, ctx: _Ctx, plan: dict, protection: dict,
                csv_passwords: dict) -> tuple[bytes, dict]:
    """Applica un passo PDF->PDF. Restituisce (dati, protezione aggiornata)."""
    p = step.merged_params()
    a = step.action
    name = Path(item.path).stem
    if a == "compress":
        return compress_pdf(data, p.get("level", "medium")), protection
    if a == "protect":
        user, owner = p.get("user_password") or "", p.get("owner_password") or ""
        if p.get("password_csv"):
            user = csv_passwords.get(os.path.basename(item.path))
            if user is None:
                raise BatchError("PWD-03", _("Password non definita per questo file nel CSV."), status="skipped")
        if item.pdfa:
            ctx.warnings.append(_("conformità PDF/A rimossa"))
        protection = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": user or "",
                      "owner_pw": owner or None,
                      "permissions": _permissions(bool(p.get("allow_print")), bool(p.get("allow_copy")),
                                                  bool(p.get("allow_edit")))}
        if not protection["owner_pw"]:
            import secrets
            protection["owner_pw"] = (user + "#" if user else "") + secrets.token_urlsafe(16)
        return data, protection
    if a == "remove_password":
        if not protection:
            raise BatchError("UNP-01", _("Non protetto: nessuna password da rimuovere."), status="skipped")
        if protection.get("_restricted"):
            raise BatchError("PWD-04", _("Serve la password dei permessi per rimuovere le restrizioni."))
        return data, {}
    if a == "watermark":
        return add_watermark(data, p.get("text", ""), int(p.get("fontsize", 60)), int(p.get("opacity", 30)),
                             int(p.get("rotation", 45)), p.get("color", "#C00000"), p.get("pages", ""),
                             bool(p.get("replace", True)), name=name, check=ctx.check), protection
    if a == "background":
        return add_background(data, p.get("color", "#FFF8E1"), p.get("file", ""),
                              bool(p.get("first_page_separate")), p.get("pages", ""), bool(p.get("replace", True)),
                              check=ctx.check), protection
    if a in ("header_footer", "page_numbers"):
        numbering = plan.get("numbering", {}).get(a)
        first = int(p.get("start", 1))
        total = queue_total = None
        if numbering:
            first, total, queue_total = numbering
        values = {"nome": name, "data": datetime.now().strftime("%d/%m/%Y")}
        if a == "header_footer":
            texts = {"top-left": p.get("header_left", ""), "top-center": p.get("header_center", ""),
                     "top-right": p.get("header_right", ""), "bottom-left": p.get("footer_left", ""),
                     "bottom-center": p.get("footer_center", ""), "bottom-right": p.get("footer_right", "")}
            return add_header_footer(data, texts, float(p.get("fontsize", 9)), float(p.get("margin", 10.0)),
                                     p.get("color", "#000000"), first, total, queue_total, bool(p.get("mirror")),
                                     bool(p.get("skip_first")), bool(p.get("count_skipped", True)),
                                     bool(p.get("replace", True)), values, check=ctx.check), protection
        return add_header_footer(data, {p.get("position", "bottom-center"): p.get("text", "")},
                                 float(p.get("fontsize", 10)), float(p.get("margin", 10.0)), "#000000", first,
                                 total, queue_total, False, bool(p.get("skip_first")),
                                 bool(p.get("count_skipped", True)), True, values, check=ctx.check), protection
    if a == "bates":
        out, _last = add_bates(data, int(plan.get("bates_start", p.get("start", 1))), p.get("prefix", ""),
                               int(p.get("digits", 6)), p.get("suffix", ""), p.get("position", "bottom-right"),
                               float(p.get("fontsize", 9)), float(p.get("margin", 8.0)), check=ctx.check)
        return out, protection
    if a == "remove_marks":
        kinds = {k for k in MARK_KINDS if p.get(k)}
        out, n = remove_marks(data, kinds)
        if not n:
            raise BatchError("MRK-01", _("Nessun elemento riconoscibile da rimuovere."), status="skipped")
        return out, protection
    if a == "insert_pages":
        out, warns = insert_pages(data, p.get("file", ""), p.get("where", "start"), int(p.get("number", 1)))
        ctx.warnings.extend(warns)
        return out, protection
    if a == "ocr":
        fn = _ocr_function()
        if fn is None:
            raise BatchError("DEP-01", _("L'OCR non è disponibile: il modulo OCR non è installato."))
        mode = p.get("mode", "skip_text")
        if mode == "skip_text" and _has_text_everywhere(data):
            raise BatchError("OCR-01", _("Nessuna pagina da riconoscere."), status="skipped")
        langs = [x for x in re.split(r"[+,\s]+", p.get("languages", "ita+eng")) if x]
        return fn(data, langs, mode, None, None), protection
    raise BatchError("GEN-02", _("Operazione non gestita: {a}").format(a=a))


def _target_dir(item: QueueItem, settings: JobSettings) -> str:
    src_dir = os.path.dirname(item.path)
    if settings.destination_mode == "same":
        return src_dir
    if settings.destination_mode == "subfolder":
        return os.path.join(src_dir, settings.subfolder or "elaborati")
    dest = settings.destination
    if settings.keep_structure and settings.input_root:
        rel = os.path.relpath(src_dir, os.path.realpath(settings.input_root))
        if not rel.startswith(".."):
            dest = os.path.join(dest, rel) if rel != "." else dest
    return dest


def _resolve_conflict(path: str, settings: JobSettings, reserved: set[str]) -> str | None:
    """Percorso finale secondo la politica, o None se il file va saltato."""
    if settings.conflict == "overwrite":
        if path in reserved:
            pass
        else:
            return path
    exists = os.path.exists(path) or path in reserved
    if not exists:
        return path
    if settings.conflict == "skip":
        return None
    stem, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{stem} ({n}){ext}"
        if not os.path.exists(cand) and cand not in reserved:
            return cand
        n += 1


def _write_output(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        atomic_write(path, data)
    except DocumentError as e:
        raise BatchError("WRT-01", str(e)) from e


def run_job(progress, items: list[QueueItem], settings: JobSettings, started: datetime | None = None,
            printer: str | None = None) -> JobReport:
    """Esegue il lavoro; non solleva per gli errori dei singoli file né per l'annullamento."""
    started = started or datetime.now()
    items = list(items)   # coda congelata
    report = JobReport([FileResult(it.path) for it in items], list(settings.steps), started,
                       settings=settings.to_dict())
    steps = list(settings.steps)
    merge_index = next((i for i, s in enumerate(steps) if ACTIONS.get(s.action, None) and
                        ACTIONS[s.action].kind == "merge"), None)
    pre_steps = steps if merge_index is None else steps[:merge_index]
    post_steps = [] if merge_index is None else steps[merge_index + 1:]
    candidates = list(settings.passwords)
    csv_passwords: dict = {}
    for s in steps:
        if s.action == "remove_password":
            candidates += _candidates(s.merged_params().get("passwords", ""))
        if s.action == "protect" and s.merged_params().get("password_csv"):
            try:
                csv_passwords = _load_password_csv(s.merged_params()["password_csv"])
            except BatchError as e:
                for r in report.results:
                    r.status, r.code, r.message = "error", e.code, e.message
                report.finished = datetime.now()
                return report
    # conteggio pagine per numerazioni continue e Bates
    if any(s.action in ("bates", "header_footer", "page_numbers") for s in steps):
        for it in items:
            if not it.pages:
                try:
                    it.pages = analyze_file(it.path).pages
                except Exception:
                    pass
    plan = _bates_plan(items, settings)
    reserved: set[str] = set()
    merged_parts: list[tuple[str, bytes]] = []
    n = max(1, len(items))
    stop = False

    def update(frac: float, text: str) -> None:
        if progress is not None:
            progress.update(frac, text)

    for i, item in enumerate(items):
        res = report.results[i]
        if stop:
            break
        t0 = time.monotonic()
        ctx = _Ctx(progress, i, len(items))
        try:
            update(i / n, _("File {i} di {n}: {name}").format(i=i + 1, n=len(items), name=item.name))
            try:
                with open(item.path, "rb") as f:
                    raw = f.read()
            except FileNotFoundError:
                raise BatchError("FILE-01", _("File non trovato: è stato spostato o eliminato."))
            except OSError as e:
                raise BatchError("FILE-01", _("File non leggibile: {e}").format(e=e.strerror or e))
            res.bytes_in = len(raw)
            data, protection, cand_no = _open_input(raw, item, candidates)
            if cand_no and cand_no > (1 if item.password else 0) and any(s.action == "remove_password"
                                                                           for s in steps):
                ctx.warnings.append(_("sbloccato con la candidata n. {n}").format(
                    n=cand_no - (1 if item.password else 0)))
            res.pages = _page_count(data)
            for k, step in enumerate(pre_steps):
                spec = ACTIONS.get(step.action)
                if spec is None:
                    raise BatchError("GEN-02", _("Operazione sconosciuta: {a}").format(a=step.action))
                if spec.kind in ("pdf",):
                    try:
                        data, protection = _apply_step(step, data, item, ctx, plan[i], protection, csv_passwords)
                    except (BatchError, OperationCancelled):
                        raise
                    except Exception as e:
                        raise BatchError("GEN-01", _("Passo {k} ({a}) non riuscito: {e}").format(
                            k=k + 1, a=_(spec.label), e=e)) from e
                    ctx.check()
                elif spec.kind == "print":
                    _do_print(data, step, item, ctx, printer, protection)
                elif spec.kind == "convert":
                    res.outputs = _do_convert(data, step, item, i, settings, started, res.pages, reserved, ctx)
            if merge_index is not None:
                merged_parts.append((Path(item.path).stem, data))
                res.status = "ok"
            elif any(ACTIONS[s.action].kind in ("print", "convert") for s in pre_steps if s.action in ACTIONS):
                res.status = "ok"
                if res.outputs:
                    res.bytes_out = sum(os.path.getsize(o) for o in res.outputs if os.path.exists(o))
            else:
                if protection:
                    data = _reencrypt(data, protection)
                if any(s.action == "compress" for s in steps) and len(data) >= len(raw):
                    data = raw
                    ctx.warnings.append(_("nessuna riduzione ottenuta"))
                ctx.check()
                if settings.replace_originals:
                    if settings.backup:
                        bak = item.path + ".bak"
                        tmp = bak + ".tmp"
                        shutil.copy2(item.path, tmp)
                        os.replace(tmp, bak)
                    target = item.path
                else:
                    name = render_name(settings.pattern, item.path, i, settings, started, res.pages, plan[i])
                    target = _resolve_conflict(os.path.join(_target_dir(item, settings), name), settings, reserved)
                    if target is None:
                        raise BatchError("EXIST-01", _("Saltato: esiste già."), status="skipped")
                reserved.add(target)
                _write_output(target, data)
                res.outputs = [target]
                res.bytes_out = len(data)
                res.status = "ok"
            if ctx.warnings and res.status == "ok":
                res.status = "warning"
                res.message = "; ".join(ctx.warnings)
        except OperationCancelled:
            res.status = "cancelled"
            res.message = _("Annullato dall'utente.")
            report.cancelled = True
            stop = True
        except BatchError as e:
            res.status, res.code, res.message = e.status, e.code, e.message
            if e.status == "error" and settings.error_policy == "stop":
                stop = True
        except Exception as e:  # errore imprevisto: codice generico
            res.status, res.code, res.message = "error", "GEN-01", _("Errore imprevisto: {e}").format(e=e)
            if settings.error_policy == "stop":
                stop = True
        res.duration_ms = int((time.monotonic() - t0) * 1000)

    if merge_index is not None and not report.cancelled:
        _finish_merge(report, items, merged_parts, steps[merge_index], post_steps, settings, started, plan,
                      progress)
    if progress is not None and not report.cancelled:
        try:
            progress.update(1.0, _("Completato"))
        except OperationCancelled:
            pass
    report.finished = datetime.now()
    return report


def _finish_merge(report: JobReport, items, parts, step: Step, post_steps, settings, started, plan,
                  progress) -> None:
    failed = [r for r in report.results if r.status in ("error", "skipped")]
    if failed or len(parts) != len(items):
        for r in report.results:
            if r.status == "ok":
                r.status = "skipped"
                r.code = "MRG-01"
                r.message = _("Unione non eseguita: un file della coda non è stato elaborato ({f}).").format(
                    f=os.path.basename(failed[0].input) if failed else "")
        return
    p = step.merged_params()
    try:
        data = merge_pdfs(parts, bool(p.get("bookmarks", True)), bool(p.get("blank_after_odd")),
                          check=(progress.check if progress is not None and hasattr(progress, "check") else None))
        dummy = QueueItem(path=os.path.join(os.path.dirname(items[0].path), (p.get("name") or "Unione") + ".pdf"))
        ctx = _Ctx(progress, 0, 1)
        for s in post_steps:
            if ACTIONS[s.action].kind == "pdf":
                data, _prot = _apply_step(s, data, dummy, ctx, {}, {}, {})
        pattern = settings.pattern if settings.pattern not in ("{nome}", "") else "{nome}"
        name = render_name(pattern, dummy.path, 0, settings, started, _page_count(data))
        target = _resolve_conflict(os.path.join(_target_dir(QueueItem(dummy.path), settings) if
                                                settings.destination_mode != "folder" else settings.destination,
                                                name), settings, set())
        if target is None:
            raise BatchError("EXIST-01", _("Saltato: esiste già."), status="skipped")
        _write_output(target, data)
        for r in report.results:
            r.outputs = [target]
        report.results[-1].bytes_out = len(data)
    except OperationCancelled:
        report.cancelled = True
        for r in report.results:
            r.status, r.message = "cancelled", _("Annullato dall'utente.")
    except BatchError as e:
        for r in report.results:
            r.status, r.code, r.message = e.status, e.code, e.message


def _do_print(data: bytes, step: Step, item: QueueItem, ctx: _Ctx, printer: str | None, protection: dict) -> None:
    p = step.merged_params()
    if protection.get("_restricted") and not (protection.get("permissions", -1) & pymupdf.PDF_PERM_PRINT):
        raise BatchError("PERM-01", _("La stampa non è consentita per questo file (serve la password dei permessi)."))
    count = _page_count(data)
    pages = str(p.get("pages", "") or "").strip()
    if pages:
        selected, oor = parse_pages(pages, count)
        if oor:
            ctx.warnings.append(_("alcune pagine richieste non esistono: stampate solo quelle esistenti"))
        if not selected:
            raise BatchError("PRN-02", _("Nessuna pagina da stampare."))
        pages = ",".join(str(x + 1) for x in selected)
    fd, tmp = tempfile.mkstemp(prefix="foliant-stampa-", suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        cmd = _print_command(tmp, p.get("printer") or printer, int(p.get("copies", 1)), p.get("duplex", "none"),
                             pages)
        _run_print(cmd)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


_IMAGE_FORMATS = {"png", "jpg", "jpeg", "tiff"}


def _do_convert(data: bytes, step: Step, item: QueueItem, index: int, settings: JobSettings, started: datetime,
                pages: int, reserved: set[str], ctx: _Ctx) -> list[str]:
    fn = _convert_function()
    if fn is None:
        raise BatchError("DEP-01", _("La conversione non è disponibile: il modulo Converti non è installato."))
    p = step.merged_params()
    fmt = p.get("format", "docx")
    ext = ".pdf" if fmt == "pdfa" else "." + fmt
    options = {k: v for k, v in p.items() if k not in ("format",)}
    dest = _target_dir(item, settings)
    os.makedirs(dest, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=".foliant-conv-", dir=dest)
    outputs: list[str] = []
    try:
        src = os.path.join(tmp_dir, "ingresso.pdf")
        with open(src, "wb") as f:
            f.write(data)
        out_tmp = os.path.join(tmp_dir, "uscita" + ext)
        ret = fn(src, out_tmp, fmt, progress=None, **options)
        produced = ret if isinstance(ret, (list, tuple)) else [ret or out_tmp]
        produced = [x for x in produced if x and os.path.exists(x)]
        if not produced:
            raise BatchError("CNV-01", _("La conversione non ha prodotto alcun file."))
        pattern = settings.pattern
        multi = len(produced) > 1 or fmt in _IMAGE_FORMATS
        if multi and "{pagina" not in pattern and "{parte" not in pattern:
            pattern += "_{pagina:3}"
        for k, path in enumerate(sorted(produced, key=natural_key)):
            name = render_name(pattern, item.path, index, settings, started, pages, {"pagina": k + 1, "parte": k + 1},
                               os.path.splitext(path)[1] or ext)
            target = _resolve_conflict(os.path.join(dest, name), settings, reserved)
            if target is None:
                raise BatchError("EXIST-01", _("Saltato: esiste già."), status="skipped")
            reserved.add(target)
            os.replace(path, target)
            outputs.append(target)
        return outputs
    except BatchError:
        for o in outputs:
            try:
                os.unlink(o)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =============================================================================
# Sequenze salvate (BAT-20)
# =============================================================================
def validate_sequence(steps: list[Step]) -> list[str]:
    errors = []
    for i, s in enumerate(steps):
        spec = ACTIONS.get(s.action)
        if spec is None:
            errors.append(_("Operazione sconosciuta: {a}").format(a=s.action))
        elif spec.kind in ("convert", "print") and i != len(steps) - 1:
            errors.append(_("«{a}» deve essere l'ultimo passo della sequenza.").format(a=_(spec.label)))
    return errors


def _sequence_filename(name: str) -> str:
    return (_sanitize(name) or "sequenza") + ".json"


def save_sequence(directory: str, name: str, steps: list[Step]) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, _sequence_filename(name))
    data = {"format": "foliant-sequence", "version": 1, "name": name, "steps": [s.to_dict() for s in steps]}
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return path


def load_sequence(path: str) -> tuple[str, list[Step]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise DocumentError(_("Sequenza non leggibile: {f}").format(f=os.path.basename(path))) from e
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise DocumentError(_("Il file non contiene una sequenza di Foliant."))
    steps = [Step.from_dict(s) for s in data["steps"] if isinstance(s, dict)]
    return str(data.get("name") or Path(path).stem), steps


def list_sequences(directory: str) -> list[tuple[str, str]]:
    out = []
    if not os.path.isdir(directory):
        return out
    for fn in sorted(os.listdir(directory), key=natural_key):
        if fn.endswith(".json"):
            path = os.path.join(directory, fn)
            try:
                name, _steps = load_sequence(path)
            except DocumentError:
                continue
            out.append((name, path))
    return out


def delete_sequence(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


__all__ = [n for n in dir() if not n.startswith("_") and n not in ("annotations",)] + [
    "_ocr_function", "_convert_function", "_print_command", "Any"]
