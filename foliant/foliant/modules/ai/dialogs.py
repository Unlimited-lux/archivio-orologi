"""Dialoghi dell'assistente: impostazioni dei profili, informativa, anteprima, scelte."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from foliant.api import _, bind_icon, theme

from . import config, providers
from .config import CATALOG, KeyringUnavailable, Profile, ProfileStore
from .prompts import LANGUAGE_NAMES_IT


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
    return lab


# =================================================================== informativa
def consent_text(profile: Profile, encrypted: bool = False) -> str:
    dest = _("elaborazione sul computer: i dati non lasciano il computer") if profile.is_local else profile.host
    lines = [
        _("<b>Destinazione:</b> {d}").format(d=dest),
        _("<b>Modello:</b> {m}").format(m=profile.model or "—"),
        "",
        _("<b>Vengono inviati</b>: il contenuto dell'ambito che scegli (testo delle pagine con i marcatori di "
          "pagina, immagini delle aree scelte), le tue richieste e i messaggi precedenti della stessa conversazione."),
        _("<b>Non vengono mai inviati</b>: il file PDF in sé, il nome e il percorso del file, i metadati, gli "
          "allegati incorporati e gli altri documenti. Commenti e valori dei campi modulo solo se attivi "
          "l'opzione relativa."),
        _("Foliant non riceve alcun dato e non raccoglie telemetria sulle funzioni IA. I dati inviati sono "
          "trattati secondo le condizioni del fornitore."),
    ]
    if profile.privacy_url:
        lines.append(_("Informativa del fornitore: {u}").format(u=profile.privacy_url))
    if encrypted:
        lines.append(_("Il documento è cifrato: il contenuto viene inviato in forma decifrata."))
    if profile.insecure:
        lines.append(_("<b>Attenzione:</b> la connessione non è cifrata."))
    return "<br>".join(lines)


class ConsentDialog(QDialog):
    def __init__(self, profile: Profile, encrypted: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Informativa sui dati inviati"))
        lay = QVBoxLayout(self)
        title = QLabel(_("Prima di usare il profilo «{n}»").format(n=profile.name))
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(title)
        body = QLabel(consent_text(profile, encrypted))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMinimumWidth(460)
        lay.addWidget(body)
        box = QDialogButtonBox()
        box.addButton(_("Accetto"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(_("Non accetto"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


# =================================================================== anteprima
class PreviewDialog(QDialog):
    """Anteprima integrale di ciò che verrà inviato, con «Invia»."""

    def __init__(self, summary: str, text: str, parent=None, *, confirm: bool = True):
        super().__init__(parent)
        self.setWindowTitle(_("Verrà inviato"))
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        lab = QLabel(summary)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        edit = QPlainTextEdit(text)
        edit.setReadOnly(True)
        lay.addWidget(edit, 1)
        box = QDialogButtonBox()
        if confirm:
            box.addButton(_("Invia"), QDialogButtonBox.ButtonRole.AcceptRole)
            box.addButton(QDialogButtonBox.StandardButton.Cancel)
        else:
            box.addButton(QDialogButtonBox.StandardButton.Close)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


# =================================================================== pagine senza testo
class TextlessDialog(QDialog):
    """AI-15: OCR locale, invio come immagini o esclusione."""

    def __init__(self, pages: list[int], images_allowed: bool, languages: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Pagine senza testo"))
        self.choice: str | None = None
        lay = QVBoxLayout(self)
        nums = ", ".join(str(p + 1) for p in pages)
        lab = QLabel(_("Queste pagine non hanno testo utilizzabile: {p}.\nCome vuoi procedere?").format(p=nums))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.language = QComboBox()
        for code in languages or ["ita+eng"]:
            self.language.addItem(code, code)
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Lingue OCR:")))
        row.addWidget(self.language, 1)
        lay.addLayout(row)
        self.save_ocr = QCheckBox(_("Salva il testo riconosciuto nel documento"))
        self.save_ocr.setEnabled(False)
        self.save_ocr.setToolTip(_("Usa la modalità OCR di Foliant per rendere il PDF ricercabile."))
        lay.addWidget(self.save_ocr)
        buttons = QHBoxLayout()
        self.btn_ocr = QPushButton(_("Esegui OCR locale"))
        self.btn_images = QPushButton(_("Invia le pagine come immagini"))
        self.btn_images.setVisible(images_allowed)
        self.btn_exclude = QPushButton(_("Escludi queste pagine"))
        cancel = QPushButton(_("Annulla"))
        for b, c in ((self.btn_ocr, "ocr"), (self.btn_images, "images"), (self.btn_exclude, "exclude")):
            b.clicked.connect(lambda _c=False, c=c: self._pick(c))
            buttons.addWidget(b)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        lay.addLayout(buttons)
        if not languages:
            self.btn_ocr.setEnabled(False)
            lay.addWidget(_muted(_("OCR non disponibile: installa Tesseract e le lingue necessarie.")))

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


# =================================================================== impostazioni
class SettingsDialog(QDialog):
    """Impostazioni › Assistente IA (AI-01, AI-02, AI-18)."""

    def __init__(self, store: ProfileStore, parent=None, *, verify=None):
        super().__init__(parent)
        self.store = store
        self._verify_fn = verify          # funzione (profile, key) -> list[str], simulabile nei test
        self.setWindowTitle(_("Impostazioni dell'Assistente IA"))
        self.resize(820, 600)
        outer = QVBoxLayout(self)
        self.enabled = QCheckBox(_("Funzioni IA"))
        self.enabled.setChecked(store.enabled())
        self.enabled.toggled.connect(store.set_enabled)
        outer.addWidget(self.enabled)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load_current)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_add = QPushButton(_("Aggiungi profilo"))
        bind_icon(self.btn_add, "plus")
        self.btn_dup = QPushButton(_("Duplica"))
        self.btn_del = QPushButton(_("Elimina"))
        self.btn_add.clicked.connect(self.add_profile)
        self.btn_dup.clicked.connect(self.duplicate_profile)
        self.btn_del.clicked.connect(self.delete_profile)
        for b in (self.btn_add, self.btn_dup, self.btn_del):
            row.addWidget(b)
        left.addLayout(row)
        body.addLayout(left, 2)

        self.editor = QGroupBox(_("Profilo"))
        form = QFormLayout(self.editor)
        self.name = QLineEdit()
        self.provider = QComboBox()
        for key, c in CATALOG.items():
            self.provider.addItem(_(c["name"]), key)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.base_url = QLineEdit()
        self.protocol = QComboBox()
        self.protocol.addItem("Anthropic Messages", "anthropic")
        self.protocol.addItem(_("Compatibile OpenAI (chat completions)"), "openai")
        key_row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(_("Chiave API (salvata nel portachiavi)"))
        self.show_key = QPushButton()
        bind_icon(self.show_key, "eye")
        self.show_key.setCheckable(True)
        self.show_key.setToolTip(_("Mostra la chiave"))
        self.show_key.toggled.connect(lambda on: self.key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.show_key)
        key_w = QWidget()
        key_w.setLayout(key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        self.session_only = QCheckBox(_("Usa la chiave solo per questa sessione"))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.context = QSpinBox()
        self.context.setRange(1000, 2_000_000)
        self.context.setSingleStep(1000)
        self.context.setSuffix(" token")
        self.max_out = QSpinBox()
        self.max_out.setRange(256, 128_000)
        self.max_out.setSuffix(" token")
        self.images = QCheckBox(_("Il modello accetta immagini"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSpecialValueText(_("nessun limite"))
        self.period = QComboBox()
        self.period.addItem(_("al giorno"), "day")
        self.period.addItem(_("al mese"), "month")
        lim_row = QHBoxLayout()
        lim_row.setContentsMargins(0, 0, 0, 0)
        lim_row.addWidget(self.limit)
        lim_row.addWidget(self.period)
        lim_w = QWidget()
        lim_w.setLayout(lim_row)
        self.privacy = QLineEdit()
        self.verify_btn = QPushButton(_("Verifica connessione"))
        bind_icon(self.verify_btn, "plug-zap")
        self.verify_btn.clicked.connect(self.verify)
        self.verify_result = QLabel("")
        self.verify_result.setWordWrap(True)
        self.save_btn = QPushButton(_("Salva profilo"))
        self.save_btn.clicked.connect(self.save_profile)
        self.activate_btn = QPushButton(_("Rendi attivo"))
        self.activate_btn.clicked.connect(self.activate_profile)
        self.revoke_btn = QPushButton(_("Revoca consenso"))
        self.revoke_btn.clicked.connect(self.revoke_consent)
        form.addRow(_("Nome"), self.name)
        form.addRow(_("Fornitore"), self.provider)
        form.addRow(_("URL base"), self.base_url)
        form.addRow(_("Protocollo"), self.protocol)
        form.addRow(_("Chiave API"), key_w)
        form.addRow("", self.session_only)
        form.addRow(_("Modello"), self.model)
        form.addRow(_("Finestra di contesto"), self.context)
        form.addRow(_("Risposta massima"), self.max_out)
        form.addRow("", self.images)
        form.addRow(_("Limite di utilizzo"), lim_w)
        form.addRow(_("Informativa del fornitore"), self.privacy)
        btns = QHBoxLayout()
        for b in (self.verify_btn, self.save_btn, self.activate_btn, self.revoke_btn):
            btns.addWidget(b)
        bw = QWidget()
        bw.setLayout(btns)
        btns.setContentsMargins(0, 0, 0, 0)
        form.addRow(bw)
        form.addRow(self.verify_result)
        body.addWidget(self.editor, 3)

        opts = QGroupBox(_("Opzioni"))
        ol = QFormLayout(opts)
        self.confirm_each = QCheckBox(_("Chiedi conferma prima di ogni invio"))
        self.confirm_each.setChecked(store.option("confirm_each", False))
        self.confirm_each.toggled.connect(lambda v: store.set_option("confirm_each", v))
        self.history_on = QCheckBox(_("Conserva la cronologia delle conversazioni"))
        self.history_on.setChecked(store.option("history_enabled", True))
        self.history_on.toggled.connect(lambda v: store.set_option("history_enabled", v))
        self.retention = QComboBox()
        for days, text in ((0, _("mai")), (30, _("30 giorni")), (90, _("90 giorni")), (365, _("365 giorni"))):
            self.retention.addItem(text, days)
        idx = self.retention.findData(store.option("retention_days", 0))
        self.retention.setCurrentIndex(max(0, idx))
        self.retention.currentIndexChanged.connect(lambda _i: store.set_option("retention_days",
                                                                               self.retention.currentData()))
        ol.addRow(self.confirm_each)
        ol.addRow(self.history_on)
        ol.addRow(_("Elimina automaticamente dopo"), self.retention)
        outer.addWidget(opts)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        outer.addWidget(close)
        self._current: Profile | None = None
        self._models_verified: list[str] = []
        self.refresh()

    # ----------------------------------------------------------------- elenco
    def refresh(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        active = self.store.active_id()
        for p in self.store.profiles():
            text = p.name + (_("  (attivo)") if p.id == active else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.list.addItem(item)
            if p.id == (select or (self._current.id if self._current else None)):
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._load_current()

    def _load_current(self, *_a) -> None:
        item = self.list.currentItem()
        p = self.store.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self._current = p
        self.editor.setEnabled(p is not None)
        self.btn_dup.setEnabled(p is not None)
        self.btn_del.setEnabled(p is not None)
        if p is None:
            return
        self.name.setText(p.name)
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(p.provider)))
        self.provider.blockSignals(False)
        self.base_url.setText(p.base_url)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(p.protocol)))
        self.key.setText("")
        has_key = bool(self.store.secrets.get(p.id))
        self.key.setPlaceholderText(_("Chiave salvata (lascia vuoto per non cambiarla)") if has_key
                                    else _("Chiave API (salvata nel portachiavi)"))
        self.session_only.setChecked(self.store.secrets.is_session_only(p.id))
        self.model.clear()
        self.model.addItems(p.models or ([p.model] if p.model else []))
        self.model.setCurrentText(p.model)
        self.context.setValue(int(p.context_window))
        self.max_out.setValue(int(p.max_output))
        self.images.setChecked(p.images)
        self.limit.setValue(int(p.usage_limit))
        self.period.setCurrentIndex(max(0, self.period.findData(p.usage_period)))
        self.privacy.setText(p.privacy_url)
        self._models_verified = list(p.models) if p.verified else []
        state = _("Verificato") if p.verified else _("Non verificato")
        if p.consent:
            state += " · " + _("informativa accettata")
        self.verify_result.setText(state)
        self.activate_btn.setEnabled(p.verified)
        self.revoke_btn.setEnabled(p.consent)

    def _provider_changed(self) -> None:
        key = self.provider.currentData()
        c = CATALOG.get(key, CATALOG["custom"])
        self.base_url.setText(c["base_url"])
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(c["protocol"])))
        self.model.clear()
        self.model.addItems(c["models"])
        self.context.setValue(c["context_window"])
        self.images.setChecked(c["images"])
        self.privacy.setText(c["privacy_url"])

    # ----------------------------------------------------------------- azioni
    def add_profile(self) -> Profile:
        p = Profile.from_catalog("anthropic")
        n = len(self.store.profiles()) + 1
        p.name = _("Profilo {n}").format(n=n)
        self.store.save(p)
        self.refresh(select=p.id)
        return p

    def duplicate_profile(self) -> None:
        if self._current:
            q = self.store.duplicate(self._current.id)
            if q:
                self.refresh(select=q.id)

    def delete_profile(self) -> None:
        if not self._current:
            return
        if QMessageBox.question(self, _("Elimina profilo"),
                                _("Eliminare il profilo «{n}» e la sua chiave dal portachiavi?")
                                .format(n=self._current.name)) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(self._current.id)
        self._current = None
        self.refresh()

    def _collect(self) -> Profile:
        p = self._current or Profile()
        key = self.provider.currentData()
        p.name = self.name.text().strip() or p.name
        p.provider = key
        p.base_url = self.base_url.text().strip()
        p.protocol = self.protocol.currentData()
        p.model = self.model.currentText().strip()
        p.context_window = self.context.value()
        p.max_output = self.max_out.value()
        p.images = self.images.isChecked()
        p.usage_limit = self.limit.value()
        p.usage_period = self.period.currentData()
        p.privacy_url = self.privacy.text().strip()
        p.requires_key = key != "custom"
        return p

    def _store_key(self, p: Profile) -> bool:
        key = self.key.text().strip()
        if not key:
            return True
        if self.session_only.isChecked():
            self.store.secrets.set(p.id, key, session_only=True)
            return True
        try:
            self.store.secrets.set(p.id, key)
        except KeyringUnavailable as e:
            answer = QMessageBox.question(
                self, _("Portachiavi non disponibile"),
                _("{e}\n\nLa chiave non verrà mai salvata in chiaro. Usarla solo per questa sessione? "
                  "Andrà reinserita al prossimo avvio.").format(e=e))
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self.store.secrets.set(p.id, key, session_only=True)
            self.session_only.setChecked(True)
        return True

    def save_profile(self) -> bool:
        p = self._collect()
        old = self.store.get(p.id)
        if old is not None and (old.base_url != p.base_url or old.model != p.model or old.protocol != p.protocol):
            p.verified = p.model in self._models_verified and old.base_url == p.base_url
        if p.insecure and not p.insecure_confirmed:
            answer = QMessageBox.warning(
                self, _("Connessione non cifrata"),
                _("L'endpoint «{h}» non usa una connessione cifrata e non è sul computer: i dati viaggerebbero "
                  "in chiaro. Salvare comunque?").format(h=p.host),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
            p.insecure_confirmed = True
        if not self._store_key(p):
            return False
        self.store.save(p)
        self.key.clear()
        self.refresh(select=p.id)
        return True

    def verify(self) -> bool:
        if not self.save_profile():
            return False
        p = self.store.get(self._current.id)
        key = self.store.secrets.get(p.id)
        if p.requires_key and not key:
            self.verify_result.setText(_("Inserisci la chiave API."))
            return False
        try:
            if self._verify_fn is not None:
                models = self._verify_fn(p, key)
            else:
                provider = providers.make_provider(p.protocol, p.base_url, key, p.model,
                                                   p.connect_timeout, p.idle_timeout)
                models = provider.list_models()
        except providers.AIError as e:
            p.verified = False
            self.store.save(p)
            self.verify_result.setText(_("Verifica non riuscita: {m}").format(
                m=_("Chiave non valida") if e.kind == providers.AUTH else e.user_message()))
            self.verify_result.setToolTip(providers.mask_secret(e.detail, key))
            self.activate_btn.setEnabled(False)
            return False
        except Exception as e:  # noqa: BLE001
            self.verify_result.setText(_("Verifica non riuscita: {m}").format(m=providers.mask_secret(str(e), key)))
            self.activate_btn.setEnabled(False)
            return False
        p.models = list(models) or p.models
        if not p.model and p.models:
            p.model = p.models[0]
        p.verified = True
        self._models_verified = list(p.models)
        self.store.save(p)
        self.refresh(select=p.id)
        self.verify_result.setText(_("Connessione riuscita: {n} modelli disponibili.").format(n=len(p.models)))
        return True

    def ask_consent(self, profile: Profile) -> bool:
        return ConsentDialog(profile, parent=self).exec() == QDialog.DialogCode.Accepted

    def activate_profile(self) -> bool:
        if not self._current:
            return False
        p = self.store.get(self._current.id)
        ok, why = p.can_activate()
        if not ok:
            self.verify_result.setText(why)
            return False
        if not p.consent:
            if not self.ask_consent(p):
                return False
            p.consent = True
            self.store.save(p)
        self.store.set_active(p.id)
        self.refresh(select=p.id)
        return True

    def revoke_consent(self) -> None:
        if not self._current:
            return
        p = self.store.get(self._current.id)
        p.consent = False
        self.store.save(p)
        if self.store.active_id() == p.id:
            self.store.set_active(None)
        self.refresh(select=p.id)


# =================================================================== registro invii
class SendLogDialog(QDialog):
    def __init__(self, log: config.SendLog, parent=None, *, ask_save=None):
        super().__init__(parent)
        self.log = log
        self.ask_save = ask_save
        self.setWindowTitle(_("Registro degli invii"))
        self.resize(820, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(_muted(_("Il registro non contiene il contenuto inviato, solo i dati dell'invio.")))
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([_("Data e ora"), _("Profilo"), _("Dominio"), _("Modello"),
                                              _("Funzione"), _("Pagine"), _("Caratteri"), _("Immagini"), _("Esito")])
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        exp = QPushButton(_("Esporta…"))
        clr = QPushButton(_("Cancella registro"))
        exp.clicked.connect(self.export)
        clr.clicked.connect(self.clear)
        row.addWidget(exp)
        row.addWidget(clr)
        row.addStretch(1)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        entries = self.log.entries()
        self.table.setRowCount(len(entries))
        for r, e in enumerate(reversed(entries)):
            vals = [e.get("time", ""), e.get("profile", ""), e.get("domain", ""), e.get("model", ""),
                    e.get("function", ""), ", ".join(str(p) for p in e.get("pages", [])), str(e.get("chars", "")),
                    str(e.get("images", "")), e.get("outcome", "")]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()

    def export(self) -> None:
        path = self.ask_save() if self.ask_save else None
        if path:
            self.log.export(path)

    def clear(self) -> None:
        self.log.clear()
        self.reload()


def language_combo(current: str = "it", with_auto: bool = False) -> QComboBox:
    combo = QComboBox()
    if with_auto:
        combo.addItem(_("Lingua dell'interfaccia"), "")
    for code, name in LANGUAGE_NAMES_IT.items():
        combo.addItem(_(name), code)
    combo.setEditable(False)
    idx = combo.findData(current)
    combo.setCurrentIndex(max(0, idx))
    return combo


class InfoTextDialog(QDialog):
    def __init__(self, title: str, html: str, parent=None, buttons: list[tuple[str, object]] | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        b = QTextBrowser()
        b.setOpenLinks(False)
        b.setHtml(html)
        lay.addWidget(b, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.clicked = None
        for text, value in buttons or []:
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, v=value: self._done(v))
            row.addWidget(btn)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def _done(self, value) -> None:
        self.clicked = value
        self.accept()
