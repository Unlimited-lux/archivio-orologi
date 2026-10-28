"""Elaborazione multipla dei dati dei moduli (FRM-30), senza interfaccia.

- Raccolta: valori di più PDF compilati in una tabella, una riga per file
  (prima colonna «File»).
- Compilazione in serie: un PDF compilato per ogni record, con nome costruito
  da un modello («{Cognome}_{Nome}.pdf», «{n}» = numero di riga da 1).

Ogni funzione accetta `progress(frazione, testo)` (può sollevare un'eccezione
di annullamento, che interrompe l'elaborazione); ogni accesso al motore PDF
avviene tenendo ENGINE_LOCK, un documento alla volta.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

import pymupdf

from foliant.core.engine import ENGINE_LOCK
from foliant.i18n import _

from . import engine as E
from . import interchange as X

Progress = Callable[[float, str], None] | None

FILE_COLUMN = "File"

TRANSLATIONS: dict[str, str] = {
    "Il file non è un PDF leggibile: {e}": "The file is not a readable PDF: {e}",
    "Il file non è un PDF.": "The file is not a PDF.",
    "Il PDF è protetto da password.": "The PDF is password protected.",
    "Il PDF non contiene campi modulo.": "The PDF contains no form fields.",
    "Errore durante la lettura: {e}": "Error while reading: {e}",
    "Lettura di {f}": "Reading {f}",
    "Riga {n} di {t}": "Row {n} of {t}",
    "Impossibile aprire il modulo: {e}": "Cannot open the form: {e}",
    "Impossibile salvare «{f}»: {e}": "Cannot save “{f}”: {e}",
    "Ricalcolo non riuscito: {e}": "Recalculation failed: {e}",
    "Completato": "Done",
}

_BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _report(progress: Progress, fraction: float, text: str) -> None:
    if progress is not None:
        progress(fraction, text)


def _cell(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    return "" if value is None else value


# ====================================================================== raccolta
def _read_values(path: str) -> dict[str, Any]:
    """Valori del modulo in `path`; solleva FormError con un messaggio per l'utente."""
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(path)
        except Exception as e:
            raise E.FormError(_("Il file non è un PDF leggibile: {e}").format(e=e)) from e
        try:
            if not pdf.is_pdf:
                raise E.FormError(_("Il file non è un PDF."))
            if pdf.needs_pass:
                raise E.FormError(_("Il PDF è protetto da password."))
            values = E.form_values(pdf)
            if not values:
                raise E.FormError(_("Il PDF non contiene campi modulo."))
            return values
        except E.FormError:
            raise
        except Exception as e:
            raise E.FormError(_("Errore durante la lettura: {e}").format(e=e)) from e
        finally:
            pdf.close()


def collect(paths: list[str], progress: Progress = None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Righe {«File»: nome del file, campo: valore…} e errori (percorso, messaggio).
    I file illeggibili, protetti o senza modulo sono saltati e segnalati."""
    rows: list[dict] = []
    errors: list[tuple[str, str]] = []
    total = len(paths) or 1
    for i, path in enumerate(paths):
        name = os.path.basename(path)
        _report(progress, i / total, _("Lettura di {f}").format(f=name))
        try:
            values = _read_values(path)
        except E.FormError as e:
            errors.append((path, str(e)))
            continue
        row: dict[str, Any] = {FILE_COLUMN: name}
        for k, v in values.items():
            if k != FILE_COLUMN:
                row[k] = _cell(v)
        rows.append(row)
    _report(progress, 1.0, _("Completato"))
    return rows, errors


def collect_csv(paths: list[str], out_path: str, progress: Progress = None) -> dict:
    """Raccolta in un file CSV (intestazione + una riga per file, prima colonna «File»).
    Restituisce {"path", "rows", "errors"}."""
    rows, errors = collect(paths, progress)
    data = X.export_csv(rows) if rows else (FILE_COLUMN + "\n").encode("utf-8")
    with open(out_path, "wb") as fh:
        fh.write(data)
    return {"path": out_path, "rows": len(rows), "errors": errors}


# ====================================================================== compilazione in serie
def sanitize(text: Any) -> str:
    """Testo utilizzabile in un nome di file: caratteri non ammessi sostituiti da «_»."""
    return _BAD_CHARS.sub("_", "" if text is None else str(_cell(text))).strip()


def file_name(pattern: str, record: dict[str, Any], n: int) -> str:
    """Nome di file dal modello: {Campo} = valore del record, {n} = numero di riga (da 1)."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key == "n":
            return str(n)
        return sanitize(record.get(key, record.get(key.strip(), "")))

    name = re.sub(r"\{([^{}]+)\}", repl, pattern or "{n}.pdf")
    name = _BAD_CHARS.sub("_", name).strip()
    ext = ".pdf"
    stem = name[:-4] if name.lower().endswith(ext) else name
    stem = stem.strip().rstrip(".").strip()
    return (stem or str(n)) + ext


def _free_path(out_dir: str, name: str, used: set[str]) -> str:
    stem, ext = os.path.splitext(name)
    candidate, k = name, 2
    while candidate in used or os.path.exists(os.path.join(out_dir, candidate)):
        candidate = f"{stem} ({k}){ext}"
        k += 1
    used.add(candidate)
    return os.path.join(out_dir, candidate)


def fill_series(template_path: str, records: list[dict], out_dir: str, name_pattern: str = "{n}.pdf",
                progress: Progress = None, *, flatten: bool = False) -> dict:
    """Un PDF compilato per ogni record. Un valore rifiutato è segnalato (riga da 1, campo,
    messaggio) senza fermare gli altri campi né le altre righe. I file esistenti non
    vengono mai sovrascritti. Restituisce {"created", "errors", "unknown"}."""
    try:
        with open(template_path, "rb") as fh:
            template = fh.read()
        with ENGINE_LOCK:
            probe = pymupdf.open("pdf", template)
            try:
                if probe.needs_pass:
                    raise E.FormError(_("Il PDF è protetto da password."))
                names = {f.name for f in E.list_fields(probe) if f.kind in E.FILLABLE}
            finally:
                probe.close()
    except Exception as e:
        raise E.FormError(_("Impossibile aprire il modulo: {e}").format(e=e)) from e
    os.makedirs(out_dir, exist_ok=True)
    created: list[str] = []
    errors: list[tuple[int, str, str]] = []
    unknown: set[str] = set()
    used: set[str] = set()
    total = len(records) or 1
    for i, record in enumerate(records):
        row = i + 1
        _report(progress, i / total, _("Riga {n} di {t}").format(n=row, t=len(records)))
        with ENGINE_LOCK:
            pdf = pymupdf.open("pdf", template)
            try:
                for name, value in record.items():
                    if name not in names:
                        if name != FILE_COLUMN:
                            unknown.add(name)
                        continue
                    try:
                        E.apply_values(pdf, {name: value})
                    except E.FormError as e:
                        errors.append((row, name, str(e)))
                    except Exception as e:  # un campo difettoso non ferma gli altri
                        errors.append((row, name, str(e) or e.__class__.__name__))
                try:
                    E.recalculate(pdf)
                except Exception as e:
                    errors.append((row, "", _("Ricalcolo non riuscito: {e}").format(e=e)))
                if flatten:
                    E.flatten(pdf)
                path = _free_path(out_dir, file_name(name_pattern, record, row), used)
                try:
                    pdf.save(path, garbage=3, deflate=True)
                except Exception as e:
                    errors.append((row, "", _("Impossibile salvare «{f}»: {e}").format(f=os.path.basename(path), e=e)))
                    continue
                created.append(path)
            finally:
                pdf.close()
    _report(progress, 1.0, _("Completato"))
    return {"created": created, "errors": errors, "unknown": sorted(unknown)}
