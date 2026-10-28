"""Test della lettura ad alta voce (VIS-35) con il motore vocale simulato."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QMenu

from foliant.api import selection_from_words, theme
from foliant.modules.viewer import speech as S
from foliant.modules.viewer import speech_engine as E

PARAGRAPH = ("Das Uhrwerk wurde im Jahr 1952 gebaut. Es besteht aus 21 Steinen "
             "und einer Unruh aus Glucydur. Die Gangreserve betraegt zwei Tage!")


def _controller(window, engine=None):
    ctl = window.ctx.viewer_parts.get("speech") if hasattr(window.ctx, "viewer_parts") else None
    if ctl is None:
        ctl = S.install(window.ctx)
    ctl.set_engine(engine if engine is not None else E.FakeEngine())
    return ctl


@pytest.fixture
def speech(viewer_window):
    ctl = _controller(viewer_window)
    yield viewer_window, ctl, ctl.engine
    ctl.stop()


def _paragraph_pdf(path, lang_catalog=None, tagged_lang=None, encrypt_perms=None, pages=1):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 80, 400, 300), PARAGRAPH, fontsize=12)
    if lang_catalog:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", f"({lang_catalog})")
    if tagged_lang:
        root = doc.get_new_xref()
        elem = doc.get_new_xref()
        doc.update_object(root, f"<< /Type /StructTreeRoot /K [{elem} 0 R] >>")
        doc.update_object(elem, f"<< /Type /StructElem /S /P /P {root} 0 R /Lang ({tagged_lang}) "
                                f"/Pg {doc.page_xref(0)} 0 R /K 0 >>")
        doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
        doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<< /Marked true >>")
    kw = {}
    if encrypt_perms is not None:
        kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="proprietario",
                  permissions=encrypt_perms)
    doc.save(str(path), **kw)
    doc.close()
    return str(path)


def _finish_all(engine, limit=200):
    for _i in range(limit):
        if not engine.speaking:
            break
        engine.finish()


# ------------------------------------------------------------------ logica pura
def test_build_sentences_splits_on_punctuation_and_blocks():
    words = [(0, 0, 10, 10, "Uno", 0, 0, 0), (12, 0, 20, 10, "due.", 0, 0, 1),
             (0, 12, 10, 22, "Tre", 0, 1, 0), (0, 40, 10, 50, "Titolo", 1, 0, 0),
             (0, 60, 10, 70, "ripe-", 2, 0, 0), (0, 72, 10, 82, "tuto", 2, 1, 0)]
    s = E.build_sentences(3, words)
    assert [x.text for x in s] == ["Uno due.", "Tre", "Titolo", "ripetuto"]
    assert s[0].page == 3 and len(s[0].rects) == 1 and len(s[3].rects) == 2


def test_rate_mapping_and_permissions():
    assert E.spd_rate(1.0) == 0 and E.spd_rate(0.5) == -50 and E.spd_rate(3.0) == 100
    assert E.espeak_wpm(2.0) == 350
    assert E.clamp_rate(10) == 3.0 and E.clamp_rate(0.1) == 0.5
    assert E.reading_allowed(-1)
    assert E.reading_allowed(pymupdf.PDF_PERM_ACCESSIBILITY)
    assert not E.reading_allowed(pymupdf.PDF_PERM_PRINT)
    assert E.primary_language("de-DE") == "de"


def test_process_engine_arguments():
    spd = E.SpdSayEngine()
    args = spd._args("Hallo", "de-DE", 2.0, 0.0, None)
    assert args[0] == "-w" and ["-l", "de"] == args[args.index("-l"):args.index("-l") + 2]
    assert args[-1] == "Hallo" and "50" in args
    esp = E.EspeakEngine("espeak-ng")
    assert esp._args("Hallo", "de-DE", 1.0, 0.0, None) == ["-s", "175", "-p", "50", "-v", "de", "Hallo"]


# ------------------------------------------------------------------ criteri di accettazione
def test_selection_text_sent_exactly_in_reading_order(speech, tmp_path):
    window, ctl, engine = speech
    view = window.open_path(_paragraph_pdf(tmp_path / "par.pdf"))
    words = view.words(0)
    sel = selection_from_words(0, words)
    view.set_text_selection(sel)
    assert ctl.read_selection()
    _finish_all(engine)
    assert " ".join(engine.texts) == " ".join(sel.text.split()) == PARAGRAPH
    assert engine.texts[0] == "Das Uhrwerk wurde im Jahr 1952 gebaut."
    assert len(engine.texts) == 3
    assert not ctl.playing and ctl.toolbar.isHidden()


def test_partial_selection_reads_only_selected_words(speech, tmp_path):
    window, ctl, engine = speech
    view = window.open_path(_paragraph_pdf(tmp_path / "par.pdf"))
    words = view.words(0)[3:9]
    view.set_text_selection(selection_from_words(0, words))
    ctl.read_selection()
    _finish_all(engine)
    assert " ".join(engine.texts) == " ".join(w[4] for w in words)


@pytest.mark.parametrize("where", ["structure", "catalog"])
def test_language_from_lang(speech, tmp_path, where):
    window, ctl, engine = speech
    kw = {"tagged_lang": "de-DE", "lang_catalog": "it-IT"} if where == "structure" else {"lang_catalog": "de-DE"}
    window.open_path(_paragraph_pdf(tmp_path / "de.pdf", **kw))
    assert ctl.read_page()
    assert engine.calls[0]["lang"] == "de-DE"
    assert E.primary_language(engine.calls[0]["lang"]) == "de"


def test_language_falls_back_to_interface(speech, tmp_path):
    window, ctl, engine = speech
    window.open_path(_paragraph_pdf(tmp_path / "nolang.pdf"))
    ctl.read_page()
    assert engine.calls[0]["lang"] == (S.language() or "it")


def test_copy_forbidden_but_accessibility_allowed_reads(speech, tmp_path):
    window, ctl, engine = speech
    path = _paragraph_pdf(tmp_path / "acc.pdf",
                          encrypt_perms=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY)
    view = window.open_path(path, "utente")
    assert view is not None
    assert ctl.read_page()
    assert engine.texts and engine.texts[0].startswith("Das Uhrwerk")
    # anche dopo una modifica (documento interno non più cifrato) i permessi valgono ancora
    with view.document.change("prova") as pdf:
        pdf[0].insert_text((72, 700), "Nachtrag.", fontsize=10)
    ctl.stop()
    assert ctl.read_page()


def test_no_copy_and_no_accessibility_refused(speech, tmp_path):
    window, ctl, engine = speech
    path = _paragraph_pdf(tmp_path / "noacc.pdf", encrypt_perms=pymupdf.PDF_PERM_PRINT)
    window.open_path(path, "utente")
    assert not ctl.read_page()
    assert engine.calls == [] and "permessi" in ctl.last_message


def test_sentence_out_of_view_scrolls_and_is_highlighted(speech, tmp_path, qtbot):
    window, ctl, engine = speech
    path = tmp_path / "lontano.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Prima frase in alto.", fontsize=12)
    page.insert_text((72, 800), "Seconda frase in fondo.", fontsize=12)
    doc.save(str(path))
    doc.close()
    view = window.open_path(str(path))
    view.set_zoom(3.0)
    view.go_to_page(0)
    qtbot.wait(20)
    ctl.read_page()
    top = view.verticalScrollBar().value()
    assert ctl.current_sentence().text == "Prima frase in alto."
    far = view.page_to_view(0, E.build_sentences(0, view.words(0))[1].bbox)
    assert not view.viewport().rect().intersects(far.toRect())     # frase successiva fuori vista
    engine.finish()
    s = ctl.current_sentence()
    assert s.text == "Seconda frase in fondo."
    assert view.verticalScrollBar().value() > top
    vr = view.page_to_view(0, s.bbox)
    assert view.viewport().rect().contains(vr.toRect())
    # evidenziazione: barra d'accento a sinistra della frase
    view.flush_rendering()
    img = view.viewport().grab().toImage()
    bar = view.page_to_view(0, pymupdf.Rect(s.rects[0].x0 - 5, s.rects[0].y0, s.rects[0].x0 - 3, s.rects[0].y1))
    c = img.pixelColor(int(bar.center().x()), int(bar.center().y()))
    accent = theme.color("accent")
    assert abs(c.red() - accent.red()) < 30 and abs(c.green() - accent.green()) < 30
    # e dentro la frase il fondo bianco è tinto
    inside = view.page_to_view(0, s.rects[0])
    tint = img.pixelColor(int(inside.left()) + 1, int(inside.top()) + 1)
    assert (tint.red(), tint.green(), tint.blue()) != (255, 255, 255)


def test_no_engine_disables_with_message(viewer_window, monkeypatch, tmp_path):
    monkeypatch.setattr(E.shutil, "which", lambda _p: None)
    engine = E.detect_engine()
    assert isinstance(engine, E.NullEngine) and not engine.available()
    window = viewer_window
    ctl = _controller(window, engine)
    view = window.open_path(_paragraph_pdf(tmp_path / "p.pdf"))
    ctl.update_actions()
    msg = S._(S.NO_ENGINE_TEXT)
    assert not ctl.act_menu.isEnabled() and ctl.act_menu.toolTip() == msg
    for act in (ctl.act_page, ctl.act_to_end, ctl.act_selection):
        assert not act.isEnabled() and act.toolTip() == msg
    # menu contestuale
    menu = QMenu()
    from foliant.api import PageEvent
    pe = PageEvent(0, pymupdf.Point(100, 100), QPointF(10, 10), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    ctl._menu_provider(view, pe, menu)
    acts = [a for a in menu.actions() if not a.isSeparator()]
    assert acts and not acts[0].isEnabled() and acts[0].toolTip() == msg
    assert not ctl.read_page() and ctl.last_message == msg


# ------------------------------------------------------------------ comportamento
def test_read_to_end_continues_and_follows_manual_page_change(speech, long_pdf):
    window, ctl, engine = speech
    view = window.open_path(long_pdf)
    assert ctl.read_to_end()
    assert engine.texts[0] == "Pagina 1"
    _finish_all(engine, limit=3)       # 2 frasi a pagina → si passa alla pagina 2
    assert ctl.page == 1 and engine.texts[2] == "Pagina 2"
    assert view.current_page == 1
    view.go_to_page(9)                 # cambio pagina manuale
    assert ctl.page == 9 and engine.texts[-1] == "Pagina 10"
    assert not ctl.toolbar.isHidden()
    ctl.stop()
    assert ctl.toolbar.isHidden() and ctl._overlay not in view.overlays


def test_read_page_stops_at_page_end_and_controls(speech, long_pdf):
    window, ctl, engine = speech
    window.open_path(long_pdf)
    ctl.read_page()
    ctl.next_sentence()
    assert engine.texts[-1] == "Contenuto numero 1 del documento"
    ctl.previous_sentence()
    assert engine.texts[-1] == "Pagina 1"
    ctl.toggle_pause()
    assert ctl.paused and not engine.speaking and ctl.act_play.text() == S._("Riproduci")
    n = len(engine.calls)
    ctl.toggle_pause()
    assert len(engine.calls) == n + 1 and engine.texts[-1] == "Pagina 1"
    ctl.set_rate(2.0)
    assert engine.calls[-1]["rate"] == 2.0 and ctl.speed_box.currentData() == 2.0
    ctl.set_voice("de")
    assert engine.calls[-1]["voice"] == "de"
    _finish_all(engine)
    assert not ctl.playing and engine.texts[-1] == "Contenuto numero 1 del documento"


def test_page_without_text_suggests_ocr(speech, tmp_path):
    window, ctl, engine = speech
    path = tmp_path / "vuoto.pdf"
    doc = pymupdf.open()
    doc.new_page()
    p = doc.new_page()
    p.insert_text((72, 100), "Testo.", fontsize=12)
    doc.save(str(path))
    doc.close()
    window.open_path(str(path))
    assert not ctl.read_page(0)
    assert "OCR" in ctl.last_message and engine.calls == []
    assert window.statusBar().currentMessage() == ctl.last_message
    assert ctl.read_to_end(0)          # da qui alla fine salta la pagina vuota
    assert engine.texts == ["Testo."]


def test_context_menu_reads_selection(speech, tmp_path):
    window, ctl, engine = speech
    view = window.open_path(_paragraph_pdf(tmp_path / "par.pdf"))
    view.set_text_selection(selection_from_words(0, view.words(0)[:5]))
    menu = QMenu()
    from foliant.api import PageEvent
    pe = PageEvent(0, pymupdf.Point(80, 90), QPointF(10, 10), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    ctl._menu_provider(view, pe, menu)
    act = [a for a in menu.actions() if a.text() == S._("Leggi la selezione ad alta voce")][0]
    assert act.isEnabled()
    act.trigger()
    assert engine.texts == [" ".join(w[4] for w in view.words(0)[:5])]
