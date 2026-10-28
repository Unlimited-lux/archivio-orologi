"""Funzioni P2 collegate all'interfaccia: attività, scrittura, segnalibri, sezioni, estrazione."""

from __future__ import annotations

import json

import pymupdf
import pytest

from foliant.modules.ai import tasks_library
from foliant.modules.ai.engine import Scope
from foliant.modules.ai.tasks_library import TaskStore, TaskTemplate

from .conftest import FakeProvider, make_active_profile
from .test_panel import wait_done


@pytest.fixture
def fake():
    return FakeProvider()


@pytest.fixture
def ready(window, fake):
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    services.refresh()
    return services


# ------------------------------------------------------------------ AI-20
def test_quiz_template_export_with_solutions(qtbot, window, ready, fake, sample_pdf, tmp_path):
    window.open_path(sample_pdf)
    quiz = {"questions": [{"question": f"Domanda {i}", "options": ["a", "b", "c"], "answer": "a",
                           "explanation": f"perché {i}", "page": 1} for i in range(5)]}
    fake.responses = [json.dumps(quiz)]
    task = next(t for t in tasks_library.BUILTIN if t.id == "quiz")
    panel = ready.panel
    panel.run_template(task, {"numero": "5", "tipo": "multiple choice"}, "document")
    wait_done(qtbot, panel)
    assert "5 multiple choice questions" in fake.requests[0].system
    out = tmp_path / "quiz.md"
    panel.save_result(str(out))
    text = out.read_text()
    head, _sep, solutions = text.partition("## Soluzioni")
    assert head.count("Domanda") == 5 and solutions.count("perché") == 5
    assert "perché" not in head


def test_legal_template_has_disclaimer(qtbot, window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    fake.responses = ["Rischi principali: nessuno [p. 1]"]
    task = next(t for t in tasks_library.BUILTIN if t.category == "Legale")
    panel = ready.panel
    panel.run_template(task, {}, "document")
    wait_done(qtbot, panel)
    msg = panel.conversation().messages[-1]
    assert "non sostituisce il parere di un professionista" in msg.text


def test_cv_template_excludes_personal_data():
    task = next(t for t in tasks_library.BUILTIN if t.id == "cv")
    instr = tasks_library.build_instructions(task, {"requisiti": "Python"}, Scope(), "it")
    assert "age" in instr and "marital status" in instr and "Python" in instr


def test_custom_task_roundtrip_same_instructions(tmp_path):
    store_a = TaskStore(tmp_path / "a.json")
    t = TaskTemplate.from_dict({"name": "Mia", "category": "Ricerca", "instructions":
                                "Elenca i punti su {tema} in {lingua}", "params": [{"name": "tema"}]})
    store_a.save(t)
    exported = TaskStore.export(store_a.custom())
    store_b = TaskStore(tmp_path / "b.json")
    imported = store_b.import_text(exported)
    scope = Scope()
    assert tasks_library.build_instructions(imported[0], {"tema": "orologi"}, scope, "it") == \
        tasks_library.build_instructions(t, {"tema": "orologi"}, scope, "it")


def test_invalid_task_import_rejected(tmp_path):
    from foliant.api import FoliantError
    store = TaskStore(tmp_path / "x.json")
    with pytest.raises(FoliantError, match="Segnaposto sconosciuti"):
        store.import_text(json.dumps({"tasks": [{"name": "X", "category": "Studio", "instructions": "{ignoto}"}]}))
    with pytest.raises(FoliantError):
        store.import_text("non json")
    assert store.custom() == []


def test_tasks_dialog(window, ready, tmp_path):
    from foliant.modules.ai.tasks_view import TasksDialog
    dlg = TasksDialog(TaskStore(tmp_path / "t.json"), window)
    dlg.category.setCurrentIndex(dlg.category.findData("Studio"))
    names = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert names and all("Studio" in n for n in names)
    dlg.search.setText("quiz")
    assert dlg.list.count() == 1
    dlg.run()
    assert dlg.chosen.id == "quiz" and dlg.values()["numero"] == "5"


# ------------------------------------------------------------------ AI-22 sezione
def test_summarize_section_sends_section_pages(qtbot, window, ready, fake, text_pdf):
    path = text_pdf(12)
    doc = pymupdf.open(path)
    doc.set_toc([[1, "Uno", 1], [1, "Due", 5], [1, "Tre", 9]])
    doc.saveIncr()
    doc.close()
    view = window.open_path(path)
    view.go_to_page(6)
    ready.controller.summarize_section()
    wait_done(qtbot, ready.panel)
    sent = fake.all_text()
    for n in range(5, 9):
        assert f"pagina {n} parola{n}" in sent
    assert "pagina 4 parola4" not in sent and "pagina 9 parola9" not in sent


def test_generate_bookmarks_controller(window, ready, fake, tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 80), f"Capitolo {i + 1}", fontsize=22)
        p.insert_text((72, 140), "Testo normale del capitolo con varie parole.", fontsize=10)
    path = tmp_path / "cap.pdf"
    doc.save(str(path))
    view = window.open_path(str(path))
    fake.responses = [json.dumps([{"title": f"Capitolo {i + 1}", "page": i + 1} for i in range(3)])]
    ready.controller.exec_dialog = lambda dlg: dlg.choose("replace")
    ready.controller.generate_bookmarks()
    with view.document.lock:
        toc = view.document.pdf.get_toc()
    assert [e[1] for e in toc] == ["Capitolo 1", "Capitolo 2", "Capitolo 3"]


# ------------------------------------------------------------------ AI-19
def test_writer_new_pdf_and_insert(qtbot, window, ready, fake, sample_pdf):
    view = window.open_path(sample_pdf)
    fake.responses = ["Gentile cliente, la consegna è prevista per lunedì."]
    seen = {}

    def run(dlg):
        dlg.instructions.setPlainText("Risposta su un ritardo")
        dlg.run_generate()
        seen["dlg"] = dlg
        return 0
    ready.controller.exec_dialog = run
    dlg = ready.controller.open_writer()
    text = dlg.text()
    assert "Gentile cliente" in text
    n_views = len(window.views())
    dlg.new_pdf()
    assert len(window.views()) == n_views + 1
    new_doc = window.ctx.current_document()
    assert "Gentile cliente, la consegna è prevista per lunedì." in " ".join(new_doc.page_text(0).split())
    from foliant.modules.ai.ui import Controller
    window.tabs.setCurrentWidget(view)
    Controller._insert_written(ready.controller, view.document, 1, pymupdf.Point(100, 200), text)
    with view.document.lock:
        page = view.document.pdf[1]
        annots = [(a.info["content"], pymupdf.Rect(a.rect)) for a in page.annots()]
    assert annots and "Gentile cliente" in annots[0][0] and abs(annots[0][1].x0 - 100) < 2
    view.document.undo()
    with view.document.lock:
        assert not list(view.document.pdf[1].annots())


# ------------------------------------------------------------------ AI-10
def test_extract_controller_opens_grid(window, ready, fake, tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Articolo  Quantità  Prezzo", fontsize=11)
    p.insert_text((72, 120), "Cinturino  2  45,00", fontsize=11)
    path = tmp_path / "tab.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    fake.responses = [json.dumps({"records": [{"Articolo": "Cinturino", "Quantità": 2, "Prezzo": "45,00",
                                               "_page": 1}]})]
    shown = []
    ready.controller.exec_dialog = lambda dlg: shown.append(dlg) or 0
    dlg = ready.controller.extract_data("tables")
    assert shown and dlg is shown[0]
    assert len(dlg.result.records) == 1


def test_p2_actions_disabled_without_profile(window, sample_pdf):
    window.open_path(sample_pdf)
    services = window.ctx.ai_services
    services.refresh()
    assert all(not a.isEnabled() for a in services.doc_actions + services.profile_actions)
    assert all(a.isEnabled() for a in services.local_actions)


# ------------------------------------------------------------------ AI-21
def _vehicle_pdf(tmp_path):
    doc = pymupdf.open()
    for i in range(8):
        p = doc.new_page()
        if i == 6:
            p.insert_text((72, 100), "La vettura rossa è parcheggiata nel cortile.", fontsize=12)
        else:
            p.insert_text((72, 100), f"Contenuto generico numero {i + 1} su orologi e cinturini.", fontsize=12)
    path = tmp_path / "veicoli.pdf"
    doc.save(str(path))
    return str(path)


def _embed(texts):
    # vettori simulati: «automobile» e «vettura» vicini
    out = []
    for t in texts:
        t = t.lower()
        car = 1.0 if ("vettura" in t or "automobile" in t) else 0.0
        out.append([car, 1.0 - car, 0.1])
    return out


def test_semantic_search_with_vectors(window, ready, fake, tmp_path):
    p = ready.store.get(ready.store.active_id())
    p.embed_model = "vettori-simulati"
    ready.store.save(p)
    ready.embedder = _embed
    view = window.open_path(_vehicle_pdf(tmp_path))
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.semantic_search("automobile")
    assert dlg.results and dlg.results[0].page == 6
    dlg.open_selected()
    assert view.current_page == 6 and view.search_hits.get(6)
    assert fake.requests == []


def test_semantic_search_chat_fallback(window, ready, fake, tmp_path):
    window.open_path(_vehicle_pdf(tmp_path))

    def rank(req):
        lines = req.messages[-1].content.splitlines()
        hit = next(line for line in lines if "vettura" in line)
        return json.dumps([int(hit[1:hit.index("]")])])
    fake.default = rank
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.semantic_search("automobile")
    assert dlg.results[0].page == 6
    assert "più richieste" in dlg.note.text()


# ------------------------------------------------------------------ AI-13
def test_collection_chat_and_reference(qtbot, window, ready, fake, text_pdf, tmp_path):
    a = text_pdf(2, name="A.pdf", texts=["Alfa uno", "Alfa due"])
    b = text_pdf(3, name="B.pdf", texts=["Beta uno", "Beta due prezzo 100", "Beta tre"])
    window.open_path(a)
    fake.responses = ["Il prezzo è in [B › p. 2]."]
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.open_collection()
    dlg.add_open()
    dlg.add_paths([b, b])                   # stesso documento due volte: una sola voce
    assert len(dlg.coll.members) == 2
    dlg.ask("Dov'è il prezzo?")
    sent = fake.all_text()
    assert "=== A › [p. 1] ===" in sent and "=== B › [p. 2] ===" in sent
    msg = dlg.conv.messages[-1]
    assert msg.refs[0]["verified"] and msg.refs[0]["page"] == 1
    from PySide6.QtCore import QUrl
    dlg.on_link(QUrl("page:1:0"))
    view = window.ctx.current_view()
    assert view.document.path.name == "B.pdf" and view.current_page == 1
    # salvataggio e riapertura
    dlg.save()
    from foliant.modules.ai.collection import CollectionStore
    loaded = CollectionStore().get(dlg.coll.id)
    assert loaded.conversations[-1].messages[-1].text == "Il prezzo è in [B › p. 2]."
    dlg2 = ready.controller.open_collection()
    dlg2.load(loaded)
    dlg2.on_link(QUrl("page:1:0"))
    assert window.ctx.current_view().current_page == 1


def test_collection_excludes_forbidden(window, ready, fake, text_pdf, tmp_path):
    ok = text_pdf(1, name="ok.pdf", texts=["Documento libero"])
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Segreto vietato", fontsize=12)
    bad = tmp_path / "vietato.pdf"
    doc.save(str(bad), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="p", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    fake.responses = ["ok"]
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.open_collection()
    dlg.add_paths([ok, str(bad)])
    assert dlg.coll.members[1].excluded
    assert "escluso" in dlg.list.item(1).text()
    dlg.ask("Domanda")
    assert "Segreto" not in fake.all_text() and "Documento libero" in fake.all_text()


# ------------------------------------------------------------------ AI-23 / AI-17
def test_command_proposal_validated_and_no_content_sent(window, ready, fake, text_pdf):
    view = window.open_path(text_pdf(10))
    fake.responses = [json.dumps({"proposals": [{"tool": "vai-a-pagina", "params": {"page": 7},
                                                 "explanation": "pagina richiesta"}]})]
    ready.controller.exec_dialog = lambda dlg: dlg.choose()
    ready.controller.open_commands("vai a pagina 7")
    assert view.current_page == 6
    sent = fake.all_text()
    assert "Contenuto unico" not in sent and "10 pages" in sent


def test_command_out_of_range_rejected(window, ready, fake, text_pdf):
    view = window.open_path(text_pdf(10))
    fake.responses = [json.dumps({"proposals": [{"tool": "vai-a-pagina", "params": {"page": "30-35"}}]})]
    ready.controller.exec_dialog = lambda dlg: dlg.choose()
    dlg = ready.controller.open_commands("ruota le pagine 30-35")
    assert dlg.chosen is None and dlg.proposals == []
    assert "non valide" in dlg.notice.text()
    assert view.current_page == 0


def test_command_catalog_contains_app_actions(window, ready):
    from foliant.modules.ai import commands
    cat = ready.controller.tool_catalog()
    assert any(t.id == "vai-a-pagina" for t in cat)
    ok, rej = commands.validate({"proposals": [{"tool": "inesistente"}]}, cat, 5)
    assert not ok and "inesistente" in rej[0]


def test_operations_dialog_lists_and_cancels(qtbot, window, ready, fake, sample_pdf):
    from foliant.modules.ai.ui import OperationsDialog
    window.open_path(sample_pdf)
    fake.responses = [{"chunks": ["a", "b", "c", "d"], "delay": 0.3}]
    panel = ready.panel
    panel.send("x")
    dlg = OperationsDialog(ready, window)
    assert "chat" in dlg.list.item(0).text()
    dlg.list.setCurrentRow(0)
    dlg.cancel_selected()
    wait_done(qtbot, panel)
    assert panel.conversation().messages[-1].status == "interrupted"


def test_shortcut_opens_assistant_mode(window, sample_pdf):
    window.open_path(sample_pdf)
    act = next(a for a in window.findChildren(__import__("PySide6.QtGui", fromlist=["QAction"]).QAction)
               if a.shortcut().toString() == "Ctrl+Alt+I")
    act.trigger()
    assert window.ctx.current_mode() == "ia"


# ------------------------------------------------------------------ AI-26 / AI-20 / AI-05 integrazione
def test_bibliography_dialog_disabled_without_service(window, ready):
    ready.controller.exec_dialog = lambda dlg: 0
    dlg = ready.controller.open_bibliography()
    assert dlg is not None
    assert "Nessun servizio bibliografico" in "".join(
        w.text() for w in dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))


@pytest.mark.parametrize("task_id", ["generic", "contract-summary", "invoice-check", "product-sheet",
                                     "customer-reply", "compare"])
def test_business_templates_build_instructions(task_id):
    task = next(t for t in tasks_library.BUILTIN if t.id == task_id)
    values = {p.name: (p.default or "valore di prova") for p in task.params}
    instr = tasks_library.build_instructions(task, values, Scope(), "it")
    assert "{" not in instr.replace("{{", "").replace("}}", "")
    assert "Italian" in instr


def test_product_sheet_runs_on_document(qtbot, window, ready, fake, sample_pdf):
    window.open_path(sample_pdf)
    fake.responses = ["## Scheda\n\n| Marca | da verificare |\n|---|---|"]
    task = next(t for t in tasks_library.BUILTIN if t.id == "product-sheet")
    ready.panel.run_template(task, {"prodotto": "orologio", "canale": "e-commerce"}, "document")
    wait_done(qtbot, ready.panel)
    req = fake.requests[0]
    assert "Do not invent data" in req.system and "Orologeria Verdi" in req.messages[-1].content


def test_summary_of_pasted_text(qtbot, window, ready, fake):
    from foliant.modules.ai.ui import GeneralChatDialog
    dlg = GeneralChatDialog(window.ctx, ready, window)
    p = dlg.panel
    p.set_function("summary")
    p.send("Testo incollato da riassumere con varie informazioni sulla vendita di orologi.")
    wait_done(qtbot, p)
    assert "Testo incollato da riassumere" in fake.requests[0].messages[-1].content


# ------------------------------------------------------------------ AI-25 / AI-27 integrazione
def test_generate_graphic_disabled_without_image_model(window, ready, sample_pdf):
    window.open_path(sample_pdf)
    seen = []
    ready.controller.exec_dialog = lambda dlg: seen.append(dlg) or 0
    dlg = ready.controller.generate_graphic()
    assert seen and not dlg.generate_button.isEnabled()


def test_convert_to_docx_with_heading(window, ready, fake, tmp_path):
    import zipfile
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 80), "Titolo del contratto", fontsize=20)
    p.insert_text((72, 130), "Testo del primo articolo del contratto.", fontsize=10)
    path = tmp_path / "c.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    fake.default = json.dumps({"blocks": [{"id": 0, "role": "heading1"}, {"id": 1, "role": "paragraph"}],
                               "tables": []})
    out = tmp_path / "c.docx"
    report = ready.controller.convert_to_docx(str(out))
    assert report["ai_used"]
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert "Heading1" in xml and "Titolo del contratto" in xml


def test_chat_shortcut_scoped_to_ai_mode(window, sample_pdf):
    from PySide6.QtGui import QKeySequence
    window.open_path(sample_pdf)
    act = window.ctx.mode("ia").toolbar.actions()[0]
    assert act.text() == "Chiedi"
    window.ctx.set_mode("leggi")
    assert act.shortcut().isEmpty()
    window.ctx.set_mode("ia")
    assert act.shortcut() == QKeySequence("Ctrl+Shift+A")


def test_customer_message_placeholders_highlighted(qtbot, window, ready, fake, sample_pdf):
    from foliant.modules.ai import render
    window.open_path(sample_pdf)
    fake.responses = ["Gentile [nome del cliente], la consegna è prevista [p. 1]."]
    task = next(t for t in tasks_library.BUILTIN if t.id == "customer-reply")
    ready.panel.run_template(task, {"argomento": "consegna", "tono": "cordiale"}, "document")
    wait_done(qtbot, ready.panel)
    msg = ready.panel.conversation().messages[-1]
    assert any("[nome del cliente]" in f for f in msg.flags)
    out = render.render_markdown(msg.text)
    assert out.placeholders == ["nome del cliente"] and "da completare" in out.html
    assert "placeholders in square brackets" in fake.requests[0].system
