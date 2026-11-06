"""Genera i PDF di prova di un'azienda di orologi usati da `test_layout_documents.py`.

    python tests/modules/text_edit/documenti_orologi.py

- fattura_writer.pdf, contratto_writer.pdf (5 pagine, paragrafi giustificati), certificato_garanzia.pdf:
  documenti Word (python-docx, margini predefiniti 90 pt su pagina Letter) convertiti con LibreOffice;
- listino_browser.pdf: listino in tabella con bordi stampato da Chromium (playwright di Node), con colonne
  strette: le celle Anno e Prezzo vicine finiscono nella stessa riga del motore di estrazione.

I PDF sono nel repository: la generazione serve solo a rifarli (servono LibreOffice, Node e playwright).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

CLAUSOLE = [
    ("Oggetto", "Il Venditore cede e trasferisce all'Acquirente, che accetta e acquista, l'orologio da polso "
     "marca Rolex, modello Submariner Date, referenza 126610LN, numero di serie 7Y4K2209, completo di scatola, "
     "garanzia internazionale e documenti originali, nello stato di conservazione in cui si trova e che "
     "l'Acquirente dichiara di conoscere e accettare dopo averlo esaminato personalmente."),
    ("Prezzo e pagamento", "Il prezzo della compravendita è convenuto in euro dodicimilacinquecento (€ 12.500,00), "
     "comprensivo di IVA secondo il regime del margine. L'Acquirente versa alla firma del presente contratto un "
     "acconto di euro duemila mediante bonifico bancario e corrisponderà il saldo alla consegna dell'orologio, "
     "che avverrà presso la sede del Venditore entro dieci giorni lavorativi dalla sottoscrizione."),
    ("Garanzie del Venditore", "Il Venditore garantisce di essere il legittimo proprietario dell'orologio, che "
     "lo stesso è libero da pegni, vincoli, sequestri e diritti di terzi, che non proviene da attività illecite e "
     "che tutte le componenti sono originali, fatta eccezione per il vetro zaffiro sostituito nel corso della "
     "revisione eseguita nel mese di marzo dell'anno corrente da un laboratorio autorizzato."),
    ("Consegna e passaggio del rischio", "La proprietà e il rischio di perimento passano all'Acquirente al momento "
     "della consegna, che sarà documentata da apposito verbale sottoscritto da entrambe le parti. In caso di "
     "spedizione su richiesta dell'Acquirente, il Venditore provvederà a stipulare una polizza assicurativa per "
     "l'intero valore dichiarato, con spese a carico dell'Acquirente."),
    ("Recesso", "Trattandosi di vendita conclusa presso i locali commerciali del Venditore, non si applica il "
     "diritto di recesso previsto dal Codice del consumo per i contratti a distanza. Le parti possono tuttavia "
     "risolvere consensualmente il contratto prima della consegna, con restituzione dell'acconto versato e "
     "senza ulteriori obblighi o penali a carico di alcuna di esse."),
    ("Riservatezza", "Le parti si impegnano a mantenere riservate le condizioni economiche del presente contratto "
     "e a non divulgarle a terzi, salvo che ciò sia richiesto dalla legge o da un provvedimento dell'autorità "
     "giudiziaria, oppure sia necessario per la tutela dei propri diritti in sede giudiziale o stragiudiziale."),
    ("Trattamento dei dati personali", "I dati personali delle parti sono trattati nel rispetto del Regolamento "
     "europeo 2016/679 ai soli fini dell'esecuzione del presente contratto e degli adempimenti fiscali e "
     "contabili connessi, per il tempo strettamente necessario e con misure di sicurezza adeguate."),
    ("Comunicazioni", "Ogni comunicazione relativa al presente contratto dovrà essere effettuata per iscritto "
     "mediante posta elettronica certificata o raccomandata con avviso di ricevimento agli indirizzi indicati "
     "in epigrafe, ovvero ai diversi indirizzi che ciascuna parte potrà comunicare successivamente all'altra."),
    ("Legge applicabile e foro competente", "Il presente contratto è regolato dalla legge italiana. Per ogni "
     "controversia relativa alla sua interpretazione, esecuzione o risoluzione sarà competente in via esclusiva "
     "il Foro di Milano, fatti salvi i diritti inderogabili del consumatore previsti dalla normativa vigente."),
    ("Disposizioni finali", "Il presente contratto annulla e sostituisce ogni precedente intesa tra le parti "
     "relativa al medesimo oggetto. Eventuali modifiche saranno valide solo se approvate per iscritto da "
     "entrambe le parti. Il contratto è redatto in due originali, uno per ciascuna parte."),
]

LISTINO_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>Listino</title>
<style>
body { font-family: 'Liberation Sans', Arial, sans-serif; font-size: 10pt; margin: 0; }
h1 { font-size: 16pt; margin: 0 0 4pt 0; }
table { border-collapse: collapse; margin-top: 10pt; }
th, td { border: 1px solid #444; padding: 2px 3px; }
th { background: #eee; text-align: left; }
td.num, th.num { text-align: right; }
td.c { text-align: center; }
</style></head><body>
<h1>Orologeria Bellini – Listino usato certificato</h1>
<p>Prezzi validi dal 1° novembre 2026, IVA inclusa nel regime del margine.</p>
<table>
<tr><th>Referenza</th><th>Modello</th><th class="c">Anno</th><th class="num">Prezzo</th></tr>
<tr><td>124060</td><td>Rolex Submariner</td><td class="c">2023</td><td class="num">€ 8.900,00</td></tr>
<tr><td>126610LN</td><td>Rolex Submariner Date</td><td class="c">2022</td><td class="num">€ 9.750,00</td></tr>
<tr><td>5711/1A</td><td>Patek Philippe Nautilus</td><td class="c">2018</td><td class="num">€ 7.400,00</td></tr>
<tr><td>310.30.42</td><td>Omega Speedmaster Professional</td><td class="c">2021</td><td class="num">€ 6.300,00</td></tr>
<tr><td>M79230N</td><td>Tudor Black Bay 58</td><td class="c">2020</td><td class="num">€ 3.150,00</td></tr>
</table>
<p>Tutti gli orologi sono revisionati dal nostro laboratorio e coperti da garanzia di ventiquattro mesi.</p>
</body></html>
"""


def _document(font: str, size: float):
    from docx import Document
    from docx.shared import Pt
    d = Document()
    st = d.styles["Normal"]
    st.font.name = font
    st.font.size = Pt(size)
    return d


def fattura(path: Path) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt
    d = _document("Liberation Sans", 10)
    r = d.add_paragraph().add_run("Orologeria Bellini S.r.l.")
    r.bold = True
    r.font.size = Pt(14)
    d.add_paragraph("Via Montenapoleone 12 – 20121 Milano – P. IVA 01234567890")
    d.add_paragraph("Fattura n. 2026/0147 del 03/11/2026").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("Spett.le Marco Ferrari\nCorso Vittorio Emanuele 45\n10121 Torino")
    t = d.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ["Descrizione", "Q.tà", "Prezzo unitario", "Importo"]):
        c.text = h
    for row in [("Rolex Submariner 124060, acciaio, quadrante nero, anno 2021", "1", "€ 11.800,00", "€ 11.800,00"),
                ("Cinturino in pelle di alligatore", "2", "€ 240,00", "€ 480,00"),
                ("Revisione completa del movimento", "1", "€ 650,00", "€ 650,00")]:
        cells = t.add_row().cells
        for k, (c, v) in enumerate(zip(cells, row)):
            c.text = v
            if k:
                c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    widths = [Cm(6.0), Cm(1.6), Cm(3.4), Cm(3.4)]
    t.autofit = False
    for col, w in zip(t.columns, widths):
        col.width = w
    for row in t.rows:
        for c, w in zip(row.cells, widths):
            c.width = w
    d.add_paragraph()
    d.add_paragraph("Totale imponibile € 12.930,00").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("Totale fattura € 15.774,60").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("Pagamento: bonifico bancario a 30 giorni data fattura. IBAN IT60 X054 2811 1010 0000 0123 456.")
    d.save(path)


def contratto(path: Path) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    d = _document("Liberation Serif", 12)
    p = d.add_paragraph()
    r = p.add_run("CONTRATTO DI COMPRAVENDITA DI OROLOGIO")
    r.bold = True
    r.font.size = Pt(14)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("tra").alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("Orologeria Bellini S.r.l., con sede in Milano, Via Montenapoleone 12, codice fiscale e "
                    "partita IVA 01234567890, in persona del legale rappresentante Giulia Bellini "
                    "(di seguito «Venditore»)").alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    d.add_paragraph("e").alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("il sig. Marco Ferrari, nato a Torino il 14 maggio 1978, residente in Torino, Corso "
                    "Vittorio Emanuele 45, codice fiscale FRRMRC78E14L219X (di seguito «Acquirente»)"
                    ).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for n in range(1, 25):
        title, body = CLAUSOLE[(n - 1) % len(CLAUSOLE)]
        d.add_paragraph().add_run(f"Art. {n} – {title}").bold = True
        d.add_paragraph(body).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    d.add_paragraph("Milano, 3 novembre 2026")
    d.add_paragraph("Il Venditore").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("L'Acquirente").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.save(path)


def certificato(path: Path) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    d = _document("Liberation Sans", 11)
    p = d.add_paragraph()
    r = p.add_run("CERTIFICATO DI GARANZIA")
    r.bold = True
    r.font.size = Pt(18)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("Orologeria Bellini S.r.l. – Laboratorio di assistenza tecnica").alignment = \
        WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("Si certifica che l'orologio sotto descritto è stato revisionato dal nostro laboratorio ed "
                    "è coperto da garanzia di ventiquattro mesi dalla data di consegna per difetti di "
                    "funzionamento del movimento, esclusi urti, infiltrazioni e usura del cinturino.")
    for label in ("Marca: Omega", "Modello: Speedmaster Professional Moonwatch", "Referenza: 310.30.42.50.01.001",
                  "Numero di serie: 81234567", "Cliente: Marco Ferrari"):
        d.add_paragraph(label)
    d.add_paragraph()
    d.add_paragraph("Data: 03/11/2026").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph()
    d.add_paragraph("Il responsabile tecnico").alignment = WD_ALIGN_PARAGRAPH.RIGHT
    d.add_paragraph("Pagina 1 di 1").alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.save(path)


def office_to_pdf(src: Path, out_dir: Path, work: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    subprocess.run([soffice, "--headless", f"-env:UserInstallation=file://{work}/lo", "--convert-to", "pdf",
                    "--outdir", str(out_dir), str(src)], check=True, capture_output=True, timeout=180)
    return out_dir / (src.stem + ".pdf")


def chromium_to_pdf(html: Path, pdf: Path, work: Path) -> None:
    script = work / "stampa.js"
    script.write_text(
        "const { chromium } = require('playwright');(async () => {const b = await chromium.launch();"
        f"const p = await b.newPage(); await p.goto('file://{html}');"
        f"await p.pdf({{ path: '{pdf}', format: 'A4', margin: {{ top: '15mm', bottom: '15mm', left: '15mm', "
        "right: '15mm' } }); await b.close();})();", encoding="utf-8")
    env = dict(os.environ)
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
    env["NODE_PATH"] = root
    subprocess.run(["node", str(script)], check=True, capture_output=True, timeout=120, env=env)


def main(out_dir: Path = HERE) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name, make in (("fattura_writer", fattura), ("contratto_writer", contratto),
                           ("certificato_garanzia", certificato)):
            src = work / f"{name}.docx"
            make(src)
            shutil.copy(office_to_pdf(src, work, work), out_dir / f"{name}.pdf")
        html = work / "listino_browser.html"
        html.write_text(LISTINO_HTML, encoding="utf-8")
        chromium_to_pdf(html, out_dir / "listino_browser.pdf", work)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
