"""Compilazione di un modulo firmato: salvataggio incrementale e firma ancora valida (FRM-09)."""

from __future__ import annotations

import pymupdf
from PySide6.QtWidgets import QApplication

from foliant.modules import forms
from foliant.modules.forms import engine as E
from tests.test_document import _sign, _signed_pdf, _validate, test_signer  # noqa: F401 (fixture)

from .test_fill import click_field, open_form


def _signed_form(tmp_path, signer):
    """Modulo con un campo (widget e campo nello stesso oggetto, come nei moduli reali) poi firmato."""
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Modulo firmato", fontsize=14)
    x = d.get_new_xref()
    d.update_object(x, f"<</Type/Annot/Subtype/Widget/FT/Tx/T(Nome)/Rect[72 400 300 430]/F 4"
                       f"/DA(0 g /Helv 12 Tf)/P {d.page_xref(0)} 0 R>>")
    d.xref_set_key(d.page_xref(0), "Annots", f"[{x} 0 R]")
    font = d.get_new_xref()
    d.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    d.xref_set_key(d.pdf_catalog(), "AcroForm", f"<</Fields[{x} 0 R]/DA(0 g /Helv 0 Tf)/DR<</Font<</Helv {font} 0 R>>>>>>")
    path = tmp_path / "modulo_firmato.pdf"
    path.write_bytes(_sign(d.tobytes(), signer))
    return path


def _fill_and_save(qtbot, window, path, value):
    view = open_form(window, path)
    QApplication.processEvents()
    click_field(qtbot, view, "Nome")
    f = forms.instance().filler(view)
    qtbot.keyClicks(f.editor, value)
    f.commit_active()
    assert window.save_view(view)
    return path.read_bytes()


def test_fill_signed_form_saves_incrementally(qtbot, window, tmp_path, test_signer):  # noqa: F811
    from pyhanko.sign.diff_analysis import ModificationLevel
    path = _signed_form(tmp_path, test_signer)
    signed = path.read_bytes()
    out = _fill_and_save(qtbot, window, path, "Mario Rossi")
    assert out.startswith(signed) and len(out) > len(signed)  # originale = prefisso esatto
    st = _validate(out, test_signer)
    assert st.intact and st.valid and st.docmdp_ok
    assert st.modification_level == ModificationLevel.FORM_FILLING
    again = pymupdf.open(str(path))
    assert E.form_values(again)["Nome"] == "Mario Rossi" and "Mario Rossi" in again[0].get_text()


def test_fill_signed_form_with_kid_widget_keeps_signature_intact(qtbot, window, tmp_path, test_signer):  # noqa: F811
    """Widget separato dal campo: firma integra e valida; l'analisi delle differenze di pyHanko non
    riconosce però l'aggiornamento dell'aspetto sui widget figli (limite dello strumento di verifica)."""
    path = _signed_pdf(tmp_path, test_signer, objstm=False)
    signed = path.read_bytes()
    out = _fill_and_save(qtbot, window, path, "Anna")
    assert out.startswith(signed)
    st = _validate(out, test_signer)
    assert st.intact and st.valid


def _certified_form(tmp_path, signer, perm):
    """Modulo certificato con pyHanko (firma di certificazione con /DocMDP al livello `perm`)."""
    import io

    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    from .conftest import build_form
    out = io.BytesIO()
    signers.sign_pdf(IncrementalPdfFileWriter(io.BytesIO(build_form().tobytes())),
                     signers.PdfSignatureMetadata(field_name="Certificazione", certify=True,
                                                  docmdp_permissions=perm),
                     signer=signer[0], output=out)
    path = tmp_path / f"certificato_{perm.name}.pdf"
    path.write_bytes(out.getvalue())
    return path


def test_certification_info_levels(tmp_path, test_signer):  # noqa: F811
    from pyhanko.sign.fields import MDPPerm
    for perm, level in ((MDPPerm.NO_CHANGES, 1), (MDPPerm.FILL_FORMS, 2), (MDPPerm.ANNOTATE, 3)):
        d = pymupdf.open(str(_certified_form(tmp_path, test_signer, perm)))
        info = E.certification_info(d)
        assert info["level"] == level and info["field"] == "Certificazione"
        assert info["signer"] == "Firmatario di prova"
        assert (E.fill_blocked_reason(d) is not None) == (level == 1)
    assert E.certification_info(pymupdf.open()) is None


def test_certified_no_changes_is_read_only_with_signer(qtbot, window, tmp_path, test_signer):  # noqa: F811
    """FRM-01: documento certificato senza modifiche consentite → sola lettura, con riferimento
    alla firma di certificazione; nessuna richiesta di password del proprietario."""
    from pyhanko.sign.fields import MDPPerm
    view = open_form(window, _certified_form(tmp_path, test_signer, MDPPerm.NO_CHANGES))
    f = forms.instance().filler(view)
    f.state.refresh(force=True)
    f.update_bar()
    assert "Firmatario di prova" in f.text_of_bar() and "Certificazione" in f.text_of_bar()
    assert "Inserisci password del proprietario" not in f.banner_actions
    assert "Modifica modulo" not in f.banner_actions
    click_field(qtbot, view, "nome")
    assert f.editor is None
    assert E.form_values(view.document.pdf)["nome"] == ""


def test_certified_fill_forms_allows_filling(qtbot, window, tmp_path, test_signer):  # noqa: F811
    from pyhanko.sign.fields import MDPPerm
    view = open_form(window, _certified_form(tmp_path, test_signer, MDPPerm.FILL_FORMS))
    f = forms.instance().filler(view)
    click_field(qtbot, view, "nome")
    assert f.editor is not None
    qtbot.keyClicks(f.editor, "Anna")
    f.commit_active()
    assert E.form_values(view.document.pdf)["nome"] == "Anna"


def _lock_form(tmp_path, signer=None):
    """Campi A, B, C e un campo firma che dopo la firma blocca solo A e B."""
    import io
    d = pymupdf.open()
    d.new_page()
    for i, n in enumerate("ABC"):
        E.create_field(d, 0, E.TEXT, pymupdf.Rect(72, 80 + 30 * i, 272, 100 + 30 * i), n)
    E.create_field(d, 0, E.SIGNATURE, pymupdf.Rect(300, 80, 500, 130), "Firma",
                   lock={"action": "Include", "fields": ["A", "B"]})
    data = d.tobytes()
    if signer is not None:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers
        out = io.BytesIO()
        signers.sign_pdf(IncrementalPdfFileWriter(io.BytesIO(data)), signers.PdfSignatureMetadata(field_name="Firma"),
                         signer=signer[0], output=out)
        data = out.getvalue()
    path = tmp_path / ("bloccato_firmato.pdf" if signer else "bloccato.pdf")
    path.write_bytes(data)
    return path


def test_signature_lock_makes_only_listed_fields_read_only(qtbot, window, tmp_path, test_signer):  # noqa: F811
    """FRM-20: firma con blocco dei soli A e B → A e B in sola lettura, C compilabile."""
    view = open_form(window, _lock_form(tmp_path, test_signer))
    f = forms.instance().filler(view)
    f.state.refresh(force=True)
    assert f.state.signature_locked == {"A", "B"}
    click_field(qtbot, view, "A")
    assert f.editor is None
    click_field(qtbot, view, "C")
    assert f.editor is not None
    qtbot.keyClicks(f.editor, "ok")
    f.commit_active()
    assert E.form_values(view.document.pdf)["C"] == "ok"
    assert {x.name for x in f.state.fields if x.readonly} == {"A", "B"}


def test_signature_lock_follows_renamed_field(tmp_path):
    d = pymupdf.open(str(_lock_form(tmp_path)))
    a = [f for f in E.list_fields(d) if f.name == "A"][0]
    E.update_properties(d, a.page, a.xref, name="cliente.cognome")
    sig = [f for f in E.list_fields(d) if f.kind == E.SIGNATURE][0]
    assert sig.lock == {"action": "Include", "fields": ["cliente.cognome", "B"]}


def test_signature_sv_constraints_round_trip(tmp_path):
    """Vincoli per chi firma (/SV): motivi ammessi vincolanti, marca temporale e certificazione."""
    d = pymupdf.open()
    d.new_page()
    x = E.create_field(d, 0, E.SIGNATURE, pymupdf.Rect(300, 80, 500, 130), "Firma")
    sv = {"reasons": ["Venditore", "Acquirente"], "timestamp": "https://tsa.esempio.test", "certify": 2}
    E.update_properties(d, 0, x, sv=sv)
    again = pymupdf.open("pdf", d.tobytes())
    f = E.list_fields(again)[0]
    assert f.sv == sv and E.field_info(again, f.page, f.xref).sv == sv
    raw = again.xref_object(f.field_xref, compressed=True)
    assert "/Type/SV" in raw.replace(" ", "") and "/Ff 8" in raw
    E.update_properties(again, f.page, f.xref, sv={})
    assert again.xref_get_key(f.field_xref, "SV")[0] == "null"
    dup = E.duplicate_field(d, 0, x, 0, 80)
    assert E.field_info(d, 0, dup).sv == sv and E.field_info(d, 0, dup).name != "Firma"
