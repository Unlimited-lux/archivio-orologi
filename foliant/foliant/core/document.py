"""Documento aperto: caricamento, modifiche con annulla/ripeti, salvataggio atomico.

Regole per chi modifica un documento:
- ogni modifica passa da `Document.change(etichetta)`, che registra lo stato
  precedente per l'annullamento e, se il blocco solleva un'eccezione,
  ripristina il documento com'era (nessuna modifica parziale);
- il file su disco non viene mai toccato finché l'utente non salva, e il
  salvataggio scrive prima un file temporaneo e poi lo sostituisce;
- non conservare oggetti pagina (`pdf[i]`) oltre la singola operazione:
  dopo un annulla/ripeti il documento interno viene sostituito.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import pymupdf

from .engine import ENGINE_LOCK
from .errors import DocumentError, PasswordRequired, WrongPassword

MAX_UNDO_STEPS = 100
MAX_UNDO_BYTES = 512 * 1024 * 1024

# Tipi di notifica inviati agli osservatori.
CONTENT = "content"   # il contenuto è cambiato (modifica, annulla, ripeti)
SAVED = "saved"       # salvato: cambia titolo/stato, non il contenuto
CLOSED = "closed"

Listener = Callable[["Document", str], None]


@dataclass
class _Snapshot:
    label: str
    data: bytes
    state: int
    save_options: dict = field(default_factory=dict)


def atomic_write(target: str | os.PathLike, data: bytes) -> None:
    """Scrive `data` in `target` senza mai lasciare un file a metà."""
    target = Path(os.path.realpath(target))          # salva nel file vero, non sul collegamento
    directory = target.parent
    if target.exists() and (not os.access(target, os.W_OK) or not (target.stat().st_mode & 0o222)):
        raise DocumentError(f"«{target.name}» è in sola lettura: salvalo con un altro nome.")
    try:
        fd, tmp = tempfile.mkstemp(prefix=".foliant-", suffix=".tmp", dir=directory)
    except OSError as e:
        raise DocumentError(f"Impossibile scrivere nella cartella «{directory}»: {e.strerror}.") from e
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            if target.exists():
                os.chmod(tmp, target.stat().st_mode & 0o7777)
            else:
                umask = os.umask(0); os.umask(umask)
                os.chmod(tmp, 0o666 & ~umask)
        except OSError:
            pass
        os.replace(tmp, target)
    except BaseException as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(e, OSError):
            raise DocumentError(f"Salvataggio non riuscito: {e.strerror}.") from e
        raise


def _encryption_method(description: str | None) -> int:
    d = (description or "").upper()
    if "AES" in d and "256" in d:
        return pymupdf.PDF_ENCRYPT_AES_256
    if "AES" in d:
        return pymupdf.PDF_ENCRYPT_AES_128
    if "RC4" in d and "40" in d:
        return pymupdf.PDF_ENCRYPT_RC4_40
    if "RC4" in d:
        return pymupdf.PDF_ENCRYPT_RC4_128
    return pymupdf.PDF_ENCRYPT_AES_256


def protection_options(pdf: pymupdf.Document, password: str | None, auth_code: int, needed_password: bool) -> dict:
    """Opzioni di salvataggio che mantengono la protezione del file originale.

    Il motore non conserva da solo la cifratura quando riscrive il file: la
    riapplichiamo con lo stesso metodo e gli stessi permessi. Se la password
    del proprietario non è nota, ne generiamo una casuale così che le
    restrizioni restino in vigore.
    """
    description = (pdf.metadata or {}).get("encryption")
    if not description:
        return {}
    opts: dict = {"encryption": _encryption_method(description), "permissions": pdf.permissions}
    owner_known = auth_code in (4, 6)
    if needed_password and password:
        opts["user_pw"] = password
        opts["owner_pw"] = password if owner_known else secrets.token_urlsafe(24)
    else:
        opts["user_pw"] = ""
        opts["owner_pw"] = secrets.token_urlsafe(24)
    return opts


def open_pdf_bytes(data: bytes, password: str | None = None, filetype: str | None = None) -> tuple[pymupdf.Document, bool, dict]:
    """Apre dati PDF (o altri formati supportati, convertiti in PDF).

    Restituisce (documento, convertito, opzioni_protezione): `convertito` è
    True se l'origine non era un PDF; `opzioni_protezione` serve a salvare
    mantenendo la cifratura originale.
    """
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype=filetype or "pdf")
        except Exception as e:
            raise DocumentError("Il file non è un PDF valido oppure è danneggiato.") from e
        needed_password = bool(pdf.needs_pass)
        auth_code = 0
        if needed_password:
            if password is None:
                pdf.close()
                raise PasswordRequired()
            auth_code = pdf.authenticate(password)
            if not auth_code:
                pdf.close()
                raise WrongPassword()
        protection = protection_options(pdf, password, auth_code, needed_password) if pdf.is_pdf else {}
        converted = False
        if not pdf.is_pdf:
            try:
                pdf_bytes = pdf.convert_to_pdf()
            except Exception as e:
                pdf.close()
                raise DocumentError("Impossibile convertire il file in PDF.") from e
            pdf.close()
            pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            converted = True
        if pdf.page_count == 0:
            pdf.close()
            raise DocumentError("Il documento non contiene pagine.")
        return pdf, converted, protection


_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jxr", ".pnm", ".pgm", ".ppm", ".pbm"}
_OTHER_TYPES = {".xps", ".oxps", ".epub", ".fb2", ".cbz", ".mobi", ".svg", ".txt"}


class Document:
    """Documento PDF modificabile con cronologia e salvataggio sicuro."""

    def __init__(
        self,
        pdf: pymupdf.Document,
        path: str | os.PathLike | None = None,
        password: str | None = None,
        *,
        display_name: str | None = None,
        modified: bool = False,
        protection: dict | None = None,
    ):
        self._pdf = pdf
        self.path: Path | None = Path(path) if path else None
        self.password = password
        self._display_name = display_name
        self.lock = ENGINE_LOCK
        # Protezione del file all'apertura (vuoto se non cifrato) e opzioni di
        # salvataggio persistenti: per impostazione predefinita si mantiene la
        # protezione originale; il modulo Proteggi può cambiarle.
        self.original_protection: dict = dict(protection or {})
        self.save_options: dict = dict(protection or {})
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []
        self._state = 0
        self._next_state = 1
        self._saved_state = -1 if modified else 0
        self._listeners: list[Listener] = []
        self._depth = 0
        self.version = 0
        self.closed = False

    # ------------------------------------------------------------------ apertura
    @classmethod
    def open(cls, path: str | os.PathLike, password: str | None = None) -> "Document":
        path = Path(path)
        try:
            data = path.read_bytes()
        except FileNotFoundError as e:
            raise DocumentError(f"Il file «{path.name}» non esiste.") from e
        except IsADirectoryError as e:
            raise DocumentError(f"«{path.name}» è una cartella, non un file.") from e
        except OSError as e:
            raise DocumentError(f"Impossibile leggere «{path.name}»: {e.strerror}.") from e
        suffix = path.suffix.lower()
        filetype = None
        if suffix in _IMAGE_TYPES or suffix in _OTHER_TYPES:
            filetype = suffix.lstrip(".")
        pdf, converted, protection = open_pdf_bytes(data, password, filetype)
        if converted:
            return cls(pdf, None, None, display_name=path.with_suffix(".pdf").name, modified=True)
        return cls(pdf, path, password, protection=protection)

    @classmethod
    def from_bytes(cls, data: bytes, *, display_name: str = "Senza titolo.pdf", password: str | None = None,
                   modified: bool = True) -> "Document":
        pdf, _converted, protection = open_pdf_bytes(data, password)
        return cls(pdf, None, password, display_name=display_name, modified=modified, protection=protection)

    @classmethod
    def new(cls, width: float = 595.28, height: float = 841.89, pages: int = 1) -> "Document":
        """Nuovo documento con pagine vuote (A4 verticale per impostazione)."""
        with ENGINE_LOCK:
            pdf = pymupdf.open()
            for _ in range(max(1, pages)):
                pdf.new_page(width=width, height=height)
        return cls(pdf, None, None, display_name="Senza titolo.pdf", modified=False)

    # --------------------------------------------------------------- proprietà
    @property
    def pdf(self) -> pymupdf.Document:
        """Documento del motore. Usarlo tenendo `lock` (o dentro `change()`)."""
        return self._pdf

    @property
    def page_count(self) -> int:
        with self.lock:
            return self._pdf.page_count

    @property
    def display_name(self) -> str:
        if self.path is not None:
            return self.path.name
        return self._display_name or "Senza titolo.pdf"

    @property
    def is_modified(self) -> bool:
        return self._state != self._saved_state

    @property
    def needs_save_as(self) -> bool:
        return self.path is None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def page_text(self, index: int, sort: bool = True) -> str:
        with self.lock:
            return self._pdf[index].get_text("text", sort=sort)

    # --------------------------------------------------------------- osservatori
    def add_listener(self, fn: Listener) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, kind: str) -> None:
        if kind == CONTENT:
            self.version += 1
        for fn in list(self._listeners):
            fn(self, kind)

    # ---------------------------------------------------------------- modifiche
    @contextmanager
    def change(self, label: str) -> Iterator[pymupdf.Document]:
        """Blocco di modifica annullabile come singolo passo.

        Esempio::

            with doc.change("Ruota pagina") as pdf:
                pdf[0].set_rotation(90)
        """
        with self.lock:
            if self.closed:
                raise DocumentError("Il documento è stato chiuso.")
            if self._depth:
                self._depth += 1
                try:
                    yield self._pdf
                finally:
                    self._depth -= 1
                return
            before = self._snapshot()
            before_opts = dict(self.save_options)
            previous_state = self._state
            self._depth = 1
            try:
                yield self._pdf
            except BaseException:
                self._depth = 0
                self._restore(before)
                self.save_options = before_opts
                raise
            self._depth = 0
            self._push(self._undo, _Snapshot(label, before, previous_state, before_opts))
            self._redo.clear()
            self._state = self._next_state
            self._next_state += 1
        self._notify(CONTENT)

    def replace_with(self, data: bytes, label: str) -> None:
        """Sostituisce l'intero contenuto con un altro PDF (annullabile)."""
        with self.change(label):
            self._restore(data)

    def undo(self) -> bool:
        with self.lock:
            if not self._undo or self._depth:
                return False
            snap = self._undo.pop()
            self._redo.append(_Snapshot(snap.label, self._snapshot(), self._state, dict(self.save_options)))
            self._restore(snap.data)
            self._state = snap.state
            self.save_options = dict(snap.save_options)
        self._notify(CONTENT)
        return True

    def redo(self) -> bool:
        with self.lock:
            if not self._redo or self._depth:
                return False
            snap = self._redo.pop()
            self._push(self._undo, _Snapshot(snap.label, self._snapshot(), self._state, dict(self.save_options)))
            self._restore(snap.data)
            self._state = snap.state
            self.save_options = dict(snap.save_options)
        self._notify(CONTENT)
        return True

    def _snapshot(self) -> bytes:
        # KEEP: la copia conserva la cifratura originale (stesse password e permessi);
        # _restore() ri-autentica con self.password.
        return self._pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_KEEP)

    def _restore(self, data: bytes) -> None:
        new = pymupdf.open(stream=data, filetype="pdf")
        if new.needs_pass and self.password is not None:
            new.authenticate(self.password)
        old, self._pdf = self._pdf, new
        if old is not new:
            old.close()

    @staticmethod
    def _push(stack: list[_Snapshot], snap: _Snapshot) -> None:
        stack.append(snap)
        total = sum(len(s.data) for s in stack)
        while len(stack) > 1 and (len(stack) > MAX_UNDO_STEPS or total > MAX_UNDO_BYTES):
            total -= len(stack.pop(0).data)

    # --------------------------------------------------------------- salvataggio
    @property
    def is_protected(self) -> bool:
        """True se al salvataggio il file verrà cifrato."""
        return self.save_options.get("encryption", pymupdf.PDF_ENCRYPT_NONE) not in (
            pymupdf.PDF_ENCRYPT_NONE, pymupdf.PDF_ENCRYPT_KEEP)

    def to_bytes(self, **options) -> bytes:
        opts = {"garbage": 3, "deflate": True}
        with self.lock:
            keep = (self.save_options == self.original_protection and self.original_protection
                    and (self._pdf.metadata or {}).get("encryption"))
        if keep and "encryption" not in options:   # protezione invariata e ancora presente: riusa la cifratura originale
            opts.update(options)
            opts["encryption"] = pymupdf.PDF_ENCRYPT_KEEP
            return self._write(opts)
        opts.update(self.save_options)
        opts.update(options)
        if opts.get("encryption", pymupdf.PDF_ENCRYPT_NONE) in (pymupdf.PDF_ENCRYPT_NONE, pymupdf.PDF_ENCRYPT_KEEP):
            opts["encryption"] = pymupdf.PDF_ENCRYPT_NONE
            for key in ("user_pw", "owner_pw", "permissions"):
                opts.pop(key, None)
        return self._write(opts)

    def _write(self, opts: dict) -> bytes:
        """Serializza senza toccare il documento aperto.

        Con garbage ≥ 2 MuPDF compatta e RINUMERA gli oggetti del documento che scrive: gli xref
        tenuti da moduli e strumenti (selezioni, collegamenti, immagini) diventerebbero sbagliati
        dopo ogni salvataggio. La compattazione si fa quindi su una copia.
        """
        with self.lock:
            if opts.get("garbage", 0) < 2:
                return self._pdf.tobytes(**opts)
            copy = pymupdf.open("pdf", self._snapshot())
        try:
            if copy.needs_pass and self.password is not None:
                copy.authenticate(self.password)
            return copy.tobytes(**opts)
        finally:
            copy.close()

    def save(self, path: str | os.PathLike | None = None, **options) -> None:
        """Salva (in modo atomico). Senza percorso usa quello corrente."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise DocumentError("Scegli dove salvare il documento.")
        if (path is None and not options and not self.is_modified
                and self.save_options == self.original_protection and target.exists()):
            self._notify(SAVED)   # niente da scrivere: non invalidare firme né cifratura
            return
        data = self.to_bytes(**options)
        atomic_write(target, data)
        self.path = target
        self._saved_state = self._state
        self._notify(SAVED)

    def save_copy(self, path: str | os.PathLike, **options) -> None:
        """Salva una copia senza cambiare il file associato al documento."""
        atomic_write(Path(path), self.to_bytes(**options))

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self._undo.clear()
            self._redo.clear()
            try:
                self._pdf.close()
            except Exception:
                pass
        self._notify(CLOSED)
        self._listeners.clear()
