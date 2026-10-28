"""Controllo ortografico nei campi di testo dei commenti (ANN-44).

Le parole sconosciute sono sottolineate mentre si scrive; il menu contestuale propone le
correzioni, «Aggiungi al dizionario» e «Ignora», la lingua del testo e l'interruttore della
funzione. Il dizionario è il servizio condiviso del modulo di modifica del testo (stessi
elenchi integrati e stesso dizionario personale); senza quel servizio, o per una lingua senza
dizionario, il controllo resta spento e il menu lo indica. Nulla viene scritto nel PDF: le
sottolineature sono formati di presentazione dell'editor, non del documento.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit

from foliant.api import _

SETTING = "annotations/spellcheck"

_service = None


def service():
    """Servizio ortografico condiviso, oppure None se non disponibile."""
    global _service
    if _service is None:
        try:
            from foliant.modules.text_edit.spell import shared_service
            _service = shared_service()
        except Exception:      # modulo o dizionari assenti: funzione non disponibile
            return None
    return _service


def set_service(svc) -> None:
    """Sostituisce il servizio (test, dizionario personale isolato)."""
    global _service
    _service = svc


_settings = None


def use_settings(settings) -> None:
    """Impostazioni dell'applicazione in cui ricordare l'interruttore (registrazione del modulo)."""
    global _settings
    _settings = settings


def _store():
    return _settings if _settings is not None else QSettings()


def enabled() -> bool:
    v = _store().value(SETTING, True)
    return v not in (False, "false", "0", 0)


def set_enabled(on: bool) -> None:
    _store().setValue(SETTING, bool(on))


def default_language() -> str:
    try:
        from foliant import i18n
        return i18n.language() or "it"
    except Exception:
        return "it"


class SpellHighlighter(QSyntaxHighlighter):
    """Sottolinea in rosso ondulato le parole non riconosciute nella lingua scelta."""

    def __init__(self, document, lang: str | None = None):
        super().__init__(document)
        self.lang = lang or default_language()
        self.active = enabled()
        self.fmt = QTextCharFormat()
        self.fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        self.fmt.setUnderlineColor(QColor(220, 40, 40))

    def available(self) -> bool:
        svc = service()
        return svc is not None and svc.has_language(self.lang)

    def misspelled(self, text: str) -> list[tuple[int, int, str]]:
        svc = service()
        if not self.active or svc is None or not svc.has_language(self.lang):
            return []
        return svc.misspelled(text, self.lang)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (nome di Qt)
        for start, end, _w in self.misspelled(text):
            self.setFormat(start, end - start, self.fmt)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.rehighlight()

    def set_active(self, on: bool) -> None:
        self.active = on
        self.rehighlight()


def attach(edit: QTextEdit | QPlainTextEdit, lang: str | None = None) -> SpellHighlighter:
    """Attiva il controllo su un campo di testo (sottolineature e menu contestuale)."""
    hl = SpellHighlighter(edit.document(), lang)
    edit._spell = hl
    edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    edit.customContextMenuRequested.connect(lambda pos, e=edit: _show_menu(e, pos))
    return hl


def word_at(edit, pos) -> tuple[int, int, str] | None:
    """Parola segnalata sotto la posizione (coordinate del viewport), con inizio/fine nel documento."""
    cursor = edit.cursorForPosition(pos)
    block = cursor.block()
    offset = cursor.positionInBlock()
    for start, end, word in edit._spell.misspelled(block.text()):
        if start <= offset <= end:
            return block.position() + start, block.position() + end, word
    return None


def build_menu(edit, pos) -> QMenu:
    """Menu contestuale: correzioni e azioni del controllo ortografico, poi le voci standard."""
    hl: SpellHighlighter = edit._spell
    svc = service()
    menu = QMenu(edit)
    hit = word_at(edit, pos) if hl.active else None
    if hit is not None and svc is not None:
        start, end, word = hit
        sugg = svc.suggestions(word, hl.lang)
        for s in sugg:
            menu.addAction(s, lambda s=s: replace_range(edit, start, end, s))
        if not sugg:
            menu.addAction(_("Nessun suggerimento")).setEnabled(False)
        menu.addSeparator()
        menu.addAction(_("Aggiungi al dizionario"), lambda: (svc.add_to_personal(word), hl.rehighlight()))
        menu.addAction(_("Ignora"), lambda: (svc.ignore_all(word), hl.rehighlight()))
        menu.addSeparator()
    standard = edit.createStandardContextMenu()
    for act in standard.actions():
        act.setParent(menu)
        menu.addAction(act)
    menu.addSeparator()
    toggle = menu.addAction(_("Controllo ortografico"))
    toggle.setCheckable(True)
    toggle.setChecked(hl.active)
    toggle.toggled.connect(lambda on: (set_enabled(on), hl.set_active(on)))
    langs = menu.addMenu(_("Lingua del controllo"))
    if svc is None:
        langs.addAction(_("Dizionari non disponibili")).setEnabled(False)
    else:
        for code in svc.available_languages():
            a = langs.addAction(svc.language_name(code))
            a.setCheckable(True)
            a.setChecked(code == hl.lang)
            a.triggered.connect(lambda _c=False, c=code: hl.set_language(c))
        if not svc.has_language(hl.lang):
            langs.addAction(_("Nessun dizionario per «{l}»: controllo disattivato").format(l=hl.lang)).setEnabled(False)
    return menu


def _show_menu(edit, pos) -> None:
    build_menu(edit, pos).exec(edit.viewport().mapToGlobal(pos))


def replace_range(edit, start: int, end: int, text: str) -> None:
    cursor = QTextCursor(edit.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText(text)
