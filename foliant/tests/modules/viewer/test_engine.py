"""Logica pura del viewer: ricerca, etichette, cronologia, segnalibri."""

from __future__ import annotations

import os

import pymupdf
import pytest

from tests.conftest import system_font
from foliant.modules.viewer import engine as E

DEJAVU = system_font("truetype/dejavu/DejaVuSans.ttf")


def _page(lines, fontfile=None):
    doc = pymupdf.open()
    page = doc.new_page()
    y = 100
    for line in lines:
        if fontfile:
            page.insert_text((72, y), line, fontsize=12, fontname="dv", fontfile=fontfile)
        else:
            page.insert_text((72, y), line, fontsize=12)
        y += 16
    doc = pymupdf.open("pdf", doc.tobytes())
    return doc, doc[0]


def _count(page, query, **opts):
    pt = E.extract_page_text(page)
    return len(E.find_in_page_text(pt, query, E.SearchOptions(**opts)))


def test_case_sensitivity():
    _d, page = _page(["Contratto uno Contratto due", "Contratto tre", "contratto quattro contratto"])
    assert _count(page, "contratto") == 5
    assert _count(page, "contratto", case_sensitive=True) == 2


def test_whole_word():
    _d, page = _page(["Bisogna ricontrattare il prezzo"])
    assert _count(page, "contratt") == 1
    assert _count(page, "contratt", whole_word=True) == 0
    assert _count(page, "ricontrattare", whole_word=True) == 1


@pytest.mark.skipif(not os.path.exists(DEJAVU), reason="font DejaVu assente")
def test_ignore_accents_and_ligatures():
    _d, page = _page(["perché la ﬁnanza"], fontfile=DEJAVU)
    assert _count(page, "perche") == 0
    assert _count(page, "perche", ignore_accents=True) == 1
    assert _count(page, "finanza") == 1


def test_hyphenated_word_across_lines():
    _d, page = _page(["Questa è una infor-", "mazione importante"])
    pt = E.extract_page_text(page)
    hits = E.find_in_page_text(pt, "informazione", E.SearchOptions())
    assert len(hits) == 1
    assert len(hits[0]) == 2  # evidenziata su entrambe le righe
    assert hits[0][0].y0 < hits[0][1].y0


def test_phrase_across_lines_and_snippet():
    _d, page = _page(["il prezzo del", "Rolex Daytona e altro"])
    pt = E.extract_page_text(page)
    hits = E.find_in_page_text(pt, "del rolex", E.SearchOptions())
    assert len(hits) == 1
    before, found, after = E.context_snippet(pt, hits[0])
    assert found.lower() == "del rolex"
    assert "prezzo" in before
    assert after.startswith(" Daytona")


def test_empty_page_has_no_text():
    doc = pymupdf.open()
    doc.new_page()
    pt = E.extract_page_text(doc[0])
    assert not pt.has_text
    assert E.find_in_page_text(pt, "x") == []


def test_page_labels_resolution():
    doc = pymupdf.open()
    for _ in range(110):
        doc.new_page()
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                         {"startpage": 10, "prefix": "", "style": "D", "firstpagenum": 1}])
    labels = E.page_labels(doc)
    assert labels[3] == "iv" and labels[14] == "5"
    assert E.resolve_page_input("iv", labels, 110) == 3
    assert E.resolve_page_input("#15", labels, 110) == 14
    assert E.resolve_page_input("5", labels, 110) == 14     # etichetta prima del numero fisico
    assert E.resolve_page_input("999", labels, 110) is None
    assert E.resolve_page_input("#0", labels, 110) is None
    assert E.resolve_page_input("xyz", labels, 110) is None


def test_page_labels_absent_and_duplicates():
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    labels = E.page_labels(doc)
    assert labels == ["", "", ""]
    assert E.resolve_page_input("2", labels, 3) == 1
    dup = ["A", "B", "A"]
    assert E.resolve_page_input("A", dup, 3) == 0
    assert E.resolve_page_input("A", dup, 3, start_after=0) == 2


def test_history():
    h = E.NavigationHistory(limit=3)
    assert not h.can_back
    h.record(E.Position(2))
    assert h.back(E.Position(39)) == E.Position(2)
    assert h.forward(E.Position(2)) == E.Position(39)
    # salto A->B, Indietro, salto verso C: Avanti disabilitato
    h.back(E.Position(39))
    h.record(E.Position(2))
    assert not h.can_forward
    for i in range(10):
        h.record(E.Position(i))
    assert len(h._back) == 3
    h.clamp(5)
    assert all(p.page <= 4 for p in h._back)


def _items():
    B = E.Bookmark
    return [B(1, "Cap 1", 0), B(2, "1.1", 1), B(2, "1.2", 2), B(1, "Cap 2", 5), B(2, "2.1", 6), B(1, "Cap 3", 8)]


def test_bookmark_operations():
    items = _items()
    out, pos = E.bookmark_add(items, 0, "Nuovo", 3)
    assert pos == 3 and out[3].level == 1 and out[3].title == "Nuovo"
    out = E.bookmark_delete(items, 3)
    assert [b.title for b in out] == ["Cap 1", "1.1", "1.2", "Cap 3"]
    out, idx = E.bookmark_move_up(items, 3)
    assert [b.title for b in out][:3] == ["Cap 2", "2.1", "Cap 1"] and idx == 0
    out, idx = E.bookmark_move_down(items, 0)
    assert [b.title for b in out] == ["Cap 2", "2.1", "Cap 1", "1.1", "1.2", "Cap 3"] and idx == 2
    out = E.bookmark_indent(items, 3)
    assert [(b.title, b.level) for b in out][3:5] == [("Cap 2", 2), ("2.1", 3)]
    out, idx = E.bookmark_outdent(items, 1)
    assert [(b.title, b.level) for b in out][:3] == [("Cap 1", 1), ("1.2", 2), ("1.1", 1)]
    out, idx = E.bookmark_move_into(items, 5, 0, as_child=True)
    assert [(b.title, b.level) for b in out][:4] == [("Cap 1", 1), ("1.1", 2), ("1.2", 2), ("Cap 3", 2)]
    assert all(E.is_valid_structure(x) for x in (out,))
    assert E.current_bookmark(items, 7) == 4
    assert list(E.section_pages(items, 0, 10)) == [0, 1, 2, 3, 4]
    assert list(E.section_pages(items, 5, 10)) == [8, 9]


def test_write_outline_roundtrip_with_style_and_collapse():
    doc = pymupdf.open()
    for _ in range(10):
        doc.new_page()
    items = _items()
    items[0].dest = {"collapse": True}
    items[3].dest = {"bold": True, "italic": True, "color": (1, 0, 0)}
    items[5].title = "Résumé — 概要"
    E.write_outline(doc, items)
    doc = pymupdf.open("pdf", doc.tobytes())
    back = E.read_outline(doc)
    assert [b.title for b in back] == [b.title for b in items]
    assert [b.page for b in back] == [b.page for b in items]
    assert back[0].collapsed and not back[3].collapsed
    assert back[3].bold and back[3].italic and back[3].color == (1.0, 0.0, 0.0)


def test_outline_text_roundtrip():
    items = _items()
    text = E.outline_to_text(items)
    assert text.splitlines()[1] == "\t1.1\t2"
    back, errors = E.outline_from_text(text, 10)
    assert not errors
    assert [(b.level, b.title, b.page) for b in back] == [(b.level, b.title, b.page) for b in items]


def test_outline_import_three_levels_and_bad_lines():
    text = "Uno\t1\n  Due\t2\n    Tre\t3\nFuori\t99\nsenza numero\n"
    items, errors = E.outline_from_text(text, 10)
    assert [(b.level, b.title, b.page) for b in items] == [(1, "Uno", 0), (2, "Due", 1), (3, "Tre", 2)]
    assert len(errors) == 2


def test_outline_every_n():
    items = E.outline_every_n(25, 10)
    assert [b.page for b in items] == [0, 10, 20]
    assert items[-1].title == "Pagine 21-25"


def test_outline_from_headings():
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 80), f"Capitolo {i + 1}", fontsize=22)
        p.insert_text((72, 120), f"Sezione {i + 1}.1", fontsize=16)
        for k in range(10):
            p.insert_text((72, 150 + k * 14), "testo normale del corpo del documento " * 2, fontsize=10)
    items = E.outline_from_headings(doc)
    assert [(b.level, b.title, b.page) for b in items][:2] == [(1, "Capitolo 1", 0), (2, "Sezione 1.1", 0)]
    assert len(items) == 6


def test_verify_outline():
    items = _items()
    items.append(E.Bookmark(1, "Cap 3", 8))
    items.append(E.Bookmark(1, "Rotto", 50))
    problems = dict((i, why) for i, why in E.verify_outline(items, 10))
    assert problems[7] == "broken" and problems[6] == "duplicate"


def test_regex_search():
    _d, page = _page(["CAP 20121 Milano, 00184 Roma", "telefono 0212345678 e 10100"])
    pt = E.extract_page_text(page)
    assert len(E.find_in_page_text(pt, r"\b\d{5}\b", E.SearchOptions(regex=True))) == 3
    assert len(E.find_in_page_text(pt, r"milano|roma", E.SearchOptions(regex=True))) == 2
    assert len(E.find_in_page_text(pt, r"milano", E.SearchOptions(regex=True, case_sensitive=True))) == 0
    with pytest.raises(ValueError):
        E.compile_query("(", E.SearchOptions(regex=True))


def test_snippet_does_not_spill_into_next_line():
    _d, page = _page(["CAP 20121 Milano, 00184 Roma", "telefono 0212345678"])
    pt = E.extract_page_text(page)
    hits = E.find_in_page_text(pt, "20121")
    assert E.context_snippet(pt, hits[0])[1] == "20121"


# ------------------------------------------------------- ordine di lettura (VIS-24)
def _two_columns():
    """A4 con due caselle affiancate; la colonna destra è più corta."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 100, 280, 400),
                        "Colonna sinistra con il Submariner. Testo che continua e va a capo. "
                        "Altre righe della colonna sinistra per renderla più lunga di quella destra.",
                        fontsize=11)
    page.insert_textbox(pymupdf.Rect(315, 100, 545, 200), "Colonna destra: Speedmaster.", fontsize=11)
    page.insert_textbox(pymupdf.Rect(50, 40, 545, 70), "Titolo a tutta pagina", fontsize=14)
    return pymupdf.open("pdf", doc.tobytes())


def test_two_columns_follow_reading_order():
    doc = _two_columns()
    text = E.extract_page_text(doc[0]).text
    assert text.index("Titolo") < text.index("Submariner") < text.index("Speedmaster")
    assert text.index("renderla") < text.index("Speedmaster")     # tutta la colonna sinistra prima
    pt = E.extract_page_text(doc[0])
    assert E.find_spans(pt, "Submariner") and E.find_spans(pt, "Speedmaster")
    assert not E.find_spans(pt, "Speedmaster. Colonna sinistra")    # niente frasi a cavallo delle colonne


def test_reading_order_single_column_top_to_bottom():
    blocks = [{"bbox": (50, 300, 500, 320), "n": 2}, {"bbox": (50, 100, 500, 120), "n": 1},
              {"bbox": (60, 500, 480, 520), "n": 3}]
    assert [b["n"] for b in E.reading_order(blocks)] == [1, 2, 3]


def test_reading_order_columns_and_wide_blocks():
    blocks = [{"bbox": (320, 120, 540, 150), "n": "R1"}, {"bbox": (50, 120, 280, 400), "n": "L1"},
              {"bbox": (50, 40, 540, 70), "n": "T"}, {"bbox": (50, 450, 540, 480), "n": "W"},
              {"bbox": (320, 500, 540, 520), "n": "R2"}, {"bbox": (50, 500, 280, 560), "n": "L2"}]
    assert [b["n"] for b in E.reading_order(blocks)] == ["T", "L1", "R1", "W", "L2", "R2"]


# ------------------------------------------------- segnalibri dalla struttura (VIS-17)
def _tagged_pdf(headings, rolemap=None, alt=None):
    """PDF taggato costruito a mano: `headings` = per pagina, elenco di (ruolo, testo); ogni
    titolo è un contenuto marcato con MCID e un elemento della struttura con /Pg."""
    doc = pymupdf.open()
    for _ in headings:
        doc.new_page()
    font_page = doc[0]
    font_page.insert_text((10, 10), " ", fontname="helv")
    font_res = doc.xref_get_key(font_page.xref, "Resources")
    root = doc.get_new_xref()
    doc.update_object(root, "<<>>")
    elems = []
    for pi, items in enumerate(headings):
        page = doc[pi]
        if pi:
            doc.xref_set_key(page.xref, "Resources", font_res[1] if font_res[0] == "xref" else font_res[1])
        stream = "BT /helv 14 Tf\n"
        y = 780
        for mcid, (tag, text) in enumerate(items):
            stream += f"/{tag} <</MCID {mcid}>> BDC 1 0 0 1 72 {y} Tm ({text}) Tj EMC\n"
            y -= 30
            x = doc.get_new_xref()
            extra = f" /Alt ({alt[text]})" if alt and text in alt else ""
            doc.update_object(x, f"<< /Type /StructElem /S /{tag} /P {root} 0 R /Pg {page.xref} 0 R /K {mcid}{extra} >>")
            elems.append(x)
        stream += "ET\n"
        cx = doc.get_new_xref()
        doc.update_object(cx, "<<>>")
        doc.update_stream(cx, stream.encode())
        old = doc.xref_get_key(page.xref, "Contents")
        doc.xref_set_key(page.xref, "Contents", f"[{old[1]} {cx} 0 R]" if old[0] == "xref" else f"{cx} 0 R")
        doc.xref_set_key(page.xref, "StructParents", str(pi))
    rm = (" /RoleMap << " + " ".join(f"/{a} /{b}" for a, b in rolemap.items()) + " >>") if rolemap else ""
    doc.update_object(root, "<< /Type /StructTreeRoot /K [" + " ".join(f"{x} 0 R" for x in elems) + f"]{rm} >>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
    return pymupdf.open("pdf", doc.tobytes())


def test_outline_from_structure_5_h1_12_h2():
    """Dato un PDF taggato con 5 H1 e 12 H2, l'albero ha 5 voci di primo livello e 12 di
    secondo con destinazioni sulle pagine corrette."""
    pages = []
    n2 = 0
    for c in range(5):
        items = [("H1", f"Capitolo {c + 1}")]
        for _s in range(3 if c < 2 else 2):
            n2 += 1
            items.append(("H2", f"Sezione {n2}"))
        items.append(("P", "testo normale"))
        pages.append(items)
    assert n2 == 12
    pdf = _tagged_pdf(pages)
    items = E.outline_from_structure(pdf)
    assert [b.level for b in items].count(1) == 5 and [b.level for b in items].count(2) == 12
    assert [(b.title, b.page) for b in items if b.level == 1] == [(f"Capitolo {c + 1}", c) for c in range(5)]
    assert items[1].title == "Sezione 1" and items[1].page == 0
    assert all(b.dest["page"] == b.page for b in items)
    assert "testo normale" not in [b.title for b in items]
    assert E.is_valid_structure(items)


def test_outline_from_structure_rolemap_alt_and_untagged():
    pdf = _tagged_pdf([[("Titolo1", "Primo"), ("Sottotitolo", "Secondo")]],
                      rolemap={"Titolo1": "H1", "Sottotitolo": "H2"}, alt={"Primo": "Primo capitolo"})
    items = E.outline_from_structure(pdf)
    assert [(b.level, b.title) for b in items] == [(1, "Primo capitolo"), (2, "Secondo")]
    plain = pymupdf.open()
    plain.new_page().insert_text((72, 100), "senza struttura")
    assert E.outline_from_structure(plain) == []
