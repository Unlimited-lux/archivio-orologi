"""Test del motore di filigrane, sfondi, numerazioni ed etichette (stamp.py)."""

from __future__ import annotations

import datetime as dt

import pymupdf
import pytest

from foliant.modules.pages import stamp
from foliant.modules.pages.engine import PageOpError

# ------------------------------------------------------------------ helper

def make_doc(pages: int = 3, size=(595, 842), rotation: int = 0, crop=None) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), f"Pagina originale {i + 1}", fontsize=20)
        page.insert_text((72, 140), "Orologeria Verdi - contratto", fontsize=12)
        if crop:
            page.set_cropbox(pymupdf.Rect(crop))
        if rotation:
            page.set_rotation(rotation)
    return doc


def pixels(page: pymupdf.Page, dpi: int = 40) -> bytes:
    return page.get_pixmap(dpi=dpi, alpha=False).samples


def changed_bbox(before: pymupdf.Pixmap, after: pymupdf.Pixmap) -> tuple[float, float, float, float] | None:
    """Riquadro (frazioni 0–1 della pagina visualizzata) dei pixel cambiati."""
    w, h, n = after.width, after.height, after.n
    a, b = before.samples, after.samples
    xs, ys = [], []
    for y in range(h):
        row = y * after.stride
        for x in range(w):
            i = row + x * n
            if abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) > 30:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs) / w, min(ys) / h, (max(xs) + 1) / w, (max(ys) + 1) / h


def roundtrip(doc: pymupdf.Document) -> pymupdf.Document:
    return pymupdf.open("pdf", doc.tobytes(garbage=3))


def png_bytes(color=(255, 0, 0), size=(40, 20)) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), 0)
    pix.clear_with(0)
    for x in range(size[0]):
        for y in range(size[1]):
            pix.set_pixel(x, y, color)
    return pix.tobytes("png")


# ------------------------------------------------------------------ filigrane

def test_text_watermark_present_extractable_and_listed():
    doc = make_doc()
    assert stamp.add_watermark(doc, text="RISERVATO") == [0, 1, 2]
    doc = roundtrip(doc)
    for i in range(3):
        assert "RISERVATO" in doc[i].get_text()
        assert doc[i].search_for("RISERVATO")
        assert "Pagina originale" in doc[i].get_text()
    assert stamp.list_watermarks(doc) == {0: ["watermark"], 1: ["watermark"], 2: ["watermark"]}
    names = {info["name"] for info in doc.get_ocgs().values()}
    assert any(n.startswith("Foliant") for n in names)


def test_watermark_subset_of_pages():
    doc = make_doc(4)
    assert stamp.add_watermark(doc, [1, 3], text="X-MARK") == [1, 3]
    assert [("X-MARK" in doc[i].get_text()) for i in range(4)] == [False, True, False, True]


def test_remove_restores_rendering_exactly():
    doc = make_doc(2, rotation=90, crop=(10, 20, 580, 830))
    before = [pixels(doc[i]) for i in range(2)]
    stamp.add_watermark(doc, text="BOZZA", opacity=0.5)
    stamp.add_watermark(doc, image=png_bytes(), behind=True)
    assert all(pixels(doc[i]) != before[i] for i in range(2))
    doc = roundtrip(doc)
    assert stamp.remove_watermarks(doc) == 4
    assert stamp.list_watermarks(doc) == {}
    for i in range(2):
        assert pixels(doc[i]) == before[i]
        assert "BOZZA" not in doc[i].get_text()
    # nessun livello «Foliant» rimasto vuoto
    assert not doc.get_ocgs()
    doc = roundtrip(doc)
    assert pixels(doc[0]) == before[0]


def test_remove_only_selected_kind_and_pages():
    doc = make_doc(3)
    stamp.add_watermark(doc, text="FILIGRANA")
    stamp.add_page_numbers(doc)
    assert stamp.remove_watermarks(doc, [0], kind="watermark") == 1
    assert "FILIGRANA" not in doc[0].get_text()
    assert "FILIGRANA" in doc[1].get_text()
    assert stamp.list_watermarks(doc)[0] == ["numbers"]
    assert stamp.remove_watermarks(doc, kind=None) == 5
    assert stamp.list_watermarks(doc) == {}


def test_update_watermark_replaces():
    doc = make_doc(2)
    stamp.add_watermark(doc, text="VECCHIA")
    stamp.update_watermark(doc, None, text="NUOVA")
    for i in range(2):
        text = doc[i].get_text()
        assert "NUOVA" in text and "VECCHIA" not in text
    assert stamp.list_watermarks(doc) == {0: ["watermark"], 1: ["watermark"]}


def test_behind_vs_over():
    # Un rettangolo nero copre la zona centrale: la filigrana dietro non si vede.
    def doc_with_box():
        d = make_doc(1)
        d[0].draw_rect(pymupdf.Rect(0, 300, 595, 550), color=None, fill=(0, 0, 0))
        return d

    over, behind = doc_with_box(), doc_with_box()
    ref = pixels(over[0])
    opts = dict(color=(1, 0, 0), opacity=1.0, rotation=0, font_size=80)
    stamp.add_watermark(over, text="SOPRA", **opts)
    stamp.add_watermark(behind, text="SOTTO", behind=True, **opts)
    assert pixels(over[0]) != ref
    assert pixels(behind[0]) == ref  # coperta dal rettangolo del contenuto
    assert "SOTTO" in behind[0].get_text()
    assert stamp.list_watermarks(behind) == {0: ["watermark"]}


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_pages_stamp_upright_and_placed(rotation):
    doc = make_doc(1, rotation=rotation, crop=(15, 25, 560, 800))
    page = doc[0]
    ref = page.get_pixmap(dpi=30, alpha=False)
    stamp.add_page_numbers(doc, fmt="NUM {n}", position="bottom-right", font_size=14)
    after = doc[0].get_pixmap(dpi=30, alpha=False)
    box = changed_bbox(ref, after)
    assert box is not None
    x0, y0, x1, y1 = box
    # in basso a destra nella pagina come la si vede
    assert x0 > 0.6 and y0 > 0.85, box
    # testo orizzontale (più largo che alto) nella vista
    assert (x1 - x0) * after.width > (y1 - y0) * after.height
    # direzione del testo nella vista: orizzontale da sinistra a destra
    page = doc[0]
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if "NUM 1" in "".join(s["text"] for s in line["spans"]):
                d = pymupdf.Point(line["dir"]) * page.rotation_matrix - pymupdf.Point(0, 0) * page.rotation_matrix
                assert abs(d.x - 1) < 1e-3 and abs(d.y) < 1e-3
                break
        else:
            continue
        break
    else:
        pytest.fail("testo del numero non trovato")


def test_rotated_watermark_center():
    doc = make_doc(1, rotation=90)
    ref = doc[0].get_pixmap(dpi=30, alpha=False)
    stamp.add_watermark(doc, text="CENTRO", rotation=0, opacity=1, color=(1, 0, 0), font_size=40)
    box = changed_bbox(ref, doc[0].get_pixmap(dpi=30, alpha=False))
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    assert abs(cx - 0.5) < 0.05 and abs(cy - 0.5) < 0.05


def test_image_watermark():
    doc = make_doc(2)
    ref = pixels(doc[0])
    assert stamp.add_watermark(doc, image=png_bytes(), scale=0.3, rotation=30, opacity=0.8) == [0, 1]
    assert pixels(doc[0]) != ref
    assert stamp.list_watermarks(doc) == {0: ["watermark"], 1: ["watermark"]}
    stamp.remove_watermarks(doc)
    assert pixels(doc[0]) == ref


def test_image_watermark_from_path(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(png_bytes((0, 0, 255)))
    doc = make_doc(1)
    assert stamp.add_watermark(doc, image=str(path), rotation=0, position="top-left", opacity=1) == [0]
    pix = doc[0].get_pixmap(dpi=30, alpha=False)
    # pixel blu nell'angolo in alto a sinistra (margine 36 pt)
    x, y = int(pix.width * 0.15), int(pix.height * 0.06)
    r, g, b = pix.pixel(x, y)[:3]
    assert b > 200 and r < 60


def test_watermark_opacity_applied():
    doc = make_doc(1)
    stamp.add_background(doc, color=(0, 0, 0))  # fondo nero ovunque
    stamp.add_watermark(doc, image=png_bytes((255, 255, 255)), rotation=0, opacity=0.5, scale=0.5)
    pix = doc[0].get_pixmap(dpi=30, alpha=False)
    r, g, b = pix.pixel(pix.width // 2, pix.height // 2)[:3]
    assert 100 < r < 160  # bianco al 50% su nero


# ------------------------------------------------------------------ sfondo

def test_background_color_visible_where_blank():
    doc = make_doc(2)
    stamp.add_background(doc, color=(1, 1, 0))
    pix = doc[0].get_pixmap(dpi=30, alpha=False)
    assert pix.pixel(pix.width // 2, pix.height - 5)[:3] == (255, 255, 0)
    assert "Pagina originale 1" in doc[0].get_text()  # contenuto ancora sopra
    assert stamp.list_watermarks(doc) == {0: ["background"], 1: ["background"]}
    assert stamp.remove_watermarks(doc, kind="background") == 2
    pix = doc[0].get_pixmap(dpi=30, alpha=False)
    assert pix.pixel(pix.width // 2, pix.height - 5)[:3] == (255, 255, 255)


def test_background_image_and_rotated():
    doc = make_doc(1, rotation=270, crop=(20, 20, 500, 700))
    stamp.add_background(doc, image=png_bytes((0, 128, 0)))
    pix = doc[0].get_pixmap(dpi=30, alpha=False)
    for x, y in ((2, 2), (pix.width - 3, pix.height - 3), (pix.width - 3, 2)):
        r, g, b = pix.pixel(x, y)[:3]
        assert g > 100 and r < 30, (x, y)


# ------------------------------------------------------------------ numeri

def test_page_numbers_searchable():
    doc = make_doc(4)
    assert stamp.add_page_numbers(doc, fmt="Pagina {n} di {total}") == [0, 1, 2, 3]
    doc = roundtrip(doc)
    for i in range(4):
        assert doc[i].search_for(f"Pagina {i + 1} di 4"), i


def test_page_numbers_start_offset_and_skip_first():
    doc = make_doc(4)
    assert stamp.add_page_numbers(doc, fmt="p.{n}/{total}", start=10, skip_first=True) == [1, 2, 3]
    assert "p." not in doc[0].get_text()
    assert doc[1].search_for("p.11/13")
    assert doc[3].search_for("p.13/13")


def test_page_numbers_subset_and_label():
    doc = make_doc(5)
    stamp.set_page_labels(doc, [{"startpage": 0, "style": "r", "prefix": "", "firstpagenum": 1}])
    stamp.add_page_numbers(doc, [2, 3], fmt="{n} [{label}]", position="top-left")
    assert doc[2].search_for("1 [iii]")
    assert doc[3].search_for("2 [iv]")
    assert "[" not in doc[0].get_text()


def test_page_numbers_position_top_left():
    doc = make_doc(1)
    stamp.add_page_numbers(doc, fmt="ALTO {n}", position="top-left", margin=20)
    r = doc[0].search_for("ALTO 1")[0]
    assert r.x0 == pytest.approx(20, abs=1.5)
    assert r.y0 < 40


# ------------------------------------------------------------------ Bates

def test_bates_sequence():
    doc = make_doc(3)
    last = stamp.add_bates(doc, prefix="CAUSA-", suffix="/A", start=7, digits=5)
    assert last == "CAUSA-00009/A"
    for i in range(3):
        assert doc[i].search_for(f"CAUSA-{7 + i:05d}/A")
    assert stamp.list_watermarks(doc)[2] == ["bates"]
    assert stamp.remove_watermarks(doc, kind="bates") == 3


def test_bates_digits_too_few():
    doc = make_doc(3)
    with pytest.raises(PageOpError):
        stamp.add_bates(doc, start=98, digits=2)


# ------------------------------------------------------------------ intestazioni

def test_header_footer_placeholders():
    doc = make_doc(3)
    pages = stamp.add_header_footer(
        doc, header_left="{file}", header_right="Data {date}", footer_center="Pag. {n} di {total}",
        filename="/tmp/contratto.pdf", date_format="%Y-%m-%d", date=dt.date(2026, 3, 9))
    assert pages == [0, 1, 2]
    for i in range(3):
        page = doc[i]
        assert page.search_for("contratto.pdf")[0].y0 < 60
        assert page.search_for("Data 2026-03-09")[0].x1 > 500
        footer = page.search_for(f"Pag. {i + 1} di 3")[0]
        assert footer.y1 > 780
        assert abs((footer.x0 + footer.x1) / 2 - 297.5) < 2
    assert stamp.remove_watermarks(doc, kind="headerfooter") == 3
    assert "contratto.pdf" not in doc[0].get_text()


def test_header_footer_requires_text():
    with pytest.raises(PageOpError):
        stamp.add_header_footer(make_doc(1))


# ------------------------------------------------------------------ etichette

def test_page_labels_set_get_remove():
    doc = make_doc(6)
    stamp.set_page_labels(doc, [
        {"startpage": 0, "style": "r", "prefix": "", "firstpagenum": 1},
        {"startpage": 2, "style": "D", "prefix": "A-", "firstpagenum": 5},
    ])
    assert [doc[i].get_label() for i in range(6)] == ["i", "ii", "A-5", "A-6", "A-7", "A-8"]
    rules = stamp.get_page_labels(doc)
    assert rules[1] == {"startpage": 2, "style": "D", "prefix": "A-", "firstpagenum": 5}
    stamp.remove_page_labels(doc)
    assert stamp.get_page_labels(doc) == []
    assert doc[0].get_label() == ""


def test_page_labels_adds_first_rule():
    doc = make_doc(4)
    stamp.set_page_labels(doc, [{"startpage": 2, "style": "A", "prefix": "", "firstpagenum": 1}])
    assert [doc[i].get_label() for i in range(4)] == ["1", "2", "A", "B"]


@pytest.mark.parametrize("rule", [
    {"startpage": 9, "style": "D"},
    {"startpage": 0, "style": "X"},
    {"startpage": 0, "style": "D", "firstpagenum": 0},
    {"startpage": 0, "style": "", "prefix": ""},
])
def test_page_labels_invalid(rule):
    with pytest.raises(PageOpError):
        stamp.set_page_labels(make_doc(3), [rule])


def test_page_labels_duplicate_start():
    with pytest.raises(PageOpError):
        stamp.set_page_labels(make_doc(3), [{"startpage": 1, "style": "D"}, {"startpage": 1, "style": "r"}])


def test_preview_labels():
    assert stamp.preview_labels(4, [{"startpage": 0, "style": "R", "prefix": "", "firstpagenum": 3}]) == \
        ["III", "IV", "V", "VI"]


# ------------------------------------------------------------------ errori

@pytest.mark.parametrize("kwargs", [
    {},
    {"text": "   "},
    {"text": "A", "image": b"xx"},
    {"text": "A", "opacity": 0},
    {"text": "A", "opacity": 1.5},
    {"text": "A", "position": "altrove"},
    {"text": "A", "color": (2, 0, 0)},
    {"text": "A", "color": "rosso"},
    {"text": "A", "font_size": 0},
    {"text": "A", "font": "inesistente"},
    {"image": b"non un'immagine"},
    {"image": "/percorso/che/non/esiste.png"},
    {"image": b""},
])
def test_watermark_invalid_inputs(kwargs):
    doc = make_doc(1)
    before = doc.tobytes()
    with pytest.raises(PageOpError):
        stamp.add_watermark(doc, **kwargs)
    assert stamp.list_watermarks(doc) == {}
    assert len(doc.tobytes()) == len(before) or stamp.list_watermarks(doc) == {}


def test_invalid_pages():
    doc = make_doc(2)
    with pytest.raises(PageOpError):
        stamp.add_watermark(doc, [5], text="A")
    with pytest.raises(PageOpError):
        stamp.add_page_numbers(doc, [-1])
    with pytest.raises(PageOpError):
        stamp.add_page_numbers(doc, [])
    with pytest.raises(PageOpError):
        stamp.remove_watermarks(doc, kind="altro")
    with pytest.raises(PageOpError):
        stamp.add_background(doc)
    with pytest.raises(PageOpError):
        stamp.add_page_numbers(doc, fmt="")
    with pytest.raises(PageOpError):
        stamp.add_page_numbers(doc, start=-3)


def test_unknown_placeholders_left_untouched():
    doc = make_doc(1)
    stamp.add_page_numbers(doc, fmt="{x} {n}")
    assert doc[0].search_for("{x} 1")


def test_multiple_stamps_independent_removal_after_clean_contents():
    doc = make_doc(1)
    ref = pixels(doc[0])
    stamp.add_watermark(doc, text="UNO")
    stamp.add_bates(doc, prefix="B")
    # un altro modulo potrebbe unire i flussi di contenuto
    doc[0].clean_contents()
    assert sorted(stamp.list_watermarks(doc)[0]) == ["bates", "watermark"]
    assert stamp.remove_watermarks(doc, kind="watermark") == 1
    assert "UNO" not in doc[0].get_text()
    assert doc[0].search_for("B000001")
    stamp.remove_watermarks(doc, kind="bates")
    assert pixels(doc[0]) == ref


def test_long_text_shrinks_to_fit():
    doc = make_doc(1, size=(300, 400))
    stamp.add_watermark(doc, text="TESTO MOLTO LUNGO CHE NON CI STA", font_size=80, rotation=0)
    r = doc[0].search_for("TESTO MOLTO LUNGO")[0]
    assert r.x0 >= 30 and r.x1 <= 270


def test_blank_page_without_contents():
    doc = pymupdf.open()
    doc.new_page()
    ref = pixels(doc[0])
    stamp.add_page_numbers(doc, fmt="Pagina {n}")
    assert doc[0].search_for("Pagina 1")
    doc = roundtrip(doc)
    assert stamp.remove_watermarks(doc, kind="numbers") == 1
    assert pixels(doc[0]) == ref


def test_stamp_survives_undo_snapshot_roundtrip():
    doc = make_doc(2)
    stamp.add_bates(doc, prefix="X")
    data = doc.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
    again = pymupdf.open("pdf", data)
    assert stamp.list_watermarks(again) == {0: ["bates"], 1: ["bates"]}
