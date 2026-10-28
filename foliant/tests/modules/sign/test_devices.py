"""SIG-23: firma con un dispositivo PKCS#11 (token software SoftHSM)."""

from __future__ import annotations

import pytest

from foliant.modules.sign import devices, digital

from .conftest import TOKEN_PIN


def test_certificates_listed_without_pin(softhsm):
    module, info = softhsm
    certs, errors = devices.list_certificates([module])
    assert errors == []
    [c] = certs
    assert c.info.sha256 == info.sha256 and c.token_label == "Carta firma" and c.cert_id == "01"
    assert devices.is_token_uri(c.uri) and devices.find_certificate(c.uri).info.sha256 == info.sha256


def test_sign_on_device_key_never_leaves(softhsm, pdf_bytes):
    module, info = softhsm
    [c], _e = devices.list_certificates([module])
    did = devices.open_digital_id(c.uri, TOKEN_PIN)
    try:
        out = digital.sign_pdf(pdf_bytes, did, digital.SignOptions(reason="Vendita"))
    finally:
        did.close()
    [st] = digital.verify_pdf(out, trusted=[info])
    assert st.status == digital.VALID and st.signer == "Mario Rossi (carta)"


def test_wrong_pin(softhsm):
    module, _info = softhsm
    [c], _e = devices.list_certificates([module])
    with pytest.raises(digital.WrongCertificatePassword, match="PIN errato"):
        devices.open_digital_id(c.uri, "000000")


def test_unloadable_module_does_not_block_others(softhsm, tmp_path):
    module, _info = softhsm
    bad = tmp_path / "rotto.so"
    bad.write_bytes(b"non un modulo")
    certs, errors = devices.list_certificates([str(bad), module])
    assert len(certs) == 1 and len(errors) == 1 and "rotto.so" in errors[0]
    with pytest.raises(devices.TokenError, match="rotto.so"):
        devices.add_module(str(bad))
    assert devices.add_module(module) in devices.modules()


def test_ids_dialog_devices_tab_and_sign_dialog(window, qtbot, softhsm, pdf_bytes):
    """Gestisci identità › Dispositivi: il modulo aggiunto mostra il certificato della carta; nel dialogo di
    firma la carta compare fra gli ID, chiede il PIN (non la password) e firma sul dispositivo."""
    from foliant.modules.sign.ui_digital import DigitalSignDialog, IDsDialog
    module, info = softhsm
    devices.add_module(module)
    c = window.ctx.sign_controller
    ids = IDsDialog(window, c)
    qtbot.addWidget(ids)
    assert ids.device_certs.count() == 1 and "Mario Rossi (carta)" in ids.device_certs.item(0).text()
    dlg = DigitalSignDialog(window, c)
    qtbot.addWidget(dlg)
    i = next(k for k in range(dlg.id_combo.count()) if dlg.id_combo.itemText(k).startswith("🔐"))
    dlg.id_combo.setCurrentIndex(i)
    assert dlg.password_label.text() == "PIN:" and "Mario Rossi (carta)" in dlg.cert_info.text()
    dlg.password.setText("999999")
    dlg._accept()
    assert dlg.digital_id is None and "PIN errato" in dlg.error.text()
    dlg.password.setText(TOKEN_PIN)
    dlg._accept()
    did = dlg.digital_id
    assert did is not None and did.info.sha256 == info.sha256
    try:
        out = digital.sign_pdf(pdf_bytes, did, dlg.options)
    finally:
        did.close()
    assert digital.verify_pdf(out, trusted=[info])[0].status == digital.VALID


def test_without_python_pkcs11_everything_else_works(window, qtbot, monkeypatch):
    """Dipendenza facoltativa: senza python-pkcs11 la scheda lo spiega e il dialogo di firma funziona."""
    import sys
    from foliant.modules.sign.ui_digital import DigitalSignDialog, IDsDialog
    monkeypatch.setitem(sys.modules, "pkcs11", None)          # import pkcs11 → ImportError
    c = window.ctx.sign_controller
    ids = IDsDialog(window, c)
    qtbot.addWidget(ids)
    assert "python-pkcs11" in ids.device_status.text()
    dlg = DigitalSignDialog(window, c)
    qtbot.addWidget(dlg)
    assert dlg.device_certificates() == []
