"""Motore delle operazioni strutturali sulle pagine (PAG-02…07, 11, 12, 15, 18–24)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.core.platform import find_tool
from foliant.modules.pages import engine as E

from .conftest import numbered_pdf, page_texts, save_numbered


# ------------------------------------------------------------------ PAG-02
def test_parse_ranges_union_and_open():
    assert E.parse_ranges("1-3, 8-", 10) == [0, 1, 2, 7, 8, 9]
    assert len(E.parse_ranges("1-3, 8-", 10)) == 6
    assert E.parse_ranges("-2; 5", 10) == [0, 1, 4]
    assert E.parse_ranges(" 3 - 1 ", 10) == [0, 1, 2]
    assert E.parse_ranges("3-1", 10, ordered=True) == [2, 1, 0]
    assert E.parse_ranges("1-2,2", 10, ordered=True) == [0, 1, 1]


@pytest.mark.parametrize("text", ["4-12", "12"])
def test_parse_ranges_out_of_bounds_cites_page(text):
    with pytest.raises(E.RangeError) as e:
        E.parse_ranges(text, 10)
    assert "12" in str(e.value) and "10" in str(e.value)


@pytest.mark.parametrize("text", ["", "  ", "a", "-", "1-x"])
def test_parse_ranges_invalid(text):
    with pytest.raises(E.RangeError):
        E.parse_ranges(text, 10)


def test_filters():
    doc = numbered_pdf(6)
    assert E.resolve_pages(doc, "1-6", E.FILTER_ODD) == [0, 2, 4]
    doc[1].set_rotation(90)
    assert E.resolve_pages(doc, None, E.FILTER_LANDSCAPE) == [1]
    with pytest.raises(E.RangeError):
        E.resolve_pages(doc, "2", E.FILTER_ODD)


def test_labels_interpretation():
    doc = numbered_pdf(24)
    doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 4, "style": "D", "firstpagenum": 1}])
    labels = E.page_labels(doc)
    assert labels[:5] == ["i", "ii", "iii", "iv", "1"]
    assert E.parse_ranges("ii-2", 24, labels=labels) == [1, 2, 3, 4, 5]


def test_labels_ambiguous():
    with pytest.raises(E.RangeError) as e:
        E.parse_ranges("A", 3, labels=["A", "B", "A"])
    assert "1" in str(e.value) and "3" in str(e.value)


def test_format_ranges():
    assert E.format_ranges([0, 1, 2, 6, 8, 9]) == "1-3, 7, 9-10"


# ------------------------------------------------------------------ PAG-04/05
def test_move_selected_before_first():
    doc = numbered_pdf(6)
    new = E.move_pages(doc, [1, 4], 0)
    assert page_texts(doc) == ["P2", "P5", "P1", "P3", "P4", "P6"]
    assert new == [0, 1]


def test_move_to_end_and_noop():
    doc = numbered_pdf(6)
    E.move_pages(doc, [0], 6)
    assert page_texts(doc) == ["P2", "P3", "P4", "P5", "P6", "P1"]
    assert not E.reorder(doc, list(range(6)))
    with pytest.raises(E.PageOpError):
        E.move_order(6, [0], 9)


def test_move_keeps_bookmark_target():
    doc = numbered_pdf(6)
    doc.set_toc([[1, "cinque", 5]])
    E.move_pages(doc, [4], 0)
    assert doc.get_toc()[0][2] == 1


def test_reverse_swap_collate():
    assert E.reverse_order(5) == [4, 3, 2, 1, 0]
    assert E.swap_pairs_order(5) == [1, 0, 3, 2, 4]
    order, uneven = E.collate_order(6, None, True)
    # [F1,F2,F3,R3,R2,R1] -> [F1,R1,F2,R2,F3,R3]
    assert order == [0, 5, 1, 4, 2, 3] and not uneven
    order, uneven = E.collate_order(5, 3, False)
    assert order == [0, 3, 1, 4, 2] and uneven
    assert E.reverse_order(6, [1, 2, 3]) == [0, 3, 2, 1, 4, 5]


# ------------------------------------------------------------------ PAG-06
def test_insert_blank_like_adjacent():
    doc = numbered_pdf(3, size=(595, 842))
    new = E.insert_blank_pages(doc, 1, 2)
    assert doc.page_count == 5 and new == [1, 2]
    for i in new:
        assert abs(doc[i].rect.width - 595) <= 1 and abs(doc[i].rect.height - 842) <= 1
        assert not doc[i].get_text().strip()
    data = doc.tobytes()
    assert pymupdf.open(stream=data).page_count == 5


def test_insert_blank_custom_landscape():
    doc = numbered_pdf(1)
    E.insert_blank_pages(doc, 1, 1, size=(100 * E.MM, 200 * E.MM), landscape=True)
    r = doc[1].rect
    assert abs(r.width / E.MM - 200) <= 0.5 and abs(r.height / E.MM - 100) <= 0.5
    assert doc[1].rotation == 0


def test_insert_blank_copies_rotation_and_validates():
    doc = numbered_pdf(1)
    doc[0].set_rotation(90)
    E.insert_blank_pages(doc, 1, 1)
    assert doc[1].rotation == 90
    with pytest.raises(E.PageOpError):
        E.insert_blank_pages(doc, 0, 0)
    with pytest.raises(E.PageOpError):
        E.insert_blank_pages(doc, 0, 1, size=(2, 100))


# ------------------------------------------------------------------ PAG-07
def test_insert_from_other_file():
    a = numbered_pdf(4, prefix="A")
    b = numbered_pdf(3, prefix="B")
    new = E.insert_pdf_pages(a, b, 1, [1, 2])
    assert page_texts(a) == ["A1", "B2", "B3", "A2", "A3", "A4"]
    assert new == [1, 2]


def test_insert_interleaved():
    a = numbered_pdf(3, prefix="A")
    b = numbered_pdf(3, prefix="B")
    E.insert_pdf_pages(a, b, 0, interleave=1)
    assert page_texts(a) == ["A1", "B1", "A2", "B2", "A3", "B3"]


def test_insert_interleaved_longer_source():
    a = numbered_pdf(2, prefix="A")
    b = numbered_pdf(4, prefix="B")
    E.insert_pdf_pages(a, b, 0, interleave=1)
    assert page_texts(a) == ["A1", "B1", "A2", "B2", "B3", "B4"]


def test_insert_same_document():
    a = numbered_pdf(2, prefix="A")
    E.insert_pdf_pages(a, a, 2)
    assert page_texts(a) == ["A1", "A2", "A1", "A2"]


def test_insert_shared_image_stored_once(tmp_path):
    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 300), False)
    img.clear_with(120)
    b = pymupdf.open()
    xref = 0
    for _i in range(10):
        p = b.new_page()
        if not xref:
            xref = p.insert_image(pymupdf.Rect(0, 0, 300, 300), pixmap=img)
        else:
            p.insert_image(pymupdf.Rect(0, 0, 300, 300), xref=xref)
    a = numbered_pdf(1)
    E.insert_pdf_pages(a, b, 1)
    out = pymupdf.open(stream=a.tobytes(garbage=3, deflate=True))
    images = {x for i in range(out.page_count) for x, *_r in out[i].get_images()}
    assert len(images) == 1


def test_open_source_password(tmp_path):
    p = save_numbered(tmp_path / "b.pdf", 2, password="pw")
    with pytest.raises(E.PasswordRequired):
        E.open_source(p)
    with pytest.raises(E.WrongPassword):
        E.open_source(p, "nope")
    assert E.open_source(p, "pw").page_count == 2


def test_open_source_corrupt(tmp_path):
    p = tmp_path / "rotto.pdf"
    p.write_bytes(b"not a pdf at all")
    with pytest.raises(E.DocumentError):
        E.open_source(p)


# ------------------------------------------------------------------ PAG-11
def test_delete_pages():
    doc = numbered_pdf(10)
    E.delete_pages(doc, E.parse_ranges("2,4-6", 10))
    assert page_texts(doc) == ["P1", "P3", "P7", "P8", "P9", "P10"]


def test_delete_all_refused():
    doc = numbered_pdf(3)
    with pytest.raises(E.PageOpError):
        E.delete_pages(doc, [0, 1, 2])
    assert doc.page_count == 3


def test_delete_cleans_references():
    doc = numbered_pdf(8)
    doc.set_toc([[1, "sette", 7], [1, "uno", 1]])
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "page": 6, "from": pymupdf.Rect(10, 10, 50, 50),
                        "to": pymupdf.Point(0, 0)})
    summary = E.linked_items(doc, [6])
    assert summary["bookmarks"] == 1 and summary["links"] == 1
    E.delete_pages(doc, [6])
    out = pymupdf.open(stream=doc.tobytes(garbage=3))
    xrefs = {out[i].xref for i in range(out.page_count)}
    for i in range(out.page_count):
        for link in out[i].get_links():
            assert link.get("page", -1) < out.page_count
    for item in out.get_toc(simple=False):
        assert item[2] == -1 or out[item[2] - 1].xref in xrefs


def test_delete_removes_unused_images():
    doc = numbered_pdf(2)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pix.clear_with(30)
    doc[1].insert_image(pymupdf.Rect(0, 0, 200, 200), pixmap=pix)
    E.delete_pages(doc, [1])
    out = pymupdf.open(stream=doc.tobytes(garbage=3))
    assert not any(out.xref_get_key(x, "Subtype")[1] == "/Image" for x in range(1, out.xref_length()))


# ------------------------------------------------------------------ PAG-12
def test_extract_single_file():
    doc = numbered_pdf(10)
    data = E.extract_pages(doc, E.parse_ranges("1-3,7", 10, ordered=True))
    out = pymupdf.open(stream=data)
    assert page_texts(out) == ["P1", "P2", "P3", "P7"]


def test_extract_bookmarks_filtered():
    doc = numbered_pdf(10)
    doc.set_toc([[1, "sette", 7], [1, "nove", 9]])
    out = pymupdf.open(stream=E.extract_pages(doc, [6]))
    assert out.get_toc() == [[1, "sette", 1]]


def test_extract_one_per_page_with_template(tmp_path):
    doc = numbered_pdf(10)
    parts = [E.SplitPart([i]) for i in range(10)]
    files = E.write_parts(doc, parts, tmp_path, "{nome}_p{pagina}", "doc")
    assert [f.name for f in files] == [f"doc_p{i}.pdf" for i in range(1, 11)]
    assert all(pymupdf.open(str(f)).page_count == 1 for f in files)


def test_extract_drops_encryption(tmp_path):
    p = save_numbered(tmp_path / "c.pdf", 3, password="pw")
    src = E.open_source(p, "pw")
    out = pymupdf.open(stream=E.extract_pages(src, [0]))
    assert not out.needs_pass


# ------------------------------------------------------------------ PAG-15
def test_duplicate_after():
    doc = numbered_pdf(3)
    new = E.duplicate_pages(doc, [1], copies=2)
    assert page_texts(doc) == ["P1", "P2", "P2", "P2", "P3"]
    assert new == [2, 3]


def test_duplicate_block_and_end():
    doc = numbered_pdf(4)
    E.duplicate_pages(doc, [0, 1], where="block")
    assert page_texts(doc) == ["P1", "P2", "P1", "P2", "P3", "P4"]
    doc = numbered_pdf(3)
    E.duplicate_pages(doc, [0], where="end")
    assert page_texts(doc) == ["P1", "P2", "P3", "P1"]


def test_duplicate_shares_image(tmp_path):
    import os
    doc = numbered_pdf(1)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1300, 1300), False)
    pix.set_rect(pix.irect, (10, 200, 30))
    samples = bytearray(os.urandom(1300 * 1300 * 3))
    pix = pymupdf.Pixmap(pymupdf.csRGB, 1300, 1300, bytes(samples), False)
    doc[0].insert_image(pymupdf.Rect(0, 0, 400, 400), pixmap=pix)
    before = len(doc.tobytes(garbage=3, deflate=True))
    E.duplicate_pages(doc, [0])
    after = len(doc.tobytes(garbage=3, deflate=True))
    assert before > 4_000_000
    assert after - before < 10_000


def test_duplicate_form_field_independent():
    doc = numbered_pdf(1)
    w = pymupdf.Widget()
    w.field_name = "Nome"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(50, 600, 200, 630)
    w.field_value = "Mario"
    doc[0].add_widget(w)
    E.duplicate_pages(doc, [0])
    names = [x.field_name for x in doc[1].widgets()]
    assert names and names[0] != "Nome"
    page1 = doc[1]
    copy = next(page1.widgets())
    copy.field_value = "Luigi"
    copy.update()
    page0 = doc[0]
    assert next(page0.widgets()).field_value == "Mario"


# ------------------------------------------------------------------ PAG-18
def test_rotate_four_times_identity():
    doc = numbered_pdf(1)
    before = doc[0].get_pixmap().samples
    for _i in range(4):
        E.rotate_pages(doc, [0], 90)
    assert doc[0].rotation == 0
    assert doc[0].get_pixmap().samples == before


def test_rotate_landscape_filter_only():
    doc = numbered_pdf(4)
    doc[1].set_rotation(90)
    targets = E.resolve_pages(doc, None, E.FILTER_LANDSCAPE)
    E.rotate_pages(doc, targets, 90)
    assert [doc[i].rotation for i in range(4)] == [0, 180, 0, 0]


def test_rotate_inherited():
    doc = numbered_pdf(3)
    pages_root = doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0]
    for i in range(3):
        doc.xref_set_key(doc[i].xref, "Rotate", "null")
    doc.xref_set_key(int(pages_root), "Rotate", "90")
    data = doc.tobytes()
    doc = pymupdf.open(stream=data)
    assert [doc[i].rotation for i in range(3)] == [90, 90, 90]
    E.rotate_pages(doc, [1], 90)
    assert [doc[i].rotation for i in range(3)] == [90, 180, 90]


def test_rotate_invalid():
    doc = numbered_pdf(1)
    with pytest.raises(E.PageOpError):
        E.rotate_pages(doc, [0], 45)


def test_rotate_highlight_follows_word():
    doc = numbered_pdf(1)
    quads = doc[0].search_for("P1", quads=True)
    doc[0].add_highlight_annot(quads)
    E.rotate_pages(doc, [0], 90)
    out = pymupdf.open(stream=doc.tobytes())
    page = out[0]
    annot = next(page.annots())
    word = page.search_for("P1")[0]   # coordinate della pagina non ruotata
    assert annot.rect.intersects(word)


# ------------------------------------------------------------------ PAG-19
def test_replace_keeps_bookmark():
    a = numbered_pdf(5, prefix="A")
    a.set_toc([[1, "tre", 3]])
    b = numbered_pdf(2, prefix="B")
    E.replace_pages(a, [2, 3], b, [0, 1])
    assert page_texts(a) == ["A1", "A2", "B1", "B2", "A5"]
    assert a.get_toc()[0][2] == 3


def test_replace_keeps_original_annotation():
    a = numbered_pdf(3, prefix="A")
    a[2].add_text_annot((100, 300), "nota")
    b = numbered_pdf(1, prefix="B")
    E.replace_pages(a, [2], b, [0], keep_annots="original")
    assert page_texts(a)[2] == "B1"
    assert [x.type[1] for x in a[2].annots()] == ["Text"]


def test_replace_more_source_pages():
    a = numbered_pdf(4, prefix="A")
    b = numbered_pdf(3, prefix="B")
    new = E.replace_pages(a, [1, 2], b, [0, 1, 2])
    assert page_texts(a) == ["A1", "B1", "B2", "B3", "A4"]
    assert new == [1, 2, 3]


def test_replace_fewer_source_pages():
    a = numbered_pdf(5, prefix="A")
    b = numbered_pdf(1, prefix="B")
    E.replace_pages(a, [1, 2, 3], b, [0])
    assert page_texts(a) == ["A1", "B1", "A5"]


# ------------------------------------------------------------------ PAG-20…23
def test_split_every_3():
    plan = E.split_document(numbered_pdf(10), "every", 3)
    assert [len(p.indices) for p in plan.parts] == [3, 3, 3, 1]


def test_split_every_too_big_warns():
    plan = E.split_document(numbered_pdf(3), "every", 5)
    assert len(plan.parts) == 1 and plan.warnings


def test_split_ranges_uncovered_warning():
    plan = E.split_document(numbered_pdf(10), "ranges", "1-2; 5-6")
    assert len(plan.parts) == 2
    assert plan.uncovered == [2, 3, 6, 7, 8, 9]
    assert "3-4, 7-10" in plan.warnings[0]


def test_split_parts_balanced():
    plan = E.split_document(numbered_pdf(10), "parts", 3)
    assert [len(p.indices) for p in plan.parts] == [4, 3, 3]


def test_split_names_and_no_overwrite(tmp_path):
    doc = numbered_pdf(9)
    plan = E.split_document(doc, "parts", 3)
    (tmp_path / "doc_parte1.pdf").write_bytes(b"esistente")
    files = E.write_parts(doc, plan.parts, tmp_path, "{nome}_parte{n}", "doc")
    assert [f.name for f in files] == ["doc_parte1 (2).pdf", "doc_parte2.pdf", "doc_parte3.pdf"]
    assert (tmp_path / "doc_parte1.pdf").read_bytes() == b"esistente"


def test_split_bookmarks():
    doc = numbered_pdf(20)
    doc.set_toc([[1, "Uno", 1], [1, "Capitolo 2: Analisi/Risultati", 6], [2, "a", 7], [2, "b", 8], [1, "Tre", 15]])
    plan = E.split_document(doc, "bookmarks", 1)
    assert [len(p.indices) for p in plan.parts] == [5, 9, 6]
    assert E.render_name("{segnalibro}", **plan.parts[1].fields) == "Capitolo 2_ Analisi_Risultati"
    part = pymupdf.open(stream=E.part_bytes(doc, plan.parts[1]))
    assert [t[1:] for t in part.get_toc()] == [["Capitolo 2: Analisi/Risultati", 1], ["a", 2], ["b", 3]]


def test_split_bookmarks_none():
    with pytest.raises(E.PageOpError):
        E.split_document(numbered_pdf(3), "bookmarks", 1)


def test_split_by_size():
    import os
    doc = numbered_pdf(6)
    for i in range(6):
        pix = pymupdf.Pixmap(pymupdf.csRGB, 200, 200, os.urandom(200 * 200 * 3), False)
        doc[i].insert_image(pymupdf.Rect(0, 200, 200, 400), pixmap=pix)
    plan = E.split_document(doc, "size", 300_000)
    sizes = [len(E.part_bytes(doc, p)) for p in plan.parts]
    assert all(s <= 300_000 or p.oversize for s, p in zip(sizes, plan.parts))
    assert [i for p in plan.parts for i in p.indices] == list(range(6))
    assert len(plan.parts) > 1


def test_split_separators_and_text():
    doc = numbered_pdf(12)
    for i in (3, 8):
        doc.delete_page(i)
        doc.new_page(pno=i)
    plan = E.split_document(doc, "separators", blank=True, discard=True)
    assert [len(p.indices) for p in plan.parts] == [3, 4, 3]
    inv = pymupdf.open()
    for n in (101, 102, 103):
        inv.new_page().insert_text((72, 72), f"Fattura n. {n}")
        inv.new_page().insert_text((72, 72), "allegato")
    plan = E.split_document(inv, "separators", pattern=r"Fattura n\. (\d+)")
    names = [E.render_name("fattura_{match1}", **p.fields) for p in plan.parts]
    assert names == ["fattura_101", "fattura_102", "fattura_103"]
    with pytest.raises(E.RangeError):
        E.split_document(inv, "separators", pattern="(")


def test_safe_filename():
    assert E.safe_filename('a/b\\c:d*e?"f<g>h|') == "a_b_c_d_e__f_g_h_"
    assert len(E.safe_filename("è" * 300).encode()) <= 200


# ------------------------------------------------------------------ PAG-24
def test_merge_order_and_image(tmp_path):
    from PIL import Image
    a = save_numbered(tmp_path / "A.pdf", 3, prefix="A")
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B")
    img = tmp_path / "foto.jpg"
    Image.new("RGB", (300, 200), (200, 10, 10)).save(img, dpi=(72, 72))
    out = pymupdf.open(stream=E.merge_files([b, a, str(img)]))
    assert out.page_count == 6
    assert page_texts(out)[:5] == ["B1", "B2", "A1", "A2", "A3"]
    assert out[5].get_images()


def test_merge_bookmarks_nested(tmp_path):
    a = save_numbered(tmp_path / "A.pdf", 3, prefix="A", toc=[[1, "interno", 2]])
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B")
    out = pymupdf.open(stream=E.merge_files([a, b]))
    assert out.get_toc() == [[1, "A", 1], [2, "interno", 2], [1, "B", 4]]


def test_merge_ranges_and_blank(tmp_path):
    a = save_numbered(tmp_path / "A.pdf", 3, prefix="A")
    b = save_numbered(tmp_path / "B.pdf", 2, prefix="B")
    out = pymupdf.open(stream=E.merge_files([E.MergeEntry(a, "2-3"), E.MergeEntry(b, "1")]))
    assert page_texts(out) == ["A2", "A3", "B1"]
    out = pymupdf.open(stream=E.merge_files([a, b], blank_after_odd=True))
    assert out.page_count == 6 and not out[3].get_text().strip()


def test_merge_form_fields_distinct(tmp_path):
    paths = []
    for name in ("A", "B"):
        d = numbered_pdf(1, prefix=name)
        w = pymupdf.Widget()
        w.field_name = "Nome"
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(50, 600, 200, 630)
        w.field_value = name
        d[0].add_widget(w)
        p = tmp_path / f"{name}.pdf"
        d.save(str(p))
        paths.append(str(p))
    out = pymupdf.open(stream=E.merge_files(paths))
    fields = [(w.field_name, w.field_value) for i in range(2) for w in out[i].widgets()]
    assert len({f[0] for f in fields}) == 2
    assert {f[1] for f in fields} == {"A", "B"}


def test_merge_errors_collected(tmp_path):
    a = save_numbered(tmp_path / "A.pdf", 1, prefix="A")
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"xxx")
    errors: list[str] = []
    out = pymupdf.open(stream=E.merge_files([a, str(bad)], errors=errors))
    assert out.page_count == 1 and errors and "rotto.pdf" in errors[0]
    with pytest.raises(E.DocumentError):
        E.merge_files([str(bad)])


def test_insert_with_source_bookmarks():
    a = numbered_pdf(3, prefix="A")
    a.set_toc([[1, "a1", 1], [1, "a3", 3]])
    b = numbered_pdf(2, prefix="B")
    b.set_toc([[1, "interno", 2]])
    E.insert_pdf_pages(a, b, 1, bookmark_title="B")
    assert a.get_toc() == [[1, "a1", 1], [1, "B", 2], [2, "interno", 3], [1, "a3", 5]]


def test_merge_alternate(tmp_path):
    f = save_numbered(tmp_path / "F.pdf", 3, prefix="F")
    r = save_numbered(tmp_path / "R.pdf", 3, prefix="R")
    out = pymupdf.open(stream=E.merge_alternate(f, r, reverse_b=True))
    assert page_texts(out) == ["F1", "R3", "F2", "R2", "F3", "R1"]


def _tagged_pdf(n: int = 3) -> pymupdf.Document:
    d = numbered_pdf(n)
    els = []
    root, doc_el = d.get_new_xref(), d.get_new_xref()
    for i in range(n):
        d[i].clean_contents()
        c = d[i].get_contents()[0]
        d.update_stream(c, b"/P <</MCID 0>> BDC BT /helv 12 Tf 72 720 Td (x) Tj ET EMC")
        d.xref_set_key(d[i].xref, "StructParents", str(i))
        e = d.get_new_xref()
        els.append(e)
        d.update_object(e, f"<</Type/StructElem/S/P/P {doc_el} 0 R/Pg {d[i].xref} 0 R/K 0>>")
    pt = d.get_new_xref()
    d.update_object(pt, "<</Nums[" + " ".join(f"{i} [{els[i]} 0 R]" for i in range(n)) + "]>>")
    d.update_object(doc_el, f"<</Type/StructElem/S/Document/P {root} 0 R/K[{' '.join(f'{e} 0 R' for e in els)}]>>")
    d.update_object(root, f"<</Type/StructTreeRoot/K {doc_el} 0 R/ParentTree {pt} 0 R>>")
    d.xref_set_key(d.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    return pymupdf.open(stream=d.tobytes())


def _struct_page_refs(doc):
    refs = set()
    for x in range(1, doc.xref_length()):
        obj = doc.xref_object(x, compressed=True)
        if "/StructElem" in obj:
            refs |= {int(m) for m in __import__("re").findall(r"/Pg (\\d+) 0 R", obj)}
    return refs


def test_delete_prunes_structure_tree():
    doc = _tagged_pdf(3)
    E.delete_pages(doc, [1])
    out = pymupdf.open(stream=doc.tobytes(garbage=3))
    live = {out[i].xref for i in range(out.page_count)}
    assert _struct_page_refs(out) <= live
    root = int(out.xref_get_key(out.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    pt = int(out.xref_get_key(root, "ParentTree")[1].split()[0])
    items = E._split_array(out.xref_get_key(pt, "Nums")[1])
    assert items[0::2] == ["0", "2"]
    doc_el = int(out.xref_get_key(root, "K")[1].split()[0])
    assert len(E._split_array(out.xref_get_key(doc_el, "K")[1])) == 2


def test_extract_prunes_structure_tree():
    doc = _tagged_pdf(3)
    out = pymupdf.open(stream=E.extract_pages(doc, [2]))
    live = {out[i].xref for i in range(out.page_count)}
    assert _struct_page_refs(out) <= live


def test_split_text_top_area_only():
    doc = pymupdf.open()
    for n in range(3):
        p = doc.new_page()
        p.insert_text((72, 72), f"Fattura n. {n}")
        p.insert_text((72, 700), "rif. Fattura n. 99")
    plan = E.split_document(doc, "separators", pattern=r"Fattura n\. (\d+)", top_fraction=0.25)
    assert [p.fields["match1"] for p in plan.parts] == ["0", "1", "2"]


def test_labels_anchored_after_insert_and_delete():
    doc = numbered_pdf(10)
    doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 3, "style": "D", "firstpagenum": 1}])
    assert E.page_labels(doc)[:5] == ["i", "ii", "iii", "1", "2"]
    E.insert_blank_pages(doc, 0, 2)
    labels = E.page_labels(doc)
    assert labels[5] == "1" and page_texts(doc)[5] == "P4"
    E.delete_pages(doc, [5])          # elimina la pagina che apre l'intervallo
    assert E.page_labels(doc)[5] == "1" and page_texts(doc)[5] == "P5"
    assert doc.get_page_labels()[0]["startpage"] == 0


def test_labels_follow_moved_page():
    doc = numbered_pdf(6)
    doc.set_page_labels([{"startpage": 0, "style": "D"}, {"startpage": 4, "style": "A"}])
    E.move_pages(doc, [4], 1)
    labels = E.page_labels(doc)
    assert page_texts(doc)[1] == "P5" and labels[1] == "A"


def test_split_text_with_ocr_on_scanned_pages():
    """PAG-23: su pagine scannerizzate (solo immagine) il testo si cerca con l'OCR."""
    if not find_tool("tesseract"):
        pytest.skip("Tesseract non installato")
    src = pymupdf.open()
    for n in (101, 102):
        for text in (f"Fattura n. {n}", "Allegato"):
            p = src.new_page(width=595, height=842)
            p.insert_text((72, 120), text, fontsize=28)
    scans = pymupdf.open()
    for i in range(src.page_count):
        pix = src[i].get_pixmap(dpi=150)
        page = scans.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=pix)
    assert not scans[0].get_text().strip()
    plan = E.split_document(scans, "separators", pattern=r"Fattura n\. (\d+)", ocr=True)
    assert [p.fields.get("match1") for p in plan.parts] == ["101", "102"]
    assert [len(p.indices) for p in plan.parts] == [2, 2]
