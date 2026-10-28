"""Fornitori: endpoint compatibile OpenAI su un server locale (loopback) e classificazione degli errori.

Nessuna richiesta lascia il computer: il server di prova gira su 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from foliant.modules.ai import providers
from foliant.modules.ai.providers import (
    AUTH, MODEL, NETWORK, QUOTA, RATE, SERVER, AIError, ChatRequest, ImagePart, Message, OpenAICompatProvider,
)


class Simulated(BaseHTTPRequestHandler):
    """Endpoint simulato: registra le richieste e risponde secondo `server.plan`."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path.endswith("/models"):
            self._json(200, {"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(("POST", self.path, dict(self.headers), body))
        plan = self.server.plan.pop(0) if self.server.plan else {"chunks": ["Ciao ", "mondo"]}
        if "status" in plan:
            headers = plan.get("headers", {})
            self._json(plan["status"], plan.get("body", {"error": {"message": "errore"}}), headers)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for c in plan["chunks"]:
                data = {"choices": [{"delta": {"content": c}, "finish_reason": None}]}
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
                if plan.get("delay"):
                    time.sleep(plan["delay"])
            fin = {"choices": [{"delta": {}, "finish_reason": plan.get("finish", "stop")}]}
            self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.server.closed.set()

    def _json(self, status, data, headers=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def server(monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Simulated)
    srv.requests = []
    srv.plan = []
    srv.closed = threading.Event()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield srv
    srv.shutdown()


def provider(server, key="chiave-di-prova-123"):
    return OpenAICompatProvider(server.url, key, "m1", connect_timeout=5, idle_timeout=5)


def test_models_and_streaming(server):
    p = provider(server)
    assert p.verify() == ["m1", "m2", "m3"]
    got = []
    req = ChatRequest("Sistema", [Message("user", "Domanda", [ImagePart(b"\x89PNG", "image/png")])], 100)
    res = p.complete(req, got.append)
    assert res.text == "Ciao mondo" and got == ["Ciao ", "mondo"] and res.stop_reason == "end"
    method, path, headers, body = server.requests[-1]
    assert path == "/v1/chat/completions" and body["stream"] is True
    assert body["messages"][0] == {"role": "system", "content": "Sistema"}
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert headers["Authorization"] == "Bearer chiave-di-prova-123"


def test_truncated_answer_marked(server):
    server.plan = [{"chunks": ["parziale"], "finish": "length"}]
    res = provider(server).complete(ChatRequest("", [Message("user", "x")]))
    assert res.truncated


@pytest.mark.parametrize("status,body,headers,kind,retry", [
    (401, {"error": "invalid key chiave-di-prova-123"}, {}, AUTH, None),
    (404, {"error": "model"}, {}, MODEL, None),
    (429, {"error": "slow down"}, {"Retry-After": "2"}, RATE, 2.0),
    (429, {"error": {"message": "insufficient_quota"}}, {}, QUOTA, None),
    (503, {"error": "busy"}, {}, SERVER, None),
])
def test_http_errors_classified(server, status, body, headers, kind, retry):
    server.plan = [{"status": status, "body": body, "headers": headers}]
    with pytest.raises(AIError) as ei:
        provider(server).complete(ChatRequest("", [Message("user", "x")]))
    assert ei.value.kind == kind and ei.value.retry_after == retry
    assert "chiave-di-prova-123" not in ei.value.detail
    assert ei.value.transient == (kind in (RATE, SERVER))


def test_cancel_closes_connection_quickly(server):
    server.plan = [{"chunks": ["a", "b", "c", "d", "e", "f"], "delay": 1.0}]
    cancel = threading.Event()
    got = []

    def on_text(t):
        got.append(t)
        if len(got) == 3:
            threading.Timer(0.01, cancel.set).start()

    t0 = None

    def mark(t):
        nonlocal t0
        on_text(t)
        if len(got) == 3:
            t0 = time.monotonic()

    res = provider(server).complete(ChatRequest("", [Message("user", "x")]), mark, cancel)
    assert res.interrupted and got == ["a", "b", "c"]
    assert time.monotonic() - t0 < 1.0


def test_network_unreachable():
    p = OpenAICompatProvider("http://127.0.0.1:9/v1", None, "m", connect_timeout=2, idle_timeout=2)
    t0 = time.monotonic()
    with pytest.raises(AIError) as ei:
        p.list_models()
    assert ei.value.kind == NETWORK
    assert time.monotonic() - t0 < 3
    assert "Rete non disponibile" in ei.value.user_message()


def test_local_and_insecure_urls():
    assert providers.is_local_url("http://localhost:11434/v1")
    assert providers.is_local_url("http://127.0.0.1:8080")
    assert not providers.is_insecure_remote("http://localhost:1/v1")
    assert providers.is_insecure_remote("http://example.org/v1")
    assert not providers.is_insecure_remote("https://example.org/v1")


def test_anthropic_error_mapping():
    import anthropic
    import httpx2 as httpx
    p = providers.AnthropicProvider(api_key="chiave-di-prova-123")
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(code, msg="x", headers=None):
        resp = httpx.Response(code, request=req, headers=headers or {}, json={"error": {"message": msg}})
        cls = {401: anthropic.AuthenticationError, 404: anthropic.NotFoundError, 429: anthropic.RateLimitError,
               400: anthropic.BadRequestError, 529: anthropic.APIStatusError}.get(code, anthropic.APIStatusError)
        return cls(msg, response=resp, body=None)

    assert p._convert(status(401, "bad key chiave-di-prova-123")).kind == AUTH
    assert "chiave-di-prova-123" not in p._convert(status(401, "bad key chiave-di-prova-123")).detail
    assert p._convert(status(404)).kind == MODEL
    e = p._convert(status(429, headers={"retry-after": "3"}))
    assert e.kind == RATE and e.retry_after == 3.0
    assert p._convert(status(400, "prompt is too long")).kind == providers.CONTEXT
    assert p._convert(status(400, "Your credit balance is too low")).kind == QUOTA
    assert p._convert(status(529, "overloaded")).kind == SERVER
    assert p._convert(anthropic.APIConnectionError(request=req)).kind == NETWORK
    assert p._convert(anthropic.APITimeoutError(request=req)).kind == providers.TIMEOUT


def test_anthropic_message_conversion():
    msgs = providers.AnthropicProvider._messages(ChatRequest("s", [
        Message("user", "testo", [ImagePart(b"abc", "image/png")]), Message("assistant", "ok")]))
    assert msgs[0]["content"][0]["type"] == "image"
    assert msgs[0]["content"][0]["source"]["media_type"] == "image/png"
    assert msgs[0]["content"][1] == {"type": "text", "text": "testo"}
    assert msgs[1] == {"role": "assistant", "content": "ok"}
