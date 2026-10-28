"""Cartelle monitorate (BAT-21)."""

from __future__ import annotations

import os
import shutil

import pymupdf

from foliant.modules.tools import engine as be
from foliant.modules.tools import watch as wt

from .conftest import build_pdf, page_lines


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cfg(tmp_path, **kw):
    folder = tmp_path / "entrata"
    folder.mkdir(exist_ok=True)
    return wt.WatchConfig(folder=str(folder), steps=[be.Step("compress")], destination=str(tmp_path / "uscita"),
                          **kw)


def test_copied_pdf_processed_after_stable_size(tmp_path):
    cfg = _cfg(tmp_path, originals="move")
    clock = Clock()
    w = wt.FolderWatcher(cfg, str(tmp_path / "stato"), clock)
    build_pdf(os.path.join(cfg.folder, "perizia.pdf"), [page_lines(1)])
    assert w.scan() == []                       # appena comparso: si attende la fine della copia
    clock.t += 6
    results = w.poll()
    assert [r.status for r in results] == ["ok"]
    assert os.listdir(cfg.destination) == ["perizia.pdf"]
    assert os.listdir(os.path.join(cfg.folder, wt.ORIGINALS_DIR)) == ["perizia.pdf"]
    assert w.processed_count == 1


def test_damaged_file_goes_to_errors_and_monitoring_continues(tmp_path):
    cfg = _cfg(tmp_path, stable_seconds=0)
    w = wt.FolderWatcher(cfg, str(tmp_path / "stato"))
    (tmp_path / "entrata" / "rotto.pdf").write_bytes(b"%PDF-1.4 rotto")
    build_pdf(os.path.join(cfg.folder, "buono.pdf"), [page_lines(2)])
    results = {os.path.basename(r.input): r for r in w.poll()}
    assert results["rotto.pdf"].status == "error"
    assert os.listdir(os.path.join(cfg.folder, wt.ERRORS_DIR)) == ["rotto.pdf"]
    assert "rotto.pdf" in w.last_error
    assert results["buono.pdf"].status == "ok"
    # «lascia gli originali»: lo stesso file non viene rielaborato
    assert w.poll() == []
    assert "rotto.pdf" in w.report().to_csv()


def test_destination_inside_watched_folder_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.destination = os.path.join(cfg.folder, "risultati")
    assert any("destinazione" in e.lower() for e in wt.validate_watch(cfg))
    cfg.destination = cfg.folder
    assert wt.validate_watch(cfg)


def test_files_copied_while_closed_processed_at_restart(tmp_path):
    cfg = _cfg(tmp_path, stable_seconds=0)
    conf = str(tmp_path / "conf" / "monitor.json")
    service = wt.WatchService(conf, str(tmp_path / "stato"))
    assert service.add(cfg) == []
    service.stop()
    for i in range(3):   # copiati ad applicazione chiusa
        build_pdf(os.path.join(cfg.folder, f"f{i}.pdf"), [page_lines(i)])
    restarted = wt.WatchService(conf, str(tmp_path / "stato"))
    assert [c.folder for c in restarted.configs()] == [cfg.folder]
    restarted.tick(wait=True)
    assert sorted(os.listdir(cfg.destination)) == ["f0.pdf", "f1.pdf", "f2.pdf"]
    restarted.stop()


def test_watch_dialog_from_batch_panel(window, tmp_path):
    batch = window.ctx.tools_module.show_batch()
    batch.add_step("watermark", {"text": "ARRIVATO"})
    dlg = batch.show_watches()
    (tmp_path / "in").mkdir()
    assert dlg.add_watch(str(tmp_path / "in"), str(tmp_path / "in")) != []      # ciclo: rifiutato
    assert dlg.add_watch(str(tmp_path / "in"), str(tmp_path / "out")) == []
    assert dlg.table.rowCount() == 1
    service = window.ctx.tools_module.watch_service
    w = next(iter(service.watchers.values()))
    w.cfg.stable_seconds = 0
    build_pdf(tmp_path / "in" / "x.pdf", [page_lines(1)])
    service.tick(wait=True)
    assert "ARRIVATO" in pymupdf.open(str(tmp_path / "out" / "x.pdf"))[0].get_text()
    dlg.table.selectRow(0)
    dlg.toggle_selected()
    assert not w.cfg.enabled and service.timer is None
    dlg.remove_selected()
    assert dlg.table.rowCount() == 0
    dlg.close()
    shutil.rmtree(tmp_path / "in", ignore_errors=True)
