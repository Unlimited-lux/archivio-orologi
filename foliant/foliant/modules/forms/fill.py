"""Compilazione dei moduli direttamente sulla pagina.

Un `FormFiller` per ogni vista documento:
- intercetta i clic sulla vista (con lo strumento di selezione o la mano) e
  apre sopra il widget del PDF un editor Qt posizionato con `view.page_to_view`;
- Tab / Maiusc+Tab percorrono i campi secondo l'ordine di tabulazione;
- disegna l'evidenziazione dei campi come livello della vista (mai salvato);
- mostra la barra del modulo in cima alla vista.

Ogni conferma è un passo di annulla/ripeti (`document.change`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import pymupdf
from PySide6.QtCore import QDate, QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QValidator
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QAbstractItemView, QPlainTextEdit, QToolButton, QWidget, QMenu, QWidgetAction,
)

from foliant.api import ENGINE_LOCK, HandTool, OperationCancelled, SelectTextTool, _, bind_icon, theme

from . import engine as E
from . import formats as fm

if TYPE_CHECKING:
    from foliant.api import Context, DocumentView

SETTING_HIGHLIGHT = "forms/highlight"


def tr_count(n: int, one: str, many: str) -> str:
    return (one if n == 1 else many).format(n=n)


# ====================================================================== stato del modulo
class FormState:
    """Informazioni sul modulo del documento, ricalcolate quando cambia la versione."""

    def __init__(self, view: "DocumentView"):
        self.view = view
        self.version = -1
        self.fields: list[E.FieldInfo] = []
        self.by_page: dict[int, list[E.FieldInfo]] = {}
        self.xfa = "none"
        self.blocked: str | None = None
        self.blocked_by_permissions = False   # sbloccabile con la password del proprietario
        self.signature_locked: set[str] = set()   # campi bloccati da una firma apposta (FRM-20)
        self.custom: list[tuple[str, str]] = []
        self.xfa_unmapped: list[str] = []

    def refresh(self, force: bool = False) -> None:
        doc = self.view.document
        if not force and self.version == doc.version:
            return
        with ENGINE_LOCK:
            if doc.closed:
                return
            pdf = doc.pdf
            if pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")[0] == "null":
                # nessun modulo: niente lettura dei campi a ogni modifica (documenti con molti commenti)
                self.fields, self.xfa, self.blocked, self.custom = [], "none", None, []
                self.blocked_by_permissions, self.signature_locked, self.xfa_unmapped = False, set(), []
                self.version = doc.version
                self.by_page = {}
                return
            try:
                self.fields = E.list_fields(pdf)
                self.xfa = E.xfa_status(pdf)
                # permessi effettivi della sessione: dopo «Sblocca…» (document.unlock) tutto è consentito
                self.blocked_by_permissions = not doc.allows(pymupdf.PDF_PERM_FORM)
                if self.blocked_by_permissions:
                    self.blocked = _("I permessi del documento non consentono la compilazione dei moduli.")
                else:
                    self.blocked = E.fill_blocked_reason(pdf, -1)  # resta il controllo della certificazione
                self.custom = E.custom_scripts(pdf, self.fields)
                self.signature_locked = self._signature_locks(pdf)
                self.xfa_unmapped = E.xfa_unmapped(pdf) if self.xfa == "hybrid" else []
            except Exception:  # file danneggiato: nessun modulo utilizzabile
                self.fields, self.xfa, self.blocked, self.custom = [], "none", None, []
        self.version = doc.version
        self._mark_locked(self.fields)
        self.by_page = {}
        for f in self.fields:
            self.by_page.setdefault(f.page, []).append(f)

    def _signature_locks(self, pdf) -> set[str]:
        # solo se c'è una firma apposta: con migliaia di campi la lettura completa costa
        if not any(f.kind == E.SIGNATURE and f.value for f in self.fields):
            return set()
        return E.signature_locked_names(pdf)

    def _mark_locked(self, fields) -> None:
        """I campi bloccati da una firma sono di sola lettura in compilazione."""
        for f in fields:
            if f.name in self.signature_locked and f.kind != E.SIGNATURE:
                f.readonly = True

    def patch(self, names: set[str]) -> None:
        """Rilegge solo i campi con questi nomi (dopo una compilazione): con migliaia di campi
        rileggere tutto a ogni valore sarebbe troppo lento."""
        doc = self.view.document
        from .scan import widget_info
        targets = [f for f in self.fields if f.name in names]
        with ENGINE_LOCK:
            if doc.closed:
                return
            try:
                fresh = [widget_info(doc.pdf, f.page, f.xref) for f in targets]
            except Exception:
                fresh = None
        if fresh is None or any(x is None for x in fresh) or not targets:
            self.refresh(force=True)
            return
        self._mark_locked(fresh)
        by_key = {(f.page, f.xref): f for f in fresh}
        self.fields = [by_key.get((f.page, f.xref), f) for f in self.fields]
        self.by_page = {}
        for f in self.fields:
            self.by_page.setdefault(f.page, []).append(f)
        self.version = doc.version

    @property
    def has_calculations(self) -> bool:
        return any(f.calc or f.formula for f in self.fields)

    @property
    def has_form(self) -> bool:
        return bool(self.fields) or self.xfa != "none"

    def fillable(self) -> list[E.FieldInfo]:
        return [f for f in self.fields if f.kind in E.FILLABLE + (E.BUTTON,) and not f.readonly
                and not f.hidden and f.visibility != "print"]

    def required_empty(self, passwords: dict[str, str]) -> list[E.FieldInfo]:
        out, seen = [], set()
        groups: dict[str, list[E.FieldInfo]] = {}
        for f in self.fields:
            groups.setdefault(f.name, []).append(f)
        for f in self.fields:
            if not f.required or f.kind not in E.FILLABLE or f.name in seen:
                continue
            seen.add(f.name)
            if f.password and passwords.get(f.name):
                continue
            if all(E.export_value(g) in ("", E.OFF, [], None) for g in groups[f.name]):
                out.append(f)
        return out

    def field_at(self, page: int, point: pymupdf.Point) -> E.FieldInfo | None:
        hit = None
        for f in self.by_page.get(page, []):
            if f.hidden or f.visibility == "print":
                continue
            if pymupdf.Rect(f.rect).contains(point):
                hit = f
        return hit

    def find(self, page: int, xref: int) -> E.FieldInfo | None:
        return next((f for f in self.by_page.get(page, []) if f.xref == xref), None)


# ====================================================================== editor
class _TabMixin:
    """Tab e Maiusc+Tab vanno al campo successivo/precedente (Qt li intercetta prima di keyPressEvent)."""

    def event(self, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if self.owner.handle_editor_key(ev):
                return True
        return super().event(ev)


class _FormatValidator(QValidator):
    """Filtro di battitura del formato del campo (es. solo cifre nei numeri)."""

    def __init__(self, fmt: fm.Format, parent=None, fits=None):
        super().__init__(parent)
        self.fmt = fmt
        self.fits = fits  # divieto di scorrimento: funzione che dice se il testo entra nel campo

    def validate(self, text, pos):
        filtered = fm.filter_input(self.fmt, text)
        if filtered != text or (self.fits is not None and not self.fits(text)):
            return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos

    def fixup(self, text):
        return fm.filter_input(self.fmt, text)


class _LineEdit(_TabMixin, QLineEdit):
    def __init__(self, owner: "FormFiller", parent):
        super().__init__(parent)
        self.owner = owner

    def keyPressEvent(self, ev):
        if self.owner.handle_editor_key(ev):
            return
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source):  # pragma: no cover - QLineEdit non lo usa
        super().insertFromMimeData(source)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.owner.editor_focus_out(self)


class _TextEdit(_TabMixin, QPlainTextEdit):
    def __init__(self, owner: "FormFiller", parent):
        super().__init__(parent)
        self.owner = owner
        self.maxlen = 0
        self.setTabChangesFocus(True)
        self.textChanged.connect(self._limit)

    def _limit(self):
        if self.maxlen and len(self.toPlainText()) > self.maxlen:
            cur = self.textCursor()
            self.blockSignals(True)
            self.setPlainText(self.toPlainText()[: self.maxlen])
            self.blockSignals(False)
            cur.movePosition(cur.MoveOperation.End)
            self.setTextCursor(cur)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ev.modifiers():
            super().keyPressEvent(ev)
            return
        if self.owner.handle_editor_key(ev):
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.owner.editor_focus_out(self)


class _KeyCatcher(_TabMixin, QWidget):
    """Riceve la tastiera per caselle, opzioni e pulsanti (nessun testo da modificare)."""

    def __init__(self, owner: "FormFiller", parent):
        super().__init__(parent)
        self.owner = owner
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def keyPressEvent(self, ev):
        if self.owner.handle_editor_key(ev):
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        self.owner.activate_click()

    def paintEvent(self, ev):
        pass


class _Combo(_TabMixin, QComboBox):
    def __init__(self, owner: "FormFiller", parent):
        super().__init__(parent)
        self.owner = owner

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape):
            if self.owner.handle_editor_key(ev):
                return
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.owner.handle_editor_key(ev):
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        if not self.view().isVisible():
            self.owner.editor_focus_out(self)


class _List(_TabMixin, QListWidget):
    def __init__(self, owner: "FormFiller", parent):
        super().__init__(parent)
        self.owner = owner

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.owner.handle_editor_key(ev):
                return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.owner.editor_focus_out(self)


# ====================================================================== controllore
class FormFiller(QObject):
    """Compilazione dei campi su una vista."""

    changed = Signal()            # campi/valori cambiati (per pannelli e barra)
    designRequested = Signal()
    resetRequested = Signal()
    dataRequested = Signal()

    def __init__(self, ctx: "Context", view: "DocumentView", module):
        super().__init__(view)
        self.ctx = ctx
        self.view = view
        self.module = module
        self.state = FormState(view)
        self.passwords: dict[str, str] = {}
        self.active: E.FieldInfo | None = None
        self.editor: QWidget | None = None
        self.original: Any = None
        self._committing = False
        self._closing = False
        self._pressed: tuple[int, int] | None = None   # pulsante premuto in attesa del rilascio
        self._hover: tuple[int, int] | None = None     # pulsante sotto il puntatore
        self.design = False             # modalità modifica modulo: niente compilazione
        self.bar_closed = False
        self.highlight = bool(ctx.settings().value(SETTING_HIGHLIGHT, False, type=bool))
        self._next_required_index = -1
        self._error_label: QLabel | None = None
        self._custom_warned = False
        self.unavailable_action: str | None = None   # pulsante con script cliccato (FRM-18)
        self._pending_patch: set[str] | None = None
        self.banner_text = ""                 # testo dell'avviso del modulo (barra del nucleo, UX-14)
        self.banner_actions: list[str] = []    # etichette delle azioni dell'avviso
        self._banner_shown = False
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        view.overlays.append(self.paint_overlay)
        view.verticalScrollBar().valueChanged.connect(self.reposition)
        view.horizontalScrollBar().valueChanged.connect(self.reposition)
        view.layoutChanged.connect(self.reposition)
        view.contentChanged.connect(self._on_content_changed)
        self.document.add_listener(self._on_document_event)
        self.ctx.documentAccessChanged.connect(self._on_access_changed)
        # FRM-01: la barra compare subito; l'analisi completa (lenta con migliaia di campi)
        # parte appena la vista è mostrata
        with ENGINE_LOCK:
            try:
                quick = bool(self.document.pdf.is_form_pdf) or \
                    self.document.pdf.xref_get_key(self.document.pdf.pdf_catalog(), "AcroForm/XFA")[0] != "null"
            except Exception:
                quick = False
        if quick:
            self._show_banner(_("Analisi del modulo…"), [], "info")
        QTimer.singleShot(0, self, self._initial_analysis)  # legato al filler: annullato se la vista sparisce

    def _alive(self) -> bool:
        """False se vista o barra sono già state distrutte (callback differiti tardivi)."""
        import shiboken6
        try:
            return (self.view is not None and shiboken6.isValid(self) and shiboken6.isValid(self.view)
                    and not self.document.closed)
        except RuntimeError:
            return False

    def _initial_analysis(self) -> None:
        if not self._alive():
            return
        self.state.refresh(force=True)
        self.update_bar()
        self.changed.emit()
        self.repair_on_open()

    def repair_on_open(self) -> None:
        """Aspetti mancanti (/NeedAppearances) o widget non collegati al modulo: si
        generano/ricollegano subito, così il salvataggio li scrive (FRM-01, FRM-09)."""
        doc = self.document
        if doc.closed or self.state.xfa == "dynamic" or not self.state.fields:
            return
        with ENGINE_LOCK:
            needed = E.needs_appearances(doc.pdf) or E.orphan_count(doc.pdf) > 0
            from_xml = E.fields_from_xfa(doc.pdf) if self.state.xfa == "hybrid" else {}
        if from_xml:
            # FRM-34: se dati XML e campi divergono, prevalgono i dati XML
            try:
                with doc.change(_("Allinea i campi ai dati XML"), kind="form") as pdf:
                    E.apply_values(pdf, from_xml)
            except Exception:
                pass
        if not needed:
            return
        try:
            with doc.change(_("Ripara il modulo"), kind="form") as pdf:
                E.prepare_for_edit(pdf)
        except Exception:
            return
        self.ctx.status(_("Modulo riparato: aspetti dei campi generati e campi ricollegati."))

    # ------------------------------------------------------------------ utilità
    @property
    def document(self):
        return self.view.document

    def fill_enabled(self) -> bool:
        if self.design or self.state.blocked:
            return False
        return isinstance(self.view.tool, (SelectTextTool, HandTool))


    def _on_content_changed(self) -> None:
        if self._pending_patch is not None:
            names, self._pending_patch = self._pending_patch, None
            self.state.patch(names)
        else:
            self.state.refresh()
        if self.active is not None and not self._committing:
            again = self.state.find(self.active.page, self.active.xref)
            if again is None:
                self.close_editor(commit=False)
            else:
                self.active = again
                if not self.editor or not self.editor.hasFocus():
                    self._load_editor_value()
        self.update_bar()
        self.changed.emit()

    def _on_document_event(self, _doc, kind: str) -> None:
        """Il salvataggio compatta il file e può rinumerare gli oggetti: si rileggono i campi."""
        if kind != "saved":
            return
        active = self.active
        self.state.refresh(force=True)
        if active is not None:
            again = next((f for f in self.state.by_page.get(active.page, [])
                          if f.name == active.name and f.rect == active.rect), None)
            if again is None:
                self.close_editor(commit=False)
            else:
                self.active = again
        self.module.document_saved(self.view)
        self.update_bar()
        self.changed.emit()

    def _on_access_changed(self, view) -> None:
        """Permessi o sola lettura cambiati dal nucleo (es. «Sblocca…»): il modulo può diventare compilabile."""
        if view is not self.view or not self._alive() or self.document.closed:
            return
        self.state.refresh(force=True)
        self.update_bar()
        self.changed.emit()

    last_warning: str | None = None

    def dispose(self) -> None:
        """Scheda chiusa: scollega tutto e rilascia vista e documento, così che la memoria
        si liberi (NFR-06). Il filler è figlio della vista: senza togliere i riferimenti
        Python alla vista, vista e filler si terrebbero vivi a vicenda."""
        view = self.view
        if view is None:
            return
        self.blockSignals(True)          # niente «changed» che ricrei un filler per la vista chiusa
        try:
            self.document.remove_listener(self._on_document_event)
        except Exception:
            pass
        try:
            self.ctx.documentAccessChanged.disconnect(self._on_access_changed)
        except (RuntimeError, TypeError):
            pass
        self.close_editor(commit=False)
        self._hide_banner()
        try:
            view.overlays.remove(self.paint_overlay)
        except ValueError:
            pass
        for signal, slot in ((view.verticalScrollBar().valueChanged, self.reposition),
                             (view.horizontalScrollBar().valueChanged, self.reposition),
                             (view.layoutChanged, self.reposition),
                             (view.contentChanged, self._on_content_changed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            view.viewport().removeEventFilter(self)
            view.removeEventFilter(self)
        except RuntimeError:
            pass
        self.active = None
        self.state.view = None
        self.state.fields, self.state.by_page = [], {}
        self.view = None
        self.setParent(None)
        self.deleteLater()

    # ------------------------------------------------------------------ barra
    def update_bar(self) -> None:
        """Avviso del modulo nella barra di stato del documento del nucleo (UX-14)."""
        st = self.state
        if st.version < 0:
            return  # analisi iniziale non ancora fatta: resta «Analisi del modulo…»
        st.refresh()
        if not st.has_form or self.bar_closed:
            self._hide_banner()
            return
        n = len({f.name for f in st.fields})
        req = len(st.required_empty(self.passwords))
        level = "info"
        if st.xfa == "dynamic":
            text = _("Modulo XML dinamico: Foliant non può mostrarne il contenuto né compilarlo. "
                     "I dati XML si possono esportare e il file viene salvato senza alterarli.")
            level = "warning"
        elif st.blocked:
            text = _("Modulo non compilabile: {reason}").format(reason=st.blocked)
            level = "warning"
        else:
            parts = [tr_count(n, _("{n} campo"), _("{n} campi"))]
            parts.append(tr_count(req, _("{n} obbligatorio da compilare"), _("{n} obbligatori da compilare"))
                         if req else _("nessun campo obbligatorio da compilare"))
            if st.xfa == "hybrid":
                parts.append(_("modulo XML statico: i dati XML vengono aggiornati"))
                if st.xfa_unmapped:
                    parts.append(tr_count(len(st.xfa_unmapped), _("{n} campo senza dati XML associati"),
                                          _("{n} campi senza dati XML associati")))
            if st.custom:
                names = list(dict.fromkeys(name for name, _ev in st.custom))
                shown = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
                parts.append(_("calcoli personalizzati non eseguiti ({f}): calcoli non aggiornati").format(f=shown))
            if self.unavailable_action:
                parts.append(_("azione del pulsante «{n}» non disponibile: contiene uno script, che Foliant "
                               "non esegue").format(n=self.unavailable_action))
            text = ", ".join(parts)
        fill = st.xfa != "dynamic" and not st.blocked and not self.design
        actions: list[tuple] = []
        if fill:
            actions.append((_("Nascondi evidenziazione") if self.highlight else _("Evidenzia campi"),
                            lambda: self.toggle_highlight()))
            if req:
                actions.append((_("Campo obbligatorio successivo"), self.next_required))
            actions.append((_("Reimposta"), self.reset_requested))
            actions.append((_("Importa/Esporta dati"), self.data_requested))
        if st.xfa != "dynamic" and not self.design and not st.blocked:
            actions.append((_("Modifica modulo"), self.design_requested))
        if st.blocked and st.blocked_by_permissions:
            actions.insert(0, (_("Inserisci password del proprietario"), self.ask_owner_password))
        self._show_banner(text, actions, level)

    def _show_banner(self, text: str, actions: list[tuple], level: str) -> None:
        self.banner_text = text
        self.banner_actions = [a[0] for a in actions]
        try:
            banner = self.ctx.show_banner(self.view, "forms", text, actions, level=level,
                                          priority=30 if level != "info" else 10, on_close=self._on_banner_closed)
        except (RuntimeError, AttributeError):
            banner = None
        self._banner_shown = banner is not None

    def _hide_banner(self) -> None:
        if self._banner_shown:
            try:
                self.ctx.hide_banner(self.view, "forms")
            except RuntimeError:
                pass
        self._banner_shown = False
        self.banner_text = ""
        self.banner_actions = []

    def _on_banner_closed(self) -> None:
        self.bar_closed = True
        self._banner_shown = False

    def bar_visible(self) -> bool:
        """True se l'avviso del modulo è tra quelli visibili della scheda."""
        try:
            return "forms" in self.ctx.banners(self.view)
        except RuntimeError:
            return False

    def close_bar(self) -> None:
        self.bar_closed = True
        self._hide_banner()

    def text_of_bar(self) -> str:
        return self.banner_text

    # ------------------------------------------------------------------ comandi della barra
    def toggle_highlight(self, on: bool | None = None) -> None:
        self.highlight = (not self.highlight) if on is None else bool(on)
        self.ctx.settings().setValue(SETTING_HIGHLIGHT, self.highlight)
        self.module.highlight_changed(self.highlight)
        self.view.viewport().update()

    def set_highlight(self, on: bool) -> None:
        self.highlight = on
        self.update_bar()
        self.view.viewport().update()

    def next_required(self) -> E.FieldInfo | None:
        self.commit_active()
        empty = self.state.required_empty(self.passwords)
        if not empty:
            self.ctx.status(_("Nessun campo obbligatorio da compilare."))
            return None
        stops = [f for f in self.navigation_stops() if f.name in {e.name for e in empty}]
        if not stops:
            stops = empty
        names = []
        unique = []
        for f in stops:
            if f.name not in names:
                names.append(f.name)
                unique.append(f)
        self._next_required_index = (self._next_required_index + 1) % len(unique)
        target = unique[self._next_required_index]
        self.activate(target)
        return target

    def ask_owner_password(self) -> None:
        from .dialogs import ask_password
        pw = ask_password(self.view, _("Password del proprietario"),
                          _("Inserisci la password del proprietario per consentire la compilazione:"))
        if pw is not None:
            self.unlock_with_owner_password(pw)

    def unlock_with_owner_password(self, password: str) -> bool:
        """Sblocca con la password del proprietario tramite il nucleo (document.unlock): l'accesso
        completo vale per tutta l'applicazione e ctx.documentAccessChanged aggiorna il modulo."""
        if not self.document.unlock(password):
            self.ctx.error(_("Password del proprietario"), _("Password errata."))
            return False
        self.ctx.status(_("Compilazione consentita."))
        return True

    def reset_requested(self) -> None:
        self.resetRequested.emit()

    def data_requested(self) -> None:
        self.dataRequested.emit()

    def design_requested(self) -> None:
        self.designRequested.emit()

    # ------------------------------------------------------------------ eventi della vista
    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if obj is self.view:
            if t == QEvent.Type.Resize:
                QTimer.singleShot(0, self, self.reposition)
            return False
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            if not self.fill_enabled():
                return False
            hit = self.view.view_to_page(ev.position())
            if hit is None:
                self.commit_active()
                return False
            self.state.refresh()
            f = self.state.field_at(hit[0], hit[1])
            if f is None:
                self.commit_active()
                return False
            if f.kind not in E.FILLABLE + (E.BUTTON, E.SIGNATURE):
                return False
            self.click_field(f, release_later=True)
            return True
        if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton and self._pressed:
            pressed, self._pressed = self._pressed, None
            hit = self.view.view_to_page(ev.position())
            f = self.state.field_at(hit[0], hit[1]) if hit else None
            if f is not None and (f.page, f.xref) == pressed and self._alive():
                self.module.run_button(self, f, "U")   # rilascio sopra lo stesso pulsante
            return True
        if t == QEvent.Type.MouseMove and not ev.buttons():
            if self.fill_enabled():
                hit = self.view.view_to_page(ev.position())
                f = self.state.field_at(hit[0], hit[1]) if hit else None
                self._hover_button(f if f is not None and f.kind == E.BUTTON and not f.readonly else None)
                if f is not None and f.kind in E.FILLABLE + (E.BUTTON,) and not f.readonly:
                    cur = Qt.CursorShape.IBeamCursor if f.kind == E.TEXT else Qt.CursorShape.PointingHandCursor
                    self.view.viewport().setCursor(cur)
                    if f.tooltip:
                        self.view.viewport().setToolTip(f.tooltip)
                    return False
                self.view.viewport().setToolTip("")
                self.view.viewport().setCursor(self.view.tool.cursor)
            return False
        if t == QEvent.Type.KeyPress and obj is self.view.viewport():
            return False
        return False

    def click_field(self, f: E.FieldInfo, release_later: bool = False) -> None:
        """Clic su un campo. Sui pulsanti è pressione e rilascio; con `release_later` (mouse)
        il rilascio arriva dopo, ed esegue /U solo se avviene sopra lo stesso pulsante."""
        if f.kind == E.SIGNATURE:
            self.commit_active()
            self.module.signature_clicked(self.view, f)
            return
        if f.readonly:
            self.commit_active()
            if f.name in self.state.signature_locked:
                self.ctx.status(_("Il campo «{n}» è bloccato da una firma.").format(n=f.name))
            else:
                self.ctx.status(_("Il campo «{n}» è in sola lettura.").format(n=f.name))
            return
        if f.kind in (E.CHECKBOX, E.RADIO):
            self.activate(f)
            self.toggle_active()
            return
        if f.kind == E.BUTTON:
            self.activate(f)
            if f.image:   # campo immagine: il clic sceglie subito l'immagine (FRM-19)
                self.module.run_button(self, f, "U")
                return
            self.module.run_button(self, f, "D")
            if release_later:
                self._pressed = (f.page, f.xref)   # il rilascio sopra lo stesso pulsante esegue /U
            else:
                self.module.run_button(self, f, "U")
            return
        self.activate(f)

    # ------------------------------------------------------------------ attivazione
    def activate(self, f: E.FieldInfo) -> None:
        """Porta il focus sul campo `f` e ne apre l'editor."""
        if self.active is not None and (self.active.page, self.active.xref) == (f.page, f.xref) and self.editor:
            self.editor.setFocus()
            return
        previous = self.active
        if not self.commit_active():
            return
        if previous is not None and previous.kind == E.BUTTON and (previous.page, previous.xref) != (f.page, f.xref):
            self.module.run_button(self, previous, "Bl")
        self.active = f
        if f.kind == E.BUTTON:
            self.module.run_button(self, f, "Fo")
        self.view.ensure_visible(f.page, f.rect)
        self._create_editor(f)
        self.reposition()
        self.view.viewport().update()
        self.changed.emit()

    def _create_editor(self, f: E.FieldInfo) -> None:
        vp = self.view.viewport()
        ed: QWidget
        if f.kind == E.TEXT and (f.multiline and not f.comb):
            ed = _TextEdit(self, vp)
            ed.maxlen = f.maxlen
            ed.setFrameShape(QFrame.Shape.NoFrame)
        elif f.kind == E.TEXT:
            ed = _LineEdit(self, vp)
            ed.setFrame(False)
            if f.maxlen:
                ed.setMaxLength(f.maxlen)
            if f.password:
                ed.setEchoMode(QLineEdit.EchoMode.Password)
            fits = None
            if f.do_not_scroll and not f.comb:
                # FRM-02: con il divieto di scorrimento si rifiutano i caratteri che non entrano
                def fits(text, ed=ed):
                    from PySide6.QtGui import QFontMetricsF
                    shown = "•" * len(text) if ed.echoMode() == QLineEdit.EchoMode.Password else text
                    return QFontMetricsF(ed.font()).horizontalAdvance(shown) <= max(4.0, ed.width() - 6)
            if f.fmt.type != fm.NONE or fits is not None:
                ed.setValidator(_FormatValidator(f.fmt, ed, fits))
            ed.setAlignment({E.ALIGN_CENTER: Qt.AlignmentFlag.AlignHCenter,
                             E.ALIGN_RIGHT: Qt.AlignmentFlag.AlignRight}.get(f.align, Qt.AlignmentFlag.AlignLeft)
                            | Qt.AlignmentFlag.AlignVCenter)
            if f.fmt.type in (fm.DATE,):
                act = ed.addAction(self.module.icon("calendar"), QLineEdit.ActionPosition.TrailingPosition)
                act.setToolTip(_("Scegli una data"))
                act.triggered.connect(self.open_calendar)
            if f.file_select:
                act = ed.addAction(self.module.icon("folder-open"), QLineEdit.ActionPosition.TrailingPosition)
                act.setToolTip(_("Sfoglia…"))
                act.triggered.connect(self.browse_file)
        elif f.kind == E.COMBO:
            ed = _Combo(self, vp)
            ed.setEditable(f.editable)
            for e_, d in zip(f.exports, f.options):
                ed.addItem(d, e_)
            if f.editable:
                comp = QCompleter([*f.options], ed)
                comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                ed.setCompleter(comp)
            ed.activated.connect(lambda _i: self._combo_activated())
        elif f.kind == E.LIST:
            ed = _List(self, vp)
            ed.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if f.multiselect
                                else QAbstractItemView.SelectionMode.SingleSelection)
            for d in f.options:
                ed.addItem(d)
            if f.commit_on_change:
                ed.itemSelectionChanged.connect(lambda: self.commit_active(keep=True))
        else:
            ed = _KeyCatcher(self, vp)
        ed.setObjectName("formsEditor")
        self.editor = ed
        self._style_editor(f, ed)
        self._load_editor_value()
        self.original = self._editor_value()
        ed.show()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)

    def _style_editor(self, f: E.FieldInfo, ed: QWidget) -> None:
        if isinstance(ed, _KeyCatcher):
            return
        scale = self._scale(f)
        size = f.fontsize or max(4.0, min(12.0, f.rect.height * 0.7)) if not f.multiline else (f.fontsize or 10)
        font = QFont({"Times": "Serif", "Courier": "Monospace"}.get(f.font, "Sans Serif"))
        font.setPixelSize(max(6, int(round(size * scale))))
        ed.setFont(font)
        r, g, b = (list(f.text_color) + [0, 0, 0])[:3]
        bg = f.fill_color or (1, 1, 1)
        ed.setStyleSheet(
            f"#formsEditor {{ color: rgb({int(r * 255)},{int(g * 255)},{int(b * 255)}); "
            f"background: rgb({int(bg[0] * 255)},{int(bg[1] * 255)},{int(bg[2] * 255)}); "
            f"border: 2px solid {theme.hex_color('accent')}; padding: 0px; }}")

    def _scale(self, f: E.FieldInfo) -> float:
        r = self.view.page_to_view(f.page, f.rect)
        return r.height() / max(1.0, f.rect.height) if f.rect.height else 1.0

    def reposition(self, *_args) -> None:
        if not self._alive():
            return
        if self.active is None or self.editor is None:
            return
        try:
            r = self.view.page_to_view(self.active.page, self.active.rect)
        except (IndexError, RuntimeError):
            return
        if isinstance(self.editor, _List):
            r = r.adjusted(0, 0, 0, max(0.0, 60 - r.height()))
        self.editor.setGeometry(r.toAlignedRect())

    def _load_editor_value(self) -> None:
        f, ed = self.active, self.editor
        if f is None or ed is None:
            return
        if isinstance(ed, _LineEdit):
            value = self.passwords.get(f.name, "") if f.password else (f.value or "")
            if f.fmt.type != fm.NONE and value:
                shown = fm.display(f.fmt, value)[0] if f.fmt.type in (fm.DATE, fm.TIME, fm.SPECIAL) \
                    else fm.edit_text(f.fmt, value)
                value = shown if f.fmt.type != fm.SPECIAL else value
            ed.setText(value)
        elif isinstance(ed, _TextEdit):
            ed.setPlainText((f.value or "").replace("\r", "\n"))
        elif isinstance(ed, _Combo):
            idx = ed.findData(f.value)
            if idx >= 0:
                ed.setCurrentIndex(idx)
            elif f.editable:
                ed.setEditText(f.value or "")
            else:
                ed.setCurrentIndex(-1)
                if f.value:
                    ed.setPlaceholderText(f.value)
        elif isinstance(ed, _List):
            values = f.value if isinstance(f.value, list) else ([f.value] if f.value else [])
            ed.blockSignals(True)
            for i, e_ in enumerate(f.exports):
                ed.item(i).setSelected(e_ in values)
            ed.blockSignals(False)

    def _editor_value(self) -> Any:
        f, ed = self.active, self.editor
        if isinstance(ed, _LineEdit):
            return ed.text()
        if isinstance(ed, _TextEdit):
            return ed.toPlainText()
        if isinstance(ed, _Combo):
            text = ed.currentText()
            idx = ed.findText(text)
            if idx >= 0:
                return ed.itemData(idx)
            return text if f and f.editable else (f.value if f else "")
        if isinstance(ed, _List):
            sel = sorted(ed.row(i) for i in ed.selectedItems())
            vals = [f.exports[i] for i in sel]
            return vals if f.multiselect else (vals[0] if vals else "")
        return None

    # ------------------------------------------------------------------ conferma
    def commit_active(self, keep: bool = False) -> bool:
        """Conferma il valore dell'editor. False se il valore non è valido (il focus resta)."""
        f, ed = self.active, self.editor
        if f is None or ed is None or self._committing:
            return True
        value = self._editor_value()
        ok = True
        if f.kind in (E.TEXT, E.COMBO, E.LIST) and value != self.original:
            ok = self.apply_value(f, value)
        if ok and not keep:
            self.close_editor(commit=False)
        elif ok:
            self.original = self._editor_value()
        return ok

    def apply_value(self, f: E.FieldInfo, value: Any) -> bool:
        """Valida, memorizza e ricalcola. Restituisce False (con messaggio) se non valido."""
        if f.kind == E.TEXT:
            try:
                stored = E.normalize_input(f, value)
            except E.FormError as e:
                self.show_error(f, str(e))
                self._restore_original()
                return False
            err = E.validate(f, stored)
            if err and not (not str(stored).strip() and f.required):
                self.show_error(f, err)
                self._restore_original()
                return False
            if f.password:
                self.passwords[f.name] = stored
                self.update_bar()
                return True
            value = stored
        self._committing = True
        try:
            calcs = self.state.has_calculations
            with self.document.change(_("Compila «{n}»").format(n=f.name), kind="form") as pdf:
                E.set_value(pdf, f.page, f.xref, value)
                changed = {f.name} | set(E.recalculate(pdf) if calcs else [])
                if self.state.xfa == "hybrid":
                    E.sync_xfa_from_fields(pdf)
                self._pending_patch = changed
        except E.FormError as e:
            self.show_error(f, str(e))
            return False
        except OperationCancelled as e:  # una guardia del nucleo ha rifiutato la modifica
            self.show_error(f, str(e) or _("Modifica non consentita."))
            self._restore_original()
            return False
        except Exception as e:  # mai chiudere l'applicazione per un campo
            self.show_error(f, _("Impossibile memorizzare il valore: {e}").format(e=e))
            return False
        finally:
            self._committing = False
            self._pending_patch = None
        self.state.refresh()
        if self.active is not None:
            self.active = self.state.find(self.active.page, self.active.xref) or self.active
        self.update_bar()
        self.changed.emit()
        return True

    def _restore_original(self) -> None:
        ed = self.editor
        if isinstance(ed, _LineEdit):
            ed.setText(self.original or "")
            ed.selectAll()
        elif isinstance(ed, _TextEdit):
            ed.setPlainText(self.original or "")

    def cancel_active(self) -> None:
        self._restore_original()
        self.close_editor(commit=False)

    def close_editor(self, commit: bool = True) -> None:
        if commit and not self.commit_active():
            return
        ed, self.editor = self.editor, None
        self.active = None
        if ed is not None:
            self._closing = True
            try:
                ed.hide()
                ed.deleteLater()
            finally:
                self._closing = False
            self.view.setFocus()
        self.view.viewport().update()
        self.changed.emit()

    def editor_focus_out(self, ed: QWidget) -> None:
        if self._closing or self._committing or ed is not self.editor:
            return
        # il focus è andato altrove (clic fuori dal campo, altra finestra): conferma
        QTimer.singleShot(0, self, lambda: self._focus_out_commit(ed))

    def _focus_out_commit(self, ed) -> None:
        if ed is not self.editor or self.editor is None:
            return
        from PySide6.QtWidgets import QApplication
        w = QApplication.focusWidget()
        if w is not None and (w is ed or ed.isAncestorOf(w) or isinstance(w, QCalendarWidget)):
            return
        if isinstance(w, QWidget) and w.window() is not ed.window():
            return  # popup del selettore o altra finestra
        self.commit_active()

    def show_error(self, f: E.FieldInfo, message: str) -> None:
        """Messaggio non modale vicino al campo (e nella barra di stato)."""
        self.ctx.status(message, 8000)
        if self._error_label is None:
            self._error_label = QLabel(self.view.viewport())
            self._error_label.setObjectName("formsError")
            self._error_label.setWordWrap(True)
        lab = self._error_label
        lab.setStyleSheet(f"#formsError {{ background: {theme.hex_color('danger')}; color: white; "
                          "padding: 4px 8px; border-radius: 4px; }")
        lab.setText(message)
        lab.adjustSize()
        r = self.view.page_to_view(f.page, f.rect)
        lab.move(int(r.left()), int(r.bottom()) + 4)
        lab.show()
        lab.raise_()
        self.last_error = message
        QTimer.singleShot(5000, lab, lab.hide)

    last_error: str | None = None

    # ------------------------------------------------------------------ tastiera
    def handle_editor_key(self, ev) -> bool:
        key = ev.key()
        mods = ev.modifiers()
        f = self.active
        if f is None:
            return False
        if key == Qt.Key.Key_Escape:
            self.cancel_active()
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            back = key == Qt.Key.Key_Backtab or bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.focus_next(back)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if f.kind == E.BUTTON:
                self.press_button(f)
                return True
            if f.kind in (E.TEXT, E.COMBO, E.LIST):
                self.commit_active(keep=True)
                return True
        if f.kind in (E.CHECKBOX, E.RADIO, E.BUTTON) and key == Qt.Key.Key_Space:
            if f.kind == E.BUTTON:
                self.press_button(f)
            else:
                self.toggle_active()
            return True
        if f.kind == E.RADIO and key in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Left):
            self.move_in_group(1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Right) else -1)
            return True
        if (f.kind == E.TEXT and f.fmt.type == fm.DATE and key == Qt.Key.Key_Down
                and mods & Qt.KeyboardModifier.AltModifier):
            self.open_calendar()
            return True
        return False

    def navigation_stops(self) -> list[E.FieldInfo]:
        """Tappe di Tab nell'ordine del documento (salta nascosti e sola lettura)."""
        stops: list[E.FieldInfo] = []
        with ENGINE_LOCK:
            pdf = self.document.pdf
            pages = sorted(self.state.by_page)
            for p in pages:
                for f in E.navigation_order(pdf, p):
                    if f.kind in E.FILLABLE + (E.BUTTON,) and not f.readonly and not f.hidden \
                            and f.visibility != "print":
                        stops.append(f)
        return stops

    def focus_next(self, back: bool = False) -> E.FieldInfo | None:
        current = self.active
        if not self.commit_active():
            return None
        stops = self.navigation_stops()
        if not stops:
            return None
        idx = -1
        if current is not None:
            idx = next((i for i, s in enumerate(stops) if (s.page, s.xref) == (current.page, current.xref)), -1)
        nxt = stops[(idx + (-1 if back else 1)) % len(stops)] if idx >= 0 else stops[-1 if back else 0]
        self.activate(nxt)
        return nxt

    def toggle_active(self) -> None:
        f = self.active
        if f is None or f.readonly:
            return
        try:
            calcs = self.state.has_calculations
            with self.document.change(_("Compila «{n}»").format(n=f.name), kind="form") as pdf:
                E.toggle(pdf, f.page, f.xref)
                changed = {f.name} | set(E.recalculate(pdf) if calcs else [])
                if self.state.xfa == "hybrid":
                    E.sync_xfa_from_fields(pdf)
                self._pending_patch = changed
        except (E.FormError, OperationCancelled) as e:
            self._pending_patch = None
            self.show_error(f, str(e) or _("Modifica non consentita."))
            return
        self._pending_patch = None
        self.state.refresh()
        self.active = self.state.find(f.page, f.xref) or f
        self.update_bar()
        self.view.viewport().update()
        self.changed.emit()

    def move_in_group(self, step: int) -> None:
        f = self.active
        with ENGINE_LOCK:
            sib = E.radio_siblings(self.document.pdf, f.page, f.xref)
        if len(sib) < 2:
            return
        i = sib.index((f.page, f.xref)) if (f.page, f.xref) in sib else 0
        page, xref = sib[(i + step) % len(sib)]
        target = self.state.find(page, xref)
        if target is None:
            return
        self.activate(target)
        if target.value != target.export:
            self.toggle_active()

    def activate_click(self) -> None:
        if self.active and self.active.kind in (E.CHECKBOX, E.RADIO):
            self.toggle_active()
        elif self.active and self.active.kind == E.BUTTON:
            self.press_button(self.active)

    def _combo_activated(self) -> None:
        f = self.active
        if f is not None and f.commit_on_change:
            self.commit_active(keep=True)

    # ------------------------------------------------------------------ pulsanti
    def press_button(self, f: E.FieldInfo) -> None:
        """Pressione da tastiera (spazio o Invio): pressione e rilascio."""
        self.module.run_button(self, f, "D")
        self.module.run_button(self, f, "U")

    def _hover_button(self, f: E.FieldInfo | None) -> None:
        """Ingresso e uscita del puntatore dai pulsanti (azioni /E e /X)."""
        key = (f.page, f.xref) if f is not None else None
        if key == self._hover:
            return
        old = self.state.find(*self._hover) if self._hover else None
        self._hover = key
        if old is not None:
            self.module.run_button(self, old, "X")
        if f is not None:
            self.module.run_button(self, f, "E")

    # ------------------------------------------------------------------ calendario e file
    def browse_file(self) -> None:
        """Campo di selezione file: si memorizza il percorso scelto, non il contenuto."""
        ed = self.editor
        if not isinstance(ed, QLineEdit):
            return
        paths = self.ctx.ask_open_paths(_("Scegli un file"), _("Tutti i file") + " (*)", multiple=False)
        if paths:
            ed.setText(paths[0])
            self.commit_active(keep=True)

    def open_calendar(self) -> None:
        f, ed = self.active, self.editor
        if f is None or not isinstance(ed, QLineEdit):
            return
        if not fm.pattern_has_day(f.fmt.pattern):
            self.open_month_picker()
            return
        cal = QCalendarWidget()
        d = fm.parse_date(ed.text(), f.fmt.pattern)
        if d:
            cal.setSelectedDate(QDate(d.year, d.month, d.day))
        menu = QMenu(ed)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cal)
        menu.addAction(wa)

        def chosen(qd: QDate):
            from datetime import date
            ed.setText(fm.format_date(date(qd.year(), qd.month(), qd.day()), f.fmt.pattern))
            menu.close()
            self.commit_active(keep=True)

        cal.clicked.connect(chosen)
        cal.activated.connect(chosen)
        self.calendar = cal
        self.module.exec_popup(menu, ed.mapToGlobal(ed.rect().bottomLeft()))

    def open_month_picker(self) -> None:
        """Modelli senza giorno (es. mm/aaaa): si scelgono solo mese e anno."""
        from datetime import date

        from PySide6.QtWidgets import QSpinBox
        f, ed = self.active, self.editor
        d = fm.parse_date(ed.text(), f.fmt.pattern) or date.today()
        menu = QMenu(ed)
        box = QWidget(menu)
        lay = QHBoxLayout(box)
        month = QComboBox(box)
        for i in range(12):
            month.addItem(fm.format_date(date(2000, i + 1, 1), "mmmm"))
        month.setCurrentIndex(d.month - 1)
        year = QSpinBox(box)
        year.setRange(1900, 2200)
        year.setValue(d.year)
        ok = QToolButton(box)
        ok.setText(_("OK"))
        lay.addWidget(month)
        lay.addWidget(year)
        lay.addWidget(ok)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(box)
        menu.addAction(wa)

        def chosen():
            ed.setText(fm.format_date(date(year.value(), month.currentIndex() + 1, 1), f.fmt.pattern))
            menu.close()
            self.commit_active(keep=True)

        ok.clicked.connect(chosen)
        self.month_picker = (month, year, chosen)
        self.calendar = None
        self.module.exec_popup(menu, ed.mapToGlobal(ed.rect().bottomLeft()))

    # ------------------------------------------------------------------ disegno
    def paint_overlay(self, view, p: QPainter, page: int) -> None:
        self.state.refresh()
        fields = self.state.by_page.get(page, [])
        if not fields:
            return
        if self.design:
            self.module.paint_design(view, p, page, fields)
            return
        if self.highlight and not self.state.blocked:
            hl = QColor(theme.color("accent"))
            hl.setAlpha(46)
            req = QColor(theme.color("danger"))
            for f in fields:
                if f.kind not in E.FILLABLE + (E.BUTTON,) or f.readonly or f.hidden or f.visibility == "print":
                    continue
                r = QRectF(f.rect.x0, f.rect.y0, f.rect.width, f.rect.height)
                p.fillRect(r, hl)
                if f.required:
                    p.setPen(QPen(req, 1.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(r)
        a = self.active
        if a is not None and a.page == page and a.kind in (E.CHECKBOX, E.RADIO, E.BUTTON):
            pen = QPen(theme.color("accent"), 1.6)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(a.rect.x0 - 1.5, a.rect.y0 - 1.5, a.rect.width + 3, a.rect.height + 3))


def bind_pixmap(name: str):
    from foliant.api import icon
    return icon(name).pixmap(18, 18)
