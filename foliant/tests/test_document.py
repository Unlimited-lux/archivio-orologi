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
