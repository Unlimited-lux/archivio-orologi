"""Generazione di elementi grafici con l'IA (AI-25), senza interfaccia.

- `can_generate(profile)`: la funzione è disponibile solo con un profilo che ha un
  modello per immagini su un endpoint compatibile OpenAI (`POST {base}/images/generations`).
- `generate_images(...)`: varianti PNG (bytes) richieste con `response_format: b64_json`.
  Gli errori HTTP passano dalla stessa classificazione del fornitore (`AIError`); un rifiuto
  per le regole sui contenuti diventa `AIError(REFUSED)` (messaggio dedicato, AI-16).
- Elaborazione locale: `has_transparency`, `remove_uniform_background`, `with_opacity`.
- `Library`: elementi generati in `data_dir()/grafica` (PNG + metadati JSON).
- Applicazione al documento, ognuna in UN `document.change()` (un solo passo di annulla):
  `apply_as_stamp` crea un'annotazione timbro (Stamp) con l'immagine come aspetto
  (`page.add_stamp_annot(rect, stamp=png)`, che conserva la trasparenza); solo se il motore
  non la accetta ripiega su un'immagine nel contenuto della pagina (`insert_image`, overlay).
  L'annotazione è la scelta preferita: resta un oggetto selezionabile/eliminabile come gli
  altri timbri e non altera il contenuto della pagina.
  `apply_as_watermark` (sopra il contenuto, con opacità) e `apply_as_background` (sotto).
- `vector_text_stamp(text, color)`: timbro testuale vettoriale composto localmente (PDF di
  una pagina), applicabile con `apply_vector_stamp`.

Nessun accesso alla rete fuori da `generate_images`; nessun widget.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .config import data_dir, ensure_private_dir, write_private
from .providers import BAD_REQUEST, INTERRUPTED, REFUSED, TIMEOUT, AIError, mask_secret

KINDS = ("watermark", "background", "stamp", "sticker")
KIND_LABELS = {
    "watermark": "Filigrana",
    "background": "Sfondo",
    "stamp": "Timbro",
    "sticker": "Adesivo",
}
STYLES = ("", "piatto", "acquerello", "linea", "fotografico", "vintage", "minimalista")
SIZES = ("1024x1024", "1536x1024", "1024x1536")
DEFAULT_SIZE = "1024x1024"
MIN_VARIANTS, MAX_VARIANTS = 1, 4
GENERATION_TIMEOUT = 180.0        # la generazione di immagini può richiedere minuti

# kind -> indicazione aggiunta alla descrizione (inviata al modello, in inglese)
_KIND_HINTS = {
    "watermark": "A simple, light watermark graphic suitable to be printed semi-transparent over a document page.",
    "background": "A full-page background image for a document, low contrast so that text stays readable.",
    "stamp": "A rubber-stamp style emblem, centered, isolated, with clean edges.",
    "sticker": "A sticker-style illustration, centered, isolated, with clean edges.",
}

_REFUSAL_WORDS = ("content_policy", "content policy", "safety", "moderation", "policy_violation",
                  "violat", "not allowed", "rejected")


# ================================================================ disponibilità
def can_generate(profile) -> tuple[bool, str]:
    """(disponibile, spiegazione se non lo è)."""
    if profile is None:
        return False, _("Configura un profilo nelle impostazioni dell'Assistente IA per generare immagini.")
    if not (getattr(profile, "image_model", "") or "").strip():
        return False, _("Il profilo attivo non ha un modello per la generazione di immagini. "
                        "Indicalo nelle impostazioni del profilo per usare questa funzione.")
    if getattr(profile, "protocol", "") != "openai":
        return False, _("La generazione di immagini richiede un endpoint compatibile OpenAI "
                        "con un modello per immagini: il fornitore del profilo attivo non la supporta.")
    return True, ""


# ==================================================================== richiesta
def compose_prompt(prompt: str, kind: str = "stamp", style: str = "", colors: str = "",
                   transparent: bool = False) -> str:
    """Descrizione inviata al modello: testo dell'utente + indicazioni di tipo, stile, colori."""
    parts = [prompt.strip()]
    hint = _KIND_HINTS.get(kind)
    if hint:
        parts.append(hint)
    if style:
        parts.append(f"Style: {style}.")
    if colors:
        parts.append(f"Colors: {colors}.")
    if transparent:
        parts.append("Transparent background, no backdrop.")
    return " ".join(p for p in parts if p)


def _open_long(provider, req: urllib.request.Request, timeout: float):
    """Come `provider._open`, ma con un'attesa adatta ai tempi di generazione."""
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())
    except urllib.error.HTTPError as e:
        raise provider._http_error(e) from e
    except urllib.error.URLError as e:
        raise provider._url_error(e) from e
    except (TimeoutError, socket.timeout) as e:
        raise AIError(TIMEOUT, str(e), host=provider.host) from e
    except OSError as e:
        from .providers import NETWORK
        raise AIError(NETWORK, mask_secret(str(e), provider.api_key), host=provider.host) from e


def _is_refusal(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in _REFUSAL_WORDS)


def generate_images(provider, model: str, prompt: str, style: str = "", colors: str = "",
                    transparent: bool = False, n: int = 1, size: str = DEFAULT_SIZE, *,
                    kind: str = "stamp", timeout: float | None = None) -> list[bytes]:
    """Genera `n` (1–4) varianti PNG tramite `POST {base}/images/generations`.

    `provider` è un `OpenAICompatProvider` (o compatibile: `_url`, `_headers`, `_http_error`,
    `_url_error`, `host`, `api_key`). Solleva `AIError` (REFUSED per i rifiuti sui contenuti).
    """
    n = max(MIN_VARIANTS, min(MAX_VARIANTS, int(n)))
    body: dict = {
        "model": model,
        "prompt": compose_prompt(prompt, kind, style, colors, transparent),
        "n": n,
        "size": size or DEFAULT_SIZE,
        "response_format": "b64_json",
    }
    if transparent:
        body["background"] = "transparent"
    headers = dict(provider._headers())
    headers["Accept"] = "application/json"
    req = urllib.request.Request(provider._url("images/generations"), data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    wait = timeout if timeout is not None else max(GENERATION_TIMEOUT, float(getattr(provider, "idle_timeout", 0) or 0))
    try:
        resp = _open_long(provider, req, wait)
    except AIError as e:
        if e.kind == BAD_REQUEST and _is_refusal(e.detail):
            raise AIError(REFUSED, e.detail, host=e.host) from e
        raise
    try:
        with resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw)
    except (ValueError, OSError) as e:
        kind_ = TIMEOUT if isinstance(e, (TimeoutError, socket.timeout)) else INTERRUPTED
        raise AIError(kind_, mask_secret(str(e), provider.api_key), host=provider.host) from e
    if isinstance(data, dict) and data.get("error"):
        detail = mask_secret(json.dumps(data["error"])[:1000], provider.api_key)
        raise AIError(REFUSED if _is_refusal(detail) else BAD_REQUEST, detail, host=provider.host)
    items = data.get("data") if isinstance(data, dict) else None
    images: list[bytes] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if not b64:
            continue
        try:
            raw_img = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
        png = to_png(raw_img)
        if png:
            images.append(png)
    if not images:
        if isinstance(data, dict) and _is_refusal(json.dumps(data)[:2000]):
            raise AIError(REFUSED, "no image returned", host=provider.host)
        raise AIError(INTERRUPTED, "no image in response", host=provider.host)
    return images


# ============================================================ elaborazione locale
def _pixmap(png: bytes) -> pymupdf.Pixmap:
    with ENGINE_LOCK:
        pix = pymupdf.Pixmap(png)
        if pix.colorspace is None or pix.colorspace.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if not pix.alpha:
            pix = pymupdf.Pixmap(pix, 1)
        return pix


def to_png(data: bytes) -> bytes | None:
    """Converte un'immagine qualsiasi (PNG/JPEG/WebP…) in PNG; None se non leggibile."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    try:
        with ENGINE_LOCK:
            pix = pymupdf.Pixmap(data)
            if pix.colorspace is not None and pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            return pix.tobytes("png")
    except Exception:  # noqa: BLE001 - immagine non valida
        return None


def image_size(png: bytes) -> tuple[int, int]:
    with ENGINE_LOCK:
        pix = pymupdf.Pixmap(png)
        return pix.width, pix.height


def _rgba(png: bytes):
    """(array numpy h×w×4 con alfa NON premoltiplicato, larghezza, altezza).

    I campioni del motore con canale alfa sono premoltiplicati: qui si riportano a colori
    «diritti», così confronti di colore e modifiche dell'alfa sono corretti.
    """
    import numpy as np
    pix = _pixmap(png)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4).astype(np.float64)
    a = arr[:, :, 3:4]
    rgb = np.where(a > 0, np.clip(arr[:, :, :3] * 255.0 / np.maximum(a, 1), 0, 255), 0)
    out = np.concatenate([rgb, a], axis=2).round().astype(np.uint8)
    return out, pix.width, pix.height


def _png_from_rgba(arr) -> bytes:
    """PNG da un array RGBA con alfa non premoltiplicato."""
    import numpy as np
    h, w = arr.shape[:2]
    f = arr.astype(np.float64)
    f[:, :, :3] = f[:, :, :3] * f[:, :, 3:4] / 255.0       # il motore vuole campioni premoltiplicati
    data = f.round().astype(np.uint8).tobytes()
    with ENGINE_LOCK:
        pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, data, True)
        return pix.tobytes("png")


def has_transparency(png: bytes) -> bool:
    """True se almeno un pixel non è del tutto opaco."""
    with ENGINE_LOCK:
        pix = pymupdf.Pixmap(png)
        if not pix.alpha:
            return False
    arr, _w, _h = _rgba(png)
    return bool((arr[:, :, 3] < 255).any())


def background_color(png: bytes, tolerance: int = 24) -> tuple[int, int, int] | None:
    """Colore di sfondo uniforme stimato dai quattro angoli; None se gli angoli differiscono."""
    import numpy as np
    arr, w, h = _rgba(png)
    k = max(1, min(w, h) // 50)
    corners = [arr[:k, :k], arr[:k, w - k:], arr[h - k:, :k], arr[h - k:, w - k:]]
    samples = np.concatenate([c[:, :, :3].reshape(-1, 3) for c in corners]).astype(int)
    ref = np.median(samples, axis=0)
    if np.abs(samples - ref).max() > tolerance:
        return None
    return tuple(int(x) for x in ref)


def remove_uniform_background(png: bytes, tolerance: int = 24) -> bytes:
    """Rende trasparente lo sfondo uniforme (colore degli angoli), con bordi sfumati.

    I pixel entro `tolerance` dal colore di sfondo diventano trasparenti; tra `tolerance`
    e `2·tolerance` l'opacità cresce gradualmente. Se lo sfondo non è uniforme l'immagine
    resta invariata.
    """
    import numpy as np
    bg = background_color(png, tolerance)
    if bg is None:
        return png
    arr, _w, _h = _rgba(png)
    dist = np.abs(arr[:, :, :3].astype(int) - np.array(bg)).max(axis=2)
    tol = max(1, int(tolerance))
    factor = np.clip((dist - tol) / float(tol), 0.0, 1.0)
    arr[:, :, 3] = (arr[:, :, 3].astype(float) * factor).astype(np.uint8)
    return _png_from_rgba(arr)


def with_opacity(png: bytes, opacity: float) -> bytes:
    """Moltiplica il canale alfa per `opacity` (0–1)."""
    import numpy as np
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity >= 0.999:
        return png
    arr, _w, _h = _rgba(png)
    arr[:, :, 3] = (arr[:, :, 3].astype(float) * opacity).astype(np.uint8)
    return _png_from_rgba(arr)


# ==================================================================== libreria
@dataclass
class LibraryItem:
    id: str
    prompt: str
    kind: str
    created: str
    path: str = ""

    def png(self) -> bytes:
        return Path(self.path).read_bytes()


class Library:
    """Libreria personale degli elementi generati (`data_dir()/grafica`)."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else data_dir() / "grafica"

    def save(self, png: bytes, prompt: str, kind: str, **extra) -> LibraryItem:
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r}")
        ensure_private_dir(self.root)
        item_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        path = self.root / f"{item_id}.png"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        meta = {"prompt": prompt, "kind": kind,
                "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        meta.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
        write_private(self.root / f"{item_id}.json", json.dumps(meta, ensure_ascii=False, indent=1))
        return LibraryItem(item_id, prompt, kind, meta["created"], str(path))

    def items(self, kind: str | None = None) -> list[LibraryItem]:
        if not self.root.is_dir():
            return []
        out: list[LibraryItem] = []
        for meta_path in self.root.glob("*.json"):
            png_path = meta_path.with_suffix(".png")
            if not png_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if kind and meta.get("kind") != kind:
                continue
            out.append(LibraryItem(meta_path.stem, str(meta.get("prompt", "")), str(meta.get("kind", "")),
                                   str(meta.get("created", "")), str(png_path)))
        out.sort(key=lambda it: it.created, reverse=True)
        return out

    def get(self, item_id: str) -> LibraryItem | None:
        return next((it for it in self.items() if it.id == item_id), None)

    def remove(self, item_id: str) -> bool:
        if not re.fullmatch(r"[\w-]+", item_id or ""):
            return False
        found = False
        for suffix in (".png", ".json"):
            p = self.root / f"{item_id}{suffix}"
            try:
                p.unlink()
                found = True
            except FileNotFoundError:
                pass
        return found


def item_to_dict(item: LibraryItem) -> dict:
    return asdict(item)


# ============================================================ applicazione
def _unrotated(page: pymupdf.Page) -> pymupdf.Rect:
    """Riquadro visibile della pagina in coordinate non ruotate (quelle di insert_image)."""
    return page.rect * page.derotation_matrix


def _fit(area: pymupdf.Rect, w: int, h: int, scale: float = 1.0) -> pymupdf.Rect:
    s = min(area.width / max(1, w), area.height / max(1, h)) * scale
    cw, ch = w * s, h * s
    c = (area.tl + area.br) / 2
    return pymupdf.Rect(c.x - cw / 2, c.y - ch / 2, c.x + cw / 2, c.y + ch / 2)


def apply_as_stamp(document, page: int, rect, png: bytes, *, label: str | None = None) -> str:
    """Applica l'immagine come timbro sulla pagina `page` in un solo passo annullabile.

    Restituisce "annot" (annotazione timbro con aspetto immagine) oppure "image" (ripiego:
    immagine nel contenuto della pagina, sopra il testo).
    """
    rect = pymupdf.Rect(rect)
    with document.change(label or _("Timbro generato")) as pdf:
        pg = pdf[page]
        try:
            pg.add_stamp_annot(rect, stamp=png)   # aspetto = immagine, trasparenza conservata
            return "annot"
        except Exception:  # noqa: BLE001 - motore senza timbri immagine: ripiego robusto
            pg.insert_image(rect, stream=png, overlay=True, keep_proportion=True)
            return "image"


def apply_as_watermark(document, pages, png: bytes, opacity: float = 0.3, *, scale: float = 0.6,
                       label: str | None = None) -> None:
    """Filigrana centrata sopra il contenuto delle pagine indicate, con l'opacità scelta."""
    data = with_opacity(png, opacity)
    w, h = image_size(png)
    with document.change(label or _("Filigrana generata")) as pdf:
        xref = 0
        for i in pages:
            pg = pdf[i]
            r = _fit(pg.rect, w, h, scale) * pg.derotation_matrix   # centrata e diritta come si vede
            if xref:
                pg.insert_image(r, xref=xref, overlay=True, rotate=pg.rotation)
            else:
                xref = pg.insert_image(r, stream=data, overlay=True, rotate=pg.rotation)


def apply_as_background(document, pages, png: bytes, *, label: str | None = None) -> None:
    """Sfondo a pagina intera sotto il contenuto delle pagine indicate."""
    with document.change(label or _("Sfondo generato")) as pdf:
        xref = 0
        for i in pages:
            pg = pdf[i]
            r = _unrotated(pg)
            if xref:
                pg.insert_image(r, xref=xref, overlay=False, keep_proportion=False, rotate=pg.rotation)
            else:
                xref = pg.insert_image(r, stream=png, overlay=False, keep_proportion=False, rotate=pg.rotation)


# ============================================================ timbro vettoriale
def _rgb(color) -> tuple[float, float, float]:
    if isinstance(color, str):
        c = color.strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
            raise ValueError(f"colore non valido: {color!r}")
        return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    vals = tuple(color)[:3]
    if any(v > 1 for v in vals):
        return tuple(v / 255 for v in vals)  # type: ignore[return-value]
    return tuple(float(v) for v in vals)  # type: ignore[return-value]


def vector_text_stamp(text: str, color="#c0392b", *, fontsize: float = 28) -> bytes:
    """Timbro testuale vettoriale (PDF di una pagina): cornice doppia arrotondata + testo."""
    rgb = _rgb(color)
    text = (text or "").strip() or "?"
    with ENGINE_LOCK:
        font = pymupdf.Font("hebo")
        lines = text.upper().splitlines()
        width = max(font.text_length(line, fontsize) for line in lines)
        pad = fontsize * 0.7
        w = width + 2 * pad
        h = fontsize * 1.25 * len(lines) + 2 * pad - fontsize * 0.25
        doc = pymupdf.open()
        page = doc.new_page(width=w, height=h)
        outer = pymupdf.Rect(1.5, 1.5, w - 1.5, h - 1.5)
        inner = pymupdf.Rect(5, 5, w - 5, h - 5)
        page.draw_rect(outer, color=rgb, width=3, radius=0.12)
        page.draw_rect(inner, color=rgb, width=1, radius=0.1)
        y = pad + fontsize * 0.8
        for line in lines:
            lw = font.text_length(line, fontsize)
            page.insert_text(((w - lw) / 2, y), line, fontname="hebo", fontsize=fontsize, color=rgb)
            y += fontsize * 1.25
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        return data


def vector_stamp_png(stamp_pdf: bytes, dpi: int = 300) -> bytes:
    """Anteprima/variante raster (PNG trasparente) di un timbro vettoriale."""
    with ENGINE_LOCK:
        doc = pymupdf.open("pdf", stamp_pdf)
        try:
            return doc[0].get_pixmap(dpi=dpi, alpha=True).tobytes("png")
        finally:
            doc.close()


def apply_vector_stamp(document, page: int, rect, stamp_pdf: bytes, *, label: str | None = None) -> None:
    """Inserisce il timbro vettoriale nel contenuto della pagina (un passo di annulla)."""
    rect = pymupdf.Rect(rect)
    with document.change(label or _("Timbro testuale")) as pdf:
        src = pymupdf.open("pdf", stamp_pdf)
        try:
            pdf[page].show_pdf_page(rect, src, 0, overlay=True, keep_proportion=True)
        finally:
            src.close()
