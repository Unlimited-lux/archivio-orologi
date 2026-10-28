"""AI-26: ricerca bibliografica e revisione della letteratura. Nessuna rete esterna."""

from __future__ import annotations

import json
import threading
import unicodedata
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pymupdf
import pytest

from foliant.modules.ai import biblio, providers
from foliant.modules.ai.biblio import Entry, Library

ENTRIES = [
    Entry("Calibri meccanici nell'Ottocento", ["Anna Rossi", "Luca Bianchi"], 2019, "Rivista di Orologeria",
          "10.1000/a1", "Studio sui calibri meccanici.", "https://doi.org/10.1000/a1",
          "https://oa.example/a1.pdf"),
    Entry("Escapement efficiency", ["John Smith"], 2021, "Horological Journal", "10.1000/b2",
          "Measures escapement efficiency.", "https://doi.org/10.1000/b2"),
    Entry("Lubrication & wear {tests}", ["Maria Verdi"], 2015, "", "hdl:20.500/xyz", "Wear tests.",
          "https://repo.example/xyz"),
    Entry("Quartz transition", ["Ken Sato", "Ana Lopez"], None, "Tech History", "10.1000/d4", "",
          "https://doi.org/10.1000/d4"),
]

SECRET = "TESTOSEGRETODOCUMENTO riservato"


class FakeService:
    name = "fake"

    def __init__(self, entries=ENTRIES):
        self.entries = list(entries)
        self.queries: list[str] = []

    def search(self, keywords, limit=10):
        self.queries.append(keywords)
        return list(self.entries[:limit])


# ------------------------------------------------------------------ revisione
REVIEW = (
    "## Introduzione\nI calibri sono studiati [1] e l'efficienza [2, 5].\n\n"
    "## Temi\nUsura [3]; transizione al quarzo [4]. Studio inesistente [5].\n\n"
    "## Lacune\nPoco sui materiali [1; 3].\n\n"
    "## Bibliografia\n1. Voce inventata dal modello\n5. Altra voce inventata\n"
)


def test_review_only_real_entries_and_fake_citation_marked(job, fake_provider):
    fake_provider.responses = [REVIEW]
    service = FakeService()
    found, _counts = biblio.search_all(service, ["calibri meccanici"])
    assert len(found) == 4
    text, used = biblio.generate_review(job, "Storia dei calibri", found)
    assert used == ENTRIES
    assert "[5 — non verificata]" in text
    assert "[2] [5 — non verificata]" in text
    assert "[1]" in text and "[1; 3]" in text
    body, bib = text.split("## Bibliografia")
    assert "inventata" not in text
    refs = [line for line in bib.strip().splitlines() if line[:1].isdigit()]
    assert len(refs) == 4
    assert refs[0].startswith("1. Anna Rossi, Luca Bianchi (2019). Calibri meccanici")
    assert "doi:10.1000/a1" in refs[0]
    assert all("[5]" not in r for r in refs)
    # le voci sono tutte nel prompt, numerate 1..4
    sent = fake_provider.requests[-1].all_text()
    assert "[4] Quartz transition" in sent and "[5]" not in sent


def test_review_uses_only_library_entries(job, fake_provider):
    lib = Library()
    lib.add(ENTRIES[0])
    lib.add(ENTRIES[2])
    fake_provider.responses = ["## Introduzione\nTesto [1] [2] [3]."]
    text, used = biblio.generate_review(job, "domanda", ENTRIES, lib)
    assert used == [ENTRIES[0], ENTRIES[2]]
    assert "[3 — non verificata]" in text
    assert text.count("\n1. ") == 1 and "\n3. " not in text


def test_review_pdf_has_numbered_references():
    text = biblio.finalize_review("## Intro\nVedi [1] e [9].", ENTRIES[:2])
    data = biblio.review_pdf(text, ENTRIES[:2])
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        content = unicodedata.normalize("NFKC", "".join(p.get_text() for p in doc))
    assert "Bibliografia" in content
    assert "non verificata" in content
    assert content.count("Bibliografia") == 1
    assert "Escapement efficiency" in content


# ------------------------------------------------------------------ riservatezza
def test_requests_contain_only_keywords(window, job, fake_provider, text_pdf):
    path = text_pdf(pages=1, texts=[SECRET])
    view = window.open_path(path)                      # documento aperto nell'app
    assert view is not None
    fake_provider.responses = [json.dumps(["Quali calibri meccanici dell'Ottocento?",
                                           "What is the efficiency of escapements?"])]
    service = FakeService()
    subs = biblio.propose_subquestions(job, "Storia dei calibri meccanici")
    assert len(subs) == 2
    biblio.search_all(service, subs)
    assert service.queries == ["calibri meccanici ottocento", "efficiency escapements"]
    for q in service.queries:
        assert set(q.split()) <= set(biblio.keywords_for(None, " ".join(subs)).split())
        assert "TESTOSEGRETODOCUMENTO".lower() not in q.lower()
    # nessuna chiamata al modello per le parole chiave
    assert len(fake_provider.requests) == 1


def test_keywords_local():
    assert biblio.keywords_for(None, "Qual è il ruolo della lubrificazione negli orologi del 1900?") == \
        "lubrificazione orologi 1900"
    assert biblio.keywords_for(None, "") == ""


# ------------------------------------------------------------------ servizio HTTP locale
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.paths.append(self.path)
        status, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server(monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
                "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.paths = []
    srv.reply = (200, {})
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


OPENALEX_WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1234/ABC",
    "display_name": "Watch escapements",
    "publication_year": 2020,
    "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {"display_name": "Bob Ross"}}],
    "primary_location": {"source": {"display_name": "Journal of Time"}, "landing_page_url": "https://j.example/1"},
    "open_access": {"is_oa": True, "oa_url": "https://oa.example/1.pdf"},
    "abstract_inverted_index": {"Escapements": [0], "regulate": [1], "time": [2, 4], "over": [3]},
}


def test_openalex_parsing_and_query(local_server):
    local_server.reply = (200, {"results": [OPENALEX_WORK, {"id": "https://openalex.org/W2", "title": "Bare"}]})
    svc = biblio.OpenAlexService(f"http://127.0.0.1:{local_server.server_port}")
    res = svc.search("watch escapements", limit=5)
    assert len(res) == 2
    e = res[0]
    assert e.title == "Watch escapements"
    assert e.authors == ["Ada Lovelace", "Bob Ross"]
    assert e.year == 2020 and e.venue == "Journal of Time"
    assert e.identifier == "10.1234/abc" and e.url == "https://doi.org/10.1234/abc"
    assert e.abstract == "Escapements regulate time over time"
    assert e.oa_url == "https://oa.example/1.pdf"
    assert res[1].identifier == "https://openalex.org/W2" and res[1].title == "Bare"
    path = local_server.paths[0]
    parsed = urllib.parse.urlparse(path)
    assert parsed.path == "/works"
    q = urllib.parse.parse_qs(parsed.query)
    assert q == {"search": ["watch escapements"], "per-page": ["5"]}


def test_crossref_parsing(local_server):
    local_server.reply = (200, {"message": {"items": [{
        "DOI": "10.5/X", "title": ["A <i>study</i>"], "author": [{"given": "Ada", "family": "L"}],
        "issued": {"date-parts": [[2011, 3]]}, "container-title": ["J"], "abstract": "<jats:p>Abs</jats:p>",
        "URL": "https://doi.org/10.5/x"}]}})
    svc = biblio.CrossrefService(f"http://127.0.0.1:{local_server.server_port}")
    [e] = svc.search("study", 3)
    assert (e.title, e.authors, e.year, e.venue, e.identifier, e.abstract) == \
        ("A study", ["Ada L"], 2011, "J", "10.5/x", "Abs")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(local_server.paths[0]).query)
    assert q == {"query": ["study"], "rows": ["3"]}


def test_service_errors_classified(local_server):
    svc = biblio.OpenAlexService(f"http://127.0.0.1:{local_server.server_port}", sleep=lambda s: None)
    local_server.reply = (429, {"error": "slow down"})
    with pytest.raises(providers.AIError) as ei:
        svc.search("x y")
    assert ei.value.kind == providers.RATE
    assert len(local_server.paths) == 1 + svc.retries
    local_server.reply = (500, {})
    with pytest.raises(providers.AIError) as ei:
        svc.search("x y")
    assert ei.value.kind == providers.SERVER
    port = local_server.server_port
    local_server.shutdown()
    local_server.server_close()
    with pytest.raises(providers.AIError) as ei:
        biblio.OpenAlexService(f"http://127.0.0.1:{port}", sleep=lambda s: None).search("x")
    assert ei.value.kind == providers.NETWORK


# ------------------------------------------------------------------ impostazioni
def test_disabled_without_service(store):
    assert biblio.service_from_settings(store) is None
    assert biblio.service_from_settings(None) is None
    biblio.save_service_settings(store, "openalex")
    svc = biblio.service_from_settings(store)
    assert isinstance(svc, biblio.OpenAlexService) and svc.base_url == biblio.OPENALEX_URL
    biblio.save_service_settings(store, "crossref", "https://cr.example/api/")
    svc = biblio.service_from_settings(store)
    assert isinstance(svc, biblio.CrossrefService) and svc.base_url == "https://cr.example/api"
    biblio.save_service_settings(store, "crossref", "http://remote.example")
    assert biblio.service_from_settings(store) is None
    biblio.save_service_settings(store, "")
    assert biblio.service_from_settings(store) is None


# ------------------------------------------------------------------ libreria ed esportazioni
def test_library_dedupe_and_persistence():
    lib = Library()
    assert lib.add(ENTRIES[0]) is True
    dup = Entry("Altro titolo", identifier="https://doi.org/10.1000/A1")
    assert lib.add(dup) is False
    assert lib.add_many(ENTRIES) == 3
    assert lib.path.name == "bibliografia.json"
    again = Library()
    assert [e.identifier for e in again.list()] == [e.identifier for e in ENTRIES]
    assert again.remove("10.1000/b2") is True
    assert len(Library()) == 3


def test_bibtex_export():
    bib = biblio.to_bibtex(ENTRIES)
    assert bib.count("@") == 4
    assert "@article{rossi2019calibri," in bib
    assert "author = {Anna Rossi and Luca Bianchi}" in bib
    assert "doi = {10.1000/a1}" in bib
    assert "@misc{verdi2015lubrication," in bib
    assert r"title = {Lubrication \& wear \{tests\}}" in bib
    assert "note = {hdl:20.500/xyz}" in bib
    keys = biblio.to_bibtex([ENTRIES[0], ENTRIES[0]])
    assert "rossi2019calibri," in keys and "rossi2019calibria," in keys


def test_ris_export():
    ris = biblio.to_ris(ENTRIES)
    assert ris.count("ER  - ") == 4
    assert "TY  - JOUR\nTI  - Calibri meccanici nell'Ottocento\nAU  - Anna Rossi\nAU  - Luca Bianchi\nPY  - 2019" in ris
    assert "DO  - 10.1000/a1" in ris
    assert "L1  - https://oa.example/a1.pdf" in ris
    assert "TY  - GEN" in ris and "ID  - hdl:20.500/xyz" in ris


def test_subquestions_bad_reply(job, fake_provider):
    fake_provider.responses = ["nessun elenco qui"]
    with pytest.raises(ValueError):
        biblio.propose_subquestions(job, "domanda")
    fake_provider.responses = ["1. Prima domanda\n2. Seconda domanda"]
    assert biblio.propose_subquestions(job, "domanda") == ["Prima domanda", "Seconda domanda"]


# ------------------------------------------------------------------ dialogo
def test_dialog_smoke(qtbot, job, fake_provider, store, tmp_path):
    from foliant.modules.ai.biblio_view import BiblioDialog

    service = FakeService()
    opened, errors = [], []
    saved = iter([str(tmp_path / "r.pdf"), str(tmp_path / "b.bib"), str(tmp_path / "b.ris")])
    fake_provider.responses = [json.dumps(["Calibri meccanici?", "Escapement efficiency?"]), "Sintesi breve.",
                               REVIEW]

    dlg = BiblioDialog(lambda title, fn: fn(job), service, lambda *a: next(saved), opened.append,
                       store=store, on_error=lambda t, m: errors.append(m), question="Storia dei calibri")
    qtbot.addWidget(dlg)
    assert dlg.disabled_label.isHidden()
    dlg.propose()
    assert dlg.subquestion_texts() == ["Calibri meccanici?", "Escapement efficiency?"]
    dlg.search()
    assert service.queries == ["calibri meccanici", "escapement efficiency"]
    assert dlg.results_list.count() == 4
    dlg.summarize_current()
    assert "Sintesi breve." in dlg.detail.toPlainText()
    dlg._open_current("oa_url")
    assert opened == ["https://oa.example/a1.pdf"]
    for row in range(4):
        dlg.results_list.setCurrentRow(row)
        dlg.add_current_to_library()
    assert dlg.library_list.count() == 4
    dlg.generate_review()
    text = dlg.review_text.toPlainText()
    assert "[5 — non verificata]" in text
    assert dlg.export_pdf() and (tmp_path / "r.pdf").read_bytes().startswith(b"%PDF")
    assert dlg.export_bibtex() and (tmp_path / "b.bib").read_text().count("@") == 4
    assert dlg.export_ris() and (tmp_path / "b.ris").read_text().count("ER  - ") == 4
    assert errors == []


def test_dialog_disabled_without_service(qtbot, store):
    from foliant.modules.ai.biblio_view import BiblioDialog

    dlg = BiblioDialog(lambda t, f: None, None, lambda *a: None, lambda u: None, store=store)
    qtbot.addWidget(dlg)
    assert not dlg.disabled_label.isHidden()
    assert "Nessun servizio bibliografico configurato" in dlg.disabled_label.text()
    assert not dlg.tabs.isEnabled()
    dlg.service_combo.setCurrentIndex(dlg.service_combo.findData("openalex"))
    dlg.apply_service()
    assert isinstance(dlg.service, biblio.OpenAlexService)
    assert dlg.tabs.isEnabled() and dlg.disabled_label.isHidden()
    assert store.option("biblio_service", "") == "openalex"
