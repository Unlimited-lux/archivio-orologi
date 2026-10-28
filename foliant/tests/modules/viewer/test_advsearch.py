"""VIS-25 — Ricerca avanzata e su più documenti."""

from __future__ import annotations

import csv
import os
import time

import pymupdf
import pytest

from foliant.modules.viewer import advsearch, advsearch_engine as eng


def _ctl(window):
    parts = window.ctx.viewer_parts
    if "advsearch" not in parts:
        parts["advsearch"] = advsearch.install(window.ctx)
    return parts["advsearch"]


def _pdf(path, pages, extra=None, **save):
    doc = pymupdf.open()
    for i, lines in enumerate(pages):
        page = doc.new_page()
        y = 100
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 16
        if extra:
            extra(doc, page, i)
    doc.save(str(path), **save)
    doc.close()
    return str(path)


def _leaf_items(tree):
    out = []
    for i in range(tree.topLevelItemCount()):
        f = tree.topLevelItem(i)
        for j in range(f.childCount()):
            p = f.child(j)
            for k in range(p.childCount()):
                out.append(p.child(k))
    return out


# ------------------------------------------------------------------ motore puro

def test_engine_modes_on_string():
    o = eng.AdvOptions(query="orologio verde", mode=eng.ALL_WORDS)
    assert len(eng.find_in_string("Un orologio rosso", o)) == 0
    assert len(eng.find_in_string("Un orologio verde", o)) == 2
    o.mode = eng.ANY_WORD
    assert len(eng.find_in_string("Un orologio rosso", o)) == 1
    o.mode = eng.PHRASE
    assert eng.find_in_string("Il mio orologio  verde è qui", o)[0][1] == "orologio verde"
    assert eng.validate(eng.AdvOptions(query="(abc", mode=eng.REGEX))
    assert eng.validate(eng.AdvOptions(query="(abc", mode=eng.PHRASE)) is None


def test_regex_guard_stops_pathological_expression(tmp_path):
    path = _pdf(tmp_path / "lento.pdf", [["a" * 34 + "!"], ["CAP 20121"]])
    opts = eng.AdvOptions(query=r"(a+)+$|\b\d{5}\b", mode=eng.REGEX)

    class P:
        def update(self, *a):
            pass

    import threading
    t0 = time.monotonic()
    hits, summary = eng.search_files(P(), [path], opts, threading.RLock(), page_timeout=0.5)
    assert time.monotonic() - t0 < 10
    assert summary.timed_out == [(path, 0)]
    assert [h.found for h in hits] == ["20121"]


# ------------------------------------------------------------------ interfaccia

def test_action_menu_and_non_modal_panel(viewer_window, sample_pdf):
    ctl = _ctl(viewer_window)
    assert ctl.act_open.shortcut().toString() == "Ctrl+Shift+F"
    viewer_window.open_path(sample_pdf)
    panel = ctl.open()
    assert panel.isVisible() and not panel.isModal()
    assert ctl.act_open in viewer_window.ctx.mode("leggi").toolbar.actions()
    assert ctl.act_open.isEnabled()


def test_regex_cap_three_results(viewer_window, tmp_path):
    path = _pdf(tmp_path / "cap.pdf", [["Milano 20121, Roma 00184", "telefono 0212345678"], ["Torino 10121 anno 2024"]])
    viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    assert ctl.search(r"\b\d{5}\b", scope=advsearch.SCOPE_CURRENT, mode=eng.REGEX) == 3
    assert sorted(h.found for h in ctl.hits) == ["00184", "10121", "20121"]
    items = _leaf_items(ctl.panel.tree)
    assert len(items) == 3 and "<b>20121</b>" in items[0].text(0)


def test_invalid_regex_error_in_field(viewer_window, sample_pdf, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    called = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: called.append(a))
    viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.search("(abc", mode=eng.REGEX) == 0
    assert ctl.panel.error.isVisible() and "non valida" in ctl.panel.error.text()
    assert not ctl.panel.search_button.isEnabled()
    assert not called
    ctl.configure("abc")
    assert not ctl.panel.error.isVisible()


def test_folder_with_encrypted_files(viewer_window, tmp_path):
    folder = tmp_path / "archivio"
    (folder / "sotto").mkdir(parents=True)
    for i in range(6):
        words = ["Orologio da tasca" if i % 2 == 0 else "Cassa in acciaio"]
        _pdf(folder / ("sotto" if i > 3 else "") / f"doc{i}.pdf", [words, ["fine"]])
    for name in ("cifrato1.pdf", "cifrato2.pdf"):
        _pdf(folder / name, [["Orologio segreto"]], encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="x", owner_pw="y")
    (folder / "rotto.txt").write_text("non pdf")
    ctl = _ctl(viewer_window)
    ctl.open()
    n = ctl.search("orologio", scope=advsearch.SCOPE_FOLDER, folder=str(folder))
    files = sorted({h.file.rsplit("/", 1)[1] for h in ctl.hits})
    assert files == ["doc0.pdf", "doc2.pdf", "doc4.pdf"] and n == 3
    assert ctl.summary.files == 6
    assert sorted(p.rsplit("/", 1)[1] for p, _r in ctl.summary.skipped) == ["cifrato1.pdf", "cifrato2.pdf"]
    assert "2 file saltati" in ctl.summary_text and "cifrato1.pdf" in ctl.summary_text
    assert ctl.panel.tree.topLevelItemCount() == 3
    # senza sottocartelle
    assert ctl.search("orologio", recursive=False) == 2
    # clic su un risultato → apre il file alla pagina
    view = ctl.go_to(0)
    assert view is not None and view.document.path.name == ctl.hits[0].file.rsplit("/", 1)[1]
    assert view.current_hit == (0, 0)


def test_include_comments_click_goes_to_page(viewer_window, tmp_path):
    def extra(doc, page, i):
        if i == 2:
            a = page.add_text_annot((300, 400), "Da verificare con il cliente")
            a.update()

    path = _pdf(tmp_path / "commenti.pdf", [["uno"], ["due"], ["tre"]], extra)
    view = viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.search("verificare", scope=advsearch.SCOPE_CURRENT, mode=eng.PHRASE, comments=False) == 0
    assert ctl.search("verificare", comments=True) == 1
    hit = ctl.hits[0]
    assert hit.kind == eng.KIND_COMMENT and hit.page == 2 and hit.found == "verificare"
    item = _leaf_items(ctl.panel.tree)[0]
    assert "Commento" in item.text(0)
    ctl.panel.tree.itemClicked.emit(item, 0)
    assert view.current_page == 2
    assert view.current_hit == (2, 0)
    d = pymupdf.open(path)
    pg = d[2]
    annot_rect = pymupdf.Rect(next(pg.annots()).rect)
    del pg
    d.close()
    assert pymupdf.Rect(view.search_hits[2][0]) == annot_rect


def test_all_words_and_any_word(viewer_window, tmp_path):
    path = _pdf(tmp_path / "parole.pdf", [["cassa in oro"], ["cinturino in pelle"], ["cassa e cinturino"]])
    viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.open()
    ctl.search("cassa cinturino", scope=advsearch.SCOPE_CURRENT, mode=eng.ALL_WORDS)
    assert {h.page for h in ctl.hits} == {2} and len(ctl.hits) == 2
    ctl.search(mode=eng.ANY_WORD)
    assert {h.page for h in ctl.hits} == {0, 1, 2} and len(ctl.hits) == 4


def test_open_documents_scope_and_activation(viewer_window, tmp_path):
    a = viewer_window.open_path(_pdf(tmp_path / "a.pdf", [["Rolex Submariner"]]))
    b = viewer_window.open_path(_pdf(tmp_path / "b.pdf", [["niente"], ["Rolex Daytona"]]))
    _pdf(tmp_path / "c.pdf", [["Rolex non aperto"]])
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.search("rolex", scope=advsearch.SCOPE_CURRENT) == 1
    assert ctl.search("rolex", scope=advsearch.SCOPE_OPEN) == 2
    assert viewer_window.ctx.current_view() is b
    first = next(i for i, h in enumerate(ctl.hits) if h.file.endswith("a.pdf"))
    assert ctl.go_to(first) is a
    assert viewer_window.ctx.current_view() is a and a.current_hit == (0, 0)


def test_bookmarks_fields_metadata(viewer_window, tmp_path):
    def extra(doc, page, i):
        if i == 0:
            w = pymupdf.Widget()
            w.field_name = "cliente"
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            w.rect = pymupdf.Rect(72, 200, 300, 220)
            w.field_value = "Officina Bianchi"
            page.add_widget(w)

    path = tmp_path / "misti.pdf"
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page()
        p.insert_text((72, 100), "testo", fontsize=12)
        extra(doc, p, i)
    doc.set_toc([[1, "Capitolo Bianchi", 2]])
    doc.set_metadata({"author": "Studio Bianchi"})
    doc.save(str(path))
    doc.close()
    viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    ctl.open()
    # il valore del campo è anche nel testo della pagina (aspetto del campo)
    assert ctl.search("bianchi", scope=advsearch.SCOPE_CURRENT) == 1
    assert ctl.hits[0].kind == eng.KIND_TEXT
    ctl.search("bianchi", bookmarks=True, fields=True, metadata=True)
    kinds = sorted(h.kind for h in ctl.hits)
    assert eng.KIND_BOOKMARK in kinds and eng.KIND_FIELD in kinds and eng.KIND_METADATA in kinds
    bm = next(h for h in ctl.hits if h.kind == eng.KIND_BOOKMARK)
    assert bm.page == 1
    view = ctl.go_to(ctl.hits.index(bm))
    assert view.current_page == 1


def test_export_csv(viewer_window, tmp_path):
    path = _pdf(tmp_path / "csv.pdf", [["Orologio uno"], ["Orologio due"]])
    viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.open()
    ctl.search("orologio", scope=advsearch.SCOPE_CURRENT)
    out = ctl.export_csv(str(tmp_path / "out.csv"))
    with open(out, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["file", "pagina", "tipo", "estratto"]
    assert rows[1] == [path, "1", "Testo", "Orologio uno"]
    assert rows[2][1] == "2" and len(rows) == 3


def test_visual_capture(viewer_window, tmp_path, request):
    import os
    out = os.environ.get("FOLIANT_CAPTURE_DIR")
    if not out:
        pytest.skip("solo per la verifica visiva")
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme

    def extra(doc, page, i):
        page.add_text_annot((300, 300), "Da verificare il CAP 20121").update()

    path = _pdf(tmp_path / "vista.pdf", [["Milano 20121, Roma 00184"], ["Torino 10121"]], extra)
    viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    for name in ("light", "dark"):
        theme.apply(QApplication.instance(), name)
        ctl.open()
        ctl.search(r"\b\d{5}\b", scope=advsearch.SCOPE_CURRENT, mode=eng.REGEX, comments=True)
        ctl.go_to(0)
        viewer_window.ctx.current_view().flush_rendering()
        QApplication.processEvents()
        viewer_window.grab().save(f"{out}/adv_win_{name}.png")
        ctl.panel.grab().save(f"{out}/adv_panel_{name}.png")
    theme.apply(QApplication.instance(), "light")


def test_option_checkboxes_are_spaced(viewer_window, sample_pdf):
    viewer_window.open_path(sample_pdf)
    panel = _ctl(viewer_window).open()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    for boxes in ((panel.opt_case, panel.opt_word, panel.opt_accents),
                  (panel.inc_comments, panel.inc_bookmarks, panel.inc_fields, panel.inc_metadata)):
        for a, b in zip(boxes, boxes[1:]):
            assert b.geometry().left() - a.geometry().right() >= 12
    panel.hide()


def test_search_inside_pdf_attachments(viewer_window, tmp_path):
    """Allegati PDF inclusi: l'occorrenza è nell'allegato, il clic apre l'allegato in una scheda
    alla pagina giusta; senza l'opzione l'allegato non si cerca."""
    inner = pymupdf.open()
    inner.new_page().insert_text((72, 100), "Introduzione")
    inner.new_page().insert_text((72, 100), "Certificato Patek Philippe 5711")
    path = _pdf(tmp_path / "con_allegato.pdf", [["Documento principale"]])
    doc = pymupdf.open(path)
    doc.embfile_add("certificato.pdf", inner.tobytes(), filename="certificato.pdf")
    doc.embfile_add("note.txt", b"Patek nel testo semplice", filename="note.txt")
    doc.saveIncr()
    doc.close()
    view = viewer_window.open_path(path)
    ctl = _ctl(viewer_window)
    ctl.open()
    assert ctl.search("Patek", scope=advsearch.SCOPE_CURRENT, mode=eng.PHRASE) == 0
    assert ctl.search("Patek", attachments=True) == 1
    hit = ctl.hits[0]
    assert hit.attachment == "certificato.pdf" and hit.page == 1 and hit.kind == eng.KIND_TEXT
    item = _leaf_items(ctl.panel.tree)[0]
    assert "certificato.pdf" in item.text(0)
    ctl.panel.tree.itemClicked.emit(item, 0)
    opened = viewer_window.current_view()
    assert opened is not view and opened.document.display_name == "certificato.pdf"
    assert opened.current_page == 1 and opened.current_hit == (1, 0)


def test_folder_search_includes_attachments(viewer_window, tmp_path):
    folder = tmp_path / "archivio"
    folder.mkdir()
    inner = pymupdf.open()
    inner.new_page().insert_text((72, 100), "Garanzia Omega Speedmaster")
    path = _pdf(folder / "pratica.pdf", [["Pratica senza parola chiave"]])
    doc = pymupdf.open(path)
    doc.embfile_add("garanzia.pdf", inner.tobytes(), filename="garanzia.pdf")
    doc.saveIncr()
    doc.close()
    ctl = _ctl(viewer_window)
    ctl.open()
    ctl.set_folder(str(folder))
    assert ctl.search("Speedmaster", scope=advsearch.SCOPE_FOLDER, mode=eng.PHRASE, attachments=True) == 1
    assert ctl.hits[0].attachment == "garanzia.pdf" and ctl.hits[0].file.endswith("pratica.pdf")


# ------------------------------------------------------------- indice full-text
def _archive(folder, n=4):
    folder.mkdir(exist_ok=True)
    for i in range(n):
        _pdf(folder / f"pratica{i}.pdf", [[f"Pratica numero {i}"], [f"Cliente {'Rossi' if i == 2 else 'Bianchi'}"]])
    enc = pymupdf.open()
    enc.new_page().insert_text((72, 100), "Rossi riservato")
    enc.save(str(folder / "cifrato.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    return folder


def test_fulltext_index_create_search_update_delete(viewer_window, tmp_path):
    from foliant.modules.viewer import fulltext
    folder = _archive(tmp_path / "archivio")
    ctl = _ctl(viewer_window)
    ctl.open()
    stats = ctl.create_index(str(folder), "Archivio pratiche")
    assert stats.added == 4 and [os.path.basename(p) for p, _r in stats.skipped] == ["cifrato.pdf"]
    assert "Archivio_pratiche" in fulltext.list_indexes()
    p = ctl.ensure_panel()
    assert p.scope.currentData() == advsearch.SCOPE_INDEX and p.index_box.isVisibleTo(p)
    assert ctl.search("Rossi", mode=eng.PHRASE) == 1
    hit = ctl.hits[0]
    assert hit.file.endswith("pratica2.pdf") and hit.page == 1 and not hit.stale
    # clic: apre il file alla pagina e calcola l'evidenziazione
    view = ctl.go_to(0)
    assert view is not None and view.current_page == 1 and view.current_hit == (1, 0)
    # file cambiato dopo l'indicizzazione: segnalato; «Aggiorna» rilegge solo quello
    time.sleep(0.01)
    _pdf(folder / "pratica3.pdf", [["Pratica numero 3"], ["Cliente Rossi trasferito"]])
    assert ctl.search("Rossi") == 1 and "aggiorna l'indice" in ctl.summary_text
    stats = ctl.update_index()
    assert (stats.updated, stats.added, stats.unchanged) == (1, 0, 3)
    assert ctl.search("Rossi") == 2
    os.remove(folder / "pratica0.pdf")
    assert ctl.update_index().removed == 1
    assert ctl.delete_index(confirm=False)
    assert "Archivio_pratiche" not in fulltext.list_indexes()


def test_fulltext_modes_and_regex():
    from foliant.modules.viewer import fulltext
    opts = eng.AdvOptions(query="orologio automatico", mode=eng.ALL_WORDS)
    assert fulltext._fts_query(opts) == '"orologio" AND "automatico"'
    opts.mode = eng.ANY_WORD
    assert fulltext._fts_query(opts) == '"orologio" OR "automatico"'
    opts.mode = eng.PHRASE
    assert fulltext._fts_query(opts) == '"orologio automatico"'
    opts.mode = eng.REGEX
    assert fulltext._fts_query(opts) is None
