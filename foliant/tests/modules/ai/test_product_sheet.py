"""Schede prodotto (AI-30)."""

from __future__ import annotations

import csv
import io
import json

from foliant.modules.ai import product_sheet

from .conftest import FakeProvider, make_active_profile


def _ready(window):
    fake = FakeProvider()
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    services.refresh()
    return services, fake


def test_specs_table_with_page_and_csv(window, text_pdf, tmp_path):
    services, fake = _ready(window)
    window.open_path(text_pdf(3, texts=["Aspirapolvere modello X", "Potenza 1200 W, peso 3 kg", "Garanzia"]))
    fake.responses = [json.dumps({"nome": "Modello X", "descrizione_breve": "Breve", "descrizione": "Lunga",
                                  "specifiche": [{"nome": "Potenza", "valore": 1200, "unita": "W", "pagina": 2},
                                                 {"nome": "Rumorosità", "valore": 70, "unita": "dB", "pagina": 2}]})]
    services.controller.exec_dialog = lambda dlg: 0
    dlg = services.controller.open_product_sheet()
    dlg.generate()
    s = dlg.sheet.specifiche[0]
    assert (s.nome, s.valore, s.unita, s.pagina, s.verified) == ("Potenza", 1200, "W", 1, True)
    assert dlg.sheet.specifiche[1].verified is False          # 70 dB non è nel documento
    assert dlg.specs.item(0, 3).text() == "2"
    out = tmp_path / "spec.csv"
    dlg.export_csv(str(out))
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"))))
    assert rows[0] == ["nome", "valore", "unità", "pagina"] and rows[1] == ["Potenza", "1200", "W", "2"]
    assert len(rows) == 3


def test_short_description_over_limit_flagged():
    from foliant.modules.ai.engine import Scope
    sheet = product_sheet.build_sheet({"nome": "X", "descrizione_breve": "a" * 350}, Scope())
    assert any("300" in w and "350" in w for w in sheet.warnings)


def _omega(window, text_pdf, services, fake):
    window.open_path(text_pdf(3, texts=["Perizia di un Omega Speedmaster Professional",
                                        "Cassa in acciaio da 42 mm, calibro 3861 a carica manuale, anno 2025.",
                                        "Conclusioni della perizia"]))
    fake.responses = [json.dumps({
        "nome": "Omega Speedmaster Professional", "descrizione_breve": "Cronografo", "descrizione": "Perizia",
        "specifiche": [{"nome": "Diametro della cassa", "valore": 42, "unita": "mm", "pagina": 2},
                       {"nome": "Calibro", "valore": 3861, "unita": "", "pagina": 2},
                       {"nome": "Anno", "valore": 2025, "unita": "", "pagina": 2},
                       {"nome": "Impermeabilità", "valore": 50, "unita": "m", "pagina": 2}]})]
    services.controller.exec_dialog = lambda dlg: 0
    dlg = services.controller.open_product_sheet()
    dlg.generate()
    return dlg


def test_verified_page_refs_are_not_marked_unverified(window, text_pdf):
    """Collaudo C-5: i riferimenti di pagina verificati sono collegamenti, senza «(non verificato)»."""
    services, fake = _ready(window)
    dlg = _omega(window, text_pdf, services, fake)
    specs = dlg.sheet.specifiche
    assert [(s.valore, s.pagina, s.verified) for s in specs] == [(42, 1, True), (3861, 1, True), (2025, 1, True),
                                                                 (50, 1, False)]
    html = dlg.view.toHtml()
    assert html.count("(non verificato)") == 1          # solo il valore 50 m, che non è nel testo
    assert html.count("page:") == 4


def test_page_ref_click_goes_to_page(window, text_pdf):
    from PySide6.QtCore import QUrl
    services, fake = _ready(window)
    dlg = _omega(window, text_pdf, services, fake)
    view = window.ctx.current_view()
    view.go_to_page(0)
    dlg.view.anchorClicked.emit(QUrl("page:2"))
    assert view.current_page == 1


def test_language_follows_interface(window, text_pdf):
    """In inglese la lingua proposta per la scheda è l'inglese, non l'italiano."""
    from foliant import i18n
    services, fake = _ready(window)
    window.open_path(text_pdf(1))
    services.controller.exec_dialog = lambda dlg: 0
    assert services.controller.open_product_sheet().language.currentData() == "it"
    i18n.set_language("en")
    try:
        dlg = services.controller.open_product_sheet()
        assert dlg.language.currentData() == "en" and dlg.language.currentText() == "English"
    finally:
        i18n.set_language("it")
