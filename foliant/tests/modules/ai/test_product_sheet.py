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
