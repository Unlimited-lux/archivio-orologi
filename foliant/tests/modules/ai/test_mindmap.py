"""Test di AI-12: mappa concettuale e scaletta."""

from __future__ import annotations

import json

import pymupdf
import pytest

from foliant.modules.ai import mindmap as mm

ANSWER = {
    "title": "Contratto di compravendita",
    "page": 1,
    "children": [
        {"title": "Parti", "page": 1, "children": [
            {"title": "Venditore", "page": 2},
            {"title": "Acquirente", "page": 2},
        ]},
        {"titolo": "Oggetto", "pagina": 3, "figli": [
            {"titolo": "Orologio da tasca", "pagina": 4, "nota": "Cassa in argento"},
            {"titolo": "Garanzia", "pagina": 5},
        ]},
        {"title": "Pagamento", "page": 6, "children": [
            {"title": "Prezzo", "page": 6},
            {"title": "Scadenze", "page": 7},
        ]},
    ],
}


def _tree():
    root, warnings = mm.parse_tree(json.dumps(ANSWER), page_count=10)
    assert warnings == []
    return root


def _leaf(root, title):
    return next(n for n in root.walk() if n.title == title)


def test_parse_counts_and_pages():
    root = _tree()
    assert root.count() == 10
    assert len(root.children) == 3
    leaf = _leaf(root, "Orologio da tasca")
    assert leaf.page == 3 and leaf.note == "Cassa in argento"
    assert len({n.id for n in root.walk()}) == 10
    assert root.parent_of(leaf.id).title == "Oggetto"
    assert root.find(leaf.id) is leaf


def test_json_in_prose_and_fences():
    text = "Ecco la mappa richiesta:\n```json\n" + json.dumps(ANSWER) + "\n```\nSpero sia utile."
    root, _w = mm.parse_tree(text, 10)
    assert root.count() == 10
    root2, _w = mm.parse_tree("Risultato: " + json.dumps(ANSWER) + " fine.", 10)
    assert root2.count() == 10


def test_markdown_fallback():
    text = """Scaletta:
- Contratto [p. 1]
  - Parti (p. 2)
    - Venditore
  - Oggetto [p. 3]
"""
    root, warnings = mm.parse_tree(text, 5)
    assert root.title == "Contratto" and root.page == 0
    assert [c.title for c in root.children] == ["Parti", "Oggetto"]
    assert root.children[0].page == 1 and root.children[1].page == 2
    assert root.children[0].children[0].title == "Venditore"
    assert warnings == []


def test_invalid_answer_raises():
    with pytest.raises(mm.MindMapError):
        mm.parse_tree("Non sono in grado di rispondere.", 5)
    with pytest.raises(mm.MindMapError):
        mm.parse_tree("", 5)


def test_out_of_range_page_warns():
    root, warnings = mm.parse_tree('{"title": "A", "page": 99, "children": [{"title": "B", "page": 0}]}', 5)
    assert root.page is None and root.children[0].page is None
    assert len(warnings) == 1 and "99" in warnings[0]


def test_depth_limit_merges():
    deep = {"title": "R", "children": [{"title": "L1", "children": [{"title": "L2", "children": [
        {"title": "L3", "children": [{"title": "L4"}]}]}]}]}
    root, warnings = mm.parse_tree(json.dumps(deep), 5, max_depth=2)
    l2 = root.children[0].children[0]
    assert l2.title == "L2" and l2.children == []
    assert "L3" in l2.note and "L4" in l2.note
    assert root.count() == 3
    assert warnings and "accorpati" in warnings[0]


def test_node_limit_drops_breadth_first():
    root, warnings = mm.parse_tree(json.dumps(ANSWER), 10, max_nodes=4)
    assert root.count() == 4
    assert [c.title for c in root.children] == ["Parti", "Oggetto", "Pagamento"]
    assert warnings and "6" in warnings[0]


def test_edit_and_json_roundtrip():
    root = _tree()
    leaf = _leaf(root, "Garanzia")
    mm.rename(root, leaf.id, "Garanzia biennale")
    text = mm.to_json(root)
    assert "Garanzia biennale" in text
    data = json.loads(text)
    assert set(data) == {"id", "title", "note", "page", "children"}
    back = mm.from_json(text)
    assert mm.to_json(back) == text
    new = mm.add_child(root, root.id, "Clausole")
    assert root.find(new.id) is new and root.count() == 11
    mm.move(root, new.id, root.children[0].id, 0)
    assert root.children[0].children[0] is new
    mm.delete(root, new.id)
    assert root.count() == 10
    with pytest.raises(mm.MindMapError):
        mm.delete(root, root.id)
    with pytest.raises(mm.MindMapError):
        mm.move(root, root.children[0].id, root.children[0].children[0].id, 0)


def test_markdown_outline_and_svg_and_layout():
    root = _tree()
    md = mm.to_markdown_outline(root)
    assert md.startswith("- Contratto di compravendita (p. 1)")
    assert "    - Orologio da tasca (p. 4)" in md
    boxes = mm.layout(root)
    assert set(boxes) == {n.id for n in root.walk()}
    rx = boxes[root.id][0]
    assert all(boxes[c.id][0] > rx for c in root.children)
    svg = mm.to_svg(root)
    assert svg.startswith("<svg") and "Orologio da tasca" in svg and "href" not in svg
    assert len(mm.short_label("x" * 100)) == mm.MAX_LABEL


def test_insert_page_and_png():
    root = _tree()
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page(width=595, height=842).insert_text((72, 72), f"Pagina {i + 1}")
    index = mm.insert_page(doc, root, at=0)
    assert index == 0 and doc.page_count == 3
    text = " ".join(doc[0].get_text().split())
    for node in root.walk():
        assert node.title in text
    assert "Pagina 1" in doc[1].get_text()
    assert doc[0].get_drawings()
    end = mm.insert_page(doc, root, at=-1, size=(300, 200))
    assert end == 3 and doc[3].rect.width == 300
    doc.close()
    png = mm.render_png(root)
    assert png.startswith(b"\x89PNG")


def test_view_click_and_rename(qtbot):
    from foliant.modules.ai.mindmap_view import MindMapView

    view = MindMapView()
    qtbot.addWidget(view)
    root = _tree()
    view.set_tree(root)
    assert view.tree().count() == 10
    assert view.node_item(root.id) is not None
    leaf = _leaf(root, "Orologio da tasca")
    with qtbot.waitSignal(view.pageRequested, timeout=1000) as blocker:
        view.click_node(leaf.id)
    assert blocker.args == [3]

    with qtbot.waitSignal(view.changed, timeout=1000):
        item = view._outline_item(leaf.id)
        item.setText(0, "Orologio da taschino")
    assert view.tree().find(leaf.id).title == "Orologio da taschino"
    assert "Orologio da taschino" in mm.to_json(view.tree())

    parent = root.parent_of(leaf.id)
    view.toggle_collapsed(parent.id)
    assert view.node_item(leaf.id) is None
    view.toggle_collapsed(parent.id)
    assert view.node_item(leaf.id) is not None

    view.select(parent.id)
    with qtbot.waitSignal(view.changed, timeout=1000):
        new = view.add_child_to_selected("Nuovo")
    assert view.tree().find(new.id).title == "Nuovo"
    with qtbot.waitSignal(view.changed, timeout=1000):
        view.delete_selected()
    assert view.tree().count() == 10
    view.show_outline()
    view.btn_zoom_in.click()
    view.show_map()
