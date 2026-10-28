"""Logica dei campi: valori, creazione, proprietà, calcoli, disposizione (senza interfaccia)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.forms import engine as E
from foliant.modules.forms import formats as fm

from .conftest import build_form, reopen


def by_name(pdf, name):
    return [f for f in E.list_fields(pdf) if f.name == name]


def one(pdf, name):
    return by_name(pdf, name)[0]


# ---------------------------------------------------------------- lettura e valori
def test_list_fields_kinds(form):
    kinds = {f.name: f.kind for f in E.list_fields(form)}
    assert kinds == {"nome": "text", "cognome": "text", "privacy": "checkbox", "scelta": "radio",
                     "paese": "combo", "colori": "list"}
    assert one(form, "nome").required
    assert [f.export for f in by_name(form, "scelta")] == ["A", "B", "C"]


def test_text_value_roundtrip_unicode(form):
    f = one(form, "nome")
    E.set_value(form, f.page, f.xref, "Àlfa Ελληνικά 日本語")
    again = reopen(form)
    assert one(again, "nome").value == "Àlfa Ελληνικά 日本語"
    # valore visibile nell'aspetto
    assert "Àlfa" in again[0].get_text()


def test_maxlen_truncates(form):
    f = one(form, "cognome")
    E.update_properties(form, f.page, f.xref, maxlen=5)
    E.set_value(form, f.page, f.xref, "ABCDEFGH")
    assert one(form, "cognome").value == "ABCDE"
    assert form.xref_get_key(f.field_xref, "MaxLen") == ("int", "5")


def test_comb_requires_maxlen_and_disables_multiline(form):
    f = one(form, "cognome")
    with pytest.raises(E.FormError):
        E.update_properties(form, f.page, f.xref, comb=True)
    E.update_properties(form, f.page, f.xref, multiline=True)
    E.update_properties(form, f.page, f.xref, comb=True, maxlen=11)
    g = one(form, "cognome")
    assert g.comb and not g.multiline and not g.password and g.maxlen == 11
    E.set_value(form, g.page, g.xref, "RSSMRA80A01")
    assert one(reopen(form), "cognome").value == "RSSMRA80A01"


def test_password_value_never_written(form):
    f = one(form, "cognome")
    E.update_properties(form, f.page, f.xref, password=True)
    E.set_value(form, f.page, f.xref, "segretissima")
    data = form.tobytes()
    assert b"segretissima" not in data
    assert one(reopen(form), "cognome").value == ""


def test_radio_group_exclusive(form):
    a, b, c = by_name(form, "scelta")
    E.set_value(form, 0, a.xref, "A")
    E.toggle(form, 0, b.xref)
    again = reopen(form)
    fx = one(again, "scelta").field_xref
    assert again.xref_get_key(fx, "V") == ("name", "/B")
    states = [again.xref_get_key(f.xref, "AS")[1] for f in by_name(again, "scelta")]
    assert states == ["/Off", "/B", "/Off"]


def test_radio_no_toggle_off(form):
    a, b, c = by_name(form, "scelta")
    E.set_value(form, 0, b.xref, "B")
    assert E.toggle(form, 0, b.xref) == E.OFF  # senza flag si disattiva
    E.update_properties(form, 0, b.xref, no_toggle_off=True)
    E.set_value(form, 0, b.xref, "B")
    assert E.toggle(form, 0, b.xref) == "B"
    assert one(form, "scelta").value == "B"


def test_radio_three_widgets_one_field():
    pdf = pymupdf.open()
    pdf.new_page()
    for i, v in enumerate("SML"):
        E.create_field(pdf, 0, E.RADIO, pymupdf.Rect(50 + 30 * i, 50, 64 + 30 * i, 64), "taglia", export=v)
    again = reopen(pdf)
    fields = by_name(again, "taglia")
    assert len({f.field_xref for f in fields}) == 1
    assert [f.export for f in fields] == ["S", "M", "L"]
    root_fields = E._acroform_fields(again)
    assert fields[0].field_xref in root_fields


def test_checkbox_toggle_twice_and_sync_pages():
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page()
    x1 = E.create_field(pdf, 0, E.CHECKBOX, pymupdf.Rect(50, 50, 64, 64), "ok")
    x3 = E.create_field(pdf, 2, E.CHECKBOX, pymupdf.Rect(50, 50, 64, 64), "tmp")
    E.merge_into(pdf, 2, x3, "ok")
    E.toggle(pdf, 0, x1)
    assert pdf.xref_get_key(x3, "AS") == ("name", "/Yes")
    E.toggle(pdf, 0, x1)
    assert pdf.xref_get_key(x1, "AS") == ("name", "/Off")
    assert pdf.xref_get_key(x3, "AS") == ("name", "/Off")


def test_combo_export_value(form):
    f = one(form, "paese")
    assert f.options == ["Italia", "Francia"] and f.exports == ["IT", "FR"]
    E.set_value(form, 0, f.xref, "Francia")
    again = reopen(form)
    assert one(again, "paese").value == "FR"
    assert "Francia" in again[0].get_text()


def test_combo_editable_free_text(form):
    f = one(form, "paese")
    with pytest.raises(E.FormError):
        E.set_value(form, 0, f.xref, "Svizzera")
    E.update_properties(form, 0, f.xref, editable=True)
    E.set_value(form, 0, f.xref, "Svizzera")
    assert one(form, "paese").value == "Svizzera"


def test_list_multiselect_v_and_i(form):
    f = one(form, "colori")
    E.set_value(form, 0, f.xref, ["rosso", "blu"])
    again = reopen(form)
    g = one(again, "colori")
    assert g.value == ["rosso", "blu"]
    assert again.xref_get_key(g.field_xref, "I") == ("array", "[0 2]")


def test_form_values_and_apply(form):
    E.apply_values(form, {"nome": "Mario", "privacy": "Yes", "scelta": "C", "paese": "IT", "colori": ["verde"]})
    vals = E.form_values(form)
    assert vals == {"nome": "Mario", "cognome": "", "privacy": "Yes", "scelta": "C", "paese": "IT", "colori": ["verde"]}
    applied, unknown = E.apply_values(form, {"nome": "Anna", "inesistente": "x"})
    assert applied == ["nome"] and unknown == ["inesistente"]


def test_reset_to_defaults(form):
    f = one(form, "paese")
    E.update_properties(form, 0, f.xref, default="IT")
    E.apply_values(form, {"nome": "x", "cognome": "y", "privacy": True, "scelta": "A", "paese": "FR"})
    E.reset_form(form)
    vals = E.form_values(form)
    assert vals["paese"] == "IT"
    assert vals["nome"] == "" and vals["cognome"] == "" and vals["privacy"] == "Off" and vals["scelta"] == "Off"


def test_reset_selected_only(form):
    E.apply_values(form, {"nome": "x", "cognome": "y"})
    E.reset_form(form, ["nome"])
    vals = E.form_values(form)
    assert vals["nome"] == "" and vals["cognome"] == "y"


# ---------------------------------------------------------------- creazione e proprietà
@pytest.mark.parametrize("kind,ft", [
    (E.TEXT, "/Tx"), (E.CHECKBOX, "/Btn"), (E.RADIO, "/Btn"), (E.COMBO, "/Ch"), (E.LIST, "/Ch"),
    (E.BUTTON, "/Btn"), (E.SIGNATURE, "/Sig"), ("date", "/Tx"),
])
def test_create_each_kind(kind, ft):
    pdf = pymupdf.open()
    pdf.new_page()
    xref = E.create_field(pdf, 0, kind, pymupdf.Rect(50, 50, 200, 80))
    again = reopen(pdf)
    f = E.list_fields(again)[0]
    assert again.xref_get_key(E._field_xref(again, f.xref), "FT")[1] == ft
    assert again.xref_get_key(again.pdf_catalog(), "AcroForm")[0] != "null"
    assert f.field_xref in E._acroform_fields(again) or E._root_of(again, f.xref) in E._acroform_fields(again)
    if kind == "date":
        assert f.fmt.type == fm.DATE and f.name == "Data1"
    if kind == E.TEXT:
        assert f.name == "Testo1"
    del xref


def test_create_then_undo_leaves_no_trace(tmp_path):
    from foliant.api import Document
    path = tmp_path / "vuoto.pdf"
    d = pymupdf.open()
    d.new_page()
    d.save(str(path))
    doc = Document.open(path)
    with doc.change("Crea campo") as pdf:
        E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70))
    doc.undo()
    with doc.lock:
        pdf = doc.pdf
        assert pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")[0] == "null"
        assert list(pdf[0].widgets()) == []
        assert pdf.xref_get_key(pdf.page_xref(0), "Annots")[0] == "null"


def test_small_rect_enlarged_and_clamped():
    pdf = pymupdf.open()
    pdf.new_page(width=200, height=200)
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(190, 190, 191, 191))
    r = E.field_info(pdf, 0, x).rect
    assert r.width >= 4 and r.height >= 4 and r.x1 <= 200.01 and r.y1 <= 200.01


def test_hierarchical_rename():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70))
    assert E.field_info(pdf, 0, x).name == "Testo1"
    E.update_properties(pdf, 0, x, name="cliente.nome")
    again = reopen(pdf)
    f = E.list_fields(again)[0]
    assert f.name == "cliente.nome"
    root = E._acroform_fields(again)
    assert len(root) == 1 and again.xref_get_key(root[0], "T") == ("string", "cliente")
    assert E.form_values(again) == {"cliente.nome": ""}


def test_name_taken_and_merge_shares_value():
    pdf = pymupdf.open()
    pdf.new_page()
    a = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "A")
    b = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 100, 200, 120), "B")
    with pytest.raises(E.NameTaken) as exc:
        E.update_properties(pdf, 0, b, name="A")
    assert exc.value.compatible
    E.merge_into(pdf, 0, b, "A")
    E.set_value(pdf, 0, a, "condiviso")
    assert [f.value for f in E.list_fields(pdf)] == ["condiviso", "condiviso"]
    assert E.form_values(pdf) == {"A": "condiviso"}


def test_required_on_many_fields_single_undo(tmp_path):
    from foliant.api import Document
    path = tmp_path / "m.pdf"
    build_form(path).close()
    doc = Document.open(path)
    with doc.change("Obbligatorio") as pdf:
        for f in E.list_fields(pdf)[:3]:
            E.update_properties(pdf, f.page, f.xref, required=True)
    with doc.lock:
        assert all(f.required for f in E.list_fields(doc.pdf)[:3])
    doc.undo()
    with doc.lock:
        assert [f.required for f in E.list_fields(doc.pdf)[:3]] == [True, False, False]


def test_visibility_flags(form):
    f = one(form, "cognome")
    E.update_properties(form, 0, f.xref, visibility="screen")
    assert one(form, "cognome").visibility == "screen"
    assert not (E._annot_flags(form, f.xref) & E.AF_PRINT)
    E.update_properties(form, 0, f.xref, visibility="hidden")
    assert one(form, "cognome").hidden
    E.update_properties(form, 0, f.xref, visibility="visible")
    assert one(form, "cognome").visibility == "visible"


def test_appearance_da_and_alignment(form):
    f = one(form, "cognome")
    E.update_properties(form, 0, f.xref, font="Times", fontsize=12, text_color=(1, 0, 0), align=1)
    E.set_value(form, 0, f.xref, "Centro")
    again = reopen(form)
    g = one(again, "cognome")
    da = again.xref_get_key(g.field_xref, "DA")[1]
    assert "/TiRo 12 Tf" in da and da.startswith("1 0 0 rg")
    assert again.xref_get_key(g.field_xref, "Q") == ("int", "1")
    words = [w for w in again[0].get_text("words") if w[4] == "Centro"]
    assert words
    mid = (words[0][0] + words[0][2]) / 2
    assert abs(mid - (g.rect.x0 + g.rect.x1) / 2) < 6


def test_transparent_fill_and_dashed_border(form):
    f = one(form, "cognome")
    E.update_properties(form, 0, f.xref, fill_color=None, border_style="D", border_width=2)
    again = reopen(form)
    x = one(again, "cognome").xref
    assert again.xref_get_key(x, "MK/BG")[0] == "null"
    assert again.xref_get_key(x, "BS/S") == ("name", "/D")
    assert again.xref_get_key(x, "BS/W") == ("int", "2")


def test_check_style_and_export_unicode(form):
    f = one(form, "privacy")
    E.update_properties(form, 0, f.xref, check_style="cross", export="Sì")
    E.set_value(form, 0, f.xref, True)
    again = reopen(form)
    assert E.form_values(again)["privacy"] == "Sì"
    assert one(again, "privacy").check_style == "cross"


def test_sort_options(form):
    f = one(form, "colori")
    E.update_properties(form, 0, f.xref, options=["Zeta", "alfa", "Beta"], sort=True)
    assert one(form, "colori").options == ["alfa", "Beta", "Zeta"]


def test_options_paste_fifty():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.LIST, pymupdf.Rect(50, 50, 200, 200))
    items = [f"Voce {i}" for i in range(50)]
    E.update_properties(pdf, 0, x, options=items)
    assert E.field_info(pdf, 0, x).options == items


def test_rotated_page_widget_rotation():
    pdf = pymupdf.open()
    pdf.new_page().set_rotation(90)
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70))
    assert pdf.xref_get_key(x, "MK/R") == ("int", "90")


# ---------------------------------------------------------------- formati, validazioni, calcoli
def test_number_format_display_and_canonical():
    pdf = pymupdf.open()
    pdf.new_page()
    fmt = fm.Format(fm.NUMBER, 2, 2, 0, "€", False)
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "importo", fmt=fmt)
    info = E.field_info(pdf, 0, x)
    E.set_value(pdf, 0, x, E.normalize_input(info, "1234,5"))
    again = reopen(pdf)
    f = E.list_fields(again)[0]
    assert f.value == "1234.5"
    assert "1.234,50 €" in again[0].get_text()
    k = again.xref_get_key(f.field_xref, "AA/K/JS")[1]
    assert k == 'AFNumber_Keystroke(2, 2, 0, 0, "€", false);'


@pytest.mark.parametrize("separator,text,stored", [
    (2, "12.500", "12500"), (2, "1.234", "1234"), (2, "1234,5", "1234.5"), (2, "12.500,00", "12500"),
    (2, "1.234.567", "1234567"), (3, "12.500", "12500"), (3, "12,5", "12.5"),
    (0, "12,500", "12500"), (0, "1,234.56", "1234.56"), (1, "12,500", "12500"), (4, "1'234.5", "1234.5"),
])
def test_number_input_follows_separator_style(separator, text, stored):
    """FRM-08: l'input si legge con lo stile dei separatori del campo (il punto delle migliaia
    di «12.500» con lo stile 1.234,56 non è un separatore decimale)."""
    assert fm.normalize(fm.Format(fm.NUMBER, 2, separator, 0, "€", False), text) == stored


def test_number_input_rejects_two_decimal_separators():
    with pytest.raises(fm.FormatError):
        fm.normalize(fm.Format(fm.NUMBER, 2, 2), "1,2,3")
    with pytest.raises(fm.FormatError):
        fm.normalize(fm.Format(fm.NUMBER, 2, 0), "1.2.3")


def test_number_edit_text_round_trip():
    """Il valore nell'editor usa il separatore decimale dello stile: confermarlo non lo cambia."""
    for sep in range(5):
        fmt = fm.Format(fm.NUMBER, 2, sep)
        for stored in ("1234.5", "12500", "-0.25"):
            assert fm.normalize(fmt, fm.edit_text(fmt, stored)) == stored
    assert fm.edit_text(fm.Format(fm.NUMBER, 2, 2), "1234.5") == "1234,5"
    assert fm.edit_text(fm.Format(fm.NUMBER, 2, 0), "1234.5") == "1234.5"


def test_percent_input():
    info = E.FieldInfo(0, 0, 0, "p", E.TEXT, pymupdf.Rect(), fmt=fm.Format(fm.PERCENT, 2, 2))
    assert E.normalize_input(info, "25%") == "0.25"
    assert fm.display(info.fmt, "0.25")[0] == "25,00%"


def test_negative_red_parens():
    assert fm.display(fm.Format(fm.NUMBER, 2, 2, 3), "-1234.5") == ("(1.234,50)", True)


def test_keystroke_filter():
    assert fm.filter_input(fm.Format(fm.NUMBER), "12a3") == "123"
    assert fm.filter_input(fm.Format(fm.SPECIAL, mask="99999"), "0018A7") == "00187"


def test_special_mask():
    f = fm.Format(fm.SPECIAL, mask="99999")
    assert fm.normalize(f, "00187") == "00187"
    with pytest.raises(fm.FormatError):
        fm.normalize(f, "0018A")


def test_dates():
    f = fm.Format(fm.DATE, pattern="dd/mm/yyyy")
    assert fm.normalize(f, "1-3-26") == "01/03/2026"
    assert fm.normalize(f, "5 marzo 2026") == "05/03/2026"
    assert fm.normalize(f, "3 March 99") == "03/03/1999"
    with pytest.raises(fm.FormatError):
        fm.normalize(f, "31/02/2026")
    assert fm.normalize(fm.Format(fm.DATE, pattern="mm/yyyy"), "3/2026") == "03/2026"


def test_times():
    assert fm.normalize(fm.Format(fm.TIME, pattern="HH:MM"), "9:5") == "09:05"
    assert fm.normalize(fm.Format(fm.TIME, pattern="h:MM tt"), "21:30") == "9:30 PM"


def test_format_scripts_roundtrip():
    for f in (fm.Format(fm.NUMBER, 3, 4, 2, "CHF", True), fm.Format(fm.PERCENT, 1, 0),
              fm.Format(fm.DATE, pattern="yyyy-mm-dd"), fm.Format(fm.TIME, pattern="HH:MM:ss"),
              fm.Format(fm.SPECIAL, mask="99999-9999"), fm.Format(fm.SPECIAL, mask="AA-999")):
        f_js, _k = f.scripts()
        assert fm.Format.from_script(f_js) == f
    assert fm.is_custom_script('AFNumber_Format(2, 0, 0, 0, "", true); app.alert(1);')


def test_validation_range_script_and_check(form):
    f = one(form, "cognome")
    E.update_properties(form, 0, f.xref, validation=E.Validation(18, 99))
    g = one(form, "cognome")
    assert form.xref_get_key(g.field_xref, "AA/V/JS")[1] == "AFRange_Validate(true, 18, true, 99);"
    assert E.validate(g, "17")
    assert E.validate(g, "18") is None
    assert E.validate(g, "") is None


def test_validation_regex_message(form):
    f = one(form, "cognome")
    v = E.Validation(regex=r"^[^@\s]+@[^@\s]+\.\w+$", message="E-mail non valida")
    E.update_properties(form, 0, f.xref, validation=v)
    g = one(form, "cognome")
    assert g.validation == v
    assert E.validate(g, "mario@") == "E-mail non valida"
    assert E.validate(g, "mario@example.com") is None
    assert E.Validation(5, 1).check()
    assert E.Validation(regex="(").check()


def test_required_validation(form):
    assert E.validate(one(form, "nome"), "") is not None
    assert [f.name for f in E.missing_required(form)] == ["nome"]


def _calc_doc():
    pdf = pymupdf.open()
    pdf.new_page()
    y = 40
    xs = {}
    for n in ("A", "B", "k", "S", "T"):
        xs[n] = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, y, 200, y + 18), n)
        y += 25
    return pdf, xs


def test_sum_recalculated():
    pdf, xs = _calc_doc()
    E.update_properties(pdf, 0, xs["S"], calc=E.Calculation("SUM", ["A", "B"]))
    E.apply_values(pdf, {"A": "2", "B": "3"})
    E.recalculate(pdf)
    assert one(pdf, "S").value == "5"
    E.set_value(pdf, 0, xs["A"], "5")
    E.recalculate(pdf)
    assert one(pdf, "S").value == "8"


def test_average_counts_empty_as_zero():
    pdf, xs = _calc_doc()
    E.update_properties(pdf, 0, xs["S"], calc=E.Calculation("AVG", ["A", "B", "k"]))
    E.apply_values(pdf, {"A": "2", "B": "4"})
    E.recalculate(pdf)
    assert one(pdf, "S").value == "2"


def test_float_rounding():
    pdf, xs = _calc_doc()
    E.update_properties(pdf, 0, xs["S"], calc=E.Calculation("SUM", ["A", "B"]))
    E.apply_values(pdf, {"A": "0.1", "B": "0.2"})
    E.recalculate(pdf)
    assert one(pdf, "S").value == "0.3"


def test_group_sum():
    pdf = pymupdf.open()
    pdf.new_page()
    for i in range(5):
        E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 40 + 25 * i, 200, 58 + 25 * i), f"importo.{i}")
    tot = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 40, 400, 58), "Totale",
                         calc=E.Calculation("SUM", ["importo"]))
    E.apply_values(pdf, {f"importo.{i}": "10" for i in range(5)})
    E.recalculate(pdf)
    assert E.field_info(pdf, 0, tot).value == "50"
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 200, 200, 218), "importo.5")
    E.set_value(pdf, 0, x, "10")
    E.recalculate(pdf)
    assert E.field_info(pdf, 0, tot).value == "60"


def test_cycle_and_order():
    pdf, xs = _calc_doc()
    E.update_properties(pdf, 0, xs["T"], calc=E.Calculation("PRD", ["S", "k"]))
    E.update_properties(pdf, 0, xs["S"], calc=E.Calculation("SUM", ["A", "B"]))
    assert E.calc_order(pdf) == ["T", "S"]
    assert E.order_problems(pdf) == [("T", "S")]
    E.set_calc_order(pdf, E.dependency_order(pdf))
    assert E.calc_order(pdf) == ["S", "T"]
    assert E.find_cycle(pdf, "A", E.Calculation("SUM", ["T"])) == ["A", "T", "S", "A"]
    E.apply_values(pdf, {"A": "1", "B": "2", "k": "4"})
    E.recalculate(pdf)
    assert one(pdf, "T").value == "12"


def test_custom_scripts_detected():
    pdf, xs = _calc_doc()
    fx = E._field_xref(pdf, xs["T"])
    pdf.xref_set_key(fx, "AA", "<</C<</S/JavaScript/JS(event.value = 42;)>>>>")
    assert E.custom_scripts(pdf) == [("T", "calcolo")]


# ---------------------------------------------------------------- disposizione e tabulazione
def test_align_left_and_distribute():
    rects = [pymupdf.Rect(50, 10, 150, 30), pymupdf.Rect(60, 50, 160, 70), pymupdf.Rect(72, 130, 172, 150)]
    out = E.align_rects(rects, "left")
    assert all(abs(r.x0 - 50) < 0.01 for r in out)
    dist = E.distribute_rects(rects, "v")
    gaps = [dist[i + 1].y0 - dist[i].y1 for i in range(2)]
    assert abs(gaps[0] - gaps[1]) < 0.01 and dist[0] == rects[0] and dist[2] == rects[2]
    same = E.align_rects([pymupdf.Rect(0, 0, 100, 20), pymupdf.Rect(10, 10, 160, 40)], "same_size", reference=1)
    assert same[0] == pymupdf.Rect(0, 0, 150, 30)


def test_tab_order_rows_columns_manual():
    pdf = pymupdf.open()
    pdf.new_page()
    a = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 150, 70), "A")
    b = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 52, 400, 72), "B")
    c = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 150, 150, 170), "C")
    d = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(300, 150, 400, 170), "D")
    assert [f.name for f in E.navigation_order(pdf, 0)] == ["A", "B", "C", "D"]
    E.auto_tab_order(pdf, 0, "column")
    assert [f.name for f in E.navigation_order(pdf, 0)] == ["A", "C", "B", "D"]
    E.set_tab_order(pdf, 0, [c, a, b, d])
    again = reopen(pdf)
    assert [f.name for f in E.navigation_order(again, 0)] == ["C", "A", "B", "D"]
    assert E.tab_mode(again, 0) == "manual"


def test_repair_orphan_widgets():
    pdf = pymupdf.open()
    pdf.new_page()
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 150, 70), "A")
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/Fields", "[]")
    assert E.orphan_count(pdf) == 1
    E.prepare_for_edit(pdf)
    again = reopen(pdf)
    assert E.orphan_count(again) == 0 and E.form_values(again) == {"A": ""}


def test_need_appearances_cleared():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 150, 70), "A")
    E.set_value(pdf, 0, x, "testo")
    pdf.xref_set_key(x, "AP", "null")
    pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/NeedAppearances", "true")
    E.prepare_for_edit(pdf)
    assert pdf.xref_get_key(x, "AP/N")[0] == "xref"
    assert not E.needs_appearances(pdf)


def test_flatten(form):
    f = one(form, "nome")
    E.set_value(form, 0, f.xref, "ABC123")
    assert E.flatten(form) > 0
    again = reopen(form)
    assert E.list_fields(again) == []
    assert again.xref_get_key(again.pdf_catalog(), "AcroForm")[0] == "null"
    assert "ABC123" in again[0].get_text()


def test_fill_blocked_by_permissions(tmp_path):
    path = tmp_path / "p.pdf"
    pdf = build_form()
    pdf.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    again = pymupdf.open(str(path))
    assert E.fill_blocked_reason(again)
    assert E.fill_blocked_reason(build_form()) is None


def test_formula_field():
    pdf = pymupdf.open()
    pdf.new_page()
    xs = {n: E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 40 + 30 * i, 200, 58 + 30 * i), n)
          for i, n in enumerate(("Imponibile", "Sconto", "Totale", "c"))}
    E.update_properties(pdf, 0, xs["Totale"], formula="Imponibile * 1.22 - Sconto")
    E.apply_values(pdf, {"Imponibile": "100", "Sconto": "10"})
    E.recalculate(pdf)
    again = reopen(pdf)
    t = one(again, "Totale")
    assert t.value == "112" and t.formula.expression == "Imponibile * 1.22 - Sconto"
    assert E.custom_scripts(again) == []
    E.update_properties(pdf, 0, xs["Totale"], formula="(Imponibile + Sconto) / c")
    E.recalculate(pdf)
    assert one(pdf, "Totale").value == ""
    with pytest.raises(E.FormError):
        E.update_properties(pdf, 0, xs["Totale"], formula="Imponibile * Inesistente")
    assert E.find_cycle(pdf, "Imponibile", E.FX.parse("Totale + 1")) == ["Imponibile", "Totale", "Imponibile"]


def test_unicode_value_embedded_font():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 300, 72), "g")
    before = len(pdf.tobytes(garbage=3, deflate=True))
    E.set_value(pdf, 0, x, "Ελληνικά 日本語")
    data = pdf.tobytes(garbage=3, deflate=True)
    again = pymupdf.open("pdf", data)
    f = E.list_fields(again)[0]
    assert f.value == "Ελληνικά 日本語"
    assert "Ελληνικά 日本語" in again[0].get_text()
    fonts = [again.xref_object(i, compressed=True) for i in range(1, again.xref_length())]
    assert any("/FontFile" in o for o in fonts)  # carattere incorporato
    assert len(data) - before < 200_000  # sottoinsieme, non il carattere intero
    assert again.page_count == 1


def test_button_distinct_down_caption():
    pdf = pymupdf.open()
    pdf.new_page()
    b = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 72), "invia", caption="Invia")
    E.update_properties(pdf, 0, b, caption_down="Invio…")
    again = reopen(pdf)
    f = one(again, "invia")
    assert f.caption == "Invia" and f.caption_down == "Invio…"
    n = int(again.xref_get_key(f.xref, "AP/N")[1].split()[0])
    d = int(again.xref_get_key(f.xref, "AP/D")[1].split()[0])
    assert n != d and again.xref_stream(n) != again.xref_stream(d)
    assert b"Invia" in again.xref_stream(n)


def test_system_font_embedded(tmp_path):
    if not E.system_font_file("DejaVu Serif"):
        pytest.skip("carattere DejaVu Serif non installato")
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 300, 72), "t")
    E.update_properties(pdf, 0, x, font="DejaVu Serif", fontsize=12)
    E.set_value(pdf, 0, x, "Serif incorporato")
    again = reopen(pdf)
    f = E.list_fields(again)[0]
    assert f.font == "DejaVu Serif" and f.value == "Serif incorporato"
    assert "Serif incorporato" in again[0].get_text()
    fonts = [again.xref_object(i, compressed=True) for i in range(1, again.xref_length())]
    assert any("DejaVu#20Serif" in o for o in fonts) and any("/FontFile" in o for o in fonts)
    with pytest.raises(E.FormError):
        E.update_properties(pdf, 0, x, font="Carattere Inesistente XYZ")
    E.update_properties(pdf, 0, x, font="Times")
    assert E.field_info(pdf, 0, x).font == "Times"


def test_tagged_document_form_element(tmp_path):
    pdf = pymupdf.open()
    pdf.new_page()
    cat = pdf.pdf_catalog()
    root, docel, pt = pdf.get_new_xref(), pdf.get_new_xref(), pdf.get_new_xref()
    pdf.update_object(pt, "<</Nums[]>>")
    pdf.update_object(docel, f"<</Type/StructElem/S/Document/P {root} 0 R/K[]>>")
    pdf.update_object(root, f"<</Type/StructTreeRoot/K {docel} 0 R/ParentTree {pt} 0 R/ParentTreeNextKey 0>>")
    pdf.xref_set_key(cat, "StructTreeRoot", f"{root} 0 R")
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "nome")
    assert pdf.xref_get_key(x, "StructParent") == ("int", "0")
    kids = pdf.xref_get_key(docel, "K")[1]
    elem = int(kids.strip("[]").split()[0])
    assert pdf.xref_get_key(elem, "S") == ("name", "/Form")
    assert pdf.xref_get_key(elem, "K/Obj") == ("xref", f"{x} 0 R")
    assert pdf.xref_get_key(pt, "Nums")[1].replace(" ", "") == f"[0{elem}0R]"
    y = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 200, 110), "cognome")
    assert pdf.xref_get_key(y, "StructParent") == ("int", "1")
    E.delete_field(pdf, 0, x)
    assert str(elem) not in pdf.xref_get_key(docel, "K")[1]
    again = reopen(pdf)
    assert [f.name for f in E.list_fields(again)] == ["cognome"]
    # documento senza struttura: nessun collegamento
    plain = pymupdf.open()
    plain.new_page()
    z = E.create_field(plain, 0, E.TEXT, pymupdf.Rect(50, 50, 200, 70), "a")
    assert plain.xref_get_key(z, "StructParent")[0] == "null"


def test_rich_text_value_kept_coherent():
    import xml.etree.ElementTree as ET
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 300, 120), "nota", multiline=True)
    fx = E._field_xref(pdf, x)
    flags = int(pdf.xref_get_key(fx, "Ff")[1])
    pdf.xref_set_key(fx, "Ff", str(flags | E.FF_RICH_TEXT))
    pdf.xref_set_key(fx, "RV", "(<body><p><b>vecchio</b></p></body>)")
    E.set_value(pdf, 0, x, "Riga <1> & co\rRiga 2")
    rv = pdf.xref_get_key(fx, "RV")[1]
    root = ET.fromstring(rv)
    texts = [p.text for p in root.iter("{http://www.w3.org/1999/xhtml}p")]
    assert texts == ["Riga <1> & co", "Riga 2"]
    assert E.field_info(pdf, 0, x).value == "Riga <1> & co\rRiga 2"
    plain = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 150, 300, 170), "semplice")
    E.set_value(pdf, 0, plain, "x")
    assert pdf.xref_get_key(E._field_xref(pdf, plain), "RV")[0] == "null"


def test_scan_matches_widget_reader():
    """La lettura veloce (scan.py) coincide con quella del motore, anche su pagine ruotate e ritagliate."""
    import dataclasses
    from foliant.modules.forms import scan as S
    pdf = build_form(pages=2)
    pdf[1].set_cropbox(pymupdf.Rect(10, 20, 500, 800))
    pdf[1].set_rotation(90)
    E.create_field(pdf, 1, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "r", fmt=fm.Format(fm.NUMBER, 2, 2, 1, "€", True),
                   validation=E.Validation(1, 9), maxlen=8, font="Times", fontsize=11, text_color=(1, 0, 0),
                   align=2, tooltip="t", export_name="tm", fill_color=None, border_style="D", border_width=2)
    E.create_field(pdf, 1, E.BUTTON, pymupdf.Rect(50, 100, 150, 120), "b", caption="Ok", caption_down="Giù")
    E.apply_values(pdf, {"nome": "Mario", "privacy": True, "scelta": "B", "paese": "FR", "colori": ["blu"]})
    slow = [E._info(pdf, i, w) for i in range(pdf.page_count) for w in pdf[i].widgets()]
    fast = S.scan_fields(pdf)
    assert len(slow) == len(fast)
    for a, b in zip(slow, fast):
        for fld in dataclasses.fields(a):
            va, vb = getattr(a, fld.name), getattr(b, fld.name)
            if isinstance(va, (tuple, pymupdf.Rect)) and isinstance(vb, (tuple, pymupdf.Rect)):
                assert all(abs(p - q) < 0.01 for p, q in zip(va, vb)), (a.name, fld.name)
            elif isinstance(va, float) or isinstance(vb, float):
                assert abs((va or 0) - (vb or 0)) < 0.01, (a.name, fld.name)
            else:
                assert va == vb, (a.name, fld.name, va, vb)


def test_rtl_value_appearance():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 350, 72), "nome")
    E.set_value(pdf, 0, x, "שלום עולם")
    again = reopen(pdf)
    assert E.list_fields(again)[0].value == "שלום עולם"
    text = again[0].get_text()
    assert "שלום" in text and "עולם" in text
    # allineato a destra: l'inizio del testo è vicino al bordo destro del campo
    words = [w for w in again[0].get_text("words") if w[4] in ("שלום", "עולם")]
    assert max(w[2] for w in words) > 300
    fonts = [again.xref_object(i, compressed=True) for i in range(1, again.xref_length())]
    assert any("/FontFile" in o for o in fonts)
    y = E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 100, 350, 122), "ar")
    E.set_value(pdf, 0, y, "مرحبا")
    assert E.field_info(pdf, 0, y).value == "مرحبا"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_blank_signature_appearance_has_border(rotation):
    """Il campo firma vuoto ha nel file sfondo e bordo (/MK /BC, /BS /W), su pagine ruotate e no."""
    pdf = pymupdf.open()
    pdf.new_page().set_rotation(rotation)
    x = E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(50, 50, 250, 100), "Firma")
    stream = pdf.xref_stream(int(pdf.xref_get_key(x, "AP/N")[1].split()[0]))
    assert b"0.45 0.5 0.58 RG 1 w" in stream and b"re S" in stream
    assert b"1 1 1 rg" in stream


def _png(color, size=(400, 300)):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _ap(pdf, xref, state):
    t, v = pdf.xref_get_key(xref, f"AP/{state}")
    return pdf.xref_stream(int(v.split()[0])) if t == "xref" else None


def test_button_state_appearances_and_icons():
    """FRM-18: icone e didascalie per stato → aspetti /N, /R, /D distinti; evidenziazione «push»."""
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 250, 110), "invia", caption="Invia")
    E.update_properties(pdf, 0, x, icon_normal=_png((200, 30, 30)), icon_down=_png((30, 30, 200)),
                        caption_rollover="Clicca", caption_down="Invio…", layout="icon_left")
    again = reopen(pdf)
    f = [g for g in E.list_fields(again) if g.name == "invia"][0]
    assert f.layout == "icon_left" and set(f.icons) == {"normal", "down"} and not f.image
    assert f.caption_rollover == "Clicca" and f.caption_down == "Invio…"
    n, r, d = (_ap(again, f.xref, s) for s in "NRD")
    assert b"(Invia)" in n and b"(Clicca)" in r and b"(Invio\x85)" in d   # «…» in WinAnsi
    assert b"/Ic Do" in n and b"/Ic Do" in d
    icon_n = again.xref_get_key(f.xref, "MK/I")[1]
    icon_d = again.xref_get_key(f.xref, "MK/IX")[1]
    assert icon_n != icon_d
    assert again.xref_get_key(f.xref, "H") == ("name", "/P") and f.highlight == "push"
    E.update_properties(pdf, 0, x, highlight="outline")
    assert pdf.xref_get_key(x, "H") == ("name", "/O") and E.field_info(pdf, 0, x).highlight == "outline"
    assert E.list_fields(pdf)[0].highlight == "outline"   # lettura veloce (scan.py)
    # il lettore di prova disegna l'icona normale (rossa) a sinistra e la didascalia
    pix = again[0].get_pixmap(clip=pymupdf.Rect(50, 50, 250, 110))
    assert pix.pixel(20, 30)[0] > 150 and pix.pixel(20, 30)[2] < 80
    assert "Invia" in again[0].get_text()


@pytest.mark.parametrize("layout", E.BUTTON_LAYOUTS)
def test_button_layouts(layout):
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 200, 130), "b", caption="Ok")
    E.update_properties(pdf, 0, x, icon_normal=_png((0, 160, 0), (60, 60)), layout=layout)
    f = E.field_info(pdf, 0, x)
    assert f.layout == layout and not f.image
    stream = _ap(pdf, x, "N")
    assert (b"(Ok)" in stream) == (layout != "icon")
    assert (b"/Ic Do" in stream) == (layout != "text")


def test_button_icon_from_pdf_page_and_duplicate():
    src = pymupdf.open()
    src.new_page(width=100, height=100).draw_rect(pymupdf.Rect(10, 10, 90, 90), fill=(0, 0, 1))
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 150), "logo", caption="")
    E.update_properties(pdf, 0, x, icon_normal=src.tobytes(), layout="icon")
    assert b"/Ic Do" in _ap(pdf, x, "N")
    d = E.duplicate_field(pdf, 0, x, 120, 0)
    g = E.field_info(pdf, 0, d)
    assert g.icons == ("normal",) and g.layout == "icon"
    assert pdf.xref_get_key(d, "MK/I") == pdf.xref_get_key(x, "MK/I")   # icona condivisa, non copiata
    assert not E.is_image_field(pdf, x)   # pulsante con icona, non campo immagine


def test_image_field_keeps_image_after_property_change():
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 250, 110), "foto")
    E.mark_image_field(pdf, 0, x)
    E.set_button_image(pdf, 0, x, _png((200, 30, 30)))
    assert E.is_image_field(pdf, x)
    E.update_properties(pdf, 0, x, tooltip="Foto dell'orologio")
    assert b"Do" in _ap(pdf, x, "N") and E.field_info(pdf, 0, x).image


def test_button_action_events():
    """FRM-18: l'azione si può legare a un evento diverso dal rilascio; gli script restano."""
    pdf = pymupdf.open()
    pdf.new_page()
    x = E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(50, 50, 150, 80), "b", caption="b")
    pdf.xref_set_key(x, "AA/Fo", "<</S/JavaScript/JS(app.alert(1))>>")
    E.set_button_action(pdf, 0, x, {"type": "print", "event": "E"})
    assert pdf.xref_get_key(x, "A")[0] == "null" and pdf.xref_get_key(x, "AA/E/S") == ("name", "/Named")
    assert E.button_action(pdf, x) == {"type": "print", "name": "Print", "event": "E"}
    assert E.button_action(pdf, x, "U") == {} and E.button_action(pdf, x, "E")["type"] == "print"
    E.set_button_action(pdf, 0, x, {"type": "reset"})
    assert E.button_action(pdf, x) == {"type": "reset", "fields": []}
    assert E.button_action(pdf, x, "E") == {}
    assert pdf.xref_get_key(x, "AA/Fo/S") == ("name", "/JavaScript")   # script non toccato
    with pytest.raises(E.FormError):
        E.set_button_action(pdf, 0, x, {"type": "reset", "event": "Z"})
