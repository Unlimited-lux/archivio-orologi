"""Motore del confronto: criteri di accettazione CMP-01…CMP-06 (logica pura)."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.api import DocumentError, OperationCancelled, PasswordRequired, WrongPassword
from foliant.modules.tools import compare_engine as ce

from .conftest import MM, page_lines, read


def run(a, b, **opts):
    pw_a = opts.pop("pw_a", None)
    pw_b = opts.pop("pw_b", None)
    options = ce.CompareOptions(**opts)
    return ce.run_compare(None, ce.SideInput(read(a), "A", password=pw_a),
                          ce.SideInput(read(b), "B", password=pw_b), options)


def kinds(result):
    return [d.kind for d in result.differences]


# ----------------------------------------------------------------- CMP-01
def test_same_file_has_no_differences(pdf_builder):
    a = pdf_builder([page_lines(1), page_lines(2)])
    r = run(a, a)
    assert r.identical_files
    assert r.differences == []


def test_swap_turns_additions_into_deletions(pdf_builder):
    a = pdf_builder([page_lines(1), ["Riga comune", "Testo solo nuovo"]])
    b = pdf_builder([page_lines(1), ["Riga comune"]])
    ab = run(a, b, mode=ce.MODE_TEXT)
    ba = run(b, a, mode=ce.MODE_TEXT)
    assert kinds(ab) == [ce.DELETED]
    assert kinds(ba) == [ce.ADDED]
    assert ab.differences[0].text_a == ba.differences[0].text_b == "Testo solo nuovo"


def test_encrypted_side_requires_password(pdf_builder, encrypted_pdf):
    a = pdf_builder([["Riservato"]])
    with pytest.raises(PasswordRequired):
        run(a, encrypted_pdf)
    with pytest.raises(WrongPassword):
        run(a, encrypted_pdf, pw_b="sbagliata")
    r = run(a, encrypted_pdf, pw_b="segreta", mode=ce.MODE_TEXT)
    assert r.differences == []


def test_non_pdf_is_rejected(tmp_path, pdf_builder):
    a = pdf_builder([["x"]])
    txt = tmp_path / "nota.txt"
    txt.write_text("ciao")
    with pytest.raises(DocumentError, match="Convertilo"):
        run(a, txt)


def test_damaged_pdf_gives_clear_error(tmp_path, pdf_builder):
    a = pdf_builder([["x"]])
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 20)
    with pytest.raises(DocumentError):
        run(a, bad)


def test_cancel_stops_comparison(pdf_builder):
    a = pdf_builder([page_lines(i) for i in range(6)])
    b = pdf_builder([page_lines(i + 1) for i in range(6)])

    class Cancelling:
        calls = 0

        def update(self, fraction=None, text=None):
            self.calls += 1
            if self.calls > 3:
                raise OperationCancelled()

        def check(self):
            pass

    with pytest.raises(OperationCancelled):
        ce.run_compare(Cancelling(), ce.SideInput(read(a), "A"), ce.SideInput(read(b), "B"))


def test_page_range_limits_comparison(pdf_builder):
    a = pdf_builder([page_lines(1), page_lines(2), page_lines(3)])
    b = pdf_builder([page_lines(1), page_lines(2), page_lines(7)])
    r = ce.run_compare(None, ce.SideInput(read(a), "A", page_range=(0, 1)),
                       ce.SideInput(read(b), "B", page_range=(0, 1)), ce.CompareOptions())
    assert r.differences == []
    assert r.side_a.pages == [0, 1]


# ----------------------------------------------------------------- CMP-02
def test_modified_number_is_one_modification(pdf_builder):
    a = pdf_builder([page_lines(1), ["Il canone è di 100 euro"]])
    b = pdf_builder([page_lines(1), ["Il canone è di 120 euro"]])
    r = run(a, b)
    assert len(r.differences) == 1
    d = r.differences[0]
    assert d.kind == ce.MODIFIED and d.text_a == "100" and d.text_b == "120"
    assert d.pages("a") == [1] and d.pages("b") == [1]
    assert d.detail == "1[0→2]0"


def test_inserted_page_only_additions_on_that_page(pdf_builder):
    a = pdf_builder([page_lines(1), page_lines(2), page_lines(3), page_lines(4)])
    b = pdf_builder([page_lines(1), page_lines(2), page_lines(9), page_lines(3), page_lines(4)])
    r = run(a, b)
    assert r.differences
    assert all(d.kind == ce.ADDED for d in r.differences)
    assert all(d.pages("b") == [2] for d in r.differences)
    assert (2, 3) in r.pairs and (3, 4) in r.pairs


def test_ignore_case(pdf_builder):
    a = pdf_builder([["Contratto di Vendita tra le parti"]])
    b = pdf_builder([["contratto di vendita TRA le parti"]])
    assert run(a, b, mode=ce.MODE_TEXT, ignore_case=True).differences == []
    r = run(a, b, mode=ce.MODE_TEXT)
    assert r.differences and all(d.kind == ce.MODIFIED for d in r.differences)
    changed = " ".join(d.text_a for d in r.differences).split()
    assert sorted(changed) == ["Contratto", "Vendita", "tra"]


def test_ignore_punctuation_and_whitespace(pdf_builder):
    a = pdf_builder([["Prezzo: mille euro, pagamento a vista"]])
    b = pdf_builder([["Prezzo mille euro pagamento a vista"]])
    assert run(a, b, mode=ce.MODE_TEXT, ignore_punctuation=True).differences == []
    assert run(a, b, mode=ce.MODE_TEXT).differences
    c = pdf_builder([["un orologio auto matico"]])
    d = pdf_builder([["un orologio automatico"]])
    assert run(c, d, mode=ce.MODE_TEXT, ignore_whitespace=True).differences == []
    assert run(c, d, mode=ce.MODE_TEXT).differences


def test_hyphenation_and_ligatures(pdf_builder):
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def lig(page):
        page.insert_text((72, 90), "Firma del contrat-", fontsize=12)
        page.insert_text((72, 110), "to di vendita", fontsize=12)
        page.insert_text((72, 130), "ufﬁcio centrale", fontsize=12, fontname="dejavu", fontfile=font)
    a = pdf_builder([lig])
    b = pdf_builder([["Firma del contratto di vendita", "ufficio centrale"]])
    assert run(a, b, mode=ce.MODE_TEXT).differences == []


def test_reflow_across_pages_is_not_a_difference(pdf_builder):
    lines = [f"riga numero {i} del testo continuo" for i in range(12)]
    a = pdf_builder([lines[:6], lines[6:]])
    b = pdf_builder([lines[:4], lines[4:]])
    assert run(a, b, mode=ce.MODE_TEXT).differences == []


def test_unrelated_documents_warning(pdf_builder):
    a = pdf_builder([["alfa beta gamma delta epsilon zeta eta theta"]])
    b = pdf_builder([["uno due tre quattro cinque sei sette otto"]])
    r = run(a, b, mode=ce.MODE_TEXT)
    assert any("non correlati" in w for w in r.warnings)


def test_extraction_forbidden_falls_back_to_visual(tmp_path, pdf_builder):
    path = tmp_path / "noestrai.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Testo protetto", fontsize=12)
    perms = pymupdf.PDF_PERM_PRINT
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="", permissions=perms)
    doc.close()
    a = pdf_builder([["Testo protetto"]])
    with pytest.raises(DocumentError, match="estrazione"):
        run(a, path, mode=ce.MODE_TEXT, pw_b="")
    r = run(a, path, pw_b="")
    assert any("estrazione" in w for w in r.warnings)


# ----------------------------------------------------------------- CMP-03
def _bold_page(bold_word: bool, size=12):
    def draw(page):
        page.insert_text((72, 100), "Orologio", fontsize=size, fontname="helv")
        page.insert_text((150, 100), "svizzero", fontsize=12, fontname="hebo" if bold_word else "helv")
    return draw


def test_format_difference_bold(pdf_builder):
    a = pdf_builder([_bold_page(False)])
    b = pdf_builder([_bold_page(True)])
    assert run(a, b, mode=ce.MODE_TEXT).differences == []
    r = run(a, b, mode=ce.MODE_TEXT, compare_format=True)
    assert len(r.differences) == 1
    d = r.differences[0]
    assert d.kind == ce.FORMAT and d.text_b == "svizzero" and "stile" in d.detail


def test_format_difference_size(pdf_builder):
    a = pdf_builder([_bold_page(False, size=14)])
    b = pdf_builder([_bold_page(False, size=16)])
    r = run(a, b, mode=ce.MODE_TEXT, compare_format=True)
    assert [d.detail for d in r.differences] == ["dimensione 14 → 16 pt"]


def test_subset_prefix_is_ignored():
    assert ce._font_family("ABCDEF+Helvetica") == ce._font_family("XYZABC+Helvetica")


# ----------------------------------------------------------------- CMP-04
def _rect_on_page3(page, i):
    if i == 2:
        x, y = 300, 400
        page.draw_rect(pymupdf.Rect(x, y, x + 10 * MM, y + 10 * MM), color=(0, 0, 0), fill=(0, 0, 0))


@pytest.mark.parametrize("mode", [ce.MODE_VISUAL, ce.MODE_AUTO])
def test_visual_rectangle_detected(pdf_builder, mode):
    pages = [page_lines(1), page_lines(2), page_lines(3)]
    a = pdf_builder(pages)
    b = pdf_builder(pages, extra=_rect_on_page3)
    r = run(a, b, mode=mode, dpi=100)
    assert len(r.differences) == 1
    d = r.differences[0]
    assert d.kind == ce.VISUAL and d.pages("b") == [2]
    _p, x0, y0, x1, y1 = d.rects_b[0]
    target = (300, 400, 300 + 10 * MM, 400 + 10 * MM)
    assert all(abs(u - v) <= MM for u, v in zip((x0, y0, x1, y1), target))


def test_antialiasing_is_ignored_at_medium(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Cronografo automatico in acciaio", fontsize=18)
    page.draw_circle((300, 400), 80, color=(0, 0, 0), width=2)
    page.draw_line((50, 600), (500, 650), width=1.3)
    pymupdf.TOOLS.set_aa_level(8)
    a = np.frombuffer(page.get_pixmap(dpi=150).samples, np.uint8)
    pix = page.get_pixmap(dpi=150)
    pymupdf.TOOLS.set_aa_level(0)
    pix0 = page.get_pixmap(dpi=150)
    pymupdf.TOOLS.set_aa_level(8)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    b = np.frombuffer(pix0.samples, np.uint8).reshape(pix0.height, pix0.width, 3)
    assert (a != b).any()
    s = ce.SENSITIVITY["medium"]
    mask = ce.diff_mask(a, b, s["tolerance"])
    assert ce.regions_from_mask(mask, s["cell_pixels"], s["min_cells"]) == []


def _scan(tmp_path, name, offset_mm, mark=False):
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((72, 100), "Perizia orologio", fontsize=20)
    p.draw_rect(pymupdf.Rect(100, 200, 400, 300), color=(0, 0, 0), width=3)
    p.draw_line((80, 500), (520, 520), width=2)
    img = p.get_pixmap(dpi=150).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page()
    o = offset_mm * MM
    page.insert_image(pymupdf.Rect(o, 0, page.rect.width + o, page.rect.height), stream=img)
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


def test_shifted_scans_aligned(tmp_path):
    a = _scan(tmp_path, "s1.pdf", 0)
    b = _scan(tmp_path, "s2.pdf", 3)
    r = run(a, b, mode=ce.MODE_VISUAL)
    assert r.differences == []
    assert not any("disallineate" in w for w in r.warnings)
    r = run(a, b, mode=ce.MODE_VISUAL, auto_align=False)
    assert any("disallineate" in w for w in r.warnings)


def test_page_size_reported_once(pdf_builder):
    a = pdf_builder([["Formato"]], size=(595, 842))
    b = pdf_builder([["Formato"]], size=(612, 792))
    r = run(a, b, mode=ce.MODE_VISUAL, dpi=72)
    sizes = [d for d in r.differences if d.subtype == "page_size"]
    assert len(sizes) == 1


def test_manual_pairing_recomputes_only_those_pages(pdf_builder):
    a = pdf_builder([page_lines(1), page_lines(2), page_lines(3)])
    b = pdf_builder([page_lines(1), page_lines(2), page_lines(3)], extra=_rect_on_page3)
    r = run(a, b, mode=ce.MODE_VISUAL, dpi=72)
    before = [d for d in r.differences if d.pages("a") != [2]]
    called = []
    orig = ce.compare_visual_pages

    def spy(doc_a, pa, doc_b, pb, *args, **kw):
        called.append((pa, pb))
        return orig(doc_a, pa, doc_b, pb, *args, **kw)

    import unittest.mock
    with unittest.mock.patch.object(ce, "compare_visual_pages", spy):
        ce.recompute_pair(r, read(a), read(b), 1, 2)
    assert called == [(1, 2)]
    assert (1, 2) in r.pairs
    assert any(d.pages("a") == [1] and d.kind == ce.VISUAL for d in r.differences)
    assert [x.id for x in r.differences] == list(range(1, len(r.differences) + 1))
    assert before == [d for d in before]


def test_page_only_in_b_without_text(pdf_builder):
    def picture(page):
        page.draw_rect(pymupdf.Rect(100, 100, 300, 300), fill=(0.2, 0.2, 0.2))
    a = pdf_builder([page_lines(1), page_lines(2)])
    b = pdf_builder([page_lines(1), picture, page_lines(2)])
    r = run(a, b, dpi=72)
    assert [(d.kind, d.subtype) for d in r.differences] == [(ce.ADDED, "page")]
    assert r.differences[0].pages("b") == [1]


# ----------------------------------------------------------------- CMP-05
def test_sync_mapping_skips_inserted_pages(pdf_builder):
    a = pdf_builder([page_lines(i) for i in range(1, 7)])
    b = pdf_builder([page_lines(i) for i in (1, 2, 3)] + [page_lines(20), page_lines(21)]
                    + [page_lines(i) for i in (4, 5, 6)])
    r = run(a, b, mode=ce.MODE_TEXT)
    # inizio di pagina 4 di A -> pagina 6 di B
    for frac in (0.0, 0.3, 0.6):
        assert int(r.map_a_to_b(3 + frac)) == 5
    assert int(r.map_b_to_a(5.3)) == 3
    # mentre B scorre il blocco inserito, A resta sul punto di inserimento
    assert abs(r.map_b_to_a(3.5) - r.map_b_to_a(4.5)) < 0.01


# ----------------------------------------------------------------- CMP-06
def _img(draw, size=(200, 200)):
    img = np.full((size[1], size[0], 3), 255, np.uint8)
    draw(img)
    return img


def test_overlay_identical_has_no_pure_colors():
    def draw(img):
        img[50:60, 20:180] = 0
    a = _img(draw)
    out = ce.overlay_composite(a, a.copy())
    red = (out[:, :, 0] > 150) & (out[:, :, 1] < 100)
    blue = (out[:, :, 2] > 150) & (out[:, :, 0] < 100)
    assert not red.any() and not blue.any()
    assert (out[55, 100] == 40).all()


def test_overlay_moved_line_shows_both_colors():
    a = _img(lambda im: im.__setitem__((slice(50, 53), slice(20, 180)), 0))
    b = _img(lambda im: im.__setitem__((slice(62, 65), slice(20, 180)), 0))
    out = ce.overlay_composite(a, b)
    assert tuple(out[51, 100]) == (220, 38, 38)
    assert tuple(out[63, 100]) == (37, 99, 235)


def test_overlay_opacity_zero_shows_only_b():
    a = _img(lambda im: im.__setitem__((slice(10, 20), slice(10, 20)), 0))
    b = _img(lambda im: im.__setitem__((slice(100, 110), slice(10, 20)), 0))
    out = ce.overlay_composite(a, b, opacity_a=0.0)
    assert (out[15, 15] == 255).all()
    assert tuple(out[105, 15]) == (37, 99, 235)


def test_overlay_manual_shift():
    a = _img(lambda im: im.__setitem__((slice(10, 20), slice(10, 20)), 0))
    out = ce.overlay_composite(a, a.copy(), shift_b=(30, 0))
    assert tuple(out[15, 15]) == (220, 38, 38)
    assert tuple(out[15, 45]) == (37, 99, 235)


def test_session_roundtrip_keeps_offsets(pdf_builder):
    a = pdf_builder([page_lines(1)])
    b = pdf_builder([page_lines(2)])
    r = run(a, b, mode=ce.MODE_TEXT)
    data = ce.session_to_dict(r, {(0, 0): (3 * MM, 0.0)})
    import json
    back = json.loads(json.dumps(data))
    assert ce.parse_offsets(back) == {(0, 0): (3 * MM, 0.0)}
    assert ce.CompareOptions.from_dict(back["options"]) == r.options


# ----------------------------------------------------------------- CMP-08
def _footer(date):
    def extra(page, i):
        page.insert_text((72, 810), f"Stampato il {date}", fontsize=9)
        page.insert_text((500, 810), f"Pagina {i + 1}", fontsize=9)
    return extra


def test_ignore_headers_footers(pdf_builder):
    pages = [page_lines(1), page_lines(2), page_lines(3), page_lines(4)]
    a = pdf_builder(pages, extra=_footer("27/10/2026"))
    b = pdf_builder(pages, extra=_footer("28/10/2026"))
    assert run(a, b).differences
    assert run(a, b, ignore_headers_footers=True).differences == []


def test_ignore_page_numbers(pdf_builder):
    def num(offset):
        return lambda page, i: page.insert_text((290, 820), f"- {i + 1 + offset} -", fontsize=9)
    pages = [page_lines(1), page_lines(2)]
    a = pdf_builder(pages, extra=num(0))
    b = pdf_builder(pages, extra=num(4))
    assert run(a, b, mode=ce.MODE_TEXT).differences
    assert run(a, b, mode=ce.MODE_TEXT, ignore_page_numbers=True).differences == []


def test_exclusion_area_on_all_pages(pdf_builder):
    def stamp(date):
        def extra(page, i):
            page.insert_text((420, 760), f"Rev. {date}", fontsize=10)
            page.draw_rect(pymupdf.Rect(410, 740, 560, 780), color=(0, 0, 0), width=1 if date == "A" else 2.5)
        return extra
    pages = [page_lines(1), page_lines(2)]
    a = pdf_builder(pages, extra=stamp("A"))
    b = pdf_builder(pages, extra=stamp("B"))
    assert run(a, b, dpi=72).differences
    r = run(a, b, dpi=72, exclusions=[[-1, 400, 730, 570, 790]])
    assert r.differences == []
    assert any("Aree escluse" in c for c in r.options.criteria())


# ----------------------------------------------------------------- CMP-09
def test_moved_page_reported_once_without_text_differences(tmp_path, pdf_builder):
    pages = [page_lines(i) for i in range(1, 9)]
    a = pdf_builder(pages)
    order = [0, 6, 1, 2, 3, 4, 5, 7]          # pagina 7 spostata in posizione 2
    b = pdf_builder([pages[i] for i in order])
    r = run(a, b, mode=ce.MODE_TEXT)
    assert [(d.kind, d.subtype) for d in r.differences] == [(ce.PAGE, "moved")]
    d = r.differences[0]
    assert d.pages("a") == [6] and d.pages("b") == [1]


def test_swapped_pages_matched_by_content(pdf_builder):
    pages = [page_lines(i) for i in range(1, 6)]
    a = pdf_builder(pages)
    b = pdf_builder([pages[0], pages[1], pages[3], pages[2], pages[4]])
    r = run(a, b, mode=ce.MODE_TEXT)
    assert not [d for d in r.differences if d.kind in (ce.ADDED, ce.DELETED, ce.MODIFIED)]
    assert any(d.kind == ce.PAGE and d.subtype == "moved" for d in r.differences)


def _logo(color):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


def test_image_replaced(pdf_builder):
    def page(color):
        def draw(p):
            p.insert_text((72, 90), "Carta intestata della ditta", fontsize=12)
            p.insert_image(pymupdf.Rect(400, 50, 500, 150), stream=_logo(color))
        return draw
    a = pdf_builder([page((200, 0, 0))])
    b = pdf_builder([page((0, 0, 200))])
    r = run(a, b, dpi=72)
    images = [d for d in r.differences if d.kind == ce.IMAGE]
    assert [d.subtype for d in images] == ["replaced"]
    assert images[0].rects_a and images[0].rects_b
    assert not [d for d in r.differences if d.kind == ce.VISUAL]


def test_annotations_added_and_modified(tmp_path):
    def make(name, second_text, extra):
        doc = pymupdf.open()
        for i in range(2):
            doc.new_page().insert_text((72, 90), f"Pagina {i + 1} della perizia", fontsize=12)
        a1 = doc[1].add_text_annot((100, 200), "Verificare il seriale")
        a1.set_info(title="Mario")
        a1.update()
        a2 = doc[1].add_text_annot((300, 400), second_text)
        a2.update()
        if extra:
            doc[1].add_text_annot((100, 600), "Nuovo commento").update()
        path = tmp_path / name
        doc.save(str(path))
        return str(path)
    a = make("a.pdf", "Cassa in acciaio", False)
    b = make("b.pdf", "Cassa in oro", True)
    r = run(a, b, mode=ce.MODE_TEXT)
    ann = [d for d in r.differences if d.kind == ce.ANNOTATION]
    assert sorted(d.subtype for d in ann) == ["added", "modified"]
    mod = next(d for d in ann if d.subtype == "modified")
    assert mod.text_a == "Cassa in acciaio" and mod.text_b == "Cassa in oro"
    assert all(d.pages("b") == [1] for d in ann)


def test_form_field_value(tmp_path):
    def make(name, value):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 90), "Modulo di acquisto", fontsize=12)
        w = pymupdf.Widget()
        w.field_name = "importo"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(72, 120, 250, 140)
        w.field_value = value
        page.add_widget(w)
        path = tmp_path / name
        doc.save(str(path))
        return str(path)
    r = run(make("a.pdf", "100"), make("b.pdf", "150"), mode=ce.MODE_TEXT)
    fields = [d for d in r.differences if d.kind == ce.FIELD]
    assert len(fields) == 1
    assert fields[0].text_a == "100" and fields[0].text_b == "150" and "importo" in fields[0].detail


def test_rotation_only(tmp_path, pdf_builder):
    a = pdf_builder([page_lines(1)])
    doc = pymupdf.open(a)
    doc[0].set_rotation(90)
    b = tmp_path / "ruotato.pdf"
    doc.save(str(b))
    r = run(a, b, dpi=72)
    assert [(d.kind, d.subtype) for d in r.differences] == [(ce.PAGE, "rotation")]


# ----------------------------------------------------------------- CMP-12
def _letter_scan(tmp_path, name, amount):
    """Scansione (solo immagine) di una lettera; il testo «vero» è nel campo subject
    perché l'OCR simulato possa «riconoscerlo»."""
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((72, 100), f"Importo dovuto: {amount} euro", fontsize=16)
    img = p.get_pixmap(dpi=100).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=img)
    doc.set_metadata({"subject": f"Importo dovuto: {amount} euro"})
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


def _fake_ocr(data, languages, mode, pages=None, progress=None):
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = (doc.metadata or {}).get("subject", "")
    for page in doc:
        page.insert_text((72, 100), text, fontsize=16, render_mode=3)
    out = doc.tobytes()
    doc.close()
    return out


def test_scan_preset_uses_ocr_in_memory(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(ce, "_ocr_function", lambda: _fake_ocr)
    a = _letter_scan(tmp_path, "a.pdf", "1250")
    b = _letter_scan(tmp_path, "b.pdf", "1350")
    before = (hashlib.sha256(read(a)).hexdigest(), hashlib.sha256(read(b)).hexdigest())
    r = run(a, b, preset="scan", sensitivity="low")
    mods = [d for d in r.differences if d.kind == ce.MODIFIED]
    assert len(mods) == 1 and mods[0].text_a == "1250" and mods[0].text_b == "1350"
    assert not [d for d in r.differences if d.kind == ce.VISUAL]
    assert (hashlib.sha256(read(a)).hexdigest(), hashlib.sha256(read(b)).hexdigest()) == before
    assert run(a, a, preset="scan").differences == []


def test_scan_preset_without_ocr_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "_ocr_function", lambda: None)
    a = _letter_scan(tmp_path, "a.pdf", "1250")
    b = _letter_scan(tmp_path, "b.pdf", "1350")
    r = run(a, b, preset="scan")
    assert any("OCR non disponibile" in w for w in r.warnings)
    assert r.options.mode == ce.MODE_VISUAL
    assert any(d.kind == ce.VISUAL for d in r.differences)


def test_transform_image_rotation_and_scale():
    img = _img(lambda im: im.__setitem__((slice(10, 20), slice(10, 20)), 0))
    assert ce.transform_image(img) is img
    half = ce.transform_image(img, 0.0, 0.5)
    assert (half[5:10, 5:10] < 128).all() and (half[15:20, 15:20] == 255).all()
    rot = ce.transform_image(img, 5.0)
    assert rot.shape == img.shape and (rot != img).any()
