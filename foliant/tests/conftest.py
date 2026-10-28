"""Configurazione comune dei test: Qt senza schermo e PDF di esempio generati al volo."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FOLIANT_SYNC_TASKS", "1")

import warnings

import foliant  # noqa: F401  (prima di pymupdf: stessa configurazione dell'app, senza analisi del layout)
import pymupdf
import pytest


@pytest.fixture(autouse=True)
def _flush_deferred_deletes():
    """Distrugge a fine test gli oggetti con deleteLater (finestre e viste chiuse).

    Senza un ciclo di eventi vero si accumulerebbero, e il primo qtbot.wait (o un cambio di
    tema) di un test successivo li distruggerebbe o ristilizzerebbe tutti insieme."""
    yield
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is not None:
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _isolated_user_dirs(monkeypatch, tmp_path_factory):
    """Ogni test ha cartelle utente proprie (dati, stato, configurazione, recupero): niente
    file lasciati da altri test o da una sessione vera (es. il recupero dopo un arresto
    anomalo, che mostrerebbe un avviso). La cache resta condivisa (fontconfig è lento)."""
    base = tmp_path_factory.mktemp("utente")
    for var, name in (("XDG_DATA_HOME", "dati"), ("XDG_STATE_HOME", "stato"),
                      ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(var, str(base / name))
    monkeypatch.setenv("FOLIANT_RECOVERY_DIR", str(base / "recupero"))


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """Rete di sicurezza: nei test nessuna finestra modale reale può bloccare la suite.

    Le finestre di dialogo non simulate dal test si chiudono subito (Annulla/No) con un
    avviso. I test che vogliono una risposta precisa la simulano con monkeypatch, come sempre.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

    def warn(what):
        warnings.warn(f"finestra modale non simulata nel test: {what}", stacklevel=3)

    def box_exec(self, *a, **k):
        warn(f"QMessageBox «{self.text()[:60]}»")
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda *a, _n=name, **k: (warn(f"QMessageBox.{_n}"), QMessageBox.StandardButton.No)[1]))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: (warn(type(self).__name__), 0)[1])
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: (warn("QMenu"), None)[1])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (warn("QInputDialog.getText"), ("", False))[1]))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (warn("QInputDialog.getItem"), ("", False))[1]))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (warn("QInputDialog.getInt"), (0, False))[1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (warn("QFileDialog"), ("", ""))[1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (warn("QFileDialog"), ([], ""))[1]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (warn("QFileDialog"), ("", ""))[1]))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: (warn("QFileDialog"), "")[1]))
    yield


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
def settings(tmp_path_factory):
    """Impostazioni del test in una cartella propria: la finestra vi scrive subito (segno della
    pulizia dei dati delle versioni precedenti), senza mescolarsi ai file del test in tmp_path."""
    from PySide6.QtCore import QSettings
    s = QSettings(str(tmp_path_factory.mktemp("impostazioni") / "foliant-test.ini"), QSettings.Format.IniFormat)
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
        if not view.document.closed:
            view.document._saved_state = view.document._state  # niente richieste di salvataggio
        win.close_view(view)
