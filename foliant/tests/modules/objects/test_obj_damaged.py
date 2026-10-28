"""PDF danneggiati: troncati e riparati dal motore, albero delle pagine con ciclo (NFR-13/15)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules import objects as O
from foliant.modules.objects import extract as X
from foliant.modules.objects import links as L
from foliant.modules.objects import linkcheck as LC

from .test_obj_ui import ui  # noqa: F401


def _pdf_with_links(pages=3):
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 100), f"Pagina {i + 1} " * 20)
        L.create_link(d, i, pymupdf.Rect(50, 200, 150, 220), L.LinkTarget(kind=L.URI, uri=f"https://s{i}.it"))
    return d


def truncated_bytes() -> bytes:
    """PDF troncato che il motore apre riparandolo, con pagine i cui xref sono fuori dalla tabella."""
    b = _pdf_with_links().tobytes(garbage=0, deflate=False)
    for k in range(30, 95):
        t = b[:len(b) * k // 100]
        try:
            d = pymupdf.open(stream=t, filetype="pdf")
        except Exception:
            continue
        bad = False
        for i in range(d.page_count):
            x = d.page_xref(i)
            if not 0 < x < d.xref_length():
                bad = True
            else:
                try:
                    d.xref_get_key(x, "Annots")
                except Exception:
                    bad = True
        if bad:
            return t
    pytest.skip("nessun troncamento produce pagine con xref non validi con questa versione del motore")


def test_logic_survives_truncated_pdf():
    d = pymupdf.open(stream=truncated_bytes(), filetype="pdf")
    for i in range(d.page_count):
        assert isinstance(L.page_links(d, i), list)
        assert isinstance(L.link_xrefs(d, i), list)
    assert isinstance(LC.check_document(d), list)
    assert isinstance(X.plan(d, X.ExtractOptions()), list)


def test_link_panel_switches_to_damaged_document(ui, tmp_path):
    good = tmp_path / "links.pdf"
    d = pymupdf.open()
    d.new_page()
    for k in range(2):
        L.create_link(d, 0, pymupdf.Rect(50, 50 + 40 * k, 150, 70 + 40 * k), L.LinkTarget(kind=L.URI, uri=f"https://g{k}.it"))
    d.save(str(good))
    bad = tmp_path / "troncato.pdf"
    bad.write_bytes(truncated_bytes())
    panel = O._state["link_panel"]
    v1 = ui.window.open_path(str(good))
    panel.set_view(v1)
    assert len(panel.rows) == 2
    statuses = []
    v2 = ui.window.open_path(str(bad))
    assert v2 is not None
    v2.statusMessage.connect(statuses.append)
    panel.set_view(v2)
    assert all(not r.destination.startswith("https://g") for r in panel.rows)
    assert panel.table.rowCount() == len([r for r in panel.rows])
    # strumenti del modulo sullo stesso documento: nessuna eccezione
    c = ui.c
    for i in range(v2.document.page_count):
        c.parsed(v2, i, quiet=True)
        c.links(v2, i)
    ui.ctx.set_mode("modifica")
    v2.set_tool(c.object_tool)
    c.select_all(v2)
    assert not any("bad xref" in s for s in statuses)


def test_page_tree_with_cycle(tmp_path):
    d = pymupdf.open()
    for _k in range(2):
        d.new_page()
    pages_xref = int(d.xref_get_key(d.pdf_catalog(), "Pages")[1].split()[0])
    kids = d.xref_get_key(pages_xref, "Kids")[1].strip("[]")
    d.xref_set_key(pages_xref, "Kids", f"[{kids} {pages_xref} 0 R]")
    d.xref_set_key(pages_xref, "Count", "3")
    data = d.tobytes()
    try:
        d2 = pymupdf.open(stream=data, filetype="pdf")
        n = d2.page_count
    except Exception:
        return   # il motore rifiuta il file: nessun rischio per il modulo
    for i in range(n):
        try:
            d2[i]
        except Exception:
            continue
        assert isinstance(L.page_links(d2, i), list)
    assert isinstance(LC.check_document(d2), list)
    assert isinstance(X.plan(d2, X.ExtractOptions()), list)
