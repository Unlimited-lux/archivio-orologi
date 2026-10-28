"""Ispezione, sanificazione e metadati: SEC-21, SEC-22, SEC-23, SEC-24 (logica)."""

from __future__ import annotations

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
