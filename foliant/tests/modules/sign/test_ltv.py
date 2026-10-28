"""SIG-22: livelli B-LT e B-LTA, «Aggiungi informazioni di verifica», «Aggiungi marca temporale al documento».

Usa la CA di prova con responder OCSP locale di `test_revocation` e la TSA fittizia di conftest.
"""

from __future__ import annotations

import io

from pyhanko.pdf_utils.reader import PdfFileReader

from foliant.modules.sign import digital

from .test_revocation import pki  # noqa: F401 - fixture


def _dss(data: bytes) -> dict:
    root = PdfFileReader(io.BytesIO(data), strict=False).root
    if "/DSS" not in root:
        return {}
    dss = root["/DSS"]
    return {k: len(dss[k]) for k in ("/Certs", "/OCSPs", "/CRLs", "/VRI") if k in dss}


def test_b_lta_verifies_offline(pki, pdf_bytes, tsa):  # noqa: F811
    resp, did, ca = pki
    out, warnings = digital.sign_pdf_ex(pdf_bytes, did, digital.SignOptions(
        reason="Archivio contratti", timestamper=tsa[0], pades_level="B-LTA", validation_roots=[ca, tsa[1]]))
    assert warnings == []
    dss = _dss(out)
    assert dss.get("/OCSPs", 0) >= 1 and dss.get("/Certs", 0) >= 2
    resp.server.shutdown()                                   # verifica senza rete
    resp.server.server_close()
    statuses = digital.verify_pdf(out, trusted=[ca, tsa[1]], allow_fetching=False, revocation_mode="mai")
    sig = statuses[0]
    assert sig.status == digital.VALID, sig.reasons
    assert sig.pades_level == "B-LTA"
    assert statuses[-1].kind == "marca temporale del documento" and statuses[-1].status == digital.VALID


def test_add_validation_info_to_b_b(pki, pdf_bytes):  # noqa: F811
    resp, did, ca = pki
    signed = digital.sign_pdf(pdf_bytes, did, digital.SignOptions(reason="Vendita"))
    assert _dss(signed) == {}
    out, warnings = digital.add_validation_info_pdf(signed, trusted=[ca])
    assert warnings == [] and out.startswith(signed)
    dss = _dss(out)
    assert dss.get("/OCSPs", 0) >= 1 and dss.get("/Certs", 0) >= 2
    [st] = digital.verify_pdf(out, trusted=[ca])
    assert st.status == digital.VALID, st.reasons


def test_lta_without_document_timestamp_stays_b_lt(pki, pdf_bytes, tsa):  # noqa: F811
    """Server irraggiungibile per la marca di documento: resta B-LT, con avviso."""
    resp, did, ca = pki
    calls = {"n": 0}
    real = tsa[0]

    class Flaky(type(real)):
        def __init__(self):
            self.__dict__.update(real.__dict__)

        async def async_timestamp(self, *a, **k):
            calls["n"] += 1
            if calls["n"] > 1:
                from pyhanko.sign.timestamps.common_utils import TimestampRequestError
                raise TimestampRequestError("server non raggiungibile")
            return await real.async_timestamp(*a, **k)

    out, warnings = digital.sign_pdf_ex(pdf_bytes, did, digital.SignOptions(
        timestamper=Flaky(), pades_level="B-LTA", validation_roots=[ca, tsa[1]]))
    assert any("B-LT" in w for w in warnings), warnings
    statuses = digital.verify_pdf(out, trusted=[ca, tsa[1]])
    assert len(statuses) == 1 and statuses[0].pades_level == "B-LT"


def test_revocation_unavailable_reaches_b_t_with_reason(pki, pdf_bytes, tsa):  # noqa: F811
    resp, did, ca = pki
    resp.server.shutdown()
    resp.server.server_close()
    out, warnings = digital.sign_pdf_ex(pdf_bytes, did, digital.SignOptions(
        timestamper=tsa[0], pades_level="B-LT", validation_roots=[ca, tsa[1]]))
    assert any("Dati di verifica non ottenuti" in w for w in warnings) and "Livello raggiunto: B-T." in warnings
    assert digital.verify_pdf(out, trusted=[ca, tsa[1]])[0].pades_level == "B-T"


def test_add_document_timestamp_later(pdf_bytes, id_a, tsa):
    signed = digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(timestamper=tsa[0]))
    out = digital.add_document_timestamp(signed, tsa[0], trusted=[tsa[1]])
    assert out.startswith(signed)
    statuses = digital.verify_pdf(out, trusted=[id_a[1], tsa[1]])
    assert [s.kind for s in statuses] == ["approvazione", "marca temporale del documento"]
    assert all(s.status == digital.VALID for s in statuses), [s.reasons for s in statuses]


def test_sign_dialog_pades_level(window, qtbot):
    """B-B senza TSA (altri livelli disattivati); con una TSA B-T predefinito, B-LT/B-LTA selezionabili."""
    from foliant.modules.sign import store
    from foliant.modules.sign.ui_digital import DigitalSignDialog
    c = window.ctx.sign_controller
    dlg = DigitalSignDialog(window, c)
    qtbot.addWidget(dlg)
    assert dlg.tsa.currentData() is None and dlg.pades.currentData() == "B-B"
    assert not dlg.pades.model().item(3).isEnabled()
    store.TSAConfig().add("TSA", "https://tsa.example.org")
    dlg2 = DigitalSignDialog(window, c)
    qtbot.addWidget(dlg2)
    assert dlg2.pades.currentData() == "B-T" and dlg2.pades.model().item(3).isEnabled()
    dlg2.tsa.setCurrentIndex(0)
    assert dlg2.pades.currentData() == "B-B"


def test_panel_long_term_actions(window, monkeypatch, tmp_path, pdf_bytes, id_a, tsa):
    from foliant.modules.sign import store
    store.TrustStore().add(id_a[1])
    store.TrustStore().add(tsa[1])
    store.TSAConfig().add("TSA", "http://127.0.0.1:9/")
    p = tmp_path / "vendita.pdf"
    p.write_bytes(digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(timestamper=tsa[0])))
    before = p.read_bytes()
    view = window.open_path(str(p))
    c = window.ctx.sign_controller
    monkeypatch.setattr(window.ctx, "info", lambda *a: None)
    assert c.add_long_term(view, "dss")
    after_dss = p.read_bytes()
    assert after_dss.startswith(before) and _dss(after_dss).get("/Certs", 0) >= 1
    real = digital.add_document_timestamp
    monkeypatch.setattr(digital, "add_document_timestamp", lambda data, _t, **k: real(data, tsa[0], **k))
    assert c.add_long_term(window.current_view(), "ts")
    final = p.read_bytes()
    assert final.startswith(after_dss)
    statuses = digital.verify_pdf(final, trusted=[id_a[1], tsa[1]])
    assert [s.status for s in statuses] == ["valida", "valida"] and statuses[0].pades_level == "B-LTA"


def test_archive_timestamp_renewal_notice(monkeypatch, pdf_bytes, id_a, tsa):
    signed = digital.sign_pdf(pdf_bytes, id_a[2], digital.SignOptions(timestamper=tsa[0]))
    out = digital.add_document_timestamp(signed, tsa[0], trusted=[tsa[1]])
    statuses = digital.verify_pdf(out, trusted=[id_a[1], tsa[1]])
    assert not any("scade il" in r for r in statuses[-1].reasons)
    monkeypatch.setattr(digital, "ARCHIVE_RENEWAL_DAYS", 400)     # il certificato della TSA di prova dura un anno
    statuses = digital.verify_pdf(out, trusted=[id_a[1], tsa[1]])
    assert any("nuova marca temporale" in r for r in statuses[-1].reasons)
