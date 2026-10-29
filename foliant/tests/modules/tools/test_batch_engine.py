"""Motore delle operazioni in blocco: criteri di accettazione BAT (logica pura)."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime

import pymupdf
import pytest

from foliant.core.platform import find_tool
from foliant.api import OperationCancelled
from foliant.modules.tools import engine as be

from .conftest import build_pdf, page_lines

STARTED = datetime(2026, 10, 28, 9, 30, 0)


def mk(path, pages=1, text="Documento {n}"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    return build_pdf(path, [[text.format(n=i + 1), "contenuto di prova"] for i in range(pages)])


def items_for(paths):
    return [be.analyze_file(p) for p in paths]


def job(dest, *steps, **kw):
    return be.JobSettings(steps=[be.Step(a, p) for a, p in steps], destination=str(dest), **kw)


def page_texts(path, password=None):
    doc = pymupdf.open(path)
    if password:
        doc.authenticate(password)
    out = [p.get_text() for p in doc]
    doc.close()
    return out


def leftovers(folder):
    return [f for f in os.listdir(folder) if f.startswith(".") or f.endswith(".tmp")]


# ------------------------------------------------------------------ BAT-01
def test_collect_folder_with_subfolders(tmp_path):
    root = tmp_path / "in"
    for n in ("a", "b", "c"):
        mk(root / f"{n}.pdf")
    for n in ("d", "e"):
        mk(root / "sub" / f"{n}.pdf")
    (root / "sub" / "nota.txt").write_text("x")
    found, ignored = be.collect_inputs([str(root)], recursive=True)
    assert len(found) == 5
    assert len(ignored) == 1 and ignored[0][0].endswith("nota.txt")


def test_collect_no_duplicates_and_symlink_cycles(tmp_path):
    root = tmp_path / "in"
    p = mk(root / "uno.pdf")
    os.symlink(p, root / "collegamento.pdf")
    os.symlink(root, root / "ciclo")
    found, _ig = be.collect_inputs([str(root)])
    assert found == [os.path.realpath(p)]
    again, _ig = be.collect_inputs([str(root / "collegamento.pdf")], existing=found)
    assert again == []


def test_natural_sort():
    paths = ["/x/doc10.pdf", "/x/doc2.pdf", "/x/doc1.pdf"]
    assert sorted(paths, key=be.natural_key) == ["/x/doc1.pdf", "/x/doc2.pdf", "/x/doc10.pdf"]


def test_analyze_encrypted_requires_password(encrypted_pdf, sample_pdf):
    it = be.analyze_file(encrypted_pdf)
    assert it.needs_password and it.status_text == "richiede password"
    ok = be.analyze_file(sample_pdf)
    assert ok.pages == 3 and ok.has_text and ok.status_text == ""


# ------------------------------------------------------------------ BAT-02
def test_same_name_from_different_folders_renamed(tmp_path):
    a = mk(tmp_path / "x" / "relazione.pdf")
    b = mk(tmp_path / "y" / "relazione.pdf")
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for([a, b]), job(dest, ("compress", {})), STARTED)
    assert sorted(os.listdir(dest)) == ["relazione (1).pdf", "relazione.pdf"]
    assert all(r.status in ("ok", "warning") for r in rep.results)


def test_skip_policy_leaves_existing_file(tmp_path):
    a = mk(tmp_path / "in" / "doc.pdf")
    dest = tmp_path / "out"
    dest.mkdir()
    existing = dest / "doc.pdf"
    existing.write_bytes(b"vecchio")
    mtime = existing.stat().st_mtime_ns
    rep = be.run_job(None, items_for([a]), job(dest, ("watermark", {}), conflict="skip"), STARTED)
    assert existing.read_bytes() == b"vecchio" and existing.stat().st_mtime_ns == mtime
    assert rep.results[0].status == "skipped" and "esiste già" in rep.results[0].message


def test_keep_structure(tmp_path):
    root = tmp_path / "ingresso"
    x = mk(root / "a" / "x.pdf")
    y = mk(root / "b" / "y.pdf")
    dest = tmp_path / "dest"
    be.run_job(None, items_for([x, y]), job(dest, ("watermark", {}), keep_structure=True, input_root=str(root)),
               STARTED)
    assert (dest / "a" / "x.pdf").exists() and (dest / "b" / "y.pdf").exists()


def test_replace_originals_with_backup(tmp_path):
    a = mk(tmp_path / "in" / "doc.pdf")
    original = open(a, "rb").read()
    s = job("", ("watermark", {"text": "COPIA"}), replace_originals=True)
    rep = be.run_job(None, items_for([a]), s, STARTED)
    assert rep.results[0].status == "ok"
    assert open(a + ".bak", "rb").read() == original
    assert "COPIA" in page_texts(a)[0]


def test_destination_modes(tmp_path):
    a = mk(tmp_path / "in" / "doc.pdf")
    s = job("", ("watermark", {}), destination_mode="same")
    s.pattern = be.default_pattern(s)
    assert s.pattern == "{nome}_filigrana"
    be.run_job(None, items_for([a]), s, STARTED)
    assert (tmp_path / "in" / "doc_filigrana.pdf").exists()
    s2 = job("", ("watermark", {}), destination_mode="subfolder", subfolder="elaborati")
    be.run_job(None, items_for([a]), s2, STARTED)
    assert (tmp_path / "in" / "elaborati" / "doc.pdf").exists()


# ------------------------------------------------------------------ BAT-03
def test_counter_pattern(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf") for n in "abc"]
    dest = tmp_path / "out"
    s = job(dest, ("compress", {}), pattern="{nome}_{contatore:3}", counter_start=1)
    prev = [os.path.basename(o) for _i, o in be.preview_names(items_for(paths), s, STARTED)]
    assert prev == ["a_001.pdf", "b_002.pdf", "c_003.pdf"]
    be.run_job(None, items_for(paths), s, STARTED)
    assert sorted(os.listdir(dest)) == prev


def test_invalid_chars_and_placeholders():
    s = be.JobSettings(steps=[be.Step("compress")], destination="/tmp")
    assert be.render_name("Fattura/2026", "/x/a.pdf", 0, s, STARTED) == "Fattura_2026.pdf"
    assert be.render_name("{data}_{ora}_{nome}", "/x/a.pdf", 0, s, STARTED) == "2026-10-28_09-30-00_a.pdf"
    errs = be.validate_pattern("{nome}_{autore_x}")
    assert errs and "autore_x" in errs[0]
    assert be.validate_pattern("{nome")
    s.pattern = "{autore_x}"
    errors, _w = be.validate_settings([be.QueueItem("/x/a.pdf")], s)
    assert any("autore_x" in e for e in errors)


# ------------------------------------------------------------------ BAT-04 / BAT-05
def _ten_files(tmp_path, encrypted_pdf):
    paths = []
    for i in range(10):
        p = tmp_path / "in" / f"f{i + 1:02d}.pdf"
        if i == 3:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.7\nrotto" * 3)
        elif i == 6:
            p.write_bytes(open(encrypted_pdf, "rb").read())
        else:
            mk(p)
        paths.append(str(p))
    items = items_for(paths)
    items[6].password = "sbagliata"
    return items


def test_errors_per_file_continue(tmp_path, encrypted_pdf):
    items = _ten_files(tmp_path, encrypted_pdf)
    rep = be.run_job(None, items, job(tmp_path / "out", ("watermark", {})), STARTED)
    c = rep.counts()
    assert c["ok"] == 8 and c["error"] == 2
    codes = {rep.results[3].code, rep.results[6].code}
    assert codes == {"PDF-01", "PWD-01"}
    assert set(rep.failed_items()) == {items[3].path, items[6].path}
    assert leftovers(tmp_path / "out") == []


def test_errors_stop_policy(tmp_path, encrypted_pdf):
    items = _ten_files(tmp_path, encrypted_pdf)
    rep = be.run_job(None, items, job(tmp_path / "out", ("watermark", {}), error_policy="stop"), STARTED)
    assert [r.status for r in rep.results[:3]] == ["ok"] * 3
    assert rep.results[3].status == "error"
    assert all(r.status == "not_processed" for r in rep.results[4:])


def test_csv_report_and_no_passwords(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf") for n in "ab"]
    secret = "Segreto!2026"
    rep = be.run_job(None, items_for(paths), job(tmp_path / "out", ("protect", {"user_password": secret,
                                                                                "owner_password": "Altro#99"})),
                     STARTED)
    text = rep.to_csv()
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["file_ingresso", "file_uscita", "esito", "codice", "messaggio", "pagine", "byte_ingresso",
                       "byte_uscita", "durata_ms"]
    assert len(rows) == 3 and all(r[2] == "Completato" for r in rows[1:])
    js = rep.to_json()
    json.loads(js)
    for blob in (text, js, rep.summary()):
        assert secret not in blob and "Altro#99" not in blob


def test_cancel_during_eighth_file(tmp_path):
    paths = [mk(tmp_path / "in" / f"f{i + 1:02d}.pdf") for i in range(20)]
    dest = tmp_path / "out"

    class P:
        def update(self, fraction=None, text=None):
            if text and text.startswith("File 8 di"):
                raise OperationCancelled()

        def check(self):
            pass

    rep = be.run_job(P(), items_for(paths), job(dest, ("watermark", {})), STARTED)
    assert rep.cancelled
    assert [r.status for r in rep.results[:7]] == ["ok"] * 7
    assert rep.results[7].status == "cancelled"
    assert all(r.status == "not_processed" for r in rep.results[8:])
    assert sorted(os.listdir(dest)) == [f"f{i + 1:02d}.pdf" for i in range(7)]
    for f in os.listdir(dest):
        pymupdf.open(str(dest / f)).close()


def test_missing_file_reported(tmp_path):
    p = mk(tmp_path / "in" / "via.pdf")
    item = be.analyze_file(p)
    os.unlink(p)
    rep = be.run_job(None, [item], job(tmp_path / "out", ("compress", {})), STARTED)
    assert rep.results[0].status == "error" and rep.results[0].code == "FILE-01"


# ------------------------------------------------------------------ BAT-09 / 10 / 11
def test_compress_never_grows(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=3)
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("compress", {"level": "high"})), STARTED)
    r = rep.results[0]
    assert r.bytes_out <= r.bytes_in
    assert os.path.getsize(r.outputs[0]) == r.bytes_out
    assert "Documento 1" in page_texts(r.outputs[0])[0]


def test_compress_encrypted_keeps_password(tmp_path, encrypted_pdf):
    item = be.analyze_file(encrypted_pdf)
    item.password = "segreta"
    rep = be.run_job(None, [item], job(tmp_path / "out", ("compress", {})), STARTED)
    out = rep.results[0].outputs[0]
    doc = pymupdf.open(out)
    assert doc.needs_pass and doc.authenticate("segreta")
    doc.close()


def test_protect_requires_password(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf") for n in "ab"]
    rep = be.run_job(None, items_for(paths), job(tmp_path / "out", ("protect", {"user_password": "Prova!2026"})),
                     STARTED)
    for r in rep.results:
        doc = pymupdf.open(r.outputs[0])
        assert doc.needs_pass
        assert not doc.authenticate("") and not doc.authenticate("errata")
        assert doc.authenticate("Prova!2026")
        assert "AES" in doc.metadata["encryption"] and "256" in doc.metadata["encryption"]
        doc.close()


def test_protect_permissions_only(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf")
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("protect", {"owner_password": "capo",
                                                                              "allow_print": False})), STARTED)
    doc = pymupdf.open(rep.results[0].outputs[0])
    assert not doc.needs_pass
    assert not doc.permissions & pymupdf.PDF_PERM_PRINT
    doc.close()


def test_protect_same_passwords_blocked():
    s = be.JobSettings(steps=[be.Step("protect", {"user_password": "x", "owner_password": "x"})], destination="/tmp")
    errors, _w = be.validate_settings([be.QueueItem("/tmp/a.pdf")], s)
    assert any("diverse" in e for e in errors)


def test_protect_csv_passwords(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf") for n in ("a", "b", "c", "d")]
    table = tmp_path / "pw.csv"
    table.write_text("a.pdf;pa\nb.pdf;pb\nc.pdf;pc\n")
    rep = be.run_job(None, items_for(paths), job(tmp_path / "out", ("protect", {"password_csv": str(table)})),
                     STARTED)
    for r, pw in zip(rep.results[:3], ("pa", "pb", "pc")):
        doc = pymupdf.open(r.outputs[0])
        assert doc.authenticate(pw) and not pymupdf.open(r.outputs[0]).authenticate("pa" if pw != "pa" else "pb")
        doc.close()
    assert rep.results[3].status == "skipped" and "non definita" in rep.results[3].message


def _locked(tmp_path, name, user="chiave", owner="capo", perms=-1):
    path = tmp_path / "in" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=20)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user, owner_pw=owner, permissions=perms)
    doc.close()
    return str(path)


def test_remove_known_password(tmp_path):
    paths = [_locked(tmp_path, f"l{i}.pdf", user="chiave", owner="chiave") for i in range(4)]
    rep = be.run_job(None, items_for(paths), job(tmp_path / "out", ("remove_password", {"passwords": "chiave"})),
                     STARTED)
    for r in rep.results:
        assert r.status in ("ok", "warning")
        doc = pymupdf.open(r.outputs[0])
        assert not doc.needs_pass and not doc.metadata.get("encryption")
        doc.close()
    assert "chiave" not in rep.to_csv() + rep.to_json()


def test_remove_password_candidate_number(tmp_path):
    p = _locked(tmp_path, "l.pdf", user="terza", owner="terza")
    rep = be.run_job(None, items_for([p]), job(tmp_path / "out", ("remove_password", {"passwords": "uno\ndue\nterza"})),
                     STARTED)
    r = rep.results[0]
    assert "candidata n. 3" in r.message and "terza" not in r.message


def test_remove_password_needs_owner(tmp_path):
    p = _locked(tmp_path, "l.pdf", user="aperta", owner="capo", perms=pymupdf.PDF_PERM_COPY)
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for([p]), job(dest, ("remove_password", {"passwords": "aperta"})), STARTED)
    assert rep.results[0].status == "error" and "permessi" in rep.results[0].message
    assert not dest.exists() or os.listdir(dest) == []


def test_remove_password_not_protected_and_limit(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf")
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("remove_password", {"passwords": "x"})), STARTED)
    assert rep.results[0].status == "skipped" and "Non protetto" in rep.results[0].message
    s = job(tmp_path / "out", ("remove_password", {"passwords": "\n".join(str(i) for i in range(21))}))
    errors, _w = be.validate_settings(items_for([a]), s)
    assert errors


# ------------------------------------------------------------------ BAT-12 / 13 / 16
def test_watermark_every_page_and_odd_pages(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=3)
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("watermark", {"text": "RISERVATO"})), STARTED)
    texts = page_texts(rep.results[0].outputs[0])
    assert all("RISERVATO" in t for t in texts)
    assert all(f"Documento {i + 1}" in t for i, t in enumerate(texts))
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out2", ("watermark", {"text": "BOZZA", "pages": "dispari"})),
                     STARTED)
    texts = page_texts(rep.results[0].outputs[0])
    assert ["BOZZA" in t for t in texts] == [True, False, True]


def test_watermark_name_placeholder_and_replace(tmp_path):
    a = mk(tmp_path / "in" / "contratto.pdf")
    data = be.add_watermark(open(a, "rb").read(), "{nome}")
    assert "contratto" not in pymupdf.open(stream=data).load_page(0).get_text()
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("watermark", {"text": "{nome}"})), STARTED)
    out = rep.results[0].outputs[0]
    assert "contratto" in page_texts(out)[0]
    again = be.add_watermark(open(out, "rb").read(), "NUOVA", replace=True)
    t = pymupdf.open(stream=again).load_page(0).get_text()
    assert "NUOVA" in t and t.count("contratto") == 0


def test_watermark_marked_and_removable(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    original = open(a, "rb").read()
    marked = be.add_page_numbers(be.add_watermark(original, "RISERVATO"))
    doc = pymupdf.open(stream=marked)
    assert b"/FolStamp_watermark_" in b"".join(doc.xref_stream(x) for x in doc[0].get_contents())
    doc.close()
    cleaned, n = be.remove_marks(marked, {"watermark"})
    assert n == 2
    t = pymupdf.open(stream=cleaned).load_page(0).get_text()
    assert "RISERVATO" not in t and "Pagina 1 di 2" in t
    # visivamente identico all'originale
    def pix(d):
        x = pymupdf.open(stream=d)
        s = x[0].get_pixmap(dpi=50).samples
        x.close()
        return s
    assert pix(be.remove_marks(be.add_watermark(original, "X"), {"watermark"})[0]) == pix(original)


def test_remove_marks_nothing_found(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf")
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("remove_marks", {"watermark": True})), STARTED)
    assert rep.results[0].status == "skipped" and "nessun elemento riconoscibile" in rep.results[0].message


def test_background_letterhead(tmp_path):
    letter = build_pdf(tmp_path / "carta.pdf", [["CARTA UNO"], ["CARTA DUE"]])
    a = mk(tmp_path / "in" / "a.pdf", pages=3)
    rep = be.run_job(None, items_for([a]), job(tmp_path / "out", ("background", {"file": letter,
                                                                                 "first_page_separate": True})),
                     STARTED)
    texts = page_texts(rep.results[0].outputs[0])
    assert "CARTA UNO" in texts[0] and all("CARTA DUE" in t for t in texts[1:])
    assert all(f"Documento {i + 1}" in t for i, t in enumerate(texts))


# ------------------------------------------------------------------ BAT-14 / 15
def test_page_numbers_per_file_and_continuous(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=3)
    b = mk(tmp_path / "in" / "b.pdf", pages=4)
    rep = be.run_job(None, items_for([a, b]), job(tmp_path / "out", ("page_numbers", {})), STARTED)
    assert "Pagina 3 di 3" in page_texts(rep.results[0].outputs[0])[2]
    assert "Pagina 4 di 4" in page_texts(rep.results[1].outputs[0])[3]
    rep = be.run_job(None, items_for([a, b]), job(tmp_path / "out2", ("page_numbers", {
        "text": "{pagina} di {totale_coda}", "continuous": True})), STARTED)
    tb = page_texts(rep.results[1].outputs[0])
    assert "4 di 7" in tb[0] and "7 di 7" in tb[3]


def test_header_mirror_and_skip_first(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    data = be.add_header_footer(open(a, "rb").read(), {"top-right": "INTESTA"}, mirror=True)
    doc = pymupdf.open(stream=data)
    r1 = doc[0].search_for("INTESTA")[0]
    r2 = doc[1].search_for("INTESTA")[0]
    assert r1.x0 > doc[0].rect.width / 2 and r2.x1 < doc[1].rect.width / 2
    data = be.add_page_numbers(open(a, "rb").read(), text="N{pagina}", skip_first=True, count_skipped=True)
    doc = pymupdf.open(stream=data)
    assert "N1" not in doc[0].get_text() and "N2" in doc[1].get_text()
    twice = be.add_header_footer(be.add_header_footer(open(a, "rb").read(), {"top-left": "H"}), {"top-left": "H"})
    assert pymupdf.open(stream=twice)[0].get_text().count("H") == 1


def test_bates_continuous_and_names(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf", pages=k) for n, k in (("a", 2), ("b", 3), ("c", 1))]
    s = job(tmp_path / "out", ("bates", {"prefix": "ACME-", "digits": 6, "start": 1}),
            pattern="{bates_inizio}_{bates_fine}")
    rep = be.run_job(None, items_for(paths), s, STARTED)
    labels = []
    for r in rep.results:
        labels += [t for page in page_texts(r.outputs[0]) for t in page.split() if t.startswith("ACME-")]
    assert labels == [f"ACME-{i:06d}" for i in range(1, 7)]
    assert os.path.basename(rep.results[1].outputs[0]) == "ACME-000003_ACME-000005.pdf"
    s = job(tmp_path / "out2", ("bates", {"prefix": "ACME-", "restart": True}))
    rep = be.run_job(None, items_for(paths), s, STARTED)
    assert all("ACME-000001" in page_texts(r.outputs[0])[0] for r in rep.results)


def test_bates_insufficient_digits(tmp_path):
    paths = [mk(tmp_path / "in" / f"{n}.pdf", pages=k) for n, k in (("a", 2), ("b", 3))]
    s = job(tmp_path / "out", ("bates", {"digits": 6, "start": 999998}))
    errors, _w = be.validate_settings(items_for(paths), s)
    assert any("Cifre insufficienti" in e for e in errors)


# ------------------------------------------------------------------ BAT-17 / 18
def test_merge_order_and_bookmarks(tmp_path):
    c = mk(tmp_path / "in" / "C.pdf", pages=2)
    a = mk(tmp_path / "in" / "A.pdf", pages=5)
    b = mk(tmp_path / "in" / "B.pdf", pages=1)
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for([c, a, b]), job(dest, ("merge", {"name": "Tutto"})), STARTED)
    out = rep.results[0].outputs[0]
    doc = pymupdf.open(out)
    assert doc.page_count == 8
    assert [(t[1], t[2]) for t in doc.get_toc()] == [("C", 1), ("A", 3), ("B", 8)]
    doc.close()
    assert os.listdir(dest) == ["Tutto.pdf"]
    rep = be.run_job(None, items_for([c, a, b]), job(tmp_path / "o2", ("merge", {"blank_after_odd": True})), STARTED)
    doc = pymupdf.open(rep.results[0].outputs[0])
    assert [t[2] % 2 for t in doc.get_toc()] == [1, 1, 1]


def test_merge_with_damaged_file_produces_nothing(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf")
    bad = tmp_path / "in" / "rotto.pdf"
    bad.write_bytes(b"%PDF-1.4 rotto")
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for([a, str(bad)]), job(dest, ("merge", {})), STARTED)
    assert not dest.exists() or os.listdir(dest) == []
    assert rep.results[1].status == "error"
    assert "rotto.pdf" in rep.results[0].message


def test_insert_pages(tmp_path):
    cover = build_pdf(tmp_path / "cop.pdf", [["COPERTINA"]])
    a = mk(tmp_path / "in" / "a.pdf", pages=5)
    data, _w = be.insert_pages(open(a, "rb").read(), cover, "start")
    doc = pymupdf.open(stream=data)
    assert doc.page_count == 6 and "COPERTINA" in doc[0].get_text()
    data, _w = be.insert_pages(open(a, "rb").read(), "", "every", 2)
    doc = pymupdf.open(stream=data)
    assert doc.page_count == 7
    assert [i for i in range(7) if not doc[i].get_text().strip()] == [2, 5]
    data, warns = be.insert_pages(open(a, "rb").read(), "", "after", 10)
    assert warns and pymupdf.open(stream=data).page_count == 6


# ------------------------------------------------------------------ BAT-19
def test_print_jobs_in_order(tmp_path, monkeypatch):
    paths = [mk(tmp_path / "in" / f"{n}.pdf", pages=k) for n, k in (("a", 1), ("b", 2), ("c", 3))]
    log = []

    def fake_cmd(path, printer, copies=1, duplex="none", pages=""):
        doc = pymupdf.open(path)
        log.append((doc.page_count, copies, duplex, printer, pages))
        doc.close()
        return ["true"]

    monkeypatch.setattr(be, "_print_command", fake_cmd)
    rep = be.run_job(None, items_for(paths), job("", ("print", {"copies": 2, "duplex": "long"})), STARTED,
                     printer="Prova")
    assert [r.status for r in rep.results] == ["ok"] * 3
    assert log == [(1, 2, "long", "Prova", ""), (2, 2, "long", "Prova", ""), (3, 2, "long", "Prova", "")]
    log.clear()
    rep = be.run_job(None, items_for(paths[:1]), job("", ("print", {"pages": "1-2"})), STARTED)
    assert rep.results[0].status == "warning" and log[0][4] == "1"


def test_print_forbidden(tmp_path, monkeypatch):
    p = _locked(tmp_path, "np.pdf", user="", owner="capo", perms=pymupdf.PDF_PERM_COPY)
    ok = mk(tmp_path / "in" / "ok.pdf")
    monkeypatch.setattr(be, "_print_command", lambda *a, **k: ["true"])
    rep = be.run_job(None, items_for([p, ok]), job("", ("print", {})), STARTED)
    assert rep.results[0].status == "error" and rep.results[0].code == "PERM-01"
    assert rep.results[1].status == "ok"


# ------------------------------------------------------------------ BAT-06 / 08 (motori simulati)
def test_convert_with_fake_engine(tmp_path, fake_engines):
    paths = [mk(tmp_path / "in" / f"d{i}.pdf") for i in range(3)]
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for(paths), job(dest, ("watermark", {}), ("convert", {"format": "docx"})), STARTED)
    assert sorted(os.listdir(dest)) == ["d0.docx", "d1.docx", "d2.docx"]
    assert [c[0] for c in fake_engines["convert"]] == ["docx"] * 3
    assert all(r.status == "ok" for r in rep.results)


def test_convert_images_multiple_outputs(tmp_path, monkeypatch):
    a = mk(tmp_path / "in" / "nome.pdf", pages=3)

    def convert(input_path, output_path, fmt, progress=None, **options):
        base = os.path.splitext(output_path)[0]
        outs = []
        for i in range(3):
            p = f"{base}-{i + 1}.png"
            open(p, "wb").write(b"png")
            outs.append(p)
        return outs

    monkeypatch.setattr(be, "_convert_function", lambda: convert)
    dest = tmp_path / "out"
    be.run_job(None, items_for([a]), job(dest, ("convert", {"format": "png"})), STARTED)
    assert sorted(os.listdir(dest)) == ["nome_001.png", "nome_002.png", "nome_003.png"]


def test_ocr_with_fake_engine(tmp_path, fake_engines):
    (tmp_path / "in").mkdir()
    scan = build_pdf(tmp_path / "in" / "scan.pdf", [lambda p: p.draw_rect((50, 50, 200, 200), fill=(0, 0, 0))])
    native = mk(tmp_path / "in" / "testo.pdf")
    rep = be.run_job(None, items_for([scan, native]), job(tmp_path / "out", ("ocr", {"languages": "ita"})), STARTED)
    assert rep.results[0].status == "ok" and "OCR" in page_texts(rep.results[0].outputs[0])[0]
    assert rep.results[1].status == "skipped" and "Nessuna pagina" in rep.results[1].message
    assert fake_engines["ocr"] == [(["ita"], "searchable")]   # modalità del modulo OCR: testo invisibile


def test_missing_engines_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(be, "_ocr_function", lambda: None)
    monkeypatch.setattr(be, "_convert_function", lambda: None)
    ok, msg = be.action_available("ocr")
    assert not ok and "OCR" in msg
    ok, msg = be.action_available("convert")
    assert not ok and "Converti" in msg
    errors, _w = be.validate_settings([be.QueueItem("/x/a.pdf")], job(tmp_path, ("ocr", {})))
    assert errors


def test_convert_must_be_last(tmp_path):
    s = job(tmp_path, ("convert", {}), ("watermark", {}))
    assert be.validate_sequence(s.steps)


# ------------------------------------------------------------------ BAT-20
def test_sequences_roundtrip_without_secrets(tmp_path):
    steps = [be.Step("ocr", {"languages": "ita"}), be.Step("compress", {"level": "high"}),
             be.Step("protect", {"user_password": "Segreta1", "owner_password": "Capo2"})]
    folder = tmp_path / "seq"
    path = be.save_sequence(str(folder), "OCR → Comprimi → Proteggi", steps)
    raw = open(path, encoding="utf-8").read()
    assert "Segreta1" not in raw and "Capo2" not in raw
    name, loaded = be.load_sequence(path)
    assert name == "OCR → Comprimi → Proteggi"
    assert [s.action for s in loaded] == ["ocr", "compress", "protect"]
    assert loaded[1].params == {"level": "high"}
    assert be.list_sequences(str(folder)) == [(name, path)]
    be.delete_sequence(path)
    assert be.list_sequences(str(folder)) == []


def test_step_failure_reports_step(tmp_path, monkeypatch):
    a = mk(tmp_path / "in" / "a.pdf")

    def boom(data, level="medium"):
        raise RuntimeError("guasto")

    monkeypatch.setattr(be, "compress_pdf", boom)
    dest = tmp_path / "out"
    rep = be.run_job(None, items_for([a]), job(dest, ("watermark", {}), ("compress", {})), STARTED)
    assert rep.results[0].status == "error" and "Passo 2 (Comprimi)" in rep.results[0].message
    assert not dest.exists() or os.listdir(dest) == []


# ----------------------------------------------------------------- BAT-07
def _jpeg(path, size=(300, 200), dpi=(150, 150), orientation=None):
    from PIL import Image
    img = Image.new("RGB", size, (200, 30, 30))
    for x in range(size[0] // 3):
        for y in range(10):
            img.putpixel((x, y), (0, 0, 0))
    exif = None
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
    img.save(path, "JPEG", dpi=dpi, **({"exif": exif} if exif else {}))
    return str(path)


def test_create_pdf_original_size(tmp_path):
    src = tmp_path / "foto"
    src.mkdir()
    paths = [_jpeg(src / f"f{i}.jpg") for i in range(3)]
    out = tmp_path / "out"
    items = [be.analyze_file(p) for p in paths]
    settings = be.JobSettings(steps=[be.Step("create_pdf", {"page_size": "original"})], destination=str(out))
    errors, _w = be.validate_settings(items, settings)
    assert not errors
    report = be.run_job(None, items, settings)
    assert report.counts()["ok"] == 3
    for p in sorted(out.iterdir()):
        doc = pymupdf.open(str(p))
        assert doc.page_count == 1
        assert abs(doc[0].rect.width - 300 * 72 / 150) <= 1 and abs(doc[0].rect.height - 200 * 72 / 150) <= 1


def test_create_pdf_exif_and_tiff(tmp_path):
    from PIL import Image
    rotated = _jpeg(tmp_path / "ruotata.jpg", orientation=6)    # 90° in senso orario
    frames = [Image.new("RGB", (100, 140), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (9, 9, 9))]
    tiff = tmp_path / "multi.tif"
    frames[0].save(tiff, save_all=True, append_images=frames[1:])
    data = be.image_to_pdf(rotated, "original")
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert doc[0].rect.height > doc[0].rect.width     # 300x200 ruotata -> verticale
    assert pymupdf.open(stream=be.image_to_pdf(str(tiff)), filetype="pdf").page_count == 4


def test_create_single_pdf_with_bookmarks_and_office_errors(tmp_path, monkeypatch):
    a = _jpeg(tmp_path / "a.jpg")
    txt = tmp_path / "b.txt"
    txt.write_text("Nota di consegna\nOrologio revisionato", encoding="utf-8")
    docx = tmp_path / "c.docx"
    docx.write_bytes(b"non un vero docx")
    monkeypatch.setattr(be, "_office_command", lambda src, outdir: None)
    items = [be.analyze_file(p) for p in (a, str(txt), str(docx))]
    out = tmp_path / "out"
    settings = be.JobSettings(steps=[be.Step("create_pdf"), be.Step("merge", {"name": "Raccolta"})],
                              destination=str(out))
    _e, warnings = be.validate_settings(items, settings)
    assert any("LibreOffice" in w for w in warnings)
    report = be.run_job(None, items, settings)
    by = {os.path.basename(r.input): r for r in report.results}
    assert by["c.docx"].status == "error" and by["c.docx"].code == "DEP-02"
    # unione: con un file in errore si applica la politica di BAT-17
    single = be.JobSettings(steps=[be.Step("create_pdf"), be.Step("merge", {"name": "Raccolta"})],
                            destination=str(tmp_path / "out2"))
    report = be.run_job(None, items[:2], single)
    merged = tmp_path / "out2" / "Raccolta.pdf"
    doc = pymupdf.open(str(merged))
    assert [t[1] for t in doc.get_toc() if t[0] == 1] == ["a", "b"]
    assert "Nota di consegna" in doc[1].get_text()


def test_create_step_must_be_first(tmp_path):
    items = [be.QueueItem(path=str(tmp_path / "x.png"))]
    settings = be.JobSettings(steps=[be.Step("compress"), be.Step("create_pdf")], destination=str(tmp_path))
    errors, _w = be.validate_settings(items, settings)
    assert any("primo passo" in e for e in errors)


# ----------------------------------------------------------------- BAT-04 / BAT-05 (pausa, giornale, cronologia)
def _queue(tmp_path, n=4):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    return [be.analyze_file(build_pdf(src / f"f{i}.pdf", [["Pagina di prova", f"file {i}"]])) for i in range(n)]


def test_pause_and_resume_give_same_results(tmp_path):
    import threading
    import time as _t
    items = _queue(tmp_path)
    started = datetime(2026, 10, 28, 9, 0, 0)
    ref = be.JobSettings(steps=[be.Step("watermark", {"text": "COPIA"})], destination=str(tmp_path / "ref"))
    be.run_job(None, items, ref, started=started)
    control = be.JobControl()
    control.pause()
    out = tmp_path / "pausa"
    settings = be.JobSettings(steps=[be.Step("watermark", {"text": "COPIA"})], destination=str(out))
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("r", be.run_job(None, items, settings, started=started,
                                                                        control=control)))
    th.start()
    _t.sleep(0.4)
    assert not out.exists() or not os.listdir(out)
    control.resume()
    th.join(20)
    assert box["r"].counts()["ok"] == 4
    for name in os.listdir(tmp_path / "ref"):
        # identici byte per byte (operazioni deterministiche, data fissata)
        assert (tmp_path / "ref" / name).read_bytes() == (out / name).read_bytes()


def test_skip_current_file_only(tmp_path):
    items = _queue(tmp_path, 3)
    control = be.JobControl()

    class P:
        n = 0

        def update(self, f=None, t=None):
            self.n += 1
            if self.n == 2:            # durante il secondo file
                control.skip_current()

        def check(self):
            pass
    out = tmp_path / "out"
    report = be.run_job(P(), items, be.JobSettings(steps=[be.Step("compress")], destination=str(out)),
                        control=control)
    assert [r.status for r in report.results] == ["ok", "cancelled", "ok"]
    assert not report.cancelled
    assert len(os.listdir(out)) == 2


def _profile_files() -> dict[str, bytes]:
    out = {}
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        root = os.environ.get(var)
        for dirpath, _dirs, files in os.walk(root or "/nonexistent"):
            for f in files:
                with open(os.path.join(dirpath, f), "rb") as fh:
                    out[os.path.join(dirpath, f)] = fh.read()
    return out


def test_interrupted_job_leaves_no_journal(tmp_path):
    """Riservatezza: nessun giornale su disco con i percorsi della coda; dopo un'interruzione
    i file restano integri e la ripresa si fa solo dal rapporto in memoria della sessione."""
    items = _queue(tmp_path, 5)
    settings = be.JobSettings(steps=[be.Step("protect", {"user_password": "segreta", "owner_password": "capo"})],
                              destination=str(tmp_path / "out"))
    before = set(os.listdir(tmp_path))

    class Crash:
        n = 0

        def update(self, f=None, t=None):
            self.n += 1
            if self.n == 4:
                raise KeyboardInterrupt   # arresto improvviso a metà lavoro

        def check(self):
            pass
    try:
        be.run_job(Crash(), items, settings)
    except KeyboardInterrupt:
        pass
    assert set(os.listdir(tmp_path)) == before | {"out"}
    assert not any(b"f3.pdf" in data or str(tmp_path).encode() in data for data in _profile_files().values())
    assert not hasattr(be, "write_journal") and not hasattr(be, "interrupted_job")
    for f in os.listdir(tmp_path / "out"):
        assert pymupdf.open(str(tmp_path / "out" / f)).needs_pass   # nessun file corrotto
    # annullamento ordinario: i file non completati restano nel rapporto in memoria
    control = be.JobControl()

    class Cancel:
        n = 0

        def update(self, f=None, t=None):
            self.n += 1
            if self.n == 3:
                raise OperationCancelled()

        def check(self):
            pass
    report = be.run_job(Cancel(), items, be.JobSettings(steps=[be.Step("compress")],
                                                        destination=str(tmp_path / "out2")), control=control)
    pending = report.pending_items()
    assert pending and all(p in [it.path for it in items] for p in pending)
    again = be.run_job(None, [be.analyze_file(p) for p in pending],
                       be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "out2")))
    assert again.counts()["ok"] == len(pending)


def test_pdf_report_and_no_history_on_disk(tmp_path):
    items = _queue(tmp_path, 2)
    report = be.run_job(None, items, be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "o")))
    assert not hasattr(be, "save_history") and not hasattr(be, "list_history")
    assert be.HISTORY_LIMIT == 20
    assert not any(b"f0.pdf" in data for data in _profile_files().values())
    data = be.report_pdf(report)
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = doc[0].get_text()
    assert "Rapporto dell'elaborazione in blocco" in text and "f0.pdf" in text


# ----------------------------------------------------------------- BAT-15 indice e serie, BAT-17 modalità
def test_bates_index_and_series(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    items = [be.analyze_file(build_pdf(src / f"{n}.pdf", [["x"]] * k)) for n, k in (("a", 2), ("b", 3), ("c", 1))]
    settings = be.JobSettings(steps=[be.Step("bates", {"prefix": "ACME-", "digits": 6, "start": 1,
                                                       "series": "ACME"})],
                              destination=str(tmp_path / "out"), pattern="{bates_inizio}_{bates_fine}")
    csv_text = be.bates_index_csv(items, settings)
    assert "b.pdf,ACME-000003,ACME-000005" in csv_text
    report = be.run_job(None, items, settings)
    assert report.counts()["ok"] == 3
    assert os.path.exists(tmp_path / "out" / "ACME-000003_ACME-000005.pdf")
    store = str(tmp_path / "conf")
    be.record_bates_series(settings, items, report, store)
    assert be.load_series(store) == {"ACME": 6}
    nxt = be.JobSettings(steps=[be.Step("bates", {"prefix": "ACME-", "digits": 6, "series": "ACME",
                                                  "continue_series": True})], destination=str(tmp_path / "o2"))
    be.prepare_bates_series(nxt, store)
    be.run_job(None, items[:1], nxt)
    doc = pymupdf.open(str(tmp_path / "o2" / "a.pdf"))
    assert "ACME-000007" in doc[0].get_text()


def test_merge_one_file_per_folder(tmp_path):
    root = tmp_path / "in"
    for sub, names in (("uno", ("a", "b")), ("due", ("c",))):
        (root / sub).mkdir(parents=True)
        for n in names:
            build_pdf(root / sub / f"{n}.pdf", [[n]])
    paths, _ign = be.collect_inputs([str(root)])
    items = [be.analyze_file(p) for p in paths]
    out = tmp_path / "out"
    report = be.run_job(None, items, be.JobSettings(steps=[be.Step("merge", {"group": "folder"})],
                                                   destination=str(out)))
    assert sorted(os.listdir(out)) == ["due.pdf", "uno.pdf"]
    assert pymupdf.open(str(out / "uno.pdf")).page_count == 2
    assert report.counts()["ok"] == 3


def test_merge_omit_failed_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    good = [build_pdf(src / f"{n}.pdf", [[n]]) for n in ("a", "b")]
    bad = src / "rotto.pdf"
    bad.write_bytes(b"%PDF-1.4 rotto")
    items = [be.analyze_file(p) for p in (good[0], str(bad), good[1])]
    out = tmp_path / "out"
    stop = be.run_job(None, items, be.JobSettings(steps=[be.Step("merge")], destination=str(out)))
    assert not out.exists() or not os.listdir(out)
    assert any(r.code == "MRG-01" for r in stop.results)
    omit = be.run_job(None, items, be.JobSettings(steps=[be.Step("merge", {"on_error": "omit"})],
                                                 destination=str(out)))
    assert os.listdir(out) == ["Unione.pdf"]
    assert pymupdf.open(str(out / "Unione.pdf")).page_count == 2
    assert omit.results[0].status == "warning" and "rotto.pdf" in omit.results[0].message


def test_not_enough_space_blocks_start(tmp_path, monkeypatch):
    items = _queue(tmp_path, 2)
    settings = be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "out"))
    assert not be.validate_settings(items, settings)[0]
    import collections
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(be.shutil, "disk_usage", lambda p: Usage(100, 100, 10))
    errors, _w = be.validate_settings(items, settings)
    assert any("Spazio insufficiente" in e for e in errors)


# ----------------------------------------------------------------- pagine ruotate e ritagliate
@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_marks_on_rotated_and_cropped_pages(tmp_path, rot, crop):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "corpo", fontsize=12)
    if crop:
        page.set_cropbox(pymupdf.Rect(50, 60, 545, 800))
    page.set_rotation(rot)
    src = tmp_path / "in.pdf"
    doc.save(str(src))
    steps = [be.Step("watermark", {"text": "BOZZA"}), be.Step("bates", {"prefix": "ACME-"}),
             be.Step("header_footer", {"header_left": "TESTATA", "footer_center": "PIEDE"})]
    report = be.run_job(None, [be.analyze_file(str(src))], be.JobSettings(steps=steps,
                                                                          destination=str(tmp_path / "out")))
    assert report.counts()["ok"] + report.counts()["warning"] == 1
    out = pymupdf.open(str(tmp_path / "out" / "in.pdf"))[0]
    m = out.rotation_matrix
    pos = {}
    for w in out.get_text("words"):
        c = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * m
        pos[w[4]] = (c.x / out.rect.width, c.y / out.rect.height)
    assert abs(pos["BOZZA"][0] - 0.5) < 0.05 and abs(pos["BOZZA"][1] - 0.5) < 0.05
    assert pos["ACME-000001"][0] > 0.6 and pos["ACME-000001"][1] > 0.8
    assert pos["TESTATA"][0] < 0.4 and pos["TESTATA"][1] < 0.15
    assert abs(pos["PIEDE"][0] - 0.5) < 0.1 and pos["PIEDE"][1] > 0.85


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_background_covers_rotated_cropped_page(tmp_path, rot):
    import numpy as np
    lh = pymupdf.open()
    lh.new_page(width=595, height=842).insert_text((60, 60), "INTESTATA", fontsize=30)
    lh_path = tmp_path / "carta.pdf"
    lh.save(str(lh_path))
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.set_cropbox(pymupdf.Rect(20, 30, 575, 820))
    page.set_rotation(rot)
    out = pymupdf.open(stream=be.add_background(doc.tobytes(), color="#FF0000"), filetype="pdf")[0]
    pix = out.get_pixmap(dpi=20)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    assert ((a[:, :, 0] > 200) & (a[:, :, 1] < 50)).mean() > 0.97
    out2 = pymupdf.open(stream=be.add_background(doc.tobytes(), file=str(lh_path)), filetype="pdf")[0]
    w = next(x for x in out2.get_text("words") if x[4] == "INTESTATA")
    c = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * out2.rotation_matrix
    assert c.y / out2.rect.height < 0.15      # in alto nella pagina visualizzata


def test_merge_bookmarks_disambiguate_same_names(tmp_path):
    for sub in ("a", "b"):
        (tmp_path / "in" / sub).mkdir(parents=True)
        build_pdf(tmp_path / "in" / sub / "relazione.pdf", [[sub]])
    paths, _ign = be.collect_inputs([str(tmp_path / "in")])
    items = [be.analyze_file(p) for p in paths]
    be.run_job(None, items, be.JobSettings(steps=[be.Step("merge")], destination=str(tmp_path / "out")))
    toc = pymupdf.open(str(tmp_path / "out" / "Unione.pdf")).get_toc()
    assert [t[1] for t in toc] == ["relazione (a)", "relazione (b)"]


@pytest.mark.parametrize("rot", [0, 90])
def test_bates_white_box_on_scan(tmp_path, rot):
    import numpy as np
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(page.rect, fill=(0.3, 0.3, 0.3), width=0)     # «scansione» scura
    page.set_rotation(rot)
    data, last = be.add_bates(doc.tobytes(), 1, "FT-", 5, box=True)
    out = pymupdf.open(stream=data, filetype="pdf")[0]
    w = next(x for x in out.get_text("words") if x[4] == "FT-00001")
    clip = pymupdf.Rect(w[:4]) * out.rotation_matrix
    clip.normalize()
    pix = out.get_pixmap(dpi=72, clip=clip)
    a = np.frombuffer(pix.samples, np.uint8)
    assert (a > 240).mean() > 0.5        # sfondo bianco attorno alle cifre
    assert last == 1


def test_merge_keeps_original_bookmarks_nested(tmp_path):
    a = pymupdf.open()
    for i in range(3):
        a.new_page().insert_text((72, 100), f"articolo {i + 1}", fontsize=12)
    a.set_toc([[1, "Premesse", 1], [1, "Clausole", 2], [2, "Pagamento", 3]])
    a.save(str(tmp_path / "contratto.pdf"))
    build_pdf(tmp_path / "allegato.pdf", [["allegato"]])
    items = [be.analyze_file(str(tmp_path / n)) for n in ("contratto.pdf", "allegato.pdf")]
    be.run_job(None, items, be.JobSettings(steps=[be.Step("merge")], destination=str(tmp_path / "out")))
    toc = pymupdf.open(str(tmp_path / "out" / "Unione.pdf")).get_toc()
    assert toc == [[1, "contratto", 1], [2, "Premesse", 1], [2, "Clausole", 2], [3, "Pagamento", 3],
                   [1, "allegato", 4]]



def test_watermark_shares_marking_with_pages_module(tmp_path):
    """BAT-12 / PAG-36: le filigrane in blocco e quelle di Pagine si sostituiscono e si rimuovono a vicenda."""
    from foliant.modules.pages import stamp
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    doc = pymupdf.open(a)
    stamp.add_watermark(doc, None, text="UNLIMITED")
    from_pages = doc.tobytes()
    doc.close()
    replaced = be.add_watermark(from_pages, "UNLIMITED", replace=True)
    text = pymupdf.open(stream=replaced, filetype="pdf").load_page(0).get_text()
    assert text.count("UNLIMITED") == 1
    # filigrana messa in blocco, rimossa dal modulo Pagine
    batch = be.add_watermark(open(a, "rb").read(), "RISERVATO")
    doc = pymupdf.open(stream=batch, filetype="pdf")
    assert stamp.list_watermarks(doc) == {0: ["watermark"], 1: ["watermark"]}
    assert stamp.remove_watermarks(doc) == 2
    assert "RISERVATO" not in doc[0].get_text()
    # anche lo sfondo a colore e i Bates usano la marcatura comune
    marked = be.add_bates(be.add_background(open(a, "rb").read(), color="#EEEEEE"), 1, "X-")[0]
    doc = pymupdf.open(stream=marked, filetype="pdf")
    assert sorted(stamp.list_watermarks(doc)[0]) == ["background", "bates"]


def test_legacy_marks_still_removed(tmp_path):
    """I file prodotti prima della marcatura comune restano ripulibili."""
    a = mk(tmp_path / "in" / "a.pdf", pages=1)
    with be.ENGINE_LOCK:
        doc = pymupdf.open(a)
        page = doc[0]
        before = page.get_contents()
        page.insert_text((72, 400), "VECCHIA", fontsize=40)
        be._mark_new_contents(doc, page, before, "watermark")
        legacy = doc.tobytes()
    out = be.add_watermark(legacy, "NUOVA", replace=True)
    t = pymupdf.open(stream=out, filetype="pdf").load_page(0).get_text()
    assert "NUOVA" in t and "VECCHIA" not in t


def test_header_and_numbers_share_marking_and_dont_replace_each_other(tmp_path):
    from foliant.modules.pages import stamp
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    data = be.add_header_footer(open(a, "rb").read(), {"top-left": "CONTRATTO {nome}"}, values={"nome": "X"})
    data = be.add_page_numbers(data)
    doc = pymupdf.open(stream=data, filetype="pdf")
    t = doc[0].get_text()
    assert "CONTRATTO X" in t and "Pagina 1 di 2" in t      # i numeri non tolgono l'intestazione
    assert sorted(stamp.list_watermarks(doc)[0]) == ["headerfooter", "numbers"]
    assert stamp.remove_watermarks(doc, None, "all") == 4
    assert "CONTRATTO" not in doc[0].get_text()


def _scanned_letter(path, text):
    src = pymupdf.open()
    src.new_page().insert_text((72, 120), text, fontsize=22)
    img = src[0].get_pixmap(dpi=200).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=img)
    doc.save(str(path))
    return str(path)


def test_ocr_step_with_real_ocr_module(tmp_path):
    """Integrazione con il vero modulo OCR (se tesseract è installato)."""

    if find_tool("tesseract") is None or be._ocr_function() is None:
        pytest.skip("OCR non disponibile")
    src = _scanned_letter(tmp_path / "scansione.pdf", "Fattura numero 4521")
    report = be.run_job(None, [be.analyze_file(src)], be.JobSettings(
        steps=[be.Step("ocr", {"languages": "ita+eng"})], destination=str(tmp_path / "out")))
    assert report.results[0].status in ("ok", "warning"), report.results[0].message
    text = pymupdf.open(str(tmp_path / "out" / "scansione.pdf"))[0].get_text()
    assert "4521" in text


@pytest.mark.parametrize("fmt,ext,count", [("txt", ".txt", 1), ("png", ".png", 2), ("docx", ".docx", 1)])
def test_convert_step_with_real_convert_module(tmp_path, fmt, ext, count):
    """Integrazione con il vero modulo Converti."""
    if be._convert_function() is None:
        pytest.skip("modulo Converti non disponibile")
    src = mk(tmp_path / "in" / "scheda.pdf", pages=2)
    report = be.run_job(None, [be.analyze_file(src)], be.JobSettings(
        steps=[be.Step("convert", {"format": fmt, "dpi": 72})], destination=str(tmp_path / "out")))
    assert report.results[0].status in ("ok", "warning"), report.results[0].message
    produced = sorted(os.listdir(tmp_path / "out"))
    assert len(produced) == count and all(n.endswith(ext) for n in produced)


def test_bates_font_choice(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=1)
    data, _last = be.add_bates(open(a, "rb").read(), 1, "ACME-", font="cobo")
    page = pymupdf.open(stream=data, filetype="pdf")[0]
    fonts = {sp["font"] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
             for sp in ln["spans"] if "ACME" in sp["text"]}
    assert any("Courier" in f for f in fonts)


def test_pdfa_step_reports_conformance(tmp_path, monkeypatch):
    calls = []

    def fake(data, level):
        calls.append(level)
        doc = pymupdf.open(stream=data, filetype="pdf")
        doc.set_metadata({"title": "archivio"})
        out = doc.tobytes()
        ok = "rotto" not in (pymupdf.open(stream=data, filetype="pdf")[0].get_text())
        return out, {"level": level, "errors": [] if ok else ["font non incorporato: Helvetica"],
                     "compliant": ok}
    monkeypatch.setattr(be, "_pdfa_function", lambda: fake)
    good = mk(tmp_path / "in" / "fattura.pdf", pages=1)
    bad = tmp_path / "in" / "scheda.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), "rotto", fontsize=12)
    d.save(str(bad))
    items = [be.analyze_file(p) for p in (good, str(bad))]
    rep = be.run_job(None, items, be.JobSettings(steps=[be.Step("pdfa", {"level": "2b"})],
                                                 destination=str(tmp_path / "out")))
    assert calls == ["2b", "2b"]
    assert rep.results[0].status == "ok" and "conforme" in rep.results[0].message
    assert rep.results[1].status == "error" and rep.results[1].code == "PDFA-02"
    assert "font non incorporato" in rep.results[1].message
    assert os.listdir(tmp_path / "out") == ["fattura.pdf"]
    rep = be.run_job(None, items[1:], be.JobSettings(steps=[be.Step("pdfa", {"save_anyway": True})],
                                                     destination=str(tmp_path / "out2")))
    assert rep.results[0].status == "warning" and os.listdir(tmp_path / "out2") == ["scheda.pdf"]


def test_pdfa_step_without_ghostscript_is_clear(tmp_path):
    if be._pdfa_function() is None:
        pytest.skip("modulo Converti non disponibile")

    if find_tool("gs"):
        pytest.skip("Ghostscript presente: verificato dal modulo Converti")
    a = mk(tmp_path / "in" / "a.pdf", pages=1)
    rep = be.run_job(None, [be.analyze_file(a)], be.JobSettings(steps=[be.Step("pdfa")],
                                                               destination=str(tmp_path / "out")))
    assert rep.results[0].status == "error" and rep.results[0].code == "PDFA-01"
    assert "Ghostscript" in rep.results[0].message


def test_parallel_processing_same_results(tmp_path):
    """BAT-04: Comprimi su 10 file con parallelismo 4 e 1 dà gli stessi risultati."""
    src = tmp_path / "src"
    src.mkdir()
    items = [be.analyze_file(build_pdf(src / f"f{i}.pdf", [page_lines(i), page_lines(i + 1)])) for i in range(10)]
    started = datetime(2026, 10, 28, 9, 0, 0)
    steps = [be.Step("watermark", {"text": "COPIA"}), be.Step("compress")]
    one = be.run_job(None, items, be.JobSettings(steps=steps, destination=str(tmp_path / "uno"),
                                                 pattern="{nome}_{contatore:2}"), started)
    four = be.run_job(None, items, be.JobSettings(steps=steps, destination=str(tmp_path / "quattro"),
                                                  pattern="{nome}_{contatore:2}", workers=4), started)
    assert [r.status for r in four.results] == [r.status for r in one.results]
    assert all(r.status in ("ok", "warning") for r in four.results)
    assert sorted(os.listdir(tmp_path / "uno")) == sorted(os.listdir(tmp_path / "quattro"))
    for name in os.listdir(tmp_path / "uno"):
        assert (tmp_path / "uno" / name).read_bytes() == (tmp_path / "quattro" / name).read_bytes()


def test_parallel_errors_and_cancel(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    paths = [build_pdf(src / f"f{i}.pdf", [page_lines(i)]) for i in range(6)]
    (src / "f3.pdf").write_bytes(b"%PDF-1.4 rotto")
    items = [be.analyze_file(p) for p in paths]
    rep = be.run_job(None, items, be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "o"),
                                                 workers=3))
    assert [r.status for r in rep.results].count("error") == 1 and rep.results[3].status == "error"

    class Cancel:
        cancelled = False
        n = 0

        def update(self, f=None, t=None):
            self.n += 1
            if self.n == 3:
                self.cancelled = True
            self.check()

        def check(self):
            if self.cancelled:
                raise OperationCancelled()
    out = tmp_path / "c"
    rep = be.run_job(Cancel(), items, be.JobSettings(steps=[be.Step("compress")], destination=str(out), workers=3))
    assert rep.cancelled
    assert all(not n.startswith(".") for n in (os.listdir(out) if out.exists() else []))


@pytest.mark.parametrize("workers", [1, 3])
def test_compress_does_not_discard_other_steps(tmp_path, workers):
    """Filigrana > Comprimi > Proteggi su PDF piccoli: protezione e filigrana restano anche se
    la compressione non riduce il file."""
    src = tmp_path / "src"
    src.mkdir()
    paths = [build_pdf(src / f"f{i}.pdf", [["testo"], ["testo"], ["testo"]]) for i in range(2)]
    items = [be.analyze_file(p) for p in paths]
    steps = [be.Step("watermark", {"text": "BOZZA"}), be.Step("compress"),
             be.Step("protect", {"user_password": "aperta", "owner_password": "capo"})]
    out = tmp_path / "out"
    rep = be.run_job(None, items, be.JobSettings(steps=steps, destination=str(out), workers=workers))
    for r in rep.results:
        assert r.status in ("ok", "warning")
        doc = pymupdf.open(r.outputs[0])
        assert doc.needs_pass and doc.authenticate("aperta")
        assert "BOZZA" in doc[0].get_text()
    # Comprimi > Proteggi: sempre protetto
    rep = be.run_job(None, items[:1], be.JobSettings(steps=steps[1:], destination=str(tmp_path / "o2"),
                                                     workers=workers))
    assert pymupdf.open(rep.results[0].outputs[0]).needs_pass


def test_defaults_and_names_in_english(tmp_path):
    """NFR-25: in inglese le operazioni in blocco scrivono testi e nomi in inglese."""
    from foliant.i18n import set_language
    from foliant.modules.tools.translations import EN
    from foliant.api import add_translations
    add_translations("en", EN)
    a = mk(tmp_path / "in" / "contratto.pdf", pages=3)
    set_language("en")
    try:
        item = be.analyze_file(a)
        for action, expected in (("watermark", "CONFIDENTIAL"), ("page_numbers", "Page 1 of 3"),
                                 ("header_footer", "Page 1 of 3")):
            settings = be.JobSettings(steps=[be.Step(action)], destination_mode="same")
            settings.pattern = be.default_pattern(settings)
            rep = be.run_job(None, [item], settings)
            out = rep.results[0].outputs[0]
            assert expected in pymupdf.open(out)[0].get_text()
            assert "RISERVATO" not in pymupdf.open(out)[0].get_text()
            assert "Pagina" not in pymupdf.open(out)[0].get_text()
        assert be.default_pattern(be.JobSettings(steps=[be.Step("watermark")], destination_mode="same")) \
            == "{name}_watermark"
        assert be.Step("page_numbers").merged_params()["text"] == "Page {page} of {total}"
        st = be.JobSettings(steps=[be.Step("compress")])
        assert be.render_name("{name}_{operation}_{counter:2}", a, 0, st, __import__("datetime").datetime.now()) \
            == "contratto_compressed_01.pdf"
        assert not be.validate_pattern("{name}_{page:3}_{date}")
        assert be.Step("merge").merged_params()["name"] == "Merged"
        assert be.Step("ocr").merged_params()["languages"] == "ita+eng"
    finally:
        set_language("it")
    assert be.Step("watermark").merged_params()["text"] == "RISERVATO"


# ----------------------------------------------------------------- BAT-16
def _with_marked_header(path):
    """PDF con intestazione marcata come artefatto da un «altro programma» e una filigrana-annotazione."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 300), "Corpo del contratto", fontsize=12)
    xref = page.get_contents()[0]
    body = doc.xref_stream(xref)
    marked = (b"/Artifact <</Type /Pagination /Subtype /Header>> BDC BT /helv 9 Tf 72 30 Td (TESTATA ALTRUI) Tj ET EMC\n"
              + body)
    doc.update_stream(xref, marked)
    page.clean_contents()
    doc.save(str(path))
    return str(path)


def test_remove_marks_scope_and_counts(tmp_path):
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    data = be.add_header_footer(be.add_watermark(open(a, "rb").read(), "BOZZA"), {"bottom-center": "PIEDE"})
    # solo la filigrana: il piè resta
    w = tmp_path / "w.pdf"
    w.write_bytes(data)
    rep = be.run_job(None, [be.analyze_file(str(w))], be.JobSettings(
        steps=[be.Step("remove_marks", {"watermark": True})], destination=str(tmp_path / "o1")))
    out = pymupdf.open(rep.results[0].outputs[0])
    assert all("BOZZA" not in p.get_text() and "PIEDE" in p.get_text() for p in out)
    assert "2 filigrane" in rep.results[0].message
    # intestazione marcata da un altro programma: rimossa solo con l'ambito «tutti»
    other = _with_marked_header(tmp_path / "altro.pdf")
    item = be.analyze_file(other)
    rep = be.run_job(None, [item], be.JobSettings(steps=[be.Step("remove_marks", {"watermark": False,
                                                                                 "header_footer": True})],
                                                  destination=str(tmp_path / "o2")))
    assert rep.results[0].status == "skipped" and "nessun elemento riconoscibile" in rep.results[0].message
    rep = be.run_job(None, [item], be.JobSettings(steps=[be.Step("remove_marks", {
        "watermark": False, "header_footer": True, "scope": "marked"})], destination=str(tmp_path / "o3")))
    text = pymupdf.open(rep.results[0].outputs[0])[0].get_text()
    assert "TESTATA ALTRUI" not in text and "Corpo del contratto" in text


def test_remove_watermark_visually_identical(tmp_path):
    from foliant.modules.tools import compare_engine as ce
    a = mk(tmp_path / "in" / "a.pdf", pages=2)
    original = open(a, "rb").read()
    marked = be.add_watermark(original, "BOZZA")
    cleaned, n = be.remove_marks(marked, {"watermark"})
    assert n == 2
    r = ce.run_compare(None, ce.SideInput(original, "a"), ce.SideInput(cleaned, "b"),
                       ce.CompareOptions(mode=ce.MODE_VISUAL, dpi=72))
    assert r.differences == []


# ----------------------------------------------------------------- BAT-02 disco pieno
def test_disk_full_pauses_and_retries(tmp_path, monkeypatch):
    import errno
    import threading
    import time as _t
    items = _queue(tmp_path, 3)
    real = be.atomic_write
    fails = {"n": 1}

    def flaky(path, data):
        if fails["n"] and os.path.basename(str(path)) == "f1.pdf":
            fails["n"] -= 1
            raise be.DocumentError("Salvataggio non riuscito") from OSError(errno.ENOSPC, "No space left")
        return real(path, data)
    monkeypatch.setattr(be, "atomic_write", flaky)
    control = be.JobControl()
    box = {}
    out = tmp_path / "out"
    th = threading.Thread(target=lambda: box.setdefault("r", be.run_job(
        None, items, be.JobSettings(steps=[be.Step("compress")], destination=str(out)), control=control)))
    th.start()
    for _i in range(200):
        if control.disk_full:
            break
        _t.sleep(0.02)
    assert control.disk_full and control.paused
    assert sorted(os.listdir(out)) == ["f0.pdf"]            # in pausa sul secondo file
    control.resume()                                       # «spazio liberato»
    th.join(20)
    assert all(r.status in ("ok", "warning") for r in box["r"].results)
    assert sorted(os.listdir(out)) == ["f0.pdf", "f1.pdf", "f2.pdf"]


def test_disk_full_without_control_is_file_error(tmp_path, monkeypatch):
    import errno
    items = _queue(tmp_path, 2)
    monkeypatch.setattr(be, "atomic_write", lambda p, d: (_ for _ in ()).throw(
        OSError(errno.ENOSPC, "No space left")))
    rep = be.run_job(None, items, be.JobSettings(steps=[be.Step("compress")], destination=str(tmp_path / "o")))
    assert [r.code for r in rep.results] == ["DISK-01", "DISK-01"]


def test_numbers_and_dates_follow_language(tmp_path):
    """C11 (NFR-26): in inglese punto decimale e date all'americana; in italiano la virgola."""
    from datetime import datetime
    from foliant.api import add_translations
    from foliant.i18n import set_language
    from foliant.modules.tools import batch_ui as bu
    from foliant.modules.tools import compare_engine as ce
    from foliant.modules.tools.translations import EN
    add_translations("en", EN)
    assert bu._human_size(1536) == "1,5 KB" and ce._fmt_num(2.5) == "2,5"
    set_language("en")
    try:
        assert bu._human_size(1536) == "1.5 KB" and ce._fmt_num(2.5) == "2.5"
        rep = be.JobReport([be.FileResult("a.pdf", status="ok")], [], datetime(2026, 10, 28, 14, 30, 5))
        rep.finished = datetime(2026, 10, 28, 14, 30, 7)
        text = pymupdf.open(stream=be.report_pdf(rep))[0].get_text()
        assert "10/28/2026" in text and "28/10/2026" not in text
    finally:
        set_language("it")
