"""Test della rilevazione di pagine vuote e duplicate (PAG-40)."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from foliant.api import OperationCancelled
from foliant.modules.pages import detect


def text_page(doc, n, body=True):
    p = doc.new_page(width=595, height=842)
    if body:
        p.insert_text((72, 100), f"Capitolo {n}", fontsize=24)
        for k in range(12):
            p.insert_text((72, 160 + 20 * k), f"Riga {k} del testo della pagina numero {n} di prova.", fontsize=11)
    return p


def noise_png(w, h, fraction, seed=3):
    rng = np.random.default_rng(seed)
    arr = np.full((h, w), 245, dtype=np.uint8)            # carta leggermente grigia
    mask = rng.random((h, w)) < fraction
    arr[mask] = 20
    return pymupdf.Pixmap(pymupdf.csGRAY, w, h, arr.tobytes(), False).tobytes("png")


def test_blank_pages_2_and_5():
    doc = pymupdf.open()
    for n in range(1, 7):
        text_page(doc, n, body=n not in (2, 5))
    assert detect.find_blank_pages(doc) == [1, 4]


def test_scanned_back_with_little_noise_is_blank():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, stream=noise_png(600, 850, 0.004))
    q = doc.new_page(width=595, height=842)
    q.insert_image(q.rect, stream=noise_png(600, 850, 0.05))
    details = {}
    assert detect.find_blank_pages(doc, ink_threshold=0.01, details=details) == [0]
    assert details[0].ink < 0.01 < details[1].ink


def test_artifacts_annotations_and_invisible_text():
    doc = pymupdf.open()
    p = text_page(doc, 1, body=False)
    p.insert_text((290, 820), "- 7 -", fontsize=10)                    # solo numero di pagina
    assert detect.find_blank_pages(doc) == [0]
    assert detect.find_blank_pages(doc, ignore_pagination_artifacts=False) == []
    doc = pymupdf.open()
    p = text_page(doc, 1, body=False)
    p.insert_text((72, 400), "testo OCR invisibile", fontsize=12, render_mode=3)
    assert detect.find_blank_pages(doc) == [0]
    assert detect.find_blank_pages(doc, ocr_text_counts=True) == []
    doc = pymupdf.open()
    p = text_page(doc, 1, body=False)
    p.add_text_annot((100, 100), "nota")
    p.draw_rect(pymupdf.Rect(100, 300, 110, 310), color=None, fill=(0, 0, 0))   # macchiolina sotto soglia
    assert detect.find_blank_pages(doc) == [0]
    p.draw_rect(pymupdf.Rect(100, 300, 400, 500), color=None, fill=(0, 0, 0))
    assert detect.find_blank_pages(doc) == []


def test_invalid_page_reported_blank():
    doc = pymupdf.open()
    text_page(doc, 1)
    doc.new_page(width=200, height=200)
    doc.xref_set_key(doc[1].xref, "MediaBox", "[0 0 0 0]")
    details = {}
    blanks = detect.find_blank_pages(doc, details=details)
    assert 1 in blanks


def test_duplicate_identical_page_4_of_1():
    doc = pymupdf.open()
    for n in (1, 2, 3):
        text_page(doc, n)
    doc.fullcopy_page(0)                                    # pagina 4 = copia della 1
    text_page(doc, 5)
    assert detect.find_duplicate_pages(doc, mode="identical") == [(3, 0)]
    assert detect.find_duplicate_pages(doc, mode="identical", scope="adjacent") == []


def test_duplicate_similar_and_adjacent_chain():
    doc = pymupdf.open()
    text_page(doc, 1)
    p = text_page(doc, 1)
    p.draw_rect(pymupdf.Rect(500, 800, 502, 802), color=None, fill=(0, 0, 0))   # differenza minima
    q = doc.new_page(width=595, height=842)                  # stessa impaginazione, testo diverso
    q.insert_text((72, 100), "Indice generale", fontsize=24)
    for k in range(12):
        q.insert_text((72, 160 + 20 * k), f"Voce {k}: orologi da tasca, movimenti e casse {k * 7}.", fontsize=11)
    assert detect.find_duplicate_pages(doc, mode="identical") == []
    assert detect.find_duplicate_pages(doc, mode="similar") == [(1, 0)]
    doc = pymupdf.open()
    for _k in range(3):
        text_page(doc, 1)
    assert detect.find_duplicate_pages(doc, scope="adjacent") == [(1, 0), (2, 0)]


def test_progress_cancel():
    class P:
        def update(self, fraction=None, text=None):
            raise OperationCancelled()

    doc = pymupdf.open()
    text_page(doc, 1)
    with pytest.raises(OperationCancelled):
        detect.find_blank_pages(doc, progress=P())
