"""VIS-49 — Ripristina e ricarica."""

from __future__ import annotations

import os
import time

import pymupdf
import pytest

from foliant.api import ENGINE_LOCK
from foliant.core import platform
from foliant.modules.viewer import reload_engine as E


def _pdf_bytes(pages: int, tag: str = "A") -> bytes:
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 100), f"{tag} pagina {i + 1}", fontsize=20)
    data = d.tobytes()
    d.close()
    return data


def _write(path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
    # garantisce un'impronta diversa anche su file system a bassa risoluzione
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def _text(view, index: int) -> str:
    with ENGINE_LOCK:
        return view.document.pdf[index].get_text()


@pytest.fixture
def ctl(viewer_window):
    from foliant.modules.viewer import reload as R
    c = viewer_window.ctx.viewer_parts.get("reload") or R.install(viewer_window.ctx)
    c.debounce_ms = 150
    c.set_poll_interval(50)
    return c


@pytest.fixture
def pdf30(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(_pdf_bytes(30, "A"))
    return str(path)


# ------------------------------------------------------------------ logica pura
def test_engine_decisions(tmp_path):
    assert E.decide("auto", False) == E.RELOAD
    assert E.decide("ask", False) == E.ASK
    assert E.decide("never", False) == E.IGNORE
    assert E.decide("never", True) == E.CONFLICT
    assert E.normalize_mode("boh") == "auto"
    assert E.clamp_page(11, 5) == 4 and E.clamp_page(-3, 5) == 0 and E.clamp_page(2, 0) == 0
    p = tmp_path / "x.pdf"
    assert E.file_stamp(p) == E.MISSING
    data = _pdf_bytes(2)
    p.write_bytes(data[: len(data) // 2])
    assert not E.looks_complete(p)
    p.write_bytes(data)
    assert E.looks_complete(p)
    assert E.file_stamp(p).exists


# ------------------------------------------------------------------ criteri di accettazione
def test_revert_discards_changes(viewer_window, ctl, pdf30, monkeypatch):
    view = viewer_window.open_path(pdf30)
    with view.document.change("Elimina"):
        view.document.pdf.delete_page(0)
    assert view.document.is_modified and view.page_count == 29
    monkeypatch.setattr(ctl, "confirm_revert", lambda v: True)
    viewer_window.tabs.setCurrentWidget(view)
    ctl.act_revert.trigger()
    new = viewer_window.current_view()
    assert new is not view
    assert not new.document.is_modified
    assert new.page_count == 30
    disk = pymupdf.open(pdf30)
    assert [_text(new, i) for i in range(30)] == [disk[i].get_text() for i in range(30)]
    assert "*" not in viewer_window.tabs.tabText(viewer_window.tabs.indexOf(new))


def test_reload_is_not_a_user_close(viewer_window, ctl, pdf30):
    """La ricarica sostituisce la vista senza consultare i controlli `close_document` dei
    moduli: un controllo che rifiuta non deve lasciare due schede dello stesso file."""
    view = viewer_window.open_path(pdf30)
    asked = []
    guard = lambda v, info: asked.append(v) or False
    viewer_window.ctx.add_guard("close_document", guard)
    try:
        new = ctl.revert(view)
    finally:
        viewer_window.ctx.remove_guard("close_document", guard)
    assert new is not None and new is not view and asked == []
    assert viewer_window.views() == [new]


def test_revert_cancelled_keeps_changes(viewer_window, ctl, pdf30, monkeypatch):
    view = viewer_window.open_path(pdf30)
    with view.document.change("Elimina"):
        view.document.pdf.delete_page(0)
    monkeypatch.setattr(ctl, "confirm_revert", lambda v: False)
    assert ctl.revert(view) is None
    assert viewer_window.current_view() is view and view.document.is_modified


def test_revert_keeps_tab_position_page_and_zoom(viewer_window, ctl, pdf30, tmp_path):
    other = tmp_path / "altro.pdf"
    other.write_bytes(_pdf_bytes(2))
    view = viewer_window.open_path(pdf30)
    viewer_window.open_path(str(other))
    viewer_window.tabs.setCurrentWidget(view)
    view.set_zoom(1.5)
    view.go_to_page(7)
    assert viewer_window.tabs.indexOf(view) == 0
    new = ctl.revert(view)
    assert viewer_window.tabs.indexOf(new) == 0
    assert viewer_window.tabs.count() == 2
    assert new.current_page == 7
    assert abs(new.zoom - 1.5) < 1e-6


def test_external_change_auto_reload_keeps_page(viewer_window, ctl, pdf30, qtbot):
    ctl.debounce_ms = 500
    view = viewer_window.open_path(pdf30)
    view.go_to_page(11)   # pagina 12
    assert view.current_page == 11
    t0 = time.monotonic()
    _write(pdf30, _pdf_bytes(30, "B"))
    qtbot.waitUntil(lambda: ctl.reload_count == 1, timeout=10000)
    assert time.monotonic() - t0 < 10.0     # criterio 2 s sulla macchina di riferimento; margine per la suite carica
    new = viewer_window.current_view()
    assert "B pagina 12" in _text(new, 11)
    assert new.current_page == 11
    assert not new.document.is_modified


def test_fewer_pages_goes_to_last_page(viewer_window, ctl, pdf30, qtbot):
    view = viewer_window.open_path(pdf30)
    view.go_to_page(20)
    _write(pdf30, _pdf_bytes(5, "C"))
    qtbot.waitUntil(lambda: ctl.reload_count == 1, timeout=3000)
    new = viewer_window.current_view()
    assert new.page_count == 5 and new.current_page == 4


def test_modified_document_shows_bar_without_reload(viewer_window, ctl, pdf30, qtbot, monkeypatch):
    view = viewer_window.open_path(pdf30)
    with view.document.change("Elimina"):
        view.document.pdf.delete_page(0)
    _write(pdf30, _pdf_bytes(30, "B"))
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    assert ctl.reload_count == 0
    # barra di avviso del nucleo sopra la vista, con le tre scelte
    from foliant.modules.viewer.reload import ReloadBar
    assert ReloadBar.BANNER_ID in viewer_window.ctx.banners(view)
    banner_bar = viewer_window.tabs.page(view).banners
    assert banner_bar.isVisible()
    buttons = {b.text(): b for b in banner_bar.action_buttons}
    assert set(buttons) == {"Ricarica e perdi le modifiche", "Ignora", "Salva con nome…"}
    assert view.document.is_modified and view.page_count == 29
    # «Ignora»: la barra sparisce, nessuna ricarica, nessuna nuova barra per lo stesso cambiamento
    buttons["Ignora"].click()
    assert ctl.bar(view) is None and ReloadBar.BANNER_ID not in viewer_window.ctx.banners(view)
    qtbot.wait(400)
    assert ctl.bar(view) is None and ctl.reload_count == 0
    # nuovo cambiamento → barra di nuovo; «Ricarica e perdi le modifiche»
    _write(pdf30, _pdf_bytes(30, "D"))
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    ctl.bar(view).buttons["Ricarica e perdi le modifiche"].click()
    new = viewer_window.current_view()
    assert "D pagina 1" in _text(new, 0) and not new.document.is_modified


def test_conflict_save_as(viewer_window, ctl, pdf30, qtbot, tmp_path, monkeypatch):
    view = viewer_window.open_path(pdf30)
    with view.document.change("Elimina"):
        view.document.pdf.delete_page(0)
    _write(pdf30, _pdf_bytes(30, "B"))
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    out = tmp_path / "mio.pdf"
    monkeypatch.setattr(viewer_window.ctx, "ask_save_path", lambda *a, **k: str(out))
    ctl.bar(view).buttons["Salva con nome…"].click()
    assert out.exists() and pymupdf.open(str(out)).page_count == 29
    assert ctl.bar(view) is None


def test_chunked_write_reloads_once(viewer_window, ctl, pdf30, qtbot):
    ctl.debounce_ms = 500
    view = viewer_window.open_path(pdf30)
    data = _pdf_bytes(30, "E")
    third = len(data) // 3
    with open(pdf30, "wb") as f:
        for i, chunk in enumerate((data[:third], data[third:2 * third], data[2 * third:])):
            f.write(chunk)
            f.flush()
            if i < 2:
                qtbot.wait(200)
    qtbot.waitUntil(lambda: ctl.reload_count >= 1, timeout=3000)
    qtbot.wait(800)
    assert ctl.reload_count == 1
    assert "E pagina 1" in _text(viewer_window.current_view(), 0)
    assert view is not viewer_window.current_view()


def test_invalid_file_postpones_reload(viewer_window, ctl, pdf30, qtbot):
    viewer_window.open_path(pdf30)
    data = _pdf_bytes(30, "F")
    _write(pdf30, data[: len(data) // 2])   # scrittura interrotta a metà
    qtbot.wait(500)
    assert ctl.reload_count == 0
    _write(pdf30, data)
    qtbot.waitUntil(lambda: ctl.reload_count == 1, timeout=3000)
    assert "F pagina 1" in _text(viewer_window.current_view(), 0)


def test_own_save_does_not_reload(viewer_window, ctl, pdf30, qtbot):
    view = viewer_window.open_path(pdf30)
    with view.document.change("Elimina"):
        view.document.pdf.delete_page(0)
    assert viewer_window.save_view(view)
    qtbot.wait(500)
    assert ctl.reload_count == 0 and ctl.bar(view) is None
    assert viewer_window.current_view() is view


def test_mode_never(viewer_window, ctl, pdf30, qtbot):
    ctl.set_mode("never")
    assert ctl.mode == "never"
    view = viewer_window.open_path(pdf30)
    _write(pdf30, _pdf_bytes(30, "B"))
    qtbot.wait(600)
    assert ctl.reload_count == 0 and ctl.bar(view) is None
    assert viewer_window.current_view() is view
    assert "A pagina 1" in _text(view, 0)


def test_mode_ask(viewer_window, ctl, pdf30, qtbot):
    ctl.mode_actions["ask"].trigger()
    assert ctl.mode == "ask"
    view = viewer_window.open_path(pdf30)
    _write(pdf30, _pdf_bytes(30, "B"))
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    assert ctl.reload_count == 0
    ctl.bar(view).buttons["Ricarica"].click()
    assert ctl.reload_count == 1


def test_deleted_file_bar_and_save_recreates(viewer_window, ctl, pdf30, qtbot):
    view = viewer_window.open_path(pdf30)
    os.remove(pdf30)
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    bar = ctl.bar(view)
    assert bar.kind == "missing" and list(bar.buttons) == ["Salva", "Salva con nome…"]
    assert ctl.reload_count == 0
    bar.buttons["Salva"].click()
    assert os.path.exists(pdf30)
    assert ctl.bar(view) is None
    qtbot.wait(400)
    assert ctl.reload_count == 0 and viewer_window.current_view() is view


def test_deleted_file_save_as_elsewhere(viewer_window, ctl, pdf30, qtbot, monkeypatch, tmp_path):
    """NFR-12/VIS-49: l'avviso del file eliminato o spostato offre anche «Salva con nome…»."""
    view = viewer_window.open_path(pdf30)
    os.remove(pdf30)
    qtbot.waitUntil(lambda: ctl.bar(view) is not None, timeout=3000)
    target = tmp_path / "altrove.pdf"
    monkeypatch.setattr(viewer_window.ctx, "ask_save_path", lambda *a, **k: str(target))
    banner = viewer_window.tabs.page(view).banners
    [b for b in banner.action_buttons if b.text() == "Salva con nome…"][0].click()
    assert target.exists() and not os.path.exists(pdf30)
    assert str(view.document.path) == str(target)
    assert ctl.bar(view) is None
    from foliant.modules.viewer.reload import ReloadBar
    assert ReloadBar.BANNER_ID not in viewer_window.ctx.banners(view)


def test_replaced_file_still_watched(viewer_window, ctl, pdf30, qtbot):
    """Gli editor sostituiscono il file (scrittura su temporaneo + rinomina)."""
    viewer_window.open_path(pdf30)
    for n, tag in enumerate(("G", "H"), start=1):
        tmp = pdf30 + ".tmp"
        with open(tmp, "wb") as f:
            f.write(_pdf_bytes(3, tag))
        os.replace(tmp, pdf30)
        qtbot.waitUntil(lambda n=n: ctl.reload_count == n, timeout=3000)
        assert f"{tag} pagina 1" in _text(viewer_window.current_view(), 0)


def test_encrypted_reload_keeps_password(viewer_window, ctl, tmp_path, qtbot, monkeypatch):
    path = tmp_path / "cifrato.pdf"
    d = pymupdf.open(stream=_pdf_bytes(3, "A"))
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="segreta")
    view = viewer_window.open_path(str(path), "segreta")
    asked = []
    monkeypatch.setattr(ctl, "ask_password", lambda v: asked.append(1) or "segreta")
    d = pymupdf.open(stream=_pdf_bytes(3, "Z"))
    d.save(str(path) + ".new", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="segreta")
    # come un programma che salva bene: su Windows l'antivirus può tenere aperto per un attimo il file
    platform.replace_file(str(path) + ".new", path)
    qtbot.waitUntil(lambda: ctl.reload_count == 1, timeout=3000)
    assert not asked
    assert "Z pagina 1" in _text(viewer_window.current_view(), 0)
    assert view is not viewer_window.current_view()


def test_changed_notice_visible_outside_document_view(viewer_window, tmp_path, qtbot):
    from foliant.modules.viewer import reload as R
    window = viewer_window
    ctl = window.ctx.viewer_parts.get("reload") or R.install(window.ctx)
    path = tmp_path / "conflitto.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "originale")
    doc.save(str(path))
    view = window.open_path(str(path))
    with view.document.change("Modifica") as pdf:
        pdf[0].insert_text((72, 200), "modifica locale")
    ctl._show_bar(view, R.BAR_CHANGED)
    # un'altra modalità copre la vista: l'avviso resta nella barra di stato e sulla scheda
    from PySide6.QtWidgets import QLabel
    window.ctx.set_central_widget(QLabel("altra modalità"))
    assert ctl.notice.isVisible()
    assert "altra applicazione" in ctl.notice.text()
    assert not window.tabs.tabIcon(window.tabs.indexOf(view)).isNull()
    ctl.notice.click()
    assert window.ctx.central_widget() is None
    ctl.ignore(view)
    assert not ctl.notice.isVisible()
    assert window.tabs.tabIcon(window.tabs.indexOf(view)).isNull()
