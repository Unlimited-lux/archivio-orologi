"""Test dei dialoghi di filigrane, numerazioni, intestazioni ed etichette."""

from __future__ import annotations

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
    dlg.fields["header_left"].setText("{file}")
    dlg.scope.set_mode("all")
    dlg.apply()
    assert "esempio.pdf" in text_of(view, 2)
    assert "Pagina 3 di 3" in text_of(view, 2)
    for e in dlg.fields.values():
        e.setText("")
    assert not dlg.ok_button.isEnabled()


def test_background_dialog(opened, accept):
    window, view = opened
    assert sd.open_background_dialog(window.ctx, view, [0, 1])
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {0: ["background"], 1: ["background"]}


def test_remove_dialog(opened, accept, qtbot):
    window, view = opened
    with view.document.change("x") as pdf:
        stamp.add_watermark(pdf, text="TOGLIMI")
        stamp.add_page_numbers(pdf)
    dlg = sd.RemoveStampsDialog(window, view.document, [])
    qtbot.addWidget(dlg)
    assert "(3)" in dlg.checks["watermark"].text()
    assert not dlg.checks["bates"].isEnabled()
    dlg.checks["numbers"].setChecked(False)
    assert dlg.apply() == 3
    with ENGINE_LOCK:
        assert stamp.list_watermarks(view.document.pdf) == {0: ["numbers"], 1: ["numbers"], 2: ["numbers"]}
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
    theme.apply(QApplication.instance(), "dark")
    try:
        for cls in (sd.WatermarkDialog, sd.BackgroundDialog, sd.PageNumbersDialog, sd.BatesDialog,
                    sd.HeaderFooterDialog, sd.PageLabelsDialog, sd.RemoveStampsDialog):
            dlg = cls(window, view.document, [0])
            qtbot.addWidget(dlg)
            dlg.show()
            assert not dlg.grab().isNull()
            dlg.close()
    finally:
        theme.apply(QApplication.instance(), "light")


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
    dlg.fmt.setEditText("{n}/{total}")
    dlg.skip_first.setChecked(True)
    assert dlg.preview_index() == 1
    opts = dlg.preview_options(dlg.engine_options(), 1)
    assert opts["fmt"] == "{n}/3" and opts["start"] == 2 and not opts["skip_first"]
    dlg.refresh_preview()
    assert "2" in dlg.preview_caption.text()
    assert not dlg.preview_label.pixmap().isNull()
