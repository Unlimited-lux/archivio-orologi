"""Attivazione dei collegamenti con conferme di sicurezza (OBJ-41).

Usata dagli strumenti del modulo (Ctrl+clic). La logica di decisione
(`decide`) è pura e testabile; le finestre sono funzioni sostituibili nei test.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QMessageBox

from foliant.api import ENGINE_LOCK, _

from . import links as L

SETTINGS_KEY = "objects/allowed_sites"
SAFE_SCHEMES = ("http", "https", "mailto", "ftp", "tel")


@dataclass
class Decision:
    action: str            # "open_url", "confirm_url", "goto", "open_pdf", "blocked", "missing", "not_found", "none"
    uri: str = ""
    host: str = ""
    page: int = -1
    path: str = ""
    message: str = ""
    mismatch: str = ""     # dominio mostrato nel testo, diverso dalla destinazione


def allowed_sites(settings) -> set[str]:
    if settings is None:
        return set()
    v = settings.value(SETTINGS_KEY, [])
    if isinstance(v, str):
        v = [v] if v else []
    return {str(x).lower() for x in (v or [])}


def allow_site(settings, host: str) -> None:
    if settings is None or not host:
        return
    sites = allowed_sites(settings)
    sites.add(host.lower())
    settings.setValue(SETTINGS_KEY, sorted(sites))


def visible_domain(text: str) -> str:
    m = re.search(r"(?:https?://)?((?:[\w-]+\.)+[a-z]{2,})", text or "", re.I)
    return m.group(1).lower() if m else ""


def _same_site(a: str, b: str) -> bool:
    a = a.lower().removeprefix("www.")
    b = b.lower().removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def decide(target: L.LinkTarget, page_count: int, doc_path: str | None, sites: set[str],
           shown_text: str = "") -> Decision:
    """Cosa fare al clic su un collegamento (senza effetti)."""
    if target.kind == L.URI:
        uri = target.uri.strip()
        scheme = uri.split(":", 1)[0].lower() if ":" in uri else ""
        if scheme not in SAFE_SCHEMES:
            return Decision("blocked", uri=uri, message=_("Schema non ammesso: il collegamento non viene aperto."))
        host = L.uri_host(uri)
        shown = visible_domain(shown_text)
        mismatch = shown if shown and host and not _same_site(shown, host) else ""
        if host and host in sites and not mismatch:
            return Decision("open_url", uri=uri, host=host)
        return Decision("confirm_url", uri=uri, host=host, mismatch=mismatch)
    if target.kind == L.PAGE:
        if 0 <= target.page < page_count:
            return Decision("goto", page=target.page)
        return Decision("not_found", message=_("Destinazione non trovata."))
    if target.kind in (L.FILE, L.LAUNCH):
        path = target.file
        if doc_path and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(doc_path)), path))
        if target.kind == L.LAUNCH or not path.lower().endswith(".pdf"):
            return Decision("blocked", path=path,
                            message=_("Per sicurezza i collegamenti che avviano file o programmi sono bloccati:\n{p}")
                            .format(p=path))
        if not os.path.exists(path):
            return Decision("missing", path=path, message=_("File collegato non trovato:\n{p}").format(p=path))
        return Decision("open_pdf", path=path, page=max(0, target.page))
    return Decision("none", message=_("Azione non supportata."))


def confirm_url(parent, uri: str, host: str, mismatch: str) -> tuple[bool, bool]:
    """Chiede conferma; restituisce (apri, consenti_sempre)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(_("Collegamento esterno"))
    text = _("Il documento vuole aprire questo indirizzo:") + f"<br><br>{uri.replace(host, '<b>' + host + '</b>', 1) if host else uri}"
    if mismatch:
        text += "<br><br>" + _("Attenzione: il testo mostra «{a}» ma il collegamento porta a «{b}».").format(
            a=mismatch, b=host)
    box.setText(text)
    cb = QCheckBox(_("Consenti sempre per questo sito"))
    if host and not mismatch:
        box.setCheckBox(cb)
    box.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
    ok = box.exec() == QMessageBox.StandardButton.Open
    return ok, ok and cb.isChecked()


def open_url(uri: str) -> None:
    QDesktopServices.openUrl(QUrl(uri))


def activate(ctx, view, page: int, point: pymupdf.Point) -> bool:
    """Attiva il collegamento sotto il punto. True se c'era un collegamento."""
    with ENGINE_LOCK:
        infos = L.page_links(view.document.pdf, page)
        page_count = view.document.pdf.page_count
        hit = None
        for info in reversed(infos):
            if info.rect.contains(point):
                hit = info
                break
        shown = ""
        if hit is not None:
            try:
                shown = view.document.pdf[page].get_textbox(hit.rect)
            except Exception:
                shown = ""
    if hit is None:
        return False
    doc_path = str(view.document.path) if view.document.path else None
    d = decide(hit.target, page_count, doc_path, allowed_sites(ctx.settings()), shown)
    if d.action == "goto":
        t = hit.target
        to = pymupdf.Point(t.left or 0, t.top or 0) if t.top is not None else None
        view.go_to_page(d.page, to)
    elif d.action == "open_url":
        open_url(d.uri)
    elif d.action == "confirm_url":
        ok, always = confirm_url(view, d.uri, d.host, d.mismatch)
        if ok:
            if always:
                allow_site(ctx.settings(), d.host)
            open_url(d.uri)
    elif d.action == "open_pdf":
        new = ctx.open_path(d.path)
        if new is not None:
            new.go_to_page(min(d.page, new.page_count - 1))
    elif d.message:
        ctx.error(_("Collegamento"), d.message)
    return True
