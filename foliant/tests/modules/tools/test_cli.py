"""Elaborazione in blocco da riga di comando (BAT-22)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pymupdf

from foliant.modules.tools import cli

from .conftest import build_pdf, page_lines


def _folder(tmp_path, n=5):
    src = tmp_path / "ingresso"
    src.mkdir()
    for i in range(n):
        build_pdf(src / f"doc{i}.pdf", [page_lines(i)])
    return src


def test_headless_compress_folder(tmp_path):
    src = _folder(tmp_path)
    out = tmp_path / "uscita"
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM")}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    proc = subprocess.run([sys.executable, "-m", "foliant.modules.tools.cli", "--operazione", "compress",
                           "--destinazione", str(out), "-q", str(src)], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert len(os.listdir(out)) == 5


def test_damaged_file_exit_2_and_json_report(tmp_path):
    src = _folder(tmp_path, 2)
    (src / "rotto.pdf").write_bytes(b"%PDF-1.4 rotto")
    report = tmp_path / "rapporto.json"
    code = cli.main(["-o", "compress", "-d", str(tmp_path / "out"), "-r", str(report), "-q", str(src)],
                    stderr=io.StringIO())
    assert code == cli.EXIT_ERRORS
    data = json.loads(report.read_text(encoding="utf-8"))
    bad = [f for f in data["file"] if f["file_ingresso"].endswith("rotto.pdf")]
    assert bad and bad[0]["esito"] == "error" and bad[0]["codice"]


def test_password_as_argument_rejected(tmp_path):
    src = _folder(tmp_path, 1)
    err = io.StringIO()
    code = cli.main(["-o", "protect", "-p", "user_password=segreta", "-d", str(tmp_path / "out"), str(src)],
                    stderr=err)
    assert code == cli.EXIT_INVALID
    assert "--password-stdin" in err.getvalue()
    assert not (tmp_path / "out").exists()


def test_password_from_stdin_and_never_in_report(tmp_path):
    src = _folder(tmp_path, 2)
    report = tmp_path / "r.csv"
    code = cli.main(["-o", "protect", "--password-stdin", "-d", str(tmp_path / "out"), "-r", str(report), "-q",
                     str(src)], stdin=io.StringIO("user_password=apri\nowner_password=capo\n"),
                    stderr=io.StringIO())
    assert code == cli.EXIT_OK
    doc = pymupdf.open(str(tmp_path / "out" / "doc0.pdf"))
    assert doc.needs_pass and doc.authenticate("apri")
    text = report.read_text(encoding="utf-8")
    assert "apri" not in text and "capo" not in text


def test_locked_input_without_password_is_invalid(tmp_path, encrypted_pdf):
    code = cli.main(["-o", "compress", "-d", str(tmp_path / "out"), encrypted_pdf], stdin=io.StringIO(""),
                    stderr=io.StringIO())
    assert code == cli.EXIT_INVALID


def test_interrupt_leaves_no_partial_files(tmp_path, monkeypatch):
    src = _folder(tmp_path, 6)
    out = tmp_path / "out"
    original = cli._Progress.update
    calls = {"n": 0}

    def interrupted(self, fraction=None, text=None):
        calls["n"] += 1
        if calls["n"] == 3:
            self.cancel()          # come Ctrl+C / SIGTERM
        return original(self, fraction, text)
    monkeypatch.setattr(cli._Progress, "update", interrupted)
    code = cli.main(["-o", "compress", "-d", str(out), "-q", str(src)], stderr=io.StringIO())
    assert code == cli.EXIT_CANCELLED
    names = os.listdir(out)
    assert all(n.endswith(".pdf") and not n.startswith(".") for n in names)
    for n in names:
        assert pymupdf.open(str(out / n)).page_count == 1


def test_sequence_by_name_and_list(tmp_path, monkeypatch):
    from foliant.modules.tools import engine as be
    monkeypatch.setenv("FOLIANT_CONFIG_DIR", str(tmp_path / "conf"))
    be.save_sequence(str(tmp_path / "conf" / "foliant-sequenze"), "Bozze",
                     [be.Step("watermark", {"text": "BOZZA"})])
    src = _folder(tmp_path, 1)
    code = cli.main(["-s", "Bozze", "-d", str(tmp_path / "out"), "-q", str(src)], stderr=io.StringIO())
    assert code == cli.EXIT_OK
    assert "BOZZA" in pymupdf.open(str(tmp_path / "out" / "doc0.pdf"))[0].get_text()
    out = io.StringIO()
    assert cli.main(["--elenco-operazioni"], stdout=out) == 0
    assert "compress" in out.getvalue()
    assert cli.main(["-o", "inesistente", "-d", str(tmp_path), str(src)], stderr=io.StringIO()) == cli.EXIT_INVALID
