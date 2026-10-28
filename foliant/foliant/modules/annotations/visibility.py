"""Mostra/nascondi le annotazioni nella vista (ANN-39) e «Applica il filtro alla pagina» (ANN-35),
senza modificare il file: si usano `DocumentView.show_annotations` e
`DocumentView.set_annotation_filter` del nucleo (pagine intere e tasselle)."""

from __future__ import annotations


def set_view_annotations(view, visible: bool) -> None:
    view.show_annotations = visible
    view._invalidate_render()
    view._schedule_visible()
    view.viewport().update()


def set_view_filter(view, excluded: dict[int, set[int]] | None) -> None:
    """Nasconde nella vista le annotazioni in `excluded` ({pagina: xref}); None = nessun filtro."""
    view._annot_excluded = excluded
    if excluded is None:
        view.set_annotation_filter(None)
    else:
        view.set_annotation_filter(lambda page, xref: xref not in excluded.get(page, ()))
