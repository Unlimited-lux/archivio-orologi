"""Protezione del documento: tipo di accesso, permessi effettivi e descrizione della cifratura.

Il motore non conserva l'esito dell'autenticazione e non confronta /P con /Perms
(revisione 6): questi dati si calcolano all'apertura e restano nel `Document`
anche quando il documento interno viene sostituito (annulla/ripeti, sostituzioni).

Permessi (ISO 32000-2, tabella 22; SEC-02): `/P` è un intero a 32 bit con segno.
Con accesso da proprietario, o per un file non cifrato, tutto è consentito (-1).
"""

from __future__ import annotations

import hashlib
import re

import pymupdf

ACCESS_NONE = "none"      # file non cifrato
ACCESS_USER = "user"      # aperto con la password di apertura (o senza, se vuota): valgono i permessi
ACCESS_OWNER = "owner"    # aperto o sbloccato con la password dei permessi: tutto consentito

ALL_PERMISSIONS = -1

# Permessi che ne implicano altri (SEC-02): i commenti comprendono la compilazione dei
# campi; la modifica del contenuto comprende l'assemblaggio; l'estrazione per
# l'accessibilità è sempre consentita (PDF 2.0 impone di ignorare il bit 10).
_IMPLIED_BY = {
    pymupdf.PDF_PERM_FORM: pymupdf.PDF_PERM_ANNOTATE,
    pymupdf.PDF_PERM_ASSEMBLE: pymupdf.PDF_PERM_MODIFY,
}


def is_encrypted(pdf: pymupdf.Document) -> bool:
    """Il documento del motore ha un dizionario /Encrypt (anche se già autenticato)."""
    try:
        return pdf.xref_get_key(-1, "Encrypt")[0] != "null"
    except Exception:
        return False


def allows(permissions: int, perm: int) -> bool:
    """True se tutti i bit `PDF_PERM_*` di `perm` sono consentiti da `permissions`."""
    if permissions == ALL_PERMISSIONS:
        return True
    for bit in range(32):
        flag = 1 << bit
        if not perm & flag:
            continue
        if flag == pymupdf.PDF_PERM_ACCESSIBILITY or permissions & flag:
            continue
        implied = _IMPLIED_BY.get(flag)
        if implied is not None and permissions & implied:
            continue
        return False
    if perm & pymupdf.PDF_PERM_PRINT_HQ and not permissions & pymupdf.PDF_PERM_PRINT:
        return False
    return True


def _key(pdf: pymupdf.Document, name: str) -> tuple[str, str]:
    try:
        return pdf.xref_get_key(-1, "Encrypt/" + name)
    except Exception:
        return "null", "null"


def _int(pdf: pymupdf.Document, name: str) -> int | None:
    kind, val = _key(pdf, name)
    if kind in ("int", "real"):
        try:
            return int(float(val))
        except ValueError:
            return None
    return None


def _name(pdf: pymupdf.Document, name: str) -> str:
    kind, val = _key(pdf, name)
    return val.lstrip("/") if kind == "name" else ""


def encrypt_dict_text(pdf: pymupdf.Document) -> str:
    """Testo del dizionario /Encrypt (stringhe binarie in esadecimale), vuoto se assente."""
    try:
        kind, val = pdf.xref_get_key(-1, "Encrypt")
        if kind == "xref":
            return pdf.xref_object(int(val.split()[0]), compressed=True)
        return val if kind == "dict" else ""
    except Exception:
        return ""


def encryption_info(pdf: pymupdf.Document) -> dict:
    """Descrizione della cifratura del file ({} se non cifrato).

    Chiavi: `method` («AES-256», «AES-128», «RC4-128», «RC4-40», «nessuno», o il nome
    del filtro), `engine` (costante `PDF_ENCRYPT_*` corrispondente), `filter`, `V`, `R`,
    `length` (bit della chiave), `encrypt_metadata`, `P` (valore di /P), `cfm` (metodo del
    filtro di cifratura dei flussi), `description` (testo del motore, es. «Standard V5 R6
    256-bit AES»).
    """
    if not is_encrypted(pdf):
        return {}
    v = _int(pdf, "V") or 0
    r = _int(pdf, "R") or 0
    stmf = _name(pdf, "StmF")
    cfm = _name(pdf, f"CF/{stmf}/CFM") if stmf and stmf != "Identity" else ""
    length = _int(pdf, "Length")
    if cfm == "AESV3" or v == 5:
        bits, algo = 256, "AES"
    elif cfm == "AESV2":
        bits, algo = 128, "AES"
    elif cfm == "None" or (v == 4 and stmf == "Identity"):
        bits, algo = 0, ""
    else:
        algo = "RC4"
        if v == 1:
            bits = 40
        elif length:
            bits = length if length > 32 else length * 8
        else:
            cf_len = _int(pdf, f"CF/{stmf}/Length") if stmf else None
            bits = (cf_len * 8 if cf_len and cf_len <= 32 else cf_len) or 40
    method = f"{algo}-{bits}" if algo else "nessuno"
    engine = {"AES-256": pymupdf.PDF_ENCRYPT_AES_256, "AES-128": pymupdf.PDF_ENCRYPT_AES_128,
              "RC4-128": pymupdf.PDF_ENCRYPT_RC4_128, "RC4-40": pymupdf.PDF_ENCRYPT_RC4_40}.get(
        method, pymupdf.PDF_ENCRYPT_UNKNOWN)
    filt = _name(pdf, "Filter")
    kind, em = _key(pdf, "EncryptMetadata")
    try:
        description = (pdf.metadata or {}).get("encryption") or ""
    except Exception:
        description = ""
    if not description:
        description = f"{filt} V{v} R{r} {bits}-bit {algo}".strip()
    return {
        "method": method if filt in ("Standard", "") else filt,
        "engine": engine,
        "filter": filt,
        "V": v,
        "R": r,
        "length": bits,
        "encrypt_metadata": not (kind == "bool" and em == "false"),
        "P": _int(pdf, "P"),
        "cfm": cfm,
        "description": description,
    }


# ---------------------------------------------------------------- /Perms (R6)

def _pdf_string(text: str, key: str) -> bytes | None:
    """Valore binario della stringa `/key` nel testo di un dizionario (esadecimale o letterale)."""
    m = re.search(rf"/{key}\s*(<|\()", text)
    if not m:
        return None
    i = m.end()
    if m.group(1) == "<":
        end = text.find(">", i)
        if end < 0:
            return None
        digits = re.sub(r"\s+", "", text[i:end])
        if len(digits) % 2:
            digits += "0"
        try:
            return bytes.fromhex(digits)
        except ValueError:
            return None
    out = bytearray()
    depth = 1
    escapes = {"n": 10, "r": 13, "t": 9, "b": 8, "f": 12}
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in escapes:
                out.append(escapes[n]); i += 2
            elif n.isdigit():
                j = i + 1
                while j < len(text) and j < i + 4 and text[j] in "01234567":
                    j += 1
                out.append(int(text[i + 1:j], 8) & 0xFF); i = j
            elif n in "\r\n":
                i += 2
            else:
                out.append(ord(n) & 0xFF); i += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return bytes(out)
        out.append(ord(c) & 0xFF)
        i += 1
    return None


def _aes(key: bytes, data: bytes, *, iv: bytes | None, decrypt: bool) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    mode = modes.CBC(iv) if iv is not None else modes.ECB()
    c = Cipher(algorithms.AES(key), mode)
    op = c.decryptor() if decrypt else c.encryptor()
    return op.update(data) + op.finalize()


def _hash_r6(password: bytes, salt: bytes, udata: bytes, revision: int) -> bytes:
    """Algoritmo 2.B (ISO 32000-2 §7.6.4.3.4); per R5 un semplice SHA-256."""
    k = hashlib.sha256(password + salt + udata).digest()
    if revision < 6:
        return k
    i = 0
    while True:
        e = _aes(k[:16], (password + k + udata) * 64, iv=k[16:32], decrypt=False)
        k = (hashlib.sha256, hashlib.sha384, hashlib.sha512)[sum(e[:16]) % 3](e).digest()
        i += 1
        if i >= 64 and e[-1] <= i - 32:
            return k[:32]


def perms_value(pdf: pymupdf.Document, password: str) -> tuple[int, bool] | None:
    """Decifra /Perms (V5) con la chiave del file: (permessi, flag EncryptMetadata).

    Restituisce None se il file non ha /Perms, la password non è valida o manca la
    libreria crittografica; solleva ValueError se /Perms non si decifra correttamente
    (permessi alterati).
    """
    text = encrypt_dict_text(pdf)
    if "/Perms" not in text or _int(pdf, "V") != 5:
        return None
    o, u = _pdf_string(text, "O"), _pdf_string(text, "U")
    oe, ue, perms = _pdf_string(text, "OE"), _pdf_string(text, "UE"), _pdf_string(text, "Perms")
    if not (o and u and oe and ue and perms) or len(o) < 48 or len(u) < 48 or len(perms) < 16:
        return None
    revision = _int(pdf, "R") or 6
    pw = (password or "").encode("utf-8")[:127]
    try:
        if _hash_r6(pw, o[32:40], u[:48], revision) == o[:32]:
            key = _aes(_hash_r6(pw, o[40:48], u[:48], revision), oe[:32], iv=bytes(16), decrypt=True)
        elif _hash_r6(pw, u[32:40], b"", revision) == u[:32]:
            key = _aes(_hash_r6(pw, u[40:48], b"", revision), ue[:32], iv=bytes(16), decrypt=True)
        else:
            return None
        plain = _aes(key, perms[:16], iv=None, decrypt=True)
    except ImportError:
        return None
    if plain[9:12] != b"adb" or plain[8:9] not in (b"T", b"F"):
        raise ValueError("Perms")
    return int.from_bytes(plain[:4], "little", signed=True), plain[8:9] == b"T"


PERMISSION_BITS = 0xF3C    # bit 3, 4, 5, 6, 9, 10, 11, 12: tutti a 1 = tutto consentito


def _revision2(p: int) -> int:
    """Revisione 2: i bit 9–12 non esistono e seguono i bit 3, 4 e 6 (stampa fedele, assemblaggio, moduli)."""
    if p & pymupdf.PDF_PERM_PRINT:
        p |= pymupdf.PDF_PERM_PRINT_HQ
    if p & pymupdf.PDF_PERM_MODIFY:
        p |= pymupdf.PDF_PERM_ASSEMBLE
    if p & pymupdf.PDF_PERM_ANNOTATE:
        p |= pymupdf.PDF_PERM_FORM
    return p | pymupdf.PDF_PERM_ACCESSIBILITY


def effective_permissions(pdf: pymupdf.Document, password: str | None) -> tuple[int, bool]:
    """(permessi /P effettivi con accesso utente, permessi alterati).

    Per i file R6 si applica l'AND bit a bit di /P e /Perms (SEC-02): se non coincidono,
    o se /Perms non si decifra, i permessi risultano alterati. Con tutti i permessi
    consentiti il risultato è -1.
    """
    p = _int(pdf, "P")
    if p is None:
        try:
            p = int(pdf.permissions)
        except Exception:
            p = 0
    if (_int(pdf, "R") or 0) == 2:
        p = _revision2(p)
    tampered = False
    try:
        checked = perms_value(pdf, password or "")
    except ValueError:
        checked, tampered = None, True
    if checked is not None:
        perms, _em = checked
        tampered = (p & 0xFFFFFFFF) != (perms & 0xFFFFFFFF)
        p &= perms
    if (p & PERMISSION_BITS) == PERMISSION_BITS:
        p = ALL_PERMISSIONS
    return p, tampered
