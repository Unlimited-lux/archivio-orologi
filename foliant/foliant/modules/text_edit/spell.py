"""Controllo ortografico (TXT-14): logica pura, senza widget.

Dizionari
---------
- Integrati: gli elenchi di parole (con frequenza) distribuiti con `pyspellchecker`
  (italiano, inglese e altre lingue), funzionano senza rete.
- Hunspell: i file `.dic` presenti in `/usr/share/hunspell` o nella cartella
  dell'utente (`~/.local/share/foliant/dictionaries`) aggiungono parole alla lingua
  indicata dal nome del file (`it_IT.dic` -> "it"). **Limite**: le regole di affisso
  (`.aff`) non vengono applicate; si usano solo le radici (il testo prima di "/"),
  come semplice elenco di parole aggiuntive. Una lingua senza dizionario integrato
  ma con un `.dic` funziona quindi solo per le forme base.
  `SpellService.refresh()` rilegge le cartelle: un dizionario installato durante la
  sessione è usato senza riavviare.
- Dizionario personale: UTF-8, una parola per riga, in
  `~/.config/foliant/dizionario_personale.txt` (o `$XDG_CONFIG_HOME/foliant/...`).

Lingua: `/Lang` del catalogo del documento, altrimenti la lingua dell'interfaccia.
(La lingua dei singoli run/elementi di struttura non è ancora considerata.)

Le correzioni nel documento passano da `engine.replace_block` dentro un unico
`Document.change()` (un solo passo di annulla); ogni sostitutivo prende lo stile
del primo carattere della parola.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pymupdf

from ...core import platform
from ...core.errors import FoliantError
from ...i18n import _
from ...i18n import language as ui_language
from . import engine
from .analysis import analyze_page, unframe_matrix
from .model import SOFT_HYPHEN, TextBlock

# lingue con elenco integrato in pyspellchecker che vengono offerte
BUILTIN_LANGUAGES = ("it", "en", "de", "fr", "es", "pt", "nl")
SYSTEM_DICT_DIRS = ("/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts")
MAX_WORD_LEN = 30          # parole più lunghe: nessun suggerimento
MAX_DIST2_LEN = 15         # oltre questa lunghezza solo suggerimenti a distanza 1
APOSTROPHES = "'’ʼ"

# forme elise italiane (prima dell'apostrofo) sempre accettate
IT_ELISIONS = {
    "l", "d", "c", "m", "t", "s", "v", "n", "un", "dell", "all", "nell", "sull", "dall", "coll", "pell",
    "quell", "quest", "bell", "sant", "buon", "grand", "senz", "tutt", "anch", "com", "dov", "cos", "ch",
    "po", "mo", "be", "gl", "agl", "dagl", "negl", "sugl", "degl", "mezz", "ciascun", "nessun", "alcun",
    "qualcun", "ognun", "ventitre", "nient", "quant", "tant", "altr", "mett", "fors",
}
EN_SUFFIXES = {"s", "t", "ll", "re", "ve", "d", "m"}

LANGUAGE_NAMES = {
    "it": "Italiano", "en": "Inglese", "de": "Tedesco", "fr": "Francese", "es": "Spagnolo",
    "pt": "Portoghese", "nl": "Olandese", "ru": "Russo", "pl": "Polacco", "ro": "Rumeno", "sv": "Svedese",
    "da": "Danese", "cs": "Ceco", "el": "Greco", "hu": "Ungherese", "ca": "Catalano", "fi": "Finlandese",
}

TRANSLATIONS = {
    # spell.py
    "Italiano": "Italian", "Inglese": "English", "Tedesco": "German", "Francese": "French",
    "Spagnolo": "Spanish", "Portoghese": "Portuguese", "Olandese": "Dutch", "Russo": "Russian",
    "Polacco": "Polish", "Rumeno": "Romanian", "Svedese": "Swedish", "Danese": "Danish", "Ceco": "Czech",
    "Greco": "Greek", "Ungherese": "Hungarian", "Catalano": "Catalan", "Finlandese": "Finnish",
    "Correzione ortografica": "Spelling correction",
    "Correzioni ortografiche ({n})": "Spelling corrections ({n})",
    "La parola non corrisponde più al testo del blocco.": "The word no longer matches the block text.",
    "Il blocco non esiste più.": "The block no longer exists.",
    "blocco non modificabile: {r}": "block not editable: {r}",
    "Nessuna correzione applicata.": "No correction applied.",
    "Impossibile leggere il file: {e}": "Cannot read the file: {e}",
    "Impossibile scrivere il file: {e}": "Cannot write the file: {e}",
    "Nessun dizionario per la lingua «{l}».": "No dictionary for the language «{l}».",
    # spell_ui.py
    "Controlla ortografia del documento": "Check document spelling",
    "Controllo ortografico": "Spell check",
    "Ignora": "Ignore",
    "Ignora tutto": "Ignore all",
    "Aggiungi al dizionario": "Add to dictionary",
    "Aggiungi al dizionario personale": "Add to personal dictionary",
    "Nessun suggerimento": "No suggestions",
    "Lingua del controllo": "Spell check language",
    "Parola": "Word",
    "Pagina": "Page",
    "Contesto": "Context",
    "Lingua:": "Language:",
    "Non nel dizionario:": "Not in dictionary:",
    "Sostituisci con:": "Replace with:",
    "Suggerimenti:": "Suggestions:",
    "Cambia": "Change",
    "Cambia tutto": "Change all",
    "Dizionari…": "Dictionaries…",
    "Controlla di nuovo": "Check again",
    "Chiudi": "Close",
    "Nessun documento aperto.": "No document open.",
    "Nessun errore ortografico trovato.": "No spelling errors found.",
    "{n} parole da controllare.": "{n} words to check.",
    "Questa parola è in un blocco non modificabile: la correzione non è applicabile ({r}).":
        "This word is in a non-editable block: the correction cannot be applied ({r}).",
    "Correzione non riuscita": "Correction failed",
    "{n} correzioni applicate.": "{n} corrections applied.",
    "{n} correzioni non applicabili.": "{n} corrections could not be applied.",
    "Dizionari": "Dictionaries",
    "Lingue installate": "Installed languages",
    "integrato": "built-in",
    "Hunspell (solo radici): {p}": "Hunspell (stems only): {p}",
    "Cartella dei dizionari dell'utente: {p}": "User dictionary folder: {p}",
    "Installa dizionario…": "Install dictionary…",
    "Rimuovi dizionario": "Remove dictionary",
    "Aggiorna": "Refresh",
    "Dizionari Hunspell (*.dic)": "Hunspell dictionaries (*.dic)",
    "Dizionario installato: {n}": "Dictionary installed: {n}",
    "Si possono rimuovere solo i dizionari installati nella cartella dell'utente.":
        "Only dictionaries installed in the user folder can be removed.",
    "Rimuovere il dizionario «{n}»?": "Remove the dictionary «{n}»?",
    "Dizionario personale": "Personal dictionary",
    "File: {p}": "File: {p}",
    "Nuova parola": "New word",
    "Aggiungi": "Add",
    "Rimuovi": "Remove",
    "Importa…": "Import…",
    "Esporta…": "Export…",
    "Importa dizionario personale": "Import personal dictionary",
    "Esporta dizionario personale": "Export personal dictionary",
    "Elenchi di parole (*.txt *.dic);;Tutti i file (*)": "Word lists (*.txt *.dic);;All files (*)",
    "Elenchi di parole (*.txt)": "Word lists (*.txt)",
    "{n} parole importate.": "{n} words imported.",
    "Dizionario personale esportato.": "Personal dictionary exported.",
    "Gestisci i dizionari: {l} non è installata.": "Manage dictionaries: {l} is not installed.",
}


class SpellError(FoliantError):
    """Errore del controllo ortografico con messaggio per l'utente."""


# ====================================================================== tokenizzazione
_LETTER = r"[^\W_]"
_WORD_RX = re.compile(
    _LETTER + r"(?:" + _LETTER + r"|[" + APOSTROPHES + r"](?=" + _LETTER + r")|­|-\n(?=[^\W\d_]))*"
)
_URL_RX = re.compile(r"(?:\b(?:https?|ftp)://|\bwww\.)[^\s<>\"]+", re.IGNORECASE)
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_APOS_RX = re.compile("[" + APOSTROPHES + "]")


@dataclass
class SpellOptions:
    ignore_digits: bool = True        # parole con cifre
    ignore_caps: bool = True          # parole tutte maiuscole (sigle)
    ignore_urls: bool = True
    ignore_emails: bool = True
    min_length: int = 2               # le lettere isolate non vengono controllate


def clean_word(token: str) -> str:
    """Forma da cercare: senza trattini di sillabazione e a capo, apostrofi uniformati."""
    t = token.replace(SOFT_HYPHEN, "").replace("-\n", "")
    t = _APOS_RX.sub("'", t)
    return unicodedata.normalize("NFC", t)


def match_case(original: str, replacement: str) -> str:
    """Adatta maiuscole/minuscole del sostitutivo a quelle della parola originale."""
    if not replacement or not original:
        return replacement
    if replacement != replacement.lower():
        return replacement           # il sostitutivo ha già maiuscole proprie
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


# ====================================================================== lessico
class _Lexicon:
    """Parole di una lingua (minuscole) con frequenza, e indici per i suggerimenti."""

    def __init__(self, words: dict[str, int]):
        self.words = words
        letters = set()
        for w in words:
            letters.update(w)
        self.letters = "".join(sorted(c for c in letters if c.isalpha()))
        self._buckets: dict[tuple[str, int], list[str]] | None = None
        self._tails: dict[tuple[str, int], list[str]] | None = None

    def __contains__(self, word: str) -> bool:
        return word in self.words

    def freq(self, word: str) -> int:
        return self.words.get(word, 0)

    def buckets(self):
        if self._buckets is None:
            heads: dict[tuple[str, int], list[str]] = {}
            tails: dict[tuple[str, int], list[str]] = {}
            for w in self.words:
                n = len(w)
                heads.setdefault((w[0], n), []).append(w)
                if n >= 4:
                    tails.setdefault((w[-3:], n), []).append(w)
            self._buckets, self._tails = heads, tails
        return self._buckets, self._tails


def _edits1(word: str, letters: str) -> set[str]:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [a + b[1:] for a, b in splits if b]
    transposes = [a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1]
    replaces = [a + c + b[1:] for a, b in splits if b for c in letters]
    inserts = [a + c + b for a, b in splits for c in letters]
    return set(deletes + transposes + replaces + inserts)


def _distance(a: str, b: str, limit: int = 2) -> int:
    """Distanza di Damerau-Levenshtein (trasposizioni adiacenti), interrotta oltre `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    la, lb = len(a), len(b)
    prev2 = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        best = cur[0]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            if v < best:
                best = v
        if best > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[lb]


# ====================================================================== risultati
@dataclass
class Issue:
    """Parola sconosciuta trovata nel testo di pagina."""

    page: int
    block: int                               # indice del blocco nella pagina
    bbox: pymupdf.Rect                       # riquadro del blocco (riquadro di lavoro)
    start: int                               # posizione in `TextBlock.text`
    end: int
    word: str                                # testo originale (come appare nel blocco)
    context: str
    suggestions: list[str] = field(default_factory=list)
    editable: bool = True
    reason: str = ""
    rects: list[pymupdf.Rect] = field(default_factory=list)   # pagina non ruotata

    @property
    def key(self) -> tuple:
        return (self.page, self.word.lower(), self.context)


@dataclass
class CorrectionSummary:
    applied: int = 0
    failed: list[tuple[Issue, str]] = field(default_factory=list)
    substitutions: list = field(default_factory=list)


class _NothingApplied(Exception):
    pass


# ====================================================================== servizio
def _default_personal_path() -> Path:
    return platform.app_dir("config") / "dizionario_personale.txt"


def default_user_dict_dir() -> Path:
    return platform.app_dir("data") / "dictionaries"


def dic_language(path: str | os.PathLike) -> str:
    """Lingua (codice primario minuscolo) dal nome di un file `.dic`: `it_IT.dic` -> "it"."""
    stem = Path(path).stem
    return re.split(r"[_\-.]", stem)[0].lower()


def read_dic_stems(path: str | os.PathLike) -> list[str]:
    """Radici di un file `.dic` Hunspell (prima di "/"); la prima riga (conteggio) è saltata."""
    raw = Path(path).read_bytes()
    enc = "utf-8"
    aff = Path(path).with_suffix(".aff")
    try:
        if aff.exists():
            for line in aff.read_text("latin-1", errors="replace").splitlines():
                if line.startswith("SET "):
                    enc = line.split()[1].strip()
                    break
    except OSError:
        pass
    try:
        text = raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("latin-1", errors="replace")
    out = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line or (i == 0 and line.isdigit()) or line.startswith("#"):
            continue
        stem = line.split("/", 1)[0].split("\t", 1)[0].split(" ", 1)[0].strip()
        if stem and not any(c.isdigit() for c in stem):
            out.append(stem)
    return out


class SpellService:
    """Dizionari, dizionario personale, regole di esclusione e suggerimenti."""

    def __init__(self, personal_path: str | os.PathLike | None = None,
                 user_dict_dir: str | os.PathLike | None = None,
                 system_dict_dirs: Iterable[str | os.PathLike] | None = None,
                 builtin_languages: Iterable[str] = BUILTIN_LANGUAGES,
                 options: SpellOptions | None = None):
        self.personal_path = Path(personal_path) if personal_path else _default_personal_path()
        self.user_dict_dir = Path(user_dict_dir) if user_dict_dir else default_user_dict_dir()
        self.system_dict_dirs = [Path(p) for p in (SYSTEM_DICT_DIRS if system_dict_dirs is None
                                                   else system_dict_dirs)]
        self.builtin_languages = tuple(builtin_languages)
        self.options = options or SpellOptions()
        self.ignored: set[str] = set()                 # «Ignora tutto» (sessione)
        self._lexicons: dict[str, tuple[tuple, _Lexicon]] = {}
        self._builtin_words: dict[str, dict[str, int]] = {}
        self._dic_files: dict[str, list[tuple[str, float]]] = {}
        self._suggest_cache: dict[tuple[str, str, int], list[str]] = {}
        self._personal: dict[str, str] = {}            # minuscola -> forma salvata
        self._load_personal()
        self.refresh()

    # ------------------------------------------------------------ dizionari
    def refresh(self) -> None:
        """Rilegge le cartelle dei dizionari Hunspell (installazioni/rimozioni senza riavvio)."""
        found: dict[str, list[tuple[str, float]]] = {}
        for d in [*self.system_dict_dirs, self.user_dict_dir]:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for name in names:
                if not name.lower().endswith(".dic") or name.startswith("hyph_"):
                    continue
                path = os.path.join(d, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                found.setdefault(dic_language(name), []).append((path, mtime))
        self._dic_files = found
        self._suggest_cache.clear()

    def dictionary_files(self, lang: str | None = None) -> dict[str, list[str]]:
        """File `.dic` trovati, per lingua."""
        out = {k: [p for p, _m in v] for k, v in self._dic_files.items()}
        return {lang: out.get(lang, [])} if lang else out

    def _builtin_available(self, lang: str) -> bool:
        if lang not in self.builtin_languages:
            return False
        try:
            from spellchecker import SpellChecker  # noqa: F401
        except Exception:
            return False
        return True

    def available_languages(self) -> list[str]:
        langs = [lang for lang in self.builtin_languages if self._builtin_available(lang)]
        langs += sorted(lang for lang in self._dic_files if lang not in langs)
        return langs

    def has_language(self, lang: str) -> bool:
        return bool(lang) and (self._builtin_available(lang) or lang in self._dic_files)

    @staticmethod
    def language_name(lang: str) -> str:
        name = LANGUAGE_NAMES.get(lang)
        return _(name) if name else lang

    def _load_builtin(self, lang: str) -> dict[str, int]:
        words = self._builtin_words.get(lang)
        if words is None:
            words = {}
            if self._builtin_available(lang):
                try:
                    from spellchecker import SpellChecker
                    sc = SpellChecker(language=lang, distance=1, case_sensitive=False)
                    words = dict(sc.word_frequency.dictionary)
                except Exception:
                    words = {}
            self._builtin_words[lang] = words
        return words

    def lexicon(self, lang: str) -> _Lexicon | None:
        """Lessico della lingua (caricato al primo uso e tenuto in memoria), None se manca."""
        files = tuple(self._dic_files.get(lang, []))
        hit = self._lexicons.get(lang)
        if hit is not None and hit[0] == files:
            return hit[1]
        if not self.has_language(lang):
            return None
        words = dict(self._load_builtin(lang)) if files else self._load_builtin(lang)
        for path, _m in files:
            try:
                stems = read_dic_stems(path)
            except OSError:
                continue
            for s in stems:
                low = unicodedata.normalize("NFC", s.lower())
                if low not in words:
                    words[low] = 1
        if not words:
            return None
        lex = _Lexicon(words)
        self._lexicons[lang] = (files, lex)
        return lex

    # ------------------------------------------------------------ dizionario personale
    def _load_personal(self) -> None:
        self._personal = {}
        try:
            text = self.personal_path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            return
        for w in _word_lines(text):
            self._personal.setdefault(w.lower(), w)

    def _save_personal(self) -> None:
        words = sorted(self._personal.values(), key=lambda w: (w.lower(), w))
        _atomic_write_text(self.personal_path, "".join(w + "\n" for w in words))

    def personal_words(self) -> list[str]:
        return sorted(self._personal.values(), key=lambda w: (w.lower(), w))

    def set_personal_words(self, words: Iterable[str]) -> None:
        self._personal = {}
        for w in words:
            w = clean_word(w.strip())
            if w:
                self._personal.setdefault(w.lower(), w)
        self._save_personal()

    def add_to_personal(self, word: str) -> None:
        w = clean_word(word.strip())
        if not w:
            return
        self._personal[w.lower()] = w
        self._save_personal()

    def remove_from_personal(self, word: str) -> None:
        if self._personal.pop(clean_word(word.strip()).lower(), None) is not None:
            self._save_personal()

    def export_personal(self, path: str | os.PathLike) -> None:
        try:
            _atomic_write_text(Path(path), "".join(w + "\n" for w in self.personal_words()))
        except OSError as e:
            raise SpellError(_("Impossibile scrivere il file: {e}").format(e=e)) from e

    def import_personal(self, path: str | os.PathLike) -> int:
        """Aggiunge le parole di un file (una per riga, accetta anche `.dic`); restituisce quante sono nuove."""
        try:
            raw = Path(path).read_bytes()
        except OSError as e:
            raise SpellError(_("Impossibile leggere il file: {e}").format(e=e)) from e
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        added = 0
        for w in _word_lines(text):
            w = clean_word(w.split("/", 1)[0])
            if w.isdigit() or not w:
                continue
            if w.lower() not in self._personal:
                self._personal[w.lower()] = w
                added += 1
        self._save_personal()
        return added

    def ignore_all(self, word: str) -> None:
        self.ignored.add(clean_word(word).lower())

    # ------------------------------------------------------------ verifica
    def is_known(self, word: str, lang: str) -> bool:
        """La parola (già pulita) è corretta? Ricerca senza distinzione di maiuscole."""
        low = clean_word(word).lower()
        if not low:
            return True
        if low in self._personal or low in self.ignored:
            return True
        lex = self.lexicon(lang)
        if lex is None:
            return True                 # lingua senza dizionario: nessuna segnalazione
        return low in lex

    def _check_token(self, token: str, start: int, lang: str, lex: _Lexicon) -> list[tuple[int, int, str]]:
        """Parti errate di un token (posizioni assolute)."""
        word = clean_word(token)
        low = word.lower()
        if low in self._personal or low in self.ignored or low in lex:
            return []
        if "'" not in word:
            return [(start, start + len(token), token)]
        # elisioni («dell'anno») e forme con apostrofo («John's»): controllo per parti
        out = []
        pieces = [(m.start(), m.end()) for m in re.finditer(r"[^" + APOSTROPHES + r"]+", token)]
        for k, (a, b) in enumerate(pieces):
            part = token[a:b]
            pl = clean_word(part).lower()
            if len(pl) < self.options.min_length:
                continue
            if k < len(pieces) - 1:
                # parte prima dell'apostrofo
                if (pl + "'") in lex or pl in lex or pl in self._personal or pl in self.ignored:
                    continue
                if lang == "it" and (pl in IT_ELISIONS or any((pl + v) in lex for v in "oaei")):
                    continue
            else:
                if lang == "en" and pl in EN_SUFFIXES:
                    continue
                if pl in lex or pl in self._personal or pl in self.ignored:
                    continue
            out.append((start + a, start + b, part))
        return out

    def misspelled(self, text: str, lang: str, options: SpellOptions | None = None
                   ) -> list[tuple[int, int, str]]:
        """Parole sconosciute di `text`: lista di (inizio, fine, parola) nel testo dato."""
        lex = self.lexicon(lang)
        if lex is None or not text:
            return []
        opt = options or self.options
        skip: list[tuple[int, int]] = []
        if opt.ignore_urls and ("://" in text or "www." in text.lower()):
            skip += [m.span() for m in _URL_RX.finditer(text)]
        if opt.ignore_emails and "@" in text:
            skip += [m.span() for m in _EMAIL_RX.finditer(text)]
        out: list[tuple[int, int, str]] = []
        for m in _WORD_RX.finditer(text):
            s, e = m.span()
            token = m.group()
            if skip and any(a < e and s < b for a, b in skip):
                continue
            if opt.ignore_digits and any(c.isdigit() for c in token):
                continue
            # parola attaccata a cifre («3kg», «A4»): ignorata con le cifre
            if opt.ignore_digits and ((s > 0 and text[s - 1].isdigit()) or (e < len(text) and text[e].isdigit())):
                continue
            word = clean_word(token)
            letters = [c for c in word if c.isalpha()]
            if len(letters) < opt.min_length:
                continue
            if opt.ignore_caps and len(letters) > 1 and all(c.isupper() for c in letters):
                continue
            out.extend(self._check_token(token, s, lang, lex))
        return out

    # ------------------------------------------------------------ suggerimenti
    def suggestions(self, word: str, lang: str, n: int = 5) -> list[str]:
        """Correzioni proposte, dalla più probabile (distanza di modifica, poi frequenza)."""
        clean = clean_word(word)
        low = clean.lower()
        if not low or len(low) > MAX_WORD_LEN:
            return []
        key = (lang, low, n)
        hit = self._suggest_cache.get(key)
        if hit is None:
            hit = self._compute_suggestions(low, lang, n)
            if len(self._suggest_cache) > 2000:
                self._suggest_cache.clear()
            self._suggest_cache[key] = hit
        out, seen = [], set()
        for s in hit:
            s2 = match_case(clean, s)
            if s2 not in seen:
                seen.add(s2)
                out.append(s2)
        return out

    def _compute_suggestions(self, low: str, lang: str, n: int) -> list[str]:
        lex = self.lexicon(lang)
        if lex is None:
            return []
        personal = {k: 1 for k in self._personal}

        def score(w: str, d: int):
            # distanza, poi stessa iniziale, poi frequenza
            return (d, 0 if w[:1] == low[:1] else 1, -(lex.freq(w) or personal.get(w, 0)), w)

        cands: dict[str, int] = {}
        for e in _edits1(low, lex.letters + "'"):
            if e != low and (e in lex or e in personal):
                cands[e] = 1
        if len(cands) < n and len(low) <= MAX_DIST2_LEN:
            heads, tails = lex.buckets()
            L = len(low)
            pool: list[str] = []
            for k in range(max(1, L - 2), L + 3):
                pool.extend(heads.get((low[0], k), ()))
                if L >= 4:
                    pool.extend(tails.get((low[-3:], k), ()))
            for w in pool:
                if w in cands or w == low:
                    continue
                d = _distance(low, w, 2)
                if d <= 2:
                    cands[w] = d
        ranked = sorted(cands, key=lambda w: score(w, cands[w]))
        return ranked[:n]

    # ------------------------------------------------------------ documento
    def check_document(self, pdf: pymupdf.Document, lang: str, pages: Iterable[int] | None = None,
                       granularity: str = "paragraph", suggest: bool = True) -> list[Issue]:
        """Parole sconosciute nel testo di pagina (va chiamata tenendo ENGINE_LOCK).

        Con `suggest=False` i suggerimenti non vengono calcolati (li si chiede poi con
        `suggestions()` per la parola selezionata): utile per documenti lunghi.
        """
        if self.lexicon(lang) is None:
            return []
        out: list[Issue] = []
        indices = range(pdf.page_count) if pages is None else [p for p in pages if 0 <= p < pdf.page_count]
        for p in indices:
            page = pdf[p]
            unframe = unframe_matrix(page)
            for block in _page_blocks(page, p, granularity):
                out.extend(self.block_issues(block, lang, unframe, page, suggest))
        return out

    def block_issues(self, block: TextBlock, lang: str, unframe: pymupdf.Matrix | None = None,
                     page: pymupdf.Page | None = None, suggest: bool = True) -> list[Issue]:
        text = block.text
        found = self.misspelled(text, lang)
        if not found:
            return []
        unframe = unframe or pymupdf.Matrix(1, 0, 0, 1, 0, 0)
        spans = _line_spans(block)
        reason = block.reason
        out = []
        for s, e, word in found:
            out.append(Issue(
                page=block.page, block=block.index, bbox=pymupdf.Rect(block.bbox), start=s, end=e, word=word,
                context=_context(text, s, e),
                suggestions=self.suggestions(word, lang) if suggest else [],
                editable=not reason, reason=_("blocco non modificabile: {r}").format(r=reason) if reason else "",
                rects=_rects(block, spans, s, e, unframe, page),
            ))
        return out


# ====================================================================== applicazione delle correzioni
def _locate_block(blocks: list[TextBlock], issue: Issue) -> TextBlock | None:
    best, best_iou = None, 0.0
    for b in blocks:
        inter = pymupdf.Rect(b.bbox) & issue.bbox
        if inter.is_empty:
            continue
        iou = inter.get_area() / max((pymupdf.Rect(b.bbox) | issue.bbox).get_area(), 1e-6)
        if iou > best_iou:
            best, best_iou = b, iou
    return best if best_iou > 0.2 else None


def _locate_word(text: str, issue: Issue) -> tuple[int, int] | None:
    if text[issue.start:issue.end] == issue.word:
        return issue.start, issue.end
    rx = re.compile(r"(?<![^\W_])" + re.escape(issue.word) + r"(?![^\W_])")
    positions = [m.start() for m in rx.finditer(text)]
    if not positions:
        return None
    s = min(positions, key=lambda x: abs(x - issue.start))
    return s, s + len(issue.word)


@dataclass
class _Span:
    start: int
    end: int


def apply_corrections(document, corrections: Iterable[tuple[Issue, str]], label: str | None = None,
                      granularity: str = "paragraph") -> CorrectionSummary:
    """Applica più correzioni in un unico passo annullabile (`document.change(label)`).

    Le pagine vengono rianalizzate dentro la modifica; ogni sostitutivo prende lo stile
    del primo carattere della parola. Se nessuna correzione è applicabile il documento
    resta invariato e non si crea un passo di annulla.
    """
    from .search import replace_in_block

    items = [(i, r) for i, r in corrections]
    summary = CorrectionSummary()
    todo = [(i, r) for i, r in items if i.editable]
    summary.failed.extend((i, i.reason) for i, r in items if not i.editable)
    if not todo:
        return summary
    if label is None:
        label = _("Correzione ortografica") if len(todo) == 1 else \
            _("Correzioni ortografiche ({n})").format(n=len(todo))
    by_page: dict[int, list[tuple[Issue, str]]] = {}
    for i, r in todo:
        by_page.setdefault(i.page, []).append((i, r))
    try:
        with document.change(label) as pdf:
            for p in sorted(by_page):
                if not 0 <= p < pdf.page_count:
                    summary.failed.extend((i, _("Il blocco non esiste più.")) for i, _r in by_page[p])
                    continue
                blocks = analyze_page(pdf[p], p, granularity)
                per_block: dict[int, tuple[TextBlock, list[tuple[_Span, str, Issue]]]] = {}
                for issue, rep in by_page[p]:
                    block = _locate_block(blocks, issue)
                    if block is None:
                        summary.failed.append((issue, _("Il blocco non esiste più.")))
                        continue
                    if not block.editable:
                        summary.failed.append((issue, _("blocco non modificabile: {r}").format(r=block.reason)))
                        continue
                    span = _locate_word(block.text, issue)
                    if span is None:
                        summary.failed.append((issue, _("La parola non corrisponde più al testo del blocco.")))
                        continue
                    entry = per_block.setdefault(block.index, (block, []))
                    if any(sp.start < span[1] and span[0] < sp.end for sp, _r, _i in entry[1]):
                        continue      # stessa parola indicata due volte
                    entry[1].append((_Span(*span), rep, issue))
                # dal basso verso l'alto: un blocco che cresce non tocca quelli già riscritti
                for idx in sorted(per_block, reverse=True):
                    block, entries = per_block[idx]
                    paragraphs = replace_in_block(block, [sp for sp, _r, _i in entries],
                                                  [r for _sp, r, _i in entries])
                    try:
                        result = engine.replace_block(pdf, p, block, paragraphs)
                    except engine.TextEditError as e:
                        summary.failed.extend((i, str(e)) for _sp, _r, i in entries)
                        continue
                    summary.applied += len(entries)
                    summary.substitutions.extend(getattr(result, "substitutions", []) or [])
            if summary.applied == 0:
                raise _NothingApplied()
    except _NothingApplied:
        pass
    return summary


# ====================================================================== lingua
def document_language(pdf: pymupdf.Document) -> str | None:
    """Codice primario della lingua da `/Lang` del catalogo (es. "it-IT" -> "it")."""
    try:
        kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "Lang")
    except Exception:
        return None
    if kind not in ("string", "name") or not val:
        return None
    tag = val.strip().strip("()/").strip()
    code = re.split(r"[-_]", tag)[0].lower() if tag else ""
    return code if re.fullmatch(r"[a-z]{2,3}", code) else None


def choose_language(service: SpellService, pdf: pymupdf.Document | None = None) -> str:
    """Lingua del controllo: quella del documento, poi dell'interfaccia, poi la prima disponibile."""
    if pdf is not None:
        lang = document_language(pdf)
        if lang and service.has_language(lang):
            return lang
    lang = ui_language()
    if service.has_language(lang):
        return lang
    avail = service.available_languages()
    return avail[0] if avail else lang


_SERVICE: SpellService | None = None


def shared_service() -> SpellService:
    """Servizio condiviso dall'applicazione (dizionario personale e «Ignora tutto» comuni)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SpellService()
    return _SERVICE


# ====================================================================== utilità
def _word_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        w = line.strip().lstrip("﻿")
        if w and not w.startswith("#"):
            out.append(w)
    return out


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _page_blocks(page: pymupdf.Page, p: int, granularity: str) -> list[TextBlock]:
    try:
        from .search import page_blocks
        return page_blocks(page, p, granularity)
    except Exception:
        return analyze_page(page, p, granularity)


def _line_spans(block: TextBlock):
    try:
        from .search import _line_spans as spans
        return spans(block)
    except Exception:
        return None


def _rects(block: TextBlock, spans, s: int, e: int, unframe: pymupdf.Matrix, page) -> list[pymupdf.Rect]:
    try:
        from .search import _match_rects
        rects = _match_rects(block, spans, s, e, unframe, page)
        if rects:
            return rects
    except Exception:
        pass
    return [pymupdf.Rect(block.bbox) * unframe]


def _context(text: str, s: int, e: int, n: int = 30) -> str:
    a, b = max(0, s - n), min(len(text), e + n)
    out = ("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else "")
    return re.sub(r"\s+", " ", out).strip()
