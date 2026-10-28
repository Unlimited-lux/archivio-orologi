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

from foliant.api import ENGINE_LOCK, DocumentError, OperationCancelled, _, atomic_write, placement

INPUT_EXTENSIONS = (".pdf",)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")
TEXT_EXTENSIONS = (".txt", ".md")
OFFICE_EXTENSIONS = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".doc", ".xls", ".ppt", ".rtf")
CREATE_EXTENSIONS = IMAGE_EXTENSIONS + TEXT_EXTENSIONS + OFFICE_EXTENSIONS
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


def run_ocr(fn: Callable, data: bytes, languages: list[str], redo: bool = False) -> bytes:
    """Chiama `ocr_pdf_bytes` del modulo OCR in modalità «ricercabile» (aspetto invariato).

    `redo`: rifà anche il livello OCR delle pagine che hanno già testo invisibile."""
    import inspect
    try:
        accepts_options = "options" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        accepts_options = False
    if accepts_options:
        try:
            from foliant.modules.ocr.engine import REPLACE, SKIP, OcrOptions
            opts = OcrOptions(languages=list(languages), existing_text=REPLACE if redo else SKIP)
            return fn(data, list(languages), "searchable", None, None, options=opts)
        except ImportError:
            pass
    return fn(data, list(languages), "searchable", None, None)


def _pdfa_function() -> Callable | None:
    """`convert_to_pdfa(data, level) -> (byte, resoconto)` del modulo Converti (CNV-14)."""
    try:
        from foliant.modules.convert.pdfa import convert_to_pdfa
    except Exception:
        return None
    return convert_to_pdfa


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
    if path.lower().endswith(CREATE_EXTENSIONS):
        return item   # verrà convertito in PDF (BAT-07)
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


_UNTRANSLATED_DEFAULTS = {"languages"}   # codici tecnici, non testi


@dataclass
class ActionSpec:
    id: str
    label: str
    suffix: str
    params: list[Param]
    kind: str = "pdf"

    def defaults(self) -> dict:
        """Valori predefiniti; i testi predefiniti (es. «RISERVATO») nella lingua dell'interfaccia."""
        out = {}
        for p in self.params:
            value = p.default
            if p.type == "str" and isinstance(value, str) and value and p.key not in _UNTRANSLATED_DEFAULTS:
                value = _(value)
            out[p.key] = value
        return out

    def secret_keys(self) -> set[str]:
        return {p.key for p in self.params if p.secret}


POSITIONS = [("top-left", "In alto a sinistra"), ("top-center", "In alto al centro"),
             ("top-right", "In alto a destra"), ("bottom-left", "In basso a sinistra"),
             ("bottom-center", "In basso al centro"), ("bottom-right", "In basso a destra")]


def _build_actions() -> dict[str, ActionSpec]:
    pos = [(k, v) for k, v in POSITIONS]
    actions = [
        ActionSpec("create_pdf", "Crea PDF da altri formati", "pdf", [
            Param("page_size", "Formato pagina (immagini)", "choice", "a4",
                  [("a4", "A4"), ("letter", "Letter"), ("original", "Dimensione originale")]),
            Param("fit", "Adattamento", "choice", "fit",
                  [("fit", "Adatta mantenendo le proporzioni"), ("fill", "Riempi la pagina"),
                   ("center", "Centra a dimensione reale")]),
            Param("margin", "Margine (mm)", "float", 0.0, minimum=0, maximum=100),
        ], kind="create"),
        ActionSpec("pdfa", "Converti in PDF/A con verifica", "pdfa", [
            Param("level", "Livello", "choice", "2b", [("1b", "PDF/A-1b"), ("2b", "PDF/A-2b"), ("3b", "PDF/A-3b")]),
            Param("save_anyway", "Se la conformità non è raggiunta, salva comunque (con avviso)", "bool", False),
        ]),
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
            Param("series", "Serie (nome, facoltativo)", "str", ""),
            Param("continue_series", "Continua la serie (ultimo numero usato + 1)", "bool", False),
            Param("position", "Posizione", "position", "bottom-right", pos),
            Param("fontsize", "Dimensione (pt)", "int", 9, minimum=4, maximum=72),
            Param("margin", "Margine (mm)", "float", 8.0, minimum=0, maximum=100),
            Param("box", "Riquadro bianco dietro il numero (scansioni)", "bool", False),
            Param("font", "Font", "choice", "helv",
                  [("helv", "Helvetica"), ("hebo", "Helvetica grassetto"), ("tiro", "Times"),
                   ("tibo", "Times grassetto"), ("cour", "Courier"), ("cobo", "Courier grassetto")]),
        ]),
        ActionSpec("remove_marks", "Rimuovi filigrane, sfondi, intestazioni e Bates", "pulito", [
            Param("watermark", "Filigrane", "bool", True),
            Param("background", "Sfondi", "bool", False),
            Param("header_footer", "Intestazioni, piè e numeri di pagina", "bool", False),
            Param("bates", "Numeri Bates", "bool", False),
            Param("scope", "Ambito", "choice", "foliant",
                  [("foliant", "Solo elementi aggiunti da Foliant"),
                   ("marked", "Tutti gli elementi marcati come tali (anche da altri programmi)")]),
        ]),
        ActionSpec("insert_pages", "Inserisci pagine", "inserito", [
            Param("file", "PDF da inserire (vuoto = pagina bianca)", "path", ""),
            Param("where", "Dove", "choice", "start",
                  [("start", "All'inizio"), ("end", "Alla fine"), ("after", "Dopo la pagina"),
                   ("every", "Dopo ogni N pagine")]),
            Param("number", "Pagina / N", "int", 1, minimum=1),
        ]),
        ActionSpec("rotate", "Ruota pagine", "ruotato", [
            Param("degrees", "Rotazione", "choice", "90",
                  [("90", "90° in senso orario"), ("180", "180°"), ("270", "90° in senso antiorario")]),
            Param("pages", "Pagine (vuoto = tutte, «dispari», «pari», 1-3,5)", "pages", ""),
        ]),
        ActionSpec("delete_pages", "Elimina pagine", "ridotto", [
            Param("mode", "Cosa eliminare", "choice", "range",
                  [("range", "Intervallo di pagine"), ("blank", "Pagine bianche")]),
            Param("pages", "Pagine (es. 1-3,5)", "pages", ""),
        ]),
        ActionSpec("extract_pages", "Estrai pagine", "estratto", [
            Param("pages", "Pagine (es. 1-3,5)", "pages", "1"),
        ]),
        ActionSpec("split", "Dividi", "parte", [
            Param("mode", "Modalità", "choice", "every",
                  [("every", "Ogni N pagine"), ("bookmarks", "Per segnalibri di primo livello")]),
            Param("every_n", "N pagine", "int", 1, minimum=1),
        ], kind="multi"),
        ActionSpec("flatten", "Appiattisci annotazioni e moduli", "appiattito", [
            Param("annotations", "Annotazioni", "bool", True),
            Param("widgets", "Campi modulo", "bool", True),
        ]),
        ActionSpec("sanitize", "Sanifica", "sanificato", [
            Param("metadata", "Metadati e XMP", "bool", True),
            Param("javascript", "JavaScript", "bool", True),
            Param("embedded_files", "File incorporati", "bool", True),
            Param("links", "Collegamenti", "bool", False),
            Param("hidden_text", "Testo nascosto", "bool", True),
        ]),
        ActionSpec("metadata", "Imposta proprietà e metadati", "proprieta", [
            Param("title", "Titolo ({nome} = nome del file)", "str", ""),
            Param("author", "Autore", "str", ""),
            Param("subject", "Oggetto", "str", ""),
            Param("keywords", "Parole chiave", "str", ""),
        ]),
        ActionSpec("initial_view", "Imposta la vista iniziale", "vista", [
            Param("layout", "Layout pagina", "choice", "SinglePage",
                  [("SinglePage", "Pagina singola"), ("OneColumn", "Scorrimento continuo"),
                   ("TwoPageLeft", "Due pagine"), ("TwoPageRight", "Due pagine con copertina")]),
            Param("mode", "Pannello all'apertura", "choice", "UseNone",
                  [("UseNone", "Nessuno"), ("UseOutlines", "Segnalibri"), ("UseThumbs", "Miniature"),
                   ("FullScreen", "Schermo intero")]),
        ]),
        ActionSpec("redact", "Redigi per testo o schema", "redatto", [
            Param("pattern", "Schema", "choice", "text",
                  [("text", "Testo libero"), ("codice_fiscale", "Codice fiscale"), ("iban", "IBAN"),
                   ("email", "Indirizzo e-mail")]),
            Param("text", "Testo da redigere (uno per riga)", "text", ""),
            Param("ignore_case", "Ignora maiuscole/minuscole", "bool", True),
        ]),
        ActionSpec("linearize", "Ottimizza per il web", "web", []),
        ActionSpec("form_export", "Esporta i dati dei moduli", "dati", [
            Param("format", "Formato", "choice", "csv",
                  [("csv", "CSV"), ("json", "JSON"), ("xfdf", "XFDF"), ("fdf", "FDF"), ("xml", "XML")]),
        ], kind="multi"),
        ActionSpec("form_import", "Importa i dati dei moduli", "compilato", [
            Param("file", "File dei dati (CSV con colonna «File» per file diversi, JSON, XFDF, FDF, XML)",
                  "path", ""),
        ]),
        ActionSpec("export_images", "Esporta tutte le immagini", "immagine", [
            Param("min_size", "Dimensione minima (px)", "int", 16, minimum=1),
        ], kind="multi"),
        ActionSpec("merge", "Unisci in un file", "unito", [
            Param("bookmarks", "Un segnalibro per file", "bool", True),
            Param("blank_after_odd", "Pagina bianca dopo i file con pagine dispari", "bool", False),
            Param("name", "Nome del file unito", "str", "Unione"),
            Param("group", "Modalità", "choice", "all",
                  [("all", "Un unico file"), ("folder", "Un file per cartella"), ("every", "Un file ogni N ingressi")]),
            Param("every_n", "N (per «un file ogni N ingressi»)", "int", 10, minimum=1),
            Param("on_error", "Se un file è in errore", "choice", "stop",
                  [("stop", "Interrompi l'unione"), ("omit", "Ometti il file e prosegui")]),
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
    if action_id == "pdfa" and _pdfa_function() is None:
        return False, _("La conversione in PDF/A non è disponibile: il modulo Converti non è installato.")
    if action_id == "convert" and _convert_function() is None:
        return False, _("La conversione non è disponibile: il modulo Converti non è installato.")
    return True, ""


@dataclass
class Step:
    action: str
    params: dict = field(default_factory=dict)
    ask: list = field(default_factory=list)     # parametri «chiedi all'avvio»
    enabled: bool = True

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
        out = {"action": self.action, "params": params}
        if self.ask:
            out["ask"] = list(self.ask)
        if not self.enabled:
            out["enabled"] = False
        return out

    def ask_keys(self) -> list[str]:
        """Parametri da chiedere all'avvio: quelli marcati e le password vuote."""
        spec = ACTIONS.get(self.action)
        keys = list(self.ask)
        if spec:
            for p in spec.params:
                if p.secret and p.type == "password" and not (self.params or {}).get(p.key) and p.key not in keys:
                    keys.append(p.key)
        return keys

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(str(data.get("action", "")), dict(data.get("params") or {}), list(data.get("ask") or []),
                   bool(data.get("enabled", True)))


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
    conflict_choices: dict = field(default_factory=dict)   # «chiedi ogni volta»: percorso -> scelta

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
# segnaposti inglesi accettati accanto agli italiani (i modelli salvati restano validi)
PLACEHOLDER_ALIASES = {"name": "nome", "ext": "est", "folder": "cartella", "date": "data", "time": "ora",
                       "counter": "contatore", "pages": "pagine", "operation": "operazione", "page": "pagina",
                       "part": "parte", "bates_start": "bates_inizio", "bates_end": "bates_fine",
                       "total": "totale", "queue_total": "totale_coda"}
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
        return default_name_placeholder() + "_" + _(spec.suffix)
    return default_name_placeholder()


def default_name_placeholder() -> str:
    """«{nome}» o «{name}» secondo la lingua dell'interfaccia."""
    value = _("{nome}")
    return value if value in ("{nome}", "{name}") else "{nome}"


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
        name = PLACEHOLDER_ALIASES.get(name, name)
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
        name = PLACEHOLDER_ALIASES.get(name, name)
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


def estimated_output_size(items: list[QueueItem], settings: JobSettings) -> int:
    """Stima prudente dello spazio necessario per i risultati."""
    factor = 1.3
    for s in settings.steps:
        if s.action == "convert":
            factor = 8.0 if s.merged_params().get("format") in ("png", "jpg", "jpeg", "tiff") else 3.0
        elif s.action in ("ocr", "create_pdf"):
            factor = max(factor, 2.0)
    return int(sum(it.size for it in items) * factor)


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
        if spec.kind == "create" and i != 0:
            errors.append(_("«{a}» deve essere il primo passo della sequenza.").format(a=_(spec.label)))
        if spec.kind in ("convert", "print", "multi") and i != len(settings.steps) - 1:
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
        if step.action == "redact" and params.get("pattern", "text") == "text" \
                and not str(params.get("text", "")).strip():
            errors.append(_("Indica il testo da redigere."))
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
            elif probe:
                need = estimated_output_size(items, settings)
                try:
                    free = shutil.disk_usage(probe).free
                except OSError:
                    free = None
                if free is not None and need > free:
                    errors.append(_("Spazio insufficiente nella destinazione: servono circa {n} MB.").format(
                        n=max(1, need // (1024 * 1024))))
    if len(items) > 1 and not any(k in settings.pattern for k in ("{nome", "{contatore", "{bates")) \
            and not any(s.action == "merge" for s in settings.steps) and not settings.replace_originals:
        warnings.append(_("Il modello produce nomi non univoci: aggiungi {contatore} oppure i conflitti saranno "
                          "risolti secondo la politica scelta."))
    others = [it for it in items if not it.path.lower().endswith(".pdf")]
    if others and not any(s.action == "create_pdf" for s in settings.steps):
        warnings.append(_("{n} file non PDF saranno saltati: aggiungi «Crea PDF da altri formati».").format(
            n=len(others)))
    office = [it for it in items if it.path.lower().endswith(OFFICE_EXTENSIONS)]
    if office and any(s.action == "create_pdf" for s in settings.steps) and _office_command("x", "y") is None:
        warnings.append(_("{n} documenti Office non convertibili: LibreOffice non è installato.").format(
            n=len(office)))
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

    def pending_items(self) -> list[str]:
        """File lasciati a metà o non iniziati per un annullamento (non quelli in errore)."""
        return [r.input for r in self.results if r.status in ("cancelled", "not_processed")]

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
            parts.append(_("durata {s} s").format(s=f"{secs:.1f}".replace(".", ",")))
        return " · ".join(parts)


# =============================================================================
# Operazioni singole (bytes -> bytes)
# =============================================================================
def _open(data: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=data, filetype="pdf")


def _save(doc: pymupdf.Document, **opts) -> bytes:
    # no_new_id: senza un nuovo /ID casuale lo stesso lavoro dà file identici byte per byte (BAT-04)
    kw = {"garbage": 3, "deflate": True, "no_new_id": True}
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


def _stamp():
    """Timbri del modulo Pagine (PAG-36): stessa marcatura per le operazioni singole e in blocco."""
    try:
        from foliant.modules.pages import stamp
    except Exception:
        return None
    return stamp


_STAMP_KINDS = {"watermark": ("watermark",), "background": ("background",),
                "header_footer": ("headerfooter", "numbers"), "bates": ("bates",),
                "headerfooter_only": ("headerfooter",), "page_numbers": ("numbers",)}


def _remove_stamps(doc: pymupdf.Document, kinds) -> int:
    st = _stamp()
    names = [k2 for k in kinds for k2 in _STAMP_KINDS.get(k, ())]
    if st is None or not names:
        return 0
    try:
        return st.remove_watermarks(doc, None, names)
    except Exception:
        return 0


def _stamp_error(e: Exception) -> BatchError:
    return BatchError("STM-01", str(e))


def remove_marks(data: bytes, kinds: set[str] | list[str], check=None) -> tuple[bytes, int]:
    out, counts = remove_marks_detailed(data, kinds)
    return out, sum(counts.values())


# sottotipi degli artefatti marcati secondo lo standard (BAT-16, ambito «tutti gli elementi marcati»)
_ARTIFACT_SUBTYPES = {"watermark": ("Watermark",), "header_footer": ("Header", "Footer", "PageNum"),
                      "bates": ("Bates",)}
_BDC_RE = re.compile(rb"/Artifact\s*<<(.*?)>>\s*BDC", re.S)
_MARK_TOKEN_RE = re.compile(rb"\b(BDC|BMC|EMC)\b")


def _artifact_kind(props: bytes) -> str | None:
    if re.search(rb"/Type\s*/Background", props):
        return "background"
    if not re.search(rb"/Type\s*/Pagination", props):
        return None
    m = re.search(rb"/Subtype\s*/(\w+)", props)
    sub = m.group(1).decode() if m else ""
    for kind, subs in _ARTIFACT_SUBTYPES.items():
        if sub in subs:
            return kind
    return None


def _strip_artifacts(stream: bytes, kinds: set[str]) -> tuple[bytes, dict[str, int]]:
    """Toglie le sequenze /Artifact marcate dei tipi indicati (con le sequenze annidate)."""
    counts: dict[str, int] = {}
    out = bytearray()
    pos = 0
    while True:
        m = _BDC_RE.search(stream, pos)
        if m is None:
            break
        kind = _artifact_kind(m.group(1))
        if kind is None or kind not in kinds or MARK_KEY.encode() in m.group(1):
            out += stream[pos:m.end()]
            pos = m.end()
            continue
        depth = 1
        scan = m.end()
        end = None
        for t in _MARK_TOKEN_RE.finditer(stream, scan):
            if t.group(1) in (b"BDC", b"BMC"):
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    end = t.end()
                    break
        if end is None:   # sequenza non chiusa: non si tocca
            out += stream[pos:m.end()]
            pos = m.end()
            continue
        out += stream[pos:m.start()]
        pos = end
        counts[kind] = counts.get(kind, 0) + 1
    out += stream[pos:]
    return bytes(out), counts


def remove_marks_detailed(data: bytes, kinds: set[str] | list[str], all_marked: bool = False
                          ) -> tuple[bytes, dict[str, int]]:
    """Rimuove filigrane, sfondi, intestazioni/piè e Bates; restituisce (dati, conteggio per tipo).

    Predefinito: solo gli elementi aggiunti da Foliant (operazioni in blocco e modulo Pagine).
    `all_marked`: anche quelli che altri programmi hanno marcato come artefatto secondo lo
    standard e le annotazioni /Watermark. Il contenuto non marcato non viene mai toccato."""
    kinds = set(kinds)
    counts: dict[str, int] = {}

    def add(kind, n):
        if n:
            counts[kind] = counts.get(kind, 0) + n

    with ENGINE_LOCK:
        doc = _open(data)
        try:
            for kind in kinds:
                add(kind, _remove_stamps(doc, {kind}))
            for page in doc:
                for kind in kinds:
                    add(kind, _remove_marks_page(doc, page, {kind}))
            if all_marked:
                for page in doc:
                    for xref in page.get_contents():
                        stream = doc.xref_stream(xref) or b""
                        cleaned, found = _strip_artifacts(stream, kinds)
                        if found:
                            doc.update_stream(xref, cleaned)
                            for k, n in found.items():
                                add(k, n)
                    if "watermark" in kinds:
                        for annot in list(page.annots() or []):
                            if annot.type[1] == "Watermark":
                                page.delete_annot(annot)
                                add("watermark", 1)
            return (_save(doc) if counts else data), counts
        finally:
            doc.close()


def _image_dpis(doc, check=None) -> dict[int, float]:
    """Risoluzione effettiva di ogni immagine (la più bassa tra i suoi usi).

    Gli usi si leggono senza decodificare le immagini e si abbinano agli xref per
    dimensione in pixel; a parità di dimensione vale l'uso più grande (risoluzione più bassa)."""
    import math
    dpis: dict[int, float] = {}
    for page in doc:
        if check is not None:
            check()
        by_size: dict[tuple[int, int], list[int]] = {}
        for info in page.get_images(full=True):
            by_size.setdefault((info[2], info[3]), []).append(info[0])
        if not by_size:
            continue
        try:
            uses = page.get_image_info(hashes=False, xrefs=False)
        except Exception:
            uses = []
        for u in uses:
            t = u.get("transform") or (0, 0, 0, 0, 0, 0)
            dw, dh = math.hypot(t[0], t[1]), math.hypot(t[2], t[3])
            if dw < 1 or dh < 1:
                continue
            dpi = min(u["width"] / (dw / 72.0), u["height"] / (dh / 72.0))
            for xref in by_size.get((u["width"], u["height"]), []):
                dpis[xref] = min(dpis.get(xref, dpi), dpi)
    return dpis


def _image_samples(doc, xref: int):
    """(modo, dimensioni, pixel, maschera, lunghezza attuale) di un'immagine ricampionabile, o None.

    Solo immagini a 8 bit in scala di grigi o RGB, senza /Mask, /Decode o maschere a 1 bit:
    il resto (scansioni in bianco e nero, CMYK, maschere di colore) resta intatto."""
    keys = {k: doc.xref_get_key(xref, k) for k in ("Subtype", "ImageMask", "Mask", "Decode", "BitsPerComponent",
                                                     "Filter", "SMask")}
    if (keys["Subtype"][1] != "/Image" or keys["ImageMask"][1] == "true" or keys["Mask"][0] != "null"
            or keys["Decode"][0] != "null" or keys["BitsPerComponent"][1] not in ("8", "null")
            or any(f in keys["Filter"][1] for f in ("JBIG2", "CCITT", "JPX"))):
        return None
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        return None
    mask = None
    if keys["SMask"][0] == "xref":
        sx = int(keys["SMask"][1].split()[0])
        mpix = pymupdf.Pixmap(doc, sx)
        if mpix.n != 1 or mpix.alpha:
            return None
        mask = (sx, (mpix.width, mpix.height), mpix.samples)
    return ("L" if pix.n == 1 else "RGB", (pix.width, pix.height), pix.samples, mask,
            len(doc.xref_stream_raw(xref) or b""))


def _encode_resampled(sample, scale: float, quality: int):
    """Ricampiona e codifica in JPEG (solo Pillow: gira fuori dal blocco del motore)."""
    from PIL import Image
    mode, size, pixels, mask, old_len = sample
    w, h = max(1, round(size[0] * scale)), max(1, round(size[1] * scale))
    img = Image.frombytes(mode, size, pixels).resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    jpg = buf.getvalue()
    if len(jpg) >= old_len:
        return None
    new_mask = None
    if mask is not None:
        sx, msize, mpixels = mask
        new_mask = (sx, Image.frombytes("L", msize, mpixels).resize((w, h), Image.LANCZOS).tobytes())
    return jpg, (w, h), mode, new_mask


def _write_resampled(doc, xref: int, encoded) -> None:
    jpg, (w, h), mode, mask = encoded
    doc.update_stream(xref, jpg, compress=False)
    doc.xref_set_key(xref, "Filter", "/DCTDecode")
    doc.xref_set_key(xref, "DecodeParms", "null")
    doc.xref_set_key(xref, "Width", str(w))
    doc.xref_set_key(xref, "Height", str(h))
    doc.xref_set_key(xref, "BitsPerComponent", "8")
    doc.xref_set_key(xref, "ColorSpace", "/DeviceGray" if mode == "L" else "/DeviceRGB")
    if mask is not None:
        sx, mbytes = mask
        doc.update_stream(sx, mbytes, compress=True)
        doc.xref_set_key(sx, "DecodeParms", "null")
        doc.xref_set_key(sx, "Width", str(w))
        doc.xref_set_key(sx, "Height", str(h))
        doc.xref_set_key(sx, "BitsPerComponent", "8")


def compress_pdf(data: bytes, level: str = "medium", check=None) -> bytes:
    """Riduce le immagini oltre la soglia e pulisce il file.

    Ogni immagine è trattata una sola volta anche se compare su molte pagine, e tra
    un'immagine e l'altra il lavoro si può annullare (la riscrittura in blocco di MuPDF
    ricomprimeva l'immagine a ogni uso e non lasciava rispondere l'interfaccia).
    Ricampionamento e JPEG avvengono fuori dal blocco del motore, così più file
    compressi in parallelo procedono davvero insieme."""
    targets = {"low": (225, 200, 85), "medium": (180, 150, 75), "high": (110, 96, 55)}
    threshold, target, quality = targets.get(level, targets["medium"])
    with ENGINE_LOCK:
        doc = _open(data)
    try:
        with ENGINE_LOCK:
            dpis = _image_dpis(doc, check)
        for xref, dpi in dpis.items():
            if dpi <= threshold:
                continue
            if check is not None:
                check()
            try:
                with ENGINE_LOCK:
                    sample = _image_samples(doc, xref)
                encoded = _encode_resampled(sample, target / dpi, quality) if sample else None
                if encoded:
                    with ENGINE_LOCK:
                        _write_resampled(doc, xref, encoded)
            except Exception:
                pass   # immagine non gestibile: resta com'è
        with ENGINE_LOCK:
            try:
                doc.subset_fonts()
            except Exception:
                pass
            return doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True,
                               use_objstms=1, no_new_id=True)
    finally:
        with ENGINE_LOCK:
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
        key = PLACEHOLDER_ALIASES.get(m.group(1), m.group(1))
        return str(values[key]) if key in values else m.group(0)
    return re.sub(r"\{(\w+)\}", repl, text or "")


def add_watermark(data: bytes, text: str, fontsize: int = 60, opacity: int = 30, rotation: int = 45,
                  color: str = "#C00000", pages: str = "", replace: bool = True, name: str = "", check=None) -> bytes:
    text = _expand(text, {"nome": name})
    st = _stamp()
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            selected, _oor = parse_pages(pages, doc.page_count)
            if st is not None:
                if replace:
                    _remove_stamps(doc, {"watermark"})
                    for page in doc:
                        _remove_marks_page(doc, page, {"watermark"})
                if selected and text.strip():
                    if check:
                        check()
                    try:
                        st.add_watermark(doc, selected, text=text, font_size=fontsize, color=_rgb(color),
                                         opacity=max(0.01, min(1.0, opacity / 100.0)), rotation=rotation,
                                         position="center")
                    except Exception as e:
                        raise _stamp_error(e) from e
                return _save(doc)
            if replace:
                for page in doc:
                    _remove_marks_page(doc, page, {"watermark"})
            for pno in selected:
                if check:
                    check()
                page = doc[pno]
                before = page.get_contents()
                r = page.rect   # pagina visualizzata (rotazione inclusa)
                width = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
                derot = page.derotation_matrix
                center = pymupdf.Point(r.width / 2, r.height / 2) * derot
                start = pymupdf.Point(r.width / 2 - width / 2, r.height / 2 + fontsize * 0.35) * derot
                page.insert_text(start, text, fontsize=fontsize, fontname="helv", color=_rgb(color),
                                 fill_opacity=max(0.01, min(1.0, opacity / 100.0)), rotate=page.rotation,
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
                _remove_stamps(doc, {"background"})
                for page in doc:
                    _remove_marks_page(doc, page, {"background"})
            selected, _oor = parse_pages(pages, doc.page_count)
            st = _stamp()
            if src is None and st is not None:
                if selected:
                    try:
                        st.add_background(doc, selected, color=_rgb(color))
                    except Exception as e:
                        raise _stamp_error(e) from e
                return _save(doc)
            for k, pno in enumerate(selected):
                if check:
                    check()
                page = doc[pno]
                before = page.get_contents()
                # placement corregge le pagine ruotate e ritagliate (page.rect è la pagina visualizzata)
                area = placement.page_rect(page)
                if src is not None:
                    spage = 0 if (not first_page_separate or pno == 0 or src.page_count < 2) else 1
                    placement.show_pdf_page(page, area, src, spage, overlay=False, upright=True)
                else:
                    placement.draw_rect(page, area, color=None, fill=_rgb(color), overlay=False, width=0)
                _mark_new_contents(doc, page, before, "background")
            return _save(doc)
        finally:
            doc.close()
            if src is not None:
                src.close()


def _place(page: pymupdf.Page, text: str, position: str, fontsize: float, margin_pt: float,
           color=(0, 0, 0), box: bool = False) -> None:
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
    if box:   # riquadro bianco dietro il testo, per la leggibilità sulle scansioni
        rect = pymupdf.Rect(x - 2, y - fontsize, x + width + 2, y + fontsize * 0.35) * page.derotation_matrix
        rect.normalize()
        placement.draw_rect(page, rect, color=None, fill=(1, 1, 1), width=0)
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
            st = _stamp()
            if replace:
                _remove_stamps(doc, {"page_numbers" if kind == "page_numbers" else "headerfooter_only"})
                if kind != "page_numbers":   # i vecchi numeri erano marcati come intestazioni
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
                placed: dict[str, str] = {}
                for pos, text in texts.items():
                    if not text:
                        continue
                    where = pos
                    if mirror and (pno + 1) % 2 == 0:
                        where = pos.replace("left", "@").replace("right", "left").replace("@", "right")
                    placed[where] = _expand(text, vals)
                if st is not None and placed:
                    # marcatura comune con il modulo Pagine (PAG-36): rimovibile da entrambe le parti
                    try:
                        if kind == "page_numbers":
                            for where, text in placed.items():
                                st.add_page_numbers(doc, [pno], fmt=text, position=where, margin=margin,
                                                    font_size=fontsize, color=_rgb(color))
                        else:
                            slots = {("header_" if w.startswith("top") else "footer_") + w.split("-")[1]: t
                                     for w, t in placed.items()}
                            st.add_header_footer(doc, [pno], margin=margin, font_size=fontsize,
                                                 color=_rgb(color), **slots)
                    except Exception as e:
                        raise _stamp_error(e) from e
                else:
                    for where, text in placed.items():
                        _place(page, text, where, fontsize, margin, _rgb(color))
                    _mark_new_contents(doc, page, before, kind)
                number += 1
            return _save(doc)
        finally:
            doc.close()


def add_page_numbers(data: bytes, text: str | None = None, position: str = "bottom-center",
                     fontsize: float = 10, margin_mm: float = 10.0, first_number: int = 1, **kw) -> bytes:
    if text is None:
        text = _("Pagina {pagina} di {totale}")
    kw.setdefault("kind", "page_numbers")
    return add_header_footer(data, {position: text}, fontsize=fontsize, margin_mm=margin_mm,
                             first_number=first_number, **kw)


def bates_label(n: int, prefix: str = "", digits: int = 6, suffix: str = "") -> str:
    return f"{prefix}{str(n).zfill(digits)}{suffix}"


def add_bates(data: bytes, start: int = 1, prefix: str = "", digits: int = 6, suffix: str = "",
              position: str = "bottom-right", fontsize: float = 9, margin_mm: float = 8.0,
              check=None, box: bool = False, font: str = "helv") -> tuple[bytes, int]:
    """Numera le pagine; restituisce (dati, ultimo_numero)."""
    margin = margin_mm * 72 / 25.4
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            _remove_stamps(doc, {"bates"})
            for page in doc:
                _remove_marks_page(doc, page, {"bates"})
            st = _stamp()
            if st is not None and not box and doc.page_count:
                if check:
                    check()
                try:
                    st.add_bates(doc, None, prefix=prefix, suffix=suffix, start=start, digits=digits,
                                 position=position, margin=margin, font_size=fontsize, font=font or "helv")
                except Exception as e:
                    raise _stamp_error(e) from e
                return _save(doc), start + doc.page_count - 1
            n = start
            for page in doc:
                if check:
                    check()
                before = page.get_contents()
                _place(page, bates_label(n, prefix, digits, suffix), position, fontsize, margin, box=box)
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
                        # segnalibri originali del file, annidati sotto il suo
                        for level, sub, pno, *_rest in src.get_toc(simple=True):
                            if 1 <= pno <= src.page_count:
                                toc.append([level + 1, sub, start + pno])
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
class SkipFile(Exception):
    """«Annulla file»: scarta il file corrente e passa al successivo."""


class JobControl:
    """Pausa/ripresa e annullamento del solo file corrente (BAT-04).

    I comandi arrivano dal thread dell'interfaccia; il lavoro li osserva nei
    punti sicuri (tra un file e l'altro e a ogni pagina)."""

    def __init__(self) -> None:
        import threading
        self._paused = threading.Event()
        self._skip = threading.Event()
        self.disk_full = False   # in pausa perché la destinazione è piena

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def skip_current(self) -> None:
        self._skip.set()

    def checkpoint(self, progress=None) -> None:
        while self._paused.is_set():
            if progress is not None and getattr(progress, "cancelled", False):
                break
            time.sleep(0.05)
        if self._skip.is_set():
            self._skip.clear()
            raise SkipFile()


class _Ctx:
    """Stato per un file in elaborazione."""

    def __init__(self, progress, index: int, total_files: int, control: JobControl | None = None):
        self.progress = progress
        self.index = index
        self.total_files = total_files
        self.control = control
        self.warnings: list[str] = []
        self.notes: list[str] = []   # informazioni sull'esito (non avvisi)

    def check(self) -> None:
        if self.control is not None:
            self.control.checkpoint(self.progress)
        if self.progress is not None:
            self.progress.check() if hasattr(self.progress, "check") else self.progress.update()


PAGE_SIZES = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}


def _office_command(src: str, outdir: str) -> list[str] | None:
    """Comando di conversione dei documenti Office (sostituibile nei test)."""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    return [exe, "--headless", "--convert-to", "pdf", "--outdir", outdir, src]


def image_to_pdf(path: str, page_size: str = "a4", fit: str = "fit", margin_mm: float = 0.0,
                 warnings: list | None = None) -> bytes:
    """Immagine (anche TIFF multipagina) -> PDF, con orientamento EXIF applicato."""
    from PIL import Image, ImageOps, ImageSequence
    try:
        img = Image.open(path)
    except Exception as e:
        raise BatchError("IMG-01", _("Immagine non leggibile: {e}").format(e=e)) from e
    frames = []
    if path.lower().endswith(".gif"):
        frames = [img.copy()]
        if getattr(img, "n_frames", 1) > 1 and warnings is not None:
            warnings.append(_("GIF animata: usato solo il primo fotogramma"))
    else:
        frames = [f.copy() for f in ImageSequence.Iterator(img)]
    margin = margin_mm * 72 / 25.4
    with ENGINE_LOCK:
        out = pymupdf.open()
        for frame in frames:
            frame = ImageOps.exif_transpose(frame)
            dpi = frame.info.get("dpi", (96, 96))
            try:
                dx, dy = float(dpi[0]) or 96.0, float(dpi[1]) or 96.0
            except Exception:
                dx = dy = 96.0
            if frame.mode in ("CMYK", "P", "LA", "I;16", "I", "F", "1", "L") and frame.mode != "L":
                frame = frame.convert("RGB")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            w_pt, h_pt = frame.width * 72.0 / dx, frame.height * 72.0 / dy
            if page_size == "original":
                pw, ph = w_pt + 2 * margin, h_pt + 2 * margin
            else:
                pw, ph = PAGE_SIZES.get(page_size, PAGE_SIZES["a4"])
                if w_pt > h_pt:
                    pw, ph = ph, pw
            page = out.new_page(width=pw, height=ph)
            area = pymupdf.Rect(margin, margin, pw - margin, ph - margin)
            if fit == "fill" and page_size != "original":
                target, keep = area, False
            elif fit == "center" and page_size != "original":
                x0 = area.x0 + (area.width - w_pt) / 2
                y0 = area.y0 + (area.height - h_pt) / 2
                target, keep = pymupdf.Rect(x0, y0, x0 + w_pt, y0 + h_pt), True
            else:
                target, keep = area, True
            page.insert_image(target, stream=buf.getvalue(), keep_proportion=keep)
        data = out.tobytes(garbage=3, deflate=True, no_new_id=True)
        out.close()
    return data


def to_pdf_bytes(path: str, params: dict, warnings: list | None = None) -> bytes:
    """Converte un file non PDF in PDF (BAT-07)."""
    low = path.lower()
    if low.endswith(IMAGE_EXTENSIONS):
        return image_to_pdf(path, params.get("page_size", "a4"), params.get("fit", "fit"),
                            float(params.get("margin", 0.0) or 0.0), warnings)
    if low.endswith(TEXT_EXTENSIONS):
        with ENGINE_LOCK:
            try:
                src = pymupdf.open(path, filetype="txt")
                data = src.convert_to_pdf()
                src.close()
            except Exception as e:
                raise BatchError("FMT-02", _("Testo non convertibile: {e}").format(e=e)) from e
        return data
    if low.endswith(OFFICE_EXTENSIONS):
        with tempfile.TemporaryDirectory(prefix="foliant-office-") as tmp:
            cmd = _office_command(path, tmp)
            if cmd is None:
                raise BatchError("DEP-02", _("Motore di conversione dei documenti non disponibile (LibreOffice)."))
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            except Exception as e:
                raise BatchError("FMT-03", _("Documento non convertibile (forse protetto da password).")) from e
            out = os.path.join(tmp, Path(path).stem + ".pdf")
            if not os.path.exists(out):
                raise BatchError("FMT-03", _("Documento non convertibile (forse protetto da password)."))
            with open(out, "rb") as f:
                return f.read()
    raise BatchError("FMT-01", _("Formato non ammesso per l'operazione."))


def _open_input(data: bytes, item: QueueItem, candidates: list[str]) -> tuple[bytes, dict, int | None]:
    """Apre il file; restituisce (dati in chiaro, opzioni di cifratura originali, n. candidata)."""
    if not data.strip():
        raise BatchError("PDF-01", _("File vuoto (0 byte): probabilmente la copia o il download non è "
                                     "andato a buon fine."))
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
            plain = doc.tobytes(garbage=0, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE, no_new_id=True)
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
        out = compress_pdf(data, p.get("level", "medium"), check=ctx.check)
        if len(out) >= len(data):
            # nessun guadagno: si tiene il risultato del passo precedente (mai l'originale:
            # gli altri passi della sequenza, es. la protezione, devono restare applicati)
            if _("nessuna riduzione ottenuta") not in ctx.warnings:
                ctx.warnings.append(_("nessuna riduzione ottenuta"))
            return data, protection
        return out, protection
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
                                 bool(p.get("count_skipped", True)), True, values, kind="page_numbers",
                                 check=ctx.check), protection
    if a == "bates":
        out, _last = add_bates(data, int(plan.get("bates_start", p.get("start", 1))), p.get("prefix", ""),
                               int(p.get("digits", 6)), p.get("suffix", ""), p.get("position", "bottom-right"),
                               float(p.get("fontsize", 9)), float(p.get("margin", 8.0)), check=ctx.check,
                               box=bool(p.get("box", False)), font=p.get("font", "helv"))
        return out, protection
    if a == "remove_marks":
        kinds = {k for k in MARK_KINDS if p.get(k)}
        out, counts = remove_marks_detailed(data, kinds, p.get("scope") == "marked")
        if not counts:
            raise BatchError("MRK-01", _("Saltato: nessun elemento riconoscibile (si può intervenire con la "
                                         "modifica manuale)."), status="skipped")
        names = {"watermark": _("filigrane"), "background": _("sfondi"),
                 "header_footer": _("intestazioni/piè/numeri"), "bates": _("numeri Bates")}
        ctx.notes.append(_("rimossi: {d}").format(d=", ".join(f"{n} {names[k]}" for k, n in sorted(counts.items()))))
        return out, protection
    if a == "insert_pages":
        out, warns = insert_pages(data, p.get("file", ""), p.get("where", "start"), int(p.get("number", 1)))
        ctx.warnings.extend(warns)
        return out, protection
    if a == "pdfa":
        fn = _pdfa_function()
        if fn is None:
            raise BatchError("DEP-01", _("La conversione in PDF/A non è disponibile: il modulo Converti non è "
                                         "installato."))
        level = str(p.get("level", "2b"))
        try:
            out, info = fn(data, level)
        except Exception as e:   # es. Ghostscript mancante: messaggio del modulo Converti
            raise BatchError("PDFA-01", str(e)) from e
        errors = list((info or {}).get("errors") or [])
        if errors and not p.get("save_anyway"):
            raise BatchError("PDFA-02", _("Conformità PDF/A-{l} non raggiunta: {e}").format(
                l=level, e="; ".join(errors[:5])))
        if errors:
            ctx.warnings.append(_("conformità PDF/A-{l} non raggiunta: {e}").format(l=level, e="; ".join(errors[:5])))
        else:
            ctx.notes.append(_("verifica PDF/A-{l}: conforme").format(l=level))
        return out, {}   # PDF/A non ammette la cifratura
    if a == "ocr":
        fn = _ocr_function()
        if fn is None:
            raise BatchError("DEP-01", _("L'OCR non è disponibile: il modulo OCR non è installato."))
        mode = p.get("mode", "skip_text")
        if mode == "skip_text" and _has_text_everywhere(data):
            raise BatchError("OCR-01", _("Nessuna pagina da riconoscere."), status="skipped")
        langs = [x for x in re.split(r"[+,\s]+", p.get("languages", "ita+eng")) if x]
        return run_ocr(fn, data, langs, redo=(mode == "all")), protection
    if a in EXTRA_PDF_ACTIONS:
        return EXTRA_PDF_ACTIONS[a](data, p, item, ctx), protection
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
    policy = settings.conflict_choices.get(path, settings.conflict)
    if policy == "ask":            # nessuna risposta registrata: come «rinomina»
        policy = "rename"
    if policy == "overwrite":
        if path in reserved:
            pass
        else:
            return path
    exists = os.path.exists(path) or path in reserved
    if not exists:
        return path
    if policy == "skip":
        return None
    stem, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{stem} ({n}){ext}"
        if not os.path.exists(cand) and cand not in reserved:
            return cand
        n += 1


def planned_conflicts(items: list[QueueItem], settings: JobSettings, started: datetime) -> list[tuple[str, str]]:
    """Per «chiedi ogni volta»: (ingresso, uscita già esistente) dei file da un PDF a un PDF."""
    if settings.replace_originals or any(ACTIONS.get(s.action) and ACTIONS[s.action].kind != "pdf"
                                         for s in settings.steps if s.enabled):
        return []
    plan = _bates_plan(items, settings)
    out = []
    for i, item in enumerate(items):
        name = render_name(settings.pattern, item.path, i, settings, started, item.pages, plan[i])
        target = os.path.join(_target_dir(item, settings), name)
        if os.path.exists(target):
            out.append((item.path, target))
    return out


class DiskFull(Exception):
    """Spazio esaurito nella destinazione (il file temporaneo è già stato rimosso)."""


def _is_disk_full(e: BaseException) -> bool:
    import errno
    for x in (e, e.__cause__):
        if isinstance(x, OSError) and x.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            return True
    return False


def _write_output(path: str, data: bytes) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        atomic_write(path, data)
    except (DocumentError, OSError) as e:
        if _is_disk_full(e):
            raise DiskFull() from e
        raise BatchError("WRT-01", str(e)) from e


def _write_waiting(path: str, data: bytes, control: "JobControl | None", progress) -> None:
    """Scrive il risultato; con il disco pieno il lavoro va in pausa finché l'utente libera spazio
    e riprende (si riprova lo stesso file) oppure annulla (BAT-02)."""
    while True:
        try:
            _write_output(path, data)
            return
        except DiskFull:
            if control is None:
                raise BatchError("DISK-01", _("Spazio esaurito nella destinazione: il file non è stato scritto."))
            control.disk_full = True
            control.pause()
            control.checkpoint(progress)
            control.disk_full = False
            if progress is not None and getattr(progress, "cancelled", False):
                raise OperationCancelled()


def _compute_pdf(item: QueueItem, ctx: "_Ctx", steps, pre_steps, candidates, plan_i, csv_passwords):
    """Lettura e passi PDF→PDF di un file (eseguibile in parallelo: non scrive su disco).

    Restituisce (byte letti, PDF di partenza, risultato, protezione, pagine)."""
    if getattr(ctx, "abort", False):
        raise OperationCancelled()
    try:
        with open(item.path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        raise BatchError("FILE-01", _("File non trovato: è stato spostato o eliminato."))
    except OSError as e:
        raise BatchError("FILE-01", _("File non leggibile: {e}").format(e=e.strerror or e))
    bytes_in = len(raw)
    create = next((s for s in steps if s.action == "create_pdf"), None)
    if not item.path.lower().endswith(".pdf") and item.path.lower().endswith(CREATE_EXTENSIONS):
        if create is None:
            raise BatchError("FMT-01", _("Formato non ammesso per l'operazione."), status="skipped")
        raw = to_pdf_bytes(item.path, create.merged_params(), ctx.warnings)
    data, protection, cand_no = _open_input(raw, item, candidates)
    if cand_no and cand_no > (1 if item.password else 0) and any(s.action == "remove_password" for s in steps):
        ctx.warnings.append(_("sbloccato con la candidata n. {n}").format(n=cand_no - (1 if item.password else 0)))
    pages = _page_count(data)
    for k, step in enumerate(pre_steps):
        spec = ACTIONS[step.action]
        if spec.kind != "pdf":
            continue
        try:
            data, protection = _apply_step(step, data, item, ctx, plan_i, protection, csv_passwords)
        except (BatchError, OperationCancelled, SkipFile):
            raise
        except Exception as e:
            raise BatchError("GEN-01", _("Passo {k} ({a}) non riuscito: {e}").format(
                k=k + 1, a=_(spec.label), e=e)) from e
        ctx.check()
    return bytes_in, raw, data, protection, pages


def run_job(progress, items: list[QueueItem], settings: JobSettings, started: datetime | None = None,
            printer: str | None = None, control: JobControl | None = None,
            journal: str | None = None) -> JobReport:
    """Esegue il lavoro; non solleva per gli errori dei singoli file né per l'annullamento.

    `control` permette pausa e «annulla file»; `journal` è il percorso del giornale
    persistente, aggiornato dopo ogni file (ripresa dopo un'interruzione)."""
    started = started or datetime.now()
    items = list(items)   # coda congelata
    report = JobReport([FileResult(it.path) for it in items], list(settings.steps), started,
                       settings=settings.to_dict())
    steps = [s for s in settings.steps if s.enabled]
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
    merged_parts: list[tuple[int, str, bytes]] = []
    n = max(1, len(items))
    stop = False

    def update(frac: float, text: str) -> None:
        if control is not None:
            control.checkpoint(progress)
        if progress is not None:
            progress.update(frac, text)

    write_journal(journal, report, items, settings, finished=False)
    futures = ctxs = pool = None
    parallel_ok = (settings.workers > 1 and merge_index is None and len(items) > 1
                   and all(ACTIONS.get(s.action) and ACTIONS[s.action].kind in ("pdf", "create") for s in pre_steps))
    if parallel_ok:
        from concurrent.futures import ThreadPoolExecutor
        ctxs = [_Ctx(progress, i, len(items), control) for i in range(len(items))]
        pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="foliant-batch")
        futures = [pool.submit(_compute_pdf, it, ctxs[k], steps, pre_steps, candidates, plan[k], csv_passwords)
                   for k, it in enumerate(items)]
    for i, item in enumerate(items):
        res = report.results[i]
        if stop:
            break
        t0 = time.monotonic()
        ctx = _Ctx(progress, i, len(items), control)
        try:
            update(i / n, _("File {i} di {n}: {name}").format(i=i + 1, n=len(items), name=item.name))
            if futures is not None:
                # elaborazione parallela: qui si raccoglie il risultato, nell'ordine della coda
                ctx = ctxs[i]
                res.bytes_in, raw, data, protection, res.pages = futures[i].result()
            else:
                try:
                    with open(item.path, "rb") as f:
                        raw = f.read()
                except FileNotFoundError:
                    raise BatchError("FILE-01", _("File non trovato: è stato spostato o eliminato."))
                except OSError as e:
                    raise BatchError("FILE-01", _("File non leggibile: {e}").format(e=e.strerror or e))
                res.bytes_in = len(raw)
                create = next((s for s in steps if s.action == "create_pdf"), None)
                if not item.path.lower().endswith(".pdf") and item.path.lower().endswith(CREATE_EXTENSIONS):
                    if create is None:
                        raise BatchError("FMT-01", _("Formato non ammesso per l'operazione."), status="skipped")
                    raw = to_pdf_bytes(item.path, create.merged_params(), ctx.warnings)
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
                        except (BatchError, OperationCancelled, SkipFile):
                            raise
                        except Exception as e:
                            raise BatchError("GEN-01", _("Passo {k} ({a}) non riuscito: {e}").format(
                                k=k + 1, a=_(spec.label), e=e)) from e
                        ctx.check()
                    elif spec.kind == "create":
                        pass   # conversione già fatta all'apertura
                    elif spec.kind == "print":
                        _do_print(data, step, item, ctx, printer, protection)
                    elif spec.kind == "convert":
                        res.outputs = _do_convert(data, step, item, i, settings, started, res.pages, reserved, ctx)
                    elif spec.kind == "multi":
                        res.outputs = _do_multi(data, step, item, i, settings, started, res.pages, reserved, ctx)
            if merge_index is not None:
                merged_parts.append((i, Path(item.path).stem, data))
                res.status = "ok"
            elif any(ACTIONS[s.action].kind in ("print", "convert", "multi") for s in pre_steps
                     if s.action in ACTIONS):
                res.status = "ok"
                if res.outputs:
                    res.bytes_out = sum(os.path.getsize(o) for o in res.outputs if os.path.exists(o))
            else:
                if protection:
                    data = _reencrypt(data, protection)
                # solo per una sequenza fatta unicamente di «Comprimi»: se il file non si riduce si
                # riscrive l'originale (che mantiene anche la sua eventuale protezione)
                if pre_steps and all(s.action == "compress" for s in pre_steps) and len(data) >= len(raw):
                    data = raw
                    if _("nessuna riduzione ottenuta") not in ctx.warnings:
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
                _write_waiting(target, data, control, progress)
                res.outputs = [target]
                res.bytes_out = len(data)
                res.status = "ok"
            if ctx.warnings and res.status == "ok":
                res.status = "warning"
                res.message = "; ".join(ctx.notes + ctx.warnings)
            elif ctx.notes and res.status == "ok":
                res.message = "; ".join(ctx.notes)
            elif res.status == "ok" and len(pre_steps) > 1 and not res.message:
                res.message = _("passi completati: {p}").format(p=", ".join(action_label(s.action)
                                                                              for s in pre_steps))
        except SkipFile:
            res.status, res.code = "cancelled", "USR-02"
            res.message = _("File annullato dall'utente.")
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
        write_journal(journal, report, items, settings, finished=False)
    if pool is not None:   # file non ancora iniziati (annullamento o «fermati al primo errore»)
        for f in futures:
            f.cancel()
        for c in ctxs:
            c.abort = True
        pool.shutdown(wait=True)

    if merge_index is not None and not report.cancelled:
        _finish_merge(report, items, merged_parts, steps[merge_index], post_steps, settings, started, plan,
                      progress)
    if progress is not None and not report.cancelled:
        try:
            progress.update(1.0, _("Completato"))
        except OperationCancelled:
            pass
    report.finished = datetime.now()
    write_journal(journal, report, items, settings, finished=True)
    return report


# =============================================================================
# Giornale persistente e cronologia dei rapporti (BAT-04, BAT-05)
# =============================================================================
def write_journal(path: str | None, report: JobReport, items: list[QueueItem], settings: JobSettings,
                  finished: bool) -> None:
    """Giornale del lavoro (senza segreti). Alla fine del lavoro viene rimosso."""
    if not path:
        return
    if finished:
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    data = {"format": "foliant-batch-journal", "version": 1, "started": report.started.isoformat(),
            "settings": settings.to_dict(),
            "files": [{"path": it.path, "status": r.status} for it, r in zip(items, report.results)]}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        atomic_write(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass   # il giornale non deve mai far fallire il lavoro


def interrupted_job(path: str) -> tuple[list[str], JobSettings] | None:
    """Lavoro interrotto (es. chiusura anomala): file non completati e impostazioni."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("format") != "foliant-batch-journal":
        return None
    pending = [f["path"] for f in data.get("files", []) if f.get("status") not in ("ok", "warning", "skipped")]
    if not pending:
        return None
    st = dict(data.get("settings") or {})
    steps = [Step.from_dict(x) for x in st.pop("steps", [])]
    fields = JobSettings.__dataclass_fields__
    settings = JobSettings(steps=steps, **{k: v for k, v in st.items() if k in fields and k != "steps"})
    return pending, settings


def discard_journal(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


HISTORY_LIMIT = 20


def save_history(directory: str, report: JobReport) -> str:
    """Conserva il rapporto tra gli ultimi 20 consultabili."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"lavoro_{report.started:%Y%m%d_%H%M%S_%f}.json")
    atomic_write(path, report.to_json().encode("utf-8"))
    files = sorted(f for f in os.listdir(directory) if f.startswith("lavoro_") and f.endswith(".json"))
    for old in files[:-HISTORY_LIMIT]:
        try:
            os.unlink(os.path.join(directory, old))
        except OSError:
            pass
    return path


def list_history(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    files = sorted((f for f in os.listdir(directory) if f.startswith("lavoro_") and f.endswith(".json")),
                   reverse=True)
    return [os.path.join(directory, f) for f in files]


def load_report(path: str) -> JobReport:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    job = data.get("lavoro", {})
    results = [FileResult(input=x.get("file_ingresso", ""), outputs=list(x.get("file_uscita") or []),
                          status=x.get("esito", "not_processed"), code=x.get("codice", ""),
                          message=x.get("messaggio", ""), pages=x.get("pagine", 0),
                          bytes_in=x.get("byte_ingresso", 0), bytes_out=x.get("byte_uscita", 0),
                          duration_ms=x.get("durata_ms", 0)) for x in data.get("file", [])]
    started = datetime.fromisoformat(job["inizio"]) if job.get("inizio") else datetime.now()
    finished = datetime.fromisoformat(job["fine"]) if job.get("fine") else None
    steps = [Step.from_dict(s) for s in job.get("passi", [])]
    return JobReport(results, steps, started, finished, bool(job.get("annullato")), job.get("impostazioni") or {})


def report_pdf(report: JobReport) -> bytes:
    """Rapporto del lavoro in PDF per l'archiviazione."""
    from .report import _Writer
    with ENGINE_LOCK:
        doc = pymupdf.open()
        try:
            w = _Writer(doc)
            w.line(_("Rapporto dell'elaborazione in blocco"), size=16, gap=10)
            w.line(_("Inizio: {d}").format(d=report.started.strftime("%d/%m/%Y %H:%M:%S")), size=9)
            if report.finished:
                w.line(_("Fine: {d}").format(d=report.finished.strftime("%d/%m/%Y %H:%M:%S")), size=9)
            w.line(_("Operazioni: {o}").format(o=", ".join(action_label(s.action) for s in report.steps)), size=9)
            w.line(report.summary(), size=10, gap=10)
            for r in report.results:
                w.line(f"{r.status_label} · {os.path.basename(r.input)}", size=9.5, gap=2)
                detail = " ".join(x for x in (r.code, r.message) if x)
                outs = ", ".join(os.path.basename(o) for o in r.outputs)
                if outs:
                    w.line("→ " + outs, size=8, indent=12, gap=2, color=(0.3, 0.3, 0.3))
                if detail:
                    w.line(detail, size=8, indent=12, gap=2, color=(0.5, 0.2, 0.1))
                w.space(3)
            return doc.tobytes(garbage=3, deflate=True, no_new_id=True)
        finally:
            doc.close()


def _merge_groups(items, parts, p: dict) -> list[tuple[str, str, list[tuple[int, str, bytes]]]]:
    """Gruppi da unire: (cartella di riferimento, nome, parti)."""
    name = p.get("name") or _("Unione")
    mode = p.get("group", "all")
    if mode == "folder":
        groups: dict[str, list] = {}
        for part in parts:
            groups.setdefault(os.path.dirname(items[part[0]].path), []).append(part)
        return [(folder, os.path.basename(folder) or name, g) for folder, g in groups.items()]
    if mode == "every":
        n = max(1, int(p.get("every_n", 10) or 10))
        chunks = [parts[k:k + n] for k in range(0, len(parts), n)]
        return [(os.path.dirname(items[c[0][0]].path), f"{name}_{k + 1:03d}", c) for k, c in enumerate(chunks)]
    return [(os.path.dirname(items[parts[0][0]].path) if parts else "", name, parts)]


def _finish_merge(report: JobReport, items, parts, step: Step, post_steps, settings, started, plan,
                  progress) -> None:
    p = step.merged_params()
    failed = [r for r in report.results if r.status in ("error", "skipped", "cancelled")]
    if (failed and p.get("on_error", "stop") == "stop") or not parts:
        for r in report.results:
            if r.status in ("ok", "warning"):
                r.status = "skipped"
                r.code = "MRG-01"
                r.message = _("Unione non eseguita: un file della coda non è stato elaborato ({f}).").format(
                    f=os.path.basename(failed[0].input) if failed else "")
        return
    if failed:
        note = _("file omessi dall'unione: {f}").format(f=", ".join(os.path.basename(r.input) for r in failed))
        for _i, _t, _d in parts:
            r = report.results[_i]
            r.status = "warning"
            r.message = "; ".join(x for x in (r.message, note) if x)
    # segnalibri distinguibili per file omonimi di cartelle diverse: «relazione (a)», «relazione (b)»
    counts: dict[str, int] = {}
    for _i, t, _d in parts:
        counts[t] = counts.get(t, 0) + 1
    parts = [(i, f"{t} ({os.path.basename(os.path.dirname(items[i].path)) or '/'})" if counts[t] > 1 else t, d)
             for i, t, d in parts]
    reserved: set[str] = set()
    check = progress.check if progress is not None and hasattr(progress, "check") else None
    for folder, gname, gparts in _merge_groups(items, parts, p):
        try:
            data = merge_pdfs([(t, d) for _i, t, d in gparts], bool(p.get("bookmarks", True)),
                              bool(p.get("blank_after_odd")), check=check)
            dummy = QueueItem(path=os.path.join(folder, gname + ".pdf"))
            ctx = _Ctx(progress, 0, 1)
            for s in post_steps:
                if ACTIONS[s.action].kind == "pdf":
                    data, _prot = _apply_step(s, data, dummy, ctx, {}, {}, {})
            pattern = settings.pattern if settings.pattern not in ("{nome}", "") else "{nome}"
            name = render_name(pattern, dummy.path, 0, settings, started, _page_count(data))
            target_dir = settings.destination if settings.destination_mode == "folder" else _target_dir(dummy, settings)
            target = _resolve_conflict(os.path.join(target_dir, name), settings, reserved)
            if target is None:
                raise BatchError("EXIST-01", _("Saltato: esiste già."), status="skipped")
            reserved.add(target)
            _write_output(target, data)
            for _i, _t, _d in gparts:
                report.results[_i].outputs = [target]
            report.results[gparts[-1][0]].bytes_out = len(data)
        except OperationCancelled:
            report.cancelled = True
            for _i, _t, _d in gparts:
                report.results[_i].status = "cancelled"
                report.results[_i].message = _("Annullato dall'utente.")
            return
        except BatchError as e:
            for _i, _t, _d in gparts:
                r = report.results[_i]
                r.status, r.code, r.message = e.status, e.code, e.message


# =============================================================================
# Indice e serie Bates (BAT-15)
# =============================================================================
def bates_index(items: list[QueueItem], settings: JobSettings) -> list[tuple[str, str, str]]:
    """Per ogni file: (nome, primo numero, ultimo numero)."""
    plan = _bates_plan(items, settings)
    return [(it.name, plan[i].get("bates_inizio", ""), plan[i].get("bates_fine", ""))
            for i, it in enumerate(items) if plan[i].get("bates_inizio")]


def bates_index_csv(items: list[QueueItem], settings: JobSettings, sep: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\n")
    w.writerow(["file", "bates_inizio", "bates_fine"])
    for row in bates_index(items, settings):
        w.writerow(row)
    return buf.getvalue()


def _series_path(directory: str) -> str:
    return os.path.join(directory, "serie-bates.json")


def load_series(directory: str) -> dict[str, int]:
    try:
        with open(_series_path(directory), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def prepare_bates_series(settings: JobSettings, directory: str) -> None:
    """«Continua la serie»: il numero iniziale diventa l'ultimo usato + 1."""
    series = load_series(directory)
    for step in settings.steps:
        if step.action == "bates":
            p = step.merged_params()
            name = str(p.get("series") or "").strip()
            if name and p.get("continue_series") and name in series:
                step.params["start"] = series[name] + 1


def record_bates_series(settings: JobSettings, items: list[QueueItem], report: JobReport, directory: str) -> None:
    """Aggiorna la serie con l'ultimo numero usato dai file completati."""
    series = load_series(directory)
    changed = False
    plan = _bates_plan(items, settings)
    for step in settings.steps:
        if step.action != "bates":
            continue
        p = step.merged_params()
        name = str(p.get("series") or "").strip()
        if not name:
            continue
        last = None
        for i, it in enumerate(items):
            if report.results[i].status in ("ok", "warning") and "bates_start" in plan[i]:
                last = plan[i]["bates_start"] + max(1, it.pages) - 1
        if last is not None:
            series[name] = max(series.get(name, 0), last)
            changed = True
    if changed:
        os.makedirs(directory, exist_ok=True)
        atomic_write(_series_path(directory), json.dumps(series, ensure_ascii=False, indent=1).encode("utf-8"))


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
# Operazioni aggiuntive (BAT-23)
# =============================================================================
REDACT_PATTERNS = {
    "codice_fiscale": r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]",
    "iban": r"IT\d{2}[A-Z]\d{10}[0-9A-Z]{12}",
    "email": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
}


def _x_rotate(data: bytes, p: dict, item, ctx) -> bytes:
    deg = int(p.get("degrees", 90) or 90)
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            pages, oor = parse_pages(str(p.get("pages", "") or ""), doc.page_count)
            if oor:
                ctx.warnings.append(_("alcune pagine richieste non esistono"))
            for n in pages:
                page = doc[n]
                page.set_rotation((page.rotation + deg) % 360)
            return _save(doc)
        finally:
            doc.close()


def _is_blank(page: pymupdf.Page) -> bool:
    if page.get_text("text").strip() or page.get_images() or any(True for _a in page.annots()):
        return False
    try:
        if page.get_drawings():
            return False
    except Exception:
        pass
    return True


def _x_delete_pages(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if p.get("mode", "range") == "blank":
                pages = [n for n in range(doc.page_count) if _is_blank(doc[n])]
                if not pages:
                    raise BatchError("PAG-02", _("Saltato: nessuna pagina bianca."), status="skipped")
            else:
                spec = str(p.get("pages", "") or "").strip()
                if not spec:
                    raise BatchError("PAR-01", _("Indica le pagine da eliminare."))
                pages, oor = parse_pages(spec, doc.page_count)
                if oor:
                    ctx.warnings.append(_("alcune pagine richieste non esistono"))
            if len(set(pages)) >= doc.page_count:
                raise BatchError("PAG-01", _("Il documento resterebbe senza pagine."))
            doc.delete_pages(sorted(set(pages)))
            ctx.notes.append(_("{n} pagine eliminate").format(n=len(set(pages))))
            return _save(doc)
        finally:
            doc.close()


def _x_extract_pages(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            pages, oor = parse_pages(str(p.get("pages", "") or ""), doc.page_count)
            if not pages:
                raise BatchError("PAG-03", _("Nessuna delle pagine richieste esiste in questo file."))
            if oor:
                ctx.warnings.append(_("alcune pagine richieste non esistono"))
            doc.select(pages)
            return _save(doc)
        finally:
            doc.close()


def _x_flatten(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            if hasattr(doc, "bake"):
                doc.bake(annots=bool(p.get("annotations", True)), widgets=bool(p.get("widgets", True)))
            else:   # motore vecchio: riscrive le pagine come immagini vettoriali non è possibile
                raise BatchError("DEP-03", _("Il motore PDF non supporta l'appiattimento."))
            return _save(doc)
        finally:
            doc.close()


def _x_sanitize(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            opts = dict(metadata=bool(p.get("metadata", True)), xml_metadata=bool(p.get("metadata", True)),
                        javascript=bool(p.get("javascript", True)), embedded_files=bool(p.get("embedded_files", True)),
                        attached_files=bool(p.get("embedded_files", True)), remove_links=bool(p.get("links", False)),
                        hidden_text=bool(p.get("hidden_text", True)), reset_fields=False, reset_responses=False,
                        clean_pages=True, redactions=False, thumbnails=True)
            if hasattr(doc, "scrub"):
                doc.scrub(**opts)
            else:
                if opts["metadata"]:
                    doc.set_metadata({})
                    doc.del_xml_metadata()
            return _save(doc, garbage=4)
        finally:
            doc.close()


def _xmp_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def build_xmp(title: str = "", author: str = "", subject: str = "", keywords: str = "") -> str:
    def alt(tag, value):
        return (f"<dc:{tag}><rdf:Alt><rdf:li xml:lang=\"x-default\">{_xmp_escape(value)}</rdf:li></rdf:Alt>"
                f"</dc:{tag}>") if value else ""
    creator = (f"<dc:creator><rdf:Seq><rdf:li>{_xmp_escape(author)}</rdf:li></rdf:Seq></dc:creator>"
               if author else "")
    kw = f"<pdf:Keywords>{_xmp_escape(keywords)}</pdf:Keywords>" if keywords else ""
    return ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            f'{alt("title", title)}{creator}{alt("description", subject)}{kw}'
            '<xmp:CreatorTool>Foliant</xmp:CreatorTool>'
            '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


def _x_metadata(data: bytes, p: dict, item, ctx) -> bytes:
    values = {"nome": Path(item.path).stem, "data": datetime.now().strftime("%d/%m/%Y")}
    fields = {k: _expand(str(p.get(k, "") or ""), values) for k in ("title", "author", "subject", "keywords")}
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            meta = dict(doc.metadata or {})
            for k, v in fields.items():
                if v:
                    meta[k] = v
            meta = {k: v for k, v in meta.items() if k in ("title", "author", "subject", "keywords", "creator",
                                                            "producer", "creationDate", "modDate") and v is not None}
            doc.set_metadata(meta)
            doc.set_xml_metadata(build_xmp(meta.get("title", ""), meta.get("author", ""), meta.get("subject", ""),
                                           meta.get("keywords", "")))
            return _save(doc)
        finally:
            doc.close()


def _x_initial_view(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            doc.set_pagelayout(str(p.get("layout", "SinglePage")))
            doc.set_pagemode(str(p.get("mode", "UseNone")))
            return _save(doc)
        finally:
            doc.close()


def _x_redact(data: bytes, p: dict, item, ctx) -> bytes:
    kind = p.get("pattern", "text")
    flags = re.IGNORECASE if p.get("ignore_case", True) else 0
    if kind == "text":
        terms = [t.strip() for t in str(p.get("text", "") or "").splitlines() if t.strip()]
        if not terms:
            raise BatchError("RED-02", _("Indica il testo da redigere."))
        regex = re.compile("|".join(re.escape(t) for t in terms), flags)
    else:
        regex = re.compile(REDACT_PATTERNS[kind], flags if kind == "email" else 0)
    count = 0
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            for page in doc:
                found = []
                # corrispondenze nelle singole parole
                for w in page.get_text("words"):
                    if regex.fullmatch(w[4]) or (kind != "text" and regex.search(w[4])):
                        found.append(pymupdf.Rect(w[:4]))
                # testo libero su più parole
                if kind == "text":
                    text = page.get_text("text")
                    for m in {m.group(0) for m in regex.finditer(text)}:
                        if " " in m.strip():
                            found.extend(page.search_for(m))
                for r in found:
                    page.add_redact_annot(r, fill=(0, 0, 0))
                if found:
                    count += len(found)
                    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS
                                          if hasattr(pymupdf, "PDF_REDACT_IMAGE_PIXELS") else 2)
                ctx.check()
            if not count:
                raise BatchError("RED-01", _("Saltato: nessuna corrispondenza."), status="skipped")
            ctx.notes.append(_("{n} redazioni").format(n=count))
            return _save(doc, garbage=4)
        finally:
            doc.close()


def _x_linearize(data: bytes, p: dict, item, ctx) -> bytes:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            try:
                return doc.tobytes(garbage=4, deflate=True, linear=True, no_new_id=True)
            except Exception:
                ctx.warnings.append(_("linearizzazione non supportata dal motore: file solo ottimizzato"))
                return doc.tobytes(garbage=4, deflate=True, clean=True, no_new_id=True)
        finally:
            doc.close()


def _forms():
    try:
        from foliant.modules.forms import engine as fe
        from foliant.modules.forms import interchange as fx
    except Exception:
        raise BatchError("DEP-01", _("I dati dei moduli non sono disponibili: il modulo Moduli non è installato."))
    return fe, fx


def _form_export_parts(data: bytes, p: dict) -> list[tuple[bytes, str]]:
    fe, fx = _forms()
    fmt = p.get("format", "csv")
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            values = fe.form_values(doc)
        finally:
            doc.close()
    if not values:
        raise BatchError("FRM-01", _("Saltato: il file non contiene campi modulo."), status="skipped")
    return [(fx.export_data(fmt, values), "." + fmt)]


def _x_form_import(data: bytes, p: dict, item, ctx) -> bytes:
    fe, fx = _forms()
    path = str(p.get("file", "") or "")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raise BatchError("FILE-02", _("File dei dati non leggibile: {f}").format(f=os.path.basename(path) or "–"))
    fmt = fx.format_of(path, raw)
    try:
        if fmt == "csv":
            records = fx.csv_records(raw)
            keyed = [r for r in records if str(r.get("File", "")).strip()]
            if keyed:   # una riga per file, riconosciuto dal nome
                names = {item.name.lower(), Path(item.path).stem.lower()}
                rec = next((r for r in keyed if str(r["File"]).strip().lower() in names), None)
                if rec is None:
                    raise BatchError("FRM-02", _("Saltato: nessuna riga per questo file nei dati."), status="skipped")
                values = {k: v for k, v in rec.items() if k != "File"}
            else:
                values = records[0] if records else {}
        else:
            values = fx.parse_data(fmt, raw)
    except BatchError:
        raise
    except Exception as e:
        raise BatchError("FRM-03", _("Dati dei moduli non validi: {e}").format(e=e)) from e
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            applied, unknown = fe.apply_values(doc, values)
            if not applied:
                raise BatchError("FRM-04", _("Saltato: nessun campo corrisponde ai dati."), status="skipped")
            ctx.notes.append(_("{n} campi compilati").format(n=len(applied)))
            if unknown:
                ctx.warnings.append(_("campi sconosciuti: {c}").format(c=", ".join(unknown[:5])))
            return _save(doc)
        finally:
            doc.close()


EXTRA_PDF_ACTIONS = {
    "form_import": _x_form_import,
    "rotate": _x_rotate, "delete_pages": _x_delete_pages, "extract_pages": _x_extract_pages,
    "flatten": _x_flatten, "sanitize": _x_sanitize, "metadata": _x_metadata, "initial_view": _x_initial_view,
    "redact": _x_redact, "linearize": _x_linearize,
}


def _split_parts(data: bytes, p: dict) -> list[tuple[bytes, str]]:
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            n = doc.page_count
            ranges: list[tuple[int, int]] = []
            if p.get("mode", "every") == "bookmarks":
                starts = sorted({e[2] - 1 for e in doc.get_toc() if e[0] == 1 and e[2] >= 1})
                if not starts:
                    raise BatchError("SPL-01", _("Il file non ha segnalibri di primo livello."), status="skipped")
                if starts[0] != 0:
                    starts.insert(0, 0)
                ranges = [(a, (starts[k + 1] if k + 1 < len(starts) else n) - 1) for k, a in enumerate(starts)]
            else:
                step = max(1, int(p.get("every_n", 1) or 1))
                ranges = [(a, min(a + step, n) - 1) for a in range(0, n, step)]
            out = []
            for a, b in ranges:
                part = pymupdf.open()
                part.insert_pdf(doc, from_page=a, to_page=b)
                out.append((_save(part), ".pdf"))
                part.close()
            return out
        finally:
            doc.close()


def _image_parts(data: bytes, p: dict) -> list[tuple[bytes, str]]:
    minimum = int(p.get("min_size", 16) or 1)
    out = []
    seen = set()
    with ENGINE_LOCK:
        doc = _open(data)
        try:
            for page in doc:
                for img in page.get_images(full=True):
                    xref = img[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    try:
                        pix = pymupdf.Pixmap(doc, xref)
                        if pix.n - pix.alpha >= 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        if pix.width >= minimum and pix.height >= minimum:
                            out.append((pix.tobytes("png"), ".png"))
                    except Exception:
                        continue
        finally:
            doc.close()
    if not out:
        raise BatchError("IMG-02", _("Saltato: nessuna immagine nel file."), status="skipped")
    return out


def _do_multi(data: bytes, step: Step, item: QueueItem, index: int, settings: JobSettings, started: datetime,
              pages: int, reserved: set[str], ctx: _Ctx) -> list[str]:
    """Passi che producono più file per ingresso (dividi, esporta immagini)."""
    p = step.merged_params()
    if step.action == "split":
        parts = _split_parts(data, p)
    elif step.action == "form_export":
        parts = _form_export_parts(data, p)
    else:
        parts = _image_parts(data, p)
    pattern = settings.pattern
    if len(parts) > 1 and "{pagina" not in pattern and "{parte" not in pattern:
        pattern += "_{parte:3}"
    dest = _target_dir(item, settings)
    outputs: list[str] = []
    try:
        for k, (blob, ext) in enumerate(parts):
            ctx.check()
            name = render_name(pattern, item.path, index, settings, started, pages,
                               {"parte": k + 1, "pagina": k + 1}, ext)
            target = _resolve_conflict(os.path.join(dest, name), settings, reserved)
            if target is None:
                raise BatchError("EXIST-01", _("Saltato: esiste già."), status="skipped")
            reserved.add(target)
            _write_output(target, blob)
            outputs.append(target)
        ctx.notes.append(_("{n} file prodotti").format(n=len(outputs)))
        return outputs
    except BaseException:
        for o in outputs:
            try:
                os.unlink(o)
            except OSError:
                pass
        raise


# =============================================================================
# Sequenze salvate (BAT-20)
# =============================================================================
def validate_sequence(steps: list[Step]) -> list[str]:
    errors = []
    for i, s in enumerate(steps):
        spec = ACTIONS.get(s.action)
        if spec is None:
            errors.append(_("Operazione sconosciuta: {a}").format(a=s.action))
        elif spec.kind in ("convert", "print", "multi") and i != len(steps) - 1:
            errors.append(_("«{a}» deve essere l'ultimo passo della sequenza.").format(a=_(spec.label)))
    return errors


def _sequence_filename(name: str) -> str:
    return (_sanitize(name) or "sequenza") + ".json"


SEQUENCE_VERSION = 1


OUTPUT_KEYS = ("destination_mode", "destination", "subfolder", "keep_structure", "pattern", "counter_start",
               "conflict", "error_policy")


def output_of(settings: JobSettings) -> dict:
    """Uscita del lavoro da salvare nella sequenza (senza segreti)."""
    return {k: getattr(settings, k) for k in OUTPUT_KEYS}


def sequence_data(name: str, steps: list[Step], description: str = "", output: dict | None = None) -> dict:
    data = {"format": "foliant-sequence", "version": SEQUENCE_VERSION, "name": name, "description": description,
            "created": datetime.now().isoformat(timespec="seconds"), "steps": [s.to_dict() for s in steps]}
    if output:
        data["output"] = {k: v for k, v in output.items() if k in OUTPUT_KEYS}
    return data


def save_sequence(directory: str, name: str, steps: list[Step], description: str = "",
                  output: dict | None = None) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, _sequence_filename(name))
    atomic_write(path, json.dumps(sequence_data(name, steps, description, output), ensure_ascii=False,
                                  indent=2).encode("utf-8"))
    return path


def export_sequence(path: str, name: str, steps: list[Step], description: str = "",
                    output: dict | None = None) -> None:
    atomic_write(path, json.dumps(sequence_data(name, steps, description, output), ensure_ascii=False,
                                  indent=2).encode("utf-8"))


def sequence_output(path: str) -> dict | None:
    """Uscita salvata nella sequenza, se presente."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    out = data.get("output") if isinstance(data, dict) else None
    return {k: v for k, v in out.items() if k in OUTPUT_KEYS} if isinstance(out, dict) else None


def import_sequence(path: str, directory: str) -> tuple[str, list[str]]:
    """Importa una sequenza da file: (percorso salvato, avvisi). Rifiuta schemi più
    recenti e operazioni sconosciute; ignora eventuali password presenti."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise DocumentError(_("Sequenza non leggibile: {f}").format(f=os.path.basename(path))) from e
    if not isinstance(data, dict) or data.get("format", "foliant-sequence") != "foliant-sequence":
        raise DocumentError(_("Il file non contiene una sequenza di Foliant."))
    if int(data.get("version", 1) or 1) > SEQUENCE_VERSION:
        raise DocumentError(_("La sequenza usa uno schema più recente ({v}): aggiorna Foliant.").format(
            v=data.get("version")))
    name, steps = load_sequence(path)
    unknown = sorted({s.action for s in steps if s.action not in ACTIONS})
    if unknown:
        raise DocumentError(_("Operazioni sconosciute: {o}").format(o=", ".join(unknown)))
    warnings = []
    for s in steps:
        for k in ACTIONS[s.action].secret_keys():
            if s.params.get(k):
                s.params[k] = ""
                if _("Le password contenute nel file sono state ignorate.") not in warnings:
                    warnings.append(_("Le password contenute nel file sono state ignorate."))
    return save_sequence(directory, name, steps, str(data.get("description", "")),
                         data.get("output") if isinstance(data.get("output"), dict) else None), warnings


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
