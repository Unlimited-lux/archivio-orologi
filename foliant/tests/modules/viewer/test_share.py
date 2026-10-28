"""VIS-45 — Invio e condivisione locale."""

from __future__ import annotations

import hashlib

import pymupdf
from PySide6.QtGui import QGuiApplication


def _ctl(w):
    return w.ctx.viewer_parts["share"]


def test_email_saved_document(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "titolo.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Preventivo Rolex"})
    doc.save(str(path))
    viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    calls = []
    monkeypatch.setattr(ctl, "mail_client", lambda: "xdg-email")
    monkeypatch.setattr(ctl, "launch", lambda prog, args: calls.append((prog, args)) or True)
    assert ctl.send_email()
    prog, args = calls[0]
    assert args[args.index("--subject") + 1] == "Preventivo Rolex"
    assert args[args.index("--attach") + 1] == str(path)


def test_email_copy_with_changes_keeps_disk_file(viewer_window, sample_pdf, monkeypatch):
    view = viewer_window.open_path(sample_pdf)
    before = hashlib.sha256(open(sample_pdf, "rb").read()).hexdigest()
    with view.document.change("Testo") as pdf:
        pdf[0].insert_text((72, 300), "MODIFICA NON SALVATA")
    ctl = _ctl(viewer_window)
    calls = []
    monkeypatch.setattr(ctl, "mail_client", lambda: "xdg-email")
    monkeypatch.setattr(ctl, "launch", lambda prog, args: calls.append(args) or True)
    monkeypatch.setattr(ctl, "ask_modified_choice", lambda: "copy")
    assert ctl.send_email()
    attached = calls[0][calls[0].index("--attach") + 1]
    assert attached != sample_pdf and attached.endswith("esempio.pdf")
    assert "MODIFICA NON SALVATA" in pymupdf.open(attached)[0].get_text()
    assert hashlib.sha256(open(sample_pdf, "rb").read()).hexdigest() == before
    ctl.cleanup()


def test_no_mail_client_message(viewer_window, sample_pdf, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    msgs = []
    monkeypatch.setattr(ctl, "mail_client", lambda: None)
    monkeypatch.setattr(viewer_window.ctx, "info", lambda t, m: msgs.append(m))
    assert not ctl.send_email()
    assert "Mostra nella cartella" in msgs[0]


def test_copy_file_to_clipboard(viewer_window, sample_pdf):
    viewer_window.open_path(sample_pdf)
    assert _ctl(viewer_window).copy_file()
    urls = QGuiApplication.clipboard().mimeData().urls()
    assert urls and urls[0].toLocalFile() == sample_pdf
