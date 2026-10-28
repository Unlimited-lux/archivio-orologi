"""Test automatici delle funzioni P1 del motore annotazioni (specifica, sezione 5).

Solo PyMuPDF, nessuna dipendenza da Qt. "Salva e riapri" = `reopen(doc)`.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import pymupdf
import pytest

from foliant.modules.annotations import engine as E


# ------------------------------------------------------------------ utilità
def make_doc(pages: int = 2) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Pagina {i + 1}", fontsize=24)
        page.insert_text((72, 140), "Il termine di consegna è fissato al 30 aprile.", fontsize=12)
    return doc


def reopen(doc: pymupdf.Document) -> pymupdf.Document:
    return pymupdf.open(stream=doc.tobytes(), filetype="pdf")


_KEEP: list = []


def load(d: pymupdf.Document, page_no: int, xref: int) -> pymupdf.Annot:
    """Carica un'annotazione tenendo viva la pagina (PyMuPDF tiene il genitore con un riferimento debole)."""
    page = d[page_no]
    _KEEP.append(page)
    return page.load_annot(xref)


def comment_annots(page: pymupdf.Page) -> list[pymupdf.Annot]:
    return [a for a in page.annots() if a.type[0] not in E.NON_COMMENT_TYPES]


def words_quads(page: pymupdf.Page, phrase: str) -> list[pymupdf.Quad]:
    return page.search_for(phrase, quads=True)


def pdf_point(page: pymupdf.Page, p) -> tuple[float, float]:
    return E.to_pdf_point(page, p[0], p[1])


def floats(v: str) -> list[float]:
    return [float(x) for x in v.strip("[]").split()]


def pix_diff(a: pymupdf.Pixmap, b: pymupdf.Pixmap) -> float:
    """Frazione di campioni che differiscono di più di 48 livelli (tolleranza di antialiasing)."""
    assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
    sa, sb = a.samples, b.samples
    bad = sum(1 for x, y in zip(sa, sb) if abs(x - y) > 48)
    return bad / len(sa)


def png_bytes(w: int = 40, h: int = 20) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), 1)
    pix.clear_with(0)  # trasparente
    pix.set_rect(pymupdf.IRect(5, 5, w - 5, h - 5), (200, 30, 30, 255))
    return pix.tobytes("png")


STYLE = E.Style()


@pytest.fixture
def doc():
    d = make_doc()
    yield d
    d.close()


# ================================================================== ANN-37 risposte
def test_reply_structure_after_save(doc):
    """ANN-37: risposta = /Text con /IRT al genitore, /Contents e /T dell'autore."""
    q = words_quads(doc[0], "termine di consegna")
    hl = E.create_markup(doc, 0, E.HIGHLIGHT, q, STYLE, author="Anna")
    rep = E.add_reply(doc, 0, hl, "D'accordo", author="Paolo")
    d2 = reopen(doc)
    assert d2.xref_get_key(rep, "Subtype") == ("name", "/Text")
    assert d2.xref_get_key(rep, "IRT") == ("xref", f"{hl} 0 R")
    a = load(d2, 0, rep)
    assert a.info["content"] == "D'accordo"
    assert a.info["title"] == "Paolo"
    assert rep in [x.xref for x in d2[0].annots()]


def test_replies_nested_and_sorted_by_creation_date(doc):
    """ANN-37: le risposte sono annidate sotto il commento principale in ordine di /CreationDate."""
    note = E.create_note(doc, 0, (200, 200), "Radice", STYLE, author="Anna")
    r_late = E.add_reply(doc, 0, note, "seconda", author="B", date="D:20260310120000+01'00'")
    r_early = E.add_reply(doc, 0, note, "prima", author="C", date="D:20260101090000+01'00'")
    r_mid = E.add_reply(doc, 0, note, "intermedia", author="D", date="D:20260201090000+01'00'")
    items = E.list_annotations(reopen(doc))
    assert [a.xref for a in items] == [note]
    assert [r.xref for r in items[0].replies] == [r_early, r_mid, r_late]
    assert [r.contents for r in items[0].replies] == ["prima", "intermedia", "seconda"]


def test_delete_root_removes_replies(doc):
    """ANN-37: eliminando il commento principale spariscono anche le 3 risposte."""
    note = E.create_note(doc, 0, (200, 200), "Radice", STYLE, author="Anna")
    other = E.create_note(doc, 0, (400, 400), "Altro", STYLE, author="Anna")
    reps = [E.add_reply(doc, 0, note, f"r{i}", author="X") for i in range(3)]
    nested = E.add_reply(doc, 0, reps[0], "risposta alla risposta", author="Y")
    E.delete_annot(doc, 0, note)
    d2 = reopen(doc)
    present = {a.xref for a in d2[0].annots()}
    for x in [note, *reps, nested]:
        assert x not in present
    assert other in present
    assert [a.xref for a in E.list_annotations(d2)] == [other]


def test_cyclic_irt_chain_lists_all(doc):
    """ANN-37: una catena ciclica di /IRT non blocca l'elenco e tutte le annotazioni compaiono."""
    a = E.create_note(doc, 0, (100, 300), "A", STYLE, author="X")
    b = E.create_note(doc, 0, (200, 300), "B", STYLE, author="X")
    page = doc[0]
    page.load_annot(a).set_irt_xref(b)
    page.load_annot(b).set_irt_xref(a)
    d2 = reopen(doc)
    assert d2.xref_get_key(a, "IRT")[1] == f"{b} 0 R"
    items = E.list_annotations(d2)
    listed = {x.xref for x in items} | {r.xref for x in items for r in x.replies}
    assert {a, b} <= listed


# ================================================================== ANN-38 stato
def test_status_accepted_structure(doc):
    """ANN-38: stato "Accepted" di Maria = /Text con /IRT, /StateModel /Review, /State /Accepted, /T Maria."""
    note = E.create_note(doc, 0, (200, 200), "Commento", STYLE, author="Anna")
    st = E.set_status(doc, 0, note, E.STATE_ACCEPTED, "Maria")
    d2 = reopen(doc)
    assert d2.xref_get_key(st, "Subtype") == ("name", "/Text")
    assert d2.xref_get_key(st, "IRT") == ("xref", f"{note} 0 R")
    assert d2.xref_get_key(st, "StateModel") == ("name", "/Review")
    assert d2.xref_get_key(st, "State") == ("name", "/Accepted")
    assert load(d2, 0, st).info["title"] == "Maria"
    items = E.list_annotations(d2)
    assert len(items) == 1 and items[0].xref == note
    assert items[0].status == "Accepted"
    # lo stato non compare come risposta visibile
    assert all(not r.is_state for r in items[0].replies)


def test_status_latest_wins_and_history(doc):
    """ANN-38: "Accepted" poi "Completed" → stato visibile Completed, cronologia con entrambi in ordine."""
    note = E.create_note(doc, 0, (200, 200), "Commento", STYLE, author="Anna")
    s1 = E.set_status(doc, 0, note, E.STATE_ACCEPTED, "Maria", date="D:20260301100000+01'00'")
    s2 = E.set_status(doc, 0, note, E.STATE_COMPLETED, "Luca", date="D:20260302100000+01'00'")
    item = E.list_annotations(reopen(doc))[0]
    assert item.status == "Completed"
    assert [h.xref for h in item.history] == [s1, s2]
    assert [h.state for h in item.history] == ["Accepted", "Completed"]


def test_status_from_other_program_order_by_date(doc):
    """ANN-38: stati creati da altri programmi (xref in ordine inverso) → vale il più recente per data."""
    note = E.create_note(doc, 0, (200, 200), "Commento", STYLE, author="Anna")
    E.set_status(doc, 0, note, E.STATE_REJECTED, "Maria", date="D:20260305100000+01'00'")
    E.set_status(doc, 0, note, E.STATE_ACCEPTED, "Maria", date="D:20260301100000+01'00'")
    item = E.list_annotations(reopen(doc))[0]
    assert item.status == "Rejected"


def test_marked_on_then_off(doc):
    """ANN-38: spunta attivata e poi disattivata → /StateModel /Marked, ultimo /Unmarked, .marked False."""
    note = E.create_note(doc, 0, (200, 200), "Commento", STYLE, author="Anna")
    m1 = E.set_marked(doc, 0, note, True, "Maria")
    d_mid = E.list_annotations(reopen(doc))[0]
    assert d_mid.marked is True
    m2 = E.set_marked(doc, 0, note, False, "Maria")
    d2 = reopen(doc)
    for x in (m1, m2):
        assert d2.xref_get_key(x, "StateModel") == ("name", "/Marked")
    assert d2.xref_get_key(m1, "State") == ("name", "/Marked")
    assert d2.xref_get_key(m2, "State") == ("name", "/Unmarked")
    item = E.list_annotations(d2)[0]
    assert item.marked is False
    assert item.status == E.STATE_NONE  # la spunta non altera lo stato di revisione


# ================================================================== ANN-40 appiattimento
def _five_annots(doc, page_no=0):
    page = doc[page_no]
    E.create_markup(doc, page_no, E.HIGHLIGHT, words_quads(page, "termine di consegna"), STYLE, author="A")
    E.create_shape(doc, page_no, E.RECT, [(100, 300), (200, 380)], STYLE, author="A")
    E.create_shape(doc, page_no, E.LINE, [(250, 300), (400, 360)], STYLE, author="A")
    E.create_freetext(doc, page_no, (100, 450, 300, 490), "Totale 100", STYLE, author="A")
    E.create_stamp(doc, page_no, (400, 600), E.StampSpec("Approvato", name="Approved"), STYLE, author="A")


def test_flatten_all_removes_annots_keeps_text_and_rendering(doc):
    """ANN-40: dopo l'appiattimento nessuna annotazione, testo estraibile e rendering invariato."""
    _five_annots(doc)
    note = E.create_note(doc, 0, (500, 200), "nota", STYLE, author="A")
    E.add_reply(doc, 0, note, "risposta", author="B")
    before = doc[0].get_pixmap(dpi=50, annots=True)
    n = E.flatten(doc)
    assert n >= 6
    d2 = reopen(doc)
    assert comment_annots(d2[0]) == []
    assert "Totale 100" in d2[0].get_text()
    after = d2[0].get_pixmap(dpi=50, annots=True)
    assert pix_diff(before, after) < 0.01


def test_flatten_selected_page_only(doc):
    """ANN-40: con `pages` si appiattisce solo quella pagina; le altre annotazioni restano modificabili."""
    E.create_freetext(doc, 0, (100, 450, 300, 490), "Totale 100", STYLE, author="A")
    E.create_shape(doc, 0, E.RECT, [(100, 300), (200, 380)], STYLE, author="A")
    r1 = E.create_shape(doc, 1, E.RECT, [(100, 300), (200, 380)], STYLE, author="A")
    n1 = E.create_note(doc, 1, (300, 300), "resta", STYLE, author="A")
    assert E.flatten(doc, [0]) == 2
    d2 = reopen(doc)
    assert comment_annots(d2[0]) == []
    assert "Totale 100" in d2[0].get_text()
    left = {a.xref for a in comment_annots(d2[1])}
    assert {r1, n1} <= left
    # restano modificabili
    x0 = load(d2, 1, r1).rect.x0
    E.move_annot(d2, 1, r1, 10, 0)
    assert load(d2, 1, r1).rect.x0 > x0 + 5


def test_flatten_skips_non_printing(doc):
    """ANN-40: un'annotazione con Print disattivato non viene appiattita e resta come annotazione."""
    x = E.create_shape(doc, 0, E.RECT, [(100, 300), (200, 380)], STYLE.copy(width=4), author="A")
    E.set_flag(doc, 0, x, E.FLAG_PRINT, False)
    clean = make_doc()[0].get_pixmap(dpi=50, annots=False)
    E.flatten(doc)
    d2 = reopen(doc)
    assert x in {a.xref for a in d2[0].annots()}
    assert pix_diff(clean, d2[0].get_pixmap(dpi=50, annots=False)) < 0.001


# ================================================================== ANN-41/42 XFDF / FDF
def _review_doc(doc):
    """1 evidenziazione, 1 nota con 2 risposte, 1 timbro personalizzato, 1 rettangolo a pagina 2."""
    page = doc[0]
    hl = E.create_markup(doc, 0, E.HIGHLIGHT, words_quads(page, "termine di consegna"),
                         STYLE.copy(color=(1, 1, 0)), author="Anna", contents="Verificare")
    note = E.create_note(doc, 0, (300, 300), "A & B < C 😀", STYLE.copy(color=(0, 0.5, 1)), author="Bruno")
    r1 = E.add_reply(doc, 0, note, "Prima risposta", author="Carla", date="D:20260101090000+01'00'")
    r2 = E.add_reply(doc, 0, note, "Seconda risposta", author="Dario", date="D:20260102090000+01'00'")
    st = E.create_stamp(doc, 0, (300, 600), E.StampSpec("Collaudato", (0.1, 0.5, 0.2)), STYLE, author="Elena")
    rc = E.create_shape(doc, 1, E.RECT, [(100, 300), (220, 380)], STYLE.copy(color=(0.8, 0.1, 0.1)),
                        author="Fabio", contents="Riquadro")
    return dict(hl=hl, note=note, r1=r1, r2=r2, stamp=st, rect=rc)


def test_export_xfdf_well_formed_with_replies(doc):
    """ANN-41: XFDF ben formato, un elemento per annotazione, risposte con inreplyto."""
    ids = _review_doc(doc)
    xml = E.export_xfdf(doc, "a.pdf")
    root = ET.fromstring(xml.encode("utf-8"))
    ns = {"x": E.XFDF_NS}
    assert root.tag == f"{{{E.XFDF_NS}}}xfdf"
    annots = root.find("x:annots", ns)
    elems = list(annots)
    assert len(elems) == 6
    tags = sorted(e.tag.split("}")[1] for e in elems)
    assert tags == ["highlight", "square", "stamp", "text", "text", "text"]
    note_name = load(doc, 0, ids["note"]).info["id"]
    replies = [e for e in elems if e.get("inreplyto")]
    assert len(replies) == 2
    assert all(e.get("inreplyto") == note_name for e in replies)
    pages = sorted(int(e.get("page")) for e in elems)
    assert pages == [0, 0, 0, 0, 0, 1]


def test_export_xfdf_includes_stamp_appearance(doc):
    """ANN-41: l'XFDF include l'aspetto del timbro personalizzato."""
    _review_doc(doc)
    root = ET.fromstring(E.export_xfdf(doc).encode("utf-8"))
    stamp = root.find(f".//{{{E.XFDF_NS}}}stamp")
    assert stamp is not None
    assert stamp.find(f"{{{E.XFDF_NS}}}appearance") is not None


def test_xfdf_special_chars_round_trip(doc):
    """ANN-41: il testo "A & B < C 😀" esportato e reimportato è identico."""
    text = "A & B < C 😀"
    E.create_note(doc, 0, (300, 300), text, STYLE, author="Zoë & Co <x>")
    xml = E.export_xfdf(doc)
    target = make_doc()
    res = E.import_xfdf(target, xml)
    assert res.imported == 1
    items = E.list_annotations(reopen(target))
    assert items[0].contents == text
    assert items[0].author == "Zoë & Co <x>"


def test_xfdf_import_into_clean_copy(doc):
    """ANN-42: importando in una copia priva di commenti si ricreano tipi, pagine, /Rect, colori,
    testi, autori, date e collegamenti delle risposte."""
    _review_doc(doc)
    xml = E.export_xfdf(doc)
    copy = make_doc()
    res = E.import_xfdf(copy, xml)
    assert res.imported == 6 and res.skipped == 0
    src = {a.name: a for p in range(2) for a in E.page_annotations(doc, p)}
    dst_doc = reopen(copy)
    dst = {a.name: a for p in range(2) for a in E.page_annotations(dst_doc, p)}
    assert set(src) == set(dst)
    by_xref = {a.xref: a for a in dst.values()}
    for nm, s in src.items():
        d = dst[nm]
        assert (d.type, d.page) == (s.type, s.page), nm
        if s.type not in ("Square", "Circle"):  # vedi test_xfdf_import_square_rect
            for u, v in zip(d.rect, s.rect):
                assert u == pytest.approx(v, abs=0.5), (s.type, d.rect, s.rect)
        assert E.color_to_hex(d.color) == E.color_to_hex(s.color), s.type
        assert d.contents == s.contents
        assert d.author == s.author
        assert d.created == s.created
        if s.irt:
            assert by_xref[d.irt].name == next(a.name for a in src.values() if a.xref == s.irt)


def test_xfdf_import_square_rect(doc):
    """ANN-42: /Rect di rettangoli ed ellissi reimportati da XFDF invariato (tolleranza 0,5 pt)."""
    sq = E.create_shape(doc, 0, E.RECT, [(100, 300), (220, 380)], STYLE)
    ci = E.create_shape(doc, 0, E.ELLIPSE, [(300, 300), (420, 380)], STYLE)
    src = {a.name: a.rect for a in E.page_annotations(doc, 0) if a.xref in (sq, ci)}
    copy = make_doc()
    E.import_xfdf(copy, E.export_xfdf(doc))
    dst = {a.name: a.rect for a in E.page_annotations(reopen(copy), 0)}
    for nm, r in src.items():
        for u, v in zip(dst[nm], r):
            assert u == pytest.approx(v, abs=0.5), (dst[nm], r)


def test_xfdf_import_twice_no_duplicates(doc):
    """ANN-42: lo stesso XFDF importato due volte non duplica i commenti (regola "salta")."""
    _review_doc(doc)
    xml = E.export_xfdf(doc)
    copy = make_doc()
    E.import_xfdf(copy, xml)
    count = sum(len(comment_annots(p)) for p in copy)
    res = E.import_xfdf(copy, xml)
    assert res.imported == 0 and res.skipped == 6
    assert sum(len(comment_annots(p)) for p in copy) == count


def test_xfdf_import_page_out_of_range_skipped():
    """ANN-42: un commento su una pagina inesistente è saltato e conteggiato; gli altri sono importati."""
    target = make_doc(2)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><xfdf xmlns="{E.XFDF_NS}"><annots>'
           '<text page="0" name="ok-1" rect="100,700,120,720" title="R"><contents>valido</contents></text>'
           '<text page="11" name="ko-1" rect="100,700,120,720" title="R"><contents>pagina 12</contents></text>'
           '</annots></xfdf>')
    res = E.import_xfdf(target, xml)
    assert res.imported == 1
    assert res.skipped == 1
    assert res.messages
    assert [a.contents for a in E.list_annotations(target)] == ["valido"]


@pytest.mark.parametrize("bad", ["<xfdf><annots><text page='0'", "not xml at all", "<root><annots/></root>"])
def test_xfdf_malformed_raises_and_leaves_doc(doc, bad):
    """ANN-42: un XFDF malformato solleva AnnotationError e lascia il documento invariato."""
    E.create_note(doc, 0, (300, 300), "esistente", STYLE, author="A")
    before = [(a.xref, a.contents) for a in E.list_annotations(doc)]
    with pytest.raises(E.AnnotationError):
        E.import_xfdf(doc, bad, replace=True)
    assert [(a.xref, a.contents) for a in E.list_annotations(doc)] == before


def test_export_fdf(doc):
    """ANN-41: FDF valido che inizia con %FDF, un dizionario per annotazione con il /Page corretto."""
    _review_doc(doc)
    fdf = E.export_fdf(doc, "a.pdf")
    assert fdf.startswith(b"%FDF")
    assert fdf.rstrip().endswith(b"%%EOF")
    pages = [int(x) for x in re.findall(rb"/Page (\d+)", fdf)]
    assert sorted(pages) == [0, 0, 0, 0, 0, 1]
    m = re.search(rb"/Annots\[([^\]]*)\]", fdf)
    assert m and len(re.findall(rb"\d+ 0 R", m.group(1))) == 6
    for n in re.findall(rb"(\d+) 0 R", m.group(1)):
        assert re.search(rb"\n" + n + rb" 0 obj\n", fdf)


# ================================================================== ANN-43 riepilogo
def _summary_doc(doc):
    hl = E.create_markup(doc, 0, E.HIGHLIGHT, words_quads(doc[0], "termine di consegna"), STYLE,
                         author="Anna", contents="Data da confermare")
    E.add_reply(doc, 0, hl, "Confermata", author="Bruno")
    E.create_note(doc, 0, (300, 400), "Secondo commento", STYLE, author="Carla")
    E.create_note(doc, 1, (300, 300), "Terzo commento", STYLE, author="Dario")
    E.create_shape(doc, 1, E.RECT, [(100, 500), (200, 600)], STYLE, author="Elena", contents="Quarto commento")
    return ["Data da confermare", "Secondo commento", "Terzo commento", "Quarto commento"]


def test_summary_text(doc):
    """ANN-43: riepilogo testuale con pagine, autori, testi in ordine, testo marcato e risposte."""
    texts = _summary_doc(doc)
    out = E.summary_text(doc, "doc.pdf")
    pos = [out.index(t) for t in texts]
    assert pos == sorted(pos)
    assert "Pagina 1" in out and "Pagina 2" in out
    assert "termine di consegna" in out
    assert "Confermata" in out and "Bruno" in out
    for a in ("Anna", "Carla", "Dario", "Elena"):
        assert a in out


def test_summary_csv(doc):
    """ANN-43: CSV con intestazione e una riga per commento."""
    texts = _summary_doc(doc)
    rows = list(csv.reader(io.StringIO(E.summary_csv(doc))))
    header, body = rows[0], rows[1:]
    body = [r for r in body if not r[header.index("Risposta a")]]
    assert header[0] == "Pagina" and "Testo" in header
    ti = header.index("Testo")
    assert [r[ti] for r in body] == texts
    assert [r[0] for r in body] == ["1", "1", "2", "2"]
    assert body[0][header.index("Testo marcato")] == "termine di consegna"


def test_summary_csv_reply_rows(doc):
    """ANN-43: il CSV contiene una riga per ogni commento e per ogni risposta (con riferimento al padre)."""
    _summary_doc(doc)
    rows = list(csv.reader(io.StringIO(E.summary_csv(doc))))
    assert len(rows) - 1 == 5
    assert any("Confermata" in r for r in rows[1:])


def test_summary_html(doc):
    """ANN-43: riepilogo HTML con testi, testo marcato ed escape dei caratteri speciali."""
    texts = _summary_doc(doc)
    E.create_note(doc, 1, (400, 300), "x < y & z", STYLE, author="F")
    html = E.summary_html(doc, "doc.pdf")
    assert html.startswith("<!doctype html>")
    pos = [html.index(t) for t in texts]
    assert pos == sorted(pos)
    assert "termine di consegna" in html
    assert "x &lt; y &amp; z" in html
    assert "x < y" not in html


def test_summary_pdf(doc):
    """ANN-43: riepilogo PDF valido, commenti in ordine con autore e pagina, testo marcato,
    originale non modificato."""
    texts = _summary_doc(doc)
    original = doc.tobytes(no_new_id=True)
    data = E.summary_pdf(doc, "doc.pdf")
    assert data.startswith(b"%PDF")
    out = pymupdf.open(stream=data, filetype="pdf")
    text = "".join(p.get_text() for p in out)
    pos = [text.index(t) for t in texts]
    assert pos == sorted(pos)
    assert "termine di consegna" in text
    assert "Pagina 1" in text and "Pagina 2" in text
    for a in ("Anna", "Carla", "Dario", "Elena"):
        assert a in text
    assert doc.tobytes(no_new_id=True) == original


# ================================================================== ANN-24/25/26 timbri
def test_preset_stamp(doc):
    """ANN-24: timbro "Approvato" in (300,400) → /Stamp /Name /Approved, /AP /N non vuoto,
    /Contents "Approvato", punto dentro /Rect."""
    x = E.create_stamp(doc, 0, (300, 400), E.StampSpec("Approvato", (0.08, 0.5, 0.24), "", "Approved"),
                       STYLE, author="Luca")
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Stamp")
    assert d2.xref_get_key(x, "Name") == ("name", "/Approved")
    k, v = d2.xref_get_key(x, "AP/N")
    assert k == "xref"
    ap = int(v.split()[0])
    assert len(d2.xref_stream(ap) or b"") > 0
    a = load(d2, 0, x)
    assert a.info["content"] == "Approvato"
    assert a.rect.contains(pymupdf.Point(300, 400))
    # anche in coordinate PDF il punto cade in /Rect
    r = floats(d2.xref_get_key(x, "Rect")[1])
    px, py = pdf_point(d2[0], (300, 400))
    assert r[0] <= px <= r[2] and r[1] <= py <= r[3]
    assert E.list_annotations(d2)[0].kind == E.STAMP


def test_fill_template():
    """ANN-25: modello con autore, data gg/mm/aaaa e ora."""
    s = E.fill_template("Approvato da {autore} il {data} {ora}", author="Luca Bianchi",
                        when=datetime(2026, 3, 15, 10, 42))
    assert s == "Approvato da Luca Bianchi il 15/03/2026 10:42"
    assert E.fill_template("{data}", when=datetime(2026, 3, 15), date_format="%Y-%m-%d") == "2026-03-15"


def test_dynamic_stamp_frozen_value(doc):
    """ANN-25: il testo valorizzato è nell'aspetto e in /Contents e resta invariato alla riapertura."""
    sub = E.fill_template("Approvato da {autore} il {data} {ora}", author="Luca Bianchi",
                          when=datetime(2026, 3, 15, 10, 42))
    x = E.create_stamp(doc, 0, (300, 400), E.StampSpec("Approvato", (0.08, 0.5, 0.24), sub, "Approved"),
                       STYLE, author="Luca Bianchi")
    d2 = reopen(doc)
    assert sub in load(d2, 0, x).info["content"]
    ap = int(d2.xref_get_key(x, "AP/N")[1].split()[0])
    assert b"Approvato da Luca Bianchi il 15/03/2026 10:42" in d2.xref_stream(ap)
    assert E.stamp_spec_of(d2, x).subtitle == sub


def test_image_stamp(doc):
    """ANN-26: timbro da PNG → /Stamp con /IT /StampImage e aspetto con immagine."""
    x = E.create_image_stamp(doc, 0, (300, 400), png_bytes(), STYLE, author="A", name="Logo")
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Stamp")
    assert d2.xref_get_key(x, "IT") == ("name", "/StampImage")
    a = load(d2, 0, x)
    assert a.rect.contains(pymupdf.Point(300, 400))
    assert a.rect.width == pytest.approx(2 * a.rect.height, rel=0.01)
    assert d2.xref_get_key(x, "AP/N")[0] == "xref"


def test_image_stamp_rejects_bad_image(doc):
    """ANN-26: un'immagine illeggibile produce AnnotationError."""
    with pytest.raises(E.AnnotationError):
        E.create_image_stamp(doc, 0, (300, 400), b"not an image", STYLE)


# ================================================================== ANN-29 allegati
def test_attachment_round_trip(doc):
    """ANN-29: allegato salvato e riaperto → stessi byte e stesso nome file."""
    data = bytes(range(256)) * 40 + b"\x00fine"
    x = E.create_attachment(doc, 0, (100, 140), data, "verbale.docx", STYLE, author="A")
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/FileAttachment")
    name, got = E.attachment_data(d2, 0, x)
    assert name == "verbale.docx"
    assert got == data
    fs = d2.xref_get_key(x, "FS")
    assert fs[0] in ("xref", "dict")
    flags = load(d2, 0, x).flags
    for f in (pymupdf.PDF_ANNOT_IS_PRINT, pymupdf.PDF_ANNOT_IS_NO_ZOOM, pymupdf.PDF_ANNOT_IS_NO_ROTATE):
        assert flags & f


# ================================================================== ANN-30/31/32 misure
def test_scale_calibrate():
    """ANN-30: 100 pt calibrati come 5 m → 50 pt = "2,50 m"."""
    s = E.Scale.calibrate(100, 5, "m")
    assert E.measure_value(E.DISTANCE, [(0, 0), (50, 0)], s) == "2,50 m"
    with pytest.raises(E.AnnotationError):
        E.Scale.calibrate(0, 5, "m")


def test_distance_measure(doc):
    """ANN-31: 72 pt = 1 in, da (100,100) a (244,100) → /Line /IT /LineDimension, /L, /Contents "2,00 in"."""
    s = E.Scale.from_ratio(72, "pt", 1, "in")
    x = E.create_measure(doc, 0, E.DISTANCE, [(100, 100), (244, 100)], STYLE, s, author="A")
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Line")
    assert d2.xref_get_key(x, "IT") == ("name", "/LineDimension")
    assert load(d2, 0, x).info["content"] == "2,00 in"
    expected = [*pdf_point(d2[0], (100, 100)), *pdf_point(d2[0], (244, 100))]
    for u, v in zip(floats(d2.xref_get_key(x, "L")[1]), expected):
        assert u == pytest.approx(v, abs=0.5)
    assert d2.xref_get_key(x, "Measure/R")[0] == "string"
    assert E.list_annotations(d2)[0].kind == E.DISTANCE


def test_distance_recomputed_from_geometry(doc):
    """ANN-30/31: lunghezza ricavata da /L × /C della scala = valore in /Contents (0,5%)."""
    s = E.Scale.calibrate(100, 5, "m")
    x = E.create_measure(doc, 0, E.DISTANCE, [(100, 200), (230, 290)], STYLE, s, author="A")
    d2 = reopen(doc)
    sc = E.scale_of(d2, x)
    assert sc is not None and sc.unit == "m"
    assert sc.factor == pytest.approx(0.05, rel=1e-6)
    assert "100 pt = 5 m" in sc.label
    L = floats(d2.xref_get_key(x, "L")[1])
    length = ((L[2] - L[0]) ** 2 + (L[3] - L[1]) ** 2) ** 0.5
    shown = float(load(d2, 0, x).info["content"].split()[0].replace(",", "."))
    assert length * sc.factor == pytest.approx(shown, rel=0.005)


def test_area_measure(doc):
    """ANN-32: scala 100 pt = 1 m, quadrato 100×100 pt → "1,00 m²", /Polygon /IT /PolygonDimension."""
    s = E.Scale.calibrate(100, 1, "m")
    x = E.create_measure(doc, 0, E.AREA, [(100, 100), (200, 100), (200, 200), (100, 200)], STYLE, s)
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Polygon")
    assert d2.xref_get_key(x, "IT") == ("name", "/PolygonDimension")
    content = load(d2, 0, x).info["content"]
    assert content == "1,00 m²"
    # ricalcolo da /Vertices e /Measure
    v = floats(d2.xref_get_key(x, "Vertices")[1])
    pts = list(zip(v[::2], v[1::2]))
    sc = E.scale_of(d2, x)
    assert E.format_measure(sc.area(E.polygon_area(pts)), sc.unit, square=True) == content


def test_perimeter_measure(doc):
    """ANN-32: segmenti di 100 e 50 pt con 100 pt = 1 m → "1,50 m", /PolyLine /IT /PolyLineDimension."""
    s = E.Scale.calibrate(100, 1, "m")
    x = E.create_measure(doc, 0, E.PERIMETER, [(100, 100), (200, 100), (200, 150)], STYLE, s)
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/PolyLine")
    assert d2.xref_get_key(x, "IT") == ("name", "/PolyLineDimension")
    assert load(d2, 0, x).info["content"] == "1,50 m"
    E.update_measure(d2, 0, x)
    assert load(d2, 0, x).info["content"] == "1,50 m"


def test_update_measure_after_transform(doc):
    """ANN-31: raddoppiando la lunghezza con transform_annot e update_measure, il valore raddoppia."""
    s = E.Scale.from_ratio(72, "pt", 1, "in")
    x = E.create_measure(doc, 0, E.DISTANCE, [(100, 100), (244, 100)], STYLE, s)
    page = doc[0]
    r = pymupdf.Rect(page.load_annot(x).rect)
    E.transform_annot(doc, 0, x, pymupdf.Rect(r.x0, r.y0, r.x0 + 2 * r.width, r.y1))
    E.update_measure(doc, 0, x)
    d2 = reopen(doc)
    assert load(d2, 0, x).info["content"] == "4,00 in"


# ================================================================== ANN-20/21 forme
def test_polygon_vertices(doc):
    """ANN-20: poligono di 5 vertici → /Polygon con 10 valori in /Vertices (0,5 pt)."""
    pts = [(100, 100), (200, 120), (220, 220), (150, 260), (90, 200)]
    x = E.create_shape(doc, 0, E.POLYGON, pts, STYLE, author="A")
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Polygon")
    v = floats(d2.xref_get_key(x, "Vertices")[1])
    assert len(v) == 10
    exp = [c for p in pts for c in pdf_point(d2[0], p)]
    for u, w in zip(v, exp):
        assert u == pytest.approx(w, abs=0.5)
    assert E.list_annotations(d2)[0].kind == E.POLYGON


def test_polyline_vertices(doc):
    """ANN-20: polilinea di 4 vertici → /PolyLine con 8 valori."""
    x = E.create_shape(doc, 0, E.POLYLINE, [(100, 100), (200, 120), (220, 220), (150, 260)], STYLE)
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/PolyLine")
    assert len(floats(d2.xref_get_key(x, "Vertices")[1])) == 8


def test_polygon_too_few_points(doc):
    """ANN-20: poligono con 2 vertici → nessuna annotazione creata."""
    with pytest.raises(E.AnnotationError):
        E.create_shape(doc, 0, E.POLYGON, [(100, 100), (200, 100)], STYLE)
    assert comment_annots(doc[0]) == []


def test_cloud(doc):
    """ANN-21: nuvola di 4 vertici → /Polygon con /IT /PolygonCloud e /BE /S /C."""
    x = E.create_shape(doc, 0, E.CLOUD, [(100, 100), (250, 100), (250, 200), (100, 200)], STYLE)
    d2 = reopen(doc)
    assert d2.xref_get_key(x, "Subtype") == ("name", "/Polygon")
    assert d2.xref_get_key(x, "IT") == ("name", "/PolygonCloud")
    assert d2.xref_get_key(x, "BE/S") == ("name", "/C")
    assert len(floats(d2.xref_get_key(x, "Vertices")[1])) == 8
    assert E.list_annotations(d2)[0].kind == E.CLOUD


# ================================================================== ANN-09 flag
def test_locked_annotation_refuses_delete_and_transform(doc):
    """ANN-09/ANN-03: annotazione bloccata → eliminazione e spostamento rifiutati, bit 8 di /F attivo."""
    x = E.create_note(doc, 0, (200, 200), "bloccata", STYLE)
    E.set_flag(doc, 0, x, E.FLAG_LOCKED, True)
    rect = pymupdf.Rect(load(doc, 0, x).rect)
    with pytest.raises(E.AnnotationError):
        E.delete_annot(doc, 0, x)
    with pytest.raises(E.AnnotationError):
        E.transform_annot(doc, 0, x, rect + (50, 0, 50, 0))
    with pytest.raises(E.AnnotationError):
        E.move_annot(doc, 0, x, 10, 10)
    d2 = reopen(doc)
    a = load(d2, 0, x)
    assert a is not None
    assert a.rect == rect
    assert int(d2.xref_get_key(x, "F")[1]) & (1 << 7)
    assert E.list_annotations(d2)[0].locked


def test_print_flag_off(doc):
    """ANN-09: con "Stampa" disattivata il bit 3 di /F è 0; riattivandola torna 1."""
    x = E.create_stamp(doc, 0, (300, 400), E.StampSpec("Bozza", name="Draft"), STYLE)
    assert int(doc.xref_get_key(x, "F")[1]) & (1 << 2)
    E.set_flag(doc, 0, x, E.FLAG_PRINT, False)
    d2 = reopen(doc)
    assert int(d2.xref_get_key(x, "F")[1]) & (1 << 2) == 0
    E.set_flag(d2, 0, x, E.FLAG_PRINT, True)
    assert int(d2.xref_get_key(x, "F")[1]) & (1 << 2)


def test_new_annotations_have_print_flag(doc):
    """ANN-09: ogni nuova annotazione ha Print; note e allegati anche NoZoom e NoRotate."""
    page = doc[0]
    xs = [
        E.create_markup(doc, 0, E.HIGHLIGHT, words_quads(page, "termine"), STYLE),
        E.create_freetext(doc, 0, (100, 450, 300, 490), "testo", STYLE),
        E.create_shape(doc, 0, E.RECT, [(100, 300), (200, 380)], STYLE),
        E.create_shape(doc, 0, E.POLYGON, [(100, 500), (200, 500), (150, 560)], STYLE),
        E.create_ink(doc, 0, [[(300, 300), (320, 330), (350, 310)]], STYLE),
        E.create_stamp(doc, 0, (300, 600), E.StampSpec("Bozza", name="Draft"), STYLE),
        E.create_measure(doc, 0, E.DISTANCE, [(100, 700), (200, 700)], STYLE, E.Scale()),
    ]
    for x in xs:
        assert page.load_annot(x).flags & pymupdf.PDF_ANNOT_IS_PRINT, doc.xref_get_key(x, "Subtype")
    nz = pymupdf.PDF_ANNOT_IS_PRINT | pymupdf.PDF_ANNOT_IS_NO_ZOOM | pymupdf.PDF_ANNOT_IS_NO_ROTATE
    n = E.create_note(doc, 0, (500, 200), "nota", STYLE)
    f = E.create_attachment(doc, 0, (500, 300), b"x", "a.txt", STYLE)
    for x in (n, f):
        assert page.load_annot(x).flags & nz == nz


# ================================================================== ANN-03 ordine di sovrapposizione
def test_restack_front_and_back(doc):
    """ANN-03: "porta in primo piano" sposta l'annotazione in fondo all'array /Annots."""
    a = E.create_shape(doc, 0, E.RECT, [(100, 100), (200, 200)], STYLE)
    b = E.create_shape(doc, 0, E.RECT, [(150, 150), (250, 250)], STYLE)

    def order(d):
        return [int(x) for x in re.findall(r"(\d+) 0 R", d.xref_get_key(d[0].xref, "Annots")[1])]

    assert order(doc).index(a) < order(doc).index(b)
    E.restack(doc, 0, a, front=True)
    d2 = reopen(doc)
    assert order(d2)[-1] == a
    E.restack(d2, 0, a, front=False)
    assert order(d2)[0] == a
    with pytest.raises(E.AnnotationError):
        E.restack(d2, 0, 99999, front=True)


def test_move_rect_after_save(doc):
    """ANN-03: un rettangolo con /Rect [100 100 200 200] spostato di 50 pt a destra, salvato e riaperto
    → /Rect [150 100 250 200] (tolleranza 0,5 pt); più spostamenti non deformano il rettangolo."""
    x = E.create_shape(doc, 0, E.RECT, [(101, 101), (199, 199)], STYLE)
    r0 = load(doc, 0, x).rect
    assert tuple(r0) == pytest.approx((100, 100, 200, 200), abs=0.5)
    E.move_annot(doc, 0, x, 50, 0)
    r = load(reopen(doc), 0, x).rect
    for u, v in zip(r, (150, 100, 250, 200)):
        assert u == pytest.approx(v, abs=0.5)
    for _ in range(3):
        E.move_annot(doc, 0, x, 0, 0)
    assert load(doc, 0, x).rect.width == pytest.approx(r0.width, abs=0.5)


def test_move_line_after_save(doc):
    """ANN-03: spostando una linea di 50 pt a destra i suoi estremi /L si spostano di 50 pt."""
    x = E.create_shape(doc, 0, E.LINE, [(100, 100), (200, 150)], STYLE)
    before = floats(doc.xref_get_key(x, "L")[1])
    E.move_annot(doc, 0, x, 50, 0)
    after = floats(reopen(doc).xref_get_key(x, "L")[1])
    assert after[0] - before[0] == pytest.approx(50, abs=0.5)
    assert after[2] - before[2] == pytest.approx(50, abs=0.5)
    assert after[1] == pytest.approx(before[1], abs=0.5)
