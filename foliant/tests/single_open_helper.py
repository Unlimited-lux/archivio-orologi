"""Processo Foliant vero per i test dell'apertura unica (tests/test_single_open_windows.py).

Avvio: `python single_open_helper.py PERCORSO`. Apre il file come dalla riga di comando
(`open_request`, lo stesso percorso di `foliant FILE`) in una finestra propria, senza il canale
dell'istanza singola: così due processi provano davvero ad aprire lo stesso file. Ogni stato è una
riga JSON su stdout; i comandi arrivano su stdin, uno per riga:

    open PERCORSO        riapre (stesso processo, stessa finestra)
    newwindow PERCORSO   apre in una nuova finestra dello stesso processo
    save                 salva il documento aperto
    saveas PERCORSO      «Salva con nome» verso PERCORSO
    close                chiude la scheda del documento
    state                stato attuale
    quit                 esce normalmente

I messaggi modali (avvisi, domande) vengono chiusi da soli e riportati in «messages».
"""

from __future__ import annotations

import json
import os
import sys
import threading


def main() -> int:
    target = sys.argv[1]
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from foliant.core import engine
    from foliant.core import platform
    from foliant.ui import privacy

    QApplication.setApplicationName("Foliant")
    QApplication.setOrganizationName("Foliant")
    if platform.IS_WINDOWS and os.environ.get("FOLIANT_TEST_INI"):
        # come i test nel processo: impostazioni in un file della cartella del test, non nel registro
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.environ["FOLIANT_TEST_INI"])
    platform.prepare_environment(QSettings)
    engine.configure()
    privacy.prepare()
    app = QApplication([sys.argv[0]])
    settings = QSettings()
    privacy.install(app)

    from foliant.ui.instance import open_request
    from foliant.ui.main_window import MainWindow, app_windows, register_window

    messages: list[str] = []

    def close_modals():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                messages.append(" ".join(t for t in (w.text(), w.informativeText()) if t))
                w.reject()
            elif isinstance(w, QDialog) and w.isVisible() and w.isModal():
                messages.append(w.windowTitle())
                w.reject()

    modal_timer = QTimer()
    modal_timer.setInterval(50)
    modal_timer.timeout.connect(close_modals)
    modal_timer.start()

    def same(a, b) -> bool:
        try:
            return os.path.samefile(a, b)
        except OSError:
            return False

    def views_of(path):
        return [(w, v) for w in app_windows() for v in w.views()
                if v.document.path is not None and same(str(v.document.path), path)]

    current = {"path": target}

    def report(event: str, **extra):
        state = {"event": event, "pid": os.getpid(),
                 "opened": len(views_of(current["path"])) if os.path.exists(current["path"]) else 0,
                 "windows": len(app_windows()), "messages": list(messages)}
        state.update(extra)
        print(json.dumps(state), flush=True)

    window = register_window(MainWindow(settings))
    window.show()

    def first_open():
        open_request({"files": [(os.path.abspath(target), None)], "new_window": False, "command": None},
                     settings, window)
        report("opened")
    QTimer.singleShot(0, first_open)

    lines: list[str] = []
    lock = threading.Lock()

    def read_stdin():
        for line in sys.stdin:
            with lock:
                lines.append(line.rstrip("\r\n"))
        with lock:
            lines.append("quit")
    threading.Thread(target=read_stdin, daemon=True).start()

    def handle(line: str):
        cmd, _sep, arg = line.partition(" ")
        if cmd == "open":
            open_request({"files": [(os.path.abspath(arg), None)], "new_window": False, "command": None},
                         settings, window)
            report("open", path=arg, opened_arg=len(views_of(arg)))
        elif cmd == "newwindow":
            open_request({"files": [(os.path.abspath(arg), None)], "new_window": True, "command": None}, settings)
            report("newwindow", path=arg, opened_arg=len(views_of(arg)))
        elif cmd == "save":
            found = views_of(current["path"])
            ok = bool(found) and found[0][0].save_view(found[0][1])
            report("save", ok=bool(ok))
        elif cmd == "saveas":
            found = views_of(current["path"])
            ok = False
            if found:
                win, view = found[0]
                win.ctx.ask_save_path = lambda *a, **k: arg
                ok = win.save_view(view, save_as=True)
                if ok:
                    current["path"] = arg
            report("saveas", ok=bool(ok))
        elif cmd == "close":
            for win, view in views_of(current["path"]):
                win.close_view(view, ask=False)
            report("close")
        elif cmd == "state":
            report("state")
        elif cmd == "quit":
            for win in list(app_windows()):
                for view in list(win.views()):
                    win.close_view(view, ask=False)
            report("quit")
            app.quit()

    def poll():
        with lock:
            todo = lines[:]
            del lines[:]
        for line in todo:
            if line:
                handle(line)
    poll_timer = QTimer()
    poll_timer.setInterval(30)
    poll_timer.timeout.connect(poll)
    poll_timer.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
