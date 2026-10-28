"""Mostra/nascondi le annotazioni nella vista (ANN-39), senza modificare il file.

Soluzione locale in attesa di un'opzione del nucleo (vedi CORE_REQUESTS.md): si
sostituisce il disegno delle pagine della singola vista con uno senza annotazioni."""

from __future__ import annotations

import math
import types

import pymupdf
from PySide6.QtGui import QImage

MAX_RENDER_PIXELS = 24_000_000


def _render_without_annots(view, index: int, scale: float) -> QImage:
    g = view._geoms[index]
    pixels = (g.width * scale) * (g.height * scale)
    if pixels > MAX_RENDER_PIXELS:
        scale *= math.sqrt(MAX_RENDER_PIXELS / pixels)
    page = view.document.pdf[index]
    mat = pymupdf.Matrix(scale, scale)
    if view._view_rotation:
        mat = mat.prerotate(view._view_rotation)
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=False)
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


def set_view_annotations(view, visible: bool) -> None:
    if visible:
        if "_render_page" in view.__dict__:
            del view.__dict__["_render_page"]
    else:
        view._render_page = types.MethodType(lambda v, i, s: _render_without_annots(v, i, s), view)
    view._invalidate_render()
    view._schedule_visible()
    view.viewport().update()
