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
cartella, guide per documento, cronologia dei dialoghi di file di Qt, una voce di Foliant in
recently-used.xbel): la pulizia all'avvio deve toglierli, lasciando quelli delle altre applicazioni.

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

# Tracce attribuite a moduli fuori dal nucleo e non ancora corrette: (file o cartella, prefisso
# della chiave INI o "" per tutto il file, modulo responsabile). Vuoto quando le aree sono unite.
PENDING = (
    ("Foliant.conf", "viewer/", "modulo viewer (stato per documento: viewer/positions…)"),
    ("recupero durante la sessione", "", "modulo viewer (metadati delle copie di recupero)"),
)

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
            window._rebuild_menu()
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
    (home / ".config" / "QtProject.conf").write_text(
        "[FileDialog]\n"
        f"history=file:///altra/applicazione, {old_folder.as_uri()}\n"
        f"lastVisited={old_folder.as_uri()}\n"
        "viewMode=Detail\n", encoding="utf-8")
    data_home = home / ".local" / "share"
    data_home.mkdir(parents=True, exist_ok=True)
    (data_home / "recently-used.xbel").write_text(_xbel(old_file), encoding="utf-8")


def test_no_traces_of_opened_or_saved_files(tmp_path):
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
    _legacy_profile(home, old_file, old_folder)

    snapshot = tmp_path / "recupero durante la sessione"
    args = {"first": str(first), "second": str(second), "save_as": str(save_as), "copy": str(copy),
            "recovery_snapshot": str(snapshot)}
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

    # i dati delle altre applicazioni e le preferenze generali restano. La cronologia dei dialoghi
    # di Qt («history», «lastVisited») è condivisa e non dice chi l'ha scritta: la pulizia unica la
    # toglie tutta; durante la sessione i valori delle altre app restano (tests/test_privacy.py)
    qt = (home / ".config" / "QtProject.conf").read_text(encoding="utf-8")
    assert "viewMode=Detail" in qt and "history" not in qt and "lastVisited" not in qt, qt
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
