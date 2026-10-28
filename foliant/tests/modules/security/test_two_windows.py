"""Più finestre nello stesso processo (Ctrl+N): lo stato del modulo è per finestra."""
from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from foliant.modules import security


def test_second_window_closed_first_still_works(window, sample_pdf, tmp_path, qtbot):
    from foliant.ui.main_window import open_window
    first = security.controller_for(window.ctx)
    assert first is not None and first.ctx is window.ctx
    other = open_window(window.settings, near=window)
    second = security.controller_for(other.ctx)
    assert second is not None and second is not first
    other.close()
    other.deleteLater()
    QApplication.processEvents()
    QApplication.processEvents()
    # il modulo nella prima finestra continua a funzionare
    view = window.open_path(sample_pdf)
    assert security.controller_for(window.ctx) is first
    first.add_area_mark(0, pymupdf.Rect(72, 72, 200, 100))
    assert first.has_marks(view.document)
    first.refresh_document_state(view)
    rows = first.security_rows(view.document) if hasattr(first, "security_rows") else None
    from foliant.modules.security import controller as C
    assert C.security_rows(view.document)
    QApplication.processEvents()


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


def test_trust_kept_only_in_memory(window, sample_pdf, monkeypatch):
    """Riservatezza: nessun percorso dei documenti attendibili nelle impostazioni."""
    c = security.controller_for(window.ctx)
    store = _Settings({c.TRUST_KEY: '{"files": {"/vecchio/percorso.pdf": "x"}}'})
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    c._session_trust = None
    view = window.open_path(sample_pdf)
    c.trust_document(view, folder=True)
    assert c.is_trusted(view.document)
    assert c.TRUST_KEY not in store and not any("pdf" in str(v) or "/" in str(v) for v in store.values())
    c.revoke_trust()
    assert not c.is_trusted(view.document)


def test_lock_covers_both_windows(window, monkeypatch):
    from foliant.ui.main_window import open_window
    c = security.controller_for(window.ctx)
    store = _Settings()
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    other = open_window(window.settings, near=window)
    assert security.controller_for(other.ctx).applock is c.applock
    c.applock.configure(True, "1234", 0, False)
    try:
        c.applock.lock()
        assert window in c.applock.screens and other in c.applock.screens
        assert other.windowTitle() == "Foliant"
        assert c.applock.attempt("1234")
    finally:
        if c.applock.locked:
            c.applock.unlock()
        c.applock.configure(False, None, 0, False)
        other.close()
        other.deleteLater()
        QApplication.processEvents()


OLD_TRUST = '{"files": {"/home/rossi/zq7k-contratto.pdf": "ab"}, "folders": ["/home/rossi/zq7k"]}'


@pytest.fixture(params=[OLD_TRUST, ""], ids=["elenco", "vuoto"])
def old_trust_settings(request, settings):
    settings.setValue("security/trusted", request.param)
    settings.sync()
    return settings


def test_old_trust_list_removed_at_startup(old_trust_settings, window):
    """Riservatezza: l'elenco dei documenti attendibili delle versioni precedenti (percorsi di file e
    cartelle) si cancella all'avvio del modulo, senza aprire documenti; anche se vuoto."""
    c = security.controller_for(window.ctx)
    assert c is not None and c.TRUST_KEY == "security/trusted"
    s = old_trust_settings
    s.sync()
    assert not s.contains("security/trusted")
    assert all("zq7k" not in str(s.value(k)) for k in s.allKeys())
