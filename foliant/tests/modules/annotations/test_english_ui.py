"""NFR-25: interfaccia inglese completa del modulo Commenti.

Si costruisce una finestra in italiano e una in inglese e si raccolgono i testi di tutto ciò che il
modulo mostra (barra della modalità con i menu dei pulsanti, pannelli, mini barra, menu contestuale,
menu «Commenti», dialoghi); poi i testi che il modulo scrive nei documenti (soggetto, stato, timbri,
riepiloghi). Sono segnalate le voci inglesi rimaste identiche all'italiano (esclusi nomi propri,
sigle, numeri e codici) o che contengono parole solo italiane."""

from __future__ import annotations

import re

import pymupdf
import pytest
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QMenu,
                               QTreeWidget, QWidget)

from foliant.modules.annotations import engine
from foliant.modules.annotations.i18n_en import EN

WORD = re.compile(r"[a-zàèéìòù]{4,}")


def _italian_only_words() -> set[str]:
    """Parole (≥ 4 lettere) presenti nei testi italiani del catalogo e assenti da quelli inglesi."""
    it = {w for k in EN for w in WORD.findall(k.lower())}
    en = {w for v in EN.values() for w in WORD.findall(v.lower())}
    return it - en


# voci uguali nelle due lingue ammesse: nomi propri, sigle, valori tecnici
SAME_IN_BOTH = re.compile(r"^(|[\d\s.,:x×%/+°'\"-]*|.*\b(pt|mm|cm|in|m|km|ft)\b.*|XFDF|FDF|CSV|PDF|HTML|PNG|"
                          r"Helvetica|Times|Courier|Maria Rossi|Anna|Bruno|.*\.pdf|\s*↳ .*|A|[A-Z]{1,4}|.*\(.*\)|Area|Standard|"
                          r"#[0-9A-Fa-f]{6}|\d+ ?%|{.*})$")


def _menu_texts(menu: QMenu, owner: str, seen: set) -> list[tuple[str, str]]:
    if id(menu) in seen:
        return []
    seen.add(id(menu))
    menu.aboutToShow.emit()                 # menu riempiti al momento (dinamici, personalizzati)
    out = []
    for a in menu.actions():
        out += [(owner, a.text()), (owner, a.toolTip() if a.toolTip() != a.text().replace("&", "") else "")]
        if a.menu() is not None:
            out += _menu_texts(a.menu(), owner, seen)
    return out


def _widget_texts(root: QWidget, owner: str) -> list[tuple[str, str]]:
    out = []
    seen: set = set()
    widgets = [root] + root.findChildren(QWidget)
    for w in widgets:
        if isinstance(w, QAbstractButton):
            out += [(owner, w.text()), (owner, w.toolTip())]
            if getattr(w, "menu", None) and w.menu() is not None:
                out += _menu_texts(w.menu(), owner, seen)
            if hasattr(w, "defaultAction") and w.defaultAction() is not None:
                out += [(owner, w.defaultAction().text())]
        elif isinstance(w, QLabel):
            out.append((owner, w.text()))
        elif isinstance(w, QComboBox):
            out += [(owner, w.itemText(i)) for i in range(w.count())]
            out.append((owner, w.toolTip()))
        elif isinstance(w, QLineEdit):
            out += [(owner, w.placeholderText()), (owner, w.toolTip())]
            if not w.isReadOnly():
                out.append((owner, w.text()))
        elif isinstance(w, QGroupBox):
            out.append((owner, w.title()))
        elif isinstance(w, QTreeWidget):
            h = w.headerItem()
            out += [(owner, h.text(i)) for i in range(h.columnCount())]
        elif isinstance(w, QMenu):
            out += _menu_texts(w, owner, seen)
        if w.toolTip():
            out.append((owner, w.toolTip()))
    return out


def _ui_texts(settings, text_pdf, monkeypatch, lang) -> list[tuple[str, str]]:
    from PySide6.QtWidgets import QApplication

    from foliant import i18n
    from foliant.modules.annotations import dialogs
    from foliant.modules.annotations.minibar import MiniBar
    from foliant.modules.annotations.panels import build_annotation_menu
    from foliant.ui.main_window import MainWindow
    i18n.set_language(lang)
    win = None
    dialogs_seen: list[QDialog] = []
    try:
        monkeypatch.setattr(QDialog, "exec", lambda self: dialogs_seen.append(self) or 0)
        win = MainWindow(settings)
        win.show()
        view = win.open_path(text_pdf)
        module = win.ctx.annotations
        ctrl = module.ctrl
        win.set_mode("commenta")
        note = ctrl.apply("n", engine.create_note, 0, (300, 300), "x", engine.Style(), "A", view=view)
        ctrl.select([(0, note)], view)
        module.comments.reload()
        texts = _widget_texts(module.mode.toolbar, "barra")
        texts += _widget_texts(module.properties, "proprietà")
        for key in ("highlight", engine.FREETEXT, engine.RECT, engine.INK, engine.DISTANCE, "eraser"):
            module.properties.show_tool(key) if hasattr(module.properties, "show_tool") else None
            texts += _widget_texts(module.properties, "proprietà")
        texts += _widget_texts(module.comments, "pannello")
        texts += _widget_texts(MiniBar(ctrl), "mini barra")
        menu = QMenu()
        build_annotation_menu(ctrl, menu, 0, note, pymupdf.Point(300, 300))
        texts += _menu_texts(menu, "menu contestuale", set())
        for section in (_tr("Commenti"), "Commenti"):
            for act in win._menu_sections.get(section, []):
                texts.append(("menu principale", act.text()))
        dialogs.edit_comment(view, _tr("Nota"), "A", "")
        dialogs.ask_scale(view, engine.Scale())
        for d in dialogs_seen:
            texts += _widget_texts(d, type(d).__name__)
        QApplication.processEvents()
        return texts
    finally:
        i18n.set_language("it")
        if win is not None:
            for v in list(win.views()):
                v.document._saved_state = v.document._state
            monkeypatch.setattr(win, "_ask_unsaved", lambda docs: "discard", raising=False)
            win.close()
            win.deleteLater()


def _tr(text):
    from foliant.i18n import _
    return _(text)


def _suspicious(it_texts, en_texts, catalog_only: bool = True) -> list[str]:
    italian = _italian_only_words()
    same = {t for _w, t in it_texts} & {t for _w, t in en_texts}
    found = set()
    for owner, t in en_texts:
        t = t.replace("&", "")
        if not t:
            continue
        if t in same and not SAME_IN_BOTH.match(t) and re.search(r"[a-zà-ù]{3,}", t) and (t in EN or not catalog_only):
            found.add(f"{owner}: «{t}» (identica in italiano)")
        tokens = [p for p in engine.PLACEHOLDERS if p != "{file}" and p in t]
        if tokens:
            found.add(f"{owner}: «{t}» (segnaposto italiani: {', '.join(tokens)})")
        words = [w for w in WORD.findall(t.lower()) if w in italian]
        if words:
            found.add(f"{owner}: «{t}» (parole italiane: {', '.join(words)})")
    return sorted(found)


def test_ui_has_no_italian_in_english(qtbot, settings, text_pdf, monkeypatch):
    it = _ui_texts(settings, text_pdf, monkeypatch, "it")
    en = _ui_texts(settings, text_pdf, monkeypatch, "en")
    import os
    if os.environ.get("DUMP_UI_TEXTS"):
        print("\n".join(sorted({f"{o}: {t}" for o, t in en if t})))
    assert len(en) > 100, "raccolta dei testi non riuscita"
    found = _suspicious(it, en)
    assert not found, "\n".join(found)
    assert ("pannello", "1 comment") in en       # singolare corretto nel contatore


# ------------------------------------------------------------------ testi scritti nei documenti
def _document_texts() -> list[tuple[str, str]]:
    pdf = pymupdf.open()
    pdf.new_page()
    out = []
    style = engine.Style()
    note = engine.create_note(pdf, 0, (100, 100), "x", style, "Anna")
    engine.add_reply(pdf, 0, note, "y", "Bruno")
    st = engine.set_status(pdf, 0, note, engine.STATE_ACCEPTED, "Bruno")
    out.append(("stato", engine.annotation(pdf, 0, st).contents))
    rect = engine.create_shape(pdf, 0, engine.RECT, [(100, 200), (200, 260)], style, "Anna")
    hl = engine.create_markup(pdf, 0, engine.HIGHLIGHT, [pymupdf.Rect(100, 300, 200, 312).quad], style, "Anna")
    for x in (note, rect, hl):
        out.append(("soggetto", engine.annotation(pdf, 0, x).subject))
    for label, _name, _c in engine.PRESET_STAMPS:
        out.append(("timbro", _tr(label)))
    for label, template, _c in engine.DYNAMIC_STAMPS:
        out.append(("timbro dinamico", _tr(label)))
        out.append(("timbro dinamico", engine.fill_template(_tr(template), author="Anna", counter=3)))
    out += [("riepilogo txt", line) for line in engine.summary_text(pdf, "doc.pdf").splitlines()]
    out += [("riepilogo csv", line) for line in engine.summary_csv(pdf).splitlines()]
    html = re.sub(r"<[^>]+>", "\n", engine.summary_html(pdf, "doc.pdf"))
    out += [("riepilogo html", line.strip()) for line in html.splitlines()]
    out += [("misure csv", line) for line in engine.measures_csv(pdf).splitlines()]
    for page in pymupdf.open(stream=engine.summary_pdf(pdf, "doc.pdf", layout="list"), filetype="pdf"):
        out += [("riepilogo pdf", line) for line in page.get_text().splitlines()]
    return out


@pytest.fixture
def english():
    from foliant import i18n
    i18n.set_language("en")
    yield
    i18n.set_language("it")


def test_document_texts_in_english(english):
    from foliant import i18n
    en = _document_texts()
    i18n.set_language("it")
    it = _document_texts()
    i18n.set_language("en")
    found = _suspicious(it, en, catalog_only=False)
    assert not found, "\n".join(found)


def test_dynamic_stamp_english_placeholders():
    """I modelli in inglese usano {author} {date} {time} {page} {counter} e vengono compilati."""
    from datetime import datetime
    when = datetime(2026, 10, 28, 9, 5)
    text = engine.fill_template("Protocol no. {counter} of {date}, {author}, p. {page} at {time}", author="Anna",
                                when=when, page=0, counter=7)
    assert text == "Protocol no. 007 of 28/10/2026, Anna, p. 1 at 09:05"
    assert engine.unknown_placeholders("{author} {date} {counter} {nome}") == []
    assert engine.fill_template("No. {counter}", when=when) == "No. {counter}"


@pytest.mark.parametrize("lang, date, time", [("it", "28/10/2026", "14:05"), ("en", "10/28/2026", "2:05 PM")])
def test_dates_follow_language(lang, date, time):
    """NFR-26: data e ora dei timbri dinamici e dei commenti secondo la lingua (anno a 4 cifre)."""
    from datetime import datetime

    from foliant import i18n
    when = datetime(2026, 10, 28, 14, 5)
    i18n.set_language(lang)
    try:
        assert engine.fill_template(_tr("Ricevuto il {data} alle {ora}"), when=when) in (
            f"Ricevuto il {date} alle {time}", f"Received on {date} at {time}")
        assert engine.dynamic_subtitle("{data}", "Anna", when) == date
        assert engine.format_date("D:20261028140500") == f"{date} {time}"
        assert engine.local_time(datetime(2026, 1, 1, 0, 7)) == ("12:07 AM" if lang == "en" else "00:07")
        assert engine.fill_template("{data}", when=when, date_format="%Y-%m-%d") == "2026-10-28"  # modello scelto
    finally:
        i18n.set_language("it")
