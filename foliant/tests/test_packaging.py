"""Voce del menu applicazioni, icona simbolica e comandi delle sue azioni rapide (NFR-32, VIS-53)."""

from __future__ import annotations

import configparser
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
SYMBOLIC = ROOT / "foliant" / "resources" / "foliant-symbolic.svg"


def _desktop_entry() -> configparser.ConfigParser:
    entry = configparser.ConfigParser(interpolation=None, strict=True)    # chiavi o sezioni doppie: errore
    entry.optionxform = str
    entry.read_string((PACKAGING / "foliant.desktop").read_text(encoding="utf-8"))
    return entry


def test_desktop_entry_has_keywords_and_quick_actions(tmp_path):
    from foliant.app import parse_args

    desktop = _desktop_entry()
    main = desktop["Desktop Entry"]
    keywords = main["Keywords"].split(";")
    keywords_en = main["Keywords[en]"].split(";")
    assert {"pdf", "modifica", "firma", "ocr", "modulo", "unisci", "converti"} <= set(keywords)
    assert {"pdf", "edit", "sign", "ocr", "form", "merge", "convert"} <= set(keywords_en)
    assert main["Exec"].split()[-1] == "%F"
    actions = [a for a in main["Actions"].split(";") if a]
    assert actions == ["new-window", "create-pdf", "merge"]
    expected = {
        "new-window": ("Nuova finestra", lambda o: o.new_window and o.command is None),
        "create-pdf": ("Crea PDF da file…", lambda o: o.command == "create-pdf"),
        "merge": ("Unisci file…", lambda o: o.command == "merge"),
    }
    for action in actions:
        section = desktop[f"Desktop Action {action}"]
        assert section["Name"] == expected[action][0] and section["Name[en]"]
        argv = shlex.split(section["Exec"].replace("@EXEC@", "/usr/bin/foliant"))
        assert argv[0] == "/usr/bin/foliant"
        options = parse_args([a for a in argv[1:] if not a.startswith("%")])
        assert not options.unknown and not options.files            # opzioni davvero supportate da app.py
        assert expected[action][1](options)
    if shutil.which("desktop-file-validate"):
        path = tmp_path / "foliant.desktop"
        path.write_text((PACKAGING / "foliant.desktop").read_text(encoding="utf-8")
                        .replace("@EXEC@", "/usr/bin/foliant"), encoding="utf-8")
        result = subprocess.run(["desktop-file-validate", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_symbolic_icon_is_monochrome_and_installed():
    from PySide6.QtSvg import QSvgRenderer

    root = ET.parse(SYMBOLIC).getroot()
    colors = set()
    for element in root.iter():
        assert "style" not in element.attrib
        for attr in ("fill", "stroke", "stop-color", "color"):
            if element.get(attr) not in (None, "none"):
                colors.add(element.get(attr))
    assert colors == {"currentColor"}                    # il desktop lo colora con il colore del tema
    assert "#" not in SYMBOLIC.read_text(encoding="utf-8")
    assert QSvgRenderer(SYMBOLIC.read_bytes()).isValid()
    install = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    uninstall = (PACKAGING / "uninstall.sh").read_text(encoding="utf-8")
    assert "hicolor/symbolic/apps" in install and "foliant/resources/foliant-symbolic.svg" in install
    assert "hicolor/symbolic/apps/foliant-symbolic.svg" in uninstall
    if shutil.which("bash"):
        for script in ("install.sh", "uninstall.sh"):
            assert subprocess.run(["bash", "-n", str(PACKAGING / script)]).returncode == 0


def test_create_and_merge_commands_open_their_dialogs(window, settings, monkeypatch, qtbot):
    """`foliant --create` / `--merge` (e le azioni del file .desktop) aprono «Crea PDF da file» e
    il dialogo «Unisci file»: comando registrato da un modulo o azione rapida della Home."""
    from PySide6.QtWidgets import QDialog

    from foliant.i18n import language, set_language
    from foliant.ui.instance import open_request
    from foliant.ui.main_window import MainWindow

    asked = []
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda title="", *a, **k: asked.append(title) or [])
    dialogs = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: dialogs.append(type(self).__name__) or 0)
    assert window.run_command("create-pdf")
    assert asked == ["Crea PDF da file"]
    assert window.run_command("merge")
    assert dialogs == ["MergeDialog"]
    # dall'istanza singola: il comando arriva con la richiesta e parte subito dopo
    open_request({"files": [], "new_window": False, "command": "create-pdf"}, settings, window)
    qtbot.waitUntil(lambda: len(asked) == 2)
    # un comando registrato da un modulo ha la precedenza; uno sconosciuto mostra la Home
    calls = []
    window.ctx.add_command("merge", lambda: calls.append("merge"))
    assert window.run_command("merge") and calls == ["merge"]
    assert not window.run_command("sconosciuto")
    assert window.docs_stack.currentWidget() is window.home
    # con l'interfaccia in inglese le azioni della Home hanno il titolo tradotto dal modulo
    previous = language()
    set_language("en")
    try:
        english = MainWindow(settings)
        qtbot.addWidget(english)
        monkeypatch.setattr(english.ctx, "ask_open_paths", lambda title="", *a, **k: asked.append(title) or [])
        assert english.run_command("create-pdf") and len(asked) == 3
    finally:
        set_language(previous)
