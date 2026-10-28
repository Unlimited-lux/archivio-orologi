"""Logica dell'assistente: ambito, chat, riassunti, traduzioni, errori (senza interfaccia)."""

from __future__ import annotations

import json
import threading

import pymupdf
import pytest

from foliant.api import Document, OperationCancelled
from foliant.modules.ai import chunking, content, engine
from foliant.modules.ai.engine import DOCUMENT, PAGES, SELECTION, Conversation, Scope, SummaryOptions
from foliant.modules.ai.providers import AUTH, LIMIT, RATE, SERVER, AIError, ChatResult


def open_pdf(path):
    return pymupdf.open(path)


# ------------------------------------------------------------------ ambito e sicurezza
def test_page_range_parsing():
    assert content.parse_page_range("1-3, 7", 10) == [0, 1, 2, 6]
    with pytest.raises(ValueError):
        content.parse_page_range("12-15", 10)
    with pytest.raises(ValueError):
        content.parse_page_range("a-b", 10)


def test_redaction_mark_excludes_text(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Pagamento su conto IBAN-123 entro marzo", fontsize=12)
    rects = page.search_for("IBAN-123")
    page.add_redact_annot(rects[0])
    pdf_path = tmp_path / "r.pdf"
    doc.save(str(pdf_path))
    pdf = pymupdf.open(str(pdf_path))
    scope = engine.build_scope(pdf, DOCUMENT)
    assert "IBAN-123" not in scope.pages[0].text
    assert "Pagamento" in scope.pages[0].text
    assert any("oscuramento" in n for n in scope.notes)
    assert "oscuramento" in scope.preview()
    png, w, h = content.render_area(pdf, 0, rects[0])
    pix = pymupdf.Pixmap(png)
    assert pix.pixel(pix.width // 2, pix.height // 2)[:3] == (0, 0, 0)


def test_hidden_text_excluded_but_ocr_layer_included(tmp_path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 100), "Testo visibile", fontsize=12)
    p1.insert_text((72, 200), "SegretoInvisibile", fontsize=12, render_mode=3)
    ocg = doc.add_ocg("Nascosto", on=False)
    p1.insert_text((72, 300), "SegretoLivello", fontsize=12, oc=ocg)
    p2 = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    p2.insert_image(p2.rect, pixmap=pix)
    p2.insert_text((72, 100), "StratoOCR", fontsize=12, render_mode=3)
    path = tmp_path / "h.pdf"
    doc.save(str(path))
    pdf = pymupdf.open(str(path))
    scope = engine.build_scope(pdf, DOCUMENT)
    t1, t2 = scope.pages[0].text, scope.pages[1].text
    assert "visibile" in t1
    assert "SegretoInvisibile" not in t1 and "SegretoLivello" not in t1
    assert "StratoOCR" in t2


def test_extraction_forbidden_blocks(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=12)
    path = tmp_path / "noextract.pdf"
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="prop", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    pdf = pymupdf.open(str(path))
    pdf.authenticate("")
    assert not content.extraction_allowed(pdf)
    with pytest.raises(content.ContentBlocked):
        engine.build_scope(pdf, DOCUMENT)
    pdf2 = pymupdf.open(str(path))
    pdf2.authenticate("prop")
    assert content.extraction_allowed(pdf2)


def test_no_file_name_or_metadata_sent(job, fake_provider, text_pdf):
    path = text_pdf(4, name="contratto-rossi.pdf", metadata={"author": "Rossi", "title": "Accordo Rossi"})
    pdf = open_pdf(path)
    scope = engine.build_scope(pdf, PAGES, pages=[1, 2])
    engine.summarize(job, scope, SummaryOptions())
    sent = fake_provider.all_text()
    assert "pagina 2" in sent and "pagina 3" in sent
    assert "pagina 1 " not in sent and "pagina 4" not in sent
    assert "contratto-rossi" not in sent and "Rossi" not in sent


def test_textless_pages_detected(tmp_path):
    doc = pymupdf.open()
    for i in range(5):
        p = doc.new_page()
        if i in (1, 3):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
            pix.clear_with(120)
            p.insert_image(p.rect, pixmap=pix)
        else:
            p.insert_text((72, 100), f"Testo della pagina {i + 1} con parole", fontsize=12)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    pdf = pymupdf.open(str(path))
    assert content.textless_pages(pdf, list(range(5))) == [1, 3]


# ------------------------------------------------------------------ chat
def test_chat_contains_all_pages_with_markers(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(3))
    scope = engine.build_scope(pdf, DOCUMENT)
    conv = Conversation()
    engine.chat(job, conv, scope, "Di cosa parla?")
    req = fake_provider.requests[0].messages[-1].content
    for i in range(1, 4):
        marker = f"=== [p. {i}] ==="
        assert marker in req
        assert req.index(marker) < req.index(f"pagina {i} parola{i}")
    assert "Di cosa parla?" in req


def test_chat_history_resent_and_reset(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(2))
    scope = engine.build_scope(pdf, DOCUMENT)
    conv = Conversation()
    for q in ("Domanda uno", "Domanda due"):
        ans = engine.chat(job, conv, scope, q)
        conv.messages.append(engine.ChatMessage("user", q))
        conv.messages.append(engine.ChatMessage("assistant", ans.text))
    engine.chat(job, conv, scope, "Domanda tre")
    msgs = fake_provider.requests[-1].messages
    assert len(msgs) == 5 and msgs[0].content == "Domanda uno" and msgs[2].content == "Domanda due"
    conv = Conversation()  # «Nuova conversazione»
    engine.chat(job, conv, scope, "Domanda quattro")
    assert len(fake_provider.requests[-1].messages) == 1


def test_references_verified_and_unverified(job, fake_provider, text_pdf):
    from foliant.modules.ai.render import render_markdown
    pdf = open_pdf(text_pdf(10))
    scope = engine.build_scope(pdf, DOCUMENT)
    fake_provider.responses = ["Vedi [p. 3] e [p. 42]"]
    ans = engine.chat(job, Conversation(), scope, "?")
    refs = ans.refs
    assert refs[0]["verified"] and refs[0]["page"] == 2
    assert not refs[1]["verified"]
    out = render_markdown(ans.text, scope.space)
    assert "href='page:0'" in out.html
    assert "non verificato" in out.html


def test_answer_without_refs_flagged(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(2))
    scope = engine.build_scope(pdf, DOCUMENT)
    ans = engine.chat(job, Conversation(), scope, "?")
    assert "non basata su pagine specifiche" in ans.flags


def test_label_reference_resolves_physical_page(text_pdf):
    from foliant.modules.ai.render import find_refs
    path = text_pdf(6, labels=[{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                               {"startpage": 5, "prefix": "", "style": "D", "firstpagenum": 1}])
    pdf = open_pdf(path)
    scope = engine.build_scope(pdf, DOCUMENT)
    assert scope.pages[3].label == "iv"
    assert "[p. iv]" in chunking.format_pages(scope.pages)
    ref = find_refs("come a [p. iv]", scope.space)[0]
    assert ref.verified and ref.page == 3


# ------------------------------------------------------------------ riassunto
def test_summary_presets_in_instructions(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(2))
    scope = engine.build_scope(pdf, DOCUMENT)
    engine.summarize(job, scope, SummaryOptions("short"))
    assert "100 words" in fake_provider.requests[-1].system
    engine.summarize(job, scope, SummaryOptions("custom", 300))
    assert "300 words" in fake_provider.requests[-1].system


def test_long_document_chunked_within_window(store, fake_provider, text_pdf):
    from foliant.modules.ai.config import SendLog
    from tests.modules.ai.conftest import make_active_profile
    profile = make_active_profile(store, context_window=8000)
    gw = engine.Gateway(store, SendLog(), provider_factory=lambda p: fake_provider, sleep=lambda s: None)
    texts = [f"Pagina numero {i + 1}. " + ("parola " * 120) + f"fine{i + 1}" for i in range(500)]
    pdf = open_pdf(text_pdf(500, texts=texts))
    scope = engine.build_scope(pdf, DOCUMENT)
    n, chars = engine.plan_requests(scope, profile)
    assert engine.needs_confirmation(n, chars)
    fake_provider.default = "Riassunto parziale breve [p. 1]."
    engine.summarize(engine.Job(gw), scope, SummaryOptions())
    for r in fake_provider.requests:
        assert chunking.estimate_tokens(r.all_text()) + r.max_tokens <= 8000
    sent = fake_provider.all_text()
    for i in range(500):
        assert f"fine{i + 1}" in sent
    assert "Riassunto parziale breve" in fake_provider.requests[-1].messages[-1].content
    # domanda: solo i blocchi scelti, ciascuno con il marcatore
    fake_provider.requests.clear()
    engine.chat(engine.Job(gw), Conversation(), scope, "Cosa dice fine250?")
    req = fake_provider.requests[0]
    assert chunking.estimate_tokens(req.all_text()) + req.max_tokens <= 8000
    assert "fine250" in req.all_text() and "=== [p. 250] ===" in req.all_text()
    assert len(req.all_text()) < 40000


def test_index_cache_reused_until_change(tmp_path, text_pdf):
    from foliant.modules.ai.chunking import IndexCache
    path = text_pdf(3)
    cache = IndexCache()
    doc = Document.open(path)
    fp = content.content_fingerprint(doc.pdf)
    engine.build_scope(doc.pdf, DOCUMENT, cache=cache, fingerprint=fp)
    assert cache.builds == 1
    cache2 = IndexCache()  # riapertura: indice dal disco
    doc2 = Document.open(path)
    fp2 = content.content_fingerprint(doc2.pdf)
    assert fp2 == fp
    engine.build_scope(doc2.pdf, DOCUMENT, cache=cache2, fingerprint=fp2)
    assert cache2.builds == 0
    with doc2.change("modifica") as pdf:
        pdf[0].insert_text((72, 700), "nuovo testo", fontsize=11)
    fp3 = content.content_fingerprint(doc2.pdf)
    assert fp3 != fp
    engine.build_scope(doc2.pdf, DOCUMENT, cache=cache2, fingerprint=fp3)
    assert cache2.builds == 1


def test_excluded_pages_marked_partial(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(5))
    scope = engine.build_scope(pdf, DOCUMENT, exclude=[1, 3])
    ans = engine.summarize(job, scope, SummaryOptions())
    sent = fake_provider.all_text()
    assert "pagina 2" not in sent and "pagina 4" not in sent
    assert "parziale: pagine 2, 4 escluse" in ans.flags


def test_ocr_text_used_without_modifying_document(job, fake_provider, text_pdf):
    path = text_pdf(2)
    doc = Document.open(path)
    scope = engine.build_scope(doc.pdf, DOCUMENT, ocr_texts={1: ("Testo riconosciuto OCR", 0.9)})
    engine.summarize(job, scope, SummaryOptions())
    assert "Testo riconosciuto OCR" in fake_provider.all_text()
    assert not doc.is_modified


# ------------------------------------------------------------------ traduzione, spiegazione, riscrittura
def test_translate_pages_sections(job, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(2))
    scope = engine.build_scope(pdf, PAGES, pages=[0, 1])
    fake_provider.default = lambda req: req.messages[-1].content.upper()
    ans = engine.translate_text(job, scope, "en", glossary=[("Foliant", None)])
    assert "### Pagina 1" in ans.text and "### Pagina 2" in ans.text
    assert "CONTENUTO UNICO DELLA PAGINA 1" in ans.text
    assert "English" in fake_provider.requests[0].system
    assert "«Foliant»: do not translate" in fake_provider.requests[0].system


def test_explain_sends_term_paragraph_and_level(job, fake_provider, tmp_path):
    from foliant.api import selection_from_words
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 72, 500, 200), "Il piano di ammortamento prevede rate costanti.", fontsize=11)
    p.insert_textbox(pymupdf.Rect(72, 400, 500, 500), "Altro paragrafo irrilevante sulle ferie.", fontsize=11)
    path = tmp_path / "e.pdf"
    doc.save(str(path))
    pdf = pymupdf.open(str(path))
    words = [w for w in pdf[0].get_text("words") if w[4] == "ammortamento"]
    sel = selection_from_words(0, words)
    scope = engine.build_scope(pdf, SELECTION, selection=sel)
    engine.explain(job, scope, "simple")
    text = fake_provider.all_text()
    assert "ammortamento" in text and "rate costanti" in text and "simple" in text
    assert "ferie" not in text


def test_rewrite_proposal_and_diff(job, fake_provider):
    scope = Scope(kind=SELECTION, selection="Il documento e stato aprovato")
    fake_provider.responses = ["«Il documento è stato approvato»"]
    ans = engine.rewrite(job, scope, "correct")
    assert ans.text == "Il documento è stato approvato"
    diff = engine.word_diff(scope.selection, ans.text)
    assert ("-", "e") in diff and ("+", "è") in diff
    fake_provider.responses = ["Il documento e stato aprovato"]
    assert "nessuna modifica proposta" in engine.rewrite(job, scope, "correct").flags


def test_block_translator_json(job, fake_provider):
    fake_provider.default = lambda req: json.dumps([s.upper() for s in json.loads(req.messages[-1].content)])
    tr = engine.block_translator(job, "en")
    assert tr(0, ["uno", "due"]) == ["UNO", "DUE"]
    fake_provider.default = "non json"
    with pytest.raises(ValueError):
        tr(0, ["uno"])


# ------------------------------------------------------------------ errori e ritentativi (AI-16)
def test_rate_limit_retry_after(job, gateway, fake_provider):
    fake_provider.responses = [AIError(RATE, "troppe richieste", retry_after=2), "ok"]
    ans = engine.chat(job, Conversation(), Scope(), "ciao")
    assert ans.text == "ok"
    assert len(fake_provider.requests) == 2
    assert gateway.waits == [2]


def test_rate_limit_waits_real_time(store, active_profile, fake_provider):
    import time
    from foliant.modules.ai.config import SendLog
    gw = engine.Gateway(store, SendLog(), provider_factory=lambda p: fake_provider)
    fake_provider.responses = [AIError(RATE, "x", retry_after=0.3), "ok"]
    engine.chat(engine.Job(gw), Conversation(), Scope(), "ciao")
    t = fake_provider.request_times
    assert t[1] - t[0] >= 0.3


def test_auth_error_not_retried_and_key_masked(store, active_profile, fake_provider):
    from foliant.modules.ai.config import SendLog
    gw = engine.Gateway(store, SendLog(), sleep=lambda s: None)
    gw.provider_factory = None

    class Bad(type(fake_provider)):
        pass
    bad = Bad([AIError(AUTH, "HTTP 401 key chiave-di-prova-123 invalid")])
    gw.provider = lambda profile, model=None: bad
    with pytest.raises(AIError) as ei:
        engine.chat(engine.Job(gw), Conversation(), Scope(), "x")
    assert ei.value.kind == AUTH
    assert "chiave-di-prova-123" not in ei.value.detail
    assert len(bad.requests) == 1


def test_server_error_retried_three_times(job, gateway, fake_provider):
    fake_provider.responses = [AIError(SERVER, "500")] * 5
    with pytest.raises(AIError) as ei:
        engine.chat(job, Conversation(), Scope(), "x")
    assert ei.value.kind == SERVER
    assert len(fake_provider.requests) == 4
    assert gateway.waits == [1, 2, 4]


def test_partial_text_kept_on_network_loss(job, fake_provider):
    from foliant.modules.ai.providers import INTERRUPTED
    fake_provider.responses = [{"chunks": ["Prima ", "parte"], "error": AIError(INTERRUPTED, "reset")}]
    with pytest.raises(AIError) as ei:
        engine.chat(job, Conversation(), Scope(), "x")
    assert ei.value.partial == "Prima parte"
    assert len(fake_provider.requests) == 1


def test_cancel_during_stream(job, fake_provider):
    import time
    fake_provider.responses = [{"chunks": ["a", "b", "c", "d", "e", "f"], "delay": 0.2}]
    cancel = threading.Event()
    got = []

    def on_text(t):
        got.append(t)
        if len(got) == 3:
            threading.Timer(0.05, cancel.set).start()

    job.on_text, job.cancel = on_text, cancel
    t0 = time.monotonic()
    ans = engine.chat(job, Conversation(), Scope(), "x")
    assert ans.status == "interrupted"
    assert got == ["a", "b", "c"]
    assert time.monotonic() - t0 < 2.0


def test_local_usage_limit(store, fake_provider):
    from foliant.modules.ai.config import SendLog
    from tests.modules.ai.conftest import make_active_profile
    make_active_profile(store, usage_limit=1)
    gw = engine.Gateway(store, SendLog(), provider_factory=lambda p: fake_provider, sleep=lambda s: None)
    engine.chat(engine.Job(gw), Conversation(), Scope(), "uno")
    with pytest.raises(AIError) as ei:
        engine.chat(engine.Job(gw), Conversation(), Scope(), "due")
    assert ei.value.kind == LIMIT
    assert len(fake_provider.requests) == 1


def test_send_log_has_no_content(job, gateway, fake_provider, text_pdf):
    pdf = open_pdf(text_pdf(3))
    scope = engine.build_scope(pdf, PAGES, pages=[0, 1])
    engine.summarize(job, scope, SummaryOptions())
    entries = gateway.log.entries()
    assert entries[-1]["function"] == "summary" and entries[-1]["pages"] == [1, 2]
    assert entries[-1]["chars"] > 0 and entries[-1]["outcome"] == "ok"
    raw = gateway.log.path.read_text()
    assert "Contenuto unico" not in raw


def test_consent_required(store, fake_provider):
    from foliant.modules.ai.config import SendLog
    from tests.modules.ai.conftest import make_active_profile
    p = make_active_profile(store)
    p.consent = False
    store.save(p)
    gw = engine.Gateway(store, SendLog(), provider_factory=lambda q: fake_provider)
    with pytest.raises(engine.ConsentRequired):
        engine.chat(engine.Job(gw), Conversation(), Scope(), "x")
    assert fake_provider.requests == []
