"""Stampa: intervalli, permessi (VIS-06), orientamento (VIS-11), livelli (VIS-20), output."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
from PySide6.QtPrintSupport import QPrinter

from foliant.modules.viewer import printing as printing_mod
from foliant.modules.viewer.printing import PrintOptionsDialog
from foliant.modules.viewer.printing_engine import (
    ACTUAL_SIZE, FIT_PAGE, LAYERS_PRINT, LAYERS_SCREEN, LOW_QUALITY_DPI, PrintOptions, RangeError,
    parse_page_ranges, placement, print_capability, print_document,
)

from .conftest import part

RED_RECT = pymupdf.Rect(150, 300, 450, 600)


@pytest.fixture(autouse=True)
def _no_message_boxes(monkeypatch):
    """Un errore inatteso non deve aprire un dialogo modale (bloccherebbe i test)."""
    from PySide6.QtWidgets import QMessageBox

    def fail(_parent, title, text, *a, **k):
        raise AssertionError(f"messaggio inatteso: {title}: {text}")

    monkeypatch.setattr(QMessageBox, "warning", fail)
    monkeypatch.setattr(QMessageBox, "information", fail)


def _printer(path) -> QPrinter:
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(str(path))
    pr.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    pr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    return pr


def _pixel(path, page: int, point: tuple[float, float]) -> tuple[int, int, int]:
    """Colore dell'output nel punto (in punti PDF) della pagina."""
    out = pymupdf.open(str(path))
    pix = out[page].get_pixmap(matrix=pymupdf.Matrix(1, 1))
    return pix.pixel(int(point[0]), int(point[1]))[:3]


def _is_red(rgb) -> bool:
    r, g, b = rgb
    return r > 180 and g < 90 and b < 90


def _is_white(rgb) -> bool:
    return min(rgb) > 220


def _drive(monkeypatch, ctl, options: PrintOptions | None, device):
    calls = {}

    def ask_options(view):
        calls["options"] = True
        return options

    def ask_printer(view, opts):
        calls["printer"] = opts
        return device

    monkeypatch.setattr(ctl, "ask_options", ask_options)
    monkeypatch.setattr(ctl, "ask_printer", ask_printer)
    return calls


@pytest.fixture
def annotated_pdf(tmp_path):
    path = tmp_path / "annotato.pdf"
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=24)
    annot = doc[0].add_rect_annot(RED_RECT)
    annot.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    annot.update()
    doc.save(str(path))
    doc.close()
    return str(path)


def _protected(tmp_path, name, permissions, owner="o"):
    path = tmp_path / name
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=24)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw=owner, user_pw="",
             permissions=permissions)
    doc.close()
    return str(path)


# ------------------------------------------------------------------ motore
def test_parse_page_ranges():
    assert parse_page_ranges("1-3, 5, 8-", 10) == [0, 1, 2, 4, 7, 8, 9]
    assert parse_page_ranges("-2; 4", 10) == [0, 1, 3]
    assert parse_page_ranges(" 6 - 4 ", 10) == [5, 4, 3]
    assert parse_page_ranges("7", 7) == [6]
    for bad in ("", "a", "1-2-3", "3--", "-"):
        with pytest.raises(RangeError):
            parse_page_ranges(bad, 10)
    with pytest.raises(RangeError, match="fuori intervallo"):
        parse_page_ranges("2, 12", 10)
    with pytest.raises(RangeError, match="Intervallo non valido"):
        parse_page_ranges("1-x", 10)


def test_placement_fit_actual_and_rotation():
    # A4 su A4: adatta = scala 1, centrata a 0
    p = placement(595, 842, 595, 842, FIT_PAGE)
    assert not p.rotate and p.scale == pytest.approx(1) and p.x == pytest.approx(0)
    # A5 dimensione reale su A4: scala 1, centrata
    p = placement(420, 595, 595, 842, ACTUAL_SIZE)
    assert p.scale == 1 and p.x == pytest.approx((595 - 420) / 2) and p.y == pytest.approx((842 - 595) / 2)
    # A5 adattata: ingrandita
    assert placement(420, 595, 595, 842, FIT_PAGE).scale > 1.3
    # A3 reale su A4: ridotta per entrare
    assert placement(842, 1191, 595, 842, ACTUAL_SIZE).scale < 1
    # pagina orizzontale su foglio verticale: ruotata con orientamento automatico
    p = placement(842, 595, 595, 842, FIT_PAGE, auto_rotate=True)
    assert p.rotate and p.scale == pytest.approx(1)
    p = placement(842, 595, 595, 842, FIT_PAGE, auto_rotate=False)
    assert not p.rotate and p.scale == pytest.approx(595 / 842)
    assert p.y > 0


def test_print_capability_bits():
    full = print_capability(-1)
    assert full.allowed and full.max_dpi == 300 and full.reason == ""
    low = print_capability(pymupdf.PDF_PERM_PRINT)
    assert low.allowed and low.max_dpi <= 150 and "bassa qualità" in low.reason
    none = print_capability(pymupdf.PDF_PERM_ACCESSIBILITY)
    assert not none.allowed and "non consentono la stampa" in none.reason


def test_options_sequence_copies():
    o = PrintOptions(pages=[0, 1], copies=2)
    assert o.sequence() == [0, 1, 0, 1]
    o.collate = False
    assert o.sequence() == [0, 0, 1, 1]


# ---------------------------------------------------------------- interfaccia
def test_action_in_read_mode_and_menu(viewer_window, sample_pdf):
    ctl = part(viewer_window, "printing")
    assert ctl.action.shortcut().toString() == "Ctrl+P"
    assert ctl.action in viewer_window.modes["leggi"].toolbar.actions()
    assert any(ctl.action in acts for acts in viewer_window._menu_sections.values())
    assert not ctl.action.isEnabled()            # nessun documento
    viewer_window.open_path(sample_pdf)
    assert ctl.action.isEnabled()


def test_print_range_to_pdf(viewer_window, long_pdf, tmp_path, monkeypatch):
    viewer_window.open_path(long_pdf)
    ctl = part(viewer_window, "printing")
    out = tmp_path / "out.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=parse_page_ranges("2-4, 10", 30)), _printer(out))
    assert ctl.print_current() == 4
    result = pymupdf.open(str(out))
    assert result.page_count == 4
    # contenuto: ogni foglio contiene un'immagine della pagina
    assert all(page.get_images() for page in result)


def test_print_copies(viewer_window, sample_pdf, tmp_path, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = part(viewer_window, "printing")
    out = tmp_path / "copie.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], copies=3), _printer(out))
    assert ctl.print_current() == 3
    assert pymupdf.open(str(out)).page_count == 3


def test_print_cancelled_by_options(viewer_window, sample_pdf, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = part(viewer_window, "printing")
    calls = _drive(monkeypatch, ctl, None, None)
    assert ctl.print_current() == 0
    assert "printer" not in calls


@pytest.mark.parametrize("with_annots", [True, False])
def test_print_with_or_without_annotations(viewer_window, annotated_pdf, tmp_path, monkeypatch, with_annots):
    viewer_window.open_path(annotated_pdf)
    ctl = part(viewer_window, "printing")
    out = tmp_path / "annot.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], annotations=with_annots), _printer(out))
    assert ctl.print_current() == 1
    center = (RED_RECT.x0 + RED_RECT.width / 2, RED_RECT.y0 + RED_RECT.height / 2)
    rgb = _pixel(out, 0, center)
    assert _is_red(rgb) if with_annots else _is_white(rgb)


def test_annotation_without_print_flag_is_not_printed(viewer_window, tmp_path, monkeypatch):
    """VIS-43: un'annotazione senza il flag /Print non va su carta, anche con «annotazioni»."""
    path = tmp_path / "noprint.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    annot = page.add_rect_annot(RED_RECT)
    annot.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    annot.set_flags(0)                                  # visibile a schermo, non stampabile
    annot.update()
    doc.save(path)
    viewer_window.open_path(str(path))
    ctl = part(viewer_window, "printing")
    out = tmp_path / "out.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], annotations=True), _printer(out))
    assert ctl.print_current() == 1
    center = (RED_RECT.x0 + RED_RECT.width / 2, RED_RECT.y0 + RED_RECT.height / 2)
    assert _is_white(_pixel(out, 0, center))


def test_form_values_printed_without_annotations(viewer_window, tmp_path, monkeypatch):
    """VIS-43: senza «annotazioni e commenti» si tolgono i commenti, non i valori dei campi."""
    path = tmp_path / "modulo.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    widget = pymupdf.Widget()
    widget.field_name, widget.field_type = "prezzo", pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect, widget.text_fontsize = pymupdf.Rect(100, 100, 400, 140), 24
    widget.field_value = "EUR 7250 MATRICOLA"
    page.add_widget(widget)
    note = page.add_rect_annot(RED_RECT)
    note.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    note.update()
    doc.save(path)
    viewer_window.open_path(str(path))
    ctl = part(viewer_window, "printing")
    out = tmp_path / "out.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], annotations=False), _printer(out))
    assert ctl.print_current() == 1
    result = pymupdf.open(str(out))
    pix = result[0].get_pixmap(clip=pymupdf.Rect(100, 100, 400, 140))
    dark = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 100)
    assert dark > 50                                    # il valore del campo è stampato
    center = (RED_RECT.x0 + RED_RECT.width / 2, RED_RECT.y0 + RED_RECT.height / 2)
    assert _is_white(_pixel(out, 0, center))            # il commento no


def test_permission_denied_disables_action(viewer_window, tmp_path, monkeypatch):
    path = _protected(tmp_path, "nostampa.pdf", pymupdf.PDF_PERM_ACCESSIBILITY)
    viewer_window.open_path(path)
    ctl = part(viewer_window, "printing")
    assert not ctl.action.isEnabled()
    assert "non consentono la stampa" in ctl.action.toolTip()
    # anche se invocata (es. scorciatoia), nessun dialogo e nessuna stampa
    calls = _drive(monkeypatch, ctl, PrintOptions(pages=[0]), None)
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    assert ctl.print_current() == 0
    assert not calls and errors


def test_permission_state_follows_current_document(viewer_window, tmp_path, sample_pdf):
    ctl = part(viewer_window, "printing")
    viewer_window.open_path(_protected(tmp_path, "nostampa.pdf", pymupdf.PDF_PERM_ACCESSIBILITY))
    assert not ctl.action.isEnabled()
    viewer_window.open_path(sample_pdf)
    assert ctl.action.isEnabled()
    assert "non consentono" not in ctl.action.toolTip()
    viewer_window.tabs.setCurrentIndex(0)
    assert not ctl.action.isEnabled()


def test_unlock_enables_print_without_switching_tab(viewer_window, tmp_path):
    """SEC-02: «Sblocca…» con la password dei permessi riattiva subito Stampa, cattura area e
    segnalibri, senza cambiare scheda né modificare il documento."""
    viewer_window.open_path(_protected(tmp_path, "permessi.pdf", pymupdf.PDF_PERM_ACCESSIBILITY, owner="capo"))
    view = viewer_window.current_view()
    ctl = part(viewer_window, "printing")
    bookmarks = part(viewer_window, "bookmarks").panel
    bookmarks.set_view(view)
    assert not ctl.action.isEnabled()
    assert not bookmarks.editable
    assert view.document.unlock("capo")
    assert ctl.action.isEnabled()
    assert "non consentono" not in ctl.action.toolTip()
    assert bookmarks.editable and bookmarks.actions["add"].isEnabled()
    assert "non consente" not in part(viewer_window, "tools").act_snapshot.toolTip()


def test_unlock_of_background_tab_updates_state_when_shown(viewer_window, tmp_path, sample_pdf):
    viewer_window.open_path(_protected(tmp_path, "permessi.pdf", pymupdf.PDF_PERM_ACCESSIBILITY, owner="capo"))
    locked = viewer_window.current_view()
    viewer_window.open_path(sample_pdf)
    ctl = part(viewer_window, "printing")
    assert ctl.action.isEnabled()
    assert locked.document.unlock("capo")          # vista non corrente: lo stato della corrente non cambia
    assert ctl.action.isEnabled()
    viewer_window.ctx.activate_view(locked)
    assert ctl.action.isEnabled()


def test_permission_kept_after_edit(viewer_window, tmp_path):
    """Dopo una modifica la copia interna non è cifrata: i permessi restano quelli d'apertura."""
    viewer_window.open_path(_protected(tmp_path, "nostampa.pdf",
                                       pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_MODIFY))
    view = viewer_window.current_view()
    with view.document.change("prova") as pdf:
        pdf[0].insert_text((72, 200), "x")
    view.document._saved_state = view.document._state   # niente richiesta di salvataggio in chiusura
    assert not part(viewer_window, "printing").action.isEnabled()


def test_low_quality_print_max_150_dpi(viewer_window, tmp_path, monkeypatch):
    path = _protected(tmp_path, "bassa.pdf", pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT)
    viewer_window.open_path(path)
    ctl = part(viewer_window, "printing")
    assert ctl.action.isEnabled()
    assert "bassa qualità" in ctl.action.toolTip()
    out = tmp_path / "bassa_out.pdf"
    calls = _drive(monkeypatch, ctl, PrintOptions(pages=[0]), _printer(out))
    assert ctl.print_current() == 1
    assert calls["printer"].max_dpi <= LOW_QUALITY_DPI
    page = pymupdf.open(str(out))[0]
    (img,) = page.get_images(full=True)
    dpi = img[2] / (page.rect.width / 72)
    assert dpi <= 151


def test_high_quality_print_uses_more_than_150_dpi(viewer_window, sample_pdf, tmp_path, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = part(viewer_window, "printing")
    out = tmp_path / "alta.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0]), _printer(out))
    ctl.print_current()
    page = pymupdf.open(str(out))[0]
    img = page.get_images(full=True)[0]
    assert img[2] / (page.rect.width / 72) > 200


def test_print_uses_document_orientation_not_view_rotation(viewer_window, sample_pdf, tmp_path, monkeypatch):
    viewer_window.open_path(sample_pdf)
    view = viewer_window.current_view()
    view.rotate_view(90)
    ctl = part(viewer_window, "printing")
    out = tmp_path / "ruotata.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], auto_rotate=False), _printer(out))
    ctl.print_current()
    img = pymupdf.open(str(out))[0].get_images(full=True)[0]
    assert img[3] > img[2]   # pagina verticale come nel documento


def test_landscape_page_auto_rotated_on_portrait_sheet(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "orizz.pdf"
    doc = pymupdf.open()
    doc.new_page(width=842, height=595).insert_text((72, 100), "Orizzontale", fontsize=24)
    doc.save(str(path))
    viewer_window.open_path(str(path))
    ctl = part(viewer_window, "printing")
    out = tmp_path / "orizz_out.pdf"
    _drive(monkeypatch, ctl, PrintOptions(pages=[0], auto_rotate=True), _printer(out))
    ctl.print_current()
    page = pymupdf.open(str(out))[0]
    assert page.rect.height > page.rect.width        # foglio A4 verticale
    # l'immagine riempie il foglio: margini bianchi assenti al centro dei bordi
    bbox = page.get_image_rects(page.get_images()[0][0])[0]
    assert bbox.width == pytest.approx(595, abs=3) and bbox.height == pytest.approx(842, abs=3)


def test_layers_print_state(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "livelli.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    ocg = doc.add_ocg("Solo schermo", on=True)
    doc.xref_set_key(ocg, "Usage", "<</Print<</PrintState/OFF>>>>")
    page.draw_rect(RED_RECT, color=(1, 0, 0), fill=(1, 0, 0), oc=ocg)
    doc.save(str(path))
    viewer_window.open_path(str(path))
    ctl = part(viewer_window, "printing")
    center = (RED_RECT.x0 + RED_RECT.width / 2, RED_RECT.y0 + RED_RECT.height / 2)
    for mode, red in ((LAYERS_PRINT, False), (LAYERS_SCREEN, True)):
        out = tmp_path / f"liv_{mode}.pdf"
        _drive(monkeypatch, ctl, PrintOptions(pages=[0], layers=mode), _printer(out))
        ctl.print_current()
        assert _is_red(_pixel(out, 0, center)) is red
    # il documento non è stato toccato
    assert not viewer_window.current_view().document.is_modified


def test_print_document_on_pdfwriter(sample_pdf, qapp, tmp_path):
    from foliant.core.document import Document
    doc = Document.open(sample_pdf)
    out = tmp_path / "writer.pdf"
    writer = QPdfWriter(str(out))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    assert print_document(doc, writer, PrintOptions(pages=[2, 0])) == 2
    del writer
    assert pymupdf.open(str(out)).page_count == 2
    doc.close()


def test_print_error_reported_not_raised(viewer_window, sample_pdf, monkeypatch):
    viewer_window.open_path(sample_pdf)
    ctl = part(viewer_window, "printing")
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    monkeypatch.setattr(printing_mod, "print_document", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("guasto")))
    _drive(monkeypatch, ctl, PrintOptions(pages=[0]), object())
    assert ctl.print_current() == 0
    assert errors == ["guasto"]


def test_options_dialog_validation(viewer_window, long_pdf):
    viewer_window.open_path(long_pdf)
    view = viewer_window.current_view()
    view.go_to_page(4)
    dlg = part(viewer_window, "printing").options_dialog(view)
    assert isinstance(dlg, PrintOptionsDialog)
    assert dlg.options().pages == list(range(30))
    dlg.rb_current.setChecked(True)
    assert dlg.options().pages == [4]
    dlg.rb_range.setChecked(True)
    dlg.range_edit.setText("3-1, 40")
    dlg.accept()
    assert dlg.result_options is None
    assert "fuori intervallo" in dlg.error_label.text()
    dlg.range_edit.setText("3-1, 30")
    dlg.rb_actual.setChecked(True)
    dlg.annotations.setChecked(False)
    dlg.copies.setValue(2)
    dlg.accept()
    o = dlg.result_options
    assert o.pages == [2, 1, 0, 29] and o.scaling == ACTUAL_SIZE and not o.annotations and o.copies == 2
    assert not dlg.layers.isVisible()   # nessun livello nel documento


def test_empty_signature_field_not_printed(tmp_path):
    import pymupdf
    from foliant.api import Document
    from foliant.modules.viewer.printing_engine import render_page
    path = tmp_path / "firma.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    w.field_name = "Firma"
    w.rect = pymupdf.Rect(100, 100, 300, 160)
    page.add_widget(w)
    doc.save(str(path))
    d = Document.open(path)
    img = render_page(d.pdf, 0, 72, True)

    def orange(c):
        return c.red() > 200 and 80 < c.green() < 200 and c.blue() < 80

    pixels = [img.pixelColor(x, y) for x in range(100, 300, 4) for y in range(100, 160, 4)]
    assert not any(orange(c) for c in pixels)
    assert not d.is_modified
