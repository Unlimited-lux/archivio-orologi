"""Test del dialogo Trova e sostituisci / Trova ed elimina."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import Qt

from foliant.modules.text_edit.find_dialog import MODE_DELETE, SCOPE_RANGE, TRANSLATIONS, FindReplaceDialog


def _pdf(path, pages: int = 3) -> str:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Il Fornitore della pagina {i + 1} consegna.", fontname="helv", fontsize=12)
        page.insert_text((72, 140), "BOZZA riservata del Fornitore", fontname="helv", fontsize=12)
        page.insert_text((72, 180), "data: 03/11/2025", fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def _text(view) -> str:
    with view.document.lock:
        return "\n".join(p.get_text() for p in view.document.pdf)


@pytest.fixture
def setup(window, qtbot, tmp_path, monkeypatch):
    view = window.open_path(_pdf(tmp_path / "doc.pdf"))
    assert view is not None
    # pytest-qt chiude la finestra prima dei finalizzatori: niente richiesta «Salvare le modifiche?»
    view.document.add_listener(lambda doc, kind: setattr(doc, "_saved_state", doc._state))
    ctx = window.ctx
    errors, infos, questions = [], [], []
    monkeypatch.setattr(ctx, "error", lambda title, text: errors.append(text))
    monkeypatch.setattr(ctx, "info", lambda title, text: infos.append(text))
    monkeypatch.setattr(ctx, "confirm", lambda title, text: questions.append(text) or True)
    dlg = FindReplaceDialog(ctx, window)
    qtbot.addWidget(dlg)
    dlg.show()
    return view, dlg, errors, infos, questions


def test_find_lists_results_and_highlights(setup, qtbot):
    view, dlg, errors, _infos, _q = setup
    assert not dlg.isModal()
    assert not dlg.find_btn.isEnabled()           # campo vuoto
    dlg.find_edit.setText("Fornitore")
    assert dlg.find_btn.isEnabled()
    qtbot.mouseClick(dlg.find_btn, Qt.MouseButton.LeftButton)
    assert len(dlg.matches) == 6
    assert dlg.results.count() == 6
    assert dlg.results.item(0).text().startswith("p. 1 — ")
    assert "Fornitore" in dlg.results.item(0).text()
    assert sum(len(v) for v in view.search_hits.values()) == 6
    assert view.current_hit == (0, 0)
    dlg.next_btn.click()
    assert dlg.current == 1 and view.current_hit == (0, 1)
    dlg.next_btn.click()
    assert view.current_hit == (1, 0)
    dlg.prev_btn.click()
    dlg.prev_btn.click()
    dlg.prev_btn.click()                          # torna all'ultima
    assert dlg.current == 5
    dlg.results.setCurrentRow(2)
    assert dlg.current == 2 and view.current_hit == (1, 0)
    assert errors == []
    dlg.close()
    assert view.search_hits == {}


def test_replace_all_with_confirmation_and_undo(setup):
    view, dlg, errors, _infos, questions = setup
    dlg.find_edit.setText("Fornitore")
    dlg.replace_edit.setText("Appaltatore")
    dlg.case_box.setChecked(True)
    dlg.replace_all_btn.click()
    assert questions == []          # nessuna conferma per un'operazione annullabile (UX-13)
    text = _text(view)
    assert text.count("Appaltatore") == 6 and "Fornitore" not in text
    assert view.document.can_undo
    assert "Occorrenze sostituite: 6." in dlg.summary_label.text()
    assert dlg.matches == []
    assert errors == []
    view.document.undo()
    assert _text(view).count("Fornitore") == 6


def test_regex_replace_and_inline_error(setup):
    view, dlg, errors, _infos, _q = setup
    dlg.regex_box.setChecked(True)
    dlg.find_edit.setText(r"(\d{2})/(\d{2}")
    assert dlg.error_label.isVisibleTo(dlg)
    assert "Espressione regolare non valida" in dlg.error_label.text()
    for b in (dlg.find_btn, dlg.next_btn, dlg.replace_btn, dlg.replace_all_btn):
        assert not b.isEnabled()
    dlg.find_edit.setText(r"(\d{2})/(\d{2})/(\d{4})")
    assert not dlg.error_label.isVisibleTo(dlg)
    dlg.replace_edit.setText("$4")                # gruppo inesistente
    assert dlg.error_label.isVisibleTo(dlg) and not dlg.replace_all_btn.isEnabled()
    dlg.replace_edit.setText("$3-$2-$1")
    assert dlg.replace_all_btn.isEnabled()
    dlg.replace_all_btn.click()
    assert _text(view).count("data: 2025-11-03") == 3
    assert errors == []


def test_replace_one_moves_to_next(setup):
    view, dlg, errors, _infos, _q = setup
    dlg.find_edit.setText("Fornitore")
    dlg.replace_edit.setText("Cliente")
    dlg.find_btn.click()
    assert dlg.current == 0
    dlg.replace_btn.click()
    assert errors == []
    assert _text(view).count("Cliente") == 1
    assert len(dlg.matches) == 5
    assert dlg.current == 0 and dlg.matches[0].page == 0   # la successiva: seconda riga di pagina 1
    assert view.document.can_undo


def test_delete_mode_with_excluded_occurrence(setup):
    view, dlg, errors, _infos, questions = setup
    dlg.set_mode(MODE_DELETE)
    assert dlg.delete_all_btn.isVisibleTo(dlg) and not dlg.replace_all_btn.isVisibleTo(dlg)
    assert dlg.space_box.isChecked()
    assert dlg.windowTitle() == "Trova ed elimina"
    dlg.find_edit.setText("BOZZA")
    dlg.find_btn.click()
    assert dlg.results.count() == 3
    dlg.results.item(1).setCheckState(Qt.CheckState.Unchecked)
    dlg.delete_all_btn.click()
    assert questions == []
    text = _text(view)
    assert text.count("BOZZA") == 1
    assert "riservata del Fornitore" in text
    assert "  " not in text
    assert "Occorrenze eliminate: 2." in dlg.summary_label.text()
    assert errors == []
    assert view.document.undo_label == "Trova ed elimina (2)"


def test_page_range_scope(setup):
    view, dlg, errors, _infos, _q = setup
    dlg.scope_combo.setCurrentIndex(SCOPE_RANGE)
    dlg.find_edit.setText("Fornitore")
    dlg.range_edit.setText("9")
    assert dlg.error_label.isVisibleTo(dlg) and not dlg.find_btn.isEnabled()
    dlg.range_edit.setText("2-3")
    dlg.find_btn.click()
    assert {m.page for m in dlg.matches} == {1, 2}
    assert errors == []


def test_no_document(window, qtbot):
    dlg = FindReplaceDialog(window.ctx, window)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.find_edit.setText("x")
    assert not dlg.find_btn.isEnabled()
    dlg.on_find()                                 # nessuna eccezione


def test_translations_cover_dialog_strings():
    for key in ("Trova e sostituisci", "Sostituisci tutto", "Elimina tutto", "Ignora accenti",
                "Rimuovi anche uno spazio adiacente", "Espressione regolare non valida: {err}.",
                "Sostituisci tutto ({n})", "Trova ed elimina ({n})", "L'occorrenza non esiste più."):
        assert key in TRANSLATIONS
