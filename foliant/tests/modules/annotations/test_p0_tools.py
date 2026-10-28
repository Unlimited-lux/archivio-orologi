"""Criteri P0: selezione e modifica, proprietà, markup, forme, matita, gomma, elenco commenti
(ANN-03/04/10/12/18/19/22/23/34)."""

from __future__ import annotations

import math

import pymupdf
from PySide6.QtCore import Qt

from foliant.modules.annotations import engine

from .conftest import annots, click, double_click, drag, key, key_of, press, release, saved


def _rect(module, a=(100, 400), b=(200, 500), style=None):
    return module.ctrl.apply("r", engine.create_shape, 0, engine.RECT, [a, b], style or engine.Style(width=1), "A")


def _info(module, xref, page=0):
    return module.ctrl.info(page, xref)


# ------------------------------------------------------------------ ANN-03
def test_ann03_drag_moves_rect(comments, tmp_path):
    """ANN-03: trascinando un rettangolo di 50 pt a destra /Rect si sposta di 50 pt."""
    window, view, module = comments
    xref = _rect(module)
    before = _info(module, xref).rect
    module.activate("select")
    drag(view, 0, (150, 450), (200, 450))
    doc = saved(window, view, tmp_path)
    after = annots(doc, 0, "Square")[0].rect
    assert abs(after.x0 - (before.x0 + 50)) < 0.5 and abs(after.x1 - (before.x1 + 50)) < 0.5
    assert abs(after.y0 - before.y0) < 0.5


def test_ann03_click_selects_and_delete_key_removes_three(comments, tmp_path):
    """ANN-03: tre annotazioni selezionate (Maiusc+clic) ed eliminate con Canc."""
    window, view, module = comments
    xs = [_rect(module, (100 + i * 120, 400), (200 + i * 120, 480)) for i in range(3)]
    module.activate("select")
    click(view, 0, 150, 440)
    click(view, 0, 270, 440, Qt.KeyboardModifier.ShiftModifier)
    click(view, 0, 390, 440, Qt.KeyboardModifier.ShiftModifier)
    assert sorted(x for _p, x in module.ctrl.selection) == sorted(xs)
    key(view, Qt.Key.Key_Delete)
    doc = saved(window, view, tmp_path)
    assert annots(doc, 0, "Square") == []
    module.comments.reload()
    assert module.comments.items() == []


def test_ann03_rubber_band_selection(comments):
    """ANN-03: rettangolo di selezione in una zona senza testo."""
    window, view, module = comments
    a = _rect(module, (100, 500), (150, 550))
    b = _rect(module, (200, 500), (250, 550))
    module.activate("select")
    drag(view, 0, (90, 480), (300, 600))
    assert sorted(x for _p, x in module.ctrl.selection) == sorted([a, b])


def test_ann03_arrow_keys_move(comments):
    """ANN-03: frecce spostano di 1 pt, con Maiusc di 10 pt."""
    window, view, module = comments
    xref = _rect(module)
    before = _info(module, xref).rect
    module.activate("select")
    click(view, 0, 150, 450)
    key(view, Qt.Key.Key_Right)
    key(view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    after = _info(module, xref).rect
    assert abs(after.x0 - before.x0 - 1) < 0.3 and abs(after.y0 - before.y0 - 10) < 0.3


def test_ann03_resize_with_handle(comments):
    """ANN-03: la maniglia in basso a destra ridimensiona."""
    window, view, module = comments
    xref = _rect(module)
    r = _info(module, xref).rect
    module.activate("select")
    click(view, 0, 150, 450)
    drag(view, 0, (r.x1, r.y1), (r.x1 + 40, r.y1 + 20))
    r2 = _info(module, xref).rect
    assert abs(r2.width - r.width - 40) < 1 and abs(r2.height - r.height - 20) < 1


def test_ann03_locked_not_moved_or_deleted(comments):
    """ANN-03: un'annotazione bloccata non si sposta né si elimina e compare un messaggio."""
    window, view, module = comments
    xref = _rect(module)
    module.ctrl.set_flag(engine.FLAG_LOCKED, True, [(0, xref)])
    before = _info(module, xref).rect
    module.activate("select")
    drag(view, 0, (150, 450), (220, 450))
    assert _info(module, xref).rect == before
    click(view, 0, 150, 450)
    key(view, Qt.Key.Key_Delete)
    assert _info(module, xref) is not None
    assert any("bloccat" in m[2] for m in module.messages)


def test_ann03_text_markup_not_draggable(comments):
    """ANN-03: i markup ancorati al testo non si trascinano."""
    window, view, module = comments
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    (p, xref), = module.ctrl.selection
    before = _info(module, xref).rect
    module.activate("select")
    drag(view, 0, (100, 95), (100, 300))
    assert _info(module, xref).rect == before


def test_ann03_bring_to_front(comments, tmp_path):
    """ANN-03: portare in primo piano mette l'annotazione in fondo all'array /Annots."""
    window, view, module = comments
    a = _rect(module, (100, 400), (200, 500))
    b = _rect(module, (150, 450), (250, 550))
    module.ctrl.restack(True, [(0, a)])
    with view.document.lock:
        _h, _k, refs = engine._annots_array(view.document.pdf, view.document.pdf[0])
    assert refs.index(a) > refs.index(b)


# ------------------------------------------------------------------ ANN-04
def test_ann04_rect_properties(comments, tmp_path):
    """ANN-04: bordo #FF0000, riempimento #00FF00, opacità 50%, 3 pt, tratteggiato."""
    window, view, module = comments
    xref = _rect(module)
    module.ctrl.select([(0, xref)], view)
    module.ctrl.set_style(color=(1, 0, 0), fill=(0, 1, 0), opacity=0.5, width=3, dash="dashed")
    doc = saved(window, view, tmp_path)
    sq = annots(doc, 0, "Square")[0]
    assert key_of(doc, sq.xref, "C")[1] == "[1 0 0]"
    assert key_of(doc, sq.xref, "IC")[1] == "[0 1 0]"
    assert float(key_of(doc, sq.xref, "CA")[1]) == 0.5
    assert float(key_of(doc, sq.xref, "BS/W")[1]) == 3
    assert key_of(doc, sq.xref, "BS/S")[1] == "/D"


def test_ann04_properties_panel_applies(comments, tmp_path):
    """ANN-04: il pannello proprietà applica colore e spessore alla selezione."""
    window, view, module = comments
    xref = _rect(module)
    module.ctrl.select([(0, xref)], view)
    panel = module.properties
    panel.refresh()
    panel.color._pick((0, 0, 1))
    panel.width.setValue(4)
    info = _info(module, xref)
    assert info.color == (0.0, 0.0, 1.0) and info.width == 4


def test_ann04_line_ends(comments, tmp_path):
    """ANN-04: estremità iniziale nessuna e finale freccia chiusa → /LE [/None /ClosedArrow]."""
    window, view, module = comments
    xref = module.ctrl.apply("l", engine.create_shape, 0, engine.LINE, [(100, 100), (300, 100)], engine.Style(), "A")
    module.ctrl.select([(0, xref)], view)
    panel = module.properties
    panel.refresh()
    panel.end_end.setCurrentIndex(panel.end_end.findData("ClosedArrow"))
    doc = saved(window, view, tmp_path)
    line = annots(doc, 0, "Line")[0]
    assert key_of(doc, line.xref, "LE")[1].replace(" ", "") == "[/None/ClosedArrow]"


def test_ann04_one_undo_for_multi_selection(comments):
    """ANN-04: cambio colore su tre annotazioni di tipo diverso annullato con un solo Annulla."""
    window, view, module = comments
    a = _rect(module)
    b = module.ctrl.apply("e", engine.create_shape, 0, engine.ELLIPSE, [(300, 400), (400, 500)], engine.Style(), "A")
    c = module.ctrl.apply("n", engine.create_note, 0, (500, 300), "x", engine.Style(), "A")
    olds = [_info(module, x).color for x in (a, b, c)]
    module.ctrl.select([(0, a), (0, b), (0, c)], view)
    module.ctrl.set_style(color=(0, 0, 1))
    assert all(_info(module, x).color == (0, 0, 1) for x in (a, b, c))
    view.document.undo()
    assert [_info(module, x).color for x in (a, b, c)] == olds


def test_ann04_cmyk_color_kept_when_width_changes(comments, tmp_path):
    """ANN-04: /C CMYK letto da file resta invariato se si cambia solo lo spessore."""
    window, view, module = comments
    xref = _rect(module)
    with view.document.change("cmyk") as pdf:
        pdf.xref_set_key(xref, "C", "[0 1 1 0]")
    module.ctrl.set_style([(0, xref)], width=3)
    doc = saved(window, view, tmp_path)
    sq = annots(doc, 0, "Square")[0]
    assert key_of(doc, sq.xref, "C")[1] == "[0 1 1 0]"


# ------------------------------------------------------------------ ANN-10 / ANN-12
def test_ann10_highlight_line(comments, tmp_path):
    """ANN-10: «Lorem ipsum» evidenziato → 8 valori in /QuadPoints e /C [1 1 0]."""
    window, view, module = comments
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    doc = saved(window, view, tmp_path)
    hl = annots(doc, 0, "Highlight")[0]
    qp = key_of(doc, hl.xref, "QuadPoints")[1].strip("[]").split()
    assert len(qp) == 8
    assert key_of(doc, hl.xref, "C")[1] == "[1 1 0]"
    words = [w for w in doc[0].get_text("words") if w[4] in ("Lorem", "ipsum")]
    glyphs = pymupdf.Rect(words[0][:4]) | pymupdf.Rect(words[1][:4])
    q = hl.vertices
    box = pymupdf.Rect(min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q))
    assert abs(box.x0 - glyphs.x0) < 1 and abs(box.x1 - glyphs.x1) < 1


def test_ann10_paragraph_three_quads(comments, tmp_path):
    """ANN-10: un paragrafo di tre righe → tre quadrilateri (24 valori)."""
    window, view, module = comments
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 197), (200, 229))
    doc = saved(window, view, tmp_path)
    hl = annots(doc, 0, "Highlight")[0]
    assert len(key_of(doc, hl.xref, "QuadPoints")[1].strip("[]").split()) == 24


def test_ann10_selection_then_command(comments):
    """ANN-10: testo selezionato prima e poi «Evidenzia»."""
    window, view, module = comments
    module.activate("select")
    from foliant.api import selection_from_words
    words = [w for w in view.words(0) if w[4] in ("Lorem", "ipsum")]
    view.set_text_selection(selection_from_words(0, words))
    module.activate(engine.HIGHLIGHT)
    infos = module.ctrl.annotations()
    assert len(infos) == 1 and infos[0].kind == engine.HIGHLIGHT
    assert infos[0].marked_text == "Lorem ipsum"


def test_ann10_glyphs_stay_dark(comments):
    """ANN-10: i glifi sotto l'evidenziazione restano scuri (fusione moltiplicativa)."""
    window, view, module = comments
    words = [w for w in view.words(0) if w[4] == "Lorem"]
    r = pymupdf.Rect(words[0][:4])
    with view.document.lock:
        before = view.document.pdf[0].get_pixmap(clip=r, matrix=pymupdf.Matrix(4, 4))
        dark_before = min(sum(before.pixel(x, y)) for x in range(before.width) for y in range(before.height))
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    with view.document.lock:
        after = view.document.pdf[0].get_pixmap(clip=r, matrix=pymupdf.Matrix(4, 4))
        dark_after = min(sum(after.pixel(x, y)) for x in range(after.width) for y in range(after.height))
    assert dark_after <= dark_before + 0.1 * 765


def test_ann10_image_page_area_highlight(window, image_pdf, monkeypatch):
    """ANN-10: su una pagina immagine l'evidenziatore crea un'evidenziazione ad area con avviso OCR."""
    view = window.open_path(image_pdf)
    module = window.ctx.annotations
    msgs = []
    monkeypatch.setattr(window.ctx, "status", lambda m, t=5000: msgs.append(m))
    window.set_mode("commenta")
    view.set_zoom(1.0)
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (100, 100), (300, 200))
    infos = module.ctrl.annotations()
    assert len(infos) == 1 and infos[0].kind == engine.HIGHLIGHT
    assert any("OCR" in m for m in msgs)
    view.document._saved_state = view.document._state


def test_ann12_three_markups_same_quads(comments, tmp_path):
    """ANN-12: sottolineato, barrato e ondulato sulla stessa parola hanno /QuadPoints identici."""
    window, view, module = comments
    from foliant.api import selection_from_words
    words = [w for w in view.words(0) if w[4] == "Lorem"]
    for kind in (engine.UNDERLINE, engine.STRIKEOUT, engine.SQUIGGLY):
        view.set_text_selection(selection_from_words(0, words))
        module.tools[kind].apply_selection(view)
    doc = saved(window, view, tmp_path)
    qps = {a.type[1]: key_of(doc, a.xref, "QuadPoints")[1] for a in annots(doc, 0)}
    assert set(qps) == {"Underline", "StrikeOut", "Squiggly"}
    assert len(set(qps.values())) == 1


def _colored_rows(doc, rect, zoom=4):
    pix = doc[0].get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
    rows = []
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)[:3]
            if r > 150 and g < 100 and b < 100 or (b > 150 and r < 100):
                rows.append(y)
                break
    return rows, pix.height


def test_ann12_line_positions(comments, tmp_path):
    """ANN-12: barrato tra il 30% e il 70% dell'altezza; sottolineato nel 20% inferiore."""
    window, view, module = comments
    from foliant.api import selection_from_words
    words = [w for w in view.words(0) if w[4] == "Lorem"]
    r = pymupdf.Rect(words[0][:4])
    view.set_text_selection(selection_from_words(0, words))
    module.tools[engine.STRIKEOUT].apply_selection(view)
    with view.document.lock:
        rows, h = _colored_rows(view.document.pdf, r)
    mid = sum(rows) / len(rows) / h
    assert 0.3 <= mid <= 0.7, mid
    view.document.undo()
    view.set_text_selection(selection_from_words(0, words))
    module.tools[engine.UNDERLINE].apply_selection(view)
    with view.document.lock:
        rows, h = _colored_rows(view.document.pdf, r + (0, 0, 0, 2))
    assert sum(rows) / len(rows) / h >= 0.75


# ------------------------------------------------------------------ ANN-18 / ANN-19
def test_ann18_shift_square(comments):
    """ANN-18: con Maiusc il rettangolo è un quadrato."""
    window, view, module = comments
    module.activate(engine.RECT)
    drag(view, 0, (100, 400), (220, 460), Qt.KeyboardModifier.ShiftModifier)
    info = module.ctrl.annotations()[0]
    assert abs(info.rect.width - info.rect.height) < 0.5


def test_ann18_ellipse_properties(comments, tmp_path):
    """ANN-18: ellisse riempimento blu, bordo nero 1 pt, opacità 30%."""
    window, view, module = comments
    module.ctrl.set_style_for(engine.ELLIPSE, engine.Style(color=(0, 0, 0), fill=(0, 0, 1), width=1, opacity=0.3))
    module.activate(engine.ELLIPSE)
    drag(view, 0, (100, 400), (250, 500))
    doc = saved(window, view, tmp_path)
    c = annots(doc, 0, "Circle")[0]
    assert key_of(doc, c.xref, "IC")[1] == "[0 0 1]"
    assert key_of(doc, c.xref, "C")[1] == "[0 0 0]"
    assert float(key_of(doc, c.xref, "BS/W")[1]) == 1
    assert abs(float(key_of(doc, c.xref, "CA")[1]) - 0.3) < 0.01


def test_ann18_thick_stroke_inside_rect(comments, tmp_path):
    """ANN-18: un tratto di 10 pt resta dentro /Rect."""
    window, view, module = comments
    module.ctrl.set_style_for(engine.RECT, engine.Style(color=(1, 0, 0), width=10))
    module.activate(engine.RECT)
    drag(view, 0, (200, 400), (300, 500))
    doc = saved(window, view, tmp_path)
    sq = annots(doc, 0, "Square")[0]
    r = sq.rect
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
    for x, y in ((r.x0 - 1.5, (r.y0 + r.y1) / 2), (r.x1 + 1.5, (r.y0 + r.y1) / 2),
                 ((r.x0 + r.x1) / 2, r.y0 - 1.5), ((r.x0 + r.x1) / 2, r.y1 + 1.5)):
        px = pix.pixel(int(x * 2), int(y * 2))
        assert not (px[0] > 200 and px[1] < 100), (x, y, px)


def test_ann18_reverse_drag_normalized_and_click_nothing(comments, tmp_path):
    """ANN-18: trascinamento inverso normalizzato; clic senza trascinamento non crea nulla."""
    window, view, module = comments
    module.activate(engine.RECT)
    click(view, 0, 300, 300)
    assert module.ctrl.annotations() == []
    drag(view, 0, (300, 500), (200, 400))
    doc = saved(window, view, tmp_path)
    vals = [float(v) for v in key_of(doc, annots(doc, 0, "Square")[0].xref, "Rect")[1].strip("[]").split()]
    assert vals[0] < vals[2] and vals[1] < vals[3]


def test_ann19_arrow(comments, tmp_path):
    """ANN-19: freccia da (50,50) a (250,50) → /L, /LE [/None /OpenArrow], /IT /LineArrow, punta in /Rect."""
    window, view, module = comments
    module.activate(engine.ARROW)
    drag(view, 0, (50, 50), (250, 50))
    doc = saved(window, view, tmp_path)
    line = annots(doc, 0, "Line")[0]
    v = line.vertices
    assert abs(v[0][0] - 50) < 0.5 and abs(v[1][0] - 250) < 0.5 and abs(v[0][1] - 50) < 0.5
    assert key_of(doc, line.xref, "LE")[1].replace(" ", "") == "[/None/OpenArrow]"
    assert key_of(doc, line.xref, "IT")[1] == "/LineArrow"
    assert line.rect.x1 >= 250 and line.rect.y0 < 50 < line.rect.y1


def test_ann19_shift_snaps_45(comments):
    """ANN-19: con Maiusc una linea a 40° diventa a 45°."""
    window, view, module = comments
    module.activate(engine.LINE)
    a = (100, 400)
    b = (100 + 100 * math.cos(math.radians(40)), 400 + 100 * math.sin(math.radians(40)))
    drag(view, 0, a, b, Qt.KeyboardModifier.ShiftModifier)
    with view.document.lock:
        page = view.document.pdf[0]
        line = list(page.annots())[0]
        (x0, y0), (x1, y1) = line.vertices
    assert abs(math.degrees(math.atan2(y1 - y0, x1 - x0)) - 45) < 0.5


def test_ann19_caption(comments, tmp_path):
    """ANN-19: linea con didascalia «Ingresso» → /Cap true, /Contents «Ingresso»."""
    window, view, module = comments
    module.ctrl.apply("l", engine.create_shape, 0, engine.LINE, [(100, 300), (300, 300)], engine.Style(), "A",
                      "Ingresso")
    doc = saved(window, view, tmp_path)
    line = annots(doc, 0, "Line")[0]
    assert key_of(doc, line.xref, "Cap")[1] == "true"
    assert line.info["content"] == "Ingresso"


# ------------------------------------------------------------------ ANN-22 / ANN-23
def test_ann22_three_strokes_one_ink(comments, tmp_path):
    """ANN-22: tre tratti rapidi con matita rossa 2 pt → un solo /Ink con tre tratti."""
    window, view, module = comments
    module.ctrl.set_style_for(engine.INK, engine.Style(color=(1, 0, 0), width=2))
    module.activate(engine.INK)
    for y in (400, 420, 440):
        drag(view, 0, (100, y), (200, y + 5))
    doc = saved(window, view, tmp_path)
    inks = annots(doc, 0, "Ink")
    assert len(inks) == 1
    assert len(inks[0].vertices) == 3
    assert key_of(doc, inks[0].xref, "C")[1] == "[1 0 0]"
    assert float(key_of(doc, inks[0].xref, "BS/W")[1]) == 2


def test_ann22_simplification():
    """ANN-22: 10.000 campioni → al più 2.000 punti con scarto ≤ 0,5 pt."""
    pts = [(50 + i * 0.05, 400 + 20 * math.sin(i / 300) + 0.05 * ((i * 7919) % 5)) for i in range(10_000)]
    doc = pymupdf.open()
    doc.new_page()
    xref = engine.create_ink(doc, 0, [pts], engine.Style(width=1), "A")
    page = doc[0]
    stroke = [pymupdf.Point(p) for p in page.load_annot(xref).vertices[0]]
    assert len(stroke) <= 2000
    for p in pts[::50]:
        d = min(engine._dist_to_segment(pymupdf.Point(p), stroke[i], stroke[i + 1]) for i in range(len(stroke) - 1))
        assert d <= 0.51


def test_ann22_marker_transparent(comments, tmp_path):
    """ANN-22: il pennarello ha /CA < 1 e fusione moltiplicativa."""
    window, view, module = comments
    module.activate("marker")
    drag(view, 0, (70, 96), (150, 96))
    doc = saved(window, view, tmp_path)
    ink = annots(doc, 0, "Ink")[0]
    assert float(key_of(doc, ink.xref, "CA")[1]) < 1
    assert ink.blendmode == "Multiply"


def test_ann23_partial_eraser(comments):
    """ANN-23: gomma parziale al centro di un tratto orizzontale → 2 tratti, nessun punto nel raggio."""
    window, view, module = comments
    xref = module.ctrl.apply("i", engine.create_ink, 0, [[(100, 400), (300, 400)]], engine.Style(width=2), "A")
    module.activate("eraser")
    eraser = module.tools["eraser"]
    drag(view, 0, (200, 395), (200, 405))
    with view.document.lock:
        page = view.document.pdf[0]
        strokes = page.load_annot(xref).vertices
    assert len(strokes) == 2
    for s in strokes:
        for p in s:
            assert abs(pymupdf.Point(p) - pymupdf.Point(200, 400)) > eraser.radius - 0.01


def test_ann23_whole_stroke_and_others_untouched(comments):
    """ANN-23: gomma a tratto intero elimina l'/Ink; rettangolo e nota restano."""
    window, view, module = comments
    module.ctrl.apply("i", engine.create_ink, 0, [[(100, 400), (300, 400)]], engine.Style(width=2), "A")
    r = _rect(module, (180, 380), (220, 420))
    n = module.ctrl.apply("n", engine.create_note, 0, (250, 400), "x", engine.Style(), "A")
    module.activate("eraser")
    module.tools["eraser"].whole = True
    drag(view, 0, (150, 400), (260, 400))
    kinds = {i.kind for i in module.ctrl.annotations()}
    assert engine.INK not in kinds
    assert _info(module, r) is not None and _info(module, n) is not None


def test_ann23_undo_restores_inklist(comments):
    """ANN-23: Annulla dopo la cancellazione ripristina /InkList."""
    window, view, module = comments
    xref = module.ctrl.apply("i", engine.create_ink, 0, [[(100, 400), (200, 410), (300, 400)]],
                             engine.Style(width=2), "A")
    with view.document.lock:
        before = view.document.pdf.xref_get_key(xref, "InkList")
    module.activate("eraser")
    drag(view, 0, (200, 400), (200, 415))
    view.document.undo()
    with view.document.lock:
        assert view.document.pdf.xref_get_key(xref, "InkList") == before


# ------------------------------------------------------------------ ANN-34
def test_ann34_panel_groups(comments):
    """ANN-34: 3 evidenziazioni a pagina 1 e 2 note a pagina 4 → due gruppi con autore e data."""
    window, view, module = comments
    ctrl = module.ctrl
    for y in (95, 197, 213):
        ctrl.apply("h", engine.create_markup, 0, engine.HIGHLIGHT, [pymupdf.Rect(72, y - 10, 150, y + 3)],
                   engine.Style(), "Anna")
    for y in (300, 400):
        ctrl.apply("n", engine.create_note, 3, (300, y), "nota", engine.Style(), "Luca")
    panel = module.comments
    panel.reload()
    tree = panel.tree
    groups = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    assert [g.text(0) for g in groups] == ["Pagina 1 (3)", "Pagina 4 (2)"]
    assert all("Anna" in groups[0].child(i).text(0) for i in range(3))
    assert all("/" in groups[1].child(i).text(0) for i in range(2))   # data presente


def test_ann34_click_item_selects_annotation(comments):
    """ANN-34: clic su una voce → pagina mostrata e annotazione selezionata; e viceversa."""
    window, view, module = comments
    ctrl = module.ctrl
    a = ctrl.apply("n", engine.create_note, 3, (300, 600), "nota", engine.Style(), "Luca")
    b = ctrl.apply("n", engine.create_note, 0, (300, 300), "altra", engine.Style(), "Luca")
    panel = module.comments
    panel.reload()
    item = panel.item_for(3, a)
    item.setSelected(True)
    assert ctrl.selection == [(3, a)]
    assert view.current_page == 3
    ctrl.select([(0, b)], view)
    assert panel.item_for(0, b).isSelected()
    assert not panel.item_for(3, a).isSelected()


def test_ann34_links_and_widgets_not_listed(comments):
    """ANN-34: collegamenti e campi modulo non sono elencati."""
    window, view, module = comments
    with view.document.change("prepara") as pdf:
        page = pdf[0]
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 50, 30), "uri": "https://x.y"})
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = "campo"
        w.rect = pymupdf.Rect(100, 600, 200, 620)
        page.add_widget(w)
    module.ctrl.apply("n", engine.create_note, 0, (300, 300), "nota", engine.Style(), "Luca")
    module.comments.reload()
    assert len(module.comments.items()) == 1


def test_ann34_double_click_edits_and_context_menu(comments, monkeypatch):
    """ANN-34/05: doppio clic sull'annotazione apre il commento; menu contestuale con le azioni."""
    from foliant.modules.annotations import dialogs
    from PySide6.QtWidgets import QMenu
    window, view, module = comments
    xref = _rect(module)
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: ("Rivedere", None))
    module.activate("select")
    double_click(view, 0, 150, 450)
    assert _info(module, xref).contents == "Rivedere"
    menu = QMenu()
    from foliant.modules.annotations.panels import build_annotation_menu
    build_annotation_menu(module.ctrl, menu, 0, xref)
    texts = [a.text() for a in menu.actions()]
    assert "Rispondi…" in texts and "Elimina" in texts and "Imposta stato" in texts
    assert press and release   # helper importati


def test_ann10_multipage_selection(comments, tmp_path):
    """ANN-10/12: selezione da fine pagina 2 a inizio pagina 3 → un /Highlight per pagina, un solo Annulla."""
    from foliant.api import selection_from_words
    window, view, module = comments
    w1 = [w for w in view.words(1) if w[4] in ("termine", "di", "consegna")]
    w2 = [w for w in view.words(2) if w[4] in ("Lorem", "ipsum")]
    view.set_text_selections([selection_from_words(1, w1), selection_from_words(2, w2)])
    module.tools[engine.HIGHLIGHT].apply_selection(view)
    infos = module.ctrl.annotations()
    assert sorted(i.page for i in infos) == [1, 2]
    texts = " ".join(i.marked_text for i in sorted(infos, key=lambda i: i.page))
    assert texts == "termine di consegna Lorem ipsum"
    view.document.undo()
    assert module.ctrl.annotations() == []
