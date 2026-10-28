"""Conversione dei moduli XML dinamici in moduli standard (FRM-36)."""

from __future__ import annotations

import hashlib

import pymupdf
import pytest

from foliant.modules.forms import engine as E
from foliant.modules.forms import formats as fm
from foliant.modules.forms import xfa_convert as XC

NS_T = 'xmlns="http://www.xfa.org/schema/xfa-template/3.3/"'


def make_xfa(template: str, datasets: str | None = None, *, packets=None) -> pymupdf.Document:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((50, 50), "Attendere: modulo dinamico")
    E._ensure_acroform(pdf)
    bodies = packets if packets is not None else [("template", template)] + (
        [("datasets", datasets)] if datasets is not None else [])
    parts = []
    for name, body in bodies:
        x = pdf.get_new_xref()
        pdf.update_object(x, "<<>>")
        pdf.update_stream(x, body.encode("utf-8"))
        parts.append(f"({name}) {x} 0 R")
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/XFA", "[" + " ".join(parts) + "]")
    return pymupdf.open("pdf", pdf.tobytes())


def data(xml: str) -> str:
    return ('<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data>'
            + xml + "</xfa:data></xfa:datasets>")


def tpl(body: str, page: str = "") -> str:
    return f'<template {NS_T}><subform name="form1" layout="tb">{page}{body}</subform></template>'


def out_doc(pdf):
    raw, report = XC.convert(pdf)
    return pymupdf.open("pdf", raw), report


DYNAMIC = tpl("""
<field name="nome" w="80mm" h="8mm"><ui><textEdit/></ui>
  <caption reserve="25mm"><value><text>Nome</text></value></caption></field>
<field name="importo" w="60mm" h="8mm"><ui><numericEdit/></ui><value><decimal fracDigits="2"/></value></field>
<field name="nascita" w="60mm" h="8mm"><ui><dateTimeEdit/></ui></field>
<field name="privacy" w="60mm" h="8mm"><ui><checkButton/></ui>
  <items><integer>1</integer><integer>0</integer></items></field>
<exclGroup name="sesso" layout="tb">
  <field name="m" w="30mm" h="7mm"><ui><checkButton shape="round"/></ui><items><text>M</text></items></field>
  <field name="f" w="30mm" h="7mm"><ui><checkButton shape="round"/></ui><items><text>F</text></items></field>
</exclGroup>
<field name="paese" w="60mm" h="8mm"><ui><choiceList/></ui>
  <items><text>Italia</text><text>Francia</text></items>
  <items save="1"><text>IT</text><text>FR</text></items></field>
<field name="invia" w="30mm" h="8mm"><ui><button/></ui><caption><value><text>Invia</text></value></caption></field>
<field name="firma" w="60mm" h="15mm"><ui><signature/></ui></field>
<field name="note" w="120mm" h="20mm"><ui><textEdit multiLine="1"/></ui></field>
<field name="citta" w="60mm" h="8mm"><ui><choiceList open="userInput"/></ui>
  <items><text>Roma</text><text>Milano</text></items></field>
<subform name="item" layout="tb"><occur min="1" max="-1"/>
  <field name="desc" w="80mm" h="8mm"><ui><textEdit/></ui></field>
  <field name="qty" w="30mm" h="8mm"><ui><numericEdit/></ui></field>
</subform>
""")

DYNAMIC_DATA = data("""<form1><nome>Anna</nome><importo>12.50</importo><nascita>1990-03-01</nascita>
<privacy>1</privacy><sesso>F</sesso><paese>FR</paese><note>riga 1
riga 2</note><citta>Torino</citta>
<item><desc>Viti</desc><qty>3</qty></item><item><desc>Dadi</desc><qty>5</qty></item>
<item><desc>Rondelle</desc><qty>7</qty></item></form1>""")


def test_dynamic_form_with_repeated_subform():
    out, report = out_doc(make_xfa(DYNAMIC, DYNAMIC_DATA))
    vals = E.form_values(out)
    assert vals["form1.nome"] == "Anna"
    assert vals["form1.importo"] == "12.50"
    assert vals["form1.nascita"] == "01/03/1990"
    assert vals["form1.privacy"] == "1"
    assert vals["form1.sesso"] == "F"
    assert vals["form1.paese"] == "FR"
    assert vals["form1.note"] == "riga 1\nriga 2"
    assert vals["form1.citta"] == "Torino"
    assert [vals[f"form1.item{s}.desc"] for s in ("", "[1]", "[2]")] == ["Viti", "Dadi", "Rondelle"]
    assert [vals[f"form1.item{s}.qty"] for s in ("", "[1]", "[2]")] == ["3", "5", "7"]
    info = {f.name: f for f in E.list_fields(out)}
    assert info["form1.importo"].fmt.type == fm.NUMBER
    assert info["form1.nascita"].fmt.type == fm.DATE
    assert info["form1.note"].multiline
    assert info["form1.citta"].kind == E.COMBO and info["form1.citta"].editable
    assert info["form1.paese"].kind == E.COMBO and not info["form1.paese"].editable
    assert info["form1.invia"].kind == E.BUTTON and info["form1.invia"].caption == "Invia"
    assert info["form1.firma"].kind == E.SIGNATURE
    assert info["form1.privacy"].kind == E.CHECKBOX
    assert sorted(f.export for f in E.list_fields(out) if f.name == "form1.sesso") == ["F", "M"]
    # caption a sinistra: il widget parte dopo la riserva di 25 mm
    assert info["form1.nome"].rect.x0 == pytest.approx(36 + 25 * 72 / 25.4, abs=1)
    assert "Nome" in out[0].get_text()
    assert report.fields == 16 and report.pages == 1
    assert E.xfa_status(out) == "none"
    cat = out.pdf_catalog()
    assert out.xref_get_key(cat, "AcroForm/XFA")[0] == "null"
    assert out.xref_get_key(cat, "NeedsRendering")[0] == "null"


def test_repeat_min_without_data_and_analyze():
    t = tpl('<subform name="r" layout="tb"><occur min="2" max="5"/>'
            '<field name="a" w="40mm" h="8mm"><ui><textEdit/></ui></field></subform>')
    pdf = make_xfa(t, data("<form1/>"))
    rep = XC.analyze(pdf)
    assert rep.fields == 2 and rep.pages == 1
    out, _r = out_doc(pdf)
    assert sorted(E.form_values(out)) == ["form1.r.a", "form1.r[1].a"]


def test_positioned_layout_in_mm():
    page = ('<pageSet><pageArea name="p"><contentArea x="10mm" y="20mm" w="190mm" h="257mm"/>'
            '<medium stock="a4" short="210mm" long="297mm"/></pageArea></pageSet>')
    t = (f'<template {NS_T}><subform name="form1" layout="position">{page}'
         '<field name="a" x="25.4mm" y="50mm" w="40mm" h="10mm"><ui><textEdit/></ui></field>'
         '<subform name="s" x="2cm" y="100mm" w="100mm" h="50mm" layout="position">'
         '<field name="b" x="1in" y="72pt" w="30mm" h="8mm"><ui><textEdit/></ui></field></subform>'
         '<draw x="0" y="0" w="50mm" h="0"><value><line/></value></draw>'
         '</subform></template>')
    out, _r = out_doc(make_xfa(t, data("<form1/>")))
    mm = 72 / 25.4
    r = {f.name: f.rect for f in E.list_fields(out)}
    assert r["form1.a"].x0 == pytest.approx((10 + 25.4) * mm, abs=1)
    assert r["form1.a"].y0 == pytest.approx((20 + 50) * mm, abs=1)
    assert r["form1.a"].width == pytest.approx(40 * mm, abs=1)
    assert r["form1.s.b"].x0 == pytest.approx((10 + 20) * mm + 72, abs=1)
    assert r["form1.s.b"].y0 == pytest.approx((20 + 100) * mm + 72, abs=1)
    assert out[0].rect.width == pytest.approx(210 * mm, abs=1)
    assert any(d["items"][0][0] == "l" for d in out[0].get_drawings())


def test_page_break_on_long_flowed_content():
    fields = "".join(f'<field name="f{i}" h="10mm"><ui><textEdit/></ui></field>' for i in range(60))
    out, report = out_doc(make_xfa(tpl(fields), data("<form1/>")))
    assert out.page_count >= 2 and report.pages == out.page_count
    assert len(E.list_fields(out)) == 60
    for f in E.list_fields(out):
        assert f.rect.y1 <= out[f.page].rect.height - 36 + 0.5


def test_calculate_scripts():
    t = tpl("""
<field name="a" h="8mm"><ui><numericEdit/></ui></field>
<field name="b" h="8mm"><ui><numericEdit/></ui></field>
<field name="tot" h="8mm"><ui><numericEdit/></ui><calculate><script>Sum(a, b)</script></calculate></field>
<field name="prod" h="8mm"><ui><numericEdit/></ui><calculate><script>a * b</script></calculate></field>
<field name="strano" h="8mm"><ui><textEdit/></ui>
  <calculate><script>if (a > 1) then b else 0 endif</script></calculate></field>
<field name="ok" h="8mm"><ui><button/></ui>
  <event activity="click"><script contentType="application/x-javascript">app.alert(1)</script></event></field>
""")
    pdf = make_xfa(t, data("<form1><a>2</a><b>3</b></form1>"))
    out, report = out_doc(pdf)
    info = {f.name: f for f in E.list_fields(out)}
    assert info["form1.tot"].calc == E.Calculation("SUM", ["form1.a", "form1.b"])
    assert info["form1.prod"].calc == E.Calculation("PRD", ["form1.a", "form1.b"])
    assert info["form1.strano"].calc is None
    assert ("form1.strano", "calculate") in report.scripts
    assert ("form1.ok", "click") in report.scripts
    assert any("«form1.strano»" in line and "calcolo" in line for line in report.unsupported)
    assert XC.analyze(pdf).scripts == report.scripts


def test_calculate_over_repeated_subform():
    t = tpl("""<subform name="item" layout="tb"><occur min="0" max="-1"/>
<field name="amount" h="8mm"><ui><numericEdit/></ui></field></subform>
<field name="total" h="8mm"><ui><numericEdit/></ui><calculate><script>Sum(item[*].amount)</script></calculate></field>""")
    out, _r = out_doc(make_xfa(t, data("<form1><item><amount>1</amount></item><item><amount>2</amount></item></form1>")))
    calc = {f.name: f for f in E.list_fields(out)}["form1.total"].calc
    assert calc.op == "SUM" and calc.fields == ["form1.item.amount", "form1.item[1].amount"]


def test_original_unchanged(tmp_path):
    path = tmp_path / "x.pdf"
    make_xfa(DYNAMIC, DYNAMIC_DATA).save(str(path))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    pdf = pymupdf.open(str(path))
    mem = pdf.tobytes(no_new_id=True)
    XC.convert(pdf)
    XC.analyze(pdf)
    assert pdf.tobytes(no_new_id=True) == mem
    assert not pdf.is_dirty
    pdf.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_no_template_raises():
    pdf = make_xfa("", packets=[("datasets", data("<form1/>"))])
    with pytest.raises(E.FormError):
        XC.analyze(pdf)
    with pytest.raises(E.FormError):
        XC.convert(pdf)
    plain = pymupdf.open()
    plain.new_page()
    with pytest.raises(E.FormError):
        XC.convert(plain)


def test_nothing_to_lay_out_raises():
    with pytest.raises(E.FormError):
        XC.convert(make_xfa(f'<template {NS_T}><subform name="form1"/></template>', data("<form1/>")))


def test_unsupported_listed_and_bind_none():
    t = tpl("""<field name="bar" h="10mm"><ui><barcode type="code128"/></ui></field>
<field name="x" h="8mm"><ui><textEdit/></ui><bind match="none"/><value><text>predefinito</text></value></field>
<draw h="10mm"><value><arc/></value></draw>""")
    out, report = out_doc(make_xfa(t, data("<form1><x>dai dati</x></form1>")))
    assert E.form_values(out) == {"form1.x": "predefinito"}
    assert any("barcode" in line for line in report.unsupported)
    assert any("arc" in line for line in report.unsupported)


def test_measure_and_naming():
    assert XC.measure("25.4mm") == pytest.approx(72)
    assert XC.measure("1") == 72 and XC.measure("2cm") == pytest.approx(56.69, abs=0.01)
    assert XC.measure("10pt") == 10 and XC.measure("bad", 5) == 5
    assert XC.field_name_style() in ("brackets", "underscore")


def test_translations_cover_messages():
    import ast
    import inspect
    src = inspect.getsource(XC)
    found = {n.args[0].value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_" and n.args
             and isinstance(n.args[0], ast.Constant)}
    assert found <= set(XC.TRANSLATIONS)
