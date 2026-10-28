"""SEC-31 Spazio protetto: integrazione con l'applicazione."""
from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules import security
from foliant.modules.security import controller as C
from foliant.modules.security import vault as V
from foliant.modules.security import vault_ui as VU

PASS = "vetrina-Daytona-1963"


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def sec(window, tmp_path, monkeypatch):
    c = security.CONTROLLER
    store = _Settings({C.SecurityController.VAULT_FOLDER_KEY: str(tmp_path / "spazio")})
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    monkeypatch.setattr(V, "KDF", {"iterations": 1, "memory": 8 * 1024, "lanes": 1})
    monkeypatch.setattr(QDialog, "exec", lambda self: (_ for _ in ()).throw(AssertionError("dialogo inatteso")))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    c._vault = None
    yield c
    c.lock_vault()
    # gli altri moduli reagiscono a SAVED con un timer a 0 ms: va eseguito prima che la
    # finestra del test venga distrutta
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def _pdf(path, text="Contratto di permuta: Patek Philippe 5711"):
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), text, fontsize=12)
    d.save(path)
    return path


def _ready(sec, tmp_path) -> str:
    v = sec.vault()
    v.create(PASS)
    return v.add("Permuta Rossi.pdf", _pdf(tmp_path / "x.pdf").read_bytes())


def test_setup_dialog_shows_recovery_once(sec, qtbot):
    v = sec.vault()
    dlg = VU.SetupDialog(v)
    qtbot.addWidget(dlg)
    dlg.pw.setText("corta")
    dlg.pw2.setText("corta")
    assert not dlg.create() and "8" in dlg.error.text()
    dlg.pw.setText(PASS)
    dlg.pw2.setText(PASS)
    assert dlg.create() and v.exists and v.unlocked
    assert dlg.key_box.text() == dlg.recovery and len(dlg.recovery) > 30
    assert not dlg.ok.isEnabled()
    dlg.saved.setChecked(True)
    assert dlg.ok.isEnabled()


def test_unlock_dialog(sec, tmp_path, qtbot):
    _ready(sec, tmp_path)
    v = sec.vault()
    v.lock()
    dlg = VU.UnlockDialog(v)
    qtbot.addWidget(dlg)
    dlg.secret.setText("sbagliata")
    assert not dlg.try_unlock() and "errata" in dlg.error.text() and not v.unlocked
    dlg.secret.setText(PASS)
    assert dlg.try_unlock() and v.unlocked


def test_locked_vault_asks_passphrase(sec, tmp_path, monkeypatch):
    """Con lo spazio bloccato viene chiesta la passphrase e senza non si apre nulla."""
    _ready(sec, tmp_path)
    sec.vault().lock()
    asked = []
    monkeypatch.setattr(C, "run_dialog", lambda dlg: asked.append(type(dlg).__name__) or False)
    assert not sec.manage_vault()
    assert asked == ["UnlockDialog"] and not sec.vault().unlocked


def test_open_edit_save_stays_in_vault(window, sec, tmp_path):
    oid = _ready(sec, tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    view = sec.open_from_vault(oid)
    assert view is not None and view.document.display_name == "Permuta Rossi.pdf"
    with view.document.change("Nota") as pdf:
        pdf[0].insert_text((72, 300), "Valutazione 48.000 EUR", fontsize=11)
    assert view.document.is_modified
    window.save_view(view)                      # Ctrl+S
    assert not view.document.is_modified and view.document.path is None
    assert sorted(p.name for p in tmp_path.rglob("*")) == before   # nessun file nuovo in chiaro
    assert b"48.000" not in b"".join(p.read_bytes() for p in (tmp_path / "spazio").rglob("*.bin"))
    assert "Valutazione 48.000 EUR" in pymupdf.open("pdf", sec.vault().read(oid))[0].get_text()


def test_close_modified_asks_and_saves(window, sec, tmp_path, monkeypatch):
    oid = _ready(sec, tmp_path)
    view = sec.open_from_vault(oid)
    with view.document.change("Nota") as pdf:
        pdf[0].insert_text((72, 300), "modifica", fontsize=11)
    monkeypatch.setattr(C, "ask_vault_unsaved", lambda parent, names: "save")
    assert window.close_view(view)
    assert "modifica" in pymupdf.open("pdf", sec.vault().read(oid))[0].get_text()


def test_quit_discard(window, sec, tmp_path, monkeypatch):
    oid = _ready(sec, tmp_path)
    view = sec.open_from_vault(oid)
    with view.document.change("Nota") as pdf:
        pdf[0].insert_text((72, 300), "da scartare", fontsize=11)
    monkeypatch.setattr(C, "ask_vault_unsaved", lambda parent, names: "discard")
    assert sec.vault_quit_guard(None, {"views": [view]})
    assert not view.document.is_modified
    assert "da scartare" not in pymupdf.open("pdf", sec.vault().read(oid))[0].get_text()
    monkeypatch.setattr(C, "ask_vault_unsaved", lambda parent, names: None)
    with view.document.change("Nota") as pdf:
        pdf[0].insert_text((72, 320), "x", fontsize=11)
    assert not sec.vault_quit_guard(None, {"views": [view]})


def test_auto_lock_closes_documents_and_saves_changes(window, sec, tmp_path, qtbot):
    """Dato il blocco automatico, trascorso il tempo i documenti dello spazio vengono chiusi,
    le modifiche salvate cifrate e la chiave tolta dalla memoria."""
    oid = _ready(sec, tmp_path)
    view = sec.open_from_vault(oid)
    with view.document.change("Nota") as pdf:
        pdf[0].insert_text((72, 300), "salvata al blocco", fontsize=11)
    sec._arm_vault_timer(ms=50)
    qtbot.waitUntil(lambda: not sec.vault().unlocked, timeout=3000)
    assert view not in window.views() and view.document.closed
    assert not list(sec.vault_docs)
    sec.vault().unlock(PASS)
    assert "salvata al blocco" in pymupdf.open("pdf", sec.vault().read(oid))[0].get_text()


def test_tampered_document_reported(window, sec, tmp_path, monkeypatch):
    oid = _ready(sec, tmp_path)
    path = tmp_path / "spazio" / "objects" / f"{oid}.bin"
    blob = bytearray(path.read_bytes())
    blob[-5] ^= 0xFF
    path.write_bytes(bytes(blob))
    errors = []
    monkeypatch.setattr(sec.ctx, "error", lambda title, text: errors.append((title, text)))
    assert sec.open_from_vault(oid) is None
    assert errors and errors[0][0] == "Documento alterato"


def test_export_and_save_copy(window, sec, tmp_path, monkeypatch):
    oid = _ready(sec, tmp_path)
    monkeypatch.setattr(C, "confirm", lambda *a, **k: True)
    out = tmp_path / "esportato.pdf"
    monkeypatch.setattr(sec.ctx, "ask_save_path", lambda title, name: str(out))
    assert sec.vault_export(oid)
    assert out.read_bytes() == sec.vault().read(oid)
    view = sec.open_from_vault(oid)
    assert sec.vault_save_guard(view, {"copy": True}) is True     # copia esplicita, con conferma


def test_move_current_document(window, sec, tmp_path):
    sec.vault().create(PASS)
    original = _pdf(tmp_path / "Carta identita Bianchi.pdf", "Carta d'identità CA12345XY")
    view = window.open_path(str(original))
    assert sec.can_move_to_vault()
    assert sec.move_to_vault(delete_original=True)
    assert not original.exists()
    current = window.ctx.current_view()
    assert current.document in sec.vault_docs and current.document.display_name == "Carta identita Bianchi.pdf"
    assert not sec.can_move_to_vault()
    assert view not in window.views()


def test_vault_dialog_lists_and_removes(window, sec, tmp_path, qtbot, monkeypatch):
    _ready(sec, tmp_path)
    dlg = VU.VaultDialog(sec)
    qtbot.addWidget(dlg)
    assert dlg.tree.topLevelItemCount() == 1 and dlg.tree.topLevelItem(0).text(0) == "Permuta Rossi.pdf"
    dlg.minutes.setValue(7)
    assert sec.vault_minutes() == 7 and sec.vault_idle.active
    monkeypatch.setattr(C, "confirm", lambda *a, **k: True)
    dlg._remove()
    assert dlg.tree.topLevelItemCount() == 0 and "vuoto" in dlg.notice.text()


def test_suspend_locks_vault(window, sec, tmp_path):
    _ready(sec, tmp_path)
    for hook in sec.applock.suspend_hooks:
        hook()
    assert not sec.vault().unlocked
