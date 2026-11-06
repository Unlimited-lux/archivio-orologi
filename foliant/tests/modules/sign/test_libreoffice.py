"""F-1: commenti e campi modulo su PDF esportati da LibreOffice e firmati digitalmente.

LibreOffice scrive i valori della pagina con molte cifre decimali (MediaBox A4
«595.303937007874 841.889763779528»). Il motore li legge con precisione ridotta: se
il salvataggio incrementale riscrive la pagina per aggiungere una nota, i valori non
devono cambiare, altrimenti la pagina firmata risulta alterata (per Foliant, per
pyHanko e per gli altri lettori).
"""

from __future__ import annotations

import io
import re
import subprocess

import pytest

from foliant.api import Document
from foliant.core.platform import find_tool
from foliant.modules.annotations import engine as annot_engine
from foliant.modules.forms import engine as forms_engine
from foliant.modules.sign import digital as d

LO_MEDIABOX = b"[0 0 595.303937007874 841.889763779528]"


# --------------------------------------------------------------------------- #
# Documenti
# --------------------------------------------------------------------------- #


LO_WIDGET_RECT = b"[118.849133858268 769.290708661417 298.401574803150 783.088976377953]"


def _has_array(data: bytes, key: bytes, array: bytes) -> bool:
    """`data` contiene `key` seguito dall'array con gli stessi numeri scritti uguali (spazi liberi)."""
    numbers = rb"\s+".join(re.escape(n) for n in array.strip(b"[]").split())
    return re.search(re.escape(key) + rb"\s*\[\s*" + numbers + rb"\s*\]", data) is not None


def _lo_like_pdf(form: bool = False, inherited_rotation: int = 0) -> bytes:
    """PDF scritto come da LibreOffice: xref classica, MediaBox e CropBox con 15 cifre; con
    `form` anche un campo di testo con /Rect e colori con molte cifre; con
    `inherited_rotation` la rotazione è nel nodo /Pages ed ereditata dalla pagina."""
    content = b"BT /F1 24 Tf 72 760 Td (Contratto di vendita) Tj ET"
    acroform = b"/AcroForm<</Fields[6 0 R]/DR<</Font<</Helv 5 0 R>>>>/DA(/Helv 0 Tf 0 g)>>" if form else b""
    annots = b"/Annots[6 0 R]" if form else b""
    rotate = b"/Rotate %d" % inherited_rotation if inherited_rotation else b""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R" + acroform + b">>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1" + rotate + b">>",
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 5 0 R>>/ProcSet[/PDF/Text]>>/MediaBox" + LO_MEDIABOX
        + b"/CropBox[0.000000 0 595.303937007874 841.889763779528]/Contents 4 0 R/StructParents 0" + annots + b">>",
        b"<</Length %d>>\nstream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    if form:
        objs.append(b"<</Type/Annot/Subtype/Widget/F 4\n/Rect" + LO_WIDGET_RECT
                    + b"\n/FT/Tx/P 3 0 R/T(Acquirente)/DA(0 0 0 rg /Helv 10 Tf)"
                    + b"\n/MK<</BG[0.901960784313725 0.901960784313725 0.901960784313725]>>>>")
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


_CONTRACT_HTML = """<html><body><h1>Contratto di vendita</h1>
<p>Il venditore, Orologeria Verdi, cede all'acquirente un orologio da polso usato.</p>
<p>Prezzo concordato: 1.200 euro. Consegna entro dieci giorni.</p></body></html>"""

_FORM_HTML = """<html><body><h1>Modulo d'ordine</h1><form>
<p>Acquirente: <input type="text" name="Acquirente" size="30"></p></form></body></html>"""

_INVOICE_CSV = "Articolo;Quantità;Prezzo\nOrologio;1;1200\nCinturino;2;40\nTotale;;1280\n"


@pytest.fixture(scope="module")
def libreoffice(tmp_path_factory) -> dict[str, bytes]:
    """PDF esportati da LibreOffice: contratto (Writer), modulo con un campo (Writer), fattura (Calc)."""
    soffice = find_tool("soffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    out = tmp_path_factory.mktemp("libreoffice")
    (out / "contratto.html").write_text(_CONTRACT_HTML, encoding="utf-8")
    (out / "modulo.html").write_text(_FORM_HTML, encoding="utf-8")
    (out / "fattura.csv").write_text(_INVOICE_CSV, encoding="utf-8")
    profile = f"-env:UserInstallation=file://{out}/profilo"
    writer = 'pdf:writer_pdf_Export:{"ExportFormFields":{"type":"boolean","value":"true"}}'
    for flt, files in ((writer, ["contratto.html", "modulo.html"]), ("pdf:calc_pdf_Export", ["fattura.csv"])):
        subprocess.run([soffice, "--headless", profile, "--convert-to", flt, "--outdir", str(out),
                        *[str(out / f) for f in files]], capture_output=True, timeout=180)
    docs = {}
    for name in ("contratto", "modulo", "fattura"):
        pdf = out / f"{name}.pdf"
        if not pdf.exists():
            pytest.skip("conversione LibreOffice non riuscita")
        docs[name] = pdf.read_bytes()
    # il caso del collaudo: valori della pagina con più cifre di quante ne conservi il motore
    assert re.search(rb"/MediaBox\s*\[\s*0 0 595\.3039370\d* 841\.8897637\d*\s*\]", docs["contratto"])
    assert re.search(rb"/MediaBox\s*\[\s*0 0 595\.3039370\d* 841\.8897637\d*\s*\]", docs["fattura"])
    return docs


# --------------------------------------------------------------------------- #
# Operazioni come nell'applicazione
# --------------------------------------------------------------------------- #


def _add_note(signed: bytes, page: int = 0, text: str = "Nota del revisore") -> bytes:
    """Nota (annotazione Text) aggiunta come fa il modulo Annotazioni e salvataggio del documento firmato."""
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Aggiungi nota", kind="annotation") as pdf:
        annot_engine.create_note(pdf, page, (120, 120), text, annot_engine.Style(), "Revisore")
    assert doc.saves_incrementally
    return doc.save_bytes()


def _fill_field(signed: bytes, name: str, value: str) -> bytes:
    """Compilazione come nel modulo Moduli (valore e aspetto rigenerato) e salvataggio."""
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Compila", kind="form") as pdf:
        page = pdf[0]
        [xref] = [w.xref for w in page.widgets() if w.field_name == name]
        forms_engine.set_value(pdf, 0, xref, value)
    return doc.save_bytes()


def _sign(data: bytes, ident, **kw) -> bytes:
    return d.sign_pdf(data, ident[2], d.SignOptions(**kw))


# --------------------------------------------------------------------------- #
# Verifica indipendente con pyHanko (regole standard, senza la politica di Foliant)
# --------------------------------------------------------------------------- #


def _changed_existing(data: bytes) -> dict[int, set[str]]:
    """Oggetti già presenti riscritti dall'ultima revisione → chiavi diverse secondo
    l'uguaglianza esatta di pyHanko (numeri come Decimal, nessuna tolleranza)."""
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.reader import PdfFileReader

    r = PdfFileReader(io.BytesIO(data), strict=False)
    last = r.xrefs.total_revisions - 1
    old, new = r.get_historical_resolver(last - 1), r.get_historical_resolver(last)
    out = {}
    for ref in r.xrefs.explicit_refs_in_revision(last):
        if r.xrefs.get_historical_ref(ref, last - 1) is None:
            continue
        o, n = old(ref), new(ref)
        if isinstance(o, generic.DictionaryObject) and isinstance(n, generic.DictionaryObject):
            keys = set(o.keys()) | set(n.keys())
            out[ref.idnum] = {k for k in keys if k not in o or k not in n or o.raw_get(k) != n.raw_get(k)}
        else:
            out[ref.idnum] = {"*"} if o != n else set()
    return out


def _strict_comment_rule():
    """Regola minima per i commenti, da affiancare alle regole standard di pyHanko (che non
    trattano le annotazioni): nuove annotazioni non widget con le loro dipendenze nuove, e la
    pagina solo se, tolto /Annots, è identica (uguaglianza esatta) e /Annots si è solo allungato."""
    from pyhanko.pdf_utils import generic
    from pyhanko.sign.diff_analysis import ModificationLevel
    from pyhanko.sign.diff_analysis.rules_api import QualifiedWhitelistRule, ReferenceUpdate

    class StrictComment(QualifiedWhitelistRule):
        def apply_qualified(self, old, new):
            xc = old.reader.xrefs
            refs = list(new.explicit_refs_in_revision())
            fresh = {(r.idnum, r.generation): r for r in refs if xc.get_historical_ref(r, old.revision) is None}

            def closure(obj, acc):
                if isinstance(obj, generic.IndirectObject):
                    key = (obj.idnum, obj.generation)
                    if key in fresh and key not in acc:
                        acc[key] = fresh[key]
                        closure(obj.get_object(), acc)
                elif isinstance(obj, generic.DictionaryObject):
                    for k in obj.keys():
                        if k not in ("/P", "/Parent"):
                            closure(obj.raw_get(k), acc)
                elif isinstance(obj, generic.ArrayObject):
                    for i in range(len(obj)):
                        closure(obj.raw_get(i), acc)

            for ref in refs:
                if (ref.idnum, ref.generation) in fresh:
                    continue
                page_old, page_new = old(ref), new(ref)
                if not isinstance(page_new, generic.DictionaryObject) or page_new.get("/Type") != "/Page":
                    continue
                if set(page_old.keys()) - {"/Annots"} != set(page_new.keys()) - {"/Annots"}:
                    continue
                if any(page_old.raw_get(k) != page_new.raw_get(k) for k in page_new.keys() if k != "/Annots"):
                    continue
                def annots(page):
                    if "/Annots" not in page:
                        return []
                    arr = page["/Annots"]
                    return [arr.raw_get(i) for i in range(len(arr))]

                before, after = annots(page_old), annots(page_new)
                if after[:len(before)] != before:      # /Annots solo allungato
                    continue
                acc: dict = {}
                for a in after[len(before):]:
                    annot = a.get_object()
                    if annot.get("/Subtype") in (None, "/Widget") or "/FT" in annot:
                        break
                    closure(a, acc)
                else:
                    yield ModificationLevel.ANNOTATIONS, ReferenceUpdate(ref)
                    for r in acc.values():
                        yield ModificationLevel.ANNOTATIONS, ReferenceUpdate(r)

    return StrictComment()


def _pyhanko_review(data: bytes, roots) -> list[dict]:
    """Per ogni firma: esito di pyHanko con le regole standard (DEFAULT_DIFF_POLICY) e con le
    regole standard più la regola minima per i commenti."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.diff_analysis import DEFAULT_DIFF_POLICY, StandardDiffPolicy
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    strict = StandardDiffPolicy(global_rules=list(DEFAULT_DIFF_POLICY.global_rules) + [_strict_comment_rule()],
                                form_rule=DEFAULT_DIFF_POLICY.form_rule)
    trust = [c.to_asn1() for c in roots]
    out = []
    for emb in PdfFileReader(io.BytesIO(data), strict=False).embedded_signatures:
        res = {}
        for label, policy in (("standard", DEFAULT_DIFF_POLICY), ("commenti", strict)):
            st = validate_pdf_signature(emb, ValidationContext(trust_roots=trust, allow_fetching=False),
                                        diff_policy=policy)
            res[label] = st
        out.append(res)
    return out


def _assert_pyhanko_comment_allowed(data: bytes, roots, *, docmdp_ok: bool = True, last_only: bool = False) -> None:
    """Ogni firma è integra e attendibile per pyHanko; la nota è un'annotazione (livello
    ANNOTATIONS) per le regole standard più la regola minima per i commenti. Con `last_only`
    il livello si controlla solo per l'ultima firma: per una firma seguita da un'altra firma
    e da una nota, la regola standard di pyHanko sui nuovi campi firma rifiuta qualunque
    annotazione non widget comparsa sulla pagina (limite noto di pyHanko)."""
    results = _pyhanko_review(data, roots)
    for i, res in enumerate(results):
        std, com = res["standard"], res["commenti"]
        assert std.intact and std.valid and std.trusted
        assert std.coverage.name == "ENTIRE_REVISION"
        if last_only and i < len(results) - 1:
            continue
        assert com.modification_level is not None and com.modification_level.name == "ANNOTATIONS", (
            com.diff_result)
        assert com.docmdp_ok is docmdp_ok


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #


def test_lo_like_note_keeps_page_values_and_signature(id_a):
    """Senza LibreOffice: stessa struttura del file di Writer (MediaBox/CropBox con 15 cifre)."""
    signed = _sign(_lo_like_pdf(), id_a)
    assert d.verify_pdf(signed, trusted=[id_a[1]])[0].status == d.VALID
    out = _add_note(signed)
    assert out.startswith(signed)
    update = out[len(signed):]
    # la pagina riscritta conserva i valori originali, come testo
    assert _has_array(update, b"/MediaBox", LO_MEDIABOX) and _has_array(update, b"/CropBox", LO_MEDIABOX)
    assert b"595.30398" not in update and b"841.8898 " not in update
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 1"
    _assert_pyhanko_comment_allowed(out, [id_a[1]])


def test_lo_like_form_fill_keeps_widget_values(id_a):
    """Compilazione di un campo con /Rect e /MK con molte cifre: il widget riscritto conserva i
    valori non toccati e pyHanko (regole standard) la riconosce come compilazione del modulo."""
    signed = _sign(_lo_like_pdf(form=True), id_a)
    out = _fill_field(signed, "Acquirente", "Luigi Bianchi")
    assert out.startswith(signed)
    update = out[len(signed):]
    assert _has_array(update, b"/Rect", LO_WIDGET_RECT)
    assert b"0.901960784313725 0.901960784313725 0.901960784313725" in update
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: compilazione di campi modulo"
    [res] = _pyhanko_review(out, [id_a[1]])
    std = res["standard"]
    assert std.intact and std.valid and std.trusted and std.docmdp_ok
    assert std.modification_level.name == "FORM_FILLING"


@pytest.mark.parametrize("rotation", [90, 270])
def test_note_on_page_with_inherited_rotation(id_a, rotation):
    """La nota su una pagina che eredita /Rotate da /Pages non deve dare alla pagina un /Rotate
    proprio (il modulo Annotazioni toglie e rimette la rotazione mentre crea la nota)."""
    signed = _sign(_lo_like_pdf(inherited_rotation=rotation), id_a)
    out = _add_note(signed)
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 1"
    _assert_pyhanko_comment_allowed(out, [id_a[1]])
    import pymupdf

    with pymupdf.open(stream=out) as r:
        assert r[0].rotation == rotation and len(list(r[0].annots())) == 1


def test_lo_like_note_after_undo_redo(id_a):
    signed = _sign(_lo_like_pdf(), id_a)
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Aggiungi nota", kind="annotation") as pdf:
        annot_engine.create_note(pdf, 0, (120, 120), "Nota", annot_engine.Style(), "Revisore")
    doc.undo()
    doc.redo()
    out = doc.save_bytes()
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)


def _qpdf(tmp_path, data: bytes, *args: str) -> bytes:
    qpdf = find_tool("qpdf")
    if not qpdf:
        pytest.skip("qpdf non disponibile")
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    src.write_bytes(data)
    subprocess.run([qpdf, *args, str(src), str(dst)], check=True, capture_output=True, timeout=60)
    return dst.read_bytes()


def test_lo_like_object_stream_widget_keeps_values(id_a, tmp_path):
    """Widget dentro un object stream (scritto da qpdf, che conserva il testo dei numeri):
    il testo originale si legge dal flusso di oggetti."""
    data = _qpdf(tmp_path, _lo_like_pdf(form=True), "--object-streams=generate")
    assert b"/ObjStm" in data and not _has_array(data, b"/Rect", LO_WIDGET_RECT)   # compresso nel flusso
    signed = _sign(data, id_a)
    out = _fill_field(signed, "Acquirente", "Luigi Bianchi")
    assert _has_array(out[len(signed):], b"/Rect", LO_WIDGET_RECT)
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    [res] = _pyhanko_review(out, [id_a[1]])
    assert res["standard"].modification_level.name == "FORM_FILLING" and res["standard"].docmdp_ok


def test_lo_like_encrypted_note_and_fill_keep_values(id_a, tmp_path):
    """File cifrato (AES-256, senza password di apertura): i numeri si rimettono come nel file,
    le stringhe (cifrate) restano quelle scritte dal motore."""
    data = _qpdf(tmp_path, _lo_like_pdf(form=True), "--encrypt", "", "proprietario", "256", "--")
    signed = _sign(data, id_a)
    out = _add_note(signed)
    assert _has_array(out[len(signed):], b"/MediaBox", LO_MEDIABOX)
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    out2 = _fill_field(out, "Acquirente", "Luigi Bianchi")
    assert _has_array(out2[len(out):], b"/Rect", LO_WIDGET_RECT)
    import pymupdf

    with pymupdf.open(stream=out2) as r:
        assert {w.field_name: w.field_value for w in r[0].widgets()}["Acquirente"] == "Luigi Bianchi"
    [st] = d.verify_pdf(out2, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert "compilazione di campi modulo" in st.modifications


def test_writer_contract_note_keeps_signature(libreoffice, id_a, tmp_path):
    """Passi del collaudo: contratto di Writer, firma invisibile, nota, salvataggio, riapertura."""
    path = tmp_path / "contratto_firmato.pdf"
    path.write_bytes(_sign(libreoffice["contratto"], id_a))
    signed = path.read_bytes()
    assert d.verify_pdf(signed, trusted=[id_a[1]])[0].status == d.VALID
    doc = Document.open(path)
    with doc.change("Aggiungi nota", kind="annotation") as pdf:
        annot_engine.create_note(pdf, 0, (120, 120), "Da rivedere", annot_engine.Style(), "Revisore")
    doc.save()
    doc.close()
    out = path.read_bytes()
    assert out.startswith(signed)
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 1"
    _assert_pyhanko_comment_allowed(out, [id_a[1]])
    # una seconda nota in un altro salvataggio
    out2 = _add_note(out, text="Seconda nota")
    [st] = d.verify_pdf(out2, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: 2 annotazioni aggiunte a pagina 1"
    _assert_pyhanko_comment_allowed(out2, [id_a[1]])


def test_calc_invoice_note_keeps_signature(libreoffice, id_a):
    signed = _sign(libreoffice["fattura"], id_a)
    out = _add_note(signed)
    assert out.startswith(signed)
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert st.modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 1"
    _assert_pyhanko_comment_allowed(out, [id_a[1]])


def test_writer_two_signatures_then_note(libreoffice, id_a, id_b):
    s1 = _sign(libreoffice["contratto"], id_a, reason="Venditore")
    s2 = _sign(s1, id_b, reason="Acquirente")
    out = _add_note(s2)
    assert out.startswith(s2)
    sts = d.verify_pdf(out, trusted=[id_a[1], id_b[1]])
    assert [s.status for s in sts] == [d.VALID, d.VALID], [(s.reasons, s.modifications) for s in sts]
    assert "1 annotazione aggiunta a pagina 1" in sts[0].modifications
    assert "1 firma aggiunta" in sts[0].modifications
    assert sts[1].modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 1"
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    _assert_pyhanko_comment_allowed(out, [id_a[1], id_b[1]], last_only=True)


@pytest.mark.parametrize("order", ["firma-nota-firma", "firma-firma-nota", "cert3-firma-nota"])
def test_note_with_two_signatures_any_order(id_a, id_b, order):
    """Una nota prima o dopo la seconda firma non invalida la prima: pyHanko confronta la revisione
    firmata con ognuna delle successive e, se trova un nuovo campo firma, controlla tutte le
    annotazioni aggiunte alla pagina. Con la certificazione P=3 le note sono consentite."""
    data = _lo_like_pdf()
    if order == "firma-nota-firma":
        out = _sign(_add_note(_sign(data, id_a)), id_b)
    elif order == "firma-firma-nota":
        out = _add_note(_sign(_sign(data, id_a), id_b))
    else:
        out = _add_note(_sign(_sign(data, id_a, certify=True, docmdp_level=3), id_b))
    sts = d.verify_pdf(out, trusted=[id_a[1], id_b[1]])
    assert [s.status for s in sts] == [d.VALID, d.VALID], [(s.reasons, s.modifications) for s in sts]
    assert "1 annotazione aggiunta a pagina 1" in sts[0].modifications
    assert "1 firma aggiunta" in sts[0].modifications


def test_writer_certified_fill_forms_then_note_not_allowed(libreoffice, id_a):
    """Certificazione P=2 (solo compilazione e firme): una nota non è consentita, per Foliant e
    per pyHanko, ma per la regola e non per i valori della pagina."""
    signed = _sign(libreoffice["contratto"], id_a, certify=True, docmdp_level=2)
    out = _add_note(signed)
    assert list(_changed_existing(out).values()) == [{"/Annots"}]
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.INVALID
    assert st.intact
    assert st.modifications == "Modifiche non consentite: 1 annotazione aggiunta a pagina 1"
    _assert_pyhanko_comment_allowed(out, [id_a[1]], docmdp_ok=False)


def test_writer_form_fill_after_signature(libreoffice, id_a):
    signed = _sign(libreoffice["modulo"], id_a)
    out = _fill_field(signed, "Acquirente", "Luigi Bianchi")
    assert out.startswith(signed)
    import pymupdf

    with pymupdf.open(stream=out) as r:
        assert next(r[0].widgets()).field_value == "Luigi Bianchi"
    [st] = d.verify_pdf(out, trusted=[id_a[1]])
    assert st.status == d.VALID, (st.reasons, st.modifications)
    assert "compilazione di campi modulo" in st.modifications
    [res] = _pyhanko_review(out, [id_a[1]])
    std = res["standard"]
    assert std.intact and std.valid and std.trusted and std.docmdp_ok
    assert std.modification_level.name == "FORM_FILLING"
