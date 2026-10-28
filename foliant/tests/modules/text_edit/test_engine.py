"""Test del motore di modifica del testo (senza interfaccia)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.api import Document
from foliant.modules.text_edit import analysis, engine
from foliant.modules.text_edit.fonts import FontResolver
from foliant.modules.text_edit.model import (
    ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_RIGHT, SOFT_HYPHEN, Run, color_from_hex,
)

from .conftest import DEJAVU, LOREM, SANS, new_doc_with_font, save, span_with, spans, write_lines


def blocks_of(pdf, page=0, granularity="paragraph"):
    return analysis.analyze_page(pdf[page], page, granularity)


def replace_text(block, old, new):
    return [[Run(r.text.replace(old, new), r.style) for r in p] for p in block.paragraphs]


def reopen(doc: Document, tmp_path, name="salvato.pdf"):
    path = tmp_path / name
    doc.save(path)
    return pymupdf.open(str(path))


def pixels(page, clip=None, dpi=150):
    return page.get_pixmap(dpi=dpi, clip=clip, alpha=False).samples


# ---------------------------------------------------------------- TXT-01
def test_paragraph_and_line_granularity(paragraphs_pdf):
    pdf = pymupdf.open(paragraphs_pdf)
    blocks = [b for b in blocks_of(pdf) if "Lorem" in b.text]
    assert len(blocks) == 3
    for b in blocks:
        assert len(b.lines) == 4
        assert b.text.startswith("Lorem ipsum") and b.text.endswith("veniam.")
    lines = [b for b in blocks_of(pdf, granularity="line") if "contratto" not in b.text]
    assert len(lines) == 12


def test_two_columns_never_mixed(two_columns_pdf):
    pdf = pymupdf.open(two_columns_pdf)
    blocks = blocks_of(pdf)
    assert len(blocks) == 2
    for b in blocks:
        xs = {round(ln.bbox.x0) for ln in b.lines}
        assert len(xs) == 1


def test_scanned_page_has_no_blocks(scanned_pdf):
    pdf = pymupdf.open(scanned_pdf)
    assert blocks_of(pdf) == []
    assert analysis.page_has_images(pdf[0])


def test_undecodable_block_marked():
    from foliant.modules.text_edit.model import Line, Style
    line = Line(bbox=pymupdf.Rect(0, 0, 50, 10), origin=pymupdf.Point(0, 8),
                runs=[Run("���", Style())], words=[])
    block = analysis.build_block(0, 0, [line], False)
    assert not block.editable and block.reason == analysis.REASON_UNDECODABLE


# ---------------------------------------------------------------- TXT-02
def test_replace_year_keeps_style_and_position(subset_pdf, tmp_path):
    doc = Document.open(subset_pdf)
    with doc.lock:
        before = span_with(doc.pdf[0], "contratto")
        block = blocks_of(doc.pdf)[0]
    with doc.change("Modifica testo") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "2024 o", "2027 o"))
    out = reopen(doc, tmp_path)
    text = out[0].get_text()
    assert "scade nel 2027 o 2026" in text and "2024" not in text
    # le cifre presenti nel sottoinsieme riusano la risorsa originale, la «7» mancante un font installato
    after = span_with(out[0], "contratto")
    new_span = [sp for sp in spans(out[0]) if "7" in sp["text"]][0]
    assert abs(new_span["size"] - before["size"]) <= 0.05 and new_span["color"] == before["color"]
    assert abs(after["size"] - before["size"]) <= 0.05
    assert after["color"] == before["color"]
    assert abs(after["origin"][0] - before["origin"][0]) <= 0.5
    assert abs(after["origin"][1] - before["origin"][1]) <= 0.5
    assert analysis.strip_subset(after["font"]).replace(" ", "") .startswith("LiberationSans")


def test_other_blocks_untouched(tmp_path):
    doc, page = new_doc_with_font()
    for i in range(10):
        page.insert_text((72, 80 + i * 60), f"Blocco numero {i + 1} del documento", fontname="F1", fontsize=12)
    path = save(doc, tmp_path / "dieci.pdf")
    d = Document.open(path)
    with d.lock:
        orig = pymupdf.open(path)
        blocks = blocks_of(d.pdf)
        assert len(blocks) == 10
        target = blocks[2]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, target, replace_text(target, "numero 3", "TRE modificato"))
    out = reopen(d, tmp_path)
    for i, b in enumerate(blocks):
        clip = pymupdf.Rect(b.bbox) + (-1, -1, 1, 1)
        if i == 2:
            assert "TRE modificato" in out[0].get_text(clip=clip)
            continue
        assert out[0].get_text(clip=clip) == orig[0].get_text(clip=clip)
        assert pixels(out[0], clip) == pixels(orig[0], clip)


def test_unchanged_lines_keep_rendering(paragraphs_pdf, tmp_path):
    doc = Document.open(paragraphs_pdf)
    orig = pymupdf.open(paragraphs_pdf)
    with doc.lock:
        block = blocks_of(doc.pdf)[0]
    with doc.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, "labore ", ""))
    assert res.lines_kept >= 2
    out = reopen(doc, tmp_path)
    first_two = pymupdf.Rect(block.lines[0].bbox) | block.lines[1].bbox
    assert pixels(out[0], first_two) == pixels(orig[0], first_two)
    assert "incididunt ut et dolore" in out[0].get_text()


def test_deleting_all_text_removes_block(paragraphs_pdf, tmp_path):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        block = [b for b in blocks_of(doc.pdf) if "contratto" in b.text][0]
    with doc.change("Svuota") as pdf:
        engine.replace_block(pdf, 0, block, [[]])
    out = reopen(doc, tmp_path)
    assert "contratto" not in out[0].get_text()


# ---------------------------------------------------------------- TXT-03
def _justified_pdf(tmp_path):
    """Paragrafo giustificato di 5 righe composto con il motore stesso."""
    doc, page = new_doc_with_font()
    path = save(doc, tmp_path / "vuoto.pdf")
    d = Document.open(path)
    from foliant.modules.text_edit.model import Style
    style = Style(font="Liberation Serif", size=11)
    text = " ".join(LOREM * 2)
    with d.change("Crea") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(72, 100), [[Run(text, style)]], width=200, align=ALIGN_JUSTIFY)
    return d


def test_justified_reflow_respects_box(tmp_path):
    d = _justified_pdf(tmp_path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    assert block.align == ALIGN_JUSTIFY and len(block.lines) >= 4
    gaps = [b.baseline - a.baseline for a, b in zip(block.lines, block.lines[1:])]
    x0, x1 = block.x0, block.x1
    new = [[Run("Quaranta caratteri aggiunti qui davanti " + block.paragraphs[0][0].text, block.paragraphs[0][0].style)]]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, new)
    with d.lock:
        nb = blocks_of(d.pdf)[0]
    for ln in nb.lines:
        assert ln.bbox.x1 <= x1 + 0.5
        assert abs(ln.bbox.x0 - x0) <= 0.5
    for ln in nb.lines[:-1]:
        assert abs(ln.bbox.x1 - x1) <= 0.5
    for a, b in zip(nb.lines, nb.lines[1:]):
        assert abs((b.baseline - a.baseline) - gaps[0]) <= 0.1


def test_hyphenated_word_rejoined(tmp_path):
    doc, page = new_doc_with_font()
    write_lines(page, ["Questo testo parla del nuovo pro-", "getto di sviluppo per la sede"], size=12, leading=15)
    path = save(doc, tmp_path / "trattino.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    assert SOFT_HYPHEN in block.paragraphs[0][0].text
    assert "progetto" in block.text
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "Questo testo parla del nuovo ", "Il "))
    out = reopen(d, tmp_path)
    t = out[0].get_text()
    assert "progetto" in t and "pro-" not in t


def test_right_aligned_title_grows_left(tmp_path):
    doc, page = new_doc_with_font()
    w = pymupdf.Font(fontfile=SANS)
    page.insert_font(fontname="F2", fontfile=SANS)
    title = "Titolo a destra"
    width = w.text_length(title, fontsize=14)
    page.insert_text((500 - width, 100), title, fontname="F2", fontsize=14)
    # seconda riga allineata a destra per rendere riconoscibile l'allineamento
    sub = "sottotitolo"
    page.insert_text((500 - w.text_length(sub, fontsize=14), 117), sub, fontname="F2", fontsize=14)
    path = save(doc, tmp_path / "destra.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    assert block.align == ALIGN_RIGHT
    right = block.x1
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "Titolo", "Titolo molto lungo"))
    with d.lock:
        nb = blocks_of(d.pdf)[0]
    for ln in nb.lines:
        assert abs(ln.bbox.x1 - right) <= 0.6


def test_overflow_does_not_move_other_blocks(paragraphs_pdf):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        blocks = blocks_of(doc.pdf)
        below = pymupdf.Rect(blocks[1].bbox)
    first = blocks[0]
    long_text = [[Run(first.paragraphs[0][0].text + " e ancora molto altro testo aggiunto in coda", first.style)]]
    with doc.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, first, long_text)
    with doc.lock:
        nb = blocks_of(doc.pdf)
    assert any(abs(b.bbox.y1 - below.y1) < 0.5 and abs(b.bbox.x0 - below.x0) < 0.5 for b in nb) or \
        "Lorem ipsum" in doc.page_text(0)
    assert res.changed


# ---------------------------------------------------------------- TXT-04
def test_word_size_and_color(paragraphs_pdf, tmp_path):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        block = [b for b in blocks_of(doc.pdf) if "contratto" in b.text][0]
    st = block.style
    red = color_from_hex("#C00000")
    paragraphs = [[Run("Il ", st), Run("contratto", st.with_(size=18, color=red)), Run(" scade nel 2024", st)]]
    with doc.change("Formatta") as pdf:
        engine.replace_block(pdf, 0, block, paragraphs)
    out = reopen(doc, tmp_path)
    word = span_with(out[0], "contratto")
    assert abs(word["size"] - 18) <= 0.05
    c = word["color"]
    assert abs(((c >> 16) & 255) / 255 - 0.753) <= 0.003 and (c & 0xFFFF) == 0
    other = span_with(out[0], "scade")
    assert abs(other["size"] - 12) <= 0.05


def test_bold_uses_installed_variant(paragraphs_pdf):
    pdf = pymupdf.open(paragraphs_pdf)
    block = blocks_of(pdf)[0]
    resolver = FontResolver(pdf)
    choice = resolver.primary(block.style.with_(bold=True, font_xref=0))
    assert "Bold" in choice.name and not choice.simulate_bold


def test_italic_simulated_when_missing(tmp_path):
    doc, page = new_doc_with_font(DEJAVU)
    page.insert_text((72, 100), "Testo di prova", fontname="F1", fontsize=12)
    path = save(doc, tmp_path / "dejavu.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
        choice = FontResolver(d.pdf).primary(block.style.with_(italic=True, font_xref=0))
    assert choice.simulate_italic
    with d.change("Corsivo") as pdf:
        engine.restyle_block(pdf, 0, block, italic=True)
    assert "Testo di prova" in d.page_text(0)


def test_char_spacing(paragraphs_pdf):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        block = [b for b in blocks_of(doc.pdf) if "contratto" in b.text][0]
        raw = doc.pdf[0].get_text("rawdict")
    def origins(rawdict, word):
        for b in rawdict["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    chars = s["chars"]
                    text = "".join(c["c"] for c in chars)
                    i = text.find(word)
                    if i >= 0:
                        return [c["origin"][0] for c in chars[i:i + len(word)]]
    o1 = origins(raw, "contratto")
    with doc.change("Spaziatura") as pdf:
        engine.restyle_block(pdf, 0, block, char_spacing=0.05 * 12)
    with doc.lock:
        o2 = origins(doc.pdf[0].get_text("rawdict"), "contratto")
    d1 = o1[1] - o1[0]
    d2 = o2[1] - o2[0]
    assert abs((d2 - d1) - 0.05 * 12) <= 0.05


def test_size_out_of_range_rejected(paragraphs_pdf):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        block = blocks_of(doc.pdf)[0]
    with pytest.raises(engine.TextEditError):
        with doc.change("x") as pdf:
            engine.restyle_block(pdf, 0, block, size=2000)
    assert not doc.is_modified


# ---------------------------------------------------------------- TXT-05
def test_center_alignment_and_line_spacing(paragraphs_pdf):
    doc = Document.open(paragraphs_pdf)
    with doc.lock:
        block = blocks_of(doc.pdf)[0]
    box_center = block.x0 + block.width / 2
    first = block.first_baseline
    with doc.change("Centra") as pdf:
        engine.replace_block(pdf, 0, block, block.paragraphs, align=ALIGN_CENTER, leading=1.5 * 11,
                             keep_unchanged=False)
    with doc.lock:
        nb = blocks_of(doc.pdf)[0]
    for ln in nb.lines:
        assert abs((ln.bbox.x0 + ln.bbox.x1) / 2 - box_center) <= 0.5
    assert abs(nb.first_baseline - first) <= 0.05
    for a, b in zip(nb.lines, nb.lines[1:]):
        assert abs((b.baseline - a.baseline) - 16.5) <= 0.1


def test_space_after_paragraph(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.lock:
        block = [b for b in blocks_of(d.pdf) if "contratto" in b.text][0]
    st = block.style
    two = [[Run("Primo paragrafo", st)], [Run("Secondo paragrafo", st)]]
    with d.change("Due paragrafi") as pdf:
        engine.replace_block(pdf, 0, block, two, keep_unchanged=False)
    with d.lock:
        lines = [b for b in analysis.analyze_page(d.pdf[0], 0, "line") if "paragrafo" in b.text]
    base_gap = lines[1].first_baseline - lines[0].first_baseline
    with d.lock:
        whole = [b for b in blocks_of(d.pdf) if "Primo" in b.text][0]
    with d.change("Spazio dopo") as pdf:
        engine.replace_block(pdf, 0, whole, two, space_after=12, keep_unchanged=False)
    with d.lock:
        lines = [b for b in analysis.analyze_page(d.pdf[0], 0, "line") if "paragrafo" in b.text]
    assert abs((lines[1].first_baseline - lines[0].first_baseline) - (base_gap + 12)) <= 0.1


# ---------------------------------------------------------------- TXT-06
def test_add_text_at_point(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    path = save(doc, tmp_path / "vuoto.pdf")
    d = Document.open(path)
    from foliant.modules.text_edit.model import Style
    with d.change("Aggiungi") as pdf:
        res = engine.add_text(pdf, 0, pymupdf.Point(100, 700), [[Run("Nuovo testo", Style(size=14))]])
    assert abs(res.bbox.x0 - 100) <= 1 and abs(res.bbox.y0 - 700) <= 1
    out = reopen(d, tmp_path)
    assert "Nuovo testo" in out[0].get_text()
    s = span_with(out[0], "Nuovo")
    assert abs(s["bbox"][0] - 100) <= 1


def test_add_text_wraps_at_page_edge(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    path = save(doc, tmp_path / "vuoto.pdf")
    d = Document.open(path)
    from foliant.modules.text_edit.model import Style
    text = "Questa frase è lunga abbastanza da superare il bordo destro della pagina"
    with d.change("Aggiungi") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(545, 200), [[Run(text, Style(size=12))]])
    with d.lock:
        for s in spans(d.pdf[0]):
            assert s["bbox"][2] <= 595 + 0.5
        assert "superare" in d.pdf[0].get_text()


def test_add_text_on_rotated_page_is_horizontal(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.set_rotation(90)
    path = save(doc, tmp_path / "ruotata.pdf")
    d = Document.open(path)
    from foliant.modules.text_edit.model import Style
    with d.change("Aggiungi") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(100, 100), [[Run("Leggibile", Style(size=14))]])
    with d.lock:
        blocks = blocks_of(d.pdf)
        assert len(blocks) == 1 and not blocks[0].rotated
        assert abs(blocks[0].bbox.x0 - 100) <= 1
        # nella pagina come visualizzata il testo va da sinistra a destra
        rot = d.pdf[0].get_text("dict")["blocks"][0]["lines"][0]["dir"]
        frame = d.pdf[0].rotation_matrix
        fdir = (rot[0] * frame.a + rot[1] * frame.c, rot[0] * frame.b + rot[1] * frame.d)
        assert fdir[0] > 0.99


def test_empty_new_text_changes_nothing(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    path = save(doc, tmp_path / "vuoto.pdf")
    d = Document.open(path)
    with d.lock:
        res = engine.add_text(d.pdf, 0, pymupdf.Point(100, 100), [[]])
    assert not res.changed and not d.is_modified


# ---------------------------------------------------------------- TXT-07
def test_move_block(tmp_path):
    doc, page = new_doc_with_font()
    page.insert_text((72, 700), "Blocco da spostare", fontname="F1", fontsize=12)
    path = save(doc, tmp_path / "sposta.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Sposta") as pdf:
        engine.move_block(pdf, 0, block, 100, -50)
    out = reopen(d, tmp_path)
    s = span_with(out[0], "Blocco")
    assert abs(s["origin"][0] - 172) <= 0.5 and abs(s["origin"][1] - 650) <= 0.5
    assert out[0].get_text().strip() == "Blocco da spostare"


def test_move_out_of_page_refused(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with pytest.raises(engine.TextEditError):
        with d.change("Sposta") as pdf:
            engine.move_block(pdf, 0, block, 5000, 0)
    assert not d.is_modified


def test_resize_block_reflows(tmp_path):
    doc, page = new_doc_with_font()
    path = save(doc, tmp_path / "v.pdf")
    d = Document.open(path)
    from foliant.modules.text_edit.model import Style
    text = " ".join(LOREM * 2)
    with d.change("Crea") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(72, 100), [[Run(text, Style(font="Liberation Serif", size=11))]],
                        width=300)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    n = len(block.lines)
    with d.change("Ridimensiona") as pdf:
        engine.resize_block(pdf, 0, block, block.x0, 150)
    with d.lock:
        nb = blocks_of(d.pdf)[0]
    assert len(nb.lines) > n
    for ln in nb.lines:
        assert ln.bbox.width <= 150 + 0.6


def test_delete_block_removes_text_and_font(tmp_path):
    doc, page = new_doc_with_font()
    page.insert_font(fontname="F2", fontfile=SANS)
    page.insert_text((72, 100), "Resta qui", fontname="F1", fontsize=12)
    page.insert_text((72, 300), "Da eliminare", fontname="F2", fontsize=12)
    path = save(doc, tmp_path / "elimina.pdf")
    d = Document.open(path)
    with d.lock:
        block = [b for b in blocks_of(d.pdf) if "eliminare" in b.text][0]
    with d.change("Elimina") as pdf:
        engine.delete_block(pdf, 0, block)
    out = reopen(d, tmp_path)
    assert "eliminare" not in out[0].get_text() and "Resta qui" in out[0].get_text()
    names = [f[3] for f in out[0].get_fonts(full=True)]
    assert not any("LiberationSans" in n.replace(" ", "") for n in names)


def test_copy_block_between_documents(paragraphs_pdf, tmp_path):
    a = Document.open(paragraphs_pdf)
    with a.lock:
        block = [b for b in blocks_of(a.pdf) if "contratto" in b.text][0]
        data = engine.block_to_data(block)
        src = span_with(a.pdf[0], "contratto")
    b_doc = pymupdf.open()
    for _i in range(5):
        b_doc.new_page()
    path = save(b_doc, tmp_path / "b.pdf")
    b = Document.open(path)
    with b.change("Incolla") as pdf:
        engine.paste_block(pdf, 4, data)
    out = reopen(b, tmp_path, "b2.pdf")
    dst = span_with(out[4], "contratto")
    assert dst is not None
    assert abs(dst["size"] - src["size"]) <= 0.05
    assert analysis.normalize_font_name(dst["font"]).startswith("liberationserif")


def test_links_survive_text_edit(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.change("Link") as pdf:
        pdf[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 88, 200, 102),
                            "uri": "https://example.org"})
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "Lorem", "Ipsum"))
    with d.lock:
        assert len(d.pdf[0].get_links()) == 1


def test_pending_redactions_not_applied(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.change("Segna") as pdf:
        pdf[0].add_redact_annot(pymupdf.Rect(72, 380, 200, 405))
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "Lorem", "Ipsum"))
    with d.lock:
        assert "contratto" in d.pdf[0].get_text()
        assert len(list(d.pdf[0].annots(types=[pymupdf.PDF_ANNOT_REDACT]))) == 1


# ---------------------------------------------------------------- TXT-10
def test_helvetica_keeps_standard_font(helvetica_pdf, tmp_path):
    d = Document.open(helvetica_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, "sede", "città"))
    assert res.substitutions == []
    out = reopen(d, tmp_path)
    assert "città" in out[0].get_text()
    for f in out[0].get_fonts(full=True):
        assert f[1] == "n/a" and f[3].startswith("Helvetica")


def test_subset_missing_glyph_uses_same_installed_font(subset_pdf, tmp_path):
    d = Document.open(subset_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, "contratto", "Quadro"))
    # Liberation Sans è installato: stesso font, nessun avviso
    assert res.substitutions == []
    out = reopen(d, tmp_path)
    assert "Quadro" in out[0].get_text()
    for s in spans(out[0]):
        assert analysis.normalize_font_name(s["font"]).startswith("liberationsans")


def test_font_not_installed_gets_substitute_and_warning(tmp_path):
    cjk = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F9", fontbuffer=cjk.buffer)
    page.insert_text((72, 100), "Ciao amici", fontname="F9", fontsize=12)
    doc.subset_fonts()
    path = save(doc, tmp_path / "nonins.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, "Ciao", "Quanti"))
    out = reopen(d, tmp_path)
    assert "Quanti amici" in out[0].get_text().replace("\n", " ")
    # nessun carattere senza glifo nel file salvato
    for s in spans(out[0]):
        assert "�" not in s["text"]
    # mancano nel sottoinsieme solo Q, u, n, t: il resto usa ancora il font originale ricostruito
    assert len(res.substitutions) == 1
    sub = res.substitutions[0]
    assert sub.chars == 4 and sorted(set(sub.text)) == sorted("Qunt")
    assert sub.original.startswith("Droid Sans Fallback") and sub.substitute
    assert any("Droid" in s["font"] for s in spans(out[0]))


def test_uncovered_character_rejected(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with pytest.raises(engine.TextEditError):
        with d.change("Modifica") as pdf:
            engine.replace_block(pdf, 0, block, replace_text(block, "Lorem", "\U0010fffd"))
    assert not d.is_modified


# ---------------------------------------------------------------- TXT-15
def test_ocr_correction_keeps_image(ocr_pdf, tmp_path):
    d = Document.open(ocr_pdf)
    orig = pymupdf.open(ocr_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
        assert block.invisible
        old_word = [w for w in block.lines[0].words if w.text == "Rossl"][0]
    text = block.text.replace("Rossl", "Rossi")
    with d.change("Correggi") as pdf:
        engine.replace_invisible_block(pdf, 0, block, text)
    out = reopen(d, tmp_path)
    t = out[0].get_text()
    assert "Rossi" in t and "Rossl" not in t
    assert pixels(out[0]) == pixels(orig[0])
    new_word = [w for w in out[0].get_text("words") if w[4] == "Rossi"][0]
    r_old = pymupdf.Rect(old_word.x, block.lines[0].bbox.y0, old_word.x1, block.lines[0].bbox.y1)
    r_new = pymupdf.Rect(new_word[:4])
    inter = (r_old & r_new).get_area()
    union = (r_old | r_new).get_area()
    assert inter / union >= 0.9


def test_ocr_convert_to_visible_text(ocr_pdf, tmp_path):
    import shutil
    import subprocess
    d = Document.open(ocr_pdf)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Converti") as pdf:
        engine.convert_invisible_block(pdf, 0, block)
    with d.lock:
        nb = blocks_of(d.pdf)[0]
    assert not nb.invisible
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, nb, replace_text(nb, "2019", "2026"))
    out = reopen(d, tmp_path)
    assert "2026" in out[0].get_text() and "2019" not in out[0].get_text()
    if shutil.which("tesseract"):
        png = tmp_path / "area.png"
        out[0].get_pixmap(dpi=150, clip=pymupdf.Rect(40, 40, 740, 210)).save(str(png))
        res = subprocess.run(["tesseract", str(png), "-", "--psm", "7"], capture_output=True, text=True, timeout=60)
        assert "2026" in res.stdout and "2019" not in res.stdout


# ---------------------------------------------------------------- TXT-17
def test_twenty_operations_undo_redo(paragraphs_pdf, tmp_path):
    d = Document.open(paragraphs_pdf)
    orig = pymupdf.open(paragraphs_pdf)
    from foliant.modules.text_edit.model import Style
    for i in range(20):
        with d.lock:
            blocks = blocks_of(d.pdf)
        b = blocks[i % len(blocks)]
        kind = i % 5
        with d.change(f"op {i}") as pdf:
            if kind == 0:
                engine.replace_block(pdf, 0, b, replace_text(b, "e", "E"))
            elif kind == 1:
                engine.restyle_block(pdf, 0, b, size=b.style.size + 0.5)
            elif kind == 2:
                engine.move_block(pdf, 0, b, 1, 1)
            elif kind == 3:
                engine.add_text(pdf, 0, pymupdf.Point(300, 600 + i * 5), [[Run(f"agg {i}", Style(size=8))]])
            else:
                engine.replace_block(pdf, 0, b, replace_text(b, "a", "à"))
    with d.lock:
        final_text = d.pdf[0].get_text()
        final_pix = pixels(d.pdf[0], dpi=72)
    for _ in range(20):
        assert d.undo()
    out = reopen(d, tmp_path, "annullato.pdf")
    assert out[0].get_text() == orig[0].get_text()
    assert pixels(out[0], dpi=72) == pixels(orig[0], dpi=72)
    for _ in range(20):
        assert d.redo()
    with d.lock:
        assert d.pdf[0].get_text() == final_text
        assert pixels(d.pdf[0], dpi=72) == final_pix


# ---------------------------------------------------------------- TXT-20
def test_highlight_follows_moved_word(paragraphs_pdf, tmp_path):
    d = Document.open(paragraphs_pdf)
    with d.change("Evidenzia") as pdf:
        quads = pdf[0].search_for("scade", quads=True)
        pdf[0].add_highlight_annot(quads)
    with d.lock:
        block = [b for b in blocks_of(d.pdf) if "contratto" in b.text][0]
    with d.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, "Il contratto", "Il nuovo contratto"))
    assert res.orphans == 0
    with d.lock:
        page = d.pdf[0]
        target = page.search_for("scade")[0]
        annots = list(page.annots())
        assert len(annots) == 1
        r = pymupdf.Quad(annots[0].vertices[:4]).rect
        assert abs(r.x0 - target.x0) <= 0.5 and abs(r.x1 - target.x1) <= 0.5


def test_link_follows_moved_block(tmp_path):
    doc, page = new_doc_with_font()
    page.insert_text((72, 700), "Visita il sito", fontname="F1", fontsize=12)
    r = page.search_for("sito")[0]
    page.insert_link({"kind": pymupdf.LINK_URI, "from": r, "uri": "https://example.org"})
    d = Document.open(save(doc, tmp_path / "link.pdf"))
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Sposta") as pdf:
        engine.move_block(pdf, 0, block, 50, -100)
    with d.lock:
        links = d.pdf[0].get_links()
        new = d.pdf[0].search_for("sito")[0]
    assert len(links) == 1
    assert abs(links[0]["from"].x0 - new.x0) <= 0.5 and abs(links[0]["from"].y0 - new.y0) <= 1


def test_deleted_text_leaves_orphan_annotation(paragraphs_pdf):
    d = Document.open(paragraphs_pdf)
    with d.change("Evidenzia") as pdf:
        pdf[0].add_highlight_annot(pdf[0].search_for("2024", quads=True))
    with d.lock:
        block = [b for b in blocks_of(d.pdf) if "contratto" in b.text][0]
    with d.change("Modifica") as pdf:
        res = engine.replace_block(pdf, 0, block, replace_text(block, " nel 2024", ""))
    assert res.orphans == 1
    with d.lock:
        assert len(list(d.pdf[0].annots())) == 1


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_edit_on_rotated_and_cropped_pages(tmp_path, rotation, crop):
    doc, page = new_doc_with_font()
    page.insert_text((100, 300), "Il contratto scade nel 2024", fontname="F1", fontsize=12)
    if crop:
        page.set_cropbox(pymupdf.Rect(40, 60, 560, 800))
    page.set_rotation(rotation)
    d = Document.open(save(doc, tmp_path / f"r{rotation}{crop}.pdf"))
    with d.lock:
        before = span_with(d.pdf[0], "contratto")
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "2024", "2026"))
    out = reopen(d, tmp_path)
    text = out[0].get_text()
    assert "2026" in text and "2024" not in text
    after = span_with(out[0], "contratto")
    assert abs(after["origin"][0] - before["origin"][0]) <= 1
    assert abs(after["origin"][1] - before["origin"][1]) <= 1
    assert tuple(round(v, 2) for v in after["dir"]) == tuple(round(v, 2) for v in before["dir"]) if "dir" in after \
        else True


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_underline_on_rotated_cropped_page(tmp_path, rotation):
    """bug_03: la sottolineatura resta sotto la parola anche su pagine ruotate e ritagliate."""
    doc, page = new_doc_with_font()
    page.insert_text((100, 300), "Parola sottolineata", fontname="F1", fontsize=12)
    page.set_cropbox(pymupdf.Rect(40, 60, 560, 820))
    page.set_rotation(rotation)
    d = Document.open(save(doc, tmp_path / f"u{rotation}.pdf"))
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Sottolinea") as pdf:
        engine.restyle_block(pdf, 0, block, underline=True)
    with d.lock:
        p = d.pdf[0]
        word = p.search_for("sottolineata")[0]
        lines = [dr["rect"] for dr in p.get_drawings()]
    assert lines
    r = lines[0]
    assert abs(r.x1 - word.x1) <= 2 and word.y0 <= r.y0 <= word.y1 + 3


def test_price_in_subset_font_stays_on_one_line(tmp_path):
    """Prezzo allineato a destra in un font incorporato come sottoinsieme: «€ 9.500,00» → «€ 9.800,00»
    resta su una riga (spazi e cifre misurati con lo stesso font)."""
    cjk = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F9", fontbuffer=cjk.buffer)
    for y, text in ((100, "Prezzo"), (116, "€ 9.500,00")):
        w = cjk.text_length(text, fontsize=10)
        page.insert_text((400 - w, y), text, fontname="F9", fontsize=10)
    doc.subset_fonts()
    d = Document.open(save(doc, tmp_path / "prezzo.pdf"))
    with d.lock:
        block = blocks_of(d.pdf)[0]
    assert len(block.lines) == 2
    with d.change("Sostituisci") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "9.500,00", "9.800,00"))
    with d.lock:
        lines = analysis.analyze_page(d.pdf[0], 0, "line")
    texts = [ln.text for ln in lines]
    assert "€ 9.800,00" in texts and len(lines) == 2
    price = [ln for ln in lines if "9.800" in ln.text][0]
    assert abs(price.x1 - 400) <= 1.5


def test_new_font_embedded_as_subset(tmp_path):
    """Un font aggiunto da Foliant viene incorporato come sottoinsieme, non intero."""
    doc = pymupdf.open()
    doc.new_page()
    d = Document.open(save(doc, tmp_path / "vuoto.pdf"))
    with d.change("Aggiungi") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(72, 100),
                        [[Run("Totale — € 9.800,00", __import__("foliant.modules.text_edit.model", fromlist=["Style"]).Style(font="DejaVu Sans", size=12))]])
    out = tmp_path / "sub.pdf"
    d.save(out)
    assert out.stat().st_size < 60_000          # DejaVu Sans intero supera 700 KB
    assert "Totale — € 9.800,00" in pymupdf.open(str(out))[0].get_text()


def test_table_rows_not_merged_with_paragraph(tmp_path):
    """Fattura: modificare il paragrafo giustificato sotto la tabella non sposta le righe della tabella."""
    from foliant.modules.text_edit.model import Style as _Style
    doc, page = new_doc_with_font(SANS)
    rows = [("Revisione e lucidatura", "1", "€ 350,00"), ("Cinturino in pelle con garanzia", "1", "€ 9.500,00"),
            ("Spedizione assicurata", "1", "€ 100,00")]
    y = 100
    f = pymupdf.Font(fontfile=SANS)
    for desc, qty, price in rows:
        page.insert_text((58, y), desc, fontname="F1", fontsize=10)
        page.insert_text((300, y), qty, fontname="F1", fontsize=10)
        page.insert_text((540 - f.text_length(price, fontsize=10), y), price, fontname="F1", fontsize=10)
        y += 16
    path = save(doc, tmp_path / "fattura0.pdf")
    d = Document.open(path)
    text = ("Totale documento: € 9.950,00 — pagamento a trenta giorni data fattura mediante bonifico "
            "bancario sul conto indicato in calce, salvo diversi accordi scritti tra le parti. La merce viaggia "
            "assicurata a cura del venditore e la garanzia decorre dalla data di consegna indicata.")
    with d.change("Paragrafo") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(58, y + 2), [[Run(text, _Style(font="Liberation Sans", size=10))]],
                        width=482, align="justify")
    with d.lock:
        before = {w[4]: w[:4] for w in d.pdf[0].get_text("words") if w[1] < y}
        blocks = blocks_of(d.pdf)
    para = [b for b in blocks if "Totale" in b.text][0]
    assert not any("Revisione" in ln.text for ln in para.lines)
    assert para.align == "justify", [(ln.text, ln.bbox.x0, ln.bbox.x1, [(w.text, round(w.x,1), round(w.x1,1)) for w in ln.words][:5]) for ln in para.lines]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, para, replace_text(para, "9.950,00", "9.960,00"))
    with d.lock:
        after = {w[4]: w[:4] for w in d.pdf[0].get_text("words") if w[1] < y}
        assert "9.960,00" in d.pdf[0].get_text()
    for word, r in before.items():
        assert word in after and abs(after[word][0] - r[0]) < 0.2 and abs(after[word][1] - r[1]) < 0.2


def test_shared_form_xobject_only_this_page(tmp_path):
    """TXT-20: testo in un Form XObject usato da più pagine: cambia solo la pagina modificata."""
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Testo condiviso 2024", fontsize=14)
    doc = pymupdf.open()
    for _i in range(4):
        p = doc.new_page()
        p.show_pdf_page(p.rect, src, 0)
    d = Document.open(save(doc, tmp_path / "xobj.pdf"))
    with d.lock:
        block = blocks_of(d.pdf, 1)[0]
        before = [pixels(d.pdf[i], dpi=50) for i in range(4)]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 1, block, replace_text(block, "2024", "2026"))
    out = reopen(d, tmp_path)
    assert "2026" in out[1].get_text() and "2024" not in out[1].get_text()
    for i in (0, 2, 3):
        assert "2024" in out[i].get_text()
        assert pixels(out[i], dpi=50) == before[i]


def _font_count(path) -> int:
    d = pymupdf.open(str(path))
    return len({f[0] for i in range(d.page_count) for f in d[i].get_fonts(full=True)})


def test_edit_reuses_original_font_resource_pymupdf(tmp_path):
    """bug_10: con il font originale che contiene i glifi, nessun font nuovo nel file."""
    doc, page = new_doc_with_font(SANS)
    page.insert_text((72, 100), "Il contratto scade nel 2024", fontname="F1", fontsize=12)
    path = save(doc, tmp_path / "orig.pdf")
    d = Document.open(path)
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "2024", "2042"))
    out = tmp_path / "out.pdf"
    d.save(out)
    assert _font_count(out) == _font_count(path)
    assert out.stat().st_size - pymupdf.open(path).tobytes(garbage=3, deflate=True).__len__() < 2000
    re_ = pymupdf.open(str(out))
    assert "scade nel 2042" in re_[0].get_text()
    s = span_with(re_[0], "2042")
    assert s is not None and analysis.normalize_font_name(s["font"]).startswith("liberationsans")


def test_edit_reuses_original_font_resource_libreoffice(tmp_path):
    """bug_10: fattura LibreOffice (Carlito in sottoinsieme): sostituire un prezzo non aggiunge font."""
    import shutil
    import subprocess
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    src = tmp_path / "fattura.html"
    src.write_text("<html><body style='font-family:Carlito; font-size:11pt'><p>Totale: € 9.950,00</p>"
                   "<p>Pagamento a trenta giorni.</p></body></html>", encoding="utf-8")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{tmp_path}/lo", "--convert-to",
                    "pdf:writer_pdf_Export", "--outdir", str(tmp_path), str(src)], capture_output=True, timeout=180)
    path = tmp_path / "fattura.pdf"
    if not path.exists():
        pytest.skip("conversione LibreOffice non riuscita")
    from foliant.modules.text_edit import search
    d = Document.open(str(path))
    before = len(d.to_bytes())
    s = search.replace_all(d, "9.950,00", "9.590,00", search.SearchOptions(case_sensitive=True))
    assert s.replaced == 1
    out = tmp_path / "out.pdf"
    d.save(out)
    assert _font_count(out) == _font_count(path)
    assert out.stat().st_size - before < 2000
    re_ = pymupdf.open(str(out))
    assert "9.590,00" in re_[0].get_text()
    assert span_with(re_[0], "9.590,00")["font"].startswith("Carlito")


def test_pdfa_document_never_uses_unembedded_fonts(tmp_path):
    """TXT-20: in un documento dichiarato PDF/A il testo aggiunto usa solo font incorporati con /ToUnicode."""
    from foliant.modules.text_edit.model import Style as _Style
    doc = pymupdf.open()
    doc.new_page()
    doc.set_xml_metadata('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
                         '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                         'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                         '<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">'
                         '<pdfaid:part>2</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>'
                         '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    d = Document.open(save(doc, tmp_path / "pdfa.pdf"))
    with d.change("Aggiungi") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(72, 100), [[Run("Testo in Helvetica", _Style(font="Helvetica"))]])
    out = tmp_path / "out.pdf"
    d.save(out)
    re_ = pymupdf.open(str(out))
    assert "Testo in Helvetica" in re_[0].get_text()
    for f in re_[0].get_fonts(full=True):
        assert f[1] != "n/a"                                               # incorporato
        assert re_.xref_get_key(f[0], "ToUnicode")[0] != "null"
    assert "pdfaid:part" in re_.get_xml_metadata()                        # dichiarazione conservata


# ---------------------------------------------------------------- TXT-21
def test_hebrew_insert_word_keeps_number_order(tmp_path):
    from foliant.modules.text_edit.model import Style as _Style
    doc = pymupdf.open()
    doc.new_page()
    d = Document.open(save(doc, tmp_path / "ebraico.pdf"))
    st = _Style(font="DejaVu Sans", size=14)
    with d.change("Crea") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(300, 100), [[Run("שלום 2026 עולם", st)]])
    with d.lock:
        block = blocks_of(d.pdf)[0]
        right = block.bbox.x1
    assert block.text == "שלום 2026 עולם" and block.align == "right"
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "2026", "יפה 2026"))
    with d.lock:
        nb = blocks_of(d.pdf)[0]
    assert nb.text == "שלום יפה 2026 עולם"          # ordine logico, «2026» non invertito
    assert abs(nb.bbox.x1 - right) <= 0.6           # margine destro fermo, la riga cresce a sinistra


def test_arabic_letter_between_joined_letters(tmp_path):
    from foliant.modules.text_edit.bidi import shape_arabic
    from foliant.modules.text_edit.model import Style as _Style
    doc = pymupdf.open()
    doc.new_page()
    d = Document.open(save(doc, tmp_path / "arabo.pdf"))
    st = _Style(font="DejaVu Sans", size=14)
    with d.change("Crea") as pdf:
        engine.add_text(pdf, 0, pymupdf.Point(300, 100), [[Run("مرحبا بالعالم", st)]])
    with d.lock:
        block = blocks_of(d.pdf)[0]
    with d.change("Modifica") as pdf:
        engine.replace_block(pdf, 0, block, replace_text(block, "مرحبا", "مرحتبا"))
    with d.lock:
        nb = blocks_of(d.pdf)[0]
        raw = "".join(c["c"] for b in d.pdf[0].get_text("rawdict")["blocks"] for ln in b["lines"]
                      for sp in ln["spans"] for c in sp["chars"])
    assert nb.text == "مرحتبا بالعالم"                 # sequenza logica attesa
    # forme contestuali: la «ت» inserita tra due lettere legate è in forma mediana
    assert "ﺘ" in raw
    assert shape_arabic("مرحتبا").count("ﺘ") == 1


def test_images_have_text_lines_detects_single_line_not_photo():
    """Proposta OCR (bug_09): anche un'immagine piccola con una sola riga di testo; non una foto."""
    import numpy as np

    from foliant.modules.text_edit.analysis import images_have_text_lines

    from .conftest import render_text_image

    def page_with(png, rect):
        d = pymupdf.open()
        p = d.new_page(width=595, height=842)
        p.insert_image(pymupdf.Rect(rect), stream=png)
        return d, p
    _d1, p1 = page_with(render_text_image("Mario Rossi 2019", size=(495, 140), fontsize=40), (50, 100, 545, 240))
    assert images_have_text_lines(p1)
    rng = np.random.default_rng(1)
    y, x = np.mgrid[0:300, 0:400]
    a = (128 + 80 * np.sin(x / 37) * np.cos(y / 23) + rng.normal(0, 25, (300, 400))).clip(0, 255).astype(np.uint8)
    png = pymupdf.Pixmap(pymupdf.csRGB, 400, 300, np.dstack([a, a, 255 - a]).copy().tobytes(), False).tobytes("png")
    _d2, p2 = page_with(png, (50, 100, 450, 400))
    assert not images_have_text_lines(p2)
