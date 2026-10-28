"""Fixture del modulo Converti."""

from __future__ import annotations

import pymupdf
import pytest


@pytest.fixture
def twenty_pages(tmp_path):
    doc = pymupdf.open()
    for i in range(20):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Pagina numero {i + 1}", fontsize=20)
    path = tmp_path / "venti.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def table_pdf(tmp_path):
    """PDF con una tabella bordata 5 righe x 4 colonne."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    rows = [["Marca", "Modello", "Anno", "Prezzo"]] + [
        [f"Marca{r}", f"Mod{r}", str(2000 + r), f"{r},50"] for r in range(1, 5)]
    x0, y0, cw, rh = 72, 100, 110, 24
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), val, fontsize=11)
    path = tmp_path / "tabella.pdf"
    doc.save(str(path))
    doc.close()
    return str(path), rows
