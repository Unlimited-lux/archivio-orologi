"""Dati del modulo: FDF, XFDF, CSV, JSON; reimpostazione, appiattimento, moduli XML."""

from __future__ import annotations

import csv
import os
import io
import json
import xml.etree.ElementTree as ET

import pymupdf
import pytest

from foliant.modules import forms
from foliant.modules.forms import engine as E
from foliant.modules.forms import interchange as X

from .conftest import build_form, reopen
from .test_fill import open_form, values

FILLED = {"nome": "Mario", "cognome": 'Rossi, "Mario"', "privacy": "Yes", "scelta": "B", "paese": "FR",
          "colori": ["rosso", "blu"]}


def module():
    return forms.instance()


# ---------------------------------------------------------------- formati puri
@pytest.mark.parametrize("fmt", ["fdf", "xfdf", "json", "xml"])
def test_roundtrip_formats(fmt):
    vals = {"a": "x", "b.c": "Àèì ε 日本", "b.d": ["1", "2"], "chk": "Off", "e": "riga1\rriga2"}
    data = X.export_data(fmt, vals, "m.pdf", {"chk"})
    assert X.parse_data(fmt, data) == vals


def test_csv_rfc4180_and_rows():
    data = X.export_csv([{"a": 'Rossi, "Mario"', "b": "1"}, {"a": "x", "b": "2"}])
    rows = list(csv.reader(io.StringIO(data.decode())))
    assert rows[1][0] == 'Rossi, "Mario"'
    assert X.parse_csv(data, 1) == {"a": "x", "b": "2"}
    semi = "nome;eta\nAnna;30\nLuca;40\n".encode()
    assert X.parse_csv(semi, 1) == {"nome": "Luca", "eta": "40"}


def test_xfdf_structure():
    data = X.export_xfdf({"cliente.nome": "Anna", "x": "1"}, "m.pdf")
    root = ET.fromstring(data)
    assert root.tag == "{http://ns.adobe.com/xfdf/}xfdf"
    names = [f.get("name") for f in root.iter("{http://ns.adobe.com/xfdf/}field")]
    assert names == ["cliente", "nome", "x"]


def test_fdf_uses_names_for_buttons():
    data = X.export_fdf({"chk": "Yes", "t": "Yes"}, "", {"chk"})
    assert b"/V/Yes" in data.replace(b" ", b"") and b"(Yes)" in data


def test_bad_files():
    with pytest.raises(X.InterchangeError):
        X.parse_fdf(b"not fdf")
    with pytest.raises(X.InterchangeError):
        X.parse_xfdf(b"<xfdf><fields>")
    with pytest.raises(X.InterchangeError):
        X.parse_json(b"[1,2]")


# ---------------------------------------------------------------- esporta/importa nella finestra
@pytest.mark.parametrize("fmt", ["fdf", "xfdf", "csv", "json", "xml", "txt"])
def test_export_import_into_blank_copy(window, tmp_path, form_pdf, fmt):
    view = open_form(window, form_pdf)
    module().import_values(FILLED, view)
    out = tmp_path / f"dati.{fmt}"
    module().export_data(str(out), view=view)
    blank = tmp_path / "vuoto.pdf"
    build_form(blank).close()
    view2 = open_form(window, blank)
    report = module().import_data(str(out), row=0, view=view2)
    assert values(view2) == FILLED
    assert not report["unknown"]


def test_export_skips_password_and_noexport(window, tmp_path):
    pdf = build_form()
    fs = {f.name: f for f in E.list_fields(pdf)}
    E.update_properties(pdf, 0, fs["cognome"].xref, password=True)
    E.update_properties(pdf, 0, fs["paese"].xref, no_export=True)
    pdf.save(str(tmp_path / "m.pdf"))
    view = open_form(window, tmp_path / "m.pdf")
    out = tmp_path / "d.xfdf"
    module().export_data(str(out), view=view)
    names = {f.get("name") for f in ET.fromstring(out.read_bytes()).iter("{http://ns.adobe.com/xfdf/}field")}
    assert names == {"nome", "privacy", "scelta", "colori"}


def test_json_list_is_array(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    module().import_values({"colori": ["rosso", "verde"]}, view)
    out = tmp_path / "d.json"
    module().export_data(str(out), view=view)
    assert json.loads(out.read_text())["colori"] == ["rosso", "verde"]


def test_import_report_unknown_rejected_and_single_undo(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    data = X.export_xfdf({"nome": "Anna", "inesistente": "x", "paese": "Z", "cognome": "B"})
    p = tmp_path / "d.xfdf"
    p.write_bytes(data)
    report = module().import_data(str(p), view=view)
    assert report["unknown"] == ["inesistente"]
    assert [n for n, _w in report["rejected"]] == ["paese"]
    assert values(view)["nome"] == "Anna" and values(view)["paese"] == ""
    view.document.undo()
    assert values(view)["nome"] == "" and values(view)["cognome"] == ""


def test_import_csv_second_row(window, tmp_path, form_pdf, monkeypatch):
    view = open_form(window, form_pdf)
    p = tmp_path / "d.csv"
    p.write_text("nome,cognome\nA,1\nB,2\nC,3\n")
    monkeypatch.setattr(module(), "choose_csv_row", lambda records: 1)
    module().import_data(str(p), view=view)
    assert values(view)["nome"] == "B"


def test_fdf_flag_instructions_ignored(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    p = tmp_path / "d.fdf"
    p.write_bytes(b"%FDF-1.2\n1 0 obj\n<</FDF<</Fields[<</T(nome)/V(Ugo)/SetFf 1>>]>>>>\nendobj\n"
                  b"trailer\n<</Root 1 0 R>>\n%%EOF\n")
    report = module().import_data(str(p), view=view)
    assert values(view)["nome"] == "Ugo"
    assert report.get("ignored")
    assert not [f for f in E.list_fields(view.document.pdf) if f.name == "nome"][0].readonly


# ---------------------------------------------------------------- reimposta e appiattisci
def test_reset_all_and_undo(window, tmp_path):
    pdf = build_form()
    paese = [f for f in E.list_fields(pdf) if f.name == "paese"][0]
    E.update_properties(pdf, 0, paese.xref, default="IT")
    pdf.save(str(tmp_path / "m.pdf"))
    view = open_form(window, tmp_path / "m.pdf")
    module().import_values(FILLED, view)
    module().reset(None, view)
    v = values(view)
    assert v["paese"] == "IT" and v["nome"] == "" and v["privacy"] == "Off" and v["colori"] == []
    view.document.undo()
    assert values(view) == FILLED


def test_reset_dialog_selected(window, form_pdf, monkeypatch):
    from foliant.modules.forms import dialogs
    view = open_form(window, form_pdf)
    module().import_values(FILLED, view)
    window.ctx.set_mode("moduli")
    fs = [f for f in E.list_fields(view.document.pdf) if f.name in ("nome", "cognome")]
    module().set_selection([(f.page, f.xref) for f in fs])
    module().reset_dialog()  # nessuna conferma (UX-13): selezione → solo i campi selezionati
    v = values(view)
    assert v["nome"] == "" and v["cognome"] == "" and v["paese"] == "FR"


def test_flatten_pages_scope(window, tmp_path):
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page()
    for p in range(3):
        x = E.create_field(pdf, p, E.TEXT, pymupdf.Rect(50, 50, 250, 70), f"c{p}")
        E.set_value(pdf, p, x, f"VAL{p}")
    h = E.create_field(pdf, 1, E.TEXT, pymupdf.Rect(50, 100, 250, 120), "nascosto", visibility="hidden")
    E.set_value(pdf, 1, h, "SEGRETO")
    pdf.save(str(tmp_path / "f.pdf"))
    view = open_form(window, tmp_path / "f.pdf")
    module().flatten([1, 2], view)
    out = tmp_path / "f2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert [f.name for f in E.list_fields(again)] == ["c0"]
    assert "VAL1" in again[1].get_text() and "VAL2" in again[2].get_text()
    assert "SEGRETO" not in again[1].get_text()


def test_flatten_all_renders_same(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    module().import_values(FILLED, view)
    with view.document.lock:
        before = view.document.pdf[0].get_pixmap(dpi=50).samples
    module().flatten(None, view)
    out = tmp_path / "flat.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert E.list_fields(again) == []
    assert again.xref_get_key(again.pdf_catalog(), "AcroForm")[0] == "null"
    after = again[0].get_pixmap(dpi=50).samples
    diff = sum(1 for a, b in zip(before, after) if abs(a - b) > 40)
    assert diff / len(before) < 0.01


# ---------------------------------------------------------------- moduli XML
DATASETS = (b'<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data>'
            b'<form1><nome>X</nome><citta>Roma</citta></form1></xfa:data></xfa:datasets>')
TEMPLATE = b'<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/"><subform name="form1"/></template>'


def make_xfa(path, with_fields: bool):
    pdf = pymupdf.open()
    pdf.new_page()
    if with_fields:
        E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "form1.nome")
    else:
        pdf[0].insert_text((50, 50), "Attendere: modulo dinamico")
        E._ensure_acroform(pdf)
    xs = []
    for body in (TEMPLATE, DATASETS):
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, body)
        xs.append(x)
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/XFA", f"[(template) {xs[0]} 0 R (datasets) {xs[1]} 0 R]")
    pdf.save(str(path))
    return xs


def test_xfa_detection(window, tmp_path, sample_pdf):
    make_xfa(tmp_path / "s.pdf", True)
    make_xfa(tmp_path / "d.pdf", False)
    vs = open_form(window, tmp_path / "s.pdf")
    vd = open_form(window, tmp_path / "d.pdf")
    vn = open_form(window, sample_pdf)
    fs, fd, fn = (forms.instance().filler(v) for v in (vs, vd, vn))
    assert fs.state.xfa == "hybrid" and "XML statico" in fs.text_of_bar()
    assert fd.state.xfa == "dynamic" and "dinamico" in fd.text_of_bar()
    assert not fn.bar_visible()


def test_xfa_static_sync_on_fill(window, tmp_path):
    xs = make_xfa(tmp_path / "s.pdf", True)
    view = open_form(window, tmp_path / "s.pdf")
    module().import_values({"form1.nome": "Anna"}, view)
    out = tmp_path / "s2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert E.xfa_data(again)["form1.nome"] == "Anna"
    assert E.xfa_data(again)["form1.citta"] == "Roma"
    del xs


def test_xfa_dynamic_saved_unchanged(window, tmp_path):
    make_xfa(tmp_path / "d.pdf", False)
    before = E._datasets(pymupdf.open(str(tmp_path / "d.pdf")))
    view = open_form(window, tmp_path / "d.pdf")
    out = tmp_path / "d2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    packets = dict((n, again.xref_stream(x)) for n, x in E._xfa_packets(again))
    assert packets["datasets"] == DATASETS and packets["template"] == TEMPLATE
    del before


def test_convert_hybrid_creates_new_document(window, tmp_path):
    import hashlib
    make_xfa(tmp_path / "s.pdf", True)
    before = hashlib.sha256((tmp_path / "s.pdf").read_bytes()).hexdigest()
    view = open_form(window, tmp_path / "s.pdf")
    module().import_values({"form1.nome": "Anna"}, view)
    module().convert_xfa()
    new = window.ctx.current_view()
    assert new is not view
    with new.document.lock:
        assert E.xfa_status(new.document.pdf) == "none"
        assert E.form_values(new.document.pdf) == {"form1.nome": "Anna"}
    with view.document.lock:
        assert E.xfa_status(view.document.pdf) == "hybrid"  # l'originale non cambia
    assert hashlib.sha256((tmp_path / "s.pdf").read_bytes()).hexdigest() == before


# ---------------------------------------------------------------- riconoscimento
def test_detect_and_create_subset_single_undo(window, tmp_path, monkeypatch):
    pdf = pymupdf.open()
    pg = pdf.new_page()
    for i, label in enumerate(("Nome:", "Cognome:", "Città:", "Telefono:")):
        y = 100 + 40 * i
        pg.insert_text((50, y), label, fontsize=11)
        pg.draw_line((120, y + 2), (350, y + 2))
    pdf.save(str(tmp_path / "flat.pdf"))
    view = open_form(window, tmp_path / "flat.pdf")
    from foliant.modules.forms import dialogs
    captured = {}

    def review(parent, cands):
        captured["n"] = len(cands)
        return cands[:1]

    monkeypatch.setattr(dialogs, "review_candidates", review)
    module().detect_fields()
    assert captured["n"] == 4
    fs = E.list_fields(view.document.pdf)
    assert [f.name for f in fs] == ["Nome"]
    view.document.undo()
    assert E.list_fields(view.document.pdf) == []


def test_xfa_data_wins_on_open_and_iso_dates(window, tmp_path):
    from foliant.modules.forms import formats as fm
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "form1.nome")
    E.set_value(pdf, 0, x, "Y")
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 250, 110), "form1.data", fmt=fm.Format(fm.DATE, pattern="dd/mm/yyyy"))
    xs = []
    ds = (b'<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data>'
          b'<form1><nome>X</nome><data></data></form1></xfa:data></xfa:datasets>')
    for body in (TEMPLATE, ds):
        n = pdf.get_new_xref()
        pdf.update_object(n, "<<>>")
        pdf.update_stream(n, body)
        xs.append(n)
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/XFA", f"[(template) {xs[0]} 0 R (datasets) {xs[1]} 0 R]")
    pdf.save(str(tmp_path / "x.pdf"))
    view = open_form(window, tmp_path / "x.pdf")
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    assert values(view)["form1.nome"] == "X"
    module().import_values({"form1.data": "05/03/2026"}, view)
    with view.document.lock:
        assert E.xfa_data(view.document.pdf)["form1.data"] == "2026-03-05"


def test_xfa_export_import_data(window, tmp_path):
    make_xfa(tmp_path / "d.pdf", False)
    view = open_form(window, tmp_path / "d.pdf")
    with view.document.lock:
        data = E.export_xfa_data(view.document.pdf)
    root = ET.fromstring(data)
    assert root.tag.endswith("}data") and root.find("form1/nome").text == "X"
    new = data.replace(b">X<", b">Nuovo<")
    p = tmp_path / "dati.xml"
    p.write_bytes(new)
    module().import_xfa(str(p), view)
    out = tmp_path / "d2.pdf"
    view.document.save(out)
    again = pymupdf.open(str(out))
    assert E.xfa_data(again)["form1.nome"] == "Nuovo"
    packets = dict((n, again.xref_stream(x)) for n, x in E._xfa_packets(again))
    assert packets["template"] == TEMPLATE
    bad = tmp_path / "bad.xml"
    bad.write_bytes(b"<a>\n<b></a>")
    with pytest.raises(E.FormError) as exc:
        E.import_xfa_data(again, bad.read_bytes())
    assert "riga 2" in str(exc.value)


def test_collect_and_series_from_window(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    paths = []
    for i in range(3):
        pdf = build_form()
        E.apply_values(pdf, {"nome": f"N{i}"})
        p = tmp_path / f"c{i}.pdf"
        pdf.save(str(p))
        paths.append(str(p))
    out = tmp_path / "raccolta.csv"
    report = module().collect(paths, str(out))
    rows = list(csv.reader(io.StringIO(out.read_text())))
    assert len(rows) == 4 and rows[0][0] == "File" and rows[1][0] == "c0.pdf"
    src = tmp_path / "serie.csv"
    src.write_text("nome,cognome\nAnna,Rossi\nLuca,Rossi\n")
    outdir = tmp_path / "serie"
    outdir.mkdir()
    rep = module().fill_series(str(src), str(outdir), "{cognome}.pdf", view)
    names = sorted(os.path.basename(p) for p in rep["created"])
    assert names == ["Rossi (2).pdf", "Rossi.pdf"]
    assert E.form_values(pymupdf.open(str(outdir / "Rossi (2).pdf")))["nome"] == "Luca"
    del report


def test_export_name_tm(window, tmp_path, form_pdf):
    view = open_form(window, form_pdf)
    with view.document.change("tm") as pdf:
        f = [x for x in E.list_fields(pdf) if x.name == "cognome"][0]
        E.update_properties(pdf, 0, f.xref, export_name="surname")
    module().import_values({"cognome": "Bianchi"}, view)
    out = tmp_path / "d.json"
    module().export_data(str(out), view=view)
    data = json.loads(out.read_text())
    assert data["surname"] == "Bianchi" and "cognome" not in data
    blank = tmp_path / "b.pdf"
    pdf = build_form()
    g = [x for x in E.list_fields(pdf) if x.name == "cognome"][0]
    E.update_properties(pdf, 0, g.xref, export_name="surname")
    pdf.save(str(blank))
    view2 = open_form(window, blank)
    report = module().import_data(str(out), view=view2)
    assert values(view2)["cognome"] == "Bianchi" and not report["unknown"]


def test_xfa_unmapped_fields_reported(window, tmp_path):
    make_xfa(tmp_path / "s.pdf", True)
    pdf = pymupdf.open(str(tmp_path / "s.pdf"))
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 100, 250, 120), "form1.extra")
    pdf.save(str(tmp_path / "s2.pdf"))
    assert E.xfa_unmapped(pymupdf.open(str(tmp_path / "s2.pdf"))) == ["form1.extra"]
    view = open_form(window, tmp_path / "s2.pdf")
    bar = forms.instance().filler(view).text_of_bar()
    assert "1 campo senza dati XML associati" in bar


def test_convert_dynamic_from_window(window, tmp_path):
    import hashlib

    from .test_xfa_convert import data, make_xfa as make_dynamic, tpl
    body = ('<field name="nome" w="80mm" h="9mm"><ui><textEdit/></ui><caption><value><text>Nome</text></value></caption></field>'
            '<field name="eta" w="40mm" h="9mm"><ui><numericEdit/></ui></field>')
    pdf = make_dynamic(tpl(body), data("<form1><nome>Anna</nome><eta>30</eta></form1>"))
    path = tmp_path / "dinamico.pdf"
    pdf.save(str(path))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    view = open_form(window, path)
    assert forms.instance().filler(view).state.xfa == "dynamic"
    module().convert_xfa()
    new = window.ctx.current_view()
    assert new is not view
    with new.document.lock:
        vals = E.form_values(new.document.pdf)
        assert E.xfa_status(new.document.pdf) == "none"
    assert vals["form1.nome"] == "Anna" and vals["form1.eta"] == "30"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
