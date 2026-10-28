"""Ispezione, sanificazione e metadati: SEC-21, SEC-22, SEC-23, SEC-24 (logica)."""

from __future__ import annotations

import re

import pymupdf

from foliant.api import Document
from foliant.modules.security import sanitize as S


def test_scan_active(active_pdf):
    pdf = pymupdf.open(active_pdf)
    rep = S.scan(pdf)
    assert rep.javascript == 1 and rep.embedded_files == 1 and rep.comments == 1
    assert rep.external_links == ["https://esterno.example.com/traccia"]
    assert rep.risky and rep.has_metadata


def test_scan_plain(sample_pdf):
    rep = S.scan(pymupdf.open(sample_pdf))
    assert not rep.risky and rep.javascript == 0


def test_launch_and_submit_detected(tmp_path):
    path = tmp_path / "azioni.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(10, 10, 50, 50), "file": "/bin/sh"})
    x = doc.get_new_xref()
    doc.update_object(x, "<< /S /SubmitForm /F (https://raccolta.example.com) >>")
    doc.xref_set_key(doc.pdf_catalog(), "AA", f"<< /WC {x} 0 R >>")
    doc.save(str(path))
    rep = S.scan(pymupdf.open(path))
    assert rep.launch == 1 and rep.submit_forms == 1 and rep.risky


def test_sanitize_defaults(active_pdf, tmp_path):
    doc = Document.open(active_pdf)
    with doc.change("Sanifica") as pdf:
        rep = S.sanitize(pdf, S.default_sanitize_options())
    assert "javascript" in rep.done and "metadata" in rep.done
    out = tmp_path / "pulito.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    after = S.scan(pdf)
    assert after.javascript == 0 and after.embedded_files == 0 and after.comments == 0
    assert not after.has_metadata
    assert b"JavaScript" not in out.read_bytes() and b"dati riservati" not in out.read_bytes()
    # collegamenti mantenuti per impostazione predefinita
    assert after.external_links
    doc.close()


def test_sanitize_render_identical(sample_pdf, tmp_path):
    before = pymupdf.open(sample_pdf)[0].get_pixmap().samples
    doc = Document.open(sample_pdf)
    with doc.change("Sanifica") as pdf:
        S.sanitize(pdf, S.default_sanitize_options())
    out = tmp_path / "s.pdf"
    doc.save(out)
    assert pymupdf.open(out)[0].get_pixmap().samples == before
    doc.close()


def test_hidden_layer_removed(layered_pdf, tmp_path):
    doc = Document.open(layered_pdf)
    with doc.change("Sanifica") as pdf:
        assert S.scan(pdf).hidden_layers == 1
        S.sanitize(pdf, {"hidden_layers": True})
    out = tmp_path / "livelli.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    text = pdf[0].get_text()
    assert "segreto" not in text and "Testo del livello visibile" in text and "sempre presente" in text
    assert pdf.xref_get_key(pdf.pdf_catalog(), "OCProperties")[0] == "null"
    doc.close()


def test_strip_marked_content_nested():
    data = (b"q BT (a) Tj ET Q /OC /L1 BDC BT (segreto \\) EMC) Tj ET /Span <</A 1>> BDC (x) Tj EMC EMC "
            b"/OC /L2 BDC (visibile) Tj EMC")
    out, n = S.strip_marked_content(data, {b"L1"})
    assert n == 1 and b"segreto" not in out and b"visibile" in out and b"(a) Tj" in out


def test_strip_marked_content_unbalanced_untouched():
    data = b"/OC /L1 BDC (x) Tj"
    assert S.strip_marked_content(data, {b"L1"}) == (data, 0)


def test_form_data_reset_and_remove(tmp_path):
    path = tmp_path / "modulo.pdf"
    src = pymupdf.open()
    page = src.new_page()
    w = pymupdf.Widget()
    w.field_name = "nome"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(100, 100, 300, 120)
    w.field_value = "Mario Rossi"
    page.add_widget(w)
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Azzera") as pdf:
        S.sanitize(pdf, {"form_data": True})
        assert [x.field_value for x in pdf[0].widgets()] == [""]
    doc.undo()
    with doc.change("Rimuovi campi") as pdf:
        S.sanitize(pdf, {"form_data": True, "remove_fields": True})
        assert list(pdf[0].widgets()) == []
    doc.close()


def test_metadata_xmp_sync(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.lock:
        created = doc.pdf.metadata.get("creationDate")
    with doc.change("Metadati") as pdf:
        S.write_metadata(pdf, {"title": "Relazione 2026 – Città", "author": "Anna Bianchi; Luca Verdi",
                               "keywords": "orologi, perizia; vendita"})
    out = tmp_path / "meta.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    assert pdf.metadata["title"] == "Relazione 2026 – Città"
    assert pdf.metadata["creationDate"] == created
    xmp = pdf.get_xml_metadata()
    assert '<rdf:li xml:lang="x-default">Relazione 2026 – Città</rdf:li>' in xmp
    assert "<rdf:Seq><rdf:li>Anna Bianchi</rdf:li><rdf:li>Luca Verdi</rdf:li></rdf:Seq>" in xmp
    assert xmp.count("<rdf:li>") >= 5 and "xmp:ModifyDate" in xmp
    doc.close()


def test_sanitize_keeps_pdfa_identification(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("PDF/A") as pdf:
        pdf.set_metadata({"author": "Mario Rossi"})
        pdf.set_xml_metadata(S.minimal_xmp({"author": "Mario Rossi"}, ("2", "B")))
    with doc.change("Sanifica") as pdf:
        S.sanitize(pdf, {"metadata": True})
        xmp = pdf.get_xml_metadata()
        assert "Mario Rossi" not in xmp and "<pdfaid:part>2</pdfaid:part>" in xmp
        assert not pdf.metadata.get("author")
    doc.close()


def test_inspect_extra(tmp_path, active_pdf):
    path = tmp_path / "nascosto.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "visibile", fontsize=12)
    page.insert_text((72, 140), "invisibile", fontsize=12, render_mode=3)
    a = page.add_text_annot((300, 300), "nascosta")
    a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
    a.update()
    src.set_toc([[1, "Capitolo", 1]])
    src.save(str(path))
    extra = S.inspect_extra(pymupdf.open(path))
    assert extra["hidden_text"] == 1 and extra["hidden_annots"] == 1 and extra["bookmarks"] == 1
    assert extra["ocr_text"] == 0
    meta = S.inspect_extra(pymupdf.open(active_pdf))["metadata"]
    assert any("Ufficio vendite" in m for m in meta)


def test_ocr_layer_classified(tmp_path, png_bytes):
    path = tmp_path / "scansione.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_image(page.rect, stream=png_bytes, keep_proportion=False)
    page.insert_text((72, 100), "testo riconosciuto", fontsize=12, render_mode=3)
    src.save(str(path))
    extra = S.inspect_extra(pymupdf.open(path))
    assert extra["ocr_text"] == 1 and extra["hidden_text"] == 0


def test_previous_revisions(sample_pdf):
    assert S.previous_revisions(sample_pdf) == 0
    pdf = pymupdf.open(sample_pdf)
    pdf[0].insert_text((72, 300), "aggiunta", fontsize=12)
    pdf.saveIncr()
    pdf.close()
    assert S.previous_revisions(sample_pdf) == 1


def test_flatten(tmp_path):
    path = tmp_path / "f.pdf"
    src = pymupdf.open()
    page = src.new_page()
    a = page.add_freetext_annot(pymupdf.Rect(100, 100, 300, 140), "Nota fusa", fontsize=12)
    a.update()
    src.save(str(path))
    doc = Document.open(path)
    with doc.change("Appiattisci") as pdf:
        S.flatten(pdf)
        assert list(pdf[0].annots()) == []
        assert "Nota fusa" in pdf[0].get_text()
    doc.close()


def test_format_pdf_date():
    assert S.format_pdf_date("D:20240312101500+01'00'") == "12/03/2024 10:15"
    assert S.pdf_date_to_iso("D:20240312101500+01'00'") == "2024-03-12T10:15:00+01:00"


def test_exif_removed_render_identical(tmp_path):
    import io

    from PIL import Image
    img = Image.new("RGB", (40, 30), (200, 50, 50))
    exif = Image.Exif()
    exif[0x010F] = "Fotocamera-GPS-45N"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    path = tmp_path / "foto.pdf"
    src = pymupdf.open()
    src.new_page().insert_image(pymupdf.Rect(10, 10, 110, 85), stream=buf.getvalue())
    src.save(str(path))
    before = pymupdf.open(path)[0].get_pixmap().samples
    assert S.inspect_extra(pymupdf.open(path))["image_metadata"] == 1
    doc = Document.open(path)
    with doc.change("Ripulisci") as pdf:
        S.sanitize(pdf, {"metadata": True})
    out = tmp_path / "foto-ripulita.pdf"
    doc.save(out)
    data = out.read_bytes()
    assert b"Exif" not in data and b"Fotocamera-GPS" not in data
    assert pymupdf.open(out)[0].get_pixmap().samples == before
    doc.close()


def test_strip_jpeg_metadata_rejects_non_jpeg():
    assert S.strip_jpeg_metadata(b"not a jpeg") == b"not a jpeg"


def _xmp_tree(xmp: str):
    import xml.etree.ElementTree as ET
    body = xmp[xmp.index("<x:xmpmeta"):xmp.index("</x:xmpmeta>") + len("</x:xmpmeta>")]
    return ET.fromstring(body)


def test_custom_properties_info_and_xmp(sample_pdf, tmp_path):
    """Dato «Pratica» = «2026/118», quando si salva, allora /Info ha la chiave /Pratica
    e l'XMP la stessa proprietà; togliendola sparisce da entrambi."""
    doc = Document.open(sample_pdf)
    with doc.change("Metadati") as pdf:
        S.write_metadata(pdf, {"title": "Perizia", "custom": {"Pratica": "2026/118", "Cliente": "Città – Rossi"}})
    out = tmp_path / "custom.pdf"
    doc.save(out)
    pdf = pymupdf.open(out)
    info = int(pdf.xref_get_key(-1, "Info")[1].split()[0])
    assert pdf.xref_get_key(info, "Pratica") == ("string", "2026/118")
    assert S.read_custom(pdf) == {"Pratica": "2026/118", "Cliente": "Città – Rossi"}
    root = _xmp_tree(pdf.get_xml_metadata())
    ns = "{http://ns.adobe.com/pdfx/1.3/}"
    assert root.find(f".//{ns}Pratica").text == "2026/118"
    assert "pdfaExtension:schemas" not in pdf.get_xml_metadata()
    # una modifica dei soli campi standard conserva le proprietà
    with doc.change("Titolo") as p:
        S.write_metadata(p, {"title": "Perizia 2"})
        assert S.read_custom(p) == {"Pratica": "2026/118", "Cliente": "Città – Rossi"}
    with doc.change("Rimuovi") as p:
        S.write_metadata(p, {"custom": {"Cliente": "Città – Rossi"}})
        assert S.read_custom(p) == {"Cliente": "Città – Rossi"}
        assert "Pratica" not in p.get_xml_metadata()
    # la sanificazione dei metadati toglie anche le proprietà personalizzate
    with doc.change("Sanifica") as p:
        S.sanitize(p, {"metadata": True})
        assert S.read_custom(p) == {}
    doc.close()


def test_custom_property_pdfa_extension_schema(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("PDF/A") as pdf:
        pdf.set_xml_metadata(S.minimal_xmp({}, ("2", "B")))
    with doc.change("Metadati") as pdf:
        S.write_metadata(pdf, {"custom": {"Pratica": "2026/118"}})
    out = tmp_path / "pdfa.pdf"
    doc.save(out)
    root = _xmp_tree(pymupdf.open(out).get_xml_metadata())
    ext = "{http://www.aiim.org/pdfa/ns/extension/}"
    sch = "{http://www.aiim.org/pdfa/ns/schema#}"
    prop = "{http://www.aiim.org/pdfa/ns/property#}"
    schema = root.find(f".//{ext}schemas")
    assert schema.find(f".//{sch}namespaceURI").text == "http://ns.adobe.com/pdfx/1.3/"
    assert schema.find(f".//{sch}prefix").text == "pdfx"
    assert [e.text for e in schema.iter(f"{prop}name")] == ["Pratica"]
    assert root.find(".//{http://www.aiim.org/pdfa/ns/id/}part").text == "2"
    doc.close()


def test_custom_property_names():
    assert S.check_property_name("Pratica") == ""
    assert S.check_property_name("Codice_cliente") == ""
    assert S.check_property_name("Numero pratica")
    assert S.check_property_name("1abc")
    assert S.check_property_name("Title")


def test_mod_date_has_timezone(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Metadati") as pdf:
        S.write_metadata(pdf, {"title": "X"})
        mod = pdf.metadata["modDate"]
        xmp = pdf.get_xml_metadata()
    assert re.fullmatch(r"D:\d{14}[+-]\d{2}'\d{2}'", mod)
    iso = S.pdf_date_to_iso(mod)
    assert re.search(r"[+-]\d{2}:\d{2}$", iso) and f"<xmp:ModifyDate>{iso}</xmp:ModifyDate>" in xmp
    doc.close()
