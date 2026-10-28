"""Avvio dell'applicazione."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .core import engine
from .i18n import detect_language, set_language


def install_qt_translations(app: QApplication) -> None:
    """Testi standard di Qt (Sì/No, Chiudi, Annulla, nomi dei tasti) nella lingua dell'interfaccia."""
    from PySide6.QtCore import QLibraryInfo, QTranslator

    from .i18n import language
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in ("qtbase", "qt"):
        tr = QTranslator(app)
        if tr.load(f"{name}_{language()}", folder):
            app.installTranslator(tr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    engine.configure()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setDesktopFileName("foliant")
    app = QApplication(argv)
    settings = QSettings()
    set_language(settings.value("language") or detect_language())
    install_qt_translations(app)

    from .ui import theme
    from .ui.main_window import MainWindow

    theme.apply(app, settings.value("theme", "system"))
    window = MainWindow(settings)
    window.show()
    for arg in argv[1:]:
        if not arg.startswith("-"):
            window.open_path(arg)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
