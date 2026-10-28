"""Test del motore di geometria pagine (PAG-25…PAG-30)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.pages import layout
from foliant.modules.pages.engine import MM, PAPER_SIZES, PageOpError

A4 = PAPER_SIZES["A4"]
A3 = PAPER_SIZES["A3"]


# ------------------------------------------------------------------ helpers
def new_doc(n=1, size=A4, text="Pagina {n}"):
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=size[0], height=size[1])
        if text:
            p.insert_text((72, 100), text.format(n=i + 1), fontsize=24)
            p.insert_text((72, 140), "Orologeria Verdi contratto", fontsize=12)
    return doc


def reload(doc):
    return pymupdf.open("pdf", doc.tobytes())


def raw(doc, i, key):
    return [float(v) for v in doc.xref_get_key(doc[i].xref, key)[1].strip("[]").split()]


def approx(a, b, tol):
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert abs(x - y) <= tol, (a, b)


def word_rect(page, word):
    hits = page.search_for(word)
    assert hits, word
    return hits[0]


def image_png(w=1200, h=900, seed=1):
    """Immagine con rumore (poco comprimibile)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)
    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, arr.tobytes(), False)
    return pix.tobytes("png")


# ======================================================================== PAG-25
def test_crop_a4_10mm_each_side():
    doc = new_doc()
    assert layout.crop_pages(doc, [0], (10, 10, 10, 10), unit="mm") == [0]
    doc = reload(doc)
    approx(raw(doc, 0, "CropBox"), [28.35, 28.35, 566.93, 813.54], 0.1)
    r = doc[0].rect
    assert abs(r.width / MM - 190) < 0.05 and abs(r.height / MM - 277) < 0.05


def test_crop_rotate_90_top_margin_cuts_displayed_top():
    doc = new_doc()
    doc[0].set_rotation(90)
    dw, dh = doc[0].rect.width, doc[0].rect.height
    layout.crop_pages(doc, [0], (20, 0, 0, 0), unit="mm")
    page = doc[0]
    assert abs(page.rect.width - dw) < 0.01
    assert abs(page.rect.height - (dh - 20 * MM)) < 0.01
    # il lato che appare in alto (R=90) è il bordo sinistro della pagina non ruotata (x = 0)
    approx(raw(doc, 0, "CropBox"), [20 * MM, 0, A4[0], A4[1]], 0.01)


def test_crop_nonzero_media_origin_and_percent():
    doc = new_doc()
    doc.xref_set_key(doc[0].xref, "MediaBox", "[100 200 700 1000]")
    layout.crop_pages(doc, [0], (10, 0, 0, 25), unit="%")
    approx(raw(doc, 0, "CropBox"), [100, 200, 550, 920], 0.01)


def test_crop_relative_to_current_vs_full_and_mirror():
    doc = new_doc(2)
    layout.crop_pages(doc, [0, 1], (10, 10, 10, 10))
    layout.crop_pages(doc, [0], (10, 10, 10, 10))
    approx(raw(doc, 0, "CropBox"), [20, 20, A4[0] - 20, A4[1] - 20], 0.01)
    layout.crop_pages(doc, [1], (5, 5, 5, 5), relative_to_current=False)
    approx(raw(doc, 1, "CropBox"), [5, 5, A4[0] - 5, A4[1] - 5], 0.01)
    layout.reset_crop(doc, [0, 1])
    layout.crop_pages(doc, [0, 1], (0, 0, 30, 0), mirror_even=True)
    approx(raw(doc, 0, "CropBox"), [30, 0, A4[0], A4[1]], 0.01)      # pagina 1 (dispari)
    approx(raw(doc, 1, "CropBox"), [0, 0, A4[0] - 30, A4[1]], 0.01)  # pagina 2: speculare


def test_crop_too_small_errors_and_skips():
    doc = new_doc()
    with pytest.raises(PageOpError):
        layout.crop_pages(doc, [0], (500, 500, 0, 0))
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    doc.new_page(width=595, height=842)
    assert layout.crop_pages(doc, [0, 1], (60, 60, 0, 0)) == [1]   # la pagina piccola è saltata


def test_crop_hard_writes_mediabox():
    doc = new_doc()
    layout.crop_pages(doc, [0], (10, 20, 30, 40), hard=True)
    approx(raw(doc, 0, "MediaBox"), [30, 20, A4[0] - 40, A4[1] - 10], 0.01)
    assert doc.xref_get_key(doc[0].xref, "CropBox")[0] == "null"


def test_crop_to_rect_clamped_and_reset():
    doc = new_doc()
    layout.crop_to_rect(doc, [0], (50, 60, 9999, 400))
    page = doc[0]
    approx(list(page.rect), [0, 0, A4[0] - 50, 340], 0.01)
    layout.reset_crop(doc, [0])
    doc = reload(doc)
    t, v = doc.xref_get_key(doc[0].xref, "CropBox")
    assert t == "null" or v == doc.xref_get_key(doc[0].xref, "MediaBox")[1]
    approx(list(doc[0].rect), [0, 0, *A4], 0.01)


def test_hidden_content_warning():
    doc = new_doc()
    assert not layout.hidden_content_outside_crop(doc)
    layout.crop_pages(doc, [0], (0, 400, 0, 0))     # taglia la metà inferiore: il testo è in alto
    assert not layout.hidden_content_outside_crop(doc)
    layout.reset_crop(doc, [0])
    layout.crop_pages(doc, [0], (200, 0, 0, 0))     # il testo in alto resta fuori
    assert layout.hidden_content_outside_crop(doc)


# ======================================================================== PAG-26
def test_white_margins_black_square():
    doc = new_doc(text=None)
    doc[0].draw_rect(pymupdf.Rect(150, 300, 250, 400), color=None, fill=(0, 0, 0))
    rep = layout.remove_white_margins(doc, [0], extra=0)
    assert rep["cropped"] == [0]
    # in spazio utente: x 150..250, y 842-400..842-300
    approx(raw(doc, 0, "CropBox"), [150, A4[1] - 400, 250, A4[1] - 300], 1.0)


def test_content_bbox_ignores_invisible_text_and_blank():
    doc = new_doc(text=None)
    assert layout.content_bbox(doc[0]) is None
    doc[0].insert_text((300, 700), "testo invisibile", render_mode=3)
    doc[0].draw_rect(pymupdf.Rect(400, 50, 500, 60), color=None, fill=(1, 1, 1))   # bianco su bianco
    assert layout.content_bbox(doc[0]) is None
    rep = layout.remove_white_margins(doc, [0])
    assert rep["blank"] == [0] and rep["cropped"] == []


def test_white_margins_uniform_with_extra():
    doc = new_doc(3, text=None)
    doc[0].draw_rect(pymupdf.Rect(100, 100, 200, 200), color=None, fill=(0, 0, 0))
    doc[1].draw_rect(pymupdf.Rect(300, 400, 450, 700), color=None, fill=(0, 0, 0))
    doc[2].draw_rect(pymupdf.Rect(80, 500, 120, 520), color=None, fill=(0.2, 0.2, 0.2))
    bbs = [layout.content_bbox(doc[i]) for i in range(3)]
    layout.remove_white_margins(doc, [0, 1, 2], extra=5 * MM, uniform=True)
    boxes = [raw(doc, i, "CropBox") for i in range(3)]
    assert boxes[0] == boxes[1] == boxes[2]
    crop = pymupdf.Rect(boxes[0])
    for bb in bbs:
        user = pymupdf.Rect(bb.x0, A4[1] - bb.y1, bb.x1, A4[1] - bb.y0)
        assert crop.contains(user + (-5 * MM + 0.5, -5 * MM + 0.5, 5 * MM - 0.5, 5 * MM - 0.5))
    approx(boxes[0], [80 - 5 * MM, A4[1] - 700 - 5 * MM, 450 + 5 * MM, A4[1] - 100 + 5 * MM], 1.0)


def test_white_margins_full_background_and_border():
    doc = new_doc(text=None)
    doc[0].draw_rect(doc[0].rect, color=None, fill=(0.3, 0.3, 0.3))
    rep = layout.remove_white_margins(doc, [0])
    assert rep["full"] == [0] and rep["cropped"] == []
    # scansione con bordi scuri: la fascia di bordo è ignorata
    doc = new_doc(text=None)
    p = doc[0]
    p.draw_rect(pymupdf.Rect(0, 0, A4[0], 15), color=None, fill=(0, 0, 0))
    p.draw_rect(pymupdf.Rect(200, 300, 300, 400), color=None, fill=(0, 0, 0))
    bb = layout.content_bbox(p, ignore_border=0.05)
    approx(list(bb), [200, 300, 300, 400], 1.0)


def test_content_bbox_rotated_page():
    doc = new_doc(text=None)
    doc[0].draw_rect(pymupdf.Rect(150, 300, 250, 400), color=None, fill=(0, 0, 0))
    doc[0].set_rotation(90)
    approx(list(layout.content_bbox(doc[0])), [150, 300, 250, 400], 1.0)


# ======================================================================== PAG-27
def test_trim_with_bleed():
    doc = new_doc(size=(216 * MM, 303 * MM))
    assert layout.set_trim_with_bleed(doc, [0], PAPER_SIZES["A4"], bleed=3 * MM) == []
    boxes = layout.get_boxes(doc[0])
    trim, bleed = boxes["TrimBox"]["rect"], boxes["BleedBox"]["rect"]
    assert abs(trim.width / MM - 210) < 0.1 and abs(trim.height / MM - 297) < 0.1
    assert abs(bleed.width / MM - 216) < 0.1 and abs(bleed.height / MM - 303) < 0.1
    assert not boxes["TrimBox"]["default"]


def test_boxes_defaults_and_validation():
    doc = new_doc()
    layout.crop_pages(doc, [0], (10, 10, 10, 10))
    boxes = layout.get_boxes(doc[0])
    crop = boxes["CropBox"]["rect"]
    for name in ("BleedBox", "TrimBox", "ArtBox"):
        assert boxes[name]["default"] and boxes[name]["rect"] == crop
    assert not boxes["MediaBox"]["default"] and not boxes["CropBox"]["default"]
    # riquadro oltre il supporto → limitato, con segnalazione
    assert layout.set_box(doc, [0], "ArtBox", (-10, -10, 100, 100)) == [0]
    assert raw(doc, 0, "ArtBox") == [0, 0, 100, 100]
    with pytest.raises(PageOpError):
        layout.set_box(doc, [0], "TrimBox", (1000, 1000, 1100, 1100))
    with pytest.raises(PageOpError):
        layout.set_trim_with_bleed(doc, [0], (700, 900))
    with pytest.raises(PageOpError):
        layout.set_trim_with_bleed(doc, [0], (100, 100), bleed=-1)
    layout.set_box(doc, [0], "ArtBox", None)
    assert layout.get_boxes(doc[0])["ArtBox"]["default"]


# ======================================================================== PAG-28
def test_resize_a3_to_a4_scale_text_identical():
    doc = new_doc(size=A3)
    before = doc[0].get_text()
    w_before = word_rect(doc[0], "Orologeria")
    assert layout.resize_pages(doc, [0], A4, mode="scale") == [0]
    doc = reload(doc)
    page = doc[0]
    assert abs(page.rect.width - 595) <= 1 and abs(page.rect.height - 842) <= 1
    assert page.get_text() == before
    w_after = word_rect(page, "Orologeria")
    s = w_after.width / w_before.width
    assert abs(s - 0.707) <= 0.01
    assert not page.get_images()       # nessuna rasterizzazione


def test_resize_highlight_still_covers_word():
    doc = new_doc()
    q = word_rect(doc[0], "Orologeria")
    doc[0].add_highlight_annot(q)
    layout.resize_pages(doc, [0], PAPER_SIZES["Letter"], mode="scale")
    doc = reload(doc)
    page = doc[0]
    assert abs(page.rect.width - 612) < 0.5 and abs(page.rect.height - 792) < 0.5
    word = word_rect(page, "Orologeria")
    annot = next(page.annots())
    assert annot.rect.contains(word + (1, 1, -1, -1))
    quad_rect = pymupdf.Rect(annot.vertices[0], annot.vertices[3])
    inter = quad_rect & word
    assert inter.get_area() > 0.9 * word.get_area()
    assert quad_rect.get_area() < 1.3 * word.get_area()


def test_resize_canvas_a4_to_a3_center_offset():
    doc = new_doc()
    w0 = word_rect(doc[0], "Orologeria")
    layout.resize_pages(doc, [0], A3, mode="canvas", anchor="center")
    doc = reload(doc)
    page = doc[0]
    assert abs(page.rect.width - A3[0]) < 0.5 and abs(page.rect.height - A3[1]) < 0.5
    w1 = word_rect(page, "Orologeria")
    assert abs(w1.width - w0.width) < 0.01
    assert abs((w1.x0 - w0.x0) - 123.3) <= 0.5
    assert abs((w1.y0 - w0.y0) - 174.3) <= 0.5


def test_resize_canvas_anchor_and_margins_rotated():
    doc = new_doc()
    w0 = word_rect(doc[0], "Orologeria")
    layout.resize_pages(doc, [0], None, mode="canvas", margins=(10, 0, 20, 0))
    page = doc[0]
    assert abs(page.rect.width - (A4[0] + 20)) < 0.01 and abs(page.rect.height - (A4[1] + 10)) < 0.01
    w1 = word_rect(page, "Orologeria")
    assert abs(w1.x0 - w0.x0 - 20) < 0.1 and abs(w1.y0 - w0.y0 - 10) < 0.1
    # pagina ruotata: il formato si riferisce alla pagina visualizzata
    doc = new_doc()
    doc[0].set_rotation(90)
    layout.resize_pages(doc, [0], A3, mode="scale")
    assert abs(doc[0].rect.width - A3[1]) < 0.5 and abs(doc[0].rect.height - A3[0]) < 0.5
    # formato uguale → pagina invariata
    doc = new_doc()
    assert layout.resize_pages(doc, [0], A4) == []


def test_match_page_size_to_first():
    doc = new_doc(1, size=A4)
    doc.new_page(width=A3[0], height=A3[1]).insert_text((72, 100), "Grande", fontsize=20)
    doc.new_page(width=300, height=500).insert_text((20, 50), "Piccola", fontsize=12)
    layout.match_page_size(doc, range(3), 0)
    doc = reload(doc)
    mb = raw(doc, 0, "MediaBox")
    assert raw(doc, 1, "MediaBox") == mb and raw(doc, 2, "MediaBox") == mb
    assert "Grande" in doc[1].get_text() and "Piccola" in doc[2].get_text()


# ======================================================================== PAG-29
def test_split_landscape_1x2_order_and_rtl():
    doc = new_doc(size=(842, 595), text=None)
    doc[0].insert_text((50, 100), "SINISTRA", fontsize=20)
    doc[0].insert_text((500, 100), "DESTRA", fontsize=20)
    assert layout.split_page_grid(doc, [0], rows=1, cols=2) == [0, 1]
    doc = reload(doc)
    assert doc.page_count == 2
    for p in doc:
        assert abs(p.rect.width - 421) < 0.01 and abs(p.rect.height - 595) < 0.01
    assert "SINISTRA" in doc[0].get_text() and "DESTRA" not in doc[0].get_text()
    assert "DESTRA" in doc[1].get_text()
    doc = new_doc(size=(842, 595), text=None)
    doc[0].insert_text((50, 100), "SINISTRA", fontsize=20)
    doc[0].insert_text((500, 100), "DESTRA", fontsize=20)
    layout.split_page_grid(doc, [0], rows=1, cols=2, rtl=True)
    assert "DESTRA" in doc[0].get_text() and "SINISTRA" in doc[1].get_text()


def test_split_image_size_growth_small(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.insert_image(page.rect, stream=image_png(1400, 1000))
    base = len(doc.tobytes(garbage=3, deflate=True))
    assert base > 3_000_000
    layout.split_page_grid(doc, [0], rows=2, cols=2)
    assert doc.page_count == 4
    after = len(doc.tobytes(garbage=3, deflate=True))
    assert after - base < 20_000


def test_split_annotations_assigned_and_rotated_grid():
    doc = new_doc(size=(842, 595), text=None)
    doc[0].insert_text((600, 100), "nota", fontsize=12)
    doc[0].add_text_annot((600, 300), "commento a destra")
    doc[0].add_rect_annot(pymupdf.Rect(380, 200, 520, 260))   # attraversa il taglio, più a destra
    layout.split_page_grid(doc, [0], rows=1, cols=2)
    assert len(list(doc[0].annots())) == 0
    assert len(list(doc[1].annots())) == 2
    # pagina ruotata: griglia riferita alla pagina visualizzata (orizzontale)
    doc = new_doc(size=(595, 842), text=None)
    doc[0].set_rotation(90)
    layout.split_page_grid(doc, [0], rows=1, cols=2)
    assert doc.page_count == 2
    for p in doc:
        assert abs(p.rect.width - 421) < 0.01 and abs(p.rect.height - 595) < 0.01
    with pytest.raises(PageOpError):
        layout.split_page_grid(new_doc(), [0], rows=1, cols=1)
    with pytest.raises(PageOpError):
        layout.split_page_grid(new_doc(), [0], rows=1, cols=500)


def test_split_manual_cuts_overlap_and_indices():
    doc = new_doc(3, size=(800, 600))
    out = layout.split_page_grid(doc, [0, 2], cols=1, rows=1, cuts_x=[0, 200, 200, 800])
    assert out == [0, 1, 3, 4] and doc.page_count == 5
    assert abs(doc[0].rect.width - 200) < 0.01 and abs(doc[1].rect.width - 600) < 0.01
    doc = new_doc(size=(800, 600))
    layout.split_page_grid(doc, [0], cols=2, overlap=20)
    assert abs(doc[0].rect.width - 410) < 0.01 and abs(doc[1].rect.width - 410) < 0.01


# ======================================================================== PAG-30
def _sheet_words(doc, i):
    return {w[4]: pymupdf.Rect(w[:4]) for w in doc[i].get_text("words")}


def test_nup_2up_10_pages():
    src = new_doc(10, text="P{n}")
    data = layout.nup(src, cols=2, rows=1, sheet_size=A4)
    out = pymupdf.open("pdf", data)
    assert out.page_count == 5
    for p in out:
        assert abs(p.rect.width - A4[1]) < 0.5 and abs(p.rect.height - A4[0]) < 0.5
    words = _sheet_words(out, 0)
    assert words["P1"].x0 < out[0].rect.width / 2 < words["P2"].x0
    assert src.page_count == 10


def test_nup_4up_6_pages_rows():
    src = new_doc(6, text="P{n}")
    out = pymupdf.open("pdf", layout.nup(src, cols=2, rows=2, order="rows"))
    assert out.page_count == 2
    w = _sheet_words(out, 1)
    mid_x, mid_y = out[1].rect.width / 2, out[1].rect.height / 2
    assert set(k for k in w if k.startswith("P")) == {"P5", "P6"}
    assert w["P5"].y1 < mid_y and w["P6"].y1 < mid_y
    assert w["P5"].x0 < mid_x < w["P6"].x0
    # per colonne e da destra a sinistra
    out = pymupdf.open("pdf", layout.nup(src, cols=2, rows=2, order="columns", rtl=True))
    w = _sheet_words(out, 0)
    assert w["P1"].x0 > mid_x and w["P1"].y1 < mid_y and w["P2"].y0 > mid_y and w["P3"].x0 < mid_x


def test_nup_shared_image_once_and_searchable():
    src = pymupdf.open()
    png = image_png(200, 150)
    for i in range(6):
        p = src.new_page(width=A4[0], height=A4[1])
        p.insert_image(pymupdf.Rect(100, 300, 400, 525), stream=png)
        p.insert_text((72, 100), f"Parola{i + 1}", fontsize=20)
    src = reload(src)
    out = pymupdf.open("pdf", layout.nup(src, cols=2, rows=2))
    images = [x for x in range(1, out.xref_length()) if out.xref_get_key(x, "Subtype")[1] == "/Image"]
    assert len(images) == 1
    hits = [i for i, p in enumerate(out) if p.search_for("Parola3")]
    assert hits == [0]


def test_nup_rotated_source_and_repeat_and_errors():
    src = new_doc(1, text="Ruotata")
    src[0].set_rotation(90)               # visualizzata orizzontale
    out = pymupdf.open("pdf", layout.nup(src, cols=1, rows=2, auto_rotate=False, landscape=False))
    w = _sheet_words(out, 0)["Ruotata"]
    assert w.height > w.width             # testo verticale come nella visualizzazione
    out = pymupdf.open("pdf", layout.nup(new_doc(2), cols=2, rows=4, repeat=True, border=True))
    assert out.page_count == 2
    assert len(out[0].search_for("Pagina 1")) == 8
    with pytest.raises(PageOpError):
        layout.nup(new_doc(1), cols=40, rows=40)
    plan = layout.nup_layout([A4] * 3, cols=2, rows=2)
    assert len(plan["sheets"]) == 1 and len(plan["sheets"][0]) == 3   # ultima cella vuota


# ======================================================================== follow-up
def _book_scan(fold_x=433.0, dark=True):
    doc = pymupdf.open()
    p = doc.new_page(width=842, height=595)
    bg, band = ((0.85, 0.85, 0.85), (0.25, 0.25, 0.25)) if dark else ((0.6, 0.6, 0.6), (1, 1, 1))
    p.draw_rect(p.rect, color=None, fill=bg)
    p.draw_rect(pymupdf.Rect(fold_x - 4, 0, fold_x + 4, 595), color=None, fill=band)
    p.insert_text((60, 100), "SINISTRA", fontsize=20)
    p.insert_text((600, 100), "DESTRA", fontsize=20)
    return doc


def test_detect_fold_dark_bright_and_none():
    assert abs(layout.detect_fold(_book_scan(450)[0]) - 450) <= 3
    assert abs(layout.detect_fold(_book_scan(380, dark=False)[0]) - 380) <= 3
    assert layout.detect_fold(new_doc(text=None)[0]) is None
    doc = _book_scan(450)
    out = layout.split_page_grid(doc, [0], fold=True)
    assert out == [0, 1]
    assert abs(doc[0].rect.width - 450) <= 3 and abs(doc[1].rect.width - (842 - 450)) <= 3


def test_split_remove_outside_text():
    flags = layout._TEXT_FLAGS
    doc = _book_scan()
    layout.split_page_grid(doc, [0], cols=2)
    left_all = doc[0].get_text("text", flags=flags, clip=pymupdf.INFINITE_RECT())
    assert "DESTRA" in left_all                    # senza opzione il testo esterno resta nel file
    doc = _book_scan()
    layout.split_page_grid(doc, [0], cols=2, remove_outside=True)
    doc = reload(doc)
    left = doc[0].get_text("text", flags=flags, clip=pymupdf.INFINITE_RECT())
    right = doc[1].get_text("text", flags=flags, clip=pymupdf.INFINITE_RECT())
    assert "SINISTRA" in left and "DESTRA" not in left
    assert "DESTRA" in right and "SINISTRA" not in right
    assert doc[0].get_images() == doc[1].get_images()   # immagini/risorse non toccate


def test_split_moves_form_fields_without_duplicates():
    doc = new_doc(size=(842, 595), text=None)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "nome"
    w.rect = pymupdf.Rect(600, 200, 780, 230)
    doc[0].add_widget(w)
    doc[0].add_text_annot((100, 100), "nota a sinistra")
    layout.split_page_grid(doc, [0], cols=2)
    doc = reload(doc)
    assert [x.field_name for x in doc[1].widgets()] == ["nome"]
    assert list(doc[0].widgets()) == []
    assert len(list(doc[0].annots())) == 1
    cat = doc.pdf_catalog()
    fields = doc.xref_get_key(cat, "AcroForm/Fields")[1]
    refs = [int(x) for x in layout._REF.findall(fields)]
    assert len(refs) == 1
    wx = next(doc[1].widgets()).xref
    assert doc.xref_get_key(wx, "P")[1] == f"{doc[1].xref} 0 R"


def test_resize_fill_mode_and_overflow_report():
    doc = new_doc()                                  # A4 verticale → quadrato: riempi taglia
    rep = {}
    layout.resize_pages(doc, [0], (400, 400), mode="fill", orientation="fixed", report=rep)
    assert rep["overflow"] == [0]
    page = doc[0]
    assert abs(page.rect.width - 400) < 0.5 and abs(page.rect.height - 400) < 0.5
    w = word_rect(page, "Orologeria")
    w_ref = word_rect(new_doc()[0], "Orologeria")
    assert abs(w.width / w_ref.width - 400 / A4[0]) < 0.01      # scala = max(sx, sy)
    rep = {}
    layout.resize_pages(new_doc(), [0], (400, 400), mode="scale", orientation="fixed", report=rep)
    assert rep["overflow"] == []


def test_resize_small_text_warning():
    doc = new_doc()                                  # corpo minimo 12 pt
    rep = {}
    layout.resize_pages(doc, [0], PAPER_SIZES["A6"], report=rep)     # scala ~0,5 → 6 pt: ok
    assert rep["small_text"] == []
    doc = new_doc()
    rep = {}
    layout.resize_pages(doc, [0], (100, 141), report=rep)            # scala ~0,17 → 2 pt
    assert rep["small_text"] == [0]
    assert layout.min_font_size(new_doc()[0]) == pytest.approx(12, abs=0.1)
