"""AI-29: controlli locali delle fatture (somma righe con differenza, partita IVA e codice fiscale)."""

from foliant.modules.ai import extract
from foliant.modules.ai.engine import Scope


def _result(net, piva="01234567897"):
    raw = [{"numero": "7", "partita_iva": piva, "imponibile": net, "righe": [
        {"descrizione": "a", "importo": 100.0}, {"descrizione": "b", "importo": 50.0},
        {"descrizione": "c", "importo": 25.5}]}]
    return extract.build_result("invoice", raw, Scope())


def test_line_sum_check_passes_and_fails_with_difference():
    assert not [c for c in _result(175.5).checks if "somma" in c]
    failed = [c for c in _result(180.0).checks if "somma" in c]
    assert failed and "4,50" in failed[0]


def test_partita_iva_and_codice_fiscale_validation():
    assert extract.partita_iva_valid("01234567897")
    assert not extract.partita_iva_valid("01234567890")
    assert extract.codice_fiscale_valid("RSSMRA85T10A562S")
    assert not extract.codice_fiscale_valid("RSSMRA85T10A562X")
    bad = _result(175.5, piva="01234567890").checks
    assert any("non è formalmente valido" in c for c in bad)


XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2" versione="FPR12">
 <FatturaElettronicaHeader>
  <CedentePrestatore><DatiAnagrafici><IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>01234567897</IdCodice>
   </IdFiscaleIVA><Anagrafica><Denominazione>Orologeria Verdi</Denominazione></Anagrafica></DatiAnagrafici>
  </CedentePrestatore>
  <CessionarioCommittente><DatiAnagrafici><Anagrafica><Denominazione>Cliente Srl</Denominazione></Anagrafica>
  </DatiAnagrafici></CessionarioCommittente>
 </FatturaElettronicaHeader>
 <FatturaElettronicaBody>
  <DatiGenerali><DatiGeneraliDocumento><Divisa>EUR</Divisa><Data>2026-03-01</Data><Numero>42</Numero>
   <ImportoTotaleDocumento>122.00</ImportoTotaleDocumento></DatiGeneraliDocumento></DatiGenerali>
  <DatiBeniServizi>
   <DettaglioLinee><Descrizione>Cinturino</Descrizione><Quantita>1.00</Quantita><PrezzoUnitario>100.00</PrezzoUnitario>
    <PrezzoTotale>100.00</PrezzoTotale><AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>
   <DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA><ImponibileImporto>100.00</ImponibileImporto><Imposta>22.00</Imposta>
   </DatiRiepilogo>
  </DatiBeniServizi>
 </FatturaElettronicaBody>
</p:FatturaElettronica>"""


def test_embedded_einvoice_read_locally(window, tmp_path):
    import pymupdf
    from .conftest import FakeProvider, make_active_profile
    fake = FakeProvider()
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Fattura n. 42", fontsize=12)
    doc.embfile_add("fattura.xml", XML)
    path = tmp_path / "ibrida.pdf"
    doc.save(str(path))
    window.open_path(str(path))
    services.controller.exec_dialog = lambda dlg: 0
    dlg = services.controller.extract_data("invoice")
    rec = dlg.result.records[0]
    assert rec["numero"].value == "42" and rec["totale"].value == 122.0
    assert len(rec["righe"]) == 1 and not dlg.result.checks
    assert fake.requests == []


def test_three_invoice_files_csv_with_source(window, tmp_path, monkeypatch):
    import csv
    import io
    import json

    import pymupdf
    from .conftest import FakeProvider, make_active_profile
    fake = FakeProvider()
    services = window.ctx.ai_services
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    paths = []
    for n in range(3):
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), f"Fattura {n + 1} totale 10{n},00", fontsize=12)
        path = tmp_path / f"fattura-cliente-{n + 1}.pdf"
        doc.save(str(path))
        paths.append(str(path))
    fake.default = lambda req: json.dumps({"records": [{"numero": req.messages[-1].content.split("Fattura ")[1][0],
                                                        "totale": "100,00", "_page": 1}]})
    services.controller.exec_dialog = lambda dlg: 0
    notes = []
    monkeypatch.setattr(window.ctx, "notify",
                        lambda text, actions=(), duration_ms=None: notes.append((text, list(actions))))
    task = services.controller.extract_invoice_files(paths)     # attività non modale
    assert task.wait() and task.error is None
    assert "3 fatture su 3" in notes[-1][0]
    notes[-1][1][0][1]()                                          # «Mostra» dall'avviso
    dlg = services.controller.last_dialog
    data = list(csv.reader(io.StringIO(extract.to_csv_summary(dlg.result))))
    assert data[0][0] == "file" and len(data) == 4
    assert [r[0] for r in data[1:]] == [f"fattura-cliente-{n + 1}.pdf" for n in range(3)]
    assert "fattura-cliente" not in fake.all_text()
