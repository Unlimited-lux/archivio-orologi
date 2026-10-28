"""Logica pura della presentazione (VIS-32): navigazione, schermo nero/bianco,
tempi di avanzamento e transizioni. Nessun widget: testabile senza tempi reali.

Il tempo entra solo tramite `tick(dt)`: chi usa lo stato (la finestra di
presentazione) misura il tempo trascorso con un orologio iniettabile.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCREEN_PAGE = "page"
SCREEN_BLACK = "black"
SCREEN_WHITE = "white"

TRANSITION_NONE = "none"
TRANSITION_FADE = "fade"
DEFAULT_FADE = 0.4      # secondi
MAX_FADE = 2.0

# Comandi da tastiera (indipendenti da Qt: la finestra traduce i tasti in nomi).
NEXT_KEYS = {"space", "return", "enter", "right", "down", "pagedown"}
PREV_KEYS = {"left", "up", "pageup", "backspace"}


def parse_number(kind: str, value: str) -> float | None:
    """Valore numerico da `xref_get_key` (('int', '5'), ('float', '1.5'))."""
    if kind not in ("int", "float", "real"):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


@dataclass
class PageTiming:
    """Tempi letti dal documento per una pagina."""

    duration: float | None = None          # /Dur in secondi
    transition: str = TRANSITION_NONE      # tipo risolto (fade/none)
    transition_duration: float = DEFAULT_FADE


def resolve_transition(style: str | None, duration: float | None) -> tuple[str, float]:
    """Traduce `/Trans /S` in una transizione supportata.

    `/R` (sostituzione) = nessuna transizione; ogni altro stile (Split, Blinds,
    Box, Wipe, Dissolve, Glitter, Fly, Push, Cover, Uncover, Fade) non è
    riprodotto e ripiega sulla dissolvenza.
    """
    d = DEFAULT_FADE if duration is None else max(0.0, min(MAX_FADE, duration))
    if not style:
        return TRANSITION_NONE, 0.0
    name = style.lstrip("/")
    if name == "R":
        return TRANSITION_NONE, 0.0
    return TRANSITION_FADE, d


@dataclass
class PresentationOptions:
    use_document_timing: bool = True   # /Dur e /Trans del documento
    interval: float | None = None      # secondi per pagina scelti dall'utente (None = manuale)
    transition: str = TRANSITION_FADE  # transizione scelta dall'utente (pagine senza /Trans)
    loop: bool = False                 # ciclo continuo


@dataclass
class PresentationState:
    page_count: int
    page: int = 0
    timings: list[PageTiming] = field(default_factory=list)
    options: PresentationOptions = field(default_factory=PresentationOptions)
    screen: str = SCREEN_PAGE
    typed: str = ""
    elapsed_on_page: float = 0.0
    paused: bool = False
    finished: bool = False

    def __post_init__(self) -> None:
        self.page = max(0, min(self.page, self.page_count - 1))
        if len(self.timings) < self.page_count:
            self.timings = list(self.timings) + [PageTiming() for _ in range(self.page_count - len(self.timings))]

    # ---------------------------------------------------------------- navigazione
    def go(self, index: int) -> bool:
        """Va alla pagina `index` (limitata all'intervallo). True se è cambiata."""
        index = max(0, min(index, self.page_count - 1))
        self.screen = SCREEN_PAGE
        self.elapsed_on_page = 0.0
        self.finished = False
        if index == self.page:
            return False
        self.page = index
        return True

    def next(self) -> bool:
        if self.page >= self.page_count - 1:
            if self.options.loop and self.page_count > 1:
                return self.go(0)
            self.finished = True
            return False
        return self.go(self.page + 1)

    def prev(self) -> bool:
        if self.page <= 0:
            return False
        return self.go(self.page - 1)

    def first(self) -> bool:
        return self.go(0)

    def last(self) -> bool:
        return self.go(self.page_count - 1)

    def next_index(self) -> int | None:
        if self.page < self.page_count - 1:
            return self.page + 1
        if self.options.loop and self.page_count > 1:
            return 0
        return None

    # ------------------------------------------------------------ nero/bianco
    def toggle_screen(self, kind: str) -> None:
        self.screen = SCREEN_PAGE if self.screen == kind else kind

    @property
    def blanked(self) -> bool:
        return self.screen != SCREEN_PAGE

    # ------------------------------------------------------------------ tasti
    def key(self, name: str, text: str = "") -> str:
        """Applica un tasto. Restituisce il comando eseguito:
        'next', 'prev', 'jump', 'first', 'last', 'black', 'white', 'unblank',
        'digit', 'exit', 'laser', 'pen', 'erase' o '' se ignorato.

        Con schermo nero/bianco qualsiasi tasto (tranne Esc) torna alla stessa
        pagina senza altri effetti.
        """
        name = name.lower()
        if self.blanked and name != "escape":
            self.screen = SCREEN_PAGE
            self.typed = ""
            return "unblank"
        if name == "escape":
            return "exit"
        if text.isdigit() and len(text) == 1:
            self.typed = (self.typed + text)[-6:]
            return "digit"
        if name in ("return", "enter") and self.typed:
            number = int(self.typed)
            self.typed = ""
            self.go(number - 1)
            return "jump"
        if name == "backspace" and self.typed:
            self.typed = self.typed[:-1]
            return "digit"
        self.typed = ""
        if name in NEXT_KEYS:
            self.next()
            return "next"
        if name in PREV_KEYS:
            self.prev()
            return "prev"
        if name == "home":
            self.first()
            return "first"
        if name == "end":
            self.last()
            return "last"
        if name == "b":
            self.toggle_screen(SCREEN_BLACK)
            return "black"
        if name == "w":
            self.toggle_screen(SCREEN_WHITE)
            return "white"
        if name in ("l", "p", "e"):
            return {"l": "laser", "p": "pen", "e": "erase"}[name]
        return ""

    # ------------------------------------------------------------------ tempi
    def page_duration(self, index: int | None = None) -> float | None:
        """Secondi di permanenza della pagina (None = avanzamento manuale)."""
        index = self.page if index is None else index
        if self.options.use_document_timing:
            d = self.timings[index].duration
            if d:
                return d
        return self.options.interval if self.options.interval and self.options.interval > 0 else None

    def transition_for(self, index: int | None = None) -> tuple[str, float]:
        """Transizione con cui entra la pagina `index`."""
        index = self.page if index is None else index
        t = self.timings[index]
        if self.options.use_document_timing and t.transition != TRANSITION_NONE:
            return t.transition, t.transition_duration
        if self.options.transition == TRANSITION_FADE:
            return TRANSITION_FADE, DEFAULT_FADE
        return TRANSITION_NONE, 0.0

    def remaining(self) -> float | None:
        d = self.page_duration()
        if d is None:
            return None
        return max(0.0, d - self.elapsed_on_page)

    def tick(self, dt: float) -> int:
        """Fa trascorrere `dt` secondi. Restituisce quante pagine sono avanzate."""
        if dt <= 0 or self.paused or self.blanked or self.finished:
            return 0
        advanced = 0
        self.elapsed_on_page += dt
        guard = 0
        while guard < 10_000:
            guard += 1
            d = self.page_duration()
            if d is None or self.elapsed_on_page + 1e-9 < d:
                break
            rest = self.elapsed_on_page - d
            if not self.next():
                self.elapsed_on_page = d
                break
            advanced += 1
            self.elapsed_on_page = rest
        return advanced


def format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
