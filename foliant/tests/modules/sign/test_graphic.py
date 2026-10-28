"""Firma grafica: tracciati, testo, immagini, inserimento e ripetizione (SIG-01/02/03/05/06)."""

from __future__ import annotations

import io
import math

import numpy as np
import pymupdf
import pytest
from PIL import Image

from foliant.modules.sign import graphic as g


def _wave(pressure=lambda i: 1.0, n=80):
    return [[(20 + i * 4, 60 + 25 * math.sin(i / 6), pressure(i)) for i in range(n)]]


def _drawn_item(raw=None, base=3.0):
    strokes = g.process_strokes(raw or _wave(), base_width=base)
    return g.SignatureItem(kind="firma", origin="disegnata", art=g.strokes_to_art(strokes))


_PAGES = []   # le annotazioni restano valide solo finché la pagina è viva


def _stamp_annots(pdf, page=0):
    p = pdf[page]
    _PAGES.append(p)
    return [a for a in p.annots() if a.type[0] == pymupdf.PDF_ANNOT_STAMP]


def test_smoothing_decimates_and_keeps_endpoints():
    raw = [[(i * 0.1, 0.0, 1.0) for i in range(500)]]  # campionamento molto fitto
    out = g.process_strokes(raw)
    assert len(out[0]) < 20
    assert out[0][0][:2] == pytest.approx((0.0, 0.0))
    assert out[0][-1][:2] == pytest.approx((49.9, 0.0))


def test_decimation_error_below_half_pixel():
    pts = [(i, 10 * math.sin(i / 10), 1.0) for i in range(300)]
    dec = g.decimate(pts, 0.4)
    for p in pts:
        d = min(g._perp_distance(p, a, b) for a, b in zip(dec, dec[1:]))
        assert d < 0.5


def test_drawn_signature_creates_vector_stamp_matching_bbox():
    """SIG-01: annotazione /Stamp con aspetto solo vettoriale e riquadro del tracciato (±1 pt)."""
    item = _drawn_item()
    pdf = pymupdf.open()
    pdf.new_page()
    w, h = 150.0, 150.0 / item.aspect
    rect = pymupdf.Rect(100, 200, 100 + w, 200 + h)
    xref = g.insert_signature(pdf, 0, rect, item)
    data = pdf.tobytes()
    doc = pymupdf.open("pdf", data)
    annots = _stamp_annots(doc)
    assert len(annots) == 1
    a = annots[0]
    assert abs(a.rect.x0 - rect.x0) < 1 and abs(a.rect.y1 - rect.y1) < 1
    assert abs(a.rect.width - w) < 1 and abs(a.rect.height - h) < 1
    ap = int(doc.xref_get_key(a.xref, "AP/N")[1].split()[0])
    stream = doc.xref_stream(ap).decode()
    assert " S" in stream and "Do" not in stream and "Tf" not in stream   # solo tracciati
    assert doc.xref_get_key(a.xref, "Subj")[1] == "Firma"
    assert doc.xref_get_key(a.xref, "F")[1] == "4"
    # il contenuto disegnato tocca i bordi del riquadro (ritaglio al tracciato)
    pix = doc[0].get_pixmap(dpi=144, clip=a.rect)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    ink = np.argwhere(arr[..., 0] < 128)
    assert ink[:, 1].min() <= 3 and ink[:, 1].max() >= pix.width - 4
    assert xref > 0


def test_pressure_increases_width_monotonically():
    raw = [[(10 + i * 5, 50, 0.1 + 0.9 * i / 19) for i in range(20)]]
    item = _drawn_item(raw)
    widths = [p[2] for p in item.art.items[0][1]]
    assert all(b >= a - 1e-9 for a, b in zip(widths, widths[1:]))
    assert widths[-1] > widths[0] * 2
    content = g.art_content(item.art, 300, 100, "#000000").decode()
    ws = [float(tok.split()[0]) for tok in content.split("\n")[1:]]
    assert all(b >= a - 1e-3 for a, b in zip(ws, ws[1:]))


def test_library_svg_has_no_timing_and_round_trips():
    item = _drawn_item()
    svg = g.art_to_svg(item.art)
    low = svg.lower()
    assert "time" not in low and "velocity" not in low and "speed" not in low
    back = g.svg_to_art(svg)
    assert back.aspect == pytest.approx(item.art.aspect, rel=1e-3)
    assert len(back.items[0][1]) == len(item.art.items[0][1])


def test_typed_styles_at_least_eight(qapp):
    import time
    t0 = time.perf_counter()
    styles = g.typed_styles()
    arts = [g.text_to_art("Mario Rossi", s) for s in styles]
    assert len(styles) >= 8
    assert len({s.id for s in styles}) == len(styles)
    assert all(a.width > 0 for a in arts)
    assert time.perf_counter() - t0 < 3.0


def test_typed_signature_has_no_fonts(qapp):
    style = g.typed_styles()[0]
    art = g.text_to_art("Mario Rossi", style, slant=8)
    item = g.SignatureItem(origin="digitata", art=art, text="Mario Rossi", style_id=style.id)
    pdf = pymupdf.open()
    pdf.new_page()
    g.insert_signature(pdf, 0, pymupdf.Rect(100, 100, 300, 100 + 200 / art.aspect), item)
    doc = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    assert doc[0].get_fonts(full=True) == []
    a = _stamp_annots(doc)[0]
    ap = int(doc.xref_get_key(a.xref, "AP/N")[1].split()[0])
    assert "Font" not in doc.xref_object(ap)
    assert "Tf" not in doc.xref_stream(ap).decode()
    assert "Mario Rossi" in a.info["content"]


def test_missing_glyphs_reported(qapp):
    fallback = g.style_by_id(g.fallback_family() + "|i")
    assert g.missing_characters("Łukasz Żółć", fallback) == []
    fake = g.TypedStyle("NonEsiste|n", "Font Che Non Esiste")
    assert "Ł" in g.missing_characters("Łukasz Żółć", fake)


def _noisy_signature_png(transparent=False):
    rng = np.random.default_rng(1)
    h, w = 120, 360
    img = 255 - rng.integers(0, 11, (h, w)).astype(np.int16)        # sfondo bianco ±10
    stroke = np.zeros((h, w), bool)
    for x in range(30, 330):
        y = int(60 + 30 * math.sin(x / 25))
        stroke[y - 3:y + 3, x] = True
    img[stroke] = rng.integers(0, 11, stroke.sum())
    rgb = np.dstack([img] * 3).clip(0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG")
    return buf.getvalue(), stroke


def test_background_removal_threshold():
    """SIG-03: ≥99% dello sfondo trasparente, ≥95% dei tratti opachi."""
    data, stroke = _noisy_signature_png()
    img = g.load_image(data)
    t = g.auto_threshold(img)
    out = g.remove_background(img, t)
    alpha = np.asarray(g.remove_background(img, t, crop=False))[..., 3]
    assert (alpha[~stroke] == 0).mean() >= 0.99
    assert (alpha[stroke] >= 250).mean() >= 0.95
    assert out.mode == "RGBA" and out.width < 360   # ritagliata al contenuto


def test_unsupported_image_format_reports_detected_format():
    with pytest.raises(g.GraphicSignatureError) as exc:
        g.load_image(b"%PDF-1.7 non un'immagine")
    assert "PDF" in str(exc.value)


def test_image_signature_has_smask_and_max_300ppi():
    data, _s = _noisy_signature_png()
    img = g.remove_background(g.load_image(data))
    item = g.SignatureItem(origin="immagine", png=g.png_bytes(img))
    pdf = pymupdf.open()
    pdf.new_page()
    rect = pymupdf.Rect(50, 50, 50 + 60, 50 + 60 / item.aspect)   # piccola: forza il ricampionamento
    g.insert_signature(pdf, 0, rect, item)
    doc = pymupdf.open("pdf", pdf.tobytes(garbage=3, deflate=True))
    imgs = [x for x in range(1, doc.xref_length()) if doc.xref_get_key(x, "Subtype")[1] == "/Image"
            and doc.xref_get_key(x, "SMask")[0] == "xref"]
    assert imgs
    width = int(doc.xref_get_key(imgs[0], "Width")[1])
    assert width / (rect.width / 72) <= 300.5


def test_pdfa1_uses_one_bit_mask():
    data, _s = _noisy_signature_png()
    item = g.SignatureItem(origin="immagine", png=g.png_bytes(g.remove_background(g.load_image(data))))
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                         '<rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="1"/>'
                         '</rdf:RDF></x:xmpmeta>')
    g.insert_signature(pdf, 0, pymupdf.Rect(50, 50, 200, 100), item)
    masks = [x for x in range(1, pdf.xref_length()) if pdf.xref_get_key(x, "ImageMask")[1] == "true"]
    smasks = [x for x in range(1, pdf.xref_length()) if pdf.xref_get_key(x, "SMask")[0] == "xref"]
    assert masks and not smasks


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_signature_upright(rotation):
    """SIG-05: su pagina ruotata la firma resta diritta nella stessa posizione visiva."""
    item = _drawn_item()
    pdf = pymupdf.open()
    ref = pdf.new_page()
    page = pdf.new_page()
    page.set_rotation(rotation)
    vis = pymupdf.Rect(100, 100, 250, 100 + 150 / item.aspect)
    g.insert_signature(pdf, 0, vis, item)
    g.insert_signature(pdf, 1, g.unrotated_rect(pdf[1], vis), item)
    doc = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    a = doc[0].get_pixmap(dpi=72, clip=vis, annots=True)
    b = doc[1].get_pixmap(dpi=72, clip=vis, annots=True)
    assert (a.width, a.height) == (b.width, b.height)
    da = np.frombuffer(a.samples, np.uint8).astype(int)
    db = np.frombuffer(b.samples, np.uint8).astype(int)
    assert np.abs(da - db).mean() < 4
    assert (da < 128).sum() > 50
    del ref


def test_move_resize_delete_and_embed():
    item = _drawn_item()
    pdf = pymupdf.open()
    pdf.new_page()
    xref = g.insert_signature(pdf, 0, pymupdf.Rect(100, 100, 250, 150), item)
    r = g.set_signature_rect(pdf, 0, xref, pymupdf.Rect(-50, 100, 100, 150))   # oltre il bordo
    assert r.x0 == 0
    tiny = g.set_signature_rect(pdf, 0, xref, pymupdf.Rect(10, 10, 12, 12))
    assert tiny.width >= g.MIN_SIDE and tiny.height >= g.MIN_SIDE
    g.set_signature_rect(pdf, 0, xref, pymupdf.Rect(100, 300, 250, 350))
    before = pdf[0].get_pixmap(dpi=72).samples
    g.embed_signature(pdf, 0, xref)
    doc = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    assert not _stamp_annots(doc)
    after = doc[0].get_pixmap(dpi=72).samples
    diff = np.abs(np.frombuffer(before, np.uint8).astype(int) - np.frombuffer(after, np.uint8).astype(int))
    assert diff.mean() < 1
    xref2 = g.insert_signature(doc, 0, pymupdf.Rect(100, 100, 250, 150), item)
    g.delete_signature(doc, 0, xref2)
    assert not _stamp_annots(doc)


def test_page_range_parsing():
    assert g.parse_page_range("2-4,8", 10) == [1, 2, 3, 7]
    assert g.parse_page_range("10-", 12) == [9, 10, 11]
    assert g.parse_page_range("pari", 5) == [1, 3]
    assert g.parse_page_range("dispari", 5) == [0, 2, 4]
    assert g.parse_page_range("tutte tranne la corrente", 3, current=1) == [0, 2]
    for bad in ("", "a-b", "0", "5-3", "11"):
        with pytest.raises(ValueError):
            g.parse_page_range(bad, 10)


def test_repeat_on_range_same_anchor_distance():
    """SIG-06: «2-4,8» su 10 pagine → 4 copie alla stessa distanza dall'angolo."""
    item = _drawn_item()
    pdf = pymupdf.open()
    for _i in range(10):
        pdf.new_page()
    src = pymupdf.Rect(400, 760, 520, 800)
    xref = g.insert_signature(pdf, 0, src, item)
    created, shrunk = g.repeat_signature(pdf, 0, xref, g.parse_page_range("2-4,8", 10))
    assert [p for p, _x in created] == [1, 2, 3, 7]
    assert not shrunk
    for p, _x in created:
        sigs = g.placed_signatures(pdf, p)
        assert len(sigs) == 1
        r = sigs[0].rect
        assert abs((595 - r.x1) - (595 - src.x1)) < 1 and abs((842 - r.y1) - (842 - src.y1)) < 1
    # aspetto condiviso
    aps = {g.placed_signatures(pdf, p)[0].ap_xref for p, _x in created}
    assert len(aps) == 1


def test_repeat_mixed_sizes_inside_cropbox():
    item = _drawn_item()
    pdf = pymupdf.open()
    pdf.new_page(width=595, height=842)
    pdf.new_page(width=1191, height=842)
    pdf.new_page(width=595, height=842).set_rotation(90)
    xref = g.insert_signature(pdf, 0, pymupdf.Rect(420, 760, 560, 810), item)
    created, _shrunk = g.repeat_signature(pdf, 0, xref, [1, 2], anchor="br")
    for p, _x in created:
        page = pdf[p]
        r = g.placed_signatures(pdf, p)[0].rect
        assert pymupdf.Rect(page.cropbox).contains(r)
        vis = r * page.rotation_matrix
        vpage = pymupdf.Rect(page.cropbox) * page.rotation_matrix
        vis.normalize()
        vpage.normalize()
        assert abs((vpage.x1 - vis.x1) - (595 - 560)) < 1
        assert abs((vpage.y1 - vis.y1) - (842 - 810)) < 1


def test_permissions_forbid_annotations(tmp_path):
    path = tmp_path / "noannot.pdf"
    d = pymupdf.open()
    d.new_page()
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="own", user_pw="",
           permissions=pymupdf.PDF_PERM_PRINT)
    doc = pymupdf.open(str(path))
    with pytest.raises(g.GraphicSignatureError):
        g.insert_signature(doc, 0, pymupdf.Rect(10, 10, 100, 40), _drawn_item())


def test_rotate_signature_90_steps():
    item = _drawn_item()
    pdf = pymupdf.open()
    pdf.new_page()
    xref = g.insert_signature(pdf, 0, pymupdf.Rect(100, 100, 250, 150), item)
    r = g.rotate_signature(pdf, 0, xref, 90)
    assert r.width == pytest.approx(50, abs=0.5) and r.height == pytest.approx(150, abs=0.5)
    pix = pdf[0].get_pixmap(dpi=72, clip=r)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    ink = np.argwhere(arr[..., 0] < 128)
    assert np.ptp(ink[:, 0]) > np.ptp(ink[:, 1])        # ora il tratto è verticale
    for _i in range(3):
        g.rotate_signature(pdf, 0, xref, 90)
    sig = g.find_signature(pdf, 0, xref)
    assert sig.rect.width == pytest.approx(150, abs=0.5)


def test_magnetic_guides_snap_to_signature_line_and_margin():
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.draw_line((300, 700), (520, 700))          # linea di firma
    page.insert_text((72, 650), "Firma: ______________", fontsize=12)
    xs, ys, lines = g.page_guides(pdf[0])
    assert any(abs(y - 700) < 1 for y, _a, _b in lines)
    r, active = g.snap_rect(pymupdf.Rect(320, 650, 470, 703), xs, ys, lines, tol=6)
    assert ("y", 700) in [(a, round(v)) for a, v in active]
    assert r.y1 > 700 > r.y0 and r.height == pytest.approx(53)
    r2, active2 = g.snap_rect(pymupdf.Rect(40, 300, 140, 340), xs, ys, lines, tol=6)
    assert r2.x0 == pytest.approx(36) and ("x", 36) in active2
    r3, active3 = g.snap_rect(pymupdf.Rect(200, 300, 300, 340), xs, ys, lines, tol=6)
    assert r3 == pymupdf.Rect(200, 300, 300, 340) and not active3


def test_adaptive_threshold_on_shadowed_photo():
    rng = np.random.default_rng(3)
    h, w = 160, 480
    xx = np.linspace(0, 1, w)[None, :]
    bg = 250 - 110 * xx + rng.integers(-6, 7, (h, w))      # ombra da destra
    img = np.repeat(bg, 1, axis=0).astype(float)
    stroke = np.zeros((h, w), bool)
    for x in range(30, 450):
        y = int(80 + 35 * math.sin(x / 30))
        stroke[y - 3:y + 3, x] = True
    img[stroke] = 20 + rng.integers(0, 10, stroke.sum())
    buf = io.BytesIO()
    Image.fromarray(np.dstack([img] * 3).clip(0, 255).astype(np.uint8), "RGB").save(buf, "PNG")
    pil = g.load_image(buf.getvalue())
    alpha = np.asarray(g.remove_background(pil, crop=False))[..., 3]
    assert (alpha[~stroke] == 0).mean() >= 0.97
    assert (alpha[stroke] >= 200).mean() >= 0.9


def test_group_ids_and_ungroup():
    item = _drawn_item()
    pdf = pymupdf.open()
    for _i in range(4):
        pdf.new_page()
    xref = g.insert_signature(pdf, 0, pymupdf.Rect(400, 760, 520, 800), item)
    created, _s = g.repeat_signature(pdf, 0, xref, [1, 2, 3])
    nm0 = g.placed_signatures(pdf, 0)[0].nm
    assert g.group_size(pdf, g.group_of(nm0)) == 4
    assert g.group_of(g.placed_signatures(pdf, 2)[0].nm) == nm0
    assert g.ungroup(pdf, nm0) == 3
    assert g.group_size(pdf, nm0) == 1


def test_clamp_with_offset_cropbox():
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    page.set_cropbox(pymupdf.Rect(100, 100, 500, 700))
    assert g.clamp_rect(pdf[0], pymupdf.Rect(10, 10, 60, 40)) == pymupdf.Rect(10, 10, 60, 40)
    r = g.clamp_rect(pdf[0], pymupdf.Rect(380, 10, 440, 40))
    assert r.x1 <= 400.01
