"""Test dei dialoghi PAG-08 / PAG-09 / PAG-13 (pytest-qt)."""

from __future__ import annotations

import os

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QDialog

from foliant.modules.pages import images
from foliant.modules.pages import images_dialogs as dlgs
from foliant.modules.pages.engine import MM

SHOTS = "/tmp/claude-0/shots"


# ------------------------------------------------------------------ helper
def _jpeg(path, size=(300, 200), color=(255, 0, 0)):
    Image.new("RGB", size, color).save(path, "JPEG", dpi=(72, 72))
    return str(path)


def _accept_with(monkeypatch, configure=None):
    """QDialog.exec sostituito: applica `configure(dlg)` e accetta."""
    seen = {}

    def fake_exec(self):
        # altri dialoghi (es. «modifiche non salvate» alla chiusura della finestra): annulla
        if not isinstance(self, (dlgs.InsertImagesDialog, dlgs.PasteDialog, dlgs.ExportImagesDialog)):
            return QDialog.DialogCode.Rejected
        seen["dialog"] = self
        if configure:
            configure(self)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    return seen


def _silence(monkeypatch, ctx):
    msgs = {"error": [], "info": []}
    monkeypatch.setattr(ctx, "error", lambda t, m: msgs["error"].append(m))
    monkeypatch.setattr(ctx, "info", lambda t, m: msgs["info"].append(m))
    monkeypatch.setattr(ctx, "confirm", lambda t, m: True)
    return msgs


def _set_clipboard(mime: QMimeData):
    QGuiApplication.clipboard().setMimeData(mime)


# ------------------------------------------------------------------ PAG-08
def test_insert_images_dialog_applies_and_undo(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    a = _jpeg(tmp_path / "a.jpg")
    b = _jpeg(tmp_path / "b.jpg", color=(0, 0, 255))
    monkeypatch.setattr(ctx, "ask_open_paths", lambda *a_, **k: [a, b])

    def configure(dlg):
        dlg.list.setCurrentRow(1)
        dlg.move_selected(-1)                      # b prima di a
        dlg.size_combo.setCurrentIndex(dlg.size_combo.findData("A4"))
        dlg.margin_spin.setValue(10)
        dlg.position_combo.setCurrentIndex(dlg.position_combo.findData(dlgs.POS_AFTER))
        opts = dlg.options()
        assert [os.path.basename(p) for p in opts["paths"]] == ["b.jpg", "a.jpg"]
        assert abs(opts["margin"] - 10 * MM) < 1e-6
    _accept_with(monkeypatch, configure)
    assert dlgs.open_insert_images_dialog(ctx, view, [0]) is True
    assert view.document.page_count == 5
    pdf = view.document.pdf
    assert pdf[1].get_images() and pdf[2].get_images()
    assert abs(pdf[1].rect.width - 841.89) < 0.1           # A4 orizzontale automatico
    px = pdf[1].get_pixmap().pixel(420, 297)
    assert px[2] > 200 and px[0] < 60                        # b (blu) per prima
    assert not msgs["error"]
    view.document.undo()
    assert view.document.page_count == 3


def test_insert_images_dialog_reports_skipped(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    bad = tmp_path / "rotta.png"
    bad.write_bytes(b"niente")
    good = _jpeg(tmp_path / "ok.jpg")
    _accept_with(monkeypatch)
    assert dlgs.open_insert_images_dialog(ctx, view, [], paths=[str(bad), good], at=3)
    assert view.document.page_count == 4 and view.document.pdf[3].get_images()
    assert msgs["info"] and "rotta.png" in msgs["info"][0]
    # tutte rotte → errore, documento invariato e nessun passo di annulla
    assert not dlgs.open_insert_images_dialog(ctx, view, [], paths=[str(bad)])
    assert msgs["error"] and view.document.page_count == 4


def test_insert_images_dialog_cancel(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not dlgs.open_insert_images_dialog(window.ctx, view, [0], paths=[_jpeg(tmp_path / "x.jpg")])
    assert view.document.page_count == 3
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [])
    assert not dlgs.open_insert_images_dialog(window.ctx, view, [0])


def test_insertion_index():
    assert dlgs.insertion_index(dlgs.POS_AFTER, [1, 3], 5) == 4
    assert dlgs.insertion_index(dlgs.POS_BEFORE, [3, 1], 5) == 1
    assert dlgs.insertion_index(dlgs.POS_START, [3], 5) == 0
    assert dlgs.insertion_index(dlgs.POS_END, [3], 5) == 5
    assert dlgs.insertion_index(dlgs.POS_AFTER, [], 5, current=2) == 3


def test_insert_dialog_state(qtbot, tmp_path):
    d = dlgs.InsertImagesDialog([])
    qtbot.addWidget(d)
    assert not d.ok_button.isEnabled()
    assert not d.fit_combo.isEnabled()
    d.add_paths([_jpeg(tmp_path / "x.jpg")])
    assert d.ok_button.isEnabled()
    d.size_combo.setCurrentIndex(1)
    assert d.fit_combo.isEnabled()


# ------------------------------------------------------------------ PAG-09
def test_paste_pdf_pages_after_page_1(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)
    a = pymupdf.open()
    for k in range(3):
        a.new_page().insert_text((72, 100), f"Sorgente A {k + 1}", fontsize=20)
    sub = pymupdf.open()
    sub.insert_pdf(a, from_page=0, to_page=1)
    mime = QMimeData()
    mime.setData("application/pdf", sub.tobytes())
    mime.setText("testo che non va usato")
    _set_clipboard(mime)
    assert dlgs.clipboard_has_supported_content()

    def configure(dlg):
        assert dlg.kind == images.CLIP_PDF
        assert dlg.page_count() == 2
        assert "2" in dlg.preview_label.text()
        # appunti cambiati durante il dialogo: si usa l'istantanea
        _set_clipboard(QMimeData())
    _accept_with(monkeypatch, configure)
    assert dlgs.open_paste_dialog(ctx, view, [0])
    pdf = view.document.pdf
    assert view.document.page_count == 5
    for k in (0, 1):
        assert pdf[1 + k].get_pixmap().samples == a[k].get_pixmap().samples
    view.document.undo()
    assert view.document.page_count == 3


def test_paste_screenshot_as_image(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)
    img = QImage(320, 200, QImage.Format.Format_RGB32)
    img.fill(0x00FF00)
    mime = QMimeData()
    mime.setImageData(img)
    _set_clipboard(mime)

    def configure(dlg):
        assert dlg.kind == images.CLIP_IMAGE
        assert dlg.size_combo.currentData() is None          # "Come l'immagine"
        assert dlg.page_count() == 1
        dlg.position_combo.setCurrentIndex(dlg.position_combo.findData(dlgs.POS_END))
    _accept_with(monkeypatch, configure)
    assert dlgs.open_paste_dialog(ctx, view, [0])
    page = view.document.pdf[3]
    bbox = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    assert abs(bbox.width - page.rect.width) < 1 and abs(bbox.height - page.rect.height) < 1


def test_paste_long_text_multiple_pages(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)
    text = "\n".join(f"Riga {i} del testo incollato, con accenti: è à ù" for i in range(200))
    mime = QMimeData()
    mime.setText(text)
    _set_clipboard(mime)
    pages = {}

    def configure(dlg):
        assert dlg.kind == images.CLIP_TEXT
        pages["n"] = dlg.page_count()
        assert pages["n"] > 1 and str(pages["n"]) in dlg.preview_label.text()
        dlg.position_combo.setCurrentIndex(dlg.position_combo.findData(dlgs.POS_START))
    _accept_with(monkeypatch, configure)
    assert dlgs.open_paste_dialog(ctx, view, [])
    assert view.document.page_count == 3 + pages["n"]
    got = "".join(view.document.pdf[k].get_text() for k in range(pages["n"]))
    assert " ".join(got.split()) == " ".join(text.split())


def test_paste_uri_list(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(_jpeg(tmp_path / "f.jpg")), QUrl.fromLocalFile(sample_pdf)])
    _set_clipboard(mime)
    _accept_with(monkeypatch, lambda d: d.kind == images.CLIP_URIS or pytest.fail("tipo errato"))
    assert dlgs.open_paste_dialog(ctx, view, [2])
    assert view.document.page_count == 3 + 1 + 3


def test_paste_empty_clipboard(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    msgs = _silence(monkeypatch, window.ctx)
    _set_clipboard(QMimeData())
    assert not dlgs.clipboard_has_supported_content()
    assert not dlgs.open_paste_dialog(window.ctx, view, [0])
    assert msgs["error"]


# ------------------------------------------------------------------ PAG-13
def test_export_dialog_runs_task(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    msgs = _silence(monkeypatch, ctx)
    out = tmp_path / "out"
    monkeypatch.setattr(ctx, "ask_directory", lambda *a, **k: str(out))
    calls = []
    real = ctx.run_task

    def spy(title, fn, *args, **kw):
        calls.append(title)
        return real(title, fn, *args, **kw)
    monkeypatch.setattr(ctx, "run_task", spy)

    def configure(dlg):
        dlg.folder_edit.setText("")
        dlg._browse()                                     # usa ctx.ask_directory
        assert dlg.folder_edit.text() == str(out)
        dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("jpeg"))
        assert not dlg.transparent_check.isEnabled()
        assert dlg.quality_spin.isEnabled()
        dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("png"))
        assert dlg.transparent_check.isEnabled() and not dlg.quality_spin.isEnabled()
        dlg.dpi_spin.setValue(150)
    _accept_with(monkeypatch, configure)
    assert dlgs.open_export_images_dialog(ctx, view, [0, 2])
    assert calls
    files = sorted(out.iterdir())
    assert [f.name for f in files] == ["esempio_p1.png", "esempio_p3.png"]
    with Image.open(files[0]) as im:
        assert abs(im.width - 1240) <= 1 and abs(im.height - 1754) <= 1
        assert round(im.info["dpi"][0]) == 150
    assert msgs["info"] and "2" in msgs["info"][0]
    assert view.document.page_count == 3 and not view.document.can_undo


def test_export_dialog_multipage_tiff_all_pages(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)

    def configure(dlg):
        dlg.pages_combo.setCurrentIndex(dlg.pages_combo.findData("all"))
        dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("tiff"))
        dlg.multipage_check.setChecked(True)
        dlg.dpi_spin.setValue(30)
        dlg.folder_edit.setText(str(tmp_path))
        o = dlg.options()
        assert o["indices"] == [0, 1, 2] and o["multipage_tiff"]
    _accept_with(monkeypatch, configure)
    assert dlgs.open_export_images_dialog(ctx, view, [])
    tifs = list(tmp_path.glob("*.tif"))
    assert len(tifs) == 1
    with Image.open(tifs[0]) as im:
        assert im.n_frames == 3


def test_export_dialog_resolution_too_high_uses_suggestion(window, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    ctx = window.ctx
    _silence(monkeypatch, ctx)
    monkeypatch.setattr(images, "DEFAULT_MAX_PIXELS", 1_000_000)
    real = images.export_page_images

    def limited(*a, **k):
        k["max_pixels"] = 1_000_000
        return real(*a, **k)
    monkeypatch.setattr(images, "export_page_images", limited)
    _accept_with(monkeypatch, lambda d: (d.dpi_spin.setValue(600), d.folder_edit.setText(str(tmp_path))))
    assert dlgs.open_export_images_dialog(ctx, view, [0])
    files = list(tmp_path.glob("*.png"))
    assert len(files) == 1
    with Image.open(files[0]) as im:
        assert im.width * im.height <= 1_000_000


# ------------------------------------------------------------------ screenshot
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_dialog_screenshots(qtbot, tmp_path, mode):
    from foliant.ui import theme
    theme.apply(QApplication.instance(), mode)
    try:
        os.makedirs(SHOTS, exist_ok=True)
        paths = [_jpeg(tmp_path / f"foto_{k}.jpg") for k in range(3)]
        d1 = dlgs.InsertImagesDialog(paths)
        d2 = dlgs.PasteDialog({"text": "Riga\n" * 300})
        d3 = dlgs.ExportImagesDialog(10, [1, 2], folder="/home/utente/Documenti")
        for name, d in (("insert", d1), ("paste", d2), ("export", d3)):
            qtbot.addWidget(d)
            d.show()
            QApplication.processEvents()
            assert d.grab().save(f"{SHOTS}/images_{name}_{mode}.png")
            d.close()
    finally:
        theme.apply(QApplication.instance(), "light")
