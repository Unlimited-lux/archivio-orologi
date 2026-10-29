"""Test di Trova e sostituisci / Trova ed elimina (TXT-12, TXT-13)."""

from __future__ import annotations

import os
import re

import pymupdf
import pytest

from tests.conftest import system_font
from foliant.core.platform import find_tool
from foliant.core.document import Document
from foliant.modules.text_edit import search as S
from foliant.modules.text_edit.model import SOFT_HYPHEN, Line, Run, Style, TextBlock

DEJAVU = system_font("truetype/dejavu/DejaVuSans.ttf")
LIBERATION = system_font("truetype/liberation/LiberationSans-Regular.ttf")
COLOR = (0.2, 0.2, 0.6)


def _fornitore_pdf(path) -> str:
    """20 pagine con 37 «Fornitore» (più alcuni «fornitore» minuscoli che non vanno toccati)."""
    doc = pymupdf.open()
    count = 0
    for i in range(20):
        page = doc.new_page()
        page.insert_text((72, 80), f"Articolo {i + 1}", fontname="helv", fontsize=14)
        y = 120
        per_page = 2 if i < 17 else 1     # 17*2 + 3*1 = 37
        for _k in range(per_page):
            page.insert_text((72, y), "Il Fornitore consegna la merce entro trenta giorni.", fontname="helv",
                             fontsize=11, color=COLOR)
            count += 1
            y += 40
        page.insert_text((72, y), "Ogni fornitore secondario resta escluso.", fontname="helv", fontsize=11)
    assert count == 37
    doc.save(str(path))
    doc.close()
    return str(path)


def _doc_from(build) -> Document:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return Document.from_bytes(data)


def _page_text(document: Document) -> str:
    with document.lock:
        return "\n".join(p.get_text() for p in document.pdf)


def _file_text(path) -> str:
    with pymupdf.open(str(path)) as pdf:
        return "\n".join(p.get_text() for p in pdf)


# ====================================================================== criteri di accettazione TXT-12
def test_replace_all_37_occurrences_saved(tmp_path):
    src = _fornitore_pdf(tmp_path / "contratto.pdf")
    document = Document.open(src)
    found = S.find_in_document(document, "Fornitore", S.SearchOptions(case_sensitive=True))
    assert len(found) == 37
    summary = S.replace_all(document, "Fornitore", "Appaltatore", S.SearchOptions(case_sensitive=True))
    assert summary.replaced == 37
    assert summary.not_replaceable == []
    assert document.undo_label == "Sostituisci tutto (37)"
    out = tmp_path / "salvato.pdf"
    document.save(out)
    text = _file_text(out)
    assert text.count("Appaltatore") == 37
    assert text.count("Fornitore") == 0
    assert text.count("fornitore") == 20          # minuscole intatte (maiuscole/minuscole attive)
    with pymupdf.open(str(out)) as pdf:
        spans = [s for p in pdf for b in p.get_text("dict")["blocks"] for ln in b.get("lines", [])
                 for s in ln["spans"] if "Appaltatore" in s["text"]]
    assert sum(s["text"].count("Appaltatore") for s in spans) == 37
    for s in spans:
        assert abs(s["size"] - 11) <= 0.05
        assert "Helvetica" in s["font"]
        assert pymupdf.sRGB_to_pdf(s["color"]) == pytest.approx(COLOR, abs=0.01)


def test_single_undo_reverts_all(tmp_path):
    document = Document.open(_fornitore_pdf(tmp_path / "c.pdf"))
    before = _page_text(document)
    S.replace_all(document, "Fornitore", "Appaltatore", S.SearchOptions(case_sensitive=True))
    assert _page_text(document).count("Appaltatore") == 37
    assert document.undo()
    after = _page_text(document)
    assert after.count("Fornitore") == 37 and "Appaltatore" not in after
    assert after == before
    assert not document.can_undo


def test_regex_groups_date():
    def build(doc):
        doc.new_page().insert_text((72, 100), "data: 03/11/2025", fontname="helv", fontsize=12)
    document = _doc_from(build)
    summary = S.replace_all(document, r"(\d{2})/(\d{2})/(\d{4})", "$3-$2-$1", S.SearchOptions(regex=True))
    assert summary.replaced == 1
    assert "data: 2025-11-03" in _page_text(document)


def test_named_groups_and_dollar():
    def build(doc):
        doc.new_page().insert_text((72, 100), "prezzo 120 euro", fontname="helv", fontsize=12)
    document = _doc_from(build)
    S.replace_all(document, r"(?P<n>\d+) euro", "$$${n}", S.SearchOptions(regex=True))
    assert "prezzo $120" in _page_text(document)


def test_ligature_found_by_plain_text():
    if not os.path.exists(DEJAVU):
        pytest.skip("DejaVuSans non disponibile")

    def build(doc):
        page = doc.new_page()
        page.insert_font(fontname="dejavu", fontfile=DEJAVU)
        page.insert_text((72, 100), "La \ufb01ne del contratto", fontname="dejavu", fontsize=12)
    document = _doc_from(build)
    with document.lock:
        raw = document.pdf[0].get_text("rawdict")
    chars = "".join(c["c"] for b in raw["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                    for c in s["chars"])
    if "\ufb01" not in chars:
        pytest.skip("il motore non estrae la legatura U+FB01 da questo PDF")
    found = S.find_in_document(document, "fine")
    assert len(found) == 1
    assert found[0].rects
    # anche la ricerca con la legatura trova la parola
    assert len(S.find_in_document(document, "\ufb01ne")) == 1


def test_ligature_in_block_text_normalized():
    st = Style()
    block = _block("La \ufb01ne e la \ufb02otta", st)
    rx = S.compile_pattern("fine", S.SearchOptions())
    m = S.block_matches(block, rx, S.SearchOptions())
    assert len(m) == 1 and m[0].text == "\ufb01ne"
    m = S.block_matches(block, S.compile_pattern("flotta", S.SearchOptions()), S.SearchOptions())
    assert len(m) == 1


# ====================================================================== TXT-13
def test_delete_12_bozza_no_double_spaces(tmp_path):
    texts = ["BOZZA Documento interno", "Versione BOZZA del contratto", "BOZZA testo finale BOZZA",
             "Stato: BOZZA, da rivedere", "Allegato A BOZZA BOZZA numero due", "BOZZA",
             "Note (BOZZA) generali", "Prima BOZZA. Seconda riga", "Ultima pagina BOZZA BOZZA"]
    assert sum(t.count("BOZZA") for t in texts) == 12

    def build(doc):
        page = doc.new_page()
        for i, t in enumerate(texts):
            page.insert_text((72, 80 + 40 * i), t, fontname="helv", fontsize=11)
    document = _doc_from(build)
    summary = S.replace_all(document, "BOZZA", "", S.SearchOptions(case_sensitive=True), delete=True)
    assert summary.replaced == 12
    assert document.undo_label == "Trova ed elimina (12)"
    out = tmp_path / "pulito.pdf"
    document.save(out)
    text = _file_text(out)
    assert "BOZZA" not in text
    for line in text.splitlines():
        assert "  " not in line, line
        assert line == line.strip(), line
    assert "Stato:, da rivedere" in text
    assert "Note () generali" in text
    # un solo Annulla fa ricomparire tutto
    assert document.undo()
    assert _page_text(document).count("BOZZA") == 12


def test_delete_without_space_removal():
    def build(doc):
        doc.new_page().insert_text((72, 100), "uno BOZZA due", fontname="helv", fontsize=11)
    document = _doc_from(build)
    S.replace_all(document, "BOZZA", "", delete=True, remove_adjacent_space=False)
    with document.lock:
        words = [w[4] for w in document.pdf[0].get_text("words")]
    assert words == ["uno", "due"]


# ====================================================================== opzioni
def _block(text: str, style: Style | None = None, reason: str = "") -> TextBlock:
    style = style or Style()
    line = Line(bbox=pymupdf.Rect(72, 88, 300, 102), origin=pymupdf.Point(72, 100), runs=[Run(text, style)])
    paragraphs = [[Run(p, style)] for p in text.split("\n")]
    return TextBlock(page=0, index=0, lines=[line], paragraphs=paragraphs, reason=reason)


def _count(text: str, pattern: str, **opts) -> int:
    options = S.SearchOptions(**opts)
    return len(S.block_matches(_block(text), S.compile_pattern(pattern, options), options))


def test_case_and_whole_word_options():
    text = "Fornitore e fornitore; FORNITORE. Fornitores"
    assert _count(text, "fornitore") == 4
    assert _count(text, "fornitore", case_sensitive=True) == 1
    assert _count(text, "fornitore", whole_word=True) == 3
    assert _count(text, "Fornitore", whole_word=True, case_sensitive=True) == 1
    assert _count("a.b a b", "a.b") == 1                 # letterale: il punto non è un jolly
    assert _count("a.b axb", "a.b", regex=True) == 2


def test_ignore_accents_and_spaces():
    assert _count("perché, perche, PERCHÉ", "perche") == 1
    assert _count("perché, perche, PERCHÉ", "perche", ignore_accents=True) == 3
    assert _count("perché", "perchè", ignore_accents=True) == 1
    # caratteri composti (NFD) equivalenti a quelli precomposti (NFC)
    assert _count("caffe\u0300 caffè", "caffè") == 2
    # spazi consecutivi equivalenti a uno
    assert _count("due    spazi", "due spazi") == 1
    # trattino di sillabazione invisibile
    st = Style()
    line = Line(bbox=pymupdf.Rect(0, 0, 10, 10), origin=pymupdf.Point(0, 8), runs=[Run("x", st)])
    block = TextBlock(page=0, index=0, lines=[line], paragraphs=[[Run("il contrat" + SOFT_HYPHEN + "to firmato", st)]])
    m = S.block_matches(block, S.compile_pattern("contratto", S.SearchOptions()), S.SearchOptions())
    assert len(m) == 1 and m[0].text == "contratto"


def test_match_across_line_breaks_and_hyphenation():
    def build(doc):
        page = doc.new_page()
        lines = ["Questo paragrafo descrive il contrat-", "to di fornitura e le condizioni generali",
                 "di vendita applicate."]
        for i, t in enumerate(lines):
            page.insert_text((72, 100 + i * 14), t, fontname="helv", fontsize=11)
    document = _doc_from(build)
    found = S.find_in_document(document, "contratto di fornitura")
    assert len(found) == 1
    assert len(found[0].rects) == 2               # due righe evidenziate
    found = S.find_in_document(document, "generali di vendita")
    assert len(found) == 1
    S.replace_all(document, "contratto", "accordo")
    text = _page_text(document)
    assert "accordo" in text and "contrat" not in text


def test_invalid_regex_raises_italian_error():
    with pytest.raises(S.SearchError) as exc:
        S.compile_pattern("(abc", S.SearchOptions(regex=True))
    assert "Espressione regolare non valida" in str(exc.value)
    assert "parentesi" in str(exc.value)
    with pytest.raises(S.SearchError):
        S.find_all(pymupdf.open(), "[a-", S.SearchOptions(regex=True))
    with pytest.raises(S.SearchError):
        S.compile_pattern("", S.SearchOptions())
    # il testo letterale non è mai un'espressione non valida
    S.compile_pattern("(abc", S.SearchOptions())


def test_replacement_templates():
    assert S.to_python_template("$3-$2-$1") == r"\g<3>-\g<2>-\g<1>"
    assert S.to_python_template("${anno}/x\\y") == "\\g<anno>/x\\\\y"
    m = re.match(r"(\d+)-(?P<b>\d+)", "12-34")
    assert S.expand_replacement(m, "$2:$1:${b}:$$") == "34:12:34:$"
    assert m.expand(S.to_python_template("$2/$1")) == "34/12"
    with pytest.raises(S.SearchError):
        S.expand_replacement(m, "$5")
    with pytest.raises(S.SearchError):
        S.check_replacement(r"(\d+)", "$2", S.SearchOptions(regex=True))
    S.check_replacement(r"(\d+)", "$1 $0", S.SearchOptions(regex=True))
    S.check_replacement("abc", "$9", S.SearchOptions())       # letterale: nessun controllo


def test_replacement_keeps_original_accents_in_groups():
    def build(doc):
        page = doc.new_page()
        page.insert_font(fontname="lib", fontfile=LIBERATION)
        page.insert_text((72, 100), "città vecchia", fontname="lib", fontsize=12)
    if not os.path.exists(LIBERATION):
        pytest.skip("LiberationSans non disponibile")
    document = _doc_from(build)
    S.replace_all(document, r"(citta) (\w+)", "$2 $1", S.SearchOptions(regex=True, ignore_accents=True))
    assert "vecchia città" in _page_text(document)


# ====================================================================== riscrittura del blocco
def test_replace_in_block_styles():
    a = Style(font="Helvetica", size=10)
    b = Style(font="Helvetica", size=10, bold=True)
    block = TextBlock(page=0, index=0, lines=[], paragraphs=[
        [Run("Il For", a), Run("nitore paga", b)], [Run("secondo For" + SOFT_HYPHEN, a), Run("nitore", a)]])
    rx = S.compile_pattern("Fornitore", S.SearchOptions())
    block.lines = [Line(bbox=pymupdf.Rect(0, 0, 1, 1), origin=pymupdf.Point(0, 0), runs=[Run("x", a)])]
    matches = S.block_matches(block, rx, S.SearchOptions())
    assert [m.text for m in matches] == ["Fornitore", "Fornitore"]
    paras = S.replace_in_block(block, matches, ["Appaltatore", "Cliente"])
    assert [[(r.text, r.style.bold) for r in p] for p in paras] == [
        [("Il Appaltatore", False), (" paga", True)], [("secondo Cliente", False)]]


def test_replace_in_block_preserves_soft_hyphen_elsewhere():
    st = Style()
    block = _block("x", st)
    block.paragraphs = [[Run("il contrat" + SOFT_HYPHEN + "to del Fornitore", st)]]
    m = S.block_matches(block, S.compile_pattern("Fornitore", S.SearchOptions()), S.SearchOptions())
    paras = S.replace_in_block(block, m, ["Cliente"])
    assert paras[0][0].text == "il contrat" + SOFT_HYPHEN + "to del Cliente"


def test_delete_whole_paragraph_removed():
    st = Style()
    block = _block("x", st)
    block.paragraphs = [[Run("BOZZA", st)], [Run("Testo", st)]]
    m = S.block_matches(block, S.compile_pattern("BOZZA", S.SearchOptions()), S.SearchOptions())
    paras = S.replace_in_block(block, m, [""], delete=True, remove_adjacent_space=True)
    assert [[r.text for r in p] for p in paras] == [["Testo"]]


def test_non_editable_block_not_replaceable():
    block = _block("testo illeggibile Fornitore", reason="non decodificabile")
    rx = S.compile_pattern("Fornitore", S.SearchOptions())
    m = S.block_matches(block, rx, S.SearchOptions())
    assert len(m) == 1
    assert not m[0].replaceable
    assert "non decodificabile" in m[0].reason


# ====================================================================== ambito, singola sostituzione, casi limite
def test_pages_scope_and_ranges(tmp_path):
    document = Document.open(_fornitore_pdf(tmp_path / "c.pdf"))
    found = S.find_in_document(document, "Fornitore", S.SearchOptions(case_sensitive=True), pages=[0, 19])
    assert len(found) == 3 and {m.page for m in found} == {0, 19}
    summary = S.replace_all(document, "Fornitore", "Ditta", S.SearchOptions(case_sensitive=True), pages=[1])
    assert summary.replaced == 2
    assert S.parse_page_ranges("1-3, 5", 10) == [0, 1, 2, 4]
    assert S.parse_page_ranges("4–2", 10) == [1, 2, 3]
    for bad in ("", "0", "1-11", "a-b", "3,,x"):
        with pytest.raises(S.SearchError):
            S.parse_page_ranges(bad, 10)


def test_no_match_creates_no_undo_step(tmp_path):
    document = Document.open(_fornitore_pdf(tmp_path / "c.pdf"))
    summary = S.replace_all(document, "Inesistente", "X")
    assert summary.replaced == 0
    assert not document.can_undo
    assert not document.is_modified


def test_replace_one_and_gone(tmp_path):
    document = Document.open(_fornitore_pdf(tmp_path / "c.pdf"))
    opts = S.SearchOptions(case_sensitive=True)
    found = S.find_in_document(document, "Fornitore", opts)
    summary = S.replace_one(document, found[1], "Committente", opts)
    assert summary.replaced == 1
    assert document.can_undo
    text = _page_text(document)
    assert text.count("Committente") == 1 and text.count("Fornitore") == 36
    with pytest.raises(S.SearchError, match="non esiste più"):
        S.replace_one(document, found[1], "Altro", opts)
    assert document.undo_label.startswith("Sostituisci")


def test_match_metadata_and_rects(tmp_path):
    document = Document.open(_fornitore_pdf(tmp_path / "c.pdf"))
    m = S.find_in_document(document, "Fornitore", S.SearchOptions(case_sensitive=True))[0]
    assert m.page == 0 and m.text == "Fornitore"
    assert "Il Fornitore consegna" in m.context
    with document.lock:
        expected = document.pdf[0].search_for("Fornitore")[0]
    assert len(m.rects) == 1
    r = m.rects[0]
    assert abs(r.x0 - expected.x0) < 2 and abs(r.x1 - expected.x1) < 2
    assert r.intersects(expected)


def test_rects_on_rotated_page():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 100), "Testo con Fornitore qui", fontname="helv", fontsize=12)
        page.set_rotation(90)
        page2 = doc.new_page()
        page2.set_rotation(90)
        page2.insert_text(pymupdf.Point(72, 100) * page2.derotation_matrix, "Riga orizzontale Fornitore",
                          fontname="helv", fontsize=12, rotate=90)
    document = _doc_from(build)
    found = S.find_in_document(document, "Fornitore")
    assert {m.page for m in found} == {0, 1}
    with document.lock:
        for m in found:
            expected = document.pdf[m.page].search_for("Fornitore")[0]
            assert m.rects and any(r.intersects(expected) for r in m.rects)
            union = m.rects[0]
            assert union.get_area() < 4 * expected.get_area()


def test_delete_without_recompose_keeps_positions(tmp_path):
    """TXT-13: con «Ricomponi il paragrafo» disattivato le parole successive restano ferme (±0,5 pt)."""
    import pymupdf as _pm
    from foliant.api import Document as _Doc
    from foliant.modules.text_edit import search as _search
    doc = _pm.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Prima BOZZA dopo la parola", fontname="helv", fontsize=12)
    page.insert_text((72, 130), "Seconda riga intatta", fontname="helv", fontsize=12)
    path = tmp_path / "nr.pdf"
    doc.save(str(path))
    d = _Doc.open(str(path))
    with d.lock:
        before = {w[4]: w[:4] for w in d.pdf[0].get_text("words")}
    s = _search.replace_all(d, "BOZZA", "", _search.SearchOptions(case_sensitive=True), delete=True,
                            recompose=False)
    assert s.replaced == 1
    with d.lock:
        after = {w[4]: w[:4] for w in d.pdf[0].get_text("words")}
    assert "BOZZA" not in after
    for word in ("Prima", "dopo", "la", "parola", "Seconda", "intatta"):
        assert abs(after[word][0] - before[word][0]) <= 0.5 and abs(after[word][1] - before[word][1]) <= 0.5
    assert d.undo() and "BOZZA" in d.page_text(0)


INVOICE_HTML = """<html><body style="font-family:Carlito; font-size:11pt">
<h2>Orologeria Verdi — Fattura n. 42</h2>
<table width="100%" cellpadding="2" style="border-collapse:collapse{border}">
<colgroup><col width="55%"><col width="15%"><col width="30%"></colgroup>
<tr><td{cell}>Descrizione</td><td{cell}>Q.tà</td><td{cell} align="right">Prezzo</td></tr>
<tr><td{cell}>Revisione e lucidatura</td><td{cell}>1</td><td{cell} align="right">€ 350,00</td></tr>
<tr><td{cell}>Rolex Submariner 16610 con garanzia</td><td{cell}>1</td><td{cell} align="right">€ 9.500,00</td></tr>
<tr><td{cell}>Spedizione assicurata</td><td{cell}>1</td><td{cell} align="right">€ 100,00</td></tr>
</table>
<p style="text-align:justify; margin-top:0">Totale documento: € 9.950,00 — il pagamento è dovuto entro trenta
giorni dalla data della fattura. Pagamento mediante bonifico bancario sulle coordinate indicate in calce,
salvo diversi accordi scritti tra le parti e fatte salve le condizioni generali di vendita.</p>
</body></html>"""


@pytest.mark.parametrize("bordered", [False, True])
def test_invoice_replace_keeps_table_and_justification(tmp_path, bordered):
    """Fattura da LibreOffice: «9.950,00» → «9.960,00» non sposta le celle della tabella e il
    paragrafo resta giustificato con le stesse linee di base."""
    import subprocess
    import pymupdf as _pm
    from foliant.api import Document as _Doc
    from foliant.modules.text_edit import analysis as _an
    from foliant.modules.text_edit import search as _search
    soffice = find_tool("soffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    html = INVOICE_HTML.replace("{border}", "; border:1px solid black" if bordered else "") \
        .replace("{cell}", ' style="border:1px solid black"' if bordered else "")
    src = tmp_path / "fattura.html"
    src.write_text(html, encoding="utf-8")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{tmp_path}/lo", "--convert-to",
                    "pdf:writer_pdf_Export", "--outdir", str(tmp_path), str(src)],
                   capture_output=True, timeout=180)
    pdf_path = tmp_path / "fattura.pdf"
    if not pdf_path.exists():
        pytest.skip("conversione LibreOffice non riuscita")
    d = _Doc.open(str(pdf_path))
    with d.lock:
        page = d.pdf[0]
        before_words = {(w[4], round(w[1])): w[:4] for w in page.get_text("words")}
        para = [b for b in _an.analyze_page(page, 0) if "Totale" in b.text][0]
        assert not any("Revisione" in ln.text for ln in para.lines)
        assert para.align == "justify"
        old_lines = [(round(ln.baseline, 2), round(ln.bbox.x1, 1)) for ln in para.lines]
    s = _search.replace_all(d, "9.950,00", "9.960,00", _search.SearchOptions(case_sensitive=True))
    assert s.replaced == 1
    with d.lock:
        page = d.pdf[0]
        after_words = {(w[4], round(w[1])): w[:4] for w in page.get_text("words")}
        new_para = [b for b in _an.analyze_page(page, 0) if "Totale" in b.text][0]
    for key, r in before_words.items():
        if r[1] < para.bbox.y0 - 1:          # parole della tabella e dell'intestazione: invariate
            assert key in after_words, key
            assert abs(after_words[key][0] - r[0]) < 0.2, key
    new_lines = [(round(ln.baseline, 2), round(ln.bbox.x1, 1)) for ln in new_para.lines]
    assert [b for b, _x in new_lines] == [b for b, _x in old_lines]
    assert new_para.align == "justify"
    for (b0, x0), (b1, x1) in zip(old_lines[:-1], new_lines[:-1]):
        assert abs(x1 - x0) <= 0.6


@pytest.mark.parametrize("rule", [False, True])
def test_invoice_row_just_above_justified_paragraph(tmp_path, rule):
    """Riga di tabella (cella «1» a 140 pt) subito sopra un paragrafo giustificato, con o senza filetto:
    la sostituzione nel paragrafo non riscrive la cella e mantiene la giustificazione."""
    import pymupdf as _pm
    from foliant.api import Document as _Doc
    from foliant.modules.text_edit import analysis as _an
    from foliant.modules.text_edit import search as _search
    from foliant.modules.text_edit.layout import Composer
    from foliant.modules.text_edit.fonts import FontResolver
    from foliant.modules.text_edit.model import Run, Style
    carlito = system_font("truetype/crosextra/Carlito-Regular.ttf")
    import os
    fontfile = carlito if os.path.exists(carlito) else system_font("truetype/liberation/LiberationSans-Regular.ttf")
    doc = _pm.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="F1", fontfile=fontfile)
    f = _pm.Font(fontfile=fontfile)
    page.insert_text((58.2, 95), "Revisione e lucidatura", fontname="F1", fontsize=11)
    page.insert_text((58.2 + f.text_length("Revisione e lucidatura", 11) + 140, 95), "1", fontname="F1", fontsize=11)
    price = "€ 350,00"
    page.insert_text((569.5 - f.text_length(price, 11), 95), price, fontname="F1", fontsize=11)
    if rule:
        page.draw_line((56, 99), (570, 99), width=0.5)
    path = tmp_path / "riga.pdf"
    doc.save(str(path))
    d = _Doc.open(str(path))
    text = ("Totale documento: € 9.950,00 — il pagamento è dovuto entro trenta giorni dalla data della "
            "fattura. Pagamento mediante bonifico bancario sulle coordinate indicate in calce, salvo diversi "
            "accordi scritti tra le parti e fatte salve le condizioni generali di vendita indicate sul sito.")
    st = Style(font=f.name, size=11)
    from foliant.modules.text_edit import engine as _engine
    with d.change("Paragrafo") as pdf:
        _engine.add_text(pdf, 0, _pm.Point(56.8, 98.5), [[Run(text, st)]], width=512.7, align="justify",
                         leading=13.4)
    d._saved_state = d._state
    with d.lock:
        page = d.pdf[0]
        para = [b for b in _an.analyze_page(page, 0) if "Totale" in b.text][0]
        row = {w[4]: w[:4] for w in page.get_text("words") if w[3] < 100}
    assert not any("Revisione" in ln.text for ln in para.lines)
    assert para.align == "justify" and len(para.lines) >= 3
    old = [(round(ln.baseline, 2), round(ln.bbox.x1, 1)) for ln in para.lines]
    s = _search.replace_all(d, "9.950,00", "9.960,00", _search.SearchOptions(case_sensitive=True))
    assert s.replaced == 1
    with d.lock:
        page = d.pdf[0]
        new_para = [b for b in _an.analyze_page(page, 0) if "Totale" in b.text][0]
        row_after = {w[4]: w[:4] for w in page.get_text("words") if w[3] < 100}
    for word, r in row.items():
        assert word in row_after and abs(row_after[word][0] - r[0]) < 0.2 and abs(row_after[word][1] - r[1]) < 0.2
    new = [(round(ln.baseline, 2), round(ln.bbox.x1, 1)) for ln in new_para.lines]
    assert [b for b, _x in new] == [b for b, _x in old]
    assert new_para.align == "justify"
    for (_b0, x0), (_b1, x1) in zip(old[:-1], new[:-1]):
        assert abs(x1 - x0) <= 0.6
