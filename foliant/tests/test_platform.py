"""Differenze tra sistemi (foliant/core/platform.py): stesse prove su Linux e su Windows."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from foliant.core import platform, winscan


def test_user_dirs_follow_environment(tmp_path):
    if platform.IS_WINDOWS:
        env = {"APPDATA": str(tmp_path / "r"), "LOCALAPPDATA": str(tmp_path / "l")}
        assert platform.data_home(env) == tmp_path / "r"
        assert platform.config_home(env) == tmp_path / "r"
        assert platform.state_home(env) == tmp_path / "l"
        assert platform.cache_home(env) == tmp_path / "l"
    else:
        env = {"XDG_DATA_HOME": str(tmp_path / "d"), "XDG_CONFIG_HOME": str(tmp_path / "c"),
               "XDG_STATE_HOME": str(tmp_path / "s"), "XDG_CACHE_HOME": str(tmp_path / "k")}
        assert platform.data_home(env) == tmp_path / "d"
        assert platform.config_home(env) == tmp_path / "c"
        assert platform.state_home(env) == tmp_path / "s"
        assert platform.cache_home(env) == tmp_path / "k"
        assert platform.data_home({}) == Path.home() / ".local" / "share"


def test_isolated_profile_of_the_tests():
    """conftest isola il profilo anche su Windows: niente scritture nel profilo vero."""
    for kind in ("data", "config", "state"):
        assert "utente" in str(platform.app_dir(kind)), platform.app_dir(kind)


def test_portable_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FOLIANT_PORTABLE_DIR", str(tmp_path / "dati"))
    assert platform.app_dir("data") == tmp_path / "dati" / "roaming" / "foliant"
    assert platform.app_dir("state") == tmp_path / "dati" / "local" / "foliant"


def test_private_dir(tmp_path):
    folder = platform.make_private_dir(tmp_path / "a" / "b")
    assert folder.is_dir() and platform.private_dir(folder)
    assert not platform.private_dir(tmp_path / "manca")
    f = tmp_path / "file"
    f.write_text("x")
    assert not platform.private_dir(f)


def test_pid_alive_does_not_touch_other_processes():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **platform.subprocess_kwargs())
    try:
        assert platform.pid_alive(proc.pid)
        # su Windows os.kill(pid, 0) terminerebbe il processo: deve essere ancora vivo
        time.sleep(0.2)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
    assert not platform.pid_alive(proc.pid)
    assert platform.pid_alive(os.getpid())
    assert not platform.pid_alive(0) and not platform.pid_alive(-3) and not platform.pid_alive("x")
    assert not platform.pid_alive(2 ** 40)


def test_kill_tree_terminates_group():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            **platform.subprocess_kwargs(new_group=True))
    platform.kill_tree(proc)
    assert proc.wait(timeout=15) is not None


def test_file_lock_is_exclusive(tmp_path):
    lock = tmp_path / ".lock"
    order = []

    def other():
        with platform.file_lock(lock):
            order.append("altro")

    with platform.file_lock(lock):
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        order.append("primo")
    t.join(15)
    assert order == ["primo", "altro"]


def test_replace_file(tmp_path):
    src, dst = tmp_path / "nuovo", tmp_path / "vecchio"
    src.write_text("nuovo")
    dst.write_text("vecchio")
    platform.replace_file(src, dst)
    assert dst.read_text() == "nuovo" and not src.exists()


@pytest.mark.skipif(not platform.IS_WINDOWS, reason="su Linux un file aperto si sostituisce sempre")
def test_replace_file_in_use_gives_clear_error(tmp_path):
    """Un altro programma tiene aperto il file senza condivisione: dopo i tentativi, FileInUseError
    (e l'errore di salvataggio dice «in uso da un altro programma»)."""
    import ctypes
    from ctypes import wintypes

    from foliant.core.document import atomic_write
    from foliant.core.errors import DocumentWriteError

    dst = tmp_path / "aperto.pdf"
    dst.write_bytes(b"%PDF-1.4 vecchio")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
    handle = kernel32.CreateFileW(str(dst), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)   # nessuna condivisione
    try:
        src = tmp_path / "nuovo"
        src.write_bytes(b"nuovo")
        with pytest.raises(platform.FileInUseError):
            platform.replace_file(src, dst, attempts=3, delay=0.01)
        with pytest.raises(DocumentWriteError) as exc:
            atomic_write(dst, b"%PDF-1.4 nuovo")
        assert "in uso" in str(exc.value)
        assert not [p for p in tmp_path.iterdir() if p.name.startswith(".foliant-")]   # nessun temporaneo
    finally:
        kernel32.CloseHandle(handle)
    assert dst.read_bytes() == b"%PDF-1.4 vecchio"


def test_find_tool_override_and_cache(tmp_path, monkeypatch):
    fake = tmp_path / ("tesseract.exe" if platform.IS_WINDOWS else "tesseract")
    fake.write_text("")
    monkeypatch.setenv("FOLIANT_TESSERACT", str(fake))
    platform.forget_tools()
    try:
        assert platform.find_tool("tesseract") == str(fake)
    finally:
        monkeypatch.delenv("FOLIANT_TESSERACT")
        platform.forget_tools()
    assert platform.find_tool("programma-che-non-esiste") is None


@pytest.mark.skipif(not platform.IS_WINDOWS, reason="nomi e posizioni di Windows")
def test_find_tool_windows_locations():
    gs = platform.find_tool("gs")
    if gs is not None:
        assert os.path.basename(gs).lower() in ("gswin64c.exe", "gswin32c.exe", "gs.exe")
    tess = platform.find_tool("tesseract")
    if tess is not None:
        assert tess.lower().endswith("tesseract.exe")


def test_python_command_runs_python():
    out = subprocess.run(platform.python_command("-I", "-c", "print(6 * 7)"), capture_output=True, text=True,
                         timeout=60, **platform.subprocess_kwargs())
    assert out.stdout.strip() == "42"


def test_subprocess_kwargs():
    kw = platform.subprocess_kwargs()
    if platform.IS_WINDOWS:
        assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert platform.subprocess_kwargs(new_group=True)["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kw == {} and platform.subprocess_kwargs(new_group=True) == {"start_new_session": True}


def test_fonts():
    assert platform.font_dirs()
    assert platform.font_file("sans") and os.path.isfile(platform.font_file("sans"))
    assert platform.font_file("unicode") and os.path.isfile(platform.font_file("unicode"))
    if platform.IS_WINDOWS:
        assert any(p.lower().endswith("arial.ttf") for p in platform.windows_fonts("sans"))
        assert "arial" in platform.installed_font_families()
        assert platform.font_file_for_family("Arial").lower().endswith(".ttf")
    else:
        assert platform.windows_fonts("sans") == ()
    assert platform.font_file_for_family("Famiglia inesistente 123") is None


def test_missing_tool_message():
    from foliant.i18n import set_language
    msg = platform.missing_tool_message("gs")
    assert "Ghostscript" in msg and "Reinstalla Foliant" in msg
    set_language("en")
    try:
        assert "Reinstall Foliant" in platform.missing_tool_message("qpdf")
        assert "LibreOffice" in platform.missing_tool_message("soffice")
    finally:
        set_language("it")


def test_user_full_name():
    assert isinstance(platform.user_full_name(), str) and platform.user_full_name()


def test_remove_user_data_only_foliant(tmp_path):
    for kind in ("data", "config", "state"):
        d = platform.app_dir(kind)
        d.mkdir(parents=True, exist_ok=True)
        (d / "prova.json").write_text("{}")
    other = platform.data_home() / "altra-applicazione"
    other.mkdir(parents=True, exist_ok=True)
    removed = platform.remove_user_data()
    assert removed
    for kind in ("data", "config", "state"):
        assert not platform.app_dir(kind).exists()
    assert other.is_dir()


@pytest.mark.skipif(not platform.IS_WINDOWS, reason="named pipe e SID di Windows")
def test_windows_instance_name_has_sid_and_session():
    from foliant.ui.instance import instance_name
    sid = platform.windows_user_sid()
    assert sid and sid.startswith("S-1-")
    name = instance_name()
    assert sid in name and name.endswith(f"-{platform.windows_session_id() or 0}")


def test_sapi_engine_never_puts_text_on_command_line():
    from foliant.modules.viewer.speech_engine import SapiEngine
    engine = SapiEngine()
    args = engine._args("", "it-IT", 1.5, 0.3, "Microsoft Elsa's voice")
    script = args[-1]
    assert "$s.Rate=5" in script and "'Microsoft Elsa''s voice'" in script
    args = engine._args("", "it-IT; Remove-Item C:\\", 1.0, 0, None)       # lingua non valida: ignorata
    assert "Remove-Item" not in args[-1]
    voices = engine._parse_voices("Microsoft Elsa Desktop\tit-IT\nMicrosoft Zira Desktop\ten-US\n")
    assert [(v.id, v.language) for v in voices] == [("Microsoft Elsa Desktop", "it-IT"),
                                                    ("Microsoft Zira Desktop", "en-US")]
    assert engine.available() == (platform.IS_WINDOWS and bool(__import__("shutil").which("powershell")))


def test_wia_error_messages():
    assert "vuoto" in winscan.error_message("ERR 80210003 paper empty")
    assert "inceppata" in winscan.error_message("ERR 80210002")
    assert "non risponde" in winscan.error_message("NODEVICE")
    assert "occupato" in winscan.error_message("ERR 80210006")
    assert "Acquisizione non riuscita" in winscan.error_message("ERR 12345678 qualcosa")
    if not platform.IS_WINDOWS:
        assert not winscan.available() and winscan.list_devices() == []


def test_regex_guard_times_out_without_select():
    """Il controllo delle espressioni regolari in un processo separato funziona anche dove
    select() sulle pipe non esiste (Windows)."""
    from foliant.modules.viewer.advsearch_engine import RegexGuard
    guard = RegexGuard(r"(a+)+$", 0, timeout=1.0)
    try:
        assert guard.check(["aaaa"])
        assert not guard.check(["a" * 40 + "b"])          # tempo patologico: processo terminato
        assert guard.check(["aaaa"])                       # e riavviato alla richiesta successiva
    finally:
        guard.close()


def test_nbsp_safe_font_gives_real_spaces(tmp_path, monkeypatch):
    """Font come Arial (stesso glifo per U+0020 e U+00A0): il testo inserito da Foliant deve
    estrarsi con spazi veri. Su Windows si usa una copia del font senza la voce U+00A0."""
    import pymupdf
    from fontTools.ttLib import TTFont
    source = platform.font_file("sans")
    font = TTFont(source)
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap[0xA0] = table.cmap[0x20]
    shared = tmp_path / "condiviso.ttf"
    font.save(str(shared))
    monkeypatch.setattr(platform, "IS_WINDOWS", True)

    def text_with(path):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_font(fontname="F", fontfile=path)
        page.insert_text((50, 50), "Solo testo", fontname="F")
        return page.get_text()

    fixed = platform.nbsp_safe_font(str(shared))
    assert fixed != str(shared) and "utente" in fixed                     # copia nella cache isolata
    assert text_with(fixed).strip() == "Solo testo"
    assert platform.nbsp_safe_font(str(shared)) == fixed                  # riusata
    plain = tmp_path / "normale.ttf"
    TTFont(source).save(str(plain))
    if 0xA0 not in TTFont(str(plain)).getBestCmap():
        assert platform.nbsp_safe_font(str(plain)) == str(plain)          # nessun problema: invariato
