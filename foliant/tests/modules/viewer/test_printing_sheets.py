"""Stampa (VIS-43): fogli come escono dalla stampante (`sheets_for`), solo pari o dispari,
ordine inverso, fronte/retro con le facciate a coppie, «Cosa stampare» (documento e commenti,
solo il documento, solo campi e timbri) e il loro effetto sull'output."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter

from foliant.api import ENGINE_LOCK
from foliant.modules.viewer import printing as printing_mod
from foliant.modules.viewer.printing_engine import (
    CONTENT_ALL, CONTENT_DOCUMENT, CONTENT_FIELDS, DUPLEX_LONG, DUPLEX_SHORT, LAYOUT_BOOKLET, LAYOUT_POSTER,
    PAGES_EVEN, PAGES_ODD, SIMPLEX, PrintOptions, RangeError, describe_sheet, not_printed, parse_page_ranges,
    print_content, print_document, print_sides, require_pages, selected_pages, sheet_landscape, sheets_for,
)

from .conftest import part

BLUE = (0.1, 0.2, 0.9)
NOTE = pymupdf.Rect(400, 500, 520, 620)
FIELD = pymupdf.Rect(100, 100, 400, 140)
STAMP = pymupdf.Rect(100, 300, 400, 400)


def _pages(sheets) -> list[list[tuple]]:
    """Per ogni foglio le pagine (da 1) delle sue facciate; () = facciata bianca."""
    return [[tuple(None if p is None else p + 1 for p in side.pages) for side in sheet.sides] for sheet in sheets]


def _printer(path) -> QPrinter:
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(str(path))
    pr.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    pr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
    pr.setResolution(150)
    return pr


def _gray_pages(tmp_path, n: int) -> str:
    """Pagine riconoscibili anche nell'output raster: la pagina i è piena di grigio 30 + 20·i."""
    path = tmp_path / "grigi.pdf"
    doc = pymupdf.open()
    for i in range(n):
        page = doc.new_page()
        g = (30 + 20 * i) / 255
        page.draw_rect(page.rect, color=None, fill=(g, g, g))
    doc.save(str(path))
    return str(path)


def _printed(path) -> list[int | None]:
    """Pagina (da 1) stampata su ogni facciata dell'output, dal grigio al centro; None = bianca."""
    result = []
    for page in pymupdf.open(str(path)):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(0.2, 0.2))
        value = pix.pixel(pix.width // 2, pix.height // 2)[0]
        result.append(None if value > 240 else round((value - 30) / 20) + 1)
    return result


@pytest.fixture
def form_pdf(tmp_path):
    """Modulo: sfondo blu (contenuto), campo compilato, timbro e un commento rosso."""
    path = tmp_path / "modulo.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=BLUE)
    widget = pymupdf.Widget()
    widget.field_name, widget.field_type = "intestatario", pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect, widget.text_fontsize = FIELD, 24
    widget.fill_color = (1, 1, 1)
    widget.field_value = "MARIO ROSSI"
    page.add_widget(widget)
    page.add_stamp_annot(STAMP, stamp=0)
    note = page.add_rect_annot(NOTE)
    note.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    note.update()
    doc.save(str(path))
    doc.close()
    return str(path)


# ------------------------------------------------------------- funzioni pure
def test_odd_and_even_apply_after_the_range():
    options = PrintOptions(pages=parse_page_ranges("2-7", 10), page_set=PAGES_ODD)
    assert selected_pages(options, 10) == [2, 4, 6]               # pagine 3, 5, 7
    options.page_set = PAGES_EVEN
    assert selected_pages(options, 10) == [1, 3, 5]               # pagine 2, 4, 6
    reverse_range = PrintOptions(pages=parse_page_ranges("6-1", 10), page_set=PAGES_EVEN)
    assert selected_pages(reverse_range, 10) == [5, 3, 1]          # resta l'ordine dell'intervallo
    assert selected_pages(PrintOptions(pages=[0, 1, 2, 40]), 3) == [0, 1, 2]
    with pytest.raises(RangeError, match="pari"):
        require_pages(PrintOptions(pages=[0], page_set=PAGES_EVEN), 1)
    with pytest.raises(RangeError, match="dispari"):
        require_pages(PrintOptions(pages=[1, 3], page_set=PAGES_ODD), 5)
    with pytest.raises(RangeError, match="Nessuna pagina"):
        require_pages(PrintOptions(pages=[]), 5)


def test_one_page_per_sheet_and_reverse_order():
    options = PrintOptions(pages=list(range(4)))
    assert _pages(sheets_for(options, 4)) == [[(1,)], [(2,)], [(3,)], [(4,)]]
    options.reverse = True
    assert _pages(sheets_for(options, 4)) == [[(4,)], [(3,)], [(2,)], [(1,)]]


def test_pages_per_sheet_with_even_pages_and_reverse():
    options = PrintOptions(pages=list(range(10)), pages_per_sheet=4)
    assert _pages(sheets_for(options, 10)) == [[(1, 2, 3, 4)], [(5, 6, 7, 8)], [(9, 10)]]
    options.page_set, options.pages_per_sheet, options.reverse = PAGES_EVEN, 2, True
    # pari: 2 4 6 8 10 → fogli (2,4) (6,8) (10), poi l'ultimo foglio per primo
    assert _pages(sheets_for(options, 10)) == [[(10,)], [(6, 8)], [(2, 4)]]


def test_two_sided_pairs_sides_and_reverse_keeps_each_sheet():
    options = PrintOptions(pages=list(range(5)), duplex=DUPLEX_LONG)
    sheets = sheets_for(options, 5)
    assert _pages(sheets) == [[(1,), (2,)], [(3,), (4,)], [(5,), ()]]
    assert sheets[-1].back.blank and not sheets[0].back.blank
    options.reverse = True
    # si inverte l'ordine dei fogli, non delle facciate: il retro resta dietro il suo fronte
    assert _pages(sheets_for(options, 5)) == [[(5,), ()], [(3,), (4,)], [(1,), (2,)]]
    options.duplex = SIMPLEX
    assert _pages(sheets_for(options, 5)) == [[(5,)], [(4,)], [(3,)], [(2,)], [(1,)]]


def test_copies_repeat_whole_sheets_when_two_sided():
    options = PrintOptions(pages=list(range(3)), duplex=DUPLEX_SHORT, copies=2, collate=False)
    sides = print_sides(options, sheets_for(options, 3))
    assert [tuple(p + 1 for p in s.pages) for s in sides] == [(1,), (2,), (1,), (2,), (3,), (), (3,), ()]
    options.collate = True
    sides = print_sides(options, sheets_for(options, 3))
    # ogni copia comincia su un foglio nuovo: il retro bianco resta
    assert [tuple(p + 1 for p in s.pages) for s in sides] == [(1,), (2,), (3,), (), (1,), (2,), (3,), ()]


def test_booklet_reverse_keeps_front_and_back_together():
    options = PrintOptions(pages=list(range(8)), layout=LAYOUT_BOOKLET)
    assert _pages(sheets_for(options, 8)) == [[(8, 1), (2, 7)], [(6, 3), (4, 5)]]
    options.reverse = True
    assert _pages(sheets_for(options, 8)) == [[(6, 3), (4, 5)], [(8, 1), (2, 7)]]
    options.page_set, options.reverse = PAGES_ODD, False       # 1 3 5 7 → opuscolo di 4 pagine
    assert _pages(sheets_for(options, 8)) == [[(7, 1), (3, 5)]]


def test_poster_sheets_follow_the_tiles():
    options = PrintOptions(pages=[0, 1], layout=LAYOUT_POSTER)
    sheets = sheets_for(options, 2, tiles=lambda page: 4 if page == 0 else 1)
    assert [(s.front.pages, s.front.tile) for s in sheets] == [((0,), 0), ((0,), 1), ((0,), 2), ((0,), 3),
                                                                ((1,), 0)]
    options.reverse = True
    assert [(s.front.pages, s.front.tile) for s in sheets_for(options, 2, lambda p: 4 if p == 0 else 1)][:2] == \
        [((1,), 0), ((0,), 3)]


def test_describe_sheet_for_assistive_technologies():
    sheets = sheets_for(PrintOptions(pages=[0, 1, 2], duplex=DUPLEX_LONG), 3)
    assert describe_sheet(sheets[0]) == "fronte: pagina 1; retro: pagina 2"
    assert describe_sheet(sheets[1]) == "fronte: pagina 3; retro: facciata bianca"
    assert describe_sheet(sheets_for(PrintOptions(pages=[0, 1], pages_per_sheet=2), 2)[0]) == "pagine 1, 2"


def test_print_content_and_orientation():
    assert print_content(PrintOptions()) == CONTENT_ALL
    assert print_content(PrintOptions(annotations=False)) == CONTENT_DOCUMENT       # come prima
    assert print_content(PrintOptions(content=CONTENT_FIELDS)) == CONTENT_FIELDS
    # orientamento della carta dalla prima pagina stampata; il poster usa quello della stampante
    assert sheet_landscape(PrintOptions(), 842, 595) is True
    assert sheet_landscape(PrintOptions(layout=LAYOUT_POSTER), 842, 595) is None


def test_not_printed_keeps_fields_and_stamps_only(form_pdf):
    pdf = pymupdf.open(form_pdf)
    kinds = {xref: kind for xref, kind, _id in pdf[0].annot_xrefs()}
    assert sorted(kinds.values()) == sorted([pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_STAMP,
                                             pymupdf.PDF_ANNOT_SQUARE])

    def kept(annotations):
        hidden = set(not_printed(pdf, 0, annotations))
        return sorted(kind for xref, kind in kinds.items() if xref not in hidden)
    assert kept(True) == kept(CONTENT_ALL) == sorted(kinds.values())
    assert kept(False) == kept(CONTENT_DOCUMENT) == [pymupdf.PDF_ANNOT_WIDGET]
    assert kept(CONTENT_FIELDS) == sorted([pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_STAMP])


# ------------------------------------------------------------------ output
def test_even_pages_in_reverse_order_to_file(qapp, tmp_path):
    """«2-7», solo pari, ordine inverso: escono le pagine 6, 4, 2 in quest'ordine."""
    from foliant.core.document import Document
    doc = Document.open(_gray_pages(tmp_path, 7))
    out = tmp_path / "pari.pdf"
    printer = _printer(out)
    options = PrintOptions(pages=parse_page_ranges("2-7", 7), page_set=PAGES_EVEN, reverse=True)
    assert print_document(doc, printer, options) == 3
    del printer
    doc.close()
    assert _printed(out) == [6, 4, 2]


def test_odd_pages_four_per_sheet_to_file(qapp, tmp_path):
    from foliant.core.document import Document
    doc = Document.open(_gray_pages(tmp_path, 9))
    out = tmp_path / "dispari.pdf"
    printer = _printer(out)
    # dispari 1 3 5 7 9 → due fogli; il secondo ha solo la pagina 9 (in alto a sinistra)
    assert print_document(doc, printer, PrintOptions(pages=list(range(9)), page_set=PAGES_ODD,
                                                      pages_per_sheet=4, reverse=True)) == 2
    del printer
    doc.close()
    first = pymupdf.open(str(out))[0]
    pix = first.get_pixmap(matrix=pymupdf.Matrix(0.2, 0.2))
    value = pix.pixel(pix.width // 4, pix.height // 4)[0]
    assert round((value - 30) / 20) + 1 == 9            # ordine inverso: prima il foglio con la 9
    assert pix.pixel(3 * pix.width // 4, 3 * pix.height // 4)[0] > 240


def test_two_sided_reverse_prints_blank_back_first(qapp, tmp_path):
    from foliant.core.document import Document
    doc = Document.open(_gray_pages(tmp_path, 3))
    out = tmp_path / "fronteretro.pdf"
    printer = _printer(out)
    assert print_document(doc, printer, PrintOptions(pages=[0, 1, 2], duplex=DUPLEX_LONG, reverse=True)) == 4
    del printer
    doc.close()
    assert _printed(out) == [3, None, 1, 2]


def test_two_sided_from_the_system_dialog_pairs_the_sides():
    """Il fronte/retro scelto nel dialogo del sistema decide le coppie di facciate; per un file
    PDF resta la scelta fatta in Foliant (quella dell'anteprima)."""
    class Printer:          # stampante simulata: formato di uscita e fronte/retro del dialogo
        def __init__(self, fmt, duplex):
            self.fmt, self.mode = fmt, duplex

        def outputFormat(self):
            return self.fmt

        def duplex(self):
            return self.mode
    native, pdf = QPrinter.OutputFormat.NativeFormat, QPrinter.OutputFormat.PdfFormat
    assert printing_mod.printer_duplex(Printer(native, QPrinter.DuplexMode.DuplexLongSide), "") == DUPLEX_LONG
    assert printing_mod.printer_duplex(Printer(native, QPrinter.DuplexMode.DuplexNone), DUPLEX_SHORT) == SIMPLEX
    assert printing_mod.printer_duplex(Printer(native, QPrinter.DuplexMode.DuplexAuto), DUPLEX_SHORT) == DUPLEX_SHORT
    assert printing_mod.printer_duplex(Printer(pdf, QPrinter.DuplexMode.DuplexNone), DUPLEX_LONG) == DUPLEX_LONG


def test_two_sided_copies_to_pdf_file(viewer_window, tmp_path, monkeypatch):
    """Fronte/retro con 2 copie fascicolate su file PDF: ogni copia comincia su un foglio nuovo."""
    view = viewer_window.open_path(_gray_pages(tmp_path, 3))
    ctl = part(viewer_window, "printing")
    out = tmp_path / "copie.pdf"
    monkeypatch.setattr(ctl, "ask_options", lambda v: PrintOptions(pages=[0, 1, 2], copies=2, collate=True,
                                                                    duplex=DUPLEX_LONG))
    monkeypatch.setattr(ctl, "ask_printer", lambda v, o: _printer(out))
    assert ctl.print_current() == 8                    # 2 copie × 2 fogli × 2 facciate
    assert _printed(out) == [1, 2, 3, None, 1, 2, 3, None]
    assert not view.document.is_modified


def _ink(page, clip) -> int:
    """Pixel scuri (testo) nel riquadro."""
    pix = page.get_pixmap(clip=clip)
    return sum(1 for i in range(0, len(pix.samples), pix.n) if max(pix.samples[i:i + 3]) < 100)


def _has(page, clip, test) -> bool:
    pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(0.5, 0.5))
    return any(test(pix.samples[i:i + 3]) for i in range(0, len(pix.samples), pix.n))


def _red(rgb) -> bool:
    return rgb[0] > 180 and rgb[1] < 90 and rgb[2] < 90


def _blue(rgb) -> bool:
    return rgb[2] > 150 and rgb[0] < 90


@pytest.mark.parametrize("pages_per_sheet", [1, 2])
def test_fields_and_stamps_only_for_preprinted_forms(viewer_window, form_pdf, tmp_path, monkeypatch,
                                                     pages_per_sheet):
    """«Solo campi e timbri»: escono il valore del campo e il timbro, non il contenuto della
    pagina né i commenti; anche con più pagine per foglio. Il documento non cambia."""
    view = viewer_window.open_path(form_pdf)
    ctl = part(viewer_window, "printing")
    out = tmp_path / f"campi{pages_per_sheet}.pdf"
    monkeypatch.setattr(ctl, "ask_options", lambda v: PrintOptions(pages=[0], content=CONTENT_FIELDS,
                                                                    annotations=False,
                                                                    pages_per_sheet=pages_per_sheet,
                                                                    auto_rotate=False))
    monkeypatch.setattr(ctl, "ask_printer", lambda v, o: _printer(out))
    assert ctl.print_current() == 1
    page = pymupdf.open(str(out))[0]
    whole = page.rect
    assert not _has(page, whole, _blue)                         # niente sfondo del documento
    if pages_per_sheet == 1:
        assert _ink(page, FIELD) > 50                           # valore del campo
        assert _has(page, STAMP, _red)                          # timbro
        assert not _has(page, NOTE, _red)                       # commento
    else:
        assert _has(page, whole, _red)                          # timbro, rimpicciolito nella cella
    assert not view.document.is_modified
    with ENGINE_LOCK:
        assert view.document.pdf[0].get_contents()              # il contenuto resta nel documento


def test_document_only_and_document_with_comments(viewer_window, form_pdf, tmp_path, monkeypatch):
    view = viewer_window.open_path(form_pdf)
    ctl = part(viewer_window, "printing")
    for content, note in ((CONTENT_DOCUMENT, False), (CONTENT_ALL, True)):
        out = tmp_path / f"{content}.pdf"
        monkeypatch.setattr(ctl, "ask_options", lambda v, c=content: PrintOptions(pages=[0], content=c))
        monkeypatch.setattr(ctl, "ask_printer", lambda v, o, path=out: _printer(path))
        assert ctl.print_current() == 1
        page = pymupdf.open(str(out))[0]
        assert _has(page, pymupdf.Rect(10, 10, 60, 60), _blue)          # sfondo del documento
        assert _ink(page, FIELD) > 50                                   # campo
        assert _has(page, NOTE, _red) is note                          # commento
        assert _has(page, STAMP, _red) is note                         # timbro (è un'annotazione)
    assert not view.document.is_modified
