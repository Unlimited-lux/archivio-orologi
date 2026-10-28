"""Test dei dialoghi di filigrane, numerazioni, intestazioni ed etichette."""

from __future__ import annotations

import json

from .conftest import fast_theme
import pymupdf
import pytest
from PySide6.QtWidgets import QDialog

from foliant.api import ENGINE_LOCK
from foliant.modules.pages import stamp
from foliant.modules.pages import stamp_dialogs as sd


@pytest.fixture
def accept(monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)


@pytest.fixture
def opened(window, sample_pdf):
    view = window.open_path(sample_pdf)
    assert view is not None
    return window, view


def text_of(view, i=0) -> str:
    return view.document.page_text(i)


def png_file(tmp_path) -> str:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), 0)
    pix.clear_with(0)
    path = tmp_path / "logo.png"
    pix.save(str(path))
    return str(path)


def test_watermark_dialog_applies_and_undo(opened, accept):
    window, view = opened
    original = text_of(view, 0)
    assert sd.open_watermark_dialog(window.ctx, view, [0]) is True
    assert "BOZZA" in text_of(view, 0)
    assert "BOZZA" in text_of(view, 2)  # una sola pagina selezionata: predefinito «Tutte»
    assert view.document.is_modified
    assert view.document.undo()
    assert text_of(view, 0) == original


def test_watermark_dialog_rejected(opened, monkeypatch):
    window, view = opened
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert sd.open_watermark_dialog(window.ctx, view, [0]) is False
    assert not view.document.is_modified


def test_watermark_dialog_widgets_and_options(opened, qtbot):
    window, view = opened
    dlg = sd.WatermarkDialog(window, view.document, [1])
    qtbot.addWidget(dlg)
    dlg.text.setText("COPIA")
    dlg.opacity.set_value(0.6)
    dlg.rotation.setValue(0)
    dlg.position.set_value("top-right")
    dlg.color.set_color((0, 0, 1))
    dlg.scope.set_mode("all")
    opts = dlg.options()
    assert opts["text"] == "COPIA" and opts["opacity"] == pytest.approx(0.6)
    assert opts["position"] == "top-right" and opts["pages"] is None
    assert dlg.ok_button.isEnabled()
    pages = dlg.apply()
    assert pages == [0, 1, 2]
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {0: ["watermark"], 1: ["watermark"], 2: ["watermark"]}
    # empty text disables OK with inline error
    dlg.text.setText("")
    assert not dlg.ok_button.isEnabled()
    assert dlg.error.isVisibleTo(dlg)


def test_watermark_dialog_image(opened, qtbot, tmp_path):
    window, view = opened
    dlg = sd.WatermarkDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.kind_image.setChecked(True)
    assert not dlg.ok_button.isEnabled()
    dlg.image.set_path(png_file(tmp_path))
    assert dlg.ok_button.isEnabled()
    dlg.refresh_preview()
    assert not dlg.preview_label.pixmap().isNull()
    assert dlg.apply() == [0, 1, 2]


def test_range_validation_disables_ok(opened, qtbot):
    window, view = opened
    dlg = sd.PageNumbersDialog(window, view.document, [0])
    qtbot.addWidget(dlg)
    dlg.scope.edit.setText("1-99")
    assert dlg.scope.rng.isChecked()
    assert not dlg.ok_button.isEnabled()
    assert dlg.scope.error.isVisibleTo(dlg) and dlg.scope.error.text()
    dlg.scope.edit.setText("2-3")
    assert dlg.ok_button.isEnabled()
    assert not dlg.scope.error.isVisibleTo(dlg)
    assert dlg.options()["pages"] == [1, 2]


def test_preview_shows_stamp(opened, qtbot):
    window, view = opened
    dlg = sd.WatermarkDialog(window, view.document, [0])
    qtbot.addWidget(dlg)
    dlg.refresh_preview()
    with_stamp = dlg.preview_label.pixmap().toImage()
    dlg.opacity.set_value(1.0)
    dlg.refresh_preview()
    assert dlg.preview_label.pixmap().toImage() != with_stamp
    assert not view.document.is_modified  # l'anteprima usa una copia


def test_page_numbers_dialog(opened, accept, monkeypatch):
    window, view = opened
    orig = sd.PageNumbersDialog.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.fmt.setEditText("Pagina {n} di {total}")
        self.scope.set_mode("all")

    monkeypatch.setattr(sd.PageNumbersDialog, "__init__", init)
    assert sd.open_page_numbers_dialog(window.ctx, view, [])
    with ENGINE_LOCK:
        for i in range(3):
            assert view.document.pdf[i].search_for(f"Pagina {i + 1} di 3")


def test_bates_dialog(opened, qtbot):
    window, view = opened
    dlg = sd.BatesDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.prefix.setText("DOC-")
    dlg.digits.setValue(4)
    assert "DOC-0001" in dlg.example.text() and "DOC-0003" in dlg.example.text()
    assert dlg.apply() == "DOC-0003"
    dlg.start.setValue(9998)
    assert not dlg.ok_button.isEnabled()  # 4 cifre non bastano


def test_header_footer_dialog(opened, qtbot):
    window, view = opened
    dlg = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.fields["header_left"].setPlainText("{file}")
    dlg.scope.set_mode("all")
    dlg.apply()
    assert "esempio" in text_of(view, 2)
    assert "Pagina 3 di 3" in text_of(view, 2)
    for e in dlg.fields.values():
        e.setPlainText("")
    assert not dlg.ok_button.isEnabled()
    dlg.fields["footer_center"].setPlainText("{sconosciuto}")
    assert not dlg.ok_button.isEnabled()
    assert "sconosciuto" in dlg.error.text()


def test_background_dialog(opened, accept):
    window, view = opened
    assert sd.open_background_dialog(window.ctx, view, [0, 1])
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {0: ["background"], 1: ["background"]}


def test_remove_dialog(opened, accept, qtbot):
    window, view = opened
    with view.document.change("x") as pdf:
        stamp.add_watermark(pdf, text="TOGLIMI")
        stamp.add_watermark(pdf, [1], text="ANCHE")
        stamp.add_page_numbers(pdf)
    dlg = sd.RemoveStampsDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    labels = [cb.text() for _s, cb in dlg.items]
    assert len(labels) == 3  # elencate singolarmente
    assert any("TOGLIMI" in t and "1-3" in t for t in labels) and any("ANCHE" in t for t in labels)
    dlg.select_kind("numbers", False)
    next(cb for s, cb in dlg.items if s["params"].get("text") == "ANCHE").setChecked(False)
    assert dlg.apply() == 3
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {0: ["numbers"], 1: ["watermark", "numbers"],
                                                            2: ["numbers"]}
    assert sd.open_remove_stamps_dialog(window.ctx, view, [])
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {}


def test_remove_dialog_nothing_to_remove(opened, accept, monkeypatch):
    window, view = opened
    shown = []
    monkeypatch.setattr(window.ctx, "info", lambda t, m: shown.append(m))
    assert sd.open_remove_stamps_dialog(window.ctx, view, []) is False
    assert shown


def test_page_labels_dialog(opened, qtbot, accept):
    window, view = opened
    dlg = sd.PageLabelsDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.table.cellWidget(0, 1).setCurrentIndex(dlg.table.cellWidget(0, 1).findData("r"))
    dlg.add_rule()
    assert dlg.table.cellWidget(1, 0).value() == 2
    dlg.table.cellWidget(1, 2).setText("P-")
    assert "i, P-1, P-2" in dlg.preview.text()
    dlg.apply()
    with ENGINE_LOCK:
        assert [view.document.pdf[i].get_label() for i in range(3)] == ["i", "P-1", "P-2"]
    # regola duplicata: OK disattivato
    dlg.table.cellWidget(1, 0).setValue(1)
    assert not dlg.ok_button.isEnabled()
    assert dlg.error.text()
    dlg.clear_rules()
    assert dlg.ok_button.isEnabled()
    dlg.apply()
    with ENGINE_LOCK:
        assert stamp.get_page_labels(view.document.pdf) == []


def test_engine_error_shown_not_raised(opened, accept, monkeypatch):
    window, view = opened
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))

    def boom(*a, **k):
        raise stamp.PageOpError("errore di prova")

    monkeypatch.setattr(stamp, "add_watermark", boom)
    assert sd.open_watermark_dialog(window.ctx, view, [0]) is False
    assert errors == ["errore di prova"]
    assert not view.document.is_modified


@pytest.mark.timeout(900)   # con tutti i moduli il cambio di tema ristilizza molte finestre
def test_dark_theme_construction(opened, qtbot):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    window, view = opened
    fast_theme("dark")
    try:
        for cls in (sd.WatermarkDialog, sd.BackgroundDialog, sd.PageNumbersDialog, sd.BatesDialog,
                    sd.HeaderFooterDialog, sd.PageLabelsDialog, sd.RemoveStampsDialog):
            dlg = cls(window, view.document, [0])
            qtbot.addWidget(dlg)
            dlg.show()
            assert not dlg.grab().isNull()
            dlg.close()
    finally:
        fast_theme("light")


def test_translations_cover_ui_strings():
    import re
    from pathlib import Path
    for mod in (stamp, sd):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        strings = set(re.findall(r'_\("((?:[^"\\]|\\.)*)"\)', src))
        missing = sorted(s for s in strings if s not in mod.TRANSLATIONS)
        assert not missing, (mod.__name__, missing)


def test_page_numbers_preview_skip_first(opened, qtbot):
    window, view = opened
    dlg = sd.PageNumbersDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.fmt.setEditText("{pagina}/{totale}")
    dlg.skip_first.setChecked(True)
    assert dlg.preview_index() == 1
    opts = dlg.preview_options(dlg.engine_options(), 1)
    assert opts["field_values"]["totale"] == "3" and opts["start"] == 2 and not opts["skip_first"]
    dlg.refresh_preview()
    assert "2" in dlg.preview_caption.text()
    assert not dlg.preview_label.pixmap().isNull()


# ------------------------------------------------------------------ PAG-32/33/34/36/38

def letterhead_file(tmp_path, pages=2) -> str:
    d = pymupdf.open()
    for i, col in enumerate([(1, 0, 0), (0, 0, 1)][:pages]):
        p = d.new_page(width=595, height=842)
        p.draw_rect(pymupdf.Rect(0, 0, 595, 80), color=None, fill=col)
        p.insert_text((40, 60), f"CARTA {i + 1}", fontsize=20, color=(1, 1, 1))
    path = tmp_path / "carta.pdf"
    d.save(str(path))
    return str(path)


def test_unknown_placeholder_blocks_confirm(opened, qtbot):
    window, view = opened
    for cls, setter in ((sd.WatermarkDialog, lambda d: d.text.setText("{sconosciuto}")),
                        (sd.PageNumbersDialog, lambda d: d.fmt.setEditText("Pag. {sconosciuto}"))):
        dlg = cls(window, view.document, [])
        qtbot.addWidget(dlg)
        setter(dlg)
        assert not dlg.ok_button.isEnabled()
        assert "{sconosciuto}" in dlg.error.text()


def test_field_menu_inserts_placeholder(opened, qtbot):
    window, view = opened
    dlg = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    e = dlg.fields["header_center"]
    e.setPlainText("Rif. ")
    e.moveCursor(e.textCursor().MoveOperation.End)
    dlg.field_button.insert("{titolo}")
    assert e.toPlainText() == "Rif. {titolo}"
    assert dlg.variants["default"]["header_center"] == "Rif. {titolo}"


def test_background_letterhead_dialog(opened, qtbot, tmp_path):
    window, view = opened
    dlg = sd.BackgroundDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.kind_pdf.setChecked(True)
    assert not dlg.ok_button.isEnabled()
    dlg.source_pdf.set_path(letterhead_file(tmp_path))
    assert dlg.first_rest.isEnabled()
    dlg.first_rest.setChecked(True)
    dlg.scope.set_mode("all")
    dlg.visibility.set_value("screen")
    assert dlg.ok_button.isEnabled()
    dlg.refresh_preview()
    assert not dlg.preview_label.pixmap().isNull()
    dlg.apply()
    with ENGINE_LOCK:
        pdf = view.document.pdf
        assert "CARTA 1" in pdf[0].get_text() and "CARTA 2" in pdf[2].get_text()
        params = stamp.stamp_params(pdf, "background")
    assert params["first_rest"] and params["visibility"] == "screen"


def test_watermark_tile_offset_visibility_and_modify(opened, qtbot):
    window, view = opened
    dlg = sd.WatermarkDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.text.setText("RISERVATO")
    dlg.tile.setChecked(True)
    dlg.visibility.set_value("print")
    dlg.offset_x.setValue(10)
    opts = dlg.engine_options()
    assert opts["tile"] and opts["visibility"] == "print" and opts["offset"][0] == pytest.approx(28.35, abs=0.01)
    dlg.apply()
    # «Modifica»: il dialogo riaperto elenca la filigrana e ne ricarica i parametri
    dlg2 = sd.WatermarkDialog(window, view.document, [])
    qtbot.addWidget(dlg2)
    assert dlg2.edit_combo.count() == 2
    dlg2.edit_combo.setCurrentIndex(1)
    assert dlg2.text.text() == "RISERVATO" and dlg2.tile.isChecked()
    assert dlg2.visibility.value() == "print"
    dlg2.text.setText("COPIA")
    dlg2.apply()
    with ENGINE_LOCK:
        stamps = stamp.list_stamps(view.document.pdf, "watermark")
    assert [s["params"]["text"] for s in stamps] == ["COPIA"]


def test_watermark_from_pdf_dialog(opened, qtbot, tmp_path):
    window, view = opened
    dlg = sd.WatermarkDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.kind_pdf.setChecked(True)
    assert not dlg.ok_button.isEnabled()
    dlg.source_pdf.set_path(letterhead_file(tmp_path), 1)
    dlg.rotation.setValue(0)
    assert dlg.ok_button.isEnabled()
    dlg.apply()
    assert "CARTA 2" in text_of(view, 0)


def test_watermark_pdfa1_warning(window, qtbot, tmp_path):
    d = pymupdf.open()
    d.new_page()
    d.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/'
                       '02/22-rdf-syntax-ns#"><rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" '
                       'pdfaid:part="1" pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>')
    path = tmp_path / "pdfa.pdf"
    d.save(str(path))
    view = window.open_path(str(path))
    dlg = sd.WatermarkDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    assert "PDF/A-1" in dlg.warning.text() and dlg.warning.isVisibleTo(dlg)
    assert dlg.ok_button.isEnabled()  # avviso, non blocco
    dlg.opacity.set_value(1.0)
    assert not dlg.warning.isVisibleTo(dlg)


def test_page_numbers_dialog_start_from_and_remove(opened, qtbot, accept, tmp_path):
    window, view = opened
    dlg = sd.PageNumbersDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.start_from.setValue(2)
    dlg.mirror.setCurrentIndex(dlg.mirror.findData("outer"))
    dlg.apply()
    with ENGINE_LOCK:
        pdf = view.document.pdf
        foot = lambda i: [w[4] for w in pdf[i].get_text("words") if w[1] > 780]  # noqa: E731
        assert foot(0) == [] and foot(1) == ["1"] and foot(2) == ["2"]
    # riaperto: propone Modifica (parametri ricaricati) e Rimuovi
    dlg2 = sd.PageNumbersDialog(window, view.document, [])
    qtbot.addWidget(dlg2)
    assert dlg2.start_from.value() == 2 and dlg2.mirror.currentData() == "outer"
    dlg2.exclude.setText("99")
    assert not dlg2.ok_button.isEnabled()
    dlg2.exclude.setText("")
    dlg2.request_remove()
    assert dlg2.apply() == 2
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {}


def test_header_footer_first_page_even_logo_and_templates(opened, qtbot, tmp_path):
    window, view = opened
    logo = tmp_path / "logo.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), 0)
    pix.clear_with(80)
    pix.save(str(logo))
    dlg = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.set_texts(header_center="Orologi Verdi S.r.l.\nVia Roma 1", footer_right="{pagina}/{totale}")
    dlg.set_texts("first", footer_right="{pagina}/{totale}")  # prima pagina: niente intestazione
    dlg.set_texts("even", header_left="Pari", footer_left="{pagina}/{totale}")
    dlg.logo.set_path(str(logo))
    dlg.opacity.set_value(0.8)
    dlg.underline.setChecked(True)
    dlg.scope.set_mode("all")
    assert dlg.ok_button.isEnabled(), dlg.error.text()
    dlg.save_template("Carta Verdi")
    assert dlg.template_names() == ["Carta Verdi"]
    dlg.apply()
    with ENGINE_LOCK:
        pdf = view.document.pdf
        assert "Orologi Verdi" not in pdf[0].get_text() and "1/3" in pdf[0].get_text()
        assert "Via Roma 1" in pdf[2].get_text() and "Pari" in pdf[1].get_text()
        assert "Pari" not in pdf[2].get_text()
    # il modello è nelle impostazioni dell'applicazione e si applica a un altro documento
    raw = window.ctx.settings().value(sd.TEMPLATES_KEY)
    assert "Carta Verdi" in raw
    # riservatezza: il logo è incorporato nel modello, senza percorso del file
    assert str(tmp_path) not in raw and "logo.png" not in raw and '"logo_data"' in raw
    with ENGINE_LOCK:   # e nemmeno nel record del timbro dentro il PDF
        recs = stamp.list_stamps(view.document.pdf, stamp.HEADERFOOTER)
    assert recs and recs[0]["params"]["logo"] is None and recs[0]["params"]["has_logo"]
    assert str(tmp_path) not in json.dumps(recs)
    d = pymupdf.open()
    for _i in range(3):
        d.new_page(width=595, height=842).insert_text((72, 100), "Altro documento", fontsize=14)
    d.save(str(tmp_path / "altro.pdf"))
    other = window.open_path(str(tmp_path / "altro.pdf"))
    dlg3 = sd.HeaderFooterDialog(window, other.document, [], ctx=window.ctx)
    qtbot.addWidget(dlg3)
    assert dlg3.load_template("Carta Verdi")
    assert dlg3.logo.path() == "" and dlg3.logo.data() == logo.read_bytes() and dlg3.underline.isChecked()
    dlg3.scope.set_mode("all")
    dlg3.apply()
    with ENGINE_LOCK:
        a = view.document.pdf[2].search_for("Orologi Verdi S.r.l.")
        b = other.document.pdf[2].search_for("Orologi Verdi S.r.l.")
    assert a == b
    dlg3.delete_template("Carta Verdi")
    assert dlg3.template_names() == []


def test_header_footer_modify_reopens_params(opened, qtbot):
    window, view = opened
    dlg = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.set_texts(header_center="Relazione")
    dlg.scope.set_mode("all")
    dlg.apply()
    dlg2 = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg2)
    assert dlg2.fields["header_center"].toPlainText() == "Relazione"
    assert dlg2.replace_box.isChecked() and dlg2.replace_box.isVisibleTo(dlg2)
    dlg2.fields["header_center"].setPlainText("Bozza")
    dlg2.scope.set_mode("all")
    dlg2.apply()
    for i in range(3):
        t = text_of(view, i)
        assert t.count("Bozza") == 1 and "Relazione" not in t


def test_header_footer_preview_page_navigation(opened, qtbot):
    window, view = opened
    dlg = sd.HeaderFooterDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    dlg.preview_page.setValue(3)
    assert dlg.preview_index() == 2
    opts = dlg.preview_options(dlg.engine_options(), 2)
    assert opts["field_values"]["pagina"] == 3 and opts["field_values"]["totale"] == 3
    dlg.refresh_preview()
    assert "3" in dlg.preview_caption.text()


def test_old_templates_lose_logo_path_once(window, tmp_path):
    """Pulizia all'avvio: i modelli delle versioni precedenti perdono il percorso del logo."""
    from foliant.modules.pages import privacy
    settings = window.ctx.settings()
    secret = str(tmp_path / "Riservato" / "logo-studio-rossi.png")
    settings.setValue(sd.TEMPLATES_KEY, json.dumps({"Carta": {"logo": secret, "margin": 20, "underline": True}}))
    assert privacy.cleanup_legacy(settings)
    data = json.loads(settings.value(sd.TEMPLATES_KEY))
    assert data == {"Carta": {"logo": None, "margin": 20, "underline": True}}
    assert not privacy.cleanup_legacy(settings)        # niente più da togliere: nessuna scrittura
    settings.setValue(sd.TEMPLATES_KEY, "non è json")
    assert privacy.cleanup_legacy(settings)
    assert not settings.contains(sd.TEMPLATES_KEY)


def test_image_pickers_use_core_dialog(window, qtbot, tmp_path, monkeypatch):
    """Senza contesto esplicito i selettori di file usano comunque il dialogo del nucleo."""
    img = tmp_path / "timbro.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), 0)
    pix.save(str(img))
    asked = []
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: asked.append(a) or [str(img)])
    chooser = sd.ImageChooser(None, window)
    qtbot.addWidget(chooser)
    chooser.button.click()
    assert asked and chooser.path() == str(img)
    pdf_chooser = sd.PdfPageChooser(None, window)
    qtbot.addWidget(pdf_chooser)
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [letterhead_file(tmp_path)])
    pdf_chooser.button.click()
    assert pdf_chooser.pages == 2
