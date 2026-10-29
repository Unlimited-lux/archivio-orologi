"""Controlla i risultati della prova dell'installatore. Eseguito dal Foliant installato:
    foliant-cli.exe -I -c "exec(open(r'…\\verifica_risultati.py', encoding='utf-8').read())" CARTELLA

CARTELLA contiene:
    originale.pdf        la scansione di prova, intatta;
    sessione.pdf         lo stesso PDF aperto, modificato e salvato dall'app (sessione.py);
    docx/                OCR in italiano + conversione in Word (foliant-cli blocco);
    pdf/                 OCR in italiano + compressione + PDF/A con Ghostscript (foliant-cli blocco);
    gs-ebook.pdf         compressione diretta con il Ghostscript incluso (-dPDFSETTINGS=/ebook).
I nomi effettivi dei file non compaiono nei messaggi: si stampano solo i controlli.
"""

import glob
import os
import sys

import docx
import pymupdf

sys.stdout.reconfigure(errors="replace")         # console di Windows in cp1252
base = sys.argv[1]
ATTESE = ("perizia", "orologio", "città", "perché", "garanzia", "ginevra")
errori = []


def controlla(condizione, messaggio):
    print(("ok      " if condizione else "ERRORE  ") + messaggio)
    if not condizione:
        errori.append(messaggio)


def unico(pattern):
    trovati = glob.glob(os.path.join(base, pattern))
    controlla(len(trovati) == 1, f"un solo risultato per {pattern}")
    return trovati[0] if trovati else None


originale = os.path.join(base, "originale.pdf")
size_originale = os.path.getsize(originale)
with pymupdf.open(originale) as doc:
    controlla(not doc[0].get_text().strip(), "la scansione di prova non ha testo")

with pymupdf.open(os.path.join(base, "sessione.pdf")) as doc:
    controlla("Controllato con Foliant" in doc[0].get_text(), "sessione: modifica salvata nel PDF")

percorso = unico("docx/*.docx")
if percorso:
    testo = "\n".join(p.text for p in docx.Document(percorso).paragraphs).lower()
    mancanti = [w for w in ATTESE if w not in testo]
    controlla(not mancanti, f"DOCX: testo italiano riconosciuto (mancano: {mancanti})")

percorso = unico("pdf/*.pdf")
if percorso:
    with pymupdf.open(percorso) as doc:
        testo = doc[0].get_text().lower()
        meta = doc.metadata or {}
        mancanti = [w for w in ATTESE if w not in testo]
        controlla(not mancanti, f"PDF: livello OCR in italiano (mancano: {mancanti})")
        controlla("ita" in (meta.get("keywords") or ""), "PDF: OCR eseguito con la lingua ita")
        controlla("ghostscript" in (meta.get("producer") or "").lower(),
                  f"PDF/A prodotto da Ghostscript ({meta.get('producer')})")
        catalogo = doc.pdf_catalog()
        controlla(doc.xref_get_key(catalogo, "Metadata")[0] == "xref", "PDF/A: metadati XMP presenti")
    size = os.path.getsize(percorso)
    print(f"        dimensione: {size_originale:,} -> {size:,} byte")
    controlla(size < size_originale, "PDF compresso più piccolo dell'originale")

percorso = os.path.join(base, "gs-ebook.pdf")
with pymupdf.open(percorso) as doc:
    controlla(doc.page_count == 1, "Ghostscript incluso: compressione /ebook leggibile")
print(f"        Ghostscript /ebook: {size_originale:,} -> {os.path.getsize(percorso):,} byte")
controlla(os.path.getsize(percorso) < size_originale, "Ghostscript incluso: file più piccolo")

# LibreOffice resta esterno e facoltativo: se manca, l'app spiega come installarlo
from foliant.core import platform
if platform.find_tool("soffice") is None:
    controlla("libreoffice.org" in platform.missing_tool_message("soffice"),
              "LibreOffice assente: il messaggio indica dove scaricarlo")
else:
    print("        LibreOffice presente sulla macchina di prova")

if errori:
    raise SystemExit(f"{len(errori)} controlli non riusciti")
print("risultati: ok")
