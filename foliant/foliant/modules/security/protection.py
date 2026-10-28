"""Protezione con password: impostazioni, verifica, applicazione e rimozione.

Logica pura. La protezione non cambia il contenuto del documento: vale al
salvataggio tramite `document.save_options`. Il cambio avviene comunque dentro
`document.change()`: il nucleo include `save_options` nei passi di
annulla/ripeti, quindi anche la protezione si annulla e si ripete.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import pymupdf

from foliant.api import Document, _

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
    encrypt_metadata: bool = True               # False: «Cifra tutto tranne i metadati» (SEC-06)
    integrity: bool = False                     # verifica di integrità ISO/TS 32004 (SEC-11)

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
        opts = {
            "encryption": METHODS[self.method],
            "user_pw": self.open_password,
            "owner_pw": owner,
            "permissions": self.permission_value(),
        }
        if not self.encrypt_metadata and self.method == AES256:
            opts["encrypt_metadata"] = False     # chiave prevista dal nucleo; la scrive il modulo
        return opts


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
    if document.save_options.get("encryption") == pymupdf.PDF_ENCRYPT_RC4_40:
        parts.append(_("RC4, chiave a 40 bit"))
    else:
        parts.append(method_labels().get(i.method or "", ""))
    return ", ".join(p for p in parts if p)


def settings_from_document(document: Document) -> ProtectionSettings:
    """Impostazioni iniziali del dialogo: quelle in vigore (password escluse)."""
    i = info(document)
    if not i.protected:
        return ProtectionSettings()
    return ProtectionSettings(restrict=i.restricted, allowed=i.allowed(), print_level=i.print_level,
                              method=i.method or AES256,
                              encrypt_metadata=document.save_options.get("encrypt_metadata", True) is not False)


# ------------------------------------------------- accesso (API del nucleo)
#
# Il nucleo conosce l'esito dell'autenticazione (`document.access`), i permessi
# effettivi (`document.permissions`/`allows`), la cifratura del file
# (`document.encryption_info`) e lo sblocco con la password dei permessi
# (`document.unlock`): qui restano solo nomi comodi per il modulo.

ACCESS_USER = "user"


def owner_known(document: Document) -> bool:
    """True con accesso completo: file non cifrato o aperto dal proprietario."""
    return getattr(document, "access", "none") != ACCESS_USER


def needs_owner_password(document: Document) -> bool:
    """Con accesso utente, per cambiare o togliere la protezione serve la password dei permessi."""
    return not owner_known(document)


def allowed_now(document: Document) -> set[str]:
    """Permessi effettivi nella sessione, con i nomi del modulo («modify», «copy», …, «print»)."""
    out = {k for k, bit in PERM_BITS.items() if document.allows(bit)}
    if document.allows(pymupdf.PDF_PERM_PRINT):
        out.add("print")
    return out


def unlock(document: Document, password: str) -> bool:
    """«Sblocca…»: accesso completo con la password dei permessi (verificata dal nucleo)."""
    return bool(document.unlock(password))


def encryption(document: Document) -> dict:
    """Cifratura del file all'apertura (`document.encryption_info`; {} se non cifrato)."""
    return dict(getattr(document, "encryption_info", {}) or {})


def _set_options(document: Document, options: dict, label: str) -> None:
    with document.change(label):
        document.save_options = dict(options)
    # lo stato nuovo è registrato dal listener a fine blocco


def apply_protection(document: Document, settings: ProtectionSettings) -> None:
    """Imposta la protezione che verrà applicata al salvataggio (annullabile)."""
    opts = settings.save_options()
    with document.change(_("Imposta protezione")) as pdf:
        document.save_options = dict(opts)
        if settings.method == AES256:
            _declare_aes256(pdf)


def _declare_aes256(pdf: pymupdf.Document) -> None:
    # AES-256 (R6) richiede PDF 2.0 oppure 1.7 con l'estensione ADBE di livello 8
    cat = pdf.pdf_catalog()
    kind, _v = pdf.xref_get_key(cat, "Extensions/ADBE")
    if kind == "null":
        pdf.xref_set_key(cat, "Extensions/ADBE", "<</BaseVersion/1.7/ExtensionLevel 8>>")


def remove_protection(document: Document) -> None:
    """Toglie ogni protezione: il file verrà salvato senza cifratura (annullabile)."""
    _set_options(document, {"encryption": pymupdf.PDF_ENCRYPT_NONE}, _("Rimuovi protezione"))


def restore_original(document: Document) -> None:
    """Torna alla protezione con cui il file è stato aperto."""
    _set_options(document, dict(document.original_protection), _("Ripristina protezione originale"))
