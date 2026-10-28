"""Mostra/nascondi le annotazioni nella vista (ANN-39), senza modificare il file.

Usa la proprietà del nucleo `DocumentView.show_annotations`, rispettata sia dalla resa
delle pagine intere sia da quella a tasselle degli zoom elevati."""

from __future__ import annotations


def set_view_annotations(view, visible: bool) -> None:
    view.show_annotations = visible
    view._invalidate_render()
    view._schedule_visible()
    view.viewport().update()
