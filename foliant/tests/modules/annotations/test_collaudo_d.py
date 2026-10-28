"""Collaudo D: XFDF con testo formattato (/RC) e colore del bordo; importazione di molti commenti."""

import time

import pymupdf
import pytest

from foliant.modules.annotations import engine, ui_actions
from foliant.modules.annotations.controller import FACTORY_STYLES

RICH = ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><p>Prezzo <b>da rivedere</b></p></body>')


def _red(d):
    page = d[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=pymupdf.Rect(240, 110, 460, 200))
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if (lambda c: c[0] > 150 and c[0] - c[1] > 60)(pix.pixel(x, y)))


@pytest.mark.parametrize("fmt", ["xfdf", "fdf"])
def test_roundtrip_border_rich_text_and_rect(fmt):
    d = pymupdf.open()
    d.new_page()
    x = engine.create_freetext(d, 0, (250, 120, 450, 190), "Prezzo da rivedere", FACTORY_STYLES[engine.FREETEXT], "A")
    engine.set_contents(d, 0, x, "Prezzo da rivedere", RICH)
    note = engine.create_note(d, 0, (100, 300), "Prezzo da rivedere", engine.Style(), "A")
    engine.set_contents(d, 0, note, "Prezzo da rivedere", RICH)
    page = d[0]
    rect = tuple(engine.find_annot(page, x).rect)
    red = _red(d)
    assert red > 1000
    data = engine.export_xfdf(d) if fmt == "xfdf" else ui_actions.fdf_to_xfdf(engine.export_fdf(d))
    engine.delete_all(d)
    engine.import_xfdf(d, data)
    infos = {i.type: i for i in engine.list_annotations(d)}
    page = d[0]
    assert tuple(engine.find_annot(page, infos["FreeText"].xref).rect) == pytest.approx(rect, abs=0.01)
    assert _red(d) >= red * 0.9                          # bordo rosso conservato
    for t in ("FreeText", "Text"):
        rc = engine.get_rich_text(d, infos[t].xref)
        assert "<b>da rivedere</b>" in rc, rc


def _import_time(n, pages=10):
    d = pymupdf.open()
    for _ in range(pages):
        d.new_page()
    for i in range(n):
        p = i % pages
        x = engine.create_note(d, p, (50 + (i % 25) * 20, 50 + (i // 25) % 40 * 18), f"n{i}", engine.Style(), "A")
        if i % 10 == 0:
            engine.add_reply(d, p, x, "ok", "B")
    data = engine.export_xfdf(d)
    engine.delete_all(d)
    t = time.perf_counter()
    res = engine.import_xfdf(d, data)
    elapsed = time.perf_counter() - t
    infos = engine.list_annotations(d)
    assert res.imported == n + n // 10
    assert len(infos) == n and sum(len(i.replies) for i in infos) == n // 10
    return elapsed


def test_import_1000_comments_fast():
    """Collaudo D: 1000 commenti = 12,8 s e 3000 = 99,7 s (crescita quadratica). Ora circa 1,7 s e
    5 s su questa macchina: /NM senza scansione della pagina, nessuna ricerca per elemento, una sola
    pagina caricata alla volta (MuPDF rallenta ogni creazione in proporzione alle annotazioni delle
    pagine aperte)."""
    assert _import_time(1000) < 6
    assert _import_time(3000, pages=30) < 20
