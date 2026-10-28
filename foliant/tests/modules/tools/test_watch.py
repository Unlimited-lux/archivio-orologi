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
    w = wt.FolderWatcher(cfg, clock)
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
    w = wt.FolderWatcher(cfg)
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


def _tree(root) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[p] = fh.read()
    return out


def test_watches_last_for_the_session_only(tmp_path):
    """Riservatezza: i monitoraggi valgono finché l'app è aperta; un nuovo servizio (riavvio)
    non ne conserva nessuno, e nel profilo non resta né configurazione né registro."""
    cfg = _cfg(tmp_path, stable_seconds=0)
    service = wt.WatchService()
    assert service.add(cfg) == []
    for i in range(3):   # file già presenti: elaborati alla prima scansione
        build_pdf(os.path.join(cfg.folder, f"f{i}.pdf"), [page_lines(i)])
    service.tick(wait=True)
    assert sorted(os.listdir(cfg.destination)) == ["f0.pdf", "f1.pdf", "f2.pdf"]
    w = service.watchers[cfg.id]
    assert len(w.log) == 3 and len(w.processed) == 3        # registro in memoria per la sessione
    assert "f1.pdf" in w.report().to_csv()
    service.stop()
    assert wt.WatchService().configs() == []                # riavvio: nessun monitoraggio ricordato
    profile = os.environ["XDG_CONFIG_HOME"]
    leaks = [p for p, data in _tree(profile).items() if b"entrata" in data or b"f1.pdf" in data]
    assert leaks == [] and not any("monitor" in p for p in _tree(profile))


def test_export_and_import_watch_configuration(tmp_path):
    """L'unico modo di riusare i monitoraggi: un file scelto dall'utente."""
    cfg = _cfg(tmp_path, stable_seconds=3, originals="move")
    cfg.steps = [be.Step("watermark", {"text": "ARRIVATO"})]
    service = wt.WatchService()
    assert service.add(cfg) == []
    service.stop()
    path = str(tmp_path / "scelto" / "monitor.json")
    os.makedirs(os.path.dirname(path))
    wt.export_watches(path, service.configs())
    back = wt.import_watches(path)
    assert len(back) == 1 and back[0].id != cfg.id
    b = back[0]
    assert (b.folder, b.destination, b.originals, b.stable_seconds) == (cfg.folder, cfg.destination, "move", 3)
    assert b.steps[0].action == "watermark" and b.steps[0].params["text"] == "ARRIVATO"
    (tmp_path / "altro.json").write_text("[1, 2]", encoding="utf-8")
    import pytest
    from foliant.api import DocumentError
    with pytest.raises(DocumentError):
        wt.import_watches(str(tmp_path / "altro.json"))


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
    # riservatezza: nulla nel profilo, né configurazione né registro dei file elaborati
    profile = os.environ["XDG_CONFIG_HOME"]
    assert [p for p, data in _tree(profile).items() if b"x.pdf" in data or str(tmp_path).encode() in data] == []
    exported = str(tmp_path / "monitor-esportato.json")
    window.ctx.ask_save_path = lambda *a, **k: exported
    dlg.export_config_dialog()
    assert os.path.exists(exported)
    dlg.remove_selected()
    assert dlg.table.rowCount() == 0
    window.ctx.ask_open_paths = lambda *a, **k: [exported]
    dlg.import_config_dialog()
    assert dlg.table.rowCount() == 1
    for wid in list(service.watchers):
        service.remove(wid)
    assert service.timer is None
    dlg.close()
    shutil.rmtree(tmp_path / "in", ignore_errors=True)
