"""Interfaccia delle operazioni in blocco (BAT-01…05, 20) con la finestra reale."""

from __future__ import annotations

import os

import pymupdf
import pytest

from foliant.modules.tools import batch_ui as bu
from foliant.modules.tools import engine as be

from .conftest import build_pdf, page_lines


@pytest.fixture
def batch(window):
    return window.ctx.tools_module.show_batch()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "ingresso"
    (root / "sotto").mkdir(parents=True)
    for name in ("doc10", "doc2", "alfa"):
        build_pdf(root / f"{name}.pdf", [page_lines(1), page_lines(2)])
    build_pdf(root / "sotto" / "beta.pdf", [page_lines(3)])
    build_pdf(root / "sotto" / "gamma.pdf", [page_lines(4)])
    (root / "sotto" / "nota.txt").write_text("non pdf")
    return root


def test_batch_shown_as_central_widget(window, batch):
    assert window.ctx.central_widget() is batch
    assert window.ctx.current_mode() == "strumenti"


def test_queue_from_folder_with_subfolders(batch, folder):
    added, ignored = batch.add_paths([str(folder)])
    assert added == 5 and len(ignored) == 1
    assert batch.table.rowCount() == 5
    assert "1 file ignorati" in batch.notice.text()
    assert "5 file · 8 pagine" in batch.totals.text()
    batch.add_paths([str(folder / "alfa.pdf")])
    assert len(batch.items) == 5   # nessun duplicato
    batch.sort_by_name()
    names = [it.name for it in batch.items]
    assert names.index("doc2.pdf") < names.index("doc10.pdf")


def test_encrypted_row_shows_password_needed(batch, encrypted_pdf):
    batch.add_paths([encrypted_pdf])
    assert batch.table.item(0, 4).text() == "richiede password"
    batch.set_password(0, "segreta")
    assert batch.table.item(0, 4).text() == "password indicata"


def test_run_with_counter_pattern_and_report(batch, folder, tmp_path, window):
    out = tmp_path / "uscita"
    batch.add_paths([str(folder / "alfa.pdf"), str(folder / "doc2.pdf"), str(folder / "doc10.pdf")])
    batch.add_step("watermark", {"text": "BOZZA"})
    batch.dest_path.setText(str(out))
    batch.pattern.setText("{nome}_{contatore:3}")
    assert [batch.preview.item(r, 1).text() for r in range(3)] == ["alfa_001.pdf", "doc2_002.pdf", "doc10_003.pdf"]
    report = batch.run(confirm=False)
    assert report.counts()["ok"] == 3
    assert sorted(os.listdir(out)) == ["alfa_001.pdf", "doc10_003.pdf", "doc2_002.pdf"]
    doc = pymupdf.open(str(out / "alfa_001.pdf"))
    assert "BOZZA" in doc[0].get_text()
    assert batch.report_box.isVisibleTo(batch)
    assert batch.report_table.rowCount() == 3
    csv_path = tmp_path / "rapporto.csv"
    assert batch.export_report(str(csv_path))
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("file_ingresso,file_uscita,esito")
    assert len(lines) == 4


def test_invalid_pattern_blocks_start(batch, folder, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(batch.ctx, "error", lambda t, m: errors.append(m))
    batch.add_paths([str(folder / "alfa.pdf")])
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    batch.pattern.setText("{autore_x}")
    assert batch.run(confirm=False) is None
    assert errors and "autore_x" in errors[0]
    assert not (tmp_path / "out").exists()


def test_errors_per_file_and_retry_failed(batch, folder, tmp_path, encrypted_pdf):
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"%PDF-1.4 rotto")
    batch.add_paths([str(folder / "alfa.pdf"), str(bad), encrypted_pdf, str(folder / "doc2.pdf")])
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    report = batch.run(confirm=False)
    c = report.counts()
    assert c["ok"] == 2 and c["error"] + c["skipped"] == 2
    codes = {r.code for r in report.results if r.status in ("error", "skipped")}
    assert len(codes) == 2
    batch.retry_failed()
    assert {it.path for it in batch.items} <= {str(bad), os.path.realpath(encrypted_pdf), os.path.realpath(str(bad))}
    assert len(batch.items) == len(report.failed_items())


def test_sequences_saved_without_passwords(batch, tmp_path):
    batch.add_step("protect", {"user_password": "segretissima", "owner_password": "capo"})
    batch.add_step("watermark", {"text": "RISERVATO"})
    path = batch.save_sequence("Riservati")
    assert path and os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "segretissima" not in text and "capo" not in text
    assert batch.sequence_combo.findText("Riservati") >= 0
    batch.steps = []
    batch._refresh_steps()
    assert batch.load_sequence(path)
    assert [s.action for s in batch.steps] == ["protect", "watermark"]
    assert batch.steps_list.count() == 2


def test_param_form_edits_step(batch):
    step = batch.add_step("page_numbers")
    form = batch.params
    form.widgets["fontsize"].setValue(14)
    form.widgets["text"].setText("Pag. {pagina}")
    assert step.params["fontsize"] == 14 and step.params["text"] == "Pag. {pagina}"
    batch.add_step("compress")
    batch.steps_list.setCurrentRow(0)
    assert form.step is step


def test_missing_dependency_shown_clearly(batch, monkeypatch):
    monkeypatch.setattr(be, "_ocr_function", lambda: None)
    batch.add_step("ocr")
    labels = [batch.params.form.itemAt(i).widget() for i in range(batch.params.form.count())]
    texts = [w.text() for w in labels if hasattr(w, "text") and isinstance(w.text(), str)]
    assert any("OCR non è disponibile" in t for t in texts)


def test_ocr_and_convert_with_fake_engines(batch, folder, tmp_path, fake_engines):
    batch.add_paths([str(folder / "alfa.pdf")])
    batch.add_step("ocr", {"mode": "all"})
    batch.dest_path.setText(str(tmp_path / "ocr"))
    report = batch.run(confirm=False)
    assert report.counts()["ok"] == 1 and fake_engines["ocr"]
    batch.steps = []
    batch.add_step("convert", {"format": "docx"})
    batch.dest_path.setText(str(tmp_path / "conv"))
    report = batch.run(confirm=False)
    assert report.counts()["ok"] == 1
    assert os.listdir(tmp_path / "conv") == ["alfa.docx"]


def test_replace_originals_requires_double_confirmation(batch, folder, monkeypatch):
    answers = []
    monkeypatch.setattr(batch.ctx, "confirm", lambda t, m: answers.append(t) or len(answers) < 2)
    target = folder / "alfa.pdf"
    before = target.read_bytes()
    batch.add_paths([str(target)])
    batch.add_step("compress")
    batch.replace_originals.setChecked(True)
    assert batch.run() is None          # seconda conferma negata
    assert target.read_bytes() == before
    assert len(answers) == 2


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_batch_screenshot(window, batch, folder, tmp_path, theme_name):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    theme.apply(QApplication.instance(), theme_name)
    batch.add_paths([str(folder)])
    batch.add_step("bates", {"prefix": "VND-"})
    assert window.grab().save(str(tmp_path / f"batch_{theme_name}.png"))
    theme.apply(QApplication.instance(), "light")


def test_pdf_report_and_history(batch, folder, tmp_path):
    batch.add_paths([str(folder / "alfa.pdf")])
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    batch.run(confirm=False)
    pdf = tmp_path / "rapporto.pdf"
    assert batch.export_report(str(pdf))
    assert "alfa.pdf" in pymupdf.open(str(pdf))[0].get_text()
    files = be.list_history(batch.history_dir())
    assert len(files) == 1
    batch.report = None
    assert batch.show_history(files[0])
    assert batch.report_table.rowCount() == 1


def test_interrupted_job_resumed_on_open(window, folder, tmp_path, monkeypatch):
    module = window.ctx.tools_module
    from PySide6.QtCore import QSettings
    base = os.path.dirname(window.ctx.settings().fileName())
    journal = os.path.join(base, "foliant-lavoro-in-corso.json")
    items = [be.analyze_file(str(folder / n)) for n in ("alfa.pdf", "doc2.pdf", "doc10.pdf")]
    settings = be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "out"))
    report = be.JobReport([be.FileResult(it.path, status=s) for it, s in zip(items, ("ok", "not_processed",
                                                                                     "cancelled"))],
                          settings.steps, __import__("datetime").datetime.now())
    be.write_journal(journal, report, items, settings, finished=False)
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: True)
    module.batch = None
    w = module.show_batch()
    assert sorted(it.name for it in w.items) == ["doc10.pdf", "doc2.pdf"]
    assert [s.action for s in w.steps] == ["compress"]
    assert w.dest_path.text() == str(tmp_path / "out")
    assert not os.path.exists(journal)
    del QSettings


def test_control_panel_pause_toggle(batch):
    control = be.JobControl()
    panel = bu.JobControlPanel(control, batch)
    panel.toggle_pause()
    assert control.paused and panel.pause_button.text() == "Riprendi"
    panel.toggle_pause()
    assert not control.paused
    panel.close()


def test_bates_series_and_index_from_ui(batch, folder, tmp_path):
    batch.add_paths([str(folder / "alfa.pdf"), str(folder / "doc2.pdf")])
    batch.add_step("bates", {"prefix": "VND-", "digits": 5, "series": "VND", "continue_series": True})
    batch.dest_path.setText(str(tmp_path / "o1"))
    batch.run(confirm=False)
    idx = tmp_path / "indice.csv"
    assert batch.export_bates_index(str(idx))
    assert "doc2.pdf,VND-00003,VND-00004" in idx.read_text(encoding="utf-8")
    batch.dest_path.setText(str(tmp_path / "o2"))
    batch.run(confirm=False)
    doc = pymupdf.open(str(tmp_path / "o2" / "alfa.pdf"))
    assert "VND-00005" in doc[0].get_text()


# ----------------------------------------------------------------- BAT-20 completo
def test_ask_at_launch_once_for_all_files(batch, folder, tmp_path, monkeypatch):
    calls = []

    def fake(parent, requests):
        calls.append([(i, p.key) for i, p, _v in requests])
        return {(i, p.key): ("URGENTE" if p.key == "text" else "apri") for i, p, _v in requests}
    monkeypatch.setattr(bu, "ask_launch_parameters", fake)
    batch.add_paths([str(folder / n) for n in ("alfa.pdf", "doc2.pdf", "doc10.pdf")])
    wm = batch.add_step("watermark")
    batch.params.ask_buttons["text"].setChecked(True)
    assert wm.ask == ["text"]
    batch.add_step("protect", {"owner_password": "capo"})
    batch.dest_path.setText(str(tmp_path / "out"))
    report = batch.run(confirm=False)
    assert len(calls) == 1 and (0, "text") in calls[0] and (1, "user_password") in calls[0]
    assert report.counts()["ok"] == 3
    for name in os.listdir(tmp_path / "out"):
        doc = pymupdf.open(str(tmp_path / "out" / name))
        assert doc.needs_pass and doc.authenticate("apri")
        assert "URGENTE" in doc[0].get_text()
    assert all("passi completati" in r.message for r in report.results)
    assert not batch.steps[1].params.get("user_password")   # le password non restano nella sequenza


def test_disabled_step_is_skipped(batch, folder, tmp_path):
    batch.add_paths([str(folder / "alfa.pdf")])
    batch.add_step("watermark", {"text": "NON DEVE COMPARIRE"})
    batch.add_step("page_numbers")
    batch.steps_list.item(0).setCheckState(bu.Qt.CheckState.Unchecked)
    assert not batch.steps[0].enabled
    batch.dest_path.setText(str(tmp_path / "out"))
    batch.run(confirm=False)
    text = pymupdf.open(str(tmp_path / "out" / "alfa.pdf"))[0].get_text()
    assert "NON DEVE COMPARIRE" not in text and "Pagina 1 di 2" in text


def test_export_import_duplicate_sequence(batch, tmp_path, monkeypatch):
    import json
    batch.add_step("ocr")
    batch.add_step("compress")
    batch.add_step("protect", {"user_password": "segreta", "owner_password": "capo"})
    batch.save_sequence("Scansioni")
    exported = tmp_path / "scansioni.foliantseq"
    assert batch.export_sequence(str(exported))
    text = exported.read_text(encoding="utf-8")
    assert "segreta" not in text and "capo" not in text
    data = json.loads(text)
    data["steps"][2]["params"]["user_password"] = "trapelata"
    tampered = tmp_path / "manomessa.foliantseq"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    infos = []
    monkeypatch.setattr(batch.ctx, "notify", lambda m, *a, **k: infos.append(m))
    batch.steps = []
    assert batch.import_sequence(str(tampered))
    assert [s.action for s in batch.steps] == ["ocr", "compress", "protect"]
    assert not batch.steps[2].params.get("user_password") and infos
    data["steps"].append({"action": "teletrasporto", "params": {}})
    bad = tmp_path / "ignota.foliantseq"
    bad.write_text(json.dumps(data), encoding="utf-8")
    errors = []
    monkeypatch.setattr(batch.ctx, "error", lambda t, m: errors.append(m))
    assert batch.import_sequence(str(bad)) is None and "teletrasporto" in errors[0]
    batch.sequence_combo.setCurrentIndex(batch.sequence_combo.findText("Scansioni"))
    dup = batch.duplicate_selected_sequence()
    assert dup and batch.sequence_combo.findText("Scansioni (copia)") >= 0


def test_convert_then_watermark_blocked(batch, folder, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(batch.ctx, "error", lambda t, m: errors.append(m))
    batch.add_paths([str(folder / "alfa.pdf")])
    batch.add_step("convert")
    batch.add_step("watermark")
    batch.dest_path.setText(str(tmp_path / "out"))
    assert batch.run(confirm=False) is None and errors


def test_sequence_keeps_output_settings(batch, tmp_path):
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "fatture"))
    batch.pattern.setText("{nome}_{contatore:3}")
    batch.conflict.setCurrentIndex(batch.conflict.findData("skip"))
    batch.error_policy.setCurrentIndex(batch.error_policy.findData("stop"))
    path = batch.save_sequence("Fatture")
    batch.dest_path.setText("")
    batch.pattern.setText("{nome}")
    batch.conflict.setCurrentIndex(0)
    batch.error_policy.setCurrentIndex(0)
    assert batch.load_sequence(path)
    assert batch.dest_path.text() == str(tmp_path / "fatture")
    assert batch.pattern.text() == "{nome}_{contatore:3}"
    assert batch.conflict.currentData() == "skip" and batch.error_policy.currentData() == "stop"
    exported = tmp_path / "f.foliantseq"
    assert batch.export_sequence(str(exported))
    assert be.sequence_output(str(exported))["pattern"] == "{nome}_{contatore:3}"


def test_conflict_ask_each_time(batch, folder, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    for n in ("alfa.pdf", "doc2.pdf"):
        (out / n).write_bytes(b"vecchio")
    asked = []
    answers = iter([("skip", False), ("overwrite", False)])
    monkeypatch.setattr(bu, "ask_conflict", lambda parent, target: asked.append(os.path.basename(target))
                        or next(answers))
    batch.add_paths([str(folder / n) for n in ("alfa.pdf", "doc2.pdf", "doc10.pdf")])
    batch.add_step("compress")
    batch.dest_path.setText(str(out))
    batch.conflict.setCurrentIndex(batch.conflict.findData("ask"))
    report = batch.run(confirm=False)
    assert asked == ["alfa.pdf", "doc2.pdf"]
    assert (out / "alfa.pdf").read_bytes() == b"vecchio"                  # saltato
    assert (out / "doc2.pdf").read_bytes().startswith(b"%PDF")            # sovrascritto
    assert (out / "doc10.pdf").exists()
    assert [r.status for r in report.results] == ["skipped", "ok", "ok"]
    # «applica a tutti» e annullamento
    asked.clear()
    monkeypatch.setattr(bu, "ask_conflict", lambda parent, target: asked.append(1) or ("rename", True))
    batch.run(confirm=False)
    assert len(asked) == 1 and (out / "alfa (1).pdf").exists()
    monkeypatch.setattr(bu, "ask_conflict", lambda parent, target: None)
    assert batch.run(confirm=False) is None


def test_batch_runs_without_blocking_window(batch, folder, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.delenv("FOLIANT_SYNC_TASKS", raising=False)
    batch.add_paths([str(folder)])
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    assert batch.run(confirm=False) is None
    task = batch.task
    assert task is not None and not batch.act_run.isEnabled()
    assert QApplication.activeModalWidget() is None
    assert task.wait(60000)
    QApplication.processEvents()
    assert batch.report is not None and batch.report.counts()["ok"] == 5
    assert batch.act_run.isEnabled() and batch.task is None


def test_parallel_setting_used(batch, folder, tmp_path):
    batch.workers.setValue(min(3, batch.workers.maximum()))
    assert batch.settings().workers == batch.workers.value()
    batch.add_paths([str(folder)])
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    report = batch.run(confirm=False)
    assert all(r.status in ("ok", "warning") for r in report.results) and len(os.listdir(tmp_path / "out")) == 5


def test_control_panel_shows_disk_full(batch):
    control = be.JobControl()
    panel = bu.JobControlPanel(control, batch)
    panel.refresh()
    assert panel.disk_label.isHidden()
    control.disk_full = True
    control.pause()
    panel.refresh()
    assert not panel.disk_label.isHidden() and panel.pause_button.text() == "Riprendi"
    panel.toggle_pause()
    assert not control.paused
    panel.close()


def test_resume_after_cancel_only_unfinished_files(batch, folder, tmp_path):
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"")
    paths = [str(folder / "alfa.pdf"), str(bad), str(folder / "doc2.pdf"), str(folder / "doc10.pdf")]
    batch.add_paths(paths)
    batch.add_step("compress")
    batch.dest_path.setText(str(tmp_path / "out"))
    items = list(batch.items)
    report = be.JobReport([be.FileResult(it.path, status=s) for it, s in
                           zip(items, ("ok", "error", "cancelled", "not_processed"))],
                          [be.Step("compress")], __import__("datetime").datetime.now())
    report.cancelled = True
    batch.report = report
    batch._fill_report()
    assert not batch.resume_button.isHidden()
    again = batch.resume_pending()
    assert sorted(os.path.basename(r.input) for r in again.results) == ["doc10.pdf", "doc2.pdf"]
    assert again.counts()["ok"] == 2
    assert batch.resume_button.isHidden()


def test_empty_file_has_clear_message(tmp_path):
    empty = tmp_path / "vuoto.pdf"
    empty.write_bytes(b"")
    rep = be.run_job(None, [be.analyze_file(str(empty))], be.JobSettings(steps=[be.Step("compress")],
                                                                         destination=str(tmp_path / "o")))
    assert rep.results[0].status == "error" and "vuoto" in rep.results[0].message


def test_english_defaults_and_layout_fit(window):
    """C13: in inglese cartella «processed» e modello «{name}»; nessun campo oltre il bordo,
    anche con caratteri più grandi (come sulle macchine reali)."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QLineEdit
    from foliant.i18n import set_language
    app = QApplication.instance()
    old_font = app.font()
    set_language("en")
    try:
        big = QFont(old_font)
        big.setPointSizeF(max(old_font.pointSizeF(), 10.0) * 1.3)
        app.setFont(big)
        w = bu.BatchWidget(window.ctx)
        assert w.subfolder.text() == "processed" and w.pattern.text() == "{name}"
        window.ctx.set_central_widget(w)
        window.show()
        for size in ((1280, 800), (1024, 700), (1920, 1080)):
            window.resize(*size)
            QApplication.processEvents()
            QApplication.processEvents()
            assert w._stacked == (w.width() < w.STACK_WIDTH)
            for edit in (w.subfolder, w.common_password, w.dest_path, w.pattern):
                area = next(a for a in w._areas if a.isAncestorOf(edit))
                right = edit.mapTo(area.viewport(), edit.rect().topRight()).x()
                assert right <= area.viewport().width(), (size, edit.placeholderText() or edit.text())
                assert edit.mapTo(window, edit.rect().topRight()).x() <= window.width()
    finally:
        app.setFont(old_font)
        set_language("it")
