"""Fornitori di modelli linguistici (AI-01, AI-16).

Due protocolli, nessun server di Foliant:
- `anthropic`: API Messages tramite l'SDK ufficiale;
- `openai`: endpoint compatibile con le «chat completions» (anche locale),
  chiamato via HTTP con flusso SSE.

Ogni errore diventa un `AIError` con una categoria (`kind`) e un messaggio per
l'utente; il dettaglio tecnico non contiene mai la chiave.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from foliant.api import _

# ------------------------------------------------------------------- errori
NETWORK = "network"
CERT = "cert"
TIMEOUT = "timeout"
AUTH = "auth"
MODEL = "model"
RATE = "rate"
QUOTA = "quota"
REFUSED = "refused"
CONTEXT = "context"
INTERRUPTED = "interrupted"
SERVER = "server"
BAD_REQUEST = "bad_request"
LIMIT = "limit"           # limite di utilizzo locale (trattato come quota)

TRANSIENT = {RATE, SERVER, TIMEOUT}


def error_message(kind: str, host: str = "") -> str:
    return {
        NETWORK: _("Rete non disponibile o servizio irraggiungibile."),
        CERT: _("Certificato non valido per «{host}»: la connessione è stata bloccata.").format(host=host or "?"),
        TIMEOUT: _("Il fornitore non ha risposto in tempo."),
        AUTH: _("Chiave non valida o non autorizzata. Controlla le impostazioni del profilo."),
        MODEL: _("Il modello indicato non esiste o non è disponibile. Scegli un altro modello."),
        RATE: _("Troppe richieste: limite di frequenza del fornitore raggiunto. Riprova più tardi."),
        QUOTA: _("Quota o credito del fornitore esauriti."),
        REFUSED: _("Il fornitore ha rifiutato il contenuto della richiesta."),
        CONTEXT: _("Il contenuto inviato è troppo lungo per il modello. Riduci l'ambito."),
        INTERRUPTED: _("La risposta si è interrotta o non è valida."),
        SERVER: _("Errore del servizio del fornitore. Riprova più tardi."),
        BAD_REQUEST: _("Richiesta rifiutata dal fornitore."),
        LIMIT: _("Limite di utilizzo locale raggiunto. Puoi modificarlo nelle impostazioni del profilo."),
    }.get(kind, _("Errore sconosciuto del fornitore."))


def mask_secret(text: str, secret: str | None) -> str:
    if secret and len(secret) >= 4 and text:
        text = text.replace(secret, "••••")
    return text


class AIError(Exception):
    """Errore del fornitore già classificato."""

    def __init__(self, kind: str, detail: str = "", *, retry_after: float | None = None,
                 host: str = "", partial: str = ""):
        self.kind = kind
        self.detail = detail
        self.retry_after = retry_after
        self.host = host
        self.partial = partial
        super().__init__(self.user_message())

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT

    def user_message(self) -> str:
        return error_message(self.kind, self.host)

    def masked(self, secret: str | None) -> "AIError":
        self.detail = mask_secret(self.detail, secret)
        return self


# ---------------------------------------------------------------- richieste
@dataclass
class ImagePart:
    data: bytes
    media_type: str = "image/png"
    width: int = 0
    height: int = 0


@dataclass
class Message:
    role: str                         # "user" | "assistant"
    content: str
    images: list[ImagePart] = field(default_factory=list)


@dataclass
class ChatRequest:
    system: str
    messages: list[Message]
    max_tokens: int = 4096
    model: str | None = None

    def text_size(self) -> int:
        return len(self.system) + sum(len(m.content) for m in self.messages)

    def image_count(self) -> int:
        return sum(len(m.images) for m in self.messages)

    def all_text(self) -> str:
        return "\n".join([self.system] + [m.content for m in self.messages])


@dataclass
class ChatResult:
    text: str
    stop_reason: str = "end"          # end | max_tokens | interrupted
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"

    @property
    def interrupted(self) -> bool:
        return self.stop_reason == "interrupted"


TextCallback = Callable[[str], None]


class Provider:
    """Interfaccia comune. `complete()` chiama `on_text` per ogni frammento."""

    protocol = "base"

    def __init__(self, base_url: str = "", api_key: str | None = None, model: str = "",
                 connect_timeout: float = 15.0, idle_timeout: float = 60.0):
        self.base_url = base_url
        self.api_key = api_key or None
        self.model = model
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.base_url).hostname or ""

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def complete(self, request: ChatRequest, on_text: TextCallback | None = None,
                 cancel: threading.Event | None = None) -> ChatResult:
        raise NotImplementedError

    def verify(self) -> list[str]:
        """Controlla raggiungibilità, chiave e modello. Restituisce i modelli disponibili."""
        models = self.list_models()
        if self.model and models and self.model not in models:
            raise AIError(MODEL, f"model {self.model!r} not in list", host=self.host)
        return models


def is_local_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_insecure_remote(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme == "http" and not is_local_url(url)


# ---------------------------------------------------------------- Anthropic
ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]


class AnthropicProvider(Provider):
    protocol = "anthropic"

    def __init__(self, base_url: str = ANTHROPIC_URL, *args, **kwargs):
        super().__init__(base_url or ANTHROPIC_URL, *args, **kwargs)
        self._client = None

    def _sdk(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=self.api_key or "", base_url=self.base_url, max_retries=0,
                timeout=anthropic.Timeout(self.idle_timeout, connect=self.connect_timeout),
            )
        return self._client

    def _convert(self, exc: Exception) -> AIError:
        import anthropic
        detail = mask_secret(f"{type(exc).__name__}: {exc}", self.api_key)
        host = self.host
        if isinstance(exc, anthropic.APITimeoutError):
            return AIError(TIMEOUT, detail, host=host)
        if isinstance(exc, anthropic.APIConnectionError):
            cause = repr(exc.__cause__) + str(exc)
            kind = CERT if "CERTIFICATE_VERIFY_FAILED" in cause or "SSLCertVerification" in cause else NETWORK
            return AIError(kind, detail, host=host)
        if isinstance(exc, anthropic.APIStatusError):
            status = exc.status_code
            body = str(getattr(exc, "message", "")).lower()
            retry = None
            try:
                ra = exc.response.headers.get("retry-after")
                retry = float(ra) if ra else None
            except (TypeError, ValueError, AttributeError):
                retry = None
            if status in (401, 403):
                return AIError(AUTH, detail, host=host)
            if status == 404:
                return AIError(MODEL, detail, host=host)
            if status == 429:
                return AIError(RATE, detail, retry_after=retry, host=host)
            if status == 413 or "too long" in body or "context" in body and "window" in body:
                return AIError(CONTEXT, detail, host=host)
            if status == 400 and ("credit" in body or "billing" in body):
                return AIError(QUOTA, detail, host=host)
            if status >= 500:
                return AIError(SERVER, detail, retry_after=retry, host=host)
            return AIError(BAD_REQUEST, detail, host=host)
        return AIError(INTERRUPTED, detail, host=host)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self._sdk().models.list(limit=100)]
        except Exception as e:  # noqa: BLE001 - convertito in AIError
            raise self._convert(e) from e

    @staticmethod
    def _messages(request: ChatRequest) -> list[dict]:
        out = []
        for m in request.messages:
            if m.images:
                content: list[dict] = [
                    {"type": "image", "source": {"type": "base64", "media_type": im.media_type,
                                                 "data": base64.b64encode(im.data).decode("ascii")}}
                    for im in m.images
                ]
                content.append({"type": "text", "text": m.content or "."})
                out.append({"role": m.role, "content": content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def complete(self, request, on_text=None, cancel=None):
        parts: list[str] = []
        try:
            with self._sdk().messages.stream(
                model=request.model or self.model, max_tokens=request.max_tokens,
                system=request.system, messages=self._messages(request),
            ) as stream:
                for text in stream.text_stream:
                    if cancel is not None and cancel.is_set():
                        stream.close()
                        return ChatResult("".join(parts), "interrupted")
                    parts.append(text)
                    if on_text:
                        on_text(text)
                final = stream.get_final_message()
        except AIError:
            raise
        except Exception as e:  # noqa: BLE001
            err = self._convert(e)
            err.partial = "".join(parts)
            raise err from e
        stop = {"max_tokens": "max_tokens", "refusal": "refusal"}.get(final.stop_reason or "", "end")
        if stop == "refusal":
            raise AIError(REFUSED, "stop_reason=refusal", host=self.host, partial="".join(parts))
        usage = getattr(final, "usage", None)
        return ChatResult("".join(parts), stop, getattr(usage, "input_tokens", None),
                          getattr(usage, "output_tokens", None))


# ------------------------------------------------- endpoint compatibile OpenAI
class OpenAICompatProvider(Provider):
    protocol = "openai"

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _open(self, req: urllib.request.Request):
        # urllib usa il proxy di sistema e verifica sempre il certificato.
        try:
            resp = urllib.request.urlopen(req, timeout=self.connect_timeout, context=ssl.create_default_context())
        except urllib.error.HTTPError as e:
            raise self._http_error(e) from e
        except urllib.error.URLError as e:
            raise self._url_error(e) from e
        except (TimeoutError, socket.timeout) as e:
            raise AIError(TIMEOUT, str(e), host=self.host) from e
        except OSError as e:
            raise AIError(NETWORK, mask_secret(str(e), self.api_key), host=self.host) from e
        try:
            resp.fp.raw._sock.settimeout(self.idle_timeout)  # inattività durante la risposta
        except AttributeError:
            pass
        return resp

    def _url_error(self, e: urllib.error.URLError) -> AIError:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            return AIError(CERT, str(reason), host=self.host)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return AIError(TIMEOUT, str(reason), host=self.host)
        return AIError(NETWORK, mask_secret(str(reason), self.api_key), host=self.host)

    def _http_error(self, e: urllib.error.HTTPError) -> AIError:
        try:
            body = e.read().decode("utf-8", "replace")[:2000]
        except Exception:  # noqa: BLE001
            body = ""
        detail = mask_secret(f"HTTP {e.code}: {body}", self.api_key)
        low = body.lower()
        retry = None
        ra = e.headers.get("Retry-After") if e.headers else None
        try:
            retry = float(ra) if ra else None
        except ValueError:
            retry = None
        if e.code in (401, 403):
            return AIError(AUTH, detail, host=self.host)
        if e.code == 404:
            return AIError(MODEL, detail, host=self.host)
        if e.code == 429:
            if "quota" in low or "insufficient" in low or "credit" in low:
                return AIError(QUOTA, detail, host=self.host)
            return AIError(RATE, detail, retry_after=retry, host=self.host)
        if e.code == 413 or "context" in low and ("length" in low or "window" in low or "too long" in low):
            return AIError(CONTEXT, detail, host=self.host)
        if e.code == 402:
            return AIError(QUOTA, detail, host=self.host)
        if e.code >= 500:
            return AIError(SERVER, detail, retry_after=retry, host=self.host)
        if "content" in low and ("policy" in low or "filter" in low):
            return AIError(REFUSED, detail, host=self.host)
        return AIError(BAD_REQUEST, detail, host=self.host)

    def list_models(self) -> list[str]:
        req = urllib.request.Request(self._url("models"), headers=self._headers(), method="GET")
        resp = self._open(req)
        try:
            with resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (ValueError, OSError) as e:
            raise AIError(INTERRUPTED, str(e), host=self.host) from e
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        out = []
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(str(item["id"]))
            elif isinstance(item, str):
                out.append(item)
        return out

    @staticmethod
    def _messages(request: ChatRequest) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": request.system}] if request.system else []
        for m in request.messages:
            if m.images:
                parts: list[dict] = [{"type": "text", "text": m.content}]
                for im in m.images:
                    url = f"data:{im.media_type};base64," + base64.b64encode(im.data).decode("ascii")
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                out.append({"role": m.role, "content": parts})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def complete(self, request, on_text=None, cancel=None):
        body = json.dumps({
            "model": request.model or self.model, "messages": self._messages(request),
            "max_tokens": request.max_tokens, "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(self._url("chat/completions"), data=body, headers=self._headers(), method="POST")
        resp = self._open(req)
        parts: list[str] = []
        finish = None
        usage: dict = {}
        stop_watch = threading.Event()
        cancelled = threading.Event()

        def watcher() -> None:
            # Chiude la connessione appena l'utente interrompe, anche a metà di una lettura.
            while not stop_watch.wait(0.05):
                if cancel is not None and cancel.is_set():
                    cancelled.set()
                    try:
                        resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        resp.close()
                    except Exception:  # noqa: BLE001
                        pass
                    return

        t = threading.Thread(target=watcher, daemon=True)
        t.start()
        try:
            ctype = resp.headers.get("Content-Type", "")
            if "event-stream" not in ctype:
                raw = resp.read().decode("utf-8", "replace")
                data = json.loads(raw)
                msg = data["choices"][0].get("message", {})
                text = msg.get("content") or ""
                finish = data["choices"][0].get("finish_reason")
                usage = data.get("usage") or {}
                parts.append(text)
                if on_text and text:
                    on_text(text)
            else:
                for raw_line in resp:
                    if cancelled.is_set():
                        break
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("error"):
                        err = data["error"]
                        raise AIError(SERVER, mask_secret(json.dumps(err)[:500], self.api_key), host=self.host,
                                      partial="".join(parts))
                    if data.get("usage"):
                        usage = data["usage"]
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            if cancelled.is_set():
                                break
                            parts.append(text)
                            if on_text:
                                on_text(text)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
        except AIError:
            raise
        except Exception as e:  # noqa: BLE001
            if cancelled.is_set() or (cancel is not None and cancel.is_set()):
                return ChatResult("".join(parts), "interrupted")
            kind = TIMEOUT if isinstance(e, (TimeoutError, socket.timeout)) else INTERRUPTED
            raise AIError(kind, mask_secret(str(e), self.api_key), host=self.host, partial="".join(parts)) from e
        finally:
            stop_watch.set()
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        if cancelled.is_set():
            return ChatResult("".join(parts), "interrupted")
        if finish == "content_filter":
            raise AIError(REFUSED, "finish_reason=content_filter", host=self.host, partial="".join(parts))
        stop = "max_tokens" if finish == "length" else "end"
        return ChatResult("".join(parts), stop, usage.get("prompt_tokens"), usage.get("completion_tokens"))


PROTOCOLS = {"anthropic": AnthropicProvider, "openai": OpenAICompatProvider}


def make_provider(protocol: str, base_url: str, api_key: str | None, model: str,
                  connect_timeout: float = 15.0, idle_timeout: float = 60.0) -> Provider:
    cls = PROTOCOLS.get(protocol, OpenAICompatProvider)
    return cls(base_url, api_key, model, connect_timeout, idle_timeout)
