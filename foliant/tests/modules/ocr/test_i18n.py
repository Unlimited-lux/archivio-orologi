"""NFR-25: tutti i dialoghi e i pannelli OCR sono tradotti in inglese."""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest
from PySide6.QtGui import QImage

from foliant import i18n
from foliant.modules.ocr import dialogs, engine, layout, ui
from foliant.modules.ocr.tess import Line, Word
from foliant.modules.ocr.translations import EN

from .conftest import FRASI, image_pdf, scan_image
from .i18n_scan import problems, texts


@pytest.fixture
def both_languages():
    i18n.add_translations("en", EN)

    def build(factory):
        out = {}
        for lang in ("it", "en"):
            i18n.set_language(lang)
            try:
                w = factory()
                out[lang] = texts(w)
                w.deleteLater()
            finally:
                i18n.set_language("it")
        return out
    yield build
    i18n.set_language("it")


def _result():
    words = [Word("contratto", (10, 10, 80, 30), 40.0), Word("orologio", (90, 10, 160, 30), 95.0)]
    res = engine.PageResult(index=0, lines=[Line((10, 10, 160, 30), words)], size=(400, 400), zoom=1.0)
    res.notes.append(i18n._("Risoluzione bassa ({d} dpi): accuratezza ridotta.").format(d=99))
    skipped = engine.PageResult(index=1, status="skipped", message=i18n._("testo presente"))
    failed = engine.PageResult(index=2, status="failed", message="x")
    return engine.OcrResult(pages=[res, skipped, failed], options=engine.OcrOptions(languages=["ita"]))


def _qimage():
    img = np.full((200, 150, 3), 255, np.uint8)
    return QImage(img.data, 150, 200, 450, QImage.Format.Format_RGB888).copy()


def test_dialoghi_tradotti(window, settings, both_languages, tmp_path):
    pdf_path = image_pdf(tmp_path / "s.pdf", [scan_image(lines=FRASI[:1], dpi=72)] * 3, dpi=72)
    factories = {
        "Esegui OCR": lambda: dialogs.OcrDialog(window, page_count=3, current_page=0, detected=[0, 2],
                                               settings=settings, selected=[0, 1], page_image_cb=lambda: None),
        "Lingue": lambda: dialogs.LanguagesDialog(window),
        "Testo": lambda: dialogs.TextDialog(window, "testo", result=_result(), save_path_cb=lambda *a: None,
                                            actions=[(i18n._("Aggiungi come testo ricercabile"), lambda: None)]),
        "Revisione": lambda: dialogs.ReviewDialog(window, _result(), pymupdf.open(pdf_path)),
        "Blocco": lambda: dialogs.BatchDialog(window, settings),
        "Rapporto": lambda: dialogs.ReportDialog(window, i18n._("OCR in blocco: risultato"), []),
        "Zone": lambda: dialogs.ZoneEditor(window, _qimage(), [layout.Zone((0.1, 0.1, 0.5, 0.5))], settings),
    }
    report = {}
    for name, factory in factories.items():
        got = both_languages(factory)
        assert len(got["en"]) >= 3, (name, got["en"])
        for wrong in ("Undo", "Copying", "Proofread"):     # chiavi ambigue del catalogo comune
            assert wrong not in got["en"], (name, wrong)
        bad = problems(got["it"], got["en"])
        if bad:
            report[name] = bad
    assert not report, report


def test_pannelli_banner_e_messaggi_tradotti(window, both_languages, scan_two_path):
    from foliant.modules import ocr
    ctl = ocr.controller
    view = window.open_path(scan_two_path)
    got = both_languages(lambda: ui.ReviewPanel(ctl))
    assert not problems(got["it"], got["en"])
    assert "Correct" in got["en"] and "Proofread" not in got["en"]
    got = both_languages(lambda: ui.JobBar(view, lambda: None, lambda: None))
    assert not problems(got["it"], got["en"])
    # testi del banner e messaggi finali (stringhe, non widget)
    out = {}
    for lang in ("it", "en"):
        i18n.set_language(lang)
        try:
            bar = ui.ProposalBar(view, ctl)
            bar.show_document(3, ["deu"], True, True, 1)
            doc_texts = [bar.text] + [a[0] for a in bar.actions]
            bar.show_page(0, 2)
            page_texts = [bar.text] + [a[0] for a in bar.actions]
            msgs = [i18n._("Testo riconosciuto in {n} pagine ({w} parole, {l} incerte).").format(n=1, w=2, l=0),
                    i18n._("Nessuna pagina riconosciuta."), i18n._("Le pagine {p} sono vuote.").format(p="1"),
                    i18n._("Riconoscimento annullato: il documento non è stato modificato.")]
            out[lang] = doc_texts + page_texts + msgs
            bar.hide()
        finally:
            i18n.set_language("it")
    assert not problems(out["it"], out["en"]), problems(out["it"], out["en"])


@pytest.fixture
def scan_two_path(tmp_path):
    return image_pdf(tmp_path / "scan.pdf", [scan_image(lines=FRASI[:2], dpi=100)], dpi=100)
