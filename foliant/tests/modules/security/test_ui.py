"""Flussi dell'interfaccia del modulo Proteggi (dialoghi simulati con monkeypatch)."""

from __future__ import annotations

import hashlib
import socket

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QMessageBox

from tests.conftest import perf_limit
from foliant.api import PageEvent, TextSelection
from foliant.modules import security
from foliant.modules.security import controller as C
from foliant.modules.security import dialogs as D
from foliant.modules.security import links as L
from foliant.modules.security import protection as P
from foliant.modules.security import redaction as R


@pytest.fixture
def sec(window):
    c = security.CONTROLLER
    assert c is not None and c.ctx is window.ctx
    return c


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """SEC-34: nessuna funzione del modulo apre connessioni di rete."""
    def refuse(*a, **k):
        raise AssertionError("connessione di rete non prevista")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(autouse=True)
def no_unexpected_dialogs(monkeypatch):
    """Un dialogo modale non previsto bloccherebbe i test: lo trasformiamo in errore."""
    from PySide6.QtWidgets import QDialog, QInputDialog

    def unexpected(*a, **k):
        raise AssertionError(f"dialogo inatteso: {a[1:3] if len(a) > 2 else a}")
    for name in ("information", "warning", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, unexpected)
    # QMessageBox.exec senza scelta: equivale ad «Annulla» (anche alla chiusura della finestra)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QDialog, "exec", unexpected)
    monkeypatch.setattr(QInputDialog, "getText", unexpected)


@pytest.fixture
def accept_all(monkeypatch):
    monkeypatch.setattr(C, "confirm", lambda *a, **k: True)


def _ev(page, x, y, button=Qt.MouseButton.LeftButton):
    return PageEvent(page, pymupdf.Point(x, y), QPointF(0, 0), button, button, Qt.KeyboardModifier.NoModifier)


def _text(path) -> str:
    pdf = pymupdf.open(path)
    try:
        return "".join(p.get_text() for p in pdf)
    finally:
        pdf.close()


def _sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ----------------------------------------------------------------- modalità


def test_mode_and_actions(window, sec):
    assert "proteggi" in window.modes
    texts = [a.text() for a in window.modes["proteggi"].toolbar.actions() if a.text()]
    for t in ("Password e permessi", "Rimuovi protezione", "Proprietà di sicurezza", "Segna area",
              "Trova e segna", "Trova dati personali", "Segna pagine", "Applica redazioni", "Rimuovi dati nascosti",
              "Appiattisci"):
        assert t in texts
    assert not sec.actions["apply"].isEnabled()


def test_apply_disabled_without_marks(window, sec, sample_pdf):
    window.open_path(sample_pdf)
    act = sec.actions["apply"]
    assert not act.isEnabled()
    assert act.toolTip() == "Nessuna area contrassegnata per la redazione"


# --------------------------------------------------------------- protezione


class _FakeDialog:
    def __init__(self, settings, keep_open=False, keep_perm=False):
        self._s = settings
        self.keep_open_password = keep_open
        self.keep_permissions_password = keep_perm

    def settings(self):
        return self._s


def test_protect_flow(window, sec, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    monkeypatch.setattr(C, "ask_protection",
                        lambda parent, doc, **k: _FakeDialog(P.ProtectionSettings(open_password="Apri-123!")))
    assert sec.protect()
    out = tmp_path / "protetto.pdf"
    view.document.save(out)
    pdf = pymupdf.open(out)
    assert pdf.needs_pass and pdf.authenticate("Apri-123!")
    assert sec.lock_button.isVisible() or sec.lock_button.text() == "Accesso completo"


def test_protect_dialog_validation(qtbot):
    dlg = D.ProtectDialog(P.ProtectionSettings(), already_protected=False, has_open_password=False)
    qtbot.addWidget(dlg)
    dlg.open_pw.setText("Prova-2026!")
    dlg.open_pw2.setText("Prova-2026?")
    assert not dlg.apply_button.isEnabled()
    assert dlg.error.text() == "Le password non coincidono"
    dlg.open_pw2.setText("Prova-2026!")
    assert dlg.apply_button.isEnabled()
    # permessi: password uguale a quella di apertura → bloccato
    dlg.restrict.setChecked(True)
    dlg.perm_pw.setText("Prova-2026!")
    dlg.perm_pw2.setText("Prova-2026!")
    assert not dlg.apply_button.isEnabled() and "diversa" in dlg.error.text()
    dlg.perm_pw.setText("Capo-9999!")
    dlg.perm_pw2.setText("Capo-9999!")
    assert dlg.apply_button.isEnabled()
    # AES-256: accessibilità selezionata e non modificabile
    acc = dlg.perm_boxes["accessibility"]
    assert acc.isChecked() and not acc.isEnabled()
    # commenti → compilazione forzata
    dlg.perm_boxes["form"].setChecked(False)
    dlg.perm_boxes["annotate"].setChecked(True)
    assert dlg.perm_boxes["form"].isChecked() and not dlg.perm_boxes["form"].isEnabled()
    dlg.preset.setCurrentIndex(1)   # sola lettura
    assert not dlg.perm_boxes["copy"].isChecked()
    s = dlg.settings()
    assert s.restrict and s.permissions_password == "Capo-9999!"
    # livello di compatibilità: avviso
    # SEC-06 (v2): la nuova protezione usa solo AES-256
    assert dlg.method.count() == 1 and dlg.method.currentData() == P.AES256 and not dlg.method.isEnabled()


def test_protect_dialog_empty_password_disabled(qtbot):
    dlg = D.ProtectDialog(P.ProtectionSettings(), already_protected=False, has_open_password=False)
    qtbot.addWidget(dlg)
    assert not dlg.apply_button.isEnabled()


def test_weak_password_asks(qtbot, monkeypatch):
    dlg = D.ProtectDialog(P.ProtectionSettings(), already_protected=False, has_open_password=False)
    qtbot.addWidget(dlg)
    dlg.open_pw.setText("abc")
    dlg.open_pw2.setText("abc")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    dlg._accept()
    assert asked and dlg.result() != 1


def test_change_keeps_open_password(window, sec, tmp_path, sample_pdf, monkeypatch):
    first = tmp_path / "doc.pdf"
    from foliant.api import Document
    d = Document.open(sample_pdf)
    P.apply_protection(d, P.ProtectionSettings(open_password="vecchia-1"))
    d.save(first)
    d.close()
    view = window.open_path(str(first), "vecchia-1")
    s = P.ProtectionSettings(open_password="", permissions_password="Capo-1234", restrict=True,
                             allowed={"copy"})
    monkeypatch.setattr(C, "ask_protection", lambda parent, doc, **k: _FakeDialog(s, keep_open=True))
    assert sec.protect()
    view.document.save()
    pdf = pymupdf.open(first)
    assert pdf.authenticate("vecchia-1")
    assert pdf.permissions & pymupdf.PDF_PERM_COPY


def _owner_only(tmp_path):
    path = tmp_path / "limitato.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Testo riservato")
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY)
    return str(path)


def test_remove_wrong_owner_password(window, sec, tmp_path, monkeypatch, accept_all):
    path = _owner_only(tmp_path)
    before = _sha(path)
    view = window.open_path(path)
    assert sec.lock_button.text() == "Accesso limitato"
    errors = []
    monkeypatch.setattr(C, "ask_password", lambda *a: "sbagliata")
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(t))
    assert not sec.remove_protection()
    assert errors == ["Password non corretta"]
    assert view.document.is_protected and _sha(path) == before


def test_remove_with_owner_password(window, sec, tmp_path, monkeypatch, accept_all):
    path = _owner_only(tmp_path)
    view = window.open_path(path)
    monkeypatch.setattr(C, "ask_password", lambda *a: "capo")
    assert sec.remove_protection()
    assert sec.lock_button.text() == "Accesso completo"
    view.document.save()
    data = open(path, "rb").read()
    assert b"/Encrypt" not in data
    assert "Testo riservato" in _text(path)


def test_restricted_banner_and_unlock(window, sec, tmp_path, monkeypatch):
    view = window.open_path(_owner_only(tmp_path))
    banner = sec.banners[view]
    assert banner.current().key == "restricted" and banner.isVisible()
    monkeypatch.setattr(C, "ask_password", lambda *a: "capo")
    assert sec.unlock(view.document)
    assert banner.current() is None or banner.current().key != "restricted"


def test_restricted_blocks_redaction(window, sec, tmp_path, monkeypatch):
    view = window.open_path(_owner_only(tmp_path))
    monkeypatch.setattr(C, "ask_password", lambda *a: None)
    sec.activate_area_tool()
    assert view.tool.id != "security-redact-area"


def test_security_properties_rows(window, sec, tmp_path):
    path = tmp_path / "proprieta.pdf"
    src = pymupdf.open()
    src.new_page()
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(path))
    flat = []
    for head, lines in C.security_rows(view.document):
        flat.append(head)
        flat.extend(lines)
    joined = "\n".join(flat)
    rows = dict(C.security_rows(view.document))
    assert "AES, chiave a 256 bit" in joined and "Accesso: limitato" in joined
    assert "Stampare, solo a bassa risoluzione" in rows["Azioni consentite"]
    assert "Copiare testo e immagini" in rows["Azioni non consentite"]


def test_security_properties_unprotected(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    rows = C.security_rows(view.document)
    assert rows[0][0] == "Nessuna protezione"
    assert dict(rows)["Azioni non consentite"] == []


def test_rc4_marked_weak(window, sec, tmp_path):
    path = tmp_path / "rc4.pdf"
    src = pymupdf.open()
    src.new_page()
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_RC4_40, user_pw="", owner_pw="o", permissions=-1)
    view = window.open_path(str(path))
    assert any(h == "Protezione debole" for h, _l in C.security_rows(view.document))


# ---------------------------------------------------------------- redazione


def test_area_tool_marks_and_moves(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    sec.activate_area_tool()
    tool = view.tool
    assert tool.id == "security-redact-area"
    tool.press(view, _ev(0, 60, 75))
    tool._end = pymupdf.Point(300, 110)
    tool.release(view, _ev(0, 300, 110))
    doc = view.document
    with doc.lock:
        marks = R.marks(doc.pdf)
    assert len(marks) == 1 and "Pagina 1" in marks[0].text
    assert sec.panel.count.text() == "1"
    assert sec.panel.tree.topLevelItem(0).child(0).text(2) == "Da applicare"   # bug_11
    assert sec.actions["apply"].isEnabled()
    # testo ancora presente prima dell'applicazione
    assert "Pagina 1" in doc.page_text(0)
    # clic senza trascinamento → nessun segno
    tool.press(view, _ev(0, 400, 400))
    tool.release(view, _ev(0, 400, 400))
    # trascina il segno per spostarlo
    tool.press(view, _ev(0, 100, 90))
    tool._end = pymupdf.Point(100, 140)
    tool.release(view, _ev(0, 100, 140))
    with doc.lock:
        marks = R.marks(doc.pdf)
    assert len(marks) == 1 and abs(marks[0].rect.y0 - 125) < 1
    doc.undo()
    with doc.lock:
        assert abs(R.marks(doc.pdf)[0].rect.y0 - 75) < 1


def test_mark_selection_two_lines(window, sec, tmp_path, monkeypatch, accept_all):
    path = tmp_path / "sel.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Mario Rossi, nato", fontsize=12)
    page.insert_text((72, 114), "a Roma nel 1980", fontsize=12)
    src.save(str(path))
    view = window.open_path(str(path))
    words = view.words(0)
    chosen = [w for w in words if w[4] in ("Rossi,", "nato")]
    from foliant.api import selection_from_words
    view.set_text_selection(selection_from_words(0, chosen))
    assert sec.mark_selection() == 1
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    text = view.document.page_text(0)
    assert "Mario" in text and "a Roma" in text and "Rossi" not in text and "nato" not in text


def test_find_and_mark_terms(window, sec, tmp_path, monkeypatch):
    path = tmp_path / "nomi.pdf"
    src = pymupdf.open()
    for i in range(2):
        page = src.new_page()
        page.insert_text((72, 100), "Cliente Mario Rossi e MARIO ROSSI", fontsize=12)
        page.insert_text((72, 120), "firmato Mario", fontsize=12)
        page.insert_text((72, 134), "Rossi", fontsize=12)
    src.new_page()   # pagina senza testo
    src.set_metadata({"title": "Contratto Mario Rossi"})
    src.save(str(path))
    view = window.open_path(str(path))

    def fake_run(dlg):
        dlg.terms.setPlainText("mario rossi")
        dlg.outside.setChecked(True)
        dlg.run_search()
        assert "senza testo" in dlg.notice.text()
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    n = sec.find_and_mark("terms")
    assert n == 2 * (2 + 2)        # 3 occorrenze per pagina, una su due righe
    assert len(sec.pending_outside[view.document]) == 1
    monkeypatch.setattr(C, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    out = tmp_path / "nomi-redatto.pdf"
    view.document.save_copy(out)
    text = _text(out).lower()
    assert "rossi" not in text
    pdf = pymupdf.open(out)
    assert "Rossi" not in (pdf.metadata.get("title") or "")
    assert b"Rossi" not in out.read_bytes()


def test_find_patterns_dialog(window, sec, pii_pdf, monkeypatch, accept_all):
    view = window.open_path(pii_pdf)
    sec.show_panel()
    captured = {}

    def fake_run(dlg):
        dlg.run_search()
        captured["count"] = len(dlg.matches)
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    n = sec.find_and_mark("patterns")
    assert n >= 2 * 6 and captured["count"] >= 2 * 6
    assert sec.panel.count.text() == str(n)


def test_find_dialog_invalid_regex(window, sec, sample_pdf, qtbot):
    view = window.open_path(sample_pdf)
    dlg = D.FindDialog("patterns", lambda o: sec.search(view.document, o),
                       [("email", "Email", False)])
    qtbot.addWidget(dlg)
    dlg.custom.setText("(abc")
    dlg.run_search()
    assert "non valida" in dlg.notice.text()


def test_mark_pages_even(window, sec, tmp_path, monkeypatch):
    from tests.conftest import make_pdf, perf_limit
    path = make_pdf(tmp_path / "sei.pdf", pages=6)
    view = window.open_path(path)

    def fake_run(dlg):
        dlg.radios["even"].setChecked(True)
        dlg.overlay.setText("Pagina omessa")
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.mark_pages() == 3
    with view.document.lock:
        assert sorted(m.page for m in R.marks(view.document.pdf)) == [1, 3, 5]


def test_parse_pages():
    assert D.parse_pages("2, 4-5", 10) == [1, 3, 4]
    assert D.parse_pages("", 3) == [0, 1, 2]
    with pytest.raises(ValueError):
        D.parse_pages("0-2", 3)
    with pytest.raises(ValueError):
        D.parse_pages("x", 3)


def test_repeat_mark(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    doc = view.document
    with doc.change("Segna") as pdf:
        xref = R.add_mark(pdf, 0, pymupdf.Rect(50, 20, 200, 60), R.MarkStyle())
    assert sec.repeat_mark_on(doc, 0, xref, [1, 2]) == 2
    with doc.lock:
        marks = R.marks(doc.pdf)
    assert len(marks) == 3 and all(abs(m.rect.x0 - 50) < 0.01 for m in marks)


def test_panel_group_and_remove(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    sec.show_panel()
    doc = view.document
    with doc.change("Segna") as pdf:
        for page in (0, 0, 1, 2):
            R.add_mark(pdf, page, pymupdf.Rect(60, 75 + page, 300, 110), R.MarkStyle())
    panel = sec.panel
    assert panel.count.text() == "4" and panel.tree.topLevelItemCount() == 3
    # rimuovi il segno della pagina 2 (indice 1)
    item = panel.tree.topLevelItem(1).child(0)
    panel.tree.clearSelection()
    item.setSelected(True)
    panel._remove()
    assert panel.count.text() == "3"
    monkeypatch.setattr(C, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    sec.apply_redactions(None)
    assert "Pagina 2" in doc.page_text(1) and "Pagina 1" not in doc.page_text(0)


def test_apply_selected_only(window, sec, sample_pdf, monkeypatch, accept_all):
    view = window.open_path(sample_pdf)
    sec.show_panel()
    doc = view.document
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
        R.add_mark(pdf, 1, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    first = [m for m in sec.panel.marks if m.page == 0]
    assert sec.apply_redactions(first)
    assert "Pagina 1" not in doc.page_text(0) and "Pagina 2" in doc.page_text(1)
    with doc.lock:
        assert [m.page for m in R.marks(doc.pdf)] == [1]


def test_apply_cancel_keeps_everything(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    doc = view.document
    with doc.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    monkeypatch.setattr(C, "confirm", lambda *a: False)
    assert not sec.apply_redactions(None)
    assert "Pagina 1" in doc.page_text(0)


def test_apply_offers_save_redacted(window, sec, sample_pdf, tmp_path, monkeypatch, accept_all):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    names = []
    target = tmp_path / "esempio_redatto.pdf"
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda title, name, *a: names.append(name) or str(target))
    assert sec.apply_redactions(None)
    assert names == ["esempio_redatto.pdf"]
    assert "Pagina 1" not in _text(target)
    assert "Pagina 1" in _text(sample_pdf)    # originale intatto


def test_preview_overlay(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(100, 60, 500, 200), R.MarkStyle(fill=(0, 0, 0)))
    view.flush_rendering()
    r = view.page_to_view(0, pymupdf.Rect(445, 115, 455, 125))
    pt = r.center().toPoint()
    before = view.viewport().grab().toImage().pixelColor(pt)
    sec.set_preview(True)
    assert sec.panel.preview_button.isChecked()
    after = view.viewport().grab().toImage().pixelColor(pt)
    assert after.lightness() < 30 and before.lightness() > 60
    sec.set_preview(False)


def test_unapplied_marks_banner_on_reopen(window, sec, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        for i in range(3):
            R.add_mark(pdf, i, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    out = tmp_path / "con-segni.pdf"
    asked = []
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    # SEC-19: prima di salvare si chiede; «Annulla» non salva
    monkeypatch.setattr(C, "ask_unapplied", lambda parent, n: asked.append(n) or "cancel")
    assert not window.save_view(view, save_as=True) and not out.exists()
    # «Continua con i segni»: il file contiene i segni e il testo è ancora estraibile
    monkeypatch.setattr(C, "ask_unapplied", lambda parent, n: asked.append(n) or "keep")
    assert window.save_view(view, save_as=True)
    assert asked == [3, 3] and "Pagina 1" in _text(out)
    assert len(R.marks(pymupdf.open(out))) == 3
    view.document._saved_state = view.document._state
    window.close_view(view)
    view2 = window.open_path(str(out))
    notice = sec.banners[view2].current()
    assert notice.key == "unapplied" and "3 segni" in notice.text


def test_save_guard_apply_now(window, sec, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    out = tmp_path / "applicato.pdf"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(out))
    monkeypatch.setattr(C, "ask_unapplied", lambda parent, n: "apply")
    assert window.save_view(view, save_as=True)
    assert "Pagina 1" not in _text(out) and R.marks(pymupdf.open(out)) == []


def test_style_dialog_values(qtbot):
    dlg = D.StyleDialog(R.MarkStyle(), R.ApplyOptions(), ["OMISSIS"])
    qtbot.addWidget(dlg)
    dlg.fill.set_rgb((1, 1, 1))
    dlg._check()
    assert "bianco" in dlg.warning.text()
    dlg.text.setCurrentText("OMISSIS")
    dlg.repeat.setChecked(True)
    style, options = dlg.values()
    assert style.overlay_text == "OMISSIS" and style.repeat_text and options.images == R.IMAGES_PIXELS


def test_redaction_settings_and_codes(window, sec, tmp_path, monkeypatch):
    def fake_run(dlg):
        dlg.text.setCurrentText("Riservato cliente")
        dlg.fill.set_rgb((0.2, 0.1, 0.5))
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.redaction_settings()
    assert sec.style.overlay_text == "Riservato cliente" and "Riservato cliente" in sec.codes()
    out = tmp_path / "codici.json"
    sec.export_codes(str(out))
    sec.set_codes(["altro"])
    assert sec.import_codes(str(out))[-1] == "Riservato cliente"
    assert sec.codes()[0] == C.default_codes()[0]


def test_default_codes_translated(window, sec):
    from foliant.i18n import set_language
    sec.set_codes([])
    window.settings.remove(C.CODES_KEY)
    set_language("en")
    try:
        codes = sec.codes()
        assert "Dato personale" not in codes and "Pagina omessa" not in codes
        assert "Personal data" in codes and "Page omitted" in codes
    finally:
        set_language("it")
    assert "Dato personale" in sec.codes()


# ------------------------------------------------------- avvisi e collegamenti


def test_active_content_banner(window, sec, active_pdf, monkeypatch):
    view = window.open_path(active_pdf)
    notice = sec.banners[view].current()
    assert notice.key == "active"
    assert "script (1)" in notice.text and "collegamenti esterni (1)" in notice.text
    assert sec.banners[view].isVisible()
    rows = dict(C.active_rows(sec.scans[view.document]))
    assert rows["Script (1)"] == ["Apertura del documento: app.alert('ciao');"]
    assert rows["Collegamenti esterni (1)"] == ["https://esterno.example.com/traccia"]
    shown = []
    monkeypatch.setattr(C, "show_report", lambda dlg: shown.append(dlg) or False)
    sec.show_active_details(view)
    assert shown and shown[0].tree.topLevelItemCount() >= 2


def test_no_banner_for_plain_pdf(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    assert sec.banners[view].current() is None and not sec.banners[view].isVisible()


def test_trusted_document(window, sec, active_pdf):
    view = window.open_path(active_pdf)
    sec.trust_document(view)
    assert sec.banners[view].current() is None
    window.close_view(view)
    view = window.open_path(active_pdf)
    assert sec.banners[view].current() is None
    window.close_view(view)
    with open(active_pdf, "ab") as f:      # modificato da un altro programma
        f.write(b"\n% modifica esterna\n")
    view = window.open_path(active_pdf)
    assert sec.banners[view].current().key == "active"
    sec.revoke_trust()


def test_link_javascript_blocked(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    blocked, opened = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: blocked.append(a[2]))
    monkeypatch.setattr(L, "open_url", lambda uri: opened.append(uri))
    L.follow_external(view, {"kind": pymupdf.LINK_URI, "uri": "javascript:alert(1)"})
    assert blocked and not opened
    L.follow_external(view, {"kind": pymupdf.LINK_LAUNCH, "file": "/usr/bin/xterm"})
    assert len(blocked) == 2 and not opened


def test_link_confirm_and_allow_domain(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    opened, asked = [], []
    monkeypatch.setattr(L, "open_url", lambda uri: opened.append(uri))
    answers = iter(["cancel", "always", "open"])
    monkeypatch.setattr(L, "ask_open", lambda parent, uri, host, warnings: asked.append(host) or next(answers))
    link = {"kind": pymupdf.LINK_URI, "uri": "https://esempio.it/pagina", "from": pymupdf.Rect(0, 0, 1, 1)}
    L.follow_external(view, link)
    assert opened == [] and asked == ["esempio.it"]
    L.follow_external(view, link)          # «Sì a tutti»
    assert opened == ["https://esempio.it/pagina"]
    L.follow_external(view, link)          # ora senza conferma
    assert len(asked) == 2 and len(opened) == 2
    L.follow_external(view, {"kind": pymupdf.LINK_URI, "uri": "https://altro.it"})
    assert asked[-1] == "altro.it"


def test_link_homograph_warning():
    puny, warnings = L.host_warnings("еxample.com")   # «е» cirillica
    assert puny.startswith("xn--") and warnings


def test_core_link_hook_installed(window, sec):
    from foliant.ui import tools
    entries = [(p, h) for p, _s, h in tools.LINK_HANDLERS if h is L.handler]
    assert L._installed and entries == [(1000, L.handler)]


def test_internal_links_delegated(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    seen = []
    assert L.follow_external(view, {"kind": pymupdf.LINK_GOTO, "page": 1}) is False
    from foliant.ui import tools
    tools.follow_link(view, {"kind": pymupdf.LINK_GOTO, "page": 1})
    seen.append(view.current_page)
    assert seen == [1]


# --------------------------------------------------- sanificazione e altro


def test_sanitize_flow(window, sec, active_pdf, tmp_path, monkeypatch):
    view = window.open_path(active_pdf)
    monkeypatch.setattr(C, "run_dialog", lambda dlg: True)
    names = []
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda title, name, *a: names.append(name) or None)
    assert sec.sanitize()
    assert names == ["attivo_ripulito.pdf"]
    out = tmp_path / "sanificato.pdf"
    view.document.save(out)
    from foliant.modules.security import sanitize as S
    pdf = pymupdf.open(out)
    rep = S.scan(pdf)
    assert rep.javascript == 0 and rep.embedded_files == 0 and rep.comments == 0 and not rep.has_metadata
    assert b"JavaScript" not in out.read_bytes()
    assert "Documento con contenuti attivi" in pdf[0].get_text()


def test_inspect_flow(window, sec, active_pdf, monkeypatch):
    before = _sha(active_pdf)
    view = window.open_path(active_pdf)
    shown = []
    monkeypatch.setattr(C, "show_report", lambda dlg: shown.append(dlg) or False)
    rep = sec.inspect()
    assert rep.javascript == 1 and shown
    heads = [shown[0].tree.topLevelItem(i).text(0) for i in range(shown[0].tree.topLevelItemCount())]
    assert "Metadati" in heads and any(h.startswith("Script") for h in heads)
    assert not view.document.is_modified and _sha(active_pdf) == before


def test_metadata_flow(window, sec, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)

    def fake_run(dlg):
        dlg.edits["title"].setText("Relazione 2026 – Città")
        dlg.edits["author"].setText("Anna Bianchi")
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.edit_metadata()
    out = tmp_path / "meta.pdf"
    view.document.save(out)
    meta = pymupdf.open(out).metadata
    assert meta["title"] == "Relazione 2026 – Città" and meta["author"] == "Anna Bianchi"
    view.document.undo()
    with view.document.lock:
        assert view.document.pdf.metadata["title"] != "Relazione 2026 – Città"


def test_metadata_read_only_when_restricted(window, sec, tmp_path, monkeypatch):
    path = tmp_path / "ro.pdf"
    src = pymupdf.open()
    src.new_page()
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    window.open_path(str(path))
    seen = []
    monkeypatch.setattr(C, "run_dialog", lambda dlg: seen.append(all(e.isReadOnly() for e in dlg.edits.values()))
                        or True)
    assert not sec.edit_metadata()
    assert seen == [True]


def test_flatten_flow(window, sec, tmp_path, accept_all, monkeypatch):
    path = tmp_path / "commenti.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Testo", fontsize=12)
    a = page.add_rect_annot(pymupdf.Rect(100, 200, 200, 300))
    a.set_colors(stroke=(1, 0, 0))
    a.update()
    h = page.add_text_annot((300, 300), "nascosta")
    h.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
    h.update()
    src.save(str(path))
    view = window.open_path(str(path))
    seen = {}

    def fake_run(dlg):
        dlg.update_preview()
        seen["preview"] = not dlg.preview_label.pixmap().isNull()
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.flatten()
    assert seen["preview"]
    out = tmp_path / "piatto.pdf"
    view.document.save(out)
    pdf = pymupdf.open(out)
    # la nota nascosta non viene fusa: resta annotazione (opzione predefinita di ANN-40)
    assert [a.info["content"] for a in pdf[0].annots()] == ["nascosta"]
    assert pdf[0].get_drawings()


def test_flatten_dialog_options_and_marks(window, sec, tmp_path, accept_all, monkeypatch):
    """Ritaglio con segni di redazione non applicati: il ritaglio viene appiattito, i segni
    restano da applicare (non vengono applicati di nascosto)."""
    path = tmp_path / "ritaglio.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((20, 30), "ESTERNO", fontsize=12)
    page.insert_text((200, 400), "Codice cliente 4471", fontsize=12)
    page.set_cropbox(pymupdf.Rect(100, 100, 500, 700))
    src.save(str(path))
    view = window.open_path(str(path))
    sec.add_area_mark(0, pymupdf.Rect(90, 285, 250, 305))

    def fake_run(dlg):
        dlg.boxes["crop"].setChecked(True)
        dlg.pages.setText("9")
        dlg.accept()
        assert dlg.error.text()                     # pagina inesistente: resta aperto
        dlg.pages.setText("")
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.flatten()
    with view.document.lock:
        pdf = view.document.pdf
        assert "ESTERNO" not in pdf[0].get_text(clip=pymupdf.Rect(-999, -999, 999, 999))
        assert "Codice cliente 4471" in pdf[0].get_text()
    assert sec.has_marks(view.document)


def test_upgrade_rc4_to_aes256(window, sec, tmp_path, monkeypatch):
    path = tmp_path / "vecchio.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Vecchio", fontsize=12)
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_RC4_128, user_pw="apri", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ)
    view = window.open_path(str(path), "apri")
    monkeypatch.setattr(C, "ask_password", lambda *a: "capo")
    rows = C.security_rows(view.document)
    assert any(h == "Protezione debole" for h, _l in rows)
    monkeypatch.setattr(C, "show_report", lambda dlg: dlg.action_button.text() == "Aggiorna ad AES-256")
    sec.security_properties()
    view.document.save()
    pdf = pymupdf.open(path)
    assert "AES" in (pdf.metadata or {}).get("encryption", "") or pdf.needs_pass
    assert pdf.authenticate("apri") and not pdf.permissions & pymupdf.PDF_PERM_COPY
    pdf2 = pymupdf.open(path)
    assert pdf2.authenticate("capo") & 4
    assert "256" in pdf2.metadata["encryption"]


def test_find_dialog_import_terms(window, sec, sample_pdf, tmp_path, qtbot):
    view = window.open_path(sample_pdf)
    lst = tmp_path / "termini.txt"
    lst.write_text("Orologeria\nVerdi\n\n", encoding="utf-8")
    dlg = D.FindDialog("terms", lambda o: sec.search(view.document, o))
    qtbot.addWidget(dlg)
    dlg.terms.setPlainText("Pagina")
    dlg.load_terms(str(lst))
    assert dlg.terms.toPlainText().splitlines() == ["Pagina", "Orologeria", "Verdi"]
    dlg.run_search()
    assert len(dlg.matches) == 9 and dlg.ok.isEnabled()


def test_protect_warns_on_signatures(window, sec, sample_pdf, monkeypatch):
    window.open_path(sample_pdf)
    monkeypatch.setattr(C.SecurityController, "_has_signatures", staticmethod(lambda doc: True))
    asked = []
    monkeypatch.setattr(C, "confirm", lambda parent, title, text, action: asked.append(text) or False)
    assert not sec.protect()
    assert "firme digitali" in asked[0]


def test_area_tool_resize_corner(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(100, 300, 200, 400), R.MarkStyle())
    tool = sec.area_tool()
    view.set_tool(tool)
    tool.press(view, _ev(0, 200, 400))           # angolo in basso a destra
    tool._end = pymupdf.Point(250, 450)
    tool.release(view, _ev(0, 250, 450))
    with view.document.lock:
        r = R.marks(view.document.pdf)[0].rect
    assert abs(r.x0 - 100) < 0.5 and abs(r.x1 - 250) < 0.5 and abs(r.y1 - 450) < 0.5


def test_export_import_marks(window, sec, sample_pdf, tmp_path):
    view = window.open_path(sample_pdf)
    sec.show_panel()
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    out = tmp_path / "segni.xfdf"
    assert sec.export_marks(str(out))
    with view.document.change("Pulisci") as pdf:
        R.clear_marks(pdf)
    assert sec.import_marks(str(out)) == 1
    assert sec.panel.count.text() == "1"


def test_trusted_folder(window, sec, active_pdf, tmp_path, monkeypatch):
    import shutil
    view = window.open_path(active_pdf)
    actions = dict(sec.banners[view].current().actions)
    monkeypatch.setattr(C, "ask_trust_scope", lambda parent: "folder")
    actions["Considera attendibile…"]()
    import tempfile
    from pathlib import Path
    other = Path(tempfile.mkdtemp(prefix="foliant-altra-"))
    copy = shutil.copy(active_pdf, other / "copia.pdf")
    window.close_view(view)
    view = window.open_path(active_pdf)
    assert sec.banners[view].current() is None
    view2 = window.open_path(str(copy))
    assert sec.banners[view2].current().key == "active"
    sec.revoke_trust()


def test_protect_undo_save_unencrypted(window, sec, sample_pdf, tmp_path, monkeypatch):
    view = window.open_path(sample_pdf)
    monkeypatch.setattr(C, "ask_protection",
                        lambda parent, doc, **k: _FakeDialog(P.ProtectionSettings(open_password="Apri-123!")))
    assert sec.protect()
    view.document.undo()
    out = tmp_path / "annullato.pdf"
    view.document.save(out)
    pdf = pymupdf.open(out)
    assert not pdf.needs_pass and not pdf.is_encrypted
    view.document.redo()
    out2 = tmp_path / "ripetuto.pdf"
    view.document.save(out2)
    assert pymupdf.open(out2).needs_pass


def test_bug04_rc4_40_label(window, sec, tmp_path):
    path = tmp_path / "rc440.pdf"
    src = pymupdf.open()
    src.new_page()
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_RC4_40, user_pw="", owner_pw="o", permissions=-1)
    view = window.open_path(str(path))
    heads = [h for h, _l in C.security_rows(view.document)]
    assert any("RC4, chiave a 40 bit" in h for h in heads)
    assert not any("128" in h for h in heads)


def test_bug10_uri_details_decoded(tmp_path):
    from foliant.modules.security import sanitize as S
    path = tmp_path / "uri.pdf"
    src = pymupdf.open()
    page = src.new_page()
    uris = ["https://esempio.it/a", "https://аpple.com/login", "javascript:alert(1)", "mailto:a@b.it", "http://x.it"]
    for n, u in enumerate(uris):
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10 + 20 * n, 100, 25 + 20 * n), "uri": u})
    src.save(str(path))
    rep = S.scan(pymupdf.open(path))
    assert sorted(rep.external_links) == sorted(uris)
    rows = dict(C.active_rows(rep))
    assert len(rows["Collegamenti esterni (5)"]) == 5


def test_bug09_gotor_refused_is_handled(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    opened = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(window, "open_path", lambda *a, **k: opened.append(a))
    assert L.handler(view, {"kind": pymupdf.LINK_GOTOR, "file": "altro.pdf"}) is True
    assert L.handler(view, {"kind": pymupdf.LINK_GOTOR, "file": "note.txt"}) is True
    assert opened == []
    from foliant.ui import tools
    prio = max(p for p, _s, h in tools.LINK_HANDLERS if h is L.handler)
    assert prio >= max([p for p, _s, h in tools.LINK_HANDLERS if h is not L.handler] or [prio])


def test_bug03_unlock_notifies_and_grants_engine_permissions(window, sec, tmp_path, monkeypatch):
    view = window.open_path(_owner_only(tmp_path))
    doc = view.document
    assert doc.access == "user" and not doc.allows(pymupdf.PDF_PERM_COPY)
    seen = []
    doc.add_listener(lambda d, kind: seen.append(kind))
    monkeypatch.setattr(C, "ask_password", lambda *a: "capo")
    assert sec.unlock(doc)
    assert "access" in seen and doc.access == "owner" and doc.allows(pymupdf.PDF_PERM_COPY)
    assert sec.lock_button.text() == "Accesso completo"


def test_cyclic_page_tree_open_and_save(window, sec, cyclic_pdf, tmp_path, monkeypatch):
    import sys
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    view = window.open_path(cyclic_pdf)
    if view is None:
        pytest.skip("il nucleo non apre questo file")
    banner = sec.banners[view]
    keys = [n.key for n in banner.notices]
    assert "active" in keys and "restricted" in keys
    active = next(n for n in banner.notices if n.key == "active")
    assert "script (1)" in active.text and "avvio di programmi (1)" in active.text
    with view.document.lock:
        assert R.marks(view.document.pdf) == []
    with view.document.change("Commento") as pdf:
        pdf[0].add_text_annot((100, 100), "nota")
    view.document.save(tmp_path / "ciclo-salvato.pdf")
    assert not errors


def test_many_annotations_scan_is_fast(tmp_path):
    import time
    from foliant.modules.security import sanitize as S
    path = tmp_path / "molte.pdf"
    src = pymupdf.open()
    page = src.new_page()
    for n in range(2000):
        page.add_text_annot((10 + n % 50 * 10, 10 + n // 50 * 10), f"n{n}")
    src.save(str(path))
    pdf = pymupdf.open(path)
    t = time.perf_counter()
    rep = S.scan(pdf)
    assert time.perf_counter() - t < 1.5
    assert rep.comments == 2000
    t = time.perf_counter()
    assert R.marks(pdf) == [] and not R.has_marks(pdf)
    assert time.perf_counter() - t < 0.5


def test_protect_dialog_not_truncated(qtbot):
    dlg = D.ProtectDialog(P.ProtectionSettings(), already_protected=False, has_open_password=False)
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.adjustSize()
    assert dlg.width() >= dlg.minimumSizeHint().width()
    cb = dlg.perm_boxes["assemble"]
    assert cb.width() >= cb.sizeHint().width()


def test_open_many_annotations_fast(window, sec, tmp_path):
    import time
    from tests.modules.security.conftest import build_many_annots
    view = window.open_path(build_many_annots(tmp_path / "annot.pdf"))
    assert sec.scans[view.document].comments == 2000
    t = time.perf_counter()
    sec.on_document_opened(view)          # quota del modulo nell'apertura
    assert time.perf_counter() - t < 0.5


def test_scan_many_fields_fast(tmp_path):
    import time
    from foliant.modules.security import sanitize as S
    from tests.modules.security.conftest import build_many_fields
    pdf = pymupdf.open(build_many_fields(tmp_path / "campi.pdf"))
    t = time.perf_counter()
    rep = S.scan(pdf)
    assert time.perf_counter() - t < perf_limit(0.5) and rep.form_fields == 5000
    t = time.perf_counter()
    assert R.marks(pdf) == [] and not R.has_marks(pdf)
    assert time.perf_counter() - t < perf_limit(0.3)


def test_listener_cost_on_large_document(window, sec, tmp_path):
    """Una modifica di un altro modulo su 500 pagine × 10 annotazioni: < 50 ms nei listener."""
    import time
    path = tmp_path / "grande.pdf"
    src = pymupdf.open()
    for i in range(500):
        page = src.new_page()
        for k in range(10):
            page.add_text_annot((20 + k * 30, 50), f"n{i}-{k}")
    src.save(str(path))
    view = window.open_path(str(path))
    doc = view.document
    assert not sec.has_marks(doc)
    listener = sec._listeners[view]
    panel_listener = sec.panel._on_doc
    doc.remove_listener(listener)          # si misura la prima chiamata dopo la modifica
    doc.remove_listener(panel_listener)
    with doc.change("Modifica nota", kind="annotation") as pdf:
        page = pdf[7]
        annot = page.load_annot(page.annot_xrefs()[0][0])
        annot.set_info(content="modificata")
        annot.update()
    t = time.perf_counter()
    listener(doc, "content")
    panel_listener(doc, "content")
    elapsed = time.perf_counter() - t
    doc.add_listener(listener)
    doc.add_listener(panel_listener)
    assert elapsed < 0.05, elapsed
    # i segni nuovi si vedono subito (cache invalidata dalle modifiche del modulo)
    sec.add_area_mark(3, pymupdf.Rect(100, 100, 200, 200))
    assert sec.has_marks(doc) and list(sec.marked_pages(doc)) == [3]
    doc.undo()
    assert not sec.has_marks(doc)
    doc.redo()
    assert sec.has_marks(doc)


# ------------------------------------------------- SEC-14 «Segna testo»


def _two_line_pdf(tmp_path):
    path = tmp_path / "righe.pdf"
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Mario Rossi,", fontsize=12)
    page.insert_text((72, 120), "nato a Roma", fontsize=12)
    src.save(str(path))
    return str(path)


def _char_center(doc, page, word, k=0):
    with doc.lock:
        hits = doc.pdf[page].search_for(word)
    r = hits[0]
    return pymupdf.Point(r.x0 + 1 + k, (r.y0 + r.y1) / 2), r


def test_text_tool_two_lines(window, sec, tmp_path, monkeypatch, accept_all):
    view = window.open_path(_two_line_pdf(tmp_path))
    doc = view.document
    start, _r = _char_center(doc, 0, "Rossi")
    _p, nato = _char_center(doc, 0, "nato")
    end = pymupdf.Point(nato.x1 - 1, (nato.y0 + nato.y1) / 2)
    sec.activate_text_tool()
    tool = view.tool
    assert tool.id == "security-redact-text"
    tool.press(view, _ev(0, start.x, start.y))
    tool._end = (0, end)
    tool.release(view, _ev(0, end.x, end.y))
    assert sec.has_marks(doc)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    text = doc.page_text(0)
    assert "Mario" in text and "a Roma" in text and "Rossi" not in text and "nato" not in text


def test_text_tool_partial_word(window, sec, tmp_path, monkeypatch, accept_all):
    view = window.open_path(_two_line_pdf(tmp_path))
    doc = view.document
    with doc.lock:
        chars = [c for b in doc.pdf[0].get_text("rawdict")["blocks"] for line in b["lines"]
                 for sp in line["spans"] for c in sp["chars"]]
    boxes = {i: pymupdf.Rect(c["bbox"]) for i, c in enumerate(chars)}
    word = "".join(c["c"] for c in chars)
    i = word.index("Rossi")
    a = boxes[i].tl + (boxes[i].br - boxes[i].tl) * 0.5          # «R»
    b = boxes[i + 2].tl + (boxes[i + 2].br - boxes[i + 2].tl) * 0.5   # prima «s»
    assert sec.mark_text_range(doc, (0, a), (0, b)) == 1
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    text = doc.page_text(0)
    assert "Ros" not in text and "si," in text and "Mario" in text


def test_text_tool_across_pages_single_group(window, sec, tmp_path):
    from tests.conftest import make_pdf
    view = window.open_path(make_pdf(tmp_path / "cinque.pdf", pages=5))
    doc = view.document
    a, _r = _char_center(doc, 2, "Orologeria")
    _b, r4 = _char_center(doc, 3, "Pagina 4")
    n = sec.mark_text_range(doc, (2, a), (3, pymupdf.Point(r4.x1 - 1, (r4.y0 + r4.y1) / 2)))
    assert n >= 2
    sec.show_panel()
    sec.panel.refresh()
    groups = sec.panel.groups
    assert len(groups) == 1
    item = next(iter(groups.values()))
    assert "3–4" in item.text(0)
    pages = {m.page for m in sec.panel.marks if m.group}
    assert pages == {2, 3}
    # eliminabile in un solo passo
    sec.panel.tree.clearSelection()
    item.setSelected(True)
    sec.panel._remove()
    assert not sec.has_marks(doc)


def test_convert_highlights_to_marks(window, sec, sample_pdf, monkeypatch, accept_all):
    view = window.open_path(sample_pdf)
    doc = view.document
    with doc.change("Evidenzia", kind="annotation") as pdf:
        page = pdf[0]
        page.add_highlight_annot(page.search_for("Orologeria")[0])
    assert sec.convert_highlights(doc) == 1
    with doc.lock:
        assert [a[1] for a in doc.pdf[0].annot_xrefs()] == [pymupdf.PDF_ANNOT_REDACT]
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    assert "Orologeria" not in doc.page_text(0) and "Pagina 1" in doc.page_text(0)


def test_restyle_selected_marks(window, sec, sample_pdf, monkeypatch, accept_all):
    view = window.open_path(sample_pdf)
    doc = view.document
    with doc.change("Segna") as pdf:
        for page in range(3):
            R.add_mark(pdf, page, pymupdf.Rect(300, 400, 400, 500), R.MarkStyle())
    sec.show_panel()
    sec.panel.refresh()
    first = [m for m in sec.panel.marks if m.page == 0]
    style = R.MarkStyle(fill=(0x1F / 255, 0x3A / 255, 0x93 / 255), overlay_text="OMISSIS")
    assert sec.restyle_marks(first, style) == 1
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    with doc.lock:
        r, g, b = doc.pdf[0].get_pixmap().pixel(350, 450)
        other = doc.pdf[1].get_pixmap().pixel(350, 450)
        text = doc.pdf[0].get_textbox(pymupdf.Rect(300, 400, 400, 500))
    assert abs(r - 0x1F) <= 2 and abs(g - 0x3A) <= 2 and abs(b - 0x93) <= 2
    assert other == (0, 0, 0) and "OMISSIS" in text


def test_print_guard_registered(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    with view.document.change("Segna") as pdf:
        R.add_mark(pdf, 0, pymupdf.Rect(60, 75, 300, 110), R.MarkStyle())
    monkeypatch.setattr(C, "ask_unapplied", lambda parent, n: "cancel")
    assert window.ctx.run_guards("print", view, {}) is False
    monkeypatch.setattr(C, "ask_unapplied", lambda parent, n: "keep")
    assert window.ctx.run_guards("print", view, {}) is True


def test_restricted_disables_modifying_commands(window, sec, tmp_path, monkeypatch):
    path = tmp_path / "solo-stampa.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Listino riservato")
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = window.open_path(str(path))
    window.ctx.window._update_state()
    for key in ("area", "find", "patterns", "sanitize", "flatten", "apply"):
        assert not sec.actions[key].isEnabled(), key
    assert "Sblocca" in sec.actions["area"].toolTip()
    monkeypatch.setattr(C, "ask_password", lambda *a: "capo")
    assert sec.unlock(view.document)
    for key in ("area", "find", "patterns", "sanitize", "flatten"):
        assert sec.actions[key].isEnabled(), key
    assert "Sblocca" not in sec.actions["area"].toolTip()


class _FakeSettings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


def test_saved_patterns_roundtrip(window, sec, tmp_path, qtbot, monkeypatch):
    """Dato uno schema per le matricole salvato con un nome, quando riapro la ricerca
    allora lo ritrovo, posso esportarlo e importarlo in un'altra installazione."""
    from foliant.modules.security import patterns as PT
    path = tmp_path / "matricole.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Cassa n. SN-123456, scatola SN-654321", fontsize=11)
    d.save(path)
    store = _FakeSettings()
    monkeypatch.setattr(type(sec), "settings", lambda self: store)
    view = window.open_path(str(path))
    saved = PT.SavedPatterns(store)
    dlg = D.FindDialog("patterns", lambda o: sec.search(view.document, o), [("email", "Email", False)],
                       saved=saved)
    qtbot.addWidget(dlg)
    dlg.custom.setText("")
    assert not dlg.save_pattern("Vuoto")
    dlg.custom.setText(r"SN-\d{6}")
    assert dlg.save_pattern("Matricola")
    assert dlg.pattern_boxes["user:Matricola"].isChecked()
    dlg.run_search()
    assert [m.text for m in dlg.matches] == ["SN-123456", "SN-654321"]
    # nuova apertura: lo schema è ancora disponibile
    dlg2 = D.FindDialog("patterns", lambda o: sec.search(view.document, o), [], saved=PT.SavedPatterns(store))
    qtbot.addWidget(dlg2)
    assert "user:Matricola" in dlg2.pattern_boxes
    out = tmp_path / "schemi.json"
    saved.export(str(out))
    other = PT.SavedPatterns(_FakeSettings())
    dlg3 = D.FindDialog("patterns", lambda o: o, [], saved=other)
    qtbot.addWidget(dlg3)
    dlg3.import_patterns(str(out))
    assert other.items == {"Matricola": r"SN-\d{6}"} and "1" in dlg3.notice.text()
    bad = tmp_path / "bad.json"
    bad.write_text('{"patterns": {"Rotto": "(abc", "Ok": "X\\\\d+"}}', encoding="utf-8")
    done, errors = other.import_file(str(bad))
    assert done == ["Ok"] and errors and errors[0].startswith("Rotto")


def test_metadata_custom_properties_dialog(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    errors = []

    def fake_run(dlg):
        dlg.add_property("Numero pratica", "x")
        dlg.accept()                       # nome non valido: il dialogo resta aperto
        errors.append(dlg.error.text())
        dlg.remove_selected()
        dlg.props.clear()
        dlg.add_property("Pratica", "2026/118")
        dlg.add_property("", "")           # riga vuota ignorata
        return True

    monkeypatch.setattr(C, "run_dialog", fake_run)
    assert sec.edit_metadata()
    assert errors and "Numero pratica" in errors[0]
    from foliant.modules.security import sanitize as S
    with view.document.lock:
        assert S.read_custom(view.document.pdf) == {"Pratica": "2026/118"}
    seen = []
    monkeypatch.setattr(C, "run_dialog", lambda dlg: seen.append(dlg.custom()) or False)
    sec.edit_metadata()
    assert seen == [{"Pratica": "2026/118"}]
    view.document.undo()
    with view.document.lock:
        assert S.read_custom(view.document.pdf) == {}
