"""Apertura unica: lo stesso file PDF non è mai aperto due volte nello stesso momento, né in due
schede, né in due finestre, né in due processi Foliant.

Prove di comportamento con processi Foliant veri (tests/single_open_helper.py), indipendenti
dall'API interna del blocco. Coprono i modi in cui Windows indica lo stesso file con percorsi
diversi (maiuscole, barre, nomi brevi 8.3, collegamenti, unità di `subst`), l'uccisione forzata
del processo che tiene il file, il salvataggio con il blocco attivo e la riservatezza (sul disco
né il nome né il percorso del file).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from foliant.core import platform
from tests.conftest import perf_limit

HELPER = Path(__file__).with_name("single_open_helper.py")
ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"
TOKEN = "Zq7Perizia"               # nel nome dei file: non deve comparire in nessun file del profilo


def _implemented() -> bool:
    """Il blocco dell'apertura unica è presente nel nucleo (vedi il commit dell'implementazione).
    FOLIANT_SINGLE_OPEN_FORCE=1 esegue comunque le prove (verifica del banco di prova)."""
    if os.environ.get("FOLIANT_SINGLE_OPEN_FORCE"):
        return True
    from foliant.ui import instance, main_window
    names = ("DocumentLock", "document_lock", "acquire_document_lock", "lock_document", "OpenFileLock")
    return any(hasattr(mod, n) for mod in (platform, instance, main_window) for n in names)


pytestmark = pytest.mark.skipif(not _implemented(), reason="apertura unica non ancora implementata nel nucleo")


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Perizia orologio: Rolex Submariner 16610")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def profile(tmp_path):
    """Cartelle dell'utente isolate e condivise dai processi del test."""
    base = tmp_path / "profilo"
    dirs = {}
    for key in ("config", "data", "cache", "state", "runtime", "appdata", "localappdata", "temp", "ini"):
        p = base / key
        p.mkdir(parents=True)
        dirs[key] = p
    os.chmod(dirs["runtime"], 0o700)
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "XDG_CONFIG_HOME": str(dirs["config"]), "XDG_DATA_HOME": str(dirs["data"]),
        "XDG_CACHE_HOME": str(dirs["cache"]), "XDG_STATE_HOME": str(dirs["state"]),
        "XDG_RUNTIME_DIR": str(dirs["runtime"]),
        "APPDATA": str(dirs["appdata"]), "LOCALAPPDATA": str(dirs["localappdata"]),
        "TEMP": str(dirs["temp"]), "TMP": str(dirs["temp"]), "TMPDIR": str(dirs["temp"]),
        "FOLIANT_TEST_INI": str(dirs["ini"]),
    })
    return {"env": env, "dirs": dirs, "base": base}


class Foliant:
    """Un processo Foliant che apre un file e risponde ai comandi del test."""

    def __init__(self, profile, path):
        self.proc = subprocess.Popen(
            [sys.executable, str(HELPER), str(path)], env=profile["env"], cwd=str(ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **platform.subprocess_kwargs())
        self.events: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(self.proc.stderr), daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    pass

    def wait(self, event: str, timeout: float = 90) -> dict:
        end = time.monotonic() + perf_limit(timeout)
        while time.monotonic() < end:
            try:
                item = self.events.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    break
                continue
            if item["event"] == event:
                return item
        raise AssertionError(f"evento «{event}» non ricevuto; stderr:\n" + "".join(self.stderr)[-3000:])

    def command(self, line: str, event: str | None = None) -> dict:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.wait(event or line.split(" ", 1)[0])

    def kill(self):
        """Uccisione forzata, come «taskkill /F»: nessuna pulizia del processo."""
        if self.proc.poll() is None:
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True,
                               **platform.subprocess_kwargs())
            else:
                self.proc.kill()
        self.proc.wait(timeout=30)

    def quit(self):
        if self.proc.poll() is None:
            try:
                self.command("quit")
                self.proc.wait(timeout=30)
            except Exception:
                self.kill()


@pytest.fixture
def run(profile):
    started: list[Foliant] = []

    def start(path) -> Foliant:
        f = Foliant(profile, path)
        started.append(f)
        return f
    yield start
    for f in started:
        try:
            f.quit()
        except Exception:
            pass


def _open(run, path) -> dict:
    return run(path).wait("opened")


def _assert_refused(state: dict, path) -> None:
    assert state["opened"] == 0, state
    name = Path(path).name
    assert any(name.lower() in m.lower() or "già aperto" in m or "already open" in m for m in state["messages"]), state


# ------------------------------------------------------------------ due processi, due schede, due finestre
def test_second_process_does_not_open_the_same_file(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    _assert_refused(_open(run, pdf), pdf)
    assert first.command("state")["opened"] == 1          # il primo processo continua a tenerlo


def test_same_process_second_tab_or_window_reuses_the_open_one(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    assert first.command(f"open {pdf}")["opened"] == 1
    assert first.command(f"newwindow {pdf}")["opened"] == 1


# ------------------------------------------------------------------ stesso file, percorsi diversi
def _short_path(path: Path) -> str | None:
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.kernel32.GetShortPathNameW
    fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    fn.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(1024)
    n = fn(str(path), buf, len(buf))
    return buf.value if n and buf.value.lower() != str(path).lower() else None


def _free_drive() -> str | None:
    import ctypes
    used = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in reversed(list(enumerate(string.ascii_uppercase))):
        if letter > "F" and not used & (1 << i):
            return letter + ":"
    return None


def _variants(pdf: Path, tmp_path: Path, request) -> dict[str, str]:
    """Altri nomi dello stesso file; quelli non ottenibili su questa macchina vengono saltati."""
    kind = request.param
    if kind == "maiuscole":
        if not WINDOWS:
            pytest.skip("su Linux maiuscole e minuscole indicano file diversi")
        return {kind: str(pdf).swapcase()}
    if kind == "barre":
        if not WINDOWS:
            pytest.skip("le barre «\\» sono separatori solo su Windows")
        return {kind: str(pdf).replace("\\", "/")}
    if kind == "breve-8.3":
        if not WINDOWS:
            pytest.skip("nomi brevi 8.3 solo su Windows")
        short = _short_path(pdf)
        if not short:
            pytest.skip("nomi brevi 8.3 disattivati su questo volume")
        return {kind: short}
    if kind == "collegamento-fisico":
        link = tmp_path / "altro" / "copia_fisica.pdf"
        link.parent.mkdir(exist_ok=True)
        try:
            os.link(pdf, link)
        except OSError as e:
            pytest.skip(f"collegamento fisico non disponibile: {e}")
        return {kind: str(link)}
    if kind == "collegamento-simbolico":
        link = tmp_path / "altro" / "collegamento.pdf"
        link.parent.mkdir(exist_ok=True)
        try:
            os.symlink(pdf, link)
        except OSError as e:
            pytest.skip(f"collegamento simbolico non consentito: {e}")
        return {kind: str(link)}
    if kind == "unità-subst":
        if not WINDOWS:
            pytest.skip("unità di «subst» solo su Windows")
        drive = _free_drive()
        if drive is None:
            pytest.skip("nessuna lettera di unità libera")
        res = subprocess.run(["subst", drive, str(pdf.parent)], capture_output=True, **platform.subprocess_kwargs())
        if res.returncode != 0:
            pytest.skip("subst non riuscito")
        request.addfinalizer(lambda: subprocess.run(["subst", drive, "/D"], capture_output=True,
                                                    **platform.subprocess_kwargs()))
        return {kind: drive + "\\" + pdf.name}
    raise AssertionError(kind)


@pytest.fixture(params=["maiuscole", "barre", "breve-8.3", "collegamento-fisico", "collegamento-simbolico",
                        "unità-subst"])
def variant(request, tmp_path):
    pdf = _pdf(tmp_path / "Documenti Cliente" / f"{TOKEN} Contratto.pdf")
    (kind, other), = _variants(pdf, tmp_path, request).items()
    return pdf, other


def test_same_file_through_another_path_is_not_opened_twice(run, variant):
    pdf, other = variant
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    # nel secondo processo il file indicato con l'altro percorso non si apre
    assert _open(run, other)["opened"] == 0
    # nel primo processo l'altro percorso riporta alla scheda già aperta, senza una seconda
    assert first.command(f"open {other}")["opened"] == 1


def test_other_path_first_then_canonical(run, variant):
    pdf, other = variant
    first = run(other)
    assert first.wait("opened")["opened"] == 1
    assert _open(run, pdf)["opened"] == 0


# ------------------------------------------------------------------ nomi con spazi e accenti
def test_path_with_spaces_and_accents(run, tmp_path):
    pdf = _pdf(tmp_path / "Pratiche è già" / "Perizia Rolex – c’è l’acciaio àèìòù.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    _assert_refused(_open(run, pdf), pdf)


# ------------------------------------------------------------------ processo ucciso, chiusura, Salva con nome
def test_file_opens_again_after_the_holder_is_killed(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    first.kill()                                           # taskkill /F su Windows
    assert _open(run, pdf)["opened"] == 1                  # il blocco rimasto si riconosce come orfano


def test_closing_the_tab_releases_the_file(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    assert first.command("close")["opened"] == 0
    assert _open(run, pdf)["opened"] == 1


def test_save_as_moves_the_lock(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    new = tmp_path / "salvati" / f"{TOKEN} nuovo.pdf"
    new.parent.mkdir()
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    assert first.command(f"saveas {new}")["ok"]
    assert _open(run, pdf)["opened"] == 1                  # il vecchio file è libero
    assert _open(run, new)["opened"] == 0                  # il nuovo è tenuto dal primo processo


# ------------------------------------------------------------------ salvataggio con il blocco attivo
def test_save_while_locked(run, tmp_path):
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    assert first.command("save")["ok"]                     # il blocco non impedisce di salvare
    assert _open(run, pdf)["opened"] == 0                  # e resta dopo il salvataggio


def test_file_can_be_replaced_while_locked(run, tmp_path):
    """Il blocco non tiene il PDF aperto in modo esclusivo: un altro programma (o il salvataggio
    atomico, platform.replace_file) può sostituirlo anche su Windows."""
    pdf = _pdf(tmp_path / f"{TOKEN}.pdf")
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    tmp = tmp_path / "sostituto.tmp"
    shutil.copyfile(pdf, tmp)
    platform.replace_file(str(tmp), str(pdf))
    assert first.command("state")["opened"] == 1
    assert _open(run, pdf)["opened"] == 0


# ------------------------------------------------------------------ riservatezza
def _registry_dump() -> str:
    if not WINDOWS:
        return ""
    import winreg
    out: list[str] = []

    def walk(key, path):
        try:
            h = winreg.OpenKey(key, path)
        except OSError:
            return
        with h:
            i = 0
            while True:
                try:
                    name, value, _t = winreg.EnumValue(h, i)
                except OSError:
                    break
                out.append(f"{path}\\{name}={value!r}")
                i += 1
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(h, i)
                except OSError:
                    break
                walk(key, f"{path}\\{sub}")
                i += 1
    walk(winreg.HKEY_CURRENT_USER, r"Software\Foliant")
    return "\n".join(out)


def _traces(profile, needles: list[str]) -> list[str]:
    found = []
    for root, _dirs, files in os.walk(profile["base"]):
        for name in files:
            p = os.path.join(root, name)
            for n in needles:
                if n.lower() in name.lower():
                    found.append(f"nome di file: {p}")
            try:
                data = open(p, "rb").read()
            except OSError:
                continue
            for n in needles:
                for enc in ("utf-8", "utf-16-le"):
                    if n.encode(enc) in data or n.lower().encode(enc) in data.lower():
                        found.append(f"{n!r} ({enc}) in {p}")
    reg = _registry_dump()
    found += [f"registro: {n!r}" for n in needles if n.lower() in reg.lower()]
    return found


def test_lock_keeps_no_name_or_path_on_disk(run, profile, tmp_path):
    folder = tmp_path / f"Cartella{TOKEN}"
    pdf = _pdf(folder / f"{TOKEN} Riservato.pdf")
    needles = [TOKEN, pdf.stem, folder.name]
    first = run(pdf)
    assert first.wait("opened")["opened"] == 1
    assert _open(run, pdf)["opened"] == 0
    assert _traces(profile, needles) == []                 # durante l'apertura: solo un hash con chiave
    first.kill()
    assert _traces(profile, needles) == []                 # neppure nel blocco rimasto dopo il crash
    third = run(pdf)
    assert third.wait("opened")["opened"] == 1
    third.quit()
    assert _traces(profile, needles) == []
