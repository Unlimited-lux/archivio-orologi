"""TXT-08 (disposizione) e TXT-09 (unione e divisione dei blocchi)."""

from __future__ import annotations

import math

import pymupdf
import pytest
from PySide6.QtCore import Qt

from foliant.api import Document
from foliant.modules.text_edit import analysis, engine
from foliant.modules.text_edit.model import Run

from .conftest import LOREM, new_doc_with_font, save, write_lines
from .test_ui import click, open_in_edit_mode, quiet  # noqa: F401  (fixture)


def three_blocks_pdf(tmp_path) -> str:
    doc, page = new_doc_with_font()
    page.insert_text((80, 100), "Primo blocco", fontname="F1", fontsize=12)
    page.insert_text((150, 200), "Secondo blocco più lungo", fontname="F1", fontsize=12)
    page.insert_text((60, 300), "Terzo", fontname="F1", fontsize=12)
    return save(doc, tmp_path / "tre.pdf")


def blocks_of(d, page=0):
    with d.lock:
        return analysis.analyze_page(d.pdf[page], page)


def test_align_left(tmp_path):
    d = Document.open(three_blocks_pdf(tmp_path))
    blocks = blocks_of(d)
    with d.change("Allinea") as pdf:
        engine.align_blocks(pdf, 0, blocks, "left")
    lefts = [b.bbox.x0 for b in blocks_of(d)]
    assert max(lefts) - min(lefts) <= 0.1 and abs(min(lefts) - 60) <= 0.1
    assert "Secondo blocco più lungo" in d.page_text(0)


def test_align_to_page_center_and_distribute(tmp_path):
    d = Document.open(three_blocks_pdf(tmp_path))
    blocks = blocks_of(d)
    with d.change("Centra") as pdf:
        engine.align_blocks(pdf, 0, blocks, "hcenter", reference=pymupdf.Rect(0, 0, 595, 842))
    for b in blocks_of(d):
        assert abs((b.bbox.x0 + b.bbox.x1) / 2 - 297.5) <= 0.2
    blocks = blocks_of(d)
    with d.change("Distribuisci") as pdf:
        engine.distribute_blocks(pdf, 0, blocks, "v")
    bs = sorted(blocks_of(d), key=lambda b: b.bbox.y0)
    g1 = bs[1].bbox.y0 - bs[0].bbox.y1
    g2 = bs[2].bbox.y0 - bs[1].bbox.y1
    assert abs(g1 - g2) <= 0.2


def test_rotate_thirty_degrees(tmp_path):
    d = Document.open(three_blocks_pdf(tmp_path))
    block = blocks_of(d)[0]
    with d.change("Ruota") as pdf:
        engine.rotate_block(pdf, 0, block, 30)
    with d.lock:
        line = [ln for b in d.pdf[0].get_text("dict")["blocks"] for ln in b["lines"]
                if "Primo" in "".join(s["text"] for s in ln["spans"])][0]
    angle = -math.degrees(math.atan2(line["dir"][1], line["dir"][0]))
    assert abs(angle - 30) <= 0.5
    assert "Primo blocco" in d.page_text(0, sort=False)


def test_bring_to_front_writes_after(tmp_path):
    doc, page = new_doc_with_font()
    page.insert_text((72, 100), "Sotto", fontname="F1", fontsize=30)
    page.insert_text((75, 102), "Sopra", fontname="F1", fontsize=30)
    d = Document.open(save(doc, tmp_path / "pila.pdf"))
    with d.lock:
        blocks = analysis.analyze_page(d.pdf[0], 0, "line")
    under = [b for b in blocks if "Sotto" in b.text][0]
    with d.change("Primo piano") as pdf:
        engine.restack_block(pdf, 0, under, front=True)
    with d.lock:
        content = b"".join(d.pdf.xref_stream(x) or b"" for x in d.pdf[0].get_contents())
        words = [w[4] for w in d.pdf[0].get_text("words", sort=False)]
    assert words.index("Sotto") > words.index("Sopra")
    assert content


def test_merge_then_reflow(tmp_path):
    doc, page = new_doc_with_font()
    page.insert_text((72, 100), "Prima riga separata", fontname="F1", fontsize=12)
    page.insert_text((72, 140), "seconda riga lontana", fontname="F1", fontsize=12)
    d = Document.open(save(doc, tmp_path / "unisci.pdf"))
    blocks = blocks_of(d)
    assert len(blocks) == 2
    merged = analysis.merge_blocks(blocks, analysis.SEPARATOR_SPACE)
    assert merged.text == "Prima riga separata seconda riga lontana"
    new = [[Run("Prima riga separata con testo in più " + merged.paragraphs[0][0].text.split("separata ")[1],
                merged.style)]]
    with d.change("Modifica unito") as pdf:
        engine.replace_block(pdf, 0, merged, new)
    text = d.page_text(0)
    assert "in più" in text and "lontana" in text
    lines = analysis.analyze_page(d.pdf[0], 0, "line")
    assert len(lines) >= 2       # il testo eccedente rifluisce sulla seconda riga
    for ln in lines:
        assert ln.bbox.x1 <= merged.x1 + 0.6


def test_split_does_not_change_page(tmp_path):
    doc, page = new_doc_with_font()
    write_lines(page, LOREM)
    path = save(doc, tmp_path / "dividi.pdf")
    blocks = analysis.analyze_page(pymupdf.open(path)[0], 0)
    parts = analysis.split_block(blocks[0], 2)
    assert parts is not None and len(parts[0].lines) == 2 and len(parts[1].lines) == 2
    assert analysis.split_block(blocks[0], 0) is None


def test_merge_split_redetect_in_ui(window, tmp_path, quiet, qtbot):  # noqa: F811
    doc, page = new_doc_with_font()
    page.insert_text((72, 100), "Blocco uno", fontname="F1", fontsize=12)
    page.insert_text((72, 160), "Blocco due", fontname="F1", fontsize=12)
    write_lines(page, LOREM, y=300)
    path = save(doc, tmp_path / "ui.pdf")
    view = open_in_edit_mode(window, path)
    ctrl = window.ctx.text_edit
    blocks = ctrl.blocks(view, 0)
    assert len(blocks) == 3
    tool = view.tool
    for b in blocks[:2]:
        c = b.bbox
        click(qtbot, view, 0, (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2, Qt.KeyboardModifier.ShiftModifier)
    assert len(ctrl.selected_blocks(view)) == 2
    merged = ctrl.merge_selected(view)
    assert merged is not None and merged.text == "Blocco uno Blocco due"
    assert len(ctrl.blocks(view, 0)) == 2
    assert not view.document.is_modified
    lorem = [b for b in ctrl.blocks(view, 0) if "Lorem" in b.text][0]
    assert ctrl.split_block_at(view, 0, lorem, 2)
    assert len(ctrl.blocks(view, 0)) == 3
    assert not view.document.is_modified
    ctrl.redetect(view, 0)
    assert len(ctrl.blocks(view, 0)) == 3   # di nuovo automatico: due blocchi separati + Lorem
    assert [b for b in ctrl.blocks(view, 0) if "Lorem" in b.text][0].lines.__len__() == 4


def test_multi_select_band_align_and_delete(window, tmp_path, quiet, qtbot):  # noqa: F811
    path = three_blocks_pdf(tmp_path)
    view = open_in_edit_mode(window, path)
    ctrl = window.ctx.text_edit
    vp = view.viewport()
    from .test_ui import to_view
    qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, 30, 60))
    qtbot.mouseMove(vp, to_view(view, 0, 400, 250))
    qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=to_view(view, 0, 400, 250))
    assert len(ctrl.selected_blocks(view)) == 2
    ctrl.align_selected(view, "left")
    lefts = [b.bbox.x0 for b in ctrl.blocks(view, 0) if "Terzo" not in b.text]
    assert max(lefts) - min(lefts) <= 0.1
    assert view.document.can_undo
    assert len(ctrl.selected_blocks(view)) == 2
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    text = view.document.page_text(0)
    assert "Primo" not in text and "Secondo" not in text and "Terzo" in text
    view.document.undo()
    assert "Primo" in view.document.page_text(0)


def test_rotate_from_menu(window, tmp_path, quiet, qtbot, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QInputDialog
    from foliant.modules.text_edit import ask_rotation
    view = open_in_edit_mode(window, three_blocks_pdf(tmp_path))
    ctrl = window.ctx.text_edit
    block = ctrl.blocks(view, 0)[0]
    view.tool.select(view, 0, block)
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *a, **k: (45.0, True))
    ask_rotation(window.ctx, ctrl, view)
    assert view.document.is_modified
    rotated = [b for b in ctrl.blocks(view, 0) if "Primo" in b.text][0]
    assert rotated.rotated
