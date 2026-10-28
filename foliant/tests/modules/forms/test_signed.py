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
