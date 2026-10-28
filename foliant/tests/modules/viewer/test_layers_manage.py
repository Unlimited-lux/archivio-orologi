"""VIS-21 — Gestione dei livelli: criteri di accettazione sul motore e sul pannello."""

from __future__ import annotations

import pymupdf

from foliant.modules.viewer import layers_manage as M


def _two_layers(tmp_path=None, with_annot=False, with_xobject=False):
    doc = pymupdf.open()
    a = doc.add_ocg("A", on=True)
    b = doc.add_ocg("B", on=True)
    page = doc.new_page()
    page.insert_text((72, 100), "Testo del livello A", fontsize=14, oc=a)
    page.insert_text((72, 200), "Testo del livello B", fontsize=14, oc=b)
    page.insert_text((72, 300), "Contenuto di base", fontsize=14)
    if with_xobject:
        src = pymupdf.open()
        src.new_page(width=200, height=100).insert_text((10, 50), "Modulo B", fontsize=12)
        page.show_pdf_page(pymupdf.Rect(72, 400, 272, 500), src, 0, oc=b)
    if with_annot:
        annot = page.add_text_annot((400, 100), "nota di B")
        doc.xref_set_key(annot.xref, "OC", f"{b} 0 R")
    data = doc.tobytes()
    if tmp_path is not None:
        path = tmp_path / "livelli.pdf"
        path.write_bytes(data)
        return str(path)
    return pymupdf.open("pdf", data)


def _by_name(pdf):
    return {name: x for x, name in M.ocgs(pdf).items()}


def test_hidden_at_opening_saved():
    """Dato un documento con i livelli A e B, impostando B nascosto all'apertura e salvando, alla
    riapertura B è OFF nella configurazione predefinita."""
    pdf = _two_layers()
    b = _by_name(pdf)["B"]
    M.set_default_visible(pdf, b, False)
    again = pymupdf.open("pdf", pdf.tobytes())
    b2 = _by_name(again)["B"]
    assert b2 in again.get_layer(-1).get("off", [])
    assert not M.default_visible(again, b2) and M.default_visible(again, _by_name(again)["A"])


def test_merge_b_into_a():
    """Data l'unione di B in A, il contenuto prima marcato B è marcato A e B non compare più in
    /OCGs."""
    pdf = _two_layers(with_annot=True, with_xobject=True)
    names = _by_name(pdf)
    a, b = names["A"], names["B"]
    M.merge_layers(pdf, [b], a)
    pdf = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    assert list(M.ocgs(pdf).values()) == ["A"]
    a = _by_name(pdf)["A"]
    props = M._properties(pdf, pdf[0].xref)
    assert set(props.values()) == {a}                     # tutte le sezioni marcate puntano ad A
    ocprops = pdf.xref_get_key(pdf.pdf_catalog(), "OCProperties")[1]
    assert f"{a} 0 R" in ocprops and ocprops.count(" 0 R") >= 1
    annot = next(pdf[0].annots())
    assert pdf.xref_get_key(annot.xref, "OC")[1] == f"{a} 0 R"
    # spegnendo A sparisce dalla resa anche il testo che era di B
    pdf.set_layer(-1, off=[a], on=[])
    pdf = pymupdf.open("pdf", pdf.tobytes())

    def blank(rect):
        pix = pdf[0].get_pixmap(dpi=72, clip=rect)
        return all(v == 255 for v in pix.samples)
    assert blank(pymupdf.Rect(70, 180, 300, 205))           # testo di B
    assert blank(pymupdf.Rect(70, 80, 300, 105))            # testo di A
    assert not blank(pymupdf.Rect(70, 280, 300, 305))       # contenuto di base


def test_flatten_keeps_visible_removes_hidden_and_renders_the_same():
    """Dato l'appiattimento con A visibile e B nascosto, il file salvato non contiene
    /OCProperties, il contenuto di A fa parte del contenuto di base, quello di B è rimosso e la
    resa coincide con la vista precedente."""
    pdf = _two_layers(with_annot=True, with_xobject=True)
    names = _by_name(pdf)
    a, b = names["A"], names["B"]
    pdf.set_layer(-1, on=[a], off=[b])
    pdf = pymupdf.open("pdf", pdf.tobytes())
    before = pdf[0].get_pixmap(dpi=72, annots=True).samples
    M.flatten(pdf, {a: True, b: False})
    saved = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    assert saved.xref_get_key(saved.pdf_catalog(), "OCProperties")[0] == "null"
    assert not saved.get_ocgs()
    text = saved[0].get_text()
    assert "livello A" in text and "Contenuto di base" in text
    assert "livello B" not in text and "Modulo B" not in text
    assert not list(saved[0].annots())
    assert b"/OC /MC" not in saved[0].read_contents()
    after = saved[0].get_pixmap(dpi=72, annots=True).samples
    assert before == after


def test_delete_layer_keep_or_remove_content():
    pdf = _two_layers()
    b = _by_name(pdf)["B"]
    M.delete_layer(pdf, b, keep_content=True)
    pdf = pymupdf.open("pdf", pdf.tobytes(garbage=3))
    assert list(M.ocgs(pdf).values()) == ["A"] and "livello B" in pdf[0].get_text()
    pdf2 = _two_layers()
    M.delete_layer(pdf2, _by_name(pdf2)["B"], keep_content=False)
    pdf2 = pymupdf.open("pdf", pdf2.tobytes(garbage=3))
    assert "livello B" not in pdf2[0].get_text() and "livello A" in pdf2[0].get_text()


def test_rename_add_lock():
    pdf = _two_layers()
    a = _by_name(pdf)["A"]
    M.rename_layer(pdf, a, "Quote")
    x = M.add_layer(pdf, "Note", on=False)
    M.set_locked(pdf, x, True)
    again = pymupdf.open("pdf", pdf.tobytes())
    names = _by_name(again)
    assert set(names) == {"Quote", "B", "Note"}
    assert names["Note"] in again.get_layer(-1).get("locked", [])
    assert not M.default_visible(again, names["Note"])


def test_rewrite_content_skips_inline_images_and_nesting():
    props = {"MC0": 10}
    data = (b"q /OC /MC0 BDC /Span <</MCID 1>> BDC (x) Tj EMC BI /W 1 /H 1 /BPC 8 /CS /G ID \x00EMC EI EMC "
            b"Q (base) Tj")
    assert M.rewrite_content(data, props, lambda x: "drop") == b"q   Q (base) Tj"
    unwrapped = M.rewrite_content(data, props, lambda x: "unwrap")
    assert unwrapped.startswith(b"q /OC BMC /Span") and unwrapped.count(b"EMC") == 2 + 1


# ------------------------------------------------------------------ pannello
def _panel(window, path):
    view = window.open_path(path)
    window.ctx.show_sidebar_panel("layers")
    panel = window.ctx.viewer_parts["layers"]
    panel.rebuild()
    return view, panel


def _layer(panel, name):
    return next(x for x in panel.layers() if x.text == name)


def test_panel_hidden_at_opening_then_reopen(viewer_window, tmp_path):
    view, panel = _panel(viewer_window, _two_layers(tmp_path))
    assert panel.set_default_visible(_layer(panel, "B"), False)
    assert view.document.undo_label == "Visibilità all'apertura"
    out = tmp_path / "salvato.pdf"
    view.document.save_copy(str(out))
    view.document._saved_state = view.document._state
    viewer_window.close_view(view)
    view, panel = _panel(viewer_window, str(out))
    assert not _layer(panel, "B").on and _layer(panel, "A").on


def test_panel_menu_merge_rename_delete_flatten(viewer_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu
    view, panel = _panel(viewer_window, _two_layers(tmp_path))
    menu = QMenu()
    panel.build_layer_menu(menu, _layer(panel, "B"))
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels == ["Rinomina…", "Visibile all'apertura", "Bloccato", "Unisci in", "Elimina livello…"]
    assert panel.rename(_layer(panel, "A"), "Quote")
    assert [x.text for x in panel.layers()] == ["Quote", "B"]
    assert panel.merge_into(_layer(panel, "B"), _layer(panel, "Quote"))
    assert [x.text for x in panel.layers()] == ["Quote"]
    view.document.undo()
    panel.rebuild()
    assert [x.text for x in panel.layers()] == ["Quote", "B"]
    monkeypatch.setattr(panel, "ask_delete_mode", lambda layer: "remove")
    assert panel.delete_layer(_layer(panel, "B"))
    assert [x.text for x in panel.layers()] == ["Quote"]
    assert "livello B" not in view.document.pdf[0].get_text()
    assert panel.new_layer("Note")
    assert {x.text for x in panel.layers()} == {"Quote", "Note"}
    monkeypatch.setattr(viewer_window.ctx, "confirm", lambda t, m: True)
    assert panel.flatten()
    assert panel.layers() == [] and panel.is_empty
    view.document._saved_state = view.document._state


def test_management_disabled_without_modify_permission(viewer_window, tmp_path):
    from PySide6.QtWidgets import QMenu
    pdf = _two_layers()
    path = tmp_path / "protetto.pdf"
    pdf.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    view, panel = _panel(viewer_window, str(path))
    assert not panel.can_manage()
    menu = QMenu()
    panel.build_layer_menu(menu, _layer(panel, "B"))
    assert not any(a.isEnabled() for a in menu.actions() if not a.isSeparator())
    assert not panel.rename(_layer(panel, "B"), "X")
