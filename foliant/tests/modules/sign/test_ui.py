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
    """SIG-21: la conferma arriva PRIMA della modifica invalidante; «Annulla» non modifica nulla."""
    from foliant.api import OperationCancelled
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "w.pdf", id_a)
    c = _ctl(window)
    asked = []
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    before = view.document.version
    with pytest.raises(OperationCancelled):
        with view.document.change("Ruota pagina", kind="pages") as pdf:
            pdf[0].set_rotation(90)
    assert asked == ["Ruota pagina"]
    assert not view.document.is_modified and view.document.version == before
    # le modifiche consentite (annotazioni) passano senza domande
    with view.document.change("Nota", kind="annotation") as pdf:
        pdf[0].add_text_annot((300, 300), "ok")
    assert asked == ["Ruota pagina"] and view.document.is_modified
    view.document.undo()
    # «Lavora su una copia senza firme»
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: "copy")
    with pytest.raises(OperationCancelled):
        with view.document.change("Ruota pagina", kind="pages") as pdf:
            pdf[0].set_rotation(90)
    QApplication.processEvents()
    copy_view = window.current_view()
    assert copy_view is not view and "senza firme" in copy_view.document.display_name
    assert not digital.has_signatures(copy_view.document.to_bytes())
    # «Procedi comunque»: l'avviso non si ripete
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "proceed")
    window.tabs.setCurrentWidget(view)
    with view.document.change("Ruota pagina", kind="pages") as pdf:
        pdf[0].set_rotation(90)
    with view.document.change("Ruota pagina", kind="pages") as pdf:
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
    QApplication.processEvents()
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
    with view.document.change("Nota", kind="annotation") as pdf:
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
    with view.document.change("Testo", kind="content") as pdf:
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
        with view.document.change("Nota", kind="annotation") as pdf:
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
    with doc.change("Nota", kind="annotation") as pdf:
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


def test_timestamp_wrong_credentials_asks_again(window, monkeypatch, sample_pdf, tmp_path, id_a, tsa):
    from PySide6.QtWidgets import QInputDialog

    from foliant.modules.sign import ui_digital
    view = window.open_path(sample_pdf)
    c = _ctl(window)
    target = tmp_path / "cred.pdf"

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(timestamp_url="https://tsa.example.org", timestamp_auth=("mario", "sbagliata"))
        return True

    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(target))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("giusta", True))
    attempts = []
    real = digital.sign_pdf

    def sign(data, did, opts):
        attempts.append(opts.timestamp_auth)
        if opts.timestamp_auth[1] != "giusta":
            raise digital.TimestampError("Il server di marca temporale ha rifiutato le credenziali", "credenziali")
        opts.timestamper = tsa[0]
        return real(data, did, opts)

    monkeypatch.setattr(digital, "sign_pdf", sign)
    assert c.digital_sign(view, invisible=True)
    assert attempts == [("mario", "sbagliata"), ("mario", "giusta")]


def test_required_unsigned_fields_warning(window, monkeypatch, tmp_path):
    """SIG-20: prima di stampare/inviare un documento con campi obbligatori non firmati compare l'avviso."""
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "obb.pdf", pages=1)).read_bytes()
    data, _n = digital.add_signature_field(data, "Cliente", 0, (300, 700, 500, 760), required=True,
                                           tooltip="Firma del cliente")
    p = tmp_path / "obbligatorio.pdf"
    p.write_bytes(data)
    view = window.open_path(str(p))
    c = _ctl(window)
    assert [f.name for f in c.missing_required_signatures(view)] == ["Cliente"]
    asked = []
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: asked.append(m) or False)
    assert c.confirm_unsigned_required(view, "Invia per e-mail") is False
    assert "Firma del cliente" in asked[0] and "Invia per e-mail" in asked[0]
    # agganciato alle guardie del nucleo: stampa e Salva con nome
    assert window.ctx.run_guards("print", view) is False
    assert "Stampa" in asked[-1]
    assert window.ctx.run_guards("save_as", view, {"copy": False}) is False
    assert "Salva con nome" in asked[-1]
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: True)
    assert window.ctx.run_guards("print", view) is True


def test_sign_password_protected_document(window, monkeypatch, tmp_path, id_a):
    """Contratto protetto con password (cifrato da PyMuPDF) → firma digitale → resta cifrato e la firma è valida."""
    from tests.conftest import make_pdf
    src = Path(make_pdf(tmp_path / "contratto.pdf", pages=3))
    d0 = pymupdf.open(str(src))
    prot = tmp_path / "protetto.pdf"
    d0.save(str(prot), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="Orologi2026", owner_pw="Orologi2026")
    d0.close()
    store.TrustStore().add(id_a[1])
    from foliant.modules.sign import ui_digital
    view = window.open_path(str(prot), "Orologi2026")
    c = _ctl(window)
    target = tmp_path / "protetto_firmato.pdf"

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(reason="Approvo")
        return True

    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(target))
    monkeypatch.setattr(window, "open_path", lambda p, pw=None: type(window).open_path(window, p, "Orologi2026"))
    assert c.digital_sign(view, page=1, rect=pymupdf.Rect(100, 600, 300, 680))
    data = target.read_bytes()
    assert data.startswith(prot.read_bytes())
    r = pymupdf.open(stream=data)
    assert r.needs_pass and r.authenticate("Orologi2026")
    st = digital.verify_pdf(data, trusted=[id_a[1]], password="Orologi2026")
    assert st and st[0].status == "valida", st and st[0].reasons
    new_view = window.current_view()
    assert [s.status for s in c.state(new_view).statuses] == ["valida"]


@pytest.mark.parametrize("width", [243, 320, 600])
def test_panel_buttons_never_below_minimum_size(window, qtbot, monkeypatch, sample_pdf, tmp_path, id_a, width):
    """NFR-24: nessun pulsante del pannello Firme è stretto sotto il suo minimumSizeHint."""
    from foliant.modules.sign.panel import SignaturesPanel
    _sign_file(window, monkeypatch, sample_pdf, tmp_path / "pn.pdf", id_a)
    panel = SignaturesPanel(_ctl(window))
    qtbot.addWidget(panel)
    panel.set_view(window.current_view())
    panel.resize(width, 800)
    panel.show()
    QApplication.processEvents()
    buttons = [b for b in panel.top_buttons.buttons + panel.action_buttons.buttons if b.isVisible()]
    assert len(buttons) >= 5
    for b in buttons:
        assert b.width() >= b.minimumSizeHint().width(), (b.text(), b.width(), b.minimumSizeHint().width())
    if width >= 600:
        assert panel.action_buttons._columns == 2


def test_certified_no_changes_and_signed_version_are_read_only(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """SIG-17: documento certificato «nessuna modifica» e versione firmata aperti in sola lettura."""
    from foliant.api import OperationCancelled
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "ro.pdf", id_a, certify=True, level=1)
    doc = view.document
    assert doc.locked and "nessuna modifica" in doc.locked_reason
    with pytest.raises(OperationCancelled):
        with doc.change("Nota", kind="annotation") as pdf:
            pdf[0].add_text_annot((10, 10), "x")
    assert not doc.is_modified
    c = _ctl(window)
    sv = c.open_signed_version(view, c.state(view).statuses[0])
    assert sv.document.locked and "sola lettura" in sv.document.locked_reason


def test_document_banner_for_signatures_and_fields(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """SIG-18/SIG-20: barra sopra il documento con lo stato delle firme e i campi da firmare."""
    from tests.conftest import make_pdf
    store.TrustStore().add(id_a[1])
    view = _sign_file(window, monkeypatch, sample_pdf, tmp_path / "bn.pdf", id_a)
    assert "firme" in window.ctx.banners(view)
    data = Path(make_pdf(tmp_path / "campi.pdf", pages=2)).read_bytes()
    data, _n = digital.add_signature_field(data, "Venditore", 0, (60, 700, 250, 760))
    data, _n = digital.add_signature_field(data, "Acquirente", 1, (300, 700, 500, 760))
    p = tmp_path / "campi-vuoti.pdf"
    p.write_bytes(data)
    v2 = window.open_path(str(p))
    assert "firme" in window.ctx.banners(v2)
    banner = window.ctx.show_banner(v2, "firme", "x")    # stesso id: restituisce l'avviso aggiornabile
    assert banner is not None
    _ctl(window).update_banner(v2)
    pages = []
    _ctl(window)._banner_next_field(v2)
    pages.append(v2.current_page)
    _ctl(window)._banner_next_field(v2)
    pages.append(v2.current_page)
    assert pages == [0, 1]
    plain = window.open_path(make_pdf(tmp_path / "semplice.pdf"))
    assert "firme" not in window.ctx.banners(plain)


def test_author_name_from_core_preference(window, sample_pdf):
    """La firma digitata e l'autore delle firme grafiche usano il nome dell'autore del nucleo."""
    window.settings.setValue("author", "Giulia Verdi")
    c = _ctl(window)
    assert c.default_name() == "Giulia Verdi"
    view = window.open_path(sample_pdf)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]])))
    xref = c.place(view, 0, pymupdf.Rect(100, 100, 200, 130), item)
    with view.document.lock:
        assert view.document.pdf.xref_get_key(xref, "T")[1] == "Giulia Verdi"
    window.settings.remove("author")


def test_permissions_from_core_with_owner_unlock(window, monkeypatch, tmp_path, id_a):
    """Permessi letti con Document.allows; senza permesso si propone lo sblocco da proprietario (unlock)."""
    from PySide6.QtWidgets import QInputDialog
    d0 = pymupdf.open()
    d0.new_page()
    p = tmp_path / "solo-stampa.pdf"
    d0.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="proprietario",
            permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(p), "utente")
    c = _ctl(window)
    assert not view.document.allows(pymupdf.PDF_PERM_ANNOTATE)
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("sbagliata", True))
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]])))
    c.start_placement(view, item)
    assert errors and view.tool is not c.tool_for(view) or c.tool_for(view).pending is None
    assert c.digital_sign(view, invisible=True) is False
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("proprietario", True))
    c.start_placement(view, item)
    assert view.document.access == "owner" and c.tool_for(view).pending is item
    assert c.place(view, 0, pymupdf.Rect(10, 10, 100, 40), item)


def test_redaction_on_signed_document_warns_before_full_rewrite(window, monkeypatch, sample_pdf, tmp_path, id_a):
    """NFR-09: redazione su PDF firmato → Salva → avviso; Annulla → file su disco invariato;
    «Salva una copia» → copia scritta, originale firmato intatto."""
    target = tmp_path / "red.pdf"
    view = _sign_file(window, monkeypatch, sample_pdf, target, id_a)
    signed = target.read_bytes()
    c = _ctl(window)
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: "proceed")
    doc = view.document
    with doc.change("Redazione", kind="redaction") as pdf:
        pdf[0].add_redact_annot(pymupdf.Rect(60, 120, 400, 150))
        pdf[0].apply_redactions()
    assert doc.has_signatures and not doc.saves_incrementally
    asked = []
    monkeypatch.setattr(c, "_ask_full_rewrite", lambda v, save_as: asked.append(save_as) or "cancel")
    assert window.save_view(view) is False
    assert asked == [False] and target.read_bytes() == signed and doc.is_modified
    copy = tmp_path / "red (modificato).pdf"
    monkeypatch.setattr(c, "_ask_full_rewrite", lambda v, save_as: "copy")
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(copy))
    assert window.save_view(view) is False
    assert target.read_bytes() == signed and copy.exists()
    assert b"Orologeria" not in pymupdf.open(str(copy))[0].get_text().encode()


def test_fill_parent_kids_field_in_window_keeps_signature_valid(window, monkeypatch, tmp_path, id_a):
    """Compilazione dall'interfaccia (motore dei moduli) di un campo padre + widget figlio su un
    documento firmato, poi Salva: la firma resta valida con «compilazione di campi modulo»."""
    from foliant.modules.forms import engine as fe
    doc0 = pymupdf.open()
    page = doc0.new_page()
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "acquirente"
    w.rect = pymupdf.Rect(100, 100, 300, 120)
    page.add_widget(w)
    signed = digital.sign_pdf(doc0.tobytes(), id_a[2], digital.SignOptions(rect=(100, 300, 300, 360)))
    p = tmp_path / "modulo-firmato.pdf"
    p.write_bytes(signed)
    store.TrustStore().add(id_a[1])
    view = window.open_path(str(p))
    asked = []
    monkeypatch.setattr(_ctl(window), "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    with view.document.lock:
        xref = next(view.document.pdf[0].widgets()).xref
    with view.document.change("Compila campo", kind="form") as pdf:
        fe.set_value(pdf, 0, xref, "Luigi Bianchi")
    assert asked == []
    assert window.save_view(view)
    out = p.read_bytes()
    assert out.startswith(signed)
    st = digital.verify_pdf(out, trusted=[id_a[1]])[0]
    assert st.status == "valida" and "compilazione di campi modulo" in st.modifications


def test_field_policy_filters_ids(window, qtbot, tmp_path, id_a, policy_id):
    """SIG-19/20: un campo che richiede una policy rende selezionabile solo l'ID con quella policy."""
    from foliant.modules.sign.ui_digital import DigitalSignDialog, FieldDialog
    from tests.conftest import make_pdf

    from .conftest import POLICY_OID
    fd = FieldDialog(window)
    qtbot.addWidget(fd)
    fd.policies.setText("1.2.x")
    fd._accept()
    assert "OID" in fd.error.text() and not fd.result()
    fd.policies.setText(f" {POLICY_OID} ;")
    assert fd.allowed_policies() == [POLICY_OID]
    data = Path(make_pdf(tmp_path / "policy.pdf", pages=1)).read_bytes()
    data, _n = digital.add_signature_field(data, "Cliente", 0, (300, 700, 500, 760), allowed_policies=[POLICY_OID])
    field = digital.signature_fields(data)[0]
    store.IDRegistry().add(id_a[0], id_a[1])
    store.IDRegistry().add(policy_id[0], policy_id[1])
    dlg = DigitalSignDialog(window, _ctl(window), field=field)
    qtbot.addWidget(dlg)
    model = dlg.id_combo.model()
    enabled = [dlg.id_combo.itemText(i) for i in range(dlg.id_combo.count()) if model.item(i).isEnabled()]
    assert enabled == ["Giulia Verdi"]
    assert POLICY_OID in dlg.cert_info.text()


def test_no_compatible_id_offers_import_or_graphic_only(window, qtbot, monkeypatch, tmp_path, id_a, id_b):
    """SIG-20: nessun ID conforme ai vincoli → requisiti spiegati, firma e «Crea…» disattivati,
    «Solo firma grafica» inserisce la firma della libreria dentro il campo, che resta da firmare."""
    from foliant.modules.sign import ui_digital
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "vincolo.pdf", pages=1)).read_bytes()
    data, _n = digital.add_signature_field(data, "Cliente", 0, (300, 600, 500, 680), allowed_issuers=[id_b[1]],
                                           tooltip="Firma del cliente")
    path = tmp_path / "vincolo2.pdf"
    path.write_bytes(data)
    store.IDRegistry().add(id_a[0], id_a[1])
    view = window.open_path(str(path))
    c = _ctl(window)
    field = digital.signature_fields(data)[0]
    dlg = ui_digital.DigitalSignDialog(window, c, field=field)
    qtbot.addWidget(dlg)
    assert dlg.no_compatible and not dlg.ok.isEnabled() and not dlg.create_id.isEnabled()
    assert "requisiti" in dlg.error.text() and dlg.graphic_btn is not None
    dlg._reload_ids(str(id_b[0]))           # «Sfoglia…» con un ID adatto riattiva la firma
    assert dlg.ok.isEnabled()
    lib = c.library(interactive=False)
    lib.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (90, 15, 1), (150, 0, 1)]]))),
            "Mario")
    infos = []
    monkeypatch.setattr(window.ctx, "info", lambda title, text: infos.append(text))
    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", lambda d: (d._graphic_only(), False)[1])
    assert not c.digital_sign(view, field=field)
    with view.document.lock:
        [placed] = g.placed_signatures(view.document.pdf, 0)
        r = pymupdf.Rect(placed.rect)
    box = pymupdf.Rect(field.rect)
    assert box.contains(r) and (abs(r.width - box.width) < 1 or abs(r.height - box.height) < 1)
    assert infos and "Firma del cliente" in infos[0]
    assert [f.name for f in c.empty_fields(view)] == ["Cliente"]


def test_after_signing_field_goes_to_next(window, monkeypatch, tmp_path, id_a):
    """SIG-20 punto 4: firmato un campo, la vista passa al campo successivo da firmare."""
    from foliant.modules.sign import ui_digital
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "due.pdf", pages=2)).read_bytes()
    data, _n = digital.add_signature_field(data, "Venditore", 0, (60, 600, 260, 680))
    data, _n = digital.add_signature_field(data, "Acquirente", 1, (300, 600, 500, 680), tooltip="Acquirente")
    path = tmp_path / "due2.pdf"
    path.write_bytes(data)
    view = window.open_path(str(path))
    c = _ctl(window)
    field = [f for f in digital.signature_fields(data) if f.name == "Venditore"][0]

    def fake_exec(dlg):
        dlg.digital_id = id_a[2]
        dlg.options = digital.SignOptions(reason="Vendita")
        return True
    went = []
    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", fake_exec)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(tmp_path / "due_firmato.pdf"))
    monkeypatch.setattr(type(c), "go_to_field", lambda self, v, page, rect: went.append(page))
    assert c.digital_sign(view, field=field)
    assert went == [1]


def test_banner_explains_missing_sign_permission(window, tmp_path):
    """SIG-20: documento cifrato senza permesso di firma → spiegazione al posto di «Firma ora»."""
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "base.pdf", pages=1)).read_bytes()
    data, _n = digital.add_signature_field(data, "Cliente", 0, (300, 600, 500, 680))
    doc = pymupdf.open(stream=data)
    out = tmp_path / "cifrato.pdf"
    doc.save(str(out), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="proprietario", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(out))
    c = _ctl(window)
    calls = []
    real = window.ctx.show_banner
    window.ctx.show_banner = lambda v, bid, text, actions=(), **k: (calls.append((text, [a[0] for a in actions])),
                                                                   real(v, bid, text, actions, **k))
    c.update_banner(view)
    text, actions = calls[-1]
    assert "password del proprietario" in text and "Firma ora" not in actions


def test_on_save_preference_keep_or_embed(window, qtbot, tmp_path):
    """SIG-05 punto 5: al salvataggio vale «Mantieni come annotazione» (predefinita) o «Incorpora»."""
    from foliant.modules.sign.ui_digital import SignPreferences
    from tests.conftest import make_pdf
    path = Path(make_pdf(tmp_path / "pref.pdf", pages=1))
    view = window.open_path(str(path))
    c = _ctl(window)
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    c.place(view, 0, pymupdf.Rect(400, 700, 500, 730), item)
    assert c.on_save_mode() == "keep"
    assert window.ctx.save_current()
    assert len(g.placed_signatures(pymupdf.open(str(path)), 0)) == 1      # annotazione salvata
    page = SignPreferences(None, c)
    qtbot.addWidget(page)
    page.on_save.setCurrentIndex(page.on_save.findData("embed"))
    page.apply()
    assert c.on_save_mode() == "embed"
    c.place(view, 0, pymupdf.Rect(100, 700, 200, 730), item)
    assert window.ctx.save_current()
    saved = pymupdf.open(str(path))
    assert not g.placed_signatures(saved, 0) and saved[0].get_drawings()   # incorporate nel contenuto
    view.document.undo()                                                   # un solo passo annullabile
    with view.document.lock:
        assert len(g.placed_signatures(view.document.pdf, 0)) == 2


def test_on_save_embed_skips_digitally_signed(window, monkeypatch, tmp_path, id_a):
    """Con «Incorpora» un documento firmato digitalmente mantiene le firme grafiche come annotazioni."""
    from tests.conftest import make_pdf
    src = Path(make_pdf(tmp_path / "s.pdf", pages=1))
    signed = tmp_path / "s_firmato.pdf"
    signed.write_bytes(digital.sign_pdf(src.read_bytes(), id_a[2], digital.SignOptions()))
    view = window.open_path(str(signed))
    c = _ctl(window)
    window.ctx.settings().setValue(c.ON_SAVE_KEY, "embed")
    item = g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1), (90, 0, 1)]])))
    c.place(view, 0, pymupdf.Rect(400, 700, 500, 730), item)
    assert c._embed_on_save_guard(view, {"event": "save"})
    with view.document.lock:
        assert len(g.placed_signatures(view.document.pdf, 0)) == 1


def test_library_export_import_commands(qtbot, monkeypatch, tmp_path):
    """SIG-07: «Esporta…» e «Importa…» del dialogo Libreria: 5 elementi esportati e importati in un profilo
    vuoto con nomi, tipi e impronte uguali; password sbagliata → niente importato; duplicati gestiti."""
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from foliant.modules.sign import ui_library
    from foliant.modules.sign.library import SignatureLibrary
    src = SignatureLibrary(tmp_path / "profilo1")
    for i in range(5):
        kind = "iniziali" if i == 4 else "firma"
        src.add(g.SignatureItem(kind=kind, art=g.strokes_to_art(g.process_strokes(
            [[(0, 0, 1), (40 + i * 10, 10, 1), (90, 0, 1)]]))), f"Elemento {i}")
    pkg = tmp_path / "firme.foliant-signatures"
    answers = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(pkg), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(pkg), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text: warnings.append(text))
    monkeypatch.setattr(ui_library.ExportPickDialog, "exec", lambda self: True)
    dlg = ui_library.LibraryDialog(src)
    qtbot.addWidget(dlg)
    answers[:] = [("Pacchetto-2026", True), ("Pacchetto-2026", True)]
    assert dlg.export_items() == 5 and pkg.is_file()

    dst = SignatureLibrary(tmp_path / "profilo2")
    dlg2 = ui_library.LibraryDialog(dst)
    qtbot.addWidget(dlg2)
    clicks = []

    def fake_exec(box):
        texts = {b.text(): b for b in box.buttons()}
        clicks.append(sorted(texts))
        box._chosen = texts.get(_choice[0])
        return 0
    _choice = ["Importa"]
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box._chosen)
    answers[:] = [("sbagliata", True)]
    dlg2._do("import")                                   # password errata: messaggio, niente importato
    assert dst.entries() == [] and "Password errata" in warnings[-1]
    assert not [p for p in tmp_path.rglob("*") if p.suffix in (".svg", ".png", ".json") and "profilo2" in str(p)
                and p.name != "manifest.json"]
    answers[:] = [("Pacchetto-2026", True)]
    res = dlg2.import_items()
    assert res["added"] == 5
    key = sorted((e.name, e.kind, e.sha256) for e in src.entries())
    assert sorted((e.name, e.kind, e.sha256) for e in dst.entries()) == key
    _choice[0] = "Mantieni entrambi"                     # di nuovo: tutti duplicati
    answers[:] = [("Pacchetto-2026", True)]
    res = dlg2.import_items()
    assert "Sostituisci i duplicati" in clicks[-1] and res["added"] == 5 and len(dst.entries()) == 10


def test_library_sync_dialog_and_periodic_cycle(window, qtbot, monkeypatch, tmp_path):
    """SIG-07: «Sincronizza con cartella» collega la libreria; il secondo profilo collegato alla stessa
    cartella riceve le firme nel ciclo successivo (≤ 60 s) senza riavvio; cartella irraggiungibile →
    «sincronizzazione sospesa» e la libreria locale continua a funzionare."""
    import keyring
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from foliant.modules.sign import sync, ui_library
    from foliant.modules.sign.library import SignatureLibrary
    saved = {}
    monkeypatch.setattr(keyring, "set_password", lambda svc, u, p: saved.__setitem__((svc, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda svc, u: saved.get((svc, u)))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    folder = tmp_path / "cartella-condivisa"
    folder.mkdir()
    c = _ctl(window)
    lib = c.library(interactive=False)
    lib.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (60, 12, 1)]]))), "Firma negozio")
    answers = [("Passphrase-Negozio", True), ("Passphrase-Negozio", True)]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    dlg = ui_library.LibraryDialog(lib, window, controller=c)
    qtbot.addWidget(dlg)
    sd = ui_library.SyncDialog(lib, c, dlg)
    qtbot.addWidget(sd)
    sd.choose_folder()
    assert sync.configured_folder(lib) == str(folder.resolve())
    assert list(saved.values()) == ["Passphrase-Negozio"]
    assert c._sync_timer.isActive() and c._sync_timer.interval() <= 60_000
    assert "attiva" in c.sync_status and "attiva" in sd.state.text()
    # secondo profilo (altro computer) collegato alla stessa cartella
    other = SignatureLibrary(tmp_path / "portatile")
    sync.connect(other, folder, "Passphrase-Negozio")
    assert [e.name for e in other.entries()] == ["Firma negozio"]
    other.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (80, 5, 1)]]))), "Dal portatile")
    sync.sync(other, "Passphrase-Negozio")
    with qtbot.waitSignal(c.librarySynced, timeout=2000):
        c._sync_timer.timeout.emit()                      # ciclo periodico
    assert sorted(e.name for e in lib.entries()) == ["Dal portatile", "Firma negozio"]
    assert "Dal portatile" in [dlg.list.item(i).text().replace("  ★", "") for i in range(dlg.list.count())]
    # cartella non raggiungibile: sospesa, la libreria locale funziona
    folder.rename(tmp_path / "staccata")
    c._sync_timer.timeout.emit()
    assert c.sync_status.startswith("Sincronizzazione sospesa")
    lib.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (30, 9, 1)]]))), "Offline")
    (tmp_path / "staccata").rename(folder)
    c._sync_timer.timeout.emit()
    assert "attiva" in c.sync_status
    sync.sync(other, "Passphrase-Negozio")
    assert "Offline" in [e.name for e in other.entries()]


def test_ctx_request_signature_opens_dialog_with_field(window, monkeypatch, tmp_path):
    """Per Moduli: ctx.request_signature(view, "Firma1") apre la firma con il campo preselezionato."""
    from foliant.modules.sign import ui_digital
    from tests.conftest import make_pdf
    data = Path(make_pdf(tmp_path / "campo.pdf", pages=1)).read_bytes()
    data, name = digital.add_signature_field(data, "Firma1", 0, (300, 600, 500, 680))
    p = tmp_path / "campo2.pdf"
    p.write_bytes(data)
    view = window.open_path(str(p))
    seen = []
    monkeypatch.setattr(ui_digital.DigitalSignDialog, "exec", lambda d: seen.append(d.field.name) or False)
    assert window.ctx.request_signature(view, "Firma1") is True
    assert seen == ["Firma1"]
    assert window.ctx.request_signature(view, "Inesistente") is False


def test_ids_dialog_empty_lists_and_selection_buttons(window, qtbot, id_a):
    """Elenchi vuoti con spiegazione; comandi sull'elemento attivi solo con una selezione."""
    from foliant.modules.sign.ui_digital import IDsDialog
    dlg = IDsDialog(window, _ctl(window))
    qtbot.addWidget(dlg)
    for lst in (dlg.ids, dlg.trusted, dlg.tsas):
        assert lst.count() == 1 and not lst.item(0).flags() & Qt.ItemFlag.ItemIsSelectable
        assert all(not b.isEnabled() for b in dlg._needs_selection[lst])
    assert "p12" in dlg.ids.item(0).text()
    store.IDRegistry().add(id_a[0], id_a[1])
    dlg.refresh()
    assert dlg.ids.count() == 1 and dlg.ids.item(0).flags() & Qt.ItemFlag.ItemIsSelectable
    assert all(not b.isEnabled() for b in dlg._needs_selection[dlg.ids])
    dlg.ids.setCurrentRow(0)
    assert all(b.isEnabled() for b in dlg._needs_selection[dlg.ids])
    dlg.ids.clearSelection()
    assert all(not b.isEnabled() for b in dlg._needs_selection[dlg.ids])


def _profile_traces(needle: str) -> list[str]:
    """File del profilo isolato (dati, configurazione, stato) il cui nome o contenuto contiene `needle`."""
    found = []
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "APPDATA", "LOCALAPPDATA"):   # Linux e Windows
        root = os.environ.get(var)
        if not root:
            continue
        for base, _dirs, names in os.walk(root):
            for n in names:
                f = os.path.join(base, n)
                try:
                    if needle in f or needle.encode() in Path(f).read_bytes():
                        found.append(f)
                except OSError:
                    pass
    return found


def test_no_document_names_in_profile(window, monkeypatch, tmp_path, id_a):
    """Riservatezza: firmare e verificare un documento non lascia nel profilo né nelle impostazioni
    del modulo il nome o la cartella del documento (restano solo ID digitali e certificati)."""
    from tests.conftest import make_pdf
    folder = tmp_path / "zq7k-cartella"
    folder.mkdir()
    src = make_pdf(folder / "zq7k-contratto.pdf")
    store.TrustStore().add(id_a[1])
    view = _sign_file(window, monkeypatch, src, folder / "zq7k-contratto_firmato.pdf", id_a)
    assert [s.status for s in _ctl(window).state(view).statuses] == ["valida"]
    s = window.ctx.settings()
    s.sync()
    assert all("zq7k" not in str(s.value(k)) for k in s.allKeys() if k.startswith("sign"))
    assert _profile_traces("trusted")          # controllo: la ricerca vede davvero il profilo isolato
    assert _profile_traces("zq7k") == []
