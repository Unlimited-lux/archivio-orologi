"""Riservatezza: Foliant non conserva su disco, tra un avvio e l'altro, nomi, percorsi o cartelle
dei file aperti, modificati o salvati, né impronte (hash) che li identifichino.

Qui stanno le parti del nucleo che lo garantiscono anche fuori dal codice di Foliant:

- `prepare()`: i dialoghi di file sono quelli di Qt, non quelli nativi (GTK, portale, KDE), che
  registrerebbero i file scelti nella cronologia del sistema (~/.local/share/recently-used.xbel);
- `install(app)`: il dialogo di file di Qt, quando viene distrutto, scrive le cartelle visitate
  in ~/.config/QtProject.conf (gruppo «FileDialog»: «history», «lastVisited», «shortcuts»). Un
  filtro degli eventi fotografa queste chiavi quando un dialogo nasce e le rimette com'erano
  appena il dialogo è distrutto (anche per QFileDialog.getOpenFileName & co. usati dai moduli);
  all'uscita le si rimette come all'avvio, per sicurezza. I valori delle altre applicazioni Qt
  restano, come le preferenze dell'aspetto del dialogo (vista, colonne), che non hanno percorsi;
- `cleanup_legacy(settings)`: una volta sola, cancella i dati salvati dalle versioni precedenti
  (recenti, documenti fissati, ultima cartella, guide per documento; cronologia dei dialoghi di
  file; voci di Foliant in recently-used.xbel e nei documenti recenti di KDE).

L'ultima cartella usata e le guide dei documenti restano solo in memoria durante la sessione.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QSettings, Qt
from PySide6.QtWidgets import QFileDialog

CLEANUP_KEY = "privacy/cleanup"
CLEANUP_VERSION = 1
# chiavi di Foliant (QSettings) con percorsi o impronte di percorsi, scritte dalle versioni precedenti
LEGACY_KEYS = ("recent", "recentPinned", "lastDirectory", "view/guides")
QT_GROUP = "FileDialog"
QT_PATH_KEYS = ("history", "lastVisited")      # cronologia delle cartelle visitate
# chiavi del gruppo con cartelle: le altre (viewMode, sidebarWidth, treeViewHeader, qtVersion)
# sono preferenze dell'aspetto del dialogo, senza percorsi, e restano
QT_DIALOG_KEYS = QT_PATH_KEYS + ("shortcuts",)  # «shortcuts»: cartelle nella barra laterale
_GUARDED = "foliantPrivacyGuard"


# ------------------------------------------------------------------ dialoghi di file di Qt
def _qt_settings() -> QSettings:
    return QSettings(QSettings.Scope.UserScope, "QtProject")


def file_dialog_state(keys=None) -> dict:
    """Valori del gruppo «FileDialog» di QtProject.conf (impostazioni condivise dalle app Qt),
    solo `keys` se indicate."""
    s = _qt_settings()
    s.beginGroup(QT_GROUP)
    try:
        return {key: s.value(key) for key in s.childKeys() if keys is None or key in keys}
    finally:
        s.endGroup()


def restore_file_dialog_state(snapshot: dict, keys=None) -> None:
    """Rimette il gruppo «FileDialog» com'era in `snapshot`: toglie le chiavi aggiunte e
    rimette i valori cambiati (solo `keys`, se indicate). Non tocca ciò che non è cambiato."""
    try:
        s = _qt_settings()
        s.beginGroup(QT_GROUP)
        changed = False
        for key in s.childKeys():
            if (keys is None or key in keys) and key not in snapshot:
                s.remove(key)
                changed = True
        for key, value in snapshot.items():
            if (keys is None or key in keys) and s.value(key) != value:
                s.setValue(key, value)
                changed = True
        s.endGroup()
        if changed:
            s.sync()
    except Exception:            # impostazioni illeggibili o non scrivibili: nulla da fare
        pass


class _FileDialogGuard(QObject):
    """Filtro degli eventi dell'applicazione: per ogni QFileDialog (anche quelli creati dai
    metodi statici, in C++) fotografa le chiavi con cartelle del gruppo «FileDialog» al primo
    evento e le ripristina quando il dialogo viene distrutto (`destroyed` arriva dopo il
    salvataggio di Qt in ~QFileDialog). Costo: meno di un microsecondo per evento."""

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QFileDialog) and not obj.property(_GUARDED):
            obj.setProperty(_GUARDED, True)
            snapshot = file_dialog_state(QT_DIALOG_KEYS)
            obj.destroyed.connect(lambda *_a, s=snapshot: restore_file_dialog_state(s, QT_DIALOG_KEYS))
        return False


def prepare() -> None:
    """Prima di creare QApplication (e comunque prima di ogni dialogo): dialoghi di file di Qt,
    mai quelli nativi del sistema, che registrano i file scelti nella cronologia del desktop."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)


def install(app) -> None:
    """Protezione dei dialoghi di file (idempotente: si può chiamare da ogni finestra)."""
    prepare()
    if app is None or getattr(app, "_foliant_privacy", None) is not None:
        return
    guard = _FileDialogGuard(app)
    app.installEventFilter(guard)
    start = file_dialog_state(QT_DIALOG_KEYS)
    app.aboutToQuit.connect(lambda: restore_file_dialog_state(start, QT_DIALOG_KEYS))
    app._foliant_privacy = guard


# ------------------------------------------------------------------ dati delle versioni precedenti
def cleanup_legacy(settings: QSettings) -> bool:
    """Cancella i dati con nomi o percorsi di file salvati dalle versioni precedenti, in modo
    silenzioso; solo chiavi e file noti di Foliant, ogni errore viene ignorato.

    Le chiavi di Foliant si tolgono a ogni avvio, se ci sono (costa nulla). I file condivisi con
    il sistema e con le altre applicazioni (QtProject.conf, recently-used.xbel, documenti recenti
    di KDE) si puliscono una volta sola: segno `privacy/cleanup` nelle impostazioni, senza
    percorsi. Restituisce True se quella pulizia è stata fatta ora."""
    keys = settings.allKeys()
    for key in LEGACY_KEYS:
        if any(k == key or k.startswith(key + "/") for k in keys):
            settings.remove(key)
    try:
        if int(settings.value(CLEANUP_KEY, 0) or 0) >= CLEANUP_VERSION:
            return False
    except (TypeError, ValueError):
        pass
    try:
        s = _qt_settings()
        s.beginGroup(QT_GROUP)
        for key in QT_PATH_KEYS:
            s.remove(key)
        s.endGroup()
        s.sync()
    except Exception:
        pass
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    clean_recently_used(data / "recently-used.xbel")
    clean_kde_recent(data / "RecentDocuments")
    settings.setValue(CLEANUP_KEY, CLEANUP_VERSION)
    return True


_BOOKMARK = re.compile(r"[ \t]*<bookmark\b[^>]*>.*?</bookmark>[ \t]*\n?", re.S)
_APPLICATION = re.compile(r"[ \t]*<(?:[\w-]+:)?application\b[^>]*?/>[ \t]*\n?", re.S)
_ATTR = re.compile(r'\b(name|exec)\s*=\s*"([^"]*)"')


def _is_foliant(app_tag: str) -> bool:
    return any("foliant" in value.lower() for _k, value in _ATTR.findall(app_tag))


def clean_recently_used(path: Path) -> bool:
    """Toglie dalla cronologia dei file recenti del desktop (formato XBEL di GTK) le voci
    registrate da Foliant: le applicazioni «foliant» e i segnalibri rimasti senza applicazioni.
    Il resto del file (condiviso con le altre applicazioni) resta com'è."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    def bookmark(m: re.Match) -> str:
        block = m.group(0)
        apps = _APPLICATION.findall(block)
        ours = [a for a in apps if _is_foliant(a)]
        if not ours:
            return block
        if len(ours) == len(apps):
            return ""
        return _APPLICATION.sub(lambda a: "" if _is_foliant(a.group(0)) else a.group(0), block)

    cleaned = _BOOKMARK.sub(bookmark, text)
    if cleaned == text:
        return False
    tmp = path.with_name(path.name + ".foliant-tmp")
    try:
        tmp.write_text(cleaned, encoding="utf-8")
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def clean_kde_recent(folder: Path) -> int:
    """Documenti recenti di KDE (un file .desktop per documento): cancella quelli aperti con Foliant."""
    removed = 0
    try:
        entries = list(folder.glob("*.desktop"))
    except OSError:
        return 0
    for entry in entries:
        try:
            lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if any(line.startswith("X-KDE-LastOpenedWith=") and "foliant" in line.lower() for line in lines):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed
