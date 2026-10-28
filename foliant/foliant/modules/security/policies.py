"""Policy di protezione riutilizzabili (SEC-12).

Una policy è una combinazione di impostazioni di protezione con un nome: richiesta della
password di apertura, permessi e qualità di stampa. Le password non fanno mai parte della
policy: si chiedono quando la si applica. La cifratura è sempre AES-256 (SEC-06).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import pymupdf

from foliant.api import _

from . import protection as P

SETTINGS_KEY = "security/policies"
FILE_TAG = "foliant_protection_policies"


@dataclass
class Policy:
    name: str
    open_password: bool = False           # chiede una password per aprire il documento
    restrict: bool = True
    allowed: set[str] = field(default_factory=set)
    print_level: str = P.PRINT_HIGH
    encrypt_metadata: bool = True
    integrity: bool = False

    @classmethod
    def from_settings(cls, name: str, s: P.ProtectionSettings) -> Policy:
        return cls(name=name, open_password=bool(s.open_password), restrict=s.restrict,
                   allowed=set(s.allowed) if s.restrict else set(P.PERM_BITS), print_level=s.print_level,
                   encrypt_metadata=s.encrypt_metadata, integrity=s.integrity)

    def settings(self, open_password: str = "", permissions_password: str = "") -> P.ProtectionSettings:
        return P.ProtectionSettings(open_password=open_password if self.open_password else "",
                                    permissions_password=permissions_password if self.restrict else "",
                                    restrict=self.restrict, allowed=set(self.allowed),
                                    print_level=self.print_level, method=P.AES256,
                                    encrypt_metadata=self.encrypt_metadata, integrity=self.integrity)

    def to_dict(self) -> dict:
        return {"open_password": self.open_password, "restrict": self.restrict,
                "allowed": sorted(self.allowed), "print_level": self.print_level, "method": "aes256",
                "encrypt_metadata": self.encrypt_metadata, "integrity": self.integrity}

    @classmethod
    def from_dict(cls, name: str, data: dict) -> tuple[Policy, list[str]]:
        """Policy da un dizionario importato, con gli avvisi (es. metodo più vecchio)."""
        if not isinstance(data, dict):
            raise ValueError(_("La policy «{n}» non è valida.").format(n=name))
        notes = []
        method = str(data.get("method", "aes256")).lower()
        if method not in ("aes256", "aes-256", P.AES256.lower()):
            notes.append(_("La policy «{n}» indicava una cifratura più vecchia: sarà applicata con AES a 256 "
                           "bit.").format(n=name))
        allowed = {str(k) for k in data.get("allowed", []) if str(k) in P.PERM_BITS}
        level = data.get("print_level", P.PRINT_HIGH)
        if level not in (P.PRINT_NONE, P.PRINT_LOW, P.PRINT_HIGH):
            level = P.PRINT_HIGH
        return cls(name=name, open_password=bool(data.get("open_password")), restrict=bool(data.get("restrict")),
                   allowed=allowed, print_level=level, encrypt_metadata=data.get("encrypt_metadata", True) is not False,
                   integrity=bool(data.get("integrity"))), notes

    def describe(self) -> str:
        parts = [_("AES a 256 bit")]
        parts.append(_("password di apertura") if self.open_password else _("apertura libera"))
        if not self.encrypt_metadata:
            parts.append(_("metadati in chiaro"))
        if self.integrity:
            parts.append(_("verifica di integrità"))
        if self.restrict:
            prints = {P.PRINT_NONE: _("stampa negata"), P.PRINT_LOW: _("stampa a bassa risoluzione"),
                      P.PRINT_HIGH: _("stampa consentita")}[self.print_level]
            parts.append(prints)
            labels = P.permission_labels()
            denied = [labels[k] for k in P.PERM_BITS if k not in self.allowed and k != "accessibility"]
            if denied:
                parts.append(_("negati: {list}").format(list=", ".join(d.lower() for d in denied)))
        return " · ".join(parts)


class PolicyStore:
    """Policy salvate nelle impostazioni (JSON senza password)."""

    def __init__(self, settings=None):
        self.settings = settings
        self.items: dict[str, Policy] = {}
        raw = settings.value(SETTINGS_KEY, "") if settings is not None else ""
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        for name, d in (data.items() if isinstance(data, dict) else ()):
            try:
                self.items[str(name)] = Policy.from_dict(str(name), d)[0]
            except ValueError:
                continue

    def _store(self) -> None:
        if self.settings is not None:
            self.settings.setValue(SETTINGS_KEY, json.dumps({n: p.to_dict() for n, p in self.items.items()},
                                                            ensure_ascii=False))

    def names(self) -> list[str]:
        return sorted(self.items, key=str.casefold)

    def get(self, name: str) -> Policy | None:
        return self.items.get(name)

    def add(self, policy: Policy) -> None:
        name = policy.name.strip()
        if not name:
            raise ValueError(_("Dai un nome alla policy."))
        policy.name = name
        self.items[name] = policy
        self._store()

    def rename(self, old: str, new: str) -> None:
        new = new.strip()
        if not new:
            raise ValueError(_("Dai un nome alla policy."))
        if new != old and new in self.items:
            raise ValueError(_("Esiste già una policy «{n}».").format(n=new))
        policy = self.items.pop(old)
        policy.name = new
        self.items[new] = policy
        self._store()

    def duplicate(self, name: str) -> str:
        base = _("{n} (copia)").format(n=name)
        new, k = base, 2
        while new in self.items:
            new, k = f"{base} {k}", k + 1
        src = self.items[name]
        self.items[new] = Policy(new, src.open_password, src.restrict, set(src.allowed), src.print_level,
                                 src.encrypt_metadata, src.integrity)
        self._store()
        return new

    def remove(self, name: str) -> None:
        if self.items.pop(name, None) is not None:
            self._store()

    def export(self, path: str, names: list[str] | None = None) -> None:
        chosen = {n: self.items[n].to_dict() for n in (names or self.names()) if n in self.items}
        with open(path, "w", encoding="utf-8") as f:
            json.dump({FILE_TAG: 1, "policies": chosen}, f, ensure_ascii=False, indent=2)

    def import_file(self, path: str) -> tuple[list[str], list[str]]:
        """Importa le policy del file; restituisce (nomi importati, avvisi)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ValueError(_("Impossibile leggere il file: {e}").format(e=e)) from e
        items = data.get("policies") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            raise ValueError(_("Il file non contiene policy di protezione di Foliant."))
        done, notes = [], []
        for name, d in items.items():
            try:
                policy, warn = Policy.from_dict(str(name), d)
            except ValueError as e:
                notes.append(str(e))
                continue
            self.items[policy.name] = policy
            done.append(policy.name)
            notes += warn
        self._store()
        return done, notes


# ------------------------------------------------------ elaborazione in blocco


def protected_name(path: str, out_dir: str | None) -> str:
    folder, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    return os.path.join(out_dir or folder, f"{stem}{_('_protetto')}{ext or '.pdf'}")


def protect_file(path: str, out: str, settings: P.ProtectionSettings) -> None:
    """Salva in `out` una copia di `path` protetta con `settings`. Un file già protetto
    richiede l'accesso proprietario (SEC-07): qui viene rifiutato con un messaggio."""
    opts = settings.save_options()
    src = pymupdf.open(path)
    try:
        if src.needs_pass or src.is_encrypted:
            raise P.ProtectionError(_("Il file è già protetto: aprilo con la password dei permessi e "
                                      "proteggilo dalla finestra principale."))
        if settings.method == P.AES256:
            P._declare_aes256(src)
        if not settings.encrypt_metadata or settings.integrity:
            from . import crypto as X
            data = X.encrypt(src.tobytes(garbage=1, deflate=True), opts["user_pw"], opts["owner_pw"],
                             opts["permissions"], encrypt_metadata=settings.encrypt_metadata,
                             mac=settings.integrity)
            with open(out, "wb") as f:
                f.write(data)
            return
        opts.pop("encrypt_metadata", None)
        src.save(out, garbage=1, deflate=True, **opts)
    finally:
        src.close()
