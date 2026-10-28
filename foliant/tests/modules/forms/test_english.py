"""Interfaccia inglese del modulo Moduli (NFR-25) e formati locali (NFR-26).

Il controllo statico delle chiamate `_()` non vede le stringhe in costanti, valori predefiniti
o voci di combo: qui si costruiscono davvero in italiano e poi in inglese il pannello delle
proprietà (tutte le schede), l'elenco dei campi, le azioni, il menu contestuale, l'avviso del
modulo e i dialoghi (dati, reimposta, appiattisci, copia, ordine di calcolo, riconoscimento,
elaborazione multipla, XML) e si segnalano i testi inglesi rimasti uguali all'italiano
(esclusi nomi propri, sigle, numeri e codici) o con parole solo italiane.
"""

from __future__ import annotations

import re

import pymupdf
import pytest
from PySide6.QtGui import QAction, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QListWidget,
    QMenu, QPlainTextEdit, QTabWidget, QTableWidget, QTextEdit, QTreeWidget, QWidget,
)

from foliant import i18n
from foliant.modules import forms
from foliant.modules.forms import engine as E
from foliant.modules.forms import formats as fm

# nomi propri, sigle e codici che restano uguali nelle due lingue
SAME_OK = {
    "OK", "PDF", "CSV", "XML", "FDF", "XFDF", "JSON", "TXT", "XFA", "URL", "JavaScript", "Helvetica",
    "Times", "Times Roman", "Courier", "Symbol", "ZapfDingbats", "Zapf Dingbats", "Arial", "Unicode",
    "HTML", "OCR", "AcroForm", "UTF-8", "ISO", "Tab", "Esc", "Ctrl", "Shift", "Alt", "Cmd", "€", "$",
    "Auto", "Normal", "Tooltip", "Online", "Download", "Formula", "Password",
    "Scroll Left", "Scroll Right",   # pulsanti interni delle schede di Qt
    "SUM", "AVG", "PRD", "MIN", "MAX", "HTTP", "HTTPS", "mailto", "RGB", "CMYK", "pt", "mm", "in", "px",
}
# parole solo italiane: non devono comparire nell'interfaccia inglese
ITALIAN = {
    "il", "gli", "della", "delle", "dei", "degli", "dello", "nel", "nella", "sul", "sulla", "campo",
    "campi", "valore", "valori", "pagina", "pagine", "modulo", "moduli", "nessun", "nessuno", "nessuna",
    "testo", "firma", "pulsante", "casella", "elenco", "annulla", "chiudi", "salva", "esporta",
    "importa", "proprietà", "calcolo", "calcoli", "formato", "obbligatorio", "obbligatori", "sola",
    "lettura", "nascosto", "colore", "bordo", "sfondo", "allineamento", "sinistra", "destra", "centro",
    "carattere", "dimensione", "azione", "azioni", "quando", "una", "uno", "è", "più", "già", "anche",
    "oppure", "selezionati", "selezionato", "tutti", "tutte", "nome", "numero", "ora",
    "maschera", "scelta", "opzioni", "elimina", "duplica", "aggiungi", "rimuovi", "sposta", "nuovo",
    "nuova", "riconosci", "reimposta", "appiattisci", "ordine", "mostra", "nascondi", "stampa",
    "giorno", "mese", "anno", "spunta", "vuoto", "vuota", "esempio", "errore", "avviso",
}
_WORD = re.compile(r"[A-Za-zÀ-ÿ]+")


def _texts(obj, out: list[str]) -> None:
    """Testi visibili di un widget e dei suoi figli (etichette, pulsanti, voci, segnaposto, suggerimenti)."""
    widgets = [obj] + obj.findChildren(QWidget) if isinstance(obj, QWidget) else [obj]
    for w in widgets:
        if isinstance(w, QWidget):
            out += [w.toolTip(), w.whatsThis(), w.statusTip(), w.windowTitle(), w.accessibleName()]
            for a in w.actions():
                out += [a.text(), a.toolTip()]
        if isinstance(w, QLabel):
            out.append(w.text())
        if isinstance(w, QAbstractButton):
            out.append(w.text())
        if isinstance(w, QGroupBox):
            out.append(w.title())
        if isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
            out.append(w.placeholderText())
            if w.lineEdit() is not None:
                out.append(w.lineEdit().placeholderText())
        if isinstance(w, QLineEdit):
            out.append(w.placeholderText())
        if isinstance(w, (QPlainTextEdit, QTextEdit)):
            out.append(w.placeholderText())
        if isinstance(w, QTabWidget):
            out += [w.tabText(i) for i in range(w.count())] + [w.tabToolTip(i) for i in range(w.count())]
        if isinstance(w, QListWidget):
            out += [w.item(i).text() for i in range(w.count())]
        if isinstance(w, QTreeWidget):
            h = w.headerItem()
            out += [h.text(i) for i in range(h.columnCount())]
        if isinstance(w, QTableWidget):
            for i in range(w.columnCount()):
                it = w.horizontalHeaderItem(i)
                if it is not None:
                    out.append(it.text())
        if isinstance(w, QMenu):
            out.append(w.title())
            for a in w.actions():
                out += [a.text(), a.toolTip()]
                if a.menu() is not None:
                    _texts(a.menu(), out)


def _build_doc(path) -> None:
    pdf = pymupdf.open()
    pdf.new_page()
    num = fm.Format(fm.NUMBER, 2, 2, 0, "€", False)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 50, 250, 70), "q1", fmt=num, required=True,
                   validation=E.Validation(0, 100))
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 90, 250, 110), "q2", fmt=num)
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 130, 250, 150), "q3", fmt=num,
                   calc=E.Calculation("SUM", ["q1", "q2"]))
    E.create_field(pdf, 0, E.TEXT, pymupdf.Rect(50, 170, 250, 190), "d1", fmt=fm.Format(fm.DATE))
    E.create_field(pdf, 0, E.CHECKBOX, pymupdf.Rect(50, 210, 64, 224), "c1")
    for i, v in enumerate(("v1", "v2")):
        E.create_field(pdf, 0, E.RADIO, pymupdf.Rect(50 + 40 * i, 240, 64 + 40 * i, 254), "r1", export=v)
    E.create_field(pdf, 0, E.COMBO, pymupdf.Rect(50, 270, 200, 290), "k1", options=[("x1", "x1"), ("x2", "x2")])
    E.create_field(pdf, 0, E.LIST, pymupdf.Rect(50, 300, 200, 340), "l1", options=["y1", "y2"])
    E.create_field(pdf, 0, E.BUTTON, pymupdf.Rect(300, 50, 400, 70), "b1", caption="b1")
    E.create_field(pdf, 0, E.SIGNATURE, pymupdf.Rect(300, 90, 450, 130), "s1")
    pdf.new_page()
    pdf.save(str(path))


def _collect(lang: str, qtbot, settings, tmp_path, monkeypatch) -> tuple[list[str], set[str]]:
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow

    previous = i18n.language()
    i18n.set_language(lang)
    out: list[str] = []
    win = None
    try:
        dialogs: list[QWidget] = []

        def fake_exec(self, *a, **k):
            _texts(self, out)
            dialogs.append(self)
            return 0

        monkeypatch.setattr(QDialog, "exec", fake_exec)
        monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: (_texts(self, out), None)[1])
        theme.apply(QApplication.instance(), "light")
        win = MainWindow(settings)
        qtbot.addWidget(win)
        win.resize(1200, 800)
        win.show()
        ctx = win.ctx
        for name in ("error", "info", "status"):
            monkeypatch.setattr(ctx, name, lambda *a, **k: out.extend(x for x in a if isinstance(x, str)))
        monkeypatch.setattr(ctx, "confirm", lambda *a, **k: (out.extend(x for x in a if isinstance(x, str)), False)[1])
        monkeypatch.setattr(ctx, "notify", lambda text, actions=(), *a, **k:
                            out.extend([text] + [x[0] for x in actions]))
        for name in ("ask_open_paths", "ask_save_path", "ask_directory"):
            monkeypatch.setattr(ctx, name, lambda *a, **k: (out.extend(x for x in a if isinstance(x, str)),
                                                            [] if "open" in name else None)[1])
        path = tmp_path / f"modulo_{lang}.pdf"
        _build_doc(path)
        view = ctx.open_path(str(path))
        view.flush_rendering()
        QApplication.processEvents()
        module = forms.instance()
        f = module.filler(view)
        f.state.refresh(force=True)
        f.update_bar()
        out += [f.banner_text] + list(f.banner_actions)
        # azioni del modulo (barra degli strumenti, menu)
        for value in vars(module).values():
            if isinstance(value, QAction):
                out += [value.text(), value.toolTip()]
                if value.menu() is not None:
                    _texts(value.menu(), out)
        # modalità modifica: pannello delle proprietà per ogni tipo di campo, elenco dei campi
        ctx.set_mode("moduli")
        QApplication.processEvents()
        for info in f.state.fields:
            module.set_selection([(info.page, info.xref)])
            module.show_properties()
            QApplication.processEvents()
            if module.panel is not None:
                _texts(module.panel, out)
        if module.fields_panel is not None:
            _texts(module.fields_panel, out)
        # menu contestuale (modifica e compilazione)
        menus: list[QMenu] = []
        monkeypatch.setattr(module, "exec_popup", lambda menu, pos: (menus.append(menu), _texts(menu, out)))

        class _Ev:
            page = 0
            point = pymupdf.Point(60, 60)
        for design in (True, False):
            module.design_active = design
            m = QMenu()
            module.context_menu(view, _Ev(), m)
            _texts(m, out)
        module.design_active = True
        # dialoghi e comandi
        for name in ("copy_to_pages_dialog", "export_data_dialog", "import_data_dialog", "data_menu_popup",
                     "reset_dialog", "flatten_dialog", "calc_order_dialog", "export_xfa_dialog",
                     "import_xfa_dialog", "collect_dialog", "series_dialog", "detect_fields", "check_form"):
            module.set_selection([(f.state.fields[0].page, f.state.fields[0].xref)])
            try:
                getattr(module, name)()
            except Exception as e:  # pragma: no cover - un dialogo che non si apre è un errore del test
                pytest.fail(f"{name}: {e!r}")
            QApplication.processEvents()
        from foliant.modules.forms import dialogs as D
        D.ask_csv_row(win, [{"a": "1"}, {"a": "2"}])
        D.ask_calc_order(win, ["q3", "q1"], lambda names: [("q3", "q1")], lambda: ["q1", "q3"])
        doc_strings = {x.name for x in f.state.fields} | {"v1", "v2", "x1", "x2", "y1", "y2", "b1"}
        return [s for s in out if s], doc_strings
    finally:
        if win is not None:   # niente richiesta di salvataggio alla chiusura, anche se il test fallisce
            for view_ in list(win.views()):
                if not view_.document.closed:
                    view_.document._saved_state = view_.document._state
                win.close_view(view_)
            win.close()
        monkeypatch.undo()
        i18n.set_language(previous)


def _exempt(s: str, doc: set[str], fonts: set[str]) -> bool:
    t = re.sub(r"\([^()]*\*\.[^()]*\)", "", s)          # filtri di file «(*.csv)»
    t = re.sub(r"\b[hHMst]{1,2}(:[hHMst]{1,2})+( tt)?\b|\b[AP]M\b", "", t)   # modelli e esempi di ora
    t = t.replace(";;", " ").strip().rstrip("…:").strip()
    words = _WORD.findall(t)
    if not words or sum(len(w) for w in words) <= 2:
        return True   # numeri, simboli, esempi di formato («1.234,56»)
    if t in SAME_OK or t in doc or t in fonts or s in doc:
        return True
    if all(w in SAME_OK or w.isupper() for w in words):
        return True   # sigle
    if re.fullmatch(r"[\w./:+-]+@?[\w./:+-]*", t) and not t[:1].isupper():
        return True   # codici, nomi di chiavi o di file
    return False


def test_forms_ui_is_english(qtbot, settings, tmp_path, monkeypatch):
    it, doc = _collect("it", qtbot, settings, tmp_path, monkeypatch)
    en, _doc = _collect("en", qtbot, settings, tmp_path, monkeypatch)
    fonts = set(QFontDatabase.families())
    italian = set(it)
    same = sorted({s for s in en if s in italian and not _exempt(s, doc, fonts)})
    words = sorted({s for s in en if any(w.lower() in ITALIAN for w in _WORD.findall(s))
                    and not _exempt(s, doc, fonts)})
    assert len(en) > 200   # si è davvero raccolta l'interfaccia
    assert not same, "testi uguali in italiano e in inglese:\n" + "\n".join(same)
    assert not words, "parole italiane nell'interfaccia inglese:\n" + "\n".join(words)


def test_number_and_date_defaults_follow_language():
    """NFR-26: i nuovi campi numero e data propongono i formati della lingua."""
    previous = i18n.language()
    try:
        i18n.set_language("en")
        assert fm.Format(fm.NUMBER).separator == 0 and fm.Format(fm.DATE).pattern == "mm/dd/yyyy"
        assert fm.display(fm.Format(fm.NUMBER, 1), "1234.5")[0] == "1,234.5"
        pdf = pymupdf.open()
        pdf.new_page()
        x = E.create_field(pdf, 0, E.DATE_KIND, pymupdf.Rect(50, 50, 250, 70), "when")
        assert E.field_info(pdf, 0, x).fmt.pattern == "mm/dd/yyyy"
        i18n.set_language("it")
        assert fm.Format(fm.NUMBER).separator == 2 and fm.Format(fm.DATE).pattern == "dd/mm/yyyy"
        assert fm.display(fm.Format(fm.NUMBER, 1), "1234.5")[0] == "1.234,5"
    finally:
        i18n.set_language(previous)
