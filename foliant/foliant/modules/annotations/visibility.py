"""Mostra/nascondi le annotazioni nella vista (ANN-39) e «Applica il filtro alla pagina» (ANN-35),
senza modificare il file.

Mostra/nascondi usa la proprietà del nucleo `DocumentView.show_annotations`, rispettata sia dalla
resa delle pagine intere sia da quella a tasselle degli zoom elevati. Il filtro compone la pagina
senza annotazioni con le sole annotazioni ammesse (resa a pagina intera; a tasselle si vedono tutte)."""

from __future__ import annotations

import types

import pymupdf
from PySide6.QtGui import QImage, QPainter


def _render_filtered(view, index: int, scale: float) -> QImage:
    """Pagina senza annotazioni più le sole annotazioni ammesse dal filtro (e collegamenti/campi)."""
    keep = getattr(view, "_annot_keep", None) or {}
    page = view.document.pdf[index]
    if view._view_rotation or page.rotation:
        return type(view)._render_page(view, index, scale)   # composizione solo per pagine non ruotate
    shown = view.show_annotations
    view.show_annotations = False
    try:
        img = type(view)._render_page(view, index, scale)
    finally:
        view.show_annotations = shown
    if not shown:
        return img
    g = view._geoms[index]
    sc = img.width() / max(g.width, 1)
    mat = pymupdf.Matrix(sc, sc)
    allowed = keep.get(index, set())
    from .engine import NON_COMMENT_TYPES, iter_annots
    painter = QPainter(img)
    try:
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


def _refresh(view) -> None:
    view._invalidate_render()
    view._schedule_visible()
    view.viewport().update()


def set_view_annotations(view, visible: bool) -> None:
    view.show_annotations = visible
    _refresh(view)


def set_view_filter(view, keep: dict[int, set[int]] | None, hidden: bool = False) -> None:
    """Mostra in pagina solo le annotazioni in `keep` ({pagina: xref}); None = tutte (ANN-35)."""
    view._annot_keep = keep
    if keep is None:
        if "_render_page" in view.__dict__:
            del view.__dict__["_render_page"]
    else:
        view._render_page = types.MethodType(lambda v, i, s: _render_filtered(v, i, s), view)
    view.show_annotations = not hidden
    _refresh(view)
