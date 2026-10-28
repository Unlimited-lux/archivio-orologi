"""ANN-44: controllo ortografico nei campi di testo dei commenti."""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from foliant.modules.annotations import dialogs, engine, spellcheck

spell = pytest.importorskip("foliant.modules.text_edit.spell")
pytest.importorskip("spellchecker")


@pytest.fixture
def service(tmp_path):
    svc = spell.SpellService(personal_path=tmp_path / "personale.txt")
    spellcheck.set_service(svc)
    yield svc
    spellcheck.set_service(None)


def _flagged(edit) -> list[str]:
    hl = edit._spell
    out = []
    block = edit.document().begin()
    while block.isValid():
        out += [w for _s, _e, w in hl.misspelled(block.text())]
        block = block.next()
    return out


def _pos_of(edit, word):
    text = edit.toPlainText()
    from PySide6.QtGui import QTextCursor
    c = QTextCursor(edit.document())
    c.setPosition(text.index(word) + 2)
    return edit.cursorRect(c).center()


def test_misspelled_word_flagged_with_suggestion(qtbot, service, settings):
    spellcheck.use_settings(settings)
    dlg = dialogs.CommentEditor(None, "Nota", "A", "")
    qtbot.addWidget(dlg)
    dlg.edit._spell.set_language("it")
    dlg.edit.setPlainText("prova di ortogarfia")
    dlg.show()
    qtbot.waitExposed(dlg)
    assert _flagged(dlg.edit) == ["ortogarfia"]
    # la sottolineatura è un formato di presentazione: compare nel layout del blocco
    QApplication.processEvents()
    formats = dlg.edit.document().begin().layout().formats()
    assert any(f.start == 9 and f.length == 10 for f in formats)
    menu = spellcheck.build_menu(dlg.edit, _pos_of(dlg.edit, "ortogarfia"))
    texts = [a.text() for a in menu.actions()]
    assert "ortografia" in texts and "Aggiungi al dizionario" in texts and "Ignora" in texts
    [a for a in menu.actions() if a.text() == "ortografia"][0].trigger()
    assert dlg.edit.toPlainText() == "prova di ortografia" and _flagged(dlg.edit) == []


def test_personal_dictionary_shared_between_documents(qtbot, service, settings, tmp_path):
    spellcheck.use_settings(settings)
    dlg = dialogs.TextDialog(None, "Rispondi", "Risposta:", "Contratto Rolexino")
    qtbot.addWidget(dlg)
    dlg.edit._spell.set_language("it")
    dlg.show()
    qtbot.waitExposed(dlg)
    assert "Rolexino" in _flagged(dlg.edit)
    menu = spellcheck.build_menu(dlg.edit, _pos_of(dlg.edit, "Rolexino"))
    [a for a in menu.actions() if a.text() == "Aggiungi al dizionario"][0].trigger()
    assert _flagged(dlg.edit) == []
    # «altro documento»: un nuovo servizio legge lo stesso dizionario personale
    spellcheck.set_service(spell.SpellService(personal_path=tmp_path / "personale.txt"))
    other = dialogs.TextDialog(None, "Rispondi", "Risposta:", "Rolexino in vetrina")
    qtbot.addWidget(other)
    other.edit._spell.set_language("it")
    assert _flagged(other.edit) == []


def test_toggle_and_language_without_dictionary(qtbot, service, settings):
    spellcheck.use_settings(settings)
    dlg = dialogs.TextDialog(None, "T", "L", "ortogarfia")
    qtbot.addWidget(dlg)
    hl = dlg.edit._spell
    hl.set_language("it")
    menu = spellcheck.build_menu(dlg.edit, QPoint(1, 1))
    [a for a in menu.actions() if a.text() == "Controllo ortografico"][0].toggle()
    assert not spellcheck.enabled() and _flagged(dlg.edit) == []
    spellcheck.set_enabled(True)
    hl.set_active(True)
    hl.set_language("xx")                     # lingua senza dizionario: controllo spento con indicazione
    assert _flagged(dlg.edit) == []
    menu = spellcheck.build_menu(dlg.edit, QPoint(1, 1))
    langs = [a.menu() for a in menu.actions() if a.text() == "Lingua del controllo"][0]
    assert any("Nessun dizionario" in a.text() for a in langs.actions())


def test_saved_appearance_identical_with_and_without_check(comments, service, monkeypatch):
    """Le sottolineature non finiscono nel PDF: stesso /Contents, /RC e aspetto."""
    window, view, module = comments
    results = []
    for on in (True, False):
        spellcheck.set_enabled(on)
        dlg = dialogs.CommentEditor(view, "Nota", "A", "prova di ortogarfia")
        dlg.edit._spell.set_language("it")
        text, rich = dlg.result_text()
        x = module.ctrl.apply("n", engine.create_freetext, 0, (100, 100 + 80 * on, 300, 160 + 80 * on), text,
                              engine.Style(), "A")
        if rich:
            module.ctrl.apply("t", engine.set_contents, 0, x, text, rich)
        pdf = view.document.pdf
        ap = int(pdf.xref_get_key(x, "AP/N")[1].split()[0])
        results.append((text, rich, pdf.xref_stream(ap)))
    spellcheck.set_enabled(True)
    assert results[0] == results[1]
