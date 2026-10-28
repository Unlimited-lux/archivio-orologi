"""Protezione con password: impostazioni, verifica, applicazione e rimozione.

Logica pura. La protezione non cambia il contenuto del documento: vale al
salvataggio tramite `document.save_options`. Il cambio avviene comunque dentro
`document.change()`, così è un passo di annulla/ripeti; poiché il nucleo
ripristina solo il contenuto, `_OptionsHistory` riallinea `save_options` allo
stato del documento dopo annulla/ripeti (vedi CORE_REQUESTS.md).
"""

from __future__ import annotations

import secrets
import weakref
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, CONTENT, Document, _

# ------------------------------------------------------------------ costanti

AES256 = "aes256"
AES128 = "aes128"
RC4 = "rc4"

METHODS = {
    AES256: pymupdf.PDF_ENCRYPT_AES_256,
    AES128: pymupdf.PDF_ENCRYPT_AES_128,
    RC4: pymupdf.PDF_ENCRYPT_RC4_128,
}


def method_labels() -> dict[str, str]:
    return {
        AES256: _("AES a 256 bit (consigliato)"),
        AES128: _("AES a 128 bit"),
        RC4: _("RC4 a 128 bit (compatibile con lettori vecchi)"),
    }


PRINT_NONE = "none"
PRINT_LOW = "low"
PRINT_HIGH = "high"

# Permessi modificabili (il motore li esprime come bit).
PERM_BITS = {
    "modify": pymupdf.PDF_PERM_MODIFY,
    "copy": pymupdf.PDF_PERM_COPY,
    "annotate": pymupdf.PDF_PERM_ANNOTATE,
    "form": pymupdf.PDF_PERM_FORM,
    "accessibility": pymupdf.PDF_PERM_ACCESSIBILITY,
    "assemble": pymupdf.PDF_PERM_ASSEMBLE,
}
ALL_BITS = (pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ | pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_COPY
            | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ACCESSIBILITY
            | pymupdf.PDF_PERM_ASSEMBLE)


def permission_labels() -> dict[str, str]:
    return {
        "modify": _("Modifica del contenuto"),
        "copy": _("Copia di testo e immagini"),
        "annotate": _("Commenti e annotazioni"),
        "form": _("Compilazione dei moduli"),
        "accessibility": _("Estrazione per l'accessibilità"),
        "assemble": _("Assemblaggio (inserire, ruotare, eliminare pagine)"),
    }


def print_labels() -> dict[str, str]:
    return {
        PRINT_HIGH: _("Consentita"),
        PRINT_LOW: _("Solo a bassa risoluzione"),
        PRINT_NONE: _("Non consentita"),
    }


# -------------------------------------------------------------- impostazioni


class ProtectionError(ValueError):
    """Impostazioni non valide: il messaggio è per l'utente."""


@dataclass
class ProtectionSettings:
    open_password: str = ""
    permissions_password: str = ""
    restrict: bool = False                      # applica le restrizioni dei permessi
    allowed: set[str] = field(default_factory=lambda: set(PERM_BITS))
    print_level: str = PRINT_HIGH
    method: str = AES256

    # ------------------------------------------------------------- permessi
    def permission_value(self) -> int:
        if not self.restrict:
            return -1
        allowed = set(self.allowed)
        if "annotate" in allowed:
            allowed.add("form")             # i commenti comprendono la compilazione
        if self.method == AES256:
            allowed.add("accessibility")    # obbligatoria in PDF 2.0
        value = 0
        for key, bit in PERM_BITS.items():
            if key in allowed:
                value |= bit
        if self.print_level == PRINT_LOW:
            value |= pymupdf.PDF_PERM_PRINT
        elif self.print_level == PRINT_HIGH:
            value |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
        # bit 1–2 a 0, bit riservati 7–8 e 13–32 a 1 (ISO 32000-2, tabella 22)
        return value | (~ALL_BITS & ~0b11)

    @property
    def has_restrictions(self) -> bool:
        return self.restrict and (self.permission_value() & ALL_BITS) != ALL_BITS

    # ------------------------------------------------------------ controllo
    def validate(self) -> None:
        if not self.open_password and not self.has_restrictions:
            raise ProtectionError(_("Imposta una password di apertura oppure limita almeno un permesso."))
        if self.has_restrictions and not self.permissions_password:
            raise ProtectionError(_("Per limitare i permessi serve una password dei permessi."))
        if self.permissions_password and self.open_password == self.permissions_password:
            raise ProtectionError(_("La password dei permessi deve essere diversa da quella di apertura: "
                                    "altrimenti chi apre il documento avrebbe tutti i permessi."))
        if self.method not in METHODS:
            raise ProtectionError(_("Livello di cifratura non riconosciuto."))
        if self.method != AES256:
            for pw in (self.open_password, self.permissions_password):
                try:
                    pw.encode("latin-1")
                except UnicodeEncodeError:
                    raise ProtectionError(_("Con questo livello di cifratura le password possono contenere solo "
                                            "lettere latine, numeri e simboli comuni. Usa AES a 256 bit.")) from None
                if len(pw.encode("latin-1")) > 32:
                    raise ProtectionError(_("Con questo livello di cifratura le password sono limitate a 32 "
                                            "caratteri. Usa AES a 256 bit o una password più corta."))
        elif max(len(self.open_password.encode()), len(self.permissions_password.encode())) > 127:
            raise ProtectionError(_("La password è troppo lunga (massimo 127 byte)."))

    def save_options(self) -> dict:
        """Opzioni per `Document.save_options` (dopo `validate()`)."""
        self.validate()
        # senza password dei permessi distinta, il proprietario usa quella di apertura
        owner = self.permissions_password or self.open_password or secrets.token_urlsafe(24)
        return {
            "encryption": METHODS[self.method],
            "user_pw": self.open_password,
            "owner_pw": owner,
            "permissions": self.permission_value(),
        }


def strength(password: str) -> int:
    """Robustezza indicativa 0–4 (vuota, debole, discreta, buona, ottima)."""
    if not password:
        return 0
    classes = sum(bool(any(f(c) for c in password)) for f in (str.islower, str.isupper, str.isdigit))
    classes += any(not c.isalnum() for c in password)
    score = 1
    if len(password) >= 8 and classes >= 2:
        score = 2
    if len(password) >= 10 and classes >= 3:
        score = 3
    if len(password) >= 14 and classes >= 3:
        score = 4
    return score


# ------------------------------------------------------------- stato attuale

_METHOD_BY_ENGINE = {v: k for k, v in METHODS.items()}
_METHOD_BY_ENGINE[pymupdf.PDF_ENCRYPT_RC4_40] = RC4


@dataclass
class ProtectionInfo:
    protected: bool
    has_open_password: bool
    method: str | None
    permissions: int
    restricted: bool
    changed: bool          # diverso dalla protezione con cui il file è stato aperto

    def allowed(self) -> set[str]:
        return {k for k, bit in PERM_BITS.items() if self.permissions & bit}

    @property
    def print_level(self) -> str:
        if not self.permissions & pymupdf.PDF_PERM_PRINT:
            return PRINT_NONE
        return PRINT_HIGH if self.permissions & pymupdf.PDF_PERM_PRINT_HQ else PRINT_LOW


def info(document: Document) -> ProtectionInfo:
    opts = document.save_options
    protected = document.is_protected
    perms = int(opts.get("permissions", -1)) if protected else -1
    return ProtectionInfo(
        protected=protected,
        has_open_password=protected and bool(opts.get("user_pw")),
        method=_METHOD_BY_ENGINE.get(opts.get("encryption")) if protected else None,
        permissions=perms,
        restricted=protected and (perms & ALL_BITS) != ALL_BITS,
        changed=_options_key(opts) != _options_key(document.original_protection),
    )


def _options_key(opts: dict) -> tuple:
    enc = opts.get("encryption", pymupdf.PDF_ENCRYPT_NONE)
    if enc in (pymupdf.PDF_ENCRYPT_NONE, pymupdf.PDF_ENCRYPT_KEEP):
        return ()
    return enc, opts.get("user_pw"), opts.get("owner_pw"), opts.get("permissions")


def describe(document: Document) -> str:
    """Riepilogo leggibile della protezione che verrà applicata al salvataggio."""
    i = info(document)
    if not i.protected:
        return _("Nessuna protezione")
    parts = []
    parts.append(_("password di apertura") if i.has_open_password else _("nessuna password di apertura"))
    parts.append(_("permessi limitati") if i.restricted else _("tutti i permessi"))
    parts.append(method_labels().get(i.method or "", ""))
    return ", ".join(p for p in parts if p)


def settings_from_document(document: Document) -> ProtectionSettings:
    """Impostazioni iniziali del dialogo: quelle in vigore (password escluse)."""
    i = info(document)
    if not i.protected:
        return ProtectionSettings()
    return ProtectionSettings(restrict=i.restricted, allowed=i.allowed(), print_level=i.print_level,
                              method=i.method or AES256)


# ------------------------------------------------- password del proprietario


def original_restricted(document: Document) -> bool:
    orig = document.original_protection
    if not orig:
        return False
    return (int(orig.get("permissions", -1)) & ALL_BITS) != ALL_BITS


_unlocked: "weakref.WeakSet[Document]" = weakref.WeakSet()
_open_descriptions: "weakref.WeakKeyDictionary[Document, str]" = weakref.WeakKeyDictionary()


def remember_open_state(document: Document) -> None:
    """Annota la cifratura del file all'apertura (il nucleo non la conserva)."""
    if document in _open_descriptions:
        return
    try:
        with ENGINE_LOCK:
            desc = (document.pdf.metadata or {}).get("encryption") or ""
    except Exception:
        desc = ""
    _open_descriptions[document] = desc


def open_description(document: Document) -> str:
    """Descrizione della cifratura del file aperto (es. «Standard V5 R6 256-bit AES»)."""
    desc = _open_descriptions.get(document)
    if desc is None:
        remember_open_state(document)
        desc = _open_descriptions.get(document, "")
    return desc


def unlock(document: Document, password: str) -> bool:
    """«Sblocca…»: con la password dei permessi corretta si ottiene l'accesso completo."""
    if owner_known(document):
        return True
    if verify_owner_password(document, password):
        _unlocked.add(document)
        return True
    return False


def grant_full_access(document: Document) -> None:
    _unlocked.add(document)


def owner_known(document: Document) -> bool:
    """True se il documento è stato aperto con diritti completi (o non era protetto)."""
    if document in _unlocked:
        return True
    orig = document.original_protection
    if not orig or not original_restricted(document):
        return True
    return bool(document.password) and orig.get("owner_pw") == document.password


def allowed_now(document: Document) -> set[str]:
    """Permessi effettivi nella sessione (tutti con accesso completo)."""
    if owner_known(document):
        return set(PERM_BITS) | {"print"}
    perms = int(document.original_protection.get("permissions", -1))
    out = {k for k, bit in PERM_BITS.items() if perms & bit}
    if perms & pymupdf.PDF_PERM_PRINT:
        out.add("print")
    return out


def needs_owner_password(document: Document) -> bool:
    """Per cambiare o togliere la protezione di un file con permessi limitati
    aperto senza la password dei permessi, serve conoscerla."""
    return not owner_known(document)


def verify_owner_password(document: Document, password: str) -> bool:
    """Verifica la password dei permessi sul file originale su disco."""
    if not password:
        return False
    if document.path is None or not Path(document.path).exists():
        return False
    try:
        data = Path(document.path).read_bytes()
    except OSError:
        return False
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return False
        try:
            return bool(pdf.authenticate(password) & 4)
        finally:
            pdf.close()


# ---------------------------------------------------- annulla/ripeti opzioni


class _OptionsHistory:
    """Associa a ogni stato del documento le sue opzioni di salvataggio."""

    def __init__(self, document: Document):
        self.by_state: dict[int, dict] = {}
        self.record(document)
        document.add_listener(self)

    @staticmethod
    def _state(document: Document) -> int:
        return getattr(document, "_state", 0)

    def record(self, document: Document) -> None:
        self.by_state.setdefault(self._state(document), dict(document.save_options))

    def __call__(self, document: Document, kind: str) -> None:
        if kind != CONTENT:
            return
        state = self._state(document)
        if state in self.by_state:
            if document.save_options != self.by_state[state]:
                document.save_options = dict(self.by_state[state])
        else:
            self.by_state[state] = dict(document.save_options)


_histories: "weakref.WeakKeyDictionary[Document, _OptionsHistory]" = weakref.WeakKeyDictionary()


def track(document: Document) -> None:
    """Attiva il riallineamento di `save_options` su annulla/ripeti."""
    hist = _histories.get(document)
    if hist is None:
        _histories[document] = _OptionsHistory(document)
    else:
        hist.record(document)


def _set_options(document: Document, options: dict, label: str) -> None:
    track(document)
    with document.change(label):
        document.save_options = dict(options)
    # lo stato nuovo è registrato dal listener a fine blocco


def apply_protection(document: Document, settings: ProtectionSettings) -> None:
    """Imposta la protezione che verrà applicata al salvataggio (annullabile)."""
    opts = settings.save_options()
    track(document)
    with document.change(_("Imposta protezione")) as pdf:
        document.save_options = dict(opts)
        if settings.method == AES256:
            _declare_aes256(pdf)
    grant_full_access(document)


def _declare_aes256(pdf: pymupdf.Document) -> None:
    # AES-256 (R6) richiede PDF 2.0 oppure 1.7 con l'estensione ADBE di livello 8
    cat = pdf.pdf_catalog()
    kind, _v = pdf.xref_get_key(cat, "Extensions/ADBE")
    if kind == "null":
        pdf.xref_set_key(cat, "Extensions/ADBE", "<</BaseVersion/1.7/ExtensionLevel 8>>")


def remove_protection(document: Document) -> None:
    """Toglie ogni protezione: il file verrà salvato senza cifratura (annullabile)."""
    _set_options(document, {"encryption": pymupdf.PDF_ENCRYPT_NONE}, _("Rimuovi protezione"))
    grant_full_access(document)


def restore_original(document: Document) -> None:
    """Torna alla protezione con cui il file è stato aperto."""
    _set_options(document, dict(document.original_protection), _("Ripristina protezione originale"))
