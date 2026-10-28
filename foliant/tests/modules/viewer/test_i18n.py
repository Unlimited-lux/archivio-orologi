"""NFR-25 — Interfaccia del viewer in inglese.

Apre in italiano e poi in inglese pannelli, dialoghi, azioni e menu contestuali del viewer
(ricerca e ricerca avanzata, segnalibri, miniature, allegati, livelli, articoli, misure,
stampa, proprietà del documento, preferenze di visualizzazione, palette dei comandi e
scorciatoie, presentazione, menu della scheda) e segnala i testi rimasti identici nelle due
lingue o con parole solo italiane. In più due controlli statici sul codice: ogni `_()` ha la
traduzione inglese e nessun testo italiano arriva all'interfaccia senza `_()`."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QSpinBox, QTabWidget, QTableWidget, QTextEdit, QToolButton, QTreeWidget, QWidget,
)

from foliant import i18n

MODULE = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "viewer"

# parole che in un testo inglese indicano una traduzione mancante
ITALIAN = re.compile(
    r"\b(della|delle|dello|degli|nella|nelle|alla|alle|dalla|il|gli|una|uno|pagina|pagine|documento|documenti|"
    r"salva|apri|chiudi|cerca|ricerca|nessun[oa]?|tutt[ie]|con|per|non|questo|questa|segnalibr[oi]|"
    r"allegat[oi]|livell[oi]|stampa|scorciatoi[ae]|impostazioni|anteprima|miniatur[ae]|colonna|riga|"
    r"testo|immagine|istantanea|risultati|cartella|annulla|elimina|aggiungi|rinomina|sposta|vai|"
    r"successiv[oa]|precedente|sinistra|destra|presentazione|proprietà|modifica|maiusc|invio|oppure|"
    r"anche|quando|dopo|predefinit[oa])\b", re.IGNORECASE)
# testi uguali nelle due lingue ammessi: testi standard di Qt (tradotti dal traduttore Qt
# caricato all'avvio dell'applicazione), parole identiche in inglese, dati del documento
SAME_OK = {"Cancel", "OK", "Scroll Left", "Scroll Right", "Press shortcut", "File", "file", "Font", "Quote", "PDF",
           "Layout", "Zoom", "Schema", "{n} dpi"}


def _pdf(path) -> str:
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1} Rolex", fontsize=14)
    doc.set_toc([[1, "Capitolo", 1], [2, "Sezione", 2]])
    doc.embfile_add("note.txt", b"ciao", filename="note.txt")
    ocg = doc.add_ocg("Quote", on=True)
    doc[0].insert_text((72, 200), "Misura", oc=ocg)
    doc.save(str(path))
    return str(path)


# ------------------------------------------------------------------ raccolta
def _action(a: QAction, out: list, where: str) -> None:
    if a.isSeparator():
        return
    out.append((where, a.text().replace("&", "")))
    if a.toolTip() != a.text():
        out.append((where + ".tip", a.toolTip()))
    out.append((where + ".status", a.statusTip()))
    if a.menu() is not None:
        _menu(a.menu(), out, where)


def _menu(m: QMenu, out: list, where: str) -> None:
    m.aboutToShow.emit()           # i menu riempiti all'apertura
    out.append((where + ".menu", m.title().replace("&", "")))
    for a in m.actions():
        _action(a, out, where)


def _widget(widget: QWidget, out: list, where: str) -> None:
    for w in [widget] + widget.findChildren(QWidget):
        name = f"{where}/{type(w).__name__}"
        if isinstance(w, QDialog) or w is widget:
            out.append((name + ".title", w.windowTitle()))
        out.append((name + ".tip", w.toolTip()))
        out.append((name + ".acc", w.accessibleName()))
        if isinstance(w, QLabel):
            out.append((name, w.text()))
        elif isinstance(w, QAbstractButton):
            out.append((name, w.text().replace("&", "")))
            if isinstance(w, QToolButton) and w.menu() is not None:
                _menu(w.menu(), out, name)
        elif isinstance(w, QComboBox):
            out.extend((name + ".item", w.itemText(i)) for i in range(w.count()))
        elif isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            out.append((name + ".placeholder", w.placeholderText()))
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            out.extend(((name + ".suffix", w.suffix()), (name + ".special", w.specialValueText())))
        elif isinstance(w, QGroupBox):
            out.append((name + ".title", w.title()))
        elif isinstance(w, QTabWidget):
            out.extend((name + ".tab", w.tabText(i)) for i in range(w.count()))
        if isinstance(w, QTreeWidget):
            out.extend((name + ".header", w.headerItem().text(i)) for i in range(w.columnCount()))
        if isinstance(w, QTableWidget):
            out.extend((name + ".header", w.horizontalHeaderItem(i).text()) for i in range(w.columnCount())
                       if w.horizontalHeaderItem(i) is not None)
        for a in QWidget.actions(w):
            _action(a, out, name)


def _context_menus(fns, out: list) -> None:
    """Esegue funzioni che aprono un menu con exec(): il menu aperto viene letto e chiuso."""
    def grab():
        m = QApplication.activePopupWidget()
        if isinstance(m, QMenu):
            _menu(m, out, "contesto")
            m.close()
    timer = QTimer()
    timer.timeout.connect(grab)
    timer.start(20)
    try:
        for fn in fns:
            fn()
    finally:
        timer.stop()


def _roots(win, view) -> dict[str, QWidget]:
    """Pannelli, barre e dialoghi del viewer, aperti."""
    from foliant.modules.viewer.presentation import OptionsDialog
    ctx = win.ctx
    parts = ctx.viewer_parts
    for pid in ("thumbnails", "bookmarks", "search", "attachments", "layers", "articles"):
        ctx.show_sidebar_panel(pid)
    QApplication.processEvents()
    roots: dict[str, QWidget] = {}
    for pname, ctl in parts.items():               # pannelli e barre
        if isinstance(ctl, QWidget):
            roots[pname] = ctl
        for attr in ("panel", "bar", "widget", "toolbar"):
            w = getattr(ctl, attr, None)
            if isinstance(w, QWidget):
                roots[f"{pname}.{attr}"] = w
    commands = parts["commands"]
    commands.open_palette()
    commands.open_shortcuts()
    dialogs = {"ricerca avanzata": parts["advsearch"].open(), "preferenze": parts["prefs"].open_dialog(),
               "palette": commands.palette, "scorciatoie": commands.dialog,
               "proprietà": parts["properties"].show_dialog(),
               "stampa": parts["printing"].options_dialog(view),
               "presentazione": OptionsDialog(win, parts["presentation"].options())}
    for name, d in dialogs.items():
        assert d is not None, name
        roots[name] = d
    return roots


def _collect(win, pdf: str) -> list[tuple[str, str]]:
    from foliant.ui.document_view import PageEvent
    ctx = win.ctx
    parts = ctx.viewer_parts
    view = win.open_path(pdf)
    out: list[tuple[str, str]] = []
    for pname, ctl in parts.items():               # azioni delle parti
        for attr, val in list(vars(ctl).items()):
            if isinstance(val, QAction):
                _action(val, out, f"{pname}.{attr}")
            elif isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, QAction):
                        _action(v, out, f"{pname}.{attr}[{k}]")
    for name, root in _roots(win, view).items():
        _widget(root, out, name)
        if isinstance(root, QDialog):
            root.hide()
    bm, att, layers = parts["bookmarks"].panel, parts["attachments"], parts["layers"]
    _context_menus([
        lambda: bm._context_menu(bm.tree.visualItemRect(bm.tree.topLevelItem(0)).center()),
        lambda: att._context_menu(att.tree.visualItemRect(att.tree.topLevelItem(0)).center()),
        lambda: layers._context_menu(layers.tree.rect().center()),
    ], out)
    _menu(parts["session"].build_tab_menu(0), out, "scheda")
    pe = PageEvent(page=0, point=QPointF(80, 95), pos=QPointF(100, 100), button=Qt.MouseButton.RightButton,
                   buttons=Qt.MouseButton.RightButton, modifiers=Qt.KeyboardModifier.NoModifier, event=None)
    for prov in list(win.view_menu_providers):     # menu contestuale della pagina (parti del viewer)
        if getattr(prov, "__module__", "").startswith("foliant.modules.viewer"):
            m = QMenu()
            prov(view, pe, m)
            _menu(m, out, "pagina")
    for v in win.views():
        v.document._saved_state = v.document._state
    return [(w, t) for w, t in out if t and t.strip()]


def _wordy(text: str) -> bool:
    """Testo da tradurre: almeno una parola di tre lettere (non numeri, sigle, percorsi)."""
    if "/" in text or "\\" in text or re.fullmatch(r"[\d\s.,:;×%()+\-–—#…<>=]*\w{0,2}[\d\s.,%]*", text):
        return False
    return bool(re.search(r"[A-Za-zÀ-ÿ]{3,}", text)) and not re.fullmatch(r"PDF[ \d.]*|[A-Z0-9 +\-.]+", text)


def test_viewer_ui_is_english(qtbot, tmp_path):
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow
    pdf = _pdf(tmp_path / "prova.pdf")
    texts = {}
    try:
        for lang in ("it", "en"):
            i18n.set_language(lang)
            theme.apply(QApplication.instance(), "light")
            win = MainWindow(QSettings(str(tmp_path / f"{lang}.ini"), QSettings.Format.IniFormat))
            qtbot.addWidget(win)
            win.resize(1200, 800)
            win.show()
            texts[lang] = _collect(win, pdf)
            for d in win.findChildren(QDialog):
                d.hide()
    finally:
        i18n.set_language("it")
    assert len(texts["en"]) > 400, "raccolta incompleta"
    italian = {t for _w, t in texts["it"]}
    same, words = set(), set()
    for where, text in texts["en"]:
        if text in italian and _wordy(text) and text not in SAME_OK:
            same.add(f"{where}: {text!r}")
        if ITALIAN.search(text):
            words.add(f"{where}: {text!r}")
    assert not same, "testi identici in italiano e inglese:\n" + "\n".join(sorted(same))
    assert not words, "parole italiane nell'interfaccia inglese:\n" + "\n".join(sorted(words))


# ------------------------------------------------------------------ statici
def _trees():
    for f in sorted(MODULE.glob("*.py")):
        yield f.name, ast.parse(f.read_text("utf-8"))


def test_every_literal_string_has_english_translation(qtbot, tmp_path):
    from foliant.ui.main_window import MainWindow
    try:
        i18n.set_language("en")
        win = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))   # registra i cataloghi
        qtbot.addWidget(win)
        missing = []
        for name, tree in _trees():
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_" and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                    s = n.args[0].value
                    if i18n._(s) == s and _wordy(s) and s not in SAME_OK:
                        missing.append(f"{name}:{n.lineno} {s!r}")
    finally:
        i18n.set_language("it")
    assert not missing, "\n".join(missing)


UI_CALLS = {"setText", "setToolTip", "setPlaceholderText", "addItem", "addItems", "addAction", "setWindowTitle",
            "QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QAction", "QToolButton", "QGroupBox", "QMenu",
            "showMessage", "notify", "show_banner", "error", "info", "confirm", "setStatusTip", "addMenu",
            "setTitle", "addTab", "setHeaderLabels", "setSpecialValueText", "setSuffix", "setPrefix",
            "setAccessibleName", "warning", "question", "information", "critical", "setInformativeText",
            "ask_save_path", "ask_open_paths", "addRow", "setLabelText", "getText", "getDouble", "getInt",
            "getItem", "action"}


def test_no_italian_text_outside_translation():
    found = []
    for name, tree in _trees():
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if fn not in UI_CALLS:
                continue
            for a in list(n.args) + [k.value for k in n.keywords]:
                nodes = list(a.elts) if isinstance(a, (ast.List, ast.Tuple)) else \
                    [v for v in a.values] if isinstance(a, ast.JoinedStr) else \
                    [a.left, a.right] if isinstance(a, ast.BinOp) else [a]
                for c in nodes:
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and ITALIAN.search(c.value):
                        found.append(f"{name}:{n.lineno} {fn} {c.value!r}")
    assert not found, "testi italiani senza _():\n" + "\n".join(found)


def test_no_key_registered_twice_with_different_values(qtbot, tmp_path, monkeypatch):
    """Il viewer ha un solo catalogo: una chiave registrata da due file con traduzioni diverse
    darebbe l'una o l'altra a seconda dell'ordine. Dove il senso cambia si usa pgettext."""
    import inspect

    import foliant.api as api
    from foliant.ui.main_window import MainWindow
    registered: dict[tuple[str, str], dict[str, list[str]]] = {}
    real = i18n.add_translations

    def spy(lang, mapping):
        caller = inspect.stack()[1].frame.f_globals.get("__name__", "")
        if caller.startswith("foliant.modules.viewer"):
            for key, value in mapping.items():
                registered.setdefault((lang, key), {}).setdefault(value, []).append(caller.rsplit(".", 1)[-1])
        return real(lang, mapping)
    monkeypatch.setattr(i18n, "add_translations", spy)
    monkeypatch.setattr(api, "add_translations", spy)
    import sys
    for name, mod in list(sys.modules.items()):
        if name.startswith("foliant.modules.viewer") and getattr(mod, "add_translations", None) is real:
            monkeypatch.setattr(mod, "add_translations", spy)
    win = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(win)
    assert len(registered) > 400, "registrazioni non intercettate"
    clashes = {k[1]: v for k, v in registered.items() if len(v) > 1}
    assert not clashes, "\n".join(f"{k!r}: {v}" for k, v in clashes.items())
    british = {k[1]: list(v) for k, v in registered.items()
               if any(re.search(r"colour|behaviour|centre|favourite|licence|organis", t, re.I) for t in v)}
    assert not british, f"inglese britannico: {british}"


def test_icon_only_buttons_have_tooltip_and_accessible_name(window, tmp_path):
    """NFR-22: ogni pulsante a sola icona di pannelli, barre e dialoghi del viewer ha un
    suggerimento e un nome accessibile (es. «⋮» del pannello Livelli: «Altre azioni»)."""
    view = window.open_path(_pdf(tmp_path / "prova.pdf"))
    missing = []
    for name, root in _roots(window, view).items():
        for b in [root] + root.findChildren(QAbstractButton):
            if not isinstance(b, QAbstractButton) or b.text().strip() or (b is not root and not b.isVisibleTo(root)):
                continue
            if b.icon().isNull() or not b.toolTip().strip() or not b.accessibleName().strip():
                missing.append(f"{name}: {type(b).__name__} tip={b.toolTip()!r} acc={b.accessibleName()!r}")
        if isinstance(root, QDialog):
            root.hide()
    for v in window.views():
        v.document._saved_state = v.document._state
    assert not missing, "\n".join(missing)
