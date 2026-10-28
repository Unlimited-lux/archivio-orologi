"""Rilettura su pagine ruotate e con CropBox spostato: le posizioni del modulo
coincidono con quelle del motore (get_image_info / get_drawings)."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from foliant.modules.objects import engine as E
from foliant.modules.objects import links as L


def png(w=40, h=20):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 20, 20)).save(b, "PNG")
    return b.getvalue()


def odd_doc(rot, crop):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    if crop:
        page.set_cropbox(pymupdf.Rect(30, 50, 560, 760))
    page.set_rotation(rot)
    return doc


def center(r):
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_insert_move_replace_readback(rot, crop):
    doc = odd_doc(rot, crop)
    idx = E.insert_images(doc, 0, [png()], center=pymupdf.Point(200, 300))
    info = doc[0].get_image_info()[0]
    cx, cy = center(info["bbox"])
    assert abs(cx - 200) < 0.5 and abs(cy - 300) < 0.5
    o = E.objects(doc, 0).objects[idx[0]]
    assert abs(o.rect.x0 - info["bbox"][0]) < 0.5 and abs(o.rect.y0 - info["bbox"][1]) < 0.5
    E.translate(doc, 0, idx, 25, -10)
    cx2, cy2 = center(doc[0].get_image_info()[0]["bbox"])
    assert abs(cx2 - 225) < 0.5 and abs(cy2 - 290) < 0.5
    E.replace_image(doc, 0, idx[0], png(20, 20), E.FIT)
    cx3, cy3 = center(doc[0].get_image_info()[0]["bbox"])
    assert abs(cx3 - 225) < 0.5 and abs(cy3 - 290) < 0.5


@pytest.mark.parametrize("rot", [0, 90])
@pytest.mark.parametrize("crop", [False, True])
def test_paths_readback(rot, crop):
    doc = odd_doc(rot, crop)
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"0 0 1 rg 100 100 50 40 re f", new=True)
    doc.xref_set_key(doc[0].xref, "Contents", f"{x} 0 R")
    r_engine = doc[0].get_drawings()[0]["rect"]
    o = E.objects(doc, 0).objects[0]
    assert abs(o.rect.x0 - r_engine.x0) < 0.5 and abs(o.rect.y0 - r_engine.y0) < 0.5
    E.translate(doc, 0, [0], 10, 20)
    r2 = doc[0].get_drawings()[0]["rect"]
    assert abs(r2.x0 - r_engine.x0 - 10) < 0.5 and abs(r2.y0 - r_engine.y0 - 20) < 0.5


@pytest.mark.parametrize("crop", [False, True])
def test_link_rect_readback(crop):
    doc = odd_doc(0, crop)
    xref = L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.URI, uri="https://a.it"))
    info = L.read_link(doc, 0, xref)
    assert abs(info.rect.x0 - 100) < 0.5 and abs(info.rect.y0 - 200) < 0.5
    lk = doc.reload_page(doc[0]).get_links()[0]
    assert abs(lk["from"].x0 - 100) < 0.5 and abs(lk["from"].y0 - 200) < 0.5
