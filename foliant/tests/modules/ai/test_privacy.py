"""Regola di riservatezza: l'assistente non lascia su disco nomi, percorsi o impronte dei documenti.

Cronologia, registro degli invii, raccolte e indici restano in memoria per la sessione; i dati
salvati dalle versioni precedenti vengono cancellati una volta sola all'avvio.
"""

from __future__ import annotations

import json
import os

import pytest

from foliant.modules.ai import content
from foliant.modules.ai.config import PRIVACY_CLEANUP_KEY, data_dir, purge_legacy_data

from .conftest import FakeProvider, make_active_profile

SECRET = "zq7k"     # compare nel nome del file e della cartella: non deve comparire su disco


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _legacy_files(base, *, encrypted=False):
    """Dati come li lasciavano le versioni precedenti, più quelli ammessi che devono restare."""
    conv = {"id": "c1", "title": "t", "fingerprint": "f" * 64, "path": f"/home/u/{SECRET}/{SECRET}.pdf",
            "messages": []}
    old = {
        "cronologia/c1.json": ("FOLIANT-ENC1\ngAAAA" if encrypted else json.dumps(conv)),
        "cronologia/c2.danneggiato": "{rotto",
        "cronologia/.c3.json.abc.tmp": "tmp",
        "cache/ffff.json": json.dumps({"fingerprint": "f" * 64, "pages": []}),
        "cache/.ffff.json.abc.tmp": "tmp",
        "raccolte/r1.json": json.dumps({"documents": [{"path": f"/home/u/{SECRET}.pdf"}]}),
        "registro-invii.jsonl": json.dumps({"document": "f" * 16}) + "\n",
    }
    kept = {
        "bibliografia.json": "{}",
        "attivita.json": "[]",
        "grafica/g1.png": "png",
        "grafica/g1.json": "{}",
        "cronologia/leggimi.txt": "file sconosciuto: non si tocca",
    }
    for rel, text in {**old, **kept}.items():
        _write(base / rel, text)
    return old, kept


def _set_legacy_settings(settings):
    settings.setValue("ai/history_encrypt", True)
    settings.setValue("ai/retention_days", 30)
    settings.setValue("ai/history_enabled", True)
    settings.setValue("ai/confirm_each", True)


# ------------------------------------------------------------------ pulizia una tantum
def test_purge_legacy_data_once(settings):
    base = data_dir()
    old, kept = _legacy_files(base)
    _set_legacy_settings(settings)
    assert purge_legacy_data(settings)
    for rel in old:
        assert not (base / rel).exists(), rel
    for rel in kept:
        assert (base / rel).exists(), rel
    assert not (base / "cache").exists() and not (base / "raccolte").exists()
    assert (base / "cronologia").exists()            # resta solo perché contiene un file sconosciuto
    assert settings.value("ai/history_encrypt") is None and settings.value("ai/retention_days") is None
    assert settings.value("ai/confirm_each") in (True, "true")      # le altre preferenze restano
    # seconda esecuzione: non tocca più nulla
    again = _write(base / "cache" / "nuovo.json", "{}")
    assert not purge_legacy_data(settings)
    assert again.exists()
    assert settings.value(PRIVACY_CLEANUP_KEY) in (True, "true")


def test_purge_legacy_data_on_new_profile_writes_nothing(settings):
    assert not data_dir().exists()
    assert not purge_legacy_data(settings)
    assert not data_dir().exists()
    assert settings.allKeys() == []          # profilo nuovo: nessuna scrittura all'avvio


def test_purge_removes_history_key_only_for_encrypted_history(settings, memory_keyring):
    memory_keyring.store[("foliant-ai", "history-key")] = "chiave"
    _legacy_files(data_dir())
    purge_legacy_data(settings)
    assert ("foliant-ai", "history-key") in memory_keyring.store     # cronologia in chiaro: portachiavi intatto
    settings.remove(PRIVACY_CLEANUP_KEY)
    _legacy_files(data_dir(), encrypted=True)
    purge_legacy_data(settings)
    assert ("foliant-ai", "history-key") not in memory_keyring.store


@pytest.fixture
def legacy_profile(settings):
    """Profilo «vecchio» preparato prima dell'apertura della finestra (e quindi della registrazione)."""
    old, kept = _legacy_files(data_dir())
    _set_legacy_settings(settings)
    return old, kept


def test_register_cleans_legacy_data_at_startup(legacy_profile, window, settings):
    old, kept = legacy_profile
    base = data_dir()
    for rel in old:
        assert not (base / rel).exists(), rel
    for rel in kept:
        assert (base / rel).exists(), rel
    assert settings.value("ai/retention_days") is None
    assert settings.value(PRIVACY_CLEANUP_KEY) in (True, "true")


# ------------------------------------------------------------------ prova: nessuna traccia su disco
def test_no_document_traces_after_session(qtbot, window, settings, tmp_path):
    """Usa l'assistente su un file dal nome riconoscibile e cerca nome, cartella e impronta su disco."""
    import pymupdf
    services = window.ctx.ai_services
    fake = FakeProvider(default="Risposta [p. 1]")
    services.provider_factory = lambda p: fake
    make_active_profile(services.store)
    services.refresh()
    folder = tmp_path / f"cartella-{SECRET}"
    folder.mkdir()
    path = folder / f"{SECRET}-contratto.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Testo riservato del contratto", fontsize=12)
    doc.save(str(path))
    doc.close()
    view = window.open_path(str(path))
    fp = content.content_fingerprint(view.document.pdf)
    panel = services.panel
    panel.set_scope("document")
    panel.send("Di cosa parla?")
    qtbot.waitUntil(lambda: panel.worker is None, timeout=10000)
    panel.set_function("summary")
    panel.send()
    qtbot.waitUntil(lambda: panel.worker is None, timeout=10000)
    # raccolta conservata per la sessione
    services.controller.exec_dialog = lambda dlg: 0
    dlg = services.controller.open_collection()
    dlg.add_open()
    dlg.ask("Riassumi")
    dlg.save()
    assert services.history().load_all() and services.log.entries() and services.collections.load_all()
    window.close_view(view)

    needles = [SECRET, fp, fp[:16], fp[:32]]
    roots = {data_dir()}
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.add(os.path.join(xdg, "foliant", "ai"))
    found = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                if any(n in name for n in needles):
                    found.append(os.path.join(dirpath, name))
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    with open(full, "rb") as f:
                        raw = f.read().decode("utf-8", "ignore")
                except OSError:
                    continue
                found += [f"{full}: {n}" for n in needles if n in raw]
    assert not found, found
    assert not (data_dir() / "cronologia").exists() and not (data_dir() / "cache").exists()
    assert not (data_dir() / "raccolte").exists() and not (data_dir() / "registro-invii.jsonl").exists()
    # preferenze dell'assistente: nessun riferimento al documento
    settings.sync()
    for key in settings.allKeys():
        if key.startswith("ai/"):
            value = str(settings.value(key))
            assert not any(n in value for n in needles), key
