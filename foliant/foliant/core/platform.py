"""Differenze tra sistemi operativi (Linux, Windows; macOS dove costa poco), in un solo posto.

Il resto del codice non controlla `sys.platform` né scrive percorsi di sistema: chiede qui.

- Cartelle dell'utente: `data_home()`, `config_home()`, `state_home()`, `cache_home()` (le basi:
  XDG su Linux, %APPDATA% e %LOCALAPPDATA% su Windows) e `app_dir(kind)` per le cartelle di
  Foliant dentro di esse. Su Windows si leggono le variabili d'ambiente prima delle API di sistema:
  così i test isolano il profilo come su Linux con XDG_*.
- `private_dir(path)`, `make_private_dir(path)`: cartella leggibile solo dall'utente (0700 su
  POSIX; su Windows le cartelle del profilo sono già protette dagli ACL dell'utente).
- `pid_alive(pid)`: su Windows *non* si usa `os.kill(pid, 0)`, che lì termina il processo.
- `find_tool(name)`: Tesseract, Ghostscript, qpdf, LibreOffice… anche fuori dal PATH (cartella
  dell'installatore di Foliant, Program Files, registro).
- `subprocess_kwargs()`, `kill_tree(proc)`: niente finestre di console su Windows, gruppo di
  processi da terminare insieme.
- `replace_file(src, dst)`: `os.replace` che su Windows riprova se il file è aperto da un altro
  programma (antivirus, anteprima, indicizzazione) e poi solleva `FileInUseError`.
- `font_dirs()`, `font_file(role)`: cartelle dei font e font di ripiego per ruolo.
- `user_full_name()`, `compose_mail(...)`: nome dell'account e messaggio di posta con allegato.
"""

from __future__ import annotations

import ctypes
import errno
import glob
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
APP_DIR_NAME = "foliant"


# ------------------------------------------------------------------ cartelle dell'utente
def _env_path(var: str, env=None) -> Path | None:
    value = (os.environ if env is None else env).get(var)
    return Path(value).expanduser() if value else None


def _windows_known(var: str, fallback: str, env=None) -> Path:
    return _env_path(var, env) or Path.home() / fallback


def data_home(env=None) -> Path:
    """Base dei dati dell'utente: $XDG_DATA_HOME o ~/.local/share; %APPDATA% su Windows.

    `env` (anche nelle funzioni seguenti): variabili d'ambiente al posto di os.environ."""
    if IS_WINDOWS:
        return _windows_known("APPDATA", "AppData/Roaming", env)
    return _env_path("XDG_DATA_HOME", env) or Path.home() / ".local" / "share"


def config_home(env=None) -> Path:
    """Base della configurazione: $XDG_CONFIG_HOME o ~/.config; %APPDATA% su Windows."""
    if IS_WINDOWS:
        return _windows_known("APPDATA", "AppData/Roaming", env)
    return _env_path("XDG_CONFIG_HOME", env) or Path.home() / ".config"


def state_home(env=None) -> Path:
    """Base dello stato (es. copie di recupero): $XDG_STATE_HOME o ~/.local/state; %LOCALAPPDATA% su Windows."""
    if IS_WINDOWS:
        return _windows_known("LOCALAPPDATA", "AppData/Local", env)
    return _env_path("XDG_STATE_HOME", env) or Path.home() / ".local" / "state"


def cache_home(env=None) -> Path:
    """Base della cache: $XDG_CACHE_HOME o ~/.cache; %LOCALAPPDATA% su Windows."""
    if IS_WINDOWS:
        return _windows_known("LOCALAPPDATA", "AppData/Local", env)
    return _env_path("XDG_CACHE_HOME", env) or Path.home() / ".cache"


def app_dir(kind: str = "data") -> Path:
    """Cartella di Foliant: «data», «config», «state» o «cache» (non viene creata).

    Su Windows la cache sta in %LOCALAPPDATA%\\foliant\\cache, per non mescolarla allo stato."""
    if kind == "cache" and IS_WINDOWS:
        return cache_home() / APP_DIR_NAME / "cache"
    base = {"data": data_home, "config": config_home, "state": state_home, "cache": cache_home}[kind]()
    return base / APP_DIR_NAME


def settings_dir() -> Path:
    """Cartella delle impostazioni di Foliant gestite da Qt (organizzazione «Foliant»):
    ~/.config/Foliant su Linux; su Windows le QSettings stanno nel registro e i file accanto
    (es. le sequenze delle operazioni in blocco) in %APPDATA%\\Foliant."""
    return config_home() / "Foliant"


def private_dir(path: str | os.PathLike) -> bool:
    """True se `path` è una cartella dell'utente corrente che nessun altro può leggere o scrivere.

    Su POSIX: proprietario e permessi 0700. Su Windows i permessi POSIX non esistono (st_mode
    vale sempre 0o777): le cartelle del profilo e la cartella temporanea dell'utente sono già
    private per gli ACL, quindi basta che sia una cartella vera (non un collegamento)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat
    if not stat.S_ISDIR(st.st_mode):
        return False
    if not hasattr(os, "getuid"):
        return True
    return st.st_uid == os.getuid() and not st.st_mode & 0o077


def owned_by_user(st: os.stat_result) -> bool:
    """True se il file descritto da `st` è dell'utente corrente (sempre su Windows)."""
    return not hasattr(os, "getuid") or st.st_uid == os.getuid()


def make_private_dir(path: str | os.PathLike) -> Path:
    """Crea `path` (con le cartelle intermedie) leggibile solo dall'utente; la restituisce."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def make_private_file(path: str | os.PathLike) -> None:
    """Permessi 0600 su POSIX; su Windows nulla (chmod lì cambia solo la sola lettura)."""
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ------------------------------------------------------------------ processi
def pid_alive(pid) -> bool:
    """True se esiste un processo con questo pid (anche di un altro utente)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if IS_WINDOWS:
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    if pid > 0xFFFFFFFF:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # processo di un altro utente o protetto: esiste
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def subprocess_kwargs(new_group: bool = False) -> dict:
    """Argomenti per `subprocess.run/Popen` di un programma esterno senza interfaccia.

    Su Windows niente finestra di console (Foliant.exe non ne ha una: ogni programma a riga di
    comando ne aprirebbe una propria); con `new_group` il processo ha un gruppo proprio, per
    poterlo terminare con i suoi figli (`kill_tree`)."""
    if IS_WINDOWS:
        flags = subprocess.CREATE_NO_WINDOW
        if new_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags}
    return {"start_new_session": True} if new_group else {}


def kill_tree(proc: subprocess.Popen) -> None:
    """Termina `proc` e i processi che ha avviato (avviato con `subprocess_kwargs(new_group=True)`)."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           timeout=15, **subprocess_kwargs())
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


# ------------------------------------------------------------------ file
class FileInUseError(PermissionError):
    """Su Windows il file di destinazione è aperto da un altro programma che ne impedisce la sostituzione."""

    def __init__(self, filename: str):
        super().__init__(errno.EBUSY, "file in uso da un altro programma", filename)


_SHARING_ERRORS = (5, 32, 33)    # accesso negato, violazione di condivisione, violazione del blocco


def is_file_in_use(error: OSError) -> bool:
    """True se `error` dice che il file è aperto da un altro programma (Windows)."""
    return isinstance(error, FileInUseError) or getattr(error, "winerror", None) in (32, 33)


def replace_file(src: str | os.PathLike, dst: str | os.PathLike, attempts: int = 10, delay: float = 0.1) -> None:
    """`os.replace(src, dst)`. Su Windows la sostituzione non riesce se `dst` è aperto senza
    condivisione da un altro programma (lettore PDF, anteprima di Esplora risorse, antivirus,
    indicizzazione): si riprova per circa un secondo, poi si solleva `FileInUseError`."""
    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if getattr(e, "winerror", None) not in _SHARING_ERRORS or not os.path.exists(dst):
                raise
            if os.path.isdir(dst):
                raise
            if i == attempts - 1:
                raise FileInUseError(str(dst)) from e
            time.sleep(delay)


@contextmanager
def file_lock(path: str | os.PathLike):
    """Blocco esclusivo tra processi sul file `path` (creato se manca): flock su POSIX,
    msvcrt.locking sul primo byte su Windows. Attende finché il blocco non è libero."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if IS_WINDOWS:
            import msvcrt
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)    # riprova da sé per circa 10 s
                    break
                except OSError as e:
                    if e.errno not in (errno.EDEADLK, errno.EACCES):
                        raise
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ------------------------------------------------------------------ strumenti esterni
_TOOL_NAMES = {
    "tesseract": ("tesseract",),
    "gs": ("gswin64c", "gswin32c", "gs") if IS_WINDOWS else ("gs",),
    "qpdf": ("qpdf",),
    "soffice": ("soffice", "libreoffice"),
    "lp": ("lp",),
}
_ENV_OVERRIDES = {"tesseract": "FOLIANT_TESSERACT", "gs": "FOLIANT_GHOSTSCRIPT", "qpdf": "FOLIANT_QPDF",
                  "soffice": "FOLIANT_SOFFICE"}


def bundle_dir() -> Path | None:
    """Cartella degli strumenti inclusi nel pacchetto di Foliant (Windows: «tools» accanto a
    Foliant.exe), se esiste."""
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        candidate = root / "tools"
        if candidate.is_dir():
            return candidate
    return None


def _program_files() -> list[Path]:
    dirs = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            p = Path(value) if var != "LOCALAPPDATA" else Path(value) / "Programs"
            if p not in dirs:
                dirs.append(p)
    return dirs


def _windows_registry_value(key_path: str, value_name: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    value, _kind = winreg.QueryValueEx(key, value_name)
                    if value:
                        return str(value)
            except OSError:
                continue
    return None


def _windows_candidates(name: str) -> list[str]:
    out: list[str] = []
    bundle = bundle_dir()
    pf = _program_files()
    if name == "tesseract":
        if bundle:
            out.append(str(bundle / "tesseract" / "tesseract.exe"))
        reg = _windows_registry_value(r"SOFTWARE\Tesseract-OCR", "InstallDir")
        if reg:
            out.append(os.path.join(reg, "tesseract.exe"))
        out += [str(p / "Tesseract-OCR" / "tesseract.exe") for p in pf]
    elif name == "gs":
        if bundle:
            out += [str(bundle / "ghostscript" / "bin" / "gswin64c.exe"), str(bundle / "ghostscript" / "bin" / "gswin32c.exe")]
        for p in pf:
            for exe in ("gswin64c.exe", "gswin32c.exe"):
                out += sorted(glob.glob(os.path.join(glob.escape(str(p)), "gs", "gs*", "bin", exe)), reverse=True)
    elif name == "qpdf":
        if bundle:
            out.append(str(bundle / "qpdf" / "bin" / "qpdf.exe"))
        for p in pf:
            out += sorted(glob.glob(os.path.join(glob.escape(str(p)), "qpdf*", "bin", "qpdf.exe")), reverse=True)
    elif name == "soffice":
        reg = _windows_registry_value(r"SOFTWARE\LibreOffice\UNO\InstallPath", "")
        if reg:
            out.append(os.path.join(reg, "soffice.exe"))
        out += [str(p / "LibreOffice" / "program" / "soffice.exe") for p in pf]
    return out


_POSIX_CANDIDATES = {
    "soffice": ("/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice",
                "/snap/bin/libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"),
}


@lru_cache(maxsize=None)
def _find_tool_cached(name: str, path_env: str) -> str | None:
    override = os.environ.get(_ENV_OVERRIDES.get(name, ""), "") if name in _ENV_OVERRIDES else ""
    if override and os.path.isfile(override):
        return override
    if IS_WINDOWS:
        # gli strumenti inclusi nel pacchetto hanno la precedenza: versione e lingue note
        bundled = [c for c in _windows_candidates(name) if bundle_dir() and c.startswith(str(bundle_dir()))]
        for c in bundled:
            if os.path.isfile(c):
                return c
    for exe in _TOOL_NAMES.get(name, (name,)):
        found = shutil.which(exe, path=path_env or None)
        if found:
            return found
    candidates = _windows_candidates(name) if IS_WINDOWS else list(_POSIX_CANDIDATES.get(name, ()))
    for c in candidates:
        if os.path.isfile(c) and (IS_WINDOWS or os.access(c, os.X_OK)):
            return c
    return None


def find_tool(name: str) -> str | None:
    """Percorso dell'eseguibile di uno strumento esterno, o None se manca.

    `name` è il nome Linux: «tesseract», «gs», «qpdf», «soffice», ecc. Ordine: variabile
    d'ambiente (es. FOLIANT_TESSERACT), strumenti inclusi nel pacchetto (Windows), PATH, posizioni
    note (Program Files e registro su Windows). Il risultato dipende dal PATH corrente."""
    return _find_tool_cached(name, os.environ.get("PATH", ""))


def forget_tools() -> None:
    """Dimentica i percorsi trovati (dopo un'installazione o nei test)."""
    _find_tool_cached.cache_clear()


TOOL_HINTS_WINDOWS = {
    "tesseract": "Reinstalla Foliant (Tesseract è incluso) oppure installa Tesseract-OCR da "
                 "https://github.com/UB-Mannheim/tesseract/wiki con le lingue italiana e inglese.",
    "gs": "Reinstalla Foliant (Ghostscript è incluso) oppure installa Ghostscript a 64 bit da https://ghostscript.com.",
    "qpdf": "Reinstalla Foliant (qpdf è incluso) oppure installa qpdf da https://qpdf.org.",
    "soffice": "Installa LibreOffice da https://www.libreoffice.org e riavvia Foliant.",
}


# ------------------------------------------------------------------ font
def font_dirs() -> list[str]:
    """Cartelle dei font del sistema e dell'utente."""
    if IS_WINDOWS:
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        dirs = [os.path.join(windir, "Fonts")]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
        return dirs
    home = os.path.expanduser("~")
    dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".fonts"),
            str(data_home() / "fonts")]
    if IS_MAC:
        dirs += ["/Library/Fonts", "/System/Library/Fonts", os.path.join(home, "Library", "Fonts")]
    return dirs


# Font di ripiego per ruolo: prima quelli dei sistemi Linux (DejaVu, Liberation, Noto), poi
# gli equivalenti metrici di Windows (Arial, Times New Roman, Courier New) e Segoe UI.
_FONT_ROLES = {
    "sans": ("truetype/liberation/LiberationSans-Regular.ttf", "truetype/liberation2/LiberationSans-Regular.ttf",
             "truetype/dejavu/DejaVuSans.ttf", "dejavu/DejaVuSans.ttf", "TTF/DejaVuSans.ttf",
             "truetype/noto/NotoSans-Regular.ttf", "noto/NotoSans-Regular.ttf",
             "arial.ttf", "segoeui.ttf", "Arial.ttf"),
    "sans-bold": ("truetype/liberation/LiberationSans-Bold.ttf", "truetype/liberation2/LiberationSans-Bold.ttf",
                  "truetype/dejavu/DejaVuSans-Bold.ttf", "dejavu/DejaVuSans-Bold.ttf", "TTF/DejaVuSans-Bold.ttf",
                  "arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf"),
    "serif": ("truetype/liberation/LiberationSerif-Regular.ttf", "truetype/liberation2/LiberationSerif-Regular.ttf",
              "truetype/dejavu/DejaVuSerif.ttf", "dejavu/DejaVuSerif.ttf", "times.ttf", "Times New Roman.ttf"),
    "serif-bold": ("truetype/liberation/LiberationSerif-Bold.ttf", "truetype/liberation2/LiberationSerif-Bold.ttf",
                   "truetype/dejavu/DejaVuSerif-Bold.ttf", "dejavu/DejaVuSerif-Bold.ttf", "timesbd.ttf",
                   "Times New Roman Bold.ttf"),
    "mono": ("truetype/liberation/LiberationMono-Regular.ttf", "truetype/liberation2/LiberationMono-Regular.ttf",
             "truetype/dejavu/DejaVuSansMono.ttf", "dejavu/DejaVuSansMono.ttf", "cour.ttf", "consola.ttf",
             "Courier New.ttf"),
    "mono-bold": ("truetype/liberation/LiberationMono-Bold.ttf", "truetype/liberation2/LiberationMono-Bold.ttf",
                  "truetype/dejavu/DejaVuSansMono-Bold.ttf", "dejavu/DejaVuSansMono-Bold.ttf", "courbd.ttf",
                  "consolab.ttf", "Courier New Bold.ttf"),
    # copertura Unicode ampia (testi in molte lingue, simboli)
    "unicode": ("truetype/dejavu/DejaVuSans.ttf", "dejavu/DejaVuSans.ttf", "TTF/DejaVuSans.ttf",
                "truetype/noto/NotoSans-Regular.ttf", "noto/NotoSans-Regular.ttf",
                "truetype/liberation/LiberationSans-Regular.ttf", "segoeui.ttf", "arial.ttf", "Arial Unicode.ttf"),
}


def font_candidates(role: str) -> tuple[str, ...]:
    """Percorsi possibili (esistenti o no) del font di ripiego per `role`, in ordine di preferenza."""
    out = []
    for rel in _FONT_ROLES[role]:
        for d in font_dirs():
            out.append(os.path.join(d, *rel.split("/")))
    return tuple(out)


@lru_cache(maxsize=None)
def font_file(role: str = "sans") -> str | None:
    """Primo font esistente per `role` («sans», «sans-bold», «serif», «serif-bold», «mono»,
    «mono-bold», «unicode»), o None."""
    for path in font_candidates(role):
        if os.path.isfile(path):
            return path
    return None


# ------------------------------------------------------------------ utente, posta
def user_full_name() -> str:
    """Nome completo dell'account (es. per l'autore dei commenti), o il nome utente."""
    if IS_WINDOWS:
        try:
            secur32 = ctypes.WinDLL("secur32")
            NameDisplay = 3
            size = ctypes.c_ulong(0)
            secur32.GetUserNameExW(NameDisplay, None, ctypes.byref(size))
            if size.value:
                buf = ctypes.create_unicode_buffer(size.value + 1)
                if secur32.GetUserNameExW(NameDisplay, buf, ctypes.byref(size)) and buf.value.strip():
                    return buf.value.strip()
        except Exception:
            pass
    else:
        try:
            import pwd
            gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
            if gecos:
                return gecos
        except Exception:
            pass
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return ""


def mail_available() -> bool:
    """True se si può preparare un messaggio di posta con allegato (xdg-email su Linux,
    MAPI su Windows: Outlook, Thunderbird e gli altri client predefiniti)."""
    if IS_WINDOWS:
        return _mapi() is not None
    return shutil.which("xdg-email") is not None


def mail_client() -> str | None:
    """Programma usato per la posta con allegato: percorso di xdg-email o «MAPI» su Windows."""
    if IS_WINDOWS:
        return "MAPI" if _mapi() is not None else None
    return shutil.which("xdg-email")


def _mapi():
    try:
        return ctypes.WinDLL("mapi32")
    except (OSError, AttributeError):
        return None


def compose_mail(attachment: str, subject: str = "", to: str = "", body: str = "") -> bool:
    """Apre un nuovo messaggio nel programma di posta con `attachment` allegato. Non attende
    l'invio. False se non c'è un programma di posta utilizzabile."""
    if IS_WINDOWS:
        return _mapi_send(attachment, subject, to, body)
    exe = shutil.which("xdg-email")
    if not exe:
        return False
    args = [exe, "--utf8"]
    if subject:
        args += ["--subject", subject]
    if body:
        args += ["--body", body]
    args += ["--attach", attachment]
    if to:
        args.append(to)
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True


def _mapi_send(attachment: str, subject: str, to: str, body: str) -> bool:
    """MAPISendMailW in un thread proprio (la chiamata resta bloccata finché l'utente non chiude
    la finestra del messaggio)."""
    mapi = _mapi()
    if mapi is None:
        return False
    import threading
    from ctypes import wintypes

    class MapiFileDescW(ctypes.Structure):
        _fields_ = [("ulReserved", wintypes.ULONG), ("flFlags", wintypes.ULONG), ("nPosition", wintypes.ULONG),
                    ("lpszPathName", wintypes.LPWSTR), ("lpszFileName", wintypes.LPWSTR), ("lpFileType", ctypes.c_void_p)]

    class MapiRecipDescW(ctypes.Structure):
        _fields_ = [("ulReserved", wintypes.ULONG), ("ulRecipClass", wintypes.ULONG), ("lpszName", wintypes.LPWSTR),
                    ("lpszAddress", wintypes.LPWSTR), ("ulEIDSize", wintypes.ULONG), ("lpEntryID", ctypes.c_void_p)]

    class MapiMessageW(ctypes.Structure):
        _fields_ = [("ulReserved", wintypes.ULONG), ("lpszSubject", wintypes.LPWSTR), ("lpszNoteText", wintypes.LPWSTR),
                    ("lpszMessageType", wintypes.LPWSTR), ("lpszDateReceived", wintypes.LPWSTR),
                    ("lpszConversationID", wintypes.LPWSTR), ("flFlags", wintypes.ULONG), ("lpOriginator", ctypes.c_void_p),
                    ("nRecipCount", wintypes.ULONG), ("lpRecips", ctypes.POINTER(MapiRecipDescW)),
                    ("nFileCount", wintypes.ULONG), ("lpFiles", ctypes.POINTER(MapiFileDescW))]

    send = getattr(mapi, "MAPISendMailW", None)
    if send is None:
        return False
    MAPI_LOGON_UI, MAPI_DIALOG, MAPI_TO = 0x1, 0x8, 1

    def run() -> None:
        f = MapiFileDescW(0, 0, 0xFFFFFFFF, attachment, os.path.basename(attachment), None)
        recips = (MapiRecipDescW * 1)()
        n = 0
        if to:
            recips[0] = MapiRecipDescW(0, MAPI_TO, to, "SMTP:" + to, 0, None)
            n = 1
        msg = MapiMessageW(0, subject or None, body or None, None, None, None, 0, None, n,
                           recips if n else None, 1, ctypes.pointer(f))
        try:
            send(None, None, ctypes.byref(msg), MAPI_LOGON_UI | MAPI_DIALOG, 0)
        except Exception:
            pass

    threading.Thread(target=run, name="foliant-mapi", daemon=True).start()
    return True
