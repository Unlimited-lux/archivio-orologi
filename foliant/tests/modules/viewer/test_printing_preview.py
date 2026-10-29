"""Stampa (VIS-43): anteprima nel dialogo Stampa. Fogli come usciranno dalla stampante con
tutte le opzioni, scorrimento (pulsanti e tastiera, «Foglio n di t»), aggiornamento a ogni
opzione, miniature a bassa risoluzione generate solo quando servono, nomi accessibili."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from foliant.modules.viewer.printing_engine import (
    CONTENT_DOCUMENT, CONTENT_FIELDS, DUPLEX_LONG, LAYERS_SCREEN, LAYOUT_BOOKLET, LAYOUT_POSTER, MM, PAGES_EVEN,
    poster_tiles,
)

from .conftest import part


def _numbered(tmp_path, n: int, name: str = "numerate.pdf") -> str:
    path = tmp_path / name
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 120), f"Pagina {i + 1}", fontsize=40)
    doc.save(str(path))
    return str(path)


@pytest.fixture
def form_pdf(tmp_path):
    """Sfondo blu (contenuto della pagina), campo compilato, timbro e commento rosso."""
    path = tmp_path / "modulo.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=(0.1, 0.2, 0.9))
    widget = pymupdf.Widget()
    widget.field_name, widget.field_type = "nome", pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect, widget.text_fontsize, widget.field_value = pymupdf.Rect(100, 100, 400, 140), 24, "MARIO ROSSI"
    widget.fill_color = (1, 1, 1)
    page.add_widget(widget)
    page.add_stamp_annot(pymupdf.Rect(100, 300, 400, 400), stamp=0)
    note = page.add_rect_annot(pymupdf.Rect(380, 500, 560, 700))
    note.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    note.update()
    doc.save(str(path))
    return str(path)


def _select(combo, value) -> None:
    combo.setCurrentIndex(combo.findData(value))


def _colors(image) -> dict[str, int]:
    """Pixel blu, rossi e comunque colorati dell'immagine dell'anteprima."""
    out = {"blu": 0, "rosso": 0, "colore": 0}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            out["blu"] += b > 150 and r < 90
            out["rosso"] += r > 180 and g < 90 and b < 90
            out["colore"] += max(r, g, b) - min(r, g, b) > 40
    return out


def _done(dlg, view) -> None:
    dlg.done(0)
    view.document._saved_state = view.document._state


# ------------------------------------------------------------------ fogli
def test_preview_counts_sheets_and_navigates(viewer_window, tmp_path):
    view = viewer_window.open_path(_numbered(tmp_path, 10))
    dlg = part(viewer_window, "printing").options_dialog(view)
    preview = dlg.preview
    assert dlg.sheet_label.text() == "Foglio 1 di 10"
    assert not dlg.prev_button.isEnabled() and dlg.next_button.isEnabled()
    dlg.next_button.click()
    assert dlg.sheet_label.text() == "Foglio 2 di 10" and dlg.prev_button.isEnabled()
    QTest.keyClick(preview, Qt.Key.Key_End)
    assert dlg.sheet_label.text() == "Foglio 10 di 10" and not dlg.next_button.isEnabled()
    QTest.keyClick(preview, Qt.Key.Key_PageUp)
    QTest.keyClick(preview, Qt.Key.Key_Left)
    assert preview.current == 7
    QTest.keyClick(preview, Qt.Key.Key_Home)
    QTest.keyClick(preview, Qt.Key.Key_Right)
    QTest.keyClick(preview, Qt.Key.Key_PageDown)
    assert dlg.sheet_label.text() == "Foglio 3 di 10"
    assert "Foglio 3 di 10: pagina 3" == preview.accessibleDescription()
    _done(dlg, view)


def test_preview_follows_every_option(viewer_window, tmp_path, qtbot):
    """Ogni opzione aggiorna l'anteprima (anche da sola, con il timer): più pagine per foglio,
    solo pari, ordine inverso, fronte/retro, copie, intervallo."""
    view = viewer_window.open_path(_numbered(tmp_path, 10))
    dlg = part(viewer_window, "printing").options_dialog(view)
    preview = dlg.preview
    _select(dlg.nup, 4)
    qtbot.waitUntil(lambda: dlg.sheet_label.text() == "Foglio 1 di 3", timeout=2000)
    _select(dlg.page_set, PAGES_EVEN)                     # 2 4 6 8 10 → due fogli
    dlg.reverse.setChecked(True)
    dlg.refresh_preview()
    assert dlg.sheet_label.text() == "Foglio 1 di 2"
    assert preview.accessibleDescription() == "Foglio 1 di 2: pagina 10"
    _select(dlg.duplex_combo, DUPLEX_LONG)                # le due facciate sullo stesso foglio
    dlg.refresh_preview()
    assert dlg.sheet_label.text() == "Foglio 1 di 1"
    assert preview.accessibleDescription() == "Foglio 1 di 1: fronte: pagine 2, 4, 6, 8; retro: pagina 10"
    dlg.copies.setValue(3)
    dlg.refresh_preview()
    assert dlg.total_label.text() == "Totale: 3 fogli (3 copie)"
    dlg.rb_range.setChecked(True)
    dlg.range_edit.setText("40")
    dlg.refresh_preview()
    assert not preview.sheets and dlg.sheet_label.text() == "Nessun foglio"
    assert "fuori intervallo" in preview.accessibleDescription()
    assert not dlg.prev_button.isEnabled() and not dlg.next_button.isEnabled()
    dlg.range_edit.setText("1")                           # solo pari di «1»: nessuna pagina
    dlg.refresh_preview()
    assert "non contiene pagine pari" in preview.accessibleDescription()
    dlg.accept()
    assert dlg.result_options is None and "pari" in dlg.error_label.text()
    _done(dlg, view)


def test_preview_booklet_and_duplex_show_front_and_back(viewer_window, tmp_path):
    view = viewer_window.open_path(_numbered(tmp_path, 8))
    dlg = part(viewer_window, "printing").options_dialog(view)
    _select(dlg.layout_mode, LAYOUT_BOOKLET)
    dlg.refresh_preview()
    sheet = dlg.preview.sheets[0]
    assert len(sheet.sides) == 2 and dlg.sheet_label.text() == "Foglio 1 di 2"
    assert dlg.preview.accessibleDescription() == "Foglio 1 di 2: fronte: pagine 8, 1; retro: pagine 2, 7"
    image = dlg.preview.sheet_image(400, 300)
    assert not image.isNull()
    _done(dlg, view)


# -------------------------------------------------------------- disegno
def test_preview_renders_lazily_at_low_resolution(viewer_window, long_pdf):
    view = viewer_window.open_path(long_pdf)
    dlg = part(viewer_window, "printing").options_dialog(view)
    preview = dlg.preview
    assert preview.rendered_pages() == set()              # nessuna miniatura prima del disegno
    preview.sheet_image()
    assert preview.rendered_pages() == {0}                # solo la pagina del foglio mostrato
    # alla misura del disegno (circa 400 pixel per il foglio), non a quella di stampa
    assert all(max(img.width(), img.height()) <= 500 for img in preview._cache.values())
    preview.go_to(5)
    preview.sheet_image()
    assert preview.rendered_pages() == {0, 5}
    _select(dlg.nup, 16)                                  # pagine piccole: miniature più piccole
    dlg.refresh_preview()
    preview.go_to(0)
    preview.sheet_image()
    assert preview.rendered_pages() == set(range(16)) | {5}
    assert max(max(img.width(), img.height()) for key, img in preview._cache.items() if key[1] == 12) <= 150
    _done(dlg, view)


def test_preview_draws_with_the_timer_without_blocking(viewer_window, long_pdf, qtbot):
    view = viewer_window.open_path(long_pdf)
    dlg = part(viewer_window, "printing").options_dialog(view)
    dlg.show()
    qtbot.waitUntil(lambda: dlg.preview.rendered_pages() == {0}, timeout=3000)
    dlg.next_button.click()
    qtbot.waitUntil(lambda: dlg.preview.rendered_pages() == {0, 1}, timeout=3000)
    _done(dlg, view)


def test_preview_shows_content_and_grayscale(viewer_window, form_pdf):
    """L'anteprima mostra ciò che esce: con «Documento e commenti» sfondo blu e commento rosso;
    «Solo il documento» senza commento e timbro; «Solo campi e timbri» senza lo sfondo; in
    scala di grigi nessun colore."""
    view = viewer_window.open_path(form_pdf)
    dlg = part(viewer_window, "printing").options_dialog(view)
    full = _colors(dlg.preview.sheet_image())
    assert full["blu"] > 200 and full["rosso"] > 50
    _select(dlg.content, CONTENT_DOCUMENT)
    dlg.refresh_preview()
    document = _colors(dlg.preview.sheet_image())
    assert document["blu"] > 200 and document["rosso"] == 0
    _select(dlg.content, CONTENT_FIELDS)
    dlg.refresh_preview()
    fields = _colors(dlg.preview.sheet_image())
    assert fields["blu"] == 0 and fields["rosso"] > 5          # resta il timbro
    dlg.grayscale.setChecked(True)
    dlg.refresh_preview()
    assert _colors(dlg.preview.sheet_image())["colore"] == 0
    assert not view.document.is_modified
    _done(dlg, view)


def test_preview_page_orientation_and_scaling(viewer_window, tmp_path):
    """Pagina orizzontale: con l'orientamento automatico il foglio è orizzontale come in stampa."""
    path = tmp_path / "orizz.pdf"
    doc = pymupdf.open()
    doc.new_page(width=842, height=595).draw_rect(pymupdf.Rect(0, 0, 842, 595), color=None, fill=(0, 0, 1))
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    dlg = part(viewer_window, "printing").options_dialog(view)
    width, height = dlg.preview._sheet_size
    assert width > height
    dlg.auto_rotate.setChecked(False)
    dlg.refresh_preview()
    width, height = dlg.preview._sheet_size
    assert width < height
    _done(dlg, view)


# --------------------------------------------------------- accessibilità
def test_dialog_is_keyboard_usable_with_accessible_names(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    dlg = part(viewer_window, "printing").options_dialog(view)
    for label, field in ((dlg.page_set_label, dlg.page_set), (dlg.content_label, dlg.content),
                         (dlg.nup_label, dlg.nup), (dlg.overlap_label, dlg.overlap),
                         (dlg.layers_label, dlg.layers)):
        assert label.buddy() is field and field.accessibleName() == label.text()
    for widget in (dlg.range_edit, dlg.copies, dlg.layout_mode, dlg.duplex_combo):
        assert widget.accessibleName()
    for button in (dlg.prev_button, dlg.next_button):
        assert button.toolTip() and button.accessibleName() == button.toolTip()
        assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    preview = dlg.preview
    assert preview.accessibleName() == "Anteprima di stampa"
    assert preview.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert "Pagina giù" in preview.toolTip()
    # l'anteprima e i pulsanti sono nella catena del tasto Tab
    chain, w = [], dlg.nextInFocusChain()
    while w is not dlg and len(chain) < 200:
        chain.append(w)
        w = w.nextInFocusChain()
    assert preview in chain and dlg.next_button in chain and dlg.content in chain and dlg.page_set in chain
    tips = [dlg.content.itemData(i, Qt.ItemDataRole.ToolTipRole) for i in range(dlg.content.count())]
    assert all(tips) and "prestampati" in tips[2]
    _done(dlg, view)


# ----------------------------------------------------- livelli e poster
def test_preview_layers_follow_print_state(viewer_window, tmp_path):
    """Livello «solo schermo» (/PrintState /OFF): l'anteprima lo nasconde con le impostazioni
    di stampa del documento e lo mostra con «Come a schermo»."""
    path = tmp_path / "livelli.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    ocg = doc.add_ocg("Solo schermo", on=True)
    doc.xref_set_key(ocg, "Usage", "<</Print<</PrintState/OFF>>>>")
    page.draw_rect(pymupdf.Rect(100, 100, 500, 700), color=(1, 0, 0), fill=(1, 0, 0), oc=ocg)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    dlg = part(viewer_window, "printing").options_dialog(view)
    assert dlg.layers.isVisibleTo(dlg)
    assert _colors(dlg.preview.sheet_image())["rosso"] == 0
    _select(dlg.layers, LAYERS_SCREEN)
    dlg.refresh_preview()
    assert _colors(dlg.preview.sheet_image())["rosso"] > 200
    assert not view.document.is_modified
    _done(dlg, view)


def test_preview_poster_has_one_sheet_per_tile(viewer_window, tmp_path):
    path = tmp_path / "a1.pdf"
    doc = pymupdf.open()
    doc.new_page(width=1683.78, height=2383.94).insert_text((60, 120), "POSTER", fontsize=40)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    dlg = part(viewer_window, "printing").options_dialog(view)
    _select(dlg.layout_mode, LAYOUT_POSTER)
    dlg.refresh_preview()
    area = dlg.preview._area
    tiles = poster_tiles(1683.78, 2383.94, area.width(), area.height(), 10 * MM)
    assert len(tiles) > 1 and len(dlg.preview.sheets) == len(tiles)
    assert dlg.sheet_label.text() == f"Foglio 1 di {len(tiles)}"
    assert not dlg.preview.sheet_image().isNull()
    _done(dlg, view)
