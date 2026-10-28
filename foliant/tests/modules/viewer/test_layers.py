"""Test del pannello Livelli (VIS-20 e «salva visibilità come predefinita» di VIS-21)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QCheckBox, QRadioButton

from foliant.modules.viewer import layers_engine as engine

from .conftest import part


@pytest.fixture
def layers_pdf(tmp_path):
    path = tmp_path / "tavola.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Base sempre visibile", fontsize=14)
    arredi = doc.add_ocg("Arredi", on=True)
    quote = doc.add_ocg("Quote", on=False)
    it = doc.add_ocg("IT", on=True)
    en = doc.add_ocg("EN", on=False)
    de = doc.add_ocg("DE", on=False)
    cartiglio = doc.add_ocg("Cartiglio", on=True)
    doc.set_layer(-1, rbgroups=[[it, en, de]], locked=[cartiglio])
    page.insert_text((72, 140), "Divano e tavolo", fontsize=14, oc=arredi)
    page.insert_text((72, 200), "Misura 4,20 m", fontsize=40, oc=quote)
    page.insert_text((72, 300), "Testo italiano", fontsize=14, oc=it)
    page.insert_text((72, 330), "English text", fontsize=14, oc=en)
    page.insert_text((72, 360), "Deutscher Text", fontsize=14, oc=de)
    page.insert_text((72, 420), "Cartiglio bloccato", fontsize=14, oc=cartiglio)
    doc.save(str(path))
    doc.close()
    return str(path)


def _open(window, path):
    view = window.ctx.open_path(path)
    window.ctx.show_sidebar_panel("layers")
    return view, part(window, "layers")


def _on(panel, name):
    return panel.layer(name).on


def _text(view):
    return view.document.page_text(0)


def _pixels(view):
    view.flush_rendering()
    img = view.render_page_image(0, 0.5)
    return bytes(img.constBits())


def test_tree_lists_layers_with_widgets(viewer_window, layers_pdf):
    _view, panel = _open(viewer_window, layers_pdf)
    assert not panel.is_empty
    assert [layer.text for layer in panel.layers()] == ["Arredi", "Quote", "IT", "EN", "DE", "Cartiglio"]
    w = panel._widgets
    by_name = {layer.text: w[layer.number] for layer in panel.layers()}
    assert isinstance(by_name["Quote"], QCheckBox)
    assert isinstance(by_name["EN"], QRadioButton)
    assert by_name["Arredi"].isChecked() and not by_name["Quote"].isChecked()
    assert not by_name["Cartiglio"].isEnabled()
    assert panel.count_label.text() == "6 livelli"


def test_quote_off_then_shown_without_modifying(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    doc = view.document
    assert not _on(panel, "Quote")
    assert "Misura" not in _text(view)
    assert "Divano" in _text(view)
    before = _pixels(view)
    # clic sulla casella
    widget = panel._widgets[panel.layer("Quote").number]
    widget.setChecked(True)
    assert _on(panel, "Quote")
    assert "Misura 4,20 m" in _text(view)
    assert _pixels(view) != before
    assert not doc.is_modified
    assert not doc.can_undo
    # la vista ha scartato le immagini in cache e ridisegna
    view.flush_rendering()
    assert any(k[0] == 0 for k in view._cache)


def test_radio_group_is_exclusive(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    assert _on(panel, "IT") and not _on(panel, "EN") and not _on(panel, "DE")
    panel._widgets[panel.layer("EN").number].setChecked(True)
    assert _on(panel, "EN") and not _on(panel, "IT") and not _on(panel, "DE")
    widgets = {layer.text: panel._widgets[layer.number] for layer in panel.layers()}
    assert widgets["EN"].isChecked() and not widgets["IT"].isChecked()
    text = _text(view)
    assert "English text" in text and "Testo italiano" not in text
    assert not view.document.is_modified


def test_locked_layer_does_not_change(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    assert _on(panel, "Cartiglio")
    assert not panel.set_visible_by_name("Cartiglio", False)
    assert _on(panel, "Cartiglio")
    assert "Cartiglio bloccato" in _text(view)
    with view.document.lock:   # anche il motore lo rifiuta
        view.document.pdf.set_layer_ui_config(panel.layer("Cartiglio").number, 2)
    assert _on(panel, "Cartiglio")


def test_reset_initial_and_show_all(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    panel.set_visible_by_name("Arredi", False)
    panel.set_visible_by_name("DE", True)
    assert not _on(panel, "Arredi") and _on(panel, "DE")
    assert panel.reset_initial()
    assert [(layer.text, layer.on) for layer in panel.layers()] == [
        ("Arredi", True), ("Quote", False), ("IT", True), ("EN", False), ("DE", False), ("Cartiglio", True)]
    assert panel.show_all()
    states = {layer.text: layer.on for layer in panel.layers()}
    assert states["Arredi"] and states["Quote"] and states["Cartiglio"]
    assert sum(states[n] for n in ("IT", "EN", "DE")) == 1      # il gruppo radio resta esclusivo
    assert not view.document.is_modified


def test_state_kept_after_undo_redo(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    doc = view.document
    panel.set_visible_by_name("Quote", True)
    panel.set_visible_by_name("EN", True)
    with doc.change("Ruota pagina") as pdf:
        pdf[0].set_rotation(90)
    assert _on(panel, "Quote") and _on(panel, "EN")
    doc.undo()      # il documento interno viene sostituito
    assert _on(panel, "Quote") and _on(panel, "EN") and not _on(panel, "IT")
    assert "Misura 4,20 m" in _text(view)
    widget = panel._widgets[panel.layer("Quote").number]
    assert widget.isChecked()
    doc.redo()
    assert _on(panel, "Quote") and _on(panel, "EN")


def test_state_is_per_view(viewer_window, layers_pdf, tmp_path):
    import shutil
    copy = tmp_path / "copia.pdf"
    shutil.copy(layers_pdf, copy)
    v1, panel = _open(viewer_window, layers_pdf)
    panel.set_visible_by_name("Quote", True)
    v2 = viewer_window.ctx.open_path(str(copy))
    assert panel.view is v2 and not _on(panel, "Quote")
    viewer_window.tabs.setCurrentWidget(v1)
    assert panel.view is v1 and _on(panel, "Quote")


def test_filter_by_name(viewer_window, layers_pdf):
    _view, panel = _open(viewer_window, layers_pdf)
    panel.filter_edit.setText("ARR")
    assert panel.visible_rows() == ["Arredi"]
    panel.filter_edit.setText("")
    assert len(panel.visible_rows()) == 6


def test_no_layers_empty_state(viewer_window, sample_pdf):
    panel = part(viewer_window, "layers")
    assert panel.is_empty and "Nessun documento" in panel.empty.text()
    viewer_window.ctx.open_path(sample_pdf)
    assert panel.is_empty and "non contiene livelli" in panel.empty.text()
    assert not panel.btn_reset.isEnabled()


def test_nested_tree_and_labels(viewer_window, tmp_path):
    path = tmp_path / "annidati.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    a = doc.add_ocg("Piano terra")
    b = doc.add_ocg("Impianti")
    c = doc.add_ocg("Elettrico")
    page.insert_text((72, 100), "x", oc=c)
    # /Order con un'etichetta di gruppo e livelli annidati
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties/D/Order", f"[(Edificio) {a} 0 R [{b} 0 R [{c} 0 R]]]")
    doc.save(str(path))
    doc.close()
    _view, panel = _open(viewer_window, str(path))
    assert panel.count_label.text() == "3 livelli"
    assert panel.tree.topLevelItemCount() == 1
    top = panel.tree.topLevelItem(0)
    label = panel.tree.itemWidget(top, 0)
    assert label.text() == "Edificio" and not isinstance(label, QCheckBox)
    chain = []
    row = top
    while row.childCount():
        row = row.child(0)
        chain.append(panel.tree.itemWidget(row, 0).text())
    assert chain == ["Piano terra", "Impianti", "Elettrico"]
    panel.filter_edit.setText("elettr")
    assert panel.visible_rows() == ["Edificio", "Piano terra", "Impianti", "Elettrico"]
    panel.filter_edit.setText("piano")
    assert panel.visible_rows() == ["Edificio", "Piano terra"]


def test_save_visibility_as_default(viewer_window, layers_pdf, tmp_path):
    view, panel = _open(viewer_window, layers_pdf)
    doc = view.document
    panel.set_visible_by_name("Quote", True)
    panel.set_visible_by_name("Arredi", False)
    assert panel.save_as_default()
    assert doc.is_modified and doc.can_undo
    out = tmp_path / "salvato.pdf"
    doc.save(out)
    with pymupdf.open(str(out)) as again:
        states = {layer.text: layer.on for layer in engine.layers(again)}
        assert states["Quote"] and not states["Arredi"] and states["IT"] and states["Cartiglio"]
        assert "Misura" in again[0].get_text() and "Divano" not in again[0].get_text()
        default = again.get_layer(-1)
        assert default["rbgroups"] and default["locked"]


def test_engine_apply_state_respects_names():
    doc = pymupdf.open()
    page = doc.new_page()
    a = doc.add_ocg("A", on=False)
    page.insert_text((72, 100), "A", oc=a)
    doc = pymupdf.open("pdf", doc.tobytes())
    assert not engine.apply_state(doc, {0: ("Altro", True)})
    assert engine.apply_state(doc, {0: ("A", True)})
    assert engine.layers(doc)[0].on
    assert engine.matches(engine.layers(doc)[0], "a") and not engine.matches(engine.layers(doc)[0], "zz")


def test_thumbnails_follow_layer_visibility(viewer_window, layers_pdf):
    view, panel = _open(viewer_window, layers_pdf)
    thumbs = viewer_window.ctx.viewer_parts["thumbnails"].panel
    thumbs.set_view(view)
    viewer_window.ctx.show_sidebar_panel("thumbnails")
    thumbs.flush()
    before = thumbs.image(0)
    assert before is not None
    panel._widgets[panel.layer("Quote").number].setChecked(True)
    assert thumbs.image(0) is None      # da rigenerare con il nuovo stato
    thumbs.flush()
    assert thumbs.image(0) is not None and thumbs.image(0) != before
