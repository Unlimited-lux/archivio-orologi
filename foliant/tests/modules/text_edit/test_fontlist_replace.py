"""Font del documento › Sostituisci: solo il font scelto, un solo oggetto font, nessuno spazio residuo."""

from __future__ import annotations

import os
import subprocess
from collections import Counter

import pymupdf
import pytest

from foliant.api import Document
from foliant.core.platform import find_tool
from foliant.modules.text_edit import fontlist, fonts

FATTURA = """<html><body style="font-family:Caladea; font-size:11pt">
<h2>Orologeria Bianchi — Fattura n. 117</h2>
<p><b>Cliente:</b> Mario Rossi, via Garibaldi 12, Torino</p>
<p><i>Documento emesso ai sensi del regime del margine.</i></p>
<table style="width:100%; border-collapse:collapse">
<tr><td><b>Descrizione</b></td><td><b>Q.tà</b></td><td style="text-align:right"><b>Prezzo</b></td></tr>
<tr><td>Omega Speedmaster 3570.50</td><td>1</td><td style="text-align:right">€ 4.200,00</td></tr>
<tr><td>Revisione completa del movimento</td><td>1</td><td style="text-align:right">€ 450,00</td></tr>
</table>
<p><b>Totale documento: € 4.650,00</b></p>
<p>Pagamento mediante bonifico bancario entro trenta giorni. <i>Garanzia di dodici mesi</i> sul movimento.</p>
<p style="page-break-before:always"><b>Condizioni di vendita</b></p>
<p>L'orologio è venduto <b>revisionato e garantito</b>, con scatola e documenti originali.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def fattura(tmp_path_factory) -> str:
    soffice = find_tool("soffice")
    if not soffice:
        pytest.skip("LibreOffice non disponibile")
    if not fonts.find_family("Caladea", True, False)[1]:
        pytest.skip("font Caladea non installato")
    out = tmp_path_factory.mktemp("fattura_caladea")
    src = out / "fattura.html"
    src.write_text(FATTURA, encoding="utf-8")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{out}/lo", "--convert-to",
                    "pdf:writer_pdf_Export", "--outdir", str(out), str(src)], capture_output=True, timeout=180)
    pdf = out / "fattura.pdf"
    if not pdf.exists():
        pytest.skip("conversione LibreOffice non riuscita")
    return str(pdf)


def chars_by_font(page) -> Counter:
    c: Counter = Counter()
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                c[sp["font"]] += len(sp["text"].replace(" ", ""))
    return c


def test_replace_only_the_chosen_font(fattura, tmp_path):
    d = Document.open(fattura)
    with d.lock:
        names = {e.name for e in fontlist.document_fonts(d.pdf)}
        before = [chars_by_font(d.pdf[i]) for i in range(d.pdf.page_count)]
        text_before = [d.pdf[i].get_text("words") for i in range(d.pdf.page_count)]
    assert {"Caladea-Bold", "Caladea-Regular", "Caladea-Italic"} <= names
    assert d.pdf.page_count == 2
    with d.change("Sostituisci font") as pdf:
        fontlist.replace_font(pdf, "Caladea-Bold", "Liberation Sans")
    out = tmp_path / "out.pdf"
    d.save(out)
    re = pymupdf.open(str(out))
    for i in range(re.page_count):
        after = chars_by_font(re[i])
        # regolare e corsivo restano in Caladea, con lo stesso numero di caratteri
        assert after["Caladea-Regular"] == before[i]["Caladea-Regular"]
        assert after["Caladea-Italic"] == before[i]["Caladea-Italic"]
        assert after["Caladea-Bold"] == 0
        assert "LiberationSans" not in after and "LiberationSans-Italic" not in after
        assert after["LiberationSans-Bold"] == before[i]["Caladea-Bold"]
        assert sorted(w[4] for w in re[i].get_text("words")) == sorted(w[4] for w in text_before[i])
    # un solo oggetto font nuovo per tutto il documento
    new = {f[0] for i in range(re.page_count) for f in re[i].get_fonts(full=True) if "Liberation Sans" in f[3]}
    assert len(new) == 1
    names_after = {e.name for e in fontlist.document_fonts(re)}
    assert "Caladea-Bold" not in names_after
    # il file non si gonfia: al più il sottoinsieme di un font in più
    assert os.path.getsize(out) < os.path.getsize(fattura) + 25_000


def test_replace_regular_leaves_no_space_in_old_font(fattura, tmp_path):
    d = Document.open(fattura)
    with d.change("Sostituisci font") as pdf:
        fontlist.replace_font(pdf, "Caladea-Regular", "DejaVu Serif")
    out = tmp_path / "out.pdf"
    d.save(out)
    re = pymupdf.open(str(out))
    for i in range(re.page_count):
        assert chars_by_font(re[i])["Caladea-Regular"] == 0
    assert "Caladea-Regular" not in {e.name for e in fontlist.document_fonts(re)}
    new = {f[0] for i in range(re.page_count) for f in re[i].get_fonts(full=True) if "DejaVu" in f[3]}
    assert len(new) == 1


def test_uses_font_matches_postscript_name():
    from foliant.modules.text_edit.model import Style
    st = lambda name: Style(font=name, size=11)   # noqa: E731
    assert fontlist._uses_font(st("Caladea-Bold"), "Caladea-Bold")
    assert not fontlist._uses_font(st("Caladea-Regular"), "Caladea-Bold")
    assert not fontlist._uses_font(st("Caladea-Italic"), "Caladea-Bold")
    assert not fontlist._uses_font(st("Caladea-BoldItalic"), "Caladea-Bold")
    assert fontlist._uses_font(st("LiberationSerif"), "Liberation Serif Regular")
    assert fontlist._uses_font(st("Arial,Bold"), "Arial-BoldMT")
