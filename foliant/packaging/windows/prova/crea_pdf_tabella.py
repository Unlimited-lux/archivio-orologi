"""Crea il PDF di prova per le conversioni in Excel, PowerPoint e Word: una pagina A4 con il testo
(non scansionata), con titolo, paragrafo, una tabella con i bordi, un'immagine e un commento.

La tabella ha un'intestazione, un codice con lo zero iniziale (deve restare testo), anni, importi
all'italiana (4.250,00) e date (12/03/2024), che la conversione in Excel trasforma in numeri e date.
I valori attesi sono ripetuti in verifica_risultati.py.

Uso: python crea_pdf_tabella.py destinazione.pdf
Gira con il Python della macchina di prova (pymupdf), non con il Foliant installato.
"""

import sys

import pymupdf

TITOLO = "Inventario degli orologi"
PARAGRAFO = "Elenco aggiornato dopo la revisione di primavera, con i prezzi in euro."
INTESTAZIONE = ("Codice", "Modello", "Città", "Anno", "Prezzo", "Revisione")
RIGHE = (
    ("0451", "Speedmaster", "Bienne", "1969", "4.250,00", "12/03/2024"),
    ("0452", "Submariner", "Ginevra", "1972", "7.900,50", "05/11/2023"),
    ("0453", "Calatrava", "Ginevra", "1985", "3.100,75", "21/06/2022"),
    ("0454", "Royal Oak", "Le Brassus", "1978", "18.400,00", "30/09/2021"),
)
COMMENTO = "Verificare il prezzo del Royal Oak."
COLONNE = (50, 95, 85, 50, 80, 80)                  # larghezze in punti
X0, Y0, ALTEZZA = 60, 200, 24


def main(destinazione: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((X0, 90), TITOLO, fontname="hebo", fontsize=20)
    page.insert_text((X0, 130), PARAGRAFO, fontname="helv", fontsize=11)
    # tabella con le linee di tutte le celle: la conversione la riconosce dai bordi
    larghezza = sum(COLONNE)
    righe = (INTESTAZIONE,) + RIGHE
    for r in range(len(righe) + 1):
        y = Y0 + r * ALTEZZA
        page.draw_line((X0, y), (X0 + larghezza, y), color=(0, 0, 0), width=0.8)
    x = X0
    for w in COLONNE + (0,):
        page.draw_line((x, Y0), (x, Y0 + len(righe) * ALTEZZA), color=(0, 0, 0), width=0.8)
        x += w
    for r, riga in enumerate(righe):
        x = X0
        for w, testo in zip(COLONNE, riga):
            page.insert_text((x + 5, Y0 + r * ALTEZZA + 16), testo, fontname="hebo" if r == 0 else "helv",
                             fontsize=10)
            x += w
    # un'immagine (un piccolo marchio a due colori) e un commento
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
    pix.set_rect(pix.irect, (30, 60, 120))
    pix.set_rect(pymupdf.IRect(16, 16, 48, 48), (230, 180, 40))
    page.insert_image(pymupdf.Rect(460, 60, 524, 124), pixmap=pix)
    note = page.add_text_annot((X0 + larghezza + 6, Y0 + 4 * ALTEZZA + 4), COMMENTO)
    note.set_info(title="Prova")
    note.update()
    doc.set_metadata({"title": TITOLO})
    doc.save(destinazione, deflate=True)
    doc.close()
    print("PDF di prova con la tabella creato")      # senza nome: il nome non va nei registri


if __name__ == "__main__":
    main(sys.argv[1])
