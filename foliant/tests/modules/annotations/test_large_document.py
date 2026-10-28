"""Verifica su un documento grande e realistico: contratto di 30 pagine con 300 commenti
(evidenziazioni, note, caselle, forme, disegni, risposte e stati). Limiti di tempo larghi: servono a
scoprire regressioni di complessità (quadratiche), non a misurare la macchina."""

import random
import time

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from foliant.modules.annotations import engine, ui_actions


def build(path):
    random.seed(7)
    d = pymupdf.open()
    words = ("il fornitore si impegna a consegnare la merce entro i termini concordati nel presente contratto "
             "salvo cause di forza maggiore debitamente documentate e comunicate per iscritto").split()
    for p in range(30):
        page = d.new_page(width=595, height=842)
        page.insert_text((72, 60), f"Articolo {p+1}", fontsize=14)
        y = 90
        while y < 780:
            page.insert_text((72, y), " ".join(random.choice(words) for _ in range(12)), fontsize=10)
            y += 14
    st = engine.Style()
    kinds = 0
    for i in range(300):
        p = i % 30
        page = d[p]
        r = random.random()
        x, y = random.uniform(80, 450), random.uniform(100, 750)
        if r < 0.35:
            ws = [w for w in page.get_text("words") if abs(w[1] - y) < 20][:4]
            q = [pymupdf.Rect(w[:4]).quad for w in ws] or [pymupdf.Rect(x, y, x+80, y+10).quad]
            xr = engine.create_markup(d, p, engine.HIGHLIGHT, q, st, "Anna", f"Verificare la clausola {i}")
        elif r < 0.6:
            xr = engine.create_note(d, p, (x, y), f"Nota {i}: chiedere chiarimenti al fornitore", st, "Bruno")
        elif r < 0.75:
            xr = engine.create_freetext(d, p, (x, y, x+120, y+30), f"Casella {i}", st, "Carla")
        elif r < 0.9:
            xr = engine.create_shape(d, p, engine.RECT, [(x, y), (x+60, y+30)], st, "Anna", f"Area {i}")
        else:
            xr = engine.create_ink(d, p, [[(x, y), (x+20, y+5), (x+40, y)]], st, "Bruno")
        if i % 5 == 0:
            engine.add_reply(d, p, xr, "D'accordo", "Carla")
        if i % 7 == 0:
            engine.set_status(d, p, xr, engine.STATE_ACCEPTED, "Anna")
    d.save(str(path))


@pytest.fixture
def contract(tmp_path):
    path = tmp_path / "contratto.pdf"
    build(path)
    return str(path)


def _timed(fn):
    t = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t


def test_large_contract_panel_and_roundtrip(comments, contract, tmp_path, monkeypatch):
    window, _v, module = comments
    view = window.open_path(contract)
    window.set_mode("commenta")
    ctrl = module.ctrl
    panel = module.comments
    _r, t = _timed(lambda: (panel.reload(), QApplication.processEvents()))
    assert t < 5
    infos = ctrl.annotations()
    assert len(infos) == 300
    replies = sum(len(i.replies) for i in infos)
    accepted = sum(i.status == engine.STATE_ACCEPTED for i in infos)
    assert replies == 60 and accepted == 43
    for key in ("date_desc", "author", "type", "status", "page"):
        _r, t = _timed(lambda k=key: panel.sort.setCurrentIndex(panel.sort.findData(k)))
        assert t < 2
    before = len(panel.items())
    panel.search.setText("fornitore")
    QApplication.processEvents()
    assert 0 < len(panel.items()) < before
    panel.search.setText("")
    # esportazioni in background (run_task) e reimportazione: risposte e stati conservati
    titles = []
    real = window.ctx.run_task
    monkeypatch.setattr(window.ctx, "run_task", lambda title, fn, *a, **k: titles.append(title) or real(title, fn, *a, **k))
    for fmt in ("xfdf", "fdf"):
        out = tmp_path / f"c.{fmt}"
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, o=out, **k: str(o))
        _r, t = _timed(lambda f=fmt: ui_actions.export_comments(ctrl, f))
        assert t < 5 and out.exists()
        ctrl.apply("d", engine.delete_all)
        _r, t = _timed(lambda o=out: ui_actions.import_comments(ctrl, [str(o)]))
        assert t < 8
        again = ctrl.annotations()
        assert len(again) == 300
        assert sum(len(i.replies) for i in again) == 60
        assert sum(i.status == engine.STATE_ACCEPTED for i in again) == 43
    for fmt in ("pdf", "pdf-page", "csv", "html", "txt"):
        out = tmp_path / f"riepilogo-{fmt}"
        monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, o=out, **k: str(o))
        _r, t = _timed(lambda f=fmt: ui_actions.export_summary(ctrl, f))
        assert t < 20 and out.exists() and out.stat().st_size > 0
    assert titles.count("Preparazione del riepilogo…") == 5
    assert titles.count("Esportazione dei commenti…") == 2 and titles.count("Lettura dei commenti…") == 2
    # modifica, annullamento e salvataggio restano rapidi
    _r, t = _timed(lambda: ctrl.apply("n", engine.create_note, 3, (100, 100), "x", engine.Style(), "A"))
    assert t < 3
    view.document.save(str(tmp_path / "salvato.pdf"))
    assert len(engine.list_annotations(pymupdf.open(str(tmp_path / "salvato.pdf")))) == 301
