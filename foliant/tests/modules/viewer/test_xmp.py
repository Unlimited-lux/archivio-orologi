"""VIS-41 — Metadati avanzati (XMP)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.viewer import xmp_engine as X


def _ctl(window):
    return window.ctx.viewer_parts["xmp"]


def _pdf(path, title="Documento"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), title)
    doc.set_metadata({"title": title})
    doc.save(str(path))
    return str(path)


def test_roundtrip_kinds_and_raw_structures():
    p = X.Packet()
    p.set("dc", "creator", "Anna; Bruno")
    p.set("dc", "title", "Manuale")
    p.set("dc", "subject", ["orologi", "garanzia"])
    p.set("xmpRights", "Marked", "True")
    p.set("foliant", "Cliente", "Rossi")
    q = X.parse_xmp(X.build_xmp(p))
    assert q.get("dc", "creator").value == ["Anna", "Bruno"] and q.get("dc", "creator").kind == X.SEQ
    assert q.get("dc", "title").value == "Manuale" and q.get("dc", "title").kind == X.ALT
    assert q.get("dc", "subject").kind == X.BAG
    assert q.get("foliant", "Cliente").value == "Rossi"
    with pytest.raises(ValueError, match="riga"):
        X.parse_xmp("<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n<rdf:RDF>")


def test_template_applied_to_three_open_documents(viewer_window, tmp_path):
    """Dato un modello con autore «Ufficio legale» e diritti «Riservato», applicato a 3 documenti
    aperti che poi vengono salvati, ciascuno contiene in XMP l'autore e lo stato dei diritti."""
    ctl = _ctl(viewer_window)
    template = X.Packet()
    template.set("dc", "creator", "Ufficio legale")
    template.set("dc", "rights", "Riservato")
    template.set("xmpRights", "Marked", "True")
    ctl.save_template("Legale", template)
    assert "Legale" in ctl.templates()
    views = [viewer_window.open_path(_pdf(tmp_path / f"doc{i}.pdf", f"Doc {i}")) for i in range(3)]
    assert ctl.apply_template("Legale", viewer_window.ctx.views()) == 3
    for i, view in enumerate(views):
        out = tmp_path / f"salvato{i}.pdf"
        view.document.save_copy(str(out))
        view.document._saved_state = view.document._state
        packet = X.parse_xmp(pymupdf.open(str(out)).get_xml_metadata())
        assert packet.get("dc", "creator").value == ["Ufficio legale"]
        assert packet.get("dc", "rights").text() == "Riservato"
        assert packet.get("xmpRights", "Marked").text() == "True"
        assert pymupdf.open(str(out)).metadata["author"] == "Ufficio legale"
    ctl.delete_template("Legale")
    assert "Legale" not in ctl.templates()


def test_export_import_dublin_core_identical(viewer_window, tmp_path, monkeypatch):
    """Dato un XMP esportato da un documento e importato in un altro, le proprietà Dublin Core
    dei due pacchetti sono identiche."""
    ctl = _ctl(viewer_window)
    a = viewer_window.open_path(_pdf(tmp_path / "a.pdf", "Primo"))
    p = X.Packet()
    p.set("dc", "title", "Catalogo 2026")
    p.set("dc", "creator", ["Anna", "Bruno"])
    p.set("dc", "description", "Orologi d'epoca")
    p.set("dc", "subject", ["Rolex", "Omega"])
    assert ctl.write(a, p)
    xmp_path = ctl.export_xmp(a, str(tmp_path / "catalogo.xmp"))
    b = viewer_window.open_path(_pdf(tmp_path / "b.pdf", "Secondo"))
    assert ctl.import_xmp(b, xmp_path, replace=False)
    assert ctl.read(a).dublin_core() == ctl.read(b).dublin_core()
    assert ctl.read(b).dublin_core()["title"] == "Catalogo 2026"
    for v in (a, b):
        v.document._saved_state = v.document._state


def test_invalid_xmp_import_reports_line(viewer_window, tmp_path, monkeypatch):
    ctl = _ctl(viewer_window)
    view = viewer_window.open_path(_pdf(tmp_path / "c.pdf"))
    bad = tmp_path / "rotto.xmp"
    bad.write_text("<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n<rdf:RDF>\n", encoding="utf-8")
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    assert not ctl.import_xmp(view, str(bad))
    assert "riga" in errors[0] and not view.document.is_modified


def test_pdfa_custom_property_creates_extension_schema(viewer_window, tmp_path):
    """Dato un PDF/A-2b con una proprietà personalizzata aggiunta, il pacchetto XMP salvato
    contiene la descrizione dello schema di estensione corrispondente."""
    ctl = _ctl(viewer_window)
    view = viewer_window.open_path(_pdf(tmp_path / "pdfa.pdf"))
    p = ctl.read(view)
    p.set("pdfaid", "part", "2")
    p.set("pdfaid", "conformance", "B")
    assert ctl.write(view, p)
    p = ctl.read(view)
    p.set("foliant", "Cliente", "Rossi")
    assert ctl.write(view, p)
    out = tmp_path / "pdfa_salvato.pdf"
    view.document.save_copy(str(out))
    view.document._saved_state = view.document._state
    xmp = pymupdf.open(str(out)).get_xml_metadata()
    assert "pdfaExtension:schemas" in xmp and "http://ns.foliant.app/custom/1.0/" in xmp
    assert "<pdfaProperty:name>Cliente</pdfaProperty:name>" in xmp
    assert X.parse_xmp(xmp).get("foliant", "Cliente").value == "Rossi"


def test_dialog_edits_rights_and_custom_property(viewer_window, tmp_path, monkeypatch):
    from foliant.modules.viewer.xmp import MetadataDialog
    ctl = _ctl(viewer_window)
    view = viewer_window.open_path(_pdf(tmp_path / "d.pdf"))
    dlg = MetadataDialog(ctl, view)
    dlg.copyright.setCurrentIndex(dlg.copyright.findData("True"))
    dlg.notice.setText("© 2026 Archivio Orologi")
    dlg.url.setText("https://example.com/diritti")
    assert dlg.add_property("foliant", "Lotto", "A-17")
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    assert not dlg.add_property("foliant", "1nome", "x")          # nome XML non valido
    assert "Nome non valido" in errors[0]
    packet = dlg.packet_with_rights()
    assert ctl.write(view, packet)
    again = ctl.read(view)
    assert again.get("xmpRights", "Marked").text() == "True"
    assert again.get("dc", "rights").text() == "© 2026 Archivio Orologi"
    assert again.get("xmpRights", "WebStatement").text() == "https://example.com/diritti"
    assert again.get("foliant", "Lotto").text() == "A-17"
    assert view.document.undo_label == "Metadati avanzati"
    view.document._saved_state = view.document._state


def test_properties_dialog_offers_advanced_metadata(viewer_window, tmp_path, monkeypatch):
    view = viewer_window.open_path(_pdf(tmp_path / "e.pdf"))
    dlg = viewer_window.ctx.viewer_parts["properties"].show_dialog()
    called = []
    monkeypatch.setattr(_ctl(viewer_window), "open_dialog", lambda v=None: called.append(v))
    dlg.metadata_button.click()
    assert called == [view]
    dlg.hide()
