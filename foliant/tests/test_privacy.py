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


# ====================================================================== nomi e percorsi dei file
# Regola del committente: nessun nome, percorso o cartella dei file aperti o salvati resta su
# disco tra un avvio e l'altro (vedi foliant/ui/privacy.py e tests/test_no_file_traces.py).
def _qt_file_dialog_group() -> dict:
    from foliant.ui.privacy import file_dialog_state
    return file_dialog_state()


def _set_qt_file_dialog_group(values: dict) -> None:
    from PySide6.QtCore import QSettings
    s = QSettings(QSettings.Scope.UserScope, "QtProject")
    s.remove("FileDialog")
    s.beginGroup("FileDialog")
    for k, v in values.items():
        s.setValue(k, v)
    s.endGroup()
    s.sync()


def test_file_dialogs_are_not_native(window):
    from PySide6.QtCore import QCoreApplication, Qt
    assert QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)


def test_file_dialog_leaves_no_history_and_keeps_other_apps(window, tmp_path):
    """Il QFileDialog di Qt, distrutto, scrive le cartelle visitate in QtProject.conf: il nucleo
    rimette subito il gruppo com'era, compresi i valori di un'altra applicazione."""
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog
    before = _qt_file_dialog_group()
    _set_qt_file_dialog_group({"history": ["file:///altra/app"], "lastVisited": "file:///altra/app"})
    try:
        folder = tmp_path / "zq7 cartella segreta"
        folder.mkdir()
        dialog = QFileDialog(window, "Apri", str(folder))
        dialog.show()
        dialog.setDirectory(str(folder))
        dialog.done(0)
        dialog.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        path = QSettings(QSettings.Scope.UserScope, "QtProject").fileName()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "zq7" not in text and "altra/app" in text
        state = _qt_file_dialog_group()
        assert state.get("lastVisited") == "file:///altra/app"
        assert state.get("history") in (["file:///altra/app"], "file:///altra/app")
    finally:
        _set_qt_file_dialog_group(before)


def test_legacy_settings_are_removed(tmp_path, qtbot):
    from PySide6.QtCore import QSettings

    from foliant.ui import privacy
    from foliant.ui.main_window import MainWindow
    s = QSettings(str(tmp_path / "vecchie.ini"), QSettings.Format.IniFormat)
    s.setValue("recent", ["/home/u/zq7 vecchio.pdf"])
    s.setValue("recentPinned", ["/home/u/zq7 vecchio.pdf"])
    s.setValue("lastDirectory", "/home/u/zq7")
    s.setValue("view/guides/0123456789abcdef0123456789abcdef01234567", "[[\"h\", 10, null]]")
    s.setValue("theme", "dark")                                  # preferenza generale: resta
    win = MainWindow(s, load_modules=False)
    qtbot.addWidget(win)
    keys = s.allKeys()
    assert not [k for k in keys if k.startswith(("recent", "lastDirectory", "view/guides"))]
    assert s.value("theme") == "dark" and int(s.value(privacy.CLEANUP_KEY)) == privacy.CLEANUP_VERSION
    assert win.recent_files() == [] and win.last_directory() != "/home/u/zq7"
    # le chiavi di Foliant si tolgono a ogni avvio; i file condivisi una volta sola
    s.setValue("recent", ["/home/u/zq7 altro.pdf"])
    assert privacy.cleanup_legacy(s) is False
    assert not s.contains("recent")


def test_legacy_shared_files_cleaned_once(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from foliant.ui import privacy
    before = _qt_file_dialog_group()
    _set_qt_file_dialog_group({"history": ["file:///home/u/zq7"], "lastVisited": "file:///home/u/zq7",
                               "viewMode": "Detail"})
    data = tmp_path / "dati"
    (data / "RecentDocuments").mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (data / "RecentDocuments" / "zq7.pdf.desktop").write_text(
        "[Desktop Entry]\nURL[$e]=file:///home/u/zq7.pdf\nX-KDE-LastOpenedWith=foliant\n", encoding="utf-8")
    (data / "RecentDocuments" / "altro.txt.desktop").write_text(
        "[Desktop Entry]\nURL[$e]=file:///home/u/altro.txt\nX-KDE-LastOpenedWith=kate\n", encoding="utf-8")
    try:
        s = QSettings(str(tmp_path / "f.ini"), QSettings.Format.IniFormat)
        assert privacy.cleanup_legacy(s) is True
        # la cronologia dei dialoghi di Qt è condivisa e non dice chi l'ha scritta: non si tocca
        state = _qt_file_dialog_group()
        assert state.get("history") in (["file:///home/u/zq7"], "file:///home/u/zq7")
        assert state.get("lastVisited") == "file:///home/u/zq7" and state.get("viewMode") == "Detail"
        assert sorted(p.name for p in (data / "RecentDocuments").iterdir()) == ["altro.txt.desktop"]
        _set_qt_file_dialog_group({"history": ["file:///altra/app"]})
        assert privacy.cleanup_legacy(s) is False                  # una volta sola
        assert _qt_file_dialog_group().get("history") in (["file:///altra/app"], "file:///altra/app")
    finally:
        _set_qt_file_dialog_group(before)


def test_file_dialog_keeps_what_another_app_writes_while_it_is_open(window, tmp_path):
    """Un'altra applicazione Qt scrive la cronologia mentre un dialogo di Foliant è aperto: alla
    chiusura il dialogo di Foliant toglie solo ciò che ha scritto lui, i valori dell'altra restano."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QFileDialog
    before = _qt_file_dialog_group()
    _set_qt_file_dialog_group({"history": ["file:///prima"], "lastVisited": "file:///prima", "viewMode": "List"})
    try:
        folder = tmp_path / "zq7 dialogo aperto"
        folder.mkdir()
        dialog = QFileDialog(window, "Apri", str(folder))
        dialog.show()
        dialog.setDirectory(str(folder))
        QApplication.processEvents()
        _set_qt_file_dialog_group({"history": ["file:///durante/altra/app"],    # l'altra app
                                   "lastVisited": "file:///durante/altra/app", "viewMode": "List"})
        dialog.done(0)
        dialog.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        state = _qt_file_dialog_group()
        assert "zq7" not in repr(state)
        assert state.get("lastVisited") == "file:///durante/altra/app"
        assert state.get("history") in (["file:///durante/altra/app"], "file:///durante/altra/app")
    finally:
        _set_qt_file_dialog_group(before)


_OTHER_APP = r'''
import sys
from PySide6.QtCore import QSettings
s = QSettings(QSettings.Scope.UserScope, "QtProject")
s.beginGroup("FileDialog")
s.setValue("history", [sys.argv[1]])
s.setValue("lastVisited", sys.argv[1])
s.endGroup()
s.sync()
'''

_FOLIANT_SESSION = r'''
import subprocess, sys
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog
from foliant.ui import privacy
privacy.prepare()
app = QApplication([])
privacy.install(app)
folder, other_app, other_value = sys.argv[1], sys.argv[2], sys.argv[3]

def scenario():
    # un dialogo di Foliant visita la cartella del documento
    d = QFileDialog(None, "Apri", folder)
    d.show(); d.setDirectory(folder); d.done(0); d.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # un'altra applicazione Qt (un altro processo) scrive la sua cronologia mentre Foliant è aperto
    subprocess.run([sys.executable, "-c", other_app, other_value], check=True)
    # un secondo dialogo di Foliant, chiuso con il metodo statico come fanno i moduli
    QTimer.singleShot(100, lambda: [w.reject() for w in QApplication.topLevelWidgets()
                                    if isinstance(w, QFileDialog) and w.isVisible()])
    QFileDialog.getExistingDirectory(None, "Cartella", folder)
    QTimer.singleShot(50, app.quit)

QTimer.singleShot(0, scenario)
sys.exit(app.exec())
'''


def test_other_app_history_survives_foliant_exit(tmp_path):
    """Processo vero: un'altra «app» scrive la cronologia dei dialoghi mentre Foliant è aperto;
    dopo l'uscita di Foliant i suoi valori restano e le cartelle dei dialoghi di Foliant no."""
    config = tmp_path / "config"
    (config / "QtProject.conf").parent.mkdir(parents=True)
    (config / "QtProject.conf").write_text(
        "[FileDialog]\nhistory=file:///altra/prima\nlastVisited=file:///altra/prima\nviewMode=Detail\n",
        encoding="utf-8")
    folder = tmp_path / "zq7 cartella di foliant"
    folder.mkdir()
    env = dict(os.environ, XDG_CONFIG_HOME=str(config), HOME=str(tmp_path / "home"),
               XDG_DATA_HOME=str(tmp_path / "dati"), XDG_STATE_HOME=str(tmp_path / "stato"),
               QT_QPA_PLATFORM="offscreen")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", _FOLIANT_SESSION, str(folder), _OTHER_APP, "file:///altra/durante"],
                       cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (config / "QtProject.conf").read_text(encoding="utf-8")
    assert "zq7" not in text, text
    assert "lastVisited=file:///altra/durante" in text and "history=file:///altra/durante" in text, text
    assert "viewMode=Detail" in text, text


XBEL = """<?xml version="1.0" encoding="UTF-8"?>
<xbel version="1.0"
      xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"
      xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info"
>
  <bookmark href="file:///home/u/zq7-solo.pdf" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info>
      <metadata owner="http://freedesktop.org">
        <mime:mime-type type="application/pdf"/>
        <bookmark:applications>
          <bookmark:application name="Foliant" exec="&apos;foliant %u&apos;" modified="2026-01-01T00:00:00Z" count="1"/>
        </bookmark:applications>
      </metadata>
    </info>
  </bookmark>
  <bookmark href="file:///home/u/condiviso.pdf" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info>
      <metadata owner="http://freedesktop.org">
        <mime:mime-type type="application/pdf"/>
        <bookmark:applications>
          <bookmark:application name="foliant" exec="&apos;foliant %u&apos;" modified="2026-01-01T00:00:00Z" count="1"/>
          <bookmark:application name="Evince" exec="&apos;evince %u&apos;" modified="2026-01-01T00:00:00Z" count="2"/>
        </bookmark:applications>
      </metadata>
    </info>
  </bookmark>
  <bookmark href="file:///home/u/altro.txt" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info>
      <metadata owner="http://freedesktop.org">
        <mime:mime-type type="text/plain"/>
        <bookmark:applications>
          <bookmark:application name="gedit" exec="&apos;gedit %u&apos;" modified="2026-01-01T00:00:00Z" count="1"/>
        </bookmark:applications>
      </metadata>
    </info>
  </bookmark>
</xbel>
"""


def test_recently_used_keeps_other_applications(tmp_path):
    import xml.dom.minidom

    from foliant.ui.privacy import clean_recently_used
    path = tmp_path / "recently-used.xbel"
    path.write_text(XBEL, encoding="utf-8")
    assert clean_recently_used(path) is True
    text = path.read_text(encoding="utf-8")
    xml.dom.minidom.parseString(text)                              # ancora XML valido
    assert "zq7-solo" not in text and "foliant" not in text.lower()
    assert "condiviso.pdf" in text and "evince" in text            # aperto anche da Evince: resta per lui
    assert "altro.txt" in text and "gedit" in text
    assert clean_recently_used(path) is False                      # nulla da fare
    assert clean_recently_used(tmp_path / "manca.xbel") is False
