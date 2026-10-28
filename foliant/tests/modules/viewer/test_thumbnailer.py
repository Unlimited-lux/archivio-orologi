"""VIS-53 — Generatore di miniature per il file manager."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from PySide6.QtGui import QImage

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _run(*args, timeout=10):
    t = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "foliant.modules.viewer.thumbnailer", *args], cwd=ROOT,
                       capture_output=True, timeout=timeout)
    return p.returncode, time.perf_counter() - t


def test_valid_pdf_thumbnail_within_2s(sample_pdf, tmp_path):
    out = tmp_path / "t.png"
    code, elapsed = _run("-s", "128", "file://" + sample_pdf, str(out))
    assert code == 0 and elapsed < 2.0 + 1.5   # avvio dell'interprete incluso
    img = QImage(str(out))
    assert max(img.width(), img.height()) == 128


def test_encrypted_produces_no_image(encrypted_pdf, tmp_path):
    out = tmp_path / "t.png"
    code, _ = _run(encrypted_pdf, str(out))
    assert code == 2 and not out.exists()


def test_malformed_terminates(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + os.urandom(4000))
    out = tmp_path / "t.png"
    code, elapsed = _run(str(bad), str(out), "--time-limit", "2")
    assert code != 0 and elapsed < 5
    assert not out.exists()
