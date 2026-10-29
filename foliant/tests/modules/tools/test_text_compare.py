"""Confronto di testo semplice (CMP-13)."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from foliant.api import DocumentError
from foliant.modules.tools import compare_engine as ce
from foliant.modules.tools import text_compare as tc


def test_single_word_difference():
    a = "Il cronografo è in acciaio.\nGaranzia di due anni.\n"
    b = "Il cronografo è in titanio.\nGaranzia di due anni.\n"
    diffs = tc.compare_texts(a, b, "word")
    assert len(diffs) == 1
    d = diffs[0]
    assert d.kind == ce.MODIFIED and d.text_a == "acciaio." and d.text_b == "titanio."
    assert d.line_a == 1 and a[d.span_a[0]:d.span_a[1]] == "acciaio."


def test_line_and_char_granularity_and_options():
    a = "uno\ndue\ntre"
    b = "uno\nDUE\ntre\nquattro"
    lines = tc.compare_texts(a, b, "line")
    assert [d.kind for d in lines] == [ce.MODIFIED, ce.ADDED]
    assert [d.kind for d in tc.compare_texts(a, b, "line", ignore_case=True)] == [ce.ADDED]
    chars = tc.compare_texts("abc", "abd", "char")
    assert len(chars) == 1 and chars[0].text_a == "c" and chars[0].text_b == "d"
    assert tc.compare_texts("a  b", "a b", "line", ignore_whitespace=True) == []


def test_crlf_vs_lf_is_identical():
    assert tc.compare_texts("riga 1\r\nriga 2\r\n", "riga 1\nriga 2\n", "line") == []
    assert tc.compare_texts("riga 1\r\nriga 2", "riga 1\nriga 2", "word") == []


@pytest.mark.parametrize("a,b", [
    ("alfa\nbeta\ngamma\n", "alfa\nBETA\ngamma\ndelta\n"),
    ("senza a capo finale", "senza a capo finale\nmodificato"),
    ("x\ny\n", "x\ny"),
])
@pytest.mark.skipif(sys.platform == "win32", reason="«patch» di Git per Windows (MSYS) rifiuta i file del test "
                    "(uscita 3) anche quando il diff è corretto: verifica fatta con GNU patch su Linux")
def test_unified_diff_applies_with_patch(tmp_path, a, b):
    if not shutil.which("patch"):
        pytest.skip("patch non disponibile")
    src = tmp_path / "a.txt"
    src.write_text(a, encoding="utf-8", newline="")
    diff = tmp_path / "d.diff"
    diff.write_text(tc.unified_diff(a, b), encoding="utf-8", newline="")
    # nomi relativi: il «patch» di Git per Windows (MSYS) non accetta sempre i percorsi «C:\\…»
    subprocess.run(["patch", "-s", src.name, diff.name], check=True, cwd=tmp_path)
    assert src.read_text(encoding="utf-8") == b


def test_decode_text():
    assert tc.decode_text("città".encode("utf-8")) == "città"
    assert tc.decode_text(b"\xef\xbb\xbfciao") == "ciao"
    assert tc.decode_text("città".encode("utf-16")) == "città"
    assert tc.decode_text("città".encode("cp1252")) == "città"
    assert tc.decode_text("città".encode("iso-8859-15"), "iso-8859-15") == "città"
    with pytest.raises(DocumentError):
        tc.decode_text(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")


def test_dialog_compare_and_exports(window, tmp_path, sample_pdf):
    dlg = tc.TextCompareDialog(window.ctx, window)
    f = tmp_path / "a.txt"
    f.write_bytes("Prezzo: 100 euro\r\n".encode("utf-8"))
    assert dlg.side_a.load_path(str(f))
    dlg.side_b.edit.setPlainText("Prezzo: 120 euro\n")
    diffs = dlg.run()
    assert len(diffs) == 1 and dlg.list.count() == 1
    dlg.list.setCurrentRow(0)
    assert dlg.side_a.edit.extraSelections()
    out = dlg.export_unified(str(tmp_path / "x.diff"))
    assert "-Prezzo: 100 euro" in open(out, encoding="utf-8").read()
    pdf = dlg.export_pdf(str(tmp_path / "x.pdf"))
    import pymupdf
    assert "Confronto di testo" in pymupdf.open(pdf)[0].get_text()
    window.open_path(sample_pdf)
    assert dlg.side_b.from_pdf()
    assert "Pagina 1" in dlg.side_b.edit.toPlainText()
    bad = tmp_path / "bin.dat"
    bad.write_bytes(b"\x00\x01\x02" * 50)
    errors = []
    window.ctx.error = lambda t, m: errors.append(m)
    assert not dlg.side_a.load_path(str(bad)) and errors
    dlg.close()


def test_open_file_uses_core_dialog(window, tmp_path, monkeypatch):
    """«Apri file» passa dal dialogo del nucleo (senza cronologia), non da QFileDialog."""
    src = tmp_path / "appunti-riservati.txt"
    src.write_text("uno\ndue\n", encoding="utf-8")
    asked = []
    monkeypatch.setattr(window.ctx, "ask_open_paths", lambda *a, **k: asked.append((a, k)) or [str(src)])
    dlg = tc.TextCompareDialog(window.ctx, window)
    dlg.side_a.choose_file()
    assert asked and asked[0][1].get("multiple") is False
    assert "uno" in dlg.side_a.edit.toPlainText()
    dlg.close()
