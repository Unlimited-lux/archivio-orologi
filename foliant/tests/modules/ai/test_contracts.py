"""Contratti (AI-28) e comunicazione al cliente dalla revisione (AI-31)."""

from __future__ import annotations

import json

import pymupdf

from .conftest import FakeProvider, make_active_profile
from .test_panel import wait_done


def _ready(window):
    fake = FakeProvider()
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    services.refresh()
    services.controller.exec_dialog = lambda dlg: 0
    return services, fake


def _contract(tmp_path, n=6):
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 100), f"Art. {i + 1} Clausola numero {i + 1} sul pagamento", fontsize=11)
    path = tmp_path / "contratto.pdf"
    doc.save(str(path))
    return str(path)


def test_draft_numbered_articles_with_disclaimer(window):
    services, fake = _ready(window)
    fake.responses = ["Art. 1 Oggetto\nLa locazione riguarda...\nArt. 2 Durata\nQuattro anni."]
    dlg = services.controller.open_contracts()
    for k, v in (("tipo", "locazione"), ("parti", "Rossi e Bianchi"), ("durata", "4 anni")):
        dlg.params[k].setText(v)
    dlg.draft()
    sent = fake.requests[0].system
    assert "locazione" in sent and "Rossi e Bianchi" in sent and "4 anni" in sent
    text = dlg.draft_text.toPlainText()
    assert "**Art. 1** — Oggetto" in text and "**Art. 2** — Durata" in text
    assert "non sostituisce il parere di un professionista" in text


def _clauses_reply(levels, quote_ok=True):
    return json.dumps({"clauses": [{"numero": str(i + 1), "titolo": f"T{i + 1}", "pagina": i + 1,
                                    "testo": f"Clausola numero {i + 1}" if quote_ok or i else "testo inventato",
                                    "sintesi": "s", "criticita": lv, "proposta": "p"}
                                   for i, lv in enumerate(levels)]})


def test_review_table_click_comments_undo(window, tmp_path):
    services, fake = _ready(window)
    view = window.open_path(_contract(tmp_path))
    fake.responses = [_clauses_reply(["bassa", "alta", "bassa", "alta", "bassa", "bassa"], quote_ok=False)]
    dlg = services.controller.open_contracts()
    dlg.review()
    assert dlg.table.rowCount() == 6 and dlg.table.item(2, 2).text() == "3" and dlg.table.item(1, 3).text() == "alta"
    dlg.on_row(2)
    assert view.current_page == 2
    assert dlg.clauses[0].verified is False and all(c.verified for c in dlg.clauses[1:])
    dlg.confirm = lambda t, m: True
    assert dlg.add_comments() == 2
    d = view.document
    with d.lock:
        counts = [len(list(d.pdf[i].annots())) for i in range(6)]
    assert counts == [0, 1, 0, 1, 0, 0]
    d.undo()
    with d.lock:
        assert sum(len(list(d.pdf[i].annots())) for i in range(6)) == 0


def test_risks_sources_to_verify(window, tmp_path):
    services, fake = _ready(window)
    window.open_path(_contract(tmp_path, 2))
    fake.responses = [json.dumps({"risks": [{"descrizione": "Penale elevata", "clausola": "2"}],
                                  "sources": [{"riferimento": "art. 1382 c.c.", "citazione": "clausola penale"}]})]
    dlg = services.controller.open_contracts()
    dlg.party.setText("acquirente")
    dlg.assess()
    assert dlg.risks.sources[0].da_verificare
    html = dlg.risks_view.toHtml()
    assert "da verificare" in html and "non sostituisce il parere" in html


def test_prepare_customer_communication_from_review(qtbot, window, tmp_path):
    services, fake = _ready(window)
    window.open_path(_contract(tmp_path))
    fake.responses = [_clauses_reply(["alta", "bassa", "alta", "bassa", "bassa", "bassa"]),
                      "Gentile [nome del cliente], ..."]
    dlg = services.controller.open_contracts()
    dlg.review()
    dlg.comm_btn.click()
    wait_done(qtbot, services.panel)
    req = fake.requests[-1]
    assert "Clausola 1" in req.system and "Clausola 3" in req.system and "Clausola 2" not in req.system
    assert "cordiale" in req.system or "formale" in req.system
