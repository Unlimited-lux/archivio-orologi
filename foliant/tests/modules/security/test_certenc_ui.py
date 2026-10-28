"""SEC-09 Cifratura con certificato: integrazione."""
from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules import security
from foliant.modules.security import certenc as CE
from foliant.modules.security import certenc_ui as CU
from foliant.modules.security import controller as C
from foliant.modules.security import protection as P
from tests.modules.security.test_certenc import PIN, _make


@pytest.fixture(scope="module")
def people(tmp_path_factory):
    d = tmp_path_factory.mktemp("cert_ui")
    return {n: _make(d, n) for n in ("Anna", "Bruno", "Carla")} | {"Scaduto": _make(d, "Scaduto", days=-1)}


@pytest.fixture
def sec(window, monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: (_ for _ in ()).throw(AssertionError("dialogo inatteso")))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return security.CONTROLLER


def _listino(tmp_path):
    path = tmp_path / "listino.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Listino rivenditori: Submariner 9.500", fontsize=12)
    d.save(path)
    return path


def test_dialog_recipients_and_permissions(people, qtbot, monkeypatch):
    dlg = CU.CertEncryptDialog()
    qtbot.addWidget(dlg)
    a = dlg.add_file(people["Anna"]["p12"], PIN)
    assert a is not None and a.mine and "(io)" in dlg.tree.topLevelItem(0).text(0)
    b = dlg.add_file(people["Bruno"]["pem"])
    assert b is not None and not b.mine
    assert dlg.add_file(people["Bruno"]["der"]) is None and "già" in dlg.error.text()
    dlg.print_combo.setCurrentIndex(dlg.print_combo.findData(P.PRINT_NONE))
    dlg.perm_boxes["copy"].setChecked(False)
    assert b.print_level == P.PRINT_NONE and "copy" not in b.allowed
    assert "non consentita" in dlg.tree.topLevelItem(1).text(3).lower()
    monkeypatch.setattr(CU, "confirm_problem", lambda *a: False)
    assert dlg.add_file(people["Scaduto"]["pem"]) is None
    monkeypatch.setattr(CU, "confirm_problem", lambda *a: True)
    assert dlg.add_file(people["Scaduto"]["pem"]) is not None
    dlg.remove_selected()
    assert [r.name for r in dlg.recipients] == ["Anna", "Bruno"]


def test_encrypt_then_open_with_each_certificate(window, sec, people, tmp_path, monkeypatch):
    """A con stampa consentita e B con stampa negata: la Stampa è disponibile solo per A; C
    non apre il file."""
    from foliant.modules.viewer.printing_engine import document_print_capability
    view = window.open_path(str(_listino(tmp_path)))
    a = CE.Recipient(CE.load_certificate(people["Anna"]["p12"], PIN)[0], mine=True)
    b = CE.Recipient(CE.load_certificate(people["Bruno"]["pem"])[0], allowed={"copy"}, print_level=P.PRINT_NONE)

    class _Dlg:
        recipients = [a, b]

        class encrypt_metadata:
            @staticmethod
            def isChecked():
                return True

    monkeypatch.setattr(C, "ask_cert_recipients", lambda parent: _Dlg)
    out = tmp_path / "listino_cifrato.pdf"
    monkeypatch.setattr(sec.ctx, "ask_save_path", lambda title, name: str(out))
    assert sec.encrypt_with_certificate()
    assert CE.is_pubkey(out.read_bytes()) and not view.document.is_modified
    va = sec.open_with_certificate(str(out), people["Anna"]["p12"], PIN)
    vb = sec.open_with_certificate(str(out), people["Bruno"]["p12"], PIN)
    assert "Submariner" in va.document.pdf[0].get_text()
    assert document_print_capability(va.document).allowed
    assert not document_print_capability(vb.document).allowed
    errors = []
    monkeypatch.setattr(sec.ctx, "error", lambda title, text: errors.append(text))
    assert sec.open_with_certificate(str(out), people["Carla"]["p12"], PIN) is None
    assert errors and "Destinatari previsti" in errors[0]


def test_warning_when_own_certificate_missing(window, sec, people, tmp_path, monkeypatch):
    """Senza il proprio certificato tra i destinatari compare l'avviso di perdita di accesso."""
    window.open_path(str(_listino(tmp_path)))
    b = CE.Recipient(CE.load_certificate(people["Bruno"]["pem"])[0])

    class _Dlg:
        recipients = [b]

        class encrypt_metadata:
            @staticmethod
            def isChecked():
                return True

    monkeypatch.setattr(C, "ask_cert_recipients", lambda parent: _Dlg)
    seen = []
    monkeypatch.setattr(C, "confirm", lambda parent, title, text, action: seen.append(text) or False)
    assert not sec.encrypt_with_certificate()
    assert seen and "non potrai più aprire" in seen[0]


def test_open_plain_pdf_with_certificate(window, sec, tmp_path, monkeypatch):
    infos = []
    monkeypatch.setattr(sec.ctx, "info", lambda title, text: infos.append(text))
    assert sec.open_with_certificate(str(_listino(tmp_path)), "x.p12", "") is None
    assert infos and "non è cifrato con certificato" in infos[0]


def test_save_guard_for_certificate_documents(window, sec, people, tmp_path, monkeypatch):
    a = CE.Recipient(CE.load_certificate(people["Anna"]["pem"])[0])
    out = tmp_path / "c.pdf"
    out.write_bytes(CE.encrypt_for(_listino(tmp_path).read_bytes(), [a]))
    view = sec.open_with_certificate(str(out), people["Anna"]["p12"], PIN)
    seen = []
    monkeypatch.setattr(C, "confirm", lambda parent, title, text, action: seen.append(title) or False)
    assert sec.cert_save_guard(view, {"path": None, "copy": False}) is False
    assert seen == ["Salvare senza la cifratura con certificato?"]
