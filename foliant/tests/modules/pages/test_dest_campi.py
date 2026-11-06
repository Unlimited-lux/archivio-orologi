"""PAG-03 / PAG-19: destinazioni nominate, collegamenti e campi delle pagine tolte."""

from __future__ import annotations

import pymupdf

from foliant.modules.pages import engine as E


def _contratto(pages: int = 6, kids: bool = False, old_dests: bool = False, fields: bool = False) -> pymupdf.Document:
    """Contratto di compravendita di un orologio (dati inventati).

    Destinazione nominata «garanzia» → pagina 3, «consegna» → pagina 5; collegamento
    GoTo /D (garanzia) e /Dest (consegna) sulla pagina 1. Con `fields`: campi
    «acquirente» (pagina 2) e «prezzo» (pagina 6) compilati."""
    doc = pymupdf.open()
    titles = ["Contratto di vendita", "Dati dell'acquirente", "Garanzia", "Condizioni",
              "Consegna", "Prezzo e pagamento", "Firme", "Allegati"]
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 90), f"{titles[i % len(titles)]} - Rolex Datejust 36 rif. 126234", fontsize=13)
    cat = doc.pdf_catalog()

    def dest(pno):
        x = doc.get_new_xref()
        doc.update_object(x, f"[{doc[pno].xref} 0 R /XYZ 0 800 0]")
        return x

    g, c = dest(2), dest(4)
    intro = dest(0)
    if old_dests:
        dx = doc.get_new_xref()
        doc.update_object(dx, f"<</garanzia {g} 0 R /consegna {c} 0 R /inizio {intro} 0 R>>")
        doc.xref_set_key(cat, "Dests", f"{dx} 0 R")
    elif kids:
        leaf1 = doc.get_new_xref()
        doc.update_object(leaf1, f"<</Limits[(consegna)(garanzia)]/Names[(consegna) {c} 0 R (garanzia) {g} 0 R]>>")
        leaf2 = doc.get_new_xref()
        doc.update_object(leaf2, f"<</Limits[(inizio)(inizio)]/Names[(inizio) {intro} 0 R]>>")
        root = doc.get_new_xref()
        doc.update_object(root, f"<</Kids[{leaf1} 0 R {leaf2} 0 R]>>")
        doc.xref_set_key(cat, "Names", f"<</Dests {root} 0 R>>")
    else:
        names = doc.get_new_xref()
        doc.update_object(names, f"<</Names[(consegna) {c} 0 R (garanzia) {g} 0 R (inizio) {intro} 0 R]>>")
        doc.xref_set_key(cat, "Names", f"<</Dests {names} 0 R>>")
    p1 = doc[0]
    p1.insert_link({"kind": pymupdf.LINK_NAMED, "name": "garanzia", "from": pymupdf.Rect(72, 200, 260, 220)})
    p1.insert_link({"kind": pymupdf.LINK_NAMED, "name": "consegna", "from": pymupdf.Rect(72, 240, 260, 260)})
    if fields:
        for pno, name, value in ((1, "acquirente", "Mario Rossi"), (5, "prezzo", "8500")):
            w = pymupdf.Widget()
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            w.field_name = name
            w.field_value = value
            w.rect = pymupdf.Rect(72, 300, 300, 320)
            doc[pno].add_widget(w)
    data = doc.tobytes()
    doc.close()
    return pymupdf.open(stream=data, filetype="pdf")


def _reopen(doc: pymupdf.Document) -> pymupdf.Document:
    return pymupdf.open(stream=doc.tobytes(garbage=3), filetype="pdf")


def _named_links(doc: pymupdf.Document, pno: int = 0) -> set[str]:
    return {lk.get("nameddest") for lk in doc[pno].get_links() if lk.get("nameddest")}


def _field_values(doc: pymupdf.Document) -> dict[str, str]:
    cat = doc.pdf_catalog()
    k, fields = doc.xref_get_key(cat, "AcroForm/Fields")
    out = {}
    for x in E._split_array(fields) if k == "array" else []:
        fx = E._ref(x)
        kt, t = doc.xref_get_key(fx, "T")
        kv, v = doc.xref_get_key(fx, "V")
        out[t] = v
    return out


def test_delete_removes_named_dest_and_link():
    doc = _reopen(_contratto())
    E.delete_pages(doc, [2])
    out = _reopen(doc)
    names = out.resolve_names()
    assert "garanzia" not in names
    assert names["consegna"]["page"] == 3 and names["inizio"]["page"] == 0
    assert _named_links(out) == {"consegna"}


def test_delete_name_tree_with_kids_updates_limits():
    doc = _reopen(_contratto(kids=True))
    E.delete_pages(doc, [2])
    out = _reopen(doc)
    names = out.resolve_names()
    assert set(names) == {"consegna", "inizio"}
    assert names["consegna"]["page"] == 3
    assert _named_links(out) == {"consegna"}
    # nessuna voce con pagina inesistente e /Limits coerenti
    assert all(v.get("page", -1) >= 0 for v in names.values())


def test_delete_old_catalog_dests():
    doc = _reopen(_contratto(old_dests=True))
    E.delete_pages(doc, [2, 4])
    out = _reopen(doc)
    k, dests = out.xref_get_key(out.pdf_catalog(), "Dests")
    holder = int(dests.split()[0])
    assert set(out.xref_get_keys(holder)) == {"inizio"}
    assert _named_links(out) == set()


def test_extract_keeps_only_live_dests():
    doc = _contratto()
    out = pymupdf.open(stream=E.extract_pages(doc, [0, 1, 4]), filetype="pdf")
    names = out.resolve_names()
    assert "garanzia" not in names
    assert names["consegna"]["page"] == 2
    assert _named_links(out) == {"consegna"}


def test_split_parts_drop_dead_dests():
    doc = _contratto()
    plan = E.split_document(doc, "every", 3)
    first = pymupdf.open(stream=E.split_to_bytes(doc, plan)[0], filetype="pdf")
    names = first.resolve_names()
    assert set(names) == {"garanzia", "inizio"}      # pagine 1-3
    assert _named_links(first) == {"garanzia"}


def test_replace_fewer_pages_drops_dest_of_removed_page():
    doc = _reopen(_contratto())
    src = pymupdf.open()
    src.new_page().insert_text((72, 90), "Nuova garanzia")
    E.replace_pages(doc, [2, 4], src, [0])        # pagina 3 sostituita, pagina 5 tolta
    out = _reopen(doc)
    names = out.resolve_names()
    assert names["garanzia"]["page"] == 2          # stesso oggetto pagina: resta valida
    assert "consegna" not in names
    assert _named_links(out) == {"garanzia"}


def test_replace_removes_fields_of_replaced_pages():
    doc = _reopen(_contratto(fields=True))
    assert _field_values(doc) == {"acquirente": "Mario Rossi", "prezzo": "8500"}
    src = pymupdf.open()
    for t in ("Scheda tecnica", "Certificato di autenticità"):
        src.new_page().insert_text((72, 90), t)
    E.replace_pages(doc, [1, 5], src, [0, 1])
    out = _reopen(doc)
    assert _field_values(out) == {}
    assert not any(list(out[i].widgets()) for i in range(out.page_count))
    assert out.page_count == 6


def test_pdf_items_strings_and_nested():
    items = E._pdf_items(r"[(garanzia rolex) 5 0 R (a\)b [x]) [3 0 R /XYZ null null 0] <FEFF0061> <</D[7 0 R /Fit]>>]")
    assert items == ["(garanzia rolex)", "5 0 R", r"(a\)b [x])", "[3 0 R /XYZ null null 0]", "<FEFF0061>",
                     "<</D[7 0 R /Fit]>>"]
    assert E._pdf_string(items[0]) == "garanzia rolex"
    assert E._pdf_string(items[2]) == "a)b [x]"
    assert E._pdf_string(items[4]) == "a"
