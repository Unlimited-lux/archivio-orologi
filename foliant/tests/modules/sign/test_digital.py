"""Test della firma digitale PAdES (SIG-08, 10, 11, 12, 13, 15, 17, 19, 21)."""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta, timezone

import pymupdf
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12

from foliant.modules.sign import digital as d
from tests.modules.sign.conftest import PASSWORD


def _byte_ranges(data: bytes):
    return [tuple(int(x) for x in m.groups())
            for m in re.finditer(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]", data)]


def _add_annotation(data: bytes, page_index: int = 0) -> bytes:
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.generic import NameObject as N
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    w = IncrementalPdfFileWriter(io.BytesIO(data))
    page_ref = w.find_page_for_modification(page_index)[0]
    page = page_ref.get_object()
    annot = generic.DictionaryObject({
        N("/Type"): N("/Annot"), N("/Subtype"): N("/Text"),
        N("/Rect"): generic.ArrayObject([generic.FloatObject(v) for v in (100, 100, 120, 120)]),
        N("/Contents"): generic.TextStringObject("Commento"),
    })
    aref = w.add_object(annot)
    if "/Annots" in page:
        annots = page["/Annots"]
        annots.append(aref)
        w.update_container(annots)
    else:
        page[N("/Annots")] = generic.ArrayObject([aref])
    w.update_container(page)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _replace_content(data: bytes, page_index: int = 0) -> bytes:
    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.generic import NameObject as N
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    w = IncrementalPdfFileWriter(io.BytesIO(data))
    page_ref = w.find_page_for_modification(page_index)[0]
    page = page_ref.get_object()
    stream = generic.StreamObject(stream_data=b"BT /helv 30 Tf 100 100 Td (FALSO) Tj ET")
    page[N("/Contents")] = w.add_object(stream)
    w.update_container(page)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Firma e verifica di base
# --------------------------------------------------------------------------- #


def test_sign_and_verify_trusted_valid(pdf_bytes, id_a):
    _p, info, did = id_a
    signed = d.sign_pdf(pdf_bytes, did, d.SignOptions(reason="Approvo", location="Milano", contact="m@x.it",
                                                      rect=(72, 600, 272, 660)))
    [st] = d.verify_pdf(signed, trusted=[info])
    assert st.status == d.VALID and st.label == "Valida"
    assert st.signer == "Mario Rossi"
    assert st.kind == "approvazione"
    assert st.pades_level == "B-B"
    assert st.reason == "Approvo" and st.location == "Milano" and st.contact == "m@x.it"
    assert st.time_source == "dichiarata"
    assert st.modifications == "Nessuna modifica"
    assert st.intact and st.crypto_ok and st.trusted
    assert st.page == 0 and not st.invisible
    assert st.rect == pytest.approx((72, 600, 272, 660))
    assert b"/ETSI.CAdES.detached" in signed
    assert b"/SigFlags 3" in signed
    assert d.summary([st], []) == "Firmato: tutte le firme sono valide"


def test_untrusted_is_indeterminate_with_integrity(pdf_bytes, id_a):
    _p, _info, did = id_a
    signed = d.sign_pdf(pdf_bytes, did, d.SignOptions())
    [st] = d.verify_pdf(signed)
    assert st.status == d.INDETERMINATE and st.label == "Indeterminata"
    assert "Identità del firmatario non attendibile" in st.reasons
    assert any("Integrità confermata" in r for r in st.reasons)
    assert st.intact and st.crypto_ok and not st.trusted


def test_incremental_prefix_and_byterange(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    assert signed.startswith(pdf_bytes)
    [(a, b, c, n)] = _byte_ranges(signed[len(pdf_bytes):])
    assert a == 0 and c + n == len(signed)
    contents = signed[b:c]
    assert contents.startswith(b"<") and contents.endswith(b">")
    assert re.fullmatch(rb"<[0-9a-fA-F]+>", contents)
    assert signed[b - 10:b].rstrip().endswith(b"/Contents")


def test_invisible_signature_zero_rect(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    tail = signed[len(pdf_bytes):]
    assert re.search(rb"/Rect\s*\[\s*0\s+0\s+0\s+0\s*\]", tail)
    [f] = d.signature_fields(signed)
    assert f.signed and f.invisible and f.name == "Firma1"
    [st] = d.verify_pdf(signed, trusted=[id_a[1]])
    assert st.invisible


def test_visible_on_rotated_page(rotated_pdf_bytes, id_a):
    opts = d.SignOptions(rect=(300, 100, 380, 300), reason="Rotazione",
                         appearance={"layout": "testo", "show": ["nome", "data", "motivo", "etichette"]})
    signed = d.sign_pdf(rotated_pdf_bytes, id_a[2], opts)
    [st] = d.verify_pdf(signed, trusted=[id_a[1]])
    assert st.status == d.VALID
    assert re.search(rb"/Matrix\s*\[\s*0\s+1\s+-1\s+0", signed[len(rotated_pdf_bytes):])
    doc = pymupdf.open(stream=signed, filetype="pdf")
    try:
        text = doc[0].get_text()
        assert "Mario Rossi" in text or "Firmato" in text
    finally:
        doc.close()


def test_graphic_text_appearance_contains_texts_and_image(pdf_bytes, id_a):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).line((10, 80, 100, 20, 200, 70, 290, 30), fill=(0, 0, 150, 255), width=6)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    opts = d.SignOptions(rect=(72, 600, 372, 700), reason="Approvo",
                         appearance={"layout": "grafica_testo", "image_png": buf.getvalue(),
                                     "show": ["nome", "data", "motivo"]})
    signed, warnings = d.sign_pdf_ex(pdf_bytes, id_a[2], opts)
    doc = pymupdf.open(stream=signed, filetype="pdf")
    try:
        text = doc[0].get_text()
        assert "Mario Rossi" in text and "Approvo" in text
        assert datetime.now().strftime("%d/%m/%Y") in text
    finally:
        doc.close()
    assert b"/SMask" in signed[len(pdf_bytes):]
    assert not any("troncato" in w for w in warnings)


def test_long_text_truncated_with_warning(pdf_bytes, id_a):
    long_reason = "Motivo " + "molto " * 15
    opts = d.SignOptions(rect=(72, 600, 222, 650), reason=long_reason,
                         appearance={"layout": "testo", "show": ["nome", "data", "motivo", "dn", "etichette"]})
    signed, warnings = d.sign_pdf_ex(pdf_bytes, id_a[2], opts)
    assert any("troncato" in w for w in warnings)
    doc = pymupdf.open(stream=signed, filetype="pdf")
    try:
        for b in doc[0].get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for span in line["spans"]:
                    if "Motivo" in span["text"]:
                        x0, y0, x1, y1 = span["bbox"]
                        assert x1 <= 222.5 and x0 >= 71.5
    finally:
        doc.close()
    assert d.appearance_warnings(["x" * 80], 150, 50)
    assert any("piccolo" in w for w in d.appearance_warnings(["Mario"], 30, 10))


# --------------------------------------------------------------------------- #
# Modifiche successive
# --------------------------------------------------------------------------- #


def test_altered_byte_is_invalid(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    marker = b"<506167696e612031>"  # "Pagina 1"
    pos = signed.index(marker)
    tampered = signed[:pos + 16] + b"2" + signed[pos + 17:]
    assert len(tampered) == len(signed) and tampered != signed
    [st] = d.verify_pdf(tampered, trusted=[id_a[1]])
    assert st.status == d.INVALID
    assert "Il documento è stato alterato dopo la firma" in st.reasons


def test_annotation_added_is_allowed(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    updated = _add_annotation(signed, 1)
    assert updated.startswith(signed)
    [st] = d.verify_pdf(updated, trusted=[id_a[1]])
    assert st.status == d.VALID
    assert st.modifications == "Modifiche consentite: 1 annotazione aggiunta a pagina 2"
    assert d.signed_revision(updated, st) == signed


def test_annotation_added_with_pymupdf_is_allowed(pdf_bytes, id_a, tmp_path):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    path = tmp_path / "firmato.pdf"
    path.write_bytes(signed)
    doc = pymupdf.open(str(path))
    doc[0].add_text_annot((100, 100), "Nota")
    doc.save(str(path), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    updated = path.read_bytes()
    assert updated.startswith(signed)
    [st] = d.verify_pdf(updated, trusted=[id_a[1]])
    assert st.status == d.VALID
    assert "1 annotazione aggiunta a pagina 1" in st.modifications


def test_content_change_is_not_allowed(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    updated = _replace_content(signed, 0)
    [st] = d.verify_pdf(updated, trusted=[id_a[1]])
    assert st.status == d.INVALID
    assert st.modifications == "Modifiche non consentite: contenuto della pagina 1"
    assert "Versione firmata integra" in st.reasons
    assert st.intact
    assert d.signed_revision(updated, st) == signed
    assert d.summary([st], []) == "Almeno una firma presenta problemi"


# --------------------------------------------------------------------------- #
# Certificati e password
# --------------------------------------------------------------------------- #


def test_wrong_password(id_a):
    with pytest.raises(d.WrongCertificatePassword) as exc:
        d.load_pkcs12(id_a[0], "sbagliata")
    assert str(exc.value) == "Password errata"


def test_not_pkcs12(tmp_path):
    p = tmp_path / "finto.p12"
    p.write_bytes(b"non sono un pkcs12")
    with pytest.raises(d.SignError) as exc:
        d.load_pkcs12(p, "x")
    assert not isinstance(exc.value, d.WrongCertificatePassword)
    assert "PKCS#12" in str(exc.value)


def test_expired_certificate_blocked(pdf_bytes, expired_id):
    path, info = expired_id
    did = d.load_pkcs12(path, PASSWORD)
    with pytest.raises(d.CertificateNotUsable) as exc:
        d.sign_pdf(pdf_bytes, did, d.SignOptions())
    assert info.not_after.strftime("%d/%m/%Y") in str(exc.value)
    assert "10/01/2021" in str(exc.value)
    # opzione avanzata per i test: consente la firma con avviso
    warnings = d.check_usable(did, allow_expired=True)
    assert any("scaduto" in w for w in warnings)


def test_create_self_signed_properties(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "PKCS12_KDF_ROUNDS", 4096)
    path = tmp_path / "nuovo.p12"
    info = d.create_self_signed(path, "Robusta#2026", common_name="Zoë Ünal", organization="Orologeria",
                                unit="Vendite", email="zoe@example.org", country="it", years=5)
    blob = path.read_bytes()
    key, cert, _extra = pkcs12.load_key_and_certificates(blob, b"Robusta#2026")
    assert key is not None
    assert cert.issuer == cert.subject
    assert cert.serial_number.bit_length() >= 64
    years = (cert.not_valid_after_utc - cert.not_valid_before_utc).days / 365.25
    assert 4.95 < years < 5.05
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.digital_signature and ku.content_commitment
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.RFC822Name) == ["zoe@example.org"]
    cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
    assert info.subject == "Zoë Ünal" and info.self_signed and info.email == "zoe@example.org"
    assert "digitalSignature" in info.key_usage and "nonRepudiation" in info.key_usage
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert b"Robusta#2026" not in blob
    did = d.load_pkcs12(path, "Robusta#2026")
    assert did.info.sha256 == info.sha256
    # esportazione del certificato pubblico
    d.export_certificate(info, tmp_path / "pub.pem", "pem")
    d.export_certificate(did, tmp_path / "pub.cer", "der")
    assert (tmp_path / "pub.pem").read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
    assert x509.load_der_x509_certificate((tmp_path / "pub.cer").read_bytes()) == cert


def test_create_self_signed_validation(tmp_path):
    with pytest.raises(d.SignError):
        d.create_self_signed(tmp_path / "a.p12", "x" * 10, common_name="A", country="ITA")
    with pytest.raises(d.SignError):
        d.create_self_signed(tmp_path / "a.p12", "x" * 10, common_name="  ")
    assert not (tmp_path / "a.p12").exists()


def test_password_strength():
    assert d.password_strength("abc")[0] == 0
    assert d.password_strength("password")[0] == 0
    assert d.password_strength("12345678")[0] == 0
    score, label = d.password_strength("Orologio-Verdi#2026!")
    assert score >= 3 and label


# --------------------------------------------------------------------------- #
# Firme multiple e certificazione
# --------------------------------------------------------------------------- #


def test_multiple_signatures(pdf_bytes, id_a, id_b):
    s1 = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(rect=(72, 600, 272, 660)))
    s2 = d.sign_pdf(s1, id_b[2], d.SignOptions(rect=(300, 600, 500, 660)))
    assert s2.startswith(s1)
    sts = d.verify_pdf(s2, trusted=[id_a[1], id_b[1]])
    assert [s.status for s in sts] == [d.VALID, d.VALID]
    assert [s.signer for s in sts] == ["Mario Rossi", "Lucia Bianchi"]
    assert sts[0].modifications.startswith("Modifiche consentite")
    assert "firma aggiunta" in sts[0].modifications
    assert [f.name for f in d.signature_fields(s2)] == ["Firma1", "Firma2"]


def test_signed_field_cannot_be_reused(pdf_bytes, id_a):
    s1 = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(field_name="Cliente"))
    with pytest.raises(d.SigningNotAllowed):
        d.sign_pdf(s1, id_a[2], d.SignOptions(field_name="Cliente"))


def test_certification_no_changes_blocks_signing(pdf_bytes, id_a, id_b):
    cert = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(certify=True, docmdp_level=1))
    assert d.certification_level(cert) == 1
    with pytest.raises(d.SigningNotAllowed) as exc:
        d.sign_pdf(cert, id_b[2], d.SignOptions())
    assert "nessuna modifica" in str(exc.value)


def test_certify_with_existing_signature_blocked(pdf_bytes, id_a):
    s1 = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions())
    with pytest.raises(d.SigningNotAllowed):
        d.sign_pdf(s1, id_a[2], d.SignOptions(certify=True))


def test_certification_level_2(pdf_bytes, id_a, id_b):
    cert = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(certify=True, docmdp_level=2, reason="Autore"))
    [st] = d.verify_pdf(cert, trusted=[id_a[1]])
    assert st.kind == "certificazione" and st.docmdp_level == 2
    assert st.status == d.VALID
    assert d.summary([st], []) == ("Certificato da Mario Rossi — consentito: "
                                   "compilazione dei moduli e firme")
    assert b"/DocMDP" in cert and b"/Perms" in cert
    # una firma successiva è consentita dal livello 2
    s2 = d.sign_pdf(cert, id_b[2], d.SignOptions())
    sts = d.verify_pdf(s2, trusted=[id_a[1], id_b[1]])
    assert [s.status for s in sts] == [d.VALID, d.VALID]
    # un'annotazione invece no
    annotated = _add_annotation(cert)
    [st] = d.verify_pdf(annotated, trusted=[id_a[1]])
    assert st.status == d.INVALID


# --------------------------------------------------------------------------- #
# Marca temporale
# --------------------------------------------------------------------------- #


def test_timestamp_b_t(pdf_bytes, id_a, tsa):
    stamper, tsa_info = tsa
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(timestamper=stamper))
    [st] = d.verify_pdf(signed, trusted=[id_a[1], tsa_info])
    assert st.pades_level == "B-T"
    assert st.time_source == "marca temporale"
    assert st.timestamp_tsa == "TSA di prova"
    assert st.status == d.VALID
    assert abs(st.signing_time - datetime.now(timezone.utc)) < timedelta(minutes=5)


def test_timestamp_unreachable(pdf_bytes, id_a):
    with pytest.raises(d.TimestampError) as exc:
        d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(timestamp_url="http://127.0.0.1:9/", timestamp_timeout=2))
    assert exc.value.kind == "rete"


# --------------------------------------------------------------------------- #
# Campi firma
# --------------------------------------------------------------------------- #


def test_signature_fields_and_duplicates(pdf_bytes, id_a, tsa):
    data, n1 = d.add_signature_field(pdf_bytes, "Cliente", 0, (72, 600, 272, 660), required=True,
                                     tooltip="Firma del cliente",
                                     timestamp_url="http://tsa.example.org/tsr", timestamp_required=True)
    assert n1 == "Cliente" and data.startswith(pdf_bytes)
    data2, n2 = d.add_signature_field(data, "Cliente", 1, (72, 600, 272, 660))
    assert n2 != "Cliente" and n2.startswith("Cliente")
    data3, n3 = d.add_signature_field(data2, "Nascosto", 0, (300, 600, 400, 650), hidden=True)
    fields = d.signature_fields(data3)
    by = {f.name: f for f in fields}
    assert set(by) == {"Cliente", n2, "Nascosto"}
    assert by["Cliente"].required and by["Cliente"].timestamp_required_url == "http://tsa.example.org/tsr"
    assert by["Cliente"].rect == pytest.approx((72, 600, 272, 660))
    assert by["Cliente"].page == 0 and by[n2].page == 1
    assert by["Nascosto"].hidden and not by["Cliente"].hidden
    assert not any(f.signed for f in fields)
    from pyhanko.pdf_utils.reader import PdfFileReader

    reader = PdfFileReader(io.BytesIO(data3))
    cliente = next(f for f in reader.root["/AcroForm"]["/Fields"] if f.get_object()["/T"] == "Cliente")
    ts = cliente.get_object()["/SV"]["/TimeStamp"]
    assert str(ts["/URL"]) == "http://tsa.example.org/tsr" and int(ts["/Ff"]) == 1
    assert int(cliente.get_object()["/Ff"]) & 2  # obbligatorio
    assert d.summary([], fields) == "2 firme richieste"
    # firma di un campo esistente
    # il campo impone la marca temporale: senza server raggiungibile la firma non avviene
    with pytest.raises(d.TimestampError):
        d.sign_pdf(data3, id_a[2], d.SignOptions(field_name="Cliente", timestamp_url="http://127.0.0.1:9/",
                                                 timestamp_timeout=2))
    signed = d.sign_pdf(data3, id_a[2], d.SignOptions(field_name="Cliente", timestamper=tsa[0]))
    by = {f.name: f for f in d.signature_fields(signed)}
    assert by["Cliente"].signed and not by[n2].signed
    [st] = d.verify_pdf(signed, trusted=[id_a[1], tsa[1]])
    assert st.field_name == "Cliente" and st.pades_level == "B-T"


def test_field_lock(pdf_bytes, id_a):
    data, name = d.add_signature_field(pdf_bytes, "Firma", 0, (72, 600, 272, 660), lock_fields=["Nome", "Data"])
    assert b"/SigFieldLock" in data
    signed = d.sign_pdf(data, id_a[2], d.SignOptions(field_name=name))
    assert b"/FieldMDP" in signed[len(data):]


# --------------------------------------------------------------------------- #
# Copia senza firme e utilità
# --------------------------------------------------------------------------- #


def test_strip_signatures(pdf_bytes, id_a):
    signed = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(certify=True, rect=(72, 600, 272, 660)))
    original = bytes(signed)
    assert d.has_signatures(signed)
    stripped = d.strip_signatures(signed)
    assert signed == original
    assert b"/ByteRange" not in stripped and b"/DocMDP" not in stripped and b"/Perms" not in stripped
    assert not d.has_signatures(stripped)
    fields = d.signature_fields(stripped)
    assert [f.name for f in fields] == ["Firma1"] and not fields[0].signed
    assert d.verify_pdf(stripped) == []


def test_unsigned_document(pdf_bytes):
    assert not d.has_signatures(pdf_bytes)
    assert d.verify_pdf(pdf_bytes) == []
    assert d.certification_level(pdf_bytes) is None
    assert d.signature_fields(pdf_bytes) == []


def test_encrypted_with_user_password_refused(encrypted_pdf, id_a):
    data = open(encrypted_pdf, "rb").read()
    with pytest.raises(d.SigningNotAllowed):
        d.sign_pdf(data, id_a[2], d.SignOptions())


def test_no_secrets_in_digital_id_repr(id_a):
    _p, _i, did = id_a
    assert PASSWORD not in repr(did)
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(did)


def test_translations_cover_format_keys():
    for k, v in d.TRANSLATIONS_EN.items():
        assert set(re.findall(r"{(\w+)}", k)) == set(re.findall(r"{(\w+)}", v)), k


def test_field_constraints_enforced(pdf_bytes, id_a, id_b):
    from foliant.modules.sign import digital
    data, _n = digital.add_signature_field(pdf_bytes, "Cliente", 0, (300, 700, 500, 760),
                                           allowed_reasons=["Accetto"], allowed_issuers=[id_b[1]])
    with pytest.raises(digital.SigningNotAllowed, match="motivi"):
        digital.sign_pdf(data, id_b[2], digital.SignOptions(field_name="Cliente", reason="Altro"))
    with pytest.raises(digital.SigningNotAllowed, match="emessi"):
        digital.sign_pdf(data, id_a[2], digital.SignOptions(field_name="Cliente", reason="Accetto"))
    out = digital.sign_pdf(data, id_b[2], digital.SignOptions(field_name="Cliente", reason="Accetto"))
    assert out.startswith(data)


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_visible_appearance_in_box_on_rotated_cropped_page(id_a, rotation):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.set_cropbox(pymupdf.Rect(60, 90, 540, 730))
    page.set_rotation(rotation)
    vis = pymupdf.Rect(100, 120, 330, 200)                       # riquadro nella vista
    unrot = vis * page.derotation_matrix
    unrot.normalize()
    data = doc.tobytes()
    out = d.sign_pdf(data, id_a[2], d.SignOptions(rect=tuple(unrot), reason="Prova",
                                                  appearance={"layout": "testo"}))
    r = pymupdf.open(stream=out)[0]
    pix = r.get_pixmap(dpi=72, annots=True)
    arr = __import__("numpy").frombuffer(pix.samples, __import__("numpy").uint8).reshape(pix.height, pix.width, pix.n)
    ys, xs = __import__("numpy").nonzero(arr[..., 0] < 160)
    ink = pymupdf.Rect(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    assert (ink & vis).get_area() / ink.get_area() > 0.97, (ink, vis)
    st = d.signature_fields(out)[0]
    got = pymupdf.Rect(st.rect)
    assert abs(got.x0 - unrot.x0) < 1 and abs(got.y0 - unrot.y0) < 1


def test_appearance_date_format_and_label_language():
    from datetime import datetime, timezone

    class Info:
        subject = "Mario Rossi"
        subject_dn = "CN=Mario Rossi"
    now = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    opts = d.SignOptions(reason="Approvo", location="Milano")
    it = d._appearance_lines(Info, opts, now, ["nome", "data", "motivo", "etichette"],
                             {"date_format": "gg/mm/aaaa", "label_language": "it"})
    en = d._appearance_lines(Info, opts, now, ["nome", "data", "luogo", "etichette"],
                             {"date_format": "aaaa-mm-gg hh:mm:ss", "label_language": "en"})
    assert it == ["Firmato digitalmente da Mario Rossi", "Data: 04/03/2026", "Motivo: Approvo"]
    assert en[0] == "Digitally signed by Mario Rossi" and en[1].startswith("Date: 2026-03-04 ")
    assert en[2] == "Location: Milano"


def _obj(doc, x):
    try:
        return doc.xref_object(x, compressed=True)
    except Exception:
        return ""


def _key(doc, x, k):
    try:
        return doc.xref_get_key(x, k)
    except Exception:
        return ("null", "null")


def test_non_latin_name_uses_embedded_unicode_font(pdf_bytes, tmp_path):
    """SIG-09: nome in alfabeto non latino → font con copertura adeguata, incorporato in sottoinsieme."""
    from tests.modules.sign.conftest import _fast_create
    path = tmp_path / "ivan.p12"
    _fast_create(path, common_name="Иван Петров", country="IT")
    did = d.load_pkcs12(path, PASSWORD)
    out = d.sign_pdf(pdf_bytes, did, d.SignOptions(rect=(60, 600, 300, 680), reason="Согласен",
                                                   appearance={"layout": "testo"}))
    doc = pymupdf.open(stream=out)
    forms = [x for x in range(1, doc.xref_length()) if "/Type0" in _obj(doc, x)]
    assert forms
    import re as _re
    tu = int(_re.search(r"/ToUnicode (\d+) 0 R", _obj(doc, forms[0])).group(1))
    cmap = doc.xref_stream(tu).decode()
    assert "<0418>" in cmap and "<041F>" in cmap            # «И», «П»
    ff = [x for x in range(1, doc.xref_length()) if _key(doc, x, "FontFile2")[0] == "xref"]
    size = len(doc.xref_stream(int(doc.xref_get_key(ff[0], "FontFile2")[1].split()[0])))
    assert size < 200_000                                    # sottoinsieme, non il font intero
    st = d.verify_pdf(out, trusted=[did.info])
    assert st[0].status == "valida"


def test_latin_name_keeps_standard_font(pdf_bytes, id_a):
    out = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(rect=(60, 600, 300, 680), appearance={"layout": "testo"}))
    doc = pymupdf.open(stream=out)
    assert not [x for x in range(1, doc.xref_length()) if "/Type0" in _obj(doc, x)]


def test_timestamp_auth_passed_to_client(pdf_bytes, id_a, monkeypatch):
    from pyhanko.sign import timestamps
    seen = {}

    class Fake(timestamps.HTTPTimeStamper):
        def __init__(self, url, **kw):
            seen.update(kw)
            raise OSError("nessuna rete nei test")

    monkeypatch.setattr(timestamps, "HTTPTimeStamper", Fake)
    with pytest.raises(Exception):
        d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(timestamp_url="https://tsa.example.org",
                                                     timestamp_auth=("mario", "pw")))
    assert seen.get("auth") == ("mario", "pw")


def test_field_digest_method_constraint(pdf_bytes, id_a):
    data, _n = d.add_signature_field(pdf_bytes, "Cliente", 0, (300, 700, 500, 760), digest_methods=["sha512"])
    f = d.signature_fields(data)[0]
    assert f.digest_methods == ["sha512"]
    out = d.sign_pdf(data, id_a[2], d.SignOptions(field_name="Cliente"))      # sha256 richiesto → sha512
    st = d.verify_pdf(out, trusted=[id_a[1]])[0]
    assert st.status == "valida" and st.md_algorithm == "sha512"
    with pytest.raises(d.SignError):
        d.add_signature_field(pdf_bytes, "X", 0, (10, 10, 100, 40), digest_methods=["md5"])


def _expired_did(expired_id):
    return d.load_pkcs12(expired_id[0], PASSWORD)


def test_bug01_trusted_expired_without_timestamp_is_indeterminate(pdf_bytes, expired_id):
    """Certificato attendibile ma scaduto, senza marca temporale → Indeterminata (nessuna prova dell'ora)."""
    did = _expired_did(expired_id)
    out = d.sign_pdf(pdf_bytes, did, d.SignOptions(allow_expired=True))
    st = d.verify_pdf(out, trusted=[expired_id[1]])[0]
    assert st.status == "indeterminata", st.reasons
    assert any("senza prova dell'ora" in r for r in st.reasons)
    assert not any("dopo l'ora attestata" in r for r in st.reasons)


def test_bug02_expired_before_timestamp_is_invalid(pdf_bytes, expired_id, tsa):
    """Certificato scaduto prima dell'ora attestata da una marca temporale valida → Non valida."""
    did = _expired_did(expired_id)
    out = d.sign_pdf(pdf_bytes, did, d.SignOptions(allow_expired=True, timestamper=tsa[0]))
    st = d.verify_pdf(out, trusted=[expired_id[1], tsa[1]])[0]
    assert st.time_source == "marca temporale"
    assert st.status == "non valida", st.reasons
    assert any("prima dell'ora attestata dalla marca temporale" in r for r in st.reasons)


def test_valid_certificate_with_timestamp_still_valid(pdf_bytes, id_a, tsa):
    out = d.sign_pdf(pdf_bytes, id_a[2], d.SignOptions(timestamper=tsa[0]))
    st = d.verify_pdf(out, trusted=[id_a[1], tsa[1]])[0]
    assert st.status == "valida", st.reasons


@pytest.mark.parametrize("perms,password,allowed", [
    (pymupdf.PDF_PERM_PRINT, "utente", False),
    (pymupdf.PDF_PERM_PRINT, "proprietario", True),
    (pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM, "utente", True),
    (pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ANNOTATE, "utente", True),
])
def test_encrypted_permissions_checked_before_signing(id_a, perms, password, allowed):
    """SIG-08: PDF cifrato → si firma solo con i permessi di firma/compilazione o da proprietario."""
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="utente", owner_pw="proprietario",
                       permissions=perms)
    opts = d.SignOptions(password=password)
    if allowed:
        out = d.sign_pdf(data, id_a[2], opts)
        assert out.startswith(data)
        assert d.verify_pdf(out, trusted=[id_a[1]], password=password)[0].status == "valida"
    else:
        with pytest.raises(d.SigningNotAllowed, match="proprietario"):
            d.sign_pdf(data, id_a[2], opts)
        with pytest.raises(d.SigningNotAllowed, match="proprietario"):
            d.add_signature_field(data, "Campo", 0, (10, 10, 100, 40), password=password)


def _kids_field_pdf() -> bytes:
    """PDF con campo di testo a struttura padre + widget figlio (come add_widget di PyMuPDF)."""
    doc = pymupdf.open()
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "acquirente"
    w.rect = pymupdf.Rect(100, 100, 300, 120)
    page.add_widget(w)
    return doc.tobytes()


def _fill_and_save(signed: bytes) -> bytes:
    from foliant.api import Document
    doc = Document.from_bytes(signed, modified=False)
    with doc.change("Compila", kind="form") as pdf:
        page = pdf[0]
        wd = next(page.widgets())
        wd.field_value = "Luigi Bianchi"
        wd.update()
    return doc.save_bytes()


@pytest.mark.parametrize("certify,level,expected", [
    (False, 2, "valida"), (True, 2, "valida"), (True, 3, "valida"), (True, 1, "non valida")])
def test_fill_parent_kids_field_after_signature(id_a, certify, level, expected):
    """Compilazione di un campo padre + widget figlio dopo la firma: consentita
    (approvazione, certificazione P=2/3), non consentita con P=1."""
    data = _kids_field_pdf()
    signed = d.sign_pdf(data, id_a[2], d.SignOptions(rect=(100, 300, 300, 360), certify=certify,
                                                       docmdp_level=level))
    out = _fill_and_save(signed)
    assert out.startswith(signed)
    st = d.verify_pdf(out, trusted=[id_a[1]])[0]
    assert st.status == expected, (st.reasons, st.modifications)
    if expected == "valida":
        assert "compilazione di campi modulo" in st.modifications
