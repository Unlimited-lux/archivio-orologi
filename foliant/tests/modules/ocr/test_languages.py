"""Test delle lingue OCR e della gestione dei pacchetti (OCR-03)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from foliant.modules.ocr import languages as L

pytestmark = pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract non installato")


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    udir = tmp_path / "tessdata-utente"
    monkeypatch.setenv("FOLIANT_TESSDATA_DIR", str(udir))
    L._reset_caches()
    yield udir
    L._reset_caches()


def _system_pack(code: str = "eng") -> Path:
    sysdir = L.system_tessdata_dir()
    assert sysdir is not None
    return sysdir / f"{code}.traineddata"


def _free_code() -> str:
    for code in ("fry", "vie", "sqi", "yid"):
        if code not in L.available_languages():
            return code
    pytest.skip("nessun codice libero per il test")


def _probe_png(tmp_path) -> Path:
    path = tmp_path / "probe.png"
    L._make_probe_png(path)
    return path


def test_catalog_contents():
    assert len(L.CATALOG) >= 38
    assert L.CATALOG["ara"].rtl and L.CATALOG["heb"].rtl
    assert L.CATALOG["jpn_vert"].vertical and L.CATALOG["kor_vert"].vertical
    for code in ("chi_sim", "chi_tra", "hin", "ell", "rus", "ita", "eng"):
        assert code in L.CATALOG
    assert all(info.code == code for code, info in L.CATALOG.items())
    assert L.language_name("deu") == "tedesco"
    assert L.language_name("zzz") == "zzz"


def test_preinstalled_first():
    langs = L.available_languages()
    assert langs[:2] == ["ita", "eng"]
    assert "osd" not in langs
    assert L.has_osd() == _system_pack("osd").exists()
    assert L.tessdata_dir() == str(L.system_tessdata_dir())


def test_check_languages():
    L.check_languages(["ita", "eng"])
    with pytest.raises(L.MissingLanguagesError) as exc:
        L.check_languages(["ita", "deu"])
    assert exc.value.codes == ["deu"]
    assert "tedesco" in str(exc.value)
    with pytest.raises(L.LanguagePackError, match="almeno una lingua"):
        L.check_languages([])
    assert L.missing_languages(["deu", "fra", "deu", "ita"]) == ["deu", "fra"]


def test_install_use_verify_remove(tmp_path, user_dir):
    code = _free_code()
    pack = tmp_path / f"{code}.traineddata"
    shutil.copyfile(_system_pack("eng"), pack)
    assert code not in L.available_languages()  # popola la cache

    assert L.install_from_file(pack) == code
    assert code in L.available_languages()
    assert L.user_installed_languages() == [code]
    assert L.tessdata_dir() == str(user_dir)
    assert L.tessdata_dir() == str(user_dir)  # ripetibile
    # La cartella unita espone anche i pacchetti di sistema.
    assert (user_dir / "ita.traineddata").exists()
    assert "ita" in L.available_languages()

    proc = subprocess.run(
        ["tesseract", str(_probe_png(tmp_path)), "stdout", "--tessdata-dir",
         L.tessdata_dir(), "-l", f"ita+{code}"],
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    assert L.verify_installed(code)
    assert L.verify_installed("ita")
    with open(user_dir / f"{code}.traineddata", "r+b") as fh:
        fh.seek(-4, os.SEEK_END)
        fh.write(b"\x00\x01\x02\x03")
    assert not L.verify_installed(code)

    # Reinstallare sostituisce il pacchetto.
    L.install_from_file(pack)
    assert L.verify_installed(code)

    L.remove_language(code)
    assert code not in L.available_languages()
    assert L.user_installed_languages() == []
    assert not (user_dir / f"{code}.traineddata").exists()
    assert L.tessdata_dir() == str(L.system_tessdata_dir())


def test_install_with_explicit_code(tmp_path):
    code = _free_code()
    pack = tmp_path / "pacchetto.bin"
    shutil.copyfile(_system_pack("eng"), pack)
    assert L.install_from_file(pack, code) == code
    assert code in L.available_languages()


def test_invalid_code_refused(tmp_path):
    pack = tmp_path / "Not-A-Code.traineddata"
    shutil.copyfile(_system_pack("eng"), pack)
    with pytest.raises(L.LanguagePackError, match="non valido"):
        L.install_from_file(pack)


def test_damaged_packs_refused(tmp_path, user_dir):
    data = _system_pack("eng").read_bytes()
    truncated = tmp_path / "fry.traineddata"
    truncated.write_bytes(data[: len(data) // 2])
    with pytest.raises(L.LanguagePackError):
        L.install_from_file(truncated)

    noise = tmp_path / "vie.traineddata"
    noise.write_bytes(os.urandom(200_000))
    with pytest.raises(L.LanguagePackError):
        L.install_from_file(noise)

    altered = tmp_path / "sqi.traineddata"
    buf = bytearray(data)
    buf[200:5000] = bytes(4800)
    altered.write_bytes(bytes(buf))
    with pytest.raises(L.LanguagePackError, match="motore OCR"):
        L.install_from_file(altered)

    tiny = tmp_path / "yid.traineddata"
    tiny.write_bytes(b"abc")
    with pytest.raises(L.LanguagePackError, match="troppo piccolo"):
        L.install_from_file(tiny)

    with pytest.raises(L.LanguagePackError):
        L.install_from_file(tmp_path / "manca.traineddata")

    assert L.user_installed_languages() == []


def test_remove_refused():
    with pytest.raises(L.LanguagePackError, match="non si possono rimuovere"):
        L.remove_language("ita")
    with pytest.raises(L.LanguagePackError, match="non si possono rimuovere"):
        L.remove_language("eng")
    if L.has_osd():
        with pytest.raises(L.LanguagePackError, match="sistema"):
            L.remove_language("osd")
    with pytest.raises(L.LanguagePackError, match="non è installata"):
        L.remove_language("deu")


class _FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.status = 200
        self.headers = {"Content-Length": str(len(data))}


class _Progress:
    def __init__(self, cancel_after=None):
        self.calls = []
        self.cancel_after = cancel_after

    def update(self, fraction, text):
        self.calls.append((fraction, text))
        if self.cancel_after is not None and len(self.calls) >= self.cancel_after:
            raise KeyboardInterrupt("annullato")


def test_download_language(monkeypatch):
    code = _free_code()
    data = _system_pack("eng").read_bytes()
    urls = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return _FakeResponse(data)

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    progress = _Progress()
    assert L.download_language(code, progress=progress, base_url="https://example.invalid/x") == code
    assert urls == [f"https://example.invalid/x/{code}.traineddata"]
    assert progress.calls and progress.calls[-1][0] == 1.0
    assert code in L.available_languages()
    assert L.verify_installed(code)


def test_download_cancel_and_errors(monkeypatch):
    code = _free_code()
    data = _system_pack("eng").read_bytes()
    monkeypatch.setattr(L.urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse(data))
    with pytest.raises(KeyboardInterrupt):
        L.download_language(code, progress=_Progress(cancel_after=1))
    assert code not in L.available_languages()

    def failing(url, timeout=None):
        raise urllib.error.URLError("rete assente")

    monkeypatch.setattr(L.urllib.request, "urlopen", failing)
    with pytest.raises(L.LanguagePackError, match="Download non riuscito"):
        L.download_language(code)

    monkeypatch.setattr(L.urllib.request, "urlopen",
                        lambda url, timeout=None: _FakeResponse(b"<html>404</html>" * 200))
    with pytest.raises(L.LanguagePackError):
        L.download_language(code)
    assert L.user_installed_languages() == []


def test_tesseract_info():
    assert L.tesseract_available()
    assert L.tesseract_version().startswith("tesseract")
