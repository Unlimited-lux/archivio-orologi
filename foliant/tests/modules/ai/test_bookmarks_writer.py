"""Segnalibri generati, riassunto per sezione (AI-22) e scrittura assistita (AI-19)."""

from __future__ import annotations

import json
import re

import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.ai import bookmarks_ai, engine, writer
from foliant.modules.ai.bookmarks_ai import BNode


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.fixture
def structured_pdf(tmp_path):
    """6 pagine: capitolo (corpo 20) all'inizio delle pagine 1, 3, 5; sottosezioni (corpo 14)."""
    doc = pymupdf.open()
    body = "Testo normale del documento che descrive il contenuto della sezione in modo semplice. " * 3
    for ch in range(3):
        for half in range(2):
            page = doc.new_page()
            y = 80
            if half == 0:
                page.insert_text((72, y), f"Capitolo {ch + 1}", fontsize=20, fontname="hebo")
                y += 40
            sub = ch * 2 + half + 1
            page.insert_text((72, y + 200), f"Sezione {ch + 1}.{half + 1}", fontsize=14, fontname="hebo")
            page.insert_textbox(pymupdf.Rect(72, y + 10, 520, y + 180), body, fontsize=11)
            page.insert_textbox(pymupdf.Rect(72, y + 215, 520, y + 400), body + f" parte {sub}", fontsize=11)
    doc.set_toc([[1, "Vecchio segnalibro", 2]])
    path = tmp_path / "strutturato.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _proposal(extra_invalid: bool = False) -> str:
    tree = []
    for ch in range(3):
        children = [{"title": f"Sezione {ch + 1}.{h + 1}", "page": ch * 2 + h + 1, "children": []} for h in range(2)]
        tree.append({"title": f"Capitolo {ch + 1}", "page": ch * 2 + 1, "children": children})
    if extra_invalid:
        tree.append({"title": "Appendice fantasma", "page": 99, "children": []})
    return "```json\n" + json.dumps(tree) + "\n```"


def test_heading_candidates_find_larger_lines(structured_pdf):
    doc = Document.open(structured_pdf)
    cands = bookmarks_ai.heading_candidates(doc.pdf, None)
    texts = [(c["text"], c["page"]) for c in cands]
    assert ("Capitolo 1", 0) in texts and ("Sezione 3.2", 5) in texts
    assert not any("Testo normale" in t for t, _p in texts)


def test_propose_and_replace_bookmarks_single_undo(job, fake_provider, structured_pdf):
    doc = Document.open(structured_pdf)
    previous = doc.pdf.get_toc()
    fake_provider.responses = [_proposal()]
    nodes, warnings = bookmarks_ai.propose_bookmarks(job, doc.pdf, max_depth=3)
    assert warnings == []
    # solo i candidati, non il testo completo
    sent = fake_provider.requests[0].messages[0].content
    assert "Capitolo 2" in sent and "Testo normale" not in sent
    assert len(nodes) == 3 and all(len(n.children) == 2 for n in nodes)
    assert nodes[1].page == 2 and nodes[1].y is not None

    bookmarks_ai.apply_bookmarks(doc, nodes, "replace")
    toc = doc.pdf.get_toc(simple=False)
    assert [e[0] for e in toc].count(1) == 3
    assert [e[0] for e in toc].count(2) == 6
    by_title = {e[1]: e for e in toc}
    assert by_title["Capitolo 2"][2] == 3
    assert by_title["Sezione 3.2"][2] == 6
    for n in nodes:
        e = by_title[n.title]
        assert e[3]["page"] == n.page
        assert abs(e[3]["to"].y - n.y) < 1
    assert "Vecchio segnalibro" not in by_title

    assert doc.undo()
    assert doc.pdf.get_toc() == previous


def test_merge_keeps_existing(job, fake_provider, structured_pdf):
    doc = Document.open(structured_pdf)
    nodes = [BNode("Capitolo 1", 0, 80.0, [BNode("Sezione 1.1", 0, 300.0)]), BNode("Capitolo 3", 4, None)]
    bookmarks_ai.apply_bookmarks(doc, nodes, "merge")
    toc = doc.pdf.get_toc()
    assert [e[1] for e in toc] == ["Capitolo 1", "Sezione 1.1", "Vecchio segnalibro", "Capitolo 3"]


def test_invalid_page_discarded_with_warning(job, fake_provider, structured_pdf):
    doc = Document.open(structured_pdf)
    fake_provider.responses = [_proposal(extra_invalid=True)]
    nodes, warnings = bookmarks_ai.propose_bookmarks(job, doc.pdf)
    assert [n.title for n in nodes] == ["Capitolo 1", "Capitolo 2", "Capitolo 3"]
    assert any("Appendice fantasma" in w and "99" in w for w in warnings)


def test_title_not_found_goes_to_top_of_page(job, fake_provider, structured_pdf):
    doc = Document.open(structured_pdf)
    fake_provider.responses = [json.dumps([{"title": "Titolo inventato", "page": 2, "children": []}])]
    nodes, _w = bookmarks_ai.propose_bookmarks(job, doc.pdf)
    assert nodes[0].page == 1 and nodes[0].y is None


def test_no_structure_suggests_every_n(job, fake_provider, text_pdf):
    doc = Document.open(text_pdf(pages=4))
    nodes, warnings = bookmarks_ai.propose_bookmarks(job, doc.pdf)
    assert nodes == [] and warnings and "ogni" in warnings[0]
    assert fake_provider.requests == []
    fb = bookmarks_ai.fallback_every_n(12, 5)
    assert [n.page for n in fb] == [0, 5, 10]


def test_section_pages():
    toc = [[1, "A", 1], [2, "A.1", 2], [1, "B", 5], [1, "C", 9]]
    assert bookmarks_ai.section_pages(toc, 0, 12) == [0, 1, 2, 3]
    assert bookmarks_ai.section_pages(toc, 1, 12) == [1, 2, 3]
    assert bookmarks_ai.section_pages(toc, 3, 12) == [8, 9, 10, 11]


def test_summarize_section_sends_only_its_pages(job, fake_provider, text_pdf):
    path = text_pdf(pages=12)
    doc = Document.open(path)
    with doc.change("segnalibri") as pdf:
        pdf.set_toc([[1, "Uno", 1], [1, "Due", 5], [1, "Tre", 9]])
    fake_provider.responses = ["Riassunto della sezione [p. 5]."]
    ans = bookmarks_ai.summarize_section(job, doc.pdf, 1, engine.SummaryOptions(length="short"))
    assert ans.text.startswith("Riassunto")
    sent = fake_provider.requests[0].all_text()
    present = {int(n) for n in re.findall(r"\bparola(\d+)\b", sent)}
    assert present == {5, 6, 7, 8}
    assert fake_provider.requests[0] is fake_provider.requests[-1]


# ------------------------------------------------------------------ scrittura
LETTER = "Gentile cliente, la informiamo che il suo ordine è stato spedito oggi.\n\nCordiali saluti"


def test_write_text_uses_prompt_and_source(job, fake_provider, text_pdf):
    doc = Document.open(text_pdf(pages=2))
    scope = engine.build_scope(doc.pdf, engine.DOCUMENT)
    fake_provider.responses = [LETTER]
    ans = writer.write_text(job, "lettera", "Comunica la spedizione", "formale", 120, "it", source_scope=scope)
    assert ans.text == LETTER
    req = fake_provider.requests[0]
    assert "letter" in req.system and "formal" in req.system and "120" in req.system
    assert "parola2" in req.messages[0].content and "Comunica la spedizione" in req.messages[0].content
    fake_provider.responses = [LETTER]
    writer.write_text(job, "e-mail", "Saluti", "cordiale", 50, "en")
    assert "<document>" not in fake_provider.requests[1].messages[0].content
    assert "English" in fake_provider.requests[1].system


def test_insert_generated_text_box_and_undo(job, fake_provider, text_pdf):
    doc = Document.open(text_pdf(pages=3))
    fake_provider.responses = ["Gentile cliente, …"]
    text = writer.write_text(job, "lettera", "Scrivi una lettera", "formale", 50, "it").text
    rect = writer.insert_text_box_at(doc, 1, (100, 200), text)
    page = doc.pdf[1]
    annots = list(page.annots())
    assert len(annots) == 1
    a = annots[0]
    assert a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT
    assert a.info["content"] == "Gentile cliente, …"
    assert abs(a.rect.x0 - 100) < 1 and abs(a.rect.y0 - 200) < 1
    assert abs(rect.x0 - 100) < 1
    assert doc.undo_label == "Inserisci testo generato"
    assert doc.undo()
    assert list(doc.pdf[1].annots()) == []


def test_text_to_new_pdf_extracts_same_text():
    text = ("# Comunicazione\n\nGentile cliente, la presente per confermare **l'avvenuta spedizione** "
            "del suo ordine.\n\n" + "Restiamo a disposizione per ogni chiarimento. " * 120 +
            "\n\nCordiali saluti")
    data = writer.text_to_new_pdf(text)
    pdf = pymupdf.open("pdf", data)
    assert pdf.page_count >= 2
    assert all(abs(p.rect.width - 595) < 1 and abs(p.rect.height - 842) < 1 for p in pdf)
    extracted = "".join(p.get_text() for p in pdf)
    expected = text.replace("# ", "").replace("**", "")
    assert _norm(extracted) == _norm(expected)
    fonts = [f for p in pdf for f in p.get_fonts(full=True)]
    assert fonts and all(f[1] != "n/a" for f in fonts)  # caratteri incorporati


def test_markdown_lists_in_new_pdf():
    html = writer.markdown_to_html("## Punti\n- uno\n- **due**\n1. primo")
    assert "<h2>Punti</h2>" in html and "<ul><li>uno</li><li><b>due</b></li></ul>" in html and "<ol>" in html


# ------------------------------------------------------------------ dialoghi
def test_write_dialog(qtbot):
    from foliant.modules.ai.write_view import WriteDialog
    inserted, created, calls = [], [], []

    def gen(params):
        calls.append(params)
        return "Gentile cliente, …"

    dlg = WriteDialog(gen, on_insert=inserted.append, on_new_pdf=created.append)
    qtbot.addWidget(dlg)
    assert not dlg.generate_button.isEnabled()
    assert not dlg.insert_button.isEnabled()
    dlg.instructions.setPlainText("Risposta al cliente")
    assert dlg.generate_button.isEnabled()
    dlg.use_document.setChecked(True)
    dlg.generate_button.click()
    assert calls[0]["instructions"] == "Risposta al cliente" and calls[0]["use_document"]
    assert calls[0]["kind"] == "lettera"
    assert dlg.text() == "Gentile cliente, …"
    dlg.result.setPlainText("Gentile cliente, modificato")
    dlg.insert_button.click()
    dlg.new_pdf_button.click()
    assert inserted == ["Gentile cliente, modificato"] and created == ["Gentile cliente, modificato"]


def test_bookmarks_dialog_edit_and_apply(qtbot):
    from foliant.modules.ai.write_view import BookmarksDialog
    applied = []
    nodes = [BNode("Capitolo 1", 0, 80.0, [BNode("Sezione 1.1", 1, None)]), BNode("Capitolo 2", 2, None)]
    dlg = BookmarksDialog(nodes, ["avviso"], on_apply=lambda n, m: applied.append((n, m)))
    qtbot.addWidget(dlg)
    dlg.tree.topLevelItem(0).setText(0, "Primo capitolo")
    dlg.merge_button.click()
    out, mode = applied[0]
    assert mode == "merge" and dlg.mode == "merge"
    assert out[0].title == "Primo capitolo" and out[0].y == 80.0 and out[0].children[0].page == 1
    assert out[1].title == "Capitolo 2"
