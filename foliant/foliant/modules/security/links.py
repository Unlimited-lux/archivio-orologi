"""Conferma dei collegamenti esterni e blocco delle azioni pericolose (SEC-27).

Il nucleo gestisce il clic sui collegamenti in `foliant.ui.tools.follow_link`;
il modulo lo sostituisce con una versione che applica le regole di sicurezza e
delega al nucleo i collegamenti interni (vedi CORE_REQUESTS.md).
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pymupdf
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from foliant.api import ENGINE_LOCK, _

ALLOWED_SCHEMES = {"http", "https", "mailto"}
EXECUTABLE_SUFFIXES = {
    ".sh", ".bash", ".zsh", ".csh", ".run", ".bin", ".appimage", ".desktop", ".exe", ".com", ".bat", ".cmd",
    ".msi", ".scr", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".jar", ".py", ".pl", ".rb", ".app", ".deb",
    ".rpm", ".snap", ".flatpak", ".apk", ".dmg", ".pkg", ".lnk", ".hta", ".cpl", ".reg",
}
SETTINGS_KEY = "security/allowed_domains"


def scheme_of(uri: str) -> str:
    return urlsplit(uri.strip()).scheme.lower()


def host_of(uri: str) -> str:
    parts = urlsplit(uri.strip())
    if parts.scheme.lower() == "mailto":
        addr = parts.path
        return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    return (parts.hostname or "").lower()


def _scripts(label: str) -> set[str]:
    out = set()
    for c in label:
        if c.isalpha():
            name = unicodedata.name(c, "")
            out.add(name.split(" ")[0] if name else "?")
    return out


def host_warnings(host: str) -> tuple[str, list[str]]:
    """Forma punycode del dominio (se diversa) e avvisi di possibile contraffazione."""
    warnings: list[str] = []
    try:
        puny = host.encode("idna").decode("ascii") if host else ""
    except UnicodeError:
        puny = host
    if puny and puny != host:
        if any(len(_scripts(label)) > 1 for label in host.split(".")) or any(
                ord(c) > 127 for c in host):
            warnings.append(_("Il dominio contiene caratteri di alfabeti diversi e potrebbe imitare un altro "
                              "indirizzo. Forma reale: {p}").format(p=puny))
    return puny if puny != host else "", warnings


def is_executable(path: str) -> bool:
    p = Path(path)
    if p.suffix.lower() in EXECUTABLE_SUFFIXES:
        return True
    try:
        return p.is_file() and os.access(p, os.X_OK) and p.suffix.lower() != ".pdf"
    except OSError:
        return False


def classify(uri: str) -> tuple[bool, str]:
    """(consentito con conferma, motivo del blocco)."""
    scheme = scheme_of(uri)
    if scheme in ALLOWED_SCHEMES:
        return True, ""
    if scheme == "file":
        path = urlsplit(uri).path
        if path.lower().endswith(".pdf"):
            return True, ""
        return False, _("Il collegamento apre un file locale non PDF.")
    if not scheme:
        return False, _("Indirizzo non valido.")
    return False, _("Lo schema «{s}:» non è consentito per sicurezza.").format(s=scheme)


def allowed_domains(settings: QSettings | None) -> set[str]:
    if settings is None:
        return set()
    value = settings.value(SETTINGS_KEY, [])
    if isinstance(value, str):
        value = [value] if value else []
    return {str(v).lower() for v in (value or [])}


def allow_domain(settings: QSettings | None, host: str) -> None:
    if settings is None or not host:
        return
    domains = allowed_domains(settings) | {host.lower()}
    settings.setValue(SETTINGS_KEY, sorted(domains))


def visible_text_mismatch(view, link: dict, host: str) -> str:
    """Avviso se il testo visibile del collegamento indica un dominio diverso."""
    if not host or view is None or link.get("page") is None and "from" not in link:
        return ""
    try:
        page_index = view.current_page if "page_index" not in link else link["page_index"]
        with ENGINE_LOCK:
            text = view.document.pdf[page_index].get_textbox(link["from"]) or ""
    except Exception:
        return ""
    import re
    m = re.search(r"(?:https?://)?((?:[\w-]+\.)+[a-z]{2,})", text.strip().lower())
    if not m:
        return ""
    shown = m.group(1)
    shown = shown[4:] if shown.startswith("www.") else shown
    real = host[4:] if host.startswith("www.") else host
    if shown != real and not real.endswith("." + shown):
        return _("Il testo indica un indirizzo diverso dalla destinazione ({shown}).").format(shown=shown)
    return ""


def ask_open(parent, uri: str, host: str, warnings: list[str]) -> str:
    """Chiede conferma: restituisce "open", "always" o "cancel"."""
    lines = [_("Il documento vuole aprire questo indirizzo:"), "", uri]
    if host:
        lines += ["", _("Dominio: {h}").format(h=host)]
    for w in warnings:
        lines += ["", "⚠ " + w]
    lines += ["", _("«Sì a tutti» apre e non chiede più per questo dominio.")]
    answer = QMessageBox.question(
        parent, _("Collegamento esterno"), "\n".join(lines),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.YesToAll | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    if answer == QMessageBox.StandardButton.Yes:
        return "open"
    if answer == QMessageBox.StandardButton.YesToAll:
        return "always"
    return "cancel"


def open_url(uri: str) -> None:
    QDesktopServices.openUrl(QUrl(uri))


def _settings_of(view) -> QSettings | None:
    win = view.window() if view is not None else None
    return getattr(win, "settings", None)


def _base_uri(view) -> str:
    try:
        with ENGINE_LOCK:
            pdf = view.document.pdf
            kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "URI/Base")
        return val if kind == "string" else ""
    except Exception:
        return ""


def follow_external(view, link: dict, original) -> None:
    """Gestisce i collegamenti che escono da Foliant; gli altri vanno al nucleo."""
    kind = link.get("kind")
    if kind == pymupdf.LINK_URI and link.get("uri"):
        uri = link["uri"].strip()
        if not scheme_of(uri):
            base = _base_uri(view)
            if base:
                uri = urljoin(base, uri)
            elif view.document.path is not None:
                uri = urljoin(view.document.path.parent.as_uri() + "/", uri)
        ok, reason = classify(uri)
        if not ok:
            QMessageBox.warning(view, _("Collegamento bloccato"),
                                _("Il collegamento è stato bloccato:\n\n{u}\n\n{r}").format(u=uri, r=reason))
            return
        if scheme_of(uri) == "file":
            path = urlsplit(uri).path
            _open_pdf(view, path)
            return
        host = host_of(uri)
        settings = _settings_of(view)
        puny, warnings = host_warnings(host)
        mismatch = visible_text_mismatch(view, link, host)
        if mismatch:
            warnings.append(mismatch)
        if host and host in allowed_domains(settings) and not warnings:
            open_url(uri)
            return
        choice = ask_open(view, uri, puny or host, warnings)
        if choice == "always":
            allow_domain(settings, host)
        if choice in ("open", "always"):
            open_url(uri)
        return
    if kind == pymupdf.LINK_LAUNCH:
        target = link.get("file") or link.get("uri") or ""
        QMessageBox.warning(view, _("Azione bloccata"),
                            _("Il documento vuole avviare un programma o aprire un file:\n\n{f}\n\n"
                              "Foliant non avvia programmi indicati dai documenti.").format(f=target or "—"))
        return
    if kind == pymupdf.LINK_GOTOR and link.get("file"):
        target = link["file"]
        if view.document.path is not None and not os.path.isabs(target):
            target = str(view.document.path.parent / target)
        _open_pdf(view, target)
        return
    original(view, link)


def _open_pdf(view, path: str) -> None:
    if not path.lower().endswith(".pdf") or is_executable(path):
        QMessageBox.warning(view, _("Collegamento bloccato"),
                            _("Il collegamento apre un file che non è un PDF:\n\n{f}").format(f=path))
        return
    answer = QMessageBox.question(view, _("Apri altro file"),
                                  _("Il collegamento apre un altro documento:\n\n{f}\n\nAprirlo in una nuova scheda?")
                                  .format(f=path))
    if answer == QMessageBox.StandardButton.Yes:
        win = view.window()
        if hasattr(win, "open_path"):
            win.open_path(path)


_installed = False


def install() -> None:
    """Sostituisce una sola volta il gestore dei collegamenti del nucleo."""
    global _installed
    if _installed:
        return
    from foliant.ui import tools as core_tools
    original = core_tools.follow_link

    def follow_link(view, link):
        try:
            follow_external(view, link, original)
        except Exception as e:   # un errore del modulo non deve chiudere l'applicazione
            view.status(_("Collegamento non gestito: {e}").format(e=e))

    core_tools.follow_link = follow_link
    _installed = True
