"""Interfaccia OCR: modalità, proposta all'apertura, esecuzione, revisione, blocco."""

from __future__ import annotations

import os

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QDialog

from foliant.modules.ocr import detect, dialogs, engine, ui

from .conftest import FRASI, image_pdf, scan_image


def ocr_controller():
    from foliant.modules import ocr
    return ocr.controller


def wait_analysis(qtbot, view):
    ctl = ocr_controller()
    qtbot.waitUntil(lambda: ctl.state(view).complete and len(ctl.state(view).infos) == view.page_count, timeout=5000)


@pytest.fixture
def ten_pages(tmp_path):
    """10 pagine: 3 e 7 scannerizzate (solo immagine), le altre con testo vettoriale."""
    scan = image_pdf(tmp_path / "s.pdf", [scan_image(lines=FRASI[:3], dpi=150)], dpi=150)
    src = pymupdf.open(scan)
    doc = pymupdf.open()
    for n in range(1, 11):
        if n in (3, 7):
            doc.insert_pdf(src)
        else:
            p = doc.new_page()
            p.insert_text((72, 100), f"Pagina digitale numero {n} con testo vettoriale", fontsize=14)
    path = tmp_path / "dieci.pdf"
    doc.save(str(path))
    return str(path)


@pytest.fixture
def scan_two(tmp_path):
    return image_pdf(tmp_path / "scansione.pdf", [scan_image(lines=FRASI[:3], dpi=200),
                                                   scan_image(lines=FRASI[3:6], dpi=200)], dpi=200)


def test_modalita_e_azioni(window):
    assert "ocr" in window.modes
    ctl = ocr_controller()
    for key in ("run", "edit", "text", "area", "review", "batch", "languages", "detect"):
        assert key in ctl.actions
    assert not ctl.actions["run"].isEnabled()          # nessun documento
    assert ctl.actions["batch"].isEnabled()


def test_proposta_pagine_3_e_7(window, qtbot, ten_pages, monkeypatch):
    view = window.open_path(ten_pages)
    ctl = ocr_controller()
    qtbot.waitUntil(lambda: ctl.state(view).bar is not None and ctl.state(view).bar.isVisible(), timeout=3000)
    wait_analysis(qtbot, view)
    bar = ctl.state(view).bar
    assert ctl.state(view).detected == [2, 6]
    assert "(2)" in bar.label.text()
    seen = {}

    def fake_exec(dlg):
        seen["pages"] = dlg.selected_pages()
        seen["text"] = dlg.pages.currentText()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(dialogs.OcrDialog, "exec", fake_exec)
    bar.btn_run.click()
    assert seen["pages"] == [2, 6] and "3, 7" in seen["text"]
    bar.btn_later.click()
    assert not bar.isVisible()


def test_nessuna_proposta_per_pdf_digitale_o_gia_ocr(window, qtbot, sample_pdf, scan_two, tmp_path):
    ctl = ocr_controller()
    view = window.open_path(sample_pdf)
    wait_analysis(qtbot, view)
    assert ctl.state(view).bar is None or not ctl.state(view).bar.isVisible()
    out = tmp_path / "ocr.pdf"
    out.write_bytes(engine.ocr_pdf_bytes(open(scan_two, "rb").read(), ["ita"], "searchable"))
    view2 = window.open_path(str(out))
    wait_analysis(qtbot, view2)
    assert all(i.category == detect.OCR_LAYER for i in ctl.state(view2).infos.values())
    assert ctl.state(view2).bar is None or not ctl.state(view2).bar.isVisible()


def test_rilevamento_disattivato_e_non_chiedere_piu(window, qtbot, scan_two):
    ctl = ocr_controller()
    ctl.actions["detect"].setChecked(False)
    view = window.open_path(scan_two)
    wait_analysis(qtbot, view)
    assert ctl.state(view).bar is None or not ctl.state(view).bar.isVisible()
    assert ctl.actions["run"].isEnabled()                # il comando resta disponibile
    ctl.actions["detect"].setChecked(True)
    ctl.start_analysis(view)
    qtbot.waitUntil(lambda: ctl.state(view).bar is not None and ctl.state(view).bar.isVisible(), timeout=3000)
    ctl.state(view).bar.btn_never.click()
    view.document._saved_state = view.document._state
    window.close_view(view)
    view = window.open_path(scan_two)
    wait_analysis(qtbot, view)
    qtbot.wait(50)
    assert ctl.state(view).bar is None or not ctl.state(view).bar.isVisible()


def test_proposta_al_clic_su_pagina_immagine(window, qtbot, scan_two):
    ctl = ocr_controller()
    view = window.open_path(scan_two)
    wait_analysis(qtbot, view)
    ctl.dismiss(view)
    ctl.state(view).dismissed = False
    view.flush_rendering()
    r = view.page_rect(0)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=r.center().toPoint())
    bar = ctl.state(view).bar
    assert bar.isVisible() and bar.page == 0
    assert "modificabile" in bar.btn_run.text()


def test_ocr_modificabile_annulla_e_file_intatto(window, qtbot, scan_two):
    original = open(scan_two, "rb").read()
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    opts = engine.OcrOptions(languages=["ita"], mode=engine.EDITABLE)
    result = ctl.run(view, opts, [0, 1], preview=False)
    assert len(result.recognized) == 2
    text = view.document.page_text(0)
    assert engine.char_accuracy(text, "\n".join(FRASI[:3])) >= 0.95
    assert view.document.is_modified
    assert open(scan_two, "rb").read() == original      # il file non si tocca finché non si salva
    assert ctl.state(view).placed[0]
    view.document.undo()
    assert view.document.page_text(0).strip() == ""
    view.document.redo()
    assert "compravendita" in view.document.page_text(0)


def test_anteprima_con_correzione(window, qtbot, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()

    def fake_exec(dlg):
        dlg.only_low.setChecked(False)
        dlg.show()
        item = dlg.table.item(1, 0)
        assert item.text() == "presente"
        item.setText("PRESENTE")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialogs.ReviewDialog, "exec", fake_exec)
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=True)
    assert "Il PRESENTE contratto" in view.document.page_text(0).replace("\n", " ")
    # anteprima rifiutata: nessuna modifica
    monkeypatch.setattr(dialogs.ReviewDialog, "exec", lambda dlg: QDialog.DialogCode.Rejected)
    before = view.document.version
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [1], preview=True)
    assert view.document.version == before


def test_solo_testo_e_ocr_area(window, scan_two, monkeypatch, tmp_path):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    shown = []

    def fake_exec(dlg):
        if dlg.result is not None:
            assert "— Pagina 1 —" in dlg.edit.toPlainText()
            dlg.separator.setCurrentIndex(dlg.separator.findData("formfeed"))
        shown.append(dlg.edit.toPlainText())
        return 0
    monkeypatch.setattr(dialogs.TextDialog, "exec", fake_exec)
    ctl.run(view, engine.OcrOptions(languages=["ita"], mode=engine.TEXT_ONLY), [0, 1])
    assert shown[-1].count("\f") == 1
    assert engine.char_accuracy(shown[-1].replace("\f", "\n"), "\n".join(FRASI[:6])) >= 0.95
    assert not view.document.is_modified
    ctl.ocr_area(view, 0, pymupdf.Rect(50, 75, 545, 100))
    assert engine.char_accuracy(shown[-1], FRASI[0]) >= 0.95
    # salvataggio TXT
    dest = tmp_path / "testo.txt"
    monkeypatch.setattr(ctl.ctx, "ask_save_path", lambda *a, **k: str(dest))
    dlg = dialogs.TextDialog(window, "ciao àè", save_cb=None)
    ctl.show_text  # noqa: B018
    from foliant.modules.ocr import ui as ui_mod
    monkeypatch.setattr(ui_mod.TextDialog, "exec", lambda d: d.save() or 0)
    ctl.show_text("città perché", "t")
    assert dest.read_text(encoding="utf-8") == "città perché"
    dlg.close()


def test_strumento_area(window, qtbot, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    window.set_mode("ocr")
    ctl.actions["area"].setChecked(True)
    assert view.tool is ctl.area_tool
    got = []
    monkeypatch.setattr(ctl.area_tool, "on_area", lambda v, p, r: got.append((p, r)))
    view.flush_rendering()
    t = view.page_transform(0)
    a = t.map(QPointF(50, 70)).toPoint()
    b = t.map(QPointF(300, 100)).toPoint()
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(view.viewport(), pos=b)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b)
    assert got and got[0][0] == 0 and abs(got[0][1].x0 - 50) < 2 and abs(got[0][1].y1 - 100) < 2
    window.set_mode("leggi")
    assert not ctl.actions["area"].isChecked()


def test_correzione_parola_nel_documento(window, scan_two):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    for mode in (engine.SEARCHABLE, engine.EDITABLE):
        ctl.run(view, engine.OcrOptions(languages=["ita"], mode=mode), [0], preview=False)
        rect, word = next((r, w) for r, w in ctl.state(view).placed[0] if w.text == "contratto")
        assert ctl.correct_word(view, 0, rect, word, "accordo")
        text = view.document.page_text(0)
        assert "accordo" in text and "contratto" not in text
        view.document.undo()
        assert "contratto" in view.document.page_text(0)
        view.document.undo()


def test_pannello_parole_incerte(window, scan_two):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    st = ctl.state(view)
    words = [w for _r, w in st.placed[0]]
    words[1].conf = 20.0                     # simula una parola incerta
    ctl.actions["review"].setChecked(True)
    panel = ctl.review_panel
    panel.set_view(view)
    assert panel.table.rowCount() >= 1
    row = next(i for i, r in enumerate(panel.rows) if r[2] is words[1])
    panel.table.setCurrentCell(row, 1)
    assert panel.edit.text() == words[1].text
    panel.edit.setText("PRESENTE")
    panel.btn_fix.click()
    assert "PRESENTE" in view.document.page_text(0)
    view.flush_rendering()
    ctl.actions["review"].setChecked(False)


def test_lingua_mancante(window, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    asked = []
    monkeypatch.setattr(ctl, "ask_missing", lambda missing: asked.append(missing) or None)
    assert ctl.run(view, engine.OcrOptions(languages=["ita", "deu"]), [0], preview=False) is None
    assert asked == [["deu"]] and not view.document.is_modified
    monkeypatch.setattr(ctl, "ask_missing", lambda missing: "remove")
    res = ctl.run(view, engine.OcrOptions(languages=["ita", "deu"]), [0], preview=False)
    assert res is not None and res.options.languages == ["ita"]


def test_annullato_e_errori(window, scan_two, monkeypatch):
    from foliant.api import OperationCancelled
    view = window.open_path(scan_two)
    ctl = ocr_controller()

    def cancel(*a, **k):
        raise OperationCancelled()
    monkeypatch.setattr(engine, "recognize_document", cancel)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    assert not view.document.is_modified
    errors = []
    monkeypatch.setattr(ctl.ctx, "error", lambda t, m: errors.append(m))

    def boom(*a, **k):
        raise RuntimeError("guasto")
    monkeypatch.setattr(engine, "recognize_document", boom)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    assert errors and "guasto" in errors[0]


def test_ocr_in_blocco(window, tmp_path, monkeypatch, sample_pdf):
    """Cartella mista: scansioni, PDF nativo, danneggiato, protetto, TIFF multipagina (OCR-15)."""
    import shutil

    import cv2
    from foliant.modules.ocr import batch
    ctl = ocr_controller()
    src = tmp_path / "lotto"
    src.mkdir()
    for n in range(2):
        image_pdf(src / f"scan{n}.pdf", [scan_image(lines=FRASI[:2], dpi=150)], dpi=150)
    shutil.copy(sample_pdf, src / "nativo.pdf")
    (src / "rotto.pdf").write_bytes(b"non un pdf")
    with pymupdf.open(str(src / "scan0.pdf")) as d:
        d.save(str(src / "protetto.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    img = scan_image(lines=FRASI[:2], dpi=150)
    ok, buf = cv2.imencode(".png", img)
    pages = [pymupdf.open(stream=buf.tobytes(), filetype="png") for _ in range(3)]
    tif = pymupdf.open()
    for p in pages:
        tif.insert_pdf(pymupdf.open(stream=p.convert_to_pdf()))
    pix = [tif[i].get_pixmap(dpi=150) for i in range(3)]
    from PIL import Image
    ims = [Image.frombytes("RGB", (p.width, p.height), p.samples) for p in pix]
    ims[0].save(src / "pagine.tif", save_all=True, append_images=ims[1:])
    existing = src / "scan0_ocr.pdf"
    monkeypatch.setattr(dialogs.ReportDialog, "open", lambda d: None)
    items = ctl.run_batch([str(src)], engine.SEARCHABLE, ["ita"])
    by = {os.path.basename(i.path): i for i in items}
    assert len(items) == 6
    assert by["scan0.pdf"].status == batch.OK and by["scan1.pdf"].status == batch.OK
    assert by["pagine.tif"].status == batch.OK and by["pagine.tif"].pages == 3
    assert by["nativo.pdf"].status == batch.SKIPPED
    assert by["rotto.pdf"].status == batch.ERROR and by["protetto.pdf"].status == batch.ERROR
    with pymupdf.open(str(existing)) as d:
        assert "compravendita" in d[0].get_text()
    csv_text = batch.report_csv(items)
    assert len(csv_text.strip().splitlines()) == 7
    # nome già esistente: suffisso numerico, il file esistente resta identico
    before = existing.read_bytes()
    items2 = ctl.run_batch([str(src / "scan0.pdf")], engine.SEARCHABLE, ["ita"])
    assert items2[0].output.endswith("scan0_ocr (2).pdf") and existing.read_bytes() == before
    # sovrascrittura con copia di sicurezza byte-identica
    orig = (src / "scan1.pdf").read_bytes()
    batch.process_file(batch.BatchItem(str(src / "scan1.pdf")), engine.OcrOptions(languages=["ita"]),
                       overwrite=True)
    assert (src / "scan1.bak.pdf").read_bytes() == orig
    with pymupdf.open(str(src / "scan1.pdf")) as d:
        assert "compravendita" in d[0].get_text()


def test_dialogo_impostazioni(window, settings):
    dlg = dialogs.OcrDialog(window, page_count=5, current_page=1, detected=[0, 3], settings=settings,
                            mode=engine.EDITABLE)
    assert dlg.mode() == engine.EDITABLE and dlg.selected_pages() == [0, 3]
    dlg.pages.setCurrentIndex(dlg.pages.findData("custom"))
    dlg.range_edit.setText("2-4, 9")
    dlg.accept()
    assert "fuori intervallo" in dlg.error.text()
    dlg.range_edit.setText("2-fine")
    dlg.cb_deskew.setChecked(False)
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    o = dlg.options()
    assert dlg.selected_pages() == [1, 2, 3, 4] and o.preprocess.deskew is False and o.languages == ["ita", "eng"]
    dlg2 = dialogs.OcrDialog(window, page_count=5, current_page=0, detected=[], settings=settings)
    assert dlg2.cb_deskew.isChecked() is False         # impostazioni ricordate


def test_dialogo_lingue(window):
    dlg = dialogs.LanguagesDialog(window)
    assert dlg.table.rowCount() >= 38
    codes = [dlg.table.item(r, 1).text() for r in range(dlg.table.rowCount())]
    assert codes[:2] == ["ita", "eng"]
    dlg.table.selectRow(codes.index("ita"))
    assert not dlg.btn_remove.isEnabled() and not dlg.btn_download.isEnabled()
    dlg.table.selectRow(codes.index("deu"))
    assert dlg.btn_download.isEnabled()


def test_traduzioni_inglesi():
    from foliant import i18n
    from foliant.modules.ocr.translations import EN
    i18n.add_translations("en", EN)
    i18n.set_language("en")
    try:
        assert i18n._("Riconosci testo") == "Recognize text"
        from foliant.modules.ocr.languages import language_name
        assert language_name("deu") == "German"
    finally:
        i18n.set_language("it")


def test_pagine(window):
    assert engine.parse_pages("1-3, 7, 10-fine", 12) == [0, 1, 2, 6, 9, 10, 11]
    assert engine.parse_pages("dispari", 5) == [0, 2, 4]
    with pytest.raises(ValueError):
        engine.parse_pages("abc", 3)
    assert engine.format_pages([2, 6, 7, 8]) == "3, 7-9"


def test_destinazione_nuovo_documento(window, scan_two):
    original = open(scan_two, "rb").read()
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    ctl.run(view, engine.OcrOptions(languages=["ita"], mode=engine.EDITABLE), [0], preview=False,
            new_document=True)
    assert len(window.views()) == 2
    new = window.current_view()
    assert new is not view and "(OCR)" in new.document.display_name
    assert "compravendita" in new.document.page_text(0)
    assert not view.document.is_modified and view.document.page_text(0).strip() == ""
    assert open(scan_two, "rb").read() == original
    new.document._saved_state = new.document._state


def test_profilo_esporta_importa(window, settings):
    a = dialogs.OcrDialog(window, page_count=3, current_page=0, detected=[], settings=settings)
    a.rb_editable.setChecked(True)
    a.cb_denoise.setChecked(False)
    a.existing.setCurrentIndex(a.existing.findData(engine.REPLACE))
    a.dpi.setCurrentIndex(a.dpi.findData(400))
    import json
    data = json.loads(json.dumps(a.profile()))
    b = dialogs.OcrDialog(window, page_count=3, current_page=0, detected=[], settings=None)
    b.load_profile(data)
    assert b.profile() == a.profile()
    with pytest.raises(ValueError):
        b.load_profile({"altro": 1})


def test_convalida_immediata_intervallo(window):
    dlg = dialogs.OcrDialog(window, page_count=10, current_page=0, detected=[], settings=None)
    dlg.pages.setCurrentIndex(dlg.pages.findData("custom"))
    for bad in ("12-15", "5-3"):
        dlg.range_edit.setText(bad)
        assert not dlg.ok_button.isEnabled() and dlg.error.text()
    dlg.range_edit.setText("2-4, 7")
    assert dlg.ok_button.isEnabled() and not dlg.error.text()
    assert "Da elaborare: 4 pagine" in dlg.summary.text()


def test_permessi_richiedono_password_proprietario(window, tmp_path, monkeypatch):
    img = scan_image(lines=FRASI[:2], dpi=150)
    plain = image_pdf(tmp_path / "p.pdf", [img], dpi=150)
    path = tmp_path / "limitato.pdf"
    with pymupdf.open(plain) as d:
        d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
               permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
    view = window.open_path(str(path))
    ctl = ocr_controller()
    monkeypatch.setattr(ctl, "ask_owner_password", lambda: None)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    assert not view.document.is_modified
    errors = []
    monkeypatch.setattr(ctl.ctx, "error", lambda t, m: errors.append(m))
    monkeypatch.setattr(ctl, "ask_owner_password", lambda: "sbagliata")
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    assert errors and not view.document.is_modified
    monkeypatch.setattr(ctl, "ask_owner_password", lambda: "capo")
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is not None
    assert "compravendita" in view.document.page_text(0)


def test_documento_firmato_chiede_conferma(window, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    monkeypatch.setattr(ui.OcrController, "_is_signed", staticmethod(lambda v: True))
    asked = []
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: asked.append(m) or False)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    assert asked and "firmato" in asked[0] and not view.document.is_modified


def test_annulla_mantieni_pagine_completate(window, tmp_path, monkeypatch):
    imgs = [scan_image(lines=["Una riga di prova"], dpi=150) for _ in range(3)]
    path = image_pdf(tmp_path / "tre.pdf", imgs, dpi=150)
    view = window.open_path(path)
    ctl = ocr_controller()
    real = engine.recognize_document

    def interrupted(pdf, opts, pages=None, progress=None):
        full = real(pdf, opts, [0], None)
        raise engine.OcrInterrupted(full)
    monkeypatch.setattr(engine, "recognize_document", interrupted)
    monkeypatch.setattr(ctl, "ask_keep_partial", lambda partial: False)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"]), [0, 1, 2], preview=False) is None
    assert not view.document.is_modified
    monkeypatch.setattr(ctl, "ask_keep_partial", lambda partial: True)
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0, 1, 2], preview=False)
    assert "prova" in view.document.page_text(0) and view.document.page_text(1).strip() == ""
    view.document.undo()
    assert view.document.page_text(0).strip() == ""


def test_esportazione_alto_dal_pannello_testo(window, scan_two, monkeypatch, tmp_path):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    dest = tmp_path / "out.alto.xml"
    monkeypatch.setattr(ctl.ctx, "ask_save_path", lambda *a, **k: str(dest))

    def fake_exec(dlg):
        dlg.btn_alto.click()
        return 0
    monkeypatch.setattr(dialogs.TextDialog, "exec", fake_exec)
    ctl.run(view, engine.OcrOptions(languages=["ita"], mode=engine.TEXT_ONLY), [0])
    import xml.etree.ElementTree as ET
    root = ET.parse(dest).getroot()
    assert "alto" in root.tag and len(root.findall(".//{*}String")) > 10


def test_area_nel_documento_e_testo_esistente(window, scan_two, monkeypatch, sample_pdf):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    clicked = {}

    def fake_exec(dlg):
        clicked["buttons"] = list(dlg.action_buttons)
        clicked["text"] = dlg.edit.toPlainText()
        btn = dlg.action_buttons.get(clicked.get("press", ""))
        if btn is not None:
            btn.click()
        return 0
    monkeypatch.setattr(dialogs.TextDialog, "exec", fake_exec)
    errors = []
    monkeypatch.setattr(ctl.ctx, "error", lambda t, m: errors.append(m))
    area = pymupdf.Rect(50, 75, 545, 100)
    for label, mode_word in (("Aggiungi come testo ricercabile", 3), ("Converti in testo modificabile", 0)):
        clicked["press"] = label
        ctl.ocr_area(view, 0, area)
        assert engine.char_accuracy(clicked["text"], FRASI[0]) >= 0.95
        assert not errors, errors
        page_text = view.document.page_text(0)
        assert "compravendita" in page_text and "acciaio" not in page_text      # solo l'area
        with ENGINE_LOCK_ctx():
            spans = view.document.pdf[0].get_texttrace()
        assert spans and all(s["type"] == mode_word for s in spans)
        view.document.undo()
        assert view.document.page_text(0).strip() == ""
    # area con testo già estraibile: niente OCR, testo mostrato e «Riconosci comunque»
    view2 = window.open_path(sample_pdf)
    clicked["press"] = ""
    monkeypatch.setattr(engine, "recognize_area", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OCR")))
    ctl.ocr_area(view2, 0, pymupdf.Rect(60, 120, 400, 150))
    assert "Orologeria Verdi" in clicked["text"] and "Riconosci comunque" in clicked["buttons"]


def ENGINE_LOCK_ctx():
    from foliant.api import ENGINE_LOCK
    return ENGINE_LOCK


def test_rimuovi_livello_ocr(window, scan_two):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    assert view.document.page_text(1).strip()
    ctl.actions["remove"].trigger()
    assert view.document.page_text(0).strip() == "" and view.document.page_text(1).strip() == ""
    view.document.undo()
    assert "compravendita" in view.document.page_text(0)


def test_forza_ocr_chiede_conferma(window, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    ctl = ocr_controller()
    asked = []
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: asked.append(m) or False)
    opts = engine.OcrOptions(languages=["ita"], existing_text=engine.FORCE)
    assert ctl.run(view, opts, [0], preview=False) is None
    assert asked and not view.document.is_modified


def test_revisione_da_tastiera(window, scan_two, qtbot):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    words = [w for _r, w in ctl.state(view).placed[0]]
    target = next(w for w in words if w.text == "contratto")
    same = [w for w in words if w.text == "in"]
    target.conf = 10.0
    for w in same:
        w.conf = 10.0
    panel = ctl.review_panel
    panel.set_view(view)
    n = len(panel.rows)
    row = next(i for i, r in enumerate(panel.rows) if r[2] is target)
    panel.table.setCurrentCell(row, 1)
    panel.edit.setText("accordo")
    qtbot.keyClick(panel.edit, Qt.Key.Key_Return)
    assert "accordo" in view.document.page_text(0)
    assert len(panel.rows) == n - 1
    row = next(i for i, r in enumerate(panel.rows) if r[2].text == "in")
    panel.table.setCurrentCell(row, 1)
    panel.btn_all.click()
    assert not any(r[2].text == "in" for r in panel.rows)
    if panel.rows:
        w = panel.rows[0][2]
        panel.table.setCurrentCell(0, 1)
        panel.btn_dict.click()
        assert w.text.lower() in ctl.user_dictionary()
    view.document.undo()
    assert "contratto" in view.document.page_text(0)
