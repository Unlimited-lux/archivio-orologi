"""SEC-12 Policy di protezione riutilizzabili."""
from __future__ import annotations

import json

import pymupdf
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from foliant.modules import security
from foliant.modules.security import controller as C
from foliant.modules.security import dialogs as D
from foliant.modules.security import policies as POL
from foliant.modules.security import protection as P

OPEN_PW, PERM_PW = "Apri-Listino-2026!", "Capo-Listino-2026!"


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


@pytest.fixture
def sec(window, monkeypatch):
    c = security.CONTROLLER
    store = _Settings()
    monkeypatch.setattr(type(c), "settings", lambda self: store)
    monkeypatch.setattr(QDialog, "exec", lambda self: (_ for _ in ()).throw(AssertionError("dialogo inatteso")))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return c


def _riservato() -> POL.Policy:
    allowed = set(P.PERM_BITS) - {"copy"}
    return POL.Policy("Riservato", open_password=True, restrict=True, allowed=allowed, print_level=P.PRINT_LOW)


def _encrypt_keys(path) -> tuple[str, str, str]:
    import re
    data = open(path, "rb").read()
    enc = re.search(rb"/Filter\s*/Standard.*?>>", data, re.S).group(0)
    return tuple(re.search(rb"/" + k.encode() + rb"\s*(-?\d+)", enc).group(1).decode() for k in ("V", "R", "P"))


def test_policy_matches_manual_protection(window, sec, sample_pdf, tmp_path, monkeypatch):
    """Dato «Riservato» (AES-256, stampa a bassa risoluzione, copia negata), quando viene
    applicata, allora /V, /R e /P coincidono con quelli di una protezione manuale."""
    sec.policy_store().add(_riservato())
    view = window.open_path(sample_pdf)
    monkeypatch.setattr(C, "ask_policy_passwords", lambda parent, policy: (OPEN_PW, PERM_PW))
    assert sec.apply_policy("Riservato")
    by_policy = tmp_path / "policy.pdf"
    view.document.save(by_policy)
    manual = tmp_path / "manuale.pdf"
    settings = P.ProtectionSettings(open_password=OPEN_PW, permissions_password=PERM_PW, restrict=True,
                                    allowed=set(P.PERM_BITS) - {"copy"}, print_level=P.PRINT_LOW)
    src = pymupdf.open(sample_pdf)
    src.save(manual, **settings.save_options())
    assert _encrypt_keys(by_policy) == _encrypt_keys(manual)
    v, r, p = _encrypt_keys(by_policy)
    assert (v, r) == ("5", "6")
    pdf = pymupdf.open(by_policy)
    assert pdf.needs_pass and pdf.authenticate(OPEN_PW)
    assert not pdf.permissions & pymupdf.PDF_PERM_COPY and pdf.permissions & pymupdf.PDF_PERM_PRINT
    assert not pdf.permissions & pymupdf.PDF_PERM_PRINT_HQ


def test_saved_from_protect_dialog_without_passwords(window, sec, sample_pdf, qtbot):
    view = window.open_path(sample_pdf)
    i = P.info(view.document)
    dlg = D.ProtectDialog(P.settings_from_document(view.document), already_protected=i.protected,
                          has_open_password=i.has_open_password, save_policy=sec.save_policy)
    qtbot.addWidget(dlg)
    dlg.require_open.setChecked(True)
    dlg.open_pw.setText(OPEN_PW)
    dlg.open_pw2.setText(OPEN_PW)
    dlg.restrict.setChecked(True)
    dlg.perm_pw.setText(PERM_PW)
    dlg.perm_pw2.setText(PERM_PW)
    dlg.perm_boxes["copy"].setChecked(False)
    assert dlg.save_as_policy("Listino rivenditori")
    policy = sec.policy_store().get("Listino rivenditori")
    assert policy.open_password and policy.restrict and "copy" not in policy.allowed
    raw = json.dumps(dict(sec.settings()))
    assert OPEN_PW not in raw and PERM_PW not in raw


def test_export_contains_no_password(sec, tmp_path):
    """Dato un file di policy esportato, quando vi si cerca la password usata, allora non c'è."""
    store = sec.policy_store()
    store.add(POL.Policy.from_settings("Riservato", P.ProtectionSettings(
        open_password=OPEN_PW, permissions_password=PERM_PW, restrict=True, allowed={"print"})))
    out = tmp_path / "policy.json"
    store.export(str(out))
    text = out.read_text("utf-8")
    assert OPEN_PW not in text and PERM_PW not in text
    assert json.loads(text)["policies"]["Riservato"]["method"] == "aes256"


def test_import_old_method_and_invalid(sec, tmp_path):
    f = tmp_path / "vecchie.json"
    f.write_text(json.dumps({"policies": {"Vecchia": {"method": "rc4", "restrict": True, "allowed": ["print"],
                                                      "print_level": "low"},
                                          "Rotta": "x"}}), "utf-8")
    store = sec.policy_store()
    done, notes = store.import_file(str(f))
    assert done == ["Vecchia"]
    assert any("AES" in n for n in notes) and any("Rotta" in n for n in notes)
    assert store.get("Vecchia").settings("", PERM_PW).method == P.AES256
    bad = tmp_path / "bad.json"
    bad.write_text("{}", "utf-8")
    with pytest.raises(ValueError):
        store.import_file(str(bad))


def test_batch_ten_files(window, sec, tmp_path, monkeypatch):
    """Dato 10 PDF, quando si applica la policy in blocco, allora tutti risultano cifrati."""
    files = []
    for i in range(10):
        d = pymupdf.open()
        d.new_page().insert_text((72, 100), f"Listino {i}: Submariner 9.500", fontsize=12)
        path = tmp_path / f"listino_{i}.pdf"
        d.save(path)
        files.append(str(path))
    locked = tmp_path / "gia_protetto.pdf"
    pymupdf.open(files[0]).save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="x", user_pw="y")
    sec.policy_store().add(_riservato())
    out_dir = tmp_path / "protetti"
    out_dir.mkdir()
    monkeypatch.setattr(C, "ask_policy_passwords", lambda parent, policy: (OPEN_PW, PERM_PW))
    errors = []
    monkeypatch.setattr(sec.ctx, "error", lambda title, text: errors.append(text))
    failed = sec.apply_policy_to_files("Riservato", files + [str(locked)], str(out_dir))
    assert [f for f, _e in failed] == [str(locked)] and errors
    outputs = sorted(out_dir.iterdir())
    assert len(outputs) == 10
    for out in outputs:
        pdf = pymupdf.open(out)
        assert pdf.needs_pass and pdf.authenticate(OPEN_PW)
        assert pdf.metadata["encryption"].startswith("Standard V5 R6")
        assert not pdf.permissions & pymupdf.PDF_PERM_COPY
    # gli originali restano invariati
    assert not pymupdf.open(files[3]).needs_pass


def test_policy_dialog_management(sec, tmp_path, qtbot):
    store = sec.policy_store()
    store.add(_riservato())
    dlg = D.PolicyDialog(store, has_document=False)
    qtbot.addWidget(dlg)
    assert dlg.current() == "Riservato" and not dlg.apply_button.isEnabled() and dlg.batch_button.isEnabled()
    assert "bassa risoluzione" in dlg.tree.currentItem().text(1)
    dlg._duplicate()
    assert store.names() == ["Riservato", "Riservato (copia)"]
    assert not dlg.rename("Riservato") and "già" in dlg.notice.text()
    assert dlg.rename("Clienti VIP")
    assert store.names() == ["Clienti VIP", "Riservato"]
    out = tmp_path / "p.json"
    store.export(str(out))
    dlg._remove()
    dlg._remove()
    assert store.names() == [] and "Nessuna policy" in dlg.notice.text()
    dlg.import_from(str(out))
    assert store.names() == ["Clienti VIP", "Riservato"] and "2" in dlg.notice.text()


def test_policy_password_dialog(qtbot):
    dlg = D.PolicyPasswordDialog(_riservato())
    qtbot.addWidget(dlg)
    dlg.open_pw.setText(OPEN_PW)
    dlg.open_pw2.setText(OPEN_PW)
    dlg.perm_pw.setText(PERM_PW)
    dlg.perm_pw2.setText("diversa")
    assert "coincidono" in dlg.problem()
    dlg.perm_pw2.setText(PERM_PW)
    assert dlg.problem() == "" and dlg.passwords() == (OPEN_PW, PERM_PW)
    only_open = D.PolicyPasswordDialog(POL.Policy("Apertura", open_password=True, restrict=False))
    qtbot.addWidget(only_open)
    assert only_open.perm_pw is None


def test_policy_menu(window, sec, sample_pdf):
    sec.policy_store().add(_riservato())
    window.open_path(sample_pdf)
    sec.fill_policy_menu(sec.policy_menu)
    texts = [a.text() for a in sec.policy_menu.actions() if not a.isSeparator()]
    assert texts == ["Riservato", "Gestisci policy…"]
    assert sec.policy_menu.actions()[0].isEnabled()
