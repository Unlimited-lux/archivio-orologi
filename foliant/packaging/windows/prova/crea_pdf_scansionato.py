"""Crea il PDF di prova per l'installatore: una pagina A4 «scansionata» (solo immagine, 300 dpi)
con un testo italiano, senza livello di testo.

Uso: python crea_pdf_scansionato.py destinazione.pdf
Gira con il Python della macchina di prova (pymupdf, Pillow), non con il Foliant installato.
"""

import io
import os
import sys

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RIGHE = (
    "Perizia di un orologio da polso",
    "",
    "La cassa in acciaio è in buone condizioni, con",
    "segni d'uso leggeri sulla lunetta. Il quadrante",
    "è originale e la città di produzione è Ginevra.",
    "Il movimento automatico funziona perché è stato",
    "revisionato. Garanzia di dodici mesi.",
)

FONT_CANDIDATI = (
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def immagine(dpi: int = 300) -> Image.Image:
    larghezza, altezza = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("L", (larghezza, altezza), 255)
    draw = ImageDraw.Draw(img)
    font_path = next(p for p in FONT_CANDIDATI if os.path.exists(p))
    font = ImageFont.truetype(font_path, size=int(dpi * 16 / 72))          # corpo 16 pt
    y = int(dpi * 1.2)
    for riga in RIGHE:
        draw.text((int(dpi * 1.0), y), riga, fill=0, font=font)
        y += int(dpi * 30 / 72)
    return img.filter(ImageFilter.GaussianBlur(0.6))                       # un po' di «scanner»


def main(destinazione: str) -> None:
    buf = io.BytesIO()
    immagine().save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(destinazione, deflate=True)
    doc.close()
    print("PDF di prova creato")                    # senza nome: il nome non va nei registri


if __name__ == "__main__":
    main(sys.argv[1])
