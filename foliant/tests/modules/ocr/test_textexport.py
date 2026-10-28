"""Esportazione del testo riconosciuto (OCR-07): TXT, ALTO, hOCR."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

import pymupdf

from foliant.modules.ocr import engine, textexport as tx
from foliant.modules.ocr.tess import Line, Word

from .conftest import FRASI, image_pdf, scan_image

ALTO = "{http://www.loc.gov/standards/alto/ns-v4#}"
XH = "{http://www.w3.org/1999/xhtml}"


def _line(text: str, y: float, block: int = 0, par: int = 0, conf: float = 90.0) -> Line:
    words, x = [], 10.0
    for t in text.split():
        w = 12.0 * len(t)
        words.append(Word(t, (x, y, x + w, y + 20), conf))
        x += w + 8
    bbox = (words[0].bbox[0], y, words[-1].bbox[2], y + 20) if words else (10, y, 10, y + 20)
    return Line(bbox=bbox, words=words, baseline=(0.0, -4.0), block=block, par=par)


def _page(index: int, texts, **kw) -> engine.PageResult:
    lines = []
    for i, item in enumerate(texts):
        if isinstance(item, tuple):
            text, block, par = item
        else:
            text, block, par = item, 0, 0
        lines.append(_line(text, 30.0 + 30 * i, block, par))
    return engine.PageResult(index=index, lines=lines, size=(1000, 1400), **kw)


def _result(*pages) -> engine.OcrResult:
    return engine.OcrResult(pages=list(pages))


# ===================================================================== TXT
def test_page_text_paragraphs_blank_line():
    res = _page(0, [("Prima riga", 0, 0), ("seconda riga", 0, 0), ("Nuovo paragrafo", 0, 1), ("Altro blocco", 1, 0)])
    assert tx.page_text(res) == "Prima riga\nseconda riga\n\nNuovo paragrafo\n\nAltro blocco"
    assert tx.page_text(res, paragraphs=True) == "Prima riga seconda riga\n\nNuovo paragrafo\n\nAltro blocco"


def test_dehyphenate():
    res = _page(0, ["Il pro-", "getto è pronto", "Socio-", "Economico", "anno 20-", "24 fine"])
    out = tx.page_text(res, dehyphenate=True)
    assert "progetto" in out and "pro-" not in out
    assert out.splitlines()[0] == "Il progetto"
    assert out.splitlines()[1] == "è pronto"
    assert "Socio-\nEconomico" in out
    assert "20-\n24" in out
    # senza opzione il trattino resta
    assert "pro-\ngetto" in tx.page_text(res)
    par = tx.page_text(res, paragraphs=True, dehyphenate=True)
    assert par == "Il progetto è pronto Socio-Economico anno 20- 24 fine"


def test_dehyphenate_soft_hyphen_and_not_sign_and_whole_line():
    res = _page(0, ["con­", "tratto e ri¬", "cevuta", "fine"])
    out = tx.page_text(res, dehyphenate=True)
    assert out == "contratto\ne ricevuta\nfine"


def test_dehyphenate_only_within_paragraph():
    res = _page(0, [("pro-", 0, 0), ("getto", 0, 1)])
    assert tx.page_text(res, dehyphenate=True) == "pro-\n\ngetto"


def test_skipped_and_failed_pages():
    ok = _page(0, ["Testo riconosciuto"])
    skipped = engine.PageResult(index=1, status="skipped", existing_text="Testo esistente\r\nseconda  \n\n")
    failed = engine.PageResult(index=2, status="failed", message="errore")
    assert tx.page_text(skipped) == "Testo esistente\nseconda"
    assert tx.page_text(failed) == ""
    out = tx.result_text(_result(ok, skipped, failed))
    assert out.count("— Pagina") == 3
    assert "Testo esistente" in out and "Testo riconosciuto" in out
    assert out.index("— Pagina 1 —") < out.index("Testo riconosciuto") < out.index("— Pagina 2 —") \
        < out.index("Testo esistente") < out.index("— Pagina 3 —")


def test_formfeed_separator_three_pages():
    r = _result(_page(0, ["Uno"]), _page(1, ["Due", "due bis"]), _page(2, ["Tre"]))
    out = tx.result_text(r, separator="formfeed")
    assert out.count("\f") == 2
    segs = out.split("\f")
    assert [s.strip() for s in segs] == ["Uno", "Due\ndue bis", "Tre"]
    assert "Pagina" not in out


def test_formfeed_existing_text_does_not_add_breaks():
    r = _result(_page(0, ["Uno"]), engine.PageResult(index=1, status="skipped", existing_text="a\fb"),
                engine.PageResult(index=2, status="failed"))
    assert tx.result_text(r, separator="formfeed").count("\f") == 2


def test_line_separator_before_each_page():
    r = _result(_page(0, ["Uno"]), _page(4, ["Cinque"]))
    out = tx.result_text(r)
    assert out.startswith("— Pagina 1 —\nUno")
    assert "— Pagina 5 —\nCinque" in out
    assert out.endswith("\n")


def test_crlf():
    r = _result(_page(0, ["Uno", "due"]), _page(1, ["Tre"]))
    out = tx.result_text(r, newline="\r\n")
    assert "\r\n" in out
    assert "\n" not in out.replace("\r\n", "")
    assert "Uno\r\ndue" in out


def test_nfc():
    decomposed = unicodedata.normalize("NFD", "perché città")
    r = _result(_page(0, [decomposed]))
    out = tx.result_text(r)
    assert "perché città" in out
    assert unicodedata.is_normalized("NFC", out)


def test_save_txt_utf8_no_bom(tmp_path):
    p = tmp_path / "out.txt"
    tx.save_txt(p, "città — prova\n")
    data = p.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8") == "città — prova\n"


# ===================================================================== ALTO
def _special_result():
    return _result(_page(0, [("A & B <c> «virgolette»", 0, 0), ("seconda riga", 0, 0), ("blocco due", 1, 0)]),
                   engine.PageResult(index=1, status="skipped", existing_text="già"),
                   _page(2, ["terza pagina"]))


def test_alto_structure_and_coordinates():
    r = _special_result()
    xml = tx.to_alto(r, source_name="scansione & co.pdf")
    root = ET.fromstring(xml.encode("utf-8"))
    assert root.tag == f"{ALTO}alto"
    assert root.find(f"{ALTO}Description/{ALTO}MeasurementUnit").text == "pixel"
    assert root.find(f"{ALTO}Description/{ALTO}sourceImageInformation/{ALTO}fileName").text == "scansione & co.pdf"
    assert root.find(f".//{ALTO}softwareName").text == "Foliant"
    pages = root.findall(f".//{ALTO}Page")
    assert [p.get("PHYSICAL_IMG_NR") for p in pages] == ["1", "3"]      # solo pagine riconosciute
    assert pages[0].get("WIDTH") == "1000" and pages[0].get("HEIGHT") == "1400"
    assert len(pages[0].findall(f".//{ALTO}TextBlock")) == 2
    strings = root.findall(f".//{ALTO}String")
    words = [w for p in r.recognized for w in p.words]
    assert len(strings) == len(words)
    for s, w in zip(strings, words):
        assert s.get("CONTENT") == w.text
        x0, y0 = float(s.get("HPOS")), float(s.get("VPOS"))
        assert (x0, y0, x0 + float(s.get("WIDTH")), y0 + float(s.get("HEIGHT"))) == w.bbox
        assert 0.0 <= float(s.get("WC")) <= 1.0
    assert [s.get("CONTENT") for s in strings[:4]] == ["A", "&", "B", "<c>"]
    # spazi tra le parole: n_parole - 1 per riga
    for tl in root.iter(f"{ALTO}TextLine"):
        assert len(tl.findall(f"{ALTO}SP")) == len(tl.findall(f"{ALTO}String")) - 1
    ids = [e.get("ID") for e in root.iter() if e.get("ID")]
    assert len(ids) == len(set(ids))
    assert "&amp;" in xml and "&lt;c&gt;" in xml and "«virgolette»" in xml


def test_alto_wc_clamped():
    res = _page(0, ["parola"])
    res.lines[0].words[0].conf = 150
    xml = tx.to_alto(_result(res))
    s = ET.fromstring(xml.encode()).find(f".//{ALTO}String")
    assert float(s.get("WC")) == 1.0


# ===================================================================== hOCR
def test_hocr_structure():
    r = _special_result()
    html = tx.to_hocr(r, source_name='a"b.pdf')
    assert html.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html')
    body = re.sub(r"<!DOCTYPE[^>]*>", "", html, count=1)
    root = ET.fromstring(body.encode("utf-8"))
    metas = {m.get("name"): m.get("content") for m in root.iter(f"{XH}meta") if m.get("name")}
    assert metas["ocr-system"].startswith("Foliant")
    assert "ocrx_word" in metas["ocr-capabilities"]
    pages = [d for d in root.iter(f"{XH}div") if d.get("class") == "ocr_page"]
    assert len(pages) == 2
    assert "bbox 0 0 1000 1400" in pages[0].get("title") and "ppageno 0" in pages[0].get("title")
    assert "ppageno 2" in pages[1].get("title")
    assert len([d for d in pages[0] if d.get("class") == "ocr_carea"]) == 2
    words = [s for s in root.iter(f"{XH}span") if s.get("class") == "ocrx_word"]
    expected = [w for p in r.recognized for w in p.words]
    assert [s.text for s in words] == [w.text for w in expected]
    for s, w in zip(words, expected):
        m = re.search(r"bbox (\d+) (\d+) (\d+) (\d+); x_wconf (\d+)", s.get("title"))
        assert tuple(float(v) for v in m.groups()[:4]) == w.bbox
        assert int(m.group(5)) == 90
    lines = [s for s in root.iter(f"{XH}span") if s.get("class") == "ocr_line"]
    assert all("baseline" in s.get("title") for s in lines)
    assert "&amp;" in html and "&lt;c&gt;" in html


def test_hocr_html_parser():
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.words = []
            self._in = False

        def handle_starttag(self, tag, attrs):
            self._in = dict(attrs).get("class") == "ocrx_word"

        def handle_data(self, data):
            if self._in:
                self.words.append(data)
                self._in = False

    p = P()
    p.feed(tx.to_hocr(_special_result()))
    assert p.words[:4] == ["A", "&", "B", "<c>"]


def test_save_alto_hocr_no_bom(tmp_path):
    r = _special_result()
    tx.save_alto(tmp_path / "a.xml", r, "x.pdf")
    tx.save_hocr(tmp_path / "a.hocr", r, "x.pdf")
    for name in ("a.xml", "a.hocr"):
        data = (tmp_path / name).read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf")
        assert "«virgolette»" in data.decode("utf-8")
    assert (tmp_path / "a.xml").read_text("utf-8") == tx.to_alto(r, "x.pdf")


# ===================================================================== end-to-end
def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def test_end_to_end(tmp_path):
    path = image_pdf(tmp_path / "a.pdf", [scan_image(lines=FRASI[:2], dpi=200)], dpi=200)
    with pymupdf.open(path) as pdf:
        result = engine.recognize_document(pdf, engine.OcrOptions(languages=["ita"], mode=engine.TEXT_ONLY,
                                                                  dpi=200, workers=1))
    text = tx.result_text(result, separator="formfeed")
    assert engine.char_accuracy(text, "\n".join(FRASI[:2])) >= 0.95
    root = ET.fromstring(tx.to_alto(result, "a.pdf").encode("utf-8"))
    strings = root.findall(f".//{ALTO}String")
    words = result.pages[0].words
    assert len(strings) == len(words) > 0
    for s, w in zip(strings, words):
        x0, y0 = float(s.get("HPOS")), float(s.get("VPOS"))
        box = (x0, y0, x0 + float(s.get("WIDTH")), y0 + float(s.get("HEIGHT")))
        assert _iou(box, w.bbox) >= 0.99
