"""SIG-07: sincronizzazione della libreria firme fra due profili attraverso una cartella."""

from __future__ import annotations

import datetime as dt

import pytest

from foliant.modules.sign import graphic as g
from foliant.modules.sign import sync
from foliant.modules.sign.library import SignatureLibrary

PASS = "Cartella-Condivisa-2026"


def _item(kind="firma", w=60):
    return g.SignatureItem(kind=kind, art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (w, 12, 1), (90, 0, 1)]])))


@pytest.fixture
def two(tmp_path):
    folder = tmp_path / "Nextcloud" / "firme"
    folder.mkdir(parents=True)
    a, b = SignatureLibrary(tmp_path / "pc-negozio"), SignatureLibrary(tmp_path / "portatile")
    sync.connect(a, folder, PASS)
    sync.connect(b, folder, PASS)
    return a, b, folder


def _names(lib):
    return sorted(e.name for e in lib.entries())


def test_added_on_one_profile_appears_on_the_other(two):
    a, b, folder = two
    e = a.add(_item(), "Firma del titolare")
    assert sync.sync(a, PASS).pushed == 1
    res = sync.sync(b, PASS)
    assert res.pulled == 1
    [eb] = b.entries()
    assert (eb.id, eb.name, eb.kind, eb.sha256) == (e.id, e.name, e.kind, e.sha256)
    # niente in chiaro nella cartella condivisa
    for p in folder.rglob("*"):
        if p.is_file():
            assert b"Firma del titolare" not in p.read_bytes() and b"polyline" not in p.read_bytes()
    # nessuna modifica: un secondo ciclo non trasferisce nulla
    assert sync.sync(a, PASS).pushed == 0 and sync.sync(b, PASS).pulled == 0


def test_rename_and_default_propagate(two):
    a, b, _f = two
    x = a.add(_item(), "Firma")
    y = a.add(_item(w=70), "Firma corta")
    sync.sync(a, PASS)
    sync.sync(b, PASS)
    b.rename(x.id, "Firma completa")
    b.set_default(y.id)
    sync.sync(b, PASS)
    sync.sync(a, PASS)
    assert _names(a) == ["Firma completa", "Firma corta"]
    assert a.default("firma").id == y.id


def test_conflict_keeps_both_versions(two):
    a, b, _f = two
    x = a.add(_item(), "Firma")
    sync.sync(a, PASS)
    sync.sync(b, PASS)
    a.rename(x.id, "Firma A")
    b.rename(x.id, "Firma B")
    sync.sync(a, PASS)
    res = sync.sync(b, PASS)
    assert res.conflicts == 1
    sync.sync(a, PASS)
    assert _names(a) == _names(b) == ["Firma A", "Firma B (conflitto)"]


def test_deletion_is_logical_and_recoverable(two, monkeypatch):
    a, b, folder = two
    x = a.add(_item(), "Da eliminare")
    sync.sync(a, PASS)
    sync.sync(b, PASS)
    a.remove(x.id)
    assert sync.sync(a, PASS).deleted == 1
    assert sync.sync(b, PASS).deleted == 1 and b.entries() == []
    [(eid, name, _when)] = sync.deleted_items(b, PASS)
    assert (eid, name) == (x.id, "Da eliminare")
    sync.restore(b, PASS, eid)
    assert _names(b) == ["Da eliminare"]
    sync.sync(a, PASS)
    assert _names(a) == ["Da eliminare"]
    # dopo 30 giorni la cancellazione diventa definitiva e il file sparisce dalla cartella
    a.remove(x.id)
    sync.sync(a, PASS)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=31)
    monkeypatch.setattr(sync, "_now", lambda: later)
    assert sync.sync(a, PASS).purged == 1
    assert not (folder / "items" / f"{x.id}.enc").exists() and sync.deleted_items(a, PASS) == []


def test_wrong_passphrase_and_unreachable_folder(two, tmp_path):
    a, _b, folder = two
    c = SignatureLibrary(tmp_path / "terzo")
    with pytest.raises(sync.SyncPassphraseError):
        sync.connect(c, folder, "sbagliata")
    a.add(_item(), "Firma")
    folder.rename(tmp_path / "scollegata")
    with pytest.raises(sync.SyncSuspended):
        sync.sync(a, PASS)
    # la libreria locale continua a funzionare
    a.add(_item(w=80), "Altra firma")
    assert _names(a) == ["Altra firma", "Firma"]
    (tmp_path / "scollegata").rename(folder)
    assert sync.sync(a, PASS).pushed == 2


def test_typed_and_image_items_round_trip(two, qapp):
    import io

    from PIL import Image
    a, b, _f = two
    style = g.typed_styles()[0]
    typed = g.SignatureItem(kind="iniziali", origin="digitata", text="MR", style_id=style.id,
                            art=g.text_to_art("MR", style, 0))
    buf = io.BytesIO()
    Image.new("RGBA", (60, 20), (0, 0, 0, 255)).save(buf, "PNG")
    img = g.SignatureItem(kind="firma", origin="immagine", png=buf.getvalue())
    a.add(typed, "Iniziali")
    a.add(img, "Timbro")
    sync.sync(a, PASS)
    sync.sync(b, PASS)
    assert sorted((e.name, e.origin, e.sha256) for e in b.entries()) == \
        sorted((e.name, e.origin, e.sha256) for e in a.entries())
    for e in b.entries():
        b.load_item(e.id)
