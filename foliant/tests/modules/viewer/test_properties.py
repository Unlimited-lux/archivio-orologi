"""Informazioni sul documento (VIS-40): file, contenuto, protezione, caratteri, apertura."""

from __future__ import annotations

from PySide6.QtGui import QKeySequence
import hashlib

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from foliant.modules.viewer.properties import PropertiesDialog
from foliant.modules.viewer.properties_engine import (
    FontInfo, describe_page_size, font_from_entry, format_pdf_date, format_size, paper_name, parse_pdf_date,
)

from .conftest import part


@pytest.fixture
def meta_pdf(tmp_path):
    path = tmp_path / "metadati.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Testo Helvetica", fontname="helv", fontsize=14)
    page.insert_font(fontname="F1", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 140), "Testo incorporato", fontname="F1", fontsize=14)
    second = doc.new_page(width=612, height=792)
    second.insert_text((72, 100), "Pagina Letter", fontname="helv")
    doc.set_metadata({
        "title": "Contratto di vendita",
        "author": "Mario Rossi",
        "subject": "Orologio da tasca",
        "keywords": "contratto, orologio",
        "creator": "Scrittore 1.0",
        "producer": "Motore PDF",
        "creationDate": "D:20240315103000+01'00'",
        "modDate": "D:20241201080510Z",
    })
    doc.set_toc([[1, "Inizio", 1], [1, "Fine", 2]])
    doc.save(str(path))
    doc.close()
    return str(path)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ------------------------------------------------------------------ motore
def test_parse_and_format_pdf_dates():
    dt = parse_pdf_date("D:20240315103000+01'00'")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 3, 15, 10, 30)
    assert dt.utcoffset().total_seconds() == 3600
    assert format_pdf_date("D:20240315103000+01'00'") == "15/03/2024 10:30:00 UTC+01:00"
    assert format_pdf_date("D:20241201080510Z") == "01/12/2024 08:05:10 UTC"
    assert format_pdf_date("D:20240315103000-05'30'").endswith("UTC-05:30")
    assert format_pdf_date("D:2023") == "01/01/2023 00:00:00"
    assert format_pdf_date("") == ""
    assert format_pdf_date("ieri") == "ieri"          # non interpretabile: invariato
    assert parse_pdf_date("D:20241345") is None       # mese non valido


def test_paper_names():
    assert paper_name(595, 842) == "A4"
    assert paper_name(595.28, 841.89) == "A4"
    assert paper_name(842, 595) == "A4"
    assert paper_name(842, 1191) == "A3"
    assert paper_name(612, 792) == "Letter"
    assert paper_name(612, 1008) == "Legal"
    assert paper_name(500, 500) is None
    text = describe_page_size(595, 842)
    assert text.startswith("A4 verticale") and "209,9 × 297,0 mm" in text and "595,00 × 842,00 pt" in text
    assert describe_page_size(842, 595).startswith("A4 orizzontale")


def test_format_size():
    assert format_size(512) == "512 byte"
    assert format_size(2048).startswith("2,0 KB")
    assert format_size(3 * 1024 * 1024).startswith("3,00 MB")


def test_font_entry_embedding_states():
    assert font_from_entry((5, "n/a", "Type1", "Helvetica", "helv", "WinAnsiEncoding", 0)).embedding == "not_embedded"
    sub = font_from_entry((7, "ttf", "TrueType", "ABCDEF+DejaVuSans", "F1", "", 0))
    assert sub == FontInfo("DejaVuSans", "TrueType", "", "subset")
    assert font_from_entry((8, "cff", "Type1C", "Nimbus", "F2", "", 0)).embedding == "embedded"


# ---------------------------------------------------------------- raccolta
def test_collect_metadata_and_file(viewer_window, meta_pdf):
    viewer_window.open_path(meta_pdf)
    ctl = part(viewer_window, "properties")
    info = ctl.collect(viewer_window.current_view())
    assert info.title == "Contratto di vendita"
    assert info.author == "Mario Rossi"
    assert info.subject == "Orologio da tasca"
    assert info.keywords == "contratto, orologio"
    assert info.creator == "Scrittore 1.0" and info.producer == "Motore PDF"
    assert info.created == "15/03/2024 10:30:00 UTC+01:00"
    assert info.modified == "01/12/2024 08:05:10 UTC"
    assert info.path == meta_pdf
    assert info.file_size and info.file_size > 0
    assert info.page_count == 2
    assert info.pdf_version.startswith("PDF 1.")
    assert info.page_size.startswith("A4 verticale")
    assert info.bookmarks == 2
    assert not info.security.encrypted
    assert not info.tagged and not info.fast_web_view


def test_current_page_size_follows_view(viewer_window, meta_pdf):
    viewer_window.open_path(meta_pdf)
    view = viewer_window.current_view()
    view.go_to_page(1)
    info = part(viewer_window, "properties").collect(view)
    assert info.current_page == 1 and info.page_size.startswith("Letter")


def test_fonts_with_embedding_state(viewer_window, meta_pdf):
    viewer_window.open_path(meta_pdf)
    info = part(viewer_window, "properties").collect(viewer_window.current_view())
    by_state = {f.embedding for f in info.fonts}
    assert "not_embedded" in by_state
    assert by_state & {"embedded", "subset"}
    helv = [f for f in info.fonts if f.name == "Helvetica"]
    assert len(helv) == 1                     # niente duplicati tra le pagine
    assert helv[0].embedding_label() == "Non incorporato"


def test_large_document_fonts_via_task(viewer_window, tmp_path, monkeypatch):
    from foliant.modules.viewer import properties as props_mod
    monkeypatch.setattr(props_mod, "LARGE_DOCUMENT_PAGES", 2)
    path = tmp_path / "grande.pdf"
    doc = pymupdf.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"P{i}")
    doc.save(str(path))
    viewer_window.open_path(str(path))
    titles = []
    original = viewer_window.ctx.run_task

    def spy(title, fn, *a, **k):
        titles.append(title)
        return original(title, fn, *a, **k)

    monkeypatch.setattr(viewer_window.ctx, "run_task", spy)
    info = part(viewer_window, "properties").collect(viewer_window.current_view())
    assert titles and len(info.fonts) == 1


def test_encrypted_shows_method_and_permissions(viewer_window, tmp_path):
    path = tmp_path / "cifrato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Riservato")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT)
    viewer_window.open_path(str(path))
    dlg = part(viewer_window, "properties").show_dialog()
    sec = dlg.info.security
    assert sec.encrypted and "AES 256" in sec.method and not sec.owner_access
    text = dlg.text()
    assert "AES 256 bit" in text
    assert "Stampa del documento: Consentito" in text
    assert "Modifica del contenuto: Non consentito" in text
    assert "Copia ed estrazione di testo e immagini: Non consentito" in text
    assert "Stampa ad alta qualità: Non consentito" in text


def test_encrypted_with_user_password(viewer_window, encrypted_pdf, monkeypatch):
    viewer_window.open_path(encrypted_pdf, "segreta")
    info = part(viewer_window, "properties").collect(viewer_window.current_view())
    assert info.security.encrypted and "AES" in info.security.method


def test_tagged_and_advanced(viewer_window, tmp_path):
    path = tmp_path / "taggato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<</Marked true>>")
    doc.embfile_add("nota.txt", b"ciao")
    doc.add_ocg("Livello 1")
    widget = pymupdf.Widget()
    widget.field_name = "nome"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(72, 100, 200, 120)
    doc[0].add_widget(widget)
    doc.save(str(path))
    viewer_window.open_path(str(path))
    info = part(viewer_window, "properties").collect(viewer_window.current_view())
    assert info.tagged
    assert info.attachments == 1 and info.layers == 1 and info.form_fields == 1
    assert info.objects > 5


def test_dialog_tabs_copy_and_no_modification(viewer_window, meta_pdf, qtbot):
    before = _sha(meta_pdf)
    viewer_window.open_path(meta_pdf)
    doc = viewer_window.current_view().document
    version = doc.version
    ctl = part(viewer_window, "properties")
    dlg = ctl.show_dialog()
    assert isinstance(dlg, PropertiesDialog) and dlg.isVisible() and not dlg.isModal()
    tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert tabs == ["File", "Contenuto", "Protezione", "Caratteri", "Apertura"]
    assert dlg.font_tree.topLevelItemCount() == len(dlg.info.fonts) >= 2
    dlg.copy_button.click()
    clip = QApplication.clipboard().text()
    assert "Titolo: Contratto di vendita" in clip and "Versione PDF: PDF 1." in clip and "Helvetica" in clip
    # sola lettura: nessuna modifica al documento né al file
    assert not doc.is_modified and doc.version == version and not doc.can_undo
    assert _sha(meta_pdf) == before
    dlg.close()


def test_action_menu_and_shortcut(viewer_window, sample_pdf):
    ctl = part(viewer_window, "properties")
    assert ctl.action.shortcut() == QKeySequence("Alt+Return")
    assert ctl.action in viewer_window.modes["leggi"].toolbar.actions()
    assert any(ctl.action in acts for acts in viewer_window._menu_sections.values())
    assert not ctl.action.isEnabled()
    assert ctl.show_dialog() is None
    viewer_window.open_path(sample_pdf)
    assert ctl.action.isEnabled()
    ctl.action.trigger()
    assert ctl.dialog is not None and ctl.dialog.isVisible()
    ctl.dialog.close()


def test_english_translation(viewer_window, meta_pdf):
    from foliant.i18n import set_language
    viewer_window.open_path(meta_pdf)
    set_language("en")
    try:
        info = part(viewer_window, "properties").collect(viewer_window.current_view())
        assert info.created == "2024-03-15 10:30:00 UTC+01:00"
        dlg = PropertiesDialog(info, "x", viewer_window)
        assert dlg.tabs.tabText(2) == "Security"
        assert "Printing: Allowed" in dlg.text()
        dlg.deleteLater()
    finally:
        set_language("it")


def test_opening_section_reads_document_settings(viewer_window, tmp_path):
    import pymupdf
    path = tmp_path / "apertura.pdf"
    doc = pymupdf.open()
    for _i in range(5):
        doc.new_page()
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "PageLayout", "/TwoColumnLeft")
    doc.xref_set_key(cat, "PageMode", "/UseOutlines")
    doc.xref_set_key(cat, "OpenAction", f"[{doc[2].xref} 0 R /FitH 800]")
    doc.save(str(path))
    viewer_window.open_path(str(path))
    ctl = viewer_window.ctx.viewer_parts["properties"]
    info = ctl.collect(viewer_window.current_view())
    rows = dict(info.opening)
    assert rows["Pagina iniziale"] == "3"
    assert rows["Pannello all'apertura"] == "segnalibri"
    assert "Apertura" in ctl.show_dialog().text() if hasattr(ctl.show_dialog(), "text") else True
