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


def test_table_detection_still_works_without_layout_analysis():
    """Escludere l'analisi del layout non deve rompere le funzioni di pymupdf che ne
    controllano la presenza (find_tables chiama importlib.util.find_spec("pymupdf.layout"))."""
    import importlib.util
    import sys

    import pymupdf
    assert "onnxruntime" not in sys.modules
    importlib.util.find_spec("pymupdf.layout")          # non solleva
    d = pymupdf.open()
    page = d.new_page()
    for r in range(4):
        for c in range(3):
            x, y = 72 + c * 120, 100 + r * 30
            page.draw_rect(pymupdf.Rect(x, y, x + 120, y + 30), color=(0, 0, 0), width=1)
            page.insert_text((x + 5, y + 20), f"R{r}C{c}", fontsize=10)
    tables = page.find_tables().tables
    assert tables and tables[0].row_count == 4 and tables[0].col_count == 3
