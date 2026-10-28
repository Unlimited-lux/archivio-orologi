"""Integrazione del modulo Firma nella finestra: dialoghi, strumenti, flusso di
firma digitale, pannello firme e avvisi sui documenti firmati."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from foliant.api import PageEvent
from foliant.modules.sign import digital, graphic as g, store

from .conftest import PASSWORD


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)


def _ctl(window):
    return window.ctx.sign_controller


def _panel(window):
    return window._sidebar["firme"][2]


def _ev(page, x, y, button=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    return PageEvent(page=page, point=pymupdf.Point(x, y), pos=QPointF(0, 0), button=button, buttons=button,
                     modifiers=mods)


def _sign_file(window, monkeypatch, path, target, id_a, *, invisible=False, rect=(100, 600, 300, 680),
               certify=False, level=2):
    """Esegue il flusso «Firma digitale» con i dialoghi simulati."""
    from foliant.modules.sign import ui_digital
    view = window.open_path(str(path))
    c = _ctl(window)

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(reason="Approvo", location="Milano", certify=certify,
                                          docmdp_level=level)
        return True

    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(target))
    if invisible:
        ok = c.digital_sign(view, invisible=True, certify=certify)
    else:
        ok = c.digital_sign(view, page=0, rect=pymupdf.Rect(*rect), certify=certify)
    assert ok
    return window.current_view()


def test_mode_panel_and_actions(window):
    assert "firma" in window.modes
    assert "firme" in window._sidebar
    texts = [a.text() for a in window.modes["firma"].toolbar.actions()]
    assert "Crea firma" in texts and "Firma digitale" in texts and "Verifica firme" in texts


def test_draw_tab_use_disabled_until_stroke(qtbot):
    from foliant.modules.sign.ui_create import CreateSignatureDialog
    dlg = CreateSignatureDialog()
    qtbot.addWidget(dlg)
    assert not dlg.use_button.isEnabled()
    pad = dlg.draw.pad
    pad.add_point(10, 10, start=True)
    pad.add_point(12, 11)
    pad.end_stroke()
    assert not dlg.use_button.isEnabled()          # tratto sotto la lunghezza minima
    for i in range(40):
        pad.add_point(20 + i * 5, 60 + (i % 7) * 3, 0.2 + i / 60, start=(i == 0))
    pad.end_stroke()
    assert dlg.use_button.isEnabled()
    pad.undo_stroke()
    assert not dlg.use_button.isEnabled()
    pad.add_point(20, 60, start=True)
    pad.add_point(200, 70)
    pad.end_stroke()
    dlg._accept()
    assert dlg.item is not None and dlg.item.origin == "disegnata" and dlg.save_to_library


def test_type_tab_previews_and_incompatible(qtbot):
    from foliant.modules.sign.ui_create import TypeTab
    tab = TypeTab("Mario Rossi")
    qtbot.addWidget(tab)
    assert tab.preview_count() >= 8
    assert tab.is_valid()
    tab.text.setText("   ")
    tab.refresh()
    assert not tab.is_valid()


def test_image_tab_reports_unsupported(qtbot, tmp_path):
    from foliant.modules.sign.ui_create import ImageTab
    tab = ImageTab()
    qtbot.addWidget(tab)
    p = tmp_path / "x.png"
    p.write_bytes(b"%PDF-1.4 finto")
    tab.load_path(str(p))
    assert "PDF" in tab.error.text() and not tab.is_valid()


def test_place_move_resize_undo(window, sample_pdf):
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    raw = [[(10 + i * 4, 40 + (i % 9) * 2, 1.0) for i in range(60)]]
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes(raw)))
    c.start_placement(view, item)
    tool = view.tool
    assert tool.pending is item
    assert tool.press(view, _ev(0, 200, 300))
    tool.release(view, _ev(0, 200, 300))
    with view.document.lock:
        sigs = g.placed_signatures(view.document.pdf, 0)
    assert len(sigs) == 1
    r = sigs[0].rect
    assert abs((r.x0 + r.x1) / 2 - 200) < 1
    # ridimensionamento da maniglia: proporzioni invariate
    aspect = r.width / r.height
    tool.selected = (0, sigs[0].xref)
    tool.press(view, _ev(0, r.x1, r.y1))
    tool._drag["now"] = pymupdf.Point(r.x1 + 60, r.y1 + 5)
    tool.release(view, _ev(0, r.x1 + 60, r.y1 + 5))
    with view.document.lock:
        r2 = g.placed_signatures(view.document.pdf, 0)[0].rect
    assert abs(r2.width / r2.height - aspect) / aspect < 0.005
    assert r2.width > r.width
    assert view.document.undo()
    assert view.document.undo()
    with view.document.lock:
        assert g.placed_signatures(view.document.pdf, 0) == []


def test_repeat_group_undone_in_one_step(window, tmp_path):
    from tests.conftest import make_pdf
    view = window.open_path(make_pdf(tmp_path / "dieci.pdf", pages=10))
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    xref = c.place(view, 0, pymupdf.Rect(450, 780, 540, 800), item)
    from foliant.modules.sign import ui_digital

    class FakeRepeat:
        def __init__(self, *a):
            self.pages, self.anchor = [1, 2, 3, 7], "auto"

        def exec(self):
            return True

    ui_digital_repeat = ui_digital.RepeatDialog
    ui_digital.RepeatDialog = FakeRepeat
    try:
        c.repeat(view, 0, xref)
    finally:
        ui_digital.RepeatDialog = ui_digital_repeat
    with view.document.lock:
        counts = [len(g.placed_signatures(view.document.pdf, p)) for p in range(10)]
    assert counts == [1, 1, 1, 1, 0, 0, 0, 1, 0, 0]
    view.document.undo()
    with view.document.lock:
        counts = [len(g.placed_signatures(view.document.pdf, p)) for p in range(10)]
    assert counts == [1] + [0] * 9


def test_digital_sign_flow_and_panel_valid(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """Firma con certificato di test → riapertura → la verifica riporta «valida»."""
    store.TrustStore().add(id_a[1])
    original = Path(sample_pdf).read_bytes()
    target = tmp_path / "esempio_firmato.pdf"
    view = _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    assert Path(view.document.path) == target
    assert Path(sample_pdf).read_bytes() == original           # originale intatto
    assert target.read_bytes().startswith(original)            # salvataggio incrementale
    st = _ctl(window).state(view)
    assert [s.status for s in st.statuses] == ["valida"]
    panel = _panel(window)
    assert panel.view is view
    assert panel.list.count() == 1 and "Valida" in panel.list.item(0).text()
    assert "valide" in panel.banner.text()
    panel.list.setCurrentRow(0)
    assert "Mario Rossi" in panel.details.toPlainText()


def test_untrusted_then_trust_from_panel(window, monkeypatch, sample_pdf, tmp_path, id_a):
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "f.pdf", id_a, invisible=True)
    c = _ctl(window)
    st = c.state(view)
    assert st.statuses[0].status == "indeterminata"
    assert st.statuses[0].invisible
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: True)
    c.trust_signer(view, st.statuses[0])
    assert c.state(view).statuses[0].status == "valida"


def test_later_modification_detected(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """Una modifica successiva alla firma viene rilevata dalla verifica."""
    store.TrustStore().add(id_a[1])
    target = tmp_path / "firmato.pdf"
    _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    data = bytearray(target.read_bytes())
    i = data.find(b"Orologeria")
    data[i] = ord("X")                                   # byte alterato nell'intervallo firmato
    altered = tmp_path / "alterato.pdf"
    altered.write_bytes(bytes(data))
    view = window.open_path(str(altered))
    st = _ctl(window).state(view)
    assert st.statuses and st.statuses[0].status == "non valida"
    assert "presenta problemi" in _panel(window).banner.text()


def test_wrong_certificate_password_message(window, qtbot, sample_pdf, id_a):
    from foliant.modules.sign.ui_digital import DigitalSignDialog
    window.open_path(sample_pdf)
    dlg = DigitalSignDialog(window, _ctl(window))
    qtbot.addWidget(dlg)
    dlg._reload_ids(str(id_a[0]))
    dlg.password.setText("sbagliata")
    dlg._accept()
    assert "Password errata" in dlg.error.text()
    assert dlg.password.text() == "" and dlg.digital_id is None
    dlg.password.setText(PASSWORD)
    dlg._accept()
    assert dlg.digital_id is not None and dlg.options.reason


def test_expired_certificate_blocked_in_dialog(window, qtbot, sample_pdf, expired_id):
    from foliant.modules.sign.ui_digital import DigitalSignDialog
    window.open_path(sample_pdf)
    dlg = DigitalSignDialog(window, _ctl(window))
    qtbot.addWidget(dlg)
    dlg._reload_ids(str(expired_id[0]))
    dlg.password.setText(PASSWORD)
    dlg._accept()
    assert dlg.digital_id is None
    assert expired_id[1].not_after.strftime("%d/%m/%Y") in dlg.error.text()
    assert not dlg.allow_expired.isHidden()


def test_signed_document_edit_warning(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """SIG-21: una modifica a un documento firmato chiede conferma; «Annulla» la ritira."""
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "w.pdf", id_a)
    c = _ctl(window)
    asked = []
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    with view.document.change("Ruota pagina") as pdf:
        pdf[0].set_rotation(90)
    QApplication.processEvents()
    assert asked == ["Ruota pagina"]
    assert not view.document.is_modified
    # «Lavora su una copia senza firme»
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: "copy")
    with view.document.change("Ruota pagina") as pdf:
        pdf[0].set_rotation(90)
    QApplication.processEvents()
    copy_view = window.current_view()
    assert copy_view is not view and "senza firme" in copy_view.document.display_name
    assert not digital.has_signatures(copy_view.document.to_bytes())
    # «Procedi comunque»: l'avviso non si ripete
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "proceed")
    window.tabs.setCurrentWidget(view)
    with view.document.change("Ruota pagina") as pdf:
        pdf[0].set_rotation(90)
    QApplication.processEvents()
    with view.document.change("Ruota pagina") as pdf:
        pdf[0].set_rotation(180)
    assert view.document.is_modified and asked.count("Ruota pagina") == 2


def test_graphic_signature_on_signed_doc(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """Firma grafica (annotazione) su documento firmato: consentita senza avviso per le firme di
    approvazione; con certificazione livello 2 chiede conferma prima dell'inserimento."""
    store.TrustStore().add(id_a[1])
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "g.pdf", id_a)
    c = _ctl(window)
    asked = []
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]])))
    assert c.place(view, 0, pymupdf.Rect(10, 10, 100, 40), item)
    assert asked == []
    assert window.save_view(view)
    assert c.state(view).statuses[0].status == "valida"
    view2 = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "c2.pdf", id_a, certify=True, level=2)
    assert c.place(view2, 0, pymupdf.Rect(10, 10, 100, 40), item) is None
    assert asked and not view2.document.is_modified


def test_panel_valid_invalid_and_empty_field(window, monkeypatch, tmp_path, id_a, id_b):
    """SIG-18: firma corretta, firma alterata e campo vuoto → 3 voci e barra «problemi»."""
    from tests.conftest import make_pdf
    store.TrustStore().add(id_a[1])
    store.TrustStore().add(id_b[1])
    base = Path(make_pdf(tmp_path / "tre.pdf", pages=1)).read_bytes()
    data, _n = digital.add_signature_field(base, "Cliente", 0, (300, 700, 500, 760))
    data = digital.sign_pdf(data, id_a[2], digital.SignOptions(rect=(60, 700, 250, 760)))
    data = digital.sign_pdf(data, id_b[2], digital.SignOptions(rect=(60, 600, 250, 660)))
    # altera un byte coperto dalla seconda firma soltanto (nella sua revisione)
    statuses = digital.verify_pdf(data, trusted=[id_a[1], id_b[1]])
    first_end = min(s.revision_end for s in statuses)
    buf = bytearray(data)
    pos = buf.find(b"Lucia Bianchi", first_end)
    buf[pos] = ord("l")
    path = tmp_path / "misto.pdf"
    path.write_bytes(bytes(buf))
    view = window.open_path(str(path))
    st = _ctl(window).state(view)
    assert sorted(s.status for s in st.statuses) == ["non valida", "valida"]
    panel = _panel(window)
    texts = [panel.list.item(i).text() for i in range(panel.list.count())]
    assert len(texts) == 3
    assert any(t.startswith("Valida") for t in texts) and any(t.startswith("Non valida") for t in texts)
    assert any(t.startswith("Da firmare") for t in texts)
    assert "presenta problemi" in panel.banner.text()
    assert not panel.btn_next.isHidden()


def test_signed_version_opens_prefix(window, monkeypatch, sample_pdf, tmp_path, id_a):
    target = tmp_path / "v.pdf"
    view = _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    c = _ctl(window)
    s = c.state(view).statuses[0]
    new_view = c.open_signed_version(view, s)
    assert new_view is not None
    assert target.read_bytes()[:s.revision_end] == digital.signed_revision(target.read_bytes(), s)


def test_add_empty_field_to_unsigned_document(window, monkeypatch, sample_pdf):
    from foliant.modules.sign import ui_digital
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    monkeypatch.setattr(ui_digital.FieldDialog, "exec", lambda self: True)
    c.add_field(view, 0, pymupdf.Rect(100, 600, 300, 660))
    assert view.document.is_modified
    st = c.state(view)
    assert [f.name for f in st.fields] == ["Firma"] and not st.fields[0].signed
    view.document.undo()
    assert not view.document.is_modified


def test_unsaved_changes_asked_before_signing(window, monkeypatch, sample_pdf):
    view = window.open_path(sample_pdf)
    with view.document.change("Ruota") as pdf:
        pdf[0].set_rotation(90)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert _ctl(window).digital_sign(view, invisible=True) is False


@pytest.mark.parametrize("dark", [False, True])
def test_screenshot(window, monkeypatch, sample_pdf, tmp_path, id_a, dark):
    """Cattura per la verifica visiva (non confronta pixel)."""
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    if dark:
        theme.apply(QApplication.instance(), "dark")
    store.TrustStore().add(id_a[1])
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "s.pdf", id_a)
    window.set_mode("firma")
    raw = [[(10 + i * 4, 40 + 15 * math.sin(i / 5), 0.3 + i / 80) for i in range(50)]]
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes(raw)), color="#1d3fb8")
    c = _ctl(window)
    c.start_placement(view, item)
    view.tool.hover = (0, pymupdf.Point(400, 300))
    view.flush_rendering()
    shot = tmp_path / f"firma-{'scuro' if dark else 'chiaro'}.png"
    assert window.grab().save(str(shot))
    dest = os.environ.get("FOLIANT_SHOTS")
    if dest:
        Path(dest).mkdir(parents=True, exist_ok=True)
        window.grab().save(str(Path(dest) / shot.name))
    theme.apply(QApplication.instance(), "light")


def test_timestamp_unreachable_offers_choices(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """SIG-10: TSA irraggiungibile → scelta; fino alla scelta nessun file; «senza marca» firma."""
    from foliant.modules.sign import ui_digital
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    target = tmp_path / "ts.pdf"

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(timestamp_url="http://127.0.0.1:9/", timestamp_timeout=2)
        return True

    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(target))
    seen = []

    def choose(message, current=None):
        seen.append(message)
        assert not target.exists()
        return "cancel" if len(seen) == 1 else "without"

    monkeypatch.setattr(c, "_ask_timestamp_failure", choose)
    assert c.digital_sign(view, invisible=True) is False
    assert not target.exists() and len(seen) == 1
    assert c.digital_sign(window.current_view(), invisible=True) is True
    assert target.exists()
    st = digital.verify_pdf(target.read_bytes())
    assert st[0].time_source == "dichiarata"


def test_invitation_counts_visible_fields_and_cycles(window, tmp_path):
    """SIG-20: 3 campi vuoti di cui 1 nascosto → «2 firme richieste», «Campo successivo» sui 2 visibili."""
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "campi.pdf", pages=2)).read_bytes()
    data, _n = digital.add_signature_field(data, "Venditore", 0, (60, 700, 250, 760))
    data, _n = digital.add_signature_field(data, "Acquirente", 1, (300, 700, 500, 760))
    data, _n = digital.add_signature_field(data, "Nascosto", 1, (60, 600, 250, 660), hidden=True)
    path = tmp_path / "campi-vuoti.pdf"
    path.write_bytes(data)
    view = window.open_path(str(path))
    panel = _panel(window)
    assert "2 firme richieste" in panel.banner.text()
    assert len(panel.visible_empty_fields()) == 2
    pages = []
    for _i in range(3):
        panel.next_field()
        pages.append(view.current_page)
    assert pages == [0, 1, 0]
    assert not panel.btn_sign_now.isHidden()


def test_context_menu_on_signature_selects_panel_entry(window, monkeypatch, sample_pdf, tmp_path, id_a, id_b):
    from PySide6.QtWidgets import QMenu
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "due.pdf", pages=1)).read_bytes()
    data = digital.sign_pdf(data, id_a[2], digital.SignOptions(rect=(60, 600, 250, 660)))
    data = digital.sign_pdf(data, id_b[2], digital.SignOptions(rect=(300, 600, 500, 660)))
    path = tmp_path / "due-firme.pdf"
    path.write_bytes(data)
    view = window.open_path(str(path))
    c = _ctl(window)
    menu = QMenu()
    c.menu_provider(view, _ev(0, 400, 630), menu)
    acts = [a for a in menu.actions() if "Lucia Bianchi" in a.text()]
    assert acts
    acts[0].trigger()
    cur = _panel(window).current()
    assert cur[0] == "status" and cur[1].signer == "Lucia Bianchi"


def test_certified_no_changes_blocks_graphic(window, monkeypatch, sample_pdf, tmp_path, id_a):
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "cert.pdf", id_a, certify=True, level=1)
    c = _ctl(window)
    assert c.state(view).certification == 1
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]])))
    assert c.place(view, 0, pymupdf.Rect(10, 10, 100, 40), item) is None
    assert not view.document.is_modified
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    c.start_digital_sign(False)
    assert errors and "nessuna modifica" in errors[0]


def test_appearance_profiles(window, qtbot, monkeypatch, sample_pdf, id_a):
    """SIG-09: profilo salvato con nome, riapplicato; grafica mancante → «da completare»."""
    from PySide6.QtWidgets import QInputDialog

    from foliant.modules.sign.ui_digital import DigitalSignDialog
    window.open_path(sample_pdf)
    dlg = DigitalSignDialog(window, _ctl(window))
    qtbot.addWidget(dlg)
    dlg.layout_combo.setCurrentIndex(dlg.layout_combo.findData("nome_grande"))
    dlg.show_checks["luogo"].setChecked(False)
    dlg.lang_combo.setCurrentIndex(dlg.lang_combo.findData("en"))
    assert any(line.startswith("Digitally signed by") for line in dlg.preview.lines)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Contratti", True))
    dlg._save_profile()
    profiles = store.AppearanceProfiles().list()
    assert [p.name for p in profiles] == ["Contratti"] and profiles[0].default
    assert profiles[0].layout == "nome_grande" and "luogo" not in profiles[0].show
    assert profiles[0].label_language == "en"
    store.AppearanceProfiles().save(store.AppearanceProfile("Logo", graphic="non-esiste"))
    dlg2 = DigitalSignDialog(window, _ctl(window))
    qtbot.addWidget(dlg2)
    texts = [dlg2.profile_combo.itemText(i) for i in range(dlg2.profile_combo.count())]
    assert any("da completare" in t for t in texts)
    dlg2.profile_combo.setCurrentIndex(texts.index("Contratti"))
    assert dlg2.layout_combo.currentData() == "nome_grande"
    assert not dlg2.show_checks["luogo"].isChecked()
    dlg2._reload_ids(str(id_a[0]))
    dlg2.password.setText(PASSWORD)
    dlg2._accept()
    assert dlg2.options.appearance["layout"] == "nome_grande"
    assert dlg2.options.appearance["label_language"] == "en"
    assert "luogo" not in dlg2.options.appearance["show"]


def test_signed_document_annotation_saved_incrementally(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """SIG-21: commento aggiunto e salvato → salvataggio incrementale, firma «Valida» con modifiche consentite."""
    store.TrustStore().add(id_a[1])
    target = tmp_path / "commentato.pdf"
    view = _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    signed = target.read_bytes()
    c = _ctl(window)
    asked = []
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "proceed")
    with view.document.change("Nota") as pdf:
        pdf[0].add_text_annot((300, 300), "Va bene")
    QApplication.processEvents()
    assert asked == []                                   # modifica consentita: nessun avviso
    assert window.save_view(view)
    data = target.read_bytes()
    assert data.startswith(signed) and len(data) > len(signed)
    st = c.state(view)
    assert st.statuses[0].status == "valida"
    assert "consentite" in st.statuses[0].modifications
    # una seconda modifica, al contenuto, chiede conferma
    with view.document.change("Testo") as pdf:
        pdf[0].insert_text((100, 400), "AGGIUNTO")
    QApplication.processEvents()
    assert asked == ["Testo"]
    assert window.save_view(view)
    assert target.read_bytes().startswith(data)
    assert c.state(view).statuses[0].status == "non valida"
    # «Salva una copia»: anche la copia è incrementale
    copy = tmp_path / "copia.pdf"
    view.document.save_copy(copy)
    assert copy.read_bytes().startswith(signed)


def test_delete_group_from_menu(window, tmp_path):
    from PySide6.QtWidgets import QMenu
    from tests.conftest import make_pdf
    view = window.open_path(make_pdf(tmp_path / "quattro.pdf", pages=4))
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    xref = c.place(view, 0, pymupdf.Rect(450, 780, 540, 800), item)
    with view.document.change("Ripeti") as pdf:
        g.repeat_signature(pdf, 0, xref, [1, 2, 3])
    menu = QMenu()
    c.menu_provider(view, _ev(0, 500, 790), menu)
    act = next(a for a in menu.actions() if a.text() == "Elimina tutto il gruppo")
    act.trigger()
    with view.document.lock:
        assert sum(len(g.placed_signatures(view.document.pdf, p)) for p in range(4)) == 0
    view.document.undo()
    with view.document.lock:
        assert sum(len(g.placed_signatures(view.document.pdf, p)) for p in range(4)) == 4


def test_field_dialog_lock_spec(qtbot, window, monkeypatch, sample_pdf):
    from foliant.modules.sign import ui_digital
    dlg = ui_digital.FieldDialog(None)
    qtbot.addWidget(dlg)
    assert dlg.lock_spec() == ("Include", None)
    dlg.lock_action.setCurrentIndex(dlg.lock_action.findData("Include"))
    dlg._accept()
    assert "campi" in dlg.error.text()
    dlg.lock_fields.setText("Nome, Data")
    assert dlg.lock_spec() == ("Include", ["Nome", "Data"])
    view = window.open_path(sample_pdf)

    def fake_exec(self):
        self.lock_action.setCurrentIndex(self.lock_action.findData("All"))
        return True

    monkeypatch.setattr(ui_digital.FieldDialog, "exec", fake_exec)
    _ctl(window).add_field(view, 0, pymupdf.Rect(100, 600, 300, 660))
    assert b"/Lock" in view.document.to_bytes(garbage=0, deflate=False)


def test_settings_disable_auto_verify(window, monkeypatch, sample_pdf, tmp_path, id_a):
    data = digital.sign_pdf(Path(sample_pdf).read_bytes(), id_a[2], digital.SignOptions())
    p = tmp_path / "auto.pdf"
    p.write_bytes(data)
    window.settings.setValue("sign/auto_verify", "false")
    view = window.open_path(str(p))
    assert _ctl(window).state(view).statuses == []
    window.settings.setValue("sign/auto_verify", "true")


def test_field_constraints_filter_ids_and_reasons(window, qtbot, tmp_path, id_a, id_b):
    """SIG-19/20: campo che ammette solo l'emittente di B e due motivi → solo l'ID conforme è selezionabile."""
    from foliant.modules.sign.ui_digital import DigitalSignDialog
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "vincoli.pdf", pages=1)).read_bytes()
    data, name = digital.add_signature_field(data, "Cliente", 0, (300, 700, 500, 760),
                                             allowed_reasons=["Accetto", "Rifiuto"], allowed_issuers=[id_b[1]])
    field = digital.signature_fields(data)[0]
    assert field.allowed_reasons == ["Accetto", "Rifiuto"]
    assert [c.sha256 for c in field.allowed_issuers] == [id_b[1].sha256]
    store.IDRegistry().add(id_a[0], id_a[1])
    store.IDRegistry().add(id_b[0], id_b[1])
    path = tmp_path / "vincoli2.pdf"
    path.write_bytes(data)
    window.open_path(str(path))
    dlg = DigitalSignDialog(window, _ctl(window), field=field)
    qtbot.addWidget(dlg)
    model = dlg.id_combo.model()
    enabled = [dlg.id_combo.itemText(i) for i in range(dlg.id_combo.count()) if model.item(i).isEnabled()]
    assert enabled == ["Lucia Bianchi"]
    assert [dlg.reason.itemText(i) for i in range(dlg.reason.count())] == ["Accetto", "Rifiuto"]
    assert not dlg.reason.isEditable()
    dlg._reload_ids(str(id_a[0]))          # forzando l'ID non conforme la firma è rifiutata
    dlg.id_combo.setCurrentIndex(dlg.id_combo.findData(str(Path(id_a[0]).resolve())))
    dlg.password.setText(PASSWORD)
    dlg._accept()
    assert dlg.digital_id is None and "non accetta" in dlg.error.text()
    out = digital.sign_pdf(data, id_b[2], digital.SignOptions(field_name="Cliente", reason="Accetto"))
    assert digital.verify_pdf(out, trusted=[id_b[1]])[0].status == "valida"


def test_keyboard_placement_and_arrows(window, qtbot, sample_pdf):
    """NFR-21: Invio posiziona la firma in attesa, le frecce la spostano, Canc la elimina."""
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (60, 12, 1), (100, 0, 1)]])))
    c.start_placement(view, item)
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_Return)
    with view.document.lock:
        sigs = g.placed_signatures(view.document.pdf, view.current_page)
    assert len(sigs) == 1
    r0 = sigs[0].rect
    qtbot.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    with view.document.lock:
        r1 = g.placed_signatures(view.document.pdf, view.current_page)[0].rect
    assert r1.x0 == pytest.approx(r0.x0 + 10, abs=0.01)
    qtbot.keyClick(view, Qt.Key.Key_Delete)
    with view.document.lock:
        assert g.placed_signatures(view.document.pdf, view.current_page) == []


def test_robust_with_damaged_and_encrypted(window, monkeypatch, tmp_path, encrypted_pdf, id_a):
    """Documenti cifrati o con firma danneggiata: nessun crash, esito chiaro."""
    view = window.open_path(encrypted_pdf, "segreta")
    assert view is not None
    st = _ctl(window).state(view)
    assert st.error == "" and st.statuses == []
    from tests.conftest import make_pdf
    data = digital.sign_pdf(Path(make_pdf(tmp_path / "d.pdf", pages=1)).read_bytes(), id_a[2],
                            digital.SignOptions())
    i = data.rfind(b"/Contents <")
    broken = data[:i + 20] + b"ZZ" + data[i + 22:]        # CMS corrotto
    p = tmp_path / "rotto.pdf"
    p.write_bytes(broken)
    view2 = window.open_path(str(p))
    st2 = _ctl(window).state(view2)
    assert st2.statuses and st2.statuses[0].status in ("non valida", "indeterminata") or st2.error
    _panel(window).refresh()


def test_save_incremental_even_without_auto_verify(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """Rilevamento sincrono: con la verifica automatica disattivata il salvataggio resta incrementale."""
    data = digital.sign_pdf(Path(sample_pdf).read_bytes(), id_a[2], digital.SignOptions())
    p = tmp_path / "noauto.pdf"
    p.write_bytes(data)
    window.settings.setValue("sign/auto_verify", "false")
    try:
        view = window.open_path(str(p))
        with view.document.change("Nota") as pdf:
            pdf[0].add_text_annot((300, 300), "ok")
        QApplication.processEvents()
        assert window.save_view(view)
        assert p.read_bytes().startswith(data)
        st = digital.verify_pdf(p.read_bytes(), trusted=[id_a[1]])
        assert st[0].status == "valida"
    finally:
        window.settings.setValue("sign/auto_verify", "true")


def test_damaged_library_item_reports_error(window, monkeypatch, sample_pdf):
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    lib = c.library(interactive=False)
    e = lib.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]]))), "Rotta")
    (lib.root / e.file).write_text("non è svg")
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    c.use_library_item(e.id)
    assert errors and "danneggiato" in errors[0]
    assert c.tool_for(view).pending is None


def test_undo_redo_after_incremental_save(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """Annulla/ripeti dopo un salvataggio incrementale: ogni salvataggio conserva i byte
    firmati come prefisso e la firma resta valida."""
    store.TrustStore().add(id_a[1])
    target = tmp_path / "ur.pdf"
    view = _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    signed = target.read_bytes()
    c = _ctl(window)
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: "proceed")
    doc = view.document
    with doc.change("Nota") as pdf:
        pdf[0].add_text_annot((300, 300), "Prima nota")
    QApplication.processEvents()
    assert window.save_view(view)
    first = target.read_bytes()
    assert doc.undo() and doc.is_modified
    assert window.save_view(view)
    second = target.read_bytes()
    assert second.startswith(first) and first.startswith(signed)
    assert digital.verify_pdf(second, trusted=[id_a[1]])[0].status == "valida"
    import pymupdf as _p
    assert len(list(_p.open(stream=second)[0].annots())) == 0
    assert doc.redo()
    assert window.save_view(view)
    third = target.read_bytes()
    assert third.startswith(second)
    assert len(list(_p.open(stream=third)[0].annots())) == 1
    st = digital.verify_pdf(third, trusted=[id_a[1]])
    assert st[0].status == "valida", st[0].reasons + st[0].modification_details
    assert c.state(view).statuses[0].status == "valida"


def test_timestamp_failure_use_other_server(window, monkeypatch, sample_pdf, tmp_path, id_a, tsa):
    """SIG-10: «Usa un altro server» riprova con il server scelto."""
    from foliant.modules.sign import ui_digital
    store.TSAConfig().add("Guasto", "http://127.0.0.1:9/")
    store.TSAConfig().add("Buono", "http://tsa.example.invalid/")
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    target = tmp_path / "altro.pdf"

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(timestamp_url="http://127.0.0.1:9/", timestamp_timeout=2)
        return True

    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(target))
    assert [srv.name for srv in c.other_tsa_servers("http://127.0.0.1:9/")] == ["Buono"]
    used = []
    real_sign = digital.sign_pdf

    def sign(data, did, opts):
        used.append(opts.timestamp_url)
        if opts.timestamp_url == "http://tsa.example.invalid/":
            opts.timestamper = tsa[0]
        return real_sign(data, did, opts)

    monkeypatch.setattr(digital, "sign_pdf", sign)
    monkeypatch.setattr(c, "_ask_timestamp_failure",
                        lambda msg, cur: ("server", "http://tsa.example.invalid/", 5.0))
    assert c.digital_sign(view, invisible=True)
    assert used == ["http://127.0.0.1:9/", "http://tsa.example.invalid/"]
    st = digital.verify_pdf(target.read_bytes())
    assert st[0].time_source == "marca temporale"
    assert c.clock_skew_warning(target.read_bytes()) is None


def test_field_duplicated_on_pages_with_required_certification(window, monkeypatch, sample_pdf):
    """SIG-19: campo duplicato su più pagine, con livello di certificazione richiesto (/SV /MDP)."""
    from foliant.modules.sign import ui_digital
    view = window.open_path(sample_pdf)

    def fake_exec(self):
        self.pages.setText("tutte")
        self.mdp.setCurrentIndex(self.mdp.findData(2))
        return True

    monkeypatch.setattr(ui_digital.FieldDialog, "exec", fake_exec)
    _ctl(window).add_field(view, 0, pymupdf.Rect(100, 600, 300, 660))
    fields = _ctl(window).state(view).fields
    assert sorted(f.page for f in fields) == [0, 1, 2]
    assert len({f.name for f in fields}) == 3
    assert all(f.certify_level == 2 for f in fields)
    view.document.undo()
    assert not view.document.is_modified


def test_repeat_dialog_thumbnails(window, qtbot, tmp_path):
    """SIG-06: anteprima delle copie sulle miniature."""
    from foliant.modules.sign.ui_digital import RepeatDialog
    from tests.conftest import make_pdf
    view = window.open_path(make_pdf(tmp_path / "anteprima.pdf", pages=12))
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    xref = c.place(view, 0, pymupdf.Rect(450, 780, 540, 800), item)
    with view.document.lock:
        items = g.repeat_preview(view.document.pdf, 0, xref, [1, 2], "br", color=(255, 0, 0))
    assert [p for p, _pix, _s in items] == [1, 2]
    pix = items[0][1]
    import numpy as np
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    red = np.argwhere((arr[..., 0] == 255) & (arr[..., 1] == 0))
    assert red.size and red[:, 0].mean() > pix.height * 0.8      # riquadro in basso
    shown = []

    def preview(pages, anchor):
        shown.append(list(pages))
        from PySide6.QtGui import QImage
        img = QImage(10, 10, QImage.Format.Format_RGB32)
        return [(p, img, False) for p in pages]

    dlg = RepeatDialog(window, 12, 0, preview)
    qtbot.addWidget(dlg)
    assert dlg.thumbs.count() == RepeatDialog.MAX_THUMBS + 1          # 11 pagine: 8 miniature + «… e altre»
    dlg.r_range.setChecked(True)
    dlg.range.setText("2-3")
    assert dlg.thumbs.count() == 2 and shown[-1] == [1, 2]


def test_embed_all_signatures(window, monkeypatch, tmp_path):
    from tests.conftest import make_pdf
    view = window.open_path(make_pdf(tmp_path / "inc.pdf", pages=3))
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    for p in range(3):
        c.place(view, p, pymupdf.Rect(400, 700, 500, 730), item)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: True)
    c.embed_all()
    with view.document.lock:
        assert all(not g.placed_signatures(view.document.pdf, p) for p in range(3))
    view.document.undo()
    with view.document.lock:
        assert all(len(g.placed_signatures(view.document.pdf, p)) == 1 for p in range(3))


def test_guides_include_form_fields():
    pdf = pymupdf.open()
    page = pdf.new_page()
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "Nome"
    w.rect = pymupdf.Rect(300, 500, 450, 520)
    page.add_widget(w)
    xs, ys, _lines = g.page_guides(pdf[0])
    assert 300 in [round(x) for x in xs] and 520 in [round(y) for y in ys]


def test_online_revocation_only_on_request(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """NFR-19 / SIG-16: senza richiesta la verifica non usa la rete; «Verifica online» sì."""
    data = digital.sign_pdf(Path(sample_pdf).read_bytes(), id_a[2], digital.SignOptions())
    p = tmp_path / "online.pdf"
    p.write_bytes(data)
    calls = []
    real = digital.verify_pdf

    def spy(data, **kw):
        calls.append((kw.get("revocation_mode"), kw.get("allow_fetching")))
        return real(data, **{**kw, "allow_fetching": False, "revocation_mode": "mai"})

    monkeypatch.setattr(digital, "verify_pdf", spy)
    window.open_path(str(p))
    assert calls == [("mai", False)]
    panel = _panel(window)
    assert not panel.btn_online.isHidden()
    panel.btn_online.click()
    assert calls[-1] == ("sempre", True)
