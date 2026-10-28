"""SEC-10 Cifratura dei soli allegati (busta di sicurezza)."""
from __future__ import annotations

import hashlib
import io
import re

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules import security
from foliant.modules.security import controller as C
from foliant.modules.security import envelope as E

PW = "Busta-Cliente-2026"
CONTRATTO = b"%PDF-1.7 contratto di compravendita Rolex Daytona " * 200
DOCUMENTO = bytes(range(256)) * 40          # p. es. la scansione di un documento d'identità


def _with_attachments() -> bytes:
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Documenti per la pratica 2026/118", fontsize=14)
    d.embfile_add("contratto.pdf", CONTRATTO, filename="contratto.pdf", desc="Contratto firmato")
    d.embfile_add("documento.jpg", DOCUMENTO, filename="documento.jpg")
    return d.tobytes()


def _reader(data: bytes):
    from pyhanko.pdf_utils.reader import PdfFileReader
    return PdfFileReader(io.BytesIO(data))


def _first_stream(obj):
    obj = obj.get_object()
    if not hasattr(obj, "data"):
        obj = obj[0].get_object()
    return obj


def test_pages_readable_without_password_attachments_need_it():
    """Dato un PDF con due allegati cifrato con «Cifra solo gli allegati», quando lo si apre
    senza password, allora le pagine sono leggibili e il testo estraibile, mentre gli
    allegati richiedono la password."""
    out = E.encrypt_attachments(_with_attachments(), PW)
    enc = re.search(rb"/Filter\s*/Standard.*?>>\s*>>", out, re.S).group(0)
    assert re.search(rb"/StmF\s*/Identity", enc) and re.search(rb"/StrF\s*/Identity", enc)
    assert re.search(rb"/EFF\s*/FolEFF", enc) and re.search(rb"/AuthEvent\s*/EFOpen", enc)
    assert re.search(rb"/CFM\s*/AESV3", enc) and re.search(rb"/V\s*5", enc) and re.search(rb"/R\s*6", enc)
    # lettore indipendente, senza password: la pagina è in chiaro
    r = _reader(out)
    page = r.root["/Pages"]["/Kids"][0].get_object()
    content = _first_stream(page["/Contents"]).data
    assert b"Tj" in content or b"TJ" in content
    assert "Documenti per la pratica".encode().hex().encode() in content     # testo in chiaro (esadecimale)
    # i byte dell'allegato non compaiono in chiaro nel file
    assert CONTRATTO[:60] not in out and DOCUMENTO[:256] not in out
    # i nomi restano visibili senza password (avviso nel riepilogo)
    names = r.root["/Names"]["/EmbeddedFiles"]["/Names"]
    assert {str(names[i]) for i in range(0, len(names), 2)} == {"contratto.pdf", "documento.jpg"}


def test_attachment_hash_identical_with_password():
    """Estraendo un allegato con la password corretta, i byte coincidono (hash identico)."""
    out = E.encrypt_attachments(_with_attachments(), PW)
    pdf = pymupdf.open("pdf", out)
    assert pdf.needs_pass                       # limite del motore: chiede la password anche per le pagine
    assert not pdf.authenticate("sbagliata")
    assert pdf.authenticate(PW)
    for name, original in (("contratto.pdf", CONTRATTO), ("documento.jpg", DOCUMENTO)):
        assert hashlib.sha256(pdf.embfile_get(name)).digest() == hashlib.sha256(original).digest()
    assert "Documenti per la pratica" in pdf[0].get_text()
    assert E.is_envelope(pdf)


def test_cover_document_lists_files(tmp_path):
    a = tmp_path / "Perizia Submariner.pdf"
    a.write_bytes(CONTRATTO)
    b = tmp_path / "ricevuta.txt"
    b.write_bytes(b"ricevuta 9.500 EUR")
    cover = E.cover_document([str(a), str(b)])
    text = cover[0].get_text()
    assert "Busta di sicurezza" in text and "Perizia Submariner.pdf" in text and "ricevuta.txt" in text
    assert sorted(cover.embfile_names()) == ["Perizia Submariner.pdf", "ricevuta.txt"]
    out = E.encrypt_attachments(cover.tobytes(), PW)
    pdf = pymupdf.open("pdf", out)
    assert pdf.authenticate(PW) and pdf.embfile_get("ricevuta.txt") == b"ricevuta 9.500 EUR"


def test_empty_password_refused():
    with pytest.raises(ValueError):
        E.encrypt_attachments(_with_attachments(), "")


@pytest.fixture
def sec(window, monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: (_ for _ in ()).throw(AssertionError("dialogo inatteso")))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return security.CONTROLLER


def test_save_envelope_flow(window, sec, tmp_path, monkeypatch):
    src = tmp_path / "pratica.pdf"
    src.write_bytes(_with_attachments())
    view = window.open_path(str(src))
    security._update_actions(sec, view)
    assert sec.actions["envelope"].isEnabled()
    out = tmp_path / "pratica_busta.pdf"
    monkeypatch.setattr(C, "ask_envelope_password", lambda parent: PW)
    monkeypatch.setattr(sec.ctx, "ask_save_path", lambda title, name: str(out))
    assert sec.save_envelope()
    pdf = pymupdf.open(out)
    assert pdf.authenticate(PW) and pdf.embfile_get("contratto.pdf") == CONTRATTO
    # il documento aperto non cambia
    assert not view.document.is_modified and not view.document.is_protected


def test_envelope_action_disabled_without_attachments(window, sec, sample_pdf, monkeypatch):
    view = window.open_path(sample_pdf)
    security._update_actions(sec, view)
    assert not sec.actions["envelope"].isEnabled() and sec.actions["new_envelope"].isEnabled()
    seen = []
    monkeypatch.setattr(sec.ctx, "info", lambda title, text: seen.append(text))
    assert not sec.save_envelope()
    assert seen and "Crea busta di sicurezza" in seen[0]


def test_create_envelope_flow(window, sec, tmp_path, monkeypatch):
    f = tmp_path / "carta_identita.jpg"
    f.write_bytes(DOCUMENTO)
    out = tmp_path / "busta.pdf"
    monkeypatch.setattr(C, "ask_envelope_password", lambda parent: PW)
    monkeypatch.setattr(sec.ctx, "ask_save_path", lambda title, name: str(out))
    assert sec.create_envelope([str(f)])
    pdf = pymupdf.open(out)
    assert pdf.authenticate(PW) and pdf.embfile_get("carta_identita.jpg") == DOCUMENTO


def test_envelope_row_in_security_summary(window, sec, tmp_path, monkeypatch):
    path = tmp_path / "busta.pdf"
    path.write_bytes(E.encrypt_attachments(_with_attachments(), PW))
    from foliant.core.document import Document
    doc = Document.open(path, password=PW) if "password" in Document.open.__code__.co_varnames else None
    if doc is None:
        pytest.skip("apertura con password non disponibile")
    rows = C.security_rows(doc)
    assert any("solo gli allegati" in head for head, _d in rows)
    doc.close()


def test_password_pair_dialog(qtbot):
    from foliant.modules.security import dialogs as D
    dlg = D.PasswordPairDialog("t", "")
    qtbot.addWidget(dlg)
    assert dlg.problem()
    dlg.pw.setText(PW)
    dlg.pw2.setText("x")
    assert "coincidono" in dlg.problem()
    dlg.pw2.setText(PW)
    assert dlg.problem() == ""
