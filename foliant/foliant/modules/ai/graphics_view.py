"""Dialogo «Genera con IA» per filigrane, sfondi, timbri e adesivi (AI-25).

Uso tipico da uno strumento (Filigrana, Sfondo, Timbri, Adesivi)::

    dlg = GenerateGraphicDialog(profile, provider, lambda t, fn: ctx.run_task(t, fn),
                                parent, kind="stamp")
    if dlg.exec() and dlg.chosen_png:
        graphics.apply_as_stamp(document, page, rect, dlg.chosen_png)

`provider` è il fornitore per il modello di immagini (o una funzione senza argomenti che lo
restituisce, chiamata solo al momento della generazione). `run(titolo, fn)` esegue
`fn(progress=None)` mostrando l'avanzamento e ne restituisce il risultato (None se annullato).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme

from . import graphics
from .providers import REFUSED, AIError

THUMB = 160


class GenerateGraphicDialog(QDialog):
    """Descrizione → varianti in anteprima → scelta («Usa»), salvata nella libreria."""

    def __init__(self, profile, provider, run: Callable[[str, Callable], object], parent=None, *,
                 kind: str = "stamp", library: graphics.Library | None = None):
        super().__init__(parent)
        self.profile = profile
        self._provider = provider
        self.run = run
        self.library = library or graphics.Library()
        self.variants: list[bytes] = []
        self.chosen_png: bytes | None = None
        self.chosen_item: graphics.LibraryItem | None = None
        self.last_prompt = ""
        self.setWindowTitle(_("Genera con IA"))
        self.resize(720, 620)

        lay = QVBoxLayout(self)
        self.disabled_label = QLabel()
        self.disabled_label.setWordWrap(True)
        self.disabled_label.setStyleSheet(f"color: {theme.color('warning').name()};")
        lay.addWidget(self.disabled_label)

        form = QFormLayout()
        self.kind_combo = QComboBox()
        for k in graphics.KINDS:
            self.kind_combo.addItem(_(graphics.KIND_LABELS[k]), k)
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(kind)))
        form.addRow(_("Tipo"), self.kind_combo)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(_("Descrivi l'elemento, ad esempio: «sigillo rotondo con un orologio»"))
        self.description.setFixedHeight(80)
        form.addRow(_("Descrizione"), self.description)
        self.style_combo = QComboBox()
        self.style_combo.setEditable(True)
        for s in graphics.STYLES:
            self.style_combo.addItem(_(s) if s else _("Libero"), s)
        form.addRow(_("Stile"), self.style_combo)
        self.colors = QLineEdit()
        self.colors.setPlaceholderText(_("ad esempio: blu e oro"))
        form.addRow(_("Colori"), self.colors)
        self.transparent = QCheckBox(_("Sfondo trasparente"))
        form.addRow("", self.transparent)
        self.variants_spin = QSpinBox()
        self.variants_spin.setRange(graphics.MIN_VARIANTS, graphics.MAX_VARIANTS)
        self.variants_spin.setValue(3)
        form.addRow(_("Varianti"), self.variants_spin)
        self.size_combo = QComboBox()
        for s in graphics.SIZES:
            self.size_combo.addItem(s.replace("x", " × "), s)
        form.addRow(_("Dimensione"), self.size_combo)
        lay.addLayout(form)

        self.previews = QListWidget()
        self.previews.setViewMode(QListView.IconMode)
        self.previews.setIconSize(QSize(THUMB, THUMB))
        self.previews.setResizeMode(QListView.Adjust)
        self.previews.setMovement(QListView.Static)
        self.previews.setSpacing(8)
        self.previews.setMinimumHeight(THUMB + 40)
        self.previews.itemSelectionChanged.connect(self._update_buttons)
        self.previews.itemDoubleClicked.connect(lambda _item: self.use())
        lay.addWidget(self.previews, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        lay.addWidget(self.message)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton(_("Genera"))
        bind_icon(self.generate_button, "sparkles")
        self.generate_button.clicked.connect(self.generate)
        self.remove_bg_button = QPushButton(_("Rimuovi sfondo"))
        bind_icon(self.remove_bg_button, "image-minus")
        self.remove_bg_button.setToolTip(_("Rende trasparente lo sfondo uniforme della variante scelta (in locale)"))
        self.remove_bg_button.clicked.connect(self.remove_background)
        self.use_button = QPushButton(_("Usa"))
        bind_icon(self.use_button, "check")
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self.use)
        cancel = QPushButton(_("Annulla"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.generate_button)
        buttons.addWidget(self.remove_bg_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.use_button)
        lay.addLayout(buttons)

        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()
        ok, reason = graphics.can_generate(profile)
        self.available = ok
        self.disabled_label.setText(reason)
        self.disabled_label.setVisible(not ok)
        for w in (self.description, self.style_combo, self.colors, self.transparent, self.variants_spin,
                  self.size_combo, self.generate_button):
            w.setEnabled(ok)
        if not ok:
            self.generate_button.setToolTip(reason)
        self._update_buttons()

    # ---------------------------------------------------------------- stato
    @property
    def kind(self) -> str:
        return self.kind_combo.currentData()

    def _kind_changed(self) -> None:
        self.transparent.setChecked(self.kind in ("stamp", "sticker"))

    def selected_index(self) -> int:
        rows = [self.previews.row(i) for i in self.previews.selectedItems()]
        return rows[0] if rows else -1

    def _update_buttons(self) -> None:
        i = self.selected_index()
        self.use_button.setEnabled(i >= 0)
        needs_bg = i >= 0 and self.kind in ("stamp", "sticker") and not graphics.has_transparency(self.variants[i])
        self.remove_bg_button.setVisible(needs_bg)
        if needs_bg:
            self.message.setText(_("La variante scelta non ha uno sfondo trasparente: "
                                   "puoi rimuovere localmente lo sfondo uniforme."))

    def _show_variants(self) -> None:
        self.previews.clear()
        for n, png in enumerate(self.variants):
            pm = QPixmap()
            pm.loadFromData(png)
            item = QListWidgetItem(QIcon(pm.scaled(THUMB, THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)),
                                   _("Variante {n}").format(n=n + 1))
            self.previews.addItem(item)
        if self.variants:
            self.previews.setCurrentRow(0)
        self._update_buttons()

    # -------------------------------------------------------------- azioni
    def _get_provider(self):
        p = self._provider
        return p() if callable(p) else p

    def generate(self) -> None:
        if not self.available:
            return
        prompt = self.description.toPlainText().strip()
        if not prompt:
            self.message.setText(_("Scrivi una descrizione dell'elemento da generare."))
            return
        idx = self.style_combo.findText(self.style_combo.currentText())
        style = self.style_combo.itemData(idx) if idx >= 0 else self.style_combo.currentText().strip()
        args = dict(prompt=prompt, style=style or "", colors=self.colors.text().strip(),
                    transparent=self.transparent.isChecked(), n=self.variants_spin.value(),
                    size=self.size_combo.currentData(), kind=self.kind)
        model = self.profile.image_model

        def work(progress=None):
            if progress is not None:
                progress.update(0.05, _("Generazione delle immagini…"))
            try:
                return graphics.generate_images(self._get_provider(), model, **args)
            except AIError as e:
                return e

        self.message.setText(_("Generazione in corso…"))
        result = self.run(_("Generazione di immagini"), work)
        if result is None:
            self.message.setText(_("Generazione annullata."))
            return
        if isinstance(result, AIError):
            if result.kind == REFUSED:
                self.message.setText(_("Il fornitore ha rifiutato la richiesta in base alle sue regole sui "
                                       "contenuti. Modifica la descrizione e riprova."))
            else:
                self.message.setText(result.user_message())
            return
        self.last_prompt = prompt
        self.variants = list(result)
        self.message.setText(_("Scegli una variante oppure rigenera."))
        self.generate_button.setText(_("Rigenera"))
        self._show_variants()

    def remove_background(self) -> None:
        i = self.selected_index()
        if i < 0:
            return
        self.variants[i] = graphics.remove_uniform_background(self.variants[i])
        if not graphics.has_transparency(self.variants[i]):
            self.message.setText(_("Lo sfondo non è uniforme: non è stato possibile rimuoverlo."))
        else:
            self.message.setText(_("Sfondo rimosso."))
        self._show_variants()
        self.previews.setCurrentRow(i)

    def use(self) -> None:
        i = self.selected_index()
        if i < 0:
            return
        self.chosen_png = self.variants[i]
        try:
            self.chosen_item = self.library.save(self.chosen_png, self.last_prompt, self.kind)
        except OSError as e:
            self.message.setText(_("Impossibile salvare nella libreria: {e}").format(e=e))
            self.chosen_item = None
        self.accept()
