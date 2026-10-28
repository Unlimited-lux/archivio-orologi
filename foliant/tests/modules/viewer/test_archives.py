"""VIS-04 — Archivi compressi."""

from __future__ import annotations

import os
import zipfile

import pytest

from foliant.modules.viewer import archives_engine as A


def test_compress_and_extract_byte_identical(tmp_path, make_pdf_files):
    a, b = make_pdf_files
    z = tmp_path / "pacco.zip"
    A.compress([a, b], str(z))
    out = A.extract_all(str(z), str(tmp_path / "estratti"))
    assert len(out) == 2
    for orig in (a, b):
        got = tmp_path / "estratti" / os.path.basename(orig)
        assert got.read_bytes() == open(orig, "rb").read()


def test_path_traversal_stays_inside(tmp_path):
    z = tmp_path / "ostile.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../x.pdf", b"%PDF-1.4 finto")
        zf.writestr("/assoluto/y.pdf", b"%PDF-1.4 finto")
    dest = tmp_path / "dest" / "dentro"
    out = A.extract_all(str(z), str(dest))
    assert sorted(os.path.relpath(p, dest) for p in out) == ["assoluto/y.pdf", "x.pdf"]
    assert not (tmp_path / "x.pdf").exists() and not (tmp_path / "dest" / "x.pdf").exists()


def test_size_threshold_aborts_without_residue(tmp_path):
    z = tmp_path / "grande.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.pdf", b"0" * 5000)
    dest = tmp_path / "d"
    with pytest.raises(A.ArchiveError):
        A.extract_all(str(z), str(dest), limit=1000)
    assert not dest.exists() or not any(dest.rglob("*"))


def test_corrupt_archive(tmp_path):
    z = tmp_path / "rotto.zip"
    z.write_bytes(b"non sono uno zip")
    with pytest.raises(A.ArchiveError):
        A.list_pdfs(str(z))


@pytest.fixture
def make_pdf_files(tmp_path):
    from tests.conftest import make_pdf
    return make_pdf(tmp_path / "uno.pdf"), make_pdf(tmp_path / "due.pdf", pages=2)


def test_open_pdf_from_archive(viewer_window, tmp_path, make_pdf_files, monkeypatch):
    z = tmp_path / "pacco.zip"
    A.compress(list(make_pdf_files), str(z))
    ctl = viewer_window.ctx.viewer_parts["archives"]
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("due.pdf", True))
    view = ctl.open_archive(str(z))
    assert view is not None
    assert view.document.page_count == 2
    assert view.document.needs_save_as          # nessun file scritto: si propone «Salva con nome»
    assert view.document.display_name == "due.pdf"


def test_archive_without_pdf(viewer_window, tmp_path, monkeypatch):
    z = tmp_path / "vuoto.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("note.txt", "ciao")
    ctl = viewer_window.ctx.viewer_parts["archives"]
    errors = []
    monkeypatch.setattr(viewer_window.ctx, "error", lambda t, m: errors.append(m))
    assert ctl.open_archive(str(z)) is None
    assert errors and "PDF" in errors[0]
