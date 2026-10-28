"""SEC-24 Appiattimento (con le regole di ANN-40 e FRM-32)."""
from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.core.document import Document
from foliant.modules.security import flatten as F
from tests.test_document import _sign, test_signer  # noqa: F401  (fixture del firmatario)


def _render(page, dpi=150):
    pix = page.get_pixmap(dpi=dpi, annots=True, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)


def _within_tolerance(a, b) -> bool:
    """Tolleranza dell'appiattimento (ANN-40): al massimo lo 0,5 % dei pixel con uno
    scarto oltre 8/255 in almeno un canale."""
    assert a.shape == b.shape
    bad = (np.abs(a - b).max(axis=2) > 8).sum()
    return bad <= 0.005 * a.shape[0] * a.shape[1]


def _commented(n_comments=20, rotation=0):
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 60), "Contratto di compravendita", fontsize=14)
    for i in range(n_comments):
        x, y = 72 + (i % 5) * 95, 100 + (i // 5) * 70
        kind = i % 4
        if kind == 0:
            a = page.add_rect_annot((x, y, x + 60, y + 40))
            a.set_colors(stroke=(1, 0, 0))
        elif kind == 1:
            a = page.add_freetext_annot((x, y, x + 85, y + 20), f"Totale {100 + i}", fontsize=9)
        elif kind == 2:
            a = page.add_line_annot((x, y), (x + 60, y + 40))
        else:
            a = page.add_circle_annot((x, y, x + 40, y + 40))
            a.set_colors(stroke=(0, 0, 1), fill=(0.9, 0.9, 1))
        a.update()
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 420, 200, 440), "uri": "https://example.org"})
    for i, (name, value) in enumerate((("acquirente", "Mario Rossi"), ("prezzo", "9.500"))):
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = pymupdf.Rect(72, 470 + 30 * i, 300, 490 + 30 * i)
        w.field_value = value
        page.add_widget(w)
    cb = pymupdf.Widget()
    cb.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    cb.field_name = "garanzia"
    cb.rect = pymupdf.Rect(72, 540, 90, 558)
    cb.field_value = True
    page.add_widget(cb)
    if rotation:
        page.set_rotation(rotation)
    return d


@pytest.mark.parametrize("rotation", [0, 90])
def test_flatten_everything_matches_render(tmp_path, rotation):
    """Dato 20 commenti e un modulo compilato, quando si appiattisce tutto e si salva, allora
    le pagine non hanno /Annots salvo i collegamenti, niente /AcroForm, rendering entro la
    tolleranza."""
    d = _commented(rotation=rotation)
    before = _render(d[0])
    rep = F.flatten(d, F.FlattenOptions())
    assert rep.annots == 20 and rep.widgets == 3
    out = tmp_path / "piatto.pdf"
    d.save(out, garbage=3)
    pdf = pymupdf.open(out)
    page = pdf[0]
    assert [t for _x, t, _n in page.annot_xrefs()] == [pymupdf.PDF_ANNOT_LINK]
    assert pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")[0] == "null"
    assert _within_tolerance(before, _render(page))
    text = page.get_text()
    assert "Totale 101" in text and "Mario Rossi" in text   # il testo resta reale


def test_flatten_links_not_kept_on_request():
    d = _commented(2)
    F.flatten(d, F.FlattenOptions(keep_links=False))
    assert d[0].annot_xrefs() == []


def test_hidden_and_non_printing_annotations():
    """Annotazione Hidden o senza Print: con l'opzione predefinita resta annotazione e non
    entra nel contenuto; con «elimina» non c'è più."""
    for hidden in (F.HIDDEN_KEEP, F.HIDDEN_DELETE):
        d = pymupdf.open()
        page = d.new_page()
        h = page.add_freetext_annot((72, 72, 300, 100), "NASCOSTA", fontsize=12)
        h.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
        h.update()
        n = page.add_freetext_annot((72, 150, 300, 180), "NONSTAMPA", fontsize=12)
        n.set_flags(0)
        n.update()
        rep = F.flatten(d, F.FlattenOptions(hidden=hidden))
        page = d[0]
        content = b"".join(d.xref_stream(x) for x in page.get_contents())
        assert b"FolFlat" not in content and rep.annots == 0
        assert b"NASCOSTA" not in content and b"NONSTAMPA" not in content
        assert len(page.annot_xrefs()) == (2 if hidden == F.HIDDEN_KEEP else 0)


def test_unapplied_redaction_marks_are_never_baked():
    """Un segno di redazione non applicato non deve diventare un riquadro fuso con il testo
    ancora presente sotto: resta un segno."""
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 100), "IBAN IT60X0542811101000000123456", fontsize=11)
    page.add_redact_annot(pymupdf.Rect(70, 88, 400, 104), fill=(0, 0, 0))
    page.add_rect_annot((72, 200, 120, 240)).update()
    F.flatten(d, F.FlattenOptions())
    page = d[0]
    assert [t for _x, t, _n in page.annot_xrefs()] == [pymupdf.PDF_ANNOT_REDACT]


def test_notes_and_replies(tmp_path):
    d = pymupdf.open()
    page = d.new_page()
    note = page.add_text_annot((100, 100), "Verificare la matricola")
    reply = page.add_text_annot((120, 100), "Fatto")
    d.xref_set_key(reply.xref, "IRT", f"{note.xref} 0 R")
    rep = F.flatten(d, F.FlattenOptions())
    assert rep.notes_lost == 1 and rep.warnings
    assert d[0].annot_xrefs() == []


def test_signed_signature_excluded_by_default(test_signer):  # noqa: F811
    d = pymupdf.open()
    page = d.new_page()
    page.add_rect_annot((72, 300, 200, 350)).update()
    pdf = pymupdf.open("pdf", _sign(d.tobytes(), test_signer))
    F.flatten(pdf, F.FlattenOptions())
    sigs = [w for w in pdf[0].widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE]
    assert sigs and pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")[0] != "null"
    F.flatten(pdf, F.FlattenOptions(signatures=True))
    assert not list(pdf[0].widgets())


def test_page_range_only():
    d = pymupdf.open()
    for _i in range(3):
        d.new_page().add_rect_annot((72, 72, 150, 150)).update()
    F.flatten(d, F.FlattenOptions(pages=[1]))
    assert [len(p.annot_xrefs()) for p in d] == [1, 0, 1]


def _layered():
    d = pymupdf.open()
    page = d.new_page()
    hidden = d.add_ocg("Prezzi riservati", on=False)
    shown = d.add_ocg("Testo", on=True)
    page.insert_text((72, 100), "PREZZO-ACQUISTO 4200", fontsize=12, oc=hidden)
    page.insert_text((72, 140), "Descrizione visibile", fontsize=12, oc=shown)
    page.insert_text((72, 180), "Sempre", fontsize=12)
    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    img.clear_with(0)
    page.insert_image(pymupdf.Rect(300, 300, 340, 340), pixmap=img, oc=hidden)
    return d


def test_flatten_layers(tmp_path):
    """Dato un livello nascosto con testo, quando si appiattiscono i livelli, allora quel
    testo non c'è più e il documento non ha /OCProperties."""
    d = _layered()
    before = _render(d[0])
    rep = F.flatten(d, F.FlattenOptions(layers=True, annots=False, widgets=False))
    assert rep.layer_sections >= 2
    out = tmp_path / "livelli.pdf"
    d.save(out, garbage=3)
    pdf = pymupdf.open(out)
    page = pdf[0]
    raw = b"".join(pdf.xref_stream(x) for x in page.get_contents())
    assert b"PREZZO" not in raw and "PREZZO" not in page.get_text()
    assert "Descrizione visibile" in page.get_text() and "Sempre" in page.get_text()
    assert pdf.xref_get_key(pdf.pdf_catalog(), "OCProperties")[0] == "null"
    assert not page.get_images()
    assert _within_tolerance(before, _render(page))


def test_strip_hidden_sections_lexer():
    data = (b"q (a\\) BDC EMC) Tj /OC /oc1 BDC BT (x) Tj ET /Span <</MCID 1>> BDC (y) Tj EMC EMC "
            b"/OC /oc2 BDC (z) Tj EMC BI /W 1 /H 1 ID \x00EMC\xff EI /Im1 Do Q")
    out, n = F.strip_hidden_sections(data, lambda name: name == "oc1", lambda name: name == "Im1")
    assert n == 2
    assert out.startswith(b"q (a\\) BDC EMC) Tj") and b"(x)" not in out and b"(y)" not in out
    assert b"/OC /oc2 BDC (z) Tj EMC" in out and b"ID \x00EMC\xff EI" in out and out.rstrip().endswith(b"Q")


@pytest.mark.parametrize("mediabox", [None, (50, 30, 650, 830)])
def test_flatten_crop(tmp_path, mediabox):
    """Dato una pagina ritagliata con testo fuori dal CropBox, quando si appiattisce il
    ritaglio e si salva, allora il testo esterno non è estraibile e /MediaBox coincide con
    il CropBox precedente."""
    d = pymupdf.open()
    page = d.new_page(width=600, height=800)
    if mediabox:
        d.xref_set_key(page.xref, "MediaBox", "[%g %g %g %g]" % mediabox)
        page = d.reload_page(page)
    page.insert_text((20, 30), "ESTERNO", fontsize=12)
    page.insert_text((200, 400), "INTERNO", fontsize=12)
    page.add_rect_annot((5, 5, 40, 40)).update()               # interamente fuori
    crop = pymupdf.Rect(100, 100, 500, 700)
    page.set_cropbox(crop)
    before = _render(d[0])
    old = d.xref_get_key(d[0].xref, "CropBox")[1]
    F.flatten(d, F.FlattenOptions(crop=True, annots=False, widgets=False))
    out = tmp_path / "ritaglio.pdf"
    d.save(out, garbage=3)
    pdf = pymupdf.open(out)
    page = pdf[0]
    every = page.get_text(clip=pymupdf.Rect(-5000, -5000, 5000, 5000))
    assert "ESTERNO" not in every and "INTERNO" in every
    nums = lambda s: [float(v) for v in s.strip("[]").split()]   # noqa: E731
    assert nums(pdf.xref_get_key(page.xref, "MediaBox")[1]) == nums(old)
    assert pdf.xref_get_key(page.xref, "CropBox")[0] == "null"
    assert page.annot_xrefs() == []
    assert _within_tolerance(before, _render(page))


def test_crop_and_rasterize_refuse_pending_marks():
    d = pymupdf.open()
    page = d.new_page()
    page.add_redact_annot(pymupdf.Rect(10, 10, 50, 50))
    page.set_cropbox(pymupdf.Rect(0, 0, 300, 300))
    with pytest.raises(F.PendingMarks):
        F.flatten(d, F.FlattenOptions(crop=True))
    with pytest.raises(F.PendingMarks):
        F.flatten(d, F.FlattenOptions(rasterize=True))


def test_foliant_watermark_flattened(tmp_path):
    """Dato una filigrana aggiunta da Foliant, quando si appiattiscono le filigrane, allora
    resta visibile ma il comando di rimozione non la trova più."""
    from foliant.modules.pages import stamp
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Listino riservato", fontsize=12)
    stamp.add_watermark(d, text="RISERVATO")
    assert stamp.list_watermarks(d)
    before = _render(d[0])
    rep = F.flatten(d, F.FlattenOptions(annots=False, widgets=False))
    assert rep.stamps == 1
    assert stamp.list_watermarks(d) == {}
    assert stamp.remove_watermarks(d) == 0
    assert _within_tolerance(before, _render(d[0]))
    assert not any(name.startswith("Foliant") for name in
                   (i.get("name", "") for i in (d.get_ocgs() or {}).values()))


def test_rasterize_pages(tmp_path):
    for rotation in (0, 90):
        d = _commented(4, rotation=rotation)
        before = _render(d[0], dpi=72)
        rep = F.flatten(d, F.FlattenOptions(rasterize=True, dpi=150))
        page = d[0]
        assert rep.rasterized == 1 and rep.warnings
        assert page.get_text().strip() == ""
        assert len(page.get_images()) == 1
        assert [t for _x, t, _n in page.annot_xrefs()] == [pymupdf.PDF_ANNOT_LINK]
        assert page.rotation == rotation
        a, b = before, _render(page, dpi=72)
        assert a.shape == b.shape
        assert (np.abs(a - b).max(axis=2) > 48).mean() < 0.02


def test_flatten_undo_restores(sample_pdf, tmp_path):
    """Dato un appiattimento eseguito e salvato, Annulla riporta le annotazioni e il
    documento risulta modificato."""
    path = tmp_path / "c.pdf"
    _commented(3).save(path)
    doc = Document.open(path)
    with doc.change("Appiattisci", kind="redaction") as pdf:
        F.flatten(pdf, F.FlattenOptions())
    doc.save(tmp_path / "salvato.pdf")
    doc.undo()
    with doc.lock:
        assert len(doc.pdf[0].annot_xrefs()) == 3 + 1 + 3
    assert doc.is_modified
    doc.close()


def test_preview_does_not_touch_document():
    d = _layered()
    d[0].add_rect_annot((72, 400, 200, 450)).update()
    pix = F.preview(d, F.FlattenOptions(layers=True), 0, width=200)
    assert pix.width == 200
    assert d.xref_get_key(d.pdf_catalog(), "OCProperties")[0] != "null"
    assert len(d[0].annot_xrefs()) == 1
