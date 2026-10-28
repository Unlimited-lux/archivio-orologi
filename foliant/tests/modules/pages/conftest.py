"""Fixture del modulo Pagine."""

from __future__ import annotations

import pymupdf
import pytest


def numbered_pdf(pages: int = 6, size=(595, 842), prefix: str = "P") -> pymupdf.Document:
    """Documento in memoria con il testo «P1», «P2»… su ciascuna pagina."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), f"{prefix}{i + 1}", fontsize=24)
    return doc


def page_texts(doc: pymupdf.Document) -> list[str]:
    return [doc[i].get_text("text").split()[0] if doc[i].get_text("text").split() else "" for i in range(doc.page_count)]


def save_numbered(path, pages: int = 6, prefix: str = "P", size=(595, 842), toc=None, password=None) -> str:
    doc = numbered_pdf(pages, size, prefix)
    if toc:
        doc.set_toc(toc)
    kwargs = {}
    if password:
        kwargs = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password + "x"}
    doc.save(str(path), **kwargs)
    doc.close()
    return str(path)


@pytest.fixture
def ten_pdf(tmp_path):
    return save_numbered(tmp_path / "doc.pdf", 10)


@pytest.fixture
def pages_window(window, ten_pdf):
    """Finestra con un documento di 10 pagine aperto e la modalità Pagine attiva."""
    view = window.open_path(ten_pdf)
    window.set_mode("pagine")
    return window, view


@pytest.fixture(autouse=True)
def _no_blocking_close(monkeypatch):
    """Alla chiusura della finestra di test i documenti modificati non vengono salvati.

    pytest-qt chiude i widget prima dei finalizzatori delle fixture: senza questa
    patch la domanda «Modifiche non salvate» resterebbe in attesa."""
    from PySide6.QtWidgets import QMessageBox

    original = QMessageBox.exec

    def fake_exec(box):
        if box.windowTitle() in ("Modifiche non salvate", "Unsaved changes"):
            for b in box.buttons():
                if box.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole:
                    b.click()
                    return 0
        return original(box)
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


@pytest.fixture(autouse=True)
def _clear_clipboard(qapp):
    """Svuota gli appunti: dati lasciati negli appunti fanno cadere Qt offscreen all'uscita."""
    yield
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().clear()
