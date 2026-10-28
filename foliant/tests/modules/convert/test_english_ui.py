"""Interfaccia inglese di Converti (NFR-25): nessun testo rimasto in italiano.

Apre finestre, pannelli e azioni del modulo in italiano e in inglese e raccoglie ogni
testo visibile (etichette, pulsanti, voci delle combo, segnaposto, suggerimenti, titoli,
intestazioni, suffissi). Segnala i testi inglesi uguali a quelli italiani (esclusi sigle,
numeri, codici e nomi propri) o che contengono parole italiane.
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QTableWidget,
    QWidget,
)

from foliant.i18n import set_language

# testi identici nelle due lingue ammessi (sigle, formati, unità, nomi propri)
ALLOWED = {
    "PNG", "JPEG", "TIFF", "BMP", "GIF", "CSV", "RTF", "HTML", "XML", "EPUB", "LZW", "Deflate", "UTF-8", "UTF-16",
    "LF", "CRLF", "OCR", "ita+eng", "mm", "pt", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5", "Letter",
    "Legal", "Tabloid", "16:9", "4:3", "Preset", "Portfolio", "Scanner", "PDF/A-1b", "PDF/A-2b", "PDF/A-3b",
    "PDF/A-2u", "PDF/A-3u", "PDF/X-3", "×", "ppi", "dpi", "OK", "Layout", "Word (DOCX)", "Excel (XLSX)",
    "PowerPoint (PPTX)",
}
# dati inseriti dal test (nomi di file, cartelle, dispositivi), non testi dell'interfaccia
TEST_DATA = {"doc", "Sim", "Cartella", "img.png", "a.pdf", "desc"}
ITALIAN_WORDS = re.compile(
    r"\b(di|del|della|delle|dei|degli|il|lo|gli|una|uno|con|senza|pagine|pagina|nessun[oa]?|tutte|"
    r"file creati|crea|apri|salva|annulla|chiudi|cartella|sfoglia|immagini|qualità|risoluzione|colore|"
    r"formato|stampa|documento|testo|nome|data|elenco|miniature|copertina|ordina|ruota|rimuovi|"
    r"aggiungi|scegli|riduzione|dimensione|livello|anteprima|campi|livelli|ritaglio)\b", re.IGNORECASE)


def _texts(widget: QWidget) -> list[str]:
    out = [widget.windowTitle()] if widget.isWindow() else []
    for w in [widget] + widget.findChildren(QWidget):
        if w.toolTip():
            out.append(w.toolTip())
        if isinstance(w, QLabel):
            out.append(w.text())
        elif isinstance(w, QAbstractButton):
            out.append(w.text())
        elif isinstance(w, QComboBox):
            out.extend(w.itemText(i) for i in range(w.count()))
        elif isinstance(w, QLineEdit):
            out.extend([w.placeholderText()] + ([w.text()] if not w.isReadOnly() else []))
        elif isinstance(w, QGroupBox):
            out.append(w.title())
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            out.extend([w.suffix(), w.specialValueText()])
        elif isinstance(w, QTableWidget):
            out.extend(w.horizontalHeaderItem(c).text() for c in range(w.columnCount())
                       if w.horizontalHeaderItem(c))
    return [t.strip() for t in out if t and t.strip()]


def _suspicious(it: list[str], en: list[str]) -> list[str]:
    it_set = set(it)
    bad = []
    for t in en:
        plain = re.sub(r"\{[^}]*\}|[\d.,:%]+", " ", t).strip()
        if not re.search(r"[A-Za-zÀ-ÿ]{3,}", plain) or t in ALLOWED or t in TEST_DATA:
            continue
        if t.startswith(("/", "#")) or re.fullmatch(r"[\w.-]+\.\w{2,4}", t):
            continue      # percorsi, colori, nomi di file
        if t in it_set and not all(w in ALLOWED or w.isupper() for w in re.findall(r"[A-Za-zÀ-ÿ/+-]+", plain)):
            bad.append(t)
        elif ITALIAN_WORDS.search(plain):
            bad.append(t)
    return sorted(set(bad))


def _dialogs(window, tmp_path):
    """Costruttori delle finestre del modulo (stessi argomenti nelle due lingue)."""
    from PIL import Image
    from foliant.modules.convert.batch_dialog import BatchDialog
    from foliant.modules.convert.create_dialogs import (
        BlankDialog, CameraDialog, CaptureDialog, ImagesDialog, OfficeOptionsDialog, PhotosDialog, ScannerDialog,
    )

    class _Cam:
        def open(self, index):
            pass

        def read(self):
            return None

        def close(self):
            pass
    from foliant.modules.convert.export_dialog import FORMAT_ORDER, ExportDialog
    from foliant.modules.convert.optimize_dialogs import AdvancedDialog, CompressDialog, FlattenDialog
    from foliant.modules.convert.portfolio_dialog import EditPortfolioDialog, PortfolioDialog
    from PySide6.QtGui import QImage
    img = tmp_path / "img.png"
    Image.new("RGB", (40, 30), "white").save(img)
    ctx = window.ctx
    makers = []
    for fmt in FORMAT_ORDER:
        makers.append((f"export:{fmt}", lambda f=fmt: ExportDialog(window, page_count=3, name="doc",
                                                                    folder=str(tmp_path), fmt=f, scanned=True)))
    makers += [
        ("images", lambda: ImagesDialog(window, [str(img)])),
        ("blank", lambda: BlankDialog(window)),
        ("scanner", lambda: ScannerDialog(window, [("sim:0", "Sim")], has_document=True, preview=lambda d, s: None)),
        ("office", lambda: OfficeOptionsDialog(window, kinds={"sheet", "slides"})),
        ("camera", lambda: CameraDialog(window, _Cam(), [(0, "Sim")])),
        ("photos", lambda: PhotosDialog(window, [str(img)], ask_more=lambda: [])),
        ("capture", lambda: CaptureDialog(window, QImage(80, 60, QImage.Format.Format_RGB32), 1)),
        ("compress", lambda: CompressDialog(window, before=1000, estimates={"lossless": 900, "high": 500,
                                                                             "medium": 400, "low": 300})),
        ("advanced", lambda: AdvancedDialog(window, analysis=[("Immagini", 10), ("Font", 5), ("Altro", 1)])),
        ("flatten", lambda: FlattenDialog(window, 3)),
        ("batch", lambda: BatchDialog(window, ctx)),
        ("portfolio", lambda: PortfolioDialog(window, [str(img)])),
        ("edit-portfolio", lambda: EditPortfolioDialog(window, [("a.pdf", 10, "desc")], ["Cartella"])),
    ]
    return makers


def test_dialogs_fully_english(window, tmp_path):
    results = {}
    for lang in ("it", "en"):
        set_language(lang)
        try:
            for name, make in _dialogs(window, tmp_path):
                dlg = make()
                results.setdefault(name, {})[lang] = _texts(dlg)
                dlg.deleteLater()
        finally:
            set_language("it")
    bad = {name: _suspicious(r["it"], r["en"]) for name, r in results.items()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, "testi non tradotti:\n" + "\n".join(f"  {k}: {v}" for k, v in bad.items())


def test_actions_and_home_english(qtbot, settings):
    from PySide6.QtWidgets import QApplication
    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow
    texts = {}
    for lang in ("it", "en"):
        set_language(lang)
        try:
            theme.apply(QApplication.instance(), "light")
            win = MainWindow(settings)
            qtbot.addWidget(win)
            c = win.ctx.convert_controller
            acts = list(c.actions.values()) + list(win.modes["converti"].toolbar.actions())
            out = [a.text() for a in acts] + [a.toolTip() for a in acts]
            out += [c.create_button.text(), c.create_button.toolTip()]
            out += [t for _o, t, _i, _cb, d in win.home._actions for t in (t, d)]
            texts[lang] = [t for t in out if t]
            win.close()
        finally:
            set_language("it")
    assert _suspicious(texts["it"], texts["en"]) == []


def test_generated_names_and_reports_english(sample_pdf, tmp_path):
    from foliant.i18n import _
    from foliant.modules.convert.batch import BatchItem, report_csv
    from foliant.modules.convert.engine import run_conversion
    set_language("en")
    try:
        result = run_conversion(sample_pdf, tmp_path / "r.txt", "txt")
        report = result.report_text()
        csv_text = report_csv([BatchItem(sample_pdf, "completato", [str(tmp_path / "r.txt")], "")])
        suffixes = [_("_compresso"), _("_personalizzato"), _("_riparato")]
        from foliant.modules.convert.export_dialog import build_options
        panel = build_options("png")
        template = panel.fields["name_template"].text()
        images = __import__("foliant.modules.convert.engine", fromlist=["convert_pdf"]).convert_pdf(
            sample_pdf, tmp_path / "img" / "doc.png", "png", dpi=50)
    finally:
        set_language("it")
    assert not ITALIAN_WORDS.search(report), report
    assert "completed" in csv_text and not ITALIAN_WORDS.search(csv_text.splitlines()[0])
    assert suffixes == ["_compressed", "_custom", "_repaired"]
    assert template == "{name}_{page:03}"
    assert [p.rsplit("/", 1)[-1] for p in images] == ["doc_001.png", "doc_002.png", "doc_003.png"]


def test_sizes_use_language_decimal_separator():
    """Collaudo C11 (NFR-26): «1,4 KB» in italiano, «1.4 KB» in inglese."""
    from foliant.modules.convert.optimize_dialogs import human_size
    from foliant.modules.convert.portfolio import _size
    try:
        set_language("en")
        assert human_size(1434) == "1.4 KB" and _size(1434) == "1.4 KB"
        assert human_size(5 * 1024 ** 2 + 200 * 1024) == "5.2 MB"
        set_language("it")
        assert human_size(1434) == "1,4 KB" and _size(1434) == "1,4 KB"
    finally:
        set_language("it")
