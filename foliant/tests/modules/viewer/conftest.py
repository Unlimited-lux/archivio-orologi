"""Fixture del modulo viewer."""

from __future__ import annotations

import pymupdf
import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Cartelle di stato e recupero isolate per ogni test."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FOLIANT_RECOVERY_DIR", str(tmp_path / "recovery"))


@pytest.fixture(autouse=True)
def _no_blocking_close_prompt(monkeypatch):
    """La chiusura della finestra a fine test non deve aprire la richiesta modale
    «Salvare le modifiche?» (la fixture `window` poi chiude i documenti senza salvare)."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)


@pytest.fixture
def viewer_window(window):
    """Finestra con il modulo viewer caricato."""
    assert "viewer" in [n for n, e in window.module_results if e is None]
    return window


def part(window, name):
    return window.ctx.viewer_parts[name]


@pytest.fixture
def long_pdf(tmp_path):
    """PDF di 30 pagine con testo distinto per pagina."""
    path = tmp_path / "lungo.pdf"
    doc = pymupdf.open()
    for i in range(30):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=24)
        page.insert_text((72, 140), f"Contenuto numero {i + 1} del documento", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)
