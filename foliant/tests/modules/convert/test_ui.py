"""Test dell'interfaccia del modulo Converti (finestre simulate con monkeypatch)."""

from __future__ import annotations

import io
import os

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules.convert.create_dialogs import BlankDialog, ImagesDialog
from foliant.modules.convert.export_dialog import ExportDialog
from foliant.modules.convert.optimize_dialogs import CompressDialog


@pytest.fixture
def ctrl(window):
    return window.ctx.convert_controller


@pytest.fixture
def messages(window, monkeypatch):
    """Raccoglie errori e informazioni invece di mostrare finestre modali."""
    log = {"error": [], "info": [], "confirm": [], "notify": []}
    monkeypatch.setattr(window.ctx, "error", lambda t, m: log["error"].append((t, m)))
    monkeypatch.setattr(window.ctx, "info", lambda t, m: log["info"].append((t, m)))
    monkeypatch.setattr(window.ctx, "notify", lambda text, actions=(), duration_ms=None:
                        log["notify"].append((text, [a[0] for a in actions])))

    def confirm(t, m):
        log["confirm"].append((t, m))
        return True
    monkeypatch.setattr(window.ctx, "confirm", confirm)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return log


def _accept_export(monkeypatch, folder, fmt=None, pages="", **fields):
    def fake_exec(dlg):
        if fmt:
            dlg.format_box.setCurrentIndex(dlg.format_box.findData(fmt))
        dlg.folder_edit.setText(str(folder))
        dlg.range_edit.setText(pages)
        dlg.panels[dlg.fmt].load(fields)
        if not dlg.validate():
            return QDialog.DialogCode.Rejected
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportDialog, "exec", fake_exec)


# ------------------------------------------------------------- registrazione
def test_mode_and_home_actions(window, ctrl):
    assert "converti" in window.modes
    titles = [a[1] for a in window.home._actions]
    assert "Crea PDF da file" in titles and "Immagini in PDF" in titles
    mode = window.modes["converti"]
    texts = [a.text() for a in mode.toolbar.actions()]
    assert "Esporta come…" in texts and "Comprimi" in texts


# ------------------------------------------------------------------ CNV-01
def test_export_open_document_with_unsaved_changes(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    view = window.open_path(sample_pdf)
    with view.document.change("Testo") as pdf:
        pdf[1].insert_text((72, 300), "Modifica non salvata", fontsize=12)
    before = open(sample_pdf, "rb").read()
    out = tmp_path / "out"
    _accept_export(monkeypatch, out, "txt", pages="2-3")
    result = ctrl.export()
    assert result is not None and result.paths == [str(out / "esempio.txt")]
    text = open(result.paths[0], encoding="utf-8").read()
    assert "Modifica non salvata" in text and "Pagina 1" not in text and "Pagina 3" in text
    assert open(sample_pdf, "rb").read() == before
    assert view.document.is_modified


def test_export_invalid_range_creates_nothing(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    window.open_path(sample_pdf)
    out = tmp_path / "out"
    _accept_export(monkeypatch, out, "txt", pages="25-30")
    assert ctrl.export() is None
    assert not out.exists()
    dlg = ExportDialog(window, page_count=3, name="x", folder=str(out))
    dlg.range_edit.setText("25-30")
    assert not dlg.validate()
    assert dlg.range_error.isVisibleTo(dlg) and "3 pagine" in dlg.range_error.text()


def test_export_dialog_remembers_options(window, settings, tmp_path):
    dlg = ExportDialog(window, page_count=3, name="x", folder=str(tmp_path), fmt="png", settings=settings)
    dlg.panels["png"].fields["dpi"].setValue(300)
    dlg.accept()
    dlg2 = ExportDialog(window, page_count=3, name="x", folder=str(tmp_path), fmt="png", settings=settings)
    assert dlg2.panels["png"].fields["dpi"].value() == 300
    fmt, path, opts = dlg2.values()
    assert fmt == "png" and path.endswith("x.png") and opts["dpi"] == 300


def test_export_restricted_document_blocked(window, ctrl, tmp_path, monkeypatch, messages):
    path = tmp_path / "vietato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=12)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    doc.close()
    window.open_path(str(path))
    called = []
    monkeypatch.setattr(ExportDialog, "exec", lambda d: called.append(1) or 0)
    assert ctrl.export("docx") is None
    assert not called and "copia del contenuto" in messages["error"][0][1]


def test_export_unopened_file_with_password(window, ctrl, encrypted_pdf, tmp_path, monkeypatch, messages):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [encrypted_pdf])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("segreta", True))
    _accept_export(monkeypatch, tmp_path, "txt")
    result = ctrl.export()
    assert "Riservato" in open(result.paths[0], encoding="utf-8").read()


def test_ocr_never_warning_before_start(window, ctrl, tmp_path, monkeypatch, messages):
    src = pymupdf.open()
    sp = src.new_page()
    sp.insert_text((60, 200), "SCANSIONE", fontsize=40)
    pix = sp.get_pixmap(dpi=100)
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    shown = []

    def fake_box_exec(box):
        shown.append(box.text())
        box_buttons = {b.text(): b for b in box.buttons()}
        if "Annulla" in box_buttons and "Attiva OCR" in box_buttons:
            box_buttons["Annulla"].click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_box_exec)

    def fake_exec(dlg):
        dlg.format_box.setCurrentIndex(dlg.format_box.findData("docx"))
        dlg.ocr_box.setCurrentIndex(dlg.ocr_box.findData("never"))
        dlg.folder_edit.setText(str(tmp_path / "o"))
        dlg.validate()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportDialog, "exec", fake_exec)
    assert ctrl.export() is None
    assert shown and "OCR" in shown[0] or "scansionate" in shown[0]
    assert not (tmp_path / "o").exists()


# ------------------------------------------------------------- creazione
def _png(path, size=(400, 300), mode="RGB"):
    from PIL import Image
    Image.new(mode, size, (10, 120, 200) if mode == "RGB" else (10, 120, 200, 128)).save(path)
    return str(path)


def test_create_from_images(window, ctrl, tmp_path, monkeypatch, messages):
    paths = [_png(tmp_path / f"img{n}.png") for n in (10, 2, 1)]
    monkeypatch.setattr(ImagesDialog, "exec", lambda d: (d.sort_box.setCurrentIndex(0), d._sort(),
                                                         QDialog.DialogCode.Accepted)[-1])
    view = ctrl.create_from_images(paths)
    assert view is not None and view.document.page_count == 3
    assert view.document.is_modified and view.document.path is None
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert sorted((round(d[0].rect.width), round(d[0].rect.height))) == [595, 842]   # A4 predefinito


def test_images_dialog_sort_and_options(window, tmp_path):
    paths = [_png(tmp_path / f"img{n}.png") for n in (10, 2)]
    dlg = ImagesDialog(window, paths)
    dlg.sort_box.setCurrentIndex(dlg.sort_box.findData("name"))
    dlg._sort()
    assert [os.path.basename(p) for p in dlg.paths()] == ["img2.png", "img10.png"]
    dlg.list.item(0).setSelected(True)
    dlg._rotate(90)
    opts = dlg.options()
    assert opts["rotations"] == {dlg.paths()[0]: 90} and opts["page_size"] == "A4"


def test_create_blank_and_close_asks(window, ctrl, monkeypatch, messages):
    def fake_exec(d):
        d.pages.setValue(3)
        d.paper_box.setCurrentIndex(d.paper_box.findData("grid"))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(BlankDialog, "exec", fake_exec)
    view = ctrl.create_blank()
    assert view.document.page_count == 3
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert abs(d[0].rect.width - 595) <= 1 and abs(d[0].rect.height - 842) <= 1
        assert d[0].get_text().strip() == ""
    asked = []

    def box_exec(box):
        asked.append(box.text())
        next(b for b in box.buttons() if b.text() == "Annulla").click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    assert not window.close_view(view)
    assert asked


def test_blank_dialog_rejects_invalid_size(window):
    dlg = BlankDialog(window)
    dlg.size_box.setCurrentIndex(dlg.size_box.findData("custom"))
    dlg.w.setValue(0.5)
    dlg.unit.setCurrentIndex(dlg.unit.findData("pt"))
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg.error.isVisibleTo(dlg) and dlg.error.text()


def test_clipboard(window, ctrl, messages):
    from PySide6.QtGui import QGuiApplication, QImage
    cb = QGuiApplication.clipboard()
    cb.clear()
    ctrl.actions["clipboard"].setEnabled(ctrl.clipboard_supported())
    assert not ctrl.actions["clipboard"].isEnabled()
    img = QImage(1920, 1080, QImage.Format.Format_RGB32)
    img.fill(0x336699)
    cb.setImage(img)
    assert ctrl.clipboard_supported()
    view = ctrl.create_from_clipboard()
    assert view is not None and view.document.page_count == 1
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        info = d[0].get_images(full=True)[0]
        assert (info[2], info[3]) == (1920, 1080)
        assert d.xref_get_key(info[0], "Filter")[1] != "/DCTDecode"
    cb.clear()


def test_create_from_text_file(window, ctrl, tmp_path, messages):
    path = tmp_path / "note.txt"
    path.write_text("Orologio Verdi\nseconda riga", encoding="utf-8")
    views = ctrl.create_from_files([str(path)])
    assert len(views) == 1
    assert "Orologio Verdi" in views[0].document.page_text(0)


# ------------------------------------------------------------- compressione
@pytest.fixture
def photo_pdf(tmp_path):
    import random
    from PIL import Image
    random.seed(1)
    img = Image.effect_noise((1200, 900), 60).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 60), "Foto del quadrante", fontsize=12)
    p.insert_image(pymupdf.Rect(72, 80, 360, 296), stream=buf.getvalue())
    path = tmp_path / "foto.pdf"
    doc.save(str(path))
    return str(path)


def test_compress_document_undoable(window, ctrl, photo_pdf, monkeypatch, messages):
    view = window.open_path(photo_pdf)
    before_file = open(photo_pdf, "rb").read()
    seen = {}

    def fake_exec(d):
        seen["estimates"] = [d.table.item(r, 1).text() for r in range(d.table.rowCount())]
        d.table.selectRow(3)
        d.update_preview()
        seen["preview"] = d.after_label.pixmap() is not None and not d.after_label.pixmap().isNull()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CompressDialog, "exec", fake_exec)
    res = ctrl.compress()
    assert res["reduced"] and res["after"] < res["before"]
    assert all(e != "—" for e in seen["estimates"]) and seen["preview"]
    assert view.document.is_modified and "Foto del quadrante" in view.document.page_text(0)
    assert open(photo_pdf, "rb").read() == before_file
    assert view.document.undo()
    assert "Riduzione" in messages["notify"][0][0]


def test_compress_no_reduction(window, ctrl, tmp_path, monkeypatch, messages):
    from foliant.modules.convert import optimize
    path = tmp_path / "piccolo.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.save(str(path), garbage=4, deflate=True)
    view = window.open_path(str(path))
    monkeypatch.setattr(CompressDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(optimize, "compress_pdf_bytes", lambda data, *a, **k: data + b"\n%%")
    res = ctrl.compress()
    assert res["reduced"] is False and not view.document.is_modified
    assert "Nessuna riduzione" in messages["notify"][0][0]


# ------------------------------------------------------------- riparazione
def test_repair_ui(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    data = bytearray(open(sample_pdf, "rb").read())
    i = data.rfind(b"startxref")
    data[i + 10:i + 14] = b"9999"
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(bytes(data))
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(bad)])
    results = ctrl.repair()
    assert len(results) == 1
    out = results[0][1]
    assert out.endswith("rotto_riparato.pdf") and pymupdf.open(out).page_count == 3


def test_repair_jpeg_proposes_creation(window, ctrl, tmp_path, monkeypatch, messages):
    from PIL import Image
    fake = tmp_path / "foto.pdf"
    Image.new("RGB", (50, 40), (200, 0, 0)).save(fake, "JPEG")
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: [str(fake)])
    created = []
    monkeypatch.setattr(ctrl, "create_from_images", lambda paths=None: created.append(paths))
    ctrl.repair()
    assert "JPEG" in messages["confirm"][0][1] and created


# ------------------------------------------------------------- in blocco
def test_batch_convert_logic(tmp_path, encrypted_pdf):
    from tests.conftest import make_pdf
    from foliant.modules.convert.batch import ERROR, OK, SKIPPED, batch_convert, collect_pdfs
    root = tmp_path / "in"
    for sub in ("a", "b", "c"):
        (root / sub).mkdir(parents=True)
        make_pdf(root / sub / f"{sub}.pdf", pages=1)
    (root / "rotto.pdf").write_bytes(b"non un pdf")
    (root / "note.txt").write_text("x")
    files, rejected = collect_pdfs([str(root), str(root / "a" / "a.pdf"), encrypted_pdf])
    assert len(files) == 5 and rejected == [] and files.count(str(root / "a" / "a.pdf")) == 1
    out = tmp_path / "out"
    items = batch_convert(files, "txt", out_dir=str(out), keep_structure=True, root=str(root))
    status = {os.path.basename(i.path): i.status for i in items}
    assert status["rotto.pdf"] == ERROR and status["protetto.pdf"] == SKIPPED
    assert all(status[f"{s}.pdf"] == OK for s in "abc")
    for sub in "abc":
        assert (out / sub / f"{sub}.txt").exists()


def test_batch_convert_ui(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    from foliant.modules.convert import batch_dialog
    from foliant.modules.convert.batch_dialog import BatchDialog

    def fake_exec(d):
        d.add_paths([sample_pdf])
        d.format_box.setCurrentIndex(d.format_box.findData("txt"))
        d.dest_box.setCurrentIndex(1)
        d.folder_edit.setText(str(tmp_path / "dest"))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(BatchDialog, "exec", fake_exec)
    reports = []
    monkeypatch.setattr(batch_dialog, "show_batch_report", lambda c, items: reports.append(items))
    monkeypatch.setattr("foliant.modules.convert.batch_dialog.show_batch_report",
                        lambda c, items: reports.append(items))
    items = ctrl.batch_convert()
    assert items and items[0].status == "completato"
    assert (tmp_path / "dest" / "esempio.txt").exists()


def test_translations_cover_ui_strings():
    """Ogni testo italiano dell'interfaccia del modulo ha la traduzione inglese."""
    import ast
    import pathlib
    from foliant.modules.convert.i18n_en import EN
    folder = pathlib.Path(__file__).resolve().parents[3] / "foliant" / "modules" / "convert"
    missing = set()
    for f in folder.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                if node.args[0].value not in EN:
                    missing.add(node.args[0].value)
    assert not missing, sorted(missing)[:20]


def test_flatten_ui_undoable(window, ctrl, tmp_path, monkeypatch, messages):
    from foliant.modules.convert.optimize_dialogs import FlattenDialog
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Testo", fontsize=12)
    p.add_highlight_annot(pymupdf.Rect(70, 88, 120, 104))
    path = tmp_path / "ann.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    monkeypatch.setattr(FlattenDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    ctrl.flatten()
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert list(d[0].annots()) == []
    assert view.document.undo()
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert len(list(d[0].annots())) == 1
    assert len(list(pymupdf.open(str(path))[0].annots())) == 1


def test_scanner_ui_flatbed_two_pages_insert(window, ctrl, sample_pdf, monkeypatch, messages):
    from PIL import Image
    from foliant.modules.convert.create_dialogs import ScannerDialog

    class Fake:
        def list_devices(self):
            return [("sim:0", "Simulato")]

        def scan(self, device, settings, progress=None):
            yield Image.new("RGB", (int(8.27 * settings.dpi), int(11.69 * settings.dpi)), "white")

    ctrl.scanner_backend = Fake()
    view = window.open_path(sample_pdf)

    def fake_exec(d):
        d.dpi.setCurrentIndex(d.dpi.findData(75))
        d.insert.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ScannerDialog, "exec", fake_exec)
    clicks = {"n": 0}

    def box_exec(box):
        clicks["n"] += 1
        label = "Acquisisci un'altra pagina" if clicks["n"] == 1 else "Fine"
        for b in box.buttons():
            if b.text() == label:
                b.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    ctrl.create_from_scanner()
    assert view.document.page_count == 5
    assert "Pagina 1" in view.document.page_text(0) and "Pagina 2" in view.document.page_text(3)


def test_scanner_none_found(window, ctrl, messages):
    class Empty:
        def list_devices(self):
            return []
    ctrl.scanner_backend = Empty()
    assert ctrl.create_from_scanner() is None
    assert "Nessuno scanner" in messages["info"][0][1]


def test_batch_parallel(tmp_path):
    from tests.conftest import make_pdf
    from foliant.modules.convert.batch import OK, batch_convert
    files = [make_pdf(tmp_path / f"f{k}.pdf", pages=2, text=f"File {k} pagina {{n}}") for k in range(4)]
    (tmp_path / "rotto.pdf").write_bytes(b"%PDF-1.4 rotto")
    files.append(str(tmp_path / "rotto.pdf"))
    items = batch_convert(files, "txt", out_dir=str(tmp_path / "out"), workers=2)
    assert [i.status for i in items[:4]] == [OK] * 4 and items[4].status == "errore"
    for k in range(4):
        assert "File %d pagina 2" % k in (tmp_path / "out" / f"f{k}.txt").read_text(encoding="utf-8")


def test_screen_capture(window, ctrl, monkeypatch, messages):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap, QScreen
    from foliant.modules.convert.create_dialogs import CaptureDialog
    pm = QPixmap(800, 600)
    pm.fill()
    monkeypatch.setattr(QScreen, "grabWindow", lambda self, *a: pm)
    steps = iter([CaptureDialog.ANOTHER, QDialog.DialogCode.Accepted])

    def fake_exec(d):
        code = next(steps)
        if code == CaptureDialog.ANOTHER:
            d.preview.set_selection(QRect(100, 50, 300, 200))     # area ritagliata
        return code
    monkeypatch.setattr(CaptureDialog, "exec", fake_exec)
    view = ctrl.create_from_screen(delay_s=0)
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert d.page_count == 2
        sizes = [(img[2], img[3]) for p in d for img in p.get_images(full=True)]
        assert sizes == [(300, 200), (800, 600)]
        info = d[1].get_images(full=True)[0]
        assert d.xref_get_key(info[0], "Filter")[1] == "/FlateDecode"
    monkeypatch.setattr(QScreen, "grabWindow", lambda self, *a: QPixmap())
    n = len(window.views())
    assert ctrl.create_from_screen(delay_s=0) is None
    assert len(window.views()) == n and messages["error"]


def test_photos_dialog_manual_corners(window, ctrl, tmp_path, monkeypatch, messages):
    pytest.importorskip("cv2")
    from PIL import Image
    from foliant.modules.convert.create_dialogs import PhotosDialog
    path = tmp_path / "foto.png"
    Image.new("RGB", (400, 300), "white").save(path)

    def fake_exec(d):
        d.editor._corners[:] = [[10, 10], [390, 20], [380, 290], [20, 280]]
        d.size.setCurrentIndex(d.size.findData(None))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PhotosDialog, "exec", fake_exec)
    view = ctrl.create_from_photos([str(path)])
    assert view is not None and view.document.page_count == 1


def test_remove_conformance_ui(window, ctrl, sample_pdf, tmp_path, messages):
    if __import__("shutil").which("gs") is None:
        pytest.skip("Ghostscript non installato")
    from foliant.modules.convert.engine import convert_pdf
    out = convert_pdf(sample_pdf, tmp_path / "a.pdf", "pdfa-2b")[0]
    view = window.open_path(out)
    assert ctrl.remove_conformance() is True
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert "pdfaid" not in (d.get_xml_metadata() or "")
    assert view.document.undo()


def test_batch_same_names(tmp_path):
    from tests.conftest import make_pdf
    from foliant.modules.convert.batch import batch_convert
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    files = [make_pdf(tmp_path / "a" / "doc.pdf", text="A {n}"), make_pdf(tmp_path / "b" / "doc.pdf", text="B {n}")]
    items = batch_convert(files, "txt", out_dir=str(tmp_path / "out"), policy="overwrite")
    outs = sorted(o for i in items for o in i.outputs)
    assert len(outs) == 2 and len(set(outs)) == 2


def test_export_presets(window, settings, tmp_path):
    dlg = ExportDialog(window, page_count=3, name="x", folder=str(tmp_path), fmt="png", settings=settings)
    dlg.panels["png"].fields["dpi"].setValue(600)
    dlg.save_preset("Stampa")
    dlg2 = ExportDialog(window, page_count=3, name="x", folder=str(tmp_path), fmt="txt", settings=settings)
    assert dlg2.preset_box.findData("Stampa") >= 0
    dlg2.apply_preset("Stampa")
    assert dlg2.fmt == "png" and dlg2.panels["png"].fields["dpi"].value() == 600


def test_epub_metadata_options(sample_pdf, tmp_path):
    import zipfile
    from foliant.modules.convert.engine import convert_pdf
    out = convert_pdf(sample_pdf, tmp_path / "m.epub", "epub", title="Catalogo 2026", author="Verdi", lang="en")[0]
    opf = zipfile.ZipFile(out).read("OEBPS/content.opf").decode()
    assert "<dc:title>Catalogo 2026</dc:title>" in opf and "<dc:creator>Verdi</dc:creator>" in opf
    assert "<dc:language>en</dc:language>" in opf


def test_batch_asks_password_for_locked(window, ctrl, encrypted_pdf, sample_pdf, tmp_path, monkeypatch, messages):
    from PySide6.QtWidgets import QInputDialog
    from foliant.modules.convert import batch_dialog
    from foliant.modules.convert.batch_dialog import BatchDialog

    def fake_exec(d):
        d.add_paths([sample_pdf, encrypted_pdf])
        d.format_box.setCurrentIndex(d.format_box.findData("txt"))
        d.dest_box.setCurrentIndex(1)
        d.folder_edit.setText(str(tmp_path / "dest"))
        d.workers.setValue(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(BatchDialog, "exec", fake_exec)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("segreta", True))
    monkeypatch.setattr(batch_dialog, "show_batch_report", lambda c, items: None)
    items = ctrl.batch_convert()
    assert [i.status for i in items] == ["completato", "completato"]
    assert (tmp_path / "dest" / "protetto.txt").exists()


def test_clipboard_after_windows_destroyed(qtbot, settings):
    """Il segnale globale degli appunti non deve raggiungere azioni di finestre già distrutte."""
    import gc
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    from foliant.ui.main_window import MainWindow
    for _k in range(2):
        win = MainWindow(settings)
        win.show()
        win.close()
        win.deleteLater()
        QApplication.processEvents()
        QApplication.sendPostedEvents(None, 0)
        del win
        gc.collect()
    errors = []
    import sys
    old = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a)
    try:
        QGuiApplication.clipboard().setText("x")
        QApplication.processEvents()
    finally:
        sys.excepthook = old
    assert not errors


def test_compress_open_encrypted_keeps_protection(window, ctrl, tmp_path, monkeypatch, messages):
    """Sicurezza: la copia compressa di un PDF aperto con la sola password utente resta cifrata
    con gli stessi permessi (nessuna restrizione aggirata)."""
    import io as _io
    from PIL import Image
    from PySide6.QtWidgets import QInputDialog
    img = Image.effect_noise((900, 700), 60).convert("RGB")
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 60), "Riservato", fontsize=12)
    p.insert_image(pymupdf.Rect(72, 80, 360, 300), stream=buf.getvalue())
    path = tmp_path / "cifrato.pdf"
    perms = pymupdf.PDF_PERM_PRINT
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="capo",
             permissions=perms)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("utente", True))
    window.open_path(str(path))

    def fake_exec(d):
        d.target.setCurrentIndex(d.target.findData("file"))
        d.table.selectRow(3)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CompressDialog, "exec", fake_exec)
    out = tmp_path / "compresso.pdf"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    res = ctrl.compress()
    assert res and res.get("output") == str(out)
    result = pymupdf.open(str(out))
    assert result.needs_pass
    assert result.authenticate("utente")
    assert "AES" in result.metadata["encryption"] and "256" in result.metadata["encryption"]
    assert not result.permissions & pymupdf.PDF_PERM_COPY
    assert not result.permissions & pymupdf.PDF_PERM_MODIFY
    assert result.permissions & pymupdf.PDF_PERM_PRINT


def test_create_portfolio_ui(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    from foliant.modules.convert.portfolio_dialog import COL_CUSTOM, COL_DESC, COL_FOLDER, PortfolioDialog
    other = tmp_path / "nota.txt"
    other.write_text("ciao", encoding="utf-8")

    def fake_exec(d):
        d.title.setText("Pratica 42")
        d.table.item(0, COL_FOLDER).setText("Contratti")
        d.table.item(1, COL_FOLDER).setText("Note")
        d.table.item(1, COL_DESC).setText("Appunto del cliente")
        d.custom_name.setText("Cliente")
        d.table.item(0, COL_CUSTOM).setText("Rossi")
        d.table.item(1, COL_CUSTOM).setText("Bianchi")
        d.view.setCurrentIndex(d.view.findData("thumbnails"))
        d.sort.setCurrentIndex(d.sort.findData("custom"))
        d.initial.setCurrentIndex(d.initial.findData(1))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PortfolioDialog, "exec", fake_exec)
    view = ctrl.create_portfolio([sample_pdf, str(other)])
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert d.embfile_count() == 2 and "Pratica 42" in d[0].get_text() and "Appunto del cliente" in d[0].get_text()
        cx = int(d.xref_get_key(d.pdf_catalog(), "Collection")[1].split()[0])
        assert d.xref_get_key(cx, "View")[1] == "/T"
        assert d.xref_get_key(cx, "Sort/S")[1] == "/Cliente"
        assert d.xref_get_key(cx, "Schema/Cliente/Subtype")[1] == "/S"
        assert d.xref_get_key(cx, "D")[1] == "<2>nota.txt"
        assert sorted(d.embfile_names()) == ["<1>esempio.pdf", "<2>nota.txt"]
    assert view.document.display_name == "Pratica 42.pdf"


def test_images_as_image_size_ignores_margin(window, ctrl, tmp_path, monkeypatch, messages):
    """«Come l'immagine»: il campo Margini è disattivato e non deve aggiungere una cornice."""
    from PIL import Image
    path = tmp_path / "foto.png"
    Image.new("RGB", (384, 288), (20, 60, 200)).save(path, dpi=(72, 72))

    def fake_exec(d):
        d.size_box.setCurrentIndex(d.size_box.findData(None))
        assert not d.margin.isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ImagesDialog, "exec", fake_exec)
    view = ctrl.create_from_images([str(path)])
    with pymupdf.open(stream=view.document.to_bytes()) as d:
        assert abs(d[0].rect.width - 384) < 0.5 and abs(d[0].rect.height - 288) < 0.5


def test_compress_document_reduction_survives_save(window, ctrl, tmp_path, monkeypatch, messages):
    """«Applica al documento aperto»: la riduzione annunciata è quella del file salvato."""
    import os
    doc = pymupdf.open()
    for i in range(40):
        p = doc.new_page()
        for k in range(30):
            p.insert_text((50, 50 + k * 20), f"Pagina {i} riga {k} del catalogo orologi usati", fontsize=10)
    path = tmp_path / "quaranta.pdf"
    doc.save(str(path), garbage=0, deflate=True)       # senza flussi di oggetti
    view = window.open_path(str(path))

    def fake_exec(d):
        d.table.selectRow(0)                            # senza perdita
        d.target.setCurrentIndex(d.target.findData("document"))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CompressDialog, "exec", fake_exec)
    res = ctrl.compress()
    before = os.path.getsize(path)
    assert res["reduced"], messages["notify"]
    if res["reduced"]:
        assert window.save_view(view)
        saved = os.path.getsize(path)
        assert saved == res["after"] and saved < before
    else:
        assert not view.document.is_modified and os.path.getsize(path) == before


def test_export_uses_thumbnail_selection(window, ctrl, twenty_pages, tmp_path, monkeypatch, messages):
    view = window.open_path(twenty_pages)

    previous = window.selected_pages_provider
    window.ctx.set_selected_pages_provider(lambda v: [1, 4, 6] if v is view else [])
    monkeypatch.setattr(window, "selected_pages_provider", window.selected_pages_provider)
    _ = previous

    def fake_exec(dlg):
        assert dlg.selected == [1, 4, 6]
        dlg.folder_edit.setText(str(tmp_path))
        dlg.format_box.setCurrentIndex(dlg.format_box.findData("txt"))
        dlg.range_edit.setText("selezionate")
        dlg.validate()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ExportDialog, "exec", fake_exec)
    result = ctrl.export()
    text = open(result.paths[0], encoding="utf-8").read()
    nums = [int(line.split()[-1]) for line in text.replace("\f", "\n").splitlines() if line.startswith("Pagina")]
    assert nums == [2, 5, 7]


def test_repaired_document_banner_and_copy(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    data = bytearray(open(sample_pdf, "rb").read())
    i = data.rfind(b"startxref")
    data[i + 10:i + 14] = b"9999"
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(bytes(data))
    view = window.open_path(str(bad))
    assert view.document.repaired
    assert "riparato" in window.ctx.banners(view)
    out = tmp_path / "rotto_riparato.pdf"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    assert ctrl.save_repaired_copy(view) == str(out)
    fixed = pymupdf.open(str(out))
    assert fixed.page_count == 3 and not getattr(fixed, "is_repaired", False)
    assert "riparato" not in window.ctx.banners(view)
    assert open(bad, "rb").read() == bytes(data)


def test_export_runs_non_modal(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    """Le conversioni lunghe non bloccano la finestra: run_task con modal=False e on_done."""
    window.open_path(sample_pdf)
    seen = {}
    original = window.ctx.run_task

    def spy(title, fn, *args, **kwargs):
        if kwargs.get("modal") is False:
            seen["modal"] = False
            seen["on_done"] = kwargs.get("on_done") is not None
        return original(title, fn, *args, **kwargs)
    monkeypatch.setattr(window.ctx, "run_task", spy)
    _accept_export(monkeypatch, tmp_path, "txt")
    result = ctrl.export()
    assert seen == {"modal": False, "on_done": True}
    assert result is not None and os.path.exists(result.paths[0])



def test_conversion_result_is_a_notification(window, ctrl, sample_pdf, tmp_path, monkeypatch, messages):
    """Fine conversione: avviso non bloccante con «Apri» e «Mostra cartella», nessuna finestra modale."""
    window.open_path(sample_pdf)
    opened = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: opened.append(self.text()) or 0)
    _accept_export(monkeypatch, tmp_path, "txt")
    ctrl.export()
    assert not opened
    text, actions = messages["notify"][-1]
    assert "esempio.txt" in text and actions[:2] == ["Apri", "Mostra cartella"]


def test_export_allowed_after_owner_unlock(window, ctrl, tmp_path, monkeypatch, messages):
    """Dopo lo sblocco con la password dei permessi la conversione è consentita."""
    path = tmp_path / "vietato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=12)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    doc.close()
    view = window.open_path(str(path))
    assert not view.document.allows(pymupdf.PDF_PERM_COPY)
    assert view.document.unlock("capo")
    assert view.document.allows(pymupdf.PDF_PERM_COPY)
    _accept_export(monkeypatch, tmp_path / "out", "txt")
    result = ctrl.export()
    assert result is not None and "Riservato" in open(result.paths[0], encoding="utf-8").read()
