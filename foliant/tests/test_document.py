import os

import pymupdf
import pytest

from foliant.core.document import Document, atomic_write
from foliant.core.errors import DocumentError, PasswordRequired, WrongPassword
from tests.conftest import make_pdf


def test_open_and_text(sample_pdf):
    doc = Document.open(sample_pdf)
    assert doc.page_count == 3
    assert "Pagina 2" in doc.page_text(1)
    assert not doc.is_modified
    assert doc.display_name == "esempio.pdf"


def test_change_undo_redo(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Elimina pagina") as pdf:
        pdf.delete_page(0)
    assert doc.page_count == 2
    assert doc.is_modified
    assert doc.undo_label == "Elimina pagina"
    assert doc.undo()
    assert doc.page_count == 3
    assert not doc.is_modified          # tornati allo stato salvato
    assert doc.redo()
    assert doc.page_count == 2
    assert doc.is_modified


def test_change_rolls_back_on_error(sample_pdf):
    doc = Document.open(sample_pdf)
    with pytest.raises(RuntimeError):
        with doc.change("Operazione fallita") as pdf:
            pdf.delete_page(0)
            raise RuntimeError("boom")
    assert doc.page_count == 3
    assert not doc.can_undo
    assert not doc.is_modified


def test_nested_change_is_single_step(sample_pdf):
    doc = Document.open(sample_pdf)
    with doc.change("Esterna") as pdf:
        pdf.delete_page(0)
        with doc.change("Interna") as pdf2:
            pdf2.delete_page(0)
    assert doc.page_count == 1
    doc.undo()
    assert doc.page_count == 3
    assert not doc.can_undo


def test_save_is_atomic_and_original_untouched_until_save(sample_pdf, tmp_path):
    before = open(sample_pdf, "rb").read()
    doc = Document.open(sample_pdf)
    with doc.change("Ruota") as pdf:
        pdf[0].set_rotation(90)
    assert open(sample_pdf, "rb").read() == before
    doc.save()
    assert not doc.is_modified
    check = pymupdf.open(sample_pdf)
    assert check[0].rotation == 90
    check.close()
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert leftovers == []


def test_save_as_changes_path(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    target = tmp_path / "copia.pdf"
    doc.save(target)
    assert doc.path == target
    assert doc.display_name == "copia.pdf"
    assert target.exists()


def test_save_copy_keeps_path(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("x") as pdf:
        pdf.delete_page(2)
    doc.save_copy(tmp_path / "c.pdf")
    assert str(doc.path) == sample_pdf
    assert doc.is_modified


def test_open_errors(tmp_path):
    with pytest.raises(DocumentError):
        Document.open(tmp_path / "manca.pdf")
    bad = tmp_path / "rotto.pdf"
    bad.write_bytes(b"questo non e un pdf")
    with pytest.raises(DocumentError):
        Document.open(bad)
    with pytest.raises(DocumentError):
        Document.open(tmp_path)


def test_encrypted(encrypted_pdf, tmp_path):
    with pytest.raises(PasswordRequired):
        Document.open(encrypted_pdf)
    with pytest.raises(WrongPassword):
        Document.open(encrypted_pdf, "sbagliata")
    doc = Document.open(encrypted_pdf, "segreta")
    assert "Riservato" in doc.page_text(0)
    with doc.change("Aggiungi pagina") as pdf:
        pdf.new_page()
    assert doc.undo()                      # il ripristino deve ri-autenticare
    assert doc.page_count == 1
    assert doc.redo()
    doc.save(tmp_path / "ancora-protetto.pdf")
    reopened = pymupdf.open(tmp_path / "ancora-protetto.pdf")
    assert reopened.needs_pass             # la protezione è mantenuta
    assert reopened.authenticate("segreta")
    assert reopened.page_count == 2


def test_open_image_converts(tmp_path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    img = tmp_path / "foto.png"
    pix.save(str(img))
    doc = Document.open(img)
    assert doc.page_count == 1
    assert doc.needs_save_as
    assert doc.is_modified
    assert doc.display_name == "foto.pdf"


def test_new_document():
    doc = Document.new(pages=2)
    assert doc.page_count == 2
    assert doc.needs_save_as
    assert not doc.is_modified


def test_listeners_and_replace(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    events = []
    doc.add_listener(lambda d, k: events.append(k))
    other = make_pdf(tmp_path / "altro.pdf", pages=5)
    doc.replace_with(open(other, "rb").read(), "Sostituisci")
    assert doc.page_count == 5
    assert events == ["content"]
    doc.undo()
    assert doc.page_count == 3


def test_atomic_write_error(tmp_path):
    with pytest.raises(DocumentError):
        atomic_write(tmp_path / "non" / "esiste" / "x.pdf", b"x")


def test_save_options_encryption(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("Proteggi"):
        doc.save_options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "a", "owner_pw": "b"}
    out = tmp_path / "p.pdf"
    doc.save(out)
    check = pymupdf.open(out)
    assert check.needs_pass and check.authenticate("a")


def test_read_only_file_is_not_overwritten(sample_pdf):
    import stat
    os.chmod(sample_pdf, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    doc = Document.open(sample_pdf)
    with doc.change("x") as pdf:
        pdf.delete_page(0)
    with pytest.raises(DocumentError):
        doc.save()
    assert pymupdf.open(sample_pdf).page_count == 3


def test_save_without_changes_keeps_file_untouched(sample_pdf):
    before = os.stat(sample_pdf).st_mtime_ns
    doc = Document.open(sample_pdf)
    doc.save()
    assert os.stat(sample_pdf).st_mtime_ns == before


def test_undo_restores_protection_settings(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("Proteggi"):
        doc.save_options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "a", "owner_pw": "b"}
    assert doc.is_protected
    doc.undo()
    assert not doc.is_protected
    doc.redo()
    assert doc.is_protected


def test_encrypted_passwords_and_permissions_preserved(tmp_path):
    path = tmp_path / "riservato.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Contratto")
    perms = pymupdf.PDF_PERM_PRINT
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="cliente", owner_pw="notaio", permissions=perms)
    for pw in ("cliente", "notaio"):
        doc = Document.open(path, pw)
        with doc.change("Aggiungi pagina") as pdf:
            pdf.new_page()
        out = tmp_path / f"salvato-{pw}.pdf"
        doc.save(out)
        check = pymupdf.open(out)
        assert check.needs_pass
        assert check.authenticate("cliente") == 2
        check2 = pymupdf.open(out)
        assert check2.authenticate("notaio") == 4
        check3 = pymupdf.open(out)
        check3.authenticate("cliente")
        assert not check3.permissions & pymupdf.PDF_PERM_MODIFY


def test_save_keeps_live_xrefs(tmp_path):
    """Il salvataggio compatta il file ma non rinumera gli oggetti del documento aperto."""
    d = pymupdf.open()
    for i in range(5):
        p = d.new_page()
        p.insert_text((72, 72), f"P{i}")
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 50, 50), "uri": f"https://{i}.example"})
    src = tmp_path / "x.pdf"
    d.save(str(src))
    doc = Document.open(src)
    with doc.change("Elimina") as pdf:
        pdf.delete_page(1)
        pdf.delete_page(1)
    before = [(pg.xref, [l["xref"] for l in pg.get_links()]) for pg in doc.pdf]
    doc.save(tmp_path / "y.pdf")
    after = [(pg.xref, [l["xref"] for l in pg.get_links()]) for pg in doc.pdf]
    assert before == after
    out = pymupdf.open(str(tmp_path / "y.pdf"))
    assert out.page_count == 3 and out.xref_length() < 20       # il file salvato è compattato


def test_unsupported_security_handler_named(tmp_path):
    d = pymupdf.open()
    d.new_page()
    p = tmp_path / "a.pdf"
    d.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="x", user_pw="y")
    bad = tmp_path / "b.pdf"
    bad.write_bytes(p.read_bytes().replace(b"/Standard", b"/FooSecure"))
    with pytest.raises(DocumentError, match="FooSecure"):
        Document.open(bad)


def test_listener_error_does_not_fail_save(sample_pdf, tmp_path, monkeypatch):
    import sys
    doc = Document.open(sample_pdf)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[1]))

    def broken(document, kind):
        raise RuntimeError("osservatore difettoso")
    doc.add_listener(broken)
    with doc.change("x") as pdf:
        pdf.delete_page(0)
    doc.save(tmp_path / "out.pdf")                   # non solleva
    assert not doc.is_modified and errors
    assert pymupdf.open(str(tmp_path / "out.pdf")).page_count == 2


def test_saved_file_uses_object_streams(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    with doc.change("x") as pdf:
        pdf[0].insert_text((72, 300), "nuovo")
    out = tmp_path / "o.pdf"
    doc.save(out)
    assert b"/ObjStm" in out.read_bytes()


# ============================================================ accesso e permessi (SEC-01…SEC-03)

def _protected(tmp_path, user="cliente", owner="notaio", perms=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY,
               enc=pymupdf.PDF_ENCRYPT_AES_256, name="protetto.pdf"):
    path = tmp_path / name
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Contratto riservato", fontsize=14)
    d.save(str(path), encryption=enc, user_pw=user, owner_pw=owner, permissions=perms)
    return path


def test_access_of_plain_document(sample_pdf):
    doc = Document.open(sample_pdf)
    assert doc.access == "none"
    assert doc.permissions == -1
    assert doc.encryption_info == {}
    assert doc.allows(pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_COPY)
    assert doc.unlock("qualsiasi")          # niente da sbloccare


def test_access_user_owner_and_encryption_info(tmp_path):
    path = _protected(tmp_path)
    user = Document.open(path, "cliente")
    assert user.access == "user"
    assert user.permissions != -1
    assert user.allows(pymupdf.PDF_PERM_PRINT)
    assert not user.allows(pymupdf.PDF_PERM_MODIFY)
    assert not user.allows(pymupdf.PDF_PERM_COPY) and not user.can_copy
    assert user.allows(pymupdf.PDF_PERM_ACCESSIBILITY)
    info = user.encryption_info
    assert (info["method"], info["V"], info["R"], info["length"]) == ("AES-256", 5, 6, 256)
    assert info["encrypt_metadata"] is True and info["perms_tampered"] is False
    assert info["engine"] == pymupdf.PDF_ENCRYPT_AES_256
    owner = Document.open(path, "notaio")
    assert owner.access == "owner" and owner.permissions == -1
    assert owner.allows(pymupdf.PDF_PERM_MODIFY)
    old = Document.open(_protected(tmp_path, enc=pymupdf.PDF_ENCRYPT_RC4_128, name="rc4.pdf"), "cliente")
    assert (old.encryption_info["method"], old.encryption_info["V"], old.encryption_info["R"]) == ("RC4-128", 2, 3)
    assert not old.allows(pymupdf.PDF_PERM_PRINT_HQ)                  # R3: stampa solo a bassa risoluzione
    r2 = Document.open(_protected(tmp_path, enc=pymupdf.PDF_ENCRYPT_RC4_40, name="rc4-40.pdf"), "cliente")
    assert r2.encryption_info["R"] == 2 and r2.encryption_info["length"] == 40
    assert r2.allows(pymupdf.PDF_PERM_PRINT_HQ)                        # R2: i bit 9–12 non esistono
    everything = Document.open(_protected(tmp_path, perms=-4, name="tutto.pdf"), "cliente")
    assert everything.access == "user" and everything.permissions == -1
    # documento costruito da un documento del motore già aperto: stessi permessi effettivi
    engine = pymupdf.open(tmp_path / "rc4-40.pdf")
    engine.authenticate("cliente")
    direct = Document(engine)
    assert direct.access == "user" and direct.allows(pymupdf.PDF_PERM_PRINT_HQ) and not direct.can_copy
    engine = pymupdf.open(tmp_path / "tutto.pdf")
    engine.authenticate("cliente")
    assert Document(engine).permissions == -1


def test_access_with_empty_open_password(tmp_path):
    path = _protected(tmp_path, user="")
    doc = Document.open(path)
    assert doc.access == "user" and not doc.allows(pymupdf.PDF_PERM_MODIFY)
    assert "Contratto" in doc.page_text(0)
    owner = Document.open(path, "notaio")          # la password dei permessi dà subito l'accesso completo
    assert owner.access == "owner"
    wrong = Document.open(path, "sbagliata")       # una password errata non toglie l'accesso utente
    assert wrong.access == "user" and "Contratto" in wrong.page_text(0)


def test_permissions_tampered_r6(tmp_path):
    """/P alterato rispetto a /Perms: si applica l'AND dei due e i permessi risultano alterati (SEC-02)."""
    path = _protected(tmp_path, user="")
    data = path.read_bytes()
    p = Document.open(path).encryption_info["P"]
    forged = f"/P {p | pymupdf.PDF_PERM_MODIFY}".encode()
    original = f"/P {p}".encode()
    assert len(forged) == len(original) and original in data
    path.write_bytes(data.replace(original, forged))
    doc = Document.open(path)
    assert doc.encryption_info["perms_tampered"] is True
    assert not doc.allows(pymupdf.PDF_PERM_MODIFY)


def test_allows_implied_permissions():
    from foliant.core.security import allows
    assert allows(pymupdf.PDF_PERM_ANNOTATE, pymupdf.PDF_PERM_FORM)      # i commenti comprendono la compilazione
    assert allows(pymupdf.PDF_PERM_MODIFY, pymupdf.PDF_PERM_ASSEMBLE)    # la modifica comprende l'assemblaggio
    assert allows(0, pymupdf.PDF_PERM_ACCESSIBILITY)
    assert not allows(pymupdf.PDF_PERM_PRINT_HQ, pymupdf.PDF_PERM_PRINT_HQ)   # stampa fedele senza stampa
    assert allows(-1, pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_COPY)


def test_unlock_after_changes_keeps_owner_through_undo_and_protection(tmp_path):
    path = _protected(tmp_path)
    doc = Document.open(path, "cliente")
    events = []
    doc.add_listener(lambda d, k: events.append(k))
    with doc.change("Aggiungi pagina", kind="pages") as pdf:
        pdf.new_page()
    assert not doc.unlock("sbagliata")
    assert doc.access == "user" and "Contratto" in doc.page_text(0)   # il documento resta leggibile
    assert doc.unlock("notaio")
    assert events[-1] == "access"
    assert doc.access == "owner" and doc.permissions == -1 and doc.can_copy
    assert doc.original_protection["owner_pw"] == "notaio" == doc.save_options["owner_pw"]
    with doc.lock:
        assert doc.pdf.permissions & pymupdf.PDF_PERM_MODIFY        # anche il motore vede l'accesso completo
    assert doc.undo()
    assert doc.save_options["owner_pw"] == "notaio"                  # opzioni della cronologia aggiornate
    with doc.lock:
        assert doc.pdf.permissions & pymupdf.PDF_PERM_MODIFY        # istantanea ri-autenticata come proprietario
    assert doc.redo() and doc.page_count == 2
    out = tmp_path / "salvato.pdf"
    doc.save(out)
    check = pymupdf.open(out)
    assert check.authenticate("cliente") == 2
    assert not check.permissions & pymupdf.PDF_PERM_MODIFY           # stessa protezione: permessi invariati
    assert pymupdf.open(out).authenticate("notaio") & 4
    # cambio di protezione dopo lo sblocco: la password dei permessi resta quella vera
    with doc.change("Proteggi", kind="metadata"):
        doc.save_options = dict(doc.save_options, permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
    doc.save(out)
    assert pymupdf.open(out).authenticate("notaio") & 4
    assert pymupdf.open(out).authenticate("cliente") == 2


# ============================================================ guardie e sola lettura (SIG-21, SIG-17)

def test_change_guard_receives_kind_and_can_cancel(sample_pdf, monkeypatch):
    import sys
    from foliant.core.errors import OperationCancelled
    doc = Document.open(sample_pdf)
    seen, events = [], []
    doc.add_listener(lambda d, k: events.append(k))

    def guard(document, label, kind):
        seen.append((label, kind))
        return kind != "content"
    doc.add_change_guard(guard)
    with pytest.raises(OperationCancelled):
        with doc.change("Elimina pagina") as pdf:       # kind predefinito: «content»
            pdf.delete_page(0)
    assert doc.page_count == 3 and not doc.can_undo and not doc.is_modified and events == []
    with doc.change("Nota", kind="annotation") as pdf:
        pdf[0].add_text_annot((100, 100), "ok")
        with doc.change("Interna", kind="content"):   # i blocchi annidati non interrogano di nuovo
            pass
    assert seen == [("Elimina pagina", "content"), ("Nota", "annotation")]
    assert doc.can_undo and events == ["content"]
    doc.remove_change_guard(guard)
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a[1]))

    def broken(document, label, kind):
        raise RuntimeError("guardia difettosa")
    doc.add_change_guard(lambda d, label, kind: None)                 # None = prosegui
    doc.add_change_guard(broken)                                      # difettosa: segnalata, non blocca
    with doc.change("Ruota", "pages") as pdf:
        pdf[0].set_rotation(90)
    assert errors and doc.undo_label == "Ruota"


def test_set_locked_blocks_changes(sample_pdf, tmp_path):
    from foliant.core.errors import OperationCancelled
    doc = Document.open(sample_pdf)
    with doc.change("x") as pdf:
        pdf.delete_page(0)
    events = []
    doc.add_listener(lambda d, k: events.append(k))
    doc.set_locked("Versione firmata: solo lettura")
    assert events == ["lock"] and doc.locked and doc.locked_reason == "Versione firmata: solo lettura"
    with pytest.raises(OperationCancelled, match="Versione firmata"):
        with doc.change("y") as pdf:
            pdf.delete_page(0)
    with pytest.raises(OperationCancelled):
        doc.replace_with(pymupdf.open(sample_pdf).tobytes(), "Sostituisci")
    assert not doc.can_undo and not doc.undo()
    assert doc.page_count == 2
    doc.save(tmp_path / "copia.pdf")                  # salvare resta possibile
    doc.set_locked(None)
    assert events == ["lock", "saved", "lock"] and not doc.locked
    assert doc.undo() and doc.page_count == 3


def test_repaired_flag(sample_pdf):
    data = open(sample_pdf, "rb").read()
    assert not Document.from_bytes(data).repaired
    broken = Document.from_bytes(data.replace(b"startxref", b"startxrXf"))
    assert broken.repaired and broken.page_count == 3


def test_encrypt_metadata_option_is_ignored(sample_pdf, tmp_path):
    from foliant.core.document import ENCRYPT_METADATA_SUPPORTED
    assert ENCRYPT_METADATA_SUPPORTED is False        # SEC-06: il motore non lo consente
    doc = Document.open(sample_pdf)
    with doc.change("Proteggi"):
        doc.save_options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "a", "owner_pw": "b",
                            "encrypt_metadata": False}
    doc.save(tmp_path / "p.pdf")
    assert pymupdf.open(tmp_path / "p.pdf").authenticate("a")


def test_saved_notification_updates_disk_stamp(sample_pdf):
    """Un modulo che scrive il file e notifica SAVED non fa scattare l'avviso «modificato da un altro programma»."""
    from foliant.core.document import SAVED
    doc = Document.open(sample_pdf)
    data = open(sample_pdf, "rb").read() + b"\n% aggiunto\n"
    with open(sample_pdf, "wb") as f:                   # scrittura non passata da atomic_write
        f.write(data)
    assert doc.disk_changed()
    doc._notify(SAVED)
    assert not doc.disk_changed()
    atomic_write(sample_pdf, data + b"% ancora\n")      # le scritture di Foliant non contano come esterne
    assert not doc.disk_changed()


def test_to_bytes_without_object_streams(sample_pdf):
    doc = Document.open(sample_pdf)
    assert b"/ObjStm" not in doc.to_bytes()            # i byte restano leggibili per chi cerca nel file
    assert b"/ObjStm" in doc.save_bytes()


# ============================================================ salvataggio incrementale (NFR-09, SIG-21)

@pytest.fixture(scope="module")
def test_signer():
    """Firmatario di prova per pyHanko: certificato autofirmato con chiave EC."""
    from datetime import datetime, timedelta, timezone

    from asn1crypto import keys as akeys
    from asn1crypto import x509 as ax509
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Firmatario di prova")])
    now = datetime.now(timezone.utc)
    usage = x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                          encipher_only=False, decipher_only=False)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30)).add_extension(usage, critical=True)
            .sign(key, hashes.SHA256()))
    acert = ax509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    akey = akeys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    signer = signers.SimpleSigner(signing_cert=acert, signing_key=akey,
                                  cert_registry=SimpleCertificateStore.from_certs([acert]))
    return signer, acert


def _sign(data: bytes, signer) -> bytes:
    import io

    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    out = io.BytesIO()
    signers.sign_pdf(IncrementalPdfFileWriter(io.BytesIO(data)), signers.PdfSignatureMetadata(field_name="Firma1"),
                     signer=signer[0], output=out)
    return out.getvalue()


def _validate(data: bytes, signer):
    import io
    import logging

    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)      # analisi delle differenze: avvisi rumorosi
    sig = PdfFileReader(io.BytesIO(data)).embedded_signatures[0]
    return validate_pdf_signature(sig, ValidationContext(trust_roots=[signer[1]]))


def _signed_pdf(tmp_path, signer, objstm: bool, name="firmato.pdf"):
    d = pymupdf.open()
    for i in range(2):
        p = d.new_page()
        p.insert_text((72, 100), f"Pagina {i + 1}", fontsize=20)
        p.insert_image(pymupdf.Rect(72, 200, 172, 300), pixmap=pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20), 0))
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.field_value = pymupdf.PDF_WIDGET_TYPE_TEXT, "Nome", ""
    w.rect = pymupdf.Rect(72, 400, 300, 430)
    d[0].add_widget(w)
    path = tmp_path / name
    path.write_bytes(_sign(d.tobytes(use_objstms=1 if objstm else 0), signer))
    return path


@pytest.mark.parametrize("objstm", [False, True])
def test_signed_document_saved_incrementally(tmp_path, test_signer, objstm):
    path = _signed_pdf(tmp_path, test_signer, objstm)
    signed = path.read_bytes()
    doc = Document.open(path)
    assert doc.has_signatures and doc.original_bytes == signed
    with doc.change("Nota", kind="annotation") as pdf:
        pdf[1].add_text_annot((100, 100), "Va bene")
    doc.save()
    first = path.read_bytes()
    assert first.startswith(signed) and len(first) > len(signed)     # originale = prefisso esatto
    st = _validate(first, test_signer)
    assert st.intact and st.valid
    assert len(list(pymupdf.open(path)[1].annots())) == 1
    # «Salva con nome» dopo annulla/ripeti: byte del file + modifiche anche nel nuovo file
    doc.undo()
    doc.redo()
    other = tmp_path / "copia.pdf"
    doc.save(other)
    second = other.read_bytes()
    assert second.startswith(first) and doc.path == other
    assert _validate(second, test_signer).intact
    # annulla dopo il salvataggio: nuova revisione in coda, firma integra
    doc.undo()
    doc.save()
    third = other.read_bytes()
    assert third.startswith(second) and len(list(pymupdf.open(other)[1].annots())) == 0
    assert _validate(third, test_signer).intact
    # «Salva una copia»: incrementale, senza cambiare il file del documento
    copy = tmp_path / "terza.pdf"
    doc.save_copy(copy)
    assert copy.read_bytes().startswith(signed) and doc.path == other


@pytest.mark.parametrize("objstm", [False, True])
def test_signed_form_fill_is_permitted_change(tmp_path, test_signer, objstm):
    """Compilazione di un campo: l'aggiornamento contiene solo il campo e pyHanko la classifica come consentita."""
    from pyhanko.sign.diff_analysis import ModificationLevel
    path = _signed_pdf(tmp_path, test_signer, objstm)
    signed = path.read_bytes()
    doc = Document.open(path)
    with doc.change("Compila", kind="form") as pdf:
        widget = next(w for w in pdf[0].widgets() if w.field_name == "Nome")
        kind, parent = pdf.xref_get_key(widget.xref, "Parent")
        pdf.xref_set_key(int(parent.split()[0]) if kind == "xref" else widget.xref, "V", "(Mario Rossi)")
    doc.undo()
    doc.redo()
    out = doc.save_bytes()
    assert out.startswith(signed)
    st = _validate(out, test_signer)
    assert st.intact and st.valid and st.docmdp_ok
    assert st.modification_level == ModificationLevel.FORM_FILLING


def test_signed_document_rewrites_when_protection_changes(tmp_path, test_signer):
    from foliant.core.incremental import IncrementalSaveError
    path = _signed_pdf(tmp_path, test_signer, False)
    signed = path.read_bytes()
    doc = Document.open(path)
    assert doc.save_bytes() == signed                     # nessuna modifica: i byte originali
    with doc.change("Proteggi", kind="metadata"):
        doc.save_options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "a", "owner_pw": "b"}
    assert not doc.save_bytes().startswith(signed)        # nuova cifratura: riscrittura completa
    with pytest.raises(IncrementalSaveError):
        doc.save_bytes(incremental=True)


def test_replace_with_renumbered_content_is_not_saved_incrementally(tmp_path, test_signer):
    """Dopo `replace_with` con un PDF rinumerato il confronto per numero di oggetto non vale più:
    niente aggiornamento incrementale (che produrrebbe un file incoerente) finché non si annulla."""
    from foliant.core.incremental import IncrementalSaveError
    path = _signed_pdf(tmp_path, test_signer, True)
    signed = path.read_bytes()
    doc = Document.open(path)
    doc.replace_with(doc.to_bytes(), "Sostituisci")             # compattato: oggetti rinumerati
    with pytest.raises(IncrementalSaveError):
        doc.save_bytes(incremental=True)
    out = doc.save_bytes()                                      # firmato ma non allineato: riscrittura completa
    assert not out.startswith(signed)
    r = pymupdf.open("pdf", out)
    assert r.page_count == 2 and "Pagina 2" in r[1].get_text()
    doc.undo()                                                  # di nuovo la numerazione del file
    with doc.change("Nota", kind="annotation") as pdf:
        pdf[1].add_text_annot((100, 100), "ok")
    out = doc.save_bytes()
    assert out.startswith(signed) and _validate(out, test_signer).intact
    # una revisione in coda al file firmato conserva la numerazione: resta incrementale
    tmp = tmp_path / "revisione.pdf"
    tmp.write_bytes(signed)
    rev = pymupdf.open(tmp)
    rev[0].add_text_annot((50, 50), "revisione")
    rev.save(str(tmp), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    rev.close()
    doc.replace_with(tmp.read_bytes(), "Sostituisci con la revisione")
    out = doc.save_bytes(incremental=True)
    assert out.startswith(signed) and _validate(out, test_signer).intact
    assert len(list(pymupdf.open("pdf", out)[0].annots())) == 1


def test_explicit_incremental_and_save_strategy(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    original = open(sample_pdf, "rb").read()
    assert not doc.has_signatures
    with doc.change("Nota", kind="annotation") as pdf:
        pdf[0].add_text_annot((100, 100), "x")
    assert doc.save_bytes(incremental=True).startswith(original)   # su richiesta anche senza firme
    assert not doc.save_bytes().startswith(original)                 # predefinito: file compattato
    calls = []

    def strategy(document, path):
        calls.append(path)
        return b"%PDF-strategia" if path and path.name == "speciale.pdf" else None
    doc.save_strategy = strategy
    doc.save(tmp_path / "speciale.pdf")
    assert (tmp_path / "speciale.pdf").read_bytes() == b"%PDF-strategia"
    doc.save(tmp_path / "normale.pdf")
    assert pymupdf.open(tmp_path / "normale.pdf").page_count == 3
    assert [p.name for p in calls] == ["speciale.pdf", "normale.pdf"]


def test_encrypted_document_incremental_keeps_encryption(tmp_path):
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "Pagina cifrata", fontsize=20)
    path = tmp_path / "cifrato.pdf"
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="own", user_pw="", use_objstms=1)
    original = path.read_bytes()
    doc = Document.open(path)
    with doc.change("Nota", kind="annotation") as pdf:
        pdf[0].add_text_annot((100, 100), "ciao")
    doc.undo()
    doc.redo()
    doc.save(incremental=True)
    assert path.read_bytes().startswith(original)
    r = pymupdf.open(path)
    assert r.metadata["encryption"] and "Pagina cifrata" in r[0].get_text() and len(list(r[0].annots())) == 1
