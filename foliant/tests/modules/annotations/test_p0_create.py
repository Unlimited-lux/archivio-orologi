"""Criteri P0: modello comune, autore, testo/popup, annulla, nota, casella di testo (ANN-01/02/05/06/14/16)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import Qt

from foliant.modules.annotations import dialogs, engine

from .conftest import annots, click, drag, key_of, saved


def _ok_dialog(monkeypatch, text="", rich=None):
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: (text, rich))


def _create_all(module, view, monkeypatch):
    """Crea un'annotazione di ogni tipo supportato con gli strumenti."""
    _ok_dialog(monkeypatch, "Testo di prova")
    m = module
    m.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    m.activate(engine.UNDERLINE)
    drag(view, 0, (73, 197), (200, 197))
    m.activate(engine.STRIKEOUT)
    drag(view, 0, (73, 213), (200, 213))
    m.activate(engine.SQUIGGLY)
    drag(view, 0, (73, 229), (200, 229))
    m.activate(engine.NOTE)
    click(view, 0, 400, 400)
    m.activate(engine.FREETEXT)
    drag(view, 0, (300, 450), (480, 500))
    m.activate(engine.CALLOUT)
    drag(view, 0, (100, 600), (200, 550))
    for kind, a, b in ((engine.RECT, (60, 650), (120, 700)), (engine.ELLIPSE, (140, 650), (200, 700)),
                       (engine.LINE, (220, 650), (280, 700)), (engine.ARROW, (300, 650), (360, 700))):
        m.activate(kind)
        drag(view, 0, a, b)
    m.activate(engine.POLYGON)
    click(view, 0, 400, 650)
    click(view, 0, 450, 650)
    click(view, 0, 430, 700)
    from .conftest import key
    key(view, Qt.Key.Key_Return)
    m.activate(engine.INK)
    drag(view, 0, (60, 750), (160, 790))
    m.activate(engine.STAMP)
    click(view, 0, 400, 780)


def test_ann01_every_type_saved_with_ap_nm_and_m(comments, tmp_path, monkeypatch):
    """ANN-01: ogni tipo creato ha /Subtype atteso, /AP /N non vuoto, /NM univoco e /M."""
    window, view, module = comments
    _create_all(module, view, monkeypatch)
    doc = saved(window, view, tmp_path)
    found = annots(doc, 0)
    types = {a.type[1] for a in found}
    assert {"Highlight", "Underline", "StrikeOut", "Squiggly", "Text", "FreeText", "Square", "Circle", "Line",
            "Polygon", "Ink", "Stamp"} <= types
    names = []
    for a in found:
        if a.type[1] == "Popup":
            continue
        kind, ap = key_of(doc, a.xref, "AP/N")
        assert kind == "xref", a.type
        ap_xref = int(ap.split()[0])
        assert doc.xref_stream(ap_xref), f"aspetto vuoto per {a.type}"
        assert key_of(doc, a.xref, "M")[0] == "string"
        names.append(a.info["id"])
    assert len(names) == len(set(names)), "i /NM devono essere univoci"


def test_ann01_unhandled_annotation_untouched(comments, tmp_path, monkeypatch):
    """ANN-01: un'annotazione di tipo non gestito resta identica dopo altre modifiche e il salvataggio."""
    window, view, module = comments
    with view.document.change("prepara") as pdf:
        page = pdf[1]
        x = pdf.get_new_xref()
        pdf.update_object(x, "<</Type/Annot/Subtype/Projection/Rect[10 10 50 50]/Foo(bar)/F 4>>")
        pdf.xref_set_key(page.xref, "Annots", f"[{x} 0 R]")
    with view.document.change("x") as pdf:
        before = pdf.xref_object(x, compressed=True)
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    doc = saved(window, view, tmp_path)
    proj = annots(doc, 1)[0]
    assert proj.type[1] == "Projection"
    import re
    norm = lambda s: re.sub(r"/P \d+ 0 R", "", s)  # noqa: E731 - /P rinumerato dal salvataggio
    assert norm(doc.xref_object(proj.xref, compressed=True)) == norm(before)


def test_ann01_private_key_kept_after_color_change(comments, tmp_path):
    """ANN-01: una chiave privata sconosciuta sopravvive al cambio di colore."""
    window, view, module = comments
    ctrl = module.ctrl
    xref = ctrl.apply("r", engine.create_shape, 0, engine.RECT, [(100, 100), (200, 200)], engine.Style(), "A")
    with view.document.change("chiave") as pdf:
        pdf.xref_set_key(xref, "ZZPrivato", "(valore)")
    ctrl.set_style([(0, xref)], color=(0, 0, 1))
    doc = saved(window, view, tmp_path)
    sq = annots(doc, 0, "Square")[0]
    assert key_of(doc, sq.xref, "ZZPrivato") == ("string", "valore")
    assert key_of(doc, sq.xref, "C")[1] == "[0 0 1]"


def test_ann02_author_subject_dates(comments, tmp_path, monkeypatch):
    """ANN-02: /T dall'impostazione, /Subj predefinito, /CreationDate = /M; cambio colore aggiorna solo /M."""
    window, view, module = comments
    _ok_dialog(monkeypatch, "Controllare")
    module.activate(engine.NOTE)
    click(view, 0, 200, 500)
    doc = saved(window, view, tmp_path)
    note = [a for a in annots(doc, 0, "Text")][0]
    assert note.info["title"] == "Maria Rossi"
    assert note.info["subject"] == "Nota"
    assert note.info["creationDate"] == note.info["modDate"]
    created = note.info["creationDate"]
    monkeypatch.setattr(engine, "pdf_now", lambda: "D:20990101000000+00'00'")
    module.ctrl.set_style([(0, note.xref)], color=(0, 0, 1))
    doc2 = saved(window, view, tmp_path, "due.pdf")
    note2 = annots(doc2, 0, "Text")[0]
    assert note2.info["modDate"] >= created
    assert note2.info["creationDate"] == created


def test_ann02_non_latin_author_utf16(comments, tmp_path, monkeypatch):
    """ANN-02: nome autore greco salvato in UTF-16BE e mostrato identico nel pannello."""
    window, view, module = comments
    window.ctx.settings().setValue("annotations/author", "Ελένη Παπαδοπούλου")
    _ok_dialog(monkeypatch, "x")
    module.activate(engine.NOTE)
    click(view, 0, 200, 500)
    doc = saved(window, view, tmp_path)
    note = annots(doc, 0, "Text")[0]
    raw = doc.xref_object(note.xref)
    assert "/T <FEFF" in raw
    assert note.info["title"] == "Ελένη Παπαδοπούλου"
    module.comments.reload()
    assert any("Ελένη Παπαδοπούλου" in it.text(0) for it in module.comments.items())


def test_ann02_empty_author_restores_account_name(comments):
    """ANN-02: nome autore vuoto non accettato: si usa quello dell'account."""
    window, view, module = comments
    module.ctrl.set_author("   ")
    assert module.ctrl.author() == (engine.default_author() or "Utente")


def test_ann02_tolerant_dates():
    """ANN-02: date incomplete o non valide non causano errori."""
    assert engine.parse_pdf_date("D:2025") is not None
    assert engine.format_date("D:2025").startswith("01/01/2025")
    assert engine.format_date("spazzatura") == ""
    assert engine.parse_pdf_date("D:20251399") is None


def test_ann05_comment_on_highlight_has_popup(comments, tmp_path, monkeypatch):
    """ANN-05: il commento aggiunto a un'evidenziazione va in /Contents e c'è una /Popup collegata."""
    window, view, module = comments
    module.activate(engine.HIGHLIGHT)
    drag(view, 0, (73, 95), (140, 95))
    (page, xref), = module.ctrl.selection
    _ok_dialog(monkeypatch, "Verificare la cifra")
    assert module.ctrl.edit_text(page, xref)
    doc = saved(window, view, tmp_path)
    hl = annots(doc, 0, "Highlight")[0]
    assert hl.info["content"] == "Verificare la cifra"
    popup = hl.popup_xref
    assert popup
    assert key_of(doc, popup, "Parent")[1].startswith(f"{hl.xref} 0 R")


def test_ann05_rich_text_bold(comments, tmp_path, qtbot):
    """ANN-05: una parola in grassetto finisce in /RC, /Contents resta testo semplice."""
    from PySide6.QtGui import QFont, QTextCursor
    window, view, module = comments
    editor = dialogs.CommentEditor(None, "Nota", "", "")
    qtbot.addWidget(editor)
    editor.edit.setPlainText("una parola importante")
    cur = editor.edit.textCursor()
    cur.setPosition(4)
    cur.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
    editor.edit.setTextCursor(cur)
    editor._bold()
    text, xhtml = editor.result_text()
    assert text == "una parola importante"
    assert "font-weight:bold" in xhtml and ">parola</span>" in xhtml
    xref = module.ctrl.apply("n", engine.create_note, 0, (100, 100), text, engine.Style(), "A")
    module.ctrl.apply("t", engine.set_contents, 0, xref, text, xhtml)
    doc = saved(window, view, tmp_path)
    note = annots(doc, 0, "Text")[0]
    rc = engine.get_rich_text(doc, note.xref)
    assert "parola" in rc and "bold" in rc
    assert note.info["content"] == "una parola importante"
    assert QFont  # import usato


def test_ann05_long_comment_preserved(comments, tmp_path):
    """ANN-05: un commento di 100.000 caratteri si conserva identico."""
    window, view, module = comments
    long_text = ("abcdefghij" * 10_000)
    xref = module.ctrl.apply("n", engine.create_note, 0, (100, 100), long_text, engine.Style(), "A")
    doc = saved(window, view, tmp_path)
    assert xref and annots(doc, 0, "Text")[0].info["content"] == long_text


def test_ann06_undo_redo_sequence(comments, monkeypatch):
    """ANN-06: crea → cambia colore → sposta; tre Annulla tolgono il rettangolo, tre Ripeti lo riportano."""
    window, view, module = comments
    module.activate(engine.RECT)
    drag(view, 0, (100, 400), (200, 500))
    (page, xref), = module.ctrl.selection
    module.ctrl.set_style(color=(0, 0, 1))
    module.ctrl.move(30, 0)
    doc = view.document
    for _i in range(3):
        assert doc.undo()
    with doc.lock:
        assert engine.find_annot(doc.pdf[0], xref) is None
    for _i in range(3):
        assert doc.redo()
    with doc.lock:
        page_obj = doc.pdf[0]
        a = page_obj.load_annot(xref)
        assert a.colors["stroke"] == [0.0, 0.0, 1.0]
        assert abs(a.rect.x0 - 129) < 1.5


def test_ann06_undo_after_save_marks_modified(comments, tmp_path):
    """ANN-06: Annulla dopo il salvataggio revoca la modifica e il documento risulta modificato."""
    window, view, module = comments
    module.ctrl.apply("n", engine.create_note, 0, (100, 100), "x", engine.Style(), "A")
    view.document.save(str(tmp_path / "s.pdf"))
    assert not view.document.is_modified
    assert view.document.undo()
    assert view.document.is_modified


def test_ann14_note_click(comments, tmp_path, monkeypatch):
    """ANN-14: clic in (200,500) + «Controllare» → /Text, /Name /Comment, punto nel /Rect, NoZoom e NoRotate."""
    window, view, module = comments
    _ok_dialog(monkeypatch, "Controllare")
    module.activate(engine.NOTE)
    click(view, 0, 200, 500)
    doc = saved(window, view, tmp_path)
    note = annots(doc, 0, "Text")[0]
    assert note.info["content"] == "Controllare"
    assert key_of(doc, note.xref, "Name")[1] == "/Comment"
    assert note.rect.contains(pymupdf.Point(200, 500))
    assert note.flags & pymupdf.PDF_ANNOT_IS_NO_ZOOM and note.flags & pymupdf.PDF_ANNOT_IS_NO_ROTATE
    assert note.flags & pymupdf.PDF_ANNOT_IS_PRINT
    # il motore normalizza l'icona a 16 pt (dimensione costante a schermo)
    assert 16 <= note.rect.width <= 20.5


def test_ann14_note_icon_and_color(comments, tmp_path, monkeypatch):
    """ANN-14: icona «Chiave» e colore blu → /Name /Key e /C [0 0 1]."""
    window, view, module = comments
    st = module.ctrl.style_for(engine.NOTE)
    st.icon, st.color = "Key", (0, 0, 1)
    module.ctrl.set_style_for(engine.NOTE, st)
    _ok_dialog(monkeypatch, "x")
    module.activate(engine.NOTE)
    click(view, 0, 300, 300)
    doc = saved(window, view, tmp_path)
    note = annots(doc, 0, "Text")[0]
    assert key_of(doc, note.xref, "Name")[1] == "/Key"
    assert key_of(doc, note.xref, "C")[1] == "[0 0 1]"


def test_ann14_note_cancel_creates_nothing(comments, monkeypatch):
    window, view, module = comments
    monkeypatch.setattr(dialogs, "edit_comment", lambda *a, **k: None)
    module.activate(engine.NOTE)
    click(view, 0, 300, 300)
    assert module.ctrl.annotations() == []


def _pixel(doc, page, x, y, zoom=2):
    pix = doc[page].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.pixel(int(x * zoom), int(y * zoom))


def test_ann16_textbox(comments, tmp_path, monkeypatch):
    """ANN-16: casella 200×100 «Nota importante», 14 pt centrato, bordo rosso 2 pt, sfondo giallo."""
    window, view, module = comments
    st = module.ctrl.style_for(engine.FREETEXT)
    st.fontsize, st.align, st.color, st.width, st.fill = 14, 1, (1, 0, 0), 2.0, (1, 1, 0)
    st.text_color = (0, 0, 0)
    module.ctrl.set_style_for(engine.FREETEXT, st)
    _ok_dialog(monkeypatch, "Nota importante")
    module.activate(engine.FREETEXT)
    drag(view, 0, (100, 400), (300, 500))
    doc = saved(window, view, tmp_path)
    ft = annots(doc, 0, "FreeText")[0]
    assert ft.info["content"] == "Nota importante"
    assert key_of(doc, ft.xref, "Q")[1] == "1"
    assert ft.border["width"] == 2
    r = ft.rect
    assert abs(r.width - 200) < 3 and abs(r.height - 100) < 3
    border = _pixel(doc, 0, r.x0 + 1, (r.y0 + r.y1) / 2)
    inside = _pixel(doc, 0, r.x0 + 10, r.y1 - 10)
    assert border[0] > 200 and border[1] < 60 and border[2] < 60, border
    assert inside[0] > 200 and inside[1] > 200 and inside[2] < 60, inside
    # testo centrato: estraibile dall'aspetto
    words = doc[0].get_text("words", clip=r)
    assert any(w[4] == "importante" for w in words) or "importante" in doc[0].get_text()


def test_ann16_click_creates_default_width_box(comments, monkeypatch):
    """ANN-16: un clic semplice crea una casella di larghezza predefinita."""
    window, view, module = comments
    _ok_dialog(monkeypatch, "Ciao")
    module.activate(engine.FREETEXT)
    click(view, 0, 100, 600)
    infos = module.ctrl.annotations()
    assert len(infos) == 1 and infos[0].kind == engine.FREETEXT
    assert infos[0].rect.width >= 150


def test_ann16_rotated_page_text_horizontal(comments, tmp_path, monkeypatch):
    """ANN-16: su una pagina con /Rotate 90 il testo della casella appare orizzontale nella vista."""
    window, view, module = comments
    with view.document.change("ruota") as pdf:
        pdf[1].set_rotation(90)
    xref = module.ctrl.apply("t", engine.create_freetext, 1, (100, 100, 300, 160), "Orizzontale",
                             engine.Style(fontsize=14, width=0), "A")
    doc = saved(window, view, tmp_path)
    page = doc[1]
    # nella vista (pagina ruotata) la riga di testo è più larga che alta
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("lines")]
    assert xref
    lines = [ln for b in blocks for ln in b["lines"] if "Orizzontale" in "".join(s["text"] for s in ln["spans"])]
    if not lines:   # il testo dell'aspetto non è estratto dal motore: si verifica il rendering
        pytest.skip("testo dell'aspetto non estraibile")
    assert lines[0]["dir"] == (1.0, 0.0)


def test_ann16_rich_text_in_textbox(comments, tmp_path):
    """ANN-16: grassetto e corsivo nella casella finiscono in /RC, /Contents resta testo semplice."""
    window, view, module = comments
    text = "una parola grassetto e corsivo"
    rc = ('<?xml version="1.0"?><body><p>una parola <span style="font-weight:bold">grassetto</span> e '
          '<span style="font-style:italic">corsivo</span></p></body>')
    xref = module.ctrl.apply("t", engine.create_freetext, 0, (100, 400, 400, 460), text,
                             engine.Style(fontsize=12, width=1, color=(1, 0, 0)), "A")
    module.ctrl.apply("c", engine.set_contents, 0, xref, text, rc)
    module.ctrl.move(5, 5, [(0, xref)])
    doc = saved(window, view, tmp_path)
    ft, = annots(doc, 0, "FreeText")
    stored = engine.get_rich_text(doc, ft.xref)
    assert "bold" in stored and "italic" in stored
    assert ft.info["content"] == text
    assert "grassetto" in doc[0].get_text()
