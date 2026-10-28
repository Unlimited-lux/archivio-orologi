"""SEC-06 «Cifra tutto tranne i metadati» e SEC-11 verifica di integrità."""
from __future__ import annotations

import re
import zlib

import pymupdf
import pytest

from foliant.modules import security
from foliant.modules.security import controller as C
from foliant.modules.security import crypto as X
from foliant.modules.security import protection as P

OPEN_PW, PERM_PW = "Apri-2026!", "Capo-2026!"


class _FakeDialog:
    def __init__(self, settings):
        self._s = settings
        self.keep_open_password = False
        self.keep_permissions_password = False

    def settings(self):
        return self._s


@pytest.fixture
def sec(window):
    return security.CONTROLLER


def _source(tmp_path):
    path = tmp_path / "listino.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Listino riservato: Daytona 116500LN", fontsize=12)
    d.set_metadata({"title": "Listino rivenditori 2026", "author": "Unlimited"})
    d.save(path)
    return path


def _object_stream(data: bytes, xref: int) -> tuple[int, int]:
    """(inizio, fine) dei byte del flusso dell'oggetto `xref` nel file."""
    m = re.search(rb"(?:^|[\r\n])" + str(xref).encode() + rb"\s+0\s+obj\b", data)
    start = data.index(b"stream", m.end())
    start = data.index(b"\n", start) + 1
    return start, data.index(b"endstream", start)


def _xrefs(path, password):
    pdf = pymupdf.open(path)
    pdf.authenticate(password)
    meta = int(pdf.xref_get_key(pdf.pdf_catalog(), "Metadata")[1].split()[0])
    return meta, pdf[0].get_contents()[0]


def _protect(window, sec, tmp_path, monkeypatch, **kw):
    view = window.open_path(str(_source(tmp_path)))
    s = P.ProtectionSettings(open_password=OPEN_PW, permissions_password=PERM_PW, restrict=True,
                             allowed={"annotate"}, **kw)
    monkeypatch.setattr(C, "ask_protection", lambda parent, doc, **k: _FakeDialog(s))
    assert sec.protect()
    out = tmp_path / "protetto.pdf"
    view.document.save(out)
    return view, out


def test_plain_metadata_readable_contents_encrypted(window, sec, tmp_path, monkeypatch):
    """Con «Cifra tutto tranne i metadati», il flusso /Metadata si legge senza password
    (titolo visibile), i flussi delle pagine no."""
    view, out = _protect(window, sec, tmp_path, monkeypatch, encrypt_metadata=False)
    data = out.read_bytes()
    assert re.search(rb"/EncryptMetadata\s+false", data) and re.search(rb"/V\s*5", data) and re.search(rb"/R\s*6", data)
    meta, content = _xrefs(out, OPEN_PW)
    a, b = _object_stream(data, meta)
    assert b"Listino rivenditori 2026" in data[a:b]          # leggibile senza password
    a, b = _object_stream(data, content)
    raw = data[a:b]
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    assert b"BT" not in raw and b"Tj" not in raw and b"TJ" not in raw   # contenuto della pagina cifrato
    pdf = pymupdf.open(out)
    assert pdf.needs_pass and pdf.authenticate(OPEN_PW)
    assert "Daytona" in pdf[0].get_text() and pdf.metadata["title"] == "Listino rivenditori 2026"
    assert not pdf.permissions & pymupdf.PDF_PERM_COPY and pdf.permissions & pymupdf.PDF_PERM_ANNOTATE


def test_default_encrypts_metadata(window, sec, tmp_path, monkeypatch):
    _view, out = _protect(window, sec, tmp_path, monkeypatch)
    assert b"Listino rivenditori 2026" not in out.read_bytes()


def test_integrity_written_and_verified(window, sec, tmp_path, monkeypatch):
    """Prodotto con la verifica di integrità e riaperto: verifica superata, rendering uguale."""
    view, out = _protect(window, sec, tmp_path, monkeypatch, integrity=True)
    data = out.read_bytes()
    assert X.has_integrity(data) and b"/AuthCode" in data
    assert X.verify(data, [OPEN_PW]) == X.INTEGRITY_OK
    copy = tmp_path / "ricevuto.pdf"                 # il file salvato è ancora aperto: se ne apre una copia
    copy.write_bytes(data)
    reopened = window.open_path(str(copy), password=OPEN_PW)
    assert sec.integrity_results.get(reopened.document) == X.INTEGRITY_OK
    assert reopened.document.locked_reason is None
    ref = pymupdf.open(_source(tmp_path))[0].get_pixmap(dpi=72).samples
    assert reopened.document.pdf[0].get_pixmap(dpi=72).samples == ref


def test_tampered_file_warns_and_is_read_only(window, sec, tmp_path, monkeypatch):
    """Un byte alterato in un flusso cifrato: aprendo con la password corretta compare
    l'avviso di alterazione e il documento è in sola lettura."""
    _view, out = _protect(window, sec, tmp_path, monkeypatch, integrity=True)
    data = bytearray(out.read_bytes())
    _meta, content = _xrefs(out, OPEN_PW)
    a, b = _object_stream(bytes(data), content)
    data[(a + b) // 2] ^= 0x01                       # un byte del flusso cifrato della pagina
    bad = tmp_path / "alterato.pdf"
    bad.write_bytes(bytes(data))
    assert X.verify(bytes(data), [OPEN_PW]) == X.INTEGRITY_TAMPERED
    view = window.open_path(str(bad), password=OPEN_PW)
    assert sec.integrity_results.get(view.document) == X.INTEGRITY_TAMPERED
    assert any(b.endswith("integrity") for b in sec.ctx.banners(view))
    from foliant.core.errors import OperationCancelled
    with pytest.raises(OperationCancelled):
        with view.document.change("Prova") as pdf:
            pdf[0].insert_text((72, 300), "x")


def test_strategy_inactive_without_options(window, sec, sample_pdf):
    view = window.open_path(sample_pdf)
    assert view.document.save_strategy == sec.save_strategy
    assert sec.save_strategy(view.document, None) is None


def test_integrity_follows_protection_changes(window, sec, tmp_path, monkeypatch):
    view, _out = _protect(window, sec, tmp_path, monkeypatch, integrity=True)
    assert sec.has_integrity(view.document)
    P.remove_protection(view.document)
    assert not sec.has_integrity(view.document)
    assert sec.save_strategy(view.document, None) is None


def test_protect_dialog_options(window, sec, sample_pdf, qtbot):
    from foliant.modules.security import dialogs as D
    view = window.open_path(sample_pdf)
    i = P.info(view.document)
    dlg = D.ProtectDialog(P.settings_from_document(view.document), already_protected=i.protected,
                          has_open_password=i.has_open_password)
    qtbot.addWidget(dlg)
    dlg.plain_metadata.setChecked(True)
    assert "leggibili" in dlg.metadata_note.text()
    dlg.integrity.setChecked(True)
    s = dlg.settings()
    assert not s.encrypt_metadata and s.integrity


def test_policy_carries_options_and_batch(window, sec, tmp_path, monkeypatch):
    from foliant.modules.security import policies as POL
    s = P.ProtectionSettings(open_password=OPEN_PW, permissions_password=PERM_PW, restrict=True,
                             allowed={"annotate"}, encrypt_metadata=False, integrity=True)
    pol = POL.Policy.from_settings("Listini", s)
    again, _notes = POL.Policy.from_dict("Listini", pol.to_dict())
    assert not again.encrypt_metadata and again.integrity and "integrità" in again.describe()
    out = tmp_path / "lotto.pdf"
    POL.protect_file(str(_source(tmp_path)), str(out), again.settings(OPEN_PW, PERM_PW))
    data = out.read_bytes()
    assert X.verify(data, [OPEN_PW]) == X.INTEGRITY_OK and re.search(rb"/EncryptMetadata\s+false", data)
