"""Firma con smart card e token crittografici PKCS#11 (SIG-23).

La chiave privata non lascia mai il dispositivo: pyHanko (`PKCS11Signer`) chiede al dispositivo l'operazione
di firma. I moduli PKCS#11 si rilevano fra i percorsi noti del sistema (p11-kit, OpenSC, moduli delle
carte di firma più diffuse in Italia) e si possono aggiungere a mano; l'elenco dei moduli aggiunti è in
`tokens.json` nella cartella dati (solo percorsi, mai PIN).

Un ID su dispositivo è identificato da un URI interno «pkcs11:<modulo>|<seriale token>|<id certificato>»,
usato come dato della voce nel dialogo di firma al posto del percorso del file .p12.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foliant.i18n import _

from .digital import CertInfo, DigitalID, SignError, WrongCertificatePassword, cert_info_from_der
from .store import _read_json, _sign_dir, _write_json

URI_PREFIX = "pkcs11:"

# percorsi tipici dei moduli PKCS#11 su Linux (il primo esistente per nome viene proposto)
KNOWN_MODULES = [
    "/usr/lib/x86_64-linux-gnu/p11-kit-proxy.so", "/usr/lib64/p11-kit-proxy.so", "/usr/lib/p11-kit-proxy.so",
    "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so", "/usr/lib64/opensc-pkcs11.so", "/usr/lib/opensc-pkcs11.so",
    "/usr/lib/libbit4xpki.so", "/usr/lib/libbit4ipki.so", "/usr/lib/libbit4opki.so",      # Bit4id (carte CNS/firma)
    "/usr/lib/libASEP11.so", "/usr/local/lib/libASEP11.so",                               # Athena / InfoCert
    "/usr/lib/libcvP11.so", "/usr/lib/libeTPkcs11.so", "/usr/lib/libIDPrimePKCS11.so",
]


class TokenError(SignError):
    pass


class PinLocked(TokenError):
    pass


@dataclass
class TokenCert:
    module: str
    token_label: str
    token_serial: str
    cert_id: str            # CKA_ID in esadecimale
    label: str
    info: CertInfo

    @property
    def uri(self) -> str:
        return f"{URI_PREFIX}{self.module}|{self.token_serial}|{self.cert_id}"

    @property
    def display(self) -> str:
        return f"{self.info.subject} — {self.token_label}"


def is_token_uri(value) -> bool:
    return isinstance(value, str) and value.startswith(URI_PREFIX)


def parse_uri(uri: str) -> tuple[str, str, str]:
    module, serial, cid = uri[len(URI_PREFIX):].rsplit("|", 2)
    return module, serial, cid


# ---------------------------------------------------------------- moduli
def _config_path() -> Path:
    return _sign_dir() / "tokens.json"


def added_modules() -> list[str]:
    raw = _read_json(_config_path(), {"modules": []})
    return [m for m in (raw.get("modules", []) if isinstance(raw, dict) else []) if isinstance(m, str)]


def add_module(path: str) -> str:
    p = str(Path(path).resolve())
    if not Path(p).is_file():
        raise TokenError(_("Modulo PKCS#11 non trovato: {p}").format(p=path))
    load_module(p)      # verifica subito che si carichi
    mods = [m for m in added_modules() if m != p] + [p]
    _write_json(_config_path(), {"version": 1, "modules": mods})
    return p


def remove_module(path: str) -> None:
    _write_json(_config_path(), {"version": 1, "modules": [m for m in added_modules() if m != path]})


def modules() -> list[str]:
    """Moduli da interrogare: quelli aggiunti dall'utente e quelli noti presenti sul sistema."""
    out = []
    for m in added_modules() + [k for k in KNOWN_MODULES if Path(k).is_file()]:
        if m not in out:
            out.append(m)
    return out


_LIBS: dict[str, object] = {}


def load_module(path: str):
    """Carica un modulo PKCS#11. python-pkcs11 tiene un solo modulo per processo: per usare più
    dispositivi di produttori diversi conviene il modulo p11-kit-proxy, che li riunisce tutti."""
    import pkcs11
    path = str(Path(path).resolve())
    if path in _LIBS:
        return _LIBS[path]
    # python-pkcs11 riconosce un modulo già caricato solo dal percorso scritto uguale
    same = [k for k in getattr(pkcs11, "_loaded", {}) if str(Path(k).resolve()) == path]
    try:
        lib = pkcs11.lib(same[0] if same else path)
    except Exception as e:  # noqa: BLE001 - modulo non caricabile o incompatibile
        raise TokenError(_("Impossibile caricare il modulo PKCS#11 {p}: {e}").format(
            p=path, e=str(e) or type(e).__name__)) from e
    _LIBS[path] = lib
    return lib


# ------------------------------------------------------------ certificati
def _non_repudiation_first(c: TokenCert) -> tuple:
    return ("nonRepudiation" not in c.info.key_usage, c.info.subject.lower())


def list_certificates(module_paths=None) -> tuple[list[TokenCert], list[str]]:
    """Certificati presenti sui dispositivi inseriti (senza PIN: i certificati sono pubblici).
    Restituisce (certificati, errori): un modulo difettoso non impedisce di usare gli altri."""
    from pkcs11 import Attribute, ObjectClass, TokenFlag
    out: list[TokenCert] = []
    errors: list[str] = []
    for path in module_paths if module_paths is not None else modules():
        try:
            lib = load_module(path)
            slots = lib.get_slots(token_present=True)
        except TokenError as e:
            errors.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(_("Modulo {p}: {e}").format(p=path, e=str(e) or type(e).__name__))
            continue
        for slot in slots:
            try:
                tok = slot.get_token()
                if not tok.flags & TokenFlag.TOKEN_INITIALIZED:    # slot libero (es. SoftHSM)
                    continue
                with tok.open() as session:
                    for obj in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
                        der = bytes(obj[Attribute.VALUE])
                        cid = bytes(obj[Attribute.ID] or b"").hex()
                        try:
                            label = obj[Attribute.LABEL] or ""
                        except Exception:  # noqa: BLE001
                            label = ""
                        out.append(TokenCert(module=path, token_label=tok.label.strip(),
                                             token_serial=tok.serial.decode("ascii", "replace").strip()
                                             if isinstance(tok.serial, bytes) else str(tok.serial).strip(),
                                             cert_id=cid, label=label, info=cert_info_from_der(der)))
            except Exception as e:  # noqa: BLE001 - dispositivo rimosso o non leggibile
                errors.append(_("Dispositivo nel modulo {p} non leggibile: {e}").format(
                    p=Path(path).name, e=str(e) or type(e).__name__))
    out.sort(key=_non_repudiation_first)
    return out, errors


def find_certificate(uri: str) -> TokenCert:
    module, serial, cid = parse_uri(uri)
    certs, _errors = list_certificates([module])
    for c in certs:
        if c.token_serial == serial and c.cert_id == cid:
            return c
    raise TokenError(_("Dispositivo non inserito o certificato non trovato."))


# ------------------------------------------------------------------ firma
class TokenDigitalID(DigitalID):
    """ID su dispositivo: la sessione resta aperta fino a `close()` (dopo la firma)."""

    def __init__(self, signer, info: CertInfo, session, uri: str):
        super().__init__(signer, info, [], path=uri)
        self._session = session

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass


def _pin_error(exc, token) -> TokenError:
    import pkcs11.exceptions as px
    if isinstance(exc, px.PinLocked):
        return PinLocked(_("PIN bloccato: il dispositivo non accetta altri tentativi. Sbloccalo con il PUK."))
    if isinstance(exc, (px.PinIncorrect, px.PinLenRange, px.PinInvalid)):
        from pkcs11 import TokenFlag
        flags = getattr(token, "flags", 0)
        msg = _("PIN errato")
        if flags & TokenFlag.USER_PIN_FINAL_TRY:
            msg += " — " + _("ultimo tentativo prima del blocco")
        elif flags & TokenFlag.USER_PIN_COUNT_LOW:
            msg += " — " + _("pochi tentativi residui")
        return WrongCertificatePassword(msg)
    return TokenError(_("Errore del dispositivo: {e}").format(e=str(exc) or type(exc).__name__))


def open_digital_id(uri: str, pin: str | None) -> TokenDigitalID:
    """Apre la sessione con il PIN e prepara il firmatario PKCS#11. PIN None: tastierino del lettore."""
    import pkcs11
    from pyhanko.sign.pkcs11 import PKCS11Signer
    module, serial, cid = parse_uri(uri)
    lib = load_module(module)
    token = None
    for slot in lib.get_slots(token_present=True):
        t = slot.get_token()
        s = t.serial.decode("ascii", "replace").strip() if isinstance(t.serial, bytes) else str(t.serial).strip()
        if s == serial:
            token = t
            break
    if token is None:
        raise TokenError(_("Dispositivo non inserito o certificato non trovato."))
    try:
        session = token.open(user_pin=pin if pin is not None else pkcs11.PROTECTED_AUTH)
    except Exception as e:  # noqa: BLE001
        raise _pin_error(e, token) from e
    try:
        signer = PKCS11Signer(session, cert_id=bytes.fromhex(cid), key_id=bytes.fromhex(cid))
        if signer.signing_cert.public_key.algorithm == "ec":
            # molti dispositivi offrono solo ECDSA «grezzo»: l'impronta si calcola qui, la firma sul dispositivo
            signer = PKCS11Signer(session, cert_id=bytes.fromhex(cid), key_id=bytes.fromhex(cid),
                                  use_raw_mechanism=True)
        info = cert_info_from_der(signer.signing_cert.dump())
    except Exception as e:  # noqa: BLE001
        session.close()
        raise TokenError(_("Chiave privata o certificato non disponibili sul dispositivo: {e}").format(
            e=str(e) or type(e).__name__)) from e
    return TokenDigitalID(signer, info, session, uri)


TRANSLATIONS_EN = {
    "Modulo PKCS#11 non trovato: {p}": "PKCS#11 module not found: {p}",
    "Impossibile caricare il modulo PKCS#11 {p}: {e}": "Cannot load the PKCS#11 module {p}: {e}",
    "Modulo {p}: {e}": "Module {p}: {e}",
    "Dispositivo nel modulo {p} non leggibile: {e}": "Device in module {p} unreadable: {e}",
    "Dispositivo non inserito o certificato non trovato.": "Device not inserted or certificate not found.",
    "PIN bloccato: il dispositivo non accetta altri tentativi. Sbloccalo con il PUK.":
        "PIN locked: the device accepts no more attempts. Unlock it with the PUK.",
    "PIN errato": "Wrong PIN", "ultimo tentativo prima del blocco": "last attempt before locking",
    "pochi tentativi residui": "few attempts left", "Errore del dispositivo: {e}": "Device error: {e}",
    "Chiave privata o certificato non disponibili sul dispositivo: {e}":
        "Private key or certificate not available on the device: {e}",
}
