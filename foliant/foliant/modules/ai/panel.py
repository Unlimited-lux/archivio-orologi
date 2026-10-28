"""Pannello Assistente IA (AI-03): ambito, funzioni, risposta in streaming, azioni sui risultati."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

import pymupdf
from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

from foliant.api import (
    ENGINE_LOCK, FoliantError, OperationCancelled, Tool, _, bind_icon, language, theme,
)

from . import compose, content, engine, render
from .config import Profile
from .dialogs import ConsentDialog, PreviewDialog, TextlessDialog, language_combo
from .engine import AREA, DOCUMENT, NONE, PAGES, SELECTION, ChatMessage, Conversation, Scope, SummaryOptions
from .providers import AUTH, LIMIT, MODEL, QUOTA, AIError, ImagePart
from .worker import Worker

FUNCTIONS = [
    ("chat", "Chiedi", "message-circle"),
    ("summary", "Riassumi", "scroll-text"),
    ("translate", "Traduci", "languages"),
    ("explain", "Spiega", "lightbulb"),
    ("rewrite", "Correggi e riscrivi", "spell-check"),
    ("outline", "Scaletta", "list-tree"),
    ("map", "Mappa concettuale", "network"),
    ("image", "Immagine o area", "image"),
]

SCOPES = [
    (DOCUMENT, "Documento"),
    (PAGES, "Pagine"),
    (SELECTION, "Selezione"),
    (AREA, "Area o immagine"),
    (NONE, "Nessun documento (chat generale)"),
]

REWRITE_ACTIONS = [("correct", "Correggi"), ("improve", "Migliora"), ("tone", "Cambia tono"),
                   ("expand", "Espandi"), ("condense", "Condensa"), ("custom", "Istruzione libera")]
TONES = ["formale", "informale", "professionale", "cordiale", "persuasivo"]
IMAGE_ACTIONS = [("describe", "Descrivi"), ("chart", "Spiega il grafico"), ("ocr", "Estrai testo"),
                 ("translate", "Traduci il testo"), ("", "Domanda libera")]


def _safe_callback(widget, method: str):
    """Callback del tema che ignora i widget già distrutti."""
    import weakref

    import shiboken6
    ref = weakref.ref(widget)

    def call() -> None:
        w = ref()
        if w is not None and shiboken6.isValid(w):
            getattr(w, method)()
    return call


class AreaTool(Tool):
    """Trascina un rettangolo sulla pagina per chiedere all'IA (AI-11)."""

    id = "ai-area"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, callback) -> None:
        self.callback = callback
        self._start = None
        self._rect = None

    def press(self, view, ev):
        if ev.page is None or ev.button != Qt.MouseButton.LeftButton:
            return False
        self._start = (ev.page, ev.point)
        self._rect = None
        return True

    def move(self, view, ev):
        if self._start is None or ev.point is None:
            return False
        page, p0 = self._start
        p1 = view.clamp_to_page(page, ev.pos)
        self._rect = (page, pymupdf.Rect(p0, p1).normalize())
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self._start is None:
            return False
        rect = self._rect
        self._start = None
        self._rect = None
        view.viewport().update()
        if rect is not None:
            self.callback(rect[0], rect[1])
        return True

    def paint(self, view, painter, page):
        if self._rect is None or self._rect[0] != page:
            return
        r = self._rect[1]
        c = theme.color("accent")
        painter.setPen(c)
        fill = theme.color("accent")
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))


class SafeBrowser(QTextBrowser):
    """Visualizzatore che non carica mai risorse esterne."""

    def loadResource(self, rtype, url):  # noqa: N802 - API Qt
        return None


@dataclass
class Outcome:
    """Ultimo risultato, per le azioni (copia, nota, inserimento…)."""

    function: str
    scope: Scope
    message: ChatMessage
    answer: object = None
    document: object = None
    options: dict = field(default_factory=dict)


class AIPanel(QWidget):
    answerFinished = Signal()

    def __init__(self, ctx, services, parent=None, *, standalone: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.services = services
        self.standalone = standalone
        self.view = None
        self.worker: Worker | None = None
        self._convs: dict[int, Conversation] = {}
        self._general = Conversation(function="chat")
        self._spaces: dict[str, render.PageSpace] = {}
        self._stream_msg: ChatMessage | None = None
        self._stream_conv: Conversation | None = None
        self._last: Outcome | None = None
        self._area: tuple[int, pymupdf.Rect] | None = None
        self._external_image: tuple[bytes, int, int] | None = None
        self._doc_changed = False
        self._pending_retry = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._render)
        self.setObjectName("aiPanel")
        self.setAcceptDrops(True)
        self.setMinimumWidth(240)   # l'area proprietà del nucleo è larga ~300 px
        self._build()
        theme.on_change(_safe_callback(self, "_render"))
        self.refresh_state()

    # ================================================================ costruzione
    def _tool_button(self, icon_name: str, tip: str, slot) -> QToolButton:
        b = QToolButton(self)
        bind_icon(b, icon_name)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        return b

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel(_("Assistente IA"))
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_new = self._tool_button("message-square-plus", _("Nuova conversazione"), self.new_conversation)
        self.btn_history = self._tool_button("history", _("Cronologia"), self.show_history)
        self.btn_log = self._tool_button("list", _("Registro degli invii"), self.show_log)
        self.btn_settings = self._tool_button("settings", _("Impostazioni dell'Assistente IA"), self.open_settings)
        for b in (self.btn_new, self.btn_history, self.btn_log, self.btn_settings):
            head.addWidget(b)
        lay.addLayout(head)

        self.stack = QStackedWidget(self)
        lay.addWidget(self.stack, 1)

        # --- non configurato
        off = QWidget()
        ol = QVBoxLayout(off)
        ol.addStretch(1)
        self.off_label = QLabel(_("Configura un fornitore per usare l'assistente.\n"
                                  "Finché non c'è un profilo attivo, Foliant non contatta alcun servizio IA."))
        self.off_label.setWordWrap(True)
        self.off_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ol.addWidget(self.off_label)
        self.configure_btn = QPushButton(_("Configura un fornitore"))
        bind_icon(self.configure_btn, "settings")
        self.configure_btn.clicked.connect(self.open_settings)
        ol.addWidget(self.configure_btn, 0, Qt.AlignmentFlag.AlignCenter)
        ol.addStretch(2)
        self.stack.addWidget(off)

        # --- principale
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(6)
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        ml.addWidget(self.banner)

        row = QHBoxLayout()
        self.function = QComboBox()
        for key, text, icon_name in FUNCTIONS:
            self.function.addItem(_(text), key)
        self.function.currentIndexChanged.connect(self._function_changed)
        self.function.setToolTip(_("Funzione"))
        row.addWidget(self.function, 1)
        self.model_combo = QComboBox()
        self.model_combo.setToolTip(_("Modello"))
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        row.addWidget(self.model_combo, 1)
        ml.addLayout(row)

        row = QHBoxLayout()
        self.scope_combo = QComboBox()
        for key, text in SCOPES:
            self.scope_combo.addItem(_(text), key)
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        self.scope_combo.setToolTip(_("Ambito"))
        row.addWidget(self.scope_combo, 1)
        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText(_("es. 1-3, 7"))
        self.pages_edit.textChanged.connect(self._update_send_line)
        row.addWidget(self.pages_edit, 1)
        ml.addLayout(row)
        self.range_error = QLabel("")
        self.range_error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.range_error.setVisible(False)
        self.range_error.setWordWrap(True)
        ml.addWidget(self.range_error)

        # --- opzioni per funzione
        self.options = QStackedWidget()
        self.options.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._opt_pages: dict[str, int] = {}
        empty = QWidget()
        self._opt_pages["chat"] = self.options.addWidget(empty)
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        self.sum_length = QComboBox()
        for k, t in (("short", "Breve"), ("medium", "Media"), ("detailed", "Dettagliata"),
                     ("custom", "Personalizzata")):
            self.sum_length.addItem(_(t), k)
        self.sum_length.setCurrentIndex(1)
        self.sum_words = QSpinBox()
        self.sum_words.setRange(50, 2000)
        self.sum_words.setValue(300)
        self.sum_words.setSuffix(_(" parole"))
        self.sum_words.setEnabled(False)
        self.sum_length.currentIndexChanged.connect(
            lambda _i: self.sum_words.setEnabled(self.sum_length.currentData() == "custom"))
        self.sum_form = QComboBox()
        for k, t in (("bullets", "Punti chiave"), ("paragraphs", "Paragrafi"), ("both", "Punti + sintesi")):
            self.sum_form.addItem(_(t), k)
        wl.addWidget(self.sum_length, 1)
        wl.addWidget(self.sum_words, 1)
        outer = QWidget()
        ol2 = QVBoxLayout(outer)
        ol2.setContentsMargins(0, 0, 0, 0)
        ol2.setSpacing(4)
        ol2.addWidget(w)
        ol2.addWidget(self.sum_form)
        self._opt_pages["summary"] = self.options.addWidget(outer)
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        self.target_lang = language_combo(self.services.store.option("last_target", "en"))
        self.glossary = QLineEdit()
        self.glossary.setPlaceholderText(_("Glossario: termine=traduzione; termine=- (non tradurre)"))
        wl.addWidget(self.target_lang)
        wl.addWidget(self.glossary, 1)
        self._opt_pages["translate"] = self.options.addWidget(w)
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        self.explain_level = QComboBox()
        for k, t in (("simple", "Semplice"), ("standard", "Standard"), ("deep", "Approfondito")):
            self.explain_level.addItem(_(t), k)
        self.explain_level.setCurrentIndex(1)
        wl.addWidget(QLabel(_("Livello:")))
        wl.addWidget(self.explain_level, 1)
        self._opt_pages["explain"] = self.options.addWidget(w)
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        self.rewrite_action = QComboBox()
        for k, t in REWRITE_ACTIONS:
            self.rewrite_action.addItem(_(t), k)
        self.rewrite_tone = QComboBox()
        for t in TONES:
            self.rewrite_tone.addItem(_(t), t)
        wl.addWidget(self.rewrite_action, 1)
        wl.addWidget(self.rewrite_tone, 1)
        self._opt_pages["rewrite"] = self.options.addWidget(w)
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        self.depth = QSpinBox()
        self.depth.setRange(2, 5)
        self.depth.setValue(3)
        self.depth.setPrefix(_("Livelli: "))
        self.max_nodes = QSpinBox()
        self.max_nodes.setRange(5, 200)
        self.max_nodes.setValue(40)
        self.max_nodes.setPrefix(_("Nodi: "))
        wl.addWidget(self.depth)
        wl.addWidget(self.max_nodes)
        self._opt_pages["outline"] = self._opt_pages["map"] = self.options.addWidget(w)
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        r = QHBoxLayout()
        self.image_action = QComboBox()
        for k, t in IMAGE_ACTIONS:
            self.image_action.addItem(_(t), k)
        self.area_btn = QPushButton(_("Seleziona area"))
        bind_icon(self.area_btn, "square-dashed-mouse-pointer")
        self.area_btn.clicked.connect(self.start_area_tool)
        self.ext_btn = QPushButton(_("Immagine…"))
        bind_icon(self.ext_btn, "image-plus")
        self.ext_btn.clicked.connect(self.choose_external_image)
        wl.addWidget(self.image_action)
        r.addWidget(self.area_btn, 1)
        r.addWidget(self.ext_btn, 1)
        wl.addLayout(r)
        self.thumb = QLabel()
        self.thumb.setVisible(False)
        self.thumb.setMaximumHeight(120)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(self.thumb)
        self._opt_pages["image"] = self.options.addWidget(w)
        ml.addWidget(self.options)

        self.answer_lang = language_combo("", with_auto=True)
        self.answer_lang.setToolTip(_("Lingua della risposta"))
        ml.addWidget(self.answer_lang)
        row = QHBoxLayout()
        self.include_comments = QCheckBox(_("Commenti"))
        self.include_comments.setToolTip(_("Includi i commenti del documento"))
        self.include_forms = QCheckBox(_("Campi modulo"))
        self.include_forms.setToolTip(_("Includi i valori dei campi modulo"))
        row.addWidget(self.include_comments)
        row.addWidget(self.include_forms)
        row.addStretch(1)
        ml.addLayout(row)

        row = QHBoxLayout()
        self.send_line = QLabel("")
        self.send_line.setWordWrap(True)
        self.send_line.setStyleSheet(f"color: {theme.hex_color('text_muted')}; font-size: 11px;")
        row.addWidget(self.send_line, 1)
        self.preview_btn = QToolButton()
        self.preview_btn.setText(_("Anteprima"))
        self.preview_btn.setAutoRaise(True)
        self.preview_btn.clicked.connect(self.show_preview)
        row.addWidget(self.preview_btn)
        ml.addLayout(row)

        self.browser = SafeBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.on_link)
        self.browser.setFrameShape(QFrame.Shape.StyledPanel)
        ml.addWidget(self.browser, 1)

        # --- proposta di riscrittura
        self.proposal_box = QFrame()
        pl = QVBoxLayout(self.proposal_box)
        pl.setContentsMargins(0, 0, 0, 0)
        self.diff_view = QTextBrowser()
        self.diff_view.setMaximumHeight(64)
        pl.addWidget(self.diff_view)
        self.proposal_edit = QPlainTextEdit()
        self.proposal_edit.setMaximumHeight(56)
        self.proposal_edit.setToolTip(_("Puoi ritoccare la proposta prima di accettarla"))
        pl.addWidget(self.proposal_edit)
        pr = QHBoxLayout()
        self.accept_btn = QPushButton(_("Accetta"))
        bind_icon(self.accept_btn, "check")
        self.accept_btn.clicked.connect(self.accept_proposal)
        self.discard_btn = QPushButton(_("Scarta"))
        bind_icon(self.discard_btn, "x")
        self.discard_btn.clicked.connect(self.discard_proposal)
        self.annot_btn = QPushButton(_("Annotazione"))
        self.annot_btn.setToolTip(_("Aggiungi come annotazione di sostituzione"))
        self.annot_btn.clicked.connect(self.annotate_proposal)
        pr.addWidget(self.accept_btn)
        pr.addWidget(self.discard_btn)
        pr.addWidget(self.annot_btn, 1)
        pl.addLayout(pr)
        self.replace_reason = QLabel("")
        self.replace_reason.setWordWrap(True)
        self.replace_reason.setStyleSheet(f"color: {theme.hex_color('text_muted')}; font-size: 11px;")
        pl.addWidget(self.replace_reason)
        self.proposal_box.setVisible(False)
        ml.addWidget(self.proposal_box)

        # --- azioni sul risultato
        self.actions_bar = QHBoxLayout()
        self.act_copy = self._tool_button("copy", _("Copia"), self.copy_result)
        self.act_note = self._tool_button("sticky-note", _("Inserisci come nota"), self.insert_note)
        self.act_box = self._tool_button("type", _("Inserisci come casella di testo"), self.insert_textbox)
        self.act_page = self._tool_button("file-plus", _("Inserisci come nuova pagina all'inizio"), self.insert_page)
        self.act_save = self._tool_button("save", _("Salva come file"), self.save_result)
        self.act_regen = self._tool_button("rotate-ccw", _("Rigenera"), self.regenerate)
        self.act_continue = self._tool_button("chevrons-right", _("Continua"), self.continue_answer)
        self.act_map = self._tool_button("network", _("Apri la mappa"), self.open_mindmap)
        for b in (self.act_copy, self.act_note, self.act_box, self.act_page, self.act_save, self.act_regen,
                  self.act_continue, self.act_map):
            self.actions_bar.addWidget(b)
        self.actions_bar.addStretch(1)
        ml.addLayout(self.actions_bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.hex_color('text_muted')}; font-size: 11px;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        ml.addWidget(self.progress)
        ml.addWidget(self.status)

        row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(_("Scrivi una domanda sul documento…"))
        self.input.setMaximumHeight(70)
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        self.send_btn = QPushButton(_("Invia"))
        bind_icon(self.send_btn, "send")
        self.send_btn.clicked.connect(lambda: self.send())
        self.stop_btn = QPushButton(_("Interrompi"))
        bind_icon(self.stop_btn, "circle-stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setVisible(False)
        col.addWidget(self.send_btn)
        col.addWidget(self.stop_btn)
        row.addLayout(col)
        ml.addLayout(row)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=lambda: self.send())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.stop,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.stack.addWidget(main)
        for combo in main.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
        self._set_actions_enabled(False)
        self._function_changed()

    # ================================================================ stato
    @property
    def store(self):
        return self.services.store

    _key_checked = False

    def profile(self) -> Profile | None:
        # il portachiavi si interroga solo quando il pannello è in uso, non all'avvio
        if self.isVisible() or self._key_checked:
            self._key_checked = True
            return self.store.active()
        return self.store.active(check_key=False)

    def set_view(self, view) -> None:
        if self.standalone:
            return
        if self.view is not None:
            try:
                self.view.selectionChanged.disconnect(self._update_send_line)
            except (RuntimeError, TypeError):
                pass
        self.view = view
        if view is not None:
            view.selectionChanged.connect(self._update_send_line)
        self._area = None
        self.thumb.setVisible(False)
        self._doc_changed = False
        self._offer_resume()
        self.refresh_state()

    def document(self):
        return None if self.view is None else self.view.document

    def conversation(self) -> Conversation:
        doc = self.document()
        if doc is None or self.scope_kind() == NONE:
            return self._general
        conv = self._convs.get(id(doc))
        if conv is None:
            conv = Conversation(path=str(doc.path or ""))
            self._convs[id(doc)] = conv
        return conv

    def forget_document(self, doc) -> None:
        self._convs.pop(id(doc), None)

    def blocked_reason(self) -> str:
        doc = self.document()
        if doc is None:
            return ""
        if not content.extraction_allowed(doc):
            return content.blocked_reason(doc.pdf)
        return ""

    def is_enabled(self) -> bool:
        return self.profile() is not None

    def refresh_state(self) -> None:
        profile = self.profile()
        self.stack.setCurrentIndex(1 if profile else 0)
        for b in (self.btn_new, self.btn_history):
            b.setEnabled(profile is not None)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if profile:
            models = profile.models or [profile.model]
            for m in models:
                self.model_combo.addItem(m, m)
            self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(profile.model)))
        self.model_combo.blockSignals(False)
        has_doc = self.document() is not None
        model = self.scope_combo.model()
        for i, (key, _t) in enumerate(SCOPES):
            item = model.item(i)
            if item is not None:
                item.setEnabled(has_doc or key == NONE)
        if not has_doc and self.scope_kind() != NONE:
            self.set_scope(NONE)
        elif has_doc and self.scope_kind() == NONE and not self.standalone and not self._general.messages:
            self.set_scope(DOCUMENT)
        reason = self.blocked_reason()
        blocked = bool(reason) and self.scope_kind() != NONE
        self._show_banner(reason if reason else "", "danger" if reason else "")
        if not reason and self._doc_changed and self.conversation().messages:
            self._show_banner(_("Il documento è cambiato: il contesto verrà aggiornato al prossimo invio e i "
                                "riferimenti precedenti potrebbero non essere aggiornati."), "warning")
        running = self.worker is not None and self.worker.running
        self.send_btn.setEnabled(profile is not None and not blocked and not running)
        self.send_btn.setVisible(not running)
        self.stop_btn.setVisible(running)
        self.function.setEnabled(not blocked)
        self._update_send_line()
        self._render()

    def _show_banner(self, text: str, kind: str) -> None:
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        if text:
            color = theme.hex_color(kind or "warning")
            self.banner.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 4px; padding: 4px;")

    def on_document_changed(self, view) -> None:
        if view is self.view:
            self._doc_changed = True
            if self._last and self._last.function == "rewrite":
                pass
            self.refresh_state()

    def _model_changed(self) -> None:
        p = self.store.get(self.store.active_id())
        m = self.model_combo.currentData()
        if p and m and m != p.model:
            p.model = m
            self.store.save(p)
            self._update_send_line()

    def function_key(self) -> str:
        return self.function.currentData()

    def set_function(self, key: str) -> None:
        idx = self.function.findData(key)
        if idx >= 0:
            self.function.setCurrentIndex(idx)

    def scope_kind(self) -> str:
        return self.scope_combo.currentData()

    def set_scope(self, kind: str, pages: str | None = None) -> None:
        idx = self.scope_combo.findData(kind)
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        if pages is not None:
            self.pages_edit.setText(pages)

    def _function_changed(self) -> None:
        key = self.function_key()
        self.options.setCurrentIndex(self._opt_pages.get(key, 0))
        self.options.setVisible(key != "chat")
        placeholders = {
            "chat": _("Scrivi una domanda sul documento…"),
            "summary": _("Indicazioni facoltative per il riassunto"),
            "translate": _("Testo da tradurre (facoltativo: altrimenti l'ambito scelto)"),
            "explain": _("Incolla un testo da spiegare (facoltativo: altrimenti la selezione)"),
            "rewrite": _("Istruzione libera (per «Istruzione libera»)"),
            "outline": "", "map": "",
            "image": _("Domanda sull'immagine (facoltativa)"),
        }
        self.input.setPlaceholderText(placeholders.get(key, ""))
        if key in ("explain", "rewrite") and content.view_selections(self.view):
            self.set_scope(SELECTION)
        if key == "image":
            self.set_scope(AREA)
        self._update_send_line()

    def _scope_changed(self) -> None:
        self.pages_edit.setVisible(self.scope_kind() == PAGES)
        self.range_error.setVisible(False)
        self._update_send_line()

    def _set_actions_enabled(self, on: bool) -> None:
        last = self._last
        fn = last.function if last else ""
        for b in (self.act_copy, self.act_save, self.act_regen):
            b.setEnabled(on)
        has_doc = on and last is not None and last.document is not None and not last.document.closed
        self.act_note.setEnabled(has_doc)
        self.act_box.setEnabled(has_doc)
        self.act_page.setEnabled(has_doc and fn == "summary")
        self.act_page.setVisible(fn == "summary")
        self.act_continue.setVisible(on and last is not None and fn == "chat"
                                     and last.message.status in ("incomplete", "interrupted"))
        self.act_map.setVisible(on and fn in ("outline", "map"))
        self.act_map.setEnabled(on and last is not None and last.answer is not None
                                and getattr(last.answer, "data", None) is not None)

    # ================================================================ riga «Verrà inviato»
    def _quick_scope(self) -> Scope:
        kind = self.scope_kind()
        s = Scope(kind=kind)
        doc = self.document()
        if doc is None or kind == NONE:
            return s
        if kind == DOCUMENT:
            s.page_indices = list(range(doc.page_count))
        elif kind == PAGES:
            try:
                s.page_indices = content.parse_page_range(self.pages_edit.text(), doc.page_count)
            except ValueError:
                s.page_indices = []
        elif kind == SELECTION and content.view_selections(self.view):
            s.selection = self.view.selected_text()
        elif kind == AREA and (self._area or self._external_image):
            s.images = [ImagePart(b"")]
        return s

    def _update_send_line(self, *_a) -> None:
        if not hasattr(self, "send_line"):
            return
        profile = self.profile()
        s = self._quick_scope()
        hist = len(self.conversation().history()) if self.function_key() == "chat" else 0
        text = _("Verrà inviato: ") + s.describe(profile, hist)
        self.send_line.setText(text)

    # ================================================================ dialoghi (simulabili nei test)
    def ask_consent(self, profile: Profile) -> bool:
        doc = self.document()
        encrypted = bool(doc and doc.original_protection)
        return ConsentDialog(profile, encrypted, self).exec() == QDialog.DialogCode.Accepted

    def ask_textless(self, pages: list[int], images_ok: bool) -> tuple[str | None, str]:
        langs = content.ocr_languages_available()
        pref = "ita+eng" if {"ita", "eng"} <= set(langs) else ("+".join(langs[:2]) if langs else "")
        choices = [pref] + [l for l in langs if l not in ("osd",)] if langs else []
        dlg = TextlessDialog(pages, images_ok, choices, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None, ""
        return dlg.choice, dlg.language.currentData() or pref, dlg.save_ocr.isChecked()

    def ask_confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def ask_preview(self, summary: str, text: str) -> bool:
        return PreviewDialog(summary, text, self).exec() == QDialog.DialogCode.Accepted

    def ask_open_url(self, url: str) -> bool:
        return QMessageBox.question(self, _("Collegamento esterno"),
                                    _("La risposta contiene questo indirizzo:\n\n{u}\n\nAprirlo nel browser?")
                                    .format(u=url)) == QMessageBox.StandardButton.Yes

    def ask_save_path(self, title: str, suggested: str, file_filter: str, suffix: str) -> str | None:
        return self.ctx.ask_save_path(title, suggested, file_filter, suffix)

    def ask_open_image(self) -> str | None:
        paths = self.ctx.ask_open_paths(_("Scegli un'immagine"),
                                        _("Immagini (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif)"),
                                        multiple=False)
        return paths[0] if paths else None

    def show_error(self, title: str, text: str) -> None:
        self.ctx.error(title, text)

    # ================================================================ invio
    def _answer_language(self) -> str:
        return self.answer_lang.currentData() or language()

    def _glossary(self) -> list[tuple[str, str | None]]:
        out = []
        for part in self.glossary.text().split(";"):
            if "=" in part:
                term, tr = part.split("=", 1)
                term, tr = term.strip(), tr.strip()
                if term:
                    out.append((term, None if tr in ("", "-") else tr))
            elif part.strip():
                out.append((part.strip(), None))
        return out

    def _ensure_ready(self) -> Profile | None:
        self._key_checked = True
        profile = self.profile()
        if profile is None:
            pending = self.store.active(check_key=False)
            if pending is not None:
                self.show_error(_("Assistente IA"), _("La chiave del profilo «{n}» non è disponibile: sblocca il "
                                                     "portachiavi oppure reinseriscila nelle impostazioni.")
                                .format(n=pending.name))
            self.open_settings()
            return None
        if not profile.consent:
            if not self.ask_consent(profile):
                self.status.setText(_("Informativa non accettata: nessun dato è stato inviato."))
                return None
            profile.consent = True
            self.store.save(profile)
        if self.services.log.limit_reached(profile):
            self.show_error(_("Assistente IA"), AIError(LIMIT).user_message())
            return None
        return profile

    def send(self, text: str | None = None):
        """Avvia la funzione scelta. Restituisce il worker o None."""
        if self.worker is not None and self.worker.running:
            return None
        if self.blocked_reason() and self.scope_kind() != NONE:
            self.show_error(_("Assistente IA"), self.blocked_reason())
            return None
        profile = self._ensure_ready()
        if profile is None:
            return None
        fn = self.function_key()
        text = self.input.toPlainText().strip() if text is None else text.strip()
        if fn == "chat" and not text:
            self.status.setText(_("Scrivi una domanda."))
            return None
        try:
            prepared = self._prepare_scope(fn, profile, text)
        except ValueError as e:
            self.range_error.setText(str(e))
            self.range_error.setVisible(True)
            return None
        except FoliantError as e:
            self.show_error(_("Assistente IA"), str(e))
            return None
        if prepared is None:
            return None
        scope, ocr_pages, ocr_lang = prepared
        if fn == "translate":
            from .prompts import LANGUAGE_NAMES_IT
            target = self.target_lang.currentData()
            sample = (scope.selection or text or "\n".join(p.text for p in scope.pages[:3]))[:4000]
            if content.guess_language(sample) == target and not self.ask_confirm(
                    _("Traduci"), _("Il testo sembra già in {l}. Tradurre comunque?").format(
                        l=_(LANGUAGE_NAMES_IT.get(target, target)).lower())):
                return None
        # stima per le operazioni onerose (AI-14)
        if fn in ("summary", "outline", "map") and scope.pages:
            n, chars = engine.plan_requests(scope, profile)
            if engine.needs_confirmation(n, chars) and not self.ask_confirm(
                    _("Operazione onerosa"),
                    _("Verranno inviate circa {n} richieste per {c} caratteri. Procedere?").format(
                        n=n, c=f"{chars:,}".replace(",", "."))):
                return None
        if self.store.option("confirm_each", False):
            hist = len(self.conversation().history()) if fn == "chat" else 0
            if not self.ask_preview(_("Verrà inviato: ") + scope.describe(profile, hist), scope.preview()):
                return None
        return self._launch(fn, scope, text, ocr_pages, ocr_lang)

    def _prepare_scope(self, fn: str, profile: Profile, text: str):
        kind = self.scope_kind()
        doc = self.document()
        if fn == "image":
            return self._prepare_image_scope(profile, text)
        if kind == NONE or doc is None:
            if fn in ("explain", "rewrite", "translate", "summary") and text:
                return Scope(kind=SELECTION, selection=text), [], ""
            if fn not in ("chat",):
                raise FoliantError(_("Questa funzione richiede un documento o un testo."))
            return Scope(kind=NONE), [], ""
        fp = content.content_fingerprint(doc.pdf)
        if kind == SELECTION:
            sel = content.view_selections(self.view)
            if not sel or not "".join(x.text for x in sel).strip():
                if text and fn in ("explain", "translate", "rewrite"):
                    return Scope(kind=SELECTION, selection=text), [], ""
                raise FoliantError(_("Seleziona del testo nel documento."))
            scope = engine.build_scope(doc.pdf, SELECTION, selection=sel, fingerprint=fp)
            if fn == "summary" and len(scope.selection.split()) < 30 and not self.ask_confirm(
                    _("Selezione breve"), _("La selezione contiene meno di 30 parole. Procedere comunque?")):
                return None
            if fn == "explain" and len(scope.selection) > 4000 and not self.ask_confirm(
                    _("Selezione lunga"), _("La selezione è molto lunga: forse è meglio «Riassumi». Procedere?")):
                return None
            return scope, [], ""
        if kind == AREA:
            return self._prepare_image_scope(profile, text)
        pages = list(range(doc.page_count)) if kind == DOCUMENT else content.parse_page_range(
            self.pages_edit.text(), doc.page_count)
        textless = content.textless_pages(doc.pdf, pages)
        exclude: list[int] = []
        ocr_pages: list[int] = []
        ocr_lang = ""
        image_pages: list[int] = []
        self._save_ocr = False
        if textless:
            answer = self.ask_textless(textless, profile.images)
            choice, ocr_lang = answer[0], answer[1]
            self._save_ocr = bool(answer[2]) if len(answer) > 2 else False
            if choice is None:
                return None
            if choice == "exclude":
                exclude = textless
            elif choice == "ocr":
                ocr_pages = textless
            elif choice == "images":
                exclude = textless
                image_pages = textless
        scope = engine.build_scope(
            doc.pdf, kind if kind != DOCUMENT else DOCUMENT, pages=pages, cache=self.services.cache, fingerprint=fp,
            exclude=exclude, include_comments=self.include_comments.isChecked(),
            include_form_values=self.include_forms.isChecked())
        if image_pages:
            for i in image_pages[:5]:
                png, w, h = content.render_area(doc.pdf, i, max_side=1568)
                scope.images.append(ImagePart(png, "image/png", w, h))
            scope.excluded_pages = [p for p in image_pages if p not in image_pages[:5]]
            if len(image_pages) > 5:
                self.status.setText(_("Inviate solo le prime 5 pagine come immagini."))
        for p in ocr_pages:
            scope.page_indices.append(p)
        scope.page_indices.sort()
        return scope, ocr_pages, ocr_lang

    def _prepare_image_scope(self, profile: Profile, text: str):
        doc = self.document()
        action = self.image_action.currentData()
        scope = Scope(kind=AREA)
        if self._external_image is not None:
            data, w, h = self._external_image
            if not profile.images:
                raise FoliantError(_("Il modello del profilo non accetta immagini."))
            scope.images = [ImagePart(data, "image/png", w, h)]
            return scope, [], ""
        if self._area is None or doc is None:
            raise FoliantError(_("Seleziona un'area della pagina con «Seleziona area» o scegli un'immagine."))
        page, rect = self._area
        max_side = int(self.store.option("max_image_side", 2048))
        png, w, h = content.render_area(doc.pdf, page, rect, max_side=max_side)
        if w < 16 or h < 16:
            raise FoliantError(_("L'area è troppo piccola (meno di 16×16 pixel)."))
        words, _e = content.safe_words(doc.pdf, page, rect)
        scope.selection = content._words_text(words)
        scope.page_indices = [page]
        scope.fingerprint = content.content_fingerprint(doc.pdf)
        if not profile.images and not profile.force_capabilities:
            if not self.ask_confirm(_("Immagini non supportate"),
                                    _("Il modello non accetta immagini. Riconoscere il testo dell'area con l'OCR "
                                      "locale e inviare solo il testo?")):
                return None
            ocr = content.ocr_area(doc.pdf, page, rect)
            scope.selection = (ocr or scope.selection).strip()
            return scope, [], ""
        scope.images = [ImagePart(png, "image/png", w, h)]
        return scope, [], ""

    def _launch(self, fn: str, scope: Scope, text: str, ocr_pages: list[int], ocr_lang: str, *,
                regenerate: bool = False, continue_from: str = ""):
        doc = self.document() if scope.kind != NONE else None
        conv = self.conversation() if scope.kind != NONE else self._general
        profile = self.profile()
        if scope.space is not None:
            self._spaces[conv.id] = scope.space
        if not conv.title:
            conv.title = (text or dict((k, _(t)) for k, t, _i in FUNCTIONS).get(fn, fn))[:60]
            conv.function = fn
        conv.fingerprint = scope.fingerprint or conv.fingerprint
        conv.profile, conv.model = profile.name, profile.model
        if doc is not None and doc.path:
            conv.path = str(doc.path)
        label = dict((k, _(t)) for k, t, _i in FUNCTIONS).get(fn, fn)
        if fn == "task":
            label = self._task_args[0].name
        user_text = text if fn == "chat" else (f"{label}: {text}" if text else label)
        if not regenerate:
            conv.messages.append(ChatMessage("user", user_text, function=fn, model=profile.model,
                                             scope=scope.describe()))
        msg = ChatMessage("assistant", continue_from, status="streaming", function=fn, model=profile.model,
                          scope=scope.describe())
        conv.messages.append(msg)
        self._stream_msg, self._stream_conv = msg, conv
        self._last = Outcome(fn, scope, msg, None, doc, {})
        self._last_text = text
        self.input.clear()
        self.proposal_box.setVisible(False)
        self._set_actions_enabled(False)
        options = self._collect_options(fn)
        if fn == "task":
            options["task"] = self._task_args
        self._last.options = options
        pdf = doc.pdf if doc is not None else None
        ocr_words: dict = {} if getattr(self, "_save_ocr", False) else None
        self._last.options["ocr_words"] = ocr_words
        self._last.options["doc_version"] = doc.version if doc is not None else None
        history_conv = Conversation(messages=[m for m in conv.messages[:-2]]) if fn == "chat" else Conversation()

        def work(job):
            if ocr_pages and pdf is not None:
                texts = engine.run_ocr(pdf, ocr_pages, ocr_lang or "ita+eng", job.step, words_out=ocr_words)
                by = {p.index: p for p in scope.pages}
                for i, (t, conf) in texts.items():
                    pt = content.PageText(i, content.page_label(pdf, i), t, [], True, conf)
                    by[i] = pt
                scope.pages = [by[k] for k in sorted(by)]
            if fn == "chat":
                q = text if not continue_from else _("Continua la risposta precedente da dove si è interrotta.")
                return engine.chat(job, history_conv, scope, q)
            if fn == "summary":
                if text and not scope.pages and not scope.selection:
                    scope.selection = text
                return engine.summarize(job, scope, options["summary"])
            if fn == "translate":
                if text and not scope.pages and not scope.selection:
                    scope.selection = text
                return engine.translate_text(job, scope, options["target"], options["glossary"])
            if fn == "explain":
                return engine.explain(job, scope, options["level"], text if scope.kind == NONE else None)
            if fn == "rewrite":
                return engine.rewrite(job, scope, options["action"], options["tone"], text)
            if fn in ("outline", "map"):
                return engine.outline(job, scope, "map" if fn == "map" else "outline", options["depth"],
                                      options["nodes"])
            if fn == "image":
                return engine.ask_image(job, scope, options["image_action"], text)
            if fn == "task":
                from . import tasks_library
                task, values = options["task"]
                return tasks_library.run_task(job, task, values, scope)
            raise FoliantError(_("Funzione non disponibile."))

        lang = options.get("target") if fn == "translate" else self._answer_language()
        worker = Worker(self.services.gateway(), work, function=fn, document=doc, language=lang or "it", parent=self)
        worker.text.connect(self._on_text)
        worker.progress.connect(self._on_progress)
        worker.waiting.connect(self._on_wait)
        worker.finished.connect(lambda ans, w=worker: self._on_finished(w, ans))
        worker.failed.connect(lambda err, w=worker: self._on_failed(w, err))
        self.worker = worker
        self.services.register_worker(worker)
        self.status.setText(_("In attesa della risposta…"))
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        worker.start()
        self.refresh_state()
        return worker

    def _collect_options(self, fn: str) -> dict:
        target = self.target_lang.currentData()
        if fn == "translate":
            self.store.set_option("last_target", target)
        return {
            "summary": SummaryOptions(self.sum_length.currentData(), self.sum_words.value(),
                                      self.sum_form.currentData()),
            "target": target, "glossary": self._glossary(), "level": self.explain_level.currentData(),
            "action": self.rewrite_action.currentData(), "tone": self.rewrite_tone.currentData(),
            "depth": self.depth.value(), "nodes": self.max_nodes.value(),
            "image_action": self.image_action.currentData(),
        }

    # ================================================================ ricezione
    def _on_text(self, chunk: str) -> None:
        if self._stream_msg is not None and self.sender() is self.worker:
            self._stream_msg.text += chunk
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _on_progress(self, fraction: float, text: str) -> None:
        self.progress.setRange(0, 1000)
        self.progress.setValue(int(fraction * 1000))
        self.status.setText(text)

    def _on_wait(self, seconds: float) -> None:
        self.status.setText(_("Il fornitore chiede di attendere: nuovo tentativo tra {s} s…")
                            .format(s=int(seconds + 0.99)))

    def _finish_common(self, worker: Worker) -> None:
        self.services.unregister_worker(worker)
        if worker is self.worker:
            self.worker = None
        self.progress.setVisible(False)
        self._render_timer.stop()

    def _on_finished(self, worker: Worker, answer) -> None:
        msg, conv = self._stream_msg, self._stream_conv
        self._finish_common(worker)
        if msg is None:
            return
        prefix = msg.text if msg.function == "chat" and msg.text and not msg.text.endswith(answer.text) else ""
        msg.text = (prefix + answer.text) if prefix and answer.text and not answer.text.startswith(prefix) else \
            answer.text
        msg.status = answer.status
        msg.refs = list(answer.refs)
        msg.flags = list(answer.flags)
        if answer.status == "interrupted":
            msg.flags.append(_("interrotto"))
        elif answer.status == "incomplete":
            msg.flags.append(_("incompleto"))
        status_text = {"interrupted": _("Operazione interrotta."),
                       "incomplete": _("Risposta incompleta.")}.get(answer.status, _("Risposta completata."))
        if self._last is not None and self._last.message is msg:
            self._last.answer = answer
            words = self._last.options.get("ocr_words")
            doc = self._last.document
            if words and doc is not None and not doc.closed:
                if doc.version != self._last.options.get("doc_version"):
                    status_text = _("Il documento è cambiato durante l'operazione: il testo riconosciuto "
                                    "non è stato salvato.")
                else:
                    try:
                        compose.add_ocr_layer(doc, words)
                        self.ctx.status(_("Testo riconosciuto salvato nel documento."))
                    except Exception as e:  # noqa: BLE001
                        self.show_error(_("Salva il testo riconosciuto"), str(e))
        self.status.setText(status_text)
        self._save_history(conv)
        self._render()
        self._set_actions_enabled(True)
        if self._last and answer.status == "complete" and (
                self._last.function == "rewrite" or (self._last.function == "translate"
                                                     and self._last.scope.selection_page is not None
                                                     and not self._last.scope.pages)):
            self._show_proposal(answer)
        self.refresh_state()
        self.answerFinished.emit()

    def _on_failed(self, worker: Worker, err) -> None:
        msg, conv = self._stream_msg, self._stream_conv
        self._finish_common(worker)
        if msg is not None:
            if isinstance(err, OperationCancelled):
                msg.status = "interrupted"
                msg.flags.append(_("interrotto"))
                self.status.setText(_("Operazione interrotta."))
            elif isinstance(err, AIError):
                if err.partial and not msg.text:
                    msg.text = err.partial
                msg.status = "interrupted" if msg.text else "error"
                msg.flags.append(_("interrotto") if msg.text else _("errore"))
                msg.flags.append(err.user_message())
                self.status.setText(err.user_message())
                self._last_error = err
            else:
                msg.status = "error"
                msg.flags.append(str(err) or type(err).__name__)
                self.status.setText(str(err))
            if not msg.text and msg.status == "error":
                msg.text = ""
            self._save_history(conv)
        self._render()
        self._set_actions_enabled(msg is not None and bool(msg.text))
        self.act_continue.setVisible(msg is not None and msg.status == "interrupted"
                                     and msg.function == "chat")
        self.refresh_state()
        self.answerFinished.emit()

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.status.setText(_("Interruzione…"))

    def _save_history(self, conv: Conversation | None) -> None:
        if conv is None:
            return
        h = self.services.history()
        if h.enabled and conv.messages and not h.save(conv):
            self.status.setText(_("Impossibile salvare la cronologia (spazio su disco?): la conversazione resta "
                                  "in memoria fino alla chiusura."))

    # ================================================================ resa
    def _render(self) -> None:
        if not hasattr(self, "browser"):
            return
        conv = self.conversation()
        space = self._spaces.get(conv.id)
        colors = render._colors()
        parts = []
        if self.view is not None and self._stream_conv is not None and self.worker is not None \
                and self.worker.document is not None and self.worker.document is not self.document():
            parts.append(f"<p style='color:{colors['muted']}'>" +
                         html.escape(_("Risposta in corso su «{d}».").format(
                             d=self.worker.document.display_name)) + "</p>")
        for i, m in enumerate(conv.messages):
            if m.role == "user":
                parts.append(f"<p style='margin-top:10px'><b>{html.escape(_('Tu'))}</b><br>"
                             f"{html.escape(m.text).replace(chr(10), '<br>')}</p>")
                continue
            head = f"<b>{html.escape(_('Assistente'))}</b>"
            if m.model:
                head += f" <span style='color:{colors['muted']}'>· {html.escape(m.model)}</span>"
            known = m.refs if m.status != "streaming" and m.refs else None
            body = render.render_markdown(m.text, space, known_refs=known, prefix=f"{i}:").html if m.text else ""
            if m.status == "streaming" and not m.text:
                body = f"<p style='color:{colors['muted']}'>…</p>"
            flags = ""
            if m.flags:
                color = colors["danger"] if m.status == "error" else colors["warning"]
                flags = f"<p style='color:{color};font-size:small'>" + html.escape(" · ".join(m.flags)) + "</p>"
            if m.status == "error":
                flags += (f"<p><a href='action:settings'>{html.escape(_('Apri le impostazioni del profilo'))}</a> · "
                          f"<a href='action:retry'>{html.escape(_('Riprova'))}</a></p>")
            elif m.status == "interrupted":
                flags += f"<p><a href='action:retry'>{html.escape(_('Riprova'))}</a></p>"
            parts.append(f"<div style='margin-top:6px'>{head}{body}{flags}</div>")
        if not conv.messages:
            parts.append(f"<p style='color:{colors['muted']}'>" + html.escape(
                _("Scegli una funzione e l'ambito, poi premi «Invia». Le risposte citano le pagine con "
                  "collegamenti cliccabili.")) + "</p>")
        bar = self.browser.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.browser.setHtml("".join(parts))
        if at_end or self._stream_msg is not None:
            bar.setValue(bar.maximum())

    # ================================================================ collegamenti
    def message_refs(self, msg_index: int) -> list[dict]:
        conv = self.conversation()
        m = conv.messages[msg_index]
        if m.refs:
            return m.refs
        return [r.to_dict() for r in render.find_refs(m.text, self._spaces.get(conv.id))]

    def on_link(self, url: QUrl) -> None:
        s = url.toString()
        if s.startswith("page:"):
            try:
                msg_i, ref_i = (int(x) for x in s[5:].split(":"))
                ref = self.message_refs(msg_i)[ref_i]
            except (ValueError, IndexError):
                return
            self.go_to_reference(ref)
        elif s.startswith("ext:"):
            try:
                msg_i, link_i = (int(x) for x in s[4:].split(":"))
                conv = self.conversation()
                links = render.render_markdown(conv.messages[msg_i].text).links
                target = links[link_i]
            except (ValueError, IndexError):
                return
            if QUrl(target).scheme().lower() not in ("http", "https", "mailto"):
                self.show_error(_("Collegamento esterno"),
                                _("Indirizzo non consentito: {u}").format(u=target))
                return
            if self.ask_open_url(target):
                QDesktopServices.openUrl(QUrl(target))
        elif s == "action:settings":
            self.open_settings()
        elif s == "action:retry":
            self.retry()

    def go_to_reference(self, ref: dict) -> None:
        view = self.view
        if view is None or ref.get("page") is None:
            return
        page = int(ref["page"])
        if not (0 <= page < view.page_count):
            return
        quote = (ref.get("quote") or "").strip()
        hits = []
        if quote:
            with ENGINE_LOCK:
                hits = view.document.pdf[page].search_for(quote, quads=True)
        view.go_to_page(page)
        if hits:
            view.set_search_hits({page: hits}, (page, 0))
            view.ensure_visible(page, hits[0].rect)
            stamp = (page, len(hits))
            QTimer.singleShot(3000, lambda v=view, st=stamp: self._clear_highlight(v, st))

    @staticmethod
    def _clear_highlight(view, stamp) -> None:
        try:
            hits = view.search_hits.get(stamp[0])
            if hits is not None and len(hits) == stamp[1]:
                view.set_search_hits({}, None)
        except RuntimeError:
            pass

    # ================================================================ azioni sul risultato
    def result_text(self) -> str:
        if self._last is None:
            return ""
        if self._last.function in ("rewrite", "translate") and self.proposal_box.isVisible():
            return self.proposal_edit.toPlainText()
        return self._last.message.text

    def copy_result(self) -> None:
        """Copia come testo semplice e, per chi lo accetta, come testo formattato."""
        raw = self.result_text()
        text = render.plain_text(raw)
        if text:
            from PySide6.QtCore import QMimeData
            mime = QMimeData()
            mime.setText(text)
            mime.setHtml(render.render_markdown(raw).html)
            QGuiApplication.clipboard().setMimeData(mime)
            self.status.setText(_("Copiato negli appunti."))

    # ---------------------------------------------------------------- trascinamento di immagini (AI-11)
    IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")

    def dragEnterEvent(self, ev):  # noqa: N802 - API Qt
        md = ev.mimeData()
        if md.hasImage() or any(u.toLocalFile().lower().endswith(self.IMAGE_SUFFIXES) for u in md.urls()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):  # noqa: N802 - API Qt
        md = ev.mimeData()
        for u in md.urls():
            path = u.toLocalFile()
            if path.lower().endswith(self.IMAGE_SUFFIXES):
                self.choose_external_image(path)
                ev.acceptProposedAction()
                return
        if md.hasImage():
            self.set_image_data(md.imageData())
            ev.acceptProposedAction()

    def set_image_data(self, image) -> None:
        """Immagine incollata o trascinata (QImage)."""
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        try:
            png, w, h, _mt = content.scale_image_bytes(bytes(ba.data()), int(self.store.option("max_image_side", 2048)))
        except FoliantError as e:
            self.show_error(_("Immagine"), str(e))
            return
        self._set_external(png, w, h)

    def _set_external(self, png: bytes, w: int, h: int) -> None:
        self._external_image = (png, w, h)
        self._area = None
        pm = QPixmap()
        pm.loadFromData(png)
        self.thumb.setPixmap(pm.scaled(300, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        self.thumb.setVisible(True)
        self.set_function("image")
        self.set_scope(AREA)

    def _anchor(self) -> tuple[int, tuple[float, float], tuple | None]:
        last = self._last
        if last and last.scope.selection_rects and last.scope.selection_page is not None:
            r = pymupdf.Rect(last.scope.selection_rects[0])
            return last.scope.selection_page, (r.x0, max(r.y0 - 22, 0)), tuple(r)
        if last and last.scope.kind == AREA and self._area:
            r = self._area[1]
            return self._area[0], (r.x1 + 4, r.y0), tuple(r)
        doc = last.document if last else None
        if self.view is not None and doc is not None and self.view.document is doc:
            page = self.view.current_page
        elif last and last.scope.page_indices:
            page = last.scope.page_indices[0]
        else:
            page = 0
        if doc is not None and not doc.closed:
            page = max(0, min(page, doc.page_count - 1))
        return page, (36, 36), None

    def _target_document(self):
        last = self._last
        doc = last.document if last else None
        if doc is None or doc.closed:
            self.show_error(_("Assistente IA"), _("Il documento d'origine non è più aperto."))
            return None
        return doc

    def _author(self) -> str:
        # preferenza «Nome autore» del nucleo (altrimenti l'utente di sistema)
        return self.ctx.author_name() or _("Assistente IA")

    def _safe(self, title: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - un errore non deve chiudere l'app
            self.show_error(title, str(e) or type(e).__name__)

    def insert_note(self) -> None:
        self._safe(_("Inserisci come nota"), self._insert_note)

    def insert_textbox(self) -> None:
        self._safe(_("Inserisci come casella di testo"), self._insert_textbox)

    def insert_page(self) -> None:
        self._safe(_("Inserisci come nuova pagina"), self._insert_page)

    def _insert_note(self) -> None:
        doc = self._target_document()
        if doc is None:
            return
        page, point, _r = self._anchor()
        subject = _("Spiegazione IA") if self._last.function == "explain" else compose.SUBJECT
        compose.add_note(doc, page, point, render.plain_text(self.result_text()), subject=subject,
                         author=self._author())
        self.status.setText(_("Nota inserita."))

    def _insert_textbox(self) -> None:
        doc = self._target_document()
        if doc is None:
            return
        page, point, r = self._anchor()
        rect = r if r and self._last.scope.kind == AREA else (point[0], point[1] + 24, point[0] + 260, point[1] + 140)
        compose.add_text_box(doc, page, rect, render.plain_text(self.result_text()), author=self._author())
        self.status.setText(_("Casella di testo inserita."))

    def _insert_page(self) -> None:
        doc = self._target_document()
        if doc is None:
            return
        like = self.view.current_page if self.view and self.view.document is doc else 0
        compose.insert_text_page(doc, self.result_text(), at=0, like_page=like, title=_("Riassunto"))
        self.status.setText(_("Pagina del riassunto inserita all'inizio."))

    def save_result(self, path: str | None = None) -> None:
        text = self.result_text()
        if not text:
            return
        if path is None:
            path = self.ask_save_path(_("Salva risposta"), "risposta.md",
                                      _("Markdown (*.md);;Testo (*.txt)"), ".md")
        if not path:
            return
        quiz = getattr(self._last.answer, "data", None) if self._last else None
        if isinstance(quiz, list) and quiz and isinstance(quiz[0], dict) and "question" in quiz[0]:
            from . import tasks_library
            text = tasks_library.quiz_markdown(quiz, with_solutions=True)
        data = render.plain_text(text) if path.endswith(".txt") else text
        from foliant.api import atomic_write
        try:
            atomic_write(path, data.encode("utf-8"))
        except FoliantError as e:
            self.show_error(_("Salva risposta"), str(e))
            return
        self.status.setText(_("Salvato in {p}").format(p=path))

    def regenerate(self) -> None:
        last = self._last
        if last is None or (self.worker and self.worker.running):
            return
        conv = self.conversation()
        if conv.messages and conv.messages[-1] is last.message:
            conv.messages.pop()
        text = getattr(self, "_last_text", "") or ""
        self._launch(last.function, last.scope, text, [], "", regenerate=True)

    def continue_answer(self) -> None:
        last = self._last
        if last is None:
            return
        self._launch("chat", last.scope if last.function == "chat" else Scope(), _("Continua"), [], "")

    def retry(self) -> None:
        last = self._last
        if last is None or (self.worker and self.worker.running):
            return
        self.regenerate()

    # ================================================================ attività (AI-20)
    _task_args = None

    def run_template(self, task, values: dict, scope_kind: str | None = None, pages: str = ""):
        """Esegue un modello di attività sull'ambito indicato."""
        if self.worker is not None and self.worker.running:
            return None
        profile = self._ensure_ready()
        if profile is None:
            return None
        if scope_kind:
            self.set_scope(scope_kind, pages or None)
        if self.blocked_reason() and self.scope_kind() != NONE:
            self.show_error(_("Assistente IA"), self.blocked_reason())
            return None
        try:
            if self.scope_kind() == NONE or self.document() is None:
                if task.scope == "document":
                    raise FoliantError(_("Questa attività richiede un documento."))
                prepared = (Scope(kind=NONE), [], "")
            else:
                prepared = self._prepare_scope("summary" if self.scope_kind() != SELECTION else "explain",
                                               profile, "")
        except (ValueError, FoliantError) as e:
            self.show_error(_("Attività"), str(e))
            return None
        if prepared is None:
            return None
        scope, ocr_pages, ocr_lang = prepared
        if self.store.option("confirm_each", False) and not self.ask_preview(
                _("Verrà inviato: ") + scope.describe(profile), scope.preview()):
            return None
        self._task_args = (task, dict(values))
        return self._launch("task", scope, "", ocr_pages, ocr_lang)

    # ================================================================ riscrittura (AI-09)
    def _show_proposal(self, answer) -> None:
        scope = self._last.scope
        colors = render._colors()
        parts = []
        for op, t in engine.word_diff(scope.selection, answer.text):
            t = html.escape(t)
            if op == "=":
                parts.append(t)
            elif op == "-":
                parts.append(f"<span style='color:{colors['danger']};text-decoration:line-through'>{t}</span>")
            else:
                parts.append(f"<span style='color:{theme.hex_color('success')};font-weight:600'>{t}</span>")
        self.diff_view.setHtml(" ".join(parts))
        self.proposal_edit.setPlainText(answer.text)
        doc = self._last.document
        ok, why = (False, _("La proposta riguarda un testo incollato, non il documento."))
        if doc is not None and scope.selection_parts:
            ok, why = False, _("La sostituzione nel documento funziona su una pagina alla volta: "
                               "seleziona il testo di una sola pagina.")
        elif doc is not None and scope.selection_page is not None:
            ok, why = compose.replace_check(doc.pdf, scope.selection_page, scope.selection_rects)
        if answer.flags and _("nessuna modifica proposta") in answer.flags:
            ok, why = False, _("Nessuna modifica proposta.")
        self.accept_btn.setEnabled(ok)
        self.accept_btn.setToolTip(why or _("Sostituisci nel documento"))
        self.annot_btn.setEnabled(doc is not None and scope.selection_page is not None)
        self.replace_reason.setText(why)
        self.replace_reason.setVisible(bool(why))
        self.proposal_box.setVisible(True)

    def proposal_obsolete(self) -> bool:
        last = self._last
        doc = last.document
        if doc is None or doc.closed or last.scope.selection_page is None:
            return True
        current = content.selection_text(doc.pdf, last.scope.selection_page,
                                         [pymupdf.Rect(r) for r in last.scope.selection_rects])
        return current.split() != last.scope.selection.split()

    def accept_proposal(self) -> bool:
        last = self._last
        if last is None or last.function not in ("rewrite", "translate"):
            return False
        doc = last.document
        if self.proposal_obsolete():
            self.replace_reason.setText(_("Il testo nel punto selezionato è cambiato: la proposta è obsoleta."))
            self.accept_btn.setEnabled(False)
            return False
        if compose.is_signed(doc.pdf) and not self.ask_confirm(
                _("Documento firmato"), _("Il documento è firmato digitalmente: la modifica invaliderà le firme. "
                                          "Procedere?")):
            return False
        new_text = self.proposal_edit.toPlainText().strip()
        try:
            warnings = compose.replace_text(doc, last.scope.selection_page, last.scope.selection_rects, new_text)
        except FoliantError as e:
            self.replace_reason.setText(str(e))
            return False
        self.proposal_box.setVisible(False)
        self.status.setText(" ".join(warnings) or _("Testo sostituito."))
        return True

    def discard_proposal(self) -> None:
        self.proposal_box.setVisible(False)
        self.status.setText(_("Proposta scartata."))

    def annotate_proposal(self) -> None:
        last = self._last
        if last is None or last.document is None or last.scope.selection_page is None:
            return
        compose.add_replacement_annotation(last.document, last.scope.selection_page, last.scope.selection_rects,
                                           self.proposal_edit.toPlainText().strip(), author=self._author())
        self.proposal_box.setVisible(False)
        self.status.setText(_("Annotazione di sostituzione aggiunta."))

    # ================================================================ immagini e aree (AI-11)
    def start_area_tool(self) -> None:
        if self.view is None:
            return
        self.ctx.set_tool(AreaTool(self.set_area))
        self.status.setText(_("Trascina un rettangolo sulla pagina."))

    def set_area(self, page: int, rect) -> None:
        self._area = (page, pymupdf.Rect(rect))
        self._external_image = None
        self.set_function("image")
        self.set_scope(AREA)
        doc = self.document()
        if doc is not None:
            try:
                png, w, h = content.render_area(doc.pdf, page, rect, max_side=480)
                pm = QPixmap()
                pm.loadFromData(png)
                self.thumb.setPixmap(pm.scaled(300, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                self.thumb.setVisible(True)
            except FoliantError as e:
                self.status.setText(str(e))
        if self.view is not None:
            self.ctx.set_tool(None)
        self._update_send_line()

    def choose_external_image(self, path: str | None = None) -> None:
        path = path or self.ask_open_image()
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            png, w, h, _mt = content.scale_image_bytes(data, int(self.store.option("max_image_side", 2048)))
        except (OSError, FoliantError) as e:
            self.show_error(_("Immagine"), str(e) if isinstance(e, FoliantError) else
                            _("Impossibile leggere l'immagine."))
            return
        self._set_external(png, w, h)

    # ================================================================ mappe (AI-12)
    def open_mindmap(self) -> None:
        last = self._last
        root = getattr(last.answer, "data", None) if last else None
        if root is None:
            return
        self.services.open_mindmap(root, last.document, self.view)

    # ================================================================ cronologia, impostazioni
    def new_conversation(self) -> None:
        if self.worker is not None and self.worker.running:
            return
        doc = self.document()
        if doc is not None and self.scope_kind() != NONE:
            self._convs[id(doc)] = Conversation(path=str(doc.path or ""))
        else:
            self._general = Conversation()
        self._last = None
        self._stream_msg = None
        self._doc_changed = False
        self.proposal_box.setVisible(False)
        self._set_actions_enabled(False)
        self.status.setText("")
        self.refresh_state()

    def load_conversation(self, conv: Conversation) -> None:
        doc = self.document()
        if doc is not None and conv.fingerprint:
            self._convs[id(doc)] = conv
            if conv.function != "chat" or self.scope_kind() == NONE:
                self.set_scope(DOCUMENT)
            fp = content.content_fingerprint(doc.pdf)
            if fp != conv.fingerprint:
                self._doc_changed = True
            self._spaces[conv.id] = engine.page_space(doc.pdf, [], fp)
        else:
            self._general = conv
        self._last = None
        self.refresh_state()

    def _offer_resume(self) -> None:
        doc = self.document()
        if doc is None or not self.is_enabled() or self._convs.get(id(doc)):
            return
        try:
            fp = content.content_fingerprint(doc.pdf)
        except Exception:  # noqa: BLE001
            return
        convs = self.services.history().for_document(fp, str(doc.path or ""))
        if convs:
            self._resume_candidate = convs[0]
            self.status.setText("")
            self._show_banner(_("C'è una conversazione precedente su questo documento: apri «Cronologia» per "
                                "riprenderla."), "warning")
            self.resume_available = True
        else:
            self.resume_available = False

    resume_available = False

    def resume_last(self) -> bool:
        conv = getattr(self, "_resume_candidate", None)
        if conv is None:
            return False
        self.load_conversation(conv)
        return True

    def show_history(self) -> None:
        self.services.show_history(self)

    def show_log(self) -> None:
        self.services.show_log(self)

    def open_settings(self) -> None:
        self.services.open_settings(self)

    def show_preview(self) -> None:
        profile = self.profile()
        if profile is None:
            return
        try:
            prepared = self._preview_scope()
        except (ValueError, FoliantError) as e:
            self.show_error(_("Anteprima"), str(e))
            return
        PreviewDialog(_("Verrà inviato: ") + prepared.describe(profile), prepared.preview() +
                      "\n\n" + _("Le istruzioni dell'app chiedono di rispondere in base al contenuto e di citare "
                                 "le pagine."), self, confirm=False).exec()

    def _preview_scope(self) -> Scope:
        doc = self.document()
        kind = self.scope_kind()
        if doc is None or kind == NONE:
            return Scope()
        if kind == SELECTION and content.view_selections(self.view):
            return engine.build_scope(doc.pdf, SELECTION, selection=content.view_selections(self.view))
        if kind in (DOCUMENT, PAGES):
            pages = list(range(doc.page_count)) if kind == DOCUMENT else content.parse_page_range(
                self.pages_edit.text(), doc.page_count)
            return engine.build_scope(doc.pdf, kind, pages=pages, cache=self.services.cache,
                                      fingerprint=content.content_fingerprint(doc.pdf),
                                      include_comments=self.include_comments.isChecked(),
                                      include_form_values=self.include_forms.isChecked())
        return Scope(kind=kind)
