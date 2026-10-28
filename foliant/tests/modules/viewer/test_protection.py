"""VIS-06 — PDF protetti: criteri di accettazione che riguardano il viewer.

Password e apertura sono del nucleo; qui si verifica che il viewer rispetti i permessi e che
nulla di ciò che scrive (impostazioni, posizioni, sessione, cronologia delle ricerche, copie
di recupero) contenga la password."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pymupdf
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from foliant.api import selection_from_words

PASSWORD = "pässwörd-Segreta7"
OWNER = "Capo-Officina9"


def _encrypted(path, user=PASSWORD, owner=OWNER, permissions=-1):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Orologio riservato {i + 1}", fontsize=16)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user, owner_pw=owner,
             permissions=permissions)
    return str(path)


def _forms(secret: str) -> list[bytes]:
    raw = secret.encode("utf-8")
    return [raw, secret.encode("utf-16-le"), base64.b64encode(raw), raw.hex().encode(),
            secret.encode("latin-1", "replace"), secret.lower().encode("utf-8")]


def _leaks(folder: Path, secret: str) -> list[str]:
    found = []
    for f in folder.rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            for form in _forms(secret):
                if form in data:
                    found.append(f"{f}: {form[:12]!r}")
    return found


def test_aes256_r6_non_ascii_password(viewer_window, tmp_path, monkeypatch):
    """Dato un PDF AES-256 R6 con password utente non ASCII, quando la si digita, il documento
    si apre; con una password errata il documento resta chiuso."""
    path = _encrypted(tmp_path / "r6.pdf")
    view = viewer_window.open_path(path, password=PASSWORD)
    assert view is not None and view.page_count == 3
    viewer_window.close_view(view)
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    answers = iter([("sbagliata", True), ("", False)])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: next(answers)))
    before = len(viewer_window.views())
    assert viewer_window.open_path(path, password="sbagliata") is None
    assert len(viewer_window.views()) == before


def test_password_never_written_anywhere(viewer_window, tmp_path, monkeypatch):
    """Dato un PDF cifrato aperto, modificato e chiuso, quando si ispezionano configurazione,
    stato, sessione, cronologia, copie di recupero e cache, la password non vi compare."""
    path = _encrypted(tmp_path / "cifrato.pdf")
    view = viewer_window.open_path(path, password=PASSWORD)
    parts = viewer_window.ctx.viewer_parts
    view.go_to_page(2)
    parts["search"].search("riservato")
    parts["search"].finish()
    parts["bookmarks"].panel.set_view(view)
    with view.document.change("Testo") as pdf:
        pdf[0].insert_text((72, 300), "nota", fontsize=12)
    assert parts["recovery"].autosave_now() == 1      # copia di recupero del documento modificato
    assert any(Path(os.environ["FOLIANT_RECOVERY_DIR"]).rglob("*.pdf"))
    parts["session"].remember(view)                   # posizione per file
    parts["session"].save_session()
    viewer_window.settings.sync()
    view.document._saved_state = view.document._state
    viewer_window.close_view(view)
    viewer_window.settings.sync()
    roots = {Path(viewer_window.settings.fileName()).parent, Path(os.environ["XDG_STATE_HOME"]),
             Path(os.environ["FOLIANT_RECOVERY_DIR"])}
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        if os.environ.get(var):
            roots.add(Path(os.environ[var]))
    leaks = []
    for root in roots:
        if root.exists():
            for secret in (PASSWORD, OWNER):
                leaks += [x for x in _leaks(root, secret) if not x.startswith(str(path))]
    assert not leaks, leaks


def test_copy_forbidden_then_unlocked(viewer_window, tmp_path):
    """Dato un PDF con password utente vuota e copia vietata, Ctrl+C non cambia gli appunti;
    dopo lo sblocco con la password del proprietario la copia funziona."""
    path = _encrypted(tmp_path / "nocopia.pdf", user="", permissions=pymupdf.PDF_PERM_PRINT)
    view = viewer_window.open_path(path)
    clip = QGuiApplication.clipboard()
    clip.setText("prima")
    view.set_text_selection(selection_from_words(0, view.words(0)[:2]))
    assert not view.copy_selection()
    assert clip.text() == "prima"
    tools = viewer_window.ctx.viewer_parts["tools"]
    assert "non consente" in tools.act_snapshot.toolTip()
    assert view.document.unlock(OWNER)
    QApplication.processEvents()
    assert view.copy_selection()
    assert clip.text().startswith("Orologio")
    assert "non consente" not in tools.act_snapshot.toolTip()


def test_attachments_read_only_without_modify_permission(viewer_window, tmp_path):
    """Funzioni non consentite → disabilitate con il motivo nel suggerimento; lo sblocco con la
    password del proprietario le riattiva."""
    path = tmp_path / "allegati.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.embfile_add("nota.txt", b"ciao", filename="nota.txt")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw=OWNER,
             permissions=pymupdf.PDF_PERM_PRINT)
    view = viewer_window.open_path(str(path))
    panel = viewer_window.ctx.viewer_parts["attachments"]
    viewer_window.ctx.show_sidebar_panel("attachments")
    QApplication.processEvents()
    assert [a.filename for a in panel.items()] == ["nota.txt"]
    assert not panel.btn_add.isEnabled() and "sola lettura" in panel.btn_add.toolTip()
    assert panel.add_files([__file__]) == []
    assert not panel.delete_item(panel.items()[0], confirm=False)
    assert not view.document.is_modified
    assert view.document.unlock(OWNER)
    QApplication.processEvents()
    assert panel.btn_add.isEnabled() and "sola lettura" not in panel.btn_add.toolTip()
