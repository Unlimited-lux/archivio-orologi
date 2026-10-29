"""Home e riservatezza: Foliant non conserva i file aperti, quindi la Home non ha l'elenco dei
documenti recenti (né filtro, fissati, «Svuota elenco») e il menu ☰ non ha «Apri recenti».
L'ultima cartella dei dialoghi resta solo in memoria per la sessione."""

from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget

from foliant.ui import theme

from .conftest import make_pdf


@pytest.fixture
def bare_window(qtbot, settings):
    """Finestra senza moduli: solo il nucleo."""
    from foliant.ui.main_window import MainWindow
    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings, load_modules=False)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    yield win


def test_home_has_no_recent_documents(bare_window, tmp_path):
    win = bare_window
    pdf = make_pdf(tmp_path / "zq7-segreto.pdf")
    view = win.open_path(pdf)
    win.close_view(view)
    win.show_home()
    QApplication.processEvents()
    home = win.home
    texts = [w.text() for w in home.findChildren(type(home.open_btn)) + home.findChildren(QLineEdit)]
    labels = [w.text() for w in home.findChildren(type(home.drop_hint))]
    assert not [t for t in labels + texts if "zq7" in t or "recent" in t.lower()]
    assert not [w for w in home.findChildren(QLineEdit) if w.isVisibleTo(home)]       # nessun filtro
    # `recent` resta per compatibilità con i moduli, vuoto e nascosto
    assert isinstance(home.recent, QListWidget) and home.recent.count() == 0
    assert not home.recent.isVisibleTo(home)
    home.set_recent([pdf], [pdf])
    assert home.recent.count() == 0 and home.visible_recent() == []


def test_file_tab_has_no_open_recent(bare_window, sample_pdf):
    win = bare_window
    win.open_path(sample_pdf)
    win.show_backstage("apri")
    texts = [a.text() for a in win.backstage.all_actions()]
    texts += [t.title_label.text() + " " + t.desc_label.text() for t in win.backstage.tiles]
    assert texts and not [t for t in texts if "recent" in t.lower() or "esempio" in t]
    win.hide_backstage()


def test_last_directory_only_in_memory(bare_window, settings, tmp_path, qtbot):
    from foliant.ui import main_window
    from foliant.ui.main_window import MainWindow
    folder = tmp_path / "zq7 cartella"
    folder.mkdir()
    pdf = make_pdf(folder / "a.pdf")
    bare_window.open_path(pdf)
    assert bare_window.last_directory() == str(folder)
    other = MainWindow(settings, load_modules=False)              # altra finestra, stessa sessione
    qtbot.addWidget(other)
    assert other.last_directory() == str(folder)
    settings.sync()
    assert "zq7" not in " ".join(f"{k}={settings.value(k)}" for k in settings.allKeys())
    with open(settings.fileName(), encoding="utf-8") as f:
        assert "zq7" not in f.read()
    main_window._session_directory = str(tmp_path / "sparita")   # cartella che non esiste più
    assert bare_window.last_directory() == os.path.expanduser("~")


def test_open_failure_is_logged_without_the_file_name(bare_window, tmp_path, monkeypatch, caplog):
    import logging

    from PySide6.QtWidgets import QMessageBox

    from foliant.core.document import Document
    pdf = make_pdf(tmp_path / "zq7-segreto.pdf")

    def broken(*a, **k):
        raise RuntimeError(f"motore guasto su {pdf}")
    monkeypatch.setattr(Document, "open", staticmethod(broken))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    with caplog.at_level(logging.DEBUG):
        assert bare_window.open_path(pdf) is None
    assert caplog.records and "zq7" not in caplog.text
