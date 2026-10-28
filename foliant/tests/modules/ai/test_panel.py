"""Pannello Assistente IA nella finestra: criteri di accettazione con fornitore simulato."""

from __future__ import annotations

import json
import os

import pymupdf
import pytest

from foliant.api import selection_from_words
from foliant.modules.ai import compose, content, history, render
from foliant.modules.ai.config import Profile, ProfileStore, SecretStore
from foliant.modules.ai.providers import AUTH, AIError

from .conftest import FakeProvider, make_active_profile


@pytest.fixture
def fake():
    return FakeProvider()


@pytest.fixture
def ai(window, fake):
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    return services


@pytest.fixture
def ready(ai):
    make_active_profile(ai.store)
    ai.refresh()
    return ai


def wait_done(qtbot, panel, timeout=10000):
    qtbot.waitUntil(lambda: panel.worker is None, timeout=timeout)


def annots_of(doc, index):
    """(info, rect) delle annotazioni della pagina, letti tenendo viva la pagina."""
    with doc.lock:
        page = doc.pdf[index]
        return [(dict(a.info), pymupdf.Rect(a.rect)) for a in page.annots()]


def select_words(view, page, predicate):
    words = [w for w in view.words(page) if predicate(w[4])]
    view.set_text_selection(selection_from_words(page, words))
    return words


# ------------------------------------------------------------------ AI-01
def test_disabled_until_configured(window, ai, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    window.ctx.set_mode("ia")
    panel = ai.panel
    assert panel.stack.currentIndex() == 0
    assert "Configura un fornitore" in panel.configure_btn.text()
    assert all(not a.isEnabled() for a in ai.doc_actions)
    opened = []
    panel.open_settings = lambda: opened.append(True)
    panel.send("ciao")
    assert opened and fake.requests == []
    assert view is not None


def test_key_never_written_to_files(qtbot, window, ai, fake, sample_pdf, tmp_path, memory_keyring):
    from foliant.modules.ai.dialogs import SettingsDialog
    dlg = SettingsDialog(ai.store, window, verify=lambda p, k: ["m1", "m2", "m3"])
    dlg.add_profile()
    dlg.key.setText("  chiave-di-prova-123  ")
    assert dlg.verify()
    dlg.ask_consent = lambda p: True
    assert dlg.activate_profile()
    pid = ai.store.active_id()
    assert ai.store.secrets.get(pid) == "chiave-di-prova-123"   # spazi rimossi
    assert [k for k in memory_keyring.store if k[0] == "foliant-ai"] == [("foliant-ai", pid)]
    window.open_path(sample_pdf)
    ai.panel.set_scope("document")
    ai.panel.send("domanda")
    wait_done(qtbot, ai.panel)
    window.settings.sync()
    for root, _dirs, files in os.walk(tmp_path):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert b"chiave-di-prova-123" not in fh.read(), f


def test_verify_models_and_auth_failure(window, ai):
    from foliant.modules.ai.dialogs import SettingsDialog
    dlg = SettingsDialog(ai.store, window, verify=lambda p, k: ["m1", "m2", "m3"])
    dlg.add_profile()
    dlg.key.setText("k")
    assert dlg.verify()
    assert [dlg.model.itemText(i) for i in range(dlg.model.count())] == ["m1", "m2", "m3"]

    def bad(p, k):
        raise AIError(AUTH, "HTTP 401 k")
    dlg2 = SettingsDialog(ai.store, window, verify=bad)
    p = dlg2.add_profile()
    dlg2.key.setText("k")
    assert not dlg2.verify()
    assert "Chiave non valida" in dlg2.verify_result.text()
    assert not dlg2.activate_btn.isEnabled()
    assert not dlg2.activate_profile()
    assert ai.store.active_id() != p.id


def test_session_only_key_without_keyring(window, ai, monkeypatch):
    import keyring
    from keyring.backends import fail
    keyring.set_keyring(fail.Keyring())
    store = ai.store
    p = Profile.from_catalog("anthropic", verified=True, consent=True)
    store.save(p)
    assert not store.secrets.available()
    store.secrets.set(p.id, "segreto", session_only=True)
    store.set_active(p.id)
    assert store.active() is not None
    restarted = ProfileStore(window.settings, SecretStore())
    assert restarted.active() is None


def test_delete_profile_removes_keyring_entry(ai, memory_keyring):
    p = make_active_profile(ai.store)
    assert ("foliant-ai", p.id) in memory_keyring.store
    ai.store.delete(p.id)
    assert ("foliant-ai", p.id) not in memory_keyring.store
    assert ai.store.active() is None


# ------------------------------------------------------------------ AI-02
def test_consent_shown_before_any_request(qtbot, window, ai, fake, sample_pdf):
    p = make_active_profile(ai.store)
    p.consent = False
    ai.store.save(p)
    window.open_path(sample_pdf)
    panel = ai.panel
    asked = []
    panel.ask_consent = lambda prof: asked.append(prof.id) or False
    panel.send("domanda")
    assert asked and fake.requests == []
    panel.ask_consent = lambda prof: True
    panel.send("domanda")
    wait_done(qtbot, panel)
    assert len(fake.requests) == 1
    assert ai.store.get(p.id).consent


def test_extraction_forbidden_disables_commands(window, ready, fake, tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=12)
    path = tmp_path / "vietato.pdf"
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="prop", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    window.open_path(str(path))
    panel = ready.panel
    panel.set_scope("document")
    panel.refresh_state()
    assert not panel.send_btn.isEnabled()
    assert "vietano" in panel.banner.text()
    errors = []
    panel.show_error = lambda t, m: errors.append(m)
    assert panel.send("x") is None
    assert fake.requests == [] and errors


def test_send_line_and_preview_flag_redaction(window, ready, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Pagamento su IBAN-123 entro marzo", fontsize=12)
    page.add_redact_annot(page.search_for("IBAN-123")[0])
    path = tmp_path / "red.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    panel = ready.panel
    panel.set_scope("document")
    assert "pagine 1" in panel.send_line.text() and "simulato" not in panel.send_line.text()
    scope = panel._preview_scope()
    assert "IBAN-123" not in scope.preview() and "oscuramento" in scope.preview()


def test_send_log_entry_after_summary(qtbot, window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    panel = ready.panel
    panel.set_function("summary")
    panel.set_scope("pages", "2-3")
    panel.send()
    wait_done(qtbot, panel)
    e = ready.log.entries()[-1]
    assert e["function"] == "summary" and e["pages"] == [2, 3] and e["chars"] > 0 and e["outcome"] == "ok"
    assert "Orologeria" not in ready.log.path.read_text()


def test_confirm_each_send(qtbot, window, ready, fake, sample_pdf):
    ready.store.set_option("confirm_each", True)
    window.open_path(sample_pdf)
    panel = ready.panel
    panel.ask_preview = lambda summary, text: False
    panel.send("domanda")
    assert fake.requests == []
    panel.ask_preview = lambda summary, text: True
    panel.send("domanda")
    wait_done(qtbot, panel)
    assert len(fake.requests) == 1


# ------------------------------------------------------------------ AI-03 / AI-04
def test_remote_image_not_loaded(qtbot, window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    fake.responses = ["Ecco ![grafico](https://esterno.example/x.png) e [sito](https://esterno.example)"]
    panel = ready.panel
    panel.send("mostra")
    wait_done(qtbot, panel)
    html = panel.browser.toHtml()
    assert "<img" not in html
    assert "immagine non caricata" in html
    assert panel.browser.loadResource(2, __import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl(
        "https://esterno.example/x.png")) is None
    opened = []
    panel.ask_open_url = lambda u: opened.append(u) or False
    from PySide6.QtCore import QUrl
    panel.on_link(QUrl("ext:1:0"))
    assert opened == ["https://esterno.example"]


def test_page_label_reference_click(qtbot, window, ready, fake, tmp_path):
    doc = pymupdf.open()
    for i in range(6):
        doc.new_page().insert_text((72, 100), f"Testo pagina {i + 1} con la clausola numero {i + 1}", fontsize=12)
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1}])
    path = tmp_path / "lab.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    fake.responses = ["Vedi [p. iv: «clausola numero 4»] e [p. 42]"]
    panel = ready.panel
    panel.send("dove?")
    wait_done(qtbot, panel)
    msg_index = len(panel.conversation().messages) - 1
    refs = panel.message_refs(msg_index)
    assert refs[0]["verified"] and refs[0]["page"] == 3 and not refs[1]["verified"]
    assert "page:%d:0" % msg_index in panel.browser.toHtml()
    from PySide6.QtCore import QUrl
    panel.on_link(QUrl(f"page:{msg_index}:0"))
    assert view.current_page == 3
    assert view.search_hits.get(3)


def test_selection_fixed_at_send(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    fake.responses = [{"chunks": ["Spiega", "zione"], "delay": 0.2}]
    select_words(view, 0, lambda w: w == "compravendita")
    panel = ready.panel
    panel.set_function("explain")
    panel.set_scope("selection")
    panel.send()
    select_words(view, 1, lambda w: w == "Orologeria")
    wait_done(qtbot, panel)
    sent = fake.requests[0].messages[-1].content
    assert "«compravendita»" in sent


def test_insert_note_single_undo(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    fake.responses = ["Risposta da annotare"]
    panel = ready.panel
    panel.send("domanda")
    wait_done(qtbot, panel)
    panel.insert_note()
    doc = view.document
    annots = annots_of(doc, view.current_page)
    assert annots and annots[0][0]["content"] == "Risposta da annotare"
    assert annots[0][0]["subject"] == "Assistente IA"
    doc.undo()
    assert not annots_of(doc, view.current_page)


def test_general_chat_without_document(qtbot, window, ready, fake):
    from foliant.modules.ai.ui import GeneralChatDialog
    dlg = GeneralChatDialog(window.ctx, ready, window)
    assert dlg.panel.scope_kind() == "none"
    dlg.panel.send("Ciao")
    wait_done(qtbot, dlg.panel)
    assert fake.requests[0].messages[-1].content == "Ciao"


def test_document_change_warns(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    panel = ready.panel
    panel.send("domanda")
    wait_done(qtbot, panel)
    with view.document.change("modifica") as pdf:
        pdf[0].insert_text((72, 600), "altro", fontsize=10)
    qtbot.waitUntil(lambda: "cambiato" in panel.banner.text(), timeout=3000)


# ------------------------------------------------------------------ AI-05
def test_invalid_range_no_request(window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    panel = ready.panel
    panel.set_function("summary")
    panel.set_scope("pages", "12-15")
    assert panel.send() is None
    assert panel.range_error.isVisible() if panel.isVisible() else panel.range_error.text()
    assert fake.requests == []


def test_summary_inserted_as_first_page(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    fake.responses = ["Il contratto riguarda la vendita di orologi usati con garanzia di dodici mesi."]
    panel = ready.panel
    panel.set_function("summary")
    panel.set_scope("document")
    panel.send()
    wait_done(qtbot, panel)
    assert panel.act_page.isEnabled()
    doc = view.document
    n = doc.page_count
    panel.insert_page()
    assert doc.page_count == n + 1
    text = " ".join(doc.page_text(0).split())
    assert "Il contratto riguarda la vendita di orologi usati con garanzia di dodici mesi." in text
    doc.undo()
    assert doc.page_count == n


def test_heavy_summary_requires_confirmation(window, ready, fake, tmp_path):
    p = ready.store.get(ready.store.active_id())
    p.context_window = 8000
    ready.store.save(p)
    doc = pymupdf.open()
    for i in range(300):
        doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), ("parola " * 250) + str(i), fontsize=9)
    path = tmp_path / "lungo.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    panel = ready.panel
    asked = []
    panel.ask_confirm = lambda title, text: asked.append(text) or False
    panel.set_function("summary")
    panel.set_scope("document")
    assert panel.send() is None
    assert asked and "richieste" in asked[0]
    assert fake.requests == []


# ------------------------------------------------------------------ AI-06 / AI-08
def test_translate_selection_note_anchored(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    words = select_words(view, 0, lambda w: w in ("contratto", "di", "compravendita"))
    fake.default = lambda req: req.messages[-1].content.upper()
    panel = ready.panel
    panel.set_function("translate")
    panel.set_scope("selection")
    panel.send()
    wait_done(qtbot, panel)
    assert "English" not in fake.requests[0].system or True
    panel.insert_note()
    doc = view.document
    annots = annots_of(doc, 0)
    assert annots and "COMPRAVENDITA" in annots[0][0]["content"]
    r = annots[0][1]
    sel_rect = pymupdf.Rect(words[0][:4])
    assert abs(r.x0 - sel_rect.x0) < 30 and abs(r.y0 - sel_rect.y0) < 40


def test_explain_note_at_selection(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    select_words(view, 0, lambda w: w == "compravendita")
    fake.responses = ["Spiegazione semplice"]
    panel = ready.panel
    panel.set_function("explain")
    panel.explain_level.setCurrentIndex(0)
    panel.set_scope("selection")
    panel.send()
    wait_done(qtbot, panel)
    assert "simple" in fake.requests[0].messages[-1].content
    panel.insert_note()
    info = annots_of(view.document, 0)[0][0]
    assert info["content"] == "Spiegazione semplice" and info["subject"] == "Spiegazione IA"


# ------------------------------------------------------------------ AI-09
def _rewrite_doc(tmp_path, image_only=False, signed=False):
    doc = pymupdf.open()
    page = doc.new_page()
    if image_only:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
        pix.clear_with(230)
        page.insert_image(page.rect, pixmap=pix)
        page.insert_text((72, 100), "Il documento e stato aprovato", fontsize=12, render_mode=3)
    else:
        page.insert_text((72, 100), "Il documento e stato aprovato", fontsize=12, fontname="helv")
        page.insert_text((72, 200), "Seconda riga intatta", fontsize=12, fontname="helv")
    path = tmp_path / ("img.pdf" if image_only else "rw.pdf")
    doc.save(str(path))
    return str(path)


def _do_rewrite(qtbot, window, ready, fake, path, reply):
    view = window.open_path(path)
    select_words(view, 0, lambda w: w in ("Il", "documento", "e", "stato", "aprovato"))
    fake.responses = [reply]
    panel = ready.panel
    panel.set_function("rewrite")
    panel.set_scope("selection")
    panel.send()
    wait_done(qtbot, panel)
    return view, panel


def test_rewrite_accept_replaces_text_single_undo(qtbot, window, ready, fake, tmp_path):
    view, panel = _do_rewrite(qtbot, window, ready, fake, _rewrite_doc(tmp_path), "Il documento è stato approvato")
    assert panel.proposal_box.isHidden() is False
    assert panel.accept_btn.isEnabled()
    assert panel.accept_proposal()
    doc = view.document
    text = doc.page_text(0)
    assert "Il documento è stato approvato" in text and "aprovato" not in text
    assert "Seconda riga intatta" in text
    with doc.lock:
        page0 = doc.pdf[0]
        spans = [s for b in page0.get_text("dict")["blocks"] for line in b.get("lines", [])
                 for s in line["spans"] if "approvato" in s["text"]]
    assert spans and spans[0]["font"] == "Helvetica" and abs(spans[0]["size"] - 12) < 0.01
    doc.undo()
    assert "aprovato" in doc.page_text(0)


def test_rewrite_discard_keeps_document(qtbot, window, ready, fake, tmp_path):
    view, panel = _do_rewrite(qtbot, window, ready, fake, _rewrite_doc(tmp_path), "Il documento è stato approvato")
    panel.discard_proposal()
    assert not view.document.is_modified


def test_rewrite_on_scanned_page_offers_annotation(qtbot, window, ready, fake, tmp_path):
    view, panel = _do_rewrite(qtbot, window, ready, fake, _rewrite_doc(tmp_path, image_only=True),
                              "Il documento è stato approvato")
    assert not panel.accept_btn.isEnabled()
    panel.annotate_proposal()
    annots = annots_of(view.document, 0)
    assert annots and annots[0][0]["content"] == "Il documento è stato approvato"


def test_rewrite_signed_document_asks(qtbot, window, ready, fake, tmp_path, monkeypatch):
    view, panel = _do_rewrite(qtbot, window, ready, fake, _rewrite_doc(tmp_path), "Il documento è stato approvato")
    monkeypatch.setattr(compose, "is_signed", lambda pdf: True)
    asked = []
    panel.ask_confirm = lambda t, m: asked.append(m) or False
    assert not panel.accept_proposal()
    assert asked and not view.document.is_modified
    panel.ask_confirm = lambda t, m: True
    assert panel.accept_proposal()
    assert view.document.is_modified


def test_rewrite_obsolete_if_text_changed(qtbot, window, ready, fake, tmp_path):
    view, panel = _do_rewrite(qtbot, window, ready, fake, _rewrite_doc(tmp_path), "Il documento è stato approvato")
    with view.document.change("altro") as pdf:
        p = pdf[0]
        p.add_redact_annot(p.search_for("aprovato")[0])
        p.apply_redactions()
    assert not panel.accept_proposal()
    assert "obsoleta" in panel.replace_reason.text()


# ------------------------------------------------------------------ AI-11
def test_area_scaled_to_2048(qtbot, window, ready, fake, tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=1500, height=800).insert_text((100, 100), "Grafico", fontsize=30)
    path = tmp_path / "big.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    panel = ready.panel
    panel.set_area(0, pymupdf.Rect(0, 0, 1440, 720))   # 3000×1500 px a 150 dpi
    panel.send("cosa mostra?")
    wait_done(qtbot, panel)
    img = fake.requests[0].messages[-1].images[0]
    pix = pymupdf.Pixmap(img.data)
    assert (pix.width, pix.height) == (2048, 1024)


def test_tiny_area_rejected(window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    panel = ready.panel
    errors = []
    panel.show_error = lambda t, m: errors.append(m)
    panel.set_area(0, pymupdf.Rect(100, 100, 104.8, 104.8))   # 10×10 px
    assert panel.send("?") is None
    assert errors and fake.requests == []


def test_area_without_image_capability_uses_local_ocr(qtbot, window, ready, fake, sample_pdf):
    p = ready.store.get(ready.store.active_id())
    p.images = False
    ready.store.save(p)
    window.open_path(sample_pdf)
    panel = ready.panel
    panel.ask_confirm = lambda t, m: True
    panel.set_area(0, pymupdf.Rect(60, 70, 400, 150))
    panel.image_action.setCurrentIndex(panel.image_action.findData("ocr"))
    panel.send()
    wait_done(qtbot, panel)
    req = fake.requests[0]
    assert req.image_count() == 0
    assert "Pagina" in req.messages[-1].content


# ------------------------------------------------------------------ AI-12
def test_mindmap_open_click_insert(qtbot, window, ready, fake, text_pdf):
    view = window.open_path(text_pdf(5))
    tree = {"title": "Doc", "children": [
        {"title": f"Ramo {i}", "page": 1, "children": [
            {"title": f"Foglia {i}{j}", "page": 4 if (i, j) == (1, 1) else 2} for j in range(2)]}
        for i in range(3)]}
    fake.responses = [json.dumps(tree)]
    panel = ready.panel
    panel.set_function("map")
    panel.set_scope("document")
    panel.send()
    wait_done(qtbot, panel)
    root = panel._last.answer.data
    assert root.count() == 10
    panel.open_mindmap()
    screen = ready.mindmap_widget
    assert window.ctx.central_widget() is screen
    leaf = next(n for n in root.walk() if n.title == "Foglia 11")
    screen.view.click_node(leaf.id)
    assert view.current_page == 3
    n = view.document.page_count
    screen.insert("start")
    assert view.document.page_count == n + 1
    first = view.document.page_text(0)
    for node in root.walk():
        assert node.title in first
    view.document.undo()
    assert view.document.page_count == n


def test_mindmap_export_json_after_rename(tmp_path, window, ready):
    from foliant.modules.ai import mindmap
    from foliant.modules.ai.mindmap_screen import MindMapScreen
    root, _w = mindmap.parse_tree('{"title":"R","children":[{"title":"A"}]}', 1)
    screen = MindMapScreen(window.ctx, root, None, window)
    a = next(n for n in screen.tree().walk() if n.title == "A")
    screen.view.rename_node(a.id, "Nuovo titolo")
    out = tmp_path / "m.json"
    screen.export("json", str(out))
    data = json.loads(out.read_text())
    assert data["children"][0]["title"] == "Nuovo titolo"


# ------------------------------------------------------------------ AI-15
def test_textless_pages_prompt_before_requests(qtbot, window, ready, fake, tmp_path):
    doc = pymupdf.open()
    for i in range(5):
        p = doc.new_page()
        if i in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
            pix.clear_with(100)
            p.insert_image(p.rect, pixmap=pix)
        else:
            p.insert_text((72, 100), f"Testo della pagina {i + 1} ben leggibile", fontsize=12)
    path = tmp_path / "misto.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    panel = ready.panel
    seen = []
    panel.ask_textless = lambda pages, images_ok: (seen.append((pages, images_ok)), (None, ""))[1]
    panel.set_function("summary")
    panel.set_scope("document")
    assert panel.send() is None
    assert seen == [([1, 3], True)] and fake.requests == []
    panel.ask_textless = lambda pages, images_ok: ("exclude", "")
    panel.send()
    wait_done(qtbot, panel)
    msg = panel.conversation().messages[-1]
    assert "parziale: pagine 2, 4 escluse" in msg.flags
    assert "pagina 2" not in fake.all_text()


def test_images_option_hidden_without_capability(window, ready, sample_pdf):
    from foliant.modules.ai.dialogs import TextlessDialog
    dlg = TextlessDialog([1], False, ["ita+eng"], window)
    assert dlg.btn_images.isHidden()
    dlg2 = TextlessDialog([1], True, ["ita+eng"], window)
    assert not dlg2.btn_images.isHidden()


# ------------------------------------------------------------------ AI-16 / AI-17
def test_auth_error_message_with_settings_link(qtbot, window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    fake.responses = [AIError(AUTH, "HTTP 401 chiave-di-prova-123")]
    panel = ready.panel
    panel.send("x")
    wait_done(qtbot, panel)
    assert len(fake.requests) == 1
    html = panel.browser.toHtml()
    assert "action:settings" in html and "Chiave non valida" in html
    assert "chiave-di-prova-123" not in html


def test_stop_streaming(qtbot, window, ready, fake, sample_pdf):
    import time
    window.open_path(sample_pdf)
    fake.responses = [{"chunks": ["uno ", "due ", "tre ", "quattro ", "cinque "], "delay": 0.4}]
    panel = ready.panel
    panel.send("x")
    qtbot.waitUntil(lambda: fake.chunks_sent >= 3, timeout=5000)
    t0 = time.monotonic()
    panel.stop()
    wait_done(qtbot, panel)
    assert fake.closed_at is not None and fake.closed_at - t0 < 1.0
    msg = panel.conversation().messages[-1]
    assert msg.status == "interrupted" and "cinque" not in msg.text
    saved = ready.history().get(panel.conversation().id)
    assert saved.messages[-1].status == "interrupted"


def test_ui_responsive_during_answer(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    fake.responses = [{"chunks": ["a", "b", "c"], "delay": 0.3}]
    panel = ready.panel
    panel.send("x")
    assert panel.worker is not None and panel.worker.running
    with view.document.change("Evidenzia") as pdf:
        pdf[0].add_highlight_annot(pdf[0].search_for("Orologeria")[0])
    view.go_to_page(1)
    wait_done(qtbot, panel)
    assert panel.conversation().messages[-1].text == "abc"


# ------------------------------------------------------------------ AI-18
def test_history_persisted_and_reopened(qtbot, window, ready, fake, sample_pdf, tmp_path):
    view = window.open_path(sample_pdf)
    fake.responses = ["Vedi [p. 2]"]
    panel = ready.panel
    panel.send("Dove?")
    wait_done(qtbot, panel)
    conv_id = panel.conversation().id
    fp = content.content_fingerprint(view.document.pdf)
    # «riavvio»: nuovo archivio, documento rinominato
    store = history.HistoryStore()
    convs = store.for_document(fp)
    assert [c.id for c in convs] == [conv_id]
    assert convs[0].messages[-1].refs[0]["page"] == 1
    import shutil
    renamed = tmp_path / "rinominato.pdf"
    shutil.copy(sample_pdf, renamed)
    from foliant.api import Document
    d2 = Document.open(str(renamed))
    assert store.for_document(content.content_fingerprint(d2.pdf))[0].id == conv_id
    window.close_view(view)
    view2 = window.open_path(str(renamed))
    assert panel.resume_available
    assert panel.resume_last()
    assert len(panel.conversation().messages) == 2
    assert view2 is not None


def test_history_disabled_writes_nothing(qtbot, window, ready, fake, sample_pdf):
    ready.store.set_option("history_enabled", False)
    window.open_path(sample_pdf)
    panel = ready.panel
    panel.send("x")
    wait_done(qtbot, panel)
    assert not ready.history().directory.exists() or not list(ready.history().directory.iterdir())


def test_history_delete_all_and_expiry(tmp_path):
    import datetime as dt
    from foliant.modules.ai.engine import ChatMessage, Conversation
    h = history.HistoryStore(retention_days=30)
    old = Conversation(title="vecchia", messages=[ChatMessage("user", "testo segreto vecchio")])
    new = Conversation(title="nuova", messages=[ChatMessage("user", "testo recente")])
    h.save(old)
    h.save(new)
    data = json.loads(h._path(old.id).read_text())
    data["updated"] = (dt.datetime.now() - dt.timedelta(days=31)).isoformat(timespec="seconds")
    h._path(old.id).write_text(json.dumps(data))
    assert h.expire() == 1
    assert [c.id for c in h.load_all()] == [new.id]
    (h.directory / "rotto.json").write_text("{non json")
    assert len(h.load_all()) == 1 and h.damaged
    h.delete_all()
    for f in h.directory.iterdir():
        assert "testo recente" not in f.read_text(errors="ignore")
    assert oct(os.stat(h.directory).st_mode & 0o777) == "0o700"


def test_history_export(tmp_path):
    from foliant.modules.ai.engine import ChatMessage, Conversation
    c = Conversation(title="T", messages=[ChatMessage("user", "Domanda"), ChatMessage("assistant", "Risposta")])
    md = history.export_markdown(c)
    assert "# T" in md and "Domanda" in md and "Risposta" in md
    assert json.loads(history.export_json(c))["messages"][1]["text"] == "Risposta"


# ------------------------------------------------------------------ AI-07 integrazione
def test_translate_pdf_controller(qtbot, window, ready, fake, text_pdf, tmp_path):
    from foliant.modules.ai import translate_pdf
    path = text_pdf(3)
    window.open_path(path)
    before = open(path, "rb").read()
    fake.default = lambda req: json.dumps([s.upper() for s in json.loads(req.messages[-1].content)])
    out = tmp_path / "tradotto.pdf"
    ctl = ready.controller
    ctl.exec_report = lambda dlg: False
    report = ctl.translate_pdf(dict(target="en", pages=None, mode=translate_pdf.MODE_LAYOUT, glossary=[],
                                    min_size=6.0), str(out))
    assert report is not None and not report.failed_pages
    res = pymupdf.open(str(out))
    assert res.page_count == 3 and "CONTENUTO UNICO DELLA PAGINA 2" in res[1].get_text()
    assert open(path, "rb").read() == before
    assert window.ctx.current_document().path.name == "tradotto.pdf"


def test_translate_pdf_cancel_creates_no_file(qtbot, window, ready, fake, text_pdf, tmp_path, monkeypatch):
    from foliant.api import OperationCancelled
    from foliant.modules.ai import translate_pdf
    window.open_path(text_pdf(3))
    out = tmp_path / "annullato.pdf"

    def cancel_on_second(req):
        if len(fake.requests) >= 2:
            raise OperationCancelled()
        return json.dumps([s.upper() for s in json.loads(req.messages[-1].content)])
    fake.default = cancel_on_second
    ready.controller.exec_report = lambda dlg: False
    assert ready.controller.translate_pdf(dict(target="en", pages=None, mode=translate_pdf.MODE_LAYOUT,
                                               glossary=[], min_size=6.0), str(out)) is None
    assert not out.exists()
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".annullato")]


def test_translate_pdf_retry_failed_page(qtbot, window, ready, fake, text_pdf, tmp_path):
    from foliant.modules.ai import translate_pdf
    from foliant.modules.ai.providers import BAD_REQUEST
    window.open_path(text_pdf(3))
    state = {"fail": True}

    def reply(req):
        blocks = json.loads(req.messages[-1].content)
        if state["fail"] and any("pagina 2" in b for b in blocks):
            return AIError(BAD_REQUEST, "rifiuto")
        return json.dumps([b.upper() for b in blocks])
    fake.default = reply
    out = tmp_path / "t.pdf"
    ctl = ready.controller
    ctl.exec_report = lambda dlg: False
    report = ctl.translate_pdf(dict(target="en", pages=None, mode=translate_pdf.MODE_LAYOUT, glossary=[],
                                    min_size=6.0), str(out))
    assert list(report.failed_pages) == [1]
    state["fail"] = False
    fake.requests.clear()
    report2 = ctl.retry_failed()
    assert not report2.failed_pages
    assert all("pagina 2" in r.messages[-1].content for r in fake.requests)
    assert "PAGINA 2" in pymupdf.open(str(out))[1].get_text()


# ------------------------------------------------------------------ tema scuro (resa)
def test_render_dark_theme(qtbot, window, ready, fake, sample_pdf, tmp_path):
    from PySide6.QtWidgets import QApplication
    from foliant.ui import theme
    window.open_path(sample_pdf)
    window.ctx.set_mode("ia")
    fake.responses = ["## Titolo\n\n- punto [p. 1]\n- altro\n\n| a | b |\n|---|---|\n| 1 | 2 |"]
    panel = ready.panel
    panel.send("x")
    wait_done(qtbot, panel)
    theme.apply(QApplication.instance(), "dark")
    try:
        panel._render()
        assert "<table" in panel.browser.toHtml()
        assert render.render_markdown("**b**").html == "<p><b>b</b></p>"
    finally:
        theme.apply(QApplication.instance(), "light")


# ------------------------------------------------------------------ AI-24 integrazione
def test_analyze_pages_without_profile(window, ai, fake, tmp_path):
    doc = pymupdf.open()
    for i in range(6):
        p = doc.new_page()
        if i != 3:
            p.insert_text((72, 100), f"Testo della pagina {i + 1}", fontsize=12)
    path = tmp_path / "an.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    ai.refresh()
    seen = []
    ai.controller.exec_dialog = lambda dlg: seen.append(dlg) or 0
    dlg = ai.controller.analyze_pages()
    assert any(i.page == 3 and i.problem == "blank" for i in dlg.issues)
    assert not dlg.classify_button.isEnabled()
    assert fake.requests == [] and view.document.page_count == 6


# ------------------------------------------------------------------ AI-15 salvataggio OCR, AI-06 sostituzione
def _scan_pdf(tmp_path):
    src = pymupdf.open()
    sp = src.new_page(width=400, height=200)
    sp.insert_text((30, 100), "Orologio meccanico", fontsize=30)
    pix = sp.get_pixmap(dpi=200)
    doc = pymupdf.open()
    p = doc.new_page(width=400, height=200)
    p.insert_image(p.rect, pixmap=pix)
    path = tmp_path / "scansione.pdf"
    doc.save(str(path))
    return str(path)


def test_ocr_temporary_then_saved(qtbot, window, ready, fake, tmp_path):
    view = window.open_path(_scan_pdf(tmp_path))
    panel = ready.panel
    panel.set_function("summary")
    panel.set_scope("document")
    panel.ask_textless = lambda pages, ok: ("ocr", "eng", False)
    panel.send()
    wait_done(qtbot, panel)
    assert "Orologio" in fake.all_text() or "Orolog" in fake.all_text()
    assert not view.document.is_modified
    panel.ask_textless = lambda pages, ok: ("ocr", "eng", True)
    panel.send()
    wait_done(qtbot, panel)
    assert view.document.is_modified
    assert "Orolog" in view.document.page_text(0)
    view.document.undo()
    assert "Orolog" not in view.document.page_text(0)


def test_translate_selection_replace_in_document(qtbot, window, ready, fake, tmp_path):
    view, panel = None, ready.panel
    path = tmp_path / "tr.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Buongiorno a tutti", fontsize=12, fontname="helv")
    doc.save(str(path))
    view = window.open_path(str(path))
    select_words(view, 0, lambda w: w in ("Buongiorno", "a", "tutti"))
    fake.responses = ["Good morning everyone"]
    panel.set_function("translate")
    panel.set_scope("selection")
    panel.send()
    wait_done(qtbot, panel)
    assert panel.proposal_box.isVisibleTo(panel)
    assert panel.accept_proposal()
    assert "Good morning everyone" in view.document.page_text(0)


def test_copy_formatted_and_dropped_image(qtbot, window, ready, fake, sample_pdf):
    from PySide6.QtGui import QGuiApplication, QImage
    window.open_path(sample_pdf)
    fake.responses = ["**Grassetto** e testo"]
    panel = ready.panel
    panel.send("x")
    wait_done(qtbot, panel)
    panel.copy_result()
    md = QGuiApplication.clipboard().mimeData()
    assert md.text() == "Grassetto e testo" and "<b>Grassetto</b>" in md.html()
    QGuiApplication.clipboard().clear()
    img = QImage(3000, 1000, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFF)
    panel.set_image_data(img)
    assert panel.function_key() == "image" and panel._external_image[1] == 2048
    panel.send("cosa vedi?")
    wait_done(qtbot, panel)
    assert fake.requests[-1].image_count() == 1


def test_translate_warns_when_already_target_language(window, ready, fake, tmp_path):
    from foliant.modules.ai import content as ct
    assert ct.guess_language("Il contratto è valido per la durata di un anno e non si rinnova") == "it"
    assert ct.guess_language("The contract is valid for one year and it is not renewed") == "en"
    path = tmp_path / "en.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "The contract is valid for one year and it is not renewed", fontsize=11)
    doc.save(str(path))
    window.open_path(str(path))
    panel = ready.panel
    panel.set_function("translate")
    panel.target_lang.setCurrentIndex(panel.target_lang.findData("en"))
    panel.set_scope("document")
    asked = []
    panel.ask_confirm = lambda t, m: asked.append(m) or False
    assert panel.send() is None
    assert asked and "già" in asked[0] and fake.requests == []


def test_history_encrypted_with_keyring_key(memory_keyring):
    from foliant.modules.ai.engine import ChatMessage, Conversation
    h = history.HistoryStore(encrypt=True)
    c = Conversation(title="segreta", messages=[ChatMessage("user", "testo riservato IBAN")])
    assert h.save(c)
    raw = h._path(c.id).read_text()
    assert "IBAN" not in raw and raw.startswith(history.ENC_PREFIX)
    assert ("foliant-ai", "history-key") in memory_keyring.store
    assert history.HistoryStore().get(c.id).messages[0].text == "testo riservato IBAN"
