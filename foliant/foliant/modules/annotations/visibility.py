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


def _render_filtered(view, index: int, scale: float) -> QImage:
    """Pagina senza annotazioni più le sole annotazioni ammesse dal filtro (e collegamenti/campi)."""
    keep = getattr(view, "_annot_keep", None) or {}
    img = _render_without_annots(view, index, scale)
    page = view.document.pdf[index]
    if view._view_rotation or page.rotation:
        return _render_with_all(view, index, scale)   # composizione solo per pagine non ruotate
    g = view._geoms[index]
    sc = img.width() / max(g.width, 1)
    mat = pymupdf.Matrix(sc, sc)
    allowed = keep.get(index, set())
    from PySide6.QtGui import QPainter
    painter = QPainter(img)
    try:
        from .engine import NON_COMMENT_TYPES, iter_annots
        for annot in iter_annots(page):
            if annot.type[0] not in NON_COMMENT_TYPES and annot.xref not in allowed:
                continue
            try:
                pix = annot.get_pixmap(matrix=mat, alpha=True)
            except Exception:
                continue
            fmt = QImage.Format.Format_RGBA8888 if pix.alpha else QImage.Format.Format_RGB888
            part = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
            painter.drawImage(pix.x, pix.y, part)
    finally:
        painter.end()
    return img


def _render_with_all(view, index: int, scale: float) -> QImage:
    return type(view)._render_page(view, index, scale)


def set_view_annotations(view, visible: bool) -> None:
    if visible:
        if getattr(view, "_annot_keep", None) is not None:
            view._render_page = types.MethodType(lambda v, i, s: _render_filtered(v, i, s), view)
        elif "_render_page" in view.__dict__:
            del view.__dict__["_render_page"]
    else:
        view._render_page = types.MethodType(lambda v, i, s: _render_without_annots(v, i, s), view)
    view._invalidate_render()
    view._schedule_visible()
    view.viewport().update()


def set_view_filter(view, keep: dict[int, set[int]] | None, hidden: bool = False) -> None:
    """Mostra in pagina solo le annotazioni in `keep` ({pagina: xref}); None = tutte (ANN-35)."""
    view._annot_keep = keep
    set_view_annotations(view, not hidden)
