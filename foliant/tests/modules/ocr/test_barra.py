"""Scheda OCR in stile Excel: gruppi con nome nella barra e opzione nella scheda File › Opzioni."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

import foliant.modules.ocr as pkg


def test_gruppi_con_nome_nella_barra(window):
    ctl = window.ctx.ocr
    a = ctl.actions
    bar = window.ctx.mode("ocr").toolbar
    assert bar.groups() == [
        ("Riconosci", [a["run"], a["edit"], a["text"]]),
        ("Correggi", [a["area"], a["review"]]),
        ("Livello di testo", [a["remove"], a["image_only"]]),
        ("Lotti", [a["batch"], a["resume"], a["languages"]]),
    ]
    # etichette brevi nella barra, nome intero nei menu e nella ricerca dei comandi
    assert a["remove"].iconText() == "Rimuovi livello" and a["remove"].text() == "Rimuovi livello OCR"
    assert a["image_only"].iconText() == "Solo immagine"
    assert a["image_only"].text() == "Converti in PDF solo immagine"
    assert bar.widgetForAction(a["resume"]).toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not a["resume"].isEnabled()                       # nessun lotto interrotto in questa sessione
    assert a["batch"].isEnabled() and a["languages"].isEnabled()


def test_niente_doppione_di_esegui_ocr(window):
    """Il vecchio «Esegui OCR…» del menu ☰ è lo stesso comando del pulsante della barra: non esiste più."""
    texts = [act.text() for act in window.findChildren(QAction)]
    assert "Esegui OCR…" not in texts
    assert texts.count("Esegui OCR") == 1


def test_proposta_ocr_in_file_opzioni(window):
    ctl = window.ctx.ocr
    detect = ctl.actions["detect"]
    backstage = window.backstage
    assert backstage.page_of(detect) == "opzioni"
    entry = next(e for e in backstage.entries("opzioni") if e.action is detect)
    assert entry.group == "Apertura dei documenti" and entry.description
    initial = detect.isChecked()
    window.show_backstage("opzioni")
    tile = next(t for t in backstage.tiles if t.action is detect)
    assert tile.isEnabled() and tile.isCheckable()
    tile.click()                                             # cambia la spunta senza chiudere la scheda File
    assert detect.isChecked() != initial and ctl.detection_enabled() == detect.isChecked()
    assert window.backstage_visible()
    tile.click()
    assert detect.isChecked() == initial
    window.hide_backstage()


def test_nessuna_voce_in_altri_comandi(window):
    ctl = window.ctx.ocr
    for key, act in ctl.actions.items():
        assert window.backstage.page_of(act) in (None, "opzioni"), key
    for path in Path(pkg.__file__).parent.glob("*.py"):
        assert "add_menu_action(" not in path.read_text(encoding="utf-8"), path.name


@pytest.fixture
def english_window(qtbot, settings):
    from PySide6.QtWidgets import QApplication

    from foliant.i18n import language, set_language
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow
    previous = language()
    set_language("en")
    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    yield win
    set_language(previous)


def test_gruppi_tradotti(english_window):
    win = english_window
    bar = win.ctx.mode("ocr").toolbar
    assert [title for title, _acts in bar.groups()] == ["Recognize", "Correct", "Text layer", "Batches"]
    entry = next(e for e in win.backstage.entries("opzioni") if e.action is win.ctx.ocr.actions["detect"])
    assert entry.group == "Opening documents"
    assert entry.description == "When a scanned PDF is opened, suggests recognizing its text"
