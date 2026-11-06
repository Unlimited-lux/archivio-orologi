"""Comandi dell'Assistente IA senza fornitore (collaudo C-4).

Senza un profilo attivo i comandi non sono «morti»: il suggerimento e la riga di stato dicono che
cosa manca e dove si configura, e un clic (dalla barra, dal menu o da Ctrl+K) lo spiega e offre di
aprire le Impostazioni. Nessun servizio IA viene contattato finché il fornitore non è configurato.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from foliant import i18n

from .conftest import FakeProvider, make_active_profile

HINT = "Configura un fornitore nelle Impostazioni dell'Assistente IA (File › Opzioni)"


def _services(window):
    services = window.ctx.ai_services
    fake = FakeProvider()
    services.provider_factory = lambda p: fake
    return services, fake


def _action(services, text):
    return next(a for a in services.doc_actions + services.profile_actions if a.text() == text)


def test_commands_explain_missing_provider(window, sample_pdf):
    services, fake = _services(window)
    window.open_path(sample_pdf)
    services.refresh()
    actions = services.doc_actions + services.profile_actions
    assert len(actions) >= 21
    for a in actions:
        assert a.isEnabled(), a.text()
        assert HINT in a.toolTip(), (a.text(), a.toolTip())
        assert HINT in a.statusTip(), (a.text(), a.statusTip())
    # i comandi che funzionano anche senza fornitore restano come prima
    assert all(a.isEnabled() and HINT not in a.toolTip() for a in services.local_actions)

    asked = []
    services.ask_configure = lambda command, reason: asked.append((command, reason)) or False
    _action(services, "Riassumi").trigger()
    _action(services, "Scheda prodotto").trigger()
    _action(services, "Contratti").trigger()
    assert [c for c, _r in asked] == ["Riassumi", "Scheda prodotto", "Contratti"]
    assert all(HINT in r for _c, r in asked)
    assert fake.requests == []


def test_configure_from_the_explanation_then_run(window, sample_pdf):
    """«Apri le impostazioni»: dopo aver configurato il fornitore il comando parte."""
    services, fake = _services(window)
    window.open_path(sample_pdf)
    services.refresh()
    opened = []

    def show_preferences(page=None):
        opened.append(page)
        make_active_profile(services.store)

    window.ctx.show_preferences = show_preferences
    services.ask_configure = lambda command, reason: True
    _action(services, "Riassumi").trigger()
    assert opened == ["Assistente IA"]
    assert services.panel.function_key() == "summary"
    assert fake.requests, "il riassunto parte dopo la configurazione"
    # ora il fornitore c'è: suggerimenti normali
    a = _action(services, "Riassumi")
    assert a.isEnabled() and HINT not in a.toolTip() and HINT not in a.statusTip()


def test_explanation_cancelled_opens_nothing(window, sample_pdf):
    services, fake = _services(window)
    window.open_path(sample_pdf)
    services.refresh()
    opened = []
    window.ctx.show_preferences = lambda page=None: opened.append(page)
    services.ask_configure = lambda command, reason: False
    _action(services, "Chiedi").trigger()
    assert opened == [] and fake.requests == []


def test_command_search_enter_explains(window, sample_pdf, qtbot):
    """Ctrl+K «riassumi» + Invio: la ricerca si chiude e compare la spiegazione (non un Invio a vuoto)."""
    services, _fake = _services(window)
    window.open_path(sample_pdf)
    services.refresh()
    asked = []
    services.ask_configure = lambda command, reason: asked.append(command) or False
    ctl = window.ctx.viewer_parts["commands"]
    ctl.open_palette()
    palette = ctl.palette
    palette.field.setText("riassumi")
    assert palette.actions[0].text() == "Riassumi" and palette.actions[0].isEnabled()
    QTest.keyClick(palette.field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: asked == ["Riassumi"])
    assert not palette.isVisible()


def test_without_document_needs_document_first(window):
    services, _fake = _services(window)
    services.refresh()
    assert window.ctx.current_view() is None
    assert all(not a.isEnabled() for a in services.doc_actions)
    assert all(a.isEnabled() and HINT in a.toolTip() for a in services.profile_actions)


def test_ai_switched_off_explains_it(window, sample_pdf):
    services, _fake = _services(window)
    make_active_profile(services.store)
    services.store.set_enabled(False)
    window.open_path(sample_pdf)
    services.refresh()
    a = _action(services, "Traduci")
    assert a.isEnabled() and "Funzioni IA" in a.toolTip() and "File › Opzioni" in a.statusTip()


def test_explanation_in_english(window, sample_pdf):
    services, _fake = _services(window)
    window.open_path(sample_pdf)
    i18n.set_language("en")
    try:
        services.refresh()
        a = _action(services, "Riassumi")
        assert "Set up a provider in the AI Assistant settings (File › Options)" in a.toolTip()
    finally:
        i18n.set_language("it")


def test_real_explanation_dialog(window, sample_pdf, monkeypatch):
    """La finestra vera: titolo, testo con il comando e il pulsante che apre le impostazioni."""
    from PySide6.QtWidgets import QMessageBox
    services, _fake = _services(window)
    window.open_path(sample_pdf)
    services.refresh()
    seen = []

    def fake_exec(box):
        seen.append((box.windowTitle(), box.text(), box.informativeText(), [b.text() for b in box.buttons()]))
        configure = next(b for b in box.buttons() if box.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole)
        assert box.defaultButton() is configure
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert services.ask_configure("Riassumi", services.unavailable_reason()) is False
    title, text, info, buttons = seen[0]
    assert title == "Assistente IA" and "«Riassumi»" in text and HINT in info
    assert "Apri le impostazioni" in buttons and "Annulla" in buttons
