"""Interfaccia OCR: modalità, proposta all'apertura, esecuzione, revisione, blocco."""

from __future__ import annotations

import os
import time

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QDialog

from foliant.modules.ocr import detect, dialogs, engine, ui

from tests.conftest import perf_limit

from .conftest import FRASI, image_pdf, scan_image


def profile_traces(needle: str, *extra: str) -> list[str]:
    """File del profilo isolato (configurazione, dati, stato) o `extra` che contengono `needle`."""
    found = []
    roots = [os.environ.get(v, "") for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")]
    files = [f for f in extra if os.path.isfile(f)]
    for root in filter(None, roots):
        for base, _dirs, names in os.walk(root):
            files.extend(os.path.join(base, n) for n in names)
    for f in files:
        try:
            with open(f, "rb") as fh:
                if needle.encode() in fh.read() or needle in f:
                    found.append(f)
        except OSError:
            pass
    return found


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
    assert "(2)" in bar.text
    seen = {}

    def fake_exec(dlg):
        seen["pages"] = dlg.selected_pages()
        seen["text"] = dlg.pages.currentText()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(dialogs.OcrDialog, "exec", fake_exec)
    bar.trigger("Esegui OCR")
    assert seen["pages"] == [2, 6] and "3, 7" in seen["text"]
    bar.trigger("Non ora")
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


def test_rilevamento_disattivato_e_non_chiedere_piu(window, qtbot, scan_two, settings):
    ctl = ocr_controller()
    ctl.actions["detect"].setChecked(False)
    view = window.open_path(scan_two)
    wait_analysis(qtbot, view)
    assert ctl.state(view).bar is None or not ctl.state(view).bar.isVisible()
    assert ctl.actions["run"].isEnabled()                # il comando resta disponibile
    ctl.actions["detect"].setChecked(True)
    ctl.start_analysis(view)
    qtbot.waitUntil(lambda: ctl.state(view).bar is not None and ctl.state(view).bar.isVisible(), timeout=3000)
    ctl.state(view).bar.trigger("Non chiedere più in questa sessione")
    view.document._saved_state = view.document._state
    window.close_view(view)
    view = window.open_path(scan_two)
    wait_analysis(qtbot, view)
    qtbot.wait(50)
    assert ctl.state(view).bar is None or not ctl.state(view).bar.isVisible()
    # riservatezza: la scelta vale solo in memoria, nessuna impronta del documento nella configurazione
    settings.sync()
    assert not settings.contains("ocr/never_ask")
    assert not any("never" in k for k in settings.allKeys())


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
    assert bar.actions[0][0] == "Rendi modificabile questa pagina"


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
        assert i18n._("Esegui OCR") == "Run OCR"
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

    def interrupted(pdf, opts, pages=None, progress=None, **kw):
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


@pytest.mark.parametrize("mode", [engine.SEARCHABLE, engine.TEXT_ONLY])
def test_pdf_cifrato_ocr_e_salvataggio(window, tmp_path, monkeypatch, mode):
    """bug_07: su PDF cifrato la scansione, il testo e la cifratura restano dopo OCR e salvataggio."""
    import numpy as np
    plain = image_pdf(tmp_path / "p.pdf", [scan_image(lines=FRASI[:2], dpi=150)], dpi=150)
    enc = tmp_path / "cifrato.pdf"
    with pymupdf.open(plain) as d:
        d.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="utente",
               permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(enc), password="capo")
    ctl = ocr_controller()
    monkeypatch.setattr(dialogs.TextDialog, "exec", lambda dlg: 0)
    ctl.run(view, engine.OcrOptions(languages=["ita"], mode=mode), [0], preview=False)
    with view.document.change("prova") as pdf:
        pdf[0].set_rotation(0)
    out = tmp_path / "salvato.pdf"
    view.document.save(str(out))
    with pymupdf.open(str(out)) as o:
        assert o.needs_pass and o.authenticate("capo")
        g = np.frombuffer(o[0].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8)
        assert (g < 128).sum() > 100
        assert ("compravendita" in o[0].get_text()) == (mode == engine.SEARCHABLE)


def test_profilo_con_lingua_non_installata(window, scan_two, monkeypatch, settings):
    """bug_04: il tedesco richiesto da un profilo non viene tolto in silenzio; l'OCR non parte."""
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    dlg = dialogs.OcrDialog(window, page_count=2, current_page=0, detected=[0, 1], settings=None)
    prof = dlg.profile()
    prof["languages"] = ["ita", "deu"]
    dlg.load_profile(prof)
    assert dlg.options().languages == ["ita", "deu"]
    assert "tedesco" in dlg.languages.warning.text()
    asked = []
    monkeypatch.setattr(ctl, "ask_missing", lambda missing: asked.append(missing) or None)
    assert ctl.run(view, dlg.options(), [0], preview=False) is None
    assert asked == [["deu"]] and not view.document.is_modified


def test_proposta_su_livello_ocr_con_strumento_modifica(window, qtbot, scan_two, tmp_path):
    """bug_05: con lo strumento di modifica, una pagina con solo livello OCR propone «Rendi modificabile»."""
    from foliant.api import Tool
    out = tmp_path / "ocr.pdf"
    out.write_bytes(engine.ocr_pdf_bytes(open(scan_two, "rb").read(), ["ita"], "searchable"))
    view = window.open_path(str(out))
    ctl = ocr_controller()
    wait_analysis(qtbot, view)
    assert ctl.state(view).infos[0].category == detect.OCR_LAYER

    class EditTool(Tool):
        id = "text-edit"
    view.set_tool(EditTool())
    view.flush_rendering()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.page_rect(0).center().toPoint())
    bar = ctl.state(view).bar
    assert bar is not None and bar.isVisible() and bar.page == 0
    assert "invisibile" in bar.text
    view.set_tool(None)


def test_avviso_bassa_risoluzione_visibile(window, tmp_path, monkeypatch):
    """bug_08: la nota «Risoluzione bassa» compare in revisione, nel messaggio finale e nella barra di stato."""
    path = image_pdf(tmp_path / "basso.pdf", [scan_image(lines=FRASI[:2], dpi=100)], dpi=100)
    view = window.open_path(path)
    ctl = ocr_controller()
    seen = {}

    def fake_exec(dlg):
        seen["summary"] = dlg._summary()
        seen["tip"] = dlg.pages.item(0).toolTip()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialogs.ReviewDialog, "exec", fake_exec)
    infos = []
    monkeypatch.setattr(ctl.ctx, "info", lambda t, m: infos.append(m))
    status = []
    monkeypatch.setattr(ctl.ctx, "status", lambda m, *a: status.append(m))
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=True)
    assert "Risoluzione bassa" in seen["summary"] and "Risoluzione bassa" in seen["tip"]
    assert any("Risoluzione bassa" in m for m in infos)
    assert any("Risoluzione bassa" in m for m in status)


def test_pdf_solo_immagine(window, sample_pdf, monkeypatch):
    """OCR-18: le pagine diventano immagini senza testo, l'aspetto resta, un Annulla ripristina."""
    import numpy as np
    view = window.open_path(sample_pdf)
    ctl = ocr_controller()
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: True)
    from foliant.api import ENGINE_LOCK
    with ENGINE_LOCK:
        before = np.frombuffer(view.document.pdf[0].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8)
    ctl.actions["image_only"].trigger()
    with ENGINE_LOCK:
        page = view.document.pdf[0]
        assert page.get_text().strip() == "" and len(page.get_images()) == 1
        after = np.frombuffer(page.get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8)
        assert detect.classify_page(page).category == detect.IMAGE_ONLY
    assert np.abs(before.astype(int) - after.astype(int)).mean() < 3
    view.document.undo()
    assert "Orologeria" in view.document.page_text(0)


def test_dialogo_lingue_colonne_e_dimensioni(window, qtbot):
    """Stato leggibile (colonna elastica + tooltip) e dimensioni sotto 1 MB non mostrate come «0 MB»."""
    dlg = dialogs.LanguagesDialog(window)
    dlg.resize(620, 520)
    dlg.show()
    qtbot.waitExposed(dlg)
    t = dlg.table
    from PySide6.QtWidgets import QHeaderView
    assert t.horizontalHeader().sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch
    texts = [t.item(r, 3).text() for r in range(t.rowCount())]
    assert all(t.item(r, 3).toolTip() == texts[r] for r in range(t.rowCount()))
    assert not any(" 0 MB" in x for x in texts)
    assert dialogs.size_label(0.4) == "< 1 MB" and dialogs.size_label(2.25) == "2.2 MB"
    fm = t.fontMetrics()
    longest = max(fm.horizontalAdvance(x) for x in texts)
    assert t.columnWidth(3) + 40 >= longest or t.horizontalScrollBar().maximum() > 0


def _wait_job(qtbot, ctl, view, timeout=120000):
    qtbot.waitUntil(lambda: ctl.jobs.get(view) is None, timeout=timeout)


def test_ocr_in_background_documento_consultabile(window, qtbot, scan_two):
    """OCR-13: l'OCR lavora in background; durante il lavoro il documento si consulta e non cambia;
    alla fine il risultato è un solo passo di annulla."""
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    job = ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    assert job is not None and job.bar.isVisible()
    view.go_to_page(1)                                   # navigazione libera
    assert not view.document.is_modified
    assert ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0], preview=False) is None
    _wait_job(qtbot, ctl, view)
    assert "compravendita" in view.document.page_text(0) and view.document.page_text(1).strip()
    view.document.undo()
    assert view.document.page_text(0).strip() == "" and view.document.page_text(1).strip() == ""


def test_ocr_in_background_documento_bloccato(window, qtbot, scan_two, monkeypatch):
    """Durante l'OCR in background il documento è bloccato (consultabile, non modificabile);
    alla fine si sblocca e il risultato si applica senza conferme."""
    from foliant.api import OperationCancelled
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    asked = []
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: asked.append(m) or True)
    ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    assert view.document.locked and "Riconoscimento" in view.document.locked_reason
    with pytest.raises(OperationCancelled):
        with view.document.change("ruota") as pdf:
            pdf[1].set_rotation(90)
    _wait_job(qtbot, ctl, view)
    assert not view.document.locked and not asked
    assert "compravendita" in view.document.page_text(0) and view.document.page_text(1).strip()


def test_ocr_in_background_sblocco_su_errore_e_annullamento(window, qtbot, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    errors = []
    monkeypatch.setattr(ctl.ctx, "error", lambda t, m: errors.append(m))

    def boom(*a, **k):
        raise RuntimeError("guasto")
    monkeypatch.setattr(engine, "recognize_document", boom)
    ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    _wait_job(qtbot, ctl, view)
    assert errors and not view.document.locked


def test_ocr_in_background_documento_modificato_senza_blocco(window, qtbot, scan_two, monkeypatch):
    """Ripiego senza blocco (nucleo che non lo offre): solo le pagine non cambiate ricevono il testo."""
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    asked = []
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: asked.append(m) or True)
    monkeypatch.setattr(type(view.document), "set_locked", lambda self, reason: None)
    ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    with view.document.change("ruota") as pdf:
        pdf[1].set_rotation(90)
    _wait_job(qtbot, ctl, view)
    assert asked and "2" in asked[0]
    assert "compravendita" in view.document.page_text(0) and view.document.page_text(1).strip() == ""


def test_ocr_in_background_annullato_o_chiuso(window, qtbot, scan_two, monkeypatch):
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    monkeypatch.setattr(ctl, "ask_keep_partial", lambda partial: False)
    ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    ctl.jobs[view].bar.btn_cancel.click()
    _wait_job(qtbot, ctl, view)
    assert not view.document.is_modified
    ctl.run_background(view, engine.OcrOptions(languages=["ita"]), [0, 1], preview=False)
    window.close_view(view)                              # chiusura durante il lavoro: nessun errore
    qtbot.waitUntil(lambda: not ctl.jobs, timeout=120000)


def test_menu_contestuale_riconosci_testo_nell_immagine(window, tmp_path, monkeypatch):
    """OCR-11: su un PDF nativo con una schermata, il menu dell'immagine riconosce e aggiunge il testo
    ricercabile solo lì; un Annulla ripristina."""
    from PySide6.QtWidgets import QMenu
    from foliant.api import PageEvent
    import cv2
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Documento digitale con una schermata", fontsize=12)
    img = scan_image(lines=FRASI[:2], dpi=200, size=(595, 120), top=40)
    ok, buf = cv2.imencode(".png", img)
    page.insert_image(pymupdf.Rect(40, 100, 555, 220), stream=buf.tobytes())
    path = tmp_path / "nativo.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    ctl = ocr_controller()
    menu = QMenu()
    pe = PageEvent(page=0, point=pymupdf.Point(200, 150), pos=QPointF(0, 0), button=Qt.MouseButton.RightButton,
                   buttons=Qt.MouseButton.RightButton, modifiers=Qt.KeyboardModifier.NoModifier)
    ctl.menu_provider(view, pe, menu)
    acts = [a for a in menu.actions() if "immagine" in a.text()]
    assert acts
    def fake_exec(dlg):
        dlg.action_buttons["Aggiungi come testo ricercabile"].click()
        return 0
    monkeypatch.setattr(dialogs.TextDialog, "exec", fake_exec)
    acts[0].trigger()
    text = view.document.page_text(0)
    assert "compravendita" in text and text.count("Documento digitale") == 1
    view.document.undo()
    assert "compravendita" not in view.document.page_text(0)
    menu2 = QMenu()
    pe.point = pymupdf.Point(300, 400)
    ctl.menu_provider(view, pe, menu2)
    assert not menu2.actions()


def test_riprendi_lotto_interrotto(window, tmp_path, monkeypatch):
    """OCR-15: un lotto interrotto si riprende elaborando solo i file non completati."""
    from foliant.api import OperationCancelled
    from foliant.modules.ocr import batch
    ctl = ocr_controller()
    monkeypatch.setattr(dialogs.ReportDialog, "open", lambda d: None)
    src = tmp_path / "lotto"
    src.mkdir()
    for n in range(3):
        image_pdf(src / f"s{n}.pdf", [scan_image(lines=[f"Documento numero {n} del lotto"], dpi=120)], dpi=120)
    real = batch.process_file
    calls = []

    def interrupt(item, *a, **k):
        calls.append(os.path.basename(item.path))
        if len(calls) == 2:
            raise OperationCancelled()
        return real(item, *a, **k)
    monkeypatch.setattr(batch, "process_file", interrupt)
    ctl.run_batch([str(src)], engine.SEARCHABLE, ["ita"])
    assert (src / "s0_ocr.pdf").exists() and not (src / "s1_ocr.pdf").exists()
    pending = ctl.pending_batch()
    assert pending and [os.path.basename(f) for f in pending["files"]] == ["s1.pdf", "s2.pdf"]
    assert ctl.actions["resume"].isEnabled()
    monkeypatch.setattr(batch, "process_file", real)
    items = ctl.resume_batch()
    assert [os.path.basename(i.path) for i in items] == ["s1.pdf", "s2.pdf"]
    assert (src / "s1_ocr.pdf").exists() and (src / "s2_ocr.pdf").exists() and not (src / "s0_ocr (2).pdf").exists()
    assert ctl.pending_batch() is None and not ctl.actions["resume"].isEnabled()


def test_lotto_interrotto_solo_in_memoria(window, settings, tmp_path, monkeypatch):
    """Riservatezza: i percorsi di un lotto interrotto non vanno nella configurazione e dopo il riavvio
    «Riprendi lotto» non è disponibile."""
    from foliant.api import OperationCancelled
    from foliant.modules.ocr import batch
    from foliant.modules.ocr.ui import OcrController
    ctl = ocr_controller()
    monkeypatch.setattr(dialogs.ReportDialog, "open", lambda d: None)
    src = tmp_path / "zq7k-cartella"
    src.mkdir()
    for n in range(2):
        image_pdf(src / f"zq7k-riservato{n}.pdf", [scan_image(lines=["Documento riservato"], dpi=72)], dpi=72)

    def interrupt(item, *a, **k):
        raise OperationCancelled()
    monkeypatch.setattr(batch, "process_file", interrupt)
    ctl.run_batch([str(src)], engine.SEARCHABLE, ["ita"], out_dir=str(tmp_path / "zq7k-uscita"))
    assert ctl.pending_batch() and ctl.actions["resume"].isEnabled()    # nella sessione si riprende
    settings.sync()
    assert not settings.contains("ocr/batch_pending")
    assert not profile_traces("zq7k", settings.fileName())
    fresh = OcrController(window.ctx)       # come un nuovo avvio: nulla da riprendere
    assert fresh.pending_batch() is None


def test_pulizia_chiavi_delle_versioni_precedenti(qtbot, tmp_path):
    """Le chiavi delle versioni precedenti (impronte dei documenti, percorsi del lotto) spariscono all'avvio."""
    import json

    from PySide6.QtCore import QSettings

    from foliant.ui.main_window import MainWindow
    path = tmp_path / "vecchia.ini"
    old = QSettings(str(path), QSettings.Format.IniFormat)
    old.setValue("ocr/never_ask", ["sha1:abc", "id:def"])
    old.setValue("ocr/batch_pending", json.dumps({"files": ["/home/u/zq7k-riservato.pdf"], "mode": "searchable",
                                                  "languages": ["ita"], "out_dir": "/home/u/zq7k-out"}))
    old.setValue("ocr/detect", "true")
    old.sync()
    win = MainWindow(old)
    qtbot.addWidget(win)
    old.sync()
    assert not old.contains("ocr/never_ask") and not old.contains("ocr/batch_pending")
    assert old.value("ocr/detect") == "true"            # le preferenze generali restano
    assert "zq7k" not in path.read_text(encoding="utf-8", errors="replace")
    assert not ocr_controller().actions["resume"].isEnabled()
    win.close()


def test_copie_temporanee_lasciate_da_un_arresto(tmp_path):
    """Le copie temporanee del documento per la resa (crash) si cancellano all'avvio; non quelle
    di un processo vivo né i file di altri programmi."""
    import time
    dead = tmp_path / "foliant-ocr-999999999-abc.pdf"
    alive = tmp_path / f"foliant-ocr-{os.getpid()}-abc.pdf"
    legacy_old = tmp_path / "foliant-ocr-xyz.pdf"
    legacy_new = tmp_path / "foliant-ocr-nuovo.pdf"
    other = tmp_path / "altro.pdf"
    for f in (dead, alive, legacy_old, legacy_new, other):
        f.write_bytes(b"%PDF-1.4")
    past = time.time() - 3600
    os.utime(legacy_old, (past, past))
    assert engine.cleanup_leftovers(str(tmp_path)) == 2
    assert not dead.exists() and not legacy_old.exists()
    assert alive.exists() and legacy_new.exists() and other.exists()


def test_copia_temporanea_cancellata_alla_chiusura(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    r = engine.ProcessRenderer(doc.tobytes())
    try:
        path = r.path
        assert path and os.path.basename(path).startswith(f"foliant-ocr-{os.getpid()}-")
        assert path in engine._LIVE_TEMP
        engine.remove_live_temp()               # come all'uscita dell'app
        assert not os.path.exists(path)
    finally:
        r.close()


@pytest.mark.parametrize("keep", [True, False])
def test_dati_di_revisione_nel_file(window, scan_two, tmp_path, monkeypatch, keep):
    """OCR-12: con l'opzione attiva i sospetti non risolti ritornano dopo salvataggio e riapertura;
    con l'opzione disattiva il file salvato non contiene dati di revisione."""
    from foliant.modules.ocr import tess
    monkeypatch.setattr(tess, "LOW_CONFIDENCE", 97.0)          # rende «sospette» molte parole
    ctl = ocr_controller()
    ctl.settings().setValue("ocr/keep_review", "true" if keep else "false")
    view = window.open_path(scan_two)
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    before = sorted((w.text, round(r.x0)) for r, w in ctl.state(view).placed[0] if w.low)
    assert before
    out = tmp_path / "salvato.pdf"
    view.document.save(str(out))
    window.close_view(view)
    with pymupdf.open(str(out)) as d:
        has = d.xref_get_key(d[0].xref, "PieceInfo")[0] != "null"
    assert has == keep
    view2 = window.open_path(str(out))
    after = sorted((w.text, round(r.x0)) for r, w in ctl.state(view2).placed.get(0, []) if w.low)
    assert after == (before if keep else [])


def test_editor_zone(window, qtbot, settings, tmp_path):
    """OCR-10: zone tracciate con il mouse, tipo, lingua, ordine, tabelle rilevate, modelli."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage
    from foliant.modules.ocr import layout
    from .test_layout import _page, _table
    import cv2
    img = _page(_table, dpi=100)
    qimg = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format.Format_RGB888).copy()
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ed = dialogs.ZoneEditor(window, qimg, [], settings, gray)
    ed.show()
    qtbot.waitExposed(ed)
    c = ed.canvas
    ed.kind.setCurrentIndex(ed.kind.findData(layout.IGNORE))
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseMove(c, QPoint(200, 80))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=QPoint(200, 80))
    assert len(ed.zones) == 1 and ed.zones[0].kind == layout.IGNORE
    assert abs(ed.zones[0].rect[2] - 200 / c.width()) < 0.01
    ed.zone_kind.setCurrentIndex(ed.zone_kind.findData(layout.TEXT))
    ed.zone_lang.setCurrentIndex(ed.zone_lang.findData("eng"))
    assert ed.zones[0].kind == layout.TEXT and ed.zones[0].languages == ["eng"]
    ed.detect_tables()
    assert len(ed.zones) == 2 and ed.ordered()[1].kind == layout.TABLE
    ed.list.setCurrentRow(1)
    ed.btn_up.click()
    assert ed.ordered()[0].kind == layout.TABLE
    ed.save_model("fattura")
    ed.list.setCurrentRow(0)
    ed.delete_current()
    assert len(ed.zones) == 1
    ed.load_model("fattura")
    assert [z.kind for z in ed.ordered()] == [layout.TABLE, layout.TEXT]
    assert "fattura" in dialogs.zone_models(settings)
    c.grab().save(str(tmp_path / "zone.png"))
    ed.close()


def test_dialogo_ocr_con_zone_e_profilo(window, settings, monkeypatch, scan_two):
    from foliant.modules.ocr import layout
    view = window.open_path(scan_two)
    ctl = ocr_controller()
    z = layout.Zone((0.1, 0.1, 0.5, 0.2), layout.IGNORE)

    def fake_exec(ed):
        ed.zones = [z]
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialogs.ZoneEditor, "exec", fake_exec)
    dlg = dialogs.OcrDialog(window, page_count=2, current_page=0, detected=[], settings=settings,
                            page_image_cb=lambda: ctl._page_image(view))
    dlg.edit_zones()
    assert "1 zone" in dlg.zones_label.text() and dlg.options().zones == [z]
    prof = dlg.profile()
    other = dialogs.OcrDialog(window, page_count=2, current_page=0, detected=[], settings=None)
    other.load_profile(prof)
    assert other.options().zones[0].kind == layout.IGNORE and other.profile() == prof
    prof["zones"] = [{"rect": [0, 0, 1, 1], "kind": "text", "languages": ["deu"]}]
    other.load_profile(prof)
    assert not other.ok_button.isEnabled() and "tedesco" in other.error.text()


def test_ocr_in_background_pausa_e_ripresa(window, qtbot, tmp_path):
    """OCR-13: in pausa non partono nuove pagine; alla ripresa il lavoro si completa."""
    imgs = [scan_image(lines=[f"Pagina numero {n} della prova"], dpi=120) for n in range(4)]
    view = window.open_path(image_pdf(tmp_path / "p.pdf", imgs, dpi=120))
    ctl = ocr_controller()
    job = ctl.run_background(view, engine.OcrOptions(languages=["ita"], workers=1), [0, 1, 2, 3], preview=False)
    assert ctl.toggle_pause(view) is True and job.bar.btn_pause.text() == "Riprendi"
    qtbot.wait(2500)
    frozen = job.progress.fraction
    qtbot.wait(1500)
    assert job.progress.fraction == frozen and frozen < 1.0 and not job.done
    assert ctl.toggle_pause(view) is False
    _wait_job(qtbot, ctl, view)
    assert all(view.document.page_text(i).strip() for i in range(4))


def test_intervallo_pagine_selezionate(window, tmp_path, monkeypatch):
    """OCR-04: l'intervallo «Pagine selezionate» viene dalla selezione delle miniature (ctx.selected_pages)."""
    imgs = [scan_image(lines=[f"Pagina {n}"], dpi=72) for n in range(5)]
    view = window.open_path(image_pdf(tmp_path / "c.pdf", imgs, dpi=72))
    ctl = ocr_controller()
    monkeypatch.setattr(ctl.ctx, "selected_pages", lambda v=None: [1, 3, 4])
    seen = {}

    def fake_exec(dlg):
        i = dlg.pages.findData("selected")
        seen["label"] = dlg.pages.itemText(i)
        dlg.pages.setCurrentIndex(i)
        seen["pages"] = dlg.selected_pages()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(dialogs.OcrDialog, "exec", fake_exec)
    ctl.open_dialog(view)
    assert seen["pages"] == [1, 3, 4] and "2, 4-5" in seen["label"]


def test_testo_non_estraibile_banner_e_forza(window, qtbot, tmp_path, monkeypatch):
    """bug_09: il banner conta le pagine non estraibili, il dialogo preseleziona «Forza OCR» e
    l'avvio chiede la conferma elencando quelle pagine."""
    from .conftest import unextractable_pdf
    view = window.open_path(unextractable_pdf(tmp_path / "u.pdf"))
    ctl = ocr_controller()
    wait_analysis(qtbot, view)
    bar = ctl.state(view).bar
    assert bar.isVisible() and "non estraibile" in bar.text and "scannerizzat" not in bar.text
    seen = {}

    def fake_exec(dlg):
        seen["existing"] = dlg.existing.currentData()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(dialogs.OcrDialog, "exec", fake_exec)
    bar.trigger("Esegui OCR")
    assert seen["existing"] == engine.FORCE
    asked = []
    monkeypatch.setattr(ctl.ctx, "confirm", lambda t, m: asked.append(m) or False)
    assert ctl.run(view, engine.OcrOptions(languages=["ita"], existing_text=engine.FORCE), [0], preview=False) is None
    assert asked and "(1)" in asked[0] and not view.document.is_modified


def test_messaggio_pagine_vuote_distinto(window, tmp_path, monkeypatch):
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "vuota.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    ctl = ocr_controller()
    infos = []
    monkeypatch.setattr(ctl.ctx, "info", lambda t, m: infos.append(m))
    ctl.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    assert infos and "vuote" in infos[0] and "Forza OCR" not in infos[0]


def test_dialogo_lingue_in_inglese(window):
    """NFR-25: in inglese le colonne del catalogo (lingua, scrittura, stato) sono tradotte, anche nei tooltip."""
    from foliant import i18n
    from foliant.modules.ocr.translations import EN
    i18n.add_translations("en", EN)
    i18n.set_language("en")
    try:
        dlg = dialogs.LanguagesDialog(window)
        t = dlg.table
        rows = {t.item(r, 1).text(): r for r in range(t.rowCount())}
        expected = {"ita": "Latin", "ell": "Greek", "rus": "Cyrillic", "ara": "Arabic", "heb": "Hebrew",
                    "jpn": "Japanese", "chi_sim": "Han", "kor": "Hangul", "hin": "Devanagari"}
        for code, script in expected.items():
            item = t.item(rows[code], 2)
            assert item.text() == script and item.toolTip() == script, (code, item.text())
        assert t.item(rows["deu"], 0).text() == "German"
        assert t.item(rows["ita"], 3).text().startswith("installed")
        italian = {"latino", "greco", "cirillico", "arabo", "ebraico", "giapponese"}
        assert not any(t.item(r, 2).text() in italian for r in range(t.rowCount()))
    finally:
        i18n.set_language("it")


def test_anteprima_preelaborazione(window, tmp_path, monkeypatch):
    """OCR-04: l'anteprima mostra la pagina e l'immagine di lavoro con l'angolo rilevato."""
    path = image_pdf(tmp_path / "i.pdf", [scan_image(lines=FRASI[:4], dpi=150, skew=4, noise=0.02)], dpi=150)
    view = window.open_path(path)
    ctl = ocr_controller()
    shown = {}

    def fake_exec(dlg):
        if isinstance(dlg, dialogs.PreviewDialog):
            shown["info"] = dlg.info.text()
            shown["images"] = [lbl.pixmap().width() for lbl in dlg.images]
            dlg.grab().save(str(tmp_path / "anteprima.png"))
            return 0
        dlg.show_preview()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(dialogs.OcrDialog, "exec", fake_exec)
    monkeypatch.setattr(dialogs.PreviewDialog, "exec", fake_exec)
    ctl.open_dialog(view)
    assert "inclinazione corretta: -4.0°" in shown["info"] or "inclinazione corretta: -3.9°" in shown["info"] \
        or "inclinazione corretta: -4.1°" in shown["info"], shown["info"]
    assert shown["images"] == [460, 460]


def test_due_finestre_chiusa_la_seconda_la_prima_funziona(window, qtbot, scan_two):
    """Ctrl+N: ogni finestra ha il proprio controller; chiudere la seconda non lascia nella prima
    riferimenti a widget distrutti («already deleted»)."""
    from PySide6.QtWidgets import QApplication

    from foliant.modules import ocr
    first = window.ctx.ocr
    second_win = window.new_window()
    second = second_win.ctx.ocr
    assert second is not first and second.review_panel is not first.review_panel
    second_win.close()
    second_win.deleteLater()
    qtbot.waitUntil(lambda: second not in ocr._live, timeout=3000)
    QApplication.processEvents()
    assert ocr.controller is first
    view = window.open_path(scan_two)
    first.run(view, engine.OcrOptions(languages=["ita"]), [0], preview=False)
    first.actions["review"].setChecked(True)
    window.tabs.setCurrentWidget(view)
    first.review_panel.set_view(view)
    first.review_panel.refresh()
    first.actions["review"].setChecked(False)


@pytest.mark.parametrize("lang", ["it", "en"])
def test_pannello_parole_incerte_pulsanti_non_tagliati(window, qtbot, scan_two, lang):
    from PySide6.QtWidgets import QPushButton

    from foliant import i18n
    old = i18n.language()
    i18n.set_language(lang)
    try:
        panel = ui.ReviewPanel(window.ctx.ocr)
        qtbot.addWidget(panel)
        panel.setFixedWidth(260)           # larghezza minima del pannello delle proprietà
        view = window.open_path(scan_two)
        window.ctx.ocr.actions["review"].setChecked(True)
        panel.set_view(view)
        panel.show()
        qtbot.waitUntil(panel.isVisible, timeout=2000)
        buttons = [b for b in panel.findChildren(QPushButton) if b.isVisible()]
        assert len(buttons) >= 4
        for b in buttons:
            assert b.sizeHint().width() - b.width() <= 3, (b.text(), b.sizeHint().width(), b.width())
    finally:
        window.ctx.ocr.actions["review"].setChecked(False)
        if old is not None:
            i18n.set_language(old)


def test_analisi_all_apertura_limitata_e_a_passi_brevi(window, qtbot, tmp_path, monkeypatch):
    """NFR-07: aprendo un PDF di 500 pagine la classificazione nel thread dell'interfaccia si ferma
    alle prime pagine, a passi brevi; il resto si analizza solo su richiesta (dialogo, clic)."""
    doc = pymupdf.open()
    for k in range(500):
        doc.new_page().insert_text((72, 72), f"Pagina {k + 1} di un catalogo di orologi")
    path = tmp_path / "lungo.pdf"
    doc.save(path)
    doc.close()
    ctl = ocr_controller()
    steps = []
    original = ctl._analysis_step

    def timed(view):
        t0 = time.perf_counter()
        original(view)
        steps.append(time.perf_counter() - t0)
    monkeypatch.setattr(ctl, "_analysis_step", timed)
    view = window.open_path(str(path))
    qtbot.waitUntil(lambda: not ctl.state(view).order, timeout=5000)
    st = ctl.state(view)
    assert len(st.infos) <= ui.ANALYSIS_PAGES and st.deferred
    assert steps and max(steps) < perf_limit(0.02), max(steps)
    assert ctl.page_info(view, 400).category == detect.TEXT        # su richiesta
    ctl.complete_analysis(view)
    assert len(st.infos) == 500 and st.complete


def test_foto_senza_testo_nessuna_proposta(window, qtbot, tmp_path):
    """La foto di un orologio (pagina di sola immagine senza caratteri) non fa proporre l'OCR; il
    controllo avviene in un thread, la scansione di un documento resta proposta."""
    from .test_detect_photo import watch_photo
    ctl = ocr_controller()
    photo = window.open_path(image_pdf(str(tmp_path / "foto.pdf"), [watch_photo()], dpi=150))
    wait_analysis(qtbot, photo)
    qtbot.waitUntil(lambda: ctl.state(photo).photo_job is None and bool(ctl.state(photo).photo_checked), timeout=5000)
    st = ctl.state(photo)
    assert st.infos[0].category == detect.IMAGE_ONLY and 0 in st.photos
    assert st.detected == []
    assert st.bar is None or not st.bar.isVisible()
    scan = window.open_path(image_pdf(str(tmp_path / "scan.pdf"), [scan_image()], dpi=300))
    qtbot.waitUntil(lambda: ctl.state(scan).bar is not None and ctl.state(scan).bar.isVisible(), timeout=5000)
    assert ctl.state(scan).detected == [0]
