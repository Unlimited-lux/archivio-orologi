"""Protezione con password: SEC-04, SEC-05, SEC-06, SEC-07, SEC-08 (logica)."""

from __future__ import annotations

import re

import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.security import protection as P


def _bit(value: int, n: int) -> int:
    return (value >> (n - 1)) & 1


def _encrypt_dict(data: bytes) -> str:
    pdf = pymupdf.open(stream=data, filetype="pdf")
    kind, value = pdf.xref_get_key(-1, "Encrypt")
    if kind == "xref":
        value = pdf.xref_object(int(value.split()[0]))
    pdf.close()
    return value


def test_open_password_aes256(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    P.apply_protection(doc, P.ProtectionSettings(open_password="Apri-123!"))
    out = tmp_path / "cifrato.pdf"
    doc.save(out)
    data = out.read_bytes()
    enc = _encrypt_dict(data)
    assert "/V 5" in enc and "/R 6" in enc and "/AESV3" in enc
    # niente testo in chiaro
    assert b"Pagina 1" not in data and b"Orologeria" not in data
    pdf = pymupdf.open(out)
    assert pdf.needs_pass
    assert pdf.authenticate("sbagliata") == 0
    assert pdf.authenticate("") == 0          # proprietario con password vuota: no
    assert pdf.authenticate("Apri-123!")
    assert "Pagina 1" in pdf[0].get_text()
    # AES-256 dichiarato con l'estensione ADBE di livello 8
    assert "ExtensionLevel 8" in pdf.xref_object(pdf.pdf_catalog())
    doc.close()


def test_original_file_untouched_until_save(sample_pdf):
    before = open(sample_pdf, "rb").read()
    doc = Document.open(sample_pdf)
    P.apply_protection(doc, P.ProtectionSettings(open_password="x-Segreta-1"))
    assert open(sample_pdf, "rb").read() == before
    assert doc.is_modified and doc.is_protected
    doc.close()


def test_same_password_twice_differs(sample_pdf, tmp_path):
    outs = []
    for n in range(2):
        doc = Document.open(sample_pdf)
        P.apply_protection(doc, P.ProtectionSettings(open_password="Uguale-2026"))
        out = tmp_path / f"c{n}.pdf"
        doc.save(out)
        outs.append(_encrypt_dict(out.read_bytes()))
        doc.close()
    u = [re.search(r"/U\s*<([0-9A-Fa-f]+)>", e).group(1) for e in outs]
    assert u[0] != u[1]


def test_permissions_bits(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    s = P.ProtectionSettings(permissions_password="Permessi-1", restrict=True,
                             allowed={"annotate"}, print_level=P.PRINT_LOW, method=P.AES256)
    P.apply_protection(doc, s)
    out = tmp_path / "permessi.pdf"
    doc.save(out)
    enc = _encrypt_dict(out.read_bytes())
    p = int(re.search(r"/P\s*(-?\d+)", enc).group(1))
    for n in (3, 6, 9, 10):
        assert _bit(p, n) == 1, n
    for n in (1, 2, 4, 5, 11, 12):
        assert _bit(p, n) == 0, n
    for n in (7, 8, *range(13, 33)):
        assert _bit(p, n) == 1, n
    # senza password di apertura si apre, con accesso limitato
    pdf = pymupdf.open(out)
    assert not pdf.needs_pass
    assert not pdf.permissions & pymupdf.PDF_PERM_COPY
    assert pdf.authenticate("Permessi-1") & 4
    doc.close()


def test_validation_errors():
    with pytest.raises(P.ProtectionError):
        P.ProtectionSettings().validate()                       # nulla da proteggere
    with pytest.raises(P.ProtectionError):
        P.ProtectionSettings(restrict=True, allowed=set()).validate()   # manca password permessi
    with pytest.raises(P.ProtectionError):
        P.ProtectionSettings(open_password="a1", permissions_password="a1", restrict=True,
                             allowed=set()).validate()
    with pytest.raises(P.ProtectionError):
        P.ProtectionSettings(open_password="пароль", method=P.AES128).validate()
    with pytest.raises(P.ProtectionError):
        P.ProtectionSettings(open_password="x" * 40, method=P.RC4).validate()
    P.ProtectionSettings(open_password="пароль-€").validate()  # AES-256 ammette Unicode


def test_unicode_password_roundtrip(sample_pdf, tmp_path):
    doc = Document.open(sample_pdf)
    P.apply_protection(doc, P.ProtectionSettings(open_password="пароль-€"))
    out = tmp_path / "u.pdf"
    doc.save(out)
    doc.close()
    reopened = Document.open(out, "пароль-€")
    assert "Pagina 1" in reopened.page_text(0)
    reopened.close()


@pytest.mark.parametrize("method,v,r,cfm", [(P.AES128, "4", "4", "AESV2"), (P.RC4, "2", "3", None)])
def test_methods(sample_pdf, tmp_path, method, v, r, cfm):
    doc = Document.open(sample_pdf)
    P.apply_protection(doc, P.ProtectionSettings(open_password="Prova-2026", method=method))
    out = tmp_path / "m.pdf"
    doc.save(out)
    enc = _encrypt_dict(out.read_bytes())
    assert f"/V {v}" in enc and f"/R {r}" in enc
    if cfm:
        assert cfm in enc
    else:
        assert "/Length 128" in enc
    assert out.read_bytes()[:8] >= b"%PDF-1.6"
    doc.close()


def test_aes256_forces_accessibility():
    s = P.ProtectionSettings(permissions_password="pp", restrict=True, allowed=set(), method=P.AES256)
    assert s.permission_value() & pymupdf.PDF_PERM_ACCESSIBILITY
    s.method = P.AES128
    assert not s.permission_value() & pymupdf.PDF_PERM_ACCESSIBILITY


def test_comments_imply_forms():
    s = P.ProtectionSettings(permissions_password="pp", restrict=True, allowed={"annotate"})
    assert s.permission_value() & pymupdf.PDF_PERM_FORM


def test_change_password(tmp_path, sample_pdf):
    first = tmp_path / "vecchia.pdf"
    doc = Document.open(sample_pdf)
    P.apply_protection(doc, P.ProtectionSettings(open_password="vecchia"))
    doc.save(first)
    doc.close()
    doc = Document.open(first, "vecchia")
    assert P.owner_known(doc)
    P.apply_protection(doc, P.ProtectionSettings(open_password="nuova-2026"))
    doc.save()
    doc.close()
    pdf = pymupdf.open(first)
    assert pdf.authenticate("vecchia") == 0
    assert pdf.authenticate("nuova-2026")


def test_remove_protection(encrypted_pdf, tmp_path):
    doc = Document.open(encrypted_pdf, "segreta")
    assert doc.is_protected
    P.remove_protection(doc)
    assert not doc.is_protected
    out = tmp_path / "chiaro.pdf"
    doc.save(out)
    doc.close()
    data = out.read_bytes()
    assert b"/Encrypt" not in data and b"/Prev" not in data
    pdf = pymupdf.open(out)
    assert not pdf.needs_pass and not pdf.is_encrypted
    assert "Riservato" in pdf[0].get_text()


def test_owner_password_required_for_restricted(tmp_path):
    path = tmp_path / "limitato.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Testo")
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    src.close()
    before = path.read_bytes()
    doc = Document.open(path)
    assert P.needs_owner_password(doc)
    assert "copy" not in P.allowed_now(doc)
    assert not P.unlock(doc, "sbagliata")
    assert path.read_bytes() == before
    assert P.unlock(doc, "capo")
    assert not P.needs_owner_password(doc)
    assert "copy" in P.allowed_now(doc)
    doc.close()


def test_undo_redo_restores_save_options(sample_pdf):
    doc = Document.open(sample_pdf)
    assert not doc.is_protected
    P.apply_protection(doc, P.ProtectionSettings(open_password="Uno-1234"))
    assert doc.is_protected
    with doc.change("Ruota") as pdf:
        pdf[0].set_rotation(90)
    doc.undo()
    assert doc.is_protected
    doc.undo()
    assert not doc.is_protected
    doc.redo()
    assert doc.is_protected and doc.save_options["user_pw"] == "Uno-1234"
    doc.close()


def test_info_and_settings(sample_pdf):
    doc = Document.open(sample_pdf)
    assert P.describe(doc) == "Nessuna protezione"
    P.apply_protection(doc, P.ProtectionSettings(open_password="Ab-12345", permissions_password="Cd-98765",
                                                 restrict=True, allowed={"copy"}, print_level=P.PRINT_NONE))
    i = P.info(doc)
    assert i.protected and i.has_open_password and i.restricted and i.changed
    s = P.settings_from_document(doc)
    assert s.restrict and "copy" in s.allowed and "modify" not in s.allowed
    assert s.print_level == P.PRINT_NONE
    doc.close()


def test_strength():
    assert P.strength("") == 0
    assert P.strength("abc") == 1
    assert P.strength("Orologio-2026!") == 4
