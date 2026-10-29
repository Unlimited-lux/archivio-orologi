"""Interfaccia del confronto (CMP-01, 05, 06, 07, 08, 10) con la finestra reale."""

from __future__ import annotations

import os

import pymupdf
import pytest
from PySide6.QtCore import Qt

from foliant.api import OperationCancelled
from foliant.modules.tools import compare_engine as ce
from foliant.modules.tools import compare_ui as cu
from foliant.modules.tools import report as rp

from .conftest import page_lines
from tests.conftest import perf_limit


def controller(window):
    return window.ctx.tools_module.compare


def start(window, a, b, **opts):
    sa = cu.Source.from_file(a)
    sb = cu.Source.from_file(b)
    ia = ce.SideInput(sa.data, sa.label, sa.path)
    ib = ce.SideInput(sb.data, sb.label, sb.path)
    widget = controller(window).run(ia, ib, sa, sb, ce.CompareOptions(**opts))
    assert widget is not None
    widget.fit("width")
    return widget


def test_mode_and_actions_registered(window):
    mode = window.ctx.mode("strumenti")
    assert mode is not None and mode.order == 100
    texts = [a.text() for a in mode.toolbar.actions()]
    assert "Confronta documenti" in texts and "Operazioni in blocco" in texts


def test_ribbon_groups_like_excel(window):
    """Scheda Strumenti in gruppi con nome (Confronta | Automazione); le voci dell'ex menu ☰ restano solo
    nella barra: nessuna azione doppia e niente in File › Altri comandi."""
    module = window.ctx.tools_module
    acts = module.actions
    bar = module.mode.toolbar
    assert bar.groups() == [("Confronta", [acts["compare"], acts["show"], acts["session"], acts["text"],
                                           acts["revisions"]]),
                            ("Automazione", [acts["batch"]])]
    assert bar.group_title(acts["compare"]) == "Confronta"
    assert bar.group_title(acts["batch"]) == "Automazione"
    assert acts["session"].text() == "Apri sessione di confronto" and acts["session"].iconText() == "Apri sessione"
    assert all(window.backstage.page_of(a) is None for a in acts.values())


def test_module_no_longer_uses_the_old_menu():
    from pathlib import Path

    import foliant.modules.tools as package
    folder = Path(package.__file__).parent
    assert not [p.name for p in folder.glob("*.py") if "add_menu_action" in p.read_text(encoding="utf-8")]


def test_setup_proposes_active_document_and_swaps(window, pdf_builder):
    a = pdf_builder([page_lines(1)])
    b = pdf_builder([page_lines(2), page_lines(3)])
    va = window.open_path(a)
    vb = window.open_path(b)
    window.tabs.setCurrentWidget(va)
    dlg = cu.CompareSetupDialog(window.ctx, window)
    assert dlg.side_a.source.label == "doc1.pdf"
    assert dlg.side_b.source.label == "doc2.pdf"
    assert dlg.start.isEnabled()
    dlg.swap()
    assert dlg.side_a.source.label == "doc2.pdf" and dlg.side_b.source.label == "doc1.pdf"
    assert dlg.side_a.source.page_count == 2
    dlg.side_b.all_pages.setChecked(False)
    dlg.side_a.all_pages.setChecked(False)
    dlg.side_a.first.setValue(2)
    ia, ib, _sa, _sb = dlg.inputs()
    assert ia.page_range == (1, 1) and ib.page_range == (0, 0)
    dlg.mode_buttons[ce.MODE_TEXT].setChecked(True)
    dlg.ignore_case.setChecked(True)
    o = dlg.options()
    assert o.mode == ce.MODE_TEXT and o.ignore_case
    dlg.preset.setCurrentIndex(dlg.preset.findData("scan"))
    assert dlg.options().preset == "scan" and dlg.options().sensitivity == "low"
    dlg.preset.setCurrentIndex(dlg.preset.findData("drawing"))
    assert dlg.options().mode == ce.MODE_VISUAL and dlg.options().sensitivity == "high"
    dlg.close()
    del vb


def test_unsaved_document_is_labelled(window, pdf_builder):
    a = pdf_builder([page_lines(1)])
    view = window.open_path(a)
    with view.document.change("prova") as pdf:
        pdf[0].insert_text((72, 300), "nuovo testo", fontsize=12)
    src = cu.Source.from_view(view)
    assert src.unsaved
    assert "nuovo testo" in pymupdf.open(stream=src.data).load_page(0).get_text()
    view.document._saved_state = view.document._state   # niente richiesta di salvataggio alla chiusura


def test_encrypted_file_asks_password_before_processing(window, encrypted_pdf, monkeypatch):
    answers = iter(["sbagliata", None])
    asked = []

    def fake(parent, name, wrong):
        asked.append(wrong)
        return next(answers)
    monkeypatch.setattr(cu, "ask_password", fake)
    assert cu.Source.from_file(encrypted_pdf) is None
    assert asked == [False, True]
    monkeypatch.setattr(cu, "ask_password", lambda *a: "segreta")
    src = cu.Source.from_file(encrypted_pdf)
    assert src is not None and src.password == "segreta" and src.page_count == 1


def test_comparison_session_shown_and_closed(window, pdf_builder):
    a = pdf_builder([["Il canone è di 100 euro"]])
    b = pdf_builder([["Il canone è di 120 euro"]])
    w = start(window, a, b)
    assert window.ctx.central_widget() is w
    assert window.ctx.current_mode() == "strumenti"
    assert w.panel.model.rowCount() == 1
    w.close_session()
    assert window.ctx.central_widget() is None
    assert controller(window).widget is None


def _many(n_add, n_del):
    """Righe di A e B con n_add aggiunte e n_del eliminazioni separate da righe comuni."""
    a, b = [], []
    for i in range(n_add + n_del + 1):
        a.append(f"riga comune numero {i}")
        b.append(f"riga comune numero {i}")
        if i < n_del:
            a.append(f"tolta {i}")
        elif i < n_del + n_add:
            b.append(f"aggiunta {i - n_del}")
    return a, b


def test_filters_counters_and_hidden_message(window, pdf_builder):
    la, lb = _many(5, 3)
    w = start(window, pdf_builder([la]), pdf_builder([lb]), mode=ce.MODE_TEXT)
    counts = w.result.counts()
    assert counts[ce.ADDED] == 5 and counts[ce.DELETED] == 3
    w.panel.filters[ce.DELETED].setChecked(False)
    assert w.panel.model.rowCount() == 5
    assert w.panel.counter.text() == "Visibili 5 di 8"
    assert all(d.kind != ce.DELETED for d in w.pane_a.diffs)
    for cb in w.panel.filters.values():
        cb.setChecked(False)
    assert w.panel.model.rowCount() == 0
    assert "8 differenze nascoste dai filtri" in w.panel.empty.text()
    assert "identici" not in w.panel.empty.text()
    w.panel.show_all()
    assert w.panel.model.rowCount() == 8
    w.panel.search.setText("aggiunta 3")
    assert w.panel.model.rowCount() == 1


def test_identical_message(window, pdf_builder):
    a = pdf_builder([page_lines(1)])
    w = start(window, a, a)
    assert w.panel.model.rowCount() == 0
    assert "identici secondo i criteri selezionati" in w.panel.empty.text()
    assert "Tipo di confronto" in w.panel.empty.text()


def test_next_difference_visits_each_once(window, pdf_builder):
    la, lb = _many(25, 0)
    w = start(window, pdf_builder([la[:14], la[14:]]), pdf_builder([lb[:14], lb[14:27], lb[27:]]),
              mode=ce.MODE_TEXT)
    diffs = w.panel.model.visible
    n = len(diffs)
    assert n >= 3
    visited = []
    for _i in range(n):
        assert w.panel.next()
        visited.append(w.panel.current().id)
    assert visited == [d.id for d in diffs]
    ended = []
    w.panel.endReached.connect(lambda: ended.append(True))
    assert not w.panel.next()
    assert ended and "Fine dell'elenco" in w.panel.message.text()
    assert w.panel.next() and w.panel.current().id == diffs[0].id


def test_click_on_item_shows_difference_in_both_panes(window, pdf_builder):
    pages_a = [page_lines(i) for i in range(1, 6)] + [["Il prezzo è 100 euro"]]
    pages_b = [page_lines(i) for i in range(1, 6)] + [["Il prezzo è 150 euro"]]
    w = start(window, pdf_builder(pages_a), pdf_builder(pages_b), mode=ce.MODE_TEXT)
    d = w.panel.model.visible[0]
    w.panel.list.setCurrentIndex(w.panel.model.index(0))
    assert w.pane_a.view.current_page == 5 and w.pane_b.view.current_page == 5
    for pane in (w.pane_a, w.pane_b):
        r = pane.view.page_to_view(5, pymupdf.Rect(d.locations(pane.side)[0][1:5]))
        assert pane.view.viewport().rect().contains(r.center().toPoint())
    assert w.pane_a.selected is d


def test_pick_in_pane_selects_everywhere(window, pdf_builder):
    w = start(window, pdf_builder([["alfa beta gamma"]]), pdf_builder([["alfa delta gamma"]]), mode=ce.MODE_TEXT)
    d = w.result.differences[0]
    loc = d.locations("b")[0]
    hit = w.pane_b.diff_at(loc[0], pymupdf.Point((loc[1] + loc[3]) / 2, (loc[2] + loc[4]) / 2))
    assert hit is d
    w.pane_b.picked.emit(d)
    assert w.panel.current() is d and w.pane_a.selected is d


def test_synchronized_scroll_and_zoom(window, pdf_builder):
    a = pdf_builder([page_lines(i) for i in range(1, 7)])
    b = pdf_builder([page_lines(i) for i in (1, 2, 3)] + [page_lines(20), page_lines(21)]
                    + [page_lines(i) for i in (4, 5, 6)])
    w = start(window, a, b, mode=ce.MODE_TEXT)
    va, vb = w.pane_a.view, w.pane_b.view
    cu.set_view_position(va, 2.2)
    cu.set_view_position(va, 3.05)
    assert va.current_page == 3
    assert vb.current_page == 5
    # sincronizzazione disattivata: B non si muove
    w.act_sync.setChecked(False)
    before = vb.verticalScrollBar().value()
    cu.set_view_position(va, 0.5)
    assert vb.verticalScrollBar().value() == before
    w.act_sync.trigger()   # riattiva
    assert w.act_sync.isChecked()
    assert vb.current_page == 0
    va.set_zoom(2.0)
    assert abs(vb.zoom - 2.0) < 1e-6


def test_narrow_window_switches_to_vertical(window, pdf_builder):
    a = pdf_builder([page_lines(1)])
    w = start(window, a, pdf_builder([page_lines(2)]))
    from PySide6.QtWidgets import QApplication
    window.resize(700, 700)
    QApplication.processEvents()
    assert w.width() < 900, w.width()
    assert w.splitter.orientation() == Qt.Orientation.Vertical
    window.resize(1400, 800)
    QApplication.processEvents()
    assert w.splitter.orientation() == Qt.Orientation.Horizontal


def test_overlay_view_and_session_roundtrip(window, pdf_builder, tmp_path, qtbot):
    a = pdf_builder([page_lines(1)])
    b = pdf_builder([page_lines(1)], extra=lambda p, i: p.draw_line((72, 400), (500, 400), width=2))
    w = start(window, a, b)
    w.set_layout("overlay")
    assert w.stack.currentWidget() is w.overlay
    arr, _scale = w.overlay.composite()
    blue = (arr[:, :, 2] > 180) & (arr[:, :, 0] < 90)
    assert blue.any()
    w.overlay.opacity_b.setValue(0)
    arr, _scale = w.overlay.composite()
    assert not ((arr[:, :, 2] > 180) & (arr[:, :, 0] < 90)).any()
    w.overlay.dx.setValue(3.0)
    assert w.overlay.offsets[(0, 0)] == (3.0, 0.0)
    w.overlay.canvas.nudged.emit(0.1, 0.0)
    assert abs(w.overlay.offsets[(0, 0)][0] - 3.1) < 1e-9
    w.overlay.alternate.setChecked(True)
    assert w.overlay.timer.interval() >= 1000 / 3
    w.overlay.alternate.setChecked(False)
    path = w.save_session(str(tmp_path / "sessione.foliantcmp"))
    assert path and os.path.exists(path)
    w2 = controller(window).open_session(path)
    assert w2 is not None and w2 is not w
    assert abs(w2.overlay.offsets[(0, 0)][0] - 3.1) < 1e-9
    w2.overlay.angle.setValue(1.5)
    w2.overlay.scale.setValue(98.0)
    assert w2.overlay.offsets[(0, 0)][2:] == (1.5, 98.0)
    arr, _s = w2.overlay.composite()
    assert arr is not None
    path2 = w2.save_session(str(tmp_path / "sessione2.foliantcmp"))
    w3 = controller(window).open_session(path2)
    assert w3.overlay.offsets[(0, 0)][2:] == (1.5, 98.0)
    assert abs(w3.overlay.angle.value() - 1.5) < 1e-9
    assert w2.layout_mode() == "overlay"
    w2.overlay.reset_alignment()
    assert (0, 0) not in w2.overlay.offsets


def test_export_report_and_cancel_leaves_no_file(window, pdf_builder, tmp_path, monkeypatch):
    w = start(window, pdf_builder([["alfa beta"]]), pdf_builder([["alfa gamma"]]))
    out = tmp_path / "rapporto.pdf"
    assert w.export_report(str(out)) == str(out)
    doc = pymupdf.open(str(out))
    assert "Rapporto di confronto" in doc[0].get_text()
    doc.close()

    def cancelled(*a, **k):
        raise OperationCancelled()
    monkeypatch.setattr(rp, "build_report", cancelled)
    out2 = tmp_path / "annullato.pdf"
    assert w.export_report(str(out2)) is None
    assert not out2.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
    annotated = tmp_path / "b_annotato.pdf"
    assert w.export_annotated(str(annotated)) == str(annotated)
    assert annotated.exists()


def test_failed_comparison_shows_error_and_no_session(window, pdf_builder, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    a = pdf_builder([["x"]])
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"%PDF-1.4\n\x00\x00 rotto")
    sa = cu.Source(label="a", path=a, data=open(a, "rb").read())
    sb = cu.Source(label="b", path=str(bad), data=bad.read_bytes())
    res = controller(window).run(ce.SideInput(sa.data, "a"), ce.SideInput(sb.data, "b"), sa, sb, ce.CompareOptions())
    assert res is None and errors
    assert window.ctx.central_widget() is None


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_screenshot_renders(window, pdf_builder, tmp_path, theme_name):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    theme.apply(QApplication.instance(), theme_name)
    w = start(window, pdf_builder([["Il canone è di 100 euro"]]), pdf_builder([["Il canone è di 120 euro"]]))
    w.panel.next()
    for pane in (w.pane_a, w.pane_b):
        pane.view.flush_rendering()
    img = window.grab()
    assert img.save(str(tmp_path / f"confronto_{theme_name}.png"))
    theme.apply(QApplication.instance(), "light")


# ----------------------------------------------------------------- CMP-11
def test_review_statuses_filter_report_and_session(window, pdf_builder, tmp_path, monkeypatch):
    import hashlib
    import json
    la, lb = _many(3, 2)
    a, b = pdf_builder([la]), pdf_builder([lb])
    hashes = [hashlib.sha256(open(p, "rb").read()).hexdigest() for p in (a, b)]
    w = start(window, a, b, mode=ce.MODE_TEXT)
    diffs = w.result.differences
    assert len(diffs) == 5
    w.panel.set_status("accepted", diffs[:3])
    w.panel.set_status("rejected", [diffs[3]])
    w.panel.set_note("controllare con il cliente", [diffs[3]])
    w.panel.status_filter.setCurrentIndex(w.panel.status_filter.findData("rejected"))
    assert w.panel.model.visible == [diffs[3]]
    assert "3 accettate" in w.panel.status_counts.text()
    w.panel.status_filter.setCurrentIndex(0)
    out = tmp_path / "rapporto.pdf"
    w.export_report(str(out), apply_filters=False)
    text = pymupdf.open(str(out))[0].get_text()
    assert text.count("Accettata") == 3 and text.count("Rifiutata") == 1
    assert "controllare con il cliente" in text
    assert [hashlib.sha256(open(p, "rb").read()).hexdigest() for p in (a, b)] == hashes
    path = w.save_session(str(tmp_path / "rev.foliantcmp"))
    w.panel.filters[ce.ADDED].setChecked(False)
    w2 = controller(window).open_session(path)
    assert [d.status for d in w2.result.differences] == ["accepted"] * 3 + ["rejected", ""]
    assert w2.result.differences[3].note == "controllare con il cliente"
    # B cambiato dopo il salvataggio: stati mantenuti per le differenze ancora presenti
    lb2 = [x for x in lb if x != "aggiunta 1"]   # la differenza rifiutata scompare
    from .conftest import build_pdf
    build_pdf(b, [lb2])
    infos = []
    monkeypatch.setattr(window.ctx, "notify", lambda m, *a, **k: infos.append(("", m)))
    w3 = controller(window).open_session(path)
    assert any("aggiunta 1" in m for _t, m in infos)
    statuses = {d.text_b or d.text_a: d.status for d in w3.result.differences}
    assert statuses == {"tolta 0": "accepted", "tolta 1": "accepted", "aggiunta 0": "accepted", "aggiunta 2": ""}
    # schema più recente rifiutato
    data = json.load(open(path, encoding="utf-8"))
    data["version"] = 99
    newer = tmp_path / "nuova.foliantcmp"
    newer.write_text(json.dumps(data), encoding="utf-8")
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    assert controller(window).open_session(str(newer)) is None
    assert errors and "99" in errors[0]


def test_text_and_revision_actions(window, pdf_builder):
    module = window.ctx.tools_module
    assert module.actions["text"].isEnabled()
    dlg = module.compare_text()
    assert dlg.isVisible()
    dlg.close()
    view = window.open_path(pdf_builder([page_lines(1)]))
    assert module.actions["revisions"].isEnabled()
    rd = module.show_revisions()
    assert rd.isVisible()
    rd.close()
    del view


def test_twenty_thousand_differences_list(qtbot):
    import time
    opts = ce.CompareOptions()
    info = ce.SideInfo("a", None, "0" * 64, "", 400, list(range(400)))
    diffs = [ce.Difference(ce.ADDED if i % 2 else ce.DELETED, text_a=f"parola {i}", text_b=f"parola {i}",
                           rects_a=[(i // 50, 72, 100 + (i % 50) * 14, 200, 110 + (i % 50) * 14)],
                           rects_b=[(i // 50, 72, 100 + (i % 50) * 14, 200, 110 + (i % 50) * 14)], id=i + 1)
             for i in range(20000)]
    result = ce.CompareResult(info, info, opts, diffs)
    panel = cu.DiffPanel()
    qtbot.addWidget(panel)
    panel.resize(320, 700)
    panel.show()
    t = time.monotonic()
    panel.set_result(result)
    assert time.monotonic() - t < perf_limit(1.0)
    assert panel.model.rowCount() == 20000
    t = time.monotonic()
    panel.list.scrollToBottom()
    panel.list.repaint()
    panel.filters[ce.DELETED].setChecked(False)
    assert time.monotonic() - t < perf_limit(1.0)
    assert panel.counter.text() == "Visibili 10000 di 20000"


def test_draw_exclusion_area_and_recompute(window, pdf_builder, monkeypatch):
    def stamp(date):
        def extra(page, i):
            page.insert_text((420, 760), f"Stampato il {date}", fontsize=9)
        return extra
    a = pdf_builder([page_lines(1), page_lines(2)], extra=stamp("27/10"))
    b = pdf_builder([page_lines(1), page_lines(2)], extra=stamp("28/10"))
    w = start(window, a, b, mode=ce.MODE_TEXT)
    assert len(w.result.differences) == 2
    w.act_exclude.trigger()
    assert isinstance(w.pane_a.view.tool, cu.ExclusionTool)
    monkeypatch.setattr(cu, "ask_exclusion_scope", lambda parent: "all")
    w.pane_a.area_drawn.emit(0, pymupdf.Rect(400, 740, 590, 780))
    w2 = controller(window).widget
    assert w2 is not w
    assert w2.result.differences == []
    assert w2.result.options.exclusions == [[-1, 400.0, 740.0, 590.0, 780.0]]
    assert "Aree escluse: 1" in w2.panel.empty.text()
    assert not isinstance(w2.pane_a.view.tool, cu.ExclusionTool)


def test_uncertain_filter(qtbot):
    info = ce.SideInfo("a", None, "0" * 64, "", 1, [0])
    diffs = [ce.Difference(ce.MODIFIED, text_a="1250", text_b="1350", rects_a=[(0, 1, 1, 5, 5)],
                           rects_b=[(0, 1, 1, 5, 5)], id=1, uncertain=True),
             ce.Difference(ce.ADDED, text_b="euro", rects_b=[(0, 10, 1, 15, 5)], id=2)]
    panel = cu.DiffPanel()
    qtbot.addWidget(panel)
    panel.set_result(ce.CompareResult(info, info, ce.CompareOptions(), diffs))
    assert not panel.uncertain_only.isHidden()
    assert "incerta" in panel.model.data(panel.model.index(0))
    panel.uncertain_only.setChecked(True)
    assert panel.model.visible == [diffs[0]]


def test_tab_menu_compare_with(window, pdf_builder, monkeypatch):
    from PySide6.QtWidgets import QMenu
    view = window.open_path(pdf_builder([page_lines(1)]))
    module = window.ctx.tools_module
    assert module.tab_menu in window.tab_menu_providers
    called = []
    monkeypatch.setattr(module, "start_compare", lambda v=None: called.append(v))
    menu = QMenu()
    for provider in window.tab_menu_providers:
        provider(view, menu)
    act = next(a for a in menu.actions() if a.text() == "Confronta con…")
    act.trigger()
    assert called == [view]


def test_comparison_runs_without_blocking_window(window, pdf_builder, monkeypatch):
    """CMP-01 flusso 5: il confronto non apre finestre modali e la finestra resta usabile."""
    from PySide6.QtWidgets import QApplication
    monkeypatch.delenv("FOLIANT_SYNC_TASKS", raising=False)
    a = pdf_builder([page_lines(i) for i in range(1, 30)])
    b = pdf_builder([page_lines(i) for i in range(2, 31)])
    sa, sb = cu.Source.from_file(a), cu.Source.from_file(b)
    shown = []
    ctl = controller(window)
    ret = ctl.run(ce.SideInput(sa.data, "a"), ce.SideInput(sb.data, "b"), sa, sb, ce.CompareOptions(dpi=100),
                  then=shown.append)
    assert ret is None and ctl.task is not None
    assert QApplication.activeModalWidget() is None
    assert ctl.task.wait(60000)
    QApplication.processEvents()
    assert shown and shown[0] is ctl.widget
    assert window.ctx.central_widget() is ctl.widget


def test_outcomes_use_non_blocking_notifications(window, pdf_builder, tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(window.ctx, "notify", lambda text, actions=(), **k: notes.append((text, actions)))
    monkeypatch.setattr(window.ctx, "info", lambda *a: (_ for _ in ()).throw(AssertionError("finestra modale")))
    w = start(window, pdf_builder([["alfa beta"]]), pdf_builder([["alfa gamma"]]))
    w.export_report(str(tmp_path / "r.pdf"))
    w.save_session(str(tmp_path / "s.foliantcmp"))
    texts = [t for t, _a in notes]
    assert any("Rapporto salvato" in t for t in texts) and any("Sessione salvata" in t for t in texts)
    actions = next(a for t, a in notes if "Rapporto salvato" in t)
    assert actions and actions[0][0] == "Apri"
    window.ctx.tools_module.compare.close()
    window.ctx.tools_module.show_compare()
    assert any("Nessun confronto aperto" in t for t, _a in notes)
