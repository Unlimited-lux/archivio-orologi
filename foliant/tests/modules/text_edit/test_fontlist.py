"""TXT-11: font del documento, sostituzione globale, incorporamento, corrispondenze; traduzioni."""

from __future__ import annotations

import ast
from pathlib import Path

import pymupdf

from foliant.api import Document
from foliant.modules.text_edit import analysis, engine, fontlist, fonts
from foliant.modules.text_edit.model import Run, Style

from .conftest import SANS, SERIF, save, span_with


def test_states_and_pages(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="FA", fontfile=SERIF)
    page.insert_text((72, 100), "Completo", fontname="FA", fontsize=12)
    page.insert_text((72, 160), "Standard", fontname="helv", fontsize=12)
    page2 = doc.new_page()
    page2.insert_font(fontname="FB", fontfile=SANS)
    page2.insert_text((72, 100), "Sottoinsieme", fontname="FB", fontsize=12)
    page2.insert_font(fontname="FA", fontfile=SERIF)
    page2.insert_text((72, 160), "Ancora completo", fontname="FA", fontsize=12)
    # sottoinsieme solo per FB: lo creiamo in un documento separato e lo copiamo
    path = save(doc, tmp_path / "f.pdf")
    d = pymupdf.open(path)
    d.subset_fonts()
    entries = {e.name: e for e in fontlist.document_fonts(d)}
    helv = entries["Helvetica"]
    assert helv.state == fontlist.STATE_NOT_EMBEDDED and helv.pages == [1]
    serif = [e for e in entries.values() if "Serif" in e.name][0]
    assert serif.pages == [1, 2]
    assert serif.state in (fontlist.STATE_FULL, fontlist.STATE_SUBSET)
    sans = [e for e in entries.values() if "Sans" in e.name][0]
    assert sans.state == fontlist.STATE_SUBSET and sans.pages == [2]
    # un font incorporato completo
    full = pymupdf.open(path)
    entries = {e.name: e for e in fontlist.document_fonts(full)}
    assert [e for e in entries.values() if "Serif" in e.name][0].state == fontlist.STATE_FULL


def test_replace_font_everywhere(tmp_path):
    doc = pymupdf.open()
    for i in range(10):
        page = doc.new_page()
        page.insert_font(fontname="FA", fontfile=SERIF)
        page.insert_text((72, 100), f"Pagina {i + 1} con il font da sostituire", fontname="FA", fontsize=12)
    path = save(doc, tmp_path / "dieci.pdf")
    d = Document.open(path)
    with d.lock:
        before = [d.pdf[i].get_text() for i in range(10)]
    with d.change("Sostituisci font") as pdf:
        summary = fontlist.replace_font(pdf, "Liberation Serif Regular", "DejaVu Sans")
    assert summary.blocks == 10
    out = tmp_path / "out.pdf"
    d.save(out)
    re = pymupdf.open(str(out))
    for i in range(10):
        assert re[i].get_text() == before[i]
        for s in re[i].get_text("dict")["blocks"][0]["lines"][0]["spans"]:
            assert "Liberation" not in s["font"]


def test_embed_requires_same_system_font(helvetica_pdf):
    d = Document.open(helvetica_pdf)
    try:
        with d.change("Incorpora") as pdf:
            fontlist.embed_font(pdf, "Font Inesistente XYZ")
    except engine.TextEditError as e:
        assert "Sostituisci" in str(e)
    assert not d.is_modified


def _not_embedded_pdf(tmp_path) -> str:
    """Testo in «LiberationSans» non incorporato (installato nel sistema)."""
    doc = pymupdf.open()
    page = doc.new_page()
    font = doc.get_new_xref()
    doc.update_object(font, "<< /Type /Font /Subtype /TrueType /BaseFont /LiberationSans "
                            "/Encoding /WinAnsiEncoding >>")
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /F9 {font} 0 R >> >>")
    cont = doc.get_new_xref()
    doc.update_object(cont, "<< >>")
    doc.update_stream(cont, b"BT /F9 14 Tf 72 742 Td (Testo non incorporato) Tj ET")
    doc.xref_set_key(page.xref, "Contents", f"{cont} 0 R")
    return save(doc, tmp_path / "nonincorporato.pdf")


def test_embed_not_embedded_font(tmp_path):
    path = _not_embedded_pdf(tmp_path)
    d = Document.open(path)
    with d.lock:
        entries = {e.name: e for e in fontlist.document_fonts(d.pdf)}
    assert entries["LiberationSans"].state == fontlist.STATE_NOT_EMBEDDED
    with d.change("Incorpora") as pdf:
        fontlist.embed_font(pdf, "LiberationSans")
    with d.lock:
        assert "Testo non incorporato" in d.pdf[0].get_text()
        states = {e.name: e.state for e in fontlist.document_fonts(d.pdf)}
    assert any("Liberation" in n and st != fontlist.STATE_NOT_EMBEDDED for n, st in states.items())


def test_user_mapping_used_for_missing_font(tmp_path):
    path = tmp_path / "map.json"
    fontlist.save_mappings({"Font Introvabile": "DejaVu Serif"}, path)
    try:
        assert fontlist.load_mappings(path) == {"Font Introvabile": "DejaVu Serif"}
        d = pymupdf.open()
        d.new_page()
        choice = fonts.FontResolver(d).primary(Style(font="Font Introvabile", size=12))
        assert "DejaVu Serif" in choice.name
        # senza corrispondenza: sostituto metrico/predefinito
        fonts.set_user_mappings({})
        choice = fonts.FontResolver(d).primary(Style(font="Font Introvabile", size=12))
        assert "DejaVu Serif" not in choice.name
    finally:
        fonts.set_user_mappings({})


def test_mapping_without_subset_prefix(tmp_path):
    """Riservatezza: le corrispondenze salvano solo il nome di famiglia, senza il prefisso del
    sottoinsieme preso dal documento; le voci delle versioni precedenti si ripuliscono all'avvio."""
    import json
    path = tmp_path / "map.json"
    try:
        fontlist.save_mappings({"ABCDEF+Calibri": "DejaVu Sans"}, path)
        assert json.loads(path.read_text(encoding="utf-8")) == {"Calibri": "DejaVu Sans"}
        path.write_text(json.dumps({"QWERTY+Cambria": "DejaVu Serif", "Arial": "Liberation Sans"}), "utf-8")
        assert fontlist.load_mappings(path) == {"Cambria": "DejaVu Serif", "Arial": "Liberation Sans"}
        assert fontlist.forget_subset_prefixes(path)
        assert "QWERTY" not in path.read_text(encoding="utf-8")
        assert json.loads(path.read_text(encoding="utf-8")) == {"Cambria": "DejaVu Serif", "Arial": "Liberation Sans"}
        assert not fontlist.forget_subset_prefixes(path)        # già pulito: nessuna riscrittura
        assert not fontlist.forget_subset_prefixes(tmp_path / "assente.json")
    finally:
        fonts.set_user_mappings({})


def test_fonts_dialog(window, paragraphs_pdf, monkeypatch, qtbot):
    from foliant.modules.text_edit import open_fonts_dialog
    from foliant.modules.text_edit.fontlist_ui import FontsDialog
    view = window.open_path(paragraphs_pdf)
    monkeypatch.setattr(window, "close_view", lambda v, *a, orig=window.close_view: (
        setattr(v.document, "_saved_state", v.document._state), orig(v))[1])
    dlg = open_fonts_dialog(window.ctx)
    assert isinstance(dlg, FontsDialog)
    assert dlg.table.rowCount() >= 1
    monkeypatch.setattr(dlg, "ask_family", lambda title: "DejaVu Sans")
    dlg.table.selectRow(0)
    dlg.replace_selected()
    assert view.document.is_modified
    assert "Blocchi ricomposti" in dlg.summary.text()
    with view.document.lock:
        s = span_with(view.document.pdf[0], "contratto")
    assert "DejaVu" in s["font"]
    dlg.close()


def test_all_strings_translated():
    from foliant.modules.text_edit.translations import EN
    catalog = dict(EN)
    base = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "text_edit"
    for extra in ("find_dialog", "spell_ui"):
        try:
            mod = __import__(f"foliant.modules.text_edit.{extra}", fromlist=["TRANSLATIONS"])
            catalog.update(getattr(mod, "TRANSLATIONS", {}))
        except Exception:
            pass
    missing = []
    for f in base.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if node.args[0].value not in catalog:
                    missing.append((f.name, node.args[0].value))
    assert not missing, missing
