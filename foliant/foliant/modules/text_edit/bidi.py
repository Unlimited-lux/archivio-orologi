"""Scritture da destra a sinistra (TXT-21): direzione del paragrafo, ordine visivo delle righe e
forme contestuali dell'arabo (forme di presentazione Unicode).

Semplificazione dell'algoritmo bidirezionale (UAX #9) sufficiente per paragrafi in ebraico o arabo con
numeri e parole latine: le sequenze da sinistra a destra (cifre, latino) restano nel loro ordine, il resto
della riga è disposto da destra a sinistra. Il motore estrae poi il testo in ordine logico.
"""

from __future__ import annotations

import unicodedata

# lettere arabe: (isolata, finale, iniziale, mediana); None = forma assente (la lettera non si lega dopo)
_FORMS: dict[str, tuple] = {
    "ء": ("ﺀ", None, None, None),
    "آ": ("ﺁ", "ﺂ", None, None),
    "أ": ("ﺃ", "ﺄ", None, None),
    "ؤ": ("ﺅ", "ﺆ", None, None),
    "إ": ("ﺇ", "ﺈ", None, None),
    "ئ": ("ﺉ", "ﺊ", "ﺋ", "ﺌ"),
    "ا": ("ﺍ", "ﺎ", None, None),
    "ب": ("ﺏ", "ﺐ", "ﺑ", "ﺒ"),
    "ة": ("ﺓ", "ﺔ", None, None),
    "ت": ("ﺕ", "ﺖ", "ﺗ", "ﺘ"),
    "ث": ("ﺙ", "ﺚ", "ﺛ", "ﺜ"),
    "ج": ("ﺝ", "ﺞ", "ﺟ", "ﺠ"),
    "ح": ("ﺡ", "ﺢ", "ﺣ", "ﺤ"),
    "خ": ("ﺥ", "ﺦ", "ﺧ", "ﺨ"),
    "د": ("ﺩ", "ﺪ", None, None),
    "ذ": ("ﺫ", "ﺬ", None, None),
    "ر": ("ﺭ", "ﺮ", None, None),
    "ز": ("ﺯ", "ﺰ", None, None),
    "س": ("ﺱ", "ﺲ", "ﺳ", "ﺴ"),
    "ش": ("ﺵ", "ﺶ", "ﺷ", "ﺸ"),
    "ص": ("ﺹ", "ﺺ", "ﺻ", "ﺼ"),
    "ض": ("ﺽ", "ﺾ", "ﺿ", "ﻀ"),
    "ط": ("ﻁ", "ﻂ", "ﻃ", "ﻄ"),
    "ظ": ("ﻅ", "ﻆ", "ﻇ", "ﻈ"),
    "ع": ("ﻉ", "ﻊ", "ﻋ", "ﻌ"),
    "غ": ("ﻍ", "ﻎ", "ﻏ", "ﻐ"),
    "ف": ("ﻑ", "ﻒ", "ﻓ", "ﻔ"),
    "ق": ("ﻕ", "ﻖ", "ﻗ", "ﻘ"),
    "ك": ("ﻙ", "ﻚ", "ﻛ", "ﻜ"),
    "ل": ("ﻝ", "ﻞ", "ﻟ", "ﻠ"),
    "م": ("ﻡ", "ﻢ", "ﻣ", "ﻤ"),
    "ن": ("ﻥ", "ﻦ", "ﻧ", "ﻨ"),
    "ه": ("ﻩ", "ﻪ", "ﻫ", "ﻬ"),
    "و": ("ﻭ", "ﻮ", None, None),
    "ى": ("ﻯ", "ﻰ", None, None),
    "ي": ("ﻱ", "ﻲ", "ﻳ", "ﻴ"),
}
# legature obbligatorie lam + alef: (isolata, finale)
_LAM_ALEF = {"آ": ("ﻵ", "ﻶ"), "أ": ("ﻷ", "ﻸ"),
             "إ": ("ﻹ", "ﻺ"), "ا": ("ﻻ", "ﻼ")}


def is_rtl_char(ch: str) -> bool:
    return unicodedata.bidirectional(ch) in ("R", "AL")


def is_ltr_char(ch: str) -> bool:
    return unicodedata.bidirectional(ch) in ("L", "EN", "AN")


def paragraph_is_rtl(text: str) -> bool:
    """Direzione del paragrafo: il primo carattere con direzione forte decide (UAX #9, P2–P3)."""
    for ch in text:
        bd = unicodedata.bidirectional(ch)
        if bd in ("R", "AL"):
            return True
        if bd == "L":
            return False
    return False


def _joins_before(ch: str) -> bool:
    """La lettera si lega alla successiva (ha una forma iniziale/mediana)."""
    f = _FORMS.get(ch)
    return bool(f and f[2])


def _transparent(ch: str) -> bool:
    return unicodedata.category(ch) == "Mn"


def shape_arabic(text: str) -> str:
    """Forme contestuali (iniziale, mediana, finale, isolata) e legatura lam-alef, in ordine logico."""
    if not any(ch in _FORMS for ch in text):
        return text
    out: list[str] = []
    chars = list(text)
    i = 0
    n = len(chars)

    def prev_joining(k: int) -> bool:
        k -= 1
        while k >= 0 and _transparent(chars[k]):
            k -= 1
        return k >= 0 and _joins_before(chars[k])

    def next_letter(k: int) -> str | None:
        k += 1
        while k < n and _transparent(chars[k]):
            k += 1
        return chars[k] if k < n else None

    while i < n:
        ch = chars[i]
        forms = _FORMS.get(ch)
        if forms is None:
            out.append(ch)
            i += 1
            continue
        nxt = next_letter(i)
        if ch == "ل" and nxt in _LAM_ALEF:
            iso, fin = _LAM_ALEF[nxt]
            out.append(fin if prev_joining(i) else iso)
            # salta la alef (e i segni trasparenti tra le due)
            i += 1
            while i < n and chars[i] != nxt:
                out.append(chars[i])
                i += 1
            i += 1
            continue
        before = prev_joining(i)
        after = nxt is not None and nxt in _FORMS and forms[2] is not None
        if before and after and forms[3]:
            out.append(forms[3])
        elif before and forms[1]:
            out.append(forms[1])
        elif after and forms[2]:
            out.append(forms[2])
        else:
            out.append(forms[0])
        i += 1
    return "".join(out)


def unshape(text: str) -> str:
    """Forme di presentazione -> lettere di base (per il testo logico estratto)."""
    if not any("ﭐ" <= c <= "﷿" or "ﹰ" <= c <= "﻿" for c in text):
        return text
    return "".join(unicodedata.normalize("NFKC", c) if ("ﭐ" <= c <= "﷿" or "ﹰ" <= c <= "﻿")
                   else c for c in text)


_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}


def visual_order(tokens: list[tuple[str, object, bool]]) -> list[tuple[str, object, bool]]:
    """Riordina i pezzi di una riga di un paragrafo da destra a sinistra in ordine visivo
    (da sinistra a destra sulla pagina). `tokens`: (testo, stile, è_spazio) in ordine logico."""
    def kind(text: str) -> str:
        if any(is_rtl_char(c) for c in text):
            return "R"
        if any(is_ltr_char(c) for c in text):
            return "L"
        return "N"

    kinds = [kind(t) for t, _s, _sp in tokens]
    # i neutri tra due sequenze L restano con loro
    groups: list[tuple[str, list]] = []
    i = 0
    while i < len(tokens):
        if kinds[i] == "L":
            j = i
            while j + 1 < len(tokens):
                k = j + 1
                while k < len(tokens) and kinds[k] == "N":
                    k += 1
                if k < len(tokens) and kinds[k] == "L":
                    j = k
                else:
                    break
            groups.append(("L", tokens[i:j + 1]))
            i = j + 1
        else:
            groups.append(("R", [tokens[i]]))
            i += 1
    out: list[tuple[str, object, bool]] = []
    for g, items in reversed(groups):
        if g == "L":
            out.extend(items)
        else:
            for t, s, sp in reversed(items):
                out.append(("".join(_MIRROR.get(c, c) for c in reversed(t)), s, sp))
    return out
