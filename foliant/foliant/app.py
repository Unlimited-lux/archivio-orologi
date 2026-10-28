"""Avvio dell'applicazione."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .core import engine
from .i18n import _, detect_language, set_language

PAGE_FRAGMENT = re.compile(r"^(?P<path>.+)#page=(?P<page>\d+)$", re.IGNORECASE)


@dataclass
class Options:
    """Riga di comando analizzata (NFR-31)."""

    files: list[tuple[str, int | None]] = field(default_factory=list)   # percorso assoluto, pagina da 1
    new_window: bool = False
    command: str | None = None
    unknown: list[str] = field(default_factory=list)                    # opzioni sconosciute, ignorate

    def request(self) -> dict:
        """Richiesta per l'istanza già aperta (vedi ui/instance.py)."""
        return {"files": [[p, n] for p, n in self.files], "new_window": self.new_window, "command": self.command}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foliant", description=_("Editor PDF per Linux."), allow_abbrev=False)
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help=_("file da aprire; «file.pdf#page=N» lo apre alla pagina N"))
    parser.add_argument("-p", "--page", type=int, metavar="N", help=_("apre il primo file alla pagina N"))
    parser.add_argument("--new-window", action="store_true",
                        help=_("apre una nuova finestra anche se Foliant è già in esecuzione"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create", dest="command", action="store_const", const="create-pdf",
                       help=_("apre «Crea PDF da file»"))
    group.add_argument("--merge", dest="command", action="store_const", const="merge",
                       help=_("apre «Unisci file»"))
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def _target(arg: str, cwd: str) -> tuple[str, int | None]:
    """Percorso assoluto e pagina di un argomento: «a.pdf», «a.pdf#page=3» o «file:///a.pdf#page=3»."""
    page = None
    if arg.startswith("file:"):
        url = QUrl(arg)
        m = re.fullmatch(r"page=(\d+)", url.fragment(), re.IGNORECASE)
        page = int(m.group(1)) if m else None
        path = url.toLocalFile() or arg
    else:
        path = arg
        m = PAGE_FRAGMENT.match(arg)
        if m and not os.path.exists(os.path.join(cwd, os.path.expanduser(arg))):
            path, page = m.group("path"), int(m.group("page"))
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(path))), (max(1, page) if page is not None else None)


def parse_args(args: list[str], cwd: str | None = None) -> Options:
    """Analizza gli argomenti (senza il nome del programma). Le opzioni sconosciute non diventano
    file da aprire: finiscono in `unknown`. Opzioni errate (es. «--page x»): messaggio e uscita."""
    parser = build_parser()
    ns, unknown = parser.parse_known_intermixed_args(args)
    if ns.page is not None and ns.page < 1:
        parser.error(_("la pagina deve essere un numero maggiore di zero"))
    cwd = cwd or os.getcwd()
    files = [_target(a, cwd) for a in ns.files]
    if ns.page is not None and files and files[0][1] is None:
        files[0] = (files[0][0], ns.page)
    return Options(files=files, new_window=ns.new_window, command=ns.command, unknown=list(unknown))


def install_qt_translations(app: QApplication) -> None:
    """Testi standard di Qt (Sì/No, Chiudi, Annulla, nomi dei tasti) nella lingua dell'interfaccia.

    In italiano i nomi dei tasti disegnati da Qt (menu, suggerimenti) seguono quelli
    dell'interfaccia (UX-18: «Maiusc», «Pag giù»…), non quelli del catalogo di Qt («Shift», «Pag↓»).
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    from .i18n import language
    from .ui.dialogs import _KEY_NAMES_IT

    class KeyNames(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):
            if context == "QShortcut" and source in _KEY_NAMES_IT:
                return _KEY_NAMES_IT[source]
            return None                    # stringa nulla: Qt passa ai cataloghi successivi

    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    keep = getattr(app, "_foliant_translators", [])
    for name in ("qtbase", "qt"):
        tr = QTranslator(app)
        if tr.load(f"{name}_{language()}", folder):
            app.installTranslator(tr)
            keep.append(tr)
    if language() == "it":
        keys = KeyNames(app)
        app.installTranslator(keys)        # l'ultimo installato è il primo consultato
        keep.append(keys)
    app._foliant_translators = keep


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setDesktopFileName("foliant")
    if any(a in ("-h", "--help", "--version") for a in argv[1:]):
        set_language(QSettings().value("language") or detect_language())
        parse_args(argv[1:])            # stampa l'aiuto o la versione e termina, senza interfaccia
        return 0
    engine.configure()
    app = QApplication(argv)
    settings = QSettings()
    set_language(settings.value("language") or detect_language())
    install_qt_translations(app)
    options = parse_args(app.arguments()[1:])      # senza le opzioni di Qt (-platform, -style…)
    for opt in options.unknown:
        print(_("foliant: opzione sconosciuta ignorata: {o}").format(o=opt), file=sys.stderr)

    # Istanza singola: se Foliant è già aperto gli si consegnano file e comandi e si esce.
    from .ui.instance import InstanceServer, instance_name, open_request, send_request
    name = instance_name()              # "" senza una cartella privata: ogni avvio fa da sé
    if name and send_request(name, options.request()):
        return 0
    if name:
        server = InstanceServer(name, app)
        # collegato prima di listen(): una richiesta che arriva mentre si aprono i file di questo
        # avvio (domanda della password, avviso di errore: cicli di eventi annidati) non va persa
        server.requestReceived.connect(lambda request: open_request(request, settings))
        if not server.listen() and send_request(name, options.request()):
            return 0                    # un'altra istanza è partita nel frattempo

    from .ui import theme
    from .ui.main_window import MainWindow, register_window

    theme.apply(app, settings.value("theme", "system"))
    window = register_window(MainWindow(settings))
    window.show()
    open_request(options.request(), settings, window)
    if options.unknown:
        window.statusBar().showMessage(
            _("Opzioni sconosciute ignorate: {o}").format(o=" ".join(options.unknown)), 10000)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
