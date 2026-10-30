"""Conversione in Word con l'impaginazione «Fedele» (pdf2docx), la stessa di Esporta › Word ›
Impaginazione fedele: `foliant-cli blocco` usa solo «Testo scorrevole», qui si chiama il motore di
conversione del Foliant installato.
    foliant-cli.exe -I -c "exec(open(r'…\\converti_fedele.py', encoding='utf-8').read())" FILE.pdf CARTELLA

Scrive CARTELLA\\fedele.docx; i nomi dei file non compaiono nei messaggi.
"""

import os
import sys

from foliant.modules.convert.engine import run_conversion

sorgente, cartella = sys.argv[1], sys.argv[2]
os.makedirs(cartella, exist_ok=True)
risultato = run_conversion(sorgente, os.path.join(cartella, "fedele.docx"), "docx", layout="faithful")
for avviso in risultato.warnings:
    print("avviso:", avviso)
print(f"Word con impaginazione fedele: {len(risultato.paths)} file")
