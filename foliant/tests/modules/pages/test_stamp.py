"""Test del motore di filigrane, sfondi, numerazioni ed etichette (stamp.py)."""

from __future__ import annotations

import datetime as dt
import json
import re

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
        assert any(r.y0 < 60 for r in page.search_for("contratto"))  # {file} = {nome}: senza estensione
        assert not page.search_for("contratto.pdf")
        assert page.search_for("Data 2026-03-09")[0].x1 > 500
        footer = page.search_for(f"Pag. {i + 1} di 3")[0]
        assert footer.y1 > 780
        assert abs((footer.x0 + footer.x1) / 2 - 297.5) < 2
    assert stamp.remove_watermarks(doc, kind="headerfooter") == 3
    assert not any(r.y0 < 60 for r in doc[0].search_for("contratto"))


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


def test_unknown_placeholders_rejected():
    """PAG-32: segnaposto sconosciuto → errore; parentesi letterali raddoppiate."""
    doc = make_doc(1)
    with pytest.raises(PageOpError, match="x"):
        stamp.add_page_numbers(doc, fmt="{x} {n}")
    assert stamp.list_watermarks(doc) == {}
    stamp.add_page_numbers(doc, fmt="{{pag}} {pagina:3}")
    assert doc[0].search_for("{pag} 001")


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


# =====================================================================
# Criteri di accettazione PAG-33/34/36/38 e posizionamento esatto
# =====================================================================

def render_usage(page: pymupdf.Page, usage: str = "View", zoom: float = 0.5) -> pymupdf.Pixmap:
    """Rendering con l'evento di contenuto facoltativo indicato («View» o «Print»)."""
    m = pymupdf.mupdf
    w, h = page.rect.width * zoom, page.rect.height * zoom
    pix = m.fz_new_pixmap_with_bbox(m.fz_device_rgb(), m.fz_round_rect(m.FzRect(0, 0, w, h)),
                                    m.FzSeparations(), 0)
    m.fz_clear_pixmap_with_value(pix, 255)
    dev = m.fz_new_draw_device(m.FzMatrix(), pix)
    ctm = m.FzMatrix(zoom, 0, 0, zoom, 0, 0)
    m.pdf_run_page_with_usage(m.pdf_page_from_fz_page(page.this), dev,
                              m.fz_concat(page_ctm(page), ctm), usage, m.FzCookie())
    m.fz_close_device(dev)
    return pymupdf.Pixmap(pix)


def page_ctm(page):
    m = pymupdf.mupdf
    mb, ctm = m.FzRect(), m.FzMatrix()
    m.pdf_page_transform(m.pdf_page_from_fz_page(page.this), mb, ctm)
    return ctm


def letterhead(pages=2) -> bytes:
    d = pymupdf.open()
    colors = [(1, 0, 0), (0, 0, 1), (0, 1, 0)]
    for i in range(pages):
        p = d.new_page(width=595, height=842)
        p.draw_rect(pymupdf.Rect(0, 0, 595, 80), color=None, fill=colors[i])
        p.insert_text((40, 60), f"CARTA {i + 1}", fontsize=20, color=(1, 1, 1))
    return d.tobytes()


def image_xrefs(doc: pymupdf.Document) -> list[int]:
    return [x for x in range(1, doc.xref_length()) if doc.xref_get_key(x, "Subtype")[1] == "/Image"]


# ------------------------------------------------------------- PAG-38

def test_pag38_background_fff5cc_text_unchanged():
    doc = make_doc(3)
    before = [doc[i].get_pixmap(dpi=30, alpha=False) for i in range(3)]
    stamp.add_background(doc, color=(1, 0xF5 / 255, 0xCC / 255), opacity=1.0)
    for i in range(3):
        a, b = before[i], doc[i].get_pixmap(dpi=30, alpha=False)
        white = text = 0
        for y in range(0, a.height, 3):
            for x in range(0, a.width, 3):
                pa, pb = a.pixel(x, y), b.pixel(x, y)
                if pa == (255, 255, 255):
                    assert pb == (255, 245, 204), (x, y, pb)
                    white += 1
                elif max(pa) < 60:  # testo nero pieno
                    assert max(pb) < 60
                    text += 1
        assert white > 100
    assert "Pagina originale 1" in doc[0].get_text()


def test_pag38_letterhead_first_rest_rule():
    doc = make_doc(4)
    assert stamp.add_background(doc, source_pdf=letterhead(2), first_rest=True) == [0, 1, 2, 3]
    tops = [doc[i].get_pixmap(dpi=20, alpha=False).pixel(5, 5) for i in range(4)]
    assert tops[0] == (255, 0, 0)
    assert tops[1:] == [(0, 0, 255)] * 3
    assert "CARTA 1" in doc[0].get_text() and "CARTA 2" in doc[3].get_text()
    # la pagina sorgente è incorporata una sola volta per tipo (2 Form XObject in tutto)
    forms = {doc.xref_get_key(x, "Subtype")[1] for x in range(1, doc.xref_length())}
    assert "/Form" in forms


def test_pag38_remove_restores_rendering():
    doc = make_doc(2, rotation=90)
    ref = [pixels(doc[i]) for i in range(2)]
    stamp.add_background(doc, source_pdf=letterhead(1), scale="fit")
    stamp.add_background(doc, color=(0.9, 0.9, 1))
    doc = roundtrip(doc)
    assert stamp.remove_watermarks(doc, kind="background") == 4
    assert [pixels(doc[i]) for i in range(2)] == ref


def test_pag38_screen_only_hidden_in_print():
    doc = make_doc(1)
    stamp.add_background(doc, color=(1, 0, 0), visibility="screen")
    doc = roundtrip(doc)
    view, prn = render_usage(doc[0], "View"), render_usage(doc[0], "Print")
    assert view.pixel(5, view.height - 5)[:3] == (255, 0, 0)
    assert prn.pixel(5, prn.height - 5)[:3] == (255, 255, 255)


def test_pag38_scale_modes():
    doc = make_doc(1)
    small = pymupdf.open()
    sp = small.new_page(width=200, height=100)
    sp.draw_rect(sp.rect, color=None, fill=(0, 0, 0))
    src = small.tobytes()

    def black_box(d):
        pix = d[0].get_pixmap(dpi=36, alpha=False)  # 0.5 px per punto
        box = changed_bbox(make_doc(1)[0].get_pixmap(dpi=36, alpha=False), pix)
        return [round(v * s) for v, s in zip(box, (595, 842, 595, 842))]

    d = make_doc(1)
    stamp.add_background(d, source_pdf=src, scale="actual", position="top-left")
    x0, y0, x1, y1 = black_box(d)
    assert abs((x1 - x0) - 200) <= 4 and abs((y1 - y0) - 100) <= 4 and x0 <= 2 and y0 <= 2
    d = make_doc(1)
    stamp.add_background(d, source_pdf=src, scale="fit")
    x0, y0, x1, y1 = black_box(d)
    assert abs((x1 - x0) - 595) <= 4 and abs((y1 - y0) - 297.5) <= 5  # proporzioni 2:1
    d = make_doc(1)
    stamp.add_background(d, source_pdf=src, scale="percent", percent=50, position="bottom-right")
    x0, y0, x1, y1 = black_box(d)
    assert abs((x1 - x0) - 100) <= 4 and x1 >= 592 and y1 >= 839
    with pytest.raises(PageOpError):
        stamp.add_background(make_doc(1), source_pdf=src, scale="enorme")


def test_pag38_image_background_embedded_once():
    doc = make_doc(5)
    stamp.add_background(doc, image=png_bytes((0, 128, 0)), scale="fill")
    doc = roundtrip(doc)
    assert len(image_xrefs(doc)) == 1


def test_pag38_artifact_background():
    doc = make_doc(1)
    stamp.add_background(doc, color=(1, 1, 0))
    assert b"/Artifact <</Type/Background>> BDC" in doc[0].read_contents()


def test_scan_detection_for_warning():
    doc = make_doc(2)
    doc[1].insert_image(doc[1].rect, stream=png_bytes((200, 200, 200)), keep_proportion=False)
    assert stamp.opaque_scan_pages(doc) == [1]


# ------------------------------------------------------------- PAG-36

def test_pag36_riservato_ten_pages_text_selectable():
    doc = make_doc(10)
    pages = stamp.add_watermark(doc, text="RISERVATO", opacity=0.3, rotation=45, position="center")
    assert pages == list(range(10))
    for i in range(10):
        assert doc[i].search_for("RISERVATO")
        assert doc[i].search_for(f"Pagina originale {i + 1}")  # testo della pagina ancora selezionabile
    assert b"/Artifact <</Type/Pagination/Subtype/Watermark>> BDC" in doc[0].read_contents()


def test_pag36_behind_text_covers_watermark():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 450), "MMMMMMMM", fontsize=90)
    ref = page.get_pixmap(dpi=30, alpha=False)
    stamp.add_watermark(doc, text="SOTTO", rotation=0, font_size=200, color=(1, 0, 0), opacity=1, behind=True)
    after = doc[0].get_pixmap(dpi=30, alpha=False)
    black = red = 0
    for y in range(ref.height):
        for x in range(ref.width):
            if ref.pixel(x, y) == (0, 0, 0):
                assert after.pixel(x, y) == (0, 0, 0)
                black += 1
            elif after.pixel(x, y) == (255, 0, 0):
                red += 1
    assert black > 50 and red > 50


def test_pag36_tile_png_embedded_once():
    doc = make_doc(4)
    stamp.add_watermark(doc, image=png_bytes(), tile=True, spacing=30, scale=0.1, rotation=30)
    stamp.add_watermark(doc, image=png_bytes((0, 0, 255)), tile=True, spacing=30, scale=0.1, rotation=0, opacity=1)
    doc = roundtrip(doc)
    assert len(image_xrefs(doc)) == 2  # una per immagine, non una per copia o per pagina
    # più copie per pagina
    pix = doc[0].get_pixmap(dpi=20, alpha=False)
    blue = sum(1 for y in range(0, pix.height, 2) for x in range(0, pix.width, 2)
               if pix.pixel(x, y)[2] > 150 and pix.pixel(x, y)[0] < 120)
    assert blue > 20


def test_pag36_text_tile():
    doc = make_doc(1)
    stamp.add_watermark(doc, text="COPIA", tile=True, font_size=24, spacing=40)
    assert len(doc[0].search_for("COPIA")) >= 6


def test_pag36_print_only():
    doc = make_doc(1)
    stamp.add_watermark(doc, text="STAMPA", rotation=0, opacity=1, color=(1, 0, 0), font_size=80,
                        visibility="print")
    doc = roundtrip(doc)
    ref = make_doc(1)
    assert render_usage(doc[0], "View").samples == render_usage(ref[0], "View").samples
    assert render_usage(doc[0], "Print").samples != render_usage(ref[0], "Print").samples
    # gli ON/OFF della configurazione predefinita non vengono toccati: pubblicato con /Usage e /AS
    cat = doc.xref_object(doc.pdf_catalog())
    assert "/AS" in cat and "/Print" in cat


def test_pag36_offset_from_anchor():
    a, b = make_doc(1), make_doc(1)
    stamp.add_watermark(a, text="SPOSTA", rotation=0, position="top-left")
    stamp.add_watermark(b, text="SPOSTA", rotation=0, position="top-left", offset=(50, 20))
    ra, rb = a[0].search_for("SPOSTA")[0], b[0].search_for("SPOSTA")[0]
    assert rb.x0 - ra.x0 == pytest.approx(50, abs=0.5)
    assert rb.y0 - ra.y0 == pytest.approx(20, abs=0.5)


def test_pag36_watermark_from_pdf_page():
    doc = make_doc(2, rotation=90)
    stamp.add_watermark(doc, source_pdf=letterhead(2), source_page=1, scale=0.5, rotation=0)
    assert "CARTA 2" in doc[1].get_text()
    with pytest.raises(PageOpError):
        stamp.add_watermark(make_doc(1), source_pdf=letterhead(1), source_page=4)
    with pytest.raises(PageOpError):
        stamp.add_watermark(make_doc(1), source_pdf=b"non un pdf")


def test_pag36_list_and_remove_individually():
    doc = make_doc(3)
    stamp.add_watermark(doc, text="PRIMA", rotation=0, position="top-center")
    stamp.add_watermark(doc, [1, 2], text="SECONDA", rotation=0, position="bottom-center", opacity=0.8)
    stamps = stamp.list_stamps(doc, "watermark")
    assert [s["params"]["text"] for s in stamps] == ["PRIMA", "SECONDA"]
    assert stamps[1]["pages"] == [1, 2]
    assert stamps[1]["params"]["opacity"] == pytest.approx(0.8)
    assert stamp.remove_stamp(doc, stamps[0]["id"]) == 3
    assert "PRIMA" not in doc[1].get_text() and "SECONDA" in doc[1].get_text()
    # modifica di una sola filigrana: niente duplicati
    stamp.update_watermark(doc, [1, 2], stamp_id=stamps[1]["id"], text="TERZA", rotation=0)
    assert [s["params"]["text"] for s in stamp.list_stamps(doc)] == ["TERZA"]


def test_pag36_pdfa1_detection():
    doc = make_doc(1)
    assert stamp.pdfa_part(doc) is None
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/'
                         '02/22-rdf-syntax-ns#"><rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
                         'pdfaid:part="1" pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>')
    assert stamp.pdfa_part(doc) == "1B"


def test_pag36_watermark_fields():
    doc = make_doc(2)
    doc.set_metadata({"title": "Contratto 42"})
    stamp.add_watermark(doc, text="{titolo} - p. {pagina}/{totale}", rotation=0)
    assert doc[1].search_for("Contratto 42 - p. 2/2")


# ------------------------------------------------------------- PAG-33

def test_pag33_start_from_page_two():
    doc = make_doc(10)
    stamp.add_page_numbers(doc, fmt="{pagina}", position="bottom-center", start_from=1, start=1)
    footer = lambda i: [w[4] for w in doc[i].get_text("words") if w[1] > 780]  # noqa: E731
    assert footer(0) == []
    assert footer(1) == ["1"]
    assert footer(9) == ["9"]


def test_pag33_exclusions_and_styles():
    doc = make_doc(5)
    stamp.add_page_numbers(doc, fmt="- {pagina} -", exclude=[2], style="r")
    words = [" ".join(w[4] for w in doc[i].get_text("words") if w[1] > 780) for i in range(5)]
    assert words == ["- i -", "- ii -", "", "- iii -", "- iv -"]


def test_pag33_mirror_outer():
    doc = make_doc(4)
    stamp.add_page_numbers(doc, fmt="N{pagina}", position="bottom-center", mirror="outer")
    for i in range(4):
        r = doc[i].search_for(f"N{i + 1}")[0]
        if (i + 1) % 2:
            assert r.x1 > 500, i  # dispari a destra
        else:
            assert r.x0 < 100, i  # pari a sinistra


def test_pag33_print_label():
    doc = make_doc(3)
    stamp.set_page_labels(doc, [{"startpage": 0, "style": "A", "prefix": "All. ", "firstpagenum": 1}])
    stamp.add_page_numbers(doc, use_label=True)
    assert doc[1].search_for("All. B")


def test_pag33_tagged_pdf_numbers_are_artifacts():
    doc = make_doc(2)
    cat = doc.pdf_catalog()
    struct = doc.get_new_xref()
    doc.update_object(struct, "<</Type/StructTreeRoot/K[]>>")
    doc.xref_set_key(cat, "StructTreeRoot", f"{struct} 0 R")
    doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")
    stamp.add_page_numbers(doc, position="top-right")
    stamp.add_page_numbers(doc, position="bottom-center", fmt="Pag. {pagina}")
    doc = roundtrip(doc)
    content = doc[0].read_contents()
    # ogni disegno di Foliant è racchiuso in un contenuto marcato /Artifact (niente MCID)
    blocks = re.findall(rb"/Artifact <<([^>]*)>> BDC q /FolStamp_numbers_\w+ Do Q EMC", content)
    assert blocks == [b"/Type/Pagination/Subtype/Header", b"/Type/Pagination/Subtype/Footer"]
    assert b"MCID" not in content
    struct_xref = int(doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    assert doc.xref_get_key(struct_xref, "K")[1].replace(" ", "") == "[]"
    # la numerazione si toglie e il rendering torna come prima
    assert stamp.remove_watermarks(doc, kind="numbers") == 4


def test_pag33_replace_existing():
    doc = make_doc(3)
    stamp.add_page_numbers(doc, fmt="A{pagina}")
    stamp.add_page_numbers(doc, fmt="B{pagina}", replace=True)
    assert "A1" not in doc[0].get_text() and doc[0].search_for("B1")
    assert stamp.list_watermarks(doc)[0] == ["numbers"]


# ------------------------------------------------------------- PAG-34

def test_pag34_title_and_page_of_total():
    doc = make_doc(5)
    doc.set_metadata({"title": "Relazione"})
    stamp.add_header_footer(doc, header_center="{titolo}", footer_right="Pagina {pagina} di {totale}")
    for i in range(5):
        r = doc[i].search_for("Relazione")[0]
        assert r.y1 < 60
    assert doc[4].search_for("Pagina 5 di 5")[0].y0 > 780


def test_pag34_modify_without_duplicates():
    doc = make_doc(3)
    stamp.add_header_footer(doc, header_center="Originale")
    params = stamp.stamp_params(doc, "headerfooter")
    kwargs = stamp.kwargs_from_params("headerfooter", params)
    kwargs["header_center"] = "Bozza"
    stamp.add_header_footer(doc, replace=True, **kwargs)
    for i in range(3):
        text = doc[i].get_text()
        assert text.count("Bozza") == 1 and "Originale" not in text
    assert len(stamp.list_stamps(doc, "headerfooter")) == 1


def test_pag34_first_page_different_empty():
    doc = make_doc(4)
    stamp.add_header_footer(doc, header_center="INTESTAZIONE", first={})
    assert not doc[0].search_for("INTESTAZIONE")
    assert all(doc[i].search_for("INTESTAZIONE") for i in range(1, 4))
    assert 0 not in stamp.list_watermarks(doc)


def test_pag34_even_odd_and_multiline():
    doc = make_doc(4)
    stamp.add_header_footer(doc, footer_right="Dispari\n{pagina}", even={"footer_left": "Pari\n{pagina}"})
    odd, even = doc[0].search_for("Dispari")[0], doc[1].search_for("Pari")[0]
    assert odd.x1 > 500 and even.x0 < 100
    num = [r for r in doc[0].search_for("1") if r.y0 > odd.y0 + 8]
    assert num, "seconda riga sotto la prima"
    assert not doc[1].search_for("Dispari")


def test_pag34_template_same_result_on_other_document():
    a, b = make_doc(2), make_doc(2)
    stamp.add_header_footer(a, header_left="Orologi & Co.", footer_center="{pagina}", font_size=14,
                            color=(0, 0, 0.5), margin=50, underline=True, opacity=0.8)
    template = stamp.kwargs_from_params("headerfooter", stamp.stamp_params(a, "headerfooter"))
    template = json.loads(json.dumps(template))  # come salvato nelle impostazioni
    stamp.add_header_footer(b, **template)
    assert a[0].search_for("Orologi & Co.") == b[0].search_for("Orologi & Co.")
    assert pixels(a[1]) == pixels(b[1])


def test_pag34_logo_underline_opacity():
    doc = make_doc(1)
    ref = doc[0].get_pixmap(dpi=40, alpha=False)
    stamp.add_header_footer(doc, header_right="Titolo", underline=True, logo=png_bytes((0, 0, 255), (60, 30)),
                            logo_position="top-left", logo_height=30, opacity=0.5)
    pix = doc[0].get_pixmap(dpi=40, alpha=False)
    # logo semitrasparente in alto a sinistra
    r, g, b = pix.pixel(int(pix.width * 36 / 595) + 3, int(pix.height * 40 / 842))
    assert b > 200 and 100 < r < 160
    box = changed_bbox(ref, pix)
    assert box[1] < 0.1  # tutto in alto
    t = doc[0].search_for("Titolo")[0]
    # sottolineatura: pixel cambiati appena sotto la linea di base del testo
    xs = range(int(t.x0 * 40 / 72) + 1, int(t.x1 * 40 / 72) - 1)
    rows = range(int((t.y1 - 3) * 40 / 72), int((t.y1 + 1) * 40 / 72) + 1)
    changed = [y for y in rows if sum(pix.pixel(x, y) != ref.pixel(x, y) for x in xs) >= len(xs) * 0.9]
    assert changed, "riga continua sotto il testo"


# ------------------------------------------------------------- posizionamento esatto

def odd_geometry_doc(rotation: int, kind: str) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Contenuto", fontsize=20)
    if kind == "shifted":
        doc.xref_set_key(page.xref, "MediaBox", "[100 200 695 1042]")
        doc.xref_set_key(page.xref, "CropBox", "[130 250 665 1000]")
    elif kind == "larger":
        doc.xref_set_key(page.xref, "CropBox", "[-40 -30 635 872]")  # più grande del MediaBox
    if rotation:
        doc.xref_set_key(page.xref, "Rotate", str(rotation))
    return doc


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("kind", ["shifted", "larger"])
def test_readback_positions_on_odd_geometry(rotation, kind):
    from foliant.api import placement
    doc = odd_geometry_doc(rotation, kind)
    stamp.add_header_footer(doc, header_left="ALTOSX", footer_right="BASSODX", margin=30, font_size=12)
    page = doc[0]
    to_display = placement.page_to_display_matrix(page)
    dw, dh = (placement.page_rect(page) * to_display).width, (placement.page_rect(page) * to_display).height
    tl = page.search_for("ALTOSX")[0] * to_display
    br = page.search_for("BASSODX")[0] * to_display
    assert tl.x0 == pytest.approx(30, abs=1.5) and tl.y0 == pytest.approx(30, abs=4)
    assert br.x1 == pytest.approx(dw - 30, abs=1.5) and br.y1 == pytest.approx(dh - 30, abs=4)
    # testo diritto nella pagina visualizzata
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if "ALTOSX" in "".join(s["text"] for s in line["spans"]):
                d = pymupdf.Point(line["dir"]) * to_display - pymupdf.Point(0, 0) * to_display
                assert abs(d.x - 1) < 1e-3 and abs(d.y) < 1e-3
    # e la rimozione ripristina il rendering
    ref = odd_geometry_doc(rotation, kind)
    stamp.remove_watermarks(doc, kind=None)
    assert pixels(doc[0]) == pixels(ref[0])


@pytest.mark.parametrize("rotation", [90, 270])
def test_background_covers_visible_area_on_odd_geometry(rotation):
    doc = odd_geometry_doc(rotation, "larger")
    stamp.add_background(doc, color=(0, 1, 0))
    pix = doc[0].get_pixmap(dpi=20, alpha=False)
    for x, y in ((1, 1), (pix.width - 2, 1), (1, pix.height - 2), (pix.width - 2, pix.height - 2)):
        assert pix.pixel(x, y) == (0, 255, 0), (x, y)
