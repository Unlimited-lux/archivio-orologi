"""Avvio dell'applicazione."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .core import engine
from .i18n import detect_language, set_language


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
