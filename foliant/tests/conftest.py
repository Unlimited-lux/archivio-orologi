"""Configurazione comune dei test: Qt senza schermo e PDF di esempio generati al volo."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FOLIANT_SYNC_TASKS", "1")

import pymupdf
import pytest


def make_pdf(path, pages: int = 3, text: str = "Pagina {n}", size=(595, 842), extra=None) -> str:
    """Crea un PDF con testo noto su ogni pagina e lo salva in `path`."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), text.format(n=i + 1), fontsize=24)
        page.insert_text((72, 140), "Orologeria Verdi - contratto di compravendita", fontsize=12)
        if extra:
            extra(page, i)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def sample_pdf(tmp_path):
    return make_pdf(tmp_path / "esempio.pdf")


@pytest.fixture
def encrypted_pdf(tmp_path):
    path = tmp_path / "protetto.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Riservato", fontsize=20)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="proprietario")
    doc.close()
    return str(path)


@pytest.fixture
def settings(tmp_path):
    from PySide6.QtCore import QSettings
    s = QSettings(str(tmp_path / "foliant-test.ini"), QSettings.Format.IniFormat)
    return s


@pytest.fixture
def window(qtbot, settings):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow

    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    yield win
    for view in list(win.views()):
        view.document._saved_state = view.document._state  # niente richieste di salvataggio
        win.close_view(view)
