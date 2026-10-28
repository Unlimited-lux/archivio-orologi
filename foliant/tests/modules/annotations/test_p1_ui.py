"""Criteri P1 verificati dall'interfaccia: appunti, flag, area, correzioni, commento di testo,
callout, poligoni, timbri, allegati, filtri, ordinamento, risposte, stato, visibilità,
appiattimento, esportazione e importazione, riepilogo."""

from __future__ import annotations

import re

import pymupdf
from PySide6.QtCore import Qt

from foliant.modules.annotations import dialogs, engine, ui_actions

from .conftest import annots, click, double_click, drag, key, key_of, saved


def _rect(module, a=(100, 400), b=(200, 500), page=0):
    return module.ctrl.apply("r", engine.create_shape, page, engine.RECT, [a, b], engine.Style(width=1), "A")


def test_ann07_copy_paste_other_page(comments, tmp_path):
    """ANN-07: rettangolo copiato a pagina 1 e incollato a pagina 3: stessi /C /IC /BS /Rect, /NM diverso."""
    window, view, module = comments
    xref = module.ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(100, 400), (200, 500)],
                             engine.Style(color=(1, 0, 0), fill=(0, 1, 0), width=2), "A")
    module.activate("select")
    click(view, 0, 150, 450)
    key(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    view.go_to_page(2)
    module.ctrl.paste(2)
    doc = saved(window, view, tmp_path)
    a, = annots(doc, 0, "Square")
    b, = annots(doc, 2, "Square")
    for k in ("C", "IC", "BS/W"):
        assert key_of(doc, a.xref, k) == key_of(doc, b.xref, k)
    assert a.rect == b.rect
    assert a.info["id"] != b.info["id"]
    assert key_of(doc, b.xref, "P")[1] == f"{doc[2].xref} 0 R"
    assert xref


def test_ann07_cut_paste_keeps_identity(comments):
    """ANN-07: taglia+incolla sposta la nota conservando /NM, autore e data di creazione."""
    window, view, module = comments
    xref = module.ctrl.apply("n", engine.create_note, 1, (300, 300), "nota", engine.Style(), "Mario")
    info = module.ctrl.info(1, xref)
    module.ctrl.select([(1, xref)], view)
    assert module.ctrl.copy(cut=True)
    (page, new), = module.ctrl.paste(3)
    assert module.ctrl.info(1, xref) is None
    moved = module.ctrl.info(3, new)
    assert (moved.name, moved.author, moved.created) == (info.name, info.author, info.created)


def test_ann07_cut_paste_moves_replies(comments):
    """ANN-07: nota con due risposte tagliata da pagina 2 e incollata a pagina 4: risposte collegate con /IRT."""
    window, view, module = comments
    xref = module.ctrl.apply("n", engine.create_note, 1, (300, 300), "nota", engine.Style(), "Mario")
    module.ctrl.reply(1, xref, "prima")
    module.ctrl.reply(1, xref, "seconda")
    module.ctrl.set_status(1, xref, engine.STATE_ACCEPTED)
    module.ctrl.select([(1, xref)], view)
    module.ctrl.copy(cut=True)
    (page, new), = module.ctrl.paste(3)
    assert module.ctrl.annotations() and all(i.page == 3 for i in module.ctrl.annotations())
    moved = module.ctrl.info(3, new)
    assert sorted(r.contents for r in moved.replies) == ["prima", "seconda"]
    assert moved.status == engine.STATE_ACCEPTED


def test_ann07_duplicate_same_page_offset(comments):
    window, view, module = comments
    xref = _rect(module)
    module.ctrl.select([(0, xref)], view)
    (p, new), = module.ctrl.duplicate()
    r1, r2 = module.ctrl.info(0, xref).rect, module.ctrl.info(0, new).rect
    assert abs(r2.x0 - r1.x0 - 10) < 1 and abs(r2.y0 - r1.y0 - 10) < 1


def test_ann09_flags(comments, tmp_path):
    """ANN-09: Stampa disattivata → bit 3 di /F a 0; nuove annotazioni con Print attivo."""
    window, view, module = comments
    xref = _rect(module)
    assert module.ctrl.info(0, xref).flags & engine.FLAG_PRINT
    module.ctrl.select([(0, xref)], view)
    module.properties.refresh()
    module.properties.printable.setChecked(False)
    doc = saved(window, view, tmp_path)
    assert not annots(doc, 0, "Square")[0].flags & pymupdf.PDF_ANNOT_IS_PRINT


def test_ann09_locked_contents(comments, monkeypatch):
    """ANN-09: con «Contenuto bloccato» il testo non è modificabile ma la casella si sposta."""
    window, view, module = comments
    xref = module.ctrl.apply("t", engine.create_freetext, 0, (100, 400, 300, 450), "Fisso", engine.Style(), "A")
    module.ctrl.set_flag(engine.FLAG_LOCKED_CONTENTS, True, [(0, xref)])
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: ("Cambiato", None))
    assert not module.ctrl.edit_text(0, xref)
    assert module.ctrl.info(0, xref).contents == "Fisso"
    assert module.ctrl.move(10, 0, [(0, xref)])


def test_ann11_area_highlight(comments, tmp_path):
    """ANN-11: area da (100,100) a (300,200) → /Highlight con /Rect e un solo quadrilatero; clic → nulla."""
    window, view, module = comments
    module.activate("area-highlight")
    click(view, 0, 400, 400)
    assert module.ctrl.annotations() == []
    drag(view, 0, (100, 500), (300, 600))
    (p, xref), = module.ctrl.selection
    info = module.ctrl.info(0, xref)
    assert abs(info.rect.x0 - 100) < 1.5 and abs(info.rect.x1 - 300) < 1.5
    # l'area si ridimensiona: /QuadPoints e /Rect estesi insieme
    module.ctrl.resize(0, xref, pymupdf.Rect(info.rect.x0, info.rect.y0, info.rect.x1 + 50, info.rect.y1))
    with view.document.lock:
        page = view.document.pdf[0]
        verts = page.load_annot(xref).vertices
    assert len(verts) == 4 and abs(max(v[0] for v in verts) - 350) < 2


def test_ann13_insert_and_replace(comments, tmp_path, monkeypatch):
    """ANN-13: «Inserisci testo» → /Caret con /Contents; «Sostituisci» → /StrikeOut con /IRT al /Caret e /RT /Group."""
    window, view, module = comments
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "non ")
    module.activate(engine.CARET)
    words = [w for w in view.words(0) if w[4] == "Lorem"]
    r = pymupdf.Rect(words[0][:4])
    click(view, 0, r.x1 + 1, (r.y0 + r.y1) / 2)
    from foliant.api import selection_from_words
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "blu")
    words2 = [w for w in view.words(0) if w[4] == "Prima"]
    view.set_text_selection(selection_from_words(0, words2))
    module.tools[engine.REPLACE].apply_selection(view)
    module.activate("paragraph")
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "")
    click(view, 0, 400, 700)
    doc = saved(window, view, tmp_path)
    carets = annots(doc, 0, "Caret")
    assert len(carets) == 3
    first = [c for c in carets if c.info["content"] == "non "][0]
    assert abs((first.rect.x0 + first.rect.x1) / 2 - (r.x1 + 1)) < 2.5
    blu = [c for c in carets if c.info["content"] == "blu"][0]
    strike = annots(doc, 0, "StrikeOut")[0]
    assert strike.irt_xref == blu.xref
    assert key_of(doc, strike.xref, "RT")[1] == "/Group"
    assert any(key_of(doc, c.xref, "Sy")[1] == "/P" for c in carets)
    # il pannello mostra una sola voce «Sostituzione»; eliminare il barrato elimina il gruppo
    infos = module.ctrl.annotations()
    labels = sorted(i.label for i in infos)
    assert labels.count("Sostituzione") == 1 and "Barrato" not in labels
    with view.document.lock:
        sx = [a.xref for a in view.document.pdf[0].annots() if a.type[1] == "StrikeOut"][0]
    module.ctrl.delete([(0, sx)])
    with view.document.lock:
        assert not any(a.type[1] == "StrikeOut" for a in view.document.pdf[0].annots())
        assert len([a for a in view.document.pdf[0].annots() if a.type[1] == "Caret"]) == 2
    view.document.undo()
    with view.document.lock:
        assert len([a for a in view.document.pdf[0].annots() if a.type[1] == "Caret"]) == 3


def test_ann15_typewriter(comments, tmp_path, monkeypatch):
    """ANN-15: «Mario Rossi» 11 pt nero → /FreeText /IT /FreeTextTypeWriter, /DA 11 nero, senza bordo."""
    window, view, module = comments
    st = module.ctrl.style_for(engine.FREETEXT)
    st.fontsize, st.text_color = 11, (0, 0, 0)
    module.ctrl.set_style_for(engine.FREETEXT, st)
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "Mario Rossi")
    module.activate(engine.TYPEWRITER)
    click(view, 0, 100, 600)
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "   ")
    click(view, 0, 100, 700)
    doc = saved(window, view, tmp_path)
    ft, = annots(doc, 0, "FreeText")
    assert key_of(doc, ft.xref, "IT")[1] == "/FreeTextTypeWriter"
    assert ft.info["content"] == "Mario Rossi"
    da = key_of(doc, ft.xref, "DA")[1]
    assert re.search(r" 11(\.0)? Tf", da) and "0 0 0 rg" in da.replace(".000", "")
    assert not ft.border or ft.border.get("width", 0) == 0
    assert "Mario Rossi" in doc[0].get_text()


def test_ann17_callout(comments, tmp_path, monkeypatch):
    """ANN-17: callout con punta indicata: /IT /FreeTextCallout, /CL che inizia dalla punta, /Rect la contiene."""
    window, view, module = comments
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: ("Vedi qui", None))
    module.activate(engine.CALLOUT)
    drag(view, 0, (100, 500), (220, 420))
    doc = saved(window, view, tmp_path)
    ft, = annots(doc, 0, "FreeText")
    assert key_of(doc, ft.xref, "IT")[1] == "/FreeTextCallout"
    cl = [float(v) for v in key_of(doc, ft.xref, "CL")[1].strip("[]").split()]
    tip = engine.from_pdf_point(doc[0], cl[0], cl[1])
    assert abs(tip[0] - 100) < 1 and abs(tip[1] - 500) < 1
    assert ft.rect.contains(pymupdf.Point(tip))


def test_ann20_polygon_tool(comments, tmp_path):
    """ANN-20: cinque clic e doppio clic → /Polygon con 10 valori; Esc con 2 vertici → nulla."""
    window, view, module = comments
    module.activate(engine.POLYGON)
    pts = [(100, 400), (200, 380), (260, 450), (200, 520), (110, 500)]
    for p in pts:
        click(view, 0, *p)
    double_click(view, 0, *pts[-1])
    module.activate(engine.POLYLINE)
    click(view, 0, 300, 300)
    click(view, 0, 350, 300)
    key(view, Qt.Key.Key_Escape)
    doc = saved(window, view, tmp_path)
    poly, = annots(doc, 0, "Polygon")
    assert len(poly.vertices) == 5
    for (x, y), v in zip(pts, poly.vertices):
        assert abs(v[0] - x) < 0.5 and abs(v[1] - y) < 0.5
    assert annots(doc, 0, "PolyLine") == []


def test_ann21_cloud_tool(comments, tmp_path):
    """ANN-21: nuvola con 4 vertici → /Polygon /IT /PolygonCloud e /BE /S /C."""
    window, view, module = comments
    module.activate(engine.CLOUD)
    for p in ((100, 400), (250, 400), (250, 500), (100, 500)):
        click(view, 0, *p)
    key(view, Qt.Key.Key_Return)
    doc = saved(window, view, tmp_path)
    poly, = annots(doc, 0, "Polygon")
    assert key_of(doc, poly.xref, "IT")[1] == "/PolygonCloud"
    assert key_of(doc, poly.xref, "BE/S")[1] == "/C"


def test_ann24_25_stamp_tools(comments, tmp_path, monkeypatch):
    """ANN-24/25: timbro predefinito con un clic; timbro dinamico con autore e data congelati."""
    window, view, module = comments
    module.use_stamp(engine.StampSpec("Approvato", (0, 0.5, 0), "", "Approved"))
    click(view, 0, 300, 400)
    module.use_stamp(engine.StampSpec("Approvato", (0, 0.5, 0)), template="Approvato da {autore} il {data}")
    click(view, 0, 300, 600)
    doc = saved(window, view, tmp_path)
    stamps = annots(doc, 0, "Stamp")
    contents = sorted(s.info["content"] for s in stamps)
    assert contents[0] == "Approvato"
    assert contents[1].startswith("Approvato da Maria Rossi il ")
    std = [s for s in stamps if s.info["content"] == "Approvato"][0]
    assert key_of(doc, std.xref, "Name")[1] == "/Approved"
    assert std.rect.contains(pymupdf.Point(300, 400))


def test_ann29_attachment_tool(comments, tmp_path, monkeypatch):
    """ANN-29: allegato in un punto; «Salva allegato con nome» produce un file identico."""
    window, view, module = comments
    src = tmp_path / "verbale.docx"
    src.write_bytes(b"contenuto del verbale" * 100)
    monkeypatch.setattr(dialogs, "ask_open_file", lambda *a, **k: str(src))
    module.activate(engine.ATTACHMENT)
    click(view, 0, 100, 700)
    (p, xref), = module.ctrl.selection
    out = tmp_path / "estratto.docx"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    ui_actions.save_attachment(module.ctrl, p, xref)
    assert out.read_bytes() == src.read_bytes()
    doc = saved(window, view, tmp_path)
    fa, = annots(doc, 0, "FileAttachment")
    assert fa.file_info["filename"] == "verbale.docx"


def test_ann32_area_measure_tool(comments, tmp_path):
    """ANN-32: con scala 100 pt = 1 m un quadrato 100×100 pt misura «1,00 m²»."""
    window, view, module = comments
    module.ctrl.scale = engine.Scale.calibrate(100, 1, "m")
    module.activate(engine.AREA)
    for p in ((100, 400), (200, 400), (200, 500), (100, 500)):
        click(view, 0, *p)
    key(view, Qt.Key.Key_Return)
    doc = saved(window, view, tmp_path)
    poly, = annots(doc, 0, "Polygon")
    assert poly.info["content"] == "1,00 m²"
    assert key_of(doc, poly.xref, "IT")[1] == "/PolygonDimension"


def test_ann31_distance_tool(comments, tmp_path):
    """ANN-31: scala 72 pt = 1 in, misura da (100,100) a (244,100) → «2,00 in»."""
    window, view, module = comments
    module.ctrl.scale = engine.Scale.from_ratio(72, "pt", 1, "in")
    module.activate(engine.DISTANCE)
    drag(view, 0, (100, 100), (244, 100))
    doc = saved(window, view, tmp_path)
    line, = annots(doc, 0, "Line")
    assert line.info["content"] == "2,00 in"
    assert key_of(doc, line.xref, "IT")[1] == "/LineDimension"


def _populate(module):
    ctrl = module.ctrl
    a = ctrl.apply("h", engine.create_markup, 0, engine.HIGHLIGHT, [pymupdf.Rect(72, 290, 260, 303)],
                   engine.Style(), "Anna", "Verificare la scadenza")
    b = ctrl.apply("n", engine.create_note, 1, (300, 300), "Tutto bene", engine.Style(), "Luca")
    c = ctrl.apply("n", engine.create_note, 2, (300, 300), "Altro", engine.Style(), " Anna ")
    return a, b, c


def test_ann35_filters_and_search(comments):
    """ANN-35: filtro per autore con contatore «n di N»; ricerca senza maiuscole e accenti."""
    window, view, module = comments
    a, b, c = _populate(module)
    panel = module.comments
    panel.reload()
    panel.set_filter("authors", {"Anna"})
    assert {tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()} == {(0, a), (2, c)}
    assert panel.count.text() == "2 di 3 commenti"
    panel.set_filter("authors", set())
    panel.search.setText("SCADENZA")
    assert [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()] == [(0, a)]
    panel.search.setText("nessuna corrispondenza")
    assert panel.empty.isVisibleTo(panel) and "Nessun commento corrisponde" in panel.empty.text()


def test_ann35_filter_type_and_status(comments):
    window, view, module = comments
    a, b, c = _populate(module)
    module.ctrl.set_status(0, a, engine.STATE_ACCEPTED)
    panel = module.comments
    panel.reload()
    panel.set_filter("types", {engine.HIGHLIGHT})
    panel.set_filter("status", {engine.STATE_ACCEPTED})
    assert [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()] == [(0, a)]


def test_ann36_sort_by_date_desc(comments, monkeypatch):
    """ANN-36: ordinamento per data di modifica decrescente: prima la più recente."""
    window, view, module = comments
    dates = iter(["D:20260101100000+01'00'", "D:20260301100000+01'00'", "D:20260201100000+01'00'"])
    monkeypatch.setattr(engine, "pdf_now", lambda: next(dates))
    a, b, c = _populate(module)
    panel = module.comments
    panel.reload()
    panel.sort.setCurrentIndex(panel.sort.findData("date_desc"))
    keys = [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()]
    assert keys[0] == (1, b)


def test_ann37_38_reply_and_status_from_ui(comments, monkeypatch):
    """ANN-37/38: risposta e stato dal menu; il pannello mostra risposta e «Accettato»."""
    window, view, module = comments
    a, b, c = _populate(module)
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "D'accordo")
    module.ctrl.reply(0, a)
    module.ctrl.set_status(0, a, engine.STATE_ACCEPTED)
    panel = module.comments
    panel.reload()
    item = panel.item_for(0, a)
    assert item.childCount() == 1 and "D'accordo" in item.child(0).text(0)
    assert "Accettato" in item.text(0)
    item.setCheckState(0, Qt.CheckState.Checked)
    assert module.ctrl.info(0, a).marked


def test_ann39_hide_annotations(comments):
    """ANN-39: «Nascondi annotazioni» → resa senza annotazioni; il file non cambia."""
    window, view, module = comments
    _rect(module, (100, 400), (300, 500))
    view.flush_rendering()
    before = view.document.to_bytes()
    module.ctrl.set_annotations_hidden(True, view)
    view.flush_rendering()
    img = view._render_page(0, 1.0)
    with view.document.lock:
        pix = view.document.pdf[0].get_pixmap(annots=False)
    assert img.width() == pix.width
    color = img.pixelColor(int(100 * 1.0), int(450 * 1.0))
    assert color.red() > 240 and color.green() > 240   # nessun bordo rosso
    assert len(view.document.to_bytes()) == len(before)
    module.ctrl.set_annotations_hidden(False, view)
    img2 = view._render_page(0, 1.0)
    c2 = img2.pixelColor(100, 450)
    assert c2.green() < 200


def test_ann40_flatten_from_ui(comments):
    """ANN-40: appiattimento con conferma e annullabile."""
    window, view, module = comments
    xref = module.ctrl.apply("t", engine.create_freetext, 0, (100, 400, 300, 450), "Totale 100", engine.Style(), "A")
    assert ui_actions.flatten(module.ctrl) == 1
    assert module.ctrl.annotations() == []
    assert "Totale 100" in view.document.page_text(0)
    view.document.undo()
    assert module.ctrl.info(0, xref) is not None


def test_ann41_42_export_import_ui(comments, tmp_path, monkeypatch):
    """ANN-41/42: esportazione XFDF e reimportazione in un'altra copia del documento."""
    window, view, module = comments
    a, b, c = _populate(module)
    module.ctrl.reply(0, a, "Risposta")
    out = tmp_path / "commenti.xfdf"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    assert ui_actions.export_comments(module.ctrl, "xfdf") == str(out)
    ui_actions.delete_all(module.ctrl)
    assert module.ctrl.annotations() == []
    res = ui_actions.import_comments(module.ctrl, [str(out)])
    assert res.imported == 4
    infos = module.ctrl.annotations()
    assert len(infos) == 3 and sum(len(i.replies) for i in infos) == 1
    view.document.undo()
    assert module.ctrl.annotations() == []


def test_ann42_import_from_pdf_and_malformed(comments, tmp_path):
    """ANN-42: importazione da un altro PDF; file malformato → documento invariato e messaggio."""
    window, view, module = comments
    other = pymupdf.open()
    other.new_page()
    p = other[0]
    p.add_text_annot((100, 100), "dal revisore")
    other.save(str(tmp_path / "altro.pdf"))
    res = ui_actions.import_comments(module.ctrl, [str(tmp_path / "altro.pdf")])
    assert res.imported == 1
    bad = tmp_path / "rotto.xfdf"
    bad.write_text("<xfdf><annots><text page='0'")
    version = view.document.version
    assert ui_actions.import_comments(module.ctrl, [str(bad)]) is None
    assert view.document.version == version
    assert any(m[0] == "error" for m in module.messages)


def test_ann43_summary_files(comments, tmp_path, monkeypatch):
    """ANN-43: riepilogo PDF e CSV salvati senza modificare l'originale."""
    window, view, module = comments
    _populate(module)
    version = view.document.version
    for fmt in ("pdf", "csv", "txt", "html"):
        out = tmp_path / f"riepilogo.{fmt}"
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, o=out, **k: str(o))
        assert ui_actions.export_summary(module.ctrl, fmt) == str(out)
        assert out.stat().st_size > 0
    text = pymupdf.open(str(tmp_path / "riepilogo.pdf"))[0].get_text()
    assert "Verificare la scadenza" in text and "Tutto bene" in text
    assert view.document.version == version


def test_context_menu_on_text_selection(comments):
    """Menu contestuale: «Commenta il testo» sulla selezione e «Aggiungi nota qui»."""
    from PySide6.QtWidgets import QMenu
    from foliant.api import PageEvent, selection_from_words
    from PySide6.QtCore import QPointF
    window, view, module = comments
    words = [w for w in view.words(0) if w[4] == "Lorem"]
    view.set_text_selection(selection_from_words(0, words))
    menu = QMenu()
    pe = PageEvent(0, pymupdf.Point(500, 700), QPointF(0, 0), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    module._view_menu(view, pe, menu)
    texts = [a.text() for a in menu.actions()]
    assert "Commenta il testo" in texts and "Aggiungi nota qui" in texts
    sub = [a for a in menu.actions() if a.text() == "Commenta il testo"][0].menu()
    [a for a in sub.actions() if a.text() == "Evidenzia"][0].trigger()
    assert module.ctrl.annotations()[0].kind == engine.HIGHLIGHT



def test_ann20_drag_vertex(comments):
    """ANN-20: trascinando il secondo vertice di una polilinea cambia solo quella coppia e /Rect."""
    window, view, module = comments
    pts = [(100, 400), (200, 420), (300, 400), (400, 450)]
    xref = module.ctrl.apply("p", engine.create_shape, 0, engine.POLYLINE, pts, engine.Style(width=1), "A")
    module.activate("select")
    click(view, 0, 150, 410)
    assert module.ctrl.selection == [(0, xref)]
    drag(view, 0, (200, 420), (220, 480))
    verts = module.ctrl.vertices(0, xref)
    assert abs(verts[1].x - 220) < 0.6 and abs(verts[1].y - 480) < 0.6
    for i in (0, 2, 3):
        assert abs(verts[i].x - pts[i][0]) < 0.1 and abs(verts[i].y - pts[i][1]) < 0.1
    assert module.ctrl.info(0, xref).rect.y1 >= 480


def test_ann17_move_tip_only(comments, monkeypatch):
    """ANN-17: spostando solo la punta la casella non cambia posizione."""
    window, view, module = comments
    xref = module.ctrl.apply("c", engine.create_freetext, 0, (200, 200, 350, 260), "Nota", engine.Style(), "A",
                             callout=[(100, 100), (150, 230), (200, 230)])
    with view.document.lock:
        page = view.document.pdf[0]
        before = engine._freetext_inner(page, page.load_annot(xref))
    assert module.ctrl.move_vertex(0, xref, 0, (80, 150))
    with view.document.lock:
        page = view.document.pdf[0]
        after = engine._freetext_inner(page, page.load_annot(xref))
    assert abs(after.x0 - before.x0) < 0.5 and abs(after.y0 - before.y0) < 0.5
    tip = module.ctrl.vertices(0, xref)[0]
    assert abs(tip.x - 80) < 0.5 and abs(tip.y - 150) < 0.5


def test_ann31_measure_updates_when_vertex_moves(comments):
    """ANN-31: spostando un estremo raddoppiandone la lunghezza il valore raddoppia."""
    window, view, module = comments
    xref = module.ctrl.apply("m", engine.create_measure, 0, engine.DISTANCE, [(100, 100), (200, 100)],
                             engine.Style(), engine.Scale.calibrate(100, 1, "m"), "A")
    assert module.ctrl.info(0, xref).contents == "1,00 m"
    module.ctrl.move_vertex(0, xref, 1, (300, 100))
    assert module.ctrl.info(0, xref).contents == "2,00 m"


def test_ann25_counter_never_decrements(comments):
    """ANN-25: contatore 001, 002, 003; dopo Annulla e nuova apposizione 004."""
    window, view, module = comments
    tpl = "Protocollo n. {contatore} del {data}"
    for i, y in enumerate((200, 300, 400)):
        module.use_stamp(engine.StampSpec("Protocollo"), template=tpl)
        click(view, 0, 300, y)
    texts = sorted(i.contents for i in module.ctrl.annotations())
    assert [t.split()[2] for t in texts] == ["001", "002", "003"]
    view.document.undo()
    module.use_stamp(engine.StampSpec("Protocollo"), template=tpl)
    click(view, 0, 300, 600)
    texts = sorted(i.contents for i in module.ctrl.annotations())
    assert [t.split()[2] for t in texts] == ["001", "002", "004"]


def test_ann26_custom_stamp_library(comments, tmp_path, monkeypatch):
    """ANN-26: timbro da PNG con trasparenza salvato in libreria e disponibile dopo il riavvio."""
    window, view, module = comments
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), 1)
    pix.clear_with(0)
    png = tmp_path / "logo.png"
    png.write_bytes(pix.tobytes("png"))
    monkeypatch.setattr(dialogs, "ask_open_file", lambda *a, **k: str(png))
    module._image_stamp()
    click(view, 0, 300, 300)
    assert [d["name"] for d in module.ctrl.custom_stamps()] == ["logo"]
    from foliant.modules.annotations.controller import Controller
    assert [d["name"] for d in Controller(window.ctx).custom_stamps()] == ["logo"]
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    assert key_of(doc, st.xref, "IT")[1] == "/StampImage"


def test_ann30_calibrate_tool(comments, monkeypatch):
    """ANN-30: segmento di 100 pt calibrato come 5 m; una misura di 50 pt vale «2,50 m»."""
    window, view, module = comments
    monkeypatch.setattr(dialogs, "ask_scale",
                        lambda parent, scale, calibrate_points=None: engine.Scale.calibrate(calibrate_points, 5, "m"))
    module.activate("calibrate")
    drag(view, 0, (100, 300), (200, 300))
    module.activate(engine.DISTANCE)
    drag(view, 0, (100, 400), (150, 400))
    assert module.ctrl.annotations()[0].contents == "2,50 m"


def test_ann33_measures_csv(comments):
    """ANN-33: CSV con intestazione e una riga per misura."""
    import csv
    import io
    window, view, module = comments
    sc = engine.Scale.calibrate(100, 1, "m")
    for kind, pts in ((engine.DISTANCE, [(100, 100), (200, 100)]), (engine.PERIMETER, [(100, 200), (200, 200), (200, 250)]),
                      (engine.AREA, [(100, 300), (200, 300), (200, 400), (100, 400)])):
        module.ctrl.apply("m", engine.create_measure, 0, kind, pts, engine.Style(), sc, "A")
    with view.document.lock:
        rows = list(csv.reader(io.StringIO(engine.measures_csv(view.document.pdf))))
    assert rows[0][0] == "Pagina" and len(rows) == 4
    assert [r[3] for r in rows[1:]] == ["1,00 m", "1,50 m", "1,00 m²"]
    assert float(rows[2][2]) == 1.5


def test_ann03_rotate_stamp(comments, tmp_path):
    """ANN-03: timbro ruotato di 30°: /Rect contiene l'aspetto ruotato e la resa è ruotata."""
    window, view, module = comments
    xref = module.ctrl.apply("s", engine.create_stamp, 0, (300, 400), engine.StampSpec("Approvato", name="Approved"),
                             engine.Style(), "A")
    before = module.ctrl.info(0, xref).rect
    assert module.ctrl.rotate(30, [(0, xref)])
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    w, h = before.width, before.height
    import math
    exp_h = w * math.sin(math.radians(30)) + h * math.cos(math.radians(30))
    assert abs(st.rect.height - exp_h) < 1
    # i pixel colorati occupano gli angoli opposti dell'ingombro (diagonale) e non escono da /Rect
    pix = doc[0].get_pixmap(clip=st.rect + (-3, -3, 3, 3))
    red = [(x, y) for y in range(pix.height) for x in range(pix.width) if pix.pixel(x, y)[0] > 150 and pix.pixel(x, y)[1] < 100]
    assert red
    assert min(x for x, y in red) >= 2 and max(x for x, y in red) <= pix.width - 2


def test_ann16_autofit_height(comments, tmp_path):
    """ANN-16: con l'adattamento automatico, aggiungendo cinque righe l'altezza di /Rect aumenta."""
    window, view, module = comments
    xref = module.ctrl.apply("t", engine.create_freetext, 0, (100, 400, 300, 430), "Una riga",
                             engine.Style(fontsize=12, width=1, color=(0, 0, 0)), "A")
    h1 = module.ctrl.info(0, xref).rect.height
    module.ctrl.apply("c", engine.set_contents, 0, xref, "Una riga\nDue\nTre\nQuattro\nCinque\nSei")
    h2 = module.ctrl.info(0, xref).rect.height
    assert h2 > h1 + 50
    doc = saved(window, view, tmp_path)
    assert "Sei" in doc[0].get_text()


def test_ann05_popup_left_open_is_saved(comments, tmp_path):
    """ANN-05: una popup lasciata aperta resta aperta dopo salvataggio e riapertura (/Open true)."""
    window, view, module = comments
    xref = module.ctrl.apply("n", engine.create_note, 0, (300, 300), "Aperta", engine.Style(), "A")
    assert module.ctrl.toggle_popup(0, xref)
    view.flush_rendering()
    assert module.ctrl.popups(view, 0) and module.ctrl.popups(view, 0)[0][3] == "Aperta"
    doc = saved(window, view, tmp_path)
    note, = annots(doc, 0, "Text")
    assert note.is_open
    assert key_of(doc, note.popup_xref, "Open")[1] == "true"
    qtbot_wait = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.processEvents
    qtbot_wait()
    with view.document.lock:
        xref = [a.xref for a in view.document.pdf[0].annots() if a.type[1] == "Text"][0]   # rinumerati
    assert module.ctrl.toggle_popup(0, xref), module.messages
    assert module.ctrl.popups(view, 0) == [], module.messages


def test_selection_reset_after_save(comments, tmp_path, qtbot):
    """Dopo il salvataggio (xref rinumerati) selezione e pannello restano coerenti."""
    window, view, module = comments
    a = _rect(module)
    module.ctrl.apply("n", engine.create_note, 0, (400, 300), "dopo", engine.Style(), "A")
    module.ctrl.select([(0, a)], view)
    view.document.save(str(tmp_path / "x.pdf"))
    qtbot.wait(20)
    assert module.ctrl.selection == [(0, a)]       # selezione valida dopo il salvataggio
    from .conftest import key as _key
    _key(view, Qt.Key.Key_Delete)
    assert module.ctrl.info(0, a) is None
    module.comments.reload()
    for it in module.comments.items():
        assert module.ctrl.info(*it.data(0, Qt.ItemDataRole.UserRole)) is not None


def test_ann43_page_layout_numbers(comments):
    """ANN-43: layout con la pagina: numeri dei marcatori identici a quelli delle voci."""
    window, view, module = comments
    _populate(module)
    with view.document.lock:
        data = engine.summary_pdf(view.document.pdf, "doc.pdf", "page")
    out = pymupdf.open(stream=data, filetype="pdf")
    assert out.page_count == 3        # una pagina per ogni pagina con commenti
    texts = [p.get_text() for p in out]
    assert "1. Evidenziazione" in texts[0] and "2. Nota" in texts[1] and "3. Nota" in texts[2]
    for i, t in enumerate(texts, start=1):
        assert str(i) in t.split()


def test_ann08_default_style_survives_restart(comments, tmp_path, monkeypatch):
    """ANN-08: evidenziatore predefinito verde, dopo il riavvio /C è il verde; preferiti esportati e importati."""
    window, view, module = comments
    st = module.ctrl.style_for(engine.HIGHLIGHT)
    st.color = (0, 1, 0)
    module.ctrl.set_style_for(engine.HIGHLIGHT, st)
    from foliant.modules.annotations.controller import Controller
    fresh = Controller(window.ctx)            # nuova sessione con le stesse preferenze
    assert fresh.style_for(engine.HIGHLIGHT).color == (0, 1, 0)
    module.ctrl.add_favorite(engine.HIGHLIGHT, "Evidenziatore verde 40%",
                             engine.Style(color=(0, 1, 0), opacity=0.4))
    out = tmp_path / "stili.json"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    from foliant.modules.annotations import ui_actions as ua
    assert ua.export_styles(module.ctrl) == str(out)
    window.ctx.settings().remove("annotations/favorites")
    assert module.ctrl.favorites() == []
    assert ua.import_styles(module.ctrl, str(out)) == 1
    assert module.ctrl.use_favorite(engine.HIGHLIGHT, "Evidenziatore verde 40%")
    assert module.ctrl.style_for(engine.HIGHLIGHT).opacity == 0.4


def test_ann08_keep_tool_three_rects(comments):
    """ANN-08: con «Mantieni strumento» tre rettangoli consecutivi."""
    window, view, module = comments
    module.ctrl.keep_tool = True
    module.activate(engine.RECT)
    for x in (100, 200, 300):
        drag(view, 0, (x, 400), (x + 60, 460))
    assert sum(1 for i in module.ctrl.annotations() if i.kind == engine.RECT) == 3


def test_styles_corrupted_profile_falls_back(comments):
    """ANN-08: profilo corrotto → valori di fabbrica senza errori."""
    window, view, module = comments
    window.ctx.settings().setValue("annotations/styles", "{non json")
    from foliant.modules.annotations.controller import FACTORY_STYLES, Controller
    assert Controller(window.ctx).style_for(engine.RECT).color == FACTORY_STYLES[engine.RECT].color


def test_ann27_sticker(comments, tmp_path):
    """ANN-27: adesivo apposto → /Stamp con /AP /N non vuoto e /Subj «Adesivo», tipo «Adesivo» nel pannello."""
    window, view, module = comments
    module.use_sticker("spunta")
    click(view, 0, 300, 400)
    infos = module.ctrl.annotations()
    assert infos[0].label == "Adesivo"
    module.ctrl.move(10, 0, [(0, infos[0].xref)])
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    assert st.info["subject"] == "Adesivo"
    ap = int(key_of(doc, st.xref, "AP/N")[1].split()[0])
    assert b" l " in doc.xref_stream(ap)          # grafica vettoriale
    pix = doc[0].get_pixmap(clip=st.rect)
    assert any(pix.pixel(x, y)[1] > 120 and pix.pixel(x, y)[0] < 100
               for x in range(pix.width) for y in range(pix.height))


def test_ann35_color_and_replies_filters(comments):
    window, view, module = comments
    a, b, c = _populate(module)
    module.ctrl.set_style([(1, b)], color=(0, 0, 1))
    module.ctrl.reply(2, c, "ok")
    panel = module.comments
    panel.reload()
    panel.set_filter("colors", {"#0000FF"})
    assert [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()] == [(1, b)]
    panel.set_filter("colors", set())
    panel.set_filter("replies", {"with"})
    keys = [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()]
    assert keys[0] == (2, c) and len(keys) == 2   # commento e la sua risposta


def test_ann38_history_menu(comments, monkeypatch):
    """ANN-38: «Accettato» poi «Completato»: stato visibile Completato, cronologia con entrambi."""
    from PySide6.QtWidgets import QMenu
    from foliant.modules.annotations.panels import build_annotation_menu
    window, view, module = comments
    a, b, c = _populate(module)
    module.ctrl.set_status(0, a, engine.STATE_ACCEPTED)
    module.ctrl.set_status(0, a, engine.STATE_COMPLETED)
    assert module.ctrl.info(0, a).status == engine.STATE_COMPLETED
    shown = []
    monkeypatch.setattr(dialogs, "show_text", lambda p, t, text: shown.append(text))
    menu = QMenu()
    build_annotation_menu(module.ctrl, menu, 0, a)
    [x for x in menu.actions() if x.text() == "Cronologia degli stati…"][0].trigger()
    assert shown and shown[0].index("Accettato") < shown[0].index("Completato")


def test_ann40_flatten_selection(comments):
    """ANN-40: appiattendo 2 annotazioni su 4 le altre 2 restano modificabili."""
    window, view, module = comments
    xs = [_rect(module, (100 + 100 * i, 400), (180 + 100 * i, 480)) for i in range(4)]
    module.ctrl.select([(0, xs[0]), (0, xs[1])], view)
    assert ui_actions.flatten(module.ctrl, selection=True) == 2
    left = {i.xref for i in module.ctrl.annotations()}
    assert left == {xs[2], xs[3]}
    with view.document.lock:   # i due appiattiti sono nel contenuto della pagina
        pix = view.document.pdf[0].get_pixmap(annots=False)

    def red(x0):
        return any(pix.pixel(x, y)[0] - pix.pixel(x, y)[1] > 60
                   for x in range(x0 - 3, x0 + 4) for y in range(436, 444))
    assert red(100) and red(200) and not red(300) and not red(400)


def test_ann29_pdfa1_blocks_attachments(comments, tmp_path, monkeypatch):
    """ANN-29: su un documento PDF/A-1b l'allegato è impedito con un messaggio sul profilo."""
    window, view, module = comments
    with view.document.change("pdfa") as pdf:
        pdf.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                             '<rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="1" '
                             'pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>')
    src = tmp_path / "a.txt"
    src.write_bytes(b"x")
    monkeypatch.setattr(dialogs, "ask_open_file", lambda *a, **k: str(src))
    module.activate(engine.ATTACHMENT)
    click(view, 0, 100, 100)
    assert module.ctrl.annotations() == []
    assert any("PDF/A-1" in m[2] for m in module.messages)


def test_ann03_align_and_distribute(comments):
    """ANN-03: allinea a sinistra e distribuisci in verticale."""
    window, view, module = comments
    xs = [_rect(module, (100 + 30 * i, 300 + 80 * i * i), (160 + 30 * i, 340 + 80 * i * i)) for i in range(3)]
    module.ctrl.select([(0, x) for x in xs], view)
    assert module.ctrl.align("left")
    lefts = {round(module.ctrl.info(0, x).rect.x0, 1) for x in xs}
    assert len(lefts) == 1
    assert module.ctrl.align("vdist")
    r = [module.ctrl.info(0, x).rect for x in xs]
    assert abs((r[1].y0 - r[0].y1) - (r[2].y0 - r[1].y1)) < 0.6


def test_ann07_paste_system_text_and_image(comments):
    """ANN-07: testo negli appunti → commento di testo; immagine PNG → /Stamp con immagine."""
    from PySide6.QtGui import QGuiApplication, QImage, QColor
    window, view, module = comments
    QGuiApplication.clipboard().setText("Testo incollato")
    module.activate("select")
    key(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    kinds = [i.kind for i in module.ctrl.annotations()]
    assert kinds == [engine.TYPEWRITER]
    img = QImage(40, 20, QImage.Format.Format_ARGB32)
    img.fill(QColor(200, 0, 0))
    QGuiApplication.clipboard().setImage(img)
    module.ctrl.paste_system(1)
    infos = module.ctrl.annotations()
    assert any(i.kind == engine.STAMP and i.page == 1 for i in infos)


def test_ann20_add_remove_vertex_and_cloud_intensity(comments, tmp_path):
    """ANN-20/21: vertici aggiunti/rimossi dal menu; nuvola con intensità 2 → /BE << /S /C /I 2 >>."""
    from PySide6.QtWidgets import QMenu
    from foliant.modules.annotations.panels import build_annotation_menu
    window, view, module = comments
    xref = module.ctrl.apply("p", engine.create_shape, 0, engine.POLYGON, [(100, 400), (300, 400), (200, 550)],
                             engine.Style(width=1), "A")
    menu = QMenu()
    build_annotation_menu(module.ctrl, menu, 0, xref, pymupdf.Point(200, 402))
    [a for a in menu.actions() if a.text() == "Aggiungi vertice qui"][0].trigger()
    assert len(module.ctrl.vertices(0, xref)) == 4
    menu = QMenu()
    build_annotation_menu(module.ctrl, menu, 0, xref, pymupdf.Point(200, 550))
    [a for a in menu.actions() if a.text() == "Rimuovi vertice"][0].trigger()
    assert len(module.ctrl.vertices(0, xref)) == 3
    r = _rect(module, (100, 600), (200, 700))
    module.ctrl.apply("c", lambda pdf: engine.set_cloud_intensity(pdf, 0, r, 2) or True)
    doc = saved(window, view, tmp_path)
    sq, = annots(doc, 0, "Square")
    assert key_of(doc, sq.xref, "BE/S")[1] == "/C" and key_of(doc, sq.xref, "BE/I")[1] == "2"


def test_ann30_scale_per_page(comments):
    """ANN-30: pagine con scale diverse: ogni misura usa la scala della propria pagina."""
    window, view, module = comments
    sc1 = engine.Scale.calibrate(100, 1, "m")
    sc1.all_pages = True
    module.ctrl.set_scale(sc1, 0)
    sc2 = engine.Scale.calibrate(100, 10, "m")
    sc2.all_pages = False
    module.ctrl.set_scale(sc2, 1)
    for page in (0, 1):
        module.ctrl.apply("m", engine.create_measure, page, engine.DISTANCE, [(100, 100), (200, 100)],
                          engine.Style(), module.ctrl.scale_for(page), "A")
    vals = {i.page: i.contents for i in module.ctrl.annotations()}
    assert vals == {0: "1,00 m", 1: "10,00 m"}


def test_encrypted_document_comment_and_save(window, encrypted_pdf, tmp_path, monkeypatch):
    """Documento cifrato: la nota si aggiunge e il file salvato resta protetto."""
    monkeypatch.setattr(window.ctx, "error", lambda *a: None)
    view = window.open_path(encrypted_pdf, "segreta")
    module = window.ctx.annotations
    window.set_mode("commenta")
    xref = module.ctrl.apply("n", engine.create_note, 0, (100, 100), "riservata", engine.Style(), "A")
    assert xref
    out = tmp_path / "cifrato.pdf"
    view.document.save(str(out))
    doc = pymupdf.open(str(out))
    assert doc.needs_pass and doc.authenticate("segreta")
    assert annots(doc, 0, "Text")[0].info["content"] == "riservata"
    view.document._saved_state = view.document._state


def test_no_comment_permission_blocks_tools(window, tmp_path, monkeypatch):
    """Permessi che vietano i commenti: strumenti disattivati con spiegazione."""
    path = tmp_path / "noannot.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Testo")
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
           permissions=pymupdf.PDF_PERM_PRINT)
    msgs = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: msgs.append(m))
    view = window.open_path(str(path))
    module = window.ctx.annotations
    module.activate(engine.NOTE)
    assert view.tool is not module.tools[engine.NOTE]
    assert msgs and "non consente" in msgs[0]


def test_ann34_many_annotations_first_screen_fast(window, tmp_path, qtbot):
    """ANN-34: con migliaia di commenti la prima schermata dell'elenco compare subito."""
    import time
    path = tmp_path / "molti.pdf"
    d = pymupdf.open()
    for i in range(120):
        p = d.new_page()
        for j in range(50):
            p.add_text_annot((50 + (j % 10) * 50, 50 + (j // 10) * 60), f"nota {i}-{j}")
    d.save(str(path))
    view = window.open_path(str(path))
    module = window.ctx.annotations
    panel = module.comments
    panel.set_view(view)
    t = time.perf_counter()
    panel.reload()
    assert time.perf_counter() - t < 2.0
    assert panel.items()
    qtbot.waitUntil(lambda: len(panel._infos) == 6000, timeout=60000)
    qtbot.waitUntil(lambda: panel.count.text() == "6000 commenti", timeout=60000)


def test_ann35_pages_and_marked_filters(comments, monkeypatch):
    """ANN-35: filtri per intervallo di pagine e per segno di spunta."""
    window, view, module = comments
    a, b, c = _populate(module)
    module.ctrl.set_marked(1, b, True)
    panel = module.comments
    panel.reload()
    monkeypatch.setattr(dialogs, "ask_text", lambda *x, **k: "2-3")
    panel._ask_pages()
    assert {tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()} == {(1, b), (2, c)}
    panel.set_filter("marked", {"yes"})
    assert [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()] == [(1, b)]
    from foliant.modules.annotations.panels import parse_pages
    assert parse_pages("1-3, 7; 99", 10) == {0, 1, 2, 6}


def test_ann25_custom_dynamic_template(comments, monkeypatch):
    """ANN-25: nuovo modello con segnaposto salvato in libreria e apposto con i valori correnti."""
    window, view, module = comments
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "Visto da {autore} {sconosciuto}")
    assert module.new_dynamic_template() == "Visto da {autore} {sconosciuto}"
    assert any("{sconosciuto}" in m[2] for m in module.messages)
    module._fill_dynamic()
    act = [a for a in module.dyn_menu.actions() if a.text() == "Visto da {autore} {sconosciuto}"][0]
    act.trigger()
    click(view, 0, 300, 400)
    assert module.ctrl.annotations()[0].contents == "Visto da Maria Rossi {sconosciuto}"


def test_ann26_library_export_import(comments, tmp_path, monkeypatch):
    """ANN-26: libreria esportata e importata in un'altra installazione con tutti i timbri; file corrotto → invariata."""
    window, view, module = comments
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 10), 1)
    module.ctrl.add_custom_stamp("logo", pix.tobytes("png"))
    module.ctrl.add_dynamic_template("Visto il {data}")
    out = tmp_path / "timbri.json"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    assert ui_actions.export_stamp_library(module.ctrl) == str(out)
    s = window.ctx.settings()
    s.remove("annotations/custom_stamps")
    s.remove("annotations/dynamic_templates")
    assert ui_actions.import_stamp_library(module.ctrl, str(out)) == 2
    assert [d["name"] for d in module.ctrl.custom_stamps()] == ["logo"]
    bad = tmp_path / "rotto.json"
    bad.write_text('{"version": 1, "stamps": [{"name": "x", "png": "AAAA"}]}')
    assert ui_actions.import_stamp_library(module.ctrl, str(bad)) == 0
    assert [d["name"] for d in module.ctrl.custom_stamps()] == ["logo"]


def test_ann03_rotate_shapes_and_textbox(comments, tmp_path):
    """ANN-03: rettangolo, ellisse e casella di testo ruotati di 30° restano ruotati dopo modifiche e salvataggio."""
    import math
    window, view, module = comments
    r = module.ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(100, 100), (300, 160)],
                          engine.Style(color=(1, 0, 0), width=2), "A")
    t = module.ctrl.apply("t", engine.create_freetext, 0, (300, 500, 500, 560), "Ruotata",
                          engine.Style(fontsize=14, width=1, color=(1, 0, 0)), "A")
    assert module.ctrl.rotate(30, [(0, r), (0, t)])
    module.ctrl.set_style([(0, r)], color=(0, 0, 1))          # il motore rigenera l'aspetto
    module.ctrl.apply("c", engine.set_contents, 0, t, "Ruotata due")
    module.ctrl.move(10, 0, [(0, r)])
    doc = saved(window, view, tmp_path)
    sq, = annots(doc, 0, "Square")
    ap = int(key_of(doc, sq.xref, "AP/N")[1].split()[0])
    m = [float(v) for v in key_of(doc, ap, "Matrix")[1].strip("[]").split()]
    assert abs(m[1] / m[0] - math.tan(math.radians(30))) < 0.01
    exp_h = 200 * math.sin(math.radians(30)) + 60 * math.cos(math.radians(30))
    assert abs(sq.rect.height - exp_h) < 3
    ft, = annots(doc, 0, "FreeText")
    ap2 = int(key_of(doc, ft.xref, "AP/N")[1].split()[0])
    assert key_of(doc, ap2, "Matrix")[1] != "[1 0 0 1 0 0]"
    assert "Ruotata due" in doc[0].get_text()


def test_ann07_paste_into_other_document(comments, tmp_path):
    """ANN-07: timbro personalizzato copiato dal documento A e incollato nel documento B con resa identica."""
    window, view_a, module = comments
    xref = module.ctrl.apply("s", engine.create_stamp, 0, (300, 300), engine.StampSpec("Venduto", name="Sold"),
                             engine.Style(), "A")
    module.ctrl.select([(0, xref)], view_a)
    module.ctrl.copy()
    other = pymupdf.open()
    other.new_page()
    other.new_page()
    other.save(str(tmp_path / "b.pdf"))
    view_b = window.open_path(str(tmp_path / "b.pdf"))
    (page, new), = module.ctrl.paste(1)
    info = module.ctrl.info(1, new, view=view_b)
    assert info.kind == engine.STAMP and info.name != module.ctrl.info(0, xref, view=view_a).name
    out = tmp_path / "b_salvato.pdf"
    view_b.document.save(str(out))
    doc = pymupdf.open(str(out))
    assert "VENDUTO" in doc[1].get_text()
    st, = annots(doc, 1, "Stamp")
    assert key_of(doc, st.xref, "P")[1] == f"{doc[1].xref} 0 R"


def test_ann26_stamp_from_pdf_page_is_vector(comments, tmp_path, monkeypatch):
    """ANN-26: timbro creato da una pagina PDF vettoriale: aspetto con tracciati, nessuna immagine."""
    window, view, module = comments
    src = pymupdf.open()
    sp = src.new_page(width=200, height=100)
    sp.draw_rect((10, 10, 190, 90), color=(0, 0, 1), width=4)
    sp.insert_text((30, 60), "LOGO", fontsize=30, color=(1, 0, 0))
    src.new_page()
    src.save(str(tmp_path / "logo.pdf"))
    monkeypatch.setattr(dialogs, "ask_open_file", lambda *a, **k: str(tmp_path / "logo.pdf"))
    monkeypatch.setattr(dialogs, "ask_text", lambda *a, **k: "1")
    module._page_stamp()
    click(view, 0, 300, 400)
    module.ctrl.move(10, 0)
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    ap = int(key_of(doc, st.xref, "AP/N")[1].split()[0])
    stream = doc.xref_stream(ap)
    assert b" re" in stream or b" l" in stream
    assert b"Do" not in stream                 # nessuna immagine raster
    assert "LOGO" in doc[0].get_text()


def test_ann26_snapshot_area(comments, tmp_path):
    """ANN-26: istantanea di un'area → timbro in libreria con /IT /StampSnapshot."""
    window, view, module = comments
    module.activate("snapshot")
    drag(view, 0, (70, 85), (200, 105))
    assert any(d["name"].startswith("Istantanea") for d in module.ctrl.custom_stamps())
    click(view, 0, 300, 600)
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    assert key_of(doc, st.xref, "IT")[1] == "/StampSnapshot"


def test_ann35_apply_filter_to_page_and_dates(comments, monkeypatch):
    """ANN-35: «Applica il filtro alla pagina» nasconde in pagina le escluse senza cambiare /F; filtro per data."""
    window, view, module = comments
    a = module.ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(100, 400), (200, 500)],
                          engine.Style(color=(1, 0, 0), width=3), "Anna")
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine, "pdf_now", lambda: "D:20200101100000+00'00'")
        b = module.ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(300, 400), (400, 500)],
                              engine.Style(color=(1, 0, 0), width=3), "Luca")
    panel = module.comments
    panel.reload()
    panel.set_filter("days", {"7"})
    assert [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()] == [(0, a)]
    flags_before = module.ctrl.info(0, b).flags
    panel.set_apply_to_page(True)
    img = view._render_page(0, 1.0)

    def red(x, y):
        c = img.pixelColor(int(x), int(y))
        return c.red() > 150 and c.green() < 100

    assert any(red(100 + d, 450) for d in range(-2, 3))
    assert not any(red(300 + d, 450) for d in range(-2, 3))
    assert module.ctrl.info(0, b).flags == flags_before
    panel.set_filter("days", set())
    img = view._render_page(0, 1.0)
    assert any(img.pixelColor(300 + d, 450).red() > 150 and img.pixelColor(300 + d, 450).green() < 100
               for d in range(-2, 3))


def test_ann03_minibar(comments, monkeypatch):
    """ANN-03: mini-barra sopra l'annotazione selezionata con colore, commento ed elimina."""
    window, view, module = comments
    xref = _rect(module)
    module.ctrl.select([(0, xref)], view)
    bar = module.minibar
    assert bar.isVisible()
    r = view.page_to_view(0, module.ctrl.info(0, xref).rect)
    assert bar.geometry().bottom() <= r.top() or bar.geometry().top() >= r.bottom()
    bar.color._pick((0, 0, 1))
    assert module.ctrl.info(0, xref).color == (0, 0, 1)
    bar._delete()
    assert module.ctrl.info(0, xref) is None and not bar.isVisible()
