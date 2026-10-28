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


# ------------------------------------------------------------- opuscolo e poster (VIS-44)
def _numbered(tmp_path, n, size=(595, 842), name="numerate.pdf"):
    path = tmp_path / name
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page(width=size[0], height=size[1]).insert_text((50, 100), f"PAG{i + 1:02d}", fontsize=30)
    doc.save(str(path))
    return str(path)


def _sides_of(doc):
    """Per ogni facciata: (numero a sinistra, numero a destra) letti dal testo."""
    out = []
    for page in doc:
        mid = page.rect.width / 2
        left = right = None
        for w in page.get_text("words"):
            if w[4].startswith("PAG"):
                if (w[0] + w[2]) / 2 < mid:
                    left = int(w[4][3:])
                else:
                    right = int(w[4][3:])
        out.append((left, right))
    return out


def test_booklet_sides_order():
    from foliant.modules.viewer.printing_engine import booklet_sides
    one_based = [tuple(None if x is None else x + 1 for x in s) for s in booklet_sides(8)]
    assert one_based == [(8, 1), (2, 7), (6, 3), (4, 5)]
    assert [tuple(None if x is None else x + 1 for x in s) for s in booklet_sides(8, rtl=True)][0] == (1, 8)


def test_booklet_8_pages_to_file(qapp, tmp_path):
    """Dato un documento di 8 pagine in modalità opuscolo, la stampa su file dà 4 facciate con le
    coppie (8,1), (2,7), (6,3), (4,5)."""
    from foliant.core.document import Document
    from foliant.modules.viewer.printing_engine import LAYOUT_BOOKLET, impose_booklet
    path = _numbered(tmp_path, 8)
    assert _sides_of(impose_booklet(pymupdf.open(path), list(range(8)))) == [(8, 1), (2, 7), (6, 3), (4, 5)]
    doc = Document.open(path)
    out = tmp_path / "opuscolo.pdf"
    printer = _printer(out)
    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    assert print_document(doc, printer, PrintOptions(pages=list(range(8)), layout=LAYOUT_BOOKLET)) == 4
    del printer
    doc.close()
    printed = pymupdf.open(str(out))
    assert printed.page_count == 4 and printed[0].rect.width > printed[0].rect.height


def test_booklet_6_pages_adds_blanks(qapp, tmp_path):
    """Dato un documento di 6 pagine in modalità opuscolo, si aggiungono 2 pagine bianche e si
    ottengono 4 facciate."""
    from foliant.core.document import Document
    from foliant.modules.viewer.printing_engine import LAYOUT_BOOKLET, impose_booklet
    path = _numbered(tmp_path, 6)
    assert _sides_of(impose_booklet(pymupdf.open(path), list(range(6)))) == [(None, 1), (2, None), (6, 3), (4, 5)]
    doc = Document.open(path)
    out = tmp_path / "opuscolo6.pdf"
    printer = _printer(out)
    printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    assert print_document(doc, printer, PrintOptions(pages=list(range(6)), layout=LAYOUT_BOOKLET)) == 4
    del printer
    doc.close()
    assert pymupdf.open(str(out)).page_count == 4


def test_poster_a1_on_a4_with_10mm_overlap(qapp, tmp_path):
    """Data una pagina A1 stampata come poster su A4 al 100% con sovrapposizione di 10 mm, ogni
    punto della pagina compare in almeno un foglio e i fogli adiacenti condividono 10 mm."""
    from foliant.core.document import Document
    from foliant.modules.viewer.printing_engine import LAYOUT_POSTER, MM, poster_tiles
    a1 = (1683.78, 2383.94)
    a4 = (595.28, 841.89)
    tiles = poster_tiles(*a1, *a4, 10 * MM)
    # copertura: ogni punto di una griglia fitta cade in almeno un riquadro
    for gx in range(0, 1684, 23):
        for gy in range(0, 2384, 23):
            assert any(t.x0 <= gx <= t.x1 and t.y0 <= gy <= t.y1 for t in tiles), (gx, gy)
    # fogli adiacenti: fascia condivisa di 10 mm (salvo l'ultimo, che può sovrapporsi di più)
    xs = sorted({round(t.x0, 3) for t in tiles})
    ys = sorted({round(t.y0, 3) for t in tiles})
    assert abs((xs[1] - xs[0]) - (a4[0] - 10 * MM)) < 0.01
    assert abs((ys[1] - ys[0]) - (a4[1] - 10 * MM)) < 0.01
    for t in tiles:
        assert t.width <= a4[0] + 0.01 and t.height <= a4[1] + 0.01
    path = tmp_path / "a1.pdf"
    d = pymupdf.open()
    d.new_page(width=a1[0], height=a1[1]).insert_text((60, 120), "POSTER", fontsize=40)
    d.save(str(path))
    doc = Document.open(str(path))
    out = tmp_path / "poster.pdf"
    printer = _printer(out)
    assert print_document(doc, printer, PrintOptions(pages=[0], layout=LAYOUT_POSTER,
                                                     poster_overlap_mm=10)) == len(tiles)
    del printer
    doc.close()
    assert pymupdf.open(str(out)).page_count == len(tiles)
    from foliant.modules.viewer.printing_engine import impose_poster
    sheets = impose_poster(pymupdf.open(str(path)), [0], *a4, 10)
    assert sheets.page_count == len(tiles) and "POSTER" in sheets[0].get_text()


def test_small_page_poster_is_single_sheet():
    from foliant.modules.viewer.printing_engine import poster_tiles
    assert len(poster_tiles(300, 400, 595, 842, 28)) == 1


def test_dialog_layout_modes(viewer_window, sample_pdf):
    from foliant.modules.viewer.printing_engine import LAYOUT_BOOKLET, LAYOUT_POSTER
    view = viewer_window.open_path(sample_pdf)
    dlg = part(viewer_window, "printing").options_dialog(view)
    assert dlg.options().layout == "normal" and not dlg.overlap.isVisibleTo(dlg)
    dlg.layout_mode.setCurrentIndex(dlg.layout_mode.findData(LAYOUT_BOOKLET))
    dlg.booklet_rtl.setChecked(True)
    o = dlg.options()
    assert o.layout == LAYOUT_BOOKLET and o.booklet_rtl and not dlg.size_box.isEnabled()
    assert "pagine bianche" in dlg.mode_hint.text()
    dlg.layout_mode.setCurrentIndex(dlg.layout_mode.findData(LAYOUT_POSTER))
    dlg.overlap.setValue(15)
    o = dlg.options()
    assert o.layout == LAYOUT_POSTER and o.poster_overlap_mm == 15 and dlg.overlap.isVisibleTo(dlg)
