"""Fixture del modulo Moduli: PDF con campi di ogni tipo."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.forms import engine as E


def build_form(path=None, pages: int = 1) -> pymupdf.Document:
    pdf = pymupdf.open()
    for _ in range(pages):
        pg = pdf.new_page()
        pg.insert_text((72, 60), "Modulo di prova", fontsize=14)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(72, 80, 272, 100), "nome", required=True)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 80, 500, 100), "cognome")
    E.create_field(pdf, 0, E.CHECKBOX, pymupdf.Rect(72, 120, 86, 134), "privacy")
    for i, v in enumerate(("A", "B", "C")):
        E.create_field(pdf, 0, E.RADIO, pymupdf.Rect(72 + 40 * i, 150, 86 + 40 * i, 164), "scelta", export=v)
    E.create_field(pdf, 0, E.COMBO, pymupdf.Rect(72, 180, 222, 200), "paese",
                   options=[("IT", "Italia"), ("FR", "Francia")])
    E.create_field(pdf, 0, E.LIST, pymupdf.Rect(72, 210, 222, 270), "colori",
                   options=["rosso", "verde", "blu"], multiselect=True)
    if path:
        pdf.save(str(path))
    return pdf


@pytest.fixture
def form_pdf(tmp_path):
    path = tmp_path / "modulo.pdf"
    build_form(path).close()
    return str(path)


@pytest.fixture
def form():
    pdf = build_form()
    yield pdf
    pdf.close()


def reopen(pdf: pymupdf.Document) -> pymupdf.Document:
    return pymupdf.open("pdf", pdf.tobytes(garbage=3, deflate=True))


@pytest.fixture(autouse=True)
def no_modal_boxes(monkeypatch):
    """Nessuna finestra modale nei test: le risposte sono registrate in `calls`."""
    from PySide6.QtWidgets import QMessageBox
    calls = []

    def fake(kind):
        def _f(parent, title, text, *a, **k):
            calls.append((kind, title, text))
            if kind == "question":
                return QMessageBox.StandardButton.Yes
            return QMessageBox.StandardButton.Ok
        return _f

    for kind in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(fake(kind)))
    # finestra «Salvare le modifiche?» alla chiusura: nessun pulsante scelto (chiusura rimandata
    # alla fixture `window`, che chiude le viste senza chiedere)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: calls.append(("exec", self.windowTitle(), self.text())) or 0)
    return calls


@pytest.fixture(autouse=True)
def free_windows():
    """Dopo ogni test distrugge le finestre principali: le icone legate al tema restano poche
    e la creazione delle finestre successive non rallenta."""
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from foliant.ui.main_window import MainWindow
    for w in QApplication.topLevelWidgets():
        if isinstance(w, MainWindow):
            for view in list(w.views()):
                view.document._saved_state = view.document._state
                w.close_view(view)
            w.hide()
            w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
