"""Sessione dell'applicazione installata, senza schermo (QT_QPA_PLATFORM=offscreen).

Eseguita dal Foliant installato:
    Foliant.exe -I -c "exec(open(r'…\\sessione.py', encoding='utf-8').read())" file.pdf rapporto.json

Avvia Foliant come un utente (foliant.app.main con tutti i moduli e il PDF come argomento),
controlla che il documento sia aperto, lo modifica, lo salva, passa per tutte le modalità e
chiude la finestra. Il rapporto (passi ed errori, senza nomi di file) va in `rapporto.json`,
che sta fuori dal profilo dell'utente. Foliant.exe non ha una console: niente print.
"""

import json
import os
import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

pdf_path, report_path = sys.argv[1], sys.argv[2]
report = {"errors": [], "steps": []}

# finestre modali: risposta prudente, senza bloccare la prova; nessun dialogo di file atteso
QMessageBox.exec = lambda self, *a, **k: QMessageBox.StandardButton.Cancel
for _n in ("question", "warning", "information", "critical"):
    setattr(QMessageBox, _n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
QDialog.exec = lambda self, *a, **k: 0
QMenu.exec = lambda self, *a, **k: None
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))


def unexpected_file_dialogs():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QFileDialog) and w.isVisible():
            report["errors"].append("dialogo di file inatteso")
            w.reject()


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
        for _i in range(100):                        # apertura in background: fino a 10 s
            if window.views():
                break
            spin(100)
        views = window.views()
        assert views and os.path.samefile(str(views[0].document.path), pdf_path), "il PDF non è aperto"
        view = views[0]
        report["steps"].append("aperto")
        report["pages"] = view.document.page_count if hasattr(view.document, "page_count") else None
        with view.document.change("Prova dell'installatore") as pdf:
            pdf[0].insert_text((72, 60), "Controllato con Foliant", fontsize=12)
        spin(500)
        assert window.save_view(view), "Salva non riuscito"
        report["steps"].append("salvato")
        for mode_id in list(window.modes):
            window.set_mode(mode_id)
            spin(150)
        window.show_home()
        spin(150)
        report["steps"].append("modalità")
        window.close()
        spin(300)
        report["steps"].append("chiuso")
    except BaseException:
        report["errors"].append(traceback.format_exc())
    finally:
        QApplication.closeAllWindows()
        QTimer.singleShot(200, app.quit)


_orig_exec = QApplication.exec


def _exec(self=None):
    poll = QTimer(QApplication.instance())
    poll.timeout.connect(unexpected_file_dialogs)
    poll.start(100)
    QTimer.singleShot(0, scenario)
    QTimer.singleShot(180000, QApplication.instance().quit)
    try:
        return _orig_exec()
    except TypeError:
        return _orig_exec(QApplication.instance())


QApplication.exec = _exec
try:
    from foliant.app import main
    report["exit_code"] = main(["foliant", pdf_path])
except BaseException:
    report["errors"].append(traceback.format_exc())
with open(report_path, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)
