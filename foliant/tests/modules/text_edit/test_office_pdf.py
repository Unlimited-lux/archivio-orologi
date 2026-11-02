"""PDF esportati da fogli di calcolo (foglio presenze da LibreOffice Calc, come da Excel): il font
incorporato è un sottoinsieme senza mappa Unicode e il testo sta centrato nelle celle della tabella.

foglio_presenze.pdf: generato da un .xlsx con LibreOffice («--convert-to pdf»), font Carlito.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtGui import QFontDatabase, QRawFont

from foliant.modules.text_edit.analysis import analyze_page
from foliant.modules.text_edit.engine import replace_block
from foliant.modules.text_edit.fonts import KIND_ORIGINAL, FontResolver
from foliant.modules.text_edit.model import ALIGN_CENTER, Run
from foliant.modules.text_edit.ui_editor import REGISTRY, unicode_cmap

from .test_ui import block_with, ctrl_of, open_in_edit_mode, quiet  # noqa: F401 - fixture

PDF = Path(__file__).with_name("foglio_presenze.pdf")
NOTE = "PERMESSO RETRIBUITO O FERIE SE NON SONO MATURATI"


def _block(doc, text):
    return next(b for b in analyze_page(doc[0], 0) if text in "".join(ln.text for ln in b.lines))


def _line(doc, text):
    """(testo, riquadro) della riga che contiene `text`: senza Carlito nel sistema (CI su Windows) le
    cifre usano un font sostitutivo, quindi la riga è fatta di più pezzi."""
    for b in doc[0].get_text("dict")["blocks"]:
        for line in b.get("lines", ()):
            joined = "".join(s["text"] for s in line["spans"])
            if text in joined:
                return joined, pymupdf.Rect(line["bbox"]), line["spans"][0]["origin"][1]
    raise AssertionError(text)


def test_cells_keep_their_alignment():
    doc = pymupdf.open(str(PDF))
    for text in (NOTE, "Certificato: 458908714", "22", "PR", "NOTE"):
        assert _block(doc, text).align == ALIGN_CENTER, text


def test_edited_note_stays_centred_in_its_cell(tmp_path):
    doc = pymupdf.open(str(PDF))
    _t, before, baseline = _line(doc, NOTE)
    block = _block(doc, NOTE)
    style = block.paragraphs[0][0].style
    replace_block(doc, 0, block, [[Run("PERMESSO 123456ciao", style)]])
    out = tmp_path / "out.pdf"
    doc.save(str(out))
    _t, box, new_baseline = _line(pymupdf.open(str(out)), "PERMESSO 123456ciao")
    assert abs((box.x0 + box.x1) / 2 - (before.x0 + before.x1) / 2) < 1.0  # stesso centro della cella
    assert abs(new_baseline - baseline) < 0.5                              # stessa linea di base


def test_editor_shows_text_with_a_unicode_font(qapp):
    """Il sottoinsieme incorporato ha solo la mappa Mac Roman con i numeri dei glifi: nel riquadro di
    modifica lo spazio diventava «7» e le cifre altre lettere. Si mostra il font equivalente di sistema."""
    doc = pymupdf.open(str(PDF))
    style = _block(doc, NOTE).paragraphs[0][0].style
    resolver = FontResolver(doc)
    original = resolver.primary(style, NOTE)
    assert original.kind == KIND_ORIGINAL and not unicode_cmap(original.data())
    family = REGISTRY.family_for(resolver, style)
    raw = QRawFont.fromFont(_font(family))
    text = "PERMESSO 123456ciao"
    assert raw.isValid() and all(raw.supportsCharacter(c) for c in text)
    assert family in QFontDatabase.families()


def _font(family):
    from PySide6.QtGui import QFont
    font = QFont(family)
    font.setPointSizeF(12)
    return font


def test_typing_in_the_editor_writes_the_typed_text(window, quiet, qtbot, tmp_path):  # noqa: F811
    path = tmp_path / "presenze.pdf"
    path.write_bytes(PDF.read_bytes())
    view = open_in_edit_mode(window, str(path))
    ctrl = ctrl_of(window)
    block = block_with(ctrl, view, "PERMESSO")
    ed = ctrl.open_editor(view, 0, block)
    assert ed.align == ALIGN_CENTER
    ed.selectAll()
    qtbot.keyClicks(ed, "PERMESSO 123456ciao")
    assert ed.toPlainText() == "PERMESSO 123456ciao"
    ctrl.commit_editor(view)
    out = tmp_path / "out.pdf"
    view.document.save(out)
    assert "PERMESSO 123456ciao" in pymupdf.open(str(out))[0].get_text()


if __name__ == "__main__":
    pytest.main([__file__])
