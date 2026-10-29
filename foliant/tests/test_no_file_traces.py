"""Regola del committente: Foliant non conserva su disco, tra un avvio e l'altro, nomi, percorsi
o cartelle dei file aperti, modificati o salvati (né in chiaro, né come impronta del percorso o
del contenuto), né cronologie che li contengano.

Test a scatola chiusa: un processo Foliant vero (foliant.app.main, con tutti i moduli) in un
profilo utente isolato (HOME, XDG_*, FOLIANT_RECOVERY_DIR, XDG_RUNTIME_DIR temporanei) apre un
PDF dalla riga di comando e uno dal dialogo «Apri», li modifica (anche con le guide), aspetta il
salvataggio automatico di recupero, salva, «Salva con nome» e «Salva una copia» in un'altra
cartella, passa per tutte le modalità e la Home, chiude la finestra. Poi si cerca nel profilo
(compresi QtProject.conf e recently-used.xbel) ogni traccia dei nomi e delle cartelle: in
UTF-8, UTF-16 (LE/BE) e come impronta MD5/SHA-1/SHA-256 del percorso o del contenuto.
I file che il test salva esplicitamente stanno fuori dal profilo e non vengono cercati.

Il profilo parte con i dati di una versione precedente (recenti, documenti fissati, ultima
cartella, guide per documento, una voce di Foliant in recently-used.xbel): la pulizia all'avvio
deve toglierli, lasciando quelli delle altre applicazioni. La cronologia dei dialoghi di file di
Qt (QtProject.conf) è condivisa e non dice chi l'ha scritta: Foliant non tocca le voci che c'erano
(qui solo di un'altra applicazione) né quelle che un'altra applicazione scrive mentre Foliant è
aperto, e toglie solo ciò che scrivono i suoi dialoghi.

Su Windows le impostazioni di Foliant e la cronologia dei dialoghi di Qt stanno nel registro
(HKCU\\Software\\Foliant, HKCU\\Software\\QtProject): il test ne salva una copia, le svuota, vi
scrive i dati della «versione precedente» e alla fine le esporta in un file del profilo, cercato
come gli altri, poi le rimette com'erano. Il profilo isolato è fatto da %APPDATA%, %LOCALAPPDATA% e
%TEMP%; si cercano anche le cronologie della shell di Windows (elementi recenti, RecentDocs, MRU
dei dialoghi comuni) e le Jump List.

Le tracce dei moduli ancora da correggere (PENDING) non fanno fallire il test ma lo segnano come
«xfail» con il loro elenco: quando i moduli saranno corretti il test passerà da solo, e PENDING
andrà svuotato.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pymupdf
import pytest

import foliant

ROOT = Path(foliant.__file__).resolve().parent.parent      # cartella del progetto (contiene foliant/)
WINDOWS = sys.platform == "win32"
REG_FOLIANT = r"Software\Foliant"
REG_QT = r"Software\QtProject"
# cronologie della shell di Windows (condivise): esportate per cercarvi i nomi dei file
REG_SHELL = (r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs",
             r"Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32",
             r"Software\Microsoft\Windows\CurrentVersion\Search\RecentApps")

# Tracce attribuite a moduli fuori dal nucleo e non ancora corrette: (file o cartella, prefisso
# della chiave INI o "" per tutto il file, modulo responsabile). Vuoto quando le aree sono unite.
PENDING: tuple = ()        # tutti i moduli corretti: nessuna traccia ammessa

CHILD = textwrap.dedent(r'''
    import json, os, sys, time, traceback
    args = json.loads(sys.argv[1])
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox

    # finestre modali che non sono dialoghi di file: risposta prudente, senza bloccare il test
    QMessageBox.exec = lambda self, *a, **k: QMessageBox.StandardButton.Cancel
    for _n in ("question", "warning", "information", "critical"):
        setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    QDialog.exec = lambda self, *a, **k: 0                       # solo le chiamate da Python
    QMenu.exec = lambda self, *a, **k: None
    QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))

    report = {"errors": [], "steps": []}
    answers = []                       # risposte dei dialoghi di file veri (QFileDialog di Qt)

    def answer_file_dialogs():
        """Risponde come un utente: cartella, nome nel campo del nome, «Apri»/«Salva»."""
        for w in QApplication.topLevelWidgets():
            if not (isinstance(w, QFileDialog) and w.isVisible()):
                continue
            target = w.property("zqTarget")
            if not target:
                if not answers:
                    report["errors"].append("dialogo di file inatteso: " + w.windowTitle())
                    w.reject()
                    continue
                target = answers.pop(0)
                w.setProperty("zqTarget", target)
            w.setDirectory(os.path.dirname(target))
            edit = w.findChild(QLineEdit, "fileNameEdit")
            if edit is None:
                report["errors"].append("campo del nome non trovato nel dialogo di file")
                w.reject()
                continue
            edit.setText(os.path.basename(target))
            w.accept()                 # se il dialogo non accetta ancora, si riprova al giro dopo

    def spin(ms):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def scenario():
        app = QApplication.instance()
        try:
            from foliant.ui.main_window import app_windows
            window = app_windows()[-1]
            spin(300)
            views = window.views()
            assert views and str(views[0].document.path) == args["first"], "il PDF della riga di comando non è aperto"
            report["steps"].append("aperto da riga di comando")
            answers.append(args["second"])
            window.open_dialog()                               # «Apri» con il dialogo vero
            assert any(str(v.document.path) == args["second"] for v in window.views()), "«Apri» non ha aperto il file"
            report["steps"].append("aperto dal dialogo")
            # un'altra applicazione Qt (un altro processo) scrive la sua cronologia dei dialoghi
            import subprocess
            subprocess.run([sys.executable, "-c", args["other_app"]], check=True, timeout=60)
            report["steps"].append("altra applicazione")
            for view in window.views():
                window.activate_view(view)
                with view.document.change("Prova") as pdf:
                    pdf[0].insert_text((72, 300), "Modifica di prova", fontsize=14)
                if hasattr(view, "add_guide"):
                    view.add_guide("h", 120.0)
            spin(2500)                                         # salvataggio automatico di recupero (1 s)
            # le copie di recupero sono ammesse durante la sessione, ma senza nome né percorso:
            # se ne fa una fotografia fuori dal profilo, cercata come il profilo
            import shutil
            recovery = os.environ["FOLIANT_RECOVERY_DIR"]
            if os.path.isdir(recovery):
                shutil.copytree(recovery, args["recovery_snapshot"], dirs_exist_ok=True)
            report["steps"].append("copie di recupero: %d" % sum(len(f) for _d, _s, f in os.walk(recovery)))
            first = next(v for v in window.views() if str(v.document.path) == args["first"])
            window.activate_view(first)
            assert window.save_view(first), "Salva non riuscito"
            answers.append(args["save_as"])
            assert window.save_view(first, save_as=True), "Salva con nome non riuscito"
            answers.append(args["copy"])
            assert window.save_copy(first), "Salva una copia non riuscito"
            report["steps"].append("salvato")
            for mode_id in list(window.modes):
                window.set_mode(mode_id)
                spin(150)
            window.show_home()
            spin(150)
            for page in ("nuovo", "apri", "informazioni", "gestisci", "opzioni"):
                window.show_backstage(page)
                spin(50)
            window.hide_backstage()
            for view in window.views():
                if view.document.is_modified:
                    window.save_view(view)
            report["steps"].append("modalità")
            window.close()
            spin(300)
            report["closed"] = bool(window._closed) if hasattr(window, "_closed") else True
        except BaseException:
            report["errors"].append(traceback.format_exc())
        finally:
            QApplication.closeAllWindows()
            QTimer.singleShot(200, app.quit)

    _orig_exec = QApplication.exec

    def _exec(self=None):
        poll = QTimer(QApplication.instance())
        poll.timeout.connect(answer_file_dialogs)
        poll.start(50)
        QTimer.singleShot(0, scenario)
        QTimer.singleShot(240000, QApplication.instance().quit)
        try:
            return _orig_exec()
        except TypeError:
            return _orig_exec(QApplication.instance())

    QApplication.exec = _exec
    from foliant.app import main
    code = main(["foliant", args["first"]])
    print("ZQ7-REPORT" + json.dumps(report))
    sys.exit(code)
''')


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for n in range(2):
        doc.new_page().insert_text((72, 100), f"Pagina {n + 1}", fontsize=24)
    doc.save(path)
    doc.close()


def _needles(paths: dict[str, Path], tokens: dict[str, str], contents: dict[str, bytes]) -> dict[bytes, str]:
    """Sequenze di byte vietate nel profilo → descrizione."""
    found: dict[bytes, str] = {}
    for label, token in tokens.items():
        for enc in ("utf-8", "utf-16-le", "utf-16-be"):
            found[token.encode(enc)] = f"{label} ({enc})"
    for label, path in paths.items():
        for form in {str(path), os.path.realpath(path), path.as_uri()}:
            for algo in ("md5", "sha1", "sha256"):
                digest = hashlib.new(algo, form.encode("utf-8")).hexdigest()
                found[digest.encode()] = f"{algo} del percorso {label}"
                found[digest.upper().encode()] = f"{algo} del percorso {label}"
    for label, data in contents.items():
        for algo in ("md5", "sha1", "sha256"):
            digest = hashlib.new(algo, data).hexdigest()
            found[digest.encode()] = f"{algo} del contenuto {label}"
    return found


def _xbel(old_file: Path) -> str:
    """Cronologia dei file recenti del desktop: una voce di Foliant e una di un'altra applicazione."""
    entry = """  <bookmark href="{href}" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info>
      <metadata owner="http://freedesktop.org">
        <mime:mime-type type="{mime}"/>
        <bookmark:applications>
          <bookmark:application name="{app}" exec="&apos;{app} %u&apos;" modified="2026-01-01T00:00:00Z" count="1"/>
        </bookmark:applications>
      </metadata>
    </info>
  </bookmark>
"""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<xbel version="1.0"\n'
            '      xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"\n'
            '      xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info"\n>\n'
            + entry.format(href=old_file.as_uri(), mime="application/pdf", app="foliant")
            + entry.format(href="file:///home/altro-programma.txt", mime="text/plain", app="gedit")
            + "</xbel>\n")


def _legacy_profile(home: Path, old_file: Path, old_folder: Path) -> None:
    """Dati lasciati da una versione precedente di Foliant, più quelli di altre applicazioni."""
    old_sha1 = hashlib.sha1(str(old_file).encode("utf-8")).hexdigest()
    guides = json.dumps(json.dumps([["h", 10.0, None]]))          # stringa JSON tra virgolette (INI)
    (home / ".config" / "Foliant" / "Foliant.conf").write_text(
        "[General]\n"
        f"recent={old_file}\n"
        f"recentPinned={old_file}\n"
        f"lastDirectory={old_folder}\n"
        "theme=dark\n\n"
        "[view]\n"
        f"guides\\{old_sha1}={guides}\n\n"
        # preferenza generale senza percorsi: recupero automatico ogni secondo, per esercitarlo
        "[viewer]\nautosave_seconds=1\n", encoding="utf-8")
    # cronologia dei dialoghi di Qt di un'altra applicazione (le voci delle versioni precedenti di
    # Foliant qui non si distinguerebbero e resterebbero: vedi README, «Riservatezza»)
    (home / ".config" / "QtProject.conf").write_text(
        "[FileDialog]\n"
        "history=file:///altra/applicazione\n"
        "lastVisited=file:///altra/applicazione\n"
        "viewMode=Detail\n", encoding="utf-8")
    data_home = home / ".local" / "share"
    data_home.mkdir(parents=True, exist_ok=True)
    (data_home / "recently-used.xbel").write_text(_xbel(old_file), encoding="utf-8")


# ------------------------------------------------------------------ registro (Windows)
def _reg_export(path: str) -> dict:
    """Albero HKCU\\`path` → {sottochiave relativa: {nome: (tipo, valore)}}; {} se manca."""
    import winreg
    out: dict = {}

    def walk(rel: str) -> None:
        full = path + ("\\" + rel if rel else "")
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, full)
        except OSError:
            return
        with key:
            values, i = {}, 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                values[name] = (kind, value)
                i += 1
            out[rel] = values
            subs, i = [], 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
        for sub in subs:
            walk(rel + "\\" + sub if rel else sub)

    walk("")
    return out


def _reg_delete(path: str) -> None:
    """Cancella HKCU\\`path` con tutte le sottochiavi (se esiste)."""
    import winreg
    for rel in sorted(_reg_export(path), key=lambda r: -r.count("\\")):   # prima le più profonde
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path + ("\\" + rel if rel else ""))
        except OSError:
            pass


def _reg_import(path: str, tree: dict) -> None:
    import winreg
    for rel, values in sorted(tree.items()):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + ("\\" + rel if rel else "")) as key:
            for name, (kind, value) in values.items():
                winreg.SetValueEx(key, name, 0, kind, value)


def _reg_dump(target: Path, paths) -> None:
    """Esporta le chiavi in un file «INI» del profilo: [chiave] e righe nome=valore; i valori
    binari restano byte grezzi (es. nomi in UTF-16 nelle cronologie della shell)."""
    with open(target, "wb") as fh:
        for path in paths:
            for rel, values in sorted(_reg_export(path).items()):
                fh.write(f"[{path}\\{rel}]\n".replace("\\", "/").encode("utf-8"))
                for name, (_kind, value) in values.items():
                    if isinstance(value, bytes):
                        data = value
                    elif isinstance(value, list):
                        data = "\x00".join(map(str, value)).encode("utf-8")
                    else:
                        data = str(value).encode("utf-8")
                    fh.write(name.encode("utf-8") + b"=" + data + b"\n")


def _legacy_registry(old_file: Path, old_folder: Path) -> None:
    """Come `_legacy_profile`, ma nel registro: impostazioni di una versione precedente e
    cronologia dei dialoghi di Qt di un'altra applicazione."""
    from PySide6.QtCore import QSettings
    old_sha1 = hashlib.sha1(str(old_file).encode("utf-8")).hexdigest()
    s = QSettings("HKEY_CURRENT_USER\\" + REG_FOLIANT + "\\Foliant", QSettings.Format.NativeFormat)
    for key, value in (("recent", str(old_file)), ("recentPinned", str(old_file)),
                       ("lastDirectory", str(old_folder)), ("theme", "dark"),
                       (f"view/guides/{old_sha1}", json.dumps([["h", 10.0, None]])), ("viewer/autosave_seconds", 1)):
        s.setValue(key, value)
    s.sync()
    q = QSettings(QSettings.Scope.UserScope, "QtProject")
    q.beginGroup("FileDialog")
    q.setValue("history", "file:///altra/applicazione")
    q.setValue("lastVisited", "file:///altra/applicazione")
    q.setValue("viewMode", "Detail")
    q.endGroup()
    q.sync()


def _windows_recent_dirs() -> list[Path]:
    """Elementi recenti e Jump List dell'utente vero (la shell non segue %APPDATA%)."""
    base = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Recent"
    return [base, base / "AutomaticDestinations", base / "CustomDestinations"]


def test_no_traces_of_opened_or_saved_files(tmp_path):
    if not WINDOWS:
        return _run_scenario(tmp_path)
    backup = {path: _reg_export(path) for path in (REG_FOLIANT, REG_QT)}
    try:
        for path in backup:
            _reg_delete(path)
        return _run_scenario(tmp_path)
    finally:
        for path, tree in backup.items():
            _reg_delete(path)
            _reg_import(path, tree)


def _run_scenario(tmp_path):
    tok = {k: uuid.uuid4().hex for k in ("cartella", "documento", "secondo", "altra_cartella", "salvato", "copia",
                                         "vecchio", "vecchia_cartella")}
    folder = tmp_path / f"zq7 cartella {tok['cartella']}"
    other = tmp_path / f"zq7 altra {tok['altra_cartella']}"
    folder.mkdir()
    other.mkdir()
    first = folder / f"zq7-segreto {tok['documento']}.pdf"
    second = folder / f"zq7-secondo {tok['secondo']}.pdf"
    save_as = other / f"zq7-salvato {tok['salvato']}.pdf"
    copy = other / f"zq7-copia {tok['copia']}.pdf"
    old_folder = tmp_path / f"zq7 vecchia {tok['vecchia_cartella']}"           # non esiste più
    old_file = old_folder / f"zq7-vecchio {tok['vecchio']}.pdf"
    _make_pdf(first)
    _make_pdf(second)
    original = {"documento": first.read_bytes(), "secondo": second.read_bytes()}

    profile = tmp_path / "profilo"
    home = profile / "home"
    env = dict(os.environ)
    for var in [v for v in env if v.startswith(("XDG_", "FOLIANT_"))]:
        env.pop(var)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local" / "share"),
               XDG_STATE_HOME=str(home / ".local" / "state"), XDG_CACHE_HOME=str(home / ".cache"),
               XDG_RUNTIME_DIR=str(profile / "run"), FOLIANT_RECOVERY_DIR=str(profile / "recupero"),
               QT_QPA_PLATFORM="offscreen", FOLIANT_SYNC_TASKS="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join([str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])))
    for d in (home / ".config" / "Foliant", profile / "run"):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(profile / "run", 0o700)
    if WINDOWS:
        # profilo isolato: dati e configurazione in %APPDATA%, stato e cache in %LOCALAPPDATA%,
        # file temporanei in %TEMP% (cercati anch'essi); impostazioni nel registro
        roaming, local, temp = home / "AppData" / "Roaming", home / "AppData" / "Local", profile / "temp"
        for d in (roaming, local, temp):
            d.mkdir(parents=True, exist_ok=True)
        env.update(APPDATA=str(roaming), LOCALAPPDATA=str(local), TEMP=str(temp), TMP=str(temp))
        _legacy_registry(old_file, old_folder)
        recent_before = {p: set(os.listdir(p)) if p.is_dir() else set() for p in _windows_recent_dirs()}
    else:
        _legacy_profile(home, old_file, old_folder)

    snapshot = tmp_path / "recupero durante la sessione"
    other_app = ("from PySide6.QtCore import QSettings\n"
                 "s = QSettings(QSettings.Scope.UserScope, 'QtProject')\n"
                 "s.beginGroup('FileDialog')\n"
                 "s.setValue('history', ['file:///altra/applicazione', 'file:///altra/durante'])\n"
                 "s.setValue('lastVisited', 'file:///altra/durante')\n"
                 "s.endGroup()\ns.sync()\n")
    args = {"first": str(first), "second": str(second), "save_as": str(save_as), "copy": str(copy),
            "recovery_snapshot": str(snapshot), "other_app": other_app}
    r = subprocess.run([sys.executable, "-c", CHILD, json.dumps(args)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=280)
    marker = [line for line in r.stdout.splitlines() if line.startswith("ZQ7-REPORT")]
    assert marker, f"Foliant non ha completato lo scenario (codice {r.returncode})\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}"
    report = json.loads(marker[-1][len("ZQ7-REPORT"):])
    assert not report["errors"], "\n".join(report["errors"] + report["steps"]) + "\n" + r.stderr[-3000:]
    assert report.get("closed", False), "la finestra non si è chiusa"
    assert save_as.exists() and copy.exists(), "i file salvati esplicitamente non ci sono"
    # nessun nome di file nemmeno nei messaggi del processo (possono finire nel giornale di sistema)
    assert not [t for t in tok.values() if t in r.stderr], r.stderr[-3000:]

    # i dati delle altre applicazioni e le preferenze generali restano: anche la cronologia dei
    # dialoghi di Qt che un'altra applicazione ha scritto mentre Foliant era aperto
    if WINDOWS:
        from PySide6.QtCore import QSettings
        q = QSettings(QSettings.Scope.UserScope, "QtProject")
        q.beginGroup("FileDialog")
        assert q.value("viewMode") == "Detail" and q.value("lastVisited") == "file:///altra/durante"
        assert list(q.value("history")) == ["file:///altra/applicazione", "file:///altra/durante"], q.value("history")
        q.endGroup()
        s = QSettings("HKEY_CURRENT_USER\\" + REG_FOLIANT + "\\Foliant", QSettings.Format.NativeFormat)
        assert s.value("theme") == "dark"
        # registro di Foliant e di Qt, cronologie della shell: esportati nel profilo e cercati
        _reg_dump(profile / "registro-foliant.ini", (REG_FOLIANT, REG_QT))
        _reg_dump(profile / "registro-shell.ini", REG_SHELL)
        with open(profile / "elementi-recenti.txt", "w", encoding="utf-8") as fh:
            for d, before in recent_before.items():
                for name in sorted((set(os.listdir(d)) if d.is_dir() else set()) - before):
                    fh.write(f"{d.name}/{name}\n")
                    lnk = d / name
                    if lnk.is_file():
                        fh.flush()
                        with open(lnk, "rb") as src, open(profile / f"recente-{len(name)}-{abs(hash(name))}.bin", "wb") as dst:
                            dst.write(src.read())
    else:
        qt = (home / ".config" / "QtProject.conf").read_text(encoding="utf-8")
        assert "viewMode=Detail" in qt and "lastVisited=file:///altra/durante" in qt, qt
        assert "history=file:///altra/applicazione, file:///altra/durante" in qt, qt
        xbel = (home / ".local" / "share" / "recently-used.xbel").read_text(encoding="utf-8")
        assert "altro-programma.txt" in xbel and "gedit" in xbel, xbel
        assert "theme=dark" in (home / ".config" / "Foliant" / "Foliant.conf").read_text(encoding="utf-8")

    paths = {"documento": first, "secondo": second, "cartella": folder, "altra cartella": other,
             "salvato": save_as, "copia": copy, "vecchio": old_file, "vecchia cartella": old_folder}
    contents = dict(original)
    contents.update({"salvato": save_as.read_bytes(), "copia": copy.read_bytes(),
                     "documento salvato": first.read_bytes()})
    needles = _needles(paths, tok, contents)

    traces: list[tuple[str, str]] = []
    for root in (profile, snapshot):
        traces += _scan(root, tok, needles)
    pending = sorted({f"{where}: {what} → {_pending(where)}" for where, what in traces if _pending(where)})
    others = sorted({f"{where}: {what}" for where, what in traces if not _pending(where)})
    assert not others, ("tracce dei file nel profilo:\n" + "\n".join(others + pending)
                        + "\n(scenario: " + "; ".join(report["steps"]) + ")")
    if pending:
        pytest.xfail("restano solo tracce di moduli non ancora corretti:\n" + "\n".join(pending))


def _pending(where: str) -> str | None:
    for place, key_prefix, owner in PENDING:
        if place in where and (not key_prefix or f"[{key_prefix}" in where):
            return owner
    return None


def _scan(root: Path, tok: dict[str, str], needles: dict[bytes, str]) -> list[tuple[str, str]]:
    """(dove, cosa) per ogni traccia; nei file INI «dove» comprende la chiave: «file [gruppo/chiave]»."""
    traces: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            for token_label, token in tok.items():
                if token in name:
                    traces.append((str(Path(dirpath, name).relative_to(root.parent)), f"nome con «{token_label}»"))
        for name in filenames:
            path = Path(dirpath, name)
            if not path.is_file() or path.is_symlink():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            where = str(path.relative_to(root.parent))
            for needle, label in needles.items():
                if needle in data:
                    keys = _ini_keys(data, needle) if name.endswith((".conf", ".ini")) else []
                    for key in keys or [""]:
                        traces.append((f"{where} [{key}]" if key else where, label))
    return traces


def _ini_keys(data: bytes, needle: bytes) -> list[str]:
    """Chiavi («gruppo/chiave») delle righe di un file INI che contengono `needle`."""
    group, keys = "", []
    for line in data.split(b"\n"):
        text = line.decode("utf-8", "replace").strip()
        if text.startswith("[") and text.endswith("]"):
            group = text[1:-1]
            continue
        if needle in line:
            key = text.split("=", 1)[0].strip().replace("\\", "/")
            keys.append(key if group in ("", "General") else f"{group}/{key}")
    return keys
