"""Operazioni aggiuntive in blocco (BAT-23)."""

from __future__ import annotations

import os

import pymupdf

from foliant.modules.tools import engine as be

from .conftest import build_pdf


def _items(paths):
    return [be.analyze_file(str(p)) for p in paths]


def _run(tmp_path, paths, action, params=None, out="out", **kw):
    settings = be.JobSettings(steps=[be.Step(action, params or {})], destination=str(tmp_path / out), **kw)
    errors, _w = be.validate_settings(_items(paths), settings)
    assert not errors, errors
    return be.run_job(None, _items(paths), settings)


def _files(tmp_path, n=3, pages=2, text="Pagina di prova"):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    return [build_pdf(src / f"f{i}.pdf", [[f"{text} {i}", f"riga {k}"] for k in range(pages)]) for i in range(n)]


def test_metadata_in_info_and_xmp(tmp_path):
    paths = _files(tmp_path)
    report = _run(tmp_path, paths, "metadata", {"author": "Ufficio legale", "title": "Pratica {nome}"})
    assert report.counts()["ok"] == 3
    for name in os.listdir(tmp_path / "out"):
        doc = pymupdf.open(str(tmp_path / "out" / name))
        assert doc.metadata["author"] == "Ufficio legale"
        assert doc.metadata["title"] == "Pratica " + name[:-4]
        xmp = doc.get_xml_metadata()
        assert "Ufficio legale" in xmp and "dc:creator" in xmp


def test_redact_codice_fiscale(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    paths = []
    for i in range(3):
        paths.append(build_pdf(src / f"cf{i}.pdf", [[f"Cliente: RSSMRA80A01H501{'UVW'[i]}", "Contratto di vendita"],
                                                    ["Garante BNCLGU75C12F205Z firmatario"]]))
    report = _run(tmp_path, paths, "redact", {"pattern": "codice_fiscale"})
    assert report.counts()["ok"] == 3
    for r in report.results:
        assert r.message == "2 redazioni"
        text = "".join(p.get_text() for p in pymupdf.open(r.outputs[0]))
        assert "RSSMRA" not in text and "BNCLGU" not in text
        assert "Contratto di vendita" in text


def test_redact_no_match_skipped(tmp_path):
    report = _run(tmp_path, _files(tmp_path, 1), "redact", {"pattern": "text", "text": "inesistente"})
    assert report.results[0].status == "skipped" and "nessuna corrispondenza" in report.results[0].message


def test_delete_all_pages_is_error_others_continue(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    three = build_pdf(src / "tre.pdf", [["a"], ["b"], ["c"]])
    five = build_pdf(src / "cinque.pdf", [["a"], ["b"], ["c"], ["d"], ["e"]])
    report = _run(tmp_path, [three, five], "delete_pages", {"pages": "1-3"})
    by = {os.path.basename(r.input): r for r in report.results}
    assert by["tre.pdf"].status == "error" and by["tre.pdf"].code == "PAG-01"
    assert by["cinque.pdf"].status == "ok"
    assert pymupdf.open(by["cinque.pdf"].outputs[0]).page_count == 2


def test_delete_blank_pages(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 90), "uno")
    doc.new_page()
    doc.new_page().insert_text((72, 90), "tre")
    doc.save(str(src / "b.pdf"))
    report = _run(tmp_path, [src / "b.pdf"], "delete_pages", {"mode": "blank"})
    assert pymupdf.open(report.results[0].outputs[0]).page_count == 2


def test_split_every_two_pages(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = build_pdf(src / "atto.pdf", [[f"p{i}"] for i in range(5)])
    report = _run(tmp_path, [path], "split", {"every_n": 2})
    outs = report.results[0].outputs
    assert [os.path.basename(o) for o in outs] == ["atto_001.pdf", "atto_002.pdf", "atto_003.pdf"]
    assert [pymupdf.open(o).page_count for o in outs] == [2, 2, 1]


def test_split_by_bookmarks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    path = build_pdf(src / "s.pdf", [[f"p{i}"] for i in range(4)])
    doc = pymupdf.open(path)
    doc.set_toc([[1, "Uno", 1], [1, "Due", 3]])
    doc.saveIncr()
    doc.close()
    outs = _run(tmp_path, [path], "split", {"mode": "bookmarks"}).results[0].outputs
    assert [pymupdf.open(o).page_count for o in outs] == [2, 2]


def test_split_must_be_last_step(tmp_path):
    items = _items(_files(tmp_path, 1))
    settings = be.JobSettings(steps=[be.Step("split"), be.Step("compress")], destination=str(tmp_path / "o"))
    errors, _w = be.validate_settings(items, settings)
    assert any("ultimo passo" in e for e in errors)
    assert be.validate_sequence(settings.steps)


def test_rotate_and_extract(tmp_path):
    paths = _files(tmp_path, 1, pages=3)
    out = _run(tmp_path, paths, "rotate", {"degrees": "90", "pages": "2"}).results[0].outputs[0]
    assert [p.rotation for p in pymupdf.open(out)] == [0, 90, 0]
    out = _run(tmp_path, paths, "extract_pages", {"pages": "2-3"}, out="ex").results[0].outputs[0]
    doc = pymupdf.open(out)
    assert doc.page_count == 2 and "riga 1" in doc[0].get_text()


def test_flatten_annotations(tmp_path):
    paths = _files(tmp_path, 1, pages=1)
    doc = pymupdf.open(paths[0])
    doc[0].add_text_annot((100, 200), "Nota da appiattire")
    doc.saveIncr()
    doc.close()
    out = _run(tmp_path, paths, "flatten").results[0].outputs[0]
    assert not list(pymupdf.open(out)[0].annots())


def test_sanitize_metadata(tmp_path):
    paths = _files(tmp_path, 1, pages=1)
    doc = pymupdf.open(paths[0])
    doc.set_metadata({"author": "Segreto"})
    doc.saveIncr()
    doc.close()
    out = _run(tmp_path, paths, "sanitize").results[0].outputs[0]
    assert not pymupdf.open(out).metadata.get("author")


def test_initial_view(tmp_path):
    out = _run(tmp_path, _files(tmp_path, 1), "initial_view", {"layout": "TwoPageLeft", "mode": "UseOutlines"}
               ).results[0].outputs[0]
    doc = pymupdf.open(out)
    assert doc.pagelayout == "TwoPageLeft" and doc.pagemode == "UseOutlines"


def test_linearize(tmp_path):
    r = _run(tmp_path, _files(tmp_path, 1), "linearize").results[0]
    assert r.status in ("ok", "warning") and pymupdf.open(r.outputs[0]).page_count == 2


def test_export_images(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pix.set_rect(pix.irect, (200, 0, 0))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 150, 125), stream=pix.tobytes("png"))
    pix2 = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix2.set_rect(pix2.irect, (0, 0, 200))
    page.insert_image(pymupdf.Rect(200, 50, 260, 110), stream=pix2.tobytes("png"))
    doc.save(str(src / "foto.pdf"))
    report = _run(tmp_path, [src / "foto.pdf"], "export_images")
    outs = report.results[0].outputs
    assert [os.path.basename(o) for o in outs] == ["foto_001.png", "foto_002.png"]
    assert pymupdf.Pixmap(outs[0]).width == 40
    none = _run(tmp_path, _files(tmp_path, 1), "export_images", out="vuoto").results[0]
    assert none.status == "skipped"


def _form(path, value=""):
    doc = pymupdf.open()
    page = doc.new_page()
    for k, name in enumerate(("cliente", "importo")):
        w = pymupdf.Widget()
        w.field_name = name
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.rect = pymupdf.Rect(72, 100 + 40 * k, 300, 120 + 40 * k)
        w.field_value = value
        page.add_widget(w)
    doc.save(str(path))
    return str(path)


def test_form_data_export_and_import(tmp_path):
    """BAT-23: esporta e importa in blocco i dati dei moduli (ricevute, schede di acquisto)."""
    src = tmp_path / "src"
    src.mkdir()
    a = _form(src / "scheda1.pdf")
    b = _form(src / "scheda2.pdf")
    data = tmp_path / "dati.csv"
    data.write_text("File,cliente,importo\nscheda1.pdf,Rossi,8500\nscheda2.pdf,Bianchi,12000\n", encoding="utf-8")
    items = [be.analyze_file(p) for p in (a, b)]
    rep = be.run_job(None, items, be.JobSettings(steps=[be.Step("form_import", {"file": str(data)})],
                                                 destination=str(tmp_path / "compilati")))
    assert all(r.status == "ok" and "2 campi" in r.message for r in rep.results)
    filled = [be.analyze_file(r.outputs[0]) for r in rep.results]
    rep = be.run_job(None, filled, be.JobSettings(steps=[be.Step("form_export", {"format": "json"})],
                                                  destination=str(tmp_path / "dati")))
    import json
    out = {os.path.basename(r.outputs[0]): json.loads(open(r.outputs[0], encoding="utf-8").read())
           for r in rep.results}
    assert any(v.get("cliente") == "Rossi" for v in out.values())
    assert any(str(v.get("importo")) == "12000" for v in out.values())
    assert all(name.endswith(".json") for name in out)
    # file senza campi: saltato
    plain = build_pdf(src / "lettera.pdf", [["testo"]])
    rep = be.run_job(None, [be.analyze_file(plain)], be.JobSettings(steps=[be.Step("form_export")],
                                                                   destination=str(tmp_path / "x")))
    assert rep.results[0].status == "skipped"
