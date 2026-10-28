"""NFR-19: avviando Foliant non si carica nulla che registri telemetria o identificativi."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_startup_loads_no_telemetry_component(tmp_path):
    env = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "cache"), HOME=str(tmp_path / "home"),
               XDG_DATA_HOME=str(tmp_path / "dati"), XDG_STATE_HOME=str(tmp_path / "stato"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), QT_QPA_PLATFORM="offscreen")
    env.pop("FOLIANT_LAYOUT", None)
    code = ("import sys; import foliant.core.engine, pymupdf; "
            "from PySide6.QtWidgets import QApplication; app = QApplication([]); "
            "from foliant.ui.main_window import MainWindow; from PySide6.QtCore import QSettings; "
            "w = MainWindow(QSettings()); "
            "bad = [m for m in ('onnxruntime', 'pymupdf.layout', 'pymupdf4llm') if m in sys.modules]; "
            "print('CARICATI', bad); sys.exit(1 if bad else 0)")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (tmp_path / "cache" / "Microsoft").exists()
    assert not (tmp_path / "home" / ".cache" / "Microsoft").exists()
