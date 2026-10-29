"""Test del controllo ortografico (TXT-14)."""

from __future__ import annotations

import time

import pymupdf
import pytest
from PySide6.QtGui import QTextCharFormat, QTextCursor

from foliant.core.document import Document
from foliant.modules.text_edit import spell as spell_mod
from foliant.modules.text_edit.spell import (
    TRANSLATIONS, SpellOptions, SpellService, apply_corrections, choose_language, document_language, match_case,
)
from foliant.modules.text_edit.spell_ui import (
    DictionaryDialog, SpellCheckDialog, SpellHighlighter, attach_spellcheck, build_context_menu,
)
from tests.conftest import perf_limit

ERRORS = {
    "lavorazzione": "lavorazione",
    "zaffirro": "zaffiro",
    "transparente": "trasparente",
    "movimmento": "movimento",
    "garanzzia": "garanzia",
}

LINES = [
    "La lavorazzione della cassa è terminata.",
    "Il quadrante ha un vetro zaffirro.",
    "La cassa è transparente e robusta.",
    "Il movimmento è fatto a mano.",
    "La garanzzia dura due anni.",
]


@pytest.fixture
def service(tmp_path):
    return SpellService(personal_path=tmp_path / "config" / "dizionario_personale.txt",
                        user_dict_dir=tmp_path / "dizionari", system_dict_dirs=[])


def _errors_pdf(path, lines=LINES, lang: str | None = None) -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 100
    for line in lines:
        page.insert_text((72, y), line, fontname="helv", fontsize=12)
        y += 60
    if lang:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", f"({lang})")
    doc.save(str(path))
    doc.close()
    return str(path)


def _text(document) -> str:
    with document.lock:
        return "\n".join(p.get_text() for p in document.pdf)


# ====================================================================== logica
def test_misspelled_word_flagged_with_suggestion_fast(service):
    service.misspelled("riscaldamento", "it")       # caricamento del dizionario
    service.suggestions("sbaglaito", "it")
    t0 = time.perf_counter()
    found = service.misspelled("La lavorazzione del pezzo", "it")
    sugg = service.suggestions("lavorazzione", "it")
    elapsed = time.perf_counter() - t0
    assert found == [(3, 15, "lavorazzione")]
    assert "lavorazione" in sugg[:5]
    assert elapsed < perf_limit(0.3)


def test_suggestions_fast_for_long_and_unknown_words(service):
    service.suggestions("prova", "it")
    for word in ("precipitevolissimevolmentx", "qwrtzpklmn", "Orologgio", "contrato"):
        t0 = time.perf_counter()
        service.suggestions(word, "it")
        assert time.perf_counter() - t0 < perf_limit(0.3), word
    assert service.suggestions("Orologgio", "it")[0] == "Orologio"   # maiuscola mantenuta
    assert "contratto" in service.suggestions("contrato", "it")


def test_case_insensitive_and_ignore_rules(service):
    text = ("Visita https://www.esempio.it/pagina o scrivi a mario.rossi@esempio.it: "
            "ONU BZZT 3kg A4 x2 Lavorazione OROLOGIO")
    assert service.misspelled(text, "it") == []
    opts = SpellOptions(ignore_digits=False, ignore_caps=False, ignore_urls=False, ignore_emails=False)
    words = {w.lower() for _s, _e, w in service.misspelled(text, "it", opts)}
    assert "bzzt" in words
    assert "3kg" in words or "kg" in words
    assert any("esempio" in w or "www" in w or "https" in w for w in words) or "rossi" in words


def test_elisions(service):
    assert service.misspelled("dell'anno l'orologio quest'anno un'ora all’alba", "it") == []
    found = service.misspelled("dell'annno", "it")
    assert found == [(5, 10, "annno")]
    assert service.misspelled("John's watch isn't broken", "en") == []


def test_soft_hyphen_rejoined(service):
    assert service.misspelled("lavo­razione", "it") == []
    assert service.misspelled("lavo-\nrazione", "it") == []
    found = service.misspelled("lavo­razzione", "it")
    assert len(found) == 1 and found[0][0] == 0


def test_personal_dictionary_persists_after_restart(tmp_path):
    path = tmp_path / "cfg" / "dizionario_personale.txt"
    first = SpellService(personal_path=path, system_dict_dirs=[], user_dict_dir=tmp_path / "d")
    assert first.misspelled("Foliantissimo", "it")
    first.add_to_personal("Foliantissimo")
    assert first.misspelled("foliantissimo", "it") == []
    # "riavvio": nuova istanza con lo stesso file
    second = SpellService(personal_path=path, system_dict_dirs=[], user_dict_dir=tmp_path / "d")
    assert second.misspelled("Foliantissimo", "it") == []
    assert path.read_text("utf-8").splitlines() == ["Foliantissimo"]
    # esportazione / importazione / rimozione
    out = tmp_path / "export.txt"
    second.export_personal(out)
    second.remove_from_personal("foliantissimo")
    assert second.misspelled("Foliantissimo", "it")
    (tmp_path / "altre.txt").write_text("Cronografone\n\nFoliantissimo\n", "utf-8")
    assert second.import_personal(tmp_path / "altre.txt") == 2
    assert second.misspelled("Cronografone Foliantissimo", "it") == []


def test_ignore_all_is_session_only(service, tmp_path):
    service.ignore_all("Zorbix")
    assert service.misspelled("zorbix", "it") == []
    other = SpellService(personal_path=service.personal_path, system_dict_dirs=[], user_dict_dir=tmp_path / "d")
    assert other.misspelled("zorbix", "it")


def test_hunspell_dictionary_installed_during_session(service):
    assert service.misspelled("zorbillo", "it")
    assert "xx" not in service.available_languages()
    service.user_dict_dir.mkdir(parents=True)
    (service.user_dict_dir / "it_IT.dic").write_text("2\nzorbillo/S\nflumbo\n", "utf-8")
    (service.user_dict_dir / "xx_XX.dic").write_text("1\nbarabba/AB\n", "utf-8")
    service.refresh()
    assert service.misspelled("zorbillo", "it") == []
    assert "xx" in service.available_languages()
    assert service.misspelled("barabba barabbo", "xx") == [(8, 15, "barabbo")]
    assert "barabba" in service.suggestions("barabbo", "xx")


def test_missing_language_reports_nothing(service):
    assert not service.has_language("qq")
    assert service.misspelled("qualsiasi cosa", "qq") == []


def test_language_choice(tmp_path, service, monkeypatch):
    monkeypatch.setattr(spell_mod, "ui_language", lambda: "it")
    doc = pymupdf.open(_errors_pdf(tmp_path / "en.pdf", lang="en-US"))
    assert document_language(doc) == "en"
    assert choose_language(service, doc) == "en"
    doc2 = pymupdf.open(_errors_pdf(tmp_path / "none.pdf"))
    assert document_language(doc2) is None
    assert choose_language(service, doc2) == "it"


def test_match_case():
    assert match_case("Lavorazzione", "lavorazione") == "Lavorazione"
    assert match_case("lavorazzione", "lavorazione") == "lavorazione"
    assert match_case("ROMA", "roma") == "ROMA"


def test_check_document_finds_errors(tmp_path, service):
    document = Document.open(_errors_pdf(tmp_path / "e.pdf"))
    with document.lock:
        issues = service.check_document(document.pdf, "it")
    assert sorted(i.word for i in issues) == sorted(ERRORS)
    for i in issues:
        assert i.editable and i.page == 0
        assert i.rects and i.context
        block_text = i.context.replace("…", "")
        assert i.word in block_text
        assert ERRORS[i.word] in i.suggestions, (i.word, i.suggestions)


def test_apply_corrections_one_undo_step(tmp_path, service):
    document = Document.open(_errors_pdf(tmp_path / "e.pdf"))
    original = _text(document)
    with document.lock:
        issues = service.check_document(document.pdf, "it", suggest=False)
    version = document.version
    summary = apply_corrections(document, [(i, ERRORS[i.word]) for i in issues], "Correzioni")
    assert summary.applied == 5 and not summary.failed
    assert document.version == version + 1
    corrected = _text(document)
    for wrong, right in ERRORS.items():
        assert wrong not in corrected and right in corrected
    with document.lock:
        assert service.check_document(document.pdf, "it") == []
    # un solo passo di annulla
    assert document.undo()
    assert _text(document) == original
    assert document.redo()
    out = tmp_path / "salvato.pdf"
    document.save(out)
    reopened = pymupdf.open(str(out))
    text = "\n".join(p.get_text() for p in reopened)
    for right in ERRORS.values():
        assert right in text
    assert "La lavorazione della cassa è terminata." in text


def test_apply_corrections_keeps_style(tmp_path, service):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Testo normale e", fontname="helv", fontsize=12)
    page.insert_text((72 + pymupdf.get_text_length("Testo normale e ", "helv", 12), 100), "lavorazzione",
                     fontname="hebo", fontsize=12, color=(0.8, 0, 0))
    path = tmp_path / "s.pdf"
    doc.save(str(path))
    document = Document.open(path)
    with document.lock:
        issues = service.check_document(document.pdf, "it", suggest=False)
    assert [i.word for i in issues] == ["lavorazzione"]
    assert apply_corrections(document, [(issues[0], "lavorazione")]).applied == 1
    with document.lock:
        spans = [s for b in document.pdf[0].get_text("dict")["blocks"] for ln in b.get("lines", [])
                 for s in ln["spans"] if "lavorazione" in s["text"]]
    assert spans
    assert spans[0]["color"] == pymupdf.sRGB_to_pdf(0xCC0000) or spans[0]["color"] > 0xA00000
    assert "Bold" in spans[0]["font"] or spans[0]["flags"] & 16


def test_apply_corrections_nothing_applicable_creates_no_step(tmp_path, service):
    document = Document.open(_errors_pdf(tmp_path / "e.pdf"))
    with document.lock:
        issues = service.check_document(document.pdf, "it", suggest=False)
    for i in issues:
        i.editable = False
        i.reason = "prova"
    version = document.version
    summary = apply_corrections(document, [(i, "x") for i in issues])
    assert summary.applied == 0 and len(summary.failed) == 5
    assert document.version == version


def test_translations_cover_ui_strings():
    import ast
    import inspect

    from foliant.modules.text_edit import spell_ui
    missing = []
    for mod in (spell_mod, spell_ui):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                if node.args[0].value not in TRANSLATIONS:
                    missing.append(node.args[0].value)
    assert not missing, missing


# ====================================================================== interfaccia
@pytest.fixture
def quiet(window, monkeypatch):
    """Dialoghi simulati, nessuna richiesta di salvataggio e appunti vuoti alla fine."""
    log = {"errors": [], "confirms": [], "status": [], "infos": []}
    ctx = window.ctx
    monkeypatch.setattr(ctx, "error", lambda t, m: log["errors"].append((t, m)))
    monkeypatch.setattr(ctx, "info", lambda t, m: log["infos"].append((t, m)))
    monkeypatch.setattr(ctx, "confirm", lambda t, m: log["confirms"].append((t, m)) or True)
    orig_status = ctx.status
    monkeypatch.setattr(ctx, "status", lambda m, timeout_ms=5000: (log["status"].append(m),
                                                                   orig_status(m, timeout_ms)))
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    original_close = window.close_view

    def close_without_prompt(view, *a, **k):
        view.document._saved_state = view.document._state
        return original_close(view, *a, **k)

    monkeypatch.setattr(window, "close_view", close_without_prompt)
    yield log
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().clear()


def _helv_pdf(path, text="Benvenuti nella nostra sede") -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), text, fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def _open_editor(window, path):
    view = window.open_path(path)
    assert view is not None
    window.ctx.set_mode("modifica")
    ctrl = window.ctx.text_edit
    block = ctrl.blocks(view, 0)[0]
    ed = ctrl.open_editor(view, 0, block)
    assert ed is not None
    return view, ctrl, block, ed


def _spell_ranges(ed, pos):
    block = ed.document().findBlock(pos)
    return [(r.start, r.length) for r in block.layout().formats()
            if r.format.underlineStyle() == QTextCharFormat.UnderlineStyle.SpellCheckUnderline]


def test_highlighter_marks_typed_word_without_touching_styles(window, quiet, qtbot, tmp_path, service):
    view, ctrl, block, ed = _open_editor(window, _helv_pdf(tmp_path / "h.pdf"))
    hl = attach_spellcheck(ed, service, "it")
    assert isinstance(hl, SpellHighlighter)
    assert _spell_ranges(ed, 0) == []
    ed.moveCursor(QTextCursor.MoveOperation.End)
    t0 = time.perf_counter()
    qtbot.keyClicks(ed, " lavorazzione")
    assert time.perf_counter() - t0 < perf_limit(2.0)
    text = ed.document().begin().text()
    start = text.index("lavorazzione")
    assert (start, len("lavorazzione")) in _spell_ranges(ed, start)
    # i formati dei caratteri (da cui si ricavano i run) non cambiano
    paras = ed.paragraphs()
    for para in paras:
        for run in para:
            assert run.style.same_look(block.style)
            assert not run.style.underline
    fmt_cursor = QTextCursor(ed.document())
    fmt_cursor.setPosition(start + 2)
    assert fmt_cursor.charFormat().underlineStyle() != QTextCharFormat.UnderlineStyle.SpellCheckUnderline
    # correzione dal menu contestuale
    cur = QTextCursor(ed.document())
    cur.setPosition(start + 3)
    pos = ed.cursorRect(cur).center()
    menu = build_context_menu(ed, hl, pos)
    texts = [a.text() for a in menu.actions()]
    assert "lavorazione" in texts[:5]
    assert "Ignora" in texts and "Ignora tutto" in texts and "Aggiungi al dizionario" in texts
    assert len(texts) > 8                                # voci standard di QTextEdit mantenute
    next(a for a in menu.actions() if a.text() == "lavorazione").trigger()
    assert ed.plain_text() == "Benvenuti nella nostra sede lavorazione"
    assert _spell_ranges(ed, start) == []
    assert all(r.style.same_look(block.style) for p in ed.paragraphs() for r in p)
    ctrl.cancel_editor(view)


def test_highlighter_menu_ignore_and_add(window, quiet, qtbot, tmp_path, service):
    view, ctrl, _block, ed = _open_editor(window, _helv_pdf(tmp_path / "h.pdf", "Il zorbillo e il flumbo"))
    hl = attach_spellcheck(ed, service, "it")
    text = ed.document().begin().text()
    for word, label in (("zorbillo", "Ignora tutto"), ("flumbo", "Aggiungi al dizionario")):
        s = text.index(word)
        assert (s, len(word)) in _spell_ranges(ed, s)
        cur = QTextCursor(ed.document())
        cur.setPosition(s + 2)
        menu = build_context_menu(ed, hl, ed.cursorRect(cur).center())
        next(a for a in menu.actions() if a.text() == label).trigger()
        assert (s, len(word)) not in _spell_ranges(ed, s)
    assert "flumbo" in service.personal_words()
    assert "zorbillo" in service.ignored
    ctrl.cancel_editor(view)


def test_missing_language_status_and_no_underline(window, quiet, qtbot, tmp_path, service):
    view, ctrl, _block, ed = _open_editor(window, _helv_pdf(tmp_path / "h.pdf", "Il zorbillo"))
    messages = []
    attach_spellcheck(ed, service, "qq", status=messages.append)
    assert messages and "qq" in messages[0]
    assert _spell_ranges(ed, 0) == []
    ctrl.cancel_editor(view)


def _commit_typed(window, ctrl, path, service, with_spell: bool, qtbot, out):
    view, ctrl, _block, ed = _open_editor(window, path)
    if with_spell:
        attach_spellcheck(ed, service, "it")
    ed.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClicks(ed, " lavorazzione sbagliatta")
    ctrl.commit_editor(view)
    view.document.save(out)
    return out


def test_highlighting_does_not_change_saved_output(window, quiet, qtbot, tmp_path, service):
    ctrl = window.ctx.text_edit
    a = _commit_typed(window, ctrl, _helv_pdf(tmp_path / "a.pdf"), service, True, qtbot, tmp_path / "a_out.pdf")
    b = _commit_typed(window, ctrl, _helv_pdf(tmp_path / "b.pdf"), service, False, qtbot, tmp_path / "b_out.pdf")
    da, db = pymupdf.open(str(a)), pymupdf.open(str(b))
    assert da[0].get_text() == db[0].get_text()
    assert "lavorazzione sbagliatta" in da[0].get_text()
    assert da[0].get_pixmap(dpi=100).samples == db[0].get_pixmap(dpi=100).samples


def test_dialog_change_each_word(window, quiet, qtbot, tmp_path, service):
    view = window.open_path(_errors_pdf(tmp_path / "d.pdf"))
    assert view is not None
    dlg = SpellCheckDialog(window.ctx, service, window, lang="it")
    qtbot.addWidget(dlg)
    dlg.show()
    assert len(dlg.issues) == 5
    assert view.search_hits                                 # parola evidenziata nella pagina
    for _k in range(5):
        issue = dlg.current_issue()
        assert issue is not None and issue.editable
        assert dlg.change_btn.isEnabled()
        dlg.replacement.setEditText(ERRORS[issue.word])
        dlg.change_btn.click()
    assert dlg.issues == []
    assert not quiet["errors"]
    out = tmp_path / "corretto.pdf"
    view.document.save(out)
    text = pymupdf.open(str(out))[0].get_text()
    for wrong, right in ERRORS.items():
        assert right in text and wrong not in text
    dlg.close()
    assert view.search_hits == {}


def test_dialog_change_all_ignore_and_non_editable(window, quiet, qtbot, tmp_path, service):
    lines = ["Il zorbillo è qui.", "Un altro zorbillo là.", "Poi flumbo e garanzzia."]
    view = window.open_path(_errors_pdf(tmp_path / "d.pdf", lines))
    dlg = SpellCheckDialog(window.ctx, service, window, lang="it")
    qtbot.addWidget(dlg)
    dlg.recheck()
    words = [i.word for i in dlg.issues]
    assert words.count("zorbillo") == 2 and "flumbo" in words and "garanzzia" in words
    version = view.document.version
    dlg.select(words.index("zorbillo"))
    dlg.replacement.setEditText("orologio")
    dlg.change_all_btn.click()
    assert view.document.version == version + 1             # un solo passo di annulla
    assert _text(view.document).count("orologio") == 2
    words = [i.word for i in dlg.issues]
    assert words == ["flumbo", "garanzzia"]
    dlg.select(0)
    dlg.add_btn.click()
    assert "flumbo" in service.personal_words()
    assert [i.word for i in dlg.issues] == ["garanzzia"]
    # blocco non modificabile: correzione disattivata
    dlg.issues[0].editable = False
    dlg.issues[0].reason = "non decodificabile"
    dlg.select(0)
    assert not dlg.change_btn.isEnabled() and not dlg.change_all_btn.isEnabled()
    assert dlg.ignore_btn.isEnabled()
    dlg.ignore_btn.click()
    assert dlg.issues == []
    dlg.recheck()
    assert dlg.issues == []                                 # «Ignora» resta valido nel dialogo


def test_dialog_missing_language(window, quiet, qtbot, tmp_path, service):
    window.open_path(_errors_pdf(tmp_path / "d.pdf", lang="qq-QQ"))
    dlg = SpellCheckDialog(window.ctx, service, window)
    qtbot.addWidget(dlg)
    assert any("qq" in m for m in quiet["status"])
    assert dlg.language() in service.available_languages()


def test_dictionary_dialog(window, quiet, qtbot, tmp_path, service, monkeypatch):
    dlg = DictionaryDialog(window.ctx, service, window)
    qtbot.addWidget(dlg)
    assert str(service.personal_path) in dlg.path_label.text()
    dlg.new_word.setText("Cronografone")
    dlg.add_btn.click()
    assert service.personal_words() == ["Cronografone"]
    exported = tmp_path / "esportato.txt"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(exported))
    dlg.export_btn.click()
    assert exported.read_text("utf-8") == "Cronografone\n"
    imp = tmp_path / "imp.txt"
    imp.write_text("Lunetta\nScappamento\n", "utf-8")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(imp)])
    dlg.import_btn.click()
    assert dlg.words.count() == 3
    dlg.words.item(0).setSelected(True)
    dlg.remove_btn.click()
    assert dlg.words.count() == 2
    # installazione e rimozione di un dizionario senza riavvio
    src = tmp_path / "src"
    src.mkdir()
    (src / "xx_XX.dic").write_text("1\nbarabba\n", "utf-8")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(src / "xx_XX.dic")])
    dlg.install_btn.click()
    assert "xx" in service.available_languages()
    row = next(i for i in range(dlg.languages.count()) if "(xx)" in dlg.languages.item(i).text())
    dlg.languages.setCurrentRow(row)
    dlg.remove_dict_btn.click()
    assert "xx" not in service.available_languages()
