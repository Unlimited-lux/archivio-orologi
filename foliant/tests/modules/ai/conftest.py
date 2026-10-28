"""Fixture del modulo IA: fornitore simulato, portachiavi in memoria, cartella dati temporanea.

Nessuna chiamata di rete: il fornitore simulato registra le richieste e
restituisce risposte, frammenti, ritardi ed errori programmati.
"""

from __future__ import annotations

import threading
import time

import pytest

from foliant.modules.ai import providers
from foliant.modules.ai.providers import AIError, ChatResult


class MemoryKeyring:
    """Portachiavi simulato (backend keyring in memoria)."""

    def __init__(self):
        import keyring.backend

        store = {}

        class Backend(keyring.backend.KeyringBackend):
            priority = 1

            def get_password(self, service, username):
                return store.get((service, username))

            def set_password(self, service, username, password):
                store[(service, username)] = password

            def delete_password(self, service, username):
                import keyring.errors
                if (service, username) not in store:
                    raise keyring.errors.PasswordDeleteError("missing")
                del store[(service, username)]

        self.store = store
        self.backend = Backend()


class FakeProvider(providers.Provider):
    """Fornitore simulato.

    `responses`: lista di elementi consumati in ordine; ciascuno è una stringa,
    un `AIError`, una funzione `f(request) -> str|AIError` oppure un dict
    {"chunks": [...], "delay": s, "stop": "end"}. Senza risposte programmate usa `default`.
    """

    protocol = "fake"

    def __init__(self, responses=None, default="Risposta simulata.", models=("m1", "m2", "m3")):
        super().__init__("https://simulato.example", "chiave", "m1")
        self.responses = list(responses or [])
        self.default = default
        self.requests = []
        self.models = list(models)
        self.request_times = []
        self.closed_at = None
        self.chunks_sent = 0

    def list_models(self):
        if self.responses and isinstance(self.responses[0], AIError):
            raise self.responses.pop(0)
        return list(self.models)

    def complete(self, request, on_text=None, cancel=None):
        self.requests.append(request)
        self.request_times.append(time.monotonic())
        item = self.responses.pop(0) if self.responses else self.default
        if callable(item) and not isinstance(item, AIError):
            item = item(request)
        if isinstance(item, AIError):
            raise item
        if isinstance(item, dict):
            chunks = item.get("chunks", [])
            delay = item.get("delay", 0)
            sent = []
            for c in chunks:
                if delay and cancel is None:
                    time.sleep(delay)
                stop = cancel is not None and (cancel.wait(delay) if delay else cancel.is_set())
                if stop:
                    self.closed_at = time.monotonic()
                    return ChatResult("".join(sent), "interrupted")
                sent.append(c)
                self.chunks_sent += 1
                if on_text:
                    on_text(c)
            if "error" in item:
                raise item["error"]
            return ChatResult("".join(sent), item.get("stop", "end"))
        text = str(item)
        if on_text:
            on_text(text)
        return ChatResult(text, "end")

    # utilità per i test
    def all_text(self) -> str:
        return "\n".join(r.all_text() for r in self.requests)


@pytest.fixture(autouse=True)
def ai_environment(tmp_path, monkeypatch):
    import keyring
    data = tmp_path / "ai-data"
    monkeypatch.setenv("FOLIANT_AI_DIR", str(data))
    kr = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(kr.backend)
    yield kr
    keyring.set_keyring(previous)


@pytest.fixture
def memory_keyring(ai_environment):
    return ai_environment


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def store(settings):
    from foliant.modules.ai.config import ProfileStore
    return ProfileStore(settings)


def make_active_profile(store, **kw):
    from foliant.modules.ai.config import Profile
    p = Profile.from_catalog("anthropic", verified=True, consent=True, **kw)
    store.save(p)
    store.secrets.set(p.id, "chiave-di-prova-123")
    store.set_active(p.id)
    return p


@pytest.fixture
def active_profile(store):
    return make_active_profile(store)


@pytest.fixture
def gateway(store, active_profile, fake_provider):
    from foliant.modules.ai.config import SendLog
    from foliant.modules.ai.engine import Gateway
    waits = []
    gw = Gateway(store, SendLog(), provider_factory=lambda p: fake_provider, sleep=lambda s: waits.append(s))
    gw.waits = waits
    return gw


@pytest.fixture
def job(gateway):
    from foliant.modules.ai.engine import Job
    return Job(gateway)


@pytest.fixture
def text_pdf(tmp_path):
    """PDF con testo diverso su ogni pagina."""
    import pymupdf

    def make(pages=3, name="doc.pdf", texts=None, labels=None, metadata=None):
        doc = pymupdf.open()
        for i in range(pages):
            page = doc.new_page()
            text = texts[i] if texts else f"Contenuto unico della pagina {i + 1} parola{i + 1}"
            page.insert_textbox(pymupdf.Rect(72, 72, 520, 760), text, fontsize=11)
        if labels:
            doc.set_page_labels(labels)
        if metadata:
            doc.set_metadata(metadata)
        path = tmp_path / name
        doc.save(str(path))
        doc.close()
        return str(path)

    return make


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    """Documenti modificati dai test: niente richiesta di salvataggio alla chiusura della finestra."""
    window = getattr(item, "funcargs", {}).get("window")
    if window is None:
        return (yield)
    services = getattr(window.ctx, "ai_services", None)
    for w in list(getattr(services, "workers", [])):
        w.cancel()
        w.wait(5)
    for view in list(window.views()):
        view.document._saved_state = view.document._state
    return (yield)
