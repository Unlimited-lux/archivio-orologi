"""Documento aperto: caricamento, modifiche con annulla/ripeti, salvataggio atomico.

Regole per chi modifica un documento:
- ogni modifica passa da `Document.change(etichetta, kind=...)`, che registra lo
  stato precedente per l'annullamento e, se il blocco solleva un'eccezione,
  ripristina il documento com'era (nessuna modifica parziale);
- prima di ogni modifica il nucleo interroga le guardie registrate
  (`add_change_guard`) e rifiuta le modifiche ai documenti bloccati
  (`set_locked`): in entrambi i casi solleva `OperationCancelled` senza
  toccare nulla;
- il file su disco non viene mai toccato finché l'utente non salva, e il
  salvataggio scrive prima un file temporaneo e poi lo sostituisce;
- un documento firmato si salva in modo incrementale (byte originali + modifiche
  in coda), anche con «Salva con nome», così le firme restano valide per le
  modifiche consentite (NFR-09, SIG-21);
- non conservare oggetti pagina (`pdf[i]`) oltre la singola operazione:
  dopo un annulla/ripeti il documento interno viene sostituito.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import pymupdf

from ..i18n import _
from . import security
from .engine import ENGINE_LOCK
from .errors import DocumentError, OperationCancelled, PasswordRequired, WrongPassword
from .incremental import IncrementalSaveError, incremental_update, signed_fields
from .security import ACCESS_NONE, ACCESS_OWNER, ACCESS_USER, ALL_PERMISSIONS

MAX_UNDO_STEPS = 100
MAX_UNDO_BYTES = 512 * 1024 * 1024

# Tipi di notifica inviati agli osservatori.
CONTENT = "content"   # il contenuto è cambiato (modifica, annulla, ripeti)
SAVED = "saved"       # salvato: cambia titolo/stato, non il contenuto
CLOSED = "closed"
ACCESS = "access"     # tipo di accesso o permessi cambiati (es. `unlock()` con la password dei permessi)
LOCK = "lock"         # documento bloccato o sbloccato in sola lettura logica (`set_locked()`)

# Tipi di modifica per `change(etichetta, kind=...)`: le guardie li usano per
# distinguere le modifiche consentite in un documento firmato (commenti, moduli)
# da quelle che invalidano le firme. Altri valori sono ammessi.
CHANGE_KINDS = ("content", "annotation", "form", "pages", "metadata")

# SEC-06 «Cifra tutto tranne i metadati»: il motore (PyMuPDF 1.2x / MuPDF) non ha
# un'opzione per scrivere /EncryptMetadata false e, anche mantenendo un dizionario
# /Encrypt che la dichiara, cifra comunque il flusso /Metadata. L'opzione
# `save_options["encrypt_metadata"]` è quindi accettata ma ignorata: i metadati
# vengono sempre cifrati. I moduli possono consultare questa costante.
ENCRYPT_METADATA_SUPPORTED = False

Listener = Callable[["Document", str], None]
ChangeGuard = Callable[["Document", str, str], "bool | None"]
SaveStrategy = Callable[["Document", "Path | None"], "bytes | None"]


@dataclass
class _Snapshot:
    label: str
    data: bytes
    state: int
    save_options: dict = field(default_factory=dict)
    aligned: bool = True     # numerazione degli oggetti continua con quella del file (vedi Document._aligned)


@dataclass
class OpenState:
    """Esito dell'apertura: conversione, protezione, accesso e stato del file."""

    converted: bool = False
    protection: dict = field(default_factory=dict)     # opzioni di salvataggio che mantengono la cifratura
    access: str = ACCESS_NONE
    permissions: int = ALL_PERMISSIONS                  # /P effettivo con accesso utente
    encryption: dict = field(default_factory=dict)      # security.encryption_info() (+ perms_tampered)
    repaired: bool = False                              # il motore ha ricostruito la xref

    @classmethod
    def from_pdf(cls, pdf: pymupdf.Document) -> "OpenState":
        """Stato dedotto da un documento del motore già aperto (accesso utente se cifrato)."""
        st = cls()
        try:
            st.repaired = bool(getattr(pdf, "is_repaired", False))
            if security.is_encrypted(pdf):
                st.encryption = security.encryption_info(pdf)
                st.access = ACCESS_USER
                # come all'apertura: bit 9–12 per R2, AND con /Perms, -1 se tutto è consentito
                st.permissions, st.encryption["perms_tampered"] = security.effective_permissions(pdf, None)
        except Exception:
            pass
        return st


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
    riapplichiamo con lo stesso metodo e gli stessi permessi (/P del file, anche
    se il documento è aperto dal proprietario). Se la password del proprietario
    non è nota, ne generiamo una casuale così che le restrizioni restino in vigore.
    """
    description = (pdf.metadata or {}).get("encryption")
    if not description:
        return {}
    perms = security.encryption_info(pdf).get("P")
    opts: dict = {"encryption": _encryption_method(description),
                  "permissions": perms if perms is not None else pdf.permissions}
    owner_known = auth_code in (4, 6)
    if needed_password and password:
        opts["user_pw"] = password
        opts["owner_pw"] = password if owner_known else secrets.token_urlsafe(24)
    else:
        opts["user_pw"] = ""
        opts["owner_pw"] = password if (owner_known and password) else secrets.token_urlsafe(24)
    return opts


def _file_stamp(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _security_handler(data: bytes) -> str | None:
    """Nome del gestore di sicurezza (/Encrypt /Filter) letto dai byte grezzi, se presente."""
    m = re.search(rb"/Encrypt\s+(\d+)\s+(\d+)\s+R", data)
    body = None
    if m:
        obj = re.search(rb"(?<!\d)" + m.group(1) + rb"\s+" + m.group(2) + rb"\s+obj(.*?)endobj", data, re.S)
        body = obj.group(1) if obj else None
    else:
        inline = re.search(rb"/Encrypt\s*<<(.*?)>>", data, re.S)
        body = inline.group(1) if inline else None
    f = re.search(rb"/Filter\s*/([^\s/<>\[\]()]+)", body) if body else None
    return f.group(1).decode("latin-1") if f else None


def _empty_user_password_access(pdf: pymupdf.Document, data: bytes, password: str | None) -> int:
    """Codice di autenticazione per un file cifrato con password di apertura vuota.

    Il motore lo apre da solo con accesso utente. Riprovare con la password vuota non
    cambia nulla (è quella giusta) e rivela se anche la password dei permessi è vuota;
    una password fornita si verifica su una copia, perché una password errata
    toglierebbe l'accesso al documento aperto.
    """
    code = pdf.authenticate("") or 2
    if code & 4 or not password:
        return code
    probe = pymupdf.open(stream=data, filetype="pdf")
    try:
        owner = probe.authenticate(password)
    finally:
        probe.close()
    if owner & 4:
        pdf.authenticate(password)
        return owner
    return code


def open_document_bytes(data: bytes, password: str | None = None,
                        filetype: str | None = None) -> tuple[pymupdf.Document, OpenState]:
    """Apre dati PDF (o altri formati supportati, convertiti in PDF).

    Restituisce (documento del motore, OpenState). Solleva PasswordRequired,
    WrongPassword o DocumentError con un messaggio per l'utente.
    """
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype=filetype or "pdf")
        except Exception as e:
            handler = _security_handler(data)
            if handler and handler != "Standard":
                raise DocumentError(f"Il file è protetto con un sistema di sicurezza non supportato ({handler}).") from e
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
        state = OpenState()
        if pdf.is_pdf:
            state.repaired = bool(getattr(pdf, "is_repaired", False))
            if security.is_encrypted(pdf):
                if not needed_password:
                    auth_code = _empty_user_password_access(pdf, data, password)
                state.encryption = security.encryption_info(pdf)
                state.access = ACCESS_OWNER if auth_code & 4 else ACCESS_USER
                used = password if (needed_password or auth_code & 4) else ""
                state.permissions, tampered = security.effective_permissions(pdf, used)
                state.encryption["perms_tampered"] = tampered
            state.protection = protection_options(pdf, password, auth_code, needed_password)
        if not pdf.is_pdf:
            try:
                pdf_bytes = pdf.convert_to_pdf()
            except Exception as e:
                pdf.close()
                raise DocumentError("Impossibile convertire il file in PDF.") from e
            pdf.close()
            pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            state.converted = True
        if pdf.page_count == 0:
            pdf.close()
            raise DocumentError("Il documento non contiene pagine.")
        return pdf, state


def open_pdf_bytes(data: bytes, password: str | None = None, filetype: str | None = None) -> tuple[pymupdf.Document, bool, dict]:
    """Come `open_document_bytes`, nella forma storica (documento, convertito, opzioni_protezione).

    `convertito` è True se l'origine non era un PDF; `opzioni_protezione` serve a
    salvare mantenendo la cifratura originale.
    """
    pdf, state = open_document_bytes(data, password, filetype)
    return pdf, state.converted, state.protection


_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jxr", ".pnm", ".pgm", ".ppm", ".pbm"}
_OTHER_TYPES = {".xps", ".oxps", ".epub", ".fb2", ".cbz", ".mobi", ".svg", ".txt"}


class Document:
    """Documento PDF modificabile con cronologia e salvataggio sicuro.

    Attributi principali oltre a quelli storici:
    - `original_bytes`: byte del file all'apertura (o dopo l'ultimo salvataggio), base
      del salvataggio incrementale; None per documenti nuovi o convertiti;
    - `has_signatures`: il file contiene campi firma firmati (/FT /Sig con /V);
    - `repaired`: il motore ha dovuto ricostruire la xref all'apertura (CNV-32);
    - `access` («none», «user», «owner»), `permissions`, `encryption_info`: esito
      dell'autenticazione all'apertura (SEC-01…SEC-03);
    - `locked_reason`: motivo della sola lettura logica (SIG-17), None se modificabile.
    """

    def __init__(
        self,
        pdf: pymupdf.Document,
        path: str | os.PathLike | None = None,
        password: str | None = None,
        *,
        display_name: str | None = None,
        modified: bool = False,
        protection: dict | None = None,
        state: OpenState | None = None,
        original_bytes: bytes | None = None,
    ):
        self._pdf = pdf
        self.path: Path | None = Path(path) if path else None
        self.password = password
        self._display_name = display_name
        self.disk_stamp = _file_stamp(self.path) if self.path is not None else None
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
        self._guards: list[ChangeGuard] = []
        self._depth = 0
        self._change_thread: int | None = None
        self.version = 0
        self.closed = False
        with ENGINE_LOCK:
            st = state if state is not None else OpenState.from_pdf(pdf)
            try:
                signed = signed_fields(pdf) > 0
            except Exception:
                signed = False
        # Accesso e protezione (SEC-01…SEC-03)
        self.access: str = st.access
        self._permissions: int = int(st.permissions)
        self.encryption_info: dict = dict(st.encryption)
        # Stato del file (CNV-32) e base del salvataggio incrementale (NFR-09)
        self.repaired: bool = bool(st.repaired)
        self.original_bytes: bytes | None = original_bytes
        self.has_signatures: bool = signed
        self._base_aligned = original_bytes is not None   # numerazione degli oggetti = quella di original_bytes
        # Lo stato attuale conserva la numerazione degli oggetti del file: False dopo
        # `replace_with()` con un PDF che non prosegue `original_bytes` (es. compattato e
        # rinumerato). Segue annulla/ripeti tramite le istantanee.
        self._aligned = True
        # Sola lettura logica (SIG-17) e strategia di salvataggio facoltativa di un modulo
        self.locked_reason: str | None = None
        self.save_strategy: SaveStrategy | None = None

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
        pdf, state = open_document_bytes(data, password, filetype)
        if state.converted:
            return cls(pdf, None, None, display_name=path.with_suffix(".pdf").name, modified=True, state=state)
        return cls(pdf, path, password, protection=state.protection, state=state, original_bytes=data)

    @classmethod
    def from_bytes(cls, data: bytes, *, display_name: str = "Senza titolo.pdf", password: str | None = None,
                   modified: bool = True) -> "Document":
        pdf, state = open_document_bytes(data, password)
        return cls(pdf, None, password, display_name=display_name, modified=modified, protection=state.protection,
                   state=state, original_bytes=None if state.converted else data)

    @classmethod
    def new(cls, width: float = 595.28, height: float = 841.89, pages: int = 1) -> "Document":
        """Nuovo documento con pagine vuote (A4 verticale per impostazione)."""
        with ENGINE_LOCK:
            pdf = pymupdf.open()
            for _i in range(max(1, pages)):
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
    def can_copy(self) -> bool:
        """Il file consente di copiarne il contenuto (permesso di copia, o accesso da proprietario)."""
        return self.allows(pymupdf.PDF_PERM_COPY)

    @property
    def is_modified(self) -> bool:
        return self._state != self._saved_state

    @property
    def needs_save_as(self) -> bool:
        return self.path is None or self.read_only

    @property
    def read_only(self) -> bool:
        """Il file su disco non è scrivibile: «Salva» diventa «Salva con nome»."""
        if self.path is None:
            return False
        target = os.path.realpath(self.path)
        if os.path.exists(target):
            return not os.access(target, os.W_OK)
        return not os.access(os.path.dirname(target) or ".", os.W_OK)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) and self.locked_reason is None

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self.locked_reason is None

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def page_text(self, index: int, sort: bool = True) -> str:
        with self.lock:
            return self._pdf[index].get_text("text", sort=sort)

    # ------------------------------------------------------- accesso e permessi
    @property
    def permissions(self) -> int:
        """Bit /P effettivi nella sessione (con l'AND di /Perms per i file R6).

        -1 = tutto consentito: file non cifrato o accesso da proprietario. Restano
        validi anche dopo le modifiche, quando il documento interno è una copia.
        """
        if self.access != ACCESS_USER or self._engine_owner():
            return ALL_PERMISSIONS
        return self._permissions

    def allows(self, perm: int) -> bool:
        """True se il documento consente i permessi `pymupdf.PDF_PERM_*` indicati (anche combinati).

        I commenti comprendono la compilazione dei campi, la modifica comprende
        l'assemblaggio, l'estrazione per l'accessibilità è sempre consentita e la stampa
        fedele richiede anche la stampa (SEC-02).
        """
        return security.allows(self.permissions, perm)

    def _engine_owner(self) -> bool:
        """Compatibilità: un modulo ha autenticato il proprietario direttamente sul motore."""
        p = self.encryption_info.get("P")
        if p is None:
            return False
        try:
            with self.lock:
                if not security.is_encrypted(self._pdf):
                    return False
                live = int(self._pdf.permissions)
        except Exception:
            return False
        full = 0xFFFFFFFC
        return live != p and (live & full) == full

    def _owner_code(self, password: str) -> int:
        """Codice di autenticazione di `password` sul file, verificato su una copia."""
        with self.lock:
            source = self.original_bytes
            if source is None and security.is_encrypted(self._pdf):
                source = self._snapshot()
            if source is None:
                return 0
            try:
                probe = pymupdf.open(stream=source, filetype="pdf")
            except Exception:
                return 0
            try:
                return int(probe.authenticate(password)) if security.is_encrypted(probe) else 0
            finally:
                probe.close()

    def unlock(self, password: str) -> bool:
        """Passa all'accesso da proprietario con la password dei permessi (SEC-02 «Sblocca…»).

        Funziona anche dopo modifiche: annulla/ripeti ri-autenticano il proprietario e
        la password diventa quella del proprietario in `original_protection`,
        `save_options` e nelle opzioni registrate nella cronologia, così al salvataggio
        la protezione resta identica. Restituisce True se l'accesso è completo (anche
        per un file non cifrato o già aperto dal proprietario); notifica ACCESS.
        """
        if self.access != ACCESS_USER:
            return True
        if not password or not (self._owner_code(password) & 4):
            return False
        with self.lock:
            if security.is_encrypted(self._pdf):
                self._pdf.authenticate(password)       # verificata: non toglie l'accesso al documento aperto
            old_owner = self.original_protection.get("owner_pw")
            for opts in [self.original_protection, self.save_options] + [s.save_options for s in self._undo + self._redo]:
                if opts and "owner_pw" in opts and opts.get("owner_pw") == old_owner:
                    opts["owner_pw"] = password
            self.password = password
            self.access = ACCESS_OWNER
        self._notify(ACCESS)
        return True

    # ---------------------------------------------------- sola lettura logica
    def set_locked(self, reason: str | None) -> None:
        """Blocca (con un motivo) o sblocca (None) le modifiche al documento (SIG-17).

        Bloccato, `change()` solleva OperationCancelled con il motivo e annulla/ripeti
        sono disattivati; il salvataggio resta possibile. Notifica LOCK se cambia.
        """
        if reason is not None:
            reason = str(reason).strip() or _("Il documento è in sola lettura.")
        if reason == self.locked_reason:
            return
        self.locked_reason = reason
        self._notify(LOCK)

    @property
    def locked(self) -> bool:
        return self.locked_reason is not None

    # --------------------------------------------------------------- osservatori
    def add_listener(self, fn: Listener) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def add_change_guard(self, fn: ChangeGuard) -> None:
        """Registra `fn(document, etichetta, kind) -> bool`, chiamata prima di ogni modifica.

        Se una guardia restituisce False la modifica non avviene e `change()` solleva
        OperationCancelled (True o None = prosegui). Una guardia può anche sollevare
        OperationCancelled con un messaggio proprio. Le guardie sono chiamate fuori dal
        lock del motore (possono mostrare una finestra), nell'ordine di registrazione e
        solo per il blocco esterno, non per i `change()` annidati.
        """
        if fn not in self._guards:
            self._guards.append(fn)

    def remove_change_guard(self, fn: ChangeGuard) -> None:
        if fn in self._guards:
            self._guards.remove(fn)

    def _notify(self, kind: str) -> None:
        if kind == SAVED and self.path is not None:
            # chi notifica SAVED ha appena scritto il file (anche i moduli con un proprio salvataggio)
            self.disk_stamp = _file_stamp(self.path)
        self._emit(kind)

    def _emit(self, kind: str) -> None:
        if kind == CONTENT:
            self.version += 1
        for fn in list(self._listeners):
            try:
                fn(self, kind)
            except Exception:       # un osservatore difettoso non deve far fallire un'operazione riuscita
                sys.excepthook(*sys.exc_info())

    # ---------------------------------------------------------------- modifiche
    def _check_change(self, label: str, kind: str) -> None:
        """Documento bloccato e guardie: solleva OperationCancelled se la modifica non è ammessa."""
        if self.locked_reason is not None:
            raise OperationCancelled(self.locked_reason)
        for guard in list(self._guards):
            try:
                ok = guard(self, label, kind)
            except OperationCancelled:
                raise
            except Exception:       # una guardia difettosa non blocca ogni modifica
                sys.excepthook(*sys.exc_info())
                continue
            if ok is not None and not ok:
                raise OperationCancelled(_("Operazione annullata: {label}.").format(label=label))

    @contextmanager
    def change(self, label: str, kind: str = "content") -> Iterator[pymupdf.Document]:
        """Blocco di modifica annullabile come singolo passo.

        `kind` classifica la modifica per le guardie: «content» (predefinito),
        «annotation», «form», «pages», «metadata» (vedi CHANGE_KINDS).

        Esempio::

            with doc.change("Ruota pagina", kind="pages") as pdf:
                pdf[0].set_rotation(90)
        """
        nested = bool(self._depth) and self._change_thread == threading.get_ident()
        if not nested and not self.closed:
            self._check_change(label, kind)
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
            if self.locked_reason is not None:     # bloccato nel frattempo da un altro thread
                raise OperationCancelled(self.locked_reason)
            before = self._snapshot()
            before_opts = dict(self.save_options)
            before_aligned = self._aligned
            previous_state = self._state
            self._depth = 1
            self._change_thread = threading.get_ident()
            try:
                yield self._pdf
            except BaseException:
                self._depth = 0
                self._change_thread = None
                self._restore(before)
                self.save_options = before_opts
                self._aligned = before_aligned
                raise
            self._depth = 0
            self._change_thread = None
            self._push(self._undo, _Snapshot(label, before, previous_state, before_opts, before_aligned))
            self._redo.clear()
            self._state = self._next_state
            self._next_state += 1
        self._notify(CONTENT)

    def replace_with(self, data: bytes, label: str, kind: str = "content") -> None:
        """Sostituisce l'intero contenuto con un altro PDF (annullabile).

        Se `data` non prosegue i byte del file (`original_bytes` + revisioni in coda), la
        numerazione degli oggetti cambia: fino all'annullamento il salvataggio incrementale
        non è possibile e un documento firmato si riscrive per intero.
        """
        with self.change(label, kind):
            self._restore(data)
            base = self.original_bytes
            self._aligned = bool(isinstance(data, (bytes, bytearray)) and base is not None and data.startswith(base))

    def undo(self) -> bool:
        with self.lock:
            if not self._undo or self._depth or self.locked_reason is not None:
                return False
            snap = self._undo.pop()
            self._redo.append(_Snapshot(snap.label, self._snapshot(), self._state, dict(self.save_options),
                                        self._aligned))
            self._restore(snap.data)
            self._state = snap.state
            self.save_options = dict(snap.save_options)
            self._aligned = snap.aligned
        self._notify(CONTENT)
        return True

    def redo(self) -> bool:
        with self.lock:
            if not self._redo or self._depth or self.locked_reason is not None:
                return False
            snap = self._redo.pop()
            self._push(self._undo, _Snapshot(snap.label, self._snapshot(), self._state, dict(self.save_options),
                                             self._aligned))
            self._restore(snap.data)
            self._state = snap.state
            self.save_options = dict(snap.save_options)
            self._aligned = snap.aligned
        self._notify(CONTENT)
        return True

    def _snapshot(self) -> bytes:
        # KEEP: la copia conserva la cifratura originale (stesse password e permessi);
        # _restore() ri-autentica con self.password. garbage=0: stessa numerazione degli
        # oggetti, su cui si basa il salvataggio incrementale.
        return self._pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_KEEP)

    def _restore(self, data: bytes) -> None:
        new = pymupdf.open(stream=data, filetype="pdf")
        if new.needs_pass:
            if self.password is not None:
                new.authenticate(self.password)
        elif self.access == ACCESS_OWNER and security.is_encrypted(new):
            new.authenticate(self.password or "")     # di nuovo come proprietario (anche dopo unlock())
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
        """Il documento come file PDF completo e compattato (senza toccare il documento aperto).

        Per impostazione predefinita senza flussi di oggetti, così chi cerca nei byte
        (es. `/ByteRange`, `/Sig`) li trova; i file salvati su disco li usano (`save_bytes`).
        """
        opts = {"garbage": 3, "deflate": True}
        with self.lock:
            keep = (self.save_options == self.original_protection and self.original_protection
                    and (self._pdf.metadata or {}).get("encryption"))
        if keep and "encryption" not in options:   # protezione invariata e ancora presente: riusa la cifratura originale
            opts.update(options)
            opts["encryption"] = pymupdf.PDF_ENCRYPT_KEEP
            opts.pop("encrypt_metadata", None)
            return self._write(opts)
        opts.update(self.save_options)
        opts.update(options)
        opts.pop("encrypt_metadata", None)        # non supportato dal motore (ENCRYPT_METADATA_SUPPORTED)
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

    def _wants_incremental(self, incremental: bool | None, options: dict) -> bool:
        """Salvataggio incrementale: obbligatorio (automatico) per i documenti firmati.

        Serve la base `original_bytes` con la stessa numerazione degli oggetti del documento
        aperto e un file non riparato; un cambio di protezione o opzioni di scrittura
        esplicite richiedono invece la riscrittura completa.
        """
        if incremental is False:
            return False
        possible = (self.original_bytes is not None and self._base_aligned and self._aligned
                    and not self.repaired)
        same_protection = self.save_options == self.original_protection
        if incremental:
            if not possible:
                raise IncrementalSaveError(_("Il salvataggio incrementale non è possibile per questo documento."))
            if not same_protection:
                raise IncrementalSaveError(_("Il cambio di protezione richiede la riscrittura completa del file."))
            return True
        return possible and self.has_signatures and same_protection and not options

    def save_bytes(self, path: str | os.PathLike | None = None, *, incremental: bool | None = None,
                   **options) -> bytes:
        """Byte che `save()` scriverebbe in `path`.

        - con una `save_strategy` registrata che restituisce dei byte, quelli;
        - per un documento firmato (o con `incremental=True`): `original_bytes` + un
          aggiornamento incrementale con gli oggetti cambiati;
        - altrimenti il file completo, compattato e con flussi di oggetti.
        """
        if self.save_strategy is not None:
            data = self.save_strategy(self, Path(path) if path is not None else self.path)
            if data is not None:
                return data
        if self._wants_incremental(incremental, options):
            with self.lock:
                return incremental_update(self.original_bytes, self._pdf, self.password)
        return self.to_bytes(**{"use_objstms": 1, **options})

    def save(self, path: str | os.PathLike | None = None, *, incremental: bool | None = None, **options) -> None:
        """Salva (in modo atomico). Senza percorso usa quello corrente.

        I documenti firmati si salvano in modo incrementale, anche su un nuovo percorso
        (vedi `save_bytes`); `incremental=False` forza la riscrittura completa.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise DocumentError("Scegli dove salvare il documento.")
        if (path is None and not options and incremental is None and not self.is_modified
                and self.save_options == self.original_protection and target.exists()):
            self._emit(SAVED)   # niente da scrivere: non invalidare firme né cifratura
            return
        data = self.save_bytes(target, incremental=incremental, **options)
        atomic_write(target, data)
        self.path = target
        self._saved_state = self._state
        self._after_write(data)
        self._notify(SAVED)

    def _after_write(self, data: bytes) -> None:
        """Il file salvato diventa la base dei salvataggi successivi."""
        base = self.original_bytes
        # un aggiornamento incrementale conserva la numerazione; una riscrittura completa no
        self._base_aligned = bool(self._base_aligned and base is not None and data.startswith(base))
        self.original_bytes = data
        try:
            with self.lock:
                self.has_signatures = signed_fields(self._pdf) > 0
        except Exception:
            pass

    def disk_changed(self) -> bool:
        """Il file su disco è cambiato (altro programma) dall'apertura o dall'ultimo salvataggio."""
        if self.path is None or self.disk_stamp is None:
            return False
        now = _file_stamp(self.path)
        return now is not None and now != self.disk_stamp

    def save_copy(self, path: str | os.PathLike, *, incremental: bool | None = None, **options) -> None:
        """Salva una copia senza cambiare il file associato al documento (incrementale se firmato)."""
        atomic_write(Path(path), self.save_bytes(path, incremental=incremental, **options))

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
        self._guards.clear()
