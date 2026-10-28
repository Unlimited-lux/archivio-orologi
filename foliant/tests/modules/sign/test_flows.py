"""Flussi reali di un'azienda di compravendita di orologi, dall'inizio alla fine.

1. Contratto di vendita con due firmatari: il venditore prepara i campi, certifica (compilazione e firme
   consentite) bloccando prezzo e matricola, con marca temporale; l'acquirente compila le note nella finestra,
   salva e firma il proprio campo; poi una modifica al prezzo deve essere rilevata.
2. PDF firmati «da altri programmi»: firme prodotte direttamente con pyHanko con impostazioni diverse da quelle
   di Foliant (adbe.pkcs7.detached, marca temporale incorporata, marca temporale del documento).
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest

from foliant.modules.sign import digital, store

from .conftest import PASSWORD  # noqa: F401 - stessi ID di prova degli altri test


def _contract(path: Path) -> bytes:
    """Contratto di compravendita di un orologio con campi Prezzo, Matricola e Note."""
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 90), "Contratto di compravendita - Orologeria Verdi", fontsize=14)
        page.insert_text((72, 120), f"Pagina {n + 1} di 2", fontsize=10)
    page = doc[0]
    for i, (name, value) in enumerate([("Prezzo", "12.500,00 EUR"), ("Matricola", "A1234567"), ("Note", "")]):
        w = pymupdf.Widget()
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.field_value = value
        w.rect = pymupdf.Rect(200, 200 + i * 40, 450, 225 + i * 40)
        page.add_widget(w)
    doc.save(str(path))
    return path.read_bytes()


@pytest.fixture
def trusted(id_a, id_b, tsa):
    for cert in (id_a[1], id_b[1], tsa[1]):
        store.TrustStore().add(cert)
    return [id_a[1], id_b[1], tsa[1]]


def test_sale_contract_two_signers(window, monkeypatch, tmp_path, id_a, id_b, tsa, trusted):
    from foliant.modules.forms import engine as fe
    data = _contract(tmp_path / "contratto.pdf")
    # il venditore prepara i campi firma (obbligatori) e blocca prezzo e matricola con la propria firma
    data, _n = digital.add_signature_field(data, "Venditore", 1, (60, 600, 260, 680), required=True,
                                           tooltip="Firma del venditore", lock_fields=["Prezzo", "Matricola"],
                                           lock_action="Include")
    data, _n = digital.add_signature_field(data, "Acquirente", 1, (320, 600, 520, 680), required=True,
                                           tooltip="Firma dell'acquirente")
    seller = digital.sign_pdf(data, id_a[2], digital.SignOptions(
        field_name="Venditore", reason="Vendita orologio", location="Milano", certify=True, docmdp_level=2,
        timestamper=tsa[0]))
    contract = tmp_path / "contratto_venditore.pdf"
    contract.write_bytes(seller)
    [st] = [s for s in digital.verify_pdf(seller, trusted=trusted)]
    assert st.status == digital.VALID and st.kind == "certificazione" and st.pades_level == "B-T"
    assert [f.name for f in digital.signature_fields(seller) if not f.signed] == ["Acquirente"]
    fields = {f.name: f for f in digital.signature_fields(seller)}
    assert fields["Venditore"].signed and not fields["Acquirente"].locked

    # l'acquirente apre il contratto, compila le note (consentito: nessun avviso) e salva
    view = window.open_path(str(contract))
    c = window.ctx.sign_controller
    asked = []
    monkeypatch.setattr(c, "_ask_invalidation", lambda v, what: asked.append(what) or "cancel")
    with view.document.lock:
        note = next(w for w in view.document.pdf[0].widgets() if w.field_name == "Note")
    with view.document.change("Compila campo", kind="form") as pdf:
        fe.set_value(pdf, 0, note.xref, "Garanzia 24 mesi, scatola e documenti originali")
    assert asked == []
    assert window.save_view(view)
    filled = contract.read_bytes()
    assert filled.startswith(seller)
    [st] = digital.verify_pdf(filled, trusted=trusted)
    assert st.status == digital.VALID and "compilazione" in st.modifications

    # l'acquirente firma il proprio campo, anche lui con marca temporale
    final = digital.sign_pdf(filled, id_b[2], digital.SignOptions(
        field_name="Acquirente", reason="Acquisto orologio", location="Torino", timestamper=tsa[0]))
    statuses = digital.verify_pdf(final, trusted=trusted)
    assert [(s.field_name, s.status) for s in statuses] == [("Venditore", "valida"), ("Acquirente", "valida")]
    assert all(s.pades_level == "B-T" and s.time_source == "marca temporale" for s in statuses)
    assert statuses[0].signer == "Mario Rossi" and statuses[1].signer == "Lucia Bianchi"
    assert not [f for f in digital.signature_fields(final) if not f.signed]
    assert "Certificato da Mario Rossi" in digital.summary(statuses, []) and \
        "tutte le 2 firme sono valide" in digital.summary(statuses, [])

    # dopo le firme qualcuno cambia il prezzo: la firma del venditore (che lo blocca) non è più valida
    tampered = tmp_path / "manomesso.pdf"
    tampered.write_bytes(final)
    doc2 = pymupdf.open(str(tampered))
    page2 = doc2[0]
    w2 = next(w for w in page2.widgets() if w.field_name == "Prezzo")
    w2.field_value = "1.250,00 EUR"
    w2.update()
    doc2.saveIncr()
    doc2.close()
    bad = digital.verify_pdf(tampered.read_bytes(), trusted=trusted)
    seller_st = next(s for s in bad if s.field_name == "Venditore")
    assert seller_st.status == digital.INVALID, seller_st.reasons
    assert "campo bloccato modificato: «Prezzo»" in seller_st.modifications, seller_st.reasons


def _pyhanko_sign(data: bytes, did, *, subfilter, timestamper=None, field="Firma1"):
    """Firma con pyHanko «puro», come farebbe un altro programma."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    w = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
    meta = signers.PdfSignatureMetadata(field_name=field, subfilter=subfilter, reason="Firma esterna")
    out = io.BytesIO()
    signers.PdfSigner(meta, did.signer, timestamper=timestamper,
                      new_field_spec=fields.SigFieldSpec(field, box=(50, 50, 250, 100))).sign_pdf(w, output=out)
    return out.getvalue()


def test_foreign_pkcs7_detached_with_embedded_timestamp(pdf_bytes, id_a, tsa, trusted):
    from pyhanko.sign.fields import SigSeedSubFilter
    signed = _pyhanko_sign(pdf_bytes, id_a[2], subfilter=SigSeedSubFilter.ADOBE_PKCS7_DETACHED, timestamper=tsa[0])
    [st] = digital.verify_pdf(signed, trusted=trusted)
    assert st.status == digital.VALID, st.reasons
    assert st.time_source == "marca temporale" and st.timestamp_tsa == "TSA di prova"
    assert st.reason == "Firma esterna"


def test_foreign_signature_then_document_timestamp(pdf_bytes, id_a, tsa, trusted):
    """Firma PAdES seguita da una marca temporale del documento (/DocTimeStamp), come nei flussi LTA."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter
    signed = _pyhanko_sign(pdf_bytes, id_a[2], subfilter=SigSeedSubFilter.PADES)
    w = IncrementalPdfFileWriter(io.BytesIO(signed), strict=False)
    out = io.BytesIO()
    signers.PdfTimeStamper(tsa[0]).timestamp_pdf(w, md_algorithm="sha256", output=out)
    statuses = digital.verify_pdf(out.getvalue(), trusted=trusted)
    assert len(statuses) == 2
    sig, dts = statuses
    assert sig.status == digital.VALID, sig.reasons
    assert dts.kind == "marca temporale del documento" and dts.status == digital.VALID, dts.reasons


def test_foreign_signed_file_opens_in_window_and_annotation_keeps_valid(window, tmp_path, id_a, tsa, trusted,
                                                                          pdf_bytes):
    """Un PDF firmato da un altro programma si apre e un commento salvato non invalida la firma
    (salvataggio incrementale)."""
    from pyhanko.sign.fields import SigSeedSubFilter
    p = tmp_path / "esterno.pdf"
    p.write_bytes(_pyhanko_sign(pdf_bytes, id_a[2], subfilter=SigSeedSubFilter.ADOBE_PKCS7_DETACHED,
                                timestamper=tsa[0]))
    original = p.read_bytes()
    view = window.open_path(str(p))
    with view.document.change("Commento", kind="annotation") as pdf:
        pdf[0].add_text_annot((300, 300), "Verificato in negozio")
    assert window.save_view(view)
    data = p.read_bytes()
    assert data.startswith(original)
    [st] = digital.verify_pdf(data, trusted=trusted)
    assert st.status == digital.VALID and "annotazion" in st.modifications.lower()
