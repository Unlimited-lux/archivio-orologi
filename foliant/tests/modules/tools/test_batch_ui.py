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
