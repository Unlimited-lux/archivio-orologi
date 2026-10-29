"""Lingue di riconoscimento OCR e gestione dei pacchetti lingua (OCR-03).

Un pacchetto lingua è un file ``<codice>.traineddata`` del motore Tesseract.
I pacchetti di sistema (installati dal gestore pacchetti della distribuzione)
convivono con quelli installati dall'utente nella sua cartella dati; quando
esistono pacchetti utente, la cartella utente diventa una cartella "unita" che
contiene i pacchetti utente più collegamenti a quelli di sistema, così il
motore riceve un'unica ``--tessdata-dir``.

Nessuna dipendenza da Qt: il modulo è usabile da worker e test.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from foliant.core import platform
from foliant.i18n import _


class LanguagePackError(Exception):
    """Errore sui pacchetti lingua; il messaggio è in italiano e mostrabile all'utente."""


class MissingLanguagesError(LanguagePackError):
    """Una o più lingue richieste non sono installate."""

    def __init__(self, codes: list[str]):
        self.codes = list(codes)
        names = ", ".join(language_name(c) for c in self.codes)
        super().__init__(_("Lingue non installate: {names}.").format(names=names))


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str
    script: str
    rtl: bool = False
    vertical: bool = False
    size_mb: float = 0


def _L(code, name, script, size, rtl=False, vertical=False) -> tuple[str, LanguageInfo]:
    return code, LanguageInfo(code, name, script, rtl, vertical, size)


# Dimensioni indicative dei modelli "fast".
CATALOG: dict[str, LanguageInfo] = dict([
    _L("ita", "italiano", "latino", 2.5),
    _L("eng", "inglese", "latino", 3.9),
    _L("deu", "tedesco", "latino", 1.5),
    _L("fra", "francese", "latino", 1.1),
    _L("spa", "spagnolo", "latino", 2.2),
    _L("por", "portoghese", "latino", 1.9),
    _L("nld", "olandese", "latino", 1.9),
    _L("pol", "polacco", "latino", 2.5),
    _L("ces", "ceco", "latino", 1.9),
    _L("slk", "slovacco", "latino", 2.1),
    _L("slv", "sloveno", "latino", 1.7),
    _L("hrv", "croato", "latino", 2.1),
    _L("srp", "serbo (cirillico)", "cirillico", 1.6),
    _L("srp_latn", "serbo (latino)", "latino", 1.8),
    _L("ron", "rumeno", "latino", 1.5),
    _L("hun", "ungherese", "latino", 2.0),
    _L("fin", "finlandese", "latino", 2.0),
    _L("swe", "svedese", "latino", 2.3),
    _L("dan", "danese", "latino", 1.6),
    _L("nor", "norvegese", "latino", 1.8),
    _L("isl", "islandese", "latino", 1.5),
    _L("est", "estone", "latino", 2.1),
    _L("lav", "lettone", "latino", 1.6),
    _L("lit", "lituano", "latino", 1.8),
    _L("cat", "catalano", "latino", 1.0),
    _L("eus", "basco", "latino", 1.8),
    _L("glg", "galiziano", "latino", 1.8),
    _L("lat", "latino", "latino", 2.0),
    _L("sqi", "albanese", "latino", 1.5),
    _L("tur", "turco", "latino", 2.4),
    _L("ell", "greco", "greco", 1.4),
    _L("grc", "greco antico", "greco", 1.9),
    _L("rus", "russo", "cirillico", 3.6),
    _L("ukr", "ucraino", "cirillico", 2.3),
    _L("bul", "bulgaro", "cirillico", 1.6),
    _L("bel", "bielorusso", "cirillico", 1.6),
    _L("mkd", "macedone", "cirillico", 1.3),
    _L("ara", "arabo", "arabo", 1.4, rtl=True),
    _L("fas", "persiano", "arabo", 0.4, rtl=True),
    _L("urd", "urdu", "arabo", 1.0, rtl=True),
    _L("heb", "ebraico", "ebraico", 0.9, rtl=True),
    _L("yid", "yiddish", "ebraico", 0.6, rtl=True),
    _L("chi_sim", "cinese semplificato", "han", 2.4),
    _L("chi_tra", "cinese tradizionale", "han", 2.3),
    _L("chi_sim_vert", "cinese semplificato (verticale)", "han", 1.8, vertical=True),
    _L("chi_tra_vert", "cinese tradizionale (verticale)", "han", 1.8, vertical=True),
    _L("jpn", "giapponese", "giapponese", 2.4),
    _L("jpn_vert", "giapponese (verticale)", "giapponese", 2.9, vertical=True),
    _L("kor", "coreano", "hangul", 1.6),
    _L("kor_vert", "coreano (verticale)", "hangul", 1.0, vertical=True),
    _L("hin", "hindi", "devanagari", 1.6),
    _L("fry", "frisone occidentale", "latino", 1.0),
    _L("vie", "vietnamita", "latino", 0.6),
])

PROTECTED = ("ita", "eng")

_EXCLUDED = ("osd", "equ")
_CODE_RE = re.compile(r"^[a-z]{3}(_[a-z]+)*$")
_MANIFEST = "manifest.json"
_SUFFIX = ".traineddata"
_MIN_SIZE = 1024
_SYSTEM_EXTRA_DIRS = ("configs", "tessconfigs")
_COMMON_DIRS = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
    "/usr/local/share/tessdata",
)

_lock = threading.RLock()
_UNSET = object()
_cache: dict[str, object] = {}


def _reset_caches() -> None:
    """Svuota tutte le cache (usata dai test e dopo installazioni/rimozioni)."""
    with _lock:
        _cache.clear()


def _invalidate() -> None:
    with _lock:
        for key in ("available", "tessdata_dir"):
            _cache.pop(key, None)


# --------------------------------------------------------------- nomi ----


def language_name(code: str) -> str:
    info = CATALOG.get(code)
    return _(info.name) if info else code


# ------------------------------------------------------------ motore ----


def _no_core_dump() -> None:  # pragma: no cover - eseguita nel processo figlio
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def _run(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess | None:
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    # Un pacchetto danneggiato può far abortire il motore: evita i core dump (lenti).
    if args and args[0] == "tesseract":
        exe = platform.find_tool("tesseract")
        if exe is None:
            return None
        args = [exe, *args[1:]]
    if os.name == "posix":
        extra = {"preexec_fn": _no_core_dump}
    else:
        extra = platform.subprocess_kwargs()
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env=env, check=False, **extra)
    except (OSError, subprocess.SubprocessError):
        return None


def tesseract_version() -> str | None:
    with _lock:
        if "version" not in _cache:
            proc = _run(["tesseract", "--version"])
            version = None
            if proc is not None and proc.returncode == 0:
                out = (proc.stdout or proc.stderr or "").strip()
                version = out.splitlines()[0].strip() if out else None
            _cache["version"] = version
        return _cache["version"]  # type: ignore[return-value]


def tesseract_available() -> bool:
    return tesseract_version() is not None


# ---------------------------------------------------------- cartelle ----


def _has_packs(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.glob("*" + _SUFFIX))
    except OSError:
        return False


def system_tessdata_dir() -> Path | None:
    with _lock:
        if "system_dir" in _cache:
            return _cache["system_dir"]  # type: ignore[return-value]
        found: Path | None = None
        prefix = os.environ.get("TESSDATA_PREFIX")
        if prefix:
            for cand in (Path(prefix), Path(prefix) / "tessdata"):
                if _has_packs(cand):
                    found = cand
                    break
        if found is None:
            proc = _run(["tesseract", "--list-langs"])
            if proc is not None and proc.returncode == 0:
                text = (proc.stdout or "") + "\n" + (proc.stderr or "")
                m = re.search(r'List of available languages in "([^"]+)"', text)
                if m and _has_packs(Path(m.group(1))):
                    found = Path(m.group(1))
        if found is None:
            for cand in _COMMON_DIRS:
                if _has_packs(Path(cand)):
                    found = Path(cand)
                    break
        if found is not None:
            found = Path(os.path.normpath(found))
        _cache["system_dir"] = found
        return found


def user_tessdata_dir() -> Path:
    override = os.environ.get("FOLIANT_TESSDATA_DIR")
    if override:
        return Path(override)
    return platform.app_dir("data") / "tessdata"


def _system_packs() -> dict[str, Path]:
    sysdir = system_tessdata_dir()
    if sysdir is None:
        return {}
    try:
        return {p.name[: -len(_SUFFIX)]: p for p in sysdir.glob("*" + _SUFFIX) if p.is_file()}
    except OSError:
        return {}


# --------------------------------------------------------- manifesto ----


def _read_manifest() -> dict[str, dict]:
    path = user_tessdata_dir() / _MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    packs = data.get("packs") if isinstance(data, dict) else None
    return {k: v for k, v in packs.items() if isinstance(v, dict)} if isinstance(packs, dict) else {}


def _write_manifest(packs: dict[str, dict]) -> None:
    udir = user_tessdata_dir()
    udir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=udir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "packs": packs}, fh, indent=2, sort_keys=True)
        platform.replace_file(tmp, udir / _MANIFEST)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def user_installed_languages() -> list[str]:
    udir = user_tessdata_dir()
    return sorted(code for code in _read_manifest()
                  if (udir / (code + _SUFFIX)).is_file()
                  and not (udir / (code + _SUFFIX)).is_symlink())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------- cartella per il motore ----


def _sync_merged_dir(udir: Path, user_codes: set[str]) -> None:
    """Allinea nella cartella utente i collegamenti ai pacchetti di sistema."""
    system = _system_packs()
    sysdir = system_tessdata_dir()
    # Rimuove i mirror obsoleti (file non registrati nel manifesto).
    for entry in udir.glob("*" + _SUFFIX):
        code = entry.name[: -len(_SUFFIX)]
        if code in user_codes:
            continue
        stale = code not in system
        if entry.is_symlink():
            try:
                stale = stale or Path(os.readlink(entry)) != system[code]
            except OSError:
                stale = True
        if stale:
            entry.unlink(missing_ok=True)
    for code, src in system.items():
        if code in user_codes:
            continue
        dst = udir / (code + _SUFFIX)
        if dst.exists() or dst.is_symlink():
            continue
        try:
            dst.symlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
    if sysdir is not None:
        for name in _SYSTEM_EXTRA_DIRS:
            src = sysdir / name
            dst = udir / name
            if not src.is_dir() or dst.exists() or dst.is_symlink():
                continue
            try:
                dst.symlink_to(src, target_is_directory=True)
            except OSError:
                shutil.copytree(src, dst)


def tessdata_dir() -> str | None:
    with _lock:
        cached = _cache.get("tessdata_dir", _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]
        user_codes = set(user_installed_languages())
        if not user_codes:
            sysdir = system_tessdata_dir()
            result = str(sysdir) if sysdir is not None else None
        else:
            udir = user_tessdata_dir()
            try:
                _sync_merged_dir(udir, user_codes)
            except OSError as exc:
                raise LanguagePackError(
                    _("Impossibile preparare la cartella delle lingue: {err}").format(err=exc)
                ) from exc
            result = str(udir)
        _cache["tessdata_dir"] = result
        return result


# ------------------------------------------------------ disponibilità ----


def _sort_codes(codes) -> list[str]:
    codes = set(codes)
    head = [c for c in PROTECTED if c in codes]
    return head + sorted(codes - set(head))


def _all_installed() -> set[str]:
    return set(_system_packs()) | set(user_installed_languages())


def available_languages(refresh: bool = False) -> list[str]:
    with _lock:
        if refresh:
            _cache.pop("available", None)
            _cache.pop("system_dir", None)
            _cache.pop("tessdata_dir", None)
        if "available" not in _cache:
            _cache["available"] = _sort_codes(c for c in _all_installed() if c not in _EXCLUDED)
        return list(_cache["available"])  # type: ignore[arg-type]


def has_osd() -> bool:
    return "osd" in _all_installed()


def missing_languages(codes: list[str]) -> list[str]:
    installed = set(available_languages())
    out: list[str] = []
    for c in codes:
        if c not in installed and c not in out:
            out.append(c)
    return out


def check_languages(codes: list[str]) -> None:
    if not codes:
        raise LanguagePackError(_("Scegli almeno una lingua."))
    missing = missing_languages(codes)
    if missing:
        raise MissingLanguagesError(missing)


# -------------------------------------------------------- validazione ----


def _check_header(path: Path, size: int) -> None:
    corrupt = _("Il file non è un pacchetto lingua valido o è danneggiato.")
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
            if len(head) < 4:
                raise LanguagePackError(corrupt)
            (num,) = struct.unpack("<i", head)
            if not 1 <= num <= 100:
                raise LanguagePackError(corrupt)
            raw = fh.read(8 * num)
    except OSError as exc:
        raise LanguagePackError(_("Impossibile leggere il file: {err}").format(err=exc)) from exc
    if len(raw) < 8 * num:
        raise LanguagePackError(corrupt)
    offsets = struct.unpack("<%dq" % num, raw)
    header_end = 4 + 8 * num
    last = header_end
    valid = 0
    for off in offsets:
        if off == -1:
            continue
        if off < last or off >= size:
            raise LanguagePackError(corrupt)
        last = off
        valid += 1
    if valid == 0:
        raise LanguagePackError(corrupt)


def _make_probe_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("L", (240, 64), 255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 20), "Foliant 123", fill=0)
    img.save(path)


def validate_traineddata(path) -> None:
    path = Path(path)
    if not path.is_file():
        raise LanguagePackError(_("Il file del pacchetto lingua non esiste."))
    try:
        size = path.stat().st_size
        with open(path, "rb"):
            pass
    except OSError as exc:
        raise LanguagePackError(_("Impossibile leggere il file: {err}").format(err=exc)) from exc
    if size < _MIN_SIZE:
        raise LanguagePackError(_("Il file è troppo piccolo per essere un pacchetto lingua."))
    _check_header(path, size)

    if not tesseract_available():
        raise LanguagePackError(
            _("Il motore OCR (Tesseract) non è installato: impossibile verificare il pacchetto.")
        )
    code = path.name[: -len(_SUFFIX)] if path.name.endswith(_SUFFIX) else "pack"
    if not _CODE_RE.match(code):
        code = "pack"
    with tempfile.TemporaryDirectory(prefix="foliant-tessdata-") as tmp:
        tmpdir = Path(tmp)
        try:
            shutil.copyfile(path, tmpdir / (code + _SUFFIX))
            probe = tmpdir / "probe.png"
            _make_probe_png(probe)
        except OSError as exc:
            raise LanguagePackError(_("Impossibile leggere il file: {err}").format(err=exc)) from exc
        proc = _run(["tesseract", str(probe), "stdout", "--tessdata-dir", str(tmpdir),
                     "-l", code], timeout=60)
        if proc is None:
            raise LanguagePackError(
                _("Il motore OCR non è riuscito a caricare il pacchetto (tempo scaduto o errore).")
            )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            reason = detail[-1] if detail else ""
            msg = _("Pacchetto lingua incompatibile o danneggiato: il motore OCR non riesce a caricarlo.")
            if reason:
                msg += " (" + reason + ")"
            raise LanguagePackError(msg)


# ---------------------------------------------- installazione/rimozione ----


def install_from_file(path, code: str | None = None) -> str:
    path = Path(path)
    if code is None:
        name = path.name
        code = name[: -len(_SUFFIX)] if name.endswith(_SUFFIX) else path.stem
    if not (code in CATALOG or _CODE_RE.match(code)):
        raise LanguagePackError(
            _("Codice lingua non valido: «{code}».").format(code=code)
        )
    # Valida una copia con il nome definitivo, così il motore la carica con quel codice.
    with tempfile.TemporaryDirectory(prefix="foliant-install-") as tmp:
        staged = Path(tmp) / (code + _SUFFIX)
        try:
            shutil.copyfile(path, staged)
        except OSError as exc:
            if not path.is_file():
                raise LanguagePackError(_("Il file del pacchetto lingua non esiste.")) from exc
            raise LanguagePackError(_("Impossibile leggere il file: {err}").format(err=exc)) from exc
        validate_traineddata(staged)
        digest = _sha256(staged)
        size = staged.stat().st_size

        with _lock:
            udir = user_tessdata_dir()
            try:
                udir.mkdir(parents=True, exist_ok=True)
                fd, tmp_dst = tempfile.mkstemp(prefix=".install-", suffix=".part", dir=udir)
                os.close(fd)
                try:
                    shutil.copyfile(staged, tmp_dst)
                    if _sha256(Path(tmp_dst)) != digest:
                        raise LanguagePackError(_("Copia del pacchetto non riuscita: dati alterati."))
                    platform.replace_file(tmp_dst, udir / (code + _SUFFIX))
                except BaseException:
                    Path(tmp_dst).unlink(missing_ok=True)
                    raise
                packs = _read_manifest()
                packs[code] = {"sha256": digest, "size": size}
                _write_manifest(packs)
            except OSError as exc:
                raise LanguagePackError(
                    _("Installazione non riuscita: {err}").format(err=exc)
                ) from exc
            _invalidate()
    return code


def verify_installed(code: str) -> bool:
    packs = _read_manifest()
    udir = user_tessdata_dir()
    target = udir / (code + _SUFFIX)
    if code in packs and target.is_file() and not target.is_symlink():
        try:
            return _sha256(target) == packs[code].get("sha256")
        except OSError:
            return False
    return code in _system_packs()


def remove_language(code: str) -> None:
    if code in PROTECTED:
        raise LanguagePackError(_("L'italiano e l'inglese non si possono rimuovere."))
    with _lock:
        if code not in user_installed_languages():
            if code in _system_packs():
                raise LanguagePackError(
                    _("«{name}» è un pacchetto di sistema: si rimuove con il gestore pacchetti "
                      "della distribuzione.").format(name=language_name(code))
                )
            raise LanguagePackError(
                _("La lingua «{name}» non è installata.").format(name=language_name(code))
            )
        udir = user_tessdata_dir()
        try:
            (udir / (code + _SUFFIX)).unlink(missing_ok=True)
            packs = _read_manifest()
            packs.pop(code, None)
            _write_manifest(packs)
        except OSError as exc:
            raise LanguagePackError(_("Rimozione non riuscita: {err}").format(err=exc)) from exc
        _invalidate()
        # Ripristina subito il collegamento al pacchetto di sistema eventualmente oscurato.
        if user_installed_languages():
            tessdata_dir()


# ------------------------------------------------------------ download ----


def download_language(code: str, progress=None,
                      base_url: str = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/",
                      timeout: float = 60) -> str:
    """Scarica e installa un pacchetto. Da chiamare solo su azione esplicita dell'utente."""
    if not (code in CATALOG or _CODE_RE.match(code)):
        raise LanguagePackError(_("Codice lingua non valido: «{code}».").format(code=code))
    url = base_url.rstrip("/") + "/" + code + _SUFFIX
    name = language_name(code)
    with tempfile.TemporaryDirectory(prefix="foliant-download-") as tmp:
        dest = Path(tmp) / (code + _SUFFIX)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if status is not None and status >= 400:
                    raise LanguagePackError(
                        _("Download non riuscito: risposta HTTP {status}.").format(status=status)
                    )
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError, AttributeError):
                    total = 0
                done = 0
                with open(dest, "wb") as fh:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            frac = min(done / total, 1.0) if total else 0.0
                            progress.update(frac, _("Download di {name}: {mb:.1f} MB").format(
                                name=name, mb=done / 1048576))
                if total and done != total:
                    raise LanguagePackError(_("Download non riuscito: file incompleto."))
        except LanguagePackError:
            raise
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise LanguagePackError(
                _("Download non riuscito: {err}").format(err=reason)
            ) from exc
        if progress is not None:
            progress.update(1.0, _("Verifica del pacchetto {name}…").format(name=name))
        return install_from_file(dest, code)
