"""Lettura ad alta voce (VIS-35): logica pura e motori di sintesi vocale.

- `build_sentences(page, words)`: frasi in ordine di lettura dalle parole del
  motore PDF (x0, y0, x1, y1, parola, blocco, riga, n), con i rettangoli delle
  righe per l'evidenziazione.
- `document_language(pdf, page)`: `/Lang` degli elementi di struttura della
  pagina (PDF taggati), poi `/Lang` del catalogo (da chiamare con ENGINE_LOCK).
- `reading_allowed(perms)`: copia consentita oppure bit 10 (accessibilità).
- Motori: `SpeechEngine` astratto con `available()`, `voices()`, `speak()`,
  `stop()` e il segnale `finished` (fine frase). Implementazioni: QtTextToSpeech
  (se importabile), `spd-say` (speech-dispatcher) ed `espeak-ng`/`espeak` via
  `QProcess` (mai bloccanti), su Windows le voci di sistema (SAPI, System.Speech) tramite
  PowerShell, `NullEngine` (nessun motore) e `FakeEngine` per i test.
"""

from __future__ import annotations

import math
import re
import shutil
import sys
from dataclasses import dataclass, field

import pymupdf
from PySide6.QtCore import QObject, QProcess, Signal

# ====================================================================== frasi
_SENTENCE_END = re.compile(r"[.!?…;:]['\"»”’)\]]*$")
MAX_SENTENCE_WORDS = 60


@dataclass
class Sentence:
    page: int
    text: str
    words: list[tuple] = field(default_factory=list)

    @property
    def rects(self) -> list[pymupdf.Rect]:
        """Un rettangolo per riga (unione delle parole della stessa riga)."""
        out: list[pymupdf.Rect] = []
        key = None
        for w in self.words:
            k = (w[5], w[6])
            r = pymupdf.Rect(w[:4])
            if k == key and out:
                out[-1] |= r
            else:
                out.append(r)
                key = k
        return out

    @property
    def bbox(self) -> pymupdf.Rect:
        r = pymupdf.Rect()
        for w in self.words:
            r |= pymupdf.Rect(w[:4])
        return r


def _join(words: list[tuple]) -> str:
    """Testo parlato: parole separate da spazi; i trattini di sillabazione a fine
    riga vengono ricongiunti."""
    parts: list[str] = []
    prev_key = None
    for w in words:
        text = w[4]
        key = (w[5], w[6])
        if parts and prev_key is not None and key != prev_key and parts[-1].endswith("-") \
                and len(parts[-1]) > 1 and parts[-1][-2].isalpha() and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)
        prev_key = key
    return " ".join(parts)


def build_sentences(page: int, words: list[tuple]) -> list[Sentence]:
    """Divide le parole (già in ordine di lettura) in frasi: fine a punteggiatura
    terminale, a cambio di blocco (paragrafo) o oltre `MAX_SENTENCE_WORDS`."""
    out: list[Sentence] = []
    cur: list[tuple] = []
    for i, w in enumerate(words):
        if cur and w[5] != cur[-1][5]:
            out.append(Sentence(page, _join(cur), cur))
            cur = []
        cur.append(w)
        if _SENTENCE_END.search(w[4]) or len(cur) >= MAX_SENTENCE_WORDS:
            out.append(Sentence(page, _join(cur), cur))
            cur = []
    if cur:
        out.append(Sentence(page, _join(cur), cur))
    return [s for s in out if s.text.strip()]


def sentence_index_at(sentences: list[Sentence], point: pymupdf.Point | None) -> int:
    """Frase che contiene il punto o, in mancanza, la prima che lo segue."""
    if point is None or not sentences:
        return 0
    best, best_d = 0, None
    for i, s in enumerate(sentences):
        for w in s.words:
            r = pymupdf.Rect(w[:4])
            if r.contains(point):
                return i
            dx = max(r.x0 - point.x, 0.0, point.x - r.x1)
            dy = max(r.y0 - point.y, 0.0, point.y - r.y1)
            d = dx * dx + dy * dy * 4
            if best_d is None or d < best_d:
                best, best_d = i, d
    return best


# ===================================================================== lingua
def _pdf_string(pdf: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = pdf.xref_get_key(xref, key)
    if kind == "string" and value.strip():
        return value.strip()
    return ""


def catalog_language(pdf: pymupdf.Document) -> str:
    try:
        return _pdf_string(pdf, pdf.pdf_catalog(), "Lang")
    except Exception:
        return ""


def _ref(value: str) -> int:
    m = re.match(r"^(\d+) 0 R$", value.strip())
    return int(m.group(1)) if m else 0


def structure_languages(pdf: pymupdf.Document, limit: int = 20000) -> dict[int, str]:
    """{indice pagina: lingua} dal primo elemento di struttura con `/Lang` (proprio
    o ereditato) che appartiene alla pagina (`/Pg` proprio o ereditato)."""
    try:
        kind, root = pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")
    except Exception:
        return {}
    root_xref = _ref(root) if kind == "xref" else 0
    if not root_xref:
        return {}
    page_of = {pdf.page_xref(i): i for i in range(pdf.page_count)}
    result: dict[int, str] = {}
    seen: set[int] = set()
    stack: list[tuple[int, str, int]] = [(root_xref, "", 0)]   # (xref, lingua ereditata, pagina ereditata xref)
    while stack and len(seen) < limit:
        xref, lang, pg = stack.pop()
        if xref in seen:
            continue
        seen.add(xref)
        own = _pdf_string(pdf, xref, "Lang")
        lang = own or lang
        kind, value = pdf.xref_get_key(xref, "Pg")
        if kind == "xref":
            pg = _ref(value) or pg
        if lang and pg in page_of and page_of[pg] not in result:
            result[page_of[pg]] = lang
        kind, value = pdf.xref_get_key(xref, "K")
        children: list[int] = []
        if kind == "xref":
            children = [_ref(value)]
        elif kind == "array":
            children = [int(n) for n in re.findall(r"(\d+) 0 R", value)]
        for child in reversed(children):
            if child and child not in seen:
                stack.append((child, lang, pg))
    return result


def document_language(pdf: pymupdf.Document, page: int, fallback: str = "") -> str:
    """Lingua per leggere `page`: struttura logica, poi catalogo, poi `fallback`."""
    try:
        lang = structure_languages(pdf).get(page)
    except Exception:
        lang = ""
    return lang or catalog_language(pdf) or fallback


def primary_language(tag: str) -> str:
    """"de-DE" → "de"."""
    return (tag or "").replace("_", "-").split("-")[0].lower()


# =================================================================== permessi
def reading_allowed(perms: int) -> bool:
    """Lettura consentita se copia consentita o bit 10 (accessibilità) attivo."""
    if perms == -1:
        return True
    return bool(perms & pymupdf.PDF_PERM_COPY) or bool(perms & pymupdf.PDF_PERM_ACCESSIBILITY)


# ================================================================== velocità
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
MIN_RATE, MAX_RATE = 0.5, 3.0


def clamp_rate(rate: float) -> float:
    return max(MIN_RATE, min(MAX_RATE, float(rate)))


def spd_rate(rate: float) -> int:
    """Moltiplicatore 0,5×–3× → velocità di spd-say (−100…100, 0 = normale)."""
    rate = clamp_rate(rate)
    v = (rate - 1.0) * 100 if rate < 1 else (rate - 1.0) / 2.0 * 100
    return int(round(max(-100, min(100, v))))


def espeak_wpm(rate: float) -> int:
    return int(round(175 * clamp_rate(rate)))


def qt_rate(rate: float) -> float:
    """Moltiplicatore → QTextToSpeech.rate (−1…1, logaritmico)."""
    return max(-1.0, min(1.0, math.log2(clamp_rate(rate)) / math.log2(3.0)))


# ===================================================================== motori
@dataclass
class Voice:
    id: str
    name: str
    language: str = ""


class SpeechEngine(QObject):
    """Interfaccia del motore di sintesi. `finished` = frase terminata da sola
    (non dopo `stop()`)."""

    finished = Signal()
    failed = Signal(str)
    voicesChanged = Signal()
    name = ""

    def available(self) -> bool:
        return False

    def voices(self) -> list[Voice]:
        return []

    def speak(self, text: str, lang: str = "", rate: float = 1.0, pitch: float = 0.0,
              voice: str | None = None) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class NullEngine(SpeechEngine):
    name = ""

    def speak(self, text, lang="", rate=1.0, pitch=0.0, voice=None) -> None:
        self.failed.emit("no engine")


class FakeEngine(SpeechEngine):
    """Motore simulato per i test: registra le richieste; `finish()` termina la frase."""

    name = "fake"

    def __init__(self, voices: list[Voice] | None = None, auto_finish: bool = False, parent=None):
        super().__init__(parent)
        self.calls: list[dict] = []
        self.speaking = False
        self.stops = 0
        self.auto_finish = auto_finish
        self._voices = voices if voices is not None else [
            Voice("it", "Italiano", "it"), Voice("de", "Deutsch", "de"), Voice("en", "English", "en")]

    def available(self) -> bool:
        return True

    def voices(self) -> list[Voice]:
        return list(self._voices)

    def speak(self, text, lang="", rate=1.0, pitch=0.0, voice=None) -> None:
        self.calls.append({"text": text, "lang": lang, "rate": rate, "pitch": pitch, "voice": voice})
        self.speaking = True
        if self.auto_finish:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.finish)

    def stop(self) -> None:
        self.stops += 1
        self.speaking = False

    def finish(self) -> None:
        if self.speaking:
            self.speaking = False
            self.finished.emit()

    @property
    def texts(self) -> list[str]:
        return [c["text"] for c in self.calls]


class ProcessEngine(SpeechEngine):
    """Sintesi tramite un programma di sistema avviato con QProcess (asincrono)."""

    def __init__(self, program: str, parent=None):
        super().__init__(parent)
        self.program = program
        self._proc: QProcess | None = None
        self._voices: list[Voice] | None = None
        self._voice_proc: QProcess | None = None

    def available(self) -> bool:
        return bool(shutil.which(self.program))

    # -- voci (lette in modo asincrono alla prima richiesta)
    def voices(self) -> list[Voice]:
        if self._voices is None and self._voice_proc is None:
            self._voice_proc = QProcess(self)
            self._voice_proc.finished.connect(self._on_voices)
            self._voice_proc.start(self.program, self._voice_args())
        return list(self._voices or [])

    def _on_voices(self, *_a) -> None:
        proc = self._voice_proc
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace") if proc else ""
        self._voices = self._parse_voices(out)
        self.voicesChanged.emit()

    def _voice_args(self) -> list[str]:
        return []

    def _parse_voices(self, out: str) -> list[Voice]:
        return []

    def _args(self, text, lang, rate, pitch, voice) -> list[str]:
        raise NotImplementedError

    def speak(self, text, lang="", rate=1.0, pitch=0.0, voice=None) -> None:
        self.stop()
        proc = QProcess(self)
        proc.finished.connect(lambda *_a, p=proc: self._on_finished(p))
        proc.errorOccurred.connect(lambda err, p=proc: self._on_error(p, err))
        self._proc = proc
        # uno spazio iniziale evita che un testo che comincia con "-" sia letto come opzione
        safe = " " + text if text.startswith("-") else text
        proc.start(self.program, self._args(safe, lang, rate, pitch, voice))

    def _on_finished(self, proc: QProcess) -> None:
        if proc is self._proc:
            self._proc = None
            self.finished.emit()
        proc.deleteLater()

    def _on_error(self, proc: QProcess, err) -> None:
        if proc is self._proc and err == QProcess.ProcessError.FailedToStart:
            self._proc = None
            self.failed.emit(proc.errorString())

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()


class SpdSayEngine(ProcessEngine):
    name = "speech-dispatcher"

    def __init__(self, parent=None):
        super().__init__("spd-say", parent)

    def _args(self, text, lang, rate, pitch, voice):
        args = ["-w", "-r", str(spd_rate(rate)), "-p", str(int(round(max(-1, min(1, pitch)) * 100)))]
        if lang:
            args += ["-l", primary_language(lang)]
        if voice:
            args += ["-y", voice]
        return args + [text]

    def _voice_args(self):
        return ["-L"]

    def _parse_voices(self, out):
        voices = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                voices.append(Voice(parts[0], parts[0], parts[1]))
        return voices

    def stop(self) -> None:
        running = self._proc is not None
        super().stop()
        if running:
            QProcess.startDetached("spd-say", ["-C"])   # interrompe anche l'audio già in coda


class EspeakEngine(ProcessEngine):
    name = "espeak"

    def __init__(self, program: str = "espeak-ng", parent=None):
        super().__init__(program, parent)

    def _args(self, text, lang, rate, pitch, voice):
        args = ["-s", str(espeak_wpm(rate)), "-p", str(int(round(50 + max(-1, min(1, pitch)) * 49)))]
        if voice or lang:
            args += ["-v", voice or primary_language(lang)]
        return args + [text]

    def _voice_args(self):
        return ["--voices"]

    def _parse_voices(self, out):
        voices = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                voices.append(Voice(parts[4] if len(parts) > 4 else parts[3], parts[3], parts[1]))
        return voices


_PS_PRELUDE = ("$ErrorActionPreference='Stop';[Console]::InputEncoding=[Text.Encoding]::UTF8;"
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8;Add-Type -AssemblyName System.Speech;"
               "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;")


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class SapiEngine(ProcessEngine):
    """Windows: voci di sistema (SAPI) con System.Speech, in un processo PowerShell per frase.
    Il testo passa dallo standard input (mai sulla riga di comando); l'intonazione non è
    regolabile."""

    name = "sapi"

    def __init__(self, parent=None):
        super().__init__("powershell", parent)

    def available(self) -> bool:
        return sys.platform == "win32" and bool(shutil.which(self.program))

    def _voice_args(self):
        script = (_PS_PRELUDE + "foreach($v in $s.GetInstalledVoices()){if($v.Enabled){"
                  "$i=$v.VoiceInfo;[Console]::Out.WriteLine($i.Name+[char]9+$i.Culture.Name)}}")
        return ["-NoProfile", "-NonInteractive", "-Command", script]

    def _parse_voices(self, out):
        voices = []
        for line in out.splitlines():
            name, _tab, lang = line.strip().partition("\t")
            if name:
                voices.append(Voice(name, name, lang))
        return voices

    def _args(self, text, lang, rate, pitch, voice):
        sapi_rate = max(-10, min(10, int(round((rate - 1.0) * 10))))
        script = _PS_PRELUDE + f"$s.Rate={sapi_rate};"
        if voice:
            script += f"try{{$s.SelectVoice({_ps_quote(voice)})}}catch{{}};"
        elif lang and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", lang):
            script += ("try{$s.SelectVoiceByHints('NotSet','NotSet',0,"
                       f"[Globalization.CultureInfo]::new({_ps_quote(lang)}))}}catch{{}};")
        script += "$t=[Console]::In.ReadToEnd();$s.Speak($t)"
        return ["-NoProfile", "-NonInteractive", "-Command", script]

    def speak(self, text, lang="", rate=1.0, pitch=0.0, voice=None) -> None:
        self.stop()
        proc = QProcess(self)
        proc.finished.connect(lambda *_a, p=proc: self._on_finished(p))
        proc.errorOccurred.connect(lambda err, p=proc: self._on_error(p, err))
        self._proc = proc
        proc.start(self.program, self._args("", lang, rate, pitch, voice))
        proc.write(text.encode("utf-8"))
        proc.closeWriteChannel()


class QtSpeechEngine(SpeechEngine):
    """QTextToSpeech (modulo facoltativo, assente in PySide6-Essentials)."""

    name = "qt"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtTextToSpeech import QTextToSpeech   # noqa: F401 (ImportError se assente)
        self._QTextToSpeech = QTextToSpeech
        self._tts = QTextToSpeech(self)
        self._speaking = False
        self._tts.stateChanged.connect(self._on_state)

    def available(self) -> bool:
        return self._tts.state() != self._QTextToSpeech.State.Error

    def voices(self) -> list[Voice]:
        return [Voice(v.name(), v.name(), v.locale().bcp47Name()) for v in self._tts.availableVoices()]

    def speak(self, text, lang="", rate=1.0, pitch=0.0, voice=None) -> None:
        from PySide6.QtCore import QLocale
        if lang:
            self._tts.setLocale(QLocale(lang.replace("-", "_")))
        if voice:
            for v in self._tts.availableVoices():
                if v.name() == voice:
                    self._tts.setVoice(v)
        self._tts.setRate(qt_rate(rate))
        self._tts.setPitch(max(-1.0, min(1.0, pitch)))
        self._speaking = True
        self._tts.say(text)

    def _on_state(self, state) -> None:
        if state == self._QTextToSpeech.State.Ready and self._speaking:
            self._speaking = False
            self.finished.emit()

    def stop(self) -> None:
        self._speaking = False
        self._tts.stop()


def detect_engine(parent=None) -> SpeechEngine:
    """Primo motore disponibile: QtTextToSpeech, SAPI (Windows), spd-say, espeak-ng, espeak;
    altrimenti NullEngine."""
    try:
        eng = QtSpeechEngine(parent)
        if eng.available() and eng.voices():
            return eng
        eng.deleteLater()
    except Exception:
        pass
    if sys.platform == "win32":
        sapi = SapiEngine(parent)
        if sapi.available():
            return sapi
        sapi.deleteLater()
    if shutil.which("spd-say"):
        return SpdSayEngine(parent)
    for prog in ("espeak-ng", "espeak"):
        if shutil.which(prog):
            return EspeakEngine(prog, parent)
    return NullEngine(parent)
