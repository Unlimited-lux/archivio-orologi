"""Interfaccia inglese del modulo Firma (NFR-25).

Costruisce in italiano e poi in inglese i dialoghi, il pannello, la barra e gli avvisi
del modulo e confronta tutti i testi visibili: segnala quelli rimasti identici nelle due
lingue (esclusi nomi propri, sigle, numeri e codici) o che in inglese contengono parole
solo italiane.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QTabWidget, QTextBrowser, QWidget,
)

from foliant import i18n
from foliant.modules.sign import digital, store

from .conftest import PASSWORD

ITALIAN_ONLY = {
    "il", "lo", "gli", "di", "del", "della", "delle", "dei", "degli", "per", "con", "non", "firma", "firme",
    "firmato", "campo", "campi", "documento", "nessun", "nessuna", "annulla", "salva", "modifica", "pagina",
    "una", "che", "alla", "sul", "sulla", "più", "già", "questo", "questa", "copia", "senza", "verifica",
    "certificato", "attendibile", "motivo", "luogo", "contatto", "scade", "valida", "dettagli", "chiudi",
    "crea", "elimina", "rinomina", "immagine", "disegna", "digita", "iniziali", "libreria", "nome",
    "ora", "tutte", "altre", "pari", "dispari", "intervallo", "applica", "sfoglia", "obbligatorio", "aspetto",
    "marca", "temporale", "nuovo", "pagine", "mostra", "usa", "colore", "spessore", "soglia", "sfondo",
    "ruota", "inclinazione", "versione", "rapporto", "esporta", "vai", "considera",
}
# testi (o parole) uguali nelle due lingue per natura: nomi, sigle, formati, dati di prova
ALLOWED_WORDS = {
    "pdf", "pem", "der", "cer", "sha", "ecdsa", "rsa", "p-256", "url", "oid", "pades", "tsa", "ocsp", "crl",
    "it", "en", "english", "italiano", "mario", "rossi", "lucia", "bianchi", "firma1", "dejavu", "serif",
    "sans", "mono", "liberation", "freeserif", "freesans", "freemono", "bitstream", "charter", "caladea",
    "carlito", "inter", "noto", "helvetica", "png", "jpg", "jpeg", "bmp", "tif", "tiff", "gif", "webp", "p12",
    "pfx", "crt", "p7b", "p7c", "xml", "txt", "orologeria", "verdi", "https", "http", "id", "utc", "cms",
    "milano", "approvo", "b-b", "b-t", "gg", "mm", "aaaa", "hh", "ss", "x509", "a4", "ok",
    "e-mail", "file", "password", "example", "org", "scroll", "left", "right",
}


def _texts(widget: QWidget) -> list[tuple[str, str]]:
    """(dove, testo) per tutti i testi visibili all'utente del widget e dei figli."""
    out: list[tuple[str, str]] = []

    def add(where, text):
        if text and text.strip():
            out.append((where, text.strip()))

    for w in [widget] + widget.findChildren(QWidget):
        cls = type(w).__name__
        add(f"{cls}.windowTitle", w.windowTitle() if w.isWindow() else "")
        add(f"{cls}.toolTip", w.toolTip())
        add(f"{cls}.accessibleName", w.accessibleName())
        if isinstance(w, QLabel):
            add("QLabel", w.text())
        elif isinstance(w, QAbstractButton):
            add("Button", w.text())
        elif isinstance(w, QLineEdit):
            add("QLineEdit.placeholder", w.placeholderText())
        elif isinstance(w, QComboBox):
            add("QComboBox.placeholder", w.placeholderText())
            for i in range(w.count()):
                add("QComboBox.item", w.itemText(i))
        elif isinstance(w, QGroupBox):
            add("QGroupBox", w.title())
        elif isinstance(w, QTabWidget):
            for i in range(w.count()):
                add("QTabWidget.tab", w.tabText(i))
        elif isinstance(w, QListWidget):
            for i in range(w.count()):
                add("QListWidget.item", w.item(i).text())
        elif isinstance(w, (QTextBrowser, QPlainTextEdit)):
            add(cls, w.toPlainText())
        for a in w.actions():
            add("QAction", a.text())
    return out


def _words(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'’-]*", text)]


def _problems(it_texts, en_texts) -> list[str]:
    from PySide6.QtGui import QFontDatabase
    # nomi di font, codici, dati della macchina: non sono testi dell'interfaccia
    system = set(QFontDatabase.families())
    problems = []
    en_set = {t for _w, t in en_texts}
    for where, text in en_texts:
        bad = [w for w in _words(text) if w in ITALIAN_ONLY]
        if bad:
            problems.append(f"{where}: «{text}» contiene parole italiane {bad}")
    for where, text in it_texts:
        if text in en_set and text not in system:
            words = [p for w in _words(text) if w not in ALLOWED_WORDS for p in w.split("-")
                     if len(p) >= 3 and p not in ALLOWED_WORDS and not p.isdigit()]
            if words:
                problems.append(f"{where}: «{text}» identico nelle due lingue")
    return sorted(set(problems))


def _collect(build) -> list[tuple[str, str]]:
    """Costruisce i widget con `build()` e ne raccoglie i testi."""
    texts = []
    for w in build():
        if isinstance(w, QWidget):
            w.adjustSize()
            texts += _texts(w)
        else:
            texts += [("text", str(w))]
    return texts


@pytest.fixture
def english(qapp):
    yield
    i18n.set_language("it")


def _signed_file(tmp_path, id_a, name="f.pdf", **opts) -> Path:
    from tests.conftest import make_pdf
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = Path(make_pdf(tmp_path / ("base-" + name), pages=2)).read_bytes()
    data, _n = digital.add_signature_field(data, "Cliente", 1, (300, 700, 500, 760), required=True,
                                           tooltip="Cliente")
    out = digital.sign_pdf(data, id_a[2], digital.SignOptions(rect=(60, 700, 250, 760), reason="Approvo",
                                                              location="Milano", **opts))
    p = tmp_path / name
    p.write_bytes(out)
    return p


def _build_all(qtbot, settings, tmp_path, id_a, monkeypatch):
    """Tutti i dialoghi, il pannello, la barra e gli avvisi del modulo nella lingua corrente."""
    from foliant.modules.sign import ui_create, ui_digital, ui_library
    from foliant.modules.sign.panel import SignaturesPanel, status_details
    from foliant.ui.main_window import MainWindow
    win = MainWindow(settings)
    qtbot.addWidget(win)
    c = win.ctx.sign_controller
    store.TrustStore().add(id_a[1])
    store.IDRegistry().add(id_a[0], id_a[1])
    store.TSAConfig().add("TSA", "https://tsa.example.org", user="mario")
    view = win.open_path(str(_signed_file(tmp_path, id_a)))
    st = c.state(view)
    widgets: list = []
    panel = SignaturesPanel(c)
    qtbot.addWidget(panel)
    panel.set_view(view)
    widgets.append(panel)
    for s in st.statuses:
        widgets.append(status_details(s))
    widgets.append(digital.summary(st.statuses, c.empty_fields(view)))
    widgets.append(win.modes["firma"].toolbar)
    # barra sopra il documento
    c.update_banner(view)
    assert "firme" in win.ctx.banners(view)
    widgets += win.findChildren(QWidget, "documentBanner")
    # dialoghi
    lib = c.library(interactive=False)
    from foliant.modules.sign import graphic as g
    lib.add(g.SignatureItem(art=g.strokes_to_art(g.process_strokes([[(0, 0, 1), (50, 10, 1)]]))), "Mario")
    for dlg in (ui_create.CreateSignatureDialog(win, default_name="Mario Rossi"),
                ui_library.LibraryDialog(lib, win, controller=c),
                ui_digital.DigitalSignDialog(win, c),
                ui_digital.DigitalSignDialog(win, c, certify=True),
                ui_digital.DigitalSignDialog(win, c, visible=False),
                ui_digital.FieldDialog(win),
                ui_digital.SelfSignedDialog(win, c),
                ui_digital.IDsDialog(win, c),
                ui_digital.RepeatDialog(win, 10, 0, preview=lambda pages, anchor: []),
                ui_digital.SignPreferences(win, c),
                ui_library.SyncDialog(lib, c, win)):
        qtbot.addWidget(dlg)
        widgets.append(dlg)
        if isinstance(dlg, ui_create.CreateSignatureDialog):
            for i in range(dlg.tabs.count()):
                dlg.tabs.setCurrentIndex(i)
    # avvisi SIG-21 / NFR-09 (QMessageBox costruiti e letti senza mostrarli)
    boxes = []

    def fake_exec(box):
        boxes.append(box)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    # l'etichetta dell'operazione arriva già tradotta dal modulo che la esegue
    c._ask_invalidation(view, "Ruota pagina" if i18n.language() == "it" else "Rotate page")
    c._ask_full_rewrite(view, False)
    c._ask_full_rewrite(view, True)
    c._ask_timestamp_failure("x", "https://tsa.example.org")
    for b in boxes:
        widgets.append(b)
        widgets += [b.text(), b.informativeText()]
    # aspetto della firma visibile con i valori predefiniti
    from datetime import datetime, timezone

    class Info:
        subject = "Mario Rossi"
        subject_dn = "CN=Mario Rossi"
    widgets += digital._appearance_lines(Info, digital.SignOptions(), datetime.now(timezone.utc),
                                         ["nome", "data", "etichette"])
    dsd = ui_digital.DigitalSignDialog(win, c)
    qtbot.addWidget(dsd)
    widgets.append(dsd.reason.currentText())
    return widgets, win


def test_english_interface_has_no_italian_leftovers(qtbot, settings, tmp_path, id_a, monkeypatch, english):
    i18n.set_language("it")
    it_texts = _collect(lambda: _build_all(qtbot, settings, tmp_path / "it", id_a, monkeypatch)[0])
    i18n.set_language("en")
    en_texts = _collect(lambda: _build_all(qtbot, settings, tmp_path / "en", id_a, monkeypatch)[0])
    assert len(en_texts) > 150
    problems = _problems(it_texts, en_texts)
    assert not problems, "\n".join(problems)


def test_appearance_follows_interface_language(qapp, english):
    from datetime import datetime, timezone

    class Info:
        subject = "Mario Rossi"
        subject_dn = "CN=Mario Rossi"
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    i18n.set_language("en")
    lines = digital._appearance_lines(Info, digital.SignOptions(), now, ["nome", "data", "etichette"])
    assert lines[0] == "Digitally signed by Mario Rossi" and lines[1].startswith("Date: ")
    assert store.AppearanceProfile("x").label_language == "en"
    # un profilo salvato in italiano resta in italiano
    lines = digital._appearance_lines(Info, digital.SignOptions(), now, ["nome", "etichette"],
                                      {"label_language": "it"})
    assert lines == ["Firmato digitalmente da Mario Rossi"]
    i18n.set_language("it")
    assert store.AppearanceProfile("x").label_language == "it"


def test_page_range_accepts_english_words():
    from foliant.modules.sign.graphic import parse_page_range
    assert parse_page_range("all", 4) == [0, 1, 2, 3]
    assert parse_page_range("even", 4) == [1, 3]
    assert parse_page_range("all but current", 4, current=1) == [0, 2, 3]


def test_catalog_uses_american_english():
    """Gli altri moduli usano l'inglese americano: «Color», non «Colour»."""
    from foliant.modules.sign import _translations
    british = re.compile(r"\b(colour\w*|centre\w*|grey\w*|behaviour\w*|favourite\w*|licence\w*|cancelled|"
                         r"organis\w*|recognis\w*|customis\w*|initialis\w*|optimis\w*|authoris\w*|"
                         r"finalis\w*|normalis\w*|synchronis\w*|analys(e|ed|es|ing))\b", re.I)
    bad = sorted({m.group(0) for v in _translations().values() for m in british.finditer(v)})
    assert not bad, bad


def test_font_names_are_not_interface_texts(qapp):
    """I nomi dei font installati (es. «Z003 [urw]» con Ghostscript) dipendono dalla macchina."""
    from PySide6.QtGui import QFontDatabase
    font = QFontDatabase.families()[0]
    texts = [("QListWidget.item", font), ("QLabel", "Zzz qwerty")]
    assert _problems(texts, texts) == ["QLabel: «Zzz qwerty» identico nelle due lingue"]


def test_dates_follow_interface_language(qapp, english):
    """NFR-26: le date di pannello, dettagli e dialoghi seguono la lingua (formato del nucleo)."""
    from datetime import datetime, timezone

    from foliant.modules.sign import panel, ui_digital
    dt = datetime(2026, 10, 28, 14, 30, 5, tzinfo=timezone.utc)
    i18n.set_language("it")
    assert ui_digital._date(dt) == "28/10/2026" and digital._fmt_date(dt).endswith("/2026")
    assert panel._fmt_time(dt).startswith("28/10/2026")
    i18n.set_language("en")
    assert ui_digital._date(dt) == "10/28/2026"
    assert panel._fmt_time(dt).startswith("10/28/2026") and ("AM" in panel._fmt_time(dt) or "PM" in panel._fmt_time(dt))
