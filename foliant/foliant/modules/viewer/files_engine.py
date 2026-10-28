"""Logica pura della gestione dei file locali (VIS-03), senza interfaccia.

Validazione dei nomi, nomi univoci, duplica, rinomina e sposta con errori
comprensibili per l'utente. Nessuna funzione tocca widget.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from foliant.i18n import _

MAX_NAME_BYTES = 255


class FileOpError(Exception):
    """Errore di un'operazione su file, con messaggio per l'utente."""


class NameConflict(FileOpError):
    """Esiste già un file con il nome di destinazione."""

    def __init__(self, target: str):
        super().__init__(_("Esiste già un file chiamato «{n}».").format(n=os.path.basename(target)))
        self.target = target


# ------------------------------------------------------------------- nomi
def validate_name(name: str) -> str:
    """Controlla un nome di file (solo il nome, senza cartella). Restituisce il nome
    ripulito dagli spazi esterni o solleva FileOpError con il motivo."""
    name = (name or "").strip()
    if not name:
        raise FileOpError(_("Il nome non può essere vuoto."))
    if name in (".", ".."):
        raise FileOpError(_("«{n}» non è un nome di file valido.").format(n=name))
    bad = [c for c in ("/", "\0") if c in name]
    if bad:
        shown = ", ".join("«/»" if c == "/" else _("carattere nullo") for c in bad)
        raise FileOpError(_("Il nome contiene caratteri non consentiti: {c}.").format(c=shown))
    size = len(name.encode("utf-8", "surrogateescape"))
    if size > MAX_NAME_BYTES:
        raise FileOpError(_("Il nome è troppo lungo ({n} byte, massimo {m}).").format(n=size, m=MAX_NAME_BYTES))
    return name


def with_suffix_of(name: str, original: str) -> str:
    """Se il nuovo nome non ha estensione, mantiene quella del file originale."""
    if not Path(name).suffix and Path(original).suffix:
        return name + Path(original).suffix
    return name


def unique_path(folder: str, stem: str, suffix: str, first: str = "{stem}{suffix}",
                numbered: str = "{stem} ({n}){suffix}") -> str:
    """Primo percorso libero in `folder` secondo i modelli `first` e poi `numbered` (n = 2, 3, …)."""
    candidate = os.path.join(folder, first.format(stem=stem, suffix=suffix))
    n = 2
    while os.path.lexists(candidate):
        candidate = os.path.join(folder, numbered.format(stem=stem, suffix=suffix, n=n))
        n += 1
    return candidate


def auto_rename_target(target: str) -> str:
    """«nome.pdf» già esistente → «nome (2).pdf», «nome (3).pdf», …"""
    p = Path(target)
    return unique_path(str(p.parent), p.stem, p.suffix)


def copy_name(path: str) -> str:
    """Percorso della copia: «nome (copia).pdf», poi «nome (copia 2).pdf», …"""
    p = Path(path)
    return unique_path(str(p.parent), p.stem, p.suffix,
                       first="{stem} (" + _("copia") + "){suffix}",
                       numbered="{stem} (" + _("copia") + " {n}){suffix}")


# --------------------------------------------------------------- permessi
def _describe_os_error(e: OSError, path: str, folder: str | None = None) -> str:
    folder = folder or os.path.dirname(path) or "."
    code = e.errno
    if code in (errno.EACCES, errno.EPERM):
        return _("Permesso di scrittura mancante sulla cartella «{d}».").format(d=folder)
    if code == errno.EROFS:
        return _("Il volume che contiene «{d}» è in sola lettura.").format(d=folder)
    if code == errno.ENOENT:
        return _("Il file «{n}» non esiste più.").format(n=os.path.basename(path))
    if code == errno.ENAMETOOLONG:
        return _("Il nome o il percorso è troppo lungo.")
    if code == errno.ENOSPC:
        return _("Spazio su disco insufficiente in «{d}».").format(d=folder)
    if code == errno.EISDIR:
        return _("«{n}» è una cartella.").format(n=os.path.basename(path))
    return _("Operazione non riuscita su «{n}»: {e}.").format(n=os.path.basename(path), e=e.strerror or e)


def check_writable_folder(folder: str) -> None:
    """Solleva FileOpError se nella cartella non si possono creare/rimuovere file."""
    if not os.path.isdir(folder):
        raise FileOpError(_("La cartella «{d}» non esiste.").format(d=folder))
    if not os.access(folder, os.W_OK | os.X_OK):
        raise FileOpError(_("Permesso di scrittura mancante sulla cartella «{d}».").format(d=folder))


def _check_source(path: str) -> None:
    if not os.path.isfile(path):
        raise FileOpError(_("Il file «{n}» non esiste più.").format(n=os.path.basename(path)))


# -------------------------------------------------------------- operazioni
def duplicate(path: str) -> str:
    """Copia byte-identica accanto all'originale; restituisce il percorso della copia."""
    _check_source(path)
    folder = os.path.dirname(os.path.abspath(path))
    check_writable_folder(folder)
    target = copy_name(path)
    try:
        with open(path, "rb") as src, open(target, "xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        shutil.copystat(path, target)
    except OSError as e:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise FileOpError(_describe_os_error(e, target, folder)) from e
    return target


def move(src: str, dst: str, overwrite: bool = False) -> str:
    """Rinomina o sposta `src` in `dst` (percorso completo). Con un file già presente in
    `dst` solleva NameConflict, salvo `overwrite`. In caso di errore il file resta intatto."""
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    _check_source(src)
    if src == dst:
        return dst
    validate_name(os.path.basename(dst))
    src_dir = os.path.dirname(src)
    dst_dir = os.path.dirname(dst)
    check_writable_folder(src_dir)
    check_writable_folder(dst_dir)
    if os.path.lexists(dst):
        if os.path.isdir(dst):
            raise FileOpError(_("Esiste già una cartella chiamata «{n}».").format(n=os.path.basename(dst)))
        if os.path.samefile(src, dst):
            # Stesso file (es. cambia solo maiuscole su un file system che non le distingue).
            pass
        elif not overwrite:
            raise NameConflict(dst)
    try:
        os.replace(src, dst)
        return dst
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise FileOpError(_describe_os_error(e, src, dst_dir if e.errno != errno.ENOENT else None)) from e
    # Altro volume: copia completa, poi rimozione dell'originale.
    tmp = dst + ".foliant-part"
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise FileOpError(_describe_os_error(e, dst, dst_dir)) from e
    try:
        os.unlink(src)
    except OSError as e:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise FileOpError(_describe_os_error(e, src, src_dir)) from e
    return dst


def rename_target(path: str, new_name: str) -> str:
    """Percorso di destinazione per rinominare `path` in `new_name` (nome validato,
    estensione originale mantenuta se omessa)."""
    name = with_suffix_of(validate_name(new_name), path)
    return os.path.join(os.path.dirname(os.path.abspath(path)), name)


def delete_permanently(path: str) -> None:
    _check_source(path)
    folder = os.path.dirname(os.path.abspath(path))
    check_writable_folder(folder)
    try:
        os.unlink(path)
    except OSError as e:
        raise FileOpError(_describe_os_error(e, path, folder)) from e


def replace_in_list(items: list[str], old: str, new: str) -> list[str]:
    """Sostituisce `old` con `new` mantenendo la posizione (senza duplicati)."""
    out: list[str] = []
    for p in items:
        q = new if p == old else p
        if q not in out:
            out.append(q)
    return out
