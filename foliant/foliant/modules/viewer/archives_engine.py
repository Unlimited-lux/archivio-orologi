"""Archivi ZIP (VIS-04): logica pura, senza interfaccia."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_EXTRACTED = 2 * 1024 * 1024 * 1024    # soglia predefinita: 2 GB
MAX_RATIO = 200                            # rapporto di compressione anomalo


class ArchiveError(Exception):
    """Errore con messaggio per l'utente."""


class PasswordNeeded(ArchiveError):
    pass


def compress(paths: list[str], target: str) -> None:
    """Crea un archivio ZIP (deflate) con i file indicati; nomi univoci, UTF-8."""
    names: set[str] = set()
    tmp_fd, tmp = tempfile.mkstemp(prefix=".foliant-zip-", suffix=".tmp", dir=os.path.dirname(target) or ".")
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                base = Path(p).name
                name, n = base, 1
                while name in names:
                    stem, ext = os.path.splitext(base)
                    name = f"{stem} ({n}){ext}"
                    n += 1
                names.add(name)
                zf.write(p, name)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _open(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArchiveError(f"L'archivio «{Path(path).name}» è danneggiato o illeggibile.") from e


def safe_name(name: str) -> str | None:
    """Percorso relativo sicuro dentro la destinazione (None se vuoto)."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/")]
    parts = [p for p in parts if not (len(p) == 2 and p[1] == ":")]   # lettere di unità
    return "/".join(parts) or None


def list_pdfs(path: str) -> list[str]:
    with _open(path) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".pdf")]


def is_encrypted(path: str) -> bool:
    with _open(path) as zf:
        return any(i.flag_bits & 0x1 for i in zf.infolist())


def read_member(path: str, name: str, password: str | None = None, limit: int = MAX_EXTRACTED) -> bytes:
    with _open(path) as zf:
        info = zf.getinfo(name)
        _check_sizes([info], limit)
        try:
            return zf.read(info, pwd=password.encode("utf-8") if password else None)
        except RuntimeError as e:
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise PasswordNeeded("L'archivio è protetto da password.") from e
            raise ArchiveError(str(e)) from e
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as e:
            raise ArchiveError(f"Impossibile leggere «{name}»: l'archivio è danneggiato.") from e


def _check_sizes(infos, limit: int) -> None:
    total = sum(i.file_size for i in infos)
    if total > limit:
        raise ArchiveError("La dimensione estratta supera la soglia consentita: estrazione interrotta.")
    for i in infos:
        if i.compress_size and i.file_size / max(1, i.compress_size) > MAX_RATIO and i.file_size > 10 * 1024 * 1024:
            raise ArchiveError("Rapporto di compressione anomalo: estrazione interrotta per sicurezza.")


def extract_all(path: str, dest: str, password: str | None = None, limit: int = MAX_EXTRACTED,
                progress=None) -> list[str]:
    """Estrae tutto dentro `dest`; nomi pericolosi normalizzati; nessun file
    residuo in caso di errore (si estrae in una cartella temporanea e si sposta)."""
    dest_path = Path(dest).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    with _open(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        _check_sizes(infos, limit)
        work = Path(tempfile.mkdtemp(prefix=".foliant-unzip-", dir=dest_path))
        written: list[str] = []
        try:
            pwd = password.encode("utf-8") if password else None
            for n, info in enumerate(infos):
                if progress is not None:
                    progress(n / max(1, len(infos)), info.filename)
                rel = safe_name(info.filename)
                if rel is None:
                    continue
                target = (work / rel).resolve()
                if work.resolve() not in target.parents:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(info, pwd=pwd) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                except RuntimeError as e:
                    raise PasswordNeeded("L'archivio è protetto da password.") from e
                except (zipfile.BadZipFile, OSError) as e:
                    raise ArchiveError(f"Impossibile estrarre «{info.filename}»: l'archivio è danneggiato.") from e
            # spostamento finale nella destinazione (nomi già esistenti: suffisso)
            for src in sorted(p for p in work.rglob("*") if p.is_file()):
                rel = src.relative_to(work)
                final = dest_path / rel
                final.parent.mkdir(parents=True, exist_ok=True)
                n = 1
                while final.exists():
                    final = final.with_name(f"{rel.stem} ({n}){rel.suffix}")
                    n += 1
                os.replace(src, final)
                written.append(str(final))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return written
