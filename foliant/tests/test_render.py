import pymupdf

from foliant.core.render import page_pixmap


def _orange(pix):
    return sum(1 for y in range(pix.height) for x in range(0, pix.width, 2)
               if (lambda c: c[0] > 200 and 100 < c[1] < 200 and c[2] < 100)(pix.pixel(x, y)))


def test_print_render_hides_unsigned_signature_placeholder():
    d = pymupdf.open()
    p = d.new_page(width=300, height=200)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    w.field_name = "Firma"
    w.rect = pymupdf.Rect(50, 50, 250, 120)
    p.add_widget(w)
    d = pymupdf.open("pdf", d.tobytes())
    xref = next(d[0].widgets()).xref
    before = d.xref_object(xref)
    assert _orange(page_pixmap(d, 0)) > 0                                   # a schermo: indicatore
    assert _orange(page_pixmap(d, 0, hide_unsigned_signatures=True)) == 0   # in stampa: niente
    assert _orange(page_pixmap(d, 0)) > 0                                   # documento ripristinato
    assert d.xref_object(xref) == before                                  # nessuna traccia nel file
