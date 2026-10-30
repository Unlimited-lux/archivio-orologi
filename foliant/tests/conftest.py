"""Configurazione comune dei test: Qt senza schermo e PDF di esempio generati al volo."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # la piattaforma «offscreen» di Qt su Windows cerca i font solo in QT_QPA_FONTDIR (altrimenti
    # nessuno: niente famiglie, misure dei testi sbagliate); con una finestra vera sono quelli di sistema
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
os.environ.setdefault("FOLIANT_SYNC_TASKS", "1")

import warnings

import foliant  # noqa: F401  (prima di pymupdf: stessa configurazione dell'app, senza analisi del layout)
import pymupdf
import pytest


@pytest.fixture(autouse=True)
def _flush_deferred_deletes():
    """Distrugge a fine test gli oggetti con deleteLater (finestre e viste chiuse).

    Senza un ciclo di eventi vero si accumulerebbero, e il primo qtbot.wait (o un cambio di
    tema) di un test successivo li distruggerebbe o ristilizzerebbe tutti insieme."""
    yield
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is not None:
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _isolated_user_dirs(monkeypatch, tmp_path_factory):
    """Ogni test ha cartelle utente proprie (dati, stato, configurazione, recupero): niente
    file lasciati da altri test o da una sessione vera (es. il recupero dopo un arresto
    anomalo, che mostrerebbe un avviso). La cache resta condivisa (fontconfig è lento)."""
    base = tmp_path_factory.mktemp("utente")
    # Linux: XDG_*; Windows: %APPDATA% (dati e configurazione) e %LOCALAPPDATA% (stato), letti da
    # foliant.core.platform. La cache resta quella vera anche su Windows (LOCALAPPDATA/foliant/cache
    # sarebbe dentro lo stato isolato: costa solo le icone del tema).
    for var, name in (("XDG_DATA_HOME", "dati"), ("XDG_STATE_HOME", "stato"),
                      ("XDG_CONFIG_HOME", "config"), ("APPDATA", "roaming"), ("LOCALAPPDATA", "locale")):
        monkeypatch.setenv(var, str(base / name))
    monkeypatch.setenv("FOLIANT_RECOVERY_DIR", str(base / "recupero"))
    if sys.platform != "win32":
        # cartella di runtime privata (0700): blocchi dei file aperti e canali tra processi del test
        # (su Windows stanno in %LOCALAPPDATA%, già isolata)
        run = base / "run"
        run.mkdir(mode=0o700)
        run.chmod(0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    if sys.platform == "win32":
        # le QSettings() di Foliant starebbero nel registro vero (HKCU\\Software\\Foliant), comune a
        # tutti i test: qui vanno in un file INI della cartella del test, come su Linux con XDG
        from PySide6.QtCore import QSettings
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(base / "config"))


@pytest.fixture(autouse=True)
def _restore_core_translations():
    """Il catalogo del nucleo torna com'era dopo ogni test: un test che registra traduzioni
    senza passare da un modulo (es. con una spia di add_translations) non le lascia ai successivi."""
    from foliant import i18n
    from foliant.ui import i18n_en  # noqa: F401  (catalogo inglese del nucleo, registrato una volta sola)
    saved = {lang: dict(cat) for lang, cat in i18n._catalogs.items()}
    yield
    for lang in list(i18n._catalogs):
        if lang in saved:
            i18n._catalogs[lang].clear()
            i18n._catalogs[lang].update(saved[lang])


@pytest.fixture(autouse=True)
def _neutral_system_locale(monkeypatch):
    """Formato carta e unità predefiniti come con la localizzazione «C» dei server Linux (A4, mm),
    anche su un Windows in inglese statunitense (Letter, pollici): i test che riguardano le
    impostazioni locali le simulano da sé (test_locale_defaults.py)."""
    from PySide6.QtCore import QLocale

    from foliant.core import locale_defaults
    monkeypatch.setattr(locale_defaults, "system_locale", lambda: QLocale(QLocale.Language.C))


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """Rete di sicurezza: nei test nessuna finestra modale reale può bloccare la suite.

    Le finestre di dialogo non simulate dal test si chiudono subito (Annulla/No) con un
    avviso. I test che vogliono una risposta precisa la simulano con monkeypatch, come sempre.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

    def warn(what):
        warnings.warn(f"finestra modale non simulata nel test: {what}", stacklevel=3)

    def box_exec(self, *a, **k):
        warn(f"QMessageBox «{self.text()[:60]}»")
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda *a, _n=name, **k: (warn(f"QMessageBox.{_n}"), QMessageBox.StandardButton.No)[1]))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: (warn(type(self).__name__), 0)[1])
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: (warn("QMenu"), None)[1])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (warn("QInputDialog.getText"), ("", False))[1]))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (warn("QInputDialog.getItem"), ("", False))[1]))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (warn("QInputDialog.getInt"), (0, False))[1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (warn("QFileDialog"), ("", ""))[1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (warn("QFileDialog"), ([], ""))[1]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (warn("QFileDialog"), ("", ""))[1]))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: (warn("QFileDialog"), "")[1]))
    yield


def system_font(rel: str) -> str:
    """Percorso di un font dei pacchetti Linux usato dai test (es. «truetype/dejavu/DejaVuSans.ttf»):
    in /usr/share/fonts o, su Windows, nella cartella FOLIANT_TEST_FONTS con la stessa struttura
    (in GitHub Actions ci si scaricano DejaVu e Liberation). Se manca restituisce il percorso
    Linux, che i test controllano con os.path.exists come prima."""
    linux = "/usr/share/fonts/" + rel
    extra = os.environ.get("FOLIANT_TEST_FONTS")
    if not os.path.exists(linux) and extra:
        candidate = os.path.join(extra, *rel.split("/"))
        if os.path.exists(candidate):
            return candidate
    return linux


def set_data_home(monkeypatch, path) -> None:
    """Cartella dei dati dell'utente per un test: XDG_DATA_HOME su Linux, %APPDATA% su Windows
    (vedi foliant.core.platform.data_home)."""
    monkeypatch.setenv("APPDATA" if sys.platform == "win32" else "XDG_DATA_HOME", str(path))


def perf_limit(seconds: float) -> float:
    """Soglia di un test di velocità moltiplicata per FOLIANT_PERF_FACTOR (predefinito 1: soglie
    invariate). La CI su Windows lo imposta perché vi girano 4 processi di lavoro su 4 processori."""
    return seconds * float(os.environ.get("FOLIANT_PERF_FACTOR") or 1)


def _scale_qtbot_timeouts() -> None:
    """Con FOLIANT_PERF_FACTOR anche i tempi massimi di waitUntil/waitSignal crescono: sono limiti
    superiori (un test che passa non rallenta) e nessun test conta sul loro scadere."""
    factor = float(os.environ.get("FOLIANT_PERF_FACTOR") or 1)
    if factor <= 1:
        return
    from pytestqt.qtbot import QtBot

    def scaled(method):
        def wrapper(self, *args, timeout: int = 5000, **kwargs):
            return method(self, *args, timeout=int(timeout * factor) if timeout and timeout > 0 else timeout,
                          **kwargs)
        wrapper.__name__ = method.__name__
        wrapper.__doc__ = method.__doc__
        return wrapper
    for name in ("waitUntil", "waitSignal", "waitSignals"):
        setattr(QtBot, name, scaled(getattr(QtBot, name)))


_scale_qtbot_timeouts()


def has_mode(path, mode: int) -> bool:
    """Permessi POSIX attesi (es. 0o700 per una cartella privata). Su Windows i permessi POSIX
    non esistono: cartelle e file nel profilo dell'utente sono privati per gli ACL di Windows,
    quindi il controllo non si applica."""
    if sys.platform == "win32":
        return True
    import stat
    return stat.S_IMODE(os.stat(path).st_mode) == mode


def make_pdf(path, pages: int = 3, text: str = "Pagina {n}", size=(595, 842), extra=None) -> str:
    """Crea un PDF con testo noto su ogni pagina e lo salva in `path`."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), text.format(n=i + 1), fontsize=24)
        page.insert_text((72, 140), "Orologeria Verdi - contratto di compravendita", fontsize=12)
        if extra:
            extra(page, i)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def sample_pdf(tmp_path):
    return make_pdf(tmp_path / "esempio.pdf")


@pytest.fixture
def encrypted_pdf(tmp_path):
    path = tmp_path / "protetto.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Riservato", fontsize=20)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segreta", owner_pw="proprietario")
    doc.close()
    return str(path)


@pytest.fixture
def settings(tmp_path_factory):
    """Impostazioni del test in una cartella propria: la finestra vi scrive subito (segno della
    pulizia dei dati delle versioni precedenti), senza mescolarsi ai file del test in tmp_path."""
    from PySide6.QtCore import QSettings
    s = QSettings(str(tmp_path_factory.mktemp("impostazioni") / "foliant-test.ini"), QSettings.Format.IniFormat)
    return s


@pytest.fixture
def window(qtbot, settings):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow

    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    yield win
    for view in list(win.views()):
        if not view.document.closed:
            view.document._saved_state = view.document._state  # niente richieste di salvataggio
        win.close_view(view)
