"""ANN-01: un commento su un PDF firmato lascia valida la firma (salvataggio incrementale del nucleo)."""

import pymupdf
import pytest

from foliant.modules.annotations import engine

digital = pytest.importorskip("foliant.modules.sign.digital")


def test_comment_keeps_signature_valid(comments, tmp_path):
    window, view, module = comments
    old = digital.PKCS12_KDF_ROUNDS
    digital.PKCS12_KDF_ROUNDS = 2048
    try:
        digital.create_self_signed(tmp_path / "id.p12", "segreta-123", common_name="Maria Rossi")
    finally:
        digital.PKCS12_KDF_ROUNDS = old
    dig = digital.load_pkcs12(tmp_path / "id.p12", "segreta-123")
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Contratto di vendita", fontsize=14)
    signed = digital.sign_pdf(src.tobytes(), dig, digital.SignOptions(reason="Prova"))
    path = tmp_path / "firmato.pdf"
    path.write_bytes(signed)
    v = window.open_path(str(path))
    assert module.ctrl.apply("n", engine.create_note, 0, (300, 300), "Controllare", engine.Style(), "A",
                             view=v)
    v.document.save()
    data = path.read_bytes()
    assert data.startswith(signed) and len(data) > len(signed)     # modifiche in coda
    statuses = digital.verify_pdf(data)
    assert statuses and all(s.intact and s.crypto_ok for s in statuses)
    reopened = pymupdf.open(stream=data, filetype="pdf")
    p = reopened[0]
    assert any(a.info.get("content") == "Controllare" for a in p.annots())
