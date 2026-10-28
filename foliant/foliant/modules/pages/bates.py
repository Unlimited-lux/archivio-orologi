"""Numerazione Bates su più file (PAG-35), senza interfaccia.

La timbratura di un singolo documento è `stamp.add_bates`; qui si pianifica e
si esegue la numerazione su un elenco ordinato di file:

* `plan_bates(entries, …)` apre ogni file, conta le pagine, salta i file
  cifrati senza password valida, rileva un Bates di Foliant già presente e
  assegna gli intervalli di numeri (continui o ripartiti per documento);
* `check_digits(plan, digits)` verifica PRIMA di iniziare che le cifre bastino
  (`BatesDigitsError.required_digits` è la proposta);
* `bates_files(entries, …)` esegue: salva copie in `out_folder` oppure
  sovrascrive i file sul posto (scrittura atomica; la conferma è del chiamante),
  scrive il registro CSV facoltativo e restituisce un `BatesReport` (anche se
  l'utente annulla: file completati e prossimo numero da usare per riprendere).

Una voce può essere «esterna» (`BatesEntry(external=True, page_count=n)`): riceve
numeri e righe di registro ma il file non viene toccato; serve al dialogo per il
documento aperto, a cui la numerazione è applicata con `document.change()`.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, OperationCancelled, _, atomic_write

from . import stamp
from .engine import PageOpError, render_name, unique_path

TRANSLATIONS = {
    "Aggiungi almeno un file da numerare.": "Add at least one file to number.",
    "Le cifre devono essere tra {a} e {b}.": "Digits must be between {a} and {b}.",
    "Il numero iniziale non è valido.": "The start number is not valid.",
    "Il numero iniziale non può essere negativo.": "The start number cannot be negative.",
    "Con {d} cifre si arriva al massimo a {max}, ma l'ultimo numero sarebbe {last}. Usa almeno {req} cifre.":
        "With {d} digits the maximum is {max}, but the last number would be {last}. Use at least {req} digits.",
    "«{n}»: file non trovato o illeggibile.": "“{n}”: file not found or unreadable.",
    "«{n}»: non è un PDF valido.": "“{n}”: not a valid PDF.",
    "«{n}»: protetto da password, saltato.": "“{n}”: password protected, skipped.",
    "«{n}»: password errata, saltato.": "“{n}”: wrong password, skipped.",
    "«{n}»: non contiene pagine, saltato.": "“{n}”: contains no pages, skipped.",
    "«{n}»: impossibile scrivere il file ({e}).": "“{n}”: cannot write the file ({e}).",
    "«{n}»: errore durante la numerazione ({e}).": "“{n}”: error while numbering ({e}).",
    "Questi file hanno già una numerazione Bates di Foliant:\n{f}": "These files already have Foliant Bates numbering:\n{f}",
    "Nessun file può essere numerato.": "No file can be numbered.",
    "Impossibile scrivere il registro «{n}»: {e}.": "Cannot write the log “{n}”: {e}.",
    "Numerazione di «{n}» ({i} di {t})": "Numbering “{n}” ({i} of {t})",
}

DIGITS_MIN = 3
DIGITS_MAX = 15
# Sei aree della pagina (PAG-35).
POSITIONS = ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right")
DEFAULT_TEMPLATE = "{nome}_{bates_inizio}-{bates_fine}"


class BatesDigitsError(PageOpError):
    """Le cifre non bastano: `required_digits` è il minimo necessario."""

    def __init__(self, message: str, required_digits: int):
        super().__init__(message)
        self.required_digits = required_digits


class BatesExistsError(PageOpError):
    """Alcuni file hanno già un Bates di Foliant e `replace` è falso."""

    def __init__(self, message: str, paths: list[str]):
        super().__init__(message)
        self.paths = paths


@dataclass
class BatesEntry:
    path: str
    password: str | None = None
    external: bool = False          # numeri e registro sì, file non toccato
    page_count: int | None = None   # obbligatorio per le voci esterne
    has_bates: bool = False         # per le voci esterne: Bates di Foliant già presente


@dataclass
class PlannedFile:
    entry: BatesEntry
    page_count: int
    first: int
    last: int
    has_bates: bool = False

    @property
    def path(self) -> str:
        return self.entry.path


@dataclass
class BatesPlan:
    files: list[PlannedFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (percorso, motivo)

    @property
    def last_number(self) -> int | None:
        return max((f.last for f in self.files), default=None)


@dataclass
class BatesFileResult:
    path: str
    first: int
    last: int
    first_code: str
    last_code: str
    output: str | None              # file scritto (None per le voci esterne)
    external: bool = False


@dataclass
class BatesReport:
    done: list[BatesFileResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_number: int = 1            # da usare per riprendere (numerazione continua)
    cancelled: bool = False
    log_path: str | None = None
    remaining: list[str] = field(default_factory=list)   # file non completati (annullamento/errore)


def _as_entry(e) -> BatesEntry:
    if isinstance(e, BatesEntry):
        return e
    if isinstance(e, (str, os.PathLike)):
        return BatesEntry(str(e))
    path, password = (list(e) + [None])[:2]
    return BatesEntry(str(path), password)


def _check_params(start, digits) -> tuple[int, int]:
    try:
        start = int(start)
    except (TypeError, ValueError):
        raise PageOpError(_("Il numero iniziale non è valido.")) from None
    if start < 0:
        raise PageOpError(_("Il numero iniziale non può essere negativo."))
    try:
        digits = int(digits)
    except (TypeError, ValueError):
        digits = 0
    if not DIGITS_MIN <= digits <= DIGITS_MAX:
        raise PageOpError(_("Le cifre devono essere tra {a} e {b}.").format(a=DIGITS_MIN, b=DIGITS_MAX))
    return start, digits


def _open(entry: BatesEntry) -> tuple[pymupdf.Document | None, str | None]:
    """Apre il file della voce; (documento, None) oppure (None, motivo per saltarlo)."""
    name = Path(entry.path).name
    try:
        data = Path(entry.path).read_bytes()
    except OSError:
        return None, _("«{n}»: file non trovato o illeggibile.").format(n=name)
    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return None, _("«{n}»: non è un PDF valido.").format(n=name)
        if doc.needs_pass:
            if not entry.password:
                doc.close()
                return None, _("«{n}»: protetto da password, saltato.").format(n=name)
            if not doc.authenticate(entry.password):
                doc.close()
                return None, _("«{n}»: password errata, saltato.").format(n=name)
        if doc.page_count == 0:
            doc.close()
            return None, _("«{n}»: non contiene pagine, saltato.").format(n=name)
    return doc, None


def has_foliant_bates(pdf: pymupdf.Document) -> bool:
    try:
        return any(stamp.BATES in kinds for kinds in stamp.list_watermarks(pdf).values())
    except Exception:
        return False


def plan_bates(entries: Sequence, *, start: int = 1, continuous: bool = True) -> BatesPlan:
    """Conta le pagine di ogni file e assegna gli intervalli di numeri (vedi modulo)."""
    start = int(start)
    plan = BatesPlan()
    number = start
    for raw in entries:
        entry = _as_entry(raw)
        if entry.external:
            count = int(entry.page_count or 0)
            has = entry.has_bates
            if count <= 0:
                plan.skipped.append((entry.path, _("«{n}»: non contiene pagine, saltato.").format(
                    n=Path(entry.path).name)))
                continue
        else:
            doc, reason = _open(entry)
            if doc is None:
                plan.skipped.append((entry.path, reason))
                continue
            with ENGINE_LOCK:
                try:
                    count = doc.page_count
                    has = has_foliant_bates(doc)
                finally:
                    doc.close()
        first = number if continuous else start
        last = first + count - 1
        plan.files.append(PlannedFile(entry, count, first, last, has))
        if continuous:
            number = last + 1
    return plan


def check_digits(plan: BatesPlan, digits: int) -> None:
    """Solleva `BatesDigitsError` se l'ultimo numero non sta nelle cifre indicate."""
    last = plan.last_number
    if last is None:
        return
    need = len(str(last))
    if need > digits:
        raise BatesDigitsError(
            _("Con {d} cifre si arriva al massimo a {max}, ma l'ultimo numero sarebbe {last}. "
              "Usa almeno {req} cifre.").format(d=digits, max="9" * digits, last=last, req=need), need)


def apply_bates(pdf: pymupdf.Document, first: int, *, prefix: str = "", suffix: str = "", digits: int = 6,
                replace: bool = True, **style) -> tuple[str, str]:
    """Numera tutte le pagine di `pdf` da `first` (togliendo prima un Bates di
    Foliant se `replace`). Restituisce (primo codice, ultimo codice)."""
    if replace:
        stamp.remove_watermarks(pdf, kind=stamp.BATES)
    last_code = stamp.add_bates(pdf, None, prefix=prefix, suffix=suffix, start=first, digits=digits, **style)
    return stamp.format_bates(first, prefix=prefix, suffix=suffix, digits=digits), last_code


def _style(position, margin, font_size, color, font) -> dict:
    return {"position": position, "margin": margin, "font_size": font_size, "color": color, "font": font}


def _save_bytes(doc: pymupdf.Document, encrypted: bool) -> bytes:
    if encrypted:
        try:
            return doc.tobytes(garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        except Exception:
            pass
    return doc.tobytes(garbage=1, deflate=True)


def write_log(path: str | os.PathLike, rows: Sequence[tuple[str, int, str]]) -> None:
    """Registro CSV: intestazione «file,pagina,numero» e una riga per pagina."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "pagina", "numero"])
            w.writerows(rows)
    except OSError as e:
        raise PageOpError(_("Impossibile scrivere il registro «{n}»: {e}.").format(
            n=Path(path).name, e=e.strerror or e)) from e


def bates_files(entries: Sequence, *, prefix: str = "", suffix: str = "", start: int = 1, digits: int = 6,
                continuous: bool = True, position: str = "bottom-right", margin: float = 36,
                font_size: float = 10, color=(0, 0, 0), font: str = "cour", replace: bool = False,
                out_folder: str | os.PathLike | None = None, name_template: str | None = None,
                log_csv: str | os.PathLike | None = None,
                progress=None, plan: BatesPlan | None = None) -> BatesReport:
    """Applica la numerazione Bates a più file nell'ordine dato.

    `entries`: percorsi, coppie (percorso, password) o `BatesEntry`.
    `out_folder=None` sovrascrive i file sul posto (scrittura atomica), altrimenti
    salva copie nella cartella con nome da `name_template` (segnaposto {nome},
    {bates_inizio}, {bates_fine}; predefinito: nome originale).
    Prima di iniziare verifica le cifre (`BatesDigitsError`) e la presenza di un
    Bates di Foliant (`BatesExistsError` se `replace` è falso).
    `progress`: oggetto con `update(frazione, testo)` o funzione `(frazione, testo)`;
    l'annullamento (OperationCancelled) non solleva: il rapporto ha `cancelled=True`.
    """
    start, digits = _check_params(start, digits)
    if not entries:
        raise PageOpError(_("Aggiungi almeno un file da numerare."))
    if position not in POSITIONS and position not in stamp.POSITIONS:
        position = "bottom-right"
    style = _style(position, margin, font_size, color, font)
    if plan is None:
        plan = plan_bates(entries, start=start, continuous=continuous)
    report = BatesReport(skipped=list(plan.skipped), next_number=start)
    if not plan.files:
        raise PageOpError(_("Nessun file può essere numerato."))
    check_digits(plan, digits)
    existing = [f.path for f in plan.files if f.has_bates]
    if existing and not replace:
        raise BatesExistsError(_("Questi file hanno già una numerazione Bates di Foliant:\n{f}").format(
            f="\n".join(Path(p).name for p in existing)), existing)
    if out_folder is not None:
        Path(out_folder).mkdir(parents=True, exist_ok=True)

    def report_progress(fraction: float, text: str) -> None:
        if progress is None:
            return
        if hasattr(progress, "update"):
            progress.update(fraction, text)
        else:
            progress(fraction, text)

    fmt = lambda n: stamp.format_bates(n, prefix=prefix, suffix=suffix, digits=digits)  # noqa: E731
    rows: list[tuple[str, int, str]] = []
    taken: set[str] = set()
    total = len(plan.files)
    k = 0
    try:
        for k, pf in enumerate(plan.files):
            name = Path(pf.path).name
            report_progress(k / total, _("Numerazione di «{n}» ({i} di {t})").format(n=name, i=k + 1, t=total))
            output = None
            if not pf.entry.external:
                doc, reason = _open(pf.entry)
                if doc is None:          # cambiato dopo la pianificazione
                    report.skipped.append((pf.path, reason))
                    report.remaining.append(pf.path)
                    continue
                try:
                    with ENGINE_LOCK:
                        encrypted = bool(doc.is_encrypted or doc.metadata.get("encryption"))
                        apply_bates(doc, pf.first, prefix=prefix, suffix=suffix, digits=digits, replace=replace,
                                    **style)
                        data = _save_bytes(doc, encrypted)
                except PageOpError:
                    raise
                except Exception as e:
                    report.errors.append(_("«{n}»: errore durante la numerazione ({e}).").format(n=name, e=e))
                    report.remaining.append(pf.path)
                    continue
                finally:
                    with ENGINE_LOCK:
                        doc.close()
                if out_folder is None:
                    target = Path(pf.path)
                else:
                    stem = render_name(name_template or "{nome}", nome=Path(pf.path).stem,
                                       bates_inizio=fmt(pf.first), bates_fine=fmt(pf.last))
                    target = unique_path(out_folder, stem + ".pdf", taken=taken)
                try:
                    atomic_write(target, data)
                except OSError as e:
                    report.errors.append(_("«{n}»: impossibile scrivere il file ({e}).").format(
                        n=name, e=e.strerror or e))
                    report.remaining.append(pf.path)
                    continue
                output = str(target)
            rows.extend((name, i + 1, fmt(pf.first + i)) for i in range(pf.page_count))
            report.done.append(BatesFileResult(pf.path, pf.first, pf.last, fmt(pf.first), fmt(pf.last), output,
                                               pf.entry.external))
            report.next_number = pf.last + 1 if continuous else start
        report_progress(1.0, "")
    except OperationCancelled:
        report.cancelled = True
        done = {r.path for r in report.done}
        report.remaining.extend(f.path for f in plan.files[k:] if f.path not in done
                                and f.path not in report.remaining)
    if log_csv:
        write_log(log_csv, rows)
        report.log_path = str(log_csv)
    return report


def preview_codes(plan: BatesPlan, *, prefix: str = "", suffix: str = "", digits: int = 6) -> list[tuple[str, str, str]]:
    """(nome file, primo codice, ultimo codice) per l'anteprima del dialogo."""
    fmt = lambda n: stamp.format_bates(n, prefix=prefix, suffix=suffix, digits=digits)  # noqa: E731
    return [(Path(f.path).name, fmt(f.first), fmt(f.last)) for f in plan.files]


def remove_bates_files(paths: Sequence[str]) -> list[str]:
    """Rimuove la numerazione Bates di Foliant dai file indicati (sul posto). Restituisce i file modificati."""
    changed: list[str] = []
    for p in paths:
        doc, _reason = _open(BatesEntry(str(p)))
        if doc is None:
            continue
        try:
            with ENGINE_LOCK:
                if stamp.remove_watermarks(doc, kind=stamp.BATES):
                    atomic_write(p, doc.tobytes(garbage=1, deflate=True))
                    changed.append(str(p))
        finally:
            with ENGINE_LOCK:
                doc.close()
    return changed
