"""Esecuzione del motore Tesseract (processo esterno, locale e offline) e lettura dell'hOCR.

Il motore riceve un'immagine PNG in scala di grigi e restituisce una gerarchia
blocco → paragrafo → riga → parola con riquadri in pixel e affidabilità.
Nessun accesso alla rete: Tesseract lavora solo sui file locali.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable

import cv2
import numpy as np

from foliant.i18n import _

PAGE_TIMEOUT = 120.0   # secondi massimi per pagina
LOW_CONFIDENCE = 70.0  # sotto questa affidabilità una parola è «sospetta» (OCR-12)


class OcrError(Exception):
    """Errore del riconoscimento con messaggio per l'utente."""


class OcrCancelled(Exception):
    pass


@dataclass
class Word:
    text: str
    bbox: tuple[float, float, float, float]   # pixel dell'immagine elaborata
    conf: float = 0.0                          # affidabilità 0–100
    bold: bool = False

    @property
    def low(self) -> bool:
        return self.conf < LOW_CONFIDENCE


@dataclass
class Line:
    bbox: tuple[float, float, float, float]
    words: list[Word] = field(default_factory=list)
    baseline: tuple[float, float] = (0.0, 0.0)  # pendenza, scostamento (pixel, dal fondo della riga)
    x_size: float = 0.0
    x_ascenders: float = 0.0
    x_descenders: float = 0.0
    block: int = 0
    par: int = 0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bold: bool = False
    italic: bool = False
    family: str = "sans"     # sans | serif | mono (stimata dall'immagine)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def baseline_y(self, x: float) -> float:
        slope, offset = self.baseline
        return self.bbox[3] + offset + slope * (x - self.bbox[0])


def tesseract_cmd() -> str | None:
    return shutil.which("tesseract")


def _tessdata_args() -> list[str]:
    try:
        from .languages import tessdata_dir
        d = tessdata_dir()
    except Exception:
        d = None
    return ["--tessdata-dir", d] if d else []


def _env() -> dict:
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"   # parallelismo gestito da Foliant (una pagina per processo)
    if _tessdata_args():
        env.pop("TESSDATA_PREFIX", None)
    return env


def _run(args: list[str], cancelled: Callable[[], bool] | None, timeout: float) -> tuple[int, str]:
    cmd = tesseract_cmd()
    if cmd is None:
        raise OcrError(_("Il motore OCR (tesseract) non è installato. Installa il pacchetto «tesseract-ocr»."))
    proc = subprocess.Popen([cmd, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env())
    start = time.monotonic()
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.15)
                return proc.returncode, (err or b"").decode("utf-8", "replace") + (out or b"").decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                pass
            if cancelled is not None and cancelled():
                raise OcrCancelled()
            if time.monotonic() - start > timeout:
                raise OcrError(_("Tempo massimo di riconoscimento superato ({s} s).").format(s=int(timeout)))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _write_png(gray: np.ndarray, path: str) -> None:
    ok, buf = cv2.imencode(".png", gray)
    if not ok:
        raise OcrError(_("Impossibile preparare l'immagine per il riconoscimento."))
    with open(path, "wb") as f:
        f.write(buf.tobytes())


def detect_orientation(gray: np.ndarray, cancelled=None) -> int:
    """Rotazione oraria (0/90/180/270) che raddrizza il contenuto, 0 se incerta."""
    try:
        from .languages import has_osd
        if not has_osd():
            return 0
    except Exception:
        pass
    h, w = gray.shape[:2]
    scale = min(1.0, 2600.0 / max(h, w))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    with tempfile.TemporaryDirectory(prefix="foliant-osd-") as tmp:
        img = os.path.join(tmp, "p.png")
        _write_png(gray, img)
        try:
            code, out = _run([img, "stdout", *_tessdata_args(), "--psm", "0", "-l", "osd"], cancelled, 60)
        except OcrError:
            return 0
    if code != 0:
        return 0
    m = re.search(r"Rotate:\s*(\d+)", out)
    c = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not m:
        return 0
    rot = int(m.group(1)) % 360
    if c and float(c.group(1)) < 1.5:
        return 0
    return rot


class _HocrParser(HTMLParser):
    LINE_CLASSES = {"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[Line] = []
        self._stack: list[str] = []
        self._word: Word | None = None
        self._word_depth = 0
        self._block = 0
        self._par = 0
        self._line: Line | None = None

    @staticmethod
    def _title(attrs: dict) -> dict:
        out = {}
        for part in (attrs.get("title") or "").split(";"):
            bits = part.strip().split()
            if bits:
                out[bits[0]] = bits[1:]
        return out

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        self._stack.append(cls)
        if self._word is not None:
            self._word_depth += 1
            if tag in ("strong", "b"):
                self._word.bold = True
            return
        t = self._title(a)
        if cls == "ocr_carea":
            self._block += 1
        elif cls == "ocr_par":
            self._par += 1
        elif cls in self.LINE_CLASSES and "bbox" in t:
            bbox = tuple(float(v) for v in t["bbox"][:4])
            bl = t.get("baseline", ["0", "0"])
            self._line = Line(
                bbox=bbox,
                baseline=(float(bl[0]), float(bl[1])) if len(bl) >= 2 else (0.0, 0.0),
                x_size=float(t.get("x_size", ["0"])[0]),
                x_ascenders=float(t.get("x_ascenders", ["0"])[0]),
                x_descenders=float(t.get("x_descenders", ["0"])[0]),
                block=self._block, par=self._par,
            )
            self.lines.append(self._line)
        elif cls == "ocrx_word" and "bbox" in t:
            conf = float(t.get("x_wconf", ["0"])[0])
            self._word = Word("", tuple(float(v) for v in t["bbox"][:4]), conf)
            self._word_depth = 0

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()
        if self._word is not None:
            if self._word_depth == 0:
                w = self._word
                self._word = None
                w.text = w.text.strip()
                if w.text and self._line is not None:
                    self._line.words.append(w)
            else:
                self._word_depth -= 1

    def handle_data(self, data):
        if self._word is not None:
            self._word.text += data


def parse_hocr(text: str) -> list[Line]:
    p = _HocrParser()
    p.feed(text)
    p.close()
    return [ln for ln in p.lines if ln.words]


def recognize(gray: np.ndarray, languages: list[str], dpi: int = 300, psm: int = 3,
              cancelled: Callable[[], bool] | None = None, timeout: float = PAGE_TIMEOUT) -> list[Line]:
    """Riconosce un'immagine in scala di grigi e restituisce le righe con le parole."""
    if not languages:
        raise OcrError(_("Scegli almeno una lingua."))
    with tempfile.TemporaryDirectory(prefix="foliant-ocr-") as tmp:
        img = os.path.join(tmp, "p.png")
        base = os.path.join(tmp, "out")
        _write_png(gray, img)
        args = [img, base, *_tessdata_args(), "-l", "+".join(languages), "--psm", str(psm), "--dpi", str(int(dpi)),
                "-c", "tessedit_create_hocr=1", "-c", "hocr_font_info=0", "-c", "tessedit_pageseg_mode=" + str(psm)]
        code, out = _run(args, cancelled, timeout)
        path = base + ".hocr"
        if code != 0 or not os.path.exists(path):
            msg = out.strip().splitlines()[-1] if out.strip() else ""
            raise OcrError(_("Il riconoscimento non è riuscito: {m}").format(m=msg or code))
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = drop_noise(parse_hocr(f.read()))
            # più canali (tabelle): si ripete finché non ci sono più righe da dividere
            for _i in range(8):
                before = len(lines)
                lines = split_columns(lines)
                if len(lines) == before:
                    break
            return lines


def drop_noise(lines: list[Line], min_conf: float = 70.0) -> list[Line]:
    """Scarta i «segni» poco affidabili senza lettere né cifre (rumore della scansione)."""
    out = []
    for ln in lines:
        ln.words = [w for w in ln.words if w.conf >= min_conf or any(c.isalnum() for c in w.text)]
        if ln.words:
            out.append(ln)
    return out


def version() -> str | None:
    cmd = tesseract_cmd()
    if not cmd:
        return None
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:
        return None


def _gaps(ln: Line, min_gap: float) -> list[tuple[float, float]]:
    out = []
    for a, b in zip(ln.words, ln.words[1:]):
        if b.bbox[0] - a.bbox[2] >= min_gap:
            out.append((a.bbox[2], b.bbox[0]))
    return out


def _split_line(ln: Line, x: float, col: int) -> Line:
    words = [w for w in ln.words if ((w.bbox[0] + w.bbox[2]) / 2 < x) == (col == 0)]
    x0 = min(w.bbox[0] for w in words)
    bbox = (x0, ln.bbox[1], max(w.bbox[2] for w in words), ln.bbox[3])
    offset = ln.baseline_y(x0) - ln.bbox[3]
    return Line(bbox=bbox, words=words, baseline=(ln.baseline[0], offset), x_size=ln.x_size,
                x_ascenders=ln.x_ascenders, x_descenders=ln.x_descenders, block=ln.block * 100 + col + 1,
                par=ln.par * 100 + col + 1)


def split_columns(lines: list[Line]) -> list[Line]:
    """Separa le righe che il motore ha unito attraverso due colonne.

    Un «canale» è uno spazio tra parole molto più largo della riga che si ripete
    alla stessa ascissa in almeno due righe consecutive: le righe vengono divise lì
    e il testo della colonna sinistra precede quello della destra."""
    if len(lines) < 2:
        return lines
    out: list[Line] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        h = ln.x_size or (ln.bbox[3] - ln.bbox[1])
        gaps = _gaps(ln, 2.5 * h)
        region: list[Line] = []
        gutter = None
        for g in gaps:
            run = [ln]
            lo, hi = g
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                hn = nxt.x_size or (nxt.bbox[3] - nxt.bbox[1])
                match = [(a, b) for a, b in _gaps(nxt, 2.5 * hn) if a < hi and b > lo]
                if not match:
                    break
                lo, hi = max(lo, match[0][0]), min(hi, match[0][1])
                run.append(nxt)
                j += 1
            if len(run) >= 2 and hi > lo:
                region, gutter = run, (lo + hi) / 2
                break
        if not region:
            out.append(ln)
            i += 1
            continue
        out.extend(_split_line(r, gutter, 0) for r in region)
        out.extend(_split_line(r, gutter, 1) for r in region)
        i += len(region)
    return out
