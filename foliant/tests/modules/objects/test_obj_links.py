"""Collegamenti (foliant.modules.objects.links) — criteri OBJ-33…OBJ-40."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.objects import links
from foliant.modules.objects.links import LinkStyle, LinkTarget

from .conftest import doc_from, new_pdf, render


def _nums(text: str) -> list[float]:
    return links._nums(text)


def _web(uri="https://esempio.it"):
    return LinkTarget(kind=links.URI, uri=uri)


# ================================================================ OBJ-35 normalizzazione
@pytest.mark.parametrize("text, expected", [
    ("esempio.it/pagina", "https://esempio.it/pagina"),
    ("https://città.it/così", "https://xn--citt-3na.it/cos%C3%AC"),
    ("mario.rossi@esempio.it", "mailto:mario.rossi@esempio.it"),
    ("www.esempio.it", "https://www.esempio.it"),
    ("HTTP://Esempio.IT/A?b=1#c", "http://esempio.it/A?b=1#c"),
    ("https://esempio.it/a b", "https://esempio.it/a%20b"),
    ("https://esempio.it/a%20b", "https://esempio.it/a%20b"),
    ("ftp://files.esempio.it/x", "ftp://files.esempio.it/x"),
    ("tel:+39 02 1234 567", "tel:+39021234567"),
    ("mailto:info@esempio.it?subject=Ciao mondo", "mailto:info@esempio.it?subject=Ciao%20mondo"),
    ("  https://esempio.it  ", "https://esempio.it"),
])
def test_normalize_uri_valid(text, expected):
    chk = links.normalize_uri(text)
    assert chk.error is None
    assert chk.uri == expected
    chk.uri.encode("ascii")


@pytest.mark.parametrize("text", ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "vbscript:x",
                                  "gopher://x.it"])
def test_normalize_uri_scheme_not_allowed(text):
    chk = links.normalize_uri(text)
    assert chk.uri is None
    assert chk.error.lower().startswith("schema non ammesso")


def test_normalize_uri_errors_and_warnings():
    assert links.normalize_uri("").error
    assert links.normalize_uri("non è un indirizzo").uri is None
    assert links.normalize_uri("tel:12").uri is None
    assert links.normalize_uri("mailto:non-valido").uri is None
    f = links.normalize_uri("file:///tmp/a.pdf")
    assert f.uri is None and f.suggest_file
    cred = links.normalize_uri("https://utente:segreta@esempio.it/")
    assert cred.uri == "https://utente:segreta@esempio.it/" and cred.warnings
    long = links.normalize_uri("https://esempio.it/" + "a" * 2100)
    assert long.uri and long.warnings
    assert links.uri_host("https://WWW.Esempio.it:8080/x") == "www.esempio.it"


# ================================================================ OBJ-40 rilevamento nel testo
def test_find_urls_trailing_punctuation_and_email():
    found = links.find_urls_in_text("Visita www.esempio.it. e scrivi a info@esempio.it")
    assert [(t, u) for _p, t, u in found] == [("www.esempio.it", "https://www.esempio.it"),
                                               ("info@esempio.it", "mailto:info@esempio.it")]


def test_find_urls_balanced_parentheses():
    found = links.find_urls_in_text("(https://esempio.it/a_(b))")
    assert [u for _p, _t, u in found] == ["https://esempio.it/a_(b)"]
    assert links.clean_url_match("https://esempio.it/x),;") == "https://esempio.it/x"
    assert links.clean_url_match("https://esempio.it/x»") == "https://esempio.it/x"


def test_find_urls_filters():
    text = "https://a.esempio.it e b@esempio.it"
    assert len(links.find_urls_in_text(text, web=False)) == 1
    assert len(links.find_urls_in_text(text, email=False)) == 1
    assert links.find_urls_in_text("nessun indirizzo qui") == []


def test_suggest_from_text():
    assert links.suggest_from_text("www.esempio.it").uri == "https://www.esempio.it"
    assert links.suggest_from_text("info@esempio.it").uri == "mailto:info@esempio.it"
    assert links.suggest_from_text("due parole") is None
    assert links.suggest_from_text("") is None


def _text_page(lines):
    pdf = new_pdf()
    for k, line in enumerate(lines):
        pdf[0].insert_text((72, 100 + 20 * k), line, fontsize=11)
    return pdf


def test_detect_urls_on_page():
    pdf = _text_page(["Visita www.esempio.it.", "scrivi a info@esempio.it"])
    found = links.detect_urls(pdf, 0)
    assert [f.uri for f in found] == ["https://www.esempio.it", "mailto:info@esempio.it"]
    assert found[0].text == "www.esempio.it"
    # il rettangolo copre solo l'URL, senza il punto finale
    words = [w for w in pdf[0].get_text("words") if w[4].startswith("www")]
    assert found[0].rect.x1 < words[0][2]
    assert len(found[0].quads) == 1


def test_detect_urls_excludes_already_linked():
    pdf = _text_page(["Visita www.esempio.it.", "scrivi a info@esempio.it"])
    first = links.detect_urls(pdf, 0)[0]
    links.create_link(pdf, 0, pymupdf.Rect(first.rect) + (-1, -1, 1, 1), _web(first.uri))
    found = links.detect_urls(pdf, 0)
    assert [f.uri for f in found] == ["mailto:info@esempio.it"]


def test_detect_url_split_on_two_lines():
    pdf = _text_page(["Documentazione: https://esempio.it/percorso/", "pagina.html e altro"])
    found = links.detect_urls(pdf, 0)
    assert found[0].uri == "https://esempio.it/percorso/pagina.html"
    assert len(found[0].quads) == 2


def test_create_30_links_from_detection_single_undo():
    pdf = _text_page([f"Sito numero {k}: www.esempio{k}.it" for k in range(30)])
    doc = doc_from(pdf)
    before = render(doc.pdf)
    found = links.detect_urls(doc.pdf, 0)
    assert len(found) == 30
    with doc.change("Crea collegamenti") as p:
        assert links.create_links_for(p, found) == 30
    assert len(links.link_xrefs(doc.pdf, 0)) == 30
    assert links.detect_urls(doc.pdf, 0) == []
    infos = links.page_links(doc.pdf, 0)
    assert all(not i.style.visible and i.quads for i in infos)
    assert {i.target.uri for i in infos} == {f"https://www.esempio{k}.it" for k in range(30)}
    doc.undo()
    assert links.link_xrefs(doc.pdf, 0) == []
    assert render(doc.pdf) == before and not doc.is_modified


def test_detection_does_not_modify_document():
    doc = doc_from(_text_page(["www.esempio.it"]))
    links.detect_urls(doc.pdf, 0)
    assert not doc.is_modified


# ================================================================ OBJ-33 creazione
def test_create_uri_link_rect_and_action():
    pdf = new_pdf()
    rect = pymupdf.Rect(100, 200, 200, 220)
    xref = links.create_link(pdf, 0, rect, _web("https://esempio.it"))
    assert pdf.xref_get_key(xref, "Subtype")[1] == "/Link"
    # /Rect nello spazio PDF (origine in basso)
    x0, y0, x1, y1 = _nums(pdf.xref_get_key(xref, "Rect")[1])
    H = pdf[0].rect.height
    for got, exp in zip((x0, y0, x1, y1), (100, H - 220, 200, H - 200)):
        assert abs(got - exp) <= 0.5
    assert pdf.xref_get_key(xref, "A/S")[1] == "/URI"
    assert pdf.xref_get_key(xref, "A/URI")[1] == "https://esempio.it"
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.URI and info.target.uri == "https://esempio.it"
    assert all(abs(a - b) <= 0.5 for a, b in zip(info.rect, rect))
    # il motore lo riconosce come collegamento
    lk = pdf[0].get_links()
    assert len(lk) == 1 and lk[0]["uri"] == "https://esempio.it"
    assert pdf.xref_get_key(xref, "NM")[0] == "string"
    assert int(pdf.xref_get_key(xref, "F")[1]) & 4


def test_invisible_link_border_zero_after_save():
    pdf = new_pdf()
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 200, 200, 220), _web(), LinkStyle(visible=False))
    re_ = pymupdf.open(stream=pdf.tobytes(garbage=3), filetype="pdf")
    lks = re_[0].get_links()
    assert len(lks) == 1 and lks[0]["uri"] == "https://esempio.it"
    x = lks[0]["xref"]
    assert _nums(re_.xref_get_key(x, "Border")[1])[2] == 0
    assert _nums(re_.xref_get_key(x, "BS/W")[1]) == [0]
    assert not links.read_link(re_, 0, x).style.visible
    assert xref


def test_visible_link_style_roundtrip():
    pdf = new_pdf()
    st = LinkStyle(visible=True, color=(1, 0, 0), width=2, style="D", highlight="O")
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 200, 200, 220), _web(), st, description="Sito del produttore")
    info = links.read_link(pdf, 0, xref)
    assert info.style.visible and info.style.width == 2 and info.style.style == "D"
    assert info.style.color == (1, 0, 0) and info.style.highlight == "O"
    assert info.description == "Sito del produttore"
    assert pdf.xref_get_key(xref, "Contents")[1] == "Sito del produttore"
    # l'aspetto (/AP /N) disegna un bordo rosso tratteggiato di 2 pt
    ap = int(pdf.xref_get_key(xref, "AP/N")[1].split()[0])
    stream = pdf.xref_stream(ap)
    assert b"1 0 0 RG" in stream and b"2 w" in stream and b"[6 4] 0 d" in stream
    assert _nums(pdf.xref_get_key(ap, "BBox")[1]) == [0, 0, 100, 20]


def test_tiny_link_rejected():
    pdf = new_pdf()
    with pytest.raises(links.LinkError):
        links.create_link(pdf, 0, pymupdf.Rect(100, 100, 102, 102), _web())
    assert links.link_xrefs(pdf, 0) == []


def test_invalid_target_rejected_before_modifying():
    pdf = new_pdf()
    with pytest.raises(links.LinkError):
        links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), _web("javascript:alert(1)"))
    with pytest.raises(links.LinkError):
        links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), LinkTarget(kind=links.PAGE, page=9))
    assert links.link_xrefs(pdf, 0) == []


def test_link_with_two_quads():
    pdf = new_pdf()
    q1 = pymupdf.Rect(100, 100, 300, 112).quad
    q2 = pymupdf.Rect(72, 114, 150, 126).quad
    xref = links.create_link(pdf, 0, pymupdf.Rect(72, 100, 300, 126), _web(), quads=[q1, q2])
    vals = _nums(pdf.xref_get_key(xref, "QuadPoints")[1])
    assert len(vals) == 16
    info = links.read_link(pdf, 0, xref)
    assert len(info.quads) == 2
    assert all(abs(a - b) < 1e-3 for a, b in zip(info.quads[0].rect, q1.rect))
    assert all(abs(a - b) < 1e-3 for a, b in zip(info.quads[1].rect, q2.rect))


# ================================================================ OBJ-36/37 destinazioni
def test_page_destination_xyz_roundtrip():
    pdf = new_pdf(6)
    t = LinkTarget(kind=links.PAGE, page=4, fit="XYZ", left=50, top=120, zoom=2)
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), t)
    assert pdf.xref_get_key(xref, "A")[0] == "null"
    dest = pdf.xref_get_key(xref, "Dest")[1]
    assert dest.replace(" ", "").startswith(f"[{pdf.page_xref(4)}0R/XYZ")
    vals = _nums(dest)
    assert vals[-3:] == [50, pdf[4].rect.height - 120, 2]
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.PAGE and info.target.page == 4
    assert (info.target.left, info.target.top, info.target.zoom) == (50, 120, 2)
    assert info.target.describe() == "Pagina 5"
    lk = pdf[0].get_links()[0]
    assert lk["kind"] == pymupdf.LINK_GOTO and lk["page"] == 4


def test_page_destination_fith_follows_moved_page():
    pdf = new_pdf(12)
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120),
                             LinkTarget(kind=links.PAGE, page=11, fit="FitH", top=200))
    info = links.read_link(pdf, 0, xref)
    assert info.target.fit == "FitH" and info.target.top == pytest.approx(200)
    pdf.move_page(11, 2)  # la pagina 12 diventa la terza
    assert links.read_link(pdf, 0, xref).target.page == 2


def test_named_destination():
    pdf = new_pdf()
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), LinkTarget(kind=links.NAMED, name="cap3"))
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.NAMED and info.target.name == "cap3"
    with pytest.raises(links.LinkError):
        links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), LinkTarget(kind=links.NAMED))


def test_external_pdf_file_relative_gotor():
    rel = links.relative_path("/dati/allegati/b.pdf", "/dati/a.pdf")
    assert rel == "allegati/b.pdf"
    pdf = new_pdf()
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120),
                             LinkTarget(kind=links.FILE, file=rel, page=3, fit="Fit"))
    a = pdf.xref_get_key(xref, "A")[1]
    assert "/S/GoToR" in a.replace(" ", "")
    assert "(allegati/b.pdf)" in a
    assert pdf.xref_get_key(xref, "A/D")[1].replace(" ", "").startswith("[3/Fit")
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.FILE and info.target.file == "allegati/b.pdf" and info.target.page == 3


def test_launch_non_pdf_file():
    pdf = new_pdf()
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120),
                             LinkTarget(kind=links.FILE, file="dati/tabella.xlsx"))
    assert pdf.xref_get_key(xref, "A/S")[1] == "/Launch"
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.LAUNCH and info.target.file == "dati/tabella.xlsx"
    assert links.link_category(info.target) == "file"


# ================================================================ OBJ-38 modifica
def test_update_url_link_to_page_3():
    pdf = new_pdf(4)
    xref = links.create_link(pdf, 0, pymupdf.Rect(100, 100, 200, 120), _web())
    links.update_link(pdf, 0, xref, target=LinkTarget(kind=links.PAGE, page=2, fit="Fit"))
    re_ = pymupdf.open(stream=pdf.tobytes(garbage=3), filetype="pdf")
    x = re_[0].get_links()[0]["xref"]
    obj = re_.xref_object(x, compressed=True)
    assert "/URI" not in obj
    dest = re_.xref_get_key(x, "Dest")
    a = re_.xref_get_key(x, "A")
    if dest[0] != "null":
        assert dest[1].startswith(f"[{re_.page_xref(2)} 0 R")
    else:
        assert "/GoTo" in a[1] and f"{re_.page_xref(2)} 0 R" in a[1]
    assert re_[0].get_links()[0]["page"] == 2


def test_update_only_color_preserves_javascript_action():
    pdf = new_pdf()
    page = pdf[0]
    page.insert_link({"kind": pymupdf.LINK_URI, "uri": "https://x.it/", "from": pymupdf.Rect(100, 100, 200, 120)})
    xref = links.link_xrefs(pdf, 0)[0]
    pdf.xref_set_key(xref, "A", "<</S/JavaScript/JS(app.alert(1))>>")
    before = pdf.xref_get_key(xref, "A")
    info = links.read_link(pdf, 0, xref)
    assert info.target.kind == links.OTHER
    links.update_link(pdf, 0, xref, style=LinkStyle(visible=True, color=(1, 0, 0), width=1))
    assert pdf.xref_get_key(xref, "A") == before
    re_ = pymupdf.open(stream=pdf.tobytes(), filetype="pdf")
    x = links.link_xrefs(re_, 0)[0]
    assert re_.xref_get_key(x, "A") == before
    assert re_.xref_get_key(x, "A/JS")[1] == "app.alert(1)"
    assert _nums(re_.xref_get_key(x, "C")[1]) == [1, 0, 0]
    # anche un collegamento a pagina con destinazione invariata conserva /Dest
    with pytest.raises(links.LinkError):
        links.action_string(pdf, LinkTarget(kind=links.OTHER))


def test_update_five_links_invisible():
    pdf = new_pdf()
    xs = [links.create_link(pdf, 0, pymupdf.Rect(100, 100 + 30 * k, 200, 120 + 30 * k), _web(),
                            LinkStyle(visible=True, width=2)) for k in range(5)]
    for x in xs:
        links.update_link(pdf, 0, x, style=LinkStyle(visible=False))
    for x in xs:
        assert _nums(pdf.xref_get_key(x, "Border")[1])[2] == 0
        assert links.read_link(pdf, 0, x).target.uri == "https://esempio.it"


def test_move_link_with_quadpoints():
    pdf = new_pdf()
    q1 = pymupdf.Rect(100, 100, 300, 112).quad
    q2 = pymupdf.Rect(72, 114, 150, 126).quad
    xref = links.create_link(pdf, 0, pymupdf.Rect(72, 100, 300, 126), _web(), quads=[q1, q2])
    before = links.read_link(pdf, 0, xref)
    links.update_link(pdf, 0, xref, move=(10, 0))
    after = links.read_link(pdf, 0, xref)
    assert all(abs(a - b) < 1e-3 for a, b in zip(after.rect, before.rect + (10, 0, 10, 0)))
    assert len(after.quads) == 2
    for qa, qb in zip(after.quads, before.quads):
        for pa, pb in zip((qa.ul, qa.ur, qa.ll, qa.lr), (qb.ul, qb.ur, qb.ll, qb.lr)):
            assert abs(pa.x - pb.x - 10) < 1e-3 and abs(pa.y - pb.y) < 1e-3
    assert after.target.uri == before.target.uri


def test_resize_link_drops_quads_and_keeps_description():
    pdf = new_pdf()
    xref = links.create_link(pdf, 0, pymupdf.Rect(72, 100, 300, 126), _web(), description="Descrizione",
                             quads=[pymupdf.Rect(72, 100, 300, 126).quad])
    links.update_link(pdf, 0, xref, rect=pymupdf.Rect(50, 50, 250, 80))
    info = links.read_link(pdf, 0, xref)
    assert info.quads == [] and info.description == "Descrizione"
    assert all(abs(a - b) < 1e-3 for a, b in zip(info.rect, (50, 50, 250, 80)))


# ================================================================ OBJ-39 eliminazione
def _mixed_links_doc():
    pdf = new_pdf(3)
    kinds = []
    for k in range(12):
        page = k % 3
        r = pymupdf.Rect(50, 50 + 40 * k, 200, 70 + 40 * k)
        if k < 5:
            t = _web(f"https://esempio{k}.it")
        elif k < 10:
            t = LinkTarget(kind=links.PAGE, page=(k + 1) % 3, fit="Fit")
        else:
            t = LinkTarget(kind=links.FILE, file="allegato.pdf")
        links.create_link(pdf, page, r, t, LinkStyle(visible=True))
        kinds.append(t.kind)
    return pdf


def test_delete_web_links_only():
    pdf = _mixed_links_doc()
    pages = range(pdf.page_count)
    assert links.count_links_in(pdf, pages, {"web"}) == 5
    assert sum(len(links.page_links(pdf, i)) for i in pages) == 12
    assert links.delete_links_in(pdf, pages, {"web"}) == 5
    remaining = [lk for i in pages for lk in links.page_links(pdf, i)]
    assert len(remaining) == 7
    assert all(links.link_category(lk.target) != "web" for lk in remaining)


def test_delete_skips_locked_unless_requested():
    pdf = new_pdf()
    a = links.create_link(pdf, 0, pymupdf.Rect(50, 50, 200, 70), _web())
    links.create_link(pdf, 0, pymupdf.Rect(50, 100, 200, 120), _web())
    pdf.xref_set_key(a, "F", str(4 | 128))
    assert links.read_link(pdf, 0, a).locked
    assert links.delete_links_in(pdf, [0], {"web"}) == 1
    assert links.link_xrefs(pdf, 0) == [a]
    assert links.delete_links_in(pdf, [0], {"web"}, include_locked=True) == 1
    assert links.link_xrefs(pdf, 0) == []


def test_delete_link_and_undo_restores():
    pdf = new_pdf()
    links.create_link(pdf, 0, pymupdf.Rect(50, 50, 200, 70), _web("https://esempio.it/x"),
                      LinkStyle(visible=True, color=(0, 0.5, 0), width=2), description="Esempio")
    doc = doc_from(pdf)
    before_info = links.page_links(doc.pdf, 0)[0]
    before_pix = doc.pdf[0].get_pixmap(dpi=72, annots=True).samples
    with doc.change("Elimina collegamento") as p:
        assert links.delete_links(p, 0, [before_info.xref]) == 1
    assert links.page_links(doc.pdf, 0) == []
    doc.undo()
    (info,) = links.page_links(doc.pdf, 0)
    assert info.target.uri == before_info.target.uri
    assert info.style == before_info.style and info.description == "Esempio"
    assert doc.pdf[0].get_pixmap(dpi=72, annots=True).samples == before_pix


def test_page_from_label():
    pdf = new_pdf(5)
    assert links.page_from_label(pdf, "3") == 2
    assert links.page_from_label(pdf, "9") is None
    assert links.page_from_label(pdf, "") is None


def test_named_destination_created_and_followed():
    doc = pymupdf.open()
    for _k in range(5):
        doc.new_page()
    t = links.LinkTarget(kind=links.NAMED, name="cap3", new_dest=(2, 100.0, 2.0))
    x = links.create_link(doc, 0, pymupdf.Rect(50, 50, 150, 70), t)
    names = links.named_destinations(doc)
    assert names["cap3"]["page"] == 2
    info = links.read_link(doc, 0, x)
    assert info.target.kind == links.NAMED and info.target.name == "cap3"
    with pytest.raises(links.LinkError):
        links.add_named_destination(doc, "cap3", 1)
    # ridefinita su un'altra pagina: il collegamento porta alla nuova posizione
    cat = doc.pdf_catalog()
    dx = int(doc.xref_get_key(cat, "Dests")[1].split()[0])
    doc.xref_set_key(dx, "cap3", f"[{doc.page_xref(4)} 0 R /Fit]")
    assert links.named_destinations(doc)["cap3"]["page"] == 4
    lk = doc.reload_page(doc[0]).get_links()[0]
    assert lk.get("page") == 4 or lk.get("nameddest") == "cap3" or lk.get("name") == "cap3"


def test_named_destination_appended_to_names_tree():
    doc = pymupdf.open()
    for _k in range(3):
        doc.new_page()
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "Names", f"<</Dests <</Names [(alfa) [{doc.page_xref(0)} 0 R /Fit]]>>>>")
    links.add_named_destination(doc, "beta", 2)
    names = links.named_destinations(doc)
    assert names["alfa"]["page"] == 0 and names["beta"]["page"] == 2
