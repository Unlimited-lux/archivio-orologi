"""Documenti realistici per la verifica di qualità OCR (azienda di compravendita di orologi).

Ogni generatore restituisce (immagine RGB, dpi, parole attese). Tutto sintetico e riproducibile.
"""

from __future__ import annotations

import re
import unicodedata

import cv2
import numpy as np
import pymupdf

SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
SANS_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"


def _render(page, dpi):
    pix = page.get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", unicodedata.normalize("NFC", text)) if w]


def word_accuracy(got: str, expected: list[str]) -> float:
    """Percentuale di parole attese presenti nel testo riconosciuto (multinsieme, punteggiatura esterna ignorata)."""
    def norm(w):
        return w.strip(".,;:()«»\"'").lower()
    pool: dict[str, int] = {}
    for w in _words(got):
        pool[norm(w)] = pool.get(norm(w), 0) + 1
    hit = 0
    for w in expected:
        k = norm(w)
        if pool.get(k, 0) > 0:
            pool[k] -= 1
            hit += 1
    return hit / max(1, len(expected))


def id_card_photo(seed: int = 1):
    """Carta d'identità fotografata di sbieco su un tavolo, con luce non uniforme e JPEG."""
    rng = np.random.default_rng(seed)
    doc = pymupdf.open()
    w_pt, h_pt = 85.6 / 25.4 * 72 * 3, 54 / 25.4 * 72 * 3       # carta ingrandita 3x (≈ foto ravvicinata)
    page = doc.new_page(width=w_pt, height=h_pt)
    page.draw_rect(page.rect, color=None, fill=(0.86, 0.92, 0.97))
    for k in range(0, int(w_pt), 14):
        page.draw_line((k, 0), (k + 60, h_pt), color=(0.80, 0.88, 0.95), width=0.6)
    page.draw_rect(pymupdf.Rect(18, 60, 150, 220), color=(0.4, 0.4, 0.45), fill=(0.75, 0.75, 0.78))
    page.insert_font(fontname="S", fontfile=SANS)
    page.insert_font(fontname="B", fontfile=SANS_B)
    page.insert_text((18, 40), "REPUBBLICA ITALIANA - CARTA DI IDENTITA", fontname="B", fontsize=13)
    fields = [("COGNOME", "ROSSI"), ("NOME", "MARIO"), ("LUOGO E DATA DI NASCITA", "MILANO 12.03.1975"),
              ("SESSO", "M"), ("CITTADINANZA", "ITA"), ("SCADENZA", "12.03.2031"), ("NUMERO", "CA12345XY")]
    expected = _words("REPUBBLICA ITALIANA - CARTA DI IDENTITA")
    for k, (label, value) in enumerate(fields):
        x = 170 if k < 4 else 330
        y = 72 + (k % 4) * 36
        page.insert_text((x, y), label, fontname="S", fontsize=8, color=(0.25, 0.25, 0.35))
        page.insert_text((x, y + 15), value, fontname="B", fontsize=12)
        expected += _words(value)
    card = _render(page, 150)
    ch, cw = card.shape[:2]
    canvas = np.full((int(ch * 1.6), int(cw * 1.5), 3), (60, 52, 45), np.uint8)
    H, W = canvas.shape[:2]
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    dst = np.float32([[W * 0.14, H * 0.20], [W * 0.88, H * 0.12], [W * 0.84, H * 0.82], [W * 0.10, H * 0.76]])
    m = cv2.getPerspectiveTransform(src, dst)
    photo = cv2.warpPerspective(card, m, (W, H), dst=canvas.copy(), borderMode=cv2.BORDER_TRANSPARENT)
    yy, xx = np.mgrid[0:H, 0:W]
    light = 0.75 + 0.35 * (xx / W)                        # luce più forte a destra
    photo = np.clip(photo.astype(np.float32) * light[..., None], 0, 255).astype(np.uint8)
    photo = np.clip(photo.astype(np.int16) + rng.normal(0, 4, photo.shape).astype(np.int16), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(photo, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
    photo = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    return photo, 150, expected


def invoice(dpi: int = 300):
    """Fattura con intestazione, tabella con bordi e importi in euro, totali e IVA."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="S", fontfile=SANS)
    page.insert_font(fontname="B", fontfile=SANS_B)
    lines = [("B", 16, "Orologeria Verdi S.r.l."), ("S", 10, "Via Montenapoleone 8, 20121 Milano - P.IVA 01234567890"),
             ("B", 13, "Fattura n. 2026/0147 del 28.10.2026"), ("S", 10, "Cliente: Bianchi Giovanni, Corso Venezia 12, Milano")]
    y = 70
    expected = []
    for f, s, t in lines:
        page.insert_text((60, y), t, fontname=f, fontsize=s)
        expected += _words(t)
        y += s + 10
    rows = [("Descrizione", "Q.tà", "Prezzo", "Importo"),
            ("Cronografo acciaio ref. 3570.50", "1", "4.850,00 €", "4.850,00 €"),
            ("Cinturino in pelle marrone", "2", "85,50 €", "171,00 €"),
            ("Revisione completa movimento", "1", "320,00 €", "320,00 €"),
            ("Scatola e garanzia", "1", "0,00 €", "0,00 €")]
    xs = [60, 300, 350, 450, 540]
    ty = y + 10
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((xs[c] + 5, ty + r * 22 + 15), cell, fontname="B" if r == 0 else "S", fontsize=10)
            expected += _words(cell)
    for x in xs:
        page.draw_line((x, ty), (x, ty + 22 * len(rows)), width=0.8)
    for r in range(len(rows) + 1):
        page.draw_line((60, ty + r * 22), (540, ty + r * 22), width=0.8)
    y = ty + 22 * len(rows) + 30
    for t in ("Imponibile 5.341,00 €", "IVA 22% 1.175,02 €", "Totale fattura 6.516,02 €",
              "Pagamento: bonifico bancario IBAN IT60 X054 2811 1010 0000 0123 456"):
        page.insert_text((300 if "IBAN" not in t else 60, y), t, fontname="S", fontsize=10)
        expected += _words(t)
        y += 18
    img = _render(page, dpi)
    img[..., 2] = np.minimum(img[..., 2], 235)
    return img, dpi, expected


def warranty(dpi: int = 300, seed: int = 2):
    """Certificato di garanzia con timbro circolare sovrapposto e firma a mano."""
    rng = np.random.default_rng(seed)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="R", fontfile=SERIF)
    text = ["CERTIFICATO DI GARANZIA INTERNAZIONALE",
            "Si certifica che l'orologio modello Submariner",
            "numero di serie 7X93K421 è stato venduto in data 15.09.2026",
            "e controllato dal nostro laboratorio. La garanzia copre",
            "difetti di fabbricazione per un periodo di 24 mesi.",
            "Rivenditore autorizzato: Orologeria Verdi, Milano"]
    expected = []
    y = 110
    for i, t in enumerate(text):
        page.insert_text((70, y), t, fontname="R", fontsize=15 if i == 0 else 12)
        expected += _words(t)
        y += 30
    img = _render(page, dpi)
    z = dpi / 72
    # timbro blu semitrasparente sopra le ultime righe
    stamp = img.copy()
    c = (int(420 * z), int(250 * z))
    cv2.circle(stamp, c, int(55 * z), (40, 70, 170), int(3 * z))
    cv2.circle(stamp, c, int(42 * z), (40, 70, 170), int(1.5 * z))
    cv2.putText(stamp, "VERDI", (c[0] - int(30 * z), c[1] + int(6 * z)), cv2.FONT_HERSHEY_SIMPLEX, 0.9 * z / 2,
                (40, 70, 170), max(1, int(1.6 * z)))
    img = cv2.addWeighted(stamp, 0.65, img, 0.35, 0)
    # firma: curva a mano libera
    pts = []
    x0, y0 = 90 * z, 330 * z
    for t in np.linspace(0, 6 * np.pi, 120):
        pts.append((int(x0 + t * 9 * z), int(y0 + np.sin(t * 1.3) * 10 * z + rng.normal(0, 1) * z)))
    cv2.polylines(img, [np.array(pts, np.int32)], False, (20, 20, 60), max(1, int(1.3 * z)))
    return img, dpi, expected


def gray_150():
    """Scansione a 150 dpi in scala di grigi, JPEG, con fondo leggermente grigio."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="S", fontfile=SANS)
    text = ["Ricevuta di acquisto usato", "Si dichiara di aver ricevuto l'orologio marca Longines,",
            "modello Conquest, cassa in acciaio 41 mm, anno 2018, con scatola",
            "e documenti originali, al prezzo pattuito di 1.450,00 euro.",
            "Il cedente dichiara di esserne il legittimo proprietario.",
            "Documento del cedente: carta di identità n. CA98765ZT"]
    expected = []
    y = 100
    for t in text:
        page.insert_text((60, y), t, fontname="S", fontsize=11)
        expected += _words(t)
        y += 22
    img = _render(page, 150)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    gray = np.clip(gray.astype(np.int16) - 18, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 70])
    gray = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), 150, expected


CORPUS = {"carta_identita_foto": id_card_photo, "fattura": invoice, "garanzia_timbro_firma": warranty,
          "ricevuta_grigi_150": gray_150}


def id_card_far(seed: int = 4):
    """Carta d'identità fotografata più da lontano (testo piccolo), inclinata e con luce radente."""
    img, _dpi, expected = id_card_photo(seed)
    h, w = img.shape[:2]
    small = cv2.resize(img, (int(w * 0.55), int(h * 0.55)), interpolation=cv2.INTER_AREA)
    return small, 150, expected


def passport_mrz():
    """Pagina dati di un passaporto con le due righe a lettura ottica (MRZ) in monospaziato."""
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=350)
    page.draw_rect(page.rect, color=None, fill=(0.93, 0.90, 0.95))
    page.insert_font(fontname="S", fontfile=SANS)
    page.insert_font(fontname="M", fontfile="/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf")
    expected = []
    for i, t in enumerate(["PASSAPORTO / PASSPORT", "Cognome / Surname: ROSSI", "Nome / Given names: MARIO",
                           "Data di nascita / Date of birth: 12 MAR 1975", "Numero / No.: YA1234567"]):
        page.insert_text((150, 40 + i * 24), t, fontname="S", fontsize=11)
        expected += _words(t)
    mrz = ["P<ITAROSSI<<MARIO<<<<<<<<<<<<<<<<<<<<<<<<<<", "YA12345674ITA7503127M3103128<<<<<<<<<<<<<<02"]
    for i, t in enumerate(mrz):
        page.insert_text((20, 290 + i * 22), t, fontname="M", fontsize=12.5)
        expected.append(t)
    return _render(page, 300), 300, expected


def small_gray_150():
    """Testo piccolo (9 pt) scansionato a 150 dpi in grigi e JPEG forte."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="S", fontfile=SANS)
    text = ["Condizioni di vendita: la garanzia convenzionale di 12 mesi decorre dalla consegna.",
            "Sono esclusi i danni da urti, umidità e manomissioni del movimento o della cassa.",
            "Il diritto di recesso si esercita entro 14 giorni con comunicazione scritta.",
            "Foro competente: Milano. Prezzo complessivo 2.380,00 euro IVA inclusa."]
    expected = []
    for i, t in enumerate(text):
        page.insert_text((50, 90 + i * 16), t, fontname="S", fontsize=9)
        expected += _words(t)
    gray = cv2.cvtColor(_render(page, 150), cv2.COLOR_RGB2GRAY)
    gray = np.clip(gray.astype(np.int16) * 0.85 + 20, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 55])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2RGB), 150, expected


def red_stamp_over_text():
    """Timbro rosso pieno («PAGATO») sopra righe di testo nero: l'inchiostro del timbro si somma a
    quello della stampa (il nero sotto il rosso resta nero), come su carta."""
    img, dpi, expected = invoice(300)
    z = dpi / 72
    stamp = np.full_like(img, 255)
    cv2.rectangle(stamp, (int(320 * z), int(330 * z)), (int(520 * z), int(380 * z)), (200, 30, 30), int(3 * z))
    cv2.putText(stamp, "PAGATO", (int(335 * z), int(368 * z)), cv2.FONT_HERSHEY_DUPLEX, 1.2 * z / 2, (200, 30, 30),
                max(1, int(2.2 * z)))
    stamp = cv2.addWeighted(stamp, 0.85, np.full_like(img, 255), 0.15, 0)
    return (img.astype(np.uint16) * stamp // 255).astype(np.uint8), dpi, expected


def thermal_receipt():
    """Scontrino termico sbiadito: testo grigio chiaro su carta leggermente grigia, stretto e lungo."""
    doc = pymupdf.open()
    page = doc.new_page(width=230, height=420)
    page.insert_font(fontname="M", fontfile="/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf")
    lines = ["OROLOGERIA VERDI", "VIA MONTENAPOLEONE 8", "SCONTRINO N. 0458", "PILA SR626SW 1 X 6,50",
             "CINTURINO 18MM 1 X 39,00", "TOTALE EURO 45,50", "CONTANTI 50,00", "RESTO 4,50"]
    expected = []
    for i, t in enumerate(lines):
        page.insert_text((15, 40 + i * 20), t, fontname="M", fontsize=10, color=(0.45, 0.45, 0.45))
        expected += _words(t)
    img = _render(page, 200)
    img = np.clip(img.astype(np.int16) - 25, 0, 255).astype(np.uint8)
    return img, 200, expected


HARD = {"carta_identita_lontana": id_card_far, "passaporto_mrz": passport_mrz, "testo_piccolo_150": small_gray_150,
        "timbro_rosso": red_stamp_over_text, "scontrino_termico": thermal_receipt}


def invoice_phone_photo(seed: int = 5):
    """Fattura A4 fotografata col telefono: prospettiva marcata, ombra della mano, JPEG."""
    rng = np.random.default_rng(seed)
    img, _dpi, expected = invoice(200)
    ih, iw = img.shape[:2]
    W, H = int(iw * 1.25), int(ih * 1.2)
    src = np.float32([[0, 0], [iw, 0], [iw, ih], [0, ih]])
    dst = np.float32([[W * 0.12, H * 0.06], [W * 0.90, H * 0.10], [W * 0.95, H * 0.96], [W * 0.05, H * 0.92]])
    m = cv2.getPerspectiveTransform(src, dst)
    canvas = np.full((H, W, 3), (95, 80, 70), np.uint8)
    photo = cv2.warpPerspective(img, m, (W, H), dst=canvas, borderMode=cv2.BORDER_TRANSPARENT)
    yy, xx = np.mgrid[0:H, 0:W]
    shadow = 1.0 - 0.35 * np.exp(-(((xx - W * 0.8) / (W * 0.25)) ** 2 + ((yy - H * 0.85) / (H * 0.2)) ** 2))
    photo = np.clip(photo.astype(np.float32) * shadow[..., None] * 0.92, 0, 255)
    photo = np.clip(photo + rng.normal(0, 3, photo.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(photo, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB), 200, expected


def skewed_gray_scan():
    """Fattura scansionata storta (6°) a 150 dpi in scala di grigi."""
    img, _dpi, expected = invoice(150)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 6, 1.0)
    gray = cv2.warpAffine(gray, m, (w, h), borderValue=235)
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE), cv2.COLOR_GRAY2RGB), 150, expected


HARD2 = {"fattura_fotografata": invoice_phone_photo, "fattura_storta_150": skewed_gray_scan}

