"""Redazione e rimozione dei dati nascosti su PDF firmati: riscrittura completa (NFR-09)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.security import controller as C
from foliant.modules.security import redaction as R
from tests.test_document import _sign, test_signer  # noqa: F401  (fixture del firmatario)

SECRET = "CONFIDENZIALE-4711"


@pytest.fixture(autouse=True)
def proceed_on_signed(monkeypatch):
    """Il modulo Firma chiede conferma prima di modificare un PDF firmato: «Procedi comunque»."""
    try:
        from foliant.modules.sign import controller as SC
    except ImportError:
        return
    monkeypatch.setattr(SC.SignController, "_ask_invalidation", lambda self, view, what: "proceed")


def _signed(tmp_path, signer, name="firmato.pdf"):
    d = pymupdf.open()
    for i in range(2):
        p = d.new_page()
        p.insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
        p.insert_text((72, 140), f"Codice {SECRET} riservato", fontsize=12)
    path = tmp_path / name
    path.write_bytes(_sign(d.tobytes(), signer))
    return path


def _leaks(data: bytes) -> bool:
    """Il segreto compare nei byte o in un qualunque flusso decompresso del file."""
    if SECRET.encode() in data:
        return True
    pdf = pymupdf.open(stream=data, filetype="pdf")
    try:
        for xref in range(1, pdf.xref_length()):
            try:
                if pdf.xref_is_stream(xref) and SECRET.encode() in (pdf.xref_stream(xref) or b""):
                    return True
                if SECRET in pdf.xref_object(xref):
                    return True
            except Exception:
                continue
        return any(SECRET in page.get_text() for page in pdf)
    finally:
        pdf.close()


def _mark_secret(view):
    with view.document.change("Segna") as pdf:
        for i in range(2):
            for r in pdf[i].search_for(SECRET):
                R.add_mark(pdf, i, r, R.MarkStyle())


@pytest.mark.parametrize("target", ["save", "copy"])
def test_redaction_on_signed_pdf_rewrites_file(window, tmp_path, test_signer, monkeypatch, target):  # noqa: F811
    from foliant.modules import security
    sec = security.CONTROLLER
    path = _signed(tmp_path, test_signer)
    signed = path.read_bytes()
    view = window.open_path(str(path))
    assert view.document.has_signatures
    _mark_secret(view)
    warned = []
    monkeypatch.setattr(C, "confirm", lambda parent, title, text, action: warned.append(text) or True)
    copy = tmp_path / "firmato_redatto.pdf"
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: target == "copy")
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(copy))
    assert sec.apply_redactions(None)
    assert any("firme digitali" in t for t in warned)          # avviso di invalidazione
    assert not view.document.saves_incrementally
    if target == "save":
        # salvataggio del nucleo (il modulo Firma sostituisce doc.save sui file firmati:
        # vedi test_integrated_save_after_redaction_is_full_rewrite)
        Document.save(view.document)
        out = path.read_bytes()
    else:
        out = copy.read_bytes()
    assert not out.startswith(signed)                           # nessuna revisione firmata conservata
    assert not _leaks(out)
    assert b"/Prev" not in out


def test_sanitize_on_signed_pdf_rewrites_file(window, tmp_path, test_signer, monkeypatch):  # noqa: F811
    from foliant.modules import security
    sec = security.CONTROLLER
    path = _signed(tmp_path, test_signer)
    signed = path.read_bytes()
    view = window.open_path(str(path))
    with view.document.change("Metadati") as pdf:
        pdf.set_metadata({"author": SECRET})
    monkeypatch.setattr(C, "run_dialog", lambda dlg: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda t, m: errors.append(m))
    ok = sec.sanitize()
    assert ok, errors
    Document.save(view.document)
    out = path.read_bytes()
    assert not out.startswith(signed) and SECRET.encode() not in out
    assert not (pymupdf.open(path).metadata or {}).get("author")


def test_undo_redaction_returns_incremental(window, tmp_path, test_signer, monkeypatch):  # noqa: F811
    from foliant.modules import security
    sec = security.CONTROLLER
    path = _signed(tmp_path, test_signer)
    view = window.open_path(str(path))
    _mark_secret(view)
    monkeypatch.setattr(C, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    assert not view.document.saves_incrementally
    view.document.undo()
    assert view.document.saves_incrementally


def test_integrated_save_after_redaction_is_full_rewrite(window, tmp_path, test_signer, monkeypatch):  # noqa: F811
    """«Salva» dall'interfaccia con tutti i moduli: il modulo Firma avvisa che la riscrittura
    invalida le firme e propone «Salva una copia»; la copia non contiene il testo redatto."""
    from foliant.modules import security
    from foliant.modules.sign import controller as SC
    sec = security.CONTROLLER
    path = _signed(tmp_path, test_signer)
    signed = path.read_bytes()
    view = window.open_path(str(path))
    _mark_secret(view)
    monkeypatch.setattr(C, "confirm", lambda *a: True)
    monkeypatch.setattr(window.ctx, "confirm", lambda *a: False)
    assert sec.apply_redactions(None)
    asked = []
    monkeypatch.setattr(SC.SignController, "_ask_full_rewrite",
                        lambda self, v, save_as: asked.append(save_as) or "copy")
    copy = tmp_path / "copia.pdf"
    monkeypatch.setattr(window.ctx, "ask_save_path", lambda *a, **k: str(copy))
    window.save_view(view)                   # «Salva» come dall'interfaccia
    assert asked == [False]
    assert path.read_bytes() == signed       # il file firmato resta intatto
    out = copy.read_bytes()
    assert not out.startswith(signed) and not _leaks(out) and b"/Prev" not in out
