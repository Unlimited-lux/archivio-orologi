"""ANN-01: i commenti su un PDF firmato lasciano valida la firma (salvataggio incrementale del
nucleo) e, con il modulo Firma caricato, non fanno comparire l'avviso SIG-21."""

import pymupdf
import pytest
from PySide6.QtWidgets import QMessageBox

from foliant.modules.annotations import engine, ui_actions

digital = pytest.importorskip("foliant.modules.sign.digital")


def _signed_pdf(tmp_path) -> tuple:
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
    return path, signed


@pytest.fixture
def asked(comments, monkeypatch):
    """Registra ogni domanda (conferma del contesto o finestra di avviso) e la annulla."""
    window = comments[0]
    questions = []
    monkeypatch.setattr(window.ctx, "confirm", lambda t, m: questions.append(t) or False)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: questions.append(box.windowTitle()) or 0)
    return questions


def test_comment_keeps_signature_valid(comments, asked, tmp_path):
    window, view, module = comments
    path, signed = _signed_pdf(tmp_path)
    v = window.open_path(str(path))
    assert module.ctrl.apply("n", engine.create_note, 0, (300, 300), "Controllare", engine.Style(), "A",
                             view=v)
    assert asked == []                                              # nessun avviso SIG-21
    v.document.save()
    data = path.read_bytes()
    assert data.startswith(signed) and len(data) > len(signed)     # modifiche in coda
    statuses = digital.verify_pdf(data)
    assert statuses and all(s.intact and s.crypto_ok for s in statuses)
    reopened = pymupdf.open(stream=data, filetype="pdf")
    p = reopened[0]
    assert any(a.info.get("content") == "Controllare" for a in p.annots())


def test_comment_tools_on_signed_pdf_ask_nothing(comments, asked, tmp_path):
    """Nota, evidenziazione, modifica, gomma ed eliminazione passano; l'appiattimento (contenuto) chiede."""
    window, view, module = comments
    path, _signed = _signed_pdf(tmp_path)
    v = window.open_path(str(path))
    ctrl = module.ctrl
    note = ctrl.apply("n", engine.create_note, 0, (300, 300), "x", engine.Style(), "A", view=v)
    ink = ctrl.apply("i", engine.create_ink, 0, [[(100, 400), (200, 420), (300, 400)]], engine.Style(), "A",
                     view=v)
    assert note and ink
    ctrl.select([(0, note)], v)
    ctrl.set_style(color=(0.0, 0.5, 1.0))
    ctrl.delete()
    assert module.tools["eraser"].erase(v, 0, [pymupdf.Point(200, 420)]) >= 1
    assert asked == [], asked
    assert module.messages == []
    before = v.document.pdf[0].first_annot is not None
    ctrl.select([], v)
    ui_actions.flatten(ctrl)
    assert asked, "l'appiattimento cambia il contenuto: il modulo Firma deve chiedere"
    assert (v.document.pdf[0].first_annot is not None) == before   # annullato: nulla appiattito
    assert not [m for m in module.messages if m[0] == "error"]
