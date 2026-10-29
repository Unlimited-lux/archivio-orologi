"""Libreria locale di firme e iniziali (SIG-04)."""

from __future__ import annotations

import math

import pytest

from tests.conftest import has_mode, set_data_home
from foliant.modules.sign import graphic as g
from foliant.modules.sign.library import LibraryError, LibraryLocked, SignatureLibrary


@pytest.fixture
def lib_root(tmp_path, monkeypatch):
    set_data_home(monkeypatch, tmp_path / "dati")
    return tmp_path / "dati" / "foliant" / "signatures"


def _drawn(kind="firma"):
    raw = [[(10 + i * 4, 40 + 15 * math.sin(i / 5), 1.0) for i in range(50)]]
    return g.SignatureItem(kind=kind, origin="disegnata", art=g.strokes_to_art(g.process_strokes(raw)))


def test_persistence_across_restart(lib_root):
    lib = SignatureLibrary()
    e = lib.add(_drawn(), "La mia firma")
    lib.add(_drawn("iniziali"), "MR")
    again = SignatureLibrary()        # «riapertura» dell'applicazione
    entries = again.entries()
    assert [(x.name, x.kind) for x in entries] == [("La mia firma", "firma"), ("MR", "iniziali")]
    item = again.load_item(e.id)
    assert item.art.aspect == pytest.approx(_drawn().art.aspect, rel=1e-3)
    assert again.default("firma").id == e.id


def test_permissions(lib_root):
    lib = SignatureLibrary()
    e = lib.add(_drawn())
    assert has_mode(lib_root, 0o700)
    assert has_mode(lib_root / e.file, 0o600)
    assert has_mode(lib_root / "manifest.json", 0o600)


def test_manage_rename_reorder_default_duplicate_remove(lib_root):
    lib = SignatureLibrary()
    a = lib.add(_drawn(), "A")
    b = lib.add(_drawn(), "B")
    lib.rename(a.id, "Alfa")
    lib.move(b.id, -1)
    assert [e.name for e in lib.entries("firma")] == ["B", "Alfa"]
    lib.set_default(b.id)
    assert lib.default("firma").id == b.id
    c = lib.duplicate(a.id)
    assert c.name == "Alfa (copia)"
    lib.remove(b.id)
    assert lib.default("firma") is not None
    assert {e.name for e in SignatureLibrary().entries()} == {"Alfa", "Alfa (copia)"}
    with pytest.raises(LibraryError):
        lib.rename(a.id, "  ")


def test_typed_and_image_items(lib_root, qapp):
    lib = SignatureLibrary()
    style = g.typed_styles()[0]
    typed = g.SignatureItem(origin="digitata", text="Mario Rossi", style_id=style.id, color="#1d4ed8",
                            art=g.text_to_art("Mario Rossi", style))
    e1 = lib.add(typed)
    from PIL import Image
    img = Image.new("RGBA", (40, 20), (0, 0, 0, 255))
    e2 = lib.add(g.SignatureItem(origin="immagine", png=g.png_bytes(img)))
    assert e1.file.endswith(".json") and e2.file.endswith(".png")
    t = SignatureLibrary().load_item(e1.id)
    assert t.text == "Mario Rossi" and t.color == "#1d4ed8" and t.art is not None
    assert SignatureLibrary().load_item(e2.id).aspect == pytest.approx(2.0)


def test_corrupted_manifest_restored_from_backup(lib_root):
    lib = SignatureLibrary()
    lib.add(_drawn(), "Firma buona")
    (lib_root / "manifest.json").write_text("{ non è json")
    again = SignatureLibrary()
    assert [e.name for e in again.entries()] == ["Firma buona"]
    assert "copia di sicurezza" in again.notice


def test_missing_file_marked_unavailable(lib_root):
    lib = SignatureLibrary()
    e = lib.add(_drawn(), "Persa")
    (lib_root / e.file).unlink()
    again = SignatureLibrary()
    assert again.entries()[0].available is False
    with pytest.raises(LibraryError):
        again.load_item(e.id)
    again.remove(e.id)
    assert again.entries() == []


def test_protected_library_has_no_plaintext(lib_root, qapp):
    lib = SignatureLibrary()
    style = g.typed_styles()[0]
    lib.add(g.SignatureItem(origin="digitata", text="Segretissimo", style_id=style.id,
                            art=g.text_to_art("Segretissimo", style)), "NomeRiservato")
    lib.add(_drawn(), "Disegno")
    lib.protect("parola-d-ordine")
    for f in lib_root.iterdir():
        raw = f.read_bytes()
        assert b"Segretissimo" not in raw and b"NomeRiservato" not in raw and b"polyline" not in raw
    locked = SignatureLibrary()
    assert locked.locked and locked.entries() == []
    with pytest.raises(LibraryLocked):
        locked.unlock("sbagliata")
    locked.unlock("parola-d-ordine")
    assert {e.name for e in locked.entries()} == {"NomeRiservato", "Disegno"}
    item = locked.load_item(locked.entries("firma")[0].id)
    assert item.text == "Segretissimo"
    locked.add(_drawn("iniziali"), "MR")
    locked.unprotect()
    assert {e.name for e in SignatureLibrary().entries()} == {"NomeRiservato", "Disegno", "MR"}


def test_limit_per_kind(lib_root, monkeypatch):
    from foliant.modules.sign import library
    monkeypatch.setattr(library, "MAX_PER_KIND", 2)
    lib = SignatureLibrary()
    lib.add(_drawn())
    lib.add(_drawn())
    with pytest.raises(LibraryError):
        lib.add(_drawn())
    lib.add(_drawn("iniziali"))


def test_export_import_package(lib_root, tmp_path):
    """SIG-07: pacchetto cifrato; importazione con nomi, tipi e impronte identici; password errata."""
    from foliant.modules.sign.library import PackageError, export_package, import_package
    lib = SignatureLibrary()
    ids = [lib.add(_drawn(), f"F{i}").id for i in range(3)] + [
        lib.add(_drawn("iniziali"), "Iniziali di Mario Rossi").id]
    pkg = tmp_path / "firme.foliant-signatures"
    assert export_package(lib, ids, "pw-pacchetto", pkg) == 4
    # testo lungo: due byte come «MR» comparivano a caso nel cifrato (test instabile)
    assert b"polyline" not in pkg.read_bytes() and b"Iniziali di Mario Rossi" not in pkg.read_bytes()
    before = {(e.name, e.kind, e.sha256) for e in lib.entries()}
    other = SignatureLibrary(tmp_path / "altro")
    with pytest.raises(PackageError):
        import_package(other, pkg, "sbagliata")
    assert other.entries() == [] and not list((tmp_path / "altro").glob("*.svg"))
    res = import_package(other, pkg, "pw-pacchetto")
    assert res["added"] == 4
    assert {(e.name, e.kind, e.sha256) for e in other.entries()} == before
    assert import_package(other, pkg, "pw-pacchetto")["skipped"] == 4
    assert import_package(other, pkg, "pw-pacchetto", duplicates="both")["added"] == 4
