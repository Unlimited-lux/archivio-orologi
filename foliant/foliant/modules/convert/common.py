"""Funzioni condivise dal modulo Converti (senza interfaccia).

- `parse_page_range`: intervalli di pagine scritti dall'utente («1-3, 7, 10-fine», «pari»);
- `unique_path` / `resolve_target`: nomi di uscita e politica se il file esiste;
- `OutputFiles`: scrittura su file temporanei rinominati solo a fine lavoro,
  così un annullamento o un errore non lascia file parziali;
- `open_source`: apertura del PDF sorgente (percorso o byte) con password e
  controllo del permesso di estrazione.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Callable

import pymupdf

from foliant.core import platform
from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, PasswordRequired, WrongPassword, _


class ConversionError(FoliantError):
    """Errore di conversione con messaggio per l'utente."""


class RangeError(ConversionError):
    """Intervallo di pagine non valido."""


# ------------------------------------------------------------------ intervalli
_END_WORDS = {"fine", "end", "ultima", "last", "n"}


def parse_page_range(expr: str | None, page_count: int, current: int = 0,
                     selected: list[int] | None = None) -> list[int]:
    """Converte un'espressione in indici di pagina (base 0), nell'ordine scritto.

    Accetta: vuoto o «tutte» (tutte le pagine), «corrente», «selezionate»,
    «pari», «dispari», numeri e intervalli separati da virgole o punto e virgola
    (`1-3, 7, 10-fine`). Le pagine ripetute sono tenute una sola volta.
    Solleva `RangeError` se l'espressione non è valida o esce dal documento.
    """
    if page_count <= 0:
        raise RangeError(_("Il documento non contiene pagine."))
    text = (expr or "").strip().lower()
    if text in ("", "tutte", "all", "*"):
        return list(range(page_count))
    if text in ("corrente", "current", "pagina corrente"):
        return [max(0, min(current, page_count - 1))]
    if text in ("selezionate", "selected"):
        if not selected:
            raise RangeError(_("Nessuna pagina selezionata."))
        return sorted({p for p in selected if 0 <= p < page_count})
    if text in ("pari", "even"):
        return [i for i in range(page_count) if (i + 1) % 2 == 0]
    if text in ("dispari", "odd"):
        return [i for i in range(page_count) if (i + 1) % 2 == 1]

    def number(tok: str) -> int:
        tok = tok.strip()
        if tok in _END_WORDS:
            return page_count
        if not tok.isdigit():
            raise RangeError(_("«{t}» non è un numero di pagina valido.").format(t=tok))
        return int(tok)

    result: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[,;]", text):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _sep, b = part.partition("-")
            start = number(a) if a.strip() else 1
            end = number(b) if b.strip() else page_count
        else:
            start = end = number(part)
        lo, hi = min(start, end), max(start, end)
        if lo < 1 or hi > page_count:
            raise RangeError(_("Le pagine {a}-{b} non esistono: il documento ha {n} pagine.").format(
                a=lo, b=hi, n=page_count))
        seq = range(start, end + 1) if start <= end else range(start, end - 1, -1)
        for p in seq:
            if p - 1 not in seen:
                seen.add(p - 1)
                result.append(p - 1)
    if not result:
        raise RangeError(_("Intervallo di pagine vuoto."))
    return result


# ------------------------------------------------------------------- nomi file
def unique_path(path: str | os.PathLike) -> str:
    """Restituisce `path` se libero, altrimenti «nome (1).ext», «nome (2).ext»…"""
    p = Path(path)
    if not p.exists():
        return str(p)
    n = 1
    while True:
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not cand.exists():
            return str(cand)
        n += 1


def resolve_target(path: str | os.PathLike, policy: str = "rename") -> str:
    """Applica la politica per i file esistenti: "rename" (suffisso) o "overwrite"."""
    return str(path) if policy == "overwrite" else unique_path(path)


def expand_name_template(template: str, *, name: str, page: int | None = None, fmt: str = "",
                         date: str | None = None) -> str:
    """Espande `{nome}`, `{pagina}` (anche `{pagina:03}`), `{data}`, `{formato}`."""
    import datetime as _dt
    values = {"nome": name, "name": name, "formato": fmt, "format": fmt,
              "data": date or _dt.date.today().isoformat(), "date": date or _dt.date.today().isoformat()}
    def repl(m: re.Match) -> str:
        key, spec = m.group(1), m.group(2) or ""
        if key in ("pagina", "page"):
            return format(page if page is not None else 1, spec.lstrip(":") or "")
        return str(values.get(key, m.group(0)))
    return re.sub(r"\{(\w+)(:[^}]*)?\}", repl, template)


def _current_umask() -> int:
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


_UMASK = _current_umask()


class OutputFiles:
    """Raccoglie i file prodotti come temporanei e li rende definitivi solo con `commit()`.

    Uso::

        with OutputFiles(policy) as out:
            tmp = out.new("/dest/nome.docx")   # percorso temporaneo su cui scrivere
            ...
            paths = out.commit()                # rinomina i temporanei nei nomi finali
    Se il blocco solleva un'eccezione (anche annullamento), i temporanei vengono rimossi.
    """

    def __init__(self, policy: str = "rename"):
        self.policy = policy
        self._items: list[tuple[str, str]] = []   # (temporaneo, finale desiderato)
        self._done: list[str] = []
        self._made_dirs: list[Path] = []           # cartelle create da noi (rimosse se restano vuote)

    def new(self, final_path: str | os.PathLike) -> str:
        final = Path(final_path)
        directory = final.parent if str(final.parent) else Path(".")
        try:
            missing = []
            d = directory
            while not d.exists() and d != d.parent:
                missing.append(d)
                d = d.parent
            directory.mkdir(parents=True, exist_ok=True)
            self._made_dirs.extend(missing)
            fd, tmp = tempfile.mkstemp(prefix=f".{final.stem[:32]}.", suffix=f".part{final.suffix[:16]}",
                                       dir=directory)
        except OSError as e:
            raise ConversionError(_("Impossibile scrivere nella cartella «{d}»: {e}").format(
                d=directory, e=e.strerror or e)) from e
        os.close(fd)
        self._items.append((tmp, str(final)))
        return tmp

    def commit(self) -> list[str]:
        taken: set[str] = set()
        for tmp, final in self._items:
            target = resolve_target(final, self.policy)
            # evita che due uscite dello stesso lotto si sovrascrivano tra loro
            while target in taken:
                target = unique_path(Path(target).with_name(Path(target).stem + " (1)" + Path(target).suffix))
            taken.add(target)
            try:
                mode = os.stat(target).st_mode & 0o7777 if os.path.exists(target) else 0o666 & ~_UMASK
                os.chmod(tmp, mode)
            except OSError:
                pass
            platform.replace_file(tmp, target)
            self._done.append(target)
        self._items.clear()
        return list(self._done)

    def discard(self) -> None:
        for tmp, _final in self._items:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        self._items.clear()
        for d in self._made_dirs:          # dalla più interna alla più esterna
            try:
                d.rmdir()
            except OSError:
                pass
        self._made_dirs.clear()

    def __enter__(self) -> "OutputFiles":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None or self._items:
            self.discard()


# -------------------------------------------------------------------- sorgente
Source = "str | os.PathLike | bytes | pymupdf.Document"


def open_source(source, password: str | None = None, *, need_extract: bool = True) -> pymupdf.Document:
    """Apre il PDF da convertire (percorso o byte) come documento indipendente.

    Chiamare tenendo ENGINE_LOCK. Solleva `PasswordRequired`/`WrongPassword`
    o `ConversionError` se il permesso di estrazione manca.
    """
    try:
        if isinstance(source, (bytes, bytearray)):
            pdf = pymupdf.open(stream=bytes(source), filetype="pdf")
        else:
            path = Path(source)
            if not path.exists():
                raise ConversionError(_("Il file «{n}» non esiste.").format(n=path.name))
            pdf = pymupdf.open(str(path), filetype="pdf")
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(_("Il file non è un PDF valido oppure è danneggiato.")) from e
    if pdf.needs_pass:
        if not password:
            pdf.close()
            raise PasswordRequired()
        code = pdf.authenticate(password)
        if not code:
            pdf.close()
            raise WrongPassword()
        owner = bool(code & 4)
    else:
        owner = False
        if password:
            try:
                owner = bool(pdf.authenticate(password) & 4)
            except Exception:
                owner = False
    if need_extract and not owner and (pdf.metadata or {}).get("encryption"):
        perms = pdf.permissions
        if not owner and not (perms & pymupdf.PDF_PERM_COPY):
            pdf.close()
            raise ConversionError(_("Il documento non consente l'estrazione del contenuto (permesso «copia del "
                                    "contenuto»). Serve la password del proprietario."))
    if pdf.page_count == 0:
        pdf.close()
        raise ConversionError(_("Il documento non contiene pagine."))
    return pdf


def check(progress, fraction: float | None = None, text: str | None = None) -> None:
    """Aggiorna l'avanzamento se presente (solleva OperationCancelled se annullato)."""
    if progress is not None:
        progress.update(fraction, text)


ProgressFn = Callable[[float, str], None]

__all__ = ["ConversionError", "RangeError", "parse_page_range", "unique_path", "resolve_target",
           "expand_name_template", "OutputFiles", "open_source", "check", "OperationCancelled",
           "ENGINE_LOCK"]


def context_of(widget):
    """Contesto del nucleo del widget (o di un suo genitore), oppure None.

    Riservatezza: i dialoghi del modulo scelgono file e cartelle solo con i metodi del
    contesto (ask_open_paths, ask_save_path, ask_directory), che non lasciano cronologie
    dei file nel profilo; mai con QFileDialog direttamente."""
    w = widget
    while w is not None:
        ctx = getattr(w, "ctx", None)
        if ctx is not None and hasattr(ctx, "ask_open_paths"):
            return ctx
        w = w.parentWidget() if hasattr(w, "parentWidget") else None
    return None
