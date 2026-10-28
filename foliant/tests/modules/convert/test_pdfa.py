"""Test della conversione in PDF/A (CNV-14) e della rimozione della dichiarazione (CNV-15)."""

from __future__ import annotations

import os
import shutil

import pymupdf
import pytest
from PIL import Image, ImageChops

from foliant.api import OperationCancelled, PasswordRequired
from foliant.modules.convert import pdfa
from foliant.modules.convert.common import ConversionError

needs_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript non installato")


def _problem_pdf(**save) -> bytes:
    """Font standard non incorporato, trasparenza, JavaScript, link e azione Launch."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Archivio orologi PDF/A", fontname="helv", fontsize=14)
    page.insert_text((72, 100), "Seconda riga di testo", fontname="tiro", fontsize=11)
    page.draw_rect(pymupdf.Rect(100, 150, 300, 300), color=(1, 0, 0), fill=(0, 0, 1), fill_opacity=0.5)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80), "uri": "https://example.org"})
    js = doc.get_new_xref()
    doc.update_object(js, "<< /S /JavaScript /JS (app.alert\\('ciao'\\);) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    launch = doc.get_new_xref()
    doc.update_object(launch, "<< /S /Launch /F (calc.exe) >>")
    doc.xref_set_key(doc.pdf_catalog(), "AA", f"<< /WC {launch} 0 R >>")
    doc.set_metadata({"title": "Prova PDF/A", "author": "Foliant"})
    data = doc.tobytes(**save)
    doc.close()
    return data


def _all_fonts_embedded(data: bytes) -> bool:
    doc = pymupdf.open(stream=data)
    fonts = [f for p in doc for f in p.get_fonts(full=True)]
    return bool(fonts) and all(f[1] not in ("n/a", "") for f in fonts)


def test_analyze_lists_problems():
    probs = pdfa.analyze_pdfa(_problem_pdf(), level="1b")
    joined = "\n".join(probs)
    assert "Font non incorporati" in joined and "Helvetica" in joined
    assert "JavaScript" in joined
    assert "Launch" in joined
    assert "Trasparenze" in joined
    assert "OutputIntent" in joined
    assert "XMP" in joined


def test_verify_detects_problems():
    errs = pdfa.verify_pdfa(_problem_pdf(), "2b")
    assert any("Font" in e for e in errs) and any("JavaScript" in e for e in errs)
    assert any("OutputIntents" in e for e in errs)


def test_invalid_level():
    with pytest.raises(ConversionError):
        pdfa.verify_pdfa(_problem_pdf(), "4z")


def test_missing_ghostscript(monkeypatch):
    monkeypatch.setattr(pdfa.shutil, "which", lambda name: None)
    with pytest.raises(ConversionError) as exc:
        pdfa.convert_to_pdfa(_problem_pdf(), "2b")
    assert "Ghostscript" in str(exc.value) and "apt install ghostscript" in str(exc.value)


@needs_gs
def test_convert_2b():
    out, report = pdfa.convert_to_pdfa(_problem_pdf(), "2b")
    assert report["compliant"] and report["errors"] == [] and report["level"] == "2b"
    assert pdfa.verify_pdfa(out, "2b") == []
    assert _all_fonts_embedded(out)
    doc = pymupdf.open(stream=out)
    xmp = doc.get_xml_metadata()
    assert pdfa._pdfaid(xmp) == ("2", "B")
    assert b"JavaScript" not in doc.tobytes(expand=255)
    assert b"/Launch" not in doc.tobytes(expand=255)
    assert "Archivio orologi PDF/A" in doc[0].get_text()
    assert doc[0].get_links()[0]["uri"] == "https://example.org"
    assert report["fixes"]


@needs_gs
def test_convert_1b_flattens_transparency_keeps_look():
    src = _problem_pdf()
    out, report = pdfa.convert_to_pdfa(src, "1b")
    assert report["compliant"], report["errors"]
    assert pdfa.verify_pdfa(out, "1b") == []
    assert out.startswith(b"%PDF-1.4")
    doc, orig = pymupdf.open(stream=out), pymupdf.open(stream=src)
    assert pdfa._pdfaid(doc.get_xml_metadata()) == ("1", "B")
    assert "Archivio orologi" in doc[0].get_text()   # testo ancora ricercabile
    a, b = doc[0].get_pixmap(dpi=100), orig[0].get_pixmap(dpi=100)
    assert (a.width, a.height) == (b.width, b.height)
    ia = Image.frombytes("RGB", (a.width, a.height), a.samples)
    ib = Image.frombytes("RGB", (b.width, b.height), b.samples)
    delta = ImageChops.difference(ia, ib).split()
    mask = ImageChops.lighter(ImageChops.lighter(delta[0], delta[1]), delta[2]).point(lambda v: 255 if v > 8 else 0)
    diff = mask.histogram()[255]
    # tolleranza larga: l'antialiasing dei font sostitutivi cambia i bordi dei glifi
    assert diff / (a.width * a.height) <= 0.02


@needs_gs
def test_convert_3b_with_xml_attachment():
    doc = pymupdf.open(stream=_problem_pdf())
    doc.embfile_add("fattura.xml", b"<?xml version='1.0'?><fattura/>", filename="fattura.xml")
    out, report = pdfa.convert_to_pdfa(doc.tobytes(), "3b")
    assert report["compliant"], report["errors"]
    res = pymupdf.open(stream=out)
    assert "fattura.xml" in res.embfile_names()
    cat = res.pdf_catalog()
    assert res.xref_get_key(cat, "AF")[0] in ("array", "xref")
    af = res.xref_get_key(cat, "AF")[1]
    spec = int(af.strip("[] ").split()[0])
    assert res.xref_get_key(spec, "AFRelationship")[1].startswith("/")
    assert res.xref_get_key(spec, "EF/F")[0] == "xref"
    assert res.embfile_get("fattura.xml").startswith(b"<?xml")


@needs_gs
def test_encrypted_input_result_not_encrypted():
    data = _problem_pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="apri", owner_pw="padrone")
    with pytest.raises(PasswordRequired):
        pdfa.convert_to_pdfa(data, "2b")
    out, report = pdfa.convert_to_pdfa(data, "2b", password="apri")
    assert report["compliant"]
    doc = pymupdf.open(stream=out)
    assert not doc.needs_pass and doc.xref_get_key(-1, "Encrypt")[0] == "null"
    assert b"/Encrypt" not in out


@needs_gs
def test_strip_identification_keeps_content_streams():
    out, _r = pdfa.convert_to_pdfa(_problem_pdf(), "2b")
    stripped = pdfa.strip_pdfa_identification(out)
    a, b = pymupdf.open(stream=out), pymupdf.open(stream=stripped)
    assert "pdfaid" in a.get_xml_metadata()
    assert "pdfaid" not in b.get_xml_metadata()
    for pa, pb in zip(a, b):
        ca = [a.xref_stream_raw(x) for x in pa.get_contents()]
        cb = [b.xref_stream_raw(x) for x in pb.get_contents()]
        assert ca == cb
    assert pdfa.verify_pdfa(stripped, "2b") and any("XMP" in e for e in pdfa.verify_pdfa(stripped, "2b"))


def test_strip_without_xmp_returns_same():
    data = _problem_pdf()
    assert pdfa.strip_pdfa_identification(data) == data


@needs_gs
def test_convert_file_save_anyway(tmp_path, monkeypatch):
    src = tmp_path / "origine.pdf"
    src.write_bytes(_problem_pdf())
    # conformità forzatamente non raggiungibile
    monkeypatch.setattr(pdfa, "verify_pdfa", lambda data, level: ["errore simulato"])
    path, report = pdfa.convert_file_to_pdfa(src, tmp_path / "a.pdf", "2b")
    assert path is None and not report["compliant"] and not (tmp_path / "a.pdf").exists()
    path, report = pdfa.convert_file_to_pdfa(src, tmp_path / "b.pdf", "2b", save_anyway=True)
    assert path and os.path.exists(path)
    xmp = pymupdf.open(path).get_xml_metadata()
    assert "pdfaid" not in xmp
    assert sorted(os.listdir(tmp_path)) == ["b.pdf", "origine.pdf"]


@needs_gs
def test_convert_file_compliant(tmp_path):
    src = tmp_path / "origine.pdf"
    src.write_bytes(_problem_pdf())
    path, report = pdfa.convert_file_to_pdfa(src, tmp_path / "pdfa.pdf", "2b")
    assert report["compliant"] and path and pdfa.verify_pdfa(open(path, "rb").read(), "2b") == []


@needs_gs
def test_cancel_kills_gs_and_cleans(tmp_path, monkeypatch):
    class Cancel:
        def update(self, fraction=None, text=None):
            if fraction is not None and fraction >= 0.15:
                raise OperationCancelled()

    monkeypatch.setattr(pdfa.tempfile, "tempdir", str(tmp_path))
    src = tmp_path / "x.pdf"
    src.write_bytes(_problem_pdf())
    with pytest.raises(OperationCancelled):
        pdfa.convert_file_to_pdfa(src, tmp_path / "out.pdf", "2b", progress=Cancel())
    assert sorted(os.listdir(tmp_path)) == ["x.pdf"]


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript non installato")
def test_pdfx3_rgb_document(tmp_path):
    """PDF/X-3: OutputIntent GTS_PDFX con profilo CMYK, TrimBox su ogni pagina, GTS_PDFXVersion, testo conservato."""
    import re
    from foliant.modules.convert.engine import convert_pdf
    from foliant.modules.convert.pdfx import verify_pdfx
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page()
        p.insert_text((72, 100), f"Scheda tecnica {i + 1}", fontsize=16)
        p.draw_rect(pymupdf.Rect(72, 130, 300, 220), fill=(0.9, 0.1, 0.1))
    src = tmp_path / "rgb.pdf"
    doc.save(str(src))
    out = convert_pdf(str(src), tmp_path / "x.pdf", "pdfx-3")[0]
    data = open(out, "rb").read()
    assert verify_pdfx(data) == []
    res = pymupdf.open(out)
    for page in res:
        assert res.xref_get_key(page.xref, "TrimBox")[0] == "array"
        assert "Scheda tecnica" in page.get_text()
    oi = res.xref_get_key(res.pdf_catalog(), "OutputIntents")[1]
    ref = int(re.search(r"(\d+) 0 R", oi).group(1))
    assert res.xref_get_key(ref, "S")[1] == "/GTS_PDFX"
    profile = int(res.xref_get_key(ref, "DestOutputProfile")[1].split()[0])
    assert res.xref_get_key(profile, "N")[1] == "4"
    assert out.endswith("x.pdf")
