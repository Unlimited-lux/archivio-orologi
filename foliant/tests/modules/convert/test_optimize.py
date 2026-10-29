"""Test di compressione, ottimizzazione, analisi dello spazio, linearizzazione e riparazione."""

from __future__ import annotations

import io
import os
import re

import pymupdf
import pytest
from PIL import Image

from foliant.core.platform import find_tool
from foliant.api import OperationCancelled
from foliant.modules.convert import optimize as opt
from foliant.modules.convert.common import ConversionError


# ------------------------------------------------------------------ fixture
def _photo(w: int, h: int, seed: int) -> bytes:
    """Immagine "fotografica" (rumore sfumato) codificata JPEG ad alta qualità."""
    base = Image.effect_noise((w // 4, h // 4), 40 + seed).convert("L")
    grad = Image.linear_gradient("L").resize(base.size)
    img = Image.merge("RGB", [base, base.transpose(Image.Transpose.FLIP_LEFT_RIGHT), grad])
    img = img.resize((w, h), Image.Resampling.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _png(w: int, h: int, color=(200, 30, 30)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    for x in range(0, w, 7):
        for y in range(0, h, 5):
            img.putpixel((x, y), (x % 255, y % 255, 90))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def photo_pdf() -> bytes:
    """4 pagine con testo, vettori, link, segnalibri e foto a 300 ppi (4×3 pollici)."""
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Orologio numero {i + 1} - scheda tecnica", fontsize=14)
        page.insert_text((72, 90), "Cassa in acciaio, movimento automatico.", fontsize=10)
        page.draw_rect(pymupdf.Rect(60, 60, 540, 400), color=(0, 0, 1), width=1)
        page.insert_image(pymupdf.Rect(72, 110, 72 + 288, 110 + 216), stream=_photo(1200, 900, i))
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 75),
                          "uri": "https://example.org"})
    doc.set_toc([[1, f"Pagina {i + 1}", i + 1] for i in range(4)])
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _text_ops(data: bytes) -> int:
    doc = pymupdf.open(stream=data, filetype="pdf")
    n = 0
    for page in doc:
        for c in page.get_contents():
            n += len(re.findall(rb"\bTj\b|\bTJ\b|\b'\b", doc.xref_stream(c)))
    doc.close()
    return n


def _texts(data: bytes, password=None) -> list[str]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    if password:
        doc.authenticate(password)
    t = [p.get_text() for p in doc]
    doc.close()
    return t


class _Cancel:
    def __init__(self, after: int):
        self.after, self.calls = after, 0

    def update(self, fraction=None, text=None):
        self.calls += 1
        if self.calls > self.after:
            raise OperationCancelled()


# ------------------------------------------------------------------ compressione
def test_levels_parameters():
    assert opt.LEVELS == ("lossless", "high", "medium", "low")
    m = opt.level_params("medium")
    assert (m.ppi, m.quality, m.mono_ppi, m.threshold) == (150, 70, 300, 225)
    assert opt.level_params("high").quality == 85 and opt.level_params("low").ppi == 96
    assert opt.level_params("lossless").ppi is None
    with pytest.raises(ConversionError):
        opt.level_params("ultra")


def test_medium_halves_photo_pdf_preserving_text(photo_pdf):
    out = opt.compress_pdf_bytes(photo_pdf, "medium")
    assert len(out) <= 0.5 * len(photo_pdf)
    assert _texts(out) == _texts(photo_pdf)
    assert _text_ops(out) == _text_ops(photo_pdf)
    doc = pymupdf.open(stream=out)
    assert len(doc.get_toc()) == 4
    assert doc[0].get_links()[0]["uri"] == "https://example.org"
    info = doc[0].get_image_info(xrefs=True)
    assert len(info) == 1
    ppi = info[0]["width"] / ((info[0]["bbox"][2] - info[0]["bbox"][0]) / 72)
    assert abs(ppi - 150) <= 1


def test_estimate_close_to_actual(photo_pdf):
    est = opt.estimate_sizes(photo_pdf)
    assert set(est) == set(opt.LEVELS)
    for level in ("lossless", "medium", "low"):
        actual = len(opt.compress_pdf_bytes(photo_pdf, level))
        assert abs(est[level] - actual) <= 0.2 * actual
    assert est["low"] < est["medium"] < est["high"] <= est["lossless"]


def test_estimate_by_sampling(photo_pdf):
    est = opt.estimate_sizes(photo_pdf, levels=("medium",), sample_pages=2)
    actual = len(opt.compress_pdf_bytes(photo_pdf, "medium"))
    assert abs(est["medium"] - actual) <= 0.2 * actual


def test_already_optimized_not_reduced(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Testo breve", fontsize=12)
    path = tmp_path / "piccolo.pdf"
    doc.save(str(path), garbage=4, deflate=True, use_objstms=1)
    doc.close()
    original = path.read_bytes()
    out_path = tmp_path / "piccolo_compresso.pdf"
    res = opt.compress_file(path, out_path, "low")
    assert res["reduced"] is False and res["output"] is None
    assert not out_path.exists()
    assert path.read_bytes() == original
    assert sorted(os.listdir(tmp_path)) == ["piccolo.pdf"]


def test_compress_file_writes_result(tmp_path, photo_pdf):
    src = tmp_path / "foto.pdf"
    src.write_bytes(photo_pdf)
    res = opt.compress_file(src, tmp_path / "foto_compresso.pdf", "low")
    assert res["reduced"] and res["after"] < res["before"] == len(photo_pdf)
    assert os.path.getsize(res["output"]) == res["after"]
    assert src.read_bytes() == photo_pdf


def test_encrypted_input_keeps_encryption():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Riservato", fontsize=12)
    page.insert_image(pymupdf.Rect(72, 100, 216, 208), stream=_photo(600, 450, 3))
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="padrone",
                       permissions=pymupdf.PDF_PERM_PRINT)
    with pytest.raises(Exception):
        opt.compress_pdf_bytes(data, "medium")
    out = opt.compress_pdf_bytes(data, "medium", password="segreta")
    res = pymupdf.open(stream=out)
    assert res.needs_pass
    assert res.authenticate("segreta")
    assert "AES" in res.metadata["encryption"] and "256" in res.metadata["encryption"]
    assert res[0].get_text().strip() == "Riservato"
    assert not res.permissions & pymupdf.PDF_PERM_COPY


def test_smask_transparency_preserved():
    img = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    noise = Image.effect_noise((900, 900), 80).convert("RGB")
    alpha = Image.new("L", (900, 900), 0)
    alpha.paste(255, (200, 200, 700, 700))
    img = Image.merge("RGBA", (*noise.split(), alpha))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(72, 72, 216, 216), stream=buf.getvalue())   # 450 ppi
    data = doc.tobytes()
    out = opt.compress_pdf_bytes(data, "medium")
    assert len(out) < len(data)
    res = pymupdf.open(stream=out)
    infos = res[0].get_image_info(xrefs=True)
    assert infos[0]["has-mask"]
    pix = res[0].get_pixmap(dpi=50, clip=pymupdf.Rect(72, 72, 90, 90))
    assert pix.pixel(2, 2)[:3] == (255, 255, 255)   # angolo trasparente: resta bianco


# ------------------------------------------------------------------ CNV-30
def test_dedup_same_logo_50_times():
    logo = _png(120, 60)
    big = pymupdf.open()
    for i in range(50):
        one = pymupdf.open()
        one.new_page(width=300, height=200).insert_image(pymupdf.Rect(10, 10, 130, 70), stream=logo)
        big.insert_pdf(one)
        one.close()
    data = big.tobytes()
    big.close()
    src = pymupdf.open(stream=data)
    assert len({x for p in src for x, *_ in p.get_images()}) == 50
    out = opt.optimize_advanced(data, dedup=True)
    res = pymupdf.open(stream=out)
    assert len({x for p in res for x, *_ in p.get_images()}) == 1
    for pno in (0, 25, 49):
        assert res[pno].get_pixmap(dpi=72).samples == src[pno].get_pixmap(dpi=72).samples


def test_unused_object_removed():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Ciao", fontsize=12)
    x = doc.get_new_xref()
    doc.update_object(x, "<< /Foliant (OGGETTO_ORFANO) >>")
    data = doc.tobytes(garbage=0)
    assert b"OGGETTO_ORFANO" in data

    def has_marker(blob):
        d = pymupdf.open(stream=blob)
        return any("OGGETTO_ORFANO" in d.xref_object(i) for i in range(1, d.xref_length()))

    assert not has_marker(opt.optimize_advanced(data, remove_unused=True))
    assert has_marker(opt.optimize_advanced(data, remove_unused=False, dedup=False))


def test_resample_600_to_150_ppi():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(72, 72, 72 + 144, 72 + 144), stream=_photo(1200, 1200, 1))   # 600 ppi
    data = doc.tobytes()
    out = opt.optimize_advanced(data, image_ppi=150, image_threshold_ppi=225, jpeg_quality=80)
    info = pymupdf.open(stream=out)[0].get_image_info()[0]
    assert abs(info["width"] / 2 - 150) <= 1 and abs(info["height"] / 2 - 150) <= 1


def test_image_below_threshold_untouched():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(72, 72, 72 + 288, 72 + 288), stream=_photo(800, 800, 2))   # 200 ppi
    data = doc.tobytes()
    out = opt.optimize_advanced(data, image_ppi=150, image_threshold_ppi=225)
    info = pymupdf.open(stream=out)[0].get_image_info()[0]
    assert info["width"] == 800


def test_advanced_removals():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Contenuto", fontsize=12)
    page.add_text_annot((100, 100), "nota")
    doc.set_toc([[1, "Inizio", 1]])
    doc.set_metadata({"title": "Titolo", "author": "Autore"})
    doc.embfile_add("dati.txt", b"allegato")
    js = doc.get_new_xref()
    doc.update_object(js, "<< /S /JavaScript /JS (app.alert\\(1\\)) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    data = doc.tobytes()
    out = opt.optimize_advanced(data, remove_metadata=True, remove_javascript=True, remove_attachments=True,
                                remove_annotations=True, remove_bookmarks=True, remove_structure=True,
                                remove_thumbnails=True)
    res = pymupdf.open(stream=out)
    assert res.get_toc() == [] and res.embfile_count() == 0
    assert not list(res[0].annots())
    assert not res.metadata.get("title")
    assert b"JavaScript" not in pymupdf.open(stream=out).tobytes(expand=255)
    assert res[0].get_text().strip() == "Contenuto"


# ------------------------------------------------------------------ analisi spazio
def test_analyze_space_sums_to_file_size(photo_pdf):
    rows = opt.analyze_space(photo_pdf)
    labels = [r[0] for r in rows]
    assert labels == list(opt.SPACE_CATEGORIES)
    total = sum(v for _l, v in rows)
    assert abs(total - len(photo_pdf)) <= 0.01 * len(photo_pdf)
    d = dict(rows)
    assert d["Immagini"] > 0.9 * len(photo_pdf)
    assert all(v >= 0 for v in d.values())


def test_analyze_space_objstm_and_categories(sample_pdf):
    doc = pymupdf.open(sample_pdf)
    doc.set_toc([[1, "Capitolo", 1]])
    doc.embfile_add("a.bin", os.urandom(5000))
    data = doc.tobytes(garbage=3, use_objstms=1, deflate=True)
    d = dict(opt.analyze_space(data))
    assert sum(d.values()) == len(data)
    assert d["Allegati"] >= 4500 and d["Segnalibri"] > 0 and d["Contenuti di pagina"] > 0


# ------------------------------------------------------------------ linearizzazione
@pytest.mark.skipif(find_tool("qpdf") is None, reason="qpdf non installato")
def test_linearize(twenty_pages):
    data = open(twenty_pages, "rb").read()
    assert not opt.is_linearized(data)
    lin = opt.linearize_bytes(data)
    assert opt.is_linearized(lin)
    assert re.search(rb"/L\s+%d\b" % len(lin), lin[:1024])
    assert _texts(lin) == _texts(data)
    # dati aggiunti in coda (come un salvataggio incrementale): la linearizzazione decade
    assert not opt.is_linearized(lin + b"\n% aggiornamento\n")


@pytest.mark.skipif(find_tool("qpdf") is None, reason="qpdf non installato")
def test_compress_with_linearize(photo_pdf):
    out = opt.optimize_advanced(photo_pdf, image_ppi=150, linearize=True)
    assert opt.is_linearized(out)


# ------------------------------------------------------------------ annullamento
def test_cancel_leaves_no_files(tmp_path, photo_pdf):
    src = tmp_path / "foto.pdf"
    src.write_bytes(photo_pdf)
    with pytest.raises(OperationCancelled):
        opt.compress_file(src, tmp_path / "out.pdf", "medium", progress=_Cancel(3))
    assert sorted(os.listdir(tmp_path)) == ["foto.pdf"]


# ------------------------------------------------------------------ riparazione
def _ten_pages() -> bytes:
    doc = pymupdf.open()
    for i in range(10):
        page = doc.new_page()
        page.insert_text((72, 72), f"Pagina {i + 1}", fontsize=20)
        page.insert_image(pymupdf.Rect(72, 100, 272, 250), stream=_png(200, 150, (i * 20, 100, 50)))
    data = doc.tobytes(garbage=1)
    doc.close()
    return data


def test_detect_file_type():
    assert opt.detect_file_type(_ten_pages()) == "pdf"
    assert opt.detect_file_type(_photo(40, 40, 0)) == "jpeg"
    assert opt.detect_file_type(_png(10, 10)) == "png"
    assert opt.detect_file_type(b"PK\x03\x04....word/document.xml") == "zip/docx"
    assert opt.detect_file_type(b"") is None


def test_repair_corrupt_xref_offsets(tmp_path):
    data = _ten_pages()
    pos = data.rfind(b"\nxref")
    assert pos > 0
    head, tail = data[:pos], data[pos:]
    tail = re.sub(rb"(\d{10}) (\d{5}) n", lambda m: b"%010d %s n" % (int(m.group(1)) + 7, m.group(2)), tail)
    broken = head + tail
    src = tmp_path / "rotto.pdf"
    src.write_bytes(broken)
    path, report = opt.repair_file(src)
    assert path.endswith("rotto_riparato.pdf")
    assert report["repaired"] and report["pages"] == 10 and report["lost_pages"] == []
    fixed = open(path, "rb").read()
    doc = pymupdf.open(stream=fixed)
    assert not doc.is_repaired and doc.page_count == 10
    orig = pymupdf.open(stream=data)
    for pno in (0, 9):
        assert doc[pno].get_pixmap(dpi=50).samples == orig[pno].get_pixmap(dpi=50).samples
    assert src.read_bytes() == broken


def test_repair_truncated_at_60_percent():
    data = _ten_pages()
    cut = data[: int(len(data) * 0.6)]
    out, report = opt.repair_pdf_bytes(cut)
    assert report["repaired"]
    assert 1 <= report["pages"] < 10
    assert report["pages"] + len(report["lost_pages"]) == 10
    assert all(1 <= p <= 10 for p in report["lost_pages"])
    doc = pymupdf.open(stream=out)
    assert doc.page_count == report["pages"] and not doc.is_repaired
    recovered = [p.get_text().strip() for p in doc]
    expected = [f"Pagina {n}" for n in range(1, 11) if n not in report["lost_pages"]]
    assert recovered == expected


def test_repair_jpeg_renamed(tmp_path):
    fake = tmp_path / "foto.pdf"
    fake.write_bytes(_photo(64, 64, 1))
    with pytest.raises(ConversionError) as exc:
        opt.repair_file(fake)
    assert exc.value.detected_type == "jpeg"
    assert "JPEG" in str(exc.value) and "PDF" in str(exc.value)
    assert sorted(os.listdir(tmp_path)) == ["foto.pdf"]


@pytest.mark.parametrize("blob", [b"", b"%PDF-1.7\n", b"%PDF-1.4\ngarbage\x00\xff" * 50, os.urandom(3000)])
def test_repair_garbage_no_crash(blob):
    try:
        opt.repair_pdf_bytes(blob)
    except ConversionError:
        pass


def test_previous_revisions(tmp_path):
    path = tmp_path / "revisioni.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Rev 1", fontsize=12)
    doc.save(str(path))
    doc.close()
    sizes = [path.stat().st_size]
    for text in ("Rev 2", "Rev 3"):
        doc = pymupdf.open(str(path))
        doc.new_page().insert_text((72, 72), text, fontsize=12)
        doc.saveIncr()
        doc.close()
        sizes.append(path.stat().st_size)
    data = path.read_bytes()
    truncated = data[: sizes[1] + (sizes[2] - sizes[1]) // 2]
    revs = opt.previous_revisions(truncated)
    assert len(revs) == 2
    second = pymupdf.open(stream=revs[-1])
    assert [p.get_text().strip() for p in second] == ["Rev 1", "Rev 2"]
