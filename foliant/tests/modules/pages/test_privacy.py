"""Riservatezza del modulo Pagine: nessun percorso di file nei timbri, nei modelli o in /tmp."""

from __future__ import annotations

import json
import os
import time

import pymupdf

from foliant.modules.pages import module as pages_module
from foliant.modules.pages import privacy, stamp


def _doc(pages=2):
    d = pymupdf.open()
    for _i in range(pages):
        d.new_page(width=595, height=842).insert_text((72, 100), "Testo", fontsize=14)
    return d


def _png(path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), 0)
    pix.clear_with(60)
    pix.save(str(path))
    return str(path)


def _pdf(path):
    d = _doc(1)
    d.save(str(path))
    return str(path)


def test_stamp_records_have_no_file_paths(tmp_path):
    """Filigrana, sfondo e logo: il record /FoliantStamp nel PDF non contiene percorsi."""
    folder = tmp_path / "Clienti Riservati"
    folder.mkdir()
    img = _png(folder / "logo-studio-rossi.png")
    letter = _pdf(folder / "carta-intestata-rossi.pdf")
    pdf = _doc()
    stamp.add_watermark(pdf, image=img)
    stamp.add_background(pdf, source_pdf=letter)
    stamp.add_header_footer(pdf, header_center="Relazione", logo=img)
    data = pdf.tobytes()
    assert b"Clienti Riservati" not in data and b"logo-studio-rossi" not in data
    assert b"carta-intestata-rossi" not in data
    records = stamp.list_stamps(pdf)
    text = json.dumps(records)
    assert str(tmp_path) not in text
    by_kind = {r["kind"]: r["params"] for r in records}
    assert by_kind[stamp.WATERMARK]["has_image"] and by_kind[stamp.WATERMARK]["image"] is None
    assert by_kind[stamp.BACKGROUND]["has_source_pdf"] and by_kind[stamp.BACKGROUND]["source_pdf"] is None
    assert by_kind[stamp.HEADERFOOTER]["has_logo"] and by_kind[stamp.HEADERFOOTER]["logo"] is None
    # i parametri restano riutilizzabili (i contrassegni has_* non diventano argomenti)
    kwargs = stamp.kwargs_from_params(stamp.HEADERFOOTER, by_kind[stamp.HEADERFOOTER])
    assert not any(k.startswith("has_") for k in kwargs)
    stamp.add_header_footer(pdf, **kwargs)


def test_leftover_temp_dirs_removed_at_start(tmp_path):
    """Le cartelle temporanee con il nome dei documenti rimaste da un arresto anomalo
    si cancellano all'avvio; quelle di istanze ancora aperte restano."""
    old = tmp_path / "foliant-pagine-k2j4x"                     # versione precedente
    old.mkdir()
    (old / "Contratto Rossi (pagine 1-3).pdf").write_bytes(b"%PDF")
    dead = tmp_path / "foliant-pagine-999999999-ab12"            # processo terminato
    dead.mkdir()
    alive = tmp_path / f"foliant-pagine-{os.getpid()}-cd34"      # questa istanza
    alive.mkdir()
    other = tmp_path / "foliant-altro"
    other.mkdir()
    recent = tmp_path / "foliant-pagine-zz99q"                    # formato precedente, appena creata
    recent.mkdir()
    hour_ago = time.time() - 2 * privacy.LEGACY_GRACE_SECONDS
    os.utime(old, (hour_ago, hour_ago))
    removed = privacy.cleanup_temp_dirs(base=str(tmp_path))
    assert sorted(os.path.basename(p) for p in removed) == sorted([old.name, dead.name])
    assert alive.exists() and other.exists() and recent.exists()


def test_temp_dir_named_after_process(tmp_path):
    path = pages_module.temp_dir()
    assert os.path.basename(path).startswith(privacy.temp_prefix())
    assert path not in privacy.cleanup_temp_dirs(keep=path)
    assert os.path.isdir(path)
