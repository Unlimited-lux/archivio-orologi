"""Finestre di dialogo del modulo Proteggi (nessuna logica sul documento)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import _, icon, theme

from . import protection as P
from .patterns import Match


def _note(text: str, kind: str = "text_muted") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color(kind)};")
    return lab


class PasswordField(QWidget):
    """Campo password con pulsante mostra/nascondi e avviso Bloc Maiusc."""

    changed = Signal()

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setMaxLength(127)
        self.edit.textChanged.connect(self.changed)
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setIcon(icon("eye"))
        self.toggle.setToolTip(_("Mostra/nascondi"))
        self.toggle.setFlat(True)
        self.toggle.toggled.connect(self._toggle)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.toggle)

    def _toggle(self, on: bool) -> None:
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.toggle.setIcon(icon("eye-off" if on else "eye"))

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


STRENGTH_TEXT = {0: "", 1: "Debole", 2: "Media", 3: "Forte", 4: "Molto forte"}


class StrengthBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setRange(0, 4)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.label = QLabel()
        self.label.setMinimumWidth(80)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.label)

    def set_password(self, pw: str) -> int:
        score = P.strength(pw)
        self.bar.setValue(score)
        color = {1: "danger", 2: "warning", 3: "success", 4: "success"}.get(score, "border")
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {theme.hex_color(color)}; }}")
        self.label.setText(_(STRENGTH_TEXT[score]) if score else "")
        return score


# ------------------------------------------------------------ protezione

PRESETS = [
    ("", None),
    ("Sola lettura", (P.PRINT_HIGH, {"accessibility"})),
    ("Solo compilazione e firma", (P.PRINT_HIGH, {"form", "accessibility"})),
    ("Commenti, compilazione e firma", (P.PRINT_HIGH, {"annotate", "form", "accessibility"})),
    ("Solo assemblaggio pagine", (P.PRINT_HIGH, {"assemble", "accessibility"})),
]


class ProtectDialog(QDialog):
    """Password di apertura, password dei permessi, restrizioni e cifratura."""

    def __init__(self, current: P.ProtectionSettings, *, already_protected: bool, has_open_password: bool,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Proteggi con password"))
        self.setMinimumWidth(520)
        self._already = already_protected
        self._had_open = has_open_password
        lay = QVBoxLayout(self)

        # --- apertura
        g1 = QGroupBox(_("Apertura"))
        f1 = QFormLayout(g1)
        self.require_open = QCheckBox(_("Richiedi una password per aprire il documento"))
        f1.addRow(self.require_open)
        keep = _("Lascia vuoto per mantenere quella attuale") if has_open_password else ""
        self.open_pw = PasswordField(keep)
        self.open_pw2 = PasswordField(keep)
        self.open_strength = StrengthBar()
        f1.addRow(_("Password:"), self.open_pw)
        f1.addRow(_("Conferma:"), self.open_pw2)
        f1.addRow("", self.open_strength)
        lay.addWidget(g1)

        # --- permessi
        g2 = QGroupBox(_("Permessi"))
        v2 = QVBoxLayout(g2)
        self.restrict = QCheckBox(_("Limita stampa e modifica"))
        v2.addWidget(self.restrict)
        f2 = QFormLayout()
        self.perm_pw = PasswordField(_("Lascia vuoto per mantenere quella attuale") if already_protected else "")
        self.perm_pw2 = PasswordField()
        f2.addRow(_("Password dei permessi:"), self.perm_pw)
        f2.addRow(_("Conferma:"), self.perm_pw2)
        self.preset = QComboBox()
        for label, _v in PRESETS:
            self.preset.addItem(_(label) if label else _("Personalizzati"))
        f2.addRow(_("Preimpostazioni:"), self.preset)
        self.print_combo = QComboBox()
        for key, label in P.print_labels().items():
            self.print_combo.addItem(label, key)
        f2.addRow(_("Stampa:"), self.print_combo)
        v2.addLayout(f2)
        grid = QGridLayout()
        self.perm_boxes: dict[str, QCheckBox] = {}
        for n, (key, label) in enumerate(P.permission_labels().items()):
            cb = QCheckBox(label)
            self.perm_boxes[key] = cb
            grid.addWidget(cb, n // 2, n % 2)
        v2.addLayout(grid)
        self.perm_note = _note("")
        v2.addWidget(self.perm_note)
        self.perms_widgets = [self.perm_pw, self.perm_pw2, self.preset, self.print_combo, *self.perm_boxes.values()]
        lay.addWidget(g2)

        # --- cifratura
        g3 = QGroupBox(_("Cifratura"))
        f3 = QFormLayout(g3)
        self.method = QComboBox()
        minimum = {P.AES256: "PDF 2.0 / 1.7 ext. 8", P.AES128: "PDF 1.6", P.RC4: "PDF 1.4"}
        for key, label in P.method_labels().items():
            self.method.addItem(f"{label} — {minimum[key]}", key)
        f3.addRow(_("Livello di cifratura:"), self.method)
        self.method_note = _note("", "warning")
        f3.addRow(self.method_note)
        lay.addWidget(g3)

        lay.addWidget(_note(_("Una password dimenticata non è recuperabile. La protezione vale dal prossimo "
                              "salvataggio; il documento aperto resta modificabile.")))
        self.error = _note("", "danger")
        lay.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText(_("Applica"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        # stato iniziale
        self.require_open.setChecked(has_open_password or not already_protected)
        self.restrict.setChecked(current.restrict)
        for key, cb in self.perm_boxes.items():
            cb.setChecked(key in current.allowed or not current.restrict)
        self.print_combo.setCurrentIndex(max(0, self.print_combo.findData(current.print_level)))
        self.method.setCurrentIndex(max(0, self.method.findData(current.method)))

        for w in (self.open_pw, self.open_pw2, self.perm_pw, self.perm_pw2):
            w.changed.connect(self._update)
        for w in (self.require_open, self.restrict, *self.perm_boxes.values()):
            w.toggled.connect(self._update)
        self.method.currentIndexChanged.connect(self._update)
        self.print_combo.currentIndexChanged.connect(self._update)
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.perm_boxes["annotate"].toggled.connect(self._update)
        self._update()

    # -------------------------------------------------------------- logica
    def _apply_preset(self, index: int) -> None:
        value = PRESETS[index][1]
        if value is None:
            return
        level, allowed = value
        self.print_combo.setCurrentIndex(self.print_combo.findData(level))
        for key, cb in self.perm_boxes.items():
            cb.setChecked(key in allowed)

    def _keep_open(self) -> bool:
        return self._had_open and self.require_open.isChecked() and not self.open_pw.text()

    def _keep_perm(self) -> bool:
        return self._already and self.restrict.isChecked() and not self.perm_pw.text()

    def _problem(self) -> str:
        if self.require_open.isChecked() and not self._keep_open():
            if not self.open_pw.text():
                return _("Scrivi la password di apertura.")
            if self.open_pw.text() != self.open_pw2.text():
                return _("Le password non coincidono")
        if self.restrict.isChecked() and not self._keep_perm():
            if not self.perm_pw.text():
                return _("Per limitare i permessi serve una password dei permessi.")
            if self.perm_pw.text() != self.perm_pw2.text():
                return _("Le password dei permessi non coincidono")
        if not self.require_open.isChecked() and not self.restrict.isChecked():
            return _("Imposta una password di apertura oppure limita almeno un permesso.")
        try:
            self.settings(validate=True)
        except P.ProtectionError as e:
            return str(e)
        return ""

    def _update(self, *_a) -> None:
        on = self.require_open.isChecked()
        for w in (self.open_pw, self.open_pw2, self.open_strength):
            w.setEnabled(on)
        self.open_strength.set_password(self.open_pw.text() if on else "")
        r = self.restrict.isChecked()
        for w in self.perms_widgets:
            w.setEnabled(r)
        # i commenti comprendono la compilazione dei moduli
        form = self.perm_boxes["form"]
        if self.perm_boxes["annotate"].isChecked():
            form.setChecked(True)
            form.setEnabled(False)
        acc = self.perm_boxes["accessibility"]
        aes256 = self.method.currentData() == P.AES256
        if aes256:
            acc.setChecked(True)
            acc.setEnabled(False)
        notes = []
        if aes256:
            notes.append(_("Con AES a 256 bit l'estrazione per l'accessibilità è sempre consentita (PDF 2.0)."))
        if self.perm_boxes["modify"].isChecked() and self.perm_boxes["annotate"].isChecked():
            notes.append(_("È consentita anche la creazione di campi modulo."))
        if r and not on:
            notes.append(_("Senza password di apertura i permessi sono rispettati dai lettori conformi ma il "
                           "contenuto resta leggibile."))
        self.perm_note.setText(" ".join(notes))
        method = self.method.currentData()
        self.method_note.setText(
            _("Algoritmo debole: usalo solo se il destinatario ha un lettore molto datato.") if method == P.RC4
            else _("AES a 128 bit è deprecato in PDF 2.0: preferisci AES a 256 bit.") if method == P.AES128 else "")
        problem = self._problem()
        self.error.setText(problem)
        self.apply_button.setEnabled(not problem)

    def settings(self, validate: bool = False) -> P.ProtectionSettings:
        s = P.ProtectionSettings(
            open_password=self.open_pw.text() if self.require_open.isChecked() else "",
            permissions_password=self.perm_pw.text() if self.restrict.isChecked() else "",
            restrict=self.restrict.isChecked(),
            allowed={k for k, cb in self.perm_boxes.items() if cb.isChecked()},
            print_level=self.print_combo.currentData(),
            method=self.method.currentData(),
        )
        if validate:
            probe = P.ProtectionSettings(**{**s.__dict__})
            if self._keep_open():
                probe.open_password = "mantieni-1"
            if self._keep_perm():
                probe.permissions_password = "mantieni-2"
            probe.validate()
        return s

    @property
    def keep_open_password(self) -> bool:
        return self._keep_open()

    @property
    def keep_permissions_password(self) -> bool:
        return self._keep_perm()

    def _accept(self) -> None:
        if self._problem():
            return
        if self.require_open.isChecked() and self.open_pw.text() and P.strength(self.open_pw.text()) < 2:
            answer = QMessageBox.question(
                self, _("Password debole"),
                _("La password scelta è debole e facile da indovinare. Usarla comunque?"))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()


# -------------------------------------------------------- password singola


def ask_password(parent, title: str, text: str) -> str | None:
    pw, ok = QInputDialog.getText(parent, title, text, QLineEdit.EchoMode.Password)
    return pw if ok else None


# ------------------------------------------------------------ trova e segna


class FindDialog(QDialog):
    """Trova parole/frasi o dati personali e scegli le occorrenze da segnare."""

    def __init__(self, mode: str, search: Callable[[dict], tuple[list[Match], list[str]]],
                 pattern_items: list[tuple[str, str, bool]] | None = None, parent=None):
        super().__init__(parent)
        self.mode = mode
        self._search = search
        self.matches: list[Match] = []
        self.setWindowTitle(_("Trova e segna") if mode == "terms" else _("Trova dati personali"))
        self.resize(640, 600)
        lay = QVBoxLayout(self)
        if mode == "terms":
            lay.addWidget(QLabel(_("Parole o frasi da oscurare (una per riga):")))
            self.terms = QPlainTextEdit()
            self.terms.setMaximumHeight(90)
            lay.addWidget(self.terms)
            opts = QHBoxLayout()
            self.case = QCheckBox(_("Maiuscole/minuscole"))
            self.whole = QCheckBox(_("Parola intera"))
            self.accents = QCheckBox(_("Ignora accenti"))
            for w in (self.case, self.whole, self.accents):
                opts.addWidget(w)
            opts.addStretch(1)
            lay.addLayout(opts)
            self.outside = QCheckBox(_("Cerca anche in metadati, segnalibri, commenti, campi e allegati"))
            lay.addWidget(self.outside)
        else:
            lay.addWidget(QLabel(_("Schemi da cercare:")))
            grid = QGridLayout()
            self.pattern_boxes: dict[str, QCheckBox] = {}
            for n, (pid, label, checked) in enumerate(pattern_items or []):
                cb = QCheckBox(label)
                cb.setChecked(checked)
                self.pattern_boxes[pid] = cb
                grid.addWidget(cb, n // 2, n % 2)
            lay.addLayout(grid)
            row = QHBoxLayout()
            row.addWidget(QLabel(_("Schema personalizzato:")))
            self.custom = QLineEdit()
            self.custom.setPlaceholderText(_("espressione regolare, es. SN-\\d{6}"))
            row.addWidget(self.custom, 1)
            lay.addLayout(row)
        scope = QHBoxLayout()
        scope.addWidget(QLabel(_("Pagine:")))
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(_("tutte (oppure es. 1-3, 5)"))
        scope.addWidget(self.pages, 1)
        self.find_button = QPushButton(_("Cerca"))
        self.find_button.setIcon(icon("search"))
        self.find_button.clicked.connect(self.run_search)
        scope.addWidget(self.find_button)
        lay.addLayout(scope)
        self.notice = _note("", "warning")
        lay.addWidget(self.notice)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Occorrenza"), _("Pagina")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.tree, 1)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Segna selezionati"))
        self.ok.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def options(self) -> dict:
        o: dict = {"pages": self.pages.text().strip()}
        if self.mode == "terms":
            o.update(terms=[t for t in self.terms.toPlainText().splitlines() if t.strip()],
                     case_sensitive=self.case.isChecked(), whole_word=self.whole.isChecked(),
                     ignore_accents=self.accents.isChecked(), outside=self.outside.isChecked())
        else:
            o.update(patterns=[k for k, cb in self.pattern_boxes.items() if cb.isChecked()],
                     custom=self.custom.text().strip())
        return o

    def run_search(self) -> None:
        try:
            matches, notices = self._search(self.options())
        except ValueError as e:
            self.notice.setText(str(e))
            return
        self.set_results(matches, notices)

    def set_results(self, matches: list[Match], notices: list[str]) -> None:
        self.matches = matches
        self.notice.setText("\n".join(notices))
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for n, m in enumerate(matches):
            key = m.pattern
            parent = groups.get(key)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [m.pattern_label or m.pattern, ""])
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
                parent.setCheckState(0, Qt.CheckState.Checked)
                parent.setExpanded(True)
                groups[key] = parent
            item = QTreeWidgetItem(parent, [m.context or m.text, str(m.page + 1) if m.page >= 0 else "—"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, n)
            item.setToolTip(0, m.text)
        for key, parent in groups.items():
            parent.setText(0, f"{parent.text(0)} ({parent.childCount()})")
        self.summary.setText(_("{n} occorrenze trovate").format(n=len(matches)) if matches
                             else _("Nessuna occorrenza trovata"))
        self.ok.setEnabled(bool(matches))

    def selected(self) -> list[Match]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                if item.checkState(0) == Qt.CheckState.Checked:
                    out.append(self.matches[item.data(0, Qt.ItemDataRole.UserRole)])
        return out


# ------------------------------------------------------------------ pagine


class PagesDialog(QDialog):
    """Scelta delle pagine: corrente, intervallo, pari, dispari, tutte."""

    def __init__(self, title: str, page_count: int, current: int, parent=None, with_overlay: bool = False,
                 overlay: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.page_count = page_count
        self.current = current
        lay = QVBoxLayout(self)
        self.group = QButtonGroup(self)
        self.radios = {}
        for key, label in (("current", _("Pagina corrente ({n})").format(n=current + 1)),
                           ("range", _("Intervallo:")), ("even", _("Pagine pari")), ("odd", _("Pagine dispari")),
                           ("all", _("Tutte le pagine ({n})").format(n=page_count))):
            rb = QRadioButton(label)
            self.radios[key] = rb
            self.group.addButton(rb)
            if key == "range":
                row = QHBoxLayout()
                row.addWidget(rb)
                self.range = QLineEdit()
                self.range.setPlaceholderText(_("es. 2, 4-7"))
                self.range.textEdited.connect(lambda _t: self.radios["range"].setChecked(True))
                row.addWidget(self.range, 1)
                lay.addLayout(row)
            else:
                lay.addWidget(rb)
        self.radios["current"].setChecked(True)
        self.overlay = None
        if with_overlay:
            row = QHBoxLayout()
            row.addWidget(QLabel(_("Testo sovrapposto:")))
            self.overlay = QLineEdit(overlay)
            self.overlay.setPlaceholderText(_("facoltativo, es. Pagina omessa"))
            row.addWidget(self.overlay, 1)
            lay.addLayout(row)
        self.error = _note("", "danger")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def pages(self) -> list[int]:
        key = next(k for k, rb in self.radios.items() if rb.isChecked())
        n = self.page_count
        if key == "current":
            return [self.current]
        if key == "even":
            return list(range(1, n, 2))
        if key == "odd":
            return list(range(0, n, 2))
        if key == "all":
            return list(range(n))
        return parse_pages(self.range.text(), n)

    def _accept(self) -> None:
        try:
            if not self.pages():
                raise ValueError(_("Nessuna pagina scelta."))
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.accept()


def parse_pages(text: str, count: int) -> list[int]:
    """«2, 4-7» → [1, 3, 4, 5, 6] (indici da zero). Solleva ValueError."""
    text = (text or "").replace(" ", "")
    if not text:
        return list(range(count))
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        if not part:
            continue
        if "-" in part:
            a, _s, b = part.partition("-")
            lo = int(a) if a.isdigit() else None
            hi = int(b) if b.isdigit() else (count if b == "" else None)
            if lo is None or hi is None:
                raise ValueError(_("Intervallo non valido: «{p}»").format(p=part))
        elif part.isdigit():
            lo = hi = int(part)
        else:
            raise ValueError(_("Intervallo non valido: «{p}»").format(p=part))
        if lo < 1 or hi > count or lo > hi:
            raise ValueError(_("Le pagine vanno da 1 a {n}.").format(n=count))
        out.extend(i - 1 for i in range(lo, hi + 1) if i - 1 not in out)
    return out


# --------------------------------------------------------------- aspetto


def _rgb_hex(rgb) -> str:
    return QColor.fromRgbF(*rgb).name()


class ColorButton(QPushButton):
    def __init__(self, rgb, parent=None):
        super().__init__(parent)
        self.setFixedWidth(64)
        self.set_rgb(rgb)
        self.clicked.connect(self._pick)

    def set_rgb(self, rgb) -> None:
        self.rgb = tuple(float(c) for c in rgb)
        self.setStyleSheet(f"background: {_rgb_hex(self.rgb)}; border: 1px solid {theme.hex_color('border')};")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor.fromRgbF(*self.rgb), self, _("Scegli colore"))
        if c.isValid():
            self.set_rgb((c.redF(), c.greenF(), c.blueF()))


class StyleDialog(QDialog):
    """Aspetto dei riquadri (riempimento, testo sovrapposto) e opzioni di applicazione."""

    def __init__(self, style, options, codes: list[str], parent=None):
        super().__init__(parent)
        from . import redaction as R
        self.setWindowTitle(_("Impostazioni di redazione"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        g = QGroupBox(_("Aspetto del riquadro"))
        f = QFormLayout(g)
        self.fill = ColorButton(style.fill)
        f.addRow(_("Riempimento:"), self.fill)
        self.text = QComboBox()
        self.text.setEditable(True)
        self.text.addItem("")
        for c in codes:
            self.text.addItem(c)
        self.text.setCurrentText(style.overlay_text)
        self.text.lineEdit().setPlaceholderText(_("nessuno (es. OMISSIS)"))
        f.addRow(_("Testo sovrapposto:"), self.text)
        self.text_color = ColorButton(style.text_color)
        f.addRow(_("Colore del testo:"), self.text_color)
        self.size = QDoubleSpinBox()
        self.size.setRange(0, 72)
        self.size.setSpecialValueText(_("Automatica"))
        self.size.setValue(style.font_size)
        f.addRow(_("Dimensione:"), self.size)
        self.repeat = QCheckBox(_("Ripeti il testo per riempire l'area"))
        self.repeat.setChecked(style.repeat_text)
        f.addRow(self.repeat)
        lay.addWidget(g)
        g2 = QGroupBox(_("Applicazione"))
        f2 = QFormLayout(g2)
        self.images = QComboBox()
        self.images.addItem(_("Cancella solo i pixel coperti"), R.IMAGES_PIXELS)
        self.images.addItem(_("Elimina le immagini toccate"), R.IMAGES_REMOVE)
        self.images.setCurrentIndex(max(0, self.images.findData(options.images)))
        f2.addRow(_("Immagini:"), self.images)
        self.graphics = QComboBox()
        self.graphics.addItem(_("Elimina la grafica coperta"), R.GRAPHICS_COVERED)
        self.graphics.addItem(_("Elimina la grafica che tocca l'area"), R.GRAPHICS_TOUCHED)
        self.graphics.setCurrentIndex(max(0, self.graphics.findData(options.graphics)))
        f2.addRow(_("Grafica:"), self.graphics)
        lay.addWidget(g2)
        self.warning = _note("", "warning")
        lay.addWidget(self.warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.fill.clicked.connect(self._check)
        self.text_color.clicked.connect(self._check)
        self._check()

    def _check(self) -> None:
        w = []
        if all(c > 0.95 for c in self.fill.rgb):
            w.append(_("Con il riempimento bianco l'omissione non sarà evidente a chi legge."))
        if self.text.currentText().strip() and self.fill.rgb == self.text_color.rgb:
            w.append(_("Il colore del testo coincide con il riempimento: il testo non si vedrà."))
        self.warning.setText(" ".join(w))

    def values(self):
        from . import redaction as R
        style = R.MarkStyle(fill=self.fill.rgb, overlay_text=self.text.currentText().strip(),
                            text_color=self.text_color.rgb, font_size=self.size.value(),
                            repeat_text=self.repeat.isChecked())
        options = R.ApplyOptions(images=self.images.currentData(), graphics=self.graphics.currentData())
        return style, options


# ------------------------------------------------------------ sanificazione


class SanitizeDialog(QDialog):
    def __init__(self, counts: dict[str, str], parent=None):
        super().__init__(parent)
        from . import sanitize as S
        self.setWindowTitle(_("Sanifica documento"))
        lay = QVBoxLayout(self)
        lay.addWidget(_note(_("Rimuove le informazioni nascoste e i contenuti attivi scelti senza cambiare "
                              "l'aspetto delle pagine.")))
        self.boxes: dict[str, QCheckBox] = {}
        defaults = S.default_sanitize_options()
        for key, label in S.sanitize_labels().items():
            extra = counts.get(key, "")
            cb = QCheckBox(f"{label}  {extra}".rstrip())
            cb.setChecked(defaults.get(key, False))
            self.boxes[key] = cb
            lay.addWidget(cb)
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Moduli:")))
        self.form_mode = QComboBox()
        self.form_mode.addItem(_("Azzera i valori"), "reset")
        self.form_mode.addItem(_("Rimuovi i campi"), "remove")
        row.addWidget(self.form_mode, 1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Sanifica"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def options(self) -> dict:
        o = {k: cb.isChecked() for k, cb in self.boxes.items()}
        o["remove_fields"] = self.form_mode.currentData() == "remove" and o.get("form_data", False)
        return o


# ----------------------------------------------------------------- metadati


class MetadataDialog(QDialog):
    def __init__(self, values: dict[str, str], info: list[tuple[str, str]], read_only: bool = False, parent=None):
        super().__init__(parent)
        from . import sanitize as S
        self.setWindowTitle(_("Metadati del documento"))
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for key in ("title", "author", "subject", "keywords"):
            e = QLineEdit(values.get(key, ""))
            e.setReadOnly(read_only)
            self.edits[key] = e
            form.addRow(S.metadata_labels()[key] + ":", e)
        lay.addLayout(form)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)
        ro = QFormLayout()
        for label, value in info:
            v = QLabel(value or "—")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            ro.addRow(label + ":", v)
        lay.addLayout(ro)
        if read_only:
            lay.addWidget(_note(_("Il documento non consente modifiche: i campi sono in sola lettura."), "warning"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not read_only)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {k: e.text() for k, e in self.edits.items()}


# ---------------------------------------------------------- elenco/rapporto


class ReportDialog(QDialog):
    """Elenco a due colonne (categoria → dettagli) con pulsanti facoltativi."""

    def __init__(self, title: str, intro: str, rows: list[tuple[str, list[str]]], parent=None,
                 action: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 460)
        lay = QVBoxLayout(self)
        if intro:
            lay.addWidget(_note(intro, "text"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        for head, details in rows:
            item = QTreeWidgetItem(self.tree, [head])
            for d in details:
                child = QTreeWidgetItem(item, [d])
                child.setToolTip(0, d)
            item.setExpanded(len(details) <= 8)
        lay.addWidget(self.tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        self.action_button = None
        if action:
            self.action_button = buttons.addButton(action, QDialogButtonBox.ButtonRole.AcceptRole)
            self.action_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
